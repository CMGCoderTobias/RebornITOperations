"""Local inventory model, validation, attention rules, and portable exports."""
import csv
import io
import json
import math
import re
from datetime import datetime, timedelta, timezone

TYPES = ['Machine', 'Network', 'Cloud', 'Storage', 'Repository', 'Service', 'Domain', 'Account', 'Software', 'Automation', 'Backup', 'Project']
STATUSES = ['UNKNOWN', 'ACTIVE', 'ONLINE', 'OFFLINE', 'MAINTENANCE', 'DORMANT', 'PAUSED', 'ARCHIVED', 'ABANDONED', 'RETIRED', 'FAILED']
RELATIONS = ['CONNECTS_TO', 'DEPENDS_ON', 'HOSTS', 'CONTAINS', 'BACKS_UP', 'USES', 'DEPLOYED_FROM', 'MONITORED_BY', 'BELONGS_TO', 'RUNS_ON', 'PROVIDES', 'REQUIRES']
MAINTENANCE_STATUSES = ['NOT_STARTED', 'DUE', 'OVERDUE', 'COMPLETED', 'SKIPPED', 'NOT_APPLICABLE']
DEFAULT_SETTINGS = {'stale_days': 90, 'upcoming_days': 30, 'restore_days': 90}
INACTIVE = {'ARCHIVED', 'RETIRED', 'ABANDONED', 'PAUSED', 'DORMANT'}

# Every category has structured details; secrets belong in a credential manager.
FIELDS = {
    'Machine': ['hostname', 'owner', 'operating_system', 'hardware', 'ip_addresses', 'mac_address', 'remote_access', 'storage', 'last_reboot', 'last_update'],
    'Network': ['address', 'configuration_location', 'network_role'],
    'Cloud': ['account', 'region', 'resource_id', 'resource_type', 'hostname', 'operating_system', 'hardware', 'ip_addresses', 'storage', 'last_reboot', 'aws_state', 'aws_state_checked', 'watchdog_id', 'watchdog_status', 'watchdog_checked', 'watchdog_apps', 'watchdog_warning'],
    'Storage': ['capacity', 'data_description', 'mount_path', 'encryption'],
    'Repository': ['url', 'local_paths', 'remote_urls', 'primary_remote', 'source_of_truth', 'last_commit', 'last_push', 'deployment_target'],
    'Service': ['process_name', 'port', 'trigger', 'last_success', 'last_failure', 'logs', 'deployment_method'],
    'Domain': ['domain', 'registrar', 'dns_provider', 'hosting_provider', 'destination', 'certificate_expires', 'domain_expires', 'renewal_method'],
    'Account': ['account', 'authentication_method', 'credential_location', 'expiration_requirements'],
    'Software': ['version', 'required_optional', 'installation_source', 'update_method'],
    'Automation': ['schedule', 'last_run', 'last_success', 'last_failure', 'logs', 'notification_method'],
    'Backup': ['data_description', 'source', 'destination', 'method', 'schedule', 'retention', 'encryption', 'last_success', 'last_failure', 'last_restore_test', 'restore_procedure'],
    'Project': ['owner', 'reference']
}


def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def parse_date(value):
    if not value:
        return None
    try:
        # Python 3.10 accepts only certain fractional widths; .NET emits seven.
        normalized = re.sub(r'(\d{2}:\d{2}:\d{2})\.(\d+)', lambda m: m[1]+'.'+m[2][:6].ljust(6, '0'), value)
        dt = datetime.fromisoformat(normalized.replace('Z', '+00:00'))
        return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt
    except (ValueError, AttributeError, TypeError):
        raise ValueError('Dates must use ISO format (YYYY-MM-DD or a timestamp).')


def number(value, label, minimum=0, maximum=1e9):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not minimum <= value <= maximum:
        raise ValueError(f'{label} must be between {minimum} and {maximum}.')
    return value


def string(value, label, required=False, limit=20000):
    if not isinstance(value, str) or len(value) > limit or (required and not value.strip()):
        raise ValueError(f'{label} must be {"nonempty " if required else ""}text (up to {limit} characters).')
    return value.strip()


