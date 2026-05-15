from datetime import datetime, date

from app.extensions import db


class ShiftTodo(db.Model):
    __tablename__ = "shift_todos"

    id = db.Column(db.Integer, primary_key=True)

    store_number = db.Column(db.String(10), nullable=False)

    title = db.Column(db.String(255), nullable=False)
    description = db.Column(db.Text, nullable=True)

    shift_type = db.Column(db.String(50), nullable=False, default="general")
    due_date = db.Column(db.Date, nullable=False, default=date.today)
    due_time = db.Column(db.String(20), nullable=True)

    priority = db.Column(db.String(30), nullable=False, default="normal")
    status = db.Column(db.String(30), nullable=False, default="open")

    created_by_user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    created_by = db.relationship("User")

    assignments = db.relationship(
        "ShiftTodoAssignment",
        backref="todo",
        lazy=True,
        cascade="all, delete-orphan",
    )

    def is_fully_completed(self):
        active_assignments = self.assignments or []
        if not active_assignments:
            return False
        return all(item.is_completed for item in active_assignments)


class ShiftTodoAssignment(db.Model):
    __tablename__ = "shift_todo_assignments"

    id = db.Column(db.Integer, primary_key=True)

    shift_todo_id = db.Column(
        db.Integer,
        db.ForeignKey("shift_todos.id"),
        nullable=False,
    )

    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)

    is_completed = db.Column(db.Boolean, nullable=False, default=False)
    completed_at = db.Column(db.DateTime, nullable=True)
    completion_notes = db.Column(db.Text, nullable=True)

    assigned_user = db.relationship("User")
