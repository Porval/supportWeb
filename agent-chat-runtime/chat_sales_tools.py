"""Expose the installed Activity Agent's read-only sales worker to chat tools."""

from datetime import date, datetime
from pathlib import Path
import json
import re
import subprocess
import sys
from zoneinfo import ZoneInfo

from chat_data_tools import read_tool_arguments, tool_summary

SALES_TOOL_NAME = 'query_product_sales'
CONTRACT = 'activity-sales-worker/1.0'
HISTORY_VERSION = 'activity-sales-history/1.0'
MAX_INPUT_BYTES = 256 * 1024
SALES_TOOL = {
    'type': 'function',
    'function': {
        'name': SALES_TOOL_NAME,
        'description': (
            '用当前活动 Agent 的线上数据 Agent 只读接口查询商品销量。'
            '按已知内部码、已核对商品或名称查询候选的内部码发起查询，并明确所用内部码。'
            '支持近 28 天、连续两个 28 天对比和最近三个完整自然月。'
            '按用户当前任务确定平台及全平台或指定门店范围。'
            '工具返回来源、覆盖和结果状态，逐商品结果由本机附在回答后。'
        ),
        'parameters': {
            'type': 'object',
            'properties': {
                'platformCode': {'type': 'string', 'enum': ['meituan', 'taobao_instant', 'jd'],
                                 'description': '美团为 meituan，淘宝闪购为 taobao_instant，京东为 jd。'},
                'internalCodes': {'type': 'array', 'minItems': 1, 'maxItems': 5000,
                                  'items': {'type': 'string'},
                                  'description': '公司商品内部码，可从本次或先前商品目录查询结果取得。'},
                'window': {'type': 'string', 'enum': [
                    'trailing_28_days', 'trailing_28_days_compare', 'last_3_complete_months'],
                    'description': '查询窗口按服务端北京时间计算，近 28 天截止昨天。'},
                'allPlatformStores': {'type': 'boolean',
                                      'description': '查询该平台全部门店时为 true；指定门店时为 false。'},
                'platformStoreIds': {'type': 'array', 'maxItems': 5000,
                                     'items': {'type': 'string'},
                                     'description': '指定门店的平台门店 ID；全平台时传空数组。'},
            },
            'required': ['platformCode', 'internalCodes', 'window', 'allPlatformStores'],
            'additionalProperties': False,
        },
    },
}
PLATFORM_LABELS = {'meituan': '美团', 'taobao_instant': '淘宝闪购', 'jd': '京东'}
PLATFORM_TERMS = {'meituan': r'美团|meituan', 'taobao_instant': r'淘宝闪购|淘宝|taobao_instant',
                  'jd': r'京东秒送|京东|(?<![a-z])jd(?![a-z])'}
SOURCE_TOOL_NAME = 'check_product_sales_source'
SOURCE_TOOL = {'type':'function','function':{'name':SOURCE_TOOL_NAME,'description':'检查指定平台是否接入已授权销量来源。',
    'parameters':{'type':'object','properties':{'platformCode':SALES_TOOL['function']['parameters']['properties']['platformCode']},
                  'required':['platformCode'],'additionalProperties':False}}}
METRIC = re.compile(r'销量|销售(?:数量|件数|量|数(?!据))|售出(?:量|件数)|卖出量|卖了|售出了|卖(?:了|出)?(?:多少|几[单件盒瓶支份])')
QUERY = re.compile(r'查|看看?|统计|拉取|给我|报(?:一下|出)|列出|获取|导出|对比|多少|几件|分别|最高|最低|排名|最多|最少|怎么样|如何|有吗|能否|能不能|能查|支持|为零|是零')
TIME_SCOPE = re.compile(r'本月|上月|上个月|这个月|本周|上周|昨天|昨日|今天|今日|最近|近.{0,5}[日天周月]|[0-9一二三四五六七八九十]+月|三(?:个|家)?平台|各平台|全平台')
DISCUSSION = re.compile(r'如何(?:提高|提升|增长|分析|计算|估算|预测)|(?:销量|销售量)(?:的)?(?:定义|口径|指标|策略|目标|预测|计算公式|字段|考核|提升方法|分析方法)|'
                        r'(?:如何|怎么|怎样)(?:.{0,5})(?:提升|提高|增长|计算|估算|预测)|(?:提高|提升|拉动|促进|预估|预测).{0,15}(?:销量|销售量)|'
                        r'(?:如何|怎么|怎样)(?:查|看|统计|分析)|(?:销量|销售量).{0,6}(?:怎么分析|计算口径|是什么|是什么意思|受哪些影响|要不要填|原因)|(?:方案|策略|机制|设计|培训|教程|示例|假设|假如|方法|复盘模板)|(?:销量|销售量).{0,3}怎么算')
