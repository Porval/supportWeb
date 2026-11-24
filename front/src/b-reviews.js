import {activityBenefitsHtml,couponTiersHtml,routeLink} from './ui.js';
import {readReviewRegistration,reviewRegistrationHtml,captureRegistrationReview,registrationReviewRequest,productReviewEnabled} from './b-review-registration.js';
import {supplementVersionHtml} from './b-group-review.js';
import {activityCategoryLabel,activityDate,datePeriodText,registrationDateText,hasSubmissionDeclarations,awaitingReview,pendingItemLabels,verifiedProductScopeLabel} from './dto.js';
import {subsidyPricesHtml} from './b-subsidy-prices.js';
import {h,badge,rows,table,input,textarea,select,panel,pager,errorPanel,stateLabel,beijing,ROLE_LABELS,actionAllowed,evidenceList,diagnostics} from './b-common.js';
import {declarationRecordHtml,factsHtml,materialHtml,FIELD_LABELS,fieldValueText,bearingText} from './b-fields.js';
import {bearingVerificationHtml,blockedReasonsHtml,confirmedComparisonHtml,productConfirmationHtml,historyEventLabel,historyDetailsHtml,nodeResultLabel} from './b-business.js';
import {factLabel} from './dto.js';
import {dateEvidenceHtml,versionSourcesHtml,ownSubmissionLink} from './b-sources.js';

const singleManagement=review=>review?.approvalMode==='single_management';
const reviewStageNames={initial:'管理初审',pricing:'价盘复审',final:'管理终审'};
const requirementLabels={
 productApplicability:['商品适用范围','请确认券适用于全部商品、指定商品，还是无需指定商品。选择指定商品时，请核对商品清单。'],
 storeBearing:['门店是否出资','请确认门店是否承担券补贴。'],
 subsidyPrices:['单品补贴价格','请核对有缺失或冲突的商品，补齐系统活动价、申请补贴、补贴后单价及修正说明。']
};
function approvalRequirements(version){
 const {stage,state}=version?.review??{};
 if(!Object.hasOwn(reviewStageNames,stage)||state!==stage+'_pending')return [];
 return (version.reviewApprovalRequirements??[]).filter(item=>Object.hasOwn(requirementLabels,item.field));
}
function approvalStage(version){
 return singleManagement(version.review)?'管理审核':reviewStageNames[version.review.stage];
}
export function reviewRequirementsHtml(version,session){
 const requirements=approvalRequirements(version);
 if(!requirements.length)return '';
 const source=version.sourceInputs?.find(input=>input.submissionId===version.submissionId);
 const canSupplement=source?.submitter?.id!=null&&source.submitter.id===session?.user?.id&&!session?.readOnly&&
  ['submissions.read_own','activities.propose'].every(permission=>session.permissions?.includes(permission));
 const items=requirements.map(({field})=>{
  const [name,instruction]=requirementLabels[field];
  const url=canSupplement?routeLink('submissions/'+encodeURIComponent(source.submissionId),{supplementVersion:version.id,field}):null;
  return `<li><strong>${h(name)}</strong> · ${h(instruction)}${url?` <a href="${h(url)}">去填写${h(name)}</a>`:''}</li>`;
 }).join('');
 const canReject=actionAllowed(version,'reviews.decide')&&session?.permissions?.includes('reviews.decide')&&!session?.readOnly;
 return `<section class="b-manual-attention" aria-label="审核前待补信息"><h3>需提报人补充，暂不能通过${h(approvalStage(version))}</h3><ul>${items}</ul>${canSupplement?'<p>请在原提报中补齐以上信息，保存后查看新版本的审核进度。</p>':canReject?'<p>可填写打回原因，由原提报人补充。</p>':''}</section>`;
}
function checkApprovalRequirements(version){
 const items=approvalRequirements(version);
 if(items.length)throw new Error('请先由提报人补充'+items.map(({field})=>requirementLabels[field][0]).join('、')+'，再通过'+approvalStage(version)+'。');
}
function couponDraftRevision(review){
 return review?.draftRevision??review?.selection?.revision??0;
}

export const needsStoreBearingConfirmation=review=>Boolean(review&&review.storeBearingPolicy!=='per_tier'&&!review.storeBearingConfirmed);

export function couponReviewHtml(review,editable=false,selection,stage=review?.selectionStage){
 if(!review)return '';
 const enabled=editable&&!review.selectionLocked;
 const options=review.options??[];
 const hasDraft=review.hasDraft===true||review.selection!=null;
 const defaults=review.defaultSelectedCouponIds??options.filter(option=>option.storeFundingStatus==='none'&&!option.sourceProductsUnmarked&&option.productScopeStatus!=='source_unmarked').map(option=>option.tierId);
 const reducing=review.selectionMode==='reduce_only';
 const sourceScope=stage==='final'?'复审保留':'初审已选';
 const eligible=reducing?new Set(review.eligibleCouponIds??[]):null;
 const selectable=eligible?options.filter(option=>eligible.has(option.tierId)):options;
 const saved=review.selection?.selectedCouponIds??review.selectedCouponIds??[];
 const selected=review.selectionLocked?(review.selectedCouponIds??[]):
  (enabled?selection:null)??(reducing||hasDraft?saved:(enabled?defaults:[]));
 const selectedIds=new Set(selected);
 const allChecked=selectable.length>0&&selectable.every(option=>selectedIds.has(option.tierId));
 const content=options.length?couponTiersHtml({tiers:options},undefined,(tier,value,platform)=>{
  if(eligible&&!eligible.has(tier.tierId))return `<span class="hint">${stage==='final'?'复审未保留':'初审未选'}</span>`;
  const name=[platform,value,tier.name!==value?tier.name:null].filter(Boolean).join(' · ');
  return `<input type="checkbox" aria-label="${h('拟参加 '+name)}" name="reviewCoupon" value="${h(tier.tierId)}"${selectedIds.has(tier.tierId)?' checked':''}${enabled?'':' disabled'}>`;
 }):'<p class="warning">本版本缺少可核对的券明细。</p>';
 return `<fieldset class="b-review-coupons"><legend>${reducing?(stage==='final'?'管理终审':'价盘复审')+'保留的满减券':'拟参加的满减券'}</legend>
  ${enabled&&selectable.length?`<div class="b-coupon-toolbar"><button type="button" class="small" data-review-coupon-all>${allChecked?'取消全选':'全选'}</button>
   <button type="button" class="small" data-review-coupon-save>保存勾选</button></div>`:''}
  ${needsStoreBearingConfirmation(review)?'<p class="warning">通过审核前，请由提报人确认门店是否出资。</p>':''}
  ${enabled&&reducing?`<p class="hint">可取消${sourceScope}的券档。${stage==='final'?'终审通过后，选券清单即确定。':'复审通过时，以本次保留的券档为准。'}</p>`:
    enabled&&!hasDraft&&defaults.length?`<p class="hint">已自动勾选 ${defaults.length} 档门店无需出资的券，可调整后保存。</p>`:''}
  ${content}
  ${review.selectionLocked?`<p class="hint">审核已确认 ${selected.length} 档。</p>`:hasDraft?`<p class="hint">已保存 ${(review.selection?.selectedCouponIds??review.selectedCouponIds??[]).length} 档拟报名券。</p>`:''}</fieldset>`;
}

