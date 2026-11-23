"""Deterministic day-level activity dates from a bound submission group."""
from datetime import date, datetime, timedelta, timezone
import hashlib
import re

from b_state import canon

VERSION = 'activity-source-dates/1.0'
LOCAL = timezone(timedelta(hours=8))
SIDES = ('startAt', 'endAt')
LABELS = {'startAt': {'开始时间', '活动开始时间', '活动开始日期', 'startAt'},
          'endAt': {'结束时间', '活动结束时间', '活动结束日期', 'endAt'}}
DAY = r'(?:(?P<year>\d{4})\s*[-/.年]\s*)?(?P<month>\d{1,2})\s*[-/.月]\s*(?P<day>\d{1,2})\s*(?:日|号)?'
DATE = re.compile(DAY)
RANGE = re.compile(rf'^\s*({DAY})\s*(?:[-－–—~～]|至|到)\s*({DAY.replace("P<","P<end_")})\s*$')
PERIOD_LABEL = re.compile(r'^\s*(?:活动时间|活动日期|活动周期|活动起止(?:日期|时间)?)\s*[:：]?\s*')
SIGNUP_LABEL = re.compile(r'^\s*报名时间\s*[:：]?\s*')
SOURCE_CONFLICT = 'ACTIVITY_DATE_CONFLICT'
YEAR_MISSING = 'ACTIVITY_DATE_YEAR_UNCONFIRMED'
SOURCE_MISSING = 'ACTIVITY_DATE_SOURCE_UNVERIFIED'
BAD_DATE = 'ACTIVITY_DATE_INVALID'


def from_parts(year, month, day):
    y, m, d = int(year) if year else None, int(month), int(day)
    # A leap year only validates that an undated month/day can exist.
    date(y if y is not None else 2000,m,d)
    return y,m,d


def parse_day(raw):
    if not isinstance(raw,str):return None
    raw=raw.strip()
    matched=DATE.fullmatch(raw)
    if matched:
        try:return from_parts(*matched.groups())
        except ValueError:return None
    try:
        value=datetime.fromisoformat(raw.replace('Z','+00:00'))
        if value.tzinfo:
            day=value.astimezone(LOCAL).date()
            return day.year,day.month,day.day
    except ValueError:
        pass
    return None


def period(raw):
    matched=RANGE.fullmatch(PERIOD_LABEL.sub('',raw,count=1))
    if not matched:
        parts=re.split(r'\s+(?:/|[-－–—~～])\s+|\s*(?:至|到)\s*',PERIOD_LABEL.sub('',raw,count=1))
        return [parse_day(part) for part in parts] if len(parts)==2 else None
    result=[]
    for prefix in ('','end_'):
        try:result.append(from_parts(*(matched[prefix+k] for k in ('year','month','day'))))
        except ValueError:result.append(None)
    return result


def moment(parts, field):
    return date(*parts).isoformat()+('T00:00:00+08:00' if field=='startAt' else 'T23:59:59.999+08:00')


def group_evidence(candidate,analysis):
    refs={eid for field in candidate['fields'].values() for eid in field['evidenceIds']}
    return [ev for ev in analysis['evidence'] if ev['evidenceId'] in refs]


def evidence_ref(ev,run_id,raw):
    return {'runId':run_id,'evidenceId':ev['evidenceId'],'materialId':ev['materialId'],
            'sourceSha256':ev['sourceSha256'],'locator':ev['locator'],'rawValue':raw}


def verify_locator(ev, docs, raw):
    doc=docs.get(ev['materialId'])
    return doc is not None and doc['sha256']==ev['sourceSha256'] and ev['quote']==raw[:500]


