"""Validate activity platform identities and reconcile them with the submitted selection."""
import json
import re

PLATFORMS = {'meituan', 'taobao_instant', 'jd'}
MENTIONS = {
    'meituan': re.compile(r'美团|美垂(?:仓)?|(?<![a-z0-9_])meituan(?![a-z0-9_])', re.I),
    'taobao_instant': re.compile(r'淘宝闪购|淘闪|(?<![a-z0-9_])taobao_instant(?![a-z0-9_])', re.I),
    'jd': re.compile(r'京东|(?<![a-z0-9_])jd(?![a-z0-9_])', re.I),
}
NEGATED = re.compile(r'(?:非|不是|不含|不属于|排除|不适用|不参加)\s*$')


def supports_platform(value, refs):
    if value not in PLATFORMS:
        return False
    mentioned = set()
    for ref in refs:
        source = ref['locator'].get('fieldPath')
        quote = ref['quote']
        if source == 'applicablePlatformCodes':
            try:
                options = json.loads(quote)
            except ValueError:
                options = None
            if isinstance(options, list) and len(options) > 1:
                continue
        if source in ('type', 'sourceActivityId', 'brandIds', 'activityMethod'):
            continue
        for code, pattern in MENTIONS.items():
            if any(not NEGATED.search(quote[:m.start()]) for m in pattern.finditer(quote)):
                mentioned.add(code)
    return mentioned == {value}


def reconcile_platforms(result, manual):
    """A selection can fill a blank; competing explicit sources remain reviewable."""
    form = (manual or {}).get('fields', {})
    single = form.get('platformCode')
    declared = form.get('applicablePlatformCodes') or []
    choices = set(declared) if isinstance(declared, list) and all(isinstance(c, str) for c in declared) else set()
    options = choices & PLATFORMS
    valid_single = isinstance(single, str) and single in PLATFORMS
    ambiguous_selection = bool(choices - PLATFORMS) or bool(single and not valid_single)
    pick = single if valid_single else next(iter(options)) if len(options) == 1 and not ambiguous_selection else None
    mismatch = bool(valid_single and options and single not in options)
    form_refs = []

    if result['candidates'] and (valid_single or options):
        for key, enabled in [('platformCode', valid_single), ('applicablePlatformCodes', bool(options))]:
            if not enabled:
                continue
            quote = single if key == 'platformCode' else json.dumps(declared, ensure_ascii=False, separators=(',', ':'))
            ref = {'materialId':manual['materialId'], 'sourceSha256':manual['sha256'],
                   'locator':{'kind':'form','fieldPath':key}, 'quote':quote}
            eid = next((e['evidenceId'] for e in result['evidence'] if all(e[k] == v for k,v in ref.items())), None)
            if eid is None:
                eid = 'selected-' + key
                used = {e['evidenceId'] for e in result['evidence']}
                while eid in used:
                    eid += '-1'
                result['evidence'].append(dict(ref, evidenceId=eid))
            form_refs.append(eid)

    for c in result['candidates']:
        old = c['fields']['platformCode']
        code = old['value']
        if old['status'] == 'conflict':
            if code not in PLATFORMS:
                old['value'] = None
            continue
        observed = code if code in PLATFORMS else None
        if mismatch or (observed and ((pick and pick != observed) or (options and observed not in options))):
            refs = list(dict.fromkeys(old['evidenceIds'] + form_refs))
            c['fields']['platformCode'] = {'status':'conflict','value':None,'evidenceIds':refs}
            result['conflicts'].append({'field':'platformCode','manualValue':single if valid_single else declared,
                                       'extractedValues':[observed] if observed else sorted(options),'evidenceIds':refs})
        elif not observed:
            c['fields']['platformCode'] = ({'status':'explicit','value':pick,'evidenceIds':form_refs.copy()}
                                           if pick else {'status':'missing','value':None,'evidenceIds':[]})
    if ambiguous_selection and result['candidates']:
        result['warnings'].append({'code':'ACTIVITY_PLATFORM_REVIEW',
                                   'message':'提报中的平台选项需要核对，现支持美团、淘宝闪购和京东。'})
    missing = any(c['fields']['platformCode']['status']=='missing' for c in result['candidates'])
    result['missingFields'] = [f for f in result['missingFields'] if f['field'] != 'platformCode']
    if missing:
        result['missingFields'].append({'field':'platformCode','reasonCode':'NOT_PROVIDED'})
