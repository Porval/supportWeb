"""Confirmed business dimensions and version-scoped per-SKU coupon projections."""
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone

from b_activity_method import PRODUCT_REGISTRATION_METHODS, THRESHOLD_SUBSIDY_METHODS

CATEGORIES = {'product_discount': '商品折扣', 'threshold_coupon': '满减活动'}
INITIATORS = ('brand', 'platform', 'jiatan_sourcing')
BEIJING = timezone(timedelta(hours=8))


def method_category(method):
    if method in PRODUCT_REGISTRATION_METHODS:
        return 'product_discount'
    if method in THRESHOLD_SUBSIDY_METHODS:
        return 'threshold_coupon'
    return None


def dimensions(activity, snapshot):
    fields = snapshot.get('resolvedFields') or snapshot.get('confirmedFields') or {}
    initiator = fields.get('type') or snapshot.get('initiatorType') or activity.get('type')
    method = snapshot.get('activityMethod') if snapshot else activity.get('activityMethod')
    category = method_category(method)
    bound = snapshot.get('couponReviewInput') or snapshot.get('mixedCouponReview') or {}
    tiers = bound.get('options') or (snapshot.get('couponSchedule') or {}).get('tiers')
    if tiers:
        category = None if category == 'product_discount' else 'threshold_coupon'
    return {'initiatorType': initiator if initiator in INITIATORS else None, 'activityCategory': category}


def tier_period(tier, today):
    start = tier.get('startOn') or tier.get('startDate')
    end = tier.get('endOn') or tier.get('endDate')
    try:
        first, last = date.fromisoformat(start), date.fromisoformat(end)
        if first > last:
            raise ValueError
        state = 'upcoming' if today < first else 'expired' if today > last else 'current'
    except (TypeError, ValueError):
        state = 'unknown'
    return {'startDate': start, 'endDate': end, 'periodState': state}


def dated_review(review, clock):
    if review is None:
        return None
    today = datetime.fromtimestamp(clock, BEIJING).date()
    return review | {'options': [tier | tier_period(tier, today) for tier in review['options']]}


def public_tier(tier):
    keys = ('tierId', 'sourceTierKey', 'name', 'platformCode', 'channel', 'thresholdAmount', 'discountAmount',
            'fundingMode', 'fundingShares', 'storeFundingStatus', 'submitterFundingNote', 'productScopeStatus',
            'startDate', 'endDate', 'periodState', 'reviewSelected')
    return {key: tier[key] for key in keys if key in tier}


class CouponIndex:
    """Built once per readable version. Index only the tiers applicable to each identity."""
    def __init__(self, review):
        self.by_upc = defaultdict(list)
        self.by_row = defaultdict(list)
        self.tiers = []
        self.detail_tiers = []
        approved = review['approvedCouponIds']
        selected = set(approved or ())
        for option in review['options']:
            tier = public_tier(option)
            tier['reviewSelected'] = tier['tierId'] in selected if approved is not None else None
            self.tiers.append(tier)
            self.detail_tiers.append(tier | {key: option.get(key) or [] for key in ('sourceProducts','sourceUpcs')})
            if option.get('productScopeStatus') == 'source_unmarked' or tier['reviewSelected'] is False:
                continue
            for upc in option.get('sourceUpcs') or []:
                self.by_upc[upc].append(tier)
            for row in option.get('productRowIds') or []:
                self.by_row[row].append(tier)

    def for_product(self, product, relation, version_id, ambiguous):
        upc = product.get('upc')
        tiers = list(self.by_upc.get(upc, ())) if upc and upc not in ambiguous else []
        for source in relation.get('relationSources', []):
            if source.get('versionId') == version_id:
                tiers.extend(self.by_row.get(source.get('rowId'), ()))
        platforms = set(relation.get('platformCodes') or [])
        return list({t['tierId']: t for t in tiers if not platforms or not t.get('platformCode')
                     or t['platformCode'] in platforms}.values())


