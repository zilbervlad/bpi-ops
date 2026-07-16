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

You are answering from permission-filtered, read-only BPI Ops context.

Rules:
- Use only the provided BPI Ops context.
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


def _build_ai_payload(prompt: str, context_bundle: dict[str, Any]) -> dict[str, Any]:
    return {
        "user_prompt": prompt,
        "context_bundle": context_bundle,
        "answer_format": {
            "style": "plain text",
            "max_words": 180,
            "tone": "direct, ops-focused, Doughy voice",
        },
    }


def _ask_openai(prompt: str, context_bundle: dict[str, Any]) -> str:
    client = OpenAI()
    payload = _build_ai_payload(prompt, context_bundle)

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



def _compact_gateway_context(
    context_bundle: dict[str, Any],
) -> dict[str, Any]:
    """Return a compact, permission-filtered context for the Brain API."""
    # Universal gateway responses return a module
    # and records directly rather than store_context
    # or scope_rollup. Preserve those records for Brain.
    if (
        context_bundle.get("module")
        and "records" in context_bundle
    ):
        records = (
            context_bundle.get("records")
            or []
        )

        compact_gateway = {
            "mode": (
                "read_only_bpi_universal_gateway"
            ),
            "module": (
                context_bundle.get("module")
            ),
            "count": (
                context_bundle.get("count")
            ),
            "filters": (
                context_bundle.get("filters")
                or {}
            ),
            "records": records[:100],
        }

        if (
            context_bundle.get(
                "manager_walk_summary"
            )
            is not None
        ):
            compact_gateway[
                "manager_walk_summary"
            ] = context_bundle.get(
                "manager_walk_summary"
            )

        return compact_gateway

    scope = context_bundle.get("scope") or {}
    page = context_bundle.get("page") or {}
    requested = context_bundle.get("requested") or {}

    compact: dict[str, Any] = {
        "mode": context_bundle.get("mode"),
        "page": {
            "page": page.get("page"),
            "path": page.get("path"),
            "section": page.get("section"),
            "resource_id": page.get("resource_id"),
            "endpoint": page.get("endpoint"),
        },
        "scope": {
            "role": scope.get("role"),
            "user_area": scope.get("user_area"),
            "user_store": scope.get("user_store"),
            "visible_store_count": scope.get("visible_store_count"),
        },
        "requested": requested,
    }

    rollup = context_bundle.get("scope_rollup") or {}

    if rollup:
        nightly = rollup.get("nightly_numbers") or {}

        compact["scope_rollup"] = {
            "business_date": rollup.get("business_date"),
            "week_start": rollup.get("week_start"),
            "week_end": rollup.get("week_end"),
            "store_count": rollup.get("store_count"),
            "checklist": rollup.get("checklist") or {},
            "maintenance": rollup.get("maintenance") or {},
            "svr": rollup.get("svr") or {},
            "verification": rollup.get("verification") or {},
            "nightly_numbers": {
                "submitted_count": nightly.get("submitted_count"),
                "missing_stores": (
                    nightly.get("missing_stores") or []
                )[:30],
                "reports": (
                    nightly.get("reports") or []
                )[:8],
            },
        }

    store_context = context_bundle.get("store_context") or {}

    page_section = str(
        page.get("section")
        or page.get("page")
        or page.get("endpoint")
        or ""
    ).strip().lower()

    is_dashboard_page = (
        page_section in {"", "dashboard", "store-dashboard", "store_dashboard"}
        or "dashboard" in page_section
    )

    active_module = "all"

    if "checklist" in page_section:
        active_module = "checklist"
    elif "maintenance" in page_section:
        active_module = "maintenance"
    elif "svr" in page_section or "store visit" in page_section:
        active_module = "svr"
    elif "nightly" in page_section:
        active_module = "nightly_numbers"
    elif "verification" in page_section:
        active_module = "verification"
    elif "cash" in page_section:
        active_module = "cash"
    elif "focus" in page_section:
        active_module = "weekly_focus"
    elif is_dashboard_page:
        active_module = "all"

    compact["active_module"] = active_module

    if store_context:
        checklist = store_context.get("checklist") or {}
        doughy_read = checklist.get("doughy_read") or {}
        maintenance = store_context.get("maintenance") or {}
        svr = store_context.get("svr") or {}
        nightly = store_context.get("nightly_numbers") or {}
        verification = store_context.get("verification") or {}
        weekly_focus = store_context.get("weekly_focus") or {}
        cash = store_context.get("cash") or {}

        compact_sections = []

        for section in (checklist.get("sections") or [])[:5]:
            compact_sections.append({
                "section_name": section.get("section_name"),
                "possible_points": section.get("possible_points"),
                "protected_points": section.get("protected_points"),
                "questionable_points": section.get("questionable_points"),
                "at_risk_points": section.get("at_risk_points"),
                "due_status": section.get("due_status"),
                "top_risks": (
                    section.get("top_risks") or []
                )[:2],
                "questionable_items": (
                    section.get("questionable_items") or []
                )[:2],
            })

        compact_svrs = []

        for report in (svr.get("recent_reports") or [])[:2]:
            compact_svrs.append({
                "id": report.get("id"),
                "visit_date": report.get("visit_date"),
                "manager_on_duty": report.get("manager_on_duty"),
                "supervisor_name": report.get("supervisor_name"),
                "observations": (
                    report.get("observations") or []
                )[:5],
            })

        compact_verifications = []

        for report in (
            verification.get("recent_reports") or []
        )[:2]:
            compact_verifications.append({
                "id": report.get("id"),
                "report_date": report.get("report_date"),
                "supervisor_name": report.get("supervisor_name"),
                "responses": (
                    report.get("responses") or []
                )[:5],
            })

        compact_store = {
            "store_number": store_context.get("store_number"),
            "business_date": store_context.get("business_date"),
        }

        if active_module in {"all", "checklist"}:
            compact_store["checklist"] = {
                "store_number": checklist.get("store_number"),
                "checklist_date": checklist.get("checklist_date"),
                "manager_on_duty": checklist.get("manager_on_duty"),
                "opening_manager": checklist.get("opening_manager"),
                "closing_manager": checklist.get("closing_manager"),
                "status": checklist.get("status"),
                "percent_complete": checklist.get("percent_complete"),
                "integrity_score": checklist.get("integrity_score"),
                "totals": checklist.get("totals") or {},
                "doughy_read": {
                    "headline": doughy_read.get("headline"),
                    "summary": doughy_read.get("summary"),
                    "review_focus": (
                        doughy_read.get("review_focus") or []
                    )[:4],
                    "current_focus": (
                        doughy_read.get("current_focus") or []
                    )[:4],
                    "future_focus": (
                        doughy_read.get("future_focus") or []
                    )[:3],
                },
                "sections": compact_sections,
            } if checklist else None

        if active_module in {"all", "maintenance"}:
            compact_store["maintenance"] = {
                "active_count": maintenance.get("active_count"),
                "status_counts": maintenance.get("status_counts") or {},
                "oldest_active": (
                    maintenance.get("oldest_active") or []
                )[:4],
            }

        if active_module in {"all", "svr"}:
            compact_store["svr"] = {
                "recent_reports": compact_svrs,
            }

        if active_module in {"all", "nightly_numbers"}:
            compact_store["nightly_numbers"] = {
                "recent_reports": (
                    nightly.get("recent_reports") or []
                )[:4],
            }

        if active_module in {"all", "verification"}:
            compact_store["verification"] = {
                "recent_reports": compact_verifications,
            }

        if active_module in {"all", "weekly_focus"}:
            compact_store["weekly_focus"] = {
                "open_count": weekly_focus.get("open_count"),
                "items": (
                    weekly_focus.get("items") or []
                )[:6],
            }

        if active_module in {"all", "cash"}:
            compact_store["cash"] = {
                "recent_logs": (
                    cash.get("recent_logs") or []
                )[:5],
            }

        compact["store_context"] = compact_store

    return compact


