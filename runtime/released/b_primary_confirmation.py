"""Confirm primary extraction without rewriting the deterministic analysis or hashes."""
import copy
import json
from account_primitives import fail,iso
from b_state import canon,fingerprint
from b_domain import fields,sub_revision,verify_declaration_binding,entity,put,history


def confirmable_candidate_ids(group,recognition,analysis):
    if group.get('candidateIds'):return group['candidateIds'], 'model_anchors'
    candidates=analysis.get('candidates',[])
    if len(recognition.get('groups',[]))==1 and len(candidates)==1 and not candidates[0].get('productRows'):
        # Only bookkeeping for one model activity with no product rows to assign.
        # The original analysis and the model's empty candidateIds remain intact.
        return [candidates[0]['candidateId']], 'single_activity_without_product_rows'
    return [], 'unbound'


def confirmation_context(domain,db,actor,sub,run,payload,body):
    binding=body.get('primaryRecognitionBinding')
    fields(binding,['attemptId','recognitionDigest','groupId'])
    value=payload.get('primaryRecognition') or {}
    if (value.get('status')!='succeeded' or value.get('attemptId')!=binding['attemptId']
            or payload.get('primaryRecognitionDigest')!=binding['recognitionDigest']):
        fail(409,'PRIMARY_RECOGNITION_REQUIRED','请完成当前修订的主识别，再核对后建档')
    view={'runId':run['id'],'inputRevision':run['input_revision'],'inputDigest':run['input_digest'],
          'catalogRevision':run['catalog_revision'],**payload}
    recognition=domain.primary_recognition.projected_suggestions(db,actor,view,value,value.get('recognition'),binding['recognitionDigest'])
    if recognition is None:fail(409,'PRIMARY_RECOGNITION_STALE','主识别依据已失效，请刷新核对')
    group=next((g for g in recognition['groups'] if g['groupId']==binding['groupId']),None)
    ids,mode=confirmable_candidate_ids(group,recognition,payload['result']) if group else ([], 'unbound')
    if not group or body['candidateId'] not in ids:
        fail(422,'PRIMARY_GROUP_INVALID','识别分组不属于当前确认对象')
    if mode=='model_anchors' and any(sum(cid in g['candidateIds'] for g in recognition['groups'])!=1 for cid in ids):
        fail(409,'PRIMARY_GROUP_SCOPE_AMBIGUOUS','商品所属主活动尚未核实，请按独立活动补充来源；不能跨组复制商品')
    analysis=payload['result'];source=sub_revision(db,sub['id'],sub['revision'])
    verify_declaration_binding(db,sub,run,analysis,payload['analysisDigest'],source)
    originals=[c for c in analysis['candidates'] if c['candidateId'] in ids]
    selected=copy.deepcopy(next(c for c in originals if c['candidateId']==body['candidateId']))
    rows=[row for c in originals for row in c['productRows']]
    if len({r['rowId'] for r in rows})!=len(rows):
        fail(409,'PRIMARY_GROUP_SCOPE_AMBIGUOUS','原商品行编号重复，请先核实商品归属')
    selected['productRows']=copy.deepcopy(rows)
    bearing=[c['fields']['companyBearing'] for c in originals]
    if len({canon(c['value']) for c in bearing})==1:
        selected['fields']['companyBearing']['evidenceIds']=list(dict.fromkeys(e for c in bearing for e in c['evidenceIds']))
    else:
        selected['fields']['companyBearing']={'value':{'state':'unknown','evidenceIds':[]},'status':'conflict','evidenceIds':list(dict.fromkeys(e for c in bearing for e in c['evidenceIds']))}
    identity=group.get('identity')
    confirmed=body['confirmedFields']
    if identity and identity.get('kind')=='shen_jia':
        if confirmed.get('platformCode')!=identity['platformCode'] or confirmed.get('sourceActivityId')!=identity['sourceActivityId']:
            fail(409,'PRIMARY_IDENTITY_CONFLICT','确认的平台或神价主ID与已核实原文不一致，请先补正材料')
    if identity and body['intent']=='create':
        for row in db.execute('SELECT body FROM activities'):
            activity=json.loads(row[0])
            if any(ext.get('platformCode')==identity['platformCode'] and ext.get('id')==identity['sourceActivityId'] for ext in activity.get('externalIds',[])):
                domain.auth.require(db,actor,'operations.read',activity['id'])
                fail(409,'DUPLICATE_ACTIVITY','此主活动已经存在，请关联来源或提交该活动的补充版本',{'activityId':activity['id'],'revision':activity['revision']})
    return selected,dict(binding,contractVersion=value['contractVersion'],runId=run['id'],inputRevision=run['input_revision'],
        inputDigest=run['input_digest'],analysisDigest=payload['analysisDigest'],candidateIds=ids,candidateBindingMode=mode,
        model=value['model'],promptVersion=value['promptVersion'],group=group,
        evidence=[e for e in recognition['evidence'] if e['evidenceId'] in group['evidenceIds']],originalCandidates=originals)


def record_confirmation(domain,db,actor,sub,run,analysis,context,outcome):
    status,result=outcome
    version=entity(db,'activity_versions',result['versionId'])
    if result['outcome']!='linked':
        version['snapshot']['primaryRecognitionBinding']=context|{'confirmedBy':actor['id'],'confirmedAt':iso(domain.clock())}
        manual=version['snapshot'].get('manualConfirmation')
        if manual:
            manual['originalCandidate']=next(c for c in context['originalCandidates'] if c['candidateId']==result['candidateId'])
        put(db,'activity_versions',version)
    original=db.execute('SELECT payload FROM resolutions WHERE submission_id=? AND input_revision=? AND candidate_id=?',
                        (sub['id'],sub['revision'],result['candidateId'])).fetchone()
    value=json.loads(original[0])|{'primaryRecognitionBinding':{k:context[k] for k in ('attemptId','recognitionDigest','groupId','candidateIds')}}
    for cid in context['candidateIds']:
        prior=db.execute('SELECT activity_id,version_id FROM resolutions WHERE submission_id=? AND input_revision=? AND candidate_id=?',
                         (sub['id'],sub['revision'],cid)).fetchone()
        if prior and tuple(prior)!=(result['activityId'],result['versionId']):
            fail(409,'PRIMARY_GROUP_ALREADY_RESOLVED','本组中的来源已关联其他活动版本')
        db.execute('INSERT INTO resolutions VALUES(?,?,?,?,?,?) ON CONFLICT(submission_id,input_revision,candidate_id) DO UPDATE SET payload=excluded.payload',
                   (sub['id'],sub['revision'],cid,result['activityId'],result['versionId'],canon(value|{'candidateId':cid})))
    done={r[0] for r in db.execute('SELECT candidate_id FROM resolutions WHERE submission_id=? AND input_revision=?',(sub['id'],sub['revision']))}
    remaining=[c['candidateId'] for c in analysis['candidates'] if c['candidateId'] not in done]
    state='partially_resolved' if remaining else 'resolved'
    db.execute('UPDATE b_submissions SET state=? WHERE id=?',(state,sub['id']))
    history(db,actor,domain.clock(),activity=result['activityId'],version=result['versionId'],submission=sub['id'],event='activity.primary_recognition_confirmed',details={k:context[k] for k in ('runId','attemptId','recognitionDigest','groupId','candidateIds')})
    return status,result|{'state':state,'remainingGroups':remaining,'primaryRecognitionBinding':value['primaryRecognitionBinding']}
