from __future__ import annotations

from datetime import date, datetime, time
from typing import Any

from app.models import (
    CashLog,
    DailyChecklist,
    DailyPrep,
    DWPRecord,
    FormSubmission,
    HRDocument,
    HRDocumentRecipient,
    MaintenanceTicket,
    NightlyNumbersReport,
    Store,
    SVRReport,
    User,
    VerificationReport,
)

from app.services.doughy_data_gateway import (
    build_doughy_context,
    visible_store_numbers,
)


def _clean(value: Any) -> str:
    return " ".join(
        str(value or "").strip().split()
    )


def _iso(value: Any) -> str | None:
    if value is None:
        return None

    if hasattr(value, "isoformat"):
        return value.isoformat()

    return str(value)


def _parse_date(
    value: Any,
    default: date | None = None,
) -> date | None:
    if isinstance(value, datetime):
        return value.date()

    if isinstance(value, date):
        return value

    if value:
        try:
            return date.fromisoformat(
                str(value).strip()
            )
        except ValueError:
            return default

    return default


def _limit(value: Any) -> int:
    try:
        parsed = int(value or 100)
    except (TypeError, ValueError):
        parsed = 100

    return max(
        1,
        min(parsed, 500),
    )


def _datetime_start(
    value: date | None,
) -> datetime | None:
    if not value:
        return None

    return datetime.combine(
        value,
        time.min,
    )


def _datetime_end(
    value: date | None,
) -> datetime | None:
    if not value:
        return None

    return datetime.combine(
        value,
        time.max,
    )


def _role(
    user_context: dict[str, Any],
) -> str:
    return str(
        user_context.get("role") or ""
    ).strip().lower()


def _all_active_store_numbers() -> set[str]:
    return {
        str(row.store_number)
        for row in Store.query.filter(
            Store.is_active.is_(True)
        ).all()
        if row.store_number
    }


def _people_store_scope(
    user_context: dict[str, Any],
) -> set[str]:
    role = _role(user_context)

    if role in {
        "admin",
        "hr",
        "coach",
    }:
        return _all_active_store_numbers()

    return visible_store_numbers(
        user_context
    )


def _sensitive_access_allowed(
    user_context: dict[str, Any],
) -> bool:
    return _role(user_context) in {
        "admin",
        "hr",
        "supervisor",
    }


def _permission_denied(
    *,
    module: str,
    user_context: dict[str, Any],
) -> dict[str, Any]:
    return {
        "ok": False,
        "error": (
            f"The requester does not have permission "
            f"to access the {module} module."
        ),
        "module": module,
        "scope": {
            "role": _role(user_context),
        },
    }


def _serialize_user(
    row: User,
) -> dict[str, Any]:
    return {
        "id": row.id,
        "name": row.name,
        "username": row.username,
        "role": row.role,
        "position": row.position,
        "area_name": row.area_name,
        "store_number": row.store_number,
        "is_active": bool(row.is_active),
    }


def _users_context(
    *,
    user_context: dict[str, Any],
    store: str | None,
    query_text: str,
    limit: int,
) -> dict[str, Any]:
    allowed_stores = _people_store_scope(
        user_context
    )

    query = User.query

    role = _role(user_context)

    if role not in {
        "admin",
        "hr",
        "coach",
    }:
        query = query.filter(
            User.store_number.in_(
                allowed_stores
            )
        )

    if store:
        if (
            role
            not in {
                "admin",
                "hr",
                "coach",
            }
            and store not in allowed_stores
        ):
            return {
                "ok": False,
                "error": (
                    "Store is outside the "
                    "requester's visible scope."
                ),
            }

        query = query.filter(
            User.store_number == store
        )

    if query_text:
        like = f"%{query_text}%"

        query = query.filter(
            db_or(
                User.name.ilike(like),
                User.username.ilike(like),
                User.role.ilike(like),
                User.position.ilike(like),
                User.store_number.ilike(like),
                User.area_name.ilike(like),
            )
        )

    rows = (
        query
        .order_by(
            User.is_active.desc(),
            User.store_number.asc(),
            User.name.asc(),
        )
        .limit(limit)
        .all()
    )

    return {
        "ok": True,
        "module": "users",
        "count": len(rows),
        "users": [
            _serialize_user(row)
            for row in rows
        ],
    }


