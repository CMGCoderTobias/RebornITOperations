"""Optional, scoped automation reports. Human verification is never automated."""
import copy
import hashlib
import secrets
import uuid
import re
from core import FIELDS, now_iso, validate_asset, string

COMMON_FIELDS = ['status', 'last_heartbeat', 'last_activity', 'cost', 'cost_checked']
DETAIL_FIELDS = {'last_reboot', 'last_update', 'last_commit', 'last_push', 'last_success', 'last_failure', 'last_run', 'version', 'certificate_expires', 'domain_expires'}


def allowed_fields(asset):
    return COMMON_FIELDS + ['details.' + key for key in FIELDS[asset['type']] if key in DETAIL_FIELDS or (asset['type'] == 'Machine' and key in {'hostname', 'operating_system', 'hardware', 'storage'})]


def token_hash(token):
    return hashlib.sha256(token.encode()).hexdigest()


def negative_reasons(report):
    changes = report['changes']
    reasons = []
    if changes.get('status') in ['OFFLINE', 'FAILED']:
        reasons.append('status:' + changes['status'])
    if changes.get('details.last_failure') and changes['details.last_failure'] != report.get('before', {}).get('details.last_failure'):
        reasons.append('failure')
    if 'cost' in changes and changes['cost'] > (report.get('before', {}).get('cost') or 0):
        reasons.append('cost increase')
    for drive, percent in re.findall(r'([A-Z]:)[^\n]*?GiB free \(([\d.]+)%\)', changes.get('details.storage', '')):
        if float(percent) < 10:
            reasons.append('low disk:' + drive)
    return tuple(reasons)


def compact_reports(reports):
    """One latest report per connection, plus at most 50 distinct adverse observations.

    Repeated adverse conditions replace the prior observation for that condition.
    Routine five-minute reports therefore cannot grow the stored history.
    """
    latest, negative = {}, {}
    for report in reports:
        latest[report['client_id']] = report
        reasons = negative_reasons(report)
        if reasons:
            negative[(report['client_id'], reasons)] = report
    bad = sorted(negative.values(), key=lambda r: r['at'])[-50:]
    keep = {r['id'] for r in list(latest.values()) + bad}
    return [r for r in reports if r['id'] in keep][-200:]


def new_client(body, asset):
    fields = body.get('fields', [])
    if not isinstance(fields, list) or not fields or any(not isinstance(key, str) or key not in allowed_fields(asset) for key in fields):
        raise ValueError('Choose at least one supported field for this asset.')
    mode = body.get('mode', 'report')
    if mode not in ['report', 'apply']:
        raise ValueError('Mode must be report or apply.')
    client = {'id': str(uuid.uuid4()), 'name': string(body.get('name', ''), 'Automation name', True, 150),
              'asset_id': asset['id'], 'fields': sorted(set(fields)), 'mode': mode,
              'enabled': True, 'created_at': now_iso(), 'last_seen': ''}
    secret = 'reborn_' + secrets.token_urlsafe(32)
    return client, secret


def build_report(asset, client, payload):
    if set(payload) - {'changes', 'event_id'}:
        raise ValueError('Only changes and an optional event_id are accepted.')
    changes = payload.get('changes')
    if not isinstance(changes, dict) or not changes:
        raise ValueError('Provide a nonempty changes object.')
    if any(key not in client['fields'] or key not in allowed_fields(asset) for key in changes):
        raise ValueError('This key is not allowed to update one or more requested fields.')
    event_id = string(payload.get('event_id', ''), 'event_id', limit=100)
    # Duplicate detection is scoped to this key, not other automations.
    if event_id and any(r.get('client_id') == client['id'] and r.get('event_id') == event_id for r in asset.get('automation_history', [])):
        return None
    updated = copy.deepcopy(asset)
    before = {}
    for key, value in changes.items():
        if key.startswith('details.'):
            field = key.split('.', 1)[1]
            before[key] = asset['details'].get(field, '')
            updated['details'][field] = value
        else:
            before[key] = asset[key]
            updated[key] = value
    updated = validate_asset(updated)
    # Store the canonical values produced by the normal inventory validator.
    normalized = {key: updated['details'][key.split('.', 1)[1]] if key.startswith('details.') else updated[key] for key in changes}
    report = {'id': str(uuid.uuid4()), 'client_id': client['id'], 'event_id': event_id,
              'source': client['name'], 'at': now_iso(), 'mode': client['mode'], 'changes': normalized, 'before': before}
    result = updated if client['mode'] == 'apply' else copy.deepcopy(asset)
    result['automation_history'] = compact_reports(asset.get('automation_history', []) + [report])
    result['revision'] = asset.get('revision', 0) + 1
    return validate_asset(result)
