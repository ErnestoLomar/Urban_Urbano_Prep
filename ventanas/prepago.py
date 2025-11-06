# -*- coding: utf-8 -*-
# prepago.py — PN532 Blinka (Adafruit) + handshake ISO-DEP + BUZZER reactivado
import sys, time, binascii, logging
from time import strftime

# Qt
from PyQt5.QtWidgets import QMainWindow, QMessageBox
from PyQt5.QtCore import QEventLoop, QTimer, QThread, pyqtSignal, QSettings, QWaitCondition, QMutex
from PyQt5 import uic
from PyQt5.QtGui import QMovie

# PN532 (Blinka / Adafruit)
import board, busio, digitalio
from adafruit_pn532.spi import PN532_SPI

# Proyecto
import variables_globales as vg

# === DB ===
sys.path.insert(1, '/home/pi/Urban_Urbano/db')
from ventas_queries import (
    guardar_venta_digital,
    obtener_ultimo_folio_de_venta_digital,
    actualizar_estado_venta_digital_revisado,
)

# =========================
# Logging
# =========================
LOG_FILE = "/home/pi/Urban_Urbano/logs/hce_prepago.log"
logger = logging.getLogger("HCEPrepago")
logger.setLevel(logging.DEBUG)
_fmt = logging.Formatter("%(asctime)s | %(levelname)s | %(name)s | %(message)s",
                         datefmt="%Y-%m-%d %H:%M:%S")
fh = logging.FileHandler(LOG_FILE); fh.setLevel(logging.DEBUG); fh.setFormatter(_fmt)
ch = logging.StreamHandler(sys.stdout); ch.setLevel(logging.INFO); ch.setFormatter(_fmt)
if not logger.handlers:
    logger.addHandler(fh); logger.addHandler(ch)

# =========================
# Constantes
# =========================
SETTINGS_PATH = "/home/pi/Urban_Urbano/ventanas/settings.ini"
UI_PATH       = "/home/pi/Urban_Urbano/ui/prepago.ui"
GIF_CARGANDO  = "/home/pi/Urban_Urbano/Imagenes/cargando.gif"
GIF_PAGADO    = "/home/pi/Urban_Urbano/Imagenes/pagado.gif"

# AID de tu app HCE (Urban) F0 55 72 62 54 00 41
AID_BYTES = bytes([0xF0,0x55,0x72,0x62,0x54,0x00,0x41])
SELECT_AID_APDU = bytes([0x00,0xA4,0x04,0x00,0x07]) + AID_BYTES + b"\x00"

# 🔔 Buzzer: BOARD 12 = BCM18 = board.D18 (ajusta si usas otro pin)
BUZZER_PIN = board.D18

# Tiempos / reintentos
DETECCION_TIMEOUT_S        = 2.5
DETECCION_INTERVALO_S      = 0.010
EXCHANGE_RETRIES           = 3
HCE_REINTENTOS             = 12
HCE_REINTENTO_INTERVALO_S  = 0.040

RECOVER_TRIES   = 20
RECOVER_SLEEP_S = 0.06
RF_RESET_HOLD_S = 0.08

