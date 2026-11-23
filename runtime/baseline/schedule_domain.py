"""Deterministic shared schedule rules. All operations use caller's auth transaction."""
import base64
import binascii
from datetime import date,datetime,timedelta,timezone
import hashlib
import json
import re
from zoneinfo import ZoneInfo,ZoneInfoNotFoundError
from account_primitives import fail,iso
from b_state import canon,fingerprint,new_id
from schedule_storage import board,schema_version

STATUSES=('review','doing','done')
FIELDS={'title','priority','owner','start','due','status','progress','note'}
ACTIONS={'schedule.requirement.create','schedule.requirement.update','schedule.requirement.move','schedule.comment.create','schedule.comment.delete','schedule.reset'}
EXAMPLE_VERSION='schedule-examples/1'
SNAPSHOT_LIMIT=8*1024*1024

def exact(value,keys):
    if not isinstance(value,dict) or set(value)!=set(keys):fail(422,'INVALID_REQUEST','字段缺失或未知')
def integer(v,minimum=0):
    if type(v) is not int or v<minimum:fail(422,'INVALID_REQUEST','版本或数量必须是非负整数')
    return v
def text(v,maxlen,nullable=False):
    if v is None and nullable:return None
    if not isinstance(v,str) or len(v)>maxlen:fail(422,'INVALID_REQUEST','文本类型或长度无效')
    try:v.encode('utf-8')
    except UnicodeError:fail(422,'INVALID_REQUEST','文本编码无效')
    return v

def calendar(v):
    if v is None:return None
    if not isinstance(v,str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}',v):fail(422,'INVALID_REQUEST','日期必须为YYYY-MM-DD或null')
    try:date.fromisoformat(v)
    except ValueError:fail(422,'INVALID_REQUEST','日期无效')
    return v

def dates(clock):
    try:today=datetime.fromtimestamp(clock(),ZoneInfo('Asia/Shanghai')).date()
    except (ZoneInfoNotFoundError,ValueError,OverflowError):fail(503,'SCHEDULE_NOT_READY','北京时间不可获取')
    start=today-timedelta(days=today.weekday())
    return today,start,start+timedelta(days=6)

def validate_fields(value):
    exact(value,FIELDS);v=value.copy();v['title']=text(v['title'],200).strip()
    if not v['title']:fail(422,'INVALID_REQUEST','需求名称必填',{'fieldErrors':{'title':'需求名称不能为空'}})
    if not isinstance(v['priority'],str) or v['priority'] not in ['P0','P1','P2'] or not isinstance(v['status'],str) or v['status'] not in STATUSES:fail(422,'INVALID_REQUEST','优先级或状态无效')
    v['owner']=(text(v['owner'],100,True) or '').strip() or None
    v['note']=text(v['note'],10000,True)
    calendar(v['start']);calendar(v['due'])
    if v['start'] and v['due'] and v['start']>v['due']:fail(422,'DATE_RANGE_INVALID','开始日期不能晚于截止日期')
    if type(v['progress']) is not int or not 0<=v['progress']<=100:fail(422,'INVALID_REQUEST','进度须为0至100的整数')
    return v

def transition(v,old,today):
    v=v.copy()
    if v['status']=='done':v['progress']=100;v['doneAt']=(old or {}).get('doneAt') or today.isoformat()
    else:v['doneAt']=None
    return v

def requirement(db,rid):
    row=db.execute("SELECT * FROM jt022_requirements WHERE id=? AND board_id='shared'",(rid,)).fetchone()
    if not row:fail(404,'NOT_FOUND','需求不存在')
    v=json.loads(row['body']);v.update(id=row['id'],revision=row['revision'],status=row['status'],position=row['position'])
    return v

