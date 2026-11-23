"""Read-only brand/activity projection. Candidate labels never become master IDs."""
import hashlib
import json

from account_primitives import APIError, fail
from b_domain import can_version, can_material
from queries import pagination, validate, enum
from b_activity_relations import catalog_brand_id, relation_summary

CONTRACT = 'brand-associations/1.0'
BRAND_HEADERS = frozenset(('品牌', '品牌名', '品牌名称', '商品品牌'))


def candidate_id(label):
    # This identifies an exact source-label group, not a verified brand entity.
    return 'candidate-brand-' + hashlib.sha256(label.encode()).hexdigest()[:24]


def allowed(auth, db, actor, resource):
    try:
        auth.require(db, actor, 'operations.read', resource)
        return True
    except APIError as error:
        if error.status != 403:
            raise
        return False


def attachment_brands(db, view, auth, actor):
    """Only explicit brand-column cells on rows bound to this version's group."""
    if view.get('sourceAvailability',{}).get('status')!='available':return
    for source in view.get('sourceInputs', []):
        for analysis in source.get('analyses', []):
            run = db.execute('SELECT payload FROM analysis_runs WHERE id=?', (analysis.get('runId'),)).fetchone()
            if not run:
                continue
            payload = json.loads(run[0]); result = payload.get('result') or {}
            candidate = next((c for c in result.get('candidates', []) if c['candidateId']==analysis.get('candidateId')), None)
            if not candidate:
                continue
            ids = {eid for row in candidate['productRows'] for eid in row['evidenceIds']}
            anchors = {(e['materialId'],e['locator'].get('sheet'),e['locator'].get('row'))
                       for e in result.get('evidence', []) if e['evidenceId'] in ids}
            readable = {m.get('materialId') for m in view.get('materials', [])}
            for doc in payload.get('materialResults', []):
                if doc['materialId'] not in readable or not can_material(db,actor,doc['materialId'],auth):
                    continue
                from b_domain import material
                original = material(db, doc['materialId'])
                if not original or original['sha256']!=doc['sha256'] or hashlib.sha256(original['content']).hexdigest()!=doc['sha256']:
                    continue
                headers = {}
                for row in doc.get('tableRows', []):
                    for cell in row['cells']:
                        loc=cell['locator']; label=cell['text'].strip(); key=(loc.get('sheet'),cell['column'])
                        if label in BRAND_HEADERS:
                            headers[key]=True
                        elif headers.get(key) and (doc['materialId'],loc.get('sheet'),loc.get('row')) in anchors and label:
                            yield label, {'kind':'attachment_brand_column','materialId':doc['materialId'],
                                          'sourceSha256':doc['sha256'],'locator':loc,'quote':cell['text']}


