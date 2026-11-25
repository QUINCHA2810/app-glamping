import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { apiFetch } from '../api/client.js'
import { useAuth } from '../auth/AuthContext.jsx'
import { AvisoError, BOTON_PRINCIPAL, Campo, CLASE_CAMPO, CLASE_TARJETA, EncabezadoPagina } from '../components/ui.jsx'

const CONTRASENA_MINIMA = 8
const NOMBRE_ROL = { 'dueño': 'Dueño', administrador: 'Administrador' }

function CambiarContrasena() {
  const { usuario, renovarSesion } = useAuth()
  const navigate = useNavigate()
  const [actual, setActual] = useState('')
  const [nueva, setNueva] = useState('')
  const [repetida, setRepetida] = useState('')
  const [enviando, setEnviando] = useState(false)
  const [error, setError] = useState(null)
  const [listo, setListo] = useState(false)

  async function guardar(evento) {
    evento.preventDefault()
    setListo(false)
    if (!actual) return setError('Escribe tu contraseña actual.')
    if (nueva.length < CONTRASENA_MINIMA) return setError(`La contraseña nueva debe tener al menos ${CONTRASENA_MINIMA} caracteres.`)
    if (nueva !== repetida) return setError('Las dos contraseñas nuevas no coinciden.')

    setEnviando(true)
    setError(null)
    try {
      const { access_token } = await apiFetch('/users/me/contrasena', {
        method: 'POST',
        body: { contrasena_actual: actual, contrasena_nueva: nueva },
      })
      // El cambio cierra todas las sesiones de la cuenta: esta sigue con el token nuevo.
      const eraTemporal = usuario?.debe_cambiar_contrasena
      renovarSesion(access_token)
      // Con la contraseña temporal no había más que esta página: ahora, al panel.
      if (eraTemporal) return navigate('/', { replace: true, state: { contrasenaCambiada: true } })
      setActual('')
      setNueva('')
      setRepetida('')
      setListo(true)
    } catch (e) {
      setError(e.message)
    } finally {
      setEnviando(false)
    }
  }

  return (
    <form
      onSubmit={guardar}
      onChange={() => {
        setError(null)
        setListo(false)
      }}
      noValidate
      className={`${CLASE_TARJETA} space-y-4 p-4 sm:p-5`}
    >
      <div>
        <h2 className="text-sm font-semibold">Cambiar contraseña</h2>
        <p className="mt-1 text-sm text-texto-suave">Se cerrarán tus sesiones abiertas en otros equipos.</p>
      </div>
      <Campo etiqueta="Contraseña actual">
        <input type="password" autoComplete="current-password" value={actual} onChange={(e) => setActual(e.target.value)} className={CLASE_CAMPO} />
      </Campo>
      <Campo etiqueta="Contraseña nueva" ayuda={`Al menos ${CONTRASENA_MINIMA} caracteres.`}>
        <input type="password" autoComplete="new-password" value={nueva} onChange={(e) => setNueva(e.target.value)} className={CLASE_CAMPO} />
      </Campo>
      <Campo etiqueta="Repite la contraseña nueva">
        <input type="password" autoComplete="new-password" value={repetida} onChange={(e) => setRepetida(e.target.value)} className={CLASE_CAMPO} />
      </Campo>
      {error && <AvisoError>{error}</AvisoError>}
      {listo && (
        <p role="status" className="rounded-lg bg-normal-suave px-3 py-2 text-sm text-normal">
          Contraseña cambiada.
        </p>
      )}
      <button type="submit" disabled={enviando} className={`${BOTON_PRINCIPAL} w-full sm:w-auto`}>
        {enviando ? 'Guardando…' : 'Cambiar contraseña'}
      </button>
    </form>
  )
}

export default function MiCuenta() {
  const { usuario } = useAuth()

  return (
    <div className="max-w-xl space-y-6">
      <EncabezadoPagina seccion="Cuenta" titulo="Mi cuenta" />
      <section className={`${CLASE_TARJETA} p-4 sm:p-5`}>
        <h2 className="text-sm font-semibold">Mis datos</h2>
        <dl className="mt-2 space-y-1.5 text-sm">
          <div className="flex flex-wrap justify-between gap-x-3">
            <dt className="text-texto-suave">Nombre</dt>
            <dd className="break-words">{[usuario?.nombre, usuario?.apellido].filter(Boolean).join(' ')}</dd>
          </div>
          <div className="flex flex-wrap justify-between gap-x-3">
            <dt className="text-texto-suave">Correo</dt>
            <dd className="break-all">{usuario?.correo}</dd>
          </div>
          <div className="flex flex-wrap justify-between gap-x-3">
            <dt className="text-texto-suave">Rol</dt>
            <dd>{NOMBRE_ROL[usuario?.rol] ?? usuario?.rol}</dd>
          </div>
        </dl>
      </section>
      <CambiarContrasena />
    </div>
  )
}
