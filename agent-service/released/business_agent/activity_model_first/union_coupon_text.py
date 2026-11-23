"""Read directly prefixed Meituan joint-coupon IDs and their common brands."""

from decimal import Decimal
import re
from unicodedata import normalize

from .coupon_workbook import _field
from .structured_evidence import StructuredProjection

LINE = re.compile(r'(?P<id>\d{8,11})\s+(?P<name>【品商共补】\s*(?:美垂仓|美团)[^【】]*?品牌联合券\s*(?P<full>\d+(?:\.\d+)?)\s*[-－]\s*(?P<off>\d+(?:\.\d+)?)\s*[（(]品补\s*(?P<brand>\d+(?:\.\d+)?)\s*[)）])')
BRANDS = re.compile(r'^(?:关联|参与|活动)?品牌(?:名单|列表)?\s*[:：]\s*')


def describe(value):
    return format(value.normalize(),'f')


def reconcile_union_text(payload, result, metadata, lookup):
    documents = payload['documents']
    if len(documents) != 1 or documents[0]['kind'] not in ('txt','md'):
        return result, metadata
    doc, = documents
    form = (payload['manualFields'] or {}).get('fields', {})
    if form.get('type') not in (None,'platform') or form.get('platformCode') not in (None,'meituan'):
        return result,metadata
    if form.get('rulesText') or form.get('manualProductRows') or form.get('sourceActivityId'):
        return result,metadata

    tiers, brand_lines = {}, []
    for block in doc['textBlocks']:
        line = block['text'].strip().removesuffix('\\').strip()
        if not line:
            continue
        match = LINE.fullmatch(line)
        if match and not brand_lines and match['id'] not in tiers:
            tiers[match['id']] = match,block
        elif tiers and (BRANDS.match(line) or brand_lines or not re.search(r'\d|【|】',line)):
            brand_lines.append(block)
        else:
            return result, metadata
    names = list(dict.fromkeys(name.strip() for block in brand_lines
                  for name in re.split(r'[、,，;；\\]+',BRANDS.sub('',block['text'].strip())) if name.strip()))
    if (not 2<=len(tiers)<=20 or not 1<=len(names)<=24
            or any(not re.fullmatch(r'[^\d:：，、;；【】\s]{2,30}',n) for n in names)):
        return result, metadata
    projection = StructuredProjection(payload,result,metadata,lookup)
    brand_refs = [projection.cite(doc,line['locator'],line['text']) for line in brand_lines]
    for identifier,(match,block) in tiers.items():
        threshold,discount,funding = (Decimal(match[k]) for k in ('full','off','brand'))
        if not 0 < funding <= discount <= threshold:
            return result,metadata
        ref = projection.cite(doc,block['locator'],block['text'])
        summary = (f'满{describe(threshold)}减{describe(discount)}元，原文品补{describe(funding)}元。'
                   '门店或公司是否承担及承担金额待提报人确认。')
        fields = {key:_field(val,[ref]) for key,val in (
            ('activityName',match['name']),('sourceActivityId',identifier),('type','platform'),('platformCode','meituan'),
            ('rulesText',summary))}
        for key in ('startAt','endAt','remark'):
            fields[key] = projection.user_field(key)
        projection.add(fields,brands=_field(names,brand_refs),method=_field('brand_threshold_subsidy_dual',[ref]))
    return projection.finish()
