import assert from 'node:assert/strict'
import { describe, it } from 'node:test'
import { descripcionClima, esViejo, rangoClimaValido, textoAntiguedad, textoSinClima } from './clima.js'

describe('código WMO', () => {
  it('texto e ícono de día', () => {
    assert.deepEqual(descripcionClima(0, true), { texto: 'Despejado', icono: 'sol' })
    assert.deepEqual(descripcionClima(2, true), { texto: 'Parcialmente nublado', icono: 'parcial' })
    assert.deepEqual(descripcionClima(3, true), { texto: 'Nublado', icono: 'nube' })
    assert.deepEqual(descripcionClima(48, true), { texto: 'Niebla', icono: 'niebla' })
    assert.deepEqual(descripcionClima(63, true), { texto: 'Lluvia', icono: 'lluvia' })
    assert.deepEqual(descripcionClima(81, true), { texto: 'Chubascos', icono: 'lluvia' })
    assert.deepEqual(descripcionClima(95, true), { texto: 'Tormenta', icono: 'tormenta' })
    assert.deepEqual(descripcionClima(99, true), { texto: 'Tormenta con granizo', icono: 'tormenta' })
  })
  it('de noche, luna en vez de sol', () => {
    assert.deepEqual(descripcionClima(1, false), { texto: 'Mayormente despejado', icono: 'luna' })
    assert.deepEqual(descripcionClima(2, false), { texto: 'Parcialmente nublado', icono: 'parcial-noche' })
    assert.equal(descripcionClima(61, false).icono, 'lluvia')
  })
  it('sin saber si es de día, como de día', () => {
    assert.equal(descripcionClima(0, null).icono, 'sol')
  })
  it('un código desconocido o nulo no rompe', () => {
    assert.deepEqual(descripcionClima(42, true), { texto: 'Condición desconocida', icono: 'nube' })
    assert.deepEqual(descripcionClima(null, true), { texto: 'Condición desconocida', icono: 'nube' })
  })
})

describe('antigüedad', () => {
  it('minutos, horas y días', () => {
    assert.equal(textoAntiguedad(20), 'Actualizado hace menos de 1 min')
    assert.equal(textoAntiguedad(60), 'Actualizado hace 1 min')
    assert.equal(textoAntiguedad(14 * 60 + 59), 'Actualizado hace 14 min')
    assert.equal(textoAntiguedad(2 * 3600), 'Actualizado hace 2 h')
    assert.equal(textoAntiguedad(2 * 3600 + 5 * 60), 'Actualizado hace 2 h 5 min')
    assert.equal(textoAntiguedad(26 * 3600), 'Actualizado hace 1 día')
    assert.equal(textoAntiguedad(3 * 86400), 'Actualizado hace 3 días')
  })
  it('sin dato', () => {
    assert.equal(textoAntiguedad(null), 'Sin fecha')
    assert.equal(textoAntiguedad(-5), 'Sin fecha')
  })
  it('viejo después de 45 min (3 consultas perdidas)', () => {
    assert.equal(esViejo(45 * 60), false)
    assert.equal(esViejo(45 * 60 + 1), true)
    assert.equal(esViejo(null), false)
  })
})

describe('sin clima', () => {
  it('explica el motivo', () => {
    assert.match(textoSinClima('sin_coordenadas'), /coordenadas/)
    assert.match(textoSinClima('sin_datos'), /15 minutos/)
    assert.equal(textoSinClima(undefined), 'Clima no disponible.')
  })
})

describe('rango de la serie', () => {
  it('hasta 31 días y en orden', () => {
    assert.equal(rangoClimaValido('2026-11-01T00:00:00Z', '2026-11-22T00:00:00Z'), true)
    assert.equal(rangoClimaValido('2026-10-01T00:00:00Z', '2026-11-22T00:00:00Z'), false)
    assert.equal(rangoClimaValido('2026-11-22T00:00:00Z', '2026-11-21T00:00:00Z'), false)
    assert.equal(rangoClimaValido('x', '2026-11-21T00:00:00Z'), false)
  })
})
