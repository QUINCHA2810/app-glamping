"""Primacía de la decisión humana en R-13 (decisión de diseño).

Si la última acción sobre una toma es un ENCENDIDO de una persona, R-13 no la corta por
su umbral hasta que, después de esa acción, el SoC llegue a su umbral de reactivación;
pero la corta igual si el SoC baja de la reserva de la batería (motivo=reserva).

Con los valores por defecto (lifepo4, conservador): prescindible 50/60, confort 35/45,
importante 20/30, reserva 20 %.

Las acciones del PATCH llevan la hora del servidor (con resolución de un segundo en
SQLite). Las lecturas "posteriores" a una acción se guardan con una fecha unos segundos
en el futuro: siguen siendo el SoC actual (el último valor) y quedan después de ella."""

import logging
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import patch

import tests.base  # noqa: F401  (antes que database, models o main: ver tests/__init__.py)
from sqlalchemy import event
from models import Accion
from servicios.control_energetico import (
    _tomas_activas,
    _ultimas_acciones,
    decidir_r13,
    primacias_humanas,
)
from tests.base import PruebaApi, importar_listener


def despues(segundos: int) -> datetime:
    return datetime.now(timezone.utc) + timedelta(seconds=segundos)


class PruebaPrimacia(PruebaApi):
    def evaluar_completo(self) -> dict:
        respuesta = self.client.post(self.ruta("/control-energetico/evaluar"), headers=self.auth())
        self.assertEqual(respuesta.status_code, 200, respuesta.text)
        return respuesta.json()

    def encender_a_mano(self, toma, usuario=None):
        respuesta = self.patch_dispositivo(toma, {"estado": "encendido"}, usuario)
        self.assertEqual(respuesta.status_code, 200, respuesta.text)
        return respuesta.json()

    def cortar(self, toma, soc=45):
        """La deja apagada por R-13 (prescindible: apaga bajo 50)."""
        self.soc(soc)
        self.assertEqual(self.evaluar(), [(toma.nombre, "apagar")])

    def ultima_accion(self, toma) -> Accion:
        self.db.expire_all()
        return (
            self.db.query(Accion)
            .filter(Accion.dispositivo_id == toma.id)
            .order_by(Accion.fecha_hora.desc(), Accion.id.desc())
            .first()
        )


