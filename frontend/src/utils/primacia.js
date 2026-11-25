// Primacía de la decisión humana (ronda DB-4). Si una persona enciende una toma, el
// sistema (R-13) la respeta hasta que la batería llegue a su umbral de reactivación,
// salvo que baje de la reserva. Quién la tiene lo calcula el backend
// (DispositivoOut.primacia_humana_hasta_soc); aquí solo se arma el texto.
import { esNumero, formatoPct } from './formato.js'

// Texto de la tarjeta de una toma con primacía vigente, o null si no tiene.
export function textoPrimaciaHumana(hastaSoc, reserva) {
  if (!esNumero(hastaSoc)) return null
  const base = `Encendida a mano: el sistema la respeta hasta que la batería llegue a ${formatoPct(hastaSoc)}`
  return esNumero(reserva) ? `${base} (la corta si baja de la reserva, ${formatoPct(reserva)}).` : `${base}.`
}

// Qué pasará si alguien enciende ahora una toma cuyo umbral de apagado ya cruzó la
// batería (el aviso antes de encender).
export function textoAlEncenderBajoUmbral(soc, umbralReactivacion, reserva) {
  if (esNumero(soc) && esNumero(reserva) && soc < reserva) {
    return `La batería está bajo la reserva (${formatoPct(reserva)}): aunque la enciendas, el sistema la volverá a apagar en la siguiente evaluación para proteger la batería.`
  }
  if (!esNumero(umbralReactivacion)) {
    return 'Si la enciendes ahora, el sistema la respetará mientras la batería no baje de la reserva.'
  }
  const reservaTexto = esNumero(reserva) ? `, salvo que baje de la reserva (${formatoPct(reserva)})` : ''
  return `Si la enciendes ahora, el sistema la respetará hasta que la batería llegue a ${formatoPct(umbralReactivacion)}${reservaTexto}.`
}
