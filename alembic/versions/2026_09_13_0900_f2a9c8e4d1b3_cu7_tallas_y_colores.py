"""CU7 tallas y colores (variantes del catalogo)

Revision ID: f2a9c8e4d1b3
Revises: e5f8a3b7c2d9
Create Date: 2026-09-13 09:00:00.000000

Migración MANUAL (no autogenerada): crea las 2 tablas del CU7 —
`tallas` y `colores` (entidades definidas en app/modules/inventario/
models.py) — con sus seeds demo (las mismas del mock del frontend).
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'f2a9c8e4d1b3'
down_revision: Union[str, Sequence[str], None] = 'e5f8a3b7c2d9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # --- CU7: tallas ----------------------------------------------------------
    op.create_table('tallas',
    sa.Column('id_talla', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('nombre_talla', sa.String(length=20), nullable=False),
    sa.Column('descripcion', sa.String(length=255), nullable=True),
    sa.Column('activo', sa.Boolean(), nullable=False),
    sa.Column('fecha_creacion', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.PrimaryKeyConstraint('id_talla', name=op.f('pk_tallas')),
    sa.UniqueConstraint('nombre_talla', name=op.f('uq_tallas_nombre_talla'))
    )
    op.create_index(op.f('ix_tallas_id_talla'), 'tallas', ['id_talla'], unique=False)
    op.create_index(op.f('ix_tallas_nombre_talla'), 'tallas', ['nombre_talla'], unique=True)

    # Semilla: tallas demo de tienda de ropa (las mismas del mock del frontend)
    op.bulk_insert(
        sa.table('tallas',
                 sa.column('nombre_talla', sa.String),
                 sa.column('descripcion', sa.String),
                 sa.column('activo', sa.Boolean)),
        [
            {'nombre_talla': 'XS', 'descripcion': 'Extra pequeño', 'activo': True},
            {'nombre_talla': 'S', 'descripcion': 'Small', 'activo': True},
            {'nombre_talla': 'M', 'descripcion': 'Medium', 'activo': True},
            {'nombre_talla': 'L', 'descripcion': 'Large', 'activo': True},
            {'nombre_talla': 'XL', 'descripcion': 'Extra large', 'activo': True},
        ],
    )

    # --- CU7: colores ---------------------------------------------------------
    op.create_table('colores',
    sa.Column('id_color', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('nombre_color', sa.String(length=50), nullable=False),
    sa.Column('codigo_hex', sa.String(length=7), nullable=False),
    sa.Column('descripcion', sa.String(length=255), nullable=True),
    sa.Column('activo', sa.Boolean(), nullable=False),
    sa.Column('fecha_creacion', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.PrimaryKeyConstraint('id_color', name=op.f('pk_colores')),
    sa.UniqueConstraint('nombre_color', name=op.f('uq_colores_nombre_color')),
    sa.UniqueConstraint('codigo_hex', name=op.f('uq_colores_codigo_hex'))
    )
    op.create_index(op.f('ix_colores_id_color'), 'colores', ['id_color'], unique=False)
    op.create_index(op.f('ix_colores_nombre_color'), 'colores', ['nombre_color'], unique=True)
    op.create_index(op.f('ix_colores_codigo_hex'), 'colores', ['codigo_hex'], unique=True)

    # Semilla: colores demo (los mismos del mock del frontend)
    op.bulk_insert(
        sa.table('colores',
                 sa.column('nombre_color', sa.String),
                 sa.column('codigo_hex', sa.String),
                 sa.column('descripcion', sa.String),
                 sa.column('activo', sa.Boolean)),
        [
            {'nombre_color': 'Negro', 'codigo_hex': '#000000',
             'descripcion': 'Negro absoluto', 'activo': True},
            {'nombre_color': 'Blanco', 'codigo_hex': '#ffffff',
             'descripcion': 'Blanco puro', 'activo': True},
            {'nombre_color': 'Azul', 'codigo_hex': '#1d528d',
             'descripcion': 'Azul Attention', 'activo': True},
            {'nombre_color': 'Rojo', 'codigo_hex': '#dc2626',
             'descripcion': 'Rojo pasión', 'activo': True},
        ],
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f('ix_colores_codigo_hex'), table_name='colores')
    op.drop_index(op.f('ix_colores_nombre_color'), table_name='colores')
    op.drop_index(op.f('ix_colores_id_color'), table_name='colores')
    op.drop_table('colores')
    op.drop_index(op.f('ix_tallas_nombre_talla'), table_name='tallas')
    op.drop_index(op.f('ix_tallas_id_talla'), table_name='tallas')
    op.drop_table('tallas')