def _serialize_dwp(
    row: DWPRecord,
) -> dict[str, Any]:
    return {
        "id": row.id,
        "conversation_date": _iso(
            row.conversation_date
        ),
        "infraction_date": _iso(
            row.infraction_date
        ),
        "store_number": row.store_number,
        "team_member_id": (
            row.team_member_id
        ),
        "team_member_name": (
            row.team_member_name_snapshot
        ),
        "submitted_by_name": (
            row.submitted_by_name_snapshot
        ),
        "discussion_type": (
            row.discussion_type
        ),
        "category": row.category,
        "status": row.status,
        "previous_conversations": _clean(
            row.previous_conversations
        ),
        "expected_performance": _clean(
            row.expected_performance
        ),
        "actual_performance": _clean(
            row.actual_performance
        ),
        "team_member_statement": _clean(
            row.team_member_statement
        ),
        "business_reason": _clean(
            row.business_reason
        ),
        "logical_consequence": _clean(
            row.logical_consequence
        ),
        "team_member_agrees_to": _clean(
            row.team_member_agrees_to
        ),
        "additional_comments": _clean(
            row.additional_comments
        ),
        "acknowledged_at": _iso(
            row.acknowledged_at
        ),
        "acknowledged_name": (
            row.acknowledged_name
        ),
        "created_at": _iso(
            row.created_at
        ),
    }


def _dwp_context(
    *,
    user_context: dict[str, Any],
    store: str | None,
    date_from: date | None,
    date_to: date | None,
    status: str,
    employee: str,
    query_text: str,
    limit: int,
) -> dict[str, Any]:
    if not _sensitive_access_allowed(
        user_context
    ):
        return _permission_denied(
            module="dwp",
            user_context=user_context,
        )

    allowed_stores = _people_store_scope(
        user_context
    )

    query = DWPRecord.query

    if _role(user_context) == "supervisor":
        query = query.filter(
            DWPRecord.store_number.in_(
                allowed_stores
            )
        )

    if store:
        if (
            _role(user_context)
            == "supervisor"
            and store not in allowed_stores
        ):
            return {
                "ok": False,
                "error": (
                    "Store is outside the "
                    "requester's visible scope."
                ),
            }

        query = query.filter(
            DWPRecord.store_number == store
        )

    if date_from:
        query = query.filter(
            DWPRecord.conversation_date
            >= date_from
        )

    if date_to:
        query = query.filter(
            DWPRecord.conversation_date
            <= date_to
        )

    if status:
        query = query.filter(
            DWPRecord.status == status
        )

    if employee:
        query = query.filter(
            DWPRecord
            .team_member_name_snapshot
            .ilike(f"%{employee}%")
        )

    if query_text:
        like = f"%{query_text}%"

        query = query.filter(
            db_or(
                DWPRecord
                .team_member_name_snapshot
                .ilike(like),
                DWPRecord
                .submitted_by_name_snapshot
                .ilike(like),
                DWPRecord
                .discussion_type
                .ilike(like),
                DWPRecord
                .category
                .ilike(like),
                DWPRecord
                .actual_performance
                .ilike(like),
                DWPRecord
                .expected_performance
                .ilike(like),
            )
        )

    rows = (
        query
        .order_by(
            DWPRecord
            .conversation_date
            .desc(),
            DWPRecord.created_at.desc(),
        )
        .limit(limit)
        .all()
    )

    return {
        "ok": True,
        "module": "dwp",
        "confidential": True,
        "count": len(rows),
        "records": [
            _serialize_dwp(row)
            for row in rows
        ],
    }


