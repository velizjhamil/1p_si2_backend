"""Script de diagnóstico para Virtual Try-On con Google Gemini (google-genai SDK).

Uso:
    python scripts/test_tryon.py [ruta_persona.jpg] [ruta_prenda.png] [--model NOMBRE_MODELO]
"""
import argparse
import os
import sys
from pathlib import Path
from PIL import Image, ImageDraw
import io

# Asegurar path para importar app.core.config si se requiere
BACKEND_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_DIR))

try:
    from dotenv import load_dotenv
    load_dotenv(BACKEND_DIR / ".env")
except ImportError:
    pass

from google import genai
from google.genai import types
from google.genai import errors


def get_api_key() -> str:
    key = os.getenv("NANO_BANANA_API_KEY") or os.getenv("GEMINI_API_KEY")
    if not key:
        print("[ERROR] Ni NANO_BANANA_API_KEY ni GEMINI_API_KEY están definidas en .env o entorno.")
        sys.exit(1)
    return key


def list_image_models(client: genai.Client):
    print("\n=== Modelos disponibles con 'image' en el nombre ===")
    try:
        models = client.models.list()
        found = []
        for m in models:
            m_name = getattr(m, "name", str(m))
            display_name = getattr(m, "display_name", "")
            methods = getattr(m, "supported_generation_methods", [])
            # Revisar si contiene "image" o genera imagen
            if "image" in m_name.lower() or "image" in display_name.lower():
                found.append(f" - {m_name} (display: {display_name})")
        if found:
            for item in found:
                print(item)
        else:
            print(" (No se encontraron modelos con 'image' en el nombre)")
    except Exception as e:
        print(f"[ERROR al listar modelos]: {e}")


def create_sample_images():
    """Genera imágenes sintéticas si no se pasan por CLI."""
    persona_path = BACKEND_DIR / "sample_persona.jpg"
    prenda_path = BACKEND_DIR / "sample_prenda.png"

    if not persona_path.exists():
        img = Image.new("RGB", (600, 800), color=(220, 220, 220))
        d = ImageDraw.Draw(img)
        # Cabeza
        d.ellipse([260, 80, 340, 160], fill=(240, 200, 180))
        # Torso
        d.rectangle([230, 160, 370, 420], fill=(100, 100, 150))
        # Piernas
        d.rectangle([240, 420, 290, 720], fill=(50, 50, 70))
        d.rectangle([310, 420, 360, 720], fill=(50, 50, 70))
        img.save(persona_path, "JPEG", quality=90)
        print(f"[INFO] Creada imagen sintética de persona en {persona_path}")

    if not prenda_path.exists():
        img_p = Image.new("RGBA", (400, 400), color=(0, 0, 0, 0))
        dp = ImageDraw.Draw(img_p)
        dp.polygon([(100, 50), (300, 50), (360, 120), (310, 160), (280, 140), (280, 360), (120, 360), (120, 140), (90, 160), (40, 120)], fill=(220, 50, 50, 255))
        img_p.save(prenda_path, "PNG")
        print(f"[INFO] Creada imagen sintética de prenda en {prenda_path}")

    return str(persona_path), str(prenda_path)


def test_tryon(persona_path: str, prenda_path: str, model_name: str):
    api_key = get_api_key()
    client = genai.Client(api_key=api_key)

    list_image_models(client)

    print(f"\n=== Probando generación con modelo: {model_name} ===")
    print(f"Persona: {persona_path}")
    print(f"Prenda:  {prenda_path}")

    with open(persona_path, "rb") as f:
        persona_bytes = f.read()
    with open(prenda_path, "rb") as f:
        prenda_bytes = f.read()

    prenda_ext = Path(prenda_path).suffix.lower()
    prenda_mime = "image/png" if prenda_ext == ".png" else "image/jpeg"

    prompt = (
        "You are a state-of-the-art virtual try-on engine. "
        "Re-render Image 1 so that the person is physically wearing the garment from Image 2. "
        "The output must look like a real photo of that person dressed in that garment. "
        "Return ONLY the final edited photograph, photorealistic, sharp, high resolution."
    )

    contents = [
        "Image 1 (PERSON to be dressed):",
        types.Part.from_bytes(data=persona_bytes, mime_type="image/jpeg"),
        "Image 2 (GARMENT to wear):",
        types.Part.from_bytes(data=prenda_bytes, mime_type=prenda_mime),
        prompt,
    ]

    config = types.GenerateContentConfig(response_modalities=["TEXT", "IMAGE"])

    try:
        print("[INFO] Enviando petición a Gemini API generate_content...")
        response = client.models.generate_content(
            model=model_name,
            contents=contents,
            config=config,
        )

        print("\n--- Respuesta recibida ---")
        prompt_feedback = getattr(response, "prompt_feedback", None)
        print(f"Prompt Feedback: {prompt_feedback}")

        if not response.candidates:
            print("[ADVERTENCIA] No hay candidates en la respuesta.")
            return

        candidate = response.candidates[0]
        finish_reason = getattr(candidate, "finish_reason", None)
        print(f"Finish Reason: {finish_reason}")

        saved_image = False
        if candidate.content and candidate.content.parts:
            print(f"Total de partes en el candidate: {len(candidate.content.parts)}")
            for idx, part in enumerate(candidate.content.parts):
                text = getattr(part, "text", None)
                if text:
                    print(f"\n[Parte {idx} - TEXTO]:\n{text}")

                inline_data = getattr(part, "inline_data", None)
                if inline_data and getattr(inline_data, "data", None):
                    img_bytes = inline_data.data
                    mime = getattr(inline_data, "mime_type", "desconocido")
                    print(f"\n[Parte {idx} - IMAGEN]: {len(img_bytes)} bytes, mime_type={mime}")
                    salida_path = BACKEND_DIR / "salida.jpg"
                    img_pil = Image.open(io.BytesIO(img_bytes)).convert("RGB")
                    img_pil.save(salida_path, "JPEG", quality=92, optimize=True)
                    print(f"--> [ÉXITO] Imagen guardada en: {salida_path}")
                    saved_image = True

        if not saved_image:
            print("[ADVERTENCIA] La respuesta no contuvo ninguna parte con inline_data de imagen.")

    except errors.APIError as api_err:
        print("\n[FALLO - Google APIError]")
        print(f"Tipo: {type(api_err).__name__}")
        print(f"Código HTTP: {getattr(api_err, 'code', 'N/A')}")
        print(f"Mensaje: {api_err.message if hasattr(api_err, 'message') else str(api_err)}")
        print(f"Detalles: {getattr(api_err, 'details', 'N/A')}")
    except Exception as e:
        print("\n[FALLO - Excepción general]")
        print(f"Tipo: {type(e).__name__}")
        print(f"Mensaje: {str(e)}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Test Gemini Virtual Try-On")
    parser.add_argument("persona", nargs="?", default=None, help="Ruta a persona.jpg")
    parser.add_argument("prenda", nargs="?", default=None, help="Ruta a prenda.png")
    parser.add_argument("--model", default=os.getenv("NANO_BANANA_MODEL", "gemini-2.5-flash"), help="Modelo de Gemini a probar")
    args = parser.parse_args()

    p_persona = args.persona
    p_prenda = args.prenda
    if not p_persona or not p_prenda:
        print("[INFO] No se pasaron rutas completas de persona/prenda. Creando/usando imágenes de muestra...")
        p_persona, p_prenda = create_sample_images()

    test_tryon(p_persona, p_prenda, args.model)
