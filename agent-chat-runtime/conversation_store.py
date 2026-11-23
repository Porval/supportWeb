"""Actor-scoped persistent conversations in the existing local MySQL database."""

from datetime import datetime, timezone
import json
import re
from uuid import uuid4

from memory import ContextUnavailable, literal

CONVERSATION_ID = re.compile(r'[0-9a-f]{32}')
MAX_STATE_BYTES = 24 * 1024 * 1024
MAX_TITLE_CHARS = 180
DEFAULT_STATE = {'turns': [], 'attachments': [], 'confirmations': [], 'snapshot': None}


class ConversationUnavailable(RuntimeError):
    pass


class ConversationNotFound(RuntimeError):
    pass


class ConversationConflict(RuntimeError):
    pass


def actor_key(actor):
    if isinstance(actor, bool) or not isinstance(actor, (str, int)):
        raise ValueError('Invalid conversation actor')
    value = str(actor)
    if not 1 <= len(value) <= 200 or not value.isprintable():
        raise ValueError('Invalid conversation actor')
    return value


def conversation_key(value):
    if not isinstance(value, str) or not CONVERSATION_ID.fullmatch(value):
        raise ValueError('Invalid conversation identifier')
    return value


def encode_state(state):
    if not isinstance(state, dict):
        raise ValueError('Invalid conversation state')
    try:
        encoded = json.dumps(state, ensure_ascii=False, separators=(',', ':'), allow_nan=False)
    except (ValueError, TypeError):
        raise ValueError('Conversation state must be JSON') from None
    if len(encoded.encode('utf-8')) > MAX_STATE_BYTES:
        raise ConversationUnavailable('Conversation state exceeds local storage budget')
    return encoded


def title_value(title):
    if not isinstance(title, str) or not title.strip() or len(title.strip()) > MAX_TITLE_CHARS:
        raise ValueError('Invalid conversation title')
    return title.strip()


def _selection(include_state=True):
    parts = "'id', conversation_id, 'title', title, 'revision', revision, " \
            "'createdEpoch', UNIX_TIMESTAMP(created_at), 'updatedEpoch', UNIX_TIMESTAMP(updated_at)"
    if include_state:
        parts += ", 'state', CAST(state_json AS JSON)"
    return 'JSON_OBJECT(' + parts + ')'


def _decode(line, include_state=True):
    try:
        value = json.loads(line)
        if not isinstance(value, dict):
            raise ValueError
        conversation_key(value['id'])
        title_value(value['title'])
        if type(value['revision']) is not int or value['revision'] < 0:
            raise ValueError
        if include_state and not isinstance(value['state'], dict):
            raise ValueError
        for field in ('created', 'updated'):
            epoch = value.pop(field + 'Epoch')
            value[field + 'At'] = datetime.fromtimestamp(float(epoch), timezone.utc).isoformat().replace('+00:00', 'Z')
        return value
    except (ValueError, TypeError, KeyError, OverflowError):
        raise ConversationUnavailable('Stored conversation is not readable') from None


class ConversationStore:
    """The executor is the existing CaseMemory client with table-specific grants."""

    def __init__(self, executor):
        self.executor = executor

    def _execute(self, sql):
        try:
            result = self.executor.execute(sql)
            if not isinstance(result, str):
                raise ConversationUnavailable('Conversation storage returned invalid data')
            return result
        except ContextUnavailable:
            raise ConversationUnavailable('Conversation storage is unavailable') from None

    def create(self, actor, title='新对话', state=None):
        actor = actor_key(actor)
        title = title_value(title)
        cid = uuid4().hex
        state = encode_state(DEFAULT_STATE if state is None else state)
        result = self._execute(f"""/* conversation:create */
            START TRANSACTION;
            INSERT INTO chat_conversation (conversation_id, actor_id, title, state_json)
                VALUES ({literal(cid)}, {literal(actor)}, {literal(title)}, {literal(state)});
            SELECT {_selection()} FROM chat_conversation
                WHERE actor_id = {literal(actor)} AND conversation_id = {literal(cid)};
            COMMIT;
        """)
        lines = result.strip().splitlines()
        if len(lines) != 1:
            raise ConversationUnavailable('Conversation was not saved')
        return _decode(lines[0])

    def load(self, actor, cid):
        actor, cid = actor_key(actor), conversation_key(cid)
        result = self._execute(f"""/* conversation:load */
            SELECT {_selection()} FROM chat_conversation
                WHERE actor_id = {literal(actor)} AND conversation_id = {literal(cid)};
        """)
        lines = result.strip().splitlines()
        if not lines:
            raise ConversationNotFound('Conversation is not available for this account')
        if len(lines) != 1:
            raise ConversationUnavailable('Stored conversation is not readable')
        return _decode(lines[0])

    def list(self, actor, limit=50, offset=0):
        actor = actor_key(actor)
        if type(limit) is not int or not 1 <= limit <= 100 or type(offset) is not int or offset < 0:
            raise ValueError('Invalid conversation list page')
        result = self._execute(f"""/* conversation:list */
            SELECT {_selection(False)} FROM chat_conversation
                WHERE actor_id = {literal(actor)}
                ORDER BY updated_at DESC, conversation_id DESC LIMIT {limit} OFFSET {offset};
        """)
        return [_decode(line, False) for line in result.strip().splitlines()]

    def save(self, actor, cid, expected_revision, state, title=None):
        actor, cid = actor_key(actor), conversation_key(cid)
        if type(expected_revision) is not int or not 0 <= expected_revision < 2**64 - 1:
            raise ValueError('Invalid conversation revision')
        encoded = encode_state(state)
        title_update = '' if title is None else ', title = ' + literal(title_value(title))
        # The row remains locked until its post-update value has been selected.
        result = self._execute(f"""/* conversation:save */
            START TRANSACTION;
            UPDATE chat_conversation
                SET state_json = {literal(encoded)}, revision = revision + 1,
                    updated_at = CURRENT_TIMESTAMP(6){title_update}
                WHERE actor_id = {literal(actor)} AND conversation_id = {literal(cid)}
                  AND revision = {expected_revision};
            SELECT ROW_COUNT();
            SELECT {_selection()} FROM chat_conversation
                WHERE actor_id = {literal(actor)} AND conversation_id = {literal(cid)};
            COMMIT;
        """)
        lines = result.strip().splitlines()
        if not lines or lines[0] not in ('0', '1'):
            raise ConversationUnavailable('Conversation save could not be verified')
        if len(lines) == 1:
            raise ConversationNotFound('Conversation is not available for this account')
        if len(lines) != 2:
            raise ConversationUnavailable('Conversation save could not be verified')
        if lines[0] == '0':
            raise ConversationConflict('Conversation changed; reload before continuing')
        record = _decode(lines[1])
        if record['revision'] != expected_revision + 1:
            raise ConversationUnavailable('Conversation save could not be verified')
        return record
