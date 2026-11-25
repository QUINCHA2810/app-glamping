import { INFO_CATEGORIA, textoUmbrales } from './categorias.js'

// Las cuatro categorías como opciones grandes, con su explicación y sus umbrales.
export default function SelectorCategoria({ presets, valor, onCambiar, nombreGrupo }) {
  return (
    <fieldset className="space-y-2">
      <legend className="text-xs font-medium text-texto-suave">Categoría</legend>
      {presets.map((preset) => {
        const info = INFO_CATEGORIA[preset.categoria]
        const elegida = valor === preset.categoria
        return (
          <label
            key={preset.categoria}
            className={`flex cursor-pointer items-start gap-3 rounded-lg border p-3 text-sm transition ${
              elegida ? 'border-marca bg-marca-suave' : 'border-borde bg-superficie hover:bg-superficie-suave'
            }`}
          >
            <input
              type="radio"
              name={nombreGrupo}
              value={preset.categoria}
              checked={elegida}
              onChange={() => onCambiar(preset.categoria)}
              className="mt-1 size-4 shrink-0 accent-marca"
            />
            <span className="min-w-0">
              <span className="flex items-center gap-1.5 font-semibold text-texto">
                <span aria-hidden="true" className={`size-2 rounded-full ${info.punto}`} />
                {info.nombre}
              </span>
              <span className="block text-texto-suave">{info.explicacion}</span>
              <span className="block text-xs font-medium text-texto tabular-nums">{textoUmbrales(preset)}</span>
            </span>
          </label>
        )
      })}
    </fieldset>
  )
}
