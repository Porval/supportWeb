import {installBusinessLabels} from './b-common.js';
import {createRegistrationDraftController} from './b-registration-draft.js';
import {hostedAdapter as adapter} from './hosted-adapter.js';
import {h,badge,empty,link,workbench,activityExecutionProgressHtml,activityDetail,brandDetail,routeLink} from './ui.js';
import {beijing,supportsActivityIntake} from './dto.js';
import {makeDownload} from './export.js';
import {createCommandClient} from './b-commands.js';
import {createActivityRatingController} from './b-rating.js';
import {createSubmissionsController} from './b-submissions.js';
import {activityHistoryHtml,activityReviewReturn,couponReviewHtml,reviewRequirementsHtml,readCouponSelection,saveCouponSelection,updateCouponBulkSelection,createReviewsController,decideWorkbenchReview,isFullAccessAdministrator,reviewWorkSummary,reviewWorkSummaryHtml} from './b-reviews.js';
import {ROLE_LABELS} from './dto.js';
import {ACTION_LABELS,actionAllowed} from './b-common.js';
import {dateEvidenceHtml,originalSubmitterNames,originalSubmissionSummaryHtml,versionSourcesHtml} from './b-sources.js';
import {supplementDatesView,readBrandSupplementDates} from './b-group-review.js';
import {createProductSales} from './product-metrics.js';

const root=document.querySelector('#test-app'),notice=document.querySelector('#test-notice');
const productSales=createProductSales({root,adapter});
let platformModule=null,autoResolve=null;
const {createAccountsController}=await import('./b-accounts.js');
const {createScheduleController}=await import('./schedule-board.js');
let current={route:'activity',params:{},hash:'#activity'},session=null,data=null;
let intakeReviewAccess={},activityIntake=false,authMethod=null;
let phoneLoginCleanup=null,smsStatus=null;
installBusinessLabels(document.body,()=>[data,session]);
let renderId=0,authEpoch=0,renderController=null,loginController=null,exportController=null;
let loginAttempt=0,loginBusy=false,logoutBusy=false,recoveryBusy=false,usernameCache='',recentDownload=null,expiryTimer=null,noticeTimer=null;
const positions=new Map(),expansions=new Map(),materialUrls=new Set();
const commands=createCommandClient(adapter,{namespace:'activity-workbench'});
const registrationDraft=createRegistrationDraftController({root,adapter,commands,notice:notify,mountSales:()=>productSales.mount(renderController?.signal)});
const rating=createActivityRatingController({root,adapter,commands,rerender:render,notice:notify});
const navigate=hash=>{if(location.hash===hash)render();else location.hash=hash};
const controllerContext={adapter,commands,frame,
 get activityIntake(){return activityIntake},get autoResolve(){return autoResolve},session:()=>session,route:()=>current.route,reviewAccess:()=>intakeReviewAccess,rerender:render,notice:notify,navigate,authError:handleAuthError};
const accounts=createAccountsController?.(controllerContext),submissions=createSubmissionsController(controllerContext),reviews=createReviewsController(controllerContext);
const schedule=createScheduleController?.({...controllerContext,recheckScheduleSession,scheduleDenied,scheduleUnavailable});
let platformMaterials=platformModule?.createPlatformMaterialsController(controllerContext);
async function prepareActivityCapabilities(capabilities,signal){
 const modern=supportsActivityIntake(capabilities);
 const [platform,automatic]=await Promise.all([
  !platformModule&&capabilities?.platformMaterials?.enabled===true?import('./b-platform-materials.js'):null,
  !autoResolve&&modern?import('./intake-auto.js'):null,
 ]);
 if(signal.aborted)return;
 if(platform&&!platformModule&&platform.platformMaterialsCapability(capabilities)){
  platformModule=platform;
  platformMaterials=platform.createPlatformMaterialsController(controllerContext);
 }
 if(automatic)autoResolve=automatic.resolveIntakeAutomatically;
 activityIntake=modern;
 intakeReviewAccess=getIntakeReviewAccess(capabilities);
}

function activeController(){if(platformModule?.parsePlatformMaterialsRoute(current.route))return platformMaterials;return ['schedule','schedule/pool'].includes(current.route)?schedule: current.route==='accounts'?accounts:current.route==='report'||current.route.startsWith('submissions')?submissions:current.route.startsWith('review')?reviews:null}
const permitted=permission=>session?.permissions?.includes(permission)===true;
function getIntakeReviewAccess(capabilities){
 const supported=capabilities?.reviewListContractVersion==='reviews/1.1'&&capabilities.implemented?.includes('reviews')===true;
 const allowed=permission=>supported&&permitted(permission)&&capabilities.allowedActions?.includes(permission)===true;
 return {capabilities,list:allowed('reviews.read'),decide:allowed('reviews.decide')};
}

function parse(){const hash=location.hash||'#activity',raw=hash.slice(1),i=raw.indexOf('?');return {route:(i<0?raw:raw.slice(0,i))||'activity',params:Object.fromEntries(new URLSearchParams(i<0?'':raw.slice(i+1))),hash}}
const isWorkbenchList=route=>['activity','brand','product'].includes(route);
const viewHash=hash=>hash.startsWith('#demo/')?hash:'#demo/'+(hash.slice(1)||'activity');
function localHash(hash){if(!hash.startsWith('#demo/'))return hash;const raw=hash.slice(6),i=raw.indexOf('?'),route=i<0?raw:raw.slice(0,i),p=new URLSearchParams(i<0?'':raw.slice(i+1));if(p.get('back'))p.set('back',localHash(p.get('back')));return '#'+route+(p.size?'?'+p:'')}
function notify(text){clearTimeout(noticeTimer);notice.textContent=text;notice.classList.add('visible');noticeTimer=setTimeout(()=>notice.classList.remove('visible'),7500)}
function revokeDownload(){if(recentDownload)URL.revokeObjectURL(recentDownload.url);recentDownload=null;root.querySelector('#download-result')?.replaceChildren()}
function clearProtected(){registrationDraft.clear();rating.clear();schedule?.clear();submissions.clear();reviews.clear();platformMaterials?.clear();accounts?.clear();commands.clear();for(const url of materialUrls)URL.revokeObjectURL(url);materialUrls.clear();authEpoch++;renderId++;renderController?.abort();exportController?.abort();clearTimeout(expiryTimer);data=null;session=null;usernameCache='';intakeReviewAccess={};activityIntake=false;clearTimeout(noticeTimer);notice.textContent='';notice.classList.remove('visible');positions.clear();expansions.clear();revokeDownload()}
function clearPassword(){const p=root.querySelector('[data-test-password]');if(p){p.value='';p.type='password'}const toggle=root.querySelector('[data-test-action="toggle-password"]');if(toggle){toggle.textContent='显示';toggle.setAttribute('aria-pressed','false')}}
function handleAuthError(error){if(error.status!==401)return false;clearProtected();adapter.clear();loginPage(errorMessage(error));return true}
function identityChanged(value){return session?.authenticated&&value.authenticated&&(session.user.id!==value.user.id||session.user.businessRole!==value.user.businessRole||JSON.stringify(session.permissions)!==JSON.stringify(value.permissions)||isFullAccessAdministrator(session)!==isFullAccessAdministrator(value))}

async function recheckScheduleSession(signal){
 const epoch=authEpoch,value=await adapter.session({signal});
 if(epoch!==authEpoch||signal?.aborted)throw new DOMException('Aborted','AbortError');
 if(!value.authenticated){const error=new Error('登录已失效');error.status=401;throw error}
 if(identityChanged(value)||!value.permissions.includes('accounts.manage')){const error=new Error('当前会话权限已变化，请重新核对');error.status=403;throw error}
 session=value;expiryCheck();return value;
}
function scheduleDenied(error){
 if(error.status===401){handleAuthError(error);return}
 clearProtected();adapter.clear();frame(empty('开发排期访问权限已失效','排期、弹窗和图片已清理，请重新核对会话。','<button data-test-action="retry">重新核对会话</button>'));
}
function scheduleUnavailable(){frame(empty('暂不能核对排期访问权限','受保护内容已隐藏，请重新核对后继续。','<button data-test-action="retry">重新核对会话</button>'))}

