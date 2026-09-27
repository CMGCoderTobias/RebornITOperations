import unittest
from aws_inventory import collect, collect_lightsail, validate_config, DEFAULT

class FakeClient:
    def __init__(self, account): self.account=account
    def get_caller_identity(self): return {'Account':self.account}
    def can_paginate(self, operation): return False
    def describe_instances(self): return {'Reservations':[{'Instances':[{'InstanceId':'i-test','InstanceType':'c7i.xlarge','State':{'Name':'running'}}]}]}
    def describe_volumes(self): return {'Volumes':[]}
    def describe_snapshots(self, **kwargs):
        assert kwargs == {'OwnerIds':['self']}
        return {'Snapshots':[]}
    def describe_addresses(self): return {'Addresses':[]}
    def get_bundles(self, **kwargs):return {'bundles':[{'bundleId':'small_win','price':12}]}
    def get_instances(self, **kwargs):return {'instances':[{'name':'windows','arn':'arn:test','bundleId':'small_win','addOns':[{'type':'AutoSnapshot','status':'Enabled'}]}]}
    def get_disks(self, **kwargs):return {'disks':[]}
    def get_instance_snapshots(self, **kwargs):return {'instanceSnapshots':[]}
    def get_disk_snapshots(self, **kwargs):return {'diskSnapshots':[]}
    def get_static_ips(self, **kwargs):return {'staticIps':[]}
    def get_auto_snapshots(self, **kwargs):return {'autoSnapshots':[{'date':'2026-09-25','status':'Success'}]}

class FakeSession:
    def __init__(self, account):self.account=account;self.calls=[]
    def client(self, service, **kwargs):
        self.calls.append(service)
        if service=='ce':raise AssertionError('Billing must remain opt-in')
        return FakeClient(self.account)

class AWSReadOnlyTest(unittest.TestCase):
    def test_config_rejects_credentials_and_invalid_account(self):
        with self.assertRaises(ValueError):validate_config({'secret_access_key':'secret'})
        with self.assertRaises(ValueError):validate_config({'account':'wrong'})
        self.assertFalse(validate_config({})['enabled'])

    def test_mismatched_account_stops_before_resource_reads(self):
        session=FakeSession('111111111111' if DEFAULT['account'] != '111111111111' else '222222222222')
        with self.assertRaises(ValueError):collect(DEFAULT,session)
        self.assertEqual(session.calls,['sts'])

    def test_inventory_is_read_only_and_billing_opt_in(self):
        session=FakeSession(DEFAULT['account'])
        result=collect(DEFAULT,session)
        self.assertEqual(result['instances'][0]['id'],'i-test')
        self.assertIsNone(result['billing'])
        self.assertEqual(session.calls,['sts','ec2','lightsail'])
        self.assertEqual(result['lightsail'][0]['instances'][0]['plan_price_usd'],12)
        self.assertTrue(result['lightsail'][0]['instances'][0]['auto_snapshot_enabled'])
        self.assertEqual(result['lightsail'][0]['auto_snapshots'][0]['resource'],'windows')
        self.assertEqual(result['warnings'],[])

    def test_lightsail_pagination_and_partial_failure(self):
        class Pages(FakeClient):
            def get_instances(self, **kwargs):
                if kwargs.get('pageToken')=='next':return {'instances':[{'name':'linux'}]}
                return {'instances':[{'name':'windows'}],'nextPageToken':'next'}
            def get_disks(self, **kwargs):raise PermissionError('Do not expose arbitrary exception text')
        warnings=[]
        result=collect_lightsail(Pages(DEFAULT['account']),'us-east-2',warnings)
        self.assertEqual([i['name'] for i in result['instances']],['windows','linux'])
        self.assertEqual(warnings,['us-east-2 get_disks: PermissionError'])
