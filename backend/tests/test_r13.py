"""Algoritmo de supervivencia (R-13) con categorías.

Modelo v2: los umbrales de cada toma se CALCULAN con su categoría y la batería y el
perfil del domo. Con los valores por defecto (lifepo4, conservador): prescindible
50/60, confort 35/45, importante 20/30; esencial nunca se corta."""

import tests.base  # noqa: F401  (antes que database, models o main: ver tests/__init__.py)
from models import Dispositivo
from schemas import UMBRALES_CALCULADOS
from servicios.categorias import CATEGORIAS, PERFILES, RESERVA_SOC_POR_BATERIA, preset_de
from tests.base import PruebaApi


class Umbrales(PruebaApi):
    def test_patch_con_umbrales_da_422_y_no_cambia_nada(self):
        toma = self.toma(categoria="prescindible")
        for cuerpo in (
            {"umbral_apagado_soc": 56},
            {"umbral_reactivacion_soc": 70},
            {"umbral_apagado_soc": 50, "umbral_reactivacion_soc": 60},
            {"categoria": "confort", "umbral_apagado_soc": 40, "umbral_reactivacion_soc": 50},
        ):
            with self.subTest(cuerpo=cuerpo):
                respuesta = self.patch_dispositivo(toma, cuerpo)
                self.assertEqual(respuesta.status_code, 422)
                self.assertEqual(respuesta.json()["detail"], UMBRALES_CALCULADOS)
        self.assertEqual(self.recargar(toma).categoria, "prescindible")

    def test_crear_con_umbrales_da_422(self):
        respuesta = self.crear({"tipo": "toma", "nombre": "t", "umbral_apagado_soc": 40, "umbral_reactivacion_soc": 50})
        self.assertEqual(respuesta.status_code, 422)
        self.assertEqual(self.db.query(Dispositivo).count(), 0)

    def test_la_banda_del_preset_siempre_es_de_10(self):
        """Reemplaza a la prueba de bandas con decimales de v1: ya no hay umbrales
        guardados que validar, y los calculados siempre dejan 10 puntos de histéresis
        sobre la reserva."""
        for tipo, reserva in RESERVA_SOC_POR_BATERIA.items():
            for perfil in PERFILES:
                for categoria in CATEGORIAS[1:]:
                    with self.subTest(tipo=tipo, perfil=perfil, categoria=categoria):
                        preset = preset_de(categoria, tipo, perfil)
                        self.assertEqual(preset.umbral_reactivacion_soc - preset.umbral_apagado_soc, 10)
                        self.assertGreaterEqual(preset.umbral_apagado_soc, reserva)

    def test_la_respuesta_trae_los_umbrales_calculados(self):
        datos = self.crear({"tipo": "toma", "nombre": "t", "categoria": "prescindible"}).json()
        self.assertEqual(
            (datos["umbral_apagado_soc"], datos["umbral_reactivacion_soc"], datos["prioridad"], datos["personalizado"]),
            (50, 60, 10, False),
        )

    def test_con_soc_fijo_la_toma_no_alterna(self):
        self.toma("t", categoria="prescindible")
        self.soc(45)
        self.assertEqual(self.evaluar(), [("t", "apagar")])
        for _ in range(3):
            self.assertEqual(self.evaluar(), [])

    def test_ciclo_con_histeresis(self):
        self.toma("t", categoria="prescindible")
        self.soc(49.9)
        self.assertEqual(self.evaluar(), [("t", "apagar")])
        self.soc(59.9)
        self.assertEqual(self.evaluar(), [])
        self.soc(60)
        self.assertEqual(self.evaluar(), [("t", "encender")])
        self.soc(50)
        self.assertEqual(self.evaluar(), [])


