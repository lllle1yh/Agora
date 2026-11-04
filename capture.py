"""把 WSL tmux 里每个角色的 pane 内容同步到 Platform/data/panes/。"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data" / "panes"
CONFIG = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
ANSI = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
OSC = re.compile(r"\x1b\][^\x07]*\x07")


def clean(text: str) -> str:
    text = OSC.sub("", text)
    text = ANSI.sub("", text)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    lines = [line.rstrip() for line in text.splitlines()]
    while lines and not lines[-1].strip():
        lines.pop()
    return "\n".join(lines)[-64000:]


def capture(target: str) -> str:
    try:
        proc = subprocess.run(
            ["wsl", "-e", "tmux", "capture-pane", "-p", "-J", "-t", target],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=8,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    if proc.returncode != 0:
        return ""
    return clean(proc.stdout)


def write_if_changed(role_id: str, text: str) -> bool:
    DATA.mkdir(parents=True, exist_ok=True)
    path = DATA / f"{role_id}.txt"
    if not text:
        if path.exists():
            path.unlink()
            return True
        return False
    if path.exists():
        try:
            if path.read_text(encoding="utf-8", errors="replace") == text:
                return False
        except OSError:
            pass
    temporary = path.with_suffix(".tmp")
    temporary.write_text(text, encoding="utf-8")
    temporary.replace(path)
    return True


def once() -> None:
    for role in CONFIG["roles"]:
        text = capture(role.get("tmux_target") or f"arch:{role['id']}")
        changed = write_if_changed(role["id"], text)
        print(f"{role['id']} {'updated' if changed else 'unchanged'} {len(text)}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--interval", type=float, default=1.0)
    args = parser.parse_args()
    if args.once:
        once()
        return 0
    DATA.mkdir(parents=True, exist_ok=True)
    print(f"[capture] {len(CONFIG['roles'])} roles, interval={args.interval}s")
    while True:
        for role in CONFIG["roles"]:
            text = capture(role.get("tmux_target") or f"arch:{role['id']}")
            if write_if_changed(role["id"], text):
                print(f"[capture] {role['name']} updated {len(text)}", flush=True)
        time.sleep(max(0.5, args.interval))


if __name__ == "__main__":
    raise SystemExit(main())
