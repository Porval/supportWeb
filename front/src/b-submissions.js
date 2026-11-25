import {h,badge,rows,table,input,textarea,select,panel,pager,errorPanel,stateLabel,beijing,evidenceList,actionAllowed,businessMessage,diagnostics,locatorLabel,TYPE_LABELS} from './b-common.js';
import {activityCategoryLabel,activityDate,datePeriodText,PLATFORM_LABELS,applicablePlatformCodes,hasSubmissionDeclarations,pendingItemLabels,awaitingReview,PRODUCT_SCOPE_INTENT_LABELS} from './dto.js';
import {brandSelectionHtml,fieldsHtml,captureFields,FIELD_LABELS,materialHtml,factsHtml,fieldValueText,submissionDeclarationCatalog,declarationFieldsHtml,declarationRecordHtml,productScopeHint} from './b-fields.js';
import {issueList,conflictsHtml,reviewResultLabel} from './b-business.js';
import {groupReviewCapabilities,localIntakeAgentVersion,boundSupplement,supplementHtml as groupSupplementHtml,supplementPatch,
 visibleGroupingCases,groupingHtml,groupingPartitions} from './b-group-review.js';
import {createPrimaryRecognitionController as createModelRecognitionController,primaryRecordHtml as modelRecognitionRecordHtml,primaryFields,primaryBinding,primaryGroupFor,primaryCandidate,primaryPresentation,agentRecognitionRecord,agentRecognitionBinding,agentCandidateMetadata,localFailureTitle,MODEL_INTAKE_CONTRACT,ACTIVITY_METHODS} from './intake-primary-recognition.js';
import {submissionProductsHtml,couponTiersHtml} from './ui.js';
import {subsidyPricesHtml,subsidyCorrectionValues,subsidyCorrections} from './b-subsidy-prices.js';

