import { lazy, Suspense, useCallback, useMemo } from 'react'
import { apiFetch } from '../api/client.js'
import { useAuth } from '../auth/AuthContext.jsx'
import { IconoEnchufe, IconoRayo, IconoSol } from '../components/Iconos.jsx'
import FlujoEnergia from '../components/panel/FlujoEnergia.jsx'
import IndicadorActualizacion from '../components/panel/IndicadorActualizacion.jsx'
import SaludoEstado from '../components/panel/SaludoEstado.jsx'
import Sparkline from '../components/panel/Sparkline.jsx'
import TarjetaAutonomia from '../components/panel/TarjetaAutonomia.jsx'
import TarjetaBateria from '../components/panel/TarjetaBateria.jsx'
import TarjetaClima from '../components/panel/TarjetaClima.jsx'
import TarjetaKpi from '../components/panel/TarjetaKpi.jsx'
import TarjetaSupervivencia from '../components/panel/TarjetaSupervivencia.jsx'
import { AvisoError, CLASE_TARJETA } from '../components/ui.jsx'
import { useDomo } from '../domos/DomoContext.jsx'
import { useConsulta } from '../hooks/useConsulta.js'
import { usePolling } from '../hooks/usePolling.js'
import { cargasActivas } from '../utils/comunicacion.js'
import { esReciente, serieCargasActivas, VENTANA_RECIENTE_MS } from '../utils/energia.js'
import { esNumero, formatoW } from '../utils/formato.js'
import { puntosDe, reducir } from '../utils/series.js'

// Recharts es pesado: las gráficas se cargan en un chunk aparte.
const GraficasEnergia = lazy(() => import('../components/graficas/GraficasEnergia.jsx'))

const INTERVALO_TIEMPO_REAL_MS = 5000
// Cada consulta de la ventana trae hasta 1000 lecturas por serie: basta con una por minuto.
const INTERVALO_VENTANA_MS = 60000
const LIMITE_VENTANA = 1000
const PUNTOS_TENDENCIA = 80
const HUECO_MAXIMO_MS = 10 * 60_000

function Esqueleto() {
  return (
    <div className="animate-pulse space-y-4" aria-label="Cargando panel">
      <div className="h-14 w-64 rounded-lg bg-borde" />
      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        {Array.from({ length: 4 }, (_, i) => (
          <div key={i} className={`${CLASE_TARJETA} h-44`} />
        ))}
      </div>
      <div className={`${CLASE_TARJETA} h-40`} />
    </div>
  )
}

function EsqueletoGraficas() {
  return (
    <div className="grid animate-pulse gap-4 lg:grid-cols-2">
      <div className={`${CLASE_TARJETA} h-72`} />
      <div className={`${CLASE_TARJETA} h-72`} />
    </div>
  )
}

