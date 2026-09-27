"""Optional read-only AWS collector. AWS credentials stay in the SDK profile chain."""
import datetime as dt
import json
import math
from decimal import Decimal, ROUND_HALF_UP
import pathlib
import re
import threading
import time

DEFAULT = dict(enabled=False, profile='reborn-it-readonly', account='000000000000', regions=['us-east-2'], costs=False, resource_costs=False, apply_matched=False, apply_status=False, billing_tag='')

def validate_config(value):
    if not isinstance(value, dict) or set(value)-set(DEFAULT):
        raise ValueError('Only connection settings are accepted; never enter credentials here.')
    result = dict(DEFAULT, **value)
    for key in ['enabled','costs','resource_costs','apply_matched','apply_status']:
        if not isinstance(result[key], bool): raise ValueError('Invalid connection option.')
    if not isinstance(result['profile'], str) or not re.fullmatch(r'[\w.-]{1,100}', result['profile']):
        raise ValueError('Use a named AWS profile, for example reborn-it-readonly.')
    if not isinstance(result['account'], str) or not re.fullmatch(r'\d{12}', result['account']):
        raise ValueError('Expected AWS account must contain 12 digits.')
    if not isinstance(result['regions'], list) or not 1 <= len(result['regions']) <= 10 or any(not isinstance(r,str) or not re.fullmatch(r'[a-z]{2}(?:-[a-z]+)+-\d',r) for r in result['regions']):
        raise ValueError('Specify valid AWS regions.')
    if not isinstance(result['billing_tag'],str) or len(result['billing_tag'])>128:
        raise ValueError('Invalid billing allocation tag.')
    return result