def validate_asset(value):
    if not isinstance(value, dict):
        raise ValueError('Asset must be an object.')
    a = {'id': string(value.get('id', ''), 'Asset ID', True, 100), 'name': string(value.get('name', ''), 'Name', True, 200)}
    for key in ['type', 'status']:
        if value.get(key) not in (TYPES if key == 'type' else STATUSES):
            raise ValueError(f'Invalid asset {key}.')
        a[key] = value[key]
    for key in ['purpose', 'location', 'provider', 'monitoring', 'recovery', 'notes', 'cost_notes']:
        a[key] = string(value.get(key, ''), key)
    for key in ['last_verified', 'last_heartbeat', 'last_activity', 'cost_checked']:
        a[key] = string(value.get(key, ''), key, limit=60)
        parse_date(a[key])
    for key in ['cost', 'expected_cost', 'heartbeat_minutes', 'presence_minutes']:
        a[key] = number(value.get(key, 0), key)
    a['billing_period'] = value.get('billing_period', 'monthly')
    if a['billing_period'] not in ['monthly', 'yearly']:
        raise ValueError('Billing period must be monthly or yearly.')
    a['important_data'] = value.get('important_data', False)
    if not isinstance(a['important_data'], bool):
        raise ValueError('Important data must be true or false.')
    details = value.get('details', {})
    if not isinstance(details, dict) or set(details) - set(FIELDS[a['type']]):
        raise ValueError('Invalid details for this asset type.')
    a['details'] = {k: string(v, k) for k, v in details.items()}
    for k, v in a['details'].items():
        if k.startswith('last_') or k.endswith('_expires'):
            parse_date(v)
    history = value.get('verification_history', [])
    if not isinstance(history, list) or len(history) > 10000:
        raise ValueError('Invalid verification history.')
    a['verification_history'] = []
    for entry in history:
        if not isinstance(entry, dict):
            raise ValueError('Invalid verification entry.')
        at = string(entry.get('at', ''), 'Verification date', True, 60)
        parse_date(at)
        a['verification_history'].append({'at': at, 'note': string(entry.get('note', ''), 'Verification note')})
    revision = number(value.get('revision', 0), 'Revision', maximum=1e15)
    if int(revision) != revision:
        raise ValueError('Revision must be an integer.')
    a['revision'] = int(revision)
    reports = value.get('automation_history', [])
    if not isinstance(reports, list) or len(reports) > 200:
        raise ValueError('Automation history must contain at most 200 reports.')
    a['automation_history'] = []
    for report in reports:
        if not isinstance(report, dict) or report.get('mode') not in ['report', 'apply']:
            raise ValueError('Invalid automation history entry.')
        clean = {key: string(report.get(key, ''), key, key in ['id', 'client_id', 'source', 'at'], 200) for key in ['id', 'client_id', 'event_id', 'source', 'at']}
        parse_date(clean['at'])
        clean['mode'] = report['mode']
        clean['reviewed_at'] = string(report.get('reviewed_at', ''), 'Review time', limit=60)
        parse_date(clean['reviewed_at'])
        clean['decision'] = report.get('decision', '')
        if clean['decision'] not in ['', 'accepted', 'dismissed']:
            raise ValueError('Invalid automation review decision.')
        for key in ['changes', 'before']:
            values = report.get(key)
            if not isinstance(values, dict) or len(values) > 30:
                raise ValueError('Invalid automation change record.')
            clean[key] = {}
            for field, field_value in values.items():
                string(field, 'Field name', True, 100)
                if isinstance(field_value, str):
                    string(field_value, 'Reported value', limit=20000)
                elif not isinstance(field_value, bool):
                    number(field_value, 'Reported value')
                else:
                    raise ValueError('Invalid reported value.')
                clean[key][field] = field_value
        a['automation_history'].append(clean)
    return a