def _hr_documents_context(
    *,
    user_context: dict[str, Any],
    store: str | None,
    status: str,
    employee: str,
    query_text: str,
    limit: int,
) -> dict[str, Any]:
    if not _sensitive_access_allowed(
        user_context
    ):
        return _permission_denied(
            module="hr_documents",
            user_context=user_context,
        )

    allowed_stores = _people_store_scope(
        user_context
    )

    query = (
        HRDocumentRecipient.query
        .join(HRDocument)
        .join(User)
    )

    if _role(user_context) == "supervisor":
        query = query.filter(
            User.store_number.in_(
                allowed_stores
            )
        )

    if store:
        if (
            _role(user_context)
            == "supervisor"
            and store not in allowed_stores
        ):
            return {
                "ok": False,
                "error": (
                    "Store is outside the "
                    "requester's visible scope."
                ),
            }

        query = query.filter(
            User.store_number == store
        )

    if status:
        query = query.filter(
            HRDocumentRecipient.status
            == status
        )

    if employee:
        query = query.filter(
            User.name.ilike(
                f"%{employee}%"
            )
        )

    if query_text:
        like = f"%{query_text}%"

        query = query.filter(
            db_or(
                HRDocument.title.ilike(like),
                HRDocument.description.ilike(
                    like
                ),
                User.name.ilike(like),
            )
        )

    rows = (
        query
        .order_by(
            HRDocumentRecipient
            .status
            .asc(),
            HRDocument.due_date.asc(),
            HRDocumentRecipient
            .assigned_at
            .desc(),
        )
        .limit(limit)
        .all()
    )

    results = []

    for row in rows:
        results.append({
            "recipient_id": row.id,
            "document_id": row.document_id,
            "document_title": (
                row.document.title
            ),
            "document_description": _clean(
                row.document.description
            ),
            "document_due_date": _iso(
                row.document.due_date
            ),
            "document_active": bool(
                row.document.is_active
            ),
            "employee_id": row.user.id,
            "employee_name": row.user.name,
            "employee_role": row.user.role,
            "employee_store": (
                row.user.store_number
            ),
            "status": row.status,
            "assigned_at": _iso(
                row.assigned_at
            ),
            "email_sent_at": _iso(
                row.email_sent_at
            ),
            "email_error": _clean(
                row.email_error
            ),
            "acknowledged_at": _iso(
                row.acknowledged_at
            ),
            "acknowledged_name": (
                row.acknowledged_name
            ),
        })

    return {
        "ok": True,
        "module": "hr_documents",
        "confidential": True,
        "count": len(results),
        "recipients": results,
    }


def _forms_context(
    *,
    user_context: dict[str, Any],
    store: str | None,
    date_from: date | None,
    date_to: date | None,
    status: str,
    query_text: str,
    limit: int,
) -> dict[str, Any]:
    allowed_stores = visible_store_numbers(
        user_context
    )

    query = FormSubmission.query

    if store:
        if store not in allowed_stores:
            return {
                "ok": False,
                "error": (
                    "Store is outside the "
                    "requester's visible scope."
                ),
            }

        query = query.filter(
            FormSubmission.store_number
            == store
        )
    else:
        query = query.filter(
            FormSubmission.store_number.in_(
                allowed_stores
            )
        )

    start = _datetime_start(
        date_from
    )
    end = _datetime_end(
        date_to
    )

    if start:
        query = query.filter(
            FormSubmission.submitted_at
            >= start
        )

    if end:
        query = query.filter(
            FormSubmission.submitted_at
            <= end
        )

    if status:
        query = query.filter(
            FormSubmission.workflow_status
            == status
        )

    if query_text:
        query = query.join(
            FormSubmission.template
        ).filter(
            FormSubmission.template
            .property.mapper.class_
            .title
            .ilike(
                f"%{query_text}%"
            )
        )

    rows = (
        query
        .order_by(
            FormSubmission
            .submitted_at
            .desc()
        )
        .limit(limit)
        .all()
    )

    results = []

    for row in rows:
        results.append({
            "id": row.id,
            "template_id": (
                row.form_template_id
            ),
            "template_title": (
                row.template.title
            ),
            "store_number": (
                row.store_number
            ),
            "submitted_by": (
                row.submitted_by.name
                if row.submitted_by
                else None
            ),
            "submitted_at": _iso(
                row.submitted_at
            ),
            "workflow_status": (
                row.workflow_status
            ),
            "score_percent": (
                row.score_percent
            ),
            "grade": row.grade,
            "failed_count": (
                row.failed_count
            ),
            "critical_failed_count": (
                row.critical_failed_count
            ),
            "answers": [
                {
                    "question": (
                        answer.question_text
                    ),
                    "answer": _clean(
                        answer.answer_text
                    ),
                    "is_failure": bool(
                        answer.is_failure
                    ),
                    "is_critical_failure": (
                        bool(
                            answer
                            .is_critical_failure
                        )
                    ),
                }
                for answer in row.answers
            ],
        })

    return {
        "ok": True,
        "module": "forms",
        "count": len(results),
        "submissions": results,
    }


