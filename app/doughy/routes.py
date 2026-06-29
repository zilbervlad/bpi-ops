from datetime import date

from flask import jsonify, render_template, request, session

from app.auth.routes import login_required
from app.models import DailyChecklist, User
from app.services.doughy_execution import build_execution_snapshot
from app.services.doughy_ai_service import ask_doughy_ai, doughy_ai_enabled, doughy_ai_provider

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

def _build_safe_doughy_answer(prompt, checklist_context):
    prompt_text = (prompt or "").lower()
    doughy_read = checklist_context.get("doughy_read") or {}
    snapshot = checklist_context.get("execution_snapshot") or {}
    totals = snapshot.get("totals") or {}

    review_focus = doughy_read.get("review_focus") or []
    current_focus = doughy_read.get("current_focus") or []
    future_focus = doughy_read.get("future_focus") or []

    protected = totals.get("protected_points", 0) or 0
    questionable = totals.get("questionable_points", 0) or 0
    at_risk = totals.get("at_risk_points", 0) or 0
    possible = totals.get("possible_points", 0) or 0

    headline = doughy_read.get("headline") or "Here’s what I see."
    summary = doughy_read.get("summary") or (
        f"{protected:g} of {possible:g} OA-mapped points are protected. "
        f"{questionable:g} are checked but not fully verified, and {at_risk:g} are not protected yet."
    )

    lines = [headline, "", summary]

    if "recover" in prompt_text or "fix" in prompt_text or "still" in prompt_text:
        if current_focus:
            lines.extend(["", "What can still be recovered:"])
            lines.extend([f"• {item}" for item in current_focus[:4]])
        if future_focus:
            lines.extend(["", "Pending later:"])
            lines.extend([f"• {item}" for item in future_focus[:3]])
    elif "review" in prompt_text or "questionable" in prompt_text or "timing" in prompt_text:
        if review_focus:
            lines.extend(["", "Needs review:"])
            lines.extend([f"• {item}" for item in review_focus[:4]])
        else:
            lines.extend(["", "I do not see checked items marked questionable from the current snapshot."])
    elif "summary" in prompt_text or "summarize" in prompt_text:
        focus_items = doughy_read.get("focus_items") or []
        if focus_items:
            lines.extend(["", "Main points:"])
            lines.extend([f"• {item}" for item in focus_items[:5]])
    else:
        if review_focus:
            lines.extend(["", "Needs review:"])
            lines.extend([f"• {item}" for item in review_focus[:3]])
        if current_focus:
            lines.extend(["", "Current risk:"])
            lines.extend([f"• {item}" for item in current_focus[:3]])
        if future_focus:
            lines.extend(["", "Pending later:"])
            lines.extend([f"• {item}" for item in future_focus[:2]])

    lines.extend([
        "",
        "I’m treating timing flags as review signals, not proof. This is read-only."
    ])

    return "\n".join(lines)


@doughy_bp.route("/ask", methods=["POST"])
@login_required
def ask():
    payload = request.get_json(silent=True) or {}

    prompt = (payload.get("prompt") or "").strip()
    store = (payload.get("store") or request.args.get("store") or "").strip()
    checklist_date = _parse_date(payload.get("date") or request.args.get("date"))

    if not prompt:
        return jsonify({
            "ok": False,
            "error": "Missing prompt.",
            "mode": "read_only_doughy_ask",
        }), 400

    user = _current_user()

    if not store:
        store = (
            session.get("user_store")
            or _safe_attr(user, "store_number", None)
            or _safe_attr(user, "store", None)
            or _safe_attr(user, "primary_store", None)
        )

    if not store:
        return jsonify({
            "ok": False,
            "error": "No store context found.",
            "mode": "read_only_doughy_ask",
        }), 400

    execution_snapshot = build_execution_snapshot(str(store), checklist_date)

    checklist_context = {
        "store": str(store),
        "business_date": checklist_date.isoformat(),
        "execution_snapshot": execution_snapshot,
        "doughy_read": execution_snapshot.get("doughy_read"),
    }

    uses_ai = False
    ai_error = None

    if doughy_ai_enabled():
        try:
            answer = ask_doughy_ai(prompt, execution_snapshot)
            uses_ai = True
        except Exception as exc:
            ai_error = str(exc)
            answer = _build_safe_doughy_answer(prompt, checklist_context)
    else:
        answer = _build_safe_doughy_answer(prompt, checklist_context)

    return jsonify({
        "ok": True,
        "answer": answer,
        "store": str(store),
        "business_date": checklist_date.isoformat(),
        "mode": "read_only_doughy_ask",
        "uses_ai": uses_ai,
        "ai_provider": doughy_ai_provider() if uses_ai else None,
        "ai_error": ai_error,
        "execution_snapshot": execution_snapshot,
    })

