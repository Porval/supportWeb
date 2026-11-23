import {activityDTO,productDTO,queryFilters,supportsActivityIntake} from './dto.js';
export class ApiError extends Error{constructor(message,code,status=0){super(message);this.code=code;this.status=status}}
export class NotConnectedError extends ApiError{}
export function createPagedReader(request){
 return async function all(path,filters={},signal,{partialActivityId,allowProductScope=false,scopeDataOrigin='activity_workbench'}={}){const rows=[];let firstMeta=null;let observedTotal;for(let page=1;page<=101;page++){
  const params=new URLSearchParams({...filters,page:String(page),pageSize:'100'});const env=await request(path+'?'+params,{signal});const {items,total}=env.data;
  if(page===1&&partialActivityId&&path==='/activities/'+encodeURIComponent(partialActivityId)+'/products'&&
     env.meta?.dataOrigin===scopeDataOrigin&&env.data.relationsComplete===false&&total===null&&
     env.data.page===1&&env.data.pageSize===100&&Array.isArray(items)&&items.length<=100&&
     items.every(row=>typeof row?.product?.id==='string'&&typeof row.product.internalCode==='string'&&
      row.relation?.activityId===partialActivityId&&row.relation.productId===row.product.id)){
   return {items,meta:env.meta,activityProductsPartial:true};
  }
  const productActivityList=/^\/products\/[^/]+\/activities$/.test(path);
  const productScope=allowProductScope&&(path==='/products'||productActivityList)&&
   total===null&&env.data.productListState==='scope_only'&&
   env.meta?.dataOrigin===scopeDataOrigin&&env.meta.complete===false&&
   (!productActivityList||env.data.relationComplete===false&&env.data.relationState==='incomplete');
  if(productScope){
   if(firstMeta&&firstMeta.datasetRevision!==env.meta.datasetRevision)throw new ApiError('数据版本在分页中改变，请重新查询','REVISION_CHANGED');
   const count=env.data.observedTotal;
   const validRows=Array.isArray(items)&&items.length<=100&&items.every(row=>
    typeof row?.id==='string'&&row.id.length>0&&typeof row.name==='string'&&
    (productActivityList||typeof row.internalCode==='string'&&row.internalCode.length>0));
   const validPage=typeof env.meta.datasetRevision==='string'&&env.meta.datasetRevision.length>0&&
    env.data.page===page&&env.data.pageSize===100;
   const validCount=count===undefined?page===1:
    Number.isSafeInteger(count)&&count>=0&&items?.length===Math.min(100,Math.max(0,count-(page-1)*100))&&
    (page===1||count===observedTotal);
   if(!validRows||!validPage||!validCount)throw new ApiError('已知商品范围分页无法确认，请重新查询','INCOMPLETE_RESPONSE');
   if(count>10000)throw new ApiError('查询超过展示上限，请缩小筛选范围','DISPLAY_LIMIT');
   firstMeta??=env.meta;
   observedTotal=count;
   rows.push(...items);
   if(count===undefined||rows.length===count)return {items:rows,meta:firstMeta,
    relationComplete:env.data.relationComplete,relationState:env.data.relationState};
   continue;
  }
  if(observedTotal!==undefined||!Array.isArray(items)||!Number.isInteger(total)||total<0)throw new ApiError('分页完整性尚不能确认，未将结果当作空数据','INCOMPLETE_RESPONSE');
  if(firstMeta&&firstMeta.datasetRevision!==env.meta.datasetRevision)throw new ApiError('数据版本在分页中改变，请重新查询','REVISION_CHANGED');firstMeta??=env.meta;rows.push(...items);
  if(rows.length>10000)throw new ApiError('查询超过展示上限，请缩小筛选范围','DISPLAY_LIMIT');
  if(rows.length>=total)return {items:rows,meta:firstMeta,relationComplete:env.data.relationComplete,relationState:env.data.relationState};if(!items.length)throw new ApiError('分页不完整，请重新查询','INCOMPLETE_RESPONSE');
 }throw new ApiError('查询分页超限，请缩小范围','DISPLAY_LIMIT')};
}

