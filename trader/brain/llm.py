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
        self.daily_budget = int(b["daily_token_budget"])
        self._key = Env.deepseek_key()
        self._client = None

    @property
    def available(self) -> bool:
        return bool(self._key)

    def _tokens_today(self) -> int:
        try:
            p = ROOT / "data" / "brain_usage.json"
            data = json.loads(p.read_text())
            today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            return data.get(today, 0)
        except Exception:
            return 0

    def _spend(self, tokens: int) -> None:
        try:
            p = ROOT / "data" / "brain_usage.json"
            data = json.loads(p.read_text()) if p.exists() else {}
            today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            data[today] = data.get(today, 0) + tokens
            p.write_text(json.dumps(data))
        except Exception:
            pass

    def budget_left(self) -> int:
        return max(0, self.daily_budget - self._tokens_today())

    def chat(self, prompt: str, deep: bool = False,
             json_mode: bool = False) -> str | None:
        if not self.available or self.budget_left() <= 0:
            return None
        try:
            from openai import OpenAI
            if self._client is None:
                self._client = OpenAI(api_key=self._key,
                                      base_url="https://api.deepseek.com")
            resp = self._client.chat.completions.create(
                model=self.model_deep if deep else self.model_fast,
                messages=[{"role": "user", "content": prompt}],
                max_tokens=self.max_tokens,
                response_format={"type": "json_object"} if json_mode else None,
                timeout=90)
            text = resp.choices[0].message.content or ""
            used = getattr(resp.usage, "total_tokens", 0) or 0
            self._spend(used)
            log.info(f"brain call: {used} tokens "
                     f"(budget left {self.budget_left()})")
            return text
        except Exception as e:
            log.warning(f"brain call failed: {e}")
            return None

    def chat_json(self, prompt: str, deep: bool = False) -> dict | None:
        text = self.chat(prompt, deep=deep, json_mode=True)
        if not text:
            return None
        try:
            return json.loads(text)
        except Exception:
            # salvage a JSON object from a chatty reply
            start = text.find("{")
            end = text.rfind("}")
            if 0 <= start < end:
                try:
                    return json.loads(text[start:end + 1])
                except Exception:
                    pass
            log.warning("brain returned unparseable JSON")
            return None
