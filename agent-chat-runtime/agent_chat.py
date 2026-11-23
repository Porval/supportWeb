"""Local conversation endpoint for the Activity Agent."""

import argparse
from functools import lru_cache, partial
from http.client import HTTPConnection, HTTPException
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import re
from pathlib import Path
from threading import BoundedSemaphore
from time import monotonic, time
from urllib.error import HTTPError, URLError
from urllib.request import Request
from urllib.parse import urlsplit, parse_qs
from uuid import uuid4

from business_relations import business_policy
from chat_business_tools import BusinessTools
from conversation_store import ConversationStore, ConversationUnavailable, ConversationConflict, ConversationNotFound
from conversation_files import AttachmentStore
from conversation_runtime import run_persistent_chat, recover_record, view as conversation_view
from product_paging import page_intent
from case_revisions import CASE_CORRECTION_QUESTION, CaseRevisionError, may_revise, save_revision
from chat_attachments import (AttachmentError, MAX_BASE64_BYTES, asks_attachment_receipt,
                              attachment_options, describe_attachment_receipt, prepare_attachments, read_uploads)
from chat_data_tools import (NAME_TOOL, NAME_TOOL_NAME, PRODUCT_TOOL, PRODUCT_TOOL_NAME, describe_access,
                             is_data_agent_access_question, run_name_tool, run_product_tool)
from chat_intake_contract import PLATFORM_QUESTION, describe_platforms, describe_intake_contract, is_field_question, load_intake_contract
from chat_product_lookup import (asks_for_draft, describe_product_lookup, is_product_question, lookup_catalog,
                                 lookup_recognized_products, query_rows, typed_internal_codes, typed_upcs)
from chat_product_search import contextual_name_query, describe_product_search, search_product_names
from chat_workbench_records import WorkbenchReadError, load_submission, requested_submission
from chat_sheet_cells import cell_request, read_sheet_cells
from chat_shared_intake import (IntakePreviewError, describe_preview, intake_request, preview_periods, requests_recognition,
                                run_preview, use_release_validators)
from memory import CaseMemory, ContextUnavailable
from product_receipts import (RECEIPT_ID, ProductReceipts, column_intent, count_intent,
                              describe_column, describe_counts, describe_selection, product_snapshot,
                              select_products, selection_intent)
from service import imports

CONTRACT = 'activity-agent-chat/1.0'
PROMPT_VERSION = 'activity-agent-chat-prompt/1.17'
PREFIX = '/api/test/v1/activity-agent'
BACKEND_PORT = 52670
MAX_BODY = 36 * 1024
MAX_ATTACHMENT_BODY = MAX_BODY + MAX_BASE64_BYTES + 16
MAX_MESSAGE_CHARS = 3000
MAX_CONTEXT_CHARS = 7200
RAW_TOOL_MARKUP = re.compile(r'<\s*/?\s*[|｜│︱\s]*DSML\b',re.IGNORECASE)
LOCAL_METADATA = {'modelLabel':None, 'modelNameSource':None, 'modelVersion':None, 'modelVersionSource':None}
MODEL_QUESTION = re.compile(
    r'^(?:请问)?(?:你|活动agent|这个agent)?(?:现在|当前|底层)?'
    r'(?:用的|使用的|用|使用|调用的|调用|接入的|接的|的)?'
    r'(?:是)?(?:什么|哪个|哪一款|哪种)?(?:大)?模型(?:的)?(?:版本|名称|名)?'
    r'(?:是|叫)?(?:什么|哪个|哪一个|哪一款|啥|几点几|几\.几|第几版|哪一版)?(?:呢|啊|呀|吗)?$')
PROMPT_VERSION_QUESTION = re.compile(
    r'(?:请问)?(?:(?:当前|这个)?(?:对话)?提示词(?:的)?(?:版本(?:号)?|是几点几)|'
    r'(?:什么|哪个|哪一版)(?:对话)?提示词)(?:是?(?:多少|什么|几点几))?(?:呢|啊|呀|吗)?')
GENERAL_VERSION_QUESTION = re.compile(
    r'(?:请问)?(?:你是|你用的是|当前)?(?:什么版本|哪个版本|版本(?:号)?(?:是(?:多少|什么))?)(?:呢|啊|呀|吗)?')
VERSION_FOLLOWUP = re.compile(r'(?:是)?(?:几点几|几\.几|第几版|哪一版)(?:呢|啊|呀|吗)?')
CASE_LIBRARY_OVERVIEW = re.compile(
    r'(?:你|活动agent)?(?:本地|现在)?(?:还有|有(?:没有)?|是否有|还记得|能查到)'
    r'(?:之前|以前|先前|历史)?(?:(?:训练|学习|学|确认|保存|识别|沉淀)(?:过)?的?)?'
    r'(?:训练)?(?:已确认案例|案例库|案例|信息|资料|记录|内容)(?:吗|么|呢)?')
CASE_LIBRARY_COUNT = re.compile(
    r'(?:你|活动agent)?(?:本地|现在)?(?:已经|之前|一共|共|总共)?'
    r'(?:有|保存了|录入了)?(?:多少|几)(?:个|条)?(?:已确认|历史|训练|识别)?'
    r'(?:的)?案例(?:了吗|了|吗|呢)?')
CASE_LIBRARY_LIST = re.compile(
    r'(?:请)?(?:帮我|给我)?(?:列出|列一下|看一下|看看|查看|有哪些)?'
    r'(?:本地|已确认|之前|以前|历史|训练过的)+案例(?:有哪些|清单|列表|吗|呢)?')
CASE_LIBRARY_LIST_LIMIT = 12
MODEL_CATALOG_URL = 'https://api.deepseek.com/models'
MODEL_CATALOG_TTL_SECONDS = 300
MODEL_LABELS = {'deepseek-flash':'DeepSeek Flash','deepseek-chat':'DeepSeek Chat',
                'deepseek-reasoner':'DeepSeek Reasoner'}
