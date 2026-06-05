"""trigger engine v2 phase 2 drop legacy ast

Revision ID: 20260605_0002
Revises: 20260605_0001
Create Date: 2026-06-05 00:05:00.000000

Phase 2 cleanup migration. Apply only after the v2 application has been deployed,
new writes are verified to populate payload_ast, and rollback to the single-ast
runtime is no longer needed.

Downgrade caveat: legacy ast is restored from payload_ast. Split v2 metadata remains,
but the old single-ast runtime can only read payload_ast as its restored formula.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "20260605_0002"
down_revision: Union[str, Sequence[str], None] = "20260605_0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.drop_column("triggers", "ast")


def downgrade() -> None:
    """Downgrade schema."""
    op.add_column("triggers", sa.Column("ast", sa.JSON(), nullable=True))
    op.execute("UPDATE triggers SET ast = payload_ast")
    op.alter_column("triggers", "ast", nullable=False)
