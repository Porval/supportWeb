// UI-only normalized values. No HTTP or browser business-data persistence.
export const STATES = Object.freeze({review:'待评审 / 排队中', doing:'开发中', done:'已上线'});
export const PRIORITIES = Object.freeze({P0:'P0 紧急', P1:'P1 重要', P2:'P2 常规'});
export function escapeHtml(value) {
  return String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
}
export function businessDate(date = new Date()) {
  const parts = new Intl.DateTimeFormat('en-CA', {timeZone:'Asia/Shanghai',year:'numeric',month:'2-digit',day:'2-digit'}).formatToParts(date);
  return ['year','month','day'].map(k => parts.find(p => p.type === k).value).join('-');
}
export function dayNumber(value) {
  if (typeof value !== 'string' || !/^\d{4}-\d{2}-\d{2}$/.test(value)) return null;
  const date = new Date(value + 'T00:00:00Z');
  return Number.isFinite(+date) && date.toISOString().slice(0,10) === value ? +date / 86400000 : null;
}
export function weekRange(today) {
  const day = dayNumber(today);
  if (day === null) throw new Error('业务日期无效');
  const monday = day - ((new Date(day * 86400000).getUTCDay() + 6) % 7);
  const format = n => new Date(n * 86400000).toISOString().slice(0,10);
  return {start:format(monday), end:format(monday + 6)};
}
export function dueLabel(due, today) {
  const day = dayNumber(due), base = dayNumber(today);
  if (day === null || base === null) return {text:'未排期', hot:false};
  const delta = day - base;
  return {text:delta < 0 ? `已逾期 ${-delta} 天` : delta === 0 ? '今天到期' : `${due.slice(5).replace('-','/')} ${delta <= 3 ? '临近' : '到期'}`, hot:delta <= 3};
}
export function ownerOptions(requirements) {
  return [...new Set(requirements.map(r => r.owner?.trim()).filter(Boolean))];
}
export function filtered(requirements, filters = {}) {
  return requirements.filter(r => (!filters.owner || r.owner?.trim() === filters.owner) && (!filters.priority || r.priority === filters.priority));
}
export function doingByDue(requirements) {
  // Stable ties retain authoritative board sequence. No in-place mutation.
  return requirements.filter(r => r.status === 'doing').map((r,i) => ({r,i})).sort((a,b) =>
    (dayNumber(a.r.due) ?? Infinity) - (dayNumber(b.r.due) ?? Infinity) || a.i - b.i).map(x => x.r);
}
export function summarize(requirements, today, {duePolicy = null} = {}) {
  const week = weekRange(today), day = dayNumber(today);
  const counts = Object.fromEntries(Object.keys(STATES).map(s => [s, requirements.filter(r => r.status === s).length]));
  const completed = requirements.filter(r => r.status === 'done' && dayNumber(r.doneAt) !== null && r.doneAt >= week.start && r.doneAt <= week.end);
  // Pending user decision is explicit; no silent default to either metric meaning.
  const policyReady = ['include_done','exclude_done'].includes(duePolicy);
  const scoped = policyReady ? requirements.filter(r => duePolicy === 'include_done' || r.status !== 'done') : [];
  const overdue = scoped.filter(r => dayNumber(r.due) !== null && dayNumber(r.due) < day);
  const upcoming = scoped.filter(r => dayNumber(r.due) !== null && dayNumber(r.due) >= day && dayNumber(r.due) <= day + 6);
  return {total:requirements.length, counts, completed, week, p0Doing:requirements.filter(r => r.status === 'doing' && r.priority === 'P0').length,
    dueCount:policyReady ? overdue.length + upcoming.length : null, overdue:policyReady ? overdue : null, upcoming:policyReady ? upcoming : null,
    dueExplanation:policyReady ? `未来7天（含今天）及逾期；${duePolicy === 'exclude_done' ? '不含已上线' : '包含已上线'}` : '到期与逾期统计口径待确认'};
}
export function newDraft() {
  return {title:'', priority:'P1', owner:'', start:'', due:'', status:'review', progress:0, note:''};
}
export function draftOf(record) {
  return Object.fromEntries(Object.keys(newDraft()).map(key => [key, record[key] ?? (key === 'progress' ? null : '')]));
}
export function validateDraft(draft) {
  const errors = {};
  if (!draft.title?.trim()) errors.title = '请输入需求名称。';
  if(draft.title?.trim().length>200)errors.title='需求名称最多200字。';
  if(draft.owner?.length>100)errors.owner='负责人最多100字。';
  if(draft.note?.length>10000)errors.note='背景说明最多10000字。';
  if (!Object.hasOwn(PRIORITIES, draft.priority)) errors.priority = '请选择有效优先级。';
  if (!Object.hasOwn(STATES, draft.status)) errors.status = '请选择有效状态。';
  for (const key of ['start','due']) if (draft[key] && dayNumber(draft[key]) === null) errors[key] = '请输入有效日期。';
  if (draft.start && draft.due && !errors.start && !errors.due && draft.start > draft.due) errors.due = '截止日期不能早于开始日期。';
  if (draft.progress !== null && (!Number.isFinite(Number(draft.progress)) || Number(draft.progress) < 0 || Number(draft.progress) > 100 || !Number.isInteger(Number(draft.progress)))) errors.progress = '进度应为0–100。';
  return errors;
}
export function moveIntent(id, status, targetId = null, placement = 'end') {
  if (!Object.hasOwn(STATES,status) || !['before','after','end'].includes(placement) || id === targetId) return null;
  // Relative stable ID, never the filtered list or a replacement column array.
  return {id,status,targetId,placement};
}
export function normalizeOutcome(value) {
  if (['saved','conflict','unknown','error','forbidden','unauthenticated'].includes(value?.status)) return value;
  return {status:'unknown', message:'尚不能确认保存结果，请核对本次操作；未自动重发。'};
}

