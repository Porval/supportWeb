import {h,badge,rows,table,input,textarea,select,evidenceList,locatorLabel,stateLabel} from './b-common.js';
import {activityDate,pendingItemLabels} from './dto.js';
import {materialHtml,brandSelectionHtml} from './b-fields.js';
import {subsidyPricesHtml,subsidyCorrections} from './b-subsidy-prices.js';

const CONTRACT='intake-group-review/2.0';
const SAFE_ID=/^[A-Za-z0-9_-]{1,128}$/;
const DIGEST=/^sha256:[0-9a-f]{64}$/;
export const groupReviewCapabilities=value=>value?.contractVersion===CONTRACT?value:{};
export const localIntakeAgentVersion=capability=>
 ['1.0.5','1.0.6','1.0.7','1.0.8'].includes(capability?.localAgentVersion)?capability.localAgentVersion:'1.0.3';

export function supplementDatesView(activity,base,version){
 if(!activity||!base||!version||base.activityId!==activity.id||version.activityId!==activity.id||
    base.latestSupplement?.versionId!==version.id||version.snapshot?.supplementBinding?.baseVersionId!==base.id||
    (activity.displayVersionId??activity.currentReviewVersionId)!==base.id||
    activity.effectiveExecutionVersionId===version.id||!SAFE_ID.test(version.id))return null;
 const fields=version.snapshot.resolvedFields??version.snapshot.confirmedFields??{};
 const start=activityDate(fields.startAt),end=activityDate(fields.endAt);
 if((!start&&!end)||(start&&end&&end<start)||(activity.startAt&&activity.endAt))return null;
 const dates=start&&end?`${start} 至 ${end}`:start?`${start} 开始，结束日期待补`:`开始日期待补，${end} 结束`;
 return {label:dates+' · 待采用',fields:[...(!activity.startAt&&start?['startAt']:[]),...(!activity.endAt&&end?['endAt']:[])],
  html:`<div data-supplement-dates>${h(dates)}（北京时间）<br>
   ${badge(`V${version.versionNo} 补充 · 待采用`,'amber')}<br>
   <a href="#review/${h(activity.id)}/${h(version.id)}">查看补充日期及来源原文</a>
  </div>`};
}

export async function readBrandSupplementDates(adapter,activities,signal){
 const summaries=new Map();
 const unique=[...new Map(activities.map(activity=>[activity.id,activity])).values()];
 await Promise.all(unique.filter(activity=>!activity.startAt||!activity.endAt).map(async activity=>{
  const vid=activity.displayVersionId??activity.currentReviewVersionId;
  if(!SAFE_ID.test(activity.id??'')||!SAFE_ID.test(vid??''))return;
  const read=async id=>(await adapter.request('/activities/'+encodeURIComponent(activity.id)+'/versions/'+encodeURIComponent(id),{signal})).data;
  try{
   const base=await read(vid);
   if(base?.id!==vid||base.activityId!==activity.id)return;
   const latest=base.latestSupplement?.versionId;
   if(!SAFE_ID.test(latest??''))return;
   const version=await read(latest);
   const summary=supplementDatesView(activity,base,version);
   if(summary)summaries.set(activity.id,summary);
  }catch(error){
   if(error.status===401||error.name==='AbortError')throw error;
   // A missing or unreadable supplement must not replace the original dates.
  }
 }));
 return summaries;
}

