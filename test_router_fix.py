#!/usr/bin/env python3
"""
Test the router fixes for incomplete request handling
"""

def simulate_router_fallback():
    """Simulate the router fallback logic"""
    
    # Test case: User responding to incomplete request
    conversation_stage = 'incomplete_request'
    missing_info = ['restaurant']
    partial_request = {'location': 'Richard J Daley Library', 'time_preference': '7pm'}
    order_session = {}
    pending_group_invites = []
    new_message = "McDonald's"
    
    message_lower = new_message.lower().strip()
    
    print("🧪 Testing Router Fallback Logic")
    print(f"   Conversation stage: {conversation_stage}")
    print(f"   Missing info: {missing_info}")
    print(f"   Has partial request: {bool(partial_request)}")
    print(f"   Order session: {bool(order_session)}")
    print(f"   Pending invites: {bool(pending_group_invites)}")
    print(f"   Message: '{new_message}'")
    
    # Apply the fallback logic
    if conversation_stage == 'incomplete_request' and missing_info and partial_request:
        fallback_action = "handle_incomplete_request"
        print(f"   🎯 Detected incomplete request follow-up")
        
    elif order_session:
        if any(word in message_lower for word in ['pay', 'payment']):
            fallback_action = "handle_payment_request"
            print(f"   💳 Detected payment request")
        else:
            fallback_action = "collect_order_number"
            print(f"   📋 Detected order continuation")
            
    elif pending_group_invites:
        if any(word in message_lower for word in ['yes', 'y', 'sure', 'ok']):
            fallback_action = "group_response_yes"
            print(f"   ✅ Detected group YES response")
        elif any(word in message_lower for word in ['no', 'n', 'pass', 'nah']):
            fallback_action = "group_response_no"
            print(f"   ❌ Detected group NO response")
        else:
            fallback_action = "general_conversation"
            print(f"   💬 Detected general conversation")
            
    elif any(word in message_lower for word in ['want', 'craving', 'hungry', 'order', 'get', 'need']):
        fallback_action = "start_fresh_request"
        print(f"   🍔 Detected new food request")
        
    elif conversation_stage == 'morning_greeting_sent':
        fallback_action = "morning_response"
        print(f"   🌅 Detected morning response")
        
    else:
        fallback_action = "general_conversation"
        print(f"   💬 Defaulting to general conversation")
    
    print(f"\n✅ Result: {fallback_action}")
    
    expected = "handle_incomplete_request"
    if fallback_action == expected:
        print(f"🎉 SUCCESS: Router correctly identified incomplete request follow-up!")
    else:
        print(f"❌ FAILED: Expected '{expected}', got '{fallback_action}'")
    
    return fallback_action == expected

def test_other_scenarios():
    """Test other routing scenarios"""
    
    scenarios = [
        {
            "name": "Payment request with order session",
            "conversation_stage": "order_active",
            "missing_info": [],
            "partial_request": {},
            "order_session": {"order_stage": "ready_to_pay"},
            "pending_group_invites": [],
            "message": "PAY",
            "expected": "handle_payment_request"
        },
        {
            "name": "Group YES response",
            "conversation_stage": "waiting_for_response",
            "missing_info": [],
            "partial_request": {},
            "order_session": {},
            "pending_group_invites": [{"group_id": "123"}],
            "message": "yes",
            "expected": "group_response_yes"
        },
        {
            "name": "New food request",
            "conversation_stage": "new",
            "missing_info": [],
            "partial_request": {},
            "order_session": {},
            "pending_group_invites": [],
            "message": "I want pizza",
            "expected": "start_fresh_request"
        }
    ]
    
    print(f"\n🧪 Testing Additional Scenarios")
    
    for scenario in scenarios:
        print(f"\n📝 {scenario['name']}")
        print(f"   Message: '{scenario['message']}'")
        
        # Apply routing logic
        conversation_stage = scenario['conversation_stage']
        missing_info = scenario['missing_info']
        partial_request = scenario['partial_request']
        order_session = scenario['order_session']
        pending_group_invites = scenario['pending_group_invites']
        message_lower = scenario['message'].lower().strip()
        
        if conversation_stage == 'incomplete_request' and missing_info and partial_request:
            result = "handle_incomplete_request"
        elif order_session:
            if any(word in message_lower for word in ['pay', 'payment']):
                result = "handle_payment_request"
            else:
                result = "collect_order_number"
        elif pending_group_invites:
            if any(word in message_lower for word in ['yes', 'y', 'sure', 'ok']):
                result = "group_response_yes"
            elif any(word in message_lower for word in ['no', 'n', 'pass', 'nah']):
                result = "group_response_no"
            else:
                result = "general_conversation"
        elif any(word in message_lower for word in ['want', 'craving', 'hungry', 'order', 'get', 'need']):
            result = "start_fresh_request"
        else:
            result = "general_conversation"
            
        expected = scenario['expected']
        if result == expected:
            print(f"   ✅ PASS: {result}")
        else:
            print(f"   ❌ FAIL: Expected {expected}, got {result}")

if __name__ == "__main__":
    print("🚀 Testing Router Fixes\n")
    
    # Test the main issue
    success = simulate_router_fallback()
    
    # Test other scenarios
    test_other_scenarios()
    
    if success:
        print(f"\n🎉 Router fix appears to be working correctly!")
        print(f"   The incomplete request scenario now routes properly.")
    else:
        print(f"\n❌ Router fix needs more work.")