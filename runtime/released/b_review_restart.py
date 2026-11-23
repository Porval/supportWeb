"""Create a fresh review version from a rejected one, retaining immutable history.

Call only inside Domain.command's transaction. No migration, source edits, source
resolution rewrites, draft copies, platform material copies, or external effects.
"""
import copy
import json
from account_primitives import APIError, fail, iso
from b_state import new_id
from b_domain import can_version, entity, fields, history, owns_submission, pending_items, put, text
from b_activity_method import read_activity_method

ACTION = 'reviews.restart'
CONTRACT = 'activity-review-restart/1.0'


def authorize(domain, db, actor, aid, vid):
    domain.auth.require(db, actor, 'operations.read', aid)
    domain.auth.require(db, actor, 'activities.propose')
    version = entity(db, 'activity_versions', vid)
    if version.get('activityId') != aid or not can_version(db, actor, vid, domain.auth):
        fail(404, 'NOT_FOUND', '活动版本不存在或不可访问')
    sub = db.execute('SELECT * FROM b_submissions WHERE id=?', (version.get('submissionId'),)).fetchone()
    if not sub or sub['state'] == 'deleted':
        fail(404, 'NOT_FOUND', '原提报不存在或不可访问')
    admin = domain.is_review_admin(actor)
    if sub['submitter_id'] != actor['id'] and not admin:
        fail(403, 'FORBIDDEN', '仅原提报人或管理员可发起重审')
    owns_submission(db, actor, sub['id'], domain.auth, True)
    return entity(db, 'activities', aid), version, sub


def projection_version(db, version):
    """Use original committed analysis for projection; recognition binds NEW vid.

Business.version calls source_projection(..., projection_version(db,value),
new_version_sources, ...). Never copy the unique resolutions rows to a new vid.
"""
    binding = (version.get('snapshot') or {}).get('reviewRestartBinding')
    if not binding:
        return version
    source_id = binding.get('sourceProjectionVersionId')
    if not isinstance(source_id, str) or not source_id or source_id == version['id']:
        fail(409, 'RESTART_SOURCE_UNAVAILABLE', '重审版本缺少原始分析绑定')
    source = entity(db, 'activity_versions', source_id)
    if source.get('activityId') != version.get('activityId') or source.get('submissionId') != version.get('submissionId'):
        fail(409, 'RESTART_SOURCE_UNAVAILABLE', '重审来源跨活动或跨提报')
    # The stored pointer always resolves directly to a version with the original
    # resolutions/history, avoiding mutable or cyclic chains.
    if (source.get('snapshot') or {}).get('reviewRestartBinding'):
        fail(409, 'RESTART_SOURCE_UNAVAILABLE', '重审来源链无效')
    return source


def pending_for_restart(version, view):
    snapshot = version.get('snapshot') or {}
    values = snapshot.get('resolvedFields') or snapshot.get('confirmedFields') or {}
    date_layer = snapshot.get('dateBackfill') or {}
    values = values | date_layer.get('dates', {})
    scope = snapshot.get('productApplicability') or {'kind': 'unknown'}
    pending = list(view.get('currentPendingItems', date_layer.get('pendingItems', snapshot.get('pendingItems', []))) or [])
    if not values.get('activityName'):
        pending.append({'field': 'activityName', 'reasonCode': 'NAME_REQUIRED'})
    for item in pending_items(values, scope, snapshot.get('unresolvedRows', [])):
        if item not in pending:
            pending.append(item)
    if not read_activity_method(snapshot):
        pending.append({'field': 'activityMethod', 'reasonCode': 'ACTIVITY_METHOD_UNCONFIRMED'})
    available = view.get('sourceAvailability') or {}
    if available.get('status') != 'available':
        pending.append({'field': 'sourceBinding', 'reasonCode': 'RESTART_SOURCE_UNAVAILABLE', 'sourceStatus': available.get('status', 'unavailable')})
    # Preserve each distinct reason instead of silently clearing an unresolved
    # identity/date/source problem just because a new version has been made.
    return list({json.dumps(item, sort_keys=True, ensure_ascii=False): item for item in pending}.values())


