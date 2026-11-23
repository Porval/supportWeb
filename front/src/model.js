import {activityCategoryLabel,initiatorTypeLabel,typeCode} from './dto.js';
export const TYPES=['品牌活动','平台活动','价探招商'];
export const ROLES=['门店运营','商品','管理','营销','招商','供应链','价盘','财务','其他'];
export function matches(value,query){return String(value??'').toLocaleLowerCase().includes(String(query??'').trim().toLocaleLowerCase())}
export function canManageAccounts(s){return s?.authenticated===true&&Array.isArray(s.permissions)&&s.permissions.includes('accounts.manage')}
export function brandName(data,id){return data.brands.find(b=>b.id===id)?.name??'品牌待核实'}
export function activityRelations(data,code,platform=''){
 const seen=new Set();return data.relations.filter(r=>r.code===code).flatMap(r=>{const a=data.activities.find(x=>x.id===r.activityId);if(!a||(!data.serverFiltered&&platform&&a.platform!==platform))return [];const key=JSON.stringify([code,a.id]);if(seen.has(key))return [];seen.add(key);return [{...r,activity:a}];});
}
export function filterActivities(data,f={}){if(data.serverFiltered)return data.activities;return data.activities.filter(a=>(!f.type||typeCode(a.initiatorType??a.type)===typeCode(f.type))&&(!f.activityCategory||a.activityCategory===f.activityCategory)&&(!f.platform||a.platform===f.platform)&&(!f.status||a.reviewLabel===f.status)&&matches(a.name+' '+a.id,f.q)&&matches(brandName(data,a.brandId),f.brand)&&(!f.expiry||(a.end&&(f.expiry==='expired'?a.end<f.today:a.end>=f.today))))}
export function filterProducts(data,f={}){if(data.serverFiltered)return data.products.map(p=>({...p,relations:activityRelations(data,p.code,f.platform)}));
 const relations=p=>activityRelations(data,p.code,f.platform).filter(r=>matches([r.activity.name,r.activity.id,...(r.activity.externalIds??[]).map(x=>x.id)].join(' '),f.activityQ));
 return data.products.filter(p=>{
 const rel=relations(p);return matches(p.name+' '+p.code+' '+p.upc,f.q)&&(!f.brand||p.brandId===f.brand)&&(!f.platform||rel.length>0)&&(!f.activityQ?.trim()||rel.length>0)&&(!f.relation||(f.relation==='yes'?rel.length>0:rel.length===0));
}).map(p=>({...p,relations:relations(p)}))}
export function paginate(rows,page=1,size=10){page=Number.isFinite(page)?page:1;size=Number.isFinite(size)&&size>0?size:10;const total=rows.length;const pages=Math.max(1,Math.ceil(total/size));const current=Math.min(Math.max(1,page),pages);return {rows:rows.slice((current-1)*size,current*size),total,pages,current,size};}
export function todayBeijing(now=new Date()){return new Intl.DateTimeFormat('sv-SE',{timeZone:'Asia/Shanghai',year:'numeric',month:'2-digit',day:'2-digit'}).format(now)}
