"""CascaVibe • painel de movimento para ESP32 + MPU-6050.

Execute com --port /dev/ttyACM0 ou --demo. Z: zerar rumo; R: reconectar; Esc: sair.
"""
import argparse
from collections import deque
import math
import os
import time

os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")
import pygame
import serial
from serial.tools import list_ports

from telemetria import LineBuffer, demo_sample, parse_sample

WIDTH, HEIGHT = 1440, 920
BG = (9, 16, 26)
CARD = (16, 27, 41)
EDGE = (36, 54, 72)
TEXT = (230, 240, 249)
MUTED = (138, 159, 182)
CYAN = (74, 216, 219)
GOLD = (255, 198, 99)
AXES = ((255, 125, 119), (91, 220, 192), (113, 165, 255))
SKY, GROUND = (39, 109, 163), (115, 74, 49)
FONTS = {}
ZERO = pygame.Rect(612, 523, 266, 37)
RECONNECT = pygame.Rect(1202, 26, 210, 38)


def font(size, bold=False):
    key = size, bold
    if key not in FONTS:
        FONTS[key] = pygame.font.SysFont("DejaVu Sans", size, bold=bold)
    return FONTS[key]


def label(surface, text, pos, size=18, color=TEXT, bold=False, center=False):
    rendered = font(size, bold).render(str(text), True, color)
    surface.blit(rendered, rendered.get_rect(center=pos) if center else pos)


def card(surface, rect, title, subtitle=None):
    pygame.draw.rect(surface, CARD, rect, border_radius=18)
    pygame.draw.rect(surface, EDGE, rect, 1, border_radius=18)
    label(surface, title, (rect[0] + 22, rect[1] + 18), 18, TEXT, True)
    if subtitle:
        label(surface, subtitle, (rect[0] + 22, rect[1] + 46), 13, MUTED)


def button(surface, rect, text, active=True):
    pygame.draw.rect(surface, (26, 57, 69) if active else EDGE, rect, border_radius=9)
    label(surface, text, rect.center, 14, CYAN if active else MUTED, True, True)


def number(value, suffix="", decimals=1):
    return "—" if value is None else f"{value:.{decimals}f}{suffix}"


def horizon(surface, roll, pitch):
    # Mantém a inversão de roll aprovada pelo usuário.
    roll = -roll
    size, radius = 370, 174
    c = pygame.Vector2(size / 2, size / 2)
    instrument = pygame.Surface((size, size), pygame.SRCALPHA)
    angle = math.radians(roll)
    d = pygame.Vector2(math.cos(angle), -math.sin(angle))
    n = pygame.Vector2(-d.y, d.x)
    mid = c + n * max(-100, min(100, pitch)) * 3.1
    instrument.fill(SKY)
    pygame.draw.polygon(instrument, GROUND,
                        [mid - d * 1000, mid + d * 1000,
                         mid + d * 1000 + n * 1000, mid - d * 1000 + n * 1000])
    pygame.draw.line(instrument, TEXT, mid - d * 1000, mid + d * 1000, 2)
    for mark in range(-80, 81, 10):
        if not mark:
            continue
        p = mid - n * mark * 3.1
        half = 35 if mark % 20 == 0 else 22
        pygame.draw.line(instrument, TEXT, p - d * half, p + d * half, 2)
        label(instrument, str(abs(mark)), p + d * (half + 18), 12, TEXT, center=True)
    mask = pygame.Surface((size, size), pygame.SRCALPHA)
    pygame.draw.circle(mask, (255, 255, 255), c, radius)
    instrument.blit(mask, (0, 0), special_flags=pygame.BLEND_RGBA_MULT)
    surface.blit(instrument, (115, 174))
    center = pygame.Vector2(300, 359)
    pygame.draw.circle(surface, EDGE, center, radius + 4, 5)
    for degrees in (-60, -30, 0, 30, 60):
        a = math.radians(degrees)
        v = pygame.Vector2(math.sin(a), -math.cos(a))
        pygame.draw.line(surface, TEXT, center + v * 158, center + v * 170, 2)
    pygame.draw.polygon(surface, GOLD, [(300, 211), (293, 223), (307, 223)])
    for sign in (-1, 1):
        pygame.draw.lines(surface, GOLD, False,
                          [(300 + sign * 95, 359), (300 + sign * 28, 359),
                           (300 + sign * 28, 369)], 4)
    pygame.draw.circle(surface, GOLD, center, 5, 2)


