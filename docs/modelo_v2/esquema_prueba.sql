-- Modelo simplificado sin ciclos (prueba). 7 tablas + 2 versiones de mediciones.
DROP SCHEMA IF EXISTS prueba CASCADE;
CREATE SCHEMA prueba;
SET search_path = prueba;

CREATE TABLE usuarios (
  id                      BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  nombre                  VARCHAR(100) NOT NULL,
  correo                  VARCHAR(150) NOT NULL UNIQUE,
  contrasena_hash         VARCHAR(255) NOT NULL,
  activo                  BOOLEAN NOT NULL DEFAULT true,
  desactivado_en          TIMESTAMPTZ,
  debe_cambiar_contrasena BOOLEAN NOT NULL DEFAULT false,
  version_sesion          INT NOT NULL DEFAULT 0,
  creado_en               TIMESTAMPTZ NOT NULL DEFAULT now(),
  CHECK (activo OR desactivado_en IS NOT NULL)
);

CREATE TABLE domos (
  id                   BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  nombre               VARCHAR(100) NOT NULL,
  ubicacion            VARCHAR(255),
  es_demostracion      BOOLEAN NOT NULL DEFAULT false,
  bateria_tipo         VARCHAR(10) NOT NULL CHECK (bateria_tipo IN ('agm','lifepo4')),
  bateria_capacidad_wh NUMERIC(8,1) NOT NULL CHECK (bateria_capacidad_wh > 0),
  reserva_pct          NUMERIC(4,1) NOT NULL CHECK (reserva_pct BETWEEN 0 AND 100),
  perfil               VARCHAR(15) NOT NULL CHECK (perfil IN ('conservador','equilibrado','maximo_uso')),
  creado_en            TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE asignaciones (
  id         BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  usuario_id BIGINT NOT NULL REFERENCES usuarios(id) ON DELETE RESTRICT,
  domo_id    BIGINT NOT NULL REFERENCES domos(id) ON DELETE CASCADE,
  rol        VARCHAR(15) NOT NULL CHECK (rol IN ('dueno','administrador')),
  UNIQUE (usuario_id, domo_id)
);
CREATE UNIQUE INDEX un_dueno_por_domo ON asignaciones (domo_id) WHERE rol = 'dueno';
CREATE INDEX ix_asig_domo ON asignaciones (domo_id);

CREATE TABLE dispositivos (
  id                  BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  domo_id             BIGINT NOT NULL REFERENCES domos(id) ON DELETE RESTRICT,
  nombre              VARCHAR(100) NOT NULL,
  tipo                VARCHAR(10) NOT NULL CHECK (tipo IN ('toma','bombillo')),
  categoria           VARCHAR(15) NOT NULL CHECK (categoria IN ('esencial','importante','confort','prescindible')),
  id_fisico           VARCHAR(50),
  estado              VARCHAR(10) NOT NULL DEFAULT 'apagado' CHECK (estado IN ('encendido','apagado')),
  brillo              SMALLINT CHECK (brillo BETWEEN 0 AND 100),
  activo              BOOLEAN NOT NULL DEFAULT true,
  conectado           BOOLEAN NOT NULL DEFAULT false,
  ultima_comunicacion TIMESTAMPTZ,
  UNIQUE (domo_id, id_fisico),
  CHECK (tipo = 'bombillo' OR brillo IS NULL),
  CHECK (tipo <> 'bombillo' OR categoria = 'esencial')
);

CREATE TABLE acciones (
  id              BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  dispositivo_id  BIGINT NOT NULL REFERENCES dispositivos(id) ON DELETE RESTRICT,
  accion          VARCHAR(10) NOT NULL CHECK (accion IN ('encender','apagar')),
  estado_anterior VARCHAR(10) NOT NULL CHECK (estado_anterior IN ('encendido','apagado')),
  estado_nuevo    VARCHAR(10) NOT NULL CHECK (estado_nuevo IN ('encendido','apagado')),
  actor_tipo      VARCHAR(15) NOT NULL CHECK (actor_tipo IN ('sistema','dueno','administrador')),
  actor_ref       VARCHAR(150) NOT NULL,
  condicion       TEXT,
  resultado       VARCHAR(20) NOT NULL,
  fecha_hora      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX ix_acc_disp_fecha ON acciones (dispositivo_id, fecha_hora DESC);

CREATE TABLE alertas (
  id              BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  domo_id         BIGINT NOT NULL REFERENCES domos(id) ON DELETE CASCADE,
  tipo            VARCHAR(30) NOT NULL CHECK (tipo IN ('bateria_baja','consumo_elevado','generacion_nula','dispositivo_desconectado','sobrecarga')),
  nivel           VARCHAR(15) NOT NULL CHECK (nivel IN ('informativo','advertencia','critico')),
  estado          VARCHAR(10) NOT NULL DEFAULT 'activa' CHECK (estado IN ('activa','atendida','cerrada')),
  variable        VARCHAR(50),
  valor_detectado NUMERIC,
  limite          NUMERIC,
  mensaje         TEXT NOT NULL,
  id_fisico_ref   VARCHAR(50),
  creada_por_r13  BOOLEAN NOT NULL DEFAULT false,
  fecha_hora      TIMESTAMPTZ NOT NULL DEFAULT now(),
  fecha_atendida  TIMESTAMPTZ,
  CHECK (estado <> 'atendida' OR fecha_atendida IS NOT NULL)
);
CREATE INDEX ix_alertas_domo ON alertas (domo_id, fecha_hora DESC);

-- Versión A: ancha (una fila por instante)
CREATE TABLE mediciones_a (
  id                 BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  domo_id            BIGINT NOT NULL REFERENCES domos(id) ON DELETE CASCADE,
  fecha_hora         TIMESTAMPTZ NOT NULL,
  soc_pct            NUMERIC(4,1) CHECK (soc_pct BETWEEN 0 AND 100),
  estado_bateria     VARCHAR(12) CHECK (estado_bateria IN ('cargando','descargando','cargada','en_reposo')),
  potencia_fv_w      NUMERIC(7,1) CHECK (potencia_fv_w >= 0),
  potencia_consumo_w NUMERIC(7,1) CHECK (potencia_consumo_w >= 0),
  voltaje_ac_v       NUMERIC(5,1),
  corriente_ac_a     NUMERIC(6,3),
  energia_kwh        NUMERIC(10,3),
  UNIQUE (domo_id, fecha_hora)
);

-- Versión B: larga (una fila por valor medido)
CREATE TABLE mediciones_b (
  id         BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  domo_id    BIGINT NOT NULL REFERENCES domos(id) ON DELETE CASCADE,
  fecha_hora TIMESTAMPTZ NOT NULL,
  variable   VARCHAR(20) NOT NULL CHECK (variable IN ('soc_pct','potencia_fv_w','potencia_consumo_w','voltaje_ac_v','corriente_ac_a','energia_kwh')),
  valor      NUMERIC NOT NULL,
  UNIQUE (domo_id, fecha_hora, variable)
);
