import hmac
import json
import os
import re

from datetime import date, datetime, timedelta

from flask import Blueprint, jsonify, request

from app.extensions import db
from app.models import (
    MaintenanceAgentAction,
    MaintenanceEquipment,
    MaintenanceTicket,
    Store,
    User,
)
from app.services.doughy_universal_gateway import build_doughy_universal_context


doughy_api_bp = Blueprint(
    "doughy_api",
    __name__,
    url_prefix="/api/integrations/doughy",
)


_MONTH_NUMBERS = {
    "january": 1,
    "february": 2,
    "march": 3,
    "april": 4,
    "may": 5,
    "june": 6,
    "july": 7,
    "august": 8,
    "september": 9,
    "october": 10,
    "november": 11,
    "december": 12,
}


def _requested_date_from_text(value: str):
    """
    Extract an obvious requested business date from a Doughy question.

    Explicit API date values still take priority. This is only a fallback
    for natural-language questions such as "How did we do July 14?"
    """
    text = str(value or "").strip().lower()

    if not text:
        return None

    today = date.today()

    if re.search(r"\b(yesterday|last night)\b", text):
        return (today - timedelta(days=1)).isoformat()

    month_match = re.search(
        r"\b("
        + "|".join(_MONTH_NUMBERS)
        + r")\s+(\d{1,2})(?:st|nd|rd|th)?"
          r"(?:,?\s+(\d{4}))?\b",
        text,
    )

    if month_match:
        month = _MONTH_NUMBERS[month_match.group(1)]
        day = int(month_match.group(2))
        year = int(month_match.group(3) or today.year)

        try:
            return date(year, month, day).isoformat()
        except ValueError:
            return None

    numeric_match = re.search(
        r"(?<!\d)(\d{1,2})[/-](\d{1,2})"
        r"(?:[/-](\d{2}|\d{4}))?(?!\d)",
        text,
    )

    if numeric_match:
        month = int(numeric_match.group(1))
        day = int(numeric_match.group(2))
        raw_year = numeric_match.group(3)

        if raw_year:
            year = int(raw_year)
            if year < 100:
                year += 2000
        else:
            year = today.year

        try:
            return date(year, month, day).isoformat()
        except ValueError:
            return None

    return None


def _authorized() -> bool:
    expected = (
        os.getenv("DOUGHY_LIVE_CONTEXT_KEY")
        or ""
    ).strip()

    if not expected:
        return False

    auth_header = (
        request.headers.get("Authorization")
        or ""
    ).strip()

    provided = ""

    if auth_header.lower().startswith("bearer "):
        provided = auth_header.split(" ", 1)[1].strip()

    return hmac.compare_digest(
        provided,
        expected,
    )


