##########################################
# Autor: Ernesto Lomar
# Fecha de creación: 12/04/2022
# Ultima modificación: 24/11/2025
#
# Script de la ventana corte.
#
##########################################

# Librerías externas
from PyQt5 import uic
from PyQt5.QtCore import *
from PyQt5.QtGui import *
from PyQt5.QtWidgets import *
from time import strftime
import logging
import time

# Hub de GPIO (BCM)
from gpio_hub import GPIOHub, PINMAP

# Librerías propias
import variables_globales as variables_globales
from variables_globales import VentanaActual
from enviar_vuelta import EnviarVuelta
from queries import obtener_datos_aforo
from asignaciones_queries import guardar_estado_del_viaje
from ventas_queries import (
    obtener_ultimo_folio_de_item_venta,
    obtener_total_de_ventas_por_folioviaje,
    obtener_total_de_efectivo_por_folioviaje,
    obtener_total_de_aforos_digitales_por_folioviaje,
    obtener_total_saldo_digital_por_folioviaje,
    obtener_ultimo_folio_de_venta_digital
)

# Instancia del hub
try:
    HUB = GPIOHub(PINMAP)
except Exception as e:
    print("No se pudo inicializar GPIOHub: " + str(e))
    logging.info(e)


class corte(QWidget):

    close_signal = pyqtSignal()
    close_signal_pasaje = pyqtSignal()

    def __init__(self, close_signal_para_enviar_vuelta):
        super().__init__()
        try:
            uic.loadUi("/home/pi/Urban_Urbano/ui/corte_copia.ui", self)

            # Variables para el control del corte.
            self.close_signal_vuelta = close_signal_para_enviar_vuelta

            # Configuración de la ventana corte.
            self.setGeometry(0, 0, 800, 440)
            self.setWindowFlags(Qt.FramelessWindowHint)
            self.close_signal_vuelta.connect(self.close_me)
            self.idUnidad = str(obtener_datos_aforo()[1])
            self.settings = QSettings('/home/pi/Urban_Urbano/ventanas/settings.ini', QSettings.IniFormat)
            self.inicializar()
        except Exception as e:
            logging.info(f"Error en la ventana corte: {e}")

    # Función para inicializar la ventana corte.
    def inicializar(self):
        try:
            self.label_fin.mousePressEvent = lambda event: self.terminar_vuelta(event, True)
            self.label_cancel.mousePressEvent = self.cancelar
        except Exception as e:
            logging.info(f"Error en la ventana corte: {e}")

    def cargar_datos(self):
        try:
            self.settings.setValue('ventana_actual', "corte")
            variables_globales.ventana_actual = VentanaActual.CERRAR_VUELTA

            self.label_head.setText(f"{self.idUnidad} {str(self.settings.value('servicio')[6:])}")  # Datos del servicio

            # Folio de viaje
            self.label_folio_viaje.setText(str(self.settings.value('folio_de_viaje') or variables_globales.folio_asignacion))

            # Vuelta
            self.label_vuelta.setText(f"Vuelta {str(self.settings.value('vuelta'))}")

            try:
                # Nombre del operador
                if len(variables_globales.nombre_de_operador_inicio) > 0:
                    self.label_operador.setText("Operador: " + variables_globales.nombre_de_operador_inicio)
                else:
                    if len(self.settings.value('nombre_de_operador_inicio')) > 0:
                        self.label_operador.setText("Operador: " + self.settings.value('nombre_de_operador_inicio'))
                    else:
                        self.label_operador.setText("Operador: ")
                        print("No hay nombre de operador")
            except Exception as e:
                print("Error al obtener el nombre del operador: " + str(e))
                logging.info("Error al obtener el nombre del operador: " + str(e))

            # Versión software
            self.label_version_software.setText(variables_globales.version_del_software)

            try:
                # Últimos folios (efectivo/digital)
                folio_ultimo_efectivo = obtener_ultimo_folio_de_item_venta()
                folio_ultimo_digital = obtener_ultimo_folio_de_venta_digital()

                if folio_ultimo_efectivo is None:
                    folio_ultimo_efectivo = (0, 0)
                if folio_ultimo_digital is None:
                    folio_ultimo_digital = (0, 0)

                self.label_ultimo_folio_efectivo.setText("Ultimo folio venta efectivo: " + str(folio_ultimo_efectivo[1]))
                self.label_ultimo_folio_digital.setText("Ultimo folio venta digital: " + str(folio_ultimo_digital[1]))
            except Exception as e:
                print("Error al obtener los ultimos folios de venta: " + str(e))
                logging.info("Error al obtener los ultimos folios de venta: " + str(e))

            # EFECTIVO
            self.label_cantidad_boletos_estud.setText(f"{str(self.settings.value('info_estudiantes')).split(',')[0]}")
            self.label_total_cobro_estud.setText(f"{str(self.settings.value('info_estudiantes')).split(',')[1]}")

            self.label_cantidad_boletos_normal.setText(f"{str(self.settings.value('info_normales')).split(',')[0]}")
            self.label_total_cobro_normal.setText(f"{str(self.settings.value('info_normales')).split(',')[1]}")

            self.label_cantidad_boletos_ninio.setText(f"{str(self.settings.value('info_chicos')).split(',')[0]}")
            self.label_total_cobro_ninio.setText(f"{str(self.settings.value('info_chicos')).split(',')[1]}")

            self.label_cantidad_boletos_admayor.setText(f"{str(self.settings.value('info_ad_mayores')).split(',')[0]}")
            self.label_total_cobro_admayor.setText(f"{str(self.settings.value('info_ad_mayores')).split(',')[1]}")

            self.label_cantidad_total_boletos_efectivo.setText(f"{str(self.settings.value('total_de_folios_efectivo'))}")
            self.label_total_cobro_efectivo.setText(f"{str(self.settings.value('total_a_liquidar_efectivo'))}")

            # DIGITAL
            self.label_cantidad_boletos_estud_digital.setText(f"{str(self.settings.value('info_estudiantes_digital')).split(',')[0]}")
            self.label_total_cobro_estud_digital.setText(f"{str(self.settings.value('info_estudiantes_digital')).split(',')[1]}")

            self.label_cantidad_boletos_normal_digital.setText(f"{str(self.settings.value('info_normales_digital')).split(',')[0]}")
            self.label_total_cobro_normal_digital.setText(f"{str(self.settings.value('info_normales_digital')).split(',')[1]}")

            self.label_cantidad_boletos_ninio_digital.setText(f"{str(self.settings.value('info_chicos_digital')).split(',')[0]}")
            self.label_total_cobro_ninio_digital.setText(f"{str(self.settings.value('info_chicos_digital')).split(',')[1]}")

            self.label_cantidad_boletos_admayor_digital.setText(f"{str(self.settings.value('info_ad_mayores_digital')).split(',')[0]}")
            self.label_total_cobro_admayor_digital.setText(f"{str(self.settings.value('info_ad_mayores_digital')).split(',')[1]}")

            self.label_cantidad_total_boletos_digital.setText(f"{str(self.settings.value('total_de_folios_digital'))}")
            self.label_total_cobro_digital.setText(f"{str(self.settings.value('total_a_liquidar_digital'))}")

            print("Total a liquidar efectivo: ", self.settings.value('total_a_liquidar_efectivo'))
            print("Total a liquidar digital: ", self.settings.value('total_a_liquidar_digital'))

            total_a_liquidar = int(float(self.settings.value('total_a_liquidar_efectivo'))) + int(float(self.settings.value('total_a_liquidar_digital')))
            print("Total a liquidar: ", total_a_liquidar)
            self.label_total_a_liquidar.setText(f"{total_a_liquidar}")
        except Exception as e:
            print(e)
            logging.info(f"Error en la ventana corte: {e}")

    # Función para cerrar la ventana de corte.
    def terminar_vuelta(self, event, imprimir):
        try:
            print("El imprimir mandado es: ", imprimir)
            self.close()
            try:
                from impresora import imprimir_ticket_de_corte
            except Exception as e:
                print("No se importaron las librerías de impresora")

            hecho = False
            try:
                hecho = imprimir_ticket_de_corte(self.idUnidad, imprimir)
            except Exception as e:
                print(f"No se pudo imprimir el ticket de corte: {e}")

            hora = variables_globales.hora_actual
            fecha = str(variables_globales.fecha_actual).replace('/', '-')
            csn_init = str(self.settings.value('csn_chofer'))
            self.settings.setValue('respaldo_csn_chofer', csn_init)

            # ---------------------------#
            #   Lógica de cierre SIEMPRE
            # ---------------------------#
            total_de_boletos_db = ""
            total_aforo_efectivo = 0
            total_aforo_digital = 0
            total_aforo_digital_saldo = 0

            ultima_venta_bd = obtener_ultimo_folio_de_item_venta()
            print("Última venta en la base de datos es: " + str(ultima_venta_bd))
            logging.info(f"Última venta en la base de datos es: {ultima_venta_bd}")

            folio_viaje = self.settings.value('folio_de_viaje') or variables_globales.folio_asignacion

            if len(str(folio_viaje)) != 0:
                total_de_boletos_db = obtener_total_de_ventas_por_folioviaje(folio_viaje)
                total_aforo_efectivo = obtener_total_de_efectivo_por_folioviaje(folio_viaje)
                total_aforo_digital = obtener_total_de_aforos_digitales_por_folioviaje(folio_viaje)
                total_aforo_digital_saldo = obtener_total_saldo_digital_por_folioviaje(folio_viaje)
            else:
                total_de_boletos_db = []
                total_aforo_efectivo = 0
                total_aforo_digital = 0
                total_aforo_digital_saldo = 0

            print("Total boletos en DB: " + str(len(total_de_boletos_db)))
            print("Total de aforo efectivo: " + str(total_aforo_efectivo))
            print("Total de aforo digital: " + str(total_aforo_digital))
            print("Total de aforo digital saldo: " + str(total_aforo_digital_saldo))
            logging.info(f"Total boletos en DB: {len(total_de_boletos_db)}")
            logging.info(f"Total de aforo efectivo: {total_aforo_efectivo}")
            logging.info(f"Total de aforo digital: {total_aforo_digital}")
            logging.info(f"Total de aforo digital saldo: {total_aforo_digital_saldo}")

            total_de_folio_aforo_efectivo = (
                int(self.settings.value('info_estudiantes').split(',')[0]) +
                int(self.settings.value('info_normales').split(',')[0]) +
                int(self.settings.value('info_chicos').split(',')[0]) +
                int(self.settings.value('info_ad_mayores').split(',')[0])
            )
            print("Total boletos en aforo: " + str(total_de_folio_aforo_efectivo))
            logging.info(f"Total boletos en aforo: {total_de_folio_aforo_efectivo}")

            if ultima_venta_bd is not None:
                print("Último folio de venta en la BD: " + str(ultima_venta_bd[1]))
                logging.info(f"Último folio de venta en la BD: {ultima_venta_bd[1]}")

                if len(total_de_boletos_db) != total_de_folio_aforo_efectivo:
                    print("No coincide el número de boletos en DB con aforo.")
                    logging.info("No coincide el número de boletos en DB con aforo.")
                    total_de_folio_aforo_efectivo = len(total_de_boletos_db)
                    print("Se actualiza aforo a: " + str(total_de_folio_aforo_efectivo))
                    logging.info(f"Se actualiza aforo a: {total_de_folio_aforo_efectivo}")

            csn_final = self.settings.value('csn_chofer_dos') or csn_init

            guardar_estado_del_viaje(
                csn_final,
                f"{self.settings.value('servicio')},{self.settings.value('pension')}",
                fecha,
                hora,
                total_de_folio_aforo_efectivo,
                total_aforo_digital,
                str(int(total_aforo_efectivo)),
                str(folio_viaje),
                total_aforo_digital_saldo
            )

            self.close_signal.emit()
            self.close_signal_pasaje.emit()
            variables_globales.ventana_actual = VentanaActual.CERRAR_TURNO
            variables_globales.folio_asignacion = 0
            #self.settings.setValue('ventana_actual', "cerrar_turno")

            self.settings.setValue('origen_actual', "")
            self.settings.setValue('folio_de_viaje', "")
            self.settings.setValue('pension', "")
            self.settings.setValue('turno', "")
            self.settings.setValue('vuelta', 1)
            self.settings.setValue('info_estudiantes', "0,0.0")
            self.settings.setValue('info_normales', "0,0.0")
            self.settings.setValue('info_chicos', "0,0.0")
            self.settings.setValue('info_ad_mayores', "0,0.0")
            # Reseteo DIGITAL (para evitar datos "fantasma" en el siguiente corte)
            self.settings.setValue('info_estudiantes_digital', "0,0.0")
            self.settings.setValue('info_normales_digital', "0,0.0")
            self.settings.setValue('info_chicos_digital', "0,0.0")
            self.settings.setValue('info_ad_mayores_digital', "0,0.0")
            self.settings.setValue('total_a_liquidar_efectivo', "0.0")
            self.settings.setValue('total_a_liquidar_digital', "0.0")
            self.settings.setValue('total_de_folios_efectivo', 0)
            self.settings.setValue('total_de_folios_digital', 0)
            
            self.settings.setValue('reiniciar_folios', 1)
            self.settings.setValue('total_a_liquidar', "0.0")
            self.settings.setValue('total_de_folios', 0)
            self.settings.setValue('csn_chofer_dos', "")

            self.enviar_vualta = EnviarVuelta(self.close_signal_vuelta)
            self.enviar_vualta.show()

        except Exception as e:
            print(f"Error en la ventana corte: {e}")
            logging.info(f"Error en la ventana corte: {e}")
            # Antes: parpadeo manual en BOARD 12; ahora usamos el hub
            try:
                HUB.buzzer_blinks(5, on_ms=55, off_ms=55)
            except Exception as be:
                logging.info(f"No se pudo accionar buzzer via hub: {be}")
            time.sleep(0.5)

    # Función para cancelar el corte.
    def cancelar(self, event):
        try:
            self.settings.setValue('csn_chofer_dos', "")
            self.settings.setValue('ventana_actual', "servicios_transbordos")
            variables_globales.numero_de_operador_final = ""
            variables_globales.nombre_de_operador_final = ""
            self.settings.setValue('numero_de_operador_final', "")
            self.settings.setValue('nombre_de_operador_final', "")
            self.close()
        except Exception as e:
            logging.info(f"Error en la ventana corte: {e}")

    # Función para cerrar la ventana de corte.
    def close_me(self):
        try:
            self.close()
        except Exception as e:
            logging.info(f"Error en la ventana corte: {e}")