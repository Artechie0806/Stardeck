"""Shorten long chart labels (category names) so axes stay readable; cached per label.

The model proposes short forms; each is checked (non-empty, within the length limit, unique
among the batch) and anything that fails falls back to a plain trim with an ellipsis. Full labels
remain in tooltips and tables.
"""

import re
from collections.abc import Callable

from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from stardeck.db import LabelRecord
from stardeck.llm import LLMClient, LLMError

MAX_BATCH = 100


def trim(label: str, max_chars: int) -> str:
    """Deterministic fallback: keep the start, end with an ellipsis."""
    label = " ".join(label.split())
    return label if len(label) <= max_chars else label[: max_chars - 1].rstrip(" -,/&(") + "…"


_FILLER = {"and", "&", "of", "the", "for", "with", "a", "an", "in", "on", "to", "total", "department", "dept"}


def compact(label: str, max_chars: int) -> str | None:
    """Fit a label without cutting words mid-way.

    1. drop filler words ("and", "of", "the"...);
    2. if dropping just the last word fits (two+ words left), do that: "Apparel Footwear" reads
       better than "Appa. Foot. Acce.";
    3. otherwise abbreviate the longest words to four letters + "." until it fits.
    Returns None if nothing fits.
    """
    words = [w for w in re.split(r"[\s/,;|]+", label) if w]
    kept = [w for w in words if w.lower() not in _FILLER] or words
    if len(" ".join(kept)) <= max_chars:
        return " ".join(kept)
    if len(kept) >= 3 and len(" ".join(kept[:-1])) <= max_chars:
        return " ".join(kept[:-1])
    for i in sorted(range(len(kept)), key=lambda i: -len(kept[i])):
        if len(kept[i]) > 5 and not kept[i].endswith("."):
            kept[i] = kept[i][:4] + "."
        if len(" ".join(kept)) <= max_chars:
            return " ".join(kept)
    while len(kept) > 1 and len(" ".join(kept)) > max_chars:
        kept.pop()
    text = " ".join(kept)
    return text if 0 < len(text) <= max_chars else None


class ShortLabel(BaseModel):
    original: str
    short: str


class ShortLabels(BaseModel):
    labels: list[ShortLabel] = Field(description="One entry per input label, in the same order.")


PROMPT = """Shorten chart labels for a dashboard so each fits in {max_chars} characters.
Keep each label recognizable to a business user: keep the distinguishing words, drop filler
("Total", "Department of", "The"), use common abbreviations (Mgmt, Intl, Dept, Svc, Q1, Jan).
Never invent new meaning, and keep the labels distinct from each other.
Labels that already fit stay exactly as they are."""


def _ask(llm: LLMClient, labels: list[str], max_chars: int) -> dict[str, str]:
    """Ask the model; then ask once more for any suggestion that's still too long.

    Small models can't count characters precisely and tend to overshoot, so they're asked for a
    tighter target than the real limit.
    """
    target = max(6, max_chars - 5)
    messages = [
        {"role": "system", "content": PROMPT.format(max_chars=target)},
        {"role": "user", "content": "\n".join(labels)},
    ]
    proposed: dict[str, str] = {}
    for _ in range(2):
        result = llm.structured(messages, ShortLabels)
        proposed |= {r.original.strip(): " ".join(r.short.split()) for r in result.labels if r.short.strip()}
        too_long = [l for l in labels if len(proposed.get(l, "")) > max_chars or l not in proposed]
        if not too_long:
            break
        feedback = "\n".join(f"{l} -> {proposed.get(l, '?')} ({len(proposed.get(l, ''))} chars)" for l in too_long)
        messages += [
            {"role": "assistant", "content": result.model_dump_json()},
            {"role": "user", "content": f"These are still longer than {target} characters. Shorten them more "
                                        f"(abbreviate, drop words):\n{feedback}"},
        ]
        labels = too_long
    return proposed


def _key(db: Session, max_chars: int, label: str) -> str:
    """Labels are often customer or product names, so each workspace keeps its own."""
    return f"{db.info.get('workspace_id') or '-'}|{max_chars}|{label}"[:400]


def shorten(db: Session, llm: LLMClient | None, labels: list[str], max_chars: int,
            allow_ai: Callable[[], bool] | None = None) -> tuple[dict[str, str], bool]:
    """Short form for every label. Returns (mapping, whether the model was used).

    Results are cached whenever the model answered (after its one retry, trimming anything still
    too long). When the model is unreachable nothing is cached, so a later call gets a real attempt.
    """
    unique = list(dict.fromkeys(l for l in labels if l))[:MAX_BATCH]
    out: dict[str, str] = {}
    todo: list[str] = []
    for label in unique:
        if len(label) <= max_chars:
            out[label] = label
        elif hit := db.get(LabelRecord, _key(db, max_chars, label)):
            out[label] = hit.short
        else:
            todo.append(label)
    proposed: dict[str, str] = {}
    used_ai = False
    # `allow_ai` is asked only when the AI is actually needed (it may count against an allowance).
    if todo and llm is not None and (allow_ai is None or allow_ai()):
        try:
            proposed = _ask(llm, todo, max_chars)
            used_ai = True
        except LLMError:
            proposed = {}
    taken = set(out.values())
    for label in todo:
        # The model's wording if it fits; else that wording compacted; else the original compacted.
        candidates = [proposed.get(label, ""), compact(proposed.get(label, ""), max_chars) or "",
                      compact(label, max_chars) or "", trim(label, max_chars)]
        short = next(c for c in candidates if 0 < len(c) <= max_chars and c not in taken) \
            if any(0 < len(c) <= max_chars and c not in taken for c in candidates) else trim(label, max_chars)
        taken.add(short)
        out[label] = short
        if used_ai:
            db.merge(LabelRecord(key=_key(db, max_chars, label), short=short))
    db.commit()
    return out, used_ai
