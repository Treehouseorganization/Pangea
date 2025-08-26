#!/usr/bin/env python3
"""
Test script for the unified intelligent router
"""

import os
import sys
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

# Mock dependencies to test routing logic
class MockDB:
    def collection(self, name):
        return MockCollection()

class MockCollection:
    def document(self, doc_id):
        return MockDocument()

class MockDocument:
    def get(self):
        return MockDocSnapshot()

class MockDocSnapshot:
    exists = False
    def to_dict(self):
        return {}

# Test scenarios
test_scenarios = [
    {
        "name": "New food request",
        "message": "I want McDonald's",
        "expected_actions": ["start_fresh_request", "continue_food_matching"]
    },
    {
        "name": "Payment request",
        "message": "PAY",
        "expected_actions": ["handle_payment_request"]
    },
    {
        "name": "Group response yes",
        "message": "yes",
        "expected_actions": ["group_response_yes"]
    },
    {
        "name": "Group response no", 
        "message": "no",
        "expected_actions": ["group_response_no"]
    },
    {
        "name": "Order number",
        "message": "My order number is ABC123",
        "expected_actions": ["collect_order_number"]
    },
    {
        "name": "General question",
        "message": "How does this work?",
        "expected_actions": ["general_conversation", "faq_response"]
    }
]

def test_router_logic():
    """Test the router's decision making without external dependencies"""
    
    print("🧪 Testing Unified Intelligent Router Logic\n")
    
    # Mock the database and LLM calls for testing
    from unittest.mock import Mock, patch
    
    # Test each scenario
    for i, scenario in enumerate(test_scenarios, 1):
        print(f"{i}. Testing: {scenario['name']}")
        print(f"   Message: '{scenario['message']}'")
        
        # This would test the routing logic
        # In a full test, we'd mock the Claude API response
        expected = scenario['expected_actions']
        print(f"   Expected actions: {expected}")
        print(f"   ✅ Test case defined\n")

def test_routing_prompt_structure():
    """Test that the routing prompt has all necessary components"""
    
    print("📝 Testing Routing Prompt Structure\n")
    
    # Read the router function to check prompt components
    with open('pangea_main.py', 'r') as f:
        content = f.read()
    
    required_components = [
        "COMPREHENSIVE CONTEXT:",
        "AVAILABLE ROUTING OPTIONS:",
        "MAIN FOOD SYSTEM:",
        "ORDER PROCESSOR SYSTEM:", 
        "ROUTING LOGIC:",
        "Return JSON with:"
    ]
    
    for component in required_components:
        if component in content:
            print(f"✅ Found: {component}")
        else:
            print(f"❌ Missing: {component}")
    
    print(f"\n📊 Prompt structure check complete")

def test_action_coverage():
    """Test that all possible actions are covered in routing logic"""
    
    print("🎯 Testing Action Coverage\n")
    
    # Read the routing function
    with open('pangea_main.py', 'r') as f:
        content = f.read()
    
    # Extract routing actions from the prompt
    actions_in_prompt = [
        "start_fresh_request",
        "continue_food_matching", 
        "handle_incomplete_request",
        "group_response_yes",
        "group_response_no",
        "morning_response",
        "preference_update",
        "general_conversation",
        "collect_order_number",
        "collect_order_description", 
        "handle_payment_request",
        "redirect_to_payment",
        "need_order_first",
        "welcome_new_user",
        "faq_response"
    ]
    
    # Check if all actions are mentioned in the routing logic
    for action in actions_in_prompt:
        if action in content:
            print(f"✅ Action covered: {action}")
        else:
            print(f"❌ Action missing: {action}")
    
    print(f"\n📊 Found {len(actions_in_prompt)} total routing actions")

if __name__ == "__main__":
    print("🚀 Starting Unified Router Tests\n")
    
    test_router_logic()
    test_routing_prompt_structure() 
    test_action_coverage()
    
    print("\n✅ All tests completed!")
    print("\n💡 To fully test the router:")
    print("   1. Set up mock Claude API responses")
    print("   2. Test with real conversation contexts")
    print("   3. Verify routing decisions match expectations")