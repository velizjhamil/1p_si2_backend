"""CU22 movimientos_inventario (kardex)

Revision ID: c9f5a3b8d2e6
Revises: b8e4f2a7c9d1
Create Date: 2026-09-13 18:00:00.000000

Migración MANUAL (no autogenerada): crea la tabla `movimientos_inventario`
del CU22. Sin seeds — los movimientos se registran por el endpoint POST
/api/v1/inventario/movimientos con datos reales.

NOTA: al llegar esta tabla, el check DINÁMICO de products.py (DELETE de
productos con movimientos -> 409) se activa automáticamente.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID


# revision identifiers, used by Alembic.
revision: str = 'c9f5a3b8d2e6'
down_revision: Union[str, Sequence[str], None] = 'b8e4f2a7c9d1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # --- CU22: movimientos_inventario (kardex) ---------------------------------
    op.create_table('movimientos_inventario',
    sa.Column('id_movimiento', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('id_producto', sa.Integer(), nullable=False),
    sa.Column('tipo', sa.String(length=20), nullable=False),
    sa.Column('cantidad', sa.Integer(), nullable=False),
    sa.Column('stock_anterior', sa.Integer(), nullable=False),
    sa.Column('stock_nuevo', sa.Integer(), nullable=False),
    sa.Column('motivo', sa.String(length=255), nullable=True),
    sa.Column('fecha_movimiento', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('id_usuario', UUID(as_uuid=True), nullable=False),
    sa.ForeignKeyConstraint(['id_producto'], ['productos.id_producto'], name=op.f('fk_movimientos_inventario_id_producto_productos')),
    sa.ForeignKeyConstraint(['id_usuario'], ['usuarios.id_usuario'], name=op.f('fk_movimientos_inventario_id_usuario_usuarios')),
    sa.PrimaryKeyConstraint('id_movimiento', name=op.f('pk_movimientos_inventario')),
    sa.CheckConstraint("tipo IN ('ENTRADA', 'SALIDA', 'AJUSTE')", name='tipo_movimiento_valido'),
    sa.CheckConstraint("cantidad >= 0", name='cantidad_no_negativa'),
    sa.CheckConstraint("stock_anterior >= 0", name='stock_anterior_no_negativo'),
    sa.CheckConstraint("stock_nuevo >= 0", name='stock_nuevo_no_negativo'),
    )
    op.create_index(op.f('ix_movimientos_inventario_id_movimiento'), 'movimientos_inventario', ['id_movimiento'], unique=False)
    op.create_index(op.f('ix_movimientos_inventario_id_producto'), 'movimientos_inventario', ['id_producto'], unique=False)
    op.create_index(op.f('ix_movimientos_inventario_tipo'), 'movimientos_inventario', ['tipo'], unique=False)
    op.create_index(op.f('ix_movimientos_inventario_id_usuario'), 'movimientos_inventario', ['id_usuario'], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f('ix_movimientos_inventario_id_usuario'), table_name='movimientos_inventario')
    op.drop_index(op.f('ix_movimientos_inventario_tipo'), table_name='movimientos_inventario')
    op.drop_index(op.f('ix_movimientos_inventario_id_producto'), table_name='movimientos_inventario')
    op.drop_index(op.f('ix_movimientos_inventario_id_movimiento'), table_name='movimientos_inventario')
    op.drop_table('movimientos_inventario')
