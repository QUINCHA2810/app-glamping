import { useEffect, useState } from 'react'

// Ejecuta `fn` (async) una vez, y de nuevo cada vez que cambia (p. ej. un
// useCallback que depende del rango elegido). Sin polling.
// Mientras llega la respuesta nueva se siguen entregando los datos
// anteriores (con cargando=true), para no dejar la página en blanco.
export function useConsulta(fn) {
  const [estado, setEstado] = useState({ fn: null, datos: null, error: null })

  useEffect(() => {
    let activo = true
    fn()
      .then((datos) => {
        if (activo) setEstado({ fn, datos, error: null })
      })
      .catch((e) => {
        if (activo) setEstado((prev) => ({ fn, datos: prev.datos, error: e.message }))
      })
    return () => {
      activo = false
    }
  }, [fn])

  return { datos: estado.datos, error: estado.fn === fn ? estado.error : null, cargando: estado.fn !== fn }
}
