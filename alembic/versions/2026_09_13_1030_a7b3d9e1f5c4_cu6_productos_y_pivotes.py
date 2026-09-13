"""CU6 productos de ropa y pivotes tallas/colores

Revision ID: a7b3d9e1f5c4
Revises: f2a9c8e4d1b3
Create Date: 2026-09-13 10:30:00.000000

Migración MANUAL (no autogenerada): crea la tabla `productos` (CU6) y
las tablas pivote N:M `producto_tallas` / `producto_colores`, con seed
demo de 10 prendas usando los IDs reales de los catálogos ya sembrados
(categorias e5f8a3b7c2d9, tallas/colores f2a9c8e4d1b3, proveedores
c3d7e9a1f4b2).
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a7b3d9e1f5c4'
down_revision: Union[str, Sequence[str], None] = 'f2a9c8e4d1b3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # --- CU6: productos -------------------------------------------------------
    op.create_table('productos',
    sa.Column('id_producto', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('nombre', sa.String(length=150), nullable=False),
    sa.Column('id_categoria', sa.Integer(), nullable=False),
    sa.Column('id_proveedor', sa.Integer(), nullable=True),
    sa.Column('precio_venta', sa.Numeric(10, 2), nullable=False),
    sa.Column('stock_total', sa.Integer(), nullable=False),
    sa.Column('imagen_url', sa.String(length=500), nullable=True),
    sa.Column('descripcion', sa.String(length=500), nullable=True),
    sa.Column('estado', sa.String(length=20), nullable=False),
    sa.Column('fecha_creacion', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('fecha_actualizacion', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['id_categoria'], ['categorias.id_categoria'], name=op.f('fk_productos_id_categoria_categorias')),
    sa.ForeignKeyConstraint(['id_proveedor'], ['proveedores.id_proveedor'], name=op.f('fk_productos_id_proveedor_proveedores')),
    sa.PrimaryKeyConstraint('id_producto', name=op.f('pk_productos')),
    sa.CheckConstraint("estado IN ('Activo', 'Inactivo', 'Agotado')", name='estado_producto_valido'),
    sa.CheckConstraint("precio_venta > 0", name='precio_venta_positivo'),
    sa.CheckConstraint("stock_total >= 0", name='stock_total_no_negativo'),
    )
    op.create_index(op.f('ix_productos_id_producto'), 'productos', ['id_producto'], unique=False)
    op.create_index(op.f('ix_productos_nombre'), 'productos', ['nombre'], unique=False)
    op.create_index(op.f('ix_productos_id_categoria'), 'productos', ['id_categoria'], unique=False)
    op.create_index(op.f('ix_productos_id_proveedor'), 'productos', ['id_proveedor'], unique=False)

    # --- CU6: pivotes N:M -----------------------------------------------------
    # ondelete CASCADE: al borrar un producto desaparecen sus asociaciones.
    op.create_table('producto_tallas',
    sa.Column('id_producto', sa.Integer(), nullable=False),
    sa.Column('id_talla', sa.Integer(), nullable=False),
    sa.ForeignKeyConstraint(['id_producto'], ['productos.id_producto'], ondelete='CASCADE', name=op.f('fk_producto_tallas_id_producto_productos')),
    sa.ForeignKeyConstraint(['id_talla'], ['tallas.id_talla'], ondelete='CASCADE', name=op.f('fk_producto_tallas_id_talla_tallas')),
    sa.PrimaryKeyConstraint('id_producto', 'id_talla', name=op.f('pk_producto_tallas')),
    )
    op.create_table('producto_colores',
    sa.Column('id_producto', sa.Integer(), nullable=False),
    sa.Column('id_color', sa.Integer(), nullable=False),
    sa.ForeignKeyConstraint(['id_producto'], ['productos.id_producto'], ondelete='CASCADE', name=op.f('fk_producto_colores_id_producto_productos')),
    sa.ForeignKeyConstraint(['id_color'], ['colores.id_color'], ondelete='CASCADE', name=op.f('fk_producto_colores_id_color_colores')),
    sa.PrimaryKeyConstraint('id_producto', 'id_color', name=op.f('pk_producto_colores')),
    )

    # Semilla: 10 prendas demo alineadas a los catálogos reales.
    # FKs: categorias 1-8 (e5f8a3b7c2d9), proveedores 2/3/4/5/7 (c3d7e9a1f4b2),
    # tallas XS-XL (f2a9c8e4d1b3, ids 1-5), colores Negro/Blanco/Azul/Rojo (ids 1-4).
    productos = sa.table('productos',
        sa.column('nombre', sa.String),
        sa.column('id_categoria', sa.Integer),
        sa.column('id_proveedor', sa.Integer),
        sa.column('precio_venta', sa.Numeric),
        sa.column('stock_total', sa.Integer),
        sa.column('imagen_url', sa.String),
        sa.column('descripcion', sa.String),
        sa.column('estado', sa.String),
    )
    op.bulk_insert(productos, [
        # cat 1=Camisas(Hombre) | proveedor 4=Confecciones Santa Cruz
        {'nombre': 'Camisa Oxford Formal', 'id_categoria': 1, 'id_proveedor': 4,
         'precio_venta': 189.90, 'stock_total': 42,
         'imagen_url': 'https://placehold.co/400x500/1e4d8c/ffffff?text=Camisa+Oxford',
         'descripcion': 'Camisa de algodón con cuello inglés, ideal para oficina.',
         'estado': 'Activo'},
        # cat 5=Poleras(Unisex) | proveedor 7=Textiles Bolivia S.R.L.
        {'nombre': 'Polo Básico Algodón', 'id_categoria': 5, 'id_proveedor': 7,
         'precio_venta': 79.50, 'stock_total': 120,
         'imagen_url': 'https://placehold.co/400x500/1e4d8c/ffffff?text=Polo+Basico',
         'descripcion': 'Polo de algodón peinado, corte regular.',
         'estado': 'Activo'},
        # cat 2=Pantalones(Hombre) | proveedor 5=Distribuidora Denim Express
        {'nombre': 'Jean Slim Fit', 'id_categoria': 2, 'id_proveedor': 5,
         'precio_venta': 249.00, 'stock_total': 65,
         'imagen_url': 'https://placehold.co/400x500/1e4d8c/ffffff?text=Jean+Slim',
         'descripcion': 'Jean stretch slim fit de 12 oz.',
         'estado': 'Activo'},
        # cat 3=Vestidos(Mujer) | proveedor 4
        {'nombre': 'Vestido Midi Floral', 'id_categoria': 3, 'id_proveedor': 4,
         'precio_venta': 320.00, 'stock_total': 18,
         'imagen_url': 'https://placehold.co/400x500/1e4d8c/ffffff?text=Vestido+Midi',
         'descripcion': 'Vestido midi con estampado floral de temporada.',
         'estado': 'Activo'},
        # cat 4=Blusas(Mujer) | proveedor 7 — stock 0 => Agotado
        {'nombre': 'Blusa Seda Mariposa', 'id_categoria': 4, 'id_proveedor': 7,
         'precio_venta': 210.00, 'stock_total': 0,
         'imagen_url': 'https://placehold.co/400x500/1e4d8c/ffffff?text=Blusa+Seda',
         'descripcion': 'Blusa de seda con lazo al cuello.',
         'estado': 'Agotado'},
        # cat 6=Chaquetas(Unisex) | proveedor 5
        {'nombre': 'Chaqueta Mezclilla Clásica', 'id_categoria': 6, 'id_proveedor': 5,
         'precio_venta': 420.00, 'stock_total': 12,
         'imagen_url': 'https://placehold.co/400x500/1e4d8c/ffffff?text=Chaqueta',
         'descripcion': 'Chaqueta de mezclilla con botones metálicos.',
         'estado': 'Activo'},
        # cat 7=Faldas(Mujer) | proveedor 4
        {'nombre': 'Falda Plisada Talle Alto', 'id_categoria': 7, 'id_proveedor': 4,
         'precio_venta': 165.00, 'stock_total': 24,
         'imagen_url': 'https://placehold.co/400x500/1e4d8c/ffffff?text=Falda+Plisada',
         'descripcion': 'Falda plisada con resorte en talle alto.',
         'estado': 'Activo'},
        # cat 2=Pantalones | proveedor 7
        {'nombre': 'Pantalón Vestir Clásico', 'id_categoria': 2, 'id_proveedor': 7,
         'precio_venta': 280.00, 'stock_total': 33,
         'imagen_url': 'https://placehold.co/400x500/1e4d8c/ffffff?text=Pantalon',
         'descripcion': 'Pantalón de vestir con pinzas, tela antiarrugas.',
         'estado': 'Activo'},
        # cat 5=Poleras | proveedor 2=Calzados del Oriente S.A.
        {'nombre': 'Polo Deportivo Dry-Fit', 'id_categoria': 5, 'id_proveedor': 2,
         'precio_venta': 95.00, 'stock_total': 87,
         'imagen_url': 'https://placehold.co/400x500/1e4d8c/ffffff?text=Polo+DryFit',
         'descripcion': 'Polo técnico de secado rápido para entrenamiento.',
         'estado': 'Activo'},
        # cat 1=Camisas | proveedor 3=Importadora Andina Ltda. — Inactivo
        {'nombre': 'Camisa Lino Verano', 'id_categoria': 1, 'id_proveedor': 3,
         'precio_venta': 220.00, 'stock_total': 5,
         'imagen_url': 'https://placehold.co/400x500/1e4d8c/ffffff?text=Camisa+Lino',
         'descripcion': 'Camisa de lino fresco, manga larga.',
         'estado': 'Inactivo'},
    ])

    # Seed de asociaciones N:M (IDs: tallas 1=XS..5=XL, colores 1=Negro,
    # 2=Blanco, 3=Azul, 4=Rojo; productos 1..10 en orden de inserción).
    producto_tallas = sa.table('producto_tallas',
        sa.column('id_producto', sa.Integer),
        sa.column('id_talla', sa.Integer),
    )
    op.bulk_insert(producto_tallas, [
        # 1 Camisa Oxford: S M L XL
        {'id_producto': 1, 'id_talla': 2}, {'id_producto': 1, 'id_talla': 3},
        {'id_producto': 1, 'id_talla': 4}, {'id_producto': 1, 'id_talla': 5},
        # 2 Polo Básico: S M L XL
        {'id_producto': 2, 'id_talla': 2}, {'id_producto': 2, 'id_talla': 3},
        {'id_producto': 2, 'id_talla': 4}, {'id_producto': 2, 'id_talla': 5},
        # 3 Jean Slim: XS S M L XL (todas)
        {'id_producto': 3, 'id_talla': 1}, {'id_producto': 3, 'id_talla': 2},
        {'id_producto': 3, 'id_talla': 3}, {'id_producto': 3, 'id_talla': 4},
        {'id_producto': 3, 'id_talla': 5},
        # 4 Vestido Midi: XS S M
        {'id_producto': 4, 'id_talla': 1}, {'id_producto': 4, 'id_talla': 2},
        {'id_producto': 4, 'id_talla': 3},
        # 5 Blusa Seda: XS S M
        {'id_producto': 5, 'id_talla': 1}, {'id_producto': 5, 'id_talla': 2},
        {'id_producto': 5, 'id_talla': 3},
        # 6 Chaqueta: M L XL
        {'id_producto': 6, 'id_talla': 3}, {'id_producto': 6, 'id_talla': 4},
        {'id_producto': 6, 'id_talla': 5},
        # 7 Falda Plisada: S M L
        {'id_producto': 7, 'id_talla': 2}, {'id_producto': 7, 'id_talla': 3},
        {'id_producto': 7, 'id_talla': 4},
        # 8 Pantalón Vestir: XS S M L XL
        {'id_producto': 8, 'id_talla': 1}, {'id_producto': 8, 'id_talla': 2},
        {'id_producto': 8, 'id_talla': 3}, {'id_producto': 8, 'id_talla': 4},
        {'id_producto': 8, 'id_talla': 5},
        # 9 Polo Dry-Fit: S M L XL
        {'id_producto': 9, 'id_talla': 2}, {'id_producto': 9, 'id_talla': 3},
        {'id_producto': 9, 'id_talla': 4}, {'id_producto': 9, 'id_talla': 5},
        # 10 Camisa Lino: M L XL
        {'id_producto': 10, 'id_talla': 3}, {'id_producto': 10, 'id_talla': 4},
        {'id_producto': 10, 'id_talla': 5},
    ])

    producto_colores = sa.table('producto_colores',
        sa.column('id_producto', sa.Integer),
        sa.column('id_color', sa.Integer),
    )
    op.bulk_insert(producto_colores, [
        # 1 Camisa Oxford: Blanco Azul
        {'id_producto': 1, 'id_color': 2}, {'id_producto': 1, 'id_color': 3},
        # 2 Polo Básico: Negro Blanco
        {'id_producto': 2, 'id_color': 1}, {'id_producto': 2, 'id_color': 2},
        # 3 Jean Slim: Azul Negro
        {'id_producto': 3, 'id_color': 3}, {'id_producto': 3, 'id_color': 1},
        # 4 Vestido Midi: Azul (floral azul)
        {'id_producto': 4, 'id_color': 3},
        # 5 Blusa Seda: Blanco Rojo
        {'id_producto': 5, 'id_color': 2}, {'id_producto': 5, 'id_color': 4},
        # 6 Chaqueta: Azul
        {'id_producto': 6, 'id_color': 3},
        # 7 Falda Plisada: Negro
        {'id_producto': 7, 'id_color': 1},
        # 8 Pantalón Vestir: Negro Azul
        {'id_producto': 8, 'id_color': 1}, {'id_producto': 8, 'id_color': 3},
        # 9 Polo Dry-Fit: Negro Rojo
        {'id_producto': 9, 'id_color': 1}, {'id_producto': 9, 'id_color': 4},
        # 10 Camisa Lino: Blanco
        {'id_producto': 10, 'id_color': 2},
    ])


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table('producto_colores')
    op.drop_table('producto_tallas')
    op.drop_index(op.f('ix_productos_id_proveedor'), table_name='productos')
    op.drop_index(op.f('ix_productos_id_categoria'), table_name='productos')
    op.drop_index(op.f('ix_productos_nombre'), table_name='productos')
    op.drop_index(op.f('ix_productos_id_producto'), table_name='productos')
    op.drop_table('productos')