def validate_maintenance(value, ids):
    if not isinstance(value, dict):
        raise ValueError('Maintenance must be an object.')
    m = {key: string(value.get(key, ''), key, key in ['id', 'asset_id', 'title'], 200 if key in ['id', 'asset_id', 'title'] else 20000) for key in ['id', 'asset_id', 'title', 'notes', 'next_due', 'last_completed']}
    if m['asset_id'] not in ids:
        raise ValueError('Maintenance must reference an existing asset.')
    for k in ['next_due', 'last_completed']:
        parse_date(m[k])
    m['frequency_days'] = number(value.get('frequency_days', 0), 'Frequency', maximum=36500)
    if int(m['frequency_days']) != m['frequency_days']:
        raise ValueError('Frequency must be a whole number of days.')
    m['priority'] = value.get('priority', 'NORMAL')
    m['status'] = value.get('status', 'NOT_STARTED')
    if m['priority'] not in ['LOW', 'NORMAL', 'HIGH', 'CRITICAL'] or m['status'] not in MAINTENANCE_STATUSES:
        raise ValueError('Invalid maintenance status or priority.')
    history = value.get('completion_history', [])
    if not isinstance(history, list) or len(history) > 100:
        raise ValueError('Invalid maintenance completion history.')
    m['completion_history'] = []
    for entry in history:
        at = string(entry.get('at', ''), 'Submitted at', True, 60)
        parse_date(at)
        steps = entry.get('steps', [])
        if not isinstance(steps, list) or len(steps) > 500:
            raise ValueError('Invalid checklist steps.')
        clean = []
        for step in steps:
            if not isinstance(step.get('checked'), bool):
                raise ValueError('Checklist states must be true or false.')
            clean.append({'label': string(step.get('label', ''), 'Step', True), 'checked': step['checked']})
        m['completion_history'].append({'at': at, 'notes': string(entry.get('notes', ''), 'Review notes'), 'steps': clean})
    return m


def checklist_steps(notes):
    import re
    lines = [line for line in notes.splitlines() if line.strip()]
    numbered = [re.sub(r'^\s*(\d+[.)]|[-*])\s+', '', line) for line in lines if re.match(r'^\s*(\d+[.)]|[-*])\s+', line)]
    return numbered or ([line for line in re.split(r'(?<=[.!?])\s+', notes) if line.strip()] if notes else [])


def validate_settings(value):
    if not isinstance(value, dict) or set(value) - set(DEFAULT_SETTINGS):
        raise ValueError('Invalid settings.')
    return {k: int(number(value.get(k, v), k, 1, 36500)) for k, v in DEFAULT_SETTINGS.items()}


def validate_document(doc):
    if not isinstance(doc, dict) or doc.get('schema_version') != 1:
        raise ValueError('Expected a Reborn inventory JSON export with schema_version 1.')
    for key in ['assets', 'relationships', 'maintenance']:
        if not isinstance(doc.get(key), list):
            raise ValueError(f'{key} must be a list.')
    assets = [validate_asset(a) for a in doc['assets']]
    ids = {a['id'] for a in assets}
    if len(ids) != len(assets):
        raise ValueError('Duplicate asset IDs.')
    relations = []
    relation_ids, triples = set(), set()
    for r in doc['relationships']:
        if not isinstance(r, dict):
            raise ValueError('Invalid relationship.')
        r = {k: string(r.get(k, ''), k, True, 100) for k in ['id', 'source_id', 'target_id', 'kind']}
        triple = (r['source_id'], r['target_id'], r['kind'])
        if r['source_id'] not in ids or r['target_id'] not in ids or r['source_id'] == r['target_id'] or r['kind'] not in RELATIONS:
            raise ValueError('Invalid relationship endpoints or kind.')
        if r['id'] in relation_ids or triple in triples:
            raise ValueError('Duplicate relationship.')
        relation_ids.add(r['id'])
        triples.add(triple)
        relations.append(r)
    maintenance = [validate_maintenance(m, ids) for m in doc['maintenance']]
    if len({m['id'] for m in maintenance}) != len(maintenance):
        raise ValueError('Duplicate maintenance IDs.')
    return {'schema_version': 1, 'assets': assets, 'relationships': relations, 'maintenance': maintenance, 'settings': validate_settings(doc.get('settings', {}))}


def maintenance_state(m, now=None):
    now = now or datetime.now().astimezone()
    if m['status'] in ['COMPLETED', 'SKIPPED', 'NOT_APPLICABLE']:
        return m['status']
    due = parse_date(m['next_due'])
    if m['status'] == 'NOT_STARTED' and m['last_completed'] and m['frequency_days'] and due and due.date() > now.date():
        return 'COMPLETED'
    if due:
        if due.date() < now.date():
            return 'OVERDUE'
        if due.date() == now.date():
            return 'DUE'
    return m['status']


