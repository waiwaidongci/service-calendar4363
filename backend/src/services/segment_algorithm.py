"""分段复盘算法：按报告时区的开单日期，为所有小组选同一套切点。

设计要点：
- 底稿只在 aggregate 阶段扫描一次，产出「日期 × 小组」的样本数/超时数矩阵；
  之后所有切法都只在这张日级小矩阵（前缀和）上搜索，不为每种切法重扫十万单。
- 段内每组超时率 p=k/n 逐单预测，平方误差 = Σ[k(1-p)^2+(n-k)p^2] = Σk(n-k)/n。
  用 Fraction 精确保存，保证「同分」判定与并列规则可靠。
- 约束：最多 3 个切点；每段每组样本数至少 min_count。
- 并列时先取切点少，再按切点日期依次更早。
- 每个搜索层（0..3 个切点）都是一个可持久化的阶段，中断后从下一层继续。
"""
from fractions import Fraction
from collections import defaultdict
from datetime import datetime
from zoneinfo import ZoneInfo

from src.services.timecalc import business_minutes

MIN_COUNT = 10
MAX_CUTS = 3


# ---------- 编码：Fraction 以分子/分母落库，避免浮点误差破坏并列判定 ----------
def _ser(entry):
    if entry is None:
        return None
    cost, cuts = entry
    return {'n': cost.numerator, 'd': cost.denominator, 'cuts': list(cuts)}


def _de(entry):
    if entry is None:
        return None
    return (Fraction(entry['n'], entry['d']), tuple(entry['cuts']))


# ---------- 阶段 1：一次扫描底稿，得到日期×小组矩阵与段成本 ----------
def aggregate(sources, policy):
    zone = ZoneInfo(policy['timezone'])
    sla = policy['sla_minutes']
    cells = defaultdict(lambda: [0, 0])
    group_totals = defaultdict(lambda: [0, 0])
    for s in sources:
        if s['priority'] not in sla:
            raise ValueError('优先级无SLA口径：' + s['priority'])
        day = datetime.fromisoformat(s['opened']).astimezone(zone).date().isoformat()
        group = s['department']
        overdue = int(business_minutes(s['opened'], s['closed'], policy) > sla[s['priority']])
        cells[(day, group)][0] += 1
        cells[(day, group)][1] += overdue
        group_totals[group][0] += 1
        group_totals[group][1] += overdue
    dates = sorted({day for day, _ in cells})
    groups = sorted(group_totals)
    matrix = [[list(cells.get((day, group), [0, 0])) for group in groups] for day in dates]
    return {
        'dates': dates,
        'groups': groups,
        'matrix': matrix,
        'group_totals': {g: list(group_totals[g]) for g in groups},
        'orders': sum(v[0] for v in group_totals.values()),
        'overdue': sum(v[1] for v in group_totals.values()),
        'timezone': policy['timezone'],
    }


def prefix_sums(agg):
    dates = agg['dates']
    groups = agg['groups']
    pn = [[0] * (len(dates) + 1) for _ in groups]
    pk = [[0] * (len(dates) + 1) for _ in groups]
    for j, row in enumerate(agg['matrix']):
        for gi, (n, k) in enumerate(row):
            pn[gi][j + 1] = pn[gi][j] + n
            pk[gi][j + 1] = pk[gi][j] + k
    return pn, pk


def _segment_cost(pn, pk, i, j, min_count):
    """闭区间日期下标 [i, j] 的平方误差；任一组样本不足则返回 None。"""
    cost = Fraction(0)
    for gi in range(len(pn)):
        n = pn[gi][j + 1] - pn[gi][i]
        if n < min_count:
            return None
        k = pk[gi][j + 1] - pk[gi][i]
        cost += Fraction(k * (n - k), n)
    return cost


def aggregate_stage(sources, policy, min_count=MIN_COUNT):
    """扫描底稿一次，落库日级矩阵与 0 切点层的 dp（后续阶段只依赖这些）。"""
    agg = aggregate(sources, policy)
    days = len(agg['dates'])
    agg['min_count'] = min_count
    agg['max_cuts'] = MAX_CUTS
    agg['dp0'] = [_ser((c, ())) if (c := _segment_cost(*prefix_sums(agg), 0, j, min_count)) is not None else None
                  for j in range(days)]
    return agg


# ---------- 阶段 2..4：每层固定增加一个切点 ----------
def _better(a, b):
    """同一分段数下比较：误差小者优；同分时切点序列字典序早者优。"""
    if a[0] != b[0]:
        return a[0] < b[0]
    return a[1] < b[1]


