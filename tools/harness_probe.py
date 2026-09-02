"""Drive one full harness turn with no browser and no server.

    cd backend && python ../tools/harness_probe.py --prompt "在工作区建一个 hello.txt"

Prints the session's event log as it is produced, then the exact messages the
next request would carry. Uses the real configured provider, so it costs a few
tokens; pass --keep to leave the session and its workspace behind for
inspection.
"""
import argparse
import asyncio
import sys
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

from core.database import Base, SessionLocal, engine  # noqa: E402
from core.providers import provider_config  # noqa: E402
import models  # noqa: E402,F401  (registers the mappers)
from harness import build_context, events as ev, run_turn  # noqa: E402
from harness.session import manager  # noqa: E402
from harness.session.projection import derive_messages, estimate_tokens  # noqa: E402
from harness.session.sqlite_store import SqliteSessionStore  # noqa: E402
from models.user import User  # noqa: E402

DEFAULT_PROMPT = "在工作区新建 hello.txt，写入当前时间，然后读回来确认内容。"

# Only these carry text worth printing inline; the rest are shown by type alone.
_DETAIL = {
    ev.USER_MESSAGE: lambda d: d.get("content", ""),
    ev.ASSISTANT_MESSAGE: lambda d: (d.get("content") or "")
    or f"<{len(d.get('tool_calls') or [])} 个工具调用>",
    ev.TOOL_CALL: lambda d: f"{d['name']} {d['arguments'][:100]}",
    ev.TOOL_RESULT: lambda d: f"{d['name']} -> {str(d['content'])[:100]!r}",
    ev.TOOL_APPROVAL: lambda d: f"{d['name']} :: {d['reason']}",
    ev.AGENT_ERROR: lambda d: d.get("message", ""),
    ev.LLM_USAGE: lambda d: f"in={d['prompt_tokens']} out={d['completion_tokens']} cached={d['cached_tokens']}",
    ev.COMPACTION_SUMMARY: lambda d: f"covers<={d['covers_to_seq']}",
}


def _probe_user() -> int:
    """A dedicated account, so probing never writes into a real user's history."""
    with SessionLocal() as db:
        user = db.query(User).filter(User.username == "__harness_probe__").first()
        if user is None:
            user = User(
                username="__harness_probe__",
                email="probe@localhost",
                password_hash="!",          # unusable: this account is never logged into
                role="user",
                is_disabled=True,
            )
            db.add(user)
            db.commit()
            db.refresh(user)
        return user.id


async def main() -> int:
    parser = argparse.ArgumentParser(description="Run one harness turn end to end.")
    parser.add_argument("--prompt", default=DEFAULT_PROMPT)
    parser.add_argument("--preset", default="standard")
    parser.add_argument("--keep", action="store_true", help="保留会话与工作区")
    args = parser.parse_args()

    cfg = provider_config()
    print(f"provider = {cfg['model']} @ {cfg['url']}")
    print(f"api key  = {'set' if cfg['key'] else 'MISSING'}")

    Base.metadata.create_all(bind=engine)
    session = manager.create(_probe_user(), args.preset, title="probe")
    print(f"session  = {session.id}  preset={args.preset}")

    hctx = build_context(session.id, args.preset)
    print(f"tools    = {[t['name'] for t in hctx.tools.describe()]}")
    print(f"workspace= {hctx.workspace.root}\n")

    approval_pending = False
    try:
        async for event in run_turn(hctx, args.prompt):
            detail = _DETAIL.get(event.type, lambda _: "")(event.data)
            print(f"  {event.seq:>3}  {event.type:<22} {detail}")
            if event.type == ev.TOOL_APPROVAL:
                approval_pending = True
    except Exception:
        print("\nTURN FAILED")
        traceback.print_exc()
        return 1

    log = SqliteSessionStore().read(session.id)
    messages = derive_messages(log, hctx.system_prompt)
    print(f"\n投影出的消息（{len(messages)} 条，约 {estimate_tokens(messages)} tokens）：")
    for message in messages:
        calls = len(message.get("tool_calls") or [])
        body = str(message.get("content") or "")[:80].replace("\n", " ")
        print(f"  {message['role']:<10} {body!r}{f'  +{calls} calls' if calls else ''}")

    print(f"\n用量：{manager.usage_summary(session.id)}")
    print(f"工作区文件：{sorted(p.name for p in Path(hctx.workspace.root).rglob('*') if p.is_file())}")
    if approval_pending:
        print("\n本轮停在人工审批处 —— 这是策略生效，不是失败。")

    if args.keep:
        print(f"\n已保留会话 {session.id}")
    else:
        manager.delete(session.id, session.user_id)
        print("\n已清理会话与工作区（加 --keep 可保留）")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
