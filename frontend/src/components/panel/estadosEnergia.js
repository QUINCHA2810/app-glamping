// Estado energético: lo que R-13 ya hizo. Cada uno lleva nombre y símbolo además
// del color, y las tomas que están por apagarse se dicen aparte: todavía no pasó.
export const ESTADOS_ENERGIA = {
  normal: {
    nombre: 'Normal',
    simbolo: '✓',
    detalle: 'El sistema no ha apagado ninguna carga.',
    tarjeta: 'border-borde bg-superficie',
    etiqueta: 'bg-normal-suave text-normal',
  },
  ahorro: {
    nombre: 'Ahorro',
    simbolo: '◐',
    detalle: 'El sistema apagó solo cargas prescindibles.',
    tarjeta: 'border-ahorro/30 bg-ahorro-suave',
    etiqueta: 'bg-ahorro text-white',
  },
  supervivencia: {
    nombre: 'Supervivencia',
    simbolo: '!',
    detalle: 'El sistema apagó cargas para proteger la batería.',
    tarjeta: 'border-supervivencia/30 bg-supervivencia-suave',
    etiqueta: 'bg-supervivencia text-white',
  },
}

export function estadoEnergiaDe(control) {
  return ESTADOS_ENERGIA[control?.modo_actual] ?? ESTADOS_ENERGIA.normal
}

// null si ninguna toma está por apagarse.
export function textoPorCortar(control) {
  const tomas = control?.tomas_por_cortar ?? []
  if (tomas.length === 0) return null
  const nombres = tomas.map((t) => t.nombre).join(', ')
  return tomas.length === 1
    ? `La batería bajó del umbral de ${nombres}; se apagará en la próxima evaluación.`
    : `La batería bajó del umbral de ${nombres}; se apagarán en la próxima evaluación.`
}
