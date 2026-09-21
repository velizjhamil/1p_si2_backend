"""Multi-sucursal con aislamiento operativo

Revision ID: e6a1b2c3d4f5
Revises: d5f9b3a7c1e2
Create Date: 2026-09-20 21:00:00.000000

Migración para Multi-sucursal con aislamiento operativo estricto:
1. `sucursales.id_gerente`: FK 1 a 1 exclusiva a `usuarios.id_usuario` para Gerente Titular (GS).
2. `usuarios.id_sucursal`: FK a `sucursales.codigo_sucursal` vinculando personal GS y V.
3. `inventario_sucursal`: Nueva tabla de stock físico por sucursal y producto.
4. `movimientos_inventario.id_sucursal`: FK a `sucursales.codigo_sucursal` en el kardex.
5. `ventas.id_sucursal`: FK a `sucursales.codigo_sucursal`.
6. `reservas.id_sucursal`: FK a `sucursales.codigo_sucursal`.
7. `devoluciones.id_sucursal`: FK a `sucursales.codigo_sucursal`.
8. Backfill de datos iniciales para mantener consistencia.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = 'e6a1b2c3d4f5'
down_revision: Union[str, Sequence[str], None] = 'd5f9b3a7c1e2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # --- 1. sucursales.id_gerente (1 a 1) --------------------------------
    op.add_column(
        'sucursales',
        sa.Column('id_gerente', postgresql.UUID(as_uuid=True), nullable=True)
    )
    op.create_unique_constraint(
        'uq_sucursales_id_gerente',
        'sucursales',
        ['id_gerente']
    )
    op.create_foreign_key(
        'fk_sucursales_id_gerente_usuarios',
        'sucursales',
        'usuarios',
        ['id_gerente'],
        ['id_usuario'],
        ondelete='SET NULL'
    )
    op.create_index(
        'ix_sucursales_id_gerente',
        'sucursales',
        ['id_gerente']
    )

    # --- 2. usuarios.id_sucursal -----------------------------------------
    op.add_column(
        'usuarios',
        sa.Column('id_sucursal', sa.Integer(), nullable=True)
    )
    op.create_foreign_key(
        'fk_usuarios_id_sucursal_sucursales',
        'usuarios',
        'sucursales',
        ['id_sucursal'],
        ['codigo_sucursal'],
        ondelete='SET NULL'
    )
    op.create_index(
        'ix_usuarios_id_sucursal',
        'usuarios',
        ['id_sucursal']
    )

    # --- 3. inventario_sucursal ------------------------------------------
    op.create_table(
        'inventario_sucursal',
        sa.Column('id_inventario', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('id_sucursal', sa.Integer(), nullable=False),
        sa.Column('id_producto', sa.Integer(), nullable=False),
        sa.Column('stock', sa.Integer(), server_default=sa.text('0'), nullable=False),
        sa.Column('stock_minimo', sa.Integer(), server_default=sa.text('5'), nullable=False),
        sa.Column('fecha_actualizacion', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint('id_inventario', name='pk_inventario_sucursal'),
        sa.ForeignKeyConstraint(
            ['id_sucursal'],
            ['sucursales.codigo_sucursal'],
            name='fk_inventario_sucursal_id_sucursal',
            ondelete='CASCADE'
        ),
        sa.ForeignKeyConstraint(
            ['id_producto'],
            ['productos.id_producto'],
            name='fk_inventario_sucursal_id_producto',
            ondelete='CASCADE'
        ),
        sa.UniqueConstraint('id_sucursal', 'id_producto', name='uq_inventario_sucursal_producto'),
        sa.CheckConstraint('stock >= 0', name='stock_sucursal_no_negativo'),
        sa.CheckConstraint('stock_minimo >= 0', name='stock_minimo_no_negativo'),
    )
    op.create_index(
        'ix_inventario_sucursal_id_inventario',
        'inventario_sucursal',
        ['id_inventario']
    )
    op.create_index(
        'ix_inventario_sucursal_id_sucursal',
        'inventario_sucursal',
        ['id_sucursal']
    )
    op.create_index(
        'ix_inventario_sucursal_id_producto',
        'inventario_sucursal',
        ['id_producto']
    )

    # --- 4. movimientos_inventario.id_sucursal ---------------------------
    op.add_column(
        'movimientos_inventario',
        sa.Column('id_sucursal', sa.Integer(), nullable=True)
    )
    op.create_foreign_key(
        'fk_movimientos_inventario_id_sucursal',
        'movimientos_inventario',
        'sucursales',
        ['id_sucursal'],
        ['codigo_sucursal'],
        ondelete='SET NULL'
    )
    op.create_index(
        'ix_movimientos_inventario_id_sucursal',
        'movimientos_inventario',
        ['id_sucursal']
    )

    # --- 5. ventas.id_sucursal -------------------------------------------
    op.add_column(
        'ventas',
        sa.Column('id_sucursal', sa.Integer(), nullable=True)
    )
    op.create_foreign_key(
        'fk_ventas_id_sucursal_sucursales',
        'ventas',
        'sucursales',
        ['id_sucursal'],
        ['codigo_sucursal'],
        ondelete='SET NULL'
    )
    op.create_index(
        'ix_ventas_id_sucursal',
        'ventas',
        ['id_sucursal']
    )

    # --- 6. reservas.id_sucursal -----------------------------------------
    op.add_column(
        'reservas',
        sa.Column('id_sucursal', sa.Integer(), nullable=True)
    )
    op.create_foreign_key(
        'fk_reservas_id_sucursal_sucursales',
        'reservas',
        'sucursales',
        ['id_sucursal'],
        ['codigo_sucursal'],
        ondelete='SET NULL'
    )
    op.create_index(
        'ix_reservas_id_sucursal',
        'reservas',
        ['id_sucursal']
    )

    # --- 7. devoluciones.id_sucursal -------------------------------------
    op.add_column(
        'devoluciones',
        sa.Column('id_sucursal', sa.Integer(), nullable=True)
    )
    op.create_foreign_key(
        'fk_devoluciones_id_sucursal_sucursales',
        'devoluciones',
        'sucursales',
        ['id_sucursal'],
        ['codigo_sucursal'],
        ondelete='SET NULL'
    )
    op.create_index(
        'ix_devoluciones_id_sucursal',
        'devoluciones',
        ['id_sucursal']
    )

    # --- 8. Backfill e inicialización consistente de datos ----------------
    # Población inicial de inventario por sucursal
    op.execute(
        """
        INSERT INTO inventario_sucursal (id_sucursal, id_producto, stock, stock_minimo, fecha_actualizacion)
        SELECT s.codigo_sucursal, p.id_producto, COALESCE(p.stock_total, 0), 5, NOW()
        FROM sucursales s
        CROSS JOIN productos p
        ON CONFLICT (id_sucursal, id_producto) DO NOTHING;
        """
    )
    # Asociar personal existente GS y V a la primera sucursal si id_sucursal está vacío
    op.execute(
        """
        UPDATE usuarios
        SET id_sucursal = (SELECT codigo_sucursal FROM sucursales ORDER BY codigo_sucursal LIMIT 1)
        WHERE id_sucursal IS NULL
          AND id_rol IN (SELECT id_rol FROM roles WHERE UPPER(nombre_rol) IN ('GS', 'V'));
        """
    )
    # Asignar gerente titular a la primera sucursal si hay un usuario GS
    op.execute(
        """
        UPDATE sucursales s
        SET id_gerente = sub.id_usuario
        FROM (
            SELECT u.id_usuario, u.id_sucursal
            FROM usuarios u
            JOIN roles r ON u.id_rol = r.id_rol
            WHERE UPPER(r.nombre_rol) = 'GS'
            ORDER BY u.fecha_creacion ASC
            LIMIT 1
        ) sub
        WHERE s.codigo_sucursal = sub.id_sucursal
          AND s.id_gerente IS NULL;
        """
    )
    # Backfill para ventas, reservas, devoluciones y kardex previos
    op.execute(
        """
        UPDATE ventas
        SET id_sucursal = (SELECT codigo_sucursal FROM sucursales ORDER BY codigo_sucursal LIMIT 1)
        WHERE id_sucursal IS NULL;
        """
    )
    op.execute(
        """
        UPDATE reservas
        SET id_sucursal = (SELECT codigo_sucursal FROM sucursales ORDER BY codigo_sucursal LIMIT 1)
        WHERE id_sucursal IS NULL;
        """
    )
    op.execute(
        """
        UPDATE devoluciones
        SET id_sucursal = (SELECT codigo_sucursal FROM sucursales ORDER BY codigo_sucursal LIMIT 1)
        WHERE id_sucursal IS NULL;
        """
    )
    op.execute(
        """
        UPDATE movimientos_inventario
        SET id_sucursal = (SELECT codigo_sucursal FROM sucursales ORDER BY codigo_sucursal LIMIT 1)
        WHERE id_sucursal IS NULL;
        """
    )


def downgrade() -> None:
    op.drop_index('ix_devoluciones_id_sucursal', table_name='devoluciones')
    op.drop_constraint('fk_devoluciones_id_sucursal_sucursales', 'devoluciones', type_='foreignkey')
    op.drop_column('devoluciones', 'id_sucursal')

    op.drop_index('ix_reservas_id_sucursal', table_name='reservas')
    op.drop_constraint('fk_reservas_id_sucursal_sucursales', 'reservas', type_='foreignkey')
    op.drop_column('reservas', 'id_sucursal')

    op.drop_index('ix_ventas_id_sucursal', table_name='ventas')
    op.drop_constraint('fk_ventas_id_sucursal_sucursales', 'ventas', type_='foreignkey')
    op.drop_column('ventas', 'id_sucursal')

    op.drop_index('ix_movimientos_inventario_id_sucursal', table_name='movimientos_inventario')
    op.drop_constraint('fk_movimientos_inventario_id_sucursal', 'movimientos_inventario', type_='foreignkey')
    op.drop_column('movimientos_inventario', 'id_sucursal')

    op.drop_index('ix_inventario_sucursal_id_producto', table_name='inventario_sucursal')
    op.drop_index('ix_inventario_sucursal_id_sucursal', table_name='inventario_sucursal')
    op.drop_index('ix_inventario_sucursal_id_inventario', table_name='inventario_sucursal')
    op.drop_table('inventario_sucursal')

    op.drop_index('ix_usuarios_id_sucursal', table_name='usuarios')
    op.drop_constraint('fk_usuarios_id_sucursal_sucursales', 'usuarios', type_='foreignkey')
    op.drop_column('usuarios', 'id_sucursal')

    op.drop_index('ix_sucursales_id_gerente', table_name='sucursales')
    op.drop_constraint('fk_sucursales_id_gerente_usuarios', 'sucursales', type_='foreignkey')
    op.drop_constraint('uq_sucursales_id_gerente', 'sucursales', type_='unique')
    op.drop_column('sucursales', 'id_gerente')
