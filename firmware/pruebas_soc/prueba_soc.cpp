// Prueba en el PC del estimador de SoC (domo_nodo/soc_estimador.cpp) y de la recalibración
// guiada (domo_nodo/soc_recalibracion.cpp). Desde firmware/:
//   C:\msys64\ucrt64\bin\g++.exe -std=c++17 -Wall -static -o prueba_soc.exe pruebas_soc/prueba_soc.cpp domo_nodo/soc_estimador.cpp domo_nodo/soc_recalibracion.cpp
//   .\prueba_soc.exe
// Usa los mismos valores que sensores_config.h (tabla y constantes del SoC).
#include <stdio.h>
#include <math.h>
#include <string.h>
#include "../domo_nodo/soc_estimador.h"
#include "../domo_nodo/soc_recalibracion.h"

// Solo para leer la tabla y las constantes reales; las macros de pines no estorban.
#include "../domo_nodo/sensores_config.h"

using namespace soc;

static int fallos = 0, pruebas = 0;

static void comprobar(bool ok, const char* que) {
    pruebas++;
    if (!ok) { fallos++; printf("FALLO: %s\n", que); }
}
static bool cerca(float a, float b, float tol = 0.01f) { return fabsf(a - b) <= tol; }

static Config config() {
    Config c;
    c.capacidadWh = BATERIA_CAPACIDAD_WH;
    c.eficienciaCarga = EFICIENCIA_CARGA;
    c.eficienciaDescarga = EFICIENCIA_DESCARGA;
    c.reposoIpvA = REPOSO_REAL_IPV_A;
    c.reposoIbatA = REPOSO_REAL_IBAT_A;
    c.reposoS = REPOSO_REAL_S;
    c.dtMaxReposoS = REPOSO_DT_MAX_S;
    c.sinCargaS = SIN_CARGA_S;
    c.tauCorreccionS = TAU_CORRECCION_S;
    c.tauEstadoS = TAU_ESTADO_S;
    c.pEstableW = P_ESTABLE_W;
    c.rCompensacionOhm = R_COMPENSACION_OHM;
    c.vFlotacionMin = V_FLOTACION_MIN;
    c.socCargadaMin = SOC_CARGADA_MIN;
    c.tablaV = SOC_TABLA_V;
    c.tablaPct = SOC_TABLA_PCT;
    c.tablaPuntos = SOC_TABLA_PUNTOS;
    c.vCargaCompleta = V_CARGA_COMPLETA;
    c.iColaMaxA = I_COLA_MAX_A;
    c.cargaCompletaS = CARGA_COMPLETA_S;
    return c;
}

static recal::Config configRecal() {
    return { RECAL_IPV_MAX_A, RECAL_IINV_MAX_A, RECAL_IBAT_MAX_A, RECAL_REPOSO_MIN * 60.0f,
             RECAL_MAX_MIN * 60.0f, RECAL_PROPUESTA_MIN * 60.0f, (float)REPOSO_DT_MAX_S };
}

// Un intervalo de publicación como lo hace Sensores.ino: contar, reposo, estado.
static Estado paso(Estimador& e, const Config& c, float pNeta, float v, float iPv, float dt = 10) {
    double wh = fabs(pNeta) * dt / 3600.0;
    contar(e, c, pNeta > 0 ? wh : 0, pNeta < 0 ? wh : 0);
    reposoYCorreccion(e, c, iPv, pNeta / v, v, dt);
    return actualizarEstado(e, c, pNeta, v, dt);
}

// Lo mismo, y dice si corrigió por voltaje.
static bool pasoCorrige(Estimador& e, const Config& c, float pNeta, float v, float iPv, float dt = 10) {
    double wh = fabs(pNeta) * dt / 3600.0;
    contar(e, c, pNeta > 0 ? wh : 0, pNeta < 0 ? wh : 0);
    bool corrigio = reposoYCorreccion(e, c, iPv, pNeta / v, v, dt);
    actualizarEstado(e, c, pNeta, v, dt);
    return corrigio;
}

// n intervalos de 10 s; devuelve en cuál corrigió por primera vez (-1 si nunca).
static int primeraCorreccion(Estimador& e, const Config& c, int n, float pNeta, float v, float iPv) {
    int primera = -1;
    for (int i = 0; i < n; i++)
        if (pasoCorrige(e, c, pNeta, v, iPv) && primera < 0) primera = i;
    return primera;
}

