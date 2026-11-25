import { useCallback, useState } from 'react'
import { Navigate } from 'react-router-dom'
import { apiFetch } from '../api/client.js'
import { useAuth } from '../auth/AuthContext.jsx'
import PaginaDomo from '../components/PaginaDomo.jsx'
import RecalibracionSoc from '../components/configuracion/RecalibracionSoc.jsx'
import { INFO_CATEGORIA } from '../components/dispositivos/categorias.js'
import { AvisoError, BOTON_PRINCIPAL, Campo, CLASE_CAMPO, CLASE_TARJETA } from '../components/ui.jsx'
import { useConsulta } from '../hooks/useConsulta.js'
import { formatoPct } from '../utils/formato.js'
import { esDuenoDe } from '../utils/roles.js'

const TIPOS_BATERIA = [
  {
    tipo: 'agm',
    nombre: 'AGM (plomo sellado)',
    detalle: 'Se desgasta rápido si se descarga mucho, así que el sistema deja más energía sin usar.',
  },
  {
    tipo: 'lifepo4',
    nombre: 'LiFePO4 (litio)',
    detalle: 'Tolera descargas más profundas, así que el sistema puede usar más de su capacidad.',
  },
]

const PERFILES = {
  conservador: { nombre: 'Conservador', detalle: 'Apaga antes lo prescindible y deja más margen en la batería.' },
  equilibrado: { nombre: 'Equilibrado', detalle: 'Un punto medio entre margen y aprovechamiento.' },
  maximo_uso: { nombre: 'Máximo uso', detalle: 'Mantiene las cargas encendidas más tiempo y aprovecha más la batería.' },
}

const NOMBRE_BATERIA = { agm: 'AGM', lifepo4: 'LiFePO4' }

function plural(cantidad, singular, varias) {
  return cantidad === 1 ? singular : varias
}

function ResultadoGuardado({ resultado }) {
  return (
    <p role="status" className="rounded-lg bg-normal-suave px-3 py-2 text-sm text-normal">
      Guardado. {resultado.tomas_recalculadas} {plural(resultado.tomas_recalculadas, 'toma recalculada', 'tomas recalculadas')};{' '}
      {resultado.tomas_personalizadas}{' '}
      {plural(resultado.tomas_personalizadas, 'se dejó igual por ser personalizada', 'se dejaron igual por ser personalizadas')}.
    </p>
  )
}

function VistaPrevia({ presets }) {
  return (
    <table className="w-full text-left text-sm">
      <thead className="text-xs text-texto-suave">
        <tr className="border-b border-borde">
          <th scope="col" className="py-1.5 pr-2 font-medium">
            Categoría
          </th>
          <th scope="col" className="px-2 py-1.5 text-right font-medium">
            Se apaga bajo
          </th>
          <th scope="col" className="py-1.5 pl-2 text-right font-medium">
            Vuelve desde
          </th>
        </tr>
      </thead>
      <tbody className="divide-y divide-borde tabular-nums">
        {presets.map((preset) => (
          <tr key={preset.categoria}>
            <th scope="row" className="py-1.5 pr-2 font-normal">
              <span className="flex items-center gap-1.5">
                <span aria-hidden="true" className={`size-2 shrink-0 rounded-full ${INFO_CATEGORIA[preset.categoria].punto}`} />
                {INFO_CATEGORIA[preset.categoria].nombre}
              </span>
            </th>
            {preset.corte_automatico ? (
              <>
                <td className="px-2 py-1.5 text-right">{formatoPct(preset.umbral_apagado_soc)}</td>
                <td className="py-1.5 pl-2 text-right">{formatoPct(preset.umbral_reactivacion_soc)}</td>
              </>
            ) : (
              <td colSpan={2} className="py-1.5 pl-2 text-right text-texto-suave">
                Nunca se apaga sola
              </td>
            )}
          </tr>
        ))}
      </tbody>
    </table>
  )
}

