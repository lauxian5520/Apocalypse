"""Shell tool handler. Registration is gated by HARNESS_SHELL_ENABLED."""
from harness.tools.base import ToolContext


async def bash(ctx: ToolContext, command: str, timeout: int = 0) -> str:
    result = await ctx.sandbox.run(command, timeout=timeout)
    return result.render()


HANDLERS = {"bash": bash}
