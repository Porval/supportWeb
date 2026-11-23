"""Read-only, source-bound Agent sales receipts; no inferred zeros or net sales."""
from datetime import date, datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path

BEIJING = timezone(timedelta(hours=8))
RULE_VERSION = 'agent-sales-28d/1.0'


def aggregate_days(days, start, end):
    """Return a total only for 28 unique, complete, valid daily source values."""
    expected = {(start + timedelta(days=n)).isoformat() for n in range(28)}
    if end != start + timedelta(days=27) or len(days) != 28:
        raise ValueError('Invalid sales window')
    if {day['date'] for day in days} != expected:
        raise ValueError('Duplicate, missing or out-of-window dates')
    values = []
    for day in days:
        if (day['status'] != 'one_record' or day['expectedRows'] != 1 or
                day['receivedRows'] != 1 or day['pages'] != 1 or
                day['platformDayStatus'] != 'available'):
            raise ValueError('Incomplete or ambiguous daily source')
        value = day['salesQty']
        if value is None and day['salesQtyState'] == 'null':
            continue
        if type(value) is not int or value < 0 or day['salesQtyState'] != 'recorded':
            raise ValueError('Invalid daily sales quantity')
        values.append(value)
    return (sum(values) if len(values) == 28 else None), len(values)


class AgentProductMetrics:
    def __init__(self, source, binding_source, clock):
        self.clock = clock
        self.receipt = None
        try:
            source = Path(source)
            if source.is_symlink() or source.stat().st_size > 2_000_000:
                raise ValueError('Invalid metrics source')
            self.binding = json.loads(Path(binding_source).read_text())
            raw = source.read_bytes()
            self.sha = hashlib.sha256(raw).hexdigest()
            if self.sha != self.binding['receiptSha256']:
                raise ValueError('Metrics source checksum mismatch')
            receipt = json.loads(raw)
            origin = receipt['recognitionInput']
            query = receipt['salesSource']
            if (receipt['schemaVersion'] != 'jt024-meituan-candidate-operating-data/1.0' or
                    receipt['platform'] != '美团' or self.binding['platformCode'] != 'meituan' or
                    origin['sha256'] != self.binding['recognitionSha256'] or
                    any(origin[k] != self.binding['recognitionBinding'][k] for k in ('activityId', 'versionId')) or
                    query['method'] != 'GET' or query['field'] != 'sales_qty' or
                    query['timezone'] != 'Asia/Shanghai' or
                    query['grain'] != ['date', 'platform', 'item_code'] or
                    query['requestParameters']['platform'] != '美团'):
                raise ValueError('Metrics scope mismatch')
            self.start = date.fromisoformat(query['startDate'])
            self.end = date.fromisoformat(query['endDate'])
            self.sales = {}
            for row in receipt['rows']:
                if row['rowId'] in self.sales:
                    raise ValueError('Duplicate source row')
                total, value_days = aggregate_days(row['days'], self.start, self.end)
                if row['salesQty28d'] != total or row['salesRecordedDays'] != value_days:
                    raise ValueError('Agent total disagrees with daily evidence')
                self.sales[row['rowId']] = (row, total, value_days)
            self.receipt = receipt
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            # Preserve the activity even when the independently supplied data fails.
            self.receipt = None

    def enrich(self, recognition, platform_codes):
        if recognition.get('status') != 'ready':
            return recognition
        metadata = {'status': 'unavailable', 'reasonCode': 'METRICS_SOURCE_UNAVAILABLE',
                    'ruleVersion': RULE_VERSION}
        matched = {}
        if self.receipt:
            query = self.receipt['salesSource']
            metadata.update(sourceSha256=self.sha, provider='company_data_agent',
                            field='sales_qty', platformCode='meituan',
                            startDate=self.start.isoformat(), endDate=self.end.isoformat(),
                            fetchedAt=query['finishedAt'], sourceUpdatedAt=None,
                            timezone='Asia/Shanghai', refundTreatmentVerified=False)
            if recognition.get('binding') != self.binding['recognitionBinding'] or platform_codes != ['meituan']:
                metadata['reasonCode'] = 'METRICS_BINDING_MISMATCH'
            elif self.end != datetime.fromtimestamp(self.clock(), BEIJING).date() - timedelta(days=1):
                metadata.update(status='stale', reasonCode='METRICS_WINDOW_STALE')
            else:
                metadata.update(status='ready', reasonCode=None)
                for row in recognition['rows']:
                    entry = self.sales.get(row['rowId'])
                    candidates = row.get('candidates') or []
                    if not entry or row.get('uniqueUpcHit') is not True or len(candidates) != 1:
                        continue
                    evidence, total, count = entry
                    candidate = candidates[0]
                    if (candidate.get('internalCode') != evidence['itemCode'] or
                            candidate.get('spec') != evidence['spec'] or row.get('rawUpc') != evidence['upc']):
                        continue
                    matched[(row['candidateId'], row['rowId'])] = {
                        'status': 'ready' if total is not None else 'partial',
                        'quantity': str(total) if total is not None else None,
                        'unit': '件', 'startDate': self.start.isoformat(), 'endDate': self.end.isoformat(),
                        'platformCode': 'meituan', 'sourceSha256': self.sha, 'ruleVersion': RULE_VERSION,
                        'recordedDays': count, 'expectedDays': 28, 'fetchedAt': query['finishedAt'],
                        'refundTreatmentVerified': False,
                        'sourceSummary': '美团 · 数据Agent商品日报 sales_qty 原值按日加总；退款扣减口径待核实，未自行扣减退款。单位按目录商品规格计；商品身份仍待人工核对。取数于 ' + query['finishedAt'],
                    }
        fallback = {'status': metadata['status'] if metadata['status'] != 'ready' else 'unavailable',
                    'quantity': None, 'reasonCode': metadata.get('reasonCode') or 'METRICS_IDENTITY_UNAVAILABLE'}
        rows = [row | {'operatingMetrics': {
                    **row.get('operatingMetrics', {}),
                    'sales28d': matched.get((row['candidateId'], row['rowId']), fallback),
                    'activeStores': {'status': 'unavailable', 'count': None,
                                     'reasonCode': 'CURRENT_PLATFORM_STORE_SKU_SOURCE_UNAVAILABLE'},
                }} for row in recognition['rows']]
        return recognition | {'rows': rows, 'operatingMetricsSource': metadata}
