# backend/app/services/ia_service.py
"""Servicio de Inteligencia Artificial y Recomendaciones con Google Gemini.

Implementa un asistente de compras y moda con conexión a tierra (Grounding / RAG)
directamente sobre las tablas de la base de datos (productos, categorías, tallas,
colores, sucursales y promociones activas).
"""
import json
import logging
import re
from datetime import date
from typing import Any, Dict, List, Optional

from sqlalchemy import or_
from sqlalchemy.orm import Session, joinedload

from app.core.config import get_settings
from app.modules.descuentos.models import Descuento
from app.modules.empresa.models import Sucursal
from app.modules.inventario.models import Categoria, Color, Producto, Talla
from app.schemas.ia import (
    ChatMessage,
    ChatRequest,
    ChatResponseData,
    ColorResumen,
    ProductoResumenIA,
)

logger = logging.getLogger("attention.ia_service")


class IAService:
    """Orquestador de consultas inteligentes y generación con Google Gemini."""

    @staticmethod
    def _obtener_cliente_gemini():
        """Inicializa el cliente oficial de Google GenAI si la API key está disponible."""
        settings = get_settings()
        api_key = settings.GEMINI_API_KEY
        if not api_key:
            logger.warning("GEMINI_API_KEY no configurada en las variables de entorno.")
            return None

        try:
            from google import genai

            return genai.Client(api_key=api_key)
        except Exception as exc:
            logger.error(f"Error al inicializar el cliente Google GenAI: {exc}")
            return None

    @classmethod
    def _recuperar_contexto_db(
        cls,
        db: Session,
        consulta: str,
        id_sucursal: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Extrae datos reales y relevantes de la base de datos para grounding."""
        # 1. Categorías activas
        categorias = (
            db.query(Categoria)
            .filter(Categoria.activo.is_(True))
            .order_by(Categoria.nombre)
            .all()
        )
        categorias_info = [
            f"- [{c.id_categoria}] {c.nombre} (Línea: {c.linea}) - {c.descripcion or ''}".strip()
            for c in categorias
        ]

        # 2. Sucursales activas
        query_sucursales = (
            db.query(Sucursal)
            .options(joinedload(Sucursal.ciudad))
            .filter(Sucursal.is_active.is_(True))
        )
        if id_sucursal:
            query_sucursales = query_sucursales.filter(
                Sucursal.codigo_sucursal == id_sucursal
            )
        sucursales = query_sucursales.all()
        sucursales_info = [
            f"- {s.nombre} (Ciudad: {s.ciudad.nombre if s.ciudad else 'N/A'}): {s.direccion or 'Sin dirección'}. Horario: {s.horario_atencion or 'No especificado'}. Tel: {s.telefono or 'N/A'}"
            for s in sucursales
        ]

        # 3. Promociones y Descuentos vigentes (CU12)
        hoy = date.today()
        promos = (
            db.query(Descuento)
            .filter(
                Descuento.activo.is_(True),
                Descuento.fecha_inicio <= hoy,
                or_(Descuento.fecha_fin.is_(None), Descuento.fecha_fin >= hoy),
            )
            .all()
        )
        promos_info = []
        for p in promos:
            beneficio = (
                f"{p.valor}% OFF" if p.tipo == "PORCENTAJE" else f"Bs {p.valor:.2f} OFF"
            )
            cupon = f"Cupón: '{p.codigo}'" if p.codigo else "Descuento automático"
            minimo = (
                f" (Compra mínima: Bs {p.monto_minimo_compra:.2f})"
                if p.monto_minimo_compra
                else ""
            )
            promos_info.append(
                f"- {p.nombre}: {beneficio} ({cupon}){minimo}. {p.descripcion or ''}"
            )

        # 4. Búsqueda de Productos Relevantes en Catálogo (CU6, CU7)
        # Extraemos palabras clave significativas de la consulta del usuario
        palabras = [
            p.lower()
            for p in re.findall(r"\b[a-zA-ZáéíóúÁÉÍÓÚñÑ]{3,}\b", consulta)
            if p.lower()
            not in {"que", "las", "los", "con", "para", "una", "uno", "por", "del", "tienen"}
        ]

        query_prods = (
            db.query(Producto)
            .options(
                joinedload(Producto.categoria),
                joinedload(Producto.tallas),
                joinedload(Producto.colores),
            )
            .filter(Producto.estado == "Activo")
        )

        filtros_or = []
        for palabra in palabras[:5]:  # Máximo 5 términos clave
            filtros_or.append(Producto.nombre.ilike(f"%{palabra}%"))
            filtros_or.append(Producto.descripcion.ilike(f"%{palabra}%"))

        productos_coincidentes = []
        if filtros_or:
            productos_coincidentes = (
                query_prods.filter(or_(*filtros_or)).limit(15).all()
            )

        # Complementamos con productos destacados si la búsqueda es muy general
        ids_coincidentes = {p.id_producto for p in productos_coincidentes}
        productos_generales = (
            query_prods.filter(~Producto.id_producto.in_(ids_coincidentes))
            .limit(10)
            .all()
        )

        productos_totales = productos_coincidentes + productos_generales

        productos_info = []
        for p in productos_totales[:20]:
            tallas_str = ", ".join([t.nombre_talla for t in p.tallas]) or "Estándar"
            colores_str = ", ".join([c.nombre_color for c in p.colores]) or "Único"
            cat_str = p.categoria.nombre if p.categoria else "General"
            productos_info.append(
                f"- [ID:{p.id_producto}] '{p.nombre}' | Cat: {cat_str} | Precio: Bs {float(p.precio_venta):.2f} | Tallas: {tallas_str} | Colores: {colores_str} | Stock: {p.stock_total} | Desc: {p.descripcion or ''}"
            )

        return {
            "categorias": categorias_info,
            "sucursales": sucursales_info,
            "promociones": promos_info,
            "productos": productos_info,
            "productos_candidatos": productos_totales,
        }

    @classmethod
    def _construir_system_instruction(cls, contexto: Dict[str, Any]) -> str:
        """Construye el prompt de sistema para el rol de asesora de moda Attention."""
        categorias_txt = "\n".join(contexto["categorias"]) or "No registradas"
        sucursales_txt = "\n".join(contexto["sucursales"]) or "No registradas"
        promos_txt = "\n".join(contexto["promociones"]) or "Sin promociones activas"
        productos_txt = "\n".join(contexto["productos"]) or "Sin prendas disponibles"

        return f"""
Eres "Attention AI", la asesora virtual experta en moda, tendencias y atención al cliente de la prestigiosa tienda de ropa Attention (Bolivia).

OBJETIVO:
Asesorar de forma cálida, profesional y persuasiva a los clientes respondiendo preguntas sobre prendas, tallas, colores, precios (en Bolivianos 'Bs'), promociones vigentes, probador virtual y sucursales físicas.

DIRECTIVAS ESTRICTAS DE RESPUESTA:
1. INFORMACIÓN FIDEDIGNA: Solo recomienda prendas que figuren en la sección [CATÁLOGO DE PRENDAS DISPONIBLES]. NUNCA inventes productos, precios ni tallas que no existan en la lista.
2. CITACIÓN DE PRODUCTOS: Cada vez que recomiendes una prenda en tu texto, debes incluir su ID numérico en la lista "productos_recomendados".
3. INFORMACIÓN CORPORATIVA: Si preguntan por horarios, direcciones o teléfonos, utiliza con precisión la sección [SUCURSALES FÍSICAS].
4. OFERTAS: Si preguntan por descuentos o hay promociones aplicables, informa los códigos o beneficios de [PROMOCIONES ACTIVAS].
5. TONO: Amigable, elegante, conciso y en español neutro latinoamericano. Puedes usar formato Markdown (negrita, viñetas).
6. FORMATO DE SALIDA ESTRICTO: Tu respuesta DEBE SER UN OBJETO JSON VÁLIDO con la siguiente estructura exacta:
{{
  "respuesta": "Texto en Markdown para el cliente con la explicación y asesoría.",
  "productos_recomendados": [1, 2],
  "sugerencias": ["¿En qué colores viene la primera prenda?", "¿Tienen probador virtual?", "¿Cuál es el horario de la sucursal central?"]
}}

=== DATOS EN TIEMPO REAL DE LA TIENDA ATTENTION ===

[CATEGORÍAS DE PRENDAS]
{categorias_txt}

[SUCURSALES FÍSICAS]
{sucursales_txt}

[PROMOCIONES ACTIVAS]
{promos_txt}

[CATÁLOGO DE PRENDAS DISPONIBLES]
{productos_txt}
"""

    @classmethod
    def _enriquecer_productos(
        cls, db: Session, product_ids: List[int]
    ) -> List[ProductoResumenIA]:
        """Carga el detalle visual completo de los productos recomendados para las tarjetas móviles."""
        if not product_ids:
            return []

        # Asegurar unicidad conservando orden
        ids_unicos = []
        for pid in product_ids:
            if isinstance(pid, int) and pid not in ids_unicos:
                ids_unicos.append(pid)

        if not ids_unicos:
            return []

        productos = (
            db.query(Producto)
            .options(
                joinedload(Producto.categoria),
                joinedload(Producto.tallas),
                joinedload(Producto.colores),
            )
            .filter(Producto.id_producto.in_(ids_unicos))
            .all()
        )

        mapa_prods = {p.id_producto: p for p in productos}
        resumenes: List[ProductoResumenIA] = []

        for pid in ids_unicos:
            p = mapa_prods.get(pid)
            if not p:
                continue

            colores_res = [
                ColorResumen(nombre_color=c.nombre_color, codigo_hex=c.codigo_hex)
                for c in p.colores
            ]
            tallas_str = [t.nombre_talla for t in p.tallas]

            resumenes.append(
                ProductoResumenIA(
                    id_producto=p.id_producto,
                    nombre=p.nombre,
                    precio_venta=float(p.precio_venta),
                    imagen_url=p.imagen_url,
                    categoria=p.categoria.nombre if p.categoria else None,
                    linea=p.categoria.linea if p.categoria else None,
                    tallas=tallas_str,
                    colores=colores_res,
                    stock_total=p.stock_total,
                )
            )

        return resumenes

    @classmethod
    def _fallback_respuesta(
        cls,
        db: Session,
        consulta: str,
        contexto: Dict[str, Any],
        motivo: str = "",
    ) -> ChatResponseData:
        """Genera una respuesta inteligente de degradación elegante basada directamente en la BD."""
        candidatos = contexto.get("productos_candidatos", [])
        ids = [p.id_producto for p in candidatos[:3]]
        detalles = cls._enriquecer_productos(db, ids)

        lineas = [
            "¡Hola! He consultado nuestro catálogo en tiempo real para encontrar las mejores opciones disponibles para ti:\n"
        ]
        for prod in detalles:
            tallas_txt = ", ".join(prod.tallas) if prod.tallas else "Talla estándar"
            lineas.append(
                f"- **{prod.nombre}** — *Bs {prod.precio_venta:.2f}* (Tallas: {tallas_txt})"
            )

        lineas.append(
            "\n¿Te gustaría ver más opciones o conocer detalles de envíos y sucursales?"
        )

        return ChatResponseData(
            respuesta="\n".join(lineas),
            productos_recomendados=ids,
            productos_detalle=detalles,
            sugerencias=[
                "¿Qué promociones tienen vigentes?",
                "¿Dónde quedan sus sucursales?",
                "¿Cómo funciona el probador virtual?",
            ],
        )

    @classmethod
    def generar_respuesta_chat(
        cls,
        db: Session,
        request: ChatRequest,
    ) -> ChatResponseData:
        """Punto de entrada principal: procesa consulta, realiza RAG y llama a Gemini."""
        # 1. Recuperar contexto de la base de datos
        contexto = cls._recuperar_contexto_db(
            db, request.mensaje, request.id_sucursal
        )

        # 2. Inicializar cliente Gemini
        client = cls._obtener_cliente_gemini()
        if not client:
            return cls._fallback_respuesta(
                db,
                request.mensaje,
                contexto,
                motivo="Cliente Gemini no disponible",
            )

        system_instruction = cls._construir_system_instruction(contexto)
        settings = get_settings()

        # Modelos en orden de preferencia
        modelos_a_intentar = [
            settings.GEMINI_MODEL,
            "gemini-3.5-flash-lite",
            "gemini-3.6-flash",
        ]
        # Quitar duplicados preservando orden
        modelos_unicos = []
        for m in modelos_a_intentar:
            if m and m not in modelos_unicos:
                modelos_unicos.append(m)

        # 3. Construir historial de conversación para Gemini
        contents = []
        for m in request.historial[-6:]:  # Últimos 6 turnos para mantener contexto
            role = "user" if m.rol == "usuario" else "model"
            contents.append({"role": role, "parts": [{"text": m.contenido}]})

        # Mensaje actual
        contents.append({"role": "user", "parts": [{"text": request.mensaje}]})

        # 4. Invocación a Gemini con manejo robusto de excepciones
        from google.genai import types

        config = types.GenerateContentConfig(
            system_instruction=system_instruction,
            response_mime_type="application/json",
            temperature=0.4,
            max_output_tokens=1500,
        )

        ultimo_error = None
        for model_name in modelos_unicos:
            try:
                response = client.models.generate_content(
                    model=model_name,
                    contents=contents,
                    config=config,
                )

                if not response or not response.text:
                    continue

                raw_text = response.text.strip()
                # Limpieza de bloques de código markdown si los hubiera
                if raw_text.startswith("```json"):
                    raw_text = raw_text[7:]
                if raw_text.startswith("```"):
                    raw_text = raw_text[3:]
                if raw_text.endswith("```"):
                    raw_text = raw_text[:-3]
                raw_text = raw_text.strip()

                parsed = json.loads(raw_text)
                respuesta_texto = parsed.get("respuesta", "").strip()
                recomendados_ids = parsed.get("productos_recomendados", [])
                sugerencias = parsed.get("sugerencias", [])

                # Normalizar IDs a enteros
                ids_limpios = []
                for item in recomendados_ids:
                    try:
                        ids_limpios.append(int(item))
                    except (ValueError, TypeError):
                        continue

                # 5. Enriquecer con datos completos de la base de datos
                detalles = cls._enriquecer_productos(db, ids_limpios)

                if not sugerencias:
                    sugerencias = [
                        "¿Tienen probador virtual para estas prendas?",
                        "¿Cuáles son los horarios de las sucursales?",
                        "¿Tienen algún cupón de descuento vigente?",
                    ]

                return ChatResponseData(
                    respuesta=respuesta_texto
                    or "¡Hola! Estoy a tu disposición para ayudarte con las prendas de Attention.",
                    productos_recomendados=ids_limpios,
                    productos_detalle=detalles,
                    sugerencias=sugerencias[:4],
                )

            except Exception as exc:
                logger.warning(
                    f"Fallo al invocar modelo '{model_name}': {exc}. Intentando siguiente..."
                )
                ultimo_error = exc

        logger.error(
            f"Todos los modelos de Gemini fallaron o no están disponibles: {ultimo_error}"
        )
        return cls._fallback_respuesta(
            db,
            request.mensaje,
            contexto,
            motivo=str(ultimo_error),
        )
