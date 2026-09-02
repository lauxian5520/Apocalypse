"""Server-sent-event framing.

One definition of the wire format, because the browser side parses it by hand.
`frontend/js/widgets/sprite-chat.js` slices a fixed 6 characters off each line,
so the `data: ` prefix must keep its space — do not "tidy" it away.
"""
import json

SSE_HEADERS = {
    "Cache-Control": "no-cache",
    "X-Accel-Buffering": "no",      # tell nginx not to buffer the stream
}

SSE_DONE = "data: [DONE]\n\n"


def sse(payload: dict) -> str:
    """Frame one JSON payload as an SSE `data:` line."""
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"