# =========================
# Capa PN532 (bajo nivel)
# =========================
class PN532LL:
    """Capa de utilidades crudas sobre Adafruit PN532_SPI (SPI)"""
    def __init__(self, cs_pin=board.CE0, rst_pin=board.D27, spi_baud=400_000):
        self.spi = busio.SPI(board.SCLK, board.MOSI, board.MISO)
        self._spi_config(spi_baud)
        self.cs  = digitalio.DigitalInOut(cs_pin)
        self.rst = digitalio.DigitalInOut(rst_pin)
        self.pn  = PN532_SPI(self.spi, self.cs, reset=self.rst)
        self.core = self.pn

    def _spi_config(self, baud):
        while not self.spi.try_lock():
            pass
        try:
            self.spi.configure(baudrate=baud, phase=0, polarity=0)
        finally:
            self.spi.unlock()

    def firmware(self):
        return self.pn.firmware_version

    def sam_config(self):
        self.pn.SAM_configuration()

    def call(self, cmd, response_length=0, params=b"", timeout=1.0):
        for _ in range(3):
            try:
                return self.core.call_function(cmd, response_length=response_length,
                                               params=params, timeout=timeout)
            except RuntimeError:
                time.sleep(0.03)
                try: self.pn.SAM_configuration()
                except Exception: pass
        return None

    def rf_on(self, on: bool):
        try:
            self.call(0x32, response_length=0, params=bytes([0x01, 0x01 if on else 0x00]), timeout=0.5)
            return True
        except Exception:
            return False

    def tune_for_poll(self):
        try:
            self.call(0x32, response_length=0, params=bytes([0x01,0x00]), timeout=0.5)  # RF off
            time.sleep(0.02)
            self.call(0x32, response_length=0, params=bytes([0x01,0x01]), timeout=0.5)  # RF on
            self.call(0x32, response_length=0, params=bytes([0x05,0xFF,0x01,0xFF]), timeout=0.5)  # retries
        except Exception:
            pass

    def in_list_passive_106A(self, timeout=1.2):
        resp = self.call(0x4A, response_length=255, params=bytes([0x01,0x00]), timeout=timeout)
        if not resp or len(resp) < 3 or resp[0] < 1:
            return None
        i = 1
        tg = resp[i]; i += 1
        atqa = bytes(resp[i:i+2]); i += 2
        sak = resp[i]; i += 1
        uid_len = resp[i]; i += 1
        uid = bytes(resp[i:i+uid_len]); i += uid_len
        ats = b""
        if len(resp) > i:
            ats_len = resp[i]; i += 1
            ats = bytes(resp[i:i+ats_len])
        return {"tg": tg, "uid": uid, "atqa": atqa, "sak": sak, "ats": ats, "raw": bytes(resp)}

    def in_release(self):
        try:
            self.call(0x52, response_length=0, params=b"", timeout=0.5)  # InRelease
            return True
        except Exception:
            return False

    def in_data_exchange(self, tg, payload: bytes, resp_len=255):
        resp = self.call(0x40, response_length=resp_len, params=bytes([tg]) + payload, timeout=1.0)
        if not resp:
            return False, b""
        ok = (resp[0] == 0x00)
        return ok, bytes(resp[1:])

    def hard_reset(self):
        try:
            self.rst.switch_to_output(value=False); time.sleep(0.4)
            self.rst.switch_to_output(value=True);  time.sleep(0.6)
            return True
        except Exception:
            return False

