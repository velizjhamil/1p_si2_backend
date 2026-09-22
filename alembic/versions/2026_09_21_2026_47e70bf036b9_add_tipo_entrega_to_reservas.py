"""add_tipo_entrega_to_reservas

Revision ID: 47e70bf036b9
Revises: c8b9d0e1f2a3
Create Date: 2026-09-21 20:26:07.511610

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '47e70bf036b9'
down_revision: Union[str, Sequence[str], None] = 'c8b9d0e1f2a3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('reservas', sa.Column('tipo_entrega', sa.String(length=20), nullable=False, server_default='RETIRO'))
    op.add_column('reservas', sa.Column('direccion_entrega', sa.String(length=255), nullable=True))
    op.add_column('reservas', sa.Column('telefono_entrega', sa.String(length=50), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('reservas', 'telefono_entrega')
    op.drop_column('reservas', 'direccion_entrega')
    op.drop_column('reservas', 'tipo_entrega')