export function createQueryReader(request,all){
 return async function read({route='demo/activity',filter={},signal}={}){
   const [catalogs,capabilities,context]=await Promise.all([request('/catalogs',{signal}),request('/capabilities',{signal}),request('/context',{signal})]);
   const parts=(route.replace(/^demo\//,'')||'activity').split('/');const [view='activity',id]=parts;
   const source=catalogs.meta.dataOrigin;
   const activityIntake=source==='activity_workbench'&&supportsActivityIntake(capabilities.data);
   const data={activityIntake,platforms:(catalogs.data.platforms??[]).map(p=>p.label),brands:catalogs.data.brands??[],activities:[],products:[],relations:[],complete:true,serverFiltered:true,meta:catalogs.meta,capabilities:capabilities.data,context:context.data,detailModules:null};
   const activities=new Map(),products=new Map(),relations=new Map();
   const addActivity=a=>activities.set(a.id,activityDTO(a));
   const addProduct=p=>{products.set(p.id,productDTO(p));if(p.brandId&&p.brandName&&!data.brands.some(b=>b.id===p.brandId))data.brands.push({id:p.brandId,name:p.brandName,queryOnly:true})};
   const addRelation=(p,a,explicitRelation)=>{
    addProduct(p);
    if(!activities.has(a.id))addActivity(a);
    const fields=['relationKind','relationLabel','platformCodes','versionIds','brandIds','relationSources','participationPrices','activityCategory','initiatorType','couponTiers'];
    // Product query activities carry relationship fields inline. Activity detail returns a separate SKU relation.
    const source=explicitRelation??Object.fromEntries(fields.filter(k=>a[k]!==undefined).map(k=>[k,a[k]]));
    relations.set(JSON.stringify([p.internalCode,a.id]),{...source,code:p.internalCode,activityId:a.id});
   };
   function accept(meta){data.complete=data.complete&&meta.complete===true;data.meta=meta}
   if(view==='activity'&&id||view==='review'&&id){
    const activityId=id;
    const path='/activities/'+encodeURIComponent(activityId);
    const env=await request(path,{signal});accept(env.meta);addActivity(env.data);
    if(env.data.productListState==='scope_only')data.complete=false;
    else{
     const confirmedIncomplete=view==='activity'&&activityIntake&&env.meta.dataOrigin===source&&env.data.id===activityId&&
      env.data.relationsComplete===false&&env.data.productCount===null;
     const goods=await all(path+'/products',{sort:'source_order'},signal,{partialActivityId:confirmedIncomplete?activityId:undefined,scopeDataOrigin:source});
     accept(goods.meta);data.complete&&=env.data.relationsComplete===true;
     if(goods.activityProductsPartial)data.activityProductsPartial=true;
     for(const row of goods.items)addRelation(row.product,env.data,{...row.relation,historySales:row.historicalSales,latestActivity:row.latestActivity});
    }
    data.brands=[...new Map([...data.brands,...(env.data.brands??[])].map(b=>[b.id,b])).values()];
   }else if(view==='brand'&&id){
    const env=await request('/brands/'+encodeURIComponent(id),{signal});accept(env.meta);data.detailModules=env.data.modules;data.brands=[...new Map([...data.brands,env.data].map(b=>[b.id,b])).values()];
    const [acts,prods]=await Promise.all([all('/brands/'+encodeURIComponent(id)+'/activities',{},signal),all('/brands/'+encodeURIComponent(id)+'/products',{},signal)]);accept(acts.meta);accept(prods.meta);
    if(activityIntake)data.brandActivities={items:acts.items.map(activityDTO),complete:acts.meta.brandActivityRelationsComplete===true};
    acts.items.forEach(addActivity);for(const p of prods.items){addProduct(p);for(const a of p.activities??[])addRelation(p,a)}
   }else if(view==='product'){
    const query=queryFilters(view,filter,{linkedOnly:activityIntake});
    const productOptions={allowProductScope:activityIntake,scopeDataOrigin:source};
    const list=await all('/products',query,signal,productOptions);accept(list.meta);
    for(const p of list.items){
     addProduct(p);
     if(activityIntake){
     for(const a of p.activities??[])addRelation(p,a);
     if(p.relationComplete!==true)data.complete=false;
     continue;
    }
    const rel=await all('/products/'+encodeURIComponent(p.id)+'/activities',
      query.platformCode?{platformCode:query.platformCode}:{},signal,productOptions);
     accept(rel.meta);if(rel.relationComplete!==true)data.complete=false;
     for(const a of rel.items)addRelation(p,a);
    }
   }else if(view==='brand'){
    const linkedOnly=activityIntake;
    const q=queryFilters(view,filter);
    if(linkedOnly)q.hasActivities='yes';
    const list=await all('/brands',q,signal);
    accept(list.meta);
    if(linkedOnly){
     data.brands=list.items.filter(b=>Array.isArray(b.activities)&&b.activities.some(a=>typeof a?.id==='string'&&a.id));
     data.linkedBrandList={associationContractVersion:list.meta.brandAssociationsContractVersion,complete:list.meta.brandActivityRelationsComplete===true&&data.brands.length===list.items.length};
    }else data.brands=list.items;
    for(const b of data.brands)for(const a of b.activities??[])if(a?.id)addActivity(a);
   }else if(['activity','reviews'].includes(view)){
    const q=queryFilters('activity',filter);
    if(activityIntake&&filter.status==='待安排审核')q.reviewResult='awaiting_assignment';
    if(activityIntake&&capabilities.data.platformMaterials?.enabled===true&&
     capabilities.data.platformMaterials.contractVersion==='platform-materials/1.0'&&filter.platformProgressState)q.platformProgressState=filter.platformProgressState;
    const list=await all('/activities',q,signal);accept(list.meta);list.items.forEach(addActivity);
   }
   data.activities=[...activities.values()];data.products=[...products.values()];data.relations=[...relations.values()];return data;
  };
}

