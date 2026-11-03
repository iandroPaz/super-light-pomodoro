#!/usr/bin/env python3
"""Pomodoro de terminal, sem dependências externas e sem início automático."""

import argparse
import contextlib
from datetime import date
import json
import math
import os
from pathlib import Path
import select
import shutil
import subprocess
import sys
import tempfile
import time


ROOT = Path(__file__).resolve().parent
CONFIG_PATH = ROOT / "pomodoro.json"
STATE_PATH = ROOT / ".pomodoro-state.json"
MESSAGES = {
    "pt": {
        "foco": "Foco", "pausa_curta": "Pausa curta", "pausa_longa": "Pausa longa",
        "read_error": "Não foi possível ler {name}: {error}",
        "json_object": "{name} precisa conter um objeto JSON.",
        "unknown_property": "Propriedade desconhecida: {keys}.",
        "invalid_language": "idioma deve ser 'pt' ou 'en'.",
        "invalid_duration": "{key} deve ser um número maior que 0 e até 1440.",
        "invalid_interval": "pomodoros_ate_pausa_longa deve ser um inteiro maior que 0.",
        "invalid_boolean": "{key} deve ser true ou false.",
        "invalid_state": "Progresso inválido. Use 'reiniciar' para zerar o ciclo.",
        "invalid_history": "Histórico inválido em {name}. Corrija o arquivo ou restaure um backup.",
        "locked": "Já existe uma contagem em andamento nesta configuração.",
        "notification": "{label} terminou. Próxima etapa aguarda seu comando.",
        "status": "Próxima etapa: {label} ({minutes:g} min)",
        "completed_count": "Focos concluídos: {total} | Neste ciclo: {cycle}/{interval}",
        "start_hint": "Para iniciar: python3 pomodoro.py iniciar",
        "description": "Pomodoro leve. Cada etapa só começa com o comando iniciar (start).",
        "epilog": "Edite pomodoro.json para configurar os tempos (em minutos) e o idioma (pt/en).",
        "command_help": "iniciar/start: próxima etapa; pular/skip: avançar uma etapa; status: consultar; reiniciar/reset: zerar o ciclo",
        "config_help": "caminho do JSON de configuração",
        "state_help": "caminho do arquivo de progresso",
        "help": "mostra esta ajuda e encerra",
        "arguments": "argumentos posicionais", "options": "opções",
        "reset": "Progresso zerado. A próxima etapa será foco; nenhuma contagem foi iniciada.",
        "started": "{label} iniciado. Ctrl+C cancela sem avançar o ciclo.",
        "controls_hint": "Durante a contagem: pause + Enter pausa; resume + Enter retoma; p + Enter alterna; pular/skip + Enter avança uma etapa.",
        "paused": "Pausado",
        "resumed": "Contagem retomada",
        "completed": "{label} concluído!",
        "skipped": "{label} pulado. Nenhuma contagem foi iniciada para a próxima etapa.",
        "cancelled": "\nContagem cancelada. A etapa continua pendente e recomeça do zero no próximo iniciar.",
        "error": "Erro: {error}",
    },
    "en": {
        "foco": "Focus", "pausa_curta": "Short break", "pausa_longa": "Long break",
        "read_error": "Could not read {name}: {error}",
        "json_object": "{name} must contain a JSON object.",
        "unknown_property": "Unknown property: {keys}.",
        "invalid_language": "idioma must be 'pt' or 'en'.",
        "invalid_duration": "{key} must be a number greater than 0 and at most 1440.",
        "invalid_interval": "pomodoros_ate_pausa_longa must be an integer greater than 0.",
        "invalid_boolean": "{key} must be true or false.",
        "invalid_state": "Invalid progress. Use 'reset' to reset the cycle.",
        "invalid_history": "Invalid history in {name}. Fix the file or restore a backup.",
        "locked": "A countdown is already running with this configuration.",
        "notification": "{label} finished. The next stage awaits your command.",
        "status": "Next stage: {label} ({minutes:g} min)",
        "completed_count": "Completed focus sessions: {total} | This cycle: {cycle}/{interval}",
        "start_hint": "To start: python3 pomodoro.py start",
        "description": "Lightweight Pomodoro. Each stage starts only with the start command.",
        "epilog": "Edit pomodoro.json to configure durations (in minutes) and language (pt/en).",
        "command_help": "iniciar/start: next stage; pular/skip: advance one stage; status: show progress; reiniciar/reset: reset the cycle",
        "config_help": "path to the configuration JSON",
        "state_help": "path to the progress file",
        "help": "show this help message and exit",
        "arguments": "positional arguments", "options": "options",
        "reset": "Progress reset. The next stage is focus; no countdown has started.",
        "started": "{label} started. Ctrl+C cancels without advancing the cycle.",
        "controls_hint": "During the countdown: pause + Enter pauses; resume + Enter resumes; p + Enter toggles; pular/skip + Enter advances one stage.",
        "paused": "Paused",
        "resumed": "Countdown resumed",
        "completed": "{label} completed!",
        "skipped": "{label} skipped. No countdown has started for the next stage.",
        "cancelled": "\nCountdown cancelled. The stage remains pending and starts over on the next start command.",
        "error": "Error: {error}",
    },
}
LABELS = {stage: MESSAGES["pt"][stage] for stage in ("foco", "pausa_curta", "pausa_longa")}
DEFAULT_CONFIG = {
    "idioma": "pt",
    "foco_minutos": 25,
    "pausa_curta_minutos": 5,
    "pausa_longa_minutos": 15,
    "pomodoros_ate_pausa_longa": 4,
    "alerta_sonoro": True,
    "notificacao_desktop": True,
}


