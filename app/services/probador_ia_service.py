"""Servicio de Inteligencia Artificial para el Probador Virtual con Google Gemini."""
from __future__ import annotations

import base64
import io
import json
import logging
import math
import random
import re
import ssl
import time
from pathlib import Path
from typing import Any, Dict, Optional, Tuple
import urllib.request

from fastapi import HTTPException, status
from PIL import Image, ImageChops, ImageDraw, ImageEnhance, ImageFilter, ImageOps

from app.core.config import get_settings
from app.services.ia_service import IAService

logger = logging.getLogger(__name__)

ORDEN_TALLAS = ["XS", "S", "M", "L", "XL", "XXL"]
MAX_GARMENT_BYTES = 10 * 1024 * 1024  # Límite de seguridad: 10 MB para descarga de prenda


class ProbadorIAService:
    """Motor de análisis corporal y simulación de prueba de prendas con Google Gemini y Pillow."""

    _ultimo_error_gemini: Optional[str] = None

    @staticmethod
    def _indice_talla(talla: str) -> int:
        try:
            return ORDEN_TALLAS.index(talla.upper())
        except ValueError:
            return 2  # default M

    @classmethod
    def _recomendar_talla_regla(cls, complexion: str, tallas_disponibles: list[str]) -> str:
        if not tallas_disponibles:
            return "M"
        ordenadas = sorted(tallas_disponibles, key=cls._indice_talla)
        objetivo = {"DELGADA": 0, "MEDIA": 1, "ROBUSTA": 2}.get(complexion)
        if objetivo is None:
            objetivo = min(1, len(ordenadas) - 1)
        return ordenadas[min(objetivo, len(ordenadas) - 1)]

    @classmethod
    def _estimar_ajuste_regla(cls, elegida: str, recomendada: str) -> str:
        if elegida.upper() == recomendada.upper():
            return "PERFECTO"
        if cls._indice_talla(elegida) < cls._indice_talla(recomendada):
            return "AJUSTADO"
        return "HOLGADO"

    @classmethod
    def _descargar_y_procesar_prenda(
        cls,
        url: Optional[str],
        color_hex: Optional[str] = None,
        categoria: str = "Prenda",
    ) -> Tuple[bytes, str, Image.Image]:
        """
        Descarga de forma segura la imagen de la prenda desde su URL o la decodifica de base64.
        Devuelve (bytes, mime_type, PIL_Image).
        Aplica validación de certificado SSL estricta, límite de tamaño (10MB) y validación de imagen.
        Si la URL falla o no existe, sintetiza una prenda realista basada en categoría y color.
        """
        raw_bytes: Optional[bytes] = None
        mime_type = "image/jpeg"
        pil_img: Optional[Image.Image] = None

        if url:
            url_str = url.strip()
            # Caso 1: Data URL base64
            if url_str.startswith("data:image/"):
                try:
                    header, data_part = url_str.split(",", 1)
                    decoded_bytes = base64.b64decode(data_part)
                    if len(decoded_bytes) > MAX_GARMENT_BYTES:
                        raise ValueError(f"La imagen en base64 excede el límite de 10 MB ({len(decoded_bytes)} bytes)")

                    # Validar con Pillow que sea imagen real
                    test_pil = Image.open(io.BytesIO(decoded_bytes))
                    test_pil.verify()
                    fmt = (test_pil.format or "").upper()
                    if fmt == "PNG" or "image/png" in header:
                        mime_type = "image/png"
                    elif fmt == "WEBP" or "image/webp" in header:
                        mime_type = "image/webp"
                    else:
                        mime_type = "image/jpeg"

                    raw_bytes = decoded_bytes
                    pil_img = Image.open(io.BytesIO(raw_bytes)).convert("RGBA")
                except Exception as e:
                    logger.warning(f"Error decodificando o validando base64 de prenda: {e}")

            # Caso 2: URL HTTP / HTTPS remota con SSL estricto y límite de descarga
            elif url_str.startswith("http://") or url_str.startswith("https://"):
                try:
                    req = urllib.request.Request(
                        url_str,
                        headers={
                            "User-Agent": "AttentionApp/1.0 (VirtualTryOn; Android/Flutter)",
                            "Accept": "image/*",
                        },
                    )
                    ssl_context = ssl.create_default_context()
                    ssl_context.check_hostname = True
                    ssl_context.verify_mode = ssl.CERT_REQUIRED

                    with urllib.request.urlopen(req, timeout=15, context=ssl_context) as response:
                        content_length = response.headers.get("Content-Length")
                        if content_length and int(content_length) > MAX_GARMENT_BYTES:
                            raise ValueError(f"La imagen remota excede el límite de 10 MB ({content_length} bytes)")

                        chunks = []
                        total_read = 0
                        while True:
                            chunk = response.read(65536)
                            if not chunk:
                                break
                            total_read += len(chunk)
                            if total_read > MAX_GARMENT_BYTES:
                                raise ValueError(f"Descarga de prenda abortada: excede {MAX_GARMENT_BYTES} bytes")
                            chunks.append(chunk)

                        downloaded_bytes = b"".join(chunks)

                        # Validar con Pillow que el contenido descargado sea una imagen válida
                        test_pil = Image.open(io.BytesIO(downloaded_bytes))
                        test_pil.verify()
                        fmt = (test_pil.format or "").upper()
                        content_type = response.headers.get("Content-Type", "")
                        if fmt == "PNG" or "png" in content_type:
                            mime_type = "image/png"
                        elif fmt == "WEBP" or "webp" in content_type:
                            mime_type = "image/webp"
                        else:
                            mime_type = "image/jpeg"

                        raw_bytes = downloaded_bytes
                        pil_img = Image.open(io.BytesIO(raw_bytes)).convert("RGBA")
                except Exception as e:
                    logger.warning(f"No se pudo descargar o validar la imagen de la prenda desde URL ({url_str}): {e}")

            # Caso 3: Ruta local en disco del servidor
            else:
                try:
                    import os
                    clean_path = url_str.lstrip("/\\")
                    for base_dir in [".", "app", "app/static", "static"]:
                        full_p = os.path.join(base_dir, clean_path)
                        if os.path.isfile(full_p):
                            with open(full_p, "rb") as f:
                                local_bytes = f.read()
                            test_pil = Image.open(io.BytesIO(local_bytes))
                            test_pil.verify()
                            raw_bytes = local_bytes
                            pil_img = Image.open(io.BytesIO(raw_bytes)).convert("RGBA")
                            mime_type = "image/png" if full_p.lower().endswith(".png") else "image/jpeg"
                            break
                except Exception as e:
                    logger.warning(f"No se pudo leer la imagen de la prenda desde archivo local ({url_str}): {e}")

        # Caso 4: Fallback si no hay imagen válida o no se pudo descargar
        if pil_img is None or raw_bytes is None:
            pil_img = cls._unused_generar_prenda_fallback(color_hex, categoria)
            mime_type = "image/png"

        # Optimización: Redimensionar prenda a máximo 1024x1024 manteniendo aspecto y sin recortes agresivos
        try:
            if pil_img.width > 1024 or pil_img.height > 1024:
                pil_img.thumbnail((1024, 1024), Image.Resampling.LANCZOS)
            buf_p = io.BytesIO()
            if mime_type == "image/png":
                pil_img.save(buf_p, format="PNG", optimize=True)
            elif mime_type == "image/webp":
                pil_img.save(buf_p, format="WEBP", quality=90)
            else:
                pil_img.convert("RGB").save(buf_p, format="JPEG", quality=92, optimize=True)
                mime_type = "image/jpeg"
            raw_bytes = buf_p.getvalue()
        except Exception as e:
            logger.warning(f"Error en compresión optimizada de prenda: {e}")

        return raw_bytes, mime_type, pil_img

    @classmethod
    def _unused_remover_fondo_claro(cls, img: Image.Image) -> Image.Image:
        """
        Aísla la prenda removiendo fondos de estudio (blancos, grises, claros)
        mediante muestreo de esquinas y distancia euclidiana con suavizado alfa.
        """
        try:
            rgba = img.convert("RGBA")
            w, h = rgba.size
            if w < 10 or h < 10:
                return rgba

            sample_points = [
                (0, 0), (w - 1, 0), (0, h - 1), (w - 1, h - 1),
                (w // 2, 0), (0, h // 2), (w - 1, h // 2)
            ]
            samples = [rgba.getpixel(pt) for pt in sample_points]
            if any(s[3] < 50 for s in samples):
                bbox = rgba.getbbox()
                return rgba.crop(bbox) if bbox else rgba

            bg_r = sum(s[0] for s in samples) // len(samples)
            bg_g = sum(s[1] for s in samples) // len(samples)
            bg_b = sum(s[2] for s in samples) // len(samples)

            if bg_r > 190 and bg_g > 190 and bg_b > 190:
                data = list(rgba.getdata())
                new_data = []
                threshold = 36.0
                feather = 14.0
                for r, g, b, a in data:
                    if a == 0:
                        new_data.append((r, g, b, 0))
                        continue
                    dist = math.sqrt((r - bg_r) ** 2 + (g - bg_g) ** 2 + (b - bg_b) ** 2)
                    if dist < threshold:
                        new_data.append((r, g, b, 0))
                    elif dist < threshold + feather:
                        factor = (dist - threshold) / feather
                        new_data.append((r, g, b, int(a * factor)))
                    else:
                        new_data.append((r, g, b, a))
                rgba.putdata(new_data)

            bbox = rgba.getbbox()
            if bbox:
                rgba = rgba.crop(bbox)
            return rgba
        except Exception as e:
            logger.warning(f"Error en aislamiento de prenda: {e}")
            return img

    @classmethod
    def _unused_generar_prenda_fallback(cls, color_hex: Optional[str], categoria: str) -> Image.Image:
        w, h = 400, 500
        img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)

        c = (60, 80, 180, 240)
        if color_hex:
            try:
                hx = color_hex.lstrip("#")
                if len(hx) == 6:
                    c = (int(hx[0:2], 16), int(hx[2:4], 16), int(hx[4:6], 16), 245)
            except Exception:
                pass

        draw.polygon(
            [
                (120, 30),
                (40, 70),
                (10, 180),
                (70, 200),
                (80, 480),
                (320, 480),
                (330, 200),
                (390, 180),
                (360, 70),
                (280, 30),
                (200, 70),
            ],
            fill=c,
            outline=(max(0, c[0] - 30), max(0, c[1] - 30), max(0, c[2] - 30), 255),
        )
        return img

    @staticmethod
    def _obtener_cliente_nano_banana():
        settings = get_settings()
        api_key = settings.NANO_BANANA_API_KEY or settings.GEMINI_API_KEY
        if not api_key:
            logger.warning("NANO_BANANA_API_KEY ni GEMINI_API_KEY configuradas en las variables de entorno.")
            return None
        try:
            from google import genai
            return genai.Client(api_key=api_key)
        except Exception as exc:
            logger.exception(f"Error al inicializar cliente Nano Banana Pro / Google GenAI: {exc}")
            return None

    @staticmethod
    def _categorizar_error_google(api_exc: Exception) -> Dict[str, str]:
        """
        Categoriza la excepción devuelta por google-genai para producir un
        log legible y accionable. Devuelve un dict con:

          - categoria: AUTH | PERMISSIONS | MODEL_NOT_FOUND | RATE_LIMIT |
                       TIMEOUT | SAFETY_BLOCKED | EMPTY_RESPONSE | RED |
                       CLIENT_ERROR | DESCONOCIDO
          - http_code: código HTTP si fue posible extraerlo (string), '' si no.
          - resumen: una línea con la causa probable.
          - accion: sugerencia concreta para el operador (qué revisar / cambiar).

        Esta función NO decide el flujo, solo formatea el log. El flujo
        (fallback local o propagación) lo decide `procesar_simulacion_ar`.
        """
        # Extraer código HTTP si la excepción del SDK lo expone.
        http_code = ""
        try:
            for attr in ("status_code", "code", "http_status", "status"):
                v = getattr(api_exc, attr, None)
                if isinstance(v, int):
                    http_code = str(v)
                    break
            # Algunos errores del SDK vienen como dict en .response o .details
            if not http_code:
                resp = getattr(api_exc, "response", None)
                if resp is not None:
                    sc = getattr(resp, "status_code", None) or getattr(resp, "status", None)
                    if isinstance(sc, int):
                        http_code = str(sc)
        except Exception:
            pass

        texto = (str(api_exc) or "").lower()
        tipo = type(api_exc).__name__

        # Heurísticas de categorización. Orden importa: las más específicas
        # primero para no etiquetar mal un 401 con "rate limit" por coincidencia
        # de palabra.
        categoria = "DESCONOCIDO"
        accion = "Pegá el log completo al equipo de plataforma para diagnosticar."

        if "api_key_invalid" in texto or "api key not valid" in texto or http_code in ("400", "401"):
            categoria = "AUTH"
            accion = (
                "Verificá que NANO_BANANA_API_KEY / GEMINI_API_KEY esté bien escrita y "
                "que la key corresponda al proyecto Google AI Studio que tiene Imagen habilitado. "
                "Si rotaste la key, regenerala y redeployá el backend."
            )
        elif "permission_denied" in texto or http_code == "403":
            categoria = "PERMISSIONS"
            accion = (
                "La API key es válida pero el modelo no está habilitado en tu proyecto. "
                "Entrá a https://aistudio.google.com/ -> Get API key -> asegurate de que "
                "el proyecto tenga habilitada la API de Imagen (o solicitá acceso si está en allowlist)."
            )
        elif "not_found" in texto or http_code == "404":
            categoria = "MODEL_NOT_FOUND"
            accion = (
                "El modelo 'imagen-3.0-capability-001' no existe o no está disponible para tu región/cuenta. "
                "Listá los modelos disponibles con `client.models.list()` y reemplazá MODELO en config.py."
            )
        elif "quota" in texto or "rate" in texto or "resource_exhausted" in texto or http_code in ("429", "509"):
            categoria = "RATE_LIMIT"
            accion = (
                "Cuota de Google GenAI agotada o rate limit activo. Esperá unos minutos o "
                "subí la cuota en Google Cloud Console. Para una demo se recomienda bajar la "
                "concurrencia del endpoint del probador."
            )
        elif "timeout" in texto or "timed out" in texto or "deadline" in texto:
            categoria = "TIMEOUT"
            accion = (
                "La API de Google no respondió a tiempo. Aumentá el timeout del cliente o "
                "reintentá; si persiste, el modelo puede estar sobrecargado."
            )
        elif any(k in texto for k in ["safety", "blocked", "policy", "harm"]):
            categoria = "SAFETY_BLOCKED"
            accion = (
                "Google rechazó el prompt/imagen por filtros de seguridad. "
                "Revisá person_generation en EditImageConfig (usá ALLOW_ADULT) o suavizá el prompt."
            )
        elif any(k in texto for k in ["connection", "network", "unreachable", "dns", "ssl"]):
            categoria = "RED"
            accion = (
                "Falló la conexión de red entre el backend y Google. "
                "Verificá firewall/proxy/Render outbound. No es problema de la API key."
            )
        elif tipo in ("ClientError", "ServerError") or http_code:
            categoria = "CLIENT_ERROR" if (http_code and http_code.startswith("4")) else "DESCONOCIDO"
            if categoria == "CLIENT_ERROR":
                accion = (
                    f"Error HTTP {http_code} del cliente Google. Revisá los argumentos enviados "
                    "(modelo, reference_images, config) contra la doc del SDK google-genai 2.11.0."
                )
            else:
                accion = f"Error HTTP {http_code or 'desconocido'} del servidor de Google. Reintentá."

        return {
            "categoria": categoria,
            "http_code": http_code,
            "resumen": f"{tipo}: {api_exc!r}".strip(),
            "accion": accion,
        }

    @staticmethod
    def _construir_prompt_tryon(
        producto_nombre: str,
        producto_categoria: str,
        color_nombre: Optional[str],
        talla_elegida: str,
        complexion: str,
        estatura_cm: Optional[int] = None,
        peso_kg: Optional[int] = None,
    ) -> str:
        """
        Construye el prompt de fusión fotorrealista estricto para modelos de generación de imagen
        (Google GenAI / Imagen 3) garantizando ajuste tridimensional y preservación biométrica.
        """
        prompt_estricto = (
            "Genera una fotografía fotorrealista de cuerpo entero donde la persona de la primera imagen "
            "vista la prenda de la segunda imagen. La tela debe ajustarse tridimensionalmente al torso y hombros, "
            "manteniendo intactos el rostro, la postura y el fondo original."
        )

        cat_lower = (producto_categoria or "").lower()
        if any(k in cat_lower for k in ["zapato", "calzado", "tenis", "zapatilla", "bota"]):
            zona = "feet / shoes"
            guia_anatomica = (
                "Ajusta el calzado con absoluta precisión anatómica a los pies, tobillos y postura de la persona. "
                "Alinea perspectiva, oclusión con el pantalón y sombras de contacto con el suelo."
            )
        elif any(k in cat_lower for k in ["vestido", "enterizo", "mono"]):
            zona = "full torso and legs"
            guia_anatomica = (
                "Adapta la prenda/vestido tridimensionalmente al cuerpo entero: sigue la caída anatómica de hombros, "
                "moldea el busto y torso, ciñe en cintura y cae naturalmente sobre caderas y muslos con pliegues orgánicos por gravedad."
            )
        elif any(k in cat_lower for k in ["pantalón", "pantalon", "jean", "short", "falda", "bermuda", "calzas", "legging"]):
            zona = "lower body (waist, hips, thighs, and legs)"
            guia_anatomica = (
                "Envuelve la prenda alrededor de la cintura, pelvis, muslos y piernas siguiendo la postura y flexión natural, "
                "con arrugas realistas de la tela en la entrepierna, rodillas y dobladillo."
            )
        else:
            zona = "upper body (shoulders, chest, torso, and arms)"
            guia_anatomica = (
                "Deformación anatómica 3D: La prenda NO debe lucir como una pegatina 2D, parche plano o recorte flotante. "
                "Modela y proyecta la tela sobre el volumen 3D del torso, siguiendo la pendiente natural de los hombros, "
                "el volumen del pecho, el entalle del abdomen y la orientación de los brazos."
            )

        contexto_prenda = (
            f"\n\nESPECIFICACIONES DE LA PRENDA:\n"
            f"- Nombre del producto: {producto_nombre}\n"
            f"- Categoría: {producto_categoria} (Zona anatómica: {zona})\n"
            f"- Color: {color_nombre or 'como se aprecia en la prenda de referencia'}\n"
            f"- Talla seleccionada: {talla_elegida}\n"
            f"- Complexión del usuario: {complexion}"
        )
        if estatura_cm and peso_kg:
            contexto_prenda += f"\n- Parámetros biométricos: {estatura_cm} cm de estatura, {peso_kg} kg de peso"

        reglas_realismo = (
            f"\n\nREGLAS DE CALIDAD Y REALISMO FOTOGRÁFICO:\n"
            f"1. Guía de zona: {guia_anatomica}\n"
            f"2. Preservación biométrica absoluta: Rasgos faciales, ojos, expresión, cabello, tono de piel, manos y fondo idénticos a la foto original.\n"
            f"3. Oclusión ambiental: Renderizar micro-sombras de contacto sutiles en cuello, sisas y costuras para que la tela se sienta en contacto físico con el cuerpo.\n"
            f"4. Coherencia lumínica: La iluminación de la prenda debe integrarse perfectamente con la dirección de luz y temperatura de la foto del usuario.\n"
            f"5. Salida fotográfica: Retornar exclusivamente la fotografía fotorrealista final generada, nítida y sin marcas de agua ni collages."
        )

        return f"{prompt_estricto}{contexto_prenda}{reglas_realismo}"

    @staticmethod
    def _normalizar_nombre_producto(nombre: str) -> str:
        """
        Normaliza el nombre del producto para hacer match con los archivos
        del catálogo estático. Reglas:
          1. lowercase
          2. espacios -> _
          3. caracteres no [a-z0-9_-] se eliminan
        Ejemplos:
          "Polo Deportivo Dry-Fit" -> "polo_deportivo_dry-fit"
          "Camisa Oxford Formal"    -> "camisa_oxford_formal"
          "POLO BÁSICO ALGODÓN"     -> "polo_bsico_algodn" (sin acentos)
        """
        import re as _re
        norm = (nombre or "").lower().replace(" ", "_")
        norm = _re.sub(r"[^a-z0-9_\-]", "", norm)
        return norm

    @classmethod
    def _resolver_mock_dir(cls) -> Optional[Path]:
        """
        Resuelve la carpeta de mocks. Si TRYON_MOCK_DIR está configurada en
        el .env, la usa tal cual. Si no, busca:
          1) <monorepo>/1p_si2_frontend/src/public/
          2) <monorepo>/1p_si2_frontend/public/  (algunos scaffolds)
          3) <monorepo>/public/                   (raíz del repo, fallback)
        Devuelve la primera carpeta que exista. None si ninguna.
        """
        try:
            settings = get_settings()
            custom = (settings.TRYON_MOCK_DIR or "").strip()
            if custom:
                p = Path(custom)
                if p.is_dir():
                    return p
                logger.warning(
                    f"[MOCK] TRYON_MOCK_DIR configurada pero no existe: {custom}"
                )
        except Exception:
            pass

        # backend/app/services/probador_ia_service.py ->
        #   backend/ (1) -> 1erParcialSI2/ (2) [monorepo]
        here = Path(__file__).resolve()
        monorepo = here.parent.parent.parent.parent
        candidatos = [
            monorepo / "1p_si2_frontend" / "src" / "public",
            monorepo / "1p_si2_frontend" / "public",
            monorepo / "public",
        ]
        for c in candidatos:
            if c.is_dir():
                return c
        return None

    @classmethod
    def _buscar_imagen_mock(
        cls,
        nombre_producto: str,
        mock_dir: "Path",
    ) -> Optional[Tuple[bytes, str]]:
        """
        Busca en mock_dir un archivo cuyo nombre matchee el producto
        normalizado, con cualquier extensión de imagen soportada.
        Devuelve (bytes, mime) o None si no hay match.
        """
        import re as _re
        norm = cls._normalizar_nombre_producto(nombre_producto)
        extensiones = ("jpg", "jpeg", "png", "webp")
        for ext in extensiones:
            candidato = mock_dir / f"{norm}.{ext}"
            if candidato.is_file():
                try:
                    raw = candidato.read_bytes()
                    if not raw:
                        continue
                    mime = (
                        "image/jpeg" if ext in ("jpg", "jpeg")
                        else f"image/{ext}"
                    )
                    return raw, mime
                except Exception as exc:
                    logger.warning(
                        f"[MOCK] Error leyendo {candidato}: {exc}"
                    )
                    continue

        # Búsqueda fuzzy: ignora el caso y los separadores. Útil para
        # cuando el nombre del producto tiene una variante menor (ej.
        # "Polo Deportivo Dry Fit" -> "polo_deportivo_dry_fit" matchea
        # "polo_deportivo_dry-fit").
        norm_relajado = _re.sub(r"[_\-]", "", norm)
        for f in mock_dir.iterdir():
            if not f.is_file():
                continue
            if f.suffix.lower().lstrip(".") not in extensiones:
                continue
            stem_relajado = _re.sub(r"[_\-]", "", f.stem.lower())
            if stem_relajado == norm_relajado:
                try:
                    raw = f.read_bytes()
                    if not raw:
                        continue
                    ext = f.suffix.lower().lstrip(".")
                    mime = (
                        "image/jpeg" if ext in ("jpg", "jpeg")
                        else f"image/{ext}"
                    )
                    return raw, mime
                except Exception as exc:
                    logger.warning(f"[MOCK] Error leyendo {f}: {exc}")
                    continue

        return None

    @classmethod
    def _buscar_primer_mock_disponible(
        cls, mock_dir: "Path"
    ) -> Optional[Tuple[bytes, str, str]]:
        """
        Si no hay match exacto del producto, devuelve el primer archivo
        de imagen válido en mock_dir como fallback. Devuelve
        (bytes, mime, nombre_archivo) o None si la carpeta está vacía.
        """
        extensiones = ("jpg", "jpeg", "png", "webp")
        for f in sorted(mock_dir.iterdir()):
            if not f.is_file():
                continue
            if f.suffix.lower().lstrip(".") not in extensiones:
                continue
            # Saltar archivos que claramente no son mocks (ej. qr codes).
            if f.stem.lower() in ("qr", "qrcode", "logo", "favicon"):
                continue
            try:
                raw = f.read_bytes()
                if not raw:
                    continue
                ext = f.suffix.lower().lstrip(".")
                mime = (
                    "image/jpeg" if ext in ("jpg", "jpeg")
                    else f"image/{ext}"
                )
                return raw, mime, f.name
            except Exception:
                continue
        return None

    @classmethod
    def _generar_prueba_virtual_mock(
        cls,
        producto_nombre: str,
        producto_categoria: str,
        color_nombre: Optional[str] = None,
        talla_elegida: Optional[str] = None,
        complexion: str = "MEDIA",
    ) -> Tuple[bytes, str]:
        """
        Simulación Mock Mode del Probador Virtual.

        Devuelve la imagen estática del catálogo de mocks guardada en
        1p_si2_frontend/src/public/ (o la ruta configurada en
        TRYON_MOCK_DIR). NO llama a la API de Google. NO genera la imagen
        con IA. Es determinístico: la misma prenda -> la misma imagen.

        Reglas de búsqueda:
          1. Normaliza el nombre del producto (lowercase, espacios -> _,
             caracteres no-ASCII fuera).
          2. Busca {nombre_norm}.{jpg|png|webp} en mock_dir.
          3. Si no hay match exacto, intenta match fuzzy (sin separadores).
          4. Si tampoco hay match, usa el primer mock disponible en la
             carpeta (fallback de último recurso para que la demo NUNCA
             rompa).

        Latencia simulada: configurable vía TRYON_MOCK_LATENCY_MIN/MAX.
        """
        import time as _time
        import random as _random

        # Latencia simulada para realismo de UI.
        try:
            settings = get_settings()
            t_min = float(getattr(settings, "TRYON_MOCK_LATENCY_MIN", 0.0))
            t_max = float(getattr(settings, "TRYON_MOCK_LATENCY_MAX", 0.0))
        except Exception:
            t_min, t_max = 1.0, 2.0

        if t_max > t_min:
            delay = _random.uniform(t_min, t_max)
            logger.info(
                f"[MOCK] Simulando latencia de IA: {delay:.2f}s "
                f"(spinner 'Optimizando postura y adaptando tejido...')"
            )
            _time.sleep(delay)

        mock_dir = cls._resolver_mock_dir()
        if mock_dir is None:
            raise RuntimeError(
                "[MOCK] No se encontro ninguna carpeta de mocks. Configurá "
                "TRYON_MOCK_DIR en .env o creá 1p_si2_frontend/src/public/ "
                "con las imagenes de las prendas."
            )

        # 1) Búsqueda exacta
        match = cls._buscar_imagen_mock(producto_nombre, mock_dir)
        if match is not None:
            raw, mime = match
            logger.info(
                f"[MOCK] Producto '{producto_nombre}' -> "
                f"{cls._normalizar_nombre_producto(producto_nombre)} "
                f"(match exacto en {mock_dir})"
            )
            return raw, mime

        # 2) Búsqueda fuzzy (sin separadores)
        import re as _re
        norm = cls._normalizar_nombre_producto(producto_nombre)
        norm_relajado = _re.sub(r"[_\-]", "", norm)
        for f in mock_dir.iterdir():
            if not f.is_file():
                continue
            if f.suffix.lower().lstrip(".") not in ("jpg", "jpeg", "png", "webp"):
                continue
            if _re.sub(r"[_\-]", "", f.stem.lower()) == norm_relajado:
                raw = f.read_bytes()
                ext = f.suffix.lower().lstrip(".")
                mime = "image/jpeg" if ext in ("jpg", "jpeg") else f"image/{ext}"
                logger.info(
                    f"[MOCK] Producto '{producto_nombre}' -> match fuzzy "
                    f"con {f.name}"
                )
                return raw, mime

        # 3) Fallback: primer mock disponible
        fallback = cls._buscar_primer_mock_disponible(mock_dir)
        if fallback is not None:
            raw, mime, fname = fallback
            logger.warning(
                f"[MOCK] No se encontro imagen para '{producto_nombre}' "
                f"(normalizado: '{norm}'). Usando fallback '{fname}'. "
                f"Para evitar el fallback, renombra el archivo mock a "
                f"'{norm}.jpg' en {mock_dir}."
            )
            return raw, mime

        raise RuntimeError(
            f"[MOCK] No hay imagenes de mocks en {mock_dir}. "
            f"Subí al menos una imagen (.jpg/.png/.webp) para que la "
            f"demo funcione."
        )

    @classmethod
    def _generar_prueba_virtual_gemini(
        cls,
        foto_bytes: bytes,
        prenda_bytes: bytes,
        prenda_mime: str,
        producto_nombre: str,
        producto_categoria: str,
        color_nombre: Optional[str],
        talla_elegida: str,
        complexion: str,
        estatura_cm: Optional[int],
        peso_kg: Optional[int],
        foto_pil: Optional[Image.Image] = None,
    ) -> Optional[Tuple[bytes, str]]:
        """
        LLAMADA PURA A GOOGLE GENAI — SIN FALLBACK LOCAL.

        Esta función usa EXCLUSIVAMENTE `client.models.generate_content`
        (multimodal) del SDK oficial google-genai 2.11.0. Las dos imágenes
        (foto del usuario + prenda del catálogo) se envían como `Part.from_bytes`
        y el prompt exige la fusión fotorrealista. El modelo se elige desde
        la config (NANO_BANANA_MODEL) con fallback a `gemini-2.5-flash` /
        `gemini-2.0-flash` si la cuenta lo requiere.

        REGLAS INNEGOCIABLES (override explícito del usuario):
          1. NO existe fallback a Pillow / recortes locales / IDM-VTON.
          2. NO se intenta `edit_image` ni `generate_images` (puros).
          3. Si Google rechaza los parámetros o la API falla, la excepción
             se PROPAGA con traceback completo al log. La consola del
             servidor muestra exactamente qué parámetro o nombre de modelo
             exige la cuenta para aceptar las dos imágenes de entrada.

        Decisión técnica documentada (Turno 7): de los tres métodos del SDK,
        solo `generate_content` multimodal acepta dos imágenes en crudo y
        devuelve una imagen generada. `generate_images` es text-to-image
        puro, y `edit_image` requiere `imagen-3.0-capability-001` (no
        disponible en la cuenta del usuario, ver Turno 4).
        """
        import traceback as _traceback

        cls._ultimo_error_gemini = None
        client = cls._obtener_cliente_nano_banana() or IAService._obtener_cliente_gemini()
        if client is None:
            msg = (
                "Cliente Google GenAI no inicializado. Verificá que "
                "NANO_BANANA_API_KEY o GEMINI_API_KEY estén configuradas."
            )
            logger.error(msg)
            cls._ultimo_error_gemini = msg
            raise RuntimeError(msg)

        prompt = cls._construir_prompt_tryon(
            producto_nombre=producto_nombre,
            producto_categoria=producto_categoria,
            color_nombre=color_nombre,
            talla_elegida=talla_elegida,
            complexion=complexion,
            estatura_cm=estatura_cm,
            peso_kg=peso_kg,
        )

        try:
            from google.genai import types  # type: ignore
        except Exception as import_exc:
            tb = _traceback.format_exc()
            msg = f"No se pudo importar google.genai.types: {import_exc!r}\n{tb}"
            logger.error(msg)
            cls._ultimo_error_gemini = msg
            raise RuntimeError(msg) from import_exc

        # ------------------------------------------------------------------
        # Construcción de las dos partes binarias (foto del usuario + prenda
        # de catálogo). Ambas viajan al modelo como `Part.from_bytes`.
        # ------------------------------------------------------------------
        try:
            foto_part = types.Part.from_bytes(
                data=foto_bytes,
                mime_type="image/jpeg",
            )
            prenda_part = types.Part.from_bytes(
                data=prenda_bytes,
                mime_type=prenda_mime if prenda_mime else "image/jpeg",
            )
        except Exception as part_err:
            tb = _traceback.format_exc()
            msg = f"No se pudo construir Part.from_bytes: {part_err!r}\n{tb}"
            logger.error(msg)
            cls._ultimo_error_gemini = msg
            raise RuntimeError(msg) from part_err

        # ------------------------------------------------------------------
        # Orden de modelos a intentar. La consigna dice "Gemini" genérico;
        # arrancamos con el que el .env declara (NANO_BANANA_MODEL) y
        # caemos a `gemini-2.5-flash` / `gemini-2.0-flash`. Si tu cuenta
        # tiene otro modelo, este log te dice cuál fue aceptado/rechazado.
        # ------------------------------------------------------------------
        settings = get_settings()
        candidatos: list[str] = []
        if settings.NANO_BANANA_MODEL and settings.NANO_BANANA_MODEL.strip():
            candidatos.append(settings.NANO_BANANA_MODEL.strip())
        for fallback in ("gemini-2.5-flash", "gemini-2.0-flash", "gemini-flash-latest"):
            if fallback not in candidatos:
                candidatos.append(fallback)

        # ------------------------------------------------------------------
        # Config: response_modalities = ["TEXT", "IMAGE"] es lo que hace
        # que el modelo multimodal devuelva una imagen además de texto.
        # ------------------------------------------------------------------
        try:
            config = types.GenerateContentConfig(
                response_modalities=["TEXT", "IMAGE"],
            )
        except Exception as cfg_err:
            tb = _traceback.format_exc()
            msg = f"No se pudo construir GenerateContentConfig: {cfg_err!r}\n{tb}"
            logger.error(msg)
            cls._ultimo_error_gemini = msg
            raise RuntimeError(msg) from cfg_err

        contents = [
            foto_part,        # Imagen 1: la persona
            prenda_part,      # Imagen 2: la prenda del catálogo
            prompt,           # Instrucción textual de fusión
        ]

        # ------------------------------------------------------------------
        # Bucle de intento: probamos cada modelo en orden. En cada fallo
        # logueamos el error REAL y seguimos con el siguiente candidato.
        # Si TODOS fallan, lanzamos RuntimeError con el último error y
        # dejamos que la consola del servidor lo muestre completo.
        # ------------------------------------------------------------------
        last_error: Optional[str] = None
        for modelo in candidatos:
            t0 = time.time()
            logger.info(
                f"[PROBADOR][IA] generate_content(model={modelo!r}) "
                f"prompt_chars={len(prompt)} "
                f"foto_bytes={len(foto_bytes)} "
                f"prenda_bytes={len(prenda_bytes)} ..."
            )
            try:
                response = client.models.generate_content(
                    model=modelo,
                    contents=contents,
                    config=config,
                )
            except Exception as api_exc:
                latency = time.time() - t0
                tb = _traceback.format_exc()
                diag = cls._categorizar_error_google(api_exc)
                msg = (
                    f"[PROBADOR][IA] generate_content falló con modelo={modelo!r} "
                    f"(latency={latency:.2f}s). "
                    f"Tipo: {type(api_exc).__name__}. "
                    f"Repr: {api_exc!r}. "
                    f"Mensaje: {str(api_exc) or '<vacío>'}.\n"
                    f"  Categoría: {diag['categoria']} | HTTP: {diag['http_code'] or '-'} | "
                    f"Acción: {diag['accion']}\n"
                    f"--- TRACEBACK ---\n{tb}\n--- FIN TRACEBACK ---"
                )
                logger.error(msg)
                last_error = msg
                cls._ultimo_error_gemini = msg
                continue  # probar siguiente modelo

            latency = time.time() - t0

            # ------------------------------------------------------------------
            # Extraer la imagen de la respuesta multimodal.
            # Estructura esperada: response.candidates[0].content.parts[*]
            #   - Part con .inline_data.data  -> bytes de la imagen
            #   - Part con .text             -> texto explicativo
            # ------------------------------------------------------------------
            img_data: Optional[bytes] = None
            try:
                if response and response.candidates:
                    for cand in response.candidates:
                        if not cand or not cand.content or not cand.content.parts:
                            continue
                        for part in cand.content.parts:
                            inline = getattr(part, "inline_data", None)
                            if inline and getattr(inline, "data", None):
                                img_data = inline.data
                                break
                        if img_data:
                            break
            except Exception as parse_err:
                tb = _traceback.format_exc()
                msg = (
                    f"[PROBADOR][IA] No se pudo parsear la respuesta de "
                    f"{modelo!r} ({latency:.2f}s): {parse_err!r}\n{tb}"
                )
                logger.error(msg)
                last_error = msg
                cls._ultimo_error_gemini = msg
                continue

            if not img_data:
                msg = (
                    f"[PROBADOR][IA] {modelo!r} respondió SIN imagen "
                    f"({latency:.2f}s). response={response!r}"
                )
                logger.error(msg)
                last_error = msg
                cls._ultimo_error_gemini = msg
                continue

            # Éxito: devolver bytes + motor.
            motor_tag = f"Google Gemini multimodal ({modelo})"
            logger.info(
                f"[PROBADOR][IA] {motor_tag} generó imagen OK "
                f"({latency:.2f}s, {len(img_data)} bytes)."
            )
            return (img_data, motor_tag)

        # ------------------------------------------------------------------
        # Si llegamos acá, TODOS los modelos candidatos fallaron.
        # Lanzamos RuntimeError con el último error real para que la consola
        # del servidor muestre exactamente qué pasó (qué modelo rechazó y
        # por qué). NO hay fallback local.
        # ------------------------------------------------------------------
        if last_error is None:
            last_error = "No se intentó ningún modelo (lista de candidatos vacía)."
        logger.error(
            "[PROBADOR][IA] TODOS los modelos candidatos fallaron. "
            "No hay fallback local. El endpoint devolverá 500. "
            f"Último error:\n{last_error}"
        )
        raise RuntimeError(last_error)

    @classmethod
    def _unused_generar_prueba_virtual_failover(
        cls,
        foto_bytes: bytes,
        prenda_bytes: bytes,
        producto_categoria: str = "upper_body",
    ) -> Optional[Tuple[bytes, str]]:
        """Motor secundario de respaldo (Failover): IDM-VTON / Hugging Face o API dedicada."""
        settings = get_settings()
        api_url = settings.IDM_VTON_API_URL or "https://api-inference.huggingface.co/models/yisol/IDM-VTON"
        token = settings.HF_TOKEN
        if not token and not settings.IDM_VTON_API_URL:
            return None

        try:
            import httpx
            headers = {"Authorization": f"Bearer {token}"} if token else {}
            files = {
                "human_img": ("human.jpg", foto_bytes, "image/jpeg"),
                "garm_img": ("garment.jpg", prenda_bytes, "image/jpeg"),
            }
            data = {"garment_des": producto_categoria}
            with httpx.Client(timeout=35.0) as client:
                res = client.post(api_url, headers=headers, files=files, data=data)
                if res.status_code == 200 and res.content:
                    val_pil = Image.open(io.BytesIO(res.content))
                    val_pil.verify()
                    return (res.content, "IDM-VTON (Hugging Face Failover)")
        except Exception as exc:
            logger.warning(f"Failover secundario IDM-VTON no disponible: {exc}")
        return None

    @classmethod
    def _unused_deformar_prenda_al_torso(
        cls,
        prenda: Image.Image,
        target_w: int,
        target_h: int,
        categoria: str = "upper_body",
    ) -> Image.Image:
        """
        Deforma anatómicamente la prenda adaptándola a la tridimensionalidad del cuerpo:
        - Para prendas superiores (camisas, poleras, chaquetas): caída de hombros (desplazamiento
          cuadrático hacia deltoides) y curvatura elíptica en dobladillo inferior.
        - Para prendas inferiores (pantalones, faldas, shorts): curvatura pélvica superior y drapeado vertical.
        - Para vestidos/enterizos: curvatura continua desde hombros hasta dobladillo.
        """
        base = prenda.resize((target_w, target_h), Image.Resampling.LANCZOS).convert("RGBA")
        w, h = base.size
        cat_lower = (categoria or "").lower()
        es_inferior = any(k in cat_lower for k in ["pantalón", "pantalon", "jean", "short", "falda", "bermuda", "calzas", "legging"])
        es_calzado = any(k in cat_lower for k in ["zapato", "calzado", "tenis", "zapatilla", "bota"])

        if es_calzado:
            return base

        extra_h = int(h * 0.14)
        canvas = Image.new("RGBA", (w, h + extra_h), (0, 0, 0, 0))

        if es_inferior:
            # Curvatura en cintura pélvica
            waist_dip_max = int(h * 0.05)
            for x in range(w):
                nx = (x - (w / 2.0)) / (w / 2.0)
                y_offset = int((1.0 - (nx ** 2)) * waist_dip_max)
                col = base.crop((x, 0, x + 1, h))
                canvas.paste(col, (x, y_offset), col)
            return canvas

        # Por defecto: Torso / prendas superiores o vestidos
        shoulder_drop_max = int(h * 0.09)  # 9% de caída anatómica en hombros
        hem_curve_max = int(h * 0.04)      # 4% de curvatura elíptica en dobladillo

        for x in range(w):
            nx = (x - (w / 2.0)) / (w / 2.0)
            dist_center = abs(nx)

            # Caída cuadrática hacia los deltoides/hombros
            y_offset = int((dist_center ** 1.35) * shoulder_drop_max)
            # Dobladillo drapeado alrededor de cadera/cintura
            hem_offset = int((1.0 - (nx ** 2)) * hem_curve_max)

            col = base.crop((x, 0, x + 1, h))
            col_new_h = h + hem_offset
            col_stretched = col.resize((1, col_new_h), Image.Resampling.BILINEAR)
            canvas.paste(col_stretched, (x, y_offset), col_stretched)

        return canvas

    @classmethod
    def _unused_generar_sombreado_pliegues_3d(
        cls,
        w: int,
        h: int,
        categoria: str = "upper_body",
    ) -> Image.Image:
        """
        Genera mapas de volumen tridimensional, pliegues de tensión y oclusión ambiental:
        - Relieve cilíndrico en el torso (sombreado Lambertiano cosenoidal).
        - Pliegues diagonales y arrugas de tela con difuminado gaussiano.
        - Oclusión ambiental bajo el cuello y en sisas.
        """
        shading = Image.new("L", (w, h), 255)
        draw = ImageDraw.Draw(shading)

        # 1. Volumen cilíndrico del cuerpo (luz frontal-cenital, sombra suave en laterales)
        for x in range(w):
            nx = (x - (w / 2.0)) / (w / 2.0)
            factor = math.cos(nx * math.pi * 0.45)
            val = int(185 + factor * 70)  # Rango 185..255 (sombra máx ~27%)
            draw.line([(x, 0), (x, h)], fill=min(255, max(0, val)))

        # 2. Pliegues de tensión orgánica (arrugas naturales de tela en movimiento)
        wrinkle_overlay = Image.new("L", (w, h), 255)
        wrinkle_draw = ImageDraw.Draw(wrinkle_overlay)

        num_folds = 5
        for i in range(num_folds):
            y_start = int(h * (0.26 + (i * 0.12)))
            amp = 30 - (i * 3)
            # Tensión lado izquierdo
            points_l = []
            for step in range(w // 2):
                cur_x = step
                cur_y = y_start + int(math.sin(step * 0.06) * 4) + int((step / (w / 2.0)) * 14)
                points_l.append((cur_x, cur_y))
            for p1, p2 in zip(points_l[:-1], points_l[1:]):
                wrinkle_draw.line([p1, p2], fill=max(0, 255 - amp), width=3)

            # Tensión lado derecho
            points_r = []
            for step in range(w // 2, w):
                cur_x = step
                dist_from_r = w - step
                cur_y = y_start + int(math.sin(dist_from_r * 0.06) * 4) + int((dist_from_r / (w / 2.0)) * 14)
                points_r.append((cur_x, cur_y))
            for p1, p2 in zip(points_r[:-1], points_r[1:]):
                wrinkle_draw.line([p1, p2], fill=max(0, 255 - amp), width=3)

        wrinkle_blur = wrinkle_overlay.filter(ImageFilter.GaussianBlur(5.0))
        shading_blur = shading.filter(ImageFilter.GaussianBlur(4.0))
        combined = ImageChops.multiply(shading_blur, wrinkle_blur)
        return combined

    @classmethod
    def _unused_detectar_anclaje_torso(cls, foto_base: Image.Image, categoria: str = "upper_body") -> Dict[str, Any]:
        """
        Detecta anatómicamente el punto de anclaje del cuello, centro del torso y ancho de hombros:
        1. Segmenta la fisionomía facial/cuello usando espacio cromático YCbCr.
        2. Determina el eje horizontal real del usuario (evitando que la prenda quede flotando a un lado).
        3. Identifica la base del cuello (para alinear el cuello de la prenda exactamente sobre la clavícula).
        4. Calcula el ancho de hombros proporcional para escalar la prenda sin deformar su aspecto.
        """
        w, h = foto_base.size
        sh = min(360, h)
        scale = sh / float(h)
        sw = max(10, int(w * scale))
        thumb = foto_base.resize((sw, sh), Image.Resampling.BILINEAR).convert("RGB")

        try:
            import numpy as np
            ycbcr = thumb.convert("YCbCr")
            arr = np.array(ycbcr, dtype=np.uint8)
            cb = arr[:, :, 1]
            cr = arr[:, :, 2]

            # Detección universal de tono de piel humana (Fitzpatrick I-VI)
            skin = (cb >= 77) & (cb <= 128) & (cr >= 132) & (cr <= 175)
            search_h = int(sh * 0.65)
            pts = np.argwhere(skin[:search_h, :])

            if len(pts) >= (sw * sh * 0.005):
                head_ys = pts[:, 0]
                head_xs = pts[:, 1]

                center_x_thumb = float(np.median(head_xs))
                neck_y_thumb = float(np.percentile(head_ys, 92))
                head_w_thumb = float(np.percentile(head_xs, 90) - np.percentile(head_xs, 10))

                center_x = int(center_x_thumb / scale)
                neck_y = int(neck_y_thumb / scale)

                shoulder_w_est = int((head_w_thumb / scale) * 2.45)
                shoulder_w = max(int(w * 0.42), min(shoulder_w_est, int(w * 0.72)))

                return {
                    "center_x": max(int(w * 0.20), min(center_x, int(w * 0.80))),
                    "neck_y": max(int(h * 0.10), min(neck_y, int(h * 0.55))),
                    "shoulder_w": shoulder_w,
                    "metodo": "anatomico_piel",
                }
        except Exception as e:
            logger.debug(f"Anclaje automático por visión no concluyente: {e}")

        # Fallback proporcional si no hay rostro detectable
        return {
            "center_x": w // 2,
            "neck_y": int(h * 0.22),
            "shoulder_w": int(w * 0.52),
            "metodo": "fallback_proporcional",
        }

    @classmethod
    def _unused_componer_imagen_ar(
        cls,
        foto_base: Image.Image,
        prenda_img: Image.Image,
        placement: Optional[Dict[str, float]] = None,
        color_hex: Optional[str] = None,
        complexion: str = "MEDIA",
        categoria: str = "upper_body",
        demo_mode: bool = False,
    ) -> str:
        """
        Composición local fotorrealista de la prenda sobre la foto del usuario.

        Pipeline de calidad (modo estándar):
          1. Detección anatómica del torso (_detectar_anclaje_torso). Si falla
             la detección por piel, fallback proporcional AJUSTADO POR COMPLEXIÓN
             (no un 52% mágico igual para todos).
          2. Limpieza de fondo de catálogo (_remover_fondo_claro).
          3. Deformación 3D de la prenda (_deformar_prenda_al_torso).
          4. Mapeo de volumen y pliegues (_generar_sombreado_pliegues_3d).
          5. **Oclusión ambiental enriquecida**: sombra en V bajo el cuello y
             oclusión en sisas (debajo de los brazos) — los toques que evitan
             el efecto "pegatina plana" sobre el cuerpo.
          6. Fusión de iluminación de la foto real (env_light) sobre la prenda.
          7. **Feathering agresivo de bordes** (4-6 px) + segundo pase de blur
             exterior para fundir el contorno con la piel.
          8. Sombra de contacto bajo la prenda (desplazada 6 px) para anclar
             visualmente la prenda al cuerpo.

        Si demo_mode=True (recomendado para presentaciones en vivo):
          - Feathering sube a 6-8 px.
          - Armonización cromática sube de 6% a 10%.
          - Sombra de contacto más marcada (0.45 vs 0.35).
          - Oclusión en cuello y sisas más profunda.
        """
        w_foto, h_foto = foto_base.size
        resultado = foto_base.copy().convert("RGBA")

        # Parámetros ajustables según modo (demo vs estándar).
        if demo_mode:
            FEATHER_INNER = 4.0
            FEATHER_OUTER = 6.0
            COLOR_BLEND = 0.10
            SHADOW_ALPHA = 0.45
            NECK_OCCLUSION_ALPHA = 90
            ARMPIT_OCCLUSION_ALPHA = 70
        else:
            FEATHER_INNER = 2.5
            FEATHER_OUTER = 4.0
            COLOR_BLEND = 0.06
            SHADOW_ALPHA = 0.35
            NECK_OCCLUSION_ALPHA = 60
            ARMPIT_OCCLUSION_ALPHA = 45

        try:
            # 1. Detección anatómica del usuario en la foto real.
            anclaje = cls._unused_detectar_anclaje_torso(foto_base=foto_base, categoria=categoria)
            center_x = anclaje["center_x"]
            neck_y = anclaje["neck_y"]
            shoulder_w = anclaje["shoulder_w"]

            # Si la detección por piel falló y caímos al proporcional,
            # ajustamos el ancho de hombros por complexión. MODO CONSERVADOR:
            # usamos factores más chicos que antes (38-44% del ancho de la
            # foto) para que la prenda SIEMPRE entre cómoda en el torso.
            if anclaje.get("metodo") == "fallback_proporcional":
                factor = {"DELGADA": 0.38, "MEDIA": 0.42, "ROBUSTA": 0.46}.get(complexion, 0.42)
                shoulder_w = int(w_foto * factor)
                center_x = w_foto // 2
                # Subimos la línea de cuello a 32% del alto de la foto
                # (zona segura debajo de la cara, sobre los hombros).
                neck_y = int(h_foto * 0.32)
            else:
                # Detección por piel OK. Si el neck_y está muy arriba
                # (agarro la frente), lo bajamos al menos al 32%.
                neck_y = max(neck_y, int(h_foto * 0.32))

            cat_lower = (categoria or "").lower()
            es_inferior = any(k in cat_lower for k in ["pantalón", "pantalon", "jean", "short", "falda", "bermuda", "calzas", "legging"])
            es_vestido = any(k in cat_lower for k in ["vestido", "enterizo", "mono"])
            es_calzado = any(k in cat_lower for k in ["zapato", "calzado", "tenis", "zapatilla", "bota"])

            prenda_clean = cls._unused_remover_fondo_claro(prenda_img)
            aspect_ratio = max(0.5, min(prenda_clean.height / float(max(1, prenda_clean.width)), 3.0))

            # ----------------------------------------------------------------
            # GEOMETRÍA PROPORCIONAL AL TORSO REAL — CAPS CONSERVADORES.
            # Estos son los topes absolutos. Si la detección de piel dio
            # un shoulder_w gigante, NO lo respetamos: capeamos al máximo
            # permitido para que la prenda entre limpia en el pecho.
            # ----------------------------------------------------------------
            MAX_W_RATIO = 0.45          # <= 45% del ancho de la foto (topado)
            MIN_W_RATIO = 0.28          # >= 28% (nunca ridículamente chica)
            MAX_TORSO_RATIO = 0.50      # <= 50% del segmento cuello->cintura
            MAX_H_FOTO_RATIO = 0.40     # <= 40% del alto de la foto (regla dura)
            MIN_TOP_GAP = 0.02          # gap mínimo entre cuello y top de prenda

            # Anclaje de la cintura al 70% del alto (punto seguro para que la
            # prenda termine antes de la cadera).
            cintura_y = int(h_foto * 0.70)
            torso_len = max(1, cintura_y - neck_y)
            max_h_torso = int(torso_len * MAX_TORSO_RATIO)
            max_h_foto = int(h_foto * MAX_H_FOTO_RATIO)

            # ----------------------------------------------------------------
            # REGLA DE ORO: LA RELACIÓN DE ASPECTO ES SAGRADA.
            # La altura de la prenda SIEMPRE sale de ancho * aspect_ratio.
            # Los caps de tamaño se aplican al ANCHO. Si la altura resultante
            # no entra en la foto, se reduce el ancho proporcionalmente,
            # NUNCA se aplasta la altura de forma independiente.
            # ----------------------------------------------------------------
            # Helper inline: dado un ancho objetivo, devuelve (w, h) que
            # mantiene el aspect ratio Y verifica que h <= max_h permitido.
            # Si h excede el máximo, reduce w hasta que entre.
            def fit_size(desired_w: int, max_h: int) -> tuple[int, int]:
                if desired_w <= 0 or max_h <= 0:
                    return 1, 1
                h_natural = desired_w * aspect_ratio
                if h_natural <= max_h:
                    return desired_w, int(h_natural)
                # La altura natural excede el máximo: reducir el ancho.
                # target_w = max_h / aspect_ratio
                w_reducido = max_h / aspect_ratio
                if w_reducido < 1:
                    w_reducido = 1
                return int(w_reducido), max_h

            if es_calzado:
                target_w = int(w_foto * 0.35)
                target_w, target_h = fit_size(target_w, max_h_foto)
                pos_x = center_x - target_w // 2
                pos_y = int(h_foto * 0.78)
            elif es_inferior:
                target_w = int(min(shoulder_w * 0.85, w_foto * MAX_W_RATIO))
                target_w = max(target_w, int(w_foto * MIN_W_RATIO))
                target_w, target_h = fit_size(target_w, max_h_foto)
                pos_x = center_x - target_w // 2
                pos_y = cintura_y
            elif es_vestido:
                target_w = int(min(shoulder_w, w_foto * MAX_W_RATIO))
                target_w = max(target_w, int(w_foto * MIN_W_RATIO))
                target_w, target_h = fit_size(target_w, max_h_foto)
                pos_x = center_x - target_w // 2
                pos_y = neck_y + int(h_foto * MIN_TOP_GAP)
            else:
                # Torso superior (camisas, poleras, buzos, casacas, chaquetas).
                target_w = int(min(shoulder_w, w_foto * MAX_W_RATIO))
                target_w = max(target_w, int(w_foto * MIN_W_RATIO))
                # El cap vertical lo define el MENOR entre max_h_torso y
                # max_h_foto (el más restrictivo gana), pero la regla de
                # aspect ratio se mantiene: si la altura no entra,
                # se reduce el ancho, NO se aplasta la altura.
                cap_vertical = min(max_h_torso, max_h_foto)
                target_w, target_h = fit_size(target_w, cap_vertical)
                pos_x = center_x - target_w // 2
                # Top de la prenda: GAP MÍNIMO debajo del cuello, NUNCA arriba.
                pos_y = neck_y + int(h_foto * MIN_TOP_GAP)

            # ----------------------------------------------------------------
            # CANDADOS FINALES CONTRA CARA TAPADA Y PRENDA GIGANTE.
            # Regla crítica: cualquier cambio al ancho recalcula la altura
            # con aspect_ratio. NUNCA se aplasta la altura.
            # ----------------------------------------------------------------
            # Candado 1: el top de la prenda NUNCA puede estar más arriba del
            #           33% de la altura de la foto. Eso deja libre la zona
            #           de cara (que suele ocupar 0-30% en fotos verticales
            #           tipo mobile) con un margen saludable de 3%.
            MIN_POS_Y = int(h_foto * 0.33)
            if pos_y < MIN_POS_Y:
                pos_y = MIN_POS_Y

            # Candado 2: el ancho NUNCA excede MAX_W_RATIO (45%) del ancho
            # de la foto. Si la detección pidió más, topamos Y recalculamos
            # la altura con aspect_ratio (no se aplasta).
            if target_w > int(w_foto * MAX_W_RATIO):
                target_w = int(w_foto * MAX_W_RATIO)
                target_h = int(target_w * aspect_ratio)

            # Candado 3: si la altura calculada proporcionalmente todavía
            # excede el cap vertical, REDUCIMOS el ancho (no la altura).
            # Esto preserva la forma de la prenda: si la camisa es alta,
            # queda más angosta; nunca queda aplastada.
            if target_h > max_h_foto:
                target_w = int(max_h_foto / aspect_ratio)
                if target_w < 1:
                    target_w = 1
                target_h = max_h_foto

            # Candado 4: ancho mínimo para que la prenda no quede
            # ridículamente chica. PERO OJO: si la prenda es alta y la
            # foto es bajita (caso horizontal 1920x1080), el ancho
            # mínimo X aspect_ratio puede NO entrar en max_h_foto. En ese
            # caso patológico, el ancho mínimo EFECTIVO se reduce a
            # (max_h_foto / aspect_ratio) para que la altura quepa sin
            # aplastarse. La prenda queda más chica de lo deseable, pero
            # conserva su forma.
            min_w = int(w_foto * MIN_W_RATIO)
            if target_w < min_w:
                target_w = min_w
            # Verificación final: que la altura con el ancho actual
            # quepa en max_h_foto. Si no, reducir el ancho al techo
            # compatible (NO aplastar la altura).
            if int(target_w * aspect_ratio) > max_h_foto and aspect_ratio > 0:
                target_w = int(max_h_foto / aspect_ratio)
                if target_w < 1:
                    target_w = 1
            target_h = int(target_w * aspect_ratio)
            if target_h > max_h_foto:
                target_h = max_h_foto  # último recurso: clipamos por abajo

            pos_x = max(0, min(pos_x, w_foto - target_w))
            pos_y = max(0, min(pos_y, h_foto - target_h))

            # 2. Deformación anatómica 3D (caída de hombros y curvatura de pecho).
            # ATENCIÓN: _deformar_prenda_al_torso AGREGA un extra_h = 14% por
            # ENCIMA de la prenda para acomodar la caída de hombros. Eso significa
            # que ph puede ser mayor que target_h y que la parte de arriba de la
            # prenda deformada puede quedar ARRIBA de pos_y si no compensamos.
            prenda_deformed = cls._unused_deformar_prenda_al_torso(
                prenda=prenda_clean,
                target_w=target_w,
                target_h=target_h,
                categoria=categoria,
            )
            pw, ph = prenda_deformed.size

            # Compensar la expansión vertical. Si la deformación agregó
            # espacio por arriba (ph > target_h), subimos pos_y en esa
            # cantidad. Después volvemos a aplicar el candado principal
            # (MIN_POS_Y = 30% del alto de la foto) para garantizar que
            # NADA de la prenda tape la cara.
            expansion = max(0, ph - target_h)
            pos_y_compensada = pos_y + expansion
            if pos_y_compensada < MIN_POS_Y:
                pos_y_compensada = MIN_POS_Y
            # Si la prenda no entra entera después de compensar, alineamos
            # por la parte de ABAJO (mantiene el top en MIN_POS_Y).
            if pos_y_compensada + ph > h_foto:
                pos_y_compensada = max(MIN_POS_Y, h_foto - ph)

            pos_x = max(0, min(pos_x, w_foto - pw))
            pos_y = pos_y_compensada

            # 3. Mapeo de volumen 3D y pliegues de tensión orgánica.
            shading_3d = cls._unused_generar_sombreado_pliegues_3d(w=pw, h=ph, categoria=categoria)

            # 4. Captura de iluminación de la foto real y fusión.
            user_crop = foto_base.crop((pos_x, pos_y, pos_x + pw, pos_y + ph)).convert("RGB")
            user_lum = user_crop.convert("L").filter(ImageFilter.GaussianBlur(16.0))
            env_light = user_lum.point(lambda p: int(185 + (p / 255.0) * 70))
            luz_combinada = ImageChops.multiply(shading_3d, env_light)

            r, g, b, a = prenda_deformed.split()
            r_s = ImageChops.multiply(r, luz_combinada)
            g_s = ImageChops.multiply(g, luz_combinada)
            b_s = ImageChops.multiply(b, luz_combinada)

            # Armonización cromática con la temperatura de la foto.
            garment_rgb = Image.merge("RGB", (r_s, g_s, b_s))
            env_tint = user_crop.filter(ImageFilter.GaussianBlur(28.0))
            garment_harmonized = Image.blend(garment_rgb, env_tint, COLOR_BLEND)

            # 5. Oclusión ambiental enriquecida (NUEVO).
            #    Aplicamos dos oclusiones suaves ANTES de feather:
            #    a) Sombra en V bajo el cuello (la prenda se mete debajo de la
            #       mandíbula; sin esto parece flotar).
            #    b) Oclusión en sisas (la prenda se mete debajo de los brazos).
            cat_lower = (categoria or "").lower()
            if any(k in cat_lower for k in ["vestido", "enterizo", "mono"]) or not es_inferior and not es_calzado:
                occlusion = Image.new("L", (pw, ph), 0)
                od = ImageDraw.Draw(occlusion)

                # Sombra en V bajo el cuello (centro superior, V apuntando abajo).
                v_w = int(pw * 0.35)
                v_top = int(ph * 0.04)
                v_bottom = int(ph * 0.20)
                v_left = pw // 2 - v_w // 2
                v_right = pw // 2 + v_w // 2
                od.polygon(
                    [(pw // 2, v_top), (v_left, v_bottom), (v_right, v_bottom)],
                    fill=NECK_OCCLUSION_ALPHA,
                )

                # Oclusión en sisas (dos elipses a izquierda y derecha del torso
                # superior, cerca de los hombros).
                s_w = int(pw * 0.10)
                s_h = int(ph * 0.18)
                s_y = int(ph * 0.14)
                od.ellipse(
                    [int(pw * 0.06), s_y, int(pw * 0.06) + s_w, s_y + s_h],
                    fill=ARMPIT_OCCLUSION_ALPHA,
                )
                od.ellipse(
                    [int(pw * 0.94) - s_w, s_y, int(pw * 0.94), s_y + s_h],
                    fill=ARMPIT_OCCLUSION_ALPHA,
                )

                occlusion = occlusion.filter(ImageFilter.GaussianBlur(8.0))
                # Restamos esta máscara del alpha de la prenda donde la prenda
                # es opaca, para que las oclusiones "coman" un poco la prenda
                # en cuello y sisas, simulando que está metida bajo el cuerpo.
                a_occluded = ImageChops.subtract(a, occlusion, scale=1, offset=0)

                # 6. Feathering agresivo de bordes en dos pasadas.
                a_inner = a_occluded.filter(ImageFilter.GaussianBlur(FEATHER_INNER))
                a_outer = a_occluded.filter(ImageFilter.GaussianBlur(FEATHER_OUTER))
                # Mezclamos: la interior mantiene el cuerpo, la exterior
                # suaviza el contorno para fundirlo con la piel.
                a_final = ImageChops.lighter(a_inner, a_outer)
            else:
                # Para prendas inferiores / calzado: feathering estándar.
                a_inner = a.filter(ImageFilter.GaussianBlur(FEATHER_INNER))
                a_outer = a.filter(ImageFilter.GaussianBlur(FEATHER_OUTER))
                a_final = ImageChops.lighter(a_inner, a_outer)

            r_h, g_h, b_h = garment_harmonized.split()
            prenda_final = Image.merge("RGBA", (r_h, g_h, b_h, a_final))

            # 7. Sombra de contacto bajo la prenda.
            shadow_mask = a_final.filter(ImageFilter.GaussianBlur(6.0))
            shadow_layer = Image.new("RGBA", (pw, ph), (15, 15, 20, 0))
            shadow_layer.putalpha(shadow_mask.point(lambda p: int(p * SHADOW_ALPHA)))
            resultado.paste(
                shadow_layer,
                (pos_x, min(h_foto - ph, pos_y + 6)),
                shadow_layer,
            )

            # 8. Composición final.
            resultado.paste(prenda_final, (pos_x, pos_y), prenda_final)

        except Exception as e:
            logger.warning(
                f"Fallo en deformación e inpainting 3D de prenda: {type(e).__name__}: {e}"
            )

        buf = io.BytesIO()
        resultado.convert("RGB").save(buf, format="JPEG", quality=92, optimize=True)
        b64_str = base64.b64encode(buf.getvalue()).decode("utf-8")
        return f"data:image/jpeg;base64,{b64_str}"

    @classmethod
    def procesar_simulacion_ar(
        cls,
        foto_usuario_data_url: str,
        producto_nombre: str,
        producto_categoria: str,
        talla_elegida: str,
        tallas_disponibles: list[str],
        color_nombre: Optional[str] = None,
        color_hex: Optional[str] = None,
        prenda_imagen_url: Optional[str] = None,
        complexion: str = "MEDIA",
        estatura_cm: Optional[int] = None,
        peso_kg: Optional[int] = None,
    ) -> Dict[str, Any]:
        """
        Orquesta el Vestidor Virtual.

        Modo MOCK (default desde Turno 8, presentación del proyecto):
          - NO llama a la API de Google GenAI.
          - Devuelve la imagen estática del catálogo en
            1p_si2_frontend/src/public/ (o TRYON_MOCK_DIR).
          - Búsqueda por nombre del producto normalizado
            (lowercase, espacios -> _, sin acentos).
          - Si no hay match, usa el primer mock disponible como fallback.
          - Simula 1-2s de latencia para que la UI muestre el spinner
            "Optimizando postura y adaptando tejido...".
          - gemini_activo=False, motor="Catalogo estatico (Mock Mode)".

        Modo IA real (TRYON_MOCK_MODE=False en .env):
          - Llama a client.models.generate_content con la foto del usuario
            y la prenda del catálogo.
          - Si la API falla, la excepción se PROPAGA (sin fallback).
        """
        # ----------------------------------------------------------------
        # 1) Decidir modo: MOCK (default) vs IA real.
        # ----------------------------------------------------------------
        try:
            settings = get_settings()
            mock_mode = bool(getattr(settings, "TRYON_MOCK_MODE", True))
        except Exception:
            mock_mode = True

        # ----------------------------------------------------------------
        # 2) Reglas deterministas de ajuste y recomendación de talla
        #    (funcionan idénticas en MOCK y en IA real).
        # ----------------------------------------------------------------
        talla_recomendada = cls._recomendar_talla_regla(complexion, tallas_disponibles)
        ajuste_estimado = cls._estimar_ajuste_regla(talla_elegida, talla_recomendada)

        # ----------------------------------------------------------------
        # 3) FLUJO MOCK: devuelve la imagen estática del catálogo.
        # ----------------------------------------------------------------
        if mock_mode:
            logger.info(
                f"[PROBADOR] MOCK MODE activo para producto='{producto_nombre}' "
                f"cat='{producto_categoria}' complexion='{complexion}'."
            )
            try:
                img_bytes, mime = cls._generar_prueba_virtual_mock(
                    producto_nombre=producto_nombre,
                    producto_categoria=producto_categoria,
                    color_nombre=color_nombre,
                    talla_elegida=talla_elegida,
                    complexion=complexion,
                )
            except Exception as mock_exc:
                # Si el mock falla (ej. no hay carpeta o no hay imagen),
                # propagamos el error con contexto claro.
                logger.error(
                    f"[PROBADOR] Mock Mode fallo: {type(mock_exc).__name__}: {mock_exc}"
                )
                raise

            b64_str = base64.b64encode(img_bytes).decode("utf-8")
            # Forzar MIME image/jpeg en el data URL para consistencia con
            # el contrato del frontend. Si la imagen es PNG/WebP, igual
            # el navegador la renderiza porque respeta el mime del archivo.
            data_url = f"data:{mime};base64,{b64_str}"
            return {
                "resultado_imagen_url": data_url,
                "ajuste_estimado": ajuste_estimado,
                "talla_recomendada": talla_recomendada,
                "comentario_estilo": (
                    f"Prenda '{producto_nombre}' adaptada con IA fotorrealista "
                    f"(modo demo / catálogo estático). "
                    f"Talla recomendada: {talla_recomendada} con ajuste estimado {ajuste_estimado.lower()}."
                ),
                "gemini_activo": False,
                "motor": "Catalogo estatico (Mock Mode)",
                "demo_mode": True,
            }

        # ----------------------------------------------------------------
        # 4) FLUJO IA REAL: client.models.generate_content multimodal.
        #    (Solo si TRYON_MOCK_MODE=False en .env)
        # ----------------------------------------------------------------
        foto_bytes: bytes = b""
        foto_pil: Optional[Image.Image] = None
        prenda_pil: Optional[Image.Image] = None

        try:
            comma_idx = foto_usuario_data_url.find(",")
            if comma_idx != -1:
                foto_bytes = base64.b64decode(foto_usuario_data_url[comma_idx + 1 :])
            else:
                foto_bytes = base64.b64decode(foto_usuario_data_url)
        except Exception as e:
            logger.error(f"Error al decodificar foto_usuario_data_url: {e}")
            foto_bytes = b""

        try:
            if foto_bytes:
                try:
                    foto_pil = Image.open(io.BytesIO(foto_bytes)).convert("RGB")
                    if max(foto_pil.width, foto_pil.height) > 1280:
                        foto_pil.thumbnail((1280, 1280), Image.Resampling.LANCZOS)
                    buf_f = io.BytesIO()
                    foto_pil.save(buf_f, format="JPEG", quality=92, optimize=True)
                    foto_bytes = buf_f.getvalue()
                except Exception as e:
                    logger.error(f"Error optimizando imagen de usuario: {e}")

            prenda_bytes, prenda_mime, prenda_pil = cls._descargar_y_procesar_prenda(
                url=prenda_imagen_url,
                color_hex=color_hex,
                categoria=producto_categoria,
            )

            if not (foto_bytes and prenda_bytes):
                raise RuntimeError(
                    "Faltan bytes de foto de usuario o de prenda para invocar la IA."
                )

            jpeg_bytes, motor_nombre = cls._generar_prueba_virtual_gemini(
                foto_bytes=foto_bytes,
                prenda_bytes=prenda_bytes,
                prenda_mime=prenda_mime,
                producto_nombre=producto_nombre,
                producto_categoria=producto_categoria,
                color_nombre=color_nombre,
                talla_elegida=talla_elegida,
                complexion=complexion,
                estatura_cm=estatura_cm,
                peso_kg=peso_kg,
                foto_pil=foto_pil,
            )
            if not jpeg_bytes:
                raise RuntimeError(
                    "Google GenAI devolvió una respuesta vacía. "
                    "Ver logs anteriores para ver el error exacto."
                )

            b64_str = base64.b64encode(jpeg_bytes).decode("utf-8")
            resultado_imagen_url = f"data:image/jpeg;base64,{b64_str}"
            return {
                "resultado_imagen_url": resultado_imagen_url,
                "ajuste_estimado": ajuste_estimado,
                "talla_recomendada": talla_recomendada,
                "comentario_estilo": (
                    f"Prenda '{producto_nombre}' adaptada con IA fotorrealista ({motor_nombre}). "
                    f"Talla recomendada: {talla_recomendada} con ajuste estimado {ajuste_estimado.lower()}."
                ),
                "gemini_activo": True,
                "motor": motor_nombre,
            }

        finally:
            # Privacidad Absoluta: Destrucción inmediata de buffers biométricos de memoria
            if foto_pil:
                try:
                    foto_pil.close()
                except Exception:
                    pass
            if prenda_pil:
                try:
                    prenda_pil.close()
                except Exception:
                    pass
            del foto_bytes
            foto_bytes = b""