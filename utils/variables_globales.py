##########################################
# Autor: Ernesto Lomar
# Fecha de creación: 26/04/2022
# Ultima modificación: 16/08/2022
#
# Script para almacenar las variables globales que se esten utilizando en el programa
##########################################

version_del_software = "EL.v3.59"
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
modo_nfcCard = True

from enum import Enum
# La clase VentanaActual es una enumeración de los posibles valores de la variable ventana_actual
class VentanaActual(Enum):
  CHOFER = 'chofer',
  CERRAR_VUELTA = 'cerrar_vuelta',
  CERRAR_TURNO = 'cerrar_turno',
  
ventana_actual = VentanaActual.CHOFER

# ---- Arbitraje PN532 ----
import threading, time
pn532_lock = threading.RLock()
pn532_owner = None

def pn532_acquire(owner: str, timeout: float = 3.0) -> bool:
    """Intenta tomar el lock global del PN532."""
    t0 = time.time()
    while time.time() - t0 < timeout:
        if pn532_lock.acquire(blocking=False):
            global pn532_owner
            pn532_owner = owner
            return True
        time.sleep(0.005)
    return False

def pn532_release():
    """Libera el lock global del PN532."""
    global pn532_owner
    pn532_owner = None
    try:
        pn532_lock.release()
    except Exception:
        pass

# Señal de reset solicitada por UI externa; la consume el dueño del lock
pn532_reset_requested = False