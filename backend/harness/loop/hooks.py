"""The extension bus.

Two points in a turn accept listeners. A listener returning a value
short-circuits the waterfall; returning None delegates to the next one.

- `pre_step` runs before each model request and can reject the step, which is
  where a rate limit or a per-user quota would live.
- `pre_execute` guards every tool call. The built-in approval policy is
  registered here as an ordinary listener rather than being called from the
  loop, so a stricter guard or an audit sink is one more `hooks.on(...)` in
  `harness/context.py` instead of an edit to the loop.
"""
import logging
from typing import Any, Callable

logger = logging.getLogger(__name__)

PRE_STEP = "pre_step"
PRE_EXECUTE = "pre_execute"

HOOK_POINTS = (PRE_STEP, PRE_EXECUTE)


class HookBus:
    def __init__(self):
        self._listeners: dict[str, list[tuple[str, Callable]]] = {p: [] for p in HOOK_POINTS}

    def on(self, point: str, name: str, listener: Callable) -> None:
        if point not in self._listeners:
            raise ValueError(f"unknown hook point: {point}")
        self._listeners[point].append((name, listener))

    def emit(self, point: str, *args, **kwargs) -> Any:
        """Run listeners in order; the first non-None answer wins."""
        for name, listener in self._listeners.get(point, []):
            try:
                answer = listener(*args, **kwargs)
            except Exception:
                # A misbehaving listener must not take the turn down with it.
                logger.exception("[harness] hook %s/%s failed", point, name)
                continue
            if answer is not None:
                return answer
        return None

    def describe(self) -> list[dict]:
        """Registered listeners, for the UI's plugin panel."""
        return [
            {"point": point, "name": name}
            for point in HOOK_POINTS
            for name, _ in self._listeners[point]
        ]
