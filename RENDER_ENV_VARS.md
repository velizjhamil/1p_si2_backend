# 🚀 Variables de Entorno para Render

Este archivo contiene las variables de entorno que DEBEN configurarse en Render para que el backend funcione correctamente en producción.

## 📋 Configuración en Render Dashboard

Ve a tu servicio en Render:
1. Dashboard → Tu servicio backend
2. Environment → Environment Variables
3. Agrega cada variable de abajo

---

## ✅ Variables Obligatorias

### 🔑 Base de Datos (Supabase)

```bash
DATABASE_URL=postgresql://postgres:224029479.jhamil@db.dxbmnnjtzpmiigrqqoec.supabase.co:5432/postgres
```

**Alternativa (partes individuales):**
```bash
DB_HOST=db.dxbmnnjtzpmiigrqqoec.supabase.co
DB_PORT=5432
DB_USER=postgres
DB_PASSWORD=224029479.jhamil
DB_NAME=postgres
```

---

### 🔐 Seguridad JWT

```bash
SECRET_KEY=1d6a1b8d6cfe39178464088a8f31c8253330034dc16663a94958305b36fc8d33
```

⚠️ **CRÍTICO**: Esta clave firma los JWT. Si usas la clave por defecto del código, los tokens son inseguros.

```bash
ALGORITHM=HS256
```

```bash
ACCESS_TOKEN_EXPIRE_MINUTES=60
```

---

### 🌐 CORS (Frontend en Vercel)

**Formato JSON (recomendado):**
```bash
CORS_ORIGINS=["https://1p-si2-frontend.vercel.app","http://localhost:4200"]
```

**Formato alternativo (CSV):**
```bash
CORS_ORIGINS=https://1p-si2-frontend.vercel.app,http://localhost:4200
```

⚠️ **IMPORTANTE**: 
- Incluye AMBOS dominios (producción + localhost para desarrollo)
- Sin espacios extra en el formato JSON
- Si tu app Vercel tiene múltiples dominios (*.vercel.app), agrégalos todos

---

## 🎯 Resumen de Variables (copy-paste)

```bash
# Base de datos
DATABASE_URL=postgresql://postgres:224029479.jhamil@db.dxbmnnjtzpmiigrqqoec.supabase.co:5432/postgres

# JWT
SECRET_KEY=1d6a1b8d6cfe39178464088a8f31c8253330034dc16663a94958305b36fc8d33
ALGORITHM=HS256
ACCESS_TOKEN_EXPIRE_MINUTES=60

# CORS
CORS_ORIGINS=["https://1p-si2-frontend.vercel.app","http://localhost:4200"]
```

---

## 🧪 Verificación Post-Deploy

### 1. Verificar que el backend arrancó correctamente:

```bash
curl https://attention-backend-czw9.onrender.com/
```

**Respuesta esperada:**
```json
{
  "message": "Bienvenido a la API de Attention E-Commerce",
  "documentacion": "/docs"
}
```

### 2. Verificar CORS en los logs de Render:

Busca esta línea en los logs durante el startup:
```
🌐 CORS configured with origins: ['https://1p-si2-frontend.vercel.app', 'http://localhost:4200']
```

### 3. Test de login desde frontend:

Abre https://1p-si2-frontend.vercel.app/ e intenta hacer login. Si hay error CORS, verifica que:
- La variable `CORS_ORIGINS` esté bien escrita (formato JSON válido)
- El dominio de Vercel coincida exactamente (sin `/` al final)
- Se haya redeployado el servicio después de agregar las variables

---

## 🔧 Troubleshooting

### Error: "CORS policy blocked"
- ✅ Verifica `CORS_ORIGINS` en Render
- ✅ Redeploya el servicio
- ✅ Limpia cache del navegador

### Error: "Invalid token" o tokens que no funcionan
- ✅ Verifica que `SECRET_KEY` esté configurada
- ✅ Asegúrate de que sea la MISMA clave en todos los deploys
- ✅ Si cambiaste la clave, los tokens anteriores quedan invalidados

### Error 500 en login
- ✅ Verifica logs de Render en tiempo real
- ✅ Confirma que `DATABASE_URL` es correcta
- ✅ Prueba la conexión a Supabase desde Render (puede haber IPs bloqueadas)

### Base de datos no conecta
- ✅ Verifica que Supabase permite conexiones desde cualquier IP
- ✅ En Supabase Dashboard → Settings → Database → Connection Pooling
- ✅ Usa Connection Pooling URL si hay muchas conexiones simultáneas

---

## 📊 URLs del Proyecto

| Componente | URL |
|------------|-----|
| Frontend (Vercel) | https://1p-si2-frontend.vercel.app/ |
| Backend (Render) | https://attention-backend-czw9.onrender.com/api/v1 |
| Docs API | https://attention-backend-czw9.onrender.com/docs |
| Base de datos | Supabase (PostgreSQL) |

---

## 🔄 Actualizar Variables

Si necesitas actualizar alguna variable:
1. Render Dashboard → Environment → Edita la variable
2. **Guarda los cambios** (botón Save Changes)
3. El servicio se **redeployará automáticamente**
4. Espera ~2-3 minutos para el redeploy completo

