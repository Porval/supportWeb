import {badge, beijing, errorPanel, h, info, pager, rows, table} from './b-common.js';
import {digestBody} from './b-commands.js';
import {activityDate,datePeriodText} from './dto.js';

const CONTRACT = 'platform-materials/1.0';
const ACTION = 'platform_materials.submit';
const VERIFY_ACTION = 'platform_materials.verify';
const RECHECK_ACTION = 'platform_materials.recheck';
const MATERIAL_ROUTE = /^activity\/([A-Za-z0-9_-]{1,128})\/platform\/([A-Za-z0-9_-]{1,128})\/materials$/;
const WORKFLOW = {
 initial_pending:'待初审', pricing_pending:'复审中', final_pending:'终审中', rejected:'审核不通过',
 pending_materials:'待补平台资料', pending_verification:'待审核上线', returned:'退回待补资料', live:'已上线', unknown:'平台进度待核实',
 blocked_review:'待内部审核通过'
};
const LIFECYCLE = {
 pending_registration:'待报名', pending_check:'待检查', pending_recheck:'待复查', retrospective:'复盘'
};
const PLATFORM_LABELS = {meituan:'美团', taobao_instant:'淘宝闪购'};
const COMPARISON = {complete:'可逐项对照', partial:'部分商品待核对', scope_only:'按范围展示', unavailable:'暂无可比依据'};
const RESULT = {same:'一致', missing_from_platform:'审核有，平台清单未列入', added_on_platform:'平台清单新增', needs_check:'待核对'};
const PRESENCE = {present:'有', absent:'无', unknown:'待核对'};
const CONFLICT = new Set(['ACTIVITY_VERSION_CONFLICT','PLATFORM_VERSION_CONFLICT','REVIEW_BASIS_UNAVAILABLE','EXECUTION_VERSION_UNCONFIRMED','PLATFORM_BASIS_UNAVAILABLE','LIVE_CHANGE_POLICY_UNCONFIRMED','PLATFORM_NOT_PENDING_VERIFICATION','PLATFORM_NOT_PENDING_RECHECK']);
const MEDIA = {csv:'text/csv', xlsx:'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'};
const SCREENSHOT_MEDIA = {png:'image/png',jpg:'image/jpeg',jpeg:'image/jpeg',webp:'image/webp'};
const has = (value, key) => Object.hasOwn(value, key);
const canWrite = value => value?.allowedActions?.includes(ACTION) === true;
const canVerify = value => value?.allowedActions?.includes(VERIFY_ACTION) === true;
const canRecheck = value => value?.allowedActions?.includes(RECHECK_ACTION) === true;
const safeId = id => typeof id==='string' && /^[A-Za-z0-9_-]{1,128}$/.test(id);
const stateLabel = state => has(WORKFLOW,state)?WORKFLOW[state]:WORKFLOW.unknown;
const progressLabel = value => LIFECYCLE[value.lifecycleStage]??stateLabel(value.state);
const platformName = code => has(PLATFORM_LABELS,code)?PLATFORM_LABELS[code]:code;
const visible = value => h(value??'待核实');
const checkCommentLabel = decision => decision==='return'?'退回原因（必填）':'备注（选填）';
const brandNames = brands => (brands??[]).map(b=>b.nameSnapshot??b.id).join('、');
const textID = value => `<span class="pm-id" dir="auto">${visible(value)}</span>`;
const notices = reasons => (reasons??[]).map(r=>`<p class="hint">${h(r.message??r.code)}</p>`).join('');
const panel = (title,body) => `<section class="panel pm-panel"><h2>${h(title)}</h2>${body}</section>`;
const button = (label,action,extra='') => `<button type="button" data-pm-action="${action}" ${extra}>${h(label)}</button>`;
const htmlLink = (label,href,style='button') => `<a class="${style}" href="${h(href)}">${h(label)}</a>`;
const routeURL = (route,params={}) => '#'+route+(Object.keys(params).length?'?'+new URLSearchParams(params):'');
const validBack = value => /^#(?:demo\/)?activity(?:\?|$)/.test(value??'')?value.replace('#demo/','#'):null;
const progress = (a,caps) => (a.platformProgress??[]).filter(p=>safeId(p.platformCode)&&caps.platformCodes.includes(p.platformCode));