# The catalog reports the model family. Public release notes supply the announced build.
MODEL_RELEASES = {
    ('deepseek-flash', 'DeepSeek-V4.1-Flash'): (
        'V4.1-Flash-1017', 'https://api-docs.deepseek.com/zh-cn/news/news261017/'),
}
UI = Path(__file__).with_name('chat_ui')
TERMS = {
    'seven_space_coupon_sku_ledger/v1': ('七度空间', '七度空间+心相印'),
    'brand_coupon_plan/v1': ('珀莱雅','六个品牌','15档','美垂仓券规划'),
    'platform_joint_coupon_text/v1': ('美垂仓通用','多品牌联合券','四档联合券','品商共补'),
    'brand_meituan_dual_plan/v1': ('高露洁','代理门店'),
    'coupon_subsidy_ledger/v1': ('混补台账','三方券','两方券','二方券','谷雨99'),
    'category_coupon_text/v1': ('提转化品类券','19-2同享券'),
    'multi_id_coupon_text/v1': ('1031大促','六条短文本券','互斥品类券','暴涨红包'),
    'brand_coupon_tier_sheet/v1': ('薇诺娜','投券明细','第五档','第5档'),
    'declared_coupon_upc/v1': ('她研社','12月一券','美妆仓一券'),
    'mixed_shenjia_sheet/v1': ('神价拼表','多个神价','主活动','神价归组','子活动清单'),
    'item_subsidy_sheet/v1': ('淘宝单品补贴','淘宝闪购单品补贴','淘宝11月补贴','第72行','潘婷'),
}
STATIC = {'/': ('index.html', 'text/html'), '/chat.css': ('chat.css','text/css'),
          '/chat.js': ('chat.js','text/javascript')}


class ChatError(Exception):
    def __init__(self, status, code, message):
        self.status, self.code, self.message = status, code, message
        super().__init__(code)


def read_messages(body):
    if not isinstance(body, dict) or 'messages' not in body or set(body) - {'messages','attachments','productReceipt'}:
        raise ChatError(400, 'INVALID_REQUEST', '请输入一条消息。')
    receipt = body.get('productReceipt')
    if receipt is not None and (not isinstance(receipt,str) or not RECEIPT_ID.fullmatch(receipt)):
        raise ChatError(400,'PRODUCT_RECEIPT_INVALID','商品核对回执格式不正确。')
    messages = body['messages']
    if not isinstance(messages,list) or not 1 <= len(messages) <= 9 or len(messages) % 2 == 0:
        raise ChatError(400, 'INVALID_HISTORY', '请检查本轮对话记录。')
    normalized=[]
    for i,item in enumerate(messages):
        role = 'assistant' if i % 2 else 'user'
        content = item.get('content') if isinstance(item,dict) else None
        if (not isinstance(item,dict) or set(item) != {'role','content'} or item['role'] != role
                or not isinstance(content,str) or not 1 <= len(content.strip()) <= MAX_MESSAGE_CHARS):
            raise ChatError(400, 'INVALID_MESSAGE', '每条消息须在 1 至 3000 字之间。')
        normalized.append({'role':role,'content':content.strip()})
    if sum(len(item['content']) for item in normalized)>MAX_CONTEXT_CHARS:
        raise ChatError(413, 'CHAT_TOO_LONG', '对话内容较长，请开启新对话。')
    return normalized


def active_chat_cases(store):
    result=store.execute("""
        SELECT JSON_OBJECT('key', c.case_key, 'revision', c.revision, 'scope', c.case_scope,
                           'summary', c.confirmed_summary, 'hint', c.recognition_hint,
                           'activeCount', COUNT(*) OVER ())
        FROM approved_case c
        WHERE c.active = TRUE AND c.revision =
            (SELECT MAX(v.revision) FROM approved_case v WHERE v.case_key = c.case_key)
        ORDER BY c.case_key
        LIMIT 100;
    """)
    cases=[json.loads(line) for line in result.splitlines()]
    return cases, cases[0]['activeCount'] if cases else 0


def relevant_cases(store, messages):
    cases, count = active_chat_cases(store)
    queries=[item['content'].casefold() for item in reversed(messages) if item['role']=='user'][:3]

    def relevance(case):
        terms=TERMS.get(case['scope'],())
        scores=[len(term)*(3-index) for index,q in enumerate(queries) for term in terms if term.casefold() in q]
        return max(scores, default=0)

    ranked=sorted(((relevance(case), case) for case in cases),key=lambda item:item[0],reverse=True)
    return [case for score,case in ranked[:3] if score], count


@lru_cache(maxsize=8)
def provider_model_names(settings, refresh_window):
    from business_agent.workbench_deepseek import adapter

    try:
        private = adapter.load_private_config(settings)
        request = Request(MODEL_CATALOG_URL, method='GET',
                          headers={'Authorization':'Bearer '+private.api_key,'Accept':'application/json'})
        payload = adapter.decode(adapter.provider_call(request, 2))
    except HTTPError as exc:
        exc.close()
        return {}
    except (adapter.AdapterError, OSError, HTTPException, ValueError):
        return {}

    rows = payload.get('data') if isinstance(payload, dict) else None
    names = {}
    if isinstance(rows, list):
        for row in rows:
            if not isinstance(row, dict):
                continue
            model, name = row.get('id'), row.get('name')
            if (isinstance(model, str) and adapter.MODEL_ID.fullmatch(model)
                    and isinstance(name, str) and re.fullmatch(r'DeepSeek[- ][A-Za-z0-9. -]{1,80}', name)):
                names[model] = name
    return names


def model_metadata(settings, model):
    window = int(monotonic()) // MODEL_CATALOG_TTL_SECONDS
    name = provider_model_names(settings, window).get(model) if model else None
    version, source = MODEL_RELEASES.get((model, name), (None, None))
    return {'modelLabel':name.replace('-', ' ') if name else MODEL_LABELS.get(model,model),
            'modelNameSource':MODEL_CATALOG_URL if name else None,
            'modelVersion':version,'modelVersionSource':source}


def normalized_question(question):
    return re.sub(r'[\s，。？！?!、,]+', '', question.casefold().rstrip('.'))


def case_library_question(question):
    text = normalized_question(question)
    if CASE_LIBRARY_COUNT.fullmatch(text):
        return 'count'
    if CASE_LIBRARY_LIST.fullmatch(text):
        return 'list'
    if (CASE_LIBRARY_OVERVIEW.fullmatch(text)
            and any(word in text for word in ('本地','训练','学习','案例'))):
        return 'overview'
    return None


def runtime_question(messages):
    for item in reversed(messages[::2]):
        text = normalized_question(item['content'])
        if PROMPT_VERSION_QUESTION.fullmatch(text):
            return 'prompt_version'
        if MODEL_QUESTION.fullmatch(text) or GENERAL_VERSION_QUESTION.fullmatch(text):
            return 'model'
        if not VERSION_FOLLOWUP.fullmatch(text):
            return None
    return 'model'


