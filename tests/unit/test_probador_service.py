import base64
import io
import unittest
from unittest.mock import patch
from PIL import Image
from fastapi import HTTPException

from app.core.config import get_settings
from app.services.probador_ia_service import ProbadorIAService


class TestProbadorIAService(unittest.TestCase):

    def setUp(self):
        img = Image.new("RGB", (600, 800), color=(200, 200, 200))
        buf = io.BytesIO()
        img.save(buf, format="JPEG")
        self.sample_user_photo_b64 = "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode("utf-8")

        img_p = Image.new("RGBA", (400, 400), color=(255, 0, 0, 255))
        buf_p = io.BytesIO()
        img_p.save(buf_p, format="PNG")
        self.sample_garment_b64 = "data:image/png;base64," + base64.b64encode(buf_p.getvalue()).decode("utf-8")

    def test_construir_prompt_zonas(self):
        # 1. Lower body
        p_lower = ProbadorIAService._construir_prompt_tryon(
            producto_nombre="Jean Slim Fit",
            producto_categoria="Pantalones y Jeans",
            color_nombre="Azul",
            talla_elegida="32",
            complexion="MEDIA",
            estatura_cm=175,
            peso_kg=70,
        )
        self.assertIn("lower body (waist, hips and legs)", p_lower)
        self.assertIn("size 32", p_lower)
        self.assertIn("body type MEDIA", p_lower)

        # 2. Full torso and legs
        p_vestido = ProbadorIAService._construir_prompt_tryon(
            producto_nombre="Vestido de Noche",
            producto_categoria="Vestidos de gala",
            color_nombre="Negro",
            talla_elegida="M",
            complexion="DELGADA",
            estatura_cm=165,
            peso_kg=52,
        )
        self.assertIn("full torso and legs", p_vestido)

        # 3. Feet
        p_calzado = ProbadorIAService._construir_prompt_tryon(
            producto_nombre="Zapatillas Urbanas",
            producto_categoria="Calzado",
            color_nombre="Blanco",
            talla_elegida="40",
            complexion="MEDIA",
            estatura_cm=170,
            peso_kg=68,
        )
        self.assertIn("feet", p_calzado)

        # 4. Upper body (default)
        p_upper = ProbadorIAService._construir_prompt_tryon(
            producto_nombre="Camisa Oxford",
            producto_categoria="Camisas",
            color_nombre="Azul Cielo",
            talla_elegida="L",
            complexion="ROBUSTA",
            estatura_cm=180,
            peso_kg=90,
        )
        self.assertIn("upper body (shoulders, chest, torso and arms)", p_upper)
        self.assertIn("You are a state-of-the-art virtual try-on engine", p_upper)
        self.assertIn("Return ONLY the final edited photograph, photorealistic, sharp, high resolution.", p_upper)

    def test_simulacion_ar_fallback_automatico_sin_error_502(self):
        """Cuando Gemini falla o da timeout, el backend NUNCA arroja 502; aplica Pillow local por defecto."""
        with patch.object(ProbadorIAService, "_generar_prueba_virtual_gemini", side_effect=Exception("Timeout / Quota exceeded")):
            res = ProbadorIAService.procesar_simulacion_ar(
                foto_usuario_data_url=self.sample_user_photo_b64,
                producto_nombre="Camisa Oxford",
                producto_categoria="Camisas",
                talla_elegida="M",
                tallas_disponibles=["S", "M", "L"],
                color_nombre="Blanco",
                prenda_imagen_url=self.sample_garment_b64,
            )

            # Debe retornar respuesta 200/201 exitosa al cliente
            self.assertFalse(res["gemini_activo"])
            self.assertEqual(res["motor"], "Composición local (Pillow)")
            self.assertIn("Simulación visual generada con motor gráfico local", res["comentario_estilo"])
            self.assertTrue(res["resultado_imagen_url"].startswith("data:image/jpeg;base64,"))
            self.assertEqual(res["talla_recomendada"], "M")
            self.assertEqual(res["ajuste_estimado"], "PERFECTO")

    def test_simulacion_ar_gemini_exito(self):
        """Cuando Gemini tiene éxito, retorna gemini_activo=True, motor de Gemini y el nuevo JPG."""
        fake_img = Image.new("RGB", (300, 400), color=(10, 20, 30))
        buf = io.BytesIO()
        fake_img.save(buf, format="JPEG")
        fake_bytes = buf.getvalue()

        with patch.object(ProbadorIAService, "_generar_prueba_virtual_gemini", return_value=(fake_bytes, "gemini-2.5-flash")):
            res = ProbadorIAService.procesar_simulacion_ar(
                foto_usuario_data_url=self.sample_user_photo_b64,
                producto_nombre="Camisa Oxford",
                producto_categoria="Camisas",
                talla_elegida="M",
                tallas_disponibles=["S", "M", "L"],
                color_nombre="Blanco",
                prenda_imagen_url=self.sample_garment_b64,
            )

            self.assertTrue(res["gemini_activo"])
            self.assertEqual(res["motor"], "Google Gemini (gemini-2.5-flash)")
            self.assertIn("adaptada con IA (gemini-2.5-flash)", res["comentario_estilo"])
            self.assertIn(res["talla_recomendada"], ["S", "M", "L"])
            self.assertIn(res["ajuste_estimado"], ["PERFECTO", "AJUSTADO", "HOLGADO"])
            self.assertTrue(res["resultado_imagen_url"].startswith("data:image/jpeg;base64,"))
            self.assertIn(res["talla_recomendada"], ["S", "M", "L"])
            self.assertIn(res["ajuste_estimado"], ["PERFECTO", "AJUSTADO", "HOLGADO"])
            self.assertTrue(res["resultado_imagen_url"].startswith("data:image/jpeg;base64,"))

    def test_descargar_prenda_limite_tamano(self):
        """Valida que _descargar_y_procesar_prenda rechace payloads base64 > 10MB."""
        oversized = "data:image/png;base64," + ("A" * (11 * 1024 * 1024))
        raw, mime, pil_img = ProbadorIAService._descargar_y_procesar_prenda(
            url=oversized,
            color_hex="#ff0000",
            categoria="Camisas",
        )
        self.assertIsNotNone(pil_img)
        self.assertIn(mime, ["image/png", "image/jpeg"])


if __name__ == "__main__":
    unittest.main()