function reasonMessage(reason){return reason?'登录已失效，请重新登录。':''}
function errorMessage(error){return error.code==='INVALID_CREDENTIALS'?'账号或密码不正确。':error.message||'无法连接工作台，请稍后重试。'}
function shell(content){
 const signed=session?.authenticated===true;
 const planning=['schedule','schedule/pool'].includes(current.route);
 const role=isFullAccessAdministrator(session)?'管理员':ROLE_LABELS[session?.user?.businessRole]??'未分配业务角色';
 const nav=(path,label,active)=>`<a class="nav-item ${active?'active':''}" href="${path}">${label}</a>`;
 const links=[nav('#activity','活动工作台',['activity','brand','product'].includes(current.route.split('/')[0]))];
 if(signed&&isFullAccessAdministrator(session)&&session.capabilities?.activityAgentChat===true)links.push(nav('/agent-chat','DeepSeek 对话',false));
 if(signed&&permitted('submissions.read_own'))links.push(nav('#submissions','我的提报',current.route.startsWith('submissions')));
 if(signed&&permitted('reviews.read'))links.push(nav('#reviews?view=pending','审核待办',current.route.startsWith('review')));
 if(signed&&permitted('accounts.manage'))links.push(nav('#accounts','账号管理',current.route==='accounts'),nav('#schedule','开发排期',planning));
 const identity=signed?(session.anonymous===true?`<span class="test-header-identity"><strong>匿名访客</strong><span>只读查看</span></span><button type="button" data-test-action="logout" ${logoutBusy?'disabled':''}>账号登录</button>`:`<span class="test-header-identity"><strong>${h(session.user.name||'已登录')}</strong><span>${h(role)}</span></span><button type="button" data-test-action="profile">本人信息</button><button type="button" data-test-action="logout" ${logoutBusy?'disabled':''}>退出登录</button>`):badge('未登录');
 return `<div class="layout test-shell"><aside><a class="brand" href="#activity"><span class="logo" aria-hidden="true">价</span><b>价探</b></a><nav aria-label="主导航">${links.join('')}</nav></aside><div class="workspace"><header class="topbar"><span>价探 · ${planning?'开发排期':'活动运营'}</span><div class="row">${signed&&session.readOnly?badge('只读'):''}${identity}</div></header><main id="main" tabindex="-1">${content}</main><footer>价探 · 活动工作台<span>日期与时间均为北京时间</span></footer></div></div>`;
}
function canDecideVersion(version) {
 return intakeReviewAccess.decide && actionAllowed(version,'reviews.decide') &&
  version?.nodes?.some(node=>node.stage===version.review?.stage && node.state==='pending');
}
function workbenchCouponTable(activity, version) {
 const coupons=version?.couponReview;
 if(!coupons || version.activityId!==activity.id)return null;
 const editable=canDecideVersion(version) && activity.reviewAction?.action==='reviews.decide' &&
  activity.reviewAction.versionId===version.id;
 const decision=editable?`<button type="button" class="primary" data-coupon-review-submit="${h(activity.id)}">审核</button>`:'';
 return `<div id="review-coupon-selection" data-version-id="${h(version.id)}">${couponReviewHtml(coupons,editable,undefined,version.review?.stage)}${decision}</div>`;
}
function frame(content,{workbenchView}={}){
 phoneLoginCleanup?.();
 rating.clear();
 document.title=['schedule','schedule/pool'].includes(current.route)?'价探 · 开发排期':'价探 · 活动工作台';
 root.innerHTML=shell(content);
 if(workbenchView)root.querySelector('#main').dataset.workbenchView=workbenchView;
 for(const a of root.querySelectorAll('a[href^="#demo/"]'))a.setAttribute('href',localHash(a.getAttribute('href')));
 for(const a of root.querySelectorAll('a[href^="#report"]'))if(!permitted('submissions.create'))a.remove();
 if(activityIntake&&!intakeReviewAccess.list)for(const a of root.querySelectorAll('a[href="#reviews"],a[href^="#reviews?"]'))a.setAttribute('href','#submissions');
 const process=root.querySelector('#process');if(process){for(const e of process.querySelectorAll('label,fieldset,.actions'))e.remove();const p=document.createElement('p');p.className='test-readonly-note';p.textContent='本区仅展示已记录审核事实；当前允许访问的具体版本见下方入口。';process.append(p)}
 if(data){const p=document.createElement('p');p.className='source-meta test-source';p.textContent=`${data.meta.sourceLabel||'价探活动工作台'} · 数据版本：${data.meta.datasetRevision??'待核实'} · 获取：${beijing(data.meta.fetchedAt)??'待核实'} · ${data.complete?'当前查询关系完整':current.route==='product'?'只展示和下载已确认的商品关联':'关系不完整，数量与导出待核实'}。`;
  const details=document.createElement('details');details.className='source-meta-details';const summary=document.createElement('summary');summary.textContent='查看数据来源';details.append(summary,p);root.querySelector('#main')?.append(details);for(const b of root.querySelectorAll('[data-action=export],[data-action=export-activity]')){b.disabled=(b.dataset.action==='export-activity'&&!data.complete)||data.products.length===0||!permitted('operations.download');if(b.disabled){b.title=data.productExportReason??'结果为空、缺少完整明细或没有下载权限，暂不能导出';if(data.productExportReason&&b.dataset.action==='export')b.setAttribute('aria-describedby','product-export-reason')}}
  if(current.route.startsWith('activity/')){const a=data.activities.find(x=>x.id===decodeURIComponent(current.route.split('/')[1]));if(a){const host=root.querySelector('#materials');if(host){
   const source=data.authorizedSource;
   const choices=a.authorizedVersions??[];
   const version=source?.version;
   const selector=choices.length>1?`<label>查看来源版本<select data-source-version>${choices.map(v=>
    `<option value="${h(v.versionId)}" ${version?.id===v.versionId?'selected':''}>V${h(v.versionNo??'待核实')}</option>`).join('')}</select></label>`:'';
   if(activityIntake){
    const message=source?.message??'当前版本的原提交信息暂不可用。';
    host.innerHTML=`<h2>原提交信息</h2>${version?originalSubmissionSummaryHtml(version):`<p class="muted">${h(message)}</p>`}
     ${version||selector?`<details ${current.params.sourceVersion?'open':''}><summary>查看完整提交记录</summary>${selector}
      ${version?`${versionSourcesHtml(version,session,{brands:data.brands,products:data.products})}
       <p>本次补充：${h(version.changeSummary??'未提供补充说明')}</p>
       <a class="button" href="#review/${h(a.id)}/${h(version.id)}">查看本版审核与历史</a>`:`<p class="muted">${h(message)}</p>`}</details>`:''}`;
   }else{
    host.innerHTML=`<h2>提交资料 / 来源记录</h2>${selector}${version?versionSourcesHtml(version,session,{brands:data.brands,products:data.products}):`<p class="muted">${h(source?.message??'当前版本来源不可用。')}</p>`}`;
   }
  }
   const currentVersion=data.authorizedReview;
   const canStart=actionAllowed(currentVersion,'reviews.start');
   const canDecide=canDecideVersion(currentVersion);
   const work=activityIntake?reviewWorkSummary(a,currentVersion,{canStart,canDecide,fullAccessAdministrator:isFullAccessAdministrator(session)}):null;
   const process=root.querySelector('#process');
   const back=current.hash;
   const target=currentVersion?routeLink('review/'+encodeURIComponent(a.id)+'/'+encodeURIComponent(currentVersion.id),{back,section:'process'}):'';
   const label=canStart?'前往提交审核':canDecide?'前往处理当前审核':'查看本版本审核详情';
   const inlineReview=Boolean(root.querySelector('#review-coupon-selection [data-coupon-review-submit]'));
   if(process)process.innerHTML=`<h2>活动进度</h2>
    <p>审核：${badge(a.reviewLabel,'blue')}</p>${work?`<p>${work.issue?h(work.issue)+'<br>':''}${h(work.next)}</p>`:''}
    ${activityExecutionProgressHtml(data,a,back)}
    ${inlineReview?'':target?`<a class="button" href="${h(target)}">${label}</a>`:'<p class="muted">当前可查看授权范围内的已有记录。</p>'}
    ${activityIntake&&intakeReviewAccess.list?'<p><a href="#reviews?view=pending">打开我的审核待办</a></p>':''}`;

  }}

 }
 if(session?.authenticated){const pending=session.anonymous===true||['schedule','schedule/pool'].includes(current.route)?[]:commands.pending().filter(record=>!submissions.ownsPending?.(record)&&!platformMaterials?.ownsPending(record));if(pending.length){const section=document.createElement('section');section.className='b-recovery';section.setAttribute('aria-label','请求结果恢复');section.innerHTML=`<h2>有待核对的提交结果</h2><p>先读取原请求回执；“未找到”也不能证明在途请求不会提交。重试只沿用原键和正文。</p>${pending.map(r=>`<p>${h(ACTION_LABELS[r.action]??'本次操作')} <button type="button" data-command-resolve="${h(r.slot)}">核对原请求结果</button>${r.body&&!(activityIntake&&r.action==='reviews.decide')?` <button type="button" data-command-retry="${h(r.slot)}">以原请求重试</button>`:' <span>原请求正文未留存；附件需重选同一文件，其他操作需恢复原输入。</span>'}</p>`).join('')}`;root.querySelector('#main')?.prepend(section)}const p=document.createElement('p');p.className='test-main-expiry';p.textContent=(session.anonymous===true?'匿名只读会话到期：':'会话到期：')+`${beijing(session.absoluteExpiresAt)??'待核实'}。`;root.querySelector('#main')?.append(p)}
 attachDownload();
}
function showProfile(){
 if(!session?.authenticated)return;
 const dialog=document.createElement('dialog');dialog.id='test-profile-dialog';dialog.setAttribute('aria-labelledby','test-profile-title');
 dialog.innerHTML=`<div class="section-heading"><h2 id="test-profile-title">本人信息</h2><button type="button" data-test-action="profile-close" aria-label="关闭">×</button></div><dl class="info-grid"><div><dt>姓名</dt><dd>${h(session.user.name||'未填写')}</dd></div><div><dt>业务角色</dt><dd>${h(ROLE_LABELS[session.user.businessRole]??'未分配')}</dd></div><div><dt>登录方式</dt><dd>${session.authMethod==='phone_otp'?'手机验证码':'账号密码'}</dd></div></dl><p>会话到期：${h(beijing(session.absoluteExpiresAt))}</p>`;
 root.append(dialog);dialog.addEventListener('cancel',()=>dialog.remove(),{once:true});dialog.showModal();
}
function loginPage(message='',{retry=false,pendingLogout=false}={}){
 if(authMethod==='phone_otp'){
  const recovery=retry?'<button type="button" data-test-action="retry">重新核对会话</button>':'';
  const retryLogout=pendingLogout?'<button type="button" data-test-action="logout">重试退出当前会话</button>':'';
  const extras=recovery||retryLogout?`<div class="test-login-recovery">${recovery}${retryLogout}</div>`:'';
  frame(`<section class="test-login"><div class="test-login-head"><h1 tabindex="-1">登录活动工作台</h1><p class="test-login-sub">已开通账号请使用手机验证码登录，也可匿名只读浏览已有活动</p></div>
   <form id="hosted-phone-form" class="test-login-form" method="post" action="/api/activity/v1/account/auth/otp/verify">
    <div class="login-field"><label for="hosted-phone">手机号</label>
    <input id="hosted-phone" name="phone" type="tel" inputmode="tel" autocomplete="tel-national" required pattern="1[3-9][0-9]{9}" maxlength="11" placeholder="请输入 11 位手机号"></div>
    <div class="login-field"><label for="hosted-code">验证码</label><div class="password-input">
    <input id="hosted-code" name="code" inputmode="numeric" autocomplete="one-time-code" required pattern="[0-9]{6}" maxlength="6" placeholder="请输入 6 位验证码">
    <button type="button" id="send-code">获取验证码</button></div></div>
    <p id="test-login-message" class="warning test-login-message" role="status">${h(message)}</p>
    <div class="test-login-actions"><button type="submit" class="primary" disabled>登录</button><button type="button" id="anonymous-login" class="secondary-login" data-test-action="anonymous-login">匿名登录</button></div>
   </form>${extras}</section>`);
  mountPhoneLogin(root.querySelector('#hosted-phone-form'));
  return;
 }
 if(authMethod!=='password'){
  frame(empty('暂不能核对登录方式',message||'请重新读取服务状态。','<button data-test-action="retry">重新核对会话</button>'));
  return;
 }
 const passwordField='activity-password';
 const extras=retry||pendingLogout?`<div class="test-login-recovery">${retry?'<button data-test-action="retry">重新核对会话</button>':''}${pendingLogout?'<button data-test-action="logout">重试退出当前会话</button>':''}</div>`:'';
 frame(`<section class="test-login"><div class="test-login-head"><h1 tabindex="-1">登录活动工作台</h1><p class="test-login-sub">管理员请使用账号密码登录，也可匿名只读浏览已有活动</p></div>
 <form id="test-login-form" class="test-login-form" method="post" action="/api/activity/v1/account/login" novalidate>
 <div class="login-field"><label for="test-username">账号</label><input id="test-username" name="username" value="${h(usernameCache)}" autocomplete="username" autocapitalize="none" spellcheck="false" aria-describedby="username-error"><span id="username-error" class="field-error" aria-live="polite"></span></div>
 <div class="login-field"><label for="${passwordField}">密码</label><div class="password-input"><input id="${passwordField}" name="password" data-test-password type="password" autocomplete="current-password" aria-describedby="password-error"><button type="button" data-test-action="toggle-password" aria-controls="${passwordField}" aria-pressed="false">显示</button></div><span id="password-error" class="field-error" aria-live="polite"></span></div>
 <p id="test-login-message" class="warning test-login-message" role="status">${h(message)}</p><div class="test-login-actions"><button type="submit" class="primary" id="test-login-submit">登录</button><button type="button" class="secondary-login" data-test-action="anonymous-login">匿名登录</button></div></form>${extras}</section>`);
 clearPassword();root.querySelector('h1')?.focus({preventScroll:true});
}
function setLoginMessage(text){const p=root.querySelector('#test-login-message');if(p)p.textContent=text}