def runtime_info(settings, store, viewer, worker=None, intake_contract=None, product_cache=None,
                 recognition_socket=None, product_master=None, case_editor=None, sales_settings=None, conversations=None):
    from business_agent.workbench_deepseek import adapter

    model=None
    context_ready=False
    memory_ready=False
    try:
        model=adapter.load_private_config(settings).model
    except adapter.AdapterError:
        pass
    try:
        context_ready=store.health()
    except ContextUnavailable:
        pass
    if conversations:
        try:
            conversations.list(viewer['id'],limit=1)
            memory_ready=True
        except ConversationUnavailable:
            pass
    return {'displayName':'价探活动 Agent','provider':'DeepSeek','configuredModel':model,
            **model_metadata(settings, model),'promptVersion':PROMPT_VERSION,
            'capabilities':[
                {'key':'chat','label':'直接对话和多轮追问','ready':model is not None},
                {'key':'conversation_memory','label':'保存会话、附件和确认内容，支持历史检索','ready':memory_ready},
                {'key':'case_search','label':'查询已确认的活动案例与券档口径','ready':context_ready},
                {'key':'intake_contract','label':'查询活动识别标准字段','ready':intake_contract is not None},
                {'key':'shared_intake','label':'使用工作台活动识别服务预览','ready':bool(recognition_socket and recognition_socket.is_socket())},
                {'key':'product_lookup','label':'按 UPC、内部码查询商品，支持商品分页','ready':product_cache is not None and product_cache.is_file()},
                {'key':'workbench_read','label':'按当前账号权限读取工作台记录','ready':True},
                {'key':'sales_query','label':'查询线上数据 Agent 销量','ready':bool(sales_settings and sales_settings.is_file())},
                {'key':'case_revision','label':'对话中修正已确认案例','ready':may_revise(case_editor,viewer)},
                {'key':'product_name_search','label':'按名称、品牌和规格查商品','ready':bool(
                    product_master and product_master.is_file() or product_cache and product_cache.is_file())},
            ],
            'workbenchPermissions':viewer['permissions'], 'attachments':attachment_options(worker),
            'caseRevisionEnabled':may_revise(case_editor,viewer)}


def reply_record(rid, model, reply, *, metadata, cases=(), attachments=(), source):
    return {'contractVersion':CONTRACT,'requestId':rid,'reply':reply,'model':model,
            **metadata,'source':source,'promptVersion':PROMPT_VERSION,'attachments':list(attachments),
            'caseReferences':[{key:row[key] for key in ('key','revision')} for row in cases]}


def case_library_answer(settings, store, request_id, intent):
    from business_agent.workbench_deepseek import adapter

    cases, count = active_chat_cases(store)
    model = None
    try:
        model = adapter.load_private_config(settings).model
    except adapter.AdapterError:
        pass
    reply = f'本地案例库现有 {count} 个已确认案例。'
    selected = []
    if count:
        reply += '这些业务口径可跨对话查询。'
        if intent == 'list':
            selected = cases[:CASE_LIBRARY_LIST_LIMIT]
            lines = ['- ' + re.split(r'[。；]', row['summary'], maxsplit=1)[0] for row in selected]
            reply += '\n\n' + '\n'.join(lines)
            if count > len(selected):
                reply += f'\n\n以上展示 {len(selected)} 个案例。'
        reply += '\n\n可以按品牌、活动名称或活动编号继续查。'
    store.note_use(request_id, selected)
    return {**reply_record(request_id,model,reply,metadata=LOCAL_METADATA,cases=selected,source='case_library'),
            'caseCount':count}


def model_completion(private, messages, provider, deadline, *, tools=(), tool_choice=None):
    from business_agent.workbench_deepseek import adapter

    body = {'model':private.model, 'messages':messages, 'max_tokens':2048,
            'stream':False, 'thinking':{'type':'disabled'}}
    if tool_choice:
        body.update(tools=tools, tool_choice=tool_choice)
    request = Request(adapter.ENDPOINT,data=adapter.encode(body),method='POST',
                      headers={'Authorization':'Bearer '+private.api_key,'Content-Type':'application/json'})
    timeout = deadline - monotonic()
    if timeout <= 0:
        raise TimeoutError('Model deadline')
    result = adapter.decode(provider(request,timeout))
    choices, model = result.get('choices'), result.get('model')
    if (not isinstance(choices,list) or len(choices) != 1 or not isinstance(choices[0],dict)
            or not isinstance(model,str) or not adapter.MODEL_ID.fullmatch(model)):
        raise ValueError('Invalid model response')
    return choices[0], model


def completion_text(choice):
    message = choice.get('message')
    text = message.get('content') if isinstance(message,dict) else None
    if choice.get('finish_reason') == 'length':
        raise ChatError(502,'MODEL_TRUNCATED','DeepSeek 回复过长，请缩小问题范围再试。')
    if choice.get('finish_reason') != 'stop' or not isinstance(text,str) or not text.strip() or len(text) > 7000:
        raise ValueError('Invalid model reply')
    if RAW_TOOL_MARKUP.search(text):
        raise ChatError(502,'MODEL_TOOL_FORMAT','DeepSeek 返回的工具格式异常，本轮未生成可显示的答复。')
    return text.strip()


def product_followup(rid, messages, attachments, attachment_results, cache, master, prior, record,
                     count, selection, column):
    snapshot = product_snapshot({}, attachments, previous=prior) or {}
    if column:
        return reply_record(rid,None,describe_column(snapshot.get('headers',[]),column,messages[-1]['content']),
                            metadata=LOCAL_METADATA,attachments=attachment_results,source='attachment_column')

    if not snapshot.get('counts'):
        rows = query_rows(attachments,messages)
        remembered_rows = not rows and prior and prior.get('rawRows')
        if remembered_rows:
            rows = remembered_rows
        if rows:
            lookup = lookup_catalog(cache,rows,master)
            if remembered_rows and lookup.get('status') == 'available':
                lookup['inputRows'] = prior['rawInputRows']
            snapshot = product_snapshot({'productLookup':lookup}, attachments, previous=prior) or {}

    counts = snapshot.get('counts')
    if selection and counts:
        result = select_products(snapshot,selection)
        reply = describe_selection(result)
        data = reply_record(rid,None,reply,metadata=LOCAL_METADATA,attachments=attachment_results,
                            source='product_selection')
        data.update(productSelection=result, historyReply=f'已在本机筛选出 {len(result["items"])} 个带内部码的商品。',
                    _receiptSnapshot=snapshot)
        return data
    if not count and not record:
        if selection and (attachments or prior):
            reply = '这批材料暂时没有可用的商品核对结果。请检查读取回执或商品目录状态。'
            return reply_record(rid,None,reply,metadata=LOCAL_METADATA,attachments=attachment_results,
                                source='product_selection')
        return None

    if record:
        files = '、'.join(item['filename'] for item in attachments)
        header = f"已读取工作台提报 {record['submissionId']}（输入修订 {record['inputRevision']}）。"
        if files:
            header += f'原件为《{files}》。'
        else:
            header += '这条记录当前没有可读取的表格或文档。'
        if record['skipped']:
            header += '部分材料因下载权限、文件大小、数量上限或完整性校验无法读取。'
        reply = header
        if counts:
            reply += '\n\n' + describe_counts(counts,count or 'recognized')
        elif files:
            reply += '当前未取得可用的商品核对结果。'
        source = 'workbench_submission'
    else:
        reply = (describe_counts(counts,count) if counts else
                 '当前暂缺可核对的商品结果。可以发工作台提报链接，或提供原表。')
        source = 'product_count'

    data = reply_record(rid,None,reply,metadata=LOCAL_METADATA,attachments=attachment_results,source=source)
    if record:
        data['workbenchSubmission'] = record
    if counts:
        data.update(productCounts=counts,_receiptSnapshot=snapshot)
    return data


