import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / 'scripts/prepare_input.py'


class PrepareInputTest(unittest.TestCase):
    def test_sources_are_rehashed_and_design_discovery_is_empty(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / 'cases').mkdir()
            def write(name, data):
                (root / name).write_text(json.dumps(data), encoding='utf-8')
            (root / 'source.txt').write_text('original')
            import hashlib
            sha = hashlib.sha256(b'original').hexdigest()
            write('manifest.json', {'runId': 'run', 'requiredMatrix': ['web']})
            write('cases/impact-analysis.json', {'sources': [{'sourceId': 'S'}]})
            write('cases/acs.json', [])
            write('cases/scenarios.json', [{'scenarioId': 'A'}])
            write('cases/cases.json', [])
            write('spec.json', {'sourceHash': sha, 'sourcePath': str(root / 'source.txt'),
                                'sourceFiles': {'S': str(root / 'source.txt')},
                                'baselineHash': 'frozen', 'impactHash': 'frozen-impact'})
            output = root / 'output.json'
            command = [sys.executable, str(SCRIPT), str(root), str(root / 'spec.json'), str(output)]
            result = subprocess.run(command, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            value = json.loads(output.read_text())
            self.assertEqual(value['currentSourceHashes'], {'S': sha})
            self.assertEqual(value['baselineHash'], 'frozen')
            self.assertEqual(value['impactHash'], 'frozen-impact')
            self.assertEqual(value['discovered'], [])
            (root / 'source.txt').write_text('changed')
            output.unlink()
            result = subprocess.run(command, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertNotEqual(json.loads(output.read_text())['currentSourceHash'], sha)

            write('cases/execution-cases.json', [{'caseId': 'published'}])
            write('discovery.json', [{'caseId': 'published', 'matrixId': 'web', 'automationRef': 'x::y'}])
            execution = {'scope': 'partial', 'inputHash': 'frozen', 'expectedShards': ['s'],
                         'shards': [], 'results': []}
            write('execution.json', execution)
            write('spec.json', {'sourceHash': sha, 'sourcePath': str(root / 'source.txt'),
                                'sourceFiles': {'S': str(root / 'source.txt')}, 'baselineHash': 'frozen',
                                'impactHash': 'frozen-impact',
                                'discoveryFile': str(root / 'discovery.json'),
                                'executionFile': str(root / 'execution.json')})
            output.unlink()
            result = subprocess.run(command + ['--phase', 'execution'], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            value = json.loads(output.read_text())
            self.assertEqual(value['cases'], [{'caseId': 'published'}])
            self.assertEqual(value['discovered'][0]['automationRef'], 'x::y')
            self.assertEqual(value['scope'], 'partial')


if __name__ == '__main__':
    unittest.main()