def summary(v,count=0):return {k:v.get(k) for k in ['id','revision','entryOrigin','title','priority','owner','start','due','status','progress','doneAt']}|{'commentCount':count}
def detail_requirement(db,v):
    n=db.execute('SELECT COUNT(*) FROM jt022_comments WHERE requirement_id=?',(v['id'],)).fetchone()[0]
    return summary(v,n)|{k:v.get(k) for k in ['note','createdAt','updatedAt']}

def image_meta(row):return {'imageId':row['id'],'mediaType':row['media_type'],'sha256':row['sha256'],'byteSize':row['byte_size'],'width':row['width'],'height':row['height'],'url':'/api/test/v1/schedule/boards/shared/images/'+row['id']}
IDENTITY_CONTRACT='comment-identity/2.0'
IDENTITY_KEYS={'contractVersion','kind','accountId','nameSnapshot','accountLabelSnapshot','profileVersionSnapshot','displayName','displaySource'}

def current_author(actor):
    name=actor['name'];label=actor['username'];version=actor['profile_version'];aid=actor['id']
    if name is not None and not isinstance(name,str) or not isinstance(label,str) or not label.strip() or type(version) is not int or version<1 or not isinstance(aid,str) or not aid:
        fail(503,'COMMENT_IDENTITY_UNAVAILABLE','当前账号显示资料不可用，评论未保存')
    name=name.strip() if name else None;name=name or None
    return {'accountId':aid,'name':name,'accountLabel':label,'profileVersion':version,'displayName':name or label,'displaySource':'name' if name else 'account_label'}

def author_snapshot(actor):
    a=current_author(actor)
    return {'contractVersion':IDENTITY_CONTRACT,'kind':'account','accountId':a['accountId'],'nameSnapshot':a['name'],'accountLabelSnapshot':a['accountLabel'],'profileVersionSnapshot':a['profileVersion'],'displayName':a['displayName'],'displaySource':'name_snapshot' if a['name'] else 'account_label_snapshot'}

def comment_identity(row):
    raw=row['author_identity_json'] if 'author_identity_json' in row.keys() else None
    if raw is None:
        # Never join today's account name into an old free-signature record.
        return {'contractVersion':IDENTITY_CONTRACT,'kind':'legacy_signature','accountId':row['actor_id'],'nameSnapshot':None,'accountLabelSnapshot':None,'profileVersionSnapshot':None,'displayName':row['author'],'displaySource':'legacy_signature'}
    def invalid():fail(503,'COMMENT_IDENTITY_INVALID','评论身份快照异常，请联系原Owner核对')
    try:v=json.loads(raw)
    except (ValueError,TypeError):invalid()
    if not isinstance(v,dict) or set(v)!=IDENTITY_KEYS or v['contractVersion']!=IDENTITY_CONTRACT:invalid()
    name=v['nameSnapshot'];label=v['accountLabelSnapshot'];version=v['profileVersionSnapshot']
    if not isinstance(v['displayName'],str) or v['displayName']!=row['author']:invalid()
    if v['kind']=='account':
        if not isinstance(v['accountId'],str) or not v['accountId'] or v['accountId']!=row['actor_id'] or not isinstance(label,str) or not label.strip() or type(version) is not int or version<1:invalid()
        if name is not None and (not isinstance(name,str) or not name or name!=name.strip()):invalid()
        if v['displayName']!=(name or label) or v['displaySource']!=('name_snapshot' if name else 'account_label_snapshot'):invalid()
    elif v['kind'] in ['legacy_signature','example']:
        if any(x is not None for x in [name,label,version]):invalid()
        if v['kind']=='example':
            if v['accountId'] is not None or v['displaySource']!='example_label' or not v['displayName'].strip():invalid()
        elif v['accountId'] not in [None,row['actor_id']] or v['displaySource']!='legacy_signature':invalid()
    else:invalid()
    return v

def comment_view(db,row):return {'id':row['id'],'author':row['author'],'text':row['text'],'time':row['time'],'authorIdentity':comment_identity(row),'images':[image_meta(i) for i in db.execute('SELECT id,media_type,sha256,byte_size,width,height FROM jt022_images WHERE comment_id=? ORDER BY ordinal',(row['id'],))]}

