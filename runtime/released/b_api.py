"""Test-v2 request routing and safe A projection; no changes to normal/demo."""
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import re
from urllib.parse import quote
from account_primitives import APIError,fail,iso,ROLES
from b_state import canon
from b_submission_declarations import (APPLICABLE_PLATFORMS,CONTRACT_VERSION,PLATFORM_CODES,PRODUCT_SCOPE_INTENTS,
                                       projected_declarations,version_platform)
from b_domain import Domain,ACTIONS,RATE,MODEL_START,MODEL_CANCEL,SUPPLEMENT,GROUP,fields,entity,material,material_view,can_material,can_version,owns_submission,submission_view,run_view,sub_revision
from b_domain import REPORT_BAD_CASE,agent_bad_cases,can_report_agent_bad_case,outstanding_agent_groups
from queries import Query,TYPES,enum,pagination,validate
import b_platform_materials as platforms
import b_activity_dates as dates
import b_registration_draft as drafts
import b_registration_review as registration_review
from b_activity_relations import project_data
from b_relation_query import RelationQueryMixin
import b_review_restart as restart_review
from b_domain import approval_requirements,grant_scopes
from b_brand_associations import BrandAssociations
from b_trial_margin import enrich_trial_margin
from b_sales_worker import SalesWorker
from storage import ENTITIES
from b_version_evidence import source_projection
from b_review_lists import ReviewLists,FILTER_KEYS
from b_workbench_structure import CATEGORIES, attach_coupons, dated_review, dimensions
import b_grouping
import b_shenjia_batch as shenjia_batch

AWAITING_ASSIGNMENT='awaiting_assignment'
ASSIGNMENT_BLOCKS=('ASSIGNMENT_REQUIRED','ASSIGNEE_ROLE_MISMATCH')
BEIJING=timezone(timedelta(hours=8))
SALES_COMPARISON=re.compile(r'/activities/([A-Za-z0-9_-]{1,128})/platforms/(meituan|taobao_instant)/sales-comparison\Z')


def activity_period(activity,clock):
    days=[]
    for field in ('startAt','endAt'):
        try:
            value=datetime.fromisoformat(activity[field].replace('Z','+00:00'))
            if value.tzinfo is None:return 'unknown'
            days.append(value.astimezone(BEIJING).date())
        except (ValueError,TypeError,KeyError,AttributeError,OverflowError):
            return 'unknown'
    start,end=days
    if start>end:return 'unknown'
    today=datetime.fromtimestamp(clock,BEIJING).date()
    return 'upcoming' if today<start else 'expired' if today>end else 'current'


KEEP_ACTIVITY={'id','name','type','platformCode','projectName','level','startAt','endAt','rawDate','submitter','submittedAt','externalIds','currentReviewVersionId','effectiveExecutionVersionId','displayVersionId','reviewVersionId','approvedVersionId','effectiveVersionId','pendingChangeVersionIds','revision','provenance','brandIds','ruleSummary','rules','brandBenefit','costEvidence','review','registration','execution','expiry','productApplicability','productListState','pendingItems','reviewState','reviewBlockedReason','activityMethod','activityMethodConfirmation'}

def load_data(db):
    from b_coupon_schedule import public_schedule
    data={table:[json.loads(r[0]) for r in db.execute('SELECT body FROM '+table+' ORDER BY id')] for table in ENTITIES}
    data['activity_brands']=[json.loads(row['body'])|{'activityId':row['activity_id'],'brandId':row['brand_id']}
                             for row in db.execute('SELECT activity_id,brand_id,body FROM activity_brands ORDER BY activity_id,brand_id')]
    data['activity_products']=[json.loads(r[0]) for r in db.execute('SELECT body FROM activity_products ORDER BY activity_id,product_id')]
    data['meta']={r[0]:json.loads(r[1]) for r in db.execute('SELECT key,value FROM metadata')}
    n=json.loads(db.execute("SELECT value FROM b_meta WHERE key='datasetCounter'").fetchone()[0]);data['meta']['datasetRevision']='jt021-test-v2:'+str(n)
    data=project_data(db,data)
    snapshots={version['id']:version.get('snapshot',{}) for version in data['activity_versions']}
    activities=[]
    for activity in data['activities']:
        display=snapshots.get(activity.get('displayVersionId'),{})
        declarations=projected_declarations(display if display else activity,version_platform(display) or activity.get('platformCode'))
        coupons=display.get('couponSchedule')
        activities.append({k:v for k,v in activity.items() if k in KEEP_ACTIVITY}
                          |{'materials':[],'history':[],'pendingItems':display.get('dateBackfill',{}).get('pendingItems',display.get('pendingItems',[]))}
                          |({'couponSchedule':public_schedule(coupons)} if coupons else {})|declarations|dimensions(activity,display))
    data['activities']=activities
    data['submissions']=[{k:v for k,v in s.items() if k in ['id','submitterId','submittedAt','processingState','activityId']} for s in data['submissions']]
    for s in db.execute("SELECT * FROM b_submissions WHERE state<>'deleted'"):
        data['submissions'].append({'id':s['id'],'submitterId':s['submitter_id'],'submittedAt':iso(s['received_at']),'processingState':s['state'],'activityId':s['activity_id']})
    safe=[]
    for v in data['activity_versions']:
        snapshot=v.get('snapshot',{})
        safe.append({k:value for k,value in v.items() if k in ['id','activityId','versionNo','submissionId','previousVersionId','basedOnVersionId','revision']}|{'snapshot':{k:value for k,value in snapshot.items() if k in ['rules','brandBenefit','costEvidence','productApplicability','productListState','pendingItems','reviewState','reviewBlockedReason']}|projected_declarations(snapshot,version_platform(snapshot))|dimensions({},snapshot),'materialRefs':[],'changeSummary':None})
    data['activity_versions']=safe;data['materials']=[];data['history']=[]
    return data

class BQuery(RelationQueryMixin,Query):
    def __init__(self,data,clock,incomplete):
        super().__init__(data,'demo',clock);self.incomplete=incomplete;self.meta.update(mode='test',warnings=['合成测试／仅本机记录／无外部执行','报名/销量/成本来源未接入；未知不按0'])
    def linked_brands(self,q):
        validate(q,{'q','hasActivities'})
        self.ready()
        readable_activities={item['id'] for item in self.data['activities']}
        linked={row['brandId'] for row in self.data['activity_brands'] if row['activityId'] in readable_activities}
        search=q.get('q','').strip().casefold()
        items=[self.brand_view(brand) for brand in self.data['brands']
               if brand['id'] in linked and search in brand['name'].casefold()]
        self.meta['brandActivityRelationsComplete']=self.data['meta'].get('brandActivityRelationsComplete') is True
        return pagination(items,q)

    def activities(self,q):
        if q.get('reviewResult')!=AWAITING_ASSIGNMENT or not hasattr(self,'awaiting_assignment_ids'):
            return super().activities(q)
        filters={key:value for key,value in q.items() if key!='reviewResult'}
        return [item for item in super().activities(filters) if item['id'] in self.awaiting_assignment_ids]

    def affected(self,q):
        return self._affected(q)
    def products(self,q):
        linked_only=enum(q,'hasActivities',{'yes'})=='yes'
        if linked_only:q={k:v for k,v in q.items() if k!='hasActivities'}|{'association':'linked'}
        if self.affected(q):self.meta['complete']=False
        result=super().products(q)
        if q.get('association')=='unlinked' and self.affected(q):fail(422,'INCOMPLETE_RELATIONS','当前范围商品关系未完整确认，不能判断未关联')
        for p in result:
            scope=q|({'brandId':p['brandId']} if p.get('brandId') else {})
            if self.affected(scope):
                self.meta['complete']=False;p.update(relationComplete=False,activityCount=None)
        if linked_only:
            # Canonical activity_products is the confirmed relation source; Agent
            # candidates and drafts never enter it. Scope/filter precedes grouping.
            grouped={}
            for product in result:
                code=product['internalCode']
                if code not in grouped:
                    grouped[code]=product|{'activities':list(product['activities'])}
                    continue
                previous=grouped[code]
                activities={item['id']:item for item in previous['activities']+product['activities']}
                previous['activities']=list(activities.values())
                previous['relationComplete']=previous['relationComplete'] and product['relationComplete']
                previous['activityCount']=len(activities) if previous['relationComplete'] else None
            result=list(grouped.values())
            self.meta['productActivityScope']='confirmed_linked'
        return result
    def detail(self,aid):
        result=super().detail(aid)
        if aid in self.incomplete:result.update(productCount=None,relationsComplete=False);self.meta['complete']=False
        return result
    def snapshot(self,q):
        if q.get('exportScope')=='confirmed_linked':
            return super().snapshot(q)
        bad=[q.get('activityId')] if q.get('kind')=='activity_products' and q.get('activityId') in self.incomplete else self.affected(q) if q.get('kind')=='product_activity' else []
        if any(self.incomplete.get(aid,{}).get('reasonCode')=='SCOPE_NOT_ENUMERATED' for aid in bad):fail(422,'SCOPE_NOT_ENUMERATED','已记录适用范围，尚未提供逐项SKU，不能伪全量',{'activityIds':bad})
        if bad:fail(422,'INCOMPLETE_RELATIONS','本次范围有未确认商品关系，不能假全量',{'activityIds':bad})
        return super().snapshot(q)

