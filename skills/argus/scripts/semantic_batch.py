#!/usr/bin/env python3
"""Prepare independent semantic questions and assemble their real Jev receipts."""
import argparse
import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'decision-router/scripts'))
import jev
from phase_gate import semantic_input_hash

GROUPS = {'sources': 'source_inventory', 'impacts': 'impact_evidence',
          'cases': 'mapping', 'realization': 'scenario_realization', 'ruleSupport': 'rule_support'}


def encoded(value):
    return json.dumps(value, ensure_ascii=False, separators=(',', ':'), allow_nan=False).encode()


def sha(data):
    return hashlib.sha256(data).hexdigest()


def read(path):
    return json.loads(path.read_text(encoding='utf-8'))


def write(path, value):
    data = json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + '\n'
    if path.exists() and path.read_text(encoding='utf-8') != data:
        raise ValueError(f'file exists with different content: {path}')
    path.write_text(data, encoding='utf-8')


def expected_subjects(run):
    cases = read(run / 'cases/cases.json')
    impact = read(run / 'cases/impact-analysis.json')
    ids = {row['caseId'] for row in cases}
    return {'sources': {s['sourceId'] for s in impact['sources']
                        if s['kind'] in {'prd', 'history', 'contract', 'business_rule'}},
            'impacts': {c['checkId'] for c in impact['checks']},
            'cases': ids, 'realization': ids, 'ruleSupport': ids}


def prepare(run, subjects_path):
    expected = expected_subjects(run)
    subjects = read(subjects_path)
    actual = {key: set() for key in GROUPS}
    batches, questions, state, mapping = [], {}, {}, {}

    def flush():
        if questions:
            payload = {'questions': dict(questions), 'state': {'subjects': dict(state)}}
            jev.build_request(payload)
            batches.append((payload, dict(mapping)))
            questions.clear(); state.clear(); mapping.clear()

    for index, item in enumerate(subjects):
        group, ident = item['group'], item['id']
        if group not in GROUPS or ident not in expected[group]:
            raise ValueError(f'unknown subject: {group}/{ident}')
        actual[group].add(ident)
        name = f'q{index + 1:06d}'
        evidence = item['evidence']
        questions[name] = {'questionId': GROUPS[group], 'subject': item['subject']}
        state[name] = {'evidence': evidence, 'evidenceHash': jev.evidence_hash(evidence)}
        mapping[name] = {'group': group, 'id': ident}
        try:
            jev.build_request({'questions': questions, 'state': {'subjects': state}})
        except ValueError as exc:
            if 'exceeds local byte budget' not in str(exc) or len(questions) == 1:
                raise
            q, s, m = questions.pop(name), state.pop(name), mapping.pop(name)
            flush()
            questions[name], state[name], mapping[name] = q, s, m
    flush()
    if actual != expected:
        raise ValueError('semantic subjects incomplete')
    directory = run / 'cases/semantic-batch'
    directory.mkdir(parents=True, exist_ok=True)
    plan = {'inputHash': semantic_input_hash(run), 'questionSetVersion': jev.DEFINITIONS['questionSetVersion'], 'batches': []}
    for index, (payload, mappings) in enumerate(batches, 1):
        name = f'{index:04d}'
        path = directory / f'{name}.input.json'
        write(path, payload)
        plan['batches'].append({'name': name, 'inputFileHash': sha(path.read_bytes()), 'mapping': mappings})
    write(directory / 'plan.json', plan)
    print(json.dumps({'batches': len(batches), 'questions': len(subjects)}))


