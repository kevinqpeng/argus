#!/usr/bin/env python3
"""Prepare independent semantic questions and assemble their real Jev receipts."""
import argparse
import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'decision-router/scripts'))
import jev
from phase_gate import (JUDGED_SOURCE_KINDS, SEMANTIC_GROUPS, semantic_document_hash,
                        semantic_documents, semantic_input_hash, semantic_subject_hashes,
                        source_content)

GROUPS = SEMANTIC_GROUPS


def encoded(value):
    return json.dumps(value, ensure_ascii=False, separators=(',', ':'), allow_nan=False).encode()


def sha(data):
    return hashlib.sha256(data).hexdigest()


def read(path):
    return json.loads(path.read_text(encoding='utf-8'))


def emit(path, value):
    data = json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + '\n'
    if path.exists() and path.read_text(encoding='utf-8') == data:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(data, encoding='utf-8')
    return True


def expected_subjects(run):
    cases = read(run / 'cases/cases.json')
    impact = read(run / 'cases/impact-analysis.json')
    ids = {row['caseId'] for row in cases}
    return {'sources': {s['sourceId'] for s in impact['sources']
                        if s['kind'] in {'prd', 'history', 'contract', 'business_rule'}},
            'impacts': {c['checkId'] for c in impact['checks']},
            'cases': ids, 'realization': ids, 'ruleSupport': ids}


def batch_current(run, plan, batch):
    if plan.get('questionSetVersion') != jev.DEFINITIONS['questionSetVersion']:
        return False
    mapping = batch.get('mapping') or {}
    if mapping and all(isinstance(item, dict) and 'subjectHash' in item for item in mapping.values()):
        hashes = semantic_subject_hashes(run)
        return all(hashes.get(item['group'], {}).get(item['id']) == item['subjectHash'] for item in mapping.values())
    return plan.get('inputHash') == semantic_document_hash(run)


def prepare(run, subjects_path, incremental=False):
    existing = run / 'cases/semantic-review.json'
    if not incremental and existing.is_file():
        review = read(existing)
        if isinstance(review, dict) and review.get('inputHash') == semantic_input_hash(run):
            raise ValueError('semantic review already recorded for this input; run report and do not rerun')
    expected = expected_subjects(run)
    if incremental:
        selected = read(subjects_path)
        expected = {group: set() for group in GROUPS}
        for item in selected:
            expected[item['group']].add(item['id'])
    hashes = semantic_subject_hashes(run)
    subjects = read(subjects_path)
    actual = {key: set() for key in GROUPS}
    batches, questions, state, mapping = [], {}, {}, {}

    def flush():
        if questions:
            payload = {'questions': dict(questions), 'state': {'subjects': dict(state)}}
            jev.build_request(payload)
            batches.append((payload, dict(mapping)))
            questions.clear(); state.clear(); mapping.clear()

    for item in subjects:
        group, ident = item['group'], item['id']
        if group not in GROUPS or ident not in expected[group]:
            raise ValueError(f'unknown subject: {group}/{ident}')
        if ident in actual[group]:
            raise ValueError(f'duplicate subject: {group}/{ident}')
        actual[group].add(ident)
        name = f'{group}:{ident}'
        evidence = item['evidence']
        questions[name] = {'questionId': GROUPS[group], 'subject': item['subject']}
        state[name] = {'evidence': evidence, 'evidenceHash': jev.evidence_hash(evidence)}
        mapping[name] = {'group': group, 'id': ident, 'subjectHash': hashes[group][ident]}
        try:
            jev.build_request({'questions': questions, 'state': {'subjects': state}})
        except ValueError as exc:
            if 'exceeds local byte budget' not in str(exc) or len(questions) == 1:
                raise
            q, s, m = questions.pop(name), state.pop(name), mapping.pop(name)
            flush()
            questions[name], state[name], mapping[name] = q, s, m
    flush()
    if any(actual[group] != expected[group] for group in GROUPS):
        raise ValueError('semantic subjects incomplete')
    directory = run / ('cases/semantic-repair-batch' if incremental else 'cases/semantic-batch')
    directory.mkdir(parents=True, exist_ok=True)
    previous_path = directory / 'plan.json'
    previous = read(previous_path) if previous_path.is_file() else {'batches': []}
    previous_batches = {batch.get('name'): batch for batch in previous.get('batches') or []}
    plan = {'inputHash': semantic_input_hash(run), 'questionSetVersion': jev.DEFINITIONS['questionSetVersion'],
            'incremental': incremental, 'batches': []}
    planned = []
    for index, (payload, mappings) in enumerate(batches, 1):
        name = f'{index:04d}'
        data = json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False) + '\n'
        prior = previous_batches.get(name)
        batch_path = directory / f'{name}.input.json'
        if batch_path.is_file() and batch_path.read_text(encoding='utf-8') == data and prior and not _hashes_equal(prior, mappings):
            raise ValueError(f'subjects stale for batch {name}; regenerate with semantic_batch.py subjects')
        planned.append((name, data, mappings))
    kept = set()
    rewritten = reused = 0
    for name, data, mappings in planned:
        kept.add(name)
        path = directory / f'{name}.input.json'
        if path.is_file() and path.read_text(encoding='utf-8') == data:
            reused += 1
        else:
            path.write_text(data, encoding='utf-8')
            rewritten += 1
            for suffix in ('raw.json', 'out.json'):
                stale = directory / f'{name}.{suffix}'
                if stale.exists():
                    stale.unlink()
        plan['batches'].append({'name': name, 'inputFileHash': sha(path.read_bytes()), 'mapping': mappings})
    for path in directory.glob('*.json'):
        if path.name == 'plan.json':
            continue
        if path.name.split('.', 1)[0] not in kept:
            path.unlink()
    emit(directory / 'plan.json', plan)
    print(json.dumps({'batches': len(batches), 'questions': len(subjects), 'reused': reused, 'rewritten': rewritten}))