@doughy_api_bp.route(
    "/live-context",
    methods=["GET", "POST"],
)
def live_context():
    if not _authorized():
        return jsonify({
            "ok": False,
            "error": "Unauthorized.",
        }), 401

    payload = request.get_json(
        silent=True,
    ) or {}

    requested_store = (
        payload.get("store")
        or request.args.get("store")
        or ""
    ).strip() or None

    incoming_query = (
        payload.get("query")
        or payload.get("question")
        or request.args.get("query")
        or request.args.get("question")
        or ""
    )

    requested_date = (
        payload.get("date")
        or request.args.get("date")
        or _requested_date_from_text(incoming_query)
        or None
    )

    module = (
        payload.get("module")
        or request.args.get("module")
        or "dashboard"
    ).strip().lower()

    allowed_modules = {
        "all",
        "dashboard",
        "checklist",
        "maintenance",
        "svr",
        "verification",
        "nightly_numbers",
        "weekly_focus",
        "cash",
        "users",
        "dwp",
        "hr_documents",
        "forms",
        "prep",
        "checklist_history",
        "maintenance_history",
        "maintenance_schedule",
        "svr_history",
        "verification_history",
        "nightly_history",
        "cash_history",
    }

    if module not in allowed_modules:
        module = "dashboard"

    page_context = {
        "page": module,
        "path": "/standalone-doughy",
        "section": (
            "dashboard"
            if module == "all"
            else module
        ),
        "resource_id": None,
        "endpoint": "doughy_api.live_context",
    }

    # Read-only requester scope supplied by an approved integration.
    #
    # The integration key authenticates the calling service.
    # The requester fields limit which stores the data gateway may return.
    requester_role = str(
        payload.get("requesting_role")
        or request.args.get("requesting_role")
        or ""
    ).strip().lower()

    requester_area = str(
        payload.get("requesting_area")
        or request.args.get("requesting_area")
        or ""
    ).strip() or None

    requester_store = str(
        payload.get("requesting_store")
        or request.args.get("requesting_store")
        or ""
    ).strip() or None

    # Preserve the existing standalone-admin behavior for older trusted
    # integrations that have not started sending requester scope yet.
    if not requester_role:
        requester_role = "admin"

    user_context = {
        "role": requester_role,
        "user_area": requester_area,
        "user_store": requester_store,
    }

    context = build_doughy_universal_context(
        user_context=user_context,
        page_context=page_context,
        requested_store=requested_store,
        requested_date=requested_date,
        date_from=(
            payload.get("date_from")
            or request.args.get("date_from")
        ),
        date_to=(
            payload.get("date_to")
            or request.args.get("date_to")
        ),
        status=(
            payload.get("status")
            or request.args.get("status")
            or ""
        ),
        employee=(
            payload.get("employee")
            or request.args.get("employee")
            or ""
        ),
        query_text=incoming_query,
        limit=(
            payload.get("limit")
            or request.args.get("limit")
            or 100
        ),
    )

    status_code = (
        200
        if context.get("ok")
        else 403
    )

    return jsonify(context), status_code



# ===================================================================
# Doughy Maintenance Agent
# ===================================================================

_MAINTENANCE_ALLOWED_ROLES = {
    "admin",
    "supervisor",
    "maintenance",
    "manager",
}

_MAINTENANCE_STATUSES = {
    "open",
    "assigned",
    "in_progress",
    "complete",
}

_MAINTENANCE_PRIORITIES = {
    "low",
    "normal",
    "high",
    "urgent",
}


def _maintenance_payload():
    return request.get_json(silent=True) or {}


def _clean_text(value, maximum=None):
    value = str(value or "").strip()

    if maximum is not None:
        value = value[:maximum]

    return value


def _parse_date_value(value):
    value = _clean_text(value)

    if not value:
        return None

    try:
        return datetime.strptime(
            value,
            "%Y-%m-%d",
        ).date()
    except ValueError:
        return None


def _parse_time_value(value):
    value = _clean_text(value)

    if not value:
        return None

    for fmt in ("%H:%M", "%I:%M %p"):
        try:
            return datetime.strptime(
                value,
                fmt,
            ).time()
        except ValueError:
            continue

    return None


def _parse_optional_int(value):
    if value in (None, ""):
        return None

    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None

    if parsed < 0:
        return None

    return parsed


def _resolve_requesting_user(payload):
    user_id = payload.get("requesting_user_id")
    username = _clean_text(
        payload.get("requesting_username"),
        80,
    )

    user = None

    if user_id not in (None, ""):
        try:
            user = User.query.get(int(user_id))
        except (TypeError, ValueError):
            user = None

    if not user and username:
        user = (
            User.query
            .filter(
                db.func.lower(User.username)
                == username.lower()
            )
            .first()
        )

    if not user or not user.is_active:
        return None

    supplied_role = _clean_text(
        payload.get("requesting_role"),
        50,
    ).lower()

    if supplied_role and supplied_role != user.role:
        return None

    return user


