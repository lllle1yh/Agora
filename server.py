from __future__ import annotations

import json
import shutil
import subprocess
import sys
import threading
import time
import urllib.parse
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import bus
import provider

ROOT = Path(__file__).resolve().parent
WORKSPACE = ROOT
DATA = ROOT / "data"
PANES = DATA / "panes"
DISCUSSIONS = WORKSPACE / "discussions"
MESSAGES = DISCUSSIONS / "messages.jsonl"
SIDECHAT = DISCUSSIONS / "sidechat.jsonl"
TOPIC = WORKSPACE / "topic.md"
BACKGROUND = WORKSPACE / "background.md"
ARCHIVE = WORKSPACE / "archive"
CONFIG_PATH = ROOT / "config.json"
LOCK = threading.RLock()
CONFIG: dict = {}
ROLES: list[dict] = []
ROLE_MAP: dict[str, dict] = {}
FILE_ROOTS = ("discussions", "proposals", "evaluation", "final", "subagents", "decisions")


def load_config() -> None:
    global CONFIG, ROLES, ROLE_MAP
    CONFIG = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    ROLES = CONFIG.get("roles", [])
    ROLE_MAP = {item["id"]: item for item in ROLES}


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def ensure_dirs() -> None:
    for path in (DISCUSSIONS, PANES, WORKSPACE / "proposals", WORKSPACE / "evaluation", WORKSPACE / "final", ARCHIVE):
        path.mkdir(parents=True, exist_ok=True)
    bus.ensure()
    SIDECHAT.touch(exist_ok=True)
    if not TOPIC.exists():
        TOPIC.write_text("请填写讨论主题。\n", encoding="utf-8")


def read_messages(after: int = 0, limit: int = 500) -> list[dict]:
    return bus.read_after(after, limit)


def append_message(text: str, role: str = "moderator", kind: str = "message") -> dict:
    text = text.strip()
    if not text:
        raise ValueError("消息不能为空")
    if len(text) > 20000:
        raise ValueError("消息超过 20000 字符")
    name = ROLE_MAP.get(role, {}).get("name", "主持人")
    return bus.append(role, text, kind, name)


def pane_path(role_id: str) -> Path:
    return PANES / f"{role_id}.txt"


def role_state(item: dict) -> dict:
    role_id = item["id"]
    path = pane_path(role_id)
    pane = ""
    updated = None
    age = None
    if path.exists():
        try:
            stat = path.stat()
            pane = path.read_text(encoding="utf-8", errors="replace")[-12000:]
            updated = datetime.fromtimestamp(stat.st_mtime).astimezone().isoformat(timespec="seconds")
            age = max(0, int(time.time() - stat.st_mtime))
        except OSError:
            pass
    if not path.exists():
        status = "未启动"
    elif age is not None and age <= 15:
        status = "输出中"
    elif age is not None and age <= 180:
        status = "最近活动"
    else:
        status = "空闲"
    return {**item, "pane": pane, "updated_at": updated, "status": status}


def list_files() -> list[dict]:
    result = []
    for root_name in FILE_ROOTS:
        root = WORKSPACE / root_name
        if not root.exists():
            continue
        for path in sorted(root.rglob("*")):
            if not path.is_file() or path.suffix.lower() not in (".md", ".txt", ".json", ".jsonl"):
                continue
            try:
                stat = path.stat()
            except OSError:
                continue
            result.append(
                {
                    "path": path.relative_to(WORKSPACE).as_posix(),
                    "size": stat.st_size,
                    "mtime": datetime.fromtimestamp(stat.st_mtime).astimezone().isoformat(timespec="seconds"),
                }
            )
            if len(result) >= 400:
                return result
    return result


def safe_file(relative: str) -> Path:
    target = (WORKSPACE / relative).resolve()
    if WORKSPACE.resolve() not in target.parents or not target.is_file():
        raise ValueError("文件不存在或越界")
    return target


def read_sidechat(limit: int = 100) -> list[dict]:
    if not SIDECHAT.exists():
        return []
    items = []
    for line in SIDECHAT.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            items.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return items[-limit:]


def append_sidechat(role: str, text: str) -> dict:
    text = text.strip()
    if not text:
        raise ValueError("消息不能为空")
    with LOCK:
        items = read_sidechat(100000)
        seq = (items[-1]["seq"] + 1) if items else 1
        item = {"seq": seq, "ts": now_iso(), "role": role, "text": text}
        with SIDECHAT.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(item, ensure_ascii=False) + "\n")
        return item


