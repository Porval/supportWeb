"""Versioned dates for the coupon tiers in a completed Agent result."""

from datetime import date, datetime
from zoneinfo import ZoneInfo

from business_agent.activity_model_first.coupon_tier_sheet import tier_schedule

BEIJING = ZoneInfo('Asia/Shanghai')
# The approved winona-2026-11-coupon-tiers case binds the year to these exact workbook bytes.
CONFIRMED_STARTS = {
    '0ac115c32bf7aebfc4240313e4aeb2e32b3d39a19c159716a61a00f04e304bf7': date(2026, 11, 1),
}


def observed_schedule(analysis, source):
    if not analysis or analysis.get('engine') != 'deepseek_model_first_v1' or analysis.get('modelUsed') is not True:
        return None
    candidates = analysis.get('candidates', [])
    if len(candidates) != 1:
        return None
    candidate, = candidates
    rules = candidate['fields'].get('rulesText', {})
    if rules.get('status') != 'explicit' or not (rules.get('value') or '').startswith('满减券共 '):
        return None
    attachments = set(source['materialIds'])
    documents = [doc for doc in analysis['materialResults'] if doc['materialId'] in attachments]
    parsed = tier_schedule({'documents': documents, 'manualFields': {'fields': source['form']}})
    if not parsed:
        return None
    document, tiers = parsed
    header = f'满减券共 {len(tiers)} 档'
    expected = '\n'.join([header, *(tier.summary for tier in tiers)])
    traced = '\n'.join([header, *(tier.summary + '，原表行号' +
                        '、'.join(str(position) for position, _ in tier.rows) for tier in tiers)])
    if not tiers or rules['value'] not in (expected, traced):
        return None

    evidence = {ref['evidenceId']: ref for ref in analysis['evidence'] if
                ref['materialId'] == document['materialId'] and ref['sourceSha256'] == document['sha256']}
    rule_evidence = {ref['locator'].get('cell'): ref for ref_id, ref in evidence.items()
                     if ref_id in rules['evidenceIds']}
    upcs = {cells['商品69码']['locator']['cell']: position for tier in tiers for position, cells in tier.rows}
    names = {cells['商品名称']['locator']['cell']: position for tier in tiers for position, cells in tier.rows}
    product_rows = {}
    for row in candidate['productRows']:
        refs = [evidence[key] for key in row['evidenceIds'] if key in evidence]
        upc_positions = {upcs[ref['locator']['cell']] for ref in refs
                         if ref['locator'].get('cell') in upcs and ref['quote'].strip() == row['rawUpc']}
        name_positions = {names[ref['locator']['cell']] for ref in refs
                          if ref['locator'].get('cell') in names and ref['quote'].strip() == row['rawName']}
        positions = upc_positions & name_positions
        if len(positions) != 1:
            return None
        position, = positions
        if position in product_rows:
            return None
        product_rows[position] = row['rowId']
    if set(product_rows) != {position for tier in tiers for position, _ in tier.rows}:
        return None

    coupons = []
    for index, tier in enumerate(tiers, 1):
        cell = tier.rows[0][1]['具体时间']
        ref = rule_evidence.get(cell['locator']['cell'])
        if not ref or ref['locator'] != cell['locator'] or ref['quote'] != cell['text']:
            return None
        sm, sd, em, ed = tier.period
        coupons.append({
            'tierId': f'coupon-{index}', 'name': tier.name, 'channel': tier.channel,
            'startMonthDay': f'{sm:02d}-{sd:02d}', 'endMonthDay': f'{em:02d}-{ed:02d}',
            'productCount': len(tier.rows), 'productRowIds': [product_rows[pos] for pos, _ in tier.rows],
            'dateEvidenceId': ref['evidenceId'],
        })
    return {'candidateId': candidate['candidateId'], 'materialId': document['materialId'],
            'sourceSha256': document['sha256'], 'tiers': coupons}


def beijing_day(value):
    if not value:
        return None
    parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
    return parsed.astimezone(BEIJING).date() if parsed.tzinfo else parsed.date()


def has_distinct_periods(schedule):
    """Detect incompatible dated tiers without deciding how to regroup them."""
    if not schedule:
        return False
    periods = []
    for tier in schedule.get('tiers', []):
        start = tier.get('startOn') or tier.get('startMonthDay')
        end = tier.get('endOn') or tier.get('endMonthDay')
        if start and end:
            periods.append((start, end))
    if any(len(start) == 5 or len(end) == 5 for start, end in periods):
        periods = [(start[-5:], end[-5:]) for start, end in periods]
    return len(set(periods)) > 1


def date_schedule(schedule, start_at=None, end_at=None):
    """Resolve an exact activity envelope from the confirmed start day and each tier."""
    approved_start = CONFIRMED_STARTS.get(schedule['sourceSha256'])
    supplied_start = beijing_day(start_at)
    start = approved_start or supplied_start
    if start is None:
        return schedule
    if supplied_start and supplied_start != start:
        raise ValueError('Start differs from confirmed source date.')
    start_md = start.strftime('%m-%d')
    tiers = []
    for tier in schedule['tiers']:
        first, last = tier['startMonthDay'], tier['endMonthDay']
        start_year = start.year + (first < start_md)
        first_day = date.fromisoformat(f'{start_year}-{first}')
        last_day = date.fromisoformat(f'{start_year + (last < first)}-{last}')
        tiers.append(tier | {'startOn': first_day.isoformat(), 'endOn': last_day.isoformat()})
    end_on = max(tier['endOn'] for tier in tiers)
    supplied_end = beijing_day(end_at)
    if min(tier['startOn'] for tier in tiers) != start.isoformat() or (supplied_end and supplied_end.isoformat() != end_on):
        raise ValueError('Parent dates must equal the full range of the coupon tiers.')
    return schedule | {
        'startAt': start.isoformat() + 'T00:00:00+08:00',
        'endAt': end_on + 'T23:59:59.999999+08:00',
        'yearBasis': 'confirmed_source' if approved_start else 'confirmed_parent_start',
        'tiers': tiers,
    }


def public_schedule(schedule):
    return {key: schedule[key] for key in ('startAt', 'endAt', 'showOriginalRules') if key in schedule} | {
        'tiers': [{key: tier[key] for key in ('tierId', 'name', 'channel', 'startOn', 'endOn', 'productCount')
                   if key in tier} for tier in schedule['tiers']],
    }
