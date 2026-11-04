from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
SETTINGS = DATA / "settings.json"


class ProviderError(RuntimeError):
    def __init__(self, message: str, *, status: int = 0) -> None:
        super().__init__(message)
        self.status = status


@dataclass(slots=True)
class ProviderConfig:
    provider: str = "openai"
    base_url: str = ""
    api_key: str = ""
    model: str = ""

    def public(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "base_url": self.base_url,
            "model": self.model,
            "has_key": bool(self.api_key),
        }


def load_settings() -> dict[str, Any]:
    DATA.mkdir(parents=True, exist_ok=True)
    if not SETTINGS.exists():
        return {"provider": "openai", "base_url": "", "api_key": "", "model": ""}
    try:
        raw = json.loads(SETTINGS.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"provider": "openai", "base_url": "", "api_key": "", "model": ""}
    return {
        "provider": str(raw.get("provider") or "openai"),
        "base_url": str(raw.get("base_url") or ""),
        "api_key": str(raw.get("api_key") or ""),
        "model": str(raw.get("model") or ""),
    }


def save_settings(payload: dict[str, Any]) -> dict[str, Any]:
    DATA.mkdir(parents=True, exist_ok=True)
    current = load_settings()
    provider = str(payload.get("provider") or current["provider"]).lower()
    if provider not in ("openai", "anthropic"):
        raise ValueError("provider 只能是 openai 或 anthropic")
    data = {
        "provider": provider,
        "base_url": str(payload.get("base_url") or current["base_url"]).strip().rstrip("/"),
        "model": str(payload.get("model") or current["model"]).strip(),
        "api_key": str(payload.get("api_key") or current["api_key"]).strip(),
    }
    temporary = SETTINGS.with_suffix(".tmp")
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temporary, SETTINGS)
    try:
        os.chmod(SETTINGS, 0o600)
    except OSError:
        pass
    return public_settings(data)


def public_settings(data: dict[str, Any] | None = None) -> dict[str, Any]:
    data = data or load_settings()
    return {
        "provider": data.get("provider", "openai"),
        "base_url": data.get("base_url", ""),
        "model": data.get("model", ""),
        "has_key": bool(data.get("api_key")),
    }


def _request(url: str, *, headers: dict[str, str], body: dict[str, Any] | None, timeout: float) -> dict[str, Any]:
    data = json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else None
    request = urllib.request.Request(url, data=data, method="POST" if body is not None else "GET", headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read()
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        detail = raw.decode("utf-8", "replace")[:2000]
        raise ProviderError(f"HTTP {exc.code}: {detail}", status=exc.code) from None
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise ProviderError(f"连接失败：{exc}") from None
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        raise ProviderError(f"返回不是 JSON：{raw[:500]!r}") from None
    if not isinstance(value, dict):
        raise ProviderError("返回格式不是 JSON 对象")
    return value


class Provider:
    def __init__(self, config: ProviderConfig) -> None:
        self.config = config

    def _openai_url(self) -> str:
        base = (self.config.base_url or "").strip().rstrip("/")
        if base.endswith("/chat/completions"):
            return base
        if base.endswith("/v1"):
            return base + "/chat/completions"
        return base + "/v1/chat/completions"

    def _anthropic_url(self) -> str:
        base = (self.config.base_url or "").strip().rstrip("/")
        if base.endswith("/v1/messages"):
            return base
        if base.endswith("/v1"):
            return base + "/messages"
        return base + "/v1/messages"

    def chat(
        self,
        messages: list[dict[str, str]],
        *,
        system: str | None = None,
        model: str | None = None,
        temperature: float = 0.7,
        max_tokens: int = 2000,
    ) -> str:
        model = (model or self.config.model or "").strip()
        if not model:
            raise ProviderError("模型不能为空")
        if not self.config.api_key:
            raise ProviderError("API Key 不能为空")
        if (self.config.provider or "openai").lower() == "anthropic":
            body: dict[str, Any] = {
                "model": model,
                "max_tokens": max_tokens,
                "temperature": temperature,
                "messages": messages,
            }
            if system:
                body["system"] = system
            value = _request(
                self._anthropic_url(),
                headers={
                    "x-api-key": self.config.api_key,
                    "anthropic-version": "2023-06-01",
                    "Content-Type": "application/json",
                },
                body=body,
                timeout=180,
            )
            blocks = value.get("content") or []
            return "".join(str(block.get("text") or "") for block in blocks if isinstance(block, dict))
        body = {
            "model": model,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "messages": ([{"role": "system", "content": system}] if system else []) + messages,
        }
        value = _request(
            self._openai_url(),
            headers={
                "Authorization": f"Bearer {self.config.api_key}",
                "Content-Type": "application/json",
            },
            body=body,
            timeout=180,
        )
        choices = value.get("choices") or []
        if not choices:
            raise ProviderError("返回没有 choices")
        return str((choices[0].get("message") or {}).get("content") or "")

    def test(self) -> dict[str, Any]:
        started = time.time()
        provider = (self.config.provider or "openai").lower()
        models: list[str] = []
        if provider == "openai":
            base = (self.config.base_url or "").strip().rstrip("/")
            url = base + ("/models" if base.endswith("/v1") else "/v1/models")
            try:
                value = _request(
                    url,
                    headers={"Authorization": f"Bearer {self.config.api_key}", "Accept": "application/json"},
                    body=None,
                    timeout=30,
                )
                models = [str(item.get("id")) for item in value.get("data") or [] if isinstance(item, dict)]
            except ProviderError:
                pass
        reply = self.chat([{"role": "user", "content": "只回复 OK"}], max_tokens=8, temperature=0)
        return {
            "ok": True,
            "provider": provider,
            "model": self.config.model,
            "models": models,
            "latency_ms": int((time.time() - started) * 1000),
            "reply": reply[:100],
        }


def _parse_json_array(text: str) -> list[dict[str, Any]]:
    text = (text or "").strip()
    start = text.find("[")
    end = text.rfind("]")
    if start >= 0 and end > start:
        try:
            value = json.loads(text[start:end + 1])
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)]
        except json.JSONDecodeError:
            pass
    # 兼容单个对象
    start = text.rfind("{")
    while start >= 0:
        end = text.find("}", start)
        if end < 0:
            break
        try:
            value = json.loads(text[start:end + 1])
            if isinstance(value, dict):
                return [value]
        except json.JSONDecodeError:
            pass
        start = text.rfind("{", 0, start)
    return []


