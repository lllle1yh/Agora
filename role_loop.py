"""单个讨论角色的自由讨论循环。

每个 tmux window 跑一个 role_loop.py <role-id>：
- 读取共享消息总线
- 有新消息时调用 claude -p
- 只接受一个 JSON action
- say 才写入总线；pass 不发言
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent
WORKSPACE = ROOT
CONFIG = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
ROLES = {item["id"]: item for item in CONFIG["roles"]}
DATA = ROOT / "data" / "roles"
ENV_FILE = Path.home() / ".kirke-discussion-env"

STANCE = {
    "role-01": "你是 Cairn / 证据派。你守住证据、claim、scope、可追溯；你设计证据平面，强调观察和推断分开、失败有范围。你不要求黑板做中心，但反对把证据层做成不可审计的隐藏状态。",
    "role-02": "你是 Sapper / 组织运行时派。你守住组织、角色、handoff、watchdog、模型分层；你设计组织平面，强调长时间无人值守、换人、恢复和资源调度。",
    "role-03": "你是 CTF 实战派。你只关心题型、攻击链、flag、速度。你用真实题目和攻击链质疑架构；反对纸上谈兵和过度协议化。",
    "role-04": "你是调度 / 策略派。你负责任务分配、并发、模型路由、预算、快慢路径；目标是更快拿到更多 flag。你会指出哪些架构在调度层不可行。",
    "role-05": "你是上下文 / 记忆派。你负责 handoff、压缩、工作区、长链路状态；目标是不断线、不丢信息、不重复探索。",
    "role-06": "你是验证 / 提交派。你负责 flag 核对、提交 guard、假阳性；目标是别把看起来成功当成成功。",
    "role-07": "你是新理念 / 突破派。你不满足于组合 Cairn + Sapper；你必须提出新的组织原则或打破一个共同假设，并说明为什么能多拿 flag、更快、或更能落地。你不能只给现有组件换名。",
    "role-08": "你是工程实现派。你判断有没有可执行路径、依赖、迁移、回退；不要求只能小改，但要求路径真实、成本清楚。没有路径才否决。",
    "role-09": "你是反方 / 简化派。你专门攻击过度设计；要求每个机制说明删掉会怎样；你优先砍掉不直接影响 flag、速度、稳定性的东西。",
    "role-10": "你是 Flag 效果评估者。你只投 yes/no/blocked：这个方向是否值得现在推进，能否多解出题、首个 flag 更快。你不要求理念新颖。",
    "role-11": "你是工程落地评估者。你只投 yes/no/blocked：有没有可执行路径、依赖是否明确、迁移和回退是否可控。你不要求只能小改。",
    "role-12": "你是速度/调度评估者。你只投 yes/no/blocked：wall-clock、并发、token、模型路由是否更优。",
    "role-13": "你是稳定/恢复评估者。你只投 yes/no/blocked：超时、503、实例丢失、handoff 后能不能继续。",
    "role-14": "你是风险/维护评估者。你只投 yes/no/blocked：复杂度、单点、运维成本、是否引入新坑。",
}

DEFAULT_THEME = "请围绕当前主题展开讨论，提出可执行、可验证的结论。"


def current_theme() -> str:
    parts = []
    for name in ("topic.md", "background.md"):
        path = WORKSPACE / name
        if path.exists():
            try:
                text = path.read_text(encoding="utf-8").strip()
                if text:
                    parts.append(text)
            except OSError:
                pass
    return "\n\n".join(parts) if parts else DEFAULT_THEME


def load_env() -> dict:
    env = dict(os.environ)
    if ENV_FILE.exists():
        for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            if line.startswith("export "):
                line = line[7:]
            key, value = line.split("=", 1)
            env[key.strip()] = value.strip().strip('"').strip("'")
    return env


def recent_messages(limit: int = 30) -> str:
    import bus

    items = bus.read_after(0, 100000)[-limit:]
    lines = []
    for item in items:
        text = str(item.get("text", "")).replace("\n", " ")
        lines.append(f"[{item.get('seq')}] {item.get('name')}: {text[:1200]}")
    return "\n".join(lines) if lines else "（暂无消息）"


def parse_action(text: str) -> dict | None:
    text = text.strip()
    start = text.rfind("{")
    while start >= 0:
        depth = 0
        in_string = False
        escape = False
        for index in range(start, len(text)):
            char = text[index]
            if in_string:
                if escape:
                    escape = False
                elif char == "\\":
                    escape = True
                elif char == '"':
                    in_string = False
                continue
            if char == '"':
                in_string = True
            elif char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
                if depth == 0:
                    try:
                        data = json.loads(text[start:index + 1])
                    except json.JSONDecodeError:
                        break
                    if isinstance(data, dict) and data.get("action"):
                        return data
                    break
        start = text.rfind("{", 0, start)
    return None


def call_claude(prompt: str, env: dict, timeout: int = 300) -> str:
    command = [
        "claude",
        "-p",
        prompt,
        "--output-format",
        "text",
        "--no-session-persistence",
        "--disallowedTools",
        "Bash,Edit,Write,Read,Glob,Grep,WebFetch,WebSearch,Agent,Task,NotebookEdit,Skill,Monitor",
    ]
    last = ""
    for attempt in range(3):
        try:
            proc = subprocess.run(
                command,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
                env=env,
            )
            if proc.returncode == 0 and proc.stdout.strip():
                return proc.stdout.strip()
            last = (proc.stderr or proc.stdout or "").strip()
        except subprocess.TimeoutExpired:
            last = "timeout"
        time.sleep(5 + attempt * 5)
    return f"__ERROR__ {last[-1000:]}"


def role_prompt(role_id: str, messages: str, accepted: str = "", summary: str = "", force_vote: list[str] | None = None) -> str:
    role = ROLES[role_id]
    stance = role.get("stance") or STANCE.get(role_id, "")
    evaluator = bool(role.get("evaluator")) or role_id in ("role-10", "role-11", "role-12", "role-13", "role-14")
    extra = ""
    if evaluator:
        extra = """
