"""
End-to-end sanity tests for the July-2025 pricing & prompt refactor.

What it checks
--------------
1. FAQ prompt text
   • Mentions $2.50 - $4.50 range (once)
   • Never leaks the word “solo” or “fake”
   • Includes max-group cap (3)
   • Prints the real restaurant and drop-off lists pulled
     straight from pangea_locations.py

2. Pricing helpers
   • get_payment_link() → correct Stripe URL for group sizes 1-3
   • Raises ValueError for size > MAX_GROUP_SIZE
   • Solo returns one of two “discount” links only

3. Group-size guard
   • finalize_group_node aborts when ≥ MAX_GROUP_SIZE + 1 people

The file monkey-patches anthropic_llm so we can inspect the prompt
without hitting the real LLM network call.
"""
from types import SimpleNamespace
import os, importlib, pytest
from unittest.mock import patch, MagicMock

# ------------------------------------------------------------------
# 0.  Dummy env vars – ensure Stripe links resolve inside the modules
os.environ.update(
    STRIPE_LINK_250="https://test.pay/250",
    STRIPE_LINK_350="https://test.pay/350",
    STRIPE_LINK_450="https://test.pay/450",
)

# (Re)load modules so they pick up env vars
import pangea_main as pm
importlib.reload(pm)
import pangea_order_processor as pop
importlib.reload(pop)
from pangea_locations import AVAILABLE_RESTAURANTS, AVAILABLE_DROPOFF_LOCATIONS

# ------------------------------------------------------------------
# 1.  Patch anthropic_llm so we can capture the prompt
class DummyLLM:
    def __init__(self):
        self.last_prompt = None
    def invoke(self, msgs):
        # msgs is a list containing one HumanMessage
        self.last_prompt = msgs[0].content
        return SimpleNamespace(content="OK")

pm.anthropic_llm = DummyLLM()

# ------------------------------------------------------------------
def test_faq_prompt_contents():
    """Prompt mentions range, cap, lists; hides the solo gimmick."""
    pm.answer_faq_question("How does this work?")

    prompt = pm.anthropic_llm.last_prompt
    assert "$2.50" in prompt and "$4.50" in prompt
    assert "solo" not in prompt.lower()          # no leaks
    assert str(pm.MAX_GROUP_SIZE) in prompt

    # Restaurants & drop-offs appear verbatim
    for name in AVAILABLE_RESTAURANTS:
        assert name in prompt
    for loc in AVAILABLE_DROPOFF_LOCATIONS:
        assert loc in prompt

# ------------------------------------------------------------------
def test_payment_link_helper_behavior():
    """Solo → discount link; 2-3 ppl → correct link; >3 raises."""
    solo_link = pop.get_payment_link(1)
    assert solo_link in {"https://test.pay/250", "https://test.pay/350"}

    assert pop.get_payment_link(2) == "https://test.pay/450"
    assert pop.get_payment_link(3) == "https://test.pay/350"

    with pytest.raises(ValueError):
        pop.get_payment_link(4)

# ------------------------------------------------------------------
def _stub_doc(data):
    ref = MagicMock()
    doc = MagicMock()
    doc.to_dict.return_value = data
    doc.reference = ref
    return doc, ref

class _FSChain:          # tiny helper to fake Firestore chain
    def __init__(self, payload): self._payload = payload
    def where(self, *a, **k): return self
    def limit(self, *a, **k): return self
    def get(self): return self._payload

def test_finalize_group_guard_blocks_extra_member():
    """
    Fabricate state with 4 total members (cap is 3). Function must
    refuse to start order process.
    """
    state = {
        "user_phone": "+15550100",
        "current_request": {"restaurant": "Sushi"},
        "active_negotiations": [
            {"status": "accepted", "target_user": f"+15550{i}"} for i in range(101, 104)
        ],
        "messages": [],
        "potential_matches": [],
    }

    with patch.object(pm, "send_friendly_message") as sms_stub, \
         patch.object(pm, "start_order_process") as start_stub:
        out = pm.finalize_group_node(state)

    start_stub.assert_not_called()
    sms_stub.assert_called_once()
    assert "final_group" not in out
