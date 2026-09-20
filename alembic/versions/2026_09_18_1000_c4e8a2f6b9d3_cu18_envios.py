"""CU18 envios

Revision ID: c4e8a2f6b9d3
Revises: b7c3d9e1f2a4
Create Date: 2026-09-18 10:00:00.000000

Migracion MANUAL (no autogenerada) del CU18 (Gestion de Envio):

1. `ventas.tipo_entrega` (DOMICILIO | RETIRO), NOT NULL con
   server_default 'DOMICILIO' para no romper las filas existentes. Las ventas
   POS previas (id_vendedor NOT NULL) se corrigen a 'RETIRO': se cobraron en
   mostrador, no requieren envio.
2. `envios`: un envio por venta (UNIQUE en id_venta) con estado, sucursal
   responsable, repartidor, fechas y motivo de fallo.
3. `envio_historial`: bitacora append-only de cambios de estado.

Decisiones:
- NO se hace backfill de envios para las ventas online anteriores a CU18:
  no hay datos confiables de su estado real de despacho. Un GS/ASU puede
  iniciarlas una a una con POST /api/v1/envios.
- `envios.codigo_sucursal` es NULL solo en PREPARANDO/CANCELADO (CHECK
  `sucursal_requerida_al_despachar`): la sucursal se fija al confirmar la
  preparacion.
- `envios.id_venta` sin ondelete (RESTRICT): no se puede borrar una venta
  que ya tiene envio. `envio_historial.id_envio` es CASCADE; los FK a
  usuarios son SET NULL para conservar la bitacora si se elimina un usuario.
- CHECKs en DB para estados (defensa en profundidad; las transiciones las
  valida el service).
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID


# revision identifiers, used by Alembic.
revision: str = 'c4e8a2f6b9d3'
down_revision: Union[str, Sequence[str], None] = 'b7c3d9e1f2a4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_ESTADOS = (
    "'PREPARANDO', 'LISTO_ENVIO', 'ASIGNADO', 'EN_RUTA', "
    "'ENTREGADO', 'INTENTO_FALLIDO', 'REPROGRAMADO', 'CANCELADO'"
)


def upgrade() -> None:
    """Upgrade schema: ventas.tipo_entrega + envios + envio_historial."""
    # --- 1. ventas.tipo_entrega -------------------------------------------
    op.add_column(
        'ventas',
        sa.Column(
            'tipo_entrega',
            sa.String(length=20),
            nullable=False,
            server_default='DOMICILIO',
        ),
    )
    # Ventas POS previas: entrega en mostrador, no a domicilio.
    op.execute("UPDATE ventas SET tipo_entrega = 'RETIRO' WHERE id_vendedor IS NOT NULL")
    op.create_check_constraint(
        op.f('ck_ventas_tipo_entrega_valido'),
        'ventas',
        "tipo_entrega IN ('DOMICILIO', 'RETIRO')",
    )

    # --- 2. envios ---------------------------------------------------------
    op.create_table(
        'envios',
        sa.Column('id_envio', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('id_venta', sa.Integer(), nullable=False),
        sa.Column('estado', sa.String(length=20), nullable=False),
        sa.Column('codigo_sucursal', sa.Integer(), nullable=True),
        sa.Column('id_repartidor', UUID(as_uuid=True), nullable=True),
        sa.Column('fecha_estimada_entrega', sa.DateTime(timezone=True), nullable=True),
        sa.Column('fecha_entrega_real', sa.DateTime(timezone=True), nullable=True),
        sa.Column('motivo_fallo', sa.String(length=255), nullable=True),
        sa.Column('fecha_reprogramacion', sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            'intentos_fallidos', sa.Integer(), nullable=False, server_default='0'
        ),
        sa.Column(
            'fecha_creacion',
            sa.DateTime(timezone=True),
            server_default=sa.text('now()'),
            nullable=False,
        ),
        sa.Column(
            'fecha_actualizacion',
            sa.DateTime(timezone=True),
            server_default=sa.text('now()'),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint('id_envio', name=op.f('pk_envios')),
        sa.ForeignKeyConstraint(
            ['id_venta'], ['ventas.id_venta'], name=op.f('fk_envios_id_venta_ventas')
        ),
        sa.ForeignKeyConstraint(
            ['codigo_sucursal'],
            ['sucursales.codigo_sucursal'],
            name=op.f('fk_envios_codigo_sucursal_sucursales'),
        ),
        sa.ForeignKeyConstraint(
            ['id_repartidor'],
            ['usuarios.id_usuario'],
            name=op.f('fk_envios_id_repartidor_usuarios'),
            ondelete='SET NULL',
        ),
        sa.CheckConstraint(f"estado IN ({_ESTADOS})", name='estado_envio_valido'),
        sa.CheckConstraint('intentos_fallidos >= 0', name='intentos_no_negativos'),
        sa.CheckConstraint(
            "estado IN ('PREPARANDO', 'CANCELADO') OR codigo_sucursal IS NOT NULL",
            name='sucursal_requerida_al_despachar',
        ),
    )
    op.create_index(op.f('ix_envios_id_envio'), 'envios', ['id_envio'], unique=False)
    # UNIQUE: exactamente un envio por venta.
    op.create_index(op.f('ix_envios_id_venta'), 'envios', ['id_venta'], unique=True)
    op.create_index(op.f('ix_envios_estado'), 'envios', ['estado'], unique=False)
    op.create_index(
        op.f('ix_envios_codigo_sucursal'), 'envios', ['codigo_sucursal'], unique=False
    )
    op.create_index(
        op.f('ix_envios_id_repartidor'), 'envios', ['id_repartidor'], unique=False
    )

    # --- 3. envio_historial ------------------------------------------------
    op.create_table(
        'envio_historial',
        sa.Column('id_historial', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('id_envio', sa.Integer(), nullable=False),
        sa.Column('estado_anterior', sa.String(length=20), nullable=True),
        sa.Column('estado_nuevo', sa.String(length=20), nullable=False),
        sa.Column('id_usuario', UUID(as_uuid=True), nullable=True),
        sa.Column('observacion', sa.String(length=500), nullable=True),
        sa.Column(
            'fecha',
            sa.DateTime(timezone=True),
            server_default=sa.text('now()'),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint('id_historial', name=op.f('pk_envio_historial')),
        sa.ForeignKeyConstraint(
            ['id_envio'],
            ['envios.id_envio'],
            name=op.f('fk_envio_historial_id_envio_envios'),
            ondelete='CASCADE',
        ),
        sa.ForeignKeyConstraint(
            ['id_usuario'],
            ['usuarios.id_usuario'],
            name=op.f('fk_envio_historial_id_usuario_usuarios'),
            ondelete='SET NULL',
        ),
        sa.CheckConstraint(f"estado_nuevo IN ({_ESTADOS})", name='estado_nuevo_valido'),
        sa.CheckConstraint(
            f"estado_anterior IS NULL OR estado_anterior IN ({_ESTADOS})",
            name='estado_anterior_valido',
        ),
    )
    op.create_index(
        op.f('ix_envio_historial_id_historial'),
        'envio_historial',
        ['id_historial'],
        unique=False,
    )
    op.create_index(
        op.f('ix_envio_historial_id_envio'), 'envio_historial', ['id_envio'], unique=False
    )
    op.create_index(
        op.f('ix_envio_historial_id_usuario'),
        'envio_historial',
        ['id_usuario'],
        unique=False,
    )


def downgrade() -> None:
    """Downgrade schema: elimina envios y ventas.tipo_entrega.

    ATENCION: borra TODOS los envios y su historial (datos de CU18).
    """
    op.drop_index(op.f('ix_envio_historial_id_usuario'), table_name='envio_historial')
    op.drop_index(op.f('ix_envio_historial_id_envio'), table_name='envio_historial')
    op.drop_index(op.f('ix_envio_historial_id_historial'), table_name='envio_historial')
    op.drop_table('envio_historial')

    op.drop_index(op.f('ix_envios_id_repartidor'), table_name='envios')
    op.drop_index(op.f('ix_envios_codigo_sucursal'), table_name='envios')
    op.drop_index(op.f('ix_envios_estado'), table_name='envios')
    op.drop_index(op.f('ix_envios_id_venta'), table_name='envios')
    op.drop_index(op.f('ix_envios_id_envio'), table_name='envios')
    op.drop_table('envios')

    op.drop_constraint(op.f('ck_ventas_tipo_entrega_valido'), 'ventas', type_='check')
    op.drop_column('ventas', 'tipo_entrega')
