// Íconos de línea, sin dependencias. Son decorativos: el texto va siempre al lado.
function Icono({ className = 'size-5', children }) {
  return (
    <svg
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.8"
      strokeLinecap="round"
      strokeLinejoin="round"
      className={className}
      aria-hidden="true"
    >
      {children}
    </svg>
  )
}

export const IconoPanel = (p) => (
  <Icono {...p}>
    <rect x="3.5" y="3.5" width="7" height="9" rx="1.5" />
    <rect x="13.5" y="3.5" width="7" height="5" rx="1.5" />
    <rect x="13.5" y="11.5" width="7" height="9" rx="1.5" />
    <rect x="3.5" y="15.5" width="7" height="5" rx="1.5" />
  </Icono>
)

export const IconoEnchufe = (p) => (
  <Icono {...p}>
    <path d="M9 3v5M15 3v5M6.5 8h11v3a5.5 5.5 0 0 1-11 0zM12 16.5V21" />
  </Icono>
)

export const IconoGrafica = (p) => (
  <Icono {...p}>
    <path d="M4 4v16h16M8 15l3.5-4 3 2.5L19 8" />
  </Icono>
)

export const IconoReloj = (p) => (
  <Icono {...p}>
    <circle cx="12" cy="12" r="8.5" />
    <path d="M12 7.5V12l3 2" />
  </Icono>
)

export const IconoCampana = (p) => (
  <Icono {...p}>
    <path d="M6 16.5V11a6 6 0 0 1 12 0v5.5l1.5 1.5h-15zM10 20.5a2 2 0 0 0 4 0" />
  </Icono>
)

export const IconoAjustes = (p) => (
  <Icono {...p}>
    <path d="M4 7h9M17 7h3M4 17h3M11 17h9" />
    <circle cx="15" cy="7" r="2" />
    <circle cx="9" cy="17" r="2" />
  </Icono>
)

export const IconoMas = (p) => (
  <Icono {...p}>
    <path d="M5 12h.01M12 12h.01M19 12h.01" strokeWidth="3" />
  </Icono>
)

export const IconoSalir = (p) => (
  <Icono {...p}>
    <path d="M10 4H6a2 2 0 0 0-2 2v12a2 2 0 0 0 2 2h4M15 8l4 4-4 4M19 12H9" />
  </Icono>
)

export const IconoSol = (p) => (
  <Icono {...p}>
    <circle cx="12" cy="12" r="4" />
    <path d="M12 3v2M12 19v2M3 12h2M19 12h2M5.6 5.6 7 7M17 17l1.4 1.4M5.6 18.4 7 17M17 7l1.4-1.4" />
  </Icono>
)

export const IconoBateria = (p) => (
  <Icono {...p}>
    <rect x="3" y="7.5" width="16" height="9" rx="2" />
    <path d="M21.5 11v2M7 12h3" />
  </Icono>
)

export const IconoRayo = (p) => (
  <Icono {...p}>
    <path d="M13 3 5.5 13.5H11L10 21l8-11h-5.5z" />
  </Icono>
)

export const IconoPaneles = (p) => (
  <Icono {...p}>
    <path d="M5 5h14l2 11H3zM9.5 5l-1 11M14.5 5l1 11M4 10.5h16M12 16v4M8.5 20h7" />
  </Icono>
)

export const IconoAyuda = (p) => (
  <Icono {...p}>
    <circle cx="12" cy="12" r="8.5" />
    <path d="M9.7 9.5a2.4 2.4 0 1 1 3.6 2.1c-.8.5-1.3 1-1.3 2M12 16.5h.01" />
  </Icono>
)

export const IconoDocumento = (p) => (
  <Icono {...p}>
    <path d="M6.5 3.5h7l4 4v13h-11zM13.5 3.5v4h4M9.5 17v-3M12 17v-5.5M14.5 17v-2" />
  </Icono>
)

export const IconoCuenta = (p) => (
  <Icono {...p}>
    <circle cx="12" cy="8" r="3.8" />
    <path d="M4.5 20.5c.5-4 3.5-6 7.5-6s7 2 7.5 6" />
  </Icono>
)

export const IconoPersonas = (p) => (
  <Icono {...p}>
    <circle cx="9" cy="8" r="3.5" />
    <path d="M3 20c.4-3.6 2.8-5.5 6-5.5s5.6 1.9 6 5.5M16 4.8a3.5 3.5 0 0 1 0 6.4M18 14.8c1.800.7 2.800 2.400 3 5.200" />
  </Icono>
)

export const IconoReserva = (p) => (
  <Icono {...p}>
    <path d="M12 3.5 5 6v5.5c0 4.2 2.9 7.4 7 9 4.1-1.6 7-4.8 7-9V6z" />
  </Icono>
)

// Clima de referencia (TarjetaClima).
export const IconoLuna = (p) => (
  <Icono {...p}>
    <path d="M20 14.5A8 8 0 0 1 9.5 4a8 8 0 1 0 10.5 10.5z" />
  </Icono>
)

export const IconoNube = (p) => (
  <Icono {...p}>
    <path d="M7 18h10a4 4 0 0 0 .6-7.96A6 6 0 0 0 6.1 11 3.5 3.5 0 0 0 7 18z" />
  </Icono>
)

export const IconoSolNube = (p) => (
  <Icono {...p}>
    <path d="M8 3v1.5M3.5 8H5M4.8 4.8l1 1M11.2 4.8l-1 1" />
    <path d="M5.6 10.4A3 3 0 0 1 10.9 7" />
    <path d="M9 20h8a3.5 3.5 0 0 0 .5-6.96A5 5 0 0 0 8.2 14 3 3 0 0 0 9 20z" />
  </Icono>
)

export const IconoLunaNube = (p) => (
  <Icono {...p}>
    <path d="M11.5 6.5A4.5 4.5 0 0 1 5 11a4.5 4.5 0 1 0 6.5-4.5z" />
    <path d="M9 20h8a3.5 3.5 0 0 0 .5-6.96A5 5 0 0 0 8.2 14 3 3 0 0 0 9 20z" />
  </Icono>
)

export const IconoNiebla = (p) => (
  <Icono {...p}>
    <path d="M4 9h16M3 13h18M5 17h14" />
  </Icono>
)

export const IconoLluvia = (p) => (
  <Icono {...p}>
    <path d="M7 15h10a4 4 0 0 0 .6-7.96A6 6 0 0 0 6.1 8 3.5 3.5 0 0 0 7 15z" />
    <path d="M8 18l-1 2.5M12 18l-1 2.5M16 18l-1 2.5" />
  </Icono>
)

export const IconoNieve = (p) => (
  <Icono {...p}>
    <path d="M7 15h10a4 4 0 0 0 .6-7.96A6 6 0 0 0 6.1 8 3.5 3.5 0 0 0 7 15z" />
    <path d="M8 19h.01M12 20h.01M16 19h.01" />
  </Icono>
)

export const IconoTormenta = (p) => (
  <Icono {...p}>
    <path d="M7 15h10a4 4 0 0 0 .6-7.96A6 6 0 0 0 6.1 8 3.5 3.5 0 0 0 7 15z" />
    <path d="m12.5 15-2 3.5h3l-2 3" />
  </Icono>
)
