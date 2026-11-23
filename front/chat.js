const chatPath = '/api/activity/v1/agent/chat';
const status = document.querySelector('#status');
const list = document.querySelector('#messages');
const form = document.querySelector('#composer');
const input = document.querySelector('#message');
const send = document.querySelector('#send');
let csrf, revision = 0, configured = false, busy = false, retry = null;

function notice(message, error = false) {
  status.textContent = message;
  status.dataset.error = String(error);
}
function controls() {
  input.disabled = !csrf || !configured || busy;
  send.disabled = input.disabled || !input.value.trim();
  send.textContent = busy ? '等待回复…' : '发送';
}
const sourceNames = new Map(Object.entries({
  deepseek: 'DeepSeek 模型', case_library: '已确认案例库', intake_contract: '活动识别规则',
  runtime_config: 'Agent 运行配置', shared_intake: '活动识别服务', worksheet_cells: '原表单元格',
  attachment_receipt: '附件读取回执', attachment_column: '附件列信息', product_catalog: '商品目录',
  product_name_search: '商品名称检索', product_page: '商品分页', product_selection: '商品筛选',
  product_count: '商品统计', workbench_submission: '工作台提报', sales_source: '销量数据源回执'
}));
const toolStates = new Map(Object.entries({
  success: '成功', completed: '完成', available: '可用', ok: '成功', unavailable: '不可用',
  failed: '失败', forbidden: '未授权', unauthorized: '未授权', source_unavailable: '数据源未接入',
  no_data: '无数据', invalid_arguments: '参数无效'
}));

async function request(path, options = {}) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), options.method === 'POST' ? 150000 : 15000);
  try {
    const response = await fetch(path, {
      credentials:'same-origin', cache:'no-store', ...options, signal:controller.signal
    });
    const body = await response.json();
    if (!response.ok || body.error) {
      const error = Error(body.error?.message || '暂时无法读取服务响应。');
      error.code = body.error?.code;
      error.status = response.status;
      throw error;
    }
    return body.data;
  } catch (error) {
    if (controller.signal.aborted) throw Error('等待服务响应超时。');
    throw error;
  } finally {
    clearTimeout(timer);
  }
}

function renderReceipt(line, receipt) {
  const provenance = document.createElement('small');
  provenance.className = 'provenance';
  line.append(provenance);
  if (!receipt) {
    provenance.textContent = '历史回复 · 来源未记录';
    return;
  }
  const source = sourceNames.get(receipt.source) || receipt.source || '来源未记录';
  const model = receipt.model;
  const fromConfig = receipt.source === 'runtime_config' || receipt.source === 'case_library';
  const modelType = fromConfig ? '配置模型' : '模型';
  provenance.textContent = model ? [source, modelType + ' ' + model].join(' · ') : source;

  const details = document.createElement('details');
  const summary = document.createElement('summary');
  summary.textContent = '查看回执';
  const fields = document.createElement('dl');
  function add(label, value) {
    if (typeof value !== 'string' || !value) return;
    const key = document.createElement('dt');
    const body = document.createElement('dd');
    key.textContent = label;
    body.textContent = value;
    fields.append(key, body);
  }
  add('请求编号', receipt.requestId);
  add('提示词版本', receipt.promptVersion);
  if (Array.isArray(receipt.caseReferences)) {
    for (const item of receipt.caseReferences) {
      if (typeof item?.key === 'string') {
        add('引用案例', item.revision == null ? item.key : item.key + ' · 修订 ' + item.revision);
      }
    }
  }
  const tools = new Set();
  function addTool(tool) {
    if (typeof tool?.name !== 'string') return;
    const result = tool.result && typeof tool.result === 'object' ? tool.result : tool;
    const state = toolStates.get(result.status) || result.status;
    const reason = result.code || result.reasonCode;
    const label = result.platformLabel || result.platformName || result.platformCode;
    const warnings = Array.isArray(result.warnings) ? result.warnings.map(w => w?.message) : [];
    const parts = [tool.name, label, state, reason, result.message, ...warnings];
    add('工具回执', [...new Set(parts.filter(v => typeof v === 'string' && v))].join(' · '));
    tools.add(tool.name);
  }
  const detailedCounts = new Map();
  if (Array.isArray(receipt.toolReceipts)) {
    for (const tool of receipt.toolReceipts) {
      if (typeof tool?.name !== 'string') continue;
      addTool(tool);
      detailedCounts.set(tool.name, (detailedCounts.get(tool.name) || 0) + 1);
    }
  }
  if (Array.isArray(receipt.toolResults)) {
    for (const tool of receipt.toolResults) {
      const count = detailedCounts.get(tool?.name) || 0;
      if (count) detailedCounts.set(tool.name, count - 1);
      else addTool(tool);
    }
  }
  if (Array.isArray(receipt.toolsUsed)) {
    for (const name of receipt.toolsUsed) {
      if (typeof name === 'string' && !tools.has(name)) add('调用工具', name);
    }
  }
  if (fields.childElementCount) {
    details.append(summary, fields);
    line.append(details);
  }
}