def collect(config, session, previous=None):
    from botocore.config import Config
    settings=Config(connect_timeout=5,read_timeout=20,retries={'max_attempts':2})
    identity=session.client('sts',region_name='us-east-2',config=settings).get_caller_identity()
    if identity['Account'] != config['account']:
        raise ValueError('Connected AWS account does not match the expected account; collection stopped.')
    result=dict(at=dt.datetime.now(dt.timezone.utc).isoformat(),account=identity['Account'],instances=[],volumes=[],snapshots=[],addresses=[],warnings=[])
    for region in config['regions']:
        ec2=session.client('ec2',region_name=region,config=settings)
        operations=[('describe_instances','Reservations',{}),('describe_volumes','Volumes',{}),('describe_snapshots','Snapshots',{'OwnerIds':['self']}),('describe_addresses','Addresses',{})]
        for operation,field,args in operations:
            try:
                items=[]
                if ec2.can_paginate(operation):
                    pages=ec2.get_paginator(operation).paginate(**args,PaginationConfig={'MaxItems':2000})
                else: pages=[getattr(ec2,operation)(**args)]
                for page in pages: items.extend(page.get(field,[]))
                if len(items)>=2000: result['warnings'].append(region+' '+operation+': result limit reached.')
                for item in items:
                    if field=='Reservations':
                        for a in item.get('Instances',[]):
                            result['instances'].append(dict(id=a['InstanceId'],region=region,name=next((t['Value'] for t in a.get('Tags',[]) if t['Key']=='Name'),''),type=a.get('InstanceType',''),state=a.get('State',{}).get('Name','unknown'),public_ip=a.get('PublicIpAddress',''),private_ip=a.get('PrivateIpAddress',''),zone=a.get('Placement',{}).get('AvailabilityZone',''),lifecycle=a.get('InstanceLifecycle','on-demand/other; discounts not determined')))
                    elif field=='Volumes': result['volumes'].append(dict(id=item['VolumeId'],region=region,size_gib=item.get('Size'),type=item.get('VolumeType'),encrypted=item.get('Encrypted'),instances=[v['InstanceId'] for v in item.get('Attachments',[])]))
                    elif field=='Snapshots': result['snapshots'].append(dict(id=item['SnapshotId'],region=region,volume=item.get('VolumeId'),state=item.get('State'),started=str(item.get('StartTime',''))))
                    else: result['addresses'].append(dict(public_ip=item.get('PublicIp'),instance=item.get('InstanceId'),region=region))
            except Exception as error:
                code=getattr(error,'response',{}).get('Error',{}).get('Code',type(error).__name__)
                result['warnings'].append(region+' '+operation+': '+code)
        result.setdefault('lightsail', []).append(collect_lightsail(session.client('lightsail',region_name=region,config=settings),region,result['warnings']))
    previous=previous or {}
    result['billing']=previous.get('billing')
    result['billing_history']=previous.get('billing_history',[])
    today=dt.datetime.now(dt.timezone.utc).date()
    if config['costs']:
        # One paid request maximum: no SDK retries, pagination, or extra queries.
        ce=session.client('ce',region_name='us-east-1',config=Config(connect_timeout=5,read_timeout=20,retries={'total_max_attempts':1}))
        first=today.replace(day=1)
        start=(first-dt.timedelta(days=1)).replace(day=1)
        billing=dict(checked_date=today.isoformat(),start=start.isoformat(),end=today.isoformat(),metric='UnblendedCost',scope='Account/service totals, not per-device charges',periods=[],resource_periods=[],resource_costs_enabled=False,warnings=[])
        try:
            page=ce.get_cost_and_usage(TimePeriod={'Start':start.isoformat(),'End':today.isoformat()},Granularity='MONTHLY',Metrics=['UnblendedCost'],GroupBy=[{'Type':'DIMENSION','Key':'SERVICE'}]+([{'Type':'TAG','Key':config['billing_tag']}] if config.get('billing_tag') else []))
            billing['periods']=page.get('ResultsByTime',[])
            if page.get('NextPageToken'):
                billing['warnings'].append('Partial results: additional pages were not requested to keep this refresh to one $0.01 request.')
        except Exception as error:
            code=getattr(error,'response',{}).get('Error',{}).get('Code',type(error).__name__)
            billing['warnings'].append(code+' (check IAM and Cost Explorer settings). No automatic retry this month.')
            billing['previous_success']=result['billing'].get('previous_success') or {k:v for k,v in result['billing'].items() if k!='previous_success'} if result['billing'] else None
        billing['tag']=config.get('billing_tag','')
        history={p['TimePeriod']['Start']:p for p in previous.get('billing_history',[])}
        if not billing['warnings']:
            for period in billing['periods']:history[period['TimePeriod']['Start']]=dict(period,tag=billing['tag'],checked_date=today.isoformat())
        result['billing_history']=[history[k] for k in sorted(history)[-24:]]
        result['billing']=billing
    return result

def collect_lightsail(client, region, warnings):
    """Allowlisted metadata only; never request access details or key pairs."""
    def read(operation, field, **args):
        items=[]
        try:
            for _ in range(100):
                page=getattr(client,operation)(**args)
                items.extend(page.get(field,[]))
                if len(items)>=2000:
                    warnings.append(region+' '+operation+': result limit reached.')
                    return items[:2000]
                if not page.get('nextPageToken'):return items
                args['pageToken']=page['nextPageToken']
            warnings.append(region+' '+operation+': page limit reached.')
        except Exception as error:
            code=getattr(error,'response',{}).get('Error',{}).get('Code',type(error).__name__)
            warnings.append(region+' '+operation+': '+code)
        return items
    bundles={b['bundleId']:b for b in read('get_bundles','bundles',includeInactive=True)}
    instances=[]
    for a in read('get_instances','instances'):
        bundle=bundles.get(a.get('bundleId'),{})
        instances.append(dict(id=a.get('arn'),name=a.get('name'),region=region,bundle=a.get('bundleId'),blueprint=a.get('blueprintId'),state=a.get('state',{}).get('name'),public_ip=a.get('publicIpAddress'),private_ip=a.get('privateIpAddress'),hardware={k:a.get('hardware',{}).get(k) for k in ['cpuCount','ramSizeInGb']},plan_price_usd=bundle.get('price'),auto_snapshot_enabled=any(x.get('type')=='AutoSnapshot' and x.get('status')=='Enabled' for x in a.get('addOns',[]))))
    disks=[{k:a.get(k) for k in ['name','arn','sizeInGb','state','attachedTo','isAttached']} for a in read('get_disks','disks')]
    snapshots=[{k:a.get(k) for k in ['name','arn','state','fromInstanceName','fromInstanceArn','sizeInGb','isFromAutoSnapshot']} for a in read('get_instance_snapshots','instanceSnapshots')]
    disk_snapshots=[{k:a.get(k) for k in ['name','arn','state','fromDiskName','sizeInGb']} for a in read('get_disk_snapshots','diskSnapshots')]
    addresses=[{k:a.get(k) for k in ['name','ipAddress','attachedTo','isAttached']} for a in read('get_static_ips','staticIps')]
    auto=[]
    for resource in instances+disks:
        if resource.get('name'):
            for a in read('get_auto_snapshots','autoSnapshots',resourceName=resource['name']):
                auto.append(dict(resource=resource['name'],date=a.get('date'),status=a.get('status')))
    return dict(region=region,instances=instances,disks=disks,snapshots=snapshots,disk_snapshots=disk_snapshots,addresses=addresses,auto_snapshots=auto)