def cost_matrix(agg, min_count=MIN_COUNT):
    """日期闭区间 [i, j] 的段成本；只由日级矩阵派生，不碰底稿，也不落库。"""
    pn, pk = prefix_sums(agg)
    days = len(agg['dates'])
    return [[_segment_cost(pn, pk, i, j, min_count) for j in range(days)] for i in range(days)]


def advance_stage(agg, previous_dp, min_count=MIN_COUNT, costs=None):
    """在上一层 dp 基础上再增加一个切点，返回本层 dp 与覆盖全区间的最优解。"""
    days = len(agg['dates'])
    if costs is None:
        costs = cost_matrix(agg, min_count)
    prev = [_de(x) for x in previous_dp]
    cur = [None] * days
    for j in range(days):
        best = None
        for i in range(j):
            base = prev[i]
            if base is None:
                continue
            cost = costs[i + 1][j]
            if cost is None:
                continue
            cand = (base[0] + cost, base[1] + (i + 1,))
            if best is None or _better(cand, best):
                best = cand
        cur[j] = None if best is None else _ser(best)
    return {'dp': cur, 'best': cur[-1] if days else None}


# ---------- 并列规则与出齐 ----------
def choose(agg_stage, search_stages, max_cuts=MAX_CUTS):
    """在 0..max_cuts 个切点之间选：误差最小 → 切点少 → 切点日期依次更早。"""
    candidates = []
    dp0 = agg_stage.get('dp0') or []
    if dp0 and dp0[-1] is not None:
        candidates.append((0, dp0[-1]))
    for k in range(1, max_cuts + 1):
        stage = search_stages.get(k) or search_stages.get(str(k))
        if stage and stage.get('best') is not None:
            candidates.append((k, stage['best']))
    best = None
    for k, entry in candidates:
        key = (Fraction(entry['n'], entry['d']), k, tuple(entry['cuts']))
        if best is None or key < best[0]:
            best = (key, entry)
    if best is None:
        return None
    (cost, k, cuts), entry = best
    return {
        'cut_indices': list(cuts),
        'cuts': [agg_stage['dates'][i] for i in cuts],
        'cost': float(cost),
        'cost_exact': {'n': cost.numerator, 'd': cost.denominator},
        'segments': k + 1,
        'cuts_count': k,
    }


def describe(agg_stage, cut_indices):
    """每段的起止日期、样本数、超时数，以及段内每组的样本数与超时数。"""
    pn, pk = prefix_sums(agg_stage)
    dates = agg_stage['dates']
    groups = agg_stage['groups']
    bounds = [0] + list(cut_indices) + [len(dates)]
    segments = []
    for idx in range(len(bounds) - 1):
        i, j = bounds[idx], bounds[idx + 1] - 1
        rows = []
        total_n = total_k = 0
        for gi, group in enumerate(groups):
            n = pn[gi][j + 1] - pn[gi][i]
            k = pk[gi][j + 1] - pk[gi][i]
            total_n += n
            total_k += k
            rows.append({'小组': group, '样本数': n, '超时数': k, '超时率': k / n if n else None})
        segments.append({
            '序号': idx + 1,
            '开始日期': dates[i],
            '结束日期': dates[j],
            '样本数': total_n,
            '超时数': total_k,
            '超时率': total_k / total_n if total_n else None,
            '分组': rows,
        })
    return segments


def explain(agg_stage, chosen, search_stages, max_cuts=MAX_CUTS, min_count=MIN_COUNT):
    """找不到合适切法（或无需切分）时给出缘由。"""
    if not agg_stage['orders']:
        return '冻结底稿没有可用样本，无法比较快慢段。'
    low = [g for g in agg_stage['groups'] if agg_stage['group_totals'][g][0] < min_count]
    if chosen is None:
        if low:
            detail = '、'.join('%s（%d单）' % (g, agg_stage['group_totals'][g][0]) for g in low)
            return '小组%s总样本不足%d单，任何分段都无法满足每段每组至少%d单。' % (detail, min_count, min_count)
        if len(agg_stage['dates']) <= 1:
            return '冻结底稿只落在%d个开单日期上，没有可插入的日期切点。' % len(agg_stage['dates'])
        return '无法构造满足约束的分段。'
    if chosen['cuts_count'] == 0:
        if len(agg_stage['dates']) <= 1:
            return '冻结底稿只落在%d个开单日期上，没有可插入的日期切点，只能单段呈现。' % len(agg_stage['dates'])
        higher = [k for k in range(1, max_cuts + 1) if (search_stages.get(k) or search_stages.get(str(k)) or {}).get('best') is not None]
        if not higher:
            return '所有候选切点都会让某一段内某小组少于%d单；单段本身满足约束，因此维持单段。' % min_count
        return '增加切点后总平方误差没有下降（同分时取切点更少），因此维持单段。'
    return None
