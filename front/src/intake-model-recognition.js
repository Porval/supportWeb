import {h,locatorLabel,beijing,evidenceList} from './b-common.js';
import {activityDate,PLATFORM_LABELS} from './dto.js';

const CONTRACT='activity-model-recognition/1.0';
const IN_PROGRESS=new Set(['queued','running']);
const STATUS={
 queued:'本次识别已排队',running:'DeepSeek 正在识别',succeeded:'DeepSeek 建议已返回',
 failed:'本次识别未完成',cancelled:'本次识别已取消',interrupted:'本次识别已中断',stale:'提报已变化，本次识别已过期'
};
const REQUEST_ERRORS={
 MODEL_RECOGNITION_INPUT_INVALID:'请选择当前预览中的文字，再确认发送。',
 MODEL_RECOGNITION_STALE:'当前输入或预览已变化，请刷新提报后重新选择。',
 MODEL_RECOGNITION_ACTIVE:'本次分析已有进行中的识别，正在核对进度。',
 MODEL_RECOGNITION_ALREADY_SUCCEEDED:'本次分析已有 DeepSeek 建议，正在读取。',
 MODEL_RECOGNITION_CAPACITY:'识别服务正忙。请稍后重新预览和确认。',
 MODEL_RECOGNITION_UNAVAILABLE:'DeepSeek 识别暂不可用，可继续补充或人工核对。'
};
const FIELD_LABELS={
 activityName:'活动名称',platformCode:'报名编号所属平台',sourceActivityId:'平台活动编号',
 startAt:'开始日期（北京时间）',endAt:'结束日期（北京时间）',rulesText:'优惠机制',brandBenefit:'品牌利益点',brandNameText:'品牌原文名称'
};
const LIMITS={activityName:200,platformCode:32,sourceActivityId:200,startAt:200,endAt:200,rulesText:10000,brandBenefit:5000,brandNameText:200};
const identityKeys=['platformCode','sourceActivityId'];
const REVIEWABLE_TEXT=new Set(['activityName','rulesText','brandBenefit']);
const dated=key=>key==='startAt'||key==='endAt';
const preciseDate=/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$/;
const sameValue=(key,first,second)=>dated(key)?activityDate(first)===activityDate(second):first===second;
const validValue=(key,value)=>typeof value==='string'&&value.trim()&&value.length<=LIMITS[key]&&
 (key!=='platformCode'||Object.hasOwn(PLATFORM_LABELS,value))&&(!dated(key)||Boolean(activityDate(value)));
const hasSuggestion=item=>Boolean(item.evidence.length&&validValue(item.field,item.value));
const displayValue=(key,value)=>key==='platformCode'?PLATFORM_LABELS[value]??'待核对':dated(key)?activityDate(value)??'待核对':value;
const endpoint=run=>'/analysis-runs/'+encodeURIComponent(run.runId)+'/model-recognition';
const action=(name,label,disabled=false,attrs='')=>`<button type="button" data-sub-model="${name}"${disabled?' disabled':''}${attrs}>${h(label)}</button>`;
const recordFor=run=>run?.modelRecognition?.contractVersion===CONTRACT&&Object.hasOwn(STATUS,run.modelRecognition.status)?run.modelRecognition:null;
const capabilityOf=catalog=>{
 const capability=catalog?.modelRecognition;
 return capability?.contractVersion===CONTRACT&&capability.provider==='deepseek'?capability:null;
};
const sourceName=(excerpt,materials)=>materials.find(item=>item.materialId===excerpt.materialId)?.originalName??
 materials.find(item=>item.materialId===excerpt.materialId)?.filename??'原始文字';

function validPreview(preview,run){
 return preview?.runId===run.runId&&preview.inputRevision===run.inputRevision&&preview.analysisDigest===run.analysisDigest&&
  typeof preview.previewDigest==='string'&&Array.isArray(preview.excerpts)&&preview.excerpts.length<=48&&
  new Set(preview.excerpts.map(item=>item.excerptId)).size===preview.excerpts.length&&
  preview.excerpts.every(item=>typeof item.excerptId==='string'&&typeof item.materialId==='string'&&typeof item.text==='string');
}

