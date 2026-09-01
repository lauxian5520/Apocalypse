"""AI endpoints: streaming chat, summarise, explain, provider list."""
import json

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse

from core.deps import require_user
from core.errors import AppError, ValidationError
from models.user import User
from schemas.ai import ChatIn, ExplainIn, ProviderOut, SummarizeIn
from services import ai_service

router = APIRouter(prefix="/ai", tags=["ai"])

# How much conversation history is forwarded to the provider. Bounds the request
# so a long-lived chat tab cannot grow it until the model rejects it.
MAX_HISTORY_MESSAGES = 20
MAX_SUMMARIZE_CHARS = 8000

SSE_HEADERS = {
    "Cache-Control": "no-cache",
    "X-Accel-Buffering": "no",      # tell nginx not to buffer the stream
}


def _sse(payload: dict) -> str:
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


@router.get("/providers", response_model=list[ProviderOut])
def list_providers(_: User = Depends(require_user)):
    return ai_service.get_available_providers()


@router.post("/chat")
async def chat_stream(body: ChatIn, _: User = Depends(require_user)):
    messages = [m.model_dump() for m in body.messages if m.content.strip()]
    if not messages:
        raise ValidationError("消息不能为空")
    messages = messages[-MAX_HISTORY_MESSAGES:]

    async def event_generator():
        try:
            async for chunk in ai_service.chat_stream(messages):
                yield _sse({"delta": chunk})
            yield "data: [DONE]\n\n"
        except AppError as e:
            # The response has already begun, so errors travel in-band.
            yield _sse({"error": e.message})
        except Exception as e:
            yield _sse({"error": str(e) or e.__class__.__name__})

    return StreamingResponse(event_generator(), media_type="text/event-stream", headers=SSE_HEADERS)


@router.post("/summarize")
async def summarize(body: SummarizeIn, _: User = Depends(require_user)):
    if not body.text.strip():
        raise ValidationError("内容不能为空")
    return {"summary": await ai_service.summarize(body.text[:MAX_SUMMARIZE_CHARS], body.context)}


@router.post("/explain")
async def explain(body: ExplainIn, _: User = Depends(require_user)):
    if not body.content.strip() and not body.image_urls:
        raise ValidationError("内容不能为空")
    return {"explanation": await ai_service.explain_memo(body.content, body.image_urls or None)}