def collect(db, values, payload, candidate, run, source, originals, *, allowed=None, decode=None, verify_original=True):
    """Read only evidence in the chosen candidate and its original bytes."""
    from b_auto_resolution import original_value,field_source_value
    from coupon_table import platform_code,table_records,value
    payload=payload|{'materialResults':payload['result']['materialResults']}
    docs={}
    for doc in payload['materialResults']:
        mid=doc.get('materialId')
        original=originals.get(mid)
        if (not original or mid==source['formMaterialId'] or allowed is not None and not allowed(mid)
                or original['sha256']!=doc.get('sha256')
                or verify_original and hashlib.sha256(original['content']).hexdigest()!=original['sha256']):
            continue
        if decode:
            doc=decode({'materialId':mid,'filename':original['filename'],'mediaType':original['media_type'],
                        'sha256':original['sha256'],'contentBytes':original['content']})
        if doc.get('status')=='parsed':docs[mid]=doc
    basis=group_evidence(candidate,payload['result'])
    by_location={(e['materialId'],canon(e['locator'])):e for e in basis}
    observations={field:[] for field in SIDES}
    registration=[]
    pair=tuple(values.get(field) for field in ('platformCode','sourceActivityId'))
    candidate_pair=tuple(candidate['fields'][field]['value'] if candidate['fields'][field]['status']=='explicit' else None
                         for field in ('platformCode','sourceActivityId'))
    for field in SIDES:
        refs=set(candidate['fields'][field]['evidenceIds'])
        for ev in basis:
            mid=ev['materialId']
            if ev['evidenceId'] not in refs or mid not in docs:continue
            if docs[mid]['kind'] in ('csv','xlsx','docx'):
                cell=next((cell for row in docs[mid]['tableRows'] for cell in row['cells']
                           if cell['locator']==ev['locator']),None)
                if not cell or cell['sourceType']!='string' or not verify_locator(ev,docs,cell['text']):continue
            else:
                block=next((block for block in docs[mid]['textBlocks'] if block['locator']==ev['locator']),None)
                if not block or not verify_locator(ev,docs,block['text']):continue
            raw=original_value(db,source,payload,ev,originals)
            value_at=field_source_value(raw,ev,payload,field,LABELS[field])
            if value_at:
                observations[field].append((parse_day(value_at),evidence_ref(ev,run['id'],value_at)))
    if all(pair) and pair==candidate_pair:
        for mid,doc in docs.items():
            for row,fields,_,_ in table_records(doc['tableRows']):
                if not fields or (platform_code(fields.get('平台')),value(fields.get('报名id'))) != pair:
                    continue
                for column in ('活动时间','报名时间'):
                    cell=fields.get(column)
                    ev=by_location.get((mid,canon(cell['locator']))) if cell else None
                    if not ev or cell['sourceType']!='string' or not verify_locator(ev,docs,cell['text']):continue
                    raw=cell['text']
                    ref=evidence_ref(ev,run['id'],raw)
                    if column=='报名时间':
                        registration.append(ref|{'displayValue':SIGNUP_LABEL.sub('',raw,count=1)})
                    else:
                        ref['couponIdentity']={key:{'quote':value(fields[key]),'locator':fields[key]['locator']}
                                               for key in ('券档','报名id','平台') if key in fields}
                        parts=period(raw) or [None,None]
                        for field,part in zip(SIDES,parts):
                            observations[field].append((part,ref))
    return observations,registration


def resolve(values, observations, registration):
    """Return missing fields that can be filled and a source-only date projection."""
    updates={}
    fields={}
    for field in SIDES:
        rows=observations[field]
        refs=list({canon(ref):ref for _,ref in rows}.values())
        valid={part for part,_ in rows if part is not None}
        days={(part[1],part[2]) for part in valid}
        years={part[0] for part in valid if part[0] is not None}
        parts=next(iter(valid)) if len(valid)==1 else None
        issue=None
        if not rows:issue=SOURCE_MISSING
        elif len(days)>1 or len(years)>1:issue=SOURCE_CONFLICT
        elif any(part is None for part,_ in rows):issue=BAD_DATE
        elif not years:issue=YEAR_MISSING
        elif len(days)==1:
            m,d=next(iter(days))
            parts=(next(iter(years)),m,d)
        if parts and not issue and values.get(field):
            confirmed=parse_day(values[field])
            if confirmed and confirmed!=parts:issue=SOURCE_CONFLICT
        if parts and not issue and not values.get(field):updates[field]=moment(parts,field)
        if rows:
            fields[field]={'status':'confirmed' if not issue else 'needs_check','reasonCode':issue,
                           'year':parts[0] if parts else None,'month':parts[1] if parts else None,
                           'day':parts[2] if parts else None,'sources':refs}
    merged=values|updates
    if updates and all(merged.get(field) for field in SIDES):
        try:
            start,end=(datetime.fromisoformat(merged[field].replace('Z','+00:00')) for field in SIDES)
            invalid=start.tzinfo is None or end.tzinfo is None or start>end
        except (ValueError,TypeError,AttributeError):
            invalid=True
        if invalid:
            for field in tuple(updates):
                fields[field].update(status='needs_check',reasonCode=SOURCE_CONFLICT)
            updates={}
    return updates,{'contractVersion':VERSION,'fields':fields,
                   'registrationSources':list({canon(ref):ref for ref in registration}.values())}


