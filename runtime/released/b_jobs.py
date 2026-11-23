"""Bounded explicit analysis jobs; own Agent subprocesses, one run-wide deadline."""
import base64
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import os
from pathlib import Path
import selectors
import subprocess
import sys
import threading
import time
from account_primitives import APIError,fail,iso
from b_state import canon,fingerprint
from b_domain import sub_revision,run_view,material,bump,verify_declaration_binding,grant_scopes
from b_submission_declarations import PLATFORM_CODES, recorded_form
from b_intake_grouping_validation import splits_single_child_export, ERROR_CODE as GROUPING_ERROR, ERROR_MESSAGE as GROUPING_MESSAGE
from b_schema import check,validate_agent

ROOT=Path(__file__).resolve().parents[2]
WORKER=ROOT/'business_agent/activity_intake/worker.py'
SCHEMA_DIR=None
EXPECTED_ENGINE_VERSION=None
REGROUP_WORKER=None
LEGACY_REGROUP_ENABLED=False
LIMITS={'maxBytes':5242880,'maxTextChars':200000,'maxRows':1000,'maxCandidates':20,'maxZipEntries':200,'maxExpandedBytes':20971520,'maxEntryBytes':5242880,'maxEvidence':1500}
XLSX_LIMITS = {**LIMITS, 'maxTextChars': 2_000_000, 'maxRows': 5_000}
LIMITS_VERSION='limits/1.0:'+fingerprint(XLSX_LIMITS)
MODEL_PLANNING_ERRORS = {
    'MODEL_INPUT_EXCEL_ROW_LIMIT': 'Excel 明细超过 5,000 条，请精简或分批提交',
    'MODEL_INPUT_EXCEL_TEXT_LIMIT': 'Excel 展开后超过 2,000,000 个字符，请精简或分批提交',
    'MODEL_INPUT_BATCH_LIMIT': '材料内容超过单次识别批次上限，请减少本次材料或分次提报',
    'MODEL_INPUT_ROW_LIMIT': '单行或表单内容超过识别上限，请缩短过长内容后重试',
    'MODEL_INPUT_DECODE_INCOMPLETE': '有材料未完整解码，请检查附件后重试',
    'MODEL_RECOGNITION_INPUT_INVALID': '材料没有可识别内容或超出识别范围，请检查后重试',
}
MODEL_FAILURE_MESSAGES = {
    'AGENT_MODEL_' + code: message for code, message in MODEL_PLANNING_ERRORS.items()
}
MODEL_FAILURE_MESSAGES['AGENT_MODEL_OUTPUT_LIMIT'] = '识别结果超过本地接收上限，请减少本次材料或分次提报'
MODEL_FAILURE_MESSAGES['AGENT_RESULT_SCOPE_MISMATCH'] = '后台核对 Agent 回执时发现材料范围不一致。请保留原提报，稍后在本页重新识别。'
MODEL_FAILURE_MESSAGES[GROUPING_ERROR] = GROUPING_MESSAGE
MODEL_FAILURE_MESSAGES['LOCAL_MATERIAL_READ_FAILED'] = '本机未能完整读取附件，尚未发送给 Agent。'
MODEL_FAILURE_MESSAGES['LOCAL_MATERIAL_OUTPUT_LIMIT'] = '本机展开后的材料记录超过 32 MiB，尚未发送给 Agent。'

TOP={'contractVersion','engine','modelUsed','engineVersion','parserVersions','matcherVersion','promptVersion','inputRevision','inputDigest','catalogRevision','outcome','candidates','evidence','missingFields','conflicts','materialResults','warnings','errors'}
def validate_worker_result(value,kind):
    if SCHEMA_DIR is None:
        return validate_agent(value,kind)
    return check(value,json.loads((SCHEMA_DIR/(kind+'.schema.json')).read_text()))


XLSX_DECODER = None


def preview_xlsx_readiness(upload):
    from b_domain import XLSX_PREPROCESS_SLOT

    filename, media, content, _ = upload
    if Path(filename).suffix.casefold() != '.xlsx' or 'spreadsheetml' not in media:
        fail(422, 'INVALID_MATERIAL', '本机读取检查只接受 .xlsx 附件。')
    if XLSX_DECODER is None:
        fail(503, 'MATERIAL_PREPROCESS_UNAVAILABLE', '本机表格检查暂不可用。')
    if not XLSX_PREPROCESS_SLOT.acquire(blocking=False):
        fail(429, 'MATERIAL_PREPROCESS_BUSY', '正在读取另一份表格，请稍后重试。')
    try:
        doc = XLSX_DECODER({'materialId':'PREVIEW', 'filename':filename, 'mediaType':media,
                            'contentBytes':content,'sha256':hashlib.sha256(content).hexdigest()}, XLSX_LIMITS)
    finally:
        XLSX_PREPROCESS_SLOT.release()
    if doc['status']=='parsed':
        return {'ready':True}
    issues = doc['errors'] + doc['warnings']
    limit = next((issue['message'] for issue in issues if issue['code']=='LIMIT_EXCEEDED'), None)
    if limit:
        return {'ready':False,'code':'LIMIT_EXCEEDED',
                'message':limit if limit!='Invalid input' else 'Excel 内容超过本机读取上限。'}
    return {'ready':False,'code':'INVALID_MATERIAL',
            'message':'本机无法完整读取此 Excel。请另存为 .xlsx 后重新选择。'}


