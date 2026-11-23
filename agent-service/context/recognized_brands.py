"""Resolve evidence-backed group brand names against the catalog on the intake request."""
from collections import defaultdict
import unicodedata


def normalized(name):
    return unicodedata.normalize('NFKC', name).strip().casefold()


def bind_catalog_brands(reply, payload):
    if not reply.get('ok'):
        return reply
    catalog = payload.get('catalogSnapshot') or {}
    brands = catalog.get('brands') or []
    if not brands or (payload.get('manualFields') or {}).get('fields', {}).get('brandIds'):
        return reply
    result = reply['result']
    if result.get('catalogRevision') != catalog.get('revision'):
        return reply
    by_name = defaultdict(set)
    for brand in brands:
        if isinstance(brand, dict) and isinstance(brand.get('name'), str) and isinstance(brand.get('id'), str) and brand['id']:
            by_name[normalized(brand['name'])].add(brand['id'])
    metadata = {item['candidateId']: item['brandNames'] for item in reply['metadata']['candidateMetadata']}
    evidence = {e['evidenceId']: normalized(e['quote']) for e in result['evidence']}
    changed = False
    for group in result['candidates']:
        current = group['fields']['brandIds']
        names = metadata.get(group['candidateId'], {})
        if current['status'] != 'missing' or names.get('status') != 'explicit':
            continue
        labels = names.get('value')
        refs = names.get('evidenceIds') or []
        if (not isinstance(labels, list) or not labels or not all(isinstance(v, str) for v in labels)
                or not refs or any(eid not in evidence for eid in refs)):
            continue
        labels = [normalized(label) for label in labels]
        if not all(label and len(by_name[label]) == 1 and
                                any(label in evidence[eid] for eid in refs) for label in labels):
            continue
        ids = list(dict.fromkeys(next(iter(by_name[label])) for label in labels))
        if len(ids) <= 20:
            group['fields']['brandIds'] = {'value':ids, 'status':'explicit', 'evidenceIds':list(refs)}
            changed = True
    if changed and all(g['fields']['brandIds']['status'] != 'missing' for g in result['candidates']):
        result['missingFields'] = [item for item in result['missingFields'] if item['field'] != 'brandIds']
    return reply
