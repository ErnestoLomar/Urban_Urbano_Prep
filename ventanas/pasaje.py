##########################################
# Autor: Ernesto Lomar
# Fecha de creación: 12/04/2022
# Ultima modificación: 12/04/2022
#
# Script de la ventana pasaje.
#
##########################################

# Librerías externas
from PyQt5 import uic
from PyQt5.QtWidgets import QApplication, QWidget, QLabel, QVBoxLayout, QFrame, QGraphicsDropShadowEffect
from PyQt5.QtGui import QColor, QPainter, QLinearGradient, QBrush, QPixmap
from PyQt5.QtCore import QSettings, Qt
import sys
from time import strftime
import logging
import time
import subprocess
# import usb.core

sys.path.insert(1, '/home/pi/Urban_Urbano/db')

# Hub GPIO (BCM)
try:
    from gpio_hub import GPIOHub, PINMAP
    HUB = GPIOHub(PINMAP)
except Exception as _hub_err:
    HUB = None
    logging.warning(f"No se pudo inicializar GPIOHub en pasaje.py: {_hub_err}")

# Librerías propias
from ventas_queries import insertar_venta, insertar_item_venta, obtener_ultimo_folio_de_item_venta
from queries import obtener_datos_aforo, insertar_estadisticas_boletera
import variables_globales as vg
from emergentes import VentanaEmergente
from prepago import VentanaPrepago

class OverlayPrepago(QWidget):
    def __init__(self, titulo="Cobro digital en curso", subtitulo="", logo_path=None):
        super().__init__(None, Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WA_TransparentForMouseEvents, False)  # bloquea clics al fondo
        self.setFocusPolicy(Qt.NoFocus)  # no roba foco
        self.logo_path = logo_path

        # ocupar pantalla
        try:
            g = QApplication.desktop().screenGeometry(); self.setGeometry(g)
        except Exception:
            self.setGeometry(0, 0, 800, 480)

        # tarjeta
        root = QVBoxLayout(self); root.setContentsMargins(24, 24, 24, 24)
        card = QFrame(self); card.setObjectName("card")
        card.setStyleSheet("""
            #card { background: rgba(255,255,255,0.98); border-radius: 18px; border: 1px solid rgba(0,0,0,0.06); }
            QLabel#title   { font-size: 26px; font-weight: 700; color: #1850A0; }   /* azul marca */
            QLabel#subtitle{ font-size: 15px; color: #334155; }
        """)
        shadow = QGraphicsDropShadowEffect(card); shadow.setBlurRadius(36); shadow.setOffset(0, 12); shadow.setColor(QColor(0,0,0,60))
        card.setGraphicsEffect(shadow)

        box = QVBoxLayout(card); box.setContentsMargins(28, 24, 28, 24); box.setSpacing(10)

        # logo opcional
        if self.logo_path:
            lbl_logo = QLabel(card); lbl_logo.setAlignment(Qt.AlignCenter)
            try:
                pm = QPixmap(self.logo_path)
                if not pm.isNull():
                    lbl_logo.setPixmap(pm.scaledToWidth(180, Qt.SmoothTransformation))
                    box.addWidget(lbl_logo)
            except Exception:
                pass

        title = QLabel(titulo, card); title.setObjectName("title"); title.setAlignment(Qt.AlignCenter)
        subtitle = QLabel(subtitulo, card); subtitle.setObjectName("subtitle"); subtitle.setAlignment(Qt.AlignCenter)

        # barra acento naranja debajo del título
        bar = QFrame(card); bar.setFixedHeight(4); bar.setStyleSheet("background:#F08020; border-radius:2px;")

        box.addWidget(title)
        box.addWidget(bar)
        box.addWidget(subtitle)

        root.addStretch(); root.addWidget(card); root.addStretch()

    # fondo con degradado claro azul → blanco
    def paintEvent(self, ev):
        p = QPainter(self)
        grad = QLinearGradient(0, 0, 0, self.height())
        grad.setColorAt(0.0, QColor("#EEF3FB"))   # azul muy claro
        grad.setColorAt(1.0, QColor("#F8FAFC"))   # casi blanco
        p.fillRect(self.rect(), QBrush(grad))
        p.end()