@doughy_bp.route("/execution-feed")
@login_required
def execution_feed():
    user = _current_user()
    role = (_safe_attr(user, "role", "") or "").lower()

    if role not in {"admin", "supervisor"}:
        return jsonify({"ok": False, "error": "Unauthorized"}), 403

    selected_date = _parse_date(request.args.get("date"))

    company_id = (
        session.get("company_id")
        or session.get("current_company_id")
        or _safe_attr(user, "company_id", None)
        or _safe_attr(user, "current_company_id", None)
    )

    query = DailyChecklist.query.filter(DailyChecklist.checklist_date == selected_date)

    if company_id and hasattr(DailyChecklist, "company_id"):
        query = query.filter(DailyChecklist.company_id == company_id)

    rows = query.order_by(DailyChecklist.store_number.asc(), DailyChecklist.id.desc()).all()

    latest_by_store = {}
    for row in rows:
        store_number = str(row.store_number)
        if store_number not in latest_by_store:
            latest_by_store[store_number] = row

    feed_rows = []

    for store_number in sorted(latest_by_store.keys()):
        snapshot = build_execution_snapshot(store_number, selected_date)
        totals = snapshot.get("totals") or {}
        doughy_read = snapshot.get("doughy_read") or {}

        current_risk = 0
        pending_later = 0

        for section in snapshot.get("sections") or []:
            due_status = section.get("due_status") or {}
            at_risk_points = section.get("at_risk_points") or 0

            if due_status.get("status") in {"not_due", "future_day"}:
                pending_later += at_risk_points
            else:
                current_risk += at_risk_points

        protected_points = totals.get("protected_points", 0) or 0
        questionable_points = totals.get("questionable_points", 0) or 0
        at_risk_points = totals.get("at_risk_points", 0) or 0

        due_points = protected_points + questionable_points + current_risk

        if due_points > 0:
            reliability_score = round(((protected_points + (questionable_points * 0.35)) / due_points) * 100, 1)
        else:
            reliability_score = None

        if reliability_score is None:
            reliability_label = "Pending"
        elif reliability_score >= 90:
            reliability_label = "Strong"
        elif reliability_score >= 75:
            reliability_label = "Watch"
        elif reliability_score >= 60:
            reliability_label = "Needs Review"
        else:
            reliability_label = "High Risk"

        feed_rows.append({
            "store_number": store_number,
            "manager_on_duty": snapshot.get("manager_on_duty"),
            "status": snapshot.get("status"),
            "percent_complete": snapshot.get("percent_complete"),
            "integrity_score": snapshot.get("integrity_score"),
            "protected_points": protected_points,
            "questionable_points": questionable_points,
            "at_risk_points": at_risk_points,
            "current_risk_points": current_risk,
            "pending_later_points": pending_later,
            "due_points": due_points,
            "reliability_score": reliability_score,
            "reliability_label": reliability_label,
            "headline": doughy_read.get("headline"),
            "review_focus": doughy_read.get("review_focus") or [],
            "current_focus": doughy_read.get("current_focus") or [],
            "future_focus": doughy_read.get("future_focus") or [],
        })

    scored_rows = [row for row in feed_rows if row.get("reliability_score") is not None]

    summary = {
        "stores": len(feed_rows),
        "protected_points": sum(row["protected_points"] for row in feed_rows),
        "questionable_points": sum(row["questionable_points"] for row in feed_rows),
        "current_risk_points": sum(row["current_risk_points"] for row in feed_rows),
        "pending_later_points": sum(row["pending_later_points"] for row in feed_rows),
        "avg_reliability_score": round(
            sum(row["reliability_score"] for row in scored_rows) / len(scored_rows),
            1,
        ) if scored_rows else None,
    }

    manager_groups = {}

    for row in feed_rows:
        manager_name = (row.get("manager_on_duty") or "Unassigned").strip() or "Unassigned"

        if manager_name not in manager_groups:
            manager_groups[manager_name] = {
                "manager_name": manager_name,
                "stores": [],
                "protected_points": 0,
                "questionable_points": 0,
                "current_risk_points": 0,
                "pending_later_points": 0,
                "due_points": 0,
                "reliability_scores": [],
                "review_count": 0,
                "current_risk_count": 0,
            }

        group = manager_groups[manager_name]
        group["stores"].append(row["store_number"])
        group["protected_points"] += row["protected_points"]
        group["questionable_points"] += row["questionable_points"]
        group["current_risk_points"] += row["current_risk_points"]
        group["pending_later_points"] += row["pending_later_points"]
        group["due_points"] += row.get("due_points") or 0

        if row.get("reliability_score") is not None:
            group["reliability_scores"].append(row["reliability_score"])

        if row.get("questionable_points", 0) > 0:
            group["review_count"] += 1

        if row.get("current_risk_points", 0) > 0:
            group["current_risk_count"] += 1

    manager_rows = []

    for manager_name, group in manager_groups.items():
        scores = group["reliability_scores"]

        if scores:
            avg_reliability_score = round(sum(scores) / len(scores), 1)
        else:
            avg_reliability_score = None

        if avg_reliability_score is None:
            reliability_label = "Pending"
        elif avg_reliability_score >= 90:
            reliability_label = "Strong"
        elif avg_reliability_score >= 75:
            reliability_label = "Watch"
        elif avg_reliability_score >= 60:
            reliability_label = "Needs Review"
        else:
            reliability_label = "High Risk"

        manager_rows.append({
            "manager_name": manager_name,
            "stores": sorted(group["stores"]),
            "store_count": len(group["stores"]),
            "protected_points": group["protected_points"],
            "questionable_points": group["questionable_points"],
            "current_risk_points": group["current_risk_points"],
            "pending_later_points": group["pending_later_points"],
            "due_points": group["due_points"],
            "avg_reliability_score": avg_reliability_score,
            "reliability_label": reliability_label,
            "review_count": group["review_count"],
            "current_risk_count": group["current_risk_count"],
        })

    manager_rows.sort(
        key=lambda row: (
            row["avg_reliability_score"] is None,
            row["avg_reliability_score"] if row["avg_reliability_score"] is not None else 999,
            -row["current_risk_points"],
            -row["questionable_points"],
        )
    )

    return render_template(
        "doughy_execution_feed.html",
        selected_date=selected_date,
        feed_rows=feed_rows,
        manager_rows=manager_rows,
        summary=summary,
    )