function validateCouponScope(review,selected,stage){
 if(review.selectionMode==='reduce_only'){
  if(!['pricing','final'].includes(stage))throw new Error('当前审核节点已变化，请刷新核对。');
  const eligible=new Set(review.eligibleCouponIds??[]);
  if(selected.some(id=>!eligible.has(id))){
   throw new Error(stage==='final'?'终审只能从复审保留的券档中选择，请刷新核对。':'价盘复审只能保留初审已选券档，请刷新核对。');
  }
 }
}

function couponApprovalFields(version,selected,revision){
 const review=version.couponReview;
 if(needsStoreBearingConfirmation(review))throw new Error('请先由提报人确认门店是否出资。');
 validateCouponScope(review,selected,version.review?.stage);
 if(!selected.length)throw new Error(review.selectionMode==='reduce_only'?'请至少保留一档满减券，或打回本次审核。':'请至少勾选一档拟报名的满减券。');
 return {selectedCouponIds:selected,expectedCouponSelectionRevision:revision??couponDraftRevision(review)};
}

export async function saveCouponSelection(commands,version,selectedCouponIds,signal){
 const review=version?.couponReview;
 if(!review||review.selectionLocked||!actionAllowed(version,'reviews.decide'))
  throw new Error('当前版本无法保存选券，请刷新核对。');
 const path='/activities/'+encodeURIComponent(version.activityId)+'/versions/'+encodeURIComponent(version.id)+'/coupon-selection';
 const revision=couponDraftRevision(review);
 validateCouponScope(review,selectedCouponIds,version.review?.stage);
 const body={selectedCouponIds,expectedRevision:revision};
 if(review.selectionMode==='reduce_only')body.expectedReviewStage=version.review.stage;
 const slot=['coupon-selection',version.id,body.expectedReviewStage,revision].filter(value=>value!==undefined).join(':');
 const {data}=await commands.run(slot,'reviews.coupon_selection','PUT',path,body,{signal});
 const savedIds=new Set(data?.selectedCouponIds??[]);
 if(data?.activityId!==version.activityId||data?.versionId!==version.id||data.revision!==revision+1||
    !Array.isArray(data.selectedCouponIds)||data.selectedCouponIds.length!==selectedCouponIds.length||
    !selectedCouponIds.every(id=>savedIds.has(id)))
  throw new Error('选券回执与当前活动版本不一致，请刷新核对。');
 return data;
}

export function readCouponSelection(container){
 return Array.from(container?.querySelectorAll('[name="reviewCoupon"]:checked:not(:disabled)')??[],input=>input.value);
}

export function updateCouponBulkSelection(target){
 const action=target.closest('[data-review-coupon-all]');
 const group=target.closest('.b-review-coupons');
 if(!group)return;
 const button=action??group.querySelector('[data-review-coupon-all]');
 if(!button||button.disabled)return;
 const choices=Array.from(group.querySelectorAll('[name="reviewCoupon"]:not(:disabled)'));
 if(!choices.length)return;
 if(action){
  const checked=!choices.every(input=>input.checked);
  choices.forEach(input=>{input.checked=checked});
 }
 button.textContent=choices.every(input=>input.checked)?'取消全选':'全选';
}

export async function decideWorkbenchReview(adapter,commands,activity,decision,opinion,signal,selection=[],selectionRevision){
 const activityId=activity.id,versionId=activity.reviewAction?.versionId;
 const versionPath='/activities/'+encodeURIComponent(activityId)+'/versions/'+encodeURIComponent(versionId);
 const path=versionPath+'/decisions';
 if(commands.pending().some(record=>record.action==='reviews.decide'&&record.path===path))
  throw new Error('此审核有待核对的提交结果，请先读取原请求回执。');
 const {data:version}=await adapter.request(versionPath,{signal});
 const review=version?.review;
 const node=version?.nodes?.find(item=>item.stage===review?.stage&&item.state==='pending');
 if(version?.activityId!==activityId||version?.id!==versionId||review?.stage!==activity.review?.stage||
    !node?.id||!Number.isInteger(node.revision)||!Number.isInteger(review?.revision)||!actionAllowed(version,'reviews.decide')){
  throw Object.assign(new Error('当前审核节点或权限已变化，请刷新后核对。'),{code:'STATE_CONFLICT'});
 }
 const body={expectedReviewRevision:review.revision,nodeId:node.id,expectedNodeRevision:node.revision,
  decision,reason:decision==='reject'?opinion:null,comment:decision==='approve'?opinion?.trim()||null:null,optimizationNote:null};
 if(decision==='approve')checkApprovalRequirements(version);
 if(decision==='approve'&&version.couponReview&&!version.couponReview.selectionLocked){
  Object.assign(body,couponApprovalFields(version,selection,selectionRevision));
 }
 if(decision==='approve'&&review.stage==='initial'&&productReviewEnabled(version)){
  const draft=await readReviewRegistration(adapter,version,null,signal);
  body.registrationReview=registrationReviewRequest(version,draft,{querySelectorAll:()=>[]});
 }
 if(signal?.aborted)throw new DOMException('Aborted','AbortError');
 const {data:receipt}=await commands.run('decision:'+versionId+':'+review.revision+':'+node.id,'reviews.decide','POST',path,body,{signal});
 if(receipt?.activityId!==activityId||receipt?.versionId!==versionId||receipt?.node?.id!==node.id||!receipt?.decisionId)
  throw new Error('审核回执与当前版本不一致，请刷新核对。');
 return {stageLabel:singleManagement(review)?'管理审核':reviewStageNames[review.stage],state:receipt.review?.state};
}
const assignmentMessages={
 ASSIGNMENT_REQUIRED:'审核人员尚未安排。',
 ASSIGNEE_ROLE_MISMATCH:'现有审核人员不符合该环节的要求。',
 ASSIGNEE_SELF_REVIEW:'审核安排包含提报人自审。',
 ASSIGNEE_SAME_INITIAL_FINAL:'管理初审与终审指派给了同一人。'
};

