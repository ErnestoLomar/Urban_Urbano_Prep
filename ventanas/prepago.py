# -*- coding: utf-8 -*-
"""
Ventana de prepago con HCE + PN532.
Diseño: header limpio (desde .ui) y uso de GIF para estado (cargando / pagado).
QMessageBox siempre al frente y modal.
"""

import sys
import time
import logging
from time import strftime

from PyQt5.QtWidgets import QMainWindow, QMessageBox
from PyQt5.QtCore import (
    QEventLoop, QTimer, QThread, pyqtSignal, QSettings,
    QWaitCondition, QMutex, Qt, QEvent
)
from PyQt5 import uic
from PyQt5.QtGui import QMovie, QPixmap

import board
from pn532_blinka_adapter import Pn532Blinka

import variables_globales as vg

sys.path.insert(1, '/home/pi/Urban_Urbano/db')
from ventas_queries import (
    guardar_venta_digital,
    obtener_ultimo_folio_de_venta_digital,
    actualizar_estado_venta_digital_revisado,
)

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

try:
    from gpio_hub import GPIOHub, PINMAP
    HUB = GPIOHub(PINMAP)
    logger.info("GPIOHub inicializado para buzzer/reset.")
except Exception as e:
    HUB = None
    logger.warning(f"No se pudo inicializar GPIOHub: {e}")

SETTINGS_PATH = "/home/pi/Urban_Urbano/ventanas/settings.ini"
UI_PATH = "/home/pi/Urban_Urbano/ui/prepago.ui"
GIF_CARGANDO = "/home/pi/Urban_Urbano/Imagenes/cargando.gif"
GIF_PAGADO   = "/home/pi/Urban_Urbano/Imagenes/pagado.gif"
PNG_FALLBACK = "/home/pi/Urban_Urbano/Imagenes/sin-contacto.png"

DETECCION_TIMEOUT_S = 1.2
DETECCION_INTERVALO_S = 0.005

PN532_INIT_REINTENTOS = 10
PN532_INIT_INTERVALO_S = 0.05

HCE_REINTENTOS = 12
HCE_REINTENTO_INTERVALO_S = 0.02

PN532_BACKOFF_INICIAL_S = 0.20
PN532_BACKOFF_MAX_S = 1.50