def complete_maintenance(m, now=None):
    now = now or datetime.now().astimezone()
    result = dict(m, last_completed=now.isoformat(timespec='seconds'))
    if m['frequency_days']:
        result.update(status='NOT_STARTED', next_due=(now + timedelta(days=m['frequency_days'])).date().isoformat())
    else:
        result['status'] = 'COMPLETED'
    return result


def attention(doc, now=None):
    now = now or datetime.now().astimezone()
    settings = doc['settings']
    result = []
    def flag(a, severity, category, message):
        result.append({'asset_id': a['id'], 'severity': severity, 'category': category, 'message': message})
    for a in doc['assets']:
        if a['status'] in {'ARCHIVED', 'RETIRED', 'ABANDONED'}:
            continue
        d = a['details']
        latest_reports = {r['client_id']: r for r in a.get('automation_history', [])}
        if any(r['mode'] == 'report' and not r.get('reviewed_at') for r in latest_reports.values()):
            flag(a, 'review', 'Automation report', 'New automation observations are waiting for your review')
        live = a['status'] not in INACTIVE
        if a['status'] in {'OFFLINE', 'FAILED'} and not (a['status'] == 'OFFLINE' and a.get('presence_minutes', 0)):
            flag(a, 'critical', 'Unavailable', f"{a['name']} is {a['status'].lower()}")
        if a['status'] == 'UNKNOWN':
            flag(a, 'review', 'Unknown', 'Actual state needs a review')
        if not a['purpose']:
            flag(a, 'review', 'Unexplained', 'Why does this exist? Add a purpose.')
        verified = parse_date(a['last_verified'])
        if verified is None:
            flag(a, 'review', 'Never verified', 'No manual verification recorded')
        elif (now - verified).days >= settings['stale_days']:
            flag(a, 'review', 'Forgotten', f'Not manually verified for {(now - verified).days} days')
        if live and a['heartbeat_minutes'] and not a.get('presence_minutes', 0):
            hb = parse_date(a['last_heartbeat'])
            if hb is None or (now - hb).total_seconds() > a['heartbeat_minutes'] * 60:
                flag(a, 'critical', 'Heartbeat missing', 'Expected heartbeat is missing or overdue')
        if live and a['type'] in ['Service', 'Automation'] and not a['monitoring']:
            flag(a, 'review', 'Unmonitored', 'No monitoring method recorded')
        if a['type'] == 'Backup':
            restored = parse_date(d.get('last_restore_test', ''))
            if not restored:
                flag(a, 'review', 'Unverified backup', 'Backup has never been restore-tested')
            elif (now - restored).days >= settings['restore_days']:
                flag(a, 'due', 'Restore test due', f'Restore test is {(now - restored).days} days old')
        failure, success = parse_date(d.get('last_failure', '')), parse_date(d.get('last_success', ''))
        if live and failure and (not success or failure > success):
            flag(a, 'critical', 'Recently failed', 'Last recorded failure has no later success')
        if a['important_data'] and not any(r['kind'] == 'BACKS_UP' and r['target_id'] == a['id'] and any(b['id'] == r['source_id'] and b['type'] == 'Backup' and b['status'] not in INACTIVE for b in doc['assets']) for r in doc['relationships']):
            flag(a, 'review', 'Unbacked-up', 'Important data has no linked active backup')
        for key, label in [('domain_expires', 'Domain'), ('certificate_expires', 'Certificate')]:
            expiry = parse_date(d.get(key, ''))
            if expiry:
                days = (expiry.date() - now.date()).days
                if days <= settings['upcoming_days']:
                    flag(a, 'critical' if days < 0 else 'due', 'Expiration', f'{label} expired {-days} days ago' if days < 0 else f'{label} expires in {days} days')
        if a['expected_cost'] and a['cost'] > a['expected_cost']:
            flag(a, 'due', 'Cost increase', f"Recorded cost exceeds expected cost by ${a['cost'] - a['expected_cost']:,.2f} / {a['billing_period']}")
        if a['type'] == 'Repository' and d.get('source_of_truth', '').strip().lower() in ['', 'unknown']:
            flag(a, 'review', 'Source of truth', 'Authoritative repository location is unknown')
        activity = parse_date(a['last_activity'])
        if live and activity and (now - activity).days >= settings['stale_days']:
            flag(a, 'review', 'Unused', f'Last recorded activity was {(now - activity).days} days ago')
        # Traverse relationships in either direction to find a live project/service.
        seen, pending = {a['id']}, [a['id']]
        while pending:
            current = pending.pop()
            for r in doc['relationships']:
                neighbor = r['target_id'] if r['source_id'] == current else r['source_id'] if r['target_id'] == current else None
                if neighbor and neighbor not in seen:
                    seen.add(neighbor)
                    pending.append(neighbor)
        standalone_verified = a['type'] == 'Machine' and bool(a['purpose'].strip()) and verified is not None and (now - verified).days < settings['stale_days']
        if not standalone_verified and a['type'] not in ['Project', 'Service'] and not any(b['id'] != a['id'] and b['id'] in seen and b['type'] in ['Project', 'Service'] and b['status'] in ['ACTIVE', 'ONLINE', 'MAINTENANCE'] for b in doc['assets']):
            flag(a, 'review', 'Orphaned', 'No connection to a known active project or service; review whether still needed')
    for m in doc['maintenance']:
        state = maintenance_state(m, now)
        due = parse_date(m['next_due'])
        if state in ['DUE', 'OVERDUE'] or (state == 'NOT_STARTED' and due and 0 <= (due.date() - now.date()).days <= settings['upcoming_days']):
            result.append({'asset_id': m['asset_id'], 'maintenance_id': m['id'], 'severity': 'due', 'category': 'Overdue' if state == 'OVERDUE' else 'Maintenance', 'message': m['title'] + (' · ' + m['next_due'] if m['next_due'] else '')})
    return sorted(result, key=lambda r: {'critical': 0, 'due': 1, 'review': 2}[r['severity']])


