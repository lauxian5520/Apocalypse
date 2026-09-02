"""Harness — an agent workbench with a fully replayable session log.

Two ideas, borrowed from deepseek-ai/deepseek-harness and rebuilt to fit this
codebase rather than ported from it:

1. **Replaceable seams.** The model adapter, tool registry, session store and
   sandbox are each a protocol plus a default implementation, chosen in
   `harness/context.py`. Nothing else in the subsystem knows which one it got.
2. **Total traceability.** Everything the model sees is written to an
   append-only log first. `harness.session.projection.derive_messages` is the
   only function permitted to turn that log into a request, which makes the
   claim checkable instead of aspirational.

Layering matches `services/`: no `fastapi` import lives below this package, and
failures are `core.errors` domain exceptions that `main.py` maps to statuses.
"""
from harness.context import HarnessContext, build_context
from harness.events import SessionEvent
from harness.loop.agent import resume_turn, run_turn

__all__ = ["HarnessContext", "build_context", "SessionEvent", "run_turn", "resume_turn"]