def bounded(value):
    if len(json.dumps(value,ensure_ascii=False,allow_nan=False).encode())+2048>SNAPSHOT_LIMIT:fail(413,'SNAPSHOT_LIMIT','排期快照超过完整响应预算')
    return value

def snapshot(db,clock):
    b=board(db);today,start,end=dates(clock)
    counts={r[0]:r[1] for r in db.execute('SELECT requirement_id,COUNT(*) FROM jt022_comments GROUP BY requirement_id')}
    raw=[json.loads(r['body'])|{'id':r['id'],'revision':r['revision'],'status':r['status'],'position':r['position']} for r in db.execute("SELECT * FROM jt022_requirements WHERE board_id='shared' ORDER BY position,id")]
    columns={s:{'items':[summary(v,counts.get(v['id'],0)) for v in raw if v['status']==s]} for s in STATUSES}
    for c in columns.values():c['total']=len(c['items'])
    by_id={v['id']:summary(v,counts.get(v['id'],0)) for v in raw}
    doing=sorted([v for v in raw if v['status']=='doing'],key=lambda v:(v.get('due') is None,v.get('due') or '',v['position'],v['id']))
    completed=sorted([v for v in raw if v['status']=='done' and v.get('doneAt') and start.isoformat()<=v['doneAt']<=end.isoformat()],key=lambda v:(v['doneAt'],v['id']),reverse=True)
    open_due=[v for v in raw if v['status']!='done' and v.get('due')]
    overdue=sorted([v for v in open_due if v['due']<today.isoformat()],key=lambda v:(v['due'],v['position'],v['id']))
    soon=sorted([v for v in open_due if today.isoformat()<=v['due']<=(today+timedelta(days=6)).isoformat()],key=lambda v:(v['due'],v['position'],v['id']))
    return bounded(b|{'timezone':'Asia/Shanghai','today':today.isoformat(),'weekStart':start.isoformat(),'weekEnd':end.isoformat(),'metricPolicy':{'dueStatusScope':'open_only'},'complete':True,'stats':{'doing':{'count':len(doing),'total':len(raw)},'completedThisWeek':len(completed),'dueSoonOrOverdue':len(overdue)+len(soon),'p0Doing':sum(v['priority']=='P0' for v in doing),'doneTotal':columns['done']['total']},'columns':columns,'doingItems':[by_id[v['id']] for v in doing],'completedThisWeekItems':[by_id[v['id']] for v in completed],'reminders':{name:{'state':'ready','items':[by_id[v['id']] for v in rows]} for name,rows in [('overdue',overdue),('dueNext7Days',soon),('reviewQueue',[v for v in raw if v['status']=='review'])]},'ownerOptions':sorted({v['owner'] for v in raw if v.get('owner')})})

def detail(db,rid):
    v=requirement(db,rid);b=board(db)
    return bounded({k:b[k] for k in ['boardId','generation','boardRevision']}|{'requirement':detail_requirement(db,v),'comments':[comment_view(db,row) for row in db.execute('SELECT * FROM jt022_comments WHERE requirement_id=? ORDER BY time DESC,id DESC',(rid,))]})

