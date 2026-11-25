// Rol del usuario EN UN DOMO (modelo v2: el rol es por domo, sale de `asignaciones`).
// El dueño de un domo es el de su asignación 'dueno', que GET /domos devuelve como
// `propietario_id`. `usuario.rol` es el de la cuenta: 'dueño' si es dueña de algún
// domo; sirve para el menú y para Administradores, no para decidir en un domo.
export function esDuenoDe(domo, usuario) {
  return Boolean(domo && usuario) && domo.propietario_id != null && domo.propietario_id === usuario.id
}

// Los domos donde el usuario es dueño (a los que puede asignar administradores).
export function domosDelDueno(domos, usuario) {
  return (domos ?? []).filter((domo) => esDuenoDe(domo, usuario))
}
