from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from flask import render_template, request, redirect, url_for, session, flash, abort

from app.auth.routes import login_required, role_required
from app.extensions import db
from app.models import User
from app.shift_todos import shift_todos_bp
from app.shift_todos.models import ShiftTodo, ShiftTodoAssignment

APP_TZ = ZoneInfo("America/New_York")


def now_et():
    return datetime.now(APP_TZ)


def business_date_et():
    now = now_et()
    if now.hour < 5:
        return (now - timedelta(days=1)).date()
    return now.date()


def current_account_role():
    return session.get("account_role", session.get("user_role"))


def current_store():
    return session.get("user_store")


def current_user_id():
    return session.get("user_id")


def get_store_tms(store_number):
    if not store_number:
        return []

    return User.query.filter_by(
        role="tm",
        store_number=store_number,
        is_active=True,
    ).order_by(User.name.asc()).all()


def get_gm_store_todos(store_number):
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


@shift_todos_bp.route("/", methods=["GET"])
@login_required
@role_required("general_manager", "tm")
def index():
    account_role = current_account_role()
    store_number = current_store()

    if account_role == "general_manager":
        if not store_number:
            flash("Your General Manager account does not have a store assigned.", "error")
            return redirect(url_for("dashboard.home"))

        today, open_todos, completed_todos = get_gm_store_todos(store_number)
        tms = get_store_tms(store_number)

        return render_template(
            "shift_todos/index.html",
            mode="gm",
            store_number=store_number,
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
@role_required("general_manager")
def create():
    if current_account_role() != "general_manager":
        abort(403)

    store_number = current_store()
    if not store_number:
        flash("Your General Manager account does not have a store assigned.", "error")
        return redirect(url_for("shift_todos.index"))

    title = request.form.get("title", "").strip()
    description = request.form.get("description", "").strip() or None
    shift_type = request.form.get("shift_type", "general").strip() or "general"
    due_date_raw = request.form.get("due_date", "").strip()
    due_time = request.form.get("due_time", "").strip() or None
    priority = request.form.get("priority", "normal").strip() or "normal"
    assignee_ids = list(dict.fromkeys(request.form.getlist("assignee_ids")))

    if not title:
        flash("Please enter a to-do title.", "error")
        return redirect(url_for("shift_todos.index"))

    if not assignee_ids:
        flash("Please assign this to at least one TM.", "error")
        return redirect(url_for("shift_todos.index"))

    if len(assignee_ids) > 2:
        flash("Shift To-Dos can be assigned to a maximum of 2 TMs.", "error")
        return redirect(url_for("shift_todos.index"))

    try:
        due_date = datetime.strptime(due_date_raw, "%Y-%m-%d").date() if due_date_raw else business_date_et()
    except ValueError:
        flash("Please enter a valid due date.", "error")
        return redirect(url_for("shift_todos.index"))

    assignees = User.query.filter(
        User.id.in_(assignee_ids),
        User.role == "tm",
        User.store_number == store_number,
        User.is_active == True,
    ).all()

    if len(assignees) != len(assignee_ids):
        flash("One or more selected TMs are not valid for your store.", "error")
        return redirect(url_for("shift_todos.index"))

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

    db.session.commit()

    flash("Shift To-Do created successfully.", "success")
    return redirect(url_for("shift_todos.index"))


@shift_todos_bp.route("/<int:todo_id>/cancel", methods=["POST"])
@login_required
@role_required("general_manager")
def cancel(todo_id):
    if current_account_role() != "general_manager":
        abort(403)

    todo = ShiftTodo.query.get_or_404(todo_id)

    if todo.store_number != current_store():
        abort(403)

    todo.status = "canceled"
    db.session.commit()

    flash("Shift To-Do canceled.", "success")
    return redirect(url_for("shift_todos.index"))


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
    return redirect(url_for("shift_todos.index"))
