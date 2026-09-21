"""crear tabla transacciones_pago

Revision ID: f7b2c3d4e5a6
Revises: e6a1b2c3d4f5
Create Date: 2026-09-20 23:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "f7b2c3d4e5a6"
down_revision: Union[str, None] = "e6a1b2c3d4f5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "transacciones_pago",
        sa.Column("id_transaccion", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("id_venta", sa.Integer(), nullable=False),
        sa.Column("pasarela", sa.String(length=50), nullable=False, server_default="AttentionPay"),
        sa.Column("codigo_transaccion", sa.String(length=100), nullable=False),
        sa.Column("monto", sa.Numeric(precision=10, scale=2), nullable=False),
        sa.Column("moneda", sa.String(length=10), nullable=False, server_default="BOB"),
        sa.Column("metodo_pago", sa.String(length=20), nullable=False),
        sa.Column("estado", sa.String(length=20), nullable=False, server_default="PENDIENTE"),
        sa.Column("detalles_pago", sa.String(length=1000), nullable=True),
        sa.Column("qr_data", sa.String(length=1000), nullable=True),
        sa.Column("signature", sa.String(length=255), nullable=True),
        sa.Column(
            "fecha_creacion",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "fecha_actualizacion",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "metodo_pago IN ('QR', 'EFECTIVO', 'TARJETA')",
            name="metodo_pago_transaccion_valido",
        ),
        sa.CheckConstraint(
            "estado IN ('PENDIENTE', 'PAGADO', 'RECHAZADO')",
            name="estado_transaccion_valido",
        ),
        sa.CheckConstraint("monto >= 0", name="monto_transaccion_no_negativo"),
        sa.ForeignKeyConstraint(
            ["id_venta"],
            ["ventas.id_venta"],
            name="fk_transacciones_pago_id_venta",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id_transaccion", name="pk_transacciones_pago"),
    )
    op.create_index("ix_transacciones_pago_id_transaccion", "transacciones_pago", ["id_transaccion"])
    op.create_index("ix_transacciones_pago_id_venta", "transacciones_pago", ["id_venta"])
    op.create_index(
        "ix_transacciones_pago_codigo_transaccion",
        "transacciones_pago",
        ["codigo_transaccion"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("ix_transacciones_pago_codigo_transaccion", table_name="transacciones_pago")
    op.drop_index("ix_transacciones_pago_id_venta", table_name="transacciones_pago")
    op.drop_index("ix_transacciones_pago_id_transaccion", table_name="transacciones_pago")
    op.drop_table("transacciones_pago")
