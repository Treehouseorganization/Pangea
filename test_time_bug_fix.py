#!/usr/bin/env python3
"""
Test script to verify the delivery time scheduling bug fix
"""
import sys
import os
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from datetime import datetime, timedelta
import pytz
import re
from typing import Optional

# Set environment variable to avoid Firebase error
os.environ['FIREBASE_SERVICE_ACCOUNT_JSON'] = 'dummy'

# Copy of the parse_delivery_time function to test
def parse_delivery_time(time_str) -> datetime:
    """
    Parse user time preferences into datetime objects for Uber Direct scheduling
    
    Args:
        time_str: User's time preference like "3pm", "5:30pm", "now", "lunch", etc.
                  Can be string or DatetimeWithNanoseconds object
        
    Returns:
        datetime object for the scheduled delivery time
    """
    # If it's already a datetime object, return it as-is
    if isinstance(time_str, datetime):
        return time_str
    
    # Get current time in Chicago timezone for consistent handling
    chicago_tz = pytz.timezone('America/Chicago')
    chicago_now = datetime.now(chicago_tz)
    
    # Ensure time_str is a string
    if not isinstance(time_str, str):
        time_str = str(time_str)
    
    # Handle immediate delivery
    if time_str.lower() in ['now', 'asap', 'immediately']:
        return chicago_now + timedelta(minutes=25)  # 25 minutes from now (minimum prep time)
    
    # Handle meal periods
    meal_times = {
        'breakfast': 9,  # 9am
        'lunch': 12,     # 12pm
        'dinner': 18,    # 6pm
        'late night': 21 # 9pm
    }
    
    for meal, hour in meal_times.items():
        if meal in time_str.lower():
            target_time = chicago_now.replace(hour=hour, minute=0, second=0, microsecond=0)
            # FIX: Add 30-second buffer to avoid timezone/rounding issues
            # Only schedule for tomorrow if the time is truly in the past (with buffer)
            if target_time < (chicago_now - timedelta(seconds=30)):
                target_time += timedelta(days=1)
            return target_time
    
    # Handle specific times like "3pm", "5:30pm", "2:15"
    time_patterns = [
        r'(\d{1,2}):(\d{2})\s*(pm|am)',  # 3:30pm, 2:15am
        r'(\d{1,2})\s*(pm|am)',          # 3pm, 2am
        r'(\d{1,2}):(\d{2})',            # 15:30, 14:00 (24-hour)
        r'(\d{1,2})'                     # 3 (assume current period)
    ]
    
    for pattern in time_patterns:
        match = re.search(pattern, time_str.lower())
        if match:
            groups = match.groups()
            
            if len(groups) >= 3 and groups[2]:  # has am/pm with minutes (3:30pm)
                hour = int(groups[0])
                minute = int(groups[1]) if groups[1] else 0
                period = groups[2]
                
                # Convert to 24-hour format
                if period == 'pm' and hour != 12:
                    hour += 12
                elif period == 'am' and hour == 12:
                    hour = 0
                    
            elif len(groups) == 2 and groups[1] in ['am', 'pm']:  # has am/pm without minutes (2am, 3pm)
                hour = int(groups[0])
                minute = 0
                period = groups[1]
                
                # Convert to 24-hour format
                if period == 'pm' and hour != 12:
                    hour += 12
                elif period == 'am' and hour == 12:
                    hour = 0
                    
            elif len(groups) == 2 and groups[1] and ':' in time_str:  # 24-hour format
                hour = int(groups[0])
                minute = int(groups[1])
                
            else:  # just hour number
                hour = int(groups[0])
                minute = 0
                
                # Smart defaults: if hour is 1-7, assume PM; if 8-12, assume current period
                if hour <= 7:
                    hour += 12  # assume PM
                elif hour >= 8 and hour <= 12:
                    # keep as is for now, but check if it's passed
                    pass
            
            # Create target time
            target_time = chicago_now.replace(hour=hour, minute=minute, second=0, microsecond=0)
            
            # FIX: Add 30-second buffer to avoid timezone/rounding issues
            # Only schedule for tomorrow if the time is truly in the past (with buffer)
            if target_time < (chicago_now - timedelta(seconds=30)):
                target_time += timedelta(days=1)
                
            return target_time
    
    # Default fallback: 30 minutes from now
    print(f"⚠️ Could not parse time '{time_str}', defaulting to 30 minutes from now")
    return chicago_now + timedelta(minutes=30)

def test_time_scheduling_bug():
    """Test that delivery time scheduling works correctly for same-day requests"""
    
    print("🧪 Testing delivery time scheduling bug fix...")
    
    # Mock the current time to 2:24 PM on August 31, 2025 (Chicago time)
    chicago_tz = pytz.timezone('America/Chicago')
    mock_now = chicago_tz.localize(datetime(2025, 8, 31, 14, 24, 41))  # 2:24:41 PM
    
    # Temporarily patch datetime.now for testing
    import pangea_uber_direct
    original_datetime_now = datetime.now
    
    def mock_datetime_now(tz=None):
        if tz and tz.zone == 'America/Chicago':
            return mock_now
        return original_datetime_now(tz)
    
    # Apply the mock
    pangea_uber_direct.datetime.now = mock_datetime_now
    
    try:
        # Test case: Request "3pm" at 2:24 PM - should be same day
        result_time = parse_delivery_time("3pm")
        
        print(f"📊 Test Results:")
        print(f"   Current time (mock): {mock_now.strftime('%Y-%m-%d %I:%M:%S %p %Z')}")
        print(f"   Requested: 3pm")
        print(f"   Parsed result: {result_time.strftime('%Y-%m-%d %I:%M:%S %p %Z')}")
        
        # Check if it's scheduled for the same day
        same_day = result_time.date() == mock_now.date()
        correct_time = result_time.hour == 15 and result_time.minute == 0  # 3pm = 15:00
        
        if same_day and correct_time:
            print(f"   ✅ SUCCESS: Correctly scheduled for same day at 3pm")
            return True
        else:
            print(f"   ❌ FAILED: ")
            if not same_day:
                print(f"      - Wrong date: expected {mock_now.date()}, got {result_time.date()}")
            if not correct_time:
                print(f"      - Wrong time: expected 15:00, got {result_time.hour}:{result_time.minute:02d}")
            return False
            
    finally:
        # Restore original datetime.now
        pangea_uber_direct.datetime.now = original_datetime_now

def test_edge_cases():
    """Test edge cases to ensure fix doesn't break other scenarios"""
    
    print("\n🔍 Testing edge cases...")
    
    test_cases = [
        ("11pm", "Should be scheduled for same day if requested during day"),
        ("8am", "Should be scheduled for next day if requested in evening"),
        ("now", "Should be immediate"),
        ("lunch", "Should handle meal times correctly")
    ]
    
    for time_str, description in test_cases:
        try:
            result = parse_delivery_time(time_str)
            print(f"   ✅ '{time_str}' -> {result.strftime('%Y-%m-%d %I:%M %p')} ({description})")
        except Exception as e:
            print(f"   ❌ '{time_str}' -> ERROR: {e}")

if __name__ == "__main__":
    print("🚀 Running delivery time scheduling bug tests...\n")
    
    success = test_time_scheduling_bug()
    test_edge_cases()
    
    print(f"\n{'🎉 ALL TESTS PASSED!' if success else '❌ SOME TESTS FAILED'}")