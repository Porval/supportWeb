import {h,input,textarea,select,textOrNull,TYPE_LABELS,PLATFORM_LABELS,FIELD_LABELS,beijing,businessMessage} from './b-common.js';
import {activityCategoryLabel,activityDate,APPLICABLE_PLATFORM_LABELS,PRODUCT_SCOPE_INTENT_LABELS,applicablePlatformCodes,productScopeIntentLabel,verifiedProductScopeLabel} from './dto.js';
export {FIELD_LABELS};
const SCOPE_HINTS={
 deferred:'可先保存，待资料齐全后补充。',
 all_products:'请写明全部商品的适用边界，例如品牌或品类；材料已明确时可注明原文位置。',
 specific_products:'请写商品名称、内部码或 UPC，也可添加商品清单。',
 no_specific_products:'如为整单券，请写已知条件，例如满减门槛、适用订单及排除范围。'
};
export const productScopeHint=code=>Object.hasOwn(SCOPE_HINTS,code)?SCOPE_HINTS[code]:'按本次活动实际适用的范围选择，可留空。';
export function submissionDeclarationCatalog(catalog){
 if(catalog?.submissionDeclarationsContractVersion!=='submission-declarations/1.0')return null;
 const {applicablePlatforms,productScopeIntents}=catalog;
 const matches=(items,labels)=>Array.isArray(items)&&items.length===Object.keys(labels).length&&
  Object.keys(labels).every((code,index)=>items[index]?.code===code&&items[index].label===labels[code]);
 return matches(applicablePlatforms,APPLICABLE_PLATFORM_LABELS)&&matches(productScopeIntents,PRODUCT_SCOPE_INTENT_LABELS)
  ?{applicablePlatforms,productScopeIntents}:null;
}
export function declarationFieldsHtml(catalog,value={},errors={},required=false,{split=false}={}){
 const selected=applicablePlatformCodes(value);
 const scope=value.productScopeIntent??'';
 const platformError=errors.applicablePlatformCodes;
 const scopeError=errors.productScopeIntent;
 const platforms=`<fieldset aria-describedby="b-applicable-platform-hint${platformError?' b-applicable-platform-error':''}">
   <legend data-platform-legend>适用平台<span class="b-required" data-platform-required${required?'':' hidden'}> *</span></legend>
   <div class="design-inline-options">${catalog.applicablePlatforms.map(({code,label})=>`<label><input type="checkbox"
    name="applicablePlatformCodes" value="${h(code)}"${selected.includes(code)?' checked':''}${platformError?' aria-invalid="true"':''}>${h(label)}</label>`).join('')}</div>
   <p class="hint" id="b-applicable-platform-hint">${required?'可多选，品牌活动至少选一项。':'可多选。报名编号所属平台另填。'}</p>
   ${platformError?`<p class="field-error" id="b-applicable-platform-error" role="alert">${h(platformError)}</p>`:''}
  </fieldset>`;
 const productScope=`<div><label>商品适用范围（选填）<select name="productScopeIntent" aria-describedby="b-product-scope-hint${scopeError?' b-product-scope-error':''}"${scopeError?' aria-invalid="true"':''}>
   <option value="">请选择</option>${catalog.productScopeIntents.map(({code,label})=>`<option value="${h(code)}"${scope===code?' selected':''}>${h(label)}</option>`).join('')}
  </select></label>
  <p class="hint" id="b-product-scope-hint" data-product-scope-hint>${h(productScopeHint(scope))}</p>
  ${scopeError?`<p class="field-error" id="b-product-scope-error" role="alert">${h(scopeError)}</p>`:''}
 </div>`;
 return split?{platforms,productScope}:`<div data-submission-declarations>${platforms}${productScope}</div>`;
}
export function declarationRecordHtml(input,{identity=input?.form?.platformCode,verified}={}){
 if(!input)return '';
 const platforms=applicablePlatformCodes(input,identity).map(code=>APPLICABLE_PLATFORM_LABELS[code]).join('、')||'未选择，待确认';
 return `<p>适用平台：${h(platforms)} · 商品适用范围（提报声明）：${h(productScopeIntentLabel(input.productScopeIntent))}</p>
  ${verified?`<p>商品范围核验：${h(verifiedProductScopeLabel(verified))}</p>`:''}`;
}

export function brandSelectionHtml(value=[],catalog={brands:[]},{name='brandIds',disabled=false}={}){
 const selected=new Set(value??[]),brands=catalog.brands??[];
 const label=brands.filter(b=>selected.has(b.id)).map(b=>b.name).join('、')||'未关联品牌，可后补';
 return `<div class="design-brand-picker" data-brand-picker><p data-brand-selection>${h(label)}</p><details><summary>选择品牌</summary>
  <label>搜索品牌<input type="search" data-brand-filter placeholder="输入品牌名称"${disabled?' disabled':''}></label>
  <div class="design-brand-options">${brands.map(b=>`<label data-brand-option="${h(b.name)}" hidden><input type="checkbox" data-brand-choice name="${h(name)}" value="${h(b.id)}"${selected.has(b.id)?' checked':''}${disabled?' disabled':''}>${h(b.name)}</label>`).join('')}</div>
  <p class="hint" data-brand-search-hint>输入品牌名称后显示匹配项。</p><p data-brand-no-match hidden>没有匹配的品牌，请换一个名称搜索。</p></details></div>`;
}

