import assert from 'node:assert/strict'
import { describe, it } from 'node:test'
import {
  cuerpoBrillo,
  cuerpoColor,
  cuerpoEscena,
  cuerpoPreset,
  hexDelColor,
  presetActivo,
  textoLuz,
} from './iluminacion.js'

// Igual que GET /iluminacion/opciones (servicios/iluminacion.py).
const opciones = {
  colores: [
    { valor: 'blanco', etiqueta: 'Blanco', hex: '#ffffff' },
    { valor: 'rojo', etiqueta: 'Rojo', hex: '#ff0000' },
  ],
  temperaturas_color: [
    { valor: 'calida', etiqueta: 'Cálida' },
    { valor: 'neutra', etiqueta: 'Neutra' },
    { valor: 'fria', etiqueta: 'Fría' },
  ],
  escenas: [
    { valor: 'fiesta', etiqueta: 'Fiesta' },
    { valor: 'vela', etiqueta: 'Vela' },
  ],
  presets: [
    { valor: 'calido', etiqueta: 'Cálido', temperatura_color: 'calida', brillo: null },
    { valor: 'neutro', etiqueta: 'Neutro', temperatura_color: 'neutra', brillo: null },
    { valor: 'frio', etiqueta: 'Frío', temperatura_color: 'fria', brillo: null },
    { valor: 'lectura', etiqueta: 'Lectura', temperatura_color: 'fria', brillo: 100 },
    { valor: 'relajacion', etiqueta: 'Relajación', temperatura_color: 'calida', brillo: 30 },
    { valor: 'noche', etiqueta: 'Noche', temperatura_color: 'calida', brillo: 10 },
  ],
}
const preset = (valor) => opciones.presets.find((p) => p.valor === valor)

describe('cuerpos del PATCH', () => {
  it('un preset es un solo PATCH; sin brillo propio conserva el actual', () => {
    assert.deepEqual(cuerpoPreset(preset('calido'), true), { modo_luz: 'blanco', temperatura_color: 'calida' })
    assert.deepEqual(cuerpoPreset(preset('lectura'), true), { modo_luz: 'blanco', temperatura_color: 'fria', brillo: 100 })
  })
  it('preset, escena o color con el bombillo apagado lo encienden en el mismo PATCH', () => {
    assert.deepEqual(cuerpoPreset(preset('noche'), false), {
      estado: 'encendido',
      modo_luz: 'blanco',
      temperatura_color: 'calida',
      brillo: 10,
    })
    assert.deepEqual(cuerpoEscena('vela', false), { estado: 'encendido', modo_luz: 'escena', escena: 'vela' })
    assert.deepEqual(cuerpoColor('#00FF00', false), { estado: 'encendido', modo_luz: 'color', color: '#00ff00' })
  })
  it('encendido no repite el estado', () => {
    assert.deepEqual(cuerpoEscena('fiesta', true), { modo_luz: 'escena', escena: 'fiesta' })
    assert.deepEqual(cuerpoColor('#3366cc', true), { modo_luz: 'color', color: '#3366cc' })
  })
  it('el brillo solo manda el brillo: nunca enciende ni cambia el modo', () => {
    assert.deepEqual(cuerpoBrillo(40), { brillo: 40 })
  })
})

describe('preset activo', () => {
  const blanco = (temperatura_color, brillo) => ({ modo_luz: 'blanco', temperatura_color, brillo })
  it('el que fija el brillo gana si coincide', () => {
    assert.equal(presetActivo(blanco('fria', 100), opciones.presets).valor, 'lectura')
    assert.equal(presetActivo(blanco('calida', 30), opciones.presets).valor, 'relajacion')
    assert.equal(presetActivo(blanco('calida', 10), opciones.presets).valor, 'noche')
  })
  it('si no, el de su temperatura', () => {
    assert.equal(presetActivo(blanco('fria', 70), opciones.presets).valor, 'frio')
    assert.equal(presetActivo(blanco('calida', null), opciones.presets).valor, 'calido')
    assert.equal(presetActivo(blanco('neutra', 100), opciones.presets).valor, 'neutro')
  })
  it('sin modo blanco, o sin temperatura válida, no hay preset', () => {
    assert.equal(presetActivo({ modo_luz: 'color', temperatura_color: 'fria', brillo: 100 }, opciones.presets), null)
    assert.equal(presetActivo(blanco('string', 50), opciones.presets), null)
    assert.equal(presetActivo(blanco('fria', 50), null), null)
  })
})

describe('color del selector', () => {
  it('un hex guardado se usa tal cual (en minúsculas)', () => {
    assert.equal(hexDelColor('#FF8800', opciones.colores), '#ff8800')
  })
  it('un nombre usa el tono de la lista', () => {
    assert.equal(hexDelColor('rojo', opciones.colores), '#ff0000')
  })
  it('un valor viejo o vacío queda en blanco', () => {
    assert.equal(hexDelColor('string', opciones.colores), '#ffffff')
    assert.equal(hexDelColor(null, opciones.colores), '#ffffff')
    assert.equal(hexDelColor('rojo', null), '#ffffff')
  })
})

describe('texto de la tarjeta', () => {
  it('modo y preset o escena', () => {
    assert.equal(textoLuz({ modo_luz: 'blanco', temperatura_color: 'fria', brillo: 100 }, opciones), 'Blanco · Lectura')
    assert.equal(textoLuz({ modo_luz: 'color', color: '#ff0000' }, opciones), 'Color')
    assert.equal(textoLuz({ modo_luz: 'escena', escena: 'vela' }, opciones), 'Escena · Vela')
  })
  it('sin opciones cargadas o con valores desconocidos, solo el modo', () => {
    assert.equal(textoLuz({ modo_luz: 'blanco', temperatura_color: 'fria', brillo: 100 }, null), 'Blanco')
    assert.equal(textoLuz({ modo_luz: 'escena', escena: 'otra' }, opciones), 'Escena')
  })
  it('sin modo, nada', () => {
    assert.equal(textoLuz({ modo_luz: null }, opciones), null)
  })
})
