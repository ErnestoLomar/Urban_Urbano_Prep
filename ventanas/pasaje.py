##########################################
# Autor: Ernesto Lomar
# Fecha de creación: 12/04/2022
# Ultima modificación: 12/04/2022
#
# Script de la ventana pasaje.
#
##########################################

#Librerías externas
from unicodedata import decimal
from PyQt5 import uic
from PyQt5.QtWidgets import QApplication, QWidget
from PyQt5.QtCore import QSettings, Qt
import sys
from time import strftime
import logging
import time
import RPi.GPIO as GPIO
import subprocess
#import usb.core

sys.path.insert(1, '/home/pi/Urban_Urbano/db')

#Librerías propias
from ventas_queries import insertar_venta, insertar_item_venta, obtener_ultimo_folio_de_item_venta
from queries import obtener_datos_aforo, insertar_estadisticas_boletera
import variables_globales as vg
from emergentes import VentanaEmergente
from prepago import VentanaPrepago

try:
    GPIO.setmode(GPIO.BOARD)
    GPIO.setup(12, GPIO.OUT)
except Exception as e:
    print("No se pudo inicializar el zumbador: "+str(e))

##############################################################################################
# Clase Pasajero que representa los diferentes tipos de pasajeros que existen en el sistema
# Estudiantes, Niños, Personas normales, Personas Mayores
##############################################################################################
class Pasajero:
    def __init__(self, tipo: str, precio: decimal):

        #Creamos nuestras propiedades de la clase pasajero.
        self.tipo = tipo
        self.precio = precio
        self.total_pasajeros = 0
        self.total_pasajeros_tarjeta = 0

    #Función para obtener el subtotal de los pasajeros.
    def sub_total(self):
        try:
            return self.total_pasajeros * self.precio
        except Exception as e:
            logging.info(e)
            
    def sub_total_tarjeta(self):
        try:
            return self.total_pasajeros_tarjeta * self.precio
        except Exception as e:
            logging.info(e)
            
    def total_precio(self):
        try:
            return (self.total_pasajeros + self.total_pasajeros_tarjeta) * self.precio
        except Exception as e:
            logging.info(e)
            
    def total_pasajeros_total(self):
        try:
            return self.total_pasajeros + self.total_pasajeros_tarjeta
        except Exception as e:
            logging.info(e)

    #Aumentamos en uno el numero de pasajeros
    def aumentar_pasajeros(self):
        try:
            self.total_pasajeros = self.total_pasajeros + 1
        except Exception as e:
            logging.info(e)
            
    def restar_pasajeros(self):
        try:
            self.total_pasajeros = self.total_pasajeros - 1
        except Exception as e:
            logging.info(e)
            
    def aumentar_pasajeros_tarjeta(self):
        try:
            self.total_pasajeros_tarjeta = self.total_pasajeros_tarjeta + 1
        except Exception as e:
            logging.info(e)
            
    def restar_pasajeros_tarjeta(self):
        try:
            self.total_pasajeros_tarjeta = self.total_pasajeros_tarjeta - 1
        except Exception as e:
            logging.info(e)


