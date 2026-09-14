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
