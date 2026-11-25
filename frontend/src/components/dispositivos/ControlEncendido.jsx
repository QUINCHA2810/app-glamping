import { useState } from 'react'
import { apiFetch } from '../../api/client.js'
import { avisoConfirmacion } from '../../utils/comunicacion.js'
import { esNumero, formatoPct } from '../../utils/formato.js'
import { textoAlEncenderBajoUmbral, textoPrimaciaHumana } from '../../utils/primacia.js'
import { DialogoConfirmacion } from '../Dialogo.jsx'
import { AvisoError, BOTON_PRINCIPAL, BOTON_SECUNDARIO } from '../ui.jsx'

const AVISO_TOMA_REAL = 'Orden enviada al tomacorriente; puede tardar unos segundos en conmutar.'

// Estado actual y botón de encender/apagar, en la tarjeta y en el panel. Tras un
// PATCH se muestra la respuesta del backend hasta que llega el siguiente dato del
// polling, que se reconoce porque es otro objeto.
// Primacía humana (DB-4): si la encendió una persona, el backend lo dice en
// primacia_humana_hasta_soc y aquí se muestra hasta cuándo la respeta el sistema.
export default function ControlEncendido({ domoId, dispositivo, soc, umbralApagado, umbralReactivacion, reserva, onActualizado }) {
  const [enviando, setEnviando] = useState(false)
  const [error, setError] = useState(null)
  const [aviso, setAviso] = useState(null)
  const [confirmado, setConfirmado] = useState({ base: null, estado: null, confirmacion: null })
  const [advirtiendo, setAdvirtiendo] = useState(false)

  const propio = confirmado.base === dispositivo
  const estado = propio ? confirmado.estado : dispositivo.estado
  // ¿La toma real confirmó la última orden? (backend: confirmacion). "Confirmando…" no se
  // muestra junto al aviso de orden enviada, que dice lo mismo.
  const avisoOrden = avisoConfirmacion(propio ? confirmado.confirmacion : dispositivo.confirmacion)
  const encendido = estado === 'encendido'
  const seApagaria = esNumero(soc) && esNumero(umbralApagado) && soc < umbralApagado
  const primacia = encendido ? textoPrimaciaHumana(dispositivo.primacia_humana_hasta_soc, reserva) : null

  async function enviar(nuevoEstado) {
    setAdvirtiendo(false)
    setEnviando(true)
    setError(null)
    setAviso(null)
    try {
      const respuesta = await apiFetch(`/domos/${domoId}/dispositivos/${dispositivo.id}`, {
        method: 'PATCH',
        body: { estado: nuevoEstado },
      })
      setConfirmado({ base: dispositivo, estado: respuesta.estado, confirmacion: respuesta.confirmacion })
      // Solo las tomas reales reciben la orden por MQTT (ver backend,
      // routers/dispositivos.py); los bombillos no se controlan por esa vía.
      if (dispositivo.tipo === 'toma' && dispositivo.id_fisico != null) setAviso(AVISO_TOMA_REAL)
      onActualizado()
    } catch (e) {
      setError(e.message)
    } finally {
      setEnviando(false)
    }
  }

  function pulsar() {
    if (!encendido && seApagaria) setAdvirtiendo(true)
    else enviar(encendido ? 'apagado' : 'encendido')
  }

  return (
    <div className="space-y-2">
      <div className="flex items-center justify-between gap-3">
        <span className={`flex items-center gap-2 text-sm font-medium ${encendido ? 'text-normal' : 'text-texto-suave'}`}>
          <span aria-hidden="true" className={`size-2 rounded-full ${encendido ? 'bg-normal' : 'bg-borde-fuerte'}`} />
          {encendido ? 'Encendido' : estado === 'apagado' ? 'Apagado' : estado}
        </span>
        <button
          type="button"
          disabled={enviando}
          onClick={pulsar}
          aria-label={`${encendido ? 'Apagar' : 'Encender'} ${dispositivo.nombre}`}
          className={encendido ? BOTON_SECUNDARIO : BOTON_PRINCIPAL}
        >
          {enviando ? 'Enviando…' : encendido ? 'Apagar' : 'Encender'}
        </button>
      </div>

      {avisoOrden && !(avisoOrden.tono === 'suave' && aviso) && (
        <p
          role="status"
          className={`rounded-lg px-3 py-2 text-sm ${
            avisoOrden.tono === 'supervivencia'
              ? 'bg-supervivencia-suave text-supervivencia'
              : avisoOrden.tono === 'ahorro'
                ? 'bg-ahorro-suave text-ahorro'
                : 'bg-superficie-suave text-texto-suave'
          }`}
        >
          <strong className="font-semibold">{avisoOrden.titulo}</strong> {avisoOrden.detalle}
        </p>
      )}
      {primacia && <p className="text-xs text-texto-suave">{primacia}</p>}
      {error && <AvisoError>{error}</AvisoError>}
      {aviso && !error && (
        <p role="status" className="rounded-lg bg-ahorro-suave px-3 py-2 text-sm text-ahorro">
          {aviso}
        </p>
      )}

      {advirtiendo && (
        <DialogoConfirmacion
          titulo={`¿Encender ${dispositivo.nombre}?`}
          textoConfirmar="Encender de todos modos"
          onConfirmar={() => enviar('encendido')}
          onCancelar={() => setAdvirtiendo(false)}
        >
          <p>
            La batería está en <strong className="text-texto">{formatoPct(soc)}</strong>, por debajo del{' '}
            <strong className="text-texto">{formatoPct(umbralApagado)}</strong> en el que el sistema apaga esta toma.
          </p>
          <p>{textoAlEncenderBajoUmbral(soc, umbralReactivacion, reserva)}</p>
        </DialogoConfirmacion>
      )}
    </div>
  )
}
