import { useState } from 'react'
import { apiFetch } from '../../api/client.js'
import { formatoPct } from '../../utils/formato.js'
import { cuerpoBrillo, cuerpoColor, cuerpoEscena, cuerpoPreset, hexDelColor, presetActivo } from '../../utils/iluminacion.js'
import Dialogo, { DialogoConfirmacion } from '../Dialogo.jsx'
import { AvisoError, BOTON_PRINCIPAL, BOTON_SECUNDARIO, Campo, CLASE_CAMPO } from '../ui.jsx'
import ControlEncendido from './ControlEncendido.jsx'
import SelectorCategoria from './SelectorCategoria.jsx'
import EtiquetaCategoria from './EtiquetaCategoria.jsx'
import { NOTA_ID_FISICO, NOTA_ID_FISICO_BOMBILLO, textoUmbrales, umbralApagadoEfectivo, umbralReactivacionEfectivo } from './categorias.js'

function Dato({ etiqueta, children }) {
  return (
    <div className="flex justify-between gap-3">
      <dt className="text-texto-suave">{etiqueta}</dt>
      <dd className="min-w-0 text-right break-words text-texto">{children}</dd>
    </div>
  )
}

// Botón de una opción de luz (preset o escena): marcado si es la activa.
function BotonOpcion({ activo, disabled, onClick, children }) {
  return (
    <button
      type="button"
      aria-pressed={activo}
      disabled={disabled}
      onClick={onClick}
      className={`min-h-11 rounded-lg border px-3 py-2 text-sm font-medium disabled:opacity-60 ${
        activo ? 'border-marca bg-marca-suave text-marca-fuerte' : 'border-borde-fuerte bg-superficie text-texto hover:bg-superficie-suave'
      }`}
    >
      {children}
    </button>
  )
}