export function boundSupplement({detail,saved,activity,base,latest}){
 if(!SAFE_ID.test(saved.activityId??'')||!SAFE_ID.test(saved.versionId??'')||
    activity?.id!==saved.activityId||base?.id!==saved.versionId||base.activityId!==activity.id||
    !Number.isInteger(activity.revision)||(activity.displayVersionId??activity.currentReviewVersionId)!==base.id||
    (base.latestSupplement?.versionId??null)!==(latest?.id??null))return null;
 if(latest&&(latest.activityId!==activity.id||latest.snapshot?.supplementBinding?.baseVersionId!==base.id))return null;
 const source=(base.sourceInputs??[]).find(item=>item.submissionId===detail.submissionId&&item.inputRevision===saved.revision);
 const analysis=source?.analyses?.find(item=>item.candidateId===saved.candidateId&&DIGEST.test(item.analysisDigest??'')&&SAFE_ID.test(item.runId??''));
 if(!analysis)return null;
 const storedRun=detail.runs?.find(item=>item.runId===analysis?.runId&&item.inputRevision===saved.revision&&item.analysisDigest===analysis.analysisDigest);
 const version=latest??base;
 const pendingItems=version.currentPendingItems??version.snapshot?.pendingItems;
 if(!storedRun||!analysis||!Array.isArray(pendingItems))return null;
 const originalFields=version.snapshot.resolvedFields??version.snapshot.confirmedFields;
 if(!originalFields)return null;
 const fields={...originalFields,...version.effectiveActivityDates};
 const tierFunding=(version.snapshot.storeBearingPolicy??version.couponReview?.storeBearingPolicy)==='per_tier';
 const visiblePending=tierFunding?pendingItems.filter(item=>item.field!=='storeBearing'):pendingItems;
 return {activity,base,latest,fields,snapshot:{...version.snapshot,pendingItems:visiblePending},analysis,
  source:{submissionId:detail.submissionId,inputRevision:saved.revision,analysisRunId:analysis.runId,
   analysisDigest:analysis.analysisDigest,candidateId:saved.candidateId},
  evidence:(base.evidence??[]).filter(item=>analysis.evidenceReferenceIds?.includes(item.referenceId)),
  materials:base.materials??[]};
}

export function supplementVersionHtml(activity,version){
 if(!activity?.id)return '';
 const latest=version?.latestSupplement;
 const binding=version?.snapshot?.supplementBinding;
 if(!latest&&!binding)return '';
 const path=id=>'#review/'+encodeURIComponent(activity.id)+'/'+encodeURIComponent(id);
 const effective=activity.effectiveExecutionVersionId??activity.effectiveVersionId;
 const effectiveLink=(activity.authorizedVersions??[]).some(item=>item.versionId===effective)?`<a href="${h(path(effective))}">${h(effective)}</a>`:h(effective);
 if(binding){
  return `<div class="b-note"><strong>资料补充版本</strong>
   <p>基准版本 <a href="${h(path(binding.baseVersionId))}">${h(binding.baseVersionId)}</a>。当前显示此补充版本保存的资料。</p>
   ${effective?`<p>当前生效版本 ${effectiveLink}</p>`:''}
   ${version.supplementMaterials?.length?materialHtml(version.supplementMaterials):''}</div>`;
 }
 if(!SAFE_ID.test(latest.versionId??''))return '';
 const pending=pendingItemLabels(latest.pendingItems);
 return `<div class="b-note"><strong>另有资料补充版本</strong>
  <p><a href="${h(path(latest.versionId))}">查看补充版本</a> · ${h(stateLabel(latest.reviewState))}</p>
  ${pending.length?`<details><summary>查看补充版本缺项</summary><p>${h(pending.join('、'))}</p></details>`:''}</div>`;
}

