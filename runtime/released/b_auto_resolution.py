"""Resolve one deterministic activity group using verified source evidence."""
from datetime import datetime,timezone
import hashlib
import json
import re
from account_primitives import fail
from b_state import canon,fingerprint
from b_submission_declarations import PLATFORM_FIELD,input_fingerprint,input_formula_version,projected_declarations
from b_domain import fields,owns_submission,sub_revision,material,entity,validate_form, latest_run_id, pending_items, can_material, grant_scopes

RULE='grouped-intake-auto/1.0'
QUALIFICATION='optional-brand/1.0+display-name/1.0'
SCOPE_RULE='all-products-clause/1.0'


def utc(value):
    return datetime.fromisoformat(value.replace('Z','+00:00')).astimezone(timezone.utc).isoformat() if value else None

def identity(values):
    try:values=[values.get(k) for k in ['platformCode','sourceActivityId']]+[utc(values.get(k)) for k in ['startAt','endAt']]+[sorted(values.get('brandIds') or [])]
    except (TypeError,ValueError,AttributeError):return None
    return fingerprint(values) if all(values) else None

def original_value(db,source,payload,ev,originals=None):
    if ev['materialId'] not in source['allMaterialIds']:return None
    original=originals.get(ev['materialId']) if originals is not None else material(db,ev['materialId'])
    if not original or original['sha256']!=ev['sourceSha256']:return None
    if originals is None and hashlib.sha256(original['content']).hexdigest()!=original['sha256']:return None
    loc=ev['locator']
    model_quote=(payload.get('result') or {}).get('engine')=='deepseek_model_first_v1'
    if ev['materialId']==source['formMaterialId']:
        from b_submission_declarations import recorded_form
        form=recorded_form(source);path=loc.get('fieldPath','')
        if loc.get('kind')!='form' or original['content']!=canon(form).encode():return None
        value=next((r for r in (form.get('manualProductRows') or []) if r['rowId']==path[len('manualProductRows.'):]),None) if path.startswith('manualProductRows.') else form.get(path)
        quote=value if isinstance(value,str) else canon(value)
        return value if value is not None and (isinstance(ev['quote'],str) and bool(ev['quote']) and ev['quote'] in quote if model_quote else ev['quote']==quote[:500]) else None
    doc=next((d for d in payload.get('materialResults',[]) if d.get('materialId')==ev['materialId'] and d.get('sha256')==original['sha256']),None)
    locations=doc.get('textBlocks',[])+[c for row in doc.get('tableRows',[]) for c in row['cells']] if doc else []
    located=next((l['text'] for l in locations if l['locator']==loc and (isinstance(ev['quote'],str) and bool(ev['quote']) and ev['quote'] in l['text'] if model_quote else l['text'][:500]==ev['quote'])),None)
    return located

def field_source_value(value,ev,payload,field,aliases):
    locator=ev['locator']
    if locator.get('kind')=='form':return value if locator.get('fieldPath')==field else None
    if not isinstance(value,str):return None
    match=re.fullmatch(r'\s*([^:：]+)[:：]\s*(.*)',value,re.S)
    if match and match[1].strip() in aliases:return match[2].strip()
    doc=next((d for d in payload.get('materialResults',[]) if d.get('materialId')==ev['materialId']),None)
    rows=doc.get('tableRows',[]) if doc else []
    for index,row in enumerate(rows):
        cell=next((c for c in row['cells'] if c['locator']==locator),None)
        if cell:
            for prior in reversed(rows[:index]):
                header=next((c for c in prior['cells'] if c['column']==cell['column'] and c['text'].strip() in aliases),None)
                if header:return value.strip()
    return None

def all_products_clause(value):
    if not isinstance(value,str):return False
    clauses=[part.strip() for part in re.split('[;；]',value)]
    applicable=[c for c in clauses if re.fullmatch('适用商品[:：](?:全部商品|全场商品)',c)]
    exclusion=[c for c in clauses if re.fullmatch('排除商品[:：]无',c)]
    if len(applicable)!=1 or len(exclusion)!=1:return False
    others=[c for c in clauses if c not in applicable+exclusion]
    return not any(re.search('适用商品|排除|不适用|除外|不含|仅限|限定|限制|不参与|部分商品|指定商品',c) for c in others)

