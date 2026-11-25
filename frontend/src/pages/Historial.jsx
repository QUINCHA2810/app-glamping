import { useCallback, useState } from 'react'
import { apiFetch } from '../api/client.js'
import PaginaDomo from '../components/PaginaDomo.jsx'
import { AvisoError, Campo, CLASE_CAMPO, Etiqueta, Vacio } from '../components/ui.jsx'
import { useConsulta } from '../hooks/useConsulta.js'
import { usePolling } from '../hooks/usePolling.js'
import { valorLocalAIso } from '../utils/fechas.js'
import { aFecha, formatoFechaHora } from '../utils/formato.js'

const INTERVALO_MS = 15000
const LIMITE = 200

const ROLES = [
  { valor: '', nombre: 'Todos' },
  { valor: 'sistema', nombre: 'Sistema (R-13)' },
  { valor: 'dueño', nombre: 'Dueño' },
  { valor: 'administrador', nombre: 'Administrador' },
]

const NOMBRE_ACCION = { encender: 'Encender', apagar: 'Apagar' }

function Quien({ fila }) {
  if (fila.rol === 'sistema') return <Etiqueta tono="ambar">Sistema (R-13)</Etiqueta>
  return (
    <span className="inline-flex flex-wrap items-center gap-1.5">
      <span>{fila.nombre_usuario ?? '—'}</span>
      {fila.rol && <Etiqueta>{fila.rol === 'dueño' ? 'Dueño' : fila.rol === 'administrador' ? 'Administrador' : fila.rol}</Etiqueta>}
    </span>
  )
}

function Accion({ fila }) {
  const accion = fila.accion_realizada
  return (
    <Etiqueta tono={accion === 'apagar' ? 'neutro' : accion === 'encender' ? 'verde' : 'azul'}>
      {NOMBRE_ACCION[accion] ?? accion ?? '—'}
    </Etiqueta>
  )
}

function Cambio({ fila }) {
  return (
    <span className="whitespace-nowrap">
      {fila.valor_anterior ?? '—'} <span className="text-texto-suave">→</span> {fila.valor_nuevo ?? '—'}
    </span>
  )
}

function Filtros({ filtros, dispositivos, onCambiar }) {
  const cambiar = (campo) => (e) => onCambiar({ ...filtros, [campo]: e.target.value })
  return (
    <section className="grid gap-3 rounded-2xl border border-borde bg-superficie p-4 sm:grid-cols-2 lg:grid-cols-4">
      <Campo etiqueta="Quién">
        <select value={filtros.rol} onChange={cambiar('rol')} className={CLASE_CAMPO}>
          {ROLES.map((r) => (
            <option key={r.valor} value={r.valor}>
              {r.nombre}
            </option>
          ))}
        </select>
      </Campo>
      <Campo etiqueta="Dispositivo">
        <select value={filtros.dispositivo} onChange={cambiar('dispositivo')} className={CLASE_CAMPO}>
          <option value="">Todos</option>
          {(dispositivos ?? []).map((d) => (
            <option key={d.id} value={d.id}>
              {d.nombre}
            </option>
          ))}
        </select>
      </Campo>
      <Campo etiqueta="Desde">
        <input type="datetime-local" value={filtros.desde} onChange={cambiar('desde')} className={CLASE_CAMPO} />
      </Campo>
      <Campo etiqueta="Hasta">
        <input type="datetime-local" value={filtros.hasta} onChange={cambiar('hasta')} className={CLASE_CAMPO} />
      </Campo>
    </section>
  )
}

