// Marca de Vatia: un domo geodésico con el sol detrás. El mismo dibujo está en
// public/favicon.svg; si cambia uno, hay que cambiar el otro.
export default function Logo({ className = 'size-8' }) {
  return (
    <svg viewBox="0 0 64 64" fill="none" className={className} aria-hidden="true">
      <rect width="64" height="64" rx="15" className="fill-marca-fuerte" />
      <circle cx="45" cy="20" r="8" className="fill-sol" />
      <path d="M45 6v4M59 20h-4M55 10l-3 3M35 10l3 3" className="stroke-sol" strokeWidth="3" strokeLinecap="round" />
      <path d="M10 52a22 22 0 0 1 44 0z" fill="#fff" />
      <path
        d="M10 52 23 38l9 14 9-14 13 14M23 38h18M23 38l9-8 9 8"
        className="stroke-marca-fuerte"
        strokeWidth="2.5"
        strokeLinejoin="round"
      />
    </svg>
  )
}
