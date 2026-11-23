"""reviews/1.1: permission-first safe summaries, filters and stable paging."""
from datetime import datetime
import json
from account_primitives import APIError,fail,iso
from b_domain import can_version
from b_state import canon
from queries import pagination,validate

STATES={'received','analyzing','needs_input','needs_confirmation','partially_resolved','resolved','failed'}
FILTER_KEYS=['q','brandQ','stage','result','state']
SUMMARY_FIELDS=['activityName','brands','startAt','endAt']


def object_row(db,table,id):
    row=db.execute('SELECT body FROM '+table+' WHERE id=?',(id,)).fetchone()
    if row is None:return None
    return json.loads(row[0])


def timestamp(value):
    try:
        moment=datetime.fromisoformat(value.replace('Z','+00:00'))
        return moment.timestamp() if moment.tzinfo is not None else float('-inf')
    except (AttributeError,ValueError,TypeError):return float('-inf')


class ReviewLists:
    def __init__(self,auth,domain,decide_blocked):
        self.auth=auth;self.domain=domain;self.decide_blocked=decide_blocked
    def parameters(self,q,mine_only):
        validate(q,{'view',*FILTER_KEYS})
        view=q.get('view','mine' if mine_only else 'pending')
        if view not in ['pending','handled','mine'] or mine_only and view!='mine':fail(422,'INVALID_FILTER','列表视角无效')
        result={'view':view}
        for key in ['q','brandQ']:
            value=q.get(key,'')
            if not isinstance(value,str):fail(422,'INVALID_FILTER','搜索条件必须是字符串')
            result[key]=value.strip().casefold()
        allowed={'stage':{'initial','pricing','final','platform_verification','platform_recheck'},'result':{'approve','reject'},'state':STATES}
        applicable={'stage':view!='mine','result':view=='handled','state':view=='mine'}
        for key,values in allowed.items():
            value=q.get(key)
            if value in [None,'','all']:result[key]=None;continue
            if not isinstance(value,str) or value not in values or not applicable[key]:fail(422,'INVALID_FILTER','筛选条件或适用Tab无效')
            result[key]=value
        return result
    def receipt(self,db,sid):
        sub=db.execute('SELECT submitter_id,received_at,state FROM b_submissions WHERE id=?',(sid,)).fetchone()
        if sub is None:return {'submitter':None,'submittedAt':None,'processingState':None}
        name=None
        for stored in db.execute('SELECT payload FROM b_history WHERE submission_id=? ORDER BY rowid',(sid,)):
            event=json.loads(stored[0])
            if event.get('event')=='submission.received' and event.get('actorId')==sub['submitter_id']:
                name=event.get('nameSnapshot');break
        return {'submitter':{'id':sub['submitter_id'],'nameSnapshot':name},'submittedAt':iso(sub['received_at']),'processingState':sub['state']}
    def brands(self,db,ids):
        if not isinstance(ids,list):return None
        brands=[]
        for bid in ids:
            if not isinstance(bid,str):return None
            brand=object_row(db,'brands',bid)
            brands.append({'id':bid,'name':brand.get('name') if brand else None})
        return brands
    def fields(self,db,values):
        return {'activityName':values.get('activityName') or None,'brands':self.brands(db,values.get('brandIds')),'startAt':values.get('startAt') or None,'endAt':values.get('endAt') or None}
    def finish(self,item,*,node=False,decision=False):
        missing=[field for field in SUMMARY_FIELDS if item.get(field) is None]
        submitter=item.get('submitter')
        if not submitter or submitter.get('id') is None:missing.append('submitter.id')
        if not submitter or submitter.get('nameSnapshot') is None:missing.append('submitter.nameSnapshot')
        if item.get('submittedAt') is None:missing.append('submittedAt')
        if node and item.get('stage') is None:missing.append('stage')
        if decision and item.get('nodeResult') is None:missing.append('nodeResult')
        if decision and item.get('decisionAt') is None:missing.append('decisionAt')
        if item.get('versionId') and item.get('versionNo') is None:missing.append('versionNo')
        if item.get('brands') is not None:
            missing.extend('brands['+str(i)+'].name' for i,b in enumerate(item['brands']) if b.get('name') is None)
        item['summaryMissingFields']=list(dict.fromkeys(missing))
        return item
    def version_summary(self,db,version):
        snapshot=version.get('snapshot',{});confirmed=snapshot.get('resolvedFields',snapshot.get('confirmedFields',{}))
        if not isinstance(confirmed,dict):confirmed={}
        result=self.fields(db,confirmed)
        result.update(activityId=version['activityId'],submissionId=version.get('submissionId'),versionId=version['id'],versionNo=version.get('versionNo'),stage=None,nodeResult=None,decisionAt=None,opinion=None)
        result.update(self.receipt(db,version.get('submissionId')))
        notices=[]
        bearing=confirmed.get('companyBearing')
        if isinstance(bearing,dict) and bearing.get('state')=='unknown':notices.append('公司承担待核实')
        verification=snapshot.get('resolutionBinding',snapshot.get('manualConfirmation',{})).get('bearingVerification',{})
        if verification.get('reasonCode')=='BEARING_EVIDENCE_UNVERIFIED':notices.append('承担声明依据待核实')
        if snapshot.get('productListState')=='scope_only':notices.append('商品适用范围已记录，逐项SKU未枚举')
        if snapshot.get('unresolvedRows'):notices.append('存在未确认商品关系')
        candidate=snapshot.get('manualConfirmation',{}).get('originalCandidate',{})
        if any(f.get('status')=='conflict' for f in candidate.get('fields',{}).values()):notices.append('原识别字段曾有冲突')
        result['attentionSummary']='；'.join(notices) or None
        return result
    def input_summary(self,db,sub):
        stored=db.execute('SELECT payload FROM submission_revisions WHERE submission_id=? AND revision=?',(sub['id'],sub['revision'])).fetchone()
        payload=json.loads(stored[0]) if stored else {};form=payload.get('form',{})
        if not isinstance(form,dict):form={}
        run=db.execute('SELECT state,payload FROM analysis_runs WHERE submission_id=? AND input_revision=? ORDER BY created_at DESC,rowid DESC LIMIT 1',(sub['id'],sub['revision'])).fetchone()
        run_payload=json.loads(run['payload']) if run else {};analysis=run_payload.get('result') if run and run['state'] in ['succeeded','partial'] else None
        candidates=analysis.get('candidates',[]) if isinstance(analysis,dict) else []
        values={};conflict=False
        for field in ['activityName','brandIds','startAt','endAt']:
            possibilities=[];ambiguous=False
            if field in form and form[field] is not None and form[field]!='':possibilities.append(form[field])
            for candidate in candidates:
                value=candidate.get('fields',{}).get(field,{})
                if value.get('status')=='conflict':ambiguous=True;conflict=True
                elif value.get('status')=='explicit' and value.get('value') is not None:possibilities.append(value['value'])
                elif len(candidates)>1:ambiguous=True
            if field=='brandIds':
                normal=lambda value:canon(sorted(value)) if isinstance(value,list) else canon(value)
            else:normal=canon
            unique={normal(v) for v in possibilities}
            values[field]=possibilities[0] if len(unique)==1 and not ambiguous else None
        summary=self.fields(db,values);notices=[]
        if not stored:notices.append('当前原始修订未记录')
        if len(candidates)>1:notices.append('多个活动组待分别核对')
        if conflict:notices.append('识别字段存在冲突')
        if any(d.get('status') in ['unsupported','invalid','partial'] for d in run_payload.get('materialResults',[])):notices.append('存在未完整解析的材料')
        summary['attentionSummary']='；'.join(notices) or None
        return summary
    def mine_summary(self,db,actor,sub):
        latest=db.execute('SELECT id,state FROM analysis_runs WHERE submission_id=? ORDER BY created_at DESC,rowid DESC LIMIT 1',(sub['id'],)).fetchone()
        item={'rowKey':sub['id'],'submissionId':sub['id'],'submitterId':sub['submitter_id'],'submittedAt':iso(sub['received_at']),'revision':sub['revision'],'state':sub['state'],'activityId':sub['activity_id'],'versionId':sub['version_id'],'latestRun':dict(latest) if latest else None,'versionNo':None,'stage':None,'nodeResult':None,'decisionAt':None,'opinion':None,'processingState':sub['state']}
        item.update(self.receipt(db,sub['id']))
        versions=db.execute('SELECT DISTINCT version_id FROM resolutions WHERE submission_id=? ORDER BY version_id',(sub['id'],)).fetchall()
        if versions:
            summaries=[]
            for row in versions:
                if not can_version(db,actor,row['version_id'],self.auth):summaries.append(None);continue
                version=object_row(db,'activity_versions',row['version_id'])
                summaries.append(self.version_summary(db,version) if version else None)
            for field in SUMMARY_FIELDS:
                values=[s.get(field) if s else None for s in summaries]
                normal=lambda v:canon(sorted(v,key=lambda b:b['id'])) if field=='brands' and isinstance(v,list) else canon(v)
                item[field]=values[0] if len({normal(v) for v in values})==1 else None
            if len(versions)==1 and summaries[0] is not None:
                item['versionNo']=summaries[0]['versionNo']
                review=self.domain.review_view(db,versions[0]['version_id']);item['stage']=review.get('stage') if review else None
            notices=[s['attentionSummary'] for s in summaries if s and s.get('attentionSummary')]
            if len(versions)>1:notices.append('本提报有多个已处理版本，请进入处理区核对')
            if any(s is None for s in summaries):notices.append('关联版本摘要当前不可用')
            item['attentionSummary']='；'.join(dict.fromkeys(notices)) or None
        else:item.update(self.input_summary(db,sub))
        state_notice={'needs_input':'待补充输入','needs_confirmation':'待核对识别结果','partially_resolved':'仍有活动组待处理','failed':'本次处理失败，待查看原因'}.get(sub['state'])
        if state_notice:item['attentionSummary']='；'.join(v for v in [item.get('attentionSummary'),state_notice] if v)
        return self.finish(item)
    def matches(self,item,filters):
        visible=[item.get('activityName'),item.get('activityId')]
        if filters['view']=='mine':visible.append(item.get('submissionId'))
        if filters['q'] and not any(filters['q'] in v.casefold() for v in visible if isinstance(v,str)):return False
        if filters['brandQ'] and not any(filters['brandQ'] in b['name'].casefold() for b in item.get('brands') or [] if isinstance(b.get('name'),str)):return False
        for key,field in [('stage','stage'),('result','nodeResult'),('state','processingState')]:
            if filters[key] is not None and filters[key]!=item.get(field):return False
        return True
    def query(self,db,actor,q,*,mine_only=False):
        filters=self.parameters(q,mine_only);view=filters['view']
        self.auth.require(db,actor,'submissions.read_own' if view=='mine' else 'reviews.read','own' if view=='mine' else 'assigned')
        items=[]
        if view=='mine':
            submissions={sub['id']:sub for sub in db.execute(
                "SELECT * FROM b_submissions WHERE submitter_id=? AND state<>'deleted'",(actor['id'],))}
            for sub in submissions.values():
                items.append(self.mine_summary(db,actor,sub))
        elif view=='pending':
            if self.domain.is_review_admin(actor):
                pending=db.execute("SELECT * FROM review_tasks WHERE state='pending'")
            else:
                pending=db.execute("SELECT * FROM review_tasks WHERE assignee_id=? AND state='pending'",(actor['id'],))
            for node in pending:
                if self.decide_blocked(db,actor,node):continue
                version=object_row(db,'activity_versions',node['version_id'])
                if version is None:fail(503,'LIST_SUMMARY_UNAVAILABLE','授权节点的版本摘要暂不可用')
                if not can_version(db,actor,node['version_id'],self.auth):continue
                item=self.version_summary(db,version);item.update(rowKey=node['id'],stage=node['stage'],node=dict(node),review=self.domain.review_view(db,node['version_id']),allowedActions=['reviews.decide'])
                items.append(self.finish(item,node=True))
        else:
            for record in db.execute('SELECT * FROM review_decisions WHERE actor_id=?',(actor['id'],)):
                version=object_row(db,'activity_versions',record['version_id'])
                if version is None:fail(503,'LIST_SUMMARY_UNAVAILABLE','本人决定的版本摘要暂不可用')
                if not can_version(db,actor,record['version_id'],self.auth):continue
                decision=json.loads(record['payload']);node=db.execute('SELECT stage FROM review_tasks WHERE id=? AND version_id=?',(record['node_id'],record['version_id'])).fetchone()
                opinion=[]
                for value in ([decision.get('reason'),decision.get('comment')] if decision.get('decision')=='reject' else [decision.get('comment')]):
                    if isinstance(value,str) and value.strip() and value.strip() not in opinion:opinion.append(value.strip())
                item=self.version_summary(db,version);item.update(rowKey=record['id'],stage=node['stage'] if node else None,nodeResult=decision.get('decision'),decisionAt=decision.get('at'),opinion='；'.join(opinion) or None,decision=decision)
                items.append(self.finish(item,node=True,decision=True))
        if view=='pending' and self.domain.is_review_admin(actor) and self.domain.platform_materials:
            pm=self.domain.platform_materials
            for record in db.execute('SELECT body FROM activities'):
                activity=json.loads(record[0]);vid=activity.get('approvedVersionId')
                if not vid or not can_version(db,actor,vid,self.auth):continue
                try:
                    projected=pm.project_activity(db,actor,activity)
                    for platform in projected.get('platformProgress',[]):
                        recheck='platform_materials.recheck' in platform['allowedActions']
                        if not recheck and 'platform_materials.verify' not in platform['allowedActions']:continue
                        code=platform['platformCode'];pm.require_verifier(db,actor,activity['id'],code)
                        version=object_row(db,'activity_versions',vid)
                        item=self.version_summary(db,version)
                        item.update(rowKey='platform:'+activity['id']+':'+vid+':'+code,
                            stage='platform_recheck' if recheck else 'platform_verification',
                            attentionSummary='核实平台活动ID、上线状态和商品范围',
                            platformReview={'activityId':activity['id'],'versionId':vid,'platformCode':code},
                            allowedActions=['platform_materials.recheck' if recheck else 'platform_materials.verify'])
                        items.append(self.finish(item,node=True))
                except APIError as error:
                    if error.status not in (403,404):raise
        items=[item for item in items if self.matches(item,filters)]
        if view=='pending':items.sort(key=lambda item:item['rowKey'])
        elif view=='handled':items.sort(key=lambda item:(-timestamp(item['decisionAt']),item['rowKey']))
        else:items.sort(key=lambda item:(-timestamp(item['submittedAt']),item['submissionId']))
        page=pagination(items,q)
        if view=='mine' and getattr(self.domain,'allow_submission_delete',False):
            for item in page['items']:
                blocked=self.domain.submission_delete_block(db,actor,submissions[item['submissionId']])
                item['allowedActions']=[] if blocked else ['submissions.delete']
        return page