def _hashes_equal(prior, mappings):
    old = prior.get('mapping') or {}
    if set(old) != set(mappings):
        return False
    return all(isinstance(old.get(key), dict) and old[key].get('subjectHash') == item.get('subjectHash')
               for key, item in mappings.items())


def assemble(run, incremental=False):
    directory = run / ('cases/semantic-repair-batch' if incremental else 'cases/semantic-batch')
    plan = read(directory / 'plan.json')
    if plan['questionSetVersion'] != jev.DEFINITIONS['questionSetVersion']:
        raise ValueError('semantic inputs or question set changed')
    hashes = semantic_subject_hashes(run)
    existing_path = run / 'cases/semantic-review.json'
    existing = read(existing_path) if existing_path.is_file() else None
    if not isinstance(existing, dict):
        existing = {}
    review = {'schemaVersion': 1, 'inputHash': semantic_input_hash(run),
              'questionSetVersion': plan['questionSetVersion'], 'model': None,
              **{group: {} for group in GROUPS}}
    if incremental:
        for group in GROUPS:
            review[group].update(existing.get(group) or {})
    expected = expected_subjects(run)
    if incremental:
        expected = {group: set() for group in GROUPS}
        for batch in plan['batches']:
            for target in (batch.get('mapping') or {}).values():
                expected[target['group']].add(target['id'])
    seen_names = set()
    stale = []
    for batch in plan['batches']:
        name = batch['name']
        if not isinstance(name, str) or not name.isdecimal() or name in seen_names:
            raise ValueError('invalid or duplicate batch name')
        seen_names.add(name)
        if not batch_current(run, plan, batch):
            stale.append(name)
            continue
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
                       'evidenceHash': sha(out_path.read_bytes()), 'subjectHash': hashes[group][ident]}
            previous = review[group].get(ident)
            kept = (existing.get(group) or {}).get(ident)
            if isinstance(kept, dict) and kept.get('acceptedReason') and all(
                    kept.get(key) == verdict[key] for key in ('final', 'evidenceRef', 'evidenceHash')):
                verdict['acceptedReason'] = kept['acceptedReason']
            if previous is None or verdict['final'] < previous['final']:
                review[group][ident] = verdict
    if stale or any(set(review[group]) != ids for group, ids in expected.items()):
        missing = [f'{group}/{ident}' for group, ids in expected.items() for ident in sorted(ids - set(review[group]))]
        raise ValueError('semantic subjects stale or incomplete: ' + ', '.join((stale or missing)[:12]))
    emit(run / ('cases/semantic-repair-review.json' if incremental else 'cases/semantic-review.json'), review)