def assemble(run):
    directory = run / 'cases/semantic-batch'
    plan = read(directory / 'plan.json')
    if plan['inputHash'] != semantic_input_hash(run) or plan['questionSetVersion'] != jev.DEFINITIONS['questionSetVersion']:
        raise ValueError('semantic inputs or question set changed')
    review = {'schemaVersion': 1, 'inputHash': plan['inputHash'],
              'questionSetVersion': plan['questionSetVersion'], 'model': None,
              **{group: {} for group in GROUPS}}
    expected = expected_subjects(run)
    seen_names = set()
    for batch in plan['batches']:
        name = batch['name']
        if not isinstance(name, str) or not name.isdecimal() or name in seen_names:
            raise ValueError('invalid or duplicate batch name')
        seen_names.add(name)
        inp = directory / f'{name}.input.json'
        if sha(inp.read_bytes()) != batch['inputFileHash']:
            raise ValueError('semantic request changed')
        payload = read(inp)
        request, _ = jev.build_request(payload)
        out_path = directory / f'{name}.out.json'
        result, raw = read(out_path), read(directory / f'{name}.raw.json')
        normalized = jev.normalize(raw, request['questions'])
        bindings = {key: value['questionId'] for key, value in payload['questions'].items()}
        if (result['questionSetVersion'] != plan['questionSetVersion'] or result['answers'] != normalized['answers']
                or result['questionBindings'] != bindings or result['request']['inputHash'] != sha(encoded(request))
                or result['request']['actualModel'] != raw['model'] or set(batch['mapping']) != set(bindings)):
            raise ValueError('Jev receipt does not match request and raw response')
        if review['model'] not in (None, raw['model']):
            raise ValueError('mixed models in semantic review')
        review['model'] = raw['model']
        for key, target in batch['mapping'].items():
            group, ident = target['group'], target['id']
            if group not in GROUPS or ident not in expected[group] or bindings[key] != GROUPS[group]:
                raise ValueError('question binding mismatch')
            answer = result['answers'][key]
            jev.route(answer, jev.DEFINITIONS['questions'][bindings[key]])
            verdict = {'final': answer['noul'], 'evidenceRef': str(out_path.relative_to(run)),
                       'evidenceHash': sha(out_path.read_bytes())}
            previous = review[group].get(ident)
            if previous is None or verdict['final'] < previous['final']:
                review[group][ident] = verdict
    if any(set(review[group]) != ids for group, ids in expected.items()):
        raise ValueError('semantic subjects incomplete')
    write(run / 'cases/semantic-review.json', review)


def request(run, name, record=False):
    if not name.isdecimal():
        raise ValueError('invalid batch name')
    directory = run / 'cases/semantic-batch'
    plan = read(directory / 'plan.json')
    batch = next(item for item in plan['batches'] if item['name'] == name)
    path = directory / f'{name}.input.json'
    if (plan['inputHash'] != semantic_input_hash(run) or
            plan['questionSetVersion'] != jev.DEFINITIONS['questionSetVersion'] or
            sha(path.read_bytes()) != batch['inputFileHash']):
        raise ValueError('semantic inputs changed')
    payload = read(path)
    request_data, _ = jev.build_request(payload)
    if not record:
        print(json.dumps({'questions': request_data['questions'], 'state': request_data['state']}, ensure_ascii=False))
        return
    raw = read(directory / f'{name}.raw.json')
    result = jev.normalize(raw, request_data['questions'])
    result['questionBindings'] = {key: value['questionId'] for key, value in payload['questions'].items()}
    for key, answer in result['answers'].items():
        jev.route(answer, jev.DEFINITIONS['questions'][result['questionBindings'][key]])
    result['request'] = {'actualModel': raw['model'], 'inputHash': sha(encoded(request_data)),
                         'transport': 'jev_ask'}
    write(directory / f'{name}.out.json', result)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('run', type=Path)
    parser.add_argument('action', choices=['prepare', 'request', 'record', 'assemble'])
    parser.add_argument('argument', nargs='?')
    args = parser.parse_args()
    if args.action == 'prepare':
        if args.argument is None:
            parser.error('prepare requires subjects')
        prepare(args.run.resolve(), Path(args.argument))
    elif args.action in ('request', 'record'):
        if args.argument is None:
            parser.error('request/record requires batch name')
        request(args.run.resolve(), args.argument, record=args.action == 'record')
    else:
        assemble(args.run.resolve())


if __name__ == '__main__':
    main()
