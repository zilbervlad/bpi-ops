from datetime import date

from flask import jsonify, request, session

from app.auth.routes import login_required
from app.models import DailyChecklist, User
from app.services.doughy_execution import build_execution_snapshot

from . import doughy_bp


def _guess_page_from_path(path):
    path = (path or "").lower()

    if "checklist" in path:
        return "checklist"
    if "svr" in path or "store-visit" in path:
        return "svr"
    if "maintenance" in path:
        return "maintenance"
    if "admin" in path:
        return "admin"
    if "dashboard" in path or path == "/":
        return "dashboard"
    if "nightly" in path:
        return "nightly_numbers"
    if "forms" in path:
        return "forms"
    if "verification" in path:
        return "verification"
    if "cash" in path:
        return "cash"
    if "connect" in path:
        return "connect_admin"
    if "dwp" in path:
        return "dwp"

    return "unknown"


def _safe_attr(obj, name, default=None):
    return getattr(obj, name, default) if obj is not None else default


def _current_user():
    user_id = session.get("user_id")
    if not user_id:
        return None
    return User.query.get(user_id)


def _extract_context_from_path(path):
    clean_path = (path or "").split("?")[0]
    parts = [part for part in clean_path.split("/") if part]

    context = {
        "path": clean_path or "/",
        "section": parts[0] if parts else "dashboard",
        "resource_id": None,
        "store_from_path": None,
    }

    for part in parts:
        if part.isdigit():
            context["resource_id"] = part
            break

    for part in parts:
        if part.isdigit() and len(part) == 4:
            context["store_from_path"] = part
            break

    return context

def _friendly_page_name(endpoint, fallback):
    endpoint = (endpoint or "").lower()

    page_names = {
        "dashboard": "Dashboard",
        "checklist": "Daily Checklist",
        "svr": "SVR",
        "maintenance": "Maintenance",
        "store_admin": "Store Admin",
        "reports": "Reports",
        "nightly_numbers": "Nightly Numbers",
        "cash": "Cash Control",
        "cash_review": "Cash Review",
        "verification": "Verification",
        "store_dashboard": "Store Dashboard",
        "prep": "Prep",
        "shift_todos": "Shift To-Dos",
        "forms": "Forms",
        "hr_documents": "HR Documents",
        "connect_admin": "BPI Connect Admin",
        "dwp": "DWP",
        "auth": "Admin Center",
    }

    if endpoint:
        blueprint = endpoint.split(".", 1)[0]
        if blueprint in page_names:
            return page_names[blueprint]

    return fallback or "Current Page"



def _parse_date(value):
    if not value:
        return date.today()

    try:
        return date.fromisoformat(value)
    except ValueError:
        return date.today()


def _daily_checklist_query(store, checklist_date, company_id=None):
    query = DailyChecklist.query.filter_by(
        store_number=str(store),
        checklist_date=checklist_date,
    )

    if company_id and hasattr(DailyChecklist, "company_id"):
        query = query.filter(DailyChecklist.company_id == company_id)

    return query.order_by(DailyChecklist.id.desc())


def _build_checklist_sections(daily):
    sections = {}

    for item in daily.items:
        section_name = item.section_name or "Other"

        if section_name not in sections:
            sections[section_name] = {
                "name": section_name,
                "done": 0,
                "total": 0,
            }

        sections[section_name]["total"] += 1

        if item.is_completed:
            sections[section_name]["done"] += 1

    return list(sections.values())


def _build_checklist_attention(daily, sections):
    attention = []

    completion = round(daily.percent_complete or 0, 1)
    integrity = round(daily.integrity_score or 0, 1)

    if completion < 80:
        attention.append(f"Checklist completion is {completion}%.")

    if integrity < 70:
        attention.append(f"Integrity score is {integrity}%.")

    for section in sections:
        total = section["total"]
        done = section["done"]

        if total and done == 0:
            attention.append(f"{section['name']} has 0/{total} completed.")
        elif total and (done / total) < 0.5:
            attention.append(f"{section['name']} is only {done}/{total} completed.")

    return attention[:8]


@doughy_bp.route("/context")
@login_required
def context():
    page_path = request.args.get("path") or request.referrer or request.path
    endpoint = request.args.get("endpoint") or ""
    page_label = request.args.get("page_label") or ""
    visible_heading = request.args.get("visible_heading") or ""
    browser_title = request.args.get("browser_title") or ""

    guessed_page = page_label or _guess_page_from_path(endpoint or page_path)
    page = _friendly_page_name(endpoint, guessed_page)
    path_context = _extract_context_from_path(page_path)

    user = _current_user()

    role = _safe_attr(user, "role", None)
    store = (
        request.args.get("store")
        or path_context.get("store_from_path")
        or session.get("user_store")
        or _safe_attr(user, "store_number", None)
        or _safe_attr(user, "store", None)
        or _safe_attr(user, "primary_store", None)
    )

    company_id = (
        session.get("company_id")
        or session.get("current_company_id")
        or _safe_attr(user, "company_id", None)
        or _safe_attr(user, "current_company_id", None)
    )

    return jsonify(
        {
            "page": page,
            "endpoint": endpoint,
            "visible_heading": visible_heading,
            "browser_title": browser_title,
            "path": path_context.get("path"),
            "section": path_context.get("section"),
            "resource_id": path_context.get("resource_id"),
            "role": role,
            "store": str(store) if store else None,
            "company_id": company_id,
            "mode": "read_only_context",
        }
    )


@doughy_bp.route("/checklist-context")
@login_required
def checklist_context():
    user = _current_user()

    store = (
        request.args.get("store")
        or session.get("user_store")
        or _safe_attr(user, "store_number", None)
        or _safe_attr(user, "store", None)
        or _safe_attr(user, "primary_store", None)
    )

    company_id = (
        session.get("company_id")
        or session.get("current_company_id")
        or _safe_attr(user, "company_id", None)
        or _safe_attr(user, "current_company_id", None)
    )

    checklist_date = _parse_date(request.args.get("date"))

    if not store:
        return jsonify(
            {
                "ok": False,
                "error": "No store context found.",
                "mode": "read_only_checklist_context",
            }
        ), 400

    daily = _daily_checklist_query(store, checklist_date, company_id).first()

    if not daily:
        return jsonify(
            {
                "ok": True,
                "found": False,
                "store": str(store),
                "business_date": checklist_date.isoformat(),
                "message": "No checklist found for this store/date.",
                "mode": "read_only_checklist_context",
            }
        )

    sections = _build_checklist_sections(daily)
    attention = _build_checklist_attention(daily, sections)

    execution_snapshot = build_execution_snapshot(str(store), checklist_date)

    return jsonify(
        {
            "ok": True,
            "found": True,
            "store": str(store),
            "business_date": checklist_date.isoformat(),
            "completion": round(daily.percent_complete or 0, 1),
            "integrity": round(daily.integrity_score or 0, 1),
            "sections": sections,
            "attention": attention,
            "execution_snapshot": execution_snapshot,
            "doughy_read": execution_snapshot.get("doughy_read"),
            "mode": "read_only_checklist_context",
        }
    )