SELECT_AID_APDU = bytearray([
    0x00, 0xA4, 0x04, 0x00,
    0x07, 0xF0, 0x55, 0x72, 0x62, 0x54, 0x00, 0x41,
    0x00
])


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

        self.nfc = None
        self._have_lock = False
        
    def _reinit_post_reset(self) -> bool:
        """
        Después de un hard reset, vuelve a dejar PN532 listo (SAM + RF tune).
        Si falla, recrea la instancia.
        """
        try:
            if not self.nfc:
                self.nfc = Pn532Blinka(cs_pin=board.CE0)

            # begin() en tu adapter es no-op, pero lo dejamos por consistencia
            self.nfc.begin()

            # fuerza comunicación con chip
            _ = self.nfc.getFirmwareVersion()

            # SAMConfig también aplica RF tune en tu adapter
            self.nfc.SAMConfig()
            return True
        except Exception as e:
            logger.warning(f"Reinit post-reset falló: {e}. Re-creando PN532...")
            try:
                if self.nfc:
                    self.nfc.deinit()
            except Exception:
                pass
            self.nfc = None

            try:
                self.nfc = Pn532Blinka(cs_pin=board.CE0)
                self.nfc.begin()
                _ = self.nfc.getFirmwareVersion()
                self.nfc.SAMConfig()
                return True
            except Exception as e2:
                logger.error(f"No se pudo re-crear PN532: {e2}")
                return False

    def _hard_reset_hub(self):
        if not HUB:
            return
        try:
            logger.info("Hard reset PN532 (GPIOHub)")
            # active_high=False en tu hub: pulse usa tu lógica interna correcta
            HUB.pulse("nfc_rst", 400)
            time.sleep(0.60)
        except Exception as e:
            logger.error(f"Reset GPIOHub falló: {e}")

    def iniciar_hce(self):
        backoff = PN532_BACKOFF_INICIAL_S

        while self.running:
            try:
                self.nfc = Pn532Blinka(cs_pin=board.CE0)
            except Exception as e:
                self.error_inicializacion.emit(f"No se pudo abrir PN532: {e}. Reintentando…")
                time.sleep(backoff)
                backoff = min(backoff * 1.5, PN532_BACKOFF_MAX_S)
                continue

            try:
                self._hard_reset_hub()
            except Exception:
                pass

            ok = False
            for intento in range(PN532_INIT_REINTENTOS):
                if not self.running:
                    break
                try:
                    self.nfc.begin()
                    versiondata = self.nfc.getFirmwareVersion()
                    self.nfc.SAMConfig()
                    if versiondata:
                        logger.info(f"PN532 OK: {versiondata}")
                        ok = True
                        break
                except Exception as e:
                    self.error_inicializacion.emit(f"Inicializando lector ({intento+1}/{PN532_INIT_REINTENTOS})…")
                    try:
                        self._hard_reset_hub()
                    except Exception:
                        pass
                    time.sleep(PN532_INIT_INTERVALO_S)

            if ok:
                self.error_inicializacion.emit("Lector NFC listo.")
                return

            # Falló: deinit y backoff
            try:
                if self.nfc:
                    self.nfc.deinit()
            except Exception:
                pass
            self.nfc = None

            self.error_inicializacion.emit("El lector no responde. Reintentando…")
            time.sleep(backoff)
            backoff = min(backoff * 1.5, PN532_BACKOFF_MAX_S)

    def _buzzer_ok(self):
        try:
            if HUB:
                HUB.buzzer_beep(200)
        except Exception as e:
            logger.debug(f"Buzzer OK error: {e}")

    def _buzzer_error(self):
        try:
            if HUB:
                HUB.buzzer_blinks(n=5, on_ms=55, off_ms=55)
        except Exception as e:
            logger.debug(f"Buzzer ERR error: {e}")

    def _detectar_dispositivo(self, timeout_s=DETECCION_TIMEOUT_S, intervalo_s=DETECCION_INTERVALO_S):
        inicio = time.time()
        while self.running and (time.time() - inicio) < timeout_s:
            try:
                ok = self.nfc.inListPassiveTarget(timeout=0.12)
                if ok:
                    self.contador_sin_dispositivo = 0
                    return True
            except Exception as e:
                logger.debug(f"detección error: {e}")
            time.sleep(intervalo_s)
        return False

    def _select_aid_low(self):
        try:
            self.nfc.refresh_target(timeout=0.3)
        except Exception:
            pass

        time.sleep(0.04)

        try:
            ok, r = self.nfc.inDataExchange(SELECT_AID_APDU)
        except Exception as e:
            logger.error(f"SELECT low error: {e}")
            return False, b""

        if not ok or len(r) < 2:
            time.sleep(0.04)
            try:
                self.nfc.refresh_target(timeout=0.3)
                ok, r = self.nfc.inDataExchange(SELECT_AID_APDU)
            except Exception as e:
                logger.error(f"SELECT low retry error: {e}")
                return False, b""
        return ok, (r or b"")

    def _seleccionar_aid(self):
        ok, r = self._select_aid_low()
        if not ok or len(r) < 2:
            logger.info("SELECT AID sin respuesta válida")
            return False
        sw = r[-2:]
        logger.info(f"SELECT SW={sw.hex().upper()}  DATA={r[:-2].hex().upper() if len(r)>2 else ''}")
        return sw == b"\x90\x00"

    def _enviar_apdu(self, data_bytes, *, rearm=True):
        try:
            ok, resp = self.nfc.inDataExchange(bytearray(data_bytes))
            if ok and resp is not None:
                return True, (resp or b"")
        except Exception as e:
            logger.error(f"Error inDataExchange: {e}")

        if not rearm:
            return False, b""

        logger.info("Rearme ISO-DEP: re-detección y re-SELECT")
        if self._detectar_dispositivo(timeout_s=0.6):
            ok_sel, r_sel = self._select_aid_low()
            if ok_sel and len(r_sel) >= 2 and r_sel[-2:] == b"\x90\x00":
                try:
                    ok2, resp2 = self.nfc.inDataExchange(bytearray(data_bytes))
                    return ok2, (resp2 or b"")
                except Exception as e:
                    logger.error(f"Error inDataExchange tras rearme: {e}")
        return False, b""

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
        return [p.strip() for p in texto.split(",")]

    def _validar_trama_ct(self, partes, folio_venta_digital):
        try:
            if len(partes) < 6 or partes[0] != "CT":
                return None
            if partes[5] != str(folio_venta_digital):
                return None
            try:
                id_monedero = int(partes[2])
                no_transaccion = int(partes[3])
                saldo_posterior = float(partes[4])
                precio = float(partes[5])
                tipo_transaccion = partes[6]
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
                "precio": precio,
                "tipo_transaccion": tipo_transaccion
            }
        except Exception:
            return None

    def run(self):
        if not self.running:
            return

        # Dueño exclusivo del PN532 durante todo el HCE
        if not vg.pn532_acquire("HCE", timeout=3.0):
            self.error_inicializacion.emit("PN532 ocupado. No se pudo iniciar HCE.")
            return
        self._have_lock = True

        try:
            # Asegura que el hilo CARD haya cerrado su sesión antes de abrir Blinka
            # (no setees flags manualmente; esto lo pone LeerTarjetaWorker)
            vg.wait_nfc_closed_for_hce(timeout=1.2)

            self.iniciar_hce()
            if not self.running:
                return

            while self.pagados < self.total_hce and self.running:
                try:
                    # Reset solicitado globalmente (por UI, etc.)
                    if vg.pn532_consume_reset_flag():
                        self._hard_reset_hub()
                        try:
                            if self.nfc:
                                self.nfc.SAMConfig()
                        except Exception:
                            pass

                    if self.contador_sin_dispositivo >= 15:
                        self.pago_fallido.emit("Se va a resetear el lector")
                        self._hard_reset_hub()

                        # CLAVE: volver a dejar PN532 listo tras reset
                        if not self._reinit_post_reset():
                            self.pago_fallido.emit("No se pudo re-inicializar el PN532")
                            time.sleep(0.4)

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
                        ok_tx, back = self._enviar_apdu(trama_bytes)
                        if ok_tx:
                            break
                        self.pago_fallido.emit(
                            "El celular no responde (TRAMA) - intento: "
                            + str(intento) + "/" + str(HCE_REINTENTOS)
                        )
                        intento += 1
                        time.sleep(HCE_REINTENTO_INTERVALO_S)

                    if not ok_tx:
                        self.pago_fallido.emit("Error al recibir respuesta del celular (TRAMA)")
                        continue

                    partes = self._parsear_respuesta_celular(back)
                    datos = self._validar_trama_ct(partes, folio_venta_digital)
                    if not datos:
                        self.pago_fallido.emit("Respuesta inválida del celular")
                        continue

                    venta_guardada = guardar_venta_digital(
                        folio_venta_digital,
                        vg.folio_asignacion,
                        fecha,
                        hora,
                        self.id_tarifa,
                        self.geocerca,
                        self.tipo_pasajero,
                        self.servicio,
                        datos["tipo_transaccion"],
                        datos["id_monedero"],
                        datos["saldo_posterior"],
                        self.precio
                    )

                    if not venta_guardada:
                        self._buzzer_error()
                        time.sleep(1.5)
                        continue

                    actualizar_estado_venta_digital_revisado("OK", folio_venta_digital, vg.folio_asignacion)

                    self._buzzer_ok()
                    self.pagados += 1
                    self.actualizar_settings.emit({"setting_pasajero": self.setting_pasajero, "precio": self.precio})
                    self.pago_exitoso.emit({"estado": "OKDB", "folio": folio_venta_digital, "fecha": fecha, "hora": hora})
                    self.wait_for_ok.emit()

                    self.mutex.lock()
                    self.cond.wait(self.mutex)
                    self.mutex.unlock()
                    time.sleep(1)

                except Exception as e:
                    logger.exception(f"Excepción en ciclo de cobro: {e}")
                    self.pago_fallido.emit(str(e))
                    break

        finally:
            try:
                if self.nfc:
                    self.nfc.deinit()
            except Exception:
                pass
            self.nfc = None

            # al salir, regresamos a modo CARD y pedimos un reset para estado limpio
            vg.modo_nfcCard = True
            vg.pn532_request_reset()

            if self._have_lock:
                vg.pn532_release()
                self._have_lock = False

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
        except Exception:
            pass


