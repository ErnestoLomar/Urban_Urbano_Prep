##########################################
# Autor: Ernesto Lomar
# Fecha de creación: 12/04/2022
# Última modificación: 24/11/2025
#
# Lector de tarjetas + QR separados en hilos independientes.
# - LeerTarjetaWorker: loop NFC (tarjetas) únicamente.
# - QrReaderWorker: loop QR en su propio QThread.
#
# Mantiene tu lógica original, evitando que errores/continues del NFC
# bloqueen la lectura de QR.
##########################################

# Librerías externas
from PyQt5.QtCore import QObject, pyqtSignal, QSettings, QThread, pyqtSlot, Qt
import time
import ctypes
import serial
import logging
from time import strftime
from datetime import datetime, timedelta
import subprocess
import threading
import atexit

# Hub de GPIO (BCM)
from gpio_hub import GPIOHub, PINMAP

# Librerías propias
from matrices_tarifarias import obtener_destino_de_servicios_directos, obtener_destino_de_transbordos
from ventas_queries import (
    insertar_item_venta,
    obtener_ultimo_folio_de_item_venta,
    guardar_venta_digital,
    obtener_ultimo_folio_de_venta_digital,
    actualizar_estado_venta_digital_revisado,
)
from queries import obtener_datos_aforo, insertar_estadisticas_boletera
from tickets_usados import insertar_ticket_usado, verificar_ticket_completo, verificar_ticket
import variables_globales as vg

# ---------- Estado global para coordinación con HCE ----------
setattr(vg, "nfc_closed_for_hce", False)

# -------------------- Política de reset NFC --------------------
_NFC_MAX_FALLOS_CONSECUTIVOS = 3
_NFC_RESET_COOLDOWN_S = 10.0

# -------------------- Política de re-lectura de QR -------------
_QR_COOLDOWN_S = 2.0   # segundos para ignorar el MISMO QR

# -------------------- Hub GPIO compartido ----------------------
HUB = GPIOHub(PINMAP)

def _cleanup_gpio():
    try:
        HUB.safe_state()
    finally:
        HUB.close()

atexit.register(_cleanup_gpio)


