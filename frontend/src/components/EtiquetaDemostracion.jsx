import { esDemostracion, TEXTO_DEMOSTRACION } from '../utils/demostracion.js'

// Borde punteado y colores neutros: no usa los colores de estado (normal, ahorro,
// supervivencia), que tienen otro significado.
export default function EtiquetaDemostracion({ domo, className = '' }) {
  if (!esDemostracion(domo)) return null
  return (
    <span
      className={`inline-flex w-fit items-center gap-1.5 rounded-full border border-dashed border-borde-fuerte bg-superficie-suave px-2.5 py-0.5 text-xs font-semibold text-texto ${className}`}
    >
      <svg aria-hidden="true" viewBox="0 0 16 16" className="size-3.5" fill="none" stroke="currentColor" strokeWidth="1.6">
        <path d="M6 2h4M6.5 2v4.2L3 12.5A1 1 0 0 0 3.9 14h8.2a1 1 0 0 0 .9-1.5L9.5 6.2V2" strokeLinejoin="round" />
      </svg>
      {TEXTO_DEMOSTRACION}
    </span>
  )
}
