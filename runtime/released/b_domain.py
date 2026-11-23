"""JT021 transactional local business domain, commands, ACL and immutable versions."""
import base64
import binascii
from datetime import datetime,timedelta,timezone
import hashlib
import io
import json
from pathlib import Path
import re
import threading
import uuid
from xml.etree import ElementTree
import zipfile
import zlib
from account_primitives import APIError,fail,iso,ROLES
from b_state import canon,fingerprint,new_id
from b_submission_declarations import (DECLARATION_FIELDS,PLATFORM_FIELD,SCOPE_FIELD,current_formula,input_fingerprint,
                                       input_formula_version,normalize_platforms,projected_declarations,valid_product_scope,
                                       version_platform,recorded_form,declared_scope)
from queries import pagination,validate
from b_activity_method import (read_activity_method, product_registration_enabled, requires_store_bearing,
                               has_coupon_scope, PRODUCT_REGISTRATION_METHODS, explicit_jiatan_bearing)
from b_subsidy_price import source_prices, apply_corrections, needs_correction
from business_agent.activity_intake_108.xlsx_preprocess import compact_worksheets, expanded_over_limit, WorksheetLimit
from b_submission_declarations import PLATFORM_CODES, valid_activity_method

MODEL_START='analysis.model_recognition.start'
MODEL_CANCEL='analysis.model_recognition.cancel'
PRIMARY_START='analysis.primary_recognition.start'
PRIMARY_CANCEL='analysis.primary_recognition.cancel'
SUPPLEMENT='activities.supplement'
GROUP='submissions.grouping_decide'
RATE='activities.rate'
REPORT_BAD_CASE='submissions.report_bad_case'
BAD_CASE_EVENT='submission.bad_case_reported'
ACTIONS={'submissions.shenjia_batch','reviews.restart',PRIMARY_START,PRIMARY_CANCEL,RATE,MODEL_START,MODEL_CANCEL,SUPPLEMENT,GROUP,'materials.upload','submissions.create','submissions.update','submissions.delete','analysis.start','analysis.cancel','submissions.confirm','submissions.auto_resolve','accounts.profile.update','reviews.decide','reviews.start'}
from b_workbench_structure import dimensions, method_category
ACTIONS.update((REPORT_BAD_CASE, 'reviews.coupon_selection'))
FORM_KEYS={'activityName','type','platformCode','sourceActivityId','startAt','endAt','brandIds','rulesText','brandBenefit','remark','manualProductRows'}
STAGES=['initial','pricing','final'];STAGE_ROLE={'initial':'management','pricing':'pricing','final':'management'}

def fields(body,required,optional=()):
    if not isinstance(body,dict) or set(body)-set(required)-set(optional) or set(required)-set(body):fail(422,'INVALID_REQUEST','字段缺失或未知')
def text(value,maxlen,nullable=True):
    if value is None and nullable:return value
    if not isinstance(value,str) or len(value)>maxlen:fail(422,'INVALID_REQUEST','文本类型或长度无效')
    try:value.encode('utf-8')
    except UnicodeError:fail(422,'INVALID_REQUEST','文本编码无效')
    return value

def entity(db,table,id):
    if not isinstance(id,str) or not id or len(id)>200:fail(422,'INVALID_REQUEST','记录ID必须为非空字符串')
    row=db.execute('SELECT body FROM '+table+' WHERE id=?',(id,)).fetchone()
    if not row:fail(404,'NOT_FOUND','记录不存在或不可访问')
    return json.loads(row[0])
def put(db,table,body):db.execute('INSERT INTO '+table+'(id,body) VALUES(?,?) ON CONFLICT(id) DO UPDATE SET body=excluded.body',(body['id'],canon(body)))
def bump(db):
    row=db.execute("SELECT value FROM b_meta WHERE key='datasetCounter'").fetchone();n=json.loads(row[0])+1
    db.execute("UPDATE b_meta SET value=? WHERE key='datasetCounter'",(canon(n),));db.execute("UPDATE metadata SET value=? WHERE key='datasetRevision'",(canon('jt021-test-v2:'+str(n)),))
def history(db,actor,clock,*,activity=None,version=None,submission=None,event,before=None,after=None,details=None,source='manual'):
    record={'id':new_id('EVT'),'activityId':activity,'versionId':version,'submissionId':submission,'actorId':actor['id'],'nameSnapshot':actor['name'],'roleSnapshot':actor['business_role'],'at':iso(clock),'event':event,'fromState':before,'toState':after,'details':details or {},'sourceKind':source}
    db.execute('INSERT INTO b_history VALUES(?,?,?,?,?)',(record['id'],activity,version,submission,canon(record)));return record['id']

def grant_scopes(db,actor,auth,action):
    overlay=getattr(auth,'scopes',None)
    if overlay:return overlay(db,actor,action)
    return {r[0] for r in db.execute('SELECT resource_id FROM explicit_grants WHERE user_id=? AND action=?',(actor['id'],action))}

def owns_submission(db,actor,sid,auth,write=False,*,include_deleted=False):
    if not isinstance(sid,str) or not sid or len(sid)>200:fail(422,'INVALID_REQUEST','提报ID必须为非空字符串')
    row=db.execute('SELECT * FROM b_submissions WHERE id=?',(sid,)).fetchone()
    if not row or (row['state']=='deleted' and not include_deleted):
        fail(404,'NOT_FOUND','提报不存在或不可访问')
    if row['submitter_id']==actor['id']:
        auth.require(db,actor,'submissions.edit_own' if write else 'submissions.read_own','own');return row
    if getattr(auth,'has_full_access',lambda _:False)(actor) and grant_scopes(db,actor,auth,'submissions.read')&{'*',sid}:
        return row
    fail(404,'NOT_FOUND','提报不存在或不可访问')
def can_version(db,actor,vid,auth):
    v=db.execute('SELECT body FROM activity_versions WHERE id=?',(vid,)).fetchone()
    if not v:return False
    value=json.loads(v[0]);sid=value.get('submissionId')
    own=db.execute('SELECT submitter_id FROM b_submissions WHERE id=?',(sid,)).fetchone()
    own_scope=grant_scopes(db,actor,auth,'submissions.read_own')
    if own and own[0]==actor['id'] and 'submissions.read_own' in auth.permissions(db,actor) and own_scope&{'*','own'}:return True
    review_scope=grant_scopes(db,actor,auth,'reviews.read')
    if 'reviews.read' in auth.permissions(db,actor) and review_scope&{'*','assigned',vid} and db.execute("SELECT 1 FROM review_tasks WHERE version_id=? AND assignee_id=? AND state IN ('pending','approved','rejected')",(vid,actor['id'])).fetchone():return True
    if grant_scopes(db,actor,auth,'submissions.read')&{'*',sid}:return True
    return False

def material(db,id):
    if not isinstance(id,str) or not id or len(id)>200:fail(422,'INVALID_REQUEST','材料ID必须为非空字符串')
    row=db.execute('SELECT * FROM material_blobs WHERE id=?',(id,)).fetchone()
    if not row:fail(404,'MATERIAL_UNAVAILABLE','原件不存在或不可访问')
    return row

def can_material(db,actor,id,auth,download=False):
    action='materials.download' if download else 'materials.read'
    if action not in auth.permissions(db,actor):return False
    if not grant_scopes(db,actor,auth,action)&{'*',id}:return False
    row=db.execute('SELECT owner_id FROM material_blobs WHERE id=?',(id,)).fetchone()
    if row and (row[0]==actor['id'] or getattr(auth,'has_full_access',lambda _:False)(actor)):return True
    for row in db.execute('SELECT id,body FROM activity_versions'):
        value=json.loads(row['body'])
        if id in value.get('materialIds',[]) and can_version(db,actor,row['id'],auth):return True
    for source in db.execute('SELECT version_id,material_ids FROM version_sources'):
        if id in json.loads(source['material_ids']) and can_version(db,actor,source['version_id'],auth):return True
    return False

def material_view(row):return {'materialId':row['id'],'originalName':row['filename'],'filename':row['filename'],'sha256':row['sha256'],'size':row['size'],'mediaType':row['media_type'],'parseSupport':row['parse_support'],'receivedAt':iso(row['received_at']),'downloadAvailable':True}
def add_material(db,actor,clock,filename,media,content,support,fixed_id=None):
    mid=fixed_id or new_id('MAT');digest=hashlib.sha256(content).hexdigest()
    db.execute('INSERT INTO material_blobs VALUES(?,?,?,?,?,?,?,?,?)',(mid,actor['id'],filename,media,digest,len(content),content,clock,support))
    put(db,'materials',{'id':mid,'originalName':filename,'mime':media,'sha256':digest,'sourceRef':mid,'downloadAvailable':True})
    return material_view(material(db,mid))

def _xlsx_metadata(archive, name):
    entries = [entry for entry in archive.infolist() if entry.filename == name]
    limit = 64 * 1024
    if len(entries) != 1 or entries[0].file_size > limit:
        return None
    with archive.open(entries[0]) as file:
        data = file.read(limit + 1)
    if len(data) != entries[0].file_size or b'\x00' in data or re.search(br'<!\s*(?:DOCTYPE|ENTITY)\b', data, re.I):
        return None
    return ElementTree.fromstring(data.decode('utf-8-sig'))


def _xlsx_oversize_sheet(archive, oversized):
    if len(oversized) != 1:
        return None
    path = oversized[0].filename
    if not re.fullmatch(r'xl/worksheets/[A-Za-z0-9_.-]+\.xml', path) or archive.namelist().count(path) != 1:
        return None
    try:
        workbook = _xlsx_metadata(archive, 'xl/workbook.xml')
        relations = _xlsx_metadata(archive, 'xl/_rels/workbook.xml.rels')
    except (ElementTree.ParseError, ValueError, zipfile.BadZipFile, OSError, RuntimeError, NotImplementedError, zlib.error):
        return None
    package = '{http://schemas.openxmlformats.org/package/2006/relationships}'
    if workbook is None or relations is None or relations.tag != package + 'Relationships':
        return None
    namespaces = {
        '{http://schemas.openxmlformats.org/spreadsheetml/2006/main}workbook':
            'http://schemas.openxmlformats.org/officeDocument/2006/relationships',
        '{http://purl.oclc.org/ooxml/spreadsheetml/main}workbook':
            'http://purl.oclc.org/ooxml/officeDocument/relationships',
    }
    office = namespaces.get(workbook.tag)
    if office is None:
        return None
    relationships = relations.findall(package + 'Relationship')
    targets = [entry for entry in relationships if entry.get('Target') in (path[3:], '/' + path)]
    if len(targets) != 1:
        return None
    relation = targets[0]
    relation_id = relation.get('Id')
    if (not relation_id or relation.get('Type') != office + '/worksheet'
            or relation.get('TargetMode', 'Internal') != 'Internal'
            or sum(entry.get('Id') == relation_id for entry in relationships) != 1):
        return None
    ns = workbook.tag.removesuffix('workbook')
    sheets = workbook.findall(f'{ns}sheets/{ns}sheet')
    names = [sheet.get('name', '') for sheet in sheets if sheet.get('{' + office + '}id') == relation_id]
    if len(names) != 1 or not names[0].strip() or not re.fullmatch(r'[^\x00-\x1f\x7f-\x9f]{1,31}', names[0]):
        return None
    if sum(sheet.get('name', '').casefold() == names[0].casefold() for sheet in sheets) != 1:
        return None
    return names[0]


XLSX_PREPROCESS_SLOT = threading.BoundedSemaphore(1)


def upload_payload(body):
    fields(body,['filename','mediaType','contentBase64'],['sha256']);filename=text(body['filename'],200,False);media=text(body['mediaType'],150,False)
    if not filename or any(c in filename for c in ['\x00','\r','\n']):fail(422,'INVALID_MATERIAL','文件名无效')
    try:content=base64.b64decode(body['contentBase64'],validate=True)
    except (ValueError,TypeError,binascii.Error):fail(422,'INVALID_MATERIAL','base64无效')
    if len(content)>5*1024*1024:fail(413,'MATERIAL_LIMIT','单件原件不能超过5MiB')
    if not content:fail(422,'INVALID_MATERIAL','原件为空')
    digest=hashlib.sha256(content).hexdigest()
    if 'sha256' in body and body['sha256']!=digest:fail(422,'MATERIAL_HASH_MISMATCH','原件SHA不符')
    extension=Path(filename).suffix.casefold()
    if extension in {'.html','.htm','.svg','.js','.py','.exe','.xlsm','.docm'} or media in {'text/html','image/svg+xml','application/javascript'}:fail(422,'UNSUPPORTED_MATERIAL','不接收可执行/宏/HTML材料')
    extensions={'text/plain':{'.txt'},'text/markdown':{'.md','.markdown'},'text/csv':{'.csv'},'text/tab-separated-values':{'.tsv'},'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet':{'.xlsx'},'application/vnd.openxmlformats-officedocument.wordprocessingml.document':{'.docx'},'image/png':{'.png'},'image/jpeg':{'.jpg','.jpeg'},'image/webp':{'.webp'},'application/pdf':{'.pdf'},'application/msword':{'.doc'},'application/vnd.ms-excel':{'.xls'}}
    if media in extensions and extension not in extensions[media]:fail(422,'INVALID_MATERIAL','文件扩展名与声明类型不一致')
    supported={'text/plain','text/markdown','text/csv','text/tab-separated-values','application/vnd.openxmlformats-officedocument.spreadsheetml.sheet','application/vnd.openxmlformats-officedocument.wordprocessingml.document'}
    if media.startswith('text/'):
        if media not in supported:fail(422,'UNSUPPORTED_MATERIAL','文本类型不支持')
        try:value=content.decode('utf-8-sig')
        except UnicodeError:fail(422,'INVALID_MATERIAL','文本不是UTF-8')
        if '\x00' in value or re.match(r'\s*<(?:!doctype|html|script)',value,re.I):fail(422,'UNSUPPORTED_MATERIAL','文本实质为不支持的执行/二进制材料')
    elif media in supported:
        try:
            with zipfile.ZipFile(io.BytesIO(content)) as z:
                members=z.infolist()
                if len(members)>200:raise ValueError()
                for entry in members:
                    parts=Path(entry.filename.replace('\\','/'))
                    if parts.is_absolute() or '..' in parts.parts or entry.flag_bits&1 or 'vbaproject' in entry.filename.casefold():raise ValueError()
                xlsx = 'spreadsheetml' in media
                needed='xl/workbook.xml' if xlsx else 'word/document.xml'
                if needed not in z.namelist():raise ValueError()
                entry_limit, total_limit = 5 * 1024 * 1024, 20 * 1024 * 1024
                if expanded_over_limit(members, entry_limit, total_limit):
                    if not xlsx:
                        raise ValueError()
                    if not XLSX_PREPROCESS_SLOT.acquire(blocking=False):
                        fail(429, 'MATERIAL_PREPROCESS_BUSY', '正在读取另一份较大的表格，请稍后重试。')
                    try:
                        compact_worksheets(z, entry_limit, total_limit)
                    except WorksheetLimit:
                        oversized = [entry for entry in members if entry.file_size > entry_limit]
                        name = _xlsx_oversize_sheet(z, oversized)
                        sheet = f'「{name}」工作表' if name else '工作表'
                        fail(422, 'INVALID_MATERIAL', f'{sheet}展开后超出可读取范围。请另存所需内容或拆分表格后重试。')
                    finally:
                        XLSX_PREPROCESS_SLOT.release()
        except (ValueError, zipfile.BadZipFile, OSError, RuntimeError, NotImplementedError, zlib.error):
            fail(422,'INVALID_MATERIAL','Office压缩包类型/安全预算无效')
    else:
        signatures={'image/png':lambda b:b.startswith(b'\x89PNG\r\n\x1a\n'),'image/jpeg':lambda b:b.startswith(b'\xff\xd8\xff'),'image/webp':lambda b:b.startswith(b'RIFF') and b[8:12]==b'WEBP','application/pdf':lambda b:b.startswith(b'%PDF-'),'application/msword':lambda b:b.startswith(bytes.fromhex('D0CF11E0A1B11AE1')),'application/vnd.ms-excel':lambda b:b.startswith(bytes.fromhex('D0CF11E0A1B11AE1'))}
        if media not in signatures or not signatures[media](content):fail(422,'UNSUPPORTED_MATERIAL','不支持或类型不符')
    return filename,media,content,'supported' if media in supported else 'PARSER_UNAVAILABLE'

def validate_form(db,form):
    fields(form,[],FORM_KEYS)
    for key,size in [('activityName',200),('sourceActivityId',200),('rulesText',10000),('brandBenefit',5000),('remark',5000)]:text(form.get(key),size)
    if form.get('type') not in (None, 'brand', 'platform', 'jiatan_sourcing'):
        fail(422, 'INVALID_REQUEST', '活动类型无效')
    if form.get('platformCode') not in (None, *PLATFORM_CODES):
        fail(422, 'INVALID_PLATFORM_CODE', '活动平台请选择美团、淘宝闪购或京东', {'field': 'platformCode'})
    moments=[]
    for key in ['startAt','endAt']:
        value=form.get(key)
        if value is None:moments.append(None);continue
        try:
            date=datetime.fromisoformat(value.replace('Z','+00:00'))
            if date.tzinfo is None:raise ValueError()
            moments.append(date)
        except (ValueError,AttributeError,TypeError):fail(422,'INVALID_REQUEST','时间必须显式含时区')
    if all(moments) and moments[0]>moments[1]:fail(422,'INVALID_REQUEST','时间起止颠倒')
    brands=[] if form.get('brandIds') is None else form['brandIds']
    if not isinstance(brands,list) or len(brands)>20 or any(not isinstance(b,str) for b in brands) or len(set(brands))!=len(brands):fail(422,'INVALID_REQUEST','品牌ID数组无效')
    for bid in brands:entity(db,'brands',bid)
    rows=[] if form.get('manualProductRows') is None else form['manualProductRows']
    if not isinstance(rows,list):fail(422,'INVALID_REQUEST','商品行必须是数组或null')
    if len(rows)>1000:fail(413,'INPUT_LIMIT','商品行超限')
    seen=set()
    for row in rows:
        fields(row,['rowId'],['productId','rawInternalCode','rawUpc','rawName'])
        try:
            if not row['rowId'].startswith('manual-'):raise ValueError()
            uuid.UUID(row['rowId'][7:])
        except (ValueError,TypeError,AttributeError):fail(422,'INVALID_REQUEST','人工商品rowId须为manual-UUID')
        if row['rowId'] in seen:fail(422,'INVALID_REQUEST','人工商品rowId重复')
        seen.add(row['rowId'])
        if row.get('productId') is not None:entity(db,'products',row['productId'])
        for key in ['rawInternalCode','rawUpc','rawName']:text(row.get(key),500)
    return form