##############################################################################################
# Clase Pasajero que representa los diferentes tipos de pasajeros que existen en el sistema
# Estudiantes, Niños, Personas normales, Personas Mayores
##############################################################################################
class Pasajero:
    def __init__(self, tipo: str, precio: float):
        # Propiedades de la clase pasajero
        self.tipo = tipo
        self.precio = float(precio)
        self.total_pasajeros = 0
        self.total_pasajeros_tarjeta = 0

    def sub_total(self):
        try:
            return self.total_pasajeros * self.precio
        except Exception as e:
            logging.info(e)
            return 0.0

    def sub_total_tarjeta(self):
        try:
            return self.total_pasajeros_tarjeta * self.precio
        except Exception as e:
            logging.info(e)
            return 0.0

    def total_precio(self):
        try:
            return (self.total_pasajeros + self.total_pasajeros_tarjeta) * self.precio
        except Exception as e:
            logging.info(e)
            return 0.0

    def total_pasajeros_total(self):
        try:
            return self.total_pasajeros + self.total_pasajeros_tarjeta
        except Exception as e:
            logging.info(e)
            return 0

    def aumentar_pasajeros(self):
        try:
            self.total_pasajeros += 1
        except Exception as e:
            logging.info(e)

    def restar_pasajeros(self):
        try:
            self.total_pasajeros -= 1
        except Exception as e:
            logging.info(e)

    def aumentar_pasajeros_tarjeta(self):
        try:
            self.total_pasajeros_tarjeta += 1
        except Exception as e:
            logging.info(e)

    def restar_pasajeros_tarjeta(self):
        try:
            self.total_pasajeros_tarjeta -= 1
        except Exception as e:
            logging.info(e)