def request(run, name, record=False, incremental=False):
    if not name.isdecimal():
        raise ValueError('invalid batch name')
    directory = run / ('cases/semantic-repair-batch' if incremental else 'cases/semantic-batch')
    if not (directory / 'plan.json').is_file():
        raise ValueError('semantic batch plan is missing')
    plan = read(directory / 'plan.json')
    batch = next(item for item in plan['batches'] if item['name'] == name)
    path = directory / f'{name}.input.json'
    if not batch_current(run, plan, batch) or sha(path.read_bytes()) != batch['inputFileHash']:
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
    emit(directory / f'{name}.out.json', result)


def _validate_subject(item):
    name = f"{item['group']}:{item['id']}"
    evidence = item['evidence']
    jev.build_request({'questions': {name: {'questionId': GROUPS[item['group']], 'subject': item['subject']}},
                       'state': {'subjects': {name: {'evidence': evidence, 'evidenceHash': jev.evidence_hash(evidence)}}}})


def subject_records(run):
    docs = semantic_documents(run)
    impact = docs['impact-analysis']
    units = [row for row in impact.get('sourceUnits') or [] if isinstance(row, dict)]
    obligations = [row for row in impact.get('obligations') or [] if isinstance(row, dict)]
    objects = {row.get('objectId'): row for row in impact.get('objects') or [] if isinstance(row, dict)}
    ac_by_id = {row.get('acId'): row for row in docs['acs'] if isinstance(row, dict)}
    scenario_by_id = {row.get('scenarioId'): row for row in docs['scenarios'] if isinstance(row, dict)}
    gaps, records = [], []

    def add(item):
        try:
            _validate_subject(item)
        except ValueError as exc:
            gaps.append(f"{item['group']}/{item['id']}: {exc}")
            return
        records.append(item)

    judged = [row for row in impact['sources'] if isinstance(row, dict) and row.get('kind') in JUDGED_SOURCE_KINDS]
    for source in sorted(judged, key=lambda row: row['sourceId']):
        sid = source['sourceId']
        content = source_content(run, source)
        related_units = [row for row in units if sid in (row.get('sourceIds') or [])]
        related = [row for row in obligations if sid in (row.get('sourceIds') or [])]
        if not content.strip():
            gaps.append(f'sources/{sid}: 缺少 sourceContent')
            continue
        absent = [str(row.get('unitId') or sid) for row in related_units
                  if not isinstance(row.get('sourceExcerpt'), str) or row['sourceExcerpt'] not in content]
        if absent:
            gaps.append(f"sources/{sid}: 摘录不在原文中 {', '.join(absent[:4])}")
            continue
        add({'group': 'sources', 'id': sid, 'subject': f"来源 {sid}（{source.get('kind')}）是否保留全部可测规则",
             'evidence': {'sourceContent': content, 'sourceUnits': related_units, 'obligations': related, 'source': source}})
    for check in sorted((row for row in impact['checks'] if isinstance(row, dict)), key=lambda row: row['checkId']):
        cid = check['checkId']
        change = objects.get(check.get('objectId'))
        dependency = {'reason': check.get('reason'), 'evidenceRef': check.get('evidenceRef'),
                      'disposition': check.get('disposition'),
                      'obligations': [row for row in obligations if row.get('obligationId') in set(check.get('obligationIds') or [])]}
        if not isinstance(change, dict) or not str(change.get('description') or '').strip() or not (dependency.get('reason') or dependency.get('evidenceRef')):
            gaps.append(f'impacts/{cid}: 缺少变更对象或依赖证据')
            continue
        add({'group': 'impacts', 'id': cid, 'subject': f"影响检查 {cid} / {check.get('dimension')} 的处置是否有证据",
             'evidence': {'change': change, 'check': check, 'dependencyEvidence': dependency}})

    for case in sorted((row for row in docs['cases'] if isinstance(row, dict)), key=lambda row: row['caseId']):
        cid = case['caseId']
        scenarios, acs, ac_ids = [], [], set()
        for scenario_id in case.get('scenarioIds') or []:
            scenario = scenario_by_id.get(scenario_id)
            if not isinstance(scenario, dict):
                continue
            scenarios.append(scenario)
            for ac_id in scenario.get('acIds') or []:
                ac = ac_by_id.get(ac_id)
                if isinstance(ac, dict):
                    acs.append(ac)
                    ac_ids.add(ac_id)
        rules = [row for row in obligations if set(row.get('acIds') or []) & ac_ids]
        bound = {row.get('obligationId') for row in rules}
        related_units = [row for row in units if set(row.get('obligationIds') or []) & bound]
        texts = [ac.get('text') for ac in acs if isinstance(ac.get('text'), str) and ac['text'].strip()]
        acceptance = texts[0] if len(texts) == 1 else ({'acs': [{'acId': ac.get('acId'), 'text': ac.get('text')} for ac in acs]} if texts else None)
        if not acceptance:
            gaps.append(f'cases/{cid}: 缺少关联验收条件')
            continue
        if not rules:
            gaps.append(f'ruleSupport/{cid}: 缺少关联义务')
            continue
        evidence = {'acceptance': acceptance, 'fixtures': case.get('fixtures') if isinstance(case.get('fixtures'), list) else [],
                    'steps': case.get('steps') or [], 'assertions': case.get('assertions') or [],
                    'case': case, 'scenarios': scenarios, 'acs': acs, 'obligations': rules, 'units': related_units}
        label = acceptance if isinstance(acceptance, str) else cid
        add({'group': 'cases', 'id': cid, 'subject': f'用例 {cid} 的断言是否验证验收条件：{label}', 'evidence': evidence})
        add({'group': 'realization', 'id': cid, 'subject': f'用例 {cid} 的步骤是否构成验收条件：{label}', 'evidence': evidence})
        add({'group': 'ruleSupport', 'id': cid, 'subject': f'用例 {cid} 的预期是否有规则依据：{label}', 'evidence': evidence})
    return records, gaps


