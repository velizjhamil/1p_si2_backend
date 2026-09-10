"""usuarios fecha_creacion (CU3 fecha de registro)

Revision ID: b7d5f0e2c4a9
Revises: a4c461ccb017
Create Date: 2026-09-10 00:00:00.000000

Agrega usuarios.fecha_creacion (timestamptz NOT NULL DEFAULT now()) — la
fecha de registro que muestra la tabla de Gestión de Usuarios (Step 3).
El server_default now() backfill-ea las 4 filas demo existentes.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b7d5f0e2c4a9'
down_revision: Union[str, Sequence[str], None] = 'a4c461ccb017'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        'usuarios',
        sa.Column('fecha_creacion', sa.DateTime(timezone=True),
                  server_default=sa.text('now()'), nullable=False),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('usuarios', 'fecha_creacion')