class UmbralesCalculados(PruebaApi):
    """Cambiar el perfil o la batería cambia cuándo corta R-13, sin tocar los
    dispositivos (en v1 había que recalcular sus columnas)."""

    def poner_perfil(self, perfil):
        respuesta = self.client.put(self.ruta("/configuracion"), json={"perfil_control": perfil}, headers=self.auth())
        self.assertEqual(respuesta.status_code, 200, respuesta.text)

    def test_cambiar_el_perfil_cambia_cuando_corta_sin_tocar_los_dispositivos(self):
        toma = self.toma("c")
        antes = {c.name: getattr(toma, c.name) for c in Dispositivo.__table__.columns}
        self.poner_perfil("maximo_uso")  # confort: 20 + 5 = 25
        self.soc(30)
        self.assertEqual(self.evaluar(), [])
        self.db.expire_all()
        self.assertEqual({c.name: getattr(self.recargar(toma), c.name) for c in Dispositivo.__table__.columns}, antes)

        self.poner_perfil("conservador")  # confort: 20 + 15 = 35
        self.assertEqual(self.evaluar(), [("c", "apagar")])

    def test_la_bateria_tambien(self):
        self.toma("c")
        self.soc(60)
        self.assertEqual(self.evaluar(), [])  # lifepo4: confort 35
        self.poner_bateria("agm")  # agm: confort 50 + 15 = 65
        self.assertEqual(self.evaluar(), [("c", "apagar")])

    def test_el_listado_muestra_los_umbrales_del_perfil_actual(self):
        toma = self.toma("c")
        self.poner_perfil("equilibrado")
        datos = self.client.get(self.ruta(f"/dispositivos/{toma.id}"), headers=self.auth()).json()
        self.assertEqual((datos["umbral_apagado_soc"], datos["umbral_reactivacion_soc"]), (30, 40))

    def test_la_respuesta_de_evaluar_trae_el_umbral_aplicado(self):
        self.toma("p", categoria="prescindible")
        self.poner_perfil("equilibrado")  # prescindible: 20 + 20 = 40
        self.soc(39)
        respuesta = self.client.post(self.ruta("/control-energetico/evaluar"), headers=self.auth()).json()
        self.assertEqual([(a["nombre"], a["umbral_aplicado"], a["prioridad"]) for a in respuesta["acciones"]], [("p", 40, 10)])
        # Lo que la API sigue llamando umbrales globales: el preset confort.
        self.assertEqual((respuesta["umbral_soc_bajo"], respuesta["umbral_soc_recuperacion"]), (30, 40))


class Esencial(PruebaApi):
    def test_nunca_se_corta(self):
        self.toma("e", categoria="esencial")
        self.toma("c")
        self.soc(0)
        self.assertEqual(self.evaluar(), [("c", "apagar")])
        self.assertEqual(self.evaluar(), [])

    def test_ni_con_el_perfil_que_mas_protege(self):
        self.toma("e", categoria="esencial")
        self.perfil("conservador", "agm")
        self.soc(0)
        self.assertEqual(self.evaluar(), [])
        self.assertEqual(self.modo(), "normal")

    def test_el_apagado_manual_sigue_permitido_y_no_se_reactiva_sola(self):
        toma = self.toma("e", categoria="esencial")
        self.assertEqual(self.patch_dispositivo(toma, {"estado": "apagado"}).json()["estado"], "apagado")
        self.soc(100)
        self.assertEqual(self.evaluar(), [])
        self.assertEqual(self.recargar(toma).estado, "apagado")

    def test_cortada_antes_de_volverse_esencial_se_reactiva_sin_mirar_el_soc(self):
        toma = self.toma("t")
        self.soc(10)
        self.assertEqual(self.evaluar(), [("t", "apagar")])
        self.patch_dispositivo(toma, {"categoria": "esencial"})
        self.assertEqual(self.evaluar(), [("t", "encender")])
        self.assertEqual(self.evaluar(), [])

    def test_los_bombillos_no_se_tocan(self):
        bombillo = self.bombillo()
        self.patch_dispositivo(bombillo, {"estado": "encendido"})
        self.soc(0)
        self.assertEqual(self.evaluar(), [])
        self.assertEqual(self.recargar(bombillo).estado, "encendido")


class Inactivo(PruebaApi):
    def test_r13_lo_ignora(self):
        toma = self.toma("t", activo=False, desactivado_en=self.ahora())
        self.soc(0)
        self.assertEqual(self.evaluar(), [])
        self.assertEqual(self.recargar(toma).estado, "encendido")
        self.assertEqual(self.modo(), "normal")

    def ahora(self):
        from datetime import datetime, timezone

        return datetime.now(timezone.utc)

    def test_cortada_y_desactivada_no_se_reactiva_hasta_volver_a_activarla(self):
        toma = self.toma("t")
        self.soc(10)
        self.evaluar()
        self.patch_dispositivo(toma, {"activo": False})
        self.assertEqual(self.modo(), "normal")
        self.soc(100)
        self.assertEqual(self.evaluar(), [])
        self.patch_dispositivo(toma, {"activo": True})
        self.assertEqual(self.evaluar(), [("t", "encender")])

    def test_los_listados_lo_ocultan_por_defecto(self):
        activa, inactiva = self.toma("a"), self.toma("b")
        self.patch_dispositivo(inactiva, {"activo": False})

        listado = self.client.get(self.ruta("/dispositivos"), headers=self.auth()).json()
        self.assertEqual([d["id"] for d in listado], [activa.id])

        listado = self.client.get(self.ruta("/dispositivos?incluir_inactivos=true"), headers=self.auth()).json()
        self.assertEqual([d["id"] for d in listado], [activa.id, inactiva.id])

        puntual = self.client.get(self.ruta(f"/dispositivos/{inactiva.id}"), headers=self.auth()).json()
        self.assertFalse(puntual["activo"])

        general = self.client.get(self.ruta("/estado-general"), headers=self.auth()).json()
        self.assertEqual(general["dispositivos_total"], 1)

    def test_no_hay_borrado_por_la_api(self):
        toma = self.toma()
        self.assertEqual(self.client.delete(self.ruta(f"/dispositivos/{toma.id}"), headers=self.auth()).status_code, 405)


