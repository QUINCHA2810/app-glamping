// Interruptor encendido/apagado accesible (role="switch").
export default function Interruptor({ encendido, deshabilitado, onCambiar, etiqueta }) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={encendido}
      aria-label={etiqueta}
      disabled={deshabilitado}
      onClick={() => onCambiar(!encendido)}
      className="inline-flex h-11 w-12 shrink-0 items-center rounded-lg disabled:cursor-wait disabled:opacity-60"
    >
      <span className={`relative inline-flex h-7 w-12 items-center rounded-full transition-colors ${encendido ? 'bg-marca' : 'bg-borde-fuerte'}`}>
        <span
          className={`inline-block size-5 rounded-full bg-superficie shadow transition-transform ${
            encendido ? 'translate-x-6' : 'translate-x-1'
          }`}
        />
      </span>
    </button>
  )
}
