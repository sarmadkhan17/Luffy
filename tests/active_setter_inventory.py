"""AST inventory of every code path that can persist control_state=ACTIVE.

LUFFY-ACTIVATION-AND-RISK-BASELINE-HARDENING-V2 (blocker 5). Replaces the V1
line regex, which missed multiline calls, aliases and wrappers. Static and
deliberately over-approximating: a call is a *site* when a state setter
receives a value that may be ACTIVE in its new-state position.

ACTIVE values: `<ControlState alias>.ACTIVE`, `...ControlState.ACTIVE` through
a module alias, `ControlState("ACTIVE")`, `ControlState["ACTIVE"]`,
`getattr(<alias>, "ACTIVE")`, the string "ACTIVE" (and `.value` of any of
these), any expression containing one (IfExp, tuples ...), and any name ever
assigned such an expression in the same function or at module level.

Setters: the primitive `kv_set("control_state", X)` (X = new state) and SQL
text writing control_state; then, to a fixpoint, every function that passes
one of its own parameters into a setter's new-state position (a wrapper) —
ControlStateMachine._set_fenced/set/set_if_current/FencedControl.apply are
found this way, and so is any newly added helper. Calls resolve by bare name
or attribute name (receiver ignored), through `name = x.setter` method aliases,
`from m import f as g` import aliases and `functools.partial(setter, ACTIVE)`.

Known limits (documented, not silently ignored): dynamic dispatch through
getattr(obj, "<computed>"), exec/eval and cross-process writers outside the
scanned tree. The dashboard/chat setters are out of kernel scope (reported
separately by the test, not allowlisted).
"""
from __future__ import annotations

import ast
import functools
from dataclasses import dataclass
from pathlib import Path

#: keyword-only parameter: never matched positionally
KWONLY = 10 ** 6
#: primitive persistence: kv_set(key, value) — a *keyed* setter; it sets
#: control_state when its key is "control_state". Entries are
#: (key param, key index, value param, value index) as called.
PRIMITIVE = {"kv_set": {("key", 0, "value", 1)}}
SQL_CALLS = ("execute", "executemany")


@dataclass(frozen=True)
class Site:
    path: str
    function: str           # qualified name of the enclosing function
    call: str               # the setter/wrapper name called
    line: int

    def key(self) -> tuple[str, str, str]:
        return (self.path, self.function, self.call)


class _Module:
    def __init__(self, path: str, source: str):
        self.path = path
        self.source = source
        self.tree = ast.parse(source, filename=path)
        self.cs_aliases = {"ControlState"}      # names bound to the enum
        self.mod_aliases: set[str] = set()      # names bound to a module
        self.imports: dict[str, str] = {}       # asname -> original name
        for node in ast.walk(self.tree):
            if isinstance(node, ast.ImportFrom):
                for a in node.names:
                    if a.asname:
                        self.imports[a.asname] = a.name
                    if a.name == "ControlState":
                        self.cs_aliases.add(a.asname or a.name)
            elif isinstance(node, ast.Import):
                for a in node.names:
                    self.mod_aliases.add(a.asname or a.name.split(".")[0])
        # module-level `CS = ControlState` style aliases (to a fixpoint)
        changed = True
        while changed:
            changed = False
            for node in ast.walk(self.tree):
                if isinstance(node, ast.Assign) and self._is_enum(node.value):
                    for t in node.targets:
                        if isinstance(t, ast.Name) and t.id not in self.cs_aliases:
                            self.cs_aliases.add(t.id)
                            changed = True

    def _is_enum(self, node) -> bool:
        if isinstance(node, ast.Name):
            return node.id in self.cs_aliases
        return isinstance(node, ast.Attribute) and node.attr == "ControlState"


def _const_str(node) -> str | None:
    return node.value if isinstance(node, ast.Constant) and isinstance(node.value, str) else None


def _call_name(func, aliases: dict[str, tuple[str, str]], imports: dict[str, str]
               ) -> tuple[str | None, str]:
    """(name, "attr" | "bare"). Aliases resolve through chains: `a = x.set;
    b = a` makes b an "attr" call of set; `g = helper` a "bare" call of helper."""
    if isinstance(func, ast.Attribute):
        return func.attr, "attr"
    if isinstance(func, ast.Name):
        kind, name = aliases.get(func.id, ("bare", func.id))
        return (imports.get(name, name) if kind == "bare" else name), kind
    return None, "bare"