def for_creation(values,observations,registration,candidate,analysis):
    updates,basis=resolve(values,observations,registration)
    if analysis['engineVersion']=='1.0.6':
        for field in tuple(updates):
            if candidate['fields'][field]['status']!='explicit':
                updates.pop(field)
                basis['fields'][field].update(status='needs_check',reasonCode=SOURCE_MISSING)
    return updates,basis


def overrides(values, projection):
    return {field:item['reasonCode'] for field,item in projection['fields'].items()
            if not values.get(field) and item['reasonCode']}


def has_facts(projection):
    return bool(projection['fields'] or projection['registrationSources'])


def archived(db, activity, *, can_read_version=None, can_read_material=None, decode=None,
             confirmed_year=None, verify_original=True):
    """Resolve date evidence for the current authoritative review version, without writing."""
    import json
    from account_primitives import APIError
    from b_domain import sub_revision, verify_declaration_binding
    from b_state import fingerprint
    vid=activity.get('reviewVersionId',activity.get('currentReviewVersionId'))
    if not vid or can_read_version is not None and not can_read_version(vid):
        return None
    row=db.execute('SELECT body FROM activity_versions WHERE id=?',(vid,)).fetchone()
    version=json.loads(row[0]) if row else {}
    if version.get('activityId')!=activity['id']:return None
    snap=version.get('snapshot') or {}
    fields=snap.get('resolvedFields') or snap.get('confirmedFields')
    if not fields or fields.get('platformCode')!=activity.get('platformCode'):
        return None
    pair=(fields.get('platformCode'),fields.get('sourceActivityId'))
    if pair[1] and not any((ref.get('platformCode'),ref.get('id'))==pair for ref in activity.get('externalIds',[])):
        return None
    binding=snap.get('resolutionBinding') or snap.get('manualConfirmation') or {}
    sid=version.get('submissionId')
    run_id=binding.get('runId')
    run=db.execute('SELECT * FROM analysis_runs WHERE id=? AND submission_id=?',(run_id,sid)).fetchone()
    if not run or run['state'] not in ('succeeded','partial'):
        return None
    rev=run['input_revision']
    if binding.get('inputRevision',rev)!=rev:
        return None
    attached=db.execute('SELECT material_ids FROM version_sources WHERE version_id=? AND submission_id=? AND input_revision=?',
                        (vid,sid,rev)).fetchone()
    if not attached:return None
    payload=json.loads(run['payload'])
    analysis=payload.get('result')
    candidate_id=binding.get('candidateId') or (binding.get('originalCandidate') or {}).get('candidateId')
    candidate=next((item for item in (analysis or {}).get('candidates',[]) if item['candidateId']==candidate_id),None)
    if not candidate:return None
    decision=db.execute('SELECT payload FROM resolutions WHERE submission_id=? AND input_revision=? AND candidate_id=? AND activity_id=? AND version_id=?',
                        (sid,rev,candidate_id,activity['id'],vid)).fetchone()
    recorded=json.loads(decision[0]) if decision else {}
    if (recorded.get('analysisRunId')!=run_id or recorded.get('analysisDigest')!=binding.get('analysisDigest')
            or recorded.get('outcome') not in ('created','change_saved')):
        return None
    try:
        source=sub_revision(db,sid,rev)
        if verify_original:
            originals=verify_declaration_binding(db,{'id':sid,'revision':rev},run,analysis,binding['analysisDigest'],source)
        else:
            digest=db.execute('SELECT input_digest FROM submission_revisions WHERE submission_id=? AND revision=?',
                              (sid,rev)).fetchone()
            if (not digest or digest[0]!=run['input_digest']
                    or 'sha256:'+fingerprint(analysis)!=binding['analysisDigest']):
                return None
            originals={mid:db.execute('SELECT sha256 FROM material_blobs WHERE id=?',(mid,)).fetchone()
                       for mid in source['allMaterialIds']}
    except (APIError,KeyError,TypeError,ValueError):
        return None
    material_ids=set(json.loads(attached[0]))
    allowed=lambda mid:mid in material_ids and (can_read_material is None or can_read_material(mid))
    values=fields|{field:activity.get(field) or fields.get(field) for field in SIDES}
    observations,registration=collect(db,values,payload,candidate,run,source,originals,allowed=allowed,
                                      decode=decode,verify_original=verify_original)
    if confirmed_year is not None:
        observations=confirm_year_for_59_27(activity,vid,sid,run_id,observations,confirmed_year)
    updates,projection=resolve(values,observations,registration)
    return {'activityId':activity['id'],'versionId':vid,'submissionId':sid,'inputRevision':rev,
            'runId':run_id,'analysisDigest':binding['analysisDigest'],'activityRevision':activity['revision'],
            'updates':updates,'dateEvidence':projection,'values':values,
            'completeSources':run['state']=='succeeded' and all(
                doc['status']=='parsed' for doc in analysis['materialResults'])
                and not any(w.get('code')=='LIMIT_EXCEEDED' for w in analysis['warnings'])
                and all(ev['materialId']==source['formMaterialId'] or allowed(ev['materialId'])
                        for ev in group_evidence(candidate,analysis))}


