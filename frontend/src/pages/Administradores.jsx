import { useCallback, useState } from 'react'
import { Navigate } from 'react-router-dom'
import { apiFetch } from '../api/client.js'
import { useAuth } from '../auth/AuthContext.jsx'
import Dialogo, { DialogoConfirmacion } from '../components/Dialogo.jsx'
import {
  AvisoError,
  BOTON_PRINCIPAL,
  BOTON_SECUNDARIO,
  Campo,
  CLASE_CAMPO,
  CLASE_TARJETA,
  EncabezadoPagina,
  Etiqueta,
  Vacio,
} from '../components/ui.jsx'
import { useDomo } from '../domos/DomoContext.jsx'
import { usePolling } from '../hooks/usePolling.js'
import { nombreEnSelector } from '../utils/demostracion.js'
import { domosDelDueno } from '../utils/roles.js'

const INTERVALO_MS = 30000
const CONTRASENA_MINIMA = 8

function nombreCompleto(admin) {
  return [admin.nombre, admin.apellido].filter(Boolean).join(' ')
}

function domosActivos(admin) {
  return admin.domos.filter((d) => d.estado_asignacion === 'activo')
}

// `domos`: los domos donde el usuario es dueño. La cuenta nueva queda asignada al
// elegido (POST /auth/registro exige domo_id desde el modelo v2).
function FormularioCrear({ domos, domoInicial, onCerrar, onCreado }) {
  const [domoId, setDomoId] = useState(() => String(domos.some((d) => d.id === domoInicial) ? domoInicial : (domos[0]?.id ?? '')))
  const [nombre, setNombre] = useState('')
  const [correo, setCorreo] = useState('')
  const [contrasena, setContrasena] = useState('')
  const [enviando, setEnviando] = useState(false)
  const [error, setError] = useState(null)

  async function crear(evento) {
    evento.preventDefault()
    if (!nombre.trim() || !correo.trim()) {
      setError('Escribe el nombre y el correo.')
      return
    }
    if (contrasena.length < CONTRASENA_MINIMA) {
      setError(`La contraseña inicial debe tener al menos ${CONTRASENA_MINIMA} caracteres.`)
      return
    }
    if (!domoId) {
      setError('Elige el domo que va a administrar.')
      return
    }
    setEnviando(true)
    setError(null)
    try {
      await apiFetch('/auth/registro', {
        method: 'POST',
        body: { nombre: nombre.trim(), correo: correo.trim(), contrasena, domo_id: Number(domoId) },
      })
      onCreado()
      onCerrar()
    } catch (e) {
      setError(e.message)
      setEnviando(false)
    }
  }

  return (
    <Dialogo titulo="Crear administrador" onCerrar={onCerrar}>
      <form onSubmit={crear} onChange={() => setError(null)} noValidate className="space-y-4">
        <Campo etiqueta="Nombre">
          <input autoFocus autoComplete="off" value={nombre} onChange={(e) => setNombre(e.target.value)} className={CLASE_CAMPO} />
        </Campo>
        <Campo etiqueta="Correo">
          <input type="email" autoComplete="off" value={correo} onChange={(e) => setCorreo(e.target.value)} className={CLASE_CAMPO} />
        </Campo>
        <Campo
          etiqueta="Contraseña inicial"
          ayuda={`Al menos ${CONTRASENA_MINIMA} caracteres. Compártela con el administrador: después no se puede volver a ver, y al entrar deberá cambiarla.`}
        >
          <input
            type="password"
            autoComplete="new-password"
            value={contrasena}
            onChange={(e) => setContrasena(e.target.value)}
            className={CLASE_CAMPO}
          />
        </Campo>
        <Campo etiqueta="Domo" ayuda="El domo que va a administrar. Después puedes asignarle otros.">
          {domos.length === 0 ? (
            <p className="text-sm text-texto-suave">No eres dueño de ningún domo: no hay a cuál asignarlo.</p>
          ) : (
            <select value={domoId} onChange={(e) => setDomoId(e.target.value)} className={CLASE_CAMPO}>
              {domos.map((domo) => (
                <option key={domo.id} value={domo.id}>
                  {nombreEnSelector(domo)}
                </option>
              ))}
            </select>
          )}
        </Campo>
        {error && <AvisoError>{error}</AvisoError>}
        <div className="flex flex-col-reverse gap-2 sm:flex-row sm:justify-end">
          <button type="button" onClick={onCerrar} className={BOTON_SECUNDARIO}>
            Cancelar
          </button>
          <button type="submit" disabled={enviando || domos.length === 0} className={BOTON_PRINCIPAL}>
            {enviando ? 'Creando…' : 'Crear administrador'}
          </button>
        </div>
      </form>
    </Dialogo>
  )
}

