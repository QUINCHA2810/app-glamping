// Base del API FastAPI (backend/) a partir de VITE_API_URL.
//
// Por defecto "/api": ruta relativa al mismo origen de la página. Vite la reenvía a
// http://127.0.0.1:8000 quitando el prefijo (server.proxy en `npm run dev`,
// preview.proxy en `npm run preview` y `npm run remoto`; ver vite.config.js). Así la app
// funciona desde cualquier origen, también desde el celular por Tailscale Serve
// (docs/acceso_celular.md).
//
// Sigue valiendo una URL absoluta (p. ej. VITE_API_URL=http://localhost:8000, como
// antes): entonces el navegador llama directo al backend y hace falta CORS_ORIGINS.
export const API_URL_POR_DEFECTO = '/api'

export function baseApi(valor) {
  const texto = typeof valor === 'string' ? valor.trim() : ''
  return (texto || API_URL_POR_DEFECTO).replace(/\/+$/, '')
}
