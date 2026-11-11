import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / 'scripts'
SKILLS = SCRIPTS.parents[1]
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(SKILLS / 'argus/scripts'))

import phase_gate  # noqa: E402
import render_review_table as module  # noqa: E402

MISSING = ('deliverables/ 应有且只有一个「功能名称用例(V版本).xlsx」：'
           '运行 prd-to-cases/scripts/publish_review.py 发布并清理旧版本')
ARTIFACTS = ('cases.json', 'scenarios.json', 'acs.json', 'impact-analysis.json')


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def build_run(root):
    """写一个能通过展示门禁的运行目录，返回本轮目录与命名交付物路径。"""
    round_dir = root / 'cases/presentation/1'
    round_dir.mkdir(parents=True)
    written = {f'cases/{name}': ({} if name == 'impact-analysis.json' else []) for name in ARTIFACTS}
    for ref, value in written.items():
        (root / ref).write_text(json.dumps(value), encoding='utf-8')
    (root / 'cases/exploration').mkdir(parents=True, exist_ok=True)
    (root / 'cases/exploration/evidence-check.json').write_text(
        json.dumps({'status': 'PASSED', 'failed': 0, 'failures': []}), encoding='utf-8')
    (root / 'cases/cases.xlsx').write_bytes(b'workbook')
    round_files = {'content.json': json.dumps({'cases': []}), 'cases.xlsx': b'workbook',
                   'cases.md': '正文', 'mapping.json': '{}'}
    for name, value in round_files.items():
        target = round_dir / name
        target.write_bytes(value if isinstance(value, bytes) else value.encode('utf-8'))
    receipt = {'agentId': 'agent', 'model': 'model', 'effort': 'medium', 'isolation': 'separate-session',
               'promptHash': hashlib.sha256(module.prompt_text().encode('utf-8')).hexdigest(),
               'artifactHashes': {**{ref: digest(root / ref) for ref in written},
                                  **{f'cases/presentation/1/{name}': digest(round_dir / name)
                                     for name in round_files}}}
    (round_dir / 'receipt.json').write_text(json.dumps(receipt), encoding='utf-8')
    (root / 'manifest.json').write_text(json.dumps({'presentation': {'round': 1}}), encoding='utf-8')
    return round_dir, root / 'deliverables'


class PresentationDeliveryTest(unittest.TestCase):
    def gaps(self, root):
        gate = phase_gate.Gate(root)
        gate.presentation_delivery()
        return gate.gaps

    def test_named_delivery_is_required(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.assertTrue(self.gaps(root))
            _, deliverables = build_run(root)
            self.assertEqual(self.gaps(root), [MISSING])

            # 内部文件名或错误版本的交付物都视为未发布
            deliverables.mkdir()
            (deliverables / 'cases.xlsx').write_bytes(b'workbook')
            self.assertEqual(self.gaps(root), [MISSING])
            (deliverables / 'cases.xlsx').rename(deliverables / '审批用例(V0.3).xlsx')
            (deliverables / '审批用例(V0.3).xlsx').write_bytes(b'other')
            self.assertIn('命名交付物与 cases/cases.xlsx 不一致', self.gaps(root)[0])

            (deliverables / '审批用例(V0.3).xlsx').write_bytes(b'workbook')
            self.assertEqual(self.gaps(root), [])
            (deliverables / '审批用例.xlsx').write_bytes(b'workbook')
            self.assertEqual(self.gaps(root), [MISSING])


if __name__ == '__main__':
    unittest.main()