// Presets, escenas, color y brillo. Las listas vienen de GET /iluminacion/opciones (las
// carga la página). Cada toque es un PATCH; con el bombillo apagado, un preset, una escena
// o un color lo encienden en el mismo PATCH, y el brillo solo se guarda.
function Iluminacion({ domoId, dispositivo, iluminacion, onActualizado }) {
  const opciones = iluminacion?.datos
  const encendido = dispositivo.estado === 'encendido'
  const [brillo, setBrillo] = useState(dispositivo.brillo ?? 0)
  const [color, setColor] = useState(() => hexDelColor(dispositivo.color, opciones?.colores))
  const [enviando, setEnviando] = useState(false)
  const [error, setError] = useState(null)

  const activo = presetActivo(dispositivo, opciones?.presets)
  const enEscena = dispositivo.modo_luz === 'escena'

  async function enviar(cuerpo) {
    setEnviando(true)
    setError(null)
    try {
      await apiFetch(`/domos/${domoId}/dispositivos/${dispositivo.id}`, { method: 'PATCH', body: cuerpo })
      onActualizado()
    } catch (e) {
      setError(e.message)
    } finally {
      setEnviando(false)
    }
  }

  // Al soltar el deslizador (o con el teclado): solo si cambió.
  function guardarBrillo() {
    if (brillo !== (dispositivo.brillo ?? 0)) enviar(cuerpoBrillo(brillo))
  }

  return (
    <section className="space-y-4 rounded-lg border border-borde p-3">
      <h3 className="text-sm font-semibold">Iluminación</h3>
      {!encendido && (
        <p className="text-xs text-texto-suave">
          Está apagado: un preset, una escena o un color lo encienden. El brillo solo se guarda hasta que lo enciendas.
        </p>
      )}
      {dispositivo.id_fisico == null && (
        <p className="text-xs text-texto-suave">Bombillo simulado: los cambios se guardan en la app.</p>
      )}
      {!opciones && !iluminacion?.error && <p className="text-sm text-texto-suave">Cargando opciones…</p>}
      {iluminacion?.error && <AvisoError>No se pudieron cargar las opciones de luz: {iluminacion.error}</AvisoError>}

      {opciones && (
        <>
          <div className="space-y-2">
            <p className="text-sm font-medium">Luz blanca</p>
            <div className="grid grid-cols-2 gap-2 sm:grid-cols-3">
              {opciones.presets.map((p) => (
                <BotonOpcion
                  key={p.valor}
                  activo={activo?.valor === p.valor}
                  disabled={enviando}
                  onClick={() => enviar(cuerpoPreset(p, encendido))}
                >
                  {p.etiqueta}
                </BotonOpcion>
              ))}
            </div>
          </div>

          <div className="space-y-2">
            <p className="text-sm font-medium">Escenas</p>
            <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
              {opciones.escenas.map((e) => (
                <BotonOpcion
                  key={e.valor}
                  activo={enEscena && dispositivo.escena === e.valor}
                  disabled={enviando}
                  onClick={() => enviar(cuerpoEscena(e.valor, encendido))}
                >
                  {e.etiqueta}
                </BotonOpcion>
              ))}
            </div>
          </div>

          <div className="space-y-2">
            <p className="text-sm font-medium">
              Color libre{dispositivo.modo_luz === 'color' && <span className="font-normal text-texto-suave"> (activo)</span>}
            </p>
            <div className="flex flex-wrap items-center gap-2">
              <label className="flex min-h-11 items-center gap-2 text-sm text-texto-suave">
                <input
                  type="color"
                  value={color}
                  onChange={(e) => setColor(e.target.value)}
                  className="h-11 w-14 cursor-pointer rounded-lg border border-borde-fuerte bg-superficie"
                />
                <span className="font-mono tabular-nums">{color}</span>
              </label>
              {opciones.colores
                .filter((c) => c.valor !== 'blanco')
                .map((c) => (
                  <button
                    key={c.valor}
                    type="button"
                    title={c.etiqueta}
                    aria-label={c.etiqueta}
                    onClick={() => setColor(c.hex)}
                    className="size-8 rounded-full border border-borde-fuerte"
                    style={{ backgroundColor: c.hex }}
                  />
                ))}
            </div>
            <button
              type="button"
              disabled={enviando}
              onClick={() => enviar(cuerpoColor(color, encendido))}
              className={`${BOTON_SECUNDARIO} w-full`}
            >
              Usar este color
            </button>
          </div>
        </>
      )}

      <Campo
        etiqueta={`Brillo: ${brillo} %`}
        ayuda={enEscena ? 'En una escena el brillo lo pone la escena: se aplica al volver a blanco o a color.' : undefined}
      >
        <input
          type="range"
          min={0}
          max={100}
          step={1}
          value={brillo}
          disabled={enviando}
          onChange={(e) => setBrillo(Number(e.target.value))}
          onPointerUp={guardarBrillo}
          onKeyUp={guardarBrillo}
          className="h-11 w-full accent-marca"
        />
      </Campo>
      {error && <AvisoError>{error}</AvisoError>}
    </section>
  )
}

function SoloLectura({ dispositivo, presets, configuracion }) {
  const preset = presets.find((p) => p.categoria === dispositivo.categoria)
  const apagado = umbralApagadoEfectivo(dispositivo, configuracion)
  return (
    <section className="space-y-2">
      <h3 className="text-sm font-semibold">Configuración</h3>
      <dl className="space-y-1.5 text-sm">
        <Dato etiqueta="Ubicación">{dispositivo.ubicacion || 'Sin ubicación'}</Dato>
        <Dato etiqueta="Categoría">
          <EtiquetaCategoria categoria={dispositivo.categoria} />
        </Dato>
        {apagado != null ? (
          <>
            <Dato etiqueta="Se apaga bajo">{formatoPct(apagado)}</Dato>
            <Dato etiqueta="Vuelve desde">{formatoPct(umbralReactivacionEfectivo(dispositivo, configuracion))}</Dato>
          </>
        ) : (
          <Dato etiqueta="Corte automático">{textoUmbrales(preset) || 'No aplica'}</Dato>
        )}
        <Dato etiqueta="Identificador físico">{dispositivo.id_fisico || 'Simulado'}</Dato>
      </dl>
      <p className="text-xs text-texto-suave">Estos datos los cambia el dueño.</p>
    </section>
  )
}

