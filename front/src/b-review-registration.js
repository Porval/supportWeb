import {h,rows,table} from './b-common.js';
import {APPLICABLE_PLATFORM_LABELS} from './dto.js';
import {registrationDecision} from './ui.js';
const platformCodes=['meituan','taobao_instant','jd'];
export function productReviewEnabled(version){
 if(!version)return false;
 if(Object.hasOwn(version,'activityCategory'))return version.activityCategory==='product_discount';
 if(Object.hasOwn(version.snapshot??{},'activityCategory'))return version.snapshot.activityCategory==='product_discount';
 return ['shen_jia','bao_hao_jia'].includes(version.snapshot?.activityMethod);
}

export async function readReviewRegistration(adapter,version,platform,signal){
 const snapshot=version.snapshot??{},fields=snapshot.resolvedFields??snapshot.confirmedFields??{};
 const platforms=(snapshot.applicablePlatformCodes?.length?snapshot.applicablePlatformCodes:fields.platformCode?[fields.platformCode]:[]).filter(code=>platformCodes.includes(code));
 const platformCode=platforms.includes(platform)?platform:platforms[0];
 if(!platformCode||!productReviewEnabled(version))return {platforms,platformCode,rows:[],methodSupported:false};
 const target={activityId:version.activityId,versionId:version.id,platformCode};
 try{
  const all=await Promise.all(platforms.map(async code=>{
   const result=(await adapter.request(`/activities/${encodeURIComponent(target.activityId)}/versions/${encodeURIComponent(target.versionId)}/registration-drafts/${encodeURIComponent(code)}`,{signal})).data;
   if(result.activityId!==target.activityId||result.versionId!==target.versionId||result.platformCode!==code)throw new Error('报名清单与当前活动、版本或平台不一致，请刷新核对。');
   return result;
  }));
  return {...all.find(item=>item.platformCode===platformCode),platforms,platformDrafts:all};
 }catch(error){
  if(error.name==='AbortError'||error.status===401)throw error;
  return {...target,platforms,rows:[],error:error.status===403||error.status===404?'当前账号无法读取本版本的平台拟报名清单。':error.message??'拟报名清单暂不可读，请刷新重试。'};
 }
}

// Call before the owning page paints its busy/error state. The captured values
// remain in state, so an unsuccessful submission does not erase removals.
export function captureRegistrationReview(state,root=globalThis.document){
 if(!state)return;
 state.removalInputs??={};state.scopeInputs??={};
 for(const input of root.querySelectorAll('[data-review-remove-reason]'))state.removalInputs[input.dataset.reviewRemoveReason]=input.value;
 for(const input of root.querySelectorAll('[data-review-scope-text]'))state.scopeInputs[input.dataset.reviewScopeText]={...(state.scopeInputs[input.dataset.reviewScopeText]??{}),scopeText:input.value};
 for(const input of root.querySelectorAll('[data-review-scope-source]'))state.scopeInputs[input.dataset.reviewScopeSource]={...(state.scopeInputs[input.dataset.reviewScopeSource]??{}),sourceRef:input.value};
}
const key=(platform,row)=>JSON.stringify([platform,row.candidateId,row.rowId]);
export function registrationReviewRequest(version,state,root=globalThis.document){
 if(!productReviewEnabled(version))return undefined;
 if(state?.error||!state?.platformDrafts)throw new Error('请先读取并核对各平台的拟报名清单。');
 captureRegistrationReview(state,root);
 if(version.review?.stage==='initial'){
  if(state.platformDrafts.some(item=>item.stale))throw new Error('商品来源已变化，请刷新并重新核对选品。');
  const planned=state.platformDrafts.flatMap(draft=>(draft.rows??[]).filter(row=>registrationDecision(row)==='planned'));
  if(!planned.length)throw Object.assign(new Error('请先进入商品明细，勾选拟报名商品。'),{code:'REGISTRATION_SELECTION_REQUIRED'});
  const current=new Map((version.productRecognition?.rows??[]).map(row=>[key('',row),row]));
  for(const row of planned){
   const source=current.get(key('',row))??row.sourceRow;
   // Older drafts without a saved price source are checked against the current bound source by the server.
   if(!source)continue;
   const price=source.activityPriceCandidate;
   if((price?.status==null||['ready','needs_review'].includes(price.status))&&typeof price?.amountDecimal==='string'&&
      /^[0-9]{1,24}(?:\.[0-9]{1,16})?$/.test(price.amountDecimal))continue;
   const name=row.productSnapshot?.name||source.rawName||row.internalCode||row.rowId;
   throw Object.assign(new Error('拟报名商品「'+name+'」缺少可核对的活动价，请先在商品明细核对。'),{code:'REGISTRATION_ACTIVITY_PRICE_REQUIRED'});
  }
  return {platforms:state.platformDrafts.map(item=>({platformCode:item.platformCode,expectedDraftRevision:item.revision,bindingDigest:item.bindingDigest}))};
 }
 if(version.review?.stage!=='final')return {};
 const removals=[];
 for(const draft of state.platformDrafts)for(const row of draft.reviewSnapshot?.initialRows??[]){
  const reason=state.removalInputs[key(draft.platformCode,row)]?.trim();
  if(reason)removals.push({platformCode:draft.platformCode,candidateId:row.candidateId,rowId:row.rowId,reason});
 }
 const confirmedStoreScopes=[];
 for(const [platformCode,value] of Object.entries(state.scopeInputs)){
  if(!value.scopeText?.trim()&&!value.sourceRef?.trim())continue;
  if(!value.scopeText?.trim()||!value.sourceRef?.trim())throw new Error('填写门店范围时，请同时填写核实依据；未核实可留空，下载时会提示补齐。');
  confirmedStoreScopes.push({platformCode,scopeText:value.scopeText.trim(),sourceRef:value.sourceRef.trim()});
 }
 return {removals,confirmedStoreScopes};
}