# Historical one-activity approvals are private deployment data.
from pathlib import Path
import json

_override_path = Path(__file__).resolve().parents[2] / 'private-data' / 'date-confirmation.json'
_override = json.loads(_override_path.read_text(encoding='utf-8')) if _override_path.is_file() else {}
CONFIRMED_CASE = _override.get('confirmation', {'activityId': None})
_CONFIRMED_PLATFORM_ID = _override.get('platformActivityId')


def confirm_year_for_59_27(activity,vid,sid,run_id,observations,confirmation):
    from account_primitives import fail
    case=CONFIRMED_CASE
    if (not _CONFIRMED_PLATFORM_ID or not case.get('activityId') or confirmation!=case or (activity['id'],vid,sid,run_id)!=tuple(
            case[key] for key in ('activityId','versionId','submissionId','runId'))
            or activity.get('platformCode')!='meituan'
            or not any(ref.get('platformCode')=='meituan' and ref.get('id')==_CONFIRMED_PLATFORM_ID
                       for ref in activity.get('externalIds',[]))):
        fail(409,'ACTIVITY_DATE_SOURCE_UNVERIFIED','用户确认仅适用于指定券档活动及同一来源')
    corrected={}
    for field,key in [('startAt','confirmedStartOn'),('endAt','confirmedEndOn')]:
        confirmed=date.fromisoformat(case[key])
        expected=(None,confirmed.month,confirmed.day)
        bound=[]
        for parts,ref in observations[field]:
            locator=ref['locator']
            anchors=ref.get('couponIdentity',{})
            expected_cells={'券档':('A2','59-27'),'报名id':('C2',_CONFIRMED_PLATFORM_ID),'平台':('F2','美团')}
            exact_anchors=all(key in anchors and (anchors[key]['locator'].get('cell'),anchors[key]['quote'])==cell
                              for key,cell in expected_cells.items())
            if (parts==expected and ref['sourceSha256']==case['materialSha256'] and exact_anchors
                    and ref['evidenceId']=='ev-19' and locator.get('sheet')=='Sheet1'
                    and locator.get('cell')=='E2' and ref['rawValue'].strip()=='10月30日-11月12日'):
                context={'kind':'user_confirmation','threadId':case['threadId'],'turnId':case['turnId'],
                         'messageId':case['messageId'],'confirmedDay':case[key],
                         'scope':'confirmed_coupon_activity_period'}
                bound.append(((confirmed.year,confirmed.month,confirmed.day),ref|{'yearBasis':context}))
            else:
                bound.append((parts,ref))
        if not any(ref.get('yearBasis') for _,ref in bound):
            fail(409,'ACTIVITY_DATE_SOURCE_UNVERIFIED','指定活动日期原件、引用或单元格发生变化')
        corrected[field]=bound
    return corrected


ACTION='activities.date_backfill'
REQUEST_FIELDS=('expectedActivityRevision','expectedVersionRevision','expectedReviewRevision','evidenceDigest')


def format_day(item):
    if not item or not item['month'] or not item['day']:return None
    md=f"{item['month']:02d}-{item['day']:02d}"
    return {'on':f"{item['year']:04d}-{md}" if item['year'] else None,'monthDay':md}