function FormularioPerfil({ domoId, categorias, onGuardado }) {
  const [perfil, setPerfil] = useState(categorias.perfil)
  const [enviando, setEnviando] = useState(false)
  const [error, setError] = useState(null)
  const [resultado, setResultado] = useState(null)

  const presets = categorias.perfiles.find((p) => p.perfil === perfil)?.categorias ?? []
  const cambia = perfil !== categorias.perfil

  async function guardar(evento) {
    evento.preventDefault()
    setResultado(null)
    setEnviando(true)
    setError(null)
    try {
      setResultado(await apiFetch(`/domos/${domoId}/configuracion`, { method: 'PUT', body: { perfil_control: perfil } }))
      onGuardado()
    } catch (e) {
      setError(e.message)
    } finally {
      setEnviando(false)
    }
  }

  return (
    <form onSubmit={guardar} onChange={() => setError(null)} noValidate className={`${CLASE_TARJETA} space-y-4 p-4 sm:p-5`}>
      <div>
        <h2 className="text-sm font-semibold text-texto">Perfil de control</h2>
        <p className="mt-1 text-sm text-texto-suave">
          Conservador apaga antes lo prescindible para que lo importante llegue al amanecer; Máximo uso aprovecha más la batería,
          pero con menos margen.
        </p>
      </div>

      <fieldset className="space-y-2">
        <legend className="sr-only">Perfil de control</legend>
        {categorias.perfiles.map(({ perfil: opcion }) => (
          <label
            key={opcion}
            className={`flex cursor-pointer items-start gap-3 rounded-lg border p-3 text-sm ${
              perfil === opcion ? 'border-marca bg-marca-suave' : 'border-borde'
            }`}
          >
            <input
              type="radio"
              name="perfil-control"
              checked={perfil === opcion}
              onChange={() => setPerfil(opcion)}
              className="mt-1 size-4 shrink-0 accent-marca"
            />
            <span className="min-w-0">
              <span className="block font-semibold text-texto">{PERFILES[opcion].nombre}</span>
              <span className="block text-texto-suave">{PERFILES[opcion].detalle}</span>
            </span>
          </label>
        ))}
      </fieldset>

      <div>
        <h3 className="text-xs font-medium text-texto-suave">
          Vista previa con la batería {NOMBRE_BATERIA[categorias.tipo_bateria]} (reserva {formatoPct(categorias.reserva_soc)})
        </h3>
        <VistaPrevia presets={presets} />
      </div>

      {cambia && (
        <p className="rounded-lg bg-ahorro-suave px-3 py-2 text-sm text-ahorro">
          Al guardar cambian los umbrales de las tomas que siguen el valor de su categoría; las personalizadas no se tocan. Si el
          nivel de la batería queda por debajo, el sistema las apagará en la siguiente evaluación.
        </p>
      )}
      {error && <AvisoError>{error}</AvisoError>}
      {resultado && <ResultadoGuardado resultado={resultado} />}

      <button type="submit" disabled={enviando} className={`${BOTON_PRINCIPAL} w-full sm:w-auto`}>
        {enviando ? 'Guardando…' : 'Guardar perfil'}
      </button>
    </form>
  )
}