// Trusted comment identity is a separate negotiated sub-capability. A legacy
// signature never becomes a name snapshot merely because the viewer is known.
export const COMMENT_IDENTITY_VERSION='comment-identity/2.0';
const identityError=()=>new Error('评论身份资料不符合接口契约，请重新读取或联系维护人员。');
const nonempty=v=>typeof v==='string'&&v.trim().length>0&&v===v.trim();
const exactObject=(value,keys)=>value&&typeof value==='object'&&!Array.isArray(value)&&Object.keys(value).sort().join('|')===[...keys].sort().join('|');
export function normalizeCommentCapability(context,principalKey) {
  const c=context?.commentIdentity;
  const unavailable=reason=>({negotiated:false,enabled:false,currentAuthor:null,reason});
  if(!c||c.contractVersion!==COMMENT_IDENTITY_VERSION)return unavailable('评论身份服务待升级，暂不能发送；未提交草稿已保留。');
  if(!exactObject(c,['contractVersion','enabled','writeMode','currentAuthor'])||typeof c.enabled!=='boolean'||c.writeMode!==(c.enabled?'session_account':'unavailable'))throw identityError();
  const a=c.currentAuthor;
  if(!exactObject(a,['accountId','name','accountLabel','profileVersion','displayName','displaySource'])||!nonempty(a.accountId)||a.accountId!==principalKey||!(a.name===null||nonempty(a.name))||!nonempty(a.accountLabel)||!Number.isInteger(a.profileVersion)||a.profileVersion<1||a.displayName!==(a.name??a.accountLabel)||a.displaySource!==(a.name===null?'account_label':'name'))throw identityError();
  return {negotiated:true,enabled:c.enabled,currentAuthor:structuredClone(a),reason:c.enabled?'':'评论身份服务尚未完成升级，暂不能发送；未提交草稿已保留。'};
}
export function normalizeCommentRecord(comment,{identityV2=false,historicalReceipt=false}={}) {
  if(!comment||typeof comment.author!=='string')throw identityError();
  let a=comment.authorIdentity;
  if(a===undefined) {
    if(identityV2&&!historicalReceipt)throw identityError();
    a={contractVersion:COMMENT_IDENTITY_VERSION,kind:'legacy_signature',accountId:null,nameSnapshot:null,accountLabelSnapshot:null,profileVersionSnapshot:null,displayName:comment.author,displaySource:'legacy_signature'};
  }
  if(!exactObject(a,['contractVersion','kind','accountId','nameSnapshot','accountLabelSnapshot','profileVersionSnapshot','displayName','displaySource'])||a.contractVersion!==COMMENT_IDENTITY_VERSION||typeof a.displayName!=='string'||a.displayName!==comment.author)throw identityError();
  if(a.kind==='account') {
    if(!nonempty(a.accountId)||!(a.nameSnapshot===null||nonempty(a.nameSnapshot))||!nonempty(a.accountLabelSnapshot)||!Number.isInteger(a.profileVersionSnapshot)||a.profileVersionSnapshot<1||a.displayName!==(a.nameSnapshot??a.accountLabelSnapshot)||a.displaySource!==(a.nameSnapshot===null?'account_label_snapshot':'name_snapshot'))throw identityError();
  } else if(a.kind==='legacy_signature'||a.kind==='example') {
    if(a.nameSnapshot!==null||a.accountLabelSnapshot!==null||a.profileVersionSnapshot!==null)throw identityError();
    if(a.kind==='legacy_signature'&&(!(a.accountId===null||nonempty(a.accountId))||a.displaySource!=='legacy_signature'))throw identityError();
    if(a.kind==='example'&&(a.accountId!==null||!nonempty(a.displayName)||a.displaySource!=='example_label'))throw identityError();
  } else throw identityError();
  return {...comment,authorIdentity:structuredClone(a)};
}
export function commentAuthorPresentation(comment) {
  const a=comment.authorIdentity;
  if(a?.kind==='account')return {name:a.displayName,label:'',account:a.accountLabelSnapshot};
  if(a?.kind==='example')return {name:a.displayName,label:'示例评论',account:null};
  return {name:a?.displayName||comment.author||'匿名',label:'历史署名',account:null};
}
