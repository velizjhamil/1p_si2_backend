#!/usr/bin/env python3
"""
Script de verificación de configuración del backend.
Ejecutar antes de hacer deploy para asegurarse de que todo está correcto.

Uso:
    python verify_config.py
"""
import os
import sys
from pathlib import Path

# Agregar el directorio raíz al path
sys.path.insert(0, str(Path(__file__).resolve().parent))

def print_header(text: str):
    """Imprime un header bonito."""
    print("\n" + "=" * 70)
    print(f"  {text}")
    print("=" * 70)

def print_check(ok: bool, message: str, details: str = ""):
    """Imprime un check con emoji."""
    emoji = "✅" if ok else "❌"
    print(f"{emoji} {message}")
    if details:
        print(f"   → {details}")

def main():
    print_header("🔍 Verificación de Configuración del Backend")
    
    # Importar settings
    try:
        from app.core.config import get_settings
        settings = get_settings()
        print_check(True, "Settings cargados correctamente")
    except Exception as e:
        print_check(False, "Error al cargar settings", str(e))
        return 1
    
    # Verificar DATABASE_URL
    print_header("🔑 Base de Datos")
    db_url = settings.database_url
    if db_url:
        # No imprimir la URL completa por seguridad
        if "supabase" in db_url:
            print_check(True, "DATABASE_URL configurada (Supabase)", 
                       f"Host: {settings.DB_HOST}")
        elif "localhost" in db_url:
            print_check(True, "DATABASE_URL configurada (Local)", 
                       "⚠️  Estás usando base de datos local")
        else:
            print_check(True, "DATABASE_URL configurada", 
                       "Host: " + db_url.split("@")[1].split("/")[0] if "@" in db_url else "N/A")
    else:
        print_check(False, "DATABASE_URL no configurada")
    
    # Verificar SECRET_KEY
    print_header("🔐 Seguridad JWT")
    secret_key = settings.SECRET_KEY
    
    if not secret_key:
        print_check(False, "SECRET_KEY no configurada")
    elif secret_key == "clave_super_secreta_atention_CAMBIAR_EN_PRODUCCION":
        print_check(False, "SECRET_KEY usando valor por defecto", 
                   "⚠️  PELIGRO: Configura SECRET_KEY en variables de entorno")
    elif len(secret_key) < 32:
        print_check(False, "SECRET_KEY muy corta", 
                   f"⚠️  Longitud actual: {len(secret_key)}, recomendado: 32+")
    else:
        print_check(True, "SECRET_KEY configurada correctamente", 
                   f"Longitud: {len(secret_key)} caracteres")
    
    print_check(True, f"ALGORITHM: {settings.ALGORITHM}")
    print_check(True, f"TOKEN_EXPIRE: {settings.ACCESS_TOKEN_EXPIRE_MINUTES} minutos")
    
    # Verificar CORS
    print_header("🌐 CORS Origins")
    cors_origins = settings.CORS_ORIGINS
    
    if isinstance(cors_origins, str):
        print_check(False, "CORS_ORIGINS es string (debería ser lista)", 
                   f"Valor: {cors_origins}")
        cors_list = [cors_origins]
    elif isinstance(cors_origins, list):
        cors_list = cors_origins
        print_check(True, f"CORS_ORIGINS configurado ({len(cors_list)} orígenes)")
    else:
        print_check(False, f"CORS_ORIGINS tipo inválido: {type(cors_origins)}")
        cors_list = []
    
    # Verificar orígenes específicos
    has_localhost = any("localhost" in origin or "127.0.0.1" in origin for origin in cors_list)
    has_vercel = any("vercel.app" in origin for origin in cors_list)
    has_production = any("1p-si2-frontend.vercel.app" in origin for origin in cors_list)
    
    print_check(has_localhost, "Localhost incluido (desarrollo)", 
               "Necesario para: http://localhost:4200")
    print_check(has_vercel, "Vercel incluido (producción)", 
               "Necesario para: https://1p-si2-frontend.vercel.app")
    
    if not has_production:
        print_check(False, "URL de producción específica no encontrada",
                   "⚠️  Agrega: https://1p-si2-frontend.vercel.app")
    
    print("\n   Orígenes configurados:")
    for origin in cors_list:
        print(f"   • {origin}")
    
    # Test de conexión a BD (opcional)
    print_header("🔌 Test de Conexión a Base de Datos")
    try:
        from sqlalchemy import create_engine, text
        from app.core.database import SQLALCHEMY_DATABASE_URL
        
        # Crear engine temporal para test
        test_engine = create_engine(SQLALCHEMY_DATABASE_URL, pool_pre_ping=True)
        with test_engine.connect() as conn:
            result = conn.execute(text("SELECT 1")).scalar()
            if result == 1:
                print_check(True, "Conexión a base de datos exitosa")
            else:
                print_check(False, "Query de test falló")
        test_engine.dispose()
    except Exception as e:
        print_check(False, "No se pudo conectar a la base de datos", str(e))
        print("   ℹ️  Esto es normal si Supabase no está accesible desde aquí")
    
    # Resumen final
    print_header("📋 Resumen")
    
    warnings = []
    errors = []
    
    if secret_key == "clave_super_secreta_atention_CAMBIAR_EN_PRODUCCION":
        errors.append("SECRET_KEY usando valor por defecto inseguro")
    
    if not has_production:
        warnings.append("URL de producción de Vercel no configurada en CORS")
    
    if not has_localhost:
        warnings.append("Localhost no configurado (desarrollo afectado)")
    
    if errors:
        print("\n❌ ERRORES CRÍTICOS:")
        for error in errors:
            print(f"   • {error}")
        print("\n⚠️  NO HACER DEPLOY hasta resolver estos errores")
        return 1
    
    if warnings:
        print("\n⚠️  ADVERTENCIAS:")
        for warning in warnings:
            print(f"   • {warning}")
        print("\n✅ Puedes hacer deploy, pero verifica las advertencias")
    else:
        print("\n✅ Configuración OK - Listo para deploy")
    
    print("\n" + "=" * 70)
    print("💡 Recuerda configurar las mismas variables en Render:")
    print("   • DATABASE_URL")
    print("   • SECRET_KEY")
    print("   • CORS_ORIGINS")
    print("=" * 70 + "\n")
    
    return 0

if __name__ == "__main__":
    sys.exit(main())
