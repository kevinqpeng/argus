#!/usr/bin/env python3
"""发布命名评审表；版本来自已冻结需求快照。"""
import argparse
import json
import re
import sys
from pathlib import Path

from generation import file_hash, publish_bytes, require, safe_path

VERSION = re.compile(r'(?i)^v?(\d+(?:\.\d+)+)$')


def aligned_cells(line):
    if line.lstrip().startswith('|') or not line.strip():
        return None
    if '\t' not in line and not re.search(r'\s{2,}', line):
        return None
    cells = [part.strip() for part in re.split(r'\t+|\s{2,}', line.strip()) if part.strip()]
    return cells if len(cells) >= 2 else None


def collect_versions(text):
    """读取管道表或空格/制表符对齐的版本历史。正文里的业务版本不算。"""
    lines = text.splitlines()
    tables = []

    def take(column, rows):
        values = []
        for parts in rows:
            if len(parts) <= column or not parts[column]:
                continue
            match = VERSION.fullmatch(parts[column])
            if match:
                values.append(match.group(1))
            else:
                return None
        return values

    for index, line in enumerate(lines):
        if not line.lstrip().startswith('|'):
            continue
        cells = [cell.strip() for cell in line.strip().strip('|').split('|')]
        if '版本' not in cells:
            continue
        rows = []
        for row in lines[index + 2:]:
            if not row.lstrip().startswith('|'):
                break
            rows.append([cell.strip() for cell in row.strip().strip('|').split('|')])
        values = take(cells.index('版本'), rows)
        if values is None:
            return 'unparseable', []
        if values:
            tables.append(values)
    index = 0
    while index < len(lines):
        cells = aligned_cells(lines[index])
        if cells is None or '版本' not in cells:
            index += 1
            continue
        rows = []
        cursor = index + 1
        while cursor < len(lines):
            parts = aligned_cells(lines[cursor])
            if parts is None:
                break
            rows.append(parts)
            cursor += 1
        values = take(cells.index('版本'), rows)
        if values is None:
            return 'unparseable', []
        if values:
            tables.append(values)
        index = cursor
    if not tables:
        return 'no-history', []
    if len(tables) != 1:
        return 'ambiguous', tables
    return 'ok', tables


def latest_version(text):
    """版本历史可以是 Markdown 管道表，也可以是 pdftotext 的空格对齐表。"""
    reason, tables = collect_versions(text)
    if reason != 'ok':
        return None
    return 'V' + max(tables[0], key=lambda value: tuple(map(int, value.split('.'))))


def delivery_name(feature, version):
    require(isinstance(feature, str) and feature.strip() == feature and bool(feature), '缺少评审功能名称')
    require(not feature.startswith('.') and not re.search(r'[\\/:*?"<>|\x00-\x1f]', feature), '评审功能名称包含非法文件名字符')
    require(version is None or VERSION.fullmatch(version) is not None, '需求版本格式无效')
    suffix = f'({version})' if version else ''
    return f"{feature if feature.endswith('用例') else feature + '用例'}{suffix}.xlsx"


def version_status(run, source_id):
    """区分没有版本历史、解析失败、哈希或 sourceRef 错误。"""
    status = {'version': None, 'parsed': None, 'reason': 'missing-source', 'sourceRefOk': False}
    impact = json.loads(safe_path(run, 'cases/impact-analysis.json').read_text(encoding='utf-8'))
    sources = [item for item in impact['sources'] if item.get('kind') == 'prd']
    if source_id:
        sources = [item for item in sources if item.get('sourceId') == source_id]
    if len(sources) != 1:
        status['reason'] = 'ambiguous-source'
        return status
    source = sources[0]
    ref = source.get('sourceRef', '')
    if not isinstance(ref, str) or not ref.startswith('run://'):
        status['reason'] = 'bad-ref'
        return status
    status['sourceRefOk'] = True
    path = safe_path(run, ref[len('run://'):])
    if not path.is_file():
        status['reason'] = 'missing-file'
        return status
    if not source.get('sourceHash'):
        status['reason'] = 'no-hash'
        return status
    if file_hash(path) != source.get('sourceHash'):
        status['reason'] = 'hash'
        return status
    try:
        reason, tables = collect_versions(path.read_text(encoding='utf-8'))
    except UnicodeDecodeError:
        status['reason'] = 'binary'
        return status
    status['reason'] = reason
    if reason != 'ok':
        return status
    version = 'V' + max(tables[0], key=lambda value: tuple(map(int, value.split('.'))))
    status['parsed'] = version
    recorded = source.get('version')
    if recorded:
        match = VERSION.fullmatch(recorded)
        if not match or 'V' + match.group(1) != version:
            status['reason'] = 'mismatch'
            return status
    status['version'] = version
    return status


def source_version(run, source_id):
    status = version_status(run, source_id)
    if status['reason'] == 'hash':
        require(False, '需求文档快照哈希不一致')
    return status['version']


def precheck(run, feature, source_id=None):
    """渲染后、发布前核对副本、sourceRef、版本和交付名。不写交付物。"""
    run = run.resolve()
    issues = []
    try:
        status = version_status(run, source_id)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        status = {'version': None, 'reason': 'error', 'sourceRefOk': False}
        issues.append(str(exc))
    reasons = {
        'unparseable': '版本历史无法解析',
        'ambiguous': '版本历史不唯一',
        'mismatch': '记录版本与文档不一致',
        'hash': '需求文档快照哈希不一致',
        'bad-ref': 'sourceRef 不是 run://',
        'binary': '需求快照不是文本，无法读取版本历史',
        'missing-file': 'sourceRef 指向的快照不存在',
        'no-hash': '需求快照缺少 sourceHash',
        'error': '无法读取影响分析',
    }
    if status['reason'] in reasons:
        issues.append(reasons[status['reason']])
    try:
        manifest = json.loads(safe_path(run, 'manifest.json').read_text(encoding='utf-8'))
        round_id = str((manifest.get('presentation') or {}).get('round', '1'))
        internal = safe_path(run, 'cases/cases.xlsx')
        approved = safe_path(run, f'cases/presentation/{round_id}/cases.xlsx')
        if not internal.is_file() or not approved.is_file():
            issues.append('缺少内部或展示评审表')
        elif file_hash(internal) != file_hash(approved):
            issues.append('内部评审表与展示副本不一致')
    except (OSError, ValueError, KeyError, TypeError) as exc:
        issues.append(str(exc))
    version = status.get('version')
    return {'status': 'BLOCKED' if issues else 'OK', 'version': version,
            'versionReason': status.get('reason'), 'deliveryName': delivery_name(feature, version),
            'issues': issues}


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
    parser.add_argument('--precheck', action='store_true', help='渲染后检查副本、sourceRef 和版本，不写交付物')
    args = parser.parse_args()
    try:
        if args.precheck:
            report = precheck(args.run_dir, args.feature, args.source_id)
            print(json.dumps(report, ensure_ascii=False))
            return 2 if report['status'] == 'BLOCKED' else 0
        print(publish(args.run_dir, args.feature, args.source_id))
    except (ValueError, KeyError, TypeError, OSError) as exc:
        print(f'BLOCKED: {exc}', file=sys.stderr)
        return 2
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