def apply_local_workbook_rules(result, materials):
    """Extend a single locally parsed group with supported original worksheet rules."""
    if len(result['candidates']) != 1:
        return
    candidate, = result['candidates']
    if candidate['fields']['rulesText']['status'] != 'missing':
        return
    from business_agent.activity_model_first.child_workbook_rules import child_workbook_rule
    # The price summary is anchored by the original UPC and sheet cells. Preserve
    # the original product match statuses for separate human confirmation.
    source_rows = [dict(row, matchKind='unmatched') for row in candidate['productRows']]
    observed = child_workbook_rule(
        result['materialResults'], dict(candidate, productRows=source_rows), result['evidence'],
        material_names={material['id']: material['filename'] for material in materials},
    )
    if not observed or len(result['evidence']) + len(observed['evidence']) > 1500:
        return
    evidence_ids = []
    for index, source in enumerate(observed['evidence'], start=1):
        evidence_id = f'local-workbook-rule-{index}'
        result['evidence'].append(dict(source, evidenceId=evidence_id))
        evidence_ids.append(evidence_id)
    status = observed['status']
    candidate['fields']['rulesText'] = {
        'status': status, 'value': observed['summary'] if status == 'explicit' else None,
        'evidenceIds': evidence_ids,
    }
    result['missingFields'] = [field for field in result['missingFields'] if field['field'] != 'rulesText']
    if status == 'conflict':
        result['warnings'].append({'code': 'CHILD_WORKBOOK_PRICE_CONFLICT', 'message': observed['summary'],
                                   'locator': observed['evidence'][-1]['locator']})


class JobFailure(Exception):
    def __init__(self,code):self.code=code


def verify_model_scope(metadata, expected_hash, expected_rows):
    received_rows = metadata['coverage']['sourceRows']
    if metadata['inputScopeSha256'] != expected_hash or received_rows != expected_rows:
        failure = JobFailure('AGENT_RESULT_SCOPE_MISMATCH')
        failure.details = {'stage': 'verify_scope',
                           'reason': 'row_count_mismatch' if received_rows != expected_rows else 'fingerprint_mismatch',
                           'expectedSourceRows': expected_rows, 'receivedSourceRows': received_rows}
        raise failure