def _prep_context(
    *,
    user_context: dict[str, Any],
    store: str | None,
    date_from: date | None,
    date_to: date | None,
    limit: int,
) -> dict[str, Any]:
    allowed_stores = visible_store_numbers(
        user_context
    )

    query = DailyPrep.query

    if store:
        if store not in allowed_stores:
            return {
                "ok": False,
                "error": (
                    "Store is outside the "
                    "requester's visible scope."
                ),
            }

        query = query.filter(
            DailyPrep.store_number == store
        )
    else:
        query = query.filter(
            DailyPrep.store_number.in_(
                allowed_stores
            )
        )

    if date_from:
        query = query.filter(
            DailyPrep.prep_date >= date_from
        )

    if date_to:
        query = query.filter(
            DailyPrep.prep_date <= date_to
        )

    rows = (
        query
        .order_by(
            DailyPrep.prep_date.desc(),
            DailyPrep.id.desc(),
        )
        .limit(limit)
        .all()
    )

    results = []

    for row in rows:
        completed = sum(
            1
            for item in row.items
            if item.is_completed
        )

        results.append({
            "id": row.id,
            "store_number": (
                row.store_number
            ),
            "prep_date": _iso(
                row.prep_date
            ),
            "created_at": _iso(
                row.created_at
            ),
            "item_count": len(row.items),
            "completed_count": completed,
            "percent_complete": round(
                (
                    completed
                    / len(row.items)
                    * 100
                )
                if row.items
                else 0,
                1,
            ),
            "incomplete_items": [
                {
                    "section_name": (
                        item.section_name
                    ),
                    "item_name": (
                        item.item_name
                    ),
                    "build_to": (
                        item.build_to
                    ),
                }
                for item in row.items
                if not item.is_completed
            ][:30],
        })

    return {
        "ok": True,
        "module": "prep",
        "count": len(results),
        "daily_preps": results,
    }


def _checklist_history(
    *,
    user_context: dict[str, Any],
    store: str | None,
    date_from: date | None,
    date_to: date | None,
    status: str,
    limit: int,
) -> dict[str, Any]:
    allowed_stores = visible_store_numbers(
        user_context
    )

    query = DailyChecklist.query

    if store:
        if store not in allowed_stores:
            return {
                "ok": False,
                "error": (
                    "Store is outside the "
                    "requester's visible scope."
                ),
            }

        query = query.filter(
            DailyChecklist.store_number
            == store
        )
    else:
        query = query.filter(
            DailyChecklist.store_number.in_(
                allowed_stores
            )
        )

    if date_from:
        query = query.filter(
            DailyChecklist.checklist_date
            >= date_from
        )

    if date_to:
        query = query.filter(
            DailyChecklist.checklist_date
            <= date_to
        )

    if status:
        query = query.filter(
            DailyChecklist.status == status
        )

    rows = (
        query
        .order_by(
            DailyChecklist
            .checklist_date
            .desc(),
            DailyChecklist.id.desc(),
        )
        .limit(limit)
        .all()
    )

    return {
        "ok": True,
        "module": "checklist_history",
        "count": len(rows),
        "records": [
            {
                "id": row.id,
                "store_number": (
                    row.store_number
                ),
                "checklist_date": _iso(
                    row.checklist_date
                ),
                "manager_on_duty": (
                    row.manager_on_duty
                ),
                "opening_manager": (
                    row.opening_manager
                ),
                "closing_manager": (
                    row.closing_manager
                ),
                "status": row.status,
                "percent_complete": (
                    row.percent_complete
                ),
                "integrity_score": (
                    row.integrity_score
                ),
                "created_at": _iso(
                    row.created_at
                ),
            }
            for row in rows
        ],
    }


