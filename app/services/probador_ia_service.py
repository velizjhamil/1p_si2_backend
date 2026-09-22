"""Servicio de Inteligencia Artificial para el Probador Virtual con Google Gemini."""
from __future__ import annotations

import base64
import io
import json
import logging
import math
import ssl
import time
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

        # Caso 3: Fallback si no hay imagen válida o no se pudo descargar
        if pil_img is None or raw_bytes is None:
            pil_img = cls._generar_prenda_fallback(color_hex, categoria)
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
    def _remover_fondo_claro(cls, img: Image.Image) -> Image.Image:
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
    def _generar_prenda_fallback(cls, color_hex: Optional[str], categoria: str) -> Image.Image:
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
    def _construir_prompt_tryon(
        producto_nombre: str,
        producto_categoria: str,
        color_nombre: Optional[str],
        talla_elegida: str,
        complexion: str,
        estatura_cm: Optional[int],
        peso_kg: Optional[int],
    ) -> str:
        cat_lower = (producto_categoria or "").lower()
        if any(k in cat_lower for k in ["zapato", "calzado", "tenis", "zapatilla", "bota"]):
            zona = "feet"
        elif any(k in cat_lower for k in ["vestido", "enterizo", "mono"]):
            zona = "full torso and legs"
        elif any(k in cat_lower for k in ["pantalón", "pantalon", "jean", "short", "falda", "bermuda", "calzas", "legging"]):
            zona = "lower body (waist, hips and legs)"
        else:
            zona = "upper body (shoulders, chest, torso and arms)"

        return f"""You are a state-of-the-art virtual try-on engine. You perform garment transfer: you re-dress a real person in a new garment and output a photorealistic photograph.

INPUTS
- Image 1: the PERSON (full-body photo). This is the base photo to edit.
- Image 2: the GARMENT reference ({producto_nombre}, category: {producto_categoria}, color: {color_nombre or 'as shown'}). Use it ONLY as a reference for the garment's design, color, fabric and details.

TASK
Re-render Image 1 so that the person is physically wearing the garment from Image 2. The output must look like a real photo of that person dressed in that garment. It must NOT look like a collage, cutout, sticker or overlay.

MANDATORY PROCESS
1. Identify the target area on the person: {zona}.
2. ERASE the clothing currently worn in that area (straps, hems, sleeves, necklines, bare skin belonging to old clothing) and reconstruct the body underneath naturally.
3. Re-draw the garment from Image 2 as a 3D object worn on that body: it wraps around shoulders, chest, ribcage and arms following the person's exact pose and proportions.
4. Simulate real fabric behavior: gravity, tension folds at shoulders and armpits, wrinkles at elbows and waist, natural volume and sleeve drape, collar sitting around the neck, hem falling over the waist or hips. If the garment is longer than the target area, it hangs or tucks in realistically over the existing outfit.
5. Handle occlusion correctly: hands, arms, hair and other clothing pass in front of or behind the fabric exactly as in the original pose.
6. Match the scene: same light direction, color temperature, softness, grain and sharpness as Image 1. Add contact shadows and ambient occlusion where fabric meets skin and other clothes.
7. Fit reference: size {talla_elegida}, body type {complexion}, height {estatura_cm or 'unknown'} cm, weight {peso_kg or 'unknown'} kg. Reflect how loose or snug that size would really fit this body.

GARMENT FIDELITY
- Preserve the exact design, color, fabric texture, collar, buttons, seams, stitching, prints and logos of Image 2. Do not invent, remove or restyle anything.
- Ignore the background, hanger, mannequin, model or flat-lay presentation of Image 2. Take only the garment.

PRESERVE EXACTLY
- The person's identity: face, expression, hair, skin tone, body shape, proportions, pose and hands.
- All clothing, accessories and shoes outside the target area.
- Background, camera angle, framing, aspect ratio and overall composition.

FORBIDDEN
- Flat overlay, pasted cutout, floating garment, rectangular patches, visible halos or hard edges.
- The old clothing remaining visible, ghosted or blended with the new garment.
- Wrong scale (garment larger or smaller than the body warrants), misaligned collar or sleeves.
- Changing the face or identity, extra or missing limbs, added text, watermarks or objects.

OUTPUT
Return ONLY the final edited photograph, photorealistic, sharp, high resolution. No collage, no side-by-side, no explanation."""

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
        cls._ultimo_error_gemini = None
        client = cls._obtener_cliente_nano_banana() or IAService._obtener_cliente_gemini()
        if not client:
            msg = "Cliente Google GenAI no inicializado (clave no configurada)."
            logger.info(msg)
            cls._ultimo_error_gemini = msg
            return None

        settings = get_settings()
        # Selección exclusiva de modelos estables y rápidos (evitando versiones preview que causan bloqueos)
        candidatos: list[str] = []
        if settings.NANO_BANANA_MODEL and settings.NANO_BANANA_MODEL.strip():
            m = settings.NANO_BANANA_MODEL.strip()
            if "preview" not in m.lower():
                candidatos.append(m)
        for fallback in ["gemini-2.5-flash", "gemini-2.0-flash"]:
            if fallback not in candidatos:
                candidatos.append(fallback)

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
            from google.genai import types

            image_config = None
            if foto_pil and foto_pil.width and foto_pil.height:
                try:
                    w, h = foto_pil.width, foto_pil.height
                    ratios = [
                        ("1:1", 1.0),
                        ("3:4", 0.75),
                        ("4:3", 4.0 / 3.0),
                        ("9:16", 9.0 / 16.0),
                        ("16:9", 16.0 / 9.0),
                    ]
                    best_ratio = min(ratios, key=lambda x: abs((w / h) - x[1]))[0]
                    if hasattr(types, "ImageConfig"):
                        image_config = types.ImageConfig(aspect_ratio=best_ratio)
                except Exception as e:
                    logger.debug(f"Aspect ratio config omitido: {e}")

            # Configuración de timeout estricto para evitar bloqueos del servidor
            http_options = None
            if hasattr(types, "HttpOptions") and hasattr(types, "HttpRetryOptions"):
                try:
                    http_options = types.HttpOptions(
                        timeout=8.0,
                        retry_options=types.HttpRetryOptions(attempts=1),
                    )
                except Exception as e:
                    logger.debug(f"HttpOptions omitido: {e}")

            config_kwargs: Dict[str, Any] = {"response_modalities": ["TEXT", "IMAGE"]}
            if image_config is not None:
                config_kwargs["image_config"] = image_config
            if http_options is not None:
                config_kwargs["http_options"] = http_options
            config = types.GenerateContentConfig(**config_kwargs)

            contents = [
                "Image 1 (PERSON to be dressed):",
                types.Part.from_bytes(data=foto_bytes, mime_type="image/jpeg"),
                "Image 2 (GARMENT to wear):",
                types.Part.from_bytes(data=prenda_bytes, mime_type=prenda_mime),
                prompt,
            ]

            start_total = time.time()
            TOTAL_TIMEOUT = 10.0  # Límite rápido de 10s para responder fluidamente

            for modelo in candidatos:
                if (time.time() - start_total) >= TOTAL_TIMEOUT:
                    logger.info("Tiempo de respuesta de IA agotado, pasando a composición gráfica local.")
                    break

                t0 = time.time()
                try:
                    logger.info(f"Probando simulación con modelo IA {modelo}...")
                    res = client.models.generate_content(
                        model=modelo,
                        contents=contents,
                        config=config,
                    )
                    latency = time.time() - t0

                    if res and res.candidates and res.candidates[0].content and res.candidates[0].content.parts:
                        for part in res.candidates[0].content.parts:
                            inline_data = getattr(part, "inline_data", None)
                            if inline_data and getattr(inline_data, "data", None):
                                img_data = inline_data.data
                                val_pil = Image.open(io.BytesIO(img_data))
                                val_pil.verify()
                                pil_res = Image.open(io.BytesIO(img_data)).convert("RGB")
                                buf_out = io.BytesIO()
                                pil_res.save(buf_out, format="JPEG", quality=92, optimize=True)
                                jpeg_bytes = buf_out.getvalue()
                                logger.info(f"Generación exitosa con {modelo} en {latency:.2f}s")
                                return (jpeg_bytes, modelo)

                    # Si el modelo respondió solo con texto o sin candidates, no se bloquea
                    logger.info(f"Modelo {modelo} no incluyó imagen en la respuesta.")
                except Exception as inner_exc:
                    latency = time.time() - t0
                    logger.warning(
                        f"Llamada a modelo {modelo} no completada ({latency:.2f}s): {inner_exc}. "
                        "Probando siguiente alternativa..."
                    )
                    cls._ultimo_error_gemini = str(inner_exc)
                    continue

        except Exception as exc:
            logger.warning(f"Error general en comunicación con Gemini: {exc}")
            cls._ultimo_error_gemini = str(exc)

        return None

    @classmethod
    def _componer_imagen_ar(
        cls,
        foto_base: Image.Image,
        prenda_img: Image.Image,
        placement: Dict[str, float],
        color_hex: Optional[str] = None,
        complexion: str = "MEDIA",
    ) -> str:
        w_foto, h_foto = foto_base.size
        resultado = foto_base.copy().convert("RGBA")

        try:
            top_ratio = float(placement.get("top", 0.20))
            height_ratio = float(placement.get("height", 0.48))
            width_ratio = float(placement.get("width", 0.54))
            center_x_ratio = float(placement.get("center_x", 0.50))

            target_w = max(80, int(w_foto * width_ratio))
            target_h = max(80, int(h_foto * height_ratio))

            prenda_clean = cls._remover_fondo_claro(prenda_img)
            prenda_scaled = prenda_clean.resize((target_w, target_h), Image.Resampling.LANCZOS)

            # Suavizar los bordes exteriores de la prenda para eliminar efecto de cuadro plano
            alpha = prenda_scaled.split()[3]
            alpha = alpha.filter(ImageFilter.GaussianBlur(2.5))
            prenda_scaled.putalpha(alpha)

            pos_x = int(w_foto * center_x_ratio - target_w / 2)
            pos_y = int(h_foto * top_ratio)
            pos_x = max(0, min(pos_x, w_foto - target_w))
            pos_y = max(0, min(pos_y, h_foto - target_h))

            resultado.paste(prenda_scaled, (pos_x, pos_y), prenda_scaled)

        except Exception as e:
            logger.warning(f"Fallo en inpainting de prenda: {e}")

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
        foto_bytes: bytes = b""
        try:
            comma_idx = foto_usuario_data_url.find(",")
            if comma_idx != -1:
                foto_bytes = base64.b64decode(foto_usuario_data_url[comma_idx + 1 :])
            else:
                foto_bytes = base64.b64decode(foto_usuario_data_url)
        except Exception as e:
            logger.error(f"Error al decodificar foto_usuario_data_url: {e}")
            foto_bytes = b""

        foto_pil: Optional[Image.Image] = None
        if foto_bytes:
            try:
                foto_pil = Image.open(io.BytesIO(foto_bytes)).convert("RGB")
                if max(foto_pil.width, foto_pil.height) > 1280:
                    foto_pil.thumbnail((1280, 1280), Image.Resampling.LANCZOS)
                buf_f = io.BytesIO()
                foto_pil.save(buf_f, format="JPEG", quality=92, optimize=True)
                foto_bytes = buf_f.getvalue()
            except Exception as e:
                logger.error(f"Error abriendo/optimizando imagen de usuario con Pillow: {e}")

        prenda_bytes, prenda_mime, prenda_pil = cls._descargar_y_procesar_prenda(
            url=prenda_imagen_url,
            color_hex=color_hex,
            categoria=producto_categoria,
        )

        # Reglas de talla y estimación de ajuste corporal deterministas
        talla_recomendada = cls._recomendar_talla_regla(complexion, tallas_disponibles)
        ajuste_estimado = cls._estimar_ajuste_regla(talla_elegida, talla_recomendada)

        # 1. Intento de generación con IA de forma segura (try-except)
        generacion_gemini = None
        if foto_bytes and prenda_bytes:
            try:
                generacion_gemini = cls._generar_prueba_virtual_gemini(
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
            except Exception as ia_exc:
                logger.warning(
                    f"Fallo no crítico en motor Gemini: {ia_exc}. "
                    "Activando fallback inmediato con Pillow."
                )
                generacion_gemini = None

        # 2. Si Gemini generó la imagen, la usamos; si no, aplicamos Pillow garantizando siempre 200 OK
        if generacion_gemini:
            jpeg_bytes, modelo_usado = generacion_gemini
            b64_str = base64.b64encode(jpeg_bytes).decode("utf-8")
            resultado_imagen_url = f"data:image/jpeg;base64,{b64_str}"
            gemini_activo = True
            motor = f"Google Gemini ({modelo_usado})"
            comentario_estilo = (
                f"Prenda '{producto_nombre}' adaptada con IA ({modelo_usado}). "
                f"Talla recomendada: {talla_recomendada} con ajuste estimado {ajuste_estimado.lower()}."
            )
        else:
            # Fallback robusto y fluido: motor gráfico local Pillow sin romper el servidor
            resultado_imagen_url = foto_usuario_data_url
            ancho_proporcion = 0.48 if complexion == "DELGADA" else (0.58 if complexion == "ROBUSTA" else 0.52)
            placement = {
                "top": 0.20,
                "height": 0.48,
                "width": ancho_proporcion,
                "center_x": 0.50,
            }
            if foto_pil and prenda_pil:
                try:
                    resultado_imagen_url = cls._componer_imagen_ar(
                        foto_base=foto_pil,
                        prenda_img=prenda_pil,
                        placement=placement,
                        color_hex=color_hex,
                        complexion=complexion,
                    )
                except Exception as comp_err:
                    logger.warning(f"Error en composición Pillow: {comp_err}. Se mantiene imagen base.")

            gemini_activo = False
            motor = "Composición local (Pillow)"
            comentario_estilo = (
                f"Simulación visual generada con motor gráfico local. "
                f"Talla {talla_recomendada} recomendada para complexión {complexion}."
            )

        return {
            "resultado_imagen_url": resultado_imagen_url,
            "ajuste_estimado": ajuste_estimado,
            "talla_recomendada": talla_recomendada,
            "comentario_estilo": comentario_estilo,
            "gemini_activo": gemini_activo,
            "motor": motor,
        }