#!/usr/bin/env python3
"""
Quick test script to verify scheduled delivery functionality
"""

import sys
import os
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from datetime import datetime
from pangea_uber_direct import parse_delivery_time

def test_time_parsing():
    """Test the time parsing function with various inputs"""
    
    print("🧪 Testing time parsing function...")
    
    test_cases = [
        "3pm",
        "5:30pm", 
        "lunch",
        "dinner",
        "now",
        "2:15pm",
        "18:30",
        "breakfast"
    ]
    
    for test_time in test_cases:
        try:
            parsed_time = parse_delivery_time(test_time)
            print(f"✅ '{test_time}' -> {parsed_time.strftime('%Y-%m-%d %H:%M:%S')}")
        except Exception as e:
            print(f"❌ '{test_time}' -> ERROR: {e}")
    
    print("\n🧪 Testing group data with delivery time...")
    
    # Test group data structure
    sample_group_data = {
        'restaurant': 'Chipotle',
        'location': 'Student Union',
        'delivery_time': '3pm',
        'members': ['+1234567890'],
        'group_id': 'test-group-123',
        'order_details': [
            {'user_phone': '+1234567890', 'order_number': 'ABC123', 'customer_name': 'Test User'}
        ]
    }
    
    delivery_time_str = sample_group_data.get('delivery_time', 'now')
    parsed_time = parse_delivery_time(delivery_time_str)
    
    print(f"✅ Group delivery time: '{delivery_time_str}' -> {parsed_time.strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"✅ This would be scheduled for: {parsed_time.strftime('%I:%M %p on %B %d, %Y')}")
    
    # Test UTC conversion
    import pytz
    if parsed_time.tzinfo is None:
        local_tz = pytz.timezone('America/Chicago')
        parsed_time = local_tz.localize(parsed_time)
    
    utc_time = parsed_time.astimezone(pytz.UTC)
    pickup_ready_dt = utc_time.strftime('%Y-%m-%dT%H:%M:%S.000Z')
    
    print(f"✅ Uber Direct pickup_ready_dt: {pickup_ready_dt}")
    
    print("\n🎉 All tests completed!")

if __name__ == "__main__":
    test_time_parsing()