export const parsePlatformMaterialsRoute = route => MATERIAL_ROUTE.exec(route);
export function platformMaterialsCapability(caps){
 const value=caps?.platformMaterials;
 return value?.contractVersion===CONTRACT&&value.enabled===true&&Array.isArray(value.platformCodes)?value:null;
}
function materialsURL(a,p,back){
 return routeURL(`activity/${encodeURIComponent(a.id)}/platform/${p.platformCode}/materials`,back?{back}:{});
}
export function createPlatformMaterialsView(caps){
 if(!caps)return null;
 const filters=Object.entries(caps.lifecycleEnabled===true?LIFECYCLE:{
  pending_materials:'待补平台资料',pending_verification:'待核实上线',returned:'资料已退回',live:'已上线'
 }).map(([id,name])=>({id,name}));
 return {
  filters,
  state(a){return LIFECYCLE[a.lifecycleStage]??(has(WORKFLOW,a.workflowStatus)&&a.workflowStatus!=='blocked_review'?stateLabel(a.workflowStatus):null)},
  action(a,back){
   const items=progress(a,caps);
   const actionable=p=>canWrite(p)||canVerify(p)||canRecheck(p);
   const eligible=items.find(p=>p.lifecycleStage===a.lifecycleStage&&actionable(p))??items.find(actionable)??
    items.find(p=>p.lifecycleStage==='retrospective'&&a.lifecycleStage==='retrospective');
   return eligible?htmlLink(platformAction(eligible),materialsURL(a,eligible,back),'button small'):'';
  },
  detail(a,back,{embedded=false}={}){
   const items=progress(a,caps);
   if(!items.length)return '';
   const content=items.map(p=>{
    return `<div class="pm-platform"><h3>${h(platformName(p.platformCode))}</h3><p>${badge(progressLabel(p),p.lifecycleStage==='retrospective'||p.state==='live'&&!p.lifecycleStage?'green':'amber')}</p>${p.responsibleOperator?`<p>运营负责人：${visible(p.responsibleOperator.nameSnapshot??p.responsibleOperator.id)}</p>`:'<p class="hint">运营负责人尚未分配</p>'}${p.returnReason?`<p class="warning">退回原因：${h(p.returnReason)}</p>`:''}${notices(p.blockedReasons)}${htmlLink(platformAction(p),materialsURL(a,p,back))}</div>`;
   }).join('');
   return embedded?content:panel('平台资料',content);
  }
 };
}
function platformAction(p){
 if(canRecheck(p))return '复查';
 if(canVerify(p))return p.lifecycleStage?'检查':'审核上线';
 if(canWrite(p))return p.state==='returned'?'补齐平台资料':p.lifecycleStage?'提交报名资料':'补录平台资料';
 return p.lifecycleStage==='retrospective'?'查看复盘资料':'查看平台进度';
}
function materialScope(m){
 const brands=brandNames(m.brands);
 if(m.scopeSource==='approved_version')return '按终审确认的报名范围';
 if(m.scopeMode==='all_brand_products')return brands?`覆盖${h(brands)}全部商品`:'全品牌商品范围，品牌待核实';
 return m.scopeMode==='list'?`按实际商品清单${brands?' · '+h(brands):''}`:'商品范围待核实';
}
function activityInfo(activity, platform){
 const start=activityDate(activity.startAt), end=activityDate(activity.endAt);
 const material=platform.currentMaterial;
 const fields=[['提报活动时间（北京时间）',h(datePeriodText(start,end,activity.activityDateEvidence))]];
 if(material){
  fields.push(['平台活动 ID',textID(material.platformActivityId)],['商品范围',materialScope(material)]);
 }
 if(platform.lifecycleStage==='retrospective'){
  const dayMs=86400000;
  const startMs=Date.parse(start), endMs=Date.parse(end);
  if(Number.isFinite(startMs)&&Number.isFinite(endMs)&&endMs>=startMs){
   const days=(endMs-startMs)/dayMs+1;
   const day=ms=>new Date(ms).toISOString().slice(0,10);
   fields.push(['完整活动的等长对照期（按提报时间）',h(`${day(startMs-days*dayMs)} 至 ${day(startMs-dayMs)} · ${days} 天`)]);
  }
  fields.push(['平台实际生效时间','待核对']);
 }
 return info(fields);
}
function salesComparisonHtml(result){
 if(!result)return '<p class="hint">按提报时间试算，范围为终审报名商品在当前平台的销量和销售额。活动未结束时，算到昨天。</p>';
 const periods=result.periods??{};
 const dates=p=>p?`${h(p.startDate)} 至 ${h(p.endDate)}`:'待核实';
 const unit=(v,suffix)=>v==null?'—':h(v)+suffix;
 const metric=(m,u)=>[unit(m?.current,u),unit(m?.previous,u),unit(m?.delta,u),unit(m?.changePct,'%')];
 const body=result.rows.map(row=>rows([visible(row.name??row.entityId),...metric(row.salesQty,' 件'),...metric(row.salesRevenue,' 元')]));
 const heading=['终审报名商品','本期销量','上期销量','销量增减','销量环比','本期销售额','上期销售额','销售额增减','销售额环比'];
 const messages=(result.warnings??[]).map(w=>w.code==='SOURCE_NOT_CONFIGURED'?'数据 Agent 暂未接入':w.message);
 if(result.status==='partial')messages.unshift('部分日期或商品缺数据，空值不按 0。');
 if(result.rows.some(r=>r.salesQty?.status==='zero_base'||r.salesRevenue?.status==='zero_base'))messages.push('上期为 0 时，只显示增减量，不算环比。');
 return `<p>终审报名商品：${h(result.registrationProductCount)} 款 · 按提报时间试算</p>
  <p>本期：${dates(periods.current)}；上期：${dates(periods.previous)}</p>
  ${messages.map(m=>`<p class="hint">${h(m)}</p>`).join('')}
  ${body.length?table(heading,body,'暂无可比较数据'):''}
  ${result.source?.updatedAt?`<p class="hint">来源：商品日报 · 更新于 ${h(beijing(result.source.updatedAt))}</p>`:''}`;
}
function verificationInfo(v){
 if(!v)return '<p class="hint">待管理员核实。</p>';
 return info([
  ['审核结果',v.decision==='approve'?'已确认上线':'退回待补资料'],
  ['审核人',visible(v.reviewer?.nameSnapshot??v.reviewer?.id)],['审核时间（北京时间）',visible(beijing(v.reviewedAt))],
  ['平台实际状态',visible({live:'已上线',not_live:'未上线',unknown:'无法核实'}[v.platformStatus])],
  [v.screenshot?'检查截图':'核实来源',v.screenshot?screenshotHtml(v):visible(v.evidenceSource||'未提供')],
  ['审核说明',visible(v.reason||'无补充说明')]
 ]);
}
function verificationHistory(v,recheck=false){
 if(!v)return '<p class="hint">待管理员核实。</p>';
 const result=recheck?(v.decision==='approve'?'复查通过':'复查发现问题'):(v.decision==='approve'?'已确认上线':'退回待补资料');
 return `<div class="pm-history-verification"><strong>${result}</strong>
 <small>${visible(v.reviewer?.nameSnapshot??v.reviewer?.id)} · ${visible(beijing(v.reviewedAt))}</small>
 ${v.reason?`<p>${h(v.reason)}</p>`:''}<details><summary>查看核实依据</summary><p>${v.screenshot?screenshotHtml(v):h(v.evidenceSource||'未提供')}</p><p>平台状态：${visible({live:'已上线',not_live:'未上线',unknown:'无法核实'}[v.platformStatus])}</p></details></div>`;
}
function screenshotHtml(v){
 return textID(v.screenshot.filename)+(v.screenshot.downloadUrl?
  button('下载检查截图','download-screenshot',`data-pm-id="${h(v.platformMaterialId)}"`):'');
}
function materialInfo(m){
 return info([
  ['审核版本',visible(m.approvedVersionId)], ['资料修订',visible(m.revision)],
  ['提交人',visible(m.submitter?.nameSnapshot??m.submitter?.id)], ['提交时间（北京时间）',visible(beijing(m.submittedAt))],
  ['原件',m.file?`${textID(m.file.filename)} ${m.file.downloadUrl?button('下载原件','download',`data-pm-id="${h(m.platformMaterialId)}"`):'（当前无下载授权）'}`:m.scopeSource==='approved_version'?'使用终审报名范围':'无附件']
 ]);
}
function comparisonHtml(c, material, route, query){
 const status=has(COMPARISON,c.status)?COMPARISON[c.status]:'对照状态待核实';
 const groups={approvedProducts:'审核商品',platformProducts:'平台商品',same:'一致',missingFromPlatform:'平台未列入',addedOnPlatform:'平台新增',unmatchedRows:'待核对行'};
 const counts=c.status==='scope_only'?'':info(Object.entries(groups).map(([key,name])=>[name,visible(c.counts?.[key])]));
 const resultRows=(c.rows?.items??[]).map(row=>rows([
  visible(RESULT[row.result]), [row.productNameSnapshot,row.internalCodeSnapshot,row.productId].filter(Boolean).map(textID).join('<br>')||'待核对',
  visible(PRESENCE[row.approvedPresence]), visible(PRESENCE[row.platformPresence]),
  `<details><summary>查看原值及位置</summary><pre class="b-json">${h(JSON.stringify({原值:row.rawValues,位置:row.locators,核对原因:row.reasonCodes},null,2))}</pre></details>`
 ]));
 const params={...query,compare:material.platformMaterialId};
 delete params.comparisonPage;
 const nav=c.rows?.total>c.rows?.pageSize?'<nav class="pagination" aria-label="商品对照分页">'+
  (c.rows.page>1?htmlLink('上一页',routeURL(route,{...params,comparisonPage:c.rows.page-1})):'')+
  `<span>${c.rows.page} / ${Math.ceil(c.rows.total/c.rows.pageSize)}</span>`+
  (c.rows.page*c.rows.pageSize<c.rows.total?htmlLink('下一页',routeURL(route,{...params,comparisonPage:c.rows.page+1})):'')+'</nav>':'';
 return panel('审核方案与平台资料对照',`<p>${badge(status)} · 绑定审核版本 ${visible(c.basis?.approvedVersionId)} · 资料修订 ${visible(material.revision)}</p>
  ${c.status==='scope_only'?`<p>平台配置范围：${materialScope(material)}。可从活动详情回查绑定审核版本。</p>`:counts}
  ${c.reasonCodes?.length?`<p class="hint">核对依据：${h(c.reasonCodes.join('、'))}</p>`:''}
  ${resultRows.length?table(['对照结果','商品 / 编码','审核方案','平台清单','依据'],resultRows)+nav:''}`);
}
function shaHex(buffer){
 return Array.from(new Uint8Array(buffer),byte=>byte.toString(16).padStart(2,'0')).join('');
}
async function listPayload(file,upload){
 const ext=file.name.split('.').at(-1).toLowerCase();
 if(!has(MEDIA,ext)||!upload?.extensions?.some(value=>value.replace(/^\./,'').toLowerCase()===ext))throw new Error('请选择服务支持的 CSV 或 XLSX 商品清单。');
 if(file.size===0||!Number.isInteger(upload.maxDecodedBytes)||file.size>upload.maxDecodedBytes)throw new Error('请选择非空且符合大小上限的商品清单。');
 return attachmentPayload(file,MEDIA[ext]);
}
const bufferedFiles=new WeakMap();
function readableFileError(error){
 if(error?.name==='NotReadableError'||/could not be read/i.test(error?.message??'')){
  return new Error('浏览器无法读取该本地文件（可能被 Excel/WPS 占用、尚未从微信/网盘下载到本地，或选择后文件已变动）。请先将文件保存到桌面或下载目录，关闭占用程序后重新选择。');
 }
 return error;
}
function snapshotFile(file){
 if(!file)return null;
 const existing=bufferedFiles.get(file);
 if(existing)return existing;
 const promise=file.arrayBuffer().then(buf=>new Uint8Array(buf),error=>{throw readableFileError(error)});
 promise.catch(()=>{});
 bufferedFiles.set(file,promise);
 return promise;
}
async function attachmentPayload(file,mediaType){
 const bytes=await snapshotFile(file);
 let binary='';
 for(let i=0;i<bytes.length;i+=32768)binary+=String.fromCharCode(...bytes.subarray(i,i+32768));
 return {filename:file.name,mediaType,contentBase64:btoa(binary),sha256:shaHex(await crypto.subtle.digest('SHA-256',bytes))};
}
function newDraft(){return {id:'',scope:'list',file:null}}
function newVerificationDraft(){return {decision:'approve',platformStatus:'unknown',platformActivityIdConfirmed:false,productScopeConfirmed:false,evidenceSource:'',reason:''}}
const pageNumber = value => /^[1-9]\d{0,5}$/.test(value??'')?Number(value):1;
const countLimit = value => /^[0-9]\d{0,8}$/.test(value??'')?Number(value):null;
function validation(message,field){
 return Object.assign(new Error(message),{details:{field}});
}

