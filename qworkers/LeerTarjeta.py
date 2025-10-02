##########################################
# Autor: Ernesto Lomar
# Fecha de creación: 12/04/2022
# Ultima modificación: 16/08/2022
#
# Script para la comunicación con la tarjeta.
#
##########################################

#Librerías externas
from PyQt5.QtCore import QObject, pyqtSignal, QSettings
import time
import ctypes
import RPi.GPIO as GPIO
import serial
import logging
from time import strftime
from datetime import datetime, timedelta
import subprocess

#Librerias propias
from matrices_tarifarias import obtener_destino_de_servicios_directos, obtener_destino_de_transbordos
from emergentes import VentanaEmergente
from ventas_queries import insertar_item_venta, obtener_ultimo_folio_de_item_venta
from queries import obtener_datos_aforo, insertar_estadisticas_boletera
from tickets_usados import insertar_ticket_usado, verificar_ticket_completo, verificar_ticket
import variables_globales as vg

class LeerTarjetaWorker(QObject):
    
    try:
        finished = pyqtSignal()
        progress = pyqtSignal(str)
    except Exception as e:
        print(e)
        logging.info(e)

    try:
        GPIO.setmode(GPIO.BOARD)
        GPIO.setup(12, GPIO.OUT)
    except Exception as e:
        print("\x1b[1;31;47m"+"No se pudo inicializar el zumbador: "+str(e)+'\033[0;m')
        logging.info(e)
        
    def __init__(self):
        super().__init__()
        self.ultimo_qr = ""
        self.ser = any
        try:
            self.ser = serial.Serial(port='/dev/ttyACM0',baudrate = 115200,timeout=0.5)
        except Exception as e:
            print(e)
            logging.info(e)
            self.restablecer_comunicación_QR()
        try:
            self.lib = ctypes.cdll.LoadLibrary('/home/pi/Urban_Urbano/qworkers/libernesto.so')

            # las funciones devuelven punteros que luego liberamos con free_str
            self.lib.ev2IsPresent.restype = ctypes.c_void_p
            self.lib.tipoTiscEV2.restype  = ctypes.c_void_p
            self.lib.obtenerVigencia.restype = ctypes.c_void_p

            # free_str(ptr) libera la cadena asignada por la librería
            self.lib.free_str.argtypes = [ctypes.c_void_p]
            self.lib.free_str.restype  = None
            self.lib.nfc_close_all.restype = None
        except Exception as e:
            print(e)
            logging.info(e)

        try:
            self.settings = QSettings('/home/pi/Urban_Urbano/ventanas/settings.ini', QSettings.IniFormat)
            self.idUnidad = str(obtener_datos_aforo()[1])
        except Exception as e:
            print(e)
            logging.info(e)

    def _cstr(self, ptr):
        #print("ptr: ",ptr)
        if not ptr:
            return ""
        try:
            return ctypes.string_at(ptr).decode("utf-8", "ignore")
        finally:
            self.lib.free_str(ptr)
            
    def restar_dos_horas(self, hora_1, hora_2):
        try:
            t1 = datetime.strptime(hora_1, '%H:%M:%S')
            t2 = datetime.strptime(hora_2, '%H:%M:%S')
            return t1 - t2
        except Exception as e:
            print("recorrido_mapa.py, linea 151: "+str(e))
            
    def sumar_dos_horas(self, hora1, hora2):
        try:
            formato = "%H:%M:%S"
            lista = hora2.split(":")
            hora=int(lista[0])
            minuto=int(lista[1])
            segundo=int(lista[2])
            h1 = datetime.strptime(hora1, formato)
            dh = timedelta(hours=hora) 
            dm = timedelta(minutes=minuto)          
            ds = timedelta(seconds=segundo) 
            resultado1 =h1 + ds
            resultado2 = resultado1 + dm
            resultado = resultado2 + dh
            resultado=resultado.strftime(formato)
            return str(resultado)
        except Exception as e:
            print("recorrido_mapa.py, linea 151: "+str(e))

    def run(self):
        try:
            next_poll = 0.0
            poll_interval = 0.10  # 100 ms ≈ 10 Hz
            while True:
                now = time.monotonic()
                if now < next_poll:
                    time.sleep(0.01)
                    continue
                next_poll = now + poll_interval

                try:
                    if vg.modo_nfcCard:
                        csn = self._cstr(self.lib.ev2IsPresent())

                        # Backoff si no hay tag o UID no esperado
                        if not csn:
                            next_poll = max(next_poll, time.monotonic() + 0.12)
                            continue
                        if len(csn) not in (8, 14, 20):  # 4/7/10 bytes UID en hex
                            next_poll = max(next_poll, time.monotonic() + 0.12)
                            continue
                        if len(csn) != 14:  # si tu app exige 7 bytes UID (14 hex)
                            next_poll = max(next_poll, time.monotonic() + 0.12)
                            continue

                        # Fecha y hora de la boletera
                        fecha = strftime('%Y/%m/%d').replace('/', '')[2:]
                        fecha_actual = str(subprocess.run("date", stdout=subprocess.PIPE, shell=True))
                        indice = fecha_actual.find(":")
                        hora = str(fecha_actual[(int(indice) - 2):(int(indice) + 6)]).replace(":", "")

                        try:
                            tipo = self._cstr(self.lib.tipoTiscEV2())[0:2]
                            if tipo == "KI":
                                datos_completos_tarjeta = self._cstr(self.lib.obtenerVigencia())
                                print("Datos completos de la tarjeta: ", datos_completos_tarjeta)
                                vigenciaTarjeta = datos_completos_tarjeta[:12]
                                print("Vigencia completa de la tarjeta: " + vigenciaTarjeta)

                                # Validación de vigencia
                                if len(vigenciaTarjeta) == 12 and int(vigenciaTarjeta[:2]) >= 22:
                                    now_dt = datetime.now()
                                    vigenciaActual = f'{str(now_dt.strftime("%Y-%m-%d %H:%M:%S"))[2:].replace(" ","").replace("-","").replace(":","")}'
                                    print("Fecha actual: " + vigenciaActual)
                                    print("Fecha vigencia tarjeta: " + vigenciaTarjeta)
                                    if vigenciaActual <= vigenciaTarjeta:
                                        print("Tarjeta vigente")
                                        # NO vuelvas a llamar ev2IsPresent; usa csn ya leído
                                        if len(csn) == 14:
                                            vg.vigencia_de_tarjeta = vigenciaTarjeta
                                            print("La ventana actual es: ", self.settings.value('ventana_actual'))
                                            if str(self.settings.value('ventana_actual')) not in ("chofer", "corte", "enviar_vuelta", "cerrar_turno"):
                                                if len(vg.numero_de_operador_inicio) > 0 or len(self.settings.value('numero_de_operador_inicio')) > 0:
                                                    vg.numero_de_operador_final = datos_completos_tarjeta[12:17]
                                                    vg.nombre_de_operador_final = datos_completos_tarjeta[17:41].replace("*", " ").replace(".", " ").replace("-", " ").replace("_", " ")
                                                    self.settings.setValue('numero_de_operador_final', f"{datos_completos_tarjeta[12:17]}")
                                                    self.settings.setValue('nombre_de_operador_final', f"{datos_completos_tarjeta[17:41].replace('*',' ').replace('.',' ').replace('-',' ').replace('_',' ')}")
                                                    print("Numero de operador de final es: " + vg.numero_de_operador_final)
                                                    print("El nombre del operador de final es: ", vg.nombre_de_operador_final)
                                                else:
                                                    vg.numero_de_operador_inicio = datos_completos_tarjeta[12:17]
                                                    vg.nombre_de_operador_inicio = datos_completos_tarjeta[17:41].replace("*", " ").replace(".", " ").replace("-", " ").replace("_", " ")
                                                    self.settings.setValue('numero_de_operador_inicio', f"{datos_completos_tarjeta[12:17]}")
                                                    self.settings.setValue('nombre_de_operador_inicio', f"{datos_completos_tarjeta[17:41].replace('*',' ').replace('.',' ').replace('-',' ').replace('_',' ')}")
                                                    print("Numero de operador de inicio es: " + vg.numero_de_operador_inicio)
                                                    print("El nombre del operador de inicio es: ", vg.nombre_de_operador_inicio)
                                            vg.csn_chofer_respaldo = csn
                                            self.progress.emit(csn)
                                            GPIO.output(12, True);  time.sleep(0.1)
                                            GPIO.output(12, False); time.sleep(0.1)
                                            GPIO.output(12, True);  time.sleep(0.1)
                                            GPIO.output(12, False)
                                        else:
                                            GUI = VentanaEmergente("TARJETAINVALIDA", "")
                                            GUI.show()
                                            for i in range(5):
                                                GPIO.output(12, True);  time.sleep(0.055)
                                                GPIO.output(12, False); time.sleep(0.055)
                                            time.sleep(2)
                                            GUI.close()
                                    else:
                                        insertar_estadisticas_boletera(str(self.idUnidad), fecha, hora, "SV", f"{csn}")
                                        GUI = VentanaEmergente("FUERADEVIGENCIA", "")
                                        GUI.show()
                                        for i in range(5):
                                            GPIO.output(12, True);  time.sleep(0.055)
                                            GPIO.output(12, False); time.sleep(0.055)
                                        time.sleep(2)
                                        GUI.close()
                                else:
                                    insertar_estadisticas_boletera(str(self.idUnidad), fecha, hora, "TI", f"{csn},{vigenciaTarjeta}")
                                    GUI = VentanaEmergente("TARJETAINVALIDA", "")
                                    GUI.show()
                                    for i in range(5):
                                        GPIO.output(12, True);  time.sleep(0.055)
                                        GPIO.output(12, False); time.sleep(0.055)
                                    time.sleep(2)
                                    GUI.close()
                            else:
                                insertar_estadisticas_boletera(str(self.idUnidad), fecha, hora, "TD", f"{csn},{tipo}")
                                GUI = VentanaEmergente("TARJETAINVALIDA", "")
                                GUI.show()
                                for i in range(5):
                                    GPIO.output(12, True);  time.sleep(0.055)
                                    GPIO.output(12, False); time.sleep(0.055)
                                time.sleep(2)
                                GUI.close()
                        except Exception as e:
                            print("\x1b[1;31;47mNo se pudo leer la tarjeta:", str(e), '\033[0;m')
                            continue
                    #else:
                        #print("\x1b[1;33m"+"Se esta en modo HCE")
                    if self.ser.isOpen():
                        try:
                            # Leer QR del puerto
                            try:
                                qr_bytes = self.ser.readline()
                            except Exception as e:
                                logging.info(e)
                                self.restablecer_comunicación_QR()
                                qr_bytes = b""

                            qr_str = qr_bytes.decode(errors="ignore").strip()
                            if not qr_str:
                                continue  # nada que procesar

                            # Debe existir viaje activo
                            if str(self.settings.value('folio_de_viaje')) == "":
                                print("No hay ningún viaje activo")
                                for i in range(5):
                                    GPIO.output(12, True); time.sleep(0.055)
                                    GPIO.output(12, False); time.sleep(0.055)
                                time.sleep(1)
                                continue

                            # Evitar repetir el último QR
                            if qr_str == getattr(self, "ultimo_qr", ""):
                                print("El ultimo QR se vuelve a pasar")
                                GUI = VentanaEmergente("UTILIZADO", ".....")
                                GUI.show()
                                for i in range(5):
                                    GPIO.output(12, True); time.sleep(0.055)
                                    GPIO.output(12, False); time.sleep(0.055)
                                time.sleep(4.5)
                                GUI.close()
                                continue

                            # Parseo básico
                            print("El QR es: " + qr_str)
                            qr_list = [p.strip() for p in qr_str.split(",")]
                            print("El tamaño del QR es: " + str(len(qr_list)))

                            # -------------------------- NUEVO FORMATO: VENTA DIGITAL "PD,..." --------------------------
                            if len(qr_list) >= 1 and qr_list[0] == "PD":
                                if len(qr_list) != 12:
                                    print("El QR digital no es válido")
                                    GUI = VentanaEmergente("INVALIDO", "")
                                    GUI.show()
                                    for i in range(5):
                                        GPIO.output(12, True); time.sleep(0.055)
                                        GPIO.output(12, False); time.sleep(0.055)
                                    time.sleep(4.5)
                                    GUI.close()
                                    continue

                                # Campos: PD,unidad,fecha,hora,id_tarifa,origen,**destino**,tipo_pasajero,servicio,id_monedero,saldo_posterior,precio
                                _, unidad_qr, fecha_qr, hora_qr, id_tarifa, origen, destino, tipo_de_pasajero, servicio_qr, id_monedero, saldo_posterior, precio = qr_list

                                # Validación de fecha
                                fecha_hoy = strftime('%d-%m-%Y').replace('/', '-')
                                if fecha_hoy != fecha_qr:
                                    print("La fecha del QR no es la actual")
                                    GUI = VentanaEmergente("CADUCO", "Fecha diferente")
                                    GUI.show()
                                    for i in range(5):
                                        GPIO.output(12, True); time.sleep(0.055)
                                        GPIO.output(12, False); time.sleep(0.055)
                                    time.sleep(4.5)
                                    GUI.close()
                                    continue

                                # Validación de geocerca: origen debe coincidir con la geocerca actual
                                en_geocerca = False
                                try:
                                    geo_actual = str(str(vg.geocerca.split(",")[1]).split("_")[0])
                                    origen_norm = str(origen).split("_")[0]
                                    if origen_norm == geo_actual:
                                        en_geocerca = True
                                except Exception as e:
                                    print(e)
                                    logging.info(e)

                                if not en_geocerca:
                                    print("No se encuentra en la geocerca declarada en el QR")
                                    GUI = VentanaEmergente("EQUIVOCADO", str(origen))
                                    GUI.show()
                                    for i in range(5):
                                        GPIO.output(12, True); time.sleep(0.055)
                                        GPIO.output(12, False); time.sleep(0.055)
                                    time.sleep(4.5)
                                    GUI.close()
                                    continue

                                # Verificar reutilización
                                es_ticket_usado = verificar_ticket_completo(qr_str)
                                if es_ticket_usado is not None:
                                    print("El QR ya fue usado")
                                    GUI = VentanaEmergente("UTILIZADO", ".....")
                                    GUI.show()
                                    for i in range(5):
                                        GPIO.output(12, True); time.sleep(0.055)
                                        GPIO.output(12, False); time.sleep(0.055)
                                    time.sleep(4.5)
                                    GUI.close()
                                    continue

                                # Resolver servicio si no viene en el QR
                                servicio = servicio_qr
                                if not servicio:
                                    try:
                                        # Buscar en servicios directos
                                        for servicio_vg in vg.todos_los_servicios_activos:
                                            if str(destino) in str(servicio_vg[2]):
                                                servicio = str(servicio_vg[5]) + "-" + str(str(servicio_vg[1]).split("_")[0]) + "-" + str(str(servicio_vg[2]).split("_")[0])
                                                break
                                        # Si no se halló, buscar en transbordos
                                        if not servicio:
                                            for transbordo in vg.todos_los_transbordos_activos:
                                                if str(destino) in str(transbordo[2]):
                                                    servicio = str(transbordo[5]) + "-" + str(str(transbordo[1]).split("_")[0]) + "-" + str(str(transbordo[2]).split("_")[0])
                                                    break
                                    except Exception as e:
                                        logging.info(e)

                                usted_se_dirige = str(servicio).split("-")[2] if servicio else ""

                                # --- Folio y guardado en DB ---
                                try:
                                    ultimo = obtener_ultimo_folio_de_venta_digital() or (None, 0)
                                    folio_venta_digital = (ultimo[1] if isinstance(ultimo, (list, tuple)) and len(ultimo) > 1 else 0) + 1
                                    logging.info(f"Folio de venta digital asignado: {folio_venta_digital}")
                                except Exception as e:
                                    logging.info(e)
                                    folio_venta_digital = 1

                                try:
                                    folio_asignacion = str(self.settings.value('folio_de_viaje'))
                                    geocerca_id = int(str(self.settings.value('geocerca')).split(",")[0])

                                    venta_guardada = guardar_venta_digital(
                                        folio_venta_digital,
                                        folio_asignacion,
                                        fecha_qr,
                                        hora_qr,
                                        id_tarifa,
                                        geocerca_id,
                                        tipo_de_pasajero,
                                        servicio,
                                        "q",
                                        id_monedero,
                                        saldo_posterior,
                                        precio
                                    )
                                except Exception as e:
                                    logging.info(e)
                                    venta_guardada = None

                                if venta_guardada:
                                    # Marcar como usado, actualizar estado y notificar
                                    try:
                                        insertar_ticket_usado(qr_str)
                                    except Exception as e:
                                        logging.info(e)
                                    try:
                                        self.ultimo_qr = qr_str
                                        self.settings.setValue('total_de_folios', f"{int(self.settings.value('total_de_folios')) + 1}")
                                    except Exception as e:
                                        logging.info(e)

                                    GUI = VentanaEmergente("ACEPTADO", usted_se_dirige if usted_se_dirige else "No encontrado")
                                    GUI.show()
                                    time.sleep(5)
                                    GUI.close()
                                else:
                                    for i in range(5):
                                        GPIO.output(12, True); time.sleep(0.055)
                                        GPIO.output(12, False); time.sleep(0.055)
                                    time.sleep(0.5)
                                continue  # fin de flujo PD

                            # -------------------------- FORMATO ANTERIOR (9/10 CAMPOS) --------------------------
                            if len(qr_list) not in (9, 10):
                                print("El QR no es válido")
                                GUI = VentanaEmergente("INVALIDO", "")
                                GUI.show()
                                for i in range(5):
                                    GPIO.output(12, True); time.sleep(0.055)
                                    GPIO.output(12, False); time.sleep(0.055)
                                time.sleep(4.5)
                                GUI.close()
                                continue

                            # Validación de fecha y hora
                            fecha_qr = qr_list[0]
                            fecha_hoy = strftime('%d-%m-%Y').replace('/', '-')
                            if fecha_hoy != fecha_qr:
                                print("La fecha del QR no es la actual")
                                GUI = VentanaEmergente("CADUCO", "Fecha diferente")
                                GUI.show()
                                for i in range(5):
                                    GPIO.output(12, True); time.sleep(0.055)
                                    GPIO.output(12, False); time.sleep(0.055)
                                time.sleep(4.5)
                                GUI.close()
                                continue

                            hora_caduca = qr_list[1]
                            hora_actual = strftime("%H:%M:%S")
                            if hora_actual > hora_caduca:
                                print("El QR ya caducó")
                                GUI = VentanaEmergente("CADUCO", str(hora_caduca))
                                GUI.show()
                                for i in range(5):
                                    GPIO.output(12, True); time.sleep(0.055)
                                    GPIO.output(12, False); time.sleep(0.055)
                                time.sleep(4.5)
                                GUI.close()
                                continue

                            # Datos del QR
                            tramo = qr_list[5]
                            tipo_de_pasajero = str(qr_list[6]).lower()
                            p_n = "normal"
                            if tipo_de_pasajero == "estudiante":
                                id_tipo_de_pasajero, p_n = 1, "preferente"
                            elif tipo_de_pasajero == "menor":
                                id_tipo_de_pasajero, p_n = 3, "preferente"
                            elif tipo_de_pasajero == "mayor":
                                id_tipo_de_pasajero, p_n = 4, "preferente"
                            else:
                                id_tipo_de_pasajero = 2

                            print("Tipo de pasajero: ", tipo_de_pasajero)
                            print("Id tipo de pasajero: ", id_tipo_de_pasajero)
                            print("P/N: ", p_n)

                            # Geocerca
                            en_geocerca = False
                            try:
                                doble_tarnsbordo_o_no = str(qr_list[7])
                                geo_actual = str(str(vg.geocerca.split(",")[1]).split("_")[0])
                                if doble_tarnsbordo_o_no == "st":
                                    if geo_actual in str(qr_list[8]):
                                        en_geocerca = True
                                else:
                                    if geo_actual in str(qr_list[8]):
                                        en_geocerca = True
                                    elif len(qr_list) > 9 and geo_actual in str(qr_list[9]):
                                        en_geocerca = True
                            except Exception as e:
                                print(e)
                                logging.info(e)

                            if not en_geocerca:
                                print("No se encuentra en la geocerca que debe transbordar")
                                if doble_tarnsbordo_o_no == "st":
                                    GUI = VentanaEmergente("EQUIVOCADO", str(qr_list[8]))
                                else:
                                    destino_esperado = f"{qr_list[8]} o {qr_list[9]}" if len(qr_list) > 9 else str(qr_list[8])
                                    GUI = VentanaEmergente("EQUIVOCADO", destino_esperado)
                                GUI.show()
                                for i in range(5):
                                    GPIO.output(12, True); time.sleep(0.055)
                                    GPIO.output(12, False); time.sleep(0.055)
                                time.sleep(4.5)
                                GUI.close()
                                continue

                            # Verificar reutilización
                            es_ticket_usado = verificar_ticket_completo(qr_str)
                            if es_ticket_usado is not None:
                                print("El QR ya fue usado")
                                GUI = VentanaEmergente("UTILIZADO", ".....")
                                GUI.show()
                                for i in range(5):
                                    GPIO.output(12, True); time.sleep(0.055)
                                    GPIO.output(12, False); time.sleep(0.055)
                                time.sleep(4.5)
                                GUI.close()
                                continue

                            # Impresión
                            try:
                                from impresora import imprimir_boleto_normal_sin_servicio, imprimir_boleto_normal_con_servicio
                            except Exception as e:
                                print("No se importaron las librerias de impresora")
                                logging.info(e)

                            servicio = ""
                            usted_se_dirige = ""
                            destino = str(tramo).split("-")[1] if "-" in str(tramo) else str(tramo)

                            # Resolver servicio según modo
                            if doble_tarnsbordo_o_no == "st":
                                for servicio_vg in vg.todos_los_servicios_activos:
                                    if str(destino) in str(servicio_vg[2]):
                                        servicio = str(servicio_vg[5]) + "-" + str(str(servicio_vg[1]).split("_")[0]) + "-" + str(str(servicio_vg[2]).split("_")[0])
                            else:  # "ct"
                                for transbordo in vg.todos_los_transbordos_activos:
                                    if str(destino) in str(transbordo[2]):
                                        servicio = str(transbordo[5]) + "-" + str(str(transbordo[1]).split("_")[0]) + "-" + str(str(transbordo[2]).split("_")[0])

                            # Siguiente folio
                            ultimo_folio_de_venta = obtener_ultimo_folio_de_item_venta()
                            if ultimo_folio_de_venta is not None:
                                if int(self.settings.value('reiniciar_folios')) == 0:
                                    ultimo_folio_de_venta = int(ultimo_folio_de_venta[1]) + 1
                                else:
                                    ultimo_folio_de_venta = 1
                                    self.settings.setValue('reiniciar_folios', 0)
                            else:
                                ultimo_folio_de_venta = 1

                            # Imprimir
                            hecho = False
                            if servicio != "":
                                usted_se_dirige = str(servicio).split("-")[2]
                                hecho = imprimir_boleto_normal_con_servicio(ultimo_folio_de_venta, fecha_hoy, hora_actual, self.idUnidad, servicio, tramo, qr_list)
                                logging.info("Tickets impresos correctamente.")
                            else:
                                hecho = imprimir_boleto_normal_sin_servicio(ultimo_folio_de_venta, fecha_hoy, hora_actual, self.idUnidad, tramo, qr_list)
                                logging.info("Tickets impresos correctamente, pero no se encontró el destino.")

                            if hecho:
                                # Persistencia
                                insertar_item_venta(
                                    ultimo_folio_de_venta,
                                    str(self.settings.value('folio_de_viaje')),
                                    fecha_hoy,
                                    hora_actual,
                                    int(0),
                                    int(str(self.settings.value('geocerca')).split(",")[0]),
                                    id_tipo_de_pasajero,
                                    "t",
                                    p_n,
                                    tipo_de_pasajero,
                                    0
                                )
                                print("Venta de servicio directo insertada correctamente." if doble_tarnsbordo_o_no == "st" else "Venta de transbordo insertada correctamente.")

                                self.ultimo_qr = qr_str
                                self.settings.setValue('total_de_folios', f"{int(self.settings.value('total_de_folios')) + 1}")
                                insertar_ticket_usado(qr_str)

                                GUI = VentanaEmergente("ACEPTADO", usted_se_dirige if usted_se_dirige != "" else "No encontrado")
                                GUI.show()
                                time.sleep(5)
                                GUI.close()
                            else:
                                for i in range(5):
                                    GPIO.output(12, True); time.sleep(0.055)
                                    GPIO.output(12, False); time.sleep(0.055)
                                time.sleep(0.5)

                        except Exception as e:
                            print(e)
                            logging.info(e)
                    else:
                        print("\x1b[1;31;47mNo se pudo establecer conexion con QR\033[0;m")
                        self.restablecer_comunicación_QR()
                except Exception as e:
                    print("\x1b[1;31;47m"+"No se pudo establecer conexion: "+str(e)+'\033[0;m')
                    time.sleep(3)
                    logging.info(e)
        except Exception as e:
            print(e)
            logging.info(e)

    def restablecer_comunicación_QR(self):
        try:
            time.sleep(1)
            if self.ser.isOpen():
                print("\x1b[1;32m"+"Puerto ttyACM0 del QR abierto")
                print("\x1b[1;32m"+"Cerrando puerto ttyACM0 del QR")
                self.ser.close()
                self.restablecer_comunicación_QR()
            else:
                print("\x1b[1;32m"+"Puerto ttyACM0 del QR cerrado")
                while self.ser.isOpen() == False:
                    try:
                        print("\x1b[1;33m"+"Intentando abrir puerto ttyACM0 del QR")
                        time.sleep(5)
                        self.ser = serial.Serial(port='/dev/ttyACM0',baudrate = 115200,timeout=1)
                        print("\x1b[1;32m"+"Conexión del puerto ttyACM0 del QR restablecida")
                    except:
                        pass
        except Exception as e:
            print(e)
            logging.info(e)