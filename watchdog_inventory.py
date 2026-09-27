"""Read existing Control Center heartbeats; never issue remote control commands."""
import datetime as dt
import json
import threading
import urllib.request
from automation import build_report
from core import now_iso, parse_date


class WatchdogInventory:
    def __init__(self, server):
        self.server=server
        self.stop=threading.Event()
        self.config_path=server.store.path.parent/'watchdog-connection.json'
        self.result_path=server.store.path.parent/'watchdog-latest.json'

    def apply(self, data, mappings):
        entries={i['id']:i for i in data.get('instances',[])}
        now=dt.datetime.now(dt.timezone.utc)
        with self.server.lock:
            doc=self.server.store.read()
            for index,a in enumerate(doc['assets']):
                source=mappings.get(a['id'])
                if not source or source not in entries:continue
                item=entries[source]
                heartbeat=item.get('lastHeartbeatAt')
                if not heartbeat:continue
                age=(now-parse_date(heartbeat)).total_seconds()
                status='ONLINE' if item.get('connection')=='connected' and 0<=age<900 else 'OFFLINE'
                a['details'].update(watchdog_id=source,watchdog_checked=now_iso(),watchdog_status=item.get('connection','unknown'),watchdog_apps='; '.join(str(x.get('name'))+': '+str(x.get('status')) for x in item.get('apps',[])),watchdog_warning=item.get('endpointValidation',{}).get('detail','') if item.get('endpointValidation',{}).get('status')=='invalid' else '')
                changes={'last_heartbeat':heartbeat}
                if a['status'] not in ['PAUSED','ARCHIVED','MAINTENANCE']:changes['status']=status
                client={'id':'watchdog:'+source,'name':'Watchdog Control Center: '+source,'mode':'apply','fields':['status','last_heartbeat']}
                doc['assets'][index]=build_report(a,client,{'changes':changes})
            self.server.store.replace(doc)

    def check(self):
        try:
            config=json.loads(self.config_path.read_text())
            if not config.get('enabled'):return
            # This integration intentionally uses only the co-located Control Center.
            with urllib.request.urlopen('http://127.0.0.1:49155/api/instances',timeout=10) as response:
                data=json.load(response)
            self.apply(data,config.get('mappings',{}))
            result={'at':now_iso(),'state':'reported'}
        except FileNotFoundError:return
        except Exception as error:result={'at':now_iso(),'state':'failed','error':type(error).__name__}
        self.result_path.write_text(json.dumps(result))

    def start(self):
        def loop():
            while not self.stop.is_set():
                self.check()
                self.stop.wait(60)
        threading.Thread(target=loop,daemon=True).start()