def collect_submission_declarations(body,previous):
    if previous is not None:
        try:input_formula_version(previous)
        except ValueError:fail(409,'ANALYSIS_STALE','原始输入绑定不符')
    values={key:previous[key] for key in DECLARATION_FIELDS if previous is not None and key in previous}
    if PLATFORM_FIELD in body:
        try:values[PLATFORM_FIELD]=normalize_platforms(body[PLATFORM_FIELD])
        except ValueError:fail(422,'INVALID_APPLICABLE_PLATFORM_CODES','请选择有效的适用平台',{'field':PLATFORM_FIELD})
    if SCOPE_FIELD in body:
        if not valid_product_scope(body[SCOPE_FIELD]):
            fail(422,'INVALID_PRODUCT_SCOPE_INTENT','请选择有效的商品适用范围',{'field':SCOPE_FIELD})
        values[SCOPE_FIELD]=body[SCOPE_FIELD]
    return values


def require_applicable_platform(form,declarations):
    if form.get('type')=='brand' and not projected_declarations(declarations,form.get('platformCode'))[PLATFORM_FIELD]:
        fail(422,'PLATFORM_REQUIRED','请至少选择一个适用平台',{'field':PLATFORM_FIELD})


def verify_declaration_binding(db,sub,run,analysis,analysis_digest,source):
    try:
        originals={mid:material(db,mid) for mid in source['allMaterialIds']}
        digest=input_fingerprint(sub['revision'],source,originals)
        if (any(hashlib.sha256(original['content']).hexdigest()!=original['sha256'] for original in originals.values())
                or originals[source['formMaterialId']]['content']!=canon(recorded_form(source)).encode()
                or source.get('rawTextMaterialId') and originals[source['rawTextMaterialId']]['content']!=source['rawText'].encode()):
            raise ValueError('Input bytes do not match the source')
    except (APIError,KeyError,TypeError,ValueError):
        fail(409,'ANALYSIS_STALE','原始输入绑定不符')
    row=db.execute('SELECT input_digest FROM submission_revisions WHERE submission_id=? AND revision=?',
                   (sub['id'],sub['revision'])).fetchone()
    if (not row or run['submission_id']!=sub['id'] or run['input_revision']!=sub['revision']
            or run['input_digest']!=row[0] or digest!=row[0] or 'sha256:'+fingerprint(analysis)!=analysis_digest):
        fail(409,'ANALYSIS_STALE','原始输入绑定不符')
    return originals


def submission_input(db,actor,clock,body,sid,revision,*,require_activity_name=False,previous=None):
    declarations=collect_submission_declarations(body,previous)
    if previous is not None and 'activityMethod' in previous:
        declarations['activityMethod']=previous['activityMethod']
    form=body['form']
    if require_activity_name and isinstance(form,dict):
        name=form.get('activityName')
        if name is None or (isinstance(name,str) and not name.strip()):
            fail(422,'NAME_REQUIRED','请输入活动名称',{'field':'activityName'})
    form=validate_form(db,form)
    if revision==1:require_applicable_platform(form,declarations)
    raw='' if body.get('rawText') is None else body['rawText'];text(raw,50000,False)
    ids=body.get('materialIds',[])
    if not isinstance(ids,list) or any(not isinstance(mid,str) for mid in ids):fail(422,'INVALID_REQUEST','材料ID必须为字符串数组')
    if len(ids)>5:fail(413,'MATERIAL_LIMIT','每次提报最多5件不同材料')
    if len(set(ids))!=len(ids):fail(422,'INVALID_REQUEST','材料ID不可重复')
    size=0
    for mid in ids:
        row=material(db,mid)
        if row['owner_id']!=actor['id']:fail(404,'MATERIAL_UNAVAILABLE','不能使用他人原件')
        size+=row['size']
    if size>10*1024*1024:fail(413,'MATERIAL_LIMIT','材料合计超过10MiB')
    if not any(v not in (None,'',[],{}) for v in form.values()) and not raw and not ids:fail(422,'INPUT_REQUIRED','至少有一项非空原始输入')
    value={'form':form}|declarations
    value['inputFormulaVersion']='input/1.3' if any(key in declarations for key in DECLARATION_FIELDS) else current_formula(value)
    controlled=add_material(db,actor,clock,'FORM.json','application/vnd.jiatan.form+json',canon(recorded_form(value)).encode(),'controlled_form',f'FORM-{sid}-r{revision}')['materialId']
    raw_id=add_material(db,actor,clock,'原始文字.txt','text/plain',raw.encode(),'supported')['materialId'] if raw else None
    all_ids=[controlled]+([raw_id] if raw_id else [])+ids
    value.update(rawText=raw,materialIds=ids,formMaterialId=controlled,rawTextMaterialId=raw_id,allMaterialIds=all_ids)
    originals={mid:material(db,mid) for mid in all_ids}
    return value,input_fingerprint(revision,value,originals)

def sub_revision(db,sid,revision):return json.loads(db.execute('SELECT payload FROM submission_revisions WHERE submission_id=? AND revision=?',(sid,revision)).fetchone()[0])
def latest_run_id(db,sid,revision):
    row=db.execute('SELECT id FROM analysis_runs WHERE submission_id=? AND input_revision=? ORDER BY rowid DESC LIMIT 1',(sid,revision)).fetchone()
    return row[0] if row else None

def agent_bad_cases(db,sid):
    records=[]
    for row in db.execute("""SELECT payload FROM b_history WHERE submission_id=?
                             AND json_extract(payload,'$.event')=? ORDER BY rowid""",(sid,BAD_CASE_EVENT)):
        event=json.loads(row[0])
        records.append(event['details']|{'caseId':event['id'],'markedAt':event['at'],'markedBy':event['actorId']})
    return records

def agent_bad_case(db,sid,run_id,candidate_id=None):
    return next((case for case in agent_bad_cases(db,sid)
                 if case['analysisRunId']==run_id and case.get('candidateId') in (None,candidate_id)),None)

def can_report_agent_bad_case(sub,run):
    return (sub['state'] in ('needs_confirmation','needs_input','partially_resolved')
            and run and run['state'] in ('succeeded','partial') and run.get('analysisDigest')
            and (run.get('result') or {}).get('modelUsed') is True
            and (run.get('agentRecognition') or {}).get('status')=='succeeded')

def outstanding_agent_groups(db,sub,run_id,candidates):
    cases=[case.get('candidateId') for case in agent_bad_cases(db,sub['id']) if case['analysisRunId']==run_id]
    built={row[0] for row in db.execute('SELECT candidate_id FROM resolutions WHERE submission_id=? AND input_revision=?',
                                       (sub['id'],sub['revision']))}
    return ([] if None in cases else [item['candidateId'] for item in candidates
            if item['candidateId'] not in built and item['candidateId'] not in cases]),built


def approval_requirements(snapshot, stage, single=False):
    if stage not in ('initial','final'):
        return []
    label='审核' if single else {'initial':'初审','final':'终审'}[stage]
    values=snapshot.get('resolvedFields') or snapshot.get('confirmedFields') or {}
    required=[]
    if ((snapshot.get('couponScopeRequired') or has_coupon_scope(read_activity_method(snapshot),values.get('rulesText')))
            and (snapshot.get('productApplicability') or {}).get('kind') not in ('explicit_skus','all_products','no_specific_products')):
        required.append({'field':'productApplicability','code':'COUPON_SCOPE_CONFIRMATION_REQUIRED',
                         'message':f'请先由提报人确认券活动的商品适用范围，再通过{label}。'})
    if (stage=='final' or single) and needs_correction(snapshot.get('subsidyPrices',[])):
        required.append({'field':'subsidyPrices','code':'SUBSIDY_PRICE_CONFIRMATION_REQUIRED',
                         'message':f'请先由提报人修正待核对的商品补贴价格，再通过{label}。'})
    return required


def pending_items(values,scope,unresolved,reason_overrides=None,*,activity_method=None):
    pending={}
    if values.get('type')=='brand' and not values.get('brandIds'):pending['brandIds']='BRAND_UNCONFIRMED'
    shen_jia=activity_method=='shen_jia' and values.get('platformCode') in (None,'meituan')
    if not values.get('rulesText') and not shen_jia:pending['rulesText']='RULES_UNCONFIRMED'
    if not values.get('sourceActivityId'):pending['sourceActivityId']='SOURCE_ACTIVITY_ID_MISSING'
    for field in ('startAt','endAt'):
        if not values.get(field):pending[field]='ACTIVITY_DATE_MISSING'
    if scope.get('kind')=='unknown' or (unresolved and scope.get('kind')=='explicit_skus'):
        pending['productApplicability']='PRODUCT_SCOPE_UNCONFIRMED'
    pending.update(reason_overrides or {})
    order=('brandIds','rulesText','platformCode','sourceActivityId','startAt','endAt','storeBearing','productApplicability','subsidyPrices','activityName')
    return [{'field':field,'reasonCode':pending[field]} for field in order if field in pending]


def require_product_identity(row, item, invalid='PRODUCT_IDENTITY_MISMATCH'):
    codes = [(row.get(raw), item.get(key)) for raw,key in (('rawInternalCode','internalCode'),('rawUpc','upc'))]
    known = [(expected, actual) for expected, actual in codes if expected]
    if (any(str(expected).strip() != str(actual or '').strip() for expected, actual in known)
            or not known and row.get('matchedProductId') != item['id']):
        fail(422,invalid,'原表内部码或 UPC 与所选商品不一致。请核对原表或先保留未确认。',
             {'rowId':row['rowId']})


def funding_material_hashes(db, source, actor, auth):
    return {mid:row[0] for mid in source['allMaterialIds']
            if can_material(db,actor,mid,auth)
            and (row:=db.execute('SELECT sha256 FROM material_blobs WHERE id=?',(mid,)).fetchone())}


def run_view(row, *, include_model=False, include_primary=False):
    payload=json.loads(row['payload'])
    payload.pop('_grouping',None)
    payload.pop('_modelFirst',None)
    if not include_model:
        payload.pop('modelRecognition',None)
        payload.pop('modelRecognitionDigest',None)
    if not include_primary:
        payload.pop('primaryRecognition',None)
        payload.pop('primaryRecognitionDigest',None)
    return {'runId':row['id'],'submissionId':row['submission_id'],'state':row['state'],'inputRevision':row['input_revision'],'inputDigest':row['input_digest'],'catalogRevision':row['catalog_revision'],'createdAt':iso(row['created_at']),'startedAt':iso(row['started_at']),'finishedAt':iso(row['finished_at']),**payload}
def submission_view(db,row,actor,auth,*,model_recognition=None,primary_recognition=None):
    snapshots=[]
    for revision in db.execute('SELECT * FROM submission_revisions WHERE submission_id=? ORDER BY revision',(row['id'],)):
        payload=json.loads(revision['payload'])
        snapshots.append({'inputRevision':revision['revision'],'inputDigest':revision['input_digest'],**payload,
                          **projected_declarations(payload,payload['form'].get('platformCode'))})
    runs=[run_view(r,include_model=model_recognition is not None,include_primary=primary_recognition is not None) for r in db.execute('SELECT * FROM analysis_runs WHERE submission_id=? ORDER BY rowid',(row['id'],))]
    from b_grouping import project
    from b_coupon_schedule import observed_schedule, date_schedule
    from b_coupon_review import brand_coupon_method_correction, split_brand_coupon_groups
    from b_coupon_period_dates import candidate_coupon_periods
    from b_intake_grouping_validation import splits_single_child_export, ERROR_CODE, ERROR_MESSAGE
    latest=runs[-1] if runs else None
    unresolved=row['state'] in ('needs_confirmation','needs_input') and not row['activity_id']
    for run in runs:
        if model_recognition is not None:model_recognition.project(db,actor,run,row)
        project(db,actor,auth,run)
        if run['state']=='succeeded' and run['inputRevision']==row['revision']:
            source=snapshots[-1]
            result=run.get('result') or {}
            if result.get('engine')=='deepseek_model_first_v1':
                docs=result.get('materialResults',[])
                if (run is latest and result['candidates'] and len(docs)==1
                        and (run.get('agentRecognition') or {}).get('status')=='succeeded'):
                    mid=docs[0]['materialId']
                    if can_material(db,actor,mid,auth):
                        original=material(db,mid)
                        form_id=source['formMaterialId']
                        form_original=material(db,form_id)
                        form={'materialId':form_id,'sha256':form_original['sha256'],'fields':recorded_form(source)}
                        if unresolved and original and splits_single_child_export(result, form, {mid:original['filename']}):
                            run['agentRecognition']=dict(run['agentRecognition'],status='failed',reasonCode=ERROR_CODE)
                            run['error']={'code':ERROR_CODE,'message':ERROR_MESSAGE}
                        elif original and original['sha256']==docs[0]['sha256']:
                            identified=candidate_coupon_periods(result,form,form_original['received_at'])
                            if identified:
                                run['candidatePeriods']=identified
                if run is latest and (run.get('agentRecognition') or {}).get('status')=='succeeded':
                    metadata=run['agentRecognition']['candidateMetadata']
                    by_id={item['candidateId']:item for item in metadata}
                    platforms=projected_declarations(source,source['form'].get('platformCode'))['applicablePlatformCodes']
                    corrections={}
                    for candidate in result['candidates']:
                        key=candidate['candidateId']
                        decision=brand_coupon_method_correction(result,candidate,by_id[key],platforms)
                        if decision:
                            corrections[key]=decision
                    if corrections:
                        run['agentRecognition']=run['agentRecognition']|{'candidateMetadata':[
                            item|{'activityMethod':{'value':corrections[item['candidateId']]['toMethod'],'status':'explicit' if corrections[item['candidateId']]['toMethod'] else 'missing',
                                                    'evidenceIds':corrections[item['candidateId']]['couponEvidenceIds'] if corrections[item['candidateId']]['toMethod'] else []}}
                            if item['candidateId'] in corrections else item for item in metadata]}
                    meta=run['agentRecognition']
                    run['agentRecognition']=meta|{'candidateMetadata':[
                        item|{'activityCategory':method_category(item['activityMethod']['value'])
                              if item['activityMethod']['status']=='explicit' else None}
                        for item in meta['candidateMetadata']]}
                if run is latest and unresolved and (run.get('agentRecognition') or {}).get('status')=='succeeded':
                    conflicts=split_brand_coupon_groups(result,run['agentRecognition']['candidateMetadata'],db)
                    if conflicts:
                        run['couponGroupingIssues']=conflicts
                run['subsidyPrices']={candidate['candidateId']: prices for candidate in result['candidates']
                                      if (prices:=source_prices(result,candidate))}
            schedule=observed_schedule(run.get('result'),source)
            if (run is latest and row['submitter_id']==actor['id']
                    and can_report_agent_bad_case(row,run) and not run.get('couponGroupingIssues')):
                from b_coupon_funding_confirmation import questions_for_run
                open_candidates,_=outstanding_agent_groups(db,row,run['runId'],result['candidates'])
                if open_candidates:
                    platforms=projected_declarations(source,source['form'].get('platformCode'))['applicablePlatformCodes']
                    questions=questions_for_run(result,run['agentRecognition']['candidateMetadata'],platforms,
                        set(open_candidates),funding_material_hashes(db,source,actor,auth),schedule)
                    if questions:
                        run['couponFundingQuestions']=questions
            if schedule and can_material(db,actor,schedule['materialId'],auth):
                original=material(db,schedule['materialId'])
                if original and original['sha256']==schedule['sourceSha256']:
                    form=source['form']
                    try:
                        schedule=date_schedule(schedule,form.get('startAt'),form.get('endAt'))
                    except ValueError:
                        pass
                    run['couponSchedule']=schedule
    resolutions=[]
    readable={}
    rows=db.execute("""SELECT r.*,a.body AS activity_body,base.body AS base_body,
                       current.body AS current_body,v.state AS review_state
                       FROM resolutions r
                       LEFT JOIN activities a ON a.id=r.activity_id
                       LEFT JOIN activity_versions base ON base.id=r.version_id
                       LEFT JOIN activity_versions current ON current.id=COALESCE(
                           json_extract(a.body,'$.displayVersionId'),
                           json_extract(a.body,'$.currentReviewVersionId'),r.version_id)
                       LEFT JOIN version_reviews v ON v.version_id=r.version_id AND v.activity_id=r.activity_id
                       WHERE r.submission_id=? ORDER BY r.rowid""",(row['id'],))
    for result in rows:
        value=json.loads(result['payload'])
        # A resolution identifies the group; the submission row's activity pointer does not.
        value['currentPendingItems']=None
        base=json.loads(result['base_body']) if result['base_body'] else {}
        activity=json.loads(result['activity_body']) if result['activity_body'] else {}
        binding_matches=(value.get('submissionId'),value.get('revision'),value.get('candidateId'),
                         value.get('activityId'),value.get('versionId'))==(
            result['submission_id'],result['input_revision'],result['candidate_id'],
            result['activity_id'],result['version_id'])
        if binding_matches and base.get('activityId')==activity.get('id')==result['activity_id']:
            current=base if value.get('outcome')=='change_saved' else (
                json.loads(result['current_body']) if result['current_body'] else {})
            if current.get('activityId')==result['activity_id']:
                version_id=current['id']
                if version_id not in readable:readable[version_id]=can_version(db,actor,version_id,auth)
                if readable[version_id]:
                    snapshot=current.get('snapshot')
                    items=None
                    if isinstance(snapshot,dict):
                        correction=snapshot.get('dateBackfill')
                        items=correction.get('pendingItems',snapshot.get('pendingItems')) if isinstance(correction,dict) else snapshot.get('pendingItems')
                    if isinstance(items,list):
                        value['currentPendingItems']=items
                        value['currentPendingVersionId']=version_id
        if 'pendingItems' in value:
            value['reviewState']=result['review_state']
            if result['review_state']!='not_started':value['reviewBlockedReason']=None
        resolutions.append(value)
    return {'submissionId':row['id'],'submitterId':row['submitter_id'],'submittedAt':iso(row['received_at']),'revision':row['revision'],'state':row['state'],'activityId':row['activity_id'],'versionId':row['version_id'],'parentSubmissionId':row['parent_id'],'currentInput':snapshots[-1],'revisions':snapshots,'runs':runs,'resolutions':resolutions,'materials':[material_view(material(db,mid)) for mid in snapshots[-1]['allMaterialIds']],'allowedActions':[a for a in ['submissions.edit_own','activities.propose'] if a in auth.permissions(db,actor) and (row['submitter_id']==actor['id'] or getattr(auth,'has_full_access',lambda _:False)(actor))],'blockedReasons':[]}

