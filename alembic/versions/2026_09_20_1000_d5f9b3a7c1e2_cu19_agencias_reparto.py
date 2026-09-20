"""CU19 agencias de reparto

Revision ID: d5f9b3a7c1e2
Revises: c4e8a2f6b9d3
Create Date: 2026-09-20 10:00:00.000000

Migracion MANUAL (no autogenerada) del CU19 (Gestion de Agencias de Reparto):

1. `agencias_reparto`: catalogo GLOBAL de agencias externas (sin sucursal).
   NIT unico, razon social unica sin distinguir mayusculas (indice
   funcional lower(btrim(...))) y datos de facturacion obligatorios.
2. `agencia_zonas`: cobertura = ciudad (FK a `ciudades`) + subzona opcional.
   UNIQUE funcional: NULL y '' cuentan como "toda la ciudad".
3. `agencia_tarifas`: tarifa por PESO o VOLUMEN (un solo criterio), rango
   [rango_min, rango_max) con rango_max NULL = tramo abierto, costo y
   vigencia (vigente_hasta NULL = sin fin).
4. `envios`: columnas NULLABLE `id_agencia`, `id_tarifa_aplicada`,
   `costo_agencia`, `peso_kg`, `volumen_m3` + CHECKs. Los envios existentes
   (todos con estas columnas en NULL) siguen cumpliendo los CHECK.

Decisiones:
- Sin EXCLUDE/btree_gist ni extensiones nuevas: el solapamiento de rangos
  lo valida el service; en la DB solo hay CHECK/UNIQUE/indices comunes.
- `envios.id_agencia` e `id_tarifa_aplicada` sin ondelete (RESTRICT): no se
  puede borrar una agencia/tarifa con envios. `agencia_zonas` y
  `agencia_tarifas` cascadean desde su padre, pero el RESTRICT de envios
  impide arrastrar una tarifa ya usada.
- Exclusion mutua repartidor/agencia y snapshot completo (tarifa, costo,
  peso y volumen > 0) cuando hay agencia. `costo_agencia` es un costo
  INTERNO de logistica: no toca `ventas.costo_envio`.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd5f9b3a7c1e2'
down_revision: Union[str, Sequence[str], None] = 'c4e8a2f6b9d3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema: agencias, zonas, tarifas y columnas nullable en envios."""
    # --- 1. agencias_reparto ---------------------------------------------
    op.create_table(
        'agencias_reparto',
        sa.Column('id_agencia', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('razon_social', sa.String(length=150), nullable=False),
        sa.Column('nit', sa.String(length=20), nullable=False),
        sa.Column('contacto_operativo', sa.String(length=150), nullable=True),
        sa.Column('telefono', sa.String(length=30), nullable=True),
        sa.Column('correo', sa.String(length=150), nullable=True),
        sa.Column('direccion', sa.String(length=255), nullable=True),
        sa.Column('correo_facturacion', sa.String(length=150), nullable=False),
        sa.Column('direccion_fiscal', sa.String(length=255), nullable=False),
        sa.Column(
            'is_active', sa.Boolean(), server_default=sa.text('true'), nullable=False
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
        sa.PrimaryKeyConstraint('id_agencia', name=op.f('pk_agencias_reparto')),
        sa.CheckConstraint(
            'length(btrim(razon_social)) > 0',
            name=op.f('ck_agencias_reparto_razon_social_no_vacia'),
        ),
        sa.CheckConstraint(
            'length(btrim(nit)) > 0', name=op.f('ck_agencias_reparto_nit_no_vacio')
        ),
    )
    op.create_index(
        op.f('ix_agencias_reparto_id_agencia'),
        'agencias_reparto',
        ['id_agencia'],
        unique=False,
    )
    op.create_index(
        op.f('ix_agencias_reparto_nit'), 'agencias_reparto', ['nit'], unique=True
    )
    op.create_index(
        op.f('ix_agencias_reparto_is_active'),
        'agencias_reparto',
        ['is_active'],
        unique=False,
    )
    op.create_index(
        'uq_agencias_reparto_razon_social_lower',
        'agencias_reparto',
        [sa.text('lower(btrim(razon_social))')],
        unique=True,
    )

    # --- 2. agencia_zonas -------------------------------------------------
    op.create_table(
        'agencia_zonas',
        sa.Column('id_zona', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('id_agencia', sa.Integer(), nullable=False),
        sa.Column('id_ciudad', sa.Integer(), nullable=False),
        sa.Column('nombre_zona', sa.String(length=100), nullable=True),
        sa.Column(
            'fecha_creacion',
            sa.DateTime(timezone=True),
            server_default=sa.text('now()'),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint('id_zona', name=op.f('pk_agencia_zonas')),
        sa.ForeignKeyConstraint(
            ['id_agencia'],
            ['agencias_reparto.id_agencia'],
            name=op.f('fk_agencia_zonas_id_agencia_agencias_reparto'),
            ondelete='CASCADE',
        ),
        sa.ForeignKeyConstraint(
            ['id_ciudad'],
            ['ciudades.id'],
            name=op.f('fk_agencia_zonas_id_ciudad_ciudades'),
        ),
    )
    op.create_index(
        op.f('ix_agencia_zonas_id_zona'), 'agencia_zonas', ['id_zona'], unique=False
    )
    op.create_index(
        op.f('ix_agencia_zonas_id_agencia'),
        'agencia_zonas',
        ['id_agencia'],
        unique=False,
    )
    op.create_index(
        op.f('ix_agencia_zonas_id_ciudad'), 'agencia_zonas', ['id_ciudad'], unique=False
    )
    op.create_index(
        'uq_agencia_zonas_cobertura',
        'agencia_zonas',
        [
            'id_agencia',
            'id_ciudad',
            sa.text("lower(coalesce(btrim(nombre_zona), ''))"),
        ],
        unique=True,
    )

    # --- 3. agencia_tarifas -----------------------------------------------
    op.create_table(
        'agencia_tarifas',
        sa.Column('id_tarifa', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('id_zona', sa.Integer(), nullable=False),
        sa.Column('criterio', sa.String(length=10), nullable=False),
        sa.Column('rango_min', sa.Numeric(precision=10, scale=3), nullable=False),
        sa.Column('rango_max', sa.Numeric(precision=10, scale=3), nullable=True),
        sa.Column('costo', sa.Numeric(precision=10, scale=2), nullable=False),
        sa.Column('vigente_desde', sa.Date(), nullable=False),
        sa.Column('vigente_hasta', sa.Date(), nullable=True),
        sa.Column(
            'is_active', sa.Boolean(), server_default=sa.text('true'), nullable=False
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
        sa.PrimaryKeyConstraint('id_tarifa', name=op.f('pk_agencia_tarifas')),
        sa.ForeignKeyConstraint(
            ['id_zona'],
            ['agencia_zonas.id_zona'],
            name=op.f('fk_agencia_tarifas_id_zona_agencia_zonas'),
            ondelete='CASCADE',
        ),
        sa.CheckConstraint(
            "criterio IN ('PESO', 'VOLUMEN')",
            name=op.f('ck_agencia_tarifas_criterio_valido'),
        ),
        sa.CheckConstraint(
            'rango_min >= 0', name=op.f('ck_agencia_tarifas_rango_min_no_negativo')
        ),
        sa.CheckConstraint(
            'rango_max IS NULL OR rango_max > rango_min',
            name=op.f('ck_agencia_tarifas_rango_max_mayor_que_min'),
        ),
        sa.CheckConstraint(
            'costo >= 0', name=op.f('ck_agencia_tarifas_costo_no_negativo')
        ),
        sa.CheckConstraint(
            'vigente_hasta IS NULL OR vigente_hasta >= vigente_desde',
            name=op.f('ck_agencia_tarifas_vigencia_valida'),
        ),
    )
    op.create_index(
        op.f('ix_agencia_tarifas_id_tarifa'),
        'agencia_tarifas',
        ['id_tarifa'],
        unique=False,
    )
    op.create_index(
        op.f('ix_agencia_tarifas_id_zona'), 'agencia_tarifas', ['id_zona'], unique=False
    )

    # --- 4. envios: columnas NULLABLE (los envios existentes no cambian) ---
    op.add_column('envios', sa.Column('id_agencia', sa.Integer(), nullable=True))
    op.add_column('envios', sa.Column('id_tarifa_aplicada', sa.Integer(), nullable=True))
    op.add_column(
        'envios',
        sa.Column('costo_agencia', sa.Numeric(precision=10, scale=2), nullable=True),
    )
    op.add_column(
        'envios',
        sa.Column('peso_kg', sa.Numeric(precision=10, scale=3), nullable=True),
    )
    op.add_column(
        'envios',
        sa.Column('volumen_m3', sa.Numeric(precision=10, scale=3), nullable=True),
    )
    op.create_foreign_key(
        op.f('fk_envios_id_agencia_agencias_reparto'),
        'envios',
        'agencias_reparto',
        ['id_agencia'],
        ['id_agencia'],
    )
    op.create_foreign_key(
        op.f('fk_envios_id_tarifa_aplicada_agencia_tarifas'),
        'envios',
        'agencia_tarifas',
        ['id_tarifa_aplicada'],
        ['id_tarifa'],
    )
    op.create_index(op.f('ix_envios_id_agencia'), 'envios', ['id_agencia'], unique=False)
    op.create_check_constraint(
        op.f('ck_envios_repartidor_o_agencia'),
        'envios',
        'id_agencia IS NULL OR id_repartidor IS NULL',
    )
    op.create_check_constraint(
        op.f('ck_envios_agencia_snapshot_completo'),
        'envios',
        'id_agencia IS NULL OR (id_tarifa_aplicada IS NOT NULL '
        'AND costo_agencia IS NOT NULL AND peso_kg IS NOT NULL AND peso_kg > 0 '
        'AND volumen_m3 IS NOT NULL AND volumen_m3 > 0)',
    )
    op.create_check_constraint(
        op.f('ck_envios_datos_agencia_requieren_agencia'),
        'envios',
        'id_agencia IS NOT NULL OR (id_tarifa_aplicada IS NULL '
        'AND costo_agencia IS NULL AND peso_kg IS NULL AND volumen_m3 IS NULL)',
    )
    op.create_check_constraint(
        op.f('ck_envios_costo_agencia_no_negativo'),
        'envios',
        'costo_agencia IS NULL OR costo_agencia >= 0',
    )


def downgrade() -> None:
    """Downgrade schema: quita columnas de agencia de envios y las 3 tablas.

    ATENCION: borra el catalogo de agencias/zonas/tarifas y la informacion de
    agencia de los envios (los envios en si se conservan).
    """
    op.drop_constraint(op.f('ck_envios_costo_agencia_no_negativo'), 'envios', type_='check')
    op.drop_constraint(
        op.f('ck_envios_datos_agencia_requieren_agencia'), 'envios', type_='check'
    )
    op.drop_constraint(op.f('ck_envios_agencia_snapshot_completo'), 'envios', type_='check')
    op.drop_constraint(op.f('ck_envios_repartidor_o_agencia'), 'envios', type_='check')
    op.drop_index(op.f('ix_envios_id_agencia'), table_name='envios')
    op.drop_constraint(
        op.f('fk_envios_id_tarifa_aplicada_agencia_tarifas'), 'envios', type_='foreignkey'
    )
    op.drop_constraint(
        op.f('fk_envios_id_agencia_agencias_reparto'), 'envios', type_='foreignkey'
    )
    op.drop_column('envios', 'volumen_m3')
    op.drop_column('envios', 'peso_kg')
    op.drop_column('envios', 'costo_agencia')
    op.drop_column('envios', 'id_tarifa_aplicada')
    op.drop_column('envios', 'id_agencia')

    op.drop_index(op.f('ix_agencia_tarifas_id_zona'), table_name='agencia_tarifas')
    op.drop_index(op.f('ix_agencia_tarifas_id_tarifa'), table_name='agencia_tarifas')
    op.drop_table('agencia_tarifas')

    op.drop_index('uq_agencia_zonas_cobertura', table_name='agencia_zonas')
    op.drop_index(op.f('ix_agencia_zonas_id_ciudad'), table_name='agencia_zonas')
    op.drop_index(op.f('ix_agencia_zonas_id_agencia'), table_name='agencia_zonas')
    op.drop_index(op.f('ix_agencia_zonas_id_zona'), table_name='agencia_zonas')
    op.drop_table('agencia_zonas')

    op.drop_index('uq_agencias_reparto_razon_social_lower', table_name='agencias_reparto')
    op.drop_index(op.f('ix_agencias_reparto_is_active'), table_name='agencias_reparto')
    op.drop_index(op.f('ix_agencias_reparto_nit'), table_name='agencias_reparto')
    op.drop_index(op.f('ix_agencias_reparto_id_agencia'), table_name='agencias_reparto')
    op.drop_table('agencias_reparto')
