#!/usr/bin/env python3
"""Pomodoro de terminal, sem dependências externas e sem início automático."""

import argparse
import contextlib
import json
import math
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time


ROOT = Path(__file__).resolve().parent
CONFIG_PATH = ROOT / "pomodoro.json"
STATE_PATH = ROOT / ".pomodoro-state.json"
LABELS = {"foco": "Foco", "pausa_curta": "Pausa curta", "pausa_longa": "Pausa longa"}
DEFAULT_CONFIG = {
    "foco_minutos": 25,
    "pausa_curta_minutos": 5,
    "pausa_longa_minutos": 15,
    "pomodoros_ate_pausa_longa": 4,
    "alerta_sonoro": True,
    "notificacao_desktop": True,
}


class PomodoroError(Exception):
    """Erro que pode ser apresentado diretamente no terminal."""


def read_json(path):
    try:
        with path.open(encoding="utf-8") as source:
            data = json.load(source)
    except (OSError, ValueError) as exc:
        raise PomodoroError(f"Não foi possível ler {path.name}: {exc}") from exc
    if not isinstance(data, dict):
        raise PomodoroError(f"{path.name} precisa conter um objeto JSON.")
    return data


def load_config(path):
    data = read_json(path)
    unknown = data.keys() - DEFAULT_CONFIG.keys()
    if unknown:
        raise PomodoroError(f"Propriedade desconhecida: {', '.join(sorted(unknown))}.")
    config = {**DEFAULT_CONFIG, **data}
    for key in ("foco_minutos", "pausa_curta_minutos", "pausa_longa_minutos"):
        value = config[key]
        if (type(value) not in (int, float) or value <= 0 or value > 1440
                or not math.isfinite(value)):
            raise PomodoroError(f"{key} deve ser um número maior que 0 e até 1440.")
    interval = config["pomodoros_ate_pausa_longa"]
    if type(interval) is not int or interval < 1:
        raise PomodoroError("pomodoros_ate_pausa_longa deve ser um inteiro maior que 0.")
    for key in ("alerta_sonoro", "notificacao_desktop"):
        if type(config[key]) is not bool:
            raise PomodoroError(f"{key} deve ser true ou false.")
    return config


def initial_state():
    return {"pomodoros_concluidos": 0, "pomodoros_no_ciclo": 0, "proxima_etapa": "foco"}


def load_state(path):
    if not path.exists():
        return initial_state()
    state = read_json(path)
    for key in ("pomodoros_concluidos", "pomodoros_no_ciclo"):
        if type(state.get(key)) is not int or state[key] < 0:
            raise PomodoroError("Progresso inválido. Use 'reiniciar' para zerar o ciclo.")
    if (not isinstance(state.get("proxima_etapa"), str)
            or state["proxima_etapa"] not in LABELS
            or state["pomodoros_no_ciclo"] > state["pomodoros_concluidos"]):
        raise PomodoroError("Progresso inválido. Use 'reiniciar' para zerar o ciclo.")
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
            raise PomodoroError("Já existe uma contagem em andamento nesta configuração.") from exc
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


def countdown(seconds, label):
    """Uma atualização por segundo, usando relógio monotônico para evitar deriva."""
    animated = sys.stdout.isatty()
    started = time.monotonic()
    deadline = started + seconds
    frame = 0
    if not animated:
        print(f"{label}: {clock_text(seconds)}", flush=True)
    try:
        while True:
            remaining = max(0, deadline - time.monotonic())
            if animated:
                columns = shutil.get_terminal_size(fallback=(80, 24)).columns
                width = max(0, min(28, columns - len(label) - 20))
                filled = min(width, int(width * (1 - remaining / seconds)))
                bar = f" [{'#' * filled}{'-' * (width - filled)}]" if width else ""
                spinner = "|/-\\"[frame % 4] if remaining else "*"
                line = f"{spinner} {label}{bar} {clock_text(remaining)}"
                sys.stdout.write("\r\033[2K" + line[:max(1, columns - 1)])
                sys.stdout.flush()
            if remaining <= 0:
                break
            time.sleep(min(1, remaining))
            frame += 1
    finally:
        if animated:
            print(flush=True)


def alert(label, config):
    """Emite um único alerta; usa o sino do terminal como alternativa."""
    sound = config["alerta_sonoro"]
    if config["notificacao_desktop"]:
        try:
            if sys.platform == "darwin":
                script = (f'display notification "{label} terminou. Próxima etapa aguarda seu comando." '
                          'with title "Pomodoro"')
                if sound:
                    script += ' sound name "Glass"'
                subprocess.run(["osascript", "-e", script], check=True,
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=5)
                return
            if sys.platform.startswith("linux") and shutil.which("notify-send"):
                subprocess.run(["notify-send", "Pomodoro", f"{label} terminou."], check=True,
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=5)
        except (OSError, subprocess.SubprocessError):
            pass
    if sound:
        print("\a", end="", flush=True)


def show_status(state, config):
    stage = state["proxima_etapa"]
    print(f"Próxima etapa: {LABELS[stage]} ({config[stage + '_minutos']:g} min)")
    print(f"Focos concluídos: {state['pomodoros_concluidos']} | "
          f"Neste ciclo: {state['pomodoros_no_ciclo']}/{config['pomodoros_ate_pausa_longa']}")
    print("Para iniciar: python3 pomodoro.py iniciar")


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Pomodoro leve. Cada etapa só começa com o comando iniciar.",
        epilog="Edite pomodoro.json para configurar os tempos (em minutos).",
    )
    parser.add_argument("comando", nargs="?", default="status",
                        choices=("iniciar", "status", "reiniciar"),
                        help="iniciar a próxima etapa, consultar status ou zerar o ciclo")
    parser.add_argument("--config", type=Path, default=CONFIG_PATH, help="caminho do JSON de configuração")
    parser.add_argument("--estado", type=Path, default=STATE_PATH, help="caminho do arquivo de progresso")
    args = parser.parse_args(argv)
    try:
        # Reiniciar também permite recuperar um arquivo de progresso inválido.
        if args.comando == "reiniciar":
            with state_lock(args.estado):
                save_state(args.estado, initial_state())
            print("Progresso zerado. A próxima etapa será foco; nenhuma contagem foi iniciada.")
            return 0
        config = load_config(args.config)
        if args.comando == "status":
            show_status(load_state(args.estado), config)
            return 0
        with state_lock(args.estado):
            state = load_state(args.estado)
            stage = state["proxima_etapa"]
            print(f"{LABELS[stage]} iniciado. Ctrl+C cancela sem avançar o ciclo.", flush=True)
            countdown(config[stage + "_minutos"] * 60, LABELS[stage])
            save_state(args.estado, advance(state, config))
            print(f"{LABELS[stage]} concluído!", flush=True)
            alert(LABELS[stage], config)
            show_status(load_state(args.estado), config)
        return 0
    except KeyboardInterrupt:
        print("\nContagem cancelada. A etapa continua pendente e recomeça do zero no próximo iniciar.")
        return 130
    except (PomodoroError, OSError) as exc:
        print(f"Erro: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
