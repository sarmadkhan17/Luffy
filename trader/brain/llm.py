"""DeepSeek client — thin, budget-guarded, provider-swappable."""
from __future__ import annotations

import json
import fcntl
import os
import tempfile
import uuid
from contextlib import contextmanager
import logging
from datetime import datetime, timezone

from ..core.config import Env, ROOT

log = logging.getLogger(__name__)


class BrainLLM:
    def __init__(self, cfg: dict):
        b = cfg["brain"]
        self._brain_config = b
        self.model_fast = b["model_fast"]
        self.model_deep = b["model_deep"]
        self.max_tokens = int(b["max_tokens_per_call"])
        self.max_tokens_deep = int(b.get("max_tokens_deep",
                                         max(16000, self.max_tokens)))
        self.daily_budget = int(b["daily_token_budget"])
        #: purpose -> reserved daily cap. A listed purpose spends only its
        #: own slice; unlisted purposes share what is left of daily_budget
        #: after every reservation, so no consumer can eat another's slice.
        self.purpose_budgets: dict[str, int] = {
            k: int(v) for k, v in (b.get("purpose_budgets") or {}).items()}
        self._usage_path = ROOT / "data" / "brain_usage.json"
        self._base_url = b.get("base_url", "https://api.deepseek.com")
        self._key = Env.deepseek_key()
        self._client = None

    @property
    def available(self) -> bool:
        return self._brain_config.get("enabled", True) is True and bool(self._key)

    def _disabled(self, purpose: str) -> bool:
        if self._brain_config.get("enabled", True) is True:
            return False
        log.warning("provider admission refused: brain_disabled", extra={
            "event": "provider_admission_refused", "reason_code": "brain_disabled",
            "purpose": purpose, "provider_attempts": 0})
        return True

    def _usage(self) -> dict:
        try:
            data = json.loads(self._usage_path.read_text())
        except FileNotFoundError:
            return {}
        if not isinstance(data, dict):
            raise ValueError("invalid_token_ledger")
        return data

    @contextmanager
    def _locked_usage(self):
        self._usage_path.parent.mkdir(parents=True, exist_ok=True)
        with self._usage_path.with_suffix(self._usage_path.suffix + ".lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            try:
                yield self._usage()
            finally:
                fcntl.flock(lock, fcntl.LOCK_UN)

    def _write_usage(self, data):
        fd, name = tempfile.mkstemp(prefix=".brain-usage-", dir=self._usage_path.parent)
        try:
            with os.fdopen(fd, "w") as stream:
                json.dump(data, stream)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(name, self._usage_path)
            directory = os.open(self._usage_path.parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        finally:
            if os.path.exists(name):
                os.unlink(name)

    @staticmethod
    def _today() -> str:
        return datetime.now(timezone.utc).strftime("%Y-%m-%d")

    @staticmethod
    def _count(value):
        if type(value) is not int or value < 0:
            raise ValueError("invalid_token_count")
        return value

    def _tokens_today(self) -> int:
        return self._count(self._usage().get(self._today(), 0))

    def _purpose_today(self) -> dict[str, int]:
        return dict((self._usage().get("_by_purpose") or {}).get(self._today()) or {})

    def _spend(self, tokens: int, purpose: str = "misc") -> None:
        with self._locked_usage() as data:
            self._record_spend(data, self._today(), self._count(tokens), purpose)
            self._write_usage(data)

    def _record_spend(self, data, day, tokens, purpose):
        data[day] = self._count(data.get(day, 0)) + tokens
        bp = data.setdefault("_by_purpose", {}).setdefault(day, {})
        bp[purpose] = self._count(bp.get(purpose, 0)) + tokens

    def _cap(self, deep: bool) -> int:
        return self.max_tokens_deep if deep else self.max_tokens

    def _remaining(self, data, purpose, day):
        used = dict((data.get("_by_purpose") or {}).get(day) or {})
        used = {k: self._count(v) for k, v in used.items()}
        total = self._count(data.get(day, 0))
        for reservation in data.get("_reservations", {}).values():
            tokens = self._count(reservation["tokens"])
            if reservation["day"] == day:
                total += tokens
                tag = reservation["purpose"]
                used[tag] = used.get(tag, 0) + tokens
        if purpose in self.purpose_budgets:
            own = self.purpose_budgets[purpose] - used.get(purpose, 0)
        else:
            shared = self.daily_budget - sum(self.purpose_budgets.values())
            own = shared - sum(v for k, v in used.items() if k not in self.purpose_budgets)
        return max(0, min(self.daily_budget - total, own))

    def budget_left(self, purpose: str = "misc") -> int:
        try:
            with self._locked_usage() as data:
                return self._remaining(data, purpose, self._today())
        except Exception:
            return 0  # An unreadable ledger is never an empty budget ledger.

    def _admit(self, request, deep, purpose):
        if self._disabled(purpose):
            return None
        # Reserve input as UTF-8 request bytes (a conservative tokenizer-independent
        # estimate) plus the admitted output ceiling. Actual billed usage remains
        # authoritative at settlement, including unexpected provider overages.
        input_allowance = len(json.dumps(request, ensure_ascii=False).encode("utf-8"))
        with self._locked_usage() as data:
            day = self._today()
            available = self._remaining(data, purpose, day)
            output = min(self._cap(deep), available - input_allowance)
            if output <= 0:
                return None
            identity = uuid.uuid4().hex
            data.setdefault("_reservations", {})[identity] = {
                "day": day, "purpose": purpose, "tokens": input_allowance + output}
            self._write_usage(data)
            return identity, output

    def _settle(self, identity, actual):
        actual = self._count(actual)
        with self._locked_usage() as data:
            reservation = data["_reservations"].pop(identity)
            self._record_spend(data, reservation["day"], actual, reservation["purpose"])
            self._write_usage(data)

    def _call(self, request, deep, purpose):
        if self._disabled(purpose) or not self.available or self.budget_left(purpose) <= 0:
            return None
        try:
            admitted = self._admit(request, deep, purpose)
            if admitted is None:
                return None
            identity, output = admitted
            if self._client is None:
                from openai import OpenAI
                # Every provider attempt needs its own durable admission.
                self._client = OpenAI(api_key=self._key, base_url=self._base_url, max_retries=0)
            response = self._client.chat.completions.create(**request, max_tokens=output, timeout=120)
            actual = getattr(getattr(response, "usage", None), "total_tokens", None)
            if actual is not None:
                self._settle(identity, actual)
            else:
                log.warning("brain call has unknown usage; reservation retained")
            return response
        except Exception as error:
            # A lost response can still have incurred usage. Keep its reservation
            # across retries/restarts rather than recording invented zero spend.
            log.warning("brain call failed; reservation retained: %s", type(error).__name__)
            return None

    def chat(self, prompt: str, deep: bool = False,
             json_mode: bool = False, purpose: str = "misc") -> str | None:
        response = self._call(dict(model=self.model_deep if deep else self.model_fast,
            messages=[{"role": "user", "content": prompt}],
            response_format={"type": "json_object"} if json_mode and not deep else None), deep, purpose)
        if response is None:
            return None
        try:
            choice = response.choices[0]
            text = choice.message.content or ""
            if not text:
                log.warning("brain call returned empty content (finish_reason=%s, cap=%s)",
                            choice.finish_reason, self._cap(deep))
                return None
            return text
        except Exception:
            return None

    def chat_tools(self, messages: list[dict], tools: list[dict],
                   deep: bool = False, purpose: str = "misc"):
        response = self._call(dict(model=self.model_deep if deep else self.model_fast,
                                   messages=messages, tools=tools), deep, purpose)
        if response is None:
            return None
        try:
            return response.choices[0].message
        except Exception:
            return None

    def chat_json(self, prompt: str, deep: bool = False,
                  purpose: str = "misc") -> dict | None:
        text = self.chat(prompt, deep=deep, json_mode=True, purpose=purpose)
        if not text:
            return None
        cleaned = text
        if "```" in cleaned:                      # strip markdown fences
            parts = cleaned.split("```")
            cleaned = max(parts, key=len)
            if cleaned.startswith("json"):
                cleaned = cleaned[4:]
        try:
            return json.loads(cleaned)
        except Exception:
            pass
        start, end = cleaned.find("{"), cleaned.rfind("}")
        if 0 <= start < end:
            try:
                return json.loads(cleaned[start:end + 1])
            except Exception as e:
                log.warning(f"JSON salvage failed: {e}")
        log.warning(f"brain returned unparseable output "
                    f"({len(text)} chars): {text[:200]!r}")
        return None