class PomodoroError(Exception):
    """Erro que pode ser apresentado diretamente no terminal."""

    def __init__(self, message_key, **values):
        self.key = message_key
        self.values = values
        super().__init__(self.render("pt"))

    def render(self, language):
        return message(language, self.key, **self.values)


def message(language, message_key, **values):
    return MESSAGES[language][message_key].format(**values)


def config_language(data):
    language = data.get("idioma", DEFAULT_CONFIG["idioma"])
    if not isinstance(language, str) or language not in MESSAGES:
        raise PomodoroError("invalid_language")
    return language


def read_json(path):
    try:
        with path.open(encoding="utf-8") as source:
            data = json.load(source)
    except (OSError, ValueError) as exc:
        raise PomodoroError("read_error", name=path.name, error=exc) from exc
    if not isinstance(data, dict):
        raise PomodoroError("json_object", name=path.name)
    return data


def load_config(path, data=None):
    if data is None:
        data = read_json(path)
    config_language(data)
    unknown = data.keys() - DEFAULT_CONFIG.keys()
    if unknown:
        raise PomodoroError("unknown_property", keys=", ".join(sorted(unknown)))
    config = {**DEFAULT_CONFIG, **data}
    for key in ("foco_minutos", "pausa_curta_minutos", "pausa_longa_minutos"):
        value = config[key]
        if (type(value) not in (int, float) or value <= 0 or value > 1440
                or not math.isfinite(value)):
            raise PomodoroError("invalid_duration", key=key)
    interval = config["pomodoros_ate_pausa_longa"]
    if type(interval) is not int or interval < 1:
        raise PomodoroError("invalid_interval")
    for key in ("alerta_sonoro", "notificacao_desktop"):
        if type(config[key]) is not bool:
            raise PomodoroError("invalid_boolean", key=key)
    return config


def initial_state():
    return {"pomodoros_concluidos": 0, "pomodoros_no_ciclo": 0, "proxima_etapa": "foco"}


def load_state(path):
    if not path.exists():
        return initial_state()
    state = read_json(path)
    for key in ("pomodoros_concluidos", "pomodoros_no_ciclo"):
        if type(state.get(key)) is not int or state[key] < 0:
            raise PomodoroError("invalid_state")
    if (not isinstance(state.get("proxima_etapa"), str)
            or state["proxima_etapa"] not in LABELS
            or state["pomodoros_no_ciclo"] > state["pomodoros_concluidos"]):
        raise PomodoroError("invalid_state")
    return state


def save_state(path, state):
    """Substitui o arquivo atomicamente para não deixar um JSON pela metade."""
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                         prefix=".pomodoro-", suffix=".tmp", delete=False) as out:
            temporary = Path(out.name)
            json.dump(state, out, ensure_ascii=False, indent=2)
            out.write("\n")
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def load_history(path):
    if not path.exists():
        return {}
    history = read_json(path)
    for day, totals in history.items():
        try:
            if date.fromisoformat(day).isoformat() != day:
                raise ValueError
        except ValueError:
            raise PomodoroError("invalid_history", name=path.name) from None
        if not isinstance(totals, dict):
            raise PomodoroError("invalid_history", name=path.name)
        cycles = totals.get("ciclos_concluidos")
        if type(cycles) is not int or cycles < 0:
            raise PomodoroError("invalid_history", name=path.name)
        for key in ("foco_minutos", "pausa_minutos"):
            value = totals.get(key)
            if (type(value) not in (int, float) or value < 0
                    or (isinstance(value, float) and not math.isfinite(value))):
                raise PomodoroError("invalid_history", name=path.name)
    return history