static void tabla() {
    Config c = config();
    comprobar(cerca(socDeVoltaje(c, 12.35f), 50), "tabla: 12,35 V = 50 %");
    comprobar(cerca(socDeVoltaje(c, 12.30f), 45), "tabla: interpola entre puntos");
    comprobar(socDeVoltaje(c, 11.0f) == 0 && socDeVoltaje(c, 13.2f) == 100, "tabla: extremos");
    comprobar(cerca(vCompensado(c, 12.5f, -0.65f), 12.5065f, 1e-4f), "V compensado = V - I*R");
    comprobar(vCompensado(c, 12.5f, NAN) == 12.5f, "V compensado sin corriente");
}

static void conteo() {
    Config c = config();
    Estimador e;
    contar(e, c, 100, 0);
    comprobar(isnan(e.soc), "sin iniciar no cuenta");
    iniciar(e, 50, Inicio::Manual);
    contar(e, c, 0, 120);                                   // 120 Wh de 1200 = 10 %
    comprobar(cerca(e.soc, 40) && e.fuente == Fuente::Conteo, "descarga: -10 %");
    contar(e, c, 100, 0);                                   // 100 Wh × 0,90 = 7,5 %
    comprobar(cerca(e.soc, 47.5f), "carga con eficiencia 0,90: +7,5 %");
    contar(e, c, 0, 5000);
    comprobar(e.soc == 0, "límite inferior 0 %");
    contar(e, c, 5000, 0);
    comprobar(e.soc == 100, "límite superior 100 %");
    iniciar(e, 140, Inicio::Manual);
    comprobar(e.soc == 100, "iniciar limita a 0-100");
}

// 11:40-11:47 de hoy: con sol y +20 W netos, el SoC subió de 65,2 a 80,8 %.
static void caso_1140() {
    Config c = config();
    Estimador e;
    iniciar(e, 65.2f, Inicio::Restaurado);
    Estado s = Estado::Ninguno;
    for (int i = 0; i < 42; i++) s = paso(e, c, 20.0f, 13.2f, 1.5f);
    // 20 W × 7 min = 2,33 Wh × 0,90 / 1200 = +0,175 %
    comprobar(cerca(e.soc, 65.2f + 20.0f * 420 / 3600 * 0.9f / 1200 * 100, 0.01f), "11:40: solo sube lo que se contó (+0,18 %)");
    comprobar(s == Estado::Cargando, "11:40: estado cargando");
    comprobar(e.segundosReposo == 0, "11:40: con sol no hay reposo real");
}

// 12:00-12:02: "en_reposo" con 13,2 V mientras descargaba de -0,07 a -0,23 A.
static void caso_1200() {
    Config c = config();
    Estimador e;
    iniciar(e, 84.3f, Inicio::Restaurado);
    Estado s = Estado::Ninguno;
    for (int i = 0; i < 12; i++) s = paso(e, c, -13.2f * (0.07f + 0.016f * (i % 11)), 13.2f, 3.0f);
    comprobar(e.soc < 84.3f, "12:00: descargando el SoC no sube");
    comprobar(s != Estado::EnReposo, "12:00: con sol no es en_reposo");
    comprobar(s == Estado::Descargando, "12:00: sin sentido previo, el signo: descargando");
}

