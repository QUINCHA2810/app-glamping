import { useCallback, useState } from 'react'
import { apiFetch } from '../api/client.js'
import PaginaDomo from '../components/PaginaDomo.jsx'
import { AvisoError, Campo, CLASE_CAMPO, Etiqueta, Vacio } from '../components/ui.jsx'
import { usePolling } from '../hooks/usePolling.js'
import { aFecha, formatoFechaHora, formatoNumero } from '../utils/formato.js'

const INTERVALO_MS = 15000

// Catálogos de docs/Requisitos/Variables.md (sección 11) / schemas.AlertaCreate.
const TIPOS = {
  bateria_baja: 'Batería baja',
  consumo_elevado: 'Consumo elevado',
  generacion_nula: 'Generación nula',
  dispositivo_desconectado: 'Dispositivo desconectado',
  sobrecarga: 'Sobrecarga',
}
const PRIORIDADES = {
  critico: { nombre: 'Crítico', tono: 'rojo', borde: 'border-l-supervivencia' },
  advertencia: { nombre: 'Advertencia', tono: 'ambar', borde: 'border-l-ahorro' },
  informativo: { nombre: 'Informativo', tono: 'azul', borde: 'border-l-sky-500' },
}
const ESTADOS = {
  activa: { nombre: 'Activa', tono: 'rojo' },
  atendida: { nombre: 'Atendida', tono: 'ambar' },
  cerrada: { nombre: 'Cerrada', tono: 'neutro' },
}

function TarjetaAlerta({ domoId, alerta, onActualizada }) {
  const [enviando, setEnviando] = useState(null) // estado al que se está pasando
  const [error, setError] = useState(null)
  const prioridad = PRIORIDADES[alerta.nivel_prioridad]
  const estado = ESTADOS[alerta.estado_alerta]

  async function cambiarEstado(estadoAlerta) {
    setEnviando(estadoAlerta)
    setError(null)
    try {
      await apiFetch(`/domos/${domoId}/alertas/${alerta.id}`, { method: 'PATCH', body: { estado_alerta: estadoAlerta } })
      onActualizada()
    } catch (e) {
      setError(e.message)
    } finally {
      setEnviando(null)
    }
  }

  const tieneValores = alerta.valor_detectado != null || alerta.valor_limite != null

  return (
    <li
      className={`space-y-3 rounded-2xl border border-l-4 border-borde bg-superficie p-4 ${prioridad?.borde ?? 'border-l-borde-fuerte'} ${
        alerta.estado_alerta === 'cerrada' ? 'opacity-75' : ''
      }`}
    >
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div className="min-w-0">
          <h3 className="font-medium">{TIPOS[alerta.tipo_alerta] ?? alerta.tipo_alerta ?? 'Alerta'}</h3>
          <p className="text-xs text-texto-suave">{formatoFechaHora(aFecha(alerta.fecha_hora))}</p>
        </div>
        <div className="flex flex-wrap gap-1.5">
          {alerta.nivel_prioridad && <Etiqueta tono={prioridad?.tono}>{prioridad?.nombre ?? alerta.nivel_prioridad}</Etiqueta>}
          <Etiqueta tono={estado?.tono}>{estado?.nombre ?? alerta.estado_alerta}</Etiqueta>
        </div>
      </div>

      {alerta.descripcion && <p className="text-sm text-texto">{alerta.descripcion}</p>}

      {(alerta.variable_asociada || tieneValores) && (
        <dl className="grid grid-cols-1 gap-2 rounded-lg bg-superficie-suave px-3 py-2 text-sm sm:grid-cols-3">
          <div>
            <dt className="text-xs text-texto-suave">Variable</dt>
            <dd>{alerta.variable_asociada ?? '—'}</dd>
          </div>
          <div>
            <dt className="text-xs text-texto-suave">Valor detectado</dt>
            <dd className="font-medium tabular-nums">{formatoNumero(alerta.valor_detectado)}</dd>
          </div>
          <div>
            <dt className="text-xs text-texto-suave">Límite</dt>
            <dd className="tabular-nums">{formatoNumero(alerta.valor_limite)}</dd>
          </div>
        </dl>
      )}

      {alerta.estado_alerta !== 'cerrada' && (
        <div className="flex flex-wrap gap-2">
          {alerta.estado_alerta === 'activa' && (
            <button
              type="button"
              disabled={enviando !== null}
              onClick={() => cambiarEstado('atendida')}
              className="min-h-11 rounded-lg border border-ahorro/30 bg-ahorro-suave px-3 py-1.5 text-sm font-medium text-ahorro hover:bg-ahorro-suave disabled:cursor-wait disabled:opacity-60"
            >
              {enviando === 'atendida' ? 'Guardando…' : 'Marcar atendida'}
            </button>
          )}
          <button
            type="button"
            disabled={enviando !== null}
            onClick={() => cambiarEstado('cerrada')}
            className="min-h-11 rounded-lg border border-borde-fuerte bg-superficie px-3 py-1.5 text-sm font-medium text-texto hover:bg-superficie-suave disabled:cursor-wait disabled:opacity-60"
          >
            {enviando === 'cerrada' ? 'Guardando…' : 'Cerrar'}
          </button>
        </div>
      )}

      {error && <AvisoError>No se pudo actualizar la alerta: {error}</AvisoError>}
    </li>
  )
}

function ContenidoAlertas({ domoId }) {
  const [filtros, setFiltros] = useState({ estado: 'activa', prioridad: '' })

  const consultar = useCallback(() => {
    const params = new URLSearchParams()
    if (filtros.estado) params.set('estado_alerta', filtros.estado)
    if (filtros.prioridad) params.set('nivel_prioridad', filtros.prioridad)
    return apiFetch(`/domos/${domoId}/alertas?${params}`)
  }, [domoId, filtros])

  const { datos, error, refrescar } = usePolling(consultar, INTERVALO_MS)

  return (
    <div className="space-y-4">
      <section className="grid gap-3 rounded-2xl border border-borde bg-superficie p-4 sm:grid-cols-2">
        <Campo etiqueta="Estado">
          <select value={filtros.estado} onChange={(e) => setFiltros({ ...filtros, estado: e.target.value })} className={CLASE_CAMPO}>
            <option value="">Todos</option>
            {Object.entries(ESTADOS).map(([valor, { nombre }]) => (
              <option key={valor} value={valor}>
                {nombre}
              </option>
            ))}
          </select>
        </Campo>
        <Campo etiqueta="Prioridad">
          <select
            value={filtros.prioridad}
            onChange={(e) => setFiltros({ ...filtros, prioridad: e.target.value })}
            className={CLASE_CAMPO}
          >
            <option value="">Todas</option>
            {Object.entries(PRIORIDADES).map(([valor, { nombre }]) => (
              <option key={valor} value={valor}>
                {nombre}
              </option>
            ))}
          </select>
        </Campo>
      </section>

      {error && <AvisoError>No se pudieron cargar las alertas: {error}</AvisoError>}
      {!datos ? (
        !error && <div className="h-32 animate-pulse rounded-2xl border border-borde bg-superficie" />
      ) : datos.length === 0 ? (
        <Vacio>Sin alertas para este filtro.</Vacio>
      ) : (
        <ul className="space-y-3">
          {datos.map((a) => (
            <TarjetaAlerta key={a.id} domoId={domoId} alerta={a} onActualizada={refrescar} />
          ))}
        </ul>
      )}
    </div>
  )
}

export default function Alertas() {
  return <PaginaDomo seccion="Alertas">{(domo) => <ContenidoAlertas domoId={domo.id} />}</PaginaDomo>
}
