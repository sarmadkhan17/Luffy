"""Explicit relocation of owned files; historical identity strings stay intact."""
import json
from pathlib import Path

from .config import ROOT, Env


def aliases():
    roots = json.loads(Env.get('LUFFY_LEGACY_ROOTS', '[]'))
    if not isinstance(roots, list) or any(not isinstance(x, str) for x in roots):
        raise ValueError('installation_aliases_invalid')
    paths = [Path(x) for x in roots]
    if any(not p.is_absolute() or p == Path('/') or '..' in p.parts for p in paths):
        raise ValueError('installation_aliases_invalid')
    return sorted(paths, key=lambda p: len(p.parts), reverse=True)


def relocated_path(value):
    """Return a mapped path only for an explicitly declared old project root.

    No lookup of the old host path or symlink is needed. Every alias maps to the
    current project root, retaining its suffix; arbitrary destination routing
    and escapes from the copied project are refused.
    """
    path = Path(value)
    for old in aliases():
        if path.is_relative_to(old):
            target = (ROOT / path.relative_to(old)).resolve()
            if not target.is_relative_to(ROOT.resolve()):
                raise ValueError('installation_path_escape')
            return target
    return None


def owned_path(value):
    moved = relocated_path(value)
    return str(moved) if moved is not None else value


def same_relocated_path(historical, actual):
    moved = relocated_path(historical)
    return moved is not None and moved == Path(actual).resolve()
