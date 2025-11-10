# -*- coding: utf-8 -*-
import sys
import time
import binascii
import logging
from time import strftime

# Qt
from PyQt5.QtWidgets import QMainWindow, QMessageBox
from PyQt5.QtCore import QEventLoop, QTimer, QThread, pyqtSignal, QSettings, QWaitCondition, QMutex
from PyQt5 import uic
from PyQt5.QtGui import QMovie

# Blinka adapter PN532
import board
from pn532_blinka_adapter import Pn532Blinka

# Proyecto
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
# GPIOHub (para buzzer)
# =========================
try:
    from gpio_hub import GPIOHub, PINMAP
    HUB = GPIOHub(PINMAP)
    logger.info("GPIOHub inicializado para buzzer.")
except Exception as e:
    HUB = None
    logger.warning(f"No se pudo inicializar GPIOHub: {e}")

# =========================
# Constantes útiles
# =========================
SETTINGS_PATH = "/home/pi/Urban_Urbano/ventanas/settings.ini"
UI_PATH = "/home/pi/Urban_Urbano/ui/prepago.ui"
GIF_CARGANDO = "/home/pi/Urban_Urbano/Imagenes/cargando.gif"
GIF_PAGADO = "/home/pi/Urban_Urbano/Imagenes/pagado.gif"

# Tiempo total de espera para detección (segundos)
DETECCION_TIMEOUT_S = 4
DETECCION_INTERVALO_S = 0.015  # evita busy-wait

# Reintentos de inicio PN532
PN532_INIT_REINTENTOS = 10
PN532_INIT_INTERVALO_S = 0.05

# Reintentos de interconexión con HCE
HCE_REINTENTOS = 12
HCE_REINTENTO_INTERVALO_S = 0.06

