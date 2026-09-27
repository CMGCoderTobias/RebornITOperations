import tempfile
import threading
import unittest
from datetime import datetime, timezone, timedelta
from pathlib import Path
from types import SimpleNamespace
from core import validate_asset
from storage import Store
from watchdog_inventory import WatchdogInventory

class WatchdogInventoryTest(unittest.TestCase):
    def test_heartbeat_source_and_bounded_observations(self):
        with tempfile.TemporaryDirectory() as folder:
            store=Store(Path(folder)/'test.sqlite3')
            doc=store.read()
            doc['assets']=[validate_asset({'id':'server','name':'Server','type':'Cloud','status':'UNKNOWN','provider':'AWS'})]
            store.replace(doc)
            bridge=WatchdogInventory(SimpleNamespace(store=store,lock=threading.RLock()))
            item={'id':'watchdog-prod','connection':'connected','lastHeartbeatAt':datetime.now(timezone.utc).isoformat(),'apps':[]}
            for _ in range(3):bridge.apply({'instances':[item]},{'server':'watchdog-prod'})
            a=store.read()['assets'][0]
            self.assertEqual(a['status'],'ONLINE')
            self.assertEqual(a['last_heartbeat'],item['lastHeartbeatAt'])
            self.assertEqual(len(a['automation_history']),1)
            item['lastHeartbeatAt']=(datetime.now(timezone.utc)-timedelta(minutes=16)).isoformat()
            bridge.apply({'instances':[item]},{'server':'watchdog-prod'})
            self.assertEqual(store.read()['assets'][0]['status'],'OFFLINE')