def compass(surface, heading, limited=False):
    cx, cy, radius = 746, 334, 125
    pygame.draw.circle(surface, BG, (cx, cy), radius + 12)
    pygame.draw.circle(surface, EDGE, (cx, cy), radius + 12, 2)
    for deg in range(0, 360, 5):
        angle = math.radians(deg - (heading or 0))
        v = pygame.Vector2(math.sin(angle), -math.cos(angle))
        c = pygame.Vector2(cx, cy)
        major = deg % 30 == 0
        pygame.draw.line(surface, MUTED if major else EDGE,
                         c + v * (radius - (13 if major else 6)), c + v * radius, 2)
        if deg % 90 == 0:
            text = {0: "N*", 90: "L", 180: "S", 270: "O"}[deg]
            label(surface, text, c + v * 93, 20, GOLD if deg == 0 else TEXT, True, True)
        elif major:
            label(surface, str(deg), c + v * 92, 12, MUTED, center=True)
    pygame.draw.polygon(surface, CYAN, [(cx, cy - 57), (cx - 10, cy + 18),
                                      (cx, cy + 10), (cx + 10, cy + 18)])
    pygame.draw.polygon(surface, GOLD, [(cx, cy - 140), (cx - 7, cy - 151), (cx + 7, cy - 151)])
    label(surface, number(heading, "°", 0), (cx, cy + 52), 28, TEXT, True, True)
    label(surface, "RUMO LIMITADO • INCLINAÇÃO" if limited else "*N = referência ao zerar",
          (cx, 490), 13, GOLD if limited else MUTED, center=True)


class Telemetry:
    def __init__(self, port=None, baud=115200, demo=False):
        self.port, self.baud, self.demo = port, baud, demo
        self.device = None
        self.buffer = LineBuffer()
        self.sample = None
        self.received = None
        self.last_ms = None
        self.arrivals = deque(maxlen=150)
        self.history = deque(maxlen=1200)
        self.heading_zero = 0.0
        self.message = "Aguardando telemetria do ESP32"
        self.invalid = 0
        self.started = time.monotonic()

    def connect(self):
        self.close()
        self.buffer = LineBuffer()
        self.received = None
        self.sample = None
        self.last_ms = None
        self.history.clear()
        self.arrivals.clear()
        self.heading_zero = 0.0
        if self.demo:
            return
        try:
            self.device = serial.Serial(self.port, self.baud, timeout=0)
            self.message = "Porta aberta • aguarde a calibração inicial"
        except (serial.SerialException, OSError) as error:
            self.message = f"Falha ao abrir {self.port}: {error}"
            self.device = None

    def close(self):
        if self.device is not None:
            self.device.close()
            self.device = None

    def accept(self, sample, now):
        if self.last_ms is not None and sample.ms < self.last_ms:
            self.history.clear()
            self.arrivals.clear()
            self.heading_zero = 0.0
        self.last_ms = sample.ms
        self.sample, self.received = sample, now
        self.arrivals.append(now)
        if sample.accel is not None:
            self.history.append((now, sample.accel))
            self.message = "Aceleração inclui gravidade • 1 g = 9,80665 m/s²"
        else:
            self.message = "Firmware antigo: regrave o sketch para liberar aceleração e bússola"

    def update(self, now):
        if self.demo:
            self.accept(demo_sample(now - self.started), now)
            return
        if self.device is None:
            return
        try:
            # Limite por quadro: não bloqueia a janela com ruído ou tráfego contínuo.
            data = self.device.read(min(self.device.in_waiting, 16384))
            for line in self.buffer.feed(data):
                sample = parse_sample(line)
                if sample:
                    self.accept(sample, now)
                elif line.startswith(("INFO,", "ERROR,")):
                    self.message = line.split(",", 1)[1]
                    if line.startswith("ERROR,") or "CALIBRATING" in line:
                        self.received = None
                elif line:
                    self.invalid += 1
        except (serial.SerialException, OSError) as error:
            self.message = f"USB desconectado: {error}. Use Reconectar."
            self.close()
            self.received = None

    def fresh(self, now):
        return self.received is not None and now - self.received < 1.5

    def zero(self, now):
        if self.fresh(now) and self.sample.yaw is not None:
            self.heading_zero = self.sample.yaw

    def heading(self):
        if self.sample is None or self.sample.yaw is None:
            return None
        return (self.sample.yaw - self.heading_zero) % 360

    def hz(self):
        if len(self.arrivals) < 2:
            return 0
        dt = self.arrivals[-1] - self.arrivals[0]
        return (len(self.arrivals) - 1) / dt if dt > 0 else 0


