"""Pure Graphium Plus Clips v1 model, Markdown codec and checked store."""
from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
import re
import uuid

from .companion_storage import CheckedTextAuthority, CompanionFileToken, CompanionAuthorityError

_HEADER = "# Graphium Plus Clips v1"
_MAX_RECORDS = 200
_ID_RE = re.compile(r"^clip-[0-9a-f]{32}$")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _single(value: object, fallback: str = "") -> str:
    text = str(value if value is not None else fallback).replace("\r", " ").replace("\n", " ").strip()
    return text


def _fence(text: str) -> str:
    longest = current = 0
    for ch in text:
        if ch == "`":
            current += 1; longest = max(longest, current)
        else:
            current = 0
    return "`" * max(3, longest + 1)


@dataclass(frozen=True, slots=True)
class ClipRecord:
    id: str
    title: str
    body: str
    created: str
    updated: str

    @property
    def search_text(self) -> str:
        return f"{self.title}\n{self.body}".casefold()


def new_clip(title: str, body: str) -> ClipRecord:
    now = _now()
    title = _single(title) or (_single(body).strip()[:48] or "Untitled Clip")
    return ClipRecord(f"clip-{uuid.uuid4().hex}", title, str(body), now, now)


def update_clip(record: ClipRecord, *, title: str, body: str) -> ClipRecord:
    return replace(record, title=_single(title) or "Untitled Clip", body=str(body), updated=_now())


def serialize_clips(records) -> str:
    records = tuple(records)
    if len(records) > _MAX_RECORDS:
        raise ValueError("Clips limit is 200 records.")
    ids: set[str] = set(); lines = [_HEADER, ""]
    for record in records:
        if not isinstance(record, ClipRecord) or not _ID_RE.match(record.id) or record.id in ids:
            raise ValueError("Clips contain an invalid or duplicate stable ID.")
        ids.add(record.id); fence = _fence(record.body)
        lines += [f"## {record.title}", f"ID: {record.id}", f"Created: {record.created}", f"Updated: {record.updated}", "", "### Body", "", fence + "text", record.body, fence, ""]
    return "\n".join(lines).rstrip() + "\n"


def parse_clips(text: str) -> tuple[ClipRecord, ...]:
    if not text.strip():
        return ()
    lines = text.splitlines()
    first = next((line.strip() for line in lines if line.strip()), "")
    if first != _HEADER:
        raise ValueError(f"Expected Clips header: {_HEADER}")
    out=[]; ids=set(); i=0
    while i < len(lines):
        if not lines[i].startswith("## "):
            i += 1; continue
        title=lines[i][3:].strip(); i += 1; fields={}
        while i < len(lines) and lines[i].strip() != "### Body" and not lines[i].startswith("## "):
            if ":" in lines[i]:
                k,v=lines[i].split(":",1); fields[k.strip()]=v.strip()
            i += 1
        if i >= len(lines) or lines[i].strip() != "### Body": raise ValueError(f"Clip {title!r} has no Body section.")
        i += 1
        while i < len(lines) and not lines[i].strip(): i += 1
        if i >= len(lines): raise ValueError(f"Clip {title!r} has no body fence.")
        opener=lines[i].strip(); ticks=len(opener)-len(opener.lstrip("`"))
        if ticks < 3: raise ValueError(f"Clip {title!r} has invalid body fence.")
        fence="`"*ticks; i += 1; body=[]
        while i < len(lines) and lines[i].strip()!=fence: body.append(lines[i]); i += 1
        if i >= len(lines): raise ValueError(f"Clip {title!r} body fence is not closed.")
        i += 1
        rid=fields.get("ID", "")
        if not _ID_RE.match(rid) or rid in ids: raise ValueError("Clip has invalid or duplicate stable ID.")
        ids.add(rid)
        out.append(ClipRecord(rid, title or "Untitled Clip", "\n".join(body), fields.get("Created",""), fields.get("Updated","")))
        if len(out)>_MAX_RECORDS: raise ValueError("Clips limit is 200 records.")
    return tuple(out)


@dataclass(frozen=True, slots=True)
class ClipSnapshot:
    records: tuple[ClipRecord, ...]
    token: CompanionFileToken


class ClipStore:
    def __init__(self, writer, path: str | Path) -> None:
        self._authority=CheckedTextAuthority(writer,path)
    @property
    def path(self): return self._authority.path
    def load(self) -> ClipSnapshot:
        snap=self._authority.load(); return ClipSnapshot(parse_clips(snap.text),snap.token)
    def save(self, records, expected: CompanionFileToken) -> ClipSnapshot:
        snap=self._authority.save(serialize_clips(records), expected); return ClipSnapshot(parse_clips(snap.text),snap.token)


def search_clips(records, query: str) -> tuple[ClipRecord,...]:
    tokens=tuple(x.casefold() for x in str(query).split() if x.strip())
    return tuple(r for r in records if all(t in r.search_text for t in tokens))
