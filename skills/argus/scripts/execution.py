#!/usr/bin/env python3
"""Freeze execution capacity and credential-free resource alternatives for a new run."""
import argparse
import copy
import itertools
import json
from pathlib import Path
import stat
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'prd-to-cases/scripts'))
from generation import digest, require


def account_entries(data):
    """Pool entries take precedence for a role/tenant; legacy accounts remain usable."""
    result = []
    pools = data.get('accountPools', {})
    require(isinstance(pools, dict), 'accountPools must be an object')
    for role, tenants in pools.items():
        require(isinstance(role, str) and role and isinstance(tenants, dict), 'invalid pool role')
        for tenant, entries in tenants.items():
            require(isinstance(tenant, str) and isinstance(entries, list), 'invalid pool tenant')
            for index, entry in enumerate(entries):
                result.append((role, tenant, ['accountPools', role, tenant, index], entry))
    legacy = data.get('accounts', {})
    require(isinstance(legacy, dict), 'accounts must be an object')
    for role, entry in legacy.items():
        require(isinstance(entry, dict), 'invalid legacy account')
        tenant = entry.get('tenant', '')
        if role not in pools or tenant not in pools[role]:
            result.append((role, tenant, ['accounts', role], entry))
    for role, tenant, ref, entry in result:
        require(isinstance(role, str) and role and isinstance(tenant, str), 'invalid account grouping')
        require(isinstance(entry, dict) and not set(entry) -
                {'username', 'password', 'tenant', 'principalId', 'loginPolicyVerified'}, 'invalid account fields')
        require(all(isinstance(entry.get(k), str) and entry[k].strip() for k in ('username', 'password')),
                'account needs username and password')
        require(type(entry.get('loginPolicyVerified', False)) is bool, 'invalid login policy verification')
        require('principalId' not in entry or isinstance(entry['principalId'], str) and entry['principalId'].strip(),
                'invalid principal identity')
        require('tenant' not in entry or entry['tenant'] == tenant, 'account tenant mismatch')
    return result


def account_resources(account):
    resources = ['account:user:' + digest(account['username'].strip().casefold())]
    if account.get('principalId'):
        resources.append('account:principal:' + digest(account['principalId'].strip().casefold()))
    return resources


def resolve_accounts(data, binding):
    """Runner-only: return credentials in memory after checking frozen account identity."""
    account_entries(data)
    accounts = []
    for ref in binding.get('accountRefs', []):
        value = data
        for key in ref:
            value = value[key]
        require(set(account_resources(value)) <= set(binding['resources']), 'account identity changed; create new run')
        require(value.get('loginPolicyVerified', False) or 'execution:serial' in binding['resources'],
                'login policy changed; create new run')
        accounts.append(value)
    return accounts


def private_file(path):
    require(not path.is_symlink() and path.is_file() and stat.S_IMODE(path.stat().st_mode) == 0o600,
            'private config must be a regular 0600 file')
    data = json.loads(path.read_text())
    require(isinstance(data, dict), 'private config must be an object')
    account_entries(data)
    return data


def conflicts(resources, occupied):
    resources, occupied = set(resources), set(occupied)
    return bool(resources & occupied or
                'execution:serial' in resources and any(r.startswith('execution:active:') for r in occupied) or
                'execution:serial' in occupied and any(r.startswith('execution:active:') for r in resources))


