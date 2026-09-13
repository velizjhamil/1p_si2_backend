"""CU15+CU21 ventas y detalle_ventas (checkout)

Revision ID: d0a6b4c9e3f7
Revises: c9f5a3b8d2e6
Create Date: 2026-09-13 19:30:00.000000

Migración MANUAL (no autogenerada): crea las tablas `ventas` y
`detalle_ventas` del CU15 (Carrito) + CU21 (Checkout). Sin seeds — las
ventas se registran por POST /api/v1/ventas/checkout.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID


# revision identifiers, used by Alembic.
revision: str = 'd0a6b4c9e3f7'
down_revision: Union[str, Sequence[str], None] = 'c9f5a3b8d2e6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # --- CU15+CU21: ventas ------------------------------------------------------
    op.create_table('ventas',
    sa.Column('id_venta', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('id_cliente', UUID(as_uuid=True), nullable=False),
    sa.Column('fecha_venta', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('total', sa.Numeric(10, 2), nullable=False),
    sa.Column('costo_envio', sa.Numeric(10, 2), nullable=False),
    sa.Column('metodo_pago', sa.String(length=20), nullable=False),
    sa.Column('estado_pago', sa.String(length=20), nullable=False),
    sa.Column('codigo', sa.String(length=20), nullable=False),
    sa.Column('comprobante_url', sa.String(length=500), nullable=True),
    sa.Column('nombre_cliente', sa.String(length=150), nullable=False),
    sa.Column('correo', sa.String(length=100), nullable=False),
    sa.Column('telefono', sa.String(length=20), nullable=False),
    sa.Column('direccion', sa.String(length=255), nullable=False),
    sa.Column('ciudad', sa.String(length=100), nullable=False),
    sa.Column('referencia', sa.String(length=255), nullable=True),
    sa.ForeignKeyConstraint(['id_cliente'], ['usuarios.id_usuario'], name=op.f('fk_ventas_id_cliente_usuarios')),
    sa.PrimaryKeyConstraint('id_venta', name=op.f('pk_ventas')),
    sa.CheckConstraint("metodo_pago IN ('QR', 'EFECTIVO', 'TARJETA')", name='metodo_pago_valido'),
    sa.CheckConstraint("estado_pago IN ('PENDIENTE', 'PAGADO', 'RECHAZADO')", name='estado_pago_valido'),
    sa.CheckConstraint("total >= 0", name='total_no_negativo'),
    sa.CheckConstraint("costo_envio >= 0", name='costo_envio_no_negativo'),
    )
    op.create_index(op.f('ix_ventas_id_venta'), 'ventas', ['id_venta'], unique=False)
    op.create_index(op.f('ix_ventas_id_cliente'), 'ventas', ['id_cliente'], unique=False)
    op.create_index(op.f('ix_ventas_codigo'), 'ventas', ['codigo'], unique=True)
    op.create_index(op.f('ix_ventas_estado_pago'), 'ventas', ['estado_pago'], unique=False)

    # --- CU15+CU21: detalle_ventas ----------------------------------------------
    op.create_table('detalle_ventas',
    sa.Column('id_detalle', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('id_venta', sa.Integer(), nullable=False),
    sa.Column('id_producto', sa.Integer(), nullable=False),
    sa.Column('cantidad', sa.Integer(), nullable=False),
    sa.Column('precio_unitario', sa.Numeric(10, 2), nullable=False),
    sa.Column('subtotal', sa.Numeric(10, 2), nullable=False),
    sa.Column('talla', sa.String(length=20), nullable=True),
    sa.Column('color', sa.String(length=50), nullable=True),
    sa.ForeignKeyConstraint(['id_venta'], ['ventas.id_venta'], ondelete='CASCADE', name=op.f('fk_detalle_ventas_id_venta_ventas')),
    sa.ForeignKeyConstraint(['id_producto'], ['productos.id_producto'], name=op.f('fk_detalle_ventas_id_producto_productos')),
    sa.PrimaryKeyConstraint('id_detalle', name=op.f('pk_detalle_ventas')),
    )
    op.create_index(op.f('ix_detalle_ventas_id_detalle'), 'detalle_ventas', ['id_detalle'], unique=False)
    op.create_index(op.f('ix_detalle_ventas_id_venta'), 'detalle_ventas', ['id_venta'], unique=False)
    op.create_index(op.f('ix_detalle_ventas_id_producto'), 'detalle_ventas', ['id_producto'], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f('ix_detalle_ventas_id_producto'), table_name='detalle_ventas')
    op.drop_index(op.f('ix_detalle_ventas_id_venta'), table_name='detalle_ventas')
    op.drop_index(op.f('ix_detalle_ventas_id_detalle'), table_name='detalle_ventas')
    op.drop_table('detalle_ventas')
    op.drop_index(op.f('ix_ventas_estado_pago'), table_name='ventas')
    op.drop_index(op.f('ix_ventas_codigo'), table_name='ventas')
    op.drop_index(op.f('ix_ventas_id_cliente'), table_name='ventas')
    op.drop_index(op.f('ix_ventas_id_venta'), table_name='ventas')
    op.drop_table('ventas')
