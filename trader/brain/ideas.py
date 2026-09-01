"""The Researcher -> Strategist queue — the handoff that was never wired.

The two halves of the brain ran past each other. The Researcher (harvester +
crawler) scraped 400+ ideas and mined hundreds of passages per cycle, then
threw the *text* away: `harvest_idea` recorded a 120-char title and a source
string, nothing more. Meanwhile `kernel._mechanism_once` called
`SpecWriter.write()` with no `idea=` at all, so DeepSeek invented strategies
from nothing every three hours. Everything the crawler read was dead weight.

This module is the pipe between them. Ideas are persisted whole — the text is
the part that holds the mechanism — and the Strategist draws the best
unconsumed one before it writes.

Storage is `brain_events`, not a new table: the harvester already dedupes on
`kind='harvest_idea' AND subject=<idea_id>`, and consumption is a separate
`idea_consumed` row keyed by the SAME subject. That second point matters —
the obvious dedup ("skip ideas that appear in spec_admitted/spec_rejected")
cannot work, because those rows are keyed by SPEC id, so no idea would ever
match and every idea would be retried forever.
"""
from __future__ import annotations

import html as _html
import json
import logging
import re

log = logging.getLogger(__name__)

KIND = "harvest_idea"
CONSUMED = "idea_consumed"

STRATEGY = "strategy"
RESEARCH = "research"
STREAMS = (STRATEGY, RESEARCH)
DEFAULT_STREAM = STRATEGY      # rows written before streams existed

MAX_TEXT = 4000          # a spec prompt reads ~900; keep room for the vault
MIN_TEXT = 80            # below this there is no mechanism to express
MAX_AGE_DAYS = 45        # a stale idea describes a market that has moved on
POOL = 60                # rank this many recent unconsumed ideas

# Cheap systematic-vs-hype screen, shared in spirit with `harvester.idea_score`
# but scored over the FULL text rather than the title: the queue exists to
# find the passages that actually describe a rule.
_TERMS = re.compile(
    r"\b(entry|exit|stop[- ]?loss|take[- ]?profit|backtest|sharpe|drawdown|"
    r"mean[- ]?revers\w*|momentum|breakout|divergence|funding|open interest|"
    r"basis|liquidat\w+|order flow|imbalance|volatility|z[- ]?score|"
    r"rsi|atr|ema|sma|vwap|adx|bollinger|donchian|percentile|quantile|"
    r"regime|filter|threshold|rebalanc\w+|signal|edge|alpha)\b", re.I)
_HYPE = re.compile(
    r"\b(moon|100x|pump|guaranteed|financial advice|not advice|"
    r"join (my|our)|telegram group|dm me|giveaway)\b", re.I)
_RULEY = re.compile(r"\b(if|when|while)\b.{0,60}\b(then|enter|buy|sell|"
                    r"long|short|close)\b", re.I)


def score(text: str, title: str = "") -> float:
    """How likely this passage describes an expressible mechanism.

    Term density over length, not a raw count — otherwise a long news article
    that says "volatility" twice beats a four-line rule statement.
    """
    hay = f"{title} {text}"
    if len(text) < MIN_TEXT:
        return 0.0
    hits = len(_TERMS.findall(hay))
    density = hits / max(len(hay) / 400.0, 1.0)
    s = hits + 3.0 * density
    if _RULEY.search(hay):
        s += 4.0                       # states a condition and an action
    if _HYPE.search(hay):
        s -= 8.0
    return round(s, 3)


_TAG = re.compile(r"<[^>]{0,400}>")
_SCRIPT = re.compile(r"<(script|style)\b.*?</\1>", re.I | re.S)


def clean(text: str) -> str:
    """RSS descriptions and crawled passages arrive as raw HTML.

    Markup is tokens spent on nothing and it blurs the sentence the
    Strategist is meant to read, so strip it once here rather than hoping
    the model looks past it.
    """
    t = _SCRIPT.sub(" ", text or "")
    t = _TAG.sub(" ", t)
    t = _html.unescape(t)
    return re.sub(r"\s+", " ", t).strip()


def _row_to_idea(subject: str, detail: str) -> dict | None:
    try:
        d = json.loads(detail)
    except Exception:
        return None
    if not isinstance(d, dict):
        return None
    streams = d.get("streams") or \
        ([d["stream"]] if d.get("stream") else [DEFAULT_STREAM])
    return {"idea_id": subject,
            "title": d.get("title", ""),
            "text": d.get("text", ""),
            "source": d.get("source", ""),
            "url": d.get("url", ""),
            "score": float(d.get("score", 0.0) or 0.0),
            "streams": streams}


