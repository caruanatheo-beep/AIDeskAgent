from __future__ import annotations

import time
from typing import Any

from .config import Settings, settings
from .security import redact_secrets


def new_session_state() -> dict[str, Any]:
    return {"turns": [], "call_times": [], "tool_counts": {}, "created_at": time.time()}


def normalize_session_state(state: object) -> dict[str, Any]:
    if isinstance(state, dict):
        fresh = new_session_state()
        fresh.update({key: state.get(key, default) for key, default in fresh.items()})
        return fresh
    if isinstance(state, list):  # V1 compatibility: the state only held model-call timestamps.
        fresh = new_session_state()
        fresh["call_times"] = list(state)
        return fresh
    return new_session_state()


def add_turn(state: object, question: str, answer: str, config: Settings = settings) -> dict[str, Any]:
    session = normalize_session_state(state)
    turns = list(session.get("turns", []))
    turns.append({
        "question": redact_secrets(question)[:600],
        "answer": redact_secrets(answer)[:2400],
    })
    session["turns"] = turns[-config.max_memory_turns :]
    return session


def memory_context(state: object, char_limit: int = 5000) -> str:
    session = normalize_session_state(state)
    blocks = []
    for turn in session.get("turns", []):
        blocks.append(f"User: {turn.get('question', '')}\nAssistant: {turn.get('answer', '')}")
    return "\n\n".join(blocks)[-char_limit:]


def memory_markdown(state: object) -> str:
    session = normalize_session_state(state)
    turns = session.get("turns", [])
    if not turns:
        return "*No conversation context is currently held.*"
    lines = ["### Session context"]
    for index, turn in enumerate(turns, 1):
        lines.append(f"**{index}. User:** {turn.get('question', '')}")
        lines.append(f"\n**Assistant:** {turn.get('answer', '')[:500]}{'…' if len(turn.get('answer', '')) > 500 else ''}\n")
    return "\n".join(lines)
