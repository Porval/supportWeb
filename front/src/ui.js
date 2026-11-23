import {SORT_LABELS,candidateBrand,sortCandidateRows,sortValue,selectedPlatformSales} from './product-metrics.js';
import {activityDTO,ACTIVITY_CATEGORY_LABELS,TYPE_LABELS,typeCode,activityCategoryLabel,initiatorTypeLabel,couponFundingText,PLATFORM_LABELS,datePeriodText,registrationDateText,APPLICABLE_PLATFORM_LABELS,applicablePlatformCodes,productScopeIntentLabel,verifiedProductScopeLabel,hasSubmissionDeclarations,pendingItemLabels,activityPeriodLabel,productPlatformProgress} from './dto.js';
import {TYPES,ROLES,brandName,activityRelations,paginate,todayBeijing,filterActivities,filterProducts,matches,canManageAccounts} from './model.js';
import {subsidyPricesHtml} from './b-subsidy-prices.js';
export const h=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
export const badge=(text,tone='neutral')=>`<span class="badge ${tone}">${h(text)}</span>`;
export const empty=(title,desc='',action='')=>`<section class="empty"><div class="empty-icon" aria-hidden="true">◇</div><h2>${h(title)}</h2><p>${h(desc)}</p>${action}</section>`;
export const link=(url,label,cls='')=>`<a class="${cls}" href="${h(url)}">${h(label)}</a>`;
export function routeLink(route,params={}){const q=new URLSearchParams(params).toString();return '#'+route+(q?'?'+q:'')}
const relationPlatforms=r=>(r.platformCodes?.length?r.platformCodes.map(code=>PLATFORM_LABELS[code]??code):[r.activity.platform]).filter(Boolean);
function participationPriceHtml(relation,field){
 const prices=(relation.participationPrices??[]).filter(entry=>entry[field]?.amountDecimal!=null);
 const values=prices.map(entry=>{
  const price=entry[field], platform=entry.platformCode;
  const label=platform?PLATFORM_LABELS[platform]??platform:null;
  const unit=[price.currency==='CNY'?'元':price.currency,price.pricingUnit].filter(Boolean).join('/');
  return `${label?h(label)+' ':''}${h(price.amountDecimal)}${unit?' '+h(unit):''}`;
 });
 return [...new Set(values)].join('<br>')||'—';
}
function categoryAndInitiator(item,relation=null){
 const category=relation&&Object.hasOwn(relation,'activityCategory')?relation.activityCategory:item.activityCategory;
 const initiator=relation&&Object.hasOwn(relation,'initiatorType')?relation.initiatorType:item.initiatorType??typeCode(item.type);
 return h(activityCategoryLabel(category))+'<small>发起方 · '+h(initiatorTypeLabel(initiator))+'</small>';
}
function couponValue(tier){
 return tier.thresholdAmount!=null&&tier.discountAmount!=null?'满 '+tier.thresholdAmount+' 减 '+tier.discountAmount:tier.name||'券档待核对';
}
function couponDecision(tier){
 return tier.reviewSelected===true?'审核已选':tier.reviewSelected===false?'审核未选':'待审核';
}
function couponPeriod(tier,today=todayBeijing()){
 const start=tier.startDate??tier.startOn,end=tier.endDate??tier.endOn;
 const range=[start??tier.startMonthDay??'待补',end??tier.endMonthDay??'待补'].join(' — ');
 const state=tier.periodState==='active'?'current':tier.periodState??(end&&today>end?'expired':start&&today<start?'upcoming':start&&end?'current':'unknown');
 const label={current:'有效期内',upcoming:'未开始',expired:'已过期'}[state]??'有效期待核对';
 return {range,label,state};
}
function relationBenefitHtml(relation,detailed=false){
 const category=Object.hasOwn(relation,'activityCategory')?relation.activityCategory:relation.activity?.activityCategory;
 if(category!=='threshold_coupon')return participationPriceHtml(relation,'activityPrice');
 const tiers=relation.couponTiers??[];
 if(!tiers.length)return '<span class="muted">适用券档待核对</span>';
 return tiers.map(tier=>{
  const platform=PLATFORM_LABELS[tier.platformCode]??tier.channel??'平台待核对';
  const {range,label}=couponPeriod(tier);
  const heading=h(platform)+' · '+h(couponValue(tier));
  return '<div>'+heading+'<small>'+h(label)+' · '+h(couponDecision(tier))+'</small>'+
   (detailed?'<small>'+h(range)+'</small><small>'+h(couponFundingText(tier))+'</small>':'')+'</div>';
 }).join('');
}
const salesPlatforms=relations=>[...new Set(relations.flatMap(r=>r.platformCodes??[r.activity.platformCode]))]
 .filter(code=>code==='meituan'||code==='taobao_instant');
const salesRecentHtml=(code,relations)=>salesPlatforms(relations).map(platform=>
 `<div>${h(PLATFORM_LABELS[platform])} <span data-sales-recent="${h(code)}" data-sales-platform="${h(platform)}">加载中…</span></div>`).join('')||'—';
