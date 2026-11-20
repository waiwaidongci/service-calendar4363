"""service 层集成测试：用 SQLite 内存库验证阶段续跑与并发改选。

运行：PYTHONPATH=backend python3 -m unittest discover -s backend/tests -v
"""
import os
import sys
import types
import unittest

os.environ.setdefault('DATABASE_URL', 'sqlite:////tmp/segment-tests-%d.db' % os.getpid())
os.environ.setdefault('JWT_SECRET', 'test-secret')
os.environ.setdefault('REDIS_URL', 'redis://localhost:6379/0')

# settings 依赖 /app/project.json；测试环境用 backend 下的副本
import json
import pathlib
project = json.loads((pathlib.Path(__file__).resolve().parents[1] / 'project.json').read_text())
fake = types.ModuleType('src.config.settings')
fake.CONFIG = project
fake.DATABASE_URL = os.environ['DATABASE_URL']
fake.REDIS_URL = os.environ['REDIS_URL']
fake.SECRET = os.environ['JWT_SECRET']
sys.modules['src.config.settings'] = fake

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.config.database import Base
from src.models.run import Run
from src.models.segment import SegmentSearch
from src.services import segment as service
from src.services import segment_algorithm as algo
from tests.test_segment_algorithm import build, POLICY


class ServiceTest(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine('sqlite://')
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine, expire_on_commit=False)
        service._enqueue = lambda run_id, version: None  # 不连 Redis
        self.user = types.SimpleNamespace(email='admin@demo.local', role='admin')

    def make_run(self, plan, approved=True):
        with self.Session() as db:
            run = Run(name='测试', status='completed', approved=approved,
                      snapshot={'policy': POLICY, 'sources': build(plan), 'period': '全部'},
                      checksum='x', created=0.0, review='已复核测试')
            db.add(run)
            db.commit()
            return run.id

    def test_requires_approved_report(self):
        rid = self.make_run({1: {'A': (20, 0)}, 2: {'A': (20, 0)}}, approved=False)
        with self.Session() as db:
            with self.assertRaises(Exception) as ctx:
                service.request(db, self.user, rid)
            self.assertIn('已复核', str(ctx.exception))

    def test_stages_and_result(self):
        plan = {d: {'A': (12, 0 if d <= 3 else 12)} for d in range(1, 7)}
        rid = self.make_run(plan)
        with self.Session() as db:
            service.request(db, self.user, rid)
            self.assertTrue(service.process(db, rid, 1))
            body = service.view(db, rid)
        self.assertEqual(body['status'], 'completed')
        result = body['result']
        self.assertEqual(result['chosen']['cuts'], ['2026-11-04'])
        self.assertEqual(len(result['segments']), 2)
        for seg in result['segments']:
            self.assertEqual(len(seg['分组']), 1)
            self.assertGreaterEqual(seg['分组'][0]['样本数'], 10)
            self.assertIn('超时数', seg['分组'][0])
        self.assertIsNone(result['explanation'])

    def test_resume_does_not_recompute_finished_stage(self):
        plan = {d: {'A': (12, d % 2 * 12)} for d in range(1, 9)}
        rid = self.make_run(plan)
        with self.Session() as db:
            service.request(db, self.user, rid)
            row = db.query(SegmentSearch).filter_by(run_id=rid).one()
            # 只完成到第 1 层就中断
            data = {'min_count': algo.MIN_COUNT, 'max_cuts': algo.MAX_CUTS, 'stages': {}}
            data['agg'] = algo.aggregate_stage(build(plan), POLICY)
            data['stages']['1'] = algo.advance_stage(data['agg'], data['agg']['dp0'])
            row.stage = 1
            row.status = 'running'
            row.data = data
            db.commit()

            original = algo.advance_stage
            layers = []

            def spy(agg, dp, *a, **kw):
                layers.append(dp)
                return original(agg, dp, *a, **kw)

            algo.advance_stage = spy
            try:
                self.assertTrue(service.process(db, rid, row.version))
            finally:
                algo.advance_stage = original
            # 只补第 2、3 层，不重算第 1 层
            self.assertEqual(len(layers), 2)
            body = service.view(db, rid)
        self.assertEqual(body['status'], 'completed')

    def test_stale_version_cannot_overwrite(self):
        plan = {d: {'A': (12, d % 2 * 12)} for d in range(1, 9)}
        rid = self.make_run(plan)
        with self.Session() as db:
            service.request(db, self.user, rid)           # version 1
            service.request(db, self.user, rid)           # version 2 改选
            self.assertFalse(service.process(db, rid, 1))  # 旧任务直接作废
            self.assertEqual(service.view(db, rid)['status'], 'queued')
            self.assertTrue(service.process(db, rid, 2))
            completed = service.view(db, rid)
            # 旧任务此时再跑也不能覆盖新结果
            self.assertFalse(service.process(db, rid, 1))
            self.assertEqual(service.view(db, rid)['version'], 2)
            self.assertEqual(service.view(db, rid)['status'], 'completed')
        self.assertEqual(completed['status'], 'completed')

    def test_progress_hides_partial_result(self):
        plan = {d: {'A': (12, 0)} for d in range(1, 5)}
        rid = self.make_run(plan)
        with self.Session() as db:
            service.request(db, self.user, rid)
            body = service.view(db, rid)
        self.assertEqual(body['status'], 'queued')
        self.assertNotIn('result', body)
        self.assertEqual(body['stage'], -1)


if __name__ == '__main__':
    unittest.main()
