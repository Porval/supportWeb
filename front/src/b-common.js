import {h,badge} from './ui.js';
import {beijing,ROLE_LABELS,TYPE_LABELS,PLATFORM_LABELS} from './dto.js';
export {h,badge,beijing,ROLE_LABELS,TYPE_LABELS,PLATFORM_LABELS};
export const FIELD_LABELS={activityName:'活动名称',type:'发起方',platformCode:'平台',sourceActivityId:'平台活动ID',startAt:'开始日期（北京时间）',endAt:'结束日期（北京时间）',brandIds:'关联品牌',rulesText:'活动规则',brandBenefit:'品牌利益点',remark:'备注',companyBearing:'公司承担',manualProductRows:'人工商品信息',submitterId:'提报人ID',submittedAt:'提交时间（北京时间）',activityId:'内部活动ID',versionId:'版本ID',parentSubmissionId:'来源提报ID',internalCode:'内部码',upc:'UPC',name:'名称',rawInternalCode:'原始内部码',rawUpc:'原始UPC',rawName:'原始商品名称',confirmedProducts:'已关联商品'};
export const rows=(cells)=>'<tr>'+cells.map(x=>`<td>${x}</td>`).join('')+'</tr>';
export function table(headers,body,empty='暂无记录。'){return `<div class="table-wrap"><table><thead><tr>${headers.map(x=>`<th scope="col">${h(x)}</th>`).join('')}</tr></thead><tbody>${body.join('')||`<tr><td colspan="${headers.length}"><p class="inline-empty">${h(empty)}</p></td></tr>`}</tbody></table></div>`}
export const option=(value,label,selected)=>`<option value="${h(value??'')}"${String(value??'')===String(selected??'')?' selected':''}>${h(label)}</option>`;
export function input(name,label,value='',{type='text',maxLength,placeholder='',disabled=false,required=false}={}){return `<label>${h(label)}${required?' <span class="b-required">*</span>':''}<input name="${h(name)}" type="${type}" value="${h(value??'')}" ${maxLength?`maxlength="${maxLength}"`:''} placeholder="${h(placeholder)}" ${disabled?'disabled':''}${required?' required':''}></label>`}
export const textarea=(name,label,value='',limit=5000)=>`<label>${h(label)}<textarea name="${h(name)}" rows="3" maxlength="${limit}">${h(value??'')}</textarea></label>`;
export function select(name,label,items,value='',{multiple=false,disabled=false,empty='请选择',required=false}={}){const selected=multiple?new Set(value??[]):null;return `<label>${h(label)}${required?' <span class="b-required">*</span>':''}<select name="${h(name)}" ${multiple?'multiple size="5"':''} ${disabled?'disabled':''}${required?' required':''}>${!multiple&&empty!==null?option('',empty,value):''}${items.map(i=>multiple?`<option value="${h(i.id??i.code)}"${selected.has(i.id??i.code)?' selected':''}>${h(i.name??i.label)}</option>`:option(i.id??i.code,i.name??i.label,value)).join('')}</select></label>`}
export function panel(title,body,extra=''){return `<section class="panel"><div class="section-heading"><h2>${h(title)}</h2>${extra}</div>${body}</section>`}
export function info(items){return `<dl class="info-grid">${items.map(([label,value])=>`<div><dt>${h(label)}</dt><dd>${value??'待核实'}</dd></div>`).join('')}</dl>`}
export function pager(page,total,base,params={},size=10){const pages=Number.isInteger(total)?Math.max(1,Math.ceil(total/size)):null;const url=p=>base+'?'+new URLSearchParams({...params,page:p});return `<div class="pagination"><span>${total==null?'数量待核实':`共${total}条`} · 每页${size}条</span>${page>1?`<a class="button" href="${h(url(page-1))}">上一页</a>`:'<button disabled>上一页</button>'}<span>${page} / ${pages??'待核实'}</span>${pages&&page<pages?`<a class="button" href="${h(url(page+1))}">下一页</a>`:'<button disabled>下一页</button>'}</div>`}
export const STATE_LABELS={received:'已受理',analyzing:'分析中',needs_input:'待补充',needs_confirmation:'待确认',partially_resolved:'部分已建档',resolved:'已建档或关联',failed:'处理失败',queued:'排队中',running:'处理中',succeeded:'分析完成',partial:'部分分析完成',cancel_requested:'取消中',cancelled:'已取消分析',interrupted:'已中断',initial_pending:'待初审',pricing_pending:'价盘复审中',final_pending:'待终审',approved:'内部审核通过',rejected:'审核不通过',pending:'待处理',initial:'初审',pricing:'价盘复审',final:'终审',not_started:'尚未发起审核',waiting:'等待前序节点',inactive:'尚未轮到本节点',explicit:'已识别原始值',missing:'未提供',conflict:'存在冲突',parsed:'已解析',unsupported:'暂不支持解析',invalid:'材料无效',complete:'处理完成',decode:'解析材料',decoding:'解析材料',starting:'开始处理',analyze:'识别与匹配',matching:'匹配商品',created:'已新建活动',linked:'已关联来源',change_saved:'已保存补充版本',approve:'本节点通过',reject:'本节点不通过'};
export const stateLabel=x=>STATE_LABELS[x]??x??'待核实';
export const REASON_LABELS={NOT_PROVIDED:'原始材料未提供，可人工补充或保留待核实',EXPLICIT_NAME_REQUIRED:'需要人工确认活动名称，才能完成本分组建档',BEARING_EVIDENCE_UNVERIFIED:'所选证据尚不能确认公司无承担，交管理核实',COMPANY_BEARING_REQUIRES_REVIEW:'公司有承担或承担情况未知，需管理核实',BEARING_EVIDENCE_REQUIRED:'缺少支持公司无承担的证据；可补充证据或保留未知交管理核实',NOT_ASSIGNEE:'当前账号不是此节点的指派处理人',ROLE_MISMATCH:'当前业务角色不符合本节点要求',ACTION_OR_SCOPE_DENIED:'当前账号的审核授权或数据范围不满足要求',ASSIGNMENT_REQUIRED:'本节点尚未指派处理人',POLICY_UNCONFIRMED:'重新提审或生效变更规则尚未确定，本版已保存，暂不能推进',SELF_REVIEW_POLICY_UNCONFIRMED:'本人审核自己提报活动的规则尚未确定',SAME_REVIEWER_POLICY_UNCONFIRMED:'同一人兼任初审和终审的规则尚未确定',FORBIDDEN:'当前账号没有执行本次操作的授权',PROFILE_VERSION_CONFLICT:'账号资料已被更新，请读取最新资料并核对自己的修改',STATE_CONFLICT:'当前记录已发生变化，请核对最新状态后再提交',ANALYSIS_STALE:'材料、分析或目录版本已变化，请重新核对当前输入和分析结果',DUPLICATE_ACTIVITY:'发现相同活动身份，请核对已有活动，选择关联来源或保存补充版本',DUPLICATE_ACK_REQUIRED:'活动身份信息不完整，请先人工核对重复风险',SOURCE_CONFLICT:'本次内容与原活动条件不同，请核对后保存独立补充版本',INCOMPLETE_RELATIONS:'当前范围仍有未确认的商品关系，不能作为全量导出',PARSER_UNAVAILABLE:'当前不能解析此格式，原件保留；请补充文字或人工字段',UNSUPPORTED_MATERIAL:'该材料格式不受支持，请使用已声明支持的格式',INVALID_MATERIAL:'材料编码或内容结构不符合受支持格式，请核对原件',LIMIT_EXCEEDED:'本次处理达到材料或行数上限，未覆盖的内容仍需补充核对',FORMULA_NOT_EVALUATED:'表格公式仅保留原文，未计算结果',FIELD_UNRESOLVED:'部分字段不能可靠识别，请人工核对',FIELD_NOT_TEXT:'该字段不是文本，未自动推断',PRODUCT_ATTRIBUTES_WITHOUT_ANCHOR:'品牌或规格已保留；缺少商品标识或商品名，未生成商品行，适用范围待人工核对',TEMPLATE_EXAMPLE_SKIPPED:'模板说明或示例行未当作真实活动记录',MANUAL_GROUP_AMBIGUOUS:'材料包含多场活动，人工表单未自动分配，请逐组核对',DATE_RANGE_CONFLICT:'开始时间晚于结束时间，请人工核对',IDENTIFIER_CELL_NOT_TEXT:'商品标识不是文本，不能用于精确匹配；请核对内部码或UPC',INVALID_UPC:'UPC 应为8至14位文本数字，系统未转换或补零',INTERRUPTED:'处理已中断，原材料仍保留，可核对后重试',WORKER_TIMEOUT:'本次材料处理超时，原材料仍保留，可稍后核对并重试',TIMEOUT:'请求超时，请先核对当前结果，再决定是否重试',COMMAND_UNKNOWN:'提交结果尚未确认，请先读取原请求回执',SMS_NOT_CONFIGURED:'短信服务尚未配置，本次未更换手机号',MATERIAL_UNAVAILABLE:'原件不存在或当前账号无权查看',NOT_FOUND:'记录不存在或当前账号无权查看',SESSION_REQUIRED:'登录已失效，请重新登录',TEST_CREDENTIAL_EXPIRED:'账号凭据已到期，请联系管理员'};
export const ACTION_LABELS={'materials.upload':'留存活动原件','submissions.create':'受理原始提报','submissions.update':'保存输入修订','submissions.delete':'删除提报','submissions.report_bad_case':'记录提报失败','analysis.start':'分析当前材料','analysis.cancel':'取消本次分析','submissions.confirm':'确认活动分组','submissions.auto_resolve':'系统处理活动分组','submissions.grouping_decide':'确认材料分组','activities.supplement':'保存组内补充版本','accounts.profile.update':'更新账号资料','reviews.decide':'保存本节点审核决定','reviews.start':'发起本版本审核','platform_materials.submit':'保存平台资料'};
Object.assign(REASON_LABELS,{ACCOUNT_EXISTS:'该手机号账号已存在，请核对后再试',INVALID_PHONE:'请输入有效的中国大陆11位手机号',LAST_ADMIN_REQUIRED:'至少需要保留一位有效管理员账号',VERSION_SOURCES_NOT_RECORDED:'旧版本没有记录可追溯来源，未补造原始资料',INPUT_REVISION_UNAVAILABLE:'本版绑定的原始输入修订不可用',SUBMISSION_METADATA_UNAVAILABLE:'原提报信息不可用',SUBMITTER_SNAPSHOT_NOT_RECORDED:'当时的提交人资料未记录，未使用当前人员资料补历史',ANALYSIS_BINDING_NOT_RECORDED:'旧来源未记录分析绑定，未选取其他分析补齐',ANALYSIS_BINDING_UNAVAILABLE:'本版绑定的分析结果不可用',ANALYSIS_DIGEST_MISMATCH:'已保存的分析结果核验不一致，暂不展示该结果',CANDIDATE_BINDING_UNAVAILABLE:'本版确认分组的绑定资料不可用',CONFIRMATION_UNAVAILABLE:'本次人工确认原件不可用',MATERIAL_READ_FORBIDDEN:'当前账号无权读取此原件内容',MATERIAL_HASH_MISMATCH:'原件内容核验不一致，暂不展示',INPUT_MATERIAL_NOT_RECORDED:'原始输入没有记录材料引用',INPUT_MATERIAL_NOT_BOUND:'原始材料未绑定到当前版本',INPUT_MATERIAL_MISMATCH:'原始输入与绑定原件核验不一致',EVIDENCE_MATERIAL_NOT_BOUND:'该原文引用未绑定到当前版本材料',EVIDENCE_SOURCE_UNAVAILABLE:'引用的原始材料不可用',EVIDENCE_SHA_MISMATCH:'引用与原件核验不一致，暂不展示',EVIDENCE_LOCATOR_MISMATCH:'引用位置或原文核验不一致，暂不展示'});
export function businessMessage(issue,fallback='当前信息尚未核实，请查看原材料并人工核对。'){
 if(typeof issue==='string')return REASON_LABELS[issue]??(/[\u3400-\u9fff]/u.test(issue)?issue:fallback);
 if(!issue)return fallback;
 const reason=REASON_LABELS[issue.reasonCode]??REASON_LABELS[issue.code];
 if(reason)return reason;
 if(typeof issue.message==='string'&&/[\u3400-\u9fff]/u.test(issue.message))return issue.message.replace(/\b(activityName|startAt|endAt|brandIds|platformCode|sourceActivityId|rulesText|brandBenefit|companyBearing|remark)\b/g,key=>FIELD_LABELS[key]);
 return fallback;
}
// Technical diagnostics stay in the original response/logs, outside business pages.
export function diagnostics(){return ''}
export function errorPanel(error){return `<div class="warning" role="alert">${h(businessMessage(error,'请求未完成，请核对当前结果后重试。'))}</div>${error.code==='COMMAND_UNKNOWN'?'<p class="hint">结果未知：先核对服务回执。未确认前不创建第二次提交；重试沿用原请求键和正文。</p>':''}${error.details?.activityId?`<p>需核对的活动：${h(error.details.activityId)}</p>`:''}${error.code||error.details?diagnostics({code:error.code,message:error.message,details:error.details},'查看请求诊断与冲突资料'):''}`}
export function actionAllowed(dto,action){const allowed=dto?.allowedActions;return Array.isArray(allowed)?allowed.includes(action):allowed?.[action]===true}
export const textOrNull=value=>value==null||value===''?null:String(value);
export function locatorLabel(locator){
 if(typeof locator==='string')return locator;
 if(!locator||typeof locator!=='object')return '原文位置未提供';
 if(locator.kind==='manual')return '人工填写位置 · '+(locator.location??'未提供');
 const parts=[];
 if(locator.kind==='form')parts.push('原始表单');
 if(locator.fieldPath)parts.push(FIELD_LABELS[locator.fieldPath]??(locator.fieldPath.startsWith('manualProductRows')?'人工商品信息 · '+locator.fieldPath.replace('manualProductRows','行'):('表单字段 '+locator.fieldPath)));
 if(locator.sheet)parts.push('工作表「'+locator.sheet+'」');
 if(locator.cell)parts.push('单元格 '+locator.cell);
 if(locator.line!=null)parts.push('第'+locator.line+'行');
 else if(locator.lineStart!=null)parts.push('第'+locator.lineStart+(locator.lineEnd!=null?'至'+locator.lineEnd:'')+'行');
 if(locator.row!=null&&!locator.cell)parts.push('第'+locator.row+'行');
 if(locator.column!=null&&!locator.cell)parts.push('第'+locator.column+'列');
 if(locator.paragraph!=null)parts.push('第'+locator.paragraph+'段');
 if(locator.table!=null)parts.push('第'+locator.table+'张表');
 return parts.join(' · ')||'原文片段';
}
export function evidenceList(list=[],materials=[]){return list.length?`<div class="evidence-list">${list.map(e=>{const material=materials.find(m=>m.materialId===e.materialId);const ref=e.referenceId??(e.runId&&e.evidenceId?e.runId+':'+e.evidenceId:e.evidenceId??e.runId);return `<article data-evidence-reference="${h(ref)}"><strong>${h(material?.originalName??material?.filename??'原始材料')} · ${h(locatorLabel(e.locator))}</strong><small>${ref?'引用 '+h(ref)+' · ':''}材料 ${h(e.materialId??'未提供')}${e.inputRevision!=null?' · 输入修订 '+h(e.inputRevision):''}</small><blockquote>${h(e.quote??'未提供原文片段')}</blockquote>${e.sourceSha256?`<details><summary>原文核验信息</summary><p class="mono">SHA-256：${h(e.sourceSha256)}</p></details>`:''}</article>`}).join('')}</div>`:'<p class="muted">未提供可用的提取证据，需结合原材料人工核对。</p>'}