def freeze(plan, config, inventory, private):
    result = copy.deepcopy(plan)
    settings = config.get('execution', {})
    enabled = settings.get('parallelEnabled', False)
    require(type(enabled) is bool, 'parallelEnabled must be boolean')
    source = next((k for k in ('maxConcurrency', 'executionConcurrency', 'workers') if k in settings), None)
    cap = settings[source] if source else len(plan['tasks'])
    require(type(cap) is int and cap > 0, 'invalid execution limit')
    slots = inventory.get('hostSlots', [])
    require(isinstance(slots, list) and all(isinstance(s, str) and s for s in slots), 'invalid host slots')
    require(len(set(slots)) == len(slots), 'duplicate host slots')
    catalogs = {platform: account_entries(data) for platform, data in private.items()}
    for task in result['tasks']:
        if task['kind'] != 'execution':
            continue
        require(task['platform'] in ('web', 'mobile', 'shared'), 'invalid execution platform')
        require(type(task.get('requiresLogin', True)) is bool, 'invalid login requirement')
        require(type(task.get('isolationVerified', False)) is bool, 'invalid isolation verification')
        route_ids = task.get('routeIds', [])
        require(isinstance(route_ids, list) and all(isinstance(r, str) and r for r in route_ids), 'invalid route IDs')
        requirements = task.get('accountRequirements', [])
        require(isinstance(requirements, list), 'invalid account requirements')
        require(task.get('requiresLogin', True) or not requirements, 'public task cannot require accounts')
        choices = []
        for requirement in requirements:
            require(isinstance(requirement, dict) and set(requirement) == {'role', 'tenant'} and
                    all(isinstance(v, str) for v in requirement.values()), 'invalid account requirement')
            choices.append([entry for entry in catalogs.get(task['platform'], [])
                            if entry[:2] == (requirement['role'], requirement['tenant'])])
        combinations = list(itertools.product(*choices)) if choices else [()]
        if task.get('requiresLogin', True) and not requirements:
            combinations = []
        if task['platform'] == 'shared':
            routes = [{'id': 'api', 'resources': []}]
        else:
            routes = inventory.get(task['platform'], [])
        require(isinstance(routes, list), 'invalid session inventory')
        bindings = []
        for route in routes:
            require(isinstance(route, dict) and isinstance(route.get('id'), str) and route['id'], 'invalid session route')
            resources = route.get('resources', [])
            require(isinstance(resources, list) and all(isinstance(r, str) and r for r in resources), 'invalid route resources')
            if route.get('ready') is not True and task['platform'] != 'shared':
                continue
            if route_ids and route['id'] not in route_ids:
                continue
            if task['platform'] == 'mobile':
                require(route.get('authorized') is True and any(r.startswith('device:') for r in resources)
                        and any(r.startswith('port:') for r in resources), 'mobile route requires authorized device and ports')
            if task['platform'] == 'web':
                require(any(r.startswith('browser:') for r in resources), 'web route requires independent browser slot')
            for accounts in combinations:
                locks, refs = [], []
                safe = task.get('isolationVerified', False)
                for role, tenant, ref, account in accounts:
                    # Username lock also catches an old config without the new canonical ID.
                    locks.extend(account_resources(account))
                    safe = safe and account.get('loginPolicyVerified', False)
                    refs.append(ref)
                for slot in slots:
                    leased = list(task.get('resources', [])) + resources + locks + ['host-slot:' + slot]
                    leased.append('execution:active:' + digest([plan['runId'], task['taskId']]))
                    if not enabled or not safe:
                        leased.append('execution:serial')
                    bindings.append({'resources': sorted(set(leased)), 'accountRefs': refs, 'routeId': route['id']})
        task['bindings'] = bindings
        task['blockedReason'] = None if bindings else 'missing verified slot, session, or role/tenant account'
        task['dataNamespace'] = digest([plan['runId'], task['taskId']])[:24]
    # Greedy packing per dependency frontier is a safe, reproducible capacity, not an optimum claim.
    remaining = {t['taskId']: t for t in result['tasks']}
    done, width = set(), 0
    while remaining:
        ready = [t for t in remaining.values() if set(t.get('dependsOn', [])) <= done]
        require(ready, 'invalid task dependencies')
        occupied, count = set(), 0
        for task in sorted(ready, key=lambda t: t['taskId']):
            for binding in task.get('bindings', []):
                if not conflicts(binding['resources'], occupied):
                    occupied.update(binding['resources'])
                    count += 1
                    break
        width = max(width, count)
        for task in ready:
            done.add(task['taskId'])
            del remaining[task['taskId']]
    result['executionConcurrency'] = max(1, min(cap, len(slots), width)) if enabled else 1
    result['executionPlanning'] = {'version': 1, 'parallelEnabled': enabled,
                                   'limitSource': source or 'resources', 'inventoryHash': digest(inventory)}
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('plan', 'config', 'inventory', 'output'):
        parser.add_argument(name, type=Path)
    parser.add_argument('--web-private', type=Path)
    parser.add_argument('--mobile-private', type=Path)
    args = parser.parse_args()
    try:
        config = json.loads(args.config.read_text())
        private = {p: private_file(path) for p, path in
                   [('web', args.web_private), ('mobile', args.mobile_private)] if path}
        result = freeze(json.loads(args.plan.read_text()), config.get('config', config),
                        json.loads(args.inventory.read_text()), private)
        from scheduler import validate
        validate(result)
        # Exclusive creation preserves historical plans, including on an accidental rerun.
        import os
        with os.fdopen(os.open(args.output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), 'w') as output:
            json.dump(result, output, ensure_ascii=False, allow_nan=False)
        print(json.dumps({'status': 'FROZEN', 'executionConcurrency': result['executionConcurrency']}))
        return 0
    except (ValueError, KeyError, TypeError, OSError):
        # Parser and filesystem errors can contain credential fragments or private paths.
        print(json.dumps({'status': 'BLOCKED', 'reason': 'invalid execution inputs; inspect private files locally'}))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