const btn=(label,act,cls='')=>`<button type="button" class="${cls}" data-action="${act}">${h(label)}</button>`;
const stamp=v=>v?new Intl.DateTimeFormat('zh-CN',{timeZone:'Asia/Shanghai',dateStyle:'short',timeStyle:'short',hour12:false}).format(new Date(v)):'待核实';
const period=a=>h(datePeriodText(a.start,a.end,a.activityDateEvidence));
function table(head,rows,cls=''){return `<div class="table-wrap ${cls}"><table><thead><tr>${head.map(x=>`<th scope="col">${h(x)}</th>`).join('')}</tr></thead><tbody>${rows.join('')||`<tr><td colspan="${head.length}"><div class="inline-empty">暂无可展示记录。</div></td></tr>`}</tbody></table></div>`}
const td=(...cells)=>'<tr>'+cells.map(c=>`<td>${c}</td>`).join('')+'</tr>';
function section(id,title,body,extra=''){return `<section id="${id}" class="panel"><div class="section-heading"><h2>${h(title)}</h2>${extra}</div>${body}</section>`}
function info(items){return `<dl class="info-grid">${items.map(([k,v])=>`<div><dt>${h(k)}</dt><dd>${v}</dd></div>`).join('')}</dl>`}
function option(value,label,selected){return `<option value="${h(value)}"${value===selected?' selected':''}>${h(label)}</option>`}
function select(name,label,values,value='',attrs=''){return `<label class="filter"><span>${h(label)}</span><select name="${name}" ${attrs}>${option('','全部',value)}${values.map(v=>option(typeof v==='string'?v:v.id,typeof v==='string'?v:v.name,value)).join('')}</select></label>`}
function input(name,label,value='',placeholder=''){return `<label class="filter grow"><span>${h(label)}</span><input name="${name}" value="${h(value)}" placeholder="${h(placeholder)}" autocomplete="off"></label>`}
function filterForm(body){return `<form id="filters" class="filters">${body}<button type="submit" class="primary">查询</button>${btn('重置','reset')}</form>`}
function pager(p,route,f,{unknownTotal=false}={}){const target=n=>routeLink(route,{...f,page:n});return `<div class="pagination"><span>${unknownTotal?'数量待核实':'共 '+p.total+' 条'} · 每页 ${p.size} 条</span>${p.current>1?link(target(p.current-1),'上一页','button'):'<button disabled>上一页</button>'}<span aria-current="page">${unknownTotal?'第 '+p.current+' 页':p.current+' / '+p.pages}</span>${p.current<p.pages?link(target(p.current+1),'下一页','button'):'<button disabled>下一页</button>'}</div>`}
function title(name,subtitle,actions='',headingBadge=''){return `<div class="page-heading"><div><h1 tabindex="-1">${h(name)}${headingBadge}</h1><p class="muted">${h(subtitle)}</p></div><div class="actions">${actions}</div></div>`}
function activityProjectBadge(activity){
 if(activity.activityCategory!=='product_discount')return '';
 const label={shen_jia:'神价',bao_hao_jia:'爆好价'}[activity.activityMethod];
 return label?badge(label,'shenjia'):'';
}
function activityLink(a,back,review=false){return routeLink('demo/'+(review?'review/':'activity/')+encodeURIComponent(a.id),{back})}
function status(a,view){const workflow=a.reviewLabel==='待安排审核'?null:view?.state(a);return badge(workflow??a.reviewLabel,a.reviewLabel==='审核通过'?'green':'blue')}
// Applicable scope is separate from the source platform; explicit empty scope stays empty.
function activityPlatformLabel(a){return applicablePlatformCodes(a,a.platformCode).map(code=>APPLICABLE_PLATFORM_LABELS[code]).join('、')}
function activityBrandNames(data,a){return (a.brandIds?.length?a.brandIds:[a.brandId]).map(id=>brandName(data,id)).join('、')}
function ratingButton(activity){
 return activity.ratingAction?.action==='activities.rate'
  ?` <button type="button" class="small" data-activity-rate="${h(activity.id)}">评级</button>`:'';
}
function reviewActionButton(activity,back){
 const action=activity.reviewAction;
 if(!back||action?.action!=='reviews.decide'||!action.versionId)return '';
 return ` <button type="button" class="small" data-activity-review="${h(activity.id)}">审核</button>`;
}
function activityRows(rows,data,back,reviewBack=null){return rows.map(a=>{
 const view=link(activityLink(a,back),'查看','button small');
 const review=reviewActionButton(a,reviewBack);
 const actions=review?`<span class="row">${view}${review}</span>`:view;
 return td(status(a,data.platformMaterialsView),`<strong>${h(a.name)}</strong> ${activityProjectBadge(a)} ${badge(a.grade??'未评级')}${reviewBack?ratingButton(a):''}`,categoryAndInitiator(a),h(listValue(activityBrandNames(data,a))),h(listValue(activityPlatformLabel(a))),h(activityShortSummary(a))+(a.fundingSummary?'<small>'+h(a.fundingSummary)+'</small>':''),h(listValue(a.submitter)),listPeriod(a,data),actions+(data.platformMaterialsView?.action(a,back)??''));
})}
function productRelationStatus(relation,showName=false){
 const a=relation.activity;
 const state=a.activityPeriodState;
 const period=badge(activityPeriodLabel(state),state==='current'?'green':state==='upcoming'?'blue':'neutral');
 const progress=productPlatformProgress(a.platformProgress,relation.platformCodes??(a.platformCode?[a.platformCode]:[]));
 return `<div>${showName?`<small>${h(a.name)}</small>`:''}${period}${progress.map(item=>`<small>${h(item)}</small>`).join('')}${!progress.length?`<small>${h(a.reviewLabel)}</small>`:''}</div>`;
}
export function workbench(data,view,f,route,currentHash,expanded){
 const common=title('活动工作台',view==='brand'?'按品牌查看关联活动与合作利益点':view==='product'?'按商品查看关联活动与参与进展':'全局活动列表，按活动查看进度',link('#demo/report','提报活动材料','button primary'));
 const tabs=`<nav class="tabs" aria-label="查询视角">${[['activity','活动视角'],['brand','品牌视角'],['product','商品视角']].map(([r,l])=>link('#demo/'+r,l,view===r?'selected':'')).join('')}</nav>`;
 if(view==='brand'){
  const brands=data.serverFiltered?data.brands:data.brands.filter(b=>matches(b.name,f.q));
  const p=paginate(brands,Number(f.page));
  const scope=data.linkedBrandList;
  const filters=(scope?'':select('has','是否有活动',[],'','disabled aria-describedby="brand-scope"'))+input('q','搜索品牌',f.q,'输入品牌名称');
  const note=scope?.associationContractVersion==='brand-associations/1.0'?'同一活动可出现在多个参与品牌下。待确认关联单独标注，不计已确认活动数。':scope?(scope.complete?'仅展示已关联活动的品牌。':'关联资料不完整，当前仅展示已确认有关联活动的品牌。'):'“是否有活动”的统计范围待确认，暂不可筛选。';
  const body=p.rows.map(b=>{
   const list=(scope?(b.activities??[]).filter(a=>a?.id).map(a=>activityDTO(a)):data.activities.filter(a=>(a.brandIds??[a.brandId]).includes(b.id))).sort((a,b)=>Number(a.brandAssociation?.status==='pending')-Number(b.brandAssociation?.status==='pending')).slice(0,4);
   const summaries=list.map(a=>`<div class="activity-summary${a.brandAssociation?.status==='pending'?' association-pending':''}"><div>${link(activityLink(a,currentHash),a.name)} ${badge(a.reviewLabel,'blue')} ${a.brandAssociation?.status==='pending'?badge('待确认','amber'):''}</div>
    <div>${categoryAndInitiator(a)}</div><small>${h(listValue(activityPlatformLabel(a)))} · ${listPeriod(a,data)}</small><p class="activity-short-summary">${h(activityShortSummary(a))}</p>${a.fundingSummary?'<small>'+h(a.fundingSummary)+'</small>':''}${brandAssociationSourcesHtml(a)}</div>`).join('');
   return td(`<strong>${h(b.name)}</strong>`,summaries||'—',
    h(listValue(b.benefit)),'—',
    link(routeLink('demo/brand/'+encodeURIComponent(b.id),{back:currentHash}),'查看','button small'));
  });
  const results=scope&&!body.length?empty(scope.complete?'没有符合条件的关联活动品牌':'当前无法确认符合条件的关联活动品牌',
   scope.complete?(f.q?'当前搜索范围内没有已关联活动的品牌。':'当前范围内没有已关联活动的品牌。'):'关联资料尚不完整，请稍后重新查询。'):
   table(['品牌','关联活动','品牌利益点','上月交易额','操作'],body)+pager(p,route,f);
  return `<section class="panel">${common}${tabs}${filterForm(filters)}<p id="brand-scope" class="hint">每品牌最多展示 4 条活动，完整清单见详情。</p>${results}</section>`;
 }
 if(view==='product'){
  const rows=filterProducts(data,f);const p=paginate(rows,Number(f.page));
  const incomplete=(data.activityIntake??data.meta?.dataOrigin==='activity_workbench')&&data.complete!==true;
  const linkedOnly=data.activityIntake===true;
  const emptyRelations=(linkedOnly||incomplete)&&!p.rows.length;
  return `<section class="panel">${common}${tabs}${filterForm(input('q','搜索商品',f.q,'名称 / 内部码 / UPC')+input('activityQ','搜索活动',f.activityQ,'活动名称 / 内部或平台活动 ID')+select('brand','品牌',data.brands,f.brand)+select('platform','平台',data.platforms??Object.values(PLATFORM_LABELS),f.platform)+(linkedOnly?'':select('relation','关联情况',[{id:'yes',name:'有关联活动'},{id:'no',name:'无关联活动'}],f.relation)))}<div class="actions">${btn('↓ 下载全部商品','export')}${data.productExportReason?`<span class="hint" id="product-export-reason">${h(data.productExportReason)}</span>`: '<span class="hint">Excel 包含当前筛选下全部页的已确认商品关联。</span>'}</div><p class="hint">包含已结束的历史活动。</p>${emptyRelations?empty(incomplete?'暂未查到已关联活动的商品':'当前条件下没有已关联活动的商品',incomplete?'关联来源尚不完整；附件待采用候选请在单活动详情查看。':'只展示当前条件下已关联活动的商品。',(f.q||f.activityQ)?link(routeLink(route,{...f,q:'',activityQ:'',page:1}),'清空搜索','button'):''):table(['商品（名称 / 规格 / 内部码）','品牌','关联活动','活动价 / 适用券档','近 4 周销量','状态','平台','活动时间','操作'],p.rows.flatMap(p=>{
   const rel=p.relations;const open=expanded.has(p.code);
   const prices=rel.map(r=>`<div>${rel.length>1?`<small>${h(r.activity.name)}</small>`:''}${relationBenefitHtml(r)}</div>`).join('')||'—';
   const activities=rel.map(r=>`<div>${link(activityLink(r.activity,currentHash),r.activity.name)}<div>${categoryAndInitiator(r.activity,r)}</div></div>`).join('');
   const countLabel=!data.complete||p.relationComplete===false?'已知关联':rel.length+' 个活动';
   const row=td(`<strong>${h(p.name)}</strong><small>${h(p.spec)}</small><small class="mono">${h(p.code)}${p.upc?' · UPC '+h(p.upc):''}</small>`,h(brandName(data,p.brandId)),rel.length?countLabel+activities:(p.relationComplete===false?'关联待核对':'暂无关联记录'),prices,data.activityIntake?salesRecentHtml(p.code,rel):'—',rel.length?rel.map(r=>productRelationStatus(r,rel.length>1)).join(''):'不适用',h([...new Set(rel.flatMap(relationPlatforms))].join('、')||'不适用'),rel.map(r=>period(r.activity)).join('<br>')||'不适用',rel.length?`<button data-expand="${h(p.code)}" aria-expanded="${open}" aria-controls="expand-${h(p.code)}">${open?'收起':'展开'}</button>`:'—');
   return [row,...(open?[`<tr id="expand-${h(p.code)}"><td colspan="9" class="expanded"><h3>关联活动（${incomplete?'数量待核实':rel.length}） · ${h(p.code)}</h3>${table(['活动名称 / 内部ID','活动方式 / 发起方','活动状态','内部进度','平台','活动时间','活动价 / 适用券档','操作'],rel.map(r=>{const a=r.activity;return td(`<strong>${h(a.name)}</strong><small>${h(a.id)}</small>${r.relationLabel?badge(r.relationLabel,'blue'):''}`,categoryAndInitiator(a,r),productRelationStatus(r),badge(a.reviewLabel,'blue'),h(relationPlatforms(r).join('、')),period(a),relationBenefitHtml(r,true),link(activityLink(a,currentHash),'查看活动','button small'))}))}<p class="hint">平台进度按已保存的报名与检查记录显示。</p></td></tr>`]:[])]
  }))}<p class="hint">${linkedOnly?'只展示已确认关联活动的商品；':''}同一商品参与多个活动时，按活动分别展示。</p>${pager(p,route,f,{unknownTotal:incomplete})}<div id="download-result" role="status"></div></section>`;
 }
 const rows=filterActivities(data,{...f,today:todayBeijing()});const p=paginate(rows,Number(f.page));
 const initiator = typeCode(f.type) ?? '';
 const initiatorTabs = [['', '全部活动'], ...Object.entries(TYPE_LABELS)].map(([code, label]) =>
  link(routeLink(route, {...f, type:code, page:1}), label, initiator === code ? 'selected' : '')
 ).join('');
 const activityFilters = `<input type="hidden" name="type" value="${h(initiator)}">`
  + input('q','搜索活动或ID',f.q,'活动名称 / 内部活动ID')+select('platform','平台',data.platforms??Object.values(PLATFORM_LABELS),f.platform)+select('brand','品牌',data.brands,f.brand)+select('status','内部审核',[...((data.activityIntake??data.meta?.dataOrigin==='activity_workbench')?['待安排审核']:[]),'待初审','复审中','终审中','审核通过','审核不通过'],f.status)+select('expiry','期限',[{id:'active',name:'未过期'},{id:'expired',name:'已过期'}],f.expiry)+(data.platformMaterialsView?select('platformProgressState','平台进度',data.platformMaterialsView.filters??[],f.platformProgressState):'')
  + (Object.hasOwn(ACTIVITY_CATEGORY_LABELS, f.activityCategory)
    ? select('activityCategory','活动方式',Object.entries(ACTIVITY_CATEGORY_LABELS).map(([id,name])=>({id,name})),f.activityCategory)
    : '');
 return `<section class="panel">${common}${tabs}<nav class="type-tabs" aria-label="活动发起方">${initiatorTabs}</nav>${filterForm(activityFilters)}${table(['状态','活动名称 · 等级','活动方式 / 发起方','涉及品牌','平台','优惠机制','提交人','活动时间','操作'],activityRows(p.rows,data,currentHash,currentHash.replace(/^#demo\//,'#')))}${pager(p,route,f)}</section>`;
}
function material(a){return info([['提报编号',h(a.submissionId)],['提交人 / 受理时间',h(a.submitter)+' / '+stamp(a.submittedAt)],['材料来源','来源材料原文（数据性质见页头）'],['来源平台活动ID',h(a.platformId??'待核实')]])+`<h3>原始文字材料</h3><blockquote>${h(a.rawText)}</blockquote><h3>附件及证据</h3><p class="muted">未提供真实文件，附件待接入。暂无可下载原文件；字段证据定位待接入。</p>`}
function history(a){return table(['版本','环节','结果','操作人 / 当时角色','时间（北京）','意见 / 依据'],a.history.map(x=>td('V'+x.version,h(x.node),badge(x.result,x.result==='通过'?'green':'red'),h(x.actor)+' / '+h(x.role),stamp(x.at),h(x.reason))))+(a.history.length?'':'<p class="hint">暂无审核记录。</p>')}
export const productRowKey=row=>JSON.stringify([row.candidateId,row.rowId]);
export const registrationDecision=row=>row?.decision??(row?.selected?'planned':'pending');
// Display-only de-duplication. It never changes matching, units or source data.
export function productSpecText(row){
 const candidate=row.candidates?.length===1?row.candidates[0]:null;
 const spec=candidate?.spec||row.rawSpec;
 if(!spec)return '规格见详情';
 const normalize=value=>String(value).normalize('NFKC').replace(/\s+/gu,'').toLowerCase();
 const name=normalize(candidate?.name||row.rawName||''),normalized=normalize(spec);
 const escaped=normalized.replace(/[.*+?^${}()|[\]\\]/g,'\\$&');
 // Numeric/package boundaries prevent 20ml from matching 120ml or 20ml*3.
 return new RegExp(`(^|[^0-9a-z.*×])${escaped}(?![0-9a-z.*×]|/\\d)`,'u').test(name)?'':spec;
}