export function supplementHtml(state,{catalog,products,productsForRow,busy,filesHtml,own}){
 const {target,values={},files=[]}=state;
 const {fields,snapshot,analysis}=target;
 const pending=new Set(snapshot.pendingItems.map(item=>item.field));
 const choices=(snapshot.unresolvedRows??[]).map(id=>(analysis.candidate?.productRows??[]).find(row=>row.rowId===id)).filter(Boolean);
 const brands=catalog.brands.filter(item=>!(fields.brandIds??[]).includes(item.id));
 const boundPlatform=catalog.platforms.filter(item=>!fields.platformCode||(item.code??item.id)===fields.platformCode);
 const requirements=(target.base.reviewApprovalRequirements??[]).filter(item=>pending.has(item.field));
 const priority=new Set(requirements.map(item=>item.field));
 const priceMissing=item=>['missing','conflict'].includes(item.status);
 const priceFields=priority.has('subsidyPrices')&&own?subsidyPricesHtml((snapshot.subsidyPrices??[]).filter(priceMissing),{editable:true,values}):'';
 const scope=pending.has('productApplicability')&&(!snapshot.productApplicability||snapshot.productApplicability.kind==='unknown')&&own?
  select('productScopeIntent','商品适用范围声明',(catalog.productScopeIntents??[]).filter(option=>option.code!=='deferred'),values.productScopeIntent,{empty:'请选择适用范围'}):'';
 const store=pending.has('storeBearing')?`<div>${select('storeBearing','门店是否出资',
      [{code:'yes',label:'门店出资'},...(!snapshot.couponReviewInput?.storeFundingRequired?[{code:'no',label:'门店不出资'}]:[])],
      values.storeBearing,{empty:'请选择'})}
      ${snapshot.couponReviewInput?.storeFundingRequired?'<p class="hint">原表含混补券，请确认门店出资。</p>':''}</div>`:'';
 const attention=requirements.length?`<section class="b-manual-attention"><h4>需要原提报人填写</h4>
  ${requirements.map(item=>`<p>${h(item.message)}</p>`).join('')}
  ${own?`<p>补齐后会生成补充版本。新版本进入审核时，从初审开始；原版本保留原审核记录。</p>
  ${priority.has('productApplicability')?`${scope}<p class="hint">部分商品适用，请关联清单中的商品。品牌全部商品适用，请确认品牌及适用平台。</p>`:''}
  ${priority.has('storeBearing')?store:''}${priceFields}`:''}</section>`:'';
 return `<form id="b-group-supplement-form" class="b-candidate selected" aria-labelledby="b-group-supplement-title" novalidate>
  <h3 id="b-group-supplement-title" tabindex="-1">补充本分组 · ${h(fields.activityName??target.activity.name)}</h3>
  ${state.stale?'<p class="b-note">当前活动或补充版本已有变化，已保留输入。</p><button type="button" data-sub-action="reload-group-supplement">重新读取版本并核对</button>':''}
  <fieldset class="b-confirm-fields" ${busy||state.stale?'disabled':''}>
   ${attention}
   <div class="b-form-grid">
    ${pending.has('startAt')&&!fields.startAt?input('startOn','开始日期（北京时间）',values.startOn,{type:'date'}):''}
    ${pending.has('endAt')&&!fields.endAt?input('endOn','结束日期（北京时间）',values.endOn,{type:'date'}):''}
    ${pending.has('rulesText')&&!fields.rulesText?textarea('rulesText','优惠机制',values.rulesText,10000):''}
    ${!fields.sourceActivityId&&['platformCode','sourceActivityId'].some(field=>pending.has(field))?
      select('platformCode','报名编号所属平台',boundPlatform,values.platformCode??fields.platformCode)+
      input('sourceActivityId','平台活动编号',values.sourceActivityId,{maxLength:200}):''}
    ${pending.has('brandIds')?`<div class="span-2"><p class="hint">已核品牌：${h(catalog.brands.filter(brand=>(fields.brandIds??[]).includes(brand.id)).map(brand=>brand.name).join('、')||'未关联品牌，可后补')}</p>
     ${brands.length?brandSelectionHtml(values.addBrandIds,{brands},{name:'addBrandIds'}):'<p class="hint">当前没有可追加的品牌主档。</p>'}</div>`:''}
    ${priority.has('storeBearing')?'':store}
    ${priority.has('productApplicability')?'':scope}
   </div>
   ${choices.length?table(['本组未核商品行','关联有效目录商品'],choices.map(row=>rows([
    h([row.rawInternalCode,row.rawUpc,row.rawName].filter(Boolean).join(' / ')||row.rowId),
    select('product:'+row.rowId,'商品 '+row.rowId,productsForRow?.(row)??products,values['product:'+row.rowId],{empty:'保留未核对'})
   ]))):''}
   ${pending.has('subsidyPrices')?subsidyPricesHtml(priority.has('subsidyPrices')?(snapshot.subsidyPrices??[]).filter(item=>!priceMissing(item)):snapshot.subsidyPrices,{editable:own,values}):''}
   ${textarea('rawText','补充原文或说明',values.rawText,50000)}
   <label>添加本组附件<input id="b-group-files" type="file" multiple accept=".txt,.md,.csv,.tsv,.xlsx,.docx,.png,.jpg,.jpeg,.webp,.pdf,.doc,.xls"></label>
   ${filesHtml(files)}
   <p>原活动 ${h(target.activity.id)} · 基准版本 ${h(target.base.id)}。</p>
   ${supplementVersionHtml(target.activity,target.base)}
   ${target.evidence.length?`<details><summary>查看本分组原文依据</summary>${evidenceList(target.evidence,target.materials)}</details>`:''}
  </fieldset><div class="actions"><button type="button" data-sub-action="close-group-review" ${busy?'disabled':''}>取消</button>
    <button class="primary" ${busy||state.stale?'disabled':''}>${busy?'正在保存…':'保存补充版本'}</button></div></form>`;
}

