# gpio_hub.py
# Python 3.9+
# Requiere: RPi.GPIO (sudo apt-get install python3-rpi.gpio)
from __future__ import annotations
import time
import logging
import threading
from dataclasses import dataclass
from typing import Dict, Optional

try:
    import RPi.GPIO as GPIO
except ImportError as e:
    raise SystemExit("RPi.GPIO no está instalado. Instala python3-rpi.gpio.") from e

# ---------------------------------------------------------------------------
# Configuración de pines (ajusta a tu PCB). Numeración BCM.
# active_high=False significa pin activo en nivel bajo (útil para PWRKEY/RESET).
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class PinSpec:
    pin: int                       # Número BCM
    direction: str                 # 'in' | 'out' | 'pwm'
    active_high: bool = True       # Lógica del dispositivo
    initial: Optional[bool] = False  # Estado lógico inicial para 'out'; None si no aplica
    pull: Optional[str] = None     # 'up' | 'down' | None
    freq: Optional[int] = None     # Solo para PWM (Hz)

# gpio_hub.py
PINMAP = {
    # Quectel
    "quectel_reset":  PinSpec(pin=6,  direction="out", active_high=True,  initial=False),
    "quectel_pwrkey": PinSpec(pin=19, direction="out", active_high=False, initial=False),

    # Buzzer
    "buzzer":  PinSpec(pin=18, direction="out", active_high=True, initial=False),

    # NFC reset
    "nfc_rst": PinSpec(pin=27, direction="out", active_high=False, initial=False),

    # Ventilador (enable)
    "fan_en":  PinSpec(pin=13, direction="out", active_high=True, initial=False),
}

# ---------------------------------------------------------------------------
# Backend GPIO
# ---------------------------------------------------------------------------

