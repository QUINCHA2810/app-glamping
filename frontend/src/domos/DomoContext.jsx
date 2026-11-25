import { createContext, useContext, useEffect, useState } from 'react'
import { apiFetch, DOMO_KEY } from '../api/client.js'

const DomoContext = createContext(null)

// Domos accesibles por el usuario (GET /domos ya filtra por rol) y el domo
// seleccionado, que se recuerda en localStorage entre visitas.
export function DomoProvider({ children }) {
  const [domos, setDomos] = useState([])
  const [domoId, setDomoId] = useState(() => Number(localStorage.getItem(DOMO_KEY)) || null)
  const [cargando, setCargando] = useState(true)
  const [error, setError] = useState(null)

  useEffect(() => {
    apiFetch('/domos')
      .then((lista) => {
        setDomos(lista)
        // Si el domo guardado ya no es accesible, se elige el primero.
        setDomoId((actual) => (lista.some((d) => d.id === actual) ? actual : (lista[0]?.id ?? null)))
      })
      .catch((e) => setError(e.message))
      .finally(() => setCargando(false))
  }, [])

  useEffect(() => {
    if (domoId) localStorage.setItem(DOMO_KEY, String(domoId))
  }, [domoId])

  const domo = domos.find((d) => d.id === domoId) ?? null

  return (
    <DomoContext.Provider value={{ domos, domo, domoId, setDomoId, cargando, error }}>
      {children}
    </DomoContext.Provider>
  )
}

// oxlint-disable-next-line react/only-export-components
export function useDomo() {
  return useContext(DomoContext)
}