function mountPhoneLogin(form){
 const {phone,code}=form.elements;
 const send=form.querySelector('#send-code'),submit=form.querySelector('[type=submit]'),anon=form.querySelector('#anonymous-login');
 let challenge=null,cooldown=null,operation=null,timer=null,disposed=false;
 const unavailable=()=>smsStatus==='not_configured';
 function paint(){
  clearTimeout(timer);
  const now=Date.now();
  if(challenge&&now>=challenge.expires){
   challenge=null;code.value='';
   if(!operation)setLoginMessage('验证码已过期，请重新获取。');
  }
  const seconds=cooldown?.phone===phone.value?Math.max(0,Math.ceil((cooldown.until-now)/1000)):0;
  const locked=Boolean(operation)||loginBusy||logoutBusy||recoveryBusy;
  phone.readOnly=locked;code.readOnly=locked;
  send.disabled=locked||unavailable()||seconds>0;
  send.textContent=operation?.kind==='request'?'正在获取…':seconds?'重新获取（'+seconds+' 秒）':'获取验证码';
  submit.disabled=locked||unavailable()||!challenge||challenge.phone!==phone.value||!code.validity.valid;
  submit.textContent=operation?.kind==='verify'?'正在登录…':'登录';
  if(anon){anon.disabled=locked;anon.textContent=loginBusy&&!operation?'正在进入…':'匿名登录'}
  form.setAttribute('aria-busy',String(locked));
  if(challenge||seconds>0)timer=setTimeout(paint,Math.min(1000,challenge?Math.max(1,challenge.expires-now):1000));
 }
 function start(kind){
  operation={kind,controller:new AbortController()};
  loginBusy=true;paint();return operation;
 }
 function finish(op){
  if(operation!==op)return;
  operation=null;loginBusy=false;paint();
 }
 function showFailure(error){
  const retry=error.details?.retryAfterSeconds;
  if(error.status===429&&Number.isFinite(retry)&&retry>0){
   cooldown={phone:phone.value,until:Math.max(cooldown?.phone===phone.value?cooldown.until:0,Date.now()+retry*1000)};
  }
  if(['OTP_ATTEMPTS_EXCEEDED','OTP_EXPIRED','OTP_CHALLENGE_INVALID','OTP_REQUEST_SUPERSEDED'].includes(error.code)){
   challenge=null;
  }
  if(error.code==='SMS_NOT_CONFIGURED')smsStatus='not_configured';
  setLoginMessage(error.message||'暂时无法连接短信登录服务，请稍后重试。');
 }
 function enter(op,value){
  if(operation!==op||disposed)return;
  if(!value.authenticated)throw new Error('服务尚未确认登录。');
  finish(op);session=value;render();
 }
 phone.addEventListener('input',()=>{
  if(challenge&&challenge.phone!==phone.value){
   challenge=null;code.value='';setLoginMessage('手机号已更改，请重新获取验证码。');
  }
  paint();
 });
 code.addEventListener('input',paint);
 form.addEventListener('auth-progress',paint);
 send.addEventListener('click',async()=>{
  if(send.disabled||!phone.reportValidity())return;
  const number=phone.value,op=start('request'),options={signal:op.controller.signal};
  challenge=null;code.value='';setLoginMessage('正在获取验证码…');
  try{
   const state=await adapter.session(options);
   if(disposed||operation!==op)return;
   if(state.authenticated){enter(op,state);return}
   if(state.authMethod!==authMethod){finish(op);render();return}
   const result=await adapter.requestCode(number,options);
   if(disposed||operation!==op)return;
   const resendAt=result?.resendAt??(typeof result?.resendAfter==='string'?result.resendAfter:null);
   const expires=Date.parse(result?.expiresAt),resend=Date.parse(resendAt);
   if(typeof result?.challengeId!=='string'||!result.challengeId||!Number.isFinite(expires)||
      !Number.isFinite(resend)||expires<=Date.now())throw new Error('验证码回执无效，请重新获取。');
   challenge={id:result.challengeId,phone:number,expires};cooldown={phone:number,until:resend};
   if(result.delivery==='unconfirmed'){
    const reason={
     gateway_response_timeout:'短信网关回执超时',
     gateway_connection_lost:'等待短信回执时连接中断',
     gateway_temporarily_unavailable:'短信网关暂时无法确认发送结果',
     gateway_delivery_unconfirmed:'短信网关尚未确认发送结果'
    }[result.reason]??'尚未确认发送结果';
    setLoginMessage(reason+'。收到短信即可输入验证码登录，未收到可在倒计时结束后重新获取。');
   }else{
    setLoginMessage('请输入手机收到的验证码。');
   }
   code.focus();
  }catch(error){
   if(operation===op&&!disposed){
    if(error.code==='TIMEOUT'||error.name==='TypeError')setLoginMessage('暂时无法确认验证码发送结果，请稍后重新获取。');
    else showFailure(error);
   }
  }finally{finish(op)}
 });
 form.addEventListener('submit',async event=>{
  event.preventDefault();paint();
  if(submit.disabled||!form.reportValidity())return;
  const op=start('verify'),options={signal:op.controller.signal};
  setLoginMessage('正在核对验证码…');
  try{
   const state=await adapter.verifyCode(challenge.id,code.value,options);enter(op,state);
  }catch(error){
   if(operation!==op||disposed)return;
   if(error.code==='TIMEOUT'||error.name==='TypeError'||error.code==='ALREADY_AUTHENTICATED'){
    try{
     const state=await adapter.session(options);
     if(operation!==op||disposed)return;
     if(state.authenticated){enter(op,state);return}
    }catch{}
   }
   if(operation===op&&!disposed){code.value='';showFailure(error);code.focus()}
  }finally{finish(op)}
 });
 phoneLoginCleanup=()=>{
  disposed=true;clearTimeout(timer);
  if(operation){operation.controller.abort();loginBusy=false;operation=null}
  code.value='';challenge=null;phoneLoginCleanup=null;
 };
 if(unavailable())setLoginMessage('短信服务尚未配置，暂不能获取验证码。');
 paint();phone.focus({preventScroll:true});
}
function busyLogin(busy){loginBusy=busy;root.querySelector('#hosted-phone-form')?.dispatchEvent(new Event('auth-progress'));const form=root.querySelector('#test-login-form');if(!form)return;form.setAttribute('aria-busy',String(busy));for(const e of form.querySelectorAll('input,button[type=submit],[data-test-action=toggle-password],[data-test-action=anonymous-login]'))e.disabled=busy;const submit=root.querySelector('#test-login-submit');if(submit)submit.textContent=busy?(logoutBusy?'正在退出…':recoveryBusy?'正在核对…':'正在登录…'):'登录';const cancel=form.querySelector('[data-test-action=cancel-login]');if(cancel)cancel.disabled=logoutBusy||recoveryBusy}
function saveContext(){positions.set(current.hash,window.scrollY)}
function expiryCheck(){clearTimeout(expiryTimer);if(!session?.authenticated)return;const until=Math.min(Date.parse(session.absoluteExpiresAt),session.credentialExpiresAt==null?Infinity:Date.parse(session.credentialExpiresAt));expiryTimer=setTimeout(()=>{clearProtected();adapter.clear();loginPage('会话已到期，请重新登录。');render()},Math.max(0,Math.min(until-Date.now(),2147483647)))}
async function render(){
 if(loginBusy||logoutBusy||recoveryBusy)return;
 root.querySelector('#workbench-review[open]')?.close();
 const next=parse();
 const main=root.querySelector('#main[data-workbench-view]');
 const retainWorkbench=session?.authenticated&&main&&(
  main.dataset.workbenchView==='list'&&isWorkbenchList(next.route)||
  main.dataset.workbenchView==='detail'&&next.hash===current.hash);
 saveContext();activeController()?.pause?.();submissions.pause();current=next;renderController?.abort();renderController=new AbortController();const signal=renderController.signal,id=++renderId;data=null;intakeReviewAccess={};activityIntake=false;
 if(retainWorkbench)main.setAttribute('aria-busy','true');
 else frame('<div class="loading" role="status">正在核对会话及查看范围…</div>');
 try{
  const value=await adapter.session({signal});
  if(signal.aborted||id!==renderId)return;
  if(identityChanged(value)){clearProtected();return render()}
  session=value;
  authMethod=value.authMethod;
  smsStatus=value.sms?.status??null;
  if(!value.authenticated){clearProtected();loginPage(reasonMessage(value.reason));return}
  expiryCheck();if(value.anonymous!==true)commands.bind(value.user.id);
  if(value.anonymous===true&&(current.route==='accounts'||current.route==='schedule'||current.route==='schedule/pool'||current.route==='report'||current.route==='reviews'||current.route.startsWith('submissions'))){
   frame(empty('匿名查看仅支持浏览已有活动','请返回活动工作台查看已有活动版本，或切换已开通账号登录。','<a class="button" href="#activity">返回活动工作台</a> <button type="button" data-test-action="logout">账号登录</button>'));
   return;
  }
  let controller=activeController();
  const platformRoute=current.route.split('/')[2]==='platform';
  if(platformRoute||controller===submissions||controller===reviews){
   try{
    const {data:capabilities}=await adapter.request('/capabilities',{signal});
    if(signal.aborted||id!==renderId)return;
    await prepareActivityCapabilities(capabilities,signal);
    if(signal.aborted||id!==renderId)return;
   }catch(error){
    if(signal.aborted||id!==renderId)return;
    throw error;
   }
   controller=activeController();
   if(platformRoute&&!controller){
    frame(empty('当前无法查看平台资料','当前入口未提供可用的平台资料功能。','<a class="button" href="#activity">返回工作台</a>'));
    return;
   }
  }
  if(activityIntake&&current.route==='reviews'&&!intakeReviewAccess.list){
   frame(empty('当前无法查看审核待办','审核入口需要当前会话授权和服务端支持。','<a class="button" href="#activity">返回工作台</a>'));
   return;
  }
  const [kind,entity]=current.route.split('/');if(controller){await controller.load(current.route,current.params,signal);if(!signal.aborted&&id===renderId){
   const target=controller===reviews&&current.params.section==='process'?(root.querySelector('.b-manual-attention')??root.querySelector('#review-process')):null;
   if(target){target.tabIndex=-1;target.focus({preventScroll:true});target.scrollIntoView({block:'start'})}
   else {root.querySelector('h1')?.focus({preventScroll:true});window.scrollTo(0,positions.get(current.hash)??0)}
  }return}
  if(!['activity','brand','product'].includes(kind)||(kind==='product'&&entity)){frame(empty('未找到此页面','请选择当前授权的工作入口。','<a class="button" href="#activity">返回活动工作台</a>'));return}
  const incoming=await adapter.read({route:current.route,filter:current.params,signal});if(signal.aborted||id!==renderId)return;
  if(incoming.context.mode!=='hosted'||incoming.context.authenticated!==true||typeof incoming.context.readOnly!=='boolean')throw new Error('工作台授权范围未确认，不展示业务资料。');data=incoming;
  await prepareActivityCapabilities(incoming.capabilities,signal);
  if(signal.aborted||id!==renderId)return;
  if(activityIntake){
   if(kind==='product'){
    incoming.productExportReason=incoming.products.length===0?'当前查询没有可下载的商品。':
     !permitted('operations.download')?'当前账号没有导出权限。':null;
   }
   incoming.dateEvidenceHtml=activity=>dateEvidenceHtml(activity,incoming.authorizedSource?.version?.materials??[]);
   incoming.supplementDates=activity=>supplementDatesView(activity,incoming.dateSupplementBase,incoming.dateSupplement);
   if(kind==='brand'||kind==='activity'&&!entity){
    const dates=await readBrandSupplementDates(adapter,incoming.activities,signal);
    if(signal.aborted||id!==renderId)return;
    incoming.supplementDates=activity=>dates.get(activity.id)??null;
   }
  }
  if(platformModule)incoming.platformMaterialsView=platformModule.createPlatformMaterialsView(platformModule.platformMaterialsCapability(incoming.capabilities));
  if(kind==='activity'&&entity){
   const activity=data.activities.find(a=>a.id===decodeURIComponent(entity));
   const choices=activity?.authorizedVersions??[];
   const chosen=choices.find(v=>v.versionId===current.params.sourceVersion)??choices.find(v=>v.versionId===activity?.displayVersionId)??choices[0];
   const reviewId=activity?.reviewVersionId??activity?.currentReviewVersionId;
   const currentReview=choices.find(v=>v.versionId===reviewId);
   const readVersion=async choice=>{
    if(!choice)return null;
    try{
     const env=await adapter.request('/activities/'+encodeURIComponent(activity.id)+'/versions/'+encodeURIComponent(choice.versionId),{signal});
     if(env.data.activityId!==activity.id||env.data.id!==choice.versionId)return null;
     return {version:env.data};
    }catch(e){
     if(e.status===401)throw e;
     return {message:e.status===403||e.status===404?'该版本来源当前不可读，请核对权限或选择获准版本。':'该版本来源暂不可用，可保留当前活动页面稍后重新读取。'};
    }
   };
   const source=readVersion(chosen);
   const active=currentReview?.versionId===chosen?.versionId?source:readVersion(currentReview);
   const displayId=activity?.displayVersionId??activity?.currentReviewVersionId;
   const displayed=choices.find(v=>v.versionId===displayId);
   const summary=displayed?.versionId===chosen?.versionId?source:
    displayed?.versionId===currentReview?.versionId?active:readVersion(displayed);
   const [sourceResult,reviewResult,summaryResult]=await Promise.all([source,active,summary]);
   if(signal.aborted||id!==renderId)return;
   data.authorizedSource=sourceResult;
   data.productRecognition=sourceResult?.version?.productRecognition??null;
   data.subsidyPrices=sourceResult?.version?.snapshot?.subsidyPrices??[];
   data.registrationDraft=await registrationDraft.load(activity,sourceResult?.version,data.productRecognition,signal);
   if(signal.aborted||id!==renderId)return;
   const supplementChoice=choices.find(choice=>choice.versionId===summaryResult?.version?.latestSupplement?.versionId);
   if(activityIntake&&supplementChoice){
    const supplementResult=supplementChoice.versionId===chosen?.versionId?sourceResult:await readVersion(supplementChoice);
    if(signal.aborted||id!==renderId)return;
    data.dateSupplementBase=summaryResult?.version;
    data.dateSupplement=supplementResult?.version;
   }
   data.activitySubmitters=originalSubmitterNames(summaryResult?.version);
   data.authorizedReview=activityIntake?reviewResult?.version:sourceResult?.version;
   const couponVersion=current.params.sourceVersion
    ? (sourceResult?.version?.id===current.params.sourceVersion?sourceResult.version:null)
    : data.authorizedReview??sourceResult?.version;
   if(activity){
    data.couponReviewControlsHtml=workbenchCouponTable(activity,couponVersion);
    if(couponVersion?.id===data.authorizedReview?.id)data.reviewRequirementsHtml=reviewRequirementsHtml(couponVersion,session);
   }

   if(activityIntake&&activity){
    const loaded=[sourceResult?.version,reviewResult?.version].filter(Boolean);
    const results=await Promise.all(choices.map(async choice=>{
     const version=loaded.find(item=>item.id===choice.versionId);
     let events=version?.history;
     if(!Array.isArray(events)){
      try{
       const path='/activities/'+encodeURIComponent(activity.id)+'/versions/'+encodeURIComponent(choice.versionId)+'/history';
       events=(await adapter.all(path,{},signal)).items;
      }catch(e){
       if(e.status===401)throw e;
       return null;
      }
     }
     if(!events.every(event=>event.activityId===activity.id&&event.versionId===choice.versionId))return null;
     return events.map(event=>({...event,versionNo:choice.versionNo}));
    }));
    if(signal.aborted||id!==renderId)return;
    const complete=results.filter(Boolean);
    const events=complete.flat().sort((a,b)=>String(b.at??'').localeCompare(String(a.at??'')));
    data.activityHistoryHtml=activityHistoryHtml(activity,events,{available:complete.length>0,partial:complete.length<choices.length});
   }
  }
  const expanded=expansions.get(current.hash)??new Set();expansions.set(current.hash,expanded);
  if(kind==='activity'&&entity){const a=data.activities.find(x=>x.id===decodeURIComponent(entity));frame(a?activityDetail(data,a,current.params.back?viewHash(current.params.back):'#demo/activity',false,current.hash):empty('没有权限或未找到活动'),{workbenchView:a?'detail':null})}
  else if(kind==='brand'&&entity){const b=data.brands.find(x=>x.id===decodeURIComponent(entity));frame(b?brandDetail(data,b,current.params.back?viewHash(current.params.back):'#demo/brand'):empty('没有权限或未找到品牌'),{workbenchView:b?'detail':null})}
  else frame(workbench(data,kind,current.params,'demo/'+current.route,viewHash(current.hash),expanded),{workbenchView:'list'});
  registrationDraft.mount();
  productSales.mount(signal);
  window.scrollTo(0,positions.get(current.hash)??0);root.querySelector('h1')?.focus({preventScroll:true});
  if(current.params.section==='coupons')(root.querySelector('.b-manual-attention')??root.querySelector('#review-coupon-selection'))?.scrollIntoView({block:'start'});
 }catch(error){if(signal.aborted||id!==renderId||error.name==='AbortError')return;if(error.status===401){clearProtected();adapter.clear();loginPage(errorMessage(error));return}if(error.status===403||error.status===404){
  const back=current.route.startsWith('review/')?activityReviewReturn(current.params.back,current.route.split('/')[1]):null;
  frame(empty(error.status===403?'当前账号没有访问权限':'未找到或无权查看此记录',error.message,
   link(back?.href??'#activity',back?.label??'返回工作台','button')));
  return
 }frame(empty('暂时无法读取页面',error.message??'请核对服务状态','<button data-test-action="retry">保留当前范围并重新读取</button>'))}
}
async function performLogin(form){
 if(loginBusy||logoutBusy)return;const user=form.elements.username,password=form.querySelector('[data-test-password]');usernameCache=user.value;let invalid=null;
 for(const [input,id,message]of [[user,'username-error','请输入账号。'],[password,'password-error','请输入密码。']]){const missing=!input.value;root.querySelector('#'+id).textContent=missing?message:'';input.setAttribute('aria-invalid',String(missing));if(missing&&!invalid)invalid=input}if(invalid){invalid.focus();return}
 const username=user.value,secret=password.value;const attempt=++loginAttempt,epoch=authEpoch;loginController=new AbortController();busyLogin(true);setLoginMessage('正在登录，仅在服务确认后进入工作台。');
 try{
  const pre=await adapter.session({signal:loginController.signal});
  if(attempt!==loginAttempt||epoch!==authEpoch)return;
  if(!pre.authenticated&&pre.authMethod!=='password'){clearPassword();busyLogin(false);await render();return}
  const value=pre.authenticated?pre:await adapter.login(username,secret,{signal:loginController.signal});
  if(attempt!==loginAttempt||epoch!==authEpoch)return;if(value.authenticated!==true)throw new Error('服务尚未确认登录。');clearPassword();clearTimeout(noticeTimer);notice.textContent='';notice.classList.remove('visible');session=value;busyLogin(false);await render();
 }catch(error){if(attempt!==loginAttempt||epoch!==authEpoch)return;clearPassword();
  if(error.code==='TIMEOUT'||(!error.code&&error.name!=='AbortError')){setLoginMessage('登录结果尚未确认，正在读取当前会话…');try{const latest=await adapter.session();if(attempt!==loginAttempt)return;if(latest.authenticated){session=latest;busyLogin(false);await render();return}}catch{}if(attempt===loginAttempt)setLoginMessage('无法确认登录结果，请重新核对会话；未自动重试密码提交。')}
  else setLoginMessage(errorMessage(error));
 }finally{if(attempt===loginAttempt){clearPassword();busyLogin(false);loginController=null}}
}
async function cancelLogin(){if(logoutBusy||recoveryBusy)return;usernameCache=root.querySelector('#test-username')?.value??usernameCache;const pending=loginBusy;++loginAttempt;loginController?.abort();loginController=null;clearPassword();busyLogin(false);if(!pending){for(const e of root.querySelectorAll('.field-error'))e.textContent='';for(const e of root.querySelectorAll('[aria-invalid]'))e.removeAttribute('aria-invalid');setLoginMessage('已取消登录，密码已清空。');return}
 recoveryBusy=true;clearProtected();loginPage('已取消提交，正在核对是否建立会话…');busyLogin(true);try{const s=await adapter.session();if(s.authenticated)await adapter.logout();adapter.clear();loginPage('已取消登录，当前没有可用的会话。')}catch{adapter.clear();loginPage('请求已取消，尚不能确认服务端会话结果，请核对或重试退出。',{retry:true,pendingLogout:true})}finally{recoveryBusy=false;busyLogin(false)}
}
async function performAnonymousLogin(){
 if(loginBusy||logoutBusy||recoveryBusy)return;
 const attempt=++loginAttempt,epoch=authEpoch;loginController=new AbortController();busyLogin(true);setLoginMessage('正在进入匿名查看模式…');
 try{
  const pre=await adapter.session({signal:loginController.signal});
  if(attempt!==loginAttempt||epoch!==authEpoch)return;
  const value=pre.authenticated?pre:await adapter.anonymousLogin({signal:loginController.signal});
  if(attempt!==loginAttempt||epoch!==authEpoch)return;
  if(value.authenticated!==true)throw new Error('服务尚未确认匿名查看会话。');
  clearPassword();clearTimeout(noticeTimer);notice.textContent='';notice.classList.remove('visible');
  session=value;busyLogin(false);
  if(!['activity','brand','product'].includes(current.route.split('/')[0]))navigate('#activity');
  else await render();
 }catch(error){
  if(attempt!==loginAttempt||epoch!==authEpoch)return;
  setLoginMessage(errorMessage(error));
 }finally{if(attempt===loginAttempt){busyLogin(false);loginController=null}}
}
async function logout(){if(logoutBusy||recoveryBusy)return;
 const wasAnonymous=session?.anonymous===true;
 usernameCache='';
 logoutBusy=true;++loginAttempt;loginController?.abort();clearPassword();clearProtected();loginPage(wasAnonymous?'正在切换至登录页…':'正在退出登录，受保护资料已隐藏。');busyLogin(true);
 try{await adapter.logout();adapter.clear();loginPage(wasAnonymous?'已退出匿名查看，请使用已开通账号手机号登录。':'已退出登录。');notify(wasAnonymous?'已退出匿名查看模式。':'当前会话已由服务撤销。')}
 catch(error){try{const value=await adapter.session();if(!value.authenticated){adapter.clear();loginPage('已确认当前没有有效会话。');return}}catch{}loginPage('尚不能确认退出结果，受保护资料已隐藏，请重试退出。',{pendingLogout:true,retry:true})}
 finally{logoutBusy=false;busyLogin(false)}
}
function attachDownload(){const host=root.querySelector('#download-result');if(!host||!recentDownload||recentDownload.hash!==current.hash||!session?.authenticated)return;const task=recentDownload,a=document.createElement('a');a.href=task.url;a.download=task.filename;a.textContent=`重新下载 ${task.filename}（${task.rowCount}行）`;a.className='button';a.addEventListener('click',async event=>{event.preventDefault();await download(null,task.activityId,task.filters)});host.replaceChildren(document.createTextNode('已生成XLSX；若下载被阻止，可重新核验权限后下载：'),a)}
async function download(button,activityId=null,frozenFilters=null){
 if(exportController){notify('正在生成文件，请等待。');return}if(!session?.authenticated||!session.permissions.includes('operations.download')){notify('当前没有下载权限。');return}
 const epoch=authEpoch,scopeHash=current.hash,filters=structuredClone(frozenFilters??current.params),label=button?.textContent;exportController=new AbortController();const controller=exportController;
 if(button){button.disabled=true;button.setAttribute('aria-busy','true');button.textContent='正在生成…'}
 try{const result=await makeDownload(adapter,filters,activityId,controller.signal);if(epoch!==authEpoch||!session?.authenticated)return;revokeDownload();recentDownload={...result,url:URL.createObjectURL(result.blob),hash:scopeHash,filters,activityId};const a=document.createElement('a');a.href=recentDownload.url;a.download=result.filename;a.click();attachDownload();notify(`已生成 Excel，共 ${result.rowCount} 行。`)}
 catch(error){if(epoch!==authEpoch||error.name==='AbortError')return;if(error.status===401||error.status===403){clearProtected();adapter.clear();loginPage(errorMessage(error));return}notify('生成失败：'+errorMessage(error)+' 可重试。')}
 finally{if(exportController===controller)exportController=null;if(button?.isConnected){button.disabled=false;button.removeAttribute('aria-busy');button.textContent=label}}
}
root.addEventListener('input',event=>{if(event.target.id==='test-username')usernameCache=event.target.value;activeController()?.input?.(event.target)});
root.addEventListener('change',event=>{
 if(event.target.matches('[name="reviewCoupon"]')){updateCouponBulkSelection(event.target);return}
 if(event.target.matches('[data-draft-platform]')){guarded(()=>registrationDraft.change(event.target));return}if(event.target.matches('[data-source-version]')){navigate('#'+current.route+'?'+new URLSearchParams({...current.params,sourceVersion:event.target.value}));return}guarded(()=>activeController()?.change?.(event.target))});
