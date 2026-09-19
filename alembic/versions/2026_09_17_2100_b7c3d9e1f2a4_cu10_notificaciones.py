"""CU10 notificaciones

Revision ID: b7c3d9e1f2a4
Revises: a1d2e3f4b5c6
Create Date: 2026-09-17 21:00:00.000000

Migracion MANUAL (no autogenerada): crea la tabla `notificaciones`
del CU10 (Gestion de Notificaciones). Tabla unica (sin lineas: la
notificacion es un mensaje atomico).

Decisiones:
- `notificaciones.id_usuario` referencia `usuarios.id_usuario` con
  ondelete CASCADE: si se borra el usuario, se borran sus
  notificaciones (no tiene sentido mantenerlas huerfanas).
- CHECKs en DB para `tipo` (8 valores) y longitudes de titulo/mensaje
  (defensa en profundidad, Pydantic ya acota pero la DB es la frontera
  dura para inserts via ORM directo).
- Indice compuesto `ix_notificaciones_usuario_leida_fecha` para el
  patron mas frecuente del header: "dame las no leidas del usuario X
  ordenadas por fecha DESC".
- Indice simple en `leida` (consulta del contador del header) y en
  `tipo` (filtros futuros por tipo, ej: solo STOCK).
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b7c3d9e1f2a4'
down_revision: Union[str, Sequence[str], None] = 'a1d2e3f4b5c6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema: crea notificaciones."""
    op.create_table(
        'notificaciones',
        sa.Column('id_notificacion', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('id_usuario', sa.dialects.postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('titulo', sa.String(length=100), nullable=False),
        sa.Column('mensaje', sa.String(length=500), nullable=False),
        sa.Column('tipo', sa.String(length=20), nullable=False, server_default='INFO'),
        sa.Column('leida', sa.Boolean(), nullable=False, server_default=sa.text('false')),
        sa.Column(
            'fecha_creacion',
            sa.DateTime(timezone=True),
            server_default=sa.text('now()'),
            nullable=False,
        ),
        sa.Column('referencia_tipo', sa.String(length=30), nullable=True),
        sa.Column('referencia_id', sa.String(length=64), nullable=True),
        sa.PrimaryKeyConstraint('id_notificacion', name=op.f('pk_notificaciones')),
        sa.ForeignKeyConstraint(
            ['id_usuario'],
            ['usuarios.id_usuario'],
            name=op.f('fk_notificaciones_id_usuario_usuarios'),
            ondelete='CASCADE',
        ),
        sa.CheckConstraint(
            "tipo IN ('INFO', 'WARNING', 'ERROR', 'SUCCESS', 'STOCK', "
            "'PEDIDO', 'DEVOLUCION', 'SISTEMA')",
            name='tipo_notificacion_valido',
        ),
        sa.CheckConstraint(
            'length(titulo) >= 1 AND length(titulo) <= 100',
            name='titulo_longitud_valida',
        ),
        sa.CheckConstraint(
            'length(mensaje) >= 1 AND length(mensaje) <= 500',
            name='mensaje_longitud_valida',
        ),
    )
    # Indices simples
    op.create_index(
        op.f('ix_notificaciones_id_notificacion'),
        'notificaciones',
        ['id_notificacion'],
        unique=False,
    )
    op.create_index(
        op.f('ix_notificaciones_leida'), 'notificaciones', ['leida'], unique=False
    )
    op.create_index(
        op.f('ix_notificaciones_tipo'), 'notificaciones', ['tipo'], unique=False
    )
    # Indice compuesto: patron mas frecuente del header (bandeja del usuario).
    op.create_index(
        'ix_notificaciones_usuario_leida_fecha',
        'notificaciones',
        ['id_usuario', 'leida', 'fecha_creacion'],
        unique=False,
    )


def downgrade() -> None:
    """Downgrade schema: elimina notificaciones."""
    op.drop_index('ix_notificaciones_usuario_leida_fecha', table_name='notificaciones')
    op.drop_index(op.f('ix_notificaciones_tipo'), table_name='notificaciones')
    op.drop_index(op.f('ix_notificaciones_leida'), table_name='notificaciones')
    op.drop_index(
        op.f('ix_notificaciones_id_notificacion'), table_name='notificaciones'
    )
    op.drop_table('notificaciones')