const decisionLimits={
 ASSIGNMENT_REQUIRED:['当前节点尚未指派审核人。','请联系活动业务负责人确认指派后刷新。'],
 ASSIGNEE_ROLE_MISMATCH:['当前节点的审核指派已失效。','请联系活动业务负责人核对指派后刷新。'],
 self_review:['提报人不能审核本人提报的活动。','请由符合条件的其他审核人处理。'],
 same_initial_final_actor:['本版初审和终审须由不同人员办理。','请由符合条件且未办理本版初审的审核人处理。'],
 REVIEW_VERSION_NOT_CURRENT:['所查看的版本已不是当前审核版本。','请返回活动详情，打开当前审核版本。'],
 STATE_CONFLICT:['当前节点状态或修订已变化。','请刷新核对当前审核版本和节点。']
};

export const isFullAccessAdministrator=session=>session?.authenticated===true&&
 session.capabilities?.fullAccessAdministrator===true;

const actorRole=record=>(record.authority??record.details?.authority)==='full_access_administrator'?
 '管理员':ROLE_LABELS[record.roleSnapshot]??'当时角色未记录';

function decisionLimit(reason){
 const code=reason?.details?.reasonCode??reason?.details?.policy??reason?.reasonCode??reason?.code;
 return Object.hasOwn(decisionLimits,code)?decisionLimits[code]:null;
}

export function reviewWorkSummary(activity,version,{canStart=false,canDecide=false,fullAccessAdministrator=false}={}){
 const review=version?.review;
 const coordination=version?.reviewCoordination??activity?.reviewCoordination;
 const state=review?.state??activity?.reviewState;
 const stage=review?.stage??activity?.review?.stage;
 const node=version?.nodes?.find(item=>item.stage===stage&&item.state==='pending');
 const reason=version?.blockedReasons?.find(item=>item.action==='reviews.start'&&
  Object.hasOwn(assignmentMessages,item.reasonCode??item.code));
 const block=reason?.reasonCode??reason?.code??review?.reviewBlockedReason??activity?.reviewBlockedReason;
 if(state==='not_started'){
  if(canStart)return {stage:'待提交审核',responsible:'原提报人',next:'已具备首次送审条件，提交后由已指派人员按顺序审核。'};
  if(Object.hasOwn(assignmentMessages,block))return {stage:'待安排审核',responsible:'活动业务负责人 '+(coordination?.businessContact?.displayName??'姜鹏'),
   issue:assignmentMessages[block],next:'由活动业务负责人确认审核人及指派来源。指派生效后，原提报人从本版本审核详情提交审核。'};
 }
 if(stage&&Object.hasOwn(reviewStageNames,stage)&&state===stage+'_pending'){
  const stageName=singleManagement(review??activity?.review)?'管理审核':reviewStageNames[stage];
  const responsible=node?.assignee?.nameSnapshot??(stageName+'人（姓名待核）');
  const blocked=version?.blockedReasons?.find(item=>item.action==='reviews.decide'&&(!item.nodeId||item.nodeId===node?.id));
  const limit=!canDecide&&decisionLimit(blocked);
  if(limit)return {stage:stageName,responsible,issue:limit[0],next:limit[1]};
  return {stage:stageName,responsible,
   next:canDecide?(fullAccessAdministrator?'你可使用管理员身份处理本节点。':'在本版本审核处理中选择通过或不通过。'):
    '由获授权的审核人从自己的审核待办进入处理。'};
 }
 return null;
}

export function reviewWorkSummaryHtml(summary){
 if(!summary)return '';
 return `<div class="b-review-scope"><p>${badge(summary.stage,'amber')}</p>${summary.issue?`<p>${h(summary.issue)}</p>`:''}
  <p>当前责任人：${h(summary.responsible)}<br>下一步：${h(summary.next)}</p></div>`;
}

export function activityHistoryHtml(activity,items,{available=true,partial=false}={}){
 if(!available)return '<p class="muted">当前暂时无法读取审核与操作历史。</p>';
 const body=items.map(event=>rows([
  `<a href="#review/${encodeURIComponent(activity.id)}/${encodeURIComponent(event.versionId)}">V${h(event.versionNo??'待核实')}</a>`,
  h(beijing(event.at)??'时间待核实'),h(historyEventLabel(event.event)),
  event.sourceKind==='rule_default'?'系统规则':h(event.nameSnapshot??event.actorId??'未记录')+
   `<small>${h(actorRole(event))}</small>`,
  historyDetailsHtml(event)
 ]));
 const note=partial?'<p class="hint">部分可读版本的历史暂时无法获取。</p>':'';
 return note+table(['版本','时间（北京时间）','业务操作','实际操作者 / 当时角色','留存依据与结果'],body,
  partial?'当前未取得可展示的历史记录。':'当前可读版本暂无审核或操作记录。');
}

export function activityReviewReturn(back,activityId){
 const route=back?.split('?')[0];
 if(route==='#activity')return {href:back,label:'返回活动列表'};
 if(activityId&&route==='#activity/'+encodeURIComponent(activityId))return {href:back,label:'返回活动详情'};
 return null;
}

