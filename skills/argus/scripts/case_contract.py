"""Shared structural checks; semantic sufficiency still needs source review."""


def validate_steps(steps, assertions):
    if not isinstance(steps, list) or not steps:
        raise ValueError('missing steps')
    ids = set()
    for step in steps:
        if (not isinstance(step, dict) or not isinstance(step.get('id'), str)
                or not step['id'].strip() or step['id'] in ids
                or not isinstance(step.get('action'), str) or not step['action'].strip()):
            raise ValueError('invalid or duplicate step')
        ids.add(step['id'])
        if step.get('kind', 'action') not in ('action', 'setup', 'cleanup'):
            raise ValueError('invalid step kind: ' + step['id'])
    if not isinstance(assertions, list) or not assertions:
        raise ValueError('missing assertions')
    bound = set()
    for assertion in assertions:
        if (not isinstance(assertion, dict) or not isinstance(assertion.get('stepId'), str)
                or assertion['stepId'] not in ids
                or not isinstance(assertion.get('expected'), str) or not assertion['expected'].strip()):
            raise ValueError('missing or unbound assertions')
        bound.add(assertion['stepId'])
    for step in steps:
        if step['id'] not in bound:
            reason = step.get('assertionExemption')
            if (step.get('kind', 'action') == 'action'
                    or not isinstance(reason, str) or not reason.strip()):
                raise ValueError(step['id'] + ': missing assertion or explained setup/cleanup exemption')
