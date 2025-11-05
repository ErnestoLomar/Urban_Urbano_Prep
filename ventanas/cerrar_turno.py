##########################################
# Autor: Ernesto Lomar
# Fecha de creación: 12/04/2022
# Ultima modificación: 13/08/2022
#
# Script de la ventana turno.
#
##########################################

# Librerías externas
from PyQt5 import uic
from PyQt5.QtCore import *
from PyQt5.QtGui import *
from PyQt5.QtWidgets import *
from queries import obtener_datos_aforo
from chofer import VentanaChofer
import logging
import subprocess

# GPIO hub (BCM)
from gpio_hub import GPIOHub, PINMAP

# Librerías propias
import variables_globales
from variables_globales import VentanaActual

# Instancia única del hub
try:
    HUB = GPIOHub(PINMAP)
except Exception as e:
    print("No se pudo iniciar GPIOHub: " + str(e))
    logging.info(e)

class CerrarTurno(QWidget):
    close_signal = pyqtSignal()

    def __init__(self):
        super().__init__()
        try:
            uic.loadUi("/home/pi/Urban_Urbano/ui/cerrarturno.ui", self)

            # Configuración de la ventana
            self.setGeometry(0, 0, 800, 440)
            self.setWindowFlags(Qt.FramelessWindowHint)
            self.label_cancel.mousePressEvent = self.cancelar
            self.label_fin.mousePressEvent = self.cerrar_turno
            self.label_cambiar_ruta.mousePressEvent = self.cambiar_ruta
            self.label_vigencia_tarjeta.hide()
            self.idUnidad = str(obtener_datos_aforo()[1])
            self.settings = QSettings('/home/pi/Urban_Urbano/ventanas/settings.ini', QSettings.IniFormat)
        except Exception as e:
            print(e)
            logging.info(f"Error al cargar la ventana de turno: {e}")

    # Cancelar turno
    def cancelar(self, event):
        try:
            self.settings.setValue('ventana_actual', "enviar_vuelta")
            self.close()
        except Exception as e:
            print(e)
            logging.info(f"Error al cancelar el turno: {e}")
    
    # Cerrar turno
    def cerrar_turno(self, event):
        try:
            self.close()
            self.close_signal.emit()
            variables_globales.ventana_actual = VentanaActual.CHOFER
            self.settings.setValue('servicio', "")
            self.settings.setValue('ventana_actual', "")
            self.settings.setValue('csn_chofer', "")
            variables_globales.csn_chofer = ""
            variables_globales.numero_de_operador_inicio = ""
            variables_globales.numero_de_operador_final = ""
            variables_globales.nombre_de_operador_inicio = ""
            variables_globales.nombre_de_operador_final = ""
            self.settings.setValue('numero_de_operador_inicio', "")
            self.settings.setValue('numero_de_operador_final', "")
            self.settings.setValue('nombre_de_operador_inicio', "")
            self.settings.setValue('nombre_de_operador_final', "")
            subprocess.run('sudo sh -c "sync; echo 3 > /proc/sys/vm/drop_caches"', shell=True)

            # Apagar ventilador por hub si está mapeado
            if "fan_en" in PINMAP:
                try:
                    HUB.write("fan_en", False)
                except Exception as e:
                    logging.info(f"No se pudo apagar el ventilador: {e}")
        except Exception as e:
            print(e)
            logging.info(f"Error al cerrar el turno: {e}")

    def cambiar_ruta(self, event):
        try:
            self.close()
            self.close_signal.emit()
            self.settings.setValue('ventana_actual', "")
            from abrir_ventanas import AbrirVentanas
            variables_globales.ventana_actual = VentanaActual.CHOFER
            self.registrar_usuario = VentanaChofer(AbrirVentanas.cerrar_vuelta.close_signal, AbrirVentanas.cerrar_vuelta.close_signal_pasaje)
            self.registrar_usuario.show()
            self.settings.setValue('servicio', "")
            variables_globales.numero_de_operador_final = ""
            variables_globales.nombre_de_operador_final = ""
            self.settings.setValue('numero_de_operador_final', "")
            self.settings.setValue('nombre_de_operador_final', "")
            subprocess.run('sudo sh -c "sync; echo 3 > /proc/sys/vm/drop_caches"', shell=True)
        except Exception as e:
            print(e)
            logging.info(f"Error al cambiar la ruta: {e}")

    def cargar_datos(self):
        try:
            print("La vigencia de la tarjeta es: "+str(variables_globales.vigencia_de_tarjeta))
            self.settings.setValue('ventana_actual', "cerrar_turno")
            self.label_head.setText(f"{self.idUnidad} {str(self.settings.value('servicio')[6:])}")
            self.label_vuelta.setText(f"Vuelta {str(self.settings.value('vuelta'))}")
            self.label_total_a_liquidar.setText(self.settings.value('total_a_liquidar'))

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
                print("Error al obtener el nombre del operador: "+str(e))
                logging.info("Error al obtener el nombre del operador: "+str(e))

        except Exception as e:
            print(e)
            logging.info(f"Error al cargar los datos: {e}")