def outline(run):
    records, gaps = subject_records(run)
    reusable, stale = [], []
    plan_path = run / 'cases/semantic-batch/plan.json'
    if plan_path.is_file():
        plan = read(plan_path)
        for batch in plan.get('batches') or []:
            (reusable if batch_current(run, plan, batch) else stale).append(batch.get('name'))
    print(json.dumps({
        'questionSetVersion': jev.DEFINITIONS['questionSetVersion'],
        'maxBytes': jev.DEFINITIONS['textStateContract']['maxBytes'],
        'questions': len(records),
        'counts': {group: sum(item['group'] == group for item in records) for group in GROUPS},
        'missing': gaps,
        'reusableBatches': reusable,
        'staleBatches': stale,
    }, ensure_ascii=False, indent=2))


def write_subjects(run, output):
    records, gaps = subject_records(run)
    if gaps:
        raise ValueError('semantic subjects incomplete: ' + '; '.join(gaps[:12]))
    emit(output, records)
    print(json.dumps({'subjects': len(records)}))


def low_scores(run):
    path = run / 'cases/semantic-review.json'
    threshold = jev.DEFINITIONS['thresholds']['noulYes']
    if not path.is_file():
        return threshold, []
    review = read(path)
    below = []
    for group in GROUPS:
        for ident, verdict in (review.get(group) or {}).items():
            score = verdict.get('final') if isinstance(verdict, dict) else None
            if type(score) in (int, float) and score < threshold:
                below.append({'group': group, 'id': ident, 'final': score})
    return threshold, below


