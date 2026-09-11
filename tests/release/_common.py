from __future__ import annotations
import ast, os
from pathlib import Path
ROOT=Path(os.environ.get('GRAPHIUM_REPO_ROOT',Path(__file__).resolve().parents[2])).resolve()
def parse(rel:str): return ast.parse((ROOT/rel).read_text(encoding='utf-8'),filename=rel)
def imports(rel:str):
    out=[]
    for n in ast.walk(parse(rel)):
        if isinstance(n,ast.Import): out.extend(a.name for a in n.names)
        elif isinstance(n,ast.ImportFrom) and n.module: out.append(n.module)
    return out
def call_name(n):
    if isinstance(n,ast.Name): return n.id
    if isinstance(n,ast.Attribute):
        parts=[]
        while isinstance(n,ast.Attribute): parts.append(n.attr); n=n.value
        if isinstance(n,ast.Name): parts.append(n.id)
        return '.'.join(reversed(parts))
    return ''
