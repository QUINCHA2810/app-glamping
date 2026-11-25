# Cambio definitivo a v2 (lunes 2026-11-16) y cómo volver a v1

Para tener v2 lista para las mediciones del martes 2026-11-17, con los datos de v1 hasta el
momento del cambio. Todo se corre desde `backend/` con el `.venv` activado. Ningún comando de
esta guía imprime contraseñas ni la cadena de conexión.

Tiempo estimado: 30–45 minutos. Mientras dure, la app y el listener están detenidos: la
telemetría que el ESP32 publique en ese rato no se guarda en ninguna base.

## 0. Antes de empezar

- La rama `feature/modelo-simplificado` con la ronda DB-3 en git (`git status` limpio).
- `backend/.env` con las dos bases:
  - `DATABASE_URL`: la v1 (de aquí sale el respaldo).
  - `DATABASE_URL_V2`: la v2, por el pooler de **sesión**, puerto **5432** (el 6543 resultó unas
    3 veces más lento).
- Una carpeta para el respaldo **fuera de OneDrive** (el respaldo lleva los hashes de las
  contraseñas), por ejemplo `C:\respaldos_vatia`.
- **Lo que se pierde de la v2 de prueba.** El paso 3 borra la base v2 y la vuelve a llenar con la
  copia de v1. Todo lo que se creó solo en v2 desde el 2026-11-14 desaparece. Por ejemplo, la toma
  `toma2` y cualquier cuenta o alerta creada en esas pruebas. Anótalo para volver a crearlo en el
  paso 7. Si quieres conservar una copia de esa v2 de prueba antes de borrarla:
  `python tools/respaldo_bd.py --origen v2 --destino C:\respaldos_vatia` (la copia se niega a usar
  un respaldo v2 como origen, así que no hay riesgo de confundirlos).

## 1. Detener todo lo que escribe

1. **Listener** (`python mqtt_listener.py`): `Ctrl+C`. Espera a que diga que terminó; vacía la cola
   en hasta 30 s.
2. **Simulador**, si estaba corriendo en algún domo: `Ctrl+C`.
3. **Backend** (`uvicorn`): `Ctrl+C`. Así nadie cambia la v1 desde la app durante el respaldo.
4. El ESP32 puede quedar encendido: sin listener, nada se guarda.

## 2. Respaldo de v1

```
python tools/respaldo_bd.py --origen v1 --destino C:\respaldos_vatia
```

