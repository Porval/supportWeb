"""Attach read-only Agent supply quotes after activity/source authorization."""
from datetime import datetime, timezone
from pathlib import Path
import threading


class AgentSupplyPrices:
    def __init__(self, source, load_export, lookup_supply_prices, module_sha):
        self.source = Path(source)
        self.load_export = load_export
        self.lookup = lookup_supply_prices
        self.module_sha = module_sha
        self.lock = threading.Lock()
        self.snapshot = None
        self.snapshot_key = None
        self.loaded_at = None

    def _read(self, recognition):
        catalog = recognition['catalog']
        stat = self.source.stat()
        if not self.source.is_file() or self.source.is_symlink():
            raise ValueError('supply source unavailable')
        key = (stat.st_mtime_ns, stat.st_size, catalog['sourceSha256'], catalog['sourceDate'])
        with self.lock:
            if key != self.snapshot_key:
                snapshot = self.load_export(self.source, snapshot_date=catalog['sourceDate'],
                                            expected_sha256=catalog['sourceSha256'])
                self.snapshot = snapshot
                self.snapshot_key = key
                self.loaded_at = datetime.now(timezone.utc).isoformat()
            return self.snapshot, self.loaded_at

    def enrich(self, recognition):
        if recognition.get('status') != 'ready':
            return recognition
        # The price snapshot is not part of identity binding or draft revision.
        # Price changes cannot silently confirm products or invalidate selections.
        try:
            snapshot, fetched_at = self._read(recognition)
            output = self.lookup(recognition, snapshot)
            expected = {k: recognition['binding'][k] for k in ('activityId','versionId','inputDigest','catalogExportSha256')}
            if any(output.get('binding', {}).get(k) != v for k, v in expected.items()):
                raise ValueError('supply recognition binding mismatch')
            quotes = {(row['candidateId'], row['rowId']): row for row in output['rows']}
            if len(quotes) != len(output['rows']):
                raise ValueError('duplicate supply result')
            visible = {(r['candidateId'], r['rowId']) for r in recognition['rows']}
            if not set(quotes) <= visible:
                raise ValueError('supply rows outside authorized recognition')
            metadata = {k: v for k, v in output.items() if k != 'rows'}
            metadata.update(moduleSha256=self.module_sha, fetchedAt=fetched_at)
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            quotes = {}
            metadata = {'status': 'unavailable', 'reasonCode': 'SUPPLY_SOURCE_UNAVAILABLE',
                        'moduleSha256': self.module_sha}
        rows = [row | {'agentSupplyPrices': quotes.get((row['candidateId'], row['rowId']),
                        {'status': 'unavailable' if metadata.get('status') == 'unavailable' else 'missing',
                         'storeSupplyPrice': None, 'referenceQuote': None, 'issues': ['SUPPLY_SOURCE_UNAVAILABLE']})}
                for row in recognition['rows']]
        return recognition | {'rows': rows, 'supplyPriceSource': metadata}
