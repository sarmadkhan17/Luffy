# Coding Conventions

**Analysis Date:** 2026-09-03

## Naming Patterns

**Files:**
- `snake_case` for all Python files
- Prefix helpers/private modules with underscore (e.g., `_journal.py` if internal utility)
- Test files: `test_<module_name>.py` or `test_<feature_name>.py`

**Functions:**
- `snake_case` for all functions
- Helper/private functions prefixed with single underscore: `_helper_function()`
- Short, descriptive names that indicate purpose: `decide()`, `evaluate()`, `frames()`
- Verb-forward for actions: `place_stop()`, `close_partial()`, `signal_overlap()`

**Variables:**
- `snake_case` for all variables
- Single-letter loop variables acceptable for brief loops: `i`, `x`, `s`
- Meaningful names preferred: `frames`, `backtest_results`, `entry_price`
- Temporary aggregation variables: `base` for dict/obj base before update, `kw` for kwargs dicts

**Types:**
- `CamelCase` for all classes
- `UPPERCASE_WITH_UNDERSCORES` for module-level constants
- Abbreviations in uppercase: `CFG`, `SQL`, `BUY`, `SELL`

**Examples in codebase:**
- Function: `signal_overlap()`, `recent_verdict()`, `compile_spec()`
- Class: `Analyst`, `StrategySpec`, `ExitSpec`, `Journal`, `Executor`
- Constant: `TIMEFRAMES`, `DIRECTIONS`, `REGIMES`, `MIN_THESIS`, `MAX_SIGNAL_OVERLAP`

## Code Style

**Formatting:**
- No explicit linter/formatter config found; follows PEP 8 conventions
- Indentation: 4 spaces
- Line length: observed ~80-100 char pragmatic limit (not strictly enforced)
- Blank lines: 2 between top-level definitions, 1 between methods in a class

**Linting:**
- No `.eslintrc` or linting config files present
- Follows standard Python PEP 8 style
- Uses `from __future__ import annotations` for postponed evaluation of annotations

**Type Hints:**
- Widely used throughout codebase
- Union types: `str | None` (Python 3.10+ style) instead of `Optional[str]`
- Dataclass fields frequently annotated: `id: str`, `name: str`, `exit: ExitSpec`
- Return type hints on all public functions: `def evaluate(...) -> tuple[bool, dict]:`
- Container types explicit: `list[str]`, `dict[str, float]`, `tuple[bool, dict]`

**Examples:**
```python
def frames(self, tf: str, extra: tuple = ()) -> dict:
def evaluate(self, spec: StrategySpec, timeframes=TIMEFRAMES) -> tuple[bool, dict]:
@dataclass
class StrategySpec:
    id: str
    name: str
    exit: ExitSpec
    direction: str
```

## Import Organization

**Order:**
1. `from __future__ import annotations` (always first if used)
2. Standard library imports: `import json`, `import logging`, `import re`
3. Third-party imports: `import numpy as np`, `import pandas as pd`, `import pytest`
4. Local/relative imports: `from . import ideas`, `from ..core.journal import Journal`

**Path Aliases:**
- Relative imports use dot notation: `from . import ideas`, `from ..core.config import ROOT`
- Direct module paths when crossing deep trees: `from trader.brain.analyst import Analyst`
- No explicit import aliases observed (no `import X as Y` pattern overused)

**Imports in source:**
```python
from __future__ import annotations

import json
import logging
import re
from dataclasses import asdict, dataclass, field

import numpy as np
import pandas as pd

from ..core.config import ROOT
from ..core.journal import Journal
from . import ideas
```

## Error Handling

**Patterns:**
- Custom exceptions for domain errors: `SpecError(ValueError)`, `RiskError(Exception)`, `MCPError(RuntimeError)`
- Try-except with logging for runtime recovery: `except Exception as e: log.warning(...)`
- Silent `except Exception:` for optional operations (e.g., `_confirm_fill()`)
- Multiple except blocks for specific recovery paths (e.g., `executor.py` lines 221-227)

**Examples:**
```python
try:
    compiled = compile_spec(probe)
except Exception as e:
    results[tf] = {"error": str(e)}
    continue

try:
    c.execute(stmt)
except Exception:
    pass  # optional schema update, pre-existing table OK

try:
    self.exchange.create_order(...)
except Exception as e:
    log.warning(f"[{symbol}] entry failed: {e}")
```

**Guidance:**
- Log at WARNING level for recoverable errors, use exception message as context
- NEVER silence an exception without a comment explaining why
- Use specific exception types in custom code (`SpecError`, `RiskError`)