const ACTIVE=new Set(['queued','running','cancel_requested']);
const STORE_BEARING_OPTIONS=[{code:'yes',label:'门店出钱'},{code:'no',label:'门店不出钱'}];
const fundingNoteName=q=>'couponFunding:'+encodeURIComponent(q.tierId)+':'+encodeURIComponent(q.sourceTierKey);
const fundingLabel=q=>[PLATFORM_LABELS[q.platformCode]??q.platformLabel??'平台待核对',q.name??'未命名券档'].join(' · ');
const unboundFunding=q=>Boolean(q.blockedReason||!q.tierId||!q.sourceTierKey);
const explicitMix=(...values)=>values.some(value=>
 /(?:^|[\s【（(：:、，,；;。]|[两一二三四234]方)(?:混补|共补)/.test(value??''));
function requiresStoreBearing(method,rules,name){
 return /^(?:category|brand)_threshold_subsidy_/.test(method??'')||
  !['shen_jia','bao_hao_jia'].includes(method)&&explicitMix(rules,name)||
  (!method||method==='other')&&/满减|满\s*[¥￥]?\s*\d+(?:\.\d+)?\s*元?\s*[减\-－]\s*[¥￥]?\s*\d|^\s*\d+(?:\.\d+)?\s*[-－]\s*\d+(?:\.\d+)?\s*$/.test(rules??'');
}
const hasCouponScope=(method,rules)=>requiresStoreBearing(method,rules)||method==='platform_red_packet'||
 (!['shen_jia','bao_hao_jia'].includes(method)&&/品类券|同享券|联合券/.test(rules??''));
const scopeOptions=Object.entries(PRODUCT_SCOPE_INTENT_LABELS).filter(([code])=>code!=='deferred')
 .map(([code,label])=>({code,label}));
const REPORT_BAD_CASE='submissions.report_bad_case';
const GROUPING_RETRY_STATES=new Set(['failed','cancelled','interrupted']);
const GROUPING_RETRY_PREFIX='grouping-retry:';
const GROUPING_ID=/^[A-Za-z0-9_-]{1,128}$/;
const ANALYSIS_DIGEST=/^sha256:[0-9a-f]{64}$/;
const ALLOWED=/\.(txt|md|csv|tsv|xlsx|docx|png|jpe?g|webp|pdf|doc|xls)$/i;
const LABELS={exact:'精确匹配',manual_selected:'人工已选',candidate:'待人工选择候选',unmatched:'未匹配',conflict:'存在冲突'};
const QUICK_FIELDS=['activityName','type','platformCode','brandIds','rulesText','sourceActivityId','startAt','endAt','brandBenefit','remark'];
const CONFLICT_FIELDS=[...QUICK_FIELDS.slice(0,8),'companyBearing'];
const valuePresent=value=>Array.isArray(value)?value.length>0:typeof value==='string'?value.trim().length>0:value!=null;
const DELETE_CONFLICTS={
 REVISION_CHANGED:'提报修订已变化，请按最新列表核对。',
 ANALYSIS_ACTIVE:'提报正在分析或取消分析，请在结束后查看。',
 HAS_RESOLUTION:'提报已有活动处理结果，请查看最新状态。',
 HAS_SOURCE_REFERENCE:'提报已被活动版本引用，请查看最新状态。',
 HAS_CHILD_SUBMISSION:'提报已有后续关联提报，请查看最新状态。',
 SUBMISSION_DELETED:'该提报已删除，请查看最新列表。'
};
const ACTIVITY_DATE_FIELDS={startAt:'开始日期',endAt:'结束日期'};
function couponPeriodHtml(period){
 if(!period)return '';
 const days=period.startOn&&period.endOn
  ?datePeriodText(period.startOn,period.endOn)
  :datePeriodText(null,null,{start:{monthDay:period.startMonthDay},end:{monthDay:period.endMonthDay}});
 const note=period.status==='year_inferred'?'年份按提报时间推定，可修改'
  :period.status==='year_unconfirmed'?'年份待确认':'';
 const origin=locatorLabel(period.source?.locator);
 return `<p class="hint" data-coupon-period>活动日期 ${h(days)}${note?' · '+h(note):''}${origin?' · 原表 '+h(origin):''}</p>`;
}
const NAME_PROBLEM={field:'activityName',text:'缺少活动名称。',hints:['必填 · 填写本次活动的名称。']};
const PRODUCT_GUIDANCE='写明是全部商品、仅指定商品、无需指定商品，或商品清单后续补上。指定商品可写商品名或 UPC；整单券请写已知适用条件。';
const supplementLabel=field=>({platformCode:'适用平台',rulesText:'优惠机制',sourceActivityId:'平台活动编号'}[field]??FIELD_LABELS[field]);
const normalizeLines=value=>String(value??'').replace(/\r\n?/g,'\n');
const actionButton=(action,label,{primary=false,disabled=false,extra=''}={})=>
 `<button type="button" data-sub-action="${action}"${primary?' class="primary"':''}${disabled?' disabled':''}${extra}>${h(label)}</button>`;

function analysisWaitLabel(run){
 const createdAt=Date.parse(run?.createdAt??run?.startedAt);
 if(!Number.isFinite(createdAt))return '';
 const seconds=Math.max(0,Math.floor((Date.now()-createdAt)/1000));
 const minutes=Math.floor(seconds/60);
 return `已等待 ${minutes?`${minutes} 分 `:''}${seconds%60} 秒`;
}

function materialProblem(run,source,materials,reasons){
 if(!run?.result)return null;
 const docs=run.materialResults??[];
 const attachments=new Set(source?.materialIds??[]);
 const names=new Map(materials.map(item=>[item.materialId,item.originalName??item.filename]));
 const affected=[];
 const listed=new Set();
 const issueCodes=doc=>new Set([...(doc.errors??[]),...(doc.warnings??[])].map(issue=>issue.code));
 for(const doc of docs){
  if(!attachments.has(doc.materialId)||listed.has(doc.materialId))continue;
  const codes=issueCodes(doc);
  let symptom;
  if(doc.status==='unsupported')symptom='当前格式暂不支持识别。可换成受支持的附件，或摘录原文。';
  if(doc.status==='invalid'||doc.status==='failed')symptom=codes.has('LIMIT_EXCEEDED')
   ?'文件超过读取上限。可拆分附件，或摘录相关内容。':'文件内容读取失败。可重新导出并上传，或摘录原文。';
  if(doc.status==='partial')symptom=codes.has('LIMIT_EXCEEDED')&&doc.tableRows?.length
   ?'表格只读取了部分行。请补上相关的未读取行。':'已读取部分内容。请补上本次活动相关的未读取内容。';
  if(symptom){
   affected.push(`${names.get(doc.materialId)||'附件名称未记录'} · ${symptom}`);
   listed.add(doc.materialId);
  }
 }
 const globalLimit=(run.result.warnings??[]).some(issue=>issue.code==='LIMIT_EXCEEDED');
 const unidentified=docs.some(doc=>doc.status==='partial'&&!listed.has(doc.materialId))||
  (reasons.has('PARSE_COVERAGE_UNCONFIRMED')&&!affected.length);
 if(!affected.length&&!globalLimit&&!unidentified)return null;
 if(globalLimit)affected.push('本次提取达到处理上限。请分开上传相关部分，或在这里写明遗漏内容。');
 else if(unidentified){
  const parsed=docs.filter(doc=>doc.status==='parsed'&&doc.kind!=='form'&&doc.materialId!==source?.formMaterialId);
  const readAttachments=parsed.filter(doc=>attachments.has(doc.materialId)&&names.get(doc.materialId))
   .map(doc=>`「${names.get(doc.materialId)}」${doc.tableRows?.length?`（${doc.tableRows.length} 行表格内容）`:''}`);
  const readStatus=readAttachments.length?`${readAttachments.join('、')}已读取，`:parsed.length?'原文或附件已读取，':'';
  affected.push(`${readStatus}本次内容尚未完整提取。未标出的遗漏暂时无法定位，可对照原件补充。`);
 }
 return {field:'rawAppend',text:listed.size?'部分附件读取或识别受限。':'材料内容尚未完整提取。',hints:[...affected,
  '请对照原件，补上本次未识别的活动规则或商品范围。如已知，可注明附件名、页码或表格行。']};
}

function explicitShenJia(run,candidate,source){
 const method=agentCandidateMetadata(run,candidate?.candidateId)?.activityMethod;
 const platform=candidate?.fields?.platformCode;
 return method?.status==='explicit'&&method.value==='shen_jia'&&platform?.status!=='conflict'&&
  (platform?.value==='meituan'||(!platform?.value&&applicablePlatformCodes(source).includes('meituan')))?method:null;
}

function unassignedAgentProducts(run,candidate){
 if(!candidate?.productRows?.length||run?.result?.candidates?.length<2||
    agentCandidateMetadata(run,candidate.candidateId)?.activityMethod?.status!=='missing')return false;
 return ['activityName','sourceActivityId','type','rulesText'].every(key=>candidate.fields?.[key]?.status==='missing');
}

function identifiedRule(run,candidate,materials,source,analysis=run?.result,{showEvidence=true,observed=null}={}){
 const field=candidate.fields?.rulesText;
 const refs=new Set(field?.evidenceIds??[]);
 const evidence=(analysis?.evidence??[]).filter(item=>refs.has(item.evidenceId));
 const schedule=run?.couponSchedule;
 if(schedule?.candidateId===candidate.candidateId){
  return `<details><summary>查看 ${schedule.tiers.length} 档子券</summary>${couponTiersHtml(schedule)}</details>
   ${showEvidence?ruleEvidenceHtml(evidence,materials):''}`;
 }
 if(field?.status==='conflict')return `<p>优惠机制：有不同记录，待核对。</p>${showEvidence?ruleEvidenceHtml(evidence,materials):''}`;
 if(!valuePresent(field?.value)){
  if(observed?.summary)return `<div class="b-identified-rule"><strong>原表优惠机制${observed.status==='conflict'?'（待核对）':''}</strong><p>${h(observed.summary)}</p>${ruleEvidenceHtml(observed.evidence??[],materials)}</div>`;
  const method=explicitShenJia(run,candidate,source);
  if(method){
   const methodRefs=new Set(method.evidenceIds??[]);
   const methodEvidence=(analysis?.evidence??[]).filter(item=>methodRefs.has(item.evidenceId));
   return `<p>美团神价</p>${showEvidence?ruleEvidenceHtml(methodEvidence,materials):''}`;
  }
  return unassignedAgentProducts(run,candidate)?'<p class="hint">商品归属待核对。</p>':'<p>优惠机制：待补充。</p>';
 }
 const label=evidence.length?'优惠机制':'优惠机制（依据待核对）';
 const lines=String(field.value).split(/。|\r?\n/).map(line=>line.trim()).filter(Boolean);
 return `<div class="b-identified-rule"><strong>${label}</strong><p>${h(lines[0])}${lines.length>1?'。':''}</p>
  ${lines.length>1?`<details><summary>查看 ${lines.length-1} 条优惠明细</summary><ul>${lines.slice(1).map(line=>`<li>${h(line)}</li>`).join('')}</ul></details>`:''}
  ${showEvidence?ruleEvidenceHtml(evidence,materials):''}</div>`;
}
function ruleEvidenceHtml(evidence,materials){
 return evidence.length?`<details><summary>查看优惠机制原文依据</summary>${evidenceList(evidence,materials)}</details>`:'';
}

function couponParticipation(analysis){
 const states=new Map();
 const evidence=new Map((analysis?.evidence??[]).map(ref=>[ref.evidenceId,ref]));
 const sheets=new Map();
 for(const doc of analysis?.materialResults??[]){
  if(doc.kind!=='xlsx'||doc.status!=='parsed')continue;
  const bySheet=new Map();
  for(const row of doc.tableRows){
   const name=row.locator.sheet;
   if(!bySheet.has(name))bySheet.set(name,[]);
   bySheet.get(name).push(row);
  }
  for(const [name,rows] of bySheet){
   const header=rows[0];
   const labels=new Map(header.cells.map(cell=>[cell.text.trim().toLowerCase(),cell.column]));
   if(!['活动力度','upc','商品名称','商品码','报名情况'].every(label=>labels.has(label)))continue;
   const column=labels.get('报名情况');
   sheets.set(JSON.stringify([doc.materialId,name]),new Map(rows.slice(1)
    .map(row=>[row.locator.row,row.cells.find(cell=>cell.column===column)?.text.trim()||'未标注'])));
  }
 }
 for(const candidate of analysis?.candidates??[]){
  const values=new Map();
  for(const row of candidate.productRows??[]){
   const locations=new Map();
   for(const id of row.evidenceIds??[]){
    const ref=evidence.get(id);
    const locator=ref?.locator;
    if(locator?.kind!=='xlsx')continue;
    const key=JSON.stringify([ref.materialId,locator.sheet]);
    const value=sheets.get(key)?.get(locator.row);
    if(value)locations.set(JSON.stringify([key,locator.row]),value);
   }
   if(locations.size===1)values.set(row.rowId,locations.values().next().value);
  }
  if(values.size)states.set(candidate.candidateId,values);
 }
 return states;
}

function participationSummary(states){
 if(!states?.size)return '';
 const counts=new Map();
 for(const value of states.values())counts.set(value,(counts.get(value)??0)+1);
 const labels=['参加','未标注',...Array.from(counts.keys()).filter(value=>value!=='参加'&&value!=='未标注')];
 return `<p class="hint">原表报名情况 · ${states.size} 条商品 · ${labels.filter(value=>counts.has(value)).map(value=>`${counts.get(value)} 条${h(value)}`).join(' · ')}</p>`;
}


function excelLimitMessage(run,doc){
 if(doc.kind!=='xlsx'||!['partial','invalid'].includes(doc.status))return '';
 const issue=[...(doc.errors??[]),...(doc.warnings??[])].find(item=>item.code==='LIMIT_EXCEEDED');
 if(!issue)return '';
 const detail=run?.materialReadDiagnostics?.find(item=>item.materialId===doc.materialId&&item.sha256===doc.sha256);
 return detail?.message||(issue.message&&issue.message!=='Invalid input'?issue.message:
  'Excel 内容超过读取上限，这份附件的明细没有进入 Agent 识别。');
}

export function localReadFailed(run){
 return localFailureTitle(run)==='服务端读取附件失败';
}

export function excelReadLimitHtml(run,source,materials){
 if(!localReadFailed(run))return '';
 const attachments=new Set(source?.materialIds??[]);
 const byId=new Map(materials.map(item=>[item.materialId,item]));
 const failures=(run.materialResults??[]).filter(doc=>attachments.has(doc.materialId)).map(doc=>{
  const message=excelLimitMessage(run,doc);
  const material=byId.get(doc.materialId);
  const uploaded=Number.isFinite(material?.size)?`原件约 ${Math.max(1,Math.round(material.size/1000))} KB，已上传。`:'';
  return message?`<li><strong>${h(material?.originalName??material?.filename??'Excel 附件')}</strong> · ${h(uploaded+message)}</li>`:'';
 }).filter(Boolean);
 if(!failures.length)return '';
 return `<div class="warning" role="alert"><strong>本次读取时 Excel 附件超过上限</strong>
  <ul>${failures.join('')}</ul><p>当前每份 Excel 支持 5,000 条明细（另加一行表头），最多 2,000,000 个字符。</p></div>`;
}

function agentFailureReason(run,capability){
 const record=agentRecognitionRecord(run);
 if(record?.status!=='failed')return '';
 const code=run.error?.code??record.reasonCode;
 const details=run.error?.details;
 if(['AGENT_MODEL_MODEL_RECOGNITION_PROVIDER_AUTH','AGENT_MODEL_PROVIDER_AUTH'].includes(code)){
  if(capability?.authenticationStatus==='authorized')
   return '上次 DeepSeek 鉴权失败。当前配置已通过鉴权，可在这里重新识别，原材料已保留。';
  return 'DeepSeek 鉴权失败，原材料已保留。请联系管理员更新密钥，通过鉴权后刷新本页，再重新识别。';
 }
 if(localFailureTitle(run))return code==='LOCAL_MATERIAL_OUTPUT_LIMIT'?run.error.message:
  localReadFailed(run)?'服务端未能完整读取附件，材料尚未发送给 Agent。':(run.error.message+'。材料尚未发送给 Agent。');
 if(details?.missingRowCount>0||code==='AGENT_MODEL_MODEL_RECOGNITION_ROW_COVERAGE_INCOMPLETE')
  return 'Agent 回传的商品或活动行不完整，识别结果未通过核对。';
 if(['AGENT_MODEL_MODEL_RECOGNITION_EMPTY_CONTENT','AGENT_MODEL_MODEL_RECOGNITION_JSON_INVALID'].includes(code))
  return 'Agent 没有返回可读取的识别结果。';
 if(code==='AGENT_MODEL_MODEL_RECOGNITION_INVALID_OUTPUT'&&details?.stage==='evidence'){
  const field=FIELD_LABELS[details.field];
  return field?`Agent 回传的「${field}」未通过核对。`:'Agent 回传的字段或原文依据未通过核对。';
 }
 if(code==='AGENT_MODEL_MODEL_RECOGNITION_TIMEOUT'||code==='AGENT_TIMEOUT')return '识别超时，未收到完整结果。';
 if(details?.reason==='provider_truncated_output')return 'Agent 回传的结果被截断，未收到完整信息。';
 if(code==='AGENT_MODEL_MODEL_RECOGNITION_PROVIDER_UNAVAILABLE')return 'Agent 服务暂时无法连接，未收到识别结果。';
 return businessMessage(run.error,'没有可用于建档的完整识别结果，暂时无法确认原因。');
}

function hasMaterialBrandWithoutAgentResult(run,candidate){
 if(!agentRecognitionBinding(run)||run.result.candidates.length!==1||!candidate?.productRows?.length)return false;
 const brand=agentCandidateMetadata(run,candidate.candidateId)?.brandNames;
 if(brand?.status!=='missing'||valuePresent(candidate.fields?.brandIds?.value))return false;
 const productEvidence=new Set(candidate.productRows.flatMap(row=>row.evidenceIds??[]));
 const productLocations=new Set((run.result.evidence??[]).filter(ref=>productEvidence.has(ref.evidenceId)&&ref.locator?.kind==='xlsx')
  .map(ref=>JSON.stringify([ref.materialId,ref.locator.sheet,ref.locator.row])));
 return (run.result.materialResults??[]).some(doc=>{
  if(doc.kind!=='xlsx'||doc.status!=='parsed')return false;
  const columns=new Map();
  for(const row of doc.tableRows){
   const sheet=row.locator.sheet;
   if(!columns.has(sheet)){
    columns.set(sheet,row.cells.find(cell=>cell.text.trim()==='品牌')?.column);
    continue;
   }
   const column=columns.get(sheet);
   if(column&&productLocations.has(JSON.stringify([doc.materialId,sheet,row.locator.row]))&&
      row.cells.some(cell=>cell.column===column&&cell.text.trim()))return true;
  }
  return false;
 });
}

function savedDetailsHtml(saved){
 const pending=pendingItemLabels(saved?.pendingItems);
 return (pending.length?`<p>待补充：${h(pending.join('、'))}</p>`:'')+
  (awaitingReview(saved?.reviewState,saved?.reviewBlockedReason)?'<p>待安排审核</p>':'');
}
export function recognizedGroupBrands(run,candidateId,catalog,group){
  const field=agentCandidateMetadata(run,candidateId)?.brandNames;
  const explicit=field?.status==='explicit'&&Array.isArray(field.value)?field.value:[];
  const directory=group?.productBrandEvidence??[];
  const rows=new Map((group?.rows??[]).map(row=>[row.rowId,row]));
  const key=name=>String(name??'').normalize('NFKC').trim().toLowerCase();
  const names=new Map();
  for(const name of explicit)if(key(name))names.set(key(name),name);
  const fromAgent=new Set(names.keys());
  const ids=new Set();
  const allowed=new Set((catalog?.brands??[]).map(brand=>brand.id));
  const conflicted=new Set(directory.filter(item=>item.masterStatus==='conflict').map(item=>key(item.directoryBrand)));
  for(const item of directory){
   const sourceKey=key(item.directoryBrand);
   if(fromAgent.size&&!fromAgent.has(sourceKey))continue;
   if(!conflicted.has(sourceKey)&&item.evidenceStatus==='product_identity_verified'&&item.masterStatus==='matched'&&allowed.has(item.brandId))ids.add(item.brandId);
   const label=key(item.directoryBrand);
   // A uniquely found UPC and matching original name support the brand label even if the SKU needs review.
   const grounded=item.evidenceStatus==='product_candidate'&&item.products?.some(p=>
    rows.get(p.rowId)?.uniqueUpcHit===true&&(
     key(rows.get(p.rowId)?.rawName).startsWith(label+' ')||
     (/^[\p{Script=Han}]{2,}$/u.test(label)&&key(rows.get(p.rowId)?.rawName).startsWith(label))));
   if(label&&grounded)names.set(label,item.directoryBrand);
  }
  if(field?.status!=='conflict'){
   for(const label of names.keys()){
    const master=(catalog?.brands??[]).filter(brand=>key(brand.name)===label);
    if(master.length===1&&!conflicted.has(label))ids.add(master[0].id);
   }
  }
  const candidate=run?.result?.candidates?.find(item=>item.candidateId===candidateId);
  const conflict=field?.status==='conflict'||candidate?.fields?.brandIds?.status==='conflict';
  const matches=conflict?[]:(catalog?.brands??[]).filter(b=>ids.has(b.id));
  const matchedNames=new Set(matches.map(b=>key(b.name)));
  return {ids:matches.map(b=>b.id),matched:matches.map(b=>b.name),names:[...names.values()],
   unresolved:[...names].filter(([label])=>!matchedNames.has(label)).map(([,name])=>name),conflict};

 }

export function intakeProblems(run,source,candidate,reasons,catalog,edits={},recognized=recognizedGroupBrands(run,candidate?.candidateId,catalog)){
 const projected=primaryPresentation(run,candidate?.candidateId,source?.form,edits);
 const result=projected.analysis;
 candidate=projected.candidate??candidate;
 if(!result)return valuePresent(source?.form?.activityName)?[]:[NAME_PROBLEM];
 const candidates=result.candidates??[];
 const fields=candidate?.fields??{};
 const form=candidates.length<=1?{...source?.form,...edits}:{};
 const missing=key=>!valuePresent(fields[key]?.value)&&!valuePresent(form[key])&&
  (fields[key]?.status==='missing'||candidates.length<=1&&(result.missingFields??[]).some(item=>item.field===key));
 const type=fields.type?.status==='conflict'?null:fields.type?.value??form.type;
 const tierFunding=form.type==='brand'&&type==='brand';
 const fundingHint=tierFunding?'如有补充，请写明对应券档的承担方和原件出处。':'如已明确，请写明承担方及原文依据，也可在本组人工确认时核对。';
 const declarations=Boolean(submissionDeclarationCatalog(catalog));
 const output=new Map();
 if(candidates.length<=1&&!valuePresent(form.activityName)&&!valuePresent(fields.activityName?.value))output.set('activityName',NAME_PROBLEM);
 const add=(object,text,field=null,...hints)=>{if(!output.has(object))output.set(object,{kind:object,text,field,hints})};

 for(const field of ['platformCode','sourceActivityId',...CONFLICT_FIELDS.filter(key=>!['platformCode','sourceActivityId'].includes(key))]){
  const observed=(result.conflicts??[]).find(item=>item.field===field&&
   (candidates.length<=1||item.evidenceIds?.some(id=>fields[field]?.evidenceIds?.includes(id))));
  if(!observed&&fields[field]?.status!=='conflict')continue;
  // Local title differences stay in source details; they are not a first-screen blocker.
  if(field==='activityName'&&!run?.agentRecognition&&primaryGroupFor(run,candidate?.candidateId)?.fields?.activityName?.status!=='conflict')continue;
  const label=declarations&&field==='platformCode'?'报名编号所属平台':supplementLabel(field);
  const left=observed?.manualValue;
  const right=observed?.extractedValues??[];
  const values=left!=null&&right.length
   ?`（原表单：${fieldValueText(field,left,catalog).slice(0,120)}，材料：${right.map(value=>fieldValueText(field,value,catalog)).join('、').slice(0,160)}）`:'';
  if(field==='companyBearing'){
   add(field,`公司承担有不同记录${values}。`,'rawAppend',fundingHint);
  }else{
   add(field,`${label}有不同记录${values}。`,field,
    field==='activityName'?'必填 · 核对原表单和材料，填写本次活动的名称。':`可补 · 核对原表单和材料，按本次活动实际情况更正${label}。`);
  }
 }
 const unassigned=unassignedAgentProducts(run,candidate);
 if(unassigned)add('productGroup','商品归属待核对。');
 else if((candidates.length>1||valuePresent(form.activityName))&&missing('activityName')){
  add('activityName','本分组需填写活动名称。');
 }
 const needsManualIdentity=reasons.has('BRAND_PLATFORM_SELECTION_REQUIRED')&&type==='brand';
 if(declarations){
  if(needsManualIdentity&&!applicablePlatformCodes(source).length)add('applicablePlatforms','适用平台待补充。');
  if(valuePresent(fields.sourceActivityId?.value??form.sourceActivityId)&&missing('platformCode'))
   add('platformCode','报名编号所属平台待核对。','platformCode','可补 · 按原件选择该报名编号实际所属的平台。');
 }else if(missing('platformCode')||(needsManualIdentity&&!valuePresent(fields.platformCode?.value)&&!valuePresent(form.platformCode))){
  add('platformCode','适用平台待补充。','platformCode','可补 · 选择本次活动适用的平台。');
 }
 if(type==='brand'&&!valuePresent(form.brandIds)&&!valuePresent(fields.brandIds?.value)&&(missing('brandIds')||needsManualIdentity)){
  if(recognized.conflict)add('brandIds','品牌有冲突，请核对本组关联品牌。','brandIds');
  else if(recognized.unresolved.length)add('brandIds','请核对品牌主档：'+recognized.unresolved.join('、')+'。','brandIds');
  else if(recognized.matched.length&&recognized.ids.length<=20)
   add('brandConfirmation','已识别'+recognized.matched.join('、')+'，待确认关联。');
  else add('brandIds','关联品牌待补充。','brandIds','可补 · 从品牌目录选择本次活动涉及的品牌。');
 }
 if(missing('rulesText')&&!unassigned&&!explicitShenJia(run,candidate,source))add('rulesText','优惠机制待补充。','rulesText','可补 · 写明已知的优惠形式、使用门槛和限制条件。');
 if(candidates.length===1&&reasons.has('PARSE_COVERAGE_UNCONFIRMED')){
  for(const [field,hint] of [
   ['startAt','按原件填写活动开始日期。'],['endAt','按原件填写活动结束日期。'],
   ['sourceActivityId','填写平台给出的活动编号。']
  ]){
   if(missing(field))add(field,`${supplementLabel(field)}待补充。`,field,`可补 · ${hint}`);
  }
 }

 if(['DUPLICATE_AMBIGUOUS','DUPLICATE_SCOPE_UNCONFIRMED','SOURCE_CONFLICT'].some(code=>reasons.has(code))){
  add('duplicate','与已有活动的关系待核对。','rawAppend','如与已有活动有关，请写明已知的平台活动编号或本次条件与原活动的差异。');
 }
 const unmatched=(candidate?.productRows??[]).filter(row=>!row.matchedProductId||!['exact','manual_selected'].includes(row.matchKind)).length;
 if(unmatched){
  if(declarations&&source?.productScopeIntent==='no_specific_products')add('products',`仍有 ${unmatched} 条原材料商品记录待匹配。`);
  else add('products',`有 ${unmatched} 条商品记录待匹配。`,'rawAppend',
   '请写明待匹配商品的商品名、内部码或 UPC（填写已知的即可）。');
 }
 if(['PRODUCT_SCOPE_UNCONFIRMED','PRODUCT_SCOPE_CONFLICT','PRODUCT_BRAND_CONFLICT','PRODUCT_IDENTITY_CONFLICT'].some(code=>reasons.has(code))){
  if(declarations)add('productScope','商品适用范围待核对。');
  else add('productScope','商品适用范围待确认。','rawAppend',PRODUCT_GUIDANCE);
 }
 if(reasons.has('BEARING_EVIDENCE_UNVERIFIED'))add('companyBearing','公司承担依据待核对。','rawAppend',fundingHint);
 if(!candidates.length){
  add('noGroup','未识别出可核对的活动分组。','rawAppend','请按活动名称分段，写明各活动的平台、优惠规则和适用范围。只有一场活动时也请写清名称。');
 }else if(!output.size){
  add('check','可打开本组确认，核对已识别的活动信息。');
 }
 return [...output.values()];
}
export function createSubmissionsController(ctx){
 const activityIntake=()=>ctx.activityIntake??ctx.localIntake;
 let catalog={brands:[],platforms:[]},products=[],list=null,detail=null,run=null,params={},route='',signal,timer=null,busy=false,error=null;
 let waitTimer=null;
 let catalogError=null,autoCapability=null,autoStatus=null,autoBusy=false,nameError=false,deleteTarget=null;
 let brandSearch='',brandEditor=null,brandNotice='',quickEdit=null;
 let primaryRefreshPending=false;
 let agentCapability=null;
 const agentFirst=()=>Boolean(agentCapability);
 const localRecognition=()=>catalog?.activityRecognitionMode==='local';
 const currentBadCase=()=>detail?.state==='failed'&&detail.agentBadCase?.analysisRunId===run?.runId?detail.agentBadCase:null;
 const groupBadCase=id=>(detail?.agentBadCases??(detail?.agentBadCase?[detail.agentBadCase]:[]))
  .find(item=>item.analysisRunId===run?.runId&&(!item.candidateId||item.candidateId===id));
 const hasGroupFailures=()=>(detail?.agentBadCases??[]).some(item=>item.analysisRunId===run?.runId);
 const canReportBadCase=id=>agentFirst()&&actionAllowed(detail,REPORT_BAD_CASE)&&
  (detail.reportableBadCaseCandidateIds?.includes(id)??(!hasDecision()&&!multipleCandidates()))&&
  run?.inputRevision===detail?.revision&&Boolean(run?.analysisDigest)&&!groupBadCase(id)&&
  !unknownOutcome()&&!acceptedAhead()&&!analyzing();
 const agentAvailable=()=>agentCapability?.contractVersion===MODEL_INTAKE_CONTRACT&&agentCapability.provider==='deepseek'&&agentCapability.enabled===true;
 const currentAgentBinding=()=>run?.inputRevision===detail?.revision?agentRecognitionBinding(run):null;
 const analysisBody=(revision,previous)=>({expectedRevision:revision,...(previous?{expectedPreviousAnalysisRunId:previous.runId}:{}),...(agentFirst()?{agentConsent:true}:{})});
 const agentAvailabilityMessage=()=>{
  if(agentCapability?.authenticationStatus==='rejected')return 'DeepSeek 鉴权失败，请联系管理员更新密钥后刷新。';
  if(agentCapability?.configurationStatus==='unavailable')return 'DeepSeek 服务端配置不可用，请联系管理员检查配置后刷新。';
  return 'DeepSeek Agent 当前不可用，请稍后刷新再提交。';
 };
 const agentConsentHtml=()=>agentFirst()?`<p class="b-note" data-agent-consent>${agentAvailable()?'点击提交、保存补充或重新识别，即将本次表单、文字和附件中可读取的内容交给 DeepSeek Agent 识别，返回活动分组、字段和商品清单供你核对。':h(agentAvailabilityMessage())+(agentCapability.message?' '+h(agentCapability.message):'')}</p>`:'';
 const ensureAgentAvailable=()=>{if(agentFirst()&&!agentAvailable())throw new Error(agentAvailabilityMessage())};
 const agentStatusText=()=>{
  if(localFailureTitle(run))return localFailureTitle(run);
  if(run?.state==='running'&&['starting','decoding','local_planning'].includes(run.phase))return '服务端正在准备提报材料';
  if(run?.state==='cancel_requested')return '正在取消 Agent 识别';
  const record=agentRecognitionRecord(run);
  if(!record)return '尚未完成 Agent 识别';
  if(record.status==='succeeded')return currentAgentBinding()?'Agent 识别完成 · 请核对后建档':'Agent 结果待核实，请重新识别';
  return {queued:'Agent 识别已排队',running:'Agent 正在识别',failed:'Agent 识别失败',cancelled:'Agent 识别已取消',interrupted:'Agent 识别已中断'}[record.status]??'Agent 状态待核实';
 };
 const agentSourceHtml=()=>{
  if(localRecognition()&&run?.result?.engine==='deterministic_local_v1')
   return '<p class="b-note">本次在服务端解析活动材料。请核对识别结果后建档。</p>';
  if(!agentFirst()||localFailureTitle(run))return '';
  const record=agentRecognitionRecord(run);
  if(run?.state==='running'&&['starting','decoding','local_planning'].includes(run.phase))return '';
  const source=run?.localReplay?.parentRunId?'本次使用原识别回执在本地重新归组。':record?'本次由 DeepSeek Agent 识别。':run?'以下保留历史分析来源，尚未完成当前流程的 Agent 识别。':'材料保存后将交给 DeepSeek Agent 识别。';
  return `<p class="b-note" data-agent-status>${h(source)}${record&&['failed','cancelled','interrupted'].includes(record.status)?' 原始材料仍保留，可补充或重试；本次没有可用于建档的完整识别结果。':''}</p>`;
 };
 let draft={form:{},originalDates:{},rawText:'',manualProductRows:[]},files=[],existingIds=[],parent=null,editing=false,createSlot='create:'+crypto.randomUUID(),candidateId=null,confirmation=null,targetContext=null;
 let declarationErrors={},dateError=false;
 let batchPreview=null,batchReceipt=null;
 const batchEligible=()=>route==='report'&&activityIntake()&&!editing&&!parent&&existingIds.length===0&&
  files.length===1&&/\.xlsx$/i.test(files[0].file.name);
 function batchHtml(){
  if(batchReceipt){
   const rowsHtml=batchReceipt.submissions.map(item=>rows([h(item.sourceActivityId),h(item.activityName),
    h(item.productRowCount),`<a href="#submissions/${h(item.submissionId)}">查看提报</a>`]));
   return panel('已拆分保存',`<p>已保存 ${batchReceipt.submissions.length} 条提报，共 ${batchReceipt.productRowCount} 条商品明细。</p>
    ${table(['主神价 ID','活动名称','商品行数','提报'],rowsHtml)}
    <p class="hint">可进入单场提报，按需交给 Agent 识别。</p>`);
  }
  if(!batchEligible()||batchPreview?.status!=='ready')return '';
  const rowsHtml=batchPreview.activities.map(item=>rows([h(item.sourceActivityId),h(item.activityName),
   h(datePeriodText(item.startDate,item.endDate)),h(item.productRowCount)]));
  return panel('合并神价表拆分预览',`<p>已识别 ${batchPreview.activities.length} 场神价活动，共 ${batchPreview.productRowCount} 条商品明细。</p>
   ${table(['主神价 ID','活动名称','活动日期','商品行数'],rowsHtml)}
   <p class="hint">每场以表内资料为准。共用表单里的补充内容可在各场提报里分别填写。确认后保存提报，再按场交给 Agent 识别。</p>
   <button class="primary" type="button" data-sub-action="save-shenjia-batch" ${busy?'disabled':''}>确认拆分并保存提报</button>`);
 }
 async function inspectShenjiaBatch(requestSignal){
  if(!batchEligible())return false;
  const item=files[0];
  const id=await upload(item);
  if(requestSignal!==signal||requestSignal.aborted)return true;
  const {data}=await ctx.adapter.request('/materials/'+encodeURIComponent(id)+'/shenjia-preview',{signal:requestSignal});
  if(requestSignal!==signal||requestSignal.aborted)return true;
  batchPreview=data?.status==='ready'?data:{status:'single_or_unsupported'};
  return batchPreview.status==='ready';
 }
 async function saveShenjiaBatch(){
  if(!batchEligible()||batchPreview?.status!=='ready')return;
  const requestSignal=signal;
  const {status,...expected}=batchPreview;
  const sourceMaterialId=files[0].materialId;
  busy=true;error=null;paint();
  try{
   const env=await ctx.commands.run('shenjia:'+sourceMaterialId,'submissions.shenjia_batch','POST',
    '/submissions/shenjia-batch',{sourceMaterialId,expected},{signal:requestSignal});
   if(requestSignal!==signal||requestSignal.aborted)return;
   const value=env.data;
   if(value?.sourceMaterialId!==sourceMaterialId||value.sourceSha256!==expected.sourceSha256||
      value.submissions?.length!==expected.activities.length)throw new Error('拆分保存回执不一致，请先核对提报记录。');
   batchReceipt=value;batchPreview=null;files=[];
   const acceptedForm=document.querySelector('#b-intake-form');
   if(acceptedForm)acceptedDraftForms.add(acceptedForm);
   draft={form:{},originalDates:{},rawText:'',manualProductRows:[]};
   createSlot='create:'+crypto.randomUUID();
   ctx.notice(value.alreadySaved?'这份神价表已保存，已显示原提报。':'合并神价表已拆分保存。');
  }catch(e){
   if(!ctx.authError?.(e)&&requestSignal===signal&&!requestSignal.aborted)error=e;
  }finally{
   busy=false;
   if(requestSignal===signal&&!requestSignal.aborted)paint();
  }
 }
 let groupReview={},groupEdit=null;
 const supplementSummaries=new Map();
 const acceptedDraftForms=new WeakSet();
 const declarations=()=>activityIntake()?submissionDeclarationCatalog(catalog):null;
 const showDeclarations=input=>Boolean(input&&(declarations()||hasSubmissionDeclarations(input)));
 const recordDeclarations=(input,verified)=>showDeclarations(input)?declarationRecordHtml(input,{verified}):'';
 function readDeclarations(form,data){
  const options=declarations();
  if(!options||!form.querySelector('[name="applicablePlatformCodes"]'))return null;
  const chosen=data.getAll('applicablePlatformCodes');
  return {applicablePlatformCodes:options.applicablePlatforms.filter(({code})=>chosen.includes(code)).map(({code})=>code),
   productScopeIntent:data.get('productScopeIntent')||null};
 }
 const declarationFields=(value,required=false)=>{
  const options=declarations();
  return options?declarationFieldsHtml(options,value,declarationErrors,required):'';
 };
 const focusDeclaration=field=>document.querySelector('[name="'+field+'"]')?.focus();
 const model=createModelRecognitionController(ctx,{
  receivePrimary(next){if(run?.runId===next.runId){
   captureSupplement(document.querySelector('#b-supplement-form'));
   captureConfirm(document.querySelector('#b-confirm-form'));
   run.primaryRecognition=next.primaryRecognition;run.manualNameConfirmation=next.manualNameConfirmation;const old=detail?.runs?.find(r=>r.runId===next.runId);if(old){old.primaryRecognition=next.primaryRecognition;old.manualNameConfirmation=next.manualNameConfirmation;}
   primaryRefreshPending=true;
  }},
  context(){
   if(agentFirst()||!activityIntake()||!detail||!run||!alive()||route!=='submissions/'+detail.submissionId)return null;
   return {run,signal,catalog,materials:detail.materials??[],canStart:!busy&&canSupplement()&&
    ['succeeded','partial'].includes(run.state)&&Boolean(run.result)&&run.inputRevision===detail.revision,
    values(id){
     if(confirmation&&candidateId===id)return confirmation.fields;
     if(!multipleCandidates())return quickEdit?supplementValues():primaryPresentation(run,id,detail.currentInput?.form).values;
     const fields=run.result?.candidates?.find(item=>item.candidateId===id)?.fields??{};
     return Object.fromEntries(Object.entries(fields).map(([key,item])=>[key,item.value]));
    },
    applyTarget(id){
     if(!run.result?.candidates?.some(candidate=>candidate.candidateId===id)||!canSupplement())return null;
     if(confirmation)return candidateId===id?'confirm':null;
     return !multipleCandidates()&&quickEdit?.revision===detail.revision&&!quickEdit.conflict?'supplement':null;
    }
   };
  },
  repaint(){
   if(!primaryRefreshPending){captureSupplement(document.querySelector('#b-supplement-form'));captureConfirm(document.querySelector('#b-confirm-form'))}
   primaryRefreshPending=false;
   paint();
  },
  adopt(target,fields){
   if(target==='confirm')Object.assign(confirmation.fields,fields);
   else{
    quickEdit.modelFields??=new Set();
    for(const [key,value] of Object.entries(fields)){
     if(value===quickEdit.base.form[key])delete quickEdit.edits[key];
     else quickEdit.edits[key]=value;
     quickEdit.modelFields.add(key);
    }
   }
   error=null;
   paint();
   const id=target==='confirm'?'b-confirm-form':'b-supplement-form';
   document.querySelector('#'+id+' [name="'+Object.keys(fields)[0]+'"]')?.focus();
  }
 });
 const alive=()=>!signal?.aborted&&['report','submissions'].includes(ctx.route().split('/')[0]);
 function latestRun(value){
  const runs=value.runs??[];
  return (activityIntake()?runs.findLast(item=>item.inputRevision===value.revision):runs.at(-1))??null;
 }
 function resolvedIds(){
  const saved=(detail?.resolutions??[]).filter(item=>item.revision===detail.revision);
  return new Set([...saved,...(autoStatus?.groups??[]).filter(item=>item.activityId)].map(item=>item.candidateId));
 }
 function captureDraft(form){if(!form||acceptedDraftForms.has(form))return;const data=new FormData(form);const captured=captureFields(form,draft.originalDates);for(const key of Object.keys(captured))if(!form.querySelector('[name="'+key+'"]'))delete captured[key];draft.form={...draft.form,...captured};draft.rawText=String(data.get('rawText')??'');draft.declarations=readDeclarations(form,data)??draft.declarations;if(form.querySelector('.b-manual-products'))draft.manualProductRows=[...form.querySelectorAll('[data-manual-row]')].map(row=>{const get=k=>row.querySelector(`[data-manual-field="${k}"]`)?.value||null;return {rowId:row.dataset.manualRow,productId:get('productId'),rawInternalCode:get('rawInternalCode'),rawUpc:get('rawUpc'),rawName:get('rawName')}})}
 const productItems=(row)=>products.filter(p=>{
  if(!row)return true;
  if(!row.rawInternalCode&&!row.rawUpc)return p.id===row.matchedProductId;
  return (!row.rawInternalCode||row.rawInternalCode.trim()===String(p.internalCode??'').trim())&&
   (!row.rawUpc||row.rawUpc.trim()===String(p.upc??'').trim());
 }).map(p=>({id:p.id,name:`${p.name} · ${p.internalCode} · UPC ${p.upc??'待补充'} · ${p.id}`}));
 function manualHtml(){return table(['内部码（原始值）','UPC（文本）','名称（原始值）','人工选择目录商品','操作'],draft.manualProductRows.map(r=>`<tr data-manual-row="${h(r.rowId)}"><td><input aria-label="商品内部码" data-manual-field="rawInternalCode" value="${h(r.rawInternalCode??'')}" maxlength="500"></td><td><input aria-label="商品UPC" data-manual-field="rawUpc" value="${h(r.rawUpc??'')}" maxlength="500"></td><td><input aria-label="商品名称" data-manual-field="rawName" value="${h(r.rawName??'')}" maxlength="500"></td><td><select aria-label="人工选择目录商品" data-manual-field="productId"><option value="">尚未关联</option>${productItems().map(p=>`<option value="${h(p.id)}" ${p.id===r.productId?'selected':''}>${h(p.name)}</option>`).join('')}</select></td><td><button type="button" data-sub-remove-product="${h(r.rowId)}">移除</button></td></tr>`),'未人工填写商品；仍可只交文字或材料。')}
 const canCreateBrand=()=>activityIntake()&&ctx.session()?.permissions?.includes('brands.create');
 const brandMatches=(name,query)=>Boolean(query.trim())&&String(name??'').toLocaleLowerCase().includes(query.trim().toLocaleLowerCase());
 function resetBrandPicker(){brandSearch='';brandEditor=null;brandNotice=''}
 function brandPicker({embedded=false}={}){
  if(activityIntake()&&catalogError)return '<div role="status"><p>品牌目录读取失败，当前品牌待确认。已填内容会保留。</p><button type="button" data-sub-action="retry-catalog">重新读取品牌目录</button></div>';
  const addBrand=canCreateBrand()&&!brandEditor?'<button type="button" data-sub-action="open-brand">新增品牌</button>':'';
  const selected=new Set(draft.form.brandIds??[]);
  const label=catalog.brands.filter(brand=>selected.has(brand.id)).map(brand=>brand.name).join('、')||'未关联品牌，可后补';
  const noMatch=Boolean(brandSearch.trim())&&!catalog.brands.some(brand=>brandMatches(brand.name,brandSearch));
  const brands=`<label>搜索品牌<input type="search" data-brand-search placeholder="输入品牌名称" value="${h(brandSearch)}"></label>
   <div class="design-brand-options" role="group" aria-label="可选品牌">${catalog.brands.map(brand=>`<label data-brand-option="${h(brand.name)}" ${brandMatches(brand.name,brandSearch)?'':'hidden'}><input type="checkbox" name="brandIds" value="${h(brand.id)}" ${selected.has(brand.id)?'checked':''}>${h(brand.name)} <small>${h(brand.id)}</small></label>`).join('')}
    <p class="hint" data-brand-search-hint ${brandSearch.trim()?'hidden':''}>输入品牌名称后显示匹配项。</p>
    ${activityIntake()?`<div data-brand-no-match ${noMatch?'':'hidden'}><p class="hint" role="status">没有匹配的品牌，请换一个名称搜索。</p>${addBrand}</div>`:''}</div>`;
  const picker=activityIntake()&&!catalog.brands.length
   ? `<p data-brand-selection>${h(label)}</p><p class="hint" role="status">暂无可选品牌，待接入商品库。当前品牌待确认。</p>${addBrand}`
   : `<div class="design-brand-picker${embedded?' b-intake-brand':''}"><p class="hint" data-brand-selection>${h(label)}</p>
     <details ${brandSearch||brandEditor?'open':''}><summary>选择品牌</summary>${brands}</details></div>`;
  const editor=brandEditor?`<div class="b-note" aria-busy="${brandEditor.saving}">
    <label for="b-new-brand-name">新品牌名称</label>
    <input id="b-new-brand-name" data-brand-name type="text" value="${h(brandEditor.name)}" ${brandEditor.saving?'disabled':''}>
    ${brandEditor.error?`<p class="field-error" role="alert">${h(brandEditor.error)}</p>`:''}
    <div class="actions">
      <button type="button" data-sub-action="cancel-brand" ${brandEditor.saving?'disabled':''}>取消</button>
      <button type="button" class="primary" data-sub-action="save-brand" ${brandEditor.saving?'disabled':''}>${brandEditor.saving?'正在保存…':'确认新增品牌'}</button>
    </div>
   </div>`:'';
  return picker+editor+(brandNotice?`<p class="hint" role="status">${h(brandNotice)}</p>`:'');
 }
 async function saveBrand(){
  if(!brandEditor||brandEditor.saving||!canCreateBrand())return;
  const editor=brandEditor;
  const name=editor.name.trim();
  if(!name){editor.error='请输入品牌名称。';paint();document.querySelector('[data-brand-name]')?.focus();return}
  const requestSignal=signal;
  const current=()=>requestSignal===signal&&brandEditor===editor&&alive();
  editor.saving=true;
  editor.error='';
  paint();
  try{
   const {data}=await ctx.adapter.request('/brands',{method:'POST',body:{name},signal:requestSignal});
   if(!current())return;
   const brand=data?.brand;
   if(typeof data?.created!=='boolean'||typeof brand?.id!=='string'||!brand.id||
      typeof brand.name!=='string'||!brand.name)throw new Error('Invalid brand response');
   captureDraft(document.querySelector('#b-intake-form'));
   const found=catalog.brands.some(item=>item.id===brand.id);
   catalog={...catalog,brands:found?catalog.brands.map(item=>item.id===brand.id?{...item,...brand}:item):[...catalog.brands,brand]};
   draft.form.brandIds=[...new Set([...(draft.form.brandIds??[]),brand.id])];
   resetBrandPicker();
   brandNotice=data.created?`已新增并选中“${brand.name}”。`:`“${brand.name}”已存在，已选中现有品牌。`;
   paint();
   document.querySelector('[data-brand-search]')?.focus();
  }catch(error){
   if(!current()||ctx.authError?.(error))return;
   captureDraft(document.querySelector('#b-intake-form'));
   editor.saving=false;
   editor.error=error.status&&error.status<500?error.message:'暂未确认品牌是否保存，可用同一名称重试。';
   paint();
   document.querySelector('[data-brand-name]')?.focus();
  }
 }
 function requiredNameHtml(value,{report=false}={}){
  const label='<label for="b-activity-name">活动名称 <span class="b-required">*</span></label>';
  const control=`<input id="b-activity-name" name="activityName" value="${h(value??'')}" maxlength="200" required placeholder="输入活动名称"
   ${nameError?'aria-invalid="true" aria-describedby="b-activity-name-error"':''}>
   ${nameError?'<p class="field-error" id="b-activity-name-error" role="alert">请输入活动名称。</p>':''}`;
  return report?`<div class="design-form-row">${label}<div>${control}</div></div>`:`<div>${label}${control}</div>`;
 }
 function focusRequiredName(confirm=false){
  document.querySelector(confirm?'#b-confirm-form [name="activityName"]':'#b-activity-name')?.focus();
 }
 function localIntakeHtml(){
  const type=draft.form.type;
  const declarationsCatalog=declarations();
  const {platforms='',productScope=''}=declarationsCatalog
   ?declarationFieldsHtml(declarationsCatalog,draft.declarations,declarationErrors,type==='brand',{split:true}):{};
  const typeFields=`<fieldset class="b-intake-wide"><legend>活动发起方</legend><div class="design-inline-options">
   ${Object.entries(TYPE_LABELS).map(([code,label])=>`<label><input type="radio" name="type" value="${h(code)}" ${type===code?'checked':''}>${h(label)}</label>`).join('')}
  </div></fieldset>`;
  const dates=`<fieldset><legend>活动日期（北京时间）</legend><div class="design-date-range">
   <label>开始日期<input name="startAt" type="date" value="${h(activityDate(draft.form.startAt))}"></label><span aria-hidden="true">—</span>
   <label>结束日期<input id="b-end-date" name="endAt" type="date" value="${h(activityDate(draft.form.endAt))}"
    ${dateError?'aria-invalid="true" aria-describedby="b-intake-date-error"':''}></label>
  </div><p class="hint">可先提交材料，Agent 识别后再补齐缺失的日期。</p>
  ${dateError?'<p class="field-error" id="b-intake-date-error" role="alert">结束日期不能早于开始日期。</p>':''}</fieldset>`;
  const attachments=`<div class="b-upload design-upload" data-material-drop><label for="b-material-files">上传或拖拽活动文件</label>
   <input id="b-material-files" aria-label="上传或拖拽活动文件" type="file" multiple accept=".txt,.md,.csv,.tsv,.xlsx,.docx,.png,.jpg,.jpeg,.webp,.pdf,.doc,.xls" ${busy?'disabled':''}>
   <p class="hint">可添加图片、表格或文档</p></div>
   <p class="hint">最多 5 件，单件 ≤5 MiB，合计 ≤10 MiB。可解析文本、CSV、TSV 和受限 XLSX、DOCX；图片、PDF、DOC、XLS 仅留存原件，可补充文字。</p>
   ${filesHtml()}${batchEligible()&&batchPreview?.status!=='ready'?`<button type="button" data-sub-action="inspect-shenjia-batch" ${busy?'disabled':''}>预览合并神价表</button>`:''}${existingIds.length?`<p class="hint">本修订保留附件：${existingIds.map(h).join('、')}</p>`:''}`;
  const optional=`<details class="b-intake-optional" data-intake-optional><summary>更多选填信息</summary><div class="b-intake-form-grid">
   <div class="b-intake-wide"><p class="b-intake-label">品牌（选填，可多选）</p>${brandPicker({embedded:true})}<p class="hint">已知品牌可一起选，未关联时可先保存、后补。</p></div>
   ${textarea('brandBenefit','品牌给价探的利益点',draft.form.brandBenefit,5000)}${textarea('remark','备注',draft.form.remark,5000)}
   ${declarationsCatalog?select('platformCode','报名编号所属平台',catalog.platforms,draft.form.platformCode)+
      input('sourceActivityId','平台活动编号',draft.form.sourceActivityId,{maxLength:200}):''}
  </div></details>`;
  return `<div class="b-heading"><div><p class="b-intake-crumb">活动管理 / 活动提报</p><h1>${editing?'补充活动材料':'活动提报'}</h1>
   <p class="muted">${agentFirst()?'填写基础信息并提交活动材料，DeepSeek Agent 会直接识别并返回结构化结果。':'填写基础信息并提交活动材料。提交后读取分析结果。'}</p></div>
   <a class="button" href="#activity">返回工作台</a></div>
   ${parent?`<p class="b-note">来源提报：${h(parent)}。本次形成新输入，保留旧版本及原审核结果。</p>`:''}
   ${error?errorPanel(error):''}<div class="design-report-grid b-intake-layout">
   <section class="panel b-intake-create"><form id="b-intake-form" novalidate>
    <div class="section-heading"><h2>基础信息</h2><span class="hint"><span class="b-required">*</span> 为必填项</span></div>
    <div class="b-intake-form-grid"><div class="b-intake-wide">${requiredNameHtml(draft.form.activityName)}</div>${typeFields}
     ${platforms?`<div data-submission-declarations class="b-intake-wide">${platforms}</div>`:''}${dates}${productScope}</div>
    <div class="section-heading b-intake-material-heading"><h2>活动材料</h2><span class="hint">可添加报名表、活动方案或聊天内容</span></div>
    <label>文字内容<textarea name="rawText" rows="3" maxlength="50000" placeholder="粘贴原文、券档或其他补充说明">${h(draft.rawText??'')}</textarea></label>
    ${attachments}${batchHtml()}${optional}${agentConsentHtml()}
    <div class="b-intake-form-footer">
     <div class="actions"><button type="button" data-sub-action="cancel-edit">取消</button>
     <button class="primary" ${busy||brandEditor||batchPreview?.status==='ready'||agentFirst()&&!agentAvailable()?'disabled':''} ${busy?'aria-busy="true"':''}>${busy?'正在提交…':agentFirst()?(editing?'保存修订并交给 Agent 识别':'提交并交给 Agent 识别'):editing?'保存修订并识别':'提交并识别'}</button></div></div>
   </form></section>
   <aside class="design-report-side">${panel('提交后怎么处理',`<ol class="design-agent-steps">
    <li><strong>${agentFirst()?'DeepSeek 识别材料':'识别材料'}</strong><p>按活动分组读取优惠机制、日期和商品范围，整理商品信息。</p></li>
    <li><strong>返回结构化结果</strong><p>查看字段、商品和原文依据；缺失或冲突分别提示。</p></li>
    <li><strong>核对后建档</strong><p>按组确认活动信息，再创建或关联活动。</p></li>
   </ol>`)}${panel('提报时必填','<p>活动名称必填。品牌活动至少选一个适用平台。</p><p class="hint">已知品牌可在「更多选填信息」里一次选多个。</p>')}</aside>
   </div>`;
 }
 function intakeHtml(){
  if(activityIntake())return localIntakeHtml();
  const type=draft.form.type;
  const extra=Object.fromEntries(Object.entries(draft.form).filter(([key])=>['activityName','platformCode','sourceActivityId','rulesText','manualProductRows'].includes(key)&&!(activityIntake()&&key==='activityName')&&
   !(declarations()&&['platformCode','sourceActivityId'].includes(key))));
  const identity=declarations()?`<details><summary>报名编号与来源平台（选填）</summary><div class="b-form-grid">
   ${select('platformCode','报名编号所属平台',catalog.platforms,draft.form.platformCode)}
   ${input('sourceActivityId','平台活动编号',draft.form.sourceActivityId,{maxLength:200})}</div></details>`:'';
  const primary=`<section class="panel design-report-main"><form id="b-intake-form" ${activityIntake()?'novalidate':''}><h2 class="design-section-title">基础信息</h2>${activityIntake()?requiredNameHtml(draft.form.activityName,{report:true}):''}<div class="design-form-row"><span>活动发起方</span><fieldset class="design-inline-options" aria-label="活动发起方">${[['brand','品牌活动'],['platform','平台活动'],['jiatan_sourcing','价探招商']].map(([code,label])=>`<label><input type="radio" name="type" value="${code}" ${type===code?'checked':''}>${label}</label>`).join('')}</fieldset></div>${declarations()?`<div class="design-form-row"><span></span>${declarationFields(draft.declarations,type==='brand')}</div>`:''}<div class="design-form-row"><span>活动时间</span><div class="design-date-range"><label>开始日期（北京时间）<input name="startAt" type="date" value="${h(activityDate(draft.form.startAt))}"></label><span>—</span><label>结束日期（北京时间）<input name="endAt" type="date" value="${h(activityDate(draft.form.endAt))}"></label></div></div><div class="design-form-row"><span>品牌</span><div>${brandPicker()}<p class="hint">可搜索品牌，支持关联多个品牌；不自动新建品牌。</p></div></div><div class="design-form-row"><span>品牌给价探的利益点</span><div><textarea name="brandBenefit" aria-label="品牌给价探的利益点" rows="3" maxlength="5000">${h(draft.form.brandBenefit??'')}</textarea><p class="hint">与消费者优惠、各方出资分别记录。</p></div></div><div class="design-form-row"><span>备注</span><textarea name="remark" aria-label="备注" rows="3" maxlength="5000">${h(draft.form.remark??'')}</textarea></div><h2 class="design-section-title">活动材料</h2><div class="design-form-row"><span>文字内容</span><textarea name="rawText" aria-label="文字内容" rows="4" maxlength="50000">${h(draft.rawText??'')}</textarea></div><div class="design-form-row"><span></span><div><div class="b-upload design-upload" data-material-drop><label for="b-material-files">♧ 上传或拖拽活动文件</label><input id="b-material-files" aria-label="上传或拖拽活动文件" type="file" multiple accept=".txt,.md,.csv,.tsv,.xlsx,.docx,.png,.jpg,.jpeg,.webp,.pdf,.doc,.xls" ${busy?'disabled':''}><p class="hint">可添加图片、表格或文档。</p></div><p class="hint">最多5件，单件≤5MiB，合计≤10MiB。TXT / MD / CSV / TSV / 受限XLSX、DOCX可解析；图片、PDF、DOC、XLS仅留原件，当前无OCR，请补文字。</p>${filesHtml()}${existingIds.length?`<p class="hint">本修订保留附件：${existingIds.map(h).join('、')}</p>`:''}<p class="hint">表单、文字或附件至少提供一项；未知字段可保留待核实。</p></div></div>${identity}${Object.keys(extra).length?`<details><summary>查看已留存的其他原始字段</summary>${factsHtml(extra,catalog)}</details>`:''}<div class="actions design-report-submit"><button type="button" data-sub-action="cancel-edit">返回</button><button class="primary" ${busy||brandEditor?'disabled':''} ${busy?'aria-busy="true"':''}>${busy?'正在提交并分析…':editing?'保存修订并分析':'提交并分析'}</button></div></form></section>`;
  return `<a class="design-back" href="#activity">‹ 返回工作台</a><div class="b-heading"><div><h1>${editing?'补充活动材料':'活动提报'}</h1><p class="muted">填写活动信息或上传材料，由Agent分析后创建或关联活动。</p></div></div>${parent?`<p class="b-note">来源提报：${h(parent)}。本次形成新输入，保留旧版本及原审核结果。</p>`:''}${error?errorPanel(error):''}<div class="design-report-grid">${primary}<div class="design-report-side">${panel('Agent将如何处理',`<p>${badge(busy?'正在提交分析':'待提交分析','blue')}</p><ol class="design-agent-steps"><li><strong>识别活动与材料</strong><p>理解文字内容与附件，识别活动基本信息。</p></li><li><strong>匹配品牌和商品</strong><p>精确、候选及未匹配分别展示，待你核对。</p></li><li><strong>检查重复及内容冲突</strong><p>对比已有活动，保留原始值和核对依据。</p></li><li><strong>创建或关联活动并反馈</strong>${activityIntake()?'<p>符合条件的单活动会自动创建或关联。信息缺失或冲突时，请核对后确认。</p>':'<p>先由你确认分析结果，再保存对应活动或来源。</p>'}</li></ol>`)}${panel('后续处理','<p class="design-next-stage">需审核时 → 管理初审</p><p class="hint">信息不全或冲突时提示补充；已有活动保留原记录。</p><p class="hint">公司明确无承担且有依据时按既定规则默认内部过审，不等于已经报名。</p>')}</div></div>`;
 }
 function filesHtml(items=files){return `<ul class="b-file-list">${items.map((f,i)=>`<li><div><strong>${h(f.file.name)}</strong><small>${h(f.file.size)} 字节 · ${f.materialId?'原件已保存：'+h(f.materialId):f.error?'上传未成功':f.uploading?'正在上传':'待上传'}</small>${f.error?`<span class="b-file-error">${h(f.error.message)}</span>`:''}
  ${f.previewMessage?`<span data-attachment-preview="${i}" class="${f.previewBlocked?'b-file-error':'hint'}" aria-live="polite">${h(f.previewMessage)}</span>`:''}</div><button type="button" data-sub-remove-file="${i}" ${busy?'disabled':''}>移除</button></li>`).join('')}</ul>`}
 const pendingDeletes=()=>ctx.commands.pending?.().filter(record=>record.action==='submissions.delete')??[];
 const listActive=requestSignal=>requestSignal===signal&&!requestSignal.aborted&&ctx.route()==='submissions';
 const rowCanDelete=row=>activityIntake()&&row&&actionAllowed(row,'submissions.delete')&&
  typeof row.submissionId==='string'&&Number.isInteger(row.revision)&&row.revision>0;
 const pendingForRow=row=>(ctx.commands.pending?.()??[]).some(record=>belongsToSubmission(record,row.submissionId));
 const deletePath=id=>'/submissions/'+encodeURIComponent(id)+'/delete';

 function listHtml(){
  const body=(list?.items??[]).map(row=>{
   const id=h(row.submissionId);
   const view=`<a href="#submissions/${id}">查看处理</a>`;
   const remove=rowCanDelete(row)?actionButton('open-delete','删除',{
    disabled:busy||pendingForRow(row),extra:` data-sub-delete-id="${id}"`}):'';
   return rows([id,h(beijing(row.submittedAt)),h(row.revision),badge(stateLabel(row.state)),
    row.activityId?h(row.activityId)+'<small>'+h(row.versionId)+'</small>':'尚未建档',
    remove?`<div class="b-submission-row-actions">${view}${remove}</div>`:view]);
  });
  const recovery=activityIntake()?pendingDeletes().map(record=>{
   const id=record.path?.split('/')[2]??'';
   const extra=` data-sub-delete-slot="${h(record.slot)}"`;
   return `<div class="b-note"><p role="status">提报 ${h(id)} 的删除结果待核对。</p><div class="actions">
    ${actionButton('check-delete','核对删除结果',{disabled:busy,extra})}
    ${record.body?actionButton('retry-delete','恢复原删除请求',{disabled:busy,extra}):''}</div></div>`;
  }).join(''):'';
  const failure=error?errorPanel(error)+(activityIntake()?actionButton('refresh-list','刷新列表',{disabled:busy}):''):'';
  return `<div class="b-heading"><h1>我提报的原始材料</h1><a class="button primary" href="#report">提报活动</a></div>
   ${panel('提报记录',failure+recovery+table(['提报ID','提交时间（北京时间）','输入版本','处理状态','活动 / 版本','操作'],body)+
    pager(list?.page??1,list?.total,'#submissions',{state:params.state??''},list?.pageSize??10))}${deleteDialogHtml()}`;
 }
 function deleteDialogHtml(){
  if(!deleteTarget)return '';
  return `<dialog id="b-delete-submission-dialog" aria-labelledby="b-delete-submission-title" aria-describedby="b-delete-submission-description">
   <h2 id="b-delete-submission-title">删除这条提报？</h2>
   <p class="b-delete-target">${h(valuePresent(deleteTarget.activityName)?deleteTarget.activityName:deleteTarget.submissionId)}</p>
   <p id="b-delete-submission-description">删除这条未建档提报的内容、识别结果和仅它使用的附件。</p>
   <div class="actions">
    <button type="button" data-sub-action="cancel-delete" autofocus ${busy?'disabled':''}>取消</button>
    <button type="button" class="danger" data-sub-action="confirm-delete" ${busy?'disabled aria-busy="true"':''}>${busy?'正在删除…':'确认删除'}</button>
   </div></dialog>`;
 }
 function closeDeleteDialog(){
  if(busy||!deleteTarget)return;
  const id=deleteTarget.submissionId;
  deleteTarget=null;
  paint();
  const buttons=document.querySelectorAll?.('[data-sub-delete-id]')??[];
  [...buttons].find(button=>button.dataset.subDeleteId===id)?.focus();
 }
 async function readList(requestSignal,{clampPage=false}={}){
  const query=new URLSearchParams({view:'mine',page:params.page??1,pageSize:10,...(params.state?{state:params.state}:{})});
  const env=await ctx.adapter.request('/submissions?'+query,{signal:requestSignal});
  if(!listActive(requestSignal))return false;
  list=env.data;
  if(clampPage&&Number.isInteger(list.total)&&list.pageSize>0){
   const last=Math.max(1,Math.ceil(list.total/list.pageSize));
   if(Number(params.page??1)>last){
    ctx.navigate('#submissions?'+new URLSearchParams({...params,page:last}));
    return false;
   }
  }
  return true;
 }
 function checkDeleted(data,id,revision){
  if(data?.state!=='deleted'||data.submissionId!==id||!Number.isInteger(data.revision)||data.revision<1||
     (revision!=null&&revision!==data.revision))throw new Error('删除回执与这条提报不一致，请刷新列表核对。');
 }
 async function refreshAfterDelete(id,requestSignal){
  list={...list,items:(list?.items??[]).filter(item=>item.submissionId!==id),total:null};
  ctx.notice('提报已删除。');
  try{await readList(requestSignal,{clampPage:true})}
  catch(e){
   if(!listActive(requestSignal)||ctx.authError?.(e))return;
   error=new Error('删除已确认，列表暂未刷新，请刷新列表。');
  }
 }
 async function listAction(action,data){
  if(!activityIntake()||route!=='submissions')return true;
  if(action==='open-delete'){
   const row=list?.items?.find(item=>item.submissionId===data.subDeleteId);
   if(rowCanDelete(row)&&!pendingForRow(row)){deleteTarget=row;error=null;paint()}
   return true;
  }
  if(action==='cancel-delete'){closeDeleteDialog();return true}
  const row=deleteTarget;
  if(action==='confirm-delete'&&(!rowCanDelete(row)||pendingForRow(row)))return true;
  const record=pendingDeletes().find(item=>item.slot===data.subDeleteSlot);
  if(['check-delete','retry-delete'].includes(action)&&!record)return true;
  const requestSignal=signal;
  busy=true;error=null;paint();
  try{
   if(action==='refresh-list'){
    await readList(requestSignal,{clampPage:true});
   }else if(action==='confirm-delete'){
    const {submissionId,revision}=row;
    const response=await ctx.commands.run('delete:'+submissionId+':'+revision,'submissions.delete','POST',
     deletePath(submissionId),{expectedRevision:revision},{signal:requestSignal});
    if(!listActive(requestSignal))return true;
    checkDeleted(response.data,submissionId,revision);
    deleteTarget=null;
    await refreshAfterDelete(submissionId,requestSignal);
   }else{
    const result=await resolveOriginal(record,action==='retry-delete',requestSignal);
    if(!listActive(requestSignal))return true;
    if(result){
     const id=record.path.split('/')[2];
     checkDeleted(result.data,id,record.body?.expectedRevision??Number(record.slot.split(':').at(-1)));
     await refreshAfterDelete(id,requestSignal);
    }else{
     await readList(requestSignal,{clampPage:true});
     if(listActive(requestSignal))ctx.notice('已刷新列表，服务暂未返回原删除回执。');
    }
   }
  }catch(e){
   if(!listActive(requestSignal)||ctx.authError?.(e))return true;
   deleteTarget=null;
   if(e.status===409&&e.code==='STATE_CONFLICT'){
    error=new Error(DELETE_CONFLICTS[e.details?.reasonCode]??'提报状态已变化，请刷新后查看。');
    try{await readList(requestSignal,{clampPage:true})}
    catch(readError){if(ctx.authError?.(readError))return true}
   }else if(e.code!=='COMMAND_UNKNOWN'){
    error=e;
   }
  }finally{
   busy=false;
   if(listActive(requestSignal))paint();
  }
  return true;
 }
 function intakeRecovery(){return ctx.commands.intakes?.().find(flow=>flow.submissionId===detail?.submissionId&&flow.revision===detail?.revision&&['received','analysis_unknown','analysis_failed'].includes(flow.phase)&&(!activityIntake()||!run))}
 const autoReasons={
  SINGLE_ACTIVITY_UNCONFIRMED:'没有识别到唯一活动分组', PARSE_COVERAGE_UNCONFIRMED:'部分材料尚无法完整解析',
  SOURCE_INTEGRITY_UNCONFIRMED:'原件及证据完整性待核实', FIELD_CONFLICT:'材料与填写字段有冲突',
  REQUIRED_CONTEXT_MISSING:'活动名称、类型或规则不完整', IDENTITY_INCOMPLETE:'平台、活动编号、时间或品牌身份不完整',
  DUPLICATE_AMBIGUOUS:'存在可能重复的活动', DUPLICATE_SCOPE_UNCONFIRMED:'查重范围尚不完整',
  SOURCE_CONFLICT:'已有活动与本次材料的条件不一致', PRODUCT_SCOPE_UNCONFIRMED:'适用商品范围待核实',
  BRAND_PLATFORM_SELECTION_REQUIRED:'适用平台待补充', BRAND_UNCONFIRMED:'参与品牌待补充',
  PRODUCT_SCOPE_CONFLICT:'适用商品范围有冲突', PRODUCT_MATCH_UNCONFIRMED:'有商品尚未精确匹配',
  PRODUCT_BRAND_CONFLICT:'商品与品牌范围不一致', PRODUCT_IDENTITY_CONFLICT:'商品内部码或 UPC 信息有冲突',
  BEARING_CONFLICT:'公司承担信息有冲突', BEARING_EVIDENCE_UNVERIFIED:'公司承担缺少有效依据',
  EVIDENCE_UNVERIFIED:'部分识别字段缺少有效原文依据', MANUAL_SELECTION_UNVERIFIED:'人工选择的商品缺少有效依据'
 };
 function automaticHtml(inRecords=false){
  if(!activityIntake())return '';
  if(autoBusy)return '<p role="status">正在核对并处理本次分析结果…</p>';
  const result=autoStatus?.result;
  if(result?.outcome==='needs_confirmation')return `<div class="b-note"><p>请核对后确认本次活动。</p>${result.reasonCodes.length?`<ul>${result.reasonCodes.map(code=>`<li>${h(autoReasons[code]??businessMessage(code))}</li>`).join('')}</ul>`:''}</div>`;
  if(result?.activityId){
   const label=result.outcome==='already_resolved'?'本分组已有处理结果':result.outcome==='created'?'系统已创建活动':'系统已关联已有活动';
   return `<p class="b-note">${label} · <a href="#activity/${h(result.activityId)}">查看活动</a> · <a href="#review/${h(result.activityId)}/${h(result.versionId)}">查看处理版本</a></p>`;
  }
  if(autoStatus?.state==='unknown')return '<p role="status">自动处理结果待核对。重试会先查回执，再沿用原请求。</p>'+
   (inRecords?'':actionButton('retry-auto','核对并恢复自动处理'));
  if(autoStatus?.state==='rejected')return '<p role="status">本次自动处理未完成，请核对输入或重新分析。</p>';
  if(autoStatus?.state==='unavailable')return '<p role="status">自动处理当前不可用，请核对分析结果。</p>';
  return '';
 }
 const canConfirmManually=()=>!currentBadCase()&&!autoBusy&&autoStatus?.state!=='unknown'&&!autoStatus?.result?.activityId&&(!agentFirst()||Boolean(currentAgentBinding()));
 function processingHtml(inRecords=false){
  const result=run?.result,recovery=intakeRecovery();
  const phase={queued:'排队等待',starting:'开始处理',decoding:'服务端读取材料',local_planning:'服务端整理提报材料',agent_recognizing:'Agent 正在识别',analyzing:'识别活动与匹配商品',complete:'处理完成',interrupted:'处理已中断',cancelled:'处理已取消',failed:'处理未完成'}[run?.phase]??'处理阶段待核实';
  const materials=detail.materials??[];
  const materialRows=(run?.materialResults??[]).map(m=>{const source=materials.find(x=>x.materialId===m.materialId);return rows([h(source?.originalName??source?.filename??'原始材料')+`<small>材料 ${h(m.materialId)}</small>`,h(stateLabel(m.status)),m.cacheHit?'已复用同一原件的解析结果':'本次处理',issueList([...(m.errors??[]).map(issue=>issue.code==='LIMIT_EXCEEDED'?{...issue,message:excelLimitMessage(run,m)||issue.message}:issue),...(m.warnings??[])],'没有报告处理问题。')])});
  return panel('材料处理',`<ol class="b-workflow"><li>1 · 原始输入留存<br>${h(detail.submissionId)}</li><li>2 · ${localFailureTitle(run)?'服务端处理材料':agentFirst()?'DeepSeek Agent 识别':'原材料解析及商品匹配'}<br>${h(localFailureTitle(run)||stateLabel(run?.state??'received'))}</li><li>3 · ${activityIntake()&&!agentFirst()?'自动处理或人工核对<br>以当前处理结果为准':'人工核对<br>确认后才建档或关联'}</li></ol>${automaticHtml(inRecords)}${!inRecords&&run?`<div class="b-status">${badge(stateLabel(run.state))}<span>${h(phase)} · 原始输入修订 ${h(run.inputRevision)}</span><button type="button" data-sub-action="refresh">核对最新结果</button>${ACTIVE.has(run.state)?'<button type="button" data-sub-action="cancel-analysis">取消本次分析</button>':''}</div>`:run?'':'<p class="muted">原材料已受理，尚未开始分析。</p>'}${agentFirst()?agentSourceHtml():result?`<p class="b-note">历史分析来源：${result.modelUsed===false?'本地规则解析，未调用外部模型':'模型调用来源待核实'}。不能解析的材料仍保留原件，请结合处理提示补充。</p>`:''}${run?.error?errorPanel(typeof run.error==='string'?{message:run.error}:run.error):''}${table(['材料 / 原件ID','处理结果','本次处理','需核对事项'],materialRows)}${result&&(result.warnings?.length||result.errors?.length)?`<h3>本次处理提示</h3>${issueList([...(result.errors??[]),...(result.warnings??[])])}`:''}${!inRecords&&recovery&&!ACTIVE.has(run?.state)?'<p class="b-note">本次材料已受理，首次分析仍待核对。恢复会先读原回执，必要时沿原键和原修订继续，不重新提报。</p><button type="button" data-sub-action="recover-analysis">核对并恢复本次首次分析</button>':''}${!inRecords&&!recovery&&!ACTIVE.has(run?.state)&&actionAllowed(detail,'submissions.edit_own')?`<button type="button" class="primary" data-sub-action="analyze" ${busy||autoBusy?'disabled':''}>${run?'重新分析当前输入（保留已解析结果）':'开始分析当前输入'}</button>`:''}${ACTIVE.has(run?.state)?'<p class="b-poll" role="status">正在查询本次分析结果；离开页面停止查询，不会重新提交分析。</p>':''}${run?diagnostics({runId:run.runId,inputRevision:run.inputRevision,analysisDigest:run.analysisDigest,inputDigest:run.inputDigest,engine:result?.engine,engineVersion:result?.engineVersion,modelUsed:result?.modelUsed,agentRecognition:run.agentRecognition,parserVersions:result?.parserVersions,materialResults:run.materialResults,error:run.error},'查看分析来源与技术诊断'):''}`);
 }
 function candidateHtml(inRecords=false){
  const result=run?.result;if(!result)return '';const resolved=resolvedIds();const candidates=result.candidates??[];
  return panel('识别结果与人工核对',`${table(['待补充字段','需要核对的内容'],(result.missingFields??[]).map(x=>rows([h(FIELD_LABELS[x.field]??'活动字段'),h(businessMessage(x))])),'未报告字段缺失。')}${result.conflicts?.length?`<h3>原始填写与材料识别的冲突</h3>${conflictsHtml(result.conflicts,catalog)}`:''}<p class="hint">候选与无匹配商品不会自动计入精确关系。每个活动分组分别确认，不批量合并；没有单品的活动可不填写商品。</p>${candidates.map((c,index)=>`<article class="b-candidate ${candidateId===c.candidateId?'selected':''}"><h3>分组 ${index+1} · ${h(c.fields?.activityName?.value??'名称待补充')}</h3><small>分组编号 ${h(c.candidateId)}</small><p>${resolved.has(c.candidateId)?badge('已建档或关联'):badge('待人工确认','amber')}</p>${factsHtml(Object.fromEntries(Object.entries(c.fields??{}).map(([k,v])=>[k,v.value])),catalog)}${table(['字段','原值识别情况','原文依据'],Object.entries(c.fields??{}).map(([k,v])=>rows([h(FIELD_LABELS[k]??'活动字段'),h(stateLabel(v.status)),h((v.evidenceIds??[]).join('、')||'未提供提取依据')])))}${table(['原始内部码 / UPC / 名称','匹配情况','已识别目录商品 / 待核候选'],(c.productRows??[]).map(p=>rows([h([p.rawInternalCode,p.rawUpc,p.rawName,p.rawSpec].filter(Boolean).join(' / ')||'原始标识未提供')+`<small>行ID ${h(p.rowId)}</small>`,h(LABELS[p.matchKind]??'匹配情况待核实'),h(p.matchedProductId??'尚未确认')+(p.candidates??[]).map(x=>`<small>${h(x.productId)} · ${h((x.reasons??[]).join('；'))}</small>`).join('')])),'本分组没有单品行，可继续核对活动资料。')}${!inRecords&&!resolved.has(c.candidateId)&&['succeeded','partial'].includes(run.state)&&canConfirmManually()&&actionAllowed(detail,'activities.propose')?`<button type="button" data-candidate-select="${h(c.candidateId)}">核对本分组并确认</button>`:''}</article>`).join('')||'<p class="muted">尚无可确认分组，请补充活动名称、文字或受支持材料。</p>'}${!inRecords&&confirmation?confirmHtml():''}<details><summary>查看原始材料引用与证据定位</summary>${evidenceList(result.evidence,detail.materials)}</details>${diagnostics({missingFields:result.missingFields,conflicts:result.conflicts,warnings:result.warnings,errors:result.errors},'查看识别诊断')}`);
 }
 function groupEvidenceHtml(candidate){
  const reasons=[...new Set((groupOutcome(candidate.candidateId)?.reasonCodes??[]).map(code=>autoReasons[code]??'本组资料待核对'))];
  const fields=Object.values(candidate.fields??{});
  const referenceIds=new Set(fields.flatMap(field=>field.evidenceIds??[]));
  const evidence=(run.result.evidence??[]).filter(item=>referenceIds.has(item.evidenceId));
  return `<div class="b-intake-review-context">
   ${reasons.length?`<p>${reasons.map(reason=>badge(reason,'amber')).join(' ')}</p>`:''}
   ${evidence.length?`<details><summary>查看本分组原文依据</summary>${evidenceList(evidence,detail.materials??[])}</details>`:''}
  </div>`;
 }
 function bearingEvidenceIds(candidate){
  const field=candidate.fields?.companyBearing;
  if(field?.status!=='explicit'||field.value?.state!=='no')return [];
  const conflicting=new Set((run.result.conflicts??[]).filter(item=>item.field==='companyBearing')
   .flatMap(item=>item.evidenceIds??[]));
  return (field.evidenceIds??[]).filter(id=>!conflicting.has(id)&&
   (run.result.evidence??[]).some(item=>item.evidenceId===id));
 }
 function submittedOriginalsHtml(){
  const source=detail.currentInput;
  const ids=new Set(source?.materialIds??[]);
  const materials=(detail.materials??[]).filter(material=>ids.has(material.materialId));
  const text=source?.rawText;
  return `<section aria-label="提交原件"><h4>提交原件</h4>
   ${text?`<p class="hint">提交文字</p><pre class="b-json">${h(text)}</pre>`:''}
   ${materialHtml(materials,{compact:true})}</section>`;
 }
 function correctionFields(){
  const options=(detail.materials??[]).filter(m=>(detail.currentInput?.allMaterialIds??[]).includes(m.materialId))
   .map(m=>({id:m.materialId,name:m.originalName??m.filename}));
  const source=(prefix,row)=>select(prefix+'materialId','原件',options,row.materialId,{required:true})+
   input(prefix+'location','原件位置',row.location,{maxLength:200,required:true,placeholder:'如 Sheet1 第 12 行'});
  const {products:manualProducts,couponTiers}=confirmation.corrections;
  return `<fieldset data-manual-corrections${confirmation.intent==='link_source'?' hidden disabled':''}><details${manualProducts.length||couponTiers.length?' open':''}><summary>补正漏识别的商品或子券</summary>
   <p class="hint">请对照本次提交原件。补正内容随本组一起确认。</p>
   ${manualProducts.map((row,index)=>{
    const prefix='correction-product:'+index+':';
    return `<fieldset class="b-status-list"><legend>补录商品 ${index+1}</legend>
     ${input(prefix+'identifier','商品内部码或 UPC',row.identifier,{maxLength:200})}
     ${actionButton('find-correction-product','查询公司商品目录',{extra:' data-index="'+index+'"'})}
     ${row.message?`<p class="hint" role="status">${h(row.message)}</p>`:''}
     ${select(prefix+'internalCode','选择公司商品',row.matches.map(item=>({id:item.internalCode,
       name:[item.name,item.spec,item.internalCode].filter(Boolean).join(' · ')})),row.internalCode,{required:true})}
     ${input(prefix+'signupPrice','报名价（有则填写）',row.signupPrice,{placeholder:'如 39.9'})}
     ${source(prefix,row)}${actionButton('remove-correction','移除商品',{extra:' data-kind="products" data-index="'+index+'"'})}</fieldset>`;
   }).join('')}
   ${couponTiers.map((row,index)=>{
    const prefix='correction-coupon:'+index+':';
    return `<fieldset class="b-status-list"><legend>补录子券 ${index+1}</legend>
     ${input(prefix+'name','子券名称或优惠',row.name,{maxLength:200,required:true})}
     ${input(prefix+'startOn','开始日期（北京时间）',row.startOn,{type:'date',required:true})}
     ${input(prefix+'endOn','结束日期（北京时间）',row.endOn,{type:'date',required:true})}
     ${source(prefix,row)}${actionButton('remove-correction','移除子券',{extra:' data-kind="couponTiers" data-index="'+index+'"'})}</fieldset>`;
   }).join('')}
   ${actionButton('add-correction','补录商品',{extra:' data-kind="products"',disabled:!options.length||manualProducts.length>=100})}
   ${actionButton('add-correction','补录子券',{extra:' data-kind="couponTiers"',disabled:!options.length||couponTiers.length>=30})}
  </details></fieldset>`;
 }
 const tierFundingFor=fields=>detail?.currentInput?.form?.type==='brand'&&fields.type==='brand';
 function fundingSourceContext(value){
  return JSON.stringify([value.fields.platformCode,value.corrections.couponTiers]);
 }
 function receiveFundingQuestions(error,value){
  if(error.status!==422||!Array.isArray(error.details?.questions))return false;
  value.fundingRetry={questions:error.details.questions,context:fundingSourceContext(value)};
  return true;
 }
 function fundingQuestions(id,intent='create'){
  if(!['create','propose_change'].includes(intent))return [];
  if(candidateId===id&&confirmation?.fundingRetry&&confirmation.fundingRetry.context===fundingSourceContext(confirmation)){
   return confirmation.fundingRetry.questions;
  }
  const questions=run?.couponFundingQuestions?.[id];
  return Array.isArray(questions)?questions:[];
 }
 function fundingQuestionFields(questions,answers){
  if(!questions.length)return '';
  const fields=questions.map(q=>{
   const label=h(fundingLabel(q));
   if(unboundFunding(q))return `<p class="b-file-error">${label}：无法对应原件，请补正材料后重新识别。</p>`;
   if(!ownSubmission())return `<p class="b-file-error">${label}：待提报人填写出资备注。</p>`;
   const entry=answers.find(a=>a.sourceTierKey===q.sourceTierKey&&a.tierId===q.tierId);
   return `<label>${label} · 出资备注 <span class="b-required">*</span>
    <textarea name="${h(fundingNoteName(q))}" rows="2" required maxlength="1000">${h(entry?.note??'')}</textarea></label>`;
  });
  return `<fieldset class="b-coupon-funding-notes"><legend>待补出资备注</legend>${fields.join('')}</fieldset>`;
 }
 function brandSubsidyOnlyVisible(fields,candidate){
  if(fundingQuestions(candidate.candidateId).length)return false;
  if(!tierFundingFor(fields)&&(fields.storeBearing!=='no'||fields.companyBearing?.state==='yes'))return false;
  if(fields.type!=='brand'||fields.activityMethod!=='brand_threshold_subsidy_single')return false;
  const method=agentCandidateMetadata(run,candidate.candidateId)?.activityMethod;
  const source=candidate.fields?.companyBearing;
  if(method?.status==='conflict'||['brand_threshold_subsidy_dual','brand_threshold_subsidy_triple','shen_jia','bao_hao_jia'].includes(method?.value)||
     source?.status==='conflict'||(source?.value?.state??source?.value)==='yes')return false;
  const schedule=run.couponSchedule;
  const tiers=schedule?.candidateId===candidate.candidateId?schedule.tiers??[]:[];
  return !explicitMix(candidate.fields?.rulesText?.value,fields.rulesText)&&!tiers.some(tier=>
   tier.storeFundingRequired===true||tier.storeFundingStatus==='required'||
   ['brand_store','brand_store_platform'].includes(tier.fundingMode)||
   tier.fundingShares?.some(share=>share.party!=='brand'&&(Number(share.amount)>0||Number(share.percent)>0)));
 }
 function manualGroupFields(fields,datesRequired){
  const missing=new Set();
  if(!valuePresent(fields.activityName))missing.add('activityName');
  if(datesRequired){
   for(const field of ['startAt','endAt'])if(!activityDate(fields[field]))missing.add(field);
   if(activityDate(fields.startAt)>activityDate(fields.endAt)&&activityDate(fields.endAt))missing.add('endAt');
  }
  return missing;
 }
 function groupHasCouponScope(candidate,fields){
  return hasCouponScope(agentCandidateMetadata(run,candidate.candidateId)?.activityMethod?.value,candidate.fields?.rulesText?.value)||
   hasCouponScope(fields.activityMethod,fields.rulesText)||run.couponSchedule?.candidateId===candidate.candidateId;
 }
 function scopeNeedsInput(intent,fields,allLinked){
  if(allLinked)return false;
  const platforms=applicablePlatformCodes(detail.currentInput);
  if(!platforms.length&&!fields.platformCode)return true;
  return intent!=='no_specific_products'&&(intent!=='all_products'||fields.type==='brand'&&!fields.brandIds?.length);
 }
 function confirmHtml(){
  const c=primaryCandidate(run,candidateId);
  const intake=activityIntake();
  const actions=`<div class="actions"><button type="button" data-sub-action="cancel-confirm">${quickEdit?'返回补充信息':'取消确认'}</button>
   <button type="submit" class="primary" ${busy?'disabled aria-busy="true"':''}>${busy?'正在确认…':'确认本分组并提交'}</button></div>`;
  const bearingOptions=[{code:'unknown',label:'未知，交管理核实'},{code:'yes',label:'公司有承担'}];
  if(!agentFirst()&&(!intake||bearingEvidenceIds(c).length))bearingOptions.push({code:'no',label:'明确公司无承担'});
  const bearingState=confirmation.fields.companyBearing?.state??'unknown';
  const datesRequired=confirmation.intent!=='link_source';
  const method=agentCandidateMetadata(run,candidateId)?.activityMethod?.value;
  const tierFunding=tierFundingFor(confirmation.fields);
  const storeRequired=!tierFunding&&datesRequired&&(requiresStoreBearing(method,c.fields?.rulesText?.value)||
   requiresStoreBearing(confirmation.fields.activityMethod,confirmation.fields.rulesText,confirmation.fields.activityName)||
   confirmation.corrections.couponTiers.length>0);
  const scopeRequired=datesRequired&&agentFirst()&&groupHasCouponScope(c,confirmation.fields);
  const needsScope=scopeRequired&&scopeNeedsInput(confirmation.scopeIntent,confirmation.fields,
   confirmation.productResolutions.length>0&&confirmation.productResolutions.every(row=>row.decision==='link'&&row.productId));
  const missing=intake?manualGroupFields(confirmation.fields,datesRequired):new Set();
  const dateFields=[...missing].map(key=>key==='activityName'?requiredNameHtml(confirmation.fields.activityName):
   input(key,FIELD_LABELS[key],activityDate(confirmation.fields[key]),{type:'date',required:true})).join('');
  const storeControl=tierFunding?'':`<div data-store-bearing${storeRequired?'':' hidden'}>${select('storeBearing','门店是否出钱',STORE_BEARING_OPTIONS,confirmation.fields.storeBearing??'',{required:true,disabled:!storeRequired})}</div>`;
  const storeMissing=intake&&storeRequired&&!['yes','no'].includes(confirmation.fields.storeBearing);
  const scopeControl=scopeRequired?select('groupScopeIntent','确认本活动商品范围',scopeOptions,confirmation.scopeIntent,{empty:'待确认',disabled:!ownSubmission()}):'';
  const funding=fundingQuestionFields(fundingQuestions(candidateId,confirmation.intent),confirmation.couponFundingAnswers);
  const requiredControls=dateFields+funding+(storeMissing?storeControl:'');
  const attention=intake&&(requiredControls||needsScope)?`<section class="b-manual-attention"><h4>需要提报人填写</h4>
   ${requiredControls?`<p>提交本分组前，请补齐以下资料。</p>${requiredControls}`:''}
   ${needsScope?`<p>初审通过前，请由原提报人确认本活动的商品范围。可以先建档，再补充范围。</p>${scopeControl}
    <p class="hint">部分商品适用时，请核对并关联商品清单；品牌全部商品适用时，请确认品牌和适用平台。</p>`:''}</section>`:'';
  const couponSchedule=run.couponSchedule?.candidateId===candidateId?run.couponSchedule:null;
  const productRows=(c.productRows??[]).length?table(['原始商品行','确认关联目录商品（可保留未确认）'],(c.productRows??[]).map(p=>rows([h([p.rawInternalCode,p.rawUpc,p.rawName,p.rawSpec].filter(Boolean).join(' / ')||p.rowId),select('product:'+p.rowId,'商品 '+p.rowId,productItems(p),confirmation.productResolutions.find(x=>x.rowId===p.rowId)?.productId??'',{empty:'保留未确认，不计为零'})]))): '';
  const productChoices=intake&&productRows
   ?`<details><summary>关联商品（${c.productRows.length} 条）</summary>${productRows}</details>`:productRows;
  const costEvidence=intake||tierFunding?'':`<p class="hint">仅勾选确实支持承担结论的本次证据。引用无关原文不能作为免审依据。</p>${(run.result.evidence??[]).map(e=>`<label class="b-evidence-check"><input type="checkbox" name="costEvidence" value="${h(e.evidenceId)}" ${(confirmation.fields.companyBearing?.evidenceIds??[]).includes(e.evidenceId)?'checked':''}>${h(e.evidenceId)} · ${h(locatorLabel(e.locator))} · ${h(e.quote??'')}</label>`).join('')}`;
  return `<form id="b-confirm-form" class="b-candidate selected" aria-labelledby="b-group-review-title">
   <div class="b-confirm-heading"><h3 id="b-group-review-title" tabindex="-1">核对本分组 · ${h(c.fields?.activityName?.value??candidateId)}</h3>${intake?actions:''}</div>
   ${intake?'':groupEvidenceHtml(c)}
   <fieldset class="b-confirm-fields" ${busy?'disabled':''}>
   ${attention}
   ${intake?`<h4>标准活动信息</h4><p>识别出的活动方式：${h(activityCategoryLabel(agentCandidateMetadata(run,candidateId)?.activityCategory))}</p>`:''}
   ${datesRequired?couponPeriodHtml(run.candidatePeriods?.[candidateId]):''}
   ${couponSchedule?`<p class="hint">同品牌券档在一场活动里审核，各券有效期分别保留。</p>${couponTiersHtml(couponSchedule)}`:''}
   ${recordDeclarations(detail.currentInput,autoStatus?.result??{})}${fieldsHtml(confirmation.fields,catalog,{identity:!!declarations()||hasSubmissionDeclarations(detail.currentInput),brandEvidence:intake?'':directoryBrandHtml(directoryBrandEvidence(c.candidateId)),datesRequired,omit:missing})}${select('activityMethod','活动玩法',ACTIVITY_METHODS,confirmation.fields.activityMethod??'',{empty:'待核对'})}
   ${intake?'':funding}
   ${tierFunding?'':`${storeMissing?'':storeControl}
   ${select('companyBearing','公司承担',bearingOptions,bearingOptions.some(item=>item.code===bearingState)?bearingState:'unknown',{empty:null})}`}
   ${agentFirst()?`<div data-brand-subsidy-only${datesRequired&&brandSubsidyOnlyVisible(confirmation.fields,c)?'':' hidden'}>
    <label class="b-evidence-check"><input type="checkbox" name="brandSubsidyOnly"${confirmation.fields.brandSubsidyOnly?' checked':''}>
    品牌全额补贴，门店和公司均不出补贴，单商品无需降价</label>
    <p class="hint">确认后由管理审核一次，通过即待报名。</p></div>`:''}
   ${intake&&needsScope?'':scopeControl}
   ${datesRequired?subsidyPricesHtml(run?.subsidyPrices?.[candidateId],{editable:true,values:confirmation.subsidyValues}):''}
   ${costEvidence}${productChoices}${intake&&currentAgentBinding()?correctionFields():''}
   ${select('intent','本次操作',[{code:'create',label:'新建独立活动'},{code:'link_source',label:'关联已有活动，仅补充来源'},{code:'propose_change',label:'提交已有活动的补充版本'}],confirmation.intent)}${input('targetActivityId','关联 / 变更的内部活动ID',confirmation.targetActivityId,{maxLength:200})}${input('expectedActivityRevision','目标活动当前修订号（关联 / 变更必需）',confirmation.expectedActivityRevision??'',{type:'number'})}<button type="button" data-sub-action="target">核对目标活动并读取当前修订号</button>${targetContext&&targetContext.id===confirmation.targetActivityId?`<div class="b-note">已读取目标：${h(targetContext.name)} · ${h(targetContext.id)} · 修订 ${h(targetContext.revision)}<br>当前内部结果：${h(reviewResultLabel(targetContext.review))}。只填入当前修订号，不自动合并或覆盖本次确认值。</div>`:''}<p class="hint">关联前可从活动详情核对稳定ID及当前修订号；同名或同品牌不能作为合并依据。变更版本不会直接覆盖生效版。</p><label class="b-evidence-check"><input type="checkbox" name="duplicateAcknowledgement" ${confirmation.duplicateAcknowledgement?'checked':''}>身份信息不完整时，确认已人工核对重复风险；仍以服务端查重结果为准。</label>
   ${intake?'':actions}</fieldset>${intake?submittedOriginalsHtml():''}</form>`;
 }
 function detailHtml(){return `<div class="b-heading"><div><h1>活动提报</h1><p class="mono">${h(detail.submissionId)} · 输入版本 ${h(detail.revision)}</p></div><a class="button" href="${activityIntake()?'#submissions':'#reviews?view=mine'}">返回我提报的</a></div>${error?errorPanel(error):''}${ctx.recoveryHtml?.()??''}${panel('当前处理结果',`${badge(stateLabel(detail.state))}${factsHtml({submitterId:detail.submitterId,submittedAt:detail.submittedAt,activityId:detail.activityId,versionId:detail.versionId,parentSubmissionId:detail.parentSubmissionId})}${detail.activityId?`<a class="button" href="#activity/${h(detail.activityId)}">查看活动</a> <a class="button" href="#review/${h(detail.activityId)}/${h(detail.versionId)}">查看具体版本与审核</a>`:''}${(detail.resolutions??[]).map(r=>`<p>${h(r.candidateId)}：${h(stateLabel(r.outcome))} · <a href="#activity/${h(r.activityId)}">${h(r.activityId)}</a> / <a href="#review/${h(r.activityId)}/${h(r.versionId)}">${h(r.versionId)}</a></p>`).join('')}`)}${processingHtml()}${candidateHtml()}${panel('原始表单、文字与原件',`${factsHtml(detail.currentInput?.form,catalog)}<h3>原始文字</h3><pre class="b-json">${h(detail.currentInput?.rawText||'未提供')}</pre><h3>材料原件</h3>${materialHtml(detail.materials)}<details><summary>输入版本历史（不可改写）</summary>${(detail.revisions??[]).map(r=>`<h4>输入修订 ${h(r.inputRevision)}</h4>${factsHtml(r.form,catalog)}${diagnostics({inputRevision:r.inputRevision,inputDigest:r.inputDigest},'查看输入修订核验信息')}<pre class="b-json">${h(r.rawText||'未提供原文')}</pre><p>原件ID：${h((r.allMaterialIds??[]).join('、'))}</p>`).join('')}</details>${actionAllowed(detail,'submissions.edit_own')&&!ACTIVE.has(run?.state)?`<button type="button" data-sub-action="edit">${detail.activityId||(detail.resolutions??[]).length?'保留旧版本，补充本次来源材料':'补充输入并保存新修订'}</button>`:''}`)}`}
 function belongsToSubmission(record,id,runs=[]){
  const path=record.path?.split('?')[0];
  const base='/submissions/'+encodeURIComponent(id);
  return ['/draft','/analysis-runs','/confirm','/auto-resolve','/delete','/bad-case','/grouping-decisions'].some(suffix=>path===base+suffix)||
   record.action==='submissions.create'&&record.body?.parentSubmissionId===id||
   record.action==='activities.supplement'&&(record.body?.source?.submissionId===id||record.slot?.startsWith('group-supplement:'+id+':'))||
   runs.some(item=>path==='/analysis-runs/'+encodeURIComponent(item.runId)+'/cancel');
 }
 function ownsPending(record){
  if(!activityIntake())return false;
  if(model.owns(record))return true;
  const currentRoute=ctx.route();
  if(currentRoute==='submissions')return record.action==='submissions.delete';
  return !!detail&&currentRoute==='submissions/'+detail.submissionId&&
   belongsToSubmission(record,detail.submissionId,detail.runs??[]);
 }
 const pendingWrite=()=>ctx.commands.pending?.().find(record=>!model.owns(record)&&ownsPending(record));
 const unknownOutcome=()=>!!pendingWrite()||autoStatus?.state==='unknown';
 const acceptedAhead=()=>ctx.commands.intakes?.().some(flow=>flow.submissionId===detail.submissionId&&flow.revision>detail.revision);
 const hasDecision=()=>!!(detail.activityId||detail.resolutions?.length||['resolved','partially_resolved'].includes(detail.state)||autoStatus?.result?.activityId||autoStatus?.groups?.some(item=>item.activityId));
 const analyzing=()=>ACTIVE.has(run?.state)||detail.state==='analyzing';
 const canEditInput=()=>actionAllowed(detail,'submissions.edit_own')&&!analyzing()&&!autoBusy&&!unknownOutcome()&&!acceptedAhead();
 const groupingFailure=()=>!agentFirst()&&GROUPING_RETRY_STATES.has(run?.state)&&Boolean(run.groupingDecision||run.groupingRetry);
 const canSupplement=()=>canEditInput()&&!hasDecision()&&!hasGroupFailures()&&!intakeRecovery()&&!groupingFailure();
 const multipleCandidates=()=>(run?.result?.candidates?.length??0)>1;
 const presentation=candidate=>primaryPresentation(run,candidate?.candidateId,detail?.currentInput?.form,quickEdit?.edits);
 const manualNameCapability=candidate=>{
  const value=run?.manualNameConfirmation;
  return value?.contractVersion==='manual-name-confirmation/1.0'&&value.candidateId===candidate?.candidateId&&
   run.inputRevision===detail?.revision?value:null;
 };
 const ownSubmission=()=>Boolean(ctx.session()?.user?.id&&ctx.session().user.id===detail?.submitterId);
 const canManualName=candidate=>Boolean(!agentFirst()&&manualNameCapability(candidate)?.eligible&&ownSubmission()&&
  actionAllowed(detail,'activities.propose')&&canSupplement()&&['succeeded','partial'].includes(run?.state)&&
  !resolvedIds().has(candidate.candidateId)&&!confirmation&&!groupEdit);
 const manualDirty=()=>Boolean(quickEdit&&(Object.keys(quickEdit.edits).length||quickEdit.append.trim()||quickEdit.files.length||
  Object.keys(quickEdit.declarations??{}).length));
 function manualNameHtml(candidate){
  const capability=manualNameCapability(candidate);
  if(agentFirst()||!capability||!ownSubmission()||!actionAllowed(detail,'activities.propose')||resolvedIds().has(candidate.candidateId))return '';
  if(!capability.eligible)return capability.message?`<p class="hint">${h(capability.message)}</p>`:'';
  const platform=(catalog.platforms??[]).find(item=>item.code===capability.platformCode)?.label??capability.platformCode;
  return `<div class="b-manual-name-note"><p>原表单和材料名称不同，已确认是同一场${h(platform)}活动 ${h(capability.sourceActivityId)}。${capability.existingActivity?'可关联已有活动，保留本次原始来源。':'是否沿用提报名直接建档？'}</p>
   <p>提报名：${h(capability.submissionName)}<br>材料名称：${(capability.materialNames??[]).map(h).join('、')}</p>
   <p class="hint">本次为人工确认；${h(capability.originalProductRowCount??0)} 条商品原始记录与附件保留，DeepSeek 状态按实际回执显示。</p>
   ${capability.existingActivity?`<p>已有活动：<a href="#activity/${h(capability.existingActivity.activityId)}">${h(capability.existingActivity.name??capability.existingActivity.activityId)}</a></p>`:''}</div>`;
 }
 function primaryGroupEntry(candidate,saved){
  if(agentFirst()||!catalog.primaryRecognition||(!canSupplement()&&!run?.primaryRecognition))return '';
  const status=run?.primaryRecognition?.status;
  const labels={queued:'识别已排队',running:'正在识别',succeeded:'识别已完成',failed:'识别失败，可重试',cancelled:'识别已取消',interrupted:'识别已中断',stale:'识别结果已过期'};
  const reading=['queued','running','succeeded'].includes(status);
  const disabled=busy||(!reading&&(!canSupplement()||catalog.primaryRecognition.enabled!==true));
  return `<button type="button" data-sub-primary-group="${h(candidate.candidateId)}"${disabled?' disabled':''}>${reading?'查看 DeepSeek 识别':'用 DeepSeek 识别'}</button><span class="hint">${h(labels[status]??(catalog.primaryRecognition.enabled===false?'识别暂不可用':'尚未发送'))}</span>`;
 }

 function supplementDefaults(){
  if(!quickEdit||quickEdit.revision!==detail?.revision||multipleCandidates())return {};
  const single=run?.result?.candidates?.[0];
  const projected=primaryPresentation(run,single?.candidateId,quickEdit.base.form);
  return Object.fromEntries(Object.entries(projected.defaults).filter(([key])=>QUICK_FIELDS.includes(key)));
 }
 const supplementValues=()=>({...quickEdit?.base.form,...supplementDefaults(),...quickEdit?.edits});
 function visibleReasons(candidate,codes=[]){
  const projection=presentation(candidate),fields=projection.candidate?.fields??{};
  const ready=key=>valuePresent(fields[key]?.value)&&fields[key]?.status!=='conflict';
  return codes.filter(code=>{
   if(code==='FIELD_CONFLICT'&&!run?.agentRecognition&&!Object.entries(fields).some(([key,field])=>key!=='activityName'&&field.status==='conflict')&&primaryGroupFor(run,candidate?.candidateId)?.fields?.activityName?.status!=='conflict')return false;
   if(!projection.projected)return true;
   if(code==='EXPLICIT_NAME_REQUIRED')return !ready('activityName');
   if(code==='REQUIRED_CONTEXT_MISSING')return !['activityName','type','rulesText'].every(ready);
   if(code==='FIELD_CONFLICT')return Object.values(fields).some(field=>field.status==='conflict');
   if(code==='IDENTITY_INCOMPLETE')return !['platformCode','sourceActivityId','startAt','endAt'].every(ready)||
    fields.type?.value==='brand'&&!ready('brandIds');
   return true;
  });
 }
 const groupedAuto=()=>!agentFirst()&&autoCapability?.enabled===true&&autoCapability.contractVersion==='auto-resolution/1.0'&&
  autoCapability.ruleVersion==='grouped-intake-auto/1.0';
 const groupOutcome=id=>autoStatus?.groups?.find(item=>item.candidateId===id);
 const groupCanCheck=candidate=>!groupedAuto()||groupOutcome(candidate.candidateId)?.outcome==='needs_confirmation'||
  hasDecision()&&!autoStatus?.groups;
 const groupCheckLabel=candidate=>groupedAuto()&&groupOutcome(candidate.candidateId)?.reasonCodes?.some(code=>
  ['EXPLICIT_NAME_REQUIRED','REQUIRED_CONTEXT_MISSING','BRAND_PLATFORM_SELECTION_REQUIRED'].includes(code))?'补充本组':'核对本分组';
 const canCheckGroup=candidate=>candidate&&!run?.couponGroupingIssues?.[candidate.candidateId]&&!groupBadCase(candidate.candidateId)&&!resolvedIds().has(candidate.candidateId)&&['succeeded','partial'].includes(run?.state)&&
  canConfirmManually()&&!unknownOutcome()&&!acceptedAhead()&&groupCanCheck(candidate)&&actionAllowed(detail,'activities.propose');
 function canDirectConfirm(candidate){
  if(!agentFirst()||run?.result?.candidates?.length!==1||confirmation||groupEdit||hasDecision()||!canCheckGroup(candidate))return false;
  const fields=candidate.fields;
  const dates=primaryFields(run,candidate.candidateId,detail.currentInput?.form);
  if(!activityDate(dates?.startAt)||!activityDate(dates?.endAt)||Date.parse(dates.endAt)<Date.parse(dates.startAt)||
     hasCouponScope(agentCandidateMetadata(run,candidate.candidateId)?.activityMethod?.value,fields.rulesText?.value))return false;
  if(fundingQuestions(candidate.candidateId).length)return false;
  if(needsMethodCheck(candidate))return false;
  if(submitterPriceIssues().length)return false;
  return fields.activityName?.status==='explicit'&&valuePresent(fields.activityName.value)&&
   (!valuePresent(fields.sourceActivityId?.value)||valuePresent(fields.platformCode?.value))&&
   !Object.values(fields).some(field=>field.status==='conflict')&&
   agentCandidateMetadata(run,candidate.candidateId)?.activityMethod?.status!=='conflict';
 }
 function needsMethodCheck(candidate){
  const method=agentCandidateMetadata(run,candidate?.candidateId)?.activityMethod;
  if(method?.status!=='missing'||!candidate?.productRows?.length)return false;
  return (run.result.materialResults??[]).some(document=>document.kind==='xlsx'&&document.tableRows?.some(row=>{
   const labels=new Set(row.cells.map(cell=>cell.text.trim()));
   return ['子活动ID','报名价','平台补贴','活动价'].every(label=>labels.has(label));
  }));
 }
 const groupButton=(candidate,label,primary=false)=>`<button type="button" data-candidate-select="${h(candidate.candidateId)}"${primary?' class="primary"':''}${busy||(agentFirst()?(!currentAgentBinding()||manualDirty()):catalog.primaryRecognition&&!primaryGroupFor(run,candidate.candidateId))?' disabled':''}>${h(label)}</button>`;
 const resultReasons=()=>new Set(autoStatus?.result?.reasonCodes??[]);
 function currentProductRecognition(){
  const result=detail?.productRecognition,b=result?.binding;
  return b?.submissionId===detail?.submissionId&&b.inputRevision===detail.revision&&
   b.runId===run?.runId&&b.inputDigest===run.inputDigest&&b.analysisDigest===run.analysisDigest?result:null;
 }
 function currentProductGroup(candidateId){
  return currentProductRecognition()?.groups?.find(item=>item.candidateId===candidateId&&item.status==='ready');
 }
 function directoryBrandEvidence(candidateId){
  return currentProductGroup(candidateId)?.productBrandEvidence??[];
 }
 const brandRecognition=id=>recognizedGroupBrands(run,id,catalog,currentProductGroup(id));
 function directoryBrandHtml(evidence,recognized=[]){
  const names=status=>evidence.filter(item=>item.evidenceStatus===status&&!recognized.includes(item.directoryBrand)).map(item=>h(item.directoryBrand)).join('、');
  const verified=names('product_identity_verified');
  const candidates=names('product_candidate');
  return (verified?`<p class="hint">商品目录品牌（商品身份已核对）：${verified}</p>`:'')+
   (candidates?`<p class="hint">商品目录品牌候选：${candidates}</p>`:'');
 }
 const currentProblems=candidate=>intakeProblems(run,detail.currentInput,candidate,
  new Set([...(multipleCandidates()?new Set(groupOutcome(candidate?.candidateId)?.reasonCodes??[]):resultReasons())].filter(code=>!(manualNameCapability(candidate)?.eligible&&code==='SOURCE_CONFLICT'))),catalog,quickEdit?.edits,brandRecognition(candidate?.candidateId)).map(problem=>
   problem.kind==='products'&&candidate?.productRows?.length
    ?{...problem,text:`已识别商品 ${candidate.productRows.length} 个`,hints:[]}:problem);
 const currentMaterialProblem=()=>materialProblem(run,detail.currentInput,detail.materials??[],resultReasons());


 const obsoleteAutoRequest=record=>groupedAuto()&&record?.action==='submissions.auto_resolve'&&
  (record.body&&!Object.hasOwn(record.body,'candidateId')||
   ['1.0.2','1.0.3','1.0.5','1.0.6','1.0.7'].some(version=>version!==localIntakeAgentVersion(groupReview)&&
    (detail.runs??[]).find(item=>item.runId===(record.body?.analysisRunId??run?.runId))?.result?.engineVersion===version));
 function receiptActions(record){
  const check=actionButton(record?'check-receipt':'refresh','核对处理结果',{primary:true,disabled:busy});
  if(!record?.body||!canResendRecord(record))return check;
  return check+`<details class="b-receipt-retry"><summary>原请求恢复</summary>
   <p>恢复前会再查一次原回执。仍无回执时，沿用原请求键和原正文。</p>
   ${actionButton('retry-receipt','恢复原请求',{disabled:busy})}</details>`;
 }
 function canResendRecord(record){
  if(agentFirst()&&((record.action==='analysis.start'&&record.body?.agentConsent!==true)||record.action==='submissions.auto_resolve'||record.action==='submissions.grouping_decide'))return false;
  if(record.action==='activities.supplement')return groupReview.supplements===true;
  if(record.action==='submissions.grouping_decide')
   return groupReview.groupingDecisions===true&&!record.slot.startsWith(GROUPING_RETRY_PREFIX);
  return !obsoleteAutoRequest(record);
 }
 async function resolveOriginal(record,retry,requestSignal){
  const {slot,action,method,path,body}=record;
  const obsolete=obsoleteAutoRequest(record)||agentFirst()&&!canResendRecord(record);
  const {result}=await ctx.commands.resolve(slot,{retireMissing:obsolete});
  if(result||!canResendRecord(record)||!retry||!body||requestSignal!==signal||!alive())return result;
  return ctx.commands.run(slot,action,method,path,body,{signal:requestSignal});
 }
 async function checkReceipt(retry,requestSignal){
  const record=pendingWrite();
  if(!record)return;
  const {slot,action,body}=record;
  const result=await resolveOriginal(record,retry,requestSignal);
  if(requestSignal!==signal||!alive())return;
  if(action==='submissions.delete'){
   if(result){
    checkDeleted(result.data,detail.submissionId,body?.expectedRevision??Number(slot.split(':').at(-1)));
    ctx.notice('提报已删除。');
   }else ctx.notice('服务暂未返回原删除回执，请在列表中核对。');
   ctx.navigate('#submissions');
   return;
  }
  const data=result?.data;
  if(data&&['submissions.create','submissions.update'].includes(action)&&data.submissionId&&Number.isInteger(data.revision)){
   ctx.commands.rememberIntake(slot,{phase:'received',submissionSlot:slot,submissionKey:record.key,
    submissionId:data.submissionId,revision:data.revision,analysisSlot:'analysis:'+data.submissionId+':'+data.revision+':0'});
   if(data.submissionId!==detail.submissionId){
    ctx.notice('已核对服务实际受理结果。');
    ctx.navigate('#submissions/'+data.submissionId);
    return;
   }
  }
  if(action==='analysis.start'&&data?.runId){
   const flow=ctx.commands.intakes?.().find(item=>item.analysisSlot===slot&&item.submissionId===detail.submissionId);
   const revision=data.inputRevision??body?.expectedRevision??flow?.revision;
   if(Number.isInteger(revision))ctx.commands.rememberIntake(flow?.slot??slot,{phase:'analyzing',submissionId:detail.submissionId,
    revision,runId:data.runId,analysisKey:record.key});
  }
  if(data&&action==='activities.supplement'){
   const matches=(detail.resolutions??[]).find(item=>item.activityId===data.activityId&&item.versionId===data.baseVersionId&&
    (!body?.source||item.candidateId===body.source.candidateId));
   if(!matches)throw new Error('补充回执与本次提报的分组不一致。');
   rememberSupplement(data,matches);
   groupEdit=null;
  }
  if(data&&action==='submissions.grouping_decide'){
   rememberGrouping(data,slot,body?.expectedRevision??detail.revision,body?.analysisRunId);
   groupEdit=null;
  }
  if(!await readDetail(detail.submissionId,requestSignal))return;
  if(result&&['submissions.auto_resolve','submissions.grouping_decide'].includes(action))await followAnalysis();
  await readSupplementSummaries(requestSignal);
  if(requestSignal!==signal||!alive())return;
  ctx.notice(result?'已核对服务实际受理结果。':record.state==='obsolete'?'服务未找到旧建档请求，可使用当前版本重新识别。':'已读取最新提报，服务暂未返回原请求回执。');
  paint();schedule();
 }
 const canChangeGroups=()=>activityIntake()&&detail&&!analyzing()&&!autoBusy&&!unknownOutcome()&&!acceptedAhead()&&
  actionAllowed(detail,'activities.propose')&&!confirmation&&!quickEdit;
 const activityPath=aid=>'/activities/'+encodeURIComponent(aid);
 const versionPath=(aid,vid)=>activityPath(aid)+'/versions/'+encodeURIComponent(vid);
 const groupSaveCandidates=()=> (detail?.resolutions??[]).filter(item=>item.revision===detail.revision&&item.activityId&&item.versionId&&
  ['created','change_saved'].includes(item.outcome)&&
  (Object.hasOwn(item,'currentPendingItems')?item.currentPendingItems:item.pendingItems)?.length);
 function canOpenSupplement(saved){
  const summary=supplementSummaries.get(saved?.versionId);
  return groupReview.supplements===true&&canChangeGroups()&&!groupEdit&&summary?.eligible;
 }
 async function readSupplementSummaries(requestSignal){
  if(requestSignal!==signal||requestSignal.aborted||groupReview.supplements!==true||!detail||analyzing()||
     !actionAllowed(detail,'activities.propose'))return;
  const current=detail;
  const missing=groupSaveCandidates().filter(saved=>!supplementSummaries.has(saved.versionId)||supplementSummaries.get(saved.versionId).refresh);
  await Promise.all(missing.map(async saved=>{
   try{
    const [activity,version]=await Promise.all([
     ctx.adapter.request(activityPath(saved.activityId),{signal:requestSignal}),
     ctx.adapter.request(versionPath(saved.activityId,saved.versionId),{signal:requestSignal})
    ]);
    if(requestSignal!==signal||requestSignal.aborted||current!==detail)return;
    const latest=version.data?.latestSupplement;
    const linked=version.data?.sourceInputs?.some(source=>source.submissionId===current.submissionId&&source.inputRevision===saved.revision&&
     source.analyses?.some(analysis=>analysis.candidateId===saved.candidateId&&current.runs?.some(item=>
      item.runId===analysis.runId&&item.analysisDigest===analysis.analysisDigest&&item.inputRevision===saved.revision)));
    const eligible=linked&&version.data.id===saved.versionId&&version.data.activityId===saved.activityId&&
     (activity.data?.displayVersionId??activity.data?.currentReviewVersionId)===saved.versionId&&
     (latest?.pendingItems??version.data.currentPendingItems??version.data.snapshot?.pendingItems)?.length>0;
    const confirmed=version.data?.snapshot?.resolvedFields??version.data?.snapshot?.confirmedFields;
    const confirmedBrandIds=linked&&Array.isArray(confirmed?.brandIds)?confirmed.brandIds:undefined;
    const confirmedRules=linked?confirmed?.rulesText??activity.data?.rules:undefined;
    const pending=latest?.pendingItems??version.data.currentPendingItems??version.data.snapshot?.pendingItems??[];
    const requirements=linked&&(activity.data?.displayVersionId??activity.data?.currentReviewVersionId)===saved.versionId?
     (version.data.reviewApprovalRequirements??[]).filter(item=>['storeBearing','subsidyPrices','productApplicability'].includes(item.field)):[];
    supplementSummaries.set(saved.versionId,{latest,eligible,requirements,pending,confirmedBrandIds,confirmedRules,confirmedDisplayName:linked?confirmed?.activityName:undefined});
   }catch(e){
    if(requestSignal===signal&&!requestSignal.aborted)ctx.authError?.(e);
   }
  }));
 }
 function rememberSupplement(data,saved){
  if(data.outcome!=='supplement_saved'||data.activityId!==saved.activityId||data.baseVersionId!==saved.versionId||
     !/^[A-Za-z0-9_-]{1,128}$/.test(data.supplementVersionId??'')||data.supplementVersionId===saved.versionId||
     !Number.isInteger(data.activityRevision)||!Array.isArray(data.pendingItems)||!['not_started','initial_pending'].includes(data.reviewState))
   throw new Error('补充回执与当前活动版本不一致，请刷新核对。');
  const latest={versionId:data.supplementVersionId,pendingItems:data.pendingItems,reviewState:data.reviewState};
  supplementSummaries.set(saved.versionId,{...supplementSummaries.get(saved.versionId),latest,pending:data.pendingItems,eligible:data.pendingItems.length>0,requirements:[],refresh:true});
 }
 function rememberGrouping(data,slot,revision,sourceRunId){
  const [, ,failedRunId,expectedDecision]=slot.startsWith(GROUPING_RETRY_PREFIX)?slot.split(':'):[];
  if(data?.state!=='queued'||data.inputRevision!==revision||!GROUPING_ID.test(data.runId??'')||!GROUPING_ID.test(data.decisionId??'')||
     data.runId===sourceRunId||(expectedDecision&&(data.decisionId!==expectedDecision||data.runId===failedRunId)))
   throw new Error('分组回执与当前提报不一致，请刷新核对。');
  ctx.commands.rememberIntake(slot,{phase:'analyzing',submissionId:detail.submissionId,revision,runId:data.runId});
 }
 function groupingRetryRequest(){
  if(!groupingFailure()||!canChangeGroups()||groupEdit||groupReview.groupingDecisions!==true||
     !actionAllowed(detail,'submissions.edit_own')||run.inputRevision!==detail.revision)return null;
  const binding=run.groupingDecision;
  const retry=run.groupingRetry;
  if(!binding||!retry||!GROUPING_ID.test(run.runId??'')||!GROUPING_ID.test(binding.decisionId??''))return null;
  const {sourceRunId,sourceAnalysisDigest,caseId,partitions}=retry;
  if(!GROUPING_ID.test(sourceRunId??'')||sourceRunId===run.runId||!ANALYSIS_DIGEST.test(sourceAnalysisDigest??'')||
     !GROUPING_ID.test(caseId??'')||binding.sourceRunId!==sourceRunId||binding.sourceAnalysisDigest!==sourceAnalysisDigest||
     binding.caseId!==caseId||!Array.isArray(partitions)||!partitions.length||partitions.length>20||
     !partitions.every(part=>Array.isArray(part?.unitIds)&&part.unitIds.length&&part.unitIds.every(id=>GROUPING_ID.test(id??''))))
   return null;
  const source=detail.runs?.find(item=>item.runId===sourceRunId&&item.inputRevision===detail.revision);
  if(source?.state!=='succeeded'||source.analysisDigest!==sourceAnalysisDigest)return null;
  const slot=GROUPING_RETRY_PREFIX+detail.submissionId+':'+run.runId+':'+binding.decisionId;
  if(ctx.commands.get?.(slot)?.state==='confirmed')return null;
  return {slot,body:{expectedRevision:detail.revision,analysisRunId:sourceRunId,
   analysisDigest:sourceAnalysisDigest,caseId,partitions}};
 }
 async function sendGrouping(slot,body,requestSignal){
  const path='/submissions/'+encodeURIComponent(detail.submissionId)+'/grouping-decisions';
  const {data}=await ctx.commands.run(slot,'submissions.grouping_decide','POST',path,body,{signal:requestSignal});
  if(requestSignal!==signal||requestSignal.aborted||!alive())return false;
  rememberGrouping(data,slot,body.expectedRevision,body.analysisRunId);
  return true;
 }
 async function retryGrouping(){
  const request=groupingRetryRequest();
  if(!request)return;
  const requestSignal=signal;
  busy=true;error=null;paint();
  try{
   if(!await sendGrouping(request.slot,request.body,requestSignal))return;
   ctx.notice('分组识别重试已受理，正在读取新进度。');
   await refresh();
  }catch(e){
   if(requestSignal===signal&&!requestSignal.aborted&&!ctx.authError?.(e))error=e;
  }finally{
   busy=false;
   if(requestSignal===signal)paint();
  }
 }
 function currentGroupingCases(){
  if(agentFirst()||!['succeeded','partial'].includes(run?.state))return [];
  const materials=new Map((detail.materials??[]).map(item=>[item.materialId,item]));
  return visibleGroupingCases(run,resolvedIds()).filter(item=>item.units.every(unit=>{
   const material=materials.get(unit.materialId);
   return material&&material.available!==false&&material.sha256===unit.sourceSha256;
  }));
 }
 function groupingActions(){
  if(groupEdit||confirmation)return '';
  const cases=currentGroupingCases();
  if(!cases.length)return '';
  if(groupReview.groupingDecisions!==true||!canChangeGroups())return '<p class="b-note">部分材料的活动归属待核对，当前无法保存分组决定。</p>';
  return `<div class="b-note"><p>部分材料的活动归属待核对。</p><div class="actions">${cases.map((item,i)=>
   actionButton('open-grouping',cases.length>1?`核对归属 ${i+1}`:'核对材料归属',{disabled:busy,
    extra:' data-grouping-case="'+h(item.caseId)+'"'})).join('')}</div></div>`;
 }
 function groupEditorHtml(){
  if(!groupEdit)return '';
  return groupEdit.kind==='supplement'?groupSupplementHtml(groupEdit,{
   catalog,productsForRow:productItems,busy,filesHtml,own:ownSubmission()
  }):groupingHtml(groupEdit,detail.materials??[],busy);
 }
 function captureGroupForm(form){
  if(!groupEdit||groupEdit.stale)return;
  form??=document.querySelector(groupEdit.kind==='supplement'?'#b-group-supplement-form':'#b-grouping-form');
  if(!form)return;
  const data=new FormData(form);
  if(groupEdit.kind==='grouping'){
   groupEdit.values=Object.fromEntries(groupEdit.item.units.map(unit=>[unit.unitId,data.get('unit:'+unit.unitId)]));
  }else{
   const products=groupEdit.target.snapshot.unresolvedRows??[];
   const keys=['startOn','endOn','rulesText','platformCode','sourceActivityId','productScopeIntent','storeBearing','rawText',...products.map(id=>'product:'+id)];
   groupEdit.values={...Object.fromEntries(keys.map(key=>[key,data.get(key)])),addBrandIds:data.getAll('addBrandIds'),
    ...subsidyCorrectionValues(groupEdit.target.snapshot.subsidyPrices,data)};
  }
 }
 function focusGroupHeading(id){
  const heading=document.querySelector('#'+id);
  heading?.focus({preventScroll:true});
  heading?.scrollIntoView({block:'nearest'});
 }
 async function openGroupSupplement(id,reload){
  const prior=reload?groupEdit:null;
  const saved=prior?.saved??groupSaveCandidates().find(item=>item.candidateId===id);
  if(!saved||(!reload&&!canOpenSupplement(saved))||
     (reload&&(prior?.kind!=='supplement'||!canChangeGroups()||groupReview.supplements!==true)))return;
  const requestSignal=signal,current=detail;
  const same=()=>requestSignal===signal&&!requestSignal.aborted&&current===detail;
  busy=true;error=null;paint();
  try{
   const [activity,base]=await Promise.all([
    ctx.adapter.request(activityPath(saved.activityId),{signal:requestSignal}),
    ctx.adapter.request(versionPath(saved.activityId,saved.versionId),{signal:requestSignal})
   ]);
   const latestId=base.data?.latestSupplement?.versionId;
   if(!same())return;
   const latest=latestId?(await ctx.adapter.request(versionPath(saved.activityId,latestId),{signal:requestSignal})).data:null;
   if(!same())return;
   const target=boundSupplement({detail:current,saved,activity:activity.data,base:base.data,latest});
   if(!target||!target.snapshot.pendingItems.length)throw new Error('当前版本没有可补资料，或原分组绑定已变化。');
   const observed=currentProductGroup(id)?.observedRules;
   const values={};
   if(observed?.status==='explicit'&&target.snapshot.pendingItems.some(item=>item.field==='rulesText'))
    values.rulesText=observed.summary;
   if(target.snapshot.pendingItems.some(item=>item.field==='brandIds')){
    const available=brandRecognition(id).ids.filter(brandId=>!(target.fields.brandIds??[]).includes(brandId));
    if(available.length+(target.fields.brandIds??[]).length<=20)values.addBrandIds=available;
   }
   groupEdit={kind:'supplement',saved,target,values:prior?.values??values,files:prior?.files??[]};
  }catch(e){if(same()&&!ctx.authError?.(e))error=e}
  finally{busy=false;if(same()){paint();if(groupEdit)focusGroupHeading('b-group-supplement-title')}}
 }
 function openGrouping(id){
  if(groupEdit||groupReview.groupingDecisions!==true||!canChangeGroups())return;
  const item=currentGroupingCases().find(item=>item.caseId===id);
  if(!item)return;
  groupEdit={kind:'grouping',item,runId:run.runId,values:{}};
  error=null;paint();focusGroupHeading('b-grouping-title');
 }
 function closeGroupEditor(){
  const edit=groupEdit;
  if(!edit)return;
  groupEdit=null;error=null;paint();
  const name=edit.kind==='supplement'?'groupCandidate':'groupingCase';
  const id=edit.kind==='supplement'?edit.saved.candidateId:edit.item.caseId;
  [...(document.querySelectorAll?.('[data-group-candidate], [data-grouping-case]')??[])].find(item=>item.dataset[name]===id)?.focus();
 }
 async function saveGroupEditor(form){
  const edit=groupEdit;
  if(!edit||edit.stale||busy||!canChangeGroups()||
     form.id!==(edit.kind==='supplement'?'b-group-supplement-form':'b-grouping-form'))return;
  const requestSignal=signal;
  const current=()=>requestSignal===signal&&!requestSignal.aborted&&detail&&alive();
  captureGroupForm(form);
  busy=true;error=null;paint();
  try{
   if(edit.kind==='supplement'){
    if(groupReview.supplements!==true)return;
    const patch=supplementPatch(edit);
    if(!Object.keys(patch).length&&!edit.files.length)throw new Error('请填写至少一项补充资料。');
    if(edit.files.length){
     const ids=[];
     for(const file of edit.files){
      if(!current())return;
      ids.push(await upload(file));
     }
     if(!current())return;
     patch.materialIds=[...new Set(ids)];
    }
    const {activity,base,latest,source}=edit.target;
    const slot='group-supplement:'+source.submissionId+':'+base.id+':'+activity.revision;
    const body={expectedActivityRevision:activity.revision,expectedSupplementVersionId:latest?.id??null,source,patch};
    const {data}=await ctx.commands.run(slot,'activities.supplement','POST',versionPath(activity.id,base.id)+'/supplements',
     body,{signal:requestSignal});
    if(!current())return;
    rememberSupplement(data,edit.saved);
    ctx.notice('补充版本已保存。');
   }else{
    if(groupReview.groupingDecisions!==true||!currentGroupingCases().some(item=>item.caseId===edit.item.caseId)||edit.runId!==run.runId)
     throw Object.assign(new Error('原分析已变化，请核对最新分组。'),{status:409});
    const body={expectedRevision:detail.revision,analysisRunId:run.runId,analysisDigest:run.analysisDigest,
     caseId:edit.item.caseId,partitions:groupingPartitions(edit)};
    const slot='grouping:'+detail.submissionId+':'+run.runId+':'+edit.item.caseId;
    if(!await sendGrouping(slot,body,requestSignal))return;
    ctx.notice('分组决定已受理，正在读取新识别进度。');
   }
   groupEdit=null;
   await refresh();
  }catch(e){
   if(!current()||ctx.authError?.(e))return;
   error=e;
   if(e.status===409&&e.code!=='DUPLICATE_ACTIVITY'&&e.code!=='CHANGE_VERSION_REQUIRED')edit.stale=true;
  }finally{busy=false;if(current())paint()}
 }
 function resultHeader(){
  const source=detail.currentInput;
  const attachments=new Set(source?.materialIds??[]);
  const names=(detail.materials??[]).filter(material=>attachments.has(material.materialId))
   .map(material=>material.originalName??material.filename).filter(Boolean);
  const title=source?.form?.activityName;
  return `<div class="b-heading"><div><p class="b-intake-crumb">活动管理 / <a href="#submissions">我的提报</a> / 提报结果</p>
   <h1>提报结果</h1>${title||names.length?`<p class="muted">${[title,...names].filter(Boolean).map(h).join(' · ')}</p>`:''}
   </div><a class="button" href="#submissions">返回我的提报</a></div>`;
 }
 function processingSummary(actions,status){
  const source=detail.currentInput;
  const attachmentIds=new Set(source?.materialIds??[]);
  const attachments=(detail.materials??[]).filter(material=>attachmentIds.has(material.materialId));
  const readyToCreate=autoBusy;
  const phase=readyToCreate?'正在按活动分组处理':({
   queued:'正在排队',starting:'正在准备提报材料',decoding:'服务端正在读取材料',local_planning:'服务端正在整理待发送的材料',
   analyzing:agentFirst()?'DeepSeek 正在识别活动、字段和商品':'正在识别活动与匹配商品',
   agent_recognizing:'DeepSeek 正在识别活动、字段和商品',complete:'识别结果待读取'
  }[run?.phase]??'正在读取本次分析状态');
  const statusLine=run?.state==='cancel_requested'?'正在取消本次识别':phase;
  const wait=agentFirst()&&ACTIVE.has(run?.state)?analysisWaitLabel(run):'';
  return `<section class="panel b-intake-outcome" aria-live="polite">
   <div><h2>${h(status)}</h2><p>${h(statusLine)}</p>
   ${wait?`<p data-analysis-wait role="timer" aria-live="off">${wait}</p>`:''}</div>${badge(agentFirst()?'DeepSeek Agent':'材料分析','blue')}</section>
   <div class="b-intake-progress-grid">
    ${panel('处理进度',`<ol class="b-intake-steps">
     <li class="done"><span aria-hidden="true">✓</span><div><strong>保存材料</strong><p>已保存 · 输入修订 ${h(detail.revision)}</p></div></li>
     <li ${readyToCreate?'class="done"':'class="current" aria-current="step"'}><span aria-hidden="true">${readyToCreate?'✓':'2'}</span>
      <div><strong>识别活动</strong><p>${readyToCreate?'已读取分析结果':h(statusLine)}</p></div></li>
     <li ${readyToCreate?'class="current" aria-current="step"':''}><span aria-hidden="true">3</span><div><strong>${agentFirst()?'核对后建档':'创建或待核对'}</strong>
      <p>${readyToCreate?'正在处理已识别的分组':'等待识别结果'}</p></div></li></ol>
     <div class="b-intake-progress-note"><strong>${h(statusLine)}</strong>
     <p class="hint">${agentFirst()?'材料较多时可能需要几分钟。完成后显示活动分组和商品清单，供你核对；可取消本次识别。':'完成后显示活动链接和需要你处理的事项。'}</p></div>
     ${actions?`<div class="actions">${actions}</div>`:''}`)}
    ${panel('本次材料',`<p class="hint">${attachmentIds.size?`已保存 · ${attachmentIds.size} 个附件`:'本次没有上传附件'}${valuePresent(source?.rawText)?' · 已保存文字内容':''}</p>
     ${attachments.length?materialHtml(attachments):''}<p class="hint">你可以返回「我的提报」查看已保存的材料和进度。</p>`)}
   </div>`;
 }
 function displayedPending(saved){
  return activityIntake()?saved?.currentPendingItems:saved?.pendingItems;
 }
 function groupRows(candidates,currentResolutions,canShowIssues,unknownGroupId){
  const savedByGroup=new Map(currentResolutions.map(item=>[item.candidateId,item]));
  return candidates.map((candidate,index)=>{
   const failed=groupBadCase(candidate.candidateId);
   const outcome=groupOutcome(candidate.candidateId);
   const saved=savedByGroup.get(candidate.candidateId)??(outcome?.activityId?outcome:null);
   const pending=pendingItemLabels(displayedPending(saved));
   const review=awaitingReview(saved?.reviewState,saved?.reviewBlockedReason);
   const needsCheck=!failed&&(outcome?.outcome==='needs_confirmation'||canCheckGroup(candidate));
   const unknownGroup=unknownGroupId===candidate.candidateId;
   const label=saved?({created:pending.length?'已创建 · 有待补充':'已创建',linked:'已关联活动',
    change_saved:'已保存补充版本',already_resolved:'已有活动'}[saved.outcome]??'已有处理结果'):
    failed?'本组识别失败':unknownGroup?'回执待核对':outcome?.state==='rejected'?'自动建档未完成':canDirectConfirm(candidate)?'可建档':needsCheck?'待核对':'待处理';
   const dateFields=currentAgentBinding()?primaryFields(run,candidate.candidateId,detail.currentInput?.form):null;
   const periodBasis=canShowIssues&&!saved&&!failed&&dateFields?run.candidatePeriods?.[candidate.candidateId]:null;
   const missingDates=canShowIssues&&!saved&&!failed&&dateFields
    ?Object.keys(ACTIVITY_DATE_FIELDS).filter(field=>!activityDate(dateFields[field])):[];
   const issues=canShowIssues&&!saved&&!failed?currentProblems(candidate):[];
   const issue=issues.find(item=>(!missingDates.includes(item.field)||candidate.fields?.[item.field]?.status==='conflict')&&
    (!submitterPriceIssues().length||!['products','check'].includes(item.kind)));
   const projected=presentation(candidate);
   const reasons=canShowIssues?[...new Set(visibleReasons(candidate,outcome?.reasonCodes).map(code=>autoReasons[code]??'本组资料待核对'))]:[];
   const name=supplementSummaries.get(saved?.versionId)?.confirmedDisplayName??
    (valuePresent(projected.values.activityName)?projected.values.activityName:unassignedAgentProducts(run,candidate)?
     `未归属商品 · ${candidate.productRows.length} 条`:`分组 ${index+1} · 名称待补充`);
   return {candidate:projected.candidate??candidate,projected,saved,pending,review,needsCheck,label,issue,reasons,name,dateFields,missingDates,periodBasis,failed};
  });
 }
 function manualGroupTask(group){
  const {candidate,saved,dateFields,failed}=group,id=candidate.candidateId;
  if(failed)return null;
  if(saved){
   const summary=supplementSummaries.get(saved.versionId),requirements=summary?.requirements??[];
   if(!requirements.length)return null;
   const action=ownSubmission()&&canOpenSupplement(saved)&&requirements.some(item=>summary.pending.some(p=>p.field===item.field))
    ?actionButton('open-group-supplement',requirements.length===1&&requirements[0].field==='productApplicability'?'确认商品范围':'填写审核所需资料',
      {primary:true,disabled:busy,extra:' data-group-candidate="'+h(id)+'"' }):'';
   return {lines:requirements.map(item=>item.message),action};
  }
  if(!canCheckGroup(candidate)||!dateFields||!currentAgentBinding())return null;
  const draft=initialConfirmation(primaryCandidate(run,id),dateFields),fields=draft.fields;
  const missing=[...manualGroupFields(fields,true)].map(field=>FIELD_LABELS[field]);
  const questions=fundingQuestions(id),unbound=questions.filter(unboundFunding);
  if(questions.length>unbound.length)missing.push(`${questions.length-unbound.length} 档券的出资备注`);
  const recognizedMethod=agentCandidateMetadata(run,id)?.activityMethod?.value;
  if(!tierFundingFor(fields)&&!['yes','no'].includes(fields.storeBearing)&&
     (requiresStoreBearing(recognizedMethod,candidate.fields?.rulesText?.value)||requiresStoreBearing(fields.activityMethod,fields.rulesText,fields.activityName)))
   missing.push('门店是否出钱');
  const lines=missing.length?['提交本分组前，请由提报人填写'+missing.join('、')+'。']:[];
  if(unbound.length)lines.push(`${unbound.length} 档券无法对应原件，请补正材料后重新识别。`);
  if(groupHasCouponScope(candidate,fields)&&scopeNeedsInput(draft.scopeIntent,fields,
     draft.productResolutions.length>0&&draft.productResolutions.every(row=>row.decision==='link'&&row.productId)))
   lines.push('初审通过前，请由原提报人确认商品适用范围。可以先建档。');
  return lines.length?{lines,action:groupButton(candidate,'填写本组资料',true)}:null;
 }
 function manualGroupPanel(groups,tasks){
  if(!tasks.size)return '';
  const content=groups.map(({candidate,name})=>{
   const task=tasks.get(candidate.candidateId);
   return task?`<div><strong>${h(name)}</strong>${task.lines.map(line=>`<p>${h(line)}</p>`).join('')}${task.action}</div>`:'';
  }).join('');
  return `<section class="panel b-manual-attention"><h2>需要提报人填写</h2>${content}</section>`;
 }
 function groupTable(groups,participation,tasks){
  const headers=['处理结果','活动名称','优惠机制','待补或待核对事项','操作'];
  const cells=groups.map(({candidate,projected,saved,pending,review,needsCheck,label,issue,reasons,name,dateFields,missingDates,periodBasis,failed})=>{
   const links=saved?.activityId?`<a href="#activity/${h(saved.activityId)}">查看活动</a>
    ${saved.versionId?`<a href="#review/${h(saved.activityId)}/${h(saved.versionId)}">查看处理版本</a>`:''}`:'';
   const noPending=Array.isArray(displayedPending(saved))?'无待补项':'当前待补情况待核实';
   const groupingIssue=!saved&&run?.couponGroupingIssues?.[candidate.candidateId];
   const observedRule=currentProductGroup(candidate.candidateId)?.observedRules;
   const details=groupingIssue?'<p class="warning">同品牌的满减券需要归到一场活动。请重新交给 Agent 识别。</p>':failed?`<p>${h(failed.reason||'已由人工判定失败。')}</p><p class="hint">整理本组原件后单独重提。</p>`:saved?`${pending.length?pending.map(item=>{
    if(item==='优惠机制'&&observedRule?.status==='explicit')return badge('优惠机制已识别，待确认保存','blue');
    if(item==='优惠机制'&&observedRule?.status==='conflict')return badge('优惠机制有冲突，待核对','amber');
    return badge(item+'待补','blue');
   }).join(' '):`<span class="hint">${noPending}</span>`}
    ${review?'<p class="hint">待安排审核</p>':''}`:
    `${missingDates.length&&!periodBasis?`<p class="b-missing-activity-dates">${missingDates.map(field=>activityDate(detail.currentInput?.form?.[field])
     ?'本组'+ACTIVITY_DATE_FIELDS[field]+'待核对':'缺少'+ACTIVITY_DATE_FIELDS[field]).join('、')}</p>`:''}
    ${reasons.length?reasons.map(reason=>badge(reason,'amber')).join(' '):''}${issue?`<p>${h(issue.text)}</p>${(issue.hints??[]).map(hint=>`<p class="hint">下一步：${h(hint)}</p>`).join('')}`:''}`;
   const latest=saved&&(supplementSummaries.get(saved.versionId)?.latest??saved.latestSupplement);
   const latestLink=latest?.versionId?`<a href="#review/${h(saved.activityId)}/${h(latest.versionId)}">查看补充版本</a>`:'';
   const hasTopAction=Boolean(tasks.get(candidate.candidateId)?.action);
   const supplement=!hasTopAction&&canOpenSupplement(saved)?actionButton('open-group-supplement','补充本分组',{disabled:busy,
    extra:' data-group-candidate="'+h(candidate.candidateId)+'"'}):'';
   const capability=manualNameCapability(candidate);
   const manual=canManualName(candidate)?actionButton('manual-name-confirm',capability.existingActivity?'关联已有活动':'忽略名称差异并建档',{disabled:busy||manualDirty(),primary:true,extra:' data-manual-candidate="'+h(candidate.candidateId)+'"'})+
    actionButton('edit-submission-name','修改提报名',{disabled:busy,extra:' data-manual-candidate="'+h(candidate.candidateId)+'"'}):'';
   const groupLabel=periodBasis?.status==='year_unconfirmed'?'确认活动年份':periodBasis?.status==='year_inferred'?'核对活动日期':missingDates.length?'补齐活动日期':fundingQuestions(candidate.candidateId).length?'填写出资备注':canDirectConfirm(candidate)?'修改后建档':agentFirst()?'核对或补正':groupCheckLabel(candidate);
   const ordinary=links+latestLink+supplement||(!hasTopAction&&(!quickEdit||agentFirst())&&!confirmation&&!groupEdit&&canCheckGroup(candidate)?groupButton(candidate,groupLabel):'');
   const groupFailure=multipleCandidates()&&canReportBadCase(candidate.candidateId)?badCaseButton(candidate.candidateId):'';
   const retry=failed&&ctx.session()?.permissions?.includes('submissions.create')?'<a class="button" href="#report">单独重提</a>':'';
   const action=`<div class="b-group-operation">${primaryGroupEntry(candidate,saved)}${manual||ordinary}${groupFailure}${retry}</div>`;
   const latestPending=pendingItemLabels(latest?.pendingItems);
   const supplementInfo=latest?.versionId?`<p class="hint">补充版本${latestPending.length?'待补：'+h(latestPending.join('、')):'暂无待补项'}</p>`:'';
   const savedBrands=supplementSummaries.get(saved?.versionId)?.confirmedBrandIds;
   const selectedBrands=saved?(savedBrands??[]):(run.result.candidates.length===1?detail.currentInput?.form?.brandIds:[])??[];
   const brandNames=catalog.brands.filter(b=>selectedBrands.includes(b.id)).map(b=>b.name);
   const directoryBrands=directoryBrandEvidence(candidate.candidateId);
   const names=(run.result.conflicts??[]).filter(c=>c.field==='activityName'&&c.evidenceIds?.some(id=>candidate.fields?.activityName?.evidenceIds?.includes(id)));
   const nameSource=names.length||projected.sourceNotes.length?`<details><summary>查看名称来源</summary>${names.map(c=>`<p>提报名：${h(c.manualValue)}</p><p>表内名：${h((c.extractedValues??[]).join('、'))}</p>`).join('')}${projected.sourceNotes.map(note=>`<p>${h(note.text)}</p>${note.values.map(value=>`<p>${h(value)}</p>`).join('')}`).join('')}</details>`:'';
   const metadata=agentCandidateMetadata(run,candidate.candidateId);
   const recognized=brandRecognition(candidate.candidateId);
   const brandLabels=saved||selectedBrands.length?[]:recognized.unresolved;
   const fieldIds=candidate.fields?.brandIds?.status==='explicit'?candidate.fields.brandIds.value:undefined;
   const proposal=fieldIds?.length?fieldIds:recognized.ids;
   const pendingBrands=saved||selectedBrands.length||recognized.conflict?[]:catalog.brands.filter(b=>proposal.includes(b.id)).map(b=>b.name);
   const brandSummary=(brandNames.length?`<p>关联品牌：${h(brandNames.join('、'))}</p>`:'')+
    (pendingBrands.length&&issue?.kind!=='brandConfirmation'?`<p>已识别${h(pendingBrands.join('、'))}，待确认关联。</p>`:'')+
    (brandLabels.length?`<p>已识别品牌：${brandLabels.map(h).join('、')}（待核对主档）</p>`:'')+
    (!saved&&metadata?.brandNames?.status==='conflict'?'<p class="hint">材料品牌存在冲突，请核对原文。</p>':'')+
    (!brandNames.length&&!pendingBrands.length&&!brandLabels.length?(saved&&savedBrands===undefined?'<p>品牌关联以已保存版本为准。</p>':'<p>关联品牌：未关联品牌，可后补</p>'):'')+
    (!brandNames.length&&!pendingBrands.length&&!brandLabels.length&&!saved&&hasMaterialBrandWithoutAgentResult(run,candidate)?
     '<p class="hint">原表有品牌，Agent 未返回品牌信息。</p>':'');
   const savedPeriod=saved?.agentRecognitionBinding?.activityPeriod;
   const basic=['type','platformCode','sourceActivityId','startAt','endAt']
    .filter(key=>!periodBasis||!ACTIVITY_DATE_FIELDS[key])
    .map(key=>{
     const day=key==='startAt'?savedPeriod?.confirmedStartOn:key==='endAt'?savedPeriod?.confirmedEndOn:null;
     const fallback=ACTIVITY_DATE_FIELDS[key]&&(!saved||!run.candidatePeriods?.[candidate.candidateId])?dateFields?.[key]:null;
     return [key,day??projected.values[key]??fallback];
    })
    .filter(([,value])=>valuePresent(value))
    .map(([key,value])=>`<span>${h(key==='platformCode'?'报名编号所属平台':supplementLabel(key))}：${h(fieldValueText(key,value,catalog))}</span>`).join(' · ');
   const method=ACTIVITY_METHODS.find(item=>item.code===projected.values.activityMethod)?.label;
   const category=activityCategoryLabel(metadata?.activityCategory);
   const basicSummary=(basic||method||category?`<p class="hint">活动方式：${h(category)}${basic?' · '+basic:''}${method?`${basic?' · ':''}活动玩法：${h(method)}`:''}</p>`:'')+
    (metadata?.activityMethod?.status==='conflict'?'<p class="hint">活动方式存在冲突，请核对后选择。</p>':
      !saved&&needsMethodCheck(candidate)?'<p class="hint">请在「核对本分组」确认活动方式，以确定是否逐商品报名。</p>':'');
   const values=[badge(label,saved?'green':failed||needsCheck?'amber':'blue'),`<strong>${h(name)}</strong>${basicSummary}${couponPeriodHtml(periodBasis)}${brandSummary}${participationSummary(participation.get(candidate.candidateId))}${directoryBrandHtml(directoryBrands,recognized.names)}${nameSource}`,

    valuePresent(supplementSummaries.get(saved?.versionId)?.confirmedRules)?
     `<p>${h(supplementSummaries.get(saved?.versionId).confirmedRules)}</p>`:
     identifiedRule(run,candidate,detail.materials??[],detail.currentInput,projected.analysis,{showEvidence:false,observed:currentProductGroup(candidate.candidateId)?.observedRules}),(details||'<span class="hint">—</span>')+manualNameHtml(candidate)+supplementInfo,action];
   return `<tr role="row">${values.map((value,i)=>`<td role="cell" data-label="${headers[i]}">${value}</td>`).join('')}</tr>`;
  });
  return `<div class="b-intake-group-table"><table role="table" aria-label="活动分组">
   <thead role="rowgroup"><tr role="row">${headers.map(label=>`<th scope="col" role="columnheader">${label}</th>`).join('')}</tr></thead>
   <tbody role="rowgroup">${cells.join('')}</tbody>
  </table></div>`;
 }
 function submitterPriceIssues(){
  return (run?.result?.warnings??[]).filter(item=>
   ['SUBSIDY_PRICE_CONFLICT_SUBMITTER_REVIEW','CHILD_WORKBOOK_PRICE_CONFLICT'].includes(item.code));
 }
 function compactResultHtml(){
  const candidates=run?.result?.candidates??[];
  const badCase=currentBadCase();
  const multi=candidates.length>1;
  const single=multi?null:candidates[0];
  const directConfirm=canDirectConfirm(single);
  const record=pendingWrite();
  const uncertain=unknownOutcome();
  const recovery=intakeRecovery();
  const checkingRevision=acceptedAhead();
  const decision=hasDecision();
  const currentResolutions=(detail.resolutions??[]).filter(r=>r.revision===detail.revision);
  const resolution=currentResolutions[0]??autoStatus?.result??autoStatus?.groups?.find(item=>item.activityId);
  const activityId=detail.activityId??resolution?.activityId;
  const versionId=detail.versionId??resolution?.versionId;
  const showProblems=!uncertain&&!decision&&!analyzing()&&!autoBusy&&!checkingRevision;
  const missingFormName=!valuePresent(detail.currentInput?.form?.activityName)&&!valuePresent(presentation(single).values.activityName);
  const material=showProblems&&!recovery?currentMaterialProblem():null;
  const priceIssues=showProblems?submitterPriceIssues():[];
  const problems=!showProblems?[]:multi||recovery?(missingFormName?[NAME_PROBLEM]:[]):currentProblems(single).filter(issue=>!priceIssues.length||!['products','check'].includes(issue.kind));
  if(material)problems.push(material);
  let status,actions='',recordActions='';
  const editButton=(primary=false)=>actionButton('supplement','补充信息',{primary,disabled:busy});
  const refreshButton=()=>actionButton('refresh','刷新进度',{disabled:busy||autoBusy});
  if(uncertain){
   status='处理结果待核对';
   actions=receiptActions(record);
  }else if(checkingRevision){
   status='补充已保存，待读取新修订';
   actions=refreshButton();
  }else if(autoBusy||analyzing()){
   status=agentFirst()?agentStatusText():run?.state==='cancel_requested'?'正在取消':autoBusy?'正在核对分析结果':'正在分析';
   actions=refreshButton()+(run&&['queued','running'].includes(run.state)&&actionAllowed(detail,'submissions.edit_own')
    ?actionButton('cancel-analysis','取消分析',{disabled:busy}):'');
  }else if(badCase){
   status='提报失败';
   if(ctx.session()?.permissions?.includes('submissions.create'))actions='<a class="button primary" href="#report">新建提报</a>';
  }else if(groupingFailure()){
   status={failed:'分组识别失败',cancelled:'分组识别已取消',interrupted:'分组识别已中断'}[run.state];
   actions=groupingRetryRequest()?actionButton('retry-grouping','重试分组识别',{primary:true,disabled:busy}):refreshButton();
   if(activityId)actions+=`<a class="button" href="#activity/${h(activityId)}">查看活动</a>`;
   if(activityId&&versionId)actions+=`<a class="button" href="#review/${h(activityId)}/${h(versionId)}">查看处理版本</a>`;
  }else if(decision){
   const label={created:pendingItemLabels(displayedPending(resolution)).length?'活动已创建 · 有待补充':'活动已创建',
    linked:'已关联活动',change_saved:'已保存补充版本',already_resolved:'活动已有处理结果'}[resolution?.outcome];
   status=detail.state==='partially_resolved'?'部分分组已处理':label??'活动已有处理结果';
   actions=(!multi&&activityId?`<a class="button primary" href="#activity/${h(activityId)}">查看活动</a>`:'')+
    (!multi&&!candidates.length&&activityId&&versionId?`<a class="button" href="#review/${h(activityId)}/${h(versionId)}">查看处理版本</a>`:'');
  }else if(recovery||!run||!['succeeded','partial'].includes(run.state)){
   status=agentFirst()?agentStatusText():recovery&&detail.revision>1?'补充已保存，分析未完成':
    run?.state==='cancelled'?'材料已保存 · 分析已取消':run||recovery?'材料已保存 · 分析未完成':'材料已保存 · 等待分析';
   if(actionAllowed(detail,'submissions.edit_own')){
    const retryLabel=localFailureTitle(run)?'重新读取并交给 Agent 识别':agentFirst()?
     (run?.agentRecognition?'重新交给 Agent 识别':'交给 Agent 识别'):run||recovery?'重试分析':'开始分析';
    actions=actionButton(recovery?'recover-analysis':'analyze',retryLabel,{primary:true,disabled:busy||agentFirst()&&!agentAvailable()});
    if(multi&&canSupplement()&&!quickEdit&&!confirmation)actions+=editButton();
   }
  }else{
   status=directConfirm?'Agent 识别完成':agentFirst()?agentStatusText():groupedAuto()&&!autoStatus?.groups?'材料已保存 · 可重新识别':'材料已保存 · 分析完成，待补充或核对';
   if((agentFirst()||groupedAuto())&&canEditInput()&&!confirmation&&!hasGroupFailures()){
    const analyze=actionButton('analyze',agentFirst()?(run?.agentRecognition?'重新交给 Agent 识别':'交给 Agent 识别'):localRecognition()?'在服务端重新识别':'重新识别',{disabled:busy||agentFirst()&&!agentAvailable()});
    if(directConfirm)recordActions=`${agentConsentHtml()}<div class="actions">${analyze}</div>`;
    else actions+=analyze;
   }
   if(directConfirm)actions+=actionButton('confirm-direct',busy?'正在建档…':'通过并建档',{primary:true,disabled:busy||manualDirty()});
   if(canSupplement()&&!quickEdit&&!confirmation)actions+=editButton(!directConfirm);
  }
  const groups=checkingRevision||analyzing()||autoBusy||agentFirst()&&run?.agentRecognition&&!currentAgentBinding()&&!decision?[]:groupRows(candidates,currentResolutions,!uncertain,record?.body?.candidateId);
  const showGroups=groups.length>0;
  const groupSummary=[];
  const created=groups.filter(item=>item.saved?.outcome==='created').length;
  const pendingCount=groups.filter(item=>item.pending.length).length;
  const checkCount=groups.filter(item=>!item.saved&&item.needsCheck).length;
  const linked=groups.filter(item=>item.saved?.outcome==='linked').length;
  const changed=groups.filter(item=>item.saved?.outcome==='change_saved').length;
  if(linked)groupSummary.push(`已关联 ${linked} 个活动`);
  if(changed)groupSummary.push(`已保存 ${changed} 个变更版本`);
  if(groups.some(item=>item.label==='回执待核对'))groupSummary.push('有分组回执待核对');
  if(pendingCount)groupSummary.push(`${pendingCount} 个已处理活动还有资料待补`);
  const failedCount=groups.filter(item=>item.failed).length;
  if(failedCount&&multi)groupSummary.push(`${failedCount} 个分组识别失败`);
  if(checkCount&&!directConfirm)groupSummary.push(`${checkCount} 个分组待核对`);
  if(showGroups)groupSummary.push(`识别到 ${groups.length} 个活动分组`);
  if(created&&!groupingFailure()&&status!=='处理结果待核对')status=`识别完成，已创建 ${created} 个活动`;
  const core=problems.length?`<h3>核心问题</h3><ul class="b-result-problems">${problems.slice(0,3).map(item=>`<li>${h(item.text)}</li>`).join('')}</ul>`:'';
  const safeError=error&&!(error.code==='COMMAND_UNKNOWN'&&uncertain)
   ?`<p class="warning" role="alert">${h(businessMessage(error,'请求未完成，请核对后重试。'))}</p>`:'';
  const groupHints=multi&&quickEdit?candidates.filter(group=>!resolvedIds().has(group.candidateId))
   .flatMap(group=>currentProblems(group).filter(issue=>issue.field==='rawAppend')):[];
  const editor=quickEdit&&!confirmation&&canSupplement()?supplementHtml([...problems,...groupHints],canCheckGroup(single)?single:null):'';
  const activeProgress=!uncertain&&!checkingRevision&&(analyzing()||autoBusy);
  if(single&&canReportBadCase(single.candidateId))actions+=badCaseButton(single.candidateId);
  const resultActions=actions?`${agentFirst()&&!activeProgress&&!decision&&!directConfirm&&!localFailureTitle(run)?agentConsentHtml():''}<div class="actions">${actions}</div>`:'';
  const submittedStart=activityDate(detail.currentInput?.form?.startAt);
  const submittedEnd=activityDate(detail.currentInput?.form?.endAt);
  const originalPeriod=multi&&submittedStart&&submittedEnd
   ?`<p class="hint">提报活动日期：${h(datePeriodText(submittedStart,submittedEnd))}</p>`:'';
  const summary=activeProgress?processingSummary(actions,status):`<section class="panel b-intake-outcome">
   <div><h2 class="b-result-status" role="status">${h(status)}</h2>
   ${badCase?`<p>已记录为 bad case${badCase.reason?' · '+h(badCase.reason):''}</p>`:''}
   ${groupSummary.length?`<p>${h(groupSummary.join('。'))}。</p>`:''}
   ${originalPeriod}
   ${uncertain?'<p class="hint">处理结果确认后可继续补充或核对。</p>':''}
   ${!showGroups&&decision?savedDetailsHtml(resolution?{...resolution,pendingItems:displayedPending(resolution)}:null)+
    (activityIntake()&&!Array.isArray(displayedPending(resolution))?'<p class="hint">当前待补情况待核实。</p>':''):''}</div>${resultActions}</section>`;
  const confirmEditor=confirmation&&!uncertain&&!checkingRevision?confirmHtml():'';
  const activeEditor=confirmEditor||(!uncertain&&groupEdit?groupEditorHtml():editor);
  const tasks=new Map();
  if(!activeEditor&&!uncertain&&!checkingRevision&&!badCase){
   for(const group of groups){const task=manualGroupTask(group);if(task)tasks.set(group.candidate.candidateId,task)}
  }
  const manual=activeEditor||manualGroupPanel(groups,tasks);
  const extraReview=uncertain?'':groupingActions();
  const participation=couponParticipation(run?.result);
  const priceDetails=!confirmation&&!decision&&showGroups?groups.map(({candidate})=>subsidyPricesHtml(run?.subsidyPrices?.[candidate.candidateId])).join(''):'';
  const grouped=showGroups?panel('活动分组',`${groupTable(groups,participation,tasks)}${priceDetails}${extraReview}`,`<span class="hint">${groups.length} 组</span>`):extraReview;
  const priceReview=priceIssues.length?panel('价格待提报人确认',
   `<ul class="b-business-list">${priceIssues.map(item=>`<li>${h(businessMessage(item))}</li>`).join('')}</ul>`):'';
  const guidance=core?panel('提报资料',core):'';
  const recognition=currentProductRecognition();
  const productResults=showGroups&&!priceIssues.length&&(recognition||participation.size)?panel('商品识别',groups.map(({candidate,name})=>{
   const matched=recognition?.groups?.find(group=>group.candidateId===candidate.candidateId);
   const marks=participation.get(candidate.candidateId);
   if(!matched&&!marks?.size)return '';
   return `${multi?`<h3>${h(name)}</h3>`:''}${submissionProductsHtml(matched,marks,candidate.productRows)}`;
  }).join('')):'';
  const failure=agentFirst()?agentFailureReason(run,agentCapability):'';
  const readLimit=excelReadLimitHtml(run,detail.currentInput,detail.materials??[]);
  return resultHeader()+(safeError??'')+manual+priceReview+summary+(directConfirm||failure?'':agentSourceHtml())+
   (readLimit||(failure?`<p class="warning" role="alert">${h(failure)}</p>`:''))+grouped+productResults+guidance+
   `<details class="panel b-result-records"><summary>查看完整记录</summary>
    ${recordActions}${recordDeclarations(detail.currentInput,autoStatus?.result??{})}${fullRecordHtml()}</details>`+(agentFirst()?'':model.render(catalog));
 }
 function fullRecordHtml(){
  const info=factsHtml({submissionId:detail.submissionId,submitterId:detail.submitterId,submittedAt:detail.submittedAt,
   inputRevision:detail.revision,activityId:detail.activityId,versionId:detail.versionId,parentSubmissionId:detail.parentSubmissionId});
  const resolutions=(detail.resolutions??[]).map(item=>`<div class="b-source-input">
   <p>${h(stateLabel(item.outcome))} · <a href="#activity/${h(item.activityId)}">${h(item.activityId)}</a> / <a href="#review/${h(item.activityId)}/${h(item.versionId)}">${h(item.versionId)}</a></p>
   <h4>原处理回执</h4>${savedDetailsHtml(item)||'<p class="muted">原回执未记录待补事项。</p>'}</div>`).join('');
  const revisions=(detail.revisions??[]).map(item=>`<h4>输入修订 ${h(item.inputRevision)}</h4>${factsHtml(item.form,catalog)}${recordDeclarations(item)}
   <pre class="b-json">${h(item.rawText||'未提供原文')}</pre>`).join('');
  const runs=(detail.runs??[]).map(item=>rows([h(item.inputRevision),h(stateLabel(item.state)),h(beijing(item.finishedAt??item.createdAt)),h(item.runId)]));
  const groups=run?.result?.candidates??[];
  const historicalModel=agentFirst()?'':(detail.runs??[]).filter(item=>item.runId!==run?.runId).map(item=>{
   const priorGroups=item.result?.candidates??[];
   const source=detail.revisions?.find(input=>input.inputRevision===item.inputRevision);
   return (priorGroups.length?`<details><summary>${agentRecognitionRecord(item)?'Agent 识别':'历史分析'} · 输入修订 ${h(item.inputRevision)} · ${h(item.runId)}</summary>
    ${priorGroups.map((candidate,index)=>`<h4>${h(candidate.fields?.activityName?.value??'分组 '+(index+1))}</h4>
     ${identifiedRule(item,candidate,detail.materials??[],source)}`).join('')}</details>`:'')+
    modelRecognitionRecordHtml(item,source,detail.materials??[]);
  }).join('');
  const openGroups=groups.length?groups.filter(item=>!resolvedIds().has(item.candidateId)):[null];
  const completeProblems=openGroups.flatMap(group=>currentProblems(group).map(item=>
   `<li>${groups.length>1?`<strong>${h(group.fields?.activityName?.value??group.candidateId)}</strong> `:''}${h(item.text)}</li>`)).join('');
  const material=currentMaterialProblem();
  const materialDetails=material?`<li>${h(material.text)}</li>`:'';
  const allProblems=completeProblems||materialDetails?`<h3>全部可处理问题</h3><ul class="b-business-list">${completeProblems}${materialDetails}</ul>`:'';
  const preserve=hasDecision()&&canEditInput()
   ?actionButton('edit','保留旧版本，补充本次来源材料',{disabled:busy}):'';
  return `${info}${resolutions}${agentFirst()?'':allProblems}${error?errorPanel(error):''}${agentFirst()?'':processingHtml(true)+candidateHtml(true)}
   ${panel('原始表单、文字与原件',`${factsHtml(detail.currentInput?.form,catalog)}${recordDeclarations(detail.currentInput)}
    <h3>原始文字</h3><pre class="b-json">${h(detail.currentInput?.rawText||'未提供')}</pre>
    ${submittedOriginalsHtml()}
    <details><summary>输入版本和分析历史</summary>${revisions}
     ${table(['输入修订','分析状态','时间（北京时间）','分析记录'],runs)}${historicalModel}</details>${preserve}`)}`;
 }
 function supplementField(key,value){
  if(key==='activityName')return requiredNameHtml(value);
  const label=declarations()&&key==='platformCode'?'报名编号所属平台':supplementLabel(key);
  if(key==='type')return select(key,label,Object.entries(TYPE_LABELS).map(([code,label])=>({code,label})),value);
  if(key==='platformCode')return select(key,label,catalog.platforms??[],value,{disabled:!!catalogError||!catalog.platforms?.length});
  if(key==='brandIds')return `<div><p>${h(label)}</p>${brandSelectionHtml(value,catalog,{disabled:!!catalogError||!catalog.brands.length})}</div>`;
  if(['startAt','endAt'].includes(key))return input(key,label,activityDate(value),{type:'date'});
  if(['rulesText','brandBenefit','remark'].includes(key))return textarea(key,label,value,key==='rulesText'?10000:5000);
  return input(key,label,value,{maxLength:200});
 }
 function supplementHtml(problems,confirmCandidate){
  const {base,edits,append,more}=quickEdit;
  const defaults=supplementDefaults(),value=supplementValues();
  const stale=quickEdit.revision!==detail.revision;
  const disabled=busy||stale||quickEdit.conflict||!canSupplement();
  const requested=problems.map(item=>item.field);
  const fields=[...new Set([
   ...(!valuePresent(value.activityName)||nameError?['activityName']:[]),
   ...requested.filter(key=>QUICK_FIELDS.includes(key)),...Object.keys(defaults),...(quickEdit.modelFields??[])
  ])];
  const render=keys=>keys.map(key=>{
   const control=supplementField(key,value[key]);
   const hint=problems.find(problem=>problem.field===key)?.hints?.[0];
   return hint?`<div>${control}<p class="hint">${h(hint)}</p></div>`:control;
  }).join('');
  const rawHints=[...new Set(problems.filter(problem=>problem.field==='rawAppend').flatMap(problem=>problem.hints))];
  if(multipleCandidates()&&rawHints.length)rawHints.unshift('涉及多场活动时，请在每条补充前注明活动名称。');
  const rawHelp=rawHints.length?`<ul class="b-business-list hint" id="b-supplement-help">${rawHints.map(hint=>`<li>${h(hint)}</li>`).join('')}</ul>`:'';
  const rawControl=`<label>补充说明<textarea name="rawAppend" rows="3" maxlength="50000"${rawHelp?' aria-describedby="b-supplement-help"':''}>${h(append)}</textarea></label>`;
  const other=QUICK_FIELDS.filter(key=>!fields.includes(key));
  return `<form id="b-supplement-form" class="b-supplement-form" novalidate><h3 tabindex="-1">补充信息</h3>
   <p class="hint">只填写想补充的内容，原文和已上传附件会保留。</p>
   ${stale?`<p class="warning" role="status">当前输入已更新至修订 ${h(detail.revision)}。你的修改仍保留。</p>
    ${canSupplement()?actionButton('rebase-supplement','按最新修订继续补充',{disabled:busy}):''}`:
    quickEdit.conflict?`<p class="warning" role="status">输入修订有冲突，请先核对最新进度。</p>${actionButton('refresh','刷新进度',{disabled:busy})}`:''}
   <fieldset ${disabled?'disabled':''}><div class="b-form-grid">${render(fields)}</div>
    ${catalogError?`<p class="hint">品牌和平台选项暂不可用，已填写内容会保留。</p>${actionButton('retry-catalog','重新读取目录')}`:''}
    ${declarationFields({...base,...quickEdit.declarations})}
    ${rawControl}${rawHelp}
    <div class="b-upload design-upload" data-material-drop><label for="b-material-files">添加附件</label>
    <input id="b-material-files" aria-label="添加附件" type="file" multiple accept=".txt,.md,.csv,.tsv,.xlsx,.docx,.png,.jpg,.jpeg,.webp,.pdf,.doc,.xls"></div>
    ${filesHtml(quickEdit.files)}${base.materialIds.length?`<p class="hint">保留已上传附件 ${base.materialIds.length} 件。</p>`:''}
    <details data-supplement-more ${more?'open':''}><summary>补充其他字段</summary>
     <div class="b-form-grid">${render(other)}</div></details>
   </fieldset>${agentConsentHtml()}<div class="actions">${multipleCandidates()?actionButton('cancel-supplement','取消',{disabled:busy}):''}
   ${confirmCandidate?groupButton(confirmCandidate,groupedAuto()?groupCheckLabel(confirmCandidate):'核对并确认'):''}
   <button type="submit" class="primary" ${disabled||agentFirst()&&!agentAvailable()?'disabled':''}>${busy?'正在保存…':agentFirst()?'保存并交给 Agent 识别':'保存并重新分析'}</button></div></form>`;
 }
 function createSupplement(){
  return {submissionId:detail.submissionId,revision:detail.revision,base:structuredClone(detail.currentInput),
   edits:{},append:'',files:[],more:false};
 }
 function startSupplement(){
  if(!canSupplement()||quickEdit||confirmation)return;
  quickEdit=createSupplement();
  error=null;
  nameError=false;
  paint();
  const field=valuePresent(quickEdit.base.form.activityName)?'h3':'[name="activityName"]';
  document.querySelector('#b-supplement-form '+field)?.focus();
 }
 function captureSupplement(form){
  if(!form||!quickEdit||form.querySelector('fieldset[disabled]'))return;
  const original={...quickEdit.base.form,...supplementDefaults()};
  const data=new FormData(form);
  const originalDates={...original};
  for(const key of ['startAt','endAt'])if(quickEdit.modelFields?.has(key))originalDates[key]=quickEdit.edits[key]??original[key];
  const captured=captureFields(form,originalDates);
  for(const key of QUICK_FIELDS){
   const control=form.querySelector(`[name="${key}"]`);
   if(!control||control.disabled)continue;
   const base=original[key];
   if(key==='brandIds'){
    const available=new Set(catalog.brands.map(brand=>brand.id));
    const initial=(base??[]).filter(id=>available.has(id));
    const selected=captured[key];
    if(initial.length===selected.length&&initial.every(id=>selected.includes(id)))delete quickEdit.edits[key];
    else quickEdit.edits[key]=selected;
    continue;
   }
   const before=['startAt','endAt'].includes(key)?activityDate(base):key==='platformCode'&&!catalog.platforms?.some(platform=>platform.code===base)?'':base;
   if(normalizeLines(data.get(key))===normalizeLines(before))delete quickEdit.edits[key];
   else quickEdit.edits[key]=captured[key];
  }
  const selected=readDeclarations(form,data);
  if(selected){
   quickEdit.declarations={};
   if(selected.applicablePlatformCodes.join(',')!==applicablePlatformCodes(quickEdit.base).join(','))
    quickEdit.declarations.applicablePlatformCodes=selected.applicablePlatformCodes;
   if(selected.productScopeIntent!==(quickEdit.base.productScopeIntent??null))
    quickEdit.declarations.productScopeIntent=selected.productScopeIntent;
  }
  quickEdit.append=String(data.get('rawAppend')??'');
  quickEdit.more=!!form.querySelector('[data-supplement-more]')?.open;
 }
 function rebaseSupplement(){
  if(!quickEdit||!canSupplement()||quickEdit.revision===detail.revision)return;
  quickEdit.base=structuredClone(detail.currentInput);
  quickEdit.revision=detail.revision;
  quickEdit.conflict=false;
  error=null;
  paint();
 }
 function stopWaitTimer(){
  clearInterval(waitTimer);
  waitTimer=null;
 }
 function paint(){
  if(!alive())return;
  const first=run?.result?.candidates?.[0];
  if(activityIntake()&&detail&&!editing&&route==='submissions/'+detail.submissionId&&
     !quickEdit&&!confirmation&&!groupedAuto()&&!multipleCandidates()&&!currentBadCase()&&canSupplement()&&
     !canDirectConfirm(first)&&!(first&&currentAgentBinding()&&manualGroupTask({candidate:first,dateFields:primaryFields(run,first.candidateId,detail.currentInput?.form)})))quickEdit=createSupplement();
  const active=globalThis.document?.activeElement;
  const priorOptional=globalThis.document?.querySelector?.('[data-intake-optional]')?.open;
  const priorField=active?.form?.id==='b-supplement-form'&&(active.name||active.id==='b-material-files')?active:null;
  ctx.frame(editing||route==='report'?intakeHtml():detail?(activityIntake()?compactResultHtml():detailHtml()):listHtml());
  if(!globalThis.document?.querySelector?.('[data-analysis-wait]'))stopWaitTimer();
  else if(waitTimer===null)waitTimer=setInterval(()=>{
   const label=alive()?document.querySelector('[data-analysis-wait]'):null;
   if(!label||!ACTIVE.has(run?.state)){stopWaitTimer();return}
   const elapsed=analysisWaitLabel(run);
   if(label.textContent!==elapsed)label.textContent=elapsed;
  },1000);
  const optional=globalThis.document?.querySelector?.('[data-intake-optional]');
  if(optional&&(priorOptional||brandEditor||brandNotice))optional.open=true;
  if(priorField){
   const form=document.querySelector('#b-supplement-form');
   const field=priorField.name==='applicablePlatformCodes'
    ?[...(form?.querySelectorAll('[name="applicablePlatformCodes"]')??[])].find(control=>control.value===priorField.value)
    :priorField.name?form?.elements.namedItem(priorField.name):form?.querySelector('#b-material-files');
   if(field&&!field.matches(':disabled')&&field.getClientRects().length){
    field.focus({preventScroll:true});
    if(typeof priorField.selectionStart==='number')field.setSelectionRange(priorField.selectionStart,priorField.selectionEnd,priorField.selectionDirection);
   }
  }
  if(route==='submissions'&&deleteTarget){
   const dialog=document.querySelector('#b-delete-submission-dialog');
   if(dialog){
    dialog.addEventListener('cancel',event=>{event.preventDefault();closeDeleteDialog()});
    dialog.showModal();
   }
  }
 }
 async function readDetail(id,requestSignal){
  const env=await ctx.adapter.request('/submissions/'+encodeURIComponent(id),{signal:requestSignal});
  if(requestSignal!==signal||!alive())return false;
  const current=latestRun(env.data);
  if(activityIntake()&&(detail?.revision!==env.data.revision||run?.runId!==current?.runId||run?.analysisDigest!==current?.analysisDigest)){
   if(agentFirst()&&quickEdit&&(current?.result?.candidates?.length??0)>1){
    captureSupplement(document.querySelector('#b-supplement-form'));
    if(!manualDirty())quickEdit=null;
   }
   model.reset();
   autoStatus=null;
   confirmation=null;
   candidateId=null;
  }
  detail=env.data;
  run=current;
  if(groupEdit?.kind==='grouping'&&groupEdit.runId!==run?.runId)groupEdit.stale=true;
  if(activityIntake()&&!agentFirst())model.sync(run);
  if(activityIntake()&&hasDecision())quickEdit=null;
  if(quickEdit&&quickEdit.revision===detail.revision)quickEdit.conflict=false;
  if(quickEdit&&detail.revision===quickEdit.revision+1&&
     ctx.commands.get?.('draft:'+detail.submissionId+':'+quickEdit.revision)?.state==='confirmed')quickEdit=null;
  return true;
 }
 async function followAnalysis(retry=false){
  if(agentFirst())return;
  const pending=activityIntake()?pendingWrite():null;
  if(activityIntake()&&detail&&acceptedAhead())return;
  if(pending&&(pending.action!=='submissions.auto_resolve'||pending.body?.expectedSubmissionRevision!==detail.revision||
     pending.body?.analysisRunId!==run?.runId))return;
  if(!ctx.autoResolve||autoBusy||!detail||!run||!actionAllowed(detail,'submissions.edit_own')||!actionAllowed(detail,'activities.propose'))return;
  const requestSignal=signal;
  const currentDetail=detail;
  try{
   const outcome=await ctx.autoResolve({
    commands:ctx.commands,detail:currentDetail,run,capability:autoCapability,groupReview,signal:requestSignal,retry,
    onStart(){autoBusy=true;paint()}
   });
   if(requestSignal!==signal||!alive())return;
   autoStatus=outcome;
   if(outcome?.reread)await readDetail(currentDetail.submissionId,requestSignal);
  }catch(e){
   if(requestSignal!==signal||!alive()||ctx.authError?.(e))return;
   error=e;
   autoStatus={state:!e.status||e.status>=500||e.code==='COMMAND_UNKNOWN'?'unknown':'rejected'};
  }finally{
   if(requestSignal===signal)autoBusy=false;
  }
 }
 async function lookupTarget(){
  const targetId=confirmation?.targetActivityId;
  if(!targetId){error=new Error('请先填写要核对的内部活动ID。');paint();return}
  const requestSignal=signal;
  const group=candidateId;
  const current=()=>requestSignal===signal&&!requestSignal.aborted&&candidateId===group&&confirmation;
  busy=true;
  error=null;
  paint();
  try{
   const {data}=await ctx.adapter.request('/activities/'+encodeURIComponent(targetId),{signal:requestSignal});
   if(!current())return;
   if(data?.id!==targetId||!Number.isInteger(data.revision))throw new Error('目标活动回执与请求不一致。');
   targetContext=data;
   confirmation.expectedActivityRevision=data.revision;
  }catch(e){if(current()&&!ctx.authError?.(e))error=e}
  finally{busy=false;if(current())paint()}
 }
 async function confirmManualName(id){
  const candidate=run?.result?.candidates?.find(item=>item.candidateId===id);
  if(busy||!canManualName(candidate))return;
  if(manualDirty()){error=new Error('你有尚未保存的补充内容，请先保存并重新核对，再确认建档。');paint();return}
  const capability=manualNameCapability(candidate),target=capability.existingActivity,operationSignal=signal;
  const intent=target?'link_source':'create';
  const body={expectedSubmissionRevision:detail.revision,analysisRunId:run.runId,analysisDigest:run.analysisDigest,candidateId:id,
   intent,targetActivityId:target?.activityId??null,expectedActivityRevision:target?.revision??null,
   manualNameConfirmation:{bindingDigest:capability.bindingDigest,useSubmissionName:true}};
  const slot=`manual-name-confirm:${detail.submissionId}:${detail.revision}:${id}:${capability.bindingDigest}:${intent}`;
  busy=true;error=null;paint();
  try{
   await ctx.commands.run(slot,'submissions.confirm','POST','/submissions/'+encodeURIComponent(detail.submissionId)+'/confirm',body,{signal:operationSignal});
   if(operationSignal!==signal||operationSignal.aborted)return;
   quickEdit=null;ctx.notice(target?'已人工确认并关联已有活动。':'已沿用提报名人工确认建档。');await refresh();
  }catch(e){
   if(ctx.authError?.(e)||operationSignal!==signal||operationSignal.aborted)return;
   if(e.status===409){
    try{await readDetail(detail.submissionId,operationSignal)}catch(readError){if(ctx.authError?.(readError))return}
    error=e.code==='DUPLICATE_ACTIVITY'?new Error('相同平台和主活动编号已有档案，请核对后使用“关联已有活动”。'):e;
   }else error=e;
  }finally{busy=false;if(operationSignal===signal&&!operationSignal.aborted)paint()}
 }
 async function refresh(){
  if(!detail)return;
  const requestSignal=signal;
  if(!await readDetail(detail.submissionId,requestSignal))return;
  await followAnalysis();
  if(requestSignal!==signal||!alive())return;
  await readSupplementSummaries(requestSignal);
  if(requestSignal!==signal||!alive())return;
  paint();schedule();
 }
 function badCaseButton(groupId){
  return `<button type="button" class="b-bad-case-button" data-sub-action="report-bad-case" data-failed-candidate="${h(groupId)}"${busy?' disabled':''}>提报失败</button>`;
 }
 function openBadCaseDialog(trigger){
  const groupId=trigger.dataset.failedCandidate;
  if(!canReportBadCase(groupId)||document.querySelector('#b-bad-case-dialog'))return;
  const requestSignal=signal;
  const {submissionId,revision}=detail;
  const {runId,analysisDigest}=run;
  const dialog=document.createElement('dialog');
  dialog.id='b-bad-case-dialog';
  dialog.setAttribute('aria-labelledby','b-bad-case-title');
  dialog.innerHTML=`<h2 id="b-bad-case-title">确认提报失败？</h2>
   <p>本组的材料和识别结果会保留，失败判定会记入 bad case。</p>
   <form><fieldset>
    <label>判定原因（选填）<textarea name="reason" maxlength="1000" rows="3"></textarea></label>
    <div class="actions"><button type="button" data-bad-case-cancel>取消</button>
     <button type="submit" class="b-bad-case-button">确认提报失败</button></div>
   </fieldset><p class="warning" role="alert" hidden></p></form>`;
  const form=dialog.querySelector('form');
  const fieldset=form.querySelector('fieldset');
  const feedback=form.querySelector('[role="alert"]');
  const cancel=dialog.querySelector('[data-bad-case-cancel]');
  cancel.addEventListener('click',()=>dialog.close());
  dialog.addEventListener('cancel',event=>{if(fieldset.disabled)event.preventDefault()});
  dialog.addEventListener('close',()=>{dialog.remove();if(trigger.isConnected)trigger.focus()},{once:true});
  form.addEventListener('submit',async event=>{
   event.preventDefault();event.stopPropagation();
   if(fieldset.disabled||requestSignal.aborted||!canReportBadCase(groupId))return;
   const reason=form.elements.reason.value.trim();
   const body={expectedRevision:revision,analysisRunId:runId,analysisDigest,candidateId:groupId,reason:reason||null};
   const current=()=>requestSignal===signal&&!requestSignal.aborted;
   feedback.hidden=true;fieldset.disabled=true;busy=true;
   try{
    const {data}=await ctx.commands.run('bad-case:'+submissionId+':'+runId+':'+groupId,REPORT_BAD_CASE,'POST',
     '/submissions/'+encodeURIComponent(submissionId)+'/bad-case',body,{signal:requestSignal});
    if(!current())return;
    if(data?.submissionId!==submissionId||data.badCase?.analysisRunId!==runId||data.badCase?.candidateId!==groupId)
     throw new Error('失败判定回执需核对，请刷新页面。');
    dialog.close();error=null;
    if(candidateId===groupId){confirmation=null;candidateId=null}
    detail={...detail,state:data.state,agentBadCases:[...(detail.agentBadCases??[]),data.badCase],
     agentBadCase:data.state==='failed'?data.badCase:null};
    ctx.notice('已记录本分组识别失败。');
    await refresh();
   }catch(e){
    if(!current()||ctx.authError?.(e))return;
    if(!dialog.open||e.code==='COMMAND_UNKNOWN'){
     if(dialog.open)dialog.close();
     error=e;
    }else{
     feedback.textContent=e.message??'保存失败，请重试。';
     feedback.hidden=false;
    }
   }finally{
    if(current()){busy=false;fieldset.disabled=false;if(!dialog.open)paint()}
   }
  });
  document.querySelector('#main').append(dialog);
  dialog.showModal();
  cancel.focus();
 }
 async function readCatalog(requestSignal){
  catalogError=null;
  try{
   const response=await ctx.adapter.request('/catalogs',{signal:requestSignal});
   // The envelope's completeness describes workbench relations. Catalogs return the master list directly.
   const brands=response.data?.brands;
   if(activityIntake()&&(!Array.isArray(brands)||brands.some(brand=>typeof brand?.id!=='string'||!brand.id||typeof brand.name!=='string'||!brand.name)))throw new Error('品牌目录响应无效');
   if(requestSignal===signal&&!requestSignal.aborted){catalog=response.data;if(catalog.agentFirstRecognition)agentCapability=catalog.agentFirstRecognition;}
  }catch(e){
   if(!activityIntake()||e.status===401||requestSignal.aborted)throw e;
   if(requestSignal===signal){catalog={brands:[],platforms:[]};catalogError=e}
  }
 }
 function schedule(){clearTimeout(timer);if(!alive()||!ACTIVE.has(run?.state))return;timer=setTimeout(async()=>{try{await refresh()}catch(e){if(ctx.authError?.(e))return;if(!signal?.aborted){error=e;paint();schedule()}}},1200)}
 function initialConfirmation(candidate,extracted){
  const fields={...Object.fromEntries(Object.entries(candidate.fields).map(([key,field])=>[key,field.value])),...extracted};
  if(agentFirst()){
   const suppliedIds=run.result.candidates.length===1?detail.currentInput?.form?.brandIds??[]:[];
   const explicitIds=suppliedIds.length?suppliedIds:(candidate.fields.brandIds?.status==='conflict'?[]:fields.brandIds??[]);
   const brandIds=explicitIds.length?explicitIds:brandRecognition(candidate.candidateId).ids;
   if(brandIds.length<=20)fields.brandIds=brandIds;
  }
  const productResolutions=(candidate.productRows??[]).map(row=>{
   const productId=['exact','manual_selected'].includes(row.matchKind)?row.matchedProductId??null:null;
   return {rowId:row.rowId,decision:productId?'link':'unresolved',productId};
  });
  return {fields,productResolutions,intent:'create',targetActivityId:null,expectedActivityRevision:null,
   duplicateAcknowledgement:false,corrections:{products:[],couponTiers:[]},couponFundingAnswers:[],
   scopeIntent:detail.currentInput?.productScopeIntent,subsidyValues:{}};
 }
 function startConfirm(id){
  const extracted=primaryFields(run,id,detail.currentInput?.form);
  if(agentFirst()?(!currentAgentBinding()||!extracted):catalog.primaryRecognition&&!extracted){error=new Error('请先完成当前修订的 DeepSeek Agent 识别，再核对本分组。');paint();return}
  if(agentFirst()&&manualDirty()){error=new Error('你有尚未保存的补充内容，请先保存并交给 Agent 重新识别。');paint();return}
  targetContext=null;candidateId=id;
  confirmation=initialConfirmation(primaryCandidate(run,id),extracted);
  paint();
  const focus=document.querySelector('#b-confirm-form .b-manual-attention :is(input,textarea,select):enabled')??document.querySelector('#b-group-review-title');
  focus?.focus({preventScroll:true});
  focus?.scrollIntoView({block:'nearest'});
 }
 async function submitConfirmation(id,value,operationSignal){
  const binding=currentAgentBinding();
  if(agentFirst()&&!binding)throw new Error('请先完成当前修订的 Agent 识别，再核对建档。');
  const {activityMethod=null,storeBearing,brandSubsidyOnly,companyBearing,...confirmedFields}=value.fields;
  const tierFunding=tierFundingFor(confirmedFields);
  if(!tierFunding)confirmedFields.companyBearing=companyBearing;
  const {products:manualProducts,couponTiers}=value.corrections;
  if(!valuePresent(confirmedFields.activityName))throw new Error('请填写本分组确认的活动名称。');
  if(value.intent!=='link_source'){
   if(!activityDate(confirmedFields.startAt)||!activityDate(confirmedFields.endAt))throw new Error('请填写活动的开始日期和结束日期。');
   if(Date.parse(confirmedFields.endAt)<Date.parse(confirmedFields.startAt))throw new Error('结束日期不能早于开始日期。');
   const candidate=primaryCandidate(run,id);
   const original=candidate?.fields?.rulesText?.value;
   if(!tierFunding&&(requiresStoreBearing(activityMethod,confirmedFields.rulesText,confirmedFields.activityName)||
      requiresStoreBearing(agentCandidateMetadata(run,id)?.activityMethod?.value,original)||couponTiers.length)){
    if(!['shen_jia','bao_hao_jia'].includes(activityMethod)&&explicitMix(original,confirmedFields.rulesText,confirmedFields.activityName)&&storeBearing!=='yes')
     throw new Error('混补券需要门店出资，请确认门店出钱。');
    if(!STORE_BEARING_OPTIONS.some(option=>option.code===storeBearing))throw new Error('请选择门店是否出钱。');
    confirmedFields.storeBearing=storeBearing;
   }
   if(binding&&brandSubsidyOnly&&brandSubsidyOnlyVisible(value.fields,candidate))confirmedFields.brandSubsidyOnly=true;
  }
  const body={
   expectedSubmissionRevision:detail.revision,analysisRunId:run.runId,analysisDigest:run.analysisDigest,candidateId:id,
   intent:value.intent,targetActivityId:value.intent==='create'?null:value.targetActivityId,
   expectedActivityRevision:value.intent==='create'?null:value.expectedActivityRevision,
   activityMethod,confirmedFields,productResolutions:value.productResolutions,duplicateAcknowledgement:value.duplicateAcknowledgement,
   ...(agentFirst()?{agentRecognitionBinding:binding}:catalog.primaryRecognition?{primaryRecognitionBinding:primaryBinding(run,id)}:{})
  };
  if(binding&&value.intent!=='link_source'){
   if(scopeOptions.some(option=>option.code===value.scopeIntent))body.productScopeIntent=value.scopeIntent;
   const questions=fundingQuestions(id,value.intent);
   if(questions.length){
    if(!ownSubmission())throw new Error('请由本次提报人逐档填写出资备注。');
    body.couponFundingAnswers=questions.map(q=>{
     const label=fundingLabel(q);
     if(unboundFunding(q))throw new Error(label+'无法对应原件，请补正材料后重新识别。');
     const entry=value.couponFundingAnswers.find(a=>a.tierId===q.tierId&&a.sourceTierKey===q.sourceTierKey);
     if(!entry?.note)throw new Error('请填写「'+label+'」的出资备注。');
     if([...entry.note].length>1000)throw new Error('「'+label+'」的出资备注最多 1000 字。');
     return entry;
    });
   }
   const corrections=subsidyCorrections(run?.subsidyPrices?.[id],value.subsidyValues);
   if(corrections.length)body.subsidyPriceCorrections=corrections;
  }
  if(binding&&value.intent!=='link_source'&&(manualProducts.length||couponTiers.length)){
   for(const row of manualProducts){
    if(!row.internalCode||!row.matches.some(item=>item.internalCode===row.internalCode)||row.identifier!==row.lookedUp)
     throw new Error('请先查询并选择要补入的公司商品。');
   }
   body.manualCorrections={
    products:manualProducts.map(({internalCode,materialId,location,signupPrice})=>({internalCode,materialId,location,signupPrice:signupPrice||null})),
    couponTiers:couponTiers.map(({name,startOn,endOn,materialId,location})=>({name,startOn,endOn,materialId,location})),
   };
  }
  await ctx.commands.run('confirm:'+detail.submissionId+':'+detail.revision+':'+id,'submissions.confirm',
   'POST','/submissions/'+detail.submissionId+'/confirm',body,{signal:operationSignal});
  if(operationSignal!==signal||operationSignal.aborted)return;
  confirmation=null;candidateId=null;quickEdit=null;
  ctx.notice('本分组已由服务确认，正在读回实际建档 / 关联结果。');
  await refresh();
 }
 async function confirmDirect(){
  const candidate=run?.result?.candidates?.[0];
  if(!canDirectConfirm(candidate))return;
  if(manualDirty()){
   error=new Error('请先保存补充内容，再确认建档。');paint();return;
  }
  const id=candidate.candidateId;
  const value=initialConfirmation(candidate,primaryFields(run,id,detail.currentInput?.form));
  value.fields.brandIds??=[];
  if(!tierFundingFor(value.fields))value.fields.companyBearing={state:'unknown',evidenceIds:[]};
  const operationSignal=signal;
  busy=true;error=null;paint();
  try{
   await submitConfirmation(id,value,operationSignal);
  }catch(e){
   if(operationSignal!==signal||operationSignal.aborted||ctx.authError?.(e))return;
   if(receiveFundingQuestions(e,value)){
    candidateId=id;
    confirmation=value;
   }
   error=e;
  }finally{
   busy=false;
   if(operationSignal===signal)paint();
  }
 }
 function captureConfirm(form){
  if(!form||!confirmation)return;
  const f=new FormData(form);
  const candidate=primaryCandidate(run,candidateId);
  const originalDates={startAt:confirmation.fields.startAt,endAt:confirmation.fields.endAt};
  const corrections=confirmation.corrections;
  const fromForm=(prefix,row,keys)=>{
   for(const key of keys){
    const value=f.get(prefix+key);
    if(typeof value==='string')row[key]=value.trim();
   }
  };
  corrections.products.forEach((row,index)=>fromForm('correction-product:'+index+':',row,
   ['identifier','internalCode','materialId','location','signupPrice']));
  corrections.couponTiers.forEach((row,index)=>fromForm('correction-coupon:'+index+':',row,
   ['name','startOn','endOn','materialId','location']));
  const fields={...captureFields(form,originalDates),activityMethod:f.get('activityMethod')||null};
  const couponFundingAnswers=fundingQuestions(candidateId,f.get('intent')).flatMap(q=>{
   if(unboundFunding(q))return [];
   const note=f.get(fundingNoteName(q))?.trim();
   return note?[{tierId:q.tierId,sourceTierKey:q.sourceTierKey,note}]:[];
  });
  confirmation={fundingRetry:confirmation.fundingRetry,couponFundingAnswers,fields:{...fields,
   storeBearing:f.get('storeBearing')||null,brandSubsidyOnly:f.has('brandSubsidyOnly'),companyBearing:{state:f.get('companyBearing')||'unknown',
    evidenceIds:activityIntake()?(f.get('companyBearing')==='no'?bearingEvidenceIds(candidate):[]):f.getAll('costEvidence')}},
   intent:f.get('intent'),targetActivityId:f.get('targetActivityId')||null,
   expectedActivityRevision:f.get('expectedActivityRevision')===''?null:Number(f.get('expectedActivityRevision')),
   duplicateAcknowledgement:f.has('duplicateAcknowledgement'),corrections,
   scopeIntent:f.get('groupScopeIntent')??confirmation.scopeIntent,
   subsidyValues:subsidyCorrectionValues(run?.subsidyPrices?.[candidateId],f),
   productResolutions:(candidate.productRows??[]).map(p=>({rowId:p.rowId,
    decision:f.get('product:'+p.rowId)?'link':'unresolved',productId:f.get('product:'+p.rowId)||null}))};
 }
 async function changeCorrection(action,button){
  if(!confirmation||!currentAgentBinding()||confirmation.intent==='link_source')return;
  const {kind,index}=button.dataset;
  const {corrections}=confirmation;
  if(action==='add-correction'&&(kind==='products'||kind==='couponTiers')){
   const rows=corrections[kind];
   if(rows.length<(kind==='products'?100:30))rows.push(kind==='products'?{identifier:'',matches:[]}:{});
  }
  if(action==='remove-correction'&&(kind==='products'||kind==='couponTiers')){
   corrections[kind].splice(Number(index),1);
  }
  if(action==='find-correction-product'){
   const row=corrections.products[Number(index)];
   if(!row)return;
   const query=row.identifier;
   row.matches=[];row.internalCode=null;row.lookedUp=null;row.message='';
   if(!query){row.message='请输入商品内部码或 UPC。';paint();return}
   const requestSignal=signal;
   busy=true;paint();
   try{
    const {data}=await ctx.adapter.request('/submissions/'+encodeURIComponent(detail.submissionId)+
     '/correction-products?'+new URLSearchParams({identifier:query}),{signal:requestSignal});
    row.lookedUp=query;row.matches=data.products;
    row.internalCode=row.matches.length===1?row.matches[0].internalCode:null;
    if(!row.matches.length)row.message='公司商品目录未找到该商品，请核对内部码或 UPC。';
   }catch(e){
    if(!ctx.authError?.(e))row.message=businessMessage(e,'暂时无法查询公司商品目录。');
   }finally{
    if(signal===requestSignal){busy=false;paint()}
   }
   return;
  }
  paint();
 }
 function acceptFiles(added){
  const supplement=activityIntake()&&quickEdit&&ctx.route()==='submissions/'+quickEdit.submissionId;
  if(supplement&&(!canSupplement()||quickEdit.revision!==detail.revision))return;
  if(groupEdit?.kind==='supplement'){
   if(groupEdit.stale||unknownOutcome())return;
   captureGroupForm();
  }else if(supplement)captureSupplement(document.querySelector('#b-supplement-form'));
  else captureDraft(document.querySelector('#b-intake-form'));
  const queue=groupEdit?.kind==='supplement'?groupEdit.files:supplement?quickEdit.files:files;
  const groupFiles=groupEdit?.kind==='supplement'?groupEdit.files:null;
  const retained=groupFiles?[]:supplement?quickEdit.base.materialIds:existingIds;
  const pending=[];
  try{
   if(retained.length+queue.length+added.length>5)throw new Error('每次提报最多5件附件。');
   if(added.some(f=>!ALLOWED.test(f.name)))throw new Error('所选文件含不支持格式；请选择声明支持的活动材料。');
   if(added.some(f=>f.size===0||f.name.length>200))throw new Error('附件不能为空，文件名最多200字。');
   if(added.some(f=>f.size>5*1024*1024))throw new Error('单件附件不能超过5MiB。');
   if([...queue.map(f=>f.file),...added].reduce((n,f)=>n+f.size,0)>10*1024*1024)throw new Error('附件合计不能超过10MiB。');
   for(const file of added){
    const bytesPromise=snapshotFile(file);
    const item={file,bytes:null,bytesPromise};
    bytesPromise.then(bytes=>{item.bytes=bytes},err=>{
     if(!queue.includes(item))return;
     item.error=err;
     item.previewMessage=null;
     item.previewBlocked=false;
     if(alive())paint();
    });
    if(!groupFiles&&agentFirst()&&/\.xlsx$/i.test(file.name)){
     item.previewMessage='服务端正在预解析 Excel…';
     pending.push(item);
    }
    queue.push(item);
   }
   batchPreview=null;batchReceipt=null;error=null;
  }catch(e){error=e}
  paint();
  const operationSignal=signal;
  for(const item of pending)attachmentPreviewQueue=attachmentPreviewQueue.then(()=>previewExcel(item,queue,operationSignal));
 }
 let attachmentPreviewQueue=Promise.resolve();
 function readableFileError(error){
  if(error?.name==='NotReadableError'||/could not be read/i.test(error?.message??'')){
   return new Error('浏览器无法读取该本地文件（可能被 Excel/WPS 占用、尚未从微信/网盘下载到本地，或选择后文件已变动）。请先将文件保存到桌面或下载目录，关闭占用程序后重新选择。');
  }
  return error;
 }
 function snapshotFile(file){
  const promise=file.arrayBuffer().then(buf=>new Uint8Array(buf),error=>{throw readableFileError(error)});
  promise.catch(()=>{});
  return promise;
 }
 async function previewExcel(item,queue,operationSignal){
  if(operationSignal?.aborted||!queue.includes(item))return;
  try{
   const body={...await materialBody(item),checkOnly:true};
   const {data}=await ctx.adapter.request('/materials',{method:'POST',body,signal:operationSignal});
   item.previewMessage=data.ready?'服务端预解析通过。':data.message+' 可精简内容或分批提交所需明细。';
   item.previewBlocked=!data.ready;
  }catch(e){
   if(operationSignal?.aborted)return;
   const readable=readableFileError(e);
   if(readable!==e||item.error){
    item.error=item.error??readable;
    item.previewMessage=null;
    item.previewBlocked=false;
    if(operationSignal===signal&&queue.includes(item)&&alive())paint();
    return;
   }
   item.previewMessage=e.status===422?e.message:'服务端预检查暂不可用，仍可保存原件。';
   item.previewBlocked=e.status===422;
  }
  if(operationSignal!==signal||operationSignal?.aborted||!queue.includes(item)||!alive())return;
  const status=document.querySelector('[data-attachment-preview="'+queue.indexOf(item)+'"]');
  if(status){
   status.textContent=item.previewMessage;
   status.className=item.previewBlocked?'b-file-error':'hint';
  }
 }
 async function materialBody(target){
  const item=target&&typeof target==='object'&&'file' in target?target:null;
  const file=item?item.file:target;
  let bytes=item?.bytes??null;
  if(!bytes){
   try{
    bytes=await (item?.bytesPromise??snapshotFile(file));
   }catch(e){
    bytes=await snapshotFile(file);
   }
   if(item)item.bytes=bytes;
  }
  const sha256=Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256',bytes)),x=>x.toString(16).padStart(2,'0')).join('');
  let binary='';
  for(let i=0;i<bytes.length;i+=32768)binary+=String.fromCharCode(...bytes.subarray(i,i+32768));
  const mediaType={txt:'text/plain',md:'text/markdown',csv:'text/csv',tsv:'text/tab-separated-values',
   xlsx:'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',docx:'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
   png:'image/png',jpg:'image/jpeg',jpeg:'image/jpeg',webp:'image/webp',pdf:'application/pdf',doc:'application/msword',xls:'application/vnd.ms-excel'}
   [file.name.split('.').at(-1).toLowerCase()]??'application/octet-stream';
  return {filename:file.name,mediaType,contentBase64:btoa(binary),sha256};
 }
 async function upload(f){
  if(f.materialId)return f.materialId;
  f.uploading=true;f.error=null;paint();
  try{
   const body=await materialBody(f);
   const env=await ctx.commands.run('upload:'+body.sha256+':'+f.file.name,'materials.upload','POST','/materials',body,{signal});
   f.materialId=env.data.materialId;
   return f.materialId;
  }catch(e){const err=readableFileError(e);f.error=err;throw err}
  finally{f.uploading=false}
 }
 async function sendIntake(body,{supplement=false}={},operationSignal){
  const update=supplement||editing&&!parent;
  if(!supplement&&parent)body.parentSubmissionId=parent;
  const path=update?'/submissions/'+encodeURIComponent(detail.submissionId)+'/draft':'/submissions';
  if(update)body.expectedRevision=supplement?quickEdit.revision:detail.revision;
  const flowSlot=update?'draft:'+detail.submissionId+':'+body.expectedRevision:createSlot;
  ctx.commands.rememberIntake(flowSlot,{phase:'submitting',submissionSlot:flowSlot});
  const env=await ctx.commands.run(flowSlot,update?'submissions.update':'submissions.create',update?'PUT':'POST',path,body,{signal:operationSignal});
  const {submissionId,revision}=env.data;
  if(supplement&&('/submissions/'+encodeURIComponent(submissionId)+'/draft'!==path||revision!==body.expectedRevision+1)){
   throw new Error('服务回执与本次提报不一致，请刷新进度后核对。');
  }
  const analysisSlot='analysis:'+submissionId+':'+revision+':0';
  ctx.commands.rememberIntake(flowSlot,{phase:'received',submissionId,revision,submissionKey:ctx.commands.get(flowSlot)?.key,analysisSlot});
  if(operationSignal!==signal||!alive())return;
  if(supplement)quickEdit=null;
  else{
   // The accepted form can still be mounted when route rendering pauses the controller.
   const acceptedForm=document.querySelector('#b-intake-form');
   if(acceptedForm)acceptedDraftForms.add(acceptedForm);
   draft={form:{},originalDates:{},rawText:'',manualProductRows:[]};
   files=[];existingIds=[];parent=null;editing=false;createSlot='create:'+crypto.randomUUID();
  }
  let message=supplement?'补充已保存，正在读取新修订的分析进度。':'材料已受理，正在读取实际分析进度。';
  try{
   const analyzed=await ctx.commands.run(analysisSlot,'analysis.start','POST','/submissions/'+encodeURIComponent(submissionId)+'/analysis-runs',
    analysisBody(revision),{signal:operationSignal});
   ctx.commands.rememberIntake(flowSlot,{phase:'analyzing',runId:analyzed.data.runId,analysisKey:ctx.commands.get(analysisSlot)?.key});
  }catch(e){
   ctx.commands.rememberIntake(flowSlot,{phase:e.code==='COMMAND_UNKNOWN'?'analysis_unknown':'analysis_failed',analysisKey:ctx.commands.get(analysisSlot)?.key});
   if(ctx.authError?.(e))return;
   if(e.status===409&&e.code==='STATE_CONFLICT'){
    try{
     const latest=await ctx.adapter.request('/submissions/'+encodeURIComponent(submissionId),{signal:operationSignal});
     const active=latest.data.runs?.find(item=>item.inputRevision===revision&&ACTIVE.has(item.state));
     if(active)ctx.commands.rememberIntake(flowSlot,{phase:'analyzing',runId:active.runId});
    }catch(readError){if(ctx.authError?.(readError))return}
   }
   message=supplement?'补充已保存，分析未完成。可核对或重试这一修订的分析。':'原始材料已受理；分析尚未确认，请在提报记录核对或重试。';
  }
  if(operationSignal!==signal||!alive())return;
  ctx.notice(message);
  if(supplement){await refresh();return}
  ctx.navigate('#submissions/'+submissionId);
  await ctx.rerender();
 }
 async function recoverAnalysis(operationSignal){
  const flow=intakeRecovery();
  if(!flow)throw new Error('未找到本次首次分析的恢复记录，请重新读取提报状态。');
  ensureAgentAvailable();
  let result=null;
  const prior=ctx.commands.get(flow.analysisSlot);
  if(prior){
   result=(await ctx.commands.resolve(flow.analysisSlot,{retireMissing:agentFirst()&&prior.body?.agentConsent!==true})).result;
   if(!result&&prior.state==='unknown'&&agentFirst()&&prior.body?.agentConsent!==true)
    throw new Error('旧分析请求结果仍待核对，请先刷新回执，不会重复发送材料。');
  }
  if(!result)result=await ctx.commands.run(flow.analysisSlot,'analysis.start','POST','/submissions/'+flow.submissionId+'/analysis-runs',analysisBody(flow.revision,agentFirst()?run:null),{signal:operationSignal});
  ctx.commands.rememberIntake(flow.slot,{phase:'analyzing',runId:result.data.runId,analysisKey:ctx.commands.get(flow.analysisSlot)?.key});
  await refresh();
 }
 async function supplementBody(operationSignal){
  const {base,append,files}=quickEdit;
  const edits={...supplementDefaults(),...quickEdit.edits};
  const changes=declarations()?{...quickEdit.declarations}:{};
  if(changes.applicablePlatformCodes?.join(',')===applicablePlatformCodes(base).join(','))delete changes.applicablePlatformCodes;
  if(changes.productScopeIntent===(base.productScopeIntent??null))delete changes.productScopeIntent;
  if(!Object.keys(edits).length&&!append.trim()&&!files.length&&!Object.keys(changes).length)throw new Error('请至少补充一项信息或添加附件。');
  const rawText=append.trim()?[base.rawText,append].filter(Boolean).join('\n\n'):base.rawText;
  if(rawText.length>50000)throw new Error('补充后的原文不能超过 50000 字，请缩短补充说明。');
  if(base.materialIds.length+files.length>5)throw new Error('含原件在内最多保留 5 件附件。');
  const ids=[...base.materialIds];
  for(const file of files)ids.push(await upload(file));
  if(operationSignal!==signal||!alive())return null;
  return {form:{...base.form,...edits},rawText,materialIds:[...new Set(ids)],...changes};
 }
 return {
  ownsPending,
  async load(r,p,abortSignal){clearTimeout(timer);stopWaitTimer();model.reset();resetBrandPicker();if(quickEdit&&r!=='submissions/'+quickEdit.submissionId)quickEdit=null;signal=abortSignal;route=r;params=p;nameError=false;deleteTarget=null;if(r==='report'&&!ctx.session()?.permissions?.includes('submissions.create'))throw Object.assign(new Error('当前账号没有提报授权。'),{status:403});editing=false;error=null;confirmation=null;candidateId=null;autoStatus=null;autoCapability=null;autoBusy=false;groupReview={};groupEdit=null;supplementSummaries.clear();
   const capability=activityIntake()?ctx.adapter.request('/capabilities',{signal}):null;
   const [,goods,caps]=await Promise.all([readCatalog(signal),activityIntake()&&r==='report'?null:ctx.adapter.all('/products',{},signal),capability]);
   if(abortSignal!==signal||!alive())return;
   if(caps?.data?.agentFirstRecognition)agentCapability=caps.data.agentFirstRecognition;
   products=goods?.items??[];autoCapability=caps?.data?.autoResolution;groupReview=groupReviewCapabilities(caps?.data?.groupReview);
   declarationErrors={};dateError=false;
   if(r==='report'){detail=null;run=null;const pending=ctx.commands.pending().find(x=>x.action==='submissions.create');if(pending?.body){createSlot=pending.slot;draft={form:pending.body.form,declarations:pending.body,originalDates:{startAt:pending.body.form.startAt,endAt:pending.body.form.endAt},manualProductRows:pending.body.form.manualProductRows??[],rawText:pending.body.rawText??''};existingIds=pending.body.materialIds??[];parent=pending.body.parentSubmissionId??null}paint();return}
   const id=r.split('/')[1];if(id){if(!await readDetail(id,abortSignal))return;await followAnalysis();await readSupplementSummaries(abortSignal);if(abortSignal!==signal||!alive())return;
    if(ownSubmission()&&params.supplementVersion&&['productApplicability','storeBearing','subsidyPrices'].includes(params.field)){
     const saved=groupSaveCandidates().find(item=>item.versionId===params.supplementVersion);
     const summary=supplementSummaries.get(saved?.versionId);
     if(canOpenSupplement(saved)&&summary.requirements.some(item=>item.field===params.field)&&summary.pending.some(item=>item.field===params.field)){
      await openGroupSupplement(saved.candidateId,false);if(abortSignal===signal&&alive())schedule();return;
     }
    }
    if(abortSignal!==signal||!alive())return;paint();schedule()}else{detail=null;run=null;if(await readList(abortSignal))paint()}
  },
  input(target){
   if(nameError&&target.id==='b-activity-name'&&valuePresent(target.value)){
    nameError=false;
    target.removeAttribute('aria-invalid');
    target.removeAttribute('aria-describedby');
    document.querySelector('#b-activity-name-error')?.remove();
   }
   if(target.matches('[data-brand-filter]')){
    const picker=target.closest('[data-brand-picker]');let matches=0;
    for(const option of picker.querySelectorAll('[data-brand-option]')){option.hidden=!brandMatches(option.dataset.brandOption,target.value);if(!option.hidden)matches++;}
    picker.querySelector('[data-brand-no-match]').hidden=!target.value.trim()||matches>0;
    const hint=picker.querySelector('[data-brand-search-hint]');if(hint)hint.hidden=Boolean(target.value.trim());return;
   }
   if(target.matches('[data-brand-search]')){
    const query=target.value;
    if(activityIntake())brandSearch=query;
    const picker=target.closest('.design-brand-picker');
    let matches=0;
    for(const option of picker.querySelectorAll('[data-brand-option]')){
     option.hidden=!brandMatches(option.dataset.brandOption,query);
     if(!option.hidden)matches++;
    }
    const empty=picker.querySelector('[data-brand-no-match]');
    if(empty)empty.hidden=!query.trim()||matches>0;
    const hint=picker.querySelector('[data-brand-search-hint]');if(hint)hint.hidden=Boolean(query.trim());
   }else if(target.matches('[data-brand-name]')&&brandEditor&&!brandEditor.saving){
    brandEditor.name=target.value;
   }
  },
  async change(target){
   if(activityIntake()&&model.change(target))return true;
   if(target.form?.id==='b-confirm-form'&&['type','activityMethod','activityName','rulesText','intent','storeBearing','companyBearing'].includes(target.name)){
    const form=target.form;
    captureConfirm(form);
    if(target.name==='type'||(['activityMethod','intent'].includes(target.name)&&run.couponFundingQuestions?.[candidateId]?.length)){
     if(target.name==='type'||!brandSubsidyOnlyVisible(confirmation.fields,primaryCandidate(run,candidateId)))confirmation.fields.brandSubsidyOnly=false;
     paint();
     return true;
    }
    const details=form.querySelector('[data-store-bearing]');
    const datesRequired=confirmation.intent!=='link_source';
    const storeRequired=datesRequired&&(requiresStoreBearing(agentCandidateMetadata(run,candidateId)?.activityMethod?.value,
      primaryCandidate(run,candidateId)?.fields?.rulesText?.value)||requiresStoreBearing(confirmation.fields.activityMethod,confirmation.fields.rulesText,confirmation.fields.activityName)||
     confirmation.corrections.couponTiers.length>0);
    const corrections=form.querySelector('[data-manual-corrections]');
    if(corrections){corrections.disabled=!datesRequired;corrections.hidden=!datesRequired}
    if(details){
     details.hidden=!storeRequired;
     form.elements.storeBearing.disabled=!storeRequired;
    }
    const subsidy=form.querySelector('[data-brand-subsidy-only]');
    if(subsidy){
     subsidy.hidden=!datesRequired||!brandSubsidyOnlyVisible(confirmation.fields,primaryCandidate(run,candidateId));
     if(subsidy.hidden){form.elements.brandSubsidyOnly.checked=false;confirmation.fields.brandSubsidyOnly=false;}
    }
    for(const field of ['startAt','endAt']){
     const input=form.elements[field];
     input.required=datesRequired;
     const star=input.parentElement.querySelector('.b-required');
     if(star)star.hidden=!datesRequired;
     else if(datesRequired)input.insertAdjacentHTML('beforebegin',' <span class="b-required">*</span>');
    }
    return true;
   }
   if(target.matches('[data-brand-choice]')){
    const picker=target.closest('[data-brand-picker]');
    picker.querySelector('[data-brand-selection]').textContent=[...picker.querySelectorAll('[data-brand-option]')].filter(o=>o.querySelector('input').checked).map(o=>o.dataset.brandOption).join('、')||'未关联品牌，可后补';
    captureSupplement(document.querySelector('#b-supplement-form'));captureConfirm(document.querySelector('#b-confirm-form'));captureGroupForm();return true;
   }

   if(dateError&&target.form?.id==='b-intake-form'&&['startAt','endAt'].includes(target.name)){
    captureDraft(target.form);
    dateError=false;
    target.form.querySelector('#b-end-date')?.removeAttribute('aria-invalid');
    target.form.querySelector('#b-end-date')?.removeAttribute('aria-describedby');
    target.form.querySelector('#b-intake-date-error')?.remove();
   }
   if(target.name==='type'&&target.form?.id==='b-intake-form'){
    const required=target.value==='brand';
    const marker=target.form.querySelector('[data-platform-required]');
    const hint=target.form.querySelector('#b-applicable-platform-hint');
    if(marker)marker.hidden=!required;
    if(hint)hint.textContent=required?'可多选，品牌活动至少选一项。':'可多选。报名编号所属平台另填。';
   }
   if(target.name==='productScopeIntent'||target.name==='applicablePlatformCodes'){
    const field=target.name;
    const scopeHint=target.form?.querySelector('[data-product-scope-hint]');
    if(field==='productScopeIntent'&&scopeHint)scopeHint.textContent=productScopeHint(target.value);
    if(declarationErrors[field]){
     delete declarationErrors[field];
     const form=target.form;
     if(form?.id==='b-supplement-form')captureSupplement(form);
     else captureDraft(form);
     paint();
     if(form?.id!=='b-supplement-form')focusDeclaration(field);
    }
    return true;
   }
   if(target.name==='brandIds'&&target.type==='checkbox'){captureDraft(document.querySelector('#b-intake-form'));const picker=target.closest('.design-brand-picker');if(picker)picker.querySelector('[data-brand-selection]').textContent=catalog.brands.filter(b=>(draft.form.brandIds??[]).includes(b.id)).map(b=>b.name).join('、')||'未关联品牌，可后补';return true}if(!['b-material-files','b-group-files'].includes(target.id))return false;acceptFiles([...target.files]);return true},
  drop(fileList){if(!busy)acceptFiles([...fileList])},
  async click(target){
   const groupPrimary=activityIntake()&&target.closest('[data-sub-primary-group]');
   if(groupPrimary){
    if(agentFirst())return true;
    if(busy||groupPrimary.disabled)return true;
    captureSupplement(document.querySelector('#b-supplement-form'));captureConfirm(document.querySelector('#b-confirm-form'));
    await model.openGroup(groupPrimary.dataset.subPrimaryGroup);return true;
   }
   const modelButton=activityIntake()&&target.closest('[data-sub-model]');
   if(modelButton){
    if(agentFirst())return true;
    captureSupplement(document.querySelector('#b-supplement-form'));
    captureConfirm(document.querySelector('#b-confirm-form'));
    await model.click(modelButton);
    return true;
   }
   const action=target.closest('[data-sub-action]');const add=action?.dataset.subAction;const removeFile=target.closest('[data-sub-remove-file]'),removeProduct=target.closest('[data-sub-remove-product]'),candidate=target.closest('[data-candidate-select]');if(!add&&!removeFile&&!removeProduct&&!candidate)return false;if(busy||autoBusy||brandEditor?.saving)return true;
   if(['open-delete','cancel-delete','confirm-delete','check-delete','retry-delete','refresh-list'].includes(add)){
    return listAction(add,action.dataset);
   }
   captureDraft(document.querySelector('#b-intake-form'));
   captureSupplement(document.querySelector('#b-supplement-form'));
   captureConfirm(document.querySelector('#b-confirm-form'));
   captureGroupForm();
   if(activityIntake()&&detail&&unknownOutcome()&&!['refresh','check-receipt','retry-receipt'].includes(add))return true;
   if(add==='report-bad-case'){openBadCaseDialog(action);return true}
   if(['add-correction','remove-correction','find-correction-product'].includes(add)){
    await changeCorrection(add,action);return true
   }
   if(add==='inspect-shenjia-batch'){
    const requestSignal=signal;
    busy=true;error=null;paint();
    try{
     if(!await inspectShenjiaBatch(requestSignal))ctx.notice('这份表按单场活动提报。');
    }catch(e){if(!ctx.authError?.(e)&&requestSignal===signal&&!requestSignal.aborted)error=e}
    finally{busy=false;if(requestSignal===signal&&!requestSignal.aborted)paint()}
    return true;
   }
   if(add==='save-shenjia-batch'){await saveShenjiaBatch();return true}
   if(add==='confirm-direct'){await confirmDirect();return true}
   if(add==='manual-name-confirm'){await confirmManualName(action.dataset.manualCandidate);return true}
   if(add==='edit-submission-name'){
    const selected=run?.result?.candidates?.find(candidate=>candidate.candidateId===action.dataset.manualCandidate);
    if(!canManualName(selected))return true;
    if(!quickEdit)startSupplement();
    if(quickEdit){quickEdit.more=true;paint();const field=document.querySelector('#b-supplement-form [name="activityName"]');field?.focus();field?.scrollIntoView?.({block:'center'})}
    return true;
   }
   if(add==='supplement'){startSupplement();return true}
   if(add==='open-group-supplement'||add==='reload-group-supplement'){
    await openGroupSupplement(action.dataset.groupCandidate,add==='reload-group-supplement');return true;
   }
   if(add==='open-grouping'){openGrouping(action.dataset.groupingCase);return true}
   if(add==='retry-grouping'){await retryGrouping();return true}
   if(add==='close-group-review'){
    const staleGrouping=groupEdit?.kind==='grouping'&&groupEdit.stale;
    closeGroupEditor();
    if(staleGrouping)await refresh();
    return true;
   }
   if(add==='cancel-supplement'){quickEdit=null;error=null;nameError=false;paint();document.querySelector('[data-sub-action="supplement"]')?.focus();return true}
   if(add==='rebase-supplement'){rebaseSupplement();return true}
   if(add==='open-brand'){
    if(canCreateBrand()&&!catalogError&&!brandEditor&&!catalog.brands.some(brand=>brandMatches(brand.name,brandSearch))){
     brandEditor={name:brandSearch.trim(),error:'',saving:false};
     brandNotice='';
     paint();document.querySelector('[data-brand-name]')?.focus();
    }
    return true;
   }
   if(add==='cancel-brand'){
    brandEditor=null;
    paint();document.querySelector('[data-sub-action="open-brand"]')?.focus();
    return true;
   }
   if(add==='save-brand'){await saveBrand();return true}
   if(removeFile){batchPreview=null;batchReceipt=null;(groupEdit?.files??quickEdit?.files??files).splice(Number(removeFile.dataset.subRemoveFile),1);paint();return true}if(removeProduct){draft.manualProductRows=draft.manualProductRows.filter(x=>x.rowId!==removeProduct.dataset.subRemoveProduct);paint();return true}if(candidate){
    if(activityIntake()&&(confirmation||groupEdit||quickEdit&&multipleCandidates()&&!agentFirst()))return true;
    const selected=run?.result?.candidates.find(item=>item.candidateId===candidate.dataset.candidateSelect);
    if(activityIntake()?canCheckGroup(selected):canConfirmManually())startConfirm(candidate.dataset.candidateSelect);
    return true
   }
   if(add==='add-product'){if(draft.manualProductRows.length<1000)draft.manualProductRows.push({rowId:'manual-'+crypto.randomUUID()});paint();return true}if(add==='cancel-confirm'){
    const selectedId=candidateId;
    confirmation=null;candidateId=null;error=null;nameError=false;paint();
    if(quickEdit)document.querySelector('#b-supplement-form h3')?.focus();
    else [...(document.querySelectorAll?.('[data-candidate-select]')??[])].find(button=>button.dataset.candidateSelect===selectedId)?.focus();
    return true
   }if(add==='cancel-edit'){editing=false;if(detail){paint()}else ctx.navigate('#activity');return true}if(add==='edit'){nameError=false;if(activityIntake()&&(!hasDecision()||!canEditInput()))return true;resetBrandPicker();parent=activityIntake()||detail.activityId||detail.resolutions.length?detail.submissionId:null;editing=true;draft={form:structuredClone(detail.currentInput.form),declarations:detail.currentInput,originalDates:{startAt:detail.currentInput.form.startAt,endAt:detail.currentInput.form.endAt},manualProductRows:structuredClone(detail.currentInput.form.manualProductRows??[]),rawText:detail.currentInput.rawText??''};existingIds=[...(detail.currentInput.materialIds??[])];files=[];createSlot='create:'+crypto.randomUUID();error=null;paint();return true}
   if(add==='target'){await lookupTarget();return true}
   if(activityIntake()&&detail){
   if(add==='analyze'&&(!canEditInput()||hasDecision()||hasGroupFailures()||intakeRecovery()||groupingFailure()))return true;
    if(agentFirst()&&add==='analyze'&&manualDirty()){error=new Error('你有尚未保存的补充内容，请先保存并交给 Agent 重新识别。');paint();return true}
    if(add==='recover-analysis'&&(!intakeRecovery()||analyzing()||groupingFailure()))return true;
    if(add==='cancel-analysis'&&(!['queued','running'].includes(run?.state)||!actionAllowed(detail,'submissions.edit_own')))return true;
   }
   const operationSignal=signal;busy=true;error=null;paint();try{if(['check-receipt','retry-receipt'].includes(add))await checkReceipt(add==='retry-receipt',operationSignal);if(add==='retry-auto')await followAnalysis(true);if(add==='retry-catalog')await readCatalog(operationSignal);if(add==='recover-analysis')await recoverAnalysis(operationSignal);if(add==='refresh')await refresh();if(add==='analyze'){
    const submissionId=detail.submissionId,revision=detail.revision;
    const slot='analysis:'+submissionId+':'+revision+':'+(detail.runs?.length??0);
    ensureAgentAvailable();
    const body=analysisBody(revision,(agentFirst()||groupedAuto())?run:null);
    const env=await ctx.commands.run(slot,'analysis.start','POST','/submissions/'+submissionId+'/analysis-runs',body,{signal:operationSignal});
    if(operationSignal!==signal||operationSignal.aborted)return true;
    if(activityIntake())ctx.commands.rememberIntake(slot,{phase:'analyzing',submissionId,revision,runId:env.data.runId});
    await refresh();
   }if(add==='cancel-analysis'){await ctx.commands.run('cancel:'+run.runId,'analysis.cancel','POST','/analysis-runs/'+run.runId+'/cancel',{}, {signal:operationSignal});if(operationSignal!==signal||operationSignal.aborted)return true;await refresh()}}catch(e){if(ctx.authError?.(e))return true;if(operationSignal!==signal||operationSignal.aborted)return true;error=e}finally{busy=false;if(operationSignal===signal)paint()}return true;
  },
  async submit(form){
   if(['b-group-supplement-form','b-grouping-form'].includes(form.id)){
    await saveGroupEditor(form);
    return true;
   }
   if(!['b-intake-form','b-supplement-form','b-confirm-form'].includes(form.id))return false;
   const supplement=form.id==='b-supplement-form';
   const confirm=form.id==='b-confirm-form';
   if(busy||autoBusy||(!supplement&&!confirm&&brandEditor)||confirm&&!canConfirmManually())return true;
   if(confirm&&!confirmation)return true;
   if(activityIntake()&&detail){
    if(unknownOutcome()||acceptedAhead())return true;
    if(!supplement&&!confirm&&route!=='report'&&(!editing||!hasDecision()||!canEditInput()))return true;
   }
   if(supplement&&(!quickEdit||!canSupplement()||quickEdit.conflict||quickEdit.revision!==detail.revision))return true;
   captureDraft(!supplement&&!confirm?form:null);
   captureSupplement(supplement?form:null);
   if(confirm){
    captureConfirm(form);
    if(activityIntake()&&!valuePresent(confirmation.fields.activityName)){
     error=new Error('请输入活动名称。');
     paint();focusRequiredName(true);return true;
    }
   }
   if(!supplement&&!confirm&&batchEligible()&&batchPreview?.status==='ready')return true;
   if(!supplement&&!confirm&&batchEligible()&&batchPreview?.status!=='single_or_unsupported'){
    const requestSignal=signal;
    busy=true;error=null;paint();
    try{
     if(await inspectShenjiaBatch(requestSignal))return true;
     batchPreview={status:'single_or_unsupported'};
    }catch(e){
     if(!ctx.authError?.(e)&&requestSignal===signal&&!requestSignal.aborted)error=e;
     return true;
    }finally{busy=false;if(requestSignal===signal&&!requestSignal.aborted)paint()}
    if(requestSignal!==signal||requestSignal.aborted)return true;
   }
   if(activityIntake()&&!confirm){
    const fields=supplement?supplementValues():draft.form;
    nameError=!valuePresent(fields.activityName);
    if(nameError){error=null;paint();focusRequiredName();return true}
   }
   declarationErrors={};
   if(activityIntake()&&!supplement&&!confirm){
    const start=activityDate(draft.form.startAt),end=activityDate(draft.form.endAt);
    dateError=Boolean(start&&end&&end<start);
    if(dateError){error=null;paint();document.querySelector('[name="endAt"]')?.focus();return true}
   }
   if(declarations()&&!supplement&&!confirm&&draft.form.type==='brand'&&!draft.declarations?.applicablePlatformCodes?.length){
    declarationErrors.applicablePlatformCodes='请至少选择一个适用平台';
    error=null;paint();focusDeclaration('applicablePlatformCodes');return true;
   }
   const operationSignal=signal;
   busy=true;error=null;paint();
   try{
    if(!confirm)ensureAgentAvailable();
    if(supplement){
     const body=await supplementBody(operationSignal);
     if(body)await sendIntake(body,{supplement:true},operationSignal);
    }else if(!confirm){
     const ids=[...existingIds];
     for(const file of files)ids.push(await upload(file));
     const declared=draft.declarations;
     const body={form:{...draft.form,manualProductRows:draft.manualProductRows},rawText:draft.rawText,materialIds:[...new Set(ids)]};
     if(declarations()){
      if(declared?.applicablePlatformCodes?.length||draft.form.type==='brand')body.applicablePlatformCodes=declared?.applicablePlatformCodes??[];
      if(declared?.productScopeIntent)body.productScopeIntent=declared.productScopeIntent;
     }
     await sendIntake(body,{},operationSignal);
    }
    else await submitConfirmation(candidateId,confirmation,operationSignal);
   }catch(e){if(ctx.authError?.(e))return true;if(operationSignal!==signal||operationSignal.aborted)return true;
    if(confirm&&confirmation)receiveFundingQuestions(e,confirmation);
    nameError=activityIntake()&&e.status===422&&e.code==='NAME_REQUIRED'&&e.details?.field==='activityName';
    const field=e.details?.field;
    if(declarations()&&e.status===422&&['applicablePlatformCodes','productScopeIntent'].includes(field)){
     const text=field==='productScopeIntent'?'请选择有效的商品适用范围。':
      e.code==='PLATFORM_REQUIRED'?'请至少选择一个适用平台':'请重新核对适用平台选项。';
     if(confirm)error=new Error(text.replace(/。$/,'')+'。'+(canSupplement()?'请回到补充信息更正后，再核对本组。':'本修订当前不可补充，请核对已有处理记录。'));
     else{declarationErrors[field]=text;error=null}
    }else if(confirm&&declarations()&&e.status===422&&e.code==='PLATFORM_IDENTITY_REQUIRED'){
     error=new Error('请为报名编号选择实际所属的平台。');
    }else error=nameError&&!confirm?null:e;
    if(supplement&&quickEdit&&e.status===409&&e.code==='STATE_CONFLICT'){
     quickEdit.conflict=true;
     try{await readDetail(detail.submissionId,operationSignal)}
     catch(readError){if(ctx.authError?.(readError))return true}
    }
    if(e.code==='DUPLICATE_ACTIVITY'&&confirmation&&e.details?.activityId){confirmation.targetActivityId=e.details.activityId;confirmation.expectedActivityRevision=e.details.revision??null}}finally{busy=false;if(operationSignal===signal){paint();if(nameError)focusRequiredName(confirm);else if(Object.keys(declarationErrors).length)focusDeclaration(Object.keys(declarationErrors)[0]);else if(confirm&&error?.message==='请为报名编号选择实际所属的平台。')focusDeclaration('platformCode')}}return true;
  },
  pause(){clearTimeout(timer);stopWaitTimer();model.pause();captureDraft(document.querySelector('#b-intake-form'));captureSupplement(document.querySelector('#b-supplement-form'));captureConfirm(document.querySelector('#b-confirm-form'));captureGroupForm()},clear(){clearTimeout(timer);stopWaitTimer();model.reset();resetBrandPicker();batchPreview=null;batchReceipt=null;quickEdit=null;deleteTarget=null;nameError=false;declarationErrors={};dateError=false;groupReview={};groupEdit=null;supplementSummaries.clear();catalogError=null;agentCapability=null;autoCapability=null;autoStatus=null;autoBusy=false;draft={form:{},originalDates:{},rawText:'',manualProductRows:[]};files=[];existingIds=[];parent=null;editing=false;detail=null;run=null;list=null;catalog={brands:[],platforms:[]};products=[];confirmation=null;targetContext=null;error=null;busy=false;createSlot='create:'+crypto.randomUUID()}
 }
}
