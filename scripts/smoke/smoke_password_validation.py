# backend/scripts/smoke/smoke_password_validation.py
import sys
from pydantic import ValidationError
from app.schemas.usuario import UsuarioCreate, UsuarioUpdate
from app.schemas.auth import LoginRequest

def test_password_validation():
    print("Iniciando verificación de validación de contraseñas...")

    # 1. Casos que DEBEN FALLAR en UsuarioCreate
    casos_invalidos = [
        ("123456", "Muy corta (< 8)"),
        ("holamundo123!", "Sin mayúscula"),
        ("HOLAMUNDO123!", "Sin minúscula"),
        ("HolaMundoAmigo!", "Sin número"),
        ("HolaMundo12345", "Sin carácter especial"),
        ("corta1!", "Menor a 8 caracteres"),
    ]

    for pwd, motivo in casos_invalidos:
        try:
            UsuarioCreate(
                nombre="Test",
                apellido="User",
                correo="test@example.com",
                password=pwd,
                nombre_rol="C",
            )
            print(f"[ERROR]: Debio fallar para '{pwd}' ({motivo}) pero paso.")
            sys.exit(1)
        except ValidationError:
            print(f"[OK]: Fallo como se esperaba para '{pwd}' ({motivo}).")

    # 2. Casos que DEBEN PASAR en UsuarioCreate
    casos_validos = [
        "Segura.2026",
        "Atencion#123",
        "P@ssword1",
        "MiClave_99!",
        "V@lida12345",
    ]

    for pwd in casos_validos:
        try:
            u = UsuarioCreate(
                nombre="Test",
                apellido="User",
                correo="test@example.com",
                password=pwd,
                nombre_rol="C",
            )
            assert u.password == pwd
            print(f"[OK]: Acepto contrasena valida '{pwd}'.")
        except ValidationError as e:
            print(f"[ERROR]: Debio aceptar '{pwd}' pero dio error: {e}")
            sys.exit(1)

    # 3. Retrocompatibilidad en LoginRequest (debe aceptar claves simples o cortas existentes)
    casos_login_legado = [
        "123456",
        "admin",
        "clave",
        "1234",
    ]
    for pwd in casos_login_legado:
        try:
            l = LoginRequest(correo="legacy@example.com", password=pwd)
            assert l.password == pwd
            print(f"[OK]: LoginRequest acepta contrasena historica '{pwd}'.")
        except ValidationError as e:
            print(f"[ERROR]: LoginRequest rechazo clave historica '{pwd}': {e}")
            sys.exit(1)

    print("\nTODAS LAS VALIDACIONES DE CONTRASENA Y RETROCOMPATIBILIDAD PASARON EXITOSAMENTE.")

if __name__ == "__main__":
    test_password_validation()