def answer(settings, store, messages, *, attachments=(), attachment_results=(), intake_contract=None,
           product_cache=None, product_master=None, recognition_socket=None, recognition_release=None,
           prior_product_snapshot=None, workbench_record=None, case_revision_enabled=False, transport=None,
           tool_viewer=None, tool_cookie=None, tool_editor=None, attachment_worker=None, sales_settings=None,
           conversation_memory=None, request_id=None):
    from business_agent.workbench_deepseek import adapter

    rid=request_id or 'CHAT-'+uuid4().hex
    try:
        question_text = messages[-1]['content']
        from chat_sales_tools import SOURCE_TOOL_NAME, requested_sales, requested_amount, source_checks
        sales_platform_list = requested_sales(messages)
        checks = source_checks(sales_platform_list,sales_settings,recognition_release) + requested_amount(messages)
        if checks and all(row['status']=='source_unavailable' for row in checks):
            record = reply_record(rid,None,'\n'.join(row['message'] for row in checks),metadata=LOCAL_METADATA,source='sales_source')
            record.update(toolsUsed=[SOURCE_TOOL_NAME],
                          toolResults=[{'name':SOURCE_TOOL_NAME,'status':row['status'],'code':row['code'],'platformCode':row['platformCode']} for row in checks],
                          toolReceipts=[{'name':SOURCE_TOOL_NAME,'result':row} for row in checks])
            return record
        normalized = normalized_question(question_text)
        worksheet_query = cell_request(question_text)
        if worksheet_query:
            reply = read_sheet_cells(attachments, conversation_memory, question_text, worksheet_query)
            return reply_record(rid,None,reply,metadata=LOCAL_METADATA,attachments=attachment_results,
                                source='worksheet_cells')
        if not workbench_record and asks_attachment_receipt(normalized):
            reply = describe_attachment_receipt(attachment_results)
            return reply_record(rid,None,reply,metadata=LOCAL_METADATA,attachments=attachment_results,
                                source='attachment_receipt')
        if not workbench_record and PLATFORM_QUESTION.fullmatch(normalized):
            return reply_record(rid,None,describe_platforms(),metadata=LOCAL_METADATA,source='intake_contract')
        if not workbench_record and is_field_question(normalized):
            if intake_contract is None:
                raise ChatError(503, 'INTAKE_CONTRACT_UNAVAILABLE', '当前活动识别契约暂不可读。')
            reply = describe_intake_contract(intake_contract)
            return reply_record(rid,None,reply,metadata=LOCAL_METADATA,source='intake_contract')
        snapshot = product_snapshot({},attachments,previous=prior_product_snapshot) or {}
        try:
            requested_page = page_intent(normalized,snapshot.get('page'))
        except ValueError:
            raise ChatError(400,'PRODUCT_PAGE_INVALID','请提供有效的商品序号范围，每页最多 100 条；可继续翻页。') from None
        if requested_page and not snapshot.get('rawRows') and conversation_memory and conversation_memory.state.get('attachments'):
            # A detached historical file is still readable: let the Agent restore it.
            requested_page=None
        if requested_page:
            rows = snapshot.get('rawRows',[])
            if not rows:
                return reply_record(rid,None,'当前没有可翻页的商品材料。请保留附件后发送，或提供已有提报链接。',
                                    metadata=LOCAL_METADATA,source='product_page')
            offset,limit = requested_page
            lookup = lookup_catalog(product_cache,rows,product_master,offset=offset,limit=limit)
            reply = describe_product_lookup(lookup)
            if snapshot.get('sourcePartial'):
                reply += '\n原文件仅部分解析，以上总数仅为已解析的商品行。'
            data = reply_record(rid,None,reply,metadata=LOCAL_METADATA,attachments=attachment_results,source='product_page')
            data.update(productLookup=lookup,historyReply=(f"已返回商品第 {lookup['page']['start']}–{lookup['page']['end']} 条，本地已解析 {len(rows)} 条，可继续翻页。"
                        if lookup['rows'] else f"本地共 {len(rows)} 条商品，该范围没有更多商品。"),
                        _receiptSnapshot=product_snapshot({'productLookup':lookup},attachments,previous=snapshot))
            return data
        intent = count_intent(normalized)
        selection = selection_intent(normalized)
        column = column_intent(normalized)
        if workbench_record or intent or selection or column:
            result = product_followup(
                rid, messages, attachments, attachment_results, product_cache, product_master,
                prior_product_snapshot, workbench_record, intent, selection, column)
            if result is not None:
                return result
        library_intent = case_library_question(question_text)
        if library_intent:
            return case_library_answer(settings, store, rid, library_intent)
        if is_data_agent_access_question(normalized_question(question_text)):
            return reply_record(rid,None,describe_access(product_cache,product_master),metadata=LOCAL_METADATA,
                                source='runtime_config')
        product_question = is_product_question(question_text)
        material_recognition = requests_recognition(question_text)
        if attachments and material_recognition and (not product_question or asks_for_draft(question_text)
                                                      or '活动' in question_text):
            catalog = lookup_catalog(product_cache,[])
            catalog_revision = catalog.get('sourceDate','chat-preview')
            request = intake_request(rid,attachments,question_text,catalog_revision)
            received_at = time()
            preview, details = run_preview(recognition_socket,recognition_release,request)
            periods = preview_periods(preview, request['payload']['manualFields'], conversation_memory, received_at)
            reply = describe_preview(preview, details, periods)
            response = reply_record(rid,details['model'],reply,metadata=LOCAL_METADATA,
                                    attachments=attachment_results,source='shared_intake')
            response['recognition'] = {
                'result':{key:value for key,value in preview.items() if key != 'materialResults'},
                'metadata':details}
            if periods:
                response['recognition']['candidatePeriods'] = periods
            if any(group['productRows'] for group in preview['candidates']):
                response['productLookup'] = lookup_recognized_products(product_cache,preview)
            if product_question:
                directory = response.get('productLookup') or lookup_catalog(product_cache,[],product_master)
                response['historyReply'] = reply
                response['reply'] += '\n\n' + describe_product_lookup(directory)
            return response

        name_query = contextual_name_query(messages)
        if name_query and not (typed_upcs(question_text) or typed_internal_codes(question_text)):
            result=search_product_names(product_master,product_cache,name_query)
            reply=describe_product_search(result)
            return {**reply_record(rid,None,reply,metadata=LOCAL_METADATA,attachments=attachment_results,
                                   source='product_name_search'), 'productSearch':result,
                    'historyReply':f'已按「{name_query}」在本机商品目录查找候选。'}

        catalog_result = None
        if product_question and query_rows(attachments,messages):
            catalog_result = lookup_catalog(product_cache,query_rows(attachments,messages),product_master)
            if not asks_for_draft(question_text) and not re.search(r'销量|案例|后台|记录|然后|继续|一起|并',question_text):
                reply = describe_product_lookup(catalog_result)
                return {**reply_record(rid,None,reply,metadata=LOCAL_METADATA,attachments=attachment_results,
                                       source='product_catalog'), 'productLookup':catalog_result,
                        'historyReply':'已在本机显示商品目录核对结果。'}

        private=adapter.load_private_config(settings)
        question = None if attachments else runtime_question(messages)
        metadata = model_metadata(settings, private.model)
        if question == 'prompt_version':
            return reply_record(rid,private.model,f'对话提示词版本是 {PROMPT_VERSION}。',
                                metadata=metadata,source='runtime_config')
        if question == 'model':
            model = private.model
            label, version = metadata['modelLabel'], metadata['modelVersion']
            display = f'{label}（接口标识 {model}）' if label != model else model
            reply = f'我用的是 {display}。'
            if metadata['modelNameSource']:
                reply += '模型名称来自 DeepSeek 官方模型目录。'
            if version:
                reply += f'官方发布公告记录的版本是 {version}。'
            else:
                reply += '具体发布版本待核实。'
            return reply_record(rid,model,reply,metadata=metadata,source='runtime_config')
        cases, count=relevant_cases(store,messages)
        prompt=Path(__file__).with_name('chat-prompt-v18.txt').read_text(encoding='utf-8') + '\n' + business_policy()
        business_tools = BusinessTools(
            request_id=rid,messages=messages,store=store,viewer=tool_viewer,cookie=tool_cookie,
            editor=tool_editor,worker=attachment_worker,contract=intake_contract,cache=product_cache,
            master=product_master,release=recognition_release,sales_settings=sales_settings,
            attachments=attachments,prior=prior_product_snapshot,recognition_socket=recognition_socket,
            conversation_memory=conversation_memory)
        tools = business_tools.schemas
        context={'runtime':{'role':'价探活动 Agent','provider':'DeepSeek','model':private.model,
                            **metadata,'promptVersion':PROMPT_VERSION,
                            'tools':[item['function']['name'] for item in tools],
                            'workbenchPermissions':(tool_viewer or {}).get('permissions',[]),
                            'dataAgentProductCatalogEnabled':bool(product_cache and product_cache.is_file()),
                            'caseRevisionEnabled':case_revision_enabled},
                 'conversationMemory':conversation_memory.context() if conversation_memory else None,
                 'productContext':business_tools.context(),
                 'localProductLookupAppended':catalog_result is not None,
                 'intakeContract':intake_contract, 'caseCount':count,'retrievedCases':[{k: row[k] for k in ('key','revision','summary','hint')} for row in cases]}
        history = [message if message['role'] != 'assistant' or not RAW_TOOL_MARKUP.search(message['content'])
                   else {'role':'assistant','content':'上一轮工具回复格式异常。'} for message in messages]
        model_messages=[{'role':'system','content':prompt+'\n'+json.dumps(context,ensure_ascii=False)},*history]
        if attachments:
            excerpts = [{key:value for key,value in attachment.items() if key not in ('productRows','document','sheetHeaders')}
                        for attachment in attachments]
            material = json.dumps({'attachments':excerpts},ensure_ascii=False)
            model_messages[-1] = {**messages[-1],
                                  'content':messages[-1]['content']+'\n\n附件数据（含原文位置）\n'+material}
        store.note_use(rid,cases)
        provider=transport or adapter.provider_call
        deadline=monotonic()+120
        name_result = None
        summary_failed = False
        choice, model = model_completion(private,model_messages,provider,deadline,tools=tools,tool_choice='auto')
        for step in range(6):
            if choice.get('finish_reason') != 'tool_calls':
                break
            message = choice.get('message') or {}
            calls = message.get('tool_calls')
            if not isinstance(calls,list) or not 1 <= len(calls) <= 4:
                if business_tools.results:
                    summary_failed = True
                    choice = {'finish_reason':'stop','message':{'content':'后续工具参数异常，已返回的结果如下。'}}
                    break
                raise ValueError('Invalid tool call batch')
            model_messages.append({'role':'assistant','content':None,'tool_calls':calls})
            for call in calls:
                model_messages.append(business_tools.execute(call))
            try:
                choice, model = model_completion(private,model_messages,provider,deadline,tools=tools,
                                                  tool_choice='none' if step==5 else 'auto')
            except (ChatError,HTTPError,URLError,adapter.AdapterError,ValueError,OSError):
                summary_failed = True
                choice = {'finish_reason':'stop','message':{'content':'工具已返回回执，文字汇总暂不可用。'}}
                break
        try:
            content = completion_text(choice)
        except (ChatError,ValueError):
            if not business_tools.trace:
                raise
            summary_failed = True
            content = '工具回执已保留。'
        catalog_result = business_tools.results.get('productLookup',catalog_result)
        name_result = business_tools.results.get('productSearch')
        if model != private.model:
            metadata = model_metadata(settings, model)
        reply = content.strip()
        if catalog_result is not None:
            reply += '\n\n' + describe_product_lookup(catalog_result)
        if name_result is not None:
            reply += '\n\n' + describe_product_search(name_result)
        result = reply_record(rid,model,reply,metadata=metadata,cases=cases,
                              attachments=attachment_results,source='deepseek')
        if catalog_result is not None:
            result['productLookup'] = catalog_result
            result['historyReply'] = content.strip()
        if name_result is not None:
            result['productSearch'] = name_result
            result['historyReply'] = content.strip()
        if business_tools.trace:
            result['toolsUsed'] = list(dict.fromkeys(row['name'] for row in business_tools.trace))
            result['toolResults'] = business_tools.trace
            result['toolReceipts'] = business_tools.receipts
            if summary_failed:
                result['historyReply'] = '工具调用已结束，本机保留了真实回执。'
                for receipt in business_tools.receipts:
                    text = json.dumps(receipt['result'],ensure_ascii=False,indent=2)
                    result['reply'] += '\n\n' + receipt['name'] + '\n' + text[:10000]
                    if len(text)>10000:
                        result['reply'] += '\n该回执较长，页面展示前 10000 字，完整结果保留在本次回执中。'
        for key in ('salesQuery','caseRevisions','workbenchSubmission','recognition'):
            if key in business_tools.results:
                result[key] = business_tools.results[key]
        if checks:
            from chat_sales_tools import describe_sales_result, PLATFORM_LABELS
            receipts = business_tools.results.get('salesQueries',{})
            parts = []
            for row in checks:
                code = row['platformCode']
                if row.get('metric'):
                    parts.append(row['message'])
                elif code in receipts:
                    parts.append(describe_sales_result(receipts[code]))
                elif row['status']=='source_unavailable':
                    parts.append(row['message'])
                else:
                    parts.append(f"{PLATFORM_LABELS[code]}尚无本次查询的销量回执，请核对商品内部码、查询周期和门店范围。")
            result['reply'] = '\n'.join(parts)
            result['historyReply'] = result['reply']
            result.setdefault('toolReceipts',[]).extend({'name':SOURCE_TOOL_NAME,'result':row} for row in checks)
            result.setdefault('toolResults',[]).extend({'name':SOURCE_TOOL_NAME,'status':row['status'],'code':row['code'],'platformCode':row['platformCode']} for row in checks)
            if SOURCE_TOOL_NAME not in result.setdefault('toolsUsed',[]):
                result['toolsUsed'].append(SOURCE_TOOL_NAME)
        elif business_tools.results.get('salesQuery'):
            from chat_sales_tools import describe_sales_result
            result['reply'] += '\n\n' + describe_sales_result(business_tools.results['salesQuery'])
            result['historyReply'] = content.strip()
        for change in business_tools.results.get('caseRevisions',[]):
            result['reply'] += f"\n\n已保存案例 {change['caseKey']}，版本 {change['revision']}。"
        snapshot = product_snapshot(result,business_tools.attachments,previous=business_tools.prior)
        if snapshot:
            result['_receiptSnapshot'] = snapshot
        return result
    except ContextUnavailable:
        raise ChatError(503,'CONTEXT_UNAVAILABLE','案例库暂不可用，请稍后重试。') from None
    except HTTPError as exc:
        status=exc.code
        exc.close()
        if status in (401,403):
            raise ChatError(503,'MODEL_AUTH','DeepSeek 鉴权失败，请检查本地 Agent 配置。') from None
        if status in (402,429):
            raise ChatError(429,'MODEL_QUOTA','DeepSeek 请求限额已到，请稍后重试。') from None
        raise ChatError(502,'MODEL_UNAVAILABLE','DeepSeek 暂不可用，请稍后重试。') from None
    except TimeoutError:
        raise ChatError(504,'MODEL_TIMEOUT','DeepSeek 回复超时，请重试。') from None
    except URLError as exc:
        status=504 if isinstance(exc.reason,TimeoutError) else 502
        raise ChatError(status,'MODEL_CONNECTION','连接 DeepSeek 失败，请重试。') from None
    except adapter.AdapterError as exc:
        if exc.code=='MODEL_RECOGNITION_UNAVAILABLE':
            raise ChatError(503,'MODEL_SETTINGS','暂时无法读取 DeepSeek 配置。') from None
        raise ChatError(502,'MODEL_OUTPUT','暂时无法读取 DeepSeek 的回复。') from None
    except (ValueError,TypeError,KeyError,AttributeError,IndexError,OSError,UnicodeError):
        raise ChatError(502,'MODEL_OUTPUT','暂时无法读取 DeepSeek 的回复。') from None


