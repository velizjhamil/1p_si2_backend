# Smoke Tests

Pruebas funcionales de los casos de uso principales. Usan `TestClient` de FastAPI
— **no requieren que el servidor esté levantado**.

## Ejecución

Siempre desde la **raíz del backend** (`1p_si2_backend/`):

```bash
# Activar el venv primero
.venv\Scripts\activate   # Windows
source .venv/bin/activate  # Linux/macOS

# Ejecutar individualmente
python scripts/smoke/smoke_cu8.py       # CU8  — Probador Virtual
python scripts/smoke/smoke_cu14.py      # CU14 — Reservas
python scripts/smoke/smoke_cu22.py      # CU22 — Inventario / Kardex
python scripts/smoke/smoke_cu1521.py    # CU15+CU21 — Checkout / Ventas
python scripts/smoke/smoke_cu18.py      # CU18 — Gestión de Envío (auto-limpieza, ver abajo)
```

## Requisitos

- Base de datos `tienda_ropa` corriendo con el seed de datos de prueba aplicado.
- Variables de entorno configuradas en `.env` (raíz del backend).
- Dependencias instaladas: `pip install -r requirements.txt`.

## Salida esperada

```
  [PASS] login Cliente
  [PASS] POST subir-foto 201
  ...
RESULTADO: N PASS / 0 FAIL
```

Un `FAIL` indica una regresión en el endpoint correspondiente.

## CU18 — Gestión de Envío

- `smoke_cu18.py` (integración, usa la DB configurada): crea usuarios temporales
  `smoke18_*@attention-smoke.com`, ventas, envíos, notificaciones y kardex, y **los
  borra en un `finally`**; compara los conteos con la línea base y restaura el
  stock. Aborta si detecta usuarios `smoke18_*` residuales.
- `tests/unit/test_delivery_service.py` (sin DB, solo mocks): `python tests/unit/test_delivery_service.py`.
  Cubre la validación de sucursal inactiva/inexistente, repartidor inactivo o
  que no es D, la máquina de estados y el tratamiento de fechas UTC.

### Decisiones de alcance de CU18

- **Sucursal:** el envío conserva su sucursal responsable y se filtra por ella, pero
  el sistema *actualmente no restringe automáticamente los registros visibles según la
  sucursal del Gerente de Sucursal porque `Usuario` no está asociado a una sucursal.*
  Es una mejora/requisito transversal futuro (ventas, envíos, inventario; requiere CU3).
- **Cancelación:** `CANCELADO` finaliza solo el flujo logístico. No cancela la venta,
  no repone stock, no genera devolución ni reembolso (responsabilidad de CU13/CU22).
- **Fechas:** el backend normaliza a UTC (sin zona horaria = UTC); el frontend envía
  ISO 8601 con offset.

### Pendiente / no ejecutado

- Integración con **sucursal inactiva**: la validación existe y está cubierta por la
  prueba unitaria aislada; falta la prueba de integración (no hay sucursales inactivas
  y no se crean datos artificiales en Supabase para esto).
- **`smoke_cu1521.py` y `smoke_cu11_pos.py` NO se ejecutaron contra Supabase:** su
  limpieza usa filtros que coinciden con datos reales (`DELETE FROM ventas WHERE correo =
  'cliente@attention.com'` y `DELETE FROM movimientos_inventario WHERE motivo LIKE
  '%checkout online%'` / `'Venta POS %'`). Al 2026-09-18 eso borraría 6 de las 7 ventas
  y 6 de los 8 movimientos de kardex reales. Además inician sesión con contraseñas demo
  fijas (un fallo incrementa `intentos_fallidos` de cuentas reales). Ejecutarlos solo
  contra una base de desarrollo desechable, o reescribir su limpieza por ids creados.