def public_evidence(basis, allowed, *, detail=False):
    if not basis:return {}
    fields={}
    refs=[]
    for key,item in basis['fields'].items():
        visible=[source for source in item['sources'] if allowed(source['materialId'])]
        if visible and len(visible)==len(item['sources']):
            fields[key]=item
            refs.extend(visible)

    def dto(data,sources):
        issues=[{'field':'start' if field=='startAt' else 'end','reasonCode':value['reasonCode']}
                for field,value in data.items() if value['reasonCode']]
        reasons={issue['reasonCode'] for issue in issues}
        status='conflict' if reasons-{YEAR_MISSING} else 'year_unconfirmed' if reasons else 'verified'
        result={'status':status,'start':format_day(data.get('startAt')),'end':format_day(data.get('endAt')),'issues':issues}
        if not detail:
            for key in ('start','end'):
                if result[key]:result[key]['on']=None
        if detail:
            result['evidenceRefs']=list({canon(ref):{'runId':ref['runId'],'materialId':ref['materialId'],
                'sourceSha256':ref['sourceSha256'],'locator':ref['locator'],'quote':ref['rawValue'],
                **({'yearBasis':ref['yearBasis']} if 'yearBasis' in ref else {})} for ref in sources}.values())
        return result

    output={'activityDateEvidence':dto(fields,refs)} if fields else {}
    if not detail:return output
    registrations=[ref for ref in basis['registrationSources'] if allowed(ref['materialId'])]
    if registrations:
        spans=[period(ref['displayValue']) or [parse_day(ref['displayValue']),None] for ref in registrations]
        values={}
        for pos,field in enumerate(SIDES):
            parts={span[pos] for span in spans if span[pos]}
            if not parts:continue
            part=next(iter(parts)) if len(parts)==1 else (None,None,None)
            values[field]={'year':part[0],'month':part[1],'day':part[2],
                           'reasonCode':SOURCE_CONFLICT if len(parts)>1 else YEAR_MISSING if part[0] is None else None}
        if values:output['registrationDateEvidence']=dto(values,registrations)
    return output


def require_actor(domain,db,actor,aid,vid):
    from account_primitives import fail
    from b_domain import can_version,entity
    version=entity(db,'activity_versions',vid)
    if version.get('activityId')!=aid or not can_version(db,actor,vid,domain.auth):
        fail(404,'NOT_FOUND','活动版本不存在或不可读取')
    domain.auth.require(db,actor,'operations.read')
    is_author=db.execute('SELECT 1 FROM b_submissions WHERE id=? AND submitter_id=? AND state<>?',
                         (version['submissionId'],actor['id'],'deleted')).fetchone()
    approved_operator=(aid==CONFIRMED_CASE['activityId']
                       and getattr(domain.auth,'has_full_access',lambda _:False)(actor))
    if not (is_author or approved_operator):
        fail(403,'FORBIDDEN','当前账号没有此活动的日期回填权限')
    domain.auth.require(db,actor,'activities.propose')
    return version


def plan(db,activity,version,*,can_read_material=None,decode=None):
    from b_state import fingerprint
    aid,vid=activity['id'],version['id']
    review=db.execute('SELECT * FROM version_reviews WHERE version_id=? AND activity_id=?',(vid,aid)).fetchone()
    protection=(not review or review['state']!='not_started' or version.get('versionNo')!=1
                or version.get('basedOnVersionId') or version.get('previousVersionId')
                or activity.get('reviewVersionId',activity.get('currentReviewVersionId'))!=vid
                or activity.get('displayVersionId',vid)!=vid or activity.get('approvedVersionId')
                or activity.get('effectiveExecutionVersionId') or activity.get('effectiveVersionId')
                or (activity.get('execution') or {}).get('state')=='live'
                or activity.get('pendingChangeVersionIds')
                or db.execute("SELECT 1 FROM activity_versions WHERE id<>? AND json_extract(body,'$.activityId')=?",(vid,aid)).fetchone()
                or db.execute('SELECT 1 FROM review_tasks WHERE version_id=?',(vid,)).fetchone()
                or db.execute('SELECT 1 FROM review_decisions WHERE version_id=?',(vid,)).fetchone())
    revisions={'expectedActivityRevision':activity['revision'],'expectedVersionRevision':version['revision'],
               'expectedReviewRevision':review['revision'] if review else None}
    if protection:
        return {'eligible':False,'evidenceDigest':None,**revisions,'blockedReasons':[{'code':'ACTIVITY_DATE_BACKFILL_PROTECTED'}]},None
    confirmation=CONFIRMED_CASE if aid==CONFIRMED_CASE['activityId'] else None
    from account_primitives import APIError
    try:
        source=archived(db,activity,can_read_material=can_read_material,decode=decode,confirmed_year=confirmation)
    except APIError as error:
        if error.code!=SOURCE_MISSING:raise
        source=None
    if source and not source['completeSources']:
        return {'eligible':False,'evidenceDigest':None,**revisions,'blockedReasons':[{'code':SOURCE_MISSING}]},source
    if not source or not source['updates']:
        codes={item['reasonCode'] for key,item in (source or {}).get('dateEvidence',{}).get('fields',{}).items()
               if not activity.get(key) and item['reasonCode']}
        if not codes:codes={SOURCE_MISSING if any(not activity.get(field) for field in SIDES) else 'ACTIVITY_DATE_ALREADY_COMPLETE'}
        return {'eligible':False,'evidenceDigest':None,**revisions,'blockedReasons':[{'code':code} for code in sorted(codes)]},source
    binding={key:source[key] for key in ('activityId','versionId','submissionId','inputRevision','runId','analysisDigest',
                                        'updates','dateEvidence')}
    digest='sha256:'+fingerprint({'ruleVersion':VERSION,'revisions':revisions,'binding':binding})
    return {'eligible':True,'evidenceDigest':digest,**revisions,'blockedReasons':[]},source


