"""CU12 descuentos y cupones (reglas comerciales)

Revision ID: f3a8b2c4d6e1
Revises: e2f7a1b3c5d9
Create Date: 2026-09-16 21:00:00.000000

Migracion MANUAL (no autogenerada): crea la tabla `descuentos` del
CU12. El modelo unifica descuentos automaticos (sin codigo) y cupones
(con codigo unico) en una sola tabla, discriminados por `tipo`.

Decisiones:
- `codigo` es UNIQUE cuando viene NOT NULL (PostgreSQL permite varios
  NULL en una UNIQUE constraint), lo que deja crear reglas automaticas
  sin codigo.
- `tipo` se valida a nivel DB con CHECK (PORCENTAJE | MONTO_FIJO) para
  no depender unicamente de Pydantic.
- `valor` siempre > 0. Para PORCENTAJE, maximo 100 (validado en
  Pydantic, no en DB para no tener que modificar la constraint al
  cambiar limites).
- `fecha_fin >= fecha_inicio` se valida en Pydantic (el CHECK a nivel
  DB requiere un trigger porque la constraint no puede referenciar dos
  columnas en CHECK estandar; lo dejo en la app).
- Sin seeds: los descuentos los crean GS/ASU desde la UI.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'f3a8b2c4d6e1'
down_revision: Union[str, Sequence[str], None] = 'e2f7a1b3c5d9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema: crea tabla descuentos."""
    op.create_table(
        'descuentos',
        sa.Column('id_descuento', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('codigo', sa.String(length=30), nullable=True),
        sa.Column('nombre', sa.String(length=100), nullable=False),
        sa.Column('descripcion', sa.String(length=255), nullable=True),
        # PORCENTAJE = 0-100, MONTO_FIJO = Bs.
        sa.Column('tipo', sa.String(length=15), nullable=False),
        sa.Column('valor', sa.Numeric(10, 2), nullable=False),
        # Vigencia: fecha_fin puede ser NULL = sin vencimiento
        sa.Column('fecha_inicio', sa.Date(), nullable=False),
        sa.Column('fecha_fin', sa.Date(), nullable=True),
        sa.Column('activo', sa.Boolean(), nullable=False, server_default=sa.text('true')),
        # Control de uso (para cupones con limite)
        sa.Column('usos_maximos', sa.Integer(), nullable=True),
        sa.Column('usos_actuales', sa.Integer(), nullable=False, server_default='0'),
        # Compra minima para aplicar (NULL = sin minimo)
        sa.Column('monto_minimo_compra', sa.Numeric(10, 2), nullable=True),
        sa.Column(
            'fecha_creacion',
            sa.DateTime(timezone=True),
            server_default=sa.text('now()'),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint('id_descuento', name=op.f('pk_descuentos')),
        sa.UniqueConstraint('codigo', name=op.f('uq_descuentos_codigo')),
        sa.CheckConstraint(
            "tipo IN ('PORCENTAJE', 'MONTO_FIJO')",
            name='tipo_descuento_valido',
        ),
        sa.CheckConstraint('valor > 0', name='valor_descuento_positivo'),
    )
    op.create_index(op.f('ix_descuentos_id_descuento'), 'descuentos', ['id_descuento'], unique=False)
    op.create_index(op.f('ix_descuentos_codigo'), 'descuentos', ['codigo'], unique=True)
    op.create_index(op.f('ix_descuentos_activo'), 'descuentos', ['activo'], unique=False)


def downgrade() -> None:
    """Downgrade schema: elimina tabla descuentos."""
    op.drop_index(op.f('ix_descuentos_activo'), table_name='descuentos')
    op.drop_index(op.f('ix_descuentos_codigo'), table_name='descuentos')
    op.drop_index(op.f('ix_descuentos_id_descuento'), table_name='descuentos')
    op.drop_table('descuentos')