def _visible_store_numbers_for_user(user):
    if user.role in {"admin", "maintenance"}:
        rows = (
            Store.query
            .filter(Store.is_active == True)
            .all()
        )

    elif user.role == "supervisor":
        rows = (
            Store.query
            .filter(
                Store.area_name == user.area_name,
                Store.is_active == True,
            )
            .all()
        )

    elif user.role == "manager":
        rows = (
            Store.query
            .filter(
                Store.store_number == user.store_number,
                Store.is_active == True,
            )
            .all()
        )

    else:
        rows = []

    return {
        row.store_number
        for row in rows
        if row.store_number
    }


def _maintenance_actor_or_error(payload):
    user = _resolve_requesting_user(payload)

    if not user:
        return None, (
            jsonify({
                "ok": False,
                "error": (
                    "The requesting BPI Ops user could not "
                    "be verified."
                ),
            }),
            403,
        )

    if user.role not in _MAINTENANCE_ALLOWED_ROLES:
        return None, (
            jsonify({
                "ok": False,
                "error": (
                    "This user does not have maintenance access."
                ),
            }),
            403,
        )

    return user, None


def _ticket_dict(ticket):
    return {
        "id": ticket.id,
        "store_number": ticket.store_number,
        "title": ticket.title,
        "details": ticket.details or "",
        "status": ticket.status,
        "assigned_to": ticket.assigned_to,
        "scheduled_date": (
            ticket.scheduled_date.isoformat()
            if ticket.scheduled_date
            else None
        ),
        "scheduled_time": (
            ticket.scheduled_time.strftime("%H:%M")
            if ticket.scheduled_time
            else None
        ),
        "estimated_minutes": ticket.estimated_minutes,
        "priority": ticket.priority,
        "source_type": ticket.source_type,
        "created_at": (
            ticket.created_at.isoformat()
            if ticket.created_at
            else None
        ),
    }


def _equipment_dict(equipment):
    return {
        "id": equipment.id,
        "store_number": equipment.store_number,
        "equipment_type": equipment.equipment_type,
        "equipment_name": equipment.equipment_name,
        "brand": equipment.brand,
        "model_number": equipment.model_number,
        "serial_number": equipment.serial_number,
        "install_date": (
            equipment.install_date.isoformat()
            if equipment.install_date
            else None
        ),
        "warranty_expires_on": (
            equipment.warranty_expires_on.isoformat()
            if equipment.warranty_expires_on
            else None
        ),
        "vendor_name": equipment.vendor_name,
        "notes": equipment.notes or "",
        "is_active": bool(equipment.is_active),
    }


def _valid_assignee_names():
    users = (
        User.query
        .filter(
            User.role == "maintenance",
            User.is_active == True,
        )
        .order_by(User.name.asc())
        .all()
    )

    return {
        user.name.strip()
        for user in users
        if user.name and user.name.strip()
    }


