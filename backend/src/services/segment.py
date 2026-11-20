"""分段复盘任务：落库阶段、可续跑、并发改选时旧任务不得覆盖新结果。"""
import time
import traceback

import redis
from fastapi import HTTPException
from sqlalchemy import update
from sqlalchemy.exc import IntegrityError

from src.config.settings import REDIS_URL
from src.services import segment_algorithm as algo
from src.services.audit import log
from src.models.run import Run
from src.models.segment import SegmentSearch

QUEUE = 'segment-jobs'


def _enqueue(run_id, version):
    redis.Redis.from_url(REDIS_URL).rpush(QUEUE, f'{run_id}:{version}')


def _require_reviewed(db, run_id):
    run = db.get(Run, run_id)
    if not run:
        raise HTTPException(404, '报告不存在')
    if run.status != 'completed':
        raise HTTPException(409, '仅已完成报告可以做分段复盘')
    if not run.approved:
        raise HTTPException(409, '分段复盘只使用已复核报告的冻结底稿')
    return run


def request(db, user, run_id):
    """开始或重新开始一次搜索：版本号加一，此前任务的结果因此失效。"""
    _require_reviewed(db, run_id)
    stamp = time.time()
    for _ in range(2):
        bumped = db.execute(
            update(SegmentSearch)
            .where(SegmentSearch.run_id == run_id)
            .values(version=SegmentSearch.version + 1, status='queued', stage=-1,
                    data=None, error=None, updated=stamp)
        )
        if bumped.rowcount:
            break
        try:
            db.add(SegmentSearch(run_id=run_id, version=1, status='queued', stage=-1, updated=stamp))
            db.commit()
            break
        except IntegrityError:
            # 另一请求刚插入了同一报告行：回滚后改用版本自增
            db.rollback()
    db.commit()
    row = db.query(SegmentSearch).filter_by(run_id=run_id).one()
    log(db, user.email, 'segment.queue', f'run={run_id} version={row.version}')
    db.commit()
    _enqueue(run_id, row.version)
    return row


def resume(db):
    """worker 启动时找回未完成的搜索，从已落库的阶段继续。"""
    rows = db.query(SegmentSearch).filter(SegmentSearch.status.in_(('queued', 'running'))).all()
    for row in rows:
        _enqueue(row.run_id, row.version)
    return len(rows)


def _save(db, row_id, run_id, version, fields):
    """带版本条件的写入；返回 False 表示任务已被更新的搜索取代。"""
    result = db.execute(
        update(SegmentSearch)
        .where(SegmentSearch.id == row_id, SegmentSearch.run_id == run_id,
               SegmentSearch.version == version)
        .values(**fields, updated=time.time())
    )
    db.commit()
    return result.rowcount > 0


def process(db, run_id, version):
    """从已完成的阶段之后继续，逐层推进；每次落库都核对版本。"""
    row = db.query(SegmentSearch).filter_by(run_id=run_id, version=version).first()
    if not row or row.status not in ('queued', 'running'):
        return False
    run = db.get(Run, run_id)
    if not run or run.status != 'completed':
        return False
    data = row.data or {'min_count': algo.MIN_COUNT, 'max_cuts': algo.MAX_CUTS, 'stages': {}}
    try:
        # 阶段 0：只扫描冻结底稿一次，得到日级矩阵
        if row.stage < 0:
            data['agg'] = algo.aggregate_stage(run.snapshot['sources'], run.snapshot['policy'], data['min_count'])
            if not _save(db, row.id, run_id, version, {'status': 'running', 'stage': 0, 'data': data}):
                return False
            row.stage = 0

        stage = row.stage
        # 段成本表由日级矩阵派生一次，供本次要补的所有层共用；不落库、不重扫底稿
        costs = algo.cost_matrix(data['agg'], data['min_count'])
        for layer in range(stage + 1, data['max_cuts'] + 1):
            if str(layer) in data['stages']:
                continue
            previous_dp = data['agg']['dp0'] if layer == 1 else data['stages'][str(layer - 1)]['dp']
            data['stages'][str(layer)] = algo.advance_stage(
                data['agg'], previous_dp, data['min_count'], costs)
            if not _save(db, row.id, run_id, version, {'status': 'running', 'stage': layer, 'data': data}):
                return False
            stage = layer

        # 所有阶段完成后再一次性公开：切点连同每段每组样本数与超时数
        agg = data['agg']
        chosen = algo.choose(agg, data['stages'], data['max_cuts'])
        segments = algo.describe(agg, chosen['cut_indices']) if chosen else []
        explanation = algo.explain(agg, chosen, data['stages'], data['max_cuts'], data['min_count'])
        data['result'] = {
            'timezone': agg['timezone'],
            'orders': agg['orders'],
            'overdue': agg['overdue'],
            'groups': agg['groups'],
            'days': len(agg['dates']),
            'min_count': data['min_count'],
            'max_cuts': data['max_cuts'],
            'chosen': chosen,
            'segments': segments,
            'explanation': explanation,
        }
        return _save(db, row.id, run_id, version,
                     {'status': 'completed', 'stage': data['max_cuts'], 'data': data, 'error': None})
    except Exception as exc:
        traceback.print_exc()
        _save(db, row.id, run_id, version, {'status': 'failed', 'error': str(exc), 'data': data})
        return False


def view(db, run_id):
    """只公开完成后的结果；未完成时只给进度，不泄露半成品。"""
    row = db.query(SegmentSearch).filter_by(run_id=run_id).first()
    if not row:
        return None
    body = {'run_id': run_id, 'status': row.status, 'stage': row.stage,
            'version': row.version, 'updated': row.updated}
    if row.status == 'completed' and row.data:
        body['result'] = row.data['result']
    if row.status == 'failed':
        body['error'] = row.error
    return body


def all_rows(db):
    return db.query(SegmentSearch).all()


def parse_job(payload):
    run_id, version = str(payload).split(':', 1)
    return int(run_id), int(version)