function originalProductNameHtml(rawName,displayName){
 return rawName?.trim()&&rawName.trim()!==displayName.trim()?
  `<small class="product-original-name">原表：${h(rawName)}</small>`:'';
}

export function catalogIdentityHtml(row,{evidence=false}={}){
 const identity=row?.catalogIdentity;
 if(identity?.status!=='exact_internal_code_upc')return '';
 const ids=new Set(identity.evidenceIds??[]);
 const refs=(row.sourceReferences??[]).filter(ref=>ids.has(ref.evidenceId));
 return `<small data-catalog-identity>${badge('内部码与 UPC 核对一致','green')}</small>${evidence?`<details><summary>查看内部码与 UPC 依据</summary>${refs.map(ref=>`<p>${h(ref.locator?.sheet||'原件')} ${h(ref.locator?.cell||'')}：${h(ref.quote)}</p>`).join('')}</details>`:''}`;
}
function catalogDetailStatus(row){
 const differences=new Set((row.candidates??[]).flatMap(item=>item.detailsToCheck??[]));
 const labels=[differences.has('item_name')?'名称':null,differences.has('spec')?'规格':null].filter(Boolean);
 const status={source_conflict:'目录来源存在冲突',material_code_conflict:'原表内部码与目录不一致',multiple_candidates:'目录存在多个候选',intake_review_required:'附件内容待核对',identifier_not_text:'标识原文格式待核对',invalid_upc:'UPC 格式待核对',missing_upc:'未提供 UPC',unmatched:'当前快照未找到'}[row.directoryStatus];
 return [status,labels.length?labels.join('、')+'差异待核对':null,row.materialMatchKind==='conflict'?'原表内容差异待核对':null].filter(Boolean).join('；')||'待正式关联';
}
export function catalogIdentitySummaryHtml(result){
 if(result?.status!=='ready'||result.catalogIdentityRuleVersion!=='catalog-identity/1.0')return '';
 const rows=result.rows??[];
 const identities=rows.filter(row=>row.catalogIdentity?.status==='exact_internal_code_upc').length;
 const candidates=rows.filter(row=>row.candidates?.length&&!row.catalogIdentity).length;
 const missing=rows.filter(row=>row.directoryStatus==='unmatched').length;
 const other=rows.length-identities-candidates-missing;
 const counts=[
  `附件商品 ${rows.length} 条`,
  identities?`<strong>原表内部码与 UPC 核对一致 ${identities} 条</strong>`:'',
  candidates?`找到目录候选 ${candidates} 条，待核对`:'',
  missing?`当前目录快照未匹配 ${missing} 条`:'',
  other>0?`其他待核对 ${other} 条`:''
 ].filter(Boolean);
 return `<div data-catalog-identity-summary><p>${counts.join(' · ')}</p><p class="hint">公司目录快照 ${h(result.catalog?.sourceDate||'日期待核实')}。候选商品需核对名称和规格后再关联。</p></div>`;
}
export function submissionProductsHtml(result,participation,sourceRows=[]){
 if(result?.status!=='ready'){
  const notice='<p class="hint">公司目录核对结果暂未取得，请稍后刷新；原分析结果保留。</p>';
  if(!participation?.size)return notice;
  return notice+table(['原表商品','UPC','原表报名情况'],sourceRows.map(row=>
   td(h(row.rawName||'名称未提供'),h(row.rawUpc||'未提供'),h(participation.get(row.rowId)??'—'))));
 }
 const matched=(result.rows??[]).filter(row=>row.candidates?.length);
 const other=(result.rows??[]).filter(row=>!row.candidates?.length);
 const render=row=>td(
  `<strong>${h(row.rawName||'名称未提供')}</strong><small>UPC：${h(row.rawUpc||'未提供')}</small><small>原表内部码：${h(row.rawInternalCode||'未填写')}</small>`,
  (row.candidates??[]).map(item=>`<p>${h(item.name)}<small>内部码：${h(item.internalCode)}</small><small>规格：${h(item.spec||'未提供')}</small></p>`).join('')||'当前快照没有目录候选',
  catalogIdentityHtml(row,{evidence:true})+`<p>${h(catalogDetailStatus(row))}</p>`,
  ...(participation?.size?[participation.has(row.rowId)?h(participation.get(row.rowId)):'—']:[]));
 const detail=(title,rows)=>rows.length?`<details class="product-recognition-group"><summary>${title}（${rows.length}）</summary>${table(['原表商品','公司目录候选','核对结果',...(participation?.size?['原表报名情况']:[])],rows.map(render))}</details>`:'';
 return catalogIdentitySummaryHtml(result)+detail('查看有目录依据的商品',matched)+detail('查看未匹配及其他待核对商品',other);
}

export function productRecognitionHtml(result,draft=null){
 if(draft?.reviewSnapshot?.status==='finalized'){
  const rows=draft.formalList?.rows??draft.reviewSnapshot.finalRows??[];
  const identified=new Map((result?.rows??[]).map(row=>[productRowKey(row),row]));
  return `<div data-product-recognition class="product-r5">${registrationDraftHtml(draft)}
   ${table(['商品 / 规格','品牌','内部码 / UPC','报名价','活动价','参考供货价','试算毛利','近 4 周销量','在售门店','操作'],rows.map(row=>{
    const product=row.productSnapshot??{};
    const relation={participationPrices:[row]};
    const key=productRowKey(row), match=identified.get(key);
    const source=match?.candidates?.some(item=>item.internalCode===row.internalCode)?match:null;
    const quote=source?.agentSupplyPrices, metrics=source?.operatingMetrics;
    const quantity=metrics?.sales28d?.status==='ready'?metrics.sales28d.quantity:null;
    const stores=metrics?.activeStores?.status==='ready'?metrics.activeStores.count:null;
    const rawName=row.sourceRow?.rawName;
    const displayName=product.name||rawName||'名称未提供';
    return td(`<strong>${h(displayName)}</strong>${originalProductNameHtml(rawName,displayName)}<small>${h(product.spec)}</small>`,
     h(product.brand||'—'),h([row.internalCode,product.upc||row.sourceRow?.rawUpc].filter(Boolean).join(' · ')),
     participationPriceHtml(relation,'signupPrice'),participationPriceHtml(relation,'activityPrice'),
     h(quote?.status==='reference_only'?quote.referenceQuote?.amountDecimal??'—':'—'),
     h(metrics?.trialMargin?.status==='ready'?metrics.trialMargin.amountDecimal??'—':'—'),
     row.internalCode&&['meituan','taobao_instant'].includes(draft.platformCode)?salesRecentHtml(row.internalCode,[{platformCodes:[draft.platformCode]}]):h(quantity??'—'),
     h(stores??'暂未取得'),
     source?`<button type="button" class="product-detail-link" data-product-compare="${h(key)}">详情</button>`:'—');
   }))}</div>`;
 }
 if(result?.status!=='ready')return `<div data-product-recognition>${registrationDraftHtml(draft)}<p class="hint">商品目录暂不可用，已审核清单与下载仍按保存的版本提供。</p></div>`;
 const wholeActivity=draft?.methodSupported===false;
 const groups={candidates:[],unmatched:[],review:[]};
 for(const row of result.rows)groups[wholeActivity||row.candidates.length?'candidates':row.directoryStatus==='unmatched'?'unmatched':'review'].push(row);
 const pageSize=draft?.pageSize??16,totalPages=Math.max(1,Math.ceil(groups.candidates.length/pageSize));
 const page=Math.min(Math.max(1,draft?.page??1),totalPages);
 const renderRow=row=>{
  const key=productRowKey(row),saved=draft?.rows?.find(r=>productRowKey(r)===key),decision=registrationDecision(saved);
  const op=draft?.rowOperations?.[key],editable=!!draft?.canEdit&&!draft.error&&!draft.stale&&!draft.legacyPending&&!op?.busy&&!op?.pending;
  const p=row.candidates.length===1?row.candidates[0]:null,quote=row.agentSupplyPrices;
  const displayName=p?.name||row.rawName||'名称未提供';
  const spec=productSpecText(row);
  const number=(value)=>value==null?'—':h(value);
  const price=['needs_review','ready'].includes(row.activityPriceCandidate?.status)?row.activityPriceCandidate.amountDecimal:null;
  const signupPrice=row.signupPriceCandidate;
  const signup=['ready','needs_review'].includes(signupPrice?.status)&&typeof signupPrice.amountDecimal==='string'&&
   /^[0-9]{1,24}(?:\.[0-9]{1,16})?$/.test(signupPrice.amountDecimal)?signupPrice.amountDecimal:null;
  const supply=quote?.status==='reference_only'?quote.referenceQuote?.amountDecimal:null;
  const trial=row.operatingMetrics?.trialMargin,stores=row.operatingMetrics?.activeStores;
  const sales=selectedPlatformSales(row,draft?.platformCode);
  const recorded=sales?.status==='missing_data'&&sales.recordedQty!=null?`已记录 ${h(sales.recordedQty)} 件`:null;
  const salesText=sales?.status==='ready'?`${number(sales.quantity)} 件`:recorded??
   h(sales?.statusLabel??(row.uniqueUpcHit&&['meituan','taobao_instant'].includes(draft?.platformCode)?'查询中…':'待核对商品'));
  const decisions=signup!==null?[['planned','拟报名'],['declined','不报名']]:
   [...(decision==='planned'?[[decision,'撤销已选']]:[]),['declined','不报名']];
  const actions=`${signup===null?'<span class="b-required">报名价待核对</span>':''}
   ${decisions.length?`<div class="product-decisions" role="group" aria-label="${h(displayName)} 报名状态">${decisions.map(([value,label])=>`<button type="button" data-row-decision="${value}" data-row-key="${h(key)}" aria-pressed="${decision===value}"${editable?'':' disabled'}>${label}</button>`).join('')}</div>`:''}
   ${op?.busy?'<small role="status">正在保存…</small>':op?.message?`<small class="product-row-feedback" role="status">${h(op.message)}</small>`:''}
   ${op?.pending?`<button type="button" class="product-detail-link" data-row-retry="${h(key)}"${op.busy?' disabled':''}>核对结果 / 重试</button>`:''}`;
  return `<tr data-product-row="${h(key)}">${[
   `<strong class="product-name">${h(displayName)}</strong>${originalProductNameHtml(row.rawName,displayName)}${spec?`<small>${h(spec)}</small>`:''}<small class="product-internal-code">内部码：${h(p?.internalCode??'—')}</small>${catalogIdentityHtml(row)}${row.catalogIdentity?`<small>${h(catalogDetailStatus(row))}</small>`:''}`,
   h(candidateBrand(row)||'—'),number(signup),price==null&&decision==='planned'?'待核活动价':number(price),number(supply),
   trial?.status==='ready'?`${number(trial.amountDecimal)}<small class="product-margin-rate">${trial.ratePercentDecimal!=null?h(trial.ratePercentDecimal)+'%':'—'}</small>`:trialMissingLabel(trial),
   salesText+(sales?.comparison?`<small>${h(sales.comparison)}</small>`:''),
   stores?.status==='ready'?number(stores.count):'暂未取得',...(!wholeActivity?[actions]:[]),
   `<button type="button" class="product-detail-link" data-product-compare="${h(key)}" aria-label="查看${h(displayName)}详情">详情</button>`
  ].map((cell,index)=>`<td${index>=2&&index<=7?' class="product-number"':''}>${cell}</td>`).join('')}</tr>`;
 };
 const sorted=sortCandidateRows(groups.candidates,draft?.sort,draft?.platformCode),visible=sorted.slice((page-1)*pageSize,page*pageSize);
 const others=(key,label)=>groups[key].length?`<details class="product-recognition-group" data-recognition-group="${key}"><summary>${label}（${groups[key].length}）</summary><ul class="product-unmatched">${groups[key].map(row=>`<li><span>${h(row.rawName||'名称未提供')}<small>UPC ${h(row.rawUpc||'未提供')}</small></span><button type="button" class="product-detail-link" data-product-compare="${h(productRowKey(row))}">详情</button></li>`).join('')}</ul></details>`:'';
 return `<div data-product-recognition class="product-r5"><div class="product-context"><strong data-product-candidate-count>${wholeActivity?'活动商品':'商品候选'} ${groups.candidates.length} 条</strong>${wholeActivity?'':'<span>待采用</span>'}</div>${catalogIdentitySummaryHtml(result)}${registrationDraftHtml(draft)}${candidateTableHtml(visible,draft?.sort,renderRow,wholeActivity)}<div class="product-table-footer"><p class="hint" data-trial-margin-note>试算毛利 = 活动价 − 参考供货价 · 仅供选品参考，适用门店及计价单位待核实 · — 暂无数据 · 再点已选状态可撤销</p><div class="product-pager" aria-label="候选商品分页"><button type="button" data-product-page="${page-1}" aria-label="商品上一页"${page===1?' disabled':''}>‹</button><span data-product-page-label>${page} / ${totalPages}</span><button type="button" data-product-page="${page+1}" aria-label="商品下一页"${page===totalPages?' disabled':''}>›</button></div></div>${others('unmatched','未匹配商品')}${others('review','其他待补信息')}</div>`;
}
function candidateTableHtml(rows,sort,renderRow,wholeActivity){
 const columns=[['商品'],['品牌','brand'],['报名价'],['活动价','activityPrice'],['参考供货价'],['试算毛利','margin'],['近4周销量','sales28d'],['在售门店'],...(!wholeActivity?[['报名状态']]:[]),['操作']];
 const heading=columns.map(([label,field])=>{
  const selected=field&&field===sort?.field;
  return `<th scope="col"${selected?` aria-sort="${sort.direction==='desc'?'descending':'ascending'}"`:''}>${field?`<button type="button" class="product-sort-button" data-product-sort="${field}" aria-label="按${h(label)}${selected&&sort.direction==='asc'?'降序':'升序'}排序">${h(label)} ${selected?(sort.direction==='asc'?'↑':'↓'):'↕'}</button>`:h(label)}${field==='margin'?'<small>金额 / 毛利率</small>':''}</th>`;
 }).join('');
 return `<div class="table-wrap"><table aria-label="活动候选商品"><colgroup>${[27,8,8,10,10,9,7,15,6].map(()=>`<col>`).join('')}</colgroup><thead><tr>${heading}</tr></thead><tbody>${rows.map(renderRow).join('')}</tbody></table></div>`;
}
export function agentSupplyPriceHtml(price,metadata){
 const quote=price?.referenceQuote;
 if(price?.status==='reference_only'&&typeof quote?.amountDecimal==='string'){
  const source=metadata?.source??{};
  const currency=quote.currency==='CNY'?'¥':quote.currency?quote.currency+' ':'';
  return `<strong>参考报价 ${h(currency+quote.amountDecimal)}</strong><small>适用门店待核实</small><small>${h(quote.pricingUnit||'计价单位未核实')}${quote.currency?'':' · 币种未标注'}</small><details><summary>查看报价来源</summary><p>数据 Agent · ${h(source.fileName||'商品主档')}<br>${h(source.snapshotDate||'日期待核实')} 快照 · 供货价列 · 第 ${h(quote.sourceRowNumber)} 行<br>原值：${h(quote.rawAmount)}<br>规格：${h(quote.spec)}</p><p>税口径与价格有效期未核实；此报价可用于选品试算，不作为正式门店毛利的成本依据。</p></details>`;
 }
 if(price?.status==='conflict')return '<span>报价需核对商品身份</span>';
 if(price?.status==='missing'&&metadata?.status==='ready')return '<span>暂无报价</span>';
 return '<span>报价暂未取得</span>';
}
export function marginHtml(row){
 const trial=row.operatingMetrics?.trialMargin;
 const formal=row.operatingMetrics?.margin;
 const formalNote=formal?.status==='ready'&&formal.amountDecimal!=null?`正式门店毛利：${h(formal.amountDecimal)}（按已确认收入与供货价口径）。`:'正式门店毛利尚缺已确认归店收入与门店供货价。';
 if(trial?.status==='ready'&&trial.amountDecimal!=null){
  const activitySources=(trial.activityPriceSources??[]).map(source=>`${h(source.locator?.sheet||'附件')} ${h(source.locator?.cell||'')}：${h(source.rawText)}`).join('<br>');
  const quote=trial.referenceQuoteSource??{};
  const currency=trial.currency==='CNY'?'¥':trial.currency?trial.currency+' ':'';
  return `<strong>${h(currency+trial.amountDecimal)}</strong><details><summary>查看试算出处</summary><p>${h(trial.activityPriceDecimal)} − ${h(trial.referenceQuoteDecimal)} = ${h(trial.amountDecimal)}</p><p>试算毛利率：${trial.ratePercentDecimal!=null?`${h(trial.amountDecimal)} ÷ ${h(trial.activityPriceDecimal)} × 100 = ${h(trial.ratePercentDecimal)}%`:'活动价须大于零，当前不计算'}</p><p>附件活动价：<br>${activitySources}</p><p>Agent参考报价：${h(quote.fileName||'商品主档')}<br>${h(quote.snapshotDate||'日期待核实')} · 供货价列 · 第${h(quote.sourceRowNumber)}行<br>原值：${h(quote.rawAmount)} · 目录规格：${h(quote.spec)}</p><p>${formalNote}</p></details>`;
 }
 const reason={TRIAL_SPEC_CONFLICT:'附件与报价的已知商品规格不一致，不能试算。',TRIAL_CURRENCY_CONFLICT:'两笔价格的已知币种不一致，不能试算。',TRIAL_UNIT_CONFLICT:'两笔价格的计价单位无法对应，不能试算。'}[trial?.reasonCode]||'单件活动价、参考报价或同商品来源依据尚不完整，暂不能试算。';
 return `<span>—</span><details><summary>查看缺项</summary><p>${h(reason)}</p><p>${formalNote}</p></details>`;
}
export function salesHtml(row){
 const sales=row.operatingMetrics?.sales28d;
 if(sales?.status==='ready'&&sales.quantity!=null)return `<strong>${h(sales.quantity)} 件</strong><small>${h(PLATFORM_LABELS[sales.platformCode]??'美团')} · 商品日报原销量</small><small>${h(sales.comparison??'退款扣减口径待核实')}</small><details><summary>查看销量口径</summary><p>${h(sales.startDate)} 至 ${h(sales.endDate)}（北京时间）<br>${h(sales.sourceSummary)}</p></details>`;
 if(sales?.status==='partial')return `<span>销量暂未取得</span><small>28天中${h(sales.recordedDays)}天有值；缺失不按0</small>`;
 if(sales?.status==='stale')return '<span>销量暂未取得</span><small>近28日数据待刷新</small>';
 if(sales?.statusLabel)return `<span>${h(sales.recordedQty!=null?`已记录 ${sales.recordedQty} 件`:sales.statusLabel)}</span><small>${h(sales.comparison??'')}</small>`;
 return '<span>销量暂未取得</span><small>近28个完整自然日；缺失不按0</small>';
}

function trialMissingLabel(trial){
 return ({TRIAL_SPEC_CONFLICT:'规格待核对',TRIAL_CURRENCY_CONFLICT:'币种待核对',
  TRIAL_UNIT_CONFLICT:'计价单位待核对'}[trial?.reasonCode]??'暂不能试算');
}
export function activeStoresHtml(row){
 const stores=row.operatingMetrics?.activeStores;
 if(stores?.status==='ready'&&stores.count!=null)return `<strong>${h(stores.count)} 家</strong><small>统计于 ${h(stamp(stores.asOf))}</small>`;
 return '<span>在售门店数暂未取得</span><small>按当前平台在售门店去重</small>';
}

export function activityPriceHtml(price){
 const amount=price?.status==='needs_review'&&typeof price.amountDecimal==='string'?price.amountDecimal:null;
 const status={missing:'附件未提供活动价',conflict:'两表活动价冲突',formula:'活动价为公式，待核对',invalid_amount:'活动价格式待核对',unbound:'活动价行待核对'}[price?.status]??'暂无可用活动价';
 const loc=source=>`${source?.locator?.sheet??'原件'} ${source?.locator?.cell??''}`;
 const observations=price?.observations??[];
 return `${amount!==null?`<strong>${h(amount)}</strong><small>附件活动价 · 待核对</small><small>${h(price.currency??'币种未标注')} · ${h(({single_item:'单件',bundle:'组包'})[price.quantityBasis]??'单位待核对')}</small>`:`<span>${h(status)}</span>`}${observations.length?`<details><summary>查看活动价出处</summary>${observations.map(o=>`<p>${(o.priceCells??[]).map(c=>`<small>${h(loc(c.source))}：${h(c.source?.rawText)}</small>`).join('')}</p>`).join('')}</details>`:''}`;
}
function signupPriceHtml(price){
 const amount=price?.status==='needs_review'?price.amountDecimal:null;
 const status={conflict:'两表报名价冲突',formula:'报名价为公式，待核对',invalid_amount:'报名价格式待核对'}[price?.status]??'暂无可用报名价';
 const observations=(price?.observations??[]).filter(item=>item.source);
 return (amount==null?`<p>${h(status)}</p>`:`<p><strong>${h(amount)}</strong></p>`)+
  (observations.length?`<details><summary>查看报名价出处</summary>${observations.map(item=>`<p>${h(item.source.locator?.sheet)} ${h(item.source.locator?.cell)} · ${h(item.source.rawText)}</p>`).join('')}</details>`:'');
}
function registrationDraftHtml(draft){
 const names={meituan:'美团',taobao_instant:'淘宝闪购',jd:'京东'};
 if(draft?.methodSupported===false)return `<div class="product-toolbar"><span>按原活动商品范围参与</span>
  ${draft.platforms?.length>1?`<label class="product-platform">平台 <select data-draft-platform>${draft.platforms.map(code=>`<option value="${h(code)}"${code===draft.platformCode?' selected':''}>${h(names[code]??code)}</option>`).join('')}</select></label>`:''}</div>`;
 const rows=draft?.rows??[],pending=draft?.legacyPending||Object.values(draft?.rowOperations??{}).some(op=>op.busy||op.pending);
 const finalized=draft?.reviewSnapshot?.status==='finalized';
 const summary=finalized?`<b>终审确认商品</b> · ${(draft.formalList?.rows??draft.reviewSnapshot.finalRows??[]).length} 项`:
  `<b>报名状态</b> · 拟报名 <strong data-selected-count>${rows.filter(r=>registrationDecision(r)==='planned').length}</strong> 项 · 不报名 ${rows.filter(r=>registrationDecision(r)==='declined').length} 项`;
 return `<div class="registration-draft product-toolbar"><span>${summary}</span><div class="row"><label class="product-platform">平台 <select data-draft-platform${pending?' disabled':''}>${(draft?.platforms??[]).map(code=>`<option value="${h(code)}"${code===draft.platformCode?' selected':''}>${h(names[code]??code)}</option>`).join('')}</select></label><button type="button" data-draft-reload${pending?' disabled':''}>刷新状态</button><button type="button" class="product-enroll" data-product-enroll${!draft||draft.error||draft.stale||pending?' disabled':''}>内部待报名清单</button></div></div>${draft?.legacyPending?'<p class="warning">旧版草稿保存结果尚未确认。<button type="button" data-legacy-retry>核对旧保存结果 / 重试</button></p>':''}${draft?.error?`<p role="alert" class="warning">${h(draft.error)}</p>`:''}${draft?.message?`<p role="status" class="hint">${h(draft.message)}</p>`:''}${draft?.stale?'<p class="warning">商品来源已变化，原报名状态保留，暂不能套用到新来源。</p>':''}${draft&&!draft.canEdit&&!draft.error&&!finalized?`<p class="hint">${draft.methodSupported===false?(draft.activityMethod?'此活动按活动办理，不开放逐商品报名。':'活动方式待确认，逐商品报名暂未开放。'):draft.reviewSnapshot?'初审清单已冻结，终审可填写原因移除；新增或替换需退回重审。':'当前账号没有此范围的报名状态编辑权限。'}</p>`:''}`;
}
export function productComparisonHtml(row,unused,result){
 const candidates=row.candidates??[],code=candidates.map(p=>p.internalCode),raw=row.rawInternalCode;
 return `<div class="section-heading"><h2>商品详情</h2><button type="button" data-product-compare-close>关闭</button></div><div class="two-col"><section class="soft"><h3>附件商品</h3><p>${h(row.rawName||'名称未提供')}</p>${row.rawSpec?`<p>规格：${h(row.rawSpec)}</p>`:''}</section><section class="soft"><h3>公司目录候选</h3>${candidates.map(p=>`<p>${h(p.name)}<small>规格：${h(p.spec||'未提供')}</small></p>`).join('')||'<p>当前快照没有目录候选。</p>'}</section></div><p>UPC：${h(row.rawUpc||'未提供')}</p><p>内部码：${code.map(h).join('、')||'未匹配'}</p>${raw&&!code.includes(raw)?`<p>原表内部码：${h(raw)}</p>`:''}${catalogIdentityHtml(row,{evidence:true})}<p>${h(catalogDetailStatus(row))}</p><p class="hint">附件与目录名称按原文保留；报名意向不改变商品匹配或审核结果。</p><details class="product-evidence"><summary>来源与数据缺项</summary><h3>附件与目录来源</h3>${(row.sourceReferences??[]).map(r=>`<p><small>${h(r.locator?.sheet)} ${h(r.locator?.cell)} · ${h(r.quote)}</small></p>`).join('')}<p>目录快照：${h(result?.catalog?.sourceDate||'日期待核实')} · 同步于 ${h(stamp(result?.catalog?.lastFetchedAt))}${result?.catalog?.syncStale?' · 待更新':''}</p><h3>附件报名价</h3>${signupPriceHtml(row.signupPriceCandidate)}<h3>附件活动价</h3>${activityPriceHtml(row.activityPriceCandidate)}<h3>Agent 参考供货价</h3>${agentSupplyPriceHtml(row.agentSupplyPrices,result?.supplyPriceSource)}<h3>试算毛利与毛利率</h3>${marginHtml(row)}<h3>近4周销量</h3>${salesHtml(row)}<h3>在售门店数</h3>${activeStoresHtml(row)}</details>`;
}
export function registrationSummaryHtml(draft,recognition){
 const selected=(draft?.rows??[]).filter(row=>registrationDecision(row)==='planned');
 const names={meituan:'美团',taobao_instant:'淘宝闪购',jd:'京东'};
 return `<div class="section-heading"><h2>生成待报名清单</h2><button type="button" data-product-compare-close>关闭</button></div><p>${h(names[draft?.platformCode]??draft?.platformCode)} · 跨页拟报名 ${selected.length} 项</p><ul class="product-registration-summary">${selected.map(row=>{const source=recognition.rows.find(r=>productRowKey(r)===productRowKey(row));return `<li>${h(source?.rawName||row.productSnapshot?.name||row.rowId)} <span>拟报名</span></li>`}).join('')}</ul><h3>尚缺条件</h3><ul>${(!selected.length?['先将商品状态设为拟报名']:[]).concat(draft?.formalList?.reasons??[]).concat(draft?.canEdit?[]:['当前账号没有此范围的编辑权限']).map(reason=>`<li>${h(reason)}</li>`).join('')}</ul><p class="hint">内部清单依照审核与适用范围生成，由门店运营到平台提交。</p><div class="actions"><button type="button" data-product-compare-close>返回选品</button><button type="button" class="product-enroll" disabled>生成清单</button></div>`;
}

