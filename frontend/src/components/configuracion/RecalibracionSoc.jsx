import { useCallback, useState } from 'react'
import { apiFetch } from '../../api/client.js'
import { usePolling } from '../../hooks/usePolling.js'
import { formatoPct } from '../../utils/formato.js'
import { describirRecalibracion } from '../../utils/recalibracion.js'
import { AvisoError, BOTON_PRINCIPAL, BOTON_SECUNDARIO, CLASE_TARJETA } from '../ui.jsx'

// Cada 5 s mientras la página está visible (el GET lee la memoria del backend, no MQTT):
// así se ve el avance de la sesión y la respuesta de la ESP32 a cada pedido.
const CONSULTA_MS = 5000

const TONOS = {
  neutro: 'bg-superficie-suave text-texto',
  verde: 'bg-normal-suave text-normal',
  ambar: 'bg-ahorro-suave text-ahorro',
  rojo: 'bg-supervivencia-suave text-supervivencia',
}

function Progreso({ progreso }) {
  const pct = Math.round(progreso.fraccion * 100)
  return (
    <div>
      <p className="text-sm font-medium tabular-nums">
        {Math.floor(progreso.ok)} de {progreso.requeridos} min en reposo
      </p>
      <div
        className="mt-1 h-2 w-full overflow-hidden rounded-full bg-superficie-suave"
        role="progressbar"
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={pct}
        aria-label="Minutos seguidos en reposo"
      >
        <div className="h-full rounded-full bg-marca transition-all" style={{ width: `${pct}%` }} />
      </div>
    </div>
  )
}

// Recalibración GUIADA del estado de carga (solo el dueño): el dueño pide la medición, la
// ESP32 mide en reposo con la tabla de voltaje y el dueño solo confirma. Nunca se escribe
// un valor a mano (R-13 decidiría con un SoC inventado).
export default function RecalibracionSoc({ domoId }) {
  const [enviando, setEnviando] = useState(false)
  const [errorPedido, setErrorPedido] = useState(null)
  const ruta = `/domos/${domoId}/soc-recalibracion`
  const consultar = useCallback(() => apiFetch(ruta), [ruta])
  const { datos: recal, error: errorConsulta, refrescar } = usePolling(consultar, CONSULTA_MS)
  const error = errorPedido ?? errorConsulta

  async function pedir(accion) {
    setEnviando(true)
    setErrorPedido(null)
    try {
      await apiFetch(ruta, { method: 'POST', body: { accion } })
      // La ESP32 responde por MQTT en uno o dos segundos.
      setTimeout(refrescar, 1500)
    } catch (e) {
      setErrorPedido(e.message)
    } finally {
      setEnviando(false)
    }
  }

  const d = describirRecalibracion(recal)

  return (
    <section className={`${CLASE_TARJETA} min-w-0 space-y-4 p-4 sm:p-5`} aria-labelledby="titulo-recalibrar">
      <div>
        <h2 id="titulo-recalibrar" className="text-sm font-semibold text-texto">
          Recalibrar el estado de carga
        </h2>
        <p className="mt-1 text-sm text-texto-suave">
          Úsalo si la ESP32 estuvo apagada o si el estado de carga parece incorrecto. El sistema mide la batería en reposo y te
          propone el valor; tú solo lo confirmas.
        </p>
        <ul className="mt-2 list-disc space-y-1 pl-5 text-sm text-texto-suave">
          <li>Apaga el inversor (sin consumo). Mejor de noche: con sol no se puede medir.</li>
          <li>Hacen falta 15 minutos seguidos en reposo; si algo lo interrumpe, el conteo vuelve a empezar.</li>
          <li>Después de cargar la batería, el voltaje queda algo alto por unas horas y el valor saldría alto: espera.</li>
        </ul>
      </div>

      {recal && (
        <div className={`space-y-3 rounded-lg px-3 py-2 ${TONOS[d.tono]}`} role="status" aria-live="polite">
          <p className="text-sm font-semibold">{d.titulo}</p>
          {d.progreso && <Progreso progreso={d.progreso} />}
          {d.propuesta && (
            <p className="flex flex-wrap items-baseline gap-x-2 text-base tabular-nums">
              <span>
                Actual <strong>{formatoPct(d.propuesta.actual)}</strong>
              </span>
              <span aria-hidden="true">→</span>
              <span>
                medido <strong>{formatoPct(d.propuesta.medido)}</strong>
              </span>
            </p>
          )}
          {d.detalle && <p className="text-sm">{d.detalle}</p>}
        </div>
      )}

      {error && <AvisoError>{error}</AvisoError>}

      <div className="flex flex-col gap-2 sm:flex-row">
        {d.puedeIniciar && (
          <button type="button" disabled={enviando} onClick={() => pedir('iniciar')} className={`${BOTON_PRINCIPAL} w-full sm:w-auto`}>
            {enviando ? 'Enviando…' : 'Iniciar'}
          </button>
        )}
        {d.puedeConfirmar && (
          <button type="button" disabled={enviando} onClick={() => pedir('confirmar')} className={`${BOTON_PRINCIPAL} w-full sm:w-auto`}>
            Confirmar {formatoPct(d.propuesta.medido)}
          </button>
        )}
        {d.puedeCancelar && (
          <button type="button" disabled={enviando} onClick={() => pedir('cancelar')} className={`${BOTON_SECUNDARIO} w-full sm:w-auto`}>
            Cancelar
          </button>
        )}
      </div>
    </section>
  )
}
