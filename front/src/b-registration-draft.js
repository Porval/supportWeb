import {h} from './b-common.js';
import {productReviewEnabled} from './b-review-registration.js';
import {SORT_LABELS,querySales,salesComparison,salesStatus} from './product-metrics.js';
import {productRecognitionHtml,productComparisonHtml,productRowKey as keyOf,registrationDecision} from './ui.js';
const ACTION='activity_registration_drafts.decide';
export function createRegistrationDraftController({root,adapter,commands,mountSales}){
 let state=null,recognition=null,scope=null,epoch=0,dialog=null,observer=null;
 let salesSignal=null,salesEpoch=null;
 const path=()=>`/activities/${encodeURIComponent(scope.activityId)}/versions/${encodeURIComponent(scope.versionId)}/registration-drafts/${encodeURIComponent(state.platformCode)}`;
 const legacySlot=()=>`registration-draft:${scope.activityId}:${scope.versionId}:${state.platformCode}`;
 const slot=key=>legacySlot()+':row:'+key;
 const operations=()=>Object.values(state?.rowOperations??{});
 const hasChanges=()=>!!state?.legacyPending||operations().some(op=>op.busy||op.pending);
 function close(){dialog?.remove();dialog=null}
 function paint(){
  const box=root.querySelector('[data-product-recognition]');if(!box||!recognition)return;
  const groups=[...box.querySelectorAll('[data-recognition-group]')].map(e=>({key:e.dataset.recognitionGroup,open:e.open}));
  const active=globalThis.document?.activeElement,focus=box.contains?.(active)?['data-row-key','data-row-decision','data-product-sort','data-product-page','data-row-retry'].map(name=>[name,active.getAttribute(name)]).filter(([,value])=>value!=null):[];
  box.outerHTML=productRecognitionHtml(recognition,state);
  mountSales?.();
  for(const group of groups){const element=root.querySelector(`[data-recognition-group="${group.key}"]`);if(element)element.open=group.open}
  if(focus.length){const button=[...root.querySelectorAll('[data-product-recognition] button')].find(el=>focus.every(([name,value])=>el.getAttribute(name)===value));if(!button?.disabled)button?.focus({preventScroll:true})}
 }
 function mount(){
  observer?.disconnect();observer=null;
  void refreshSales();
  const panel=root.querySelector('#goods');if(!panel||!state||!globalThis.ResizeObserver)return;
  const resize=()=>{const box=root.querySelector('[data-product-recognition]');if(!box||!state)return;const size=box.getBoundingClientRect().width>=960?16:12;if(state.pageSize!==size){state.page=Math.floor(((state.page??1)-1)*(state.pageSize??16)/size)+1;state.pageSize=size;paint()}};
  observer=new ResizeObserver(resize);observer.observe(panel);resize();
 }
 async function refreshSales(){
  // The finalized table loads sales through its own visible product cells.
  if(state?.reviewSnapshot?.status==='finalized')return;
  const signal=salesSignal, platform=state?.platformCode, target=scope, token=epoch;
  if(!signal||signal.aborted||!target||!['meituan','taobao_instant'].includes(platform)||salesEpoch===token)return;
  const codes=[...new Set((recognition?.rows??[]).filter(row=>row.uniqueUpcHit&&row.candidates?.length===1)
   .map(row=>row.candidates[0].internalCode).filter(Boolean))];
  if(!codes.length)return;
  salesEpoch=token;
  for(let offset=0;offset<codes.length;offset+=25){
   const batch=codes.slice(offset,offset+25);
   const result=await querySales(adapter,platform,'trailing_28_days_compare',batch,signal,target).catch(()=>null);
   if(signal.aborted||epoch!==token)return;
   const fetched=new Map((result?.rows??[]).map(row=>[row.internalCode,row]));
   recognition={...recognition,rows:recognition.rows.map(row=>{
    const code=row.candidates?.[0]?.internalCode;
    if(!row.uniqueUpcHit||row.candidates.length!==1||!batch.includes(code))return row;
    const item=fetched.get(code),period=item?.periods.find(period=>period.key==='current');
    const ready=period?.status==='ready'&&period.salesQty!=null;
    const prior=row.operatingMetrics?.sales28d;
    const availability=result?.status==='source_unavailable'&&prior?.status==='ready'&&prior.platformCode===platform;
    if(availability)return row;
    const window=result?.periods?.find(period=>period.key==='current');
    const sales={status:ready?'ready':period?.status??'unavailable',
     quantity:ready?period.salesQty:null,recordedQty:period?.recordedSalesQty,
     startDate:window?.startDate,endDate:window?.endDate,platformCode:platform,
     comparison:result&&result.status!=='source_unavailable'?salesComparison(item):null,
     statusLabel:salesStatus(result,period),sourceSummary:'数据 Agent · 平台商品日报销量，退款扣减口径待核实。'};
    return {...row,operatingMetrics:{...row.operatingMetrics,sales28d:sales}};
   })};
   paint();
  }
 }
 async function read(signal){
  const target={...scope,platformCode:state.platformCode},loaded=(await adapter.request(path(),{signal})).data;
  if(loaded.activityId!==target.activityId||loaded.versionId!==target.versionId||loaded.platformCode!==target.platformCode)throw new Error('报名状态范围不一致，已停止展示');
  return loaded;
 }
 function recover(){
  state.rowOperations={};
  for(const row of recognition.rows){const key=keyOf(row),record=commands.get(slot(key));if(record?.state==='unknown')state.rowOperations[key]={pending:true,body:record.body??null,message:'上次保存结果未确认，请核对结果后继续。'}}
  state.legacyPending=commands.get(legacySlot())?.state==='unknown';
 }
 async function load(activity,version,result,signal){
  result=result??{status:'unavailable',rows:[]};
  const nextScope=version?{activityId:activity.id,versionId:version.id}:null;
  if(!nextScope){clear();return null}
  const same=scope?.activityId===nextScope.activityId&&scope?.versionId===nextScope.versionId;
  const platforms=(version.snapshot?.applicablePlatformCodes??[]).filter(code=>['meituan','taobao_instant','jd'].includes(code));
  const methodSupported=productReviewEnabled(version);
  if(!platforms.length){clear();return {rows:[],platforms:[],methodSupported,error:'该版本尚未声明适用平台，暂不能设置报名状态。'}}
  salesSignal=signal;
  close();
  if(same&&state&&hasChanges()){
   if(JSON.stringify(recognition?.binding)!==JSON.stringify(result.binding)||recognition?.derivedAnalysisDigest!==result.derivedAnalysisDigest)state.stale=true;
   recognition={...result,rows:result.rows??[]};return state;
  }
  recognition={...result,rows:result.rows??[]};
  const previous=same?state:null;
  epoch++;scope=nextScope;state={sort:previous?.sort,page:previous?.page??1,pageSize:previous?.pageSize??16,platformCode:platforms.includes(previous?.platformCode)?previous.platformCode:platforms[0],platforms,methodSupported,rows:[],rowOperations:{}};
  const token=epoch;
  try{const loaded=await read(signal);if(token!==epoch)return null;state={...state,...loaded};recover();return state}
  catch(e){if(token!==epoch||e.name==='AbortError'||e.status===401)throw e;state={...state,error:e.message??'报名状态读取失败'};return state}
 }
 function clear(){epoch++;close();observer?.disconnect();observer=null;state=null;scope=null;recognition=null;salesSignal=null}
 function show(html,label){close();dialog=document.createElement('dialog');dialog.className='product-compare-dialog';dialog.setAttribute('aria-label',label);dialog.innerHTML=html;root.append(dialog);dialog.addEventListener('cancel',close,{once:true});dialog.showModal()}
 function replaceRow(key,row){state.rows=state.rows.filter(r=>keyOf(r)!==key);if(row)state.rows.push(row)}
 function metadata(saved){if(saved.revision>=(state.revision??0)){state.revision=saved.revision;state.updatedBy=saved.updatedBy;state.updatedAt=saved.updatedAt}}
 async function decide(key,choice,retry=false){
  const source=recognition?.rows.find(r=>keyOf(r)===key),oldOp=state?.rowOperations?.[key];
  if(!source||!state.canEdit||state.stale||state.error||state.legacyPending||oldOp?.busy||oldOp?.pending&&!retry)return;
  const prior=structuredClone(state.rows.find(r=>keyOf(r)===key)??null),token=epoch;
  const body=retry?oldOp?.body:{expectedRowRevision:prior?.rowRevision??0,bindingDigest:state.bindingDigest,candidateId:source.candidateId,rowId:source.rowId,decision:registrationDecision(prior)===choice?'pending':choice};
  const requestPath=path()+'/decisions',requestSlot=slot(key);
  state.rowOperations[key]={busy:true,pending:retry,body};
  if(body)replaceRow(key,{...prior,candidateId:source.candidateId,rowId:source.rowId,decision:body.decision,selected:body.decision==='planned'});
  paint();
  try{
   let result;if(retry)result=(await commands.resolve(requestSlot)).result;
   if(!result){if(!body)throw new Error('原请求正文不可用，请重新登录后读取最新状态。');result=await commands.run(requestSlot,ACTION,'POST',requestPath,body)}
   if(token!==epoch)return;
   const saved=result.data;
   if(saved.activityId!==scope.activityId||saved.versionId!==scope.versionId||saved.platformCode!==state.platformCode||!saved.row||keyOf(saved.row)!==key)throw new Error('返回的商品范围不一致，请核对结果');
   replaceRow(key,saved.row);metadata(saved);state.rowOperations[key]={message:'已保存'};
  }catch(e){
   if(token!==epoch)return;
   replaceRow(key,prior);
   if(e.status===401||e.status===403){clear();throw e}
   state.rowOperations[key]={message:e.message??'保存失败，已恢复原状态，可再次点击重试。',pending:e.code==='COMMAND_UNKNOWN',body};
   if(e.code==='SOURCE_CHANGED')state.stale=true;
   if(e.code==='DRAFT_ROW_CONFLICT'){
    // Merge only this row. Another in-flight row keeps its own optimistic state.
    try{const latest=await read();if(token!==epoch)return;replaceRow(key,latest.rows.find(r=>keyOf(r)===key));metadata(latest);state.rowOperations[key]={message:'其他人已更新此商品，已显示最新状态；可重新选择。'}}
    catch{if(token===epoch)state.rowOperations[key].message='其他人已更新此商品，请刷新状态后重试。'}
   }
  }finally{if(token===epoch&&state){if(state.rowOperations[key])state.rowOperations[key].busy=false;paint()}}
 }
 function summaryHtml(){
  const final=state.formalList??{},snapshot=state.reviewSnapshot,selected=snapshot?.status==='finalized'?final.rows??[]:state.rows.filter(row=>registrationDecision(row)==='planned');
  const scope=final.confirmedStoreScope??{};
  return `<div class="section-heading"><h2>内部待报名清单</h2><button type="button" data-product-compare-close>关闭</button></div><p>${h(state.platformCode)} · ${snapshot?.status==='finalized'?'最终确认':'拟报名'} ${selected.length} 项</p><ul>${selected.map(row=>`<li>${h(row.productSnapshot?.name||row.sourceRow?.rawName||row.rowId)} · ${h(row.internalCode??'内部码待核实')}</li>`).join('')}</ul>`+
   (final.reasons?.length?`<h3>尚缺条件</h3><ul>${final.reasons.map(reason=>`<li>${h(reason)}</li>`).join('')}</ul>`:'<p>已满足下载条件，清单与终审结果一致。</p>')+
   (snapshot?.status==='finalized'&&state.canManageFinal?`<label>已确认门店范围<textarea data-final-scope maxlength="2000">${h(scope.scopeText??'')}</textarea></label><label>核实依据<textarea data-final-scope-source maxlength="2000">${h(scope.sourceRef??'')}</textarea></label><button type="button" data-final-scope-save>保存门店范围</button>`:'')+
   `<p class="hint">下载内部清单后由运营到平台操作，下载不表示已报名。</p><p data-final-message role="status"></p><button type="button" data-final-download${!final.available||!state.canManageFinal?' disabled':''}>下载最终清单</button>`;
 }
 async function finalAction(action){
  const token=epoch,message=dialog?.querySelector('[data-final-message]');
  if(!state?.canManageFinal)return;
  try{
   if(action==='scope'){
    const body={expectedRevision:state.formalList.revision,scopeText:dialog.querySelector('[data-final-scope]').value,sourceRef:dialog.querySelector('[data-final-scope-source]').value};
    if(!body.scopeText.trim()||!body.sourceRef.trim())throw new Error('请填写已确认门店范围和核实依据。');
    await commands.run(legacySlot()+':scope:'+body.expectedRevision,'activity_registration_reviews.confirm_scope','POST',path()+'/final-list/store-scope',body);
    const loaded=await read();if(token!==epoch)return;state={...state,...loaded};show(summaryHtml(),'内部待报名清单');paint();
   }else{
    const response=await adapter.request(path()+'/final-list',{binary:true});if(token!==epoch)return;
    const blob=new Blob([response.bytes],{type:response.contentType??'text/csv;charset=utf-8'}),url=URL.createObjectURL(blob),anchor=document.createElement('a');
    anchor.href=url;anchor.download=`${scope.activityId}-${scope.versionId}-${state.platformCode}-内部待报名清单.csv`;anchor.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
    if(message)message.textContent='已生成最终确认清单；请核对浏览器下载结果。';
   }
  }catch(error){if(error.status===401||error.status===403)throw error;if(message)message.textContent=error.message??'操作失败，请刷新核对后重试。'}
 }
 async function reload(){
  if(!state||hasChanges())return;
  const token=++epoch;close();
  try{const latest=await read();if(token!==epoch)return;state={...state,...latest,error:null,message:'已载入最新报名状态。'};recover();paint();void refreshSales()}
  catch(e){if(token===epoch){state.message=e.message??'读取失败，已保留当前状态。';paint()}if(e.status===401||e.status===403)throw e}
 }
 async function change(target){
  if(!target.matches('[data-draft-platform]'))return false;
  if(hasChanges()||!state.platforms.includes(target.value))return true;
  const token=++epoch;state={...state,platformCode:target.value,rows:[],rowOperations:{},canEdit:false,error:null,message:'正在读取报名状态…',page:1};close();paint();
  try{const loaded=await read();if(token===epoch){state={...state,...loaded,message:null};recover();paint();void refreshSales()}}
  catch(e){if(token===epoch){state.error=e.message??'读取失败';paint()}if(e.status===401||e.status===403)throw e}
  return true;
 }
 async function click(target){
  const sort=target.closest('[data-product-sort]');if(sort){if(state&&SORT_LABELS[sort.dataset.productSort]){const field=sort.dataset.productSort;state.sort={field,direction:state.sort?.field===field&&state.sort.direction==='asc'?'desc':'asc'};state.page=1;paint()}return true}
  const page=target.closest('[data-product-page]');if(page){if(state){state.page=Math.max(1,Number(page.dataset.productPage)||1);paint()}return true}
  if(target.closest('[data-product-compare-close]')){close();return true}
  const compare=target.closest('[data-product-compare]');if(compare){const row=recognition?.rows.find(r=>keyOf(r)===compare.dataset.productCompare);if(row)show(productComparisonHtml(row,null,recognition),'商品详情');return true}
  const button=target.closest('[data-row-decision]');if(button){await decide(button.dataset.rowKey,button.dataset.rowDecision);return true}
  const retry=target.closest('[data-row-retry]');if(retry){await decide(retry.dataset.rowRetry,null,true);return true}
  if(target.closest('[data-final-scope-save]')){await finalAction('scope');return true}
  if(target.closest('[data-final-download]')){await finalAction('download');return true}
  if(target.closest('[data-product-enroll]')){if(state&&!state.stale&&!state.error&&!hasChanges())show(summaryHtml(),'内部待报名清单');return true}
  if(target.closest('[data-draft-reload]')){await reload();return true}
  if(target.closest('[data-legacy-retry]')){
   if(state?.legacyPending){const token=epoch;let result=(await commands.resolve(legacySlot())).result;if(!result)await commands.retry(legacySlot());if(token===epoch){state.legacyPending=false;await reload()}}return true;
  }
  return false;
 }
 return {load,clear,change,click,mount,hasChanges};
}
