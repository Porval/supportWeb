"""Version-scoped immutable input/evidence projection; never returns whole runs."""
import hashlib
import json
from account_primitives import iso
from b_state import canon,fingerprint
from b_submission_declarations import input_fingerprint,input_formula_version,projected_declarations,recorded_form
from b_domain import can_material,material_view


def source_projection(db,actor,version,sources,auth):
    """Caller has already authorized this exact version via can_version."""
    inputs=[];evidence_map={};missing=[]
    manual=version.get('snapshot',{}).get('manualConfirmation',{})
    automatic=version.get('snapshot',{}).get('resolutionBinding')
    history=[json.loads(r[0]) for r in db.execute('SELECT payload FROM b_history WHERE version_id=? ORDER BY rowid',(version['id'],))]
    def issue(target,code,**detail):
        item={'code':code,**detail}
        if item not in target:target.append(item)
    for source in sources:
        sid=source['submissionId'];revision=source['inputRevision'];bound=set(source['materialIds']);problems=[]
        item={'submissionId':sid,'inputRevision':revision,'status':'available','missing':problems,'inputDigest':None,'submittedAt':None,'submitter':None,'form':None,**projected_declarations({}),'rawText':None,'formMaterialId':None,'rawTextMaterialId':None,'attachmentMaterialIds':[],'materials':[],'analyses':[]}
        inputs.append(item)
        row=db.execute('SELECT * FROM submission_revisions WHERE submission_id=? AND revision=?',(sid,revision)).fetchone()
        submission=db.execute('SELECT submitter_id,received_at FROM b_submissions WHERE id=?',(sid,)).fetchone()
        if submission:
            snapshot=None
            for stored in db.execute('SELECT payload FROM b_history WHERE submission_id=? ORDER BY rowid',(sid,)):
                event=json.loads(stored[0])
                if event.get('event')=='submission.received' and event.get('actorId')==submission['submitter_id']:snapshot=event;break
            item['submittedAt']=iso(submission['received_at'])
            item['submitter']={'id':submission['submitter_id'],'nameSnapshot':snapshot.get('nameSnapshot') if snapshot else None,'roleSnapshot':snapshot.get('roleSnapshot') if snapshot else None,'snapshotStatus':'recorded' if snapshot else 'unavailable'}
            if not snapshot:issue(problems,'SUBMITTER_SNAPSHOT_NOT_RECORDED')
        else:issue(problems,'SUBMISSION_METADATA_UNAVAILABLE')
        if not row:
            item['status']='unavailable';issue(problems,'INPUT_REVISION_UNAVAILABLE');continue
        raw=json.loads(row['payload']);item['inputDigest']=row['input_digest']
        item.update(formMaterialId=raw.get('formMaterialId'),rawTextMaterialId=raw.get('rawTextMaterialId'),attachmentMaterialIds=raw.get('materialIds',[]))
        readable={};valid={}
        for mid in source['materialIds']:
            original=db.execute('SELECT * FROM material_blobs WHERE id=?',(mid,)).fetchone()
            if original is None:
                item['materials'].append({'materialId':mid,'available':False,'reasonCode':'MATERIAL_UNAVAILABLE'});issue(problems,'MATERIAL_UNAVAILABLE',materialId=mid);continue
            if not can_material(db,actor,mid,auth):
                item['materials'].append({'materialId':mid,'available':False,'reasonCode':'MATERIAL_READ_FORBIDDEN'});issue(problems,'MATERIAL_READ_FORBIDDEN',materialId=mid);continue
            if hashlib.sha256(original['content']).hexdigest()!=original['sha256']:
                item['materials'].append({'materialId':mid,'available':False,'reasonCode':'MATERIAL_HASH_MISMATCH'});issue(problems,'MATERIAL_HASH_MISMATCH',materialId=mid);continue
            readable[mid]=original;valid[mid]=original['sha256']
            metadata=material_view(original);metadata.update(available=True,downloadAvailable=can_material(db,actor,mid,auth,True));item['materials'].append(metadata)
        # Expose only the form/text from this bound revision and matching original bytes.
        for key,mid,expected in [('form',raw.get('formMaterialId'),canon(recorded_form(raw)).encode()),('rawText',raw.get('rawTextMaterialId'),raw.get('rawText','').encode())]:
            if mid is None:
                if key=='rawText' and raw.get('rawText','')=='':item[key]=''
                else:issue(problems,'INPUT_MATERIAL_NOT_RECORDED',field=key)
            elif mid not in bound:issue(problems,'INPUT_MATERIAL_NOT_BOUND',field=key,materialId=mid)
            elif mid in readable:
                if readable[mid]['content']==expected:item[key]=raw.get(key)
                else:issue(problems,'INPUT_MATERIAL_MISMATCH',field=key,materialId=mid)
        try:
            formula=input_formula_version(raw)
            if formula in ('input/1.2','input/1.3'):
                mids=raw['allMaterialIds']
                if not set(mids)<=bound:raise ValueError('input source unavailable')
                originals={mid:db.execute('SELECT sha256,filename,media_type FROM material_blobs WHERE id=?',(mid,)).fetchone()
                           for mid in mids}
                if any(row_ is None for row_ in originals.values()) or input_fingerprint(revision,raw,originals)!=row['input_digest']:
                    raise ValueError('input digest mismatch')
            item.update(projected_declarations(raw,raw.get('form',{}).get('platformCode')))
        except (KeyError,TypeError,ValueError):
            issue(problems,'INPUT_DIGEST_MISMATCH',field='submissionDeclarations')
        bindings=[]
        if sid==version.get('submissionId') and automatic:
            bindings.append(automatic|{'confirmationMaterialId':None})
        elif sid==version.get('submissionId') and manual.get('runId'):
            bindings.append({'runId':manual['runId'],'analysisDigest':manual.get('analysisDigest'),'candidateId':manual.get('originalCandidate',{}).get('candidateId'),'confirmationMaterialId':manual.get('confirmationMaterialId'),'originalCandidate':manual.get('originalCandidate')})
        primary=version.get('snapshot',{}).get('primaryRecognitionBinding') or {}
        if sid==version.get('submissionId') and primary.get('runId')==manual.get('runId'):
            for original_candidate in primary.get('originalCandidates',[]):
                bindings.append({'runId':primary['runId'],'analysisDigest':primary['analysisDigest'],
                    'candidateId':original_candidate['candidateId'],'originalCandidate':original_candidate,
                    'confirmationMaterialId':manual.get('confirmationMaterialId')})
        for event in history:
            details=event.get('details',{})
            if event.get('submissionId')==sid and event.get('event')=='activity.source_linked' and details.get('inputRevision')==revision and details.get('analysisRunId'):
                if details.get('resolutionBinding'):
                    bindings.append(details['resolutionBinding']|{'confirmationMaterialId':None});continue
                bindings.append({'runId':details['analysisRunId'],'analysisDigest':details.get('analysisDigest'),'candidateId':details.get('candidateId'),'confirmationMaterialId':details.get('confirmationMaterialId')})
        if not bindings:issue(problems,'ANALYSIS_BINDING_NOT_RECORDED')
        seen=set()
        for binding in bindings:
            key=(binding['runId'],binding['candidateId'])
            if key in seen:continue
            seen.add(key)
            run=db.execute('SELECT * FROM analysis_runs WHERE id=?',(binding['runId'],)).fetchone()
            if not run or run['submission_id']!=sid or run['input_revision']!=revision or run['input_digest']!=row['input_digest']:
                issue(problems,'ANALYSIS_BINDING_UNAVAILABLE',runId=binding['runId']);continue
            payload=json.loads(run['payload']);analysis=payload.get('result')
            if not analysis or payload.get('analysisDigest')!=binding['analysisDigest'] or 'sha256:'+fingerprint(analysis)!=binding['analysisDigest']:
                issue(problems,'ANALYSIS_DIGEST_MISMATCH',runId=run['id']);continue
            candidate=next((c for c in analysis.get('candidates',[]) if c['candidateId']==binding['candidateId']),None)
            committed=db.execute('SELECT 1 FROM resolutions WHERE submission_id=? AND input_revision=? AND candidate_id=? AND version_id=?',(sid,revision,binding['candidateId'],version['id'])).fetchone()
            if not candidate or not committed or binding.get('originalCandidate') is not None and canon(candidate)!=canon(binding['originalCandidate']):
                issue(problems,'CANDIDATE_BINDING_UNAVAILABLE',runId=run['id']);continue
            automatic_mode=binding.get('resolutionMode')=='automatic'
            confirmation_id=binding.get('confirmationMaterialId');confirmed=None
            resolution_data=None
            if automatic_mode:
                resolution_id=binding.get('resolutionMaterialId');original=readable.get(resolution_id)
                if original is not None and original['parse_support']=='controlled_resolution':
                    try:
                        stored=json.loads(original['content']);expected={k:v for k,v in binding.items() if k not in ['resolutionMaterialId','resolutionSha256','confirmationMaterialId']}
                        if canon(stored)==canon(expected):resolution_data=stored
                    except (ValueError,UnicodeError):pass
                if resolution_data is None:issue(problems,'RESOLUTION_UNAVAILABLE',materialId=resolution_id)
            if confirmation_id in readable:
                original=readable[confirmation_id]
                if original['parse_support']=='controlled_confirmation':
                    try:confirmed=json.loads(original['content'])
                    except (ValueError,UnicodeError):issue(problems,'CONFIRMATION_UNAVAILABLE',materialId=confirmation_id)
            if not automatic_mode and confirmed is None:issue(problems,'CONFIRMATION_UNAVAILABLE',materialId=confirmation_id)
            selected=set()
            for field in candidate['fields'].values():selected.update(field['evidenceIds'])
            for product in candidate['productRows']:selected.update(product['evidenceIds'])
            if confirmed:selected.update(confirmed.get('companyBearing',{}).get('evidenceIds',[]))
            if resolution_data:selected.update(r['evidenceId'] for r in resolution_data.get('basisEvidenceRefs',[]) if r['runId']==run['id'])
            reference_ids=[]
            for ev in analysis['evidence']:
                if ev['evidenceId'] not in selected:continue
                mid=ev['materialId'];reference=run['id']+':'+ev['evidenceId']
                if mid not in bound:issue(problems,'EVIDENCE_MATERIAL_NOT_BOUND',referenceId=reference);continue
                if mid not in readable:issue(problems,'EVIDENCE_SOURCE_UNAVAILABLE',referenceId=reference,materialId=mid);continue
                if ev['sourceSha256']!=valid[mid]:issue(problems,'EVIDENCE_SHA_MISMATCH',referenceId=reference);continue
                matches=False
                if mid==raw.get('formMaterialId'):
                    locator=ev['locator'];path=locator.get('fieldPath','');form=recorded_form(raw)
                    if locator.get('kind')=='form':
                        value=next((r for r in (form.get('manualProductRows') or []) if r['rowId']==path[len('manualProductRows.'):]),None) if path.startswith('manualProductRows.') else form.get(path)
                        quote=value if isinstance(value,str) else canon(value)
                        matches=value is not None and (isinstance(ev['quote'],str) and bool(ev['quote']) and ev['quote'] in quote if analysis.get('engine')=='deepseek_model_first_v1' else ev['quote']==quote[:500]) and readable[mid]['content']==canon(form).encode()
                else:
                    doc=next((d for d in payload.get('materialResults',[]) if d.get('materialId')==mid and d.get('sha256')==valid[mid]),None)
                    locations=doc.get('textBlocks',[])+[cell for row_ in doc.get('tableRows',[]) for cell in row_['cells']] if doc else []
                    matches=any(loc['locator']==ev['locator'] and (isinstance(ev['quote'],str) and bool(ev['quote']) and ev['quote'] in loc['text'] if analysis.get('engine')=='deepseek_model_first_v1' else loc['text'][:500]==ev['quote']) for loc in locations)
                if not matches:issue(problems,'EVIDENCE_LOCATOR_MISMATCH',referenceId=reference);continue
                reference_ids.append(reference)
                if reference not in evidence_map:evidence_map[reference]=ev|{'referenceId':reference,'runId':run['id'],'submissionId':sid,'inputRevision':revision,'candidateIds':[]}
                if candidate['candidateId'] not in evidence_map[reference]['candidateIds']:evidence_map[reference]['candidateIds'].append(candidate['candidateId'])
            changes=[]
            if confirmed:
                for field,value in confirmed.items():
                    before=candidate.get('fields',{}).get(field)
                    changes.append({'field':field,'extractedPresent':before is not None,'extractedValue':before.get('value') if before else None,'confirmedValue':value,'changed':before is None or canon(before['value'])!=canon(value)})
            item['analyses'].append({'runId':run['id'],'inputRevision':run['input_revision'],'inputDigest':run['input_digest'],'catalogRevision':run['catalog_revision'],'analysisDigest':binding['analysisDigest'],'state':run['state'],'createdAt':iso(run['created_at']),'engine':analysis.get('engine'),'engineVersion':analysis.get('engineVersion'),'modelUsed':analysis.get('modelUsed'),'candidateId':candidate['candidateId'],'candidate':candidate,'evidenceReferenceIds':reference_ids,'confirmation':None if automatic_mode else {'materialId':confirmation_id,'confirmedFields':confirmed,'changes':changes},'resolution':{'mode':'automatic' if automatic_mode else 'manual','materialId':binding.get('resolutionMaterialId') if automatic_mode else confirmation_id,'resolvedFields':resolution_data.get('resolvedFields') if resolution_data else confirmed,'ruleVersion':binding.get('ruleVersion') if automatic_mode else None,'basisEvidenceRefs':resolution_data.get('basisEvidenceRefs',[]) if resolution_data else []},'missingFields':[{'field':f,'status':'missing'} for f,v in candidate['fields'].items() if v['status']=='missing']})
        if problems:item['status']='partial'
    if not sources:issue(missing,'VERSION_SOURCES_NOT_RECORDED')
    for item in inputs:
        for problem in item['missing']:issue(missing,problem['code'],submissionId=item['submissionId'],inputRevision=item['inputRevision'],**{k:v for k,v in problem.items() if k!='code'})
    status='unavailable' if not sources else 'partial' if missing else 'available'
    return {'evidence':list(evidence_map.values()),'sourceInputs':inputs,'sourceAvailability':{'status':status,'missing':missing}}
