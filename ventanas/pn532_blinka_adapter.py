# -*- coding: utf-8 -*-
"""
Adaptador PN532 sobre Adafruit Blinka para reemplazar la API de pn532pi.

Expone métodos compatibles:
  - begin()
  - getFirmwareVersion() -> tuple(IC, Ver, Rev, Support)
  - SAMConfig()
  - inListPassiveTarget(timeout=...) -> bool
  - read_uid(timeout=...) -> bytes|None
  - inDataExchange(data_bytes, response_len=255) -> (ok: bool, r_apdu: bytes)
  - hard_reset()
"""

import time
import board, busio, digitalio
from adafruit_pn532.spi import PN532_SPI


class Pn532Blinka:
    def __init__(self, cs_pin=board.CE0, rst_pin=board.D27, baudrate=1_000_000):
        # busio gestiona la frecuencia automáticamente con PN532_SPI
        self.spi = busio.SPI(board.SCLK, board.MOSI, board.MISO)
        self.cs  = digitalio.DigitalInOut(cs_pin)
        self.rst = digitalio.DigitalInOut(rst_pin)
        self.pn  = PN532_SPI(self.spi, self.cs, reset=self.rst)  # SPI + reset HW
        self._tg = 0x01  # target lógico para InDataExchange

    # pn532pi compat
    def begin(self):
        return True

    def getFirmwareVersion(self):
        return self.pn.firmware_version  # (IC, Ver, Rev, Support)

    def SAMConfig(self):
        # lector listo para ISO14443A
        self.pn.SAM_configuration()

    def inListPassiveTarget(self, timeout=1.0):
        """Devuelve True si hay dispositivo presente."""
        uid = self.pn.read_passive_target(timeout=timeout)
        return uid is not None

    def read_uid(self, timeout=1.0):
        """Devuelve UID bytes o None."""
        return self.pn.read_passive_target(timeout=timeout)

    def inDataExchange(self, data_bytes, response_len=255):
        """
        Envía APDU ISO-DEP usando el comando InDataExchange (0x40).
        Retorna (ok, r_apdu) donde r_apdu = payload || SW1SW2.
        """
        try:
            apdu = bytes(data_bytes)
            resp = self.pn.call_function(
                0x40,
                response_length=response_len,
                params=bytes([self._tg]) + apdu
            )
            if not resp:
                return False, b""
            ok = (resp[0] == 0x00)
            return ok, bytes(resp[1:])
        except Exception:
            return False, b""

    def hard_reset(self):
        # Pulso a RSTPD_N
        try:
            self.rst.switch_to_output(value=True)
        except Exception:
            pass
        self.rst.value = False; time.sleep(0.4)
        self.rst.value = True;  time.sleep(0.6)