// Display only: service state, evidence and confirmed inputs remain authoritative.
import {h,badge,rows,table,FIELD_LABELS,TYPE_LABELS,stateLabel,businessMessage,locatorLabel,evidenceList,diagnostics} from './b-common.js';
import {fieldValueText,bearingText} from './b-fields.js';

export function issueList(items=[],empty='未报告处理问题。'){
 return items.length?`<ul class="b-business-list">${items.map(item=>`<li>${h(businessMessage(item))}${item?.locator?`<small>原文位置：${h(locatorLabel(item.locator))}</small>`:''}</li>`).join('')}</ul>`:`<p class="muted">${h(empty)}</p>`;
}

export function blockedReasonsHtml(items=[]){
 return items.length?`<div class="b-note"><strong>当前操作受限原因</strong>${issueList(items)}${diagnostics(items,'查看操作限制诊断')}</div>`:'';
}

export function bearingVerificationHtml(value,review,evidence=[],materials=[],referenceRunId=null){
 if(!value)return '<p class="muted">服务尚未返回承担核实结果，请结合原始材料核对。</p>';
 const actualDefault=review?.state==='approved'&&review?.approvalMode==='rule_default';
 const result=actualDefault?'已按规则默认内部通过':value.defaultApprovalEligible===true?'公司无承担依据已核实':value.state==='requires_management_verification'?'提报时需管理核实':'承担核实结果待确认';
 const source=value.sourceField;
 const refs=value.selectedEvidenceIds??[];
 const selected=evidence.filter(e=>e.referenceId?referenceRunId&&refs.some(id=>e.referenceId===referenceRunId+':'+id):refs.includes(e.evidenceId));
 return `<div class="b-bearing-result"><h3>公司承担核实</h3><p>${badge(result,actualDefault?'green':'amber')}</p><p>本版当前内部结果：${h(review?.state?stateLabel(review.state):'待核实')}。${review?.state==='approved'&&review?.approvalMode==='manual'?'本版已完成人工审核，不将原先的免审资格判断改写为规则默认通过。':''}</p>${table(['材料中的原始识别值','人工确认声明','服务核实说明'],[rows([h(source?.status==='missing'?'材料未提供':bearingText(source?.value)),h(bearingText(value.humanConfirmedState)),h(actualDefault?'本版已留存默认通过规则依据，未生成伪人工审核记录。':value.defaultApprovalEligible===true?'已核实公司无承担证据；当前内部结果仍以上方版本审核状态为准。':'提报时判断：'+businessMessage(value,'服务尚未确认默认通过资格；当前处理结果以本版内部审核状态为准。'))])])}<p>本次选择的承担依据：${h(refs.length?refs.map(id=>referenceRunId?referenceRunId+':'+id:id).join('、'):'未选择证据')}。</p>${selected.length?evidenceList(selected,materials):refs.length?'<p class="hint">所选证据的原文定位尚未随本版返回，可先查看原始材料；不据此补造结论。</p>':''}${diagnostics(value,'查看承担核实诊断')}</div>`;
}

export function confirmedComparisonHtml(originalFields={},confirmed={},catalog={},referenceRunId=null){
 const keys=[...new Set([...Object.keys(originalFields),...Object.keys(confirmed)])];
 return table(['字段','材料原始识别值','人工确认值','本次变化'],keys.map(key=>{
  const field=originalFields[key],before=field?.value,after=confirmed[key];
  const beforeValue=key==='companyBearing'?before?.state:before;
  const afterValue=key==='companyBearing'?after?.state:after;
  const empty=beforeValue==null||beforeValue==='';
  const changed=JSON.stringify(beforeValue)!==JSON.stringify(afterValue);
  const referenceChanged=key==='companyBearing'&&JSON.stringify(before?.evidenceIds??[])!==JSON.stringify(after?.evidenceIds??[]);
  const label=field?.status==='conflict'?'原值有冲突，需结合证据核对':changed?(empty?'人工补充':'人工修改'):'保留原值';
  return rows([h(FIELD_LABELS[key]??key),h(fieldValueText(key,before,catalog)),h(fieldValueText(key,after,catalog)),h(label+(referenceChanged?'；引用依据有调整':''))+(field?.evidenceIds?.length?`<small>原值依据：${h(field.evidenceIds.map(id=>referenceRunId?referenceRunId+':'+id:id).join('、'))}</small>`:'')]);
 }));
}