def render(surface, state, now):
    surface.fill(BG)
    live = state.fresh(now)
    s = state.sample
    full = s is not None and s.accel is not None
    pygame.draw.rect(surface, CYAN, (28, 29, 5, 34), border_radius=2)
    label(surface, "CascaVibe", (47, 18), 30, TEXT, True)
    label(surface, "LABORATÓRIO DE MOVIMENTO  /  ESP32 + MPU-6050", (49, 57), 12, MUTED)
    status = "DEMONSTRAÇÃO • DADOS SIMULADOS" if state.demo else ("AO VIVO" if live else "SEM DADOS ATUAIS")
    status_color = GOLD if state.demo or not live else CYAN
    pygame.draw.circle(surface, status_color, (783, 41), 5)
    label(surface, status, (797, 31), 14, status_color, True)
    button(surface, RECONNECT, "R  Reconectar USB", not state.demo)
    card(surface, (28, 98, 544, 486), "01  /  HORIZONTE ARTIFICIAL", "Orientação do sensor • graus")
    card(surface, (590, 98, 312, 486), "02  /  BÚSSOLA RELATIVA", "Giroscópio • sem norte magnético")
    card(surface, (920, 98, 492, 486), "03  /  ACELERÔMETRO", "Três eixos • valores medidos, incluindo gravidade")
    horizon(surface, s.roll if s else 0, s.pitch if s else 0)
    label(surface, f"ROLL  {number(-s.roll if s else None, '°')}", (156, 551), 19, CYAN, True, True)
    label(surface, f"PITCH  {number(s.pitch if s else None, '°')}", (427, 551), 19, CYAN, True, True)
    compass(surface, state.heading(), s is not None and abs(s.pitch) >= 80)
    button(surface, ZERO, "Z  Zerar referência", live and full)
    if not live:
        label(surface, "AGUARDANDO DADOS" if s is None else "ÚLTIMA LEITURA • SEM SINAL",
              (300, 418), 13, GOLD, True, True)
    if s and not full:
        label(surface, "Regrave o firmware", (746, 465), 14, GOLD, center=True)
    for i, axis in enumerate("XYZ"):
        y = 190 + i * 91
        value = s.accel[i] if full else None
        label(surface, axis, (945, y), 25, AXES[i], True)
        label(surface, f"{value:+7.2f}" if value is not None else "—", (996, y - 4), 30, TEXT, True)
        label(surface, "m/s²", (1166, y + 9), 15, MUTED)
        label(surface, f"{value / 9.80665:+.2f} g" if value is not None else "— g",
              (1295, y + 15), 16, AXES[i], center=True)
        bar = pygame.Rect(997, y + 43, 377, 9)
        pygame.draw.rect(surface, EDGE, bar, border_radius=4)
        pygame.draw.line(surface, MUTED, (bar.centerx, y + 39), (bar.centerx, y + 55), 1)
        if value is not None:
            length = round(max(-1, min(1, value / 19.6133)) * bar.width / 2)
            if length:
                pygame.draw.rect(surface, AXES[i],
                                 (bar.centerx + min(0, length), bar.y, abs(length), bar.height),
                                 border_radius=3)
    pygame.draw.line(surface, EDGE, (944, 468), (1388, 468))
    norm = math.sqrt(sum(v * v for v in s.accel)) if full else None
    label(surface, "RESULTANTE", (947, 486), 13, MUTED, True)
    label(surface, number(norm / 9.80665 if norm is not None else None, " g", 3),
          (947, 506), 31, TEXT, True)
    label(surface, "Parado: aproximadamente 1 g", (1162, 521), 12, MUTED, center=True)
    label(surface, "Barras: −2 g a +2 g", (947, 554), 12, MUTED)
    clipped = full and (any(abs(v) >= 19.4 for v in s.accel) or any(abs(v) >= 248 for v in s.gyro))
    if clipped:
        label(surface, "LIMITE DO SENSOR", (1198, 553), 12, GOLD, True)

    card(surface, (28, 602, 874, 230), "04  /  ACELERAÇÃO NO TEMPO", "Últimos 10 segundos • m/s²")
    plot = pygame.Rect(88, 676, 786, 117)
    recent = [(t, a) for t, a in state.history if 0 <= now - t <= 10]
    extent = max(10, math.ceil(max((abs(v) for _, a in recent for v in a), default=10) / 5) * 5)
    for v in (-extent, 0, extent):
        y = plot.centery - v / extent * plot.height / 2
        pygame.draw.line(surface, EDGE, (plot.left, y), (plot.right, y))
        label(surface, f"{v:+.0f}", (40, y - 8), 12, MUTED)
    for sec in (10, 5, 0):
        x = plot.right - sec / 10 * plot.width
        pygame.draw.line(surface, EDGE, (x, plot.top), (x, plot.bottom))
        label(surface, f"−{sec}s" if sec else "agora", (x - 18, 804), 11, MUTED)
    for i, axis in enumerate("XYZ"):
        label(surface, f"● {axis}", (709 + i * 55, 627), 14, AXES[i], True)
        # Interrompe a curva nas lacunas de recepção.
        segments, segment, previous = [], [], None
        for t, a in recent:
            if previous is not None and t - previous > 0.3:
                segments.append(segment)
                segment = []
            segment.append((plot.right - (now - t) / 10 * plot.width,
                            plot.centery - a[i] / extent * plot.height / 2))
            previous = t
        segments.append(segment)
        for points in segments:
            if len(points) > 1:
                pygame.draw.aalines(surface, AXES[i], False, points)

    card(surface, (920, 602, 492, 230), "05  /  GIROSCÓPIO & SENSOR", "Velocidade angular • °/s")
    for i, axis in enumerate("XYZ"):
        x = 946 + i * 152
        label(surface, axis, (x, 677), 14, AXES[i], True)
        label(surface, number(s.gyro[i] if full else None), (x, 699), 27, TEXT, True)
    pygame.draw.line(surface, EDGE, (944, 749), (1388, 749))
    label(surface, "CHIP", (946, 766), 12, MUTED)
    label(surface, number(s.temperature if full else None, " °C"), (946, 787), 18, TEXT, True)
    label(surface, "RECEPÇÃO", (1100, 766), 12, MUTED)
    label(surface, f"{state.hz():.0f} Hz" if live else "— Hz", (1100, 787), 18, TEXT, True)
    label(surface, "PACOTE", (1262, 766), 12, MUTED)
    age = max(0, now - state.received) if state.received is not None else None
    label(surface, number(age * 1000 if age is not None else None, " ms", 0),
          (1262, 787), 18, TEXT, True)
    note = state.message
    if not live and s is not None:
        note = "Sem dados atuais • valores congelados. Confira o USB e use Reconectar."
    label(surface, note[:137], (30, 850), 14, MUTED if live else GOLD)
    source = "SIMULAÇÃO" if state.demo else f"{state.port}  •  {state.baud} baud"
    label(surface, f"{source}   |   Z: zerar rumo   R: reconectar   Esc: sair", (30, 885), 12, MUTED)
    label(surface, "Rumo relativo acumula deriva", (1120, 885), 12, GOLD)


