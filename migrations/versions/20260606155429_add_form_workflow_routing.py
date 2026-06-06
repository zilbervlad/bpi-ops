"""add form workflow routing

Revision ID: 20260606155429
Revises: 80b31d459303
Create Date: 2026-06-06 15:54:29
"""

from alembic import op
import sqlalchemy as sa


revision = "20260606155429"
down_revision = "80b31d459303"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("form_templates") as batch_op:
        batch_op.add_column(sa.Column("notify_gm", sa.Boolean(), nullable=False, server_default=sa.true()))
        batch_op.add_column(sa.Column("notify_supervisor", sa.Boolean(), nullable=False, server_default=sa.true()))
        batch_op.add_column(sa.Column("notify_admin", sa.Boolean(), nullable=False, server_default=sa.true()))
        batch_op.add_column(sa.Column("notify_hr", sa.Boolean(), nullable=False, server_default=sa.false()))
        batch_op.add_column(sa.Column("notify_payroll", sa.Boolean(), nullable=False, server_default=sa.false()))

        batch_op.add_column(sa.Column("requires_gm_approval", sa.Boolean(), nullable=False, server_default=sa.false()))
        batch_op.add_column(sa.Column("requires_supervisor_approval", sa.Boolean(), nullable=False, server_default=sa.false()))
        batch_op.add_column(sa.Column("requires_hr_approval", sa.Boolean(), nullable=False, server_default=sa.false()))
        batch_op.add_column(sa.Column("requires_payroll_processing", sa.Boolean(), nullable=False, server_default=sa.false()))
        batch_op.add_column(sa.Column("notify_employee_when_complete", sa.Boolean(), nullable=False, server_default=sa.false()))

    with op.batch_alter_table("form_submissions") as batch_op:
        batch_op.add_column(sa.Column("workflow_status", sa.String(length=50), nullable=False, server_default="submitted"))
        batch_op.add_column(sa.Column("workflow_completed_at", sa.DateTime(), nullable=True))
        batch_op.add_column(sa.Column("workflow_notes", sa.Text(), nullable=True))


def downgrade():
    with op.batch_alter_table("form_submissions") as batch_op:
        batch_op.drop_column("workflow_notes")
        batch_op.drop_column("workflow_completed_at")
        batch_op.drop_column("workflow_status")

    with op.batch_alter_table("form_templates") as batch_op:
        batch_op.drop_column("notify_employee_when_complete")
        batch_op.drop_column("requires_payroll_processing")
        batch_op.drop_column("requires_hr_approval")
        batch_op.drop_column("requires_supervisor_approval")
        batch_op.drop_column("requires_gm_approval")

        batch_op.drop_column("notify_payroll")
        batch_op.drop_column("notify_hr")
        batch_op.drop_column("notify_admin")
        batch_op.drop_column("notify_supervisor")
        batch_op.drop_column("notify_gm")
