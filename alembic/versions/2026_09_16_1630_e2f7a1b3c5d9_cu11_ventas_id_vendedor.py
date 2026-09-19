"""CU11 ventas: id_vendedor para registrar ventas POS del Vendedor.

Revision ID: e2f7a1b3c5d9
Revises: e1b7c5d0a4f8
Create Date: 2026-09-16 16:30:00.000000

Migración MANUAL (no autogenerada): agrega la columna `id_vendedor` a
la tabla `ventas` para soportar el flujo POS (CU11) donde un Vendedor
(o GS/ASU) registra una venta a nombre de un cliente seleccionado.

Decisiones:
- La columna es NULLABLE a propósito: las ventas online del Cliente
  (CU15+CU21) no tienen vendedor asociado. NULL == venta digital.
- FK a usuarios.id_usuario con ON DELETE SET NULL para no romper
  ventas históricas si se elimina un usuario vendedor.
- Índice ix_ventas_id_vendedor para que el listado del Vendedor
  (filtro WHERE id_vendedor = ?) sea O(log n).
- Sin seeds: el POS los crea en runtime vía POST /api/v1/ventas/checkout.

Alcance incluido:
- Columna id_vendedor.
- Índice.
- FK constraint.

Alcance NO incluido (queda para iteraciones futuras):
- Anulación / devolución de ventas (no hay estado de negocio todavía).
- Multi-sucursal (id_sucursal en ventas).
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID


# revision identifiers, used by Alembic.
revision: str = 'e2f7a1b3c5d9'
down_revision: Union[str, Sequence[str], None] = 'e1b7c5d0a4f8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema: agrega id_vendedor a ventas."""
    op.add_column(
        'ventas',
        sa.Column('id_vendedor', UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        'fk_ventas_id_vendedor_usuarios',
        'ventas',
        'usuarios',
        ['id_vendedor'],
        ['id_usuario'],
        ondelete='SET NULL',
    )
    op.create_index(
        op.f('ix_ventas_id_vendedor'),
        'ventas',
        ['id_vendedor'],
        unique=False,
    )


def downgrade() -> None:
    """Downgrade schema: revierte id_vendedor."""
    op.drop_index(op.f('ix_ventas_id_vendedor'), table_name='ventas')
    op.drop_constraint(
        'fk_ventas_id_vendedor_usuarios', 'ventas', type_='foreignkey'
    )
    op.drop_column('ventas', 'id_vendedor')
