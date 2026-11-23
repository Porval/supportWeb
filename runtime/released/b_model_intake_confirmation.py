"""Bind user confirmation to the stored model Agent result, never local inference."""
import json
from account_primitives import fail, iso
from b_domain import fields, sub_revision, verify_declaration_binding, can_material, entity, put, history
from b_model_intake import CONTRACT, ENGINE, source_for_hash, validate_metadata
from b_intake_grouping_validation import splits_single_child_export, ERROR_CODE, ERROR_MESSAGE
from b_submission_declarations import projected_declarations, recorded_form
from b_coupon_period_dates import candidate_coupon_periods, receipt_confirmation
from b_coupon_review import brand_coupon_method_correction, split_brand_coupon_groups, GROUPING_MESSAGE
from b_activity_method import PRODUCT_REGISTRATION_METHODS, THRESHOLD_SUBSIDY_METHODS
from b_state import canon


def confirmation_context(domain, db, actor, sub, run, payload, body):
    binding = body.get('agentRecognitionBinding')
    fields(binding, ['runId', 'inputRevision', 'analysisDigest', 'promptVersion'])
    if (binding['runId'] != run['id'] or type(binding['inputRevision']) is not int
            or binding['inputRevision'] != run['input_revision']
            or binding['analysisDigest'] != payload['analysisDigest']
            or binding['promptVersion'] != payload['result'].get('promptVersion')):
        fail(409, 'AGENT_RESULT_STALE', 'Agent 结果绑定已变化，请刷新后核对')
    value = payload.get('agentRecognition') or {}
    result = payload['result']
    consent = payload.get('_modelFirst') or {}
    if (run['state'] != 'succeeded' or value.get('contractVersion') != CONTRACT
            or value.get('status') != 'succeeded' or result.get('engine') != ENGINE
            or result.get('modelUsed') is not True or consent.get('consent') is not True):
        fail(409, 'AGENT_RESULT_REQUIRED', '请先取得本修订的完整模型 Agent 识别结果')
    metadata_keys = ('provider', 'model', 'thinking', 'promptVersion', 'coverage', 'candidateMetadata', 'inputScopeSha256')
    if 'providerAttempts' in value:metadata_keys=(*metadata_keys,'providerAttempts')
    try:
        source_hash = payload['engineSourceSha256']
        if source_hash != domain.jobs.model_agent.source_hash() and source_for_hash(source_hash) is None:
            raise ValueError('Unknown Agent source')
    except (ValueError, KeyError):
        fail(409, 'AGENT_RESULT_STALE', 'Agent 结果版本或标准结构已失效，请重新识别')
    source = sub_revision(db, sub['id'], sub['revision'])
    originals = verify_declaration_binding(db, sub, run, result, payload['analysisDigest'], source)
    if any(not can_material(db, actor, mid, domain.auth) for mid in originals):
        fail(403, 'FORBIDDEN', '当前账号无权读取本次识别材料')
    form_id = source['formMaterialId']
    form = {'materialId':form_id,'sha256':originals[form_id]['sha256'],'fields':recorded_form(source)}
    filenames={mid: original['filename'] for mid,original in originals.items() if mid!=form_id}
    try:
        from b_coupon_membership_validation import verify_saved_coupon_memberships
        validate_metadata({k: value[k] for k in metadata_keys}, result,
                          prompt_version=binding['promptVersion'],
                          verify_shared_sources=lambda data:
                              verify_saved_coupon_memberships(data,form,filenames,value['inputScopeSha256']))
    except (ValueError, KeyError, TypeError):
        fail(409, 'AGENT_RESULT_STALE', 'Agent 结果版本或标准结构已失效，请重新识别')
    if splits_single_child_export(result, form, filenames):
        fail(409, ERROR_CODE, ERROR_MESSAGE)
    candidate = next(c for c in result['candidates'] if c['candidateId'] == body['candidateId'])
    confirmed = body['confirmedFields']
    correction=None
    if body['intent'] in ('create','propose_change'):
        groups = split_brand_coupon_groups(result, value['candidateMetadata'], db)
        if candidate['candidateId'] in groups:
            fail(409, 'COUPON_GROUPING_REQUIRES_MERGE', GROUPING_MESSAGE,
                 {'candidateIds': groups[candidate['candidateId']]})
        metadata=next(item for item in value['candidateMetadata'] if item['candidateId']==candidate['candidateId'])
        platforms=projected_declarations(source,confirmed.get('platformCode'))['applicablePlatformCodes']
        correction=brand_coupon_method_correction(result,candidate,metadata,platforms)
        agent_method=metadata['activityMethod']
        if correction:
            proposed=body.get('activityMethod')
            if proposed!=correction['toMethod'] and (correction['toMethod'] or proposed not in THRESHOLD_SUBSIDY_METHODS):
                fail(422,'COUPON_ACTIVITY_METHOD_REQUIRED','原表爆好价列只标注参选，请按品牌满减活动核对建档。')
        elif (agent_method['status']=='explicit' and agent_method['value'] in PRODUCT_REGISTRATION_METHODS
                and body.get('activityMethod')!=agent_method['value']):
            fail(422,'PRICE_ACTIVITY_METHOD_REQUIRED','请按 Agent 识别的神价或爆好价单独建档。')
    for key in ('platformCode', 'sourceActivityId'):
        field = candidate['fields'][key]
        if field['status'] == 'explicit' and confirmed.get(key) != field['value']:
            fail(409, 'AGENT_IDENTITY_CHANGED', '平台或活动编号与 Agent 结果不一致，请补正材料后重新识别')
    platform = confirmed.get('platformCode')
    number = confirmed.get('sourceActivityId')
    period_confirmation=None
    if body['intent'] in ('create','propose_change'):
        periods=candidate_coupon_periods(result,form,originals[form_id]['received_at'])
        period_confirmation=receipt_confirmation(periods.get(candidate['candidateId']),confirmed)
    if body['intent'] == 'create' and platform and number:
        for row in db.execute('SELECT body FROM activities'):
            activity = json.loads(row[0])
            if any(x.get('platformCode') == platform and x.get('id') == number for x in activity.get('externalIds', [])):
                domain.auth.require(db, actor, 'operations.read', activity['id'])
                fail(409, 'DUPLICATE_ACTIVITY', '该平台活动编号已有档案，请关联已有活动',
                     {'activityId': activity['id'], 'revision': activity['revision']})
    return dict(binding, contractVersion=CONTRACT, provider=value['provider'], model=value['model'],
                thinking=value['thinking'], inputDigest=run['input_digest'],
                inputScopeSha256=value['inputScopeSha256'], coverage=value['coverage'],
                engineSourceSha256=payload['engineSourceSha256'], candidateId=body['candidateId'],
                consentBy=consent['consentBy'], consentedAt=consent['consentedAt'],
                **({'activityPeriod':period_confirmation} if period_confirmation else {}),
                **({'activityMethodCorrection':correction} if correction else {}),
                **({'providerAttempts':value['providerAttempts']} if 'providerAttempts' in value else {}))


def record_confirmation(domain, db, actor, sub, run, context, outcome):
    status, result = outcome
    proof = context | {'confirmedBy': actor['id'], 'confirmedAt': iso(domain.clock())}
    if result['outcome'] != 'linked':
        version = entity(db, 'activity_versions', result['versionId'])
        version['snapshot']['agentRecognitionBinding'] = proof
        put(db, 'activity_versions', version)
    key = (sub['id'], sub['revision'], context['candidateId'])
    row = db.execute('SELECT payload FROM resolutions WHERE submission_id=? AND input_revision=? AND candidate_id=?', key).fetchone()
    value = json.loads(row[0]) | {'agentRecognitionBinding': proof}
    db.execute('UPDATE resolutions SET payload=? WHERE submission_id=? AND input_revision=? AND candidate_id=?', (canon(value), *key))
    history(db, actor, domain.clock(), activity=result['activityId'], version=result['versionId'], submission=sub['id'],
            event='activity.agent_recognition_confirmed', details=proof)
    return status, result | {'agentRecognitionBinding': proof}
