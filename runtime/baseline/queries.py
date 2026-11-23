"""Shared scope/filter/pagination/transaction snapshot serialization for r1 A."""
from datetime import datetime,timezone
import hashlib
import json
from account_primitives import fail,iso,ROLES
from seed import metric

TYPES=[{'code':'brand','label':'品牌活动'},{'code':'platform','label':'平台活动'},{'code':'jiatan_sourcing','label':'价探招商'}]
PLATFORMS=[{'code':'meituan','label':'美团'},{'code':'taobao_instant','label':'淘宝闪购'},{'code':'jiatan','label':'价探'}]
COLUMN_PAIRS=[('internalCode','内部码'),('upc','UPC'),('productName','商品名称'),('spec','规格'),('brandName','品牌'),('activityId','内部活动ID'),('activityName','活动名称'),('activityType','活动类型'),('review','内部进度'),('registrationState','报名核实结果'),('registrationSource','报名核实来源'),('registrationCheckedAt','最近核实时间'),('platformCode','平台'),('startAt','活动开始'),('endAt','活动结束'),('relationNote','关联说明')]
COLUMNS=[{'key':k,'label':label,'type':'text'} for k,label in COLUMN_PAIRS]
FILTERS={'activities':{'q','type','platformCode','brandId','reviewStage','reviewResult','registrationState','executionState','expiryState'},'brands':{'q','hasActivities'},'products':{'q','brandId','platformCode','association'},'activity_products':{'sort'}}

def invalid(message,code='INVALID_FILTER'):fail(422,code,message)
def number(v,default,maximum=None):
    if v is None:return default
    if not isinstance(v,str) or not v.isascii() or not v.isdigit() or int(v)<1 or maximum and int(v)>maximum:invalid('分页参数无效')
    return int(v)
def pagination(items,q,default=20):
    page=number(q.get('page'),1);size=number(q.get('pageSize'),default,100)
    return {'items':items[(page-1)*size:page*size],'page':page,'pageSize':size,'total':len(items)}
def validate(q,allowed):
    if set(q)-allowed-{'page','pageSize'}:invalid('未知筛选字段')
    number(q.get('page'),1);number(q.get('pageSize'),20,100)
def enum(q,key,allowed,default=None):
    value=q.get(key,default)
    if value is not None and value not in allowed:invalid(key+'无效')
    return value

def expiry(a,clock):
    try:
        start=datetime.fromisoformat(a['startAt'].replace('Z','+00:00'))
        end=datetime.fromisoformat(a['endAt'].replace('Z','+00:00'))
        if start.tzinfo is None or end.tzinfo is None or start>end:raise ValueError()
        return {'state':'expired' if end.timestamp()<=clock else 'current'}
    except (ValueError,TypeError,KeyError,AttributeError):return {'state':'unknown'}