class VentanaPasaje(QWidget):
    def __init__(self, precio, de: str, hacia: str, precio_preferente, close_signal, servicio_o_transbordo: str, id_tabla, ruta, tramo, cerrar_ventana_servicios):
        super().__init__()
        try:
            uic.loadUi("/home/pi/Urban_Urbano/ui/pasaje.ui", self)

            #Creamos nuestras variables para la ventana pasaje.
            self.origen = de
            self.destino = hacia
            self.close_signal = close_signal
            self.cerrar_servicios = cerrar_ventana_servicios
            self.precio = precio
            self.precio_preferente = precio_preferente
            self.personas_normales = Pasajero("personas_normales", self.precio)
            self.estudiantes = Pasajero("estudiantes", self.precio_preferente)
            self.personas_mayores = Pasajero("personas_mayores", self.precio_preferente)
            self.chicos = Pasajero("chicos", self.precio_preferente)
            self.servicio_o_transbordo = servicio_o_transbordo.split(',')
            self.id_tabla = id_tabla
            self.ruta = ruta
            self.tramo = tramo
            #vg.vendiendo_boleto = True

            #Realizamos configuraciones de la ventana pasaje.
            self.close_signal.connect(self.close_me)
            self.inicializar_labels()
            self.label_de.setText("De: " + str(de.split("_")[0]))
            self.label_hacia.setText("A: "+ str(hacia.split("_")[0]))
            #self.label_8.setText('$'+str(precio))
            self.label_precio_normal.setText('P.N: $'+str(precio))
            self.label_precio_preferente.setText('P.P: $'+str(precio_preferente))
            self.settings = QSettings('/home/pi/Urban_Urbano/ventanas/settings.ini', QSettings.IniFormat)
            self.Unidad = str(obtener_datos_aforo()[1])
            vg.modo_nfcCard = False
        except Exception as e:
            logging.info(e)
    
    #Función para cerrar la ventana pasaje.
    def close_me(self):
        try:
            vg.vendiendo_boleto = False
            self.close()
        except Exception as e:
            logging.info(e)

    #Inicializamos las señales de los labels al darles click
    def inicializar_labels(self):
        try:
            self.label_volver.mousePressEvent = self.handle_volver
            self.label_volver2.mousePressEvent = self.handle_volver
            self.btn_nuevo_menor_efectivo.mousePressEvent = self.handle_ninos
            self.btn_nuevo_menor_tarjeta.mousePressEvent = self.handle_ninos_tarjeta
            self.btn_nuevo_estudiante_efectivo.mousePressEvent = self.handle_estudiantes
            self.btn_nuevo_estudiante_tarjeta.mousePressEvent = self.handle_estudiantes_tarjeta
            self.btn_nuevo_adulto_efectivo.mousePressEvent = self.handle_mayores_edad
            self.btn_nuevo_adulto_tarjeta.mousePressEvent = self.handle_mayores_edad_tarjeta
            self.btn_nuevo_normal_efectivo.mousePressEvent = self.handle_personas_normales
            self.btn_nuevo_normal_tarjeta.mousePressEvent = self.handle_personas_normales_tarjeta
            self.btn_pagar.mousePressEvent = self.handle_pagar
        except Exception as e:
            logging.info(e)

    #Función para volver a la ventana pasada.
    def handle_volver(self, event):
        try:
            vg.modo_nfcCard = True
            vg.vendiendo_boleto = False
            self.close()
        except Exception as e:
            logging.info(e)

    #Función para manejar el evento de darle click al label de los niños
    def handle_ninos(self, event):
        try:
            self.chicos.aumentar_pasajeros()
            self.label_ninos_total.setText(str(self.chicos.total_pasajeros))
            self.label_ninos_total_precio.setText("$ "+str(int(self.chicos.total_precio())))
            self.calcularTotal()
        except Exception as e:
            logging.info(e)
    
    def handle_ninos_tarjeta(self, event):
        try:
            self.chicos.aumentar_pasajeros_tarjeta()
            self.label_ninos_total_tarjeta.setText(str(self.chicos.total_pasajeros_tarjeta))
            self.label_ninos_total_precio.setText("$ "+str(int(self.chicos.total_precio())))
            self.calcularTotal()
        except Exception as e:
            logging.info(e)

    #Función para manejar el evento de darle click al label de los estudiantes
    def handle_estudiantes(self, event):
        try:
            self.estudiantes.aumentar_pasajeros()
            self.label_estudiantes_total.setText(str(self.estudiantes.total_pasajeros))
            self.label_estudiantes_total_precio.setText("$ "+str(int(self.estudiantes.total_precio())))
            self.calcularTotal()
        except Exception as e:
            logging.info(e)
            
    def handle_estudiantes_tarjeta(self, event):
        try:
            self.estudiantes.aumentar_pasajeros_tarjeta()
            self.label_estudiantes_total_tarjeta.setText(str(self.estudiantes.total_pasajeros_tarjeta))
            self.label_estudiantes_total_precio.setText("$ "+str(int(self.estudiantes.total_precio())))
            self.calcularTotal()
        except Exception as e:
            logging.info(e)

    #Función para manejar el evento de darle click al label de las personas mayores
    def handle_mayores_edad(self, event):
        try:
            self.personas_mayores.aumentar_pasajeros()
            self.label_mayores_total.setText(str(self.personas_mayores.total_pasajeros))
            self.label_mayores_total_precio.setText("$ "+str(int(self.personas_mayores.total_precio())))
            self.calcularTotal()
        except Exception as e:
            logging.info(e)
    
    def handle_mayores_edad_tarjeta(self, event):
        try:
            self.personas_mayores.aumentar_pasajeros_tarjeta()
            self.label_mayores_total_tarjeta.setText(str(self.personas_mayores.total_pasajeros_tarjeta))
            self.label_mayores_total_precio.setText("$ "+str(int(self.personas_mayores.total_precio())))
            self.calcularTotal()
        except Exception as e:
            logging.info(e)

    #Función para manejar el evento de darle click al label de las personas normales
    def handle_personas_normales(self, event):
        try:
            self.personas_normales.aumentar_pasajeros()
            self.label_normales_total.setText(str(self.personas_normales.total_pasajeros))
            self.label_normales_total_precio.setText("$ "+str(int(self.personas_normales.total_precio())))
            self.calcularTotal()
        except Exception as e:
            logging.info(e)
            
    def handle_personas_normales_tarjeta(self, event):
        try:
            self.personas_normales.aumentar_pasajeros_tarjeta()
            self.label_normales_total_tarjeta.setText(str(self.personas_normales.total_pasajeros_tarjeta))
            self.label_normales_total_precio.setText("$ "+str(int(self.personas_normales.total_precio())))
            self.calcularTotal()
        except Exception as e:
            logging.info(e)
            
    def sonar_zumbador(repeticiones=5, duracion=0.055):
        for _ in range(repeticiones):
            GPIO.output(12, True)
            time.sleep(duracion)
            GPIO.output(12, False)
            time.sleep(duracion)

    #Función para manejar el evento de darle click al label pagar
    def handle_pagar(self, event):
        try:
            if self.calcularTotal() == 0:
                return

            self.close_me()

            if len(vg.folio_asignacion) <= 1:
                self.ve = VentanaEmergente("VOID", "No existe viaje", 4.5)
                self.ve.show()
                for _ in range(5):
                    GPIO.output(12, True)
                    time.sleep(0.055)
                    GPIO.output(12, False)
                    time.sleep(0.055)
                self.cerrar_servicios.emit()
                return

            try:
                from impresora import imprimir_boleto_normal_pasaje, imprimir_boleto_con_qr_pasaje
            except Exception:
                print("No se importaron las librerías de impresora")

            pasajeros = [
                ('ESTUDIANTE', self.estudiantes, 1, 'info_estudiantes', self.estudiantes.precio),
                ('NORMAL', self.personas_normales, 2, 'info_normales', self.personas_normales.precio),
                ('MENOR', self.chicos, 3, 'info_chicos', self.chicos.precio),
                ('MAYOR', self.personas_mayores, 4, 'info_ad_mayores', self.personas_mayores.precio)
            ]

            fecha = strftime('%d-%m-%Y')
            fecha_estadistica = strftime('%y%m%d')
            hora_estadistica = str(subprocess.run("date", stdout=subprocess.PIPE, shell=True).stdout.decode())
            hora_estadistica = ''.join(hora_estadistica.split()[3].split(':')[:2])  # Ej: "1543"

            def imprimir_y_guardar(tipo, data, tipo_num, setting_key, servicio, pasajeros = None):
                if pasajeros is None:
                    total_pasajeros = data.total_pasajeros
                else:
                    total_pasajeros = pasajeros
                    
                for _ in range(total_pasajeros):
                    folio = (obtener_ultimo_folio_de_item_venta() or (None, 0))[1] + 1
                    hora = strftime("%H:%M:%S")
                    
                    # intentar imprimir el boleto
                    hecho = False
                    if servicio == "SER":
                        hecho = imprimir_boleto_normal_pasaje(
                            str(folio), fecha, hora, str(self.Unidad),
                            tipo, str(data.precio), str(self.ruta), str(self.tramo)
                        )
                    elif servicio == "TRA":
                        hecho = imprimir_boleto_con_qr_pasaje(
                            str(folio), fecha, hora, str(self.Unidad),
                            tipo, str(data.precio), str(self.ruta), str(self.tramo),
                            self.servicio_o_transbordo
                        )
                    
                    # Bloque de inserción
                    insertado = False
                    if servicio == "SER":
                        insertado = insertar_item_venta(
                            folio, str(self.settings.value('folio_de_viaje')), fecha, hora,
                            int(self.id_tabla), int(str(self.settings.value('geocerca')).split(",")[0]),
                            tipo_num, "n", "preferente" if tipo != "NORMAL" else "normal",
                            tipo.lower(), data.precio
                        )
                    elif servicio == "TRA":
                        insertado = insertar_item_venta(
                            folio, str(self.settings.value('folio_de_viaje')), fecha, hora,
                            int(self.id_tabla), int(str(self.settings.value('geocerca')).split(",")[0]),
                            tipo_num, "t", "preferente" if tipo != "NORMAL" else "normal",
                            tipo.lower(), data.precio
                        )

                    # Verificar si se insertó
                    if not insertado:
                        logging.error(f"Error al registrar venta en DB (folio {folio})")
                        self.sonar_zumbador()
                    
                    # Actualizamos los contadores en settings.ini
                    total, subtotal = map(float, self.settings.value(setting_key, "0,0").split(','))
                    self.settings.setValue(setting_key, f"{int(total+1)},{subtotal+data.precio}")
                    self.settings.setValue('total_a_liquidar', str(float(self.settings.value('total_a_liquidar')) + data.precio))
                    self.settings.setValue('total_de_folios', str(int(self.settings.value('total_de_folios')) + 1))
                    self.settings.setValue('total_a_liquidar_efectivo', str(float(self.settings.value('total_a_liquidar_efectivo')) + data.precio))
                    self.settings.setValue('total_de_folios_efectivo', str(int(self.settings.value('total_de_folios_efectivo')) + 1))
                    logging.info(f"Boleto {tipo.lower()} impreso")
                    if hecho:
                        logging.info(f"Boleto {tipo.lower()} impreso")
                    else:
                        insertar_estadisticas_boletera(str(self.Unidad), fecha_estadistica, hora_estadistica, "BMI", f"S{tipo[0]}")
                        logging.info("Error al imprimir boleto")
                        self.ve = VentanaEmergente("IMPRESORA", "", 4.5)
                        self.ve.show()
                        # for _ in range(5):
                        #     GPIO.output(12, True)
                        #     time.sleep(0.055)
                        #     GPIO.output(12, False)
                        #     time.sleep(0.055)

            servicio = self.servicio_o_transbordo[0]
            
            if servicio in ['SER', 'TRA']:
                # Procesar primero todos los pasajeros normales
                for tipo, datos, tipo_num, setting, precio in pasajeros:
                    if datos.total_pasajeros > 0:
                        imprimir_y_guardar(tipo, datos, tipo_num, setting, servicio)
                
                # Luego procesar los que pagaron con tarjeta
                for tipo, datos, tipo_num, setting, precio in pasajeros:
                    total_hce = datos.total_pasajeros_tarjeta
                    if total_hce > 0:
                        if servicio == "SER":
                            ventana = VentanaPrepago(tipo=tipo, tipo_num=tipo_num, setting=setting, total_hce=total_hce, precio=precio, id_tarifa=self.id_tabla, geocerca=int(str(self.settings.value('geocerca')).split(",")[0]), servicio="n", origen=self.origen, destino=self.destino)
                            ventana.setGeometry(0, 0, 800, 480)
                            ventana.setWindowFlags(Qt.FramelessWindowHint)
                            respuesta_ventana_prepago = ventana.mostrar_y_esperar()
                            print("Exito pago es: ", respuesta_ventana_prepago['hecho'])
                            if not respuesta_ventana_prepago['hecho']:
                                if respuesta_ventana_prepago['pagado_efectivo']:
                                    print("Se pagara ahora con dinero")
                                    imprimir_y_guardar(tipo, datos, tipo_num, setting, servicio, 1)
                                else:
                                    print(f"Error en el cobro de {tipo}. Cancelando procesamiento.")
                                    break
                            print(f"Exito en el cobro de {tipo}.")
                        elif servicio == "TRA":
                            ventana = VentanaPrepago(tipo=tipo, tipo_num=tipo_num, setting=setting, total_hce=total_hce, precio=precio, id_tarifa=self.id_tabla, geocerca=int(str(self.settings.value('geocerca')).split(",")[0]), servicio="t")
                            ventana.setGeometry(0, 0, 800, 480)
                            ventana.setWindowFlags(Qt.FramelessWindowHint)
                            respuesta_ventana_prepago = ventana.mostrar_y_esperar()
                            print("Exito pago es: ", respuesta_ventana_prepago['hecho'])
                            if not respuesta_ventana_prepago['hecho']:
                                if respuesta_ventana_prepago['pagado_efectivo']:
                                    imprimir_y_guardar(tipo, datos, tipo_num, setting, servicio, 1)
                                    print("Se pagara ahora con dinero")
                                else:
                                    print(f"Error en el cobro de {tipo}. Cancelando procesamiento.")
                                    break
            
            vg.modo_nfcCard = True

        except Exception as e:
            print("Error en handle_pagar: ", e)
            logging.error(f"Error en handle_pagar: {e}")

    #Función para calcular el total de todos los pasajeros
    def calcularTotal(self):
        try:
            totalPersonas = self.chicos.total_pasajeros_total() + self.estudiantes.total_pasajeros_total() + self.personas_mayores.total_pasajeros_total() + self.personas_normales.total_pasajeros_total()
            totalPrecio = self.chicos.total_precio() + self.estudiantes.total_precio() + self.personas_mayores.total_precio() + self.personas_normales.total_precio()
            total_precios_efectivo = self.chicos.sub_total() + self.estudiantes.sub_total() + self.personas_mayores.sub_total() + self.personas_normales.sub_total()
            total_precios_tarjeta = self.chicos.sub_total_tarjeta() + self.estudiantes.sub_total_tarjeta() + self.personas_mayores.sub_total_tarjeta() + self.personas_normales.sub_total_tarjeta()
            self.label_personas_total.setText("Pasajes: "+str(totalPersonas))
            self.label_total_precio.setText("Total: $ "+str(int(totalPrecio)))
            self.label_total_precio_efectivo.setText("Efectivo: $ "+str(int(total_precios_efectivo)))
            self.label_total_precio_tarjeta.setText("Digital: $ "+str(int(total_precios_tarjeta)))
            return totalPrecio
        except Exception as e:
            logging.info(e)

if __name__ == '__main__':
    app = QApplication(sys.argv)
    GUI = VentanaPasaje(10, "calle #33", "calle #45")
    GUI.show()
    sys.exit(app.exec())