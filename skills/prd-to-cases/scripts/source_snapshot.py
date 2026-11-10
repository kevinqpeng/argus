#!/usr/bin/env python3
"""Freeze or reuse a captured online PRD without trusting a URL as a version."""
import argparse
import hashlib
import json
from pathlib import Path


def digest(data):
    return hashlib.sha256(data).hexdigest()


def write_once(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open('xb') as stream:
            stream.write(data)
    except FileExistsError:
        if path.read_bytes() != data:
            raise ValueError(f'已有不同内容，禁止覆盖: {path}')


def freeze(captured, out_dir, identity, revision, evidence):
    data = Path(captured).read_bytes()
    if not data.strip():
        raise ValueError('来源快照为空')
    metadata = {'schemaVersion': 1, 'identity': identity, 'revision': revision,
                'revisionEvidence': evidence, 'sourceHash': digest(data)}
    out_dir = Path(out_dir)
    encoded = (json.dumps(metadata, ensure_ascii=False, sort_keys=True, indent=2) + '\n').encode()
    # Check both destinations before writing either to avoid a partial conflicting update.
    for path, value in ((out_dir / 'prd.md', data), (out_dir / 'source.json', encoded)):
        if path.exists() and path.read_bytes() != value:
            raise ValueError(f'已有不同内容，禁止覆盖: {path}')
    write_once(out_dir / 'prd.md', data)
    write_once(out_dir / 'source.json', encoded)
    return metadata


def reuse(previous, out_dir, identity, revision, evidence):
    if not revision or not evidence:
        raise ValueError('复用需要本次真实读取的版本和证据引用')
    previous = Path(previous)
    metadata = json.loads((previous / 'source.json').read_text(encoding='utf-8'))
    if metadata.get('schemaVersion') != 1 or metadata.get('identity') != identity or metadata.get('revision') != revision:
        raise ValueError('来源身份或版本不匹配，须重新采集')
    if not metadata.get('revisionEvidence'):
        raise ValueError('旧快照缺少版本证据，须重新采集')
    snapshot = previous / 'prd.md'
    if digest(snapshot.read_bytes()) != metadata.get('sourceHash'):
        raise ValueError('旧快照哈希不匹配，须重新采集')
    return freeze(snapshot, out_dir, identity, revision, evidence)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=['freeze', 'reuse'])
    parser.add_argument('--input', required=True, help='已采集文本路径；reuse 为旧 sources 目录')
    parser.add_argument('--out-dir', required=True, help='本 run 的 cases/sources 目录')
    parser.add_argument('--identity', required=True, help='稳定文档身份，不含凭据')
    parser.add_argument('--revision', default='', help='本次实际获取的可靠版本；不可获时留空')
    parser.add_argument('--evidence', default='', help='本次版本核查证据路径或引用')
    args = parser.parse_args()
    try:
        if not args.identity.strip():
            raise ValueError('缺少文档身份')
        if bool(args.revision) != bool(args.evidence):
            raise ValueError('版本与核查证据必须同时提供')
        fn = freeze if args.mode == 'freeze' else reuse
        result = fn(args.input, args.out_dir, args.identity, args.revision, args.evidence)
    except (OSError, ValueError, KeyError, json.JSONDecodeError) as exc:
        parser.exit(2, f'BLOCKED: {exc}\n')
    print(json.dumps(result, ensure_ascii=False))


if __name__ == '__main__':
    main()