def _maintenance_schedule_context(
    *,
    user_context: dict[str, Any],
    store: str | None,
    date_from: date | None,
    date_to: date | None,
    status: str,
    employee: str,
    limit: int,
) -> dict[str, Any]:
    allowed_stores = visible_store_numbers(
        user_context
    )

    query = (
        MaintenanceTicket.query
        .filter(
            MaintenanceTicket
            .scheduled_date
            .isnot(None)
        )
    )

    if store:
        if store not in allowed_stores:
            return {
                "ok": False,
                "module": (
                    "maintenance_schedule"
                ),
                "error": (
                    "Store is outside the "
                    "user's visible scope."
                ),
            }

        query = query.filter(
            MaintenanceTicket
            .store_number
            == store
        )

    else:
        query = query.filter(
            MaintenanceTicket
            .store_number
            .in_(allowed_stores)
        )

    if date_from:
        query = query.filter(
            MaintenanceTicket
            .scheduled_date
            >= date_from
        )

    if date_to:
        query = query.filter(
            MaintenanceTicket
            .scheduled_date
            <= date_to
        )

    normalized_status = str(
        status or ""
    ).strip().lower()

    if normalized_status == "completed":
        query = query.filter(
            MaintenanceTicket.status.in_(
                [
                    "submitted",
                    "verified",
                    "completed",
                    "closed",
                ]
            )
        )
    elif normalized_status:
        query = query.filter(
            MaintenanceTicket.status
            == normalized_status
        )

    normalized_employee = (
        employee
        or ""
    ).strip()

    if normalized_employee:
        query = query.filter(
            MaintenanceTicket
            .assigned_to
            .ilike(
                f"%{normalized_employee}%"
            )
        )

    rows = (
        query
        .order_by(
            MaintenanceTicket
            .scheduled_date
            .asc(),
            MaintenanceTicket
            .scheduled_time
            .asc()
            .nulls_last(),
            MaintenanceTicket
            .store_number
            .asc(),
        )
        .limit(limit)
        .all()
    )

    records = []

    for row in rows:
        records.append({
            "id": row.id,
            "store_number": (
                row.store_number
            ),
            "title": row.title,
            "details": (
                row.details
                or ""
            ),
            "status": row.status,
            "assigned_to": (
                row.assigned_to
            ),
            "scheduled_date": _iso(
                row.scheduled_date
            ),
            "scheduled_time": (
                row.scheduled_time
                .isoformat(
                    timespec="minutes"
                )
                if row.scheduled_time
                else None
            ),
            "estimated_minutes": (
                row.estimated_minutes
            ),
            "priority": row.priority,
            "created_at": _iso(
                row.created_at
            ),
        })

    return {
        "ok": True,
        "module": (
            "maintenance_schedule"
        ),
        "count": len(records),
        "records": records,
        "filters": {
            "store": store,
            "date_from": _iso(
                date_from
            ),
            "date_to": _iso(
                date_to
            ),
            "status": (
                status
                or None
            ),
            "employee": (
                normalized_employee
                or None
            ),
        },
    }


