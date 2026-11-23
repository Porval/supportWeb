"""Confirmed version method; never infer it from a name or candidate product."""
import re
from account_primitives import fail
from b_submission_declarations import ACTIVITY_METHODS, valid_activity_method
from business_agent.activity_price_evidence_108.evidence import PLAIN_DECIMAL

PRODUCT_REGISTRATION_METHODS = frozenset(('shen_jia', 'bao_hao_jia'))
BRAND_SUBSIDY_METHODS = frozenset(code for code, _, kind in ACTIVITY_METHODS if kind == 'brand_subsidy')
THRESHOLD_SUBSIDY_METHODS = frozenset(code for code, _, kind in ACTIVITY_METHODS
                                     if kind in ('category_subsidy', 'brand_subsidy'))
THRESHOLD_COUPON = re.compile(r'满减|满\s*[¥￥]?\s*\d+(?:\.\d+)?\s*元?\s*[减\-－]\s*[¥￥]?\s*\d')
THRESHOLD_SHORTHAND = re.compile(r'\s*\d+(?:\.\d+)?\s*[-－]\s*\d+(?:\.\d+)?\s*')
JIATAN_BEARING = re.compile(
    r'(?:^|[，,。；;：:\n])\s*(?:由\s*)?价探方?\s*(?:承担|出资|出钱)'
    r'(?:\s*(?=$|[，,。；;])|\s*[:：]?\s*(?:全部|全额|[1-9]\d*(?:\.\d+)?\s*[元%％]))'
)
ACTIVITY_METHOD_CONTRACT_VERSION = 'activity-method/1.0'


def read_activity_method(value):
    method = (value or {}).get('activityMethod')
    return method if valid_activity_method(method) else None


def product_registration_enabled(version):
    return read_activity_method(version.get('snapshot') or {}) in PRODUCT_REGISTRATION_METHODS


def require_product_registration(version):
    if not product_registration_enabled(version):
        fail(409, 'ACTIVITY_METHOD_NOT_SUPPORTED', '仅已确认神价或爆好价的当前活动版本支持逐商品报名；请先核对活动方式')


def requires_store_bearing(method, rules):
    return method in THRESHOLD_SUBSIDY_METHODS or (
        method in (None, 'other') and bool(
            THRESHOLD_COUPON.search(rules or '') or THRESHOLD_SHORTHAND.fullmatch(rules or '')))


def has_coupon_scope(method, rules):
    return (requires_store_bearing(method, rules) or method == 'platform_red_packet'
            or method not in PRODUCT_REGISTRATION_METHODS and bool(re.search(r'品类券|同享券|联合券', rules or '')))


def explicit_jiatan_bearing(text):
    return isinstance(text, str) and bool(JIATAN_BEARING.search(text))


def require_signup_price(row):
    price = row.get('signupPriceCandidate') or {}
    amount = price.get('amountDecimal')
    if (price.get('status') in ('ready', 'needs_review')
            and isinstance(amount, str) and PLAIN_DECIMAL.fullmatch(amount)):
        return
    name = row.get('rawName') or row['rowId']
    fail(409, 'REGISTRATION_SIGNUP_PRICE_REQUIRED', f'商品「{name}」缺少可核对的报名价，无法逐商品报名。',
         {'candidateId': row['candidateId'], 'rowId': row['rowId']})
