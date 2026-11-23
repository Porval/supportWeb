// Per-actor recovery intents. Never stores password, SID or CSRF.
export class PendingCommandError extends Error{constructor(record,cause){super('提交结果尚未确认，请先核对；重试会沿用原请求。');this.name='PendingCommandError';this.record=record;this.cause=cause;this.code='COMMAND_UNKNOWN'}}
export async function digestBody(value){const raw=new TextEncoder().encode(JSON.stringify(value));return Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256',raw)),b=>b.toString(16).padStart(2,'0')).join('')}
export function createCommandClient(adapter,{storage=globalThis.sessionStorage,namespace='jt021'}={}){
 const PREFIX=namespace+'.command.';
 const intakePrefix=namespace+'.intake.';
 let actor=null;const records=new Map(),busy=new Set();
 const storageKey=slot=>PREFIX+actor+'.'+encodeURIComponent(slot);
 function persist(slot,record){
  if(actor!==record.actor){delete record.body;delete record.response;return}
  const body=record.body;
  const hasAttachment=body?.contentBase64||body?.listFile?.contentBase64||body?.screenshot?.contentBase64;
  try{storage?.setItem(storageKey(slot),JSON.stringify({...record,body:hasAttachment?undefined:body,response:undefined}))}
  catch{record.memoryOnly=true}
 }
 function get(slot){if(records.has(slot))return records.get(slot);try{const r=JSON.parse(storage?.getItem(storageKey(slot))??'null');if(r&&r.actor===actor){records.set(slot,r);return r}}catch{}return null}
 async function inspect(record,{retireMissing=false}={}){const env=await adapter.request('/commands/'+encodeURIComponent(record.key)+'?action='+encodeURIComponent(record.action));const value=env.data;
  if(['committed','accepted'].includes(value.state)&&value.response!=null){record.state='confirmed';record.commandId=value.commandId;record.response=value.response;persist(record.slot,record);return value.response?.data!==undefined?value.response:{...env,data:value.response,commandId:value.commandId}}
  if(retireMissing&&record.state==='unknown'&&value.state==='not_found'){
   record.state='obsolete';
   persist(record.slot,record);
  }
  return null;
 }
 return {
  bind(id){if(actor!==id){records.clear();busy.clear();actor=id}},
  clear(){try{for(let i=0;i<(storage?.length??0);i++){const key=storage.key(i);if(!key?.startsWith(PREFIX+actor+'.'))continue;const r=JSON.parse(storage.getItem(key));delete r.body;delete r.response;storage.setItem(key,JSON.stringify(r))}}catch{}records.clear();busy.clear();actor=null},
  pending(){const result=[...records.values()].filter(r=>r.actor===actor&&r.state==='unknown');try{for(let i=0;i<(storage?.length??0);i++){const key=storage.key(i);if(key?.startsWith(PREFIX+actor+'.')){const r=JSON.parse(storage.getItem(key));if(r.state==='unknown'&&!result.some(x=>x.slot===r.slot))result.push(get(r.slot))}}}catch{}return result},
  rememberIntake(slot,patch){if(!actor)return;const key=intakePrefix+actor+'.'+encodeURIComponent(slot);try{const old=JSON.parse(storage?.getItem(key)??'null')??{};storage?.setItem(key,JSON.stringify({...old,actor,slot,...patch}))}catch{}},
  intakes(){const result=[];try{for(let i=0;i<(storage?.length??0);i++){const key=storage.key(i);if(key?.startsWith(intakePrefix+actor+'.')){const value=JSON.parse(storage.getItem(key));if(value.actor===actor)result.push(value)}}}catch{}return result},
  get(slot){return get(slot)},
  async retry(slot){const r=get(slot);if(!r?.body)throw new Error('原请求正文已清理；请恢复原输入（附件需重新选择同一文件）后，沿用原键提交。');return this.run(slot,r.action,r.method,r.path,r.body)},
  async resolve(slot,options){const r=get(slot);if(!r)throw new Error('没有待核对请求。');return {record:r,result:await inspect(r,options)}},
  async run(slot,action,method,path,body,{signal,definitiveError}={}){
   if(!actor)throw new Error('请先核对登录身份。');if(busy.has(slot))throw new Error('该操作正在提交，请等待。');const digest=await digestBody(body);let record=get(slot);
   if(record&&record.digest!==digest&&record.state==='unknown')throw new PendingCommandError(record,new Error('原请求结果未知，输入已保留。'));
   if(!record||record.digest!==digest||record.path!==path||record.method!==method){const recovery=action==='platform_materials.submit'?{expectedActivityRevision:body.expectedActivityRevision,expectedApprovedVersionId:body.expectedApprovedVersionId,expectedPlatformRevision:body.expectedPlatformRevision}:undefined;record={slot,actor,key:crypto.randomUUID(),action,method,path,digest,body:structuredClone(body),recovery,state:'prepared'};records.set(slot,record);persist(slot,record)}
   if(record.digest!==digest)throw new Error('请恢复原请求正文后重试。');record.body=structuredClone(body);busy.add(slot);
   try{const env=await adapter.request(path,{account:path.startsWith('/users/'),method,body,commandKey:record.key,signal});if(actor!==record.actor)throw new DOMException('身份已变化','AbortError');record.state='confirmed';record.commandId=env.data?.commandId??env.commandId??null;record.response=env;persist(slot,record);return env}
   catch(error){if(actor!==record.actor){delete record.body;delete record.response;throw new DOMException('身份已变化','AbortError')}const uncertain=!definitiveError?.(error)&&(!error.status||error.status>=500||error.code==='TIMEOUT'||error.name==='AbortError');record.state=uncertain?'unknown':'rejected';persist(slot,record);
    if(uncertain){try{const result=await inspect(record);if(result)return result}catch{}throw new PendingCommandError(record,error)}throw error;
   }finally{busy.delete(slot)}
  }
 }
}
