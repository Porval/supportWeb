import {newDraft,draftOf,validateDraft,moveIntent,normalizeOutcome,businessDate} from './planning-model.js';
import {compressImage,displayImageUrl} from './planning-images.js';

// Host owns authentication, transport, authoritative snapshots and request receipts.
// Every callback is optional: absent actions are disabled, never fake successes.
export function createPlanningController(options = {}) {
  let epoch=0, pendingController=null, disposed=false, savedDraft='', returnFocus=null;
  const urls=new Set();
  const callbacks={save:options.onSave,move:options.onMove,comment:options.onComment,deleteComment:options.onDeleteComment,reset:options.onReset};
  const state={access:'unauthenticated',principalKey:null,loadState:'loading',complete:false,requirements:[],statistics:null,
    today:businessDate(),duePolicy:null,demo:false,snapshotToken:null,generation:null,page:'home',filters:{owner:'',priority:''},draft:null,editId:null,
    comment:{text:'',images:[]},commentIdentity:{negotiated:false,enabled:false,currentAuthor:null,reason:'评论身份服务待升级，暂不能发送；未提交草稿已保留。'},fieldErrors:{},imageErrors:[],compressing:false,busy:false,message:'',outstanding:null,
    conflict:false,conflictReview:null,conflictKind:null,discard:false,resetConfirm:false,resetToken:null,zoom:null,imageUrl:options.imageUrl,imagePolicy:options.imagePolicy,
    can:Object.fromEntries(Object.entries(callbacks).map(([key,value])=>[key,typeof value==='function']))};
  const draw=()=>{if(!disposed)options.render?.(state)};
  const revoke=image=>{if(image?.preview){options.revokeURL?.(image.preview);if(!options.revokeURL)URL.revokeObjectURL(image.preview);urls.delete(image.preview)}};
  function clearImages(){state.comment.images.forEach(revoke);state.comment.images=[]}
  function clearDraft(){clearImages();state.draft=null;state.editId=null;state.comment.text='';state.fieldErrors={};state.imageErrors=[];state.discard=false;state.zoom=null;savedDraft='';state.conflict=false;state.conflictReview=null;state.conflictKind=null}
  function authorized(){return !disposed&&state.access==='authorized'&&state.loadState==='ready'&&state.complete===true}
  function writable(){return authorized()&&!state.busy&&!state.outstanding&&!state.conflict}
  function setSnapshot(input) {
    const recovering=state.loadState==='error'&&input.complete===true&&input.loadState==='ready';
    const was=state.principalKey;
    const identityChanged=was!==input.principalKey;
    if(state.generation!=null&&input.generation!=null&&state.generation!==input.generation){clearDraft();state.resetConfirm=false;state.message='共享排期已重置，旧代次输入已关闭。'}
    if(identityChanged || input.access!=='authorized') {
      epoch++;pendingController?.abort();clearDraft();state.outstanding=null;state.busy=false;state.compressing=false;
      state.commentIdentity={negotiated:false,enabled:false,currentAuthor:null,reason:'请先核对登录身份。'};state.filters={owner:'',priority:''};state.resetConfirm=false;state.conflict=false;state.message='';
    }
    // Permission loss removes protected records and image references immediately.
    if(input.access!=='authorized') {
      state.access=input.access==='forbidden'?'forbidden':'unauthenticated';state.principalKey=input.principalKey??null;
      state.requirements=[];state.statistics=null;state.complete=false;state.snapshotToken=null;state.imageUrl=undefined;draw();return;
    }
    if(state.resetConfirm && state.resetToken!==input.snapshotToken){state.resetConfirm=false;state.message='共享排期已变化，请重新核对影响范围后确认重置。'}
    for(const key of ['access','principalKey','loadState','complete','requirements','statistics','today','duePolicy','demo','snapshotToken','generation','permissionUnavailable','doingItems','resetCounts','commentIdentity']) {
      if(Object.hasOwn(input,key))state[key]=key==='requirements'?structuredClone(input[key]):input[key];
    }
    state.imageUrl=options.imageUrl;
    if(recovering)state.message=state.draft?'共享资料已重新读取，未保存输入已保留。':'共享资料已重新读取。';
    refreshConflictReview();
    if(Object.hasOwn(input,'requirements')&&!Object.hasOwn(input,'statistics'))state.statistics=null;
    draw();
  }
  function refreshConflictReview(){
    state.conflictReview=null;
    if(!state.conflict||state.conflictKind!=='save'||!state.editId||!state.draft||!state.complete)return;
    const latest=state.requirements.find(r=>r.id===state.editId);
    if(!latest||!Object.hasOwn(latest,'note')||!Number.isInteger(latest.revision))return;
    state.conflictReview={latestRecord:structuredClone(latest),fields:draftOf(latest),snapshotToken:state.snapshotToken};
  }
  const record=id=>state.requirements.find(r=>r.id===id);
  function open(id=null,comments=false,focusKey=null) {
    if(!authorized()||state.busy||state.outstanding)return;
    if(state.draft&&dirty()){state.discard=true;state.message='请先保存或关闭当前需求。';draw();return}
    clearDraft();const r=id?record(id):null;if(id&&!r)return;
    state.editId=id;state.draft=r?draftOf(r):newDraft();savedDraft=JSON.stringify(state.draft);state.message='';
    returnFocus=focusKey;draw();options.focus?.(comments?'comments':'title');
  }
  function dirty(){return state.draft&&(JSON.stringify(state.draft)!==savedDraft||state.comment.text.trim()||state.comment.images.length)}
  function close(force=false){if(state.busy){state.message='正在核对操作结果，请稍候。';draw();return}if(!force&&dirty()){state.discard=true;draw();return}clearDraft();draw();options.restoreFocus?.(returnFocus)}
  function patchDraft(key,value){if(!state.draft||!writable())return;if(!Object.hasOwn(newDraft(),key))return;
    state.draft[key]=key==='progress'?(value===''?null:Number(value)):value;
    if(key==='status'&&value==='done')state.draft.progress=100;
    delete state.fieldErrors[key];
  }
  function patchComment(key,value){if(!writable()||key!=='text')return;state.comment.text=value}
  function settled(kind){
    if(kind==='save'){clearDraft();options.restoreFocus?.(returnFocus)}
    if(kind==='comment'){clearImages();state.comment.text='';state.imageErrors=[]}
    if(kind==='reset'){clearDraft();state.resetConfirm=false}
  }
  function applyOutcome(raw,kind,payload){
    const outcome=normalizeOutcome(raw);
    if(['forbidden','unauthenticated'].includes(outcome.status)){setSnapshot({access:outcome.status,principalKey:state.principalKey});return}
    // Saved must include a new authoritative complete snapshot (or a refreshed
    // one from receipt reconciliation); never optimistically mutate the board.
    if(outcome.status==='saved'&&(!outcome.snapshot||outcome.snapshot.complete!==true))outcome.status='unknown';
    if(outcome.snapshot)setSnapshot({...outcome.snapshot,access:state.access,principalKey:state.principalKey});
    if(outcome.status==='saved'){state.outstanding=null;settled(kind);state.message=outcome.message||'已由共享服务确认保存。'}
    else if(outcome.status==='conflict'){state.conflict=true;state.conflictKind=kind;refreshConflictReview();state.outstanding=null;state.resetConfirm=false;state.message=outcome.message||'共享数据已被修改；已保留输入，请读取最新资料后核对再保存。'}
    else if(outcome.status==='unknown'){
      state.outstanding={kind,payload,receipt:outcome.receipt??null,retryable:outcome.retryable===true};state.message=outcome.message||'操作结果未知，尚未自动重发。请核对本次结果。';
    }else state.message=outcome.message||'尚未保存，请保留输入后重试。';
  }
  async function run(kind,payload){
    if(!writable()||typeof callbacks[kind]!=='function')return;
    const generation=epoch,actor=state.principalKey;
    state.busy=kind;state.message='';pendingController=new AbortController();const controller=pendingController;draw();
    try{const result=await callbacks[kind](payload,{snapshotToken:state.snapshotToken,record:structuredClone(record(payload?.id)??null),signal:controller.signal});
      if(disposed||generation!==epoch||actor!==state.principalKey)return;applyOutcome(result,kind,payload);
    }catch(error){if(disposed||generation!==epoch)return;applyOutcome({status:'unknown',message:'未取得确定保存结果，请核对本次操作；草稿已保留。'},kind,payload)}
    finally{if(generation===epoch&&!disposed){state.busy=false;pendingController=null;draw()}}
  }
  async function addImages(files){
    if(!writable()||!state.draft||!state.editId||state.compressing)return;
    const generation=epoch,opened=state.draft;state.compressing=true;state.imageErrors=[];draw();
    for(const file of files){
      if(disposed||generation!==epoch||opened!==state.draft)break;
      const policy=state.imagePolicy||{};
      try{
        if(policy.maxFiles!=null&&state.comment.images.length>=policy.maxFiles)throw new Error(`最多选择${policy.maxFiles}张图片。`);
        if(policy.maxInputBytes!=null&&file.size>policy.maxInputBytes)throw new Error('图片超过当前服务允许的大小。');
        const compressed=await (options.compressImage||compressImage)(file);
        if(disposed||generation!==epoch||opened!==state.draft)break;
        if(policy.maxBytes!=null&&compressed.blob.size>policy.maxBytes)throw new Error('压缩图片仍超过512KiB，请选择较小图片。');
        const preview=(options.createURL||URL.createObjectURL)(compressed.blob);urls.add(preview);state.comment.images.push({...compressed,preview});
      }catch(error){if(generation===epoch&&opened===state.draft)state.imageErrors.push(`${file.name || '图片'}：${error.message || '处理失败'}`)}
    }
    if(generation===epoch&&!disposed){state.compressing=false;draw()}
  }
  async function act(action,args={}) {
    if(!authorized()&&action!=='reload')return;
    if(action==='home'||action==='pool'){state.page=action;draw();options.onNavigate?.(action);return}
    if(action==='edit'||action==='comments'){if(options.onOpen){if(!writable()||state.detailLoading)return;const generation=epoch;state.detailLoading=true;draw();try{await options.onOpen(args.id);if(generation!==epoch||disposed)return}catch(error){if(generation===epoch){state.message=error.message||'详情暂不可读';draw()}return}finally{if(generation===epoch){state.detailLoading=false;draw()}}}open(args.id,action==='comments',args.focusKey);return}
    if(action==='new'&&!state.detailLoading){open(null,false,args.focusKey);return}
    if(action==='close'){close();return}if(action==='discard'){close(true);return}
    if(action==='keep'){state.discard=false;draw();return}
    if(action==='filter'&&['owner','priority'].includes(args.key)){state.filters[args.key]=args.value;draw();return}
    if(action==='reload'){try{const result=await options.onReload?.();if(state.complete&&state.loadState==='ready'&&state.conflictKind!=='save'){state.conflict=false;draw()}return result}catch{state.message='无法读取最新共享排期，输入仍未保存。';draw();return}}
    if(action==='zoom'){const c=record(state.editId)?.comments?.find(c=>c.id===args.comment);const url=displayImageUrl(state.imageUrl?.(c?.images?.[args.index]));if(url){state.zoom=url;draw()}return}
    if(action==='zoom-close'){state.zoom=null;draw();return}
    if(action==='remove-image'&&writable()){revoke(state.comment.images[args.index]);state.comment.images.splice(args.index,1);draw();return}
    if(action==='reset'&&writable()&&state.can.reset){if(options.onResetPreview){try{await options.onResetPreview()}catch(error){state.message=error.message||'无法核对重置范围';draw();return}if(!writable())return}state.resetConfirm=true;state.resetToken=state.snapshotToken;draw();return}
    if(action==='reset-cancel'){state.resetConfirm=false;draw();return}
    if(action==='reset-confirm'&&state.resetConfirm){if(state.resetToken!==state.snapshotToken){state.resetConfirm=false;state.message='共享排期已变化，请重新确认。';draw();return}return run('reset',{count:state.requirements.length})}
    if(['conflict-adopt','conflict-resubmit'].includes(action)&&state.conflict&&state.conflictReview&&!state.busy){
      const review=state.conflictReview,generation=epoch;
      state.busy='conflict-review';draw();
      try{
        await options.onConflictResolution?.({id:state.editId,latestRecord:review.latestRecord,snapshotToken:review.snapshotToken,choice:action});
        if(generation!==epoch||disposed)return;
        if(state.conflictReview?.snapshotToken!==review.snapshotToken)throw new Error('最新值再次变化，请重新核对。');
        if(action==='conflict-adopt'){state.draft={...review.fields};savedDraft=JSON.stringify(state.draft)}
        state.conflict=false;state.conflictKind=null;state.conflictReview=null;state.message='已采用最新字段，尚未执行额外保存。';
      }catch(error){if(generation===epoch)state.message=error.message||'无法确认最新值，请重新读取。';return}
      finally{if(generation===epoch&&!disposed){state.busy=false;draw()}}
      if(action==='conflict-resubmit')return act('save');return;
    }
    if(action==='save'&&state.draft){state.fieldErrors=validateDraft(state.draft);if(Object.keys(state.fieldErrors).length){draw();options.focus?.(Object.keys(state.fieldErrors)[0]);return}
      return run('save',{id:state.editId,fields:{...state.draft,title:state.draft.title.trim(),owner:state.draft.owner.trim(),progress:state.draft.status==='done'?100:state.draft.progress}})}
    if(action==='move'){const move=moveIntent(args.id,args.status,args.targetId,args.placement);if(move&&record(args.id)&&(!move.targetId||record(move.targetId)?.status===move.status))return run('move',move);return}
    if(action==='comment'&&state.editId&&!state.compressing){if(!state.commentIdentity.enabled){state.message=state.commentIdentity.reason;draw();return}
      if(!state.comment.text.trim()&&!state.comment.images.length){state.message='请填写评论或选择图片。';draw();return}
      if(state.imageErrors.length){state.message='有图片处理失败，请重新选择或明确移除失败项后再发送。';draw();return}
      return run('comment',{id:state.editId,text:state.comment.text,images:state.comment.images.map(({blob,name,width,height})=>({blob,name,width,height}))})}
    if(action==='dismiss-image-errors'){state.imageErrors=[];draw();return}
    if(action==='delete-comment'&&state.editId&&record(state.editId)?.comments?.some(c=>c.id===args.id))return run('deleteComment',{id:state.editId,commentId:args.id});
    if(action==='retry-original'&&state.outstanding?.retryable&&!state.busy&&typeof options.onRetry==='function'){const generation=epoch,original=state.outstanding;state.busy='retry';draw();try{const value=await options.onRetry(original);if(generation===epoch&&!disposed)applyOutcome(value,original.kind,original.payload)}catch{if(generation===epoch)state.message='仍不能确认原请求结果，未创建新请求。'}finally{if(generation===epoch&&!disposed){state.busy=false;draw()}}return}
    if(action==='reconcile'&&state.outstanding&&!state.busy&&typeof options.onReconcile==='function'){
      const generation=epoch,original=state.outstanding;state.busy='reconcile';draw();
      try{const value=await options.onReconcile(original);if(generation===epoch&&!disposed)applyOutcome(value,original.kind,original.payload)}
      catch{if(generation===epoch)state.message='仍无法确认本次操作，未重发原操作。'}
      finally{if(generation===epoch&&!disposed){state.busy=false;draw()}}
    }
  }
  function dispose(){epoch++;disposed=true;pendingController?.abort();clearDraft();state.requirements=[];state.statistics=null;state.imageUrl=undefined}
  draw();
  return {setSnapshot,act,patchDraft,patchComment,addImages,dispose,getState:()=>state};
}