static void reposo_real() {
    Config c = config();
    Estimador e;
    iniciar(e, 80, Inicio::Restaurado);
    const float v = 12.35f;   // ~50 % en la tabla
    // Ya pasaron las 2 h sin carga (eso se prueba en sin_carga_reciente).
    e.segundosSinCarga = c.sinCargaS;
    // De noche, inversor en vacío: -0,65 A. Sin conteo, para ver solo la corrección.
    for (int i = 0; i < 179; i++) reposoYCorreccion(e, c, 0.0f, -0.65f, v, 10);
    comprobar(e.soc == 80 && !enReposoReal(e, c), "29 min 50 s de reposo: aún no corrige");
    comprobar(reposoYCorreccion(e, c, 0.0f, -0.65f, v, 10) && e.fuente == Fuente::CorreccionVoltaje, "30 min: corrige");
    float objetivo = e.ultimaTabla;
    comprobar(cerca(objetivo, socDeVoltaje(c, v + 0.65f * R_COMPENSACION_OHM)), "corrige hacia la tabla con V compensado");
    comprobar(actualizarEstado(e, c, -0.65f * v, v, 10) == Estado::EnReposo, "en reposo real: en_reposo");
    float antes = e.soc;
    for (int i = 0; i < 180; i++) reposoYCorreccion(e, c, 0.0f, -0.65f, v, 10);   // 30 min más
    comprobar(cerca((antes - e.soc) / (antes - objetivo), 1 - expf(-1), 0.01f), "filtro lento: 63 % en TAU (30 min)");
    // Se rompe el reposo: el contador vuelve a 0.
    reposoYCorreccion(e, c, 0.3f, -0.65f, v, 10);
    comprobar(e.segundosReposo == 0 && e.segundosSinCarga == 0, "corriente FV: el reposo y el tiempo sin carga vuelven a 0");
    e.segundosSinCarga = c.sinCargaS;        // para probar cada condición por separado
    reposoYCorreccion(e, c, 0.0f, -1.6f, v, 10);
    comprobar(e.segundosReposo == 0, "|I_bat| >= 1,5 A: no es reposo");
    reposoYCorreccion(e, c, 0.0f, -0.65f, v, 10);
    reposoYCorreccion(e, c, 0.0f, -0.65f, v, 120);
    comprobar(e.segundosReposo == 0, "un intervalo largo (sin broker) reinicia el reposo");
    reposoYCorreccion(e, c, NAN, -0.65f, v, 10);
    comprobar(e.segundosReposo == 0, "sin sensor del panel no hay reposo");
    reposoYCorreccion(e, c, 0.0f, NAN, v, 10);
    comprobar(e.segundosReposo == 0, "sin corriente de batería (saturado) no hay reposo");
}

// Carga superficial (23-nov, 13:27): tras cargar, descargando a 1,2 A el voltaje sigue
// inflado (12,65 V = 80 % en la tabla con ~65 % real).
static void sin_carga_reciente() {
    Config c = config();
    const float v = 12.65f, pDescarga = -1.2f * v;
    {
        Estimador e;
        iniciar(e, 65, Inicio::Manual);
        for (int i = 0; i < 360; i++) pasoCorrige(e, c, 50.0f, 13.4f, 2.0f);        // 1 h cargando
        comprobar(e.cargaAhora && e.segundosSinCarga == 0, "cargando: sin carga en 0");
        const float socFinCarga = e.soc;
        // Recién terminada la carga, de noche, 1,2 A (< 1,5 A): sería "reposo", pero no corrige.
        int primera = primeraCorreccion(e, c, 720, pDescarga, v, 0.0f);            // 2 h
        comprobar(primera < 0 && e.segundosReposo == 0, "caso de hoy: 2 h tras cargar, 1,2 A con PV 0: NO corrige");
        comprobar(e.soc < socFinCarga, "caso de hoy: el SoC solo baja por el conteo");
        // 2 h sin carga + 30 min de reposo. La P media tarda ~70 s en bajar de +6 W, así
        // que corrige ~31 min después de las 2 h: intervalo ~186 de estos 40 min.
        primera = primeraCorreccion(e, c, 240, pDescarga, v, 0.0f);
        comprobar(primera >= 180 && primera <= 195, "tras 2 h sin carga + 30 min: SÍ corrige");
        comprobar(e.fuente == Fuente::CorreccionVoltaje && !e.cargaAhora, "fuente: corrección por voltaje");
    }
    {
        // Una nube de 40 min de día después de cargar.
        Estimador e;
        iniciar(e, 65, Inicio::Manual);
        for (int i = 0; i < 360; i++) pasoCorrige(e, c, 80.0f, 13.5f, 3.0f);
        int primera = primeraCorreccion(e, c, 240, pDescarga, v, 0.0f);
        comprobar(primera < 0 && e.segundosReposo == 0, "nube de 40 min tras cargar: NO corrige");
        pasoCorrige(e, c, 40.0f, 13.3f, 1.0f);                                      // vuelve el sol
        comprobar(e.segundosSinCarga == 0, "vuelve el sol: sin carga en 0");
    }
    {
        // 1,4 A de noche cuenta como reposo; 1,6 A no.
        Estimador e;
        iniciar(e, 65, Inicio::Manual);
        e.segundosSinCarga = c.sinCargaS;
        reposoYCorreccion(e, c, 0.0f, -1.4f, 12.4f, 10);
        comprobar(e.segundosReposo == 10, "1,4 A de noche: cuenta como reposo");
        reposoYCorreccion(e, c, 0.0f, -1.6f, 12.4f, 10);
        comprobar(e.segundosReposo == 0, "1,6 A: no es reposo");
        float antes = e.segundosSinCarga;
        reposoYCorreccion(e, c, 0.0f, -0.65f, 12.4f, 120);
        comprobar(e.segundosSinCarga == antes, "un intervalo largo no suma tiempo sin carga");
    }
    {
        // Reinicio: el contador sin carga empieza en 0, aunque sea de noche y en reposo.
        Estimador e;
        comprobar(e.segundosSinCarga == 0 && !e.cargaAhora, "al arrancar: sin carga en 0");
        iniciar(e, 65, Inicio::InicialVoltaje);
        int primera = primeraCorreccion(e, c, 900, -0.65f * 12.4f, 12.4f, 0.0f);   // 2 h 30 min
        // El intervalo que completa las 2 h ya cuenta 10 s de reposo: corrige en el 899.º.
        comprobar(primera == 898, "reinicio: corrige recién a las 2 h + 30 min");
    }
}

