import unittest
from datetime import datetime, timezone
from core import validate_asset, attention, DEFAULT_SETTINGS
from automation import allowed_fields, build_report


class PCInventoryTest(unittest.TestCase):
    def test_verified_standalone_pc_needs_no_dummy_project(self):
        asset = validate_asset({'id':'pc','name':'PC','type':'Machine','status':'ONLINE','purpose':'Personal development','last_verified':'2026-09-25T12:00:00Z'})
        doc = dict(assets=[asset], relationships=[], maintenance=[], settings=DEFAULT_SETTINGS)
        self.assertNotIn('Orphaned', [r['category'] for r in attention(doc, datetime(2026,9,26,tzinfo=timezone.utc))])
        asset['last_verified'] = ''
        self.assertIn('Orphaned', [r['category'] for r in attention(doc, datetime(2026,9,26,tzinfo=timezone.utc))])

    def test_storage_report_preserves_manual_facts(self):
        asset = validate_asset({'id':'pc','name':'PC','type':'Machine','status':'ONLINE','location':'Home office','provider':'Personally owned','last_verified':'2026-09-25T12:00:00Z'})
        fields = allowed_fields(asset)
        self.assertIn('details.storage', fields)
        for forbidden in ['location','provider','last_verified']:
            self.assertNotIn(forbidden, fields)
        client = dict(id='reporter',name='Reporter',mode='apply',fields=['details.storage'])
        updated = build_report(asset, client, {'changes':{'details.storage':'C: 40 GiB free'}})
        self.assertEqual(updated['details']['storage'], 'C: 40 GiB free')
        for key in ['location','provider','last_verified']:
            self.assertEqual(updated[key], asset[key])
        with self.assertRaises(ValueError):
            build_report(asset, client, {'changes':{'last_verified':'2026-09-26'}})

    def test_mobile_offline_does_not_raise_outage_alert(self):
        asset = validate_asset({'id':'mobile','name':'Laptop','type':'Machine','status':'OFFLINE','presence_minutes':5,'heartbeat_minutes':5,'last_heartbeat':'2026-09-01T00:00:00Z'})
        result = attention(dict(assets=[asset],relationships=[],maintenance=[],settings=DEFAULT_SETTINGS),datetime(2026,9,26,tzinfo=timezone.utc))
        self.assertFalse(any(r['category'] in ['Unavailable','Heartbeat missing'] for r in result))