FOLLOWUP = re.compile(r'(?:那|那么|再|还|换成|看看|查查|查|比较|对比|帮我查|查一下|还有|只看|改成|其他|另外|另|这|上|下|前|后|同|以及|和|、|的|平台|也|呢|吗|怎么样|如何|分别|另外两|剩下两|都|所有|全|各|三|两|个|家|[？?，,。!！\s])+')

WINDOW_LABELS = {'trailing_28_days': '近 28 天', 'trailing_28_days_compare': '连续两个 28 天',
                 'last_3_complete_months': '最近三个完整自然月'}


def sales_platforms(text):
    return [code for code,pattern in PLATFORM_TERMS.items() if re.search(pattern,text,re.I)]


def requested_sales(messages):
    """Resolve explicit quantity lookups and short follow-ups, ending the thread on a topic change."""
    prior = []
    for entry in messages:
        if entry['role'] != 'user':
            continue
        text = entry['content'].strip()
        named = sales_platforms(text)
        amount_only = (re.search(r'销售额|[Gg][Mm][Vv]|销售金额|营业额|营收|多少(?:钱|元|块)|几[块元]',text)
                       and not re.search(r'销量|销售(?:数量|量|件数)|(?:几|多少)[件盒瓶支份]',text))
        unknown_platform = not named and re.search(r'天猫|饿了么|抖音|拼多多',text)
        if DISCUSSION.search(text) or amount_only or unknown_platform:
            prior = []
            continue
        if METRIC.search(text) and (QUERY.search(text) or TIME_SCOPE.search(text) or named
                                   or re.search(r'卖[了出]?(?:多少|几[件盒瓶支份])',text)):
            prior = named or (list(PLATFORM_LABELS) if re.search(r'所有|三(?:个|家)?平台|各平台|全平台',text) else prior or list(PLATFORM_LABELS))
            continue
        remaining = text
        for pattern in PLATFORM_TERMS.values():
            remaining = re.sub(pattern,'',remaining,flags=re.I)
        remaining = TIME_SCOPE.sub('',remaining)
        if prior and len(text) <= 32 and (not remaining or FOLLOWUP.fullmatch(remaining)):
            if named:
                prior = named
            elif re.search(r'其他|另外|剩下',text):
                prior = [code for code in PLATFORM_LABELS if code not in prior] or list(PLATFORM_LABELS)
            elif re.search(r'所有|三个|全平台|各平台',text):
                prior = list(PLATFORM_LABELS)
        else:
            prior = []
    return prior


def requested_amount(messages):
    """The sales history tool returns quantities. Keep monetary queries on their own metric."""
    text = messages[-1]["content"].strip()
    match = re.search(r"销售额|销售金额|营业额|成交额|交易额|GMV|卖了多少钱|卖出多少元",text,re.I)
    if (not match or re.search(r"定义|口径|公式|假设|举例|如何(?:提升|计算|提高)|怎么(?:算|提高|提升)", text)
        or not (QUERY.search(text) or TIME_SCOPE.search(text) or sales_platforms(text))):
        return []
    metric = match.group().upper() if match.group().lower()=="gmv" else match.group()
    if metric in ("卖了多少钱","卖出多少元"):
        metric = "销售额"
    return [{"platformCode":code, "metric":metric, "status":"source_unavailable",
             "code":"SALES_AMOUNT_SOURCE_UNAVAILABLE",
             "message":f"{PLATFORM_LABELS[code]}的{metric}查询尚未接入当前对话。"}
            for code in sales_platforms(text) or PLATFORM_LABELS]


def sales_source(platform, settings_path, release):
    if platform not in PLATFORM_LABELS:
        raise ValueError('unknown sales platform')
    configured = platform == 'meituan' and bool(settings_path and release)
    code = None if configured else ({'jd':'JD_SALES_SOURCE_NOT_CONNECTED','taobao_instant':'TAOBAO_SALES_SOURCE_NOT_CONNECTED'}.get(platform,'SALES_SOURCE_NOT_AUTHORIZED'))
    message = (f'{PLATFORM_LABELS[platform]}已配置销量来源，实际销量须核对查询回执。' if configured else
               f'{PLATFORM_LABELS[platform]}销量数据未接入。' if platform != 'meituan' else '美团销量数据源尚未授权接入。')
    return {'platformCode':platform, 'status':'configured' if configured else 'source_unavailable',
            'code':code, 'message':message}