static void estado_histeresis() {
    Config c = config();
    Estimador e;
    iniciar(e, 60, Inicio::Manual);
    const float v = 12.6f;
    comprobar(actualizarEstado(e, c, NAN, v, 10) == Estado::Ninguno, "sin potencia: null");
    Estado s = Estado::Ninguno;
    for (int i = 0; i < 30; i++) s = actualizarEstado(e, c, 20, v, 10);
    comprobar(s == Estado::Cargando, "+20 W: cargando");
    for (int i = 0; i < 30; i++) s = actualizarEstado(e, c, -3, v, 10);
    comprobar(s == Estado::Cargando && fabsf(e.pMedia) < P_ESTABLE_W, "-3 W (estable): conserva cargando");
    for (int i = 0; i < 30; i++) s = actualizarEstado(e, c, -20, v, 10);
    comprobar(s == Estado::Descargando, "-20 W: descargando");
    for (int i = 0; i < 30; i++) s = actualizarEstado(e, c, 3, v, 10);
    comprobar(s == Estado::Descargando, "+3 W (estable): conserva descargando");
    // Descargando a -20 W, un pico de carga de 10 s (+60 W) no cambia el estado: es la media.
    for (int i = 0; i < 30; i++) actualizarEstado(e, c, -20, v, 10);
    s = actualizarEstado(e, c, 60, v, 10);
    comprobar(s == Estado::Descargando, "pico de 10 s: la media no cambia el estado");
}

static void cargada() {
    Config c = config();
    Estimador e;
    iniciar(e, 96, Inicio::Manual);
    comprobar(actualizarEstado(e, c, 10, 13.5f, 10) == Estado::Cargada, "13,5 V y SoC 96: cargada");
    comprobar(e.soc == 96, "cargada no resincroniza a 100 %");
    Estimador f;
    iniciar(f, 60, Inicio::Manual);
    comprobar(actualizarEstado(f, c, 10, 13.5f, 10) == Estado::Cargando, "13,5 V con SoC 60 (sol a mitad de carga): cargando");
}

// ---------------------------------------------------------------- Arranque
static void arranque() {
    const uint32_t minima = 1700000000UL, ahora = 1795000000UL;
    DecisionArranque d = decidirArranque(true, 58.0f, ahora - 21 * 3600, ahora, minima, false);
    comprobar(d.accion == Arranque::Restaurar && d.soc == 58.0f && d.edadS == 21 * 3600,
              "arranque: guardado de hace 21 h -> se restaura (con su edad)");
    d = decidirArranque(true, 58.0f, ahora - 30 * 86400, ahora, minima, false);
    comprobar(d.accion == Arranque::Restaurar, "arranque: guardado de hace 30 días -> se restaura igual");
    d = decidirArranque(false, NAN, 0, ahora, minima, false);
    comprobar(d.accion == Arranque::Tabla, "arranque: sin guardado y con hora -> tabla");
    d = decidirArranque(false, NAN, 0, 0, minima, false);
    comprobar(d.accion == Arranque::EsperarHora, "arranque: sin guardado ni hora -> espera la hora NTP");
    d = decidirArranque(false, NAN, 0, 0, minima, true);
    comprobar(d.accion == Arranque::Tabla, "arranque: sin guardado, espera vencida -> tabla");
    d = decidirArranque(true, NAN, ahora - 3600, ahora, minima, false);
    comprobar(d.accion == Arranque::Tabla, "arranque: guardado NAN -> tabla");
    d = decidirArranque(true, 140.0f, ahora - 3600, ahora, minima, false);
    comprobar(d.accion == Arranque::Tabla, "arranque: guardado fuera de 0-100 -> tabla");
    d = decidirArranque(true, 66.0f, 0, ahora, minima, false);
    comprobar(d.accion == Arranque::Restaurar && d.edadS == -1, "arranque: guardado sin fecha -> se restaura, edad desconocida");
    d = decidirArranque(true, 66.0f, ahora - 3600, 0, minima, false);
    comprobar(d.accion == Arranque::Restaurar && d.edadS == -1, "arranque: guardado sin hora NTP -> se restaura, edad desconocida");
}