def workbench_actor(cookie):
    if not cookie or len(cookie)>4096:
        raise ChatError(401,'LOGIN_REQUIRED','请先登录价探工作台。')
    conn=HTTPConnection('127.0.0.1',BACKEND_PORT,timeout=4)
    try:
        conn.request('GET','/api/test/v1/operations/context',headers={'Cookie':cookie,'Accept':'application/json'})
        reply=conn.getresponse()
        if reply.status in (401,403):
            raise ChatError(401,'LOGIN_REQUIRED','请先登录价探工作台。')
        if reply.status!=200:
            raise ValueError
        data=json.loads(reply.read(65536))['data']
        actor=data['actor']
        permissions = data['permissions']
        if not isinstance(permissions,list) or any(not isinstance(code,str) for code in permissions):
            raise ValueError('Unexpected permissions')
        if data['authenticated'] is not True or 'operations.read' not in permissions:
            raise ChatError(403,'CHAT_FORBIDDEN','此工作台账号没有活动读取权限。')
        return {'id':actor['id'],'name':actor.get('name') or actor['username'],'permissions':sorted(set(permissions))}
    except (OSError,HTTPException,ValueError,TypeError,KeyError):
        raise ChatError(503,'SESSION_SERVICE_UNAVAILABLE','暂时无法核验工作台登录状态。') from None
    finally:
        conn.close()


