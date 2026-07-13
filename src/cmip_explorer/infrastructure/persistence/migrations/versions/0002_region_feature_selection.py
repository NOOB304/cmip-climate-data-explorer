"""Persist selected region feature identifiers."""

import sqlalchemy as sa
from alembic import op

revision = "0002_region_feature_selection"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def upgrade() -> None:
    columns = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("regions")}
    if "selected_feature_ids_json" not in columns:
        op.add_column(
            "regions",
            sa.Column(
                "selected_feature_ids_json",
                sa.Text(),
                nullable=False,
                server_default="[]",
            ),
        )


def downgrade() -> None:
    columns = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("regions")}
    if "selected_feature_ids_json" in columns:
        op.drop_column("regions", "selected_feature_ids_json")
