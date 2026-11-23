// Frozen JT019 r1 canonical DTO -> presentation, never a fallback dataset.
export const supportsActivityIntake=capabilities=>
 capabilities?.groupReview?.contractVersion==='intake-group-review/2.0'&&
 ['1.0.6','1.0.7','1.0.8'].includes(capabilities.groupReview.localAgentVersion);
export const TYPE_LABELS={brand:'品牌活动',platform:'平台活动',jiatan_sourcing:'价探招商'};
export const ACTIVITY_CATEGORY_LABELS={product_discount:'商品折扣',threshold_coupon:'满减活动'};
export const activityCategoryLabel=code=>ACTIVITY_CATEGORY_LABELS[code]??'待确认';
export const initiatorTypeLabel=code=>TYPE_LABELS[code]??'发起方待核对';
export function couponFundingText(tier){
 const mode={brand_only:'品牌单补',store_only:'门店全额承担',platform_only:'平台全额承担',company_only:'公司全额承担',
  brand_store:'品牌与门店两方补贴',brand_store_platform:'品牌、门店与平台三方补贴',shared:'多方共同承担'}[tier.fundingMode];
 const parties={brand:'品牌',store:'门店',platform:'平台',company:'公司'};
 const shares=(tier.fundingShares??[]).flatMap(share=>{
  const label=parties[share.party];
  if(!label||share.percent==null&&share.amount==null)return [];
  const amounts=[share.percent==null?'':share.percent+'%',share.amount==null?'':share.amount+' 元'].filter(Boolean);
  return [label+' '+amounts.join(' / ')];
 });
 return [mode,...shares].filter(Boolean).join(' · ')||
  ({required:'门店出资',none:'门店不出资'}[tier.storeFundingStatus]??'出资待核对');
}
export const PLATFORM_LABELS={meituan:'美团',taobao_instant:'淘宝闪购',jd:'京东'};
export const APPLICABLE_PLATFORM_LABELS=PLATFORM_LABELS;
export const PRODUCT_SCOPE_INTENT_LABELS={deferred:'后续补上',all_products:'全部商品',specific_products:'指定商品',no_specific_products:'无需指定商品'};
export function applicablePlatformCodes(input,identity=input?.form?.platformCode){
 if(Object.hasOwn(input??{},'applicablePlatformCodes')){
  const codes=input.applicablePlatformCodes;
  return Array.isArray(codes)?Object.keys(APPLICABLE_PLATFORM_LABELS).filter(code=>codes.includes(code)):[];
 }
 return Object.hasOwn(PLATFORM_LABELS,identity)?[identity]:[];
}
export function productScopeIntentLabel(code){
 if(code==='deferred')return '后续补上 · 待核对';
 return Object.hasOwn(PRODUCT_SCOPE_INTENT_LABELS,code)?PRODUCT_SCOPE_INTENT_LABELS[code]:'未填写';
}
export function verifiedProductScopeLabel(input){
 if(input?.productListState==='scope_only'||input?.productApplicability?.kind==='all_products'){
  const platforms=(input?.productApplicability?.platformCodes??[]).map(code=>APPLICABLE_PLATFORM_LABELS[code]).filter(Boolean);
  const kind=input?.initiatorType==='brand'||input?.type==='brand'||(input?.resolvedFields??input?.confirmedFields)?.type==='brand'?'品牌全部商品':'全部商品';
  return platforms.length?'已确认 '+platforms.join('、')+' · '+kind:'已记录全商品范围，未逐项列出商品';
 }
 if(input?.productApplicability?.kind==='explicit_skus')return '已核对指定商品';
 if(input?.productApplicability?.kind==='no_specific_products')return '已确认无需指定商品';
 return '当前未提供已核对范围';
}
export const hasSubmissionDeclarations=input=>Object.hasOwn(input??{},'applicablePlatformCodes')||Object.hasOwn(input??{},'productScopeIntent');
export const ROLE_LABELS={operations:'门店运营',merchandising:'商品',management:'管理',marketing:'营销',business_development:'招商',supply_chain:'供应链',pricing:'价盘',finance:'财务',other:'其他'};
export const platformCode=v=>Object.entries(PLATFORM_LABELS).find(([,label])=>label===v)?.[0]??v;
export const typeCode=v=>Object.entries(TYPE_LABELS).find(([,label])=>label===v)?.[0]??v;
export function beijing(value){if(!value)return null;const d=new Date(value);if(Number.isNaN(d.getTime()))return '时间待核实';return new Intl.DateTimeFormat('sv-SE',{timeZone:'Asia/Shanghai',dateStyle:'short',timeStyle:'medium'}).format(d)}
// Activity dates are Beijing calendar days. Record timestamps still use beijing().
export function activityDate(value){
 const formatted=beijing(value);
 return formatted&&formatted!=='时间待核实'?formatted.slice(0,10):null;
}
const DATE_ISSUES={
 ACTIVITY_DATE_YEAR_UNCONFIRMED:'年份待核',
 ACTIVITY_DATE_CONFLICT:'日期冲突待核',
 ACTIVITY_DATE_INVALID:'原文日期待核',
 ACTIVITY_DATE_SOURCE_UNVERIFIED:'日期来源待核'
};
function sourceDay(entry){
 const day=entry?.on;
 if(typeof day==='string'&&/^\d{4}-\d{2}-\d{2}$/.test(day))return day;
 const part=typeof entry?.monthDay==='string'&&/^(0[1-9]|1[0-2])-(0[1-9]|[12][0-9]|3[01])$/.exec(entry.monthDay);
 return part?`${Number(part[1])}月${Number(part[2])}日`:null;
}
export function datePeriodText(start,end,evidence,placeholder='时间待补'){
 const values=[start,end],keys=['start','end'];
 const days=values.map((value,i)=>{
  if(value)return value;
  const key=keys[i],entry=evidence?.[key];
  const issue=evidence?.issues?.find(item=>item.field===key);
  const label=DATE_ISSUES[issue?.reasonCode];
  const day=sourceDay(entry);
  if(!day)return label??placeholder;
  return label?`${day}（${label}）`:day;
 });
 return days.join(' — ');
}
export function registrationDateText(evidence){
 if(!evidence)return null;
 const start=sourceDay(evidence.start),end=sourceDay(evidence.end);
 if(!start&&!end)return DATE_ISSUES[evidence.issues?.[0]?.reasonCode]??null;
 if(start===end||!start||!end){
  const field=start?'start':'end';
  const label=DATE_ISSUES[evidence.issues?.find(item=>item.field===field)?.reasonCode];
  return label?`${start??end}（${label}）`:start??end;
 }
 return datePeriodText(null,null,evidence,'待核实');
}
const PENDING_LABELS={
 rulesText:'优惠机制',platformCode:'报名编号所属平台',sourceActivityId:'平台活动编号',
 startAt:'开始日期',endAt:'结束日期',storeBearing:'门店是否出资',productApplicability:'商品适用范围',subsidyPrices:'商品补贴价格',activityName:'平台正式活动名称',brandIds:'品牌'
};
export function pendingItemLabels(items,dateEvidence){
 return [...new Set((Array.isArray(items)?items:[]).map(item=>{
  const label=Object.hasOwn(PENDING_LABELS,item?.field)?PENDING_LABELS[item.field]:'资料待核对';
  const side={startAt:'start',endAt:'end'}[item?.field];
  const issue=side&&dateEvidence?.issues?.find(item=>item.field===side);
  const dateLabel=DATE_ISSUES[issue?.reasonCode];
  return dateLabel?`${label}（${dateLabel}）`:label;
 }))];
}
export function awaitingReview(state,reason){
 return state==='not_started'&&['ASSIGNMENT_REQUIRED','ASSIGNEE_ROLE_MISMATCH'].includes(reason);
}
export function reviewLabel(value){const r=typeof value==='string'?{stage:value.includes(':')?value.split(':')[0]:null,result:value.split(':').at(-1)}:value??{};if(r.result==='approved')return '审核通过';if(r.result==='rejected')return '审核不通过';if(r.approvalMode==='single_management'&&r.stage==='initial')return '待管理审核';return {initial:'待初审',pricing:'复审中',final:'终审中'}[r.stage]??'审核待核实'}
export function factLabel(value){return {unknown:'暂时无法确认',not_registered:'未报名',registered:'已报名',pending:'待平台确认',approved:'平台已确认',rejected:'平台未通过',live:'已上线'}[value]??(value?'状态待核实':'暂时无法确认')}
export function activityDTO(a){const ver=a.versions?.find(v=>v.id===a.currentReviewVersionId)??a.versions?.at(-1);const sub=a.submissions?.find(s=>s.id===ver?.submissionId);return {
 ...a,initiatorType:a.initiatorType??a.type,activityCategory:a.activityCategory??null,type:TYPE_LABELS[a.initiatorType??a.type]??'发起方待核对',platform:PLATFORM_LABELS[a.platformCode]??'平台待核实',brandId:a.brandIds?.[0]??null,brandIds:a.brandIds??[],grade:a.level??null,start:activityDate(a.startAt),end:activityDate(a.endAt),reviewLabel:awaitingReview(a.reviewState,a.reviewBlockedReason)?'待安排审核':reviewLabel(a.review),registration:{label:factLabel(a.registration?.state),source:a.registration?.sourceRef??null,checkedAt:a.registration?.checkedAt??null},launchLabel:factLabel(a.execution?.state),mechanism:a.rules??a.ruleSummary??'规则待核实',submitter:a.submitter?.displayName??'未记录',platformId:a.externalIds?.map(x=>`${PLATFORM_LABELS[x.platformCode]??x.platformCode}：${x.id}`).join('；')||null,initiator:a.initiator?.displayName??a.initiator??'待核实',benefit:a.brandBenefit??null,costNote:a.costEvidence?.sourceRef?JSON.stringify(a.costEvidence):'费用承担方、金额、单位与来源待核实',version:ver?.versionNo??null,submissionId:ver?.submissionId??null,scope:a.changeScope??'本次范围待核实',rawText:sub?.rawText??'原始输入待接入',attachments:a.materials??[],changeNote:a.changeSummary??'本次补充待核实',history:(a.history??[]).map(x=>({version:x.versionNo??a.versions?.find(v=>v.id===x.versionId)?.versionNo??'待核实',node:{initial:'初审',pricing:'复审',final:'终审'}[x.stage??x.node]??x.stage??x.node??'未记录',result:{approved:'通过',rejected:'不通过'}[x.result]??x.result??'未记录',actor:x.actorDisplayName??x.actor?.displayName??x.actorId??'未记录',role:ROLE_LABELS[x.actorRole??x.roleAtTime]??'未记录',at:x.decidedAt??x.createdAt??x.at??null,reason:x.opinion??x.reason??'未记录',sourceKind:x.sourceKind}))
}}
export const productDTO=p=>({...p,code:p.internalCode});
export const activityPeriodLabel=state=>({upcoming:'未开始',current:'进行中',expired:'已结束'}[state]??'时间待核实');
export function productPlatformProgress(items,platformCodes=[]){
 const stages={pending_registration:'待报名',pending_check:'待检查',pending_recheck:'待复查',retrospective:'复盘'};
 const states={live:'已上线',returned:'待补报名资料',pending_materials:'待报名',pending_verification:'待检查'};
 if(!Array.isArray(items))return [];
 return items.filter(item=>item&&(!platformCodes.length||platformCodes.includes(item.platformCode))).flatMap(item=>{
  const label=stages[item.lifecycleStage]??states[item.state];
  const platform=APPLICABLE_PLATFORM_LABELS[item.platformCode]??PLATFORM_LABELS[item.platformCode];
  return label&&platform?[`${platform} · ${label}`]:[];
 });
}
export function queryFilters(view,f={}, {linkedOnly=false}={}){
 const q={};if(f.q)q.q=f.q;
 if(view==='activity'){
  if(f.type)q.type=typeCode(f.type);if(Object.hasOwn(ACTIVITY_CATEGORY_LABELS,f.activityCategory))q.activityCategory=f.activityCategory;if(f.platform)q.platformCode=platformCode(f.platform);if(f.brand)q.brandId=f.brand;
  if(f.status){const r={'待初审':['initial','pending'],'复审中':['pricing','pending'],'终审中':['final','pending'],'审核通过':[null,'approved'],'审核不通过':[null,'rejected']}[f.status];if(r){if(r[0])q.reviewStage=r[0];q.reviewResult=r[1]}}
  if(f.expiry)q.expiryState=f.expiry==='expired'?'expired':'current';
 }else if(view==='product'){
  if(f.platform)q.platformCode=platformCode(f.platform);if(f.brand)q.brandId=f.brand;if(f.activityQ?.trim())q.activityQ=f.activityQ.trim();if(linkedOnly){q.hasActivities='yes';q.association='linked'}else if(f.relation)q.association=f.relation==='yes'?'linked':'unlinked';
 }
 return q;
}
export function snapshotCell(key,value){
 if(value===null||value===undefined)return '';
 if(key==='activityCategory')return activityCategoryLabel(value);
 if(key==='activityType'||key==='initiatorType')return TYPE_LABELS[value]??value;
 if(key==='platformCode')return String(value).split(',').map(code=>APPLICABLE_PLATFORM_LABELS[code]??PLATFORM_LABELS[code]??code).join('、');
 if(key==='review')return reviewLabel(value);
 if(key==='activityPeriodState')return activityPeriodLabel(value);
 if(key==='platformProgress')return productPlatformProgress(JSON.parse(value)).join('；')||'待核实';
 if(key==='startAt'||key==='endAt')return activityDate(value)??'';
 if(key==='registrationCheckedAt')return beijing(value)??'';
 return String(value);
}
