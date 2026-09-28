"""Reborn IT Operations: single-user, loopback-only server. No external packages."""
import argparse
import copy
import json
import mimetypes
import secrets
import sqlite3
import threading
import uuid
import webbrowser
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse
from core import TYPES, STATUSES, RELATIONS, FIELDS, attention, validate_asset, validate_document, validate_settings, now_iso, complete_maintenance, checklist_steps, csv_export, markdown_export
from storage import Store
from automation import allowed_fields, new_client, token_hash, build_report
from aws_inventory import AWSConnection
from watchdog_inventory import WatchdogInventory

ROOT = Path(__file__).resolve().parent
VERSION = json.loads((ROOT / 'package.json').read_text(encoding='utf-8'))['version']


class ConflictError(ValueError):
    pass


class Server(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address, db_path):
        self.store = Store(db_path)
        self.token = secrets.token_urlsafe(32)
        self.lock = threading.RLock()
        super().__init__(address, Handler)
        self.aws = AWSConnection(self)
        self.aws.start()
        self.watchdog=WatchdogInventory(self)
        self.watchdog.start()

    def server_close(self):
        if hasattr(self, 'watchdog'):self.watchdog.stop.set()
        if hasattr(self, 'aws'):
            self.aws.stop.set()
        super().server_close()


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass

    def send(self, data, code=200, mime='application/json; charset=utf-8', filename=None):
        body = json.dumps(data, ensure_ascii=False).encode() if mime.startswith('application/json') else data.encode() if isinstance(data, str) else data
        self.send_response(code)
        self.send_header('Content-Type', mime)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Content-Security-Policy', "default-src 'self'; style-src 'self'; script-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
        if filename:
            self.send_header('Content-Disposition', f'attachment; filename="{filename}"')
        self.end_headers()
        self.wfile.write(body)

    def valid_host(self):
        return self.headers.get('Host') in [f'127.0.0.1:{self.server.server_port}', f'localhost:{self.server.server_port}']

    def do_GET(self):
        if not self.valid_host():
            return self.send({'error': 'Local access only'}, 403)
        path = urlparse(self.path).path
        if path == '/api/health':
            return self.send({'app': 'RebornITOperations', 'version': VERSION})
        if path == '/api/automation/asset':
            client = self.automation_client()
            if not client:
                return self.send({'error': 'Invalid or paused automation key.'}, 401)
            with self.server.lock:
                a = next((a for a in self.server.store.read()['assets'] if a['id'] == client['asset_id']), None)
            if not a:
                return self.send({'error': 'Scoped asset no longer exists.'}, 404)
            fields = [key for key in client['fields'] if key in allowed_fields(a)]
            return self.send({'id': a['id'], 'name': a['name'], 'mode': client['mode'], 'values': {key: a['details'].get(key.split('.', 1)[1], '') if key.startswith('details.') else a[key] for key in fields}})
        if path == '/api/state':
            with self.server.lock:
                doc = self.server.store.read()
                clients = self.server.store.clients()
            return self.send({**doc, 'attention': attention(doc), 'token': self.server.token, 'version': VERSION, 'automation_clients': clients, 'aws': self.server.aws.status(), 'automation_fields': {a['id']: allowed_fields(a) for a in doc['assets']}, 'catalog': {'types': TYPES, 'statuses': STATUSES, 'relations': RELATIONS, 'fields': FIELDS}})
        if path == '/api/aws/policy':
            return self.send(json.loads((ROOT/'docs/aws-readonly-policy.json').read_text()), filename='reborn-aws-readonly-policy.json')
        if path.startswith('/api/export/'):
            with self.server.lock:
                doc = self.server.store.read()
            fmt = path.rsplit('/', 1)[-1]
            if fmt == 'json':
                return self.send(doc, filename='reborn-inventory.json')
            if fmt == 'csv':
                return self.send(csv_export(doc), mime='text/csv; charset=utf-8', filename='reborn-inventory.csv')
            if fmt == 'markdown':
                return self.send(markdown_export(doc), mime='text/markdown; charset=utf-8', filename='reborn-inventory.md')
        files = {'/': 'index.html', '/app.js': 'app.js', '/styles.css': 'styles.css', '/automation-ui.js': 'automation-ui.js', '/aws-ui.js': 'aws-ui.js'}
        if path in files:
            file = ROOT / 'web' / files[path]
            return self.send(file.read_bytes(), mime=(mimetypes.guess_type(file.name)[0] or 'text/plain') + '; charset=utf-8')
        self.send({'error': 'Not found'}, 404)

    def do_POST(self):
        path = urlparse(self.path).path
        if path == '/api/automation/report':
            return self.receive_report()
        origin = self.headers.get('Origin')
        allowed = [f'http://127.0.0.1:{self.server.server_port}', f'http://localhost:{self.server.server_port}']
        if not self.valid_host() or (origin and origin not in allowed) or not secrets.compare_digest(self.headers.get('X-Reborn-Token', ''), self.server.token):
            return self.send({'error': 'Refresh the local application and try again.'}, 403)
        try:
            length = int(self.headers.get('Content-Length', '0'))
            if length < 1 or length > 10_000_000:
                raise ValueError('Request must contain JSON smaller than 10 MB.')
            body = json.loads(self.rfile.read(length))
            if not isinstance(body, dict):
                raise ValueError('Expected a JSON object.')
            path = urlparse(self.path).path
            with self.server.lock:
                result = self.mutate(path, body)
            self.send(result)
        except ConflictError as exc:
            self.send({'error': str(exc)}, 409)
        except (ValueError, KeyError, TypeError, sqlite3.IntegrityError) as exc:
            self.send({'error': str(exc)}, 400)
        except Exception:
            self.send({'error': 'Could not save changes. Check disk access and available space.'}, 500)

    def automation_client(self):
        # Browser callers cannot use machine credentials through this API.
        if not self.valid_host() or self.headers.get('Origin') or self.headers.get('Sec-Fetch-Site'):
            return None
        auth = self.headers.get('Authorization', '')
        if not auth.startswith('Bearer ') or len(auth) > 250:
            return None
        return self.server.store.authenticate_client(token_hash(auth[7:]))

    def receive_report(self):
        try:
            with self.server.lock:
                client = self.automation_client()
                if not client:
                    return self.send({'error': 'Invalid or paused automation key.'}, 401)
                length = int(self.headers.get('Content-Length', '0'))
                if not 1 <= length <= 65536:
                    raise ValueError('Reports must contain JSON smaller than 64 KB.')
                body = json.loads(self.rfile.read(length))
                if not isinstance(body, dict):
                    raise ValueError('Expected a JSON object.')
                a = next((a for a in self.server.store.read()['assets'] if a['id'] == client['asset_id']), None)
                if not a:
                    return self.send({'error': 'Scoped asset no longer exists.'}, 404)
                updated = build_report(a, client, body)
                if updated is None:
                    return self.send({'ok': True, 'duplicate': True})
                client['last_seen'] = now_iso()
                self.server.store.save_report(updated, client)
            self.send({'ok': True, 'mode': client['mode'], 'asset_id': a['id'], 'revision': updated['revision']})
        except (ValueError, KeyError, TypeError) as exc:
            self.send({'error': str(exc)}, 400)
        except Exception:
            self.send({'error': 'Could not record report. The inventory was not verified.'}, 500)

    def mutate(self, path, body):
        store = self.server.store
        doc = store.read()
        if path == '/api/aws/reconcile':
            self.server.aws.apply_cloud_status(self.server.aws.report)
            self.server.aws.apply_billing_costs(self.server.aws.report)
            return {'ok':True}
        if path == '/api/aws/billing-link':
            return self.server.aws.link_billing(body)
        if path == '/api/aws/configure':
            return self.server.aws.configure(body)
        if path == '/api/aws/costs':
            if body.get('confirm_fee') is not True:
                raise ValueError('Confirm the $0.01 AWS fee before refreshing costs.')
            return self.server.aws.sync(billing=True)
        if path == '/api/aws/sync':
            return self.server.aws.sync()
        if path == '/api/automations/create':
            a = next((a for a in doc['assets'] if a['id'] == body.get('asset_id')), None)
            if a is None:
                raise ValueError('Choose an existing asset.')
            client, secret = new_client(body, a)
            store.create_client(client, token_hash(secret))
            return {'ok': True, 'client': client, 'secret': secret}
        if path == '/api/automations/update':
            client = next((c for c in store.clients() if c['id'] == body.get('id')), None)
            if client is None:
                raise ValueError('Automation key no longer exists.')
            if not isinstance(body.get('enabled'), bool) or body.get('mode') not in ['report', 'apply']:
                raise ValueError('Invalid automation mode or enabled state.')
            if 'fields' in body:
                asset = next(a for a in doc['assets'] if a['id'] == client['asset_id'])
                fields = body['fields']
                if not isinstance(fields, list) or not fields or any(not isinstance(k, str) or k not in allowed_fields(asset) for k in fields):
                    raise ValueError('Unsupported automation fields.')
                client['fields'] = sorted(set(fields))
            client.update(enabled=body['enabled'], mode=body['mode'])
            store.update_client(client)
            return {'ok': True}
        if path == '/api/automations/revoke':
            store.revoke_client(body.get('id'))
            return {'ok': True}
        if path == '/api/automations/review':
            a = next((a for a in doc['assets'] if a['id'] == body.get('asset_id')), None)
            if a is None:
                raise ValueError('Asset no longer exists.')
            if body.get('revision') != a.get('revision', 0):
                raise ConflictError('This asset changed. Close this dialog, refresh, and review the latest record.')
            report = next((r for r in a.get('automation_history', []) if r['id'] == body.get('report_id')), None)
            if report is None or report.get('reviewed_at') or report['mode'] != 'report':
                raise ValueError('This report is no longer awaiting review.')
            if body.get('decision') not in ['accepted', 'dismissed']:
                raise ValueError('Choose accept or dismiss.')
            if body['decision'] == 'accepted':
                for key, value in report['changes'].items():
                    if key not in allowed_fields(a):
                        raise ValueError('The asset type changed; dismiss this outdated report instead.')
                    if key.startswith('details.'):
                        a['details'][key.split('.', 1)[1]] = value
                    else:
                        a[key] = value
            report.update(reviewed_at=now_iso(), decision=body['decision'])
            a['revision'] = a.get('revision', 0) + 1
            store.replace(doc)
            return {'ok': True}
        if path == '/api/import/preview':
            valid = validate_document(body)
            return {'assets': len(valid['assets']), 'relationships': len(valid['relationships']), 'maintenance': len(valid['maintenance'])}
        if path == '/api/import':
            return {'ok': True, 'backup': store.replace(body, backup=True, revoke_keys=True)}
        if path == '/api/backup':
            return {'ok': True, 'backup': store.backup()}
        if path == '/api/assets/save':
            value = dict(body)
            value['id'] = value.get('id') or str(uuid.uuid4())
            previous = next((a for a in doc['assets'] if a['id'] == value['id']), None)
            if previous and value.get('revision') != previous.get('revision', 0):
                raise ConflictError('This asset changed while you were editing, possibly from automation. Close this dialog, refresh, and reopen it before saving.')
            if previous and 'presence_minutes' not in value:
                value['presence_minutes'] = previous.get('presence_minutes', 0)
            value['verification_history'] = previous['verification_history'] if previous else []
            value['automation_history'] = previous.get('automation_history', []) if previous else []
            value['revision'] = previous.get('revision', 0) + 1 if previous else 1
            asset = validate_asset(value)
            doc['assets'] = [asset if a['id'] == asset['id'] else a for a in doc['assets']]
            if previous is None:
                doc['assets'].append(asset)
        elif path == '/api/assets/verify':
            asset = next((a for a in doc['assets'] if a['id'] == body.get('id')), None)
            if asset is None:
                raise ValueError('Asset no longer exists.')
            asset['last_verified'] = now_iso()
            asset['verification_history'].append({'at': asset['last_verified'], 'note': body.get('note', '')})
            asset['revision'] = asset.get('revision', 0) + 1
        elif path == '/api/assets/delete':
            ident = body.get('id')
            if not any(a['id'] == ident for a in doc['assets']):
                raise ValueError('Asset no longer exists.')
            doc['assets'] = [a for a in doc['assets'] if a['id'] != ident]
            doc['relationships'] = [r for r in doc['relationships'] if ident not in [r['source_id'], r['target_id']]]
            doc['maintenance'] = [m for m in doc['maintenance'] if m['asset_id'] != ident]
            saved = store.replace(doc, backup=True)
            return {'ok': True, 'backup': saved}
        elif path == '/api/relationships/save':
            value = {k: body.get(k) for k in ['source_id', 'target_id', 'kind']}
            value['id'] = str(uuid.uuid4())
            doc['relationships'].append(value)
        elif path == '/api/relationships/delete':
            doc['relationships'] = [r for r in doc['relationships'] if r['id'] != body.get('id')]
        elif path == '/api/maintenance/save':
            value = dict(body)
            value['id'] = value.get('id') or str(uuid.uuid4())
            old = next((m for m in doc['maintenance'] if m['id'] == value['id']), None)
            if value.get('last_completed', '') != (old.get('last_completed', '') if old else '') or (value.get('status') == 'COMPLETED' and (not old or old['status'] != 'COMPLETED')):
                raise ValueError('Use Submit review to record maintenance completion with checklist evidence.')
            value['completion_history'] = old.get('completion_history', []) if old else []
            doc['maintenance'] = [value if m['id'] == value['id'] else m for m in doc['maintenance']]
            if old is None:
                doc['maintenance'].append(value)
        elif path == '/api/maintenance/complete':
            if not any(m['id'] == body.get('id') for m in doc['maintenance']):
                raise ValueError('Maintenance item no longer exists.')
            m = next(m for m in doc['maintenance'] if m['id'] == body['id'])
            if body.get('expected') != [m['notes'], m['last_completed'], m['next_due']]:
                raise ConflictError('This checklist changed or was already submitted. Reopen it before submitting.')
            steps = checklist_steps(m['notes'])
            checked = body.get('checked')
            if not isinstance(checked, list) or len(checked) != len(steps) or any(not isinstance(v, bool) for v in checked):
                raise ValueError('Submit a checked or unchecked state for every checklist step.')
            notes = body.get('notes', '')
            if not isinstance(notes, str) or len(notes) > 20000:
                raise ValueError('Review notes must be text under 20000 characters.')
            completed = complete_maintenance(m)
            entry = {'at': completed['last_completed'], 'notes': notes, 'steps': [{'label': label, 'checked': flag} for label, flag in zip(steps, checked)]}
            completed['completion_history'] = (m.get('completion_history', []) + [entry])[-100:]
            doc['maintenance'] = [completed if item['id'] == m['id'] else item for item in doc['maintenance']]
        elif path == '/api/maintenance/delete':
            doc['maintenance'] = [m for m in doc['maintenance'] if m['id'] != body.get('id')]
        elif path == '/api/settings':
            doc['settings'] = validate_settings(body)
        else:
            raise ValueError('Unknown operation.')
        store.replace(doc)
        return {'ok': True}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int, default=8741)
    parser.add_argument('--db', default=str(ROOT / 'data' / 'inventory.sqlite3'))
    parser.add_argument('--open', action='store_true')
    parser.add_argument('--desktop', action='store_true', help='Emit readiness JSON and stop when desktop stdin closes.')
    args = parser.parse_args()
    try:
        server = Server(('127.0.0.1', args.port), args.db)
    except OSError as exc:
        raise SystemExit(f'Cannot start local server: {exc}. If already running, open http://127.0.0.1:{args.port}; otherwise choose --port 8742.')
    url = f'http://127.0.0.1:{server.server_port}'
    if args.desktop:
        print(json.dumps({'event': 'ready', 'url': url, 'version': VERSION}), flush=True)
        def watch_desktop():
            sys.stdin.buffer.read()
            server.shutdown()
        threading.Thread(target=watch_desktop, daemon=True).start()
    else:
        print(f'Reborn IT Operations: {url}\nDatabase: {Path(args.db).resolve()}\nPress Ctrl+C to stop.', flush=True)
    if args.open:
        threading.Timer(0.4, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == '__main__':
    main()