你是评估者。只有当讨论中出现可评估的提案、或主持人要求评估时才发言。
普通寒暄、重复观点、没有新论据时，输出 {"action":"pass"}。
当有人输出 PROPOSAL 时，你必须独立投票：{"action":"vote","proposal":"P1","vote":"yes|no|blocked","reason":"..."}。
投票标准只看实际效果：能否多拿 flag、首个 flag 是否更快、工程是否有可执行路径、速度/稳定性是否更好、风险是否可控。
不要求架构新颖，也不要求绝对非黑板。
如果认为已经达到可推进状态，输出 {"action":"score","text":"...","ready":true}；否则 false。
"""
    else:
        extra = """
只在你真的有新论点、能推进架构设计、或必须纠正别人时才发言。
没有新内容时输出 {"action":"pass"}。
不要复述别人的话，不要为了发言而发言。
"""
    evaluator_contract = ""
    if evaluator:
        evaluator_contract = (
            '{"action":"vote","proposal":"P1","vote":"yes|no","reason":"..."}\n'
            '{"action":"score","text":"评分与结论","ready":false}'
        )
    return f"""# 角色
{role.get("stance") or STANCE.get(role_id, "")}

# 主题
{current_theme()}

# 前情摘要
{summary or "（无）"}

# 当前讨论
{messages}

# 已接受方向
{accepted or "（暂无；如果你有值得推进的中心，用 propose 提交，等评估者投票。）"}

# 强制投票
{("本轮必须先对以下提案投票，只能输出 vote JSON，不要 score/pass：" + ", ".join(force_vote)) if force_vote else "（无）"}

# 输出契约
只输出一个 JSON 对象，不要 Markdown、不要解释、不要代码围栏。
可选：
{{"action":"say","text":"你的发言"}}
{{"action":"pass"}}
{{"action":"subagent","question":"需要子 Agent 深挖的具体问题"}}
{{"action":"propose","id":"P1-role-08","text":"你认为值得作为架构中心的方向，id 必须带你的角色 id"}}
{evaluator_contract}

{extra}
"""


def subagent(role_id: str, question: str, env: dict) -> str:
    base = WORKSPACE / "subagents" / role_id / uuid.uuid4().hex[:8]
    base.mkdir(parents=True, exist_ok=True)
    (base / "request.md").write_text(f"# 子问题\n\n{question}\n", encoding="utf-8")
    prompt = f"""# 角色
{ROLES.get(role_id, {}).get("stance") or STANCE.get(role_id, "")}

# 主题
{current_theme()}

# 子问题
{question}

