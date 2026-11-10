import hashlib
import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

SCRIPTS = Path(__file__).resolve().parents[1] / 'scripts'
SKILLS = SCRIPTS.parents[1]
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(SKILLS / 'argus/scripts'))
spec = importlib.util.spec_from_file_location('publish_review', SCRIPTS / 'publish_review.py')
publisher = importlib.util.module_from_spec(spec)
spec.loader.exec_module(publisher)


class PublishReviewTest(unittest.TestCase):
    def test_latest_and_name(self):
        doc = '| 版本 | 日期 |\n|---|---|\n| V0.3 | 2026-11-09 |\n| V0.10 | 2026-11-10 |\n'
        self.assertEqual(publisher.latest_version(doc), 'V0.10')
        self.assertEqual(publisher.delivery_name('审批驳回后重新发起', 'V0.3'),
                         '审批驳回后重新发起用例(V0.3).xlsx')
        self.assertEqual(publisher.delivery_name('审批驳回后重新发起用例', 'V0.3'),
                         '审批驳回后重新发起用例(V0.3).xlsx')
        self.assertIsNone(publisher.latest_version('文档里有业务模型 V1.2，但没有版本历史表'))
        self.assertIsNone(publisher.latest_version('| 版本 |\n|---|\n| V0.3 |\n| V0.4-beta |\n'))
        self.assertEqual(publisher.delivery_name('审批驳回后重新发起', None),
                         '审批驳回后重新发起用例.xlsx')
        with self.assertRaises(ValueError):
            publisher.delivery_name('../其他', 'V0.3')

    def test_frozen_source_and_idempotent_publication(self):
        with tempfile.TemporaryDirectory() as tmp:
            run = Path(tmp)
            (run / 'cases/sources').mkdir(parents=True)
            (run / 'cases/presentation/1').mkdir(parents=True)
            text = '| 版本 | 日期 |\n|---|---|\n| V0.1 | 2026-10-26 |\n| V0.3 | 2026-11-09 |\n'
            source = run / 'cases/sources/prd.md'
            source.write_text(text, encoding='utf-8')
            sha = hashlib.sha256(source.read_bytes()).hexdigest()
            impact = {'sources': [{'sourceId': 'PRD', 'kind': 'prd', 'sourceRef': 'run://cases/sources/prd.md',
                                    'sourceHash': sha, 'version': 'V0.3'}]}
            (run / 'cases/impact-analysis.json').write_text(json.dumps(impact), encoding='utf-8')
            (run / 'manifest.json').write_text(json.dumps({'presentation': {'round': 1}}), encoding='utf-8')
            (run / 'cases/cases.xlsx').write_bytes(b'workbook')
            (run / 'cases/presentation/1/cases.xlsx').write_bytes(b'workbook')
            with patch('phase_gate.Gate.presentation', return_value=None):
                impact['sources'].append({**impact['sources'][0], 'sourceId': 'PRD2'})
                (run / 'cases/impact-analysis.json').write_text(json.dumps(impact), encoding='utf-8')
                self.assertIsNone(publisher.source_version(run, None))
                self.assertEqual(publisher.source_version(run, 'PRD'), 'V0.3')
                impact['sources'].pop()
                impact['sources'][0]['sourceHash'] = ''
                (run / 'cases/impact-analysis.json').write_text(json.dumps(impact), encoding='utf-8')
                self.assertIsNone(publisher.source_version(run, None))
                impact['sources'][0]['sourceHash'] = sha
                (run / 'cases/impact-analysis.json').write_text(json.dumps(impact), encoding='utf-8')
                result = publisher.publish(run, '审批驳回后重新发起')
                self.assertEqual(result.name, '审批驳回后重新发起用例(V0.3).xlsx')
                self.assertEqual(result.read_bytes(), b'workbook')
                self.assertEqual(publisher.publish(run, '审批驳回后重新发起'), result)
                impact['sources'][0]['version'] = 'V0.2'
                (run / 'cases/impact-analysis.json').write_text(json.dumps(impact), encoding='utf-8')
                fallback = publisher.publish(run, '审批驳回后重新发起')
                self.assertEqual(fallback.name, '审批驳回后重新发起用例.xlsx')
                impact['sources'][0]['version'] = 'V0.3'
                (run / 'cases/impact-analysis.json').write_text(json.dumps(impact), encoding='utf-8')
                result.write_bytes(b'other')
                with self.assertRaises(ValueError):
                    publisher.publish(run, '审批驳回后重新发起')
                source.write_text(text + ' changed', encoding='utf-8')
                with self.assertRaises(ValueError):
                    publisher.publish(run, '审批驳回后重新发起')


if __name__ == '__main__':
    unittest.main()