def generate_roles(topic: str, count: int = 6, evaluators: int = 0, language: str = "中文", background: str = "") -> list[dict[str, Any]]:
    settings = load_settings()
    client = Provider(
        ProviderConfig(
            provider=settings["provider"],
            base_url=settings["base_url"],
            api_key=settings["api_key"],
            model=settings["model"],
        )
    )
    count = max(1, min(12, int(count)))
    evaluators = max(0, min(6, int(evaluators)))
    total = count + evaluators
    prompt = f"""你是多 Agent 讨论的主持人。请为下面的主题设计 {count} 个讨论角色和 {evaluators} 个独立评估者，总共 {total} 个。

主题：
{topic}

背景资料：
{background.strip()[:12000] if background.strip() else "（无）"}

要求：
- 讨论角色之间必须有真实分歧，不要重复。
- 讨论角色至少包含一个务实/工程派、一个反方/挑错派。
- 评估者不参与日常讨论，只负责判断方案是否值得推进、投票 yes/no/blocked。
- 每个角色给出：name、stance（立场，一句话）、goal（在讨论中要达成什么）、color（十六进制颜色）、evaluator（布尔值 true 或 false，不要写解释）。
- 讨论角色的 evaluator 必须为 false；评估者的 evaluator 必须为 true。
- 使用{language}。
- 只输出 JSON 数组，不要 Markdown、不要解释。

格式：
[{{"name":"...","stance":"...","goal":"...","color":"#6ea8ff","evaluator":false}}]
"""
    try:
        output = client.chat(
            [{"role": "user", "content": prompt}],
            system="你只输出合法 JSON 数组，不要输出其他内容。",
            max_tokens=max(5000, min(12000, total * 600)),
            temperature=0.7,
        )
        roles = _parse_json_array(output)
    except ProviderError as exc:
        raise ProviderError(f"角色生成失败：{exc}") from exc
    if not roles:
        raise ProviderError("模型没有返回合法的 JSON 角色数组，请检查模型/网关或稍后重试")
    def is_evaluator(item: dict[str, Any]) -> bool:
        value = item.get("evaluator")
        return value is True or str(value or "").strip().lower() in ("true", "yes", "1")
    discussants = [item for item in roles if not is_evaluator(item)][:count]
    evaluator_roles = [item for item in roles if is_evaluator(item)][:evaluators]
    if len(discussants) < count or len(evaluator_roles) < evaluators:
        raise ProviderError(
            f"模型只返回了 {len(discussants)} 个讨论角色 / {len(evaluator_roles)} 个评估者，"
            f"需要 {count} / {evaluators}；请重试或调整模型"
        )
    cleaned = []
    for index, item in enumerate(discussants + evaluator_roles):
        cleaned.append(
            {
                "id": f"role-{index + 1:02d}",
                "name": str(item.get("name") or f"角色 {index + 1}")[:40],
                "stance": str(item.get("stance") or "")[:500],
                "goal": str(item.get("goal") or "")[:300],
                "color": str(item.get("color") or "#6ea8ff"),
                "evaluator": index >= count,
                "model": "",
            }
        )
    return cleaned
