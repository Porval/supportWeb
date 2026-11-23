"""Expose the existing read-only catalog operation as a DeepSeek tool."""

from collections import Counter
from product_paging import PAGE_PROPERTIES, validate_page
import re

from chat_product_lookup import COLUMN_NUMBER, MAX_MATCH_ROWS, lookup_catalog
from chat_product_search import MAX_QUERY, normalized_name, search_product_names

ACCESS_QUESTION = re.compile(
    r'(?:请问)?(?:你|活动agent)?(?:现在)?(?:不是)?(?:可以|能(?:不能)?|可不可以|是否能|有权|有没有权限|有权限|'
    r'有(?:没有)?|接了|接入了|接通了)(?:直接|主动)?(?:访问|调用|查询|使用|读取|接入|对接到|对接)?(?:公司的)?(?:数据agent|数据智能体)'
    r'(?:的)?(?:接口|访问权限|权限)?(?:吗|吧|么|呢)?')
PRODUCT_TOOL_NAME = 'lookup_product_codes'
PRODUCT_TOOL = {
    'type':'function',
    'function':{
        'name':PRODUCT_TOOL_NAME,
        'description':(
            '在活动 Agent 本机按 UPC、内部码或附件商品行查询数据 Agent 同步的公司商品目录。'
            '可传 offset/limit 分页查询完整本地商品行；空 upcs 使用原材料。返回本页全部明细、总数和 nextOffset，有更多可连续翻页。'
            '上文已有附件且用户问公司商品货号、系统编码或商品归属时，可以使用本工具。'
        ),
        'parameters':{
            'type':'object',
            'properties':{
                **PAGE_PROPERTIES,
                'internalCodes':{'type':'array','items':{'type':'string'},'description':'公司内部商品码，可与 UPC 分别查询。'},
                'upcs':{
                    'type':'array',
                    'description':'要查询的 UPC，空数组可查询当前或上轮已解析的商品行。',
                    'items':{'type':'string','pattern':'^[0-9]{8,14}$'},
                },
            },
            'required':[],
            'additionalProperties':False,
        },
    },
}


NAME_TOOL_NAME = 'search_product_names'
NAME_TOOL = {
    'type':'function',
    'function':{
        'name':NAME_TOOL_NAME,
        'description':('通过用户给出的品牌、品名和规格搜索数据 Agent 商品主档，返回待确认候选。'
                       '缺 UPC 也可以查询；对话里的「去查下」可沿用前文给出的商品名称与规格。'),
        'parameters':{
            'type':'object',
            'properties':{'query':{'type':'string','description':'根据对话、附件或先前结果组织商品名、品牌和规格，可拆词或缩短重查。'}},
            'required':['query'],
            'additionalProperties':False,
        },
    },
}


def is_data_agent_access_question(text):
    return ACCESS_QUESTION.fullmatch(text) is not None


def describe_access(cache, master=None):
    available = lookup_catalog(cache, [])
    if available['status'] != 'available':
        if master and master.is_file():
            return '可以按品名、品牌和规格查数据 Agent 的商品主档。当前 UPC 目录暂不可读。'
        return '活动 Agent 当前无法读取数据 Agent 的商品目录。'
    return (
        '可以。活动 Agent 可以读取数据 Agent 同步的公司商品目录，和工作台使用同一份目录和 UPC 匹配规则。'
        '可以按 UPC 查内部码，按已有内部码核对商品，也可以按品牌、品名和规格查找候选。'
        f"当前目录快照日期为 {available['sourceDate']}。"
    )


def read_tool_arguments(tool_call, expected_name):
    from business_agent.workbench_deepseek import adapter

    if not isinstance(tool_call, dict) or tool_call.get('type') != 'function':
        raise ValueError('Invalid tool call')
    call_id = tool_call.get('id')
    function = tool_call.get('function')
    if (not isinstance(call_id, str) or not re.fullmatch(r'[\w-]{1,120}', call_id)
            or not isinstance(function, dict) or function.get('name') != expected_name):
        raise ValueError('Unknown tool call')
    raw = function.get('arguments')
    if not isinstance(raw, str) or len(raw) > 4096:
        raise ValueError('Invalid product lookup arguments')
    try:
        return call_id, adapter.decode(raw)
    except adapter.AdapterError:
        raise ValueError('Invalid tool arguments JSON') from None


def tool_summary(call_id, summary):
    from business_agent.workbench_deepseek import adapter

    return {'role':'tool','tool_call_id':call_id,'content':adapter.encode(summary).decode('utf-8')}


def run_product_tool(tool_call, rows, cache, master=None):
    call_id, args = read_tool_arguments(tool_call, PRODUCT_TOOL_NAME)
    if not isinstance(args,dict) or set(args) - {'upcs','internalCodes','offset','limit'}:
        raise ValueError('Invalid product lookup arguments')
    upcs, codes = args.get('upcs',[]), args.get('internalCodes',[])
    if (not isinstance(upcs,list) or not isinstance(codes,list)
            or len(upcs) + len(codes) > MAX_MATCH_ROWS
            or any(not isinstance(upc,str) or not COLUMN_NUMBER.fullmatch(upc) for upc in upcs)
            or any(not isinstance(code,str) or not re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9_-]{2,79}',code) for code in codes)):
        raise ValueError('Invalid product identifiers')
    selected = list(rows) if not upcs and not codes else [row for row in rows if row.get('upc') in upcs]
    known = {row.get('upc') for row in selected}
    selected += [{'upc':upc,'upcIsText':True} for upc in dict.fromkeys(upcs) if upc not in known]
    selected += [{'upc':None,'upcIsText':False,'rawInternalCode':code,'codeIsText':True}
                 for code in dict.fromkeys(codes)]
    offset,limit = validate_page(args.get('offset',0),args.get('limit',MAX_MATCH_ROWS))
    result = lookup_catalog(cache,selected,master,offset=offset,limit=limit)
    summary = {'status':result['status'],'lookedUpRows':len(result['rows']),
               'matchedRows':dict(Counter(row['status'] for row in result['rows'])),
               'rows':result['rows'],'page':result['page'],'moreRows':result['page']['hasMore']}
    if result['status']=='available':
        summary.update(status='queried' if selected else 'needs_identifiers',sourceDate=result['sourceDate'])

    return result, tool_summary(call_id,summary)


def run_name_tool(tool_call, messages, master, cache):
    call_id,args = read_tool_arguments(tool_call, NAME_TOOL_NAME)
    query = args.get('query') if isinstance(args,dict) and set(args)=={'query'} else None
    if not isinstance(query,str) or len(query)>MAX_QUERY:
        raise ValueError('Invalid product name lookup arguments')
    if len(normalized_name(query)) < 2:
        raise ValueError('Product query must contain at least two characters')
    result = search_product_names(master,cache,query)
    summary = {'status':result['status'],'matchCount':result['totalCandidates'],
               'query':query,'candidates':result['candidates']}
    return result,tool_summary(call_id,summary)