function render(data) {
  if (!Number.isInteger(data.revision) || !Array.isArray(data.messages)) {
    throw Error('对话记录格式异常，请刷新后重试。');
  }
  revision = data.revision;
  configured = data.configured;
  const messages = document.createDocumentFragment();
  for (const item of data.messages) {
    const line = document.createElement('article');
    const speaker = document.createElement('strong');
    const text = document.createElement('p');
    line.className = item.role === 'user' ? 'user' : 'assistant';
    speaker.textContent = item.role === 'user' ? '你' : '活动 Agent';
    text.textContent = item.content;
    line.append(speaker, text);
    if (item.role === 'assistant') renderReceipt(line, item.receipt);
    messages.append(line);
  }
  list.replaceChildren(messages);
  if (!configured) notice('活动 Agent 的管理员对话接口尚未连接。', true);
  else if (data.historyTruncated) notice('显示最近 40 轮对话。');
  else if (!data.messages.length) notice('可以开始第一条对话。');
  else notice('');
  controls();
}
async function refresh() {
  const session = await request('/api/activity/v1/account/session');
  if (!session?.authenticated || !session.permissions?.includes('accounts.manage') ||
      session.capabilities?.fullAccessAdministrator !== true || session.capabilities?.activityAgentChat === false) {
    throw Error('请返回工作台，使用已获直聊授权的管理员账号登录。');
  }
  csrf = session.csrfToken;
  render(await request(chatPath));
}
function setError(error) {
  if (error.status === 401 || error.status === 403) csrf = null;
  notice(error.message || '连接失败，请核对工作台会话后重试。', true);
}
form.addEventListener('submit', async event => {
  event.preventDefault();
  if (send.disabled) return;
  const message = input.value.trim();
  if (!retry || retry.message !== message || retry.expectedRevision !== revision) {
    retry = {commandId:crypto.randomUUID(), expectedRevision:revision, message};
  }
  busy = true;
  controls();
  notice('活动 Agent 正在处理消息…');
  const waiting = setTimeout(() => notice('活动 Agent 仍在处理，模型与工具调用可能需要约两分钟。'), 25000);
  try {
    const data = await request(chatPath, {method:'POST', headers:{
      'Content-Type':'application/json', 'X-CSRF-Token':csrf
    }, body:JSON.stringify(retry)});
    retry = null;
    input.value = '';
    render(data);
    list.lastElementChild?.scrollIntoView({block:'nearest'});
  } catch (error) {
    if (error.code === 'CHAT_REVISION_CONFLICT') {
      try { await refresh(); } catch (refreshError) { setError(refreshError); return; }
    }
    if (error.code && error.code !== 'CHAT_BUSY') retry = null;
    if (!error.code && !error.status) notice('连接中断或等待超时。可重发同一条消息核对结果。', true);
    else setError(error);
  } finally {
    clearTimeout(waiting);
    busy = false;
    controls();
  }
});
input.addEventListener('input', controls);
input.addEventListener('keydown', event => {
  if (event.key === 'Enter' && (event.ctrlKey || event.metaKey) && !event.isComposing) {
    event.preventDefault();
    if (!send.disabled) form.requestSubmit();
  }
});
try { await refresh(); } catch (error) { setError(error); controls(); }
