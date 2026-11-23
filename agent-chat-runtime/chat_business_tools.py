"""Bind the existing business operations to the authenticated conversation."""

import json
import re
from uuid import NAMESPACE_URL, uuid5

from case_revisions import CaseRevisionError, may_revise
from chat_attachments import AttachmentError, prepare_attachments
from chat_case_tools import CASE_SEARCH_TOOL, CASE_REVISE_TOOL, run_case_search, run_case_revision
from chat_data_tools import (NAME_TOOL, PRODUCT_TOOL, read_tool_arguments, run_name_tool,
                             run_product_tool, tool_summary)
from chat_product_lookup import query_rows, lookup_recognized_products
from chat_shared_intake import intake_request, run_preview, IntakePreviewError
from chat_workbench_records import load_submission, WorkbenchReadError
from chat_workbench_tools import WORKBENCH_READ_TOOL, read_workbench
from memory import ContextUnavailable
from chat_sheet_cells import read_sheet_cells
from product_receipts import product_snapshot
from product_paging import PAGE_PROPERTIES, page_info, validate_page


def tool(name, description, properties=None, required=()):
    return {'type':'function','function':{'name':name,'description':description,
            'parameters':{'type':'object','properties':properties or {},'required':list(required),
                          'additionalProperties':False}}}


CONTEXT_TOOL = tool('read_product_context','分页读取本次对话已解析商品、表头和上次查询结果。offset 从 0 开始，返回 nextOffset 可继续；不得把预览页当作全部商品。',PAGE_PROPERTIES)
CELLS_TOOL = tool('read_worksheet_cells','从当前或已保存的 Excel 原件读取指定列的非空值、原文和行号，或取前几行。问原表日期或单元格值时使用此工具。活动字段 missing 不能证明原表为空。',
                  {'column':{'type':'string','pattern':'^[A-Z]{1,3}$'},'header':{'type':'string'},
                   'firstRows':{'type':'integer','minimum':1,'maximum':20},
                   'filename':{'type':'string'},'sheet':{'type':'string'}})
MEMORY_TOOL = tool('read_conversation_memory','检索当前会话已持久保存的完整历史和确认原文；不只限最近几轮。query可用品牌或关键词，offset/limit翻页，messageId与charOffset继续读取长消息。',
                   {'query':{'type':'string'},'offset':{'type':'integer','minimum':0},'limit':{'type':'integer','minimum':1,'maximum':20},
                    'messageId':{'type':'string'},'charOffset':{'type':'integer','minimum':0}})
CONFIRM_TOOL = tool('remember_conversation_confirmation','保存用户明确确认或纠正的原文到当前会话记忆，必须引用真实user消息id，不能把附件或模型推断当确认；全局案例库另用已有修订工具。',
                    {'sourceMessageId':{'type':'string'},'text':{'type':'string'}},('sourceMessageId','text'))
RESTORE_TOOL = tool('restore_saved_attachment','重新引用当前会话以前保存的某个附件，无需用户重传。attachmentId 来自会话记忆；切换后可以继续调用商品分页、匹配和识别工具。',
                    {'attachmentId':{'type':'string'}},('attachmentId',))
CONTRACT_TOOL = tool('get_intake_contract','读取活动 Agent 提交给工作台的标准字段及字段说明。')
RECOGNITION_TOOL = tool('recognize_activities','使用与正式提报相同的活动 Agent 识别当前附件或已读提报原件，返回活动分组、字段和商品预览。',
                        {'instructions':{'type':'string'}},('instructions',))
SUBMISSION_TOOL = tool('read_submission_materials','读取工作台已有提报的原件，在本地解析表头和商品行，随后可查询商品或识别活动。',
                       {'submissionId':{'type':'string'}},('submissionId',))