def _action_preview(payload, user):
    action = _clean_text(
        payload.get("action"),
        80,
    ).lower()

    visible_stores = _visible_store_numbers_for_user(user)

    if action == "create_ticket":
        store_number = _clean_text(
            payload.get("store_number"),
            10,
        )

        title = _clean_text(
            payload.get("title"),
            255,
        )

        if store_number not in visible_stores:
            return None, "Invalid or unauthorized store."

        if not title:
            return None, "A maintenance title is required."

        assigned_to = _clean_text(
            payload.get("assigned_to"),
            120,
        ) or None

        if (
            assigned_to
            and assigned_to not in _valid_assignee_names()
        ):
            return None, "Invalid maintenance assignee."

        scheduled_date = _parse_date_value(
            payload.get("scheduled_date")
        )

        scheduled_time = _parse_time_value(
            payload.get("scheduled_time")
        )

        if payload.get("scheduled_date") and not scheduled_date:
            return None, "Invalid scheduled date."

        if payload.get("scheduled_time") and not scheduled_time:
            return None, "Invalid scheduled time."

        if scheduled_time and not scheduled_date:
            return None, (
                "A scheduled date is required when "
                "a scheduled time is provided."
            )

        priority = _clean_text(
            payload.get("priority"),
            30,
        ).lower() or "normal"

        if priority not in _MAINTENANCE_PRIORITIES:
            return None, "Invalid maintenance priority."

        return {
            "action": action,
            "confirmation_required": True,
            "summary": (
                f"Create maintenance task for Store "
                f"{store_number}: {title}"
            ),
            "proposed": {
                "store_number": store_number,
                "title": title,
                "details": _clean_text(
                    payload.get("details"),
                ),
                "assigned_to": assigned_to,
                "scheduled_date": (
                    scheduled_date.isoformat()
                    if scheduled_date
                    else None
                ),
                "scheduled_time": (
                    scheduled_time.strftime("%H:%M")
                    if scheduled_time
                    else None
                ),
                "estimated_minutes": _parse_optional_int(
                    payload.get("estimated_minutes")
                ),
                "priority": priority,
            },
        }, None

    ticket_id = payload.get("ticket_id")

    try:
        ticket_id = int(ticket_id)
    except (TypeError, ValueError):
        return None, "A valid ticket ID is required."

    ticket = MaintenanceTicket.query.get(ticket_id)

    if not ticket:
        return None, "Maintenance ticket not found."

    if ticket.store_number not in visible_stores:
        return None, "You do not have access to this ticket."

    before = _ticket_dict(ticket)

    if action == "complete_ticket":
        return {
            "action": action,
            "confirmation_required": True,
            "summary": (
                f"Complete ticket #{ticket.id} for Store "
                f"{ticket.store_number}: {ticket.title}"
            ),
            "before": before,
            "proposed": {
                **before,
                "status": "complete",
                "completion_note": _clean_text(
                    payload.get("completion_note"),
                ),
            },
        }, None

    if action in {
        "update_ticket",
        "schedule_ticket",
        "assign_ticket",
        "start_ticket",
        "reopen_ticket",
    }:
        proposed = dict(before)

        if action == "start_ticket":
            proposed["status"] = "in_progress"

        elif action == "reopen_ticket":
            proposed["status"] = "open"

        else:
            if "title" in payload:
                title = _clean_text(
                    payload.get("title"),
                    255,
                )

                if not title:
                    return None, "Ticket title cannot be empty."

                proposed["title"] = title

            if "details" in payload:
                proposed["details"] = _clean_text(
                    payload.get("details")
                )

            if "status" in payload:
                status = _clean_text(
                    payload.get("status"),
                    50,
                ).lower()

                if status not in _MAINTENANCE_STATUSES:
                    return None, "Invalid maintenance status."

                proposed["status"] = status

            if "priority" in payload:
                priority = _clean_text(
                    payload.get("priority"),
                    30,
                ).lower()

                if priority not in _MAINTENANCE_PRIORITIES:
                    return None, "Invalid maintenance priority."

                proposed["priority"] = priority

            if "assigned_to" in payload:
                assigned_to = _clean_text(
                    payload.get("assigned_to"),
                    120,
                ) or None

                if (
                    assigned_to
                    and assigned_to not in _valid_assignee_names()
                ):
                    return None, "Invalid maintenance assignee."

                proposed["assigned_to"] = assigned_to

            if "scheduled_date" in payload:
                raw_date = payload.get("scheduled_date")
                scheduled_date = _parse_date_value(raw_date)

                if raw_date and not scheduled_date:
                    return None, "Invalid scheduled date."

                proposed["scheduled_date"] = (
                    scheduled_date.isoformat()
                    if scheduled_date
                    else None
                )

            if "scheduled_time" in payload:
                raw_time = payload.get("scheduled_time")
                scheduled_time = _parse_time_value(raw_time)

                if raw_time and not scheduled_time:
                    return None, "Invalid scheduled time."

                proposed["scheduled_time"] = (
                    scheduled_time.strftime("%H:%M")
                    if scheduled_time
                    else None
                )

            if "estimated_minutes" in payload:
                proposed["estimated_minutes"] = (
                    _parse_optional_int(
                        payload.get("estimated_minutes")
                    )
                )

        if (
            proposed.get("scheduled_time")
            and not proposed.get("scheduled_date")
        ):
            return None, (
                "A scheduled date is required when "
                "a scheduled time is provided."
            )

        if (
            proposed.get("assigned_to")
            and proposed.get("status") == "open"
        ):
            proposed["status"] = "assigned"

        return {
            "action": action,
            "confirmation_required": True,
            "summary": (
                f"Update ticket #{ticket.id} for Store "
                f"{ticket.store_number}: {ticket.title}"
            ),
            "before": before,
            "proposed": proposed,
        }, None

    return None, "Unsupported maintenance action."


