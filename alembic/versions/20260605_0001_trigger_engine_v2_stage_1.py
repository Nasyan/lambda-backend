"""trigger engine v2 stage 1 add columns

Revision ID: 20260605_0001
Revises:
Create Date: 2026-06-05 00:00:00.000000

Phase 1 live migration strategy:
1. Create the new enum/columns as nullable where needed for backfill.
2. Copy existing triggers.ast into payload_ast and mark legacy payloads as VALUE.
3. Backfill source_template_uuid from target_template_uuid for legacy rows. If a
   legacy row has no target_template_uuid, preserve the row and use a zero-UUID
   sentinel so operators can repair/retire that orphaned trigger after migration.
4. Enforce NOT NULL on new required columns.
5. Keep legacy ast for rollback/read compatibility. Apply revision 20260605_0002
   only after deploy verification.

No tracked prior Alembic revisions exist in this repository: alembic/versions is
gitignored and both git history and the on-disk directory had no earlier migration
files when this branch was authored. down_revision=None is intentional.

Downgrade caveat: ast is refreshed from payload_ast for compatibility, then v2-only
columns are dropped. condition/action mapping/source metadata is not preserved.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = "20260605_0001"
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


payload_return_type_enum = postgresql.ENUM(
    "BOOLEAN",
    "VALUE",
    "LIST",
    name="payload_return_type_enum",
)


def upgrade() -> None:
    """Upgrade schema."""
    bind = op.get_bind()
    payload_return_type_enum.create(bind, checkfirst=True)

    op.add_column("triggers", sa.Column("condition_ast", sa.JSON(), nullable=True))
    op.add_column("triggers", sa.Column("payload_ast", sa.JSON(), nullable=True))
    op.add_column(
        "triggers",
        sa.Column(
            "payload_return_type",
            sa.Enum(
                "BOOLEAN",
                "VALUE",
                "LIST",
                name="payload_return_type_enum",
                create_type=False,
            ),
            nullable=True,
        ),
    )
    op.add_column("triggers", sa.Column("action_mapping_ast", sa.JSON(), nullable=True))
    op.add_column(
        "triggers",
        sa.Column("source_template_uuid", postgresql.UUID(as_uuid=True), nullable=True),
    )

    op.execute(
        """
        UPDATE triggers
        SET
            payload_ast = COALESCE(payload_ast, ast),
            payload_return_type = COALESCE(payload_return_type, 'VALUE'::payload_return_type_enum),
            source_template_uuid = COALESCE(
                source_template_uuid,
                target_template_uuid,
                '00000000-0000-0000-0000-000000000000'::uuid
            )
        """
    )

    op.alter_column("triggers", "payload_ast", nullable=False)
    op.alter_column("triggers", "payload_return_type", nullable=False)
    op.alter_column("triggers", "source_template_uuid", nullable=False)
    op.create_index(
        op.f("ix_triggers_source_template_uuid"),
        "triggers",
        ["source_template_uuid"],
        unique=False,
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.execute("UPDATE triggers SET ast = payload_ast WHERE payload_ast IS NOT NULL")
    op.drop_index(op.f("ix_triggers_source_template_uuid"), table_name="triggers")
    op.drop_column("triggers", "source_template_uuid")
    op.drop_column("triggers", "action_mapping_ast")
    op.drop_column("triggers", "payload_return_type")
    op.drop_column("triggers", "payload_ast")
    op.drop_column("triggers", "condition_ast")
    payload_return_type_enum.drop(op.get_bind(), checkfirst=True)
