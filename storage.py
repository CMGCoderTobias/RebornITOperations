"""SQLite persistence. Imports are validated before an atomic transaction."""
import json
import sqlite3
import uuid
from contextlib import contextmanager
from pathlib import Path
from core import DEFAULT_SETTINGS, validate_document, now_iso


class Store:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS assets (id TEXT PRIMARY KEY, data TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS relationships (
                    id TEXT PRIMARY KEY,
                    source_id TEXT NOT NULL REFERENCES assets(id) ON DELETE CASCADE,
                    target_id TEXT NOT NULL REFERENCES assets(id) ON DELETE CASCADE,
                    kind TEXT NOT NULL,
                    UNIQUE(source_id, target_id, kind), CHECK(source_id != target_id));
                CREATE TABLE IF NOT EXISTS maintenance (
                    id TEXT PRIMARY KEY,
                    asset_id TEXT NOT NULL REFERENCES assets(id) ON DELETE CASCADE,
                    data TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS settings (id INTEGER PRIMARY KEY CHECK(id=1), data TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS automation_keys (id TEXT PRIMARY KEY, token_hash TEXT UNIQUE NOT NULL, data TEXT NOT NULL);
            ''')
            db.execute('INSERT OR IGNORE INTO settings VALUES (1, ?)', (json.dumps(DEFAULT_SETTINGS),))

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path)
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA foreign_keys=ON')
        try:
            with db:
                yield db
        finally:
            db.close()

    def read(self):
        with self.connect() as db:
            return {'schema_version': 1, 'exported_at': now_iso(),
                    'assets': [json.loads(r['data']) for r in db.execute('SELECT data FROM assets ORDER BY rowid')],
                    'relationships': [dict(r) for r in db.execute('SELECT * FROM relationships')],
                    'maintenance': [json.loads(r['data']) for r in db.execute('SELECT data FROM maintenance ORDER BY rowid')],
                    'settings': json.loads(db.execute('SELECT data FROM settings WHERE id=1').fetchone()['data'])}

    def backup(self):
        folder = self.path.parent / 'backups'
        folder.mkdir(exist_ok=True)
        dest = folder / (now_iso().replace(':', '-') + '-' + uuid.uuid4().hex[:8] + '.sqlite3')
        with self.connect() as source:
            target = sqlite3.connect(dest)
            try:
                source.backup(target)
            finally:
                target.close()
        return dest.name

    def replace(self, document, backup=False, revoke_keys=False):
        doc = validate_document(document)
        saved = self.backup() if backup else None
        with self.connect() as db:
            db.execute('DELETE FROM relationships')
            db.execute('DELETE FROM maintenance')
            db.execute('DELETE FROM assets')
            db.executemany('INSERT INTO assets VALUES (?, ?)', [(a['id'], json.dumps(a)) for a in doc['assets']])
            db.executemany('INSERT INTO relationships VALUES (?, ?, ?, ?)', [(r['id'], r['source_id'], r['target_id'], r['kind']) for r in doc['relationships']])
            db.executemany('INSERT INTO maintenance VALUES (?, ?, ?)', [(m['id'], m['asset_id'], json.dumps(m)) for m in doc['maintenance']])
            db.execute('UPDATE settings SET data=? WHERE id=1', (json.dumps(doc['settings']),))
            if revoke_keys:
                db.execute('DELETE FROM automation_keys')
        return saved

    def clients(self):
        with self.connect() as db:
            return [json.loads(r['data']) for r in db.execute('SELECT data FROM automation_keys')]

    def create_client(self, client, digest):
        with self.connect() as db:
            db.execute('INSERT INTO automation_keys VALUES (?, ?, ?)', (client['id'], digest, json.dumps(client)))

    def authenticate_client(self, digest):
        with self.connect() as db:
            row = db.execute('SELECT data FROM automation_keys WHERE token_hash=?', (digest,)).fetchone()
            client = json.loads(row['data']) if row else None
            return client if client and client['enabled'] else None

    def update_client(self, client):
        with self.connect() as db:
            db.execute('UPDATE automation_keys SET data=? WHERE id=?', (json.dumps(client), client['id']))

    def revoke_client(self, ident):
        with self.connect() as db:
            db.execute('DELETE FROM automation_keys WHERE id=?', (ident,))

    def save_report(self, asset, client):
        # Atomically persist a single patched asset plus last-seen metadata.
        with self.connect() as db:
            db.execute('UPDATE assets SET data=? WHERE id=?', (json.dumps(asset), asset['id']))
            db.execute('UPDATE automation_keys SET data=? WHERE id=?', (json.dumps(client), client['id']))
