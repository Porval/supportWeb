import {normalizeCommentCapability,normalizeCommentRecord,COMMENT_IDENTITY_VERSION} from './planning-model.js';
import {mountPlanning} from './planning-mount.js';
import {createScheduleAdapter,encodeImages,ScheduleError} from './schedule-adapter.js';

const BOARD='/boards/shared',safeId=id=>{if(!/^[A-Za-z0-9_-]+$/.test(id))throw new Error('排期对象标识无效');return id};
const actions={create:'schedule.requirement.create',update:'schedule.requirement.update',move:'schedule.requirement.move',comment:'schedule.comment.create',deleteComment:'schedule.comment.delete',reset:'schedule.reset'};
export function normalizeBoard(envelope,details=new Map()) {
  const b=envelope.data,m=envelope.meta;
  if(b?.boardId!=='shared'||b.complete!==true||b.metricPolicy?.dueStatusScope!=='open_only'||!Number.isInteger(b.generation)||b.generation!==m.generation||b.boardRevision!==m.boardRevision)throw new Error('共享排期快照或统计口径不匹配');
  const requirements=[],ids=new Set();
  for(const status of ['review','doing','done']){
    const col=b.columns?.[status];if(!Array.isArray(col?.items)||col.total!==col.items.length)throw new Error('共享排期列表不完整');
    for(const row of col.items){if(ids.has(row.id)||row.status!==status)throw new Error('共享排期对象重复或状态不符');ids.add(row.id);
      const detail=details.get(row.id);
      const fresh=detailMatchesBoard(detail,b,row);
      // Summary fields always belong to this board transaction; details may add
      // note/comments only when their whole snapshot is exactly aligned.
      requirements.push(fresh?{...detail.requirement,...row,comments:detail.comments}:row);
    }
  }
  const stats=b.stats;
  if(!stats||![stats.completedThisWeek,stats.dueSoonOrOverdue,stats.p0Doing,stats.doneTotal,...Object.values(b.orderRevisions??{})].every(n=>Number.isInteger(n)&&n>=0)||!['review','doing','done'].every(s=>Number.isInteger(b.orderRevisions?.[s])))throw new Error('排期统计或顺序版本缺失');
  if(stats.doing.total!==requirements.length||stats.doing.count!==b.columns.doing.total||stats.doneTotal!==b.columns.done.total||!Array.isArray(b.completedThisWeekItems)||stats.completedThisWeek!==b.completedThisWeekItems.length)throw new Error('共享排期统计与同版列表不一致');
  for(const key of ['overdue','dueNext7Days','reviewQueue'])if(b.reminders?.[key]?.state!=='ready'||!Array.isArray(b.reminders[key].items))throw new Error('排期提醒不可获取');
  if(stats.dueSoonOrOverdue!==b.reminders.overdue.items.length+b.reminders.dueNext7Days.items.length)throw new Error('排期到期统计不一致');
  return {requirements,complete:true,loadState:'ready',today:b.today,duePolicy:'exclude_done',demo:b.containsExamples,
    generation:b.generation,snapshotToken:`${b.generation}:${b.boardRevision}`,doingItems:b.doingItems,
    statistics:{total:stats.doing.total,counts:{review:b.columns.review.total,doing:b.columns.doing.total,done:b.columns.done.total},completed:b.completedThisWeekItems,
      week:{start:b.weekStart,end:b.weekEnd},p0Doing:stats.p0Doing,dueCount:stats.dueSoonOrOverdue,
      overdue:b.reminders.overdue.items,upcoming:b.reminders.dueNext7Days.items,dueExplanation:'未来7天（含今天）及逾期；仅未完成需求，不含已上线'}};
}


export function detailMatchesBoard(detail,board,row) {
  return !!detail && detail.generation===board.generation && detail.boardRevision===board.boardRevision
    && detail.requirement?.id===row.id && detail.requirement?.revision===row.revision
    && Array.isArray(detail.comments) && detail.comments.length===row.commentCount;
}
export async function readMatchingDetail(id,{requestDetail,refreshBoard,getBoard,isCurrent=()=>true},attemptLimit=3) {
  for(let attempt=0;attempt<attemptLimit;attempt++) {
    const envelope=await requestDetail(id);
    if(!isCurrent())throw new DOMException('Aborted','AbortError');
    let board=getBoard().data;
    if(envelope.data.generation!==board.generation || envelope.data.boardRevision!==board.boardRevision) {
      await refreshBoard();
      if(!isCurrent())throw new DOMException('Aborted','AbortError');
      board=getBoard().data;
    }
    const row=Object.values(board.columns).flatMap(c=>c.items).find(r=>r.id===id);
    if(!row)throw new Error('需求已不存在或排期已重置，请重新读取。');
    if(detailMatchesBoard(envelope.data,board,row))return envelope;
  }
  throw new Error('共享排期持续变化，未使用陈旧详情，请稍后重新打开。');
}