def jpeg(content):
    # Bounded structural JPEG parser, not a full entropy decoder. Require a frame,
    # tables, scan and exact EOI; a forged SOF dimension header alone is insufficient.
    if len(content)>512*1024:fail(413,'IMAGE_LIMIT','单图超过512KiB')
    if not content.startswith(b'\xff\xd8') or not content.endswith(b'\xff\xd9'):fail(415,'UNSUPPORTED_IMAGE','需要完整JPEG图片')
    pos=2;frame=None;tables=set();scans=0;entropy=0
    while pos<len(content):
        if content[pos]!=255:fail(415,'UNSUPPORTED_IMAGE','JPEG标记无效')
        while pos<len(content) and content[pos]==255:pos+=1
        if pos>=len(content):fail(415,'UNSUPPORTED_IMAGE','JPEG不完整')
        marker=content[pos];pos+=1
        if marker==217:
            if pos!=len(content) or frame is None or not scans or not entropy or not {219,196}<=tables:fail(415,'UNSUPPORTED_IMAGE','JPEG结构不完整')
            return frame
        if marker in [0,216] or 208<=marker<=215:fail(415,'UNSUPPORTED_IMAGE','JPEG标记无效')
        if pos+2>len(content):fail(415,'UNSUPPORTED_IMAGE','JPEG不完整')
        length=int.from_bytes(content[pos:pos+2],'big')
        if length<2 or pos+length>len(content):fail(415,'UNSUPPORTED_IMAGE','JPEG段长度无效')
        data=content[pos+2:pos+length];pos+=length
        if marker in [192,193,194]:
            if frame or len(data)<6 or data[0]!=8:fail(415,'UNSUPPORTED_IMAGE','JPEG帧无效')
            height=int.from_bytes(data[1:3],'big');width=int.from_bytes(data[3:5],'big');components=data[5]
            if components not in [1,3] or len(data)!=6+components*3:fail(415,'UNSUPPORTED_IMAGE','JPEG颜色分量无效')
            if min(width,height)<1 or max(width,height)>1000:fail(413,'IMAGE_LIMIT','图片最长边须不超过1000px')
            frame=(width,height)
        elif 192<=marker<=207 and marker not in [196,200,204]:fail(415,'UNSUPPORTED_IMAGE','JPEG编码不支持')
        if marker in [219,196]:
            if not data:fail(415,'UNSUPPORTED_IMAGE','JPEG表无效')
            tables.add(marker)
        if marker==218:
            if frame is None or len(data)<4 or data[0] not in [1,3] or len(data)!=1+2*data[0]+3:fail(415,'UNSUPPORTED_IMAGE','JPEG扫描无效')
            scans+=1
            while pos<len(content):
                if content[pos]!=255:entropy+=1;pos+=1;continue
                if pos+1>=len(content):fail(415,'UNSUPPORTED_IMAGE','JPEG扫描不完整')
                nxt=content[pos+1]
                if nxt==0 or 208<=nxt<=215:entropy+=1;pos+=2;continue
                if nxt==255:pos+=1;continue
                break
    fail(415,'UNSUPPORTED_IMAGE','JPEG缺少结束标记')

def images_input(images):
    if not isinstance(images,list):fail(422,'INVALID_REQUEST','images必须为数组')
    if len(images)>6:fail(413,'IMAGE_LIMIT','每评论最多6张图片')
    result=[];total=0
    for v in images:
        exact(v,{'mediaType','contentBase64'})
        if v['mediaType']!='image/jpeg':fail(415,'UNSUPPORTED_IMAGE','只接受JPEG')
        if not isinstance(v['contentBase64'],str):fail(422,'INVALID_REQUEST','图片须为base64文本')
        if len(v['contentBase64'])>700000:fail(413,'IMAGE_LIMIT','单图超限')
        try:raw=base64.b64decode(v['contentBase64'],validate=True)
        except (ValueError,binascii.Error):fail(415,'UNSUPPORTED_IMAGE','图片base64无效')
        if base64.b64encode(raw).decode()!=v['contentBase64']:fail(415,'UNSUPPORTED_IMAGE','图片base64不是规范编码')
        width,height=jpeg(raw);total+=len(raw)
        if total>3*1024*1024:fail(413,'IMAGE_LIMIT','评论总图超过3MiB')
        result.append({'raw':raw,'sha256':hashlib.sha256(raw).hexdigest(),'width':width,'height':height})
    return result