class Jobs:
    def __init__(self,auth,clock,model_agent=None,*,local_only=False):
        self.model_agent=model_agent;self.model_first=model_agent is not None
        self.local_only = local_only
        self.auth=auth;self.clock=clock;self.pool=ThreadPoolExecutor(max_workers=1,thread_name_prefix='jt021-analysis');self.capacity=threading.BoundedSemaphore(3);self.lock=threading.Lock();self.tokens={};self.children={};self.output_bytes={};self.closing=False
        with auth.tx() as db:
            for row in db.execute("SELECT * FROM analysis_runs WHERE state IN ('queued','running','cancel_requested')").fetchall():
                payload=json.loads(row['payload']);payload.update(phase='interrupted',error={'code':'AGENT_INTERRUPTED','message':'服务重启，需按原输入显式重试'})
                if payload.get('agentRecognition'):payload['agentRecognition'].update(status='interrupted',reasonCode='AGENT_INTERRUPTED')
                db.execute("UPDATE analysis_runs SET state='interrupted',payload=?,finished_at=? WHERE id=?",(canon(payload),clock(),row['id']))
                self.failure_state(db,row['submission_id'],row['input_revision'],'failed',bool(payload.get('_grouping')))
    def capability(self):
        return self.model_agent.capability() if self.model_first else None
    def reserve(self,rid):
        if self.closing or not self.capacity.acquire(blocking=False):fail(429,'ANALYSIS_CAPACITY','分析容量已满，请稍后重试')
        with self.lock:self.tokens[rid]=threading.Event();self.output_bytes[rid]=0
    def release_reservation(self,rid):
        with self.lock:token=self.tokens.pop(rid,None);self.output_bytes.pop(rid,None)
        if token is not None:self.capacity.release()
    def enqueue(self,rid):self.pool.submit(self.run,rid)
    def cancel(self,rid):
        with self.lock:
            token=self.tokens.get(rid)
            if token:token.set()
    def close(self):
        self.closing=True
        with self.lock:
            for token in self.tokens.values():token.set()
        self.pool.shutdown(wait=True)
    def wire(self,rid,operation,payload,deadline,*,legacy_regroup=False,regroup_engine=None):
        worker=REGROUP_WORKER if operation=='regroup' else WORKER
        if worker is None or not worker.is_file():raise JobFailure('AGENT_UNAVAILABLE')
        request_id='worker-'+rid+'-'+operation
        is_xlsx = operation == 'decode' and Path(payload['filename']).suffix.lower() == '.xlsx'
        current_engine = not legacy_regroup and regroup_engine not in ('1.0.6','1.0.7')
        limits = XLSX_LIMITS if is_xlsx or (operation != 'decode' and current_engine) else LIMITS
        payload=payload|{'limits':limits}
        request=canon({'contractVersion':'activity-intake/1.0','requestId':request_id,'operation':operation,'payload':payload}).encode()
        args=[sys.executable,'-B',str(worker)]
        if legacy_regroup:args.append('--legacy-105')
        elif regroup_engine in ('1.0.6','1.0.7'):args.append('--legacy-'+regroup_engine.replace('.',''))
        proc=subprocess.Popen(args,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,cwd=str(ROOT))
        with self.lock:self.children[rid]=proc;token=self.tokens[rid]
        sel=selectors.DefaultSelector();result=bytearray();errors=bytearray();offset=0
        try:
            for pipe,event in [(proc.stdin,selectors.EVENT_WRITE),(proc.stdout,selectors.EVENT_READ),(proc.stderr,selectors.EVENT_READ)]:os.set_blocking(pipe.fileno(),False);sel.register(pipe,event)
            while sel.get_map():
                if token.is_set():raise JobFailure('AGENT_INTERRUPTED' if self.closing else 'ANALYSIS_CANCELLED')
                remaining=deadline-time.monotonic()
                if remaining<=0:raise JobFailure('AGENT_TIMEOUT')
                for key,event in sel.select(min(.05,remaining)):
                    if key.fileobj is proc.stdin:
                        try:n=os.write(proc.stdin.fileno(),request[offset:offset+65536]);offset+=n
                        except BrokenPipeError:offset=len(request)
                        if offset==len(request):sel.unregister(proc.stdin);proc.stdin.close()
                    else:
                        chunk=os.read(key.fileobj.fileno(),65536)
                        if not chunk:sel.unregister(key.fileobj);key.fileobj.close();continue
                        dest=result if key.fileobj is proc.stdout else errors;dest.extend(chunk)
                        if len(errors)>8192:raise JobFailure('INVALID_AGENT_OUTPUT')
                        if len(result)+self.output_bytes.get(rid,0)>32*1024*1024:
                            raise JobFailure('LOCAL_MATERIAL_OUTPUT_LIMIT' if operation=='decode' else 'INVALID_AGENT_OUTPUT')
            proc.wait(timeout=max(.01,deadline-time.monotonic()))
            if proc.returncode!=0:raise JobFailure('INVALID_AGENT_OUTPUT')
            self.output_bytes[rid]+=len(result)
            wire=json.loads(result)
            if wire.get('contractVersion')!='activity-intake/1.0' or wire.get('requestId')!=request_id or set(wire) not in [{'contractVersion','requestId','ok','result'},{'contractVersion','requestId','ok','error'}]:raise JobFailure('INVALID_AGENT_OUTPUT')
            if wire.get('ok') is not True:
                code=wire.get('error',{}).get('code','INVALID_AGENT_OUTPUT')
                raise JobFailure('LOCAL_MATERIAL_OUTPUT_LIMIT' if operation=='decode' and code=='LIMIT_EXCEEDED' else code)
            return wire['result']
        except (ValueError,TypeError,subprocess.TimeoutExpired,KeyError):raise JobFailure('INVALID_AGENT_OUTPUT')
        finally:
            sel.close()
            if proc.poll() is None:proc.kill()
            proc.wait(timeout=2)
            for pipe in [proc.stdin,proc.stdout,proc.stderr]:
                if not pipe.closed:pipe.close()
            with self.lock:self.children.pop(rid,None)
    def engine_hash(self):
        files=sorted(WORKER.parent.rglob('*.py'))
        if not files:raise JobFailure('AGENT_UNAVAILABLE')
        return fingerprint([('business_agent/activity_intake/'+p.relative_to(WORKER.parent).as_posix(),hashlib.sha256(p.read_bytes()).hexdigest()) for p in files])
    def progress(self,rid,payload):
        with self.auth.tx() as db:
            row=db.execute('SELECT state FROM analysis_runs WHERE id=?',(rid,)).fetchone()
            if not row or row[0] not in ('running','cancel_requested'):return
            if row[0]=='cancel_requested':self.cancel(rid)
            db.execute('UPDATE analysis_runs SET payload=? WHERE id=?',(canon(payload),rid))
    def validate_result(self,result,run,documents,form_material_id,materials,*,engine_version=None):
        from b_model_intake import ENGINE_VERSION,PROMPT_VERSION
        model_result=isinstance(result,dict) and result.get('engine')=='deepseek_model_first_v1'
        model_schema = None
        try:
            if model_result:
                model_schema=json.loads((self.model_agent.directory/'schemas/analysis.schema.json').read_text())
                check(result,model_schema)
            else:validate_worker_result(result,'analysis')
        except (ValueError,TypeError,KeyError):raise JobFailure('INVALID_AGENT_OUTPUT')
        engine_version=engine_version or EXPECTED_ENGINE_VERSION
        engine_ok=(result.get('engineVersion')==ENGINE_VERSION and result.get('modelUsed') is True and result.get('promptVersion')==PROMPT_VERSION) if model_result else (result.get('engine')=='deterministic_local_v1' and (engine_version is None or result.get('engineVersion')==engine_version) and result.get('modelUsed') is False and result.get('promptVersion') is None)
        if not isinstance(result,dict) or set(result)!=TOP or result.get('contractVersion')!='activity-intake/1.0' or not engine_ok or result.get('inputRevision')!=run['input_revision'] or result.get('inputDigest')!=run['input_digest'] or result.get('catalogRevision')!=run['catalog_revision'] or result.get('outcome') not in ['needs_input','needs_confirmation']:raise JobFailure('INVALID_AGENT_OUTPUT')
        if result.get('materialResults')!=documents:raise JobFailure('INVALID_AGENT_OUTPUT')
        # Accept the Agent's existing bounded wire contract for every material
        # type. A multi-activity price sheet can legitimately contain >20 groups.
        candidate_limit=model_schema['properties']['candidates']['maxItems'] if model_result else 20
        if not isinstance(result['candidates'],list) or len(result['candidates'])>candidate_limit or not isinstance(result['evidence'],list):raise JobFailure('INVALID_AGENT_OUTPUT')
        hashes={m['id']:m['sha256'] for m in materials}
        # Build once: scanning every original cell for every evidence item makes
        # validation quadratic on large, otherwise valid spreadsheets.
        located={}
        for doc in documents:
            for item in doc['textBlocks']:
                located.setdefault((doc['materialId'],canon(item['locator'])),[]).append(item['text'])
            for row in doc['tableRows']:
                for item in row['cells']:
                    located.setdefault((doc['materialId'],canon(item['locator'])),[]).append(item['text'])
        evidence_ids=set()
        for evidence in result['evidence']:
            if not isinstance(evidence,dict) or set(evidence)-{'evidenceId','materialId','sourceSha256','locator','quote'} or evidence.get('materialId') not in hashes or evidence.get('sourceSha256')!=hashes[evidence['materialId']] or not isinstance(evidence.get('quote'),str) or not 0<len(evidence['quote'])<=500:raise JobFailure('INVALID_AGENT_OUTPUT')
            if evidence['materialId']==form_material_id:
                locator=evidence.get('locator',{})
                if set(locator)!={'kind','fieldPath'} or locator['kind']!='form':raise JobFailure('INVALID_AGENT_OUTPUT')
                form=json.loads(next(m['content'] for m in materials if m['id']==form_material_id))
                path=locator['fieldPath']
                if path.startswith('manualProductRows.'):
                    rid=path[len('manualProductRows.'):];value=next((r for r in (form.get('manualProductRows') or []) if r['rowId']==rid),None)
                else:value=form.get(path)
                expected=value if isinstance(value,str) else canon(value)
                if value is None or not (evidence['quote'] in expected if model_result else evidence['quote']==expected[:500]):raise JobFailure('INVALID_AGENT_OUTPUT')
            else:
                originals=located.get((evidence['materialId'],canon(evidence['locator'])),[])
                if not any(evidence['quote'] in text if model_result else text[:500]==evidence['quote'] for text in originals):raise JobFailure('INVALID_AGENT_OUTPUT')
            if evidence['evidenceId'] in evidence_ids:raise JobFailure('INVALID_AGENT_OUTPUT')
            evidence_ids.add(evidence['evidenceId'])
        catalog_products={r[0] for r in self._catalog_rows('products')};catalog_brands={r[0] for r in self._catalog_rows('brands')}
        seen=set()
        from b_domain import FORM_KEYS
        for candidate in result['candidates']:
            if not isinstance(candidate,dict) or set(candidate)!={'candidateId','fields','productRows'} or not isinstance(candidate['candidateId'],str) or candidate['candidateId'] in seen:raise JobFailure('INVALID_AGENT_OUTPUT')
            seen.add(candidate['candidateId'])
            if set(candidate['fields'])-((FORM_KEYS-{'manualProductRows'})|{'companyBearing'}):raise JobFailure('INVALID_AGENT_OUTPUT')
            product_limit=model_schema['properties']['candidates']['items']['properties']['productRows']['maxItems'] if model_result else 1000
            if not isinstance(candidate['productRows'],list) or len(candidate['productRows'])>product_limit:raise JobFailure('INVALID_AGENT_OUTPUT')
            if candidate['fields']['type']['value'] not in [None,'brand','platform','jiatan_sourcing'] or candidate['fields']['platformCode']['value'] not in (None, *PLATFORM_CODES):raise JobFailure('INVALID_AGENT_OUTPUT')
            brand_ids=candidate['fields']['brandIds']['value'] or []
            if any(b not in catalog_brands for b in brand_ids):raise JobFailure('INVALID_AGENT_OUTPUT')
            company=candidate['fields']['companyBearing']
            if company['value']['evidenceIds']!=company['evidenceIds']:raise JobFailure('INVALID_AGENT_OUTPUT')
            for field in candidate['fields'].values():
                if not isinstance(field,dict) or set(field)!={'value','status','evidenceIds'} or field['status'] not in ['explicit','missing','conflict'] or any(i not in evidence_ids for i in field['evidenceIds']):raise JobFailure('INVALID_AGENT_OUTPUT')
            row_ids=[p['rowId'] for p in candidate['productRows']]
            if len(set(row_ids))!=len(row_ids):raise JobFailure('INVALID_AGENT_OUTPUT')
            for product in candidate['productRows']:
                if any(p['productId'] not in catalog_products for p in product['candidates']):raise JobFailure('INVALID_AGENT_OUTPUT')
                if product.get('matchKind') not in ['exact','manual_selected','candidate','unmatched','conflict'] or product.get('matchedProductId') is not None and product['matchedProductId'] not in catalog_products or any(i not in evidence_ids for i in product.get('evidenceIds',[])):raise JobFailure('INVALID_AGENT_OUTPUT')
        return result
    def _catalog_rows(self,table):
        with self.auth.tx() as db:return db.execute('SELECT id,body FROM '+table).fetchall()
    def grouping_parent(self,db,run,sub,meta,source):
        from b_grouping import VERSION,LEGACY_ENGINES
        parent=db.execute('SELECT * FROM analysis_runs WHERE id=? AND submission_id=?',
                          (meta['sourceRunId'],run['submission_id'])).fetchone()
        catalog=json.loads(db.execute("SELECT value FROM b_meta WHERE key='catalogRevision'").fetchone()[0])
        if (meta['version']!=VERSION or not parent or parent['state']!='succeeded'
                or parent['input_digest']!=run['input_digest'] or parent['input_revision']!=run['input_revision']
                or parent['catalog_revision']!=run['catalog_revision'] or catalog!=run['catalog_revision']
                or meta['actorId']!=sub['submitter_id']):
            raise JobFailure('ANALYSIS_STALE')
        resolve_actor=getattr(self.auth,'regroup_actor',None)
        actor=resolve_actor(meta['actorId']) if resolve_actor else {'id':meta['actorId']}
        if actor is None or not all(grant_scopes(db,actor,self.auth,action)&scopes for action,scopes in (
                ('submissions.edit_own',{'*','own'}),('submissions.read_own',{'*','own'}),('activities.propose',{'*'}))):
            raise JobFailure('ANALYSIS_STALE')
        material_scopes=grant_scopes(db,actor,self.auth,'materials.read')
        if '*' not in material_scopes and not set(meta['materialIds'])<=material_scopes:
            raise JobFailure('ANALYSIS_STALE')
        payload=json.loads(parent['payload'])
        if (payload.get('analysisDigest')!=meta['sourceAnalysisDigest']
                or LEGACY_ENGINES.get(payload.get('engineSourceSha256'))!=payload.get('result',{}).get('engineVersion')):
            raise JobFailure('ANALYSIS_STALE')
        try:
            verify_declaration_binding(db,{'id':run['submission_id'],'revision':run['input_revision']},
                                      parent,payload['result'],meta['sourceAnalysisDigest'],source)
        except (APIError,KeyError,ValueError):
            raise JobFailure('ANALYSIS_STALE')
        return payload

    @staticmethod
    def failure_state(db,sid,revision,state,grouping):
        if grouping and db.execute('SELECT 1 FROM resolutions WHERE submission_id=? AND input_revision=?',
                                   (sid,revision)).fetchone():
            state='partially_resolved'
        db.execute("UPDATE b_submissions SET state=? WHERE id=? AND revision=? AND state<>'deleted'",
                   (state,sid,revision))

    def run(self,rid):
        payload={'phase':'starting','materialResults':[],'result':None,'error':None};deadline=time.monotonic()+(600 if self.model_first else 10)
        release=True
        try:
            with self.auth.tx() as db:
                run=db.execute('SELECT * FROM analysis_runs WHERE id=?',(rid,)).fetchone()
                if not run:return
                queued=json.loads(run['payload']);meta=queued.get('_grouping')
                if self.model_first:
                    consent=queued.get('_modelFirst')
                    if not isinstance(consent,dict) or consent.get('consent') is not True or meta:raise JobFailure('AGENT_CONSENT_REQUIRED')
                    payload['_modelFirst']=consent
                    payload['agentRecognition']=dict(queued['agentRecognition'],status='running')
                if meta:payload['_grouping']=meta
                if run['state']=='running':
                    release=False
                    return
                if run['state']=='cancel_requested':raise JobFailure('ANALYSIS_CANCELLED')
                if run['state']!='queued':return
                sub=db.execute('SELECT revision,state,submitter_id FROM b_submissions WHERE id=?',(run['submission_id'],)).fetchone()
                if not sub or sub['state']=='deleted' or sub['revision']!=run['input_revision']:
                    raise JobFailure('ANALYSIS_STALE')
                db.execute("UPDATE analysis_runs SET state='running',started_at=? WHERE id=?",(self.clock(),rid))
                source=sub_revision(db,run['submission_id'],run['input_revision']);materials=[dict(material(db,mid)) for mid in source['allMaterialIds']]
                if self.model_first:
                    from b_domain import owns_submission,can_material
                    actor=self.auth.account(db,payload['_modelFirst']['consentBy'])
                    owns_submission(db,actor,run['submission_id'],self.auth,True)
                    if any(not can_material(db,actor,m['id'],self.auth) for m in materials):raise JobFailure('MATERIAL_READ_FORBIDDEN')
                parent=self.grouping_parent(db,run,sub,meta,source) if meta else None
                catalog={'revision':run['catalog_revision'],'brands':[{k:v for k,v in json.loads(r[0]).items() if k in ['id','name']} for r in db.execute('SELECT body FROM brands')],'products':[{k:v for k,v in json.loads(r[0]).items() if k in ['id','internalCode','upc','name','spec','brandId']} for r in db.execute('SELECT body FROM products')]}
            documents=[];engine_hash=self.engine_hash()
            if parent:
                documents=parent['result']['materialResults']
                payload['materialResults']=parent['materialResults']
            for m in ([] if parent else materials):
                if m['id']==source['formMaterialId']:
                    blocks=[{'text':str(v) if isinstance(v,str) else canon(v),'locator':{'kind':'form','fieldPath':key}} for key,v in recorded_form(source).items() if v is not None]
                    doc={'materialId':m['id'],'sha256':m['sha256'],'status':'parsed','kind':'form','textBlocks':blocks,'tableRows':[],'errors':[],'warnings':[],'parserVersion':'backend-form/1'};hit=False
                else:
                    limits_key=LIMITS_VERSION
                    cache_engine=fingerprint({'engine':engine_hash,'mediaType':m['media_type'],'extension':Path(m['filename']).suffix.lower(),
                                              'ledgerDecoder':'1.0' if self.model_first else None})
                    with self.auth.tx() as db:cached=db.execute('SELECT payload FROM parse_cache WHERE sha256=? AND limits_version=? AND worker_hash=?',(m['sha256'],limits_key,cache_engine)).fetchone()
                    hit=cached is not None
                    if cached:doc=json.loads(cached[0]);doc['materialId']=m['id']
                    else:
                        doc=None
                        if self.model_first:
                            from business_agent.activity_model_first.coupon_ledger import ledger_document
                            doc=ledger_document(m['id'],m['filename'],m['media_type'],m['sha256'],m['content'])
                        if doc is None:
                            doc=self.wire(rid,'decode',{'materialId':m['id'],'filename':m['filename'],'mediaType':m['media_type'],'sha256':m['sha256'],'contentBase64':base64.b64encode(m['content']).decode()},deadline)
                        try:validate_worker_result(doc,'document')
                        except (ValueError,TypeError,KeyError):raise JobFailure('INVALID_AGENT_OUTPUT')
                        if not isinstance(doc,dict) or doc.get('materialId')!=m['id'] or doc.get('sha256')!=m['sha256'] or doc.get('status') not in ['parsed','unsupported','invalid','partial'] or not isinstance(doc.get('parserVersion'),str):raise JobFailure('INVALID_AGENT_OUTPUT')
                        if doc['status']=='parsed':
                            with self.auth.tx() as db:db.execute('INSERT OR IGNORE INTO parse_cache VALUES(?,?,?,?,?)',(m['sha256'],doc['parserVersion'],limits_key,cache_engine,canon(doc)))
                if m['id']!=source['formMaterialId']:documents.append(doc)
                payload['materialResults'].append(doc|{'cacheHit':hit});payload['phase']='decoding';self.progress(rid,payload)
            if self.model_first and any(d['status']!='parsed' or d['errors'] for d in documents):
                failure=JobFailure('LOCAL_MATERIAL_READ_FAILED')
                failure.details={'stage':'local_decode','agentRequestSent':False}
                raise failure
            payload['phase']='local_planning' if self.model_first else 'analyzing';self.progress(rid,payload)
            form_fields = recorded_form(source) if self.model_first and parent is None else source['form']
            request={'contractVersion':'activity-intake/1.0','inputRevision':run['input_revision'],'inputDigest':run['input_digest'],'manualFields':{'materialId':source['formMaterialId'],'sha256':next(m['sha256'] for m in materials if m['id']==source['formMaterialId']),'fields':form_fields},'documents':documents,'catalogSnapshot':catalog}
            legacy=parent is not None and LEGACY_REGROUP_ENABLED and parent['result']['engineVersion']=='1.0.5'
            if parent:
                group_request={'analysis':request,'caseId':meta['caseId'],
                    'partitions':meta['partitions'],'priorDecisions':meta['decisions'][:-1]}
                result=(self.wire(rid,'regroup',group_request,deadline,legacy_regroup=True) if legacy
                        else self.wire(rid,'regroup',group_request,deadline,regroup_engine=parent['result']['engineVersion']))
                engine_hash=parent['engineSourceSha256']
            elif self.model_first:
                request['materialNames'] = {m['id']: m['filename'] for m in materials
                                            if m['id'] != source['formMaterialId']}
                from b_model_recognition import ProviderFailure
                from b_model_intake import validate_metadata
                try:
                    engine_hash=self.model_agent.source_hash()
                    from business_agent.activity_model_first.source import EXCEL_LAYOUT,excel_budget_seconds,plan,scope_fingerprint
                    from business_agent.workbench_deepseek.adapter import AdapterError
                    try:
                        # The wire encoder sorts object keys, and the planner assigns
                        # evidence tokens in that order. Plan from the same JSON shape.
                        request = json.loads(canon(request))
                        planned, lookup, source_rows = plan(request)
                    except AdapterError as error:
                        if error.code not in MODEL_PLANNING_ERRORS:
                            raise
                        failure = JobFailure('AGENT_MODEL_' + error.code)
                        from b_model_intake import safe_error_details
                        failure.details = {**safe_error_details(getattr(error,'details',{})),
                                           'stage':'local_planning','agentRequestSent':False}
                        raise failure from None
                    from business_agent.activity_model_first.large_shenjia import LAYOUT as SHENJIA_LAYOUT
                    if any(batch.get('layout')==EXCEL_LAYOUT for batch in planned):
                        deadline=max(deadline,time.monotonic()+excel_budget_seconds(len(planned))+30)
                    elif any(batch.get('layout')==SHENJIA_LAYOUT for batch in planned):
                        deadline=max(deadline,time.monotonic()+max(2400,excel_budget_seconds(len(planned)))+30)
                    payload['phase']='agent_recognizing';self.progress(rid,payload)
                    result,metadata=self.model_agent.recognize('worker-'+rid+'-analyze',request,self.tokens[rid],deadline)
                    from b_coupon_membership_validation import verify_coupon_memberships
                    validate_metadata(metadata,result,planned_batches=planned,verify_shared_sources=lambda data:
                                      verify_coupon_memberships(data,request,lookup))
                    verify_model_scope(metadata, scope_fingerprint(planned), len(source_rows))
                    if metadata['coverage']['batches'] != len(planned):
                        raise JobFailure('AGENT_RESULT_SCOPE_MISMATCH')
                    payload['agentRecognition'].update(metadata,status='succeeded',reasonCode=None)
                except ProviderFailure as error:
                    failure=JobFailure('AGENT_MODEL_'+error.reason)
                    from b_model_intake import safe_error_details
                    failure.details=safe_error_details(getattr(error,'details',{}))
                    raise failure
            else:
                result=self.wire(rid,'analyze',request,deadline)
                if self.local_only:
                    apply_local_workbook_rules(result, materials)
            result=self.validate_result(result,run,documents,source['formMaterialId'],materials,
                                        engine_version=parent['result']['engineVersion'] if parent else None)
            if self.model_first and splits_single_child_export(result,request['manualFields'],request['materialNames']):
                raise JobFailure(GROUPING_ERROR)
            if parent:
                before=parent['result']
                offset=len(before['candidates'])
                if (result['candidates'][:offset]!=before['candidates']
                        or result['evidence'][:len(before['evidence'])]!=before['evidence']
                        or result['materialResults']!=documents
                        or len(result['candidates'])-offset!=len(meta['partitions'])):
                    raise JobFailure('REGROUP_SOURCE_CHANGED')
                groups=(parent.get('_grouping') or {}).get('siblingGroups',[])
                meta['siblingGroups']=[*groups,[c['candidateId'] for c in result['candidates'][offset:]]]
            payload.update(phase='complete',limitsVersion=LIMITS_VERSION,result=result,analysisDigest='sha256:'+fingerprint(result),engineSourceSha256=engine_hash,error=None)
            state='partial' if any(d['status']!='parsed' for d in documents) else 'succeeded'
            with self.auth.tx() as db:
                current=db.execute('SELECT state FROM analysis_runs WHERE id=?',(rid,)).fetchone()
                if not current or current['state'] not in ('running','cancel_requested'):return
                if current['state']=='cancel_requested' or self.tokens[rid].is_set():raise JobFailure('ANALYSIS_CANCELLED')
                sub=db.execute('SELECT revision,state,submitter_id FROM b_submissions WHERE id=?',(run['submission_id'],)).fetchone()
                if not sub or sub['state']=='deleted' or sub['revision']!=run['input_revision']:
                    raise JobFailure('ANALYSIS_STALE')
                if meta:self.grouping_parent(db,run,sub,meta,source)
                db.execute('UPDATE analysis_runs SET state=?,payload=?,finished_at=? WHERE id=?',(state,canon(payload),self.clock(),rid))
                done={r[0] for r in db.execute('SELECT candidate_id FROM resolutions WHERE submission_id=? AND input_revision=?',(run['submission_id'],run['input_revision']))}
                remaining=[c['candidateId'] for c in result['candidates'] if c['candidateId'] not in done]
                sub_state=('partially_resolved' if remaining else 'resolved') if done else result['outcome']
                db.execute("UPDATE b_submissions SET state=? WHERE id=? AND revision=? AND state<>'deleted'",
                           (sub_state,run['submission_id'],run['input_revision']))
                bump(db)
        except Exception as error:
            code=error.code if isinstance(error,JobFailure) else 'INVALID_AGENT_OUTPUT'
            with self.auth.tx() as db:
                current=db.execute('SELECT state,submission_id,input_revision FROM analysis_runs WHERE id=?',(rid,)).fetchone()
                if not current or current['state'] not in ('queued','running','cancel_requested'):return
                if current['state']=='cancel_requested' and not self.closing:code='ANALYSIS_CANCELLED'
                state='interrupted' if self.closing or code=='AGENT_INTERRUPTED' else 'cancelled' if code=='ANALYSIS_CANCELLED' else 'failed'
                if self.model_first:
                    message = MODEL_FAILURE_MESSAGES.get(code, 'Agent 识别未完成；原件已保留，请检查原因后显式重试')
                else:
                    message = '本地分析未完成；原件与成功材料结果保留，可显式重试'
                payload.update(phase=state, error={'code': code, 'message': message})
                if getattr(error,'details',None):payload['error']['details']=error.details
                if payload.get('agentRecognition'):payload['agentRecognition'].update(status=state,reasonCode=code)
                db.execute('UPDATE analysis_runs SET state=?,payload=?,finished_at=? WHERE id=?',(state,canon(payload),self.clock(),rid))
                self.failure_state(db,current['submission_id'],current['input_revision'],
                                   'needs_input' if state=='cancelled' else 'failed',bool(payload.get('_grouping')))
                bump(db)
        finally:
            if release:self.release_reservation(rid)