export function createReviewsController(ctx){
 const activityIntake=()=>ctx.activityIntake??ctx.localIntake;
 let listing=null,version=null,registration=null,history=null,activity=null,previous=null,previousError=null,products=[],listCapabilities={},tabTotals={},params={},signal,route='',error=null,savedMessage=null,busy=false,opinion={decision:'',reason:'',comment:'',optimizationNote:''};
 const can=p=>ctx.session()?.permissions?.includes(p)===true;
 const fullAccessAdministrator=()=>isFullAccessAdministrator(ctx.session());
 const localReview=()=>ctx.reviewAccess?.()??{};
 const canDecide=()=>actionAllowed(version,'reviews.decide')&&
  (!activityIntake()||localReview().decide===true&&can('reviews.decide'));
 const startPath=()=>'/activities/'+encodeURIComponent(version.activityId)+'/versions/'+encodeURIComponent(version.id)+'/start-review';
 const canStart=()=>actionAllowed(version,'reviews.start')&&(!activityIntake()||
  version.review?.state==='not_started'&&!(ctx.commands.pending?.()??[]).some(record=>record.action==='reviews.start'&&record.path===startPath()));
 const currentNode=()=>version?.nodes?.find(node=>node.stage===version.review?.stage&&node.state==='pending');
 const decisionPath=()=>'/activities/'+encodeURIComponent(version.activityId)+'/versions/'+encodeURIComponent(version.id)+'/decisions';
 const pendingDecision=()=>activityIntake()&&(ctx.commands.pending?.()??[]).some(record=>
  record.action==='reviews.decide'&&record.path===decisionPath());
 const versionHash=(a,v)=>'#review/'+encodeURIComponent(a)+'/'+encodeURIComponent(v);
 function listReady(){return listCapabilities.reviewListContractVersion==='reviews/1.1'}
 function permittedViews(){return [...(can('reviews.read')?['pending','handled']:[]),...(can('submissions.read_own')?['mine']:[])]}
 function listQuery(view,page=1,size=10){const query={view,page,pageSize:size};if(params.q&&(view!=='mine'||listReady()))query.q=params.q;if(listReady()&&params.brandQ)query.brandQ=params.brandQ;if(listReady()&&view==='handled'&&params.result&&params.result!=='all')query.result=params.result;if(listReady()&&view==='mine'&&params.state&&params.state!=='all')query.state=params.state;return new URLSearchParams(query)}
 function tabs(){const labels={pending:'待我审核',handled:'我已处理',mine:'我提报的'};return `<nav class="b-tabs" aria-label="个人工作列表">${permittedViews().map(code=>`<a href="#reviews?view=${code}" ${params.view===code?'aria-current="page" class="selected"':''}>${labels[code]}${Number.isInteger(tabTotals[code])?'（'+tabTotals[code]+'）':''}</a>`).join('')}</nav>`}
 function listHtml(){
  const view=params.view,items=listing?.items??[],ready=listReady();
  const noAssignment=activityIntake()&&view==='pending'&&listCapabilities.blockedReasons?.reviewAssignment==='ASSIGNMENT_REQUIRED';
  const headers=view==='handled'?['审核阶段','审核结果','活动名称','品牌','提交人','审核时间','审核意见','活动时间','操作']:view==='mine'?['处理状态','活动名称 / 提报编号','品牌','提报时间','处理结果 / 待核对信息','活动时间','操作']:['审核阶段','活动名称','品牌','提交人','活动时间','待核对信息','操作'];
  const back='#reviews?'+new URLSearchParams(params);
  const body=items.map(item=>{
   const brand=Array.isArray(item.brands)?item.brands.length?item.brands.map(b=>b.name??b.id).join('、'):'未关联品牌':'待核实';
   const title=h(item.activityName??'活动待识别 / 建档')+`<small class="design-row-id" title="${h(item.activityId??item.submissionId)}">${h(item.activityId??item.submissionId)}</small>${item.versionId?`<small class="design-row-id" title="${h(item.versionId)}">版本 ${item.versionNo!=null?'V'+h(item.versionNo):h(item.versionId)}</small>`:''}`;
   const period=datePeriodText(activityDate(item.startAt),activityDate(item.endAt),item.activityDateEvidence,'待核实');
   const submitter=h(item.submitter?.nameSnapshot??'当时姓名未记录')+(item.submitter?.id?`<small class="design-row-id">${h(item.submitter.id)}</small>`:'');
   const detailUrl=view==='pending'
    ?routeLink('activity/'+encodeURIComponent(item.activityId),{back,...(item.versionId?{sourceVersion:item.versionId}:{})})
    :versionHash(item.activityId,item.versionId)+'?'+new URLSearchParams({back});
   const action=item.platformReview?`<a href="#activity/${h(item.activityId)}/platform/${h(item.platformReview.platformCode)}/materials">${item.stage==='platform_recheck'?'复查':'检查'}</a>`:item.activityId?`<a href="${h(detailUrl)}">进入详情</a>`:`<a href="#submissions/${h(item.submissionId)}">查看提报处理</a>`;
   let cells;
   if(view==='handled'){const decision=item.decision,result=item.nodeResult??decision?.decision;cells=[h(item.stage?stateLabel(item.stage):'环节待核实'),badge(result==='approve'?'本节点通过':result==='reject'?'本节点不通过':'待核实',result==='approve'?'green':'red'),title,h(brand),submitter,h(beijing(item.decisionAt??decision?.at)??'未记录'),h(item.opinion??'未提供'),h(period),action]}
   else if(view==='mine')cells=[badge(stateLabel(item.processingState??item.state)),title,h(brand),h(beijing(item.submittedAt)??'未记录'),h(item.attentionSummary??'暂无待核对摘要，详情查看实际处理结果'),h(item.activityId?period:item.startAt||item.endAt?period:'尚未建档'),action];
   else cells=[badge(item.stage==='platform_recheck'?'待复查':item.stage==='platform_verification'?'待检查':(singleManagement(item.review)?'待管理审核':stateLabel(item.stage??item.node?.stage??item.review?.stage)),'purple'),title,h(brand),submitter,h(period),h(item.attentionSummary??'未提供待核对摘要'),action];
   return rows(cells).replace('<tr>',`<tr data-row-key="${h(item.rowKey??item.node?.id??item.decision?.id??item.submissionId)}">`);
  });
  const businessRole=ctx.session()?.user?.businessRole;
  const admin=fullAccessAdministrator();
  const role=admin?'管理员':ROLE_LABELS[businessRole]??'未分配';
  const responsibility=admin?'初审、复审、终审及平台检查和复查':{management:'初审、终审',pricing:'复审'}[businessRole]??'以服务实际授权为准';
  const scope=admin?'待办按服务端返回的当前可处理节点展示。':'按当前角色及分配查看需要处理的活动，进入详情审核。';
  const emptyPending=admin?'当前没有符合办理条件的待审记录。':'当前没有分配给你的待审记录。';
  return `<div class="b-heading"><div><h1>审核待办</h1><p class="muted">${scope}</p></div></div><div class="b-labels design-review-context">${badge((admin?'当前身份：':'当前角色：')+role,'blue')}${badge((admin?'审核范围：':'岗位职责：')+responsibility)}<span class="hint">${admin?'管理员可按顺序审核自己或他人的提报。':'具体活动以实际节点指派为准。'}</span></div>${tabs()}<section class="panel">${noAssignment?'<p class="b-note">当前尚未配置审核方案。</p>':''}<form id="b-review-query" class="filters">${input('q','搜索活动名称 / ID',params.q??'',{disabled:view==='mine'&&!ready})}${input('brandQ','搜索品牌',params.brandQ??'',{disabled:!ready})}${view==='handled'?select('result','审核结果',[{code:'all',label:'全部'},{code:'approve',label:'本节点通过'},{code:'reject',label:'本节点不通过'}],params.result??'all',{disabled:!ready,empty:null}):view==='mine'?select('state','处理状态',[{code:'all',label:'全部'},...['received','analyzing','needs_input','needs_confirmation','partially_resolved','resolved','failed'].map(code=>({code,label:stateLabel(code)}))],params.state??'all',{disabled:!ready,empty:null}):''}<button class="primary" ${view==='mine'&&!ready?'disabled':''}>查询</button></form>${!ready?'<p class="hint">品牌、结果及处理状态筛选待列表接口交付；当前不以单页过滤冒充完整结果。</p>':''}${error?errorPanel(error):''}${table(headers,body,activityIntake()&&view==='pending'?emptyPending:'暂无记录。')}${pager(listing?.page??1,listing?.total,'#reviews',params,listing?.pageSize??10)}<p class="hint">本页仅查看，进入活动详情审核并查看历史。初审：管理 → 复审：价盘 → 终审：管理。</p></section>`;
 }
 function historyHtml(){
  return table(['时间（北京时间）','实际操作者 / 当时角色','业务操作','内部状态变化','留存依据与结果'],(history?.items??version.history??[]).map(x=>rows([h(beijing(x.at)),x.sourceKind==='rule_default'?'系统规则默认记录'+`<small>${version.snapshot?.resolutionBinding?'原提报人':'人工确认人'}：${h(x.nameSnapshot??x.actorId??'未记录')}</small>`:h(x.nameSnapshot??x.actorId??'未记录')+'<small>'+h(actorRole(x))+'</small>',h(historyEventLabel(x.event)),h(x.fromState?stateLabel(x.fromState):'首次记录')+' → '+h(x.toState?stateLabel(x.toState):'状态未提供'),historyDetailsHtml(x)])))+(history?pager(history.page,history.total,versionHash(version.activityId,version.id),params.back?{back:params.back}:{},history.pageSize):'');
 }
 function decisionHtml(){
  const review=version.review,node=currentNode();
  const pending=pendingDecision();
  const allowed=canDecide()&&!pending;
  const stage=singleManagement(review)?'审核':review?.stage?stateLabel(review.stage):'当前节点';
  const limit=decisionLimit(error);
  const feedback=limit?`<div class="warning" role="alert">${h(limit[0])} ${h(limit[1])}</div>`:error?errorPanel(error):'';
  const blocks=(version.blockedReasons??[]).filter(item=>!activityIntake()||allowed||item.action!=='reviews.decide'||!decisionLimit(item));
  return panel('审核处理',`${infoReview()}<ol class="design-review-flow">${(version.nodes??[]).map(n=>`<li class="${n.state==='pending'?'current':''}"><strong>${h(singleManagement(review)?'管理审核':stateLabel(n.stage))}</strong><span>${h(nodeResultLabel(n.state))}</span></li>`).join('')}</ol>${feedback}${savedMessage?`<p class="b-note" role="status">${h(savedMessage)}</p>`:''}${allowed&&node?`<form id="b-review-decision"><input type="hidden" name="decision" value="${h(opinion.decision)}">${textarea('comment','审核意见（不通过时必须填写原因）',opinion.comment||opinion.reason,2000)}${review.stage==='final'?`<fieldset class="design-inline-options"><legend>商品优化（终审辅助项，可选）</legend><label><input type="radio" name="optimizationChoice" value="none" ${opinion.optimizationChoice!=='needed'?'checked':''}>无需优化</label><label><input type="radio" name="optimizationChoice" value="needed" ${opinion.optimizationChoice==='needed'?'checked':''}>需要优化</label></fieldset>${opinion.optimizationChoice==='needed'?textarea('optimizationNote','商品优化建议（可选）',opinion.optimizationNote,2000):''}`:''}<div class="actions design-review-actions"><button class="primary" type="button" data-review-decision="approve" ${busy||approvalRequirements(version).length?'disabled':''}>${busy?'正在提交…':'通过'+h(stage)}</button><button class="danger" type="button" data-review-decision="reject" ${busy?'disabled':''}>审核不通过</button></div>${diagnostics({versionId:version.id,nodeId:node.id,reviewRevision:review.revision,nodeRevision:node.revision},'查看本次审核对象及修订核验')}</form>`:pending?'<p class="b-note">本节点提交结果待核对，请查询原请求回执。</p>':'<p class="b-readonly">当前账号可查看本版资料。没有可处理的审核节点。</p>'}${blockedReasonsHtml(blocks)}${canStart()?`<button type="button" data-review-action="start" ${busy?'disabled':''}>提交此版本审核</button>`:''}${error?.status===409?'<button type="button" data-review-action="latest">核对最新状态，保留我的意见</button>':''}<p class="hint">通过仅更新本节点内部结果，报名与上线另行处理。</p>${ctx.recoveryHtml?.()??''}`);
 }
 function infoReview(){const r=version.review;
  const work=activityIntake()?reviewWorkSummary(activity,version,{canStart:canStart(),canDecide:canDecide(),fullAccessAdministrator:fullAccessAdministrator()}):null;
  return `${reviewWorkSummaryHtml(work)}<div class="b-review-scope"><p>${badge(singleManagement(r)&&r.stage?'待管理审核':stateLabel(r?.state??'未发起审核'))}</p><p>模式：${r?.approvalMode==='rule_default'?'系统规则默认通过（非人工三审）':singleManagement(r)?'一次管理审核':r?.approvalMode==='manual'?'人工三步审核':'审核方式待核实'}<br>当前节点：${h(r?.stage?(singleManagement(r)?'管理审核':stateLabel(r.stage)):'无待处理节点')}</p></div>`}
 function comparisonHtml(){if(previousError)return `<p class="b-note">上一版本内容未获授权或暂不可读：${h(previousError.message)}。不推测差异。</p>`;if(!previous)return '';const before=previous.snapshot?.resolvedFields??previous.snapshot?.confirmedFields??{},after=version.snapshot?.resolvedFields??version.snapshot?.confirmedFields??{};const keys=[...new Set([...Object.keys(before),...Object.keys(after)])];const value=(key,x)=>fieldValueText(key,x,{brands:activity?.brands??[]});const changes=keys.filter(k=>JSON.stringify(before[k])!==JSON.stringify(after[k]));const bp=previous.snapshot?.products??[],ap=version.snapshot?.products??[];if(JSON.stringify(bp)!==JSON.stringify(ap))changes.push('confirmedProducts');return `<h3>与上一版逐项比较（来自已授权的两个快照）</h3>${table(['字段','上一版本','本次补充版本'],changes.map(k=>rows([h(FIELD_LABELS[k]??(k==='confirmedProducts'?'已关联商品':k)),`<div class="b-diff-before">${h(value(k,k==='confirmedProducts'?bp:before[k]))}</div>`,`<div class="b-diff-after">${h(value(k,k==='confirmedProducts'?ap:after[k]))}</div>`])),'确认字段和精确商品未发生变化；材料来源仍按本版记录留存。')}<p>上一版内部状态：${badge(stateLabel(previous.review?.state??'未记录'))}</p>${(previous.decisions??[]).filter(d=>d.decision==='reject').map(d=>`<p class="b-note">旧版不通过：${h(d.reason)} · ${h(d.nameSnapshot??d.actorId)} · ${h(actorRole(d))} · ${h(beijing(d.at))}</p>`).join('')}`}
 function detailHtml(){
  const snapshot=version.snapshot??{},fields=snapshot.resolvedFields??snapshot.confirmedFields??{},manual=snapshot.manualConfirmation,evidence=version.evidence??[];
  const catalog={brands:activity?.brands??[],products};const role={initial:'管理',pricing:'价盘',final:'管理'}[version.review?.stage]??'暂无待处理节点';
  const source=version.sourceInputs?.[0],submitter=source?.submitter;
  const name=fields.activityName??activity?.name??'活动名称待核实';
  const value=(key,x)=>h(fieldValueText(key,x,catalog));
  const pair=(a,b,c,d)=>`<tr><th scope="row">${h(a)}</th><td>${b}</td><th scope="row">${h(c)}</th><td>${d}</td></tr>`;
  const dates=version.activityDateEvidence;
  const registrationEvidence=version.registrationDateEvidence;
  const dateRecord={activityDateEvidence:dates,registrationDateEvidence:registrationEvidence};
  const signup=registrationDateText(registrationEvidence);
  const storeBearing=fields.storeBearing==='yes'?'门店出钱':fields.storeBearing==='no'?'门店不出钱':'';
  const info=`<div class="table-wrap"><table class="design-info-table"><tbody>${pair('活动方式',h(activityCategoryLabel(version.activityCategory??snapshot.activityCategory)),'活动发起方',value('type',version.initiatorType??snapshot.initiatorType??fields.type))}${pair('适用平台',h((snapshot.applicablePlatformCodes?.length?snapshot.applicablePlatformCodes:[fields.platformCode]).filter(Boolean).map(code=>fieldValueText('platformCode',code,catalog)).join('、')||'平台待核对'),'','')}${pair('品牌',value('brandIds',fields.brandIds),'项目',h(activity?.projectName??'待核实'))}${pair('活动时间',h(datePeriodText(activityDate(version.effectiveActivityDates?.startAt??fields.startAt),activityDate(version.effectiveActivityDates?.endAt??fields.endAt),dates,'待核实')),'提交人',h(submitter?.snapshotStatus==='recorded'?submitter.nameSnapshot??'当时姓名未记录':'当时提交人未记录'))}${pair('本次提报时间',h(beijing(source?.submittedAt)??'未记录'),'报名状态',h(factLabel(activity?.registration?.state)))}${signup?pair('材料报名时间',h(signup),'',''):''}${storeBearing?pair('门店是否出钱',h(storeBearing),'',''):''}</tbody></table></div>${dateEvidenceHtml(dateRecord,version.materials)}<details><summary>查看提交与范围资料</summary>${hasSubmissionDeclarations(snapshot)?declarationRecordHtml(snapshot,{identity:fields.platformCode,verified:snapshot}):''}<p>本次提报：${ownSubmissionLink(version,version.submissionId,ctx.session())}</p></details>`;
  const pending=pendingItemLabels(version.currentPendingItems??snapshot.pendingItems,dates);
  const supplement=activityIntake()?supplementVersionHtml(activity,version):'';
  const versionHeading=snapshot.supplementBinding?'资料补充版本':'所查看版本';
  const goods='<h3>已审核商品范围</h3>'+((snapshot.products??[]).length?table(['商品','内部码','UPC','规格','日常价','活动价','公司承担'],(snapshot.products??[]).map(id=>{const p=products.find(x=>x.id===id);return rows([h(p?.name??'商品主档待核实')+`<small>${h(id)}</small>`,h(p?.internalCode??'待核实'),h(p?.upc??'待核实'),h(p?.spec??'待核实'),'待核实','待核实','金额及单位待核实'])}),snapshot.productListState==='scope_only'?'本版已记录全商品范围，未逐项列出商品。':snapshot.unresolvedRows?.length||snapshot.productApplicability?.kind==='unknown'?
   '本版商品范围待核实，当前暂无已核商品。':'本版未关联单品，可为非单品活动。'):`<p class="muted">${snapshot.productListState==='scope_only'?'本版已记录全商品范围，未逐项列出商品。':'本版暂无已核商品。'}</p>`);
  const historyRows=(version.decisions??[]).map(d=>{const node=version.nodes?.find(n=>n.id===d.nodeId);return rows([h(beijing(d.at)),h(node?(singleManagement(version.review)?'管理审核':stateLabel(node.stage)):'节点未记录'),h(d.nameSnapshot??d.actorId)+`<small>${h(actorRole(d))}</small>`,badge(d.decision==='approve'?'本节点通过':'本节点不通过',d.decision==='approve'?'green':'red'),h([d.reason,d.comment,d.optimizationNote].filter(Boolean).join('；')||'未提供'),h('V'+(version.versionNo??'待核实'))])});
  for(const event of version.history??[])if(event.sourceKind==='rule_default')historyRows.push(rows([h(beijing(event.at)),'规则默认内部通过','系统规则',badge('默认内部通过','green'),h('公司明确无承担且依据已核实。'+(snapshot.resolutionBinding?'原提报人：':'人工确认人：')+(event.nameSnapshot??event.actorId??'未记录')),h('V'+(version.versionNo??'待核实'))]));
  const reviewList=!activityIntake()||localReview().list===true&&can('reviews.read');
  const canSubmissions=can('submissions.read_own');
  const activityBack=activityReviewReturn(params.back,version.activityId);
  const canChoose=canDecide()&&currentNode()&&!pendingDecision()&&!busy;
  const couponControls=version.couponReview?couponReviewHtml(version.couponReview,Boolean(canChoose),opinion.selectedCouponIds,version.review?.stage):null;
  const fallbackBack=reviewList?(params.back?.startsWith('#reviews')?params.back:'#reviews?view='+(can('reviews.read')?'pending':'mine')):(canSubmissions?'#submissions':'#activity/'+encodeURIComponent(version.activityId));
  const fallbackLabel=reviewList?'返回审核待办':(canSubmissions?'返回我的提报':'返回活动详情');
  const back=activityBack?.href??fallbackBack;
  return `<a class="design-back" href="${h(back)}">‹ ${activityBack?.label??fallbackLabel}</a><div class="b-heading"><div><h1>${h(name)} ${badge(awaitingReview(version.review?.state,version.review?.reviewBlockedReason)?'待安排审核':stateLabel(version.review?.state),'purple')}</h1><p>${activityIntake()?versionHeading:'当前审核版本'}：${badge('V'+(version.versionNo??'待核实'),'blue')} · 当前责任角色：${h(role)}${fullAccessAdministrator()?' · 登录身份：管理员':''}</p></div></div>${reviewRequirementsHtml(version,ctx.session())}<nav class="anchors design-detail-anchors" aria-label="审核内容定位">${[['info','活动信息'],['benefits','优惠与价盘'],['materials','商品与材料'],['process','审核处理'],['history','审核历史']].map(([id,label])=>`<button type="button" data-review-anchor="${id}">${label}</button>`).join('')}</nav><div class="review-main-column"><section class="panel" id="review-info"><h2 class="design-section-title">活动信息</h2>${info}${supplement}${pending.length?`<details><summary>查看完整缺项</summary><p>${h(pending.join('、'))}</p></details>`:''}</section><section class="panel" id="review-benefits"><h2 class="design-section-title">优惠与价盘</h2>${activityBenefitsHtml({id:version.activityId,couponSchedule:snapshot.couponSchedule,rules:fields.rulesText,brandBenefit:fields.brandBenefit,costEvidence:typeof fields.companyBearing==='object'?fields.companyBearing:{state:fields.companyBearing}},null,couponControls)}${version.couponReview?.storeBearingPolicy!=='per_tier'&&(typeof fields.companyBearing==='object'?fields.companyBearing?.state:fields.companyBearing)==='unknown'?'<p class="hint">公司费用承担待确认，请在审核前核对依据。</p>':''}<details><summary>查看承担核实与原文依据</summary>${bearingVerificationHtml(version.reviewEligibility??snapshot.resolutionBinding?.bearingVerification??manual?.bearingVerification,version.review,evidence,version.materials,snapshot.resolutionBinding?.runId??manual?.runId)}</details></section><div id="review-process">${decisionHtml()}${version.reviewRestart?.available?`<section class="panel"><h3>退回后重新选品</h3><form id="b-review-restart">${textarea('reason','重审说明','',2000)}<button class="primary"${busy?' disabled':''}>建立新版本并重新选品</button></form><p class="hint">旧清单和审核记录保留，新版本重新确认商品。</p></section>`:''}</div><section class="panel" id="review-materials"><h2 class="design-section-title">商品与材料</h2>${snapshot.couponScopeRequired||snapshot.productApplicability?.kind!=='unknown'?`<p><strong>商品适用范围</strong> · ${h(verifiedProductScopeLabel(snapshot))}</p>`:''}${subsidyPricesHtml(snapshot.subsidyPrices)}${reviewRegistrationHtml(version,registration)}<details><summary>查看内部批准的商品范围</summary>${goods}</details><details><summary>查看相关原材料</summary>${materialHtml(version.materials)}</details><details><summary>查看原始提交表单、文字与本次确认差异</summary>${versionSourcesHtml(version,ctx.session(),catalog)}${evidenceList(evidence,version.materials)}</details><details><summary>查看本次补充与旧版拒绝</summary><p>${h(version.changeSummary??'未提供补充说明')}</p>${comparisonHtml()}</details></section></div><section class="panel" id="review-history"><h2 class="design-section-title">审核历史</h2>${table(['审核时间','环节','审核人','结果','审核意见','版本'],historyRows)}<p class="hint">历史记录按版本保留，不覆盖既有结论；当前版与原生效版分别留存。</p><details><summary>查看完整来源与操作记录</summary>${historyHtml()}</details></section>`;
 }
 function paint(){if(signal?.aborted||!['review','reviews'].includes(ctx.route().split('/')[0]))return;ctx.frame(version?detailHtml():listHtml())}
 async function readVersion(){const requestSignal=signal;const [,aid,vid]=route.split('/');const base='/activities/'+encodeURIComponent(aid);const [v,hs,a,goods]=await Promise.all([ctx.adapter.request(base+'/versions/'+encodeURIComponent(vid),{signal:requestSignal}),ctx.adapter.request(base+'/versions/'+encodeURIComponent(vid)+'/history?'+new URLSearchParams({page:params.page??1,pageSize:10}),{signal:requestSignal}),ctx.adapter.request(base,{signal:requestSignal}),ctx.adapter.all('/products',{},requestSignal)]);if(requestSignal!==signal||requestSignal.aborted)return;version=v.data;history=hs.data;activity=a.data;products=goods.items;registration=await readReviewRegistration(ctx.adapter,version,params.platform,requestSignal);if(requestSignal!==signal||requestSignal.aborted)return;previous=null;previousError=null;const previousId=version.basedOnVersionId??version.previousVersionId;if(previousId){try{const prior=await ctx.adapter.request(base+'/versions/'+encodeURIComponent(previousId),{signal:requestSignal});if(requestSignal!==signal||requestSignal.aborted)return;previous=prior.data}catch(e){if(e.status===401)throw e;if(requestSignal!==signal||requestSignal.aborted)return;previousError=e}}paint()}
 function capture(form){
  captureRegistrationReview(registration);
  if(!form)return;
  opinion={...opinion,...Object.fromEntries(new FormData(form))};
  if(version?.couponReview&&!version.couponReview.selectionLocked&&!busy){
   const choices=document.querySelector('#review-benefits .b-review-coupons');
   if(choices)opinion.selectedCouponIds=readCouponSelection(choices);
  }
 }
 return {
  async load(r,p,abortSignal){signal=abortSignal;if(route!==r)opinion={decision:'',reason:'',comment:'',optimizationNote:''};route=r;params={...p};busy=false;error=null;savedMessage=null;version=null;registration=null;history=null;activity=null;previous=null;previousError=null;if(r.startsWith('review/')){await readVersion();return}params.view??=can('reviews.read')?'pending':'mine';if(activityIntake()&&localReview().list!==true)throw Object.assign(new Error('当前账号没有可用的审核列表入口。'),{status:403});const caps=activityIntake()?localReview().capabilities:(await ctx.adapter.request('/capabilities',{signal:abortSignal})).data;if(abortSignal!==signal||abortSignal.aborted)return;listCapabilities=caps;if(!listReady()&&(params.brandQ||params.result&&params.result!=='all'||params.state&&params.state!=='all'||params.view==='mine'&&params.q))throw new Error('所选列表筛选尚待服务端接口交付，未返回未经筛选的结果冒充命中。');const env=await ctx.adapter.request('/reviews?'+listQuery(params.view,p.page??1),{signal:abortSignal});if(abortSignal!==signal||abortSignal.aborted)return;listing=env.data;tabTotals={[params.view]:listing.total};await Promise.all(permittedViews().filter(view=>view!==params.view).map(async view=>{const count=await ctx.adapter.request('/reviews?'+listQuery(view,1,1),{signal:abortSignal});if(abortSignal===signal&&!abortSignal.aborted)tabTotals[view]=count.data.total}));if(abortSignal!==signal||abortSignal.aborted)return;paint()},

  async click(target){
   if(target.closest('[data-review-coupon-save]')){
    if(busy||!canDecide())return true;
    const requestSignal=signal;
    capture(document.querySelector('#b-review-decision'));
    busy=true;error=null;savedMessage=null;paint();
    try{
     await saveCouponSelection(ctx.commands,version,opinion.selectedCouponIds??[],requestSignal);
     if(requestSignal===signal&&!signal.aborted){delete opinion.selectedCouponIds;await readVersion();savedMessage='券档勾选已保存。'}
    }catch(e){if(requestSignal===signal&&!ctx.authError?.(e))error=e}
    finally{if(requestSignal===signal){busy=false;paint()}}
    return true;
   }
   const anchor=target.closest('[data-review-anchor]');if(anchor){document.querySelector('#review-'+anchor.dataset.reviewAnchor)?.scrollIntoView({behavior:'smooth',block:'start'});return true}const decision=target.closest('[data-review-decision]');if(decision){const field=document.querySelector('#b-review-decision [name=decision]');if(!busy&&field){opinion.decision=decision.dataset.reviewDecision;field.value=opinion.decision;return this.submit(document.querySelector('#b-review-decision'))}return true}const action=target.closest('[data-review-action]')?.dataset.reviewAction;if(!action)return false;if(busy)return true;capture(document.querySelector('#b-review-decision'));if(action==='latest'){delete opinion.selectedCouponIds;error=null;await readVersion();return true}if(action==='start'&&canStart()){
   const requestSignal=signal;
   const relevant=()=>requestSignal===signal&&!requestSignal.aborted;
   busy=true;error=null;paint();
   try{
    await ctx.commands.run('start-review:'+version.id+':'+version.review.revision,'reviews.start','POST',startPath(),{expectedReviewRevision:version.review.revision},{signal:requestSignal});
    if(relevant())await readVersion();
   }catch(e){if(relevant()&&!ctx.authError?.(e))error=e}
   finally{if(relevant()){busy=false;paint()}}
  }return true},
  async submit(form){if(form.id==='b-review-restart'){
    if(busy||!version.reviewRestart?.available)return true;
    const reason=String(new FormData(form).get('reason')??'').trim();
    if(!reason){error=new Error('请填写重审说明。');paint();return true}
    const binding=version.reviewRestart,requestSignal=signal;
    busy=true;error=null;paint();
    try{const response=await ctx.commands.run('review-restart:'+version.id+':'+binding.expectedReviewRevision,'reviews.restart','POST',
      '/activities/'+encodeURIComponent(version.activityId)+'/versions/'+encodeURIComponent(version.id)+'/restart-review',
      {expectedActivityRevision:binding.expectedActivityRevision,expectedReviewRevision:binding.expectedReviewRevision,reason},{signal:requestSignal});
      if(requestSignal===signal&&!signal.aborted)ctx.navigate('#activity/'+encodeURIComponent(version.activityId)+'?sourceVersion='+encodeURIComponent(response.data.versionId));
    }catch(e){if(!ctx.authError?.(e)&&requestSignal===signal){error=e;paint()}}finally{busy=false}
    return true;
   }if(form.id==='b-review-query'){ctx.navigate('#reviews?'+new URLSearchParams({...params,...Object.fromEntries(new FormData(form)),page:1}));return true}if(form.id!=='b-review-decision')return false;if(busy)return true;const node=currentNode();if(!canDecide()||!node||pendingDecision()){error=new Error('当前节点、权限或请求状态已变化，请刷新核对后再操作。');paint();return true}capture(form);if(!['approve','reject'].includes(opinion.decision)){error=new Error('请选择通过当前节点或审核不通过。');paint();return true}opinion.reason=opinion.decision==='reject'?opinion.comment?.trim()??'':'';if(opinion.decision==='reject'&&!opinion.reason?.trim()){error=new Error('本节点不通过时必须填写原因。');paint();document.querySelector('#b-review-decision [name=comment]')?.focus();return true}const body={expectedReviewRevision:version.review.revision,nodeId:node.id,expectedNodeRevision:node.revision,decision:opinion.decision,reason:opinion.decision==='reject'?opinion.reason.trim():null,comment:opinion.decision==='approve'?opinion.comment||null:null,optimizationNote:version.review.stage==='final'&&opinion.optimizationChoice==='needed'?opinion.optimizationNote||null:null};try{if(body.decision==='approve'){
    checkApprovalRequirements(version);
    body.registrationReview=registrationReviewRequest(version,registration);
    if(version.couponReview&&!version.couponReview.selectionLocked){
     Object.assign(body,couponApprovalFields(version,opinion.selectedCouponIds??[]));
    }
   }}catch(e){error=e;paint();return true}const operationSignal=signal;busy=true;error=null;savedMessage=null;paint();try{const receipt=await ctx.commands.run('decision:'+version.id+':'+version.review.revision+':'+node.id,'reviews.decide','POST',decisionPath(),body,{signal:operationSignal});if(operationSignal!==signal||operationSignal.aborted)return true;if(activityIntake()&&(receipt?.data?.activityId!==version.activityId||receipt?.data?.versionId!==version.id||receipt?.data?.node?.id!==node.id||!receipt?.data?.decisionId))throw new Error('审核回执与当前版本不一致，请刷新核对。');opinion={decision:'',reason:'',comment:'',optimizationNote:''};await readVersion();savedMessage=(body.decision==='approve'?'本节点已通过':'本节点未通过')+'，已保存。当前状态：'+stateLabel(version.review?.state);ctx.notice(savedMessage)}catch(e){if(ctx.authError?.(e))return true;if(operationSignal!==signal||operationSignal.aborted)return true;error=e}finally{busy=false;if(operationSignal===signal)paint()}return true},
  change(target){if(target.matches('[data-review-registration-platform]')){capture(document.querySelector('#b-review-decision'));ctx.navigate(versionHash(version.activityId,version.id)+'?'+new URLSearchParams({...params,platform:target.value}));return true}if(target.name==='optimizationChoice'){capture(document.querySelector('#b-review-decision'));paint();return true}return false},
  pause(){capture(document.querySelector('#b-review-decision'))},
  clear(){tabTotals={};listCapabilities={};listing=null;version=null;registration=null;history=null;activity=null;previous=null;previousError=null;error=null;savedMessage=null;busy=false;opinion={decision:'',reason:'',comment:'',optimizationNote:''}}
 }
}
