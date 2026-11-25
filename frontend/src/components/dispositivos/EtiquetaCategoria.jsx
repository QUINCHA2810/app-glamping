import { INFO_CATEGORIA } from './categorias.js'

export default function EtiquetaCategoria({ categoria }) {
  const info = INFO_CATEGORIA[categoria]
  return (
    <span className="inline-flex items-center gap-1.5 rounded-full bg-superficie-suave px-2 py-0.5 text-xs font-semibold text-texto">
      <span aria-hidden="true" className={`size-2 rounded-full ${info?.punto ?? 'bg-borde-fuerte'}`} />
      {info?.nombre ?? categoria}
    </span>
  )
}