function ContenidoPanel({ domo, nombreUsuario }) {
  const domoId = domo.id

  const consultarTiempoReal = useCallback(async () => {
    // energia-reciente: autonomía (media de 15 min) y flujo (media de 60 s), calculados en el backend.
    const [general, control, dispositivos, energia] = await Promise.all([
      apiFetch(`/domos/${domoId}/estado-general`),
      apiFetch(`/domos/${domoId}/control-energetico/estado`),
      apiFetch(`/domos/${domoId}/dispositivos`),
      apiFetch(`/domos/${domoId}/energia-reciente`),
    ])
    return { general, control, dispositivos, energia }
  }, [domoId])

  // Solo la ventana reciente: una lectura vieja estiraría el eje y aplastaría la curva.
  const consultarVentana = useCallback(async () => {
    const ahora = Date.now()
    const desde = ahora - VENTANA_RECIENTE_MS
    const q = new URLSearchParams({ fecha_desde: new Date(desde).toISOString(), limite: String(LIMITE_VENTANA) })
    const [bateria, fotovoltaicas, consumo, historial] = await Promise.all([
      apiFetch(`/domos/${domoId}/lecturas-bateria?${q}`),
      apiFetch(`/domos/${domoId}/lecturas-fotovoltaicas?${q}`),
      apiFetch(`/domos/${domoId}/lecturas-consumo?${q}`),
      apiFetch(`/domos/${domoId}/historial?${q}`),
    ])
    return { bateria, fotovoltaicas, consumo, historial, desde, ahora }
  }, [domoId])

  // La reserva de la batería (la capacidad ya la usa el backend en energia-reciente).
  const consultarAjustes = useCallback(async () => ({ categorias: await apiFetch(`/domos/${domoId}/categorias`) }), [domoId])

  const tiempoReal = usePolling(consultarTiempoReal, INTERVALO_TIEMPO_REAL_MS)
  const ventana = usePolling(consultarVentana, INTERVALO_VENTANA_MS)
  const ajustes = useConsulta(consultarAjustes)

  const tendencias = useMemo(() => {
    const v = ventana.datos
    if (!v) return { generada: [], consumida: [], soc: [] }
    return {
      generada: reducir(puntosDe(v.fotovoltaicas, 'potencia_generada'), PUNTOS_TENDENCIA),
      consumida: reducir(puntosDe(v.consumo, 'potencia_consumida'), PUNTOS_TENDENCIA),
      soc: reducir(puntosDe(v.bateria, 'estado_carga_soc'), PUNTOS_TENDENCIA),
    }
  }, [ventana.datos])

  const dispositivos = tiempoReal.datos?.dispositivos
  const historial = ventana.datos?.historial
  const desdeVentana = ventana.datos?.desde
  const cargas = useMemo(
    () => (dispositivos && historial ? serieCargasActivas(dispositivos, historial, desdeVentana) : []),
    [dispositivos, historial, desdeVentana],
  )

  if (!tiempoReal.datos) {
    return tiempoReal.cargando || !tiempoReal.error ? (
      <Esqueleto />
    ) : (
      <AvisoError>No se pudieron actualizar los datos: {tiempoReal.error}</AvisoError>
    )
  }

  const { general, control, energia } = tiempoReal.datos
  const reserva = ajustes.datos?.categorias?.reserva_soc
  // Una toma encendida en la base pero sin confirmar por la toma real no cuenta (utils/comunicacion.js).
  const { encendidas: encendidos, sinConfirmar } = cargasActivas(dispositivos)
  // estado-general solo trae las medidas de los últimos 2 minutos (las viejas van en null,
  // ver backend/servicios/mediciones.VIGENCIA_S): un valor viejo nunca se muestra como actual.
  const vigente = esReciente(general.fecha_actualizacion)
  const momento = (valor) => (esNumero(valor) ? 'Ahora mismo' : 'Sin dato de los últimos 2 minutos')
  // El flujo usa los promedios de 60 s; sin una medición de los últimos 2 minutos, nada.
  const flujo = energia.vigente ? energia.flujo : null

  return (
    <div className="space-y-4">
      <SaludoEstado nombre={nombreUsuario} domo={domo} control={control} alertasActivas={general.alertas_activas} />
      <IndicadorActualizacion fechaActualizacion={general.fecha_actualizacion} />
      {tiempoReal.error && <AvisoError>No se pudieron actualizar los datos: {tiempoReal.error}</AvisoError>}

      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-4">
        <TarjetaKpi Icono={IconoSol} titulo="Generación solar" valor={formatoW(general.potencia_generada)} detalle={momento(general.potencia_generada)}>
          <Sparkline puntos={tendencias.generada} color="var(--color-serie-generacion)" etiqueta="Generación" formato={formatoW} />
        </TarjetaKpi>
        <TarjetaBateria
          soc={general.estado_carga_soc}
          reserva={reserva}
          estadoBateria={general.estado_bateria}
          vigente={vigente}
          puntos={tendencias.soc}
        />
        <TarjetaKpi Icono={IconoRayo} titulo="Consumo" valor={formatoW(general.potencia_consumida)} detalle={momento(general.potencia_consumida)}>
          <Sparkline puntos={tendencias.consumida} color="var(--color-serie-consumo)" etiqueta="Consumo" formato={formatoW} />
        </TarjetaKpi>
        <TarjetaKpi
          Icono={IconoEnchufe}
          titulo="Cargas activas"
          valor={`${encendidos} de ${dispositivos.length}`}
          detalle={sinConfirmar ? `Dispositivos encendidos · ${sinConfirmar} sin confirmar` : 'Dispositivos encendidos'}
        >
          <Sparkline
            puntos={cargas}
            color="var(--color-texto-suave)"
            etiqueta="Cargas activas"
            dominio={[0, dispositivos.length]}
            escalonada
            formato={(v) => String(v)}
          />
        </TarjetaKpi>
      </div>

      {/* Estimación de Open-Meteo: explica la generación solar (consulta propia, cada 5 min). */}
      <TarjetaClima domoId={domoId} />

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-3">
        <TarjetaAutonomia energia={energia} reserva={reserva} />
        <FlujoEnergia
          className="lg:col-span-2"
          generada={flujo?.generada}
          consumida={flujo?.consumida}
          potenciaBateria={flujo?.bateria}
          perdidas={flujo?.perdidas_w}
          eficiencia={flujo?.eficiencia_pct}
          vigente={energia.vigente}
        />
      </div>

      <TarjetaSupervivencia
        domoId={domoId}
        control={control}
        onEvaluado={() => {
          tiempoReal.refrescar()
          ventana.refrescar()
        }}
      />

      {ventana.datos ? (
        <Suspense fallback={<EsqueletoGraficas />}>
          <GraficasEnergia
            bateria={ventana.datos.bateria}
            fotovoltaicas={ventana.datos.fotovoltaicas}
            consumo={ventana.datos.consumo}
            reserva={reserva}
            huecoMaximoMs={HUECO_MAXIMO_MS}
            subtitulo="Últimas 2 horas"
            textoVacio="Sin lecturas en las últimas 2 horas"
          />
        </Suspense>
      ) : ventana.error ? (
        <AvisoError>No se pudieron cargar las gráficas: {ventana.error}</AvisoError>
      ) : (
        <EsqueletoGraficas />
      )}
    </div>
  )
}

export default function Panel() {
  const { domo, cargando } = useDomo()
  const { usuario } = useAuth()

  if (cargando) return <Esqueleto />

  if (!domo) {
    return (
      <div className={`${CLASE_TARJETA} p-6 text-sm text-texto-suave`}>No tienes domos disponibles. Pide al dueño que te asigne uno.</div>
    )
  }

  // key: al cambiar de domo se reinicia todo el estado del panel
  return <ContenidoPanel key={domo.id} domo={domo} nombreUsuario={usuario?.nombre ?? ''} />
}
