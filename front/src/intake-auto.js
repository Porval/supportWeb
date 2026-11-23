import {localIntakeAgentVersion} from './b-group-review.js';

const terminal = new Set(['succeeded', 'partial']);
const completed = new Set(['created', 'linked', 'already_resolved']);

export const autoResolutionEnabled = capability =>
 capability?.enabled === true &&
 capability.contractVersion === 'auto-resolution/1.0' &&
 capability.ruleVersion === 'single-activity-auto/1.0';

export const groupedAutoResolutionEnabled = capability =>
 capability?.enabled === true &&
 capability.contractVersion === 'auto-resolution/1.0' &&
 capability.ruleVersion === 'grouped-intake-auto/1.0';

export function trackedIntake(commands, detail, run) {
 if (!run || run.inputRevision !== detail.revision) return null;
 return commands.intakes().find(flow =>
  flow.phase === 'analyzing' && flow.submissionId === detail.submissionId &&
  flow.revision === detail.revision && flow.runId === run.runId) ?? null;
}

export async function resolveIntakeAutomatically({commands, detail, run, capability, groupReview={}, signal, onStart, retry=false}) {
 const flow = trackedIntake(commands, detail, run);
 if (!flow || !terminal.has(run.state) || signal?.aborted) return null;
 if (!groupedAutoResolutionEnabled(capability)&&flow.autoResult) return {result:flow.autoResult, reread:false};
 if (typeof run.analysisDigest !== 'string' || !/^sha256:[0-9a-f]{64}$/.test(run.analysisDigest)) {
  throw new Error('分析摘要不可用，请重新读取本次分析。');
 }
 if(groupedAutoResolutionEnabled(capability)){
  if(run.result?.engineVersion!==localIntakeAgentVersion(groupReview))return null;
  const latest=detail.runs?.findLast(item=>item.inputRevision===detail.revision);
  if(latest&&latest.runId!==run.runId)return null;
  return resolveGroups({commands,detail,run,flow,signal,onStart,retry});
 }
 if ((detail.resolutions ?? []).some(item => item.revision === detail.revision)) return null;
 const slot = 'auto:' + detail.submissionId + ':' + detail.revision + ':' + run.runId;
 const prior = commands.get(slot);
 if (prior?.state === 'rejected') return {state:'rejected'};
 if (!prior && !autoResolutionEnabled(capability)) return {state:'unavailable'};
 onStart();
 const body = {expectedSubmissionRevision:detail.revision, analysisRunId:run.runId, analysisDigest:run.analysisDigest};
 const path = '/submissions/' + encodeURIComponent(detail.submissionId) + '/auto-resolve';
 // A reload only checks the original receipt. Resending is an explicit user action.
 let env = prior ? prior.response ?? (await commands.resolve(slot)).result : null;
 if (signal?.aborted) return null;
 if (!env && (!prior || retry && prior.state !== 'confirmed' && autoResolutionEnabled(capability))) {
  env = await commands.run(slot, 'submissions.auto_resolve', 'POST', path, body, {signal});
 }
 if (signal?.aborted) return null;
 if (!env) return {state:'unknown'};
 const value = env.data;
 const success = completed.has(value?.outcome);
 if (value?.submissionId !== detail.submissionId || value.inputRevision !== detail.revision ||
     (!success && value.outcome !== 'needs_confirmation') ||
     (success && (!value.activityId || !value.versionId)) ||
     (!success && (value.activityId !== null || value.versionId !== null))) {
  throw new Error('自动处理回执和当前输入不一致，请核对原请求。');
 }
 const result = {
  outcome:value.outcome, activityId:value.activityId, versionId:value.versionId,
  resolutionMode:value.resolutionMode, productApplicability:value.productApplicability, reasonCodes:Array.isArray(value.reasonCodes)?value.reasonCodes:[]
 };
 commands.rememberIntake(flow.slot, {autoResult:result});
 return {result, reread:success};
}

async function resolveGroups({commands,detail,run,flow,signal,onStart,retry}){
 const candidates=run.result?.candidates??[];
 const resolved=new Set((detail.resolutions??[]).filter(item=>item.revision===detail.revision).map(item=>item.candidateId));
 const groups=new Map((flow.autoGroups??[]).map(item=>[item.candidateId,item]));
 const path='/submissions/'+encodeURIComponent(detail.submissionId)+'/auto-resolve';
 const prefix='auto:'+detail.submissionId+':'+detail.revision+':'+run.runId+':';
 let started=false,reread=false;
 const outcome=state=>({state,groups:[...groups.values()],result:candidates.length===1?groups.get(candidates[0].candidateId):undefined,reread});
 for(const {candidateId} of candidates){
  if(signal?.aborted)return null;
  if(resolved.has(candidateId)||groups.has(candidateId))continue;
  const slot=prefix+encodeURIComponent(candidateId);
  const body={expectedSubmissionRevision:detail.revision,analysisRunId:run.runId,analysisDigest:run.analysisDigest,candidateId};
  const prior=commands.get(slot);
  if(!started){onStart();started=true}
  let value;
  try{
   let env=prior&&prior.state!=='rejected'?prior.response??(await commands.resolve(slot)).result:null;
   if(signal?.aborted)return null;
   if(!env&&(!prior||retry&&prior.state==='unknown'))env=await commands.run(slot,'submissions.auto_resolve','POST',path,body,{signal});
   if(signal?.aborted)return null;
   if(!env&&prior?.state!=='rejected')return outcome('unknown');
   value=env?.data;
  }catch(error){
   if(signal?.aborted)return null;
   if(error.status===401||error.code==='ANALYSIS_STALE')throw error;
   if(error.code==='COMMAND_UNKNOWN'||!error.status||error.status>=500)return outcome('unknown');
  }
  if(!value){
   groups.set(candidateId,{candidateId,state:'rejected'});
  }else{
   const success=completed.has(value.outcome);
   if(value.submissionId!==detail.submissionId||value.inputRevision!==detail.revision||value.candidateId!==candidateId||
      (success?!(value.activityId&&value.versionId):value.outcome!=='needs_confirmation'||value.activityId!==null||value.versionId!==null))
    throw new Error('本分组处理回执和当前输入不一致，请刷新结果。');
   groups.set(candidateId,{candidateId,outcome:value.outcome,activityId:value.activityId,versionId:value.versionId,
    reasonCodes:Array.isArray(value.reasonCodes)?value.reasonCodes:[],
    pendingItems:Array.isArray(value.pendingItems)?value.pendingItems:undefined,
    reviewState:value.reviewState,reviewBlockedReason:value.reviewBlockedReason,productApplicability:value.productApplicability});
   reread||=success;
  }
  commands.rememberIntake(flow.slot,{autoGroups:[...groups.values()]});
 }
 return outcome(undefined);
}
