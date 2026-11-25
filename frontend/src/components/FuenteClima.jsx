import { ATRIBUCION, AVISO_ESTIMACION, URL_OPEN_METEO } from '../utils/clima.js'

// Aviso y atribución que acompañan siempre al clima de referencia (Open-Meteo, CC BY 4.0).
export default function FuenteClima({ className = '' }) {
  return (
    <p className={`text-xs text-texto-suave ${className}`}>
      {AVISO_ESTIMACION}{' '}
      <span className="whitespace-nowrap">
        {ATRIBUCION}{' '}
        <a href={URL_OPEN_METEO} target="_blank" rel="noreferrer" className="font-medium text-marca-fuerte underline">
          Open-Meteo.com
        </a>
      </span>
    </p>
  )
}