class Query:
    def __init__(self,data,mode,clock):
        self.data=data;self.mode=mode;self.clock=clock
        self.meta={'mode':mode,'dataOrigin':data['meta']['dataOrigin'],'datasetRevision':data['meta']['datasetRevision'],'fetchedAt':iso(clock),'complete':bool(data['meta']['relationsComplete']),'warnings':['本地只读演示，非真实身份/经营/执行'] if mode=='demo' else []}
        self.meta['warnings']+=['报名/上线/销量/成本来源未接入；缺失不按0']
    def ready(self):
        if not self.data['meta']['sourceReady']:fail(503,'SOURCE_UNAVAILABLE','正常业务数据源尚未接入')
    def scoped(self,grants):
        """Filter ACL before any count/catalog/export, no data from denied IDs."""
        if grants is None:return
        for table,kind in [('activities','activity'),('brands','brand'),('products','product')]:
            self.data[table]=[r for r in self.data[table] if '*' in grants or kind+':'+r['id'] in grants]
        aids={a['id'] for a in self.data['activities']};bids={b['id'] for b in self.data['brands']};pids={p['id'] for p in self.data['products']}
        self.data['activity_products']=[r for r in self.data['activity_products'] if r['activityId'] in aids and r['productId'] in pids]
        self.data['activity_brands']=[r for r in self.data['activity_brands'] if r['activityId'] in aids and r['brandId'] in bids]
        for a in self.data['activities']:a['brandIds']=[i for i in a['brandIds'] if i in bids]
        for p in self.data['products']:
            if p['brandId'] not in bids:p['brandId']=None
        self.data['activity_versions']=[v for v in self.data['activity_versions'] if v['activityId'] in aids]
        self.data['submissions']=[v for v in self.data['submissions'] if v['activityId'] in aids]
        self.data['materials']=[]  # No separately authorized real material source in A.
        self.data['history']=[v for v in self.data['history'] if v.get('activityId') in aids]
    def activity(self,a):return a|{'expiry':expiry(a,self.clock)}
    def get(self,table,id):
        r=next((r for r in self.data[table] if r['id']==id),None)
        if r is None:fail(404,'NOT_FOUND','记录不存在或不可访问')
        return r
    def activities(self,q):
        validate(q,FILTERS['activities'])
        typ=enum(q,'type',{'all','brand','platform','jiatan_sourcing'},'all')
        enum(q,'expiryState',{'current','expired','unknown'})
        enum(q,'reviewStage',{'initial','pricing','final','none'})
        enum(q,'reviewResult',{'pending','approved','rejected','unknown'})
        enum(q,'registrationState',{'unknown','not_registered','submitted','pending','accepted','rejected'})
        enum(q,'executionState',{'unknown','pending','preparing','live','failed'})
        out=[]
        for a in self.data['activities']:
            view=self.activity(a)
            if q.get('q','').strip().casefold() not in (a['name']+' '+a['id']).casefold():continue
            if typ!='all' and a['type']!=typ:continue
            if q.get('platformCode') and a['platformCode']!=q['platformCode']:continue
            if q.get('brandId') and q['brandId'] not in a['brandIds']:continue
            if q.get('reviewStage') and (a['review']['stage'] or 'none')!=q['reviewStage']:continue
            if q.get('reviewResult') and a['review']['result']!=q['reviewResult']:continue
            if q.get('registrationState') and a['registration']['state']!=q['registrationState']:continue
            if q.get('executionState') and a['execution']['state']!=q['executionState']:continue
            if q.get('expiryState') and view['expiry']['state']!=q['expiryState']:continue
            out.append(view)
        return out
    def relations(self,pid,platform=None):
        aids={r['activityId'] for r in self.data['activity_products'] if r['productId']==pid}
        return [self.activity(a) for a in self.data['activities'] if a['id'] in aids and (not platform or a['platformCode']==platform)]
    def products(self,q):
        validate(q,FILTERS['products']);association=enum(q,'association',{'all','linked','unlinked'},'all')
        result=[]
        for p in self.data['products']:
            if q.get('q','').strip().casefold() not in (' '.join(str(p.get(k) or '') for k in ['internalCode','name','upc'])).casefold():continue
            if q.get('brandId') and p['brandId']!=q['brandId']:continue
            complete=p.get('relationComplete',False) and self.data['meta']['relationsComplete']
            rel=self.relations(p['id'],q.get('platformCode'))
            if q.get('platformCode') and not rel:continue
            if association=='linked' and not rel:continue
            if association=='unlinked' and (rel or not complete):continue
            result.append(p|{'relationComplete':bool(complete),'activityCount':len(rel) if complete else None,'activities':rel})
        return result
    def brand_view(self,b):
        ids={r['activityId'] for r in self.data['activity_brands'] if r['brandId']==b['id']}
        activities=[self.activity(a) for a in self.data['activities'] if a['id'] in ids]
        return b|{'activities':activities[:4],'hasMore':len(activities)>4,'lastMonthGmv':metric()}
    def brands(self,q):
        validate(q,FILTERS['brands']);has=enum(q,'hasActivities',{'all','yes','no'},'all')
        if has!='all' and self.mode!='demo':invalid('真实“品牌有活动”范围未确认','RULE_UNCONFIRMED')
        items=[self.brand_view(b) for b in self.data['brands'] if q.get('q','').strip().casefold() in b['name'].casefold()]
        if has!='all':items=[b for b in items if bool(b['activities'])==(has=='yes')]
        return items
    def activity_products(self,aid,q):
        self.get('activities',aid);validate(q,FILTERS['activity_products'])
        sort=enum(q,'sort',{'source_order','historical_sales_desc'},'source_order')
        rel={r['productId']:r for r in self.data['activity_products'] if r['activityId']==aid}
        rows=[{'product':p,'relation':rel[p['id']],'latestActivity':None,'historicalSales':rel[p['id']].get('historicalSales',metric())} for p in self.data['products'] if p['id'] in rel]
        if sort=='historical_sales_desc':
            if any(any(r['historicalSales'].get(k) is None for k in ['value','periodStart','periodEnd','definitionRef','unit']) for r in rows):invalid('历史销量口径或区间不完整，不能排序','RULE_UNCONFIRMED')
            from decimal import Decimal,InvalidOperation
            try:rows.sort(key=lambda r:(-Decimal(r['historicalSales']['value']),r['product']['internalCode']))
            except (InvalidOperation,TypeError):invalid('销量数据无效','RULE_UNCONFIRMED')
        return rows
    def detail(self,aid):
        a=self.activity(self.get('activities',aid))
        versions=[v for v in self.data['activity_versions'] if v['activityId']==aid]
        return a|{'brands':[b for b in self.data['brands'] if b['id'] in a['brandIds']],'versions':versions,'submissions':[s for s in self.data['submissions'] if s['activityId']==aid],'productCount':len(self.activity_products(aid,{})) if self.data['meta']['relationsComplete'] else None,'relationsComplete':self.data['meta']['relationsComplete']}
    def snapshot(self,q):
        kind=enum(q,'kind',{'product_activity','activity_products'})
        if kind is None:invalid('缺少导出kind')
        number(q.get('page'),1);number(q.get('pageSize'),20,100)
        filters={k:v for k,v in q.items() if k not in ['kind','page','pageSize']}
        if not self.data['meta']['relationsComplete']:invalid('关联不完整，不能导出完整结果','INCOMPLETE_RELATIONS')
        if kind=='product_activity':products=self.products(filters)
        else:
            aid=filters.get('activityId')
            if not aid:invalid('缺少activityId')
            detail_q={k:v for k,v in filters.items() if k!='activityId'}
            self.activity_products(aid,detail_q)
            products=[self.get('products',r['product']['id'])|{'activities':[self.activity(self.get('activities',aid))],'relationComplete':r['relation']['complete']} for r in self.activity_products(aid,detail_q)]
        rows=[];brand_names={b['id']:b['name'] for b in self.data['brands']}
        for p in products:
            if not p.get('relationComplete'):invalid('商品关联不完整','INCOMPLETE_RELATIONS')
            for a in p.get('activities',[]) or [None]:
                rows.append([p['internalCode'],p.get('upc'),p['name'],p['spec'],brand_names.get(p['brandId']),a['id'] if a else None,a['name'] if a else None,a['type'] if a else None,(':'.join(v for v in [a['review']['stage'],a['review']['result']] if v)) if a else None,'暂时无法确认' if a and a['registration']['state']=='unknown' else (a['registration']['state'] if a else '不适用：无关联记录'),a['registration']['sourceRef'] if a else None,a['registration']['checkedAt'] if a else None,a['platformCode'] if a else None,a['startAt'] if a else None,a['endAt'] if a else None,'合成演示关联' if a and self.mode=='demo' else ('已知关联' if a else '无关联记录')])
                if len(rows)>10000:invalid('导出超过10000行上限','EXPORT_LIMIT')
        if not rows:invalid('当前授权筛选无结果','EMPTY_EXPORT')
        value={'kind':kind,'filters':filters,'columns':COLUMNS,'rows':rows,'rowCount':len(rows),'generatedAt':iso(self.clock),'fetchedAt':self.meta['fetchedAt'],'datasetRevision':self.meta['datasetRevision'],'complete':True}
        value['snapshotId']='snapshot-'+hashlib.sha256(json.dumps(value,ensure_ascii=False,sort_keys=True).encode()).hexdigest()[:32]
        return value
    def route(self,path,q):
        if path.startswith('/materials/'):
            fail(404,'MATERIAL_UNAVAILABLE','无可下载原始材料')
        self.ready()
        if path=='/activities':return pagination(self.activities(q),q)
        if path=='/brands':return pagination(self.brands(q),q)
        if path=='/products':return pagination(self.products(q),q)
        if path=='/exports/snapshot':return self.snapshot(q)
        parts=path.strip('/').split('/')
        if len(parts)>=2:
            category,id=parts[:2]
            if category=='materials':fail(404,'MATERIAL_UNAVAILABLE','无可下载原始材料')
            if category=='activities':
                if len(parts)==2:validate(q,set());return self.detail(id)
                if parts[2:] == ['products']:return pagination(self.activity_products(id,q),q,5)
            if category=='brands':
                brand=self.get('brands',id)
                if len(parts)==2:
                    validate(q,set())
                    return self.brand_view(brand)|{'modules':{k:{'available':False,'blockedReason':'统计口径/数据源未接入'} for k in ['currentActivities','lastMonthReview','productSales']},'relatedProductCount':len([p for p in self.data['products'] if p['brandId']==id]) if self.data['meta']['relationsComplete'] else None}
                if parts[2:]==['activities']:
                    validate(q,set());ids={r['activityId'] for r in self.data['activity_brands'] if r['brandId']==id};return pagination([self.activity(a) for a in self.data['activities'] if a['id'] in ids],q)
                if parts[2:]==['products']:
                    validate(q,set());return pagination(self.products({'brandId':id}),q)
            if category=='products' and parts[2:]==['activities']:
                p=self.get('products',id);validate(q,{'platformCode'})
                rel=self.relations(id,q.get('platformCode'));unfiltered=self.relations(id)
                return pagination(rel,q)|{'relationComplete':bool(p.get('relationComplete',False) and self.data['meta']['relationsComplete']),'relationState':'incomplete' if not (p.get('relationComplete') and self.data['meta']['relationsComplete']) else 'filtered_empty' if unfiltered and not rel else 'empty' if not rel else 'known'}
        fail(404,'NOT_FOUND','接口不存在')
