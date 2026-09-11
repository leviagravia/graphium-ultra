from __future__ import annotations
import pathlib, shutil
from tests.desktop.harness.runtime import isolated_env

APPS=('graphium','leafpad','l3afpad','mousepad','featherpad')
SIZES={'empty':0,'5KiB':5*1024,'1MiB':1024*1024,'10MiB':10*1024*1024}


def command_for(app,fixture,repo):
    if app=='graphium': base=[str(pathlib.Path(repo)/'bin/graphium')]
    else:
        exe=shutil.which(app)
        if not exe: raise RuntimeError(f'BLOCKED executable missing: {app}')
        base=[exe]
    if app=='mousepad': base.append('--disable-server')
    if app=='featherpad': base.append('--standalone')
    if fixture is not None: base.append(str(fixture))
    return base


def workload_bytes(workload):
    size=SIZES[workload]
    if size==0: return b''
    head=f'GRAPHIUM::{workload}::BEGIN\n'.encode('ascii')
    tail=f'\nGRAPHIUM::{workload}::END\n'.encode('ascii')
    if len(head)+len(tail)>size: raise ValueError(workload)
    return head+b'x'*(size-len(head)-len(tail))+tail