Crea `C:\respaldos_vatia\respaldo_AAAAMMDD_HHMM\` con un JSON por tabla y un `manifiesto.json`
con `"origen": "v1"`. Anota el nombre de la carpeta; abajo es `<carpeta>`.

## 3. Rehacer la base v2 vacía en la última revisión

```
alembic -c alembic_v2.ini downgrade base
alembic -c alembic_v2.ini upgrade head
alembic -c alembic_v2.ini current
```

`current` debe decir **`4e8b2c6d9f17 (head)`**: las 7 tablas, las columnas de voltaje FV y
temperatura, el esquema `bi` y el rol `lector_bi` (sin login ni contraseña). El `downgrade base`
borra también el `lector_bi` de v2: su contraseña se pone de nuevo en el paso 5.

Si `upgrade head` dice "Esta base tiene tablas del modelo v1", `DATABASE_URL_V2` apunta a la base
equivocada: no se creó nada. Corrige el `.env` y repite.

## 4. Ensayo y copia

```
python tools/copiar_a_v2.py <carpeta>
```

Es solo un ensayo: no escribe nada. Revisa:

- **"Energía total por domo"**: para cada domo, el valor de v1 y el de v2 deben ser iguales (sin
  "diferencia"). Si hay diferencia, los casos `MEDIDA_FUERA_DE_RANGO` o `LECTURA_SIN_FECHA` dicen
  qué filas.
- **"Datos de v1 que no pasan a v2"**: no deben aparecer ni `lecturas_fotovoltaicas.voltaje` ni
  `lecturas_bateria.temperatura` (desde DB-2 se copian).
- **"Administradores que quedan sin ningún domo"**: esas cuentas no las verá ningún dueño; si
  alguna se necesita para el martes, después de la copia se le asigna un domo directamente en la
  base o se crea de nuevo desde Administradores.
- **"Casos que requieren decisión"**: la línea final dice los códigos que hay que aceptar.

Si todo está bien, aplica con los códigos que pidió el ensayo (separados por comas):

```
python tools/copiar_a_v2.py <carpeta> --aplicar --aceptar CODIGO1,CODIGO2
```

Escribe todo en una sola transacción: o queda todo o no queda nada. Debe terminar con
"Copia aplicada.".

## 5. Contraseña de `lector_bi` (Power BI)

Solo si vas a usar Power BI con v2 el martes. Sigue `docs/PowerBI.md`, 7.3, en el **SQL Editor del
proyecto v2**:

1. En tu PC: `python -c "import secrets; print(secrets.token_urlsafe(32))"` y guárdala en tu gestor
   de contraseñas (nunca en el repositorio ni en `.env`).
2. En un snippet nuevo: `ALTER ROLE lector_bi WITH LOGIN PASSWORD '<CONTRASEÑA_GENERADA>';`
3. **Borra el snippet.**
4. Comprueba: `python tools/probar_lector_bi.py` (pide la referencia del proyecto v2, el host del
   Session pooler y la contraseña, que no se muestra). Debe terminar en "TODO CORRECTO".
5. En Power BI Desktop: **Transformar datos → Configuración de origen de datos → Cambiar origen**
   al host del pooler de v2, y las credenciales del `lector_bi` de v2 (`docs/PowerBI.md`, 7.0).

## 6. Arrancar

En terminales separadas:

```
uvicorn main:app --reload          # backend/
python mqtt_listener.py            # backend/
npm run dev                        # frontend/
```

- Solo un listener: el de v2. Nunca uno de v1 al mismo tiempo.
- El simulador **no** se corre sobre el domo del ESP32 (se niega si el domo tuvo telemetría en los
  últimos 10 minutos).

## 7. Qué revisar

1. **Listener:** cada mensaje deja `Guardado en mediciones: tema=… bloque=…` y
   `Procesado … en ~0,5 s`, con "en espera" en 0–2.
   - Con la ESP32 sin sensores, el SoC llega en `null` y aparece **una vez**:
     `R-13 automático (domo 1) no decide: … (no se repite mientras siga igual)`. Es lo esperado: R-13
     no actúa con datos viejos.
2. **Login** como dueño: el menú muestra Configuración y Administradores.
3. **Panel:** con lecturas de menos de 2 minutos, los valores. Sin ellas, "Sin datos" con "Sin dato
   de los últimos 2 minutos" (no el 51,1 % ni los 80 W de días atrás). "Actualizado hace …" dice la
   fecha del último dato.
4. **Dispositivos:** vuelve a crear `toma2` si se perdió en el paso 3 (con su `id_fisico` y la
   categoría que corresponda; el asistente preselecciona Confort). Enciéndela y apágala desde la
   app y comprueba la toma real. Encendida a mano, ya no se apaga sola a los 30 s mientras no haya
   SoC actual. En el detalle, "Umbrales calculados" en solo lectura (ya no hay modo avanzado).
5. **Configuración:** cambiar el perfil cambia los umbrales que muestra Dispositivos.
6. **Historial, Alertas, Históricos, Reportes y CSV:** listan los datos copiados de v1.
7. **Administradores → Crear administrador:** elige el domo y crea la cuenta. Ahora debe funcionar.
8. **Mi cuenta** y el selector de domo.
9. **Panel → Evaluar ahora** sin SoC actual: responde con el motivo ("No hay estado de carga (SoC)
   de los últimos 2 minutos…"), sin apagar nada.

## 8. Mantenimiento

- **Keepalive de GitHub:** crea el secreto **`DATABASE_URL_V2`** en GitHub (repositorio →
  Settings → Secrets and variables → Actions → New repository secret) con la misma cadena del
  `.env`. El workflow `supabase-keepalive.yml` hace ping a `DATABASE_URL` (v1) y a
  `DATABASE_URL_V2`. Ojo: GitHub solo corre los workflows **programados** desde la rama por
  defecto (`main`). Hasta que la rama llegue a `main`, se puede correr a mano desde la pestaña
  Actions (Run workflow → rama `feature/modelo-simplificado`).

---

## Volver a v1 si algo falla

La v1 sigue intacta: el cambio no la modifica (el respaldo solo lee). Lo que se haya guardado en v2
después del cambio **no** pasa a v1 (no hay copia de v2 a v1); queda en v2 para consultarlo.

1. Detén el listener, el backend y el frontend (`Ctrl+C`).
2. Cambia el código a la versión v1, desde la raíz del repositorio:

   ```
   git switch --detach v1-modelo-actual
   ```

   (Para trabajar sobre ella: `git switch -c vuelta-v1 v1-modelo-actual`.) La etiqueta
   `v1-modelo-actual` tiene el backend, el listener, el frontend y el firmware de v1; `main` es
   más antigua, no la uses para esto.
3. **`.env`: no hay que cambiar nada.** El código v1 lee `DATABASE_URL`, que sigue apuntando a la v1.
   `DATABASE_URL_V2` se ignora. Las dependencias son las mismas (`requirements.txt` y
   `package.json` no cambiaron).
4. Arranca igual que en el paso 6 (`uvicorn main:app --reload`, `python mqtt_listener.py`,
   `npm run dev`). El listener de v1 guarda en `lecturas_*`.
5. Power BI: vuelve a cambiar el origen al pooler de v1 con el `lector_bi` de v1.
6. Para volver a v2 después: `git switch feature/modelo-simplificado` y repetir desde el paso 1
   (un respaldo nuevo de v1 incluye lo que se guardó en v1 mientras tanto).

En v1 queda el comportamiento anterior a DB-3. Mientras la ESP32 publique el SoC en `null`, la v1
no evalúa R-13 (su última lectura de batería no trae SoC). Pero si el ESP32 deja de publicar del
todo, la v1 sigue usando el último SoC, sin importar su antigüedad.
