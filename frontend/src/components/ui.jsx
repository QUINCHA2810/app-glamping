import EtiquetaDemostracion from './EtiquetaDemostracion.jsx'

// Piezas de interfaz pequeñas y repetidas entre páginas.

export const CLASE_TARJETA = 'rounded-2xl border border-borde bg-superficie shadow-sm'

// text-base en el celular: con menos de 16 px, iOS acerca la página al enfocar.
// min-h-11: objetivo táctil de 44 px.
export const CLASE_CAMPO =
  'min-h-11 w-full rounded-lg border border-borde-fuerte bg-superficie px-2.5 py-1.5 text-base text-texto outline-none focus:border-marca focus:ring-2 focus:ring-marca/20 sm:text-sm'

const BOTON_BASE =
  'inline-flex min-h-11 items-center justify-center rounded-lg px-4 py-2 text-sm font-medium transition disabled:cursor-not-allowed disabled:opacity-60'
export const BOTON_PRINCIPAL = `${BOTON_BASE} bg-marca text-white hover:bg-marca-fuerte`
export const BOTON_SECUNDARIO = `${BOTON_BASE} border border-borde-fuerte bg-superficie text-texto hover:bg-superficie-suave`
export const BOTON_PELIGRO = `${BOTON_BASE} bg-supervivencia text-white hover:opacity-90`

export function Campo({ etiqueta, ayuda, children }) {
  return (
    <label className="flex min-w-0 flex-col gap-1 text-sm">
      <span className="text-xs font-medium text-texto-suave">{etiqueta}</span>
      {children}
      {ayuda && <span className="text-xs text-texto-suave">{ayuda}</span>}
    </label>
  )
}

export function AvisoError({ children }) {
  return (
    <p role="alert" className="rounded-lg border border-supervivencia/30 bg-supervivencia-suave px-3 py-2 text-sm text-supervivencia">
      {children}
    </p>
  )
}

export function Vacio({ children }) {
  return (
    <p className="rounded-2xl border border-dashed border-borde-fuerte bg-superficie p-6 text-center text-sm text-texto-suave">
      {children}
    </p>
  )
}

const TONOS_ETIQUETA = {
  neutro: 'bg-superficie-suave text-texto',
  verde: 'bg-normal-suave text-normal',
  ambar: 'bg-ahorro-suave text-ahorro',
  rojo: 'bg-supervivencia-suave text-supervivencia',
  azul: 'bg-sky-100 text-sky-900',
}

export function Etiqueta({ tono = 'neutro', children }) {
  return (
    <span className={`inline-flex items-center rounded-full px-2 py-0.5 text-xs font-semibold ${TONOS_ETIQUETA[tono]}`}>
      {children}
    </span>
  )
}

// Encabezado común de las páginas: la sección arriba, el domo como título. Con `domo`,
// marca los de demostración junto al nombre.
export function EncabezadoPagina({ seccion, titulo, domo }) {
  return (
    <header>
      <p className="text-sm font-medium text-texto-suave">{seccion}</p>
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
        <h1 className="text-2xl font-semibold tracking-tight break-words">{titulo}</h1>
        <EtiquetaDemostracion domo={domo} />
      </div>
    </header>
  )
}