export const commentCreateBody=(generation,payload,images)=>({expectedGeneration:generation,text:payload.text,images});

export function createScheduleController(ctx) {
  let ui=null,board=null,preview=null,signal=null,epoch=0,timer=null,command=null,editingBaseline=null,authorizationSuspended=false,contextSequence=0;
  let commentCapability=normalizeCommentCapability(null,null);
  const details=new Map(),images=new Map();
  function revokeImages(){for(const url of images.values())URL.revokeObjectURL(url);images.clear()}
  function access(){const s=ctx.session();return s?.authenticated&&s.permissions?.includes('accounts.manage')}
  function denied(error){clear();ctx.scheduleDenied(error)}
  function authorizationUnavailable(){
    authorizationSuspended=true;epoch++;adapter.clear();revokeImages();
    // Same authenticated owner's draft stays in controller memory only. All
    // protected cards, comments, dialogs/images are removed by the error render.
    ui?.setSnapshot({...identity(),loadState:'error',complete:false,permissionUnavailable:true});
  }
  const adapter=createScheduleAdapter({session:ctx.session,authorize:ctx.recheckScheduleSession,onDenied:denied,onAuthorizationUnavailable:authorizationUnavailable});
  const identity=()=>({access:'authorized',principalKey:ctx.session()?.user.id});
  function currentSnapshot(){return {...normalizeBoard(board,details),...identity(),commentIdentity:commentCapability,permissionUnavailable:authorizationSuspended,...(authorizationSuspended?{loadState:'error',complete:false}:{})}}
  function display(){if(ui&&board)ui.setSnapshot(currentSnapshot())}
  async function readContext(){
    const generation=epoch,sequence=++contextSequence,principal=ctx.session()?.user.id;
    const incoming=(await adapter.request('/context',{signal})).data;
    if(generation!==epoch||sequence!==contextSequence||principal!==ctx.session()?.user.id)return null;
    if(incoming.contractVersion!=='schedule/1.0'||incoming.timezone!=='Asia/Shanghai')throw new Error('排期接口版本不匹配');
    let next;
    try{next=normalizeCommentCapability(incoming,principal)}
    catch(error){next={negotiated:incoming.commentIdentity?.contractVersion===COMMENT_IDENTITY_VERSION,enabled:false,currentAuthor:null,reason:error.message}}
    const changed=next.negotiated!==commentCapability.negotiated||next.enabled!==commentCapability.enabled;
    const authorChanged=JSON.stringify(next.currentAuthor)!==JSON.stringify(commentCapability.currentAuthor)||next.reason!==commentCapability.reason;
    if(changed){details.clear();revokeImages()}
    commentCapability=next;
    return {capabilityChanged:changed,authorChanged};
  }
  async function readBoard(){
    const generation=epoch,result=await adapter.request(BOARD,{signal});if(generation!==epoch)return null;
    let invalidated=false;
    for(const [id,detail] of details){const row=Object.values(result.data.columns).flatMap(c=>c.items).find(r=>r.id===id);if(!row||!detailMatchesBoard(detail,result.data,row)){details.delete(id);invalidated=true}}
    if(invalidated||board?.data.generation!==result.data.generation)revokeImages();
    board=result;normalizeBoard(board,details);authorizationSuspended=false;return currentSnapshot();
  }
  async function readDetail(id){
    const generation=epoch;
    details.delete(id);
    const result=await readMatchingDetail(id,{
      requestDetail:()=>adapter.request(BOARD+'/requirements/'+safeId(id),{signal}),
      refreshBoard:readBoard,getBoard:()=>board,isCurrent:()=>generation===epoch
    });
    if(generation!==epoch)return;
    const detail={...result.data,comments:result.data.comments.map(c=>normalizeCommentRecord(c,{identityV2:commentCapability.negotiated}))};
    details.set(id,detail);revokeImages();
    // Image failure is separate from persisted comment success, never fake a URL.
    for(const comment of detail.comments){for(const image of comment.images){
      const path=BOARD+'/images/'+safeId(image.imageId);
      if(image.mediaType!=='image/jpeg'||image.url!=='/api/activity/v1/schedule'+path)throw new Error('评论图片来源不符合受限路径');
      try{
        const loaded=await adapter.request(path,{signal,binary:true});
        const digest=Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256',loaded.bytes)),b=>b.toString(16).padStart(2,'0')).join('');
        if(generation!==epoch)return;
        if(digest!==image.sha256||loaded.bytes.byteLength!==image.byteSize)throw new Error('评论图片摘要不匹配');
        images.set(image.imageId,URL.createObjectURL(new Blob([loaded.bytes],{type:'image/jpeg'})));
      }catch(error){if([401,403].includes(error.status)||generation!==epoch)throw error;/* original comment remains; view says 图片暂不可读 */}
    }}
    if(generation===epoch){const row=Object.values(board.data.columns).flatMap(c=>c.items).find(r=>r.id===id);if(!row||!detailMatchesBoard(result.data,board.data,row)){details.delete(id);revokeImages();throw new Error('读取图片期间排期已变化，请重新打开。')}display()}
  }
  async function refreshed({refreshIdentity=true}={}){
    const id=ui?.getState().editId;
    if(refreshIdentity)await readContext();await readBoard();if(id&&board&&Object.values(board.data.columns).some(col=>col.items.some(r=>r.id===id)))await readDetail(id);
    return board?currentSnapshot():null;
  }
  async function receiptOutcome(original){
    const envelope=await adapter.request('/commands/'+safeId(original.key)+'?action='+encodeURIComponent(original.action),{signal});
    if(envelope.data.state!=='committed')return {status:'unknown',receipt:original,retryable:true,message:'服务暂无原回执，仍可能在途。未重发原操作，请稍后再次核对。'};
    const committedGeneration=envelope.data.response?.generation;
    const snapshot=await refreshed();
    if(!snapshot)return {status:'unknown',receipt:original};
    command=null;
    return {status:'saved',snapshot,message:committedGeneration!==board.data.generation?'原请求曾成功，数据随后已重置；当前展示最新共享排期。':undefined};
  }
  async function perform(method,path,body,action){
    if(command)return {status:'unknown',receipt:command,message:'请先核对前一次操作结果。'};
    const original={method,path,body,action,key:crypto.randomUUID()};command=original;
    let writeConfirmed=false;
    try{
      const env=await adapter.request(path,{method,body,key:original.key,signal});writeConfirmed=true;
      const snapshot=await refreshed();if(!snapshot)return {status:'unknown',receipt:original};command=null;
      return {status:'saved',snapshot,message:env.data.generation!==board.data.generation?'原请求曾成功，数据随后已重置；已重新读取最新排期。':undefined};
    }catch(error){
      if([401,403].includes(error.status))return {status:error.status===401?'unauthenticated':'forbidden'};
      if(error.code==='AUTHORIZATION_UNAVAILABLE'){if(!writeConfirmed){command=null;return {status:'error',message:error.message}}return {status:'unknown',receipt:original,message:'操作已受理，最新权限暂不能核对；内容已隐藏，请恢复后核对原请求。'}}
      if(error.status===409){command=null;let snapshot;try{snapshot=await refreshed();}catch{}return {status:'conflict',snapshot,message:error.message+'。输入已保留，请核对最新内容再提交。'}}
      if(!writeConfirmed&&['COMMENT_IDENTITY_MIGRATION_REQUIRED','COMMENT_IDENTITY_UNAVAILABLE','COMMENT_IDENTITY_INVALID'].includes(error.code)){
        command=null;commentCapability={...commentCapability,enabled:false,reason:error.message+'；未提交草稿已保留。'};display();return {status:'error',message:error.message};
      }
      if(error.status>=400&&error.status<500){command=null;return {status:'error',message:error.message}}
      try{return await receiptOutcome(original)}catch{return {status:'unknown',receipt:original,message:'尚不能确认本次操作，请核对原请求结果；未自动重发。'}}
    }
  }
  function guardRecord(id){const r=board&&Object.values(board.data.columns).flatMap(c=>c.items).find(r=>r.id===id);if(!r)throw new Error('需求已变化，请重新读取');return r}
  const fields=d=>({...d,title:d.title.trim(),owner:d.owner?.trim()||null,start:d.start||null,due:d.due||null,note:d.note||'',progress:d.status==='done'?100:d.progress});
  async function start(route,params,abortSignal){
    pause();signal=abortSignal;const generation=epoch;
    if(!access()){ctx.frame('<p role="status">当前账号没有开发排期管理权限。</p>');return}
    ctx.frame('<div id="schedule-root"></div>');
    ui=mountPlanning(document.querySelector('#schedule-root'),{
      imageUrl:image=>images.get(image?.imageId),imagePolicy:{accept:'image/jpeg,image/png,image/webp',maxFiles:6,maxBytes:512*1024,description:'每条最多6张，压缩后每图≤512KiB，总量≤3MiB。'},
      onNavigate:page=>ctx.navigate(page==='pool'?'#schedule/pool':'#schedule'),
      onOpen:async id=>{await readDetail(id);editingBaseline={id,revision:guardRecord(id).revision}},onReload:async()=>{await refreshed();display()},
      onConflictResolution:async info=>{
        const latest=guardRecord(info.id);
        if(info.snapshotToken!==currentSnapshot().snapshotToken||latest.revision!==info.latestRecord.revision)throw new Error('核对期间资料又有变化，请重新读取最新值。');
        editingBaseline={id:latest.id,revision:latest.revision};
      },
      onResetPreview:async()=>{preview=(await adapter.request(BOARD+'/reset-preview',{signal})).data;ui.setSnapshot({...currentSnapshot(),resetCounts:preview.counts})},
      onSave:async payload=>{
        const id=payload.id,body={expectedGeneration:board.data.generation,...(id?{expectedRequirementRevision:editingBaseline?.id===id?editingBaseline.revision:guardRecord(id).revision}:{}),fields:fields(payload.fields)};
        return perform(id?'PUT':'POST',BOARD+'/requirements'+(id?'/'+safeId(id):''),body,id?actions.update:actions.create);
      },
      onMove:async payload=>{
        const r=guardRecord(payload.id),columns=[...new Set([r.status,payload.status])];
        const body={expectedGeneration:board.data.generation,expectedRequirementRevision:r.revision,expectedOrderRevisions:Object.fromEntries(columns.map(s=>[s,board.data.orderRevisions[s]])),targetStatus:payload.status,anchorId:payload.targetId,placement:payload.placement};
        return perform('POST',BOARD+'/requirements/'+safeId(payload.id)+'/move',body,actions.move);
      },
      onComment:async payload=>{
        if(!commentCapability.enabled)return {status:'error',message:commentCapability.reason};
        let encoded;try{encoded=await encodeImages(payload.images)}catch(error){return {status:'error',message:error.message}}
        return perform('POST',BOARD+'/requirements/'+safeId(payload.id)+'/comments',commentCreateBody(board.data.generation,payload,encoded),actions.comment);
      },
      onDeleteComment:async payload=>perform('POST',BOARD+'/requirements/'+safeId(payload.id)+'/comments/'+safeId(payload.commentId)+'/delete',{expectedGeneration:board.data.generation},actions.deleteComment),
      onReset:async()=>perform('POST',BOARD+'/reset',{expectedGeneration:preview.generation,expectedBoardRevision:preview.boardRevision,expectedCounts:preview.counts,exampleVersion:preview.exampleVersion,confirm:'replace_shared_with_examples'},actions.reset),
      onReconcile:async original=>receiptOutcome(original.receipt||command),
      onRetry:async pending=>{const original=pending.receipt||command;try{await adapter.request(original.path,{method:original.method,body:original.body,key:original.key,signal});return await receiptOutcome(original)}catch(error){if([401,403].includes(error.status))return {status:error.status===401?'unauthenticated':'forbidden'};if(error.status===409){command=null;return {status:'conflict',snapshot:await refreshed(),message:error.message+'，请重新核对。'}}return {status:'unknown',receipt:original}}}
    });
    ui.setSnapshot({...identity(),loadState:'loading',complete:false});ui.getState().page=route==='schedule/pool'?'pool':'home';
    try{
      await readContext();
      await readBoard();if(generation!==epoch||abortSignal.aborted)return;display();
      timer=setInterval(()=>{if(!document.hidden)void verify()},30000);
    }catch(error){if(generation!==epoch||abortSignal.aborted)return;ui?.setSnapshot({...identity(),loadState:'error',complete:false});}
  }
  async function verify(){
    const generation=epoch;try{await ctx.recheckScheduleSession(signal);if(generation!==epoch||!access())return;const change=await readContext();if(generation!==epoch||!access())return;if(change?.capabilityChanged||ui?.getState().loadState==='error'){await refreshed({refreshIdentity:false});display()}else if(change?.authorChanged)display()}
    catch(error){if(generation!==epoch)return;if([401,403].includes(error.status)){denied(error);return}ui?.setSnapshot({...identity(),loadState:'error',complete:false})}
  }
  function pause(){epoch++;clearInterval(timer);timer=null;adapter.clear();ui?.dispose();ui=null;board=null;commentCapability=normalizeCommentCapability(null,null);contextSequence++;preview=null;details.clear();revokeImages();command=null;editingBaseline=null;authorizationSuspended=false;signal=null}
  function clear(){pause()}
  return {load:start,pause,clear,verify,click(){},submit(){},input(){},change(){}};
}
