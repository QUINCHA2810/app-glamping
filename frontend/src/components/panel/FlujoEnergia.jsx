import { flujoEnergia, textoInversor } from '../../utils/energia.js'
import { esNumero, formatoW, SIN_DATOS } from '../../utils/formato.js'
import { IconoBateria, IconoEnchufe, IconoPaneles } from '../Iconos.jsx'
import { CLASE_TARJETA } from '../ui.jsx'

function Nodo({ Icono, titulo, valor, detalle }) {
  return (
    <div className="flex min-w-0 flex-col items-center justify-center rounded-2xl bg-superficie-suave p-2 text-center sm:p-3">
      <Icono className="size-6 shrink-0 text-marca-fuerte sm:size-7" />
      <p className="text-xs font-medium text-texto-suave">{titulo}</p>
      <p className="text-sm font-semibold tabular-nums sm:text-lg">{valor}</p>
      {detalle && <p className="text-xs text-texto-suave">{detalle}</p>}
    </div>
  )
}

const GIROS = {
  derecha: '',
  'abajo-derecha': 'rotate-45',
  'arriba-derecha': '-rotate-45',
}

// Activa va sólida con el valor; inactiva, punteada y con el texto que lo dice.
function Flecha({ direccion, activa, texto, valor }) {
  return (
    <div className="flex min-w-0 flex-col items-center justify-center gap-1 px-1 py-1 text-center">
      {/* El alto reserva el espacio: girar el dibujo no cambia el que ocupa en la página. */}
      <span className="flex h-10 shrink-0 items-center justify-center">
        <svg
          viewBox="0 0 48 12"
          className={`h-3 w-10 sm:w-12 ${GIROS[direccion]} ${activa ? 'text-marca-fuerte' : 'text-borde-fuerte'}`}
          fill="none"
          stroke="currentColor"
          strokeWidth="2"
          strokeLinecap="round"
          strokeLinejoin="round"
          aria-hidden="true"
        >
          <path d="M2 6h42" strokeDasharray={activa ? undefined : '3 5'} />
          <path d="M38 1l6 5-6 5" />
        </svg>
      </span>
      <span className={`text-xs leading-tight ${activa ? 'font-medium text-texto' : 'text-texto-suave'}`}>{texto}</span>
      {activa && <span className="text-xs font-semibold tabular-nums">{formatoW(valor)}</span>}
    </div>
  )
}

const DETALLE_BATERIA = {
  cargando: 'Cargando',
  descargando: 'Descargando',
  reposo: 'En reposo',
}

function mensajeDe(flujo) {
  switch (flujo.caso) {
    case 'excedente_carga':
      return 'Los paneles cubren el consumo y el excedente carga la batería.'
    case 'excedente_sin_usar':
      return `Los paneles cubren el consumo. Sobran ${formatoW(flujo.excedente)} que la batería no está recibiendo.`
    case 'equilibrio':
      return 'Los paneles cubren justo el consumo; la batería no interviene.'
    case 'deficit_bateria':
      return `Los paneles aportan ${formatoW(flujo.directo)} y la batería entrega lo que falta.`
    case 'solo_bateria':
      return 'Sin generación: la batería alimenta las cargas.'
    case 'deficit_sin_descarga':
      return 'El consumo supera la generación, pero la batería no reporta descarga: revisa las lecturas.'
    case 'descarga_con_excedente':
      return 'Los paneles cubren el consumo, pero la batería reporta descarga: revisa las lecturas.'
    case 'sin_actividad':
      return 'Sin generación ni consumo en este momento.'
    default:
      return 'Faltan lecturas para describir el flujo.'
  }
}

// La generación alimenta primero las cargas. La batería solo recibe el excedente o
// entrega el déficit: esas dos flechas muestran su potencia medida, positiva al
// cargar y negativa al descargar. Los tres valores son promedios de los últimos 60 s
// (GET /domos/{id}/energia-reciente): la corriente FV del MPPT pulsa y un valor
// instantáneo daba flujos incoherentes.
export default function FlujoEnergia({
  generada,
  consumida,
  potenciaBateria,
  perdidas,
  eficiencia,
  vigente = true,
  className = '',
}) {
  const flujo = flujoEnergia({ generada, consumida, potenciaBateria })
  const inversor = vigente ? textoInversor({ perdidas, eficiencia }) : null
  const potencia = esNumero(potenciaBateria) ? formatoW(Math.abs(potenciaBateria)) : SIN_DATOS
  const sinDatosActuales = 'Sin datos recientes'

  return (
    <section className={`${CLASE_TARJETA} min-w-0 p-4 ${className}`}>
      <h2 className="text-sm font-medium text-texto-suave">Flujo de energía</h2>
      <div className="mt-3 grid grid-cols-3 items-stretch gap-y-1">
        <Nodo Icono={IconoPaneles} titulo="Paneles" valor={formatoW(generada)} detalle="Generación" />
        <Flecha
          direccion="derecha"
          activa={vigente && flujo.directo >= 1}
          texto={vigente ? 'Directo a las cargas' : sinDatosActuales}
          valor={flujo.directo}
        />
        <Nodo Icono={IconoEnchufe} titulo="Cargas" valor={formatoW(consumida)} detalle="Consumo" />

        <Flecha
          direccion="abajo-derecha"
          activa={vigente && flujo.aBateria > 0}
          texto={!vigente ? sinDatosActuales : flujo.aBateria > 0 ? 'Excedente a la batería' : 'No carga la batería'}
          valor={flujo.aBateria}
        />
        <Nodo
          Icono={IconoBateria}
          titulo="Batería"
          valor={potencia}
          detalle={vigente ? (DETALLE_BATERIA[flujo.sentido] ?? SIN_DATOS) : SIN_DATOS}
        />
        <Flecha
          direccion="arriba-derecha"
          activa={vigente && flujo.desdeBateria > 0}
          texto={!vigente ? sinDatosActuales : flujo.desdeBateria > 0 ? 'Cubre el déficit' : 'La batería no entrega'}
          valor={flujo.desdeBateria}
        />
      </div>
      {/* Entre el lado DC (paneles y batería) y las cargas (AC): lo que se queda en el inversor. */}
      {inversor && (
        <div className="mt-3 flex flex-col items-start gap-1 sm:items-end">
          <p className="inline-flex max-w-full items-center gap-1.5 rounded-lg border border-borde bg-superficie-suave px-2.5 py-1 text-xs font-medium tabular-nums text-texto">
            <span aria-hidden="true" className="text-texto-suave">
              DC→AC
            </span>
            <span className="min-w-0">{inversor}</span>
          </p>
          <p className="text-xs text-texto-suave sm:text-right">
            Paneles y batería en DC; cargas en AC: la diferencia se pierde en el inversor.
          </p>
        </div>
      )}
      <p className={`mt-3 text-sm ${vigente ? 'text-texto-suave' : 'font-medium text-ahorro'}`}>
        {vigente ? mensajeDe(flujo) : 'Sin lecturas de los últimos 2 minutos.'}
      </p>
      {vigente && <p className="mt-1 text-xs text-texto-suave">Promedio del último minuto.</p>}
    </section>
  )
}
