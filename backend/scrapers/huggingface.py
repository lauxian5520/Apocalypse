"""
HuggingFace trending models — uses the public HF API.
"""
import httpx

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept": "application/json",
}


async def fetch(limit: int = 30) -> list[dict]:
    # Try multiple sort options; "trending" may not be supported in all regions
    for sort_by in ["likes7d", "likes", "downloads"]:
        try:
            url = "https://huggingface.co/api/models"
            params = {
                "sort": sort_by,
                "limit": limit,
                "full": "false",
                "cardData": "false",
            }
            async with httpx.AsyncClient(timeout=20, headers=HEADERS) as client:
                resp = await client.get(url, params=params)
                resp.raise_for_status()
                data = resp.json()
            if isinstance(data, list) and len(data) > 1:
                models = []
                for m in data:
                    model_id = m.get("id", "") or m.get("modelId", "")
                    parts = model_id.split("/")
                    models.append({
                        "id": model_id,
                        "name": parts[-1] if parts else model_id,
                        "author": parts[0] if len(parts) > 1 else "",
                        "url": f"https://huggingface.co/{model_id}",
                        "description": (m.get("cardData") or {}).get("summary", ""),
                        "likes": m.get("likes", 0),
                        "downloads": m.get("downloads", 0),
                        "pipeline_tag": m.get("pipeline_tag", ""),
                        "tags": (m.get("tags") or [])[:5],
                    })
                return models
        except Exception:
            continue
    return []
