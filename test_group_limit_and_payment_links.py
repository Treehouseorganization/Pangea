# test_group_limit_and_payment_links.py
import os
import importlib
from unittest.mock import patch, MagicMock

# ---------------------------------------------------------------------
# 0.   Environment prep – dummy Stripe URLs for the three tiers
os.environ["STRIPE_LINK_250"] = "https://test.pay/250"
os.environ["STRIPE_LINK_350"] = "https://test.pay/350"
os.environ["STRIPE_LINK_450"] = "https://test.pay/450"

# ---------------------------------------------------------------------
# 1.   Reload the app modules so they pick up the new env vars
import pangea_main as pm
importlib.reload(pm)                     # in case code was edited during dev
import pangea_order_processor as pop
importlib.reload(pop)

# ---------------------------------------------------------------------
def test_payment_link_helper():
    """Solo, 2-person, 3-person ⇒ correct Stripe link; size>3 ⇒ error."""
    # solo (fake-match): should return one of the two “discount” links
    assert pop.get_payment_link(1) in (
        "https://test.pay/250",
        "https://test.pay/350",
    )
    # 2-person group
    assert pop.get_payment_link(2) == "https://test.pay/450"
    # 3-person group
    assert pop.get_payment_link(3) == "https://test.pay/350"
    # Any size > 3 must raise
    import pytest
    with pytest.raises(ValueError):
        pop.get_payment_link(4)

# ---------------------------------------------------------------------
#   Helper builders for Firestore stubs -------------------------------
def _stub_doc(data):
    """Return MagicMocks that behave like a Firestore doc + reference."""
    ref = MagicMock()
    doc = MagicMock()
    doc.to_dict.return_value = data
    doc.reference = ref
    return doc, ref

def _stub_firestore_chain(return_list):
    """Chain that satisfies .where().limit().get() → return_list."""
    class Chain:
        def __init__(self, payload): self._payload = payload
        def where(self, *a, **k): return self
        def limit(self, *a, **k): return self
        def get(self): return self._payload
    return Chain(return_list)

# ---------------------------------------------------------------------
def test_yes_node_group_full_gate():
    """
    When 3 users are already accepted, the 4th gets a “group filled” SMS
    and the pending negotiation is marked declined_full.
    """
    requester_phone = "+15550100"
    fourth_user     = "+15550104"
    negotiation_id  = "neg-123"

    # 3 already-accepted docs (group is full)
    accepted_docs = [_stub_doc({"dummy": i})[0] for i in range(3)]

    # pending negotiation for user #4
    pending_data = {
        "negotiation_id": negotiation_id,
        "from_user": requester_phone,
        "to_user": fourth_user,
        "status": "pending",
        "proposal": {"restaurant": "Thai Garden"},
    }
    pending_doc, pending_ref = _stub_doc(pending_data)

    sent_sms = {}
    def fake_send(to, body, **_):
        sent_sms["to"], sent_sms["body"] = to, body

    with patch.object(pm, "db") as fake_db, \
         patch.object(pm, "send_friendly_message", side_effect=fake_send):

        fake_db.collection.side_effect = [
            _stub_firestore_chain([pending_doc]),    # pending query
            _stub_firestore_chain(accepted_docs),    # accepted query
        ]

        state = {"user_phone": fourth_user, "messages": []}
        out_state = pm.handle_group_response_yes_node(state)

        # Assertions -----------------------------------------------
        assert "filled up" in sent_sms["body"]
        pending_ref.update.assert_called_once_with({"status": "declined_full"})
        assert any(
            "Group response YES rejected" in m.content
            for m in out_state["messages"]
        )

# ---------------------------------------------------------------------
def test_finalize_group_guard_blocks_oversize():
    """
    If somehow 4 members slip through (cap is 3), finalize_group_node aborts
    before the order processor is called.
    """
    # fabricate state with 3 accepted + requester = 4 total
    state = {
        "user_phone": "+15550200",
        "current_request": {"restaurant": "Sushi"},
        "active_negotiations": [
            {"status": "accepted", "target_user": f"+15550{i}"} for i in range(201, 204)
        ],
        "messages": [],
        "potential_matches": [],
    }

    with patch.object(pm, "send_friendly_message") as fake_sms, \
         patch.object(pm, "start_order_process") as fake_start:

        out = pm.finalize_group_node(state)

    # Guard triggered: no order process, friendly error SMS sent once
    fake_start.assert_not_called()
    fake_sms.assert_called_once()
    # state returned unchanged and without a final group entry
    assert out is state and "final_group" not in out
