"""Exact test-only schedule routes. Auth precedes readiness, objects and receipts."""
import json
import re
import sqlite3
from account_primitives import fail,iso
from schedule_storage import validate,board,schema_version
from schedule_domain import Domain,ACTIONS,dates,detail,snapshot,preview,bounded,current_author,IDENTITY_CONTRACT

PREFIX='/api/test/v1/schedule'
BASE='/boards/shared'
ID=r'[A-Za-z0-9_-]{1,128}'
ROUTES=[
 ('GET',r'/context',None),('GET',BASE,None),
 ('GET',BASE+r'/requirements/('+ID+r')',None),
 ('POST',BASE+r'/requirements','schedule.requirement.create'),
 ('PUT',BASE+r'/requirements/('+ID+r')','schedule.requirement.update'),
 ('POST',BASE+r'/requirements/('+ID+r')/move','schedule.requirement.move'),
 ('POST',BASE+r'/requirements/('+ID+r')/comments','schedule.comment.create'),
 ('POST',BASE+r'/requirements/('+ID+r')/comments/('+ID+r')/delete','schedule.comment.delete'),
 ('GET',BASE+r'/images/('+ID+r')',None),
 ('GET',r'/commands/('+ID+r')',None),
 ('GET',BASE+r'/reset-preview',None),('POST',BASE+r'/reset','schedule.reset')]

def safe_meta(clock):return {'mode':'test','dataOrigin':'local_schedule','complete':False,'generation':None,'boardRevision':None,'containsExamples':None,'fetchedAt':iso(clock())}
def meta(clock,b):return safe_meta(clock)|{k:b[k] for k in ['boardId','generation','boardRevision','containsExamples']}|{'complete':True}

def dispatch(handler,headers,path,q):
    auth=handler.server.accounts;clock=handler.server.clock;sid=handler.token();handler.meta=safe_meta(clock)
    try:
        with auth.tx() as db:
            _,actor=auth.valid(db,sid,True)
            # Legacy v1 authenticator has no administrator API and never grants a board.
            if not hasattr(auth,'require'):fail(403,'FORBIDDEN','当前身份没有排期管理权限')
            auth.require(db,actor,'accounts.manage');handler.trace_actor=actor['id']
            matches=[(method,re.fullmatch(pattern,path),action) for method,pattern,action in ROUTES if re.fullmatch(pattern,path)]
            if not matches:fail(404,'NOT_FOUND','排期路由不存在')
            selected=next((m for m in matches if m[0]==handler.command),None)
            if not selected:fail(405,'METHOD_NOT_ALLOWED','排期路由不支持此方法')
            method,match,action=selected;ids=match.groups()
            if path.startswith('/commands/'):
                if set(q)!={'action'} or q['action'] not in ACTIONS:fail(422,'INVALID_REQUEST','回执查询需要本模块已知action')
            elif q:fail(422,'INVALID_REQUEST','该接口不接受查询参数')
            b=validate(db);handler.meta=meta(clock,b)
            if method!='GET':
                auth.csrf(sid,handler.headers.get('X-CSRF-Token',''))
                body=handler.body(5*1024*1024 if action=='schedule.comment.create' else 64*1024)
                status,data,replay=Domain(clock).write(db,actor,method,PREFIX+path,action,handler.headers.get('Idempotency-Key'),body,ids,handler.request_id)
                handler.meta=meta(clock,board(db));handler.response_status=status
                if replay:headers['Idempotency-Replayed']='true'
                return bounded(data)
            if path=='/context':
                today,_,_=dates(clock);author=current_author(actor);enabled=schema_version(db)==2
                return {'contractVersion':'schedule/1.0','timezone':'Asia/Shanghai','serverNow':iso(clock()),'today':today.isoformat(),'defaultBoardId':'shared','preferredAuthor':author['displayName'],'commentIdentity':{'contractVersion':IDENTITY_CONTRACT,'enabled':enabled,'writeMode':'session_account' if enabled else 'unavailable','currentAuthor':author},'capabilities':{'actions':sorted(ACTIONS),'shared':True,'images':{'mediaType':'image/jpeg','maxImages':6,'maxBytesPerImage':512*1024,'maxTotalBytes':3*1024*1024,'maxDimension':1000},'snapshotLimitBytes':8*1024*1024},'metricPolicy':{'dueStatusScope':'open_only'}}
            if path==BASE:return snapshot(db,clock)
            if path==BASE+'/reset-preview':return preview(db)
            if path.startswith('/commands/'):
                row=db.execute('SELECT * FROM jt022_commands WHERE actor_id=? AND action=? AND idem_key=?',(actor['id'],q['action'],ids[0])).fetchone()
                if not row:return {'state':'not_found'}
                return bounded({'state':'committed','commandId':row['id'],'status':row['status'],'response':json.loads(row['response']),'recordedAt':row['recorded_at'],'generation':row['generation']})
            if '/images/' in path:
                row=db.execute("SELECT i.* FROM jt022_images i JOIN jt022_comments c ON c.id=i.comment_id JOIN jt022_requirements r ON r.id=c.requirement_id WHERE i.id=? AND i.board_id='shared' AND c.board_id='shared' AND r.board_id='shared'",(ids[0],)).fetchone()
                if not row:fail(404,'NOT_FOUND','图片不存在')
                handler.binary_response=(row['content'],{'Content-Type':'image/jpeg','Cross-Origin-Resource-Policy':'same-origin'})
                return None
            return detail(db,ids[0])
    except sqlite3.Error as error:
        handler.meta['complete']=False
        fail(503,'DATABASE_BUSY' if 'locked' in str(error).lower() or 'busy' in str(error).lower() else 'PERSISTENCE_UNAVAILABLE','排期持久化暂不可用，请核对原请求')