def _simple_history_context(
    *,
    module: str,
    user_context: dict[str, Any],
    store: str | None,
    date_from: date | None,
    date_to: date | None,
    status: str,
    employee: str = "",
    limit: int,
) -> dict[str, Any]:
    allowed_stores = visible_store_numbers(
        user_context
    )

    configs = {
        "maintenance_history": {
            "model": MaintenanceTicket,
            "store": (
                MaintenanceTicket
                .store_number
            ),
            "date": (
                MaintenanceTicket.created_at
            ),
        },
        "svr_history": {
            "model": SVRReport,
            "store": SVRReport.store_number,
            "date": SVRReport.visit_date,
        },
        "verification_history": {
            "model": VerificationReport,
            "store": (
                VerificationReport
                .store_number
            ),
            "date": (
                VerificationReport
                .report_date
            ),
        },
        "nightly_history": {
            "model": NightlyNumbersReport,
            "store": (
                NightlyNumbersReport
                .store_number
            ),
            "date": (
                NightlyNumbersReport
                .report_date
            ),
        },
        "cash_history": {
            "model": CashLog,
            "store": CashLog.store_number,
            "date": CashLog.log_date,
        },
    }

    config = configs[module]
    model = config["model"]
    store_column = config["store"]
    date_column = config["date"]

    query = model.query

    if store:
        if store not in allowed_stores:
            return {
                "ok": False,
                "error": (
                    "Store is outside the "
                    "requester's visible scope."
                ),
            }

        query = query.filter(
            store_column == store
        )
    else:
        query = query.filter(
            store_column.in_(
                allowed_stores
            )
        )

    if module == "maintenance_history":
        start = _datetime_start(
            date_from
        )
        end = _datetime_end(
            date_to
        )

        if start:
            query = query.filter(
                date_column >= start
            )

        if end:
            query = query.filter(
                date_column <= end
            )

        normalized_status = str(
            status or ""
        ).strip().lower()

        if normalized_status == "completed":
            query = query.filter(
                MaintenanceTicket.status.in_(
                    [
                        "complete",
                        "completed",
                        "submitted",
                        "verified",
                        "closed",
                    ]
                )
            )
        elif normalized_status:
            query = query.filter(
                MaintenanceTicket.status
                == normalized_status
            )

        normalized_employee = str(
            employee or ""
        ).strip()

        if normalized_employee:
            query = query.filter(
                MaintenanceTicket
                .assigned_to
                .ilike(
                    f"%{normalized_employee}%"
                )
            )

    else:
        if date_from:
            query = query.filter(
                date_column >= date_from
            )

        if date_to:
            query = query.filter(
                date_column <= date_to
            )

    rows = (
        query
        .order_by(
            date_column.desc()
        )
        .limit(limit)
        .all()
    )

    results = []

    for row in rows:
        if module == "maintenance_history":
            results.append({
                "id": row.id,
                "store_number": (
                    row.store_number
                ),
                "title": row.title,
                "details": _clean(
                    row.details
                ),
                "status": row.status,
                "priority": row.priority,
                "assigned_to": (
                    row.assigned_to
                ),
                "scheduled_date": _iso(
                    row.scheduled_date
                ),
                "created_at": _iso(
                    row.created_at
                ),
            })

        elif module == "svr_history":
            results.append({
                "id": row.id,
                "store_number": (
                    row.store_number
                ),
                "visit_date": _iso(
                    row.visit_date
                ),
                "manager_on_duty": (
                    row.manager_on_duty
                ),
                "supervisor_name": (
                    row.supervisor_name
                ),
                "created_at": _iso(
                    row.created_at
                ),
            })

        elif module == "verification_history":
            results.append({
                "id": row.id,
                "store_number": (
                    row.store_number
                ),
                "report_date": _iso(
                    row.report_date
                ),
                "supervisor_name": (
                    row.supervisor_name
                ),
                "created_at": _iso(
                    row.created_at
                ),
            })

        elif module == "nightly_history":
            results.append({
                "id": row.id,
                "store_number": (
                    row.store_number
                ),
                "report_date": _iso(
                    row.report_date
                ),
                "manager_name": (
                    row.manager_name
                ),
                "royalty_sales": (
                    row.royalty_sales
                ),
                "variable_labor": (
                    row.variable_labor
                ),
                "labor_goal": (
                    row.labor_goal
                ),
                "food_variance": (
                    row.food_variance
                ),
                "adt": row.adt,
                "load_time": row.load_time,
                "cash_diff": row.cash_diff,
            })

        elif module == "cash_history":
            results.append({
                "id": row.id,
                "store_number": (
                    row.store_number
                ),
                "log_date": _iso(
                    row.log_date
                ),
                "shift_type": (
                    row.shift_type
                ),
                "cash_over_short": (
                    row.cash_over_short
                ),
                "manager_name": (
                    row.manager_name
                ),
                "created_at": _iso(
                    row.created_at
                ),
            })

    return {
        "ok": True,
        "module": module,
        "count": len(results),
        "records": results,
    }


