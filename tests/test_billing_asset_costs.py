import copy
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from aws_inventory import AWSConnection

class BillingAssetCostsTest(unittest.TestCase):
    def test_complete_month_updates_assets_preserving_manual_data(self):
        with tempfile.TemporaryDirectory() as folder:
            assets=[dict(id=i,provider='AWS',details={'account':'123'},revision=2,cost_notes='Manual note',last_verified='2000-01-01') for i in ['linux','windows']]
            document={'assets':assets}
            store=SimpleNamespace(path=Path(folder)/'inventory.sqlite3',read=lambda:copy.deepcopy(document),replace=lambda doc:document.update(doc))
            connection=AWSConnection(SimpleNamespace(store=store,lock=threading.RLock()))
            connection.billing_links={'Amazon Lightsail':{'method':'plan_plus_shared_overage','asset_ids':['linux','windows'],'plan_prices':{'linux':5,'windows':22}}}
            def period(start,end,amount):return {'TimePeriod':{'Start':start,'End':end},'checked_date':'2000-10-01','Groups':[{'Keys':['Amazon Lightsail'],'Metrics':{'UnblendedCost':{'Amount':amount,'Unit':'USD'}}}]}
            report={'account':'123','billing_history':[period('2000-08-01','2000-09-01','41.4021674133'),period('2000-09-01','2000-09-27','99')]}
            connection.apply_billing_costs(report)
            self.assertEqual([a['cost'] for a in document['assets']],[12.2,29.2])
            self.assertEqual([a['expected_cost'] for a in document['assets']],[5,22])
            for a in document['assets']:
                self.assertTrue(a['cost_notes'].startswith('Manual note'))
                self.assertEqual(a['last_verified'],'2000-01-01')
                self.assertEqual(a['revision'],3)
            connection.apply_billing_costs(report)
            self.assertEqual([a['revision'] for a in document['assets']],[3,3])
