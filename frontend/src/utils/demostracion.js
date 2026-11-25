// Domos de demostración (domos.es_demostracion): sus datos son sintéticos, los generó
// backend/tools/generar_datos_demo.py. Se marcan siempre, con el mismo texto.

export const TEXTO_DEMOSTRACION = 'Demostración · datos sintéticos'

export function esDemostracion(domo) {
  return domo?.es_demostracion === true
}

// El texto de la opción del selector de domo: un <option> no admite otra marca visible.
export function nombreEnSelector(domo) {
  return esDemostracion(domo) ? `${domo.nombre} (${TEXTO_DEMOSTRACION})` : domo.nombre
}
