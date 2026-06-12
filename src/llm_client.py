from __future__ import annotations

import json
from typing import Any

from config import GEMINI_API_KEY, GEMINI_MODEL


def extract_json(text: str) -> dict[str, Any]:
    decoder = json.JSONDecoder()
    last_error: json.JSONDecodeError | None = None

    for start, character in enumerate(text):
        if character != "{":
            continue
        try:
            payload, _ = decoder.raw_decode(text, start)
        except json.JSONDecodeError as exc:
            last_error = exc
            continue
        if isinstance(payload, dict):
            return payload

    if last_error is not None:
        raise ValueError(
            f"LLM response did not contain a valid JSON object: {last_error}"
        ) from last_error
    raise ValueError("LLM response did not contain a JSON object")


class GeminiClient:
    def __init__(self, model: str | None = None) -> None:
        self.model = model or GEMINI_MODEL
        self.last_response_text: str | None = None
        if not GEMINI_API_KEY:
            raise RuntimeError("GEMINI_API_KEY environment variable is not set")
        from google import genai

        self.client = genai.Client(api_key=GEMINI_API_KEY)

    def complete_json(self, prompt: str) -> dict[str, Any]:
        response = self.client.models.generate_content(
            model=self.model,
            contents=prompt,
            config={
                "response_mime_type": "application/json",
                "temperature": 0.0,
            },
        )
        text = getattr(response, "text", None) or str(response)
        self.last_response_text = text
        return extract_json(text)


def build_llm_client(*, dry_run: bool = False):
    if dry_run:
        raise RuntimeError("DRY_RUN is disabled for strict extraction runs")
    return GeminiClient()