function previewHtml(preview,selected,materials,busy,recovery){
 const chosen=preview.excerpts.filter(item=>selected.has(item.excerptId));
 return `<section class="b-model-preview" aria-label="DeepSeek 发送预览"><h4>选择要识别的原文</h4>
  <p>本次将把勾选的文字发送给 DeepSeek 识别。请核对片段内是否含有你不希望外发的信息。</p>
  ${preview.truncated?'<p class="hint">原文超过预览上限，本次仅可选择以下片段。</p>':''}
  ${preview.excerpts.length?`<div class="b-model-excerpts">${preview.excerpts.map(item=>`<article><label>
   <input type="checkbox" data-model-excerpt="${h(item.excerptId)}"${selected.has(item.excerptId)?' checked':''}${busy?' disabled':''}>
   ${h(sourceName(item,materials))} · ${h(locatorLabel(item.locator))}</label><pre class="b-json"><span>${h(item.text)}</span></pre></article>`).join('')}</div>
   <p class="hint" role="status" data-model-count>已选 ${chosen.length} 段文字</p>`:
   '<p>当前没有可发送的原文。可将活动说明粘贴到「补充说明」，保存并重新分析后再来选择。</p>'}
  <div class="actions">${action('close-preview','取消',busy)}
   ${preview.excerpts.length?action('send',recovery?'确认按原请求恢复':'确认发送',!chosen.length||busy,' class="primary"'):''}</div>
 </section>`;
}

function matchingFields(group,evidence){
 return (group.fields??[]).filter(item=>Object.hasOwn(FIELD_LABELS,item.field)).map(item=>({
  ...item,evidence:evidence.filter(entry=>item.evidenceIds?.includes(entry.evidenceId)&&typeof entry.quote==='string'&&entry.quote)
 }));
}

function groupRows(group,evidence){
 const fields=matchingFields(group,evidence);
 const pair=identityKeys.map(key=>fields.find(item=>item.field===key)).filter(Boolean);
 const sameGroup=pair.length===2&&pair.every(item=>item.confidence==='supported');
 const normal=fields.filter(item=>!identityKeys.includes(item.field)).map(item=>({fields:[item],paired:true}));
 return pair.length?[...normal,{fields:pair,paired:sameGroup}]:normal;
}

function canApplyRow(row){
 return row.paired&&row.fields.every(item=>item.field!=='brandNameText'&&hasSuggestion(item)&&(!dated(item.field)||preciseDate.test(item.value))&&
  (item.confidence==='supported'||item.confidence==='needs_review'&&REVIEWABLE_TEXT.has(item.field)));
}

function suggestionsHtml(record,{run,materials,values,applyTarget=()=>null,editable=false}){
 const suggestions=record.suggestions;
 if(!suggestions?.groups?.length)return '<p>本次暂未得到可核对的建议。可继续使用原提报结果。</p>';
 const groups=suggestions.groups.map((group,groupIndex)=>{
  const candidate=run.result?.candidates?.find(item=>item.candidateId===group.candidateId);
  const name=candidate?.fields?.activityName?.value;
  const current=values(group.candidateId);
  const target=applyTarget(group.candidateId);
  const rows=groupRows(group,suggestions.evidence??[]).map((row,rowIndex)=>{
   const conflict=row.fields.some(item=>hasSuggestion(item)&&current[item.field]!=null&&
    current[item.field]!==''&&!sameValue(item.field,current[item.field],item.value));
   const needsReview=!row.paired||row.fields.some(item=>item.confidence!=='supported'||!hasSuggestion(item));
   const actionable=target&&canApplyRow(row)&&row.fields.some(item=>!sameValue(item.field,current[item.field],item.value));
   const lines=row.fields.map(item=>`<div><dt>${h(FIELD_LABELS[item.field])}</dt>
    <dd>当前记录：${h(current[item.field]==null||current[item.field]===''?'未填写':displayValue(item.field,current[item.field]))}</dd>
    <dd>建议：${h(hasSuggestion(item)?displayValue(item.field,item.value):'暂无法识别')}</dd></div>`).join('');
   const excerpts=[...new Map(row.fields.flatMap(item=>item.evidence).map(item=>[item.evidenceId,item])).values()];
   return `<div class="b-model-suggestion"><dl class="info-grid">${lines}</dl>
    ${conflict||needsReview&&!row.fields.some(item=>item.field==='brandNameText')?`<p class="hint">${conflict?'与当前记录有差异，待核对。':'建议或依据待核对。'}</p>`:''}
    ${row.fields.some(item=>item.field==='brandNameText'&&hasSuggestion(item))?'<p class="hint">可用原文名称在品牌目录中检索并人工选择。</p>':''}
    ${row.fields.some(item=>identityKeys.includes(item.field))&&!row.paired?'<p class="hint">请根据原文一并核对报名编号和所属平台。</p>':''}
    ${excerpts.length?`<details><summary>查看原文依据</summary>${evidenceList(excerpts,materials)}</details>`:'<p class="muted">暂未提供可用原文依据。</p>'}
    ${actionable?action('adopt',row.fields.length>1?'采用该平台和编号':needsReview?'核对后采用':'采用建议',false,` data-model-group="${groupIndex}" data-model-row="${rowIndex}"`):''}</div>`;
  }).join('');
  return `<article class="b-candidate"><h4>${name?h(name):candidate?'分组 '+(groupIndex+1):'待人工定位的活动 '+(groupIndex+1)}</h4>
   ${editable&&!candidate?'<p class="hint">请按活动名称把确认的信息写入补充说明，再保存并重新分析。</p>':
    editable&&!target?'<p class="hint">可在本组人工核对表单中采用有依据的建议。</p>':''}
   ${rows||'<p class="muted">暂无法识别可核对的字段。</p>'}</article>`;
 }).join('');
 return groups;
}

