# Frontend — Vatia

Aplicación web (Vite + React + React Router + Tailwind CSS v4) para el
monitoreo y control energético del domo. Consume el API FastAPI de
`backend/`.

## Configuración

1. No hace falta `.env`: el frontend llama al backend por la ruta relativa `/api`,
   que Vite reenvía a `http://127.0.0.1:8000` (`vite.config.js`). Si quieres una URL
   absoluta, copia `.env.example` a `.env` y define `VITE_API_URL`. **Nunca subas
   `.env` a GitHub.**
2. Instala las dependencias:

   ```
   npm install
   ```

Con `/api` no hace falta CORS (mismo origen). Con una `VITE_API_URL` absoluta, el
backend debe permitir el origen del frontend en `CORS_ORIGINS` (por defecto ya
permite `http://localhost:5173`, ver `backend/README.md`).

## Ejecutar

```
npm run dev       # servidor de desarrollo en http://localhost:5173
npm run build     # compila a dist/
npm run preview   # sirve el build compilado
npm run remoto    # build de producción en http://127.0.0.1:4173, para el celular
                  # por Tailscale Serve (ver docs/acceso_celular.md)
```

## Estructura

- `src/api/client.js` — `apiFetch()`: URL base (`src/utils/apiUrl.js`, `/api`
  por defecto), JSON, token Bearer y cierre de sesión automático ante un 401.
- `src/auth/AuthContext.jsx` — login (`POST /auth/login`), perfil
  (`GET /users/me`) y logout.
- `src/domos/DomoContext.jsx` — domos accesibles (`GET /domos`) y domo
  seleccionado (recordado en localStorage).
- `src/components/Layout.jsx` — barra superior y menú lateral.
- `src/pages/` — Login, Panel y secciones pendientes.