class ReglaA(PruebaPrimacia):
    # 1
    def test_encendida_a_mano_bajo_el_umbral_y_sobre_la_reserva_no_se_corta(self):
        toma = self.toma("t", categoria="prescindible")
        self.cortar(toma)
        self.encender_a_mano(toma)
        datos = self.evaluar_completo()
        self.assertEqual(datos["acciones"], [])
        self.assertEqual(
            [(t["nombre"], t["umbral_apagado_soc"], t["hasta_soc"], t["reserva_soc"]) for t in datos["respetadas_por_primacia_humana"]],
            [("t", 50, 60, 20)],
        )
        # Sigue respetándola en las siguientes evaluaciones.
        self.assertEqual(self.evaluar(), [])
        self.assertEqual(self.recargar(toma).estado, "encendido")

    # 2
    def test_bajo_la_reserva_se_corta_igual_con_motivo_reserva(self):
        toma = self.toma("t", categoria="prescindible", id_fisico="toma1")
        self.cortar(toma)
        self.encender_a_mano(toma)
        self.soc(19, fecha_hora=despues(20))
        with patch("servicios.control_energetico.publicar_comando_toma") as publicar:
            datos = self.evaluar_completo()
        self.assertEqual(
            [(a["nombre"], a["accion_realizada"], a["motivo"], a["umbral_aplicado"]) for a in datos["acciones"]],
            [("t", "apagar", "reserva", 20)],
        )
        self.assertEqual(datos["respetadas_por_primacia_humana"], [])
        publicar.assert_called_once_with(domo_id=self.domo_a.id, accion="off", id_fisico="toma1", origen="r13")
        accion = self.ultima_accion(toma)
        self.assertEqual((accion.actor_tipo, accion.accion), ("sistema", "apagar"))
        self.assertEqual(accion.condicion, "modo=supervivencia; soc=19.0; motivo=reserva")
        # Se ve en el historial.
        historial = self.client.get(self.ruta("/historial"), headers=self.auth()).json()
        self.assertIn("motivo=reserva", historial[0]["condicion_control"])

    def test_justo_en_la_reserva_todavia_se_respeta(self):
        toma = self.toma("t", categoria="prescindible")
        self.cortar(toma)
        self.encender_a_mano(toma)
        self.soc(20, fecha_hora=despues(20))
        self.assertEqual(self.evaluar(), [])

    # 3
    def test_despues_de_recuperarse_vuelve_a_la_regla_normal(self):
        toma = self.toma("t", categoria="prescindible")
        self.cortar(toma)
        self.encender_a_mano(toma)
        self.soc(60, fecha_hora=despues(20))  # = reactivación: la batería se recuperó
        self.assertEqual(self.evaluar(), [])
        self.soc(49, fecha_hora=despues(30))
        datos = self.evaluar_completo()
        self.assertEqual([(a["nombre"], a["accion_realizada"], a["motivo"]) for a in datos["acciones"]], [("t", "apagar", None)])
        self.assertEqual(self.ultima_accion(toma).condicion, "modo=supervivencia; soc=49.0")

    def test_subir_sin_llegar_a_la_reactivacion_no_la_acaba(self):
        toma = self.toma("t", categoria="prescindible")
        self.cortar(toma)
        self.encender_a_mano(toma)
        self.soc(59.9, fecha_hora=despues(20))
        self.soc(45, fecha_hora=despues(30))
        self.assertEqual(self.evaluar(), [])

    def test_un_soc_alto_anterior_al_encendido_no_cuenta(self):
        toma = self.toma("t", categoria="prescindible")
        self.soc(90, fecha_hora=despues(-60))
        self.cortar(toma)
        self.encender_a_mano(toma)
        self.assertEqual(self.evaluar(), [])

    # 4
    def test_apagada_a_mano_con_100_no_se_enciende(self):
        toma = self.toma("t", categoria="prescindible")
        self.patch_dispositivo(toma, {"estado": "apagado"})
        self.soc(100)
        datos = self.evaluar_completo()
        self.assertEqual((datos["acciones"], datos["respetadas_por_primacia_humana"]), ([], []))
        self.assertEqual(self.recargar(toma).estado, "apagado")

    # 5
    def test_reactivada_por_r13_no_tiene_primacia(self):
        toma = self.toma("t", categoria="prescindible")
        self.cortar(toma)
        self.soc(60, fecha_hora=despues(20))
        self.assertEqual(self.evaluar(), [("t", "encender")])
        self.soc(45, fecha_hora=despues(30))
        self.assertEqual(self.evaluar(), [("t", "apagar")])

    # 6
    def test_sin_soc_actual_no_decide(self):
        toma = self.toma("t", categoria="prescindible")
        self.soc(45, fecha_hora=despues(-600))
        self.encender_a_mano(toma)
        respuesta = self.client.post(self.ruta("/control-energetico/evaluar"), headers=self.auth())
        self.assertEqual(respuesta.status_code, 409)
        self.assertEqual(self.recargar(toma).estado, "encendido")

    # 7
    def test_dos_tomas_solo_se_corta_la_que_no_tiene_primacia(self):
        con = self.toma("con", categoria="prescindible")
        self.toma("sin", categoria="prescindible")
        self.soc(45)
        self.assertEqual(self.evaluar(), [("con", "apagar"), ("sin", "apagar")])
        self.encender_a_mano(con)
        sin = self.toma("sin-2", categoria="prescindible")  # encendida, sin acción humana
        datos = self.evaluar_completo()
        self.assertEqual([(a["nombre"], a["accion_realizada"]) for a in datos["acciones"]], [("sin-2", "apagar")])
        self.assertEqual([t["nombre"] for t in datos["respetadas_por_primacia_humana"]], ["con"])
        self.assertEqual(self.recargar(sin).estado, "apagado")

    def test_tambien_el_administrador(self):
        toma = self.toma("t", categoria="prescindible")
        self.cortar(toma)
        self.encender_a_mano(toma, self.admin)
        self.assertEqual(self.evaluar(), [])

    def test_la_toma_simulada_igual(self):
        toma = self.toma("t", categoria="confort")  # sin id_fisico
        self.cortar(toma, soc=30)
        self.encender_a_mano(toma)
        with patch("servicios.control_energetico.publicar_comando_toma") as publicar:
            self.assertEqual(self.evaluar(), [])
        publicar.assert_not_called()

    def test_volver_a_apagarla_a_mano_acaba_la_primacia_y_r13_no_la_enciende(self):
        toma = self.toma("t", categoria="prescindible")
        self.cortar(toma)
        self.encender_a_mano(toma)
        self.patch_dispositivo(toma, {"estado": "apagado"})
        self.soc(100, fecha_hora=despues(20))
        self.assertEqual(self.evaluar(), [])
        self.assertEqual(self.recargar(toma).estado, "apagado")

    def test_corte_por_reserva_de_confort_crea_la_alerta_critica(self):
        toma = self.toma("t", categoria="confort")
        self.cortar(toma, soc=30)
        alertas_antes = len(self.alertas_bateria())
        for alerta in self.alertas_bateria():
            alerta.cambiar_estado("atendida")
        self.db.commit()
        self.encender_a_mano(toma)
        self.soc(15, fecha_hora=despues(20))
        self.assertEqual(self.evaluar(), [("t", "apagar")])
        alertas = self.alertas_bateria()
        self.assertEqual(len(alertas), alertas_antes + 1)
        self.assertEqual((alertas[-1].nivel, alertas[-1].estado), ("critico", "activa"))

    def test_con_bateria_agm_la_reserva_es_50(self):
        self.perfil("conservador", "agm")  # prescindible 80/90, reserva 50
        toma = self.toma("t", categoria="prescindible")
        self.cortar(toma, soc=64)
        self.encender_a_mano(toma)
        self.assertEqual(self.evaluar(), [])
        self.soc(49, fecha_hora=despues(20))
        datos = self.evaluar_completo()
        self.assertEqual([(a["nombre"], a["motivo"], a["umbral_aplicado"]) for a in datos["acciones"]], [("t", "reserva", 50)])


