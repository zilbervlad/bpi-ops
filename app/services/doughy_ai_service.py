import json
import os
from typing import Any

from openai import OpenAI


OPENAI_MODEL = (os.getenv("OPENAI_MODEL") or os.getenv("DOUGHY_OPENAI_MODEL") or "gpt-4.1").strip()


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
- Separate current risks from future sections that are not due yet.
- Do not recommend discipline.
- Do not offer write actions.
- Be useful to an operations leader.
""".strip()


def doughy_ai_enabled() -> bool:
    return bool((os.getenv("OPENAI_API_KEY") or "").strip())


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


def ask_doughy_ai(prompt: str, execution_snapshot: dict[str, Any]) -> str:
    client = OpenAI()

    payload = {
        "user_prompt": prompt,
        "execution_snapshot": _compact_execution_snapshot(execution_snapshot),
        "answer_format": {
            "style": "plain text",
            "max_words": 180,
            "tone": "direct, ops-focused, Doughy voice",
        },
    }

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
