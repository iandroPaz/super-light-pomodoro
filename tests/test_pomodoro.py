import contextlib
import io
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import pomodoro as p


class PomodoroTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.config_path = self.root / "config.json"
        self.state_path = self.root / "state.json"
        self.config = dict(p.DEFAULT_CONFIG)
        self.write_config()

    def write_config(self, **changes):
        self.config.update(changes)
        self.config_path.write_text(json.dumps(self.config), encoding="utf-8")

    def run_command(self, command):
        output = io.StringIO()
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
            code = p.main([command, "--config", str(self.config_path),
                           "--estado", str(self.state_path)])
        return code, output.getvalue()

    def test_complete_cycle_requires_explicit_starts(self):
        self.write_config(foco_minutos=0.0001, pausa_curta_minutos=0.0001,
                          pausa_longa_minutos=0.0001, pomodoros_ate_pausa_longa=2,
                          alerta_sonoro=False, notificacao_desktop=False)
        expected = ["pausa_curta", "foco", "pausa_longa", "foco"]
        with patch.object(p, "alert") as alert:
            for starts, stage in enumerate(expected, 1):
                code, output = self.run_command("iniciar")
                self.assertEqual(code, 0, output)
                self.assertEqual(p.load_state(self.state_path)["proxima_etapa"], stage)
                self.assertEqual(alert.call_count, starts)
        state = p.load_state(self.state_path)
        self.assertEqual(state["pomodoros_concluidos"], 2)
        self.assertEqual(state["pomodoros_no_ciclo"], 0)

    def test_status_never_starts_or_creates_state(self):
        with patch.object(p, "countdown") as countdown:
            code, output = self.run_command("status")
        self.assertEqual(code, 0)
        self.assertIn("Foco (25 min)", output)
        countdown.assert_not_called()
        self.assertFalse(self.state_path.exists())

    def test_cancel_does_not_advance_or_alert(self):
        original = {"pomodoros_concluidos": 1, "pomodoros_no_ciclo": 1,
                    "proxima_etapa": "pausa_curta"}
        p.save_state(self.state_path, original)
        with patch.object(p, "countdown", side_effect=KeyboardInterrupt), patch.object(p, "alert") as alert:
            code, _ = self.run_command("iniciar")
        self.assertEqual(code, 130)
        self.assertEqual(p.load_state(self.state_path), original)
        alert.assert_not_called()
        with p.state_lock(self.state_path):
            pass  # O cancelamento também precisa liberar a trava.

    def test_reset_recovers_corrupt_state_without_starting(self):
        self.state_path.write_text("{inválido", encoding="utf-8")
        with patch.object(p, "countdown") as countdown:
            code, _ = self.run_command("reiniciar")
        self.assertEqual(code, 0)
        self.assertEqual(p.load_state(self.state_path), p.initial_state())
        countdown.assert_not_called()

    def test_invalid_config_values_are_rejected(self):
        for field, value in [("foco_minutos", 0), ("foco_minutos", True),
                             ("foco_minutos", "25"), ("pausa_curta_minutos", -1),
                             ("pausa_longa_minutos", float("nan")),
                             ("foco_minutos", float("inf")),
                             ("foco_minutos", 10 ** 400),
                             ("pomodoros_ate_pausa_longa", 1.5),
                             ("pomodoros_ate_pausa_longa", False),
                             ("alerta_sonoro", "true"), ("notificacao_desktop", 1)]:
            with self.subTest(field=field, value=value):
                self.config = dict(p.DEFAULT_CONFIG)
                self.write_config(**{field: value})
                with self.assertRaises(p.PomodoroError):
                    p.load_config(self.config_path)

    def test_unknown_property_is_rejected(self):
        self.write_config(focoo_minutos=10)
        with self.assertRaises(p.PomodoroError):
            p.load_config(self.config_path)

    def test_missing_properties_use_defaults(self):
        self.config_path.write_text('{"foco_minutos": 0.5}', encoding="utf-8")
        config = p.load_config(self.config_path)
        self.assertEqual(config["foco_minutos"], 0.5)
        self.assertEqual(config["pausa_curta_minutos"], 5)

    def test_invalid_state_has_readable_error(self):
        for change in ({"proxima_etapa": []}, {"proxima_etapa": "desconhecida"},
                       {"pomodoros_no_ciclo": 3}, {"pomodoros_concluidos": True}):
            with self.subTest(change=change):
                p.save_state(self.state_path, {**p.initial_state(), **change})
                with self.assertRaises(p.PomodoroError):
                    p.load_state(self.state_path)

    def test_second_timer_and_reset_are_blocked(self):
        with p.state_lock(self.state_path):
            for command in ("iniciar", "reiniciar"):
                code, output = self.run_command(command)
                self.assertEqual(code, 1)
                self.assertIn("Já existe uma contagem", output)

    def test_timer_uses_elapsed_time_instead_of_decrementing(self):
        clock = [100.0]
        def delayed_sleep(seconds):
            clock[0] += seconds + 0.25
        with patch.object(p.time, "monotonic", side_effect=lambda: clock[0]), \
                patch.object(p.time, "sleep", side_effect=delayed_sleep) as sleep, \
                contextlib.redirect_stdout(io.StringIO()):
            p.countdown(3, "Foco")
        self.assertEqual(sleep.call_count, 3)
        self.assertEqual(sleep.call_args.args[0], 0.5)

    def test_macos_sends_one_notification_with_sound(self):
        with patch.object(p.sys, "platform", "darwin"), \
                patch.object(p.subprocess, "run") as run, \
                contextlib.redirect_stdout(io.StringIO()) as output:
            p.alert("Foco", self.config)
        run.assert_called_once()
        self.assertIn('sound name "Glass"', run.call_args.args[0][2])
        self.assertNotIn("\a", output.getvalue())

    def test_notification_failure_falls_back_to_single_bell(self):
        with patch.object(p.sys, "platform", "darwin"), \
                patch.object(p.subprocess, "run", side_effect=subprocess.TimeoutExpired("osascript", 5)), \
                contextlib.redirect_stdout(io.StringIO()) as output:
            p.alert("Foco", self.config)
        self.assertEqual(output.getvalue(), "\a")

    def test_disabled_alert_is_silent(self):
        with patch.object(p.subprocess, "run") as run, \
                contextlib.redirect_stdout(io.StringIO()) as output:
            p.alert("Foco", {**self.config, "alerta_sonoro": False, "notificacao_desktop": False})
        run.assert_not_called()
        self.assertEqual(output.getvalue(), "")

    def test_clock_formats_remaining_seconds_and_hours(self):
        self.assertEqual(p.clock_text(60.01), "01:01")
        self.assertEqual(p.clock_text(3600), "01:00:00")
        self.assertEqual(p.clock_text(-1), "00:00")


if __name__ == "__main__":
    unittest.main()