# =========================
# Worker HCE
# =========================
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
        self.cond  = QWaitCondition()
        self.settings = QSettings(SETTINGS_PATH, QSettings.IniFormat)

        # PN532 bajo nivel
        self.dev = PN532LL(cs_pin=board.CE0, rst_pin=board.D27, spi_baud=400_000)
        self.tg  = None  # target handle ISO-DEP

        # 🔔 Buzzer (Blinka / digitalio)
        self.buzzer = None
        try:
            self.buzzer = digitalio.DigitalInOut(BUZZER_PIN)
            self.buzzer.direction = digitalio.Direction.OUTPUT
            self.buzzer.value = False
        except Exception as e:
            logger.warning(f"No se pudo inicializar buzzer: {e}")

    # --------------- Helpers APDU/SW ---------------
    def _strip_sw(self, raw: bytes):
        if not raw or len(raw) < 2:
            return raw or b"", b""
        sw = raw[-2:]
        if sw in (b"\x90\x00", b"\x6A\x82"):
            return raw[:-2], sw
        return raw, b""

    def _safe_exchange(self, apdu: bytes):
        last = (False, b"")
        for _ in range(EXCHANGE_RETRIES):
            try:
                with vg.pn532_lock:
                    ok, resp = self.dev.in_data_exchange(self.tg, apdu, resp_len=255)
                if ok and resp:
                    return True, resp
                last = (ok, resp if resp else b"")
            except Exception as e:
                logger.debug(f"inDataExchange exception: {e}")
            time.sleep(0.05)
        return False, last[1]

    # --------------- Buzzer ---------------
    def _buzzer_ok(self):
        if not self.buzzer:
            return
        try:
            self.buzzer.value = True
            time.sleep(0.20)
            self.buzzer.value = False
        except Exception as e:
            logger.debug(f"Buzzer OK error: {e}")

    def _buzzer_error(self):
        if not self.buzzer:
            return
        try:
            for _ in range(5):
                self.buzzer.value = True
                time.sleep(0.055)
                self.buzzer.value = False
                time.sleep(0.055)
        except Exception as e:
            logger.debug(f"Buzzer ERR error: {e}")

    # --------------- Init / recreate ---------------
    def iniciar_hce(self):
        try:
            if not vg.pn532_acquire("HCE", timeout=3.0):
                self.error_inicializacion.emit("PN532 ocupado. Intente de nuevo.")
                self.running = False
                return

            with vg.pn532_lock:
                fw = self.dev.firmware()
                logger.info(f"PN532 FW: {fw}")
                self.dev.sam_config()
                self.dev.tune_for_poll()

        except Exception as e:
            logger.exception(f"Error al iniciar PN532: {e}")
            self.error_inicializacion.emit("No se pudo iniciar el lector NFC")
            self.running = False

    def _recrear(self, reintentos=5):
        for i in range(reintentos):
            try:
                with vg.pn532_lock:
                    self.dev.hard_reset()
                    self.dev.sam_config()
                    self.dev.tune_for_poll()
                return True
            except Exception as e:
                logger.warning(f"Recrear PN532 ({i+1}/{reintentos}): {e}")
                time.sleep(0.06)
        return False

    # --------------- Poll / Select / Recovery ---------------
    def _detectar_y_select(self, timeout_s=DETECCION_TIMEOUT_S):
        fin = time.time() + timeout_s
        while self.running and time.time() < fin:
            try:
                with vg.pn532_lock:
                    info = self.dev.in_list_passive_106A(timeout=1.2)
            except Exception as e:
                logger.debug(f"inList 106A err: {e}")
                info = None

            if info:
                self.tg = info["tg"]
                uid = info["uid"].hex().upper() if info["uid"] else "(sin UID)"
                ats = info["ats"].hex().upper() if info["ats"] else "(sin ATS)"
                logger.info(f"Detectado Tg={self.tg:02X} UID={uid} ATS={ats}")
                time.sleep(0.10)
                ok, rapdu = self._safe_exchange(SELECT_AID_APDU)
                if not ok or not rapdu:
                    logger.info("SELECT sin respuesta; intento recovery…")
                else:
                    data, sw = self._strip_sw(rapdu)
                    if sw == b"\x90\x00" or rapdu == b"\x90\x00":
                        logger.info("SELECT AID OK")
                        return True
                    logger.info(f"SELECT SW={sw.hex().upper() if sw else ''} DATA={data.hex().upper()}")
            else:
                time.sleep(DETECCION_INTERVALO_S)

        return False

    def _recover(self):
        try:
            with vg.pn532_lock:
                self.dev.in_release()
        except Exception:
            pass

        if self.dev.rf_on(False):
            time.sleep(RF_RESET_HOLD_S)
            self.dev.rf_on(True)
            if self._detectar_y_select(timeout_s=1.2):
                return True

        try:
            with vg.pn532_lock:
                self.dev.sam_config()
            if self._detectar_y_select(timeout_s=1.2):
                return True
        except Exception:
            pass

        logger.warning("Recovery suave falló; hard reset…")
        self.dev.hard_reset()
        if self._recrear():
            return self._detectar_y_select(timeout_s=1.5)

        return False

    # --------------- Parseo / Validación ---------------
    def _parsear_respuesta_celular(self, resp_bytes):
        if not resp_bytes:
            return []
        data, _sw = self._strip_sw(resp_bytes)
        try:
            txt = data.decode("utf-8", errors="strict").strip()
        except Exception:
            txt = data.decode("latin-1", errors="replace").strip()
        return [p.strip() for p in txt.split(",")]

    def _validar_trama_ct(self, partes, folio_venta_digital):
        try:
            # CT,OK,<id_monedero>,<no_tx>,<saldo_post>,<aforoId>
            if len(partes) < 6 or partes[0] != "CT":
                logger.error("Trama CT inválida")
                return None
            if partes[5] != str(folio_venta_digital):
                logger.warning(f"Folio de venta digital no coincide: {partes[5]} != {folio_venta_digital}")
                return None
            id_monedero = int(partes[2]); no_tx = int(partes[3]); saldo = float(partes[4])
            if not vg.folio_asignacion or id_monedero <= 0 or no_tx <= 0:
                return None
            if self.precio <= 0:
                return None
            return {"estado": partes[1], "id_monedero": id_monedero, "no_transaccion": no_tx, "saldo_posterior": saldo}
        except Exception as e:
            logger.error(f"Validación CT: {e}")
            return None

    # --------------- Loop principal ---------------
    def run(self):
        if not self.running: return
        self.iniciar_hce()
        if not self.running: return

        try:
            while self.pagados < self.total_hce and self.running:
                try:
                    if self.contador_sin_dispositivo >= 15:
                        self.pago_fallido.emit("Se va a resetear el lector")
                        if not self._recrear():
                            self.pago_fallido.emit("No se pudo re-inicializar el PN532")
                            time.sleep(0.5)
                        self.contador_sin_dispositivo = 0
                        continue

                    ultimo = obtener_ultimo_folio_de_venta_digital() or (None, 0)
                    folio = (ultimo[1] if isinstance(ultimo, (list, tuple)) and len(ultimo) > 1 else 0) + 1
                    logger.info(f"Folio de venta digital asignado: {folio}")

                    fecha = strftime('%d-%m-%Y')
                    hora  = strftime("%H:%M:%S")
                    servicio_cfg = self.settings.value('servicio', '') or ''
                    trama_txt = f"{vg.folio_asignacion},{folio},{self.precio},{hora},{servicio_cfg},{self.origen},{self.destino}"

                    logger.info("Esperando dispositivo HCE…")
                    if not self._detectar_y_select():
                        self.pago_fallido.emit("No se detectó celular")
                        self.contador_sin_dispositivo += 1
                        continue

                    intento = 0
                    ok_tx = False
                    back  = b""
                    while intento < HCE_REINTENTOS and self.running:
                        payload = (trama_txt + "," + str(intento)).encode("utf-8")
                        ok_tx, back = self._safe_exchange(payload)

                        if (not ok_tx) or (not back):
                            self._buzzer_error()  # 🔔 feedback de error de intercambio
                            self.pago_fallido.emit(f"El celular no responde (TRAMA) - intento {intento}/{HCE_REINTENTOS}")
                            if self._recover():
                                time.sleep(HCE_REINTENTO_INTERVALO_S)
                                continue
                            intento += 1
                            time.sleep(HCE_REINTENTO_INTERVALO_S)
                            continue

                        _, sw = self._strip_sw(back)
                        if sw not in (b"", b"\x90\x00"):
                            logger.warning(f"SW no OK: {sw.hex().upper()} — recuperación…")
                            if self._recover():
                                time.sleep(HCE_REINTENTO_INTERVALO_S)
                                continue
                            intento += 1
                            time.sleep(HCE_REINTENTO_INTERVALO_S)
                            continue
                        break

                    if not ok_tx or not back:
                        self._buzzer_error()  # 🔔
                        self.pago_fallido.emit("Error al recibir respuesta del celular (TRAMA)")
                        continue

                    partes = self._parsear_respuesta_celular(back)
                    logger.info(f"Respuesta celular (partes): {partes}")
                    datos = self._validar_trama_ct(partes, folio)
                    if not datos:
                        self._buzzer_error()  # 🔔
                        self.pago_fallido.emit("Respuesta inválida del celular")
                        continue
                    if datos["estado"] == "ERR":
                        self._buzzer_error()  # 🔔
                        self.pago_fallido.emit("Error reportado por el celular")
                        continue

                    venta_guardada = guardar_venta_digital(
                        folio, vg.folio_asignacion, fecha, hora,
                        self.id_tarifa, self.geocerca, self.tipo_pasajero, self.servicio,
                        "f", datos["id_monedero"], datos["saldo_posterior"], self.precio
                    )
                    if not venta_guardada:
                        logger.error("Error al guardar venta digital en base de datos.")
                        self._buzzer_error()  # 🔔
                        time.sleep(1.5)
                        continue

                    actualizar_estado_venta_digital_revisado("OK", folio, vg.folio_asignacion)
                    logger.info("Estado de venta actualizado a OK.")

                    time.sleep(1)
                    self._buzzer_ok()  # 🔔 beep de confirmación
                    self.pagados += 1
                    self.actualizar_settings.emit({"setting_pasajero": self.setting_pasajero, "precio": self.precio})

                    self.pago_exitoso.emit({"estado": "OKDB", "folio": folio, "fecha": fecha, "hora": hora})
                    self.wait_for_ok.emit()

                    self.mutex.lock(); self.cond.wait(self.mutex); self.mutex.unlock()
                    logger.info("El usuario dio OK, sigo con el flujo…")
                    time.sleep(1)
                    logger.info("Venta digital guardada y confirmada.")

                except Exception as e:
                    logger.exception(f"Excepción en ciclo de cobro: {e}")
                    self._buzzer_error()  # 🔔 opcional en excepción
                    self.pago_fallido.emit(str(e))
                    break
        finally:
            try:
                vg.pn532_release()
            except Exception:
                pass
            vg.modo_nfcCard = True
            logger.info("HCEWorker: fin del hilo run().")

    def stop(self):
        self.running = False
        try:
            self.mutex.lock(); self.cond.wakeAll(); self.mutex.unlock()
        except Exception:
            pass
        try:
            self.quit(); self.wait(1500)
        finally:
            # 🔧 libera el GPIO del buzzer
            try:
                if self.buzzer:
                    self.buzzer.deinit()
            except Exception:
                pass
            try:
                vg.pn532_release()
            except Exception:
                pass
            vg.modo_nfcCard = True

