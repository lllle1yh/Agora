"""提案与多评估者投票。"""

from __future__ import annotations

import re
from pathlib import Path

import bus

ROOT = Path(__file__).resolve().parent
WORKSPACE = ROOT
DECISIONS = WORKSPACE / "decisions"
ACCEPTED = DECISIONS / "accepted.md"
def evaluators() -> tuple[str, ...]:
    import json
    try:
        config = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
        ids = tuple(item["id"] for item in config.get("roles", []) if item.get("evaluator"))
    except (OSError, json.JSONDecodeError, KeyError):
        ids = ()
    return ids or ("role-10", "role-11", "role-12", "role-13", "role-14")
PROPOSAL_RE = re.compile(r"^PROPOSAL\s+([A-Za-z0-9_.-]+)\s*[:：]\s*(.*)$", re.S)
VOTE_RE = re.compile(r"^VOTE\s+([A-Za-z0-9_.-]+)\s+(yes|no|accept|reject|blocked)\s*[:：]?\s*(.*)$", re.S | re.I)


def proposals() -> dict[str, dict]:
    result = {}
    for item in bus.read_after(0, 100000):
        if item.get("kind") != "proposal":
            continue
        match = PROPOSAL_RE.match(str(item.get("text", "")).strip())
        if not match:
            continue
        pid, text = match.group(1), match.group(2).strip()
        result.setdefault(pid, {"id": pid, "role": item["role"], "name": item["name"], "text": text, "seq": item["seq"]})
    return result


def votes() -> dict[str, dict[str, tuple[bool, str, str]]]:
    result: dict[str, dict[str, tuple[bool, str, str]]] = {}
    for item in bus.read_after(0, 100000):
        if item.get("kind") != "vote" or item.get("role") not in evaluators():
            continue
        match = VOTE_RE.match(str(item.get("text", "")).strip())
        if not match:
            continue
        pid, raw, reason = match.group(1), match.group(2).lower(), match.group(3).strip()
        result.setdefault(pid, {})[item["role"]] = (raw in ("yes", "accept"), reason, item["seq"])
    return result


def pending_for(evaluator: str) -> list[str]:
    props = proposals()
    vote_map = votes()
    return [pid for pid in props if evaluator not in vote_map.get(pid, {})]


def accepted_text() -> str:
    if ACCEPTED.exists():
        return ACCEPTED.read_text(encoding="utf-8").strip()
    return ""


def tally() -> dict:
    props = proposals()
    vote_map = votes()
    rows = []
    for pid, prop in props.items():
        evaluator_votes = vote_map.get(pid, {})
        yes = sum(1 for value in evaluator_votes.values() if value[0])
        no = len(evaluator_votes) - yes
        rows.append({**prop, "yes": yes, "no": no, "votes": evaluator_votes})
    return {"proposals": rows, "accepted": bool(accepted_text())}


def maybe_accept() -> str:
    if accepted_text():
        return accepted_text()
    data = tally()
    for row in sorted(data["proposals"], key=lambda item: item["seq"]):
        if row["yes"] >= (len(evaluators()) // 2 + 1):
            DECISIONS.mkdir(parents=True, exist_ok=True)
            voters = ", ".join(
                f"{role}:{'yes' if value[0] else 'no'}" for role, value in sorted(row["votes"].items())
            )
            text = (
                f"# 已接受方向：{row['id']}\n\n"
                f"提出者：{row['name']}（{row['role']}）\n\n"
                f"投票：{voters}\n\n"
                f"## 提案\n\n{row['text']}\n"
            )
            ACCEPTED.write_text(text, encoding="utf-8")
            bus.append(
                "moderator",
                f"投票通过：{row['id']} 已被多数评估者接受。后续只围绕这个方向细化；"
                "新的中心提案进入 parking-lot，不再替换已接受方向。",
                "decision",
                "主持人",
            )
            return text
    return ""


if __name__ == "__main__":
    import json
    import sys

    if len(sys.argv) > 1 and sys.argv[1] == "accept":
        print(maybe_accept() or "NO_MAJORITY")
    else:
        print(json.dumps(tally(), ensure_ascii=False, indent=2, default=str))
