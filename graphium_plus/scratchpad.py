"""Pure Graphium Plus Scratchpad v1 model, Markdown codec and checked sidecar store."""
from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
import re, uuid

from .companion_storage import CheckedTextAuthority, CompanionFileToken

_HEADER="# Graphium Plus Scratchpad v1"
_ID_RE=re.compile(r"^scratch-[0-9a-f]{32}$")
SCRATCHPAD_SUFFIX=".scratchpad.md"


def scratchpad_path(document_path: str | None) -> Path | None:
    if not document_path: return None
    return Path(str(document_path) + SCRATCHPAD_SUFFIX)

def is_managed_scratchpad_path(path: str | Path | None) -> bool:
    if path is None: return False
    return str(path).casefold().endswith(SCRATCHPAD_SUFFIX.casefold())

def _now(): return datetime.now(timezone.utc).isoformat(timespec="seconds")
def _single(v): return str(v or "").replace("\r"," ").replace("\n"," ").strip()
def _fence(text):
    longest=current=0
    for ch in text:
        if ch=='`': current+=1; longest=max(longest,current)
        else: current=0
    return '`'*max(3,longest+1)

def _tags(value):
    if isinstance(value,str): values=value.split(',')
    else: values=value
    out=[]
    for item in values or ():
        clean=_single(item)
        if clean and clean not in out: out.append(clean)
    return tuple(out)

@dataclass(frozen=True, slots=True)
class ScratchRecord:
    id:str; title:str; body:str; tags:tuple[str,...]; status:str; created:str; updated:str
    extra_fields:tuple[tuple[str,str],...]=()
    @property
    def search_text(self): return f"{self.title}\n{self.body}\n{' '.join(self.tags)}".casefold()

def new_scratch(title,body,tags=()):
    now=_now(); title=_single(title) or (_single(body)[:48] or "Untitled Note")
    return ScratchRecord(f"scratch-{uuid.uuid4().hex}",title,str(body),_tags(tags),"active",now,now,())
def update_scratch(record,*,title,body,tags=None,status=None):
    target_status=record.status if status is None else str(status)
    if target_status not in {"active","archived"}: raise ValueError("Scratchpad status must be active or archived.")
    return replace(record,title=_single(title) or "Untitled Note",body=str(body),tags=record.tags if tags is None else _tags(tags),status=target_status,updated=_now())

def serialize_scratchpad(records):
    ids=set(); lines=[_HEADER,""]
    for r in tuple(records):
        if not isinstance(r,ScratchRecord) or not _ID_RE.match(r.id) or r.id in ids: raise ValueError("Scratchpad contains invalid or duplicate stable ID.")
        if r.status not in {"active","archived"}: raise ValueError("Scratchpad status must be active or archived.")
        ids.add(r.id); fence=_fence(r.body)
        lines += [f"## {r.title}",f"ID: {r.id}",f"Status: {r.status}",f"Tags: {', '.join(r.tags)}",f"Created: {r.created}",f"Updated: {r.updated}"]
        lines += [f"{label}: {value}" for label,value in r.extra_fields]
        lines += ["","### Body","",fence+"text",r.body,fence,""]
    return "\n".join(lines).rstrip()+"\n"

def parse_scratchpad(text):
    if not text.strip(): return ()
    lines=text.splitlines(); first=next((x.strip() for x in lines if x.strip()),"")
    if first!=_HEADER: raise ValueError(f"Expected Scratchpad header: {_HEADER}")
    out=[]; ids=set(); i=0
    while i<len(lines):
        if not lines[i].startswith("## "): i+=1; continue
        title=lines[i][3:].strip(); i+=1; fields={}; extras=[]
        known={'ID','Status','Tags','Created','Updated'}
        while i<len(lines) and lines[i].strip()!="### Body" and not lines[i].startswith("## "):
            if ':' in lines[i]:
                k,v=lines[i].split(':',1); key=k.strip(); value=v.strip()
                if key in known: fields[key]=value
                elif key: extras.append((key,value))
            i+=1
        if i>=len(lines) or lines[i].strip()!="### Body": raise ValueError(f"Scratchpad {title!r} has no Body section.")
        i+=1
        while i<len(lines) and not lines[i].strip(): i+=1
        if i>=len(lines): raise ValueError("Scratchpad body fence missing.")
        opener=lines[i].strip(); ticks=len(opener)-len(opener.lstrip('`'))
        if ticks<3: raise ValueError("Scratchpad body fence invalid.")
        fence='`'*ticks; i+=1; body=[]
        while i<len(lines) and lines[i].strip()!=fence: body.append(lines[i]); i+=1
        if i>=len(lines): raise ValueError("Scratchpad body fence not closed.")
        i+=1; rid=fields.get('ID',''); status=fields.get('Status','')
        if not _ID_RE.match(rid) or rid in ids: raise ValueError("Scratchpad invalid or duplicate stable ID.")
        if status not in {'active','archived'}: raise ValueError("Scratchpad status must be active or archived.")
        ids.add(rid); out.append(ScratchRecord(rid,title or "Untitled Note","\n".join(body),_tags(fields.get('Tags','')),status,fields.get('Created',''),fields.get('Updated',''),tuple(extras)))
    return tuple(out)

@dataclass(frozen=True, slots=True)
class ScratchSnapshot:
    records:tuple[ScratchRecord,...]; token:CompanionFileToken

class ScratchpadStore:
    def __init__(self,writer,path): self._authority=CheckedTextAuthority(writer,path)
    @property
    def path(self): return self._authority.path
    def load(self):
        snap=self._authority.load(); return ScratchSnapshot(parse_scratchpad(snap.text),snap.token)
    def save(self,records,expected):
        snap=self._authority.save(serialize_scratchpad(records),expected); return ScratchSnapshot(parse_scratchpad(snap.text),snap.token)

def search_scratchpad(records,query,status='active'):
    tokens=tuple(x.casefold() for x in str(query).split() if x.strip()); status=str(status)
    return tuple(r for r in records if (status=='all' or r.status==status) and all(t in r.search_text for t in tokens))
