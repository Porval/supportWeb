"""Resolve an evidenced period against a stable Beijing submission date."""
from datetime import date, datetime, timedelta, timezone

BEIJING = timezone(timedelta(hours=8))


def beijing_day(timestamp):
    return datetime.fromtimestamp(timestamp, BEIJING).date()


def resolve_activity_period(start_parts, end_parts, submitted_on):
    """Return (start, end, basis) or None if dates are invalid or equally close.

    Each input is (year or None, month, day). A source year anchors the other
    side when needed. An entirely unqualified year is picked by the distance to
    the whole period, including its start and end days.
    """
    if not start_parts or not end_parts:
        return None
    start_year, *start_md = start_parts
    end_year, *end_md = end_parts
    rollover = int(end_md < start_md)

    def dates(first_year, last_year):
        try:
            first = date(first_year, *start_md)
            last = date(last_year, *end_md)
        except ValueError:
            return None
        return (first, last) if first <= last else None

    if start_year is not None or end_year is not None:
        matched = dates(start_year if start_year is not None else end_year - rollover,
                        end_year if end_year is not None else start_year + rollover)
        return (*matched, 'source_year') if matched else None

    candidates = []
    # The largest gap between valid February 29 dates is eight years.
    for year in range(max(1, submitted_on.year - 8), min(9999, submitted_on.year + 8) + 1):
        interval = dates(year, year + rollover)
        if interval:
            first, last = interval
            distance = max((first - submitted_on).days, (submitted_on - last).days, 0)
            candidates.append((distance, first, last))
    candidates.sort()
    if not candidates or len(candidates) > 1 and candidates[0][0] == candidates[1][0]:
        return None
    _, first, last = candidates[0]
    return first, last, 'submission_date'
