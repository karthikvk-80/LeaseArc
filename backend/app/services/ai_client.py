import os
from typing import Any

import httpx


DEFAULT_OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-5-mini")
DEFAULT_GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.0-flash")


class AIProviderError(RuntimeError):
    pass


def _configured_provider(explicit_provider: str | None = None) -> str:
    provider = (explicit_provider or os.getenv("AI_PROVIDER", "openai")).strip().lower()
    if provider not in {"openai", "gemini"}:
        raise AIProviderError(f"Unsupported AI provider '{provider}'")
    return provider


def _extract_openai_text(payload: dict[str, Any]) -> str:
    chunks: list[str] = []
    for item in payload.get("output", []):
        for content in item.get("content", []):
            text_value = content.get("text")
            if isinstance(text_value, str) and text_value.strip():
                chunks.append(text_value.strip())
    if chunks:
        return "\n".join(chunks).strip()
    raise AIProviderError("OpenAI response did not include text output")


def _extract_gemini_text(payload: dict[str, Any]) -> str:
    candidates = payload.get("candidates") or []
    for candidate in candidates:
        content = candidate.get("content") or {}
        parts = content.get("parts") or []
        texts = [part.get("text", "").strip() for part in parts if isinstance(part.get("text"), str)]
        combined = "\n".join(text for text in texts if text).strip()
        if combined:
            return combined
    raise AIProviderError("Gemini response did not include text output")


async def generate_text(
    *,
    prompt: str,
    system_instruction: str,
    provider: str | None = None,
) -> tuple[str, str, str]:
    resolved_provider = _configured_provider(provider)

    if resolved_provider == "openai":
        api_key = os.getenv("OPENAI_API_KEY", "").strip()
        if not api_key:
            raise AIProviderError("OPENAI_API_KEY is not configured")

        async with httpx.AsyncClient(timeout=60) as client:
            response = await client.post(
                "https://api.openai.com/v1/responses",
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": DEFAULT_OPENAI_MODEL,
                    "instructions": system_instruction,
                    "input": prompt,
                    "max_output_tokens": 900,
                },
            )
            response.raise_for_status()
            payload = response.json()
        return _extract_openai_text(payload), "openai", DEFAULT_OPENAI_MODEL

    api_key = os.getenv("GEMINI_API_KEY", "").strip()
    if not api_key:
        raise AIProviderError("GEMINI_API_KEY is not configured")

    async with httpx.AsyncClient(timeout=60) as client:
        response = await client.post(
            f"https://generativelanguage.googleapis.com/v1beta/models/{DEFAULT_GEMINI_MODEL}:generateContent?key={api_key}",
            headers={"Content-Type": "application/json"},
            json={
                "systemInstruction": {
                    "parts": [{"text": system_instruction}],
                },
                "contents": [
                    {
                        "role": "user",
                        "parts": [{"text": prompt}],
                    }
                ],
            },
        )
        response.raise_for_status()
        payload = response.json()
    return _extract_gemini_text(payload), "gemini", DEFAULT_GEMINI_MODEL
