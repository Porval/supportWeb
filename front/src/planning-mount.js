import {createPlanningController} from './planning-controller.js';
import {renderPlanning} from './planning-view.js';

// Not auto-mounted and not registered in any route. The existing authenticated
// shell will call this after the transport/admin contract is unlocked.
export function mountPlanning(root,options={}) {
  let controller,dragId=null,trigger=null;
  function focus(key){const node=key==='comments'?root.querySelector('#planning-comments'):root.querySelector(`[name="${key}"]`);node?.focus()}
  function render(state){
    const active=root.contains(document.activeElement)?document.activeElement:null;
    const key=active?.name || active?.id, selection=active?.selectionStart;
    root.innerHTML=renderPlanning(state);options.onState?.(state);
    for(const dialog of root.querySelectorAll('dialog')){
      dialog.addEventListener('cancel',e=>{e.preventDefault();void controller.act(dialog.hasAttribute('data-plan-reset')?'reset-cancel':state.zoom?'zoom-close':'close')});
      dialog.showModal();
    }
    if(key){const node=[...root.querySelectorAll('input,textarea,select,button')].find(n=>n.name===key||n.id===key);node?.focus();if(typeof selection==='number'&&['text','textarea'].includes(node?.type))node.setSelectionRange(selection,selection)}
  }
  controller=createPlanningController({...options,render,focus,restoreFocus:()=>{if(trigger?.isConnected)trigger.focus();else root.querySelector('[data-plan-action="new"]')?.focus()}});
  const listeners=[];
  const on=(type,handler)=>{root.addEventListener(type,handler);listeners.push([type,handler])};
  on('click',event=>{
    const target=event.target.closest('[data-plan-action]');if(!target||!root.contains(target)||target.disabled)return;
    const action=target.dataset.planAction;if(['edit','comments','new'].includes(action))trigger=target;
    void controller.act(action,{id:target.dataset.id,comment:target.dataset.comment,index:Number(target.dataset.index)});
  });
  on('submit',event=>{if(event.target.matches('[data-plan-form]')){event.preventDefault();void controller.act('save')}});
  on('input',event=>{
    const target=event.target;
    if(target.closest('[data-plan-form]')&&target.name)controller.patchDraft(target.name,target.value);
    if(target.dataset.planComment)controller.patchComment(target.dataset.planComment,target.value);
  });
  on('change',event=>{
    const target=event.target;
    if(target.dataset.planFilter)void controller.act('filter',{key:target.dataset.planFilter,value:target.value});
    if(target.matches('[data-plan-images]')){const files=[...target.files];target.value='';void controller.addImages(files)}
    if(target.name==='status'&&target.closest('[data-plan-form]')){
      controller.patchDraft('status',target.value);const progress=root.querySelector('[name="progress"]');if(progress){progress.value=controller.getState().draft.progress??'';progress.readOnly=target.value==='done'}
    }
  });
  on('keydown',event=>{
    if((event.ctrlKey||event.metaKey)&&event.key==='Enter'&&event.target.matches('[data-plan-comment="text"]')){event.preventDefault();void controller.act('comment')}
    if(event.target.matches('[data-plan-card]')&&['Enter',' '].includes(event.key)){event.preventDefault();trigger=event.target;void controller.act('edit',{id:event.target.dataset.planCard})}
  });
  on('dragstart',event=>{
    const card=event.target.closest('[data-plan-card]');if(!card||card.draggable!==true){event.preventDefault();return}
    dragId=card.dataset.planCard;event.dataTransfer.effectAllowed='move';event.dataTransfer.setData('text/plain',dragId);
  });
  function clearDrop(){for(const n of root.querySelectorAll('.drop-active,.insert-before,.insert-after'))n.classList.remove('drop-active','insert-before','insert-after')}
  on('dragover',event=>{
    const col=event.target.closest('[data-plan-column]');if(!dragId||!col)return;
    event.preventDefault();event.dataTransfer.dropEffect='move';clearDrop();col.classList.add('drop-active');
    const card=event.target.closest('[data-plan-card]');if(card&&card.dataset.planCard!==dragId){const r=card.getBoundingClientRect();card.classList.add(event.clientY<r.top+r.height/2?'insert-before':'insert-after')}
  });
  on('drop',event=>{
    const col=event.target.closest('[data-plan-column]');if(!dragId||!col)return;event.preventDefault();
    const card=event.target.closest('[data-plan-card]'),id=dragId;dragId=null;
    const rect=card?.getBoundingClientRect();const placement=card?(event.clientY<rect.top+rect.height/2?'before':'after'):'end';
    clearDrop();void controller.act('move',{id,status:col.dataset.planColumn,targetId:card?.dataset.planCard??null,placement});
  });
  on('dragend',()=>{dragId=null;clearDrop()});
  return {setSnapshot:controller.setSnapshot,getState:controller.getState,act:controller.act,dispose(){listeners.forEach(([type,fn])=>root.removeEventListener(type,fn));controller.dispose();root.replaceChildren()}};
}
