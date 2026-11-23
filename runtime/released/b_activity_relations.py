"""Read-only confirmed activity/product/brand projections for JT-024 R1.

This module never writes a product or brand master.  Company internal codes from
the final review become query-only entities when the local master lacks them.
The caller must apply activity/version/product ACLs before counts or exports.
"""
from copy import deepcopy
from hashlib import sha256


CONTRACT_VERSION = 'activity-relations/1.0'
COUPON_METHODS = frozenset({
    'category_threshold_subsidy_single', 'category_threshold_subsidy_dual',
    'brand_threshold_subsidy_single', 'brand_threshold_subsidy_dual',
    'brand_threshold_subsidy_triple',
})
DIRECT_METHODS = frozenset({'shen_jia', 'bao_hao_jia'})


def _text(value):
    return value.strip() if isinstance(value, str) else ''


def _id(prefix, value):
    return prefix + sha256(value.encode('utf-8')).hexdigest()[:24]


def product_id(internal_code):
    """Stable query identity, not a new master SKU ID."""
    return _id('catalog-product-', internal_code)


def catalog_brand_id(label):
    """A catalog label identity deliberately separate from master brand IDs."""
    return _id('catalog-brand-label-', label)


def _unique(values):
    out = []
    for value in values:
        if value is not None and value not in out:
            out.append(value)
    return out


def _method(activity, versions):
    version = versions.get(activity.get('approvedVersionId'), {})
    snapshot = version.get('snapshot') or {}
    return snapshot.get('activityMethod') or activity.get('activityMethod')


def relation_kind(method):
    if method in COUPON_METHODS:
        return 'coupon_applicable', '券适用'
    if method == 'shen_jia':
        return 'shen_jia', '神价'
    if method == 'bao_hao_jia':
        return 'bao_hao_jia', '爆好价'
    return 'confirmed_product', '已确认商品'


def relation_summary(relation):
    """Safe relationship fields to attach to product-to-activity query rows."""
    value = {key: deepcopy(relation[key]) for key in (
        'relationKind', 'relationLabel', 'platformCodes', 'confirmationStatus',
        'relationSources', 'versionIds', 'brandIds', 'couponTiers', 'activityCategory', 'initiatorType',
    ) if key in relation}
    prices = _unique({
        'platformCode': source.get('platformCode'),
        'signupPrice': source.get('signupPrice'),
        'activityPrice': source.get('activityPrice'),
    } for source in relation.get('relationSources', [])
        if source.get('signupPrice') or source.get('activityPrice'))
    if prices:
        value['participationPrices'] = prices
    return value


def _frozen_price(raw):
    if not isinstance(raw, dict):
        return None
    from b_registration_review import price_projection
    price, currency_known, unit_known = price_projection(raw)
    amount = price.get('amountDecimal')
    if not isinstance(amount, str) or not amount:
        return None
    return {'amountDecimal': amount, 'currency': price.get('currency') if currency_known else None,
            'pricingUnit': price.get('pricingUnit') if unit_known else None}


def _missing_metric():
    return {'value': None, 'unit': None, 'periodStart': None, 'periodEnd': None,
            'definitionRef': None, 'sourceRef': None}


