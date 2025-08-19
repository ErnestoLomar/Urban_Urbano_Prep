from PyQt5.QtWidgets import QMainWindow, QMessageBox
from PyQt5.QtCore import QEventLoop, QTimer, QThread, pyqtSignal, QSettings
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

# Handler a archivo
fh = logging.FileHandler(LOG_FILE)
fh.setLevel(logging.DEBUG)
fh.setFormatter(_fmt)
# Handler a consola
ch = logging.StreamHandler(sys.stdout)
ch.setLevel(logging.INFO)
ch.setFormatter(_fmt)

# Evitar handlers duplicados si se recarga el módulo
if not logger.handlers:
    logger.addHandler(fh)
    logger.addHandler(ch)
    
# =========================
# Constantes útiles
# =========================
SETTINGS_PATH = "/home/pi/Urban_Urbano/ventanas/settings.ini"
UI_PATH = "/home/pi/Urban_Urbano/ui/prepago.ui"
GIF_CARGANDO = "/home/pi/Urban_Urbano/Imagenes/cargando.gif"
GIF_PAGADO = "/home/pi/Urban_Urbano/Imagenes/pagado.gif"

# Buzzer (BOARD pin 12)
GPIO_PIN_BUZZER = 12

# Tiempo total de espera para detección (segundos)
DETECCION_TIMEOUT_S = 3
# Intervalo entre intentos de detección (segundos)
DETECCION_INTERVALO_S = 0.01

# Reintentos de inicio PN532
PN532_INIT_REINTENTOS = 5
PN532_INIT_INTERVALO_S = 0.1

# APDU Select AID (HCE App)
# 00 A4 04 00 Lc <AID...> Le
# AID: F0 55 72 62 54 00 41  (longitud 0x07)
SELECT_AID_APDU = bytearray([
    0x00, 0xA4, 0x04, 0x00,
    0x07, 0xF0, 0x55, 0x72, 0x62, 0x54, 0x00, 0x41,
    0x00
])

# ANSI colores (solo para consola; logging ya incluye niveles)
YELLOW = "\x1b[1;33m"
RESET = "\x1b[0m"

