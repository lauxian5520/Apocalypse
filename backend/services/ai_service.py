"""Unified client for the configured LLM provider.

Supports DeepSeek, Zhipu (GLM), Gemini, OpenAI-compatible endpoints, Ollama and
any custom URL. Everything except Ollama speaks the OpenAI
`/chat/completions` shape, streaming or not.

Failures are raised as `core.errors` types so this module stays free of HTTP
concerns; `main.py` maps them onto status codes.
"""
import json
from typing import AsyncIterator

import httpx

from core.config import get_settings
from core.errors import UpstreamError
from core.providers import auth_headers, provider_config, require_configured

settings = get_settings()

REQUEST_TIMEOUT_SECONDS = 60
STREAM_TIMEOUT_SECONDS = 120
MAX_TOKENS = 2048
TEMPERATURE = 0.7


def active_model() -> str:
    """Model name of the currently selected provider (used as a cache key)."""
    return provider_config().get("model", "")


def _error_text(exc: Exception) -> str:
    """Best-effort human-readable reason from an httpx failure.

    For HTTP status errors the provider's own JSON body ("invalid api key",
    "insufficient balance", ...) is far more useful than the generic message,
    so read it when it is available.
    """
    resp = getattr(exc, "response", None)
    if resp is not None:
        try:
            body = resp.text
        except Exception:
            body = ""
        if body:
            return f"HTTP {resp.status_code}: {body[:500]}"
        return f"HTTP {resp.status_code}"
    return str(exc) or exc.__class__.__name__


def _build_openai_payload(messages: list, model: str, stream: bool = False) -> dict:
    return {
        "model": model,
        "messages": messages,
        "stream": stream,
        "temperature": TEMPERATURE,
        "max_tokens": MAX_TOKENS,
    }


def _build_ollama_payload(messages: list, model: str, stream: bool = False) -> dict:
    return {
        "model": model,
        "messages": messages,
        "stream": stream,
    }


async def chat(messages: list[dict]) -> str:
    """Non-streaming chat — returns full response text."""
    cfg = provider_config()
    require_configured(cfg)

    headers = auth_headers(cfg)

    is_ollama = cfg["format"] == "ollama"
    payload = (
        _build_ollama_payload(messages, cfg["model"], stream=False)
        if is_ollama
        else _build_openai_payload(messages, cfg["model"], stream=False)
    )

    # NOTE: the client is created *inside* the try — building it can also fail
    # (e.g. a broken proxy env var), and that used to escape as a raw 500.
    try:
        async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT_SECONDS) as client:
            resp = await client.post(cfg["url"], headers=headers, json=payload)
            resp.raise_for_status()
            data = resp.json()
        if is_ollama:
            return data["message"]["content"]
        return data["choices"][0]["message"]["content"]
    except UpstreamError:
        raise
    except (KeyError, IndexError, TypeError, ValueError) as e:
        raise UpstreamError(f"AI 返回格式无法解析 ({cfg['url']}): {e}")
    except Exception as e:
        label = "Ollama API Error" if is_ollama else "AI API Error"
        raise UpstreamError(f"{label} ({cfg['url']}): {_error_text(e)}")


async def _raise_for_stream_status(resp: httpx.Response, url: str) -> None:
    """raise_for_status() on a streamed response hides the provider's body.

    Read it explicitly first so the user sees "invalid api key" instead of a
    bare "Client error '401 Unauthorized'".
    """
    if resp.status_code < 400:
        return
    try:
        body = (await resp.aread()).decode("utf-8", "replace")
    except Exception:
        body = ""
    detail = f"HTTP {resp.status_code}"
    if body:
        detail += f": {body[:500]}"
    raise UpstreamError(f"AI API Error ({url}): {detail}")


async def chat_stream(messages: list[dict]) -> AsyncIterator[str]:
    """Streaming chat — yields text chunks for SSE."""
    cfg = provider_config()
    require_configured(cfg)

    headers = auth_headers(cfg)

    is_ollama = cfg["format"] == "ollama"
    payload = (
        _build_ollama_payload(messages, cfg["model"], stream=True)
        if is_ollama
        else _build_openai_payload(messages, cfg["model"], stream=True)
    )

    try:
        async with httpx.AsyncClient(timeout=STREAM_TIMEOUT_SECONDS) as client:
            async with client.stream("POST", cfg["url"], headers=headers, json=payload) as resp:
                await _raise_for_stream_status(resp, cfg["url"])
                async for line in resp.aiter_lines():
                    line = line.strip()
                    if not line:
                        continue

                    if is_ollama:
                        try:
                            data = json.loads(line)
                        except json.JSONDecodeError:
                            continue
                        content = (data.get("message") or {}).get("content", "")
                        if content:
                            yield content
                        continue

                    # OpenAI-compatible SSE. Some providers omit the space
                    # after "data:", so don't hard-code "data: ".
                    if line.startswith(":"):  # SSE keep-alive comment
                        continue
                    if line.startswith("data:"):
                        line = line[5:].lstrip()
                    if not line or line == "[DONE]":
                        continue
                    try:
                        data = json.loads(line)
                        choices = data.get("choices") or []
                        if not choices:
                            continue
                        delta = (choices[0].get("delta") or {}).get("content") or ""
                        if delta:
                            yield delta
                    except (json.JSONDecodeError, AttributeError, KeyError, IndexError, TypeError):
                        continue
    except UpstreamError:
        raise
    except Exception as e:
        label = "Ollama API Error" if is_ollama else "AI API Error"
        raise UpstreamError(f"{label} ({cfg['url']}): {_error_text(e)}")