export function fieldsHtml(value={},catalog={brands:[],platforms:[]},{identity=false,brandEvidence='',datesRequired=false,omit=new Set()}={}){return `<div class="b-form-grid">${omit.has('activityName')?'':input('activityName',FIELD_LABELS.activityName,value.activityName,{maxLength:200})}${select('type',FIELD_LABELS.type,Object.entries(TYPE_LABELS).map(([code,label])=>({code,label})),value.type)}${select('platformCode',identity?'报名编号所属平台':FIELD_LABELS.platformCode,catalog.platforms,value.platformCode)}${input('sourceActivityId',FIELD_LABELS.sourceActivityId,value.sourceActivityId,{maxLength:200})}${omit.has('startAt')?'':input('startAt',FIELD_LABELS.startAt,activityDate(value.startAt),{type:'date',required:datesRequired})}${omit.has('endAt')?'':input('endAt',FIELD_LABELS.endAt,activityDate(value.endAt),{type:'date',required:datesRequired})}<div class="span-2"><p>关联品牌</p>${brandSelectionHtml(value.brandIds,catalog)}${brandEvidence}</div><div class="span-2">${textarea('rulesText',FIELD_LABELS.rulesText,value.rulesText,10000)}</div>${textarea('brandBenefit',FIELD_LABELS.brandBenefit,value.brandBenefit,5000)}${textarea('remark',FIELD_LABELS.remark,value.remark,5000)}</div>`}
function captureActivityDate(day,original,endOfDay=false){
 if(!day)return null;
 if(day===activityDate(original))return original;
 return day+(endOfDay?'T23:59:59.999+08:00':'T00:00:00+08:00');
}

export function captureFields(form,originalDates={}){
 const data=new FormData(form);
 const keys=['activityName','type','platformCode','sourceActivityId','rulesText','brandBenefit','remark'];
 const value=Object.fromEntries(keys.map(key=>[key,textOrNull(data.get(key))]));
 value.startAt=captureActivityDate(data.get('startAt'),originalDates.startAt);
 value.endAt=captureActivityDate(data.get('endAt'),originalDates.endAt,true);
 value.brandIds=data.getAll('brandIds');
 return value;
}
const previewable=m=>m.mediaType?.startsWith('text/')||['application/vnd.jiatan.form+json','application/vnd.jiatan.confirmation+json'].includes(m.mediaType);
const supportLabel=value=>({supported:'可进行本地解析',PARSER_UNAVAILABLE:'仅留存原件，当前不能解析',controlled_form:'原始填报表单',controlled_confirmation:'人工确认记录',controlled_resolution:'系统自动处理决议'}[value]??'解析能力待核实');
export function materialHtml(materials=[],{compact=false}={}){
 if(!materials.length)return '<p class="muted">没有附件原件。</p>';
 const items=materials.map(m=>{
  const info=compact?'':`<small>${m.size==null?'大小待核实':h(m.size)+' 字节'} · ${h(m.available===false?businessMessage({code:m.reasonCode},'原件缺失或未授权读取'):supportLabel(m.parseSupport))}</small>
   ${m.sha256?`<details><summary>原件核验信息</summary><p class="mono">SHA-256：${h(m.sha256)}</p></details>`:''}`;
  const actions=m.available!==false&&m.downloadAvailable?`<div class="b-material-action">
   ${previewable(m)?`<button type="button" data-material-preview="${h(m.materialId)}">查看原文 / 表单</button>`:''}
   <button type="button" data-material-download="${h(m.materialId)}">下载原件</button></div>`:'<span class="muted">下载未授权或不可用</span>';
  return `<li><div><strong>${h(m.originalName??m.filename??m.materialId)}</strong>${info}</div>${actions}</li>`;
 });
 return `<ul class="b-file-list">${items.join('')}</ul>`;
}

export const bearingText=value=>({yes:'公司有承担',no:'公司无承担',unknown:'承担情况未知，待核实'}[typeof value==='string'?value:value?.state]??'承担情况未提供');
export function fieldValueText(key,value,{brands=[],products=[]}={}){
 if(key==='brandIds'&&(value==null||Array.isArray(value)&&!value.length))return '未关联品牌，可后补';
 if(value==null||value==='')return '未提供，待核实';
 if(key==='companyBearing'||key==='costEvidence')return bearingText(value);
 if(key==='type'||key==='initiatorType')return TYPE_LABELS[value]??'发起方待核实';
 if(key==='activityCategory')return activityCategoryLabel(value);
 if(key==='platformCode')return PLATFORM_LABELS[value]??'平台待核实';
 if(key==='startAt'||key==='endAt')return activityDate(value)??'日期待核实';
 if(key==='submittedAt')return beijing(value)??'时间待核实';
 if(key==='brandIds')return Array.isArray(value)&&value.length?value.map(id=>{const brand=brands.find(b=>b.id===id);return brand?brand.name+'（'+id+'）':id}).join('、'):'未关联品牌，可后补';
 if(key==='companyBearingState')return bearingText(value);
 if(Array.isArray(value)){
  if(!value.length)return key==='manualProductRows'?'未人工填写商品，可为非单品活动':'未提供';
  return value.map((item,index)=>{
   if(item==null)return '第'+(index+1)+'项待核实';
   if(typeof item!=='object'){const product=products.find(p=>p.id===item);return product?product.name+'（'+item+'）':String(item)}
   return '第'+(index+1)+'行：'+[item.rawInternalCode?'内部码 '+item.rawInternalCode:null,item.rawUpc?'UPC '+item.rawUpc:null,item.rawName?'商品 '+item.rawName:null,item.productId?'已选目录商品 '+item.productId:null,item.rowId?'行ID '+item.rowId:null].filter(Boolean).join('；');
  }).join('\n');
 }
 if(typeof value==='object')return '已保留结构化资料，请结合原文核对';
 return String(value);
}
export function factsHtml(fields={},catalog={}){return `<dl class="info-grid">${Object.entries(fields??{}).map(([key,value])=>`<div><dt>${h(FIELD_LABELS[key]??key)}</dt><dd>${h(fieldValueText(key,value,catalog))}</dd></div>`).join('')}</dl>`}
