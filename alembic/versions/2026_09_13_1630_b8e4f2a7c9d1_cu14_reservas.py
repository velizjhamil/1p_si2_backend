"""CU14 reservas y detalle_reservas

Revision ID: b8e4f2a7c9d1
Revises: a7b3d9e1f5c4
Create Date: 2026-09-13 16:30:00.000000

Migración MANUAL (no autogenerada): crea las tablas `reservas` y
`detalle_reservas` del CU14. Sin seeds — los smoke tests crean sus
propios registros de prueba.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID


# revision identifiers, used by Alembic.
revision: str = 'b8e4f2a7c9d1'
down_revision: Union[str, Sequence[str], None] = 'a7b3d9e1f5c4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # --- CU14: reservas --------------------------------------------------------
    op.create_table('reservas',
    sa.Column('id_reserva', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('id_cliente', UUID(as_uuid=True), nullable=False),
    sa.Column('fecha_reserva', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('fecha_expiracion', sa.Date(), nullable=False),
    sa.Column('estado', sa.String(length=20), nullable=False),
    sa.Column('total_estimado', sa.Numeric(10, 2), nullable=False),
    sa.Column('motivo_cancelacion', sa.String(length=255), nullable=True),
    sa.ForeignKeyConstraint(['id_cliente'], ['usuarios.id_usuario'], name=op.f('fk_reservas_id_cliente_usuarios')),
    sa.PrimaryKeyConstraint('id_reserva', name=op.f('pk_reservas')),
    sa.CheckConstraint("estado IN ('PENDIENTE', 'CONFIRMADA', 'CANCELADA', 'COMPLETADA')", name='estado_reserva_valido'),
    sa.CheckConstraint("total_estimado >= 0", name='total_estimado_no_negativo'),
    )
    op.create_index(op.f('ix_reservas_id_reserva'), 'reservas', ['id_reserva'], unique=False)
    op.create_index(op.f('ix_reservas_id_cliente'), 'reservas', ['id_cliente'], unique=False)
    op.create_index(op.f('ix_reservas_estado'), 'reservas', ['estado'], unique=False)

    # --- CU14: detalle_reservas ------------------------------------------------
    op.create_table('detalle_reservas',
    sa.Column('id_detalle', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('id_reserva', sa.Integer(), nullable=False),
    sa.Column('id_producto', sa.Integer(), nullable=False),
    sa.Column('cantidad', sa.Integer(), nullable=False),
    sa.Column('precio_unitario', sa.Numeric(10, 2), nullable=False),
    sa.ForeignKeyConstraint(['id_reserva'], ['reservas.id_reserva'], ondelete='CASCADE', name=op.f('fk_detalle_reservas_id_reserva_reservas')),
    sa.ForeignKeyConstraint(['id_producto'], ['productos.id_producto'], name=op.f('fk_detalle_reservas_id_producto_productos')),
    sa.PrimaryKeyConstraint('id_detalle', name=op.f('pk_detalle_reservas')),
    )
    op.create_index(op.f('ix_detalle_reservas_id_detalle'), 'detalle_reservas', ['id_detalle'], unique=False)
    op.create_index(op.f('ix_detalle_reservas_id_reserva'), 'detalle_reservas', ['id_reserva'], unique=False)
    op.create_index(op.f('ix_detalle_reservas_id_producto'), 'detalle_reservas', ['id_producto'], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f('ix_detalle_reservas_id_producto'), table_name='detalle_reservas')
    op.drop_index(op.f('ix_detalle_reservas_id_reserva'), table_name='detail_reservas' if False else 'detalle_reservas')
    op.drop_index(op.f('ix_detalle_reservas_id_detalle'), table_name='detalle_reservas')
    op.drop_table('detalle_reservas')
    op.drop_index(op.f('ix_reservas_estado'), table_name='reservas')
    op.drop_index(op.f('ix_reservas_id_cliente'), table_name='reservas')
    op.drop_index(op.f('ix_reservas_id_reserva'), table_name='reservas')
    op.drop_table('reservas')
