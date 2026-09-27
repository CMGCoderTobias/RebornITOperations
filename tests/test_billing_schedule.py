import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from aws_inventory import AWSConnection, DEFAULT, collect
from test_aws_inventory import FakeSession, FakeClient

class BillingScheduleTest(unittest.TestCase):
    def test_one_paid_request_even_if_more_pages(self):
        class Billing(FakeClient):
            calls=0
            def get_cost_and_usage(self, **kwargs):
                self.calls+=1
                return {'ResultsByTime':[], 'NextPageToken':'more'}
        client=Billing(DEFAULT['account'])
        class Session(FakeSession):
            def client(self, service, **kwargs):
                if service=='ce':
                    self_config=kwargs['config']
                    assert self_config.retries['total_max_attempts']==1
                    return client
                return super().client(service,**kwargs)
        report=collect(dict(DEFAULT,costs=True),Session(DEFAULT['account']))
        self.assertEqual(client.calls,1)
        self.assertIn('Partial results',report['billing']['warnings'][0])

    def test_monthly_attempt_survives_restart_and_inventory_is_free(self):
        with tempfile.TemporaryDirectory() as folder:
            server=SimpleNamespace(store=SimpleNamespace(path=Path(folder)/'inventory.sqlite3'))
            connection=AWSConnection(server)
            connection.configure(dict(DEFAULT,enabled=True,costs=True))
            with patch('aws_inventory.threading.Thread') as thread:
                connection.sync()  # Inventory button never incurs a billing call.
                self.assertFalse(thread.call_args.kwargs['args'][0]['costs'])
                connection.busy=False
                connection.sync(scheduled=True)
                self.assertTrue(thread.call_args.kwargs['args'][0]['costs'])
                restarted=AWSConnection(server)
                self.assertFalse(restarted.billing_due())
                restarted.sync(scheduled=True)
                self.assertFalse(thread.call_args.kwargs['args'][0]['costs'])
                restarted.busy=False
                restarted.sync(billing=True)
                self.assertTrue(thread.call_args.kwargs['args'][0]['costs'])
