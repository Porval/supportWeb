"""JT-024 explicitly authorized, read-only model suggestions for completed intake runs."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from itertools import chain
import json
import re
import threading
import time

from account_primitives import APIError, fail, iso
from b_domain import can_material, history, owns_submission, sub_revision, verify_declaration_binding
from b_state import canon, fingerprint, new_id

CONTRACT = 'activity-model-recognition/1.0'
ACTIVE = ('queued', 'running')
PREFIX = 'MODEL_RECOGNITION_'
MAX_EXCERPTS = 48
MAX_TEXT_BYTES = 24000
MAX_EXCERPT_CHARS = 800
PROVIDER_ERRORS = {'PROVIDER_AUTH': 'AUTH_FAILED', 'PROVIDER_QUOTA': 'CAPACITY',
                   'PROVIDER_UNAVAILABLE': 'UNAVAILABLE'}
HEADER_LABELS = {'活动名称', '活动名', '活动类型', '平台', '平台活动编号', '活动编号',
                 '开始时间', '结束时间', '品牌ID', '优惠机制', '品牌利益点', '备注',
                 '公司承担', '内部商品码', '内部码', 'SKU', 'UPC', '条码', '商品名称',
                 '商品名', '规格', '品牌'}
EXPLICIT_TEXT_LABELS = {
    'activityName': ('活动名称', '活动名', '活动主题'),
    'rulesText': ('优惠机制', '优惠规则', '活动规则', '报名规则'),
    'brandBenefit': ('品牌利益点', '品牌权益'),
}
FIELD_LIMITS = {
    'activityName': 200, 'platformCode': 32, 'sourceActivityId': 200,
    'startAt': 200, 'endAt': 200, 'rulesText': 10000, 'brandBenefit': 5000,
    'brandNameText': 200,
}
PLATFORM_TEXT = {
    'meituan': ('meituan', '美团'),
    'taobao_instant': ('taobao_instant', '淘宝闪购', '淘宝'),
    'jiatan': ('jiatan', '价探'),
}
ANCHOR_FIELDS = ('activityName', 'sourceActivityId')
BINDING_KEYS = ('inputRevision', 'inputDigest', 'analysisDigest', 'catalogRevision')
DIGEST_KEYS = BINDING_KEYS + ('attemptId', 'inputScopeDigest', 'model', 'thinking', 'promptVersion', 'suggestions')
ACTIVE_RUNS = """
    SELECT * FROM analysis_runs
    WHERE json_extract(payload,'$.modelRecognition.status') IN ('queued','running')