def topic_text() -> str:
    try:
        return TOPIC.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def background_text() -> str:
    try:
        return BACKGROUND.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def provider_from_payload(payload: dict) -> provider.Provider:
    saved = provider.load_settings()
    return provider.Provider(
        provider.ProviderConfig(
            provider=str(payload.get("provider") or saved.get("provider") or "openai"),
            base_url=str(payload.get("base_url") or saved.get("base_url") or ""),
            api_key=str(payload.get("api_key") or saved.get("api_key") or ""),
            model=str(payload.get("model") or saved.get("model") or ""),
        )
    )


def save_roles(topic: str, roles: list[dict], background: str = "") -> dict:
    topic = (topic or "").strip()
    if not topic:
        raise ValueError("主题不能为空")
    if not roles:
        raise ValueError("至少需要一个角色")
    if len(roles) > 16:
        raise ValueError("角色最多 16 个")
    clean = []
    for index, item in enumerate(roles):
        name = str(item.get("name") or f"角色 {index + 1}").strip()
        if not name:
            raise ValueError(f"第 {index + 1} 个角色没有名称")
        clean.append(
            {
                "id": f"role-{index + 1:02d}",
                "name": name[:40],
                "short": str(item.get("short") or name[:12]),
                "stance": str(item.get("stance") or "")[:1000],
                "goal": str(item.get("goal") or "")[:500],
                "model": str(item.get("model") or "").strip(),
                "color": str(item.get("color") or "#6ea8ff"),
                "evaluator": bool(item.get("evaluator")),
            }
        )
    CONFIG["roles"] = clean
    temporary = CONFIG_PATH.with_suffix(".tmp")
    temporary.write_text(json.dumps(CONFIG, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(CONFIG_PATH)
    TOPIC.write_text(topic + "\n", encoding="utf-8")
    lines = ["# 本轮背景", "", f"主题：{topic}", "", "角色："]
    for role in clean:
        lines.append(f"- {role['name']}：{role['stance']}")
    (DISCUSSIONS / "context-summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    load_config()
    return {"topic": topic, "roles": clean, "background": background_text()}


def archive_current() -> str:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    target = ARCHIVE / f"discussion-{stamp}"
    target.mkdir(parents=True, exist_ok=False)
    for name in ("discussions", "decisions", "proposals", "evaluation", "final", "subagents"):
        source = WORKSPACE / name
        if source.exists():
            destination = target / name
            if source.is_dir():
                shutil.copytree(source, destination)
            else:
                shutil.copy2(source, destination)
    if TOPIC.exists():
        shutil.copy2(TOPIC, target / "topic.md")
    if BACKGROUND.exists():
        shutil.copy2(BACKGROUND, target / "background.md")
    shutil.copy2(CONFIG_PATH, target / "config.json")
    meta = {
        "id": target.name,
        "created_at": now_iso(),
        "topic": topic_text(),
        "roles": ROLES,
        "messages": len(read_messages(0, 100000)),
        "background": bool(background_text()),
    }
    (target / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    (target / "README.md").write_text(
        f"# 讨论归档\n\n- 时间：{now_iso()}\n- 主题：{topic_text()}\n",
        encoding="utf-8",
    )
    return target.name


def stop_discussion() -> None:
    subprocess.run(
        [sys.executable, str(ROOT / "start_discussion.py"), "stop"],
        cwd=str(ROOT),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        timeout=30,
    )
    bus.set_paused(True)


def reset_discussion(clear_topic: bool = False) -> None:
    bus.set_paused(False)
    MESSAGES.write_text("", encoding="utf-8")
    SIDECHAT.write_text("", encoding="utf-8")
    for name in ("decisions", "proposals", "evaluation", "final", "subagents"):
        path = WORKSPACE / name
        if path.exists():
            shutil.rmtree(path)
        path.mkdir(parents=True, exist_ok=True)
    (DISCUSSIONS / "context-summary.md").write_text("", encoding="utf-8")
    # 清掉角色游标与 pane，确保新讨论从 seq 1 开始，而不是跳过旧消息。
    for path in (DATA / "roles").glob("*.last"):
        try:
            path.unlink()
        except OSError:
            pass
    for path in PANES.glob("*.txt"):
        try:
            path.unlink()
        except OSError:
            pass
    if clear_topic:
        TOPIC.write_text("", encoding="utf-8")
        BACKGROUND.write_text("", encoding="utf-8")


def archive_and_reset() -> dict:
    stop_discussion()
    archive_id = archive_current()
    reset_discussion(clear_topic=True)
    return {"id": archive_id, "path": f"archive/{archive_id}"}


def list_archives() -> list[dict]:
    result = []
    for path in sorted(ARCHIVE.glob("*/meta.json"), reverse=True):
        try:
            meta = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        result.append(meta)
    return result


def read_archive(archive_id: str) -> dict:
    if not archive_id or "/" in archive_id or "\\" in archive_id or ".." in archive_id:
        raise ValueError("归档 id 不合法")
    root = ARCHIVE / archive_id
    if not root.is_dir():
        raise KeyError(archive_id)
    meta_path = root / "meta.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.exists() else {"id": archive_id}
    messages = []
    messages_path = root / "discussions" / "messages.jsonl"
    if messages_path.exists():
        for line in messages_path.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                messages.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    topic = ""
    topic_path = root / "topic.md"
    if topic_path.exists():
        topic = topic_path.read_text(encoding="utf-8", errors="replace").strip()
    background = ""
    background_path = root / "background.md"
    if background_path.exists():
        background = background_path.read_text(encoding="utf-8", errors="replace").strip()
    return {"meta": meta, "topic": topic, "background": background, "messages": messages}


def export_markdown() -> str:
    lines = [f"# {topic_text() or '讨论记录'}", "", f"- 导出时间：{now_iso()}", "", "## 角色", ""]
    for role in ROLES:
        lines.append(f"- **{role['name']}**：{role.get('stance', '')}")
    if background_text():
        lines += ["", "## 背景资料", "", background_text(), ""]
    lines += ["", "## 讨论", ""]
    for item in read_messages(0, 100000):
        lines += [f"### {item.get('name')} · {item.get('ts')}", "", str(item.get("text") or ""), ""]
    side = read_sidechat(1000)
    if side:
        lines += ["## 旁路对话", ""]
        for item in side:
            role = "用户" if item.get("role") == "user" else "旁路顾问"
            lines += [f"**{role} · {item.get('ts')}**", "", str(item.get("text") or ""), ""]
    return "\n".join(lines)


class Handler(BaseHTTPRequestHandler):
    server_version = "Agora/1.0"

    def log_message(self, fmt: str, *args) -> None:
        print(f"[ui] {self.address_string()} {fmt % args}")

    def do_GET(self) -> None:
        parsed = urllib.parse.urlsplit(self.path)
        try:
            if parsed.path == "/api/state":
                query = urllib.parse.parse_qs(parsed.query)
                after = int(query.get("after", ["0"])[0])
                return self._json(
                    200,
                    {
                        "roles": [role_state(item) for item in ROLES],
                        "messages": read_messages(after),
                        "files": list_files(),
                        "evaluator_ready": (WORKSPACE / "final" / "READY_FOR_USER.md").exists(),
                        "final_ready": (WORKSPACE / "final" / "architecture-proposal.md").exists(),
                        "messages_count": bus.last_seq(),
                        "paused": bus.is_paused(),
                        "topic": topic_text(),
                    },
                )
            if parsed.path == "/api/role":
                query = urllib.parse.parse_qs(parsed.query)
                role_id = query.get("id", [""])[0]
                if role_id not in ROLE_MAP:
                    raise ValueError("角色不存在")
                path = pane_path(role_id)
                text = path.read_text(encoding="utf-8", errors="replace") if path.exists() else ""
                return self._json(200, {"role": ROLE_MAP[role_id], "pane": text[-64000:]})
            if parsed.path == "/api/file":
                query = urllib.parse.parse_qs(parsed.query)
                relative = query.get("path", [""])[0]
                target = safe_file(relative)
                return self._json(200, {"path": relative, "text": target.read_text(encoding="utf-8", errors="replace")[-200000:]})
            if parsed.path == "/api/settings":
                return self._json(200, {**provider.public_settings(), "topic": topic_text(), "background": background_text(), "roles": ROLES})
            if parsed.path == "/api/archives":
                return self._json(200, {"archives": list_archives()})
            if parsed.path.startswith("/api/archive/"):
                archive_id = parsed.path[len("/api/archive/"):]
                return self._json(200, read_archive(archive_id))
            if parsed.path == "/api/side-chat":
                return self._json(200, {"messages": read_sidechat(200)})
            if parsed.path == "/api/export":
                text = export_markdown()
                return self._json(200, {"filename": f"agora-{datetime.now().strftime('%Y%m%d-%H%M%S')}.md", "text": text})
            if parsed.path == "/api/health":
                return self._json(200, {"ok": True, "roles": len(ROLES)})
            return self._static(parsed.path)
        except KeyError:
            self._json(404, {"error": "不存在"})
        except ValueError as exc:
            self._json(400, {"error": str(exc)})
        except Exception as exc:  # noqa: BLE001
            self._json(500, {"error": f"{type(exc).__name__}: {exc}"})

    def do_POST(self) -> None:
        parsed = urllib.parse.urlsplit(self.path)
        path = parsed.path
        try:
            payload = self._read_json()
            if path == "/api/pause":
                return self._json(200, {"paused": bus.set_paused(bool(payload.get("paused")))})
            if path == "/api/message":
                return self._json(200, append_message(str(payload.get("text") or ""), str(payload.get("role") or "moderator"), str(payload.get("kind") or "message")))
            if path == "/api/settings":
                return self._json(200, provider.save_settings(payload))
            if path == "/api/background":
                text = str(payload.get("text") or "")
                if len(text.encode("utf-8")) > 1024 * 1024:
                    raise ValueError("背景资料超过 1 MiB")
                BACKGROUND.write_text(text, encoding="utf-8")
                return self._json(200, {"ok": True, "length": len(text)})
            if path == "/api/model/test":
                return self._json(200, provider_from_payload(payload).test())
            if path == "/api/roles/generate":
                topic = str(payload.get("topic") or topic_text()).strip()
                count = int(payload.get("count") or 6)
                evaluators = int(payload.get("evaluators") or 0)
                background = str(payload.get("background") or background_text())
                return self._json(200, {"roles": provider.generate_roles(topic, count, evaluators, "中文", background)})
            if path == "/api/config":
                return self._json(200, save_roles(str(payload.get("topic") or ""), payload.get("roles") or [], str(payload.get("background") or "")))
            if path == "/api/discussion/start":
                if payload.get("new"):
                    archive_and_reset()
                if payload.get("topic") or payload.get("roles"):
                    save_roles(str(payload.get("topic") or ""), payload.get("roles") or [], str(payload.get("background") or ""))
                subprocess.Popen(
                    [sys.executable, str(ROOT / "start_discussion.py"), "start"],
                    cwd=str(ROOT),
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    start_new_session=True,
                )
                return self._json(202, {"ok": True, "message": "正在启动讨论"})
            if path == "/api/discussion/stop":
                subprocess.run(
                    [sys.executable, str(ROOT / "start_discussion.py"), "stop"],
                    cwd=str(ROOT),
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=30,
                )
                bus.set_paused(True)
                return self._json(200, {"ok": True})
            if path == "/api/archive":
                return self._json(200, archive_and_reset())
            if path == "/api/reset":
                reset_discussion(clear_topic=True)
                return self._json(200, {"ok": True})
            if path == "/api/side-chat":
                message = str(payload.get("message") or "").strip()
                if not message:
                    raise ValueError("消息不能为空")
                append_sidechat("user", message)
                history = read_sidechat(30)
                saved = provider.load_settings()
                client = provider.Provider(
                    provider.ProviderConfig(
                        provider=str(payload.get("provider") or saved["provider"]),
                        base_url=str(payload.get("base_url") or saved["base_url"]),
                        api_key=str(payload.get("api_key") or saved["api_key"]),
                        model=str(payload.get("model") or saved["model"]),
                    )
                )
                messages = [
                    {"role": "user" if item.get("role") == "user" else "assistant", "content": str(item.get("text") or "")}
                    for item in history
                ]
                reply = client.chat(
                    messages,
                    system="你是旁路顾问。只回答用户的问题，不进入主讨论；需要时给出简洁、可执行的判断。",
                    max_tokens=1600,
                    temperature=0.5,
                )
                item = append_sidechat("assistant", reply)
                return self._json(200, {"reply": item, "history": read_sidechat(200)})
            self._json(404, {"error": "接口不存在"})
        except (ValueError, json.JSONDecodeError) as exc:
            self._json(400, {"error": str(exc)})
        except provider.ProviderError as exc:
            self._json(502, {"error": str(exc)})
        except KeyError:
            self._json(404, {"error": "不存在"})
        except Exception as exc:  # noqa: BLE001
            self._json(500, {"error": f"{type(exc).__name__}: {exc}"})

    def _read_json(self) -> dict:
        length = int(self.headers.get("Content-Length", "0"))
        if length > 2 * 1024 * 1024:
            raise ValueError("请求体超过 2 MiB")
        raw = self.rfile.read(length) if length > 0 else b"{}"
        payload = json.loads(raw.decode("utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("请求体必须是对象")
        return payload

    def _json(self, status: int, payload: dict) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _static(self, path: str) -> None:
        if path in ("", "/"):
            relative = "index.html"
        elif path.startswith("/static/"):
            relative = path[len("/static/"):]
        else:
            relative = path.lstrip("/")
        target = (ROOT / "static" / relative).resolve()
        if (ROOT / "static").resolve() not in target.parents or not target.is_file():
            self.send_error(404)
            return
        mime = {".html": "text/html; charset=utf-8", ".js": "application/javascript; charset=utf-8", ".css": "text/css; charset=utf-8"}.get(target.suffix, "application/octet-stream")
        data = target.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(data)


def main() -> int:
    ensure_dirs()
    load_config()
    host = CONFIG.get("host", "127.0.0.1")
    port = int(CONFIG.get("port", 8780))
    server = ThreadingHTTPServer((host, port), Handler)
    server.daemon_threads = True
    print(f"[ui] http://{host}:{port}")
    print(f"[ui] workspace {WORKSPACE}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