function DatosCuenta({ admin, onActualizado }) {
  const [nombre, setNombre] = useState(admin.nombre)
  const [apellido, setApellido] = useState(admin.apellido ?? '')
  const [telefono, setTelefono] = useState(admin.telefono ?? '')
  const [enviando, setEnviando] = useState(false)
  const [error, setError] = useState(null)

  async function guardar(evento) {
    evento.preventDefault()
    if (!nombre.trim()) {
      setError('Escribe el nombre.')
      return
    }
    setEnviando(true)
    setError(null)
    try {
      await apiFetch(`/administradores/${admin.id}`, {
        method: 'PATCH',
        body: { nombre: nombre.trim(), apellido: apellido.trim() || null, telefono: telefono.trim() || null },
      })
      onActualizado()
    } catch (e) {
      setError(e.message)
    } finally {
      setEnviando(false)
    }
  }

  return (
    <form onSubmit={guardar} onChange={() => setError(null)} noValidate className="space-y-3">
      <h3 className="text-sm font-semibold">Datos</h3>
      <div className="grid gap-3 sm:grid-cols-2">
        <Campo etiqueta="Nombre">
          <input value={nombre} onChange={(e) => setNombre(e.target.value)} className={CLASE_CAMPO} />
        </Campo>
        <Campo etiqueta="Apellido">
          <input value={apellido} onChange={(e) => setApellido(e.target.value)} className={CLASE_CAMPO} />
        </Campo>
      </div>
      <Campo etiqueta="Teléfono">
        <input type="tel" value={telefono} onChange={(e) => setTelefono(e.target.value)} className={CLASE_CAMPO} />
      </Campo>
      {error && <AvisoError>{error}</AvisoError>}
      <button type="submit" disabled={enviando} className={`${BOTON_SECUNDARIO} w-full sm:w-auto`}>
        {enviando ? 'Guardando…' : 'Guardar datos'}
      </button>
    </form>
  )
}

function DomosDelAdministrador({ admin, domos, onActualizado }) {
  const [enviando, setEnviando] = useState(null)
  const [error, setError] = useState(null)
  const activos = new Set(domosActivos(admin).map((d) => d.domo_id))

  async function cambiar(domoId, asignar) {
    setEnviando(domoId)
    setError(null)
    try {
      if (asignar) {
        await apiFetch(`/domos/${domoId}/administradores`, { method: 'POST', body: { correo: admin.correo } })
      } else {
        await apiFetch(`/domos/${domoId}/administradores/${admin.id}`, { method: 'PATCH', body: { estado_asignacion: 'inactivo' } })
      }
      onActualizado()
    } catch (e) {
      setError(e.message)
    } finally {
      setEnviando(null)
    }
  }

  return (
    <section className="space-y-2">
      <h3 className="text-sm font-semibold">Domos</h3>
      {domos.length === 0 ? (
        <p className="text-sm text-texto-suave">No tienes domos para asignar.</p>
      ) : (
        <ul className="divide-y divide-borde rounded-lg border border-borde">
          {domos.map((domo) => {
            const asignado = activos.has(domo.id)
            return (
              <li key={domo.id} className="flex items-center justify-between gap-3 px-3 py-2 text-sm">
                <span className="min-w-0">
                  <span className="block font-medium break-words">{domo.nombre}</span>
                  <span className="block text-xs text-texto-suave">{asignado ? 'Asignado' : 'Sin acceso'}</span>
                </span>
                <button
                  type="button"
                  disabled={enviando !== null}
                  onClick={() => cambiar(domo.id, !asignado)}
                  aria-label={`${asignado ? 'Quitar' : 'Asignar'} ${domo.nombre}`}
                  className={`${asignado ? BOTON_SECUNDARIO : BOTON_PRINCIPAL} shrink-0`}
                >
                  {enviando === domo.id ? 'Guardando…' : asignado ? 'Quitar' : 'Asignar'}
                </button>
              </li>
            )
          })}
        </ul>
      )}
      {error && <AvisoError>{error}</AvisoError>}
    </section>
  )
}

