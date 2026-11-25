# Usuarios y permisos

## 1. Usuarios del sistema

APP-GLAMPING contempla tres tipos de actores:

1. Dueño del domo
2. Administrador del domo
3. Cliente que alquila el domo

Los tres actores no tendrán el mismo nivel de acceso.

---

## 2. Dueño del domo

### Descripción

Es el usuario con mayor nivel de acceso dentro de la aplicación.

El dueño puede administrar y consultar los diferentes domos que tenga
asociados.

### Permisos

- Iniciar sesión.
- Consultar sus domos.
- Seleccionar el domo que desea visualizar.
- Consultar variables energéticas.
- Consultar información histórica.
- Visualizar estados de dispositivos.
- Controlar cargas autorizadas.
- Controlar iluminación.
- Controlar tomas inteligentes VTA+.
- Consultar información energética del sistema.
- Consultar y gestionar alertas del sistema.
- Crear, editar, activar o desactivar cuentas de administrador.
- Configurar parámetros del sistema (límites de alerta, prioridades de carga).

---

## 3. Administrador del domo

### Descripción

Es el usuario encargado de las funciones operativas del domo o de los
domos que tenga asignados.

### Permisos

- Iniciar sesión.
- Consultar información del domo asignado.
- Visualizar variables energéticas.
- Consultar información histórica.
- Visualizar estados de dispositivos.
- Controlar cargas autorizadas.
- Controlar iluminación.
- Controlar tomas inteligentes VTA+.
- Consultar y atender alertas de los domos que tenga asignados.

### Restricciones

El administrador no puede seleccionar libremente cualquier domo
perteneciente al dueño.

El administrador no puede crear, editar ni desactivar cuentas de otros
administradores, ni modificar la configuración general del sistema.

Su acceso estará limitado a los domos que tenga asignados.

---

## 4. Cliente

### Descripción

El cliente es la persona que alquila el domo.

El cliente no utilizará la aplicación y no tendrá una cuenta dentro
del sistema.

### Interacción

El cliente podrá interactuar directamente con los dispositivos disponibles
dentro del domo, principalmente:

- Iluminación.
- Tomas eléctricas.

### Restricciones

- No tendrá acceso a la aplicación.
- No tendrá usuario ni contraseña.
- No podrá consultar variables energéticas.
- No podrá consultar información histórica.
- No podrá seleccionar domos.
- No tendrá acceso al sistema de administración.

---

## 5. Matriz de permisos

| Función | Dueño | Administrador | Cliente |
|---|---|---|---|
| Iniciar sesión | Sí | Sí | No |
| Seleccionar domo | Sí | No | No |
| Ver variables energéticas | Sí | Sí | No |
| Ver información histórica | Sí | Sí | No |
| Ver estado de dispositivos | Sí | Sí | No |
| Controlar iluminación | Sí | Sí | No* |
| Controlar tomas VTA+ | Sí | Sí | No* |
| Gestionar sistema | Sí | Limitado | No |

*El cliente interactúa directamente con los dispositivos físicos dentro
del domo, no mediante la aplicación.

---

## 6. Registro y creación de cuentas

El registro de usuarios en APP-GLAMPING no es un flujo abierto al público.

### Cuenta del dueño

Para el alcance actual del proyecto (un domo experimental, con un único
dueño conocido de antemano), la cuenta del dueño se crea mediante
aprovisionamiento manual inicial:

1. El equipo de desarrollo crea el usuario directamente en Supabase Auth
   (correo y contraseña) durante el despliegue/configuración del entorno.
2. Se inserta la fila correspondiente en la tabla de usuarios con
   `rol = 'dueño'`.
3. La aplicación no expone ningún formulario público para crear esta
   cuenta.

Si en una versión futura la plataforma admite múltiples dueños
independientes (varios glampings usando el sistema), este mecanismo debe
evolucionar hacia un flujo de "primer arranque" (bootstrap) dentro de la
propia aplicación: al detectar la ausencia de un dueño para ese contexto,
se habilitaría su creación una única vez, bloqueándose después de forma
permanente.

### Cuentas de administrador

- Las cuentas de administrador solo pueden ser creadas, editadas, activadas
  o desactivadas por el dueño desde la aplicación.

### Cliente

- El cliente nunca genera una cuenta, ya que no utiliza la aplicación.

Esta sección reemplaza cualquier interpretación de "registro" como
autoregistro libre de nuevos usuarios.