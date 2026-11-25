import assert from 'node:assert/strict'
import { describe, it } from 'node:test'
import { avisoConfirmacion, cargasActivas, cuentaComoEncendida } from './comunicacion.js'

const toma = (estado, confirmacion = null) => ({ tipo: 'toma', estado, confirmacion })

describe('cargas activas', () => {
  it('no cuenta como encendida una toma sin confirmar, sin conexión o pendiente', () => {
    assert.equal(cuentaComoEncendida(toma('encendido')), true) // simulada
    assert.equal(cuentaComoEncendida(toma('encendido', 'confirmada')), true)
    assert.equal(cuentaComoEncendida(toma('encendido', 'sin_conexion')), false)
    assert.equal(cuentaComoEncendida(toma('encendido', 'sin_confirmar')), false)
    assert.equal(cuentaComoEncendida(toma('encendido', 'pendiente')), false)
    assert.equal(cuentaComoEncendida(toma('apagado', 'confirmada')), false)
  })
  it('cuenta las encendidas y aparte las que no se confirmaron', () => {
    const lista = [toma('encendido'), toma('encendido', 'sin_conexion'), toma('apagado', 'sin_conexion'), toma('encendido', 'confirmada')]
    assert.deepEqual(cargasActivas(lista), { encendidas: 2, sinConfirmar: 1 })
  })
})

describe('avisoConfirmacion', () => {
  it('textos de la tarjeta', () => {
    assert.equal(avisoConfirmacion('sin_conexion').titulo, 'Sin conexión con la toma')
    assert.equal(avisoConfirmacion('sin_confirmar').titulo, 'Sin confirmar')
    assert.equal(avisoConfirmacion('pendiente').titulo, 'Confirmando…')
    assert.equal(avisoConfirmacion('confirmada'), null)
    assert.equal(avisoConfirmacion(null), null)
  })
})