# ==============================================================#
#               WORKER EXCLUSIVO PARA QR (HILO PROPIO)          #
# ==============================================================#
class QrReaderWorker(QObject):
    """Loop exclusivo para QR en su propio QThread, para no depender del NFC."""

    # Señal para pedir emergentes en el hilo principal: título, mensaje, duración (s)
    mostrar_mensaje = pyqtSignal(str, str, float)

    def __init__(self, hub: GPIOHub, id_unidad: str):
        super().__init__()
        # QSettings propio por hilo
        self.settings = QSettings('/home/pi/Urban_Urbano/ventanas/settings.ini', QSettings.IniFormat)
        self.hub = hub
        self.idUnidad = id_unidad
        self.ser = None
        self._running = True
        self.ultimo_qr = ""
        self.ultimo_qr_ts = 0.0  # timestamp de la última vez que se procesó ese QR

    # ---------- ciclo de vida ----------
    def start(self):
        """Arranca el bucle QR (se llama desde QThread.started)."""
        try:
            self.ser = serial.Serial(port='/dev/ttyACM0', baudrate=115200, timeout=1)
            print("QR: puerto abierto")
        except Exception as e:
            print("QR: no se pudo abrir puerto al inicio:", e)
            try:
                self.ser = serial.Serial()  # placeholder cerrado
            except Exception:
                self.ser = None
        self.run()

    def stop(self):
        self._running = False
        try:
            if self.ser and self.ser.is_open:
                self.ser.close()
        except Exception:
            pass

    # ---------- helpers ----------
    def _restablecer_puerto(self):
        try:
            time.sleep(1)
            if self.ser and self.ser.is_open:
                print("QR: cerrando puerto")
                try:
                    self.ser.close()
                except Exception:
                    pass
            # reintentos hasta abrir
            while self._running and (not self.ser or not self.ser.is_open):
                try:
                    print("QR: intentando abrir puerto /dev/ttyACM0")
                    time.sleep(2)
                    self.ser = serial.Serial(port='/dev/ttyACM0', baudrate=115200, timeout=1)
                    print("QR: puerto restablecido")
                except Exception:
                    pass
        except Exception as e:
            print("QR: error restableciendo puerto:", e)
            logging.info(e)

    def _emit_mensaje(self, titulo: str, cuerpo: str, segundos: float):
        """Emite señal para mostrar una ventana emergente desde el hilo principal."""
        try:
            self.mostrar_mensaje.emit(titulo or "", cuerpo or "", float(segundos))
        except Exception as e:
            logging.info(e)

    # ---------- bucle principal QR ----------
    def run(self):
        try:
            while self._running:
                # asegurar puerto
                if not (self.ser and self.ser.is_open):
                    self._restablecer_puerto()
                    continue

                try:
                    qr_bytes = self.ser.readline()
                except Exception as e:
                    print("QR: error al leer:", e)
                    logging.info(e)
                    self._restablecer_puerto()
                    continue

                qr_str_raw = (qr_bytes or b"").decode(errors="ignore")
                qr_str_raw = qr_str_raw.strip()
                if not qr_str_raw:
                    continue

                # ------------------------------------------------------------------
                # Normalizar: si vienen varios QRs "PD,..." pegados, nos quedamos
                # con el último candidato completo (o el último razonable).
                # ------------------------------------------------------------------
                qr_str = qr_str_raw
                if "PD," in qr_str_raw:
                    partes = qr_str_raw.split("PD,")
                    candidatos = []
                    for p in partes:
                        p = p.strip()
                        if not p:
                            continue
                        cand = "PD," + p
                        candidatos.append(cand)

                    if candidatos:
                        elegido = None
                        # buscamos desde el último hacia atrás uno que parezca completo
                        for cand in reversed(candidatos):
                            # 13 campos => al menos 12 comas
                            if cand.count(",") >= 12:
                                elegido = cand
                                break
                        if elegido is None:
                            # si ninguno cumple, nos quedamos con el último por compatibilidad
                            elegido = candidatos[-1]
                        qr_str = elegido
                    else:
                        qr_str = qr_str_raw

                now = time.monotonic()

                # Filtro de relecturas del MISMO QR muy seguidas
                if qr_str == getattr(self, "ultimo_qr", ""):
                    # Si es el mismo QR y pasó poco tiempo, lo ignoramos por completo
                    if (now - getattr(self, "ultimo_qr_ts", 0.0)) < _QR_COOLDOWN_S:
                        # Solo lo ignoramos, sin ventana ni beep
                        continue
                    else:
                        # Si ya pasó el cooldown, lo tratamos como "UTILIZADO"
                        print("El ultimo QR se vuelve a pasar")
                        self._emit_mensaje("UTILIZADO", ".....", 4.5)
                        self.hub.buzzer_blinks(5, on_ms=55, off_ms=55)
                        time.sleep(4.5)
                        self.ultimo_qr = qr_str
                        self.ultimo_qr_ts = time.monotonic()
                        continue

                # ---- LÓGICA ORIGINAL DE QR (idéntica) ----
                try:
                    print("El QR es:", qr_str)

                    if str(self.settings.value('folio_de_viaje')) == "":
                        print("No hay ningún viaje activo")
                        self.hub.buzzer_blinks(5, on_ms=55, off_ms=55)
                        time.sleep(1)
                        continue

                    qr_list = [p.strip() for p in qr_str.split(",")]
                    print("El tamaño del QR es:", len(qr_list))

                    # --- NUEVO FORMATO PD ---
                    if len(qr_list) >= 1 and qr_list[0] == "PD":
                        print("El QR es Nuevo")
                        if len(qr_list) != 13:
                            print("El QR digital no es válido")
                            self._emit_mensaje("INVALIDO", "", 4.5)
                            self.hub.buzzer_blinks(5, on_ms=55, off_ms=55)
                            time.sleep(4.5)
                            continue

                        print("El QRList es:", qr_list)

                        _, unidad_qr, fecha_qr, hora_qr, id_tarifa, origen, destino, tipo_de_pasajero, servicio_qr, id_monedero, saldo_posterior, precio, tipo_transaccion = qr_list

                        fecha_hoy = strftime('%d-%m-%Y').replace('/', '-')
                        if fecha_hoy != fecha_qr:
                            self._emit_mensaje("CADUCO", "Fecha diferente", 4.5)
                            print("La fecha del QR es diferente a la fecha actual")
                            self.hub.buzzer_blinks(5, on_ms=55, off_ms=55)
                            time.sleep(4.5)
                            continue

                        print("Fecha valida")

                        en_geocerca = False
                        try:
                            geo_actual = str(str(vg.geocerca.split(",")[1]).split("_")[0])
                            origen_norm = str(origen).split("_")[0]
                            if origen_norm == geo_actual:
                                en_geocerca = True
                        except Exception as e:
                            print("Error en geocerca: ", e)
                            logging.info(e)

                        if not en_geocerca:
                            self._emit_mensaje("EQUIVOCADO", str(origen), 4.5)
                            self.hub.buzzer_blinks(5, on_ms=55, off_ms=55)
                            time.sleep(4.5)
                            print("La geocerca no es valida")
                            continue

                        print("Geocerca valida")

                        es_ticket_usado = verificar_ticket_completo(qr_str)
                        if es_ticket_usado is not None:
                            self._emit_mensaje("UTILIZADO", ".....", 4.5)
                            self.hub.buzzer_blinks(5, on_ms=55, off_ms=55)
                            time.sleep(4.5)
                            print("El ticket ya fue usado")
                            # marcamos también como último QR para que el cooldown funcione
                            self.ultimo_qr = qr_str
                            self.ultimo_qr_ts = time.monotonic()
                            continue

                        print("Ticket valido")

                        servicio = servicio_qr
                        if not servicio:
                            try:
                                for servicio_vg in vg.todos_los_servicios_activos:
                                    if str(destino) in str(servicio_vg[2]):
                                        servicio = str(servicio_vg[5]) + "-" + str(str(servicio_vg[1]).split("_")[0]) + "-" + str(str(servicio_vg[2]).split("_")[0])
                                        break
                                if not servicio:
                                    for transbordo in vg.todos_los_transbordos_activos:
                                        if str(destino) in str(transbordo[2]):
                                            servicio = str(transbordo[5]) + "-" + str(str(transbordo[1]).split("_")[0]) + "-" + str(str(transbordo[2]).split("_")[0])
                                            break
                            except Exception as e:
                                print("Error al obtener el servicio: ", e)
                                logging.info(e)

                        print("Servicio valido")

                        usted_se_dirige = str(servicio).split("-")[2] if servicio else ""
                        print("Usted se dirige:", usted_se_dirige)

                        try:
                            ultimo = obtener_ultimo_folio_de_venta_digital() or (None, 0)
                            print("Ultimo folio digital:", ultimo)
                            folio_venta_digital = (ultimo[1] if isinstance(ultimo, (list, tuple)) and len(ultimo) > 1 else 0) + 1
                            print("Folio digital:", folio_venta_digital)
                            logging.info(f"Folio digital: {folio_venta_digital}")
                        except Exception as e:
                            logging.info(e)
                            print("Error al obtener el folio digital: ", e)
                            folio_venta_digital = 1

                        print("Folio valido")

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
                                "n",
                                tipo_transaccion,
                                id_monedero,
                                saldo_posterior,
                                precio
                            )
                        except Exception as e:
                            logging.info(e)
                            print("Error al guardar la venta digital: ", e)
                            venta_guardada = None

                        if venta_guardada:
                            try:
                                insertar_ticket_usado(qr_str)
                            except Exception as e:
                                logging.info(e)
                            try:
                                self.ultimo_qr = qr_str
                                self.ultimo_qr_ts = time.monotonic()
                                self.settings.setValue('total_de_folios', f"{int(self.settings.value('total_de_folios')) + 1}")
                            except Exception as e:
                                print("Error al actualizar el ultimo QR: ", e)
                                logging.info(e)

                            actualizar_estado_venta_digital_revisado("OK", folio_venta_digital, folio_asignacion)
                            print("Estado de venta actualizado a OK.")

                            self._emit_mensaje("ACEPTADO", usted_se_dirige if usted_se_dirige else "No encontrado", 5.0)
                        else:
                            self.hub.buzzer_blinks(5, on_ms=55, off_ms=55)
                            print("Error al guardar la venta digital")
                            time.sleep(0.5)
                        continue  # fin PD

                    print("Formato anterior")

                    # --- FORMATO ANTERIOR (9/10) ---
                    if len(qr_list) not in (9, 10):
                        self._emit_mensaje("INVALIDO", "", 4.5)
                        self.hub.buzzer_blinks(5, on_ms=55, off_ms=55)
                        time.sleep(4.5)
                        continue

                    fecha_qr = qr_list[0]
                    fecha_hoy = strftime('%d-%m-%Y').replace('/', '-')
                    if fecha_hoy != fecha_qr:
                        self._emit_mensaje("CADUCO", "Fecha diferente", 4.5)
                        self.hub.buzzer_blinks(5, on_ms=55, off_ms=55)
                        time.sleep(4.5)
                        continue

                    hora_caduca = qr_list[1]
                    hora_actual = strftime("%H:%M:%S")
                    if hora_actual > hora_caduca:
                        self._emit_mensaje("CADUCO", str(hora_caduca), 4.5)
                        self.hub.buzzer_blinks(5, on_ms=55, off_ms=55)
                        time.sleep(4.5)
                        continue

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

                    en_geocerca = False
                    try:
                        doble_tarnsbordo_o_no = str(qr_list[7])
                        geo_actual = str(str(vg.geocerca.split(",")[1]).split("_")[0])
                        if doble_tarnsbordo_o_no == "st":
                            if geo_actual in str(qr_list[8]):
                                en_geocerca = True
                        else:
                            if geo_actual in str(qr_list[8]) or (len(qr_list) > 9 and geo_actual in str(qr_list[9])):
                                en_geocerca = True
                    except Exception as e:
                        logging.info(e)

                    if not en_geocerca:
                        if doble_tarnsbordo_o_no == "st":
                            destino_esperado = str(qr_list[8])
                        else:
                            destino_esperado = f"{qr_list[8]} o {qr_list[9]}" if len(qr_list) > 9 else str(qr_list[8])

                        self._emit_mensaje("EQUIVOCADO", destino_esperado, 4.5)
                        self.hub.buzzer_blinks(5, on_ms=55, off_ms=55)
                        time.sleep(4.5)
                        continue

                    es_ticket_usado = verificar_ticket_completo(qr_str)
                    if es_ticket_usado is not None:
                        self._emit_mensaje("UTILIZADO", ".....", 4.5)
                        self.hub.buzzer_blinks(5, on_ms=55, off_ms=55)
                        time.sleep(4.5)
                        # marcamos QR para que el cooldown lo ignore en relecturas
                        self.ultimo_qr = qr_str
                        self.ultimo_qr_ts = time.monotonic()
                        continue

                    try:
                        from impresora import imprimir_boleto_normal_sin_servicio, imprimir_boleto_normal_con_servicio
                    except Exception as e:
                        logging.info(e)

                    servicio = ""
                    usted_se_dirige = ""
                    destino = str(tramo).split("-")[1] if "-" in str(tramo) else str(tramo)

                    if doble_tarnsbordo_o_no == "st":
                        for servicio_vg in vg.todos_los_servicios_activos:
                            if str(destino) in str(servicio_vg[2]):
                                servicio = str(servicio_vg[5]) + "-" + str(str(servicio_vg[1]).split("_")[0]) + "-" + str(str(servicio_vg[2]).split("_")[0])
                    else:
                        for transbordo in vg.todos_los_transbordos_activos:
                            if str(destino) in str(transbordo[2]):
                                servicio = str(transbordo[5]) + "-" + str(str(transbordo[1]).split("_")[0]) + "-" + str(str(transbordo[2]).split("_")[0])

                    ultimo_folio_de_venta = obtener_ultimo_folio_de_item_venta()
                    if ultimo_folio_de_venta is not None:
                        if int(self.settings.value('reiniciar_folios')) == 0:
                            ultimo_folio_de_venta = int(ultimo_folio_de_venta[1]) + 1
                        else:
                            ultimo_folio_de_venta = 1
                            self.settings.setValue('reiniciar_folios', 0)
                    else:
                        ultimo_folio_de_venta = 1

                    hecho = False
                    if servicio != "":
                        usted_se_dirige = str(servicio).split("-")[2]
                        hecho = imprimir_boleto_normal_con_servicio(
                            ultimo_folio_de_venta, fecha_hoy, hora_actual, self.idUnidad, servicio, tramo, qr_list
                        )
                    else:
                        hecho = imprimir_boleto_normal_sin_servicio(
                            ultimo_folio_de_venta, fecha_hoy, hora_actual, self.idUnidad, tramo, qr_list
                        )

                    if hecho:
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

                        self.ultimo_qr = qr_str
                        self.ultimo_qr_ts = time.monotonic()
                        self.settings.setValue('total_de_folios', f"{int(self.settings.value('total_de_folios')) + 1}")
                        insertar_ticket_usado(qr_str)

                        self._emit_mensaje("ACEPTADO", usted_se_dirige if usted_se_dirige != "" else "No encontrado", 5.0)
                    else:
                        self.hub.buzzer_blinks(5, on_ms=55, off_ms=55)
                        time.sleep(0.5)

                except Exception as e:
                    print("QR: excepción general:", e)
                    logging.info(e)

        except Exception as e:
            print("QR loop aborted:", e)
            logging.info(e)


