"""CU9 categorias + CU24 colecciones y temporadas (tablas del catalogo)

Revision ID: e5f8a3b7c2d9
Revises: c3d7e9a1f4b2
Create Date: 2026-09-12 23:30:00.000000

Migración MANUAL (no autogenerada): crea las 3 tablas del catálogo —
`categorias` (CU9) y `colecciones` + `temporadas` (CU24, que quedó definida
en models.py junto a este paso) — con sus seeds demo.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e5f8a3b7c2d9'
down_revision: Union[str, Sequence[str], None] = 'c3d7e9a1f4b2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # --- CU9: categorías ----------------------------------------------------
    op.create_table('categorias',
    sa.Column('id_categoria', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('nombre', sa.String(length=100), nullable=False),
    sa.Column('linea', sa.String(length=50), nullable=False),
    sa.Column('descripcion', sa.String(length=255), nullable=True),
    sa.Column('activo', sa.Boolean(), nullable=False),
    sa.Column('fecha_creacion', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.PrimaryKeyConstraint('id_categoria', name=op.f('pk_categorias')),
    sa.CheckConstraint("linea IN ('Hombre', 'Mujer', 'Unisex')", name='linea_valida')
    )
    op.create_index(op.f('ix_categorias_id_categoria'), 'categorias', ['id_categoria'], unique=False)
    op.create_index(op.f('ix_categorias_nombre'), 'categorias', ['nombre'], unique=False)
    op.create_index(op.f('ix_categorias_linea'), 'categorias', ['linea'], unique=False)

    # Semilla: 8 categorías demo de tienda de ropa
    op.bulk_insert(
        sa.table('categorias',
                 sa.column('nombre', sa.String),
                 sa.column('linea', sa.String),
                 sa.column('descripcion', sa.String),
                 sa.column('activo', sa.Boolean)),
        [
            {'nombre': 'Camisas', 'linea': 'Hombre',
             'descripcion': 'Camisas formales y casuales', 'activo': True},
            {'nombre': 'Pantalones', 'linea': 'Hombre',
             'descripcion': 'Jeans, chinos y vestimenta formal', 'activo': True},
            {'nombre': 'Vestidos', 'linea': 'Mujer',
             'descripcion': 'Vestidos de fiesta y casuales', 'activo': True},
            {'nombre': 'Blusas', 'linea': 'Mujer',
             'descripcion': 'Blusas de oficina y diario', 'activo': True},
            {'nombre': 'Poleras', 'linea': 'Unisex',
             'descripcion': 'Poleras básicas y estampadas', 'activo': True},
            {'nombre': 'Chaquetas', 'linea': 'Unisex',
             'descripcion': 'Chaquetas y abrigos de temporada', 'activo': True},
            {'nombre': 'Faldas', 'linea': 'Mujer',
             'descripcion': 'Faldas de todas las longitudes', 'activo': True},
            {'nombre': 'Shorts', 'linea': 'Hombre',
             'descripcion': 'Shorts deportivos y casuales', 'activo': False},
        ],
    )

    # --- CU24: colecciones ---------------------------------------------------
    op.create_table('colecciones',
    sa.Column('id_coleccion', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('nombre_coleccion', sa.String(length=100), nullable=False),
    sa.Column('fecha_creacion', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.PrimaryKeyConstraint('id_coleccion', name=op.f('pk_colecciones'))
    )
    op.create_index(op.f('ix_colecciones_id_coleccion'), 'colecciones', ['id_coleccion'], unique=False)
    op.create_index(op.f('ix_colecciones_nombre_coleccion'), 'colecciones', ['nombre_coleccion'], unique=True)

    # --- CU24: temporadas (FK opcional a colecciones) -------------------------
    op.create_table('temporadas',
    sa.Column('id_temporada', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('id_coleccion', sa.Integer(), nullable=True),
    sa.Column('nombre_temporada', sa.String(length=100), nullable=False),
    sa.Column('fecha_inicio', sa.Date(), nullable=False),
    sa.Column('fecha_fin', sa.Date(), nullable=False),
    sa.Column('fecha_creacion', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['id_coleccion'], ['colecciones.id_coleccion'], name=op.f('fk_temporadas_id_coleccion_colecciones')),
    sa.PrimaryKeyConstraint('id_temporada', name=op.f('pk_temporadas'))
    )
    op.create_index(op.f('ix_temporadas_id_temporada'), 'temporadas', ['id_temporada'], unique=False)
    op.create_index(op.f('ix_temporadas_id_coleccion'), 'temporadas', ['id_coleccion'], unique=False)
    op.create_index(op.f('ix_temporadas_nombre_temporada'), 'temporadas', ['nombre_temporada'], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f('ix_temporadas_nombre_temporada'), table_name='temporadas')
    op.drop_index(op.f('ix_temporadas_id_coleccion'), table_name='temporadas')
    op.drop_index(op.f('ix_temporadas_id_temporada'), table_name='temporadas')
    op.drop_table('temporadas')
    op.drop_index(op.f('ix_colecciones_nombre_coleccion'), table_name='colecciones')
    op.drop_index(op.f('ix_colecciones_id_coleccion'), table_name='colecciones')
    op.drop_table('colecciones')
    op.drop_index(op.f('ix_categorias_linea'), table_name='categorias')
    op.drop_index(op.f('ix_categorias_nombre'), table_name='categorias')
    op.drop_index(op.f('ix_categorias_id_categoria'), table_name='categorias')
    op.drop_table('categorias')