def restart(domain, business, db, actor, aid, vid, body, command_id):
    fields(body, ['expectedActivityRevision', 'expectedReviewRevision', 'reason'])
    text(body['reason'], 2000, False)
    if not body['reason'].strip() or any(type(body[key]) is not int or body[key] < 1 for key in ('expectedActivityRevision', 'expectedReviewRevision')):
        fail(422, 'INVALID_REQUEST', '须提供当前活动、审核版本及重审原因')
    activity, old, sub = authorize(domain, db, actor, aid, vid)
    review = db.execute('SELECT * FROM version_reviews WHERE version_id=? AND activity_id=?', (vid, aid)).fetchone()
    current = activity.get('reviewVersionId') or activity.get('currentReviewVersionId')
    if (not review or review['state'] != 'rejected' or current != vid or
            activity['revision'] != body['expectedActivityRevision'] or review['revision'] != body['expectedReviewRevision']):
        fail(409, 'STATE_CONFLICT', '活动或审核状态已变化，只能从当前已退回版本发起重审')
    view = business.version(db, actor, aid, vid)
    pending = pending_for_restart(old, view)
    original = projection_version(db, old)
    sources = list(db.execute('SELECT submission_id,input_revision,material_ids FROM version_sources WHERE version_id=?', (vid,)))
    if not sources and not any(item['field'] == 'sourceBinding' for item in pending):
        pending.append({'field': 'sourceBinding', 'reasonCode': 'RESTART_SOURCE_UNAVAILABLE'})
    people, blocked = ([], 'REVIEW_INPUT_INCOMPLETE') if pending else domain.review_assignees(db, submitter_id=sub['submitter_id'], activity_id=aid)
    state = 'not_started' if blocked else 'initial_pending'
    new_vid, at = new_id('VER'), iso(domain.clock())
    number = db.execute("SELECT MAX(json_extract(body,'$.versionNo')) FROM activity_versions WHERE json_extract(body,'$.activityId')=?", (aid,)).fetchone()[0]
    snapshot = copy.deepcopy(old.get('snapshot') or {})
    # Derived execution/store confirmations cannot become facts of a new version.
    for key in ('confirmedStoreScope', 'reviewBlockedReason', 'registrationReview'):
        snapshot.pop(key, None)
    snapshot['pendingItems'] = pending
    snapshot['reviewRestartBinding'] = {'contractVersion': CONTRACT, 'previousVersionId': vid,
        'sourceProjectionVersionId': original['id'], 'sourceReviewRevision': review['revision'],
        'reason': body['reason'].strip(), 'actorId': actor['id'], 'createdAt': at,
        'commandId': command_id, 'sourceBindings': [{'submissionId': row['submission_id'], 'inputRevision': row['input_revision'], 'materialIds': json.loads(row['material_ids'])} for row in sources]}
    if blocked:
        snapshot['reviewBlockedReason'] = blocked
    new_version = {**copy.deepcopy(old), 'id': new_vid, 'versionNo': (number or 0) + 1, 'revision': 1,
        'previousVersionId': vid, 'basedOnVersionId': vid, 'snapshot': snapshot, 'createdAt': at,
        'changeSummary': '退回后重审：' + body['reason'].strip()}
    put(db, 'activity_versions', new_version)
    for source in sources:
        domain.attach_source(db, new_vid, source['submission_id'], source['input_revision'], json.loads(source['material_ids']))
    db.execute('INSERT INTO version_reviews VALUES(?,?,1,?,?,?)', (new_vid, aid, state, 'manual' if not blocked else None, sub['id']))
    if not blocked:
        domain.assign_review_tasks(db, new_vid, people)
    activity.update(currentReviewVersionId=new_vid, reviewVersionId=new_vid, displayVersionId=new_vid,
        revision=activity['revision'] + 1, reviewState=state, reviewBlockedReason=blocked, pendingItems=pending,
        review={'stage': 'initial' if not blocked else None, 'result': 'pending' if not blocked else 'not_started', 'approvalMode': 'manual' if not blocked else None})
    put(db, 'activities', activity)
    event = history(db, actor, domain.clock(), activity=aid, version=new_vid, submission=sub['id'],
        event='review.restarted', before='rejected', after=state,
        details={'previousVersionId': vid, 'sourceProjectionVersionId': original['id'], 'reason': body['reason'].strip(),
                 'pendingItems': pending, 'reviewBlockedReason': blocked, 'sourceReviewRevision': review['revision'],
                 'assignments': [{'stage': stage, 'assigneeId': person} for stage, person in people]})
    return 201, {'contractVersion': CONTRACT, 'activityId': aid, 'versionId': new_vid, 'previousVersionId': vid,
        'activityRevision': activity['revision'], 'review': domain.review_view(db, new_vid),
        'reviewState': state, 'reviewBlockedReason': blocked, 'pendingItems': pending, 'historyEventId': event,
        'registrationSelectionState': 'empty', 'sourceProjectionVersionId': original['id']}