// Presentation only: IDs remain in API objects, hrefs, data attributes and input values.
// Shared coverage includes asynchronously opened dialogs and history/source text.
const UUID_PART='[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}';
const MACHINE_REFERENCE=new RegExp('(?:[A-Za-z][A-Za-z0-9]*-)*'+UUID_PART+'(?:-r[0-9]+)?|(?:candidate-brand-|ROW-|REF-)[a-f0-9]{16,64}', 'gi');
export function businessReferenceText(value,labels=new Map()){
 return String(value).replace(MACHINE_REFERENCE,id=>{
  const known=labels.get(id);if(known)return known;
  if(/^(?:FORM-|AUTO-)?(?:MAT|FORM|AUTO)/i.test(id))return '对应材料';
  if(/^ACT-/i.test(id))return '对应活动';
  if(/^VER-/i.test(id))return '对应版本';
  if(/^SUB-/i.test(id))return '原提报';
  if(/^(?:test|user|account)-/i.test(id))return '姓名未记录';
  return '关联记录';
 });
}
export function installBusinessLabels(root,source){
 const labels=()=>{
  const result=new Map(),seen=new WeakSet();
  function visit(value){
   if(!value||typeof value!=='object'||seen.has(value))return;seen.add(value);
   const id=value.id??value.materialId??value.versionId;
   const name=value.displayName??value.nameSnapshot??value.originalName??value.filename??value.name??(value.versionNo!=null?'V'+value.versionNo:null);
   if(typeof id==='string'&&typeof name==='string'&&businessReferenceText(name)===name)result.set(id,name);
   for(const item of Object.values(value))visit(item);
  }
  visit(source());return result;
 };
 const observer=new MutationObserver(records=>{
  observer.disconnect();
  const names=labels(),nodes=new Set(records.flatMap(record=>record.type==='characterData'?[record.target]:[...record.addedNodes]));
  function format(node){
   if(node.nodeType===3){
    if(node.parentElement?.closest('script,style,textarea,input,[contenteditable="true"]'))return;
    const next=businessReferenceText(node.nodeValue,names);if(next!==node.nodeValue)node.nodeValue=next;
   }else if(node.nodeType===1){for(const child of [...node.childNodes])format(child)}
  }
  for(const node of nodes)if(root.contains(node))format(node);
  observer.observe(root,{subtree:true,childList:true,characterData:true});
 });
 observer.observe(root,{subtree:true,childList:true,characterData:true});
 return ()=>observer.disconnect();
}