function RestablecerContrasena({ admin }) {
  const [abierto, setAbierto] = useState(false)
  const [contrasena, setContrasena] = useState('')
  const [enviando, setEnviando] = useState(false)
  const [error, setError] = useState(null)
  const [listo, setListo] = useState(false)

  async function restablecer(evento) {
    evento.preventDefault()
    if (contrasena.length < CONTRASENA_MINIMA) {
      setError(`La contraseña temporal debe tener al menos ${CONTRASENA_MINIMA} caracteres.`)
      return
    }
    setEnviando(true)
    setError(null)
    try {
      await apiFetch(`/administradores/${admin.id}/restablecer-contrasena`, {
        method: 'POST',
        body: { contrasena_temporal: contrasena },
      })
      setContrasena('')
      setAbierto(false)
      setListo(true)
    } catch (e) {
      setError(e.message)
    } finally {
      setEnviando(false)
    }
  }

  return (
    <section className="space-y-2 border-t border-borde pt-4">
      <h3 className="text-sm font-semibold">Contraseña</h3>
      {listo && (
        <p role="status" className="rounded-lg bg-normal-suave px-3 py-2 text-sm text-normal">
          Contraseña restablecida. Sus sesiones abiertas se cerraron.
        </p>
      )}
      {abierto ? (
        <form onSubmit={restablecer} onChange={() => setError(null)} noValidate className="space-y-3">
          <Campo
            etiqueta="Contraseña temporal"
            ayuda={`Al menos ${CONTRASENA_MINIMA} caracteres. Compártela con el administrador: después no se puede volver a ver, y al entrar deberá cambiarla.`}
          >
            <input
              type="password"
              autoComplete="new-password"
              value={contrasena}
              onChange={(e) => setContrasena(e.target.value)}
              className={CLASE_CAMPO}
            />
          </Campo>
          {error && <AvisoError>{error}</AvisoError>}
          <div className="flex flex-col-reverse gap-2 sm:flex-row">
            <button type="button" onClick={() => setAbierto(false)} className={BOTON_SECUNDARIO}>
              Cancelar
            </button>
            <button type="submit" disabled={enviando} className={BOTON_PRINCIPAL}>
              {enviando ? 'Guardando…' : 'Restablecer y cerrar sus sesiones'}
            </button>
          </div>
        </form>
      ) : (
        <button
          type="button"
          onClick={() => {
            setListo(false)
            setAbierto(true)
          }}
          className={`${BOTON_SECUNDARIO} w-full sm:w-auto`}
        >
          Restablecer contraseña
        </button>
      )}
    </section>
  )
}

function AccesoCuenta({ admin, onActualizado }) {
  const [confirmando, setConfirmando] = useState(false)
  const [enviando, setEnviando] = useState(false)
  const [error, setError] = useState(null)
  const activa = admin.estado_cuenta === 'activo'

  async function cambiar(estado) {
    setEnviando(true)
    setError(null)
    try {
      await apiFetch(`/administradores/${admin.id}`, { method: 'PATCH', body: { estado_cuenta: estado } })
      setConfirmando(false)
      onActualizado()
    } catch (e) {
      setError(e.message)
      setConfirmando(false)
    } finally {
      setEnviando(false)
    }
  }

  return (
    <section className="space-y-2 border-t border-borde pt-4">
      <h3 className="text-sm font-semibold">Acceso</h3>
      {activa ? (
        <button type="button" onClick={() => setConfirmando(true)} className={`${BOTON_SECUNDARIO} w-full text-supervivencia sm:w-auto`}>
          Desactivar acceso
        </button>
      ) : (
        <button type="button" disabled={enviando} onClick={() => cambiar('activo')} className={`${BOTON_PRINCIPAL} w-full sm:w-auto`}>
          {enviando ? 'Activando…' : 'Activar acceso'}
        </button>
      )}
      {error && <AvisoError>{error}</AvisoError>}
      {confirmando && (
        <DialogoConfirmacion
          titulo={`¿Desactivar el acceso de ${nombreCompleto(admin)}?`}
          textoConfirmar="Desactivar acceso"
          peligro
          ocupado={enviando}
          onConfirmar={() => cambiar('inactivo')}
          onCancelar={() => setConfirmando(false)}
        >
          <p>No podrá iniciar sesión, y si tiene una sesión abierta dejará de funcionar de inmediato.</p>
          <p>Sus domos y su historial se conservan, y podrás activarlo de nuevo.</p>
        </DialogoConfirmacion>
      )}
    </section>
  )
}