def record_completion(path, stage, minutes):
    """Acumula somente etapas concluídas na data local de conclusão."""
    with state_lock(path):
        history = load_history(path)
        totals = history.setdefault(date.today().isoformat(), {
            "ciclos_concluidos": 0, "foco_minutos": 0, "pausa_minutos": 0,
        })
        if stage == "foco":
            totals["ciclos_concluidos"] += 1
        key = "foco_minutos" if stage == "foco" else "pausa_minutos"
        totals[key] = round(totals[key] + minutes, 10)
        save_state(path, history)


@contextlib.contextmanager
def state_lock(path):
    """O sistema libera a trava mesmo se o processo for encerrado à força."""
    with Path(str(path) + ".lock").open("a+b") as handle:
        try:
            if os.name == "nt":
                import msvcrt
                if handle.tell() == 0:
                    handle.write(b"0")
                    handle.flush()
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise PomodoroError("locked") from exc
        try:
            yield
        finally:
            if os.name == "nt":
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def advance(state, config):
    result = dict(state)
    if state["proxima_etapa"] == "foco":
        result["pomodoros_concluidos"] += 1
        result["pomodoros_no_ciclo"] += 1
        result["proxima_etapa"] = (
            "pausa_longa" if result["pomodoros_no_ciclo"] >= config["pomodoros_ate_pausa_longa"]
            else "pausa_curta"
        )
    else:
        result["proxima_etapa"] = "foco"
        if state["proxima_etapa"] == "pausa_longa":
            result["pomodoros_no_ciclo"] = 0
    return result


def clock_text(seconds):
    minutes, seconds = divmod(max(0, math.ceil(seconds)), 60)
    hours, minutes = divmod(minutes, 60)
    return f"{hours:02}:{minutes:02}:{seconds:02}" if hours else f"{minutes:02}:{seconds:02}"


class CountdownInput:
    """Lê comandos do terminal sem bloquear a contagem ou criar threads."""

    def __init__(self):
        self.buffer = ""
        self.fd = None
        try:
            if sys.stdin.isatty():
                self.fd = sys.stdin.fileno()
        except (AttributeError, OSError, ValueError):
            pass

    def read_commands(self):
        if self.fd is None:
            return []
        if os.name == "nt":
            import msvcrt
            data = ""
            while msvcrt.kbhit():
                char = msvcrt.getwch()
                if char == "\x03":
                    raise KeyboardInterrupt
                if char in ("\x00", "\xe0"):
                    msvcrt.getwch()  # Ignora teclas especiais, como as setas.
                elif char == "\b":
                    self.buffer = self.buffer[:-1]
                elif char == "\r":
                    data += self.buffer + "\n"
                    self.buffer = ""
                else:
                    self.buffer += char
        else:
            if not select.select([self.fd], [], [], 0)[0]:
                return []
            chunk = os.read(self.fd, 4096)
            if not chunk:
                self.fd = None
                return []
            data = self.buffer + chunk.decode("utf-8", errors="replace")
            self.buffer = ""
        lines = data.split("\n")
        self.buffer += lines.pop()
        return [line.strip().lower() for line in lines]


def countdown(seconds, label, language="pt"):
    """Relógio monotônico; o tempo pausado não entra na contagem."""
    animated = sys.stdout.isatty()
    controls = CountdownInput()
    deadline = time.monotonic() + seconds
    remaining = seconds
    paused = False
    frame = 0
    if controls.fd is not None:
        print(message(language, "controls_hint"), flush=True)
    if not animated:
        print(f"{label}: {clock_text(seconds)}", flush=True)
    try:
        while True:
            now = time.monotonic()
            if not paused:
                remaining = max(0, deadline - now)
            for command in controls.read_commands():
                if command in ("skip", "pular"):
                    return "skipped"
                should_pause = command in ("pause", "pausar") or (command == "p" and not paused)
                should_resume = command in ("resume", "retomar", "continuar") or (command == "p" and paused)
                if should_pause and not paused and remaining > 0:
                    paused = True
                    if not animated:
                        print(f"{message(language, 'paused')}: {clock_text(remaining)}", flush=True)
                elif should_resume and paused:
                    paused = False
                    deadline = now + remaining
                    if not animated:
                        print(f"{message(language, 'resumed')}: {clock_text(remaining)}", flush=True)
            if animated:
                display_label = f"{label} ({message(language, 'paused')})" if paused else label
                columns = shutil.get_terminal_size(fallback=(80, 24)).columns
                width = max(0, min(28, columns - len(display_label) - 20))
                filled = min(width, int(width * (1 - remaining / seconds)))
                bar = f" [{'#' * filled}{'-' * (width - filled)}]" if width else ""
                spinner = "=" if paused else ("|/-\\"[frame % 4] if remaining else "*")
                line = f"{spinner} {display_label}{bar} {clock_text(remaining)}"
                sys.stdout.write("\r\033[2K" + line[:max(1, columns - 1)])
                sys.stdout.flush()
            if remaining <= 0:
                break
            time.sleep(1 if paused else min(1, remaining))
            frame += 1
    finally:
        if animated:
            print(flush=True)