def _arg(call: ast.Call, name: str | None, index: int):
    """The argument bound to parameter `name` / position `index`, if any."""
    if index < len(call.args) and not any(isinstance(a, ast.Starred)
                                          for a in call.args[:index + 1]):
        return call.args[index]
    for k in call.keywords:
        if name is not None and k.arg == name:
            return k.value
    return None


class _Scope:
    """One function (or the module): its own calls/assignments, collected once."""

    def __init__(self, qual, node, parent, is_method):
        self.qual, self.node, self.parent, self.is_method = qual, node, parent, is_method
        self.calls: list[ast.Call] = []
        self.assigns: list[tuple[list, ast.AST]] = []
        self.own_aliases: dict[str, tuple[str, str]] = {}   # a = x.set
        self.own_links: dict[str, str] = {}                 # b = a
        self._aliases = None
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            self.params = [x.arg for x in node.args.posonlyargs + node.args.args]
            self.kwonly = [x.arg for x in node.args.kwonlyargs]
            self.offset = 1 if self.params and self.params[0] in ("self", "cls") else 0
            self.vararg = node.args.vararg.arg if node.args.vararg else None
            self.kwarg = node.args.kwarg.arg if node.args.kwarg else None
        else:
            self.params, self.kwonly, self.offset = [], [], 0
            self.vararg = self.kwarg = None

    def param_entries(self, p: str) -> list[tuple[str | None, int]]:
        """How a caller binds this scope's parameter `p` (name, index)."""
        if p in self.params:
            return [(p, self.params.index(p) - self.offset)]
        if p in self.kwonly:
            return [(p, KWONLY)]
        if p == self.vararg:                                   # *args pass-through
            return [(None, len(self.params) - self.offset + i) for i in range(4)]
        if p == self.kwarg:                                    # **kwargs pass-through
            return [("new", KWONLY), ("value", KWONLY), ("state", KWONLY),
                    ("target", KWONLY)]
        return []

    @property
    def aliases(self) -> dict[str, tuple[str, str]]:
        if self._aliases is None:
            chain, s = [], self
            while s is not None:
                chain.append(s)
                s = s.parent
            out, links = {}, {}
            for sc in reversed(chain):
                out.update(sc.own_aliases)
                links.update(sc.own_links)
                for t in sc.own_aliases:
                    links.pop(t, None)
            for _ in range(len(links) + 1):                    # resolve chains
                for t, src in links.items():
                    out[t] = out.get(src, ("bare", src))
            self._aliases = out
        return self._aliases


def _scopes(tree) -> list[_Scope]:
    """Every function scope (nested and methods included) plus the module."""
    module = _Scope("<module>", tree, None, False)
    scopes = [module]
    stack = [(child, module, "", False) for child in tree.body]
    while stack:
        n, scope, prefix, in_class = stack.pop()
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
            q = f"{prefix}{n.name}"
            inner = _Scope(q, n, scope, in_class)
            scopes.append(inner)
            stack.extend((c, inner, q + ".", False) for c in
                         n.body + n.decorator_list + n.args.defaults + n.args.kw_defaults
                         if c is not None)
            continue
        if isinstance(n, ast.ClassDef):
            stack.extend((c, scope, f"{prefix}{n.name}.", True) for c in n.body)
            continue
        if isinstance(n, ast.Call):
            scope.calls.append(n)
        elif isinstance(n, (ast.Assign, ast.AnnAssign, ast.NamedExpr)) and n.value:
            targets = n.targets if isinstance(n, ast.Assign) else [n.target]
            scope.assigns.append((targets, n.value))
            for t in targets:
                if not isinstance(t, ast.Name):
                    continue
                if isinstance(n.value, ast.Attribute):
                    scope.own_aliases[t.id] = ("attr", n.value.attr)
                    scope.own_links.pop(t.id, None)
                elif isinstance(n.value, ast.Name):
                    scope.own_links[t.id] = n.value.id
        elif isinstance(n, ast.AugAssign):
            scope.assigns.append(([n.target], n.value))
        stack.extend((c, scope, prefix, in_class) for c in ast.iter_child_nodes(n))
    return scopes