class ChatServer(ThreadingHTTPServer):
    daemon_threads=True
    def __init__(self,address, settings, store, *, attachment_worker=None, intake_contract=None,
                 product_cache=None, product_master=None, recognition_socket=None, recognition_release=None,
                 case_editor=None, sales_settings=None, conversations=None, conversation_files=None,
                 authenticate=workbench_actor, chat=answer):
        self.conversations,self.conversation_files = conversations,conversation_files
        self.instance_id=uuid4().hex
        self.settings, self.store = settings, store
        self.authenticate = authenticate
        chat_options = {key:value for key,value in {'intake_contract':intake_contract,
                        'product_cache':product_cache,'product_master':product_master,'recognition_socket':recognition_socket,
                        'recognition_release':recognition_release}.items() if value is not None}
        self.chat = partial(chat,**chat_options)
        self.intake_contract = intake_contract
        self.product_cache = product_cache
        self.product_master = product_master
        self.recognition_socket = recognition_socket
        self.attachment_worker = attachment_worker
        self.model_slots=BoundedSemaphore(2)
        self.case_editor = case_editor
        self.sales_settings = sales_settings
        self.product_receipts = ProductReceipts()
        super().__init__(address,Handler)

    def get_request(self):
        connection,address = super().get_request()
        connection.settimeout(30)
        return connection,address


