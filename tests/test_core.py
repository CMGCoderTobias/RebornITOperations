import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from core import validate_asset, validate_document, attention, complete_maintenance, maintenance_state, monthly_cost, csv_export, markdown_export, DEFAULT_SETTINGS
from storage import Store

NOW = datetime(2026, 9, 24, 12, tzinfo=timezone.utc)


def asset(ident='server', **kwargs):
    return validate_asset({'id': ident, 'name': ident, 'type': 'Machine', 'status': 'UNKNOWN', **kwargs})


def doc(assets=None, **kwargs):
    return {'schema_version': 1, 'assets': assets or [], 'relationships': [], 'maintenance': [], 'settings': DEFAULT_SETTINGS.copy(), **kwargs}


class RulesTest(unittest.TestCase):
    def test_forgotten_server_is_visible_without_heartbeat_agent(self):
        a = asset(status='OFFLINE', purpose='Old Git mirror', last_verified='2026-04-26', last_heartbeat='2026-04-30', heartbeat_minutes=5)
        categories = {r['category'] for r in attention(doc([a]), NOW)}
        self.assertTrue({'Unavailable', 'Forgotten', 'Heartbeat missing', 'Orphaned'} <= categories)

    def test_backup_exists_does_not_imply_restore_tested(self):
        a = asset(important_data=True)
        b = asset('backup', type='Backup', status='ACTIVE', details={'last_success': '2026-09-24'})
        d = doc([a, b], relationships=[{'id':'r','source_id':'backup','target_id':'server','kind':'BACKS_UP'}])
        alerts = attention(d, NOW)
        self.assertFalse(any(r['category']=='Unbacked-up' for r in alerts))
        self.assertTrue(any(r['category']=='Unverified backup' for r in alerts))
        b['status'] = 'RETIRED'
        self.assertTrue(any(r['category']=='Unbacked-up' for r in attention(d, NOW)))

    def test_heartbeat_does_not_refresh_manual_verification(self):
        a=asset(status='ONLINE', last_heartbeat=NOW.isoformat(), heartbeat_minutes=5, last_verified='2026-01-01')
        categories={r['category'] for r in attention(doc([a]),NOW)}
        self.assertIn('Forgotten',categories)
        self.assertNotIn('Heartbeat missing',categories)

    def test_domain_cost_and_resolved_failure(self):
        a=asset(type='Domain',status='ACTIVE',cost=120,expected_cost=100,billing_period='yearly',details={'domain_expires':'2026-09-23','certificate_expires':'2026-10-01'})
        b=asset('job',type='Automation',status='ACTIVE',details={'last_failure':'2026-09-20','last_success':'2026-09-21'})
        alerts=attention(doc([a,b]),NOW)
        self.assertEqual(monthly_cost(a),10)
        self.assertEqual(sum(r['category']=='Expiration' for r in alerts),2)
        self.assertTrue(any(r['category']=='Cost increase' for r in alerts))
        self.assertFalse(any(r['category']=='Recently failed' for r in alerts))

    def test_transitive_project_connectivity_and_retirement(self):
        assets=[asset(),asset('worker',type='Service',status='ACTIVE'),asset('project',type='Project',status='ACTIVE')]
        relations=[{'id':'r1','source_id':'worker','target_id':'server','kind':'RUNS_ON'},{'id':'r2','source_id':'worker','target_id':'project','kind':'BELONGS_TO'}]
        self.assertFalse(any(r['category']=='Orphaned' and r['asset_id']=='server' for r in attention(doc(assets,relationships=relations),NOW)))
        self.assertEqual(attention(doc([asset(status='RETIRED')]),NOW),[])

    def test_recurrence_and_due_dates(self):
        m={'id':'m','asset_id':'server','title':'OS update','notes':'','status':'NOT_STARTED','priority':'NORMAL','next_due':'2026-09-23','last_completed':'','frequency_days':7}
        self.assertEqual(maintenance_state(m,NOW),'OVERDUE')
        result=complete_maintenance(m,NOW)
        self.assertEqual(result['next_due'],'2026-10-01')
        self.assertEqual(result['status'],'NOT_STARTED')
        self.assertEqual(result['last_completed'],NOW.isoformat(timespec='seconds'))
        self.assertEqual(complete_maintenance(dict(m,frequency_days=0),NOW)['status'],'COMPLETED')

    def test_invalid_imports(self):
        base=doc([asset()])
        bad=[dict(base,assets=[asset(),asset()]),dict(base,relationships=[{'id':'r','source_id':'server','target_id':'missing','kind':'USES'}]),dict(base,assets=[dict(asset(),cost=float('nan'))]),dict(base,assets=[dict(asset(),last_verified='yesterday')]),dict(base,assets=[dict(asset(),details={'password':'x'})]),dict(base,schema_version=2)]
        for value in bad:
            with self.subTest(value=value),self.assertRaises(ValueError):
                validate_document(value)

    def test_exports_include_full_record_and_formula_protection(self):
        d=doc([asset(name='=HYPERLINK("x")',notes='Recovery notes')])
        self.assertIn("'=HYPERLINK",csv_export(d))
        self.assertIn('Recovery notes',csv_export(d))
        self.assertIn('Recovery notes',markdown_export(d))
        self.assertEqual(validate_document(json.loads(json.dumps(d))),d)


class PersistenceTest(unittest.TestCase):
    def test_roundtrip_and_failed_import_preserves_original(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'inventory.sqlite3'
            store=Store(path)
            original=doc([asset()])
            store.replace(original)
            self.assertEqual(Store(path).read()['assets'],original['assets'])
            with self.assertRaises(ValueError):
                store.replace(dict(original,assets=[dict(asset(),status='INVALID')]),backup=True)
            self.assertEqual(store.read()['assets'],original['assets'])
            backup=store.replace(doc([asset('new')]),backup=True)
            self.assertEqual(Store(Path(folder)/'backups'/backup).read()['assets'],original['assets'])
            self.assertEqual(store.read()['assets'][0]['id'],'new')


if __name__=='__main__':
    unittest.main()
