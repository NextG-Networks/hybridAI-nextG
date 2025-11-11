"""
Thin OpenAI client for the Reasoner.

- Uses the Responses API
- Structured Outputs via text.format (JSON Schema) to emit Intent objects
- Small + robust parsing, retries, and clear errors
"""

import os
import json
import time
import typing as t
import httpx


OPENAI_BASE_URL = os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1")
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")
OPENAI_MODEL = os.environ.get(
    "OPENAI_MODEL", "gpt-5"
)  # set to a model your org has access to


class OpenAIError(Exception):
    pass


def _headers() -> dict:
    if not OPENAI_API_KEY:
        raise OpenAIError("OPENAI_API_KEY is not set")
    return {
        "Authorization": f"Bearer {OPENAI_API_KEY}",
        "Content-Type": "application/json",
    }


INTENT_JSON_SCHEMA: dict = {
    "name": "NetworkIntent",
    "schema": {
        "$schema": "http://json-schema.org/draft-07/schema#",
        "type": "object",
        "properties": {
            "intent_id": {"type": "string"},
            "category": {
                "type": "string",
                "enum": ["performance", "reachability", "security", "capacity"],
            },
            "goal": {"type": "string"},
            "scope": {
                "type": "object",
                "properties": {
                    "service": {"type": "string"},
                    "region": {"type": "string"},
                },
                "required": ["service", "region"],
                "additionalProperties": False,
            },
            "constraints": {
                "type": "object",
                "properties": {
                    "tenancy": {"type": "string"},
                    "change_window": {"type": "string"},
                    "max_risk": {"type": "string", "enum": ["low", "medium", "high"]},
                },
                "required": ["tenancy", "change_window", "max_risk"],
                "additionalProperties": False,
            },
            "slo": {
                "type": "object",
                "properties": {
                    "latency_ms": {"type": "number"},
                    "loss_pct": {"type": "number"},
                    "availability": {"type": "number"},
                },
                "required": ["latency_ms", "loss_pct", "availability"],
                "additionalProperties": False,
            },
            "evidence_ref": {"type": "string"},
        },
        # Root required MUST include every key in properties:
        "required": [
            "intent_id",
            "category",
            "goal",
            "scope",
            "constraints",
            "slo",
            "evidence_ref",
        ],
        "additionalProperties": False,
    },
}


def reason_from_deviation(deviation: dict, policies: dict | None = None) -> dict:
    """
    Call OpenAI Responses API with Structured Outputs to turn a deviation into an Intent.
    Returns a dict that conforms to INTENT_JSON_SCHEMA.
    """
    prompt = (
        "You are a network intent reasoner.\n"
        "Given a KPI deviation event and context, produce a NetworkIntent JSON object "
        "that aims to restore SLOs. Use conservative defaults if information is missing.\n"
        "Output ONLY JSON; no prose."
    )
    user_input = {"deviation": deviation, "policies": policies or {}}

    #    body = {
    #        "model": OPENAI_MODEL,
    #        "input": [
    #            {"role": "system", "content": prompt},
    #            {
    #                "role": "user",
    #                "content": [
    #                    {"type": "text", "text": "Create an intent for this deviation:"},
    #                    {"type": "json", "json": user_input},
    #                ],
    #            },
    #        ],
    #        "text": {
    #            "format": {
    #                "type": "json_schema",
    #                "json_schema": INTENT_JSON_SCHEMA,
    #            }
    #        },
    #    }
    body = {
        "model": OPENAI_MODEL,
        "input": [
            {
                "role": "system",
                "content": [{"type": "input_text", "text": prompt}],
            },
            {
                "role": "user",
                "content": [
                    {
                        "type": "input_text",
                        "text": "Create an intent for this deviation. Here is the input JSON:\n"
                        + json.dumps(user_input, ensure_ascii=False),
                    }
                ],
            },
        ],
        "text": {
            "format": {
                "type": "json_schema",
                "name": "NetworkIntent",
                "schema": INTENT_JSON_SCHEMA["schema"],
            }
        },
    }

    url = f"{OPENAI_BASE_URL}/responses"

    # Simple retry on 429/5xx
    for attempt in range(3):
        try:
            with httpx.Client(timeout=60) as client:
                r = client.post(url, headers=_headers(), json=body)
            if r.status_code in (429, 500, 502, 503, 504, 520):
                time.sleep(1.5 * (attempt + 1))
                continue
            if r.status_code >= 400:
                # avoid dumping raw HTML into logs
                body = r.text
                if "<html" in body.lower():
                    body = "HTML error page (truncated)"
                raise OpenAIError(f"OpenAI error {r.status_code}: {body}")
            data = r.json()
            break
        except httpx.HTTPError as e:
            if attempt == 2:
                raise OpenAIError(f"HTTP error: {e}")
            time.sleep(1.5 * (attempt + 1))
    else:
        # pragma: no cover
        raise OpenAIError("Failed to reach OpenAI after retries")

    # Expected shape:
    # data["output"][0]["content"] -> list of parts
    # we want the first {"type":"output_text","text":"{...json...}"}
    intent = None
    try:
        outputs = data.get("output") or []
        if outputs:
            parts = outputs[0].get("content") or []
            for part in parts:
                if part.get("type") in ("output_text", "text"):
                    txt = part.get("text", "")
                    intent = json.loads(txt)
                    break
    except Exception:
        intent = None

    if not isinstance(intent, dict):
        raise OpenAIError(
            f"Could not parse structured intent from OpenAI response: {data}"
        )

    return intent
