"""CU17 sucursales: codigo_sucursal PK, id_ciudad FK, horario_atencion + ciudades

Revision ID: a91c4d2fe8b1
Revises: 816f55fba873
Create Date: 2026-09-12 20:10:00.000000

Migración MANUAL (no autogenerada):
1. Crea la tabla `ciudades` (catálogo) y siembra ciudades de Bolivia.
2. Renombra la PK de sucursales: id -> codigo_sucursal (op.alter_column).
3. Agrega horario_atencion (varchar 100) y id_ciudad (FK a ciudades.id).
4. Siembra 3 sucursales demo (Central, El Tesoro, Equipetrol).

autogenerate NO detecta renames de columna (propone drop+add y perdería
datos); por eso esta migración se escribe a mano.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a91c4d2fe8b1'
down_revision: Union[str, Sequence[str], None] = '816f55fba873'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # --- 1. Catálogo de ciudades -------------------------------------------
    op.create_table('ciudades',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('nombre', sa.String(length=100), nullable=False),
    sa.Column('departamento', sa.String(length=100), nullable=True),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_ciudades'))
    )
    op.create_index(op.f('ix_ciudades_id'), 'ciudades', ['id'], unique=False)
    op.create_index(op.f('ix_ciudades_nombre'), 'ciudades', ['nombre'], unique=False)

    # Semilla: ciudades donde opera Attention (Bolivia)
    ciudades = [
        ('Santa Cruz de la Sierra', 'Santa Cruz'),
        ('La Paz', 'La Paz'),
        ('Cochabamba', 'Cochabamba'),
        ('El Alto', 'La Paz'),
        ('Oruro', 'Oruro'),
        ('Sucre', 'Chuquisaca'),
        ('Tarija', 'Tarija'),
        ('Potosí', 'Potosí'),
        ('Trinidad', 'Beni'),
        ('Cobija', 'Pando'),
    ]
    op.bulk_insert(
        sa.table('ciudades',
                 sa.column('nombre', sa.String),
                 sa.column('departamento', sa.String)),
        [{'nombre': n, 'departamento': d} for n, d in ciudades],
    )

    # --- 2. Renombrar PK id -> codigo_sucursal ------------------------------
    op.alter_column(
        'sucursales',
        'id',
        new_column_name='codigo_sucursal',
        existing_type=sa.Integer(),
        existing_nullable=False,
    )

    # --- 3. Columnas nuevas de CU17 -----------------------------------------
    op.add_column('sucursales',
                  sa.Column('horario_atencion', sa.String(length=100), nullable=True))
    op.add_column('sucursales',
                  sa.Column('id_ciudad', sa.Integer(), nullable=True))

    # Asignar la ciudad por defecto (Santa Cruz) a las sucursales existentes
    # (hoy 0 filas; el UPDATE es defensivo para futuros despliegues).
    op.execute(
        "UPDATE sucursales SET id_ciudad = (SELECT id FROM ciudades WHERE nombre = 'Santa Cruz de la Sierra' LIMIT 1) WHERE id_ciudad IS NULL"
    )

    op.alter_column('sucursales', 'id_ciudad',
                    existing_type=sa.Integer(), nullable=False)
    op.create_index(op.f('ix_sucursales_id_ciudad'), 'sucursales', ['id_ciudad'], unique=False)
    op.create_foreign_key(
        op.f('fk_sucursales_id_ciudad_ciudades'),
        'sucursales', 'ciudades',
        ['id_ciudad'], ['id'],
    )

    # --- 4. Semilla: sucursales demo (la empresa existe por CU16) -----------
    op.execute(
        """
        INSERT INTO sucursales (empresa_id, id_ciudad, nombre, direccion, telefono, horario_atencion, is_active)
        SELECT e.id,
               (SELECT id FROM ciudades WHERE nombre = 'Santa Cruz de la Sierra'),
               'Sucursal Central', 'Av. San Martín 1500, Zona Central',
               '+591 3 333-0001', 'Lun-Sáb 09:00-20:00', true
        FROM empresas e ORDER BY e.id LIMIT 1
        """
    )
    op.execute(
        """
        INSERT INTO sucursales (empresa_id, id_ciudad, nombre, direccion, telefono, horario_atencion, is_active)
        SELECT e.id,
               (SELECT id FROM ciudades WHERE nombre = 'Santa Cruz de la Sierra'),
               'Sucursal El Tesoro', 'Av. Cristóbal de Mendoza, El Tesoro',
               '+591 3 333-0002', 'Lun-Dom 10:00-21:00', true
        FROM empresas e ORDER BY e.id LIMIT 1
        """
    )
    op.execute(
        """
        INSERT INTO sucursales (empresa_id, id_ciudad, nombre, direccion, telefono, horario_atencion, is_active)
        SELECT e.id,
               (SELECT id FROM ciudades WHERE nombre = 'Santa Cruz de la Sierra'),
               'Sucursal Equipetrol', 'Tercer Anillo, Equipetrol Norte',
               '+591 3 333-0003', 'Lun-Dom 10:00-21:00', true
        FROM empresas e ORDER BY e.id LIMIT 1
        """
    )


def downgrade() -> None:
    """Downgrade schema."""
    # Devolver el nombre original de la PK y eliminar columnas/tabla nuevas.
    op.drop_constraint(op.f('fk_sucursales_id_ciudad_ciudades'), 'sucursales', type_='foreignkey')
    op.drop_index(op.f('ix_sucursales_id_ciudad'), table_name='sucursales')
    op.drop_column('sucursales', 'id_ciudad')
    op.drop_column('sucursales', 'horario_atencion')
    op.alter_column(
        'sucursales',
        'codigo_sucursal',
        new_column_name='id',
        existing_type=sa.Integer(),
        existing_nullable=False,
    )
    op.drop_index(op.f('ix_ciudades_nombre'), table_name='ciudades')
    op.drop_index(op.f('ix_ciudades_id'), table_name='ciudades')
    op.drop_table('ciudades')
