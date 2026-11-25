import tailwindcss from '@tailwindcss/vite'
import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'
import { ESLOGAN, NOMBRE_APP } from './src/config/marca.js'

// index.html no puede importar JS: reemplaza sus marcadores con la marca. Corre
// antes del reemplazo de variables de Vite, que avisaría de estos marcadores.
function marcaEnHtml() {
  return {
    name: 'marca-en-html',
    transformIndexHtml: {
      order: 'pre',
      handler: (html) => html.replaceAll('%NOMBRE_APP%', NOMBRE_APP).replaceAll('%ESLOGAN%', ESLOGAN),
    },
  }
}

// Un solo origen: el frontend llama a /api/... (src/utils/apiUrl.js) y Vite lo reenvía
// al backend, que escucha solo en 127.0.0.1:8000. Las rutas del backend no llevan /api:
// el proxy lo quita. El mismo proxy sirve en `npm run dev` y en `npm run preview` /
// `npm run remoto` / `npm run publico` (docs/acceso_celular.md, docs/publicacion_web.md).
// La clave empieza con ^, así que Vite la toma como expresión regular: solo /api y
// /api/... llegan al backend; /apix u otra ruta no. xfwd agrega X-Forwarded-For con la
// IP que ve el proxy (la que pone Funnel queda a su izquierda): el backend la usa para
// el límite de intentos de inicio de sesión (backend/limite_login.py).
const PROXY_API = {
  '^/api(/|$)': {
    target: 'http://127.0.0.1:8000',
    rewrite: (ruta) => ruta.replace(/^\/api(?=\/|$)/, '') || '/',
    xfwd: true,
  },
}

// Las mismas que pone el backend (backend/publicacion.py). HSTS lo pone Funnel.
const CABECERAS_SEGURIDAD = {
  'X-Content-Type-Options': 'nosniff',
  'Referrer-Policy': 'same-origin',
  'X-Frame-Options': 'DENY',
  'Permissions-Policy': 'camera=(), microphone=(), geolocation=(), payment=(), usb=()',
}

// Hosts aceptados además de localhost y 127.0.0.1 (que Vite siempre acepta): los
// <equipo>.<tailnet>.ts.net de Tailscale Serve. NUNCA `allowedHosts: true`.
const HOSTS_PERMITIDOS = ['.ts.net']

// https://vite.dev/config/
export default defineConfig({
  plugins: [react(), tailwindcss(), marcaEnHtml()],
  server: { proxy: PROXY_API, allowedHosts: HOSTS_PERMITIDOS },
  preview: { proxy: PROXY_API, allowedHosts: HOSTS_PERMITIDOS, headers: CABECERAS_SEGURIDAD },
})