export function supplementPatch({target,values}){
 const patch={};
 for(const key of ['startOn','endOn','rulesText','rawText']){
  if(values[key]?.trim())patch[key]=values[key];
 }
 const start=patch.startOn??activityDate(target.fields.startAt);
 const end=patch.endOn??activityDate(target.fields.endAt);
 if(start&&end&&end<start)throw new Error('结束日期不能早于开始日期。');
 const {platformCode,sourceActivityId}=values;
 if(sourceActivityId?.trim()){
  if(!platformCode)throw new Error('请为报名编号选择实际所属的平台。');
  patch.platformIdentity={platformCode,sourceActivityId:sourceActivityId.trim()};
 }else if(platformCode&&platformCode!==target.fields.platformCode)throw new Error('请填写该平台的报名编号。');
 if(values.addBrandIds?.length)patch.addBrandIds=values.addBrandIds;
 if(values.productScopeIntent)patch.productScopeIntent=values.productScopeIntent;
 if(values.storeBearing&&target.snapshot.pendingItems?.some(item=>item.field==='storeBearing'))patch.storeBearing=values.storeBearing;
 const products=(target.snapshot.unresolvedRows??[]).filter(rowId=>values['product:'+rowId])
  .map(rowId=>({rowId,decision:'link',productId:values['product:'+rowId]}));
 if(products.length)patch.productResolutions=products;
 const corrections=subsidyCorrections(target.snapshot.subsidyPrices,values);
 if(corrections.length)patch.subsidyPriceCorrections=corrections;
 return patch;
}

export function visibleGroupingCases(run,resolved){
 const cases=run?.groupingCases;
 if(!DIGEST.test(run?.analysisDigest??'')||!Array.isArray(cases))return [];
 return cases.filter(item=>SAFE_ID.test(item.caseId??'')&&Array.isArray(item.affectedCandidateIds)&&
  !item.affectedCandidateIds.some(id=>resolved.has(id))&&Array.isArray(item.units)&&item.units.length>1&&
  item.units.every(unit=>SAFE_ID.test(unit.unitId??'')&&SAFE_ID.test(unit.materialId??'')&&unit.locator&&
   /^[0-9a-f]{64}$/.test(unit.sourceSha256??''))&&new Set(item.units.map(unit=>unit.unitId)).size===item.units.length);
}

export function groupingHtml(state,materials,busy){
 const options=Array.from({length:Math.min(20,state.item.units.length)},(_,i)=>({code:String(i+1),label:`活动组 ${i+1}`}));
 return `<form id="b-grouping-form" class="b-candidate selected" aria-labelledby="b-grouping-title">
  <h3 id="b-grouping-title" tabindex="-1">核对材料归属</h3>
  <p>属于同一场活动的片段选同一组。每个片段都需要分到一个组。</p>
  ${state.stale?'<p class="b-note">当前识别已变化，保留本次选择供核对。请关闭后从最新结果重新选择片段。</p>':''}
  <fieldset class="b-confirm-fields" ${busy||state.stale?'disabled':''}>
   ${table(['原文片段','归属分组'],state.item.units.map(unit=>{
    const file=materials.find(item=>item.materialId===unit.materialId);
    return rows([`<strong>${h(unit.label??'原文片段')}</strong><p>${h(file?.originalName??file?.filename??'原始材料')} · ${h(locatorLabel(unit.locator))}</p>`,
     select('unit:'+unit.unitId,'片段归属',options,state.values[unit.unitId],{empty:'请选择活动组'})]);
   }))}
  </fieldset><div class="actions"><button type="button" data-sub-action="close-group-review" ${busy?'disabled':''}>取消</button>
    <button class="primary" ${busy||state.stale?'disabled':''}>${busy?'正在提交…':'确认分组并重新识别'}</button></div></form>`;
}
export function groupingPartitions({item,values}){
 const groups=new Map();
 const max=Math.min(20,item.units.length);
 for(const unit of item.units){
  const group=Number(values[unit.unitId]);
  if(!Number.isInteger(group)||group<1||group>max)throw new Error('请为每个片段选择归属分组。');
  if(!groups.has(group))groups.set(group,[]);
  groups.get(group).push(unit.unitId);
 }
 return [...groups].sort(([a],[b])=>a-b).map(([,unitIds])=>({unitIds}));
}