const EMPTY_BUSINESS_TEXT=new Set(['待核实','待核对','待补充','未提供','无信息','规则待核实','活动规则待核实','未提供，待核实','未提供,待核实','费用承担方、金额、单位与来源待核实']);
function businessText(value){
 if(typeof value==='number')return Number.isFinite(value)?String(value):'';
 if(typeof value!=='string')return '';
 const text=value.trim();return EMPTY_BUSINESS_TEXT.has(text)?'':text;
}
export function listValue(value){
 const text=businessText(value);
 return !text||['平台待核实','类型待核实','品牌待核实','未记录','未评级'].includes(text)?'—':text;
}
function activityMethodSummary(activity){
 return activity.activityMethod==='shen_jia'&&activity.activityMethodConfirmation?.status==='confirmed'&&
  [null,undefined,'meituan'].includes(activity.platformCode)?'美团神价':'';
}
export function activityShortSummary(activity){
 const rule=businessText(activity.rules)||businessText(activity.ruleSummary)||businessText(activity.mechanism);
 const mechanism='神价活动按报名价减平台补贴计算活动价。';
 const text=rule.startsWith(mechanism)?mechanism:rule;
 return text?(text.length>64?text.slice(0,64)+'…':text):(activityMethodSummary(activity)||'—');
}
function listPeriod(activity,data){
 const supplement=data.supplementDates?.(activity);
 if(supplement?.label)return h(supplement.label);
 if(!activity.start&&!activity.end&&!activity.activityDateEvidence)return '—';
 return h(datePeriodText(activity.start,activity.end,activity.activityDateEvidence,'—'));
}
export function activityExecutionProgressHtml(data,activity,back){
 const platforms=data.platformMaterialsView?.detail(activity,back,{embedded:true});
 return `<p>报名：${h(activity.registration?.label??'暂时无法确认')}</p>${platforms||`<p>上线：${h(activity.launchLabel??'暂时无法确认')}</p>`}`;
}
function shenjiaRuleHtml(activity,recognition,rule){
 const mechanism='神价活动按报名价减平台补贴计算活动价。';
 if(!rule.startsWith(mechanism))return `<p>${h(rule)}</p>`;
 const rows=recognition?.status==='ready'&&recognition.binding?.activityId===activity.id&&
  recognition.binding.versionId===(activity.displayVersionId??activity.currentReviewVersionId)?recognition.rows:[];
 const prices=rows.map(row=>row.activityPriceCandidate).filter(price=>['ready','needs_review'].includes(price?.status)&&/^\d{1,24}(?:\.\d{1,16})?$/.test(price.amountDecimal??''));
 // Compare exact decimals without floating-point rounding or inventing currency.
 const scale=Math.max(0,...prices.map(price=>(price.amountDecimal.split('.')[1]??'').length));
 const value=price=>{const [integer,fraction='']=price.amountDecimal.split('.');return BigInt(integer+fraction.padEnd(scale,'0'))};
 const comparable=prices.length&&new Set(prices.map(price=>JSON.stringify([price.currency,price.quantityBasis]))).size===1;
 const ordered=comparable?[...prices].sort((a,b)=>value(a)<value(b)?-1:value(a)>value(b)?1:0):[];
 const first=ordered[0],last=ordered.at(-1);
 const range=first?(value(first)===value(last)?first.amountDecimal:first.amountDecimal+'～'+last.amountDecimal):'';
 const unit=first?.currency&&first?.quantityBasis?`${first.currency} / ${{single_item:'件',bundle:'组'}[first.quantityBasis]??first.quantityBasis}`:'币种及计价单位待核实';
 return `<p>${h(mechanism)}</p>${range?`<p>已识别活动价：${h(range)} <small>（${h(unit)}）</small></p>`:''}<details><summary>查看逐项优惠与原文依据</summary><p>${h(rule)}</p><p class="hint">单个商品的价格、数量及原文位置可在上方商品「详情」中查看。</p></details>`;
}
export function activityBenefitsHtml(activity,recognition=null,couponTableHtml=null){
 const rule=businessText(activity.rules)||businessText(activity.ruleSummary)||businessText(activity.mechanism);
 const benefit=businessText(activity.brandBenefit)||businessText(activity.benefit);
 const cost=activity.costEvidence??{};
 const state={yes:'公司承担：有',no:'公司承担：无'}[cost.state];
 const amount=businessText(cost.value),unit=businessText(cost.unit),source=businessText(cost.sourceRef);
 const method=activityMethodSummary(activity);
 const hasTiers=Array.isArray(activity.couponTiers)&&activity.couponTiers.length>0;
 const coupons=couponTableHtml??couponTiersHtml(hasTiers?{tiers:activity.couponTiers}:activity.couponSchedule);
 const consumer=[coupons+(activity.couponSchedule?.showOriginalRules&&rule?shenjiaRuleHtml(activity,recognition,rule):'')||
  (rule?shenjiaRuleHtml(activity,recognition,rule):method?`<p>${h(method)}</p>`:''),state?`<p>${h(state)}</p>`:'',
  amount?`<p>已记录金额：${h(amount)}${unit?' '+h(unit):'（单位待核对）'}</p>`:'',
  amount&&['unknown','unconfirmed'].includes(cost.status)?'<p class="hint">金额口径待核对</p>':'',
  (state||amount)&&source?`<p class="hint">来源：${h(source)}</p>`:''].join('');
 const card=(kind,title,body)=>`<div class="soft benefit-card${body?'':' is-empty'}" data-benefit-card="${kind}"><h3>${h(title)}</h3>${body||'<p>无信息</p>'}</div>`;
 return `<div class="${coupons?'coupon-benefits':'two-col'} benefit-cards">${card('consumer','消费者优惠',consumer)}${card('brand','品牌给价探的利益点',benefit?`<p>${h(benefit)}</p>`:'')}</div>`;
}
export function couponTiersHtml(schedule,today=todayBeijing(),renderChoice=null){
 if(!schedule?.tiers?.length)return '';
 return table(['券档','平台','出资','原表商品范围','有效期（北京时间）','状态','审核选择'],schedule.tiers.map(tier=>{
  const {range,label,state}=couponPeriod(tier,today);
  const name=couponValue(tier);
  const platform=PLATFORM_LABELS[tier.platformCode]??tier.platformLabel??tier.channel??'待核对';
  const products=tier.sourceProducts?.length?tier.sourceProducts:(tier.sourceUpcs??[]).map(upc=>({upc}));
  const count=tier.productCount??tier.productRowIds?.length??products.length;
  let scope=tier.productScopeStatus==='source_unmarked'||tier.sourceProductsUnmarked?'原表未标注参券商品':
   count>0?'原表 '+count+' 项':'范围待核对';
  const located=products.length>0&&products.every(product=>product.sourceReference?.couponCell&&product.sourceReference.sheet);
  if(located&&tier.productScopeStatus!=='source_unmarked'&&!tier.sourceProductsUnmarked){
   const rowKeys=new Set(products.map(({sourceReference:ref})=>JSON.stringify([ref.materialId,ref.sheet,ref.row])));
   const upcs=new Set(products.map(product=>product.upc??product.rawUpc).filter(Boolean));
   scope='原表 '+rowKeys.size+' 行 · '+upcs.size+' 个 UPC';
  }
  if(tier.productScopeStatus==='source_partial')scope+=' · 部分商品待核对';
  const productsHtml=products.length?'<details><summary>'+h(scope)+'</summary><ul>'+
   products.map(product=>{
    const ref=product.sourceReference;
    const cells=ref?.couponCell&&ref.sheet?[ref.sheet,'券档 '+ref.couponCell,ref.upcCell?'UPC '+ref.upcCell:null,ref.nameCell?'商品名称 '+ref.nameCell:null].filter(Boolean).join(' · '):'';
    return '<li>'+h([product.rawName??product.name,product.upc??product.rawUpc].filter(Boolean).join(' · ')||'商品名称待核对')+(cells?'<small>'+h(cells)+'</small>':'')+'</li>';
   }).join('')+'</ul></details>':h(scope);
  return td(h(name)+(tier.name&&tier.name!==name?'<small>'+h(tier.name)+'</small>':''),
   h(platform),h(couponFundingText(tier))+(tier.submitterFundingNote?`<small>提报人出资备注：${h(tier.submitterFundingNote)}</small>`:''),productsHtml,h(range),
   badge(label,{current:'green',upcoming:'blue',unknown:'amber'}[state]??'neutral'),renderChoice?renderChoice(tier,name,platform):h(couponDecision(tier)));
 }),'coupon-tiers');
}
function productDetail(data,a){
 if(a.activityCategory==='threshold_coupon'){
  const applicable=data.products.flatMap(product=>{
   const relation=data.relations.find(r=>r.activityId===a.id&&r.code===product.code);
   return relation?[td(h(product.name),h(product.code),h(product.upc??'—'),relationBenefitHtml({...relation,activity:a},true))]:[];
  });
  if(!applicable.length){
   const scope=a.productListState==='scope_only'?verifiedProductScopeLabel(a):'暂无已确认关联的商品';
   const recognition=data.productRecognition;
   const matches=recognition?.status==='ready'&&recognition.rows?.length?
    '<details><summary>附件识别商品（'+recognition.rows.length+'）</summary>'+submissionProductsHtml(recognition)+'</details>':'';
   return '<p>'+h(scope)+'</p><p class="hint">各档原表商品可在「优惠与价盘」查看。</p>'+matches;
  }
  return (applicable.length>5?btn('查看全部','all-products'):'')+'<div id="detail-products" class="five-rows">'+table(['商品','内部码','UPC','适用券档'],applicable)+'</div>';
 }
 if(data.registrationDraft?.reviewSnapshot)return productRecognitionHtml(data.productRecognition??{status:'unavailable'},data.registrationDraft);
 if(data.productRecognition?.status==='ready')return productRecognitionHtml(data.productRecognition,data.registrationDraft);
 if(data.productRecognition?.status==='unavailable')return '<p class="warning">本次商品识别暂不可用，原附件已保留。请稍后刷新；下方仍按原审核版本的商品关联展示。</p>'+productDetail({...data,productRecognition:null},a);
 if(a.productListState==='scope_only')return `<p class="hint">${h(verifiedProductScopeLabel(a))}。</p>`;
 const codes=new Set(data.relations.filter(r=>r.activityId===a.id).map(r=>r.code));
 const products=data.products.filter(p=>codes.has(p.code));
 if(data.activityProductsPartial&&!products.length)return '<p class="hint">当前暂无已核商品，商品总数待核实。</p>';
 const summary=data.activityProductsPartial?'仅展示已核商品，商品总数待核实':
  data.complete?`共 ${products.length} 个已知商品`:'商品关系不完整，数量待核实';
 return `<p class="hint">${summary}；首屏5行，表体内滚动。历史销量来源未接入，当前未按销量排序，缺失不记0。</p>${products.length>5?btn('查看全部','all-products'):''}<div id="detail-products" class="five-rows">${table(['商品 / 规格','内部码','UPC','最近一次活动','历史销量 / 期间','活动价'],products.map(p=>{const rel=data.relations.find(r=>r.code===p.code&&r.activityId===a.id);const metric=rel?.historySales;const latest=rel?.latestActivity;const sales=metric?.value==null?'待接入 · 期间待核实':h(metric.value)+' '+h(metric.unit??'单位待核实')+'<small>'+h(metric.periodStart??'期间待核实')+' — '+h(metric.periodEnd??'期间待核实')+'</small>';const price=rel?.participationPrices?.length?participationPriceHtml(rel,'activityPrice'):rel?.activityPrice==null?'待接入':h(rel.activityPrice)+' '+h(rel.currency??'币种待核实')+' / '+h(rel.priceUnit??'单位待核实');return td(`<strong>${h(p.name)}</strong><small>${h(p.spec)}</small>`,h(p.code),h(p.upc),latest?h(latest.name)+'<small>'+h(latest.id)+'</small>':'待接入',sales,price)}))}</div>`}