def _record_maintenance_action(
    *,
    user,
    action_type,
    payload,
    target_type,
    target_id=None,
    store_number=None,
    before=None,
    after=None,
    status="completed",
    error_message=None,
):
    row = MaintenanceAgentAction(
        requesting_user_id=user.id,
        requesting_username=user.username,
        requesting_role=user.role,
        source=_clean_text(
            payload.get("source"),
            50,
        ) or "doughy",
        action_type=action_type,
        target_type=target_type,
        target_id=target_id,
        store_number=store_number,
        original_message=_clean_text(
            payload.get("original_message"),
        ) or None,
        request_json=json.dumps(
            payload,
            default=str,
            sort_keys=True,
        ),
        before_json=(
            json.dumps(
                before,
                default=str,
                sort_keys=True,
            )
            if before is not None
            else None
        ),
        after_json=(
            json.dumps(
                after,
                default=str,
                sort_keys=True,
            )
            if after is not None
            else None
        ),
        status=status,
        error_message=error_message,
    )

    db.session.add(row)
    return row


@doughy_api_bp.route(
    "/maintenance/tickets",
    methods=["GET", "POST"],
)
def maintenance_tickets():
    if not _authorized():
        return jsonify({
            "ok": False,
            "error": "Unauthorized.",
        }), 401

    payload = (
        _maintenance_payload()
        if request.method == "POST"
        else request.args.to_dict()
    )

    user, error_response = _maintenance_actor_or_error(
        payload
    )

    if error_response:
        return error_response

    visible_stores = _visible_store_numbers_for_user(user)

    requested_store = _clean_text(
        payload.get("store_number")
        or payload.get("store"),
        10,
    )

    if requested_store:
        if requested_store not in visible_stores:
            return jsonify({
                "ok": False,
                "error": "Invalid or unauthorized store.",
            }), 403

        visible_stores = {requested_store}

    query = (
        MaintenanceTicket.query
        .filter(
            MaintenanceTicket.store_number.in_(
                visible_stores
            )
        )
    )

    requested_status = _clean_text(
        payload.get("status"),
        50,
    ).lower()

    if requested_status:
        if requested_status not in _MAINTENANCE_STATUSES:
            return jsonify({
                "ok": False,
                "error": "Invalid maintenance status.",
            }), 400

        query = query.filter(
            MaintenanceTicket.status == requested_status
        )

    assigned_to = _clean_text(
        payload.get("assigned_to")
        or payload.get("employee"),
        120,
    )

    if assigned_to:
        query = query.filter(
            MaintenanceTicket.assigned_to == assigned_to
        )

    tickets = (
        query
        .order_by(
            MaintenanceTicket.scheduled_date.asc(),
            MaintenanceTicket.scheduled_time.asc(),
            MaintenanceTicket.created_at.asc(),
            MaintenanceTicket.id.asc(),
        )
        .limit(250)
        .all()
    )

    return jsonify({
        "ok": True,
        "requesting_user": {
            "id": user.id,
            "username": user.username,
            "name": user.name,
            "role": user.role,
        },
        "visible_store_count": len(visible_stores),
        "count": len(tickets),
        "tickets": [
            _ticket_dict(ticket)
            for ticket in tickets
        ],
    })


