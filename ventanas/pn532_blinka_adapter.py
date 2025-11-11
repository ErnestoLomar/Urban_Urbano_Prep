# pn532_blinka_adapter.py
# -*- coding: utf-8 -*-
import time, board, busio, digitalio
from adafruit_pn532.spi import PN532_SPI

class Pn532Blinka:
    def __init__(self, cs_pin=board.CE0, rst_pin=board.D27, baudrate=250_000):
        self.spi = busio.SPI(board.SCLK, board.MOSI, board.MISO)
        while not self.spi.try_lock():
            pass
        try:
            self.spi.configure(baudrate=baudrate, phase=0, polarity=0)  # 400 kHz; baja a 250 kHz si hace falta
        finally:
            self.spi.unlock()
        self.cs  = digitalio.DigitalInOut(cs_pin)
        self.rst = digitalio.DigitalInOut(rst_pin)
        self.pn  = PN532_SPI(self.spi, self.cs, reset=self.rst)
        self._tg = 0x01
        # nuevos tiempos por defecto
        self.t_poll = 0.12
        self.t_apdu = 0.25

    def begin(self):
        return True

    def getFirmwareVersion(self):
        return self.pn.firmware_version

    def _safe_call(self, cmd, response_length=0, params=b"", timeout=None, retries=3, sleep_s=0.02):
        # timeout ahora opcional y más corto
        if timeout is None:
            timeout = self.t_apdu
        for _ in range(retries):
            try:
                return self.pn.call_function(cmd, response_length=response_length, params=params, timeout=timeout)
            except RuntimeError:
                time.sleep(sleep_s)
                try:
                    self.pn.SAM_configuration()
                except Exception:
                    pass
        return None

    def _rf_tune(self):
        try:
            # RF off/on
            self._safe_call(0x32, response_length=0, params=bytes([0x01,0x00]))
            time.sleep(0.02)
            self._safe_call(0x32, response_length=0, params=bytes([0x01,0x01]))
            # más reintentos de activación pasiva
            self._safe_call(0x32, response_length=0, params=bytes([0x05,0xFF,0x01,0xFF]))
        except Exception:
            pass

    def SAMConfig(self):
        self.pn.SAM_configuration()
        self._rf_tune()

    def inListPassiveTarget(self, timeout=None):
        if timeout is None:
            timeout = self.t_poll
        resp = self._safe_call(0x4A, response_length=255, params=bytes([0x01, 0x00]), timeout=timeout)
        if resp and len(resp) >= 2 and resp[0] >= 1:
            self._tg = resp[1]
            return True
        return False
    
    def refresh_target(self, timeout=None):
        if timeout is None:
            timeout = self.t_poll
        resp = self._safe_call(0x4A, response_length=255, params=bytes([0x01, 0x00]), timeout=timeout)
        if resp and len(resp) >= 2 and resp[0] >= 1:
            self._tg = resp[1]
            return True
        return False

    def read_uid(self, timeout=1.0):
        try:
            return self.pn.read_passive_target(timeout=timeout)
        except RuntimeError:
            self._rf_tune()
            return None

    def inDataExchange(self, data_bytes, response_len=255):
        resp = self._safe_call(0x40, response_length=response_len,
                               params=bytes([self._tg]) + bytes(data_bytes),
                               timeout=self.t_apdu)
        if not resp:
            return False, b""
        ok = (resp[0] == 0x00)
        return ok, bytes(resp[1:])

    def hard_reset(self):
        try:
            self.rst.switch_to_output(value=True)
        except Exception:
            pass
        self.rst.value = False; time.sleep(0.4)
        self.rst.value = True;  time.sleep(0.6)
        try:
            self.SAMConfig()
        except Exception:
            pass