export function activityDetail(data,a,back,review=false,reviewBack=null){
 const backUrl=back?.startsWith('#demo/')?back:review?'#demo/reviews':'#demo/activity';
 const backToReviews=review||backUrl.split('?')[0]==='#demo/reviews';
 const files=new Map();
 for(const input of data.authorizedSource?.version?.sourceInputs??[]){
  const ids=new Set(input.attachmentMaterialIds??[]);
  for(const file of input.materials??[]){
   if(ids.has(file.materialId)&&file.available!==false&&file.downloadAvailable)files.set(file.materialId,file);
  }
 }
 const fileLinks=[...files.values()].map(file=>`<button type="button" class="small" data-material-download="${h(file.materialId)}">${h(file.originalName??file.filename??'下载原件')}</button>`).join(' ');
 const heading=`${link(backUrl,'← '+(backToReviews?'返回审核待办':'返回查询列表'))}${title(a.name,'',badge(a.grade??'未评级')+(reviewBack?ratingButton(a):'')+reviewActionButton(a,reviewBack),activityProjectBadge(a))}`;
 const dateSupplement=review?null:data.supplementDates?.(a);
 const pending=pendingItemLabels((a.pendingItems??[]).filter(item=>!dateSupplement?.fields.includes(item.field)),a.activityDateEvidence);
 const signup=registrationDateText(a.registrationDateEvidence);
 const declarations=hasSubmissionDeclarations(a)?[
  ['适用平台',h(applicablePlatformCodes(a,a.platformCode).map(code=>APPLICABLE_PLATFORM_LABELS[code]).join('、')||'未选择，待确认')],
  ['商品适用范围（提报声明）',h(productScopeIntentLabel(a.productScopeIntent))],
  ['商品范围核验',h(verifiedProductScopeLabel(a))]
 ]:[];
 const brands=(a.brandIds?.length?a.brandIds:[a.brandId]).filter(Boolean);
 const methodFields=a.projectName&&!activityProjectBadge(a)?[['项目名称',h(a.projectName)]]:[];
 const snapshot=data.authorizedSource?.version?.snapshot;
 const storeBearing=(snapshot?.resolvedFields??snapshot?.confirmedFields)?.storeBearing;
 const storeCost=storeBearing==='yes'?'门店出钱':storeBearing==='no'?'门店不出钱':null;
 const overview=info([['活动方式',h(activityCategoryLabel(a.activityCategory))],['发起方',h(initiatorTypeLabel(a.initiatorType??typeCode(a.type)))],...methodFields,['平台',h(activityPlatformLabel(a)||'无信息')],['活动时间',dateSupplement?.html??period(a)],...(storeCost?[['门店是否出钱',h(storeCost)]]:[]),['品牌',brands.length?brands.map(id=>link(routeLink('demo/brand/'+id,{back:backUrl}),brandName(data,id))).join('、'):'未关联品牌，可后补'],['提交人',data.activitySubmitters?.length?data.activitySubmitters.map(h).join('、'):h(a.submitter??'未记录')]]);
 const detailFields=`<details class="activity-field-details"><summary>查看范围与完整资料</summary>${info([['活动版本','V'+h(a.version??'待核实')],['来源平台',h(a.platform)],['本次范围',h(a.scope)],...declarations,...(signup?[['材料报名时间',h(signup)]]:[])])}${data.dateEvidenceHtml?.(a)??''}${pending.length?`<p>待补充：${h(pending.join('、'))}</p>`:''}</details>`;
 const couponControls=data.couponReviewControlsHtml??null;
 const localWorkbench=couponControls!==null||(data.activityIntake??data.meta?.dataOrigin==='activity_workbench');
 const rules=section('rules','优惠、价盘与费用依据',activityBenefitsHtml(a,data.productRecognition,couponControls));
 const showGoodsFirst=a.activityCategory!=='threshold_coupon'&&data.productRecognition?.status==='ready';
 const goodsBody=subsidyPricesHtml(data.subsidyPrices)+productDetail(data,a);
 const goods=section('goods','活动商品明细',goodsBody,data.activityProductsPartial?'':`<div class="actions">${btn('↓ 下载全部商品明细','export-activity')}</div>`);
 const materials=section('materials',(data.activityIntake??data.meta?.dataOrigin==='activity_workbench')?'原提交信息':'来源材料与本次补充',material(a)+`<div class="soft"><h3>本次补充与旧拒绝原因对照</h3><p>${h(a.changeNote)}</p><p>旧版原因：${h(a.history.find(x=>x.result==='不通过')?.reason??'无拒绝记录')}</p><p class="hint">本次变更不会覆盖原生效版本；重新提审起点和变更生效边界待确认。</p></div>`);
 const process=section('process','审核进度','<p>当前：'+badge(a.reviewLabel,'blue')+'</p>');
 return `<section class="panel">${heading}${data.reviewRequirementsHtml??''}${overview}${fileLinks?'<p>提报原件　'+fileLinks+'</p>':''}${detailFields}<nav class="anchors" aria-label="详情模块">${['overview','rules','goods','materials','process','history'].map((id,i)=>`<button data-anchor="${id}">${['活动信息','优惠与价盘','商品明细',(data.activityIntake??data.meta?.dataOrigin==='activity_workbench')?'原提交信息':'来源材料','审核处理','操作历史'][i]}</button>`).join('')}</nav></section>${showGoodsFirst?goods:''}${localWorkbench?`<div id="overview" class="review-main-column">${rules}${showGoodsFirst?'':goods}${process}${materials}</div>`:`<div id="overview" class="detail-grid"><div>${rules}${showGoodsFirst?'':goods}${materials}</div><div class="review-side">${process}</div></div>`}${section('history','审核与操作历史',data.activityHistoryHtml??history(a))}<div id="download-result" role="status"></div>`;
}
export function brandAssociationSourcesHtml(activity){
 const sources=activity.brandAssociation?.sources??[];
 const labels=[...new Set(sources.map(source=>source.kind==='company_catalog_candidate'?
  `公司目录 ${source.catalogDate??'日期待核实'} · 内部码 ${source.internalCode??'待核实'}`:
  source.kind==='attachment_brand_column'?`附件品牌列 · ${source.locator?.sheet??''} ${source.locator?.cell??''} · ${source.quote??''}`:
  source.kind==='adopted_product_brand'?`已关联商品 ${source.productId}`:`版本已确认品牌 ${source.versionId??''}`))];
 return `<details><summary>查看关联出处</summary><p>${h(activity.mechanism??activity.rules??'无信息')}</p><ul>${labels.map(text=>`<li>${h(text)}</li>`).join('')}</ul></details>`;
}