export function createPlatformMaterialsController(ctx){
 let signal,route='',params={},caps=null,activity=null,state=null,history=null,historyError=null,comparison=null,comparisonError=null,salesComparison=null,salesError=null,salesBusy=false;
 let draft=newDraft(),verificationDraft=newVerificationDraft(),busy=false,error=null,receipt=null,readbackRequired=false,edit=false,receiptCheck=null;
 let checkScreenshot=null;
 const live = requestSignal => requestSignal===signal&&!requestSignal?.aborted&&ctx.route()===route;
 const resource = () => `/activities/${state.activityId}/platforms/${state.platformCode}`;
 const writePath = () => resource()+'/materials';
 const verifyPath = () => resource()+'/verification';
 const slot = (action=ACTION) => action+':'+state.activityId+':'+state.platformCode;
 const pending = (action=ACTION) => {const record=ctx.commands.get(slot(action));return record?.state==='unknown'?record:null};
 const anyPending = () => pending()??pending(VERIFY_ACTION)??pending(RECHECK_ACTION);
 const reviewAction = () => pending(VERIFY_ACTION)?.action??pending(RECHECK_ACTION)?.action??
  (canRecheck(state)?RECHECK_ACTION:VERIFY_ACTION);
 const canReview = () => reviewAction()===RECHECK_ACTION?canRecheck(state):canVerify(state);
 const originals = () => [state?.currentMaterial,...(history?.items??[])].filter(Boolean);
 const allBrandAllowed = () => state?.allowedScopeModes?.includes('all_brand_products')===true;
 const canEdit = () => canWrite(state)&&safeId(state.approvedVersionId)&&
  Number.isInteger(state.activityRevision)&&Number.isInteger(state.platformRevision);
 const back = () => routeURL('activity/'+state.activityId,validBack(params.back)?{back:validBack(params.back)}:{});
 const currentCompare = () => originals().find(m=>m.platformMaterialId===params.compare);

 function form(){
  const pendingRecord=pending();
  const disabled=busy||readbackRequired||CONFLICT.has(error?.code);
  const brand=brandNames(state.brands);
  const allAllowed=allBrandAllowed();
  const recover=pendingRecord?`<p class="warning" role="status">有待核对的保存结果。请先核对原回执；若仍未找到，使用原平台活动 ID、范围和同一清单重试。</p>
   ${button('核对原请求结果','check',busy?'disabled':'')}`:'';
  const scope=draft.scope==='all_brand_products'?'all_brand_products':'list';
  const field=error?.details?.field;
  return panel(state.currentMaterial?'重新提交平台资料':'补录平台资料',`${recover}${receiptCheck?`<p role="status">${h(receiptCheck)}</p>`:''}
   ${error?errorPanel(error):''}
   ${(readbackRequired||CONFLICT.has(error?.code))?button('刷新并核对当前版本','refresh',busy?'disabled':''):''}
   <form id="pm-form" novalidate aria-busy="${busy}">
   <fieldset ${disabled?'disabled':''}><legend>实际平台配置</legend>
   <label for="pm-platform-id">平台活动 ID（必填）</label>
   <input id="pm-platform-id" type="text" name="platformActivityId" value="${h(draft.id)}" autocomplete="off" aria-required="true" aria-invalid="${field==='platformActivityId'}" aria-describedby="pm-id-note">
   <p id="pm-id-note" class="hint">填写平台后台的活动 ID，保留前导零及原始字符。</p>
   <fieldset class="design-inline-options"><legend>商品范围</legend>
   <label><input type="radio" name="scopeMode" value="list" ${scope==='list'?'checked':''}> 按终审报名范围</label>
   ${allAllowed?`<label><input type="radio" name="scopeMode" value="all_brand_products" ${scope==='all_brand_products'?'checked':''}> 覆盖${brand?h(brand):'该品牌'}全部商品</label>`:''}
   </fieldset>
   <p class="hint">保存后进入${state.lifecycleStage?'待检查':'待核实上线'}，资料与当前通过版本绑定。</p>
   <button class="primary" type="submit">${busy?'正在保存…':pendingRecord?'以原请求重试':'保存平台资料'}</button>
   </fieldset></form>`);
 }
 function verificationForm(){
  const action=reviewAction(), original=pending(action), value=verificationDraft;
  const recheck=action===RECHECK_ACTION;
  const needsScreenshot=!recheck&&caps.checkScreenshotRequired===true;
  const disabled=busy||!canReview()||readbackRequired||CONFLICT.has(error?.code);
  return panel(recheck?'复查平台活动':state.lifecycleStage?'检查平台活动':'审核平台上线',`${original?`<p class="warning">有待核对的结果，请先核对原请求。</p>${button('核对原请求结果','check',busy?'disabled':'')}`:''}
   ${receiptCheck?`<p role="status">${h(receiptCheck)}</p>`:''}${error?errorPanel(error):''}
   ${(readbackRequired||CONFLICT.has(error?.code))?button('刷新并核对当前版本','refresh',busy?'disabled':''):''}
   <form id="pm-verify-form" novalidate aria-busy="${busy}"><fieldset ${disabled?'disabled':''}><legend>核对实际平台资料</legend>
   <label>结果 <select name="decision" data-pm-verify>
    <option value="approve" ${value.decision==='approve'?'selected':''}>${recheck?'复查通过':'确认上线'}</option>
    <option value="return" ${value.decision==='return'?'selected':''}>${recheck?'记录问题':'退回补齐'}</option></select></label>
   <label>平台实际状态 <select name="platformStatus" data-pm-verify>
    <option value="unknown" ${value.platformStatus==='unknown'?'selected':''}>无法核实</option>
    <option value="not_live" ${value.platformStatus==='not_live'?'selected':''}>未上线</option>
    <option value="live" ${value.platformStatus==='live'?'selected':''}>已上线</option></select></label>
   <label class="pm-check-option"><input type="checkbox" name="platformActivityIdConfirmed" data-pm-verify ${value.platformActivityIdConfirmed?'checked':''}> 已核对平台活动 ID</label>
   ${needsScreenshot?`<label for="pm-check-screenshot">平台活动截图（检查通过时必传）</label>
   <input id="pm-check-screenshot" type="file" accept=".png,.jpg,.jpeg,.webp" aria-describedby="pm-check-screenshot-hint pm-check-screenshot-name">
   <p id="pm-check-screenshot-hint" class="hint">请上传能看清活动 ID 和上线状态的截图。支持 PNG、JPG 或 WebP，最大 5 MB。</p>
   <div id="pm-check-screenshot-selection" ${checkScreenshot?'':'hidden'}>
    <span id="pm-check-screenshot-name">${h(checkScreenshot?.name??'')}</span>
    ${button('移除截图','remove-screenshot')}
   </div>`:`<label>核实来源（通过时必填）<textarea name="evidenceSource" data-pm-verify maxlength="2000">${h(value.evidenceSource)}</textarea></label>`}
   <label><span ${needsScreenshot?'id="pm-check-comment-label"':''}>${needsScreenshot?checkCommentLabel(value.decision):recheck?'复查说明（记录问题时必填）':'检查说明（退回时必填）'}</span>
    <textarea name="reason" data-pm-verify maxlength="2000">${h(value.reason)}</textarea></label>
   <button class="primary" type="submit">${busy?'正在保存…':original?'以原请求重试':recheck?'保存复查结果':'保存检查结果'}</button>
   </fieldset></form>`);
 }
 function historyHtml(){
  if(historyError)return panel('平台资料历史',errorPanel(historyError));
  if(!history)return '';
  const body=history.items.map(m=>rows([visible(m.revision),visible(m.approvedVersionId),textID(m.platformActivityId),materialScope(m),
   visible(m.submitter?.nameSnapshot??m.submitter?.id),visible(beijing(m.submittedAt)),verificationHistory(m.verification)+(m.recheck?verificationHistory(m.recheck,true):''),
   (m.file?.downloadUrl?button('下载原件','download',`data-pm-id="${h(m.platformMaterialId)}"`):'')+
   (m.file?' '+htmlLink('查看对照',routeURL(route,{...params,compare:m.platformMaterialId,comparisonPage:1}),'button small'):'')]));
  const pinned=Number.isInteger(history.throughRevision)?{...params,throughRevision:history.throughRevision}:params;
  return panel('平台资料历史',table(['资料修订','审核版本','平台活动 ID','商品范围','提交人','提交时间（北京）','审核记录','操作'],body,'当前授权范围暂无历史资料。')+
   (history.total>history.pageSize?pager(history.page,history.total,'#'+route,pinned,history.pageSize):''));
 }
 function paint(focus){
  if(!live(signal)||!state)return;
  const current=state.currentMaterial;
  const editButton=current&&canEdit()&&!edit&&!pending()&&!readbackRequired?button('重新提交平台资料','edit'):'';
  const prompt=readbackRequired&&!canEdit()?button('重新读取平台状态','refresh'):'';
  ctx.frame(`<div class="pm-page"><div class="b-heading"><div>${htmlLink('‹ 返回活动详情',back(),'design-back')}
   <h1 tabindex="-1">平台资料 · ${h(platformName(state.platformCode))}</h1><p>${visible(activity.name??activity.activityName)} · ${textID(state.activityId)}</p>
   </div></div>${panel('活动信息',activityInfo(activity,state))}
   ${panel('平台进度',`<p>${badge(progressLabel(state),state.lifecycleStage==='retrospective'||state.state==='live'&&!state.lifecycleStage?'green':'amber')}</p>${notices(state.blockedReasons)}
   <p>运营负责人：${visible(state.responsibleOperator?.nameSnapshot??state.responsibleOperator?.id??'尚未分配')}</p>
   ${state.returnReason?`<p class="warning">退回原因：${h(state.returnReason)}</p>`:''}
   <p class="hint">当前通过版本 ${visible(state.approvedVersionId)}。</p>
   ${receipt?`<p role="status">${h(receipt)}</p>`:''}${prompt}`)}
   ${state.lifecycleStage==='retrospective'?panel('活动销售环比',
     salesComparisonHtml(salesComparison)+(salesError?errorPanel(salesError):'')+
     button(salesBusy?'正在查询…':'按当前平台查询试算','sales-comparison',salesBusy?'disabled':'')):''}
   ${current?panel('当前平台资料',materialInfo(current)+`<div class="actions">${current.file?htmlLink('查看商品对照',routeURL(route,{...params,compare:current.platformMaterialId,comparisonPage:1})):''}${editButton}</div>`):''}
   ${current?.verification||current?.recheck?panel('检查与复查',
     (current.verification?verificationInfo(current.verification):'')+(current.recheck?verificationHistory(current.recheck,true):'')):''}
   ${canEdit()&&(edit||!current||pending()||readbackRequired)?form():''}
   ${canReview()||pending(reviewAction())?verificationForm():''}
   ${!canEdit()&&!canReview()&&!pending(reviewAction())&&error?errorPanel(error):''}
   ${!canEdit()&&pending()?`${button('核对原请求结果','check',busy?'disabled':'')}${receiptCheck?`<p role="status">${h(receiptCheck)}</p>`:''}`:''}
   ${comparison?comparisonHtml(comparison,currentCompare(),route,params):comparisonError?panel('审核方案与平台资料对照',errorPanel(comparisonError)):''}
   ${historyHtml()}</div>`);
  if(focus)document.querySelector(focus)?.focus({preventScroll:true});
 }
 async function readSupplemental(requestSignal){
  history=null;historyError=null;comparison=null;comparisonError=null;
  if(!state.currentMaterial&&!state.platformRevision)return;
  const url=new URLSearchParams({page:pageNumber(params.page),pageSize:20});
  const bound=countLimit(params.throughRevision);
  if(bound!==null)url.set('throughRevision',bound);
  try{
   const response=await ctx.adapter.request(writePath()+'?'+url,{signal:requestSignal});
   if(!live(requestSignal))return;
   history=response.data;
  }catch(e){if(e.status===401)throw e;if(!live(requestSignal))return;historyError=e}
  const material=currentCompare();
  if(material&&safeId(material.platformMaterialId)){
   const query=new URLSearchParams({page:pageNumber(params.comparisonPage),pageSize:20});
   try{
    const response=await ctx.adapter.request(writePath()+'/'+material.platformMaterialId+'/comparison?'+query,{signal:requestSignal});
    if(live(requestSignal))comparison=response.data;
   }catch(e){if(e.status===401)throw e;if(live(requestSignal))comparisonError=e}
  }
 }
 async function readAfterWrite(requestSignal,message,{preserveDraft=false}={}){
  readbackRequired=true;
  receipt=message;
  try{
   const env=await ctx.adapter.request(resource(),{signal:requestSignal});
   if(!live(requestSignal))return;
   state=env.data;
   readbackRequired=false;
   edit=preserveDraft&&canEdit();
   if(!preserveDraft){draft=newDraft();verificationDraft=newVerificationDraft();checkScreenshot=null}
   else if(!allBrandAllowed())draft.scope='list';
   await readSupplemental(requestSignal);
  }catch(e){
   if(e.status===401)throw e;
   if(live(requestSignal))error=new Error('暂时无法重新读取平台状态，请刷新核对。');
  }
 }
 async function checkReceipt(requestSignal){
  const record=anyPending();
  if(!record)return;
  const result=(await ctx.commands.resolve(record.slot)).result;
  if(!live(requestSignal))return;
  if(result?.data?.platformMaterialId&&result.data.activityId===state.activityId&&result.data.platformCode===state.platformCode){
   await readAfterWrite(requestSignal,'已查到保存回执，平台进度以重新读取的结果为准。');
  }else receiptCheck='服务当前未返回保存回执。如需重试，请使用原请求。';
  return result;
 }
 async function download(id,target,requestSignal,screenshot=false){
  const item=originals().find(m=>m.platformMaterialId===id);
  const file=screenshot?item?.verification?.screenshot:item?.file;
  if(!safeId(id)||!file?.downloadUrl)return;
  target.disabled=true;
  try{
   const response=await ctx.adapter.request(writePath()+'/'+id+(screenshot?'/check-screenshot':'/download'),{binary:true,signal:requestSignal});
   const digest=shaHex(await crypto.subtle.digest('SHA-256',response.bytes));
   if(!live(requestSignal))return;
   if(!file.sha256||digest!==file.sha256)throw new Error('原件摘要不匹配，已停止下载。');
   const url=URL.createObjectURL(new Blob([response.bytes],{type:'application/octet-stream'}));
   const anchor=document.createElement('a');
   anchor.href=url;anchor.download=file.filename;anchor.click();
   setTimeout(()=>URL.revokeObjectURL(url),30000);
  }finally{if(target.isConnected)target.disabled=false}
 }
 async function submitVerification(){
  if(busy||!canReview()||readbackRequired||CONFLICT.has(error?.code))return;
  const action=reviewAction(), recheck=action===RECHECK_ACTION;
  const needsScreenshot=!recheck&&caps.checkScreenshotRequired===true;
  const requestSignal=signal;
  error=null;receipt=null;receiptCheck=null;busy=true;paint();
  try{
   const original=pending(action);
   if(original&&await checkReceipt(requestSignal))return;
   if(!live(requestSignal))return;
   const previous=original?.body??original?.recovery;
   const body={expectedApprovedVersionId:previous?.expectedApprovedVersionId??state.approvedVersionId,
    expectedPlatformRevision:previous?.expectedPlatformRevision??state.platformRevision,
    platformMaterialId:previous?.platformMaterialId??state.currentMaterial?.platformMaterialId,...verificationDraft};
   if(recheck)body.checkStage='recheck';
   if(body.decision==='return'&&!body.reason.trim())throw validation(recheck?'请填写复查发现的问题。':'请填写退回原因。','reason');
   if(body.decision==='approve'&&(body.platformStatus!=='live'||!body.platformActivityIdConfirmed)){
    throw validation('请核对活动 ID 和平台已上线状态。','platformStatus');
   }
   if(needsScreenshot&&checkScreenshot){
    const ext=checkScreenshot.name.split('.').at(-1).toLowerCase();
    if(!has(SCREENSHOT_MEDIA,ext)||!checkScreenshot.size||checkScreenshot.size>5*1024*1024){
     throw validation('请选择 5 MB 以内的 PNG、JPG 或 WebP 截图。','screenshot');
    }
    body.screenshot=await attachmentPayload(checkScreenshot,SCREENSHOT_MEDIA[ext]);
   }else if(needsScreenshot&&body.decision==='approve'){
    throw validation('请上传平台活动截图。','screenshot');
   }else if(body.decision==='approve'&&!body.evidenceSource.trim()){
    throw validation('请填写本次核实来源。','evidenceSource');
   }
   if(!live(requestSignal))return;
   if(original&&original.digest!==await digestBody(body))throw new Error('当前审核内容和原请求不一致，请恢复原审核内容后重试。');
   const result=await ctx.commands.run(slot(action),action,'POST',verifyPath(),body,{signal:requestSignal});
   if(!live(requestSignal))return;
   if(!result.data?.decisionId||result.data.activityId!==state.activityId||result.data.platformCode!==state.platformCode||
      result.data.platformMaterialId!==body.platformMaterialId||result.data.state!==(body.decision==='approve'||recheck?'live':'returned')){
    readbackRequired=true;throw new Error('审核回执内容暂无法确认，请重新读取平台状态。');
   }
   const message=recheck?(body.decision==='approve'?'复查通过，已进入复盘。':'已记录复查问题。'):
    (body.decision==='approve'?'已记录该平台确认上线。':'已退回原运营补齐平台资料。');
   await readAfterWrite(requestSignal,message);
  }catch(e){if(e.status===401)throw e;if(live(requestSignal))error=e}
  finally{if(live(requestSignal)){busy=false;paint('h1')}}
 }
 return {
  ownsPending(record){return state&&((record.action===ACTION&&record.path===writePath())||([VERIFY_ACTION,RECHECK_ACTION].includes(record.action)&&record.path===verifyPath()))&&ctx.route()===route},
  async load(nextRoute,query,requestSignal){
   signal=requestSignal;
   if(route!==nextRoute){this.clear();signal=requestSignal}
   route=nextRoute;params=query;
   const [,aid,pc]=parsePlatformMaterialsRoute(route);
   const env=await ctx.adapter.request('/capabilities',{signal});
   if(!live(requestSignal))return;
   caps=platformMaterialsCapability(env.data);
   if(!caps?.platformCodes.includes(pc))throw Object.assign(new Error('当前服务未开放此平台的资料入口。'),{status:404});
   const base='/activities/'+aid;
   const [a,s]=await Promise.all([ctx.adapter.request(base,{signal}),ctx.adapter.request(base+'/platforms/'+pc,{signal})]);
   if(!live(requestSignal))return;
   if(a.data.id!==aid||s.data.activityId!==aid||s.data.platformCode!==pc)throw new Error('平台资料响应与当前活动不一致。');
   activity=a.data;state=s.data;
   await readSupplemental(requestSignal);
   paint();
  },
  input(target){if(target.id==='pm-platform-id')draft.id=target.value;
   if(target.hasAttribute('data-pm-verify'))verificationDraft[target.name]=target.type==='checkbox'?target.checked:target.value},
  change(target){
   if(target.id==='pm-check-screenshot'){
    checkScreenshot=target.files[0]??null;
    if(checkScreenshot)snapshotFile(checkScreenshot);
    const selection=document.querySelector('#pm-check-screenshot-selection');
    selection.hidden=!checkScreenshot;
    document.querySelector('#pm-check-screenshot-name').textContent=checkScreenshot?.name??'';
    return;
   }
   if(target.hasAttribute('data-pm-verify')){
    verificationDraft[target.name]=target.type==='checkbox'?target.checked:target.value;
    const label=document.querySelector('#pm-check-comment-label');
    if(target.name==='decision'&&label)label.textContent=checkCommentLabel(target.value);
    return;
   }
   if(target.id==='pm-file'){
    draft.file=target.files[0]??null;
    if(draft.file)snapshotFile(draft.file);
    document.querySelector('#pm-file-note').textContent=draft.file?'已选 '+draft.file.name+' · 支持 CSV 或 XLSX，清单按原件留存。':'支持 CSV 或 XLSX，清单按原件留存。';
    return;
   }
   if(target.name==='scopeMode'){
    draft.scope=target.value;
    const file=document.querySelector('#pm-file');
    file.disabled=draft.scope!=='list';
    file.parentElement.hidden=file.disabled;
   }
  },
  async click(target){
   const action=target.closest('[data-pm-action]');
   if(!action||busy)return;
   const requestSignal=signal;
   if(action.dataset.pmAction==='sales-comparison'){
    if(salesBusy||state?.lifecycleStage!=='retrospective')return;
    const requestSignal=signal;
    const target=state;
    salesBusy=true;salesError=null;paint();
    try{
     const query=new URLSearchParams({dateBasis:'approved_plan',storeScope:'all_platform'});
     const response=await ctx.adapter.request(resource()+'/sales-comparison?'+query,{signal:requestSignal});
     if(!live(requestSignal)||state!==target)return;
     const value=response.data;
     if(value?.schemaVersion!=='activity-sales-comparison/1.0'||value.activityId!==target.activityId||
        value.platformCode!==target.platformCode||value.activityVersionId!==target.approvedVersionId||!Array.isArray(value.rows))throw new Error('销量响应范围不一致');
     salesComparison=value;
    }catch(e){if(e.status===401)throw e;if(live(requestSignal))salesError=e}
    finally{if(live(requestSignal)){salesBusy=false;paint('[data-pm-action=sales-comparison]')}}
    return;
   }
   switch(action.dataset.pmAction){
    case 'download': await download(action.dataset.pmId,action,requestSignal);return;
    case 'download-screenshot': await download(action.dataset.pmId,action,requestSignal,true);return;
    case 'remove-screenshot': checkScreenshot=null;paint('#pm-check-screenshot');return;
    case 'edit': edit=true;error=null;receipt=null;draft.id=state.currentMaterial?.platformActivityId??draft.id;draft.scope=state.currentMaterial?.scopeMode??draft.scope;paint('#pm-platform-id');return;
    case 'refresh':
     if(anyPending()){receiptCheck='请先核对原请求结果。';paint();return}
     busy=true;error=null;paint();
     try{await readAfterWrite(requestSignal,receipt,{preserveDraft:!receipt})}
     finally{if(live(requestSignal)){busy=false;paint('#pm-platform-id')}}
     return;
    case 'check':
     busy=true;error=null;paint();
     try{await checkReceipt(requestSignal)}
     catch(e){if(e.status===401)throw e;if(live(requestSignal))error=e}
     finally{if(live(requestSignal)){busy=false;paint('#pm-form [type=submit]')}}
   }
  },
  async submit(form){
   if(form.id==='pm-verify-form'){await submitVerification();return}
   if(form.id!=='pm-form'||busy||!canEdit()||readbackRequired||CONFLICT.has(error?.code))return;
   const requestSignal=signal;
   error=null;receipt=null;receiptCheck=null;
   busy=true;paint();
   let focus='#pm-platform-id';
   try{
    const original=pending();
    if(original&&await checkReceipt(requestSignal))return;
    if(!live(requestSignal))return;
    if(!draft.id.trim()||[...draft.id].length>200||/[\u0000-\u001F\u007F-\u009F]/.test(draft.id)){
     throw validation('请填写 1–200 个字符的有效平台活动 ID。','platformActivityId');
    }
    const list=draft.scope==='list';
    if(!list&&!allBrandAllowed())throw validation('当前审核版本未开放单品牌全商品范围。','scopeMode');
    let file=null;
    if(list&&draft.file){
     try{file=await listPayload(draft.file,caps.upload)}
     catch(e){throw validation(e.message,'listFile')}
    }
    if(!live(requestSignal))return;
    const previous=original?.body??original?.recovery;
    const body={
     expectedActivityRevision:previous?.expectedActivityRevision??state.activityRevision,
     expectedApprovedVersionId:previous?.expectedApprovedVersionId??state.approvedVersionId,
     expectedPlatformRevision:previous?.expectedPlatformRevision??state.platformRevision,
     platformActivityId:draft.id, scopeMode:draft.scope, listFile:file
    };
    if(new TextEncoder().encode(JSON.stringify(body)).length>caps.upload?.maxJsonBytes)throw validation('清单请求超过服务允许的大小。','listFile');
    if(original&&original.digest!==await digestBody(body))throw new Error('当前输入和原请求不一致，请恢复原平台活动 ID、商品范围和同一原件。');
    if(!live(requestSignal))return;
    const result=await ctx.commands.run(slot(),ACTION,'POST',writePath(),body,{signal:requestSignal});
    if(!live(requestSignal))return;
    if(!result.data?.platformMaterialId||result.data.activityId!==state.activityId||result.data.platformCode!==state.platformCode||result.data.state!=='pending_verification'){
     readbackRequired=true;
     throw new Error('保存回执内容暂无法确认，请重新读取平台状态。');
    }
    await readAfterWrite(requestSignal,'已收到资料保存回执。');
    focus='h1';
   }catch(e){
    if(e.status===401)throw e;
    if(!live(requestSignal))return;
    error=e;
    if(e.details?.field==='listFile')focus='#pm-file';
   }finally{if(live(requestSignal)){busy=false;paint(focus)}}
  },
  pause(){busy=false},
  clear(){
   route='';signal=null;state=null;activity=null;salesComparison=null;salesError=null;salesBusy=false;draft=newDraft();verificationDraft=newVerificationDraft();checkScreenshot=null;busy=false;edit=false;error=null;receipt=null;readbackRequired=false;receiptCheck=null;
  }
 };
}