root.addEventListener('dragover',event=>{if(event.target.closest('[data-material-drop]'))event.preventDefault()});
root.addEventListener('drop',event=>{if(event.target.closest('[data-material-drop]')){event.preventDefault();activeController()?.drop?.(event.dataTransfer.files)}});
async function guarded(work){const epoch=authEpoch;try{await work()}catch(e){if(epoch!==authEpoch||e.name==='AbortError')return;if(e.status===401){clearProtected();adapter.clear();loginPage(errorMessage(e))}else notify(e.message??'操作失败，请核对后重试。')}}
async function openWorkbenchReview(button){
 const activity=data?.activities.find(item=>item.id===(button.dataset.couponReviewSubmit??button.dataset.activityReview));
 if(button.disabled||button.closest('[aria-busy="true"]')||root.querySelector('#workbench-review')||
    !intakeReviewAccess.decide||activity?.reviewAction?.action!=='reviews.decide')return;
 const couponSection=root.querySelector('#review-coupon-selection');
 if(activity.reviewAction.requiresCouponReview&&!button.dataset.couponReviewSubmit&&!(couponSection&&data.reviewRequirementsHtml)){
  if(couponSection?.querySelector('[data-coupon-review-submit]'))(root.querySelector('.b-manual-attention')??couponSection).scrollIntoView({block:'start',behavior:'smooth'});
  else navigate(routeLink('activity/'+encodeURIComponent(activity.id),{back:current.hash,section:'coupons'}));
  return;
 }
 if(couponSection?.getAttribute('aria-busy')==='true')return;
 if(button.dataset.couponReviewSubmit && (couponSection?.dataset.versionId!==activity.reviewAction.versionId ||
    couponSection?.dataset.versionId!==data.authorizedReview?.id))return;
 const signal=renderController.signal;
 let version=data.authorizedReview;
 if(version?.id!==activity.reviewAction.versionId||version.activityId!==activity.id){
  button.disabled=true;
  try{
   const path='/activities/'+encodeURIComponent(activity.id)+'/versions/'+encodeURIComponent(activity.reviewAction.versionId);
   version=(await adapter.request(path,{signal})).data;
  }finally{if(button.isConnected)button.disabled=false}
 }
 if(signal.aborted||root.querySelector('#workbench-review'))return;
 if(version?.id!==activity.reviewAction.versionId||version.activityId!==activity.id||version.review?.stage!==activity.review?.stage||!canDecideVersion(version))
  throw new Error('当前审核节点或权限已变化，请刷新后核对。');
 const requirements=reviewRequirementsHtml(version,session);
 const selection=readCouponSelection(couponSection);
 const couponReview=version.couponReview;
 const selectionRevision=couponReview?.draftRevision??couponReview?.selection?.revision??0;
 const dialog=document.createElement('dialog');
 dialog.id='workbench-review';
 dialog.className='b-workbench-review';
 dialog.setAttribute('aria-labelledby','workbench-review-title');
 dialog.innerHTML=`<h2 id="workbench-review-title">审核当前节点</h2><p>${h(activity.name)}</p>${requirements}
  <form><fieldset><label>审核备注（通过时选填，打回时填写原因）<textarea name="reason" maxlength="2000" rows="3"></textarea></label>
   <div class="actions"><button type="button" data-review-cancel autofocus>取消</button>
   <button type="submit" value="reject">打回</button><button type="submit" class="primary" value="approve"${requirements?' disabled':''}>通过</button></div>
  </fieldset><p class="warning" role="alert" hidden></p></form>`;
 const form=dialog.querySelector('form');
 const fieldset=form.querySelector('fieldset');
 const reason=form.elements.namedItem('reason');
 const feedback=form.querySelector('[role="alert"]');
 const showError=message=>{feedback.textContent=message;feedback.hidden=false};
 dialog.querySelector('[data-review-cancel]').addEventListener('click',()=>dialog.close());
 dialog.addEventListener('cancel',event=>{if(fieldset.disabled)event.preventDefault()});
 dialog.addEventListener('close',()=>{dialog.remove();if(button.isConnected)button.focus({preventScroll:true})},{once:true});
 form.addEventListener('submit',event=>{
  event.preventDefault();event.stopPropagation();
  const decision=event.submitter?.value;
  if(fieldset.disabled||signal.aborted||!['approve','reject'].includes(decision))return;
  const text=reason.value.trim();
  if(decision==='reject'&&!text){showError('请填写打回原因。');reason.focus();return}
  feedback.hidden=true;fieldset.disabled=true;
  guarded(async()=>{
   try{
    const {stageLabel}=await decideWorkbenchReview(adapter,commands,activity,decision,text,signal,selection,selectionRevision);
    if(signal.aborted)return;
    dialog.close();
    notify(stageLabel+(decision==='approve'?'已通过。':'已打回。'));
    await render();
   }catch(error){
    if(signal.aborted)return;
    if(error.status===401)throw error;
    if(['STATE_CONFLICT','COMMAND_UNKNOWN'].includes(error.code)){
     dialog.close();await render();throw error;
    }
    const selectionPrompt=isWorkbenchList(current.route)?
     '请关闭后点「查看」，进入商品明细勾选拟报名商品，再审核。':'请在「商品明细」勾选拟报名商品，再审核。';
    showError(error.code==='REGISTRATION_SELECTION_REQUIRED'?selectionPrompt:error.message??'审核未提交，请重试。');
   }finally{fieldset.disabled=false}
  });
 });
 root.append(dialog);
 dialog.showModal();
}
root.addEventListener('submit',event=>{event.preventDefault();if(event.target.id==='test-login-form')performLogin(event.target);else if(session?.authenticated&&event.target.id==='product-sales-history')guarded(()=>productSales.history(event.target,renderController?.signal));else if(session?.authenticated&&activeController())guarded(()=>activeController().submit(event.target));if(event.target.id==='filters'){const filters=Object.fromEntries(new FormData(event.target));filters.page='1';const target=routeLink(current.route,filters);if(target===location.hash)render();else location.hash=target}});
root.addEventListener('click',async event=>{
 const testButton=event.target.closest('[data-test-action]');if(testButton){const action=testButton.dataset.testAction;
  if(action==='toggle-password'){const input=root.querySelector('[data-test-password]');const show=input.type==='password';input.type=show?'text':'password';testButton.textContent=show?'隐藏':'显示';testButton.setAttribute('aria-pressed',String(show))}
  if(action==='profile'){showProfile();return}if(action==='profile-close'){root.querySelector('#test-profile-dialog')?.remove();root.querySelector('[data-test-action=profile]')?.focus();return}if(action==='anonymous-login'){await performAnonymousLogin();return}if(action==='cancel-login')await cancelLogin();if(action==='logout')await logout();if(action==='retry')render();return;
 }
 if(!session?.authenticated||logoutBusy)return;
 const saveCoupon=event.target.closest('#review-coupon-selection [data-review-coupon-save]');
 if(saveCoupon){
  const section=saveCoupon.closest('#review-coupon-selection'),group=saveCoupon.closest('.b-review-coupons');
  const version=data?.authorizedReview,signal=renderController?.signal;
  if(!group||!version||section.dataset.versionId!==version.id||!canDecideVersion(version)||
     !signal||signal.aborted||saveCoupon.disabled||section.hasAttribute('aria-busy'))return;
  const selected=readCouponSelection(group);
  const enabled=[...section.querySelectorAll('button:not(:disabled),input:not(:disabled)')];
  section.setAttribute('aria-busy','true');enabled.forEach(control=>{control.disabled=true});
  await guarded(async()=>{
   try{
    await saveCouponSelection(commands,version,selected,signal);
    if(!signal.aborted){await render();notify('券档勾选已保存。')}
   }catch(error){
    if(error.status===409&&!signal.aborted){await render();notify('券档状态已变化，已重新加载，请核对后保存。')}
    else throw error;
   }finally{
    if(section.isConnected){section.removeAttribute('aria-busy');enabled.forEach(control=>{control.disabled=false})}
   }
  });
  return;
 }
 if(event.target.closest('[data-review-coupon-all]')){updateCouponBulkSelection(event.target);return}
 if(event.target.closest('[data-product-sort],[data-product-compare],[data-product-compare-close],[data-product-page],[data-row-decision],[data-row-retry],[data-product-enroll],[data-draft-reload],[data-legacy-retry],[data-final-download],[data-final-scope-save]')){await guarded(()=>registrationDraft.click(event.target));return;}
 const rate=event.target.closest('[data-activity-rate]');
 if(rate){if(data&&!rate.closest('[aria-busy="true"]'))await guarded(()=>rating.open(rate.dataset.activityRate,rate));return}
 const review=event.target.closest('[data-activity-review],[data-coupon-review-submit]');
 if(review){await guarded(()=>openWorkbenchReview(review));return}
 const resolve=event.target.closest('[data-command-resolve]'),retry=event.target.closest('[data-command-retry]');if(resolve||retry){await guarded(async()=>{const slot=(resolve??retry).dataset[resolve?'commandResolve':'commandRetry'];const original=commands.get(slot);let result;if(resolve||['submissions.delete','platform_materials.submit','reviews.start'].includes(original?.action))result=(await commands.resolve(slot)).result;if(retry&&!result)result=await commands.retry(slot);if(!result){notify('服务暂无此回执；请保留原请求，必要时以原键和正文重试。');return}if(original?.action==='submissions.delete'){notify('已核对删除回执，请查看最新提报列表。');navigate('#submissions');return}notify('已核对服务实际受理结果。');if(['submissions.create','submissions.update'].includes(original?.action)&&result.data?.submissionId&&Number.isInteger(result.data.revision))commands.rememberIntake(slot,{phase:'received',submissionSlot:slot,submissionKey:original.key,submissionId:result.data.submissionId,revision:result.data.revision,analysisSlot:'analysis:'+result.data.submissionId+':'+result.data.revision+':0'});if(result.data?.submissionId)navigate('#submissions/'+result.data.submissionId);else await render()});return}
 const closePreview=event.target.closest('[data-material-preview-close]');if(closePreview){closePreview.closest('dialog')?.remove();return}
 const preview=event.target.closest('[data-material-preview]');if(preview){await guarded(()=>downloadMaterial(preview,true));return}
 const material=event.target.closest('[data-material-download]');if(material){await guarded(()=>downloadMaterial(material));return}
 if(activeController()){await guarded(()=>activeController().click(event.target));return}
 if(!data)return;
 const expand=event.target.closest('[data-expand]');if(expand){saveContext();const set=expansions.get(current.hash)??new Set();set.has(expand.dataset.expand)?set.delete(expand.dataset.expand):set.add(expand.dataset.expand);expansions.set(current.hash,set);frame(workbench(data,'product',current.params,'demo/'+current.route,viewHash(current.hash),set),{workbenchView:'list'});productSales.mount(renderController?.signal);[...root.querySelectorAll('[data-expand]')].find(b=>b.dataset.expand===expand.dataset.expand)?.focus({preventScroll:true});window.scrollTo(0,positions.get(current.hash)??0);return}
 const anchor=event.target.closest('[data-anchor]');
 if(anchor){
  const couponPanel=anchor.dataset.anchor==='process' && root.querySelector('#review-coupon-selection [data-coupon-review-submit]')?.closest('#review-coupon-selection');
  (couponPanel||root.querySelector('#'+anchor.dataset.anchor))?.scrollIntoView({behavior:'smooth',block:'start'});
  return;
 }
 const button=event.target.closest('[data-action]');if(!button)return;
 if(button.dataset.action==='reset'){const target=routeLink(current.route);if(target===location.hash)render();else location.hash=target}
 if(button.dataset.action==='export')await download(button);if(button.dataset.action==='export-activity')await download(button,decodeURIComponent(current.route.split('/')[1]));
 if(button.dataset.action==='all-products'){const box=root.querySelector('#detail-products');if(!box)return;box.classList.toggle('all-rows');button.textContent=box.classList.contains('all-rows')?'恢复5行滚动':'查看全部'}
});
document.querySelector('.skip').addEventListener('click',e=>{e.preventDefault();root.querySelector('#main')?.focus()});
window.addEventListener('hashchange',()=>{clearPassword();render()});
window.addEventListener('pagehide',()=>{phoneLoginCleanup?.();clearPassword();clearProtected();adapter.clear();root.replaceChildren()});
window.addEventListener('pageshow',event=>{if(event.persisted)render()});
function hasUnsavedCouponChoices(){
 const choices=root.querySelectorAll('#review-coupon-selection input[name="reviewCoupon"]:not(:disabled)');
 return Array.from(choices).some(input=>input.checked!==input.defaultChecked);
}
document.addEventListener('visibilitychange',()=>{if(!document.hidden&&!loginBusy&&!logoutBusy){
 if(phoneLoginCleanup){
  const currentLogin=phoneLoginCleanup;
  adapter.session().then(state=>{
   if(phoneLoginCleanup===currentLogin&&!loginBusy&&(state.authenticated||state.authMethod!==authMethod||smsStatus!==(state.sms?.status??null)))render();
  }).catch(()=>{});
  return;
 }
 if(schedule&&activeController()===schedule){schedule.verify();return}if(!activeController()&&!registrationDraft.hasChanges()&&!hasUnsavedCouponChoices()&&!root.querySelector('#workbench-review[open]'))render();else guarded(async()=>{const epoch=authEpoch,value=await adapter.session();if(epoch!==authEpoch)return;if(identityChanged(value)){clearProtected();await render();return}if(!value.authenticated){clearProtected();adapter.clear();loginPage(reasonMessage(value.reason))}else{session=value;expiryCheck()}})}});
