# Acceso desde el celular (Tailscale Serve)

Abrir la app desde el celular fuera de la red del domo, **solo dentro de la red privada
de Tailscale** (la tailnet). Tailscale Serve publica en la tailnet una URL
`https://<equipo>.<tailnet>.ts.net` que reenvía al puerto 4173 de este portátil, donde
`npm run remoto` sirve el build de producción del frontend.

## Cómo funciona: un solo origen

El celular solo conoce la URL de `ts.net`, así que todo pasa por ella:

```
celular ──https──> <equipo>.<tailnet>.ts.net ──Tailscale Serve──> 127.0.0.1:4173 (vite preview)
                                                                      ├─ /            → la app (dist/)
                                                                      └─ /api/...     → 127.0.0.1:8000 (uvicorn), sin el /api
```

- El frontend llama al backend por la ruta relativa `/api/...`
  (`frontend/src/utils/apiUrl.js`). Vite la reenvía a `http://127.0.0.1:8000` y le
  quita el prefijo `/api` (`frontend/vite.config.js`, `server.proxy` y `preview.proxy`).
  No hace falta CORS: la página y el API tienen el mismo origen.
- Vite solo acepta peticiones con Host `localhost`, `127.0.0.1` o `*.ts.net`
  (`allowedHosts: ['.ts.net']`). Cualquier otro Host recibe 403.
- `npm run remoto` compila con `--mode remoto`, que lee `frontend/.env.remoto`
  (`VITE_API_URL=/api`). Ese archivo tiene prioridad sobre `frontend/.env`, así que un
  `VITE_API_URL=http://localhost:8000` en `.env` no se cuela en el build del celular.
- **No se expone nada más**: el backend (8000) escucha solo en `127.0.0.1`, y Tailscale
  Serve solo reenvía el 443 de la tailnet al 4173. Mosquitto no pasa por Serve.

## Requisitos

1. El portátil **encendido y sin suspensión** (en Windows: Configuración → Sistema →
   Energía → "Suspender" en *Nunca* mientras esté enchufado). Si se suspende, la app deja
   de responder en el celular.
2. Tailscale instalado y con sesión iniciada en el portátil y en el celular, en la misma
   tailnet. (En la consola de Tailscale, HTTPS debe estar habilitado para la tailnet:
   Serve lo pide la primera vez.)
3. Corriendo en el portátil, cada uno en su terminal:
   - el backend, desde `backend/`: `uvicorn main:app` (escucha en `127.0.0.1:8000`).
     No uses `--host 0.0.0.0`. `python main.py` también sirve: usa `API_HOST`, que por
     defecto es `127.0.0.1`;
   - el listener, desde `backend/`: `python mqtt_listener.py`;
   - el frontend, desde `frontend/`: `npm run remoto` (compila y sirve en
     `http://127.0.0.1:4173`; `--strictPort` hace que falle si el puerto está ocupado en
     vez de cambiar a otro).

## Encender el acceso

Con lo anterior corriendo, en el portátil:

```
tailscale serve --bg --https=443 http://127.0.0.1:4173
```

`--bg` lo deja activo en segundo plano (también después de cerrar la terminal y de
reiniciar Tailscale). `tailscale serve status` muestra la URL. Ábrela en el celular e
inicia sesión una vez: la sesión se guarda en el navegador del celular para esa URL.

## Apagarlo

```
tailscale serve reset
```

Quita toda la configuración de Serve. La app sigue funcionando en el portátil.

## ⚠️ `tailscale funnel` es otra cosa

`tailscale funnel` publica la URL **en Internet, para cualquiera**, no solo en tu
tailnet: el inicio de sesión y el control de los dispositivos del domo quedan expuestos.
Para el celular usa siempre `tailscale serve`. Si de verdad hay que publicarla (por
ejemplo, para el jurado), sigue `docs/publicacion_web.md`: el backend debe arrancar antes
en modo público (`API_DOCS=false`).

## En el portátil, todo sigue igual

- `npm run dev` → `http://localhost:5173`, con el mismo proxy `/api`.
- Si `frontend/.env` tiene `VITE_API_URL=http://localhost:8000` (como decía antes
  `.env.example`), el modo dev sigue llamando directo al backend, como siempre, y
  necesita `CORS_ORIGINS` (por defecto ya permite `http://localhost:5173`). Si quitas o
  comentas esa línea, pasa por el proxy. El build de `npm run remoto` usa `/api` en los
  dos casos.

## Nota sobre la tailnet

Un servicio del portátil que escuche en `0.0.0.0` (todas las interfaces) también es
alcanzable desde **tus propios** equipos de la tailnet por la IP de Tailscale del
portátil (100.x.y.z), aunque Serve no lo publique. No es público, pero por eso el
backend escucha solo en `127.0.0.1`. Mosquitto escucha en la red del domo para el ESP32:
si no quieres que tus otros equipos de la tailnet lo alcancen, limítalo con el firewall
de Windows o con `listener 1883 <IP del portátil en la red del domo>` en su configuración.

## Si algo falla

| Síntoma en el celular | Causa probable |
| --- | --- |
| La URL no carga | El portátil está suspendido, `npm run remoto` no corre o Serve está apagado (`tailscale serve status`). |
| La app carga pero dice "No se pudo conectar con el servidor" | uvicorn no está corriendo en `127.0.0.1:8000`. |
| "Blocked request. This host is not allowed" | El Host no termina en `.ts.net` (p. ej. se usó la IP 100.x directamente). Usa la URL de `ts.net`. |
| `npm run remoto` falla con "Port 4173 is already in use" | Ya hay otro `vite preview` abierto: ciérralo. |
