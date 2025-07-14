# test_group_limit_and_payment_links.py
import os, random, types, builtins
from unittest.mock import patch, MagicMock, call
import importlib

# ---------------------------------------------------------------------
# 0.   Environment prep – use dummy Stripe URLs
os.environ["STRIPE_PAYMENT_LINK_2_PEOPLE"] = "https://test.pay/2"
os.environ["STRIPE_PAYMENT_LINK_3_PEOPLE"] = "https://test.pay/3"
os.environ["STRIPE_PAYMENT_LINK_4_PEOPLE"] = "https://test.pay/4"

# (re-import so pangea_main / order_processor pick up env vars)
import pangea_main as pm
importlib.reload(pm)                    # in case tests run after code edits
import pangea_order_processor as pop
importlib.reload(pop)

# ---------------------------------------------------------------------
def test_payment_link_helper():
    """Task-2 & 8: helper returns correct links."""
    assert pm.get_payment_link_for_group(["u"]) in (
        "https://test.pay/2", "https://test.pay/3"
    )
    assert pm.get_payment_link_for_group(["a", "b"]) == "https://test.pay/2"
    assert pm.get_payment_link_for_group(["a", "b", "c"]) == "https://test.pay/3"
    assert pm.get_payment_link_for_group(["a", "b", "c", "d"]) == "https://test.pay/4"

# ---------------------------------------------------------------------
def make_stub_doc(data):
    """Return a stub Firestore doc with .to_dict() & .reference.update()."""
    ref = MagicMock()
    doc = MagicMock()
    doc.to_dict.return_value = data
    doc.reference = ref
    return doc, ref

def stub_firestore_chain(return_list):
    """Return an object whose .where().where().get() => return_list."""
    class Chain:
        def __init__(self, payload): self._payload = payload
        def where(self, *a, **k): return self
        def limit(self, *a, **k): return self
        def get(self): return self._payload
    return Chain(return_list)

# ---------------------------------------------------------------------
def test_yes_node_group_full_gate():
    """User #5 gets 'group filled' reply and is NOT accepted."""
    requester_phone = "+15550100"
    fifth_user      = "+15550105"
    negotiation_id  = "neg-123"

    # 3 already-accepted docs (other_accepted)
    accepted_docs = [make_stub_doc({"dummy": i})[0] for i in range(3)]

    # pending negotiation for user #5
    pending_data = {
        "negotiation_id": negotiation_id,
        "from_user": requester_phone,
        "to_user": fifth_user,
        "status": "pending",
        "proposal": {"restaurant": "Thai Garden"},
    }
    pending_doc, pending_ref = make_stub_doc(pending_data)

    sent_sms = {}
    def fake_send(to, body, **k):
        sent_sms["to"], sent_sms["body"] = to, body

    with patch.object(pm, "db") as fake_db, \
     patch.object(pm, "send_friendly_message", side_effect=fake_send):

        pending_chain  = stub_firestore_chain([pending_doc])
        accepted_chain = stub_firestore_chain(accepted_docs)
        fake_db.collection.side_effect = [pending_chain, accepted_chain]

        # run the node
        state = {"user_phone": fifth_user, "messages": []}
        out_state = pm.handle_group_response_yes_node(state)


        # Assert sms sent & negotiation marked declined_full
        assert "filled up" in sent_sms["body"]
        pending_ref.update.assert_called_once_with({'status': 'declined_full'})
        assert any("Group response YES rejected" in m.content for m in out_state["messages"])

# ---------------------------------------------------------------------
def test_finalize_group_guard_blocks_oversize():
    """If somehow 5 members slip through, node aborts before order process."""
    # fabricate state with 4 accepted + requester = 5 total
    state = {
        "user_phone": "+15550200",
        "current_request": {"restaurant": "Sushi"},
        "active_negotiations": [
            {"status": "accepted", "target_user": f"+15550{i}"} for i in range(201, 205)
        ],
        "messages": [],
        "potential_matches": [],
    }
    # stub send_friendly_message & start_order_process
    with patch.object(pm, "send_friendly_message") as fake_sms, \
         patch.object(pm, "start_order_process") as fake_start:
        out = pm.finalize_group_node(state)

    # guard triggered → order process never called
    fake_start.assert_not_called()
    fake_sms.assert_called_once()
    assert out is state and 'final_group' not in out