class Inventory:
    def __init__(self, sources: dict[str, str]):
        parsed = [_parsed(p, s) for p, s in sorted(sources.items())]
        self.modules = [m for m, _ in parsed]
        self.scopes = {m.path: sc for m, sc in parsed}
        # setter name -> (param name, positional index as called) of the new state
        self.setters: dict[str, set[tuple[str | None, int]]] = {}
        #: (path, qualname) of every function that forwards a parameter into a
        #: setter's new-state position: a dynamic setter entry point
        self.wrapper_defs: set[tuple[str, str]] = set()
        #: setters defined as plain functions: only these match bare-name calls
        #: (a bare `set(x)` is the builtin, never ControlStateMachine.set)
        self.bare_setters: set[str] = set()
        #: keyed setters (key and value both forwarded), seeded with kv_set
        self.keyed: dict[str, set[tuple]] = {k: set(v) for k, v in PRIMITIVE.items()}
        self._find_wrappers()

    # ── ACTIVE value analysis ─────────────────────────────────────────────
    def _is_active(self, m: _Module, node, aliases: set[str]) -> bool:
        for n in ast.walk(node):
            if isinstance(n, ast.Constant) and n.value == "ACTIVE":
                return True
            if isinstance(n, ast.Attribute) and n.attr == "ACTIVE" and (
                    m._is_enum(n.value) or (isinstance(n.value, ast.Attribute)
                                            and n.value.attr == "ControlState")):
                return True
            if isinstance(n, ast.Name) and n.id in aliases:
                return True
        return False

    def _active_names(self, m: _Module, scope: _Scope, inherited: set[str]) -> set[str]:
        names = set(inherited)
        changed = True
        while changed:
            changed = False
            for targets, value in scope.assigns:
                if not self._is_active(m, value, names):
                    continue
                for t in targets:
                    for tn in ast.walk(t):
                        if isinstance(tn, ast.Name) and tn.id not in names:
                            names.add(tn.id)
                            changed = True
        return names

    def _new_state_args(self, call: ast.Call, name: str, m: _Module,
                        kind: str = "attr") -> list:
        """The argument node(s) in the new-state position of a setter call."""
        out = []
        if kind == "bare" and name not in self.bare_setters and name not in PRIMITIVE:
            return out
        for kn, ki, vn, vi in self.keyed.get(name, ()):         # keyed: key must match
            if _const_str(_arg(call, kn, ki)) == "control_state":
                value = _arg(call, vn, vi)
                out += [value] if value is not None else []
        for param, index in self.setters.get(name, ()):
            value = _arg(call, param, index)
            out += [value] if value is not None else []
        if self.setters.get(name) or self.keyed.get(name):
            out += [a.value for a in call.args if isinstance(a, ast.Starred)]
            out += [k.value for k in call.keywords if k.arg is None]      # **kwargs
        return out

    @staticmethod
    def _sql_key_value(call: ast.Call):
        """(key node, value node) of a state_kv write with bound parameters."""
        text = " ".join(filter(None, (_const_str(n) for n in ast.walk(call.args[0])))) \
            if call.args else ""
        if "state_kv" not in text or len(call.args) < 2 or not isinstance(
                call.args[1], (ast.Tuple, ast.List)) or len(call.args[1].elts) < 2:
            return None
        a, b = call.args[1].elts[:2]
        if "SET VALUE" in " ".join(text.upper().split()).replace("VALUE=", "VALUE ="):
            return b, a                                          # SET value=? WHERE key=?
        return a, b                                              # (key,value) VALUES (?,?)

    def _sql_writes_active(self, call: ast.Call, name: str, m, names) -> bool:
        if name not in SQL_CALLS + ("executescript",):
            return False
        text = " ".join(filter(None, (_const_str(n) for n in ast.walk(call))))
        return ("control_state" in text and ("state_kv" in text or "UPDATE" in text.upper())
                and any(self._is_active(m, a, names) for a in call.args))

    def _calls(self, m: _Module, scope: _Scope, names: set[str]):
        """(call, resolved name, is ACTIVE site) for each call in `scope`."""
        method_aliases = scope.aliases
        for n in scope.calls:
            name, kind = _call_name(n.func, method_aliases, m.imports)
            if name == "partial" and n.args:            # functools.partial(setter, ...)
                inner, ikind = _call_name(n.args[0], method_aliases, m.imports)
                if inner in self.setters or inner in PRIMITIVE:
                    fake = ast.Call(func=n.args[0], args=n.args[1:], keywords=n.keywords)
                    yield n, inner, any(self._is_active(m, a, names)
                                        for a in self._new_state_args(fake, inner, m, ikind))
                continue
            if name is None:
                continue
            args = self._new_state_args(n, name, m, kind)
            active = any(self._is_active(m, a, names) for a in args)
            yield n, name, active or self._sql_writes_active(n, name, m, names)

    def _find_wrappers(self) -> None:
        changed = True
        while changed:
            changed = False
            for m in self.modules:
                for sc in self.scopes[m.path][1:]:
                    aliases = sc.aliases
                    for call in sc.calls:
                        name, kind = _call_name(call.func, aliases, m.imports)
                        if name is None:
                            continue
                        for arg in self._new_state_args(call, name, m, kind):
                            for p in {n.id for n in ast.walk(arg) if isinstance(n, ast.Name)}:
                                changed |= self._register(m, sc, self.setters,
                                                          sc.param_entries(p))
                        # keyed forwarding: both key and value are parameters
                        pairs = []
                        if name in SQL_CALLS:
                            kv = self._sql_key_value(call)
                            pairs = [kv] if kv else []
                        elif kind == "attr" or name in self.bare_setters or name in PRIMITIVE:
                            pairs = [(_arg(call, kn, ki), _arg(call, vn, vi))
                                     for kn, ki, vn, vi in self.keyed.get(name, ())]
                        for key, value in pairs:
                            if not (isinstance(key, ast.Name) and isinstance(value, ast.Name)):
                                continue
                            for kn, ki in sc.param_entries(key.id):
                                changed |= self._register(
                                    m, sc, self.keyed,
                                    [(kn, ki, vn, vi) for vn, vi in sc.param_entries(value.id)])

    def _register(self, m, sc, table, entries) -> bool:
        if not entries:
            return False
        self.wrapper_defs.add((m.path, sc.qual))
        if not sc.is_method:
            self.bare_setters.add(sc.node.name)
        new = set(entries) - table.setdefault(sc.node.name, set())
        table[sc.node.name] |= new
        return bool(new)

    # ── the inventory ─────────────────────────────────────────────────────
    def sites(self) -> list[Site]:
        found = []
        for m in self.modules:
            if "ACTIVE" not in m.source:     # no ACTIVE value can occur in it
                continue
            names_of = {}
            for sc in self.scopes[m.path]:                   # parents precede children
                inherited = names_of[id(sc.parent)] if sc.parent is not None else set()
                names_of[id(sc)] = names = self._active_names(m, sc, inherited)
                for call, name, active in self._calls(m, sc, names):
                    if active:
                        found.append(Site(m.path, sc.qual, name, call.lineno))
        return sorted(found, key=lambda s: (s.path, s.line))


@functools.lru_cache(maxsize=None)
def _parsed(path: str, source: str) -> tuple[_Module, list[_Scope]]:
    """Parse once per exact source text (the AST is only read, never mutated)."""
    m = _Module(path, source)
    return m, _scopes(m.tree)


def load(root: Path, rels: list[str], overrides: dict[str, str] | None = None
         ) -> Inventory:
    """Inventory of files under `rels`; `overrides` replaces text in memory."""
    sources = {}
    for rel in rels:
        path = root / rel
        for f in ([path] if path.is_file() else sorted(path.rglob("*.py"))):
            sources[str(f.relative_to(root))] = f.read_text()
    sources.update(overrides or {})
    return Inventory(sources)


def scan_tree(root: Path, rels: list[str], overrides: dict[str, str] | None = None
              ) -> list[Site]:
    return load(root, rels, overrides).sites()