function recordHtml(record,context){
 const status=STATUS[record.status];
 return `<p role="status">${h(status)}</p>
  ${record.status==='failed'?'<p class="hint">可继续原补充流程。需要再次识别时，请重新预览并确认。</p>':''}
  ${record.status==='cancelled'?'<p class="hint">已发送的文字可能仍在 DeepSeek 处理。</p>':''}
  ${record.status==='stale'&&!record.suggestions?'<p class="hint">当前历史记录只提供识别状态。</p>':''}
  ${['succeeded','stale'].includes(record.status)&&record.suggestions?suggestionsHtml(record,context):record.status==='succeeded'?'<p>本次暂未得到可核对的建议。</p>':''}`;
}

export function modelRecognitionRecordHtml(run,input,materials){
 const record=recordFor(run);
 if(!record)return '';
 const values=id=>(run.result?.candidates?.length??0)<=1&&input?.form?input.form:Object.fromEntries(Object.entries(
  run.result?.candidates?.find(item=>item.candidateId===id)?.fields??{}).map(([key,value])=>[key,value.value]));
 return `<details><summary>DeepSeek 建议 · 输入修订 ${h(run.inputRevision)}</summary>
  ${record.finishedAt?`<p class="hint">完成时间（北京时间）${h(beijing(record.finishedAt))}</p>`:''}
  ${recordHtml(record,{run,materials,values})}</details>`;
}

const START='analysis.model_recognition.start';
const CANCEL='analysis.model_recognition.cancel';
const modelCommand=record=>[START,CANCEL].includes(record.action);
const modelFailure={
 MODEL_RECOGNITION_TIMEOUT:'DeepSeek 响应超时，可重试当前修订。',
 MODEL_RECOGNITION_AUTH_FAILED:'DeepSeek 调用权限验证失败。',
 MODEL_RECOGNITION_CAPACITY:'DeepSeek 当前额度或频率受限。',
 MODEL_RECOGNITION_PROVIDER_AUTH:'DeepSeek 服务暂时无法验证调用权限。',
 MODEL_RECOGNITION_PROVIDER_QUOTA:'DeepSeek 当前额度或频率受限。',
 MODEL_RECOGNITION_UNAVAILABLE:'DeepSeek 识别暂不可用。',
 MODEL_RECOGNITION_PROVIDER_UNAVAILABLE:'DeepSeek 服务暂时无法访问。',
 MODEL_RECOGNITION_INPUT_INVALID:'当前片段未通过识别服务校验。',
 MODEL_RECOGNITION_NO_ACTIVITIES:'本次未识别到活动。请检查材料与所选片段后重新识别。',
 MODEL_RECOGNITION_INVALID_OUTPUT:'返回内容未通过本地校验。'
};

