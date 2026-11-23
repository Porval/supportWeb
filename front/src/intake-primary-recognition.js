import {createModelRecognitionController,modelRecognitionRecordHtml} from './intake-model-recognition.js';
import {h,evidenceList} from './b-common.js';
export const PRIMARY_CONTRACT='activity-primary-recognition/1.0';
export const MODEL_INTAKE_CONTRACT='activity-model-intake/1.0';
export const agentRecognitionRecord=run=>run?.agentRecognition?.contractVersion===MODEL_INTAKE_CONTRACT&&
 run.agentRecognition.provider==='deepseek'?run.agentRecognition:null;
export function agentRecognitionBinding(run){
 const r=agentRecognitionRecord(run);
 if(r?.status!=='succeeded'||run.state!=='succeeded'||run.result?.modelUsed!==true||
    run.result.engine!=='deepseek_model_first_v1'||typeof r.promptVersion!=='string'||!r.promptVersion.trim()||
    run.result.promptVersion!==r.promptVersion||!run.runId||!Number.isInteger(run.inputRevision)||!run.analysisDigest)return null;
 return {runId:run.runId,inputRevision:run.inputRevision,analysisDigest:run.analysisDigest,promptVersion:r.promptVersion};
}
export function agentCandidateMetadata(run,candidateId){
 if(!agentRecognitionBinding(run))return null;
 const matches=(agentRecognitionRecord(run).candidateMetadata??[]).filter(item=>item.candidateId===candidateId);
 return matches.length===1?matches[0]:null;
}
export const ACTIVITY_METHODS=[['shen_jia','美团神价'],['bao_hao_jia','爆好价'],['category_threshold_subsidy_single','品类满减单方'],['category_threshold_subsidy_dual','品类满减双方'],['brand_threshold_subsidy_single','品牌满减单方'],['brand_threshold_subsidy_dual','品牌满减双方'],['brand_threshold_subsidy_triple','品牌满减三方'],['platform_red_packet','平台红包'],['other','其他']].map(([code,label])=>({code,label}));
const record=run=>run?.primaryRecognition?.contractVersion===PRIMARY_CONTRACT?run.primaryRecognition:null;
export function primaryGroupFor(run,candidateId){
 if(run?.agentRecognition)return null;
 const r=record(run);
 if(r?.status!=='succeeded'||!r.recognitionDigest)return null;
 const groups=r.recognition?.groups??[];
 if(groups.length===1&&!groups[0].candidateIds?.length&&run.result?.candidates?.length===1&&
    run.result.candidates[0].candidateId===candidateId&&!run.result.candidates[0].productRows?.length)
  return {...groups[0],candidateIds:[candidateId],candidateBindingMode:'single_activity_without_product_rows'};
 const matches=groups.filter(g=>g.candidateIds?.includes(candidateId));
 if(matches.length!==1)return null;
 const group=matches[0];
 if(group.candidateIds.some(id=>groups.filter(g=>g.candidateIds.includes(id)).length!==1))return null;
 return group;
}
export function primaryFields(run,candidateId,form={}){
 if(agentRecognitionBinding(run)){
  const candidate=run.result.candidates?.find(item=>item.candidateId===candidateId);
  if(!candidate)return null;
  const values=Object.fromEntries(Object.entries(candidate.fields??{}).map(([key,item])=>[key,item.value]));
  const single=run.result.candidates.length===1;
  const period=run.candidatePeriods?.[candidateId];
  for(const key of ['startAt','endAt']){
   // The original form dates can fill gaps only when the Agent returned a single activity.
   if(single&&!values[key]&&candidate.fields?.[key]?.status!=='conflict')values[key]=form?.[key]??null;
   if(!values[key]&&candidate.fields?.[key]?.status==='missing'&&
      ['year_inferred','source_date'].includes(period?.status))values[key]=key==='startAt'?period.startOn:period.endOn;
   if(/^\d{4}-\d{2}-\d{2}$/.test(values[key]??''))values[key]+=key==='startAt'?'T00:00:00+08:00':'T23:59:59.999+08:00';
  }
  const schedule=run.couponSchedule;
  if(schedule?.candidateId===candidateId&&schedule.startAt&&schedule.endAt){
   values.startAt=schedule.startAt;
   values.endAt=schedule.endAt;
  }
  const method=agentCandidateMetadata(run,candidateId)?.activityMethod;
  return {...values,activityMethod:method?.status==='explicit'&&ACTIVITY_METHODS.some(item=>item.code===method.value)?method.value:null};
 }
 if(run?.agentRecognition)return null;
 const group=primaryGroupFor(run,candidateId);
 if(!group)return null;
 const fields=Object.fromEntries(Object.entries(group.fields??{}).map(([key,item])=>[key,item.status==='explicit'?item.value:null]));
 for(const key of ['startAt','endAt'])if(/^\d{4}-\d{2}-\d{2}$/.test(fields[key]??''))fields[key]+=key==='startAt'?'T00:00:00+08:00':'T23:59:59.999+08:00';
 if(Array.isArray(fields.platformCode))fields.platformCode=fields.platformCode.length===1?fields.platformCode[0]:null;
 return {activityName:form.activityName||fields.activityName||null,type:form.type||fields.type||null,platformCode:fields.platformCode||null,
  sourceActivityId:fields.sourceActivityId||null,startAt:fields.startAt||null,endAt:fields.endAt||null,rulesText:fields.rulesText||null,
  activityMethod:fields.activityMethod||group.activityMethod||null};
}
export function primaryBinding(run,candidateId){
 const group=primaryGroupFor(run,candidateId),r=record(run);
 return group?{attemptId:r.attemptId,recognitionDigest:r.recognitionDigest,groupId:group.groupId}:null;
}
export function primaryCandidate(run,id){
 if(run?.agentRecognition)return run?.result?.candidates?.find(candidate=>candidate.candidateId===id);
 const original=run?.result?.candidates?.find(c=>c.candidateId===id),group=primaryGroupFor(run,id);
 if(!original||!group)return original;
 const selected=run.result.candidates.filter(c=>group.candidateIds.includes(c.candidateId));
 const rows=selected.flatMap(c=>c.productRows??[]);
 if(new Set(rows.map(r=>r.rowId)).size!==rows.length)return original;
 return {...original,productRows:rows};
}
const PRESENTATION_FIELDS=['activityName','type','platformCode','sourceActivityId','startAt','endAt','rulesText','activityMethod'];
const present=value=>typeof value==='string'?Boolean(value.trim()):Array.isArray(value)?value.length>0:value!=null;
const normalized=(key,value)=>['startAt','endAt'].includes(key)&&typeof value==='string'?value.slice(0,10):
 typeof value==='string'?value.trim():JSON.stringify(value);
