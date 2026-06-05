from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from flask import render_template, request, redirect, url_for, session, flash, abort

from app.auth.routes import login_required, role_required
from app.extensions import db
from app.models import User, Store
from app.shift_todos import shift_todos_bp
from app.shift_todos.models import ShiftTodo, ShiftTodoAssignment

APP_TZ = ZoneInfo("America/New_York")


SHIFT_TODO_CREATOR_ROLES = {"admin", "supervisor", "general_manager", "manager"}


def now_et():
    return datetime.now(APP_TZ)


def business_date_et():
    now = now_et()
    if now.hour < 5:
        return (now - timedelta(days=1)).date()
    return now.date()


def current_account_role():
    return (
        session.get("access_role")
        or session.get("account_role")
        or session.get("user_role")
        or session.get("role")
    )


def current_store():
    return session.get("user_store")


def is_multi_store_shift_todo_user():
    return current_account_role() in {"admin", "supervisor"}


def get_available_shift_todo_stores():
    query = Store.query.filter_by(is_active=True)

    if current_account_role() == "supervisor":
        area_name = session.get("area_name") or session.get("user_area")
        if area_name:
            query = query.filter(Store.area_name == area_name)

    return query.order_by(Store.store_number.asc()).all()


def selected_shift_todo_store():
    if is_multi_store_shift_todo_user():
        requested_store = request.args.get("store_number") or request.form.get("store_number")
        stores = get_available_shift_todo_stores()
        allowed_store_numbers = {store.store_number for store in stores}

        if requested_store and requested_store in allowed_store_numbers:
            return requested_store, stores

        if stores:
            return stores[0].store_number, stores

        return None, stores

    return current_store(), []


def current_user_id():
    return session.get("user_id")


def can_create_shift_todos():
    return current_account_role() in SHIFT_TODO_CREATOR_ROLES


def get_store_tms(store_number):
    if not store_number:
        return []

    return User.query.filter_by(
        role="tm",
        store_number=store_number,
        is_active=True,
    ).order_by(User.name.asc()).all()


def get_store_todos(store_number):
    todos = ShiftTodo.query.filter_by(
        store_number=store_number,
    ).filter(
        ShiftTodo.status != "canceled"
    ).order_by(
        ShiftTodo.due_date.desc(),
        ShiftTodo.created_at.desc(),
        ShiftTodo.id.desc(),
    ).all()

    open_todos = []
    completed_todos = []

    for todo in todos:
        if todo.is_fully_completed():
            completed_todos.append(todo)
        else:
            open_todos.append(todo)

    return business_date_et(), open_todos, completed_todos


def get_tm_todos(user_id):
    assignments = ShiftTodoAssignment.query.join(ShiftTodo).filter(
        ShiftTodoAssignment.user_id == user_id,
        ShiftTodo.status != "canceled",
    ).order_by(
        ShiftTodo.due_date.desc(),
        ShiftTodo.created_at.desc(),
        ShiftTodo.id.desc(),
    ).all()

    open_assignments = [item for item in assignments if not item.is_completed]
    completed_assignments = [item for item in assignments if item.is_completed]

    return open_assignments, completed_assignments


def parse_task_titles():
    """
    Supports batch create:
      task_titles = one task per line
    Also supports old fallback:
      title = one task
    """
    raw_batch = request.form.get("task_titles", "").strip()
    if raw_batch:
        titles = [line.strip() for line in raw_batch.splitlines() if line.strip()]
    else:
        single_title = request.form.get("title", "").strip()
        titles = [single_title] if single_title else []

    deduped = []
    seen = set()

    for title in titles:
        normalized = title.lower()
        if normalized not in seen:
            deduped.append(title)
            seen.add(normalized)

    return deduped


@shift_todos_bp.route("/", methods=["GET"])
@login_required
@role_required("admin", "supervisor", "general_manager", "manager", "tm")
def index():
    account_role = current_account_role()
    store_number, available_stores = selected_shift_todo_store()

    if account_role in SHIFT_TODO_CREATOR_ROLES:
        if not store_number:
            flash("No active stores are available for Shift To-Dos.", "error")
            return redirect(url_for("dashboard.home"))

        today, open_todos, completed_todos = get_store_todos(store_number)
        tms = get_store_tms(store_number)

        return render_template(
            "shift_todos/index.html",
            mode="gm",
            store_number=store_number,
            available_stores=available_stores,
            show_store_selector=is_multi_store_shift_todo_user(),
            today=today,
            tms=tms,
            open_todos=open_todos,
            completed_todos=completed_todos,
        )

    if account_role == "tm":
        open_assignments, completed_assignments = get_tm_todos(current_user_id())

        return render_template(
            "shift_todos/index.html",
            mode="tm",
            store_number=store_number,
            today=business_date_et(),
            open_assignments=open_assignments,
            completed_assignments=completed_assignments,
        )

    abort(403)


