from PyQt5.QtWidgets import QMainWindow, QMessageBox
from PyQt5.QtCore import QEventLoop, QTimer, QThread, pyqtSignal, QSettings, QWaitCondition, QMutex
from PyQt5 import uic
from PyQt5.QtGui import QMovie

import time
import binascii
import logging
import sys
from time import strftime

import RPi.GPIO as GPIO
from pn532pi import Pn532, Pn532Spi
import variables_globales as vg

# === Rutas/Imports de DB ===
sys.path.insert(1, '/home/pi/Urban_Urbano/db')
from ventas_queries import (
    guardar_venta_digital,
    obtener_ultimo_folio_de_venta_digital,
    actualizar_estado_venta_digital_revisado,
)

# =========================
# Configuración de logging
# =========================
LOG_FILE = "/home/pi/Urban_Urbano/logs/hce_prepago.log"

logger = logging.getLogger("HCEPrepago")
logger.setLevel(logging.DEBUG)

_fmt = logging.Formatter(
    "%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

fh = logging.FileHandler(LOG_FILE); fh.setLevel(logging.DEBUG); fh.setFormatter(_fmt)
ch = logging.StreamHandler(sys.stdout); ch.setLevel(logging.INFO); ch.setFormatter(_fmt)
if not logger.handlers:
    logger.addHandler(fh); logger.addHandler(ch)
    
# =========================
# Constantes útiles
# =========================
SETTINGS_PATH = "/home/pi/Urban_Urbano/ventanas/settings.ini"
UI_PATH = "/home/pi/Urban_Urbano/ui/prepago.ui"
GIF_CARGANDO = "/home/pi/Urban_Urbano/Imagenes/cargando.gif"
GIF_PAGADO = "/home/pi/Urban_Urbano/Imagenes/pagado.gif"

# Buzzer (BOARD pin 12)
GPIO_PIN_BUZZER = 12

# RSTO del PN532 -> GPIO27 (pin físico 13)
RSTPDN_PIN = 13

# Tiempo total de espera para detección (segundos)
DETECCION_TIMEOUT_S = 1.5
DETECCION_INTERVALO_S = 0.005

# Reintentos de inicio PN532
PN532_INIT_REINTENTOS = 10
PN532_INIT_INTERVALO_S = 0.05

# Reintentos de interconexión con HCE
HCE_REINTENTOS = 12
HCE_REINTENTO_INTERVALO_S = 0.025

# APDU Select AID (HCE App)
SELECT_AID_APDU = bytearray([
    0x00, 0xA4, 0x04, 0x00,
    0x07, 0xF0, 0x55, 0x72, 0x62, 0x54, 0x00, 0x41,
    0x00
])

# GPIO buzzer
try:
    GPIO.setwarnings(False)
    GPIO.setmode(GPIO.BOARD)
    GPIO.setup(GPIO_PIN_BUZZER, GPIO.OUT, initial=GPIO.LOW)
    GPIO.setup(RSTPDN_PIN, GPIO.OUT, initial=GPIO.HIGH)
except Exception as e:
    logger.error(f"No se pudo inicializar el zumbador: {e}")

# === Hilo seguro para HCE ===
class HCEWorker(QThread):
    pago_exitoso = pyqtSignal(dict)
    pago_fallido = pyqtSignal(str)
    actualizar_settings = pyqtSignal(dict)
    error_inicializacion = pyqtSignal(str)
    wait_for_ok = pyqtSignal()

    def __init__(self, total_hce, precio, tipo, id_tarifa, geocerca, servicio, setting, origen=None, destino=None):
        super().__init__()
        self.total_hce = total_hce
        self.pagados = 0
        self.precio = precio
        self.tipo_pasajero = tipo
        self.id_tarifa = id_tarifa
        self.geocerca = geocerca
        self.servicio = servicio
        self.setting_pasajero = setting
        self.origen = origen
        self.destino = destino
        self.running = True
        self.contador_sin_dispositivo = 0

        self.mutex = QMutex()
        self.cond = QWaitCondition()
        self.settings = QSettings(SETTINGS_PATH, QSettings.IniFormat)

        self.PN532_SPI = Pn532Spi(Pn532Spi.SS0_GPIO8)
        self.nfc = Pn532(self.PN532_SPI)

    def pn532_hard_reset(self):
        try:
            print("Hard reset PN532")
            with vg.pn532_lock:
                GPIO.output(RSTPDN_PIN, GPIO.LOW)
                time.sleep(0.4)
                GPIO.output(RSTPDN_PIN, GPIO.HIGH)
                time.sleep(0.6)
        except Exception as e:
            logger.error(f"Error al resetear el lector NFC: {e}")
        
    # -------------------------
    # Inicialización PN532
    # -------------------------
    def iniciar_hce(self):
        try:
            # Toma control exclusivo del PN532
            if not vg.pn532_acquire("HCE", timeout=3.0):
                self.error_inicializacion.emit("PN532 ocupado. Intente de nuevo.")
                self.running = False
                return

            intentos = 0
            versiondata = None
            while intentos < PN532_INIT_REINTENTOS and self.running:
                try:
                    with vg.pn532_lock:
                        self.nfc.begin()
                        versiondata = self.nfc.getFirmwareVersion()
                        logger.info(f"Firmware PN532: {versiondata}")
                        self.nfc.SAMConfig()
                    if versiondata:
                        logger.info("PN532 inicializado correctamente.")
                        break
                except Exception as e:
                    logger.warning(f"Intento {intentos + 1}/{PN532_INIT_REINTENTOS} - Error iniciando PN532: {e}")
                    self.pn532_hard_reset()

                intentos += 1
                time.sleep(PN532_INIT_INTERVALO_S)

            if not versiondata:
                self.pn532_hard_reset()
                self.error_inicializacion.emit("El lector NFC no responde. Acepte efectivo.")
                self.running = False

        except Exception as e:
            logger.exception(f"Error fatal al iniciar el lector NFC: {e}")
            self.error_inicializacion.emit("No se pudo iniciar el lector NFC")
            self.running = False

    def _recrear_pn532(self, recrear_spi=True, reintentos=5, pausa=0.06):
        """Recrea la instancia y vuelve a hacer begin/SAMConfig tras un reset."""
        for i in range(reintentos):
            try:
                with vg.pn532_lock:
                    if recrear_spi:
                        self.PN532_SPI = Pn532Spi(Pn532Spi.SS0_GPIO8)
                    self.nfc = Pn532(self.PN532_SPI)
                    self.nfc.begin()
                    ver = self.nfc.getFirmwareVersion()
                    self.nfc.SAMConfig()
                if ver:
                    logger.info(f"PN532 reabierto OK: {ver}")
                    return True
            except Exception as e:
                logger.warning(f"Recrear PN532 ({i+1}/{reintentos}): {e}")
                time.sleep(pausa)
        return False
            
    # -------------------------
    # Utilidades
    # -------------------------
    def _buzzer_ok(self):
        try:
            GPIO.output(GPIO_PIN_BUZZER, True)
            time.sleep(0.2)
            GPIO.output(GPIO_PIN_BUZZER, False)
        except Exception as e:
            logger.debug(f"Buzzer OK error: {e}")

    def _buzzer_error(self):
        try:
            for _ in range(5):
                GPIO.output(GPIO_PIN_BUZZER, True)
                time.sleep(0.055)
                GPIO.output(GPIO_PIN_BUZZER, False)
                time.sleep(0.055)
        except Exception as e:
            logger.debug(f"Buzzer ERR error: {e}")
            
    def _enviar_apdu(self, data_bytes):
        """Envía un APDU y devuelve (success, respuesta_bytes_o_b'')."""
        try:
            with vg.pn532_lock:
                success, response = self.nfc.inDataExchange(bytearray(data_bytes))
            if response is None:
                response = b""
            return success, response
        except Exception as e:
            logger.error(f"Error inDataExchange: {e}")
            return False, b""
        
    def _detectar_dispositivo(self, timeout_s=DETECCION_TIMEOUT_S, intervalo_s=DETECCION_INTERVALO_S):
        inicio = time.time()
        while self.running and (time.time() - inicio) < timeout_s:
            try:
                with vg.pn532_lock:
                    if self.nfc.inListPassiveTarget():
                        return True
            except Exception as e:
                logger.debug(f"inListPassiveTarget error: {e}")
            time.sleep(intervalo_s)
        return False

    def _seleccionar_aid(self):
        success, response = self._enviar_apdu(SELECT_AID_APDU)
        hex_resp = binascii.hexlify(response).decode("utf-8") if response else ""
        logger.info(f"Respuesta SELECT AID (hex): {hex_resp}")
        return success and hex_resp == "9000"

    def _parsear_respuesta_celular(self, back_bytes):
        if not back_bytes:
            return []
        try:
            texto = back_bytes.decode("utf-8", errors="replace").strip()
        except Exception:
            try:
                texto = back_bytes.decode("latin-1", errors="replace").strip()
            except Exception:
                return []
        partes = [p.strip() for p in texto.split(",")]
        return partes

    def _validar_trama_ct(self, partes, folio_venta_digital):
        if len(partes) < 5 or partes[0] != "CT":
            print("Folio de venta digital no coincide: " + partes[4] + " != " + str(folio_venta_digital))
            logger.warning(f"Folio de venta digital no coincide: {partes[4]} != {folio_venta_digital}")
            return None
        if partes[5] != str(folio_venta_digital):
            print("Folio de venta digital no coincide: " + partes[4] + " != " + str(folio_venta_digital))
            logger.warning(f"Folio de venta digital no coincide: {partes[4]} != {folio_venta_digital}")
            return None
        try:
            id_monedero = int(partes[2])
            no_transaccion = int(partes[3])
            saldo_posterior = float(partes[4])
        except Exception:
            return None
        if not vg.folio_asignacion or id_monedero <= 0 or no_transaccion <= 0:
            return None
        if self.precio <= 0:
            return None
        return {
            "estado": partes[1],
            "id_monedero": id_monedero,
            "no_transaccion": no_transaccion,
            "saldo_posterior": saldo_posterior,
        }
        
    def run(self):
        if not self.running:
            return

        self.iniciar_hce()
        if not self.running:
            return

        try:
            while self.pagados < self.total_hce and self.running:
                try:
                    if self.contador_sin_dispositivo >= 15:
                        self.pago_fallido.emit("Se va a resetear el lector")
                        self.pn532_hard_reset()
                        if not self._recrear_pn532(recrear_spi=True):
                            self.pago_fallido.emit("No se pudo re-inicializar el PN532")
                            time.sleep(0.5)
                            # opcional: continue / break según tu política
                        self.contador_sin_dispositivo = 0
                        continue
                    
                    ultimo = obtener_ultimo_folio_de_venta_digital() or (None, 0)
                    folio_venta_digital = (ultimo[1] if isinstance(ultimo, (list, tuple)) and len(ultimo) > 1 else 0) + 1
                    logger.info(f"Folio de venta digital asignado: {folio_venta_digital}")

                    fecha = strftime('%d-%m-%Y')
                    hora = strftime("%H:%M:%S")
                    servicio_cfg = self.settings.value('servicio', '') or ''
                    trama_txt = f"{vg.folio_asignacion},{folio_venta_digital},{self.precio},{hora},{servicio_cfg},{self.origen},{self.destino}"
                    
                    logger.info("Esperando dispositivo HCE...")
                    if not self._detectar_dispositivo():
                        self.pago_fallido.emit("No se detectó celular")
                        self.contador_sin_dispositivo += 1
                        continue

                    logger.info("Dispositivo detectado")
                    if not self._seleccionar_aid():
                        self.pago_fallido.emit("Error en intercambio de datos (SELECT AID)")
                        continue
                    
                    intento = 0
                    ok_tx = False
                    back = b""
                    while intento < HCE_REINTENTOS and self.running:
                        trama_bytes = (trama_txt + "," + str(intento)).encode("utf-8")
                        logger.info(f"Trama a enviar: {trama_bytes}")
                        ok_tx, back = self._enviar_apdu(trama_bytes)
                        if ok_tx:
                            break
                        self.pago_fallido.emit(
                            "El celular no responde (TRAMA) - intento: "
                            + str(intento) + "/" + str(HCE_REINTENTOS)
                            + " - Respuesta: " + str(back)
                        )
                        logger.info(f"Reintentando envío de trama... intento {intento}/{HCE_REINTENTOS}")
                        intento += 1
                        time.sleep(HCE_REINTENTO_INTERVALO_S)
                
                    if not ok_tx:
                        self.pago_fallido.emit("Error al recibir respuesta del celular (TRAMA)")
                        continue
                    
                    partes = self._parsear_respuesta_celular(back)
                    logger.info(f"Respuesta celular (partes): {partes}")
                    datos = self._validar_trama_ct(partes, folio_venta_digital)
                    if not datos:
                        self.pago_fallido.emit("Respuesta inválida del celular")
                        continue

                    if datos["estado"] == "ERR":
                        logger.warning("Celular reporta ERR en la respuesta CT.")
                    
                    venta_guardada = guardar_venta_digital(
                        folio_venta_digital,
                        vg.folio_asignacion,
                        fecha,
                        hora,
                        self.id_tarifa,
                        self.geocerca,
                        self.tipo_pasajero,
                        self.servicio,
                        "f",
                        datos["id_monedero"],
                        datos["saldo_posterior"],
                        self.precio
                    )
                    
                    if not venta_guardada:
                        logger.error("Error al guardar venta digital en base de datos.")
                        self._buzzer_error()
                        time.sleep(1.5)
                        continue
                    
                    actualizar_estado_venta_digital_revisado("OK", folio_venta_digital, vg.folio_asignacion)
                    logger.info("Estado de venta actualizado a OK.")
                    
                    time.sleep(1)
                    self._buzzer_ok()
                    self.pagados += 1
                    self.actualizar_settings.emit({
                        "setting_pasajero": self.setting_pasajero,
                        "precio": self.precio
                    })

                    self.pago_exitoso.emit({"estado": "OKDB", "folio": folio_venta_digital, "fecha": fecha, "hora": hora})
                    self.wait_for_ok.emit()

                    self.mutex.lock()
                    self.cond.wait(self.mutex)
                    self.mutex.unlock()
                    logger.info("El usuario dio OK, sigo con el flujo...")
                    time.sleep(1)
                    logger.info("Venta digital guardada y confirmada.")
                        
                except Exception as e:
                    logger.exception(f"Excepción en ciclo de cobro: {e}")
                    self.pago_fallido.emit(str(e))
                    break
        finally:
            vg.pn532_release()
            vg.modo_nfcCard = True
            logger.info("HCEWorker: fin del hilo run().")

    def stop(self):
        self.running = False
        # despierta posibles waits de QMessageBox/condición
        try:
            self.mutex.lock()
            self.cond.wakeAll()
            self.mutex.unlock()
        except Exception:
            pass
        # termina el hilo
        try:
            self.quit()  # inocuo si no usas event loop de QThread
            self.wait(1500)
        finally:
            vg.pn532_release()
            vg.modo_nfcCard = True


# === Ventana Principal ===
class VentanaPrepago(QMainWindow):
    def __init__(self, tipo=None, tipo_num=None, setting=None, total_hce=1, precio=0.0, id_tarifa=None, geocerca=None, servicio=None, origen=None, destino=None):
        super().__init__()
        self.total_hce = total_hce
        self.tipo = tipo
        self.tipo_num = tipo_num
        self.setting = setting
        self.precio = precio
        self.id_tarifa = id_tarifa
        self.geocerca = geocerca
        self.servicio = servicio
        self.origen = origen
        self.destino = destino
        self.settings = QSettings(SETTINGS_PATH, QSettings.IniFormat)
        
        self.exito_pago = {'hecho': False, 'pagado_efectivo': False, 'folio': None, 'fecha': None, 'hora': None}
        self.pagados = 0

        uic.loadUi(UI_PATH, self)
        
        self.btn_pagar_con_efectivo.clicked.connect(self.pagar_con_efectivo)
        self.label_tipo.setText(f"{self.tipo} - Precio: ${self.precio:.2f}")

        self.movie = QMovie(GIF_CARGANDO)
        self.label_icon.setMovie(self.movie)
        self.movie.start()

        self.loop = QEventLoop()
        self.destroyed.connect(self.loop.quit)

        try:
            if not GPIO.getmode():
                GPIO.setwarnings(False)
                GPIO.setmode(GPIO.BOARD)
            GPIO.setup(GPIO_PIN_BUZZER, GPIO.OUT)
        except Exception as e:
            logger.error(f"No se pudo inicializar el zumbador (UI): {e}")

        self.worker = None

    def pn532_hard_reset(self):
        try:
            print("Hard reset PN532")
            with vg.pn532_lock:
                GPIO.output(RSTPDN_PIN, GPIO.LOW)
                time.sleep(0.4)
                GPIO.output(RSTPDN_PIN, GPIO.HIGH)
                time.sleep(0.6)
        except Exception as e:
            logger.error(f"Error al resetear el lector NFC: {e}")
        
    def pagar_con_efectivo(self):
        # marca salida por efectivo
        self.exito_pago = {'hecho': False, 'pagado_efectivo': True, 'folio': None, 'fecha': None, 'hora': None}

        # 1) detén el worker si está corriendo (desbloquea cond y libera PN532)
        if self.worker and self.worker.isRunning():
            self.worker.stop()
            self.worker = None

        # 2) programa el cierre y reset fuera del slot, sin bloquear la UI
        QTimer.singleShot(0, self._finish_cash)

    def _finish_cash(self):
        # reset NO bloqueante: intenta tomar el lock con timeout corto
        try:
            lock = getattr(vg, "pn532_lock", None)
            if lock and lock.acquire(timeout=0.2):
                try:
                    GPIO.output(RSTPDN_PIN, GPIO.LOW); time.sleep(0.4)
                    GPIO.output(RSTPDN_PIN, GPIO.HIGH); time.sleep(0.6)
                finally:
                    lock.release()
            else:
                # si no se pudo, pide reset diferido y deja que el lector lo haga
                vg.pn532_reset_requested = True
        except Exception as e:
            logger.error(f"Reset PN532 diferido falló: {e}")

        vg.modo_nfcCard = True
        self.close()

    def mostrar_y_esperar(self):
        self.label_info.setText(f"Esperando cobros 1 de {self.total_hce}")
        self.iniciar_hce()
        self.show()
        self.loop.exec_()
        return self.exito_pago

    def iniciar_hce(self):
        # Pausa el lector concurrente
        vg.modo_nfcCard = False

        self.worker = HCEWorker(self.total_hce, self.precio, self.tipo_num, self.id_tarifa, self.geocerca, self.servicio, self.setting, self.origen, self.destino)
        self.worker.pago_exitoso.connect(self.pago_exitoso)
        self.worker.pago_fallido.connect(self.pago_fallido)
        self.worker.actualizar_settings.connect(self._actualizar_totales_settings)
        self.worker.error_inicializacion.connect(self.error_inicializacion_nfc)
        self.worker.wait_for_ok.connect(self.mostrar_mensaje_exito_bloqueante)
        self.worker.start()
        
    def error_inicializacion_nfc(self, mensaje):
        logger.warning(f"Error de inicialización: {mensaje}")
        self.label_info.setStyleSheet("color: red;")
        self.label_info.setText(mensaje)
        try:
            msg = QMessageBox(self)
            msg.setIcon(QMessageBox.Critical)
            msg.setWindowTitle("Error NFC")
            msg.setText(mensaje)
            msg.setStandardButtons(QMessageBox.Ok)
            msg.exec_()
        except Exception as e:
            logger.debug(f"No se pudo mostrar QMessageBox: {e}")
        QTimer.singleShot(1000, self.close)
        
    def _actualizar_totales_settings(self, data: dict):
        try:
            setting_pasajero = data.get("setting_pasajero", "")
            precio = float(data.get("precio", 0))

            pasajero_digital = f"{setting_pasajero}_digital"
            total_str = self.settings.value(pasajero_digital, "0,0")

            try:
                total, subtotal = map(float, str(total_str).split(","))
            except Exception:
                total, subtotal = 0.0, 0.0

            total = int(total + 1)
            subtotal = float(subtotal + precio)

            self.settings.setValue(pasajero_digital, f"{total},{subtotal}")

            total_liquidar = float(self.settings.value("total_a_liquidar_digital", "0") or 0)
            self.settings.setValue("total_a_liquidar_digital", str(total_liquidar + precio))

            total_folios = int(self.settings.value("total_de_folios_digital", "0") or 0)
            self.settings.setValue("total_de_folios_digital", str(total_folios + 1))
            self.settings.sync()
        except Exception as e:
            logger.error(f"Error actualizando QSettings: {e}")

    def pago_exitoso(self, data):
        self.pagados += 1
        logger.info(f"Cobro {self.pagados}/{self.total_hce} exitoso: {data['estado']}")
        self.label_info.setStyleSheet("color: green;")
        self.label_info.setText(f"Pagado {self.pagados}/{self.total_hce}")

        self.movie.stop()
        self.movie = QMovie(GIF_PAGADO)
        self.label_icon.setMovie(self.movie)
        self.movie.start()

        if self.pagados >= self.total_hce:
            self.exito_pago = {'hecho': True, 'pagado_efectivo': False, 'folio': data['folio'], 'fecha': data['fecha'], 'hora': data['hora']}
            QTimer.singleShot(2000, self.close)
        else:
            QTimer.singleShot(2000, self.restaurar_cargando)

    def mostrar_mensaje_exito_bloqueante(self):
        msg = QMessageBox(self)
        msg.setIcon(QMessageBox.Information)
        msg.setWindowTitle("Pago Exitoso")
        msg.setText("El pago se realizó exitosamente.")
        msg.setStandardButtons(QMessageBox.Ok)
        msg.exec_()
        self.worker.mutex.lock()
        self.worker.cond.wakeAll()
        self.worker.mutex.unlock()

    def restaurar_cargando(self):
        self.label_info.setStyleSheet("color: black;")
        self.label_info.setText(f"Esperando cobros {self.pagados + 1} de {self.total_hce}")
        self.movie.stop()
        self.movie = QMovie(GIF_CARGANDO)
        self.label_icon.setMovie(self.movie)
        self.movie.start()

    def pago_fallido(self, mensaje):
        logger.warning(f"Fallo: {mensaje}")
        self.label_info.setStyleSheet("color: red;")
        self.label_info.setText(mensaje)

    def cerrar_ventana(self):
        logger.info("Pago cancelado por el usuario.")
        self.exito_pago = {'hecho': False, 'pagado_efectivo': False, 'folio': None, 'fecha': None, 'hora': None}
        if self.worker:
            self.worker.stop()
        self.pn532_hard_reset()
        vg.modo_nfcCard = True
        self.close()

    def closeEvent(self, event):
        try:
            if self.worker:
                self.worker.stop()
        except Exception as e:
            logger.debug(f"closeEvent stop error: {e}")
        vg.modo_nfcCard = True
        self.loop.quit()
        event.accept()