export function reviewRegistrationHtml(version,state){
 if(!productReviewEnabled(version))return version.couponReview?'<h3>适用商品</h3><p>各档满减券的商品范围见券档明细。</p>':'<h3>商品与适用范围</h3><p class="muted">本版本未开放逐商品报名。</p>';
 if(!state)return '<h3>拟报名商品</h3><p class="muted">正在读取本版本各平台的拟报名清单…</p>';
 if(state.error)return `<h3>拟报名商品</h3><p class="warning" role="alert">${h(state.error)}</p>`;
 const stage=version.review?.stage,finalEditing=stage==='final'&&version.allowedActions?.some(action=>(typeof action==='string'?action:action.action)==='reviews.decide');
 const recognition=new Map((version.productRecognition?.rows??[]).map(row=>[JSON.stringify([row.candidateId,row.rowId]),row]));
 const blocks=(state.platformDrafts??[state]).map(draft=>{
  const frozen=draft.reviewSnapshot,platformName=APPLICABLE_PLATFORM_LABELS[draft.platformCode]??draft.platformCode;
  const selected=frozen?(frozen.status==='finalized'?frozen.finalRows:frozen.initialRows):(draft.rows??[]).filter(row=>registrationDecision(row)==='planned');
  const body=(selected??[]).map(row=>{
   const current=recognition.get(JSON.stringify([row.candidateId,row.rowId]));
   const product=row.productSnapshot??{},source=current??row.sourceRow??{};
   const price=frozen?row.activityPrice:source.activityPriceCandidate;
   const signup=frozen?row.signupPrice:source.signupPriceCandidate;
   const margin=row.trialMargin??source.operatingMetrics?.trialMargin??{};
   const quote=frozen?row.agentSupplyPrices:source.agentSupplyPrices;
   const cells=[h(source.rawName||row.sourceRow?.rawName||'原表未提供'),h(product.name||'目录名称待核对'),h(row.internalCode||'未唯一核对'),h(product.spec||source.rawSpec||'待核实'),h(signup?.amountDecimal??'待核实'),h(price?.amountDecimal??'缺活动价'),h(quote?.status==='reference_only'?quote.referenceQuote?.amountDecimal??'待核对':'待核对'),h(margin.status==='ready'?`${margin.amountDecimal} / ${margin.ratePercentDecimal??'待核实'}%（试算）`:'无可用毛利依据')];
   if(finalEditing)cells.push(`<label>移除原因（留空表示保留）<textarea data-review-remove-reason="${h(key(draft.platformCode,row))}" maxlength="2000">${h(state.removalInputs?.[key(draft.platformCode,row)]??'')}</textarea></label>`);
   return rows(cells);
  });
  const headers=['原表商品名','公司商品名','内部码','规格','报名价','活动价','参考供货价','可用毛利'];if(finalEditing)headers.push('终审调整');
  const priorScope=frozen?.confirmedStoreScope??{},input=state.scopeInputs?.[draft.platformCode]??priorScope;
  return `<section class="soft"><h4>${h(platformName)} · ${frozen?.status==='finalized'?'最终确认':'初审'+(frozen?'冻结':'待确认')}清单（${selected?.length??0}）</h4>`+
   (draft.stale&&!frozen?'<p class="warning">商品来源已变化，请刷新后重新核对。</p>':'')+
   (body.length?table(headers,body):'<p class="muted">此平台暂无拟报名商品。</p>')+
   (finalEditing?`<p class="hint">终审只能移除商品并说明原因；新增或替换请退回重审。</p><label>已确认门店范围（未核实可留空）<textarea data-review-scope-text="${h(draft.platformCode)}" maxlength="2000">${h(input.scopeText??'')}</textarea></label><label>门店范围依据<textarea data-review-scope-source="${h(draft.platformCode)}" maxlength="2000">${h(input.sourceRef??'')}</textarea></label>`:'')+
   (frozen?.removals?.length?`<p>终审移除：${frozen.removals.map(r=>`${h(r.rowId)}：${h(r.reason)}`).join('；')}</p>`:'')+'</section>';
 });
 return '<h3>版本商品清单</h3>'+blocks.join('')+'<p class="hint">初审通过确认选品身份并冻结当时的商品、活动价和可用毛利依据；终审结果用于内部待报名清单，平台实际报名另行记录。</p>';
}
