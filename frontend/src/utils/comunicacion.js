// Confirmación de la última orden de una toma real (backend: DispositivoOut.confirmacion,
// servicios/comunicacion.py). null = simulada, bombillo o sin órdenes ni respuestas.
const AVISOS = {
  pendiente: { titulo: 'Confirmando…', detalle: 'Esperando la respuesta de la toma.', tono: 'suave' },
  sin_confirmar: {
    titulo: 'Sin confirmar',
    detalle: 'La última orden no tuvo respuesta; su estado real puede ser otro.',
    tono: 'ahorro',
  },
  sin_conexion: {
    titulo: 'Sin conexión con la toma',
    detalle: 'La orden no se confirmó; su estado real puede ser otro.',
    tono: 'supervivencia',
  },
}

export function avisoConfirmacion(confirmacion) {
  return AVISOS[confirmacion] ?? null
}

// Una toma encendida cuenta como carga activa solo si su orden está confirmada (o es
// simulada): una sin confirmar o sin conexión puede estar apagada en realidad.
export function cuentaComoEncendida(dispositivo) {
  return dispositivo.estado === 'encendido' && (dispositivo.confirmacion == null || dispositivo.confirmacion === 'confirmada')
}

export function cargasActivas(dispositivos) {
  const encendidas = dispositivos.filter(cuentaComoEncendida).length
  const sinConfirmar = dispositivos.filter((d) => d.estado === 'encendido' && !cuentaComoEncendida(d)).length
  return { encendidas, sinConfirmar }
}
