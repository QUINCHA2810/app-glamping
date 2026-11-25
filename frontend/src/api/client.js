// Cliente HTTP del API FastAPI (backend/). Todas las peticiones de la app
// pasan por apiFetch para que la URL base, el JSON, el token y el manejo de
// la sesión expirada estén en un solo lugar.

import { baseApi } from '../utils/apiUrl.js'

// "/api" por defecto (mismo origen, vía el proxy de Vite); ver utils/apiUrl.js.
const API_URL = baseApi(import.meta.env.VITE_API_URL)

export const TOKEN_KEY = 'token'
export const DOMO_KEY = 'domoId'

export function getToken() {
  return localStorage.getItem(TOKEN_KEY)
}

export function limpiarSesion() {
  localStorage.removeItem(TOKEN_KEY)
  localStorage.removeItem(DOMO_KEY)
}

// FastAPI devuelve `detail` como texto en los HTTPException propios, o como
// lista de errores de validación (422).
function mensajeDeError(cuerpo, status) {
  const detail = cuerpo?.detail
  if (typeof detail === 'string') return detail
  // El backend ya los envía en español (backend/errores_validacion.py).
  if (Array.isArray(detail)) return detail.map((e) => e.msg).join(' · ')
  return `Error ${status} al comunicarse con el servidor.`
}

export async function apiFetch(ruta, opciones = {}) {
  const { body, headers, ...resto } = opciones
  const token = getToken()

  let respuesta
  try {
    respuesta = await fetch(`${API_URL}${ruta}`, {
      ...resto,
      headers: {
        'Content-Type': 'application/json',
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
        ...headers,
      },
      body: body !== undefined ? JSON.stringify(body) : undefined,
    })
  } catch {
    throw new Error('No se pudo conectar con el servidor.')
  }

  const texto = await respuesta.text()
  let cuerpo = null
  try {
    cuerpo = texto ? JSON.parse(texto) : null
  } catch {
    cuerpo = null // p. ej. un 500 con cuerpo en texto plano
  }

  // 401 con sesión activa = token vencido o inválido: se cierra la sesión.
  // Sin token (p. ej. el propio login fallido) solo se propaga el error.
  if (respuesta.status === 401 && token) {
    limpiarSesion()
    if (window.location.pathname !== '/login') window.location.assign('/login')
  }

  if (!respuesta.ok) throw new Error(mensajeDeError(cuerpo, respuesta.status))

  return cuerpo
}