"""
EXCLUDED_TEXT = re.compile(
    r'https?://|(?:^|\b)(?:api[_ -]?key|authorization|password|secret[_ -]?key|access[_ -]?token)\s*[:=]'
    r'|(?:密码|密钥|访问令牌)\s*[:：=]|\bBearer\s+\S+|-----BEGIN [A-Z ]*PRIVATE KEY-----'
    r'|\bsk-[A-Za-z0-9_-]{16,}', re.IGNORECASE
)
EXAMPLE = re.compile(r'^\s*(?:示例(?:行)?|example|sample|请填写|待填写)\s*(?:[:：]|$)', re.IGNORECASE)


class ProviderFailure(Exception):
    """Provider errors carry stable reasons and no provider response text."""

    def __init__(self, reason):
        self.reason = reason


def reject(status, reason, message):
    fail(status, PREFIX + reason, message)


def stale():
    reject(409, 'STALE', '活动提报、分析或原文范围已变化，请刷新后重试')


def invalid_input():
    reject(422, 'INPUT_INVALID', '请从当前预览选择不同的片段并确认发送')


def digest(value):
    return 'sha256:' + fingerprint(value)


def suggestion_digest(run_id, value):
    return digest({'runId': run_id, **{key: value[key] for key in DIGEST_KEYS}})


def location_key(material_id, locator):
    return material_id, canon(locator)


def preview_item(item, excluded):
    locator = item['locator']
    parent = {k: v for k, v in locator.items() if k not in ('column', 'cell')}
    text = item['text']
    return (item.get('sourceType') != 'formula' and text.strip()
            and canon(locator) not in excluded and canon(parent) not in excluded
            and not EXCLUDED_TEXT.search(text) and not EXAMPLE.match(text))


def build_excerpts(source, payload, originals, db, actor, auth):
    excerpts = []
    remaining = MAX_TEXT_BYTES
    truncated = False
    docs = {doc['materialId']: doc for doc in payload['result']['materialResults']}
    decoded = {doc['materialId']: {k: v for k, v in doc.items() if k != 'cacheHit'}
               for doc in payload['materialResults'] if doc['materialId'] != source['formMaterialId']}
    if docs != decoded:
        stale()
    example_locations = {}
    for doc in docs.values():
        example_locations[doc['materialId']] = {
            canon(w['locator']) for w in doc['warnings']
            if w['code'] == 'TEMPLATE_EXAMPLE_SKIPPED' and 'locator' in w
        }
    # The analysis warnings also carry template locations but may omit material
    # IDs. Treat a matching location conservatively across the bound documents.
    examples = {
        canon(w['locator']) for w in payload['result']['warnings']
        if w['code'] == 'TEMPLATE_EXAMPLE_SKIPPED' and 'locator' in w
    }
    for mid in source['allMaterialIds']:
        if mid == source['formMaterialId'] or not can_material(db, actor, mid, auth):
            continue
        doc = docs.get(mid)
        if not doc or doc['status'] not in ('parsed', 'partial'):
            continue
        sha = originals[mid]['sha256']
        if doc['sha256'] != sha:
            stale()
        truncated |= doc['status'] == 'partial'
        items = chain(doc['textBlocks'], (cell for row in doc['tableRows'] for cell in row['cells']
                                         if cell['text'].strip() not in HEADER_LABELS))
        for item in items:
            if not preview_item(item, examples | example_locations[mid]):
                continue
            text = item['text']
            if len(excerpts) == MAX_EXCERPTS:
                return excerpts, True
            clipped = text[:MAX_EXCERPT_CHARS].encode()[:remaining].decode('utf-8', errors='ignore')
            if not clipped.strip():
                return excerpts, True
            truncated |= clipped != text
            remaining -= len(clipped.encode())
            excerpts.append({'excerptId': f'excerpt-{len(excerpts)+1}', 'materialId': mid,
                             'locator': item['locator'], 'text': clipped, 'sourceSha256': sha,
                             'complete': clipped == text})
    return excerpts, truncated


def evidence_map(analysis):
    return {ev['evidenceId']: location_key(ev['materialId'], ev['locator']) for ev in analysis['evidence']}


def candidate_locations(analysis, selected):
    """Only link existing groups via authorized name/ID anchors."""
    locations = evidence_map(analysis)
    excerpts = {location_key(e['materialId'], e['locator']): e['excerptId'] for e in selected}
    owners, anchors, fields = {}, {}, {}
    for candidate in analysis['candidates']:
        cid = candidate['candidateId']
        fields[cid] = {}
        for field, value in candidate['fields'].items():
            ids = {excerpts[locations[eid]] for eid in value['evidenceIds'] if locations[eid] in excerpts}
            fields[cid][field] = ids
            if field in ANCHOR_FIELDS and value['status'] in ('explicit', 'conflict'):
                anchors.setdefault(cid, set()).update(ids)
            for eid in ids:
                owners.setdefault(eid, set()).add(cid)
        for product in candidate['productRows']:
            for loc in (locations[eid] for eid in product['evidenceIds']):
                if loc in excerpts:
                    owners.setdefault(excerpts[loc], set()).add(cid)
    by_id = {e['excerptId']: e for e in selected}
    for cid, ids in anchors.items():
        anchors[cid] = {eid for eid in ids if owners[eid] == {cid}}
    # An explicit rule/benefit immediately following a known activity name
    # has the same source scope even when the old parser missed its label.
    for cid, names in fields.items():
        for eid in names.get('activityName', set()) & anchors.get(cid, set()):
            origin = by_id[eid]
            locator = origin['locator']
            position = {'text': 'line', 'docx': 'paragraph'}.get(locator['kind'])
            if position not in locator:
                continue
            for excerpt in selected:
                excerpt_id, current = excerpt['excerptId'], excerpt['locator']
                if (excerpt_id not in owners and excerpt['materialId'] == origin['materialId']
                        and current['kind'] == locator['kind']
                        and current.get(position) == locator[position] + 1
                        and any(labeled_text(excerpt, field) is not None for field in ('rulesText', 'brandBenefit'))):
                    owners[excerpt_id] = {cid}
    return owners, anchors, fields


def source_support(field, value, quotes):
    if value is None:
        return False
    if field == 'platformCode':
        return any(any(token in quote for token in PLATFORM_TEXT[value]) for quote in quotes)
    if field == 'sourceActivityId':
        return any(re.search(r'(?<![A-Za-z0-9_-])' + re.escape(value) + r'(?![A-Za-z0-9_-])', quote) for quote in quotes)
    return any(value in quote for quote in quotes)


def precise_date(value):
    try:
        return datetime.fromisoformat(value.replace('Z', '+00:00')).tzinfo is not None
    except (AttributeError, ValueError):
        return False


def require_output(condition):
    if not condition:
        raise ProviderFailure('INVALID_OUTPUT')


def labeled_text(excerpt, field):
    if not excerpt['complete']:
        return None
    match = re.fullmatch(r'([^:：]+)[:：]\s*(.+?)\s*', excerpt['text'])
    if match and match[1].strip() in EXPLICIT_TEXT_LABELS.get(field, ()):
        return match[2]


def validate_suggestions(raw, analysis, selected, locations):
    owners, anchors, field_locations = locations
    excerpts = {e['excerptId']: e for e in selected}
    require_output(isinstance(raw, dict) and set(raw) == {'groups'}
                   and isinstance(raw['groups'], list) and len(raw['groups']) <= 20)
    evidence, result_groups, claimed_candidates, claimed_excerpts = {}, [], set(), set()
    candidates = {c['candidateId']: c for c in analysis['candidates']}
    for group in raw['groups']:
        require_output(isinstance(group, dict) and set(group) == {'candidateId', 'fields'})
        proposed = group['candidateId']
        require_output((proposed is None or isinstance(proposed, str) and proposed in candidates)
                       and isinstance(group['fields'], list) and len(group['fields']) <= len(FIELD_LIMITS))
        fields, group_ids = {}, set()
        for item in group['fields']:
            require_output(isinstance(item, dict) and set(item) == {'field', 'value', 'refs'})
            name, value, refs = item['field'], item['value'], item['refs']
            require_output(isinstance(name, str) and name in FIELD_LIMITS and name not in fields
                           and (value is None or isinstance(value, str) and 0 < len(value) <= FIELD_LIMITS[name])
                           and (name != 'platformCode' or value is None or value in PLATFORM_TEXT)
                           and isinstance(refs, list) and len(refs) <= MAX_EXCERPTS)
            citations, citation_excerpts, quotes = [], set(), []
            for ref in refs:
                require_output(isinstance(ref, dict) and set(ref) == {'excerptId', 'quote'})
                eid, quote = ref['excerptId'], ref['quote']
                require_output(isinstance(eid, str) and eid in excerpts and isinstance(quote, str)
                               and 0 < len(quote) <= 500 and bool(quote.strip()) and quote in excerpts[eid]['text'])
                origin = excerpts[eid]
                reference = 'model-evidence-' + fingerprint([eid, quote])
                require_output(reference not in citations)
                citations.append(reference)
                citation_excerpts.add(eid)
                quotes.append(quote)
                evidence[reference] = {'evidenceId': reference, 'materialId': origin['materialId'],
                                       'sourceSha256': origin['sourceSha256'], 'locator': origin['locator'],
                                       'quote': quote}
            group_ids.update(citation_excerpts)
            value_proven = source_support(name, value, quotes)
            fields[name] = (value if value_proven else None, citations, citation_excerpts)
        group_owners = set().union(*(owners.get(eid, set()) for eid in group_ids))
        require_output(len(group_owners) <= 1 and not claimed_excerpts & group_ids)
        if proposed:
            require_output(bool(anchors.get(proposed))
                           and all(owners.get(eid) == {proposed} for eid in group_ids))
        cid = proposed if group_ids else None
        if cid:
            require_output(cid not in claimed_candidates)
            claimed_candidates.add(cid)
        claimed_excerpts.update(group_ids)
        output = []
        for name, (value, citations, ids) in fields.items():
            known = candidates[cid]['fields'].get(name, {}) if cid else {}
            supported = (value is not None and known.get('status') == 'explicit'
                         and value == known.get('value') and bool(ids)
                         and ids <= field_locations[cid].get(name, set()))
            if cid and known.get('status') == 'missing':
                supported = (value is not None and bool(ids)
                             and all(owners.get(eid) == {cid} and labeled_text(excerpts[eid], name) == value for eid in ids))
            if name in ('startAt', 'endAt'):
                supported = supported and precise_date(value)
            output.append({'field': name, 'value': value, 'confidence': 'supported' if supported else 'needs_review',
                           'evidenceIds': citations})
        identity_fields = [field for field in output if field['field'] in ('platformCode', 'sourceActivityId')]
        if len(identity_fields) != 2 or any(field['confidence'] != 'supported' for field in identity_fields):
            for field in identity_fields:
                field['confidence'] = 'needs_review'
        result_groups.append({'candidateId': cid, 'fields': output})
    return {'groups': result_groups, 'evidence': list(evidence.values())}


class ModelRecognition:
    def __init__(self, auth, clock, provider=None):
        self.auth, self.clock, self.provider = auth, clock, provider
        self.pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix='jt024-model')
        self.lock = threading.Lock()
        self.command_lock = threading.Lock()
        self.task = None
        with auth.tx() as db:
            for run in db.execute(ACTIVE_RUNS).fetchall():
                value = json.loads(run['payload'])['modelRecognition']
                self.finish(db, run, value, 'interrupted', PREFIX + 'INTERRUPTED')

    def close(self):
        with self.lock:
            if self.task is not None:
                self.task[1].set()
        self.pool.shutdown(wait=True)

    def capability(self):
        return {'contractVersion': CONTRACT, 'provider': 'deepseek',
                'enabled': self.provider is not None and self.provider.available()}

    def bound_preview(self, db, actor, run_id):
        run = db.execute('SELECT * FROM analysis_runs WHERE id=?', (run_id,)).fetchone()
        if not run:
            fail(404, 'NOT_FOUND', '分析不存在')
        sub = owns_submission(db, actor, run['submission_id'], self.auth, True)
        owns_submission(db, actor, sub['id'], self.auth)
        payload = json.loads(run['payload'])
        catalog = json.loads(db.execute("SELECT value FROM b_meta WHERE key='catalogRevision'").fetchone()[0])
        current_run = db.execute(
            'SELECT id FROM analysis_runs WHERE submission_id=? AND input_revision=? ORDER BY created_at DESC,rowid DESC LIMIT 1',
            (sub['id'], sub['revision']),
        ).fetchone()
        if (run['input_revision'] != sub['revision'] or run['catalog_revision'] != catalog
                or not current_run or current_run[0] != run_id or run['state'] not in ('succeeded', 'partial')
                or not isinstance(payload.get('result'), dict) or not payload.get('analysisDigest')
                or sub['state'] not in ('needs_input', 'needs_confirmation') or sub['activity_id'] or sub['version_id']
                or db.execute('SELECT 1 FROM resolutions WHERE submission_id=?', (sub['id'],)).fetchone()):
            stale()
        source = sub_revision(db, sub['id'], sub['revision'])
        try:
            originals = verify_declaration_binding(db, sub, run, payload['result'], payload['analysisDigest'], source)
        except APIError as error:
            if error.code != 'ANALYSIS_STALE':
                raise
            stale()
        excerpts, truncated = build_excerpts(source, payload, originals, db, actor, self.auth)
        binding = {'runId': run_id, 'inputRevision': run['input_revision'], 'inputDigest': run['input_digest'],
                   'analysisDigest': payload['analysisDigest'], 'catalogRevision': catalog}
        preview = {'runId': run_id, 'inputRevision': binding['inputRevision'], 'analysisDigest': binding['analysisDigest'],
                   'previewDigest': digest(binding | {'ruleVersion': CONTRACT, 'excerpts': excerpts, 'truncated': truncated}),
                   'excerpts': [{key: e[key] for key in ('excerptId', 'materialId', 'locator', 'text')} for e in excerpts],
                   'truncated': truncated}
        return run, payload, binding, preview, excerpts

    def preview(self, db, actor, run_id):
        _, _, _, preview, _ = self.bound_preview(db, actor, run_id)
        if not self.capability()['enabled']:
            reject(503, 'UNAVAILABLE', 'DeepSeek 识别暂不可用')
        return preview

    def start(self, db, actor, run_id, body, command_id):
        keys = {'expectedInputRevision', 'analysisDigest', 'previewDigest', 'excerptIds', 'consent'}
        if (not isinstance(body, dict) or set(body) != keys or body['consent'] is not True
                or type(body['expectedInputRevision']) is not int
                or not isinstance(body['analysisDigest'], str) or not isinstance(body['previewDigest'], str)
                or not isinstance(body['excerptIds'], list) or not 0 < len(body['excerptIds']) <= MAX_EXCERPTS
                or any(not isinstance(eid, str) for eid in body['excerptIds'])
                or len(set(body['excerptIds'])) != len(body['excerptIds'])):
            invalid_input()
        run, payload, binding, preview, excerpts = self.bound_preview(db, actor, run_id)
        if (binding['inputRevision'] != body['expectedInputRevision']
                or any(preview[key] != body[key] for key in ('analysisDigest', 'previewDigest'))):
            stale()
        wanted = set(body['excerptIds'])
        selected = [e for e in excerpts if e['excerptId'] in wanted]
        if len(wanted) != len(selected):
            invalid_input()
        previous = payload.get('modelRecognition', {})
        if previous.get('status') in ACTIVE:
            reject(409, 'ACTIVE', '这次分析已有进行中的模型识别')
        if previous.get('status') == 'succeeded':
            reject(409, 'ALREADY_SUCCEEDED', '此范围已有识别结果，请直接查看')
        if not self.capability()['enabled']:
            reject(503, 'UNAVAILABLE', 'DeepSeek 识别暂不可用')
        if db.execute(ACTIVE_RUNS + ' LIMIT 1').fetchone() or self.busy():
            reject(429, 'CAPACITY', '当前已有模型识别在运行，请稍后再试')
        value = {key: binding[key] for key in BINDING_KEYS} | {
            'contractVersion': CONTRACT, 'attemptId': new_id('MODEL'), 'status': 'queued',
            'requestedAt': iso(self.clock()), 'finishedAt': None, 'model': None, 'thinking': None,
            'promptVersion': None, 'inputScopeDigest': self.scope(preview, selected), 'reasonCode': None,
        }
        self.save(db, run['id'], value)
        self.audit(db, run, value, actor, command_id=command_id)
        return 202, self.receipt(run['id'], value)

    @staticmethod
    def scope(preview, selected):
        return digest({'previewDigest': preview['previewDigest'], 'excerpts': selected})

    @staticmethod
    def receipt(run_id, value):
        return {'runId': run_id, 'attemptId': value['attemptId'], 'status': value['status']}

    def audit(self, db, run, value, actor=None, *, command_id=None, before=None):
        actor = actor or {'id': None, 'name': None, 'business_role': None}
        details = self.receipt(run['id'], value) | {
            'inputScopeDigest': value['inputScopeDigest'], 'reasonCode': value['reasonCode'],
        }
        if command_id is not None:
            details.update(commandId=command_id, consentBy=actor['id'])
        history(db, actor, self.clock(), submission=run['submission_id'],
                event='analysis.model_recognition', before=before, after=value['status'], details=details)

    def save(self, db, run_id, value, recognition_digest=None):
        db.execute("""UPDATE analysis_runs
            SET payload=json_set(json_remove(payload,'$.modelRecognitionDigest'),'$.modelRecognition',json(?))
            WHERE id=?""", (canon(value), run_id))
        if recognition_digest:
            db.execute("UPDATE analysis_runs SET payload=json_set(payload,'$.modelRecognitionDigest',?) WHERE id=?",
                       (recognition_digest, run_id))

    def finish(self, db, run, value, status, reason, actor=None, success=None):
        final = value | {'status': status, 'reasonCode': reason, 'finishedAt': iso(self.clock())}
        recognition_digest = None
        if success is not None:
            final.update(success)
            recognition_digest = suggestion_digest(run['id'], final)
        self.save(db, run['id'], final, recognition_digest)
        self.audit(db, run, final, actor, before=value['status'])
        return final

    def cancel(self, db, actor, run_id, body):
        if (not isinstance(body, dict) or set(body) != {'attemptId'}
                or not isinstance(body['attemptId'], str) or not body['attemptId']):
            invalid_input()
        run = db.execute('SELECT * FROM analysis_runs WHERE id=?', (run_id,)).fetchone()
        value = json.loads(run['payload']).get('modelRecognition') if run else None
        if not value or value['attemptId'] != body['attemptId']:
            prior = db.execute("""SELECT json_extract(payload,'$.details.status') AS status FROM b_history
                WHERE submission_id=? AND json_extract(payload,'$.event')='analysis.model_recognition'
                AND json_extract(payload,'$.details.runId')=? AND json_extract(payload,'$.details.attemptId')=?
                AND json_extract(payload,'$.details.status') NOT IN ('queued','running')""",
                               (run['submission_id'], run_id, body['attemptId'])).fetchone() if run else None
            if prior is None:
                fail(404, 'NOT_FOUND', '识别尝试不存在')
            return 200, self.receipt(run_id, {'attemptId': body['attemptId'], 'status': prior['status']})
        if value['status'] in ACTIVE:
            value = self.finish(db, run, value, 'cancelled', PREFIX + 'CANCELLED', actor)
        return 200, self.receipt(run_id, value)

    def busy(self):
        with self.lock:
            return self.task is not None and not self.task[2].done()

    def enqueue(self, sid, run_id, attempt_id, body):
        stop = threading.Event()
        with self.lock:
            future = self.pool.submit(self.run, sid, run_id, attempt_id, set(body['excerptIds']),
                                      body['previewDigest'], stop)
            self.task = (attempt_id, stop, future)

    def stop(self, attempt_id):
        with self.lock:
            if self.task is not None and self.task[0] == attempt_id:
                self.task[1].set()

    def check_current(self, db, sid, run_id, value, selected_ids, preview_digest):
        _, actor = self.auth.valid(db, sid, True)
        run, payload, binding, preview, excerpts = self.bound_preview(db, actor, run_id)
        selected = [e for e in excerpts if e['excerptId'] in selected_ids]
        if (len(selected) != len(selected_ids) or preview['previewDigest'] != preview_digest
                or value['inputScopeDigest'] != self.scope(preview, selected)
                or any(value[key] != binding[key] for key in BINDING_KEYS)):
            stale()
        return actor, selected, payload['result']

    def run(self, sid, run_id, attempt_id, selected_ids, preview_digest, stop):
        actor = None
        deadline = time.monotonic() + 60
        with self.auth.tx() as db:
            run, value = self.attempt(db, run_id, attempt_id, 'queued')
            if value is None:
                return
            if stop.is_set():
                self.finish(db, run, value, 'interrupted', PREFIX + 'INTERRUPTED')
                return
            try:
                actor, selected, analysis = self.check_current(db, sid, run_id, value, selected_ids, preview_digest)
            except APIError:
                self.finish(db, run, value, 'stale', PREFIX + 'STALE')
                return
            value['status'] = 'running'
            self.save(db, run_id, value)
            self.audit(db, run, value, actor, before='queued')
        success = None
        try:
            locations = candidate_locations(analysis, selected)
            owners, anchors, _ = locations
            unique_anchors = {cid: sorted(eid for eid, groups in owners.items() if groups == {cid})
                              for cid, ids in anchors.items() if ids}
            request = {
                'excerpts': [{k: e[k] for k in ('excerptId', 'materialId', 'locator', 'text')} for e in selected],
                'candidateAnchors': {cid: ids for cid, ids in unique_anchors.items() if ids},
            }
            if stop.is_set():
                raise ProviderFailure('INTERRUPTED')
            if self.provider is None:
                raise ProviderFailure('UNAVAILABLE')
            metadata, raw = self.provider.recognize(request, stop, deadline)
            if time.monotonic() >= deadline:
                raise ProviderFailure('TIMEOUT')
            require_output(isinstance(metadata, dict) and set(metadata) == {'model', 'thinking', 'promptVersion'}
                           and metadata['thinking'] in ('enabled', 'unknown')
                           and all(isinstance(metadata[k], str) and 0 < len(metadata[k]) <= 200
                                   for k in ('model', 'promptVersion')))
            success = metadata | {'suggestions': validate_suggestions(raw, analysis, selected, locations)}
            status, reason = 'succeeded', None
        except ProviderFailure as error:
            status = 'interrupted' if error.reason == 'INTERRUPTED' else 'failed'
            reason = PREFIX + PROVIDER_ERRORS.get(error.reason, error.reason)
        except Exception:
            status, reason = 'failed', PREFIX + 'INVALID_OUTPUT'
        with self.auth.tx() as db:
            run, value = self.attempt(db, run_id, attempt_id, 'running')
            if value is None:
                return
            try:
                self.check_current(db, sid, run_id, value, selected_ids, preview_digest)
            except APIError:
                status, reason, success = 'stale', PREFIX + 'STALE', None
            if stop.is_set() and status != 'stale':
                status, reason, success = 'interrupted', PREFIX + 'INTERRUPTED', None
            if status == 'succeeded' and time.monotonic() >= deadline:
                status, reason, success = 'failed', PREFIX + 'TIMEOUT', None
            self.finish(db, run, value, status, reason, actor, success)

    @staticmethod
    def attempt(db, run_id, attempt_id, status):
        run = db.execute('SELECT * FROM analysis_runs WHERE id=?', (run_id,)).fetchone()
        value = json.loads(run['payload']).get('modelRecognition') if run else None
        if not value or value['attemptId'] != attempt_id or value['status'] != status:
            return run, None
        return run, value

    def projected_suggestions(self, db, actor, view, value, suggestions, persisted_digest):
        if suggestions is None or persisted_digest != suggestion_digest(view['runId'], value | {'suggestions': suggestions}):
            return None
        run = db.execute('SELECT * FROM analysis_runs WHERE id=?', (view['runId'],)).fetchone()
        if (run['input_digest'] != value['inputDigest'] or run['input_revision'] != value['inputRevision']
                or run['catalog_revision'] != value['catalogRevision'] or view.get('analysisDigest') != value['analysisDigest']):
            return None
        try:
            source = sub_revision(db, run['submission_id'], run['input_revision'])
            originals = verify_declaration_binding(
                db, {'id': run['submission_id'], 'revision': run['input_revision']},
                run, view['result'], value['analysisDigest'], source,
            )
        except (APIError, KeyError, TypeError, ValueError):
            return None
        analysis = view['result']
        candidates = {c['candidateId']: c for c in analysis['candidates']}
        if any(g['candidateId'] is not None and g['candidateId'] not in candidates for g in suggestions['groups']):
            return None
        readable_sources = {mid: row['sha256'] for mid, row in originals.items()
                            if mid != source['formMaterialId'] and can_material(db, actor, mid, self.auth)}
        evidence = [e for e in suggestions['evidence'] if readable_sources.get(e['materialId']) == e['sourceSha256']]
        readable = {e['evidenceId'] for e in evidence}
        visible_local_anchors = {e['evidenceId'] for e in analysis['evidence']
                                 if readable_sources.get(e['materialId']) == e['sourceSha256']}
        groups = []
        for group in suggestions['groups']:
            cid = group['candidateId']
            if cid and not any(visible_local_anchors.intersection(candidates[cid]['fields'][name]['evidenceIds'])
                               for name in ANCHOR_FIELDS):
                cid = None
            fields = [field if cid else field | {'confidence': 'needs_review'} for field in group['fields']
                      if set(field['evidenceIds']) <= readable]
            if fields:
                groups.append({'candidateId': cid, 'fields': fields})
        return {'groups': groups, 'evidence': evidence}

    def project(self, db, actor, view, sub):
        value = view.pop('modelRecognition', None)
        persisted_digest = view.pop('modelRecognitionDigest', None)
        if value is None:
            return
        value = dict(value)
        suggestions = value.pop('suggestions', None)
        if value['status'] == 'succeeded':
            projected = self.projected_suggestions(db, actor, view, value, suggestions, persisted_digest)
            if projected is None:
                value.update(status='failed', reasonCode=PREFIX + 'INVALID_OUTPUT')
            else:
                value['suggestions'] = projected
        catalog = json.loads(db.execute("SELECT value FROM b_meta WHERE key='catalogRevision'").fetchone()[0])
        if (sub['revision'] != value['inputRevision'] or sub['activity_id'] or sub['version_id']
                or catalog != value['catalogRevision'] or view.get('analysisDigest') != value['analysisDigest']
                or db.execute('SELECT 1 FROM resolutions WHERE submission_id=?', (sub['id'],)).fetchone()):
            value.update(status='stale', reasonCode=PREFIX + 'STALE')
        view['modelRecognition'] = value
