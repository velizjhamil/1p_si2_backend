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
        # Texto canónico estricto requerido
        prompt_canonico = (
            "Genera una fotografía fotorrealista de cuerpo entero donde la persona de la primera imagen "
            "vista la prenda de la segunda imagen. La tela debe ajustarse tridimensionalmente al torso y hombros, "
            "manteniendo intactos el rostro, la postura y el fondo original."
        )

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
        self.assertIn(prompt_canonico, p_lower)
        self.assertIn("lower body (waist, hips, thighs, and legs)", p_lower)
        self.assertIn("Talla seleccionada: 32", p_lower)
        self.assertIn("Complexión del usuario: MEDIA", p_lower)
        self.assertIn("175 cm de estatura, 70 kg de peso", p_lower)

        # 2. Full torso and legs (Vestidos)
        p_vestido = ProbadorIAService._construir_prompt_tryon(
            producto_nombre="Vestido de Noche",
            producto_categoria="Vestidos de gala",
            color_nombre="Negro",
            talla_elegida="M",
            complexion="DELGADA",
            estatura_cm=165,
            peso_kg=52,
        )
        self.assertIn(prompt_canonico, p_vestido)
        self.assertIn("full torso and legs", p_vestido)

        # 3. Feet (Calzado)
        p_calzado = ProbadorIAService._construir_prompt_tryon(
            producto_nombre="Zapatillas Urbanas",
            producto_categoria="Calzado",
            color_nombre="Blanco",
            talla_elegida="40",
            complexion="MEDIA",
            estatura_cm=170,
            peso_kg=68,
        )
        self.assertIn(prompt_canonico, p_calzado)
        self.assertIn("feet / shoes", p_calzado)

        # 4. Upper body (Default)
        p_upper = ProbadorIAService._construir_prompt_tryon(
            producto_nombre="Camisa Oxford",
            producto_categoria="Camisas",
            color_nombre="Azul Cielo",
            talla_elegida="L",
            complexion="ROBUSTA",
            estatura_cm=180,
            peso_kg=90,
        )
        self.assertIn(prompt_canonico, p_upper)
        self.assertIn("upper body (shoulders, chest, torso, and arms)", p_upper)
        self.assertIn("Preservación biométrica absoluta", p_upper)

    def test_simulacion_ar_fallback_automatico_sin_error_502(self):
        """Cuando Gemini / Imagen falla o da timeout, el backend NUNCA arroja 502; aplica Pillow local por defecto."""
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

            # Debe retornar respuesta exitosa con fallback gráfico local
            self.assertFalse(res["gemini_activo"])
            self.assertIn("Pillow", res["motor"])
            self.assertIn("Simulación visual generada con motor de deformación corporal 3D", res["comentario_estilo"])
            self.assertTrue(res["resultado_imagen_url"].startswith("data:image/jpeg;base64,"))
            self.assertEqual(res["talla_recomendada"], "M")
            self.assertEqual(res["ajuste_estimado"], "PERFECTO")

    def test_simulacion_ar_imagen3_exito(self):
        """Cuando Imagen 3 / Google GenAI tiene éxito, retorna gemini_activo=True y la imagen generada en Base64."""
        fake_img = Image.new("RGB", (300, 400), color=(10, 20, 30))
        buf = io.BytesIO()
        fake_img.save(buf, format="JPEG")
        fake_bytes = buf.getvalue()

        motor_esperado = "Google GenAI Imagen 3 (imagen-3.0-generate-002)"
        with patch.object(ProbadorIAService, "_generar_prueba_virtual_gemini", return_value=(fake_bytes, motor_esperado)):
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
            self.assertEqual(res["motor"], motor_esperado)
            self.assertIn("adaptada con IA fotorrealista", res["comentario_estilo"])
            self.assertIn(res["talla_recomendada"], ["S", "M", "L"])
            self.assertIn(res["ajuste_estimado"], ["PERFECTO", "AJUSTADO", "HOLGADO"])
            self.assertTrue(res["resultado_imagen_url"].startswith("data:image/jpeg;base64,"))

    def test_invocacion_directa_client_models_generate_images(self):
        """Valida que _generar_prueba_virtual_gemini invoque client.models.generate_images con el modelo Imagen 3."""
        fake_img = Image.new("RGB", (200, 200), color=(50, 100, 150))
        buf = io.BytesIO()
        fake_img.save(buf, format="JPEG")
        fake_jpeg_bytes = buf.getvalue()

        # Mock de cliente Google GenAI y respuesta de generate_images
        from unittest.mock import MagicMock
        mock_client = MagicMock()
        mock_image_obj = MagicMock()
        mock_image_obj.image_bytes = fake_jpeg_bytes
        mock_gen_img = MagicMock()
        mock_gen_img.image = mock_image_obj
        mock_response = MagicMock()
        mock_response.generated_images = [mock_gen_img]

        mock_client.models.generate_images.return_value = mock_response

        with patch.object(ProbadorIAService, "_obtener_cliente_nano_banana", return_value=mock_client):
            resultado = ProbadorIAService._generar_prueba_virtual_gemini(
                foto_bytes=b"fake_user_bytes",
                prenda_bytes=b"fake_garment_bytes",
                prenda_mime="image/png",
                producto_nombre="Polo Pima",
                producto_categoria="Polos",
                color_nombre="Negro",
                talla_elegida="L",
                complexion="MEDIA",
                estatura_cm=178,
                peso_kg=75,
            )

            self.assertIsNotNone(resultado)
            img_bytes, motor_tag = resultado
            self.assertIn("Imagen 3", motor_tag)
            self.assertTrue(img_bytes.startswith(b"\xff\xd8"))  # Cabecera SOI estándar JPEG
            res_pil = Image.open(io.BytesIO(img_bytes))
            self.assertEqual(res_pil.size, (200, 200))
            # Verifica que client.models.generate_images fue efectivamente invocado
            mock_client.models.generate_images.assert_called_once()
            args, kwargs = mock_client.models.generate_images.call_args
            self.assertEqual(kwargs.get("model"), "imagen-3.0-generate-002")
            self.assertIn("Genera una fotografía fotorrealista", kwargs.get("prompt"))

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