class VistaEnLaApi(PruebaPrimacia):
    def test_el_dispositivo_trae_la_primacia_vigente(self):
        toma = self.toma("t", categoria="prescindible")
        otra = self.toma("otra", categoria="prescindible")
        self.soc(45)
        self.evaluar()
        datos = self.encender_a_mano(toma)
        self.assertEqual(datos["primacia_humana_hasta_soc"], 60)
        self.assertIsNotNone(datos["primacia_humana_desde"])
        lista = {d["nombre"]: d for d in self.client.get(self.ruta("/dispositivos"), headers=self.auth()).json()}
        self.assertEqual(lista["t"]["primacia_humana_hasta_soc"], 60)
        self.assertIsNone(lista["otra"]["primacia_humana_hasta_soc"])
        uno = self.client.get(self.ruta(f"/dispositivos/{toma.id}"), headers=self.auth()).json()
        self.assertEqual(uno["primacia_humana_hasta_soc"], 60)
        # Se acaba al recuperarse la batería.
        self.soc(60, fecha_hora=despues(20))
        uno = self.client.get(self.ruta(f"/dispositivos/{toma.id}"), headers=self.auth()).json()
        self.assertIsNone(uno["primacia_humana_hasta_soc"])

    def test_esenciales_y_bombillos_no_la_muestran(self):
        esencial = self.toma("e", categoria="esencial", estado="apagado")
        bombillo = self.bombillo()
        self.assertIsNone(self.encender_a_mano(esencial)["primacia_humana_hasta_soc"])
        self.assertIsNone(self.encender_a_mano(bombillo)["primacia_humana_hasta_soc"])

    def test_por_cortar_no_anuncia_las_respetadas_pero_si_bajo_la_reserva(self):
        toma = self.toma("t", categoria="prescindible")
        self.cortar(toma)
        self.encender_a_mano(toma)
        estado = self.client.get(self.ruta("/control-energetico/estado"), headers=self.auth()).json()
        self.assertEqual(estado["tomas_por_cortar"], [])
        self.soc(19, fecha_hora=despues(20))
        estado = self.client.get(self.ruta("/control-energetico/estado"), headers=self.auth()).json()
        self.assertEqual([t["nombre"] for t in estado["tomas_por_cortar"]], ["t"])