# APDU Select AID (HCE App) — ajusta a tu AID real
SELECT_AID_APDU = bytearray([
    0x00, 0xA4, 0x04, 0x00,
    0x07, 0xF0, 0x55, 0x72, 0x62, 0x54, 0x00, 0x41,
    0x00
])


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

        # PN532 por Blinka: CE0 (BOARD24) y RESET en D27 (BOARD13)
        # self.nfc = Pn532Blinka(cs_pin=board.CE0, rst_pin=board.D27)
        self.nfc = None

    def pn532_hard_reset(self):
        try:
            logger.info("Hard reset PN532")
            with vg.pn532_lock:
                self.nfc.hard_reset()
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
            
            # instancia aquí
            try:
                with vg.pn532_lock:
                    self.nfc = Pn532Blinka(cs_pin=board.CE0, rst_pin=board.D27)
            except Exception as e:
                self.error_inicializacion.emit(f"No se pudo abrir PN532: {e}")
                self.running = False
                return
            
            # reset preventivo antes de begin()
            self.pn532_hard_reset()
            time.sleep(0.12)

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
                    # recrear siempre para homogeneidad
                    self.nfc = Pn532Blinka(cs_pin=board.CE0, rst_pin=board.D27)
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
            if HUB:
                HUB.buzzer_beep(200)  # 200 ms
        except Exception as e:
            logger.debug(f"Buzzer OK error: {e}")

    def _buzzer_error(self):
        try:
            if HUB:
                HUB.buzzer_blinks(n=5, on_ms=55, off_ms=55)
        except Exception as e:
            logger.debug(f"Buzzer ERR error: {e}")

    def _enviar_apdu(self, data_bytes, *, rearm=True):
        """Envía APDU. Si falla y rearm=True, re-detecta y re-select una sola vez."""
        try:
            with vg.pn532_lock:
                ok, resp = self.nfc.inDataExchange(bytearray(data_bytes))
            if ok and resp is not None:
                return True, (resp or b"")
        except Exception as e:
            logger.error(f"Error inDataExchange: {e}")

        if not rearm:
            return False, b""

        logger.info("Rearme ISO-DEP: re-detección y re-SELECT")
        if self._detectar_dispositivo(timeout_s=1.8):
            # SELECT directo SIN rearmar para evitar bucle
            ok_sel, r_sel = self._select_aid_low()
            if ok_sel and len(r_sel) >= 2 and r_sel[-2:] == b"\x90\x00":
                try:
                    with vg.pn532_lock:
                        ok2, resp2 = self.nfc.inDataExchange(bytearray(data_bytes))
                    return ok2, (resp2 or b"")
                except Exception as e:
                    logger.error(f"Error inDataExchange tras rearme: {e}")
        return False, b""

    def _detectar_dispositivo(self, timeout_s=DETECCION_TIMEOUT_S, intervalo_s=DETECCION_INTERVALO_S):
        inicio = time.time()
        while self.running and (time.time() - inicio) < timeout_s:
            try:
                with vg.pn532_lock:
                    ok = self.nfc.inListPassiveTarget(timeout=1.2)
                    if ok:
                        try:
                            self.contador_sin_dispositivo = 0
                            uid = self.nfc.read_uid(timeout=0.2)
                            if uid:
                                logger.info(f"UID detectado: {uid.hex().upper()}")
                        except Exception:
                            pass
                        return True
            except Exception as e:
                logger.debug(f"detección error: {e}")
            time.sleep(intervalo_s)
        return False

    def _seleccionar_aid(self):
        ok, r = self._select_aid_low()
        print("SELECT AID: ", ok, r)
        if not ok or len(r) < 2:
            logger.info("SELECT AID sin respuesta válida")
            return False
        sw = r[-2:]
        logger.info(f"SELECT SW={sw.hex().upper()}  DATA={r[:-2].hex().upper() if len(r)>2 else ''}")
        return sw == b"\x90\x00"
    
    def _select_aid_low(self):
        """SELECT AID sin rearmar. Refresca Tg con 0x4A y espera."""
        try:
            with vg.pn532_lock:
                # refresca Tg para evitar target stale
                self.nfc.refresh_target(timeout=1.0)
        except Exception:
            pass

        time.sleep(0.18)  # antes 0.12 → da tiempo al stack ISO-DEP

        try:
            with vg.pn532_lock:
                ok, r = self.nfc.inDataExchange(SELECT_AID_APDU)
        except Exception as e:
            logger.error(f"SELECT low error: {e}")
            return False, b""

        if not ok or len(r) < 2:
            time.sleep(0.18)
            try:
                with vg.pn532_lock:
                    # reintento corto con Tg refrescado otra vez
                    self.nfc.refresh_target(timeout=0.6)
                    ok, r = self.nfc.inDataExchange(SELECT_AID_APDU)
            except Exception as e:
                logger.error(f"SELECT low retry error: {e}")
                return False, b""
        return ok, (r or b"")

    def _parsear_respuesta_celular(self, back_bytes):
        if not back_bytes:
            return []
        try:
            texto = back_bytes.decode("utf-8", errors="replace").strip()  # descarta SW
        except Exception:
            try:
                texto = back_bytes.decode("latin-1", errors="replace").strip()
            except Exception:
                return []
        partes = [p.strip() for p in texto.split(",")]
        return partes

    def _validar_trama_ct(self, partes, folio_venta_digital):
        try:
            if len(partes) < 6 or partes[0] != "CT":
                logger.error("Trama CT inválida")
                return None
            if partes[5] != str(folio_venta_digital):
                logger.warning(f"Folio de venta digital no coincide: {partes[5]} != {folio_venta_digital}")
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
        except Exception as e:
            logger.error(f"Error al validar la trama CT: {e}")
            return None

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
            try:
                vg.pn532_release()
            except Exception:
                pass
            vg.modo_nfcCard = True
            logger.info("HCEWorker: fin del hilo run().")

    def stop(self):
        self.running = False
        try:
            self.mutex.lock()
            self.cond.wakeAll()
            self.mutex.unlock()
        except Exception:
            pass
        try:
            self.quit()
            self.wait(1500)
        finally:
            try:
                vg.pn532_release()
            except Exception:
                pass
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

        self.btn_cancelar.clicked.connect(self.cancelar_transaccion)

        # (Sin inicializar GPIO aquí; el buzzer se maneja vía GPIOHub desde el worker)
        self.worker = None

    def cancelar_transaccion(self):
        logger.info("Transacción HCE cancelada por el usuario.")
        self.exito_pago = {'hecho': False, 'pagado_efectivo': False, 'folio': None, 'fecha': None, 'hora': None}
        if self.worker:
            try:
                self.worker.stop()
            except Exception as e:
                logger.debug(f"cancelar_transaccion stop error: {e}")
            self.worker = None
        vg.modo_nfcCard = True
        self.close()

    def pn532_hard_reset(self):
        try:
            logger.info("Reset PN532 solicitado desde UI")
            with vg.pn532_lock:
                if self.worker and getattr(self.worker, "nfc", None):
                    self.worker.nfc.hard_reset()
        except Exception as e:
            logger.error(f"Error al resetear el lector NFC: {e}")

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
                    if self.worker and getattr(self.worker, "nfc", None):
                        self.worker.nfc.hard_reset()
                finally:
                    lock.release()
            else:
                vg.pn532_reset_requested = True
        except Exception as e:
            logger.error(f"Reset PN532 diferido falló: {e}")

        vg.modo_nfcCard = True
        vg.pn532_reset_requested = True
        self.close()

    def mostrar_y_esperar(self):
        self.label_info.setText(f"Esperando cobros 1 de {self.total_hce}")
        self.iniciar_hce()
        self.show()
        self.loop.exec_()
        return self.exito_pago

    def iniciar_hce(self):
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
        vg.pn532_reset_requested = True
        self.loop.quit()
        event.accept()