// Luz del bombillo: presets, escenas y color. Las listas vienen de GET /iluminacion/opciones
// (su único lugar es el backend, servicios/iluminacion.py); aquí solo se arman los cuerpos
// del PATCH y lo que se muestra.
//
// Un preset, una escena o un color con el bombillo apagado lo encienden: el mismo PATCH
// lleva estado "encendido" y el modo, y el backend publica accion:on con el modo. El brillo
// con el bombillo apagado solo se guarda (cuerpoBrillo no enciende).

const HEX = /^#[0-9a-f]{6}$/i

function conEncendido(cuerpo, encendido) {
  return encendido ? cuerpo : { estado: 'encendido', ...cuerpo }
}

// Un preset de luz blanca en un solo PATCH. brillo null = conserva el brillo actual.
export function cuerpoPreset(preset, encendido) {
  const cuerpo = { modo_luz: 'blanco', temperatura_color: preset.temperatura_color }
  if (preset.brillo != null) cuerpo.brillo = preset.brillo
  return conEncendido(cuerpo, encendido)
}

export function cuerpoEscena(escena, encendido) {
  return conEncendido({ modo_luz: 'escena', escena }, encendido)
}

export function cuerpoColor(hex, encendido) {
  return conEncendido({ modo_luz: 'color', color: hex.toLowerCase() }, encendido)
}

// Solo el brillo: el backend lo aplica en el modo actual (en color, junto con el color, sin
// perderlo). Nunca enciende.
export function cuerpoBrillo(brillo) {
  return { brillo }
}

// El preset que coincide con la luz guardada, o null. Uno que fija el brillo (Lectura,
// Relajación, Noche) gana si el brillo coincide; si no, el de su temperatura sin brillo.
export function presetActivo(dispositivo, presets) {
  if (dispositivo.modo_luz !== 'blanco') return null
  const candidatos = (presets ?? []).filter((p) => p.temperatura_color === dispositivo.temperatura_color)
  return (
    candidatos.find((p) => p.brillo != null && p.brillo === dispositivo.brillo) ??
    candidatos.find((p) => p.brillo == null) ??
    null
  )
}

// "#rrggbb" para el selector de color: el guardado si es hex, el tono de un nombre de la
// lista (p. ej. "rojo"), o blanco si no se entiende (p. ej. un valor viejo "string").
export function hexDelColor(color, colores) {
  if (typeof color === 'string' && HEX.test(color.trim())) return color.trim().toLowerCase()
  return (colores ?? []).find((c) => c.valor === color)?.hex ?? '#ffffff'
}

// Lo que muestra la tarjeta: "Blanco · Lectura", "Color", "Escena · Vela"… o null sin modo.
export function textoLuz(dispositivo, opciones) {
  const modo = dispositivo.modo_luz
  if (modo === 'blanco') {
    const preset = presetActivo(dispositivo, opciones?.presets)
    const temperatura = (opciones?.temperaturas_color ?? []).find((t) => t.valor === dispositivo.temperatura_color)
    const detalle = preset?.etiqueta ?? temperatura?.etiqueta
    return detalle ? `Blanco · ${detalle}` : 'Blanco'
  }
  if (modo === 'color') return 'Color'
  if (modo === 'escena') {
    const escena = (opciones?.escenas ?? []).find((e) => e.valor === dispositivo.escena)
    return escena ? `Escena · ${escena.etiqueta}` : 'Escena'
  }
  return null
}