# === Hilo seguro para HCE ===
class HCEWorker(QThread):
    pago_exitoso = pyqtSignal(str)
    pago_fallido = pyqtSignal(str)

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
        
        self.settings = QSettings(SETTINGS_PATH, QSettings.IniFormat)
        
        # Inicializa lector PN532 (SPI)
        self.PN532_SPI = Pn532Spi(Pn532Spi.SS0_GPIO8)
        self.nfc = Pn532(self.PN532_SPI)
        
        # GPIO buzzer (no lanzar excepción si falla)
        try:
            GPIO.setwarnings(False)
            GPIO.setmode(GPIO.BOARD)
            GPIO.setup(GPIO_PIN_BUZZER, GPIO.OUT, initial=GPIO.LOW)
        except Exception as e:
            logger.error(f"No se pudo inicializar el zumbador: {e}")
        
        self.iniciar_hce()
        
    # -------------------------
    # Inicialización PN532
    # -------------------------
    def iniciar_hce(self):
        try:
            intentos = 0
            versiondata = None
            while intentos < PN532_INIT_REINTENTOS and self.running:
                try:
                    self.nfc.begin()
                    versiondata = self.nfc.getFirmwareVersion()
                    logger.info(f"Firmware PN532: {versiondata}")
                    self.nfc.SAMConfig()
                    if versiondata:
                        logger.info("PN532 inicializado correctamente.")
                        break
                except Exception as e:
                    logger.warning(f"Intento {intentos + 1}/{PN532_INIT_REINTENTOS} - Error iniciando PN532: {e}")

                intentos += 1
                time.sleep(PN532_INIT_INTERVALO_S)

            if not versiondata:
                raise RuntimeError("No se detectó el lector NFC (PN532).")

        except Exception as e:
            logger.exception(f"Error fatal al iniciar el lector NFC: {e}")
            # Emite una falla para que la UI lo muestre y terminamos el hilo
            self.pago_fallido.emit("Error al iniciar NFC")
            self.running = False
            
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
        """Envía un APDU (bytearray/bytes) y devuelve (success, respuesta_bytes_o_b'' )."""
        try:
            success, response = self.nfc.inDataExchange(bytearray(data_bytes))
            if response is None:
                response = b""
            return success, response
        except Exception as e:
            logger.error(f"Error inDataExchange: {e}")
            return False, b""
        
    def _detectar_dispositivo(self, timeout_s=DETECCION_TIMEOUT_S, intervalo_s=DETECCION_INTERVALO_S):
        """Intenta detectar un dispositivo HCE hasta agotar el timeout."""
        inicio = time.time()
        while self.running and (time.time() - inicio) < timeout_s:
            try:
                if self.nfc.inListPassiveTarget():
                    return True
            except Exception as e:
                logger.debug(f"inListPassiveTarget error: {e}")
            time.sleep(intervalo_s)
        return False

    def _seleccionar_aid(self):
        """Envía APDU SELECT AID. Éxito solo si respuesta == 0x9000."""
        success, response = self._enviar_apdu(SELECT_AID_APDU)
        hex_resp = binascii.hexlify(response).decode("utf-8") if response else ""
        logger.info(f"Respuesta SELECT AID (hex): {hex_resp}")
        return success and hex_resp == "9000"

    def _parsear_respuesta_celular(self, back_bytes):
        """
        Intenta decodificar la respuesta del celular.
        Devuelve lista de campos o [] si no válido.
        """
        if not back_bytes:
            return []

        try:
            texto = back_bytes.decode("utf-8", errors="replace").strip()
        except Exception:
            # Fallback duro
            try:
                texto = back_bytes.decode("latin-1", errors="replace").strip()
            except Exception:
                return []

        # Asegurar que no tenga basura binaria
        partes = [p.strip() for p in texto.split(",")]
        return partes

    def _validar_trama_ct(self, partes):
        """
        Valida que sea: CT,<OK/ERR>,<id_monedero>,<no_transaccion>,<saldo_posterior>
        Regresa dict con datos o None si inválida.
        """
        if len(partes) < 5 or partes[0] != "CT":
            return None

        # partes[1] puede ser OK o ERR (se registra igual; ERR lo reportamos en logs)
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

    def _actualizar_totales_settings(self):
        """Actualiza contadores en QSettings de forma segura."""
        try:
            pasajero_digital = f"{self.setting_pasajero}_digital"
            total_str = self.settings.value(pasajero_digital, "0,0")
            try:
                total, subtotal = map(float, str(total_str).split(","))
            except Exception:
                total, subtotal = 0.0, 0.0

            total = int(total + 1)
            subtotal = float(subtotal + self.precio)

            self.settings.setValue(pasajero_digital, f"{total},{subtotal}")

            total_liquidar = float(self.settings.value("total_a_liquidar_digital", "0") or 0)
            self.settings.setValue("total_a_liquidar_digital", str(total_liquidar + self.precio))

            total_folios = int(self.settings.value("total_de_folios_digital", "0") or 0)
            self.settings.setValue("total_de_folios_digital", str(total_folios + 1))
        except Exception as e:
            logger.error(f"Error actualizando QSettings: {e}")

    def run(self):
        
        if not self.running:
            return
        
        while self.pagados < self.total_hce and self.running:
            try:
                logger.info("Esperando dispositivo HCE...")
                
                if not self._detectar_dispositivo():
                    self.pago_fallido.emit("No se detectó celular")
                    continue

                logger.info("¡Dispositivo detectado!")
                
                if not self._seleccionar_aid():
                    self.pago_fallido.emit("Error en intercambio de datos (SELECT AID)")
                    continue
                
                # --- Construir y enviar trama de cobro ---
                fecha = strftime('%d-%m-%Y')
                hora = strftime("%H:%M:%S")
                
                servicio_cfg = self.settings.value('servicio', '') or ''
                # Asegurar que todos los campos vayan como texto
                trama_txt = f"{vg.folio_asignacion},{self.precio},{hora},{servicio_cfg},{self.origen},{self.destino}"
                trama_bytes = trama_txt.encode("utf-8")

                logger.info(f"Trama a enviar: {trama_txt}")
                
                ok_tx, back = self._enviar_apdu(trama_bytes)
                if not ok_tx:
                    self.pago_fallido.emit("Error al enviar APDU")
                    continue
                
                partes = self._parsear_respuesta_celular(back)
                logger.info(f"Respuesta celular (partes): {partes}")
                
                datos = self._validar_trama_ct(partes)
                if not datos:
                    self.pago_fallido.emit("Respuesta inválida del celular")
                    continue

                if datos["estado"] == "ERR":
                    logger.warning("Celular reporta ERR en la respuesta CT.")
                    
                # --- Folio y guardado en DB ---
                ultimo = obtener_ultimo_folio_de_venta_digital() or (None, 0)
                folio_venta_digital = (ultimo[1] if isinstance(ultimo, (list, tuple)) and len(ultimo) > 1 else 0) + 1
                logger.info(f"Folio de venta digital asignado: {folio_venta_digital}")
                
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
                    
                # --- Confirmación a celular ---
                ok_conf, back_conf = self._enviar_apdu(b"OKDB")
                conf_txt = (back_conf or b"").decode("utf-8", errors="replace")
                logger.info(f"Confirmación recibida del celular: {conf_txt}")
                
                if ok_conf and conf_txt == "OKDB":
                    actualizar_estado_venta_digital_revisado("OK", folio_venta_digital, vg.folio_asignacion)
                    logger.info("Estado de venta actualizado a OK.")
                else:
                    actualizar_estado_venta_digital_revisado("ERR", folio_venta_digital, vg.folio_asignacion)
                    logger.warning("Error al enviar confirmación de venta al celular (estado ERR).")
                    
                # Feedback físico y señales UI
                self._buzzer_ok()
                self.pagados += 1
                self._actualizar_totales_settings()

                self.pago_exitoso.emit(conf_txt or "OKDB")
                logger.info("Venta digital guardada y confirmada.")
                    
            except Exception as e:
                logger.exception(f"Excepción en ciclo de cobro: {e}")
                self.pago_fallido.emit(str(e))
                break
            
        logger.info("HCEWorker: fin del hilo run().")

    def stop(self):
        self.running = False
        try:
            self.quit()
            self.wait(1500)
        except Exception as e:
            logger.debug(f"Stop wait error: {e}")


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
        
        self.exito_pago = {'hecho': False, 'pagado_efectivo': False}
        self.pagados = 0

        uic.loadUi(UI_PATH, self)
        
        # UI básica
        self.btn_pagar_con_efectivo.clicked.connect(self.pagar_con_efectivo)
        self.label_tipo.setText(f"{self.tipo} - Precio: ${self.precio:.2f}")

        self.movie = QMovie(GIF_CARGANDO)
        self.label_icon.setMovie(self.movie)
        self.movie.start()

        self.loop = QEventLoop()
        self.destroyed.connect(self.loop.quit)

        # Buzzer (no explotar si ya lo configuró el hilo)
        try:
            if not GPIO.getmode():
                GPIO.setwarnings(False)
                GPIO.setmode(GPIO.BOARD)
            GPIO.setup(GPIO_PIN_BUZZER, GPIO.OUT)
        except Exception as e:
            logger.error(f"No se pudo inicializar el zumbador (UI): {e}")

        self.worker = None
        
    def pagar_con_efectivo(self):
        self.exito_pago = {'hecho': False, 'pagado_efectivo': True}
        self.close()

    def mostrar_y_esperar(self):
        self.label_info.setText(f"Esperando cobros 1 de {self.total_hce}")
        self.iniciar_hce()
        self.show()
        self.loop.exec_()
        return self.exito_pago

    def iniciar_hce(self):
        try:
            if vg.modo_nfcCard:
                self.label_info.setText("Modo tarjeta activado")
                QTimer.singleShot(2000, self.close)
                return
        except Exception as e:
            self.label_info.setText(f"Error NFC: {e}")
            logger.error(f"Error NFC (modo tarjeta): {e}")
            QTimer.singleShot(2000, self.close)
            return

        self.worker = HCEWorker(self.total_hce, self.precio, self.tipo_num, self.id_tarifa, self.geocerca, self.servicio, self.setting, self.origen, self.destino)
        self.worker.pago_exitoso.connect(self.pago_exitoso)
        self.worker.pago_fallido.connect(self.pago_fallido)
        self.worker.start()

    def pago_exitoso(self, data):
        self.pagados += 1
        logger.info(f"Cobro {self.pagados}/{self.total_hce} exitoso: {data}")
        self.label_info.setStyleSheet("color: green;")
        self.label_info.setText(f"Pagado {self.pagados}/{self.total_hce}")

        # Cambiar GIF a pagado
        self.movie.stop()
        self.movie = QMovie(GIF_PAGADO)
        self.label_icon.setMovie(self.movie)
        self.movie.start()
        
        self.mostrar_mensaje_exito()

        if self.pagados >= self.total_hce:
            self.exito_pago = {'hecho': True, 'pagado_efectivo': False}
            QTimer.singleShot(2000, self.close)
        else:
            # Volver a mostrar el gif de "cargando" después de 2 segundos
            QTimer.singleShot(2000, self.restaurar_cargando)
    
    def mostrar_mensaje_exito(self):
        try:
            msg = QMessageBox(self)
            msg.setIcon(QMessageBox.Information)
            msg.setWindowTitle("Pago Exitoso")
            msg.setText("El pago se realizó exitosamente.")
            msg.setStandardButtons(QMessageBox.Ok)
            msg.exec_()
        except Exception as e:
            logger.debug(f"No se pudo mostrar QMessageBox (posible modo sin X11): {e}")

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
        try:
            vg.modo_nfcCard = True
        except Exception:
            pass
        self.exito_pago = {'hecho': False, 'pagado_efectivo': False}
        if self.worker:
            self.worker.stop()
        self.close()

    def closeEvent(self, event):
        try:
            if self.worker:
                self.worker.stop()
        except Exception as e:
            logger.debug(f"closeEvent stop error: {e}")
        self.loop.quit()
        event.accept()