class VentanaPrepago(QMainWindow):
    def __init__(self, tipo=None, tipo_num=None, setting=None, total_hce=1, precio=0.0, id_tarifa=None,
                 geocerca=None, servicio=None, origen=None, destino=None, parent=None):
        super().__init__(parent)
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.Dialog | Qt.WindowStaysOnTopHint)

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
        self.btn_cancelar.clicked.connect(self.cancelar_transaccion)

        self.label_tipo.setText(f"{self.tipo} - Precio: ${self.precio:.2f}")

        self._setup_movies()

        self.loop = QEventLoop()
        self.destroyed.connect(self.loop.quit)

        self.worker = None

    def _setup_movies(self):
        try:
            self.movie_loading = QMovie(GIF_CARGANDO)
            self.movie_loading.setCacheMode(QMovie.CacheAll)
            self.movie_loading.setSpeed(100)
        except Exception:
            self.movie_loading = None

        try:
            self.movie_success = QMovie(GIF_PAGADO)
            self.movie_success.setCacheMode(QMovie.CacheAll)
            self.movie_success.setSpeed(100)
        except Exception:
            self.movie_success = None

        self.label_icon.installEventFilter(self)
        self._apply_movie(self.movie_loading)

    def _apply_movie(self, movie):
        if movie and movie.isValid():
            movie.setScaledSize(self.label_icon.size())
            self.label_icon.setMovie(movie)
            movie.start()
        else:
            self.label_icon.setPixmap(QPixmap(PNG_FALLBACK))
            self.label_icon.setScaledContents(True)

    def eventFilter(self, obj, ev):
        if obj is self.label_icon and ev.type() == QEvent.Resize:
            for m in (getattr(self, "movie_loading", None), getattr(self, "movie_success", None)):
                if m and m.isValid():
                    m.setScaledSize(self.label_icon.size())
        return super().eventFilter(obj, ev)

    def cancelar_transaccion(self):
        self.exito_pago = {'hecho': False, 'pagado_efectivo': False, 'folio': None, 'fecha': None, 'hora': None}
        if self.worker:
            try:
                self.worker.stop()
            except Exception:
                pass
            self.worker = None
        vg.modo_nfcCard = True
        vg.pn532_request_reset()
        self.close()

    def pagar_con_efectivo(self):
        self.exito_pago = {'hecho': False, 'pagado_efectivo': True, 'folio': None, 'fecha': None, 'hora': None}
        if self.worker and self.worker.isRunning():
            self.worker.stop()
            self.worker = None
        QTimer.singleShot(0, self._finish_cash)

    def _finish_cash(self):
        vg.modo_nfcCard = True
        vg.pn532_request_reset()
        self.close()

    def mostrar_y_esperar(self):
        self.label_3.setText("Acerque el dispositivo para realizar el cobro")
        self.label_info.setText("")
        self.iniciar_hce()
        self.show()
        self.loop.exec_()
        return self.exito_pago

    def iniciar_hce(self):
        vg.modo_nfcCard = False

        self.worker = HCEWorker(
            self.total_hce, self.precio, self.tipo_num, self.id_tarifa,
            self.geocerca, self.servicio, self.setting, self.origen, self.destino
        )
        self.worker.pago_exitoso.connect(self.pago_exitoso)
        self.worker.pago_fallido.connect(self.pago_fallido)
        self.worker.actualizar_settings.connect(self._actualizar_totales_settings)
        self.worker.error_inicializacion.connect(self.error_inicializacion_nfc)
        self.worker.wait_for_ok.connect(self.mostrar_mensaje_exito_bloqueante)
        self.worker.start()

    def error_inicializacion_nfc(self, mensaje):
        self.label_info.setStyleSheet("color: red;")
        self.label_info.setText(mensaje)
        self._apply_movie(self.movie_loading)

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
        self.label_info.setStyleSheet("color: green;")
        self.label_info.setText(f"Pagado {self.pagados}/{self.total_hce}")
        self._apply_movie(self.movie_success)

        if self.pagados >= self.total_hce:
            self.exito_pago = {'hecho': True, 'pagado_efectivo': False, 'folio': data['folio'], 'fecha': data['fecha'], 'hora': data['hora']}
            QTimer.singleShot(1200, self.close)
        else:
            QTimer.singleShot(1200, self.restaurar_cargando)

    def mostrar_mensaje_exito_bloqueante(self):
        msg = QMessageBox(self)
        msg.setIcon(QMessageBox.Information)
        msg.setWindowTitle("Pago Exitoso")
        msg.setText("El pago se realizó exitosamente.")
        msg.setStandardButtons(QMessageBox.Ok)
        msg.setWindowModality(Qt.ApplicationModal)
        msg.setWindowFlags(msg.windowFlags() | Qt.WindowStaysOnTopHint)
        msg.raise_(); msg.activateWindow()
        msg.exec_()
        if self.worker:
            self.worker.mutex.lock()
            self.worker.cond.wakeAll()
            self.worker.mutex.unlock()

    def restaurar_cargando(self):
        self.label_info.setStyleSheet("color: black;")
        self.label_3.setText("Acerque el dispositivo para realizar el cobro")
        self._apply_movie(self.movie_loading)

    def pago_fallido(self, mensaje):
        self.label_info.setStyleSheet("color: red;")
        self.label_info.setText(mensaje)
        self._apply_movie(self.movie_loading)

    def closeEvent(self, event):
        try:
            if self.worker:
                self.worker.stop()
        except Exception:
            pass

        for m in (getattr(self, "movie_loading", None), getattr(self, "movie_success", None)):
            try:
                if m: m.stop()
            except Exception:
                pass
        try:
            self.label_icon.clear()
        except Exception:
            pass

        vg.modo_nfcCard = True
        vg.pn532_request_reset()
        self.loop.quit()
        event.accept()