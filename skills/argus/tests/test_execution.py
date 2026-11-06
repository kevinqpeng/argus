import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from execution import freeze, account_entries, resolve_accounts, private_file
from scheduler import Queue


def task(name, platform='web', login=False, **extra):
    return dict(taskId=name, kind='execution', taskType='test', platform=platform,
                requiresLogin=login, isolationVerified=True, **extra)


def account(name, **extra):
    return dict({'username': name, 'password': 'TEST-SECRET', 'loginPolicyVerified': True}, **extra)


class ExecutionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.inventory = {'hostSlots': ['host:1', 'host:2', 'host:3'],
            'web': [{'id': str(i), 'ready': True, 'resources': ['browser:host:' + str(i)]} for i in range(3)],
            'mobile': [{'id': str(i), 'ready': True, 'authorized': True,
                        'resources': ['device:' + str(i), 'port:host:' + str(8200 + i)]} for i in range(2)]}
        self.private = {'web': {'accountPools': {'admin': {'t': [account('a'), account('b')]},
                                                 'user': {'t': [account('c'), account('d')]}}}}

    def plan(self, tasks, settings=None, private=None, name='run'):
        base = dict(schemaVersion=1, runId=name, inputHash='a'*64,
                    generationConcurrency=2, executionConcurrency=9, tasks=tasks)
        return freeze(base, {'execution': settings if settings is not None else {'parallelEnabled': True}},
                      self.inventory, self.private if private is None else private)

    def queue(self, plan):
        path = self.root / (plan['runId'] + '.json')
        path.write_text(json.dumps(plan))
        return Queue(path, path.with_suffix('.state'), self.root / 'leases.json')

    def test_serial_default_and_limits_precedence(self):
        tasks = [task(str(i)) for i in range(4)]
        for settings in ({}, {'workers': 8}, {'executionConcurrency': 8},
                         {'parallelEnabled': False, 'maxConcurrency': 8}):
            self.assertEqual(self.plan(tasks, settings)['executionConcurrency'], 1)
        for settings, expected in [({'parallelEnabled': True}, 3),
            ({'parallelEnabled': True, 'workers': 2}, 2),
            ({'parallelEnabled': True, 'workers': 1, 'executionConcurrency': 2}, 2),
            ({'parallelEnabled': True, 'workers': 3, 'executionConcurrency': 3, 'maxConcurrency': 1}, 1)]:
            self.assertEqual(self.plan(tasks, settings)['executionConcurrency'], expected)

    def test_role_combinations_and_no_secret_export(self):
        req = [{'role': r, 'tenant': 't'} for r in ('admin', 'user')]
        plan = self.plan([task(str(i), login=True, accountRequirements=req) for i in range(3)])
        self.assertEqual(plan['executionConcurrency'], 2)
        self.assertNotIn('TEST-SECRET', json.dumps(plan))
        q = self.queue(plan)
        a, b = [q.operate('claim', worker=w) for w in ('a', 'b')]
        self.assertIsNotNone(b)
        self.assertNotEqual(a['binding']['accountRefs'], b['binding']['accountRefs'])
        self.assertIsNone(q.operate('claim', worker='c'))

    def test_public_tasks_not_limited_by_accounts(self):
        self.assertEqual(self.plan([task(str(i)) for i in range(3)], private={})['executionConcurrency'], 3)

    def test_cross_platform_same_account_and_alias_principal(self):
        for mobile in (account('a'), account('alias', principalId='same')):
            private = {'web': {'accounts': {'admin': account('a', principalId='same')}},
                       'mobile': {'accounts': {'admin': mobile}}}
            req = [{'role': 'admin', 'tenant': ''}]
            q = self.queue(self.plan([task('a', login=True, accountRequirements=req)], private=private))
            q.operate('claim', worker='a')
            other = self.queue(self.plan([task('b', 'mobile', True, accountRequirements=req)],
                                         private=private, name='other'))
            self.assertIsNone(other.operate('claim', worker='b'))
            # Separate registries for the next subcase only.
            for file in self.root.iterdir():
                file.unlink()

    def test_single_device_ports_and_shared_business_objects(self):
        for resources in (['device:0', 'port:host:8200'], ['device:1', 'port:host:8200']):
            self.inventory['mobile'][1]['resources'] = resources
            self.assertEqual(self.plan([task('a', 'mobile'), task('b', 'mobile')])['executionConcurrency'], 1)
        self.assertEqual(self.plan([task('a', resources=['business:order:42']),
                                   task('b', resources=['business:order:42'])])['executionConcurrency'], 1)

    def test_missing_and_unknown_fail_closed(self):
        plan = self.plan([task('a', login=True, accountRequirements=[{'role': 'missing', 'tenant': ''}])])
        self.assertIsNone(self.queue(plan).operate('claim', worker='a'))
        tasks = [task('a'), task('b')]
        tasks[0]['isolationVerified'] = False
        self.assertEqual(self.plan(tasks)['executionConcurrency'], 1)
        private = {'web': {'accounts': {'admin': {'username': 'old', 'password': 'TEST-SECRET'}}}}
        req = [{'role': 'admin', 'tenant': ''}]
        plan = self.plan([task('a', login=True, accountRequirements=req), task('b')], private=private)
        self.assertEqual(plan['executionConcurrency'], 1)
        self.inventory['hostSlots'] = []
        plan = self.plan([task('a')], name='empty')
        self.assertIsNone(self.queue(plan).operate('claim', worker='a'))

    def test_cross_run_slot_lock_and_timeout_release(self):
        self.inventory['hostSlots'] = ['host:1']
        q = self.queue(self.plan([task('a', timeoutSeconds=1)]))
        other = self.queue(self.plan([task('b')], name='other'))
        claim = q.operate('claim', worker='a', now=0)
        self.assertEqual(q.operate('status', now=2)['overdue'], ['a'])
        self.assertIsNone(other.operate('claim', worker='b'))
        with self.assertRaises(ValueError):
            q.operate('finish', task_id='a', token=claim['token'], outcome='failed', evidence='receipt')
        q.operate('finish', task_id='a', token=claim['token'], outcome='failed', evidence='receipt', stopped=True)
        self.assertIsNotNone(other.operate('claim', worker='b'))

    def test_dependencies_and_pool_precedence_validation(self):
        self.assertEqual(self.plan([task('a'), task('b', dependsOn=['a'])])['executionConcurrency'], 1)
        data = {'accounts': {'admin': account('old')}, 'accountPools': {'admin': {'': []}}}
        self.assertEqual(account_entries(data), [])
        for data in ({'accountPools': []}, {'accounts': {'admin': {'password': 'x'}}},
                     {'accountPools': {'admin': {'': [account('a', loginPolicyVerified='yes')]}}}):
            with self.assertRaises(ValueError):
                account_entries(data)

    def test_business_lock_across_runs_and_multiple_devices(self):
        q = self.queue(self.plan([task('a', resources=['business:shared'])]))
        q.operate('claim', worker='a')
        other = self.queue(self.plan([task('b', 'mobile', resources=['business:shared'])], name='other'))
        self.assertIsNone(other.operate('claim', worker='b'))
        mobile = self.queue(self.plan([task('c', 'mobile'), task('d', 'mobile')], name='mobile'))
        claims = [mobile.operate('claim', worker=w) for w in ('c', 'd')]
        self.assertTrue(all(claims))
        self.assertNotEqual(claims[0]['binding']['routeId'], claims[1]['binding']['routeId'])

    def test_serial_guard_both_directions_across_runs(self):
        for serial_first in (False, True):
            first = task('a')
            second = task('b')
            (first if serial_first else second)['isolationVerified'] = False
            q = self.queue(self.plan([first]))
            q.operate('claim', worker='a')
            other = self.queue(self.plan([second], name='other'))
            self.assertIsNone(other.operate('claim', worker='b'))
            for file in self.root.iterdir():
                file.unlink()

    def test_credentials_resolved_only_in_memory_and_identity_drift_rejected(self):
        plan = self.plan([task('a', login=True, accountRequirements=[{'role': 'admin', 'tenant': 't'}])])
        binding = plan['tasks'][0]['bindings'][0]
        self.assertEqual(resolve_accounts(self.private['web'], binding)[0]['password'], 'TEST-SECRET')
        self.private['web']['accountPools']['admin']['t'][0]['username'] = 'changed'
        with self.assertRaises(ValueError):
            resolve_accounts(self.private['web'], binding)
        path = self.root / 'private'
        path.write_text('{}')
        path.chmod(0o644)
        with self.assertRaises(ValueError):
            private_file(path)

    def test_historical_inflight_lease_blocks_new_run_and_registry_cannot_change(self):
        q = self.queue(self.plan([task('a')]))
        (self.root / 'leases.json').write_text(json.dumps({'device:old': {
            'runId': 'old', 'taskId': 'old', 'token': 'old'}}))
        self.assertIsNone(q.operate('claim', worker='a'))
        wrong = Queue(self.root / 'run.json', self.root / 'run.state', self.root / 'other-leases.json')
        with self.assertRaises(ValueError):
            wrong.operate('claim', worker='a')

    def test_unavailable_routes_and_unauthorized_devices(self):
        for route in self.inventory['web']:
            route['ready'] = False
        plan = self.plan([task('a')])
        self.assertIsNone(self.queue(plan).operate('claim', worker='a'))
        self.inventory['mobile'][0]['authorized'] = False
        with self.assertRaises(ValueError):
            self.plan([task('b', 'mobile')])

    def test_mobile_private_validator_accepts_pool_and_legacy(self):
        import importlib.util
        path = Path(__file__).absolute().parents[2] / 'mobile-e2e-test/scripts/validate_config.py'
        spec = importlib.util.spec_from_file_location('mobile_config', path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        artifact = self.root / 'app.apk'
        artifact.touch()
        data = dict(self.private['web'], schemaVersion=1, service={'baseUrl': 'https://example.test'},
                    targets=[{'platform': 'android', 'deviceId': 'D', 'appId': 'app', 'appPath': str(artifact)}],
                    accounts={'old': {'username': 'old', 'password': 'TEST-SECRET'}})
        private = self.root / 'mobile-private.json'
        private.write_text(json.dumps(data))
        private.chmod(0o600)
        module.validate(private)
        data['accountPools']['admin']['t'][0]['password'] = ''
        private.write_text(json.dumps(data))
        with self.assertRaises(ValueError):
            module.validate(private)

    def test_cli_freeze_immutable_and_redacted_failure(self):
        raw = dict(schemaVersion=1, runId='cli', inputHash='a'*64, generationConcurrency=2,
                   executionConcurrency=9, tasks=[task('a')])
        for name, value in [('plan', raw), ('config', {}), ('inventory', self.inventory)]:
            (self.root / name).write_text(json.dumps(value))
        cmd = [sys.executable, '-B', str(Path(__file__).resolve().parents[1] / 'scripts/execution.py'),
               *[str(self.root / n) for n in ('plan', 'config', 'inventory', 'output')]]
        self.assertEqual(subprocess.run(cmd, capture_output=True).returncode, 0)
        self.assertEqual(subprocess.run(cmd, capture_output=True).returncode, 2)
        secret = self.root / 'private'
        secret.write_text('{"password":"TEST-SECRET", BROKEN}')
        secret.chmod(0o600)
        failed = subprocess.run(cmd + ['--web-private', str(secret)], capture_output=True, text=True)
        self.assertEqual(failed.returncode, 2)
        self.assertNotIn('TEST-SECRET', failed.stdout + failed.stderr)


if __name__ == '__main__':
    unittest.main()
