"""Submission scope declarations, legacy projection and immutable input hashes."""
from b_state import fingerprint

CONTRACT_VERSION = 'submission-declarations/1.0'
APPLICABLE_PLATFORMS = (('meituan', '美团'), ('taobao_instant', '淘宝闪购'), ('jd', '京东'))
PRODUCT_SCOPE_INTENTS = (
    ('deferred', '后续补上'), ('all_products', '全部商品'),
    ('specific_products', '指定商品'), ('no_specific_products', '无需指定商品'),
)
PLATFORM_FIELD = 'applicablePlatformCodes'
SCOPE_FIELD = 'productScopeIntent'
DECLARATION_FIELDS = (PLATFORM_FIELD, SCOPE_FIELD)
PLATFORM_CODES = tuple(code for code, _ in APPLICABLE_PLATFORMS)
SCOPE_CODES = frozenset(code for code, _ in PRODUCT_SCOPE_INTENTS)
ACTIVITY_METHODS = (
    ('shen_jia', '神价', 'direct'),
    ('bao_hao_jia', '爆好价', 'direct'),
    ('category_threshold_subsidy_single', '品类满减补贴（单方）', 'category_subsidy'),
    ('category_threshold_subsidy_dual', '品类满减补贴（双方）', 'category_subsidy'),
    ('brand_threshold_subsidy_single', '品牌满减补贴（单方）', 'brand_subsidy'),
    ('brand_threshold_subsidy_dual', '品牌满减补贴（双方）', 'brand_subsidy'),
    ('brand_threshold_subsidy_triple', '品牌满减补贴（三方）', 'brand_subsidy'),
    ('platform_red_packet', '平台红包', 'direct'),
    ('other', '其他', 'direct'),
)
METHOD_CODES = frozenset(code for code, _, _ in ACTIVITY_METHODS)


def normalize_platforms(value):
    if (not isinstance(value, list) or len(value)>len(PLATFORM_CODES)
            or any(not isinstance(code,str) or code not in PLATFORM_CODES for code in value)
            or len(set(value))!=len(value)):
        raise ValueError('Invalid applicable platforms')
    return [code for code in PLATFORM_CODES if code in value]


def valid_product_scope(value):
    return value is None or (isinstance(value,str) and value in SCOPE_CODES)


def valid_activity_method(value):
    return value is None or (isinstance(value,str) and value in METHOD_CODES)


def projected_declarations(source, scalar=None):
    if PLATFORM_FIELD in source:
        try:platforms=normalize_platforms(source[PLATFORM_FIELD])
        except ValueError:platforms=[]
    else:
        platforms=[scalar] if scalar in PLATFORM_CODES else []
    scope=source.get(SCOPE_FIELD)
    return {PLATFORM_FIELD:platforms,SCOPE_FIELD:scope if valid_product_scope(scope) else None}


def version_platform(snapshot):
    fields=snapshot.get('resolvedFields') or snapshot.get('confirmedFields') or {}
    return fields.get('platformCode')


def current_formula(source):
    if any(key in source for key in DECLARATION_FIELDS):
        return 'input/1.3' if source.get('inputFormulaVersion') == 'input/1.3' else 'input/1.2'
    if 'activityMethod' in source:return 'input/1.1'
    return 'input/1.0'


def recorded_form(source):
    """The exact submitted form bytes, including declarations on new revisions."""
    if source.get('inputFormulaVersion') == 'input/1.3':
        return source['form'] | {key: source[key] for key in DECLARATION_FIELDS if key in source}
    return source['form']


def declared_scope(values, declarations, products, unresolved, *, allow_broad):
    brands = values.get('brandIds') or []
    scope = {'kind': 'explicit_skus' if products and not unresolved else 'unknown',
             'scopeBrandIds': brands, 'evidenceRefs': [], 'scopeRuleVersion': None}
    intent = declarations.get(SCOPE_FIELD)
    codes = projected_declarations(declarations, values.get('platformCode'))[PLATFORM_FIELD]
    if allow_broad and intent in ('all_products', 'no_specific_products') and codes:
        if intent == 'no_specific_products' or values.get('type') != 'brand' or brands:
            scope.update(kind=intent, platformCodes=codes, scopeRuleVersion='submitter-scope/1.0')
    return scope


def input_formula_version(source):
    version=source.get('inputFormulaVersion','input/1.0')
    if version!=current_formula(source):
        raise ValueError('Input hash formula and declarations do not match')
    if PLATFORM_FIELD in source and normalize_platforms(source[PLATFORM_FIELD])!=source[PLATFORM_FIELD]:
        raise ValueError('Applicable platform order is not canonical')
    if SCOPE_FIELD in source and not valid_product_scope(source[SCOPE_FIELD]):
        raise ValueError('Invalid product scope declaration')
    if 'activityMethod' in source and not valid_activity_method(source['activityMethod']):
        raise ValueError('Invalid activity method declaration')
    return version


def input_fingerprint(revision,source,originals):
    version=input_formula_version(source)
    form_id=source['formMaterialId']
    payload={'inputRevision':revision,'formMaterial':{'materialId':form_id,'sha256':originals[form_id]['sha256']},
             'materials':[{'materialId':mid,'sha256':originals[mid]['sha256'],'filename':originals[mid]['filename'],
                           'mediaType':originals[mid]['media_type']} for mid in source['allMaterialIds'] if mid!=form_id]}
    if version!='input/1.0':
        payload['inputFormulaVersion']=version
        payload.update({key:source[key] for key in (*DECLARATION_FIELDS,'activityMethod') if key in source})
    return fingerprint(payload)
