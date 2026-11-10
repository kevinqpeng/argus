import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / 'scripts/merge_presentation.py'


class MergePresentationTest(unittest.TestCase):
    def test_merge_follows_frozen_order_and_rejects_duplicates(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            def write(name, data):
                path = root / name
                path.write_text(json.dumps(data), encoding='utf-8')
                return path
            cases = write('cases.json', [{'caseId': 'A'}, {'caseId': 'B'}])
            first = write('first.json', {'title': 'Review', 'cases': [{'caseId': 'B'}], 'notes': []})
            second = write('second.json', {'title': 'Review', 'cases': [{'caseId': 'A'}], 'notes': [{'question': 'Q'}]})
            output = root / 'merged.json'
            command = [sys.executable, str(SCRIPT), str(cases), str(output), str(first), str(second)]
            result = subprocess.run(command, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            merged = json.loads(output.read_text())
            self.assertEqual([row['caseId'] for row in merged['cases']], ['A', 'B'])
            self.assertEqual(merged['notes'], [{'question': 'Q'}])
            second.write_text(first.read_text())
            result = subprocess.run(command, capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(json.loads(output.read_text()), merged)
            first.write_text(json.dumps({'title': 'Review', 'cases': [{'caseId': 'B', 'steps': ['本片只覆盖驳回']}],
                                         'notes': []}), encoding='utf-8')
            second.write_text(json.dumps({'title': 'Review', 'cases': [{'caseId': 'A'}], 'notes': []}), encoding='utf-8')
            rejected = subprocess.run(command, capture_output=True, text=True)
            self.assertNotEqual(rejected.returncode, 0)
            self.assertIn('本片', rejected.stderr)
            self.assertEqual(json.loads(output.read_text()), merged)


if __name__ == '__main__':
    unittest.main()