export function createModelRecognitionController(ctx,host,options={}){
 const recordFor=options.recordFor??(run=>run?.modelRecognition?.contractVersion===CONTRACT&&Object.hasOwn(STATUS,run.modelRecognition.status)?run.modelRecognition:null);
 const endpoint=options.endpoint??(run=>'/analysis-runs/'+encodeURIComponent(run.runId)+'/model-recognition');
 const START=options.startAction??'analysis.model_recognition.start';
 const CANCEL=options.cancelAction??'analysis.model_recognition.cancel';
 const modelCommand=record=>[START,CANCEL].includes(record.action);
 const capabilityOf=options.capabilityOf??(catalog=>catalog?.modelRecognition?.contractVersion===CONTRACT?catalog.modelRecognition:null);

 let preview=null,selected=new Set(),record=null,operation=null,message='',refreshNeeded=false,pollTimer=null,failures=0;
 const modelPending=(run,action)=>ctx.commands.pending?.().find(item=>item.action===action&&
  item.path===endpoint(run)+(action===CANCEL?'/cancel':''));
 const get=()=>host.context();
 const enabled=binding=>binding?.canStart&&capabilityOf(binding.catalog)?.enabled===true;
 const sameRun=(current,prior)=>current?.run?.runId===prior?.run?.runId&&
  current?.run?.inputRevision===prior?.run?.inputRevision&&current?.run?.analysisDigest===prior?.run?.analysisDigest;
 const currentView=prior=>{
  const current=get();
  return current?.signal===prior.signal&&!prior.signal?.aborted&&sameRun(current,prior);
 };
 const updateSelection=()=>{
  const panel=document.querySelector('.b-model-preview');
  if(!panel)return;
  const count=panel.querySelector('[data-model-count]');
  const button=panel.querySelector('[data-sub-model="send"]');
  if(count)count.textContent='已选 '+selected.size+' 段文字';
  if(button)button.disabled=!selected.size||!!operation;
 };
 function closePreview(){
  preview=null;
  selected.clear();
 }
 function pause(){
  clearTimeout(pollTimer);
 }
 function schedule(){
  pause();
  if(!get()?.run||!IN_PROGRESS.has(record?.status)||operation||refreshNeeded||failures>=3)return;
  pollTimer=setTimeout(()=>void refreshAttempt(),1800);
 }
 function sync(run){
  const incoming=recordFor(run);
  if(incoming&&record?.attemptId===incoming.attemptId&&IN_PROGRESS.has(incoming.status)&&
     record.status!=='queued'&&incoming.status!==record.status)return;
  record=incoming;
  schedule();
 }
 function reset(){
  pause();
  closePreview();
  record=null;
  operation=null;
  message='';
  refreshNeeded=false;
  failures=0;
 }
 async function readAttempt(binding){
  const response=await ctx.adapter.request('/analysis-runs/'+encodeURIComponent(binding.run.runId),{signal:binding.signal});
  if(!currentView(binding))return;
  const next=response.data;
  options.onRead?.(next);
  if(next?.runId!==binding.run.runId||next.inputRevision!==binding.run.inputRevision||next.analysisDigest!==binding.run.analysisDigest){
   message='当前分析已变化，请刷新提报后重新核对。';
   refreshNeeded=true;
   return;
  }
  const received=recordFor(next);
  if(received)sync(next);
  else if(!IN_PROGRESS.has(record?.status))record=null;
 }
 function requestError(error){
  return REQUEST_ERRORS[error?.code?.replace('PRIMARY_RECOGNITION_','MODEL_RECOGNITION_')]??(error?.status===403?'当前账号无权执行此识别操作。':
   error?.status===404?'本次分析或识别预览当前不可用。':
   error?.code==='COMMAND_UNKNOWN'?'识别操作的回执待核对。请先查询原请求。':
   '识别操作未完成，可继续补充或人工核对。');
 }
 async function operate(kind,perform){
  const binding=get();
  if(!binding?.run||operation||binding.signal?.aborted)return;
  const token={kind};
  operation=token;
  message='';
  refreshNeeded=false;
  host.repaint();
  try{
   await perform(binding);
  }catch(error){
   if(!currentView(binding)||ctx.authError?.(error))return;
   message=requestError(error);
   if(['MODEL_RECOGNITION_STALE','PRIMARY_RECOGNITION_STALE'].includes(error.code)){closePreview();refreshNeeded=true}
   if(['MODEL_RECOGNITION_ACTIVE','MODEL_RECOGNITION_ALREADY_SUCCEEDED','PRIMARY_RECOGNITION_ACTIVE','PRIMARY_RECOGNITION_ALREADY_SUCCEEDED'].includes(error.code)){
    closePreview();
    try{await readAttempt(binding)}catch(readError){ctx.authError?.(readError)}
   }
  }finally{
   if(operation===token)operation=null;
   if(currentView(binding)){host.repaint();schedule()}
  }
 }
 async function refreshAttempt(){
  const binding=get();
  if(!binding?.run||operation||binding.signal?.aborted)return;
  const token={kind:'refresh'};
  operation=token;
  try{
   await readAttempt(binding);
   if(currentView(binding)&&!refreshNeeded){message='';failures=0}
  }catch(error){
   if(!currentView(binding)||ctx.authError?.(error))return;
   failures++;
   message=failures>=3?'暂时无法读取识别进度。可手动刷新进度或继续补充。':'暂时无法读取识别进度。';
  }finally{
   if(operation===token)operation=null;
   if(currentView(binding)){host.repaint();schedule()}
  }
 }
 function receipt(binding,accepted){
  if(accepted?.runId!==binding.run.runId||typeof accepted.attemptId!=='string'||accepted.status!=='queued')return;
  if(record?.attemptId===accepted.attemptId&&record.status!=='queued')return;
  record={contractVersion:CONTRACT,status:accepted.status,attemptId:accepted.attemptId,inputRevision:binding.run.inputRevision,
   analysisDigest:binding.run.analysisDigest};
 }
 async function open(){
  const initial=get();
  await operate('preview',async binding=>{
   closePreview();
   const response=await ctx.adapter.request(endpoint(binding.run)+'/preview',{signal:binding.signal});
   if(!currentView(binding))return;
   if(!validPreview(response.data,binding.run)){
    message='预览与当前分析不一致，请刷新提报后重试。';
    refreshNeeded=true;
    return;
   }
   preview=response.data;
   const pending=modelPending(binding.run,START);
   if(pending?.body?.previewDigest===preview.previewDigest){
    const wanted=new Set(pending.body.excerptIds);
    selected=new Set(preview.excerpts.filter(item=>wanted.has(item.excerptId)).map(item=>item.excerptId));
   }
  });
  if(initial&&currentView(initial)&&preview)document.querySelector('.b-model-preview [data-model-excerpt], .b-model-preview [data-sub-model="close-preview"]')?.focus();
 }
 function send(){
  const initial=get();
  if(!enabled(initial)||!preview||!selected.size||IN_PROGRESS.has(record?.status)||record?.status==='succeeded')return;
  const expected=preview;
  const excerptIds=expected.excerpts.filter(item=>selected.has(item.excerptId)).map(item=>item.excerptId);
  return operate('send',async binding=>{
   if(!validPreview(expected,binding.run))return;
   const body={expectedInputRevision:expected.inputRevision,analysisDigest:expected.analysisDigest,previewDigest:expected.previewDigest,excerptIds,consent:true};
   const pending=modelPending(binding.run,START);
   if(pending&&JSON.stringify(pending.body)!==JSON.stringify(body)){
    message='原请求结果待核对。恢复时只能确认原请求中已选定的文字。';
    return;
   }
   const slot=pending?.slot??'model-recognition:start:'+binding.run.runId+':'+crypto.randomUUID();
   const response=await ctx.commands.run(slot,START,'POST',endpoint(binding.run),body,{signal:binding.signal,
    definitiveError:error=>error.status===503&&['MODEL_RECOGNITION_UNAVAILABLE','PRIMARY_RECOGNITION_UNAVAILABLE'].includes(error.code)});
   if(!currentView(binding))return;
   closePreview();
   receipt(binding,response.data);
   try{await readAttempt(binding)}catch(error){
    if(currentView(binding)&&!ctx.authError?.(error))message='暂时无法读取识别进度，可刷新进度核对。';
   }
  });
 }
 function cancel(){
  const attempt=record;
  return operate('cancel',async binding=>{
   if(!IN_PROGRESS.has(attempt?.status))return;
   const response=await ctx.commands.run('model-recognition:cancel:'+binding.run.runId+':'+attempt.attemptId,
    CANCEL,'POST',endpoint(binding.run)+'/cancel',{attemptId:attempt.attemptId},{signal:binding.signal});
   if(!currentView(binding))return;
   const state=response.data;
   if(state?.attemptId===attempt.attemptId&&state.status==='cancelled')record={...attempt,status:'cancelled'};
   await readAttempt(binding);
  });
 }
 function checkReceipt(){
  return operate('receipt',async binding=>{
   const pending=modelPending(binding.run,START)??modelPending(binding.run,CANCEL);
   if(!pending)return;
   const outcome=await ctx.commands.resolve(pending.slot);
   if(!currentView(binding))return;
   if(pending.action===START)receipt(binding,outcome.result?.data);
   await readAttempt(binding);
   if(!currentView(binding))return;
   if(modelPending(binding.run,pending.action))message=pending.action===START
    ?'暂未取得原请求回执。恢复前请重新核对原发送内容。':'暂未取得取消回执，请刷新本次识别进度。';
  });
 }
 function render(catalog){
  const binding=get();
  if(!binding?.run)return '';
  const capability=capabilityOf(catalog);
  const startPending=modelPending(binding.run,START);
  const pending=startPending??modelPending(binding.run,CANCEL);
  const canStart=enabled(binding);
  const active=IN_PROGRESS.has(record?.status);
  if(!record&&!pending&&!binding.canStart)return '';
  if(!record&&!pending&&!capability)return '';
  const canAdopt=canStart&&record?.status==='succeeded'&&record.inputRevision===binding.run.inputRevision&&record.analysisDigest===binding.run.analysisDigest;
  const prior=globalThis.document?.querySelector('.b-model');
  const expanded=prior?.dataset?.modelRun===binding.run.runId&&prior.open;
  return `<details class="b-model" data-model-run="${h(binding.run.runId)}"${expanded?' open':''}>
   <summary>可选 · DeepSeek 增强（确认后发送）${active?' · 识别中':record?.status==='succeeded'?' · 有建议':''}</summary>
   <div class="b-model-body">
   ${record?recordHtml(record,{...binding,editable:canAdopt,applyTarget:canAdopt?binding.applyTarget:undefined}):''}
   ${record?.status==='failed'&&Object.hasOwn(modelFailure,record.reasonCode?.replace('PRIMARY_RECOGNITION_','MODEL_RECOGNITION_'))?`<p class="hint">${h(modelFailure[record.reasonCode?.replace('PRIMARY_RECOGNITION_','MODEL_RECOGNITION_')])}</p>`:''}
   ${operation?.kind==='send'?'<p role="status">正在确认本次发送…</p>':''}
   ${message?`<p class="warning" role="status">${h(message)}</p>`:''}
   ${refreshNeeded?'<button type="button" data-sub-action="refresh">刷新提报</button>':''}
   ${pending?`<p class="hint">本次识别操作回执待核对。</p>${action('receipt','核对本次回执',!!operation)}`:''}
   ${active?`<div class="actions">${action('refresh','刷新识别进度',!!operation)}${action('cancel','取消本次识别',!!operation)}</div>`:''}
   ${!record&&!pending&&capability?.enabled===false?'<p class="hint">DeepSeek 识别暂不可用，可继续补充或人工核对。</p>':''}
   ${canStart&&!active&&!preview&&record?.status!=='succeeded'?
    action('preview',operation?.kind==='preview'?'正在读取预览…':startPending?'重新核对原文':'用 DeepSeek 识别',!!operation):''}
   ${preview&&canStart&&!active?previewHtml(preview,selected,binding.materials,!!operation,!!startPending):''}
   </div></details>`;
 }
 return {
  render,sync,reset,pause,owns:modelCommand,
  change(target){
   const id=target.dataset?.modelExcerpt;
   if(!preview||!id||operation||!preview.excerpts.some(item=>item.excerptId===id))return false;
   if(target.checked)selected.add(id);else selected.delete(id);
   updateSelection();
   return true;
  },
  async click(button){
   const binding=get();
   if(!binding?.run||operation)return;
   const kind=button.dataset.subModel;
   if(kind==='preview'&&enabled(binding)&&!IN_PROGRESS.has(record?.status)&&record?.status!=='succeeded')await open();
   if(kind==='close-preview'){closePreview();host.repaint();document.querySelector('[data-sub-model="preview"]')?.focus()}
   if(kind==='send')await send();
   if(kind==='refresh')await refreshAttempt();
   if(kind==='cancel')await cancel();
   if(kind==='receipt')await checkReceipt();
   if(kind==='adopt'&&enabled(binding)&&record?.status==='succeeded'&&record.inputRevision===binding.run.inputRevision&&
      record.analysisDigest===binding.run.analysisDigest){
    const group=record.suggestions?.groups?.[Number(button.dataset.modelGroup)];
    if(!group?.candidateId)return;
    const target=binding.applyTarget(group.candidateId);
    const row=groupRows(group,record.suggestions.evidence??[])[Number(button.dataset.modelRow)];
    if(target&&row&&canApplyRow(row))host.adopt(target,Object.fromEntries(row.fields.map(item=>[item.field,item.value])));
   }
  }
 };
}