// Modelo v2: los umbrales de una toma no se editan. Salen de su categoría y de la batería
// y el perfil del domo (GET /domos/{id}/categorias); aquí solo se muestran.
function FormularioDueno({ domoId, dispositivo, presets, onGuardado, onActualizado }) {
  const esToma = dispositivo.tipo === 'toma'
  const [nombre, setNombre] = useState(dispositivo.nombre)
  const [ubicacion, setUbicacion] = useState(dispositivo.ubicacion ?? '')
  const [idFisico, setIdFisico] = useState(dispositivo.id_fisico ?? '')
  const [categoria, setCategoria] = useState(dispositivo.categoria)
  const [enviando, setEnviando] = useState(false)
  const [error, setError] = useState(null)
  const [confirmandoBaja, setConfirmandoBaja] = useState(false)

  const preset = presets.find((p) => p.categoria === categoria)
  const usaUmbrales = esToma && categoria !== 'esencial'
  // Un bombillo simulado puede recibir su id físico una vez; después no se edita aquí.
  const asignaIdBombillo = !esToma && dispositivo.id_fisico == null

  function armarCuerpo() {
    const cuerpo = {}
    if (nombre.trim() !== dispositivo.nombre) cuerpo.nombre = nombre.trim()
    if ((ubicacion.trim() || null) !== dispositivo.ubicacion) cuerpo.ubicacion = ubicacion.trim() || null
    if (esToma && (idFisico.trim() || null) !== dispositivo.id_fisico) cuerpo.id_fisico = idFisico.trim() || null
    if (asignaIdBombillo && idFisico.trim()) cuerpo.id_fisico = idFisico.trim()
    if (!esToma) return cuerpo

    if (categoria !== dispositivo.categoria) cuerpo.categoria = categoria
    return cuerpo
  }

  async function patch(cuerpo) {
    await apiFetch(`/domos/${domoId}/dispositivos/${dispositivo.id}`, { method: 'PATCH', body: cuerpo })
    onActualizado()
    onGuardado()
  }

  async function guardar(evento) {
    evento.preventDefault()
    const cuerpo = armarCuerpo()
    if (Object.keys(cuerpo).length === 0) {
      onGuardado()
      return
    }
    setEnviando(true)
    setError(null)
    try {
      await patch(cuerpo)
    } catch (e) {
      setError(e.message)
      setEnviando(false)
    }
  }

  async function desactivar() {
    setEnviando(true)
    setError(null)
    try {
      await patch({ activo: false })
    } catch (e) {
      setError(e.message)
      setEnviando(false)
      setConfirmandoBaja(false)
    }
  }

  async function reactivar() {
    setEnviando(true)
    setError(null)
    try {
      await patch({ activo: true })
    } catch (e) {
      setError(e.message)
      setEnviando(false)
    }
  }

  return (
    <form onSubmit={guardar} onChange={() => setError(null)} noValidate className="space-y-4">
      <Campo etiqueta="Nombre">
        <input value={nombre} onChange={(e) => setNombre(e.target.value)} className={CLASE_CAMPO} />
      </Campo>
      <Campo etiqueta="Ubicación">
        <input
          value={ubicacion}
          maxLength={100}
          placeholder="Por ejemplo: cocina"
          onChange={(e) => setUbicacion(e.target.value)}
          className={CLASE_CAMPO}
        />
      </Campo>

      {esToma ? (
        <SelectorCategoria
          presets={presets}
          valor={categoria}
          onCambiar={setCategoria}
          nombreGrupo={`categoria-${dispositivo.id}`}
        />
      ) : (
        <p className="text-sm text-texto-suave">
          Los bombillos siempre son <EtiquetaCategoria categoria="esencial" />: el sistema nunca los apaga.
        </p>
      )}

      {usaUmbrales && (
        <div className="space-y-1 rounded-lg border border-borde p-3 text-sm">
          <p className="font-medium">Umbrales calculados</p>
          <p className="text-texto-suave tabular-nums">{textoUmbrales(preset) || 'Sin datos'}.</p>
          <p className="text-xs text-texto-suave">
            Salen de la categoría y de la batería y el perfil del domo (Configuración); no se editan a mano.
          </p>
        </div>
      )}

      {esToma && (
        <Campo etiqueta="Identificador físico (id_fisico)" ayuda={NOTA_ID_FISICO}>
          <input value={idFisico} onChange={(e) => setIdFisico(e.target.value)} className={CLASE_CAMPO} />
        </Campo>
      )}
      {asignaIdBombillo && (
        <Campo etiqueta="Identificador físico (id_fisico)" ayuda={NOTA_ID_FISICO_BOMBILLO}>
          <input value={idFisico} onChange={(e) => setIdFisico(e.target.value)} className={CLASE_CAMPO} />
        </Campo>
      )}

      {error && <AvisoError>{error}</AvisoError>}

      <button type="submit" disabled={enviando} className={`${BOTON_PRINCIPAL} w-full`}>
        {enviando ? 'Guardando…' : 'Guardar cambios'}
      </button>

      <div className="border-t border-borde pt-4">
        {dispositivo.activo ? (
          <button
            type="button"
            onClick={() => setConfirmandoBaja(true)}
            className={`${BOTON_SECUNDARIO} w-full text-supervivencia`}
          >
            Desactivar
          </button>
        ) : (
          <button type="button" disabled={enviando} onClick={reactivar} className={`${BOTON_SECUNDARIO} w-full`}>
            Reactivar
          </button>
        )}
      </div>

      {confirmandoBaja && (
        <DialogoConfirmacion
          titulo={`¿Desactivar ${dispositivo.nombre}?`}
          textoConfirmar="Desactivar"
          peligro
          ocupado={enviando}
          onConfirmar={desactivar}
          onCancelar={() => setConfirmandoBaja(false)}
        >
          <p>Dejará de aparecer en la lista y el sistema dejará de controlarlo. Podrás reactivarlo después.</p>
          <p>
            <strong className="text-texto">Esto no lo apaga físicamente:</strong> si está encendido, seguirá encendido.
            Apágalo antes si eso es lo que quieres.
          </p>
        </DialogoConfirmacion>
      )}
    </form>
  )
}

