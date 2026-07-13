"""Initial task, confirmation, backend, region, and artifact schema."""

import sqlalchemy as sa
from alembic import op

revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "jobs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("plan_hash", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_jobs_plan_hash", "jobs", ["plan_hash"])
    op.create_table(
        "confirmations",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("job_id", sa.String(36), sa.ForeignKey("jobs.id", ondelete="CASCADE")),
        sa.Column("scope", sa.String(32), nullable=False),
        sa.Column("target_key", sa.Text(), nullable=False),
        sa.Column("failure_code", sa.String(64), nullable=False),
        sa.Column("estimated_bytes", sa.BigInteger(), nullable=False),
        sa.Column("failure_snapshot_json", sa.Text(), nullable=False),
        sa.Column("plan_hash", sa.String(64), nullable=False),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_confirmations_job_id", "confirmations", ["job_id"])
    op.create_index(
        "ix_confirmations_scope_target",
        "confirmations",
        ["job_id", "scope", "target_key"],
    )
    op.create_table(
        "tasks",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("job_id", sa.String(36), sa.ForeignKey("jobs.id", ondelete="CASCADE")),
        sa.Column("file_key", sa.Text(), nullable=False),
        sa.Column("mode", sa.String(32), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("source_url", sa.Text(), nullable=False),
        sa.Column("target_path", sa.Text(), nullable=False),
        sa.Column("expected_size", sa.BigInteger()),
        sa.Column("progress_bytes", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("checksum", sa.String(128)),
        sa.Column("checksum_type", sa.String(32)),
        sa.Column(
            "confirmation_id",
            sa.String(36),
            sa.ForeignKey("confirmations.id", ondelete="RESTRICT"),
        ),
        sa.Column("failure_code", sa.String(64)),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
    )
    for name in ("job_id", "file_key", "status"):
        op.create_index(f"ix_tasks_{name}", "tasks", [name])
    op.create_index("ix_tasks_job_file", "tasks", ["job_id", "file_key"])
    op.create_table(
        "task_events",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("task_id", sa.String(36), sa.ForeignKey("tasks.id", ondelete="CASCADE")),
        sa.Column("event_type", sa.String(64), nullable=False),
        sa.Column("payload_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_task_events_task_id", "task_events", ["task_id"])
    op.create_table(
        "backends",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("base_url", sa.Text(), nullable=False, unique=True),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("priority", sa.Integer(), nullable=False, server_default="100"),
        sa.Column("capabilities_json", sa.Text(), nullable=False, server_default="{}"),
    )
    op.create_table(
        "regions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("source_path", sa.Text(), nullable=False),
        sa.Column("source_sha256", sa.String(64), nullable=False),
        sa.Column("source_crs", sa.Text(), nullable=False),
        sa.Column("normalized_crs", sa.Text(), nullable=False),
        sa.Column("geometry_wkb", sa.LargeBinary(), nullable=False),
        sa.Column("bbox_json", sa.Text(), nullable=False),
        sa.Column("repaired", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_regions_source_sha256", "regions", ["source_sha256"])
    op.create_table(
        "artifacts",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("job_id", sa.String(36), sa.ForeignKey("jobs.id", ondelete="CASCADE")),
        sa.Column("path", sa.Text(), nullable=False, unique=True),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("year", sa.Integer()),
    )
    op.create_index("ix_artifacts_job_id", "artifacts", ["job_id"])


def downgrade() -> None:
    for table in (
        "artifacts",
        "regions",
        "backends",
        "task_events",
        "tasks",
        "confirmations",
        "jobs",
    ):
        op.drop_table(table)
