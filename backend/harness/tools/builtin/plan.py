"""Planning tool handlers.

Neither keeps server-side state: the todo list and the plan live in the session
log as tool results, which is also where the model reads them back from. One
source of truth, and the trajectory view gets them for free.
"""
from core.errors import ValidationError
from harness.tools.base import ToolContext

STATUS_MARKS = {"pending": "☐", "in_progress": "▶", "completed": "☑"}


async def todo_write(ctx: ToolContext, todos: list) -> str:
    if not isinstance(todos, list) or not todos:
        raise ValidationError("任务清单不能为空")

    lines = []
    for item in todos:
        if not isinstance(item, dict):
            raise ValidationError("任务项必须是对象，包含 content 与 status")
        status = item.get("status", "pending")
        if status not in STATUS_MARKS:
            raise ValidationError(f"未知的任务状态：{status}")
        lines.append(f"{STATUS_MARKS[status]} {item.get('content', '')}")

    done = sum(1 for i in todos if i.get("status") == "completed")
    return "任务清单已更新（{}/{} 完成）：\n{}".format(done, len(todos), "\n".join(lines))


async def exit_plan_mode(ctx: ToolContext, plan: str) -> str:
    if not plan.strip():
        raise ValidationError("方案内容不能为空")
    return "已把方案提交给用户，等待确认。"


HANDLERS = {"todo_write": todo_write, "exit_plan_mode": exit_plan_mode}