def result_base(sub,candidate=None):
    return {'outcome':'needs_confirmation','submissionId':sub['id'],'inputRevision':sub['revision'],'candidateId':candidate['candidateId'] if candidate else None,'activityId':None,'versionId':None,'resolutionMode':'automatic','ruleVersion':RULE,'qualificationVersion':QUALIFICATION,'resolutionMaterialId':None,'reviewState':None,'productApplicability':{'kind':'unknown','scopeBrandIds':[],'evidenceRefs':[],'scopeRuleVersion':None},'reasonCodes':[],'remainingCandidateIds':[]}

def origin(db,resolution):
    value=json.loads(resolution['payload']);mode=value.get('resolutionMode');rule=value.get('ruleVersion');mid=value.get('resolutionMaterialId')
    if mode is None:
        event=db.execute('SELECT payload FROM b_history WHERE id=?',(value.get('historyEventId'),)).fetchone()
        details=json.loads(event[0]).get('details',{}) if event else {}
        binding=details.get('resolutionBinding')
        if binding:mode=binding.get('resolutionMode');rule=binding.get('ruleVersion');mid=binding.get('resolutionMaterialId')
        else:
            version=entity(db,'activity_versions',resolution['version_id']);manual=version.get('snapshot',{}).get('manualConfirmation',{})
            if details.get('confirmationMaterialId'):mode='manual';mid=details['confirmationMaterialId']
            elif version.get('submissionId')==resolution['submission_id'] and manual.get('confirmationMaterialId'):mode='manual';mid=manual['confirmationMaterialId']
    return mode,rule,mid,value

def already_resolved(db,resolution):
    mode,rule,mid,value=origin(db,resolution)
    version=entity(db,'activity_versions',resolution['version_id']);review=db.execute('SELECT state FROM version_reviews WHERE version_id=?',(resolution['version_id'],)).fetchone()
    remaining=[]
    if value.get('analysisRunId'):
        run=db.execute('SELECT payload FROM analysis_runs WHERE id=?',(value['analysisRunId'],)).fetchone()
        if run:
            done={r[0] for r in db.execute('SELECT candidate_id FROM resolutions WHERE submission_id=? AND input_revision=?',
                                         (resolution['submission_id'],resolution['input_revision']))}
            remaining=[c['candidateId'] for c in json.loads(run[0]).get('result',{}).get('candidates',[])
                       if c['candidateId'] not in done]
    return {'outcome':'already_resolved','submissionId':resolution['submission_id'],'inputRevision':resolution['input_revision'],'candidateId':resolution['candidate_id'],'activityId':resolution['activity_id'],'versionId':resolution['version_id'],'resolutionMode':mode,'ruleVersion':rule,'resolutionMaterialId':mid,'reviewState':review['state'] if review else None,'productApplicability':version.get('snapshot',{}).get('productApplicability'),'originalOutcome':value['outcome'],'originalResolution':value,'reasonCodes':[] if mode else ['RESOLUTION_SOURCE_NOT_RECORDED'],'remainingCandidateIds':remaining,'pendingItems':value.get('pendingItems',[]),'reviewBlockedReason':value.get('reviewBlockedReason')}

def identity_values(field,key,analysis):
    values=[field['value']]
    if field['status']=='conflict':
        evidence=set(field['evidenceIds'])
        for conflict in analysis['conflicts']:
            if conflict['field']==key and evidence.intersection(conflict['evidenceIds']):
                values.extend(conflict.get('extractedValues',[]))
    return {v for v in values if isinstance(v,str) and v.strip()}


def corroborated_brands(field,analysis,verified_evidence):
    sources=set(field['evidenceIds'])
    if not sources or not sources <= verified_evidence.keys():
        return []
    recorded=[field['value']] if isinstance(field['value'],list) else []
    for conflict in analysis['conflicts']:
        if conflict['field']!='brandIds' or not sources.intersection(conflict['evidenceIds']):
            continue
        if not set(conflict['evidenceIds']) <= verified_evidence.keys():
            return []
        if conflict.get('manualValue') is not None:
            recorded.append(conflict['manualValue'])
        recorded.extend(conflict.get('extractedValues',[]))
    if len(recorded)<2 or any(not isinstance(ids,list) for ids in recorded):
        return []
    common=set.intersection(*(set(ids) for ids in recorded))
    return [brand for brand in recorded[0] if brand in common]