function PanelAdministrador({ admin, domos, onCerrar, onActualizado }) {
  return (
    <Dialogo titulo={nombreCompleto(admin)} onCerrar={onCerrar}>
      <p className="text-sm break-words text-texto-suave">{admin.correo}</p>
      {admin.cuenta_propia && <DatosCuenta admin={admin} onActualizado={onActualizado} />}
      <DomosDelAdministrador admin={admin} domos={domos} onActualizado={onActualizado} />
      {admin.cuenta_propia ? (
        <>
          <RestablecerContrasena admin={admin} />
          <AccesoCuenta admin={admin} onActualizado={onActualizado} />
        </>
      ) : (
        <p className="border-t border-borde pt-4 text-sm text-texto-suave">
          Esta cuenta no figura como creada por ti: puedes asignarle o quitarle tus domos, pero no editarla, cambiar su contraseña
          ni desactivarla.
        </p>
      )}
    </Dialogo>
  )
}

function TarjetaAdministrador({ admin, onGestionar }) {
  const activos = domosActivos(admin)
  const activa = admin.estado_cuenta === 'activo'

  return (
    <li className={`${CLASE_TARJETA} flex min-w-0 flex-col gap-3 p-4`}>
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div className="min-w-0">
          <h2 className="font-medium break-words">{nombreCompleto(admin)}</h2>
          <p className="text-sm break-all text-texto-suave">{admin.correo}</p>
        </div>
        <Etiqueta tono={activa ? 'verde' : 'neutro'}>{activa ? 'Activo' : 'Desactivado'}</Etiqueta>
      </div>
      <div className="flex-1">
        <p className="text-xs font-medium text-texto-suave">Domos asignados</p>
        {activos.length === 0 ? (
          <p className="text-sm text-texto-suave">Ninguno</p>
        ) : (
          <ul className="mt-1 flex flex-wrap gap-1.5">
            {activos.map((d) => (
              <li key={d.domo_id}>
                <Etiqueta>{d.nombre_domo}</Etiqueta>
              </li>
            ))}
          </ul>
        )}
      </div>
      <button type="button" onClick={onGestionar} aria-label={`Gestionar a ${nombreCompleto(admin)}`} className={BOTON_SECUNDARIO}>
        Gestionar
      </button>
    </li>
  )
}

function ContenidoAdministradores() {
  const { usuario } = useAuth()
  const { domos: accesibles, domoId } = useDomo()
  // Solo los domos donde es dueño: en uno que solo administra no puede asignar a nadie.
  const domos = domosDelDueno(accesibles, usuario)
  const [creando, setCreando] = useState(false)
  const [abiertoId, setAbiertoId] = useState(null)

  const consultar = useCallback(() => apiFetch('/administradores'), [])
  const { datos, error, refrescar } = usePolling(consultar, INTERVALO_MS)
  const abierto = datos?.find((a) => a.id === abiertoId)

  return (
    <div className="space-y-4">
      <button type="button" onClick={() => setCreando(true)} className={`${BOTON_PRINCIPAL} w-full sm:w-auto`}>
        Crear administrador
      </button>
      {error && <AvisoError>No se pudieron cargar los administradores: {error}</AvisoError>}
      {!datos ? (
        !error && <div className={`${CLASE_TARJETA} h-40 animate-pulse`} />
      ) : datos.length === 0 ? (
        <Vacio>Todavía no hay administradores.</Vacio>
      ) : (
        <ul className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-3">
          {datos.map((admin) => (
            <TarjetaAdministrador key={admin.id} admin={admin} onGestionar={() => setAbiertoId(admin.id)} />
          ))}
        </ul>
      )}
      {creando && <FormularioCrear domos={domos} domoInicial={domoId} onCerrar={() => setCreando(false)} onCreado={refrescar} />}
      {abierto && (
        <PanelAdministrador
          key={abierto.id}
          admin={abierto}
          domos={domos}
          onCerrar={() => setAbiertoId(null)}
          onActualizado={refrescar}
        />
      )}
    </div>
  )
}

export default function Administradores() {
  const { usuario } = useAuth()
  if (usuario?.rol !== 'dueño') return <Navigate to="/" replace />

  return (
    <div className="space-y-6">
      <EncabezadoPagina seccion="Cuentas" titulo="Administradores" />
      <ContenidoAdministradores />
    </div>
  )
}