def alert(label, config):
    """Emite um único alerta; usa o sino do terminal como alternativa."""
    notification = message(config.get("idioma", "pt"), "notification", label=label)
    sound = config["alerta_sonoro"]
    if config["notificacao_desktop"]:
        try:
            if sys.platform == "darwin":
                script = (f'display notification "{notification}" '
                          'with title "Pomodoro"')
                if sound:
                    script += ' sound name "Glass"'
                subprocess.run(["osascript", "-e", script], check=True,
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=5)
                return
            if sys.platform.startswith("linux") and shutil.which("notify-send"):
                subprocess.run(["notify-send", "Pomodoro", notification], check=True,
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=5)
        except (OSError, subprocess.SubprocessError):
            pass
    if sound:
        print("\a", end="", flush=True)


def show_status(state, config):
    language = config.get("idioma", "pt")
    stage = state["proxima_etapa"]
    print(message(language, "status", label=message(language, stage),
                  minutes=config[stage + "_minutos"]))
    print(message(language, "completed_count", total=state["pomodoros_concluidos"],
                  cycle=state["pomodoros_no_ciclo"], interval=config["pomodoros_ate_pausa_longa"]))
    print(message(language, "start_hint"))


def main(argv=None):
    # Lê o idioma antes de montar a ajuda, inclusive com --config personalizado.
    bootstrap = argparse.ArgumentParser(add_help=False)
    bootstrap.add_argument("--config", type=Path, default=CONFIG_PATH)
    options, _ = bootstrap.parse_known_args(argv)
    language = "pt"
    config_error = None
    data = None
    try:
        data = read_json(options.config)
        language = config_language(data)
    except PomodoroError as exc:
        config_error = exc
    parser = argparse.ArgumentParser(
        description=message(language, "description"),
        epilog=message(language, "epilog"),
        add_help=False,
    )
    parser._positionals.title = message(language, "arguments")
    parser._optionals.title = message(language, "options")
    parser.add_argument("-h", "--help", action="help", help=message(language, "help"))
    parser.add_argument("comando", nargs="?", default="status",
                        choices=("iniciar", "start", "pular", "skip", "status", "reiniciar", "reset"),
                        help=message(language, "command_help"))
    parser.add_argument("--config", type=Path, default=CONFIG_PATH, help=message(language, "config_help"))
    parser.add_argument("--estado", "--state", type=Path, default=STATE_PATH,
                        metavar="STATE" if language == "en" else "ESTADO",
                        help=message(language, "state_help"))
    args = parser.parse_args(argv)
    command = {"start": "iniciar", "reset": "reiniciar", "skip": "pular"}.get(args.comando, args.comando)
    try:
        # Reiniciar também permite recuperar um arquivo de progresso inválido.
        if command == "reiniciar":
            with state_lock(args.estado):
                save_state(args.estado, initial_state())
            print(message(language, "reset"))
            return 0
        if config_error is not None:
            raise config_error
        config = load_config(args.config, data)
        if command == "status":
            show_status(load_state(args.estado), config)
            return 0
        with state_lock(args.estado):
            state = load_state(args.estado)
            history_path = args.estado.parent / "pomodoro-history.json"
            stage = state["proxima_etapa"]
            label = message(language, stage)
            skipped = command == "pular"
            if not skipped:
                load_history(history_path)
                print(message(language, "started", label=label), flush=True)
                skipped = countdown(config[stage + "_minutos"] * 60, label, language=language) == "skipped"
            if not skipped:
                record_completion(history_path, stage, config[stage + "_minutos"])
            save_state(args.estado, advance(state, config))
            print(message(language, "skipped" if skipped else "completed", label=label), flush=True)
            if not skipped:
                alert(label, config)
            show_status(load_state(args.estado), config)
        return 0
    except KeyboardInterrupt:
        print(message(language, "cancelled"))
        return 130
    except (PomodoroError, OSError) as exc:
        detail = exc.render(language) if isinstance(exc, PomodoroError) else str(exc)
        print(message(language, "error", error=detail), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