def build_doughy_universal_context(
    *,
    user_context: dict[str, Any],
    page_context: dict[str, Any],
    requested_store: str | None = None,
    requested_date: Any = None,
    date_from: Any = None,
    date_to: Any = None,
    status: str = "",
    employee: str = "",
    query_text: str = "",
    limit: Any = 100,
) -> dict[str, Any]:
    module = str(
        page_context.get("section")
        or page_context.get("page")
        or "dashboard"
    ).strip().lower()

    parsed_from = _parse_date(
        date_from
    )
    parsed_to = _parse_date(
        date_to
    )

    store = str(
        requested_store or ""
    ).strip() or None

    status = str(
        status or ""
    ).strip()

    employee = str(
        employee or ""
    ).strip()

    query_text = str(
        query_text or ""
    ).strip()

    result_limit = _limit(
        limit
    )

    if module == "users":
        return _users_context(
            user_context=user_context,
            store=store,
            query_text=query_text,
            limit=result_limit,
        )

    if module == "dwp":
        return _dwp_context(
            user_context=user_context,
            store=store,
            date_from=parsed_from,
            date_to=parsed_to,
            status=status,
            employee=employee,
            query_text=query_text,
            limit=result_limit,
        )

    if module == "hr_documents":
        return _hr_documents_context(
            user_context=user_context,
            store=store,
            status=status,
            employee=employee,
            query_text=query_text,
            limit=result_limit,
        )

    if module == "forms":
        return _forms_context(
            user_context=user_context,
            store=store,
            date_from=parsed_from,
            date_to=parsed_to,
            status=status,
            query_text=query_text,
            limit=result_limit,
        )

    if module == "prep":
        return _prep_context(
            user_context=user_context,
            store=store,
            date_from=parsed_from,
            date_to=parsed_to,
            limit=result_limit,
        )

    if module == "maintenance_schedule":
        return _maintenance_schedule_context(
            user_context=user_context,
            store=store,
            date_from=parsed_from,
            date_to=parsed_to,
            status=status,
            employee=employee,
            limit=result_limit,
        )

    if module == "checklist_history":
        return _checklist_history(
            user_context=user_context,
            store=store,
            date_from=parsed_from,
            date_to=parsed_to,
            status=status,
            limit=result_limit,
        )

    if module in {
        "maintenance_history",
        "svr_history",
        "verification_history",
        "nightly_history",
        "cash_history",
    }:
        return _simple_history_context(
            module=module,
            user_context=user_context,
            store=store,
            date_from=parsed_from,
            date_to=parsed_to,
            status=status,
            employee=employee,
            limit=result_limit,
        )

    return build_doughy_context(
        user_context=user_context,
        page_context=page_context,
        requested_store=requested_store,
        requested_date=requested_date,
    )


# Imported late to keep model declarations readable.
from sqlalchemy import or_ as db_or
