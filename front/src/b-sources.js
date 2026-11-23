import {h,panel,rows,table,badge,beijing,ROLE_LABELS,FIELD_LABELS,businessMessage,evidenceList,diagnostics} from './b-common.js';
import {factsHtml,fieldValueText,materialHtml,declarationRecordHtml} from './b-fields.js';
import {APPLICABLE_PLATFORM_LABELS,applicablePlatformCodes,hasSubmissionDeclarations} from './dto.js';

export function ownSubmissionLink(version,submissionId,session){
 const source=version.sourceInputs?.find(s=>s.submissionId===submissionId);
 const owned=source?.submitter?.id!=null&&source.submitter.id===session?.user?.id&&session?.permissions?.includes('submissions.read_own');
 return owned?`<a href="#submissions/${h(submissionId)}">查看原提报</a>`:'原提报记录';
}
export function originalSubmitterNames(version){
 const people=new Map();
 for(const source of version?.sourceInputs??[]){
  const person=source.submitter;
  if(person?.id&&person.snapshotStatus==='recorded'&&typeof person.nameSnapshot==='string'&&person.nameSnapshot.trim()){
   people.set(person.id,person.nameSnapshot);
  }
 }
 return [...people.values()];
}
export function originalSubmissionSummaryHtml(version){
 const inputs=version.sourceInputs??[];
 if(!inputs.length)return '<p class="muted">当前版本的原提交信息暂不可用。</p>';
 return inputs.map(source=>{
  const codes=applicablePlatformCodes(source);
  const platforms=codes.map(code=>APPLICABLE_PLATFORM_LABELS[code]);
  const title=[source.form?.activityName,...platforms].filter(Boolean).join(' · ');
  const person=source.submitter?.snapshotStatus==='recorded'?source.submitter.nameSnapshot:null;
  const attachmentIds=new Set(source.attachmentMaterialIds??[]);
  const uploads=(source.materials??[]).filter(file=>attachmentIds.has(file.materialId)&&file.available!==false);
  const missing=uploads.length<attachmentIds.size;
  return `<article class="b-source-input">
   <h3>${h(title||'原活动名称待核实')}</h3>
   <p>提交人 ${h(person??'未记录')} · 提交时间（北京时间） ${h(beijing(source.submittedAt)??'未记录')}</p>
   ${uploads.length?materialHtml(uploads,{compact:true}):'<p class="muted">当前没有可查看的上传附件。</p>'}
   ${missing?'<p class="muted">部分上传附件暂不可用，详情见完整提交记录。</p>':''}
  </article>`;
 }).join('');
}
export function dateEvidenceHtml(record,materials=[]){
 const group=(label,evidence)=>{
  const refs=evidence?.evidenceRefs??[];
  const yearConfirmations=refs.filter(ref=>ref.yearBasis?.kind==='user_confirmation').map(ref=>ref.yearBasis);
  const confirmed=[...new Map(yearConfirmations.map(item=>[item.confirmedDay,item])).values()];
  const context=confirmed.length?`<p>年份经用户确认：${confirmed.map(item=>h(item.confirmedDay)).join('、')}</p>`:'';
  return refs.length?`<h3>${h(label)}</h3>${context}${evidenceList(refs,materials)}`:'';
 };
 const content=group('活动日期出处',record.activityDateEvidence)+group('材料报名时间出处',record.registrationDateEvidence);
 return content?`<details class="date-source"><summary>查看活动和报名日期出处</summary>${content}</details>`:'';
}
export function sourceMissingHtml(items=[]){return items.length?`<ul class="b-business-list">${items.map(x=>`<li>${h(businessMessage(x))}</li>`).join('')}</ul>`:''}
function changesHtml(changes=[],catalog){return table(['字段','当时识别值','本次人工确认值','服务留存的变更'],changes.map(c=>rows([h(FIELD_LABELS[c.field]??'活动字段'),h(c.extractedPresent?fieldValueText(c.field,c.extractedValue,catalog):'没有记录原始识别值'),h(fieldValueText(c.field,c.confirmedValue,catalog)),c.changed===true?'人工修改':c.changed===false?'保持原值':'变更情况未记录'])),'本次确认差异未记录。')}
export function versionSourcesHtml(version,session,catalog={}){
 const availability=version.sourceAvailability;
 const label={available:'本版来源可追溯',partial:'部分来源资料不可用',unavailable:'来源资料不可用'}[availability?.status]??'来源可用性尚未提供';
 const evidence=version.evidence??[];
 return panel('本版绑定的原始输入与处理记录',`<p>${badge(label,availability?.status==='available'?'blue':'amber')}</p><p class="hint">只展示本版已绑定的原始修订与已确认分组。原文可能包含其他活动说明，不代表其他段落已作为本版条件确认。</p>${sourceMissingHtml(availability?.missing)}${(version.sourceInputs??[]).map(source=>{
  const person=source.submitter;
  const recorded=person?.snapshotStatus==='recorded';
  return `<article class="b-source-input"><h3>提报 ${ownSubmissionLink(version,source.submissionId,session)} · 原始输入修订 ${h(source.inputRevision)}</h3><p>提交人：${h(recorded?person.nameSnapshot??'当时姓名未记录':'当时提交人信息未记录')} · 当时角色：${h(recorded?ROLE_LABELS[person.roleSnapshot]??'当时角色未记录':'未记录')}<small>提交时间 ${h(beijing(source.submittedAt)??'未记录')}</small></p>${sourceMissingHtml(source.missing)}<h3>原始填报表单</h3>${source.form===null||source.form===undefined?'<p class="b-note">原始表单不可用或未获读取授权，未以空表单替代。</p>':Object.keys(source.form).length?factsHtml(source.form,catalog):'<p class="muted">本次原始表单未填写字段。</p>'}${hasSubmissionDeclarations(source)?declarationRecordHtml(source):''}<h3>原始文字</h3>${source.rawText===null||source.rawText===undefined?'<p class="b-note">原始文字不可用或未获读取授权，未以空串替代。</p>':source.rawText===''?'<p class="muted">本次确未提供原始文字。</p>':`<pre class="b-json">${h(source.rawText)}</pre>`}<h3>本修订绑定的原件</h3>${materialHtml(source.materials)}${(source.analyses??[]).map(analysis=>{
   const refs=new Set(analysis.evidenceReferenceIds??[]);
   const bound=evidence.filter(e=>refs.has(e.referenceId));
   const resolution=analysis.resolution;
   if(resolution?.mode==='automatic'){
    return `<details><summary>查看系统自动处理分组 ${h(analysis.candidateId)}</summary><p>规则版本 ${h(resolution.ruleVersion??'未记录')} · 决议材料 ${h(resolution.materialId??'不可用')}</p>${resolution.resolvedFields?factsHtml(resolution.resolvedFields,catalog):'<p class="b-note">本次系统决议字段不可用。</p>'}<h3>本次实际引用的原文</h3>${evidenceList(bound,source.materials)}</details>`;
   }
   return `<details><summary>查看本版已确认分组 ${h(analysis.candidateId)} 的原始识别与确认差异</summary><p>本次使用本地规则解析，${analysis.modelUsed===false?'未调用外部模型':'模型来源待核实'}。确认材料ID：${h(analysis.confirmation?.materialId??'不可用')}。</p>${analysis.confirmation?changesHtml(analysis.confirmation.changes,catalog):'<p class="b-note">本次确认原件不可用，未从当前活动值补造历史确认。</p>'}<h3>本次实际引用的原文</h3>${evidenceList(bound,source.materials)}${diagnostics({runId:analysis.runId,candidateId:analysis.candidateId,inputRevision:analysis.inputRevision,inputDigest:analysis.inputDigest,analysisDigest:analysis.analysisDigest,engine:analysis.engine,engineVersion:analysis.engineVersion,evidenceReferenceIds:analysis.evidenceReferenceIds},'查看该已确认分组的核验信息')}</details>`;
  }).join('')||'<p class="muted">未提供可核验的本版分析绑定，不选取其他分析或最新草稿补齐。</p>'}${diagnostics({submissionId:source.submissionId,inputRevision:source.inputRevision,inputDigest:source.inputDigest},'查看原始修订核验信息')}</article>`;
 }).join('')||'<p class="muted">当前版本没有可用的绑定原始输入。旧版记录不补造历史姓名、原文或引文。</p>'}`);
}
