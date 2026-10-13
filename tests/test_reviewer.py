import json
from types import SimpleNamespace

from claudetrader.reviewer import ClaudeReviewer, REVIEW_SCHEMA, normalize


def test_normalize_clamps_to_allowed_range():
    assert normalize("approve", 3.0, 0.5, "x").size_multiplier == 1.0
    assert normalize("veto", 0.7, 0.5, "x").size_multiplier == 0.0
    assert normalize("reduce", 1.5, 0.5, "x").size_multiplier == 0.9
    assert normalize("reduce", 0.0, 0.5, "x").size_multiplier == 0.1
    r = normalize("something-else", 1.0, 7.0, "x" * 1000)
    assert r.decision == "veto" and r.confidence == 1.0 and len(r.reasoning) == 600


class FakeMessages:
    def __init__(self, text=None, stop_reason="end_turn", raise_exc=None):
        self.text, self.stop_reason, self.raise_exc = text, stop_reason, raise_exc
        self.last_kwargs = None

    def create(self, **kwargs):
        self.last_kwargs = kwargs
        if self.raise_exc:
            raise self.raise_exc
        content = [SimpleNamespace(type="text", text=self.text)] if self.text is not None else []
        return SimpleNamespace(stop_reason=self.stop_reason, content=content)


def fake_client(**kw):
    return SimpleNamespace(messages=FakeMessages(**kw))


def test_review_parses_structured_output_and_sends_schema():
    body = json.dumps({"decision": "reduce", "size_multiplier": 0.4, "confidence": 0.7, "reasoning": "chase"})
    client = fake_client(text=body)
    r = ClaudeReviewer(model="claude-opus-5-5", mode="advisory", client=client).review({"side": "buy"})
    assert (r.decision, r.size_multiplier, r.source) == ("reduce", 0.4, "claude")
    kw = client.messages.last_kwargs
    assert kw["model"] == "claude-opus-5-5"
    assert kw["output_config"]["format"]["schema"] == REVIEW_SCHEMA
    assert "Proposed entry" in kw["messages"][0]["content"]


def test_refusal_is_a_veto():
    r = ClaudeReviewer(client=fake_client(text=None, stop_reason="refusal"), mode="advisory").review({})
    assert r.decision == "veto" and r.size_multiplier == 0.0


def test_api_error_falls_through_by_mode():
    err = RuntimeError("boom")
    adv = ClaudeReviewer(client=fake_client(raise_exc=err), mode="advisory").review({})
    req = ClaudeReviewer(client=fake_client(raise_exc=err), mode="required").review({})
    assert adv.decision == "approve" and adv.source == "fallback"
    assert req.decision == "veto" and req.source == "fallback"


def test_garbage_output_falls_through():
    r = ClaudeReviewer(client=fake_client(text="not json"), mode="required").review({})
    assert r.decision == "veto" and r.source == "fallback"