def main():
    parser = argparse.ArgumentParser(description="CascaVibe • painel de telemetria")
    parser.add_argument("--port", help="porta serial; detecta automaticamente se houver somente um USB")
    parser.add_argument("--baud", type=int, default=115200)
    parser.add_argument("--demo", action="store_true", help="dados simulados, sem conectar ao ESP32")
    parser.add_argument("--list-ports", action="store_true", help="listar portas e sair")
    parser.add_argument("--screenshot", help="salvar PNG de demonstração e sair (exige --demo)")
    args = parser.parse_args()
    if args.list_ports:
        for port in list_ports.comports():
            print(f"{port.device}: {port.description}")
        return
    if args.screenshot and not args.demo:
        parser.error("--screenshot exige --demo")
    if not args.port and not args.demo:
        usb = [p.device for p in list_ports.comports() if p.vid is not None]
        if len(usb) != 1:
            parser.error("Informe --port (use --list-ports para listar) ou --demo.")
        args.port = usb[0]
    if args.screenshot:
        os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
    pygame.display.init()
    pygame.font.init()
    pygame.display.set_caption("CascaVibe • Laboratório de movimento")
    canvas = pygame.Surface((WIDTH, HEIGHT))
    display = pygame.display.set_mode((WIDTH, HEIGHT), pygame.RESIZABLE)
    state = Telemetry(args.port, args.baud, args.demo)
    state.connect()
    clock = pygame.time.Clock()
    running = True
    viewport = pygame.Rect(0, 0, WIDTH, HEIGHT)
    try:
        if args.screenshot:
            now = time.monotonic()
            for i in range(501):
                state.accept(demo_sample(i / 50), now - 10 + i / 50)
            render(canvas, state, now)
            pygame.image.save(canvas, args.screenshot)
            return
        while running:
            now = time.monotonic()
            for event in pygame.event.get():
                if event.type == pygame.QUIT:
                    running = False
                elif event.type == pygame.KEYDOWN:
                    if event.key == pygame.K_ESCAPE:
                        running = False
                    elif event.key == pygame.K_z:
                        state.zero(now)
                    elif event.key == pygame.K_r and not state.demo:
                        state.connect()
                elif event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
                    x = (event.pos[0] - viewport.x) * WIDTH / viewport.width
                    y = (event.pos[1] - viewport.y) * HEIGHT / viewport.height
                    if ZERO.collidepoint(x, y):
                        state.zero(now)
                    elif RECONNECT.collidepoint(x, y) and not state.demo:
                        state.connect()
            state.update(now)
            render(canvas, state, now)
            w, h = display.get_size()
            ratio = min(w / WIDTH, h / HEIGHT)
            size = (max(1, round(WIDTH * ratio)), max(1, round(HEIGHT * ratio)))
            viewport = pygame.Rect((w - size[0]) // 2, (h - size[1]) // 2, *size)
            display.fill(BG)
            display.blit(pygame.transform.smoothscale(canvas, size), viewport)
            pygame.display.flip()
            clock.tick(60)
    finally:
        state.close()
        pygame.quit()


if __name__ == "__main__":
    main()