class VentanaPasaje(QWidget):
    def __init__(self, precio, de: str, hacia: str, precio_preferente, close_signal,
                 servicio_o_transbordo: str, id_tabla, ruta, tramo, cerrar_ventana_servicios):
        super().__init__()
        try:
            uic.loadUi("/home/pi/Urban_Urbano/ui/pasaje.ui", self)

            # Variables de la ventana
            self.origen = de
            self.destino = hacia
            self.close_signal = close_signal
            self.cerrar_servicios = cerrar_ventana_servicios
            self.precio = float(precio)
            self.precio_preferente = float(precio_preferente)

            self.personas_normales = Pasajero("personas_normales", self.precio)
            self.estudiantes = Pasajero("estudiantes", self.precio_preferente)
            self.personas_mayores = Pasajero("personas_mayores", self.precio_preferente)
            self.chicos = Pasajero("chicos", self.precio_preferente)

            self.servicio_o_transbordo = servicio_o_transbordo.split(',')
            self.id_tabla = id_tabla
            self.ruta = ruta
            self.tramo = tramo

            # Config de UI
            self.close_signal.connect(self.close_me)
            self.inicializar_labels()
            self.label_de.setText("De: " + str(de.split("_")[0]))
            self.label_hacia.setText("A: " + str(hacia.split("_")[0]))
            self.label_precio_normal.setText('P.N: $' + str(self.precio))
            self.label_precio_preferente.setText('P.P: $' + str(self.precio_preferente))

            self.settings = QSettings('/home/pi/Urban_Urbano/ventanas/settings.ini', QSettings.IniFormat)
            self.Unidad = str(obtener_datos_aforo()[1])

            # Pausar lector NFC dentro de la ventana de pasaje
            vg.modo_nfcCard = False
            time.sleep(0.2)
        except Exception as e:
            logging.info(e)

    # Beep usando HUB (fallback silencioso si no hay HUB)
    def _beep(self, n: int = 5, on_ms: int = 55, off_ms: int = 55):
        try:
            if HUB is not None:
                for _ in range(max(0, int(n))):
                    HUB.buzzer_on();  time.sleep(on_ms / 1000.0)
                    HUB.buzzer_off(); time.sleep(off_ms / 1000.0)
        except Exception as e:
            logging.debug(f"No se pudo usar buzzer HUB: {e}")

    # Cerrar ventana pasaje
    def close_me(self):
        try:
            vg.vendiendo_boleto = False
            self.close()
        except Exception as e:
            logging.info(e)

    # Señales de UI
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

    # Volver
    def handle_volver(self, event):
        try:
            vg.modo_nfcCard = True
            vg.vendiendo_boleto = False
            time.sleep(0.2)
            self.close()
        except Exception as e:
            logging.info(e)

    # Handlers de conteo
    def handle_ninos(self, event):
        try:
            self.chicos.aumentar_pasajeros()
            self.label_ninos_total.setText(str(self.chicos.total_pasajeros))
            self.label_ninos_total_precio.setText("$ " + str(int(self.chicos.total_precio())))
            self.calcularTotal()
        except Exception as e:
            logging.info(e)

    def handle_ninos_tarjeta(self, event):
        try:
            self.chicos.aumentar_pasajeros_tarjeta()
            self.label_ninos_total_tarjeta.setText(str(self.chicos.total_pasajeros_tarjeta))
            self.label_ninos_total_precio.setText("$ " + str(int(self.chicos.total_precio())))
            self.calcularTotal()
        except Exception as e:
            logging.info(e)

    def handle_estudiantes(self, event):
        try:
            self.estudiantes.aumentar_pasajeros()
            self.label_estudiantes_total.setText(str(self.estudiantes.total_pasajeros))
            self.label_estudiantes_total_precio.setText("$ " + str(int(self.estudiantes.total_precio())))
            self.calcularTotal()
        except Exception as e:
            logging.info(e)

    def handle_estudiantes_tarjeta(self, event):
        try:
            self.estudiantes.aumentar_pasajeros_tarjeta()
            self.label_estudiantes_total_tarjeta.setText(str(self.estudiantes.total_pasajeros_tarjeta))
            self.label_estudiantes_total_precio.setText("$ " + str(int(self.estudiantes.total_precio())))
            self.calcularTotal()
        except Exception as e:
            logging.info(e)

    def handle_mayores_edad(self, event):
        try:
            self.personas_mayores.aumentar_pasajeros()
            self.label_mayores_total.setText(str(self.personas_mayores.total_pasajeros))
            self.label_mayores_total_precio.setText("$ " + str(int(self.personas_mayores.total_precio())))
            self.calcularTotal()
        except Exception as e:
            logging.info(e)

    def handle_mayores_edad_tarjeta(self, event):
        try:
            self.personas_mayores.aumentar_pasajeros_tarjeta()
            self.label_mayores_total_tarjeta.setText(str(self.personas_mayores.total_pasajeros_tarjeta))
            self.label_mayores_total_precio.setText("$ " + str(int(self.personas_mayores.total_precio())))
            self.calcularTotal()
        except Exception as e:
            logging.info(e)

    def handle_personas_normales(self, event):
        try:
            self.personas_normales.aumentar_pasajeros()
            self.label_normales_total.setText(str(self.personas_normales.total_pasajeros))
            self.label_normales_total_precio.setText("$ " + str(int(self.personas_normales.total_precio())))
            self.calcularTotal()
        except Exception as e:
            logging.info(e)

    def handle_personas_normales_tarjeta(self, event):
        try:
            self.personas_normales.aumentar_pasajeros_tarjeta()
            self.label_normales_total_tarjeta.setText(str(self.personas_normales.total_pasajeros_tarjeta))
            self.label_normales_total_precio.setText("$ " + str(int(self.personas_normales.total_precio())))
            self.calcularTotal()
        except Exception as e:
            logging.info(e)

    # Pagar
    def handle_pagar(self, event):
        try:
            if self.calcularTotal() == 0:
                return

            self.close_me()

            if len(vg.folio_asignacion) <= 1:
                self.ve = VentanaEmergente("VOID", "No existe viaje", 4.5)
                self.ve.show()
                self._beep(5, 55, 55)
                self.cerrar_servicios.emit()
                return

            try:
                from impresora import (
                    imprimir_boleto_normal_pasaje,
                    imprimir_boleto_con_qr_pasaje
                )
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

            def imprimir_y_guardar(tipo, data, tipo_num, setting_key, servicio, pasajeros=None):
                total_pasajeros = data.total_pasajeros if pasajeros is None else pasajeros
                for _ in range(total_pasajeros):
                    folio = (obtener_ultimo_folio_de_item_venta() or (None, 0))[1] + 1
                    hora = strftime("%H:%M:%S")

                    # Imprimir boleto
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

                    # Insertar en DB
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

                    if not insertado:
                        logging.error(f"Error al registrar venta en DB (folio {folio})")
                        self._beep(5, 55, 55)

                    # Actualizar contadores
                    total, subtotal = map(float, self.settings.value(setting_key, "0,0").split(','))
                    self.settings.setValue(setting_key, f"{int(total+1)},{subtotal+data.precio}")
                    self.settings.setValue('total_a_liquidar', str(float(self.settings.value('total_a_liquidar')) + data.precio))
                    self.settings.setValue('total_de_folios', str(int(self.settings.value('total_de_folios')) + 1))
                    self.settings.setValue('total_a_liquidar_efectivo', str(float(self.settings.value('total_a_liquidar_efectivo')) + data.precio))
                    self.settings.setValue('total_de_folios_efectivo', str(int(self.settings.value('total_de_folios_efectivo')) + 1))

                    if not hecho:
                        insertar_estadisticas_boletera(
                            str(self.Unidad), fecha_estadistica, hora_estadistica,
                            "BMI", f"S{tipo[0]}"
                        )
                        logging.info("Error al imprimir boleto")
                        self.ve = VentanaEmergente("IMPRESORA", "", 4.5)
                        self.ve.show()

            # crea overlay solo si habrá cobros digitales
            needs_overlay = any(datos.total_pasajeros_tarjeta > 0 for _, datos, _, _, _ in pasajeros)
            overlay = None
            if needs_overlay:
                overlay = OverlayPrepago(
                    titulo="Cobro digital en curso",
                    subtitulo=f"Ruta {self.ruta} • Tramo {self.tramo}",
                    logo_path="/home/pi/Urban_Urbano/Imagenes/logo.png"  # pon aquí tu ruta del logo
                )
                overlay.show(); overlay.raise_(); QApplication.processEvents()

            try:
                servicio = self.servicio_o_transbordo[0]
                if servicio in ['SER', 'TRA']:
                    for tipo, datos, tipo_num, setting, precio in pasajeros:
                        # efectivo
                        if datos.total_pasajeros > 0:
                            imprimir_y_guardar(tipo, datos, tipo_num, setting, servicio)
                        # HCE: uno por ventana
                        total_hce = datos.total_pasajeros_tarjeta
                        for _ in range(total_hce):
                            vg.modo_nfcCard = False
                            try:
                                getattr(vg, "nfc_close_all", lambda: None)()
                            finally:
                                vg.nfc_closed_for_hce = True
                            time.sleep(0.8)

                            ventana = VentanaPrepago(
                                tipo=tipo, tipo_num=tipo_num, setting=setting,
                                total_hce=1, precio=precio, id_tarifa=self.id_tabla,
                                geocerca=int(str(self.settings.value('geocerca')).split(",")[0]),
                                servicio=("n" if servicio == "SER" else "t"),
                                origen=self.origen, destino=self.destino,
                                parent=overlay  # <—— clave: hijo del overlay
                            )
                            # tamaño/posición opcional
                            ventana.setGeometry(0, 0, 800, 480)

                            r = ventana.mostrar_y_esperar()
                            time.sleep(1)

                            if not r['hecho']:
                                if r['pagado_efectivo']:
                                    imprimir_y_guardar(tipo, datos, tipo_num, setting, servicio, 1)
                                    continue
                                break

                            # impresión tras HCE OK (igual que ya tenías)
                            if servicio == "SER":
                                hecho = imprimir_boleto_normal_pasaje(
                                    str(r['folio']), r['fecha'], r['hora'], str(self.Unidad),
                                    tipo, str(precio), str(self.ruta), str(self.tramo)
                                )
                            else:
                                hecho = imprimir_boleto_con_qr_pasaje(
                                    str(r['folio']), r['fecha'], r['hora'], str(self.Unidad),
                                    tipo, str(precio), str(self.ruta), str(self.tramo),
                                    self.servicio_o_transbordo
                                )
                            if not hecho:
                                insertar_estadisticas_boletera(
                                    str(self.Unidad), fecha_estadistica, hora_estadistica,
                                    "BMI", f"{'S' if servicio=='SER' else 'T'}{tipo[0]}"
                                )
                                self.ve = VentanaEmergente("IMPRESORA", "", 4.5)
                                self.ve.show()
            finally:
                if overlay:
                    overlay.close()
                    overlay.deleteLater()

            vg.modo_nfcCard = True
            time.sleep(0.2)

        except Exception as e:
            print("Error en handle_pagar: ", e)
            logging.error(f"Error en handle_pagar: {e}")

    # Calcular totales
    def calcularTotal(self):
        try:
            totalPersonas = (
                self.chicos.total_pasajeros_total()
                + self.estudiantes.total_pasajeros_total()
                + self.personas_mayores.total_pasajeros_total()
                + self.personas_normales.total_pasajeros_total()
            )
            totalPrecio = (
                self.chicos.total_precio()
                + self.estudiantes.total_precio()
                + self.personas_mayores.total_precio()
                + self.personas_normales.total_precio()
            )
            total_precios_efectivo = (
                self.chicos.sub_total()
                + self.estudiantes.sub_total()
                + self.personas_mayores.sub_total()
                + self.personas_normales.sub_total()
            )
            total_precios_tarjeta = (
                self.chicos.sub_total_tarjeta()
                + self.estudiantes.sub_total_tarjeta()
                + self.personas_mayores.sub_total_tarjeta()
                + self.personas_normales.sub_total_tarjeta()
            )
            self.label_personas_total.setText("Pasajes: " + str(totalPersonas))
            self.label_total_precio.setText("Total: $ " + str(int(totalPrecio)))
            self.label_total_precio_efectivo.setText("Efectivo: $ " + str(int(total_precios_efectivo)))
            self.label_total_precio_tarjeta.setText("Digital: $ " + str(int(total_precios_tarjeta)))
            return totalPrecio
        except Exception as e:
            logging.info(e)
            return 0.0


if __name__ == '__main__':
    app = QApplication(sys.argv)
    # Ejemplo de prueba local (ajusta la firma si pruebas fuera del flujo normal):
    # GUI = VentanaPasaje(10, "calle_33", "calle_45", 5, lambda: None, "SER,XYZ", 1, "Ruta X", "Tramo Y", lambda: None)
    # GUI.show()
    # sys.exit(app.exec())