def source_checks(platforms, settings_path, release):
    return [sales_source(platform, settings_path, release) for platform in platforms]


def sales_request(args):
    """Validate chat parameters against the existing sales request contract."""
    def identifiers(items):
        if (not isinstance(items,list) or len(items) > 5000 or
            any(not isinstance(item,str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}',item) for item in items)):
            raise ValueError('Invalid sales scope')
        return sorted(set(items))

    required = {'platformCode', 'internalCodes', 'window', 'allPlatformStores'}
    if (not isinstance(args, dict) or not required <= args.keys()
            or args.keys() - required - {'platformStoreIds'}
            or not isinstance(args['platformCode'],str) or args['platformCode'] not in PLATFORM_LABELS
            or not isinstance(args['window'],str) or args['window'] not in WINDOW_LABELS or type(args['allPlatformStores']) is not bool):
        raise ValueError('Invalid sales tool arguments')
    codes = identifiers(args['internalCodes'])
    stores = identifiers(args.get('platformStoreIds', []))
    if not codes or bool(stores) == args['allPlatformStores']:
        raise ValueError('Invalid product or store scope')
    return {'schemaVersion': HISTORY_VERSION, 'platformCode': args['platformCode'], 'window': args['window'],
            'scope': {'internalCodes': codes, 'platformStoreIds': stores,
                      'allPlatformStores': args['allPlatformStores']}}


def sales_worker_root(release):
    base = Path(release)
    if base.name == 'agent-service':
        base = base.parent / 'runtime'
    root = base / 'released'
    worker = root / 'business_agent/activity_sales_comparison/worker.py'
    if not worker.is_file() or worker.is_symlink() or worker.resolve() != worker.absolute():
        raise ValueError('Sales runtime missing')
    return root


def _invoke_worker(payload, settings_path, release, timeout):

    raw = json.dumps(payload, ensure_ascii=False, separators=(',', ':')).encode('utf-8')
    if len(raw) > MAX_INPUT_BYTES:
        raise ValueError('Sales query exceeds the installed worker input limit')
    root = sales_worker_root(release)
    env = {'JIATAN_ACTIVITY_SALES_SETTINGS':str(settings_path)}
    if sys.platform.startswith('linux'):
        env['SSL_CERT_FILE'] = '/etc/pki/tls/certs/ca-bundle.crt'
    # Use the exact same CA and packaged Asia/Shanghai bootstrap as workbench sales.
    launcher = root.parents[1] / 'hosted_business.py'
    proc = subprocess.run(
        [sys.executable, '-I', '-B', str(launcher)],
        input=raw, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=timeout,
        cwd=root / 'business_agent/activity_sales_comparison', env=env,
    )
    if proc.returncode or not 0 < len(proc.stdout) <= 16 * 1024 * 1024:
        raise ValueError('Invalid sales worker output')
    wire = json.loads(proc.stdout)
    if (not isinstance(wire,dict) or wire.get('contractVersion') != CONTRACT
            or wire.get('ok') is not True or not isinstance(wire.get('result'),dict)):
        raise ValueError('Invalid sales worker reply')
    return wire['result']


def _failed(request, code, message):
    return {key:request[key] for key in ('schemaVersion','platformCode','window')} | {
        'status':'source_unavailable', 'periods':[], 'rows':[], 'source':None,
        'warnings':[{'code':code,'message':message}]}


def query_sales(args, settings_path, release, *, today=None, timeout=65, worker=None):
    """Reuse the installed worker with a time limit and request-bound receipt."""
    state = sales_source(args.get('platformCode') if isinstance(args,dict) else None, settings_path, release)
    if state['status'] != 'configured':
        return {'schemaVersion':'activity-sales-history/1.0','platformCode':state['platformCode'],
                'window':args.get('window'), 'status':'source_unavailable','periods':[], 'rows':[], 'source':None,
                'requestedScope':{'internalCodes':[], 'allPlatformStores':False, 'platformStoreIds':[]},
                'warnings':[{'code':state['code'],'message':state['message']}]}
    request = sales_request(args)
    current_day = today if today is not None else datetime.now(ZoneInfo('Asia/Shanghai')).date()
    if type(current_day) is not date or type(timeout) not in (int,float) or not 0 < timeout <= 65:
        raise ValueError('Invalid server-side sales date or timeout')
    payload = {'contractVersion':CONTRACT, 'operation':'history',
               'request':request, 'today':current_day.isoformat()}
    try:
        result = (worker or _invoke_worker)(payload, settings_path, release, timeout)
        if (any(result.get(key) != request[key] for key in ('schemaVersion','platformCode','window'))
                or not isinstance(result.get('periods'),list) or not isinstance(result.get('rows'),list)
                or not isinstance(result.get('warnings'),list) or not isinstance(result.get('status'),str)):
            raise ValueError('Invalid sales result identity')
        if result['rows']:
            source = result.get('source') or {}
            if result['status'] not in ('ready','partial') or not source.get('queryRef') or not source.get('updatedAt'):
                raise ValueError('Sales rows require a bound successful source receipt')
        received_codes = [row['internalCode'] for row in result['rows']]
        if len(set(received_codes)) != len(received_codes) or set(received_codes) - set(request['scope']['internalCodes']):
            raise ValueError('Invalid sales result scope')
    except subprocess.TimeoutExpired:
        result = _failed(request,'ACTIVITY_AGENT_TIMEOUT','本地活动 Agent 查询超时。')
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        result = _failed(request,'ACTIVITY_AGENT_UNAVAILABLE','本地活动 Agent 暂时无法返回可核对的查询结果。')
    return result | {'requestedScope':request['scope'], 'queriedOn':current_day.isoformat()}


def run_sales_tool(tool_call, settings_path, release, *, today=None, timeout=65, worker=None):
    call_id, args = read_tool_arguments(tool_call, SALES_TOOL_NAME)
    result = query_sales(args, settings_path, release, today=today, timeout=timeout, worker=worker)
    source = result.get('source') or {}
    summary = {
        'status': result['status'], 'platformCode': result['platformCode'], 'window': result['window'],
        'productCount': len(result['requestedScope']['internalCodes']),
        'returnedProducts': len(result['rows']), 'periods': result['periods'],
        'sourceUpdatedAt': source.get('updatedAt'), 'queryRef': source.get('queryRef'),
        'warnings': result['warnings'],
        'rows':result['rows'][:25], 'moreRows':len(result['rows'])>25,
        'localResultsAppended': True,
    }
    return result, tool_summary(call_id, summary)


def describe_sales_result(result):
    platform = PLATFORM_LABELS.get(result['platformCode'], result['platformCode'])
    if result['status']=='source_unavailable':
        return ' '.join(warning['message'] for warning in result['warnings'])
    scope = result['requestedScope']
    scope_label = '平台全部门店' if scope['allPlatformStores'] else '门店 ' + '、'.join(scope['platformStoreIds'])
    lines = [f"{platform} · {WINDOW_LABELS[result['window']]} · {scope_label}。"]
    periods = {period['key']: period for period in result['periods']}
    for period in periods.values():
        lines.append(f"{period['startDate']} 至 {period['endDate']}。")
    for row in result['rows']:
        lines.append('• ' + ' · '.join(value for value in (row.get('name'), row['internalCode']) if value))
        for total in row['periods']:
            period = periods[total['key']]
            label = f"{period['startDate']} 至 {period['endDate']}"
            if total['status'] == 'ready':
                lines.append(f"  {label} · 销量 {total['salesQty']} 件。")
            else:
                coverage = total.get('coverage') or {}
                observed = total.get('recordedSalesQty')
                recorded = f'已记录 {observed} 件' if observed is not None else '暂无有效销量记录'
                lines.append(f"  {label} · {recorded}，完整销量待确认"
                             f"（有效记录 {coverage.get('validRecords', 0)}/{coverage.get('expectedRecords', 0)}）。")
        comparison = row.get('comparison')
        if comparison and comparison['status'] == 'ready':
            lines.append(f"  比上个 28 天变化 {comparison['delta']} 件，变化率 {comparison['changePct']}%。")
    source = result.get('source') or {}
    if source.get('updatedAt'):
        lines.append(f"来源更新时间 {source['updatedAt']}。")
    if source.get('queryRef'):
        lines.append(f"数据 Agent 查询回执 {source['queryRef']}。")
    for warning in result['warnings']:
        lines.append(warning['message'])
    if not result['rows']:
        lines.append('本次查询内部码 ' + '、'.join(scope['internalCodes']) + '。')
    return '\n'.join(lines)