export default function PanelDispositivo({
  domoId,
  dispositivo,
  categorias,
  configuracion,
  iluminacion,
  soc,
  reserva,
  esDueno,
  onCerrar,
  onActualizado,
}) {
  const presets = categorias?.categorias ?? []

  return (
    <Dialogo titulo={dispositivo.nombre} onCerrar={onCerrar}>
      {dispositivo.activo && (
        <ControlEncendido
          domoId={domoId}
          dispositivo={dispositivo}
          soc={soc}
          umbralApagado={umbralApagadoEfectivo(dispositivo, configuracion)}
          umbralReactivacion={umbralReactivacionEfectivo(dispositivo, configuracion)}
          reserva={reserva}
          onActualizado={onActualizado}
        />
      )}
      {dispositivo.activo && dispositivo.tipo === 'bombillo' && (
        <Iluminacion domoId={domoId} dispositivo={dispositivo} iluminacion={iluminacion} onActualizado={onActualizado} />
      )}
      {esDueno ? (
        <FormularioDueno
          domoId={domoId}
          dispositivo={dispositivo}
          presets={presets}
          onGuardado={onCerrar}
          onActualizado={onActualizado}
        />
      ) : (
        <SoloLectura dispositivo={dispositivo} presets={presets} configuracion={configuracion} />
      )}
    </Dialogo>
  )
}