// ---------------------------------------------------------------- Carga completa
// n intervalos de 10 s con V e I_bat dados; devuelve si sincronizó en alguno.
static bool cargaCompleta(Estimador& e, const Config& c, int n, float v, float iBat) {
    bool sincronizo = false;
    for (int i = 0; i < n; i++) sincronizo = sincronizarCargaCompleta(e, c, v, iBat, 10) || sincronizo;
    return sincronizo;
}

static void carga_completa() {
    Config c = config();
    {
        // (a) Hoy, 24-nov: 13,5-13,8 V con 0,2-2,5 A y SoC 58 % durante 1 h: NO.
        Estimador e;
        iniciar(e, 58, Inicio::Manual);
        bool s = false;
        for (int i = 0; i < 360; i++) {
            float v = 13.5f + 0.3f * (i % 7) / 6.0f, iBat = 0.2f + 2.3f * (i % 11) / 10.0f;
            s = sincronizarCargaCompleta(e, c, v, iBat, 10) || s;
        }
        comprobar(!s && e.soc == 58 && e.segundosCargaCompleta == 0, "carga completa (a): 13,5-13,8 V 1 h con SoC 58 %: NO");
    }
    {
        // (b) 14,6 V con 0,5 A durante 31 min: 100 %.
        Estimador e;
        iniciar(e, 82, Inicio::Restaurado);
        comprobar(!cargaCompleta(e, c, 179, 14.6f, 0.5f) && e.soc == 82, "carga completa (b): 29 min 50 s aún no");
        comprobar(cargaCompleta(e, c, 7, 14.6f, 0.5f) && e.soc == 100 && e.fuente == Fuente::CargaCompleta,
                  "carga completa (b): 14,6 V con 0,5 A 31 min -> 100 %");
        comprobar(!cargaCompleta(e, c, 6, 14.6f, 0.5f), "carga completa (b): ya en 100 % no vuelve a avisar");
    }
    {
        // (c) 14,6 V 25 min + una muestra a 13,9 V + 10 min: NO.
        Estimador e;
        iniciar(e, 82, Inicio::Restaurado);
        bool s = cargaCompleta(e, c, 150, 14.6f, 0.5f);
        s = sincronizarCargaCompleta(e, c, 13.9f, 0.5f, 10) || s;
        comprobar(e.segundosCargaCompleta == 0, "carga completa (c): una muestra a 13,9 V reinicia el contador");
        s = cargaCompleta(e, c, 60, 14.6f, 0.5f) || s;
        comprobar(!s && e.soc == 82, "carga completa (c): 25 min + 13,9 V + 10 min: NO");
    }
    {
        // (d) 14,6 V con 3 A (todavía en bulk/absorción): NO.
        Estimador e;
        iniciar(e, 82, Inicio::Restaurado);
        comprobar(!cargaCompleta(e, c, 240, 14.6f, 3.0f) && e.soc == 82, "carga completa (d): 14,6 V con 3 A: NO");
    }
    {
        Estimador e;
        iniciar(e, 82, Inicio::Restaurado);
        comprobar(!cargaCompleta(e, c, 240, 14.6f, NAN), "carga completa: sin corriente conocida (NAN) no cuenta");
        comprobar(!cargaCompleta(e, c, 240, 14.6f, -0.3f), "carga completa: descargando no cuenta");
        cargaCompleta(e, c, 100, 14.6f, 0.5f);
        sincronizarCargaCompleta(e, c, 14.6f, 0.5f, 120);
        comprobar(e.segundosCargaCompleta == 0, "carga completa: un intervalo largo (sin broker) reinicia");
    }
}

