"""Retired R2 manual-name bypass compatibility surface.

The 2026-11-12 user clarification requires Agent decisions before confirmation.
Old clients receive an explicit refusal; existing receipts remain readable.
Business interpretation stays in the Agent path; the backend never constructs
an alternative decision from material names here.
"""
from account_primitives import fail

CONTRACT = 'manual-name-confirmation/1.0'
REASON = 'AGENT_DECISION_REQUIRED'
MESSAGE = '请先由业务 Agent 识别，再核对返回结果；不能跳过 Agent 直接建档'


def project(domain, db, actor, view, sub):
    view['manualNameConfirmation'] = {
        'contractVersion': CONTRACT, 'eligible': False, 'candidateId': None,
        'existingActivity': None, 'reasonCode': REASON, 'message': MESSAGE,
    }


def prepare(domain, db, actor, sid, body):
    fail(409, REASON, MESSAGE)