class GPIOHub:
    """
    Hub único para todas las operaciones GPIO.
    Lógica de alto nivel expuesta en métodos: encender/apagar/reiniciar/verificar Quectel,
    encender/apagar ventiladores, PWM de ventiladores, on/off reader, etc.
    """

    def __init__(self, pinmap: Dict[str, PinSpec]):
        self._pins = pinmap
        self._pwm: Dict[str, GPIO.PWM] = {}
        self._lock = threading.RLock()
        self._log = logging.getLogger("GPIOHub")
        if not self._log.handlers:
            h = logging.StreamHandler()
            fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
            h.setFormatter(fmt)
            self._log.addHandler(h)
        self._log.setLevel(logging.INFO)

        GPIO.setmode(GPIO.BCM)
        GPIO.setwarnings(False)
        self._setup_all()

    # --------------------------- inicialización -----------------------------

    def _setup_all(self) -> None:
        with self._lock:
            for name, spec in self._pins.items():
                if spec.direction == "in":
                    pud = GPIO.PUD_OFF
                    if spec.pull == "up":
                        pud = GPIO.PUD_UP
                    elif spec.pull == "down":
                        pud = GPIO.PUD_DOWN
                    GPIO.setup(spec.pin, GPIO.IN, pull_up_down=pud)

                elif spec.direction == "out":
                    initial_phys = self._logical_to_phys(name, spec.initial if spec.initial is not None else False)
                    GPIO.setup(spec.pin, GPIO.OUT, initial=initial_phys)

                elif spec.direction == "pwm":
                    GPIO.setup(spec.pin, GPIO.OUT, initial=GPIO.LOW)
                    freq = spec.freq if spec.freq else 1000
                    pwm = GPIO.PWM(spec.pin, freq)
                    pwm.start(0)  # 0% duty por seguridad
                    self._pwm[name] = pwm
                else:
                    raise ValueError(f"Dirección no válida en {name}: {spec.direction}")

    # --------------------------- utilidades base ----------------------------

    def _spec(self, name: str) -> PinSpec:
        if name not in self._pins:
            raise KeyError(f"Pin '{name}' no está definido.")
        return self._pins[name]

    def _logical_to_phys(self, name: str, logical: bool) -> int:
        spec = self._spec(name)
        # True lógico = nivel alto si active_high=True, nivel bajo si active_high=False
        truth = logical == spec.active_high
        return GPIO.HIGH if truth else GPIO.LOW

    def _phys_to_logical(self, name: str, level: int) -> bool:
        spec = self._spec(name)
        is_high = (level == GPIO.HIGH)
        return is_high if spec.active_high else not is_high

    def write(self, name: str, logical: bool) -> None:
        spec = self._spec(name)
        if spec.direction not in ("out", "pwm"):
            raise ValueError(f"Pin {name} no es de salida.")
        with self._lock:
            if spec.direction == "out":
                GPIO.output(spec.pin, self._logical_to_phys(name, logical))
            else:
                # En pines PWM, logical True/False no aplica. Usa set_pwm.
                raise ValueError(f"Pin {name} es PWM. Usa set_pwm.")

    def read(self, name: str) -> bool:
        spec = self._spec(name)
        if spec.direction != "in":
            raise ValueError(f"Pin {name} no es de entrada.")
        level = GPIO.input(spec.pin)
        return self._phys_to_logical(name, level)

    def pulse(self, name: str, ms: int) -> None:
        """
        Pulso lógico: pone True durante ms milisegundos y luego False.
        Respeta active_high/active_low del PinSpec.
        """
        spec = self._spec(name)
        if spec.direction != "out":
            raise ValueError(f"Pin {name} no es de salida.")
        with self._lock:
            GPIO.output(spec.pin, self._logical_to_phys(name, True))
            time.sleep(ms / 1000.0)
            GPIO.output(spec.pin, self._logical_to_phys(name, False))

    def set_pwm(self, name: str, duty_cycle: float) -> None:
        """
        duty_cycle en 0..100. No invierte la señal; la inversión la define el hardware.
        """
        if name not in self._pwm:
            raise ValueError(f"Pin {name} no está configurado como PWM.")
        dc = max(0.0, min(100.0, float(duty_cycle)))
        with self._lock:
            self._pwm[name].ChangeDutyCycle(dc)

    def set_pwm_freq(self, name: str, freq_hz: int) -> None:
        if name not in self._pwm:
            raise ValueError(f"Pin {name} no está configurado como PWM.")
        if freq_hz <= 0:
            raise ValueError("Frecuencia PWM inválida.")
        with self._lock:
            self._pwm[name].ChangeFrequency(freq_hz)

    # ------------------------ alto nivel: Quectel ---------------------------

    def quectel_encender(self, ms_pwrkey: int = 700, verificacion: bool = True, timeout_s: int = 20) -> bool:
        """
        PWRKEY activo por ~0.7s suele encender módulos Quectel (EC25/EG25, etc.).
        Si verificacion=True intenta leer quectel_status hasta timeout.
        Retorna True si STATUS indica encendido, False en caso contrario o si no hay STATUS.
        """
        self._log.info("Quectel: encender (PWRKEY).")
        self.pulse("quectel_pwrkey", ms_pwrkey)
        if verificacion:
            ok = self.quectel_verificar(timeout_s=timeout_s)
            self._log.info(f"Quectel: verificar tras encender = {ok}")
            return ok
        return True

    def quectel_apagar(self, ms_pwrkey: int = 700, verificacion: bool = True, timeout_s: int = 20) -> bool:
        """
        Pulso de PWRKEY para apagado ordenado. Sin AT, es lo más seguro vía GPIO.
        Retorna verificación por STATUS si existe.
        """
        self._log.info("Quectel: apagar (PWRKEY).")
        self.pulse("quectel_pwrkey", ms_pwrkey)
        if verificacion:
            ok = not self.quectel_verificar(timeout_s=timeout_s)
            self._log.info(f"Quectel: verificar tras apagar = {ok}")
            return ok
        return True

    def quectel_reiniciar(self, ms_reset: int = 200, verificacion: bool = True, timeout_s: int = 30) -> bool:
        """
        RESET_N activo por >150 ms suele forzar hard reset.
        """
        self._log.info("Quectel: reiniciar (RESET).")
        self.pulse("quectel_reset", ms_reset)
        if verificacion:
            # Espera un breve apagado y nuevo encendido
            time.sleep(2.0)
            ok = self.quectel_verificar(timeout_s=timeout_s)
            self._log.info(f"Quectel: verificar tras reinicio = {ok}")
            return ok
        return True

    def quectel_verificar(self, timeout_s: int = 15, estable_s: float = 1.5) -> Optional[bool]:
        """
        Lee 'quectel_status' si existe. True si encendido.
        Requiere mantener estado estable durante 'estable_s'.
        Retorna None si no hay pin de STATUS configurado.
        """
        if "quectel_status" not in self._pins:
            self._log.warning("Quectel: no hay pin STATUS configurado.")
            return None

        t_end = time.time() + timeout_s
        target = True  # Buscamos encendido
        last_change = None
        prev = None

        while time.time() < t_end:
            val = self.read("quectel_status")  # lógico
            now = time.time()
            if prev is None or val != prev:
                last_change = now
                prev = val
            if val == target and last_change is not None and (now - last_change) >= estable_s:
                return True
            time.sleep(0.1)
        return False

    # ------------------------ alto nivel: Ventiladores ----------------------

    def ventiladores_on(self) -> None:
        self._log.info("Ventiladores: ON")
        self.write("fan_en", True)

    def ventiladores_off(self) -> None:
        self._log.info("Ventiladores: OFF")
        self.write("fan_en", False)
        # Si hay PWM, llevar a 0 para silencio.
        if "fan_pwm" in self._pwm:
            self.set_pwm("fan_pwm", 0.0)

    def ventiladores_set_velocidad(self, duty_0_100: float, freq_hz: Optional[int] = None) -> None:
        """
        Requiere 'fan_pwm' configurado. duty en 0..100. freq opcional para drivers específicos.
        """
        if "fan_pwm" not in self._pwm:
            raise RuntimeError("No hay PWM configurado para ventiladores.")
        if freq_hz:
            self.set_pwm_freq("fan_pwm", freq_hz)
        # Asegura habilitación
        self.write("fan_en", True)
        self.set_pwm("fan_pwm", duty_0_100)

    # ------------------------ alto nivel: Buzzer ----------------------------
    def buzzer_on(self) -> None:  self.write("buzzer", True)
    def buzzer_off(self) -> None: self.write("buzzer", False)
    def buzzer_beep(self, ms: int = 120) -> None:
        self.buzzer_on(); time.sleep(ms/1000.0); self.buzzer_off()
    def buzzer_blinks(self, n: int = 1, on_ms: int = 55, off_ms: int = 55) -> None:
        for _ in range(max(0, int(n))):
            self.buzzer_on();  time.sleep(on_ms/1000.0)
            self.buzzer_off(); time.sleep(off_ms/1000.0)

    # ------------------------ alto nivel: Reader ----------------------------

    def reader_on(self) -> None:
        self._log.info("Reader: ON")
        self.write("reader_en", True)

    def reader_off(self) -> None:
        self._log.info("Reader: OFF")
        self.write("reader_en", False)

    # ------------------------ utilidades varias -----------------------------

    def safe_state(self) -> None:
        """
        Lleva salidas a estados “seguros” definidos en PinSpec.initial.
        PWM a 0%.
        """
        self._log.info("Safe state de salidas.")
        with self._lock:
            for name, spec in self._pins.items():
                if spec.direction == "out" and spec.initial is not None:
                    GPIO.output(spec.pin, self._logical_to_phys(name, spec.initial))
            for name in list(self._pwm.keys()):
                self._pwm[name].ChangeDutyCycle(0.0)

    def close(self) -> None:
        self._log.info("Liberando GPIO…")
        with self._lock:
            for pwm in self._pwm.values():
                try:
                    pwm.stop()
                except Exception:
                    pass
            self._pwm.clear()
            GPIO.cleanup()