class BrandAssociations:
    def __init__(self, business):
        self.business=business; self.auth=business.auth

    def build(self, db, actor, data):
        if 'operations.read' not in self.auth.permissions(db,actor):
            fail(403,'FORBIDDEN','当前账号无活动查询权限')
        brands={b['id']:dict(b) for b in data['brands']}
        products={p['id']:p for p in data['products']}
        finalized=set(data.get('relation_projection',{}).get('confirmedActivityIds',[]))
        associations={}; complete=True

        def add(bid, name, activity, status, basis):
            if bid not in brands:
                brands[bid]={'id':bid,'name':name,'benefit':None,'identityStatus':'candidate_label'}
            key=(bid,activity['id']); old=associations.get(key)
            if old and old['brandAssociation']['status']=='confirmed' and status!='confirmed':
                return
            if old and old['brandAssociation']['status']==status:
                if basis not in old['brandAssociation']['sources']:
                    old['brandAssociation']['sources'].append(basis)
                return
            associations[key]=activity|{'brandAssociation':{'status':status,'sources':[basis]}}

        for activity in data['activities']:
            aid=activity['id']; vid=activity.get('displayVersionId') or activity.get('currentReviewVersionId')
            if not allowed(self.auth,db,actor,aid):
                continue
            projected=self.business.date_activity(db,actor,activity)
            from b_api import activity_period
            projected=projected|{'activityPeriodState':activity_period(projected,self.business.clock())}
            # These official sources already passed RelationQueryMixin.scoped.
            # A newer display version may have a different read grant; that
            # must not hide the readable, approved relationship underneath it.
            for relation in data['activity_brands']:
                bid=relation['brandId']
                if relation['activityId']==aid and bid in brands:
                    sources=relation.get('relationSources',[]) or [relation.get('provenance') or {'kind':'confirmed_relation'}]
                    for basis in sources:
                        if aid in finalized and basis.get('kind')!='final_registration_review':
                            continue
                        add(bid,brands[bid]['name'],projected,'confirmed',basis)
            for relation in data['activity_products']:
                if relation['activityId']!=aid or relation['productId'] not in products:
                    continue
                product=products[relation['productId']]
                bids=relation.get('brandIds') or ([product['brandId']] if product.get('brandId') else [])
                for bid in bids:
                    if bid not in brands:
                        continue
                    for basis in relation.get('relationSources',[]) or [{'kind':'confirmed_product_brand','productId':product['id']}]:
                        add(bid,brands[bid]['name'],projected,'confirmed',basis)
            if aid in finalized:
                continue
            if not vid or not can_version(db,actor,vid,self.auth):
                complete=False
                continue
            view=self.business.version(db,actor,aid,vid)
            snapshot=view.get('snapshot') or {}; fields=snapshot.get('resolvedFields') or snapshot.get('confirmedFields') or {}
            confirmed=fields.get('brandIds',activity.get('brandIds',[])) or []
            for bid in confirmed:
                if bid in brands:
                    add(bid,brands[bid]['name'],projected,'confirmed',{'kind':'version_brand','versionId':vid})
            for pid in snapshot.get('products', []):
                product=products.get(pid); bid=product.get('brandId') if product else None
                if bid in brands and allowed(self.auth,db,actor,pid):
                    add(bid,brands[bid]['name'],projected,'confirmed',{'kind':'adopted_product_brand','versionId':vid,'productId':pid})
            recognition=view.get('productRecognition') or {}
            if recognition.get('status')=='ready':
                for row in recognition['rows']:
                    for candidate in row.get('candidates', []):
                        label=(candidate.get('brand') or '').strip()
                        if not label:
                            continue
                        # A real brand ID can merge with formal associations. Labels
                        # alone are never matched to brand masters by spelling.
                        bid=candidate.get('brandId')
                        if bid not in brands:bid=catalog_brand_id(label) if catalog_brand_id(label) in brands else candidate_id(label)
                        add(bid,label,projected,'pending',{'kind':'company_catalog_candidate','versionId':vid,
                            'catalogDate':recognition.get('catalog',{}).get('sourceDate'),'candidateId':row['candidateId'],
                            'rowId':row['rowId'],'internalCode':candidate.get('internalCode'),
                            'sourceReferences':row.get('sourceReferences',[])})
            else:
                complete=False
            for label,basis in attachment_brands(db,view,self.auth,actor):
                add(candidate_id(label),label,projected,'pending',basis|{'versionId':vid})
        return brands,associations,complete

    def read(self,db,actor,path,q,data,handler):
        brands,relations,complete=self.build(db,actor,data)
        handler.meta.update(brandAssociationsContractVersion=CONTRACT,brandActivityRelationsComplete=complete)

        def brand_view(brand,limit=4):
            items=[a for (bid,_),a in relations.items() if bid==brand['id']]
            items.sort(key=lambda a:(a['brandAssociation']['status']!='confirmed',a.get('name',''),a['id']))
            formal=sum(a['brandAssociation']['status']=='confirmed' for a in items)
            return brand|{'activities':items[:limit],'hasMore':len(items)>limit,
                          'confirmedActivityCount':formal,'pendingActivityCount':len(items)-formal,
                          'associationContractVersion':CONTRACT,'lastMonthGmv':{'value':None,'status':'unavailable'}}

        def brand_products(bid, filters):
            # The owning read route scopes this request-local data before
            # entering BrandAssociations. Reuse the exact main query semantics
            # rather than inventing a second product-ID ACL for virtual SKUs.
            from b_api import BQuery
            incomplete={item['activityId']:item for item in
                        data.get('relation_projection',{}).get('incompleteActivities',[])}
            query=BQuery(data,self.business.clock(),incomplete)
            items=[]
            for product in query.products(filters|{'brandId':bid,'hasActivities':'yes'}):
                activities=[]
                for activity in product['activities']:
                    association=relations.get((bid,activity['id']))
                    if not association or association['brandAssociation']['status']!='confirmed':
                        continue
                    activities.append(activity|association|relation_summary(activity))
                if not activities:
                    continue
                complete_product=product['relationComplete'] and len(activities)==len(product['activities'])
                items.append(product|{'activities':activities,'relationComplete':complete_product,
                                      'activityCount':len(activities) if complete_product else None})
            complete_products=(bool(data['meta'].get('relationsComplete'))
                               and not query.affected(filters|{'brandId':bid})
                               and all(item['relationComplete'] for item in items))
            if not complete_products:
                handler.meta['complete']=False
            return items,complete_products

        parts=path.strip('/').split('/')
        if len(parts)==1:
            validate(q,{'q','hasActivities'}); has=enum(q,'hasActivities',{'all','yes','no'},'all')
            search=q.get('q','').strip().casefold(); items=[]
            for brand in brands.values():
                value=brand_view(brand)
                if search not in brand['name'].casefold():continue
                linked=bool(value['activities'])
                if not linked and not allowed(self.auth,db,actor,brand['id']):continue
                if has=='yes' and not linked or has=='no' and linked:continue
                items.append(value)
            items.sort(key=lambda b:(b['name'],b['id']))
            return pagination(items,q)
        validate(q,{'q','platformCode'} if parts[2:]==['products'] else set()); bid=parts[1]; brand=brands.get(bid)
        if not brand or not any(key[0]==bid for key in relations) and not allowed(self.auth,db,actor,bid):
            fail(404,'NOT_FOUND','品牌不存在或不可访问')
        if len(parts)==2:
            products,products_complete=brand_products(bid,{})
            return brand_view(brand)|{'modules':{k:{'available':False,'blockedReason':'统计口径及数据源待接入'} for k in ('currentActivities','lastMonthReview','productSales')},
                                     'relatedProductCount':len(products) if products_complete else None,
                                     'observedRelatedProductCount':len(products),'productRelationsComplete':products_complete,
                                     'productRelationScope':'confirmed'}
        if parts[2:]==['activities']:
            return pagination(brand_view(brand,len(relations))['activities'],q)
        if parts[2:]==['products']:
            items,products_complete=brand_products(bid,q)
            return pagination(items,q)|{'relationsComplete':products_complete,'relationScope':'confirmed'}
        fail(404,'NOT_FOUND','品牌页面不存在')
