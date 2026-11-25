import { useState } from 'react'
import { apiFetch } from '../../api/client.js'
import Dialogo from '../Dialogo.jsx'
import { AvisoError, BOTON_PRINCIPAL, BOTON_SECUNDARIO, Campo, CLASE_CAMPO } from '../ui.jsx'
import SelectorCategoria from './SelectorCategoria.jsx'
import { NOTA_ID_FISICO, NOTA_ID_FISICO_BOMBILLO } from './categorias.js'

const TIPOS = [
  { tipo: 'toma', nombre: 'Tomacorriente', detalle: 'Puede tener categoría, y el sistema puede apagarlo para proteger la batería.' },
  { tipo: 'bombillo', nombre: 'Bombillo', detalle: 'Siempre es esencial: el sistema nunca lo apaga.' },
]

// Asistente corto para añadir un dispositivo. Un bombillo se salta el último paso.
export default function AsistenteDispositivo({ domoId, categorias, onCerrar, onCreado }) {
  const [paso, setPaso] = useState(1)
  const [tipo, setTipo] = useState('toma')
  const [nombre, setNombre] = useState('')
  const [ubicacion, setUbicacion] = useState('')
  const [categoria, setCategoria] = useState('confort')
  const [idFisico, setIdFisico] = useState('')
  const [enviando, setEnviando] = useState(false)
  const [error, setError] = useState(null)

  const esToma = tipo === 'toma'
  const totalPasos = esToma ? 3 : 2
  const esUltimo = paso === totalPasos

  async function crear() {
    setEnviando(true)
    setError(null)
    const cuerpo = { tipo, nombre: nombre.trim() }
    if (ubicacion.trim()) cuerpo.ubicacion = ubicacion.trim()
    if (esToma) cuerpo.categoria = categoria
    if (idFisico.trim()) cuerpo.id_fisico = idFisico.trim()
    try {
      await apiFetch(`/domos/${domoId}/dispositivos`, { method: 'POST', body: cuerpo })
      onCreado()
      onCerrar()
    } catch (e) {
      setError(e.message)
      setEnviando(false)
    }
  }

  function avanzar(evento) {
    evento.preventDefault()
    if (paso === 2 && !nombre.trim()) {
      setError('Escribe un nombre.')
      return
    }
    setError(null)
    if (esUltimo) crear()
    else setPaso(paso + 1)
  }

  return (
    <Dialogo titulo="Añadir dispositivo" onCerrar={onCerrar}>
      <p className="text-xs font-medium text-texto-suave">
        Paso {paso} de {totalPasos}
      </p>
      <form onSubmit={avanzar} noValidate className="space-y-4">
        {paso === 1 && (
          <fieldset className="space-y-2">
            <legend className="text-xs font-medium text-texto-suave">Tipo</legend>
            {TIPOS.map((opcion) => (
              <label
                key={opcion.tipo}
                className={`flex cursor-pointer items-start gap-3 rounded-lg border p-3 text-sm ${
                  tipo === opcion.tipo ? 'border-marca bg-marca-suave' : 'border-borde'
                }`}
              >
                <input
                  type="radio"
                  name="tipo-dispositivo"
                  checked={tipo === opcion.tipo}
                  onChange={() => setTipo(opcion.tipo)}
                  className="mt-1 size-4 shrink-0 accent-marca"
                />
                <span>
                  <span className="block font-semibold text-texto">{opcion.nombre}</span>
                  <span className="block text-texto-suave">{opcion.detalle}</span>
                </span>
              </label>
            ))}
          </fieldset>
        )}

        {paso === 2 && (
          <>
            <Campo etiqueta="Nombre">
              <input autoFocus value={nombre} onChange={(e) => setNombre(e.target.value)} className={CLASE_CAMPO} />
            </Campo>
            <Campo etiqueta="Ubicación (opcional)">
              <input
                value={ubicacion}
                maxLength={100}
                placeholder="Por ejemplo: cocina"
                onChange={(e) => setUbicacion(e.target.value)}
                className={CLASE_CAMPO}
              />
            </Campo>
            {/* El bombillo no tiene paso 3: su id físico va aquí. */}
            {!esToma && (
              <Campo etiqueta="Identificador físico (opcional)" ayuda={NOTA_ID_FISICO_BOMBILLO}>
                <input value={idFisico} onChange={(e) => setIdFisico(e.target.value)} className={CLASE_CAMPO} />
              </Campo>
            )}
          </>
        )}

        {paso === 3 && (
          <>
            <SelectorCategoria
              presets={categorias?.categorias ?? []}
              valor={categoria}
              onCambiar={setCategoria}
              nombreGrupo="categoria-nueva"
            />
            <Campo etiqueta="Identificador físico (opcional)" ayuda={NOTA_ID_FISICO}>
              <input value={idFisico} onChange={(e) => setIdFisico(e.target.value)} className={CLASE_CAMPO} />
            </Campo>
          </>
        )}

        {error && <AvisoError>{error}</AvisoError>}

        <div className="flex flex-col-reverse gap-2 sm:flex-row sm:justify-end">
          {paso > 1 ? (
            <button
              type="button"
              onClick={() => {
                setError(null)
                setPaso(paso - 1)
              }}
              className={BOTON_SECUNDARIO}
            >
              Atrás
            </button>
          ) : (
            <button type="button" onClick={onCerrar} className={BOTON_SECUNDARIO}>
              Cancelar
            </button>
          )}
          <button type="submit" disabled={enviando} className={BOTON_PRINCIPAL}>
            {esUltimo ? (enviando ? 'Añadiendo…' : 'Añadir') : 'Siguiente'}
          </button>
        </div>
      </form>
    </Dialogo>
  )
}
