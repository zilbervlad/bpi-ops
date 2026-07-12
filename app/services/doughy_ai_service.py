import json
import os
import urllib.error
import urllib.request
from typing import Any

from openai import OpenAI


AI_PROVIDER = (os.getenv("AI_PROVIDER") or "openai").strip().lower()
OPENAI_MODEL = (os.getenv("OPENAI_MODEL") or os.getenv("DOUGHY_OPENAI_MODEL") or "gpt-4.1").strip()
OLLAMA_BASE_URL = (os.getenv("OLLAMA_BASE_URL") or "http://localhost:11434").strip().rstrip("/")
OLLAMA_MODEL = (os.getenv("OLLAMA_MODEL") or "llama3.1:8b").strip()

BRAIN_API_URL = (
    os.getenv("BRAIN_API_URL")
    or "https://doughy.bostonpie.net/api/brain/ask"
).strip()

BRAIN_API_KEY = (
    os.getenv("BRAIN_API_KEY")
    or os.getenv("BRAIN_KEY")
    or ""
).strip()


DOUGHY_SYSTEM_PROMPT = """
You are Doughy, the BPI Ops assistant.

How you talk:
- Sound like a real person in operations, not a generic AI assistant.
- Be direct, clear, and useful.
- Keep it natural and conversational.
- Do not sound soft, corporate, or overly polite.
- Do not say “I'd be happy to help,” “let me know,” or generic assistant filler.
- Give the answer first.
- Keep responses tight unless the user asks for detail.

You are answering from a BPI Ops execution snapshot.

Rules:
- Use only the provided execution snapshot.
- Do not invent store data, managers, OA points, tasks, dates, or scores.
- Never say a manager faked, lied, cheated, or pencil-whipped a checklist.
- Use safe language:
  - checked but not fully verified
  - timing looks questionable
  - needs review
  - current risk
  - pending later
- Treat burst and expected-time flags as review signals, not proof.
- If timing_summary shows elapsed_minutes near 0, explain it as elapsed time being too fast versus expected_minutes.
- Never say expected time was zero unless expected_minutes is actually 0.
- Separate current risks from future sections that are not due yet.
- Do not recommend discipline.
- Do not offer write actions.
- Be useful to an operations leader.
""".strip()


def doughy_ai_enabled() -> bool:
    if AI_PROVIDER == "brain":
        return bool(BRAIN_API_URL and BRAIN_API_KEY)

    if AI_PROVIDER == "ollama":
        return bool(OLLAMA_BASE_URL and OLLAMA_MODEL)

    return bool((os.getenv("OPENAI_API_KEY") or "").strip())


def doughy_ai_provider() -> str:
    return AI_PROVIDER


def _compact_execution_snapshot(snapshot: dict[str, Any]) -> dict[str, Any]:
    doughy_read = snapshot.get("doughy_read") or {}

    sections = []
    for section in snapshot.get("sections") or []:
        sections.append({
            "section_name": section.get("section_name"),
            "protected_points": section.get("protected_points"),
            "questionable_points": section.get("questionable_points"),
            "at_risk_points": section.get("at_risk_points"),
            "possible_points": section.get("possible_points"),
            "due_status": section.get("due_status"),
            "timing_summary": section.get("timing_summary"),
            "integrity_flags": section.get("integrity_flags") or [],
            "top_risks": (section.get("top_risks") or [])[:5],
            "questionable_items": (section.get("questionable_items") or [])[:5],
        })

    return {
        "store_number": snapshot.get("store_number"),
        "checklist_date": snapshot.get("checklist_date"),
        "manager_on_duty": snapshot.get("manager_on_duty"),
        "opening_manager": snapshot.get("opening_manager"),
        "closing_manager": snapshot.get("closing_manager"),
        "status": snapshot.get("status"),
        "percent_complete": snapshot.get("percent_complete"),
        "integrity_score": snapshot.get("integrity_score"),
        "totals": snapshot.get("totals") or {},
        "doughy_read": {
            "headline": doughy_read.get("headline"),
            "summary": doughy_read.get("summary"),
            "review_focus": doughy_read.get("review_focus") or [],
            "current_focus": doughy_read.get("current_focus") or [],
            "future_focus": doughy_read.get("future_focus") or [],
            "safe_language": doughy_read.get("safe_language") or [],
        },
        "sections": sections,
    }


def _build_ai_payload(prompt: str, execution_snapshot: dict[str, Any]) -> dict[str, Any]:
    return {
        "user_prompt": prompt,
        "execution_snapshot": _compact_execution_snapshot(execution_snapshot),
        "answer_format": {
            "style": "plain text",
            "max_words": 180,
            "tone": "direct, ops-focused, Doughy voice",
        },
    }


def _ask_openai(prompt: str, execution_snapshot: dict[str, Any]) -> str:
    client = OpenAI()
    payload = _build_ai_payload(prompt, execution_snapshot)

    response = client.responses.create(
        model=OPENAI_MODEL,
        input=[
            {
                "role": "system",
                "content": DOUGHY_SYSTEM_PROMPT,
            },
            {
                "role": "user",
                "content": json.dumps(payload, default=str),
            },
        ],
        max_output_tokens=400,
    )

    return (response.output_text or "").strip()


def _ask_brain(
    prompt: str,
    execution_snapshot: dict[str, Any],
) -> str:
    compact_snapshot = _compact_execution_snapshot(execution_snapshot)

    question = (
        f"{prompt}\n\n"
        "Use only this BPI Ops execution snapshot when answering. "
        "Do not invent missing facts.\n\n"
        f"EXECUTION SNAPSHOT:\n{json.dumps(compact_snapshot, default=str)}"
    )

    request_body = {
        "question": question,
    }

    request = urllib.request.Request(
        BRAIN_API_URL,
        data=json.dumps(request_body).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {BRAIN_API_KEY}",
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "BPI-Ops-Doughy/1.0",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            data = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        error_body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(
            f"Brain API returned HTTP {exc.code}: {error_body[:300]}"
        ) from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(
            f"Could not reach Brain API: {exc.reason}"
        ) from exc

    answer = (data.get("answer") or "").strip()

    if not answer:
        raise RuntimeError("Brain API returned no answer.")

    return answer


def _ask_ollama(prompt: str, execution_snapshot: dict[str, Any]) -> str:
    payload = _build_ai_payload(prompt, execution_snapshot)

    request_body = {
        "model": OLLAMA_MODEL,
        "stream": False,
        "messages": [
            {
                "role": "system",
                "content": DOUGHY_SYSTEM_PROMPT,
            },
            {
                "role": "user",
                "content": json.dumps(payload, default=str),
            },
        ],
        "options": {
            "temperature": 0.2,
            "num_predict": 400,
        },
    }

    request = urllib.request.Request(
        f"{OLLAMA_BASE_URL}/api/chat",
        data=json.dumps(request_body).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    with urllib.request.urlopen(request, timeout=45) as response:
        data = json.loads(response.read().decode("utf-8"))

    message = data.get("message") or {}
    return (message.get("content") or "").strip()


def ask_doughy_ai(prompt: str, execution_snapshot: dict[str, Any]) -> str:
    if AI_PROVIDER == "brain":
        return _ask_brain(prompt, execution_snapshot)

    if AI_PROVIDER == "ollama":
        return _ask_ollama(prompt, execution_snapshot)

    return _ask_openai(prompt, execution_snapshot)
