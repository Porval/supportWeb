"""Authorize and preserve local, append-only coupon regrouping decisions."""
import hashlib
import json

from account_primitives import fail
from b_domain import can_material, history, latest_run_id, owns_submission, sub_revision, verify_declaration_binding
from b_state import fingerprint, new_id

VERSION = 'coupon-grouping/1.0'
ACTION = 'submissions.grouping_decide'
ENUMERATE = None
LEGACY_ENGINES = {}
CURRENT_ENGINE = '1.0.5'
INVALID = 'INVALID_GROUPING_DECISION'


def invalid():
    fail(422, INVALID, '请将本案的每个原文单元恰好分配一次')


def stale():
    fail(409, 'ANALYSIS_STALE', '原分析、目录或分组依据已变化')


def available():
    return ENUMERATE is not None


def cases(db, actor, auth, run, payload, *, include_bound=False):
    if not available() or run['state'] != 'succeeded':
        return []
    result = payload.get('result')
    if (not result or LEGACY_ENGINES.get(payload.get('engineSourceSha256'))!=result['engineVersion']
            or len(result['candidates'])>=20
            or payload.get('analysisDigest') != 'sha256:' + fingerprint(result)):
        return []
    catalog = json.loads(db.execute("SELECT value FROM b_meta WHERE key='catalogRevision'").fetchone()[0])
    if run['catalog_revision'] != catalog:
        return []
    source = sub_revision(db, run['submission_id'], run['input_revision'])
    documents = result['materialResults']
    if (any(d['status'] != 'parsed' for d in documents)
            or any(w.get('code') == 'LIMIT_EXCEEDED' for w in result['warnings'])):
        return []
    meta = payload.get('_grouping') or {}
    used = {decision['caseId'] for decision in meta.get('decisions', [])}
    enumerated = ENUMERATE(documents)
    evidence = {e['evidenceId']:e for e in result['evidence']}
    occupied = {}
    known_titles = set()
    for candidate in result['candidates']:
        fields = candidate['fields']
        if fields['sourceActivityId']['value']:
            known_titles.add((fields['platformCode']['value'],fields['activityName']['value']))
        for part in [*fields.values(),*candidate['productRows']]:
            for eid in part['evidenceIds']:
                ev = evidence[eid]
                locator = ev['locator']
                if 'row' in locator:
                    key=(ev['materialId'],locator.get('sheet'),locator.get('table'),locator['row'])
                    occupied.setdefault(key,set()).add(candidate['candidateId'])
    readable = {}
    visible = []
    for case in enumerated:
        if case['caseId'] in used:
            continue
        bound=set()
        for unit in case['units']:
            mid = unit['materialId']
            loc = unit['locator']
            bound.update(occupied.get((mid,loc.get('sheet'),loc.get('table'),loc['row']),()))
            if mid in readable:
                continue
            original = db.execute('SELECT sha256,content FROM material_blobs WHERE id=?',(mid,)).fetchone()
            if (mid in source['allMaterialIds'] and original and can_material(db,actor,mid,auth)
                    and hashlib.sha256(original['content']).hexdigest() == original['sha256']):
                readable[mid] = original['sha256']
            else:
                readable[mid] = None
        if not all(readable.get(unit['materialId'])==unit['sourceSha256'] for unit in case['units']):
            continue
        if bound and not include_bound:
            continue
        if bound:
            case['affectedCandidateIds']=sorted(bound)
        # Preserve any existing numbered group sharing the same raw platform and title.
        first=case['units'][0]
        title=first['label'].rsplit(' · ',1)[0]
        if any(t==title for _,t in known_titles):
            source_doc=next(d for d in documents if d['materialId']==first['materialId'])
            loc=first['locator']
            row=next((r for r in source_doc['tableRows'] if r['locator']['row']==loc['row']
                      and r['locator'].get('sheet')==loc.get('sheet') and r['locator'].get('table')==loc.get('table')),None)
            if row:
                aliases={'美团':'meituan','淘宝':'taobao_instant','淘宝闪购':'taobao_instant','价探':'jiatan'}
                platforms={aliases.get(c['text'].strip(),c['text'].strip()) for c in row['cells']}
                if any(t==title and (platform is None or platform in platforms) for platform,t in known_titles):
                    continue
        visible.append(case)
    return visible


def project(db, actor, auth, view):
    if not available():
        return
    rid = view['runId']
    run = db.execute('SELECT * FROM analysis_runs WHERE id=?',(rid,)).fetchone()
    if not run:
        return
    payload = json.loads(run['payload'])
    meta = payload.get('_grouping')
    if meta and all(can_material(db,actor,mid,auth) for mid in meta['materialIds']):
        view['groupingDecision'] = {key:meta[key] for key in ('version','decisionId','caseId','sourceRunId','sourceAnalysisDigest')}
    current = db.execute('SELECT revision FROM b_submissions WHERE id=?',(run['submission_id'],)).fetchone()
    if not current or run['input_revision'] != current[0] or latest_run_id(db,run['submission_id'],current[0]) != rid:
        return
    visible = cases(db,actor,auth,run,payload)
    if visible:
        view['groupingCases'] = visible
    if meta and run['state'] in ('failed','cancelled','interrupted'):
        previous = db.execute('SELECT * FROM analysis_runs WHERE id=?',(meta['sourceRunId'],)).fetchone()
        if previous and any(c['caseId']==meta['caseId'] for c in cases(db,actor,auth,previous,json.loads(previous['payload']))):
            view['groupingRetry'] = {key:meta[key] for key in ('caseId','partitions','sourceRunId','sourceAnalysisDigest')}


