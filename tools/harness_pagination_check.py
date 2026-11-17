"""Regression checks for cursor pagination and cross-page approvals."""
import json
import os
import sys
import tempfile
import types
from pathlib import Path

from sqlalchemy import create_engine, insert
from sqlalchemy.orm import sessionmaker

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend'))


def main() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        os.environ.update(APP_ENV='test', DB_TYPE='sqlite', VAR_DIR=tmp, JWT_SECRET='test-secret')
        # Import just the store; the package initializer loads Unix-only sandbox code.
        package = types.ModuleType('harness')
        package.__path__ = [str(ROOT / 'backend' / 'harness')]
        sys.modules['harness'] = package
        session_package = types.ModuleType('harness.session')
        session_package.__path__ = [str(ROOT / 'backend' / 'harness' / 'session')]
        sys.modules['harness.session'] = session_package
        from core.database import Base
        from models.harness import HarnessEvent, HarnessSession
        from harness.session.sqlite_store import SqliteSessionStore
        from harness.session import manager

        engine = create_engine('sqlite://')
        Base.metadata.create_all(engine)
        factory = sessionmaker(bind=engine)
        rows = []
        for seq in range(1000):
            kind = 'user/message' if seq % 2 == 0 else 'assistant/chunk'
            data = {'content': str(seq)} if seq % 2 == 0 else {'delta': 'x'}
            if seq == 20:
                kind, data = 'tool/approval', {'tool_call_id': 'call_1'}
            if seq == 900:
                kind, data = 'tool/result', {'tool_call_id': 'call_1'}
            rows.append({'session_id': 'one', 'seq': seq, 'type': kind, 'time': seq, 'data': json.dumps(data)})
        rows.append({'session_id': 'other', 'seq': 1, 'type': 'user/message', 'time': 1, 'data': '{}'})
        rows.extend([
            {'session_id': 'one', 'seq': 1000, 'type': 'llm/usage', 'time': 1000,
             'data': json.dumps({'model': 'fixture', 'prompt_tokens': 10, 'completion_tokens': 2})},
            {'session_id': 'child', 'seq': 0, 'type': 'llm/usage', 'time': 0,
             'data': json.dumps({'model': 'fixture', 'prompt_tokens': 5, 'completion_tokens': 3})},
        ])
        with factory.begin() as db:
            db.execute(insert(HarnessEvent), rows)
            db.execute(insert(HarnessSession), [
                {'id': 'one', 'user_id': 1, 'title': '', 'preset': 'standard', 'status': 'idle'},
                {'id': 'child', 'parent_id': 'one', 'user_id': 1, 'title': '', 'preset': 'standard', 'status': 'idle'},
            ])
        store = SqliteSessionStore(factory)
        newest, more = store.read_page('one', 100, before_seq=1000)
        assert more and [e.seq for e in newest] == list(range(900, 1000))
        previous, more = store.read_page('one', 100, before_seq=900)
        assert more and [e.seq for e in previous] == list(range(800, 900))
        forward, more = store.read_page('one', 100, after_seq=900)
        assert not more and [e.seq for e in forward] == list(range(901, 1001))
        page, more = store.read_page('one', 100, before_seq=100, conversation=True)
        approval = next(e for e in page if e.type == 'tool/approval')
        assert approval.data['settled'] is True
        assert not more and all(e.type != 'assistant/chunk' for e in page)
        assert not store.read_page('missing', 100)[0]
        manager.SessionLocal = factory
        usage = manager.usage_summary('one')
        assert (usage['requests'], usage['prompt_tokens'], usage['completion_tokens']) == (2, 15, 5)
        print('PASS: pagination, cross-page approval and parent/child usage')


if __name__ == '__main__':
    main()
