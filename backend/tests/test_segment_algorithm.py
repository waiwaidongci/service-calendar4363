"""纯算法单测：不依赖数据库/Redis/FastAPI。

运行：PYTHONPATH=backend python3 -m unittest discover -s backend/tests -v
"""
import unittest
from fractions import Fraction

from src.services.timecalc import business_minutes
from src.services import segment_algorithm as algo

POLICY = {
    'version': 'test',
    'timezone': 'UTC',
    'weekdays': [0, 1, 2, 3, 4, 5, 6],
    'windows': [[0, 1440]],
    'holidays': [],
    'sla_minutes': {'普通': 60, '紧急': 30},
}


def case(day, group, overdue, priority='普通'):
    opened = f'2026-11-{day:02d}T00:00:00+00:00'
    minutes = 120 if overdue else 30
    hh, mm = divmod(minutes, 60)
    closed = f'2026-11-{day:02d}T{hh:02d}:{mm:02d}:00+00:00'
    return {'period': '2026-11', 'department': group, 'opened': opened,
            'closed': closed, 'priority': priority}


def build(plan):
    """plan: {day: {group: (n, k)}} -> sources"""
    out = []
    for day, groups in plan.items():
        for group, (n, k) in groups.items():
            out += [case(day, group, True) for _ in range(k)]
            out += [case(day, group, False) for _ in range(n - k)]
    return out


class BusinessMinutesTest(unittest.TestCase):
    def test_lunch_and_weekend_and_holiday(self):
        policy = {'timezone': 'Asia/Shanghai', 'weekdays': [0, 1, 2, 3, 4],
                  'windows': [[540, 720], [780, 1080]], 'holidays': ['2026-11-16'],
                  'sla_minutes': {'普通': 480}}
        # 周五 16:30→18:00 计 90；周六周日不计；周一 11-16 为假日
        policy['holidays'] = ['2026-11-16']
        self.assertEqual(business_minutes('2026-11-13T16:30:00+08:00',
                                          '2026-11-17T10:00:00+08:00', policy), 90 + 60)
        # 去掉假日：周五 90 + 周一 480 + 周二 60 = 630
        policy['holidays'] = []
        self.assertEqual(business_minutes('2026-11-13T16:30:00+08:00',
                                          '2026-11-17T10:00:00+08:00', policy), 630)

    def test_negative_and_boundary(self):
        policy = {**POLICY, 'windows': [[540, 720]], 'timezone': 'Asia/Shanghai'}
        with self.assertRaises(ValueError):
            business_minutes('2026-11-02T10:00:00+08:00', '2026-11-02T09:00:00+08:00', policy)
        # 恰在窗口端点：540 到 600 = 60 分钟
        self.assertEqual(business_minutes('2026-11-02T09:00:00+08:00',
                                          '2026-11-02T10:00:00+08:00', policy), 60)


