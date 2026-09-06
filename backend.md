# CLAUDE.md — Attention Backend (FastAPI)

## Proyecto
**Attention** — Plataforma inteligente de e-commerce para tienda de ropa con vestidor virtual vía Realidad Aumentada.
Universidad Autónoma Gabriel René Moreno (UAGRM) · FICCT · Sistemas de Información II · Gestión 2-2026

---

## Stack técnico
| Capa | Tecnología |
|---|---|
| Backend | FastAPI (Python 3.11+) |
| ORM | SQLAlchemy 2.x + Alembic (migraciones) |
| Base de datos | PostgreSQL 15 |
| Autenticación | JWT (python-jose) + bcrypt |
| Cloud | AWS (despliegue) |
| Pasarela de pago principal | Libélula (Bolivia) |
| Pasarela de respaldo | PayPal / Stripe |

---

## Estructura de carpetas (NO modificar esta convención)
```
app/
├── routers/          # Endpoints FastAPI — un archivo por módulo
├── models/           # Modelos SQLAlchemy — mapeo directo a tablas
├── schemas/          # Pydantic schemas — request/response
├── services/         # Lógica de negocio — sin acceso directo a BD
├── dependencies/     # Dependencias inyectables (auth, db session)
├── core/             # Config, seguridad, constantes
└── main.py           # App principal, registro de routers
```

---

## Convenciones de nombres
- **Tablas**: snake_case en plural → `usuarios`, `roles`, `permisos`, `sucursales`
- **Modelos**: PascalCase singular → `Usuario`, `Rol`, `Permiso`, `Sucursal`
- **Schemas**: `{Entidad}Create`, `{Entidad}Update`, `{Entidad}Response`
- **Routers**: prefijo `/api/v1/{modulo}` → `/api/v1/auth`, `/api/v1/usuarios`
- **Services**: `{entidad}_service.py` → `auth_service.py`, `usuario_service.py`
- **PKs**: UUID v4 en todas las tablas (tipo `uuid` en PostgreSQL)

---

## Actores del sistema
| ID | Actor | Descripción |
|---|---|---|
| ASU | Administrador Super Usuario | Gestiona usuarios, roles y permisos |
| GS | Gerente de Sucursal | Gestiona empresa, sucursales, inventario, productos |
| V | Vendedor | Registra ventas presenciales |
| C | Cliente | Compra, reserva, usa el vestidor virtual |
| D | Agencia de Delivery | Gestiona envíos |

---

## Módulos del sistema (25 casos de uso, 3 ciclos)

### Ciclo 1 — Base (IMPLEMENTAR PRIMERO)
- CU1: Iniciar sesión (todos los actores)
- CU2: Cerrar sesión (todos los actores)
- CU3: Gestionar Usuario (ASU)
- CU4: Gestionar Rol (ASU)
- CU5: Gestionar Permisos (ASU)
- CU16: Gestión de Empresa (GS)
- CU17: Gestión de Sucursales (GS)
- CU23: Gestionar Proveedores (GS)

### Ciclo 2 — Catálogo y Ventas
- CU6: Gestionar Productos, CU7: Tallas, CU8: Probador Virtual
- CU9: Categorías, CU22: Inventario, CU24: Temporadas/Colecciones
- CU14: Reservas, CU15: Carrito, CU21: Compra

### Ciclo 3 — Ventas avanzadas, IA, Delivery
- CU11: Ventas, CU12: Descuentos, CU13: Devolución
- CU10: Notificaciones, CU20: Reportes, CU25: IA/Recomendaciones
- CU18: Envío, CU19: Agencias de Reparto

---

## Reglas de negocio clave
1. **Autenticación**: JWT con expiración configurable. Bloqueo temporal tras N intentos fallidos (RNF01).
2. **Roles y permisos**: Matriz rol-permiso en tabla `rol_permiso`. Los permisos se verifican en cada endpoint.
3. **Inventario**: Se actualiza automáticamente al confirmar una venta o reserva.
4. **Reservas**: Una reserva bloquea stock (`cantidad_reservada`). Se libera si se cancela.
5. **Sucursales**: No se puede desactivar una sucursal con inventario o reservas activas.
6. **Proveedores**: No se puede eliminar un proveedor con productos activos; solo desactivar.
7. **Roles**: No se puede eliminar un rol con usuarios activos asignados.

---

## Modelo de datos — tablas principales
```
usuario(id_usuario PK, id_rol FK, nombre, correo, password, estado)
rol(id_rol PK, nombre_rol)
permiso(id_permiso PK, nombre_permiso, descripcion)
rol_permiso(id_rol PK/FK, id_permiso PK/FK, fecha_asignacion, estado)
cliente(id_cliente PK/FK→usuario, telefono, direccion)
sucursal(codigo_sucursal PK, id_ciudad FK, direccion, nombre)
ciudad(id_ciudad PK, nombre_ciudad)
empresa(id PK, razon_social, nit, telefono, direccion, logo_url)
proveedor(id_proveedor PK, nombre, telefono, correo, estado)
producto(id_producto PK, id_proveedor FK, id_temporada FK, nombre, descripcion, costo, venta, tipo, talla, color)
inventario(id_inventario PK, codigo_sucursal FK, id_producto FK, cantidad_actual, cantidad_reservada)
kardex(id_kardex PK, id_inventario FK, tipo, cantidad, fecha, motivo)
reserva(codigo_reserva PK, id_cliente FK, codigo_sucursal FK, fecha, horario, estado)
venta(id_venta PK, id_cliente FK, codigo_reserva FK, fecha, total)
detalle_venta(codigo_venta PK/FK, id_producto PK/FK, cantidad, precio_unitario)
transaccion_pago(id_transaccion_pago PK, id_venta FK, tipo, estado, fecha, monto)
```

---

## Instrucciones para el agente

### Al generar código:
1. **Siempre** usar UUID como PK — nunca enteros autoincrementales.
2. **Siempre** separar schema de modelo — no mezclar SQLAlchemy con Pydantic.
3. **Siempre** poner la lógica en `services/`, los endpoints en `routers/` solo llaman al service.
4. **Siempre** manejar excepciones con `HTTPException` y códigos HTTP correctos.
5. **Siempre** hashear passwords con bcrypt antes de persistir.
6. **Nunca** exponer el campo `password` en ningún schema de response.
7. **Verificar permisos** con un decorador/dependencia antes de ejecutar la acción.

### Orden de generación por CU:
1. `models/{entidad}.py` — modelo SQLAlchemy
2. `schemas/{entidad}.py` — schemas Pydantic (Create, Update, Response)
3. `services/{entidad}_service.py` — lógica de negocio
4. `routers/{entidad}_router.py` — endpoints REST
5. Migración Alembic si hay tabla nueva

### Formato de respuesta estándar:
```python
{"status": "success", "data": {...}, "message": "..."}
{"status": "error", "detail": "...", "code": 400}
```