def display_name_for_unique_identity(source,payload,candidate,analysis,evs,valid):
    """A display label cannot override the independently evidenced main identity."""
    if len(analysis['candidates'])!=1:return None
    form=source['form'];name=form.get('activityName')
    if not isinstance(name,str) or not name.strip():return None
    fields=candidate['fields']
    if any(c['field'] in ('platformCode','sourceActivityId') for c in analysis['conflicts']):return None
    for key in ('platformCode','sourceActivityId'):
        f=fields[key];refs=f['evidenceIds']
        if f['status']!='explicit' or not f['value'] or not refs or any(e not in valid for e in refs):return None
        if form.get(key) and form[key]!=f['value']:return None
    platform=fields['platformCode']['value'];material_platform=False
    if not isinstance(platform,str):return None
    tokens={'meituan':{'meituan','美团','美团原价'},'taobao_instant':{'taobao_instant','淘宝闪购'},'jiatan':{'jiatan','价探'}}
    for eid in fields['platformCode']['evidenceIds']:
        value=field_source_value(valid[eid],evs[eid],payload,'platformCode',{'平台','来源平台','platformCode'})
        if value is None:value=valid[eid]
        if not isinstance(value,str) or value not in tokens.get(platform,set()):return None
        material_platform|=evs[eid]['materialId']!=source['formMaterialId']
    if not material_platform:return None
    number=fields['sourceActivityId'];material_number=False
    for eid in number['evidenceIds']:
        value=field_source_value(valid[eid],evs[eid],payload,'sourceActivityId',{'神价ID','主活动ID','平台活动ID','平台活动编号','活动编号','sourceActivityId'})
        if value!=number['value']:return None
        material_number|=evs[eid]['materialId']!=source['formMaterialId']
    if not material_number:return None
    name_refs=fields['activityName']['evidenceIds']
    if not any(e in valid and evs[e]['materialId']==source['formMaterialId'] and evs[e]['locator'].get('fieldPath')=='activityName' and valid[e]==name for e in name_refs):return None
    return name

