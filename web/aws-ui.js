'use strict';

function awsView(){

 const aws=state.aws||{},c=aws.config||{},r=aws.report||{};

 const instances=r.instances||[],billing=r.billing;

 const rows=instances.map(i=>`<tr><td>${esc(i.name||i.id)}<small>${esc(i.id)}</small></td><td>${esc(i.region)}</td><td>${esc(i.type)}</td><td>${esc(i.state)}</td><td>${esc(i.public_ip||'â€”')}</td></tr>`).join('');

 const lightsail=(r.lightsail||[]).flatMap(region=>(region.instances||[])).map(i=>`<tr><td>${esc(i.name)}<small>${esc(i.id||'')}</small></td><td>${esc(i.region)}</td><td>${esc(i.bundle||'')}<small>${esc(i.blueprint||'')}</small></td><td>${esc(i.state||'unknown')}</td><td>${esc(i.public_ip||'—')}</td><td>${i.plan_price_usd==null?'Unknown':esc(i.plan_price_usd)+' USD/month'}</td></tr>`).join('');

 const charges=(billing?.periods||[]).flatMap(p=>(p.Groups||[]).map(g=>`<tr><td>${esc(p.TimePeriod.Start)} to ${esc(p.TimePeriod.End)}${p.Estimated?' (estimated)':''}</td><td>${esc(g.Keys.join(' / '))}</td><td>${esc(g.Metrics.UnblendedCost.Amount)} ${esc(g.Metrics.UnblendedCost.Unit)}</td></tr>`)).join('');

 return heading('READ-ONLY CLOUD CONNECTION','AWS inventory and costs','The backend reads AWS through a dedicated local profile. Keys never go into asset notes or this form.',button('Configure','aws-configure','','btn')+button(aws.busy?'Checkingâ€¦':'Check now','aws-sync','','btn primary'))+

 `<section class="panel"><div class="detail-body"><p><strong>${c.enabled?'Enabled':'Not enabled'}</strong> Â· Profile: ${esc(c.profile||'reborn-it-readonly')} Â· Account: ${esc(c.account||'')} Â· Regions: ${esc((c.regions||[]).join(', '))}</p><p>Inventory refreshes every six hours when enabled. Billing refreshes once per calendar month when enabled ($0.01 per refresh). Last success: ${esc(r.at?new Date(r.at).toLocaleString():'None')}. ${aws.busy?'A check is running.':''}</p>${r.error?`<p class="error-box">${esc(r.error)}</p>`:''}${(r.warnings||[]).map(x=>`<p>${esc(x)}</p>`).join('')}<a class="btn" href="/api/aws/policy">Download IAM policy</a><p>Set up credentials locally with <code>aws configure --profile reborn-it-readonly</code>. Enter keys only into that terminal prompt. No console password or root keys are needed.</p><p>The current policy permits EC2 and Lightsail reads in us-east-2 and Cost Explorer reads. Add other regions to both the policy and connection settings when needed.</p></div></section>`+

 `<section class="panel">${panelHead('Discovered EC2 instances','Discovery does not create or duplicate assets. Instance state is an AWS observation, not an application health check.')}<div class="table-wrap"><table><thead><tr><th>Instance</th><th>Region</th><th>Type</th><th>AWS state</th><th>Public IP</th></tr></thead><tbody>${rows||'<tr><td colspan="5">No AWS observations yet.</td></tr>'}</tbody></table></div><div class="detail-body">${(r.volumes||[]).length} volumes Â· ${(r.snapshots||[]).length} owned snapshots Â· ${(r.addresses||[]).length} allocated addresses. These counts do not prove backups are adequate.</div></section>`+

 `<section class="panel">${panelHead('Discovered Lightsail servers','Windows and Linux plans. Published plan prices are not actual invoices; snapshots, disks, credits and other charges can change the bill.')}<div class="table-wrap"><table><thead><tr><th>Server</th><th>Region</th><th>Plan / image</th><th>AWS state</th><th>Public IP</th><th>Plan price</th></tr></thead><tbody>${lightsail||'<tr><td colspan="6">No Lightsail observations yet.</td></tr>'}</tbody></table></div><details class="detail-body"><summary>Lightsail disks, snapshot settings and observations</summary><pre>${esc(JSON.stringify(r.lightsail||[],null,2))}</pre></details></section>`+

 `<section class="panel"><div class="detail-body"><a class="btn" href="#costs">Open Financials</a><p>Account-wide AWS charges and linked asset bills are in Financials.</p></div></section>`;

}

