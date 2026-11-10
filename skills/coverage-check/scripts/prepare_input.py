#!/usr/bin/env python3
"""Assemble coverage input from a run and explicitly identified source bytes."""
import argparse
import hashlib
import json
import re
import subprocess
from pathlib import Path

def source_bytes(source):
    if isinstance(source, str):
        return Path(source).read_bytes()
    sha, path = source['commit'], source['path']
    if not re.fullmatch(r'[0-9a-f]{40}', sha) or not path or path.startswith('/') or '..' in Path(path).parts:
        raise ValueError('invalid frozen repository source')
    return subprocess.run(['git', '-C', source['repo'], 'show', f'{sha}:{path}'],
                          check=True, capture_output=True).stdout


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('run', type=Path)
    parser.add_argument('spec', type=Path)
    parser.add_argument('output', type=Path)
    parser.add_argument('--phase', choices=['design', 'plan', 'execution'], default='design')
    args = parser.parse_args()
    protected = [args.spec, args.run / 'manifest.json', *(args.run / 'cases' / name for name in
                 ('impact-analysis.json', 'acs.json', 'scenarios.json', 'cases.json', 'execution-cases.json'))]
    if args.output.resolve() in {path.resolve() for path in protected}:
        raise ValueError('output must not replace an input')
    read = lambda path: json.loads(path.read_text(encoding='utf-8'))
    spec, manifest = read(args.spec), read(args.run / 'manifest.json')
    base = args.run / 'cases'
    impact = read(base / 'impact-analysis.json')
    source_files = spec['sourceFiles']
    if set(source_files) != {source['sourceId'] for source in impact['sources']}:
        raise ValueError('sourceFiles must name every source exactly once')
    hashed = lambda source: hashlib.sha256(source_bytes(source)).hexdigest()
    cases_path = base / ('cases.json' if args.phase == 'design' else 'execution-cases.json')
    data = {'schemaVersion': 1, 'runId': manifest['runId'],
            'inputBasis': spec.get('inputBasis', {'kind': 'prd'}),
            'impactAnalysis': impact, 'impactHash': spec['impactHash'],
            'currentSourceHashes': {key: hashed(source) for key, source in source_files.items()},
            'sourceHash': spec['sourceHash'], 'currentSourceHash': hashed(spec['sourcePath']),
            'baselineHash': spec['baselineHash'], 'acs': read(base / 'acs.json'),
            'scenarios': read(base / 'scenarios.json'), 'cases': read(cases_path),
            'requiredMatrix': manifest['requiredMatrix'], 'discovered': []}
    if args.phase != 'design':
        data['discovered'] = read(Path(spec['discoveryFile']))
    if args.phase == 'execution':
        execution = read(Path(spec['executionFile']))
        for key in ('scope', 'inputHash', 'expectedShards', 'shards', 'results'):
            data[key] = execution[key]
    content = json.dumps(data, ensure_ascii=False, indent=2) + '\n'
    if args.output.exists() and args.output.read_text(encoding='utf-8') != content:
        raise ValueError('output exists with different content')
    args.output.write_text(content, encoding='utf-8')


if __name__ == '__main__':
    main()
