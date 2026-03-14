from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from typing import Any


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def count_tokens_rough(text: str) -> int:
    if not text:
        return 0
    ascii_chars = sum(1 for ch in text if ord(ch) < 128)
    non_ascii_chars = len(text) - ascii_chars
    return max(1, (ascii_chars + 3) // 4 + non_ascii_chars)


def tokenize_for_match(text: str) -> set[str]:
    if not text:
        return set()
    return {w for w in re.findall(r"[A-Za-z0-9_\u4e00-\u9fff]+", text.lower()) if len(w) >= 2}


def summary_match_score(*, text: str, query_terms: set[str], kind: str) -> float:
    terms = tokenize_for_match(text)
    overlap = len(query_terms & terms)
    base = float(overlap)
    if kind == "global":
        base += 0.6
    elif kind == "stage":
        base += 0.3
    return base


def fact_match_score(*, key: str, value: str, priority: int, query_terms: set[str]) -> float:
    text_terms = tokenize_for_match(f"{key} {value}")
    overlap = len(query_terms & text_terms)
    return overlap * 2.0 + min(priority, 100) / 100.0


def safe_json_parse(raw: str) -> Any:
    text = (raw or "").strip()
    if not text:
        return None
    try:
        return json.loads(text)
    except Exception:
        m = re.search(r"\{[\s\S]*\}", text)
        if not m:
            return None
        try:
            return json.loads(m.group(0))
        except Exception:
            return None


def as_str_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    result: list[str] = []
    for item in value:
        s = str(item).strip()
        if s:
            result.append(s[:220])
    return result[:5]


def normalize_decisions(value: Any) -> list[dict[str, str]]:
    if not isinstance(value, list):
        return []
    out: list[dict[str, str]] = []
    for item in value:
        if isinstance(item, dict):
            key = str(item.get("key") or "").strip()
            val = str(item.get("value") or "").strip()
            rationale = str(item.get("rationale") or "").strip()
        else:
            key = ""
            val = str(item).strip()
            rationale = ""
        if not val:
            continue
        if not key:
            key = "decision_" + hashlib.sha1(val.lower().encode("utf-8")).hexdigest()[:10]
        out.append({"key": key[:120], "value": val[:500], "rationale": rationale[:500]})
    return out[:5]


def merge_summary_texts(texts: list[str], *, max_len: int) -> str:
    lines: list[str] = []
    seen: set[str] = set()
    for text in texts:
        for raw_line in text.splitlines():
            line = raw_line.strip()
            if not line:
                continue
            normalized = re.sub(r"\s+", " ", line.lower())
            if normalized in seen:
                continue
            seen.add(normalized)
            lines.append(line)
            if len("\n".join(lines)) >= max_len:
                merged = "\n".join(lines)
                return merged[:max_len].rstrip() + "..."
    return "\n".join(lines)[:max_len]


def extract_fact_sentences(text: str) -> list[str]:
    signal = (
        "记住",
        "请记住",
        "偏好",
        "必须",
        "不要",
        "always",
        "never",
        "remember",
        "prefer",
        "must",
        "do not",
    )
    fragments = re.split(r"[。！？!?;\n]+", text)
    facts: list[str] = []
    for fragment in fragments:
        sentence = fragment.strip()
        if not sentence:
            continue
        lower = sentence.lower()
        if any(s in sentence for s in signal[:5]) or any(s in lower for s in signal[5:]):
            if len(sentence) > 220:
                sentence = sentence[:220].rstrip() + "..."
            facts.append(sentence)
    return facts[:5]


def build_rolling_summary_text(extracted: dict[str, Any]) -> str:
    text_sections: list[str] = []
    for title, key in (
        ("Goals", "goals"),
        ("Constraints", "constraints"),
        ("Decisions", "decisions"),
        ("Open Questions", "open_questions"),
    ):
        items = extracted.get(key) or []
        if key == "decisions":
            lines = [
                f"- {(d.get('key') or '').strip()}: {(d.get('value') or '').strip()}"
                for d in items
                if isinstance(d, dict)
            ]
        else:
            lines = [f"- {str(v).strip()}" for v in items if str(v).strip()]
        if lines:
            text_sections.append(f"{title}:\n" + "\n".join(lines[:5]))
    return "\n\n".join(text_sections)