class Business:
    def __init__(self,auth,jobs,clock,*,require_activity_name=False,allow_submission_delete=False,allow_submission_declarations=False,model_recognition=None,primary_recognition=None,platform_materials=None,first_review_start=False,activity_date_decoder=None,review_contact=None,sales_worker=None):
        self.auth=auth;self.jobs=jobs;self.clock=clock;self.model_recognition=model_recognition;self.primary_recognition=primary_recognition
        self.domain=Domain(auth,jobs,clock,require_activity_name=require_activity_name,allow_submission_delete=allow_submission_delete,
                           allow_submission_declarations=allow_submission_declarations)
        self.platform_materials=platform_materials
        self.domain.primary_recognition=primary_recognition
        self.domain.platform_materials=platform_materials
        self.domain.first_review_start=first_review_start
        self.domain.activity_date_evidence=activity_date_decoder is not None
        self.domain.activity_date_decoder=activity_date_decoder
        self.review_contact=review_contact
        self.product_recognition=None
        self.supply_prices=None
        self.product_metrics=None
        self.sales_worker=sales_worker if sales_worker is not None else SalesWorker(
            Path(__file__).parent/'business_agent/activity_sales_comparison',None)
        self.shenjia_batch=shenjia_batch.ShenjiaBatchWorker(
            Path(__file__).parent/'business_agent/shenjia_batch',None)
        self.registration_drafts=drafts.RegistrationDrafts(self)
        self.brand_associations=BrandAssociations(self)
        self.review_lists=ReviewLists(auth,self.domain,self.decide_blocked)
    def decide_blocked(self,db,actor,node):
        try:
            review=db.execute('SELECT * FROM version_reviews WHERE version_id=?',(node['version_id'],)).fetchone()
            if not review or node['state']!='pending' or review['state']!=node['stage']+'_pending':
                fail(409,'STATE_CONFLICT','节点已不是当前待审步骤')
            self.domain.require_review_actor(db,actor,node,review['activity_id'])
            self.domain.require_current_review(db,actor,review,node)
            policy=self.domain.review_decision_policy(db,actor,review,node)
            if policy:fail(409,'POLICY_UNCONFIRMED',policy[1],{'reasonCode':policy[0]})
        except APIError as error:
            if error.status not in (403,409):raise
            return {'code':error.code,'message':error.message,
                    'reasonCode':error.details.get('reasonCode','ACTION_OR_SCOPE_DENIED' if error.status==403 else error.code)}
        return None
    def rating_action(self,db,actor,activity_id,permissions):
        if RATE not in permissions:
            return None
        try:
            activity=self.domain.rating_target(db,actor,activity_id)
        except APIError as error:
            if error.status!=403:raise
            return None
        return {'action':RATE,'revision':activity.get('revision',1)}

    def activity_review_action(self,db,actor,activity_id,permissions):
        if 'reviews.decide' not in permissions:
            return None
        activity=entity(db,'activities',activity_id)
        version_id=activity.get('reviewVersionId') or activity.get('currentReviewVersionId')
        if not version_id:
            return None
        node=db.execute(
            '''SELECT task.* FROM review_tasks task
               JOIN version_reviews review ON review.version_id=task.version_id
               WHERE review.activity_id=? AND review.version_id=?
                 AND task.state='pending' AND review.state=task.stage||'_pending' ''',
            (activity_id,version_id)).fetchone()
        if not node or not node['assignee_id'] or not can_version(db,actor,version_id,self.auth):
            return None
        if self.decide_blocked(db,actor,node):
            return None
        from b_coupon_review import review_projection
        version=entity(db,'activity_versions',version_id)
        needs_detail=review_projection(db,version,decisions=[],include_products=False) is not None
        return {'action':'reviews.decide','versionId':version_id} | ({'requiresCouponReview':True} if needs_detail else {})

    def date_activity(self,db,actor,value,*,detail=False):
        if not self.domain.activity_date_evidence:return value
        activity=entity(db,'activities',value['id'])
        allowed=lambda mid:can_material(db,actor,mid,self.auth)
        basis=activity.get('activityDateBasis')
        if basis is None and any(not activity.get(field) for field in dates.SIDES):
            read_version=lambda vid:can_version(db,actor,vid,self.auth)
            confirmed=dates.CONFIRMED_CASE if activity['id']==dates.CONFIRMED_CASE['activityId'] else None
            try:
                source=dates.archived(db,activity,can_read_version=read_version,
                                      can_read_material=allowed,confirmed_year=confirmed,verify_original=detail)
            except APIError as error:
                if error.code!=dates.SOURCE_MISSING:raise
                source=dates.archived(db,activity,can_read_version=read_version,can_read_material=allowed,
                                      verify_original=detail)
            basis=source['dateEvidence'] if source else None
        projection=dates.public_evidence(basis,allowed,detail=detail)
        if basis and projection.get('activityDateEvidence'):
            reasons=dates.overrides(activity,basis)
            if reasons and 'pendingItems' in value:
                visible={item['field']+'At' for item in projection['activityDateEvidence']['issues']}
                value=value|{'pendingItems':[item|{'reasonCode':reasons[item['field']]}
                            if item['field'] in reasons and item['field'] in visible else item
                            for item in value['pendingItems']]}
        return value|projection

    def product_activity_status(self, db, actor, activity, clock):
        progress = []
        if self.platform_materials:
            projected = self.platform_materials.project_activity(db, actor, activity)
            progress = [{key: item.get(key) for key in ('platformCode', 'state', 'lifecycleStage')}
                        for item in projected['platformProgress']]
        return {'activityPeriodState': activity_period(activity, clock), 'platformProgress': progress}

    def date_version(self,db,actor,aid,version,actions,blocked):
        if not self.domain.activity_date_evidence:return {}
        activity=entity(db,'activities',aid)
        snap=version.get('snapshot',{})
        layer=snap.get('dateBackfill',{})
        fields=snap.get('resolvedFields') or snap.get('confirmedFields') or {}
        value={'effectiveActivityDates':{key:layer.get('dates',{}).get(key,fields.get(key)) for key in dates.SIDES},
               'currentPendingItems':layer.get('pendingItems',snap.get('pendingItems',[]))}
        if activity.get('reviewVersionId',activity.get('currentReviewVersionId'))==version['id']:
            current=self.date_activity(db,actor,activity|{'pendingItems':value['currentPendingItems']},detail=True)
            value.update({key:current[key] for key in ('activityDateEvidence','registrationDateEvidence') if key in current})
            value['currentPendingItems']=current['pendingItems']
            try:
                dates.require_actor(self.domain,db,actor,aid,version['id'])
            except APIError as error:
                if error.status not in (403,404):raise
                return value
            offer,source=dates.plan(db,activity,version,can_read_material=lambda mid:can_material(db,actor,mid,self.auth))
            if offer['eligible']:
                try:
                    conflict,_=dates.duplicate_identity(self.domain,db,actor,aid,source['values']|source['updates'])
                    reason='DUPLICATE_ACTIVITY' if conflict else None
                except APIError as error:
                    if error.status!=409:raise
                    reason=error.code
                if reason:offer.update(eligible=False,evidenceDigest=None,blockedReasons=[{'code':reason}])
            value['dateBackfill']=offer
            if offer['eligible']:actions.append(dates.ACTION)
            else:blocked.extend({'action':dates.ACTION,**reason} for reason in offer['blockedReasons'])
        return value

    def registration_version(self,db,actor,aid,vid):
        return self.version(db,actor,aid,vid,review_actions=False)

    def version(self,db,actor,aid,vid,*,review_actions=True):
        value=entity(db,'activity_versions',vid)
        if value['activityId']!=aid or not can_version(db,actor,vid,self.auth):fail(404,'NOT_FOUND','版本不存在或不可访问')
        sources=[{'submissionId':s['submission_id'],'inputRevision':s['input_revision'],'materialIds':json.loads(s['material_ids'])} for s in db.execute('SELECT * FROM version_sources WHERE version_id=?',(vid,))]
        nodes=[dict(r) for r in db.execute('SELECT * FROM review_tasks WHERE version_id=? ORDER BY CASE stage WHEN \'initial\' THEN 0 WHEN \'pricing\' THEN 1 ELSE 2 END',(vid,))]
        review=self.domain.review_view(db,vid)
        decisions=[json.loads(r[0]) for r in db.execute('SELECT payload FROM review_decisions WHERE version_id=? ORDER BY rowid',(vid,))]
        actions=[];blocked=[]
        if review:
            for node in nodes:
                if node['state']=='pending' and (node['assignee_id']==actor['id'] or self.domain.is_review_admin(actor)):
                    reason=self.decide_blocked(db,actor,node)
                    if reason:blocked.append(reason|{'action':'reviews.decide','nodeId':node['id']})
                    else:actions.append('reviews.decide')
            if review_actions and review['state']=='not_started':
                if self.domain.first_review_start:
                    activity=entity(db,'activities',aid)
                    record=db.execute('SELECT * FROM version_reviews WHERE version_id=?',(vid,)).fetchone()
                    _,reason=self.domain.review_start_target(db,actor,activity,value,record)
                    if reason:
                        code='POLICY_UNCONFIRMED' if reason in ('ASSIGNEE_SELF_REVIEW','ASSIGNEE_SAME_INITIAL_FINAL') else reason
                        blocked.append({'action':'reviews.start','code':code,'reasonCode':reason})
                    else:actions.append('reviews.start')
                else:
                    reason=review.get('reviewBlockedReason')
                    if reason:blocked.append({'code':reason})
                    else:blocked.append({'code':'POLICY_UNCONFIRMED','policy':'resubmission_or_effective_change'})
        snapshot=value.get('snapshot',{})
        supplement=snapshot.get('supplementBinding')
        extra=self.date_version(db,actor,aid,value,actions,blocked)
        extra['reviewRestart']=restart_review.project_restart(self.domain,db,actor,entity(db,'activities',aid),value)
        if self.review_contact and review:
            pending_node=next((node for node in nodes if node['stage']==review['stage'] and node['state']=='pending'),None)
            extra['reviewCoordination']={'businessContact':self.review_contact,'assignmentAvailable':False,
                'currentAssigneeId':pending_node['assignee_id'] if pending_node else None,
                'nextAction':'reviews.start' if review['state']=='not_started' else 'reviews.decide' if pending_node else None}
        if supplement:
            base=entity(db,'activity_versions',supplement['baseVersionId'])
            binding=supplement['source']
            original=[{'submissionId':r['submission_id'],'inputRevision':r['input_revision'],'materialIds':json.loads(r['material_ids'])}
                      for r in db.execute('SELECT * FROM version_sources WHERE version_id=? AND submission_id=? AND input_revision=?',
                                          (base['id'],binding['submissionId'],binding['inputRevision']))]
            projection=source_projection(db,actor,base,original,self.auth) if can_version(db,actor,base['id'],self.auth) else source_projection(db,actor,value,[],self.auth)
            for source in projection['sourceInputs']:
                source['analyses']=[a for a in source['analyses'] if a['candidateId']==binding['candidateId']
                                    and a['runId']==binding['analysisRunId']]
            ids={r for source in projection['sourceInputs'] for analysis in source['analyses'] for r in analysis['evidenceReferenceIds']}
            projection['evidence']=[e for e in projection['evidence'] if e['referenceId'] in ids]
            extra['supplementMaterials']=self.supplement_materials(db,actor,supplement)
        else:
            projection=source_projection(db,actor,restart_review.projection_version(db,value),sources,self.auth)
            latest=self.domain.latest_supplement(db,aid,vid)
            if latest and can_version(db,actor,latest['id'],self.auth):
                extra['latestSupplement']={'versionId':latest['id'],'pendingItems':latest['snapshot']['pendingItems'],
                                           'reviewState':self.domain.review_view(db,latest['id'])['state']}
        visible_materials={m['materialId']:m for source in projection['sourceInputs'] for m in source['materials'] if m['available']}
        visible_materials.update({m['materialId']:m for m in extra.get('supplementMaterials',[]) if m['available']})
        from b_coupon_review import review_projection,store_bearing_requirement
        coupons=dated_review(review_projection(db,value,decisions,visible_material_ids=set(visible_materials),
                                              review_state=review['state'] if review else None),self.clock())
        if coupons is not None:
            extra['couponReview']=coupons
        events=[json.loads(r[0]) for r in db.execute('SELECT payload FROM b_history WHERE version_id=? AND activity_id=? ORDER BY rowid',(vid,aid))]
        for event in events:
            if event.get('event')=='activity.dates_backfilled':
                event['details'].pop('basis',None)
        snapshot={key:val for key,val in snapshot.items() if key not in ('activityDateBasis','dateBackfill')}
        # Show source-bound notes only via couponReview, after verifying the viewer and tier.
        for binding_key,rows_key in (('couponReviewInput','options'),('mixedCouponReview','options'),
                                     ('couponSchedule','tiers'),('manualCorrections','couponTiers')):
            binding=snapshot.get(binding_key) or {}
            if rows_key not in binding:
                continue
            visible_rows=[]
            for tier in binding[rows_key]:
                ref=tier.get('source') or tier.get('sourceReference') or {}
                mid=ref.get('materialId') or binding.get('materialId')
                if not mid or mid in visible_materials:
                    visible_rows.append({k:v for k,v in tier.items() if k not in ('fundingNote','submitterFundingNote')})
            snapshot[binding_key]=binding | {rows_key:visible_rows}
        if 'subsidyPrices' in snapshot:
            snapshot['subsidyPrices']=[row for row in snapshot['subsidyPrices']
                                      if row['source']['materialId'] in visible_materials]
        extra['reviewApprovalRequirements']=[]
        if review and review['stage']:
            requirements=approval_requirements(snapshot,review['stage'],review['approvalMode']=='single_management')
            store=store_bearing_requirement(coupons)
            extra['reviewApprovalRequirements']=([store] if store else [])+requirements
        if 'primaryRecognitionBinding' in snapshot:
            binding=snapshot['primaryRecognitionBinding']
            snapshot['primaryRecognitionBinding']={k:binding[k] for k in ('attemptId','recognitionDigest','groupId','candidateIds','inputRevision','inputDigest','analysisDigest','promptVersion','model','confirmedBy','confirmedAt') if k in binding}
        if self.product_recognition:
            extra['productRecognition']=self.product_recognition.view(db,value,projection)
            if self.supply_prices:
                extra['productRecognition']=self.supply_prices.enrich(extra['productRecognition'])
            if self.product_metrics:
                codes=projected_declarations(snapshot,version_platform(snapshot))['applicablePlatformCodes']
                extra['productRecognition']=self.product_metrics.enrich(extra['productRecognition'], codes)
            extra['productRecognition']=enrich_trial_margin(extra['productRecognition'])
        return value|projection|extra|{'snapshot':snapshot|projected_declarations(snapshot,version_platform(snapshot))|dimensions({},snapshot),
            'review':review,'nodes':nodes,'decisions':decisions,'sources':sources,'materials':list(visible_materials.values()),
            'allowedActions':actions,'blockedReasons':blocked,'history':events}
    def supplement_materials(self,db,actor,binding):
        out=[]
        for ref in binding['materialRefs']:
            mid=ref['materialId']
            row=db.execute('SELECT * FROM material_blobs WHERE id=?',(mid,)).fetchone()
            if not row or not can_material(db,actor,mid,self.auth):
                out.append({'materialId':mid,'available':False,'reasonCode':'MATERIAL_UNAVAILABLE'})
            elif row['sha256']!=ref['sha256'] or hashlib.sha256(row['content']).hexdigest()!=ref['sha256']:
                out.append({'materialId':mid,'available':False,'reasonCode':'MATERIAL_HASH_MISMATCH'})
            else:
                out.append(material_view(row)|{'available':True,'downloadAvailable':can_material(db,actor,mid,self.auth,True)})
        return out

    def profile_read(self,db,actor,path,q):
        self.auth.require(db,actor,'accounts.manage');parts=path.strip('/').split('/')
        if len(parts)==1:
            validate(q,{'q','role','page','pageSize'})
            role=q.get('role','all')
            if role not in ['all','unassigned',*ROLES]:fail(422,'INVALID_FILTER','角色筛选无效')
            rows=db.execute('SELECT * FROM account ORDER BY username').fetchall();items=[]
            for row in rows:
                profile=self.domain.profile(db,row['id'])
                if q.get('q','').casefold() not in ((row['name'] or '')+' '+row['username']+' '+(profile['phone'] or '')).casefold():continue
                if role!='all' and row['business_role']!=(None if role=='unassigned' else role):continue
                items.append(profile)
            return pagination(items,q)
        if len(parts)!=2:fail(404,'NOT_FOUND','人员路由不存在')
        validate(q,set());return self.domain.profile(db,parts[1])
    def sales_comparison_request(self,db,actor,aid,code,q):
        validate(q,{'dateBasis','storeScope'})
        if q.get('dateBasis')!='approved_plan' or q.get('storeScope')!='all_platform':
            fail(422,'INVALID_REQUEST','请选择按提报时间试算，并明确按当前平台统计')
        if not self.platform_materials:
            fail(404,'NOT_FOUND','当前活动没有平台复盘')
        activity=self.platform_materials.authorize(db,actor,aid,code)
        status,_,version,_,material=self.platform_materials.summary(db,actor,activity,code)
        if not version or not material or not can_version(db,actor,version['id'],self.auth):
            fail(404,'NOT_FOUND','当前活动没有可查看的平台复盘')
        if status['lifecycleStage']!='retrospective':
            fail(409,'NOT_READY','活动尚未进入复盘')
        # Platform totals are an explicit request, not a fallback for missing store scope.
        self.auth.require(db,actor,'operations.read','*')
        frozen=registration_review.saved(db,aid,version['id'],code)
        rows=frozen.get('finalRows',[]) if frozen and frozen.get('status')=='finalized' else []
        internal_codes=sorted({row['internalCode'] for row in rows
                               if isinstance(row.get('internalCode'),str) and row['internalCode']})
        def day(field):
            try:
                value=datetime.fromisoformat(activity[field].replace('Z','+00:00'))
                return value.astimezone(BEIJING).date() if value.tzinfo else None
            except (KeyError,ValueError,TypeError,AttributeError,OverflowError):
                return None
        start,end=day('startAt'),day('endAt')
        request={'activityId':aid,'activityVersionId':version['id'],'platformCode':code,
                 'dateBasis':'approved_plan','startDate':start.isoformat() if start else None,
                 'endDate':end.isoformat() if end else None,
                 'scope':{'internalCodes':internal_codes,'allPlatformStores':True}}
        today=datetime.fromtimestamp(self.clock(),BEIJING).date()
        if start and end and start<=today-timedelta(days=1)<end:
            request['previewThrough']=(today-timedelta(days=1)).isoformat()
        elif start and start>=today:
            fail(409,'NO_COMPLETED_DAYS','活动尚无完整自然日可供试算')
        return request,today

    def read(self,sid,path,q,handler):
        batch_preview=re.fullmatch(r'/materials/([A-Za-z0-9_-]{1,128})/shenjia-preview',path)
        if batch_preview:
            validate(q,set())
            with self.auth.tx() as db:
                _,actor=self.auth.valid(db,sid,True)
                handler.trace_actor=actor['id']
                source=shenjia_batch.owned_source(db,actor,self.auth,batch_preview[1])
            return shenjia_batch.preview(self.shenjia_batch,source)
        if path=='/products/sales-history':
            request=self.read_scoped(sid,path,q,handler)
            today=datetime.fromtimestamp(self.clock(),BEIJING).date()
            return self.sales_worker.query('history',request,today)
        comparison=SALES_COMPARISON.fullmatch(path)
        if comparison:
            with self.auth.tx() as db:
                _,actor=self.auth.valid(db,sid,True)
                handler.trace_actor=actor['id']
                request,today=self.sales_comparison_request(db,actor,*comparison.groups(),q)
            result=self.sales_worker.query('compare',request,today)
            result['registrationProductCount']=len(request['scope']['internalCodes'])
            return result
        result=self.read_scoped(sid,path,q,handler)
        # Worker status probes do not need the database transaction held by read_scoped.
        if path in ('/catalogs','/capabilities') and getattr(self.jobs,'model_first',False):
            result['agentFirstRecognition']=self.jobs.capability()
        if path=='/catalogs':
            if self.model_recognition is not None:
                result['modelRecognition']=self.model_recognition.capability()
            if self.primary_recognition is not None:
                result['primaryRecognition']=self.primary_recognition.capability()
        return result

    def read_scoped(self,sid,path,q,handler):
        with self.auth.tx() as db:
            _,actor=self.auth.valid(db,sid,True);handler.trace_actor=actor['id'];permissions=self.auth.permissions(db,actor)
            if re.fullmatch('/submissions/[^/]+/correction-products',path):
                from b_intake_corrections import company_products
                validate(q,{'identifier'})
                owns_submission(db,actor,path.split('/')[2],self.auth,True)
                self.auth.require(db,actor,'activities.propose')
                self.auth.require(db,actor,'operations.read')
                if '*' not in grant_scopes(db,actor,self.auth,'operations.read'):
                    fail(403,'FORBIDDEN','当前账号无权查询完整公司商品目录')
                return company_products(self.product_recognition,q.get('identifier'))
            if path in ['/reviews','/submissions']:
                metadata={r[0]:json.loads(r[1]) for r in db.execute('SELECT key,value FROM metadata')}
                counter=json.loads(db.execute("SELECT value FROM b_meta WHERE key='datasetCounter'").fetchone()[0])
                handler.meta={'mode':'test','dataOrigin':metadata['dataOrigin'],'datasetRevision':'jt021-test-v2:'+str(counter),'fetchedAt':iso(self.clock()),'complete':bool(metadata['relationsComplete']),'warnings':['合成测试／仅本机记录／无外部执行','报名/销量/成本来源未接入；未知不按0']}
                return self.review_lists.query(db,actor,q,mine_only=path=='/submissions')
            enum(q,'platformCode',PLATFORM_CODES)
            data=load_data(db);query=BQuery(data,self.clock(),{r[0]:json.loads(r[1]) for r in db.execute('SELECT * FROM incomplete_relations')});handler.meta=query.meta
            scopes=grant_scopes(db,actor,self.auth,'operations.read')
            grants={'*'} if '*' in scopes else {kind+':'+item['id'] for table,kind in [('activities','activity'),('brands','brand'),('products','product')] for item in data[table] if item['id'] in scopes or kind+':'+item['id'] in scopes}
            readable_versions={v['id'] for v in data['activity_versions'] if can_version(db,actor,v['id'],self.auth)}
            detail_match = re.fullmatch(r'/activities/([^/]+)', path)
            needs_tiers = (detail_match or path in ('/activities','/products','/exports/snapshot')
                           or path.startswith(('/brands','/products/')) and path!='/products/sales-history'
                           or re.fullmatch(r'/activities/[^/]+/products',path))
            ambiguous = set()
            if needs_tiers:
                owners = {}
                for product in data['products']:
                    upc, code = product.get('upc'), product.get('internalCode') or product['id']
                    if upc and owners.setdefault(upc, code) != code:
                        ambiguous.add(upc)
            query.scoped(grants,readable_version_ids=readable_versions)
            if needs_tiers:
                attach_coupons(db,data,query.clock,can_read_material=lambda mid:can_material(db,actor,mid,self.auth),
                               ambiguous_upcs=ambiguous,detail_id=detail_match[1] if detail_match else None,
                               with_product_rows=path not in ('/activities','/brands'))
            if path=='/products/sales-history':
                self.auth.require(db,actor,'operations.read')
                validate(q,{'platformCode','window','codes','activityId','versionId'})
                platform=enum(q,'platformCode',{'meituan','taobao_instant'})
                window=enum(q,'window',{'trailing_28_days','trailing_28_days_compare','last_3_complete_months'})
                try:
                    codes=json.loads(q.get('codes','null'))
                except (ValueError,RecursionError):
                    codes=None
                if (not platform or not window or not isinstance(codes,list) or not 1<=len(codes)<=25
                        or any(not isinstance(code,str) or not code or len(code)>128 for code in codes)
                        or len(set(codes))!=len(codes)):
                    fail(422,'INVALID_REQUEST','请选择查询平台、时间范围和关联商品')
                activity_id, version_id = q.get('activityId'), q.get('versionId')
                if activity_id is not None or version_id is not None:
                    if not activity_id or not version_id or window != 'trailing_28_days_compare':
                        fail(422,'INVALID_REQUEST','请指定活动、版本及近四周环比窗口')
                    self.auth.require(db,actor,'operations.read',activity_id)
                    version = self.registration_version(db,actor,activity_id,version_id)
                    if platform not in registration_review.platforms(version):
                        fail(404,'NOT_FOUND','该版本的适用平台不可访问')
                    recognition = version.get('productRecognition') or {}
                    visible = {
                        row['candidates'][0]['internalCode'] for row in recognition.get('rows', [])
                        if recognition.get('status') == 'ready' and row.get('uniqueUpcHit') is True
                        and len(row.get('candidates', [])) == 1
                    }
                else:
                    visible={p['internalCode'] for p in query.products({'platformCode':platform,'hasActivities':'yes'})}
                if not set(codes)<=visible:
                    fail(404,'NOT_FOUND','当前平台的活动商品不存在或不可访问')
                return {'schemaVersion':'activity-sales-history/1.0','platformCode':platform,'window':window,
                        'scope':{'internalCodes':codes,'allPlatformStores':True}}
            final_route=re.fullmatch(r'/activities/([^/]+)/versions/([^/]+)/registration-drafts/(meituan|taobao_instant|jd)/final-list',path)
            if final_route:
                validate(q,set())
                raw,name=registration_review.download(self.registration_drafts,db,actor,*final_route.groups())
                handler.binary_response=(raw,{'Content-Type':'text/csv; charset=utf-8','Content-Disposition':"attachment; filename*=UTF-8''"+quote(name,safe='')})
                return None
            draft_route=drafts.ROUTE.fullmatch(path)
            if draft_route:
                validate(q,set())
                return self.registration_drafts.read(db,actor,*draft_route.groups())
            if self.platform_materials and platforms.ROUTE.fullmatch(path):
                return self.platform_materials.read(db,actor,path,q,handler)
            if path.startswith('/commands/'):
                validate(q,{'action'})
                action=q.get('action')
                if (action not in ACTIONS and action not in (drafts.ACTION,drafts.ROW_ACTION,registration_review.SCOPE_ACTION,'analysis.primary_recognition.start','analysis.primary_recognition.cancel') and not (self.platform_materials and action in platforms.ACTIONS)
                        and not (self.domain.activity_date_evidence and action==dates.ACTION)
                        or (action=='submissions.delete' and not self.domain.allow_submission_delete)
                        or (action in (MODEL_START,MODEL_CANCEL) and self.model_recognition is None)):
                    fail(422,'INVALID_REQUEST','回执action无效')
                row=db.execute('SELECT * FROM commands WHERE actor_id=? AND action=? AND idem_key=?',(actor['id'],q['action'],path[len('/commands/'):])).fetchone()
                if not row:return {'state':'not_found','commandId':None,'resourceIds':[],'recordedAt':None}
                self.check_receipt(db,actor,row)
                response=json.loads(row['response']);ids=[response[k] for k in ['materialId','submissionId','runId','attemptId','activityId','versionId','baseVersionId','supplementVersionId','decisionId','historyEventId','platformMaterialId','approvedVersionId'] if response.get(k)]
                if response.get('user'):ids.append(response['user']['id'])
                return {'state':'accepted' if row['status']==202 else 'committed','commandId':row['id'],'resourceIds':list(dict.fromkeys(ids)),'response':response,'recordedAt':iso(row['recorded_at'])}
            if path.startswith('/account-users'):
                fail(404,'NOT_FOUND','请使用account/users路由')
            if re.fullmatch('/submissions/[^/]+',path):
                validate(q,set());sid_=path.split('/')[2];row=owns_submission(db,actor,sid_,self.auth)
                result=submission_view(db,row,actor,self.auth,model_recognition=self.model_recognition,primary_recognition=self.primary_recognition)
                current_run=next((run for run in reversed(result['runs']) if run['inputRevision']==row['revision']),None)
                cases=agent_bad_cases(db,sid_)
                if cases:result['agentBadCases']=cases
                if current_run:
                    current_cases=[case for case in cases if case['analysisRunId']==current_run['runId']]
                    if row['state']=='failed' and current_cases:
                        result['agentBadCase']=current_cases[-1]
                    if 'submissions.edit_own' in result['allowedActions'] and can_report_agent_bad_case(row,current_run):
                        candidates=current_run['result']['candidates']
                        allowed,_=outstanding_agent_groups(db,row,current_run['runId'],candidates)
                        if allowed:
                            result['allowedActions'].append(REPORT_BAD_CASE)
                            result['reportableBadCaseCandidateIds']=allowed
                if self.primary_recognition:
                    for view in result.get('runs',[]):self.primary_recognition.project(db,actor,view,row)
                from b_manual_name_confirmation import project as project_manual_name
                for view in result.get('runs',[]):project_manual_name(self.domain,db,actor,view,row)
                if self.product_recognition:
                    result['productRecognition']=self.product_recognition.submission_view(
                        db,actor,sid_,self.auth,master_catalog=query.editable_catalogs())
                if self.domain.allow_submission_delete:
                    blocked=self.domain.submission_delete_block(db,actor,row)
                    if blocked:result['blockedReasons'].append({'action':'submissions.delete',**blocked})
                    else:result['allowedActions'].append('submissions.delete')
                return result
            primary_preview=re.fullmatch(r'/analysis-runs/([^/]+)/primary-recognition/preview',path)
            if primary_preview and self.primary_recognition:
                validate(q,set())
                return self.primary_recognition.preview(db,actor,primary_preview[1])
            preview=re.fullmatch(r'/analysis-runs/([^/]+)/model-recognition/preview',path)
            if preview and self.model_recognition is not None:
                validate(q,set())
                return self.model_recognition.preview(db,actor,preview[1])
            if re.fullmatch('/analysis-runs/[^/]+',path):
                validate(q,set());rid=path.split('/')[2];row=db.execute('SELECT * FROM analysis_runs WHERE id=?',(rid,)).fetchone()
                if not row:fail(404,'NOT_FOUND','分析不存在')
                sub=owns_submission(db,actor,row['submission_id'],self.auth)
                view=run_view(row,include_model=self.model_recognition is not None,include_primary=self.primary_recognition is not None)
                if self.model_recognition is not None:
                    self.model_recognition.project(db,actor,view,sub)
                if self.primary_recognition:self.primary_recognition.project(db,actor,view,sub)
                b_grouping.project(db,actor,self.auth,view)
                from b_manual_name_confirmation import project as project_manual_name
                project_manual_name(self.domain,db,actor,view,sub)
                return view
            if path.startswith('/materials/'):
                validate(q,set());parts=path.strip('/').split('/');mid=parts[1];download=len(parts)==3 and parts[2]=='download'
                if len(parts)>3 or len(parts)==3 and not download:fail(404,'NOT_FOUND','材料路由不存在')
                if not can_material(db,actor,mid,self.auth,download):fail(404,'MATERIAL_UNAVAILABLE','原件不存在或不可访问')
                row=material(db,mid)
                if download:
                    handler.binary_response=(row['content'],{'Content-Type':row['media_type'],'Content-Disposition':"attachment; filename*=UTF-8''"+quote(row['filename'].replace('\\','/').split('/')[-1],safe='')})
                    return None
                return material_view(row)
            match=re.fullmatch(r'/activities/([^/]+)/versions/([^/]+)(/history)?',path)
            if match:
                value=self.version(db,actor,match[1],match[2])
                if match[3]:validate(q,set());return pagination(value['history'],q)
                validate(q,set());return value
            if path=='/context':
                validate(q,set());return {'mode':'test','authenticated':True,'readOnly':not any(p in permissions for p in ['accounts.manage','submissions.create','reviews.decide',RATE]),'businessReadOnly':not any(p in permissions for p in ['submissions.create','reviews.decide','activities.propose',RATE]),'actor':self.auth.user(actor),'permissions':permissions,'dataOrigin':'synthetic_test','datasetRevision':query.meta['datasetRevision'],'capabilities':{'contractVersion':'1.1','allowedActions':permissions}}
            if path=='/capabilities':
                validate(q,set());return {**({'activityRecognitionMode':'local'} if getattr(self.jobs,'local_only',False) else {}),'contractVersion':'1.1','reviewListContractVersion':'reviews/1.1','reviewFilterKeys':FILTER_KEYS,'autoResolution':{'contractVersion':'auto-resolution/1.0','ruleVersion':'grouped-intake-auto/1.0','enabled':self.primary_recognition is None and not getattr(self.jobs,'local_only',False)},'allowedActions':permissions,'fullAccessAdministrator':self.domain.is_review_admin(actor),'readOnly':not any(p in permissions for p in ['accounts.manage','submissions.create','reviews.decide',RATE]),'businessReadOnly':not any(p in permissions for p in ['submissions.create','reviews.decide','activities.propose',RATE]),'implemented':['activities','brands','products','details','exports.snapshot','accounts.profiles','materials','submissions','analysis','confirm','reviews','commands'],'blockedReasons':{'externalExecution':'NOT_CONNECTED','sms':'SMS_NOT_CONFIGURED','changedVersionStart':'POLICY_UNCONFIRMED'},'authMethod':'password','groupReview':{'contractVersion':'intake-group-review/2.0','supplements':True,'groupingDecisions':b_grouping.available() and not getattr(self.jobs,'model_first',False),'localAgentVersion':b_grouping.CURRENT_ENGINE}}
            if path=='/brands' or path.startswith('/brands/'):
                return self.brand_associations.read(db,actor,path,q,data,handler)
            self.auth.require(db,actor,'operations.read')
            if path=='/exports/snapshot':self.auth.require(db,actor,'operations.download')
            if path=='/catalogs':
                validate(q,set())
                activity_platforms=[{'code':code,'label':label} for code,label in APPLICABLE_PLATFORMS]
                catalogs={'activityTypes':TYPES,'initiatorTypes':TYPES,
                          'activityCategories':[{'code':code,'label':label} for code,label in CATEGORIES.items()],'platforms':activity_platforms,'brands':query.editable_catalogs()['brands'],
                          'roles':[{'code':k,'label':v} for k,v in ROLES.items()]}
                if self.domain.allow_submission_declarations:
                    catalogs.update(submissionDeclarationsContractVersion=CONTRACT_VERSION,
                                    applicablePlatforms=activity_platforms,
                                    productScopeIntents=[{'code':code,'label':label} for code,label in PRODUCT_SCOPE_INTENTS])
                if getattr(self.jobs, 'local_only', False):
                    catalogs['activityRecognitionMode'] = 'local'
                return catalogs
            if path=='/brands' and q.get('hasActivities')=='yes':
                result=query.linked_brands(q)
                memo={}
                for brand in result['items']:
                    for item in brand['activities']:
                        if item['id'] not in memo:memo[item['id']]=self.date_activity(db,actor,item)
                    brand['activities']=[memo[item['id']] for item in brand['activities']]
                return result
            if path=='/activities' and q.get('reviewResult')==AWAITING_ASSIGNMENT and self.domain.activity_date_evidence:
                query.awaiting_assignment_ids={row[0] for row in db.execute(
                    '''SELECT r.activity_id
                       FROM version_reviews r
                       JOIN activities a ON a.id=r.activity_id
                       JOIN activity_versions v ON v.id=r.version_id
                       WHERE r.version_id=COALESCE(json_extract(a.body,'$.reviewVersionId'),
                                                   json_extract(a.body,'$.currentReviewVersionId'))
                         AND json_extract(v.body,'$.activityId')=r.activity_id
                         AND r.state='not_started'
                         AND json_extract(v.body,'$.snapshot.reviewBlockedReason') IN (?,?)''',ASSIGNMENT_BLOCKS)}
            if path=='/products' or path=='/exports/snapshot' and q.get('kind')=='product_activity':
                status_cache={}

                def status_for(activity):
                    aid=activity['id']
                    if aid not in status_cache:
                        status_cache[aid]=self.product_activity_status(db,actor,activity,query.clock)
                    return status_cache[aid]

                query.product_status=status_for
            if path=='/activities' and self.platform_materials:
                filters={k:v for k,v in q.items() if k!='platformProgressState'}
                progress=enum(q,'platformProgressState',platforms.STATES.union(platforms.LIFECYCLE_ORDER))
                query.ready()
                activities=[self.platform_materials.project_activity(db,actor,item) for item in query.activities(filters)]
                if progress:
                    activities=[item for item in activities if progress in (item.get('workflowStatus'),item.get('lifecycleStage'))
                                or any(progress in (p['state'],p['lifecycleStage']) for p in item.get('platformProgress',[]))]
                value=pagination(activities,q)
            else:
                value=query.route(path,q)
            if path=='/activities':
                for item in value['items']:
                    item['reviewAction']=self.activity_review_action(db,actor,item['id'],permissions)
                    item['ratingAction']=self.rating_action(db,actor,item['id'],permissions)
                value['items']=[self.date_activity(db,actor,item) for item in value['items']]
            if re.fullmatch('/brands/[^/]+',path):
                value['activities']=[self.date_activity(db,actor,item) for item in value['activities']]
                if self.domain.activity_date_evidence:
                    query.meta['brandActivityRelationsComplete']=data['meta'].get('brandActivityRelationsComplete') is True
                    value['activities']=[item|{'activityPeriodState':activity_period(item,query.clock)}
                                         for item in value['activities']]
            if re.fullmatch('/brands/[^/]+/activities',path):
                value['items']=[self.date_activity(db,actor,item) for item in value['items']]
                if self.domain.activity_date_evidence:
                    query.meta['brandActivityRelationsComplete']=data['meta'].get('brandActivityRelationsComplete') is True
                    value['items']=[item|{'activityPeriodState':activity_period(item,query.clock)} for item in value['items']]
            if path=='/products':
                for item in value['items']:
                    item['activities']=[activity|status_for(activity) for activity in item['activities']]
                if q.get('platformCode') and any(query.incomplete[aid].get('productListState')=='scope_only' for aid in query.affected(q)):
                    if self.domain.activity_date_evidence:value['observedTotal']=value['total']
                    value.update(total=None,productListState='scope_only')
            if re.fullmatch('/products/[^/]+/activities',path):
                product=query.get('products',path.split('/')[2]);scope=q|({'brandId':product['brandId']} if product.get('brandId') else {})
                if query.affected(scope):
                    value.update(relationComplete=False,relationState='incomplete');query.meta['complete']=False
                    if any(query.incomplete[aid].get('productListState')=='scope_only' for aid in query.affected(scope)):
                        if self.domain.activity_date_evidence:value['observedTotal']=value['total']
                        value.update(total=None,productListState='scope_only')
            if re.fullmatch('/activities/[^/]+/products',path) and path.split('/')[2] in query.incomplete:
                value['total']=None;value['relationsComplete']=False
                scope=query.incomplete[path.split('/')[2]]
                if scope.get('productListState')=='scope_only':value.update(productListState='scope_only',productApplicability=entity(db,'activities',path.split('/')[2]).get('productApplicability'))
            if re.fullmatch('/activities/[^/]+',path):
                if self.platform_materials:value=self.platform_materials.project_activity(db,actor,value)
                aid=path.split('/')[2];value['authorizedVersions']=[{'versionId':v['id'],'versionNo':v.get('versionNo')} for v in data['activity_versions'] if v['activityId']==aid and can_version(db,actor,v['id'],self.auth)]
                for version in value['versions']:
                    if can_version(db,actor,version['id'],self.auth):version['authorizedDetailUrl']='/api/test/v1/operations/activities/'+aid+'/versions/'+version['id']
                value=self.date_activity(db,actor,value,detail=True)
                value['reviewAction']=self.activity_review_action(db,actor,aid,permissions)
                value['ratingAction']=self.rating_action(db,actor,aid,permissions)
            return value
    def check_receipt(self,db,actor,row):
        action=row['action'];resource=row['resource_id']
        if action in (drafts.ACTION,drafts.ROW_ACTION,registration_review.SCOPE_ACTION):return drafts.receipt(self.auth,db,actor,row)
        if self.domain.activity_date_evidence and action==dates.ACTION:
            version=entity(db,'activity_versions',resource)
            dates.require_actor(self.domain,db,actor,version['activityId'],resource)
            return
        if self.platform_materials and action in platforms.ACTIONS:
            return self.platform_materials.check_receipt(db,actor,row)
        if action=='reviews.restart':
            version=entity(db,'activity_versions',resource)
            restart_review.authorize(self.domain,db,actor,version['activityId'],resource)
            return
        if action=='reviews.coupon_selection':
            version=entity(db,'activity_versions',resource)
            stage=json.loads(row['response']).get('reviewStage','initial')
            self.domain.coupon_reviewer(db,actor,version['activityId'],resource,stage)
            return
        if action==SUPPLEMENT:
            version=entity(db,'activity_versions',resource)
            self.domain.supplement_target(db,actor,version['activityId'],resource)
            return
        if action==RATE:
            self.domain.rating_target(db,actor,resource)
            return
        if action=='accounts.profile.update':self.auth.require(db,actor,'accounts.manage');return
        if action=='materials.upload':self.auth.require(db,actor,'submissions.create');return
        if action==shenjia_batch.ACTION:
            shenjia_batch.owned_source(db,actor,self.auth,resource)
            return
        if action=='submissions.create':self.auth.require(db,actor,'submissions.create');return
        if action=='submissions.delete' and self.domain.allow_submission_delete:
            owns_submission(db,actor,resource,self.auth,True,include_deleted=True)
            return
        if action in ['submissions.update',REPORT_BAD_CASE,'analysis.start','submissions.confirm','submissions.auto_resolve',GROUP]:owns_submission(db,actor,resource,self.auth,True)
        elif action in ('analysis.cancel',MODEL_START,MODEL_CANCEL,'analysis.primary_recognition.start','analysis.primary_recognition.cancel'):
            run=db.execute('SELECT submission_id FROM analysis_runs WHERE id=?',(resource,)).fetchone()
            if not run:fail(404,'NOT_FOUND','分析不存在')
            owns_submission(db,actor,run[0],self.auth,True)
        elif action=='reviews.start' and self.domain.first_review_start:
            version=entity(db,'activity_versions',resource)
            self.domain.require_first_review_actor(db,actor,version)
            if not can_version(db,actor,resource,self.auth):fail(403,'FORBIDDEN','回执访问权失效')
        elif action=='reviews.start' and db.execute('SELECT 1 FROM b_submissions WHERE version_id=? AND submitter_id=?',(resource,actor['id'])).fetchone():
            self.auth.require(db,actor,'activities.propose')
        else:
            self.auth.require(db,actor,'reviews.decide','assigned')
            if not can_version(db,actor,resource,self.auth):fail(403,'FORBIDDEN','回执访问权失效')
        if action in ['submissions.confirm','submissions.auto_resolve',GROUP]:self.auth.require(db,actor,'activities.propose')
    def write(self,sid,path,method,body,key):
        domain=self.domain;parts=path.strip('/').split('/');operation=None;action=None
        if path=='/submissions/shenjia-batch' and method=='POST':
            shenjia_batch.check_body(body)
            with self.auth.tx() as db:
                _,actor=self.auth.valid(db,sid,True)
                source=shenjia_batch.owned_source(db,actor,self.auth,body['sourceMaterialId'])
                replay=db.execute('SELECT 1 FROM commands WHERE actor_id=? AND action=? AND idem_key=?',
                                  (actor['id'],shenjia_batch.ACTION,key)).fetchone()
            if source['sha256']!=body['expected']['sourceSha256']:
                fail(409,'SHENJIA_BATCH_CHANGED','原件已变化，请重新预览。')
            parsed=None if replay else shenjia_batch.read_result(
                self.shenjia_batch.split(source,'split'),source['sha256'],with_files=True)
            return domain.command(sid,method,path,shenjia_batch.ACTION,key,body,
                lambda db,actor,_:shenjia_batch.save(domain,db,actor,source,body['expected'],parsed))
        coupon_route=re.fullmatch(r'/activities/([^/]+)/versions/([^/]+)/coupon-selection',path)
        if coupon_route and method=='PUT':
            return domain.command(sid,method,path,'reviews.coupon_selection',key,body,
                lambda db,actor,command_id:domain.save_coupon_selection(db,actor,*coupon_route.groups(),body))
        scope_route=re.fullmatch(r'/activities/([^/]+)/versions/([^/]+)/registration-drafts/(meituan|taobao_instant|jd)/final-list/store-scope',path)
        if scope_route and method=='POST':
            return domain.command(sid,method,path,registration_review.SCOPE_ACTION,key,body,
                lambda db,actor,command_id:registration_review.confirm_scope(self.registration_drafts,db,actor,*scope_route.groups(),body))
        verify_route=platforms.VERIFY.fullmatch(path)
        if self.platform_materials and verify_route and method=='POST':
            action=platforms.RECHECK_ACTION if isinstance(body,dict) and body.get('checkStage')=='recheck' else platforms.VERIFY_ACTION
            return domain.command(sid,method,path,action,key,body,
                lambda db,actor,command_id:self.platform_materials.verify(db,actor,*verify_route.groups(),body,command_id))
        row_route=drafts.ROW_ROUTE.fullmatch(path)
        if row_route and method=='POST':
            return domain.command(sid,method,path,drafts.ROW_ACTION,key,body,
                lambda db,actor,command_id:self.registration_drafts.decide(db,actor,*row_route.groups(),body))
        draft_route=drafts.ROUTE.fullmatch(path)
        if draft_route and method=='POST':
            return domain.command(sid,method,path,drafts.ACTION,key,body,
                lambda db,actor,command_id:self.registration_drafts.save(db,actor,*draft_route.groups(),body))
        platform_path=platforms.SUBMIT.fullmatch(path)
        if self.platform_materials and platform_path and method=='POST':
            aid,code=platform_path.groups()
            with self.auth.tx() as db:
                _,actor=self.auth.valid(db,sid,True)
                activity=self.platform_materials.authorize(db,actor,aid,code,write=True,selected=False)
                replay=db.execute('SELECT 1 FROM commands WHERE actor_id=? AND action=? AND idem_key=?',
                                  (actor['id'],platforms.ACTION,key)).fetchone()
                if not replay:
                    version,_,_=self.platform_materials.basis(db,activity,code)
                    if version and not can_version(db,actor,version['id'],self.auth):
                        fail(403,'FORBIDDEN','缺少绑定审核版读取权限')
            prepared=None if replay else self.platform_materials.prepare(body)
            def submit(db,actor,command_id):
                return self.platform_materials.submit(db,actor,aid,code,body,command_id,prepared)
            return domain.command(sid,method,path,platforms.ACTION,key,body,submit)
        primary_path=re.fullmatch(r'/analysis-runs/([^/]+)/primary-recognition(/cancel)?',path)
        if self.primary_recognition is not None and primary_path and method=='POST':
            if getattr(self.jobs,'model_first',False):fail(409,'USE_MODEL_FIRST_INTAKE','请从本提报直接交给 Agent 识别，不再使用规则识别后的二次入口')
            return self.write_model(sid,path,body,key,primary_path[1],bool(primary_path[2]),primary=True)
        model_path=re.fullmatch(r'/analysis-runs/([^/]+)/model-recognition(/cancel)?',path)
        if self.model_recognition is not None and model_path and method=='POST':
            return self.write_model(sid,path,body,key,model_path[1],bool(model_path[2]))
        if self.domain.activity_date_evidence and re.fullmatch('/activities/[^/]+/versions/[^/]+/date-backfill',path) and method=='POST':
            action=dates.ACTION;operation=lambda db,actor,command_id:dates.submit(domain,db,actor,parts[1],parts[3],body)
        elif re.fullmatch('/activities/[^/]+/rating',path) and method=='POST':
            action=RATE
            operation=lambda db,actor,command_id:domain.rate_activity(db,actor,parts[1],body,command_id)
        elif path=='/materials' and method=='POST':
            from b_domain import upload_payload,add_material
            action='materials.upload'
            with self.auth.tx() as db:
                _,actor=self.auth.valid(db,sid,True)
                domain.authorize_command(db,actor,action,path,body)
            if 'checkOnly' in body:
                if body['checkOnly'] is not True:
                    fail(422,'INVALID_REQUEST','本机读取检查参数无效。')
                from b_jobs import preview_xlsx_readiness
                upload=upload_payload({key:value for key,value in body.items() if key!='checkOnly'})
                return 200,preview_xlsx_readiness(upload),False
            upload=upload_payload(body)
            operation=lambda db,actor,command_id:(201,add_material(db,actor,self.clock(),*upload))
        elif path=='/submissions' and method=='POST':action='submissions.create';operation=lambda db,actor,command_id:domain.create_submission(db,actor,body)
        elif self.domain.allow_submission_delete and re.fullmatch('/submissions/[^/]+/delete',path) and method=='POST':
            action='submissions.delete';operation=lambda db,actor,command_id:domain.delete_submission(db,actor,parts[1],body)
        elif re.fullmatch('/submissions/[^/]+/bad-case',path) and method=='POST':
            action=REPORT_BAD_CASE;operation=lambda db,actor,command_id:domain.report_agent_bad_case(db,actor,parts[1],body)
        elif re.fullmatch('/submissions/[^/]+/draft',path) and method=='PUT':action='submissions.update';operation=lambda db,actor,command_id:domain.update_submission(db,actor,parts[1],body)
        elif re.fullmatch('/submissions/[^/]+/analysis-runs',path) and method=='POST':action='analysis.start';operation=lambda db,actor,command_id:domain.start_analysis(db,actor,parts[1],body)
        elif re.fullmatch('/analysis-runs/[^/]+/cancel',path) and method=='POST':action='analysis.cancel';operation=lambda db,actor,command_id:domain.cancel_analysis(db,actor,parts[1],body)
        elif re.fullmatch('/submissions/[^/]+/auto-resolve',path) and method=='POST':action='submissions.auto_resolve';operation=lambda db,actor,command_id:domain.auto_resolve(db,actor,parts[1],body,command_id)
        elif re.fullmatch('/submissions/[^/]+/grouping-decisions',path) and method=='POST':
            if getattr(self.jobs,'model_first',False):fail(409,'USE_MODEL_FIRST_INTAKE','请交给 Agent 识别分组，不再使用本地规则重分组')
            action=GROUP;operation=lambda db,actor,command_id:b_grouping.accept(domain,db,actor,parts[1],body,command_id)
        elif re.fullmatch('/submissions/[^/]+/confirm',path) and method=='POST':action='submissions.confirm';operation=lambda db,actor,command_id:domain.confirm(db,actor,parts[1],body,command_id,recognition=self.product_recognition)
        elif re.fullmatch('/users/[^/]+/profile',path) and method=='PUT':action='accounts.profile.update';operation=lambda db,actor,command_id:domain.edit_profile(db,actor,parts[1],body)
        elif re.fullmatch('/activities/[^/]+/versions/[^/]+/supplements',path) and method=='POST':
            action=SUPPLEMENT;operation=lambda db,actor,command_id:domain.supplement(db,actor,parts[1],parts[3],body,command_id)
        elif re.fullmatch('/activities/[^/]+/versions/[^/]+/restart-review',path) and method=='POST':
            action='reviews.restart';operation=lambda db,actor,command_id:restart_review.restart(domain,self,db,actor,parts[1],parts[3],body,command_id)
        elif re.fullmatch('/activities/[^/]+/versions/[^/]+/decisions',path) and method=='POST':action='reviews.decide';operation=lambda db,actor,command_id:domain.decide(db,actor,parts[1],parts[3],body)
        elif re.fullmatch('/activities/[^/]+/versions/[^/]+/start-review',path) and method=='POST':action='reviews.start';operation=lambda db,actor,command_id:domain.start_review(db,actor,parts[1],parts[3],body)
        if not operation:fail(405,'METHOD_NOT_ALLOWED','本契约不开放该写方法/路由')
        result = domain.command(sid,method,path,action,key,body,operation)
        if action == 'submissions.delete' and self.product_recognition:
            self.product_recognition.remove_submission_cache(parts[1])
        return result

    def write_model(self,sid,path,body,key,run_id,cancel,primary=False):
        model=self.primary_recognition if primary else self.model_recognition
        action=('analysis.primary_recognition.cancel' if cancel else 'analysis.primary_recognition.start') if primary else MODEL_CANCEL if cancel else MODEL_START

        def operation(db,actor,command_id):
            if cancel:
                return model.cancel(db,actor,run_id,body)
            return model.start(db,actor,run_id,body,command_id)

        with model.command_lock:
            status,result,replay=self.domain.command(sid,'POST',path,action,key,body,operation)
            if not replay:
                if cancel:
                    if result['status']=='cancelled':
                        model.stop(result['attemptId'])
                else:
                    model.enqueue(sid,run_id,result['attemptId'],body)
        return status,result,replay
