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
from app.modules.inventario.models import (
    Categoria,
    Coleccion,
    Color,
    InventarioSucursal,
    Producto,
    Talla,
    Temporada,
)
from app.modules.usuarios.models import Usuario
from app.schemas.ia import (
    ChatMessage,
    ChatRequest,
    ChatResponseData,
    ColorResumen,
    ProductoResumenIA,
    StockSucursalResumen,
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
        genero_usuario: Optional[str] = None,
        nombre_sucursal: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Extrae datos reales y relevantes de la base de datos para grounding con filtrado estricto."""
        hoy = date.today()
        genero_norm = (genero_usuario or "").strip().capitalize()

        # 1. Categorías activas (Filtrado estricto por género)
        query_categorias = db.query(Categoria).filter(Categoria.activo.is_(True))
        if genero_norm == "Hombre":
            query_categorias = query_categorias.filter(
                Categoria.linea.in_(["Hombre", "Unisex"])
            )
        elif genero_norm == "Mujer":
            query_categorias = query_categorias.filter(
                Categoria.linea.in_(["Mujer", "Unisex"])
            )

        categorias = query_categorias.order_by(Categoria.nombre).all()
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

        # 3b. Temporadas y Colecciones vigentes (CU24)
        temporadas = (
            db.query(Temporada)
            .options(joinedload(Temporada.coleccion))
            .filter(Temporada.fecha_fin >= hoy)
            .order_by(Temporada.fecha_inicio.desc())
            .all()
        )
        temporadas_info = [
            f"- Temporada '{t.nombre_temporada}' (Colección: {t.coleccion.nombre_coleccion if t.coleccion else 'General'}) vigente del {t.fecha_inicio} al {t.fecha_fin}"
            for t in temporadas
        ]

        # 4. Búsqueda de Productos Relevantes en Catálogo con Pre-filtrado por Género y Stock
        palabras = [
            p.lower()
            for p in re.findall(r"\b[a-zA-ZáéíóúÁÉÍÓÚñÑ]{3,}\b", consulta)
            if p.lower()
            not in {"que", "las", "los", "con", "para", "una", "uno", "por", "del", "tienen"}
        ]

        query_prods = (
            db.query(Producto)
            .join(Producto.categoria)
            .options(
                joinedload(Producto.categoria),
                joinedload(Producto.tallas),
                joinedload(Producto.colores),
                joinedload(Producto.inventarios).joinedload(InventarioSucursal.sucursal),
            )
            .filter(Producto.estado == "Activo")
        )

        # REGLA 2: Filtrado obligatorio de colecciones por género
        if genero_norm == "Hombre":
            query_prods = query_prods.filter(Categoria.linea.in_(["Hombre", "Unisex"]))
            # Prohibir terminantemente prendas o categorías exclusivamente femeninas
            query_prods = query_prods.filter(
                ~Producto.nombre.ilike("%vestido%"),
                ~Producto.nombre.ilike("%falda%"),
                ~Producto.nombre.ilike("%blusa%"),
                ~Producto.nombre.ilike("%taco%"),
            )
        elif genero_norm == "Mujer":
            query_prods = query_prods.filter(Categoria.linea.in_(["Mujer", "Unisex"]))

        filtros_or = []
        for palabra in palabras[:5]:  # Máximo 5 términos clave
            filtros_or.append(Producto.nombre.ilike(f"%{palabra}%"))
            filtros_or.append(Producto.descripcion.ilike(f"%{palabra}%"))

        productos_coincidentes = []
        if filtros_or:
            productos_coincidentes = (
                query_prods.filter(or_(*filtros_or)).limit(15).all()
            )

        # Complementamos con productos destacados de la misma colección permitida
        ids_coincidentes = {p.id_producto for p in productos_coincidentes}
        productos_generales = (
            query_prods.filter(~Producto.id_producto.in_(ids_coincidentes))
            .limit(10)
            .all()
        )

        productos_totales = productos_coincidentes + productos_generales

        nombre_suc_tag = nombre_sucursal or "Sucursal Activa"

        productos_info = []
        for p in productos_totales[:20]:
            tallas_str = ", ".join([t.nombre_talla for t in p.tallas]) or "Estándar"
            colores_str = ", ".join([c.nombre_color for c in p.colores]) or "Único"
            cat_str = p.categoria.nombre if p.categoria else "General"
            linea_str = p.categoria.linea if p.categoria else "Unisex"

            # Stock real en la sucursal activa
            stock_en_sucursal_activa = 0
            if id_sucursal:
                inv_activo = next(
                    (inv for inv in (p.inventarios or []) if inv.id_sucursal == id_sucursal),
                    None,
                )
                if inv_activo:
                    stock_en_sucursal_activa = inv_activo.stock

            # Detalle de stock por todas las sucursales físicas
            stock_por_suc = [
                f"{inv.sucursal.nombre}: {inv.stock} u."
                for inv in (p.inventarios or [])
                if inv.sucursal and inv.stock > 0
            ]
            stock_detallado = ", ".join(stock_por_suc) if stock_por_suc else "Sin stock físico en sucursales"

            stock_info_text = (
                f"Stock en {nombre_suc_tag}: {stock_en_sucursal_activa} u. (Otras sucursales: {stock_detallado})"
                if id_sucursal
                else f"Disponibilidad por sucursal: ({stock_detallado})"
            )

            productos_info.append(
                f"- [ID:{p.id_producto}] '{p.nombre}' | Colección: {linea_str} | Cat: {cat_str} | "
                f"Precio: Bs {float(p.precio_venta):.2f} | Tallas: {tallas_str} | Colores: {colores_str} | "
                f"{stock_info_text} | Stock Total: {p.stock_total} u. | Desc: {p.descripcion or ''}"
            )

        return {
            "categorias": categorias_info,
            "sucursales": sucursales_info,
            "promociones": promos_info,
            "temporadas": temporadas_info,
            "productos": productos_info,
            "productos_candidatos": productos_totales,
            "nombre_sucursal_resuelto": nombre_suc_tag,
        }

    @classmethod
    def _construir_system_instruction(
        cls,
        contexto: Dict[str, Any],
        nombre_usuario: str,
        genero_usuario: str,
        nombre_sucursal: str,
    ) -> str:
        """Construye el prompt de sistema unificado para Web y Mobile con regla estricta de género."""
        categorias_txt = "\n".join(contexto["categorias"]) or "No registradas"
        sucursales_txt = "\n".join(contexto["sucursales"]) or "No registradas"
        promos_txt = "\n".join(contexto["promociones"]) or "Sin promociones activas"
        temporadas_txt = "\n".join(contexto.get("temporadas", [])) or "Sin temporadas específicas"
        productos_txt = "\n".join(contexto["productos"]) or "Sin prendas disponibles"

        return f"""Actúa como un Asistente de Moda Personal avanzado para 'Attention E-Commerce'.
Conoce al usuario: Nombre: {nombre_usuario}, Género: {genero_usuario}, Sucursal: {nombre_sucursal}.

Instrucciones estrictas de comportamiento:
- Utiliza el nombre del usuario ({nombre_usuario}) para personalizar el saludo de forma cálida.
- **El género del usuario es innegociable:** Si el género es 'Hombre', solo puedes recomendar productos de la categoría 'Hombre' o Unisex. Tienes estrictamente prohibido sugerir prendas femeninas (como vestidos, faldas, blusas). Si el género es 'Mujer', prioriza la categoría 'Mujer'. No mezcles colecciones.
- Responde consultando el catálogo en tiempo real y el stock disponible en la sucursal {nombre_sucursal}.
- Ofrece recomendaciones concretas (nombre exacto, precio en Bolivianos 'Bs', tallas y colores) y sugiere acciones como "Ver detalles del envío" o "Consultar stock en otra sucursal".
- Mantén un tono amigable, profesional y experto en moda.
- Si no encuentras el producto exacto, sé honesto y ofrece alternativas cercanas de la misma colección.

DIRECTIVAS ESTRICTAS DE RESPUESTA:
1. INFORMACIÓN FIDEDIGNA: Solo recomienda prendas que figuren en la sección [CATÁLOGO DE PRENDAS Y STOCK EN TIEMPO REAL]. NUNCA inventes productos, precios ni tallas que no existan en la lista.
2. CITACIÓN DE PRODUCTOS: Cada vez que recomiendes una prenda en tu texto, debes incluir su ID numérico en la lista "productos_recomendados".
3. VERIFICACIÓN DE SUCURSAL: Indica la disponibilidad física en {nombre_sucursal} según los datos provistos.
4. FORMATO DE SALIDA ESTRICTO: Tu respuesta DEBE SER UN OBJETO JSON VÁLIDO con la siguiente estructura exacta:
{{
  "respuesta": "Texto en Markdown para el cliente con la explicación y asesoría.",
  "productos_recomendados": [1, 2],
  "sugerencias": ["¿En qué colores viene la primera prenda?", "¿Tienen probador virtual?", "Ver detalles del envío", "Consultar stock en otra sucursal"]
}}

=== DATOS EN TIEMPO REAL DE LA TIENDA ATTENTION ===

[CATEGORÍAS DE PRENDAS DISPONIBLES]
{categorias_txt}

[TEMPORADAS Y COLECCIONES ACTIVAS]
{temporadas_txt}

[SUCURSAL ACTIVA Y TODAS LAS TIENDAS FÍSICAS]
Sucursal de Preferencia: {nombre_sucursal}
{sucursales_txt}

[PROMOCIONES ACTIVAS]
{promos_txt}

[CATÁLOGO DE PRENDAS Y STOCK EN TIEMPO REAL]
{productos_txt}
"""

    @classmethod
    def _enriquecer_productos(
        cls, db: Session, product_ids: List[int]
    ) -> List[ProductoResumenIA]:
        """Carga el detalle visual completo de los productos recomendados para las tarjetas interactivas."""
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
                joinedload(Producto.inventarios).joinedload(InventarioSucursal.sucursal),
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

            stock_sucursales = [
                StockSucursalResumen(sucursal=inv.sucursal.nombre, stock=inv.stock)
                for inv in (p.inventarios or [])
                if inv.sucursal and inv.stock > 0
            ]

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
                    temporada=None,
                    stock_sucursales=stock_sucursales,
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
        nombre_usuario: str = "Cliente",
        genero_usuario: str = "No especificado",
        nombre_sucursal: str = "Sucursal Central",
    ) -> ChatResponseData:
        """Genera una respuesta inteligente de degradación elegante basada directamente en la BD."""
        candidatos = contexto.get("productos_candidatos", [])
        ids = [p.id_producto for p in candidatos[:3]]
        detalles = cls._enriquecer_productos(db, ids)

        saludo = f"¡Hola {nombre_usuario}!" if nombre_usuario and nombre_usuario != "Cliente" else "¡Hola!"
        lineas = [
            f"{saludo} He consultado nuestro catálogo y el stock en tiempo real en la sucursal **{nombre_sucursal}** para encontrar las mejores opciones disponibles para ti:\n"
        ]
        for prod in detalles:
            tallas_txt = ", ".join(prod.tallas) if prod.tallas else "Talla estándar"
            lineas.append(
                f"- **{prod.nombre}** — *Bs {prod.precio_venta:.2f}* (Tallas: {tallas_txt} | Colección: {prod.linea or 'General'})"
            )

        lineas.append(
            f"\n¿Te gustaría ver más opciones de la colección, consultar detalles del envío a domicilio o verificar stock en otra sucursal?"
        )

        return ChatResponseData(
            respuesta="\n".join(lineas),
            productos_recomendados=ids,
            productos_detalle=detalles,
            sugerencias=[
                "Ver detalles del envío",
                "Consultar stock en otra sucursal",
                "¿Qué promociones tienen vigentes?",
                "¿Cómo funciona el probador virtual?",
            ],
        )

    @classmethod
    def generar_respuesta_chat(
        cls,
        db: Session,
        request: ChatRequest,
        usuario_actual: Optional[Usuario] = None,
    ) -> ChatResponseData:
        """Punto de entrada principal: procesa consulta, realiza RAG y llama a Gemini."""
        # 1. Resolver nombre del cliente
        nombre_usuario = (
            request.nombre_usuario
            or (usuario_actual.nombre if usuario_actual else None)
            or "Cliente"
        )

        # 2. Resolver género del cliente
        genero_usuario = request.genero_usuario or "No especificado"

        # 3. Resolver ID de sucursal
        id_sucursal = request.id_sucursal or (
            usuario_actual.id_sucursal if usuario_actual else None
        )

        # 4. Resolver nombre de sucursal activa
        nombre_sucursal = request.nombre_sucursal
        if not nombre_sucursal and id_sucursal:
            suc = db.get(Sucursal, id_sucursal)
            if suc:
                nombre_sucursal = suc.nombre

        if not nombre_sucursal:
            primera_suc = (
                db.query(Sucursal).filter(Sucursal.is_active.is_(True)).first()
            )
            nombre_sucursal = (
                primera_suc.nombre if primera_suc else "Sucursal Central (Santa Cruz)"
            )
            if not id_sucursal and primera_suc:
                id_sucursal = primera_suc.codigo_sucursal

        # 5. Recuperar contexto de la base de datos con grounding y pre-filtrado estricto
        contexto = cls._recuperar_contexto_db(
            db,
            request.mensaje,
            id_sucursal=id_sucursal,
            genero_usuario=genero_usuario,
            nombre_sucursal=nombre_sucursal,
        )

        # 6. Inicializar cliente Gemini
        client = cls._obtener_cliente_gemini()
        if not client:
            return cls._fallback_respuesta(
                db,
                request.mensaje,
                contexto,
                motivo="Cliente Gemini no disponible",
                nombre_usuario=nombre_usuario,
                genero_usuario=genero_usuario,
                nombre_sucursal=nombre_sucursal,
            )

        system_instruction = cls._construir_system_instruction(
            contexto,
            nombre_usuario=nombre_usuario,
            genero_usuario=genero_usuario,
            nombre_sucursal=nombre_sucursal,
        )
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

        # 7. Construir historial de conversación para Gemini
        contents = []
        for m in request.historial[-6:]:  # Últimos 6 turnos para mantener contexto
            role = "user" if m.rol == "usuario" else "model"
            contents.append({"role": role, "parts": [{"text": m.contenido}]})

        # Mensaje actual
        contents.append({"role": "user", "parts": [{"text": request.mensaje}]})

        # 8. Invocación a Gemini con manejo robusto de excepciones
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

                # 9. Enriquecer con datos completos de la base de datos
                detalles = cls._enriquecer_productos(db, ids_limpios)

                if not sugerencias:
                    sugerencias = [
                        "Ver detalles del envío",
                        "Consultar stock en otra sucursal",
                        "¿Tienen probador virtual para estas prendas?",
                        "¿Tienen algún cupón de descuento vigente?",
                    ]

                return ChatResponseData(
                    respuesta=respuesta_texto
                    or f"¡Hola {nombre_usuario}! Estoy a tu disposición para ayudarte con las prendas de Attention.",
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
            nombre_usuario=nombre_usuario,
            genero_usuario=genero_usuario,
            nombre_sucursal=nombre_sucursal,
        )
