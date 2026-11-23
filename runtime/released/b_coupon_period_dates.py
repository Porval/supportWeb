"""Project worksheet periods without changing the saved Agent response."""
from datetime import date
from b_activity_dates import SIDES, parse_day, period
from b_activity_period_year import beijing_day, resolve_activity_period
from b_coupon_membership_validation import bind_coupon_sections, _position
from business_agent.activity_model_first.coupon_tier_sheet import tier_schedule
from business_agent.activity_model_first.horizontal_coupon_matrix import coupon_matrix


def _single_tier_scope(result, request):
    source = tier_schedule(request)
    if source is None or len(source[1]) != 1:
        return None
    document, (tier,) = source
    candidate, = result['candidates']
    rule = candidate['fields']['rulesText']
    if rule['status'] != 'explicit':
        return None

    evidence = {item['evidenceId']: item for item in result['evidence']
                if item['materialId'] == document['materialId']
                and item['sourceSha256'] == document['sha256']}
    period_cell = tier.rows[0][1]['具体时间']
    if not any(evidence[key]['locator'] == period_cell['locator']
               and evidence[key]['quote'] == period_cell['text']
               for key in rule['evidenceIds'] if key in evidence):
        return None

    def coordinates(item):
        return _position(item['locator']), item['quote'] if 'quote' in item else item['text']

    upcs = {coordinates(cells['商品69码']): position for position, cells in tier.rows}
    names = {coordinates(cells['商品名称']): position for position, cells in tier.rows}
    seen = set()
    for product in candidate['productRows']:
        refs = {coordinates(evidence[key]) for key in product['evidenceIds'] if key in evidence}
        upc_positions = {upcs[key] for key in refs if key in upcs and key[1].strip() == product['rawUpc']}
        name_positions = {names[key] for key in refs if key in names and key[1].strip() == product['rawName']}
        match = upc_positions & name_positions
        if len(match) != 1 or seen & match:
            return None
        seen.update(match)
    if seen != {position for position, _ in tier.rows}:
        return None
    return [(candidate, period_cell)], {
        key: _position(item['locator']) for key, item in evidence.items()
    }, document


def _period_scope(result, form):
    request = {'documents': result.get('materialResults', []), 'manualFields': form}
    if len(result['candidates']) == 1:
        return _single_tier_scope(result, request)
    matrix = coupon_matrix(request)
    scoped = bind_coupon_sections(result, matrix)
    if scoped is None:
        return None
    groups, evidence, _ = scoped
    return [(candidate, section.period) for candidate, section in groups], evidence, matrix.doc


def candidate_coupon_periods(result, form, submitted_at):
    if any(form['fields'].get(key) for key in SIDES):
        return {}
    scoped = _period_scope(result, form)
    if scoped is None:
        return {}
    groups, evidence, document = scoped
    submitted_on = beijing_day(submitted_at)
    date_conflicts = [item for item in result.get('conflicts', []) if item['field'] in SIDES]
    output = {}
    for candidate, period_cell in groups:
        if any(candidate['fields'][key]['status'] != 'missing' for key in SIDES):
            continue
        source_cell = _position(period_cell['locator'])
        if any(len(groups) == 1 or not issue['evidenceIds'] or source_cell in {evidence.get(key) for key in issue['evidenceIds']}
               for issue in date_conflicts):
            continue
        parts = period(period_cell['text'])
        if not parts or not all(parts):
            continue
        dates = resolve_activity_period(*parts, submitted_on)
        month_days = [f'{month:02d}-{day:02d}' for _, month, day in parts]
        first, last, basis = dates if dates else (None, None, None)
        output[candidate['candidateId']] = {
            'status': 'year_inferred' if basis == 'submission_date' else 'source_date' if dates else 'year_unconfirmed',
            'startOn': first.isoformat() if first else None,
            'endOn': last.isoformat() if last else None,
            'startMonthDay': month_days[0],
            'endMonthDay': month_days[1],
            'inferredFrom': submitted_on.isoformat() if basis == 'submission_date' else None,
            'source': {'materialId':document['materialId'], 'sourceSha256':document['sha256'],
                       'locator':period_cell['locator'], 'quote':period_cell['text']},
        }
    return output


def receipt_confirmation(period_basis, confirmed_fields):
    if not period_basis or period_basis['status'] != 'year_inferred':
        return None
    days = [parse_day(confirmed_fields.get(key)) for key in SIDES]
    if not all(day and day[0] is not None for day in days):
        return None
    confirmed_days = [date(*day).isoformat() for day in days]
    accepted = confirmed_days == [period_basis['startOn'], period_basis['endOn']]
    return dict(period_basis, confirmation='accepted_suggestion' if accepted else 'manual_override',
                confirmedStartOn=confirmed_days[0], confirmedEndOn=confirmed_days[1])