function Tabla({ filas }) {
  return (
    <div className="hidden overflow-x-auto rounded-2xl border border-borde bg-superficie md:block">
      <table className="w-full text-left text-sm">
        <thead className="border-b border-borde bg-superficie-suave text-xs text-texto-suave uppercase">
          <tr>
            <th className="px-3 py-2 font-medium">Fecha y hora</th>
            <th className="px-3 py-2 font-medium">Dispositivo</th>
            <th className="px-3 py-2 font-medium">Acción</th>
            <th className="px-3 py-2 font-medium">Anterior → nuevo</th>
            <th className="px-3 py-2 font-medium">Quién</th>
            <th className="px-3 py-2 font-medium">Condición</th>
            <th className="px-3 py-2 font-medium">Resultado</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-borde">
          {filas.map((f) => (
            <tr key={f.id} className={f.rol === 'sistema' ? 'bg-ahorro-suave/50' : ''}>
              <td className="px-3 py-2 whitespace-nowrap tabular-nums">{formatoFechaHora(aFecha(f.fecha_hora))}</td>
              <td className="px-3 py-2">{f.nombre_dispositivo ?? '—'}</td>
              <td className="px-3 py-2">
                <Accion fila={f} />
              </td>
              <td className="px-3 py-2">
                <Cambio fila={f} />
              </td>
              <td className="px-3 py-2">
                <Quien fila={f} />
              </td>
              <td className="px-3 py-2 text-texto-suave">{f.condicion_control ?? '—'}</td>
              <td className="px-3 py-2">{f.resultado ?? '—'}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

function Tarjetas({ filas }) {
  return (
    <ul className="space-y-3 md:hidden">
      {filas.map((f) => (
        <li
          key={f.id}
          className={`space-y-2 rounded-2xl border bg-superficie p-4 text-sm ${
            f.rol === 'sistema' ? 'border-ahorro/30' : 'border-borde'
          }`}
        >
          <div className="flex items-start justify-between gap-3">
            <span className="font-medium">{f.nombre_dispositivo ?? '—'}</span>
            <Accion fila={f} />
          </div>
          <div className="text-texto">
            <Cambio fila={f} />
          </div>
          <Quien fila={f} />
          <div className="space-y-0.5 text-xs text-texto-suave">
            <p>{formatoFechaHora(aFecha(f.fecha_hora))}</p>
            {f.condicion_control && <p className="break-words">Condición: {f.condicion_control}</p>}
            {f.resultado && <p>Resultado: {f.resultado}</p>}
          </div>
        </li>
      ))}
    </ul>
  )
}

function ContenidoHistorial({ domoId }) {
  const [filtros, setFiltros] = useState({ rol: '', dispositivo: '', desde: '', hasta: '' })

  const consultarDispositivos = useCallback(() => apiFetch(`/domos/${domoId}/dispositivos`), [domoId])
  const dispositivos = useConsulta(consultarDispositivos)

  const consultar = useCallback(() => {
    const params = new URLSearchParams({ limite: String(LIMITE) })
    if (filtros.rol) params.set('rol', filtros.rol)
    if (filtros.dispositivo) params.set('dispositivo_id', filtros.dispositivo)
    const desde = valorLocalAIso(filtros.desde)
    const hasta = valorLocalAIso(filtros.hasta)
    if (desde) params.set('fecha_desde', desde)
    if (hasta) params.set('fecha_hasta', hasta)
    return apiFetch(`/domos/${domoId}/historial?${params}`)
  }, [domoId, filtros])

  const { datos, error } = usePolling(consultar, INTERVALO_MS)

  return (
    <div className="space-y-4">
      <Filtros filtros={filtros} dispositivos={dispositivos.datos} onCambiar={setFiltros} />
      {error && <AvisoError>No se pudo cargar el historial: {error}</AvisoError>}
      {!datos ? (
        !error && <div className="h-40 animate-pulse rounded-2xl border border-borde bg-superficie" />
      ) : datos.length === 0 ? (
        <Vacio>No hay acciones registradas para estos filtros.</Vacio>
      ) : (
        <>
          <Tabla filas={datos} />
          <Tarjetas filas={datos} />
          {datos.length >= LIMITE && (
            <p className="text-xs text-texto-suave">Se muestran las {LIMITE} acciones más recientes; usa los filtros para ver otras.</p>
          )}
        </>
      )}
    </div>
  )
}

export default function Historial() {
  return <PaginaDomo seccion="Historial de acciones">{(domo) => <ContenidoHistorial domoId={domo.id} />}</PaginaDomo>
}
