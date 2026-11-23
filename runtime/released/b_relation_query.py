"""Confirmed relationship queries and ACL-safe projections for JT-024 R1.

Mix into BQuery before Query. Call scoped() once after construction and before
handing self.data to any reader, including the brand projection. No DB writes.
"""
from copy import deepcopy
from decimal import Decimal, InvalidOperation
from hashlib import sha256
import json

from account_primitives import fail, iso
from b_workbench_structure import CATEGORIES, export_participation
from b_activity_relations import catalog_brand_id, relation_summary
from queries import COLUMNS, FILTERS, enum, invalid, number, validate


def _unique(values):
    result = []
    for value in values:
        if value is not None and value not in result:
            result.append(value)
    return result


def _code(product):
    return product.get('internalCode') or product['id']


def _matches_activity(activity, search):
    if not search:
        return True
    external_ids = activity.get('externalIds') or []
    ids = [item.get('id') for item in external_ids if isinstance(item, dict)]
    return search in ' '.join(str(value or '') for value in
                              [activity.get('id'), activity.get('name'), *ids]).casefold()


def _source_brand(source, bid):
    snapshot = source.get('productSnapshot') or {}
    return snapshot.get('brandId') == bid or (
        isinstance(snapshot.get('brand'), str)
        and catalog_brand_id(snapshot['brand'].strip()) == bid)


