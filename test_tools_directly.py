#!/usr/bin/env python3
"""
Direct tool testing without SMS or Claude API
Tests the counter-proposal and proactive notification tools
"""

import sys
import os
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from pangea_main import (
    generate_counter_proposal,
    notify_compatible_users_of_active_groups,
    find_alternatives_for_user,
    calculate_compatibility,
    db
)
from datetime import datetime
import uuid

def test_counter_proposal_tool():
    """Test counter-proposal tool directly"""
    print("🧪 Testing Counter-Proposal Tool")
    print("=" * 50)
    
    # Mock rejection scenario
    rejected_proposal = {
        "restaurant": "Thai Garden",
        "location": "Student Union", 
        "time": "lunch",
        "group_size": 3
    }
    
    declining_user_prefs = {
        "favorite_cuisines": ["Mario's Pizza", "Sushi Express"],
        "usual_locations": ["Campus Center", "Library Plaza"],
        "preferred_times": ["12:30pm", "1pm"]
    }
    
    available_alternatives = [
        {"restaurant": "Mario's Pizza", "location": "Campus Center", "time": "12:30pm"},
        {"restaurant": "Sushi Express", "location": "Library Plaza", "time": "1pm"}
    ]
    
    try:
        result = generate_counter_proposal.invoke({
            "rejected_proposal": rejected_proposal,
            "declining_user_preferences": declining_user_prefs, 
            "available_alternatives": available_alternatives
        })
        
        print("✅ Counter-proposal tool executed successfully!")
        print(f"📝 Result type: {type(result)}")
        print(f"📄 Result: {result}")
        return True
        
    except Exception as e:
        print(f"❌ Counter-proposal tool failed: {e}")
        return False

def test_proactive_notification_tool():
    """Test proactive notification tool directly"""
    print("\n🧪 Testing Proactive Notification Tool")
    print("=" * 50)
    
    # Mock active group data
    active_group_data = {
        "restaurant": "Thai Garden",
        "location": "Student Union",
        "time": "lunch", 
        "current_members": ["+17089011754"],
        "group_id": str(uuid.uuid4())
    }
    
    try:
        result = notify_compatible_users_of_active_groups.invoke({
            "active_group_data": active_group_data,
            "max_notifications": 2,
            "compatibility_threshold": 0.6
        })
        
        print("✅ Proactive notification tool executed successfully!")
        print(f"📝 Result type: {type(result)}")
        print(f"📄 Result: {result}")
        return True
        
    except Exception as e:
        print(f"❌ Proactive notification tool failed: {e}")
        return False

def test_find_alternatives_tool():
    """Test find alternatives helper function"""
    print("\n🧪 Testing Find Alternatives Tool")
    print("=" * 50)
    
    user_prefs = {
        "favorite_cuisines": ["Thai Garden", "Sushi Express"],
        "usual_locations": ["Student Union", "Library Plaza"],
        "preferred_times": ["lunch", "12pm"]
    }
    
    try:
        result = find_alternatives_for_user(
            user_phone="+17089011754",
            rejected_proposal={
                "restaurant": "Thai Garden",
                "location": "Student Union", 
                "time": "lunch"
            }
        )
        
        print("✅ Find alternatives tool executed successfully!")
        print(f"📝 Result type: {type(result)}")
        print(f"📄 Result: {result}")
        return True
        
    except Exception as e:
        print(f"❌ Find alternatives tool failed: {e}")
        return False

def test_compatibility_calculation():
    """Test compatibility calculation (no API needed)"""
    print("\n🧪 Testing Compatibility Calculation")
    print("=" * 50)
    
    try:
        result = calculate_compatibility.invoke({
            "user1_restaurant": "Thai Garden",
            "user1_time": "lunch",
            "user2_restaurant": "Thai Garden", 
            "user2_time": "12pm",
            "user1_phone": "+17089011754",
            "user2_phone": "+16305470891"
        })
        
        print("✅ Compatibility calculation executed successfully!")
        print(f"📝 Compatibility score: {result}")
        return True
        
    except Exception as e:
        print(f"❌ Compatibility calculation failed: {e}")
        return False

def main():
    print("🍜 Pangea Tools Direct Testing")
    print("=" * 80)
    print("Testing tools directly without SMS flow or Claude API\n")
    
    results = []
    
    # Test each tool
    results.append(("Counter Proposal", test_counter_proposal_tool()))
    results.append(("Proactive Notifications", test_proactive_notification_tool()))
    results.append(("Find Alternatives", test_find_alternatives_tool()))
    results.append(("Compatibility Calculation", test_compatibility_calculation()))
    
    # Summary
    print("\n" + "=" * 80)
    print("📊 TOOL TEST RESULTS")
    print("=" * 80)
    
    passed = 0
    failed = 0
    
    for test_name, success in results:
        status = "✅ PASS" if success else "❌ FAIL"
        print(f"{status} {test_name}")
        if success:
            passed += 1
        else:
            failed += 1
    
    print(f"\nTotal: {len(results)} | Passed: {passed} | Failed: {failed}")
    
    if failed == 0:
        print("🎉 ALL TOOLS WORKING! Your core logic is solid!")
    else:
        print("⚠️  Some tools need attention - likely Firebase connection or logic issues")

if __name__ == "__main__":
    main()