async def summarize(text: str, context: str = "content") -> str:
    """Summarize text (news, paper, project)."""
    system = (
        "You are a helpful assistant. Summarize the following content concisely in Chinese, "
        "highlighting the most important points in 3-5 sentences."
    )
    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": f"请总结以下{context}内容：\n\n{text}"},
    ]
    return await chat(messages)


async def explain_memo(content: str, image_urls: list[str] | None = None) -> str:
    """Explain a memo post. Supports vision if image URLs are provided."""
    system = "You are a helpful assistant. Explain the following post content in a friendly tone in Chinese."
    user_content: list | str

    supports_vision = settings.ai_provider in ["openai", "gemini"]

    if image_urls and supports_vision:
        # Vision-capable message format
        user_content = [{"type": "text", "text": f"请解释以下帖子内容：\n{content}"}]
        for url in image_urls:
            user_content.append({"type": "image_url", "image_url": {"url": url}})
    else:
        user_content = f"请解释以下帖子内容：\n{content}"

    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": user_content},
    ]
    return await chat(messages)


def get_available_providers() -> list[dict]:
    """Return list of all configured AI providers."""
    providers = []
    configs = [
        ("deepseek", settings.deepseek_api_key, settings.deepseek_model),
        ("gemini", settings.gemini_api_key, settings.gemini_model),
        ("zhipu", settings.zhipu_api_key, settings.zhipu_model),
        ("openai", settings.openai_api_key, settings.openai_model),
        ("ollama", "local", settings.ollama_model),
        ("custom", settings.custom_api_key, settings.custom_model),
    ]
    for name, key, model in configs:
        if key and model:
            providers.append({"name": name, "model": model, "active": name == settings.ai_provider})
    return providers


def _extract_json_block(text: str) -> dict:
    """Extract first valid JSON object from model output."""
    s = (text or "").strip()
    if not s:
        return {}

    if s.startswith("```"):
        s = s.strip("`")
        if s.startswith("json"):
            s = s[4:].strip()

    # Fast path: whole payload is JSON.
    try:
        obj = json.loads(s)
        return obj if isinstance(obj, dict) else {}
    except Exception:
        pass

    # Fallback: find first {...} block.
    start = s.find("{")
    end = s.rfind("}")
    if start >= 0 and end > start:
        try:
            obj = json.loads(s[start : end + 1])
            return obj if isinstance(obj, dict) else {}
        except Exception:
            return {}
    return {}


async def classify_trending_items(items: list[dict], source: str = "github") -> dict:
    """Classify trending projects/models into high-level categories using configured AI."""
    if not items:
        return {"categories": []}

    compact = []
    for i, item in enumerate(items[:30], start=1):
        compact.append(
            {
                "idx": i,
                "name": item.get("name") or item.get("id") or "",
                "description": (item.get("description") or "")[:220],
                "language": item.get("language") or "",
                "pipeline_tag": item.get("pipeline_tag") or "",
            }
        )

    prompt_topics = (
        "AI模型, 大语言模型, 前端, 后端, 数据库, 开发工具, DevOps, 安全, 游戏, 移动端, 数据科学, 其他"
    )
    if source == "papers":
        prompt_topics = "CV (计算机视觉), NLP (自然语言处理), RL (强化学习), 具身智能, LLM (大语言模型), 多模态, 语音处理, 其他"

    system = (
        "You are a strict JSON classifier. "
        f"Classify each project/model/paper into one concise Chinese category such as: {prompt_topics}. "
        "Return JSON only with schema: {\"categories\":[{\"name\":str,\"items\":[int,...]}]} "
        "where items are idx values. Do not include explanations."
    )
    user = (
        f"来源: {source}\n"
        "请根据名称、描述或摘要进行自动分类，输出 JSON。\n"
        f"列表:\n{json.dumps(compact, ensure_ascii=False)}"
    )
    resp = await chat([
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ])
    obj = _extract_json_block(resp)

    categories = obj.get("categories") if isinstance(obj, dict) else None
    if not isinstance(categories, list):
        return {"categories": [{"name": "其他", "items": [i + 1 for i in range(len(compact))]}]}

    # Keep only valid positive indices.
    normalized = []
    seen = set()
    for c in categories:
        if not isinstance(c, dict):
            continue
        name = str(c.get("name") or "其他").strip() or "其他"
        idxs = c.get("items") if isinstance(c.get("items"), list) else []
        clean = []
        for idx in idxs:
            try:
                n = int(idx)
            except Exception:
                continue
            if 1 <= n <= len(compact) and n not in seen:
                seen.add(n)
                clean.append(n)
        if clean:
            normalized.append({"name": name, "items": clean})

    # Put unclassified items into "其他".
    missing = [i for i in range(1, len(compact) + 1) if i not in seen]
    if missing:
        normalized.append({"name": "其他", "items": missing})

    return {"categories": normalized}
