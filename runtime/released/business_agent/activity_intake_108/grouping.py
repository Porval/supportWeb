"""Local regrouping of unresolved, individually anchored coupon rows."""
from copy import deepcopy
import json

from api import analyze_intake, limits_for, require, sha
from coupon_table import TIER, platform_code, table_records, value

VERSION = 'coupon-grouping/1.0'
INVALID = 'INVALID_GROUPING_DECISION'
PRODUCT_COLUMNS = {'内部商品码','内部码','sku','upc','条码','商品名称','商品名'}


def token(prefix, *parts):
    payload = json.dumps(parts, ensure_ascii=False, sort_keys=True, separators=(',',':'))
    return prefix + sha(payload.encode())


def _cases(documents):
    pending, known_titles = {}, set()
    for doc in documents:
        if doc['status'] != 'parsed' or doc['textBlocks'] or doc['kind'] not in ('csv','xlsx'):
            continue
        rows = doc['tableRows']
        if any(value(cell).lower() in PRODUCT_COLUMNS for row in rows for cell in row['cells']):
            continue
        positions = [token('',row['locator']) for row in rows]
        if len(set(positions)) != len(positions):
            continue
        for row, fields, header, columns in table_records(rows):
            if not fields:
                continue
            title = value(fields.get('活动名称'))
            if not title or len(title)>200 or title.lower() in {'示例','示例行','example','sample','待确认','未知','无','-','/'}:
                continue
            platform = platform_code(fields.get('平台'))
            registration = next((c for c in row['cells'] if c['column']==columns['报名id']), None)
            if registration and (registration['sourceType']=='formula' or registration['text'].strip()):
                known_titles.add((platform,title))
                continue
            tier = fields.get('券档')
            if not platform or not TIER.fullmatch(value(tier)) or tier['sourceType'] != 'string':
                continue
            header_names = [value(cell).lower() for cell in header['cells'] if value(cell)]
            if '券档' not in header_names or len(set(header_names)) != len(header_names):
                continue
            location = row['locator']
            if any(tier['locator'].get(key)!=val for key,val in location.items()):
                continue
            mid,digest = doc['materialId'],doc['sha256']
            case_id = token('grp-',VERSION,mid,digest,header['locator'],platform,title)
            item = pending.setdefault(case_id,{'document':doc,'platform':platform,'title':title,'rows':{}})
            unit_id = token('unit-',case_id,location)
            unit = {'unitId':unit_id,'materialId':mid,'sourceSha256':digest,
                    'locator':tier['locator'],'label':title+' · '+value(tier)}
            item['rows'][unit_id] = (unit,row,fields)
    return {key:item for key,item in pending.items() if len(item['rows'])>1
            and (item['platform'],item['title']) not in known_titles and (None,item['title']) not in known_titles}


def enumerate_coupon_cases(documents):
    """Input is the trusted Worker-decoded snapshot for one run."""
    require(len({doc['materialId'] for doc in documents})==len(documents), INVALID)
    return deepcopy([{'caseId':case_id,'affectedCandidateIds':[],
                      'units':[unit for unit,_,_ in item['rows'].values()]}
                     for case_id,item in _cases(documents).items()])


def _partition_sets(partitions, expected, budget):
    require(isinstance(partitions,list) and 0<len(partitions)<=budget, INVALID)
    result, assigned = [], set()
    for part in partitions:
        require(isinstance(part,dict) and set(part)=={'unitIds'}, INVALID)
        ids=part['unitIds']
        require(isinstance(ids,list) and 0<len(ids)<=len(expected) and all(isinstance(key,str) for key in ids), INVALID)
        group=set(ids)
        require(len(group)==len(ids) and group<=expected and not group & assigned, INVALID)
        result.append(group)
        assigned.update(group)
    require(assigned==expected, INVALID)
    return result


def _copy_refs(value, evidence_ids):
    if isinstance(value,dict):
        return {key:([evidence_ids[eid] for eid in val] if key=='evidenceIds' else _copy_refs(val,evidence_ids))
                for key,val in value.items()}
    if isinstance(value,list):
        return [_copy_refs(val,evidence_ids) for val in value]
    return value


def _append_case(result, payload, case, partitions, limit, next_index):
    candidates=result['candidates']
    groups=_partition_sets(partitions,set(case['rows']),limit['maxCandidates']-len(candidates))
    order={key:index for index,key in enumerate(case['rows'])}
    for group in sorted(groups,key=lambda ids:min(order[key] for key in ids)):
        rows=[(row,fields) for key,(_,row,fields) in case['rows'].items() if key in group]
        source=case['document']
        doc={**source,'tableRows':[row for row,_ in rows],'errors':[],'warnings':[]}
        fragment=analyze_intake({**payload,'documents':[doc],'manualFields':{}},limit,
                                coupon_partition=(case['platform'],[fields for _,fields in rows]))
        require(len(fragment['candidates'])==1, INVALID)
        require(len(result['evidence'])+len(fragment['evidence'])<=limit['maxEvidence'],'LIMIT_EXCEEDED')
        evidence_ids={}
        for entry in fragment['evidence']:
            new_id='ev-'+str(len(result['evidence'])+1)
            evidence_ids[entry['evidenceId']]=new_id
            result['evidence'].append({**entry,'evidenceId':new_id})
        candidate=_copy_refs(fragment['candidates'][0],evidence_ids)
        candidate['candidateId']='candidate-'+str(next_index)
        candidates.append(candidate)
        next_index+=1
        result['conflicts'].extend(_copy_refs(fragment['conflicts'],evidence_ids))
        result['warnings'].extend(fragment['warnings'])
        for missing in fragment['missingFields']:
            if missing not in result['missingFields']:
                result['missingFields'].append(missing)
    return next_index


def regroup_coupon_case(payload, case_id, partitions, limits=None, *, prior_decisions=()):
    """Append assignments after trusted prior decisions in their original order."""
    limit=limits_for(limits)
    result=analyze_intake(payload, limit)
    documents=payload['documents']
    require(len({doc['materialId'] for doc in documents})==len(documents), INVALID)
    cases=_cases(documents)
    require(isinstance(prior_decisions,(list,tuple)) and len(prior_decisions)<limit['maxCandidates'], INVALID)
    decisions=(*prior_decisions,{'caseId':case_id,'partitions':partitions})
    next_index=1+max((int(c['candidateId'].removeprefix('candidate-')) for c in result['candidates']),default=0)
    for decision in decisions:
        require(isinstance(decision,dict) and set(decision)=={'caseId','partitions'} and isinstance(decision['caseId'],str), INVALID)
        case=cases.pop(decision['caseId'],None)
        require(case is not None, INVALID)
        next_index=_append_case(result,payload,case,decision['partitions'],limit,next_index)
    candidates=result['candidates']
    if all(c['fields']['activityName']['value'] for c in candidates):
        result['missingFields']=[item for item in result['missingFields'] if item['field']!='activityName']
    if any(c['fields']['activityName']['value'] for c in candidates):
        result['outcome']='needs_confirmation'
    return result