def monthly_cost(asset):
    return asset['cost'] / (12 if asset['billing_period'] == 'yearly' else 1)


def csv_export(doc):
    stream = io.StringIO(newline='')
    writer = csv.writer(stream)
    writer.writerow(['record_type', 'id', 'name', 'asset_type', 'status', 'purpose', 'location', 'provider', 'last_verified', 'monthly_cost_usd', 'data_json'])
    def safe(value):
        value = str(value)
        return "'" + value if value.lstrip().startswith(('=', '+', '-', '@')) else value
    for key, kind in [('assets', 'asset'), ('relationships', 'relationship'), ('maintenance', 'maintenance')]:
        for r in doc[key]:
            writer.writerow([safe(x) for x in [kind, r['id'], r.get('name', r.get('title', '')), r.get('type', ''), r.get('status', ''), r.get('purpose', ''), r.get('location', ''), r.get('provider', ''), r.get('last_verified', ''), round(monthly_cost(r), 2) if kind == 'asset' else '', json.dumps(r, ensure_ascii=False)]])
    writer.writerow(['settings', '', '', '', '', '', '', '', '', '', json.dumps(doc['settings'])])
    return stream.getvalue()


def markdown_export(doc):
    names = {a['id']: a['name'] for a in doc['assets']}
    lines = ['# Reborn IT Operations inventory', '', f'Exported: {now_iso()}', '', 'All costs in USD.', '']
    for a in doc['assets']:
        lines += [f"## {a['name']}", '', f"{a['type']} · {a['status']}", '', a['purpose'] or 'Purpose unknown.', '', '### Complete record', '', '```json', json.dumps(a, indent=2, ensure_ascii=False), '```', '', '### Relationships', '']
        for r in doc['relationships']:
            if a['id'] in [r['source_id'], r['target_id']]:
                lines.append(f"- {names[r['source_id']]} → {r['kind']} → {names[r['target_id']]}")
        lines += ['', '### Maintenance', '']
        for m in doc['maintenance']:
            if m['asset_id'] == a['id']:
                lines += ['```json', json.dumps(m, indent=2, ensure_ascii=False), '```']
        lines.append('')
    lines += ['## Review settings', '', '```json', json.dumps(doc['settings'], indent=2), '```']
    return '\n'.join(lines)
