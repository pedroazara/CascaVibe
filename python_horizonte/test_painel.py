"""Verificações de protocolo, serial virtual e desenho, sem tocar no ESP32."""
import os
import time
import unittest

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")
import pygame
from telemetria import LineBuffer, parse_sample, demo_sample
from horizonte_artificial import Telemetry, render, WIDTH, HEIGHT, SKY, GROUND

PACKET = b"IMU,1020,12.50,-3.25,359.99,0.100,0.200,9.800,1.000,2.000,3.000,28.50\n"


class PanelTests(unittest.TestCase):
    def test_units_and_legacy(self):
        s = parse_sample(PACKET.decode())
        self.assertEqual(s.accel, (0.1, 0.2, 9.8))
        self.assertEqual(s.gyro, (1, 2, 3))
        self.assertEqual(s.temperature, 28.5)
        self.assertEqual(s.pitch, -3.25)
        legacy = parse_sample("ANGLE,20,-10")
        self.assertEqual(legacy.roll, 20)
        self.assertIsNone(legacy.accel)
        self.assertIsNone(legacy.yaw)

    def test_invalid_and_rounded_heading(self):
        for bad in ("", "INFO,calibrating", "ANGLE,nan,1", "ANGLE,2,inf",
                    PACKET.decode().replace("9.800", "nan"),
                    PACKET.decode().replace("1020", "-1"), "IMU,10,0,0"):
            self.assertIsNone(parse_sample(bad))
        self.assertEqual(parse_sample(PACKET.decode().replace("359.99", "360.00")).yaw, 0)

    def test_fragmentation_and_overlong_recovery(self):
        b = LineBuffer()
        self.assertEqual(b.feed(PACKET[:16]), [])
        self.assertEqual(b.feed(PACKET[16:]), [PACKET.decode().strip()])
        self.assertEqual(b.feed(b"x" * 1000), [])
        self.assertEqual(b.feed(b"ANGLE,0,0\n" + PACKET), [PACKET.decode().strip()])

    def test_stale_reset_and_reference(self):
        t = Telemetry(demo=True)
        t.accept(parse_sample(PACKET.decode()), 100)
        t.zero(100)
        self.assertAlmostEqual(t.heading(), 0)
        self.assertTrue(t.fresh(101))
        self.assertFalse(t.fresh(102))
        t.accept(parse_sample(PACKET.decode().replace("1020", "2")), 103)
        self.assertAlmostEqual(t.heading(), 359.99)
        self.assertEqual(len(t.history), 1)

    def test_render_live_stale_legacy_missing_and_extremes(self):
        pygame.font.init()
        surface = pygame.Surface((WIDTH, HEIGHT))
        t = Telemetry(demo=True)
        render(surface, t, 100)
        self.assertEqual(tuple(surface.get_at((250, 290)))[:3], SKY)
        self.assertEqual(tuple(surface.get_at((250, 450)))[:3], GROUND)
        for i in range(501):
            t.accept(demo_sample(i / 50), 90 + i / 50)
        render(surface, t, 100)
        render(surface, t, 103)
        t.accept(parse_sample("ANGLE,180,90"), 105)
        render(surface, t, 105)
        self.assertIn("Firmware antigo", t.message)

    @unittest.skipUnless(os.name == "posix", "serial virtual requer POSIX")
    def test_real_serial_transport(self):
        import pty
        import select
        master, slave = pty.openpty()
        t = Telemetry(os.ttyname(slave))
        try:
            t.connect()
            self.assertIsNotNone(t.device)
            os.write(master, PACKET[:20])
            select.select([t.device.fileno()], [], [], 1)
            t.update(time.monotonic())
            self.assertIsNone(t.sample)
            os.write(master, PACKET[20:])
            select.select([t.device.fileno()], [], [], 1)
            t.update(time.monotonic())
            self.assertAlmostEqual(t.sample.accel[2], 9.8)
            os.close(master)
            master = None
            t.update(time.monotonic())
            self.assertIsNone(t.device)
            self.assertIsNone(t.received)
        finally:
            t.close()
            if master is not None:
                os.close(master)
            os.close(slave)


if __name__ == "__main__":
    unittest.main()