## Logging

**Framework:** Python built-in `logging` module

**Patterns:**
```python
import logging
log = logging.getLogger(__name__)
log.warning(f"[{symbol}] {message}")
```

**Guidelines:**
- One logger per module: `log = logging.getLogger(__name__)` at module level
- Use f-strings for context: `log.warning(f"[{symbol}] entry failed: {e}")`
- Log failed retries at WARNING level
- Level conventions: WARNING for recoverable issues, ERROR for journal problems

**No custom logging config in codebase** — relies on application-level setup

## Comments

**When to Comment:**
- Document non-obvious algorithm choices: "see the next bullet"
- Explain trade-offs: "Timeframe is a gene, and it is a consequential one..."
- Point to external constraints: "Binance USDM books a reduceOnly STOP_MARKET as..."
- Mark sections: `# ── signal overlap ───────────────────────────────────────`

**Section Separators:**
- Horizontal rule comments: `# ── NAME ───────────────────────────────────────`
- Used within classes and modules to organize related methods/functions

**Inline Comments:**
- Prefix `:` with context where helpful: `#: load-bearing — never delete`
- Example: `:` comments in CLAUDE.md for emphasis
- Use sparingly; prefer self-documenting code

**JSDoc/Docstrings:**
- Module-level docstrings: Multi-line `"""..."""` with purpose and key concepts
- Class docstrings: Brief one-liner if obvious, longer if complex
- Function docstrings: Not required for obvious functions; used for complex logic
- No @param/@return style; favor type hints instead

**Examples:**
```python
"""StrategySpec — the company's work product.

A strategy is ONE self-contained artifact: a named, falsifiable hypothesis
with its own entry logic, its own filters and its OWN EXITS. It replaces
`Genome`, whose family+params shape forced every scraped idea into one of
eight templates...
"""

def _declared(spec: StrategySpec) -> tuple:
    """The markets the spec itself says it trades."""
```

## Function Design

**Size:**
- Pragmatic: most functions 10-50 lines
- Large functions acceptable if they sequence clear steps (e.g., `evaluate()` in `analyst.py`)
- Consider extracting nested loops or exception handlers into helper functions

**Parameters:**
- Positional-only for simple functions
- Keyword-only after `*` when multiple optional parameters
- Avoid more than 5 positional parameters; use dataclass/dict if needed

**Return Values:**
- Tuple returns common: `tuple[bool, dict]`, `tuple[bool, list[str]]`
- Dict preferred for structured output with named fields
- None for side-effect functions (e.g., logging operations)

**Examples:**
```python
def evaluate(self, spec: StrategySpec, timeframes=TIMEFRAMES) -> tuple[bool, dict]:
    """Judge a candidate on recent evidence, best timeframe wins."""
    # ... complex logic returning (success_bool, details_dict)
    return best is not None, {...}

def frames(self, tf: str, extra: tuple = ()) -> dict:
    """Load frames for one timeframe, caching internally."""
    # ... returns dict of symbol -> DataFrame
```

## Module Design

**Exports:**
- No `__all__` observed in codebase; imports are direct
- Public API: classes and functions not prefixed with `_`
- Private/internal: `_helper()`, `_private_method()`

**Barrel Files:**
- Not used; imports go direct to module source

**Dataclasses:**
- Preferred over namedtuple for struct-like data: `StrategySpec`, `ExitSpec`
- Fields documented inline: `id: str`
- Use `field(default_factory=...)` for mutable defaults

**Examples:**
```python
@dataclass
class ExitSpec:
    stop: dict = field(default_factory=lambda: {"kind": "atr", "mult": 2.0})
    target: dict = field(default_factory=lambda: {"kind": "rr", "v": 2.0})

@dataclass
class StrategySpec:
    id: str
    name: str
    thesis: str
    # ... field with default
    generation: int = 0
```

## Code Layout Examples

**Typical class structure:**
```python
class Analyst:
    def __init__(self, journal: Journal, cfg: dict, feed=None):
        self.journal = journal
        self.cfg = cfg
        self._frames: dict = {}

    # ── data loading ─────────────────────────────────────────────
    def frames(self, tf: str, extra: tuple = ()) -> dict:
        """Load OHLCV frames for one timeframe, cached."""
        # ... implementation

    # ── selection gate ───────────────────────────────────────────
    def evaluate(self, spec: StrategySpec) -> tuple[bool, dict]:
        """Judge a spec on recent evidence."""
        # ... implementation
```

---

*Convention analysis: 2026-09-03*