def ordered(db,status):return [r[0] for r in db.execute("SELECT id FROM jt022_requirements WHERE board_id='shared' AND status=? ORDER BY position,id",(status,))]
def reorder(db,status,ids):
    # Reserve disjoint negative positions before assigning dense final positions.
    for i,rid in enumerate(ids):db.execute('UPDATE jt022_requirements SET position=? WHERE id=?',(-i-1,rid))
    for i,rid in enumerate(ids):db.execute('UPDATE jt022_requirements SET position=?,status=? WHERE id=?',(i,status,rid))
def bump(db,columns=()):
    b=board(db)
    for s in set(columns):b['orderRevisions'][s]+=1
    db.execute("UPDATE jt022_boards SET revision=revision+1,order_revisions=? WHERE id='shared'",(canon(b['orderRevisions']),))
    return board(db)
def save(db,v):
    data={k:x for k,x in v.items() if k not in ['id','revision','status','position']}
    db.execute('UPDATE jt022_requirements SET revision=?,status=?,position=?,body=? WHERE id=?',(v['revision'],v['status'],v['position'],canon(data),v['id']))
def counts(db):return {name:db.execute('SELECT COUNT(*) FROM jt022_'+name).fetchone()[0] for name in ['requirements','comments','images']}
def preview(db):return {k:v for k,v in board(db).items() if k in ['generation','boardRevision']}|{'counts':counts(db),'exampleVersion':EXAMPLE_VERSION}

def check_req(v,body):
    integer(body['expectedRequirementRevision'])
    if v['revision']!=body['expectedRequirementRevision']:fail(409,'REQUIREMENT_VERSION_CONFLICT','需求已更新，请核对',{'requirementId':v['id'],'currentRevision':v['revision']})