def accept(domain, db, actor, sid, body, command_id):
    if not available():
        fail(405,'METHOD_NOT_ALLOWED','当前服务未启用分组处理')
    if (not isinstance(body,dict) or set(body)!={'expectedRevision','analysisRunId','analysisDigest','caseId','partitions'}
            or type(body['expectedRevision']) is not int
            or any(not isinstance(body[k],str) or not body[k] or len(body[k])>200
                   for k in ('analysisRunId','analysisDigest','caseId'))):
        invalid()
    sub = owns_submission(db,actor,sid,domain.auth,True)
    owns_submission(db,actor,sid,domain.auth)
    revision = sub['revision']
    if body['expectedRevision'] != revision:
        stale()
    rid = body['analysisRunId']
    current_id = latest_run_id(db,sid,revision)
    parent = db.execute('SELECT * FROM analysis_runs WHERE id=? AND submission_id=?',(rid,sid)).fetchone()
    if not parent or parent['input_revision'] != revision or parent['state'] != 'succeeded':
        stale()
    payload = json.loads(parent['payload'])
    if body['analysisDigest'] != payload.get('analysisDigest'):
        stale()
    retry = None
    if rid != current_id:
        latest = db.execute('SELECT state,payload FROM analysis_runs WHERE id=?',(current_id,)).fetchone()
        retry = json.loads(latest['payload']).get('_grouping') if latest else None
        if (not retry or latest['state'] not in ('failed','cancelled','interrupted') or retry['sourceRunId'] != rid
                or retry['sourceAnalysisDigest'] != body['analysisDigest'] or retry['caseId'] != body['caseId']
                or retry['partitions'] != body['partitions']):
            stale()
    catalog = json.loads(db.execute("SELECT value FROM b_meta WHERE key='catalogRevision'").fetchone()[0])
    if parent['catalog_revision'] != catalog:
        stale()
    original = sub_revision(db,sid,revision)
    verify_declaration_binding(db,sub,parent,payload['result'],body['analysisDigest'],original)
    visible = cases(db,actor,domain.auth,parent,payload,include_bound=True)
    case = next((item for item in visible if item['caseId']==body['caseId']),None)
    if case is None:
        invalid()
    if case['affectedCandidateIds']:
        fail(409,'STATE_CONFLICT','原文已归属既有候选，不能拆合')
    if db.execute("""SELECT 1 FROM analysis_runs WHERE submission_id=? AND input_revision=?
                      AND state IN ('queued','running','cancel_requested')""",(sid,revision)).fetchone():
        fail(409,'STATE_CONFLICT','该输入已有运行中的分析')
    expected = {unit['unitId'] for unit in case['units']}
    partitions = body['partitions']
    limit = 20 - len(payload['result']['candidates'])
    if not isinstance(partitions,list) or not 0<len(partitions)<=limit:
        invalid()
    seen = set()
    for part in partitions:
        if not isinstance(part,dict) or set(part)!={'unitIds'}:
            invalid()
        ids = part['unitIds']
        if not isinstance(ids,list) or not 0<len(ids)<=len(expected) or any(not isinstance(x,str) for x in ids):
            invalid()
        unique = set(ids)
        if len(unique)!=len(ids) or seen & unique or not unique <= expected:
            invalid()
        seen.update(unique)
    if seen != expected:
        invalid()
    previous = payload.get('_grouping') or {}
    mids = set(previous.get('materialIds',[])) | {u['materialId'] for u in case['units']}
    if not all(can_material(db,actor,mid,domain.auth) for mid in mids):
        fail(403,'FORBIDDEN','分组原件不可读取')
    decision = {'caseId':body['caseId'],'partitions':partitions}
    decision_id = retry['decisionId'] if retry else new_id('GROUP')
    meta = {'version':VERSION,'decisionId':decision_id,'sourceRunId':rid,
            'sourceAnalysisDigest':body['analysisDigest'],'actorId':actor['id'],'materialIds':sorted(mids),
            'caseId':body['caseId'],'partitions':partitions,'decisions':[*previous.get('decisions',[]),decision]}
    result = domain.queue_analysis(db,sub,{'_grouping':meta})
    try:
        history(db,actor,domain.clock(),submission=sid,event='analysis.grouping_retried' if retry else 'analysis.grouping_decided',
                details={'decisionId':decision_id,'caseId':body['caseId'],'sourceRunId':rid,'runId':result['runId'],
                         'analysisDigest':body['analysisDigest'],'partitions':partitions,'version':VERSION,'commandId':command_id})
    except BaseException:
        domain.jobs.release_reservation(result['runId'])
        raise
    return 202, {'decisionId':decision_id,**{k:result[k] for k in ('runId','inputRevision','state')}}


def siblings(payload, candidate):
    if candidate['fields']['sourceActivityId']['value']:
        return set()
    cid = candidate['candidateId']
    groups = (payload.get('_grouping') or {}).get('siblingGroups',[])
    return next((set(group)-{cid} for group in groups if cid in group),set())


def sibling_activity_ids(db, run, sibling_ids):
    rows = db.execute('SELECT candidate_id,activity_id,payload FROM resolutions WHERE submission_id=? AND input_revision=?',
                      (run['submission_id'],run['input_revision']))
    return {r['activity_id'] for r in rows if r['candidate_id'] in sibling_ids
            and json.loads(r['payload']).get('outcome')=='created'}
