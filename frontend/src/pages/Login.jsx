import { useState } from 'react'
import { Navigate, useNavigate } from 'react-router-dom'
import { useAuth } from '../auth/AuthContext.jsx'
import Logo from '../components/Logo.jsx'
import { AvisoError, BOTON_PRINCIPAL, Campo, CLASE_CAMPO } from '../components/ui.jsx'
import { ESLOGAN, NOMBRE_APP } from '../config/marca.js'

export default function Login() {
  const { token, login } = useAuth()
  const navigate = useNavigate()
  const [correo, setCorreo] = useState('')
  const [contrasena, setContrasena] = useState('')
  const [error, setError] = useState(null)
  const [enviando, setEnviando] = useState(false)

  if (token) return <Navigate to="/" replace />

  async function enviar(evento) {
    evento.preventDefault()
    setError(null)
    setEnviando(true)
    try {
      await login(correo, contrasena)
      navigate('/', { replace: true })
    } catch (e) {
      // Mensaje tal cual lo devuelve el backend: credenciales incorrectas,
      // cuenta desactivada, etc.
      setError(e.message)
    } finally {
      setEnviando(false)
    }
  }

  return (
    <main className="flex min-h-svh flex-col lg:flex-row">
      <section className="flex flex-col justify-between gap-6 bg-marca-fuerte p-6 pt-[calc(1.5rem+env(safe-area-inset-top))] text-white lg:w-2/5 lg:p-12">
        <div className="flex items-center gap-3">
          <Logo className="size-12 rounded-xl ring-2 ring-white/30" />
          <h1 className="text-3xl font-semibold tracking-tight">{NOMBRE_APP}</h1>
        </div>
        <div className="space-y-3">
          <p className="text-2xl leading-tight font-semibold tracking-tight lg:text-4xl">{ESLOGAN}</p>
          <p className="hidden max-w-md text-white/90 lg:block">
            Consulta cuánta energía generas y consumes, y deja que el sistema proteja tu batería apagando primero lo menos
            necesario.
          </p>
        </div>
      </section>

      <section className="flex flex-1 items-center justify-center p-6 pb-[calc(1.5rem+env(safe-area-inset-bottom))]">
        <form onSubmit={enviar} className="w-full max-w-sm space-y-4">
          <h2 className="text-xl font-semibold tracking-tight">Inicia sesión</h2>
          <Campo etiqueta="Correo">
            <input
              type="email"
              autoComplete="email"
              required
              value={correo}
              onChange={(e) => setCorreo(e.target.value)}
              className={CLASE_CAMPO}
            />
          </Campo>
          <Campo etiqueta="Contraseña">
            <input
              type="password"
              autoComplete="current-password"
              required
              value={contrasena}
              onChange={(e) => setContrasena(e.target.value)}
              className={CLASE_CAMPO}
            />
          </Campo>

          {error && <AvisoError>{error}</AvisoError>}

          <button type="submit" disabled={enviando} className={`${BOTON_PRINCIPAL} w-full`}>
            {enviando ? 'Ingresando…' : 'Iniciar sesión'}
          </button>
        </form>
      </section>
    </main>
  )
}
