# Publicación en Internet (Tailscale Funnel)

Con **Tailscale Funnel** la app queda en Internet: cualquiera con la URL
`https://<equipo>.<tailnet>.ts.net` llega a la pantalla de inicio de sesión, sin
instalar Tailscale. Es el mismo montaje de un solo origen de `docs/acceso_celular.md`
(Serve, solo para la tailnet); lo único que cambia es quién puede llegar a la URL. Por
eso antes de encenderlo el backend se arranca en **modo público**.

```
navegador ──https──> <equipo>.<tailnet>.ts.net ──Funnel──> 127.0.0.1:4173 (vite preview)
                                                              ├─ /           → la app (dist/)
                                                              └─ /api/...    → 127.0.0.1:8000 (uvicorn), sin el /api
```

## Qué queda expuesto y qué no

| Expuesto (por la URL de Funnel) | NO expuesto |
| --- | --- |
| La app compilada (`frontend/dist`) | El puerto 8000 del backend (escucha solo en `127.0.0.1`) |
| `/api/...`: los endpoints del backend, todos con JWT salvo `/auth/login`, `/` y `/health` | `/docs`, `/redoc` y `/openapi.json` (404 con `API_DOCS=false`) |
| La pantalla de inicio de sesión | Mosquitto (1883), el ESP32 y la red del domo (192.168.0.x) |
| | La base de datos (Supabase): solo la usa el backend desde el portátil |

Solo `/api` y `/api/...` pasan al backend; cualquier otra ruta la responde Vite con la app
(`frontend/vite.config.js`). Vite solo acepta el Host `*.ts.net` (además de `localhost`).

Protecciones que trae el modo público:

- **Documentación cerrada**: con `API_DOCS=false` FastAPI no publica `/docs`, `/redoc`
  ni `/openapi.json`.
- **Secreto JWT**: con `API_DOCS=false`, si `JWT_SECRET_KEY` tiene menos de 32
  caracteres el backend **no arranca** (en desarrollo solo registra un ERROR). Genera uno
  con `python -c "import secrets; print(secrets.token_hex(32))"` y ponlo en `backend/.env`.
  Cambiarlo cierra todas las sesiones abiertas.
- **Límite de intentos de inicio de sesión** (`backend/limite_login.py`): como máximo 5
  intentos fallidos en 15 minutos por IP y por correo; después, 429 con "Demasiados
  intentos fallidos…", sin decir si el correo existe. Un inicio correcto borra el contador
  de ese correo.
- **Cabeceras de seguridad** en el backend y en Vite: `X-Content-Type-Options: nosniff`,
  `Referrer-Policy: same-origin`, `X-Frame-Options: DENY` y `Permissions-Policy` sin
  cámara, micrófono, ubicación, pagos ni USB. HSTS lo pone Funnel.
- **CORS**: no hace falta (un solo origen). El backend solo permite los orígenes de
  `CORS_ORIGINS` (por defecto `http://localhost:5173`), sin `*` y sin credenciales.

### ⚠️ Riesgo: bloqueo por correo

El límite por correo lo puede disparar **cualquiera** que conozca un correo de la app:
con 5 contraseñas malas, esa cuenta queda sin poder entrar durante **15 minutos**, aunque
luego se escriba la contraseña correcta. Se desbloquea **sola a los 15 minutos** del
primer fallo de la ventana, o **reiniciando el backend** (los contadores viven en
memoria). Por eso conviene no publicar el correo del dueño.

### La IP en el registro

Cada intento fallido deja una línea INFO en la consola de uvicorn:

```
INFO:     Inicio de sesión fallido desde 203.0.113.7
INFO:     Inicio de sesión fallido desde sin IP real
```

Detrás de Funnel la petición le llega al backend desde Vite (127.0.0.1), así que la IP
se toma de `X-Forwarded-For`: la entrada más a la derecha que no sea 127.0.0.1/::1 (lo que
el cliente escriba en esa cabecera queda más a la izquierda y no se usa). Si sale
**"sin IP real"**, Funnel no está pasando la IP del visitante: entonces solo se aplica el
límite por correo. Haz una prueba con una contraseña mala desde el celular con datos
móviles (fuera de la tailnet) y mira qué aparece.

## Pasos

1. **Backend en modo público**, desde `backend/` con el entorno virtual activo:

   ```powershell
   $env:API_DOCS="false"
   uvicorn main:app
   ```

   No uses `--host 0.0.0.0` ni `--reload`. Si se niega a arrancar por el secreto JWT,
   cámbialo en `backend/.env` (ver arriba). Comprueba que `http://127.0.0.1:8000/docs`
   da 404.

2. **Listener**, desde `backend/`: `python mqtt_listener.py`.

3. **Frontend**, desde `frontend/`: `npm run publico` (compila con `--mode remoto` y sirve
   en `http://127.0.0.1:4173`, igual que `npm run remoto`).

4. **Encender Funnel** (si Serve estaba activo, `tailscale serve reset` antes):

   ```
   tailscale funnel --bg --https=443 http://127.0.0.1:4173
   ```

   La primera vez Tailscale puede pedir habilitar Funnel para el equipo en la consola
   (atributo `funnel` en la política de la tailnet).

5. **Ver el estado y la URL**:

   ```
   tailscale funnel status
   ```

   Abre la URL desde un equipo fuera de la tailnet y comprueba que `<URL>/api/docs` da 404.

6. **Apagarlo** al terminar la presentación:

   ```
   tailscale funnel --https=443 off
   ```

   La app sigue funcionando en el portátil. Comprueba con `tailscale funnel status` que ya
   no hay nada publicado.

El portátil debe seguir encendido y sin suspensión mientras Funnel esté activo
(`docs/acceso_celular.md`, "Requisitos").

## Recomendación: cuenta de «jurado»

No compartas la cuenta del dueño. Crea una cuenta solo para la revisión:

- Desde la app, como dueño: **Administradores → Crear administrador**, con el **Domo
  demostración** en el selector "Domo". La cuenta nace con rol **administrador** y solo
  con ese domo asignado (no se le asigna ningún domo real).
- Nace con contraseña temporal: entra una vez con ella, cámbiala en **Mi cuenta** y
  entrega esa contraseña al jurado.
- El Domo demostración tiene datos sintéticos y sus dispositivos no tienen `id_fisico`:
  nada de lo que haga el jurado llega a una toma o un bombillo real.
- Al terminar, desactívala en **Administradores** (corta el acceso al instante, también
  en sesiones abiertas) y apaga Funnel.
