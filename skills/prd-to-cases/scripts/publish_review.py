#!/usr/bin/env python3
"""发布命名评审表；版本来自已冻结需求快照。"""
import argparse
import json
import re
import sys
from pathlib import Path

from generation import file_hash, publish_bytes, require, safe_path

VERSION = re.compile(r'(?i)^v?(\d+(?:\.\d+)+)$')


def latest_version(text):
    """只读取 Markdown 版本历史表；不从正文中的业务版本推测需求版本。"""
    lines = text.splitlines()
    tables = []
    for i, line in enumerate(lines):
        cells = [cell.strip() for cell in line.strip().strip('|').split('|')]
        if not line.lstrip().startswith('|') or '版本' not in cells:
            continue
        column = cells.index('版本')
        values = []
        for row in lines[i + 2:]:
            if not row.lstrip().startswith('|'):
                break
            parts = [cell.strip() for cell in row.strip().strip('|').split('|')]
            match = VERSION.fullmatch(parts[column]) if len(parts) > column else None
            if match:
                values.append(match.group(1))
            elif len(parts) > column and parts[column]:
                return None
        if values:
            tables.append(values)
    if len(tables) != 1:
        return None
    return 'V' + max(tables[0], key=lambda v: tuple(map(int, v.split('.'))))


def delivery_name(feature, version):
    require(isinstance(feature, str) and feature.strip() == feature and bool(feature), '缺少评审功能名称')
    require(not feature.startswith('.') and not re.search(r'[\\/:*?"<>|\x00-\x1f]', feature), '评审功能名称包含非法文件名字符')
    require(version is None or VERSION.fullmatch(version) is not None, '需求版本格式无效')
    suffix = f'({version})' if version else ''
    return f"{feature if feature.endswith('用例') else feature + '用例'}{suffix}.xlsx"


def source_version(run, source_id):
    impact = json.loads(safe_path(run, 'cases/impact-analysis.json').read_text(encoding='utf-8'))
    sources = [s for s in impact['sources'] if s.get('kind') == 'prd']
    if source_id:
        sources = [s for s in sources if s.get('sourceId') == source_id]
    if len(sources) != 1:
        return None
    source = sources[0]
    ref = source.get('sourceRef', '')
    if not ref.startswith('run://'):
        return None
    path = safe_path(run, ref[len('run://'):])
    if not path.is_file():
        return None
    if not source.get('sourceHash'):
        return None
    require(file_hash(path) == source.get('sourceHash'), '需求文档快照哈希不一致')
    try:
        version = latest_version(path.read_text(encoding='utf-8'))
    except UnicodeDecodeError:
        return None
    if version is None:
        return None
    recorded = source.get('version')
    if recorded:
        match = VERSION.fullmatch(recorded)
        if not match or 'V' + match.group(1) != version:
            return None
    return version


def publish(run, feature, source_id=None):
    run = run.resolve()
    version = source_version(run, source_id)
    name = delivery_name(feature, version)
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'argus/scripts'))
    from phase_gate import Gate
    gate = Gate(run)
    gate.presentation()
    require(not gate.gaps, '展示门禁未通过：' + '; '.join(gate.gaps))
    manifest = json.loads(safe_path(run, 'manifest.json').read_text(encoding='utf-8'))
    round_id = str((manifest.get('presentation') or {}).get('round', '1'))
    internal = safe_path(run, 'cases/cases.xlsx')
    approved = safe_path(run, f'cases/presentation/{round_id}/cases.xlsx')
    require(internal.is_file() and approved.is_file() and file_hash(internal) == file_hash(approved),
            '内部评审表与展示门禁校验的文件不一致')
    directory = safe_path(run, 'deliverables')
    directory.mkdir(parents=True, exist_ok=True)
    target = safe_path(run, 'deliverables/' + name)
    publish_bytes(target, internal.read_bytes())
    return target


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('run_dir', type=Path)
    parser.add_argument('--feature', required=True, help='本轮评审功能名称')
    parser.add_argument('--source-id', help='多份 PRD 时指定版本来源')
    args = parser.parse_args()
    try:
        print(publish(args.run_dir, args.feature, args.source_id))
    except (ValueError, KeyError, TypeError, OSError) as exc:
        print(f'BLOCKED: {exc}', file=sys.stderr)
        return 2
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
