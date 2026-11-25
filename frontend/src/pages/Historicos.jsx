import { lazy, Suspense, useCallback, useState } from 'react'
import { apiFetch } from '../api/client.js'
import PaginaDomo from '../components/PaginaDomo.jsx'
import { AvisoError, Campo, CLASE_CAMPO } from '../components/ui.jsx'
import { useConsulta } from '../hooks/useConsulta.js'
import { rangoClimaValido } from '../utils/clima.js'
import { aValorLocal, valorLocalAIso } from '../utils/fechas.js'
import { aFecha, formatoEnergia, formatoFechaHora, formatoW } from '../utils/formato.js'

const GraficasEnergia = lazy(() => import('../components/graficas/GraficasEnergia.jsx'))

const LIMITE_LECTURAS = 1000
const HORA_MS = 60 * 60 * 1000

const ATAJOS = [
  { id: '1h', nombre: 'Última hora', ms: HORA_MS },
  { id: '24h', nombre: 'Últimas 24 h', ms: 24 * HORA_MS },
  { id: '7d', nombre: 'Últimos 7 días', ms: 7 * 24 * HORA_MS },
]

function rangoDeAtajo(id) {
  const atajo = ATAJOS.find((a) => a.id === id)
  const hasta = new Date()
  return { desde: new Date(hasta.getTime() - atajo.ms).toISOString(), hasta: hasta.toISOString() }
}

function EsqueletoGraficas() {
  return (
    <div className="grid animate-pulse gap-4 lg:grid-cols-2">
      <div className="h-72 rounded-2xl border border-borde bg-superficie" />
      <div className="h-72 rounded-2xl border border-borde bg-superficie" />
    </div>
  )
}

function Resumen({ titulo, total, maxima, promedio, colorAcento }) {
  return (
    <section className="rounded-2xl border border-borde bg-superficie p-4 sm:p-5">
      <h2 className="flex items-center gap-2 text-xs font-medium tracking-wide text-texto-suave uppercase">
        <span className={`size-2 rounded-full ${colorAcento}`} />
        {titulo}
      </h2>
      <p className="mt-2 text-2xl font-semibold tabular-nums">{formatoEnergia(total)}</p>
      <p className="text-sm text-texto-suave">Energía total del período</p>
      <dl className="mt-3 grid grid-cols-2 gap-3 border-t border-borde pt-3 text-sm">
        <div>
          <dt className="text-xs text-texto-suave">Potencia máxima</dt>
          <dd className="font-medium tabular-nums">{formatoW(maxima)}</dd>
        </div>
        <div>
          <dt className="text-xs text-texto-suave">Potencia promedio</dt>
          <dd className="font-medium tabular-nums">{formatoW(promedio)}</dd>
        </div>
      </dl>
    </section>
  )
}

function SelectorRango({ seleccion, onAtajo, onPersonalizado, onActualizar, cargando }) {
  const [desde, setDesde] = useState(() => aValorLocal(new Date(Date.now() - 24 * HORA_MS)))
  const [hasta, setHasta] = useState(() => aValorLocal(new Date()))
  const [error, setError] = useState(null)

  function aplicar(evento) {
    evento.preventDefault()
    const isoDesde = valorLocalAIso(desde)
    const isoHasta = valorLocalAIso(hasta)
    if (!isoDesde || !isoHasta) return setError('Elige fecha y hora de inicio y de fin.')
    if (isoDesde > isoHasta) return setError('La fecha de inicio debe ser anterior a la de fin.')
    setError(null)
    onPersonalizado(isoDesde, isoHasta)
  }

  const claseBoton = (activo) =>
    `inline-flex min-h-11 items-center rounded-lg px-3 py-1.5 text-sm font-medium transition ${
      activo ? 'bg-marca text-white' : 'border border-borde-fuerte bg-superficie text-texto hover:bg-superficie-suave'
    }`

  return (
    <section className="space-y-3 rounded-2xl border border-borde bg-superficie p-4">
      <div className="flex flex-wrap items-center gap-2">
        {ATAJOS.map((a) => (
          <button key={a.id} type="button" onClick={() => onAtajo(a.id)} className={claseBoton(seleccion === a.id)}>
            {a.nombre}
          </button>
        ))}
        <span className={`${claseBoton(seleccion === 'personalizado')} pointer-events-none`}>Personalizado</span>
        <button
          type="button"
          onClick={onActualizar}
          disabled={cargando}
          className="ml-auto min-h-11 rounded-lg border border-borde-fuerte bg-superficie px-3 py-1.5 text-sm font-medium text-texto hover:bg-superficie-suave disabled:cursor-wait disabled:opacity-60"
        >
          {cargando ? 'Actualizando…' : 'Actualizar'}
        </button>
      </div>
      <form onSubmit={aplicar} className="grid gap-3 sm:grid-cols-[1fr_1fr_auto] sm:items-end">
        <Campo etiqueta="Desde">
          <input type="datetime-local" value={desde} onChange={(e) => setDesde(e.target.value)} className={CLASE_CAMPO} />
        </Campo>
        <Campo etiqueta="Hasta">
          <input type="datetime-local" value={hasta} onChange={(e) => setHasta(e.target.value)} className={CLASE_CAMPO} />
        </Campo>
        <button
          type="submit"
          className="min-h-11 rounded-lg border border-marca px-3 py-1.5 text-sm font-medium text-marca-fuerte hover:bg-marca-suave"
        >
          Aplicar rango
        </button>
      </form>
      {error && <AvisoError>{error}</AvisoError>}
    </section>
  )
}

