# backend/scripts/smoke/smoke_ia_prompt.py
import os
import sys

from app.core.database import SessionLocal
from app.services.ia_service import IAService
from app.schemas.ia import ChatRequest

def test_ia_prompt_and_grounding():
    print("Iniciando prueba de IA Grounding y System Prompt unificado...")
    db = SessionLocal()

    try:
        # 1. Probar con usuario Hombre
        req_hombre = ChatRequest(
            mensaje="Recomiéndame ropa para salir el fin de semana",
            genero_usuario="Hombre",
            nombre_usuario="Carlos",
            id_sucursal=1,
            nombre_sucursal="Sucursal Equipetrol",
        )

        contexto_h = IAService._recuperar_contexto_db(
            db,
            consulta=req_hombre.mensaje,
            id_sucursal=req_hombre.id_sucursal,
            genero_usuario=req_hombre.genero_usuario,
            nombre_sucursal=req_hombre.nombre_sucursal,
        )

        prompt_h = IAService._construir_system_instruction(
            contexto_h,
            nombre_usuario="Carlos",
            genero_usuario="Hombre",
            nombre_sucursal="Sucursal Equipetrol",
        )

        # Verificaciones en el prompt
        assert "Conoce al usuario: Nombre: Carlos, Género: Hombre, Sucursal: Sucursal Equipetrol." in prompt_h, "Falta cabecera de usuario en prompt"
        assert "El género del usuario es innegociable:" in prompt_h, "Falta directiva estricta de género"
        assert "Sucursal Equipetrol" in prompt_h, "Falta nombre de sucursal en prompt"

        # Verificar que no haya categorías exclusivas de Mujer en las categorías enviadas a Gemini
        for cat_line in contexto_h["categorias"]:
            assert "Línea: Mujer" not in cat_line, f"Categoría de mujer encontrada en contexto hombre: {cat_line}"

        # Verificar que ningún producto de mujer aparezca en candidatos
        for prod in contexto_h["productos_candidatos"]:
            linea = prod.categoria.linea if prod.categoria else ""
            assert linea != "Mujer", f"Producto de mujer encontrado para cliente hombre: {prod.nombre} ({linea})"
            nombre_lower = prod.nombre.lower()
            assert "vestido" not in nombre_lower, f"Vestido encontrado para hombre: {prod.nombre}"
            assert "falda" not in nombre_lower, f"Falda encontrada para hombre: {prod.nombre}"

        print("[OK] Filtrado estricto por género 'Hombre' verificado exitosamente.")

        # 2. Probar con usuario Mujer
        req_mujer = ChatRequest(
            mensaje="Quiero ver vestidos para una fiesta",
            genero_usuario="Mujer",
            nombre_usuario="Valeria",
            id_sucursal=1,
            nombre_sucursal="Sucursal Equipetrol",
        )

        contexto_m = IAService._recuperar_contexto_db(
            db,
            consulta=req_mujer.mensaje,
            id_sucursal=req_mujer.id_sucursal,
            genero_usuario=req_mujer.genero_usuario,
            nombre_sucursal=req_mujer.nombre_sucursal,
        )

        prompt_m = IAService._construir_system_instruction(
            contexto_m,
            nombre_usuario="Valeria",
            genero_usuario="Mujer",
            nombre_sucursal="Sucursal Equipetrol",
        )

        assert "Conoce al usuario: Nombre: Valeria, Género: Mujer, Sucursal: Sucursal Equipetrol." in prompt_m
        print("[OK] Prompt para cliente 'Mujer' verificado exitosamente.")

        # 3. Probar fallback respuesta (cuando Gemini no está disponible o da error)
        fallback = IAService._fallback_respuesta(
            db,
            consulta="Pantalones",
            contexto=contexto_h,
            nombre_usuario="Carlos",
            genero_usuario="Hombre",
            nombre_sucursal="Sucursal Equipetrol",
        )
        assert "Carlos" in fallback.respuesta
        assert "Sucursal Equipetrol" in fallback.respuesta
        print("[OK] Fallback inteligente incluye nombre, sucursal y opciones correctamente.")

        print("\nTODAS LAS PRUEBAS DE GROUNDING, GÉNERO Y PROMPT PASARON EXITOSAMENTE.")

    finally:
        db.close()

if __name__ == "__main__":
    test_ia_prompt_and_grounding()