@shift_todos_bp.route("/create", methods=["POST"])
@login_required
@role_required("admin", "supervisor", "general_manager", "manager")
def create():
    if not can_create_shift_todos():
        abort(403)

    store_number, available_stores = selected_shift_todo_store()
    if not store_number:
        flash("No active stores are available for Shift To-Dos.", "error")
        return redirect(url_for("shift_todos.index", store_number=store_number))

    allowed_store_numbers = {store.store_number for store in available_stores}
    if is_multi_store_shift_todo_user() and store_number not in allowed_store_numbers:
        abort(403)

    task_titles = parse_task_titles()
    description = request.form.get("description", "").strip() or None
    shift_type = request.form.get("shift_type", "general").strip() or "general"
    due_date_raw = request.form.get("due_date", "").strip()
    due_time = request.form.get("due_time", "").strip() or None
    priority = request.form.get("priority", "normal").strip() or "normal"
    assignee_ids = list(dict.fromkeys(request.form.getlist("assignee_ids")))

    if not task_titles:
        flash("Please enter at least one to-do item.", "error")
        return redirect(url_for("shift_todos.index", store_number=store_number))

    if len(task_titles) > 25:
        flash("Please create no more than 25 to-dos at one time.", "error")
        return redirect(url_for("shift_todos.index", store_number=store_number))

    if not assignee_ids:
        flash("Please assign this to at least one TM.", "error")
        return redirect(url_for("shift_todos.index", store_number=store_number))

    try:
        due_date = datetime.strptime(due_date_raw, "%Y-%m-%d").date() if due_date_raw else business_date_et()
    except ValueError:
        flash("Please enter a valid due date.", "error")
        return redirect(url_for("shift_todos.index", store_number=store_number))

    assignees = User.query.filter(
        User.id.in_(assignee_ids),
        User.role == "tm",
        User.store_number == store_number,
        User.is_active == True,
    ).order_by(User.name.asc()).all()

    if len(assignees) != len(assignee_ids):
        flash("One or more selected TMs are not valid for your store.", "error")
        return redirect(url_for("shift_todos.index", store_number=store_number))

    created_count = 0

    for title in task_titles:
        todo = ShiftTodo(
            store_number=store_number,
            title=title,
            description=description,
            shift_type=shift_type,
            due_date=due_date,
            due_time=due_time,
            priority=priority,
            status="open",
            created_by_user_id=current_user_id(),
        )

        db.session.add(todo)
        db.session.flush()

        for user in assignees:
            db.session.add(
                ShiftTodoAssignment(
                    shift_todo_id=todo.id,
                    user_id=user.id,
                    is_completed=False,
                )
            )

        created_count += 1

    db.session.commit()

    if created_count == 1:
        flash(f"Shift To-Do created and assigned to {len(assignees)} TM(s).", "success")
    else:
        flash(f"{created_count} Shift To-Dos created and assigned to {len(assignees)} TM(s).", "success")

    return redirect(url_for("shift_todos.index", store_number=store_number))


@shift_todos_bp.route("/<int:todo_id>/cancel", methods=["POST"])
@login_required
@role_required("admin", "supervisor", "general_manager", "manager")
def cancel(todo_id):
    if not can_create_shift_todos():
        abort(403)

    todo = ShiftTodo.query.get_or_404(todo_id)

    if is_multi_store_shift_todo_user():
        available_stores = get_available_shift_todo_stores()
        allowed_store_numbers = {store.store_number for store in available_stores}
        if todo.store_number not in allowed_store_numbers:
            abort(403)
    elif todo.store_number != current_store():
        abort(403)

    todo.status = "canceled"
    db.session.commit()

    flash("Shift To-Do canceled.", "success")
    return redirect(url_for("shift_todos.index", store_number=todo.store_number))


@shift_todos_bp.route("/assignment/<int:assignment_id>/complete", methods=["POST"])
@login_required
@role_required("tm")
def complete_assignment(assignment_id):
    if current_account_role() != "tm":
        abort(403)

    assignment = ShiftTodoAssignment.query.get_or_404(assignment_id)

    if assignment.user_id != current_user_id():
        abort(403)

    completion_notes = request.form.get("completion_notes", "").strip() or None

    assignment.is_completed = True
    assignment.completed_at = datetime.utcnow()
    assignment.completion_notes = completion_notes

    db.session.commit()

    flash("To-Do marked complete.", "success")
    return redirect(url_for("shift_todos.index", store_number=store_number))
