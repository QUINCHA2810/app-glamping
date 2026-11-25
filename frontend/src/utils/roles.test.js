import assert from 'node:assert/strict'
import { describe, it } from 'node:test'
import { domosDelDueno, esDuenoDe } from './roles.js'

const usuario = { id: 3, rol: 'dueño' }
const propio = { id: 1, nombre: 'Domo A', propietario_id: 3 }
const ajeno = { id: 2, nombre: 'Domo B', propietario_id: 9 }
const sinDueno = { id: 4, nombre: 'Domo C', propietario_id: null }

describe('rol por domo', () => {
  it('es dueño solo del domo cuya asignación de dueño es suya', () => {
    assert.equal(esDuenoDe(propio, usuario), true)
    // Dueño de otro domo, pero aquí administra: no cuenta el rol de la cuenta.
    assert.equal(esDuenoDe(ajeno, usuario), false)
  })
  it('sin domo, sin usuario o sin dueño, no es dueño', () => {
    assert.equal(esDuenoDe(null, usuario), false)
    assert.equal(esDuenoDe(propio, null), false)
    assert.equal(esDuenoDe(sinDueno, { id: null }), false)
  })
  it('los domos del dueño, en el mismo orden', () => {
    assert.deepEqual(domosDelDueno([ajeno, propio, sinDueno], usuario), [propio])
    assert.deepEqual(domosDelDueno(undefined, usuario), [])
  })
})