def duplicate_identity(domain,db,actor,aid,proposed):
    from b_auto_resolution import identity
    fingerprint=identity(proposed)
    duplicate=db.execute('SELECT activity_id FROM activity_identities WHERE fingerprint=?',(fingerprint,)).fetchone() if fingerprint else None
    conflict=(bool(duplicate and duplicate[0]!=aid) or domain.possible_duplicate(db,actor,proposed,skip_activity=aid)
              or domain.supplement_identity_exists(db,aid,proposed,include_current=False))
    return conflict,fingerprint if not duplicate else None


def submit(domain,db,actor,aid,vid,body):
    from account_primitives import fail,iso
    from b_domain import can_material,entity,fields,history,put
    fields(body,REQUEST_FIELDS)
    version=require_actor(domain,db,actor,aid,vid)
    activity=entity(db,'activities',aid)
    review=domain.review_view(db,vid)
    if any(type(body[key]) is not int for key in REQUEST_FIELDS[:3]) or (
            body['expectedActivityRevision'],body['expectedVersionRevision'],body['expectedReviewRevision'])!=(
                activity['revision'],version['revision'],review['revision'] if review else None):
        fail(409,'ACTIVITY_DATE_BACKFILL_STALE','活动、版本或审核修订已变化')
    projection,source=plan(db,activity,version,can_read_material=lambda mid:can_material(db,actor,mid,domain.auth),
                           decode=domain.activity_date_decoder)
    if not projection['eligible']:
        code=projection['blockedReasons'][0]['code']
        fail(409,code,'当前活动不能回填日期',{'blockedReasons':projection['blockedReasons']})
    if body['evidenceDigest']!=projection['evidenceDigest']:
        fail(409,'ACTIVITY_DATE_BACKFILL_STALE','活动日期原件或证据已变化')
    updates=source['updates']
    proposed=source['values']|updates
    conflict,fingerprint=duplicate_identity(domain,db,actor,aid,proposed)
    if conflict:fail(409,'DUPLICATE_ACTIVITY','日期补齐后存在其他活动的身份冲突')
    if fingerprint:
        db.execute('INSERT INTO activity_identities VALUES(?,?)',(fingerprint,aid))
    snap=version['snapshot']
    previous=snap.get('dateBackfill')
    pending=[item for item in (previous['pendingItems'] if previous else snap.get('pendingItems',[]))
             if item['field'] not in updates]
    before={field:activity.get(field) for field in updates}
    changes=(previous.get('dates',{}) if previous else {})|updates
    evidence={'ruleVersion':VERSION,'dates':changes,'before':before,'after':updates,'pendingItems':pending,
              'basis':source['dateEvidence'],'evidenceDigest':projection['evidenceDigest'],
              'source':{key:source[key] for key in ('submissionId','inputRevision','runId','analysisDigest')},
              'actorId':actor['id'],'recordedAt':iso(domain.clock())}
    snap['dateBackfill']=evidence
    version['revision']+=1
    activity.update(updates,pendingItems=pending,activityDateBasis=source['dateEvidence'],revision=activity['revision']+1)
    put(db,'activity_versions',version)
    put(db,'activities',activity)
    db.execute('UPDATE version_reviews SET revision=revision+1 WHERE version_id=?',(vid,))
    event=history(db,actor,domain.clock(),activity=aid,version=vid,submission=source['submissionId'],
                  event='activity.dates_backfilled',details=evidence,source='automatic_rule')
    return 200,{'activityId':aid,'versionId':vid,'activityRevision':activity['revision'],
                'versionRevision':version['revision'],'reviewRevision':review['revision']+1,
                'startAt':activity.get('startAt'),'endAt':activity.get('endAt'),'pendingItems':pending,
                'historyEventId':event}
