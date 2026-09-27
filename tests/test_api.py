import json
import tempfile
import threading
import unittest
import urllib.request
import urllib.error
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from server import Server


class ApiTest(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.server=Server(('127.0.0.1',0),Path(self.tmp.name)/'inventory.sqlite3')
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True)
        self.thread.start()
        self.base=f'http://127.0.0.1:{self.server.server_port}'

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        self.tmp.cleanup()

    def request(self,path,body=None,headers=None):
        req=urllib.request.Request(self.base+path,data=json.dumps(body).encode() if body is not None else None,headers=headers or {})
        try:
            with urllib.request.urlopen(req) as response:
                return response.status,response.read()
        except urllib.error.HTTPError as response:
            return response.code,response.read()

    def test_security_persistence_verification_and_relationships(self):
        status,raw=self.request('/api/state')
        state=json.loads(raw)
        headers={'X-Reborn-Token':state['token'],'Content-Type':'application/json'}
        a={'name':'Test server','type':'Machine','status':'UNKNOWN'}
        self.assertEqual(self.request('/api/assets/save',a)[0],403)
        self.assertEqual(self.request('/api/assets/save',a,dict(headers,Origin='https://evil.example'))[0],403)
        self.assertEqual(self.request('/api/state',headers={'Host':'evil.example'})[0],403)
        self.assertEqual(self.request('/api/assets/save',a,headers)[0],200)
        self.assertEqual(self.request('/api/assets/save',dict(a,name='Worker',type='Service'),headers)[0],200)
        state=json.loads(self.request('/api/state')[1]);one,two=state['assets']
        self.assertEqual(self.request('/api/relationships/save',{'source_id':two['id'],'target_id':one['id'],'kind':'RUNS_ON'},headers)[0],200)
        self.assertEqual(self.request('/api/assets/verify',{'id':one['id'],'note':'Checked purpose'},headers)[0],200)
        state=json.loads(self.request('/api/state')[1]);one=state['assets'][0]
        self.assertTrue(one['last_verified'])
        self.assertEqual(one['last_heartbeat'],'')
        self.assertEqual(one['verification_history'][0]['note'],'Checked purpose')
        saved=json.loads(self.request('/api/export/json')[1])
        self.assertEqual(len(saved['relationships']),1)
        self.assertEqual(self.request('/api/assets/delete',{'id':one['id']},headers)[0],200)
        self.assertEqual(json.loads(self.request('/api/state')[1])['relationships'],[])
        self.assertEqual(self.request('/api/import',saved,headers)[0],200)
        self.assertEqual(len(json.loads(self.request('/api/state')[1])['assets']),2)
        for endpoint in ['/','/app.js','/styles.css','/api/export/csv','/api/export/markdown']:
            self.assertEqual(self.request(endpoint)[0],200)

    def test_checklist_submission_records_partial_review_and_blocks_duplicates(self):
        state=json.loads(self.request('/api/state')[1])
        headers={'X-Reborn-Token':state['token']}
        self.request('/api/assets/save',dict(name='PC',type='Machine',status='ONLINE'),headers)
        aid=json.loads(self.request('/api/state')[1])['assets'][0]['id']
        self.request('/api/maintenance/save',dict(asset_id=aid,title='Review',notes='Instructions\n1. Updates\n2. Disk',frequency_days=30,next_due='2026-09-26'),headers)
        m=json.loads(self.request('/api/state')[1])['maintenance'][0]
        self.assertEqual(self.request('/api/maintenance/complete',{'id':m['id']},headers)[0],409)
        payload=dict(id=m['id'],expected=[m['notes'],m['last_completed'],m['next_due']],checked=[True,False],notes='Disk review deferred')
        self.assertEqual(self.request('/api/maintenance/complete',payload,headers)[0],200)
        self.assertEqual(self.request('/api/maintenance/complete',payload,headers)[0],409)
        saved=json.loads(self.request('/api/state')[1])['maintenance'][0]
        entry=saved['completion_history'][0]
        self.assertEqual(entry['steps'],[dict(label='Updates',checked=True),dict(label='Disk',checked=False)])
        self.assertEqual(entry['notes'],'Disk review deferred')
        self.assertEqual(entry['at'],saved['last_completed'])
        saved['title']='Edited title'
        saved['completion_history']=[]
        self.assertEqual(self.request('/api/maintenance/save',saved,headers)[0],200)
        self.assertEqual(len(json.loads(self.request('/api/state')[1])['maintenance'][0]['completion_history']),1)

    def setup_automation(self, mode='report'):
        ui={'X-Reborn-Token':json.loads(self.request('/api/state')[1])['token']}
        self.assertEqual(self.request('/api/assets/save',{'name':'Worker','type':'Service','status':'UNKNOWN','purpose':'Preserve this purpose'},ui)[0],200)
        a=json.loads(self.request('/api/state')[1])['assets'][0]
        status,raw=self.request('/api/automations/create',{'name':'Test reporter','asset_id':a['id'],'fields':['status','last_heartbeat','details.last_success'],'mode':mode},ui)
        self.assertEqual(status,200)
        client=json.loads(raw)
        return ui,a,client,{'Authorization':'Bearer '+client['secret']}

    def test_report_only_requires_human_review(self):
        ui,a,c,headers=self.setup_automation()
        payload={'changes':{'status':'ONLINE','last_heartbeat':'2026-09-24T12:00:00Z'},'event_id':'run-1'}
        self.assertEqual(self.request('/api/automation/report',payload,headers)[0],200)
        state=json.loads(self.request('/api/state')[1]);updated=state['assets'][0]
        self.assertEqual(updated['status'],'UNKNOWN')
        self.assertEqual(updated['last_heartbeat'],'')
        self.assertEqual(updated['last_verified'],'')
        self.assertTrue(any(r['category']=='Automation report' for r in state['attention']))
        report=updated['automation_history'][0]
        status,_=self.request('/api/automations/review',{'asset_id':a['id'],'report_id':report['id'],'revision':updated['revision'],'decision':'accepted'},ui)
        self.assertEqual(status,200)
        updated=json.loads(self.request('/api/state')[1])['assets'][0]
        self.assertEqual(updated['status'],'ONLINE')
        self.assertEqual(updated['last_verified'],'')
        self.assertEqual(updated['automation_history'][0]['decision'],'accepted')

    def test_apply_is_scoped_audited_and_cannot_verify(self):
        ui,a,c,headers=self.setup_automation('apply')
        self.assertEqual(self.request('/api/automation/report',{'changes':{'status':'ONLINE','details.last_success':'2026-09-24'}},headers)[0],200)
        updated=json.loads(self.request('/api/state')[1])['assets'][0]
        self.assertEqual(updated['status'],'ONLINE')
        self.assertEqual(updated['purpose'],'Preserve this purpose')
        self.assertEqual(updated['last_verified'],'')
        self.assertEqual(updated['verification_history'],[])
        self.assertEqual(updated['automation_history'][0]['before']['status'],'UNKNOWN')
        for field in ['last_verified','purpose','notes','details.last_restore_test','verification_history']:
            self.assertEqual(self.request('/api/automation/report',{'changes':{field:'2026-09-24'}},headers)[0],400)
        self.assertEqual(self.request('/api/assets/verify',{'id':a['id']},headers)[0],403)
        exported=self.request('/api/export/json')[1].decode()
        self.assertNotIn(c['secret'],exported)
        self.assertNotIn('token_hash',exported)
        self.assertIn('Test reporter',exported)

    def test_pause_revoke_and_browser_auth_rejected(self):
        ui,a,c,headers=self.setup_automation('apply')
        payload={'changes':{'status':'ONLINE'}}
        self.assertEqual(self.request('/api/automation/report',payload)[0],401)
        self.assertEqual(self.request('/api/automation/report',payload,dict(headers,Origin=self.base))[0],401)
        self.assertEqual(self.request('/api/automation/report',payload,dict(headers,**{'Sec-Fetch-Site':'same-origin'}))[0],401)
        self.assertEqual(self.request('/api/automation/asset',headers=headers)[0],200)
        self.request('/api/automations/update',{'id':c['client']['id'],'enabled':False,'mode':'apply'},ui)
        self.assertEqual(self.request('/api/automation/report',payload,headers)[0],401)
        self.request('/api/automations/update',{'id':c['client']['id'],'enabled':True,'mode':'apply'},ui)
        self.assertEqual(self.request('/api/automation/report',payload,headers)[0],200)
        self.request('/api/automations/revoke',{'id':c['client']['id']},ui)
        self.assertEqual(self.request('/api/automation/report',payload,headers)[0],401)

    def test_stale_manual_save_does_not_overwrite_report(self):
        ui,a,c,headers=self.setup_automation('apply')
        self.request('/api/automation/report',{'changes':{'status':'ONLINE'}},headers)
        a['notes']='My unsaved note'
        self.assertEqual(self.request('/api/assets/save',a,ui)[0],409)
        fresh=json.loads(self.request('/api/state')[1])['assets'][0]
        self.assertEqual(fresh['status'],'ONLINE')
        fresh['notes']='My refreshed note';fresh['status']='MAINTENANCE'
        self.assertEqual(self.request('/api/assets/save',fresh,ui)[0],200)
        saved=json.loads(self.request('/api/state')[1])['assets'][0]
        self.assertEqual(saved['status'],'MAINTENANCE')
        self.assertEqual(len(saved['automation_history']),1)

    def test_idempotence_validation_and_import_revocation(self):
        ui,a,c,headers=self.setup_automation('apply')
        payload={'changes':{'status':'ONLINE'},'event_id':'unique-123'}
        self.request('/api/automation/report',payload,headers)
        result=json.loads(self.request('/api/automation/report',payload,headers)[1])
        self.assertTrue(result['duplicate'])
        self.assertEqual(len(json.loads(self.request('/api/state')[1])['assets'][0]['automation_history']),1)
        self.assertEqual(self.request('/api/automation/report',{'changes':{'last_heartbeat':'yesterday'}},headers)[0],400)
        exported=json.loads(self.request('/api/export/json')[1])
        self.assertEqual(self.request('/api/import',exported,ui)[0],200)
        self.assertEqual(self.request('/api/automation/report',payload,headers)[0],401)
        restored=json.loads(self.request('/api/state')[1])
        self.assertEqual(len(restored['assets'][0]['automation_history']),1)
        self.assertEqual(restored['automation_clients'],[])

    def test_keys_survive_backend_restart_and_deleted_asset_fails(self):
        ui,a,c,headers=self.setup_automation('apply')
        from storage import Store
        from automation import token_hash
        reopened=Store(self.server.store.path)
        self.assertIsNotNone(reopened.authenticate_client(token_hash(c['secret'])))
        self.request('/api/assets/delete',{'id':a['id']},ui)
        self.assertEqual(self.request('/api/automation/report',{'changes':{'status':'ONLINE'}},headers)[0],404)


if __name__=='__main__':
    unittest.main()