function FormularioBateria({ domoId, categorias, configuracion, onGuardado }) {
  const [tipo, setTipo] = useState(configuracion.bateria?.tipo ?? categorias.tipo_bateria)
  const [capacidad, setCapacidad] = useState(configuracion.bateria?.capacidad_wh?.toString() ?? '')
  const [tipoGuardado, setTipoGuardado] = useState(categorias.tipo_bateria)
  const [enviando, setEnviando] = useState(false)
  const [error, setError] = useState(null)
  const [resultado, setResultado] = useState(null)

  const cambiaTipo = tipo !== tipoGuardado

  async function guardar(evento) {
    evento.preventDefault()
    setResultado(null)
    const capacidadWh = Number(capacidad)
    if (capacidad === '' || !Number.isFinite(capacidadWh) || capacidadWh <= 0) {
      setError('Escribe la capacidad en Wh: debe ser mayor que 0.')
      return
    }
    setEnviando(true)
    setError(null)
    try {
      const respuesta = await apiFetch(`/domos/${domoId}/configuracion`, {
        method: 'PUT',
        body: { bateria: { tipo, capacidad_wh: capacidadWh } },
      })
      setResultado(respuesta)
      setTipoGuardado(tipo)
      onGuardado()
    } catch (e) {
      setError(e.message)
    } finally {
      setEnviando(false)
    }
  }

  return (
    <form onSubmit={guardar} onChange={() => setError(null)} noValidate className={`${CLASE_TARJETA} space-y-4 p-4 sm:p-5`}>
      <div>
        <h2 className="text-sm font-semibold text-texto">Batería del domo</h2>
        <p className="mt-1 text-sm text-texto-suave">
          La <strong className="font-semibold text-texto">reserva</strong> es la parte de la batería que no se debe usar para no
          acortar su vida útil. De ella salen los umbrales de cada categoría.
        </p>
      </div>

      <fieldset className="space-y-2">
        <legend className="text-xs font-medium text-texto-suave">Tipo de batería</legend>
        {TIPOS_BATERIA.map((opcion) => (
          <label
            key={opcion.tipo}
            className={`flex cursor-pointer items-start gap-3 rounded-lg border p-3 text-sm ${
              tipo === opcion.tipo ? 'border-marca bg-marca-suave' : 'border-borde'
            }`}
          >
            <input
              type="radio"
              name="tipo-bateria"
              checked={tipo === opcion.tipo}
              onChange={() => setTipo(opcion.tipo)}
              className="mt-1 size-4 shrink-0 accent-marca"
            />
            <span className="min-w-0">
              <span className="block font-semibold text-texto">{opcion.nombre}</span>
              <span className="block text-texto-suave">{opcion.detalle}</span>
              <span className="block text-xs font-medium text-texto tabular-nums">
                Reserva: {formatoPct(categorias.reservas_por_bateria[opcion.tipo])}
              </span>
            </span>
          </label>
        ))}
      </fieldset>

      <Campo etiqueta="Capacidad (Wh)">
        <input
          type="number"
          inputMode="decimal"
          min={0}
          step="any"
          value={capacidad}
          onChange={(e) => setCapacidad(e.target.value)}
          className={CLASE_CAMPO}
        />
      </Campo>

      {cambiaTipo && (
        <p className="rounded-lg bg-ahorro-suave px-3 py-2 text-sm text-ahorro">
          Al guardar cambian los umbrales de las tomas que siguen el valor de su categoría. Si el nivel de la batería queda por
          debajo, el sistema las apagará en la siguiente evaluación.
        </p>
      )}

      {error && <AvisoError>{error}</AvisoError>}
      {resultado && <ResultadoGuardado resultado={resultado} />}

      <button type="submit" disabled={enviando} className={`${BOTON_PRINCIPAL} w-full sm:w-auto`}>
        {enviando ? 'Guardando…' : 'Guardar batería'}
      </button>
    </form>
  )
}

function ContenidoConfiguracion({ domoId }) {
  // Cambia al guardar, para traer de nuevo los presets: dependen de los dos formularios.
  const [version, setVersion] = useState(0)
  const consultar = useCallback(async () => {
    const [categorias, configuracion] = await Promise.all([
      apiFetch(`/domos/${domoId}/categorias`),
      apiFetch(`/domos/${domoId}/configuracion`),
    ])
    return { categorias, configuracion, version }
  }, [domoId, version])
  const { datos, error } = useConsulta(consultar)

  if (error && !datos) return <AvisoError>No se pudo cargar la configuración: {error}</AvisoError>
  if (!datos) return <p className="text-sm text-texto-suave">Cargando…</p>
  const alGuardar = () => setVersion((v) => v + 1)
  return (
    <div className="grid max-w-5xl grid-cols-1 items-start gap-4 lg:grid-cols-2">
      <FormularioBateria domoId={domoId} categorias={datos.categorias} configuracion={datos.configuracion} onGuardado={alGuardar} />
      <FormularioPerfil domoId={domoId} categorias={datos.categorias} onGuardado={alGuardar} />
      <RecalibracionSoc domoId={domoId} />
    </div>
  )
}

export default function Configuracion() {
  const { usuario } = useAuth()
  if (usuario?.rol !== 'dueño') return <Navigate to="/" replace />

  // El menú la muestra a quien es dueño de algún domo; en uno que solo administra no la puede cambiar.
  return (
    <PaginaDomo seccion="Configuración">
      {(domo) =>
        esDuenoDe(domo, usuario) ? (
          <ContenidoConfiguracion domoId={domo.id} />
        ) : (
          <p className={`${CLASE_TARJETA} max-w-2xl p-6 text-sm text-texto-suave`}>
            En este domo eres administrador: solo su dueño cambia la batería y el perfil de control, y recalibra el estado de carga.
          </p>
        )
      }
    </PaginaDomo>
  )
}
