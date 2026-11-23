"""Require a submitter note for unresolved coupon funding, bound to the original tier."""
from account_primitives import fail
from b_coupon_details import funding, nonbrand_funding, source_key
from b_coupon_review import PLATFORMS, PLATFORM_LABELS, coupon_sources, threshold_coupon


def binding(option, schedule):
    source=option.get('source') or option.get('sourceReference') or {}
    schedule=schedule or {}
    mid=source.get('materialId') or schedule.get('materialId')
    sha=source.get('sourceSha256') or schedule.get('sourceSha256')
    return mid,sha,source_key(option,schedule)


def clear_funding(values):
    # A named two- or three-party subsidy identifies the funders even without amounts.
    return values['fundingMode']!='unknown' and values['storeFundingStatus']!='unknown'


def question(option, material_hashes, schedule=None):
    values=funding(option,schedule=schedule)
    if clear_funding(values):
        return None
    mid,sha,key=binding(option,schedule)
    verified = bool(mid and sha and key and material_hashes.get(mid)==sha)
    platform=option.get('platformCode') or PLATFORMS.get(option.get('channel'))
    return {'tierId':option['tierId'],'sourceTierKey':key if verified else None,'required':True,
            'name':option.get('name') if verified else '券档来源待核对','platformCode':platform,
            'platformLabel':PLATFORM_LABELS.get(platform) or option.get('channel'),
            'question':'请备注这档券的出资情况。','discountAmount':values['discountAmount'] if verified else None,
            'blockedReason':None if verified else '这档券还未关联可核实的原件，请先核对材料。'}


def questions_for_run(analysis, metadata, platforms, open_candidates, material_hashes, schedule):
    methods={item['candidateId']:item.get('activityMethod',{}).get('value') for item in metadata}
    groups={}
    for candidate in analysis['candidates']:
        cid=candidate['candidateId']
        rules=candidate['fields']['rulesText']
        if cid not in open_candidates or not threshold_coupon(methods.get(cid),rules['value']):
            continue
        bound=schedule if schedule and schedule.get('candidateId')==cid else None
        options=(bound['tiers'] if bound else coupon_sources(analysis,rules['evidenceIds'],platforms,
                                      mixed_only=False,product_rows=candidate['productRows']))
        rows=[item for option in options if (item:=question(option,material_hashes,bound))]
        if rows:
            groups[cid]=rows
    return groups


def apply_answers(answers, options, schedule, *, source_hashes, run_id, candidate_id, actor_id, now, revision,
                  brand_subsidy_only=False):
    offered={}
    for option in options:
        item=question(option,source_hashes,schedule)
        if item:
            offered[item['tierId']]=option,item
    def reject(code,message,affected):
        fail(422,code,message,{'questions':[item for _,item in offered.values()],
                              'affectedTierIds':[item['tierId'] for item in affected]})
    if not isinstance(answers,list) or len(answers)>len(offered):
        reject('INVALID_COUPON_FUNDING_ANSWER','请核对本次提报的出资备注。',[])
    blocked=[item for _,item in offered.values() if item['blockedReason']]
    if blocked:
        reject('COUPON_FUNDING_SOURCE_REQUIRED','券档出资待核对，原件位置无法验证，请先核对材料。',blocked)
    updates={}
    for reply in answers:
        if not isinstance(reply,dict) or set(reply) not in (
                {'tierId','sourceTierKey','note'}, {'tierId','sourceTierKey','answer'}):
            reject('INVALID_COUPON_FUNDING_ANSWER','请为本次券档填写出资备注。',[])
        tid=reply['tierId']
        if not isinstance(tid,str) or tid not in offered or tid in updates:
            reject('INVALID_COUPON_FUNDING_ANSWER','券档与本次识别不一致，请刷新后核对。',[])
        option,item=offered[tid]
        if item['sourceTierKey']!=reply['sourceTierKey']:
            reject('INVALID_COUPON_FUNDING_ANSWER','券档与原件不一致，请刷新后核对。',[item])
        mid,sha,_=binding(option,schedule)
        source=option.get('source') or option.get('sourceReference') or {}
        receipt={'sourceLocator':source.get('locator'),'confirmedBy':actor_id,'confirmedAt':now,'runId':run_id,'candidateId':candidate_id,
                 'inputRevision':revision,'tierId':tid,'platformCode':item['platformCode'],
                 'sourceTierKey':item['sourceTierKey'],'materialId':mid,'sourceSha256':sha,
                 'sourceChannel':option.get('channel'),'evidenceId':option.get('evidenceId') or option.get('dateEvidenceId')}
        note=reply.get('note')
        if not isinstance(note,str) or note.strip().casefold() in ('','unknown','not_brand_only','否') or len(note.strip())>1000:
            reject('COUPON_FUNDING_NOTE_REQUIRED','请填写这档券的出资备注（最多 1000 字）。',[item])
        updates[tid]={'fundingNote':receipt | {'text':note.strip()}}
    missing=[item for tid,(_,item) in offered.items() if tid not in updates]
    if missing:
        reject('COUPON_FUNDING_NOTE_REQUIRED','还有券档缺少出资备注，请逐档填写。',missing)
    if brand_subsidy_only:
        for option in options:
            values=funding(option | updates.get(option['tierId'],{}))
            if not clear_funding(values) or values['fundingMode']!='brand_only' or nonbrand_funding(option):
                fail(422,'BRAND_SUBSIDY_ONLY_CONFLICT','仍有券档未确认由品牌全额承担，请逐档审核。')
    for tid,changes in updates.items():
        offered[tid][0].update(changes)
