import assert from 'node:assert/strict'
import { describe, it } from 'node:test'
import { API_URL_POR_DEFECTO, baseApi } from './apiUrl.js'

describe('base del API', () => {
  it('sin VITE_API_URL, la ruta relativa /api (un solo origen)', () => {
    assert.equal(API_URL_POR_DEFECTO, '/api')
    assert.equal(baseApi(undefined), '/api')
    assert.equal(baseApi(''), '/api')
    assert.equal(baseApi('   '), '/api')
  })
  it('una URL absoluta sigue funcionando, como antes', () => {
    assert.equal(baseApi('http://localhost:8000'), 'http://localhost:8000')
    assert.equal(baseApi('http://localhost:8000/'), 'http://localhost:8000')
  })
  it('sin barra final, para pegarle rutas que empiezan con /', () => {
    assert.equal(baseApi('/api/'), '/api')
    assert.equal(baseApi(' /api// '), '/api')
    assert.equal(`${baseApi('/api')}/domos/1/clima/actual`, '/api/domos/1/clima/actual')
  })
})
