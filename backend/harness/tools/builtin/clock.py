"""Clock tool.

The agent needs the time far more often than it needs anything from the
network, and asking a public time API for it is both slow and unreliable — a
probe run burned three failed `web_fetch` calls before this existed. The
system prompt carries only the date (a second-precision timestamp there would
invalidate the provider's prefix cache on every request), so exact time is a
tool call instead.
"""
from datetime import datetime, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from core.errors import ValidationError
from harness.tools.base import ToolContext


async def current_time(ctx: ToolContext, timezone: str = "") -> str:
    now = datetime.now().astimezone()

    if timezone:
        try:
            now = now.astimezone(ZoneInfo(timezone))
        except (ZoneInfoNotFoundError, ValueError):
            raise ValidationError(f"未知的时区名：{timezone}（需要 IANA 格式，如 Asia/Shanghai）")

    utc = now.astimezone(_utc)
    return (
        f"{now:%Y-%m-%d %H:%M:%S} {now:%Z}（UTC{now:%z}），星期{_WEEKDAYS[now.weekday()]}\n"
        f"UTC 时间：{utc:%Y-%m-%d %H:%M:%S}"
    )


_utc = timezone.utc
_WEEKDAYS = "一二三四五六日"

HANDLERS = {"current_time": current_time}