// ---------------------------------------------------------------- Recalibración guiada
static void recal_condiciones() {
    recal::Config r = configRecal();
    comprobar(recal::condicion(r, 0.0f, 0.0f, false, -0.25f) == recal::Motivo::EnReposo, "recal: de noche e inversor apagado: reposo");
    comprobar(recal::condicion(r, 2.0f, 0.0f, false, 0.5f) == recal::Motivo::HaySol, "recal: sol -> no (hay sol)");
    comprobar(recal::condicion(r, 0.0f, 2.0f, false, -2.0f) == recal::Motivo::ConsumoInversor, "recal: inversor con 2 A -> no");
    comprobar(recal::condicion(r, 0.0f, 29.5f, true, NAN) == recal::Motivo::InversorSinLectura, "recal: inversor saturado -> no");
    comprobar(recal::condicion(r, 0.0f, NAN, false, -0.25f) == recal::Motivo::InversorSinLectura, "recal: inversor sin lectura -> no");
    comprobar(recal::condicion(r, NAN, 0.0f, false, -0.25f) == recal::Motivo::SinSensorPv, "recal: sin sensor FV -> no");
    comprobar(recal::condicion(r, 0.0f, 0.3f, false, -1.6f) == recal::Motivo::CorrienteBateria, "recal: |I_bat| 1,6 A -> no");
    comprobar(recal::condicion(r, 0.0f, 0.3f, false, NAN) == recal::Motivo::CorrienteBateria, "recal: I_bat desconocida -> no");
}

// n muestras de 10 s; avanza el reloj `ms`.
static void muestras(recal::Recal& s, const recal::Config& r, const Config& c, int n, float iPv, float iInv,
                     float iBat, float v, uint32_t& ms) {
    for (int i = 0; i < n; i++) {
        ms += 10000;
        recal::muestra(s, r, c, iPv, iInv, false, iBat, v, 100.0f, 10, ms);
    }
}

