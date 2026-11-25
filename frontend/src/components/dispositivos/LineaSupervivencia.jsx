import { esNumero, formatoPct, SIN_DATOS } from '../../utils/formato.js'
import { CLASE_TARJETA } from '../ui.jsx'
import { INFO_CATEGORIA, umbralApagadoEfectivo, umbralReactivacionEfectivo } from './categorias.js'

const MARCAS_ESCALA = [0, 25, 50, 75, 100]

function limitar(valor) {
  return Math.min(100, Math.max(0, valor))
}

function LineaSoc({ soc }) {
  if (!esNumero(soc)) return null
  return (
    <span
      aria-hidden="true"
      className="absolute inset-y-[-3px] w-0.5 -translate-x-1/2 rounded-full bg-texto"
      style={{ left: `${limitar(soc)}%` }}
    />
  )
}

// Una toma en la misma escala que la barra: círculo lleno donde se apaga, círculo
// vacío donde se reactiva. Cada toma tiene su fila para que las marcas no se pisen,
// y los valores también están escritos: el color no es la única pista.
function FilaToma({ toma, apagado, reactivacion, soc }) {
  const info = INFO_CATEGORIA[toma.categoria]
  return (
    <li className="space-y-1">
      <div className="flex flex-wrap items-baseline justify-between gap-x-3 text-xs">
        <span className="flex min-w-0 items-center gap-1.5 font-medium text-texto">
          <span aria-hidden="true" className={`size-2 shrink-0 rounded-full ${info.punto}`} />
          <span className="truncate">{toma.nombre}</span>
        </span>
        <span className="text-texto-suave tabular-nums">
          Se apaga bajo {formatoPct(apagado)} · vuelve desde {formatoPct(reactivacion)}
        </span>
      </div>
      <div className="relative h-3" aria-hidden="true">
        <div className="absolute inset-x-0 top-1/2 h-px -translate-y-1/2 bg-borde" />
        <div
          className={`absolute top-1/2 h-1 -translate-y-1/2 rounded-full opacity-40 ${info.punto}`}
          style={{ left: `${limitar(apagado)}%`, width: `${limitar(reactivacion) - limitar(apagado)}%` }}
        />
        <LineaSoc soc={soc} />
        <span
          className={`absolute top-1/2 size-3 -translate-x-1/2 -translate-y-1/2 rounded-full ring-2 ring-superficie ${info.punto}`}
          style={{ left: `${limitar(apagado)}%` }}
        />
        <span
          className="absolute top-1/2 size-3 -translate-x-1/2 -translate-y-1/2 rounded-full border-2 border-texto-suave bg-superficie ring-2 ring-superficie"
          style={{ left: `${limitar(reactivacion)}%` }}
        />
      </div>
    </li>
  )
}

// Barra 0–100 %: la reserva sombreada, el SoC actual y, debajo, dónde se apaga y
// dónde se reactiva cada toma que el sistema puede cortar.
export default function LineaSupervivencia({ dispositivos, soc, reserva, configuracion }) {
  const tomas = dispositivos
    .map((toma) => ({
      toma,
      apagado: umbralApagadoEfectivo(toma, configuracion),
      reactivacion: umbralReactivacionEfectivo(toma, configuracion),
    }))
    .filter(({ apagado, reactivacion }) => esNumero(apagado) && esNumero(reactivacion))
    .sort((a, b) => b.apagado - a.apagado || a.toma.id - b.toma.id)
  const protegidos = dispositivos.length - tomas.length
  const hayReserva = esNumero(reserva)
  const haySoc = esNumero(soc)

  return (
    <section className={`${CLASE_TARJETA} space-y-3 p-4`}>
      <div className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1">
        <h2 className="text-sm font-semibold text-texto">Línea de supervivencia</h2>
        <p className="text-sm text-texto-suave">
          Batería: <strong className="font-semibold text-texto tabular-nums">{haySoc ? formatoPct(soc) : SIN_DATOS}</strong>
        </p>
      </div>

      <div>
        <div
          role={haySoc ? 'meter' : 'img'}
          aria-label={haySoc ? 'Estado de carga de la batería' : `Estado de carga de la batería: ${SIN_DATOS}`}
          {...(haySoc && {
            'aria-valuemin': 0,
            'aria-valuemax': 100,
            'aria-valuenow': soc,
            'aria-valuetext': `${formatoPct(soc)}${hayReserva ? `, reserva ${formatoPct(reserva)}` : ''}`,
          })}
          className="relative h-5 rounded-md bg-superficie-suave"
        >
          {haySoc && (
            <div className="absolute inset-y-0 left-0 rounded-md bg-marca/25" style={{ width: `${limitar(soc)}%` }} />
          )}
          {hayReserva && (
            <div className="franjas-reserva absolute inset-y-0 left-0 rounded-l-md" style={{ width: `${limitar(reserva)}%` }} />
          )}
          <LineaSoc soc={soc} />
        </div>
        <div className="relative mt-1 h-4 text-[11px] text-texto-suave tabular-nums" aria-hidden="true">
          {MARCAS_ESCALA.map((marca) => (
            <span
              key={marca}
              className={`absolute ${marca === 0 ? '' : marca === 100 ? '-translate-x-full' : '-translate-x-1/2'}`}
              style={{ left: `${marca}%` }}
            >
              {marca}
            </span>
          ))}
        </div>
      </div>

      {tomas.length > 0 && (
        <ul className="space-y-3">
          {tomas.map(({ toma, apagado, reactivacion }) => (
            <FilaToma key={toma.id} toma={toma} apagado={apagado} reactivacion={reactivacion} soc={soc} />
          ))}
        </ul>
      )}

      <ul className="flex flex-wrap gap-x-4 gap-y-1 text-xs text-texto-suave">
        {hayReserva && (
          <li className="flex items-center gap-1.5">
            <span aria-hidden="true" className="franjas-reserva size-3 rounded-sm" />
            Reserva: {formatoPct(reserva)}
          </li>
        )}
        <li className="flex items-center gap-1.5">
          <span aria-hidden="true" className="h-3 w-0.5 bg-texto" />
          Batería ahora
        </li>
        {tomas.length > 0 && (
          <>
            <li className="flex items-center gap-1.5">
              <span aria-hidden="true" className="size-2.5 rounded-full bg-texto-suave" />
              Se apaga
            </li>
            <li className="flex items-center gap-1.5">
              <span aria-hidden="true" className="size-2.5 rounded-full border-2 border-texto-suave" />
              Se reactiva
            </li>
          </>
        )}
      </ul>

      {protegidos > 0 && (
        <p className="text-xs text-texto-suave">
          {protegidos === 1 ? '1 dispositivo esencial no aparece' : `${protegidos} dispositivos esenciales no aparecen`}: el
          sistema nunca los apaga.
        </p>
      )}
    </section>
  )
}
