from PyQt5 import uic
from PyQt5.QtCore import Qt, QTimer
from PyQt5.QtGui import QPixmap
from PyQt5.QtWidgets import QWidget


class VentanaEmergente(QWidget):
    """
    Ventana emergente usada desde:
      - QR (a través de señales mostrar_mensaje → mensaje)
      - NFC (self.mensaje.emit("TARJETAINVALIDA", "", 2.0), etc.)
      - Otras partes (por ejemplo, VentanaEmergente("VOID", "Ya existe un viaje", 4.5))

    Firma compatible con tu código actual:
        VentanaEmergente(tipo_imagen, parametro, duracion=None)

    Donde:
      - tipo_imagen: "ACEPTADO", "INVALIDO", "EQUIVOCADO", "CADUCO", "UTILIZADO",
                     "TARJETAINVALIDA", "FUERADEVIGENCIA", "VOID", etc.
      - parametro:   texto que se mostrará (destino, mensaje extra, etc.)
      - duracion:    segundos antes de autocerrar; si es None, no se autocierra.
    """

    def __init__(self, tipo_imagen, parametro="", duracion=None, parent=None):
        super().__init__(parent)

        # Ventana sin marco, por encima
        self.setGeometry(20, 25, 761, 411)
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)

        # Cargar UI
        uic.loadUi("/home/pi/Urban_Urbano/ui/emergentes.ui", self)

        # Imagen + texto
        try:
            if tipo_imagen == "ACEPTADO":
                self.label_fondo.setPixmap(QPixmap("/home/pi/Urban_Urbano/Imagenes/Aceptado.png"))
                self.label_texto.setText(str(parametro))

            elif tipo_imagen == "NODESTINO":
                self.label_fondo.setPixmap(QPixmap("/home/pi/Urban_Urbano/Imagenes/NoDestino.png"))
                self.label_texto.setText(str(parametro))

            elif tipo_imagen == "EQUIVOCADO":
                self.label_fondo.setPixmap(QPixmap("/home/pi/Urban_Urbano/Imagenes/Equivocado.png"))
                self.label_texto.setText(str(parametro).replace("{", "").replace("}", "").replace("'", ""))

            elif tipo_imagen == "CADUCO":
                self.label_fondo.setPixmap(QPixmap("/home/pi/Urban_Urbano/Imagenes/Caducado.png"))
                self.label_texto.setText(str(parametro))

            elif tipo_imagen == "UTILIZADO":
                self.label_fondo.setPixmap(QPixmap("/home/pi/Urban_Urbano/Imagenes/Utilizado.png"))
                self.label_texto.setText(str(parametro))

            elif tipo_imagen == "INVALIDO":
                self.label_fondo.setPixmap(QPixmap("/home/pi/Urban_Urbano/Imagenes/Invalido.png"))
                self.label_texto.setText(str(parametro))

            elif tipo_imagen == "IMPRESORA":
                self.label_fondo.setPixmap(QPixmap("/home/pi/Urban_Urbano/Imagenes/problema_impresora.png"))
                self.label_texto.setText(str(parametro))

            elif tipo_imagen == "TARJETAINVALIDA":
                self.label_fondo.setPixmap(QPixmap("/home/pi/Urban_Urbano/Imagenes/001.jpg"))
                self.label_texto.setText(str(parametro))

            elif tipo_imagen == "NOCORTE":
                self.label_fondo.setPixmap(QPixmap("/home/pi/Urban_Urbano/Imagenes/nocorte.jpg"))
                self.label_texto.setText(str(parametro))

            elif tipo_imagen == "FUERADEVIGENCIA":
                self.label_fondo.setPixmap(QPixmap("/home/pi/Urban_Urbano/Imagenes/fuera_de_vigencia.jpg"))
                self.label_texto.setText(str(parametro))

            elif tipo_imagen == "VOID":
                self.label_fondo.setPixmap(QPixmap("/home/pi/Urban_Urbano/Imagenes/nocorte.jpg"))
                self.label_texto.setText(str(parametro))

            else:
                # Tipo desconocido: imagen genérica
                self.label_fondo.setPixmap(QPixmap("/home/pi/Urban_Urbano/Imagenes/no.png"))
                self.label_texto.setText(str(parametro))

        except Exception as e:
            # Si falla algo en la UI o las imágenes, mostrar pantalla genérica
            self.label_fondo.setPixmap(QPixmap("/home/pi/Urban_Urbano/Imagenes/no.png"))
            self.label_texto.setText("")
            print("emergentes.py, error al configurar ventana: " + str(e))

        # Autocierre opcional (usado tanto por QR como por otras llamadas con 3er parámetro)
        if duracion is not None:
            try:
                segundos = max(float(duracion), 0.1)
            except Exception:
                segundos = 5.0  # fallback

            self._timer = QTimer(self)
            self._timer.setSingleShot(True)
            self._timer.timeout.connect(self.close)
            self._timer.start(int(segundos * 1000))