def project_confirmed_relations(data, confirmed_sets=(), verified_scopes=()):
    """Return an additive query snapshot without mutating the input or DB.

    Only ``finalized`` sets bound to the activity's current approvedVersionId
    are authoritative. Final removals therefore cannot survive as old product
    relationships. Verified platform scope applies only to confirmed coupon
    methods; actual platform registration never adds direct-activity products.

    Source-label brands are marked ``catalog_label`` and ``queryOnly``. Their
    confirmed relation means a final product snapshot contains that catalog
    label; it does not assert a match to a brand-master identity.
    """
    result = deepcopy(data)
    activities = {}
    for activity in result.get('activities', []):
        activities.setdefault(activity['id'], activity)
    result['activities'] = list(activities.values())
    versions = {row['id']: row for row in result.get('activity_versions', [])}
    brands = {row['id']: row for row in result.get('brands', [])}
    products = {row['id']: row for row in result.get('products', [])}
    codes = {}
    for row in sorted(products.values(), key=lambda value: value['id']):
        code = _text(row.get('internalCode'))
        if code:
            codes.setdefault(code, row['id'])
    report = {'contractVersion': CONTRACT_VERSION, 'confirmedActivityIds': [],
              'completeActivityIds': [], 'incompleteActivities': [], 'issues': []}
    authoritative = []
    for source in confirmed_sets:
        activity = activities.get(source.get('activityId'))
        if (activity and source.get('status') == 'finalized'
                and source.get('versionId') == activity.get('approvedVersionId')
                and source.get('versionId') and isinstance(source.get('finalRows'), list)):
            authoritative.append(source)
    replaced = {source['activityId'] for source in authoritative}
    relations = {}
    brand_relations = {}
    for row in result.get('activity_brands', []):
        if row.get('activityId') in activities and row.get('brandId') in brands:
            brand_relations.setdefault((row['activityId'], row['brandId']), row)

    def issue(activity_id, code, **details):
        report['issues'].append({'activityId': activity_id, 'code': code, **details})

    def add_brand(activity_id, brand_id, source):
        if not brand_id or brand_id not in brands:
            return
        key = activity_id, brand_id
        row = brand_relations.setdefault(key, {
            'activityId': activity_id, 'brandId': brand_id,
            'confirmationStatus': 'confirmed', 'relationSources': [],
            'brandIdentityStatus': brands[brand_id].get('identityStatus', 'master'),
        })
        row['relationSources'] = _unique(row.get('relationSources', []) + [source])
        activity = activities[activity_id]
        activity['brandIds'] = _unique(list(activity.get('brandIds') or []) + [brand_id])

    def ensure_product(row, activity_id, source):
        code = _text(row.get('internalCode'))
        snapshot = row.get('productSnapshot') or {}
        if not code:
            issue(activity_id, 'INTERNAL_CODE_MISSING', rowId=row.get('rowId'))
            return None, None
        if code not in codes:
            pid = product_id(code)
            products[pid] = {
                'id': pid, 'internalCode': code, 'name': snapshot.get('name'),
                'spec': snapshot.get('spec'), 'upc': snapshot.get('upc'),
                'brandId': None, 'relationComplete': True, 'queryOnly': True,
                'identityStatus': 'confirmed_company_internal_code',
                'identitySource': source.get('kind'),
                'relationSourceActivityIds': [], 'relationSourceVersionIds': [],
            }
            codes[code] = pid
        product = products[codes[code]]
        bid = snapshot.get('brandId')
        if bid and bid not in brands:
            issue(activity_id, 'BRAND_ID_UNAVAILABLE', internalCode=code, brandId=bid)
            bid = None
        existing_bid = product.get('brandId')
        if existing_bid and existing_bid in brands and not brands[existing_bid].get('queryOnly'):
            if bid and bid != existing_bid:
                issue(activity_id, 'BRAND_ID_CONFLICT', internalCode=code,
                      brandIds=[existing_bid, bid])
                return product, None
            bid = existing_bid
        if not bid:
            label = _text(snapshot.get('brand'))
            if label:
                bid = catalog_brand_id(label)
                brands.setdefault(bid, {
                    'id': bid, 'name': label, 'queryOnly': True,
                    'identityStatus': 'catalog_label', 'masterBrandId': None,
                    'identitySource': 'company_catalog_product_snapshot',
                    'identityLabel': label,
                })
        if product.get('queryOnly'):
            product['relationSourceActivityIds'] = _unique(
                product.get('relationSourceActivityIds', []) + [activity_id])
            product['relationSourceVersionIds'] = _unique(
                product.get('relationSourceVersionIds', []) + [source.get('versionId')])
            known = product.get('brandId')
            if known and bid and known != bid:
                product['brandIds'] = _unique(product.get('brandIds', [known]) + [bid])
                product['brandIdentityConflict'] = True
            elif bid:
                product['brandId'] = bid
        elif not existing_bid and bid:
            # This fills the read projection only; the persisted master stays
            # untouched and the relationship retains its confirmation source.
            product['brandId'] = bid
            product['brandProjectionOnly'] = True
        return product, bid

    def add_relation(activity_id, product, source, *, brand_id=None, original=None):
        if product is None:
            return
        activity = activities[activity_id]
        code = _text(product.get('internalCode'))
        # Legacy records with missing company code retain their explicit ID.
        key = activity_id, code or product['id']
        kind, label = relation_kind(_method(activity, versions))
        row = relations.setdefault(key, deepcopy(original) if original else {
            'activityId': activity_id, 'productId': product['id'],
            'complete': True, 'activityPrice': None, 'currency': None,
            'priceUnit': None, 'historicalSales': _missing_metric(),
            'latestActivityId': None,
        })
        row.update(productId=product['id'], internalCode=code or None,
                   relationKind=kind, relationLabel=label, confirmationStatus='confirmed')
        platform = source.get('platformCode') or activity.get('platformCode')
        row['platformCodes'] = _unique(row.get('platformCodes', []) + [platform])
        row['versionIds'] = _unique(row.get('versionIds', []) + [source.get('versionId')])
        row['brandIds'] = _unique(row.get('brandIds', []) + [brand_id])
        row['relationSources'] = _unique(row.get('relationSources', []) + [source])
        if product.get('queryOnly'):
            product['relationSourceActivityIds'] = _unique(
                product.get('relationSourceActivityIds', []) + [activity_id])
            product['relationSourceVersionIds'] = _unique(
                product.get('relationSourceVersionIds', []) + [source.get('versionId')])
        if brand_id:
            add_brand(activity_id, brand_id, source)

    # Existing canonical links remain valid until a final list for the current
    # approved version replaces them. Duplicate rows are consolidated by code.
    for row in result.get('activity_products', []):
        aid = row.get('activityId')
        product = products.get(row.get('productId'))
        if aid not in activities or aid in replaced or not product:
            continue
        canonical = products[codes.get(_text(product.get('internalCode')), product['id'])]
        add_relation(aid, canonical, {
            'kind': 'existing_confirmed_relation',
            'versionId': activities[aid].get('approvedVersionId'),
            'platformCode': activities[aid].get('platformCode'),
            **({'activityPrice': {'amountDecimal': str(row['activityPrice']), 'currency': row.get('currency'),
                                 'pricingUnit': row.get('priceUnit')}} if row.get('activityPrice') is not None else {}),
        }, brand_id=canonical.get('brandId'), original=row)

    for aid, activity in activities.items():
        if aid in replaced:
            continue
        vid=activity.get('displayVersionId') or activity.get('currentReviewVersionId')
        version=versions.get(vid) or {}
        if version.get('activityId')!=aid:
            continue
        supplements=version.get('snapshot',{}).get('manualCorrections',{}).get('products',[])
        for row in supplements:
            basis={'kind':'manual_intake_confirmation','versionId':vid,'platformCode':activity.get('platformCode'),
                   'catalogDate':row['catalogDate'],'rowId':row['rowId']}
            product,bid=ensure_product(row,aid,basis)
            add_relation(aid,product,basis,brand_id=bid)

    for source in authoritative:
        aid = source['activityId']
        report['confirmedActivityIds'] = _unique(report['confirmedActivityIds'] + [aid])
        for row in source['finalRows']:
            basis = {
                'kind': 'final_registration_review', 'versionId': source['versionId'],
                'platformCode': source.get('platformCode'),
                'decisionId': source.get('finalDecisionId'),
                'confirmedBy': source.get('finalizedBy'), 'confirmedAt': source.get('finalizedAt'),
                'candidateId': row.get('candidateId'), 'rowId': row.get('rowId'),
                'productSnapshot': deepcopy(row.get('productSnapshot') or {}),
                'sourceRow': deepcopy(row.get('sourceRow') or {}),
            }
            for key in ('signupPrice', 'activityPrice'):
                if price := _frozen_price(row.get(key)):
                    basis[key] = price
            product, bid = ensure_product(row, aid, basis)
            add_relation(aid, product, basis, brand_id=bid)
        if not any(item['activityId'] == aid for item in report['issues']):
            report['completeActivityIds'] = _unique(report['completeActivityIds'] + [aid])

    enumerated_scope_platforms = {}
    for scope in verified_scopes:
        aid = scope.get('activityId')
        activity = activities.get(aid)
        if (not activity or not scope.get('approvedVersionId')
                or scope['approvedVersionId'] != activity.get('approvedVersionId')
                or _method(activity, versions) not in COUPON_METHODS):
            continue
        basis = {'kind': 'verified_coupon_scope', 'versionId': scope['approvedVersionId'],
                 'platformCode': scope.get('platformCode'),
                 'platformMaterialId': scope.get('platformMaterialId'),
                 'decisionId': scope.get('decisionId'), 'confirmedAt': scope.get('verifiedAt'),
                 'scopeMode': scope.get('scopeMode')}
        for bid in scope.get('brandIds') or []:
            add_brand(aid, bid, basis)
        selected = set()
        for pid in scope.get('productIds') or []:
            if pid in products:
                selected.add(codes.get(_text(products[pid].get('internalCode')), pid))
            else:
                issue(aid, 'SCOPE_PRODUCT_UNAVAILABLE', productId=pid)
        for code in scope.get('internalCodes') or []:
            if _text(code) in codes:
                selected.add(codes[_text(code)])
            else:
                issue(aid, 'SCOPE_PRODUCT_UNAVAILABLE', internalCode=code)
        for row in scope.get('productRows') or []:
            product, bid = ensure_product(row, aid, basis)
            add_relation(aid, product, basis, brand_id=bid)
        if scope.get('scopeMode') == 'all_brand_products':
            # Only exact brand IDs qualify. This is a known subset, never a
            # declaration that the company-wide catalog was enumerated.
            bids = set(scope.get('brandIds') or [])
            selected.update(codes.get(_text(p.get('internalCode')), p['id'])
                            for p in products.values() if p.get('brandId') in bids)
        for pid in sorted(selected):
            product = products[pid]
            add_relation(aid, product, basis, brand_id=product.get('brandId'))
        report['confirmedActivityIds'] = _unique(report['confirmedActivityIds'] + [aid])
        if scope.get('scopeNotEnumerated') or scope.get('scopeComplete') is False:
            report['incompleteActivities'].append({
                'activityId': aid, 'platformCode': scope.get('platformCode'),
                'brandIds': list(scope.get('brandIds') or []),
                'reasonCode': 'SCOPE_NOT_ENUMERATED' if scope.get('scopeNotEnumerated')
                              else 'SCOPE_INCOMPLETE', 'source': basis,
            })
        elif scope.get('scopeMode') == 'list':
            enumerated_scope_platforms.setdefault(aid, set()).add(scope.get('platformCode'))

    finalized_platforms = {}
    for source in authoritative:
        finalized_platforms.setdefault(source['activityId'], set()).add(source.get('platformCode'))
    for aid, supplied in enumerated_scope_platforms.items():
        finalized_platforms.setdefault(aid, set()).update(supplied)
        report['completeActivityIds'] = _unique(report['completeActivityIds'] + [aid])
    for aid, supplied in finalized_platforms.items():
        activity = activities[aid]
        version = versions.get(activity.get('approvedVersionId'), {})
        snapshot = version.get('snapshot') or {}
        declared = snapshot.get('applicablePlatformCodes', activity.get('applicablePlatformCodes'))
        required = set(declared) if isinstance(declared, list) else {activity.get('platformCode')}
        if required - supplied - {None}:
            report['incompleteActivities'].append({
                'activityId': aid, 'reasonCode': 'FINALIZED_PLATFORM_MISSING',
                'platformCodes': sorted(required - supplied - {None}),
            })
    incomplete_ids = {item['activityId'] for item in report['incompleteActivities']}
    incomplete_ids.update(item['activityId'] for item in report['issues'])
    report['completeActivityIds'] = [aid for aid in report['completeActivityIds'] if aid not in incomplete_ids]
    result['products'] = list(products.values())
    result['brands'] = list(brands.values())
    result['activity_products'] = list(relations.values())
    result['activity_brands'] = list(brand_relations.values())
    result['relation_projection'] = report
    return result


def _table_exists(db, table):
    return db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone() is not None


def project_data(db, data):
    """Read optional official sources; never migrate or mutate the database."""
    confirmed = []
    if _table_exists(db, 'activity_registration_reviews'):
        from b_registration_review import list_confirmed_product_sets
        confirmed = list_confirmed_product_sets(db)
    from b_platform_materials import verified_scopes
    return project_confirmed_relations(data, confirmed, verified_scopes(db))
