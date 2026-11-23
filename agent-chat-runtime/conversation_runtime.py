"""Durable lifecycle for the existing Activity Agent conversation endpoint."""
from copy import deepcopy
from datetime import datetime, timezone
import json
from hashlib import sha256
import re
from time import monotonic
from uuid import uuid4, uuid5, NAMESPACE_URL

from chat_attachments import MAX_BYTES, MAX_FILES, prepare_attachments, read_uploads
from chat_workbench_records import requested_submission, load_submission
from conversation_store import ConversationConflict, ConversationNotFound, ConversationUnavailable
from product_receipts import product_snapshot

COMMAND = re.compile(r'[a-zA-Z0-9_-]{1,80}')
CONFIRM = re.compile(r'确认|记住|以后.*按|以.+为准|改成|更正|纠正|不对|应该是|属于|明确是')
NOT_ASSERTION = re.compile(r'是否|是不是|什么|多少|哪个|哪些|为何|怎么|能否|可否|吗|呢|请.*确认|确认一下|不要记|别记|不能记|不需要记|不确认|暂未确认|待确认|^(?:请)?(?:查询|查下|查看|查一查|检索|回顾|总结|列出|解释|告诉我|读一下|读取|回忆|检查|核实)')


def user_assertion(text):
    return not any(c in text for c in ('?', '？')) and not NOT_ASSERTION.search(text)


AFFIRM = re.compile(r'^(?:对|对的|是的|没错|正确|就按这个|按这个|这样对|这样就对了)[。！!\s]*$')


def now():
    return datetime.now(timezone.utc).isoformat().replace('+00:00','Z')


def transcript(state):
    messages=[]
    for turn in state.get('turns',[]):
        common={'commandId':turn['id'],'createdAt':turn.get('createdAt'),'status':turn['status']}
        messages.append({**common,'id':turn['id']+':user','role':'user','content':turn['question'],
                         'attachments':turn.get('attachments',[]),'error':turn.get('error')})
        reply=turn.get('response')
        if reply:
            messages.append({**common,'id':turn['id']+':assistant','role':'assistant','content':reply['reply'],
                             'modelLabel':reply.get('modelLabel'),'caseReferences':reply.get('caseReferences',[]),
                             'elapsedSeconds':turn.get('elapsedSeconds')})
    return messages


def view(record):
    state=record['state']; pending=next((t for t in reversed(state.get('turns',[])) if t['status']=='processing'),None)
    active=set(state.get('activeAttachmentIds',[r['id'] for r in state.get('attachments',[])]))
    return {key:record[key] for key in ('id','title','revision','createdAt','updatedAt')} | {
        'messages':transcript(state),'attachments':[r for r in state.get('attachments',[]) if r['id'] in active],
        'confirmations':state.get('confirmations',[]),'processing':bool(pending),
        'pendingCommandId':pending['id'] if pending else None,'pendingStartedAt':pending.get('startedAt') if pending else None}


def recover_record(server,actor,cid):
    record=server.conversations.load(actor,cid)
    state=deepcopy(record['state']); changed=False
    for turn in state.get('turns',[]):
        if turn['status']=='processing' and turn.get('instanceId')!=server.instance_id:
            turn.update(status='interrupted',error={'code':'CHAT_INTERRUPTED','message':'服务重启中断了上次处理；原消息和附件已保存，可重试。'})
            changed=True
    if changed:
        try:return server.conversations.save(actor,cid,record['revision'],state)
        except ConversationConflict:return server.conversations.load(actor,cid)
    return record


def recent_messages(state,current):
    # The full transcript remains in MySQL. This is only the initial model window.
    pairs=[]
    for t in state.get('turns',[]):
        if t['id']==current['id'] or t['status']!='completed':continue
        reply=t.get('response',{})
        pairs.append([{'role':'user','content':t['question'][:3000]},
                      {'role':'assistant','content':(reply.get('historyReply') or reply.get('reply',''))[:3000]}])
    selected=[{'role':'user','content':current['question']}]; size=len(current['question'])
    for pair in reversed(pairs):
        extra=sum(len(m['content']) for m in pair)
        if size+extra>7200 or len(selected)+2>9:break
        selected=pair+selected;size+=extra
    return selected


def capture_confirmation(state,turn):
    text=turn['question'].strip()
    if not user_assertion(text):return
    previous=next((t for t in reversed(state['turns']) if t['id']!=turn['id'] and t['status']=='completed'),None)
    context=None
    if AFFIRM.fullmatch(text):
        if previous:context=previous['response']['reply']
        else:return
    elif not CONFIRM.search(text):return
    source=turn['id']+':user'
    if any(c['sourceMessageId']==source for c in state.setdefault('confirmations',[])):return
    state['confirmations'].append({'id':uuid4().hex,'sourceMessageId':source,'sourceText':text,
                                  'text':text,'context':context,'createdAt':turn['createdAt'],
                                  'scope':'conversation','kind':'user_confirmation'})


