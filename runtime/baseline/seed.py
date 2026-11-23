#!/usr/bin/env python3
"""Offline synthetic demo seed only; no accounts or business writes via HTTP."""
import argparse
import hashlib
import json
from storage import prepare_db

SOURCE='seed.py:synthetic-fixtures-r2'
PROVENANCE={'kind':'demo','sourceRef':SOURCE,'locator':'synthetic-only','observedAt':None}
def metric():return {'value':None,'unit':None,'periodStart':None,'periodEnd':None,'definitionRef':None,'sourceRef':None,'fetchedAt':None,'quality':'not_connected'}
def dataset():
    brands=[{'id':f'DEMO-B{i:03}','name':name,'benefit':benefit,'provenance':PROVENANCE} for i,(name,benefit) in enumerate([('冷酸灵','品牌供货折让3%（合成条件）'),('洁伶','服务补贴600元/月（合成条件）'),('七度空间','供货折让3%（合成条件）'),('蓝月亮','供货折让2%（合成条件）'),('样例无活动品牌',None)],1)]
    specs=[('DEMO-A001','冷酸灵11月三方混补','brand',1,'taobao_instant','initial','pending','满69减35等7档','2026-11-05','2026-11-30'),('DEMO-A002','洁伶11月三方混补','brand',2,'taobao_instant','pricing','pending','满19减10；满39减20','2026-11-06','2026-11-30'),('DEMO-A003','恒安价探满19减10','brand',3,'meituan',None,'approved','满19减10','2026-11-01','2026-11-30'),('DEMO-A004','个护家清神价（直营）','platform',4,'meituan','initial','pending','活动价9.90元','2026-11-10','2026-11-30'),('DEMO-A005','个护家清神价（代理）','platform',4,'meituan','final','pending','活动价10.90元','2026-11-10','2026-11-30'),('DEMO-A006','冷酸灵11月爆好价','platform',1,'taobao_instant',None,'approved','活动价12.90元＋品牌券2元','2026-11-08','2026-11-30'),('DEMO-A007','冬季品牌合作招募','jiatan_sourcing',1,'jiatan','initial','pending','合作条件待核实','2026-12-01','2026-12-31'),('DEMO-H001','10月淘闪满减','brand',1,'taobao_instant',None,'approved','历史材料样例','2026-10-01','2026-10-31')]
    activities=[];ab=[];versions=[];submissions=[]
    for i,(aid,name,typ,bid,platform,stage,result,rule,start,end) in enumerate(specs,1):
        vid=aid+'-V1';sid=f'DEMO-F{i:03}'
        a={'id':aid,'name':name,'type':typ,'platformCode':platform,'projectName':None,'level':None,'startAt':start+'T00:00:00+08:00','endAt':end+'T23:59:59+08:00','rawDate':None,'submitter':{'id':f'DEMO-U{i:03}','displayName':'合成提报人'+str(i)},'submittedAt':'2026-11-02T02:00:00Z','externalIds':[],'currentReviewVersionId':vid,'effectiveExecutionVersionId':None,'revision':1,'provenance':PROVENANCE,'brandIds':[f'DEMO-B{bid:03}'],'ruleSummary':rule,'rules':rule,'brandBenefit':brands[bid-1]['benefit'],'costEvidence':{'value':None,'unit':None,'sourceRef':None,'status':'unconfirmed'},'review':{'stage':stage,'result':result,'approvalMode':'demo' if result=='approved' else None},'registration':{'state':'unknown','sourceRef':None,'checkedAt':None,'versionId':None},'execution':{'state':'unknown','sourceRef':None,'checkedAt':None,'versionId':None},'materials':[],'history':[],'changeSummary':'合成记录，仅只读展示'}
        # UTC timestamps are stored; UI handles Asia/Shanghai.
        from datetime import datetime,timezone
        for field in ['startAt','endAt']:a[field]=datetime.fromisoformat(a[field]).astimezone(timezone.utc).isoformat().replace('+00:00','Z')
        activities.append(a);ab.append({'activityId':aid,'brandId':f'DEMO-B{bid:03}','complete':True,'provenance':PROVENANCE})
        versions.append({'id':vid,'activityId':aid,'versionNo':1,'submissionId':sid,'previousVersionId':None,'snapshot':{'rules':rule,'brandBenefit':a['brandBenefit'],'costEvidence':a['costEvidence']},'materialRefs':[],'changeSummary':a['changeSummary'],'revision':1})
        submissions.append({'id':sid,'submitterId':a['submitter']['id'],'submittedAt':a['submittedAt'],'rawText':'合成原始材料：'+name+'；'+rule,'manualFields':{},'processingState':'demo_snapshot','activityId':aid})
    # A synthetic rejected prior version + current version, never a real decision.
    target=next(a for a in activities if a['id']=='DEMO-A005')
    prior=next(v for v in versions if v['activityId']==target['id'])
    current={**prior,'id':'DEMO-A005-V2','versionNo':2,'previousVersionId':prior['id'],'changeSummary':'本次仅补充合成费用说明，成本/单位仍待核实','revision':2}
    versions.append(current);target['currentReviewVersionId']=current['id'];target['revision']=2;target['changeSummary']=current['changeSummary']
    history=[]
    for n,(vid,stage,result,actor,role,at,reason) in enumerate([
        (prior['id'],'final','rejected','DEMO-MANAGEMENT-A','management','2026-11-01T03:20:00Z','费用依据不完整（合成历史）'),
        (current['id'],'initial','approved','DEMO-MANAGEMENT-B','management','2026-11-02T04:45:00Z','补充材料进入复审（合成历史）'),
        (current['id'],'pricing','approved','DEMO-PRICING-A','pricing','2026-11-02T05:20:00Z','价盘核对完成（合成历史）')],1):
        history.append({'id':'DEMO-HISTORY-'+str(n),'activityId':target['id'],'submissionId':prior['submissionId'],'versionId':vid,'stage':stage,'result':result,'actorId':actor,'actorRole':role,'at':at,'opinion':reason,'fromState':stage+'_pending','toState':'rejected' if result=='rejected' else 'pricing_pending' if stage=='initial' else 'final_pending','sourceKind':'demo','provenance':PROVENANCE})
    target['history']=history
    products=[];ap=[]
    for i in range(17):
        bid=3 if i<9 else 1 if i<12 else 4 if i<14 else 2 if i<16 else 5
        pid=f'DEMO-P{i+1:04}';p={'id':pid,'internalCode':f'DEMO-SKU{i+1:04}','name':brands[bid-1]['name']+['绵柔日用','旅行装牙膏','洗护套装'][i%3],'spec':['250mm / 10片','60g / 支','500g / 瓶'][i%3],'upc':'069032443'+str(76273+i),'brandId':f'DEMO-B{bid:03}','provenance':PROVENANCE,'relationComplete':True}
        products.append(p)
        ids={3:['DEMO-A003'],1:['DEMO-A001','DEMO-A006','DEMO-H001'],4:['DEMO-A004','DEMO-A005'],2:['DEMO-A002'],5:[]}[bid]
        for aid in ids:ap.append({'activityId':aid,'productId':pid,'complete':True,'activityPrice':None,'currency':None,'priceUnit':None,'historicalSales':metric(),'latestActivityId':None,'provenance':PROVENANCE})
    return {'meta':{'schemaVersion':1,'datasetRevision':'jt019-demo-r2','dataOrigin':'synthetic_demo','sourceReady':True,'relationsComplete':True,'sourceHashes':[{'sourceRef':SOURCE,'sha256':hashlib.sha256(__import__('pathlib').Path(__file__).read_bytes()).hexdigest()}]},'brands':brands,'products':products,'activities':activities,'activity_brands':ab,'activity_products':ap,'activity_versions':versions,'submissions':submissions,'history':history}
if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--data-dir',required=True);args=p.parse_args()
    print(json.dumps({'database':str(prepare_db(args.data_dir,dataset(),mode='demo')),'mode':'demo','readOnlyAtRuntime':True}))
