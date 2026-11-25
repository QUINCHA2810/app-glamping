import { useState } from 'react'
import { apiFetch } from '../../api/client.js'
import { aFecha, formatoFechaHora, formatoPct, SIN_DATOS } from '../../utils/formato.js'
import { INFO_CATEGORIA } from '../dispositivos/categorias.js'
import { BOTON_SECUNDARIO, Etiqueta } from '../ui.jsx'
import { estadoEnergiaDe, textoPorCortar } from './estadosEnergia.js'

// Resultado de POST /domos/{id}/control-energetico/evaluar.
function ResultadoEvaluacion({ resultado, onCerrar }) {
  const { acciones } = resultado
  return (
    <div className="mt-4 rounded-2xl border border-borde bg-superficie p-3 text-sm">
      <div className="flex items-start justify-between gap-3">
        <p className="pt-2.5 font-medium">Resultado de la evaluación</p>
        <button
          type="button"
          onClick={onCerrar}
          className="-m-1 flex size-11 shrink-0 items-center justify-center rounded-lg text-texto-suave hover:bg-superficie-suave"
          aria-label="Cerrar resultado"
        >
          ✕
        </button>
      </div>
      <dl className="mt-2 grid grid-cols-1 gap-x-6 gap-y-1 sm:grid-cols-3">
        <div>
          <dt className="text-xs text-texto-suave">SoC usado</dt>
          <dd className="font-medium tabular-nums">{formatoPct(resultado.estado_carga_soc)}</dd>
        </div>
        <div>
          <dt className="text-xs text-texto-suave">Umbral global de apagado</dt>
          <dd className="font-medium tabular-nums">{formatoPct(resultado.umbral_soc_bajo)}</dd>
        </div>
        <div>
          <dt className="text-xs text-texto-suave">Umbral global de reactivación</dt>
          <dd className="font-medium tabular-nums">{formatoPct(resultado.umbral_soc_recuperacion)}</dd>
        </div>
      </dl>

      {acciones.length === 0 ? (
        <p className="mt-3 text-texto-suave">Sin cambios: ninguna toma cruzó su umbral.</p>
      ) : (
        <ul className="mt-3 divide-y divide-borde rounded-lg border border-borde">
          {acciones.map((a) => (
            <li
              key={`${a.dispositivo_id}-${a.accion_realizada}`}
              className="flex flex-wrap items-center justify-between gap-x-4 gap-y-1 px-3 py-2"
            >
              <span className="font-medium">{a.nombre}</span>
              <span className="flex items-center gap-2 text-xs">
                <Etiqueta tono={a.accion_realizada === 'apagar' ? 'ambar' : 'verde'}>
                  {a.accion_realizada === 'apagar' ? 'Apagar' : 'Encender'}
                </Etiqueta>
                <span className="text-texto-suave">umbral {formatoPct(a.umbral_aplicado)}</span>
              </span>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}

// Datos de GET /domos/{id}/control-energetico/estado (R-13) y botón para
// ejecutar la evaluación manualmente; `onEvaluado` refresca el panel.
export default function TarjetaSupervivencia({ domoId, control, onEvaluado }) {
  const [evaluando, setEvaluando] = useState(false)
  const [resultado, setResultado] = useState(null)
  const [error, setError] = useState(null)

  async function evaluar() {
    setEvaluando(true)
    setError(null)
    try {
      setResultado(await apiFetch(`/domos/${domoId}/control-energetico/evaluar`, { method: 'POST' }))
      onEvaluado()
    } catch (e) {
      setResultado(null)
      setError(e.message)
    } finally {
      setEvaluando(false)
    }
  }

  const estado = estadoEnergiaDe(control)
  const tomas = control?.tomas_apagadas_por_sistema ?? []
  const porCortar = textoPorCortar(control)

  return (
    <section className={`rounded-2xl border p-4 shadow-sm sm:p-5 ${estado.tarjeta}`}>
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex flex-wrap items-center gap-2">
          <h2 className="text-sm font-medium">Estado energético</h2>
          {control && (
            <span className={`inline-flex items-center gap-1.5 rounded-full px-2.5 py-0.5 text-xs font-semibold ${estado.etiqueta}`}>
              <span aria-hidden="true">{estado.simbolo}</span>
              {estado.nombre}
            </span>
          )}
        </div>
        <button type="button" onClick={evaluar} disabled={evaluando} className={BOTON_SECUNDARIO}>
          {evaluando ? 'Evaluando…' : 'Evaluar ahora'}
        </button>
      </div>
      {control && <p className="mt-2 text-sm text-texto-suave">{estado.detalle}</p>}
      {porCortar && (
        <p role="status" className="mt-2 rounded-lg border border-ahorro/30 bg-superficie px-3 py-2 text-sm text-ahorro">
          {porCortar}
        </p>
      )}

      {!control ? (
        <p className="mt-3 text-sm text-texto-suave">{SIN_DATOS}</p>
      ) : tomas.length === 0 ? (
        <p className="mt-3 text-sm text-texto-suave">No hay tomas apagadas por el sistema.</p>
      ) : (
        <>
          <p className="mt-3 text-sm text-texto-suave">Tomas apagadas por el sistema:</p>
          <ul className="mt-2 divide-y divide-borde rounded-lg border border-borde bg-superficie text-sm">
            {tomas.map((t) => (
              <li key={t.dispositivo_id} className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-0.5 px-3 py-2">
                <span className="font-medium">{t.nombre}</span>
                <span className="text-xs text-texto-suave">
                  {INFO_CATEGORIA[t.categoria]?.nombre ?? t.categoria} · desde {formatoFechaHora(aFecha(t.apagada_desde))}
                </span>
              </li>
            ))}
          </ul>
        </>
      )}

      {error && (
        <p role="alert" className="mt-3 rounded-lg border border-supervivencia/30 bg-supervivencia-suave px-3 py-2 text-sm text-supervivencia">
          No se pudo evaluar: {error}
        </p>
      )}
      {resultado && <ResultadoEvaluacion resultado={resultado} onCerrar={() => setResultado(null)} />}
    </section>
  )
}
