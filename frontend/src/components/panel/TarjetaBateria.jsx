import { nivelBateria } from '../../utils/energia.js'
import { esNumero, formatoPct, SIN_DATOS } from '../../utils/formato.js'
import { IconoBateria } from '../Iconos.jsx'
import Sparkline from './Sparkline.jsx'
import TarjetaKpi from './TarjetaKpi.jsx'

// El nivel siempre se dice con texto además del color.
const NIVELES = {
  normal: { texto: 'Nivel adecuado', valor: 'text-normal', barra: 'bg-normal', simbolo: '✓' },
  ahorro: { texto: 'Cerca de la reserva', valor: 'text-ahorro', barra: 'bg-ahorro', simbolo: '◐' },
  supervivencia: { texto: 'Bajo la reserva', valor: 'text-supervivencia', barra: 'bg-supervivencia', simbolo: '!' },
}

const ESTADOS_BATERIA = {
  cargando: 'Cargando',
  descargando: 'Descargando',
  cargada: 'Cargada',
  en_reposo: 'En reposo',
}

export default function TarjetaBateria({ soc, reserva, estadoBateria, vigente = true, puntos }) {
  const hayDato = esNumero(soc)
  const nivel = NIVELES[nivelBateria(soc, reserva)]
  const ancho = hayDato ? Math.min(100, Math.max(0, soc)) : 0

  return (
    <TarjetaKpi Icono={IconoBateria} titulo="Batería" valor={formatoPct(soc)} claseValor={hayDato ? (nivel?.valor ?? 'text-texto') : 'text-texto-suave'}>
      {nivel && (
        <p className={`flex items-center gap-1.5 text-sm font-semibold ${nivel.valor}`}>
          <span aria-hidden="true">{nivel.simbolo}</span>
          {nivel.texto}
        </p>
      )}
      <div
        {...(hayDato
          ? {
              role: 'meter',
              'aria-label': 'Estado de carga',
              'aria-valuemin': 0,
              'aria-valuemax': 100,
              'aria-valuenow': soc,
              'aria-valuetext': `${formatoPct(soc)}${esNumero(reserva) ? `, reserva ${formatoPct(reserva)}` : ''}`,
            }
          : { 'aria-hidden': true })}
        className="relative h-3 rounded-full bg-superficie-suave"
      >
        <div
          className={`absolute inset-y-0 left-0 rounded-full transition-[width] duration-500 ${nivel?.barra ?? 'bg-borde-fuerte'}`}
          style={{ width: `${ancho}%` }}
        />
        {esNumero(reserva) && (
          <span
            aria-hidden="true"
            className="absolute -inset-y-1 w-0.5 -translate-x-1/2 rounded-full bg-texto ring-1 ring-superficie"
            style={{ left: `${reserva}%` }}
          />
        )}
      </div>
      <p className="flex flex-wrap justify-between gap-x-3 text-xs text-texto-suave">
        <span>
          {!hayDato
            ? 'Sin dato de los últimos 2 minutos'
            : vigente
              ? (ESTADOS_BATERIA[estadoBateria] ?? estadoBateria ?? SIN_DATOS)
              : 'Última lectura'}
        </span>
        {esNumero(reserva) && <span>Reserva: {formatoPct(reserva)}</span>}
      </p>
      <Sparkline puntos={puntos} color="var(--color-marca-fuerte)" etiqueta="Batería" formato={formatoPct} />
    </TarjetaKpi>
  )
}