# ==============================================================#
#                  WORKER NFC (TARJETAS) ÚNICAMENTE             #
# ==============================================================#
class LeerTarjetaWorker(QObject):

    try:
        finished = pyqtSignal()
        progress = pyqtSignal(str)
        mensaje = pyqtSignal(str, str, float)
    except Exception as e:
        print(e)
        logging.info(e)

    def __init__(self):
        super().__init__()
        self.ultimo_qr = ""
        self.hub = HUB

        self._nfc_fallos = 0
        self._nfc_ultimo_reset_ts = 0.0

        try:
            self.hub.write("buzzer", False)
            self.hub.write("nfc_rst", False)
        except Exception as e:
            print("\x1b[1;31;47m" + "No se pudo inicializar GPIO via hub: " + str(e) + '\033[0;m')
            logging.info(e)

        try:
            self.lib = ctypes.cdll.LoadLibrary('/home/pi/Urban_Urbano/qworkers/libernesto.so')

            self.lib.ev2IsPresent.restype = ctypes.c_void_p
            self.lib.tipoTiscEV2.restype = ctypes.c_void_p
            self.lib.obtenerVigencia.restype = ctypes.c_void_p
            self.lib.ev2PackInfo.argtypes = []
            self.lib.ev2PackInfo.restype = ctypes.c_void_p

            self.lib.free_str.argtypes = [ctypes.c_void_p]
            self.lib.free_str.restype = None

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

        # Reset inicial (si no logra lock, lo difiere)
        self.pn532_hard_reset()

        # Hilo QR igual que antes
        self.qr_thread = QThread(self)
        self.qr_worker = QrReaderWorker(self.hub, self.idUnidad)
        self.qr_worker.moveToThread(self.qr_thread)
        self.qr_worker.mostrar_mensaje.connect(self.reenviar_mensaje, Qt.DirectConnection)
        self.qr_thread.started.connect(self.qr_worker.start)
        self.qr_thread.start()

    @pyqtSlot(str, str, float)
    def reenviar_mensaje(self, titulo, cuerpo, segundos):
        try:
            self.mensaje.emit(titulo, cuerpo, segundos)
        except Exception as e:
            logging.info(e)

    def pn532_hard_reset(self):
        """Reset físico PN532. Si está ocupado, pide reset diferido."""
        # si otro dueño tiene el PN532, no fuerces: pide reset y sal
        if not vg.pn532_acquire("RESET", timeout=0.25):
            vg.pn532_request_reset()
            return

        try:
            print("\x1b[1;32m" + "Hard reset PN532" + '\033[0;m')

            # cerrar sesión C (rápido) antes de reset
            def _try_close():
                try:
                    if hasattr(self, "lib") and self.lib and hasattr(self.lib, "nfc_close_all"):
                        self.lib.nfc_close_all()
                except Exception:
                    pass

            t = threading.Thread(target=_try_close, daemon=True)
            t.start()
            t.join(0.15)

            self.hub.pulse("nfc_rst", 400)
            time.sleep(0.60)

        except Exception as e:
            print("\x1b[1;31;47m" + "Error al resetear el lector NFC: " + str(e) + '\033[0;m')
            logging.error(f"Error al resetear el lector NFC: {e}")
        finally:
            vg.pn532_release()

    def _maybe_reset_nfc(self):
        now = time.monotonic()
        if self._nfc_fallos >= _NFC_MAX_FALLOS_CONSECUTIVOS and (now - self._nfc_ultimo_reset_ts) >= _NFC_RESET_COOLDOWN_S:
            self.pn532_hard_reset()
            self._nfc_ultimo_reset_ts = now
            self._nfc_fallos = 0

    def _cstr(self, ptr):
        if not ptr:
            return ""
        try:
            return ctypes.string_at(ptr).decode("utf-8", "ignore")
        finally:
            try:
                self.lib.free_str(ptr)
            except Exception:
                logging.error("Error al liberar memoria asignada por la lib")

    def _campo_invalido(self, valor):
        v = (valor or "").strip().upper()
        return v in ("IN", "INVALID", "INVALIDO", "ERROR")

    def run(self):
        try:
            poll_interval = 0.10
            while True:
                start = time.monotonic()

                # reset solicitado globalmente
                if vg.pn532_consume_reset_flag():
                    self.pn532_hard_reset()
                    self._nfc_fallos = 0

                # si volvió a modo lector, limpia latch
                if vg.modo_nfcCard and vg.nfc_closed_for_hce:
                    vg.nfc_closed_for_hce = False

                try:
                    if vg.modo_nfcCard:
                        # lectura CARD bajo lock
                        if not vg.pn532_acquire("CARD", timeout=0.2):
                            time.sleep(0.02)
                            continue

                        try:
                            pack = self._cstr(self.lib.ev2PackInfo())
                        except Exception as e:
                            logging.info(f"ev2PackInfo error: {e}")
                            self._nfc_fallos += 1
                            self._maybe_reset_nfc()
                            time.sleep(0.02)
                            continue
                        finally:
                            vg.pn532_release()

                        if not pack:
                            time.sleep(0.02)
                            continue

                        try:
                            csn, tipo, vig, nombre = pack.split("|", 3)
                        except ValueError:
                            logging.info(f"Paquete NFC inválido: {pack!r}")
                            self._nfc_fallos += 1
                            self._maybe_reset_nfc()
                            time.sleep(0.02)
                            continue

                        csn = (csn or "").strip()
                        tipo = (tipo or "").strip()
                        vig = (vig or "").strip()
                        nombre = (nombre or "").strip()

                        if any(self._campo_invalido(f) for f in (csn, tipo, vig, nombre)):
                            self._nfc_fallos += 1
                            self._maybe_reset_nfc()
                            try:
                                self.mensaje.emit("TARJETAINVALIDA", "", 2.0)
                            except Exception:
                                pass
                            self.hub.buzzer_blinks(5, on_ms=55, off_ms=55)
                            time.sleep(2)
                            continue

                        if self._nfc_fallos:
                            self._nfc_fallos = 0

                        # Fecha y hora de la boletera
                        fecha = strftime('%Y/%m/%d').replace('/', '')[2:]
                        fecha_actual = str(subprocess.run("date", stdout=subprocess.PIPE, shell=True))
                        indice = fecha_actual.find(":")
                        hora = str(fecha_actual[(int(indice) - 2):(int(indice) + 6)]).replace(":", "")

                        try:
                            tipo2 = (tipo or "")[:2]  # "KI", "DE", "IN", etc.
                            if tipo2 == "KI":
                                vig = vig or ""
                                nombre_limpio = (nombre or "").replace("*", " ").replace(".", " ").replace("-", " ").replace("_", " ")
                                datos_completos_tarjeta = f"{vig}{nombre}"
                                vigenciaTarjeta = vig[:12]  # YYMMDDhhmmss
                                print("Datos completos de la tarjeta: ", datos_completos_tarjeta)

                                # Validación de vigencia
                                if len(vigenciaTarjeta) == 12 and vigenciaTarjeta[:2].isdigit() and int(vigenciaTarjeta[:2]) >= 22:
                                    now_dt = datetime.now()
                                    vigenciaActual = f'{str(now_dt.strftime("%Y-%m-%d %H:%M:%S"))[2:].replace(" ", "").replace("-", "").replace(":", "")}'
                                    if vigenciaActual <= vigenciaTarjeta:
                                        if len(csn) == 14:
                                            vg.vigencia_de_tarjeta = vigenciaTarjeta
                                            num_operador = vig[12:17] if len(vig) >= 17 else ""

                                            if str(self.settings.value('ventana_actual')) not in ("chofer", "corte", "enviar_vuelta", "cerrar_turno"):
                                                if len(vg.numero_de_operador_inicio) > 0 or len(self.settings.value('numero_de_operador_inicio')) > 0:
                                                    vg.numero_de_operador_final = num_operador
                                                    vg.nombre_de_operador_final = nombre_limpio
                                                    self.settings.setValue('numero_de_operador_final', f"{num_operador}")
                                                    self.settings.setValue('nombre_de_operador_final', f"{nombre_limpio}")
                                                else:
                                                    vg.numero_de_operador_inicio = num_operador
                                                    vg.nombre_de_operador_inicio = nombre_limpio
                                                    self.settings.setValue('numero_de_operador_inicio', f"{num_operador}")
                                                    self.settings.setValue('nombre_de_operador_inicio', f"{nombre_limpio}")

                                            vg.csn_chofer_respaldo = csn
                                            self.progress.emit(csn)
                                            self.hub.buzzer_blinks(2, on_ms=100, off_ms=100)
                                        else:
                                            try:
                                                self.mensaje.emit("TARJETAINVALIDA", "", 2.0)
                                            except Exception as e:
                                                logging.info(e)
                                            self.hub.buzzer_blinks(5, on_ms=55, off_ms=55)
                                            time.sleep(2)
                                    else:
                                        insertar_estadisticas_boletera(str(self.idUnidad), fecha, hora, "SV", f"{csn}")
                                        try:
                                            self.mensaje.emit("FUERADEVIGENCIA", "", 2.0)
                                        except Exception as e:
                                            logging.info(e)
                                        self.hub.buzzer_blinks(5, on_ms=55, off_ms=55)
                                        time.sleep(2)
                                else:
                                    insertar_estadisticas_boletera(str(self.idUnidad), fecha, hora, "TI", f"{csn},{vigenciaTarjeta}")
                                    try:
                                        self.mensaje.emit("TARJETAINVALIDA", "", 2.0)
                                    except Exception as e:
                                        logging.info(e)
                                    self.hub.buzzer_blinks(5, on_ms=55, off_ms=55)
                                    time.sleep(2)
                            else:
                                insertar_estadisticas_boletera(str(self.idUnidad), fecha, hora, "TD", f"{csn},{tipo}")
                                try:
                                    self.mensaje.emit("TARJETAINVALIDA", "", 2.0)
                                except Exception as e:
                                    logging.info(e)
                                self.hub.buzzer_blinks(5, on_ms=55, off_ms=55)
                                time.sleep(2)
                        except Exception as e:
                            print("\x1b[1;31;47mNo se pudo leer la tarjeta:", str(e), '\033[0;m')
                            logging.info(e)
                            self._nfc_fallos += 1
                            self._maybe_reset_nfc()
                            # no bloquea QR porque QR va en otro hilo
                            continue
                    else:
                        # Entraste a HCE: cerrar sesión C UNA SOLA VEZ y marcar latch real
                        if not vg.nfc_closed_for_hce:
                            if vg.pn532_acquire("CARD_CLOSE", timeout=0.4):
                                try:
                                    try:
                                        self.lib.nfc_close_all()
                                    except Exception as e:
                                        logging.info(f"nfc_close_all error: {e}")
                                    vg.nfc_closed_for_hce = True
                                finally:
                                    vg.pn532_release()
                                time.sleep(0.05)

                except Exception as e:
                    logging.info(e)

                elapsed = time.monotonic() - start
                if elapsed < poll_interval:
                    time.sleep(poll_interval - elapsed)

        except Exception as e:
            print(e)
            logging.info(e)
        finally:
            try:
                self.qr_worker.stop()
                self.qr_thread.quit()
                self.qr_thread.wait(1000)
            except Exception:
                pass

    # -------------------- Stop explícito (opcional) --------------------
    def stop_all(self):
        try:
            self.qr_worker.stop()
        except Exception:
            pass
        try:
            self.qr_thread.quit()
            self.qr_thread.wait(1000)
        except Exception:
            pass