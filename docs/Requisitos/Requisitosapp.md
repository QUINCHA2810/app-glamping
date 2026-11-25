# Requisitos de la aplicación

## 1. Descripción

La aplicación APP-GLAMPING es una plataforma de gestión y monitoreo energético
para un sistema de domo experimental orientado al sector turístico tipo glamping.

La aplicación permitirá visualizar variables energéticas en tiempo real,
consultar información histórica y controlar determinadas cargas eléctricas
del domo.

El sistema estará conectado a Internet y utilizará un ESP32 como nodo de
adquisición y control.

## 2. Alcance de la aplicación

La aplicación estará orientada principalmente al dueño y al administrador
del domo.

El cliente que alquila el domo no tendrá acceso a la aplicación. Su interacción
con el sistema se realizará directamente mediante los dispositivos instalados
dentro del domo.

## 3. Requisitos funcionales

### RF-01 — Inicio de sesión

La aplicación debe permitir que los usuarios autorizados ingresen mediante
correo electrónico y contraseña.

### RF-02 — Creación de cuentas

El registro no es un flujo abierto al público. El dueño debe poder crear,
editar, activar o desactivar cuentas de administrador desde la aplicación.

La cuenta del dueño no se crea desde la aplicación: se aprovisiona
manualmente en Supabase durante el despliegue del sistema (ver
`Usuariosypermisos.md`).

### RF-03 — Gestión de domos

El dueño debe poder consultar los domos asociados a su cuenta y seleccionar
el domo que desea visualizar.

### RF-04 — Acceso del administrador

El administrador debe poder acceder a las funciones de monitoreo y control
de los domos que tenga asignados.

El administrador no debe poder seleccionar libremente cualquier domo
perteneciente al dueño.

### RF-05 — Visualización energética

La aplicación debe permitir visualizar las principales variables energéticas
del sistema.

### RF-06 — Monitoreo en tiempo real

La aplicación debe mostrar información actualizada de las variables
energéticas y del estado de los dispositivos.

### RF-07 — Consulta histórica

La aplicación debe permitir consultar registros históricos de las variables
energéticas almacenadas en el sistema.

### RF-08 — Control de cargas

La aplicación debe permitir activar y desactivar las cargas que hayan sido
definidas como controlables.

### RF-09 — Control de iluminación

La aplicación debe permitir controlar las luminarias inteligentes instaladas
en el domo.

### RF-10 — Control de tomas

La aplicación debe permitir controlar las tomas inteligentes VTA+ instaladas
en el domo.

### RF-11 — Estado de dispositivos

La aplicación debe mostrar el estado actual de los dispositivos controlables.

### RF-12 — Comunicación con el sistema IoT

La aplicación debe intercambiar información con el sistema IoT mediante
la arquitectura de comunicación definida para el proyecto.

### RF-13 — Registro de mediciones

El sistema debe almacenar las mediciones energéticas necesarias para
realizar consultas y análisis históricos.

### RF-14 — Gestión de permisos

El sistema debe restringir las funciones disponibles de acuerdo con el
tipo de usuario.

### RF-15 — Alertas del sistema

La aplicación debe generar y mostrar alertas cuando se detecten condiciones
anormales en las variables energéticas del domo (por ejemplo, batería baja
o consumo elevado).

### RF-16 — Historial de acciones

El sistema debe registrar las acciones relevantes ejecutadas sobre los
dispositivos, indicando qué usuario las realizó y en qué momento.

### RF-17 — Estado de comunicación

La aplicación debe permitir consultar el estado de conexión de los
dispositivos y la última comunicación registrada.

### RF-18 — Gestión de administradores

El dueño debe poder crear, editar, activar o desactivar cuentas de
administrador, y definir a qué domos tiene acceso cada uno.

### RF-19 — Configuración del sistema

El dueño debe poder ajustar parámetros de funcionamiento del sistema, como
límites de alerta y prioridades de carga.

## 4. Requisitos no funcionales

### RNF-01 — Seguridad

La aplicación debe proteger el acceso mediante autenticación y control
de permisos.

### RNF-02 — Disponibilidad

La aplicación debe funcionar mediante conexión a Internet.

### RNF-03 — Usabilidad

La interfaz debe ser clara, intuitiva y adecuada para su utilización desde
dispositivos móviles.

### RNF-04 — Escalabilidad

La arquitectura debe permitir incorporar posteriormente múltiples domos
sin modificar completamente la estructura del sistema.

### RNF-05 — Mantenibilidad

El código debe estar organizado para facilitar su mantenimiento y
ampliación durante el desarrollo del proyecto.

## 5. Consideraciones

La potencia definitiva del sistema fotovoltaico aún no está establecida.
Como referencia preliminar se considera un rango aproximado de 800 a 1000 Wp,
pero el valor definitivo dependerá del dimensionamiento energético. Ningún valor
específico (800 Wp, 1000 Wp u otro) debe tratarse como capacidad definitiva hasta
que el equipo lo determine mediante los cálculos correspondientes.

Las Zonas No Interconectadas no hacen parte del alcance actual del proyecto.
Se consideran únicamente como una posible aplicación futura y no deben utilizarse
como contexto principal para decisiones de diseño, desarrollo o implementación.

Las cortinas automáticas no hacen parte del sistema. No deben incluirse en la
planificación de requisitos futuros, salvo que aparezcan en documentos históricos
que deban conservarse como referencia.