class Orden(PruebaApi):
    def test_a_igual_prioridad_por_id(self):
        self.toma("c-1")
        self.toma("p-1", categoria="prescindible")
        self.toma("c-2")
        self.toma("p-2", categoria="prescindible")
        self.toma("i-1", categoria="importante")
        self.soc(10)
        corte = ["p-1", "p-2", "c-1", "c-2", "i-1"]
        self.assertEqual(self.evaluar(), [(nombre, "apagar") for nombre in corte])
        self.soc(90)
        regreso = ["i-1", "c-1", "c-2", "p-1", "p-2"]
        self.assertEqual(self.evaluar(), [(nombre, "encender") for nombre in regreso])

    def test_cuando_hay_cortes_y_reactivaciones_primero_van_los_cortes(self):
        self.toma("baja", categoria="importante")  # 20/30
        self.toma("alta", categoria="prescindible")  # 50/60
        self.soc(5)
        self.evaluar()
        # Encendida sin acción humana: un encendido a mano tendría primacía (DB-4,
        # tests/test_primacia_humana.py) y R-13 no la cortaría con 30 %.
        self.toma("nueva", categoria="prescindible")
        self.soc(30)
        self.assertEqual(self.evaluar(), [("nueva", "apagar"), ("baja", "encender")])


class ModoYAlertas(PruebaApi):
    def test_solo_prescindibles_es_ahorro_y_no_alerta(self):
        self.toma("p", categoria="prescindible")
        self.toma("c")
        self.soc(49)
        self.assertEqual(self.evaluar(), [("p", "apagar")])
        self.assertEqual(self.modo(), "ahorro")
        self.assertEqual(self.alertas_bateria(), [])

    def estado(self):
        return self.client.get(self.ruta("/control-energetico/estado"), headers=self.auth()).json()

    def test_antes_de_evaluar_el_modo_no_anticipa_y_la_toma_va_en_por_cortar(self):
        self.toma("p", categoria="prescindible")
        self.soc(49)
        estado = self.estado()
        self.assertEqual(estado["modo_actual"], "normal")
        self.assertEqual(estado["tomas_apagadas_por_sistema"], [])
        self.assertEqual(estado["ultima_lectura_bateria"]["estado_carga_soc"], 49)
        self.assertEqual(
            [(t["nombre"], t["categoria"], t["umbral_apagado_soc"]) for t in estado["tomas_por_cortar"]],
            [("p", "prescindible", 50)],
        )
        self.evaluar()
        estado = self.estado()
        self.assertEqual(estado["modo_actual"], "ahorro")
        self.assertEqual(estado["tomas_por_cortar"], [])
        self.assertEqual([t["nombre"] for t in estado["tomas_apagadas_por_sistema"]], ["p"])

    def test_ahorro_real_con_un_corte_pendiente(self):
        self.toma("p", categoria="prescindible")
        self.toma("c")
        self.soc(45)
        self.evaluar()
        self.soc(30)
        estado = self.estado()
        self.assertEqual(estado["modo_actual"], "ahorro")
        self.assertEqual([t["nombre"] for t in estado["tomas_por_cortar"]], ["c"])

    def test_por_cortar_excluye_esenciales_inactivas_apagadas_y_sigue_el_orden(self):
        from datetime import datetime, timezone

        self.toma("esencial", categoria="esencial")
        self.toma("inactiva", activo=False, desactivado_en=datetime.now(timezone.utc))
        self.toma("apagada", estado="apagado")
        self.toma("c")
        self.toma("p", categoria="prescindible")
        self.soc(10)
        self.assertEqual([t["nombre"] for t in self.estado()["tomas_por_cortar"]], ["p", "c"])

    def test_el_umbral_de_por_cortar_es_el_calculado(self):
        self.toma("g")
        self.soc(30)
        self.assertEqual(self.estado()["tomas_por_cortar"][0]["umbral_apagado_soc"], 35)

    def test_sin_lecturas_no_hay_por_cortar(self):
        self.toma("p", categoria="prescindible")
        self.assertEqual(self.estado()["tomas_por_cortar"], [])

    def test_sin_tomas_afectadas_es_normal(self):
        self.toma("p", categoria="prescindible")
        self.soc(80)
        self.assertEqual(self.modo(), "normal")

    def test_sin_lecturas_es_normal(self):
        self.toma("p", categoria="prescindible")
        self.assertEqual(self.modo(), "normal")

    def test_cortar_confort_alerta_y_es_supervivencia(self):
        self.toma("p", categoria="prescindible")
        self.toma("c")
        self.soc(30)
        self.assertEqual(self.modo(), "normal")
        self.evaluar()
        self.assertEqual(self.modo(), "supervivencia")
        alertas = self.alertas_bateria()
        self.assertEqual(len(alertas), 1)
        self.assertEqual(alertas[0].estado_alerta, "activa")
        self.assertTrue(alertas[0].creada_por_r13)
        # El límite es el de la toma que motiva la alerta, no el de la prescindible.
        self.assertEqual(alertas[0].valor_limite, 35)
        self.assertEqual(alertas[0].nivel_prioridad, "advertencia")
        self.assertIn("p, c", alertas[0].descripcion)

    def test_cortar_importante_alerta(self):
        self.toma("i", categoria="importante")
        self.soc(10)
        self.evaluar()
        self.assertEqual(self.alertas_bateria()[0].nivel_prioridad, "critico")

    def test_prescindible_ya_cortada_y_luego_confort(self):
        self.toma("p", categoria="prescindible")
        self.toma("c")
        self.soc(45)
        self.evaluar()
        self.assertEqual(self.alertas_bateria(), [])
        self.soc(30)
        self.evaluar()
        self.assertEqual(len(self.alertas_bateria()), 1)

    def test_la_alerta_se_atiende_al_volver_confort_aunque_siga_cortada_una_prescindible(self):
        self.toma("p", categoria="prescindible")
        self.toma("c")
        self.soc(30)
        self.evaluar()
        self.soc(46)
        self.assertEqual(self.evaluar(), [("c", "encender")])
        self.assertEqual(self.alertas_bateria()[0].estado_alerta, "atendida")
        self.assertEqual(self.modo(), "ahorro")

    def test_la_alerta_sigue_activa_mientras_quede_una_confort_cortada(self):
        self.toma("i", categoria="importante")  # 20/30
        self.toma("c")  # 35/45
        self.soc(10)
        self.evaluar()
        self.soc(35)
        self.assertEqual(self.evaluar(), [("i", "encender")])
        self.assertEqual(self.alertas_bateria()[0].estado_alerta, "activa")
        self.soc(55)
        self.assertEqual(self.evaluar(), [("c", "encender")])
        self.assertEqual(self.alertas_bateria()[0].estado_alerta, "atendida")

    def test_reactivar_no_cierra_una_alerta_escrita_a_mano(self):
        self.client.post(
            self.ruta("/alertas"),
            json={"tipo_alerta": "bateria_baja", "descripcion": "R-13 cortó cargas: escrita a mano", "nivel_prioridad": "advertencia"},
            headers=self.auth(),
        )
        self.toma("c")
        self.soc(30)
        self.evaluar()
        self.soc(70)
        self.assertEqual(self.evaluar(), [("c", "encender")])
        # v2: R-13 reconoce las suyas por creada_por_r13, no por el texto.
        alerta = self.alertas_bateria()[0]
        self.assertFalse(alerta.creada_por_r13)
        self.assertEqual(alerta.estado_alerta, "activa")

    def test_toma_cortada_aparece_con_su_categoria(self):
        self.toma("p", categoria="prescindible")
        self.soc(45)
        self.evaluar()
        datos = self.client.get(self.ruta("/control-energetico/estado"), headers=self.auth()).json()
        self.assertEqual(datos["tomas_apagadas_por_sistema"][0]["categoria"], "prescindible")
        self.assertEqual(datos["tomas_apagadas_por_sistema"][0]["prioridad"], 10)