class BusinessTools:
    def __init__(self, *, request_id, messages, store, viewer, cookie, editor, worker, contract,
                 cache, master, release, sales_settings, attachments, prior, recognition_socket=None, conversation_memory=None):
        self.conversation_memory = conversation_memory
        self.request_id, self.messages, self.store = request_id, messages, store
        self.viewer, self.cookie, self.editor = viewer or {}, cookie, editor
        self.worker, self.contract = worker, contract
        self.cache, self.master = cache, master
        self.release, self.sales_settings = release, sales_settings
        self.recognition_socket = recognition_socket
        self.attachments, self.prior = list(attachments), prior
        self.results, self.trace, self.receipts = {}, [], []
        self.calls = {}
        self.schemas = [PRODUCT_TOOL,NAME_TOOL,CONTEXT_TOOL,CASE_SEARCH_TOOL]
        self.schemas.append(CELLS_TOOL)
        if conversation_memory:
            self.schemas.extend([MEMORY_TOOL,CONFIRM_TOOL,RESTORE_TOOL])
        if recognition_socket:
            self.schemas.append(RECOGNITION_TOOL)
        if contract:
            self.schemas.append(CONTRACT_TOOL)
        if cookie:
            self.schemas.extend([WORKBENCH_READ_TOOL,SUBMISSION_TOOL])
        from chat_sales_tools import SOURCE_TOOL
        self.schemas.append(SOURCE_TOOL)
        if sales_settings and release:
            from chat_sales_tools import SALES_TOOL
            self.schemas.append(SALES_TOOL)
        if may_revise(editor,self.viewer):
            self.schemas.append(CASE_REVISE_TOOL)

    def context(self, offset=0, limit=25):
        validate_page(offset,limit)
        snapshot = product_snapshot(self.results,self.attachments,previous=self.prior) or {}
        return {'counts':snapshot.get('counts'),'lastQuery':snapshot.get('lastQuery'),'lastSales':snapshot.get('lastSales'),
                'headers':snapshot.get('headers',[]),
                'productRows':[{**row,'productIndex':i} for i,row in enumerate(snapshot.get('rawRows',[])[offset:offset+limit],offset+1)],
                'page':page_info(len(snapshot.get('rawRows',[])),offset,limit),
                'previewOnly':True,'sourcePartial':snapshot.get('sourcePartial',False),
                'candidates':snapshot.get('items',[])[:12],
                'totalProductRows':snapshot.get('rawInputRows',0)}

    def execute(self, call):
        function = call.get('function') if isinstance(call,dict) else None
        name = function.get('name') if isinstance(function,dict) else None
        call_id = call.get('id') if isinstance(call,dict) else None
        signature = None
        try:
            if name not in {schema['function']['name'] for schema in self.schemas}:
                raise ValueError('Tool is unavailable')
            call_id,args = read_tool_arguments(call,name)
            signature = json.dumps({'name':name,'args':args},sort_keys=True,ensure_ascii=False)
            if name=='revise_confirmed_case' and signature in self.calls:
                return tool_summary(call_id,self.calls[signature])
            reply = self.dispatch(call,args)
            summary = json.loads(reply['content'])
        except (ValueError,TypeError,KeyError):
            summary = {'status':'invalid_arguments','message':'工具名称或参数格式不正确，请按可用工具定义修正后重试。'}
            reply = tool_summary(call_id,summary)
        except (CaseRevisionError,WorkbenchReadError,AttachmentError,IntakePreviewError) as exc:
            summary = {'status':'failed','code':exc.code,'message':exc.message}
            reply = tool_summary(call_id,summary)
        except ContextUnavailable:
            summary = {'status':'unavailable','code':'CONTEXT_UNAVAILABLE','message':'案例库暂不可读。'}
            reply = tool_summary(call_id,summary)
        self.receipts.append({'name':name,'result':summary})
        self.trace.append({'name':name,'status':summary.get('status','completed'),
                           'code':summary.get('code')})
        if name=='revise_confirmed_case' and signature is not None:
            self.calls[signature] = summary
        return reply

    def dispatch(self,call,args):
        name = call['function']['name']
        call_id = call['id']
        if name=='read_worksheet_cells':
            if (not isinstance(args,dict) or set(args)-{'column','header','firstRows','filename','sheet'}
                    or len({'column','header','firstRows'} & set(args))!=1
                    or any(not isinstance(v,str) or not 0<len(v)<=180 for k,v in args.items() if k!='firstRows')
                    or 'column' in args and not re.fullmatch('[A-Z]{1,3}',args['column'])
                    or 'firstRows' in args and (type(args['firstRows']) is not int or not 1<=args['firstRows']<=20)):
                raise ValueError('Invalid worksheet query')
            report=read_sheet_cells(self.attachments,self.conversation_memory,self.messages[-1]['content'],args)
            return tool_summary(call_id,{'worksheetText':report})
        if name=='read_conversation_memory':
            return tool_summary(call_id,self.conversation_memory.read(args))
        if name=='remember_conversation_confirmation':
            return tool_summary(call_id,self.conversation_memory.confirm(args))
        if name=='restore_saved_attachment':
            if not isinstance(args,dict) or set(args)!={'attachmentId'}:raise ValueError('Expected attachmentId')
            ref,context,receipt=self.conversation_memory.restore_attachment(args['attachmentId'])
            if not context:return tool_summary(call_id,{'status':'unreadable','attachment':ref,'receipt':receipt})
            self.attachments=[context];self.prior=None
            for field in ('productLookup','productSearch','salesQuery','recognition'):
                self.results.pop(field,None)
            return tool_summary(call_id,{'status':'restored','attachment':ref,'receipt':receipt,**self.context()})
        if name=='lookup_product_codes':
            rows = query_rows(self.attachments,self.messages)
            if not rows and self.prior:
                rows = self.prior.get('rawRows',[])
            result,reply = run_product_tool(call,rows,self.cache,self.master)
            self.results.pop('productSearch',None)
            self.results['productLookup'] = result
            return reply
        if name=='search_product_names':
            result,reply = run_name_tool(call,self.messages,self.master,self.cache)
            self.results.pop('productLookup',None)
            self.results['productSearch'] = result
            return reply
        if name=='check_product_sales_source':
            from chat_sales_tools import sales_source
            if not isinstance(args,dict) or set(args)!={'platformCode'}:
                raise ValueError('expected platform')
            return tool_summary(call_id,sales_source(args['platformCode'],self.sales_settings,self.release))
        if name=='query_product_sales':
            from chat_sales_tools import run_sales_tool
            result,reply = run_sales_tool(call,self.sales_settings,self.release)
            self.results['salesQuery'] = result
            self.results.setdefault('salesQueries',{})[result['platformCode']] = result
            return reply
        if name=='search_confirmed_cases':
            result = run_case_search(self.store,args)
        elif name=='revise_confirmed_case':
            command_id = str(uuid5(NAMESPACE_URL,self.request_id+json.dumps(args,sort_keys=True,ensure_ascii=False)))
            result = run_case_revision(self.editor,self.viewer,args,command_id)
            self.results.setdefault('caseRevisions',[]).append(result)
        elif name=='read_workbench':
            if not isinstance(args,dict) or set(args)!={'path','query'}:
                raise ValueError('Expected path and query')
            result = read_workbench(args['path'],args['query'],self.cookie)
        elif name=='read_submission_materials':
            if not isinstance(args,dict) or set(args)!={'submissionId'}:
                raise ValueError('Expected submissionId')
            record,uploads = load_submission(52670,self.cookie,args['submissionId'])
            self.attachments,receipts = prepare_attachments(self.worker,uploads,self.request_id+'-saved',include_documents=True)
            if self.conversation_memory:
                self.conversation_memory.capture_materials(uploads,self.attachments,receipts)
            self.prior = None
            for field in ('productLookup','productSearch','salesQuery','recognition'):
                self.results.pop(field,None)
            self.results.update(workbenchSubmission=record,attachments=receipts)
            result = {'status':'read','submission':record,'attachments':receipts,**self.context()}
        elif name=='recognize_activities':
            if not isinstance(args,dict) or set(args)!={'instructions'} or not isinstance(args['instructions'],str):
                raise ValueError('Expected recognition instructions')
            if not self.attachments or not all('document' in item for item in self.attachments):
                return tool_summary(call_id,{'status':'needs_material','message':'请先读取工作台提报原件或使用当前已上传附件。'})
            request = intake_request(self.request_id,self.attachments,args['instructions'],'chat-preview')
            preview,metadata = run_preview(self.recognition_socket,self.release,request)
            self.results['recognition'] = {'result':preview,'metadata':metadata}
            self.results['productLookup'] = lookup_recognized_products(self.cache,preview)
            result = {'status':'recognized','metadata':metadata,'candidates':[
                {**{key:value for key,value in group.items() if key!='productRows'},
                 'productRowCount':len(group['productRows']),'productRows':group['productRows'][:10]}
                for group in preview['candidates']]}
        elif name=='read_product_context':
            if not isinstance(args,dict) or set(args)-{'offset','limit'}:
                raise ValueError('Expected page arguments')
            result = self.context(args.get('offset',0),args.get('limit',25))
        elif name=='get_intake_contract':
            if args!={}:
                raise ValueError('Expected no arguments')
            result = self.contract
        else:
            raise ValueError('Unsupported tool')
        return tool_summary(call_id,result)