class Costo(PruebaPrimacia):
    def contar_consultas(self, funcion):
        consultas = []
        motor = self.db.get_bind()

        def contar(*_):
            consultas.append(1)

        event.listen(motor, "before_cursor_execute", contar)
        try:
            resultado = funcion()
        finally:
            event.remove(motor, "before_cursor_execute", contar)
        return resultado, len(consultas)

    def test_una_sola_consulta_por_domo_para_la_recuperacion(self):
        tomas = [self.toma(f"t{i}", categoria="prescindible") for i in range(4)]
        self.soc(45)
        self.evaluar()
        for toma in tomas:
            self.encender_a_mano(toma)
        self.db.expire_all()
        domo = self.domo_a
        activas = _tomas_activas(self.db, domo)
        ultimas = _ultimas_acciones(self.db, domo)
        primacias, consultas = self.contar_consultas(lambda: primacias_humanas(self.db, domo, activas, ultimas))
        self.assertEqual(sorted(primacias), sorted(t.id for t in tomas))
        self.assertEqual(consultas, 1)

    def test_sin_encendidos_humanos_no_consulta_mediciones(self):
        self.toma("t", categoria="prescindible")
        self.soc(45)
        self.db.expire_all()
        domo = self.domo_a
        activas = _tomas_activas(self.db, domo)
        ultimas = _ultimas_acciones(self.db, domo)
        primacias, consultas = self.contar_consultas(lambda: primacias_humanas(self.db, domo, activas, ultimas))
        self.assertEqual((primacias, consultas), ({}, 0))

    def test_la_ultima_accion_de_cada_toma_en_una_consulta(self):
        tomas = [self.toma(f"t{i}", categoria="prescindible") for i in range(3)]
        self.soc(45)
        self.evaluar()
        self.encender_a_mano(tomas[0])
        self.patch_dispositivo(tomas[0], {"estado": "apagado"})
        self.db.expire_all()
        domo = self.domo_a
        self.assertIsNotNone(domo.id)  # recarga el domo fuera de la cuenta
        ultimas, consultas = self.contar_consultas(lambda: _ultimas_acciones(self.db, domo))
        self.assertEqual(consultas, 1)
        self.assertEqual(
            {i: (a.actor_tipo, a.accion) for i, a in ultimas.items()},
            {tomas[0].id: ("dueno", "apagar"), tomas[1].id: ("sistema", "apagar"), tomas[2].id: ("sistema", "apagar")},
        )


class FuncionPura(TestCase):
    def toma(self, id, categoria="prescindible"):
        return SimpleNamespace(id=id, categoria=categoria)

    def test_respeta_solo_las_que_tienen_primacia_y_corta_bajo_la_reserva(self):
        a, b = self.toma(1), self.toma(2)
        decision = decidir_r13(45, [a, b], [], "lifepo4", "conservador", {1})
        self.assertEqual(([t.id for t in decision.a_apagar], [t.id for t in decision.respetadas]), ([2], [1]))
        self.assertEqual(decision.cortadas_por_reserva, frozenset())
        decision = decidir_r13(19, [a, b], [], "lifepo4", "conservador", {1})
        self.assertEqual([t.id for t in decision.a_apagar], [1, 2])
        self.assertEqual(decision.cortadas_por_reserva, frozenset({1}))

    def test_sin_primacia_es_la_regla_de_siempre(self):
        a = self.toma(1)
        self.assertEqual([t.id for t in decidir_r13(45, [a], [], "lifepo4", "conservador").a_apagar], [1])

    def test_sobre_el_umbral_no_hay_nada_que_respetar(self):
        decision = decidir_r13(55, [self.toma(1)], [], "lifepo4", "conservador", {1})
        self.assertEqual((decision.a_apagar, decision.respetadas), ([], []))


class LogDelListener(TestCase):
    def test_se_anuncia_una_vez_por_toma_y_por_encendido(self):
        listener = importar_listener()
        listener._primacia_anunciada.clear()
        desde = datetime(2026, 11, 15, 12, 0, tzinfo=timezone.utc)

        def resultado(desde):
            toma = SimpleNamespace(
                dispositivo_id=7, nombre="Cargadores", umbral_apagado_soc=50, hasta_soc=60, reserva_soc=20, encendida_desde=desde
            )
            return SimpleNamespace(estado_carga_soc=45, respetadas_por_primacia_humana=[toma])

        with self.assertLogs("mqtt_listener", logging.INFO) as registro:
            listener._anunciar_primacias(1, resultado(desde))
            listener._anunciar_primacias(1, resultado(desde))
            listener._anunciar_primacias(1, resultado(desde))
            listener._anunciar_primacias(1, resultado(desde + timedelta(hours=1)))  # otro encendido
        self.assertEqual(len(registro.records), 2)
        self.assertIn("respeta 'Cargadores', encendida a mano", registro.output[0])
        listener._primacia_anunciada.clear()
