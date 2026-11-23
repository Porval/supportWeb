"""Parse calendar days from explicitly scoped coupon date cells."""
from datetime import date, datetime, timedelta, timezone
from typing import NamedTuple
import re
from unicodedata import normalize

BEIJING = timezone(timedelta(hours=8))
CHINESE_DAY = r'(?:[0-9]{4}\s*年\s*)?[0-9]{1,2}\s*月\s*[0-9]{1,2}\s*日'
ISO_DAY = r'[0-9]{4}-[0-9]{2}-[0-9]{2}(?:T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]{1,6})?(?:Z|[+-][0-9]{2}:[0-9]{2}))?'
DAY = rf'(?:{CHINESE_DAY}|{ISO_DAY})'
SPAN = re.compile(rf'(?:活动时间\s*:?\s*)?({DAY})\s*(?:至|到|[-—–~～]|\s/\s)\s*({DAY})')
SINGLE_ACTIVITY = re.compile(rf'活动(开始|结束)(?:时间|日期)?\s*:?\s*({DAY})')
SINGLE_REGISTRATION = re.compile(rf'(?:报名时间\s*:?\s*)?({DAY})')
CN_PARTS = re.compile(r'(?:(\d{4})年)?(\d{1,2})月(\d{1,2})日')
FIELDS = ('startAt', 'endAt')


class Day(NamedTuple):
    on: date | None
    month_day: str


def parse_day(text):
    """Retain month/day with an unresolved year and reject invalid calendar values."""
    cn = CN_PARTS.fullmatch(re.sub(r'\s+', '', text))
    try:
        if cn:
            year, month, day = cn.groups()
            value = date(int(year or 2000), int(month), int(day))
            return Day(value if year else None, value.isoformat()[5:])
        if re.fullmatch(ISO_DAY, text):
            value = (datetime.fromisoformat(text.replace('Z', '+00:00')).astimezone(BEIJING).date()
                     if 'T' in text else date.fromisoformat(text))
            return Day(value, value.isoformat()[5:])
    except (ValueError, OverflowError):
        pass
    return None


def activity_days(text):
    raw = normalize('NFKC', text).strip()
    if span := SPAN.fullmatch(raw):
        return tuple(parse_day(part) for part in span.groups())
    if single := SINGLE_ACTIVITY.fullmatch(raw):
        side, value = single.groups()
        day = parse_day(value)
        return (day, None) if side == '开始' else (None, day)
    return None


def registration_day(text):
    """Parse an explicitly selected registration cell independently of activity dates."""
    match = SINGLE_REGISTRATION.fullmatch(normalize('NFKC', text).strip())
    return parse_day(match[1]) if match else None


def date_boundary(key, day):
    return day.isoformat() + ('T00:00:00+08:00' if key == 'startAt' else 'T23:59:59.999+08:00')


def observe_period(group, periods, required_tiers, add, cite):
    """Keep located dates and promote only endpoints supported for each applicable tier."""
    observed = {key: [] for key in FIELDS}
    reversed_refs = []
    unparsed = False
    for cell, tier in periods:
        dates = activity_days(cell['text']) if cell['sourceType'] == 'string' else None
        if dates is None:
            unparsed = True
            continue
        start, end = dates
        if start and end and start.on and end.on and start.on > end.on:
            reversed_refs.append(cite(cell))
        for key, day in zip(FIELDS, (start, end)):
            if day:
                observed[key].append((day, tier, cite(cell)))
    for key, entries in observed.items():
        if not unparsed and {tier for day, tier, _ in entries if day.on} == required_tiers:
            for day, _, ref in entries:
                if day.on:
                    add(group, key, date_boundary(key, day.on), ref)
    if any(observed.values()):
        group['couponDates'] = observed
    if reversed_refs:
        group['couponDateOrderRefs'] = reversed_refs
    return not unparsed and all({tier for day, tier, _ in entries if day.on} == required_tiers
                                for entries in observed.values())


def reconcile_period(fields, group, manual, conflicts, evidence, warning):
    """Apply observed month/day or range contradictions after exact group merging."""
    observed = group.get('couponDates', {})
    reversed_refs = group.get('couponDateOrderRefs', [])
    if not observed:
        return
    start, end = (fields[key]['value'] for key in FIELDS)
    if start and end and datetime.fromisoformat(start.replace('Z','+00:00')) > datetime.fromisoformat(end.replace('Z','+00:00')):
        reversed_refs = [eid for key in FIELDS for eid in fields[key]['evidenceIds']]
    for key in FIELDS:
        entries = observed.get(key, [])
        days = {day.month_day for day, _, _ in entries}
        full_dates = {day.on for day, _, _ in entries if day.on}
        if not reversed_refs and len(days) <= 1 and len(full_dates) <= 1:
            continue
        field = fields[key]
        refs = list(dict.fromkeys([*field['evidenceIds'], *(eid for _,_,eid in entries), *reversed_refs]))
        candidates = list(dict.fromkeys(date_boundary(key, day.on) for day,_,_ in entries if day.on))
        existing = next((c for c in conflicts if c['field'] == key and c['evidenceIds'] == field['evidenceIds']), None)
        if existing is None:
            conflicts.append({'field':key,'manualValue':manual.get(key),'extractedValues':candidates,'evidenceIds':refs})
        else:
            existing['evidenceIds'] = refs
        field.update(value=manual.get(key),status='conflict',evidenceIds=refs)
    if reversed_refs:
        warning('DATE_RANGE_CONFLICT','开始晚于结束，须人工核对',evidence[reversed_refs[0]]['locator'])