@doughy_api_bp.route(
    "/maintenance/actions/preview",
    methods=["POST"],
)
def maintenance_action_preview():
    if not _authorized():
        return jsonify({
            "ok": False,
            "error": "Unauthorized.",
        }), 401

    payload = _maintenance_payload()

    user, error_response = _maintenance_actor_or_error(
        payload
    )

    if error_response:
        return error_response

    preview, error = _action_preview(
        payload,
        user,
    )

    if error:
        return jsonify({
            "ok": False,
            "error": error,
        }), 400

    return jsonify({
        "ok": True,
        "requesting_user": {
            "id": user.id,
            "username": user.username,
            "name": user.name,
            "role": user.role,
        },
        "preview": preview,
    })


@doughy_api_bp.route(
    "/maintenance/actions/execute",
    methods=["POST"],
)
def maintenance_action_execute():
    if not _authorized():
        return jsonify({
            "ok": False,
            "error": "Unauthorized.",
        }), 401

    payload = _maintenance_payload()

    if payload.get("confirmed") is not True:
        return jsonify({
            "ok": False,
            "error": (
                "This action must be explicitly confirmed."
            ),
        }), 400

    user, error_response = _maintenance_actor_or_error(
        payload
    )

    if error_response:
        return error_response

    preview, error = _action_preview(
        payload,
        user,
    )

    if error:
        return jsonify({
            "ok": False,
            "error": error,
        }), 400

    action = preview["action"]
    proposed = preview.get("proposed") or {}
    before = preview.get("before")

    try:
        if action == "create_ticket":
            ticket = MaintenanceTicket(
                store_number=proposed["store_number"],
                title=proposed["title"],
                details=proposed.get("details") or None,
                source_type="doughy_connect",
                status=(
                    "assigned"
                    if proposed.get("assigned_to")
                    else "open"
                ),
                assigned_to=proposed.get("assigned_to"),
                scheduled_date=_parse_date_value(
                    proposed.get("scheduled_date")
                ),
                scheduled_time=_parse_time_value(
                    proposed.get("scheduled_time")
                ),
                estimated_minutes=proposed.get(
                    "estimated_minutes"
                ),
                priority=proposed.get("priority") or "normal",
            )

            db.session.add(ticket)
            db.session.flush()

        else:
            ticket = MaintenanceTicket.query.get(
                int(payload["ticket_id"])
            )

            if action == "complete_ticket":
                completion_note = proposed.get(
                    "completion_note"
                )

                ticket.status = "complete"

                if completion_note:
                    existing = ticket.details or ""
                    divider = "\n\n" if existing else ""

                    ticket.details = (
                        f"{existing}{divider}"
                        f"Completion note: {completion_note}"
                    )

            else:
                ticket.title = proposed["title"]
                ticket.details = (
                    proposed.get("details") or None
                )
                ticket.status = proposed["status"]
                ticket.assigned_to = proposed.get(
                    "assigned_to"
                )
                ticket.scheduled_date = _parse_date_value(
                    proposed.get("scheduled_date")
                )
                ticket.scheduled_time = _parse_time_value(
                    proposed.get("scheduled_time")
                )
                ticket.estimated_minutes = proposed.get(
                    "estimated_minutes"
                )
                ticket.priority = (
                    proposed.get("priority")
                    or "normal"
                )

        db.session.flush()

        after = _ticket_dict(ticket)

        audit = _record_maintenance_action(
            user=user,
            action_type=action,
            payload=payload,
            target_type="maintenance_ticket",
            target_id=ticket.id,
            store_number=ticket.store_number,
            before=before,
            after=after,
        )

        db.session.commit()

        return jsonify({
            "ok": True,
            "message": preview["summary"],
            "ticket": after,
            "audit_action_id": audit.id,
        })

    except Exception as exc:
        db.session.rollback()

        try:
            _record_maintenance_action(
                user=user,
                action_type=action,
                payload=payload,
                target_type="maintenance_ticket",
                target_id=(
                    int(payload.get("ticket_id"))
                    if payload.get("ticket_id")
                    else None
                ),
                store_number=(
                    proposed.get("store_number")
                    or (
                        before.get("store_number")
                        if before
                        else None
                    )
                ),
                before=before,
                after=None,
                status="failed",
                error_message=str(exc),
            )

            db.session.commit()
        except Exception:
            db.session.rollback()

        return jsonify({
            "ok": False,
            "error": (
                "The maintenance action could not be completed."
            ),
            "detail": str(exc),
        }), 500


