# Configuracion y CANDADOS de la BD LOCAL de pruebas (CU20 fase 5).
#
# Objetivo: que las pruebas de integracion NUNCA toquen Supabase ni una BD de
# desarrollo/produccion. La BD de pruebas es un PostgreSQL local cuyo nombre
# termina en `_test` (por defecto `tienda_ropa_test`).
#
# Uso: importar y llamar a `configure()` ANTES de importar `app.*`
# (app/core/database.py lee DATABASE_URL al importarse):
#
#     from tests.support import local_db
#     local_db.configure()
#     import app.main
#
# Variables de entorno:
#   TEST_DATABASE_URL   postgresql://USUARIO:CLAVE@localhost:5432/tienda_ropa_test
#                       (la clave NUNCA se guarda en el repo)
#   CU20_TEST_TARGET    'supabase-readonly' -> las suites data-independientes
#                       usan la BD del .env (ya son READ ONLY). Las suites con
#                       datos sembrados jamas corren en ese modo.
#
# Fail-closed: si TEST_DATABASE_URL falta o no pasa los candados, se apunta a
# una URL inservible: las pruebas se omiten, pero NO se conectan a Supabase.
import os
import sys
from urllib.parse import urlparse

TEST_DB_NAME_DEFAULT = "tienda_ropa_test"
HOSTS_PERMITIDOS = {"localhost", "127.0.0.1", "::1"}
# URL inservible (puerto 1 cerrado): conectar falla al instante.
_URL_INSERVIBLE = "postgresql://sin_bd_de_pruebas:x@127.0.0.1:1/sin_bd_de_pruebas_test"

MODO_SUPABASE_RO = "supabase-readonly"


class BDPruebasInsegura(RuntimeError):
    """La URL no cumple los candados de la BD de pruebas."""


def validar_url_de_pruebas(url: str) -> str:
    """Devuelve la URL si es una BD local de pruebas; si no, lanza error."""
    if "supabase" in url.lower():
        raise BDPruebasInsegura("La URL de pruebas apunta a Supabase.")
    p = urlparse(url)
    if p.scheme not in ("postgresql", "postgresql+psycopg2"):
        raise BDPruebasInsegura(f"Esquema no soportado: {p.scheme!r}.")
    if (p.hostname or "") not in HOSTS_PERMITIDOS:
        raise BDPruebasInsegura(
            f"Host {p.hostname!r} no permitido: solo {sorted(HOSTS_PERMITIDOS)}."
        )
    nombre = (p.path or "/").lstrip("/")
    if not nombre.endswith("_test"):
        raise BDPruebasInsegura(f"La BD {nombre!r} no termina en '_test'.")
    return url


def modo_supabase_readonly() -> bool:
    return os.environ.get("CU20_TEST_TARGET") == MODO_SUPABASE_RO


def url_de_pruebas() -> str | None:
    """URL validada de TEST_DATABASE_URL, o None si no esta definida."""
    url = os.environ.get("TEST_DATABASE_URL")
    return validar_url_de_pruebas(url) if url else None


def configure() -> str | None:
    """Fija DATABASE_URL para que `app.*` use la BD local de pruebas.

    Retorna la URL usada (None en modo supabase-readonly). Debe llamarse antes
    de importar `app`; si `app` ya estaba importado con otra URL, aborta.
    """
    if modo_supabase_readonly():
        return None
    try:
        url = url_de_pruebas()
    except BDPruebasInsegura as exc:
        print(f"[local_db] {exc} -> pruebas de integracion omitidas.", file=sys.stderr)
        url = None
    if url is None:
        if "TEST_DATABASE_URL" not in os.environ:
            print(
                "[local_db] TEST_DATABASE_URL no definida -> pruebas de "
                "integracion omitidas (no se usa Supabase).",
                file=sys.stderr,
            )
        url = _URL_INSERVIBLE
    os.environ["DATABASE_URL"] = url

    if "app.core.database" in sys.modules:  # ya importado: verificar coherencia
        actual = str(sys.modules["app.core.database"].engine.url.render_as_string(hide_password=False))
        if actual != url:
            raise BDPruebasInsegura(
                "app.core.database ya se importo con otra URL; importe "
                "tests.support.local_db y llame configure() antes que `app`."
            )
    return None if url == _URL_INSERVIBLE else url


def es_bd_de_pruebas_local() -> bool:
    """True si el engine de la app apunta a la BD local `_test` (no Supabase)."""
    from app.core.database import engine

    u = engine.url
    return (
        (u.host or "") in HOSTS_PERMITIDOS
        and (u.database or "").endswith("_test")
        and "supabase" not in str(u.host).lower()
    )
