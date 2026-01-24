"""add password reset tokens

Revision ID: 101ac3b70585
Revises: 5b1a6dc6cea6
Create Date: 2026-01-22 05:48:59.156378

"""

from typing import Sequence, Union


# revision identifiers, used by Alembic.
revision: str = "101ac3b70585"
down_revision: Union[str, Sequence[str], None] = "5b1a6dc6cea6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass
