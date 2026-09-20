# BD local de pruebas (CU20)

Pruebas de integración contra un PostgreSQL **local** y exclusivo (`tienda_ropa_test`).
Nunca contra Supabase ni desarrollo/producción.

## Candados (`local_db.py`)
Solo se acepta una URL con host `localhost`/`127.0.0.1`/`::1`, nombre de BD terminado en
`_test` y sin la palabra `supabase`. Sin `TEST_DATABASE_URL` las suites de integración
se **omiten** (fail-closed: no caen a la BD del `.env`).

## Construir la BD (una vez, o con `--recreate` para reiniciar)
```powershell
$env:TEST_DATABASE_URL = "postgresql://postgres:CLAVE@localhost:5432/tienda_ropa_test"
python tests/support/setup_test_db.py --recreate
```
Hace: `CREATE DATABASE` → bootstrap de `roles`/`usuarios` (anteriores a Alembic) →
`alembic upgrade` (las 17 migraciones, sin modificar, por etapas) → seed determinista
(`seed_cu20.py`; su cabecera documenta todos los datos).

Por qué por etapas: las migraciones asumen "hechos previos" de la BD real que ellas no
crean (`roles`/`usuarios`, la empresa de CU16, el proveedor id=7 de CU6). Ver `ETAPAS` en
`setup_test_db.py`.

## Ejecutar
```powershell
$env:TEST_DATABASE_URL = "postgresql://postgres:CLAVE@localhost:5432/tienda_ropa_test"
python tests/integration/test_reportes_local_datos.py      # datos exactos del seed
python tests/integration/test_reportes_endpoints.py        # invariantes + SQL independiente
python tests/integration/test_reportes_authz.py            # JWT reales: 401/403/200/422
python tests/integration/test_reportes_rendimiento_vendedores.py
python tests/support/schema_diff.py                        # esquema local vs Supabase (solo lectura)
```
Modo Supabase de solo lectura (sin datos exactos; la suite `local_datos` se omite):
`$env:CU20_TEST_TARGET = "supabase-readonly"`.
