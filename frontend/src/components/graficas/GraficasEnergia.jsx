import {
  Area,
  AreaChart,
  CartesianGrid,
  Legend,
  Line,
  LineChart,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'
import { esNumero, formateadorEjeTiempo, formatoFechaHora, formatoPct, formatoW } from '../../utils/formato.js'
import { combinarSeries, MAX_PUNTOS, puntosDe, reducir } from '../../utils/series.js'
import FuenteClima from '../FuenteClima.jsx'
import { CLASE_TARJETA } from '../ui.jsx'

const GENERACION = 'var(--color-serie-generacion)'
const CONSUMO = 'var(--color-serie-consumo)'
const SOC = 'var(--color-marca-fuerte)'
const RADIACION = 'var(--color-serie-radiacion)'
const entero = new Intl.NumberFormat('es-CO', { maximumFractionDigits: 0 })
const EJE = { fontSize: 11, fill: 'var(--color-texto-suave)' }
const REJILLA = 'var(--color-borde)'
const MARGEN = { top: 5, right: 12, left: -12, bottom: 0 }

function etiquetaTooltip(ms) {
  return formatoFechaHora(new Date(ms))
}

// Eje X numérico en milisegundos, proporcional al tiempo real. Se llama
// como función (no como componente) para que Recharts lo vea como hijo directo.
// `dominio` [desde, hasta] fija el mismo eje en dos gráficas (potencia y radiación).
function ejeTiempo(filas, dominio) {
  const duracion = dominio ? dominio[1] - dominio[0] : filas.length > 1 ? filas[filas.length - 1].t - filas[0].t : 0
  return (
    <XAxis
      dataKey="t"
      type="number"
      scale="time"
      domain={dominio ?? ['dataMin', 'dataMax']}
      tickFormatter={formateadorEjeTiempo(duracion)}
      tick={EJE}
      minTickGap={28}
    />
  )
}

function Marco({ titulo, subtitulo, vacia, textoVacio, alto = 'h-56', pie, className = '', children }) {
  return (
    <section className={`${CLASE_TARJETA} p-4 sm:p-5 ${className}`}>
      <div className="flex flex-wrap items-baseline justify-between gap-x-3">
        <h2 className="text-sm font-medium">{titulo}</h2>
        {subtitulo && <p className="text-xs text-texto-suave">{subtitulo}</p>}
      </div>
      <div className={`mt-3 ${alto}`}>
        {vacia ? (
          <div className="flex h-full items-center justify-center rounded-lg bg-superficie-suave p-4 text-center text-sm text-texto-suave">
            {textoVacio}
          </div>
        ) : (
          children
        )}
      </div>
      {pie}
    </section>
  )
}

function extremos(filas) {
  return filas.length ? [filas[0].t, filas[filas.length - 1].t] : null
}

// SoC de la batería y potencia generada vs consumida. Recibe los listados
// del backend tal cual (de la lectura más reciente a la más antigua); si
// alguna serie supera `maxPuntos`, se promedia por intervalos para dibujar.
// Con `clima` (GET /domos/{id}/clima), una gráfica pequeña aparte con la radiación
// estimada (W/m²): otra unidad y otra escala que la potencia (W), así que no comparte
// eje Y, pero sí el eje de tiempo de "Producción vs consumo".
export default function GraficasEnergia({
  bateria,
  fotovoltaicas,
  consumo,
  maxPuntos = MAX_PUNTOS,
  reserva,
  huecoMaximoMs,
  subtitulo,
  textoVacio = 'Sin datos',
  clima,
  textoVacioClima = 'Sin datos del clima en este período',
}) {
  const filasSoc = combinarSeries({ soc: reducir(puntosDe(bateria, 'estado_carga_soc'), maxPuntos) }, huecoMaximoMs)
  const filasPotencia = combinarSeries(
    {
      generada: reducir(puntosDe(fotovoltaicas, 'potencia_generada'), maxPuntos),
      consumida: reducir(puntosDe(consumo, 'potencia_consumida'), maxPuntos),
    },
    huecoMaximoMs,
  )
  const filasRadiacion = clima
    ? combinarSeries({ radiacion: reducir(puntosDe(clima, 'radiacion_w_m2'), maxPuntos) }, huecoMaximoMs)
    : null
  const tramos = filasRadiacion ? [extremos(filasPotencia), extremos(filasRadiacion)].filter(Boolean) : []
  const dominio = tramos.length ? [Math.min(...tramos.map((r) => r[0])), Math.max(...tramos.map((r) => r[1]))] : undefined

  return (
    <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
      <Marco titulo="Estado de carga de la batería (%)" subtitulo={subtitulo} vacia={filasSoc.length === 0} textoVacio={textoVacio}>
        <ResponsiveContainer width="100%" height="100%">
          <AreaChart data={filasSoc} margin={MARGEN}>
            <defs>
              <linearGradient id="relleno-soc" x1="0" y1="0" x2="0" y2="1">
                <stop offset="0%" stopColor={SOC} stopOpacity={0.25} />
                <stop offset="100%" stopColor={SOC} stopOpacity={0} />
              </linearGradient>
            </defs>
            <CartesianGrid stroke={REJILLA} vertical={false} />
            {ejeTiempo(filasSoc)}
            <YAxis domain={[0, 100]} tick={EJE} />
            <Tooltip labelFormatter={etiquetaTooltip} formatter={(v) => [formatoPct(v), 'SoC']} />
            {esNumero(reserva) && (
              <ReferenceLine
                y={reserva}
                stroke="var(--color-supervivencia)"
                strokeDasharray="4 4"
                label={{ value: `Reserva ${reserva} %`, position: 'insideBottomLeft', fontSize: 11, fill: 'var(--color-texto-suave)' }}
              />
            )}
            <Area type="linear" dataKey="soc" stroke={SOC} strokeWidth={2} fill="url(#relleno-soc)" isAnimationActive={false} />
          </AreaChart>
        </ResponsiveContainer>
      </Marco>

      <Marco titulo="Producción vs consumo (W)" subtitulo={subtitulo} vacia={filasPotencia.length === 0} textoVacio={textoVacio}>
        <ResponsiveContainer width="100%" height="100%">
          <LineChart data={filasPotencia} margin={MARGEN}>
            <CartesianGrid stroke={REJILLA} vertical={false} />
            {ejeTiempo(filasPotencia, dominio)}
            <YAxis tick={EJE} />
            <Tooltip labelFormatter={etiquetaTooltip} formatter={(v, nombre) => [formatoW(v), nombre]} />
            <Legend wrapperStyle={{ fontSize: 12 }} />
            <Line name="Generada" type="linear" dataKey="generada" stroke={GENERACION} strokeWidth={2} dot={false} isAnimationActive={false} />
            <Line name="Consumida" type="linear" dataKey="consumida" stroke={CONSUMO} strokeWidth={2} dot={false} isAnimationActive={false} />
          </LineChart>
        </ResponsiveContainer>
      </Marco>

      {filasRadiacion && (
        <Marco
          className="lg:col-span-2"
          titulo="Radiación solar estimada (W/m²)"
          subtitulo={subtitulo}
          alto="h-40"
          vacia={filasRadiacion.length === 0}
          textoVacio={textoVacioClima}
          pie={<FuenteClima className="mt-2" />}
        >
          <ResponsiveContainer width="100%" height="100%">
            <AreaChart data={filasRadiacion} margin={MARGEN}>
              <defs>
                <linearGradient id="relleno-radiacion" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="0%" stopColor={RADIACION} stopOpacity={0.25} />
                  <stop offset="100%" stopColor={RADIACION} stopOpacity={0} />
                </linearGradient>
              </defs>
              <CartesianGrid stroke={REJILLA} vertical={false} />
              {ejeTiempo(filasRadiacion, dominio)}
              <YAxis domain={[0, 'auto']} tick={EJE} />
              <Tooltip
                labelFormatter={etiquetaTooltip}
                formatter={(v) => [esNumero(v) ? `${entero.format(v)} W/m²` : 'Sin dato', 'Radiación estimada']}
              />
              <Area
                type="linear"
                dataKey="radiacion"
                stroke={RADIACION}
                strokeWidth={2}
                fill="url(#relleno-radiacion)"
                isAnimationActive={false}
              />
            </AreaChart>
          </ResponsiveContainer>
        </Marco>
      )}
    </div>
  )
}
