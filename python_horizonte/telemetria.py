"""Protocolo serial CascaVibe. Sem dependências gráficas."""

from dataclasses import dataclass
import math


@dataclass(frozen=True)
class Sample:
    ms: int
    roll: float
    pitch: float
    yaw: float | None = None
    accel: tuple | None = None
    gyro: tuple | None = None
    temperature: float | None = None


def parse_sample(line):
    fields = line.strip().split(",")
    try:
        if fields[0] == "ANGLE" and len(fields) == 3:
            values = tuple(map(float, fields[1:]))
            if all(map(math.isfinite, values)):
                return Sample(0, *values)
        if fields[0] == "IMU" and len(fields) == 12:
            ms = int(fields[1])
            values = tuple(map(float, fields[2:]))
            if not 0 <= ms <= 0xFFFFFFFF or not all(map(math.isfinite, values)):
                return None
            roll, pitch, yaw, ax, ay, az, gx, gy, gz, temp = values
            if abs(roll) > 180 or abs(pitch) > 180 or not 0 <= yaw <= 360:
                return None
            # O arredondamento para duas casas no ESP32 pode produzir 360.00.
            return Sample(ms, roll, pitch, yaw % 360, (ax, ay, az), (gx, gy, gz), temp)
    except (ValueError, OverflowError):
        pass
    return None


class LineBuffer:
    """Preserva linhas fragmentadas e descarta linhas excessivamente longas."""

    def __init__(self):
        self.pending = bytearray()
        self.discard = False

    def feed(self, data):
        lines = []
        for byte in data:
            if byte == 10:
                if not self.discard:
                    lines.append(self.pending.decode("ascii", errors="replace").strip())
                self.pending.clear()
                self.discard = False
            elif not self.discard:
                self.pending.append(byte)
                if len(self.pending) > 512:
                    self.pending.clear()
                    self.discard = True
        return lines


def demo_sample(t):
    """Movimento sintético, sempre identificado como demonstração na interface."""
    roll = 22 * math.sin(t * 0.55)
    pitch = 12 * math.sin(t * 0.37)
    r, p = map(math.radians, (roll, pitch))
    accel = (-9.80665 * math.sin(p) + 0.15 * math.sin(9 * t),
             9.80665 * math.sin(r) * math.cos(p) + 0.09 * math.sin(12 * t),
             9.80665 * math.cos(r) * math.cos(p) + 0.12 * math.sin(7 * t))
    return Sample(int(t * 1000), roll, pitch, (35 + t * 7) % 360, accel,
                  (12.1 * math.cos(t * 0.55), 4.44 * math.cos(t * 0.37), 7.0),
                  29.4 + 0.3 * math.sin(t * 0.05))