# =========================
# Ventana principal
# =========================
class VentanaPrepago(QMainWindow):
    def __init__(self, tipo=None, tipo_num=None, setting=None, total_hce=1, precio=0.0,
                 id_tarifa=None, geocerca=None, servicio=None, origen=None, destino=None):
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

        self.movie = QMovie(GIF_CARGANDO); self.label_icon.setMovie(self.movie); self.movie.start()
        self.loop = QEventLoop(); self.destroyed.connect(self.loop.quit)
        self.btn_cancelar.clicked.connect(self.cancelar_transaccion)
        self.worker = None

    def cancelar_transaccion(self):
        logger.info("Transacción HCE cancelada por el usuario.")
        self.exito_pago = {'hecho': False, 'pagado_efectivo': False, 'folio': None, 'fecha': None, 'hora': None}
        if self.worker:
            try: self.worker.stop()
            except Exception as e: logger.debug(f"cancelar_transaccion stop error: {e}")
            self.worker = None
        vg.modo_nfcCard = True
        self.close()

    def pagar_con_efectivo(self):
        self.exito_pago = {'hecho': False, 'pagado_efectivo': True, 'folio': None, 'fecha': None, 'hora': None}
        if self.worker and self.worker.isRunning():
            self.worker.stop()
            self.worker = None
        QTimer.singleShot(0, self._finish_cash)

    def _finish_cash(self):
        try:
            lock = getattr(vg, "pn532_lock", None)
            if lock and lock.acquire(timeout=0.2):
                try:
                    if self.worker and getattr(self.worker, "dev", None):
                        self.worker.dev.hard_reset()
                finally:
                    lock.release()
            else:
                vg.pn532_reset_requested = True
        except Exception as e:
            logger.error(f"Reset PN532 diferido falló: {e}")
        vg.modo_nfcCard = True
        self.close()

    def mostrar_y_esperar(self):
        self.label_info.setText(f"Esperando cobros 1 de {self.total_hce}")
        self.iniciar_hce(); self.show(); self.loop.exec_()
        return self.exito_pago

    def iniciar_hce(self):
        vg.modo_nfcCard = False
        self.worker = HCEWorker(self.total_hce, self.precio, self.tipo_num, self.id_tarifa,
                                self.geocerca, self.servicio, self.setting, self.origen, self.destino)
        self.worker.pago_exitoso.connect(self.pago_exitoso)
        self.worker.pago_fallido.connect(self.pago_fallido)
        self.worker.actualizar_settings.connect(self._actualizar_totales_settings)
        self.worker.error_inicializacion.connect(self.error_inicializacion_nfc)
        self.worker.wait_for_ok.connect(self.mostrar_mensaje_exito_bloqueante)
        self.worker.start()

    def error_inicializacion_nfc(self, mensaje):
        logger.warning(f"Error de inicialización: {mensaje}")
        self.label_info.setStyleSheet("color: red;"); self.label_info.setText(mensaje)
        try:
            msg = QMessageBox(self); msg.setIcon(QMessageBox.Critical)
            msg.setWindowTitle("Error NFC"); msg.setText(mensaje)
            msg.setStandardButtons(QMessageBox.Ok); msg.exec_()
        except Exception as e:
            logger.debug(f"No se pudo mostrar QMessageBox: {e}")
        QTimer.singleShot(1000, self.close)

    def _actualizar_totales_settings(self, data: dict):
        try:
            setting_pasajero = data.get("setting_pasajero", "")
            precio = float(data.get("precio", 0))
            pasajero_digital = f"{setting_pasajero}_digital"
            total_str = self.settings.value(pasajero_digital, "0,0")
            try: total, subtotal = map(float, str(total_str).split(","))
            except Exception: total, subtotal = 0.0, 0.0
            total = int(total + 1); subtotal = float(subtotal + precio)
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
        self.movie.stop(); self.movie = QMovie(GIF_PAGADO); self.label_icon.setMovie(self.movie); self.movie.start()
        if self.pagados >= self.total_hce:
            self.exito_pago = {'hecho': True, 'pagado_efectivo': False,
                               'folio': data['folio'], 'fecha': data['fecha'], 'hora': data['hora']}
            QTimer.singleShot(2000, self.close)
        else:
            QTimer.singleShot(2000, self.restaurar_cargando)

    def mostrar_mensaje_exito_bloqueante(self):
        msg = QMessageBox(self)
        msg.setIcon(QMessageBox.Information); msg.setWindowTitle("Pago Exitoso")
        msg.setText("El pago se realizó exitosamente."); msg.setStandardButtons(QMessageBox.Ok); msg.exec_()
        self.worker.mutex.lock(); self.worker.cond.wakeAll(); self.worker.mutex.unlock()

    def restaurar_cargando(self):
        self.label_info.setStyleSheet("color: black;")
        self.label_info.setText(f"Esperando cobros {self.pagados + 1} de {self.total_hce}")
        self.movie.stop(); self.movie = QMovie(GIF_CARGANDO); self.label_icon.setMovie(self.movie); self.movie.start()

    def pago_fallido(self, mensaje):
        logger.warning(f"Fallo: {mensaje}")
        self.label_info.setStyleSheet("color: red;"); self.label_info.setText(mensaje)

    def closeEvent(self, event):
        try:
            if self.worker: self.worker.stop()
        except Exception as e:
            logger.debug(f"closeEvent stop error: {e}")
        vg.modo_nfcCard = True
        self.loop.quit()
        event.accept()