class AWSConnection:
    def __init__(self, server):
        self.server=server
        self.root=server.store.path.parent
        self.config_path=self.root/'aws-connection.json'
        self.report_path=self.root/'aws-latest.json'
        self.lock=threading.RLock()
        self.billing_attempt_path=self.root/'aws-billing-attempt.json'
        self.billing_attempt=self.read(self.billing_attempt_path,{})
        self.busy=False
        self.stop=threading.Event()
        self.config=dict(DEFAULT,**self.read(self.config_path,{}))
        self.report=self.read(self.report_path,{})
        self.billing_links_path=self.root/'aws-billing-links.json'
        self.billing_links=self.read(self.billing_links_path,{})

    @staticmethod
    def read(path,default):
        try:return json.loads(path.read_text())
        except (OSError,ValueError):return default

    @staticmethod
    def write(path,value):
        temp=path.with_suffix('.tmp')
        temp.write_text(json.dumps(value,indent=2),encoding='utf-8')
        temp.replace(path)

    def status(self):
        with self.lock:return {'config':dict(self.config),'report':self.report,'busy':self.busy,'billing_links':dict(self.billing_links)}

    def link_billing(self, body):
        service=body.get('service')
        asset_id=body.get('asset_id','')
        asset_ids=body.get('asset_ids')
        if not isinstance(service,str) or not service or len(service)>256:raise ValueError('Select an AWS service charge.')
        assets=self.server.store.read()['assets']
        if asset_id and not any(a['id']==asset_id for a in assets):raise ValueError('Select an existing asset.')
        if asset_ids is not None:
            if not isinstance(asset_ids,list) or len(asset_ids)!=2 or len(set(asset_ids))!=2 or any(not any(a['id']==i for a in assets) for i in asset_ids):
                raise ValueError('Select two different existing assets for an equal split.')
        plans=body.get('plan_prices')
        if plans is not None:
            if not asset_ids or not isinstance(plans,dict) or set(plans)!=set(asset_ids) or any(isinstance(v,bool) or not isinstance(v,(int,float)) or not math.isfinite(v) or v<0 for v in plans.values()) or sum(plans.values())<=0:
                raise ValueError('Supply a nonnegative plan price for each linked asset.')
        with self.lock:
            if plans is not None:self.billing_links[service]={'asset_ids':asset_ids,'method':'plan_plus_shared_overage','plan_prices':plans}
            elif asset_ids:self.billing_links[service]={'asset_ids':asset_ids,'method':'equal_split'}
            elif asset_id:self.billing_links[service]=asset_id
            else:self.billing_links.pop(service,None)
            self.write(self.billing_links_path,self.billing_links)
        self.apply_billing_costs(self.report)
        return {'ok':True}

    def apply_billing_costs(self, report):
        """Update explicitly allocated assets from a complete month, never MTD."""
        month=dt.datetime.now(dt.timezone.utc).date().replace(day=1).isoformat()
        periods=[p for p in report.get('billing_history',[]) if p['TimePeriod']['Start'].endswith('-01') and p['TimePeriod']['End'].endswith('-01') and p['TimePeriod']['End']<=month]
        if not periods:return
        period=max(periods,key=lambda p:p['TimePeriod']['Start'])
        with self.lock:links=dict(self.billing_links)
        cents=lambda value:int((Decimal(str(value))*100).quantize(Decimal('1'),rounding=ROUND_HALF_UP))
        with self.server.lock:
            doc=self.server.store.read()
            changed=False
            for service,rule in links.items():
                if isinstance(rule,str):
                    a=next((a for a in doc['assets'] if a['id']==rule),None)
                    groups=[g for g in period.get('Groups',[]) if g['Keys'][0]==service]
                    if not a or not groups or any(g['Metrics']['UnblendedCost']['Unit']!='USD' for g in groups):continue
                    amount=cents(sum(Decimal(g['Metrics']['UnblendedCost']['Amount']) for g in groups))/100
                    if amount<0:continue
                    marker='[AWS allocated recurring cost]'
                    notes=a.get('cost_notes','').split(marker)[0].rstrip()
                    note=f"{marker}\n{period['TimePeriod']['Start']} to {period['TimePeriod']['End']} (end exclusive): ${amount:.2f}/month from {service}. User-linked account/service allocation, not resource-itemized billing. Other AWS service charges are excluded."
                    updates=dict(cost=amount,billing_period='monthly',cost_checked=period.get('checked_date',''),cost_notes=(notes+'\n\n'+note).strip())
                    if any(a.get(k)!=v for k,v in updates.items()):a.update(updates);a['revision']=a.get('revision',0)+1;changed=True
                    continue
                if not isinstance(rule,dict) or rule.get('method')!='plan_plus_shared_overage':continue
                targets=[next((a for a in doc['assets'] if a['id']==i),None) for i in rule['asset_ids']]
                if len(targets)!=2 or any(a is None or a.get('provider')!='AWS' or a.get('details',{}).get('account')!=report.get('account') for a in targets):continue
                groups=[g for g in period.get('Groups',[]) if g['Keys'][0]==service]
                if not groups or any(g['Metrics']['UnblendedCost']['Unit']!='USD' for g in groups):continue
                total=cents(sum(Decimal(g['Metrics']['UnblendedCost']['Amount']) for g in groups))
                bases=[cents(rule['plan_prices'][a['id']]) for a in targets]
                remainder=total-sum(bases)
                first=bases[0]+remainder//2 if remainder>=0 else int((Decimal(total)*bases[0]/sum(bases)).quantize(Decimal('1'),rounding=ROUND_HALF_UP))
                for a,amount in zip(targets,[first,total-first]):
                    marker='[AWS allocated recurring cost]'
                    notes=a.get('cost_notes','').split(marker)[0].rstrip()
                    note=f"{marker}\n{period['TimePeriod']['Start']} to {period['TimePeriod']['End']} (end exclusive): ${amount/100:.2f}/month allocated from {service}. Plan first, shared overage; below-plan totals use proportional allocation. User allocation, not an itemized AWS charge. Updated from cached billing; current partial month excluded."
                    updates=dict(cost=amount/100,expected_cost=rule['plan_prices'][a['id']],billing_period='monthly',cost_checked=period.get('checked_date') or report.get('billing',{}).get('checked_date',''),cost_notes=(notes+'\n\n'+note).strip())
                    if any(a.get(k)!=v for k,v in updates.items()):
                        a.update(updates);a['revision']=a.get('revision',0)+1;changed=True
            if changed:self.server.store.replace(doc)

    def apply_cloud_status(self, report):
        instances=report.get('instances',[])+[i for r in report.get('lightsail',[]) for i in r.get('instances',[])]
        states={'running':'ONLINE','stopped':'OFFLINE','stopping':'OFFLINE','terminated':'RETIRED','shutting-down':'OFFLINE'}
        with self.server.lock:
            doc=self.server.store.read();changed=False
            for a in doc['assets']:
                if a['type']!='Cloud' or a['provider']!='AWS' or a['details'].get('account')!=report.get('account'):continue
                item=next((i for i in instances if i['id']==a['details'].get('resource_id') and i['region']==a['details'].get('region')),None)
                if not item:continue
                a['details']['aws_state']=item.get('state','unknown')
                a['details']['aws_state_checked']=report.get('at','')
                if not a['details'].get('watchdog_id') and a['status'] not in ['PAUSED','ARCHIVED','MAINTENANCE'] and item.get('state') in states:a['status']=states[item['state']]
                a['revision']=a.get('revision',0)+1;changed=True
            if changed:self.server.store.replace(doc)

    def configure(self,body):
        config=validate_config(body)
        with self.lock:
            if self.busy:raise ValueError('Wait for the running AWS check before changing settings.')
            if config['account']!=self.config['account'] or config['profile']!=self.config['profile']:
                self.report={}
                self.billing_links={}
                self.write(self.billing_links_path,self.billing_links)
                self.write(self.report_path,self.report)
            self.config=config
            self.write(self.config_path,config)
        return {'ok':True}

    def billing_due(self):
        month=dt.datetime.now(dt.timezone.utc).strftime('%Y-%m')
        return self.config['costs'] and self.billing_attempt.get('month') != month

    def sync(self, *, billing=False, scheduled=False):
        with self.lock:
            if not self.config['enabled']:raise ValueError('Enable the AWS connection after configuring its profile.')
            if self.busy:return {'ok':True,'running':True}
            config=dict(self.config)
            config['costs']=billing or (scheduled and self.billing_due())
            if config['costs']:
                self.billing_attempt={'month':dt.datetime.now(dt.timezone.utc).strftime('%Y-%m')}
                self.write(self.billing_attempt_path,self.billing_attempt)
            self.busy=True
        threading.Thread(target=self.run,args=(config,),daemon=True).start()
        return {'ok':True,'running':True}

    def run(self,config):
        attempted=dt.datetime.now(dt.timezone.utc).isoformat()
        try:
            import boto3
            session=boto3.Session(profile_name=config['profile'])
            result=collect(config,session,self.report)
            if config['apply_matched']:
                with self.server.lock:
                    doc=self.server.store.read()
                    for a in doc['assets']:
                        if a['type']!='Cloud' or a['provider']!='AWS':continue
                        match=next((i for i in result['instances'] if i['id']==a['details'].get('resource_id') and i['region']==a['details'].get('region') and a['details'].get('account')==result['account']),None)
                        if match:
                            # Resource identity must already be mapped by the user. No guessing or new assets.
                            a['details']['resource_type']='EC2 '+match['type']
                            a['revision']=a.get('revision',0)+1
                    self.server.store.replace(doc)
            if config.get('apply_status'):self.apply_cloud_status(result)
            result.update(attempted_at=attempted,error='')
            if config['costs'] and not result.get('billing',{}).get('warnings'):
                self.apply_billing_costs(result)
            with self.lock:self.report=result
        except Exception as error:
            code=getattr(error,'response',{}).get('Error',{}).get('Code',type(error).__name__)
            with self.lock:
                self.report=dict(self.report,attempted_at=attempted,error=code+': check the configured AWS profile, expected account, permissions, and SDK installation.')
        finally:
            with self.lock:
                self.write(self.report_path,self.report)
                self.busy=False

    def start(self):
        def loop():
            while not self.stop.wait(60):
                state=self.status()
                last=state['report'].get('attempted_at')
                due=not last or time.time()-dt.datetime.fromisoformat(last).timestamp()>=21600
                if state['config']['enabled'] and not state['busy'] and (due or self.billing_due()):
                    try:self.sync(scheduled=True)
                    except ValueError:pass
        threading.Thread(target=loop,daemon=True).start()
