"""Azure OpenAI client with structured-output enforcement.

The triage workflow calls `triage(issue, retriever)` to get a `TriageResult`.
The response is validated against `schema/triage_output.json` before returning.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from openai import AzureOpenAI

from knowledge.retriever import Chunk, Retriever

log = logging.getLogger(__name__)

_HERE = Path(__file__).resolve().parent.parent
_SYSTEM_PROMPT = (_HERE / "prompts" / "system.md").read_text(encoding="utf-8")
_SCHEMA = json.loads((_HERE / "schema" / "triage_output.json").read_text(encoding="utf-8"))

DEFAULT_API_VERSION = "2024-10-21"
DEFAULT_TEMPERATURE = 0.2
DEFAULT_MAX_TOKENS = 2000


@dataclass
class TriageResult:
    category: str
    mode: str
    severity: str
    labels: list[str]
    summary: str
    missing_info: list[str]
    ado_title: str
    ado_description_html: str
    ado_tags: list[str]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _build_client() -> AzureOpenAI:
    endpoint = os.environ["AZURE_OPENAI_ENDPOINT"]
    api_key = os.environ["AZURE_OPENAI_API_KEY"]
    api_version = os.environ.get("AZURE_OPENAI_API_VERSION", DEFAULT_API_VERSION)
    return AzureOpenAI(
        api_key=api_key,
        api_version=api_version,
        azure_endpoint=endpoint,
    )


def _format_grounding(chunks: list[Chunk]) -> str:
    if not chunks:
        return "(no internal grounding sources available)"
    blocks = []
    for i, c in enumerate(chunks, 1):
        header = f"[Source {i}: {c.source}{f' — {c.url}' if c.url else ''}]"
        blocks.append(f"{header}\n{c.text.strip()}")
    return "\n\n---\n\n".join(blocks)


def _build_user_prompt(issue: dict, chunks: list[Chunk]) -> str:
    grounding = _format_grounding(chunks)
    labels = ", ".join(l["name"] if isinstance(l, dict) else str(l)
                       for l in (issue.get("labels") or [])) or "(none)"
    return (
        "## Issue metadata\n"
        f"- Number: #{issue.get('number')}\n"
        f"- Title: {issue.get('title')}\n"
        f"- Author: @{(issue.get('user') or {}).get('login', 'unknown')}\n"
        f"- Existing labels: {labels}\n"
        f"- URL: {issue.get('html_url')}\n\n"
        "## Issue body\n"
        f"{issue.get('body') or '(empty)'}\n\n"
        "## Internal grounding context (for your understanding only — do not quote verbatim)\n"
        f"{grounding}\n\n"
        "## Now produce the JSON object that conforms to the TriageOutput schema."
    )


def triage(issue: dict, retriever: Retriever, *, debug_sink: dict | None = None) -> TriageResult:
    """Run a single issue through Azure OpenAI and return a validated TriageResult.

    Parameters
    ----------
    issue : dict
        The GitHub issue payload (the `issue` key of the webhook event).
    retriever : Retriever
        Knowledge retriever. Use `NoOpRetriever()` to skip RAG.
    debug_sink : dict | None
        If provided, gets populated with the raw prompt, response, and decisions
        so the workflow can upload them as an artifact.
    """
    query = f"{issue.get('title', '')}\n\n{issue.get('body', '')}"[:4000]
    chunks = retriever.retrieve(query)
    user_prompt = _build_user_prompt(issue, chunks)

    client = _build_client()
    deployment = os.environ["AZURE_OPENAI_DEPLOYMENT"]

    schema_for_api = {
        "name": "TriageOutput",
        "strict": True,
        "schema": _SCHEMA,
    }

    log.info("Calling Azure OpenAI deployment=%s for issue #%s", deployment, issue.get("number"))
    # gpt-5.1 and other reasoning-class deployments reject `max_tokens` and require
    # `max_completion_tokens`; some also reject `temperature` other than the default.
    # Build kwargs once and try; on a 400 about parameters, retry without them.
    base_kwargs = {
        "model": deployment,
        "response_format": {"type": "json_schema", "json_schema": schema_for_api},
        "messages": [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ],
    }
    try:
        completion = client.chat.completions.create(
            **base_kwargs,
            temperature=DEFAULT_TEMPERATURE,
            max_completion_tokens=DEFAULT_MAX_TOKENS,
        )
    except Exception as exc:  # noqa: BLE001
        msg = str(exc)
        if "temperature" in msg or "unsupported_value" in msg:
            log.warning("Deployment rejected temperature; retrying without it.")
            completion = client.chat.completions.create(
                **base_kwargs,
                max_completion_tokens=DEFAULT_MAX_TOKENS,
            )
        else:
            raise

    raw = completion.choices[0].message.content or "{}"
    data = json.loads(raw)
    result = TriageResult(**data)

    if debug_sink is not None:
        debug_sink["prompt"] = {"system": _SYSTEM_PROMPT, "user": user_prompt}
        debug_sink["raw_response"] = raw
        debug_sink["triage"] = result.to_dict()
        debug_sink["model"] = deployment
        debug_sink["grounding_chunks"] = [
            {"source": c.source, "url": c.url, "preview": c.text[:200]} for c in chunks
        ]

    return result