class ConversationMemory:
    def __init__(self,server,actor,record):
        self.server,self.actor,self.record=server,actor,record
        self.state=record['state']

    def context(self):
        confirmations=self.state.get('confirmations',[])
        return {'conversationId':self.record['id'],'messageCount':len(transcript(self.state)),
                'currentUserMessageId':self.state['turns'][-1]['id']+':user' if self.state.get('turns') else None,
                'savedAttachmentCount':len(self.state.get('attachments',[])),
                'savedAttachments':self.state.get('attachments',[])[-20:],
                'confirmationCount':len(confirmations),
                'confirmations':[dict(c,text=c['text'][:1500],context=(c.get('context') or '')[:2000]) for c in confirmations[-8:]],
                'historyStored':True,'olderHistoryTool':'read_conversation_memory'}

    def read(self,args):
        if not isinstance(args,dict) or set(args)-{'query','offset','limit','messageId','charOffset'}:
            raise ValueError('Invalid memory query')
        messages=transcript(self.state)
        query=args.get('query','');offset=args.get('offset',0);limit=args.get('limit',10)
        if not isinstance(query,str) or len(query)>200 or type(offset) is not int or offset<0 or type(limit) is not int or not 1<=limit<=20:
            raise ValueError('Invalid memory page')
        if args.get('messageId'):
            found=next((m for m in messages if m['id']==args['messageId']),None)
            if not found:return {'status':'not_found'}
            pos=args.get('charOffset',0)
            if type(pos) is not int or pos<0:raise ValueError('Invalid text position')
            content=found['content'];end=pos+6000
            return {'status':'read','message':dict(found,content=content[pos:end]),'totalChars':len(content),
                    'nextCharOffset':end if end<len(content) else None}
        if query:
            words=[w for w in re.split(r'[\s,，、]+',query.casefold()) if w]
            messages=[m for m in messages if any(w in (m['content']+' '+ ' '.join(r['filename'] for r in m.get('attachments',[]))).casefold() for w in words)]
        selected=[dict(m,content=m['content'][:1500],contentTruncated=len(m['content'])>1500) for m in messages[offset:offset+limit]]
        return {'status':'read','totalMessages':len(messages),'messages':selected,
                'nextOffset':offset+len(selected) if offset+len(selected)<len(messages) else None,
                'confirmations':self.state.get('confirmations',[])[-10:]}

    def read_attachment(self, ref):
        if ref not in self.state.get('attachments',[]):
            raise ValueError('Attachment is not in this conversation')
        saved=self.server.conversation_files.load_parsed(self.actor,self.record['id'],ref)
        if saved is None:
            contexts,receipts=prepare_attachments(self.server.attachment_worker,
                [self.server.conversation_files.read_upload(self.actor,self.record['id'],ref)],
                'RESTORE-'+uuid4().hex,include_documents=True)
            context=contexts[0] if contexts else None;receipt=receipts[0]
            self.server.conversation_files.save_parsed(self.actor,self.record['id'],ref,context,receipt)
            return context,receipt
        return saved

    def restore_attachment(self, attachment_id):
        ref=next((r for r in self.state.get('attachments',[]) if r['id']==attachment_id),None)
        if not ref:raise ValueError('Attachment is not in this conversation')
        context,receipt=self.read_attachment(ref)
        if context:
            state=deepcopy(self.state)
            state['activeAttachmentIds']=[ref['id']]
            state['snapshot']=product_snapshot({},[context])
            self.record=self.server.conversations.save(self.actor,self.record['id'],self.record['revision'],state)
            self.state=self.record['state']
        return ref,context,receipt

    def confirm(self,args):
        if not isinstance(args,dict) or set(args)!={'sourceMessageId','text'}:raise ValueError('Expected confirmation source and text')
        text=args['text'];source=next((m for m in transcript(self.state) if m['id']==args['sourceMessageId'] and m['role']=='user'),None)
        if (not source or not isinstance(text,str) or not 1<=len(text)<=3000 or text not in source['content']
                or not user_assertion(source['content'])):
            return {'status':'needs_confirmation','message':'需引用用户实际确认的原文；不能把模型推断或附件文字当作用户确认。'}
        items=self.state.setdefault('confirmations',[])
        existing=next((c for c in items if c['sourceMessageId']==source['id'] and c['text']==text),None)
        if existing:return {'status':'saved','confirmation':existing}
        item={'id':uuid4().hex,'sourceMessageId':source['id'],'sourceText':source['content'],'text':text,
              'createdAt':now(),'scope':'conversation','kind':'user_statement'}
        items.append(item)
        try:
            saved=self.server.conversations.save(self.actor,self.record['id'],self.record['revision'],self.state)
        except BaseException:
            items.pop();raise
        self.record=saved;self.state=saved['state']
        return {'status':'saved','confirmation':item}

    def capture_materials(self,uploads,contexts,receipts):
        refs=self.server.conversation_files.save_uploads(self.actor,self.record['id'],uploads)
        ordered=read_uploads(uploads)
        by_source={(r['filename'],r['sha256']):r for r in refs}
        if len(ordered)!=len(receipts):raise ValueError('Material receipt count mismatch')
        for (filename,encoded,digest),receipt in zip(ordered,receipts):
            ref=by_source[(filename,digest)]
            context=next((item for item in contexts if item['filename']==filename and item.get('sha256')==digest),None)
            self.server.conversation_files.save_parsed(self.actor,self.record['id'],ref,context,receipt)
        existing={r['id']:r for r in self.state.get('attachments',[])};existing.update({r['id']:r for r in refs})
        self.state['attachments']=list(existing.values());self.state['activeAttachmentIds']=[r['id'] for r in refs]
        self.state['snapshot']=product_snapshot({},contexts)


