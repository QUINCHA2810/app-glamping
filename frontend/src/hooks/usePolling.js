import { useCallback, useEffect, useRef, useState } from 'react'

// Ejecuta `fn` (async) de inmediato y luego cada `intervaloMs`.
// - Se pausa mientras la pestaña está oculta y se reanuda (con una
//   consulta inmediata) al volver a ella.
// - Si cambia `fn` (p. ej. un useCallback que depende del domo), se
//   reinicia desde cero: datos en null y cargando=true.
// - En los refrescos posteriores se conservan los datos anteriores hasta
//   que llegan los nuevos, para que la vista no parpadee.
// - `refrescar()` fuerza una consulta inmediata (p. ej. tras un PATCH).
export function usePolling(fn, intervaloMs) {
  // `fn` se guarda junto al resultado: si no coincide con la actual, el
  // resultado es de una consulta anterior (otro domo) y se descarta.
  const [estado, setEstado] = useState({ fn: null, datos: null, error: null })
  const consultarRef = useRef(null)

  useEffect(() => {
    let activo = true
    let temporizador = null
    let enCurso = false
    let repetir = false

    async function consultar() {
      // Si ya hay una consulta en vuelo, puede traer datos anteriores a un
      // cambio recién hecho: se repite apenas termine.
      if (enCurso) {
        repetir = true
        return
      }
      enCurso = true
      try {
        const datos = await fn()
        if (activo) setEstado({ fn, datos, error: null })
      } catch (e) {
        // Se conserva el último dato bueno y se informa el error.
        if (activo) setEstado((prev) => ({ fn, datos: prev.fn === fn ? prev.datos : null, error: e.message }))
      } finally {
        enCurso = false
        if (repetir && activo) {
          repetir = false
          consultar()
        } else {
          programar()
        }
      }
    }

    function programar() {
      clearTimeout(temporizador)
      if (activo && document.visibilityState === 'visible') {
        temporizador = setTimeout(consultar, intervaloMs)
      }
    }

    function alCambiarVisibilidad() {
      if (document.visibilityState === 'visible') consultar()
      else clearTimeout(temporizador)
    }

    consultarRef.current = consultar
    consultar()
    document.addEventListener('visibilitychange', alCambiarVisibilidad)

    return () => {
      activo = false
      consultarRef.current = null
      clearTimeout(temporizador)
      document.removeEventListener('visibilitychange', alCambiarVisibilidad)
    }
  }, [fn, intervaloMs])

  const refrescar = useCallback(() => consultarRef.current?.(), [])

  const vigente = estado.fn === fn
  return {
    datos: vigente ? estado.datos : null,
    error: vigente ? estado.error : null,
    cargando: !vigente,
    refrescar,
  }
}