function awsConfigure(){

 const c=state.aws.config;

 const check=(name,label)=>`<label class="checkbox"><input type="checkbox" name="${name}" ${c[name]?'checked':''}>${label}</label>`;

 openModal('Configure read-only AWS connection',`<div class="form-grid">${input('profile','AWS profile name',c.profile)}${input('account','Expected AWS account ID',c.account)}${input('billing_tag','Optional activated AWS cost allocation tag',c.billing_tag||'','text','Use a tag whose value is the asset ID or AWS resource ID for exact automatic links. Leave empty if not configured in AWS.')}${input('regions','Regions (comma separated)',c.regions.join(', '))}</div>${check('enabled','Enable six-hour inventory collection')}${check('costs','Refresh costs once per month ($0.01/month; about $0.12/year)')}${check('apply_status','Update mapped server status from AWS instance state (not application health)')}${check('apply_matched','Refresh EC2 instance type on already mapped account/region/instance IDs')}<p>All AWS actions are reads. Matched updates never create assets, change human verification, or replace per-device cost with account totals. Missing credentials or permissions are reported here.</p>`,async form=>{

  await api('aws/configure',{billing_tag:form.get('billing_tag')||'',profile:form.get('profile'),account:form.get('account'),regions:form.get('regions').split(',').map(x=>x.trim()).filter(Boolean),enabled:form.has('enabled'),costs:form.has('costs'),resource_costs:false,apply_status:form.has('apply_status'),apply_matched:form.has('apply_matched')});

  await changed('AWS connection settings saved');

 });

}

actions['aws-configure']=()=>awsConfigure();

actions['aws-sync']=async()=>{await api('aws/sync',{});await changed('AWS check started');};



actions['aws-costs']=()=>openModal('Refresh AWS costs — $0.01', '<p>This sends one Cost Explorer request. AWS charges $0.01 for this refresh. It retrieves account/service charges for the previous month and current month to date.</p><p>Continue to run this paid refresh?</p>', async()=>{await api('aws/costs',{confirm_fee:true});await changed('Cost refresh started ($0.01 AWS request)');});


