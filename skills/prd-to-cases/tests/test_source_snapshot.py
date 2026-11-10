import importlib.util
import tempfile
import unittest
from pathlib import Path

spec = importlib.util.spec_from_file_location('source_snapshot', Path(__file__).resolve().parents[1] / 'scripts/source_snapshot.py')
snapshot = importlib.util.module_from_spec(spec)
spec.loader.exec_module(snapshot)


class SourceSnapshotTest(unittest.TestCase):
    def test_freeze_reuse_and_stale_or_tampered_snapshot(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            capture = root / 'capture.md'
            capture.write_text('需求正文', encoding='utf-8')
            first, second = root / 'first', root / 'second'
            snapshot.freeze(capture, first, 'doc-1', 'v1', 'original-evidence')
            snapshot.reuse(first, second, 'doc-1', 'v1', 'current-evidence')
            self.assertEqual((second / 'prd.md').read_bytes(), capture.read_bytes())
            for identity, revision, evidence in [('doc-2', 'v1', 'e'), ('doc-1', 'v2', 'e'), ('doc-1', '', '')]:
                with self.subTest(identity=identity, revision=revision), self.assertRaises(ValueError):
                    snapshot.reuse(first, root / 'other', identity, revision, evidence)
            (first / 'prd.md').write_text('已篡改', encoding='utf-8')
            with self.assertRaises(ValueError):
                snapshot.reuse(first, root / 'other', 'doc-1', 'v1', 'e')
            capture.write_text('不同内容', encoding='utf-8')
            with self.assertRaises(ValueError):
                snapshot.freeze(capture, second, 'doc-1', 'v1', 'current-evidence')
            self.assertEqual((second / 'prd.md').read_text(encoding='utf-8'), '需求正文')


if __name__ == '__main__':
    unittest.main()