@doughy_api_bp.route(
    "/maintenance/equipment",
    methods=["GET", "POST"],
)
def maintenance_equipment():
    if not _authorized():
        return jsonify({
            "ok": False,
            "error": "Unauthorized.",
        }), 401

    payload = (
        _maintenance_payload()
        if request.method == "POST"
        else request.args.to_dict()
    )

    user, error_response = _maintenance_actor_or_error(
        payload
    )

    if error_response:
        return error_response

    visible_stores = _visible_store_numbers_for_user(user)

    if request.method == "GET":
        requested_store = _clean_text(
            payload.get("store_number")
            or payload.get("store"),
            10,
        )

        if requested_store:
            if requested_store not in visible_stores:
                return jsonify({
                    "ok": False,
                    "error": "Invalid or unauthorized store.",
                }), 403

            visible_stores = {requested_store}

        rows = (
            MaintenanceEquipment.query
            .filter(
                MaintenanceEquipment.store_number.in_(
                    visible_stores
                ),
                MaintenanceEquipment.is_active == True,
            )
            .order_by(
                MaintenanceEquipment.store_number.asc(),
                MaintenanceEquipment.equipment_type.asc(),
                MaintenanceEquipment.equipment_name.asc(),
            )
            .all()
        )

        return jsonify({
            "ok": True,
            "count": len(rows),
            "equipment": [
                _equipment_dict(row)
                for row in rows
            ],
        })

    if payload.get("confirmed") is not True:
        return jsonify({
            "ok": False,
            "error": (
                "Equipment creation must be explicitly confirmed."
            ),
        }), 400

    store_number = _clean_text(
        payload.get("store_number"),
        10,
    )

    if store_number not in visible_stores:
        return jsonify({
            "ok": False,
            "error": "Invalid or unauthorized store.",
        }), 403

    equipment_type = _clean_text(
        payload.get("equipment_type"),
        100,
    )

    equipment_name = _clean_text(
        payload.get("equipment_name"),
        160,
    )

    if not equipment_type or not equipment_name:
        return jsonify({
            "ok": False,
            "error": (
                "Equipment type and equipment name are required."
            ),
        }), 400

    row = MaintenanceEquipment(
        store_number=store_number,
        equipment_type=equipment_type,
        equipment_name=equipment_name,
        brand=_clean_text(
            payload.get("brand"),
            120,
        ) or None,
        model_number=_clean_text(
            payload.get("model_number"),
            120,
        ) or None,
        serial_number=_clean_text(
            payload.get("serial_number"),
            160,
        ) or None,
        install_date=_parse_date_value(
            payload.get("install_date")
        ),
        warranty_expires_on=_parse_date_value(
            payload.get("warranty_expires_on")
        ),
        vendor_name=_clean_text(
            payload.get("vendor_name"),
            160,
        ) or None,
        notes=_clean_text(
            payload.get("notes"),
        ) or None,
        created_by_user_id=user.id,
    )

    db.session.add(row)
    db.session.flush()

    equipment_snapshot = _equipment_dict(row)

    audit = _record_maintenance_action(
        user=user,
        action_type="create_equipment",
        payload=payload,
        target_type="maintenance_equipment",
        target_id=row.id,
        store_number=row.store_number,
        before=None,
        after=equipment_snapshot,
    )

    db.session.commit()

    return jsonify({
        "ok": True,
        "message": (
            f"Equipment record created for Store "
            f"{row.store_number}."
        ),
        "equipment": equipment_snapshot,
        "audit_action_id": audit.id,
    }), 201
