import contextlib
from datetime import date
import io
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import Mock, patch

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

    def run_command(self, command, state_option="--estado"):
        output = io.StringIO()
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
            code = p.main([command, "--config", str(self.config_path),
                           state_option, str(self.state_path)])
        return code, output.getvalue()

    def test_history_accumulates_completed_focus_and_both_breaks(self):
        self.write_config(pomodoros_ate_pausa_longa=2)
        with patch.object(p, "countdown", return_value=None), \
                patch.object(p, "alert"), patch.object(p, "date", wraps=date) as calendar:
            calendar.today.return_value = date(2026, 11, 3)
            for minutes in (25, 25, 10, 10):
                self.write_config(foco_minutos=minutes)
                code, output = self.run_command("start")
                self.assertEqual(code, 0, output)
        self.assertEqual(p.read_json(self.root / "pomodoro-history.json"), {
            "2026-11-03": {
                "ciclos_concluidos": 2, "foco_minutos": 35, "pausa_minutos": 20,
            },
        })

    def test_history_preserves_previous_days_and_survives_reset(self):
        with patch.object(p, "countdown", return_value=None), \
                patch.object(p, "alert"), patch.object(p, "date", wraps=date) as calendar:
            calendar.today.return_value = date(2026, 11, 3)
            code, output = self.run_command("start")
            self.assertEqual(code, 0, output)
            code, output = self.run_command("reset")
            self.assertEqual(code, 0, output)
            calendar.today.return_value = date(2026, 11, 4)
            code, output = self.run_command("start")
            self.assertEqual(code, 0, output)
        history = p.read_json(self.root / "pomodoro-history.json")
        self.assertEqual(set(history), {"2026-11-03", "2026-11-04"})
        for totals in history.values():
            self.assertEqual(totals, {
                "ciclos_concluidos": 1, "foco_minutos": 25, "pausa_minutos": 0,
            })

    def test_history_excludes_status_reset_skips_and_cancellations(self):
        history_path = self.root / "pomodoro-history.json"
        for command in ("status", "reset", "skip", "pular"):
            code, output = self.run_command(command)
            self.assertEqual(code, 0, output)
            self.assertFalse(history_path.exists())
        with patch.object(p, "countdown", return_value="skipped"):
            code, output = self.run_command("start")
        self.assertEqual(code, 0, output)
        self.assertFalse(history_path.exists())
        with patch.object(p, "countdown", side_effect=KeyboardInterrupt):
            code, _ = self.run_command("start")
        self.assertEqual(code, 130)
        self.assertFalse(history_path.exists())

    def test_invalid_history_is_preserved_and_blocks_timer_before_start(self):
        history_path = self.root / "pomodoro-history.json"
        for contents in ("{broken", '{"2026-11-03": {}}',
                         '{"bad-date": {}}',
                         '{"2026-11-03": {"ciclos_concluidos": true, '
                         '"foco_minutos": 0, "pausa_minutos": 0}}',
                         '{"2026-11-03": {"ciclos_concluidos": 0, '
                         '"foco_minutos": NaN, "pausa_minutos": 0}}'):
            with self.subTest(contents=contents):
                history_path.write_text(contents, encoding="utf-8")
                with patch.object(p, "countdown") as countdown:
                    code, output = self.run_command("start")
                self.assertEqual(code, 1, output)
                countdown.assert_not_called()
                self.assertFalse(self.state_path.exists())
                self.assertEqual(history_path.read_text(encoding="utf-8"), contents)

    def test_history_uses_completion_date_and_excludes_paused_time(self):
        self.write_config(foco_minutos=0.05)
        clock = [0.0]
        controls = Mock(fd=0)
        controls.read_commands.side_effect = [[], ["pause"], [], ["resume"], [], []]
        def sleep(seconds):
            clock[0] += seconds
            calendar.today.return_value = date(2026, 11, 4)
        with patch.object(p, "CountdownInput", return_value=controls), \
                patch.object(p.time, "monotonic", side_effect=lambda: clock[0]), \
                patch.object(p.time, "sleep", side_effect=sleep), \
                patch.object(p, "alert"), patch.object(p, "date", wraps=date) as calendar:
            calendar.today.return_value = date(2026, 11, 3)
            code, output = self.run_command("start")
        self.assertEqual(code, 0, output)
        self.assertEqual(clock[0], 5)
        self.assertEqual(p.read_json(self.root / "pomodoro-history.json"), {
            "2026-11-04": {
                "ciclos_concluidos": 1, "foco_minutos": 0.05, "pausa_minutos": 0,
            },
        })

    def test_english_commands_share_progress_with_portuguese(self):
        with patch.object(p, "countdown") as countdown, patch.object(p, "alert"):
            code, output = self.run_command("start", "--state")
            self.assertEqual(code, 0, output)
            countdown.assert_called_once_with(25 * 60, "Foco", language="pt")
        self.assertEqual(p.load_state(self.state_path), {
            "pomodoros_concluidos": 1, "pomodoros_no_ciclo": 1,
            "proxima_etapa": "pausa_curta",
        })
        code, output = self.run_command("status")
        self.assertEqual(code, 0, output)
        self.assertIn("Pausa curta (5 min)", output)
        with patch.object(p, "countdown") as countdown:
            code, output = self.run_command("reset", "--state")
            self.assertEqual(code, 0, output)
            countdown.assert_not_called()
        self.assertEqual(p.load_state(self.state_path), p.initial_state())

    def test_english_language_applies_to_every_stage_and_reset(self):
        self.write_config(idioma="en", pomodoros_ate_pausa_longa=2)
        for command, label in [("iniciar", "Focus"), ("start", "Short break"),
                               ("start", "Focus"), ("iniciar", "Long break")]:
            with self.subTest(label=label), patch.object(p, "countdown") as countdown, \
                    patch.object(p, "alert") as alert:
                code, output = self.run_command(command)
                self.assertEqual(code, 0, output)
                self.assertIn(f"{label} started.", output)
                self.assertIn(f"{label} completed!", output)
                self.assertIn("Next stage:", output)
                self.assertIn("Completed focus sessions:", output)
                self.assertIn("To start: python3 pomodoro.py start", output)
                self.assertEqual(countdown.call_args.args[1], label)
                self.assertEqual(countdown.call_args.kwargs["language"], "en")
                self.assertEqual(alert.call_args.args[0], label)
        code, output = self.run_command("reiniciar")
        self.assertEqual(code, 0, output)
        self.assertIn("Progress reset.", output)
        self.assertEqual(p.load_state(self.state_path), p.initial_state())

    def test_changing_language_preserves_progress(self):
        original = {"pomodoros_concluidos": 3, "pomodoros_no_ciclo": 3,
                    "proxima_etapa": "pausa_curta"}
        p.save_state(self.state_path, original)
        for language, expected in [("en", "Short break"), ("pt", "Pausa curta")]:
            self.write_config(idioma=language)
            code, output = self.run_command("status", "--state")
            self.assertEqual(code, 0, output)
            self.assertIn(expected, output)
            self.assertEqual(p.load_state(self.state_path), original)

    def test_help_uses_language_from_custom_config_in_either_order(self):
        for language, expected in [("en", "path to the configuration JSON"),
                                   ("pt", "caminho do JSON de configuração")]:
            self.write_config(idioma=language)
            for argv in (["--config", str(self.config_path), "--help"],
                         ["--help", "--config", str(self.config_path)]):
                with self.subTest(language=language, argv=argv), \
                        contextlib.redirect_stdout(io.StringIO()) as output:
                    with self.assertRaises(SystemExit) as exit:
                        p.main(argv)
                    self.assertEqual(exit.exception.code, 0)
                    self.assertIn(expected, output.getvalue())
        self.assertFalse(self.state_path.exists())

    def test_english_cancel_does_not_advance(self):
        self.write_config(idioma="en")
        with patch.object(p, "countdown", side_effect=KeyboardInterrupt), \
                patch.object(p, "alert") as alert:
            code, output = self.run_command("start")
        self.assertEqual(code, 130)
        self.assertIn("Countdown cancelled.", output)
        self.assertFalse(self.state_path.exists())
        alert.assert_not_called()

    def test_english_validation_errors(self):
        for changes, expected in [({"foco_minutos": 0}, "greater than 0"),
                                  ({"alerta_sonoro": 1}, "must be true or false"),
                                  ({"pomodoros_ate_pausa_longa": 0}, "must be an integer"),
                                  ({"unknown": 1}, "Unknown property")]:
            with self.subTest(changes=changes):
                self.config = dict(p.DEFAULT_CONFIG)
                self.write_config(idioma="en", **changes)
                code, output = self.run_command("status")
                self.assertEqual(code, 1, output)
                self.assertIn("Error:", output)
                self.assertIn(expected, output)

    def test_english_state_and_lock_errors(self):
        self.write_config(idioma="en")
        p.save_state(self.state_path, {**p.initial_state(), "proxima_etapa": "invalid"})
        code, output = self.run_command("status")
        self.assertEqual(code, 1)
        self.assertIn("Invalid progress. Use 'reset'", output)
        with p.state_lock(self.state_path):
            code, output = self.run_command("reset")
        self.assertEqual(code, 1)
        self.assertIn("A countdown is already running", output)

    def test_invalid_language_is_rejected_without_creating_progress(self):
        for language in ("es", "EN", "", None, [], True):
            with self.subTest(language=language):
                self.write_config(idioma=language)
                code, output = self.run_command("start")
                self.assertEqual(code, 1, output)
                self.assertIn("idioma deve ser 'pt' ou 'en'", output)
                self.assertFalse(self.state_path.exists())

    def test_reset_and_help_remain_available_with_broken_config(self):
        self.config_path.write_text("{invalid", encoding="utf-8")
        code, output = self.run_command("status")
        self.assertEqual(code, 1)
        self.assertIn("Não foi possível ler", output)
        code, output = self.run_command("reset")
        self.assertEqual(code, 0, output)
        self.assertEqual(p.load_state(self.state_path), p.initial_state())
        with contextlib.redirect_stdout(io.StringIO()) as output:
            with self.assertRaises(SystemExit) as exit:
                p.main(["--config", str(self.config_path), "--help"])
        self.assertEqual(exit.exception.code, 0)
        self.assertIn("Pomodoro leve", output.getvalue())

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

    def test_skip_aliases_advance_one_stage_without_countdown_or_alert(self):
        expected = [
            (1, 1, "pausa_curta"), (1, 1, "foco"),
            (2, 2, "pausa_longa"), (2, 0, "foco"),
        ]
        for language, skipped in [("pt", "pulado."), ("en", "skipped.")]:
            self.write_config(idioma=language, pomodoros_ate_pausa_longa=2)
            p.save_state(self.state_path, p.initial_state())
            for command, (total, cycle, stage) in zip(
                    ("pular", "skip", "skip", "pular"), expected):
                with self.subTest(language=language, command=command, stage=stage), \
                        patch.object(p, "countdown") as countdown, \
                        patch.object(p, "alert") as alert:
                    code, output = self.run_command(command, "--state")
                    self.assertEqual(code, 0, output)
                    self.assertIn(skipped, output)
                    self.assertEqual(p.load_state(self.state_path), {
                        "pomodoros_concluidos": total, "pomodoros_no_ciclo": cycle,
                        "proxima_etapa": stage,
                    })
                    countdown.assert_not_called()
                    alert.assert_not_called()

    def test_skip_during_countdown_or_pause_advances_only_once(self):
        for command in ("skip", "pular"):
            for paused in (False, True):
                for stage in ("foco", "pausa_curta", "pausa_longa"):
                    original = {"pomodoros_concluidos": 4, "pomodoros_no_ciclo": 4,
                                "proxima_etapa": stage}
                    p.save_state(self.state_path, original)
                    controls = Mock(fd=0)
                    controls.read_commands.side_effect = (
                        [["pause"]] if paused else []
                    ) + [[command, command]]
                    with self.subTest(command=command, paused=paused, stage=stage), \
                            patch.object(p, "CountdownInput", return_value=controls), \
                            patch.object(p.time, "monotonic", return_value=0), \
                            patch.object(p.time, "sleep") as sleep, \
                            patch.object(p, "alert") as alert:
                        code, output = self.run_command("iniciar")
                        self.assertEqual(code, 0, output)
                        self.assertIn("pulado.", output)
                        self.assertNotIn("concluído!", output)
                        self.assertEqual(p.load_state(self.state_path),
                                         p.advance(original, self.config))
                        self.assertEqual(sleep.call_count, int(paused))
                        alert.assert_not_called()
                    with p.state_lock(self.state_path):
                        pass  # Pular precisa liberar a trava para a próxima etapa.

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
        self.assertEqual(config["idioma"], "pt")

    def test_invalid_state_has_readable_error(self):
        for change in ({"proxima_etapa": []}, {"proxima_etapa": "desconhecida"},
                       {"pomodoros_no_ciclo": 3}, {"pomodoros_concluidos": True}):
            with self.subTest(change=change):
                p.save_state(self.state_path, {**p.initial_state(), **change})
                with self.assertRaises(p.PomodoroError):
                    p.load_state(self.state_path)

    def test_second_timer_and_reset_are_blocked(self):
        with p.state_lock(self.state_path):
            for command in ("iniciar", "start", "reiniciar", "reset", "pular", "skip"):
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

    def test_pause_preserves_remaining_time_and_resume_is_idempotent(self):
        for pause_command, resume_command in [("pause", "resume"), ("pausar", "retomar"),
                                               ("pausar", "continuar")]:
            with self.subTest(pause=pause_command, resume=resume_command):
                clock = [0.0]
                controls = Mock(fd=0)
                controls.read_commands.side_effect = [
                    [], [pause_command], [pause_command], [], [],
                    [resume_command], [resume_command], [],
                ]
                def sleep(seconds):
                    clock[0] += seconds
                with patch.object(p, "CountdownInput", return_value=controls), \
                        patch.object(p.time, "monotonic", side_effect=lambda: clock[0]), \
                        patch.object(p.time, "sleep", side_effect=sleep), \
                        contextlib.redirect_stdout(io.StringIO()) as output:
                    p.countdown(3, "Foco")
                self.assertEqual(clock[0], 7)
                self.assertEqual(output.getvalue().count("Pausado: 00:02"), 1)
                self.assertEqual(output.getvalue().count("Contagem retomada: 00:02"), 1)

    def test_p_toggles_pause_and_uses_selected_language(self):
        clock = [0.0]
        controls = Mock(fd=0)
        controls.read_commands.side_effect = [[], ["p"], [], ["p"], [], []]
        def sleep(seconds):
            clock[0] += seconds
        with patch.object(p, "CountdownInput", return_value=controls), \
                patch.object(p.time, "monotonic", side_effect=lambda: clock[0]), \
                patch.object(p.time, "sleep", side_effect=sleep), \
                contextlib.redirect_stdout(io.StringIO()) as output:
            p.countdown(3, "Focus", language="en")
        self.assertEqual(clock[0], 5)
        self.assertIn("Paused: 00:02", output.getvalue())
        self.assertIn("Countdown resumed: 00:02", output.getvalue())
        self.assertIn("pause + Enter", output.getvalue())

    def test_cancel_while_paused_does_not_advance_or_alert(self):
        controls = Mock(fd=0)
        controls.read_commands.return_value = ["pause"]
        with patch.object(p, "CountdownInput", return_value=controls), \
                patch.object(p.time, "sleep", side_effect=KeyboardInterrupt), \
                patch.object(p, "alert") as alert:
            code, output = self.run_command("iniciar")
        self.assertEqual(code, 130)
        self.assertIn("Pausado:", output)
        self.assertFalse(self.state_path.exists())
        alert.assert_not_called()
        with p.state_lock(self.state_path):
            pass

    def test_short_paused_timer_still_sleeps_one_second(self):
        controls = Mock(fd=0)
        controls.read_commands.return_value = ["pause"]
        with patch.object(p, "CountdownInput", return_value=controls), \
                patch.object(p.time, "monotonic", return_value=0), \
                patch.object(p.time, "sleep", side_effect=KeyboardInterrupt) as sleep, \
                contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaises(KeyboardInterrupt):
                p.countdown(0.01, "Foco")
        sleep.assert_called_once_with(1)

    def test_commands_disabled_when_stdin_is_not_a_terminal(self):
        with patch.object(p.sys, "stdin", io.StringIO("pause\n")):
            controls = p.CountdownInput()
            self.assertIsNone(controls.fd)
            self.assertEqual(controls.read_commands(), [])

    @unittest.skipIf(p.os.name == "nt", "POSIX terminal input")
    def test_terminal_commands_handle_partial_reads_multiple_lines_and_eof(self):
        terminal = Mock()
        terminal.fileno.return_value = 42
        with patch.object(p.sys, "stdin", terminal), \
                patch.object(p.select, "select", return_value=([42], [], [])), \
                patch.object(p.os, "read", side_effect=[b"pau", b"se\n P \nresume\n", b""]):
            controls = p.CountdownInput()
            self.assertEqual(controls.read_commands(), [])
            self.assertEqual(controls.read_commands(), ["pause", "p", "resume"])
            self.assertEqual(controls.read_commands(), [])
            self.assertIsNone(controls.fd)

    def test_macos_sends_one_notification_with_sound(self):
        with patch.object(p.sys, "platform", "darwin"), \
                patch.object(p.subprocess, "run") as run, \
                contextlib.redirect_stdout(io.StringIO()) as output:
            p.alert("Foco", self.config)
        run.assert_called_once()
        self.assertIn('sound name "Glass"', run.call_args.args[0][2])
        self.assertNotIn("\a", output.getvalue())

    def test_notifications_use_selected_language_on_macos_and_linux(self):
        for platform in ("darwin", "linux"):
            for language, label, expected in [("pt", "Foco", "Foco terminou."),
                                               ("en", "Focus", "Focus finished.")]:
                with self.subTest(platform=platform, language=language), \
                        patch.object(p.sys, "platform", platform), \
                        patch.object(p.shutil, "which", return_value="/usr/bin/notify-send"), \
                        patch.object(p.subprocess, "run") as run:
                    p.alert(label, {**self.config, "idioma": language, "alerta_sonoro": False})
                run.assert_called_once()
                self.assertIn(expected, run.call_args.args[0][2])

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
