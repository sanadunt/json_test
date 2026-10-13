"""Optional Claude trade reviewer.

Claude sees the proposed entry and the indicator context and returns a structured verdict:
approve, reduce (with a size multiplier in [0, 1]) or veto. The reviewer can only make a trade
smaller or skip it. It cannot increase size, change the stop, or open trades on its own.
Risk limits in risk.py run before and after this step regardless of the verdict.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, asdict

SYSTEM_PROMPT = """You are a risk reviewer for a rule-based, long-only spot crypto paper-trading bot.
A deterministic strategy has proposed an entry. Your job is to decide whether this specific entry
should be taken as proposed, taken at reduced size, or skipped.

You are not asked to predict price. Judge the quality of the setup relative to the rules:
trend alignment, volatility (ATR as % of price), whether RSI suggests a chase, how the stop
distance compares with recent volatility, recent trade outcomes, and current drawdown.

Be conservative. Vetoing a marginal setup costs little; taking a bad one costs real money.
You cannot increase size or move the stop. size_multiplier must be 1.0 for approve,
between 0.1 and 0.9 for reduce, and 0.0 for veto. Keep reasoning under 80 words."""

REVIEW_SCHEMA = {
    "type": "object",
    "properties": {
        "decision": {"type": "string", "enum": ["approve", "reduce", "veto"]},
        "size_multiplier": {"type": "number"},
        "confidence": {"type": "number"},
        "reasoning": {"type": "string"},
    },
    "required": ["decision", "size_multiplier", "confidence", "reasoning"],
    "additionalProperties": False,
}


@dataclass
class Review:
    decision: str  # approve | reduce | veto
    size_multiplier: float
    confidence: float
    reasoning: str
    source: str  # "claude" | "fallback"

    def to_dict(self) -> dict:
        return asdict(self)


def normalize(decision: str, size_multiplier: float, confidence: float, reasoning: str,
              source: str = "claude") -> Review:
    """Clamp the model's output to what the reviewer is allowed to do."""
    decision = decision if decision in {"approve", "reduce", "veto"} else "veto"
    m = float(size_multiplier)
    if decision == "approve":
        m = 1.0
    elif decision == "veto":
        m = 0.0
    else:
        m = min(0.9, max(0.1, m))
    conf = min(1.0, max(0.0, float(confidence)))
    return Review(decision, m, conf, str(reasoning)[:600], source)


class ClaudeReviewer:
    def __init__(self, model: str = "claude-opus-5-5", mode: str = "advisory", client=None):
        if mode not in {"advisory", "required"}:
            raise ValueError("mode must be advisory or required")
        self.model = model
        self.mode = mode
        self._client = client

    @property
    def client(self):
        if self._client is None:
            import anthropic  # imported lazily so backtests never need the SDK configured

            self._client = anthropic.Anthropic()
        return self._client

    def _fallback(self, why: str) -> Review:
        if self.mode == "required":
            return Review("veto", 0.0, 0.0, f"reviewer unavailable ({why}); required mode vetoes", "fallback")
        return Review("approve", 1.0, 0.0, f"reviewer unavailable ({why}); advisory mode approves", "fallback")

    def review(self, proposal: dict) -> Review:
        try:
            response = self.client.messages.create(
                model=self.model,
                max_tokens=2000,
                system=SYSTEM_PROMPT,
                messages=[{"role": "user", "content": "Proposed entry:\n" + json.dumps(proposal, indent=2, default=str)}],
                output_config={"format": {"type": "json_schema", "schema": REVIEW_SCHEMA}},
            )
        except Exception as e:  # network, auth, rate limit: never crash the trading loop
            return self._fallback(f"{type(e).__name__}: {e}"[:200])

        if response.stop_reason == "refusal":
            return Review("veto", 0.0, 0.0, "model declined to review; treated as veto", "claude")
        text = next((b.text for b in response.content if b.type == "text"), None)
        if not text:
            return self._fallback("empty response")
        try:
            data = json.loads(text)
            return normalize(data["decision"], data["size_multiplier"], data["confidence"], data["reasoning"])
        except (ValueError, KeyError, TypeError) as e:
            return self._fallback(f"unparseable response: {e}")