const displayedValue=(key,value)=>['startAt','endAt'].includes(key)&&/^\d{4}-\d{2}-\d{2}$/.test(value??'')
 ?value+(key==='startAt'?'T00:00:00+08:00':'T23:59:59.999+08:00'):value;

// A read-only UI overlay. Original analysis, confirmation binding, permissions
// and persisted input are deliberately left to their existing owners.
export function primaryPresentation(run,candidateId,form={},edits={}){
 const original=run?.result?.candidates?.find(c=>c.candidateId===candidateId);
 if(agentRecognitionBinding(run)&&original){
  const fields=Object.fromEntries(Object.entries(original.fields??{}).map(([key,item])=>[key,{...item,evidenceIds:[...(item.evidenceIds??[])]}]));
  const single=run.result.candidates.length===1;
  if(single)for(const [key,value] of Object.entries(edits??{}))fields[key]={...fields[key],value,presentationSource:'manual'};
  const defaults=Object.fromEntries(Object.entries(fields).filter(([key,item])=>item.status==='explicit'&&
   !Object.hasOwn(form??{},key)&&!Object.hasOwn(edits??{},key)).map(([key,item])=>[key,item.value]));
  const method=agentCandidateMetadata(run,candidateId)?.activityMethod;
  return {candidate:{...original,fields},analysis:run.result,values:{...Object.fromEntries(Object.entries(fields).map(([key,item])=>[key,item.value])),activityMethod:method?.status==='explicit'&&ACTIVITY_METHODS.some(item=>item.code===method.value)?method.value:null},
   defaults,resolvedFields:[],sourceNotes:[],identityClear:false,projected:false};
 }
 const single=run?.result?.candidates?.length===1;
 const manual=single?form:{};
 const overrides=single?edits:{};
 const fields=Object.fromEntries(Object.entries(original?.fields??{}).map(([key,value])=>[key,{...value,evidenceIds:[...(value.evidenceIds??[])]}]));
 const sourceNotes=[],defaults={},resolvedFields=[];
 const values=()=>Object.fromEntries(Object.entries(fields).map(([key,item])=>[key,item.value]));
 const applyManual=()=>{
  for(const key of new Set([...Object.keys(manual),...Object.keys(overrides)])){
   const override=Object.hasOwn(overrides,key);
   if(!override&&!present(manual[key]))continue;
   const value=override?overrides[key]:manual[key];
   fields[key]={...fields[key],value,
    status:fields[key]?.status==='conflict'?'conflict':present(value)?'explicit':'missing',presentationSource:'manual'};
  }
 };
 const empty=()=>{applyManual();return {candidate:original?{...original,fields}:original,analysis:run?.result,
  values:values(),defaults,resolvedFields,sourceNotes,identityClear:false,projected:false}};
 if(run?.agentRecognition)return empty();
 const group=primaryGroupFor(run,candidateId),r=record(run);
 if(!original||!group)return empty();
 if(['inputRevision','inputDigest','analysisDigest','catalogRevision'].some(key=>run[key]!=null&&r[key]!==run[key]))return empty();
 const evidence=new Map((r.recognition?.evidence??[]).map(item=>[item.evidenceId,item]));
 const groupRefs=new Set(group.evidenceIds??[]),materials=new Set(group.sourceMaterialIds??[]);
 const issues=group.issues??[];
 const globalIssue=issues.some(item=>['SOURCE_EXCERPT_TRUNCATED','LOCAL_GROUP_AMBIGUOUS'].includes(item.code));
 const usable={};
 for(const key of PRESENTATION_FIELDS){
  const item=group.fields?.[key];
  const value=Array.isArray(item?.value)&&key==='platformCode'?(item.value.length===1?item.value[0]:null):item?.value;
  const blocked=issues.some(issue=>issue.field===key||issue.code==='DATE_RANGE_CONFLICT'&&['startAt','endAt'].includes(key));
  const refs=item?.evidenceIds??[];
  if(globalIssue||blocked||item?.status!=='explicit'||typeof value!=='string'||!present(value)||!refs.length)continue;
  if(!refs.every(id=>{const ref=evidence.get(id);return groupRefs.has(id)&&ref&&materials.has(ref.materialId)&&
    typeof ref.quote==='string'&&ref.quote.trim()&&/^[a-f0-9]{64}$/.test(ref.sourceSha256??'')&&ref.locator?.kind}))continue;
  if(key==='activityMethod'&&!ACTIVITY_METHODS.some(method=>method.code===value))continue;
  usable[key]={...item,value:displayedValue(key,value)};
 }
 const scopedConflicts=(run.result.conflicts??[]).filter(item=>single||item.candidateId===candidateId||
  item.evidenceIds?.some(id=>original.fields?.[item.field]?.evidenceIds?.includes(id)));
 const conflictFields=new Set();
 for(const key of PRESENTATION_FIELDS){
  const current=original.fields?.[key],primary=group.fields?.[key];
  const observed=scopedConflicts.filter(item=>item.field===key);
  const manualValue=Object.hasOwn(overrides,key)?overrides[key]:manual[key];
  const candidates=[current?.value,usable[key]?.value,manualValue,...observed.flatMap(item=>[item.manualValue,...(item.extractedValues??[])])].filter(present);
  if(current?.status==='conflict'||primary?.status==='conflict'||observed.length||
     new Set(candidates.map(value=>normalized(key,value))).size>1)conflictFields.add(key);
 }
 for(const key of ['platformCode','sourceActivityId']){
  if(usable[key]&&present(group.identity?.[key])&&normalized(key,group.identity[key])!==normalized(key,usable[key].value))conflictFields.add(key);
 }
 const identityClear=Boolean(usable.sourceActivityId&&usable.platformCode&&['sourceActivityId','platformCode'].every(key=>
  !conflictFields.has(key)&&(!Object.hasOwn(overrides,key)||present(overrides[key]))));
 if(identityClear&&conflictFields.has('activityName')){
  conflictFields.delete('activityName');
  const names=[overrides.activityName,manual.activityName,original.fields?.activityName?.value,usable.activityName?.value,
   ...scopedConflicts.filter(item=>item.field==='activityName').flatMap(item=>[item.manualValue,...(item.extractedValues??[])])].filter(present);
  sourceNotes.push({field:'activityName',text:'主活动编号一致，名称差异保留为来源提示。',values:[...new Set(names)],
   evidenceIds:[...(group.fields?.activityName?.evidenceIds??[])]});
  if(fields.activityName?.status==='conflict')fields.activityName={...fields.activityName,status:present(fields.activityName.value)?'explicit':'missing'};
 }
 for(const [key,item] of Object.entries(usable)){
  if(conflictFields.has(key))continue;
  if(!present(manual[key])&&!Object.hasOwn(overrides,key)){
   fields[key]={...item,presentationSource:'primary_recognition'};
   defaults[key]=item.value;
  }
  resolvedFields.push(key);
 }
 for(const key of conflictFields)fields[key]={...fields[key],status:'conflict',
  evidenceIds:[...new Set([...(fields[key]?.evidenceIds??[]),...(group.fields?.[key]?.evidenceIds??[])])]};
 applyManual();
 const refs=new Set(Object.values(fields).flatMap(item=>item.evidenceIds??[]));
 const analysis={...run.result,evidence:[...(run.result.evidence??[]),...[...evidence.values()].filter(item=>refs.has(item.evidenceId))],
  conflicts:(run.result.conflicts??[]).filter(item=>!(identityClear&&item.field==='activityName'&&scopedConflicts.includes(item)))};
 return {candidate:{...original,fields},analysis,values:values(),defaults,resolvedFields,sourceNotes,
  identityClear,projected:resolvedFields.length>0,conflictFields:[...conflictFields]};
}
function converted(run){
 const r=record(run);if(!r)return run;
 const groups=r.recognition?.groups??[];
 return {...run,modelRecognition:{...r,contractVersion:'activity-model-recognition/1.0',suggestions:r.recognition?{
  groups:groups.map(g=>({candidateId:g.candidateId,fields:Object.entries(g.fields??{}).filter(([,v])=>typeof v.value==='string').map(([field,v])=>({field,value:v.value,confidence:v.status==='explicit'?'supported':'needs_review',evidenceIds:v.evidenceIds}))})),
  evidence:r.recognition.evidence}:undefined}};
}
export function localFailureTitle(run){
 if(run?.state!=='failed')return '';
 const code=run.error?.code;
 if(['LOCAL_MATERIAL_READ_FAILED','LOCAL_MATERIAL_OUTPUT_LIMIT'].includes(code)||
    (code==='AGENT_MODEL_MODEL_INPUT_DECODE_INCOMPLETE'&&
     (run.error.details?.stage==='planning'||run.materialResults?.some(doc=>doc.status!=='parsed'))))
  return '服务端读取附件失败';
 if(run.error?.details?.stage==='local_planning')return '服务端整理提报材料失败';
 return '';
}
export function primaryRecordHtml(run,input,materials){
 const local=localFailureTitle(run);
 if(local)return `<section class="b-panel"><h3>服务端提报记录</h3><p>${h(local)}，材料尚未发送给 Agent。</p></section>`;
 const agent=agentRecognitionRecord(run);
 if(agent){
  const labels={queued:'Agent 识别已排队',running:'Agent 正在识别',succeeded:agentRecognitionBinding(run)?'Agent 识别完成':'Agent 结果待核实',failed:'Agent 识别失败',cancelled:'Agent 识别已取消',interrupted:'Agent 识别已中断'};
  return `<section class="b-panel"><h3>Agent 识别记录 · DeepSeek</h3><p>${h(labels[agent.status]??'Agent 状态待核实')}</p>
   <p class="hint">模型：${h(agent.model??'待核实')} · 提示版本：${h(agent.promptVersion??'待核实')}</p>
   ${agent.coverage?`<p class="hint">源数据行 ${h(agent.coverage.sourceRows??'待核实')} · 已分类行 ${h(agent.coverage.classifiedRows??'待核实')} · 商品来源行 ${h(agent.coverage.productSourceRows??'待核实')}</p>`:''}</section>`;
 }
 return modelRecognitionRecordHtml(converted(run),input,materials).replaceAll('DeepSeek 建议','DeepSeek 主识别结果');
}
export function createPrimaryRecognitionController(ctx,host){
 let current=null,openedGroup=null;
 const controller=createModelRecognitionController(ctx,host,{
  recordFor:run=>converted(run)?.modelRecognition,
  endpoint:run=>'/analysis-runs/'+encodeURIComponent(run.runId)+'/primary-recognition',
  startAction:'analysis.primary_recognition.start',cancelAction:'analysis.primary_recognition.cancel',
  capabilityOf:catalog=>catalog?.primaryRecognition?.contractVersion===PRIMARY_CONTRACT?catalog.primaryRecognition:null,
  onRead:next=>{current=next;host.receivePrimary?.(next)}
 });
 const render=controller.render,sync=controller.sync;
 return {...controller,
  sync(run){current=run;sync(run)},
  reset(){openedGroup=null;current=null;controller.reset()},
  async openGroup(candidateId){
   const binding=host.context();
   if(binding?.catalog?.agentFirstRecognition)return;
   if(!binding?.run?.result?.candidates?.some(candidate=>candidate.candidateId===candidateId))return;
   openedGroup={runId:binding.run.runId,candidateId};
   const status=record(binding.run)?.status;
   if(!['queued','running','succeeded'].includes(status))await controller.click({dataset:{subModel:'preview'}});
   else host.repaint();
   const panel=document.querySelector('.b-model');
   if(panel){panel.open=true;panel.querySelector('summary')?.focus();panel.scrollIntoView?.({block:'start',behavior:'smooth'})}
  },
  render(catalog){
   if(catalog?.agentFirstRecognition)return '';
   const run=host.context()?.run??current,r=record(run),groups=r?.recognition?.groups??[];
   let html=render(catalog).replace('可选 · DeepSeek 增强（确认后发送）','活动信息识别 · DeepSeek').replaceAll('DeepSeek 建议已返回','活动信息已识别，请核对后建档').replaceAll('可继续使用原提报结果。','请补充材料后重新识别。');
   if(openedGroup&&run&&openedGroup.runId===run.runId){
    html=html.replace(/(<details class="b-model"[^>]*)(>)/,(whole,start,end)=>start.includes(' open')?whole:start+' open'+end);
    const selected=run.result?.candidates?.find(candidate=>candidate.candidateId===openedGroup.candidateId);
    const label=selected?.fields?.activityName?.value||openedGroup.candidateId;
    html=html.replace('<div class="b-model-body">',`<div class="b-model-body"><p class="b-note">从分组「${h(label)}」打开。以下是当前提报的原文预览，请勾选与本次识别有关的片段，确认后才发送。</p>`);
   }
   if(!groups.length)return html;
   const summaries=groups.map(group=>`<article class="b-candidate"><strong>${h(group.fields?.activityName?.value??'活动名称待核对')}</strong>
    <p>活动方式：${h(ACTIVITY_METHODS.find(m=>m.code===group.activityMethod)?.label??'待核对')} · ${h(group.status==='recognized'?'识别完成':'需要核对')}</p>
    ${group.brands?.length?`<p>材料品牌：${group.brands.map(b=>h(b.value)).join('、')}（待关联公司品牌）</p>`:''}
    ${group.couponTiers?.length?`<p>券档：${group.couponTiers.map(b=>h(b.value)).join('；')}</p>`:''}
    ${group.issues?.length?`<p class="hint">待核对：${group.issues.map(i=>h(i.code)).join('、')}</p>`:''}
    ${!group.candidateIds?.length||group.candidateIds.some(id=>!primaryGroupFor(run,id))?'<p class="hint">商品分组归属尚未确认，请按独立主活动补充材料；不会把商品复制到多个活动。</p>':''}
    <details><summary>查看识别依据</summary>${evidenceList((r.recognition.evidence??[]).filter(e=>group.evidenceIds.includes(e.evidenceId)),host.context()?.materials??[])}</details></article>`).join('');
   return html+`<section class="b-panel"><h3>主识别分组</h3>${summaries}</section>`;
  }
 };
}