class Domain:
    def __init__(self,clock):self.clock=clock
    def write(self,db,actor,method,path,action,key,body,ids,request_id):
        if action not in ACTIONS:fail(422,'INVALID_REQUEST','未知动作')
        if not isinstance(key,str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,128}',key):fail(422,'INVALID_REQUEST','需要安全的稳定Idempotency-Key')
        if not isinstance(body,dict):fail(422,'INVALID_REQUEST','正文必须是对象')
        image_hashes=[v['sha256'] for v in images_input(body.get('images'))] if action=='schedule.comment.create' and 'images' in body else []
        digest=fingerprint({'method':method,'path':path,'body':body,'imageSha256':image_hashes})
        old=db.execute('SELECT * FROM jt022_commands WHERE actor_id=? AND action=? AND idem_key=?',(actor['id'],action,key)).fetchone()
        if old:
            if old['digest']!=digest:fail(409,'IDEMPOTENCY_CONFLICT','同键正文或目标不一致')
            return old['status'],json.loads(old['response']),True
        b=board(db)
        integer(body.get('expectedGeneration'),1)
        if body['expectedGeneration']!=b['generation']:fail(409,'GENERATION_CONFLICT','排期已重置，请重新读取',{'currentGeneration':b['generation']})
        before={k:b[k] for k in ['generation','boardRevision']};command_id=new_id('SCC')
        status,result=self.perform(db,actor,action,body,ids)
        result=result|{'commandId':command_id}
        b=board(db)
        db.execute('INSERT INTO jt022_commands VALUES(?,?,?,?,?,?,?,?,?)',(command_id,actor['id'],action,key,digest,result['generation'],status,canon(result),iso(self.clock())))
        db.execute('INSERT INTO jt022_events VALUES(?,?,?,?,?,?,?,?,?)',(new_id('SCE'),b['generation'],b['boardRevision'],actor['id'],action,ids[0] if ids else None,request_id,iso(self.clock()),canon({'before':before,'after':{k:b[k] for k in ['generation','boardRevision']}})))
        return status,result,False
    def perform(self,db,actor,action,body,ids):
        now=iso(self.clock());today,_,_=dates(self.clock)
        if action in ['schedule.requirement.create','schedule.requirement.update']:
            creating=action.endswith('.create');exact(body,{'expectedGeneration','fields'} if creating else {'expectedGeneration','expectedRequirementRevision','fields'})
            fields=validate_fields(body['fields']);old=None if creating else requirement(db,ids[0])
            if old:check_req(old,body)
            fields=transition(fields,old,today)
            if creating:
                v=fields|{'id':new_id('SCH'),'revision':1,'entryOrigin':'manual','position':len(ordered(db,fields['status'])),'createdAt':now,'updatedAt':now,'createdBy':actor['id'],'updatedBy':actor['id']}
                db.execute('INSERT INTO jt022_requirements VALUES(?,?,?,?,?,?)',(v['id'],'shared',v['revision'],v['status'],v['position'],canon({k:x for k,x in v.items() if k not in ['id','revision','status','position']})))
                b=bump(db,[v['status']])
            else:
                v=old|fields;v['revision']+=1;v['updatedAt']=now;v['updatedBy']=actor['id'];columns=[]
                if v['status']!=old['status']:
                    v['position']=len(ordered(db,v['status']));columns=[old['status'],v['status']]
                save(db,v)
                if columns:reorder(db,old['status'],ordered(db,old['status']))
                b=bump(db,columns)
            return (201 if creating else 200),b|{'requirement':detail_requirement(db,v)}
        if action=='schedule.requirement.move':
            exact(body,{'expectedGeneration','expectedRequirementRevision','expectedOrderRevisions','targetStatus','anchorId','placement'})
            v=requirement(db,ids[0]);check_req(v,body);target=body['targetStatus'];source=v['status']
            if not isinstance(target,str) or target not in STATUSES or body['placement'] not in ['before','after','end']:fail(422,'INVALID_MOVE','目标列或位置无效')
            expected=body['expectedOrderRevisions'];exact(expected,{source,target})
            b=board(db)
            for s,r in expected.items():
                integer(r)
                if r!=b['orderRevisions'][s]:fail(409,'ORDER_VERSION_CONFLICT','列顺序已变化',{'currentOrderRevisions':{s:b['orderRevisions'][s] for s in expected}})
            target_ids=[rid for rid in ordered(db,target) if rid!=v['id']]
            if body['placement']=='end':
                if body['anchorId'] is not None:fail(422,'INVALID_MOVE','末尾放置不能带锚点')
                target_ids.append(v['id'])
            else:
                if body['anchorId'] not in target_ids:fail(422,'INVALID_MOVE','锚点必须属于目标列其他需求')
                at=target_ids.index(body['anchorId'])+(body['placement']=='after');target_ids.insert(at,v['id'])
            if source!=target:
                # Vacate source and append destination first, then reorder both safely.
                v=transition(v|{'status':target},v,today);v['position']=len(ordered(db,target));save(db,v)
                reorder(db,source,ordered(db,source))
            reorder(db,target,target_ids);v['position']=target_ids.index(v['id']);v['revision']+=1;v['updatedAt']=now;v['updatedBy']=actor['id'];save(db,v)
            b=bump(db,[source,target]);return 200,b|{'requirement':detail_requirement(db,v),'affectedColumns':{s:ordered(db,s) for s in {source,target}}}
        if action=='schedule.comment.create':
            if schema_version(db)!=2:fail(503,'COMMENT_IDENTITY_MIGRATION_REQUIRED','评论身份待离线升级，评论未保存')
            readonly={'actorId','actor_id','authorIdentity','nameSnapshot','accountId','accountLabelSnapshot','profileVersionSnapshot','displayName','displaySource','kind'}
            if set(body)&readonly:fail(422,'COMMENT_IDENTITY_READ_ONLY','评论身份由服务器决定')
            exact(body,{'expectedGeneration','text','images'}|({'author'} if 'author' in body else set()))
            if 'author' in body:text(body['author'],100)
            requirement(db,ids[0]);identity=author_snapshot(actor);author=identity['displayName'];message=text(body['text'],10000);images=images_input(body['images'])
            if not message.strip() and not images:fail(422,'EMPTY_COMMENT','评论正文或图片至少一项')
            cid=new_id('SCM');db.execute('INSERT INTO jt022_comments(id,board_id,requirement_id,actor_id,author,text,time,author_identity_json) VALUES(?,?,?,?,?,?,?,?)',(cid,'shared',ids[0],actor['id'],author,message,now,canon(identity)))
            for ordinal,image in enumerate(images):
                db.execute('INSERT INTO jt022_images VALUES(?,?,?,?,?,?,?,?,?,?)',(new_id('SCI'),'shared',cid,ordinal,'image/jpeg',image['sha256'],len(image['raw']),image['width'],image['height'],image['raw']))
            b=bump(db);return 201,{k:b[k] for k in ['generation','boardRevision']}|{'comment':comment_view(db,db.execute('SELECT * FROM jt022_comments WHERE id=?',(cid,)).fetchone()),**({'legacyAuthorIgnored':True} if 'author' in body else {})}
        if action=='schedule.comment.delete':
            exact(body,{'expectedGeneration'});requirement(db,ids[0]);row=db.execute('SELECT * FROM jt022_comments WHERE id=? AND requirement_id=?',(ids[1],ids[0])).fetchone()
            if not row:fail(404,'NOT_FOUND','评论不存在')
            db.execute('DELETE FROM jt022_comments WHERE id=?',(ids[1],));b=bump(db);return 200,{k:b[k] for k in ['generation','boardRevision']}|{'deletedCommentId':ids[1]}
        if action=='schedule.reset':
            exact(body,{'expectedGeneration','expectedBoardRevision','expectedCounts','exampleVersion','confirm'});integer(body['expectedBoardRevision']);exact(body['expectedCounts'],{'requirements','comments','images'})
            for n in body['expectedCounts'].values():integer(n)
            if body['exampleVersion']!=EXAMPLE_VERSION or body['confirm']!='replace_shared_with_examples':fail(422,'INVALID_REQUEST','须明确确认替换共享板为指定版本示例')
            b=board(db)
            if body['expectedBoardRevision']!=b['boardRevision'] or body['expectedCounts']!=counts(db):fail(409,'BOARD_VERSION_CONFLICT','共享数据已变化，请重新预览并确认',{'currentBoardRevision':b['boardRevision']})
            replaced=counts(db);db.execute('DELETE FROM jt022_requirements');db.execute("UPDATE jt022_boards SET generation=generation+1,contains_examples=1 WHERE id='shared'")
            self.seed_examples(db,actor,today,now);b=bump(db,STATUSES)
            return 200,b|{'replacedCounts':replaced,'insertedCounts':counts(db),'exampleVersion':EXAMPLE_VERSION,'today':today.isoformat()}
        fail(422,'INVALID_REQUEST','未知动作')
    def seed_examples(self,db,actor,today,now):
        # Deliberately synthetic, relative to server business day; never auto seeded.
        for i,(status,priority,offset) in enumerate([('doing','P0',-1),('doing','P1',0),('review','P2',6),('review','P1',7),('done','P1',-2),('done','P2',None),('doing','P0',None)]):
            v={'title':'演示示例 '+chr(65+i),'priority':priority,'owner':'示例负责人' if i%2 else None,'start':None,'due':(today+timedelta(days=offset)).isoformat() if offset is not None else None,'status':status,'progress':100 if status=='done' else 0,'doneAt':today.isoformat() if status=='done' else None,'note':'明确标识的合成示例，不代表真实开发排期','entryOrigin':'example','createdAt':now,'updatedAt':now,'createdBy':actor['id'],'updatedBy':actor['id']}
            db.execute('INSERT INTO jt022_requirements VALUES(?,?,1,?,?,?)',(new_id('SCH'),'shared',status,len(ordered(db,status)),canon({k:x for k,x in v.items() if k!='status'})))
