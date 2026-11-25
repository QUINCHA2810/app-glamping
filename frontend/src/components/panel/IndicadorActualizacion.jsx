import { useEffect, useState } from 'react'
import { aFecha } from '../../utils/formato.js'

const LIMITE_RECIENTE_S = 60

function textoHace(segundos) {
  if (segundos < 60) return `hace ${segundos} s`
  const minutos = Math.floor(segundos / 60)
  if (minutos < 60) return `hace ${minutos} min`
  const horas = Math.floor(minutos / 60)
  if (horas < 24) return `hace ${horas} h`
  return `hace ${Math.floor(horas / 24)} d`
}

// "Actualizado hace X s" a partir de fecha_actualizacion de estado-general;
// se recalcula cada segundo y avisa si los datos tienen más de 60 s.
export default function IndicadorActualizacion({ fechaActualizacion }) {
  const [ahora, setAhora] = useState(() => Date.now())

  useEffect(() => {
    const id = setInterval(() => setAhora(Date.now()), 1000)
    return () => clearInterval(id)
  }, [])

  const fecha = aFecha(fechaActualizacion)

  if (!fecha) {
    return (
      <p className="rounded-lg border border-borde bg-superficie-suave px-3 py-1.5 text-sm text-texto-suave">
        El domo todavía no ha enviado lecturas.
      </p>
    )
  }

  const segundos = Math.max(0, Math.round((ahora - fecha.getTime()) / 1000))
  const reciente = segundos <= LIMITE_RECIENTE_S

  return (
    <div className="flex flex-wrap items-center gap-2 text-sm">
      <span className="flex items-center gap-1.5 text-texto-suave">
        <span className={`size-2 rounded-full ${reciente ? 'bg-normal motion-safe:animate-pulse' : 'bg-borde-fuerte'}`} />
        Actualizado {textoHace(segundos)}
      </span>
      {!reciente && (
        <span role="status" className="rounded-lg border border-ahorro/30 bg-ahorro-suave px-2.5 py-1 text-ahorro">
          Sin datos recientes del domo
        </span>
      )}
    </div>
  )
}