class RelationQueryMixin:
    def scoped(self, grants, *, readable_version_ids=None):
        """Scope first, including the evidence that supplies virtual entities.

        grants uses the baseline '*'/activity:<id>/product:<id>/brand:<id>
        tokens. Existing master products and brands keep their explicit ACLs.
        Query-only products can be read through an authorized activity/version
        relationship. Their names/UPC/spec are rebuilt from readable evidence,
        so a first snapshot from a denied activity cannot leak through another.
        readable_version_ids should come from can_version(), not client input.
        """
        data = deepcopy(self.data)
        unrestricted = grants is None or '*' in grants
        allowed = lambda kind, key: unrestricted or kind + ':' + key in grants
        activities = {a['id']: a for a in data.get('activities', []) if allowed('activity', a['id'])}
        vids = {v['id'] for v in data.get('activity_versions', []) if v.get('activityId') in activities}
        if readable_version_ids is not None:
            vids &= set(readable_version_ids)
        products = {p['id']: p for p in data.get('products', [])}
        brands = {b['id']: b for b in data.get('brands', [])}

        def sources(row):
            return [source for source in row.get('relationSources', [])
                    if source.get('activityId', row.get('activityId')) in activities
                    and (not source.get('versionId') or source['versionId'] in vids)]

        def trim_relation(row):
            value = deepcopy(row)
            if 'relationSources' in value:
                value['relationSources'] = sources(value)
                if not value['relationSources']:
                    return None
                value['versionIds'] = _unique(s.get('versionId') for s in value['relationSources'])
                value['platformCodes'] = _unique(s.get('platformCode') for s in value['relationSources'])
            return value

        relations = []
        for row in data.get('activity_products', []):
            product = products.get(row.get('productId'))
            if row.get('activityId') not in activities or not product:
                continue
            if not product.get('queryOnly') and not allowed('product', product['id']):
                continue
            value = trim_relation(row)
            if value is not None:
                relations.append(value)
        projected_pids = {r['productId'] for r in relations}
        products = {pid: p for pid, p in products.items()
                    if pid in projected_pids and p.get('queryOnly')} | {
                    pid: p for pid, p in products.items() if not p.get('queryOnly') and allowed('product', pid)}

        brand_relations = []
        for row in data.get('activity_brands', []):
            brand = brands.get(row.get('brandId'))
            if row.get('activityId') not in activities or not brand:
                continue
            if not brand.get('queryOnly') and not allowed('brand', brand['id']):
                continue
            value = trim_relation(row)
            if value is None:
                continue
            if brand.get('queryOnly'):
                # A verified code-only coupon scope cannot disclose a brand
                # label that is known solely from another, denied activity.
                value['relationSources'] = [s for s in value.get('relationSources', [])
                                            if _source_brand(s, brand['id'])]
                if not value['relationSources']:
                    continue
            brand_relations.append(value)
        projected_bids = {r['brandId'] for r in brand_relations}
        brands = {bid: b for bid, b in brands.items()
                  if bid in projected_bids and b.get('queryOnly')} | {
                  bid: b for bid, b in brands.items() if not b.get('queryOnly') and allowed('brand', bid)}

        for relation in relations:
            relation['brandIds'] = [bid for bid in relation.get('brandIds', []) if bid in brands]
            # Sources may contain a master brand ID that is outside the actor's
            # explicit brand scope. Do not expose that identity in raw evidence.
            for source in relation.get('relationSources', []):
                snapshot = source.get('productSnapshot')
                if snapshot and snapshot.get('brandId') and snapshot['brandId'] not in brands:
                    snapshot.pop('brandId', None)
        for pid, product in products.items():
            own = [r for r in relations if r['productId'] == pid]
            if product.get('queryOnly'):
                evidence = [s for r in own for s in r.get('relationSources', [])]
                snapshots = [s['productSnapshot'] for s in evidence if s.get('productSnapshot')]
                first = snapshots[0] if snapshots else {}
                for key in ('name', 'spec', 'upc'):
                    product[key] = first.get(key)
                product['identitySource'] = evidence[0].get('kind') if evidence else None
                product['relationSourceActivityIds'] = _unique(r['activityId'] for r in own)
                product['relationSourceVersionIds'] = _unique(s.get('versionId') for s in evidence)
                product['brandIds'] = _unique(bid for r in own for bid in r.get('brandIds', []) if bid in brands)
                product['brandId'] = product['brandIds'][0] if len(product['brandIds']) == 1 else None
                product['brandIdentityConflict'] = len(product['brandIds']) > 1
            else:
                if product.get('brandId') not in brands:
                    product['brandId'] = None
                if 'brandIds' in product:
                    product['brandIds'] = [bid for bid in product['brandIds'] if bid in brands]
        for activity in activities.values():
            activity['brandIds'] = [bid for bid in activity.get('brandIds', []) if bid in brands]
        data.update(activities=list(activities.values()), products=list(products.values()),
                    brands=list(brands.values()), activity_products=relations,
                    activity_brands=brand_relations)
        data['activity_versions'] = [v for v in data.get('activity_versions', []) if v['id'] in vids]
        for key in ('submissions', 'history', 'external_facts', 'decisions', 'review_nodes'):
            if key in data:
                data[key] = [row for row in data[key] if row.get('activityId') in activities]
        data['materials'] = []
        projection = data.get('relation_projection', {})
        complete = {aid for aid in projection.get('completeActivityIds', [])
                    if aid in activities and activities[aid].get('approvedVersionId') in vids}
        incomplete = {aid: deepcopy(scope) for aid, scope in getattr(self, 'incomplete', {}).items()
                      if aid in activities and aid not in complete}
        for scope in projection.get('incompleteActivities', []):
            if scope.get('activityId') in activities:
                item = deepcopy(scope)
                if item.get('source', {}).get('versionId') and item['source']['versionId'] not in vids:
                    continue
                item['brandIds'] = [bid for bid in item.get('brandIds', []) if bid in brands]
                if item.get('reasonCode') == 'SCOPE_NOT_ENUMERATED':
                    item['productListState'] = 'scope_only'
                incomplete[item['activityId']] = item
        safe_issues = []
        for issue in projection.get('issues', []):
            if issue.get('activityId') in activities:
                safe = {'activityId': issue['activityId'], 'code': issue.get('code')}
                safe_issues.append(safe)
                incomplete.setdefault(issue['activityId'], {'reasonCode': 'CONFIRMED_RELATION_SOURCE_INCOMPLETE'})
        for scope in incomplete.values():
            if 'brandIds' in scope:
                scope['brandIds'] = [bid for bid in scope['brandIds'] if bid in brands]
        data['relation_projection'] = {
            'contractVersion': projection.get('contractVersion'),
            'confirmedActivityIds': [aid for aid in projection.get('confirmedActivityIds', []) if aid in activities],
            'completeActivityIds': sorted(complete),
            'incompleteActivities': [{'activityId': aid, **value} for aid, value in incomplete.items()],
            'issues': safe_issues,
        }
        self.incomplete = incomplete
        # Preserve the caller's request-local data alias (used by brand reader).
        self.data.clear()
        self.data.update(data)

    def editable_catalogs(self):
        """Only persisted master identities may be offered as editable inputs."""
        return {key: [deepcopy(row) for row in self.data.get(key, []) if not row.get('queryOnly')]
                for key in ('brands', 'products')}

    def activities(self, q):
        # Existing BQuery handles its extra awaiting-assignment review filter.
        category = enum(q, 'activityCategory', set(CATEGORIES) | {'unknown','all'})
        rows = super().activities({k: v for k, v in q.items() if k not in ('platformCode','activityCategory')})
        if category and category!='all':
            rows = [row for row in rows if (row.get('activityCategory') or 'unknown') == category]
        platform = q.get('platformCode')
        unique = {}
        for row in rows:
            codes = row.get('applicablePlatformCodes') or [row.get('platformCode')]
            if not platform or platform in codes:
                unique.setdefault(row['id'], row)
        return list(unique.values())

    def _product_relations(self, pid, platform=None, brand=None, activity_id=None):
        product = self.get('products', pid)
        products = {p['id']: p for p in self.data['products']}
        activities = {a['id']: a for a in self.data['activities']}
        groups = {}
        for relation in self.data['activity_products']:
            related = products.get(relation['productId'])
            if not related or _code(related) != _code(product):
                continue
            aid = relation['activityId']
            if aid not in activities or activity_id and aid != activity_id:
                continue
            codes = relation.get('platformCodes') or [activities[aid].get('platformCode')]
            if platform and platform not in codes:
                continue
            bids = relation.get('brandIds') or ([related['brandId']] if related.get('brandId') else [])
            if brand and brand not in bids:
                continue
            view = deepcopy(relation)
            if platform:
                view['platformCodes'] = [platform]
                if 'couponTiers' in view:
                    view['couponTiers'] = [tier for tier in view['couponTiers'] if tier.get('platformCode') == platform]
                if 'relationSources' in view:
                    view['relationSources'] = [source for source in view['relationSources']
                                               if source.get('platformCode') in (None, platform)]
            if aid not in groups:
                groups[aid] = view
            else:
                previous = groups[aid]
                for key in ('platformCodes', 'versionIds', 'brandIds', 'relationSources','couponTiers'):
                    previous[key] = _unique(previous.get(key, []) + view.get(key, []))
                previous['complete'] = previous.get('complete', False) and view.get('complete', False)
        return groups

    def relations(self, pid, platform=None):
        grouped = self._product_relations(pid, platform)
        return [self.activity(activity) | relation_summary(grouped[activity['id']])
                for activity in self.data['activities'] if activity['id'] in grouped]

    def _matches_product(self, product, search, relations):
        values = [product.get(key) for key in ('internalCode', 'name', 'upc')]
        for relation in relations.values():
            for source in relation.get('relationSources', []):
                snapshot = source.get('productSnapshot') or {}
                values.extend(snapshot.get(key) for key in ('name', 'upc'))
                raw = source.get('sourceRow') or {}
                values.extend(raw.get(key) for key in ('rawName', 'rawUpc', 'rawInternalCode'))
        return search in ' '.join(str(value or '') for value in values).casefold()

    def products(self, q):
        validate(q, FILTERS['products'] | {'hasActivities', 'activityQ'})
        enum(q, 'hasActivities', {'yes'})
        association = enum(q, 'association', {'all', 'linked', 'unlinked'}, 'linked')
        # This view is the confirmed linked subset. It is not an inventory of
        # master products from which a missing relation could be inferred.
        if association == 'unlinked':
            return []
        search = q.get('q', '').strip().casefold()
        activity_search = q.get('activityQ', '').strip().casefold()
        if len(activity_search) > 200:
            invalid('活动名称或 ID 不能超过 200 个字符')
        activities_by_id = {a['id']: a for a in self.data['activities']}
        if self._affected(q):
            self.meta['complete'] = False
        result, seen = [], set()
        for product in self.data['products']:
            key = _code(product)
            if key in seen:
                continue
            grouped = self._product_relations(product['id'], q.get('platformCode'), q.get('brandId'))
            if activity_search:
                grouped = {aid: relation for aid, relation in grouped.items()
                           if aid in activities_by_id and _matches_activity(activities_by_id[aid], activity_search)}
            if not grouped or not self._matches_product(product, search, grouped):
                continue
            seen.add(key)
            activities = [self.activity(a) | relation_summary(grouped[a['id']])
                          for a in self.data['activities'] if a['id'] in grouped]
            complete = bool(self.data['meta'].get('relationsComplete')) and not self._affected(q)
            complete = complete and all(row.get('complete', False) for row in grouped.values())
            confirmed_complete = set(self.data.get('relation_projection', {}).get('completeActivityIds', []))
            complete = complete and (product.get('relationComplete', False)
                                     or set(grouped) <= confirmed_complete)
            result.append(product | {'brandName':next((b['name'] for b in self.data['brands'] if b['id']==product.get('brandId')),None), 'activities': activities, 'relationComplete': complete,
                                     'activityCount': len(activities) if complete else None})
        self.meta['productActivityScope'] = 'confirmed_linked'
        return result

    def _affected(self, q):
        result = []
        activities = {a['id']: a for a in self.data['activities']}
        activity_search = q.get('activityQ', '').strip().casefold()
        for aid, scope in getattr(self, 'incomplete', {}).items():
            if aid not in activities or not _matches_activity(activities[aid], activity_search):
                continue
            platforms = scope.get('platformCodes') or ([scope['platformCode']] if scope.get('platformCode') else [])
            if q.get('platformCode') and platforms and q['platformCode'] not in platforms:
                continue
            if q.get('brandId') and scope.get('brandIds') and q['brandId'] not in scope['brandIds']:
                continue
            result.append(aid)
        return result

    def activity_products(self, aid, q):
        self.get('activities', aid)
        validate(q, {'sort', 'q', 'brandId', 'platformCode'})
        sort = enum(q, 'sort', {'source_order', 'historical_sales_desc'}, 'source_order')
        search = q.get('q', '').strip().casefold()
        result, seen = [], set()
        for product in self.data['products']:
            if _code(product) in seen:
                continue
            grouped = self._product_relations(product['id'], q.get('platformCode'), q.get('brandId'), aid)
            if aid not in grouped or not self._matches_product(product, search, grouped):
                continue
            seen.add(_code(product))
            relation = grouped[aid]
            result.append({'product': product, 'relation': relation, 'latestActivity': None,
                           'historicalSales': relation.get('historicalSales') or {}})
        if sort == 'historical_sales_desc':
            if any(any(row['historicalSales'].get(key) is None for key in
                       ('value', 'unit', 'periodStart', 'periodEnd', 'definitionRef')) for row in result):
                invalid('历史销量口径或区间不完整，不能排序', 'RULE_UNCONFIRMED')
            try:
                result.sort(key=lambda row: (-Decimal(row['historicalSales']['value']), _code(row['product'])))
            except (InvalidOperation, TypeError):
                invalid('销量数据无效', 'RULE_UNCONFIRMED')
        return result

    def snapshot(self, q):
        kind = enum(q, 'kind', {'product_activity', 'activity_products'})
        if kind is None:
            invalid('缺少导出kind')
        scope = enum(q, 'exportScope', {'confirmed_linked'})
        if scope and kind != 'product_activity':
            invalid('已确认关联导出仅适用于商品视角')
        number(q.get('page'), 1)
        number(q.get('pageSize'), 20, 100)
        filters = {key: value for key, value in q.items() if key not in ('kind', 'page', 'pageSize', 'exportScope')}
        pairs = []
        if kind == 'product_activity':
            products = self.products(filters)
            bad = self._affected(filters)
            for product in products:
                pairs.extend((product, activity) for activity in product['activities'])
        else:
            aid = filters.get('activityId')
            if not aid:
                invalid('缺少activityId')
            rows = self.activity_products(aid, {key: value for key, value in filters.items() if key != 'activityId'})
            bad = [aid] if aid in getattr(self, 'incomplete', {}) else []
            activity = self.activity(self.get('activities', aid))
            pairs = [(row['product'], activity | relation_summary(row['relation'])) for row in rows]
            if any(not row['relation'].get('complete') for row in rows):
                invalid('商品关联不完整', 'INCOMPLETE_RELATIONS')
        complete = not bad and bool(self.data['meta'].get('relationsComplete')) and (
            kind != 'product_activity' or all(product['relationComplete'] for product in products))
        if not scope and any(self.incomplete[aid].get('reasonCode') == 'SCOPE_NOT_ENUMERATED' for aid in bad):
            fail(422, 'SCOPE_NOT_ENUMERATED', '已记录适用范围，尚未提供逐项SKU，不能伪全量', {'activityIds': bad})
        if not scope and (bad or not self.data['meta'].get('relationsComplete')):
            invalid('本次范围有未确认商品关系，不能假全量', 'INCOMPLETE_RELATIONS')
        if not scope and not complete:
            invalid('商品关联不完整', 'INCOMPLETE_RELATIONS')
        brand_names = {brand['id']: brand['name'] for brand in self.data['brands']}
        rows, seen = [], set()
        for product, activity in pairs:
            key = _code(product), activity['id']
            if key in seen:
                continue
            seen.add(key)
            review, registration = activity.get('review') or {}, activity.get('registration') or {}
            source = [{key: value for key, value in item.items() if key in
                       ('kind', 'versionId', 'decisionId', 'platformMaterialId', 'rowId', 'candidateId')}
                      for item in activity.get('relationSources', [])]
            bids = activity.get('brandIds') or ([product['brandId']] if product.get('brandId') else [])
            codes = activity.get('platformCodes') or [activity.get('platformCode') or '']
            rows.append([
                product.get('internalCode'), product.get('upc'), product.get('name'), product.get('spec'),
                '、'.join(_unique(brand_names[bid] for bid in bids if bid in brand_names)) or None,
                activity['id'], activity.get('name'), activity.get('type'),
                ':'.join(value for value in (review.get('stage'), review.get('result')) if value),
                '暂时无法确认' if registration.get('state') == 'unknown' else registration.get('state'),
                registration.get('sourceRef'), registration.get('checkedAt'),
                ','.join(codes),
                activity.get('startAt'), activity.get('endAt'), activity.get('relationLabel', '已确认商品'),
                activity.get('relationKind', 'confirmed_product'),
                json.dumps(source, ensure_ascii=False, sort_keys=True, separators=(',', ':')),
            ])
            if scope:
                status_fn = getattr(self, 'product_status', None)
                status = status_fn(activity) if status_fn else {}
                progress = [item for item in status.get('platformProgress', []) if item['platformCode'] in codes]
                rows[-1].extend((status.get('activityPeriodState', 'unknown'), json.dumps(progress, ensure_ascii=False)))
            rows[-1].extend(export_participation(activity))
            if len(rows) > 10000:
                invalid('导出超过10000行上限', 'EXPORT_LIMIT')
        if not rows:
            invalid('当前授权筛选无结果', 'EMPTY_EXPORT')
        columns = deepcopy(COLUMNS) + [
            {'key': 'relationKind', 'label': '关联类型', 'type': 'text'},
            {'key': 'relationSource', 'label': '关联确认依据', 'type': 'text'},
        ]
        if scope:
            columns.extend(({'key': 'activityPeriodState', 'label': '活动状态', 'type': 'text'},
                            {'key': 'platformProgress', 'label': '平台进度', 'type': 'text'}))
        for column in columns:
            if column['key']=='activityType':
                column['label']='发起方'
        columns.extend({'key':key,'label':label,'type':'text'} for key,label in (
            ('activityCategory','活动类型'),('participationPrices','本场商品价格'),('couponTiers','本商品适用券档')))
        value = {'kind': kind, 'filters': filters, 'columns': columns, 'rows': rows,
                 'rowCount': len(rows), 'generatedAt': iso(self.clock), 'fetchedAt': self.meta['fetchedAt'],
                 'datasetRevision': self.meta['datasetRevision'], 'complete': complete}
        if scope:
            value['exportScope'] = scope
        value['snapshotId'] = 'snapshot-' + sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()[:32]
        return value
