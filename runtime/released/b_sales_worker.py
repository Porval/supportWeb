"""Call the local Activity Agent for sales without loading its online configuration."""
import threading
import time

from b_model_recognition import ProviderFailure
from b_model_worker import INPUT_LIMIT, ModelWorker
from b_state import canon

SETTINGS_ENV = 'JIATAN_ACTIVITY_SALES_SETTINGS'
HISTORY = 'activity-sales-history/1.0'
COMPARISON = 'activity-sales-comparison/1.0'


class SalesWorker(ModelWorker):
    contract = 'activity-sales-worker/1.0'
    prefix = 'ACTIVITY_SALES_'
    module = 'business_agent.activity_sales_comparison.worker'
    settings_env = SETTINGS_ENV
    output_limit = 16 * 1024 * 1024

    def import_root(self):
        # The release launcher verifies the pinned hashes of the Agent package.
        return self.directory.parent.parent

    def query(self, operation, request, today):
        identity = {'platformCode': request['platformCode']}
        if operation == 'history':
            identity.update(schemaVersion=HISTORY, window=request['window'])
            row_key = 'internalCode'
            empty_periods = []
        else:
            identity.update(schemaVersion=COMPARISON, activityId=request['activityId'],
                            activityVersionId=request['activityVersionId'], dateBasis=request['dateBasis'])
            row_key = 'entityId'
            empty_periods = {'current': None, 'previous': None}
        try:
            payload = canon({'contractVersion': self.contract, 'operation': operation,
                             'request': request, 'today': today.isoformat()}).encode()
            if len(payload) > INPUT_LIMIT:
                raise ProviderFailure('INPUT_INVALID')
            result = self.invoke([], payload, threading.Event(), time.monotonic() + 10)
            self.validate_result(result, identity, request['scope']['internalCodes'], row_key, empty_periods)
            return result
        except ProviderFailure as error:
            if error.reason == 'TIMEOUT':
                code, message = 'ACTIVITY_AGENT_TIMEOUT', '本地活动 Agent 查询超时。'
            else:
                code, message = 'ACTIVITY_AGENT_UNAVAILABLE', '本地活动 Agent 暂时无法返回可核对的查询结果。'
            return identity | {'status': 'source_unavailable', 'periods': empty_periods,
                               'rows': [], 'source': None, 'warnings': [{'code': code, 'message': message}]}

    @staticmethod
    def validate_result(result, identity, codes, row_key, empty_periods):
        if (any(result.get(key) != value for key, value in identity.items())
                or not isinstance(result.get('periods'), type(empty_periods))
                or not isinstance(result.get('rows'), list)
                or not isinstance(result.get('warnings'), list)
                or not isinstance(result.get('status'), str)):
            raise ProviderFailure('INVALID_OUTPUT')
        allowed = set(codes)
        seen = set()
        for row in result['rows']:
            code = row.get(row_key) if isinstance(row, dict) else None
            if (not isinstance(code, str) or code not in allowed or code in seen
                    or row_key == 'entityId' and row.get('entityType') != 'product'):
                raise ProviderFailure('INVALID_OUTPUT')
            seen.add(code)
