"""Content-free diagnostics returned through the existing worker error envelope."""
from business_agent.workbench_deepseek import adapter


class RecognitionError(adapter.AdapterError):
    def __init__(self, code, reason, **details):
        super().__init__(code)
        self.details = {"reason": reason, **details}


def require(condition, reason, *, code=adapter.INVALID_OUTPUT, **details):
    if not condition:
        raise RecognitionError(code, reason, **details)
