import { tramoReciente } from '../../utils/energia.js'
import { formatoHora } from '../../utils/formato.js'

const ANCHO = 100
const ALTO = 32
// Margen dentro del dibujo: el trazo tiene grosor y no debe tocar ni cortarse en los bordes.
const MARGEN_Y = 5
const MARGEN_X = 1.5

// Mini gráfica de tendencia. Una línea delgada, sin ejes: el valor exacto está en
// la tarjeta, y el resumen en aria-label y en el texto al pasar el cursor. Con huecos
// entre lecturas, muestra solo el tramo continuo más reciente, que ocupa todo el
// ancho. `escalonada` es para valores que cambian de golpe, sin lecturas periódicas.
export default function Sparkline({ puntos, color, etiqueta, dominio, escalonada = false, formato = (v) => v }) {
  const tramo = escalonada ? puntos : tramoReciente(puntos)

  if (tramo.length < 2) {
    return <p className="flex h-10 items-center text-xs text-texto-suave">Sin datos suficientes para la tendencia</p>
  }

  const valores = tramo.map((p) => p.v)
  const min = dominio?.[0] ?? Math.min(...valores)
  const max = dominio?.[1] ?? Math.max(...valores)
  const rangoV = max - min || 1
  const inicio = tramo[0].t
  const rangoT = tramo.at(-1).t - inicio || 1
  const x = (t) => MARGEN_X + ((t - inicio) / rangoT) * (ANCHO - 2 * MARGEN_X)
  // Con una serie plana, la línea queda en el centro.
  const y = (v) => (max === min ? ALTO / 2 : ALTO - MARGEN_Y - ((v - min) / rangoV) * (ALTO - 2 * MARGEN_Y))

  const trazo = tramo
    .map((p, i) => {
      if (i === 0) return `M${x(p.t).toFixed(2)} ${y(p.v).toFixed(2)}`
      if (!escalonada) return `L${x(p.t).toFixed(2)} ${y(p.v).toFixed(2)}`
      return `H${x(p.t).toFixed(2)}V${y(p.v).toFixed(2)}`
    })
    .join('')
  const desde = `Desde las ${formatoHora(new Date(inicio)).slice(0, 5)}`
  const resumen = `${etiqueta}, ${desde.toLowerCase()}: mínimo ${formato(Math.min(...valores))}, máximo ${formato(Math.max(...valores))}, ahora ${formato(valores.at(-1))}`

  return (
    <div>
      <svg viewBox={`0 0 ${ANCHO} ${ALTO}`} preserveAspectRatio="none" role="img" aria-label={resumen} className="h-10 w-full">
        <title>{resumen}</title>
        <path d={trazo} fill="none" stroke={color} strokeWidth="2" strokeLinejoin="round" vectorEffect="non-scaling-stroke" />
      </svg>
      <p className="text-right text-[11px] text-texto-suave tabular-nums">{desde}</p>
    </div>
  )
}