def project_restart(domain, db, actor, activity, version):
    """Read-only action projection for the version page; no recursive view call."""
    result = {'available': False, 'action': ACTION, 'activityId': activity['id'], 'versionId': version['id']}
    review = db.execute('SELECT * FROM version_reviews WHERE version_id=? AND activity_id=?', (version['id'], activity['id'])).fetchone()
    current = activity.get('reviewVersionId') or activity.get('currentReviewVersionId')
    if not review or review['state'] != 'rejected' or current != version['id']:
        return result | {'reasonCode': 'RESTART_REQUIRES_CURRENT_REJECTED_VERSION'}
    try:
        authorize(domain, db, actor, activity['id'], version['id'])
    except APIError as error:
        if error.status not in (403, 404):
            raise
        return result | {'reasonCode': error.code}
    return result | {'available': True, 'expectedActivityRevision': activity['revision'],
        'expectedReviewRevision': review['revision']}


def start_target(domain,db,actor,activity,version,review):
    """A new rejected-version successor may start after its remaining inputs are ready."""
    vid=version['id']
    if review['state']!='not_started' or (activity.get('reviewVersionId') or activity.get('currentReviewVersionId'))!=vid:
        return [],'STATE_CONFLICT'
    if db.execute('SELECT 1 FROM review_tasks WHERE version_id=?',(vid,)).fetchone():return [],'STATE_CONFLICT'
    try:authorize(domain,db,actor,activity['id'],vid)
    except APIError as error:
        if error.status not in (403,404):raise
        return [],'FORBIDDEN'
    snapshot=version.get('snapshot') or {}
    drafts=getattr(domain,'registration_drafts',None)
    if drafts and hasattr(drafts.business,'registration_version'):
        view=drafts.business.registration_version(db,actor,activity['id'],vid)
    else:
        from b_version_evidence import source_projection
        sources=[{'submissionId':r['submission_id'],'inputRevision':r['input_revision'],'materialIds':json.loads(r['material_ids'])}
                 for r in db.execute('SELECT * FROM version_sources WHERE version_id=?',(vid,))]
        view=source_projection(db,actor,projection_version(db,version),sources,domain.auth)
    pending=pending_for_restart(version,view)
    if pending and all(item['field']=='productApplicability' for item in pending):
        codes=snapshot.get('applicablePlatformCodes') or []
        if drafts and codes:
            try:
                states=[drafts.read(db,actor,activity['id'],vid,code) for code in codes]
                if all(not state['stale'] and any(r.get('decision')=='planned' and r.get('internalCode') for r in state['rows']) for state in states):pending=[]
            except APIError:pass
    if pending:return [],'REVIEW_INPUT_INCOMPLETE'
    sub=db.execute('SELECT submitter_id FROM b_submissions WHERE id=?',(version['submissionId'],)).fetchone()
    return domain.review_assignees(db,submitter_id=sub[0],activity_id=activity['id'])