class SearchTest(unittest.TestCase):
    def test_planted_change_point(self):
        plan = {}
        for d in range(1, 6):
            plan[d] = {'A': (12, 0), 'B': (12, 0)}
        for d in range(6, 11):
            plan[d] = {'A': (12, 12), 'B': (12, 12)}
        agg = algo.aggregate_stage(build(plan), POLICY)

        # 逐层推进后选切法
        stages = {}
        prev = agg['dp0']
        for layer in range(1, 4):
            stages[str(layer)] = algo.advance_stage(agg, prev)
            prev = stages[str(layer)]['dp']
        chosen = algo.choose(agg, stages)
        self.assertIsNotNone(chosen)
        self.assertEqual(chosen['cut_indices'], [5])
        self.assertEqual(chosen['cuts'], ['2026-11-06'])
        self.assertEqual(chosen['cost'], 0.0)

        segs = algo.describe(agg, chosen['cut_indices'])
        self.assertEqual(len(segs), 2)
        self.assertEqual(segs[0]['开始日期'], '2026-11-01')
        self.assertEqual(segs[0]['结束日期'], '2026-11-05')
        self.assertEqual(segs[1]['开始日期'], '2026-11-06')
        for seg in segs:
            for group in seg['分组']:
                self.assertGreaterEqual(group['样本数'], 10)
                self.assertIn('超时数', group)

    def test_min_count_prevents_thin_segments(self):
        # 只有 2 天，每组每天 6 单：整段 12 单满足；切成两段各 6 单不满足
        plan = {1: {'A': (6, 6), 'B': (6, 0)}, 2: {'A': (6, 0), 'B': (6, 6)}}
        agg = algo.aggregate_stage(build(plan), POLICY)
        stages = {}
        prev = agg['dp0']
        for layer in range(1, 4):
            stages[str(layer)] = algo.advance_stage(agg, prev)
            prev = stages[str(layer)]['dp']
        chosen = algo.choose(agg, stages)
        self.assertEqual(chosen['cuts_count'], 0)
        reason = algo.explain(agg, chosen, stages)
        self.assertIn('少于10单', reason)

    def test_insufficient_total_explains(self):
        plan = {1: {'A': (4, 0), 'B': (20, 0)}, 2: {'A': (5, 0), 'B': (20, 0)}}
        agg = algo.aggregate_stage(build(plan), POLICY)
        chosen = algo.choose(agg, {})
        self.assertIsNone(chosen)
        reason = algo.explain(agg, chosen, {})
        self.assertIn('A（9单）', reason.replace('小组A', 'A'))

    def test_tie_prefers_fewer_cuts_then_earlier_dates(self):
        agg = {'dates': ['d1', 'd2', 'd3'], 'orders': 90, 'groups': ['G'],
               'group_totals': {'G': [90, 45]},
               'dp0': [{'n': 1, 'd': 1, 'cuts': []}] * 3}
        stages = {'1': {'best': {'n': 1, 'd': 1, 'cuts': [1]}},
                  '2': {'best': {'n': 1, 'd': 1, 'cuts': [1, 2]}}}
        chosen = algo.choose(agg, stages)
        self.assertEqual(chosen['cuts_count'], 0)
        self.assertEqual(chosen['cuts'], [])

        # dp0 无可行解时，1 切点与 2 切点同误差 -> 取切点更少的 1 切点
        agg2 = dict(agg, dp0=[None, None, None])
        stages2 = {'1': {'best': {'n': 2, 'd': 1, 'cuts': [1]}},
                   '2': {'best': {'n': 2, 'd': 1, 'cuts': [1, 2]}}}
        chosen2 = algo.choose(agg2, stages2)
        self.assertEqual(chosen2['cuts_count'], 1)
        self.assertEqual(chosen2['cuts'], ['d2'])

    def test_earlier_cut_wins_within_same_layer(self):
        # 两个切点位置误差相同 -> 字典序更早的切点序列胜出
        plan = {}
        for d in range(1, 13):
            plan[d] = {'A': (12, 0), 'B': (12, 0)}
        for d in range(7, 13):
            plan[d] = {'A': (12, 12), 'B': (12, 12)}
        agg = algo.aggregate_stage(build(plan), POLICY)
        stage = algo.advance_stage(agg, agg['dp0'])
        self.assertIsNotNone(stage['best'])
        self.assertEqual(stage['best']['cuts'], [6])

    def test_cost_formula(self):
        # 全段一组：n=10,k=4 -> 误差 = 4*6/10 = 2.4
        plan = {1: {'G': (10, 4)}}
        agg = algo.aggregate_stage(build(plan), POLICY)
        self.assertEqual(Fraction(agg['dp0'][0]['n'], agg['dp0'][0]['d']), Fraction(24, 10))

    def test_at_most_three_cuts(self):
        plan = {}
        for d in range(1, 5):
            for g in ('A', 'B'):
                plan[d] = plan.get(d, {})
                plan[d][g] = (12, 0 if d <= 2 else 12)
        agg = algo.aggregate_stage(build(plan), POLICY)
        stages = {}
        prev = agg['dp0']
        for layer in range(1, 4):
            stages[str(layer)] = algo.advance_stage(agg, prev)
            prev = stages[str(layer)]['dp']
        chosen = algo.choose(agg, stages)
        self.assertLessEqual(chosen['cuts_count'], 3)

    def test_stage_resume_matches_full(self):
        # 模拟中断：stage 1 落库后继续，和一次性跑完结果相同
        plan = {}
        for d in range(1, 11):
            rate = 0 if d <= 4 else (12 if d >= 8 else 6)
            plan[d] = {'A': (12, rate), 'B': (12, rate)}
        sources = build(plan)
        agg = algo.aggregate_stage(sources, POLICY)
        full = {}
        prev = agg['dp0']
        for layer in range(1, 4):
            full[str(layer)] = algo.advance_stage(agg, prev)
            prev = full[str(layer)]['dp']
        # 中断后从 stage 1 的 dp 继续
        resumed = {'1': full['1']}
        prev = full['1']['dp']
        for layer in range(2, 4):
            resumed[str(layer)] = algo.advance_stage(agg, prev)
            prev = resumed[str(layer)]['dp']
        self.assertEqual(algo.choose(agg, full), algo.choose(agg, resumed))