function ContenidoHistoricos({ domoId }) {
  // `consulta` cambia (objeto nuevo) con cada atajo, rango o "Actualizar":
  // eso dispara una consulta nueva. Sin polling.
  const [consulta, setConsulta] = useState(() => ({ seleccion: '24h', ...rangoDeAtajo('24h') }))

  const consultar = useCallback(async () => {
    const rango = new URLSearchParams({ fecha_desde: consulta.desde, fecha_hasta: consulta.hasta })
    const listado = `${rango}&limite=${LIMITE_LECTURAS}`
    // El clima es de referencia: si falla o el rango pasa de 31 días, el resto se muestra igual.
    const clima = rangoClimaValido(consulta.desde, consulta.hasta)
      ? apiFetch(`/domos/${domoId}/clima?${rango}`).then(
          (filas) => ({ filas, nota: null }),
          (e) => ({ filas: [], nota: `No se pudo cargar el clima: ${e.message}` }),
        )
      : Promise.resolve({ filas: [], nota: 'La radiación estimada se muestra para rangos de hasta 31 días.' })
    const [resumen, bateria, fotovoltaicas, consumo, radiacion] = await Promise.all([
      apiFetch(`/domos/${domoId}/historico?${rango}`),
      apiFetch(`/domos/${domoId}/lecturas-bateria?${listado}`),
      apiFetch(`/domos/${domoId}/lecturas-fotovoltaicas?${listado}`),
      apiFetch(`/domos/${domoId}/lecturas-consumo?${listado}`),
      clima,
    ])
    return { resumen, bateria, fotovoltaicas, consumo, clima: radiacion }
  }, [domoId, consulta])

  const { datos, error, cargando } = useConsulta(consultar)

  function actualizar() {
    setConsulta((c) =>
      c.seleccion === 'personalizado' ? { ...c } : { seleccion: c.seleccion, ...rangoDeAtajo(c.seleccion) },
    )
  }

  // Los listados devuelven las lecturas MÁS RECIENTES hasta el límite: si
  // alguno lo alcanza, las gráficas no cubren el inicio del rango.
  const recortados = datos ? ['bateria', 'fotovoltaicas', 'consumo'].filter((k) => datos[k].length >= LIMITE_LECTURAS) : []
  const inicioGraficas = recortados.length
    ? Math.max(...recortados.map((k) => aFecha(datos[k][datos[k].length - 1].fecha_hora)?.getTime() ?? 0))
    : null

  return (
    <div className="space-y-4">
      <SelectorRango
        seleccion={consulta.seleccion}
        cargando={cargando}
        onAtajo={(id) => setConsulta({ seleccion: id, ...rangoDeAtajo(id) })}
        onPersonalizado={(desde, hasta) => setConsulta({ seleccion: 'personalizado', desde, hasta })}
        onActualizar={actualizar}
      />

      <p className="text-sm text-texto-suave">
        Del {formatoFechaHora(aFecha(consulta.desde))} al {formatoFechaHora(aFecha(consulta.hasta))}
      </p>

      {error && <AvisoError>No se pudieron cargar los históricos: {error}</AvisoError>}

      {!datos ? (
        !error && (
          <div className="space-y-4">
            <div className="grid animate-pulse gap-4 md:grid-cols-2">
              <div className="h-40 rounded-2xl border border-borde bg-superficie" />
              <div className="h-40 rounded-2xl border border-borde bg-superficie" />
            </div>
            <EsqueletoGraficas />
          </div>
        )
      ) : (
        <div className={`space-y-4 transition-opacity ${cargando ? 'opacity-60' : ''}`}>
          <div className="grid gap-4 md:grid-cols-2">
            <Resumen
              titulo="Generación"
              colorAcento="bg-serie-generacion"
              total={datos.resumen.energia_generada_total}
              maxima={datos.resumen.potencia_generada_maxima}
              promedio={datos.resumen.potencia_generada_promedio}
            />
            <Resumen
              titulo="Consumo"
              colorAcento="bg-serie-consumo"
              total={datos.resumen.energia_consumida_total}
              maxima={datos.resumen.potencia_consumida_maxima}
              promedio={datos.resumen.potencia_consumida_promedio}
            />
          </div>

          {inicioGraficas && (
            <p className="rounded-lg border border-ahorro/30 bg-ahorro-suave px-3 py-2 text-sm text-ahorro">
              Hay más de {LIMITE_LECTURAS} lecturas en este rango: las gráficas muestran solo las más recientes (desde{' '}
              {formatoFechaHora(new Date(inicioGraficas))}). El resumen de arriba sí cubre todo el período.
            </p>
          )}

          <Suspense fallback={<EsqueletoGraficas />}>
            <GraficasEnergia
              bateria={datos.bateria}
              fotovoltaicas={datos.fotovoltaicas}
              consumo={datos.consumo}
              clima={datos.clima.filas}
              textoVacioClima={datos.clima.nota ?? 'Sin datos del clima en este período (¿el domo tiene coordenadas?)'}
            />
          </Suspense>
        </div>
      )}
    </div>
  )
}

export default function Historicos() {
  return <PaginaDomo seccion="Históricos">{(domo) => <ContenidoHistoricos domoId={domo.id} />}</PaginaDomo>
}
