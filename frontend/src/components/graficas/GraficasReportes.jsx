import {
  Bar,
  BarChart,
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
import { esNumero, formatoNumero, formatoPct, formatoW } from '../../utils/formato.js'
import { CLASE_TARJETA } from '../ui.jsx'

const GENERACION = 'var(--color-serie-generacion)'
const CONSUMO = 'var(--color-serie-consumo)'
const EJE = { fontSize: 11, fill: 'var(--color-texto-suave)' }
const REJILLA = 'var(--color-borde)'
const MARGEN = { top: 5, right: 12, left: -12, bottom: 0 }

// "2026-03-10" -> "10/03". Sin pasar por Date, para no mover el día por la zona horaria.
function diaCorto(fecha) {
  return `${fecha.slice(8, 10)}/${fecha.slice(5, 7)}`
}

function Marco({ titulo, descripcion, vacia, className = '', children }) {
  return (
    <section className={`${CLASE_TARJETA} min-w-0 p-4 sm:p-5 ${className}`}>
      <h2 className="text-sm font-medium">{titulo}</h2>
      {descripcion && <p className="text-xs text-texto-suave">{descripcion}</p>}
      <div className="mt-3 h-64">
        {vacia ? (
          <div className="flex h-full items-center justify-center rounded-lg bg-superficie-suave p-4 text-center text-sm text-texto-suave">
            Sin datos en este rango
          </div>
        ) : (
          children
        )}
      </div>
    </section>
  )
}

export default function GraficasReportes({ dias, perfilHorario, reserva }) {
  const hayEnergia = dias.some((d) => esNumero(d.kwh_generados) || esNumero(d.kwh_consumidos))
  const haySoc = dias.some((d) => esNumero(d.soc_minimo))
  const hayPerfil = perfilHorario.some((h) => esNumero(h.generacion_w) || esNumero(h.consumo_w))

  return (
    <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
      <Marco titulo="Energía diaria (kWh)" descripcion="Generada y consumida cada día" vacia={!hayEnergia}>
        <ResponsiveContainer width="100%" height="100%">
          <BarChart data={dias} margin={MARGEN} barGap={2}>
            <CartesianGrid stroke={REJILLA} vertical={false} />
            <XAxis dataKey="fecha" tickFormatter={diaCorto} tick={EJE} minTickGap={16} />
            <YAxis tick={EJE} />
            <Tooltip labelFormatter={diaCorto} formatter={(v, nombre) => [`${formatoNumero(v)} kWh`, nombre]} />
            <Legend wrapperStyle={{ fontSize: 12 }} />
            <Bar name="Generada" dataKey="kwh_generados" fill={GENERACION} radius={[4, 4, 0, 0]} isAnimationActive={false} />
            <Bar name="Consumida" dataKey="kwh_consumidos" fill={CONSUMO} radius={[4, 4, 0, 0]} isAnimationActive={false} />
          </BarChart>
        </ResponsiveContainer>
      </Marco>

      <Marco titulo="Perfil horario (W)" descripcion="Promedio de cada hora del día en el rango" vacia={!hayPerfil}>
        <ResponsiveContainer width="100%" height="100%">
          <LineChart data={perfilHorario} margin={MARGEN}>
            <CartesianGrid stroke={REJILLA} vertical={false} />
            <XAxis dataKey="hora" tickFormatter={(h) => `${h} h`} tick={EJE} minTickGap={16} />
            <YAxis tick={EJE} />
            <Tooltip labelFormatter={(h) => `${h}:00 – ${h}:59`} formatter={(v, nombre) => [formatoW(v), nombre]} />
            <Legend wrapperStyle={{ fontSize: 12 }} />
            <Line name="Generación" type="monotone" dataKey="generacion_w" stroke={GENERACION} strokeWidth={2} dot={false} isAnimationActive={false} />
            <Line name="Consumo" type="monotone" dataKey="consumo_w" stroke={CONSUMO} strokeWidth={2} dot={false} isAnimationActive={false} />
          </LineChart>
        </ResponsiveContainer>
      </Marco>

      {/* La reserva se explica fuera del área: dentro, su etiqueta chocaba con los mínimos cercanos a ella. */}
      <Marco
        titulo="Batería por día (%)"
        descripcion={`Estado de carga mínimo y máximo de cada día${esNumero(reserva) ? `. Línea punteada: reserva de la batería, ${formatoPct(reserva)}` : ''}`}
        vacia={!haySoc}
        className="lg:col-span-2"
      >
        <ResponsiveContainer width="100%" height="100%">
          <LineChart data={dias} margin={MARGEN}>
            <CartesianGrid stroke={REJILLA} vertical={false} />
            <XAxis dataKey="fecha" tickFormatter={diaCorto} tick={EJE} minTickGap={16} />
            <YAxis domain={[0, 100]} tick={EJE} />
            <Tooltip labelFormatter={diaCorto} formatter={(v, nombre) => [formatoPct(v), nombre]} />
            <Legend wrapperStyle={{ fontSize: 12 }} />
            {esNumero(reserva) && (
              <ReferenceLine
                y={reserva}
                stroke="var(--color-supervivencia)"
                strokeDasharray="4 4"
              />
            )}
            <Line name="Máximo" dataKey="soc_maximo" stroke="var(--color-marca-fuerte)" strokeWidth={2} dot={{ r: 4 }} isAnimationActive={false} />
            <Line name="Mínimo" dataKey="soc_minimo" stroke={CONSUMO} strokeWidth={2} dot={{ r: 4 }} isAnimationActive={false} />
          </LineChart>
        </ResponsiveContainer>
      </Marco>
    </div>
  )
}