def record(journal, idea: dict, streams=(DEFAULT_STREAM,)) -> bool:
    """Persist one researched idea, whole. Returns True if the queue gained
    something — a new idea, or text for one that was recorded without any.

    `idea` is the harvester/crawler shape: idea_id, title, text, source, and
    either `url` or `link`.

    `streams` can be a tuple/list of stream names, or a bare string naming a
    single stream. A bare string is accepted and treated as a one-element tuple.

    An idea is one idea; it just serves 1+ consumers, so the streams it
    qualifies for live on the single row as an attribute rather than one row
    per stream — keying rows by `f"{idea_id}#{stream}"` would store the text
    (up to MAX_TEXT chars) once per stream and break `_already_processed`,
    which matches on `idea_id` alone.

    The upgrade path is not cosmetic. The old harvester wrote 416 rows
    carrying a title and a source and nothing else, and it treats any such
    row as "already processed" — so without this those ideas would sit in
    the journal forever, marked done, having never been read by anything.
    """
    iid = (idea.get("idea_id") or "").strip()
    if not iid:
        return False
    text = clean(idea.get("text") or "")[:MAX_TEXT]
    title = clean(idea.get("title") or "")[:200]

    if journal.query("SELECT 1 FROM brain_events WHERE kind=? AND subject=? "
                     "LIMIT 1", (CONSUMED, iid)):
        return False                   # spent; text arriving late changes nothing
    prior = journal.query(
        "SELECT detail FROM brain_events WHERE kind=? AND subject=? "
        "ORDER BY ts DESC LIMIT 1", (KIND, iid))
    if prior:
        had = _row_to_idea(iid, prior[0]["detail"]) or {}
        if len(had.get("text", "")) >= MIN_TEXT or len(text) < MIN_TEXT:
            return False               # already usable, or nothing to add

    # Accept bare string as single stream
    if isinstance(streams, str):
        streams = (streams,)

    clean_streams = sorted(set(s for s in streams if s in STREAMS)) \
        or [DEFAULT_STREAM]
    journal.log_brain_event(KIND, iid, {
        "title": title,
        "text": text,
        "source": (idea.get("source") or "")[:200],
        "url": (idea.get("url") or idea.get("link") or "")[:400],
        "streams": clean_streams,
        "score": score(text, title)})
    return True


def mark_consumed(journal, idea_id: str, outcome: str,
                  stream: str | None = None,
                  spec_id: str = "", detail: dict | None = None) -> None:
    """Record that a consumer has spent this idea on a specific stream.

    Called for EVERY outcome, admitted or not. An idea that produced a spec
    the Analyst refused is still spent — retrying it every three hours would
    burn the same tokens on the same dud forever.

    `stream` identifies which consumer (strategy or research) consumed it.
    If None (legacy), treat it as consuming ALL streams for backwards compat.
    """
    d = {"outcome": outcome, "spec": spec_id, **(detail or {})}
    if stream is not None:
        d["stream"] = stream
    journal.log_brain_event(CONSUMED, idea_id, d)


def pending(journal, max_age_days: float = MAX_AGE_DAYS,
            limit: int = POOL, stream: str | None = None) -> list[dict]:
    """Unconsumed ideas that carry enough text to be worth a prompt,
    best material first.

    The SQL window is fixed at POOL and NOT scaled to `limit`: recency
    orders the fetch but score orders the answer, so a window sized to the
    caller's appetite would rank one idea out of the newest one or two. The
    live journal's newest rows are whatever an RSS feed published minutes
    ago — often unrelated tech news — while the usable material sits behind
    them.

    An idea is excluded if it was consumed FOR this stream. Legacy consumption
    rows (no stream key) suppress the idea for ALL streams.
    """
    rows = journal.query(
        "SELECT subject, detail FROM brain_events e WHERE e.kind=? "
        "AND e.ts >= datetime('now', ?) "
        "ORDER BY e.ts DESC LIMIT ?",
        (KIND, f"-{float(max_age_days)} days", POOL * 6))
    seen: set[str] = set()             # an upgraded idea has two rows;
    out: list[dict] = []               # ts DESC means the newest one wins
    for r in rows:
        if r["subject"] in seen:
            continue
        seen.add(r["subject"])
        idea = _row_to_idea(r["subject"], r["detail"])
        if idea and len(idea["text"]) >= MIN_TEXT and idea["score"] > 0:
            # Check if this idea was consumed for this stream
            if _is_consumed_for_stream(journal, r["subject"], stream):
                continue
            if stream is not None and stream not in idea["streams"]:
                continue
            out.append(idea)
    out.sort(key=lambda i: i["score"], reverse=True)
    return out[:limit]


def _is_consumed_for_stream(journal, idea_id: str, stream: str | None) -> bool:
    """Check if an idea was consumed for a specific stream.

    Returns True if:
    - There's a consumption row with no stream key (legacy: consumed for ALL), or
    - There's a consumption row with stream=stream (consumed for this stream), or
    - stream is None and there are ANY consumption rows (filter by nothing = consumed).

    Returns False if there are no consumption rows, or if the consumption rows
    all specify a different stream.
    """
    rows = journal.query(
        "SELECT detail FROM brain_events WHERE kind=? AND subject=?",
        (CONSUMED, idea_id))

    if not rows:
        return False                   # not consumed at all

    for r in rows:
        try:
            d = json.loads(r["detail"])
        except Exception:
            continue
        consumed_stream = d.get("stream")

        if consumed_stream is None:
            # Legacy row with no stream: suppress for ALL streams
            return True

        if stream is None:
            # Caller didn't specify a stream, any consumption suppresses
            return True

        if consumed_stream == stream:
            # Consumed for this specific stream
            return True

    return False


def next_idea(journal, max_age_days: float = MAX_AGE_DAYS,
              stream: str | None = None) -> dict | None:
    """The single best unconsumed idea, or None when the queue is dry.

    None is not an error: the Strategist then invents unprompted, which is
    the old behaviour.
    """
    q = pending(journal, max_age_days=max_age_days, limit=1, stream=stream)
    return q[0] if q else None