static void recal_sesion() {
    recal::Config r = configRecal();
    Config c = config();
    const float v = 12.40f;                                      // con -0,25 A: ~55 % en la tabla
    {
        // 15 min en reposo -> lista, con la tabla y el V compensado de siempre.
        recal::Recal s;
        uint32_t ms = 4000000000UL;                              // cerca del desborde de millis()
        recal::iniciar(s, ms);
        muestras(s, r, c, 89, 0.0f, 0.0f, -0.25f, v, ms);
        comprobar(s.estado == recal::Estado::Esperando && cerca(recal::minutosOk(s), 14.833f, 0.01f), "recal: 14 min 50 s: sigue esperando");
        muestras(s, r, c, 1, 0.0f, 0.0f, -0.25f, v, ms);
        comprobar(s.estado == recal::Estado::Lista, "recal: 15 min en reposo -> lista");
        comprobar(cerca(s.socPropuesto, socDeVoltaje(c, vCompensado(c, v, -0.25f))) && s.socActual == 100.0f,
                  "recal: propuesta = tabla con V compensado; no cambia el SoC actual");
        float valor = NAN;
        ms += 60000;
        comprobar(recal::confirmar(s, r, ms, valor) && valor == s.socPropuesto && s.estado == recal::Estado::Aplicada,
                  "recal: confirmar dentro de 5 min -> aplicada con la propuesta");
        comprobar(!recal::confirmar(s, r, ms, valor) && s.estado == recal::Estado::Rechazada,
                  "recal: confirmar otra vez (sin sesión) -> rechazada");
    }
    {
        // Interrupción a los 10 min -> el contador vuelve a 0 con el motivo.
        recal::Recal s;
        uint32_t ms = 0;
        recal::iniciar(s, ms);
        muestras(s, r, c, 60, 0.0f, 0.0f, -0.25f, v, ms);
        muestras(s, r, c, 1, 0.0f, 2.0f, -2.0f, v, ms);
        comprobar(s.segReposo == 0 && s.motivo == recal::Motivo::ConsumoInversor && s.estado == recal::Estado::Esperando,
                  "recal: interrupción a los 10 min (inversor) -> vuelve a 0 e informa el motivo");
        muestras(s, r, c, 1, 0.5f, 0.0f, 0.2f, v, ms);
        comprobar(s.motivo == recal::Motivo::HaySol, "recal: motivo hay sol");
        muestras(s, r, c, 89, 0.0f, 0.0f, -0.25f, v, ms);
        comprobar(s.estado == recal::Estado::Esperando, "recal: tras la interrupción hacen falta otros 15 min");
        muestras(s, r, c, 1, 0.0f, 0.0f, -0.25f, v, ms);
        comprobar(s.estado == recal::Estado::Lista, "recal: 15 min seguidos tras la interrupción -> lista");
    }
    {
        // Confirmar con la propuesta vencida -> rechazada (sin muestras entre medio).
        recal::Recal s;
        uint32_t ms = 0;
        recal::iniciar(s, ms);
        muestras(s, r, c, 90, 0.0f, 0.0f, -0.25f, v, ms);
        float valor = -1;
        ms += (uint32_t)(RECAL_PROPUESTA_MIN * 60 * 1000) + 1000;
        comprobar(!recal::confirmar(s, r, ms, valor) && s.estado == recal::Estado::Rechazada && valor == -1,
                  "recal: confirmar con la propuesta vencida -> rechazada, sin valor");
        comprobar(s.motivo == recal::Motivo::PropuestaVencida, "recal: motivo propuesta vencida");
    }
    {
        // La propuesta sin confirmar vence sola; la espera vence a los 45 min.
        recal::Recal s;
        uint32_t ms = 0;
        recal::iniciar(s, ms);
        muestras(s, r, c, 90, 0.0f, 0.0f, -0.25f, v, ms);
        muestras(s, r, c, 30, 0.0f, 0.0f, -0.25f, v, ms);
        comprobar(s.estado == recal::Estado::Vencida && s.motivo == recal::Motivo::PropuestaVencida, "recal: lista 5 min sin confirmar -> vencida");
        recal::iniciar(s, ms);
        muestras(s, r, c, 269, 2.0f, 0.0f, 1.0f, v, ms);
        comprobar(s.estado == recal::Estado::Esperando, "recal: 44 min 50 s con sol: sigue esperando");
        muestras(s, r, c, 1, 2.0f, 0.0f, 1.0f, v, ms);
        comprobar(s.estado == recal::Estado::Vencida && s.motivo == recal::Motivo::SesionVencida, "recal: 45 min sin reposo -> vencida");
    }
    {
        // Cancelar, confirmar antes de tiempo, intervalo largo, nueva sesión.
        recal::Recal s;
        uint32_t ms = 0;
        float valor = NAN;
        comprobar(!recal::confirmar(s, r, ms, valor) && s.estado == recal::Estado::Rechazada, "recal: confirmar sin sesión -> rechazada");
        recal::iniciar(s, ms);
        muestras(s, r, c, 30, 0.0f, 0.0f, -0.25f, v, ms);
        comprobar(!recal::confirmar(s, r, ms, valor) && s.estado == recal::Estado::Rechazada, "recal: confirmar mientras espera -> rechazada");
        recal::iniciar(s, ms);
        muestras(s, r, c, 30, 0.0f, 0.0f, -0.25f, v, ms);
        ms += 120000;
        recal::muestra(s, r, c, 0.0f, 0.0f, false, -0.25f, v, 60, 120, ms);
        comprobar(s.segReposo == 0 && s.motivo == recal::Motivo::LecturasInterrumpidas, "recal: intervalo largo -> vuelve a 0");
        recal::cancelar(s);
        comprobar(s.estado == recal::Estado::Cancelada && !recal::activa(s), "recal: cancelar termina la sesión");
        comprobar(!recal::muestra(s, r, c, 0.0f, 0.0f, false, -0.25f, v, 60, 10, ms + 10000) && s.segReposo == 0,
                  "recal: cancelada no sigue contando");
        recal::iniciar(s, ms);
        comprobar(s.estado == recal::Estado::Esperando && isnan(s.socPropuesto), "recal: iniciar borra la propuesta anterior");
    }
    comprobar(!strcmp(recal::textoEstado(recal::Estado::Inactiva), "inactiva") && !strcmp(recal::textoEstado(recal::Estado::Lista), "lista"),
              "recal: textos de estado");
}

int main() {
    tabla();
    conteo();
    caso_1140();
    caso_1200();
    reposo_real();
    sin_carga_reciente();
    estado_histeresis();
    cargada();
    arranque();
    carga_completa();
    recal_condiciones();
    recal_sesion();
    printf("%d pruebas, %d fallos\n", pruebas, fallos);
    return fallos == 0 ? 0 : 1;
}
