import unittest
from unittest.mock import MagicMock, patch
from fastapi.testclient import TestClient

from app.main import app
from app.schemas.ia import (
    ChatMessage,
    ChatRequest,
    ChatResponseData,
    ColorResumen,
    ProductoResumenIA,
)
from app.services.ia_service import IAService


class TestIAModule(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app)

    def test_pydantic_schemas_chat_request_valid(self):
        """Valida la instanciación correcta de ChatRequest."""
        req = ChatRequest(
            mensaje="¿Qué vestidos tienen?",
            historial=[
                ChatMessage(rol="usuario", contenido="Hola"),
                ChatMessage(rol="asistente", contenido="¡Hola! ¿En qué te ayudo?"),
            ],
            id_sucursal=1,
        )
        self.assertEqual(req.mensaje, "¿Qué vestidos tienen?")
        self.assertEqual(len(req.historial), 2)
        self.assertEqual(req.id_sucursal, 1)

    def test_pydantic_schemas_chat_response_data(self):
        """Valida la estructura de datos enriquecidos devueltos por el asistente."""
        data = ChatResponseData(
            respuesta="Te recomiendo estas blusas elegantes.",
            productos_recomendados=[10, 12],
            productos_detalle=[
                ProductoResumenIA(
                    id_producto=10,
                    nombre="Blusa Satinada",
                    precio_venta=140.0,
                    categoria="Blusas",
                    linea="Mujer",
                    tallas=["S", "M"],
                    colores=[ColorResumen(nombre_color="Negro", codigo_hex="#000000")],
                    stock_total=5,
                )
            ],
            sugerencias=["¿Tienen más colores?", "¿En qué sucursal la encuentro?"],
        )
        self.assertEqual(len(data.productos_recomendados), 2)
        self.assertEqual(len(data.productos_detalle), 1)
        self.assertEqual(data.productos_detalle[0].nombre, "Blusa Satinada")
        self.assertEqual(data.productos_detalle[0].precio_venta, 140.0)

    def test_ia_service_construir_system_instruction(self):
        """Verifica que el prompt del sistema incluya adecuadamente las secciones de grounding."""
        contexto = {
            "categorias": ["- [1] Blusas (Mujer)"],
            "sucursales": ["- Sucursal Norte: Av. Banzer. Horario: 9-20"],
            "promociones": ["- 20% OFF con cupón FIESTA"],
            "productos": ["- [ID:1] 'Blusa Seda' | Cat: Blusas | Precio: Bs 150.00"],
        }
        prompt = IAService._construir_system_instruction(contexto)
        self.assertIn("Attention AI", prompt)
        self.assertIn("[CATEGORÍAS DE PRENDAS]", prompt)
        self.assertIn("[SUCURSALES FÍSICAS]", prompt)
        self.assertIn("[PROMOCIONES ACTIVAS]", prompt)
        self.assertIn("[CATÁLOGO DE PRENDAS DISPONIBLES]", prompt)
        self.assertIn("Blusa Seda", prompt)
        self.assertIn("Av. Banzer", prompt)

    def test_ia_service_fallback_respuesta(self):
        """Valida que el fallback inteligente genere respuestas amigables sin error."""
        mock_db = MagicMock()
        contexto = {
            "productos_candidatos": [],
        }
        with patch.object(IAService, "_enriquecer_productos", return_value=[]):
            res = IAService._fallback_respuesta(
                mock_db, "¿Qué vestidos tienen?", contexto, motivo="API timeout"
            )
            self.assertIsInstance(res, ChatResponseData)
            self.assertGreater(len(res.respuesta), 10)
            self.assertGreater(len(res.sugerencias), 0)

    def test_endpoint_get_status(self):
        """Verifica que GET /api/v1/ia/status devuelva estado 200 y campos de diagnóstico."""
        r = self.client.get("/api/v1/ia/status")
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertEqual(body["status"], "success")
        data = body["data"]
        self.assertIn("gemini_configurado", data)
        self.assertIn("database_online", data)
        self.assertIn("capacidades", data)

    def test_endpoint_chat_empty_message_422(self):
        """Verifica validación 422 para mensajes vacíos."""
        r = self.client.post("/api/v1/ia/chat", json={"mensaje": ""})
        self.assertEqual(r.status_code, 422)


if __name__ == "__main__":
    unittest.main()