def run_persistent_chat(server,viewer,body,cookie):
    # Import here to avoid a cycle with the existing HTTP module.
    from agent_chat import ChatError,read_messages
    actor=viewer['id']; modern='conversationId' in body or 'message' in body
    if modern:
        if set(body)-{'conversationId','commandId','expectedRevision','message','attachments','attachmentIds'}:
            raise ChatError(400,'INVALID_REQUEST','会话请求字段不正确。')
        question=body.get('message');cmd=body.get('commandId');expected=body.get('expectedRevision')
        if (not isinstance(question,str) or not 1<=len(question.strip())<=3000 or not isinstance(cmd,str)
                or not COMMAND.fullmatch(cmd) or type(expected) is not int or expected<0):
            raise ChatError(400,'INVALID_REQUEST','请检查消息与会话版本。')
        record=recover_record(server,actor,body.get('conversationId'))
    else:
        legacy=read_messages(body);question=legacy[-1]['content'];cmd=uuid4().hex
        receipt=body.get('productReceipt')
        try:record=recover_record(server,actor,receipt) if receipt else None
        except (ConversationNotFound,ValueError):record=None
        if record is None:
            state={'turns':[],'attachments':[],'confirmations':[],'snapshot':None}
            for i in range(0,len(legacy)-1,2):
                state['turns'].append({'id':uuid4().hex,'question':legacy[i]['content'],'status':'completed',
                                       'createdAt':now(),'response':{'reply':legacy[i+1]['content']},'imported':True})
            record=server.conversations.create(actor,state=state)
        expected=record['revision']
    state=deepcopy(record['state']);question=question.strip();cid=record['id']
    existing=next((t for t in state.get('turns',[]) if t['id']==cmd),None)
    by_id={r['id']:r for r in state.get('attachments',[])}
    requested=body.get('attachmentIds',state.get('activeAttachmentIds',list(by_id)))
    if not isinstance(requested,list) or len(requested)>MAX_FILES or any(not isinstance(i,str) or i not in by_id for i in requested):
        raise ChatError(400,'ATTACHMENT_REFERENCE_INVALID','保存的附件引用无效，请刷新会话。')
    refs=[by_id[i] for i in dict.fromkeys(requested)]
    uploads=body.get('attachments',[])
    verified=read_uploads(uploads)
    inputs=list(dict.fromkeys([(r['filename'],r['sha256']) for r in refs]+[(name,digest) for name,encoded,digest in verified]))
    request_hash=sha256(json.dumps({'question':question,'files':inputs},ensure_ascii=False).encode()).hexdigest()
    if existing and (existing['question']!=question or existing.get('requestHash')!=request_hash):
        raise ChatError(409,'COMMAND_CONFLICT','同一发送编号的消息或附件已变化，请作为新消息发送。')
    if existing and existing['status']=='completed':
        return {**existing['response'],'conversation':view(record),'productReceipt':cid}
    if any(t['status']=='processing' for t in state.get('turns',[])):
        error=ChatError(409,'CONVERSATION_BUSY','此会话正在处理，已保存的内容可刷新查看。');error.conversation=view(record);raise error
    if expected!=record['revision']:
        error=ChatError(409,'CONVERSATION_CHANGED','会话已有更新，已恢复最新记录，请再发送。');error.conversation=view(record);raise error
    newrefs=server.conversation_files.save_uploads(actor,cid,uploads) if uploads else []
    refs=list({r['id']:r for r in refs+newrefs}.values())
    if len(refs)>MAX_FILES or sum(r['size'] for r in refs)>MAX_BYTES:
        raise ChatError(413,'ATTACHMENT_TOO_LARGE','当前引用附件最多3个，合计不超过50 MB。')
    by_id.update({r['id']:r for r in newrefs});state['attachments']=list(by_id.values())
    active=[r['id'] for r in refs]
    if state.get('activeAttachmentIds',[])!=active:state['snapshot']=None
    state['activeAttachmentIds']=active
    if existing:
        turn=next(t for t in state['turns'] if t['id']==cmd)
        turn.pop('error',None)
    else:
        turn={'id':cmd,'question':question,'createdAt':now()};state.setdefault('turns',[]).append(turn)
    turn.update(status='processing',startedAt=now(),instanceId=server.instance_id,attachments=refs,requestHash=request_hash)
    capture_confirmation(state,turn)
    record=server.conversations.save(actor,cid,expected,state,title=question[:80] if record['title']=='新对话' else None)
    memory=ConversationMemory(server,actor,record);state=memory.state
    turn=next(t for t in state['turns'] if t['id']==cmd)
    contexts=[];receipts=[];start=monotonic()
    try:
        for ref in refs:
            saved=server.conversation_files.load_parsed(actor,cid,ref)
            if saved is None:
                content,details=prepare_attachments(server.attachment_worker,[server.conversation_files.read_upload(actor,cid,ref)],cmd,include_documents=True)
                context=content[0] if content else None;receipt=details[0]
                server.conversation_files.save_parsed(actor,cid,ref,context,receipt)
            else:context,receipt=saved
            if context:contexts.append(context)
            receipts.append(receipt)
        options={}
        submission_id=requested_submission(question)
        if submission_id:
            workbench,raw=load_submission(52670,cookie,submission_id)
            contexts,receipts=prepare_attachments(server.attachment_worker,raw,cmd,include_documents=True)
            memory.capture_materials(raw,contexts,receipts);state['snapshot']=None
            options['workbench_record']=workbench
        if refs and not contexts and not submission_id:
            raise ChatError(422,'ATTACHMENT_NO_TEXT','附件原件已保存，但暂时没有可供分析的内容。请查看附件读取结果。')
        data=server.chat(server.settings,server.store,recent_messages(state,turn),attachments=contexts,attachment_results=receipts,
            prior_product_snapshot=state.get('snapshot'),tool_viewer=viewer,tool_cookie=cookie,
            tool_editor=server.case_editor,attachment_worker=server.attachment_worker,sales_settings=server.sales_settings,
            case_revision_enabled='accounts.manage' in viewer.get('permissions',[]),conversation_memory=memory,
            request_id='CHAT-'+uuid5(NAMESPACE_URL,cid+cmd).hex,**options)
        state=memory.state;turn=next(t for t in state['turns'] if t['id']==cmd)
        snapshot=data.pop('_receiptSnapshot',None)
        state['snapshot']=snapshot if snapshot is not None else product_snapshot(data,contexts,previous=state.get('snapshot'))
        if isinstance(data.get('productLookup'),dict):data['productLookup'].pop('_sourceRows',None)
        turn.update(status='completed',response=data,elapsedSeconds=int(monotonic()-start),finishedAt=now())
        record=server.conversations.save(actor,cid,memory.record['revision'],state)
        return {**data,'conversation':view(record),'productReceipt':cid}
    except Exception as exc:
        state=memory.state;turn=next(t for t in state['turns'] if t['id']==cmd)
        turn.update(status='failed',error={'code':getattr(exc,'code','CHAT_FAILED'),
            'message':getattr(exc,'message','本次处理未完成，消息和附件已保存，可重试。')},elapsedSeconds=int(monotonic()-start))
        state['snapshot']=product_snapshot({},contexts,previous=state.get('snapshot'))
        try:record=server.conversations.save(actor,cid,memory.record['revision'],state)
        except (ConversationConflict,ConversationUnavailable):
            record=server.conversations.load(actor,cid)
        if not hasattr(exc,'status'):
            exc=ChatError(503,'CHAT_FAILED','本次处理未完成，已保存的内容可刷新查看。')
        exc.conversation=view(record)
        raise exc
