import {h} from './ui.js';

const ACTION='activities.rate';
const label=level=>level??'未评级';

export function createActivityRatingController({root,adapter,commands,rerender,notice}){
 let dialog=null;
 let generation=0;

 function clear(){
  generation++;
  dialog?.remove();
  dialog=null;
 }

 async function open(activityId,trigger){
  clear();
  const opening=generation;
  const path='/activities/'+encodeURIComponent(activityId);
  const pending=commands.pending().find(record=>record.action===ACTION&&record.path===path+'/rating');
  if(pending)await commands.resolve(pending.slot);
  const {data:activity}=await adapter.request(path);
  if(opening!==generation)return;
  if(activity.ratingAction?.action!==ACTION)throw new Error('当前账号没有此活动的评级权限。');

  let revision=activity.ratingAction.revision;
  let busy=false;
  const unresolved=pending&&commands.get(pending.slot)?.state==='unknown'?commands.get(pending.slot):null;
  let retryRecord=unresolved;
  dialog=document.createElement('dialog');
  const modal=dialog;
  modal.className='b-rating-dialog';
  modal.setAttribute('aria-labelledby','activity-rating-title');
  modal.innerHTML=`<form>
   <h2 id="activity-rating-title">活动评级</h2>
   <p>${h(activity.name)}</p>
   <p data-rating-saved class="muted">当前已保存等级：${h(label(activity.level))}</p>
   <label>等级<select name="level">
    ${[['SS','SS'],['S','S'],['A','A'],['','未评级']].map(([value,text])=>
     `<option value="${value}">${text}</option>`).join('')}
   </select></label>
   <p data-rating-error role="alert"></p>
   <div class="actions"><button type="button" data-rating-cancel>取消</button>
    <button type="submit" class="primary">保存</button></div>
  </form>`;
  const form=modal.querySelector('form');
  const select=modal.querySelector('select');
  const feedback=modal.querySelector('[data-rating-error]');
  const save=modal.querySelector('[type=submit]');
  const cancel=modal.querySelector('[data-rating-cancel]');
  select.value=unresolved?(unresolved.body?.level??''):(activity.level??'');
  function updateControls(){
   select.disabled=busy||Boolean(retryRecord);
   save.disabled=busy;
   cancel.disabled=busy;
   save.textContent=busy?'正在保存…':retryRecord?'重试保存':'保存';
  }
  function close(){
   if(busy)return;
   clear();
   trigger?.isConnected&&trigger.focus();
  }
  cancel.addEventListener('click',event=>{event.stopPropagation();close()});
  modal.addEventListener('cancel',event=>{event.preventDefault();close()});
  // Keep the dialog's form and controls independent of workbench form routing.
  modal.addEventListener('click',event=>event.stopPropagation());
  form.addEventListener('submit',async event=>{
   event.preventDefault();
   event.stopPropagation();
   if(busy)return;
   busy=true;
   feedback.textContent='';
   updateControls();
   try{
    const body={expectedActivityRevision:revision,level:select.value||null};
    const slot='rating:'+activityId+':'+revision;
    if(retryRecord)await commands.retry(retryRecord.slot);
    else await commands.run(slot,ACTION,'POST',path+'/rating',body);
    if(!modal.isConnected)return;
    clear();
    await rerender();
    notice('活动评级已保存。');
   }catch(error){
    if(!modal.isConnected)return;
    retryRecord=commands.pending().find(record=>record.action===ACTION&&record.path===path+'/rating')??null;
    feedback.textContent=retryRecord?'保存结果待确认，请点击重试保存核对原请求。':error.message??'保存失败，请重试。';
    if(error.status===409&&error.code==='STATE_CONFLICT'){
     try{
      const latest=(await adapter.request(path)).data;
      if(!modal.isConnected)return;
      revision=latest.ratingAction?.revision??latest.revision;
      modal.querySelector('[data-rating-saved]').textContent='当前已保存等级：'+label(latest.level);
      feedback.textContent='活动已更新，请核对当前已保存等级，再确认是否保存所选等级。';
     }catch{
      feedback.textContent='活动已更新，读取最新等级失败。请关闭后重新打开评级。';
     }
    }
   }finally{
    busy=false;
    if(modal.isConnected)updateControls();
   }
  });
  if(unresolved)feedback.textContent='上次保存结果待确认，请点击重试保存核对原请求。';
  updateControls();
  root.append(modal);
  modal.showModal();
 }
 return {open,clear};
}
