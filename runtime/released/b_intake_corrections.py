"""Human corrections to a checked Agent group, bound to its original submission."""
from datetime import date
import re

from account_primitives import fail
from b_coupon_schedule import beijing_day, date_schedule
from b_domain import fields, text
from business_agent.data_agent_catalog import catalog


def company_products(recognition, identifier, snapshot=None):
    value = (text(identifier, 200, False) or '').strip()
    if not value:
        fail(422, 'INVALID_REQUEST', '请输入商品内部码或 UPC')
    if not recognition:
        fail(503, 'PRODUCT_DIRECTORY_UNAVAILABLE', '公司商品目录暂不可用')
    try:
        snapshot = snapshot or catalog.read_cache(recognition.cache)
    except (OSError, ValueError, TypeError, AttributeError, KeyError):
        fail(503, 'PRODUCT_DIRECTORY_UNAVAILABLE', '公司商品目录暂不可用')
    data = snapshot['export']
    if value in data['attributes']:
        codes = [value]
    else:
        match = catalog._lookup(data, value)
        if match['status'] == 'source_conflict':
            fail(409, 'PRODUCT_DIRECTORY_CONFLICT', '该 UPC 的目录来源有冲突，请用商品内部码查询')
        codes = [entry['internalCode'] for entry in match['candidates']]
    products = []
    for code in codes:
        item = data['attributes'][code]
        barcodes = [upc for upc, ids in data['upcs'].items() if code in ids]
        products.append({'internalCode': code, 'upc': value if value in barcodes else
                         barcodes[0] if len(barcodes) == 1 else None, 'name': item['item_name'],
                         'spec': item['spec'], 'brand': item['brand']})
    return {'products': products, 'catalogSha256': snapshot['exportSha256'], 'catalogDate': snapshot['syncDay']}


def checked_corrections(body, originals, recognition, candidate, already_confirmed_codes=()):
    fields(body, [], ['products', 'couponTiers'])

    def rows(key, limit):
        items = body.get(key, [])
        if not isinstance(items, list) or len(items) > limit:
            fail(422, 'INVALID_REQUEST', '人工补正明细无效')
        return items

    def source(item):
        material_id = text(item['materialId'], 200, False)
        location = (text(item['location'], 200, False) or '').strip()
        original = originals.get(material_id)
        if not original or not location:
            fail(422, 'INVALID_EVIDENCE', '请选择本次提报的原件，并填写原件中的位置')
        return {'materialId': material_id, 'sourceSha256': original['sha256'],
                'locator': {'kind': 'manual', 'location': location},
                'basis': 'submitter_reviewed_original'}

    products = []
    snapshot=None
    if body.get('products'):
        try:
            snapshot=catalog.read_cache(recognition.cache)
        except (OSError, ValueError, TypeError, AttributeError, KeyError):
            fail(503, 'PRODUCT_DIRECTORY_UNAVAILABLE', '公司商品目录暂不可用')
    used = set(already_confirmed_codes)
    used.update(row['rawInternalCode'] for row in candidate['productRows'] if row.get('rawInternalCode'))
    for row in rows('products', 100):
        fields(row, ['internalCode', 'materialId', 'location'], ['signupPrice'])
        code = (text(row['internalCode'], 200, False) or '').strip()
        if code in used:
            fail(422, 'INVALID_CANDIDATE', '该商品已在本分组，请核对现有商品行')
        ref = source(row)
        found = company_products(recognition, code, snapshot)
        options = [product for product in found['products'] if product['internalCode'] == code]
        if len(options) != 1:
            fail(422, 'INVALID_CANDIDATE', '请选择公司商品目录中有效的内部码')
        used.add(code)
        price=row.get('signupPrice')
        if price not in (None,''):
            from business_agent.activity_price_evidence_108.evidence import PLAIN_DECIMAL
            if not isinstance(price,str) or not PLAIN_DECIMAL.fullmatch(price):
                fail(422,'INVALID_REQUEST','报名价须为有效的非负数字')
        products.append({'rowId': f'manual-product-{len(products)+1}', 'internalCode': code,
                         'productSnapshot': options[0], 'sourceReference': ref,
                         'catalogDate': found['catalogDate'], 'catalogSha256': found['catalogSha256'],
                         **({'signupPrice':price} if price not in (None,'') else {})})

    tiers = []
    for row in rows('couponTiers', 30):
        fields(row, ['name', 'startOn', 'endOn', 'materialId', 'location'])
        name = (text(row['name'], 200, False) or '').strip()
        start, end = row['startOn'], row['endOn']
        try:
            if not all(isinstance(day, str) and re.fullmatch(r'\d{4}-\d{2}-\d{2}', day)
                       for day in (start, end)) or date.fromisoformat(end) < date.fromisoformat(start):
                raise ValueError
        except ValueError:
            fail(422, 'COUPON_PERIOD_MISMATCH', '子券须填写完整的有效期，结束日不能早于开始日')
        if not name:
            fail(422, 'INVALID_REQUEST', '请填写子券名称或优惠内容')
        tiers.append({'tierId': f'manual-coupon-{len(tiers)+1}', 'name': name, 'startOn': start, 'endOn': end,
                      'sourceReference': source(row)})
    return {key: value for key, value in [('products', products), ('couponTiers', tiers)] if value}


def merge_coupon_dates(schedule, extra_tiers, confirmed, source):
    if not extra_tiers:
        return date_schedule(schedule, confirmed['startAt'], confirmed['endAt']) if schedule else None
    if schedule:
        dated = date_schedule(schedule)
        if 'startAt' not in dated:
            dated = date_schedule(schedule, source['form'].get('startAt') or confirmed['startAt'])
        if 'startAt' not in dated:
            raise ValueError
        start = beijing_day(dated['startAt'])
        end = beijing_day(dated['endAt'])
        existing = dated['tiers']
    else:
        start = beijing_day(confirmed['startAt'])
        end = beijing_day(confirmed['endAt'])
        if start is None or end is None:
            raise ValueError
        dated = {'includesManualTiers': True, 'showOriginalRules': True}
        existing = []
    tiers = [*existing, *extra_tiers]
    if len({(tier['name'], tier['startOn'], tier['endOn']) for tier in tiers}) != len(tiers):
        fail(422, 'INVALID_REQUEST', '同一券档及有效期只需保留一条')
    start = min(start, *(date.fromisoformat(tier['startOn']) for tier in tiers))
    end = max(end, *(date.fromisoformat(tier['endOn']) for tier in tiers))
    return dated | {'tiers': tiers,'includesManualTiers': True,
                    'startAt': f'{start}T00:00:00+08:00', 'endAt': f'{end}T23:59:59.999999+08:00'}