function awsBillingRows(){
 const aws=state.aws||{},report=aws.report||{},billing=report.billing||{};
 const periods=report.billing_history?.length?report.billing_history:(billing.periods||[]);
 return periods.flatMap(p=>(p.Groups||[]).flatMap(g=>{
  const service=g.Keys[0],tagValue=g.Keys[1]?.split('$').slice(1).join('$')||'';
  const matches=tagValue?state.assets.filter(a=>a.provider==='AWS'&&a.details?.account===report.account&&(a.id===tagValue||a.details?.resource_id===tagValue)):[];
  const direct=matches.length===1?matches[0]:null;
  const linked=direct||asset(aws.billing_links?.[service]);
  const resourceKind=service==='Amazon Lightsail'?'Lightsail ':service==='Amazon Elastic Compute Cloud - Compute'?'EC2 ':null;
  const related=resourceKind?state.assets.filter(a=>a.provider==='AWS'&&a.details?.account===report.account&&a.details?.resource_type?.startsWith(resourceKind)):[];
  const row={start:p.TimePeriod.Start,end:p.TimePeriod.End,estimated:p.Estimated,service,tagValue,amount:g.Metrics.UnblendedCost.Amount,unit:g.Metrics.UnblendedCost.Unit,linked,direct,related};
  const split=aws.billing_links?.[service];
  const targets=['equal_split','plan_plus_shared_overage'].includes(split?.method)?split.asset_ids.map(asset).filter(Boolean):[];
  if(!direct&&targets.length===2&&split.method==='plan_plus_shared_overage'){
   const total=Math.round(Number(row.amount)*100),bases=targets.map(a=>Math.round(split.plan_prices[a.id]*100)),baseTotal=bases[0]+bases[1];
   const over=total-baseTotal;
   const first=over>=0?bases[0]+Math.floor(over/2):Math.round(total*bases[0]/baseTotal);
   return targets.map((a,i)=>({...row,linked:a,amount:(i===0?first:total-first)/100,allocation:over>=0?`$${(bases[i]/100).toFixed(2)} plan + equal share of overage (user allocation)`:'Below combined plans: allocated in plan-price proportions (user estimate)'}));
  }
  if(!direct&&targets.length===2)return targets.map(a=>({...row,linked:a,amount:Number(row.amount)/2,allocation:'50/50 user allocation'}));
  return [row];
 })).sort((a,b)=>b.start.localeCompare(a.start)||a.service.localeCompare(b.service));
}
function awsFinancials(assetId=null){
 const aws=state.aws||{},billing=aws.report?.billing;
 const all=awsBillingRows(),rows=assetId?all.filter(r=>r.linked?.id===assetId||r.related.some(a=>a.id===assetId)):all;
 if(assetId&&!rows.length)return '';
 const totals={};for(const r of all){const key=r.start+' / '+r.unit;totals[key]=(totals[key]||0)+Number(r.amount);}
 return `<section class="panel">${panelHead(assetId?'AWS bills and related service charges':'AWS account financials',assetId?'Exact and user-confirmed allocations are labeled. Related service totals may also include other resources.':'All billed AWS services, including services not yet recorded as assets. These totals are separate from manual asset estimates.')}
 <div class="detail-body">${assetId?'':`<p>Monthly billing: ${aws.config?.costs?'enabled':'disabled'} · Last billing attempt: ${esc(billing?.checked_date||'None')} · One request: $0.01.</p>${button('Refresh costs ($0.01)','aws-costs','','btn')}<a class="btn" href="#aws">AWS settings</a><p>${Object.entries(totals).map(([label,total])=>`${esc(label)}: <strong>${total.toFixed(2)}</strong>`).join(' · ')}</p>`}
 ${(billing?.warnings||[]).map(w=>`<p class="error-box">${esc(w)}</p>`).join('')}
 <p>Unblended charges; dates end exclusively. Month-to-date values are a snapshot, not a live bill. Linked rows are part of the account total, not additional charges. Retains up to 24 months.</p></div>
 <div class="table-wrap"><table><thead><tr><th>Billing period</th><th>AWS service</th><th>Charge</th><th>Linked asset</th></tr></thead><tbody>${rows.map(r=>`<tr><td>${esc(r.start)} to ${esc(r.end)}${r.estimated?' (estimated)':''}</td><td>${esc(r.service)}${r.tagValue?`<small>${esc(r.tagValue)}</small>`:''}</td><td>${Number(r.amount).toFixed(2)} ${esc(r.unit)}</td><td>${r.linked?`${button(esc(r.linked.name),'open',r.linked.id,'text-link')}<small>${r.direct?'Exact allocation-tag match':r.allocation||'User-confirmed service allocation'}</small>`:'Account-level / unallocated'}${!r.linked&&r.related.length?`<small>Related servers; amount not allocated:</small>${r.related.map(a=>button(esc(a.name),'open',a.id,'text-link')).join(' ')}`:''}${assetId||r.direct?'':button(r.linked?'Change link':'Link charge','aws-link-bill',r.service,'text-link')}</td></tr>`).join('')||'<tr><td colspan="4">No billing observations yet.</td></tr>'}</tbody></table></div></section>`;
}
actions['aws-link-bill']=service=>{const rule=state.aws.billing_links?.[service];if(rule?.method==='plan_plus_shared_overage')return openModal('Plan prices and shared overage',`<p>${esc(service)}: charge each plan first, then divide the remaining amount equally. These are allocation estimates, not itemized AWS charges. Totals below the combined plans are split in plan-price proportions.</p>${rule.asset_ids.map(id=>input(id,asset(id)?.name||id,rule.plan_prices[id],'number')).join('')}`,async form=>{await api('aws/billing-link',{service,asset_ids:rule.asset_ids,plan_prices:Object.fromEntries(rule.asset_ids.map(id=>[id,Number(form.get(id))]))});await changed('Plan allocation saved');});return openModal('Link AWS service charges',`<p><strong>${esc(service)}</strong></p><p>Only link this entire service charge if it belongs to one asset. For services shared by several servers or buckets, leave it unallocated or configure an activated cost allocation tag in AWS settings. This association applies across the stored billing periods. Saving one asset here replaces any existing equal-split rule.</p><label>Asset<select name="asset_id"><option value="">Account-level / unallocated</option>${state.assets.map(a=>`<option value="${esc(a.id)}" ${state.aws.billing_links?.[service]===a.id?'selected':''}>${esc(a.name)}</option>`).join('')}</select></label>`,async form=>{await api('aws/billing-link',{service,asset_id:form.get('asset_id')});await changed('Billing association saved');});};
