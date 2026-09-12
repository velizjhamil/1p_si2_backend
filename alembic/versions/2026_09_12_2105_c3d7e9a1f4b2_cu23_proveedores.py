"""CU23 proveedores: id_proveedor PK, nit_rut, estado tri-estado, contacto_operativo

Revision ID: c3d7e9a1f4b2
Revises: a91c4d2fe8b1
Create Date: 2026-09-12 21:05:00.000000

Migración MANUAL (no autogenerada): crea la tabla `proveedores` con la
forma final del CU23 y siembra 5 proveedores demo. La tabla física no
existía (alembic la filtraba); la forma vieja del modelo (id/ruc/is_active)
nunca llegó a la DB, así que se crea fresh sin renames.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c3d7e9a1f4b2'
down_revision: Union[str, Sequence[str], None] = 'a91c4d2fe8b1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table('proveedores',
    sa.Column('id_proveedor', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('nombre', sa.String(length=150), nullable=False),
    sa.Column('nit_rut', sa.String(length=50), nullable=False),
    sa.Column('contacto_operativo', sa.String(length=150), nullable=True),
    sa.Column('telefono', sa.String(length=30), nullable=True),
    sa.Column('correo', sa.String(length=150), nullable=True),
    sa.Column('categoria', sa.String(length=100), nullable=True),
    sa.Column('estado', sa.String(length=20), nullable=False),
    sa.Column('direccion', sa.String(length=255), nullable=True),
    sa.Column('sucursal_id', sa.Integer(), nullable=True),
    sa.Column('fecha_creacion', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('fecha_actualizacion', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.PrimaryKeyConstraint('id_proveedor', name=op.f('pk_proveedores')),
    sa.CheckConstraint("estado IN ('Activo', 'Verificado', 'Inactivo')", name='estado_valido'),
    sa.ForeignKeyConstraint(['sucursal_id'], ['sucursales.codigo_sucursal'], name=op.f('fk_proveedores_sucursal_id_sucursales'))
    )
    op.create_index(op.f('ix_proveedores_id_proveedor'), 'proveedores', ['id_proveedor'], unique=False)
    op.create_index(op.f('ix_proveedores_nit_rut'), 'proveedores', ['nit_rut'], unique=True)

    # Semilla: 5 proveedores demo (textiles/calzado para tienda de ropa)
    op.bulk_insert(
        sa.table('proveedores',
                 sa.column('nombre', sa.String),
                 sa.column('nit_rut', sa.String),
                 sa.column('contacto_operativo', sa.String),
                 sa.column('telefono', sa.String),
                 sa.column('correo', sa.String),
                 sa.column('categoria', sa.String),
                 sa.column('estado', sa.String),
                 sa.column('direccion', sa.String),
                 sa.column('sucursal_id', sa.Integer)),
        [
            {'nombre': 'Textiles Bolivia S.R.L.', 'nit_rut': '1020304051',
             'contacto_operativo': 'Marcela Rojas', 'telefono': '+591 3 344-1101',
             'correo': 'ventas@textilesbolivia.com', 'categoria': 'Textiles',
             'estado': 'Activo', 'direccion': 'Parque Industrial Mz 12, Santa Cruz',
             'sucursal_id': 1},
            {'nombre': 'Calzados del Oriente S.A.', 'nit_rut': '1020304052',
             'contacto_operativo': 'Jorge Vargas', 'telefono': '+591 3 344-1102',
             'correo': 'compras@calzadosoriente.com', 'categoria': 'Calzado',
             'estado': 'Activo', 'direccion': 'Av. Banzer 4to Anillo, Santa Cruz',
             'sucursal_id': 2},
            {'nombre': 'Importadora Andina Ltda.', 'nit_rut': '1020304053',
             'contacto_operativo': 'Ricardo Paz', 'telefono': '+591 2 228-3303',
             'correo': 'ruperts@importadoraandina.com', 'categoria': 'Accesorios',
             'estado': 'Verificado', 'direccion': 'Calle Potosí 1450, La Paz',
             'sucursal_id': None},
            {'nombre': 'Confecciones Santa Cruz', 'nit_rut': '1020304054',
             'contacto_operativo': 'Luz María Núñez', 'telefono': '+591 3 355-4404',
             'correo': 'pedidos@confescruz.com', 'categoria': 'Textiles',
             'estado': 'Activo', 'direccion': 'Av. El Cristo s/n, Santa Cruz',
             'sucursal_id': 3},
            {'nombre': 'Distribuidora Denim Express', 'nit_rut': '1020304055',
             'contacto_operativo': 'Pablo Antelo', 'telefono': '+591 3 366-5505',
             'correo': 'info@denimexpress.com', 'categoria': 'Jeans',
             'estado': 'Inactivo', 'direccion': 'Radial 27, Santa Cruz',
             'sucursal_id': None},
        ],
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f('ix_proveedores_nit_rut'), table_name='proveedores')
    op.drop_index(op.f('ix_proveedores_id_proveedor'), table_name='proveedores')
    op.drop_table('proveedores')
