"""DeepSeek client — thin, budget-guarded, provider-swappable."""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

from ..core.config import Env, ROOT

log = logging.getLogger(__name__)


class BrainLLM:
    def __init__(self, cfg: dict):
        b = cfg["brain"]
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
        return bool(self._key)

    def _usage(self) -> dict:
        try:
            return json.loads(self._usage_path.read_text())
        except Exception:
            return {}

    @staticmethod
    def _today() -> str:
        return datetime.now(timezone.utc).strftime("%Y-%m-%d")

    def _tokens_today(self) -> int:
        """Every token spent today, tagged or not."""
        return int(self._usage().get(self._today(), 0))

    def _purpose_today(self) -> dict[str, int]:
        return dict((self._usage().get("_by_purpose") or {})
                    .get(self._today()) or {})

    def _spend(self, tokens: int, purpose: str = "misc") -> None:
        try:
            data, today = self._usage(), self._today()
            data[today] = int(data.get(today, 0)) + tokens
            bp = data.setdefault("_by_purpose", {}).setdefault(today, {})
            bp[purpose] = int(bp.get(purpose, 0)) + tokens
            self._usage_path.write_text(json.dumps(data))
        except Exception:
            pass

    def _cap(self, deep: bool) -> int:
        """Token cap for one call. Reasoner models bill chain-of-thought
        against max_tokens, so deep calls get a much larger ceiling."""
        return self.max_tokens_deep if deep else self.max_tokens

    def budget_left(self, purpose: str = "misc") -> int:
        total_left = max(0, self.daily_budget - self._tokens_today())
        used = self._purpose_today()
        if purpose in self.purpose_budgets:
            own = self.purpose_budgets[purpose] - used.get(purpose, 0)
        else:
            shared = self.daily_budget - sum(self.purpose_budgets.values())
            own = shared - sum(v for k, v in used.items()
                               if k not in self.purpose_budgets)
        return max(0, min(total_left, own))

    def chat(self, prompt: str, deep: bool = False,
             json_mode: bool = False, purpose: str = "misc") -> str | None:
        if not self.available or self.budget_left(purpose) <= 0:
            return None
        try:
            from openai import OpenAI
            if self._client is None:
                self._client = OpenAI(api_key=self._key,
                                      base_url=self._base_url)
            use_json = json_mode and not deep
            resp = self._client.chat.completions.create(
                model=self.model_deep if deep else self.model_fast,
                messages=[{"role": "user", "content": prompt}],
                max_tokens=self._cap(deep),
                response_format={"type": "json_object"} if use_json else None,
                timeout=120)
            choice = resp.choices[0]
            text = choice.message.content or ""
            used = getattr(resp.usage, "total_tokens", 0) or 0
            self._spend(used, purpose)
            log.info(f"brain call [{purpose}]: {used} tokens "
                     f"(budget left {self.budget_left(purpose)})")
            if not text:
                # reasoner models spend max_tokens on chain-of-thought before
                # emitting content; a "length" stop leaves content empty
                log.warning(
                    f"brain call returned empty content "
                    f"(finish_reason={choice.finish_reason}, cap="
                    f"{self._cap(deep)}, {used} tokens billed) — raise "
                    f"brain.max_tokens{'_deep' if deep else '_per_call'}")
                return None
            return text
        except Exception as e:
            log.warning(f"brain call failed: {e}")
            return None

    def chat_tools(self, messages: list[dict], tools: list[dict],
                   deep: bool = False, purpose: str = "misc"):
        """OpenAI-compatible tool-calling. Returns the assistant message
        object (.content, .tool_calls) or None on no-budget/error."""
        if not self.available or self.budget_left(purpose) <= 0:
            return None
        try:
            from openai import OpenAI
            if self._client is None:
                self._client = OpenAI(api_key=self._key,
                                      base_url=self._base_url)
            resp = self._client.chat.completions.create(
                model=self.model_deep if deep else self.model_fast,
                messages=messages,
                tools=tools,
                max_tokens=self._cap(deep),
                timeout=120)
            used = getattr(resp.usage, "total_tokens", 0) or 0
            self._spend(used, purpose)
            return resp.choices[0].message
        except Exception as e:
            log.warning(f"brain tool call failed: {e}")
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