class Handler(BaseHTTPRequestHandler):
    protocol_version='HTTP/1.1'
    def log_message(self,*args):
        pass

    def do_GET(self): self.dispatch('GET')
    def do_POST(self): self.dispatch('POST')
    def do_OPTIONS(self): self.dispatch('OPTIONS')

    def reply(self,status,body,media='application/json'):
        self.send_response(status)
        self.send_header('Content-Type',media+'; charset=utf-8')
        self.send_header('Content-Length',str(len(body)))
        self.send_header('Cache-Control','no-store')
        self.send_header('X-Content-Type-Options','nosniff')
        self.send_header('Referrer-Policy','no-referrer')
        self.send_header('Content-Security-Policy',"default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
        self.send_header('Connection','close')
        self.close_connection=True
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError,ConnectionResetError):
            pass

    def send_json(self,status,value):
        self.reply(status,json.dumps(value,ensure_ascii=False).encode('utf-8'))

    def dispatch(self,method):
        from business_agent.workbench_deepseek import adapter

        host=f'127.0.0.1:{self.server.server_port}'
        origin=self.headers.get('Origin')
        try:
            navigation = method=='GET' and self.path=='/'
            if (self.headers.get('Host')!=host or not navigation and self.headers.get('Sec-Fetch-Site')=='cross-site'
                    or origin not in (None,'http://'+host) or method=='POST' and origin is None):
                raise ChatError(403,'ORIGIN_DENIED','请从本机活动 Agent 页面打开。')
            if method=='GET' and self.path in STATIC:
                name,media=STATIC[self.path]
                self.reply(200,(UI/name).read_bytes(),media)
                return
            if method=='GET' and self.path in (PREFIX+'/session',PREFIX+'/capabilities'):
                viewer=self.server.authenticate(self.headers.get('Cookie'))
                data=viewer if self.path.endswith('/session') else runtime_info(
                    self.server.settings,self.server.store,viewer,self.server.attachment_worker,
                    self.server.intake_contract,self.server.product_cache,self.server.recognition_socket,
                    self.server.product_master,self.server.case_editor,self.server.sales_settings,self.server.conversations)
                self.send_json(200,{'data':data})
                return
            if self.path.startswith(PREFIX+'/conversations'):
                self.handle_conversations(method,adapter)
                return
            if self.path in (PREFIX+'/cases',PREFIX+'/cases/revise'):
                self.handle_cases(method,adapter)
                return
            if method!='POST' or self.path!=PREFIX+'/chat':
                raise ChatError(404,'NOT_FOUND','对话接口不存在。')
            viewer = self.server.authenticate(self.headers.get('Cookie'))
            length=self.headers.get('Content-Length','')
            if (self.headers.get('Transfer-Encoding') or self.headers.get_content_type()!='application/json'
                    or not re.fullmatch(r'[0-9]{1,8}', length)):
                raise ChatError(400,'INVALID_REQUEST','请求须使用 JSON。')
            if not 0<int(length)<=MAX_ATTACHMENT_BODY:
                raise ChatError(413,'INPUT_TOO_LARGE','消息或附件过大，请减少内容后重试。')
            if not self.server.model_slots.acquire(blocking=False):
                raise ChatError(429,'CHAT_BUSY','活动 Agent 正在回答其他问题，请稍后重试。')
            try:
                try:
                    body=adapter.decode(self.rfile.read(int(length)))
                    messages=read_messages(body) if not self.server.conversations else None
                    if not isinstance(body,dict):
                        raise ChatError(400,'INVALID_REQUEST','请求格式不正确。')
                except (adapter.AdapterError,TimeoutError,ValueError,UnicodeError):
                    raise ChatError(400,'INVALID_JSON','对话 JSON 无效。') from None
                if self.server.conversations:
                    data=run_persistent_chat(self.server,viewer,body,self.headers.get('Cookie'))
                    self.send_json(200,{'data':data})
                    return
                uploads=body.get('attachments', [])
                if int(length)>MAX_BODY and uploads==[]:
                    raise ChatError(413,'INPUT_TOO_LARGE','消息过长，请缩短后重试。')
                question = messages[-1]['content']
                normalized = normalized_question(question)
                submission_id = requested_submission(question)
                receipt = body.get('productReceipt')
                actor_id = viewer.get('id')
                prior = self.server.product_receipts.read(actor_id,receipt) if receipt else None
                chat_options = {'tool_viewer':viewer,'tool_cookie':self.headers.get('Cookie'),
                                'tool_editor':self.server.case_editor,'attachment_worker':self.server.attachment_worker,
                                'sales_settings':self.server.sales_settings}
                if may_revise(self.server.case_editor,viewer):
                    chat_options['case_revision_enabled'] = True
                attachments, results = [], []
                if submission_id:
                    record, saved_uploads = load_submission(BACKEND_PORT,self.headers.get('Cookie'),submission_id)
                    attachments, results = prepare_attachments(self.server.attachment_worker,saved_uploads,uuid4().hex,include_documents=True)
                    chat_options['workbench_record'] = record
                    prior = None
                elif uploads != []:
                    same_material = bool(prior and prior.get('materialFiles') ==
                        [(name,digest) for name,encoded,digest in read_uploads(uploads)])
                    try:
                        navigation = page_intent(normalized,(prior or {}).get('page')) is not None
                    except ValueError:
                        raise ChatError(400,'PRODUCT_PAGE_INVALID','请提供有效的商品序号范围，每页最多 100 条。') from None
                    skip_upload = (case_library_question(question) or PLATFORM_QUESTION.fullmatch(normalized) or is_field_question(normalized)
                                   or is_data_agent_access_question(normalized) or CASE_CORRECTION_QUESTION.search(normalized)
                                   or (same_material and not cell_request(question) and (navigation or count_intent(normalized) or selection_intent(normalized)
                                                  or column_intent(normalized))))
                    if not skip_upload:
                        attachments,results=prepare_attachments(
                            self.server.attachment_worker,uploads,uuid4().hex,include_documents=True)
                        if not attachments and not asks_attachment_receipt(normalized):
                            self.send_json(422,{'error':{'code':'ATTACHMENT_NO_TEXT','message':'附件没有可供分析的内容。',
                                                        'attachments':results}})
                            return
                if prior:
                    chat_options['prior_product_snapshot'] = prior
                if attachments or results:
                    chat_options.update(attachments=attachments,attachment_results=results)
                data = self.server.chat(self.server.settings,self.server.store,messages,**chat_options)
                snapshot = data.pop('_receiptSnapshot',None)
                if snapshot is None:
                    snapshot = product_snapshot(data,attachments,previous=prior)
                if snapshot and actor_id:
                    data['productReceipt'] = (receipt if snapshot == prior else
                                              self.server.product_receipts.remember(actor_id,snapshot))
                elif submission_id:
                    data['productReceipt'] = None
                if isinstance(data.get('productLookup'),dict):
                    data['productLookup'].pop('_sourceRows',None)
                self.send_json(200,{'data':data})
            finally:
                self.server.model_slots.release()
        except (ChatError, AttachmentError, IntakePreviewError, CaseRevisionError, WorkbenchReadError) as exc:
            detail={'code':exc.code,'message':exc.message}
            if getattr(exc,'conversation',None):detail['conversation']=exc.conversation
            self.send_json(exc.status,{'error':detail})
        except ConversationNotFound:
            self.send_json(404,{'error':{'code':'CONVERSATION_NOT_FOUND','message':'此账号无法读取该会话。'}})
        except ConversationConflict:
            self.send_json(409,{'error':{'code':'CONVERSATION_CHANGED','message':'会话已有更新，请刷新后重试。'}})
        except ConversationUnavailable:
            self.send_json(503,{'error':{'code':'CONVERSATION_STORAGE_UNAVAILABLE','message':'本地记忆存储暂不可用，请保留本次输入后重试。'}})
        except ValueError:
            self.send_json(400,{'error':{'code':'INVALID_REQUEST','message':'请求参数不正确。'}})


    def handle_conversations(self,method,adapter):
        viewer=self.server.authenticate(self.headers.get('Cookie'))
        if not self.server.conversations:
            raise ChatError(503,'CONVERSATION_UNAVAILABLE','会话保存暂未启用。')
        parsed=urlsplit(self.path)
        suffix=parsed.path[len(PREFIX+'/conversations'):]
        if suffix=='':
            if method=='GET':
                query=parse_qs(parsed.query)
                if set(query)-{'offset'}:raise ValueError('Invalid history page')
                offset=int(query.get('offset',['0'])[0])
                rows=self.server.conversations.list(viewer['id'],limit=51,offset=offset)
                self.send_json(200,{'data':{'conversations':rows[:50],'hasMore':len(rows)>50,'nextOffset':offset+50 if len(rows)>50 else None}})
                return
            if method=='POST':
                length=self.headers.get('Content-Length','')
                if self.headers.get('Transfer-Encoding') or self.headers.get_content_type()!='application/json' or not length.isdigit() or not 0<int(length)<=1024:
                    raise ChatError(400,'INVALID_REQUEST','新会话请求格式不正确。')
                try:new_body=adapter.decode(self.rfile.read(int(length)))
                except adapter.AdapterError:
                    raise ChatError(400,'INVALID_REQUEST','新会话请求格式不正确。') from None
                if new_body!={}:
                    raise ChatError(400,'INVALID_REQUEST','新会话请求格式不正确。')
                self.send_json(201,{'data':conversation_view(self.server.conversations.create(viewer['id']))})
                return
        match=re.fullmatch(r'/([0-9a-f]{32})(?:/attachments/([0-9a-f]{32}))?',suffix)
        if method!='GET' or not match:
            raise ChatError(404,'NOT_FOUND','会话接口不存在。')
        cid,aid=match.groups()
        record=recover_record(self.server,viewer['id'],cid)
        if aid:
            ref=next((r for r in record['state'].get('attachments',[]) if r['id']==aid),None)
            if not ref:raise ChatError(404,'ATTACHMENT_NOT_FOUND','该会话没有此附件。')
            self.reply(200,self.server.conversation_files.download(viewer['id'],cid,ref),'application/octet-stream')
        else:self.send_json(200,{'data':conversation_view(record)})


    def handle_cases(self, method, adapter):
        viewer = self.server.authenticate(self.headers.get('Cookie'))
        if not may_revise(self.server.case_editor,viewer):
            raise ChatError(403,'CASE_REVISE_FORBIDDEN','请用工作台管理员账号修正案例口径。')
        try:
            if method == 'GET' and self.path.endswith('/cases'):
                cases,_ = active_chat_cases(self.server.store)
                self.send_json(200,{'data':{'cases':cases}})
                return
            if method != 'POST' or not self.path.endswith('/revise'):
                raise ChatError(404,'NOT_FOUND','案例编辑接口不存在。')
            length = self.headers.get('Content-Length','')
            if (self.headers.get('Transfer-Encoding') or self.headers.get_content_type() != 'application/json'
                    or not re.fullmatch(r'[0-9]{1,5}',length) or not 0 < int(length) <= 16000):
                raise ChatError(400,'CASE_REVISION_INVALID','案例修正请求格式不正确。')
            try:
                body = adapter.decode(self.rfile.read(int(length)))
            except (adapter.AdapterError,TimeoutError):
                raise ChatError(400,'CASE_REVISION_INVALID','案例修正请求格式不正确。') from None
            result = save_revision(self.server.case_editor,body,viewer['id'])
            self.send_json(200,{'data':result})
        except ContextUnavailable:
            raise ChatError(503,'CONTEXT_UNAVAILABLE','案例库暂不可用，请保留修正内容后重试。') from None


