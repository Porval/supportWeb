"""Project repeated tab-delimited declarations using their original line evidence."""
from collections import defaultdict
from datetime import date
from decimal import Decimal
import re

FORM_DECLARATIONS = {"sourceActivityId", "manualProductRows", "rulesText", "brandBenefit",
                     "remark", "brandIds", "startAt", "endAt", "type"}
PLATFORMS = {"美团": "meituan", "淘宝闪购": "taobao_instant", "淘闪": "taobao_instant", "京东": "jd"}
PERIOD = re.compile(r"(\d{1,2})[./月](\d{1,2})日?\s*[-–—至]\s*(\d{1,2})[./月](\d{1,2})日?")
TIER = re.compile(r"(\d{1,8}(?:\.\d{1,2})?)\s*[-减]\s*(\d{1,8}(?:\.\d{1,2})?)")
FUNDING = re.compile(r"(品牌|美团|平台|商家|门店)(?:补贴|承担|补)?\s*[:：]?\s*(\d{1,8}(?:\.\d{1,2})?)")
MECHANISMS = {"单方补贴": "single", "两方共补": "dual", "二方共补": "dual",
              "双方共补": "dual", "三方共补": "triple"}


def parse_line(text):
    parts = tuple(value.strip() for value in text.rstrip().rstrip("\\").split("\t"))
    if len(parts) != 6:
        return None
    brand, platform, source_label, period, mechanism, terms = parts
    days = PERIOD.fullmatch(period)
    if (not brand or len(brand) > 100 or not source_label or len(source_label) > 100 or not days or platform not in PLATFORMS
            or mechanism not in MECHANISMS or len(terms) > 400 or not TIER.match(terms)):
        return None
    month1, day1, month2, day2 = map(int, days.groups())
    try:
        date(2000, month1, day1)
        date(2000, month2, day2)
    except ValueError:
        return None
    return parts


def consolidate(groups, replies, rows, lookup):
    """Apply the tabular layout only to a text-only batch the model recognized as activity."""
    if not rows:
        return groups, []
    by_row = {source["rowId"]: (token, source) for token, source in lookup.items()
              if source["locator"]["kind"] == "text"}
    if set(by_row) != rows.keys():
        return groups, []
    lines = {}
    for row_id in rows:
        token, source = by_row[row_id]
        parts = parse_line(source["text"])
        if parts is None:
            return groups, []
        lines.setdefault(parts, []).append((token, source))
    if len(lines) > 20:
        return groups, []
    kinds = {row["id"]: row["kind"] for _, observed, _ in replies for row in observed}
    if "product" in kinds.values() or any(
            not any(kinds.get(source["rowId"]) == "activity" for _, source in sources)
            for sources in lines.values()):
        return groups, []
    # Respect explicit identifiers and form metadata handled by the general path.
    if any(source["locator"].get("fieldPath") in FORM_DECLARATIONS for source in lookup.values()):
        return groups, []

    form_fields = {source["locator"]["fieldPath"]: (token, source["text"]) for token, source in lookup.items()
                   if source["locator"]["kind"] == "form"}
    if "platformCode" in form_fields and any(PLATFORMS[parts[1]] != form_fields["platformCode"][1] for parts in lines):
        return groups, []

    output, warnings = {}, []
    for parts, sources in lines.items():
        brand, platform, source_label, period, mechanism, terms = parts
        fields = defaultdict(list)
        for key, value, quote in (
            ("brandNames", [brand], brand),
            ("platformCode", PLATFORMS[platform], platform),
            ("remark", source_label, source_label),
            ("activityMethod", "brand_threshold_subsidy_" + MECHANISMS[mechanism], mechanism),
            ("rulesText", terms, terms),
        ):
            fields[key].append((value, [(token, quote) for token, _ in sources[:8]]))
        # A single declaration may use the submitter's explicit name. A batch title
        # does not become the name of every activity.
        if len(lines) == 1 and "activityName" in form_fields:
            token, name = form_fields["activityName"]
            fields["activityName"].append((name, [(token, name)]))
        output[parts] = {"fields": fields, "rows": []}
        locator = sources[0][1]["locator"]
        warnings.append(("ACTIVITY_PERIOD_YEAR_MISSING",
                         f"{brand}活动期间原文为 {period}，年份待提报人确认。", locator))
        if MECHANISMS[mechanism] == "triple":
            warnings.extend(_funding_warnings(brand, terms, locator))
    return output, warnings


def _funding_warnings(brand, terms, locator):
    parties = {"品牌": "brand", "美团": "platform", "平台": "platform", "商家": "merchant", "门店": "merchant"}
    tiers = list(TIER.finditer(terms))
    boundaries = [tier.start() for tier in tiers[1:]] + [len(terms)]
    for tier, end in zip(tiers, boundaries):
        split = defaultdict(list)
        for party, amount in FUNDING.findall(terms[tier.end():end]):
            split[parties[party]].append(Decimal(amount))
        label = tier.group()
        if len(split) < 3:
            yield ("COUPON_FUNDING_SPLIT_INCOMPLETE",
                   f"{brand} {label} 档三方共补未写全各方金额，请提报人补充。", locator)
        elif any(len(amounts) != 1 for amounts in split.values()) or (
                sum(amounts[0] for amounts in split.values()) != Decimal(tier.group(2))):
            yield ("COUPON_FUNDING_SPLIT_CONFLICT",
                   f"{brand} {label} 档三方承担金额需核对，请提报人确认。", locator)
