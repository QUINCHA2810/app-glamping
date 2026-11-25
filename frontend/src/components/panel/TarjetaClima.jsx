import { useCallback } from 'react'
import { apiFetch } from '../../api/client.js'
import { usePolling } from '../../hooks/usePolling.js'
import { descripcionClima, esViejo, textoAntiguedad, textoSinClima } from '../../utils/clima.js'
import { esNumero } from '../../utils/formato.js'
import FuenteClima from '../FuenteClima.jsx'
import {
  IconoLluvia,
  IconoLuna,
  IconoLunaNube,
  IconoNiebla,
  IconoNieve,
  IconoNube,
  IconoSol,
  IconoSolNube,
  IconoTormenta,
} from '../Iconos.jsx'
import { CLASE_TARJETA } from '../ui.jsx'

// El backend consulta Open-Meteo cada 15 min: basta con mirar cada 5.
const INTERVALO_MS = 5 * 60_000

const ICONOS = {
  sol: IconoSol,
  luna: IconoLuna,
  parcial: IconoSolNube,
  'parcial-noche': IconoLunaNube,
  nube: IconoNube,
  niebla: IconoNiebla,
  lluvia: IconoLluvia,
  nieve: IconoNieve,
  tormenta: IconoTormenta,
}

const entero = new Intl.NumberFormat('es-CO', { maximumFractionDigits: 0 })
const unDecimal = new Intl.NumberFormat('es-CO', { minimumFractionDigits: 1, maximumFractionDigits: 1 })
const con = (valor, formato, unidad) => (esNumero(valor) ? `${formato.format(valor)} ${unidad}` : 'Sin dato')

function Dato({ nombre, valor }) {
  return (
    <div className="min-w-0">
      <dt className="text-xs text-texto-suave">{nombre}</dt>
      <dd className="text-lg font-semibold tabular-nums">{valor}</dd>
    </div>
  )
}

// Clima de referencia del domo, para entender la generación solar. Tiene su propia
// consulta: si falla, el resto del panel sigue igual.
export default function TarjetaClima({ domoId }) {
  const consultar = useCallback(() => apiFetch(`/domos/${domoId}/clima/actual`), [domoId])
  const { datos, error } = usePolling(consultar, INTERVALO_MS)

  let contenido
  if (!datos) {
    contenido = error ? (
      <p className="text-sm text-texto-suave">No se pudo cargar el clima: {error}</p>
    ) : (
      <div className="h-16 animate-pulse rounded-lg bg-superficie-suave" aria-label="Cargando clima" />
    )
  } else if (!datos.disponible) {
    contenido = <p className="text-sm text-texto-suave">{textoSinClima(datos.motivo)}</p>
  } else {
    const c = datos.clima
    const { texto, icono } = descripcionClima(c.codigo_clima, c.es_de_dia)
    const Icono = ICONOS[icono]
    const viejo = esViejo(datos.antiguedad_s)
    contenido = (
      <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:gap-6">
        <div className="flex items-center gap-3">
          <Icono className="size-10 shrink-0 text-ahorro" />
          <div>
            <p className="text-lg font-semibold">{texto}</p>
            <p className={`text-sm ${viejo ? 'text-ahorro' : 'text-texto-suave'}`}>
              {textoAntiguedad(datos.antiguedad_s)}
              {viejo && ' · puede no reflejar el clima actual'}
            </p>
          </div>
        </div>
        <dl className="grid flex-1 grid-cols-3 gap-3">
          <Dato nombre="Nubosidad" valor={con(c.nubosidad_pct, entero, '%')} />
          <Dato nombre="Radiación solar" valor={con(c.radiacion_w_m2, entero, 'W/m²')} />
          <Dato nombre="Temperatura" valor={con(c.temperatura_c, unDecimal, '°C')} />
        </dl>
      </div>
    )
  }

  return (
    <section className={`${CLASE_TARJETA} flex min-w-0 flex-col gap-3 p-4`}>
      <div className="flex flex-wrap items-baseline justify-between gap-x-3">
        <h2 className="text-sm font-medium text-texto-suave">Clima de referencia</h2>
        {datos?.disponible && esNumero(datos.clima.precipitacion_mm) && datos.clima.precipitacion_mm > 0 && (
          <p className="text-xs text-texto-suave tabular-nums">
            Precipitación {unDecimal.format(datos.clima.precipitacion_mm)} mm
          </p>
        )}
      </div>
      {contenido}
      {datos?.disponible && <FuenteClima />}
    </section>
  )
}