def attach_coupons(db, data, clock, *, can_read_material, ambiguous_upcs, detail_id=None, with_product_rows=True):
    from b_coupon_review import review_projection
    from b_domain import entity

    products = {p['id']: p for p in data['products']}
    activities = {a['id']: a for a in data['activities']}
    readable = {v['id'] for v in data['activity_versions']}
    cache = {}

    def index(version_id):
        if version_id not in cache:
            version = entity(db, 'activity_versions', version_id)
            mids = {mid for mid in version.get('materialIds', []) if can_read_material(mid)}
            review = dated_review(review_projection(db,version,visible_material_ids=mids,
                                                    include_products=with_product_rows),clock)
            cache[version_id] = CouponIndex(review) if review else None
        return cache[version_id]

    versions = {v['id']:v for v in data['activity_versions']}
    for rel in data['activity_products'] if with_product_rows else ():
        activity = activities[rel['activityId']]
        vid = activity.get('approvedVersionId') or activity.get('displayVersionId')
        if vid not in readable:
            continue
        snap = versions[vid]['snapshot']
        rel.update({key:snap.get(key) for key in ('activityCategory','initiatorType') if snap.get(key)})
        if rel.get('activityCategory',activity['activityCategory']) != 'threshold_coupon':
            continue
        idx = index(vid)
        if idx:
            rel['couponTiers'] = idx.for_product(products[rel['productId']], rel, vid, ambiguous_upcs)
            rel['platformCodes'] = list(dict.fromkeys((rel.get('platformCodes') or []) +
                                             [t['platformCode'] for t in rel['couponTiers'] if t.get('platformCode')]))
    labels = {'brand_only':'品牌单补','brand_store':'两方补贴','brand_store_platform':'三方补贴'}
    for activity in activities.values():
        if activity.get('activityCategory')!='threshold_coupon' or activity.get('displayVersionId') not in readable:
            continue
        idx = index(activity['displayVersionId'])
        modes = {t['fundingMode'] for t in idx.tiers} if idx else set()
        known = modes & labels.keys()
        if known:
            summary = '多种补贴' if len(known)>1 else labels[next(iter(known))]
            activity['fundingSummary'] = summary + ('（部分券档出资待确认）' if 'unknown' in modes else '')
    if detail_id in activities:
        item = activities[detail_id]
        vid = item.get('displayVersionId')
        if item['activityCategory'] == 'threshold_coupon' and vid in readable:
            idx = index(vid)
            item['couponTiers'] = idx.detail_tiers if idx else []


def export_participation(activity):
    platforms = {'meituan': '美团', 'taobao_instant': '淘宝闪购', 'jd': '京东'}
    parties = {'brand': '品牌', 'store': '门店', 'platform': '平台', 'company': '公司'}
    prices = []
    for item in activity.get('participationPrices', []):
        parts = [platforms.get(item.get('platformCode'), '')]
        for key, label in [('signupPrice', '报名价'), ('activityPrice', '活动价')]:
            amount = (item.get(key) or {}).get('amountDecimal')
            if amount is not None:
                parts.append(f'{label} {amount}')
        prices.append(' '.join(filter(None, parts)))
    coupons = []
    for tier in activity.get('couponTiers', []):
        parts = [platforms.get(tier.get('platformCode'), ''), tier.get('name')]
        for share in tier.get('fundingShares') or []:
            amount, percent = share.get('amount'), share.get('percent')
            if amount is not None or percent is not None:
                value = f'{amount} 元' if amount is not None else f'{percent}%'
                parts.append(parties.get(share['party'], share['party']) + ' ' + value)
        if tier.get('startDate') and tier.get('endDate'):
            parts.append(f"{tier['startDate']} 至 {tier['endDate']}")
        coupons.append(' '.join(filter(None, parts)))
    return [activity.get('activityCategory'), '；'.join(prices) or None, '；'.join(coupons) or None]