if __name__=='__main__':
    cli=argparse.ArgumentParser(description=__doc__)
    cli.add_argument('--release',type=Path,required=True)
    cli.add_argument('--runtime-profile',type=Path,required=True)
    cli.add_argument('--case-editor-profile',type=Path)
    cli.add_argument('--deepseek-settings',type=Path,required=True)
    cli.add_argument('--product-catalog-cache',type=Path)
    cli.add_argument('--product-master-source',type=Path)
    cli.add_argument('--recognition-socket',type=Path)
    cli.add_argument('--port',type=int,default=52671)
    cli.add_argument('--sales-settings',type=Path)
    cli.add_argument('--conversation-profile',type=Path)
    cli.add_argument('--conversation-dir',type=Path)
    cli.add_argument('--mysql',default='/usr/local/mysql/bin/mysql')
    args=cli.parse_args()
    imports(args.release)
    if args.recognition_socket:
        use_release_validators(args.release)
    worker=args.release.resolve()/'released/business_agent/activity_intake_108/worker.py'
    case_editor = CaseMemory(args.mysql,args.case_editor_profile) if args.case_editor_profile else None
    conversation_profile=args.conversation_profile or args.case_editor_profile or args.runtime_profile.with_name('operator.cnf')
    conversations=ConversationStore(CaseMemory(args.mysql,conversation_profile))
    # Fail clearly on a missing migration instead of silently returning to volatile chat.
    conversations.list('__conversation_startup_check__',limit=1)
    files=AttachmentStore(args.conversation_dir or args.runtime_profile.parent/'conversation-files')
    with ChatServer(('127.0.0.1',args.port),args.deepseek_settings,CaseMemory(args.mysql,args.runtime_profile),
                    attachment_worker=worker, intake_contract=load_intake_contract(args.release),
                    product_cache=args.product_catalog_cache, product_master=args.product_master_source,
                    recognition_socket=args.recognition_socket,
                    recognition_release=args.release,case_editor=case_editor,sales_settings=args.sales_settings,
                    conversations=conversations,conversation_files=files) as server:
        print(f'活动 Agent 对话已启动于 127.0.0.1:{server.server_port}',flush=True)
        server.serve_forever()