export function productConfirmationHtml(candidateRows=[],resolutions=[]){
 if(!candidateRows.length)return '<p class="muted">本分组没有单品行，不强制补入商品。</p>';
 return table(['原始内部码 / UPC / 商品名称','原始匹配结果','人工确认结果'],candidateRows.map(row=>{
  const decision=resolutions.find(r=>r.rowId===row.rowId);
  const original=[row.rawInternalCode?'内部码 '+row.rawInternalCode:null,row.rawUpc?'UPC '+row.rawUpc:null,row.rawName?'商品 '+row.rawName:null].filter(Boolean).join(' / ');
  const match={exact:'精确匹配',manual_selected:'人工选择目录商品',candidate:'待人工确认候选',unmatched:'未匹配',conflict:'商品信息冲突'}[row.matchKind]??'匹配情况待核实';
  return rows([h(original||'未提供原始商品标识')+`<small>行ID ${h(row.rowId)}</small>`,h(match)+(row.matchedProductId?`<small>目录商品 ${h(row.matchedProductId)}</small>`:''),h(decision?.decision==='link'?'已确认关联目录商品 '+decision.productId:decision?.decision==='unresolved'?'保留未确认，不计为精确关系':'尚未确认')]);
 }));
}

export function conflictsHtml(items=[],catalog={}){
 return table(['冲突字段','人工原始填写值','材料识别值','核对依据'],items.map(item=>rows([h(FIELD_LABELS[item.field]??'活动字段'),h(fieldValueText(item.field,item.manualValue,catalog)),(item.extractedValues??[]).map(value=>`<div>${h(fieldValueText(item.field,value,catalog))}</div>`).join('')||'没有可靠识别值',h((item.evidenceIds??[]).join('、')||'证据未提供')])),'未报告字段冲突。');
}

export function historyEventLabel(event){return {'submission.received':'原始材料已受理','submission.revised':'已保存新输入修订','activity.created':'已创建活动版本','activity.coupon_method_corrected':'已纠正活动方式为品牌满减券','activity.dates_backfilled':'已补齐活动日期','review.started':'已提交内部审核','activity.supplement_saved':'已保存资料补充版本','activity.change_saved':'已保存补充版本','activity.source_linked':'已关联本次材料来源','review.decided':'已记录人工节点决定','review.rule_default':'已按规则默认内部通过','review.default_approved':'已按规则默认内部通过'}[event]??'已留存业务操作'}

export function historyDetailsHtml(event){
 const detail=event.details??{};
 const items=[];
 if(detail.ruleId)items.push(['规则依据',detail.ruleId==='company_bearing_explicit_no/v1'?'公司明确无承担且依据已核实，默认内部通过':'已留存服务规则依据']);
 if(detail.evidenceIds?.length)items.push(['原文依据编号',detail.evidenceIds.join('、')]);
 if(detail.reason)items.push(['不通过原因',detail.reason]);
 if(detail.comment)items.push(['审核说明',detail.comment]);
 if(detail.optimizationNote)items.push(['商品优化建议',detail.optimizationNote]);
 if(detail.inputRevision!=null)items.push(['本次原始输入修订',String(detail.inputRevision)]);
 
 if(detail.bearingVerification)items.push(['承担核实',detail.bearingVerification.defaultApprovalEligible===true?'公司无承担依据已核实':businessMessage(detail.bearingVerification)]);
 if(detail.decision)items.unshift(['本节点决定',detail.decision==='approve'?'本节点通过':detail.decision==='reject'?'本节点不通过':'待核实']);
 return (items.length?`<dl class="b-history">${items.map(([label,value])=>`<dt>${h(label)}</dt><dd>${h(value)}</dd>`).join('')}</dl>`:'<p class="muted">未提供补充业务说明。</p>')+diagnostics(detail,'查看历史诊断与原始记录');
}

export function reviewResultLabel(review){
 if(!review)return '内部进度待核实';
 if(review.state)return stateLabel(review.state);
 if(review.result==='approved')return '内部审核通过';
 if(review.result==='rejected')return '内部审核不通过';
 return review.stage?stateLabel(review.stage)+'待处理':'内部进度待核实';
}
export function nodeResultLabel(value){return {approved:'本节点通过',rejected:'本节点不通过',pending:'待本节点处理',waiting:'等待前序节点',inactive:'尚未轮到本节点',blocked:'当前节点受限'}[value]??'节点状态待核实'}