if __name__ == '__main__':
    unittest.main()


class BruteForceTest(unittest.TestCase):
    """随机小数据上与枚举所有切法对照。"""

    def brute(self, agg, min_count=algo.MIN_COUNT, max_cuts=algo.MAX_CUTS):
        days = len(agg['dates'])
        pn, pk = algo.prefix_sums(agg)
        best = None
        from itertools import combinations
        for k in range(0, min(max_cuts, days - 1) + 1):
            for cuts in combinations(range(1, days), k):
                bounds = [0] + list(cuts) + [days]
                cost = Fraction(0)
                ok = True
                for i, j in zip(bounds, bounds[1:]):
                    c = algo._segment_cost(pn, pk, i, j - 1, min_count)
                    if c is None:
                        ok = False
                        break
                    cost += c
                if not ok:
                    continue
                key = (cost, k, tuple(cuts))
                if best is None or key < best:
                    best = key
        return best

    def test_random_vs_brute(self):
        import random
        rng = random.Random(7)
        for trial in range(30):
            days = rng.randint(1, 8)
            groups = rng.choice([['A'], ['A', 'B'], ['A', 'B', 'C']])
            plan = {}
            for d in range(1, days + 1):
                plan[d] = {}
                for g in groups:
                    n = rng.randint(6, 14)
                    plan[d][g] = (n, rng.randint(0, n))
            agg = algo.aggregate_stage(build(plan), POLICY)
            stages = {}
            prev = agg['dp0']
            for layer in range(1, algo.MAX_CUTS + 1):
                stages[str(layer)] = algo.advance_stage(agg, prev)
                prev = stages[str(layer)]['dp']
            got = algo.choose(agg, stages)
            want = self.brute(agg)
            if want is None:
                self.assertIsNone(got, trial)
            else:
                cost, k, cuts = want
                self.assertIsNotNone(got, trial)
                self.assertEqual(got['cuts_count'], k, trial)
                self.assertEqual(tuple(got['cut_indices']), cuts, trial)
                self.assertEqual(Fraction(got['cost_exact']['n'], got['cost_exact']['d']), cost, trial)

    def test_no_rescan_of_sources(self):
        from src.services import segment_algorithm as mod
        calls = {'n': 0}
        original = mod.business_minutes

        def counting(*a, **kw):
            calls['n'] += 1
            return original(*a, **kw)

        plan = {}
        for d in range(1, 9):
            plan[d] = {'A': (12, d % 3), 'B': (12, (d + 1) % 3)}
        sources = build(plan)
        mod.business_minutes = counting
        try:
            agg = mod.aggregate_stage(sources, POLICY)
            after_scan = calls['n']
            prev = agg['dp0']
            for layer in range(1, mod.MAX_CUTS + 1):
                stage = mod.advance_stage(agg, prev)
                prev = stage['dp']
            mod.choose(agg, {str(l): mod.advance_stage(agg, agg['dp0']) for l in range(1, 1)})
            mod.describe(agg, [4])
        finally:
            mod.business_minutes = original
        self.assertEqual(after_scan, len(sources))
        self.assertEqual(calls['n'], len(sources))