async function downloadMaterial(button,preview=false){if(button.disabled)return;const epoch=authEpoch;button.disabled=true;try{const materialId=preview?button.dataset.materialPreview:button.dataset.materialDownload;const meta=(await adapter.request('/materials/'+encodeURIComponent(materialId))).data;const result=await adapter.material(materialId);if(epoch!==authEpoch||!session?.authenticated)return;const actual=Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256',result.bytes)),x=>x.toString(16).padStart(2,'0')).join('');if(meta.sha256&&actual!==meta.sha256)throw new Error('原件摘要不匹配，已停止下载。');if(preview){if(!(meta.mediaType?.startsWith('text/')||meta.mediaType?.startsWith('application/vnd.jiatan.')))throw new Error('此原件不支持安全文本查看，请下载核对。');let text=new TextDecoder('utf-8',{fatal:true}).decode(result.bytes);if(meta.mediaType?.endsWith('+json'))try{text=JSON.stringify(JSON.parse(text),null,2)}catch{}const dialog=document.createElement('dialog');dialog.className='b-original-dialog';dialog.setAttribute('aria-label','原始材料文本');dialog.innerHTML=`<div class="section-heading"><h2>${h(meta.filename??materialId)}</h2><button type="button" data-material-preview-close>关闭原文</button></div><p class="hint">原件校验通过</p><p class="hint">原始材料仅作为文本展示，不执行其中的脚本或指令。</p><pre class="b-json">${h(text.slice(0,100000))}</pre>${text.length>100000?'<p class="hint">原文较长，此处展示前100000字，完整内容请下载原件。</p>':''}`;root.append(dialog);dialog.addEventListener('cancel',()=>dialog.remove(),{once:true});dialog.showModal();return}const url=URL.createObjectURL(new Blob([result.bytes],{type:'application/octet-stream'}));materialUrls.add(url);const a=document.createElement('a');a.href=url;a.download=meta.filename??meta.originalName??materialId;a.click();notify('原件已按授权读取并核对摘要，实际保存以浏览器为准。');setTimeout(()=>{URL.revokeObjectURL(url);materialUrls.delete(url)},60000)}finally{if(button.isConnected)button.disabled=false}}

render();
