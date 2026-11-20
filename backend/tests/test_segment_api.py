"""路由层测试：登录 → 请求复盘 → 查看已公开结果。

运行：PYTHONPATH=backend:/tmp/deps python3 -m unittest discover -s backend/tests -v
"""
import os
import sys
import types
import json
import pathlib
import unittest

os.environ.setdefault('DATABASE_URL', 'sqlite:////tmp/segment-tests-%d.db' % os.getpid())
os.environ.setdefault('JWT_SECRET', 'test-secret')
os.environ.setdefault('REDIS_URL', 'redis://localhost:6379/0')

if 'src.config.settings' not in sys.modules:
    project = json.loads((pathlib.Path(__file__).resolve().parents[1] / 'project.json').read_text())
    fake = types.ModuleType('src.config.settings')
    fake.CONFIG = project
    fake.DATABASE_URL = os.environ['DATABASE_URL']
    fake.REDIS_URL = os.environ['REDIS_URL']
    fake.SECRET = os.environ['JWT_SECRET']
    sys.modules['src.config.settings'] = fake

try:
    from fastapi.testclient import TestClient
    from src.main import app
    from src.config.database import Session
    from src.models.identity import User
    from src.models.run import Run
    from src.services import segment as service
    from src.utils.security import password_hash
    from tests.test_segment_algorithm import build, POLICY
except ImportError as exc:  # 无第三方依赖时跳过
    raise unittest.SkipTest(str(exc))


class ApiTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        service._enqueue = lambda run_id, version: None
        cls.client = TestClient(app)
        cls.client.__enter__()
        with Session() as db:
            for role in ('admin', 'viewer'):
                if not db.query(User).filter_by(email=role + '@demo.local').first():
                    db.add(User(email=role + '@demo.local', role=role,
                                password=password_hash('Demo2026!')))
            run = Run(name='接口测试报告', status='completed', approved=True, checksum='c',
                      snapshot={'policy': POLICY, 'sources': build({1: {'A': (12, 0)}, 2: {'A': (12, 12)},
                                                                  3: {'A': (12, 12)}, 4: {'A': (12, 12)}}),
                                'period': '全部'}, created=0.0, review='已核对')
            db.add(run)
            db.commit()
            cls.run_id = run.id

    @classmethod
    def tearDownClass(cls):
        cls.client.__exit__(None, None, None)

    def token(self, email):
        r = self.client.post('/auth/login', json={'email': email, 'password': 'Demo2026!'})
        self.assertEqual(r.status_code, 200, r.text)
        return {'Authorization': 'Bearer ' + r.json()['token']}

    def test_viewer_can_read_progress_but_not_start(self):
        with Session() as db:
            run = Run(name='只读测试', status='completed', approved=True, checksum='v',
                      snapshot={'policy': POLICY, 'sources': [], 'period': '全部'},
                      created=0.0, review='已核对')
            db.add(run)
            db.commit()
            rid = run.id
        headers = self.token('viewer@demo.local')
        r = self.client.get(f'/runs/{rid}/segment', headers=headers)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()['status'], 'idle')
        self.assertNotIn('result', r.json())
        r = self.client.post(f'/runs/{rid}/segment', headers=headers)
        self.assertEqual(r.status_code, 403)

    def test_start_then_result_and_all(self):
        headers = self.token('admin@demo.local')
        r = self.client.post(f'/runs/{self.run_id}/segment', headers=headers)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()['status'], 'queued')
        with Session() as db:
            service.process(db, self.run_id, 1)
        r = self.client.get(f'/runs/{self.run_id}/segment', headers=headers)
        result = r.json()['result']
        self.assertEqual(result['segments'][0]['分组'][0]['小组'], 'A')
        self.assertEqual(result['chosen']['cuts'], ['2026-11-02'])
        for seg in result['segments']:
            self.assertGreaterEqual(seg['样本数'], 10)
            self.assertIn('超时数', seg)
        r = self.client.get('/segments', headers=headers)
        self.assertEqual(r.status_code, 200)
        self.assertTrue(any(x['run_id'] == self.run_id for x in r.json()))

    def test_unknown_run(self):
        headers = self.token('admin@demo.local')
        r = self.client.get('/runs/99999/segment', headers=headers)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()['status'], 'idle')
        r = self.client.post('/runs/99999/segment', headers=headers)
        self.assertEqual(r.status_code, 404)

    def test_unapproved_report_rejected(self):
        with Session() as db:
            run = Run(name='未复核', status='completed', approved=False, checksum='d',
                      snapshot={'policy': POLICY, 'sources': [], 'period': '全部'},
                      created=0.0, review='')
            db.add(run)
            db.commit()
            rid = run.id
        headers = self.token('admin@demo.local')
        r = self.client.post(f'/runs/{rid}/segment', headers=headers)
        self.assertEqual(r.status_code, 409)


if __name__ == '__main__':
    unittest.main()