export function brandDetail(data,b,back){
 const activities=data.activities.filter(a=>(a.brandIds??[a.brandId]).includes(b.id));
 const today=todayBeijing();
 const brandScope=data.brandActivities;
 const pendingAssociations=brandScope?.items.filter(a=>a.brandAssociation?.status==='pending')??[];
 const current=brandScope?brandScope.items.filter(a=>a.brandAssociation?.status!=='pending'&&a.activityPeriodState==='current'):
  data.detailModules?.currentActivities?.available===false?[]:activities.filter(a=>a.end>=today);
 const confirmed=brandScope?.items.filter(a=>a.brandAssociation?.status!=='pending')??[];
 const upcoming=confirmed.filter(a=>a.activityPeriodState==='upcoming');
 const expired=confirmed.filter(a=>a.activityPeriodState==='expired');
 const undated=brandScope?.items.filter(a=>a.brandAssociation?.status!=='pending'&&(!a.activityPeriodState||a.activityPeriodState==='unknown'))??[];
 const products=data.products.filter(p=>p.brandId===b.id);
 const url=back?.startsWith('#demo/')?back:'#demo/brand';
 const heads=['状态','活动名称 · 等级','活动方式 / 发起方','品牌','平台','优惠','提交人','时间','操作'];
 const related=products.flatMap(p=>activityRelations(data,p.code).map(r=>td(
  `${h(p.name)}<small>${h([p.code,p.upc].filter(Boolean).join(' · '))}</small>`,
  link(activityLink(r.activity,'#demo/brand/'+b.id),r.activity.name)+(r.relationLabel?badge(r.relationLabel,'blue'):''),
  period(r.activity),r.activity.activityCategory==='threshold_coupon'?'不适用':participationPriceHtml(r,'signupPrice'),relationBenefitHtml(r,true),data.activityIntake?salesRecentHtml(p.code,[r]):'—'
 )));
 const choices=products.flatMap(p=>salesPlatforms(activityRelations(data,p.code)).map(platform=>
  `<option value="${h(JSON.stringify([p.code,platform]))}">${h(p.name||p.code)} · ${h(PLATFORM_LABELS[platform])}</option>`)).join('');
 const sales=choices?`<form id="product-sales-history" class="filters"><label>商品与平台 <select name="product">${choices}</select></label><button type="submit" class="button">查询最近 3 个月</button></form><div data-sales-history-result role="status"></div>`:'<p class="hint">暂无可查询销量的关联商品。</p>';
 const intro=`<section class="panel">${link(url,'← 返回品牌列表')}${title(b.name+' · 品牌详情',b.identityStatus==='candidate_label'?'品牌身份待确认':b.id,link('#demo/report','提报活动材料','button primary'))}${info([['品牌利益点',h(b.benefit??'待核实')],['上月交易额','待接入 · 统计口径待确认']])}<nav class="anchors" aria-label="品牌页导航">${[['current','当前活动'],['related','关联商品'],['previous','上月回顾'],['sales','单商品销量']].map(([id,l])=>`<button data-anchor="${id}">${l}</button>`).join('')}</nav></section>`;
 const scopedRows=items=>activityRows(items.map(a=>({...a,brandId:b.id,brandIds:[b.id]})),data,'#demo/brand/'+b.id);
 return intro
 +section('current','当前活动',brandScope?
  (current.length?table(heads,scopedRows(current)):
   empty(brandScope.complete?'当前没有已确认在活动期内的活动':'当前未能确认在活动期内的关联活动'))+
  (upcoming.length?'<h3>即将开始</h3>'+table(heads,scopedRows(upcoming)):'')+
  (undated.length?'<h3>活动日期待核</h3>'+table(heads,scopedRows(undated)):''):
  data.detailModules?.currentActivities?.available===false?empty('当前活动统计待接入',data.detailModules.currentActivities.blockedReason??'统计口径或数据源尚未接入，暂不汇总。'):table(heads,activityRows(current,data,'#demo/brand/'+b.id)))
 +section('related','关联商品',related.length?table(['商品 / 内部码 / UPC','参与活动','活动期间','报名价','活动价 / 适用券档','近 4 周销量'],related):'<p class="hint">暂无关联商品。</p>')
 +(expired.length?section('expired','已结束的活动',table(heads,scopedRows(expired))):'')
 +(pendingAssociations.length?section('pending','待确认关联（'+pendingAssociations.length+'）',table(['活动','关联状态','出处','操作'],pendingAssociations.map(a=>td(h(a.name),badge('待确认','amber'),brandAssociationSourcesHtml(a),link(activityLink(a,'#demo/brand/'+b.id),'查看同一活动','button small'))))+'<p class="hint">仅为原附件或公司目录候选中的品牌线索，不计入已确认活动数。</p>'):'')
 +section('previous','上月活动回顾',empty('上月回顾待接入',data.detailModules?.lastMonthReview?.blockedReason??'统计口径及数据源尚未接入；已知关联活动可从列表或关联商品查看。'))
 +section('sales','单商品历史销量',data.activityIntake?sales:'<p class="hint">销量数据待接入。</p>');
}
