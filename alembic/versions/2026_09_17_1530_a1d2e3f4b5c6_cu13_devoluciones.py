"""CU13 devoluciones

Revision ID: a1d2e3f4b5c6
Revises: f3a8b2c4d6e1
Create Date: 2026-09-17 15:30:00.000000

Migracion MANUAL (no autogenerada): crea las tablas `devoluciones` y
`detalle_devoluciones` del CU13. Tabla padre (cabecera) + tabla hija
(lineas), mismo patron que ventas/detalle_ventas (CU15+CU21) y
reservas/detalle_reservas (CU14).

Decisiones:
- `devoluciones` referencia ventas.id_venta y usuarios.id_usuario (cliente,
  solicitante, procesador) — el procesador queda NULL hasta que pase de
  SOLICITADA.
- `detalle_devoluciones` referencia tanto la linea original (detalle_ventas)
  como el producto (duplicado para queries del kardex y la API).
- CHECKs en DB para estado (los 4 valores), cantidad_devuelta>0,
  subtotal>=0, monto_total_devuelto>=0.
- ondelete CASCADE en id_devolucion (detalle) para que al borrar la
  cabecera se borren las lineas. id_detalle_venta e id_producto NO
  son CASCADE: si se intenta borrar la venta/devolucion con devoluciones
  asociadas la FK lo bloquea.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a1d2e3f4b5c6'
down_revision: Union[str, Sequence[str], None] = 'f3a8b2c4d6e1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema: crea devoluciones + detalle_devoluciones."""
    op.create_table(
        'devoluciones',
        sa.Column('id_devolucion', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('id_venta', sa.Integer(), nullable=False),
        sa.Column('id_cliente', sa.dialects.postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('id_solicitante', sa.dialects.postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('id_procesador', sa.dialects.postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            'fecha_solicitud',
            sa.DateTime(timezone=True),
            server_default=sa.text('now()'),
            nullable=False,
        ),
        sa.Column('fecha_procesado', sa.DateTime(timezone=True), nullable=True),
        sa.Column('estado', sa.String(length=20), nullable=False, server_default='SOLICITADA'),
        sa.Column('motivo', sa.String(length=500), nullable=False),
        sa.Column('motivo_rechazo', sa.String(length=500), nullable=True),
        sa.Column(
            'monto_total_devuelto',
            sa.Numeric(10, 2),
            nullable=False,
            server_default='0',
        ),
        sa.PrimaryKeyConstraint('id_devolucion', name=op.f('pk_devoluciones')),
        sa.ForeignKeyConstraint(['id_venta'], ['ventas.id_venta'], name=op.f('fk_devoluciones_id_venta_ventas')),
        sa.ForeignKeyConstraint(['id_cliente'], ['usuarios.id_usuario'], name=op.f('fk_devoluciones_id_cliente_usuarios')),
        sa.ForeignKeyConstraint(['id_solicitante'], ['usuarios.id_usuario'], name=op.f('fk_devoluciones_id_solicitante_usuarios')),
        sa.ForeignKeyConstraint(
            ['id_procesador'],
            ['usuarios.id_usuario'],
            name=op.f('fk_devoluciones_id_procesador_usuarios'),
            ondelete='SET NULL',
        ),
        sa.CheckConstraint(
            "estado IN ('SOLICITADA', 'APROBADA', 'RECHAZADA', 'COMPLETADA')",
            name='estado_devolucion_valido',
        ),
        sa.CheckConstraint(
            'monto_total_devuelto >= 0',
            name='monto_total_devuelto_no_negativo',
        ),
    )
    op.create_index(op.f('ix_devoluciones_id_devolucion'), 'devoluciones', ['id_devolucion'], unique=False)
    op.create_index(op.f('ix_devoluciones_id_venta'), 'devoluciones', ['id_venta'], unique=False)
    op.create_index(op.f('ix_devoluciones_id_cliente'), 'devoluciones', ['id_cliente'], unique=False)
    op.create_index(op.f('ix_devoluciones_id_solicitante'), 'devoluciones', ['id_solicitante'], unique=False)
    op.create_index(op.f('ix_devoluciones_id_procesador'), 'devoluciones', ['id_procesador'], unique=False)
    op.create_index(op.f('ix_devoluciones_estado'), 'devoluciones', ['estado'], unique=False)

    op.create_table(
        'detalle_devoluciones',
        sa.Column('id_detalle', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('id_devolucion', sa.Integer(), nullable=False),
        sa.Column('id_detalle_venta', sa.Integer(), nullable=False),
        sa.Column('id_producto', sa.Integer(), nullable=False),
        sa.Column('cantidad_devuelta', sa.Integer(), nullable=False),
        sa.Column('precio_unitario', sa.Numeric(10, 2), nullable=False),
        sa.Column('subtotal', sa.Numeric(10, 2), nullable=False),
        sa.PrimaryKeyConstraint('id_detalle', name=op.f('pk_detalle_devoluciones')),
        sa.ForeignKeyConstraint(
            ['id_devolucion'],
            ['devoluciones.id_devolucion'],
            name=op.f('fk_detalle_devoluciones_id_devolucion_devoluciones'),
            ondelete='CASCADE',
        ),
        sa.ForeignKeyConstraint(
            ['id_detalle_venta'],
            ['detalle_ventas.id_detalle'],
            name=op.f('fk_detalle_devoluciones_id_detalle_venta_detalle_ventas'),
        ),
        sa.ForeignKeyConstraint(
            ['id_producto'],
            ['productos.id_producto'],
            name=op.f('fk_detalle_devoluciones_id_producto_productos'),
        ),
        sa.CheckConstraint('cantidad_devuelta > 0', name='cantidad_devuelta_positiva'),
        sa.CheckConstraint('precio_unitario >= 0', name='precio_unitario_no_negativo'),
        sa.CheckConstraint('subtotal >= 0', name='subtotal_no_negativo'),
    )
    op.create_index(op.f('ix_detalle_devoluciones_id_detalle'), 'detalle_devoluciones', ['id_detalle'], unique=False)
    op.create_index(op.f('ix_detalle_devoluciones_id_devolucion'), 'detalle_devoluciones', ['id_devolucion'], unique=False)
    op.create_index(op.f('ix_detalle_devoluciones_id_detalle_venta'), 'detalle_devoluciones', ['id_detalle_venta'], unique=False)
    op.create_index(op.f('ix_detalle_devoluciones_id_producto'), 'detalle_devoluciones', ['id_producto'], unique=False)


def downgrade() -> None:
    """Downgrade schema: elimina detalle_devoluciones + devoluciones."""
    op.drop_index(op.f('ix_detalle_devoluciones_id_producto'), table_name='detalle_devoluciones')
    op.drop_index(op.f('ix_detalle_devoluciones_id_detalle_venta'), table_name='detalle_devoluciones')
    op.drop_index(op.f('ix_detalle_devoluciones_id_devolucion'), table_name='detalle_devoluciones')
    op.drop_index(op.f('ix_detalle_devoluciones_id_detalle'), table_name='detalle_devoluciones')
    op.drop_table('detalle_devoluciones')

    op.drop_index(op.f('ix_devoluciones_estado'), table_name='devoluciones')
    op.drop_index(op.f('ix_devoluciones_id_procesador'), table_name='devoluciones')
    op.drop_index(op.f('ix_devoluciones_id_solicitante'), table_name='devoluciones')
    op.drop_index(op.f('ix_devoluciones_id_cliente'), table_name='devoluciones')
    op.drop_index(op.f('ix_devoluciones_id_venta'), table_name='devoluciones')
    op.drop_index(op.f('ix_devoluciones_id_devolucion'), table_name='devoluciones')
    op.drop_table('devoluciones')
