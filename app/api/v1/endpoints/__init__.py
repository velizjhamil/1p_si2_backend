# backend/app/api/v1/endpoints/__init__.py
# Paquete de routers desacoplados por caso de uso.
#
# Montaje (main.py):
#   auth       -> /api/v1/auth          (CU1 login, CU2 logout/me)
#   users      -> /api/v1/usuarios      (CU3)
#   roles      -> /api/v1/roles y /permisos (CU4+CU5 catálogos + matriz)
#   company    -> /api/v1/empresa       (CU16)
#   branches   -> /api/v1/sucursales + /ciudades (CU17)
#   suppliers  -> /api/v1/proveedores  (CU23)
#   dashboard  -> /api/v1/dashboard    (métricas de inicio, JWT)
