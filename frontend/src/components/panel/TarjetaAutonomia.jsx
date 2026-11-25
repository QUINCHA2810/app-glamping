import { useId, useState } from 'react'
import { textoAutonomia } from '../../utils/energia.js'
import { IconoAyuda, IconoReloj } from '../Iconos.jsx'
import { CLASE_TARJETA } from '../ui.jsx'

// `energia`: GET /domos/{id}/energia-reciente. El backend promedia la potencia de la batería
// de los últimos 15 min (servicios/energia_reciente.py): así un pico de segundos no hace
// saltar las horas, y todos los clientes ven lo mismo.
export default function TarjetaAutonomia({ energia, reserva }) {
  const [ayudaAbierta, setAyudaAbierta] = useState(false)
  const idAyuda = useId()
  const t = textoAutonomia(energia, reserva)
  const enReserva = energia?.autonomia?.tipo === 'reserva'

  return (
    <section className={`${CLASE_TARJETA} flex min-w-0 flex-col gap-2 p-4`}>
      <div className="flex items-start justify-between gap-2">
        <h2 className="flex items-center gap-2 pt-2.5 text-sm font-medium text-texto-suave">
          <IconoReloj className="size-5 shrink-0" />
          Autonomía estimada
        </h2>
        <button
          type="button"
          onClick={() => setAyudaAbierta((v) => !v)}
          aria-expanded={ayudaAbierta}
          aria-controls={idAyuda}
          aria-label="Cómo se calcula la autonomía"
          className="-m-1 flex size-11 shrink-0 items-center justify-center rounded-lg text-texto-suave hover:bg-superficie-suave"
        >
          <IconoAyuda />
        </button>
      </div>

      <p className={`font-semibold tracking-tight tabular-nums ${t.grande ? 'text-3xl' : 'text-xl'} ${enReserva ? 'text-supervivencia' : 'text-texto'}`}>
        {t.valor}
      </p>
      <p className="text-sm text-texto-suave">{t.detalle}</p>
      {t.sinSol && <p className="text-sm font-medium text-texto tabular-nums">{t.sinSol}</p>}
      {t.leyenda && <p className="text-xs text-texto-suave">{t.leyenda}</p>}

      <div id={idAyuda} hidden={!ayudaAbierta} className="space-y-1 rounded-lg bg-superficie-suave p-3 text-sm text-texto-suave">
        <p>
          Horas ≈ capacidad (Wh) × (SoC − reserva) ÷ 100 ÷ descarga media de la batería (W) en los últimos 15 minutos. Esa
          descarga ya descuenta lo que aportan los paneles, así que un pico de segundos casi no la mueve.
        </p>
        <p>
          Si en promedio la batería está cargando, se muestra «Cargando»; si casi no se mueve, «Más de 72 h». En los dos casos
          se muestra aparte cuánto duraría sin sol con el consumo medio (inversor y equipos de monitoreo).
        </p>
        <p>
          Es una estimación: depende del SoC estimado por voltaje (que sobreestima mientras carga) y de la potencia FV estimada
          con el voltaje nominal del panel.
        </p>
        <p>No se muestra con menos de 2 minutos de lecturas, si la última tiene más de 2 minutos o si la batería ya está en la reserva.</p>
      </div>
    </section>
  )
}