# 要求
只回答这个子问题；不要扩散到其他议题；给出可验证的判断或反例。
"""
    output = call_claude(prompt, env, timeout=240)
    (base / "output.md").write_text(output, encoding="utf-8")
    return output


def main() -> int:
    if len(sys.argv) < 2 or sys.argv[1] not in ROLES:
        print(f"usage: role_loop.py <{'|'.join(ROLES)}>", file=sys.stderr)
        return 2
    role_id = sys.argv[1]
    role = ROLES[role_id]
    stance = role.get("stance") or STANCE.get(role_id, "")
    evaluator = bool(role.get("evaluator")) or role_id in ("role-10", "role-11", "role-12", "role-13", "role-14")
    import bus

    DATA.mkdir(parents=True, exist_ok=True)
    state_file = DATA / f"{role_id}.last"
    last_seq = int(state_file.read_text().strip()) if state_file.exists() else bus.last_seq()
    env = load_env()
    print(f"[{role['name']}] started last_seq={last_seq}", flush=True)
    while True:
        try:
            if bus.is_paused():
                time.sleep(1)
                continue
            messages = bus.read_after(last_seq, 200)
            if not messages:
                time.sleep(1.5)
                continue
            last_seq = max(int(item["seq"]) for item in messages)
            state_file.write_text(str(last_seq), encoding="utf-8")
            others = [item for item in messages if item.get("role") != role_id]
            if not others:
                continue
            import voting
            accepted = voting.accepted_text()
            summary_path = WORKSPACE / "discussions" / "context-summary.md"
            summary = summary_path.read_text(encoding="utf-8")[-6000:] if summary_path.exists() else ""
            force_vote = voting.pending_for(role_id) if evaluator else []
            prompt = role_prompt(role_id, recent_messages(30), accepted, summary, force_vote)
            started = time.time()
            output = call_claude(prompt, env)
            print(f"[{role['name']}] {time.time()-started:.1f}s -> {output[:180]}", flush=True)
            action = parse_action(output)
            if action is None:
                print(f"[{role['name']}] parse failed, pass", flush=True)
                continue
            kind = action.get("action")
            if kind == "pass":
                continue
            if kind == "say":
                text = str(action.get("text") or "").strip()
                if text:
                    bus.append(role_id, text, "message", role["name"])
                continue
            if kind == "subagent":
                question = str(action.get("question") or "").strip()
                if not question:
                    continue
                answer = subagent(role_id, question, env)
                bus.append(
                    role_id,
                    f"子 Agent 问题：{question}\n\n子结论：{answer[:4000]}",
                    "subagent",
                    role["name"],
                )
                continue
            if kind == "propose":
                raw_id = str(action.get("id") or "").strip()
                text = str(action.get("text") or "").strip()
                if not raw_id or not text:
                    continue
                proposal_id = raw_id if role_id in raw_id else f"{raw_id}-{role_id}"
                if accepted:
                    bus.append(role_id, f"PARKING {proposal_id}: {text}", "parking", role["name"])
                else:
                    bus.append(role_id, f"PROPOSAL {proposal_id}: {text}", "proposal", role["name"])
                continue
            if kind == "vote" and evaluator:
                proposal_id = str(action.get("proposal") or "").strip()
                vote = str(action.get("vote") or "").strip().lower()
                reason = str(action.get("reason") or "").strip()
                if not proposal_id or vote not in ("yes", "no", "accept", "reject"):
                    continue
                bus.append(
                    role_id,
                    f"VOTE {proposal_id} {vote}: {reason}",
                    "vote",
                    role["name"],
                )
                voting.maybe_accept()
                continue
            if kind == "score" and evaluator:
                text = str(action.get("text") or "").strip()
                ready = bool(action.get("ready"))
                evaluation = WORKSPACE / "evaluation"
                evaluation.mkdir(parents=True, exist_ok=True)
                (evaluation / "scorecard.md").write_text(text, encoding="utf-8")
                if ready:
                    final = WORKSPACE / "final"
                    final.mkdir(parents=True, exist_ok=True)
                    (final / "READY_FOR_USER.md").write_text(
                        "评估者认为当前架构讨论已比较完善，符合现阶段需要。\n",
                        encoding="utf-8",
                    )
                    (final / "architecture-proposal.md").write_text(text, encoding="utf-8")
                bus.append(role_id, text, "evaluation", role["name"])
                continue
        except Exception as exc:  # noqa: BLE001
            print(f"[{role['name']}] loop error {type(exc).__name__}: {exc}", flush=True)
            time.sleep(3)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
