"""reserva_anticipo_50_expiracion_48h

Revision ID: c8b9d0e1f2a3
Revises: b7ade8af60d1
Create Date: 2026-09-21 21:30:00.000000

Agrega columnas para control del anticipo del 50%, expiración estricta de 48h
y reembolso parcial del 50% (con penalización del 50% por apartado de stock).
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c8b9d0e1f2a3'
down_revision: Union[str, Sequence[str], None] = 'b7ade8af60d1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('reservas', sa.Column('monto_anticipo', sa.Numeric(10, 2), nullable=False, server_default='0.00'))
    op.add_column('reservas', sa.Column('monto_anticipo_pagado', sa.Numeric(10, 2), nullable=False, server_default='0.00'))
    op.add_column('reservas', sa.Column('monto_reembolsado', sa.Numeric(10, 2), nullable=False, server_default='0.00'))
    op.add_column('reservas', sa.Column('monto_penalizacion', sa.Numeric(10, 2), nullable=False, server_default='0.00'))
    op.add_column('reservas', sa.Column('metodo_pago_anticipo', sa.String(length=30), nullable=True))
    op.add_column('reservas', sa.Column('codigo_transaccion_anticipo', sa.String(length=100), nullable=True))
    op.add_column('reservas', sa.Column('fecha_confirmacion', sa.DateTime(timezone=True), nullable=True))
    op.add_column('reservas', sa.Column('fecha_expiracion_dt', sa.DateTime(timezone=True), nullable=True))

    # Backfill para reservas existentes
    op.execute("UPDATE reservas SET monto_anticipo = ROUND(total_estimado * 0.5, 2) WHERE monto_anticipo = 0")
    op.execute("UPDATE reservas SET monto_anticipo_pagado = monto_anticipo WHERE estado IN ('CONFIRMADA', 'COMPLETADA') AND monto_anticipo_pagado = 0")
    op.execute("UPDATE reservas SET fecha_expiracion_dt = fecha_reserva + INTERVAL '48 hours' WHERE fecha_expiracion_dt IS NULL")


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('reservas', 'fecha_expiracion_dt')
    op.drop_column('reservas', 'fecha_confirmacion')
    op.drop_column('reservas', 'codigo_transaccion_anticipo')
    op.drop_column('reservas', 'metodo_pago_anticipo')
    op.drop_column('reservas', 'monto_penalizacion')
    op.drop_column('reservas', 'monto_reembolsado')
    op.drop_column('reservas', 'monto_anticipo_pagado')
    op.drop_column('reservas', 'monto_anticipo')
