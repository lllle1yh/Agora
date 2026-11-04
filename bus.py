"""跨 Windows / WSL 的追加式消息总线。"""

from __future__ import annotations

import json
import os
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
WORKSPACE = ROOT
MESSAGES = WORKSPACE / "discussions" / "messages.jsonl"
LOCK = WORKSPACE / "discussions" / ".messages.lock"
PAUSE = WORKSPACE / "discussions" / ".paused"


def is_paused() -> bool:
    return PAUSE.exists()


def set_paused(value: bool) -> bool:
    ensure()
    if value:
        PAUSE.write_text("paused\n", encoding="utf-8")
    elif PAUSE.exists():
        PAUSE.unlink()
    return is_paused()


def _lock(handle) -> None:
    if os.name == "nt":
        import msvcrt

        msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
    else:
        import fcntl

        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)


def _unlock(handle) -> None:
    if os.name == "nt":
        import msvcrt

        try:
            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        except OSError:
            pass
    else:
        import fcntl

        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def ensure() -> None:
    MESSAGES.parent.mkdir(parents=True, exist_ok=True)
    LOCK.parent.mkdir(parents=True, exist_ok=True)
    MESSAGES.touch(exist_ok=True)
    LOCK.touch(exist_ok=True)


def read_after(after: int = 0, limit: int = 500) -> list[dict]:
    ensure()
    result = []
    with MESSAGES.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                continue
            if int(item.get("seq", 0)) > after:
                result.append(item)
    return result[-limit:]


def last_seq() -> int:
    items = read_after(0, 100000)
    return int(items[-1]["seq"]) if items else 0


def append(role: str, text: str, kind: str = "message", name: str = "") -> dict:
    text = (text or "").strip()
    if not text:
        raise ValueError("empty message")
    ensure()
    with LOCK.open("a+b") as lock:
        _lock(lock)
        try:
            seq = last_seq() + 1
            item = {
                "seq": seq,
                "ts": datetime.now().astimezone().isoformat(timespec="seconds"),
                "role": role,
                "name": name or role,
                "kind": kind,
                "text": text,
            }
            with MESSAGES.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(item, ensure_ascii=False) + "\n")
            return item
        finally:
            _unlock(lock)


if __name__ == "__main__":
    import sys

    print(json.dumps(append(sys.argv[1] if len(sys.argv) > 1 else "moderator", " ".join(sys.argv[2:])), ensure_ascii=False))