def repair_plan(run):
    """Create deterministic, disjoint repair shards for every low semantic subject."""
    threshold, below = low_scores(run)
    records, gaps = subject_records(run)
    by_key = {(item['group'], item['id']): item for item in records}
    case_ids = sorted({item['id'] for item in below if item['group'] in {'cases', 'realization', 'ruleSupport'}})
    # Keep all dimensions of one case together; source/impact evidence is isolated.
    groups = [('cases', case_ids[i:i + 10]) for i in range(0, len(case_ids), 10)]
    groups += [('evidence', sorted({item['id'] for item in below if item['group'] in {'sources', 'impacts'}}))]
    groups = [(kind, ids) for kind, ids in groups if ids]
    root = run / 'cases/semantic-repair'
    plan = {'schemaVersion': 1, 'inputHash': semantic_input_hash(run),
            'threshold': threshold, 'sourceReviewHash': sha((run / 'cases/semantic-review.json').read_bytes()),
            'shards': []}
    for index, (kind, ids) in enumerate(groups, 1):
        subjects = []
        if kind == 'cases':
            for ident in ids:
                subjects.extend(by_key[(group, ident)] for group in ('cases', 'realization', 'ruleSupport')
                                if (group, ident) in by_key)
        else:
            subjects.extend(by_key[key] for key in by_key if key[0] in {'sources', 'impacts'} and key[1] in ids)
        name = f'{index:04d}'
        emit(root / f'{name}.input.json', {'shardId': name, 'kind': kind, 'caseIds': ids,
                                           'subjects': subjects, 'lowScores': [x for x in below
                                           if x['id'] in ids]})
        plan['shards'].append({'shardId': name, 'kind': kind, 'ids': ids,
                              'inputHash': sha((root / f'{name}.input.json').read_bytes())})
    emit(root / 'plan.json', plan)
    return {'threshold': threshold, 'lowScores': below, 'shards': len(groups), 'gaps': gaps}


def validate_repair(run, shard_name):
    """Validate one agent receipt without mutating the canonical cases."""
    root = run / 'cases/semantic-repair'
    plan = read(root / 'plan.json')
    shard = next(x for x in plan['shards'] if x['shardId'] == shard_name)
    result = read(root / f'{shard_name}.result.json')
    if result.get('shardId') != shard_name or result.get('inputHash') != shard['inputHash']:
        raise ValueError('repair receipt does not match shard input')
    actions = result.get('actions')
    if not isinstance(actions, list) or not actions:
        raise ValueError('repair receipt has no actions')
    allowed = set(shard['ids'])
    for action in actions:
        if action.get('caseId') not in allowed:
            raise ValueError('repair action escapes shard')
        kind = action.get('disposition')
        if kind not in {'repaired', 'falsePositive'} or not str(action.get('reason') or '').strip():
            raise ValueError('repair action needs disposition and reason')
        if kind == 'falsePositive' and (not str(action.get('evidenceRef') or '').strip()
                                         or not str(action.get('reviewer') or '').strip()
                                         or not str(action.get('evidenceHash') or '').strip()):
            raise ValueError('false positive needs independent evidenceRef, evidenceHash and reviewer')
        if kind == 'repaired' and not isinstance(action.get('patch'), dict):
            raise ValueError('repaired action needs patch')
        if action.get('group') not in {'cases', 'realization', 'ruleSupport', 'sources', 'impacts'}:
            raise ValueError('repair action needs group')
    emit(root / f'{shard_name}.validated.json', {'shardId': shard_name, 'inputHash': shard['inputHash'],
                                                  'resultHash': sha((root / f'{shard_name}.result.json').read_bytes())})
    return True


def assemble_repairs(run):
    root = run / 'cases/semantic-repair'
    plan = read(root / 'plan.json')
    cases = read(run / 'cases/cases.json')
    by_id = {case['caseId']: case for case in cases}
    manifest = {'schemaVersion': 1, 'inputHash': plan['inputHash'], 'actions': []}
    seen = set()
    for shard in plan['shards']:
        validate_repair(run, shard['shardId'])
        result = read(root / f"{shard['shardId']}.result.json")
        for action in result['actions']:
            key = (action['group'], action['caseId'])
            if key in seen:
                raise ValueError('duplicate repair action')
            seen.add(key)
            if action['disposition'] == 'repaired':
                if action['group'] not in {'cases', 'realization', 'ruleSupport'}:
                    raise ValueError('only case subjects may be repaired')
                case = by_id[action['caseId']]
                patch = action['patch']
                for field in ('fixtures', 'steps', 'assertions'):
                    if field in patch:
                        case[field] = patch[field]
            manifest['actions'].append(action)
    emit(run / 'cases/cases.json', cases)
    emit(root / 'manifest.json', manifest)
    return manifest


