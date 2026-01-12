##########################################
# Autor: Ernesto Lomar
# Fecha de creación: 26/04/2022
# Ultima modificación: 16/08/2022
##########################################

version_del_software = "EL.v3.72"
banderaServicio=False
longitud = 0
latitud = 0
signal = 0
connection_3g = "error"
GPS = 'error'
velocidad = 0
servicio = ""
vuelta = 0
pension = ""
csn_chofer = ""
conexion_servidor = "NO"
geocerca = "0,''"
folio_asignacion = 0
estado_del_software = ""
distancia_minima = 0.003
todos_los_servicios_activos = []
todos_los_transbordos_activos = []
vendiendo_boleto = False
detectando_geocercas_hilo = True
terminar_hilo_verificar_datos = False
vigencia_de_tarjeta = ""
numero_de_operador_inicio = ""
nombre_de_operador_inicio = ""
numero_de_operador_final = ""
nombre_de_operador_final = ""
csn_chofer_respaldo = ""
sim_id = ""
version_de_MT = "202305180001"
fecha_actual = ""
hora_actual = ""
fecha_completa_actual = ""

# Modo NFC:
# True  -> modo "Card": lectura tarjetas (lib C)
# False -> modo HCE: cobro celular (Blinka)
modo_nfcCard = True

# Latch que SOLO debe setear el hilo de tarjetas cuando realmente cerró su sesión NFC
nfc_closed_for_hce = False

from enum import Enum
class VentanaActual(Enum):
  CHOFER = 'chofer',
  CERRAR_VUELTA = 'cerrar_vuelta',
  CERRAR_TURNO = 'cerrar_turno',

ventana_actual = VentanaActual.CHOFER

# ---- Arbitraje PN532 (único) ----
import threading, time

pn532_lock = threading.RLock()
pn532_owner = None
pn532_depth = 0  # para tracking de re-entradas por mismo owner

def pn532_acquire(owner: str, timeout: float = 3.0) -> bool:
    """Toma el lock global del PN532 con timeout. Trackea owner/depth."""
    global pn532_owner, pn532_depth
    t0 = time.time()

    # Si el mismo owner re-entra, permitimos (RLock) y llevamos depth
    if pn532_owner == owner:
        pn532_lock.acquire()
        pn532_depth += 1
        return True

    while time.time() - t0 < timeout:
        if pn532_lock.acquire(blocking=False):
            pn532_owner = owner
            pn532_depth = 1
            return True
        time.sleep(0.005)
    return False

def pn532_release():
    """Libera el lock global del PN532, respetando depth."""
    global pn532_owner, pn532_depth
    try:
        if pn532_depth > 1:
            pn532_depth -= 1
            pn532_lock.release()
            return
        pn532_owner = None
        pn532_depth = 0
        pn532_lock.release()
    except Exception:
        pn532_owner = None
        pn532_depth = 0

# Señal de reset solicitada; la consume el dueño del lock (CARD o HCE)
pn532_reset_requested = False

def pn532_request_reset():
    global pn532_reset_requested
    pn532_reset_requested = True

def pn532_consume_reset_flag() -> bool:
    global pn532_reset_requested
    if pn532_reset_requested:
        pn532_reset_requested = False
        return True
    return False

def wait_nfc_closed_for_hce(timeout: float = 1.2, interval: float = 0.02) -> bool:
    """Espera a que el hilo de tarjetas cierre su sesión (nfc_closed_for_hce=True)."""
    t0 = time.time()
    while time.time() - t0 < timeout:
        if nfc_closed_for_hce:
            return True
        time.sleep(interval)
    return False