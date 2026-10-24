import json
import logging

from app.settings import settings

log = logging.getLogger("procurement.llm")


def complete_json(system: str, user: str) -> dict | None:
    """Live provider adapter. Unused unless LLM_MODE=live and LLM_API_KEY is set."""
    if settings.llm_mode != "live" or not settings.llm_api_key:
        return None
    import httpx

    response = httpx.post(
        f"{settings.llm_base_url.rstrip('/')}/chat/completions",
        headers={"Authorization": f"Bearer {settings.llm_api_key}"},
        json={
            "model": settings.llm_model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "response_format": {"type": "json_object"},
            "temperature": 0,
        },
        timeout=30,
    )
    response.raise_for_status()
    content = response.json()["choices"][0]["message"]["content"]
    parsed = json.loads(content)
    log.info("llm_extract_used model=%s", settings.llm_model)
    return parsed
