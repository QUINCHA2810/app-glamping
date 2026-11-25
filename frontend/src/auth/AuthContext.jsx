import { createContext, useCallback, useContext, useEffect, useState } from 'react'
import { apiFetch, getToken, limpiarSesion, TOKEN_KEY } from '../api/client.js'

const AuthContext = createContext(null)

export function AuthProvider({ children }) {
  const [token, setToken] = useState(() => getToken())
  const [usuario, setUsuario] = useState(null)
  // Hay token pero todavía no llega /users/me: se espera antes de decidir
  // si se muestra la app o el login.
  const cargando = Boolean(token) && usuario === null

  const logout = useCallback(() => {
    limpiarSesion()
    setToken(null)
    setUsuario(null)
  }, [])

  useEffect(() => {
    if (!token) return
    let cancelado = false
    apiFetch('/users/me')
      .then((datos) => {
        if (!cancelado) setUsuario(datos)
      })
      .catch(() => {
        if (!cancelado) logout()
      })
    return () => {
      cancelado = true
    }
  }, [token, logout])

  const login = useCallback(async (correo, contrasena) => {
    const { access_token } = await apiFetch('/auth/login', {
      method: 'POST',
      body: { correo, contrasena },
    })
    localStorage.setItem(TOKEN_KEY, access_token)
    setToken(access_token)
  }, [])

  // Después de cambiar la contraseña: el token anterior ya no vale, y la cuenta deja
  // de tener contraseña temporal.
  const renovarSesion = useCallback((accessToken) => {
    localStorage.setItem(TOKEN_KEY, accessToken)
    setUsuario((actual) => (actual ? { ...actual, debe_cambiar_contrasena: false } : actual))
    setToken(accessToken)
  }, [])

  return (
    <AuthContext.Provider value={{ token, usuario, cargando, login, logout, renovarSesion }}>
      {children}
    </AuthContext.Provider>
  )
}

// oxlint-disable-next-line react/only-export-components
export function useAuth() {
  return useContext(AuthContext)
}