class Domain:
    def __init__(self,auth,jobs,clock,*,require_activity_name=False,allow_submission_delete=False,allow_submission_declarations=False):
        self.auth=auth;self.jobs=jobs;self.clock=clock
        self.require_activity_name=require_activity_name
        self.allow_submission_delete=allow_submission_delete
        self.allow_submission_declarations=allow_submission_declarations
        self.submission_declarations_persistence_ready=True
        self.platform_materials=None
        self.first_review_start=False
        self.activity_date_evidence=False
        self.activity_date_decoder=None
    def command(self,sid,method,path,action,key,body,operation):
        try:
            if not isinstance(key,str) or len(key)>80 or not key.isascii():raise ValueError()
            uuid.UUID(key)
        except (ValueError,TypeError):fail(422,'IDEMPOTENCY_KEY_REQUIRED','写入需要稳定UUID Idempotency-Key')
        reserved=None
        try:
            with self.auth.tx() as db:
                _,actor=self.auth.valid(db,sid,True)
                resource=self.authorize_command(db,actor,action,path,body)
                digest=fingerprint({'method':method,'action':action,'path':path,'resource':resource,'body':body})
                previous=db.execute('SELECT * FROM commands WHERE actor_id=? AND action=? AND idem_key=?',(actor['id'],action,key)).fetchone()
                if previous:
                    if action in ('activity_registration_drafts.save','activity_registration_drafts.decide','activity_registration_reviews.confirm_scope'):
                        from b_registration_draft import receipt
                        receipt(self.auth,db,actor,previous)
                    if self.platform_materials and action in ('platform_materials.submit','platform_materials.verify','platform_materials.recheck'):
                        self.platform_materials.check_receipt(db,actor,previous)
                    if previous['digest']!=digest:fail(409,'IDEMPOTENCY_CONFLICT','同一幂等键的目标或正文不符')
                    return previous['status'],json.loads(previous['response']),True
                command_id=new_id('CMD')
                status,result=operation(db,actor,command_id);result=result|{'commandId':command_id}
                if action in ('analysis.start',GROUP):reserved=result['runId']
                db.execute('INSERT INTO commands VALUES(?,?,?,?,?,?,?,?,?)',(command_id,actor['id'],action,key,digest,resource,status,canon(result),self.clock()))
                bump(db)
            if reserved:self.jobs.enqueue(reserved)
            if action=='analysis.cancel' and result.get('state')=='cancel_requested':self.jobs.cancel(result['runId'])
            return status,result,False
        except BaseException:
            if reserved:self.jobs.release_reservation(reserved)
            raise
    def authorize_command(self,db,actor,action,path,body):
        parts=path.strip('/').split('/')
        if action in ('activity_registration_drafts.save','activity_registration_drafts.decide','activity_registration_reviews.confirm_scope'):
            from b_registration_draft import authorize
            authorize(self.auth,db,actor,parts[1],parts[3],parts[5],False)
            self.auth.require(db,actor,'platform_materials.write',parts[1]+':'+parts[5])
            return ':'.join((parts[1],parts[3],parts[5]))
        if self.platform_materials and action in ('platform_materials.verify','platform_materials.recheck'):
            self.platform_materials.require_verifier(db,actor,parts[1],parts[3])
            return parts[1]+':'+parts[3]
        if self.platform_materials and action=='platform_materials.submit':
            aid,code=parts[1],parts[3]
            self.platform_materials.authorize(db,actor,aid,code,write=True,selected=False)
            return aid+':'+code
        if self.activity_date_evidence and action=='activities.date_backfill':
            from b_activity_dates import require_actor
            return require_actor(self,db,actor,parts[1],parts[3])['id']
        if action==RATE:
            return self.rating_target(db,actor,parts[1])['id']
        if action=='submissions.shenjia_batch':
            from b_shenjia_batch import owned_source
            return owned_source(db,actor,self.auth,body['sourceMaterialId'])['id']
        if action=='materials.upload':self.auth.require(db,actor,'submissions.create');return 'new-material'
        if action=='submissions.create':
            self.auth.require(db,actor,'submissions.create')
            if body.get('parentSubmissionId') is not None:owns_submission(db,actor,body['parentSubmissionId'],self.auth,True)
            return body.get('parentSubmissionId') or 'new-submission'
        if action=='submissions.delete' and self.allow_submission_delete:
            return owns_submission(db,actor,parts[1],self.auth,True,include_deleted=True)['id']
        if action in ['submissions.update',REPORT_BAD_CASE,'analysis.start','submissions.confirm','submissions.auto_resolve',GROUP]:
            sub=owns_submission(db,actor,parts[1],self.auth,True)
            if action in ['submissions.confirm','submissions.auto_resolve',GROUP]:self.auth.require(db,actor,'activities.propose')
            return sub['id']
        if action in ('analysis.cancel',MODEL_START,MODEL_CANCEL,PRIMARY_START,PRIMARY_CANCEL):
            run=db.execute('SELECT * FROM analysis_runs WHERE id=?',(parts[1],)).fetchone()
            if not run:fail(404,'NOT_FOUND','分析不存在')
            owns_submission(db,actor,run['submission_id'],self.auth,True);return run['id']
        if action==SUPPLEMENT:return self.supplement_target(db,actor,parts[1],parts[3])['id']
        if action=='accounts.profile.update':self.auth.require(db,actor,'accounts.manage');return parts[1]
        if action=='reviews.restart':
            from b_review_restart import authorize
            authorize(self,db,actor,parts[1],parts[3])
            return parts[3]
        if action=='reviews.coupon_selection':
            fields(body,['selectedCouponIds','expectedRevision'],['expectedReviewStage'])
            return self.coupon_reviewer(db,actor,parts[1],parts[3],body.get('expectedReviewStage','initial'))['id']
        if action in ['reviews.decide','reviews.start']:
            vid=parts[3];version=entity(db,'activity_versions',vid)
            if version['activityId']!=parts[1] or not can_version(db,actor,vid,self.auth):fail(404,'NOT_FOUND','版本不可访问')
            if action=='reviews.start' and self.first_review_start:self.require_first_review_actor(db,actor,version)
            elif action=='reviews.start' and db.execute('SELECT 1 FROM b_submissions WHERE id=? AND submitter_id=?',(version['submissionId'],actor['id'])).fetchone():self.auth.require(db,actor,'activities.propose')
            else:
                self.auth.require(db,actor,'reviews.decide','assigned')
                if action=='reviews.decide':
                    node_id=body.get('nodeId')
                    if not isinstance(node_id,str):fail(422,'INVALID_REQUEST','节点ID必须为字符串')
                    node=db.execute('SELECT * FROM review_tasks WHERE id=? AND version_id=?',(node_id,vid)).fetchone()
                    if not node:fail(403,'FORBIDDEN','并非该节点指派人')
                    self.require_review_actor(db,actor,node,parts[1])
            return vid
        fail(422,'INVALID_REQUEST','未知命令类型')
    def rating_target(self,db,actor,activity_id):
        self.auth.require(db,actor,'operations.read',activity_id)
        self.auth.require(db,actor,RATE,activity_id)
        return entity(db,'activities',activity_id)

    def rate_activity(self,db,actor,activity_id,body,command_id):
        fields(body,['expectedActivityRevision','level'])
        level=body['level']
        if level is not None and (not isinstance(level,str) or level not in ('SS','S','A')):
            fail(422,'INVALID_REQUEST','评级须为SS、S、A或未评级')
        expected=body['expectedActivityRevision']
        if type(expected) is not int or expected<1:
            fail(422,'INVALID_REQUEST','活动修订号无效')
        activity=self.rating_target(db,actor,activity_id)
        revision=activity.get('revision',1)
        if expected!=revision:
            fail(409,'STATE_CONFLICT','活动已更新，请核对最新等级后重新保存',
                 {'activityRevision':revision,'level':activity.get('level')})
        previous=activity.get('level')
        if previous!=level:
            activity['level']=level
            activity['revision']=revision+1
            put(db,'activities',activity)
            history(db,actor,self.clock(),activity=activity_id,event='activity.rated',
                    details={'previousLevel':previous,'level':level,'commandId':command_id})
        return 200,{'activityId':activity_id,'level':level,'revision':activity.get('revision',1)}

    def create_submission(self,db,actor,body):
        optional=['rawText','materialIds','parentSubmissionId']
        if self.allow_submission_declarations:optional.extend(DECLARATION_FIELDS)
        fields(body,['form'],optional)
        sid=new_id('SUB')
        value,digest=submission_input(db,actor,self.clock(),body,sid,1,require_activity_name=self.require_activity_name)
        db.execute('INSERT INTO b_submissions VALUES(?, ?,1,\'received\',NULL,NULL,?,?)',(sid,actor['id'],body.get('parentSubmissionId'),self.clock()))
        db.execute('INSERT INTO submission_revisions VALUES(?,?,?,?)',(sid,1,digest,canon(value)))
        history(db,actor,self.clock(),submission=sid,event='submission.received',after='received')
        return 201,{'submissionId':sid,'revision':1,'state':'received','activityId':None,'versionId':None}
    def update_submission(self,db,actor,sid,body):
        fields(body,['expectedRevision','form','rawText','materialIds'],
               DECLARATION_FIELDS if self.allow_submission_declarations else ())
        sub=owns_submission(db,actor,sid,self.auth,True)
        if type(body['expectedRevision']) is not int or body['expectedRevision']!=sub['revision']:fail(409,'STATE_CONFLICT','输入版本已变化')
        if sub['activity_id'] or db.execute('SELECT 1 FROM resolutions WHERE submission_id=?',(sid,)).fetchone():fail(409,'STATE_CONFLICT','已建档输入不可改写，请新建parent提报')
        if db.execute("SELECT 1 FROM analysis_runs WHERE submission_id=? AND state IN ('queued','running','cancel_requested')",(sid,)).fetchone():fail(409,'STATE_CONFLICT','分析中，请先明确取消')
        revision=sub['revision']+1
        value,digest=submission_input(db,actor,self.clock(),body,sid,revision,require_activity_name=self.require_activity_name,
                                      previous=sub_revision(db,sid,sub['revision']))
        db.execute('INSERT INTO submission_revisions VALUES(?,?,?,?)',(sid,revision,digest,canon(value)));db.execute("UPDATE b_submissions SET revision=?,state='received' WHERE id=?",(revision,sid))
        history(db,actor,self.clock(),submission=sid,event='submission.revised',before=sub['state'],after='received')
        return 200,{'submissionId':sid,'revision':revision,'state':'received'}
    def submission_delete_reason(self,db,sub):
        sid=sub['id']
        if sub['state']=='deleted':return 'SUBMISSION_DELETED'
        if sub['state']=='analyzing' or db.execute(
                "SELECT 1 FROM analysis_runs WHERE submission_id=? AND state IN ('queued','running','cancel_requested')",
                (sid,)).fetchone():
            return 'ANALYSIS_ACTIVE'
        if (sub['state'] in ('resolved','partially_resolved') or sub['activity_id'] or sub['version_id']
                or db.execute('SELECT 1 FROM resolutions WHERE submission_id=?',(sid,)).fetchone()):
            return 'HAS_RESOLUTION'
        reference=db.execute("""
            SELECT 1 FROM version_sources WHERE submission_id=?
            UNION ALL SELECT 1 FROM version_reviews WHERE submission_id=?
            UNION ALL SELECT 1 FROM activity_versions WHERE json_extract(body,'$.submissionId')=?
            UNION ALL SELECT 1 FROM b_history WHERE submission_id=? AND (activity_id IS NOT NULL OR version_id IS NOT NULL)
            LIMIT 1
        """,(sid,sid,sid,sid)).fetchone()
        if reference:return 'HAS_SOURCE_REFERENCE'
        if db.execute("""SELECT 1 FROM b_submissions child WHERE child.parent_id=?
                         AND (child.state<>'deleted' OR EXISTS (
                             SELECT 1 FROM submission_revisions WHERE submission_id=child.id))""",(sid,)).fetchone():
            return 'HAS_CHILD_SUBMISSION'
        if sub['state'] not in ('received','needs_input','needs_confirmation','failed'):
            return 'STATE_NOT_DELETABLE'
        return None

    def submission_delete_block(self,db,actor,sub):
        if sub['submitter_id']!=actor['id']:
            return {'code':'FORBIDDEN','reasonCode':'ACTION_OR_SCOPE_DENIED'}
        try:self.auth.require(db,actor,'submissions.edit_own','own')
        except APIError as error:
            if error.status!=403:raise
            return {'code':'FORBIDDEN','reasonCode':'ACTION_OR_SCOPE_DENIED'}
        reason=self.submission_delete_reason(db,sub)
        return {'code':'STATE_CONFLICT','reasonCode':reason} if reason else None

    def delete_submission(self,db,actor,sid,body):
        fields(body,['expectedRevision'])
        expected=body['expectedRevision']
        if type(expected) is not int or expected<1:
            fail(422,'INVALID_REQUEST','输入修订须为正整数')
        sub=owns_submission(db,actor,sid,self.auth,True,include_deleted=True)
        if sub['state']=='deleted':
            fail(409,'STATE_CONFLICT','提报已删除',{'reasonCode':'SUBMISSION_DELETED'})
        if expected!=sub['revision']:
            fail(409,'STATE_CONFLICT','输入版本已变化',{'reasonCode':'REVISION_CHANGED','currentRevision':sub['revision']})
        reason=self.submission_delete_reason(db,sub)
        if reason:
            fail(409,'STATE_CONFLICT','当前状态无法删除提报',{'reasonCode':reason})
        change=db.execute("UPDATE b_submissions SET state='deleted' WHERE id=? AND revision=? AND state=?",
                          (sid,expected,sub['state']))
        if change.rowcount!=1:
            fail(409,'STATE_CONFLICT','输入版本已变化',{'reasonCode':'REVISION_CHANGED'})
        self.purge_submission_input(db,sid,sub['submitter_id'])
        history(db,actor,self.clock(),submission=sid,event='submission.deleted',before=sub['state'],after='deleted',
                details={'inputRevision':expected})
        return 200,{'submissionId':sid,'revision':expected,'state':'deleted'}

    def purge_submission_input(self,db,sid,owner):
        """Remove draft content while retaining the original command identity."""
        material_ids = {row[0] for row in db.execute("""
            SELECT DISTINCT ref.value FROM submission_revisions revision,
                json_each(revision.payload, '$.allMaterialIds') ref
            WHERE revision.submission_id=? AND ref.type='text'
        """,(sid,))}
        db.execute("""DELETE FROM commands WHERE resource_id IN
                       (SELECT id FROM analysis_runs WHERE submission_id=?)
                       AND action LIKE 'analysis.%'""",(sid,))
        db.execute('DELETE FROM analysis_runs WHERE submission_id=?',(sid,))
        db.execute('DELETE FROM submission_revisions WHERE submission_id=?',(sid,))
        db.execute('DELETE FROM b_history WHERE submission_id=?',(sid,))
        db.execute("""DELETE FROM commands WHERE resource_id=? AND action IN
                       ('submissions.update','analysis.start','submissions.grouping_decide','submissions.auto_resolve')""",(sid,))
        for material_id in material_ids:
            # An attachment can be reused by another draft or an already retained version.
            referenced = db.execute("""
                SELECT 1 FROM submission_revisions, json_each(payload, '$.allMaterialIds') WHERE value=?
                UNION ALL SELECT 1 FROM version_sources, json_each(material_ids) WHERE value=?
                UNION ALL SELECT 1 FROM activity_versions, json_each(body, '$.materialIds') WHERE value=?
                UNION ALL SELECT 1 FROM activities, json_tree(body, '$.materials') WHERE value=?
                UNION ALL SELECT 1 FROM submissions, json_tree(body, '$.materials') WHERE value=?
                UNION ALL SELECT 1 FROM commands WHERE action='submissions.shenjia_batch' AND resource_id=?
                LIMIT 1
            """,(material_id,)*6).fetchone()
            if referenced:
                continue
            removed = db.execute('DELETE FROM material_blobs WHERE id=? AND owner_id=? RETURNING sha256',
                                 (material_id,owner)).fetchone()
            if removed:
                db.execute('DELETE FROM materials WHERE id=?',(material_id,))
                db.execute("""DELETE FROM commands WHERE action='materials.upload'
                               AND json_extract(response,'$.materialId')=?""",(material_id,))
                db.execute("""DELETE FROM parse_cache WHERE sha256=? AND NOT EXISTS
                               (SELECT 1 FROM material_blobs WHERE sha256=?)""",(removed[0],removed[0]))

    def report_agent_bad_case(self,db,actor,sid,body):
        fields(body,['expectedRevision','analysisRunId','analysisDigest'],['reason','candidateId'])
        revision=body['expectedRevision']
        run_id=text(body['analysisRunId'],200,False)
        digest=text(body['analysisDigest'],100,False)
        reason=(text(body.get('reason'),1000) or '').strip()
        if type(revision) is not int or revision<1 or not run_id or not digest:
            fail(422,'INVALID_REQUEST','本次识别记录或输入修订无效')
        sub=owns_submission(db,actor,sid,self.auth,True)
        if sub['revision']!=revision or latest_run_id(db,sid,revision)!=run_id:
            fail(409,'ANALYSIS_STALE','提报或识别结果已变化，请刷新后核对')
        row=db.execute('SELECT * FROM analysis_runs WHERE id=? AND submission_id=?',(run_id,sid)).fetchone()
        run=run_view(row)
        if run.get('analysisDigest')!=digest:
            fail(409,'ANALYSIS_STALE','识别结果已变化，请刷新后核对')
        candidates=(run.get('result') or {}).get('candidates',[])
        candidate_id=body.get('candidateId')
        if 'candidateId' not in body and len(candidates)==1:
            candidate_id=candidates[0]['candidateId']
        if not isinstance(candidate_id,str) or not any(c['candidateId']==candidate_id for c in candidates):
            fail(422,'INVALID_REQUEST','请选择本次识别结果中的失败分组')
        saved=agent_bad_case(db,sid,run_id,candidate_id)
        if saved:
            return 200,{'submissionId':sid,'revision':revision,'state':sub['state'],'badCase':saved}
        outstanding,built=outstanding_agent_groups(db,sub,run_id,candidates)
        if not can_report_agent_bad_case(sub,run) or candidate_id not in outstanding:
            fail(409,'STATE_CONFLICT','只可对已返回结果且尚未建档的分组判定失败')
        agent=run['agentRecognition']
        state='partially_resolved' if built else 'needs_confirmation' if len(outstanding)>1 else 'failed'
        details={'inputRevision':revision,'analysisRunId':run_id,'analysisDigest':digest,
                 'inputDigest':row['input_digest'],'reason':reason or None,'candidateId':candidate_id,
                 'model':agent.get('model'),'promptVersion':agent.get('promptVersion'),
                 'groupCount':len(run['result'].get('candidates',[]))}
        db.execute('UPDATE b_submissions SET state=? WHERE id=?',(state,sid))
        history(db,actor,self.clock(),submission=sid,event=BAD_CASE_EVENT,before=sub['state'],after=state,details=details)
        return 200,{'submissionId':sid,'revision':revision,'state':state,'badCase':agent_bad_case(db,sid,run_id,candidate_id)}

    def start_analysis(self,db,actor,sid,body):
        fields(body,['expectedRevision'],['expectedPreviousAnalysisRunId','agentConsent']);sub=owns_submission(db,actor,sid,self.auth,True)
        if type(body['expectedRevision']) is not int or body['expectedRevision']!=sub['revision']:fail(409,'STATE_CONFLICT','输入版本变化')
        if sub['activity_id'] or sub['version_id'] or db.execute('SELECT 1 FROM resolutions WHERE submission_id=?',(sid,)).fetchone():
            fail(409,'STATE_CONFLICT','已有建档决议，不能重新识别原提报')
        if db.execute("SELECT 1 FROM analysis_runs WHERE submission_id=? AND input_revision=? AND state IN ('queued','running','cancel_requested')",(sid,sub['revision'])).fetchone():fail(409,'STATE_CONFLICT','该输入已有活动分析')
        if 'expectedPreviousAnalysisRunId' in body:
            previous=body['expectedPreviousAnalysisRunId']
            if not isinstance(previous,str) or not previous:fail(422,'INVALID_REQUEST','上一分析ID必须为非空字符串')
            if previous!=latest_run_id(db,sid,sub['revision']):fail(409,'STATE_CONFLICT','上一分析已变化，请刷新后重新识别')
        extensions=None
        if getattr(self.jobs,'model_first',False):
            if body.get('agentConsent') is not True:fail(422,'AGENT_CONSENT_REQUIRED','提交识别需确认将本修订原始材料发送给 DeepSeek')
            capability = self.jobs.capability()
            if capability.get('authenticationStatus') == 'rejected':
                fail(503, 'AGENT_MODEL_AUTH_REJECTED', 'DeepSeek 鉴权失败。请联系管理员更新密钥，刷新后重试原提报。')
            if not capability['enabled']:
                fail(503, 'AGENT_MODEL_UNAVAILABLE', 'DeepSeek 本机配置不可用。请联系管理员检查配置，刷新后重试。')
            extensions={'_modelFirst':{'consent':True,'consentBy':actor['id'],'consentedAt':iso(self.clock()),'inputRevision':sub['revision']},
                        'agentRecognition':{'contractVersion':'activity-model-intake/1.0','provider':'deepseek','status':'queued','model':None,'promptVersion':None,'reasonCode':None}}
        return 202,self.queue_analysis(db,sub,extensions)

    def queue_analysis(self,db,sub,extensions=None):
        sid=sub['id'];run=new_id('RUN')
        source=db.execute('SELECT input_digest FROM submission_revisions WHERE submission_id=? AND revision=?',(sid,sub['revision'])).fetchone()[0]
        catalog=json.loads(db.execute("SELECT value FROM b_meta WHERE key='catalogRevision'").fetchone()[0])
        last_created=db.execute('SELECT MAX(created_at) FROM analysis_runs WHERE submission_id=?',(sid,)).fetchone()[0]
        created=self.clock()
        if last_created is not None:created=max(created,last_created)
        db.execute('INSERT INTO analysis_runs VALUES(?,?,?,?,?,?,?, ?,NULL,NULL)',(run,sid,sub['revision'],source,catalog,'queued',canon({'phase':'queued','materialResults':[],'result':None,'error':None,**(extensions or {})}),created))
        db.execute("UPDATE b_submissions SET state='analyzing' WHERE id=?",(sid,))
        self.jobs.reserve(run)
        return {'runId':run,'state':'queued','inputRevision':sub['revision'],'inputDigest':source}
    def cancel_analysis(self,db,actor,rid,body):
        fields(body,[]);run=db.execute('SELECT * FROM analysis_runs WHERE id=?',(rid,)).fetchone()
        if run['state'] in ['queued','running','cancel_requested']:
            db.execute("UPDATE analysis_runs SET state='cancel_requested' WHERE id=?",(rid,))
            return 200,{'runId':rid,'state':'cancel_requested','createdBusinessNotReverted':bool(db.execute('SELECT 1 FROM resolutions WHERE submission_id=?',(run['submission_id'],)).fetchone())}
        return 200,{'runId':rid,'state':run['state'],'createdBusinessNotReverted':bool(db.execute('SELECT 1 FROM resolutions WHERE submission_id=?',(run['submission_id'],)).fetchone())}
    def review_view(self,db,vid):
        row=db.execute('SELECT * FROM version_reviews WHERE version_id=?',(vid,)).fetchone()
        if not row:return None
        value=dict(row);state=row['state'];value.update(versionId=vid,activityId=row['activity_id'],approvalMode=row['approval_mode'],stage=state[:-8] if state.endswith('_pending') else None,result='pending' if state.endswith('_pending') else state)
        if state=='not_started':
            version=entity(db,'activity_versions',vid)
            value['reviewBlockedReason']=version.get('snapshot',{}).get('reviewBlockedReason')
        value.pop('version_id');value.pop('activity_id');value.pop('approval_mode');return value
    def review_assignees(self,db,*,submitter_id=None,activity_id=None,single=False):
        stages=('initial',) if single else STAGES
        plan={row['stage']:row['user_id'] for row in db.execute('SELECT stage,user_id FROM review_plan')
              if row['stage'] in stages}
        if any(stage not in plan for stage in stages):
            return [],'ASSIGNMENT_REQUIRED'
        if not hasattr(self.auth,'account'):
            return [],'ASSIGNEE_ROLE_MISMATCH'
        if submitter_id is not None and submitter_id in plan.values() and not self.is_review_admin(self.auth.account(db,submitter_id)):
            return [],'ASSIGNEE_SELF_REVIEW'
        if not single and submitter_id is not None and plan['initial']==plan['final'] and not self.is_review_admin(self.auth.account(db,plan['initial'])):
            return [],'ASSIGNEE_SAME_INITIAL_FINAL'
        people=[]
        for stage in stages:
            try:person=self.auth.account(db,plan[stage])
            except APIError as error:
                if error.status not in (401,403,404):raise
                return [],'ASSIGNEE_ROLE_MISMATCH'
            active = getattr(self.auth, 'review_account_active', None)
            if (person is None or (person['business_role']!=STAGE_ROLE[stage] and not self.is_review_admin(person)) or person['status']!='active'
                    or (not active(db,person) if active else person['expires_at']<=self.clock())
                    or 'reviews.decide' not in self.auth.permissions(db,person)):
                return [],'ASSIGNEE_ROLE_MISMATCH'
            if submitter_id is not None:
                try:
                    self.auth.require(db,person,'reviews.decide','assigned')
                    self.auth.require(db,person,'reviews.read','assigned')
                    self.auth.require(db,person,'operations.read',activity_id)
                except APIError as error:
                    if error.status!=403:raise
                    return [],'ASSIGNEE_ROLE_MISMATCH'
            people.append((stage,person['id']))
        return people,None

    def assign_review_tasks(self,db,version,people):
        for stage,user in people:
            db.execute('INSERT INTO review_tasks VALUES(?,?,?,?,1,?)',(new_id('NODE'),version,stage,user,'pending' if stage=='initial' else 'waiting'))

    def new_review(self,db,actor,activity,version,submission,bearing,evidence_ids,default_eligible=False,automatic_basis=None):
        review_version=entity(db,'activity_versions',version)
        snapshot=review_version['snapshot']
        values=snapshot.get('resolvedFields') or snapshot.get('confirmedFields') or {}
        single=(values.get('brandSubsidyOnly') is True
                and (values.get('storeBearing')=='no' or snapshot.get('storeBearingPolicy')=='per_tier')
                and bearing=='no' and values.get('type')=='brand'
                and read_activity_method(snapshot)=='brand_threshold_subsidy_single')
        approved=not single and bearing=='no' and default_eligible and not product_registration_enabled(review_version)
        if approved:people,blocked=[],None
        elif self.first_review_start:
            people,blocked=self.review_assignees(db,submitter_id=actor['id'],activity_id=activity,single=single)
            if blocked in ('ASSIGNEE_SELF_REVIEW','ASSIGNEE_SAME_INITIAL_FINAL'):blocked='ASSIGNEE_ROLE_MISMATCH'
        else:people,blocked=self.review_assignees(db,single=single)
        state='approved' if approved else 'not_started' if blocked else 'initial_pending'
        mode='rule_default' if approved else 'single_management' if single else None if blocked else 'manual'
        db.execute('INSERT INTO version_reviews VALUES(?,?,1,?,?,?)',(version,activity,state,mode,submission))
        if approved:
            history(db,actor,self.clock(),activity=activity,version=version,submission=submission,event='review.rule_default',after='approved',source='rule_default',details=({'ruleId':'company_bearing_explicit_no/v1','evidenceIds':evidence_ids,'initiatedBy':automatic_basis.get('initiatedBy',{'id':actor['id'],'roleSnapshot':None}),'basisKind':'deterministic_evidence','basis':automatic_basis} if automatic_basis is not None else {'ruleId':'company_bearing_explicit_no/v1','evidenceIds':evidence_ids,'confirmedBy':actor['id']}))
        elif not blocked:
            self.assign_review_tasks(db,version,people)
        return state,blocked

    def confirm(self,db,actor,sid,body,command_id,*,recognition=None):
        if isinstance(body,dict) and 'manualNameConfirmation' in body:
            from b_manual_name_confirmation import prepare
            prepare(self,db,actor,sid,body)
        fields(body,['expectedSubmissionRevision','analysisRunId','analysisDigest','candidateId','intent','targetActivityId','expectedActivityRevision','confirmedFields','productResolutions','duplicateAcknowledgement'],['fieldEvidenceRefs','activityMethod','primaryRecognitionBinding','agentRecognitionBinding','manualCorrections','productScopeIntent','subsidyPriceCorrections','couponFundingAnswers'])
        if 'activityMethod' in body and not valid_activity_method(body['activityMethod']):fail(422,'INVALID_REQUEST','活动方式无效')
        sub=owns_submission(db,actor,sid,self.auth,True)
        if 'couponFundingAnswers' in body and actor['id']!=sub['submitter_id']:
            fail(403,'FORBIDDEN','请由原提报人填写券档的出资备注。')
        if type(body['expectedSubmissionRevision']) is not int or body['expectedSubmissionRevision']!=sub['revision']:fail(409,'ANALYSIS_STALE','提报输入变化')
        if not isinstance(body['analysisRunId'],str):fail(422,'INVALID_REQUEST','分析ID必须为字符串')
        run=db.execute('SELECT * FROM analysis_runs WHERE id=? AND submission_id=?',(body['analysisRunId'],sid)).fetchone()
        if not run or run['input_revision']!=sub['revision'] or run['state'] not in ['succeeded','partial'] or latest_run_id(db,sid,sub['revision'])!=run['id']:fail(409,'ANALYSIS_STALE','请使用当前最新且已完成的分析')
        if agent_bad_case(db,sid,run['id'],body['candidateId']):
            fail(409,'STATE_CONFLICT','本分组已人工判定失败，请整理材料后单独重提')
        payload=json.loads(run['payload']);analysis=payload.get('result');catalog=json.loads(db.execute("SELECT value FROM b_meta WHERE key='catalogRevision'").fetchone()[0])
        if not analysis or payload.get('analysisDigest')!=body['analysisDigest'] or 'sha256:'+fingerprint(analysis)!=body['analysisDigest'] or catalog!=run['catalog_revision']:fail(409,'ANALYSIS_STALE','分析摘要或目录版本变化')
        candidate=next((c for c in analysis['candidates'] if c['candidateId']==body['candidateId']),None)
        if not candidate:fail(422,'INVALID_REQUEST','候选不属于实际分析')
        prior=db.execute('SELECT * FROM resolutions WHERE submission_id=? AND input_revision=? AND candidate_id=?',(sid,sub['revision'],body['candidateId'])).fetchone()
        if prior:
            from b_auto_resolution import already_resolved
            return 200,already_resolved(db,prior)
        if not isinstance(body['confirmedFields'],dict):
            fail(422,'INVALID_REQUEST','确认的活动信息必须是对象')
        primary_context=None
        agent_context=None
        if getattr(self.jobs, 'local_only', False) and analysis.get('modelUsed'):
            fail(409, 'LOCAL_ANALYSIS_REQUIRED', '请先使用本地识别重新分析，再核对活动。')
        if getattr(self.jobs,'model_first',False):
            from b_model_intake_confirmation import confirmation_context
            agent_context=confirmation_context(self,db,actor,sub,run,payload,body)
        elif getattr(self,'primary_recognition',None):
            from b_primary_confirmation import confirmation_context
            candidate,primary_context=confirmation_context(self,db,actor,sub,run,payload,body)
        confirmed=body['confirmedFields']
        source=sub_revision(db,sid,sub['revision'])
        brand_initiated=(confirmed.get('type')=='brand'
                         and source['form'].get('type')=='brand')
        company_omitted=brand_initiated and 'companyBearing' not in confirmed
        if company_omitted:
            source_company=candidate['fields']['companyBearing']
            # A missing declaration is unknown. Retain an explicit source with its evidence.
            company={'state':'unknown','evidenceIds':[]}
            rules_refs=candidate['fields']['rulesText']['evidenceIds']
            jiatan_refs=[e['evidenceId'] for e in analysis['evidence']
                        if e['evidenceId'] in rules_refs and explicit_jiatan_bearing(e['quote'])]
            if jiatan_refs:
                company={'state':'yes','evidenceIds':jiatan_refs}
            elif (source_company['status']=='explicit' and source_company['evidenceIds']
                    and not any(c['field']=='companyBearing' for c in analysis['conflicts'])):
                company={'state':source_company['value']['state'],'evidenceIds':source_company['evidenceIds']}
            confirmed=confirmed|{'companyBearing':company}
        fields(confirmed,['activityName','type','platformCode','sourceActivityId','startAt','endAt','brandIds','companyBearing','rulesText','brandBenefit','remark'],['storeBearing','brandSubsidyOnly'])
        per_tier=brand_initiated and 'storeBearing' not in confirmed
        validate_form(db,{k:v for k,v in confirmed.items() if k not in ('companyBearing','storeBearing','brandSubsidyOnly')})
        if 'brandSubsidyOnly' in confirmed and confirmed['brandSubsidyOnly'] is not True:
            fail(422,'INVALID_REQUEST','品牌承担声明必须由提报人明确确认')
        if 'storeBearing' in confirmed and confirmed['storeBearing'] not in ('yes','no'):
            fail(422,'INVALID_STORE_BEARING','请选择门店出钱或门店不出钱',{'field':'storeBearing'})
        if not confirmed['activityName'] or not confirmed['activityName'].strip():fail(422,'NAME_REQUIRED','请人工确认活动名称',{'field':'activityName'})
        bearing=confirmed['companyBearing'];fields(bearing,['state','evidenceIds'])
        if bearing['state'] not in ['yes','no','unknown'] or not isinstance(bearing['evidenceIds'],list):fail(422,'INVALID_REQUEST','承担状态无效')
        evidence={e['evidenceId']:e for e in analysis['evidence']}
        if any(not isinstance(e,str) or e not in evidence for e in bearing['evidenceIds']):fail(422,'INVALID_EVIDENCE','依据不属于当前实际输入')
        if bearing['state']=='no' and not bearing['evidenceIds']:fail(409,'BEARING_EVIDENCE_REQUIRED','明确无承担须实际依据；可补证据或选unknown核实')
        # Membership proves provenance, not that a quote supports no company bearing.
        source_bearing=candidate['fields']['companyBearing']
        bound=set(source_bearing['evidenceIds'])
        selected=set(bearing['evidenceIds'])
        conflict_ids={eid for conflict in analysis['conflicts'] if conflict['field']=='companyBearing' for eid in conflict['evidenceIds']}
        default_eligible=(not company_omitted and bearing['state']=='no' and source_bearing['status']=='explicit' and source_bearing['value']['state']=='no' and bool(selected) and selected<=bound and not selected&conflict_ids)
        bearing_verification={'defaultApprovalEligible':default_eligible,'state':'verified_explicit_no' if default_eligible else 'requires_management_verification','reasonCode':None if default_eligible else 'BEARING_EVIDENCE_UNVERIFIED' if bearing['state']=='no' else 'COMPANY_BEARING_REQUIRES_REVIEW','sourceField':source_bearing,'selectedEvidenceIds':bearing['evidenceIds'],'humanConfirmedState':bearing['state']}
        rows={r['rowId']:r for r in candidate['productRows']};resolutions=body['productResolutions']
        if not isinstance(resolutions,list) or any(not isinstance(r,dict) for r in resolutions):fail(422,'INVALID_REQUEST','商品确认数组无效')
        seen=set();products=[];unresolved=[]
        for r in resolutions:
            fields(r,['rowId','decision','productId'])
            if not isinstance(r['rowId'],str) or r['rowId'] not in rows or r['rowId'] in seen or r['decision'] not in ['link','unresolved']:fail(422,'INVALID_REQUEST','商品行确认无效')
            seen.add(r['rowId'])
            if r['decision']=='link':
                item=entity(db,'products',r['productId'])
                require_product_identity(rows[r['rowId']],item)
                products.append(r['productId'])
            else:
                if r['productId'] is not None:fail(422,'INVALID_REQUEST','未解决商品不能有已关联ID')
                unresolved.append(r['rowId'])
        if seen!=set(rows):fail(422,'INVALID_REQUEST','须逐行确认或明确未解决')
        coupon_schedule=None
        manual_corrections={}
        intake_options={}
        coupon_review_input=None
        accepts_corrections=bool(agent_context) and body['intent'] in ('create','propose_change')
        if any(key in body for key in ('manualCorrections','productScopeIntent','subsidyPriceCorrections')) and not accepts_corrections:
            fail(422,'INVALID_REQUEST','人工补正须用于已核对 Agent 结果的新建或变更版本')
        if 'couponFundingAnswers' in body and not accepts_corrections:
            fail(422,'INVALID_COUPON_FUNDING_ANSWER','券档出资备注仅用于核对本组的新建或变更版本。')
        if confirmed.get('brandSubsidyOnly') and not accepts_corrections:
            fail(422,'INVALID_REQUEST','品牌承担声明须用于已核对 Agent 结果的活动版本')
        if accepts_corrections:
            if 'productScopeIntent' in body and (not body['productScopeIntent'] or not valid_product_scope(body['productScopeIntent'])):
                fail(422,'INVALID_PRODUCT_SCOPE_INTENT','请选择有效的商品适用范围')
            if 'productScopeIntent' in body and actor['id']!=sub['submitter_id']:
                fail(403,'FORBIDDEN','请由原提报人确认商品适用范围')
            metadata=next(item for item in payload['agentRecognition']['candidateMetadata'] if item['candidateId']==candidate['candidateId'])
            method_correction=agent_context.get('activityMethodCorrection')
            agent_method=method_correction['toMethod'] if method_correction else metadata['activityMethod']['value']
            agent_rules=candidate['fields']['rulesText']
            chosen_method=body.get('activityMethod')
            coupon_scope_required=(has_coupon_scope(agent_method,agent_rules['value'])
                                  or has_coupon_scope(chosen_method,confirmed.get('rulesText')))
            if (requires_store_bearing(agent_method,agent_rules['value'])
                    or requires_store_bearing(chosen_method,confirmed.get('rulesText'))):
                if not per_tier and confirmed.get('storeBearing') not in ('yes','no'):
                    fail(422,'STORE_BEARING_REQUIRED','满减活动须由提报人确认门店是否出钱',{'field':'storeBearing'})
                if actor['id']!=sub['submitter_id']:
                    fail(403,'STORE_BEARING_SUBMITTER_REQUIRED','请由原提报人确认门店是否出钱')
            bearing_refs=[eid for eid in agent_rules['evidenceIds'] if explicit_jiatan_bearing(evidence[eid]['quote'])]
            if bearing_refs:
                confirmed=confirmed|{'companyBearing':{'state':'yes','evidenceIds':bearing_refs}}
            if confirmed.get('brandSubsidyOnly'):
                if (not brand_initiated or chosen_method!='brand_threshold_subsidy_single'
                        or agent_method in PRODUCT_REGISTRATION_METHODS
                        or agent_method in ('brand_threshold_subsidy_dual','brand_threshold_subsidy_triple','category_threshold_subsidy_dual')
                        or metadata['activityMethod']['status']=='conflict' or agent_rules['status']=='conflict'):
                    fail(422,'BRAND_SUBSIDY_ONLY_CONFLICT','一次管理审核适用于已确认的品牌补贴活动。')
                if (actor['id']!=sub['submitter_id'] or confirmed.get('storeBearing')=='yes'
                        or confirmed['companyBearing']['state']=='yes' or source_bearing['status']=='conflict'
                        or source_bearing['value']['state']=='yes' or conflict_ids
                        or explicit_jiatan_bearing(confirmed.get('rulesText'))):
                    fail(422,'BRAND_SUBSIDY_ONLY_CONFLICT','原提报人须核对门店和公司均不出补贴、单商品无需降价，且与材料依据一致。')
                confirmed=confirmed|{'companyBearing':{'state':'no','evidenceIds':[]}}
            default_eligible=False
            bearing_verification.update(defaultApprovalEligible=False,state='requires_management_verification',
                                        reasonCode='COMPANY_BEARING_REQUIRES_REVIEW',
                                        humanConfirmedState=confirmed['companyBearing']['state'],
                                        selectedEvidenceIds=confirmed['companyBearing']['evidenceIds'])
            subsidy_prices=source_prices(analysis,candidate)
            if 'subsidyPriceCorrections' in body:
                if actor['id']!=sub['submitter_id']:
                    fail(403,'FORBIDDEN','请由原提报人核对补贴价格')
                subsidy_prices=apply_corrections(subsidy_prices,body['subsidyPriceCorrections'],actor['id'],iso(self.clock()))
            intake_options={'coupon_scope_required':coupon_scope_required,'subsidy_prices':subsidy_prices,
                            'scope_intent':body.get('productScopeIntent')}
            from b_coupon_schedule import observed_schedule
            from b_intake_corrections import checked_corrections,merge_coupon_dates
            from b_coupon_review import coupon_sources, explicit_mix, threshold_coupon
            from b_coupon_details import nonbrand_funding
            platforms=projected_declarations(source, confirmed.get('platformCode'))['applicablePlatformCodes']
            agent_coupon = threshold_coupon(agent_method,agent_rules['value'])
            if agent_coupon and chosen_method in PRODUCT_REGISTRATION_METHODS and coupon_sources(
                    analysis,agent_rules['evidenceIds'],platforms,mixed_only=False,product_rows=candidate['productRows']):
                fail(422,'COUPON_AND_PRICE_GROUP_CONFLICT','本组材料已识别出满减券档，请核对活动玩法。')
            coupon = chosen_method not in PRODUCT_REGISTRATION_METHODS and (
                agent_coupon
                or threshold_coupon(chosen_method,confirmed.get('rulesText'))
                or explicit_mix(confirmed['activityName']))
            if coupon:
                options=coupon_sources(analysis,agent_rules['evidenceIds'],platforms,mixed_only=False,
                                       product_rows=candidate['productRows'])
                funding=explicit_mix(agent_rules['value'],confirmed.get('rulesText'),confirmed['activityName']) or any(
                    option['storeFundingRequired'] for option in options)
                if actor['id']!=sub['submitter_id']:
                    fail(403,'STORE_BEARING_SUBMITTER_REQUIRED','请由原提报人确认满减券的门店出资')
                if confirmed.get('brandSubsidyOnly') and (funding or any(nonbrand_funding(o) for o in options)):
                    fail(422,'BRAND_SUBSIDY_ONLY_CONFLICT','有券档列出品牌以外的出资方，请逐档审核。')
                if funding and not per_tier and confirmed.get('storeBearing')!='yes':
                    fail(422,'MIXED_COUPON_STORE_BEARING_REQUIRED','混补券需要门店出资，请确认门店出钱。',{'field':'storeBearing'})
                coupon_review_input={'options':options,'storeFundingRequired':funding}
                intake_options['coupon_scope_required']=True
            if 'manualCorrections' in body:
                if not isinstance(body['manualCorrections'],dict):
                    fail(422,'INVALID_REQUEST','人工补正内容无效')
                if body['manualCorrections'].get('products'):
                    self.auth.require(db,actor,'operations.read')
                    if '*' not in grant_scopes(db,actor,self.auth,'operations.read'):
                        fail(403,'FORBIDDEN','当前账号无权查询完整公司商品目录')
                originals=verify_declaration_binding(db,sub,run,analysis,body['analysisDigest'],source)
                selected_codes=[entity(db,'products',pid).get('internalCode') for pid in products]
                allowed={mid:originals[mid] for mid in originals if can_material(db,actor,mid,self.auth)}
                manual_corrections=checked_corrections(body['manualCorrections'],allowed,recognition,candidate,selected_codes)
                if manual_corrections:
                    default_eligible=False
                    bearing_verification.update(defaultApprovalEligible=False,state='requires_management_verification',
                                                reasonCode='COMPANY_BEARING_REQUIRES_REVIEW')
            try:
                original_schedule=observed_schedule(analysis,source)
                coupon_schedule=merge_coupon_dates(original_schedule,manual_corrections.get('couponTiers',[]),confirmed,source)
            except ValueError:
                fail(422,'COUPON_PERIOD_MISMATCH','请核对主活动与各档子券的有效期。')
            if coupon_schedule and chosen_method in PRODUCT_REGISTRATION_METHODS:
                fail(422,'COUPON_AND_PRICE_GROUP_CONFLICT','本组混合了商品单价调整与满减券，请重新识别后分别建档。')
            if coupon_schedule and coupon_review_input is None:
                coupon_review_input={'options':[],'storeFundingRequired':False}
            if coupon_schedule:
                if confirmed.get('brandSubsidyOnly') and any(nonbrand_funding(t) for t in coupon_schedule['tiers']):
                    fail(422,'BRAND_SUBSIDY_ONLY_CONFLICT','有券档列出品牌以外的出资方，请逐档审核。')
                if 'startAt' not in coupon_schedule:
                    fail(422,'ACTIVITY_DATE_REQUIRED','请填写主活动开始日期，以确认各档子券的年份。',{'field':'startAt'})
                confirmed=confirmed|{key:coupon_schedule[key] for key in ('startAt','endAt')}
            if coupon_review_input is not None:
                from b_coupon_funding_confirmation import apply_answers
                options=list(coupon_schedule['tiers']) if coupon_schedule else []
                if not original_schedule:
                    options.extend(coupon_review_input['options'])
                apply_answers(body.get('couponFundingAnswers',[]),options,coupon_schedule,
                              source_hashes=funding_material_hashes(db,source,actor,self.auth),run_id=run['id'],
                              candidate_id=candidate['candidateId'],actor_id=actor['id'],now=iso(self.clock()),
                              revision=sub['revision'],brand_subsidy_only=bool(confirmed.get('brandSubsidyOnly')))
            elif body.get('couponFundingAnswers'):
                fail(422,'INVALID_COUPON_FUNDING_ANSWER','当前分组没有可填写出资备注的券档。')
        if type(body['duplicateAcknowledgement']) is not bool:fail(422,'INVALID_REQUEST','查重风险确认须布尔值')
        field_refs=[]
        if 'fieldEvidenceRefs' in body:
            originals=verify_declaration_binding(db,sub,run,analysis,body['analysisDigest'],source)
            field_refs=self.verified_field_refs(db,actor,body['fieldEvidenceRefs'],confirmed,payload,candidate,
                                                source,originals,'INVALID_EVIDENCE')
        if company_omitted:
            bearing_verification.update(humanConfirmedState=None,companyDeclarationProvided=False)
        outcome=self.persist_resolution(db,actor,sub,run,analysis,candidate,confirmed,products,unresolved,body['intent'],body['targetActivityId'],body['expectedActivityRevision'],body['duplicateAcknowledgement'],command_id,body['analysisDigest'],default_eligible,bearing_verification,manual_product_resolutions=resolutions,field_refs=field_refs,activity_method=body.get('activityMethod'),method_confirmed='activityMethod' in body,date_evidence={'fields':{},'registrationSources':[]} if primary_context or agent_context else None,coupon_schedule=coupon_schedule,manual_corrections=manual_corrections,coupon_review_input=coupon_review_input,per_tier=per_tier,submitted_fields=body['confirmedFields'],**intake_options)
        if agent_context:
            from b_model_intake_confirmation import record_confirmation
            return record_confirmation(self,db,actor,sub,run,agent_context,outcome)
        if primary_context:
            from b_primary_confirmation import record_confirmation
            return record_confirmation(self,db,actor,sub,run,analysis,primary_context,outcome)
        return outcome
    def auto_resolve(self,db,actor,sid,body,command_id):
        if getattr(self.jobs,'model_first',False):fail(409,'AGENT_CONFIRMATION_REQUIRED','请核对 Agent 识别结果，不使用本地规则自动建档')
        if getattr(self.jobs, 'local_only', False):
            fail(409, 'LOCAL_CONFIRMATION_REQUIRED', '本地识别结果须人工核对后建档。')
        if getattr(self,'primary_recognition',None):fail(409,'PRIMARY_RECOGNITION_REQUIRED','请先完成 DeepSeek 主识别并核对，不使用本地规则自动建档')
        from b_auto_resolution import automatic
        return automatic(self,db,actor,sid,body,command_id)
    def possible_duplicate(self,db,actor,confirmed,exempt=(),*,skip_activity=None):
        readable='*' in grant_scopes(db,actor,self.auth,'operations.read')
        ready=db.execute("SELECT value FROM metadata WHERE key='sourceReady'").fetchone()
        if 'operations.read' not in self.auth.permissions(db,actor) or not readable or not ready or not json.loads(ready[0]):
            fail(409,'DUPLICATE_SCOPE_UNCONFIRMED','当前无法核对活动重复范围')
        name=confirmed['activityName'].strip().casefold()
        pair=(confirmed['platformCode'],confirmed['sourceActivityId'])
        for row in db.execute('SELECT body FROM activities'):
            activity=json.loads(row[0])
            if activity['id']==skip_activity:continue
            pairs={(ext.get('platformCode'),ext.get('id')) for ext in activity.get('externalIds',[])}
            if all(pair) and pair in pairs:
                return True
            if (activity.get('name') or '').strip().casefold()==name and activity['id'] not in exempt and not (all(pair) and pairs and all(p[0]==pair[0] and p[1] and p[1]!=pair[1] for p in pairs)):
                return True
        return False

    def persist_resolution(self,db,actor,sub,run,analysis,candidate,confirmed,products,unresolved,intent,target,expected_target_revision,duplicate_ack,command_id,analysis_digest,default_eligible,bearing_verification,automatic=None,manual_product_resolutions=None,field_refs=None,date_evidence=None,activity_method=None,method_confirmed=False,coupon_schedule=None,manual_corrections=None,coupon_scope_required=False,subsidy_prices=(),scope_intent=None,coupon_review_input=None,per_tier=False,submitted_fields=None):
        sid=sub['id'];bearing=confirmed['companyBearing']
        submitted_confirmation=confirmed if submitted_fields is None else submitted_fields
        confirmed=dict(confirmed)
        incomplete_identity={}
        if intent=='create' and self.activity_date_evidence:
            from b_activity_dates import collect,for_creation,overrides
            if date_evidence is None:
                original=sub_revision(db,sid,sub['revision'])
                bound=verify_declaration_binding(db,sub,run,analysis,analysis_digest,original)
                payload=json.loads(run['payload'])
                observed,registration=collect(db,confirmed,payload,candidate,run,original,bound,
                    allowed=lambda mid:can_material(db,actor,mid,self.auth))
                updates,date_evidence=for_creation(confirmed,observed,registration,candidate,analysis)
                confirmed.update(updates)
            incomplete_identity.update(overrides(confirmed,date_evidence))
        if intent in ('create','propose_change'):
            for field,label in (('startAt','开始日期'),('endAt','结束日期')):
                if not confirmed.get(field):
                    fail(422,'ACTIVITY_DATE_REQUIRED',f'请填写活动{label}',{'field':field})
        if confirmed['sourceActivityId'] and not confirmed['platformCode']:
            confirmed['sourceActivityId']=None
            incomplete_identity['platformCode']='SOURCE_PLATFORM_UNVERIFIED'
        identity_values=[confirmed[k] for k in ['platformCode','sourceActivityId']]+[datetime.fromisoformat(confirmed[k].replace('Z','+00:00')).astimezone(timezone.utc).isoformat() if confirmed[k] else None for k in ['startAt','endAt']]+[sorted(confirmed['brandIds'] or [])]
        complete=all(identity_values)
        identity=fingerprint(identity_values) if complete else None
        existing=db.execute('SELECT activity_id FROM activity_identities WHERE fingerprint=?',(identity,)).fetchone() if identity else None
        if intent not in ['create','link_source','propose_change']:fail(422,'INVALID_REQUEST','确认意图无效')
        if existing:
            # Keep disclosure aligned with GET activities: the action name alone
            # (or an unrelated/specific resource grant) does not allow broad read.
            readable='*' in grant_scopes(db,actor,self.auth,'operations.read')
            if 'operations.read' not in self.auth.permissions(db,actor) or not readable:
                fail(409,'IDENTITY_CONFLICT_UNAVAILABLE','联合身份冲突，当前不可读取目标')
        source=sub_revision(db,sid,sub['revision'])
        try:formula=input_formula_version(source)
        except ValueError:fail(409,'ANALYSIS_STALE','原始输入绑定不符')
        method=activity_method if method_confirmed else read_activity_method(source)
        if not valid_activity_method(method):fail(422,'INVALID_REQUEST','活动方式无效')
        verify_declaration_binding(db,sub,run,analysis,analysis_digest,source)
        declarations={key:source[key] for key in DECLARATION_FIELDS if key in source}
        if scope_intent:
            declarations[SCOPE_FIELD]=scope_intent
        declarations['activityMethod']=method
        declarations['activityMethodConfirmation']={'status':'confirmed' if method else 'unconfirmed','source':'explicit_confirmation' if method_confirmed else 'submission_declaration','actorId':actor['id'],'confirmedAt':iso(self.clock()) if method else None}
        if intent=='create':
            require_applicable_platform(confirmed|{'platformCode':source['form'].get('platformCode')},source)
        if (confirmed.get('platformCode') in PLATFORM_CODES
                and declarations.get(PLATFORM_FIELD) and confirmed['platformCode'] not in declarations[PLATFORM_FIELD]):
            fail(422,'ACTIVITY_PLATFORM_CONFLICT','报名编号所属平台不在提报的适用平台内，请修正提报并重新识别。')
        scope=automatic['productApplicability'] if automatic is not None else declared_scope(
            confirmed,declarations,products or (manual_corrections or {}).get('products'),unresolved,
            allow_broad=method not in PRODUCT_REGISTRATION_METHODS)
        if needs_correction(subsidy_prices):
            incomplete_identity['subsidyPrices']='SUBSIDY_PRICE_CONFIRMATION_REQUIRED'
        if analysis.get('modelUsed') is True and candidate['fields']['rulesText']['status']=='conflict' and not confirmed.get('rulesText'):
            incomplete_identity['rulesText']='RULES_UNCONFIRMED'
        pending=(automatic['pendingItems'] if automatic is not None
                 else pending_items(confirmed,scope,unresolved,incomplete_identity,activity_method=method))
        materials=list(source['allMaterialIds'])
        if automatic is None:
            confirmation=add_material(db,actor,self.clock(),'人工确认.json','application/vnd.jiatan.form+json',canon(submitted_confirmation).encode(),'controlled_confirmation','FORM-'+command_id)
            origin={'resolutionMode':'manual','ruleVersion':None,'resolutionMaterialId':confirmation['materialId']}
        else:
            confirmation=add_material(db,actor,self.clock(),'系统自动处理决议.json','application/vnd.jiatan.resolution+json',canon(automatic).encode(),'controlled_resolution','AUTO-'+command_id)
            automatic=automatic|{'resolutionMaterialId':confirmation['materialId'],'resolutionSha256':confirmation['sha256']}
            origin={'resolutionMode':'automatic','ruleVersion':automatic['ruleVersion'],'resolutionMaterialId':confirmation['materialId'],'resolutionSha256':confirmation['sha256'],'productApplicability':automatic['productApplicability']}
        materials.append(confirmation['materialId'])
        origin.update(pendingItems=pending,analysisRunId=run['id'],analysisDigest=analysis_digest)
        if intent=='create':
            if confirmed['platformCode'] and confirmed['sourceActivityId'] and self.supplement_identity_exists(db,None,confirmed,include_current=False):
                fail(409,'DUPLICATE_ACTIVITY','报名身份已在其它补录版本中核对')
            if existing:fail(409,'DUPLICATE_ACTIVITY','已存在相同联合身份，请关联或保存变更',{'activityId':existing[0]})
            if automatic is None and not duplicate_ack:
                from b_grouping import siblings,sibling_activity_ids
                same_name=confirmed['activityName']==candidate['fields']['activityName']['value']
                exempt=sibling_activity_ids(db,run,siblings(json.loads(run['payload']),candidate)) if same_name and not confirmed['sourceActivityId'] else ()
                if self.possible_duplicate(db,actor,confirmed,exempt):
                    fail(409,'DUPLICATE_ACK_REQUIRED','存在可能重复的活动，请核对后明确确认')
            if target is not None or expected_target_revision is not None:fail(422,'INVALID_REQUEST','新建不能附已有目标版本')
            aid=new_id('ACT');vid=new_id('VER');number=1;previous=None;old_activity=None
        else:
            self.auth.require(db,actor,'operations.read')
            aid=target;old_activity=entity(db,'activities',aid)
            if type(expected_target_revision) is not int or expected_target_revision!=old_activity['revision']:fail(409,'STATE_CONFLICT','目标活动版本变化')
            if existing and existing[0]!=aid:fail(409,'DUPLICATE_ACTIVITY','联合身份命中其他活动',{'activityId':existing[0]})
            previous=old_activity.get('displayVersionId') or old_activity['currentReviewVersionId']
            if intent=='link_source':
                if method and old_activity.get('activityMethod')!=method:fail(409,'SOURCE_CONFLICT','活动方式不同，须保存独立变更版')
                compare={'activityName':'name','type':'type','platformCode':'platformCode','startAt':'startAt','endAt':'endAt','rulesText':'rules','brandBenefit':'brandBenefit'}
                if any(confirmed.get(k) not in (None,'') and ((datetime.fromisoformat(confirmed[k].replace('Z','+00:00'))!=datetime.fromisoformat(old_activity[field].replace('Z','+00:00'))) if k in ['startAt','endAt'] and old_activity.get(field) else confirmed[k]!=old_activity.get(field)) for k,field in compare.items()) or confirmed['brandIds'] and set(confirmed['brandIds'])!=set(old_activity['brandIds']):fail(409,'SOURCE_CONFLICT','来源与原条件不同，须保存独立变更版')
                no_new_company_term=(bearing_verification.get('companyDeclarationProvided') is False
                                     and bearing['state']=='unknown' and candidate['fields']['companyBearing']['status']!='conflict')
                if not no_new_company_term and bearing['state']!=old_activity.get('costEvidence',{}).get('state','unknown'):fail(409,'SOURCE_CONFLICT','承担条件不同，须保存独立变更版')
                known={r[0] for r in db.execute('SELECT product_id FROM activity_products WHERE activity_id=?',(aid,))}
                if not set(products)<=known:fail(409,'SOURCE_CONFLICT','关联商品范围不同，须保存变更版')
                vid=previous;self.attach_source(db,vid,sid,sub['revision'],materials)
                linked=projected_declarations(source,source['form'].get('platformCode')) if declarations else {}
                origin.update(linked)
                event=history(db,actor,self.clock(),activity=aid,version=vid,submission=sid,event='activity.source_linked',details={'materialIds':materials,'inputRevision':sub['revision'],'analysisRunId':run['id'],'analysisDigest':analysis_digest,'candidateId':candidate['candidateId'],'confirmationMaterialId':confirmation['materialId'] if automatic is None else None,**({'fieldEvidenceRefs':field_refs} if field_refs else {}),**linked,**({'resolutionBinding':automatic} if automatic is not None else {})},source='automatic_rule' if automatic is not None else 'manual')
                return self.finish_resolution(db,actor,sub,candidate,analysis,aid,vid,event,'linked',origin,bearing_verification)
            number=max([json.loads(r[0]).get('versionNo',0) for r in db.execute('SELECT body FROM activity_versions') if json.loads(r[0]).get('activityId')==aid],default=0)+1;vid=new_id('VER')
        if intent=='propose_change' and previous:
            base=entity(db,'activity_versions',previous).get('snapshot',{})
            if 'activityMethod' not in source and not method_confirmed:
                declarations['activityMethod']=read_activity_method(base)
                declarations['activityMethodConfirmation']=base.get('activityMethodConfirmation',{'status':'unconfirmed'})
                if automatic is None:
                    pending=pending_items(confirmed,scope,unresolved,incomplete_identity,activity_method=declarations['activityMethod'])
                    origin['pendingItems']=pending
            if self.allow_submission_declarations or declarations or any(key in base for key in DECLARATION_FIELDS):
                declarations=projected_declarations(base,version_platform(base))|declarations
            if automatic is None:
                scope=declared_scope(confirmed,declarations,products or (manual_corrections or {}).get('products'),unresolved,
                                     allow_broad=declarations['activityMethod'] not in PRODUCT_REGISTRATION_METHODS)
                pending=pending_items(confirmed,scope,unresolved,incomplete_identity,activity_method=declarations['activityMethod'])
                origin['pendingItems']=pending
        if requires_store_bearing(declarations['activityMethod'],confirmed.get('rulesText')) or (manual_corrections or {}).get('couponTiers'):
            if automatic is not None or (not per_tier and confirmed.get('storeBearing') not in ('yes','no')):
                fail(422,'STORE_BEARING_REQUIRED','满减活动须由提报人确认门店是否出钱',{'field':'storeBearing'})
            if actor['id']!=sub['submitter_id']:
                fail(403,'STORE_BEARING_SUBMITTER_REQUIRED','请由原提报人确认门店是否出钱')
        snapshot={'products':list(dict.fromkeys(products)),'unresolvedRows':unresolved,'rules':confirmed['rulesText'],
                  'brandBenefit':confirmed['brandBenefit'],'costEvidence':bearing,'pendingItems':pending,'productApplicability':scope}|declarations
        if per_tier and (coupon_review_input or coupon_schedule or requires_store_bearing(declarations['activityMethod'], confirmed.get('rulesText'))):
            snapshot['storeBearingPolicy']='per_tier'
        if coupon_scope_required:
            snapshot['couponScopeRequired']=True
        if subsidy_prices:
            snapshot['subsidyPrices']=subsidy_prices
        if scope['kind']=='all_products':
            snapshot['productListState']='scope_only'
        if coupon_schedule:
            snapshot['couponSchedule']=coupon_schedule
        if coupon_review_input:
            snapshot['couponReviewInput']=coupon_review_input
        if manual_corrections:
            snapshot['manualCorrections']=manual_corrections
        if date_evidence and (date_evidence['fields'] or date_evidence['registrationSources']):
            snapshot['activityDateBasis']=date_evidence
        if automatic is None:
            snapshot.update(confirmedFields=confirmed,manualConfirmation={'actorId':actor['id'],'analysisDigest':analysis_digest,'runId':run['id'],'originalCandidate':candidate,'productResolutions':manual_product_resolutions,'confirmationMaterialId':confirmation['materialId'],'bearingVerification':bearing_verification,**({'fieldEvidenceRefs':field_refs} if field_refs else {})})
        else:
            snapshot.update(resolvedFields=confirmed,resolutionBinding=automatic,productApplicability=automatic['productApplicability'])
            if automatic['productApplicability']['kind']=='all_products':snapshot['productListState']='scope_only'
        snapshot.update(dimensions({},snapshot))
        version={'id':vid,'activityId':aid,'versionNo':number,'submissionId':sid,'previousVersionId':previous,'basedOnVersionId':previous,'snapshot':snapshot,'materialIds':materials,'materialRefs':materials,'changeSummary':('系统按规则处理本次材料' if automatic is not None else confirmed['remark'] or '人工核对本次材料'),'revision':1}
        put(db,'activity_versions',version);self.attach_source(db,vid,sid,sub['revision'],materials)
        if intent=='create':
            state,review_blocked=self.new_review(db,actor,aid,vid,sid,bearing['state'],bearing['evidenceIds'],default_eligible and not pending,(bearing_verification|{'initiatedBy':automatic['initiatedBy']}) if automatic is not None else None)
            snapshot['reviewBlockedReason']=review_blocked
            put(db,'activity_versions',version)
            origin.update(reviewState=state,reviewBlockedReason=review_blocked)
            activity={'id':aid,'name':confirmed['activityName'],'type':confirmed['type'],'platformCode':confirmed['platformCode'],'projectName':None,'level':None,'startAt':confirmed['startAt'],'endAt':confirmed['endAt'],'rawDate':None,'submitter':{'id':actor['id'],'displayName':actor['name']},'submittedAt':iso(sub['received_at']),'externalIds':[{'platformCode':confirmed['platformCode'],'id':confirmed['sourceActivityId'],'sourceRef':sid}] if confirmed['sourceActivityId'] else [],'currentReviewVersionId':vid,'effectiveExecutionVersionId':None,'displayVersionId':vid,'reviewVersionId':vid,'approvedVersionId':vid if state=='approved' else None,'effectiveVersionId':None,'pendingChangeVersionIds':[],'revision':1,'provenance':{'kind':'automatic_rule' if automatic is not None else 'manual_confirmed','sourceRef':sid,'locator':None,'observedAt':iso(self.clock())},'brandIds':confirmed['brandIds'] or [],'ruleSummary':confirmed['rulesText'],'rules':confirmed['rulesText'],'brandBenefit':confirmed['brandBenefit'],'costEvidence':bearing,'review':{'stage':'initial' if state=='initial_pending' else None,'result':'pending' if state=='initial_pending' else state,'approvalMode':self.review_view(db,vid)['approvalMode']},'registration':{'state':'unknown','sourceRef':None,'checkedAt':None,'versionId':None},'execution':{'state':'unknown','sourceRef':None,'checkedAt':None,'versionId':None},'materials':[],'history':[],'changeSummary':version['changeSummary']}
            if automatic is not None:
                activity['productApplicability']=automatic['productApplicability']
                if automatic['productApplicability']['kind']=='all_products':activity['productListState']='scope_only'
            activity.update(dimensions(activity,snapshot))
            activity.update(declarations,pendingItems=pending,productApplicability=scope,reviewState=state,reviewBlockedReason=review_blocked)
            if scope['kind']=='all_products':
                activity['productListState']='scope_only'
            if 'activityDateBasis' in snapshot:activity['activityDateBasis']=snapshot['activityDateBasis']
            put(db,'activities',activity)
            for bid in confirmed['brandIds'] or []:db.execute('INSERT INTO activity_brands VALUES(?,?,?)',(aid,bid,canon({'activityId':aid,'brandId':bid,'complete':True,'provenance':activity['provenance']})))
            from seed import metric
            for pid in sorted(set(products)):db.execute('INSERT INTO activity_products VALUES(?,?,?)',(aid,pid,canon({'activityId':aid,'productId':pid,'complete':not unresolved,'activityPrice':None,'currency':None,'priceUnit':None,'historicalSales':metric(),'latestActivityId':None,'provenance':activity['provenance']})))
            if identity:db.execute('INSERT INTO activity_identities VALUES(?,?)',(identity,aid))
            if scope['kind']=='unknown':db.execute('INSERT INTO incomplete_relations VALUES(?,?)',(aid,canon({'rowIds':unresolved,'brandIds':confirmed['brandIds'] or [],'platformCode':confirmed['platformCode']})))
            if scope['kind']=='all_products':db.execute('INSERT INTO incomplete_relations VALUES(?,?)',(aid,canon({'reasonCode':'SCOPE_NOT_ENUMERATED','productListState':'scope_only','brandIds':confirmed['brandIds'],'platformCode':confirmed['platformCode']})))
        else:
            state='not_started';db.execute('INSERT INTO version_reviews VALUES(?,?,1,?,NULL,?)',(vid,aid,state,sid))
            old_activity['pendingChangeVersionIds']=[*old_activity.get('pendingChangeVersionIds',[]),vid];old_activity['revision']+=1;put(db,'activities',old_activity)
        event=history(db,actor,self.clock(),activity=aid,version=vid,submission=sid,event='activity.created' if intent=='create' else 'activity.change_saved',after=state,details={'inputRevision':sub['revision'],'analysisRunId':run['id'],'analysisDigest':analysis_digest,'materialIds':materials,'bearingVerification':bearing_verification,**({'fieldEvidenceRefs':field_refs} if field_refs else {}),**({'resolutionBinding':automatic} if automatic is not None else {})},source='automatic_rule' if automatic is not None else 'manual')
        return self.finish_resolution(db,actor,sub,candidate,analysis,aid,vid,event,'created' if intent=='create' else 'change_saved',origin,bearing_verification)
    def attach_source(self,db,vid,sid,revision,materials):
        db.execute('INSERT OR IGNORE INTO version_sources VALUES(?,?,?,?)',(vid,sid,revision,canon(materials)))
    def finish_resolution(self,db,actor,sub,candidate,analysis,aid,vid,event,outcome,origin=None,verification=None):
        review=self.review_view(db,vid)
        value={'submissionId':sub['id'],'revision':sub['revision'],'candidateId':candidate['candidateId'],'activityId':aid,'versionId':vid,'outcome':outcome,'historyEventId':event,**(origin or {}),
               'reviewState':review['state'],'reviewBlockedReason':review.get('reviewBlockedReason')}
        db.execute('INSERT INTO resolutions VALUES(?,?,?,?,?,?)',(sub['id'],sub['revision'],candidate['candidateId'],aid,vid,canon(value)))
        remaining,done=outstanding_agent_groups(db,sub,origin.get('analysisRunId') if origin else None,analysis['candidates'])
        state='partially_resolved' if len(done)<len(analysis['candidates']) else 'resolved'
        db.execute('UPDATE b_submissions SET state=?,activity_id=?,version_id=? WHERE id=?',(state,aid,vid,sub['id']))
        if verification is None:verification=entity(db,'activity_versions',vid).get('snapshot',{}).get('manualConfirmation',{}).get('bearingVerification')
        if origin and origin['resolutionMode']=='automatic':
            return 201,value|{'inputRevision':sub['revision'],'reviewState':self.review_view(db,vid)['state'],'reasonCodes':[],'remainingCandidateIds':remaining,'reviewEligibility':verification}
        return 201,value|{'state':state,'review':self.review_view(db,vid),'reviewEligibility':verification,'remainingGroups':remaining}
    def supplement_target(self,db,actor,aid,vid):
        self.auth.require(db,actor,'activities.propose')
        self.auth.require(db,actor,'operations.read')
        base=entity(db,'activity_versions',vid)
        if base['activityId']!=aid or not can_version(db,actor,vid,self.auth):
            fail(404,'NOT_FOUND','版本不存在或不可访问')
        own=db.execute("""SELECT 1 FROM resolutions r JOIN b_submissions s ON s.id=r.submission_id
            WHERE r.activity_id=? AND r.version_id=? AND s.submitter_id=? AND s.state<>'deleted'""",
            (aid,vid,actor['id'])).fetchone()
        if not own and not self.is_review_admin(actor):fail(404,'NOT_FOUND','版本不存在或不可访问')
        return base

    def latest_supplement(self,db,aid,base_id):
        row=db.execute("""SELECT body FROM activity_versions
            WHERE json_extract(body,'$.activityId')=? AND json_extract(body,'$.snapshot.supplementBinding.baseVersionId')=?
            ORDER BY rowid DESC LIMIT 1""",(aid,base_id)).fetchone()
        return json.loads(row[0]) if row else None

    def source_candidate(self,db,sub,source):
        run=db.execute('SELECT * FROM analysis_runs WHERE id=? AND submission_id=?',
                       (source['analysisRunId'],sub['id'])).fetchone()
        if not run or run['input_revision']!=source['inputRevision'] or run['state'] not in ('succeeded','partial'):
            fail(409,'ANALYSIS_STALE','原分组分析绑定已变化')
        payload=json.loads(run['payload'])
        analysis=payload.get('result')
        candidate=next((c for c in (analysis or {}).get('candidates',[]) if c['candidateId']==source['candidateId']),None)
        if not candidate or payload.get('analysisDigest')!=source['analysisDigest']:
            fail(409,'ANALYSIS_STALE','原分组分析或摘要已变化')
        original=sub_revision(db,sub['id'],source['inputRevision'])
        bound=verify_declaration_binding(db,{'id':sub['id'],'revision':source['inputRevision']},
                                         run,analysis,source['analysisDigest'],original)
        return payload,candidate,original,bound

    def verified_field_refs(self,db,actor,refs,values,payload,candidate,source,originals,invalid_code):
        from b_auto_resolution import original_value,field_source_value
        if not isinstance(refs,list) or len(refs)>len(values):
            fail(422,invalid_code,'字段依据无效')
        evidence={e['evidenceId']:e for e in payload['result']['evidence']}
        aliases={'activityName':{'活动名称','活动名'},'type':{'活动类型'},'platformCode':{'平台'},
                 'sourceActivityId':{'平台活动编号','活动编号','报名id'},'startAt':{'开始时间'},
                 'endAt':{'结束时间'},'rulesText':{'优惠机制'},'brandIds':{'品牌ID'},
                 'brandBenefit':{'品牌利益点'},'remark':{'备注'}}
        seen=set()
        for item in refs:
            if not isinstance(item,dict) or set(item)!={'field','evidenceIds'}:
                fail(422,invalid_code,'字段依据无效')
            field,ids=item['field'],item['evidenceIds']
            if (not isinstance(field,str) or field not in values or field not in aliases or field in seen
                    or not isinstance(ids,list) or not ids or len(ids)>48
                    or any(not isinstance(e,str) for e in ids) or len(set(ids))!=len(ids)):
                fail(422,invalid_code,'字段依据无效')
            seen.add(field)
            candidate_field=candidate['fields'].get(field,{})
            if not set(ids)<=set(candidate_field.get('evidenceIds',[])):
                fail(422,invalid_code,'字段依据不属于该分组')
            value=values[field]
            proven=[]
            for eid in ids:
                ev=evidence[eid]
                if not can_material(db,actor,ev['materialId'],self.auth):
                    fail(422,invalid_code,'字段原件不可读取')
                raw=original_value(db,source,payload,ev,originals)
                if raw is None:fail(422,invalid_code,'字段原件与依据不一致')
                extracted=field_source_value(raw,ev,payload,field,aliases[field]|{field})
                if extracted is None and ev['locator']['kind'] in ('csv','xlsx','docx'):
                    extracted=raw
                if field=='platformCode' and isinstance(extracted,str):
                    extracted={'美团':'meituan','淘宝':'taobao_instant','淘宝闪购':'taobao_instant','京东':'jd'}.get(extracted,extracted)
                elif field=='type' and isinstance(extracted,str):
                    extracted={'品牌活动':'brand','平台活动':'platform','价探招商':'jiatan_sourcing'}.get(extracted,extracted)
                elif field=='brandIds' and isinstance(extracted,str):
                    extracted=[x.strip() for x in extracted.split(',')]
                if field in ('startAt','endAt') and isinstance(extracted,str) and isinstance(value,str):
                    def local_day(moment):
                        try:
                            if re.fullmatch(r'\d{4}-\d\d-\d\d',moment):
                                return datetime.strptime(moment,'%Y-%m-%d').date().isoformat()
                            date=datetime.fromisoformat(moment.replace('Z','+00:00'))
                            if date.tzinfo:
                                return date.astimezone(timezone(timedelta(hours=8))).date().isoformat()
                        except ValueError:
                            return None
                    proven.append(local_day(extracted) is not None and local_day(extracted)==local_day(value))
                elif field=='brandIds' and isinstance(extracted,list) and isinstance(value,list):
                    proven.append(set(value)==set(extracted))
                else:
                    proven.append(extracted==value)
            exact_candidate=(candidate_field.get('status')=='explicit' and value==candidate_field.get('value')
                             and set(ids)==set(candidate_field.get('evidenceIds',[])))
            if not all(proven) and not exact_candidate:
                fail(422,invalid_code,'字段依据与保存值不一致')
        return refs

    def supplement(self,db,actor,aid,vid,body,command_id):
        from b_auto_resolution import original_value
        invalid='INVALID_GROUP_SUPPLEMENT'
        if (not isinstance(body,dict) or not {'expectedActivityRevision','expectedSupplementVersionId','source','patch'}<=set(body)
                or set(body)-{'expectedActivityRevision','expectedSupplementVersionId','source','patch','fieldEvidenceRefs'}):
            fail(422,invalid,'补录字段缺失或未知')
        binding=body['source'];patch=body['patch'];prior_id=body['expectedSupplementVersionId']
        allowed={'startOn','endOn','rulesText','platformIdentity','addBrandIds','productResolutions','productScopeIntent','subsidyPriceCorrections','storeBearing','rawText','materialIds'}
        if (not isinstance(binding,dict) or set(binding)!={'submissionId','inputRevision','analysisRunId','analysisDigest','candidateId'}
                or type(binding['inputRevision']) is not int
                or any(not isinstance(binding[k],str) or not binding[k] for k in binding if k!='inputRevision')
                or type(body['expectedActivityRevision']) is not int
                or prior_id is not None and (not isinstance(prior_id,str) or not prior_id)
                or not isinstance(patch,dict) or not patch or set(patch)-allowed):
            fail(422,invalid,'补录字段或来源格式无效')
        base=self.supplement_target(db,actor,aid,vid)
        sub=owns_submission(db,actor,binding['submissionId'],self.auth,True)
        entry=db.execute("""SELECT payload FROM resolutions WHERE submission_id=? AND input_revision=? AND candidate_id=?
                            AND activity_id=? AND version_id=?""",
                         (sub['id'],binding['inputRevision'],binding['candidateId'],aid,vid)).fetchone()
        if not entry:fail(409,'STATE_CONFLICT','该分组并未建档到指定版本')
        saved=json.loads(entry[0])
        if saved.get('analysisRunId')!=binding['analysisRunId'] or saved.get('analysisDigest')!=binding['analysisDigest']:
            # Older resolution records still retain the exact run binding in their history event.
            event=db.execute('SELECT payload FROM b_history WHERE id=?',(saved.get('historyEventId'),)).fetchone()
            details=json.loads(event[0]).get('details',{}) if event else {}
            if details.get('analysisRunId')!=binding['analysisRunId'] or details.get('analysisDigest')!=binding['analysisDigest']:
                fail(409,'ANALYSIS_STALE','建档回执与原分析绑定不符')
        payload,candidate,source,originals=self.source_candidate(db,sub,binding)
        activity=entity(db,'activities',aid)
        latest=self.latest_supplement(db,aid,vid)
        if (activity['revision']!=body['expectedActivityRevision'] or (latest and latest['id'])!=prior_id
                or (activity.get('displayVersionId') or activity.get('currentReviewVersionId'))!=vid):
            fail(409,'STATE_CONFLICT','活动或补录版本已变化，请刷新后核对')
        inherited=latest or base
        snapshot=dict(inherited['snapshot'])
        confirmed=snapshot.get('resolvedFields') or snapshot.get('confirmedFields')
        if not confirmed:fail(409,'STATE_CONFLICT','基准版本缺少可核的已存字段')
        values=dict(confirmed);evidence_values={}
        pending={p['field']:p['reasonCode'] for p in snapshot.get('pendingItems',[])}
        def change_required():
            fail(409,'CHANGE_VERSION_REQUIRED','已确认内容有变化，请走活动变更')
        for key,field,suffix in (('startOn','startAt','T00:00:00+08:00'),('endOn','endAt','T23:59:59.999+08:00')):
            if key not in patch:continue
            value=patch[key]
            if not isinstance(value,str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}',value):
                fail(422,invalid,'日期须为北京时间年月日',{'field':key})
            try:datetime.strptime(value,'%Y-%m-%d')
            except ValueError:fail(422,invalid,'日期无效',{'field':key})
            if values.get(field):change_required()
            values[field]=value+suffix;evidence_values[field]=values[field]
        if values.get('startAt') and values.get('endAt') and datetime.fromisoformat(values['startAt'])>datetime.fromisoformat(values['endAt']):
            fail(422,invalid,'结束日期早于开始日期',{'field':'endOn'})
        if 'rulesText' in patch:
            value=patch['rulesText']
            if not isinstance(value,str) or not value.strip() or len(value)>10000:fail(422,invalid,'优惠机制格式无效')
            text(value,10000,False)
            if values.get('rulesText'):change_required()
            values['rulesText']=value;evidence_values['rulesText']=value
        if 'platformIdentity' in patch:
            identity=patch['platformIdentity']
            if (not isinstance(identity,dict) or set(identity)!={'platformCode','sourceActivityId'}
                    or identity['platformCode'] not in PLATFORM_CODES
                    or not isinstance(identity['sourceActivityId'],str) or not identity['sourceActivityId'].strip()
                    or identity['sourceActivityId']!=identity['sourceActivityId'].strip() or len(identity['sourceActivityId'])>200):
                fail(422,invalid,'必须提供报名平台和编号')
            if values.get('sourceActivityId') or values.get('platformCode') not in (None,identity['platformCode']):
                change_required()
            if self.supplement_identity_exists(db,aid,identity):
                fail(409,'DUPLICATE_ACTIVITY','该报名身份已被其它活动或版本使用')
            values.update(identity);evidence_values.update(identity)
            pending.pop('platformCode',None)
        if 'addBrandIds' in patch:
            brands=patch['addBrandIds']
            if (not isinstance(brands,list) or not brands or len(brands)>20 or any(not isinstance(i,str) for i in brands)
                    or len(set(brands))!=len(brands)):
                fail(422,invalid,'新增品牌须为不同的有效主档')
            old_brands=values.get('brandIds') or []
            if old_brands and 'brandIds' not in pending:change_required()
            if set(brands)&set(old_brands) or len(old_brands)+len(brands)>20:
                fail(422,invalid,'新增品牌与已有关联重复或超过上限')
            for bid in brands:entity(db,'brands',bid)
            values['brandIds']=old_brands+brands;evidence_values['brandIds']=brands
            if candidate['fields']['brandIds']['status']!='conflict' and not (
                    'brandIds' in pending and candidate['fields']['brandIds']['status']=='explicit'
                    and old_brands):
                pending.pop('brandIds',None)
        coupon_input=snapshot.get('couponReviewInput')
        if 'storeBearing' in patch:
            if actor['id']!=sub['submitter_id']:
                fail(403,'FORBIDDEN','请由原提报人确认门店是否出钱')
            if snapshot.get('storeBearingPolicy')=='per_tier' or not coupon_input or 'storeBearing' not in pending or values.get('storeBearing') is not None:
                change_required()
            if patch['storeBearing'] not in ('yes','no'):
                fail(422,invalid,'请选择门店是否出钱',{'field':'storeBearing'})
            if coupon_input.get('storeFundingRequired') and patch['storeBearing']!='yes':
                fail(422,'MIXED_COUPON_STORE_BEARING_REQUIRED','混补券需要门店出资，请确认门店出钱。',{'field':'storeBearing'})
            values['storeBearing']=patch['storeBearing']
        products=list(snapshot.get('products',[]));unresolved=list(snapshot.get('unresolvedRows',[]))
        if 'productResolutions' in patch:
            choices=patch['productResolutions']
            rows={r['rowId']:r for r in candidate['productRows']}
            if not isinstance(choices,list) or not choices or len(choices)>1000:
                fail(422,invalid,'商品补录行无效')
            seen=set()
            for choice in choices:
                if (not isinstance(choice,dict) or set(choice)!={'rowId','decision','productId'}
                        or not isinstance(choice['rowId'],str) or choice['rowId'] in seen
                        or choice['rowId'] not in rows or choice['decision'] not in ('link','unresolved')):
                    fail(422,invalid,'商品行不属于本组或格式无效')
                rid=choice['rowId'];seen.add(rid)
                if rid not in unresolved:change_required()
                if choice['decision']=='unresolved':
                    if choice['productId'] is not None:fail(422,invalid,'未核对行不能附商品ID')
                    continue
                item=entity(db,'products',choice['productId']);row=rows[rid]
                require_product_identity(row,item,invalid)
                for eid in row['evidenceIds']:
                    ev=next(e for e in payload['result']['evidence'] if e['evidenceId']==eid)
                    if not can_material(db,actor,ev['materialId'],self.auth) or original_value(db,source,payload,ev,originals) is None:
                        fail(422,invalid,'商品依据不可读取')
                if not row['evidenceIds']:fail(422,invalid,'商品行缺少原文依据')
                products.append(item['id']);unresolved.remove(rid)
        scope=dict(snapshot.get('productApplicability') or {'kind':'unknown','scopeBrandIds':[],'evidenceRefs':[],'scopeRuleVersion':None})
        if 'productResolutions' in patch and products and snapshot.get('unresolvedRows') and not unresolved:
            scope['kind']='explicit_skus'
        if scope['kind']!='all_products':
            scope['scopeBrandIds']=values.get('brandIds') or []
        if SCOPE_FIELD in patch:
            if actor['id']!=sub['submitter_id']:
                fail(403,'FORBIDDEN','请由原提报人确认商品适用范围')
            if not valid_product_scope(patch[SCOPE_FIELD]):fail(422,invalid,'商品范围意图无效')
            if snapshot.get(SCOPE_FIELD) not in (None,'deferred') and scope.get('kind')!='unknown':change_required()
            snapshot[SCOPE_FIELD]=patch[SCOPE_FIELD]
        if (SCOPE_FIELD in patch or 'productResolutions' in patch or 'addBrandIds' in patch) and scope.get('scopeRuleVersion') in (None,'submitter-scope/1.0'):
            scope=declared_scope(values,snapshot,products or snapshot.get('manualCorrections',{}).get('products'),unresolved,
                                 allow_broad=not product_registration_enabled(inherited))
        if 'subsidyPriceCorrections' in patch:
            if actor['id']!=sub['submitter_id']:
                fail(403,'FORBIDDEN','请由原提报人核对补贴价格')
            price_rows=snapshot.get('subsidyPrices')
            if not price_rows or not patch['subsidyPriceCorrections']:
                fail(422,invalid,'当前分组没有需补正的价格')
            snapshot['subsidyPrices']=apply_corrections(price_rows,patch['subsidyPriceCorrections'],actor['id'],iso(self.clock()))
        refs=self.verified_field_refs(db,actor,body.get('fieldEvidenceRefs',[]),evidence_values,
                                      payload,candidate,source,originals,invalid)
        raw=patch.get('rawText','');ids=patch.get('materialIds',[])
        if (not isinstance(raw,str) or len(raw)>50000 or 'rawText' in patch and not raw.strip()
                or not isinstance(ids,list) or len(ids)>5 or any(not isinstance(mid,str) for mid in ids)
                or len(ids)!=len(set(ids)) or 'materialIds' in patch and not ids):
            fail(422,invalid,'补录文字或附件无效')
        text(raw,50000,False)
        assets=[]
        for mid in ids:
            original=material(db,mid)
            if (original['owner_id']!=actor['id'] or not can_material(db,actor,mid,self.auth)
                    or original['parse_support'].startswith('controlled_')
                    or hashlib.sha256(original['content']).hexdigest()!=original['sha256']):
                fail(404,'MATERIAL_UNAVAILABLE','补录附件不存在或不可用')
            assets.append(original)
        if sum(m['size'] for m in assets)>10*1024*1024:
            fail(422,invalid,'补录附件合计超过10MiB')
        if raw:
            raw_view=add_material(db,actor,self.clock(),'组内补充原文.txt','text/plain',raw.encode(),'supported')
            assets.append(material(db,raw_view['materialId']))
        carry={k:v for k,v in pending.items() if k in ('platformCode','brandIds','activityName')}
        if snapshot.get('storeBearingPolicy')!='per_tier' and 'storeBearing' in pending and values.get('storeBearing') not in ('yes','no'):
            carry['storeBearing']=pending['storeBearing']
        if needs_correction(snapshot.get('subsidyPrices',[])):
            carry['subsidyPrices']='SUBSIDY_PRICE_CONFIRMATION_REQUIRED'
        if not values.get('rulesText') and 'rulesText' in pending and candidate['fields']['rulesText']['status']=='conflict':
            carry['rulesText']=pending['rulesText']
        new_pending=pending_items(values,scope,unresolved,carry,activity_method=read_activity_method(snapshot))
        new_vid=new_id('VER')
        record={'source':binding,'baseVersionId':vid,'previousSupplementVersionId':prior_id,
                'actorId':actor['id'],'recordedAt':iso(self.clock()),'fieldEvidenceRefs':refs,
                'patch':{k:v for k,v in patch.items() if k not in ('rawText','materialIds')},
                'materials':[{'materialId':m['id'],'sha256':m['sha256']} for m in assets]}
        controlled=add_material(db,actor,self.clock(),'组内补充.json','application/vnd.jiatan.form+json',
                                canon(record).encode(),'controlled_supplement','SUP-'+command_id)
        material_refs=[*record['materials'],{'materialId':controlled['materialId'],'sha256':controlled['sha256']}]
        material_ids=list(dict.fromkeys([*inherited.get('materialIds',[]),*(r['materialId'] for r in material_refs)]))
        if set(patch)&{'rulesText','rawText','materialIds','subsidyPriceCorrections'}:
            values.pop('brandSubsidyOnly',None)
        snapshot.pop('manualConfirmation',None);snapshot.pop('resolutionBinding',None)
        snapshot.pop('reviewBlockedReason',None)
        snapshot.update(confirmedFields=values,resolvedFields=values,rules=values.get('rulesText'),products=list(dict.fromkeys(products)),
                        unresolvedRows=unresolved,pendingItems=new_pending,productApplicability=scope,
                        supplementBinding={'source':binding,'baseVersionId':vid,'previousSupplementVersionId':prior_id,
                                           'materialRefs':material_refs,'confirmationMaterialId':controlled['materialId']})
        if scope['kind']!='all_products':snapshot.pop('productListState',None)
        else:snapshot['productListState']='scope_only'
        number=db.execute("SELECT MAX(json_extract(body,'$.versionNo')) FROM activity_versions WHERE json_extract(body,'$.activityId')=?",(aid,)).fetchone()[0]+1
        snapshot.update(dimensions(activity,snapshot))
        new_version={'id':new_vid,'activityId':aid,'versionNo':number,'submissionId':sub['id'],
            'previousVersionId':inherited['id'],'basedOnVersionId':vid,'snapshot':snapshot,'materialIds':material_ids,
            'materialRefs':material_ids,'changeSummary':'同组资料补录','revision':1}
        put(db,'activity_versions',new_version)
        self.attach_source(db,new_vid,sub['id'],binding['inputRevision'],material_ids)
        latest_review=db.execute('SELECT state FROM version_reviews WHERE version_id=?',(vid,)).fetchone()
        can_replace=(
            (pending.get('subsidyPrices') and 'subsidyPriceCorrections' in patch
             or pending.get('storeBearing') and 'storeBearing' in patch
             or pending.get('productApplicability') and any(key in patch for key in (SCOPE_FIELD,'productResolutions','addBrandIds'))
             and (snapshot.get('couponScopeRequired') or has_coupon_scope(read_activity_method(snapshot),values.get('rulesText'))))
            and not product_registration_enabled(base)
            and 'storeBearing' not in carry
            and not needs_correction(snapshot.get('subsidyPrices',[]))
            and (not (snapshot.get('couponScopeRequired') or has_coupon_scope(read_activity_method(snapshot),values.get('rulesText')))
                 or scope['kind'] in ('explicit_skus','all_products','no_specific_products'))
            and latest_review and latest_review[0] in ('not_started','initial_pending','pricing_pending','final_pending','rejected')
            and all(not activity.get(key) for key in ('approvedVersionId','effectiveVersionId','effectiveExecutionVersionId'))
            and (activity.get('reviewVersionId') or activity.get('currentReviewVersionId'))==vid)
        state='not_started'
        if can_replace:
            # Review the corrected snapshot from its first node. The previous
            # decisions and source version remain intact.
            state,blocked=self.new_review(db,actor,aid,new_vid,sub['id'],values['companyBearing']['state'],
                                          values['companyBearing']['evidenceIds'])
            snapshot['reviewBlockedReason']=blocked
            snapshot['completionReview']=True
            put(db,'activity_versions',new_version)
            activity.update(currentReviewVersionId=new_vid,reviewVersionId=new_vid,displayVersionId=new_vid,
                            reviewState=state,reviewBlockedReason=blocked,pendingItems=new_pending,
                            productApplicability=scope,review={'stage':'initial' if state=='initial_pending' else None,
                            'result':'pending' if state=='initial_pending' else state,
                            'approvalMode':self.review_view(db,new_vid)['approvalMode'] if state=='initial_pending' else None})
            activity.update(projected_declarations(snapshot,version_platform(snapshot)),**dimensions(activity,snapshot))
            activity.update(startAt=values['startAt'],endAt=values['endAt'],rules=values['rulesText'],
                            ruleSummary=values['rulesText'],brandIds=values['brandIds'] or [],platformCode=values['platformCode'])
            if values.get('sourceActivityId') and values.get('platformCode') and not activity.get('externalIds'):
                activity['externalIds']=[{'platformCode':values['platformCode'],'id':values['sourceActivityId'],'sourceRef':sub['id']}]
            for bid in activity['brandIds']:
                db.execute('INSERT OR IGNORE INTO activity_brands VALUES(?,?,?)',(aid,bid,canon(
                    {'activityId':aid,'brandId':bid,'complete':True,'provenance':activity['provenance']})))
            from seed import metric
            for pid in sorted(set(products)):
                db.execute('INSERT OR IGNORE INTO activity_products VALUES(?,?,?)',(aid,pid,canon(
                    {'activityId':aid,'productId':pid,'complete':not unresolved,'activityPrice':None,'currency':None,
                     'priceUnit':None,'historicalSales':metric(),'latestActivityId':None,'provenance':activity['provenance']})))
            if scope['kind']!='unknown':
                db.execute('DELETE FROM incomplete_relations WHERE activity_id=?',(aid,))
            if scope['kind']=='all_products':
                db.execute('INSERT INTO incomplete_relations VALUES(?,?)',(aid,canon(
                    {'reasonCode':'SCOPE_NOT_ENUMERATED','productListState':'scope_only',
                     'brandIds':activity['brandIds'],'platformCode':activity['platformCode']})))
            if scope['kind']=='all_products':activity['productListState']='scope_only'
            else:activity.pop('productListState',None)
        else:
            db.execute("INSERT INTO version_reviews VALUES(?,?,1,'not_started',NULL,?)",(new_vid,aid,sub['id']))
            activity['pendingChangeVersionIds']=[*activity.get('pendingChangeVersionIds',[]),new_vid]
        activity['revision']+=1
        put(db,'activities',activity)
        history(db,actor,self.clock(),activity=aid,version=new_vid,submission=sub['id'],event='activity.supplement_saved',
                after=state,details={'source':binding,'baseVersionId':vid,'previousSupplementVersionId':prior_id,
                                             'confirmationMaterialId':controlled['materialId']})
        return 201,{'outcome':'supplement_saved','activityId':aid,'baseVersionId':vid,'supplementVersionId':new_vid,
                    'activityRevision':activity['revision'],'pendingItems':new_pending,'reviewState':state}

    @staticmethod
    def supplement_identity_exists(db,aid,identity,*,include_current=True):
        pair=(identity['platformCode'],identity['sourceActivityId'])
        excluded=aid or ''
        if include_current:
            for row in db.execute('SELECT body FROM activities WHERE id<>?',(excluded,)):
                if any((ref.get('platformCode'),ref.get('id'))==pair for ref in json.loads(row[0]).get('externalIds',[])):
                    return True
        for row in db.execute("""SELECT body FROM activity_versions
            WHERE json_extract(body,'$.activityId')<>? AND json_type(body,'$.snapshot.supplementBinding')='object'""",(excluded,)):
            fields=json.loads(row[0])['snapshot']['confirmedFields']
            if (fields.get('platformCode'),fields.get('sourceActivityId'))==pair:
                return True
        return False

    def review_decision_policy(self,db,actor,review,node):
        # R4: every server-authorized administrator may self-review and handle
        # all three ordered nodes, independent of identity provider/environment.
        if self.is_review_admin(actor):return None
        sub=db.execute('SELECT submitter_id FROM b_submissions WHERE id=?',(review['submission_id'],)).fetchone()
        if sub and sub[0]==actor['id']:return 'self_review','自审规则未确认'
        if node['stage']=='final':
            initial=db.execute("SELECT actor_id FROM review_decisions WHERE version_id=? AND node_id IN (SELECT id FROM review_tasks WHERE version_id=? AND stage='initial')",(review['version_id'],review['version_id'])).fetchone()
            if initial and initial[0]==actor['id']:return 'same_initial_final_actor','同人初终审规则未确认'
        return None

    def is_review_admin(self, actor):
        return getattr(self.auth,'is_review_admin',lambda _:False)(actor)

    def require_review_actor(self, db, actor, node, activity_id):
        self.auth.require(db,actor,'reviews.decide','assigned')
        if not self.is_review_admin(actor):
            if node['assignee_id']!=actor['id']:
                fail(403,'FORBIDDEN','并非该节点指派人',{'reasonCode':'NOT_ASSIGNEE'})
            if actor['business_role']!=STAGE_ROLE[node['stage']]:
                fail(403,'FORBIDDEN','当前角色不符合节点',{'reasonCode':'ROLE_MISMATCH'})
            return
        if not node['assignee_id']:
            fail(409,'ASSIGNMENT_REQUIRED','节点未指派')
        assignee=self.auth.account(db,node['assignee_id'])
        if (assignee['business_role']!=STAGE_ROLE[node['stage']] and not self.is_review_admin(assignee)) or not self.auth.review_account_active(db,assignee):
            fail(409,'ASSIGNEE_ROLE_MISMATCH','当前节点指派人员已失效')
        try:
            self.auth.require(db,assignee,'reviews.decide','assigned')
            self.auth.require(db,assignee,'reviews.read','assigned')
            self.auth.require(db,assignee,'operations.read',activity_id)
        except APIError as error:
            if error.status!=403:raise
            fail(409,'ASSIGNEE_ROLE_MISMATCH','当前节点指派人员缺少审核授权')

    def require_current_review(self, db, actor, review, node):
        activity=entity(db,'activities',review['activity_id'])
        current=activity.get('reviewVersionId') or activity.get('currentReviewVersionId')
        if current!=review['version_id']:
            fail(409,'STATE_CONFLICT','该版本已不是当前审核版本',{'reasonCode':'REVIEW_VERSION_NOT_CURRENT'})
        if self.is_review_admin(actor):
            sub=db.execute('SELECT submitter_id FROM b_submissions WHERE id=?',(review['submission_id'],)).fetchone()
            if not sub:
                fail(409,'ASSIGNEE_ROLE_MISMATCH','当前节点缺少有效提报和指派关系')

    def coupon_reviewer(self, db, actor, aid, vid, stage):
        version = entity(db,'activity_versions',vid)
        if version['activityId'] != aid or not can_version(db,actor,vid,self.auth):
            fail(404,'NOT_FOUND','审核版本不存在或不可访问')
        self.auth.require(db,actor,'operations.read',aid)
        if stage not in STAGES:
            fail(422,'INVALID_REQUEST','选券的审核阶段无效。')
        node=db.execute('SELECT * FROM review_tasks WHERE version_id=? AND stage=?',(vid,stage)).fetchone()
        if not node:
            fail(409,'STATE_CONFLICT','当前版本没有相应的审核节点')
        self.require_review_actor(db,actor,node,aid)
        return version

    def visible_coupon_materials(self, db, actor, version):
        return {mid for mid in version.get('materialIds',[]) if can_material(db,actor,mid,self.auth)}

    def save_coupon_selection(self, db, actor, aid, vid, body):
        from b_coupon_review import save_selection
        version=entity(db,'activity_versions',vid)
        stage=body.get('expectedReviewStage','initial')
        review=db.execute('SELECT * FROM version_reviews WHERE version_id=? AND activity_id=?',(vid,aid)).fetchone()
        node=db.execute('SELECT * FROM review_tasks WHERE version_id=? AND stage=?',(vid,stage)).fetchone()
        if not review or review['state']!=stage+'_pending' or not node or node['state']!='pending':
            fail(409,'STATE_CONFLICT','选券的审核阶段已变化，请刷新页面')
        self.require_review_actor(db,actor,node,aid)
        self.require_current_review(db,actor,review,node)
        policy=self.review_decision_policy(db,actor,review,node)
        if policy:
            fail(409,'POLICY_UNCONFIRMED',policy[1],{'policy':policy[0]})
        result=save_selection(db,version,actor,body,visible_material_ids=self.visible_coupon_materials(db,actor,version),
                              review_state=review['state'])
        return 200, result | {'activityId':aid}

    def decide(self,db,actor,aid,vid,body):
        fields(body,['expectedReviewRevision','nodeId','expectedNodeRevision','decision'],['reason','comment','optimizationNote','registrationReview','selectedCouponIds','expectedCouponSelectionRevision'])
        review=db.execute('SELECT * FROM version_reviews WHERE version_id=? AND activity_id=?',(vid,aid)).fetchone();node=db.execute('SELECT * FROM review_tasks WHERE id=? AND version_id=?',(body['nodeId'],vid)).fetchone()
        if not review or not node:fail(404,'NOT_FOUND','审核节点不存在')
        if type(body['expectedReviewRevision']) is not int or type(body['expectedNodeRevision']) is not int or review['revision']!=body['expectedReviewRevision'] or node['revision']!=body['expectedNodeRevision'] or node['state']!='pending' or review['state']!=node['stage']+'_pending':fail(409,'STATE_CONFLICT','审核版本/节点已变化',{'review':self.review_view(db,vid),'node':dict(node)})
        if not node['assignee_id']:fail(409,'ASSIGNMENT_REQUIRED','节点未指派')
        self.require_review_actor(db,actor,node,aid)
        self.require_current_review(db,actor,review,node)
        policy=self.review_decision_policy(db,actor,review,node)
        if policy:fail(409,'POLICY_UNCONFIRMED',policy[1],{'policy':policy[0]})
        if body['decision'] not in ['approve','reject']:fail(422,'INVALID_REQUEST','审核决定无效')
        for key in ['reason','comment','optimizationNote']:text(body.get(key),2000)
        if body['decision']=='reject' and not (body.get('reason') or '').strip():fail(422,'REJECTION_REASON_REQUIRED','不通过原因必填')
        if body.get('optimizationNote') and node['stage']!='final':fail(422,'INVALID_REQUEST','商品优化只在终审辅助记录')
        version=entity(db,'activity_versions',vid)
        from b_coupon_review import decide_coupons
        coupons=decide_coupons(db,version,body,visible_material_ids=self.visible_coupon_materials(db,actor,version),
                              review_state=review['state'])
        single=review['approval_mode']=='single_management'
        if body['decision']=='approve':
            missing=approval_requirements(version['snapshot'],node['stage'],single)
            if missing:
                fail(409,missing[0]['code'],missing[0]['message'])
        decision=new_id('DEC');old=review['state'];result='rejected' if body['decision']=='reject' else 'approved'
        next_state=result if result=='rejected' or single else {'initial':'pricing_pending','pricing':'final_pending','final':'approved'}[node['stage']]
        from b_registration_review import review_decision
        registration=review_decision(self,db,actor,aid,vid,node['stage'],body,decision)
        db.execute('UPDATE review_tasks SET state=?,revision=revision+1 WHERE id=?',(result,node['id']))
        if next_state.endswith('_pending'):db.execute("UPDATE review_tasks SET state='pending' WHERE version_id=? AND stage=?",(vid,next_state[:-8]))
        db.execute('UPDATE version_reviews SET state=?,revision=revision+1 WHERE version_id=?',(next_state,vid))
        value={'id':decision,'versionId':vid,'nodeId':node['id'],'actorId':actor['id'],'nameSnapshot':actor['name'],'roleSnapshot':actor['business_role'],'decision':body['decision'],'reason':body.get('reason'),'comment':body.get('comment'),'optimizationNote':body.get('optimizationNote'),'at':iso(self.clock())}
        if registration:value['registrationReview']=registration
        if coupons:value['couponReview']=coupons
        if self.is_review_admin(actor):
            value.update(authority='full_access_administrator',assignedReviewerId=node['assignee_id'])
        db.execute('INSERT INTO review_decisions VALUES(?,?,?,?,?)',(decision,node['id'],vid,actor['id'],canon(value)))
        activity=entity(db,'activities',aid)
        if activity.get('reviewVersionId',activity['currentReviewVersionId'])==vid:
            activity['reviewState']=next_state
            activity['review']={'stage':next_state[:-8] if next_state.endswith('_pending') else None,'result':'pending' if next_state.endswith('_pending') else next_state,'approvalMode':review['approval_mode']};activity['revision']+=1
            if next_state=='approved':activity['approvedVersionId']=vid
            put(db,'activities',activity)
        event=history(db,actor,self.clock(),activity=aid,version=vid,submission=review['submission_id'],event='review.decided',before=old,after=next_state,details=value)
        return 200,{'decisionId':decision,'review':self.review_view(db,vid),'node':dict(db.execute('SELECT * FROM review_tasks WHERE id=?',(node['id'],)).fetchone()),'activityId':aid,'versionId':vid,'historyEventId':event}
    def require_first_review_actor(self,db,actor,version):
        if not self.is_review_admin(actor) and not db.execute('SELECT 1 FROM b_submissions WHERE id=? AND submitter_id=?',(version['submissionId'],actor['id'])).fetchone():
            fail(403,'FORBIDDEN','仅提报人可发起本次首次审核')
        self.auth.require(db,actor,'activities.propose')

    def review_start_target(self,db,actor,activity,version,review):
        if version.get('snapshot',{}).get('reviewRestartBinding'):
            from b_review_restart import start_target
            return start_target(self,db,actor,activity,version,review)
        if ((version.get('basedOnVersionId') or version.get('previousVersionId')) and not version.get('snapshot',{}).get('completionReview')
                or review['state']=='rejected'):
            return [],'POLICY_UNCONFIRMED'
        vid=version['id']
        if (review['state']!='not_started' or activity.get('reviewVersionId',activity.get('currentReviewVersionId'))!=vid
                or db.execute('SELECT 1 FROM review_tasks WHERE version_id=?',(vid,)).fetchone()
                or db.execute('SELECT 1 FROM review_decisions WHERE version_id=?',(vid,)).fetchone()):
            return [],'STATE_CONFLICT'
        original_reason=version.get('snapshot',{}).get('reviewBlockedReason')
        if not self.first_review_start:return [],original_reason or 'STATE_CONFLICT'
        if (original_reason not in ('ASSIGNMENT_REQUIRED','ASSIGNEE_ROLE_MISMATCH')
                or activity.get('approvedVersionId') or activity.get('effectiveExecutionVersionId')
                or activity.get('effectiveVersionId')):
            return [],'STATE_CONFLICT'
        try:self.require_first_review_actor(db,actor,version)
        except APIError as error:
            if error.status!=403:raise
            return [],'FORBIDDEN'
        submission=db.execute('SELECT submitter_id FROM b_submissions WHERE id=?',(review['submission_id'],)).fetchone()
        if not submission:return [],'STATE_CONFLICT'
        return self.review_assignees(db,submitter_id=submission[0],activity_id=activity['id'],
                                     single=review['approval_mode']=='single_management')

    def start_review(self,db,actor,aid,vid,body):
        fields(body,['expectedReviewRevision'])
        version=entity(db,'activity_versions',vid)
        review=db.execute('SELECT * FROM version_reviews WHERE version_id=? AND activity_id=?',(vid,aid)).fetchone()
        if not review:fail(404,'NOT_FOUND','审核版本不存在')
        if type(body['expectedReviewRevision']) is not int or body['expectedReviewRevision']!=review['revision']:
            fail(409,'STATE_CONFLICT','审核版本变化',{'review':self.review_view(db,vid)})
        activity=entity(db,'activities',aid)
        people,reason=self.review_start_target(db,actor,activity,version,review)
        if reason=='POLICY_UNCONFIRMED':
            fail(409,reason,'重审起点或变更生效边界待确认',{'policy':'resubmission_or_effective_change'})
        if reason in ('ASSIGNEE_SELF_REVIEW','ASSIGNEE_SAME_INITIAL_FINAL'):
            policy='self_review' if reason=='ASSIGNEE_SELF_REVIEW' else 'same_initial_final_actor'
            fail(409,'POLICY_UNCONFIRMED','审核方案违反现行审核规则',{'policy':policy,'reasonCode':reason})
        if reason:
            fail(403 if reason=='FORBIDDEN' else 409,reason,'当前版本无法开始首次审核',{'reasonCode':reason})
        self.assign_review_tasks(db,vid,people)
        mode=review['approval_mode'] or 'manual'
        db.execute("UPDATE version_reviews SET state='initial_pending',approval_mode=?,revision=revision+1 WHERE version_id=?",(mode,vid))
        activity.update(reviewState='initial_pending',reviewBlockedReason=None,revision=activity['revision']+1,
                        review={'stage':'initial','result':'pending','approvalMode':mode})
        put(db,'activities',activity)
        nodes=[dict(row) for row in db.execute(
            "SELECT * FROM review_tasks WHERE version_id=? ORDER BY CASE stage WHEN 'initial' THEN 0 WHEN 'pricing' THEN 1 ELSE 2 END",(vid,))]
        event=history(db,actor,self.clock(),activity=aid,version=vid,submission=review['submission_id'],
                      event='review.started',before='not_started',after='initial_pending',
                      details={'planSource':'review_plan','assignments':[{'stage':stage,'assigneeId':uid} for stage,uid in people],
                               'originalBlockedReason':version['snapshot'].get('reviewBlockedReason')})
        return 200,{'activityId':aid,'versionId':vid,'review':self.review_view(db,vid),'nodes':nodes,'historyEventId':event}
    def profile(self,db,id):
        row=db.execute('SELECT * FROM account WHERE id=?',(id,)).fetchone()
        if not row:fail(404,'USER_NOT_FOUND','人员不存在')
        project_user=getattr(self.auth,'profile_user',None)
        user=project_user(db,row) if project_user else self.auth.user(row)
        return user|{'remark':row['remark'],'profileVersion':row['profile_version'],'accountManagementAuthorized':bool(db.execute('SELECT 1 FROM system_permissions WHERE user_id=? AND permission=?',(id,'accounts.manage')).fetchone()),'status':row['status']}
    def edit_profile(self,db,actor,id,body):
        fields(body,['expectedVersion','name','phone','businessRole','remark'],['phoneChangeProof']);old=self.profile(db,id)
        if type(body['expectedVersion']) is not int or body['expectedVersion']!=old['profileVersion']:fail(409,'PROFILE_VERSION_CONFLICT','资料已更新',{'currentProfile':old})
        text(body['name'],50);text(body['remark'],200)
        if body['businessRole'] is not None and (not isinstance(body['businessRole'],str) or body['businessRole'] not in ROLES):fail(422,'INVALID_REQUEST','业务角色无效')
        if body['phone']!=old['phone']:
            if getattr(self.auth,'auth_method',None)=='phone_otp':
                from account_primitives import phone
                if phone(body['phone'])!=old['phone']:fail(422,'RULE_UNCONFIRMED','换绑专用证明与原号策略尚未启用')
            else:fail(503,'SMS_NOT_CONFIGURED','测试用户名登录；手机号换绑未配置，资料均未保存')
        new=old|{'name':body['name'] if body['name'] and body['name'].strip() else None,'businessRole':body['businessRole'],'remark':body['remark'] if body['remark'] and body['remark'].strip() else None}
        if all(new[k]==old[k] for k in ['name','phone','businessRole','remark']):return 200,{'user':old,'changed':False}
        db.execute('UPDATE account SET name=?,business_role=?,remark=?,profile_version=profile_version+1 WHERE id=?',(new['name'],new['businessRole'],new['remark'],id))
        after=self.profile(db,id);db.execute('INSERT INTO account_profile_audit VALUES(?,?,?,?,?,?)',(new_id('EVT'),actor['id'],id,canon(old),canon(after),self.clock()))
        return 200,{'user':after,'changed':True}
