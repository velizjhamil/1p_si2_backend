"""CU8 probador virtual: fotos_usuario y simulaciones_probador

Revision ID: e1b7c5d0a4f8
Revises: d0a6b4c9e3f7
Create Date: 2026-09-13 21:00:00.000000

Migración MANUAL (no autogenerada): crea las tablas `fotos_usuario` y
`simulaciones_probador` del CU8 (Probador Virtual AR). La imagen viaja
como Data URL base64 (columna TEXT de amplio ancho).
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID


# revision identifiers, used by Alembic.
revision: str = 'e1b7c5d0a4f8'
down_revision: Union[str, Sequence[str], None] = 'd0a6b4c9e3f7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # --- CU8: fotos_usuario -----------------------------------------------------
    op.create_table('fotos_usuario',
    sa.Column('id_foto', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('id_usuario', UUID(as_uuid=True), nullable=False),
    sa.Column('url_imagen', sa.String(length=4000000), nullable=False),
    sa.Column('fecha_subida', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('estatura_cm', sa.Integer(), nullable=True),
    sa.Column('peso_kg', sa.Integer(), nullable=True),
    sa.Column('complexion', sa.String(length=20), nullable=False),
    sa.ForeignKeyConstraint(['id_usuario'], ['usuarios.id_usuario'], name=op.f('fk_fotos_usuario_id_usuario_usuarios')),
    sa.PrimaryKeyConstraint('id_foto', name=op.f('pk_fotos_usuario')),
    sa.CheckConstraint("complexion IN ('DELGADA', 'MEDIA', 'ROBUSTA', 'NO_INDICADA')", name='complexion_valida'),
    )
    op.create_index(op.f('ix_fotos_usuario_id_foto'), 'fotos_usuario', ['id_foto'], unique=False)
    op.create_index(op.f('ix_fotos_usuario_id_usuario'), 'fotos_usuario', ['id_usuario'], unique=False)

    # --- CU8: simulaciones_probador ----------------------------------------------
    op.create_table('simulaciones_probador',
    sa.Column('id_simulacion', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('id_usuario', UUID(as_uuid=True), nullable=False),
    sa.Column('id_producto', sa.Integer(), nullable=False),
    sa.Column('id_foto', sa.Integer(), nullable=False),
    sa.Column('url_resultado', sa.String(length=4000000), nullable=False),
    sa.Column('talla_elegida', sa.String(length=20), nullable=False),
    sa.Column('talla_recomendada', sa.String(length=20), nullable=False),
    sa.Column('ajuste_estimado', sa.String(length=20), nullable=False),
    sa.Column('color_nombre', sa.String(length=50), nullable=True),
    sa.Column('color_hex', sa.String(length=7), nullable=True),
    sa.Column('fecha_simulacion', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['id_usuario'], ['usuarios.id_usuario'], name=op.f('fk_simulaciones_probador_id_usuario_usuarios')),
    sa.ForeignKeyConstraint(['id_producto'], ['productos.id_producto'], name=op.f('fk_simulaciones_probador_id_producto_productos')),
    sa.ForeignKeyConstraint(['id_foto'], ['fotos_usuario.id_foto'], ondelete='CASCADE', name=op.f('fk_simulaciones_probador_id_foto_fotos_usuario')),
    sa.PrimaryKeyConstraint('id_simulacion', name=op.f('pk_simulaciones_probador')),
    sa.CheckConstraint("ajuste_estimado IN ('PERFECTO', 'AJUSTADO', 'HOLGADO')", name='ajuste_estimado_valido'),
    sa.CheckConstraint("talla_elegida IS NOT NULL AND talla_recomendada IS NOT NULL", name='tallas_obligatorias'),
    )
    op.create_index(op.f('ix_simulaciones_probador_id_simulacion'), 'simulaciones_probador', ['id_simulacion'], unique=False)
    op.create_index(op.f('ix_simulaciones_probador_id_usuario'), 'simulaciones_probador', ['id_usuario'], unique=False)
    op.create_index(op.f('ix_simulaciones_probador_id_producto'), 'simulaciones_probador', ['id_producto'], unique=False)
    op.create_index(op.f('ix_simulaciones_probador_id_foto'), 'simulaciones_probador', ['id_foto'], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f('ix_simulaciones_probador_id_foto'), table_name='simulaciones_probador')
    op.drop_index(op.f('ix_simulaciones_probador_id_producto'), table_name='simulaciones_probador')
    op.drop_index(op.f('ix_simulaciones_probador_id_usuario'), table_name='simulaciones_probador')
    op.drop_index(op.f('ix_simulaciones_probador_id_simulacion'), table_name='simulaciones_probador')
    op.drop_table('simulaciones_probador')
    op.drop_index(op.f('ix_fotos_usuario_id_usuario'), table_name='fotos_usuario')
    op.drop_index(op.f('ix_fotos_usuario_id_foto'), table_name='fotos_usuario')
    op.drop_table('fotos_usuario')
