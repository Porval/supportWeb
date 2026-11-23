"""Combine material groups only on a proven, non-ambiguous source identity."""
from collections import defaultdict
from coupon_dates import FIELDS as PERIOD_FIELDS
from coupon_table import spelling

PLATFORM_CODES = {'meituan', 'taobao_instant', 'jiatan'}


def identity(group, evidence, parsed_materials):
    values = group['values']
    platform = {value for value, _ in values.get('platformCode', [])}
    source_id = {value for value, _ in values.get('sourceActivityId', [])}
    if len(platform)!=1 or not platform <= PLATFORM_CODES or len(source_id)!=1:
        return None
    anchors = [evidence[eid] for key in ('platformCode','sourceActivityId') for _,eid in values[key]]
    materials = {anchor['materialId'] for anchor in anchors}
    if len(materials)!=1 or not materials <= parsed_materials:
        return None
    # Table identities must be proven in the same source row, not by carrying
    # one field forward from another worksheet or an unrelated row.
    by_key = {}
    for key in ('platformCode','sourceActivityId'):
        by_key[key]=[evidence[eid]['locator'] for _,eid in values[key]]
    def row_location(loc):
        return loc['kind'],loc.get('sheet'),loc.get('table'),loc.get('row')
    first,second=by_key['platformCode'],by_key['sourceActivityId']
    if not any(a['kind']==b['kind']=='text' or row_location(a)==row_location(b)
               for a in first for b in second):
        return None
    return next(iter(platform)),next(iter(source_id)),next(iter(materials))


def combine(groups):
    merged = {'values':defaultdict(list),'rows':[]}
    values=merged['values']
    period_scopes = {group['couponDateTiers'] for group in groups if 'couponDateTiers' in group}
    for group in groups:
        merged['rows'].extend(group['rows'])
        for key,observations in group['values'].items():
            if key in PERIOD_FIELDS and len(period_scopes)>1 and 'couponDates' in group:
                continue
            if key=='rulesText':
                variant=group.get('couponRuleCore')
                observations=[(variant.get(value,value),eid) for value,eid in observations] if variant else observations
            values[key].extend(observations)
    for group in groups:
        for key, entries in group.get('couponDates', {}).items():
            merged.setdefault('couponDates', defaultdict(list))[key].extend(entries)
        if group.get('couponDateOrderRefs'):
            merged.setdefault('couponDateOrderRefs', []).extend(group['couponDateOrderRefs'])
    original_names=[item for group in groups for item in group['values'].get('activityName',[])
                    if item[0]!=group.get('couponDraftName')]
    if original_names:
        values['activityName']=original_names
    elif len({value for value,_ in values.get('activityName',[])})>1:
        values.pop('activityName',None)

    lists=[group['couponBrandList'] for group in groups if 'couponBrandList' in group]
    if len(lists)>=2 and all('couponBrandList' in group or not group['values'].get('brandIds') for group in groups):
        reconcile_brands(values,merged,lists,groups)
    return merged


def reconcile_brands(values, merged, lists, groups):
    entries=[item['entries'] for item in lists]
    observed=[[eid for _,_,eid in items] for items in entries]
    known=[list(dict.fromkeys(brand for _,brand,_ in items if brand)) for items in entries]
    common=[brand for brand in known[0] if all(brand in other for other in known[1:])]
    values['brandIds']=[(common,eid) for items in entries for _,brand,eid in items if brand in common]
    others=list(dict.fromkeys(name for items in entries for name,brand,_ in items if brand not in common))
    all_names=list(dict.fromkeys(name for items in entries for name,_,_ in items))
    raw_sets=[{spelling(name) for name,_,_ in items} for items in entries]
    if any(other!=raw_sets[0] for other in raw_sets[1:]):
        refs=list(dict.fromkeys(eid for item,ids in zip(lists,observed) for eid in (*ids,*item['scopeEvidence'])))
        disputed=next(eid for items in entries for _,brand,eid in items if brand not in common)
        merged['brandDivergence']={'extractedValues':known,'evidenceIds':refs,
            'warningEvidenceId':disputed,'completeScope':all(item['scopeEvidence'] for item in lists)}
    # Only replace our generated coupon remarks. Real notes remain intact.
    generated={group['couponBrandRemark'] for group in groups if 'couponBrandRemark' in group}
    values['remark']=[item for item in values.get('remark',[]) if item[0] not in generated]
    if all_names and not values['remark']:
        remark='材料参与品牌：'+'、'.join(all_names)
        if others:
            remark+='。待核对品牌：'+'、'.join(others)
        values['remark']=[(remark[:5000],eid) for ids in observed for eid in ids]


def merge_exact_groups(groups, documents, evidence_rows):
    parsed={doc['materialId'] for doc in documents if doc['status']=='parsed' and not doc['errors']}
    evidence={item['evidenceId']:item for item in evidence_rows}
    buckets=defaultdict(list)
    for index,group in enumerate(groups):
        anchor=identity(group,evidence,parsed)
        if anchor:
            platform,source_id,material=anchor
            buckets[(platform,source_id)].append((index,material))
    replacements={}
    removed=set()
    for members in buckets.values():
        if len(members)<2 or len({material for _,material in members})!=len(members):
            continue
        indexes=[index for index,_ in members]
        replacements[indexes[0]]=combine([groups[index] for index in indexes])
        removed.update(indexes[1:])
    return [replacements.get(index,group) for index,group in enumerate(groups) if index not in removed]