def repair_subjects(run, output):
    """Build Jev subjects only for low scores after the repair agents finish."""
    threshold, below = low_scores(run)
    manifest_path = run / 'cases/semantic-repair/manifest.json'
    if not manifest_path.is_file():
        raise ValueError('repair manifest is missing')
    manifest = read(manifest_path)
    actions = {(item.get('group'), item.get('caseId')): item for item in manifest.get('actions', [])}
    records, _ = subject_records(run)
    selected = []
    for item in records:
        verdict = next((x for x in below if x['group'] == item['group'] and x['id'] == item['id']), None)
        action = actions.get((item['group'], item['id'])) or actions.get(('cases', item['id']))
        if verdict and action and action.get('disposition') == 'repaired':
            selected.append(item)
    if not selected:
        raise ValueError('no repaired subjects require incremental semantic review')
    emit(output, selected)
    print(json.dumps({'subjects': len(selected), 'threshold': threshold}))


def finalize_repairs(run):
    root = run / 'cases/semantic-repair'
    assemble_repairs(run)
    assemble(run, incremental=True)
    updated = read(run / 'cases/semantic-repair-review.json')
    canonical_path = run / 'cases/semantic-review.json'
    canonical = read(canonical_path)
    manifest = read(root / 'manifest.json')
    for group in GROUPS:
        canonical.setdefault(group, {}).update(updated.get(group) or {})
    canonical['inputHash'] = semantic_input_hash(run)
    canonical['model'] = updated.get('model') or canonical.get('model')
    for action in manifest.get('actions', []):
        if action.get('disposition') == 'repaired':
            verdict = (updated.get(action.get('group')) or {}).get(action.get('caseId'))
            if verdict is not None:
                action['postFinal'] = verdict.get('final')
                action['postEvidenceRef'] = verdict.get('evidenceRef')
                action['postEvidenceHash'] = verdict.get('evidenceHash')
    emit(canonical_path, canonical)
    emit(root / 'manifest.json', manifest)
    return manifest


def report(run):
    """Report low scores and whether each has entered the repair workflow."""
    threshold, below = low_scores(run)
    manifest = read(run / 'cases/semantic-repair/manifest.json') if (run / 'cases/semantic-repair/manifest.json').is_file() else {}
    resolved = {(a.get('caseId'), a.get('disposition')) for a in manifest.get('actions', [])}
    for item in below:
        item['repairStatus'] = 'falsePositive' if (item['id'], 'falsePositive') in resolved else ('repaired' if (item['id'], 'repaired') in resolved else 'pending')
    return {'status': 'ABSENT' if not below and not manifest else 'REPORTED', 'threshold': threshold, 'below': below}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('run', type=Path)
    parser.add_argument('action', choices=['prepare', 'request', 'record', 'assemble', 'outline', 'subjects', 'report', 'repair-plan', 'repair-prepare', 'repair-request', 'repair-record', 'repair-validate', 'repair-assemble', 'repair-subjects'])
    parser.add_argument('argument', nargs='?')
    args = parser.parse_args()
    run = args.run.resolve()
    if args.action == 'prepare':
        if args.argument is None:
            parser.error('prepare requires subjects')
        prepare(run, Path(args.argument))
    elif args.action in ('request', 'record', 'repair-request', 'repair-record'):
        if args.argument is None:
            parser.error('request/record requires batch name')
        incremental = args.action.startswith('repair-')
        request(run, args.argument, record=args.action.endswith('record'), incremental=incremental)
    elif args.action == 'outline':
        outline(run)
    elif args.action == 'report':
        print(json.dumps(report(run), ensure_ascii=False))
    elif args.action == 'repair-prepare':
        if args.argument is None:
            parser.error('repair-prepare requires subjects')
        prepare(run, Path(args.argument), incremental=True)
    elif args.action == 'repair-plan':
        print(json.dumps(repair_plan(run), ensure_ascii=False, indent=2))
    elif args.action == 'repair-validate':
        if args.argument is None:
            parser.error('repair-validate requires shard name')
        validate_repair(run, args.argument)
    elif args.action == 'repair-assemble':
        print(json.dumps(finalize_repairs(run), ensure_ascii=False, indent=2))
    elif args.action == 'repair-subjects':
        if args.argument is None:
            parser.error('repair-subjects requires output path')
        repair_subjects(run, Path(args.argument))
    elif args.action == 'subjects':
        if args.argument is None:
            parser.error('subjects requires output path')
        write_subjects(run, Path(args.argument))
    else:
        assemble(run)


if __name__ == '__main__':
    main()
