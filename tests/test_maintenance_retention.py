import unittest
from datetime import datetime, timezone
from automation import compact_reports
from core import maintenance_state, complete_maintenance

class MaintenanceRetentionTest(unittest.TestCase):
    def test_completed_recurrence_waits_until_due(self):
        now=datetime(2026,9,26,tzinfo=timezone.utc)
        m=dict(status='NOT_STARTED',last_completed='',next_due='2026-09-26',frequency_days=30)
        m=complete_maintenance(m,now)
        self.assertEqual(maintenance_state(m,now),'COMPLETED')
        self.assertEqual(maintenance_state(m,datetime(2026,10,26,tzinfo=timezone.utc)),'DUE')

    def test_routine_reports_do_not_accumulate_and_failure_survives(self):
        reports=[]
        for i in range(1000):
            r=dict(id=str(i),client_id='pc',at=f'{i:04d}',changes={'status':'FAILED' if i in [5,6] else 'ONLINE'},before={})
            reports=compact_reports(reports+[r])
        self.assertEqual([r['id'] for r in reports],['6','999'])

    def test_repeated_low_disk_is_one_condition(self):
        reports=[dict(id=str(i),client_id='pc',at=str(i),changes={'details.storage':f'C: 475 GiB total; {i} GiB free (5%)'},before={}) for i in range(5)]
        self.assertEqual(len(compact_reports(reports)),1)