def _ask_brain(
    prompt: str,
    context_bundle: dict[str, Any],
) -> str:
    normalized_prompt = str(prompt or "").strip().lower()

    product_knowledge_terms = (
        "pepperoni",
        "topping",
        "toppings",
        "pizza",
        "cheese",
        "sauce",
        "portion",
        "portions",
        "recipe",
        "meatzza",
        "extravaganzza",
        "ultimate pepperoni",
        "buffalo chicken",
        "philly",
        "pacific veggie",
    )

    live_ops_terms = (
        "store",
        "stores",
        "today",
        "yesterday",
        "checklist",
        "manager's walk",
        "managers walk",
        "restock",
        "nightly numbers",
        "maintenance",
        "svr",
        "verification",
        "dwp",
        "employee",
        "employees",
        "user",
        "users",
        "who missed",
        "what needs attention",
    )

    is_product_knowledge_question = (
        any(
            term in normalized_prompt
            for term in product_knowledge_terms
        )
        and not any(
            term in normalized_prompt
            for term in live_ops_terms
        )
    )

    compact_context = (
        {}
        if is_product_knowledge_question
        else _compact_gateway_context(context_bundle)
    )

    extra_context = (
        "AUTHORITATIVE LIVE BPI OPS CONTEXT\n\n"
        "This data is permission-filtered and read-only. "
        "Use it as the source of truth for current store, checklist, "
        "SVR, maintenance, verification, nightly numbers, and scope data. "
        "Do not invent missing facts. Distinguish documented facts from "
        "operational interpretation.\n\n"
        f"{json.dumps(compact_context, default=str)}"
    )

    if is_product_knowledge_question:
        extra_context = (
            "PRODUCT KNOWLEDGE QUESTION\n\n"
            "Answer from the local Domino's knowledge base. "
            "Do not use live BPI Ops checklist, maintenance, SVR, "
            "nightly-number, employee, or store data. "
            "For topping portions, prefer the official Topping Portions "
            "guide over specialty recipe cards or general model knowledge."
        )

    request_body = {
        "question": prompt,
        "extra_context": extra_context,
        "source": "bpi_ops",
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


def _ask_ollama(prompt: str, context_bundle: dict[str, Any]) -> str:
    payload = _build_ai_payload(prompt, context_bundle)

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


def ask_doughy_ai(prompt: str, context_bundle: dict[str, Any]) -> str:
    if AI_PROVIDER == "brain":
        return _ask_brain(prompt, context_bundle)

    if AI_PROVIDER == "ollama":
        return _ask_ollama(prompt, context_bundle)

    return _ask_openai(prompt, context_bundle)