def automatic(domain,db,actor,sid,body,command_id):
    fields(body,['expectedSubmissionRevision','analysisRunId','analysisDigest'],['candidateId'])
    sub=owns_submission(db,actor,sid,domain.auth,True)
    if type(body['expectedSubmissionRevision']) is not int or body['expectedSubmissionRevision']!=sub['revision']:fail(409,'ANALYSIS_STALE','输入版本变化')
    if not isinstance(body['analysisRunId'],str):fail(422,'INVALID_REQUEST','分析ID必须为字符串')
    run=db.execute('SELECT * FROM analysis_runs WHERE id=? AND submission_id=?',(body['analysisRunId'],sid)).fetchone()
    if latest_run_id(db,sid,sub['revision'])!=body['analysisRunId']:fail(409,'ANALYSIS_STALE','请使用当前最新分析')
    if run and run['state'] in ['queued','running','cancel_requested']:fail(409,'ANALYSIS_NOT_READY','分析尚未完成')
    if not run or run['input_revision']!=sub['revision'] or run['state'] not in ['succeeded','partial']:fail(409,'ANALYSIS_STALE','分析不属于当前有效输入')
    payload=json.loads(run['payload']);analysis=payload.get('result')
    input_row=db.execute('SELECT * FROM submission_revisions WHERE submission_id=? AND revision=?',(sid,sub['revision'])).fetchone()
    if not analysis or payload.get('analysisDigest')!=body['analysisDigest'] or 'sha256:'+fingerprint(analysis)!=body['analysisDigest'] or run['input_digest']!=input_row['input_digest']:fail(409,'ANALYSIS_STALE','分析或输入摘要变化')
    candidates=analysis['candidates']
    if 'candidateId' in body:
        selected=body['candidateId']
        if not isinstance(selected,str):fail(422,'INVALID_REQUEST','候选ID必须为字符串')
        candidate=next((c for c in candidates if c['candidateId']==selected),None)
        if not candidate:fail(422,'INVALID_REQUEST','候选不属于实际分析')
    else:
        candidate=candidates[0] if len(candidates)==1 else None
    if candidate:
        prior=db.execute('SELECT * FROM resolutions WHERE submission_id=? AND input_revision=? AND candidate_id=?',(sid,sub['revision'],candidate['candidateId'])).fetchone()
        if prior:return 200,already_resolved(db,prior)
    catalog=json.loads(db.execute("SELECT value FROM b_meta WHERE key='catalogRevision'").fetchone()[0])
    if catalog!=run['catalog_revision']:fail(409,'ANALYSIS_STALE','目录版本变化')
    attempt_digest=fingerprint({'method':'POST','action':'submissions.auto_resolve','path':'/submissions/'+sid+'/auto-resolve','resource':sid,'body':body})
    qualification=QUALIFICATION
    if domain.activity_date_evidence:
        from b_activity_dates import VERSION as DATE_RULE
        qualification+='+'+DATE_RULE
    for prior_command in db.execute("SELECT response FROM commands WHERE actor_id=? AND action='submissions.auto_resolve' AND resource_id=? AND digest=?",(actor['id'],sid,attempt_digest)):
        previous=json.loads(prior_command['response'])
        if previous.get('outcome')=='needs_confirmation' and previous.get('ruleVersion')==RULE and previous.get('qualificationVersion')==qualification:return 200,previous
    result=result_base(sub,candidate)
    result['qualificationVersion']=qualification
    completed={r[0] for r in db.execute('SELECT candidate_id FROM resolutions WHERE submission_id=? AND input_revision=?',(sid,sub['revision']))}
    result['remainingCandidateIds']=[c['candidateId'] for c in candidates if c['candidateId'] not in completed]
    reasons=result['reasonCodes']
    def risk(code):
        if code not in reasons:reasons.append(code)
    if run['state']=='partial' or any(m.get('status')!='parsed' for m in payload.get('materialResults',[])) or any(w.get('code')=='LIMIT_EXCEEDED' for w in analysis.get('warnings',[])):risk('PARSE_COVERAGE_UNCONFIRMED')
    if candidate is None:risk('SINGLE_ACTIVITY_UNCONFIRMED');return 200,result
    source=sub_revision(db,sid,sub['revision']);originals={mid:material(db,mid) for mid in source['allMaterialIds']}
    from b_submission_declarations import recorded_form
    if originals[source['formMaterialId']]['content']!=canon(recorded_form(source)).encode():risk('SOURCE_INTEGRITY_UNCONFIRMED')
    if source.get('rawTextMaterialId') and originals[source['rawTextMaterialId']]['content']!=source.get('rawText','').encode():risk('SOURCE_INTEGRITY_UNCONFIRMED')
    reported={m.get('materialId'):m for m in payload.get('materialResults',[])}
    if set(reported)!=set(originals) or any(reported.get(mid,{}).get('sha256')!=m['sha256'] for mid,m in originals.items()):risk('PARSE_COVERAGE_UNCONFIRMED')
    verified={}
    for mid,m in originals.items():
        if m['owner_id']!=actor['id'] or hashlib.sha256(m['content']).hexdigest()!=m['sha256'] or not can_material(db,actor,mid,domain.auth):
            risk('SOURCE_INTEGRITY_UNCONFIRMED')
        else:
            verified[mid]=m
    try:
        formula=input_formula_version(source)
        current_digest=input_fingerprint(sub['revision'],source,originals)
    except (KeyError,TypeError,ValueError):fail(409,'ANALYSIS_STALE','原始输入绑定不符')
    if current_digest!=run['input_digest']:fail(409,'ANALYSIS_STALE','原始输入绑定不符')
    if formula=='input/1.2' and not getattr(domain,'submission_declarations_persistence_ready',False):
        fail(409,'ANALYSIS_STALE','当前服务不支持此输入绑定')
    if 'activityMethod' in source and not getattr(domain,'activity_method_persistence_ready',False):
        fail(503,'ACTIVITY_METHOD_NOT_READY','当前服务未就绪，暂不能建档活动方式')
    group_refs={e for field in candidate['fields'].values() for e in field['evidenceIds']}
    group_refs.update(e for row in candidate['productRows'] for e in row['evidenceIds'])
    evs={e['evidenceId']:e for e in analysis['evidence'] if e['evidenceId'] in group_refs}
    valid={}
    for eid,ev in evs.items():
        value=original_value(db,source,payload,ev,verified)
        if value is not None:valid[eid]=value
    has_bound_material=any(evs[eid]['materialId']!=source['formMaterialId'] for eid in valid)
    if not has_bound_material and any(
            doc['materialId']!=source['formMaterialId'] and (doc.get('textBlocks') or doc.get('tableRows'))
            for doc in payload['materialResults']):
        risk('PARSE_COVERAGE_UNCONFIRMED')
    values={key:field['value'] for key,field in candidate['fields'].items()}
    display_name=display_name_for_unique_identity(source,payload,candidate,analysis,evs,valid)
    if display_name is not None:values['activityName']=display_name
    pending_reasons={}
    identity_fields={'activityName','type','platformCode','sourceActivityId'}
    name_refs=set(candidate['fields']['activityName']['evidenceIds'])
    draft_locations={canon(w['locator']) for w in analysis['warnings']
                     if w.get('code')=='COUPON_DRAFT_NAME' and 'locator' in w}
    number_locs={canon(evs[eid]['locator']) for eid in candidate['fields']['sourceActivityId']['evidenceIds'] if eid in valid}
    draft_refs={eid for eid in name_refs & valid.keys()
                if (canon(evs[eid]['locator']) in draft_locations or draft_locations&number_locs)
                and isinstance(valid[eid],str)
                and re.fullmatch(r'(?:满\s*)?\d+(?:\.\d+)?\s*[-－—减]\s*\d+(?:\.\d+)?\s*(?:元)?',valid[eid].strip())}
    real_refs=name_refs-draft_refs
    verified_name=bool(real_refs) and all(
        eid in valid and field_source_value(valid[eid],evs[eid],payload,'activityName',{'活动名称','活动名','activityName'})==values['activityName']
        for eid in real_refs
    )
    for key,field in candidate['fields'].items():
        if field['status']=='conflict':
            if key=='activityName' and (display_name is not None or draft_refs and verified_name):
                continue
            if key in identity_fields:
                risk('FIELD_CONFLICT')
            elif key=='brandIds':
                values[key]=corroborated_brands(field,analysis,valid)
            else:
                values[key]=None
        elif field['status']=='explicit' and (not field['evidenceIds'] or any(e not in valid for e in field['evidenceIds'])):
            if key in identity_fields:
                risk('EVIDENCE_UNVERIFIED')
            else:
                values[key]=None
    brand_field=candidate['fields']['brandIds']
    brand_divergence=any(c['field']=='brandIds' and set(c['evidenceIds']).intersection(valid)
                         for c in analysis['conflicts'])
    if (brand_divergence or brand_field['status']=='conflict'
            or brand_field['status']=='explicit' and not values.get('brandIds')):
        pending_reasons['brandIds']='BRAND_UNCONFIRMED'
    source_number_refs=candidate['fields']['sourceActivityId']['evidenceIds']
    number_locators={canon(evs[eid]['locator']) for eid in source_number_refs if eid in valid}
    if any(w.get('code')=='COUPON_BRAND_MAPPING_INCOMPLETE' and 'locator' in w
           and canon(w['locator']) in number_locators for w in analysis['warnings']):
        pending_reasons['brandIds']='BRAND_UNCONFIRMED'
    title=values.get('activityName')
    if (not isinstance(title,str) or not title.strip()
            or re.fullmatch(r'活动\s*[一二三四五六七八九十0-9]+|活动名称|模板|示例|待填写',title.strip())):
        risk('REQUIRED_CONTEXT_MISSING')
    if not values.get('type'):risk('REQUIRED_CONTEXT_MISSING')
    if values.get('sourceActivityId') and not values.get('platformCode'):
        pending_reasons['platformCode']='SOURCE_PLATFORM_UNVERIFIED'
        values['sourceActivityId']=None
    date_evidence=None
    if domain.activity_date_evidence:
        from b_activity_dates import collect,for_creation,overrides
        observed,registration=collect(db,values,payload,candidate,run,source,verified)
        for key in ('startAt','endAt'):
            if candidate['fields'][key]['status']=='conflict':
                observed[key]=[(None,ref) for _,ref in observed[key]]
        updates,date_evidence=for_creation(values,observed,registration,candidate,analysis)
        values.update(updates)
        pending_reasons.update(overrides(values,date_evidence))
    try:validate_form(db,{k:v for k,v in values.items() if k!='companyBearing'})
    except Exception as error:
        if getattr(error,'code',None) not in ['NOT_FOUND','INVALID_REQUEST']:raise
        risk('IDENTITY_INCOMPLETE')
    if (not (values.get('platformCode') and values.get('sourceActivityId'))
            and any(w.get('code')=='DATE_RANGE_CONFLICT' for w in analysis['warnings'])
            and all(candidate['fields'][key]['status']=='conflict' for key in ('startAt','endAt'))):
        risk('FIELD_CONFLICT')
    if draft_refs and not real_refs:
        if not (values.get('platformCode') and values.get('sourceActivityId')):
            risk('IDENTITY_INCOMPLETE')
        else:
            pending_reasons['activityName']='PLATFORM_NAME_UNVERIFIED'
    identity_key=identity(values)
    name_key=lambda value: (value or '').strip().casefold()
    pair=(values.get('platformCode'),values.get('sourceActivityId'))
    from b_grouping import siblings,sibling_activity_ids
    sibling_ids=siblings(payload,candidate)
    sibling_activities=sibling_activity_ids(db,run,sibling_ids)
    for other in candidates:
        if other is candidate or other['candidateId'] in sibling_ids and not other['fields']['sourceActivityId']['value']:continue
        other_fields=other['fields']
        other_pair=tuple(other_fields[k]['value'] if other_fields[k]['status']=='explicit' else None
                         for k in ('platformCode','sourceActivityId'))
        distinct=all(pair) and all(other_pair) and pair[0]==other_pair[0] and pair[1]!=other_pair[1]
        other_names=identity_values(other_fields['activityName'],'activityName',analysis)
        other_platforms=identity_values(other_fields['platformCode'],'platformCode',analysis)
        other_numbers=identity_values(other_fields['sourceActivityId'],'sourceActivityId',analysis)
        overlapping_pair=all(pair) and pair[1] in other_numbers and (not other_platforms or pair[0] in other_platforms)
        if overlapping_pair or not distinct and any(name_key(v)==name_key(values.get('activityName')) for v in other_names):
            risk('DUPLICATE_AMBIGUOUS')
    bearing=candidate['fields']['companyBearing'];state=bearing['value']['state']
    bearing_ids=bearing['evidenceIds']
    bearing_tokens={'是':'yes','有':'yes','无':'no','否':'no','未知':'unknown','yes':'yes','no':'no','unknown':'unknown'}
    def supports_bearing(eid):
        source_value=field_source_value(valid.get(eid),evs[eid],payload,'companyBearing',{'公司承担','companyBearing'}) if eid in valid else None
        if isinstance(source_value,dict):source_value=source_value.get('state')
        return isinstance(source_value,str) and bearing_tokens.get(source_value)==state
    bearing_verified=bool(bearing_ids) and all(supports_bearing(e) for e in bearing_ids)
    no_eligible=state=='no' and bearing['status']=='explicit' and bearing_verified
    if bearing['status']!='explicit' or not bearing_verified:
        values['companyBearing']={'state':'unknown','evidenceIds':[]}
    bearing_ids=values['companyBearing']['evidenceIds']
    refs=lambda ids:[{'runId':run['id'],'evidenceId':eid} for eid in ids]
    full_rules=[field_source_value(valid[e],evs[e],payload,'rulesText',{'优惠机制','rulesText'}) for e in candidate['fields']['rulesText']['evidenceIds'] if e in valid]
    declared=any(all_products_clause(v) and v==(values.get('rulesText') or '').strip() for v in full_rules)
    brands=values.get('brandIds') or []
    if len(brands)>1 or 'brandIds' in pending_reasons or values.get('type')=='brand' and len(brands)!=1:
        declared=False
    if declared and any(isinstance(values.get(key),str) and re.search('排除|不适用|不含|仅限|限定|部分商品|指定商品',values[key]) for key in ['brandBenefit','remark']):
        declared=False;pending_reasons['productApplicability']='PRODUCT_SCOPE_UNCONFIRMED'
    products=[];unresolved=[];rows=candidate['productRows']
    scope={'kind':'unknown','scopeBrandIds':values.get('brandIds') or [],'evidenceRefs':[],'scopeRuleVersion':None}
    if rows:
        current_products=[json.loads(r[0]) for r in db.execute('SELECT body FROM products')]
        if declared:pending_reasons['productApplicability']='PRODUCT_SCOPE_UNCONFIRMED'
        for row in rows:
            product=next((p for p in current_products if p['id']==row['matchedProductId']),None)
            valid_row=bool(product and row['matchKind'] in ('exact','manual_selected')
                           and row['evidenceIds'] and all(e in valid for e in row['evidenceIds']))
            if valid_row and row['matchKind']=='exact':
                identifiers=[(row[raw],key) for raw,key in [('rawInternalCode','internalCode'),('rawUpc','upc')] if row.get(raw)]
                valid_row=bool(identifiers and all(
                    {p['id'] for p in current_products if p.get(key)==code}=={product['id']} for code,key in identifiers)
                    and (not row.get('rawUpc') or re.fullmatch(r'\d{8,14}',row['rawUpc'])))
            if valid_row and row['matchKind']=='manual_selected':
                valid_row=any(isinstance(valid.get(e),dict) and valid[e].get('productId')==product['id'] for e in row['evidenceIds'])
            if valid_row and values.get('brandIds') and product.get('brandId') not in values['brandIds']:
                valid_row=False
            if valid_row and any(row.get(raw) and row[raw]!=product.get(key)
                                 for raw,key in [('rawInternalCode','internalCode'),('rawUpc','upc'),('rawName','name')]):
                valid_row=False
            if not valid_row:
                unresolved.append(row['rowId'])
                pending_reasons.setdefault('productApplicability','PRODUCT_SCOPE_UNCONFIRMED')
                continue
            products.append(product['id']);scope['evidenceRefs'].extend(refs(row['evidenceIds']))
        if not unresolved and not declared:scope['kind']='explicit_skus'
    elif declared and 'PRODUCT_SCOPE_UNCONFIRMED' not in reasons:
        scope.update(kind='all_products',scopeRuleVersion=SCOPE_RULE,evidenceRefs=refs(candidate['fields']['rulesText']['evidenceIds']))
    pending=pending_items(values,scope,unresolved,pending_reasons)
    result['productApplicability']=scope
    # Broad read is evidence of a complete local business set, not a new grant.
    read_scope=grant_scopes(db,actor,domain.auth,'operations.read')
    metadata={r[0]:json.loads(r[1]) for r in db.execute('SELECT * FROM metadata')}
    if 'operations.read' not in domain.auth.permissions(db,actor) or '*' not in read_scope or not metadata.get('sourceReady'):
        # Match the activity read API's current broad resource requirement. Stop
        # before inspecting private identities/names; risk details must not be an
        # existence oracle for records outside the caller's readable set.
        risk('DUPLICATE_SCOPE_UNCONFIRMED')
        db.execute("UPDATE b_submissions SET state=? WHERE id=?",('partially_resolved' if completed else 'needs_confirmation',sid))
        return 200,result
    if all(pair) and domain.supplement_identity_exists(db,None,values,include_current=False):
        risk('DUPLICATE_AMBIGUOUS')
    exact=set();potential=set();activities={}
    for row in db.execute('SELECT id,body FROM activities'):
        activity=json.loads(row['body']);activities[row['id']]=activity
        vid=activity.get('displayVersionId') or activity.get('currentReviewVersionId')
        v=db.execute('SELECT body FROM activity_versions WHERE id=?',(vid,)).fetchone();snapshot=json.loads(v[0]).get('snapshot',{}) if v else {}
        old=snapshot.get('resolvedFields',snapshot.get('confirmedFields',{}))
        old_id=identity(old) if old else None
        indexed=db.execute('SELECT 1 FROM activity_identities WHERE fingerprint=? AND activity_id=?',(identity_key,row['id'])).fetchone() if identity_key else None
        if identity_key and (old_id==identity_key or indexed):exact.add(row['id'])
        same_name=isinstance(values.get('activityName'),str) and (activity.get('name') or '').strip().casefold()==values['activityName'].strip().casefold()
        external={(x.get('platformCode'),x.get('id')) for x in activity.get('externalIds',[])}
        same_source=all(pair) and pair in external
        distinct=all(pair) and external and all(p[0]==pair[0] and p[1] and p[1]!=pair[1] for p in external)
        if same_source or same_name and not distinct and row['id'] not in sibling_activities:potential.add(row['id'])
    if len(exact)>1 or potential-exact:risk('DUPLICATE_AMBIGUOUS')
    target=next(iter(exact)) if len(exact)==1 else None
    target_revision=activities[target]['revision'] if target else None
    if target:
        old_activity=activities[target];target_vid=old_activity.get('displayVersionId') or old_activity.get('currentReviewVersionId');target_version=entity(db,'activity_versions',target_vid);snap=target_version.get('snapshot',{});old=snap.get('resolvedFields',snap.get('confirmedFields'))
        if not isinstance(old,dict):risk('SOURCE_CONFLICT')
        else:
            for key in ['activityName','type','platformCode','sourceActivityId','rulesText','brandBenefit']:
                if old.get(key)!=values.get(key):risk('SOURCE_CONFLICT')
            if any(utc(old.get(k))!=utc(values.get(k)) for k in ['startAt','endAt']) or set(old.get('brandIds') or [])!=set(values.get('brandIds') or []) or old.get('companyBearing',{}).get('state','unknown')!=values['companyBearing']['state']:risk('SOURCE_CONFLICT')
            old_scope=snap.get('productApplicability') or {'kind':'explicit_skus' if not snap.get('unresolvedRows') else 'unknown','scopeBrandIds':old.get('brandIds') or []}
            known={r[0] for r in db.execute('SELECT product_id FROM activity_products WHERE activity_id=?',(target,))}
            if old_scope['kind']!=scope['kind'] or set(old_scope['scopeBrandIds'])!=set(scope['scopeBrandIds']) or scope['kind']=='explicit_skus' and known!=set(products):risk('SOURCE_CONFLICT')
            if any(json.loads(r[0]).get('activityPrice') is not None for r in db.execute('SELECT body FROM activity_products WHERE activity_id=?',(target,))):risk('SOURCE_CONFLICT')
    applicable=projected_declarations(source,source['form'].get('platformCode'))[PLATFORM_FIELD]
    if target is None and values.get('type')=='brand' and not applicable:
        risk('BRAND_PLATFORM_SELECTION_REQUIRED')
    if reasons:
        db.execute("UPDATE b_submissions SET state=? WHERE id=?",('partially_resolved' if completed else 'needs_confirmation',sid));return 200,result
    no_eligible=no_eligible and not pending and identity_key is not None
    basis={'basisKind':'deterministic_evidence' if no_eligible else 'requires_management_verification','defaultApprovalEligible':no_eligible,'evidenceRefs':refs(bearing_ids),'eligibility':'verified_explicit_no' if no_eligible else 'management_review'}
    initiator={'id':sub['submitter_id'],'roleSnapshot':None}
    for receipt in db.execute('SELECT payload FROM b_history WHERE submission_id=? ORDER BY rowid',(sid,)):
        event=json.loads(receipt['payload'])
        if event.get('event')=='submission.received' and event.get('actorId')==sub['submitter_id']:
            initiator['roleSnapshot']=event.get('roleSnapshot');break
    binding={'resolutionMode':'automatic','ruleVersion':RULE,'qualificationVersion':qualification,'initiatedBy':initiator,'executor':{'kind':'deterministic_rule','name':'auto-resolution'},'runId':run['id'],'inputRevision':sub['revision'],'inputDigest':run['input_digest'],'analysisDigest':payload['analysisDigest'],'catalogRevision':run['catalog_revision'],'candidateId':candidate['candidateId'],'intent':'link_source' if target else 'create','targetActivityId':target,'targetRevisionBefore':target_revision,'resolvedFields':values,'productApplicability':scope,'pendingItems':pending,'duplicatePolicy':{'ruleVersion':RULE,'jointFingerprint':identity_key,'identityPlatform':pair[0],'sourceActivityId':pair[1],'checkedScope':'local_readable_complete'},'basisEvidenceRefs':refs(sorted(valid)),'bearingVerification':basis,'explainedWarnings':[{'code':w['code'],'scopeRuleVersion':SCOPE_RULE,'evidenceRefs':scope['evidenceRefs']} for w in analysis.get('warnings',[]) if w.get('code')=='PRODUCT_ATTRIBUTES_WITHOUT_ANCHOR' and declared]}
    if display_name is not None:
        binding['displayNamePolicy']={'ruleVersion':'display-name/1.0','displayName':display_name,'sourceNames':[c for c in analysis['conflicts'] if c['field']=='activityName'],'evidenceRefs':refs(candidate['fields']['activityName']['evidenceIds'])}
    return domain.persist_resolution(db,actor,sub,run,analysis,candidate,values,products,unresolved,binding['intent'],target,target_revision,False,command_id,payload['analysisDigest'],no_eligible,basis,automatic=binding,date_evidence=date_evidence)
