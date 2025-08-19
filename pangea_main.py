"""
Pangea Food Delivery - AI Friend SMS System
Following Anthropic's "Building Effective Agents" patterns with LangGraph
CLEANED VERSION - Duplicates removed
"""

import os
import json
from datetime import datetime, timedelta
from typing import Dict, List, Optional, TypedDict, Annotated
from dataclasses import dataclass
from venv import logger
from dotenv import load_dotenv # This loads the .env file
import uuid
import random

# Import order processing system
from pangea_order_processor import start_order_process, process_order_message

# Import locations
from pangea_locations import AVAILABLE_RESTAURANTS, AVAILABLE_DROPOFF_LOCATIONS

from pangea_locations import (
    RESTAURANTS,
    DROPOFFS,
    AVAILABLE_RESTAURANTS,
    AVAILABLE_DROPOFF_LOCATIONS,
)

MAX_GROUP_SIZE = 3 

# LangGraph imports
from langgraph.graph import StateGraph, END
from langgraph.graph.message import add_messages
from langchain_core.messages import HumanMessage, AIMessage
from langchain_core.tools import tool
from langchain_anthropic import ChatAnthropic

# External services
from twilio.rest import Client
import firebase_admin
from firebase_admin import credentials, firestore
from flask import Flask, request

load_dotenv() 

# Initialize services with 2025 best practices
twilio_client = Client(os.getenv('TWILIO_ACCOUNT_SID'), os.getenv('TWILIO_AUTH_TOKEN'))

# Use Claude Opus 4 with extended thinking and tool use capabilities
anthropic_llm = ChatAnthropic(
    model="claude-opus-4-20250514",
    api_key=os.getenv('ANTHROPIC_API_KEY'),
    temperature=0.1,
    max_tokens=4096
)

# Initialize Firebase (only if not already initialized)
if not firebase_admin._apps:
    import json
    firebase_json = os.getenv('FIREBASE_SERVICE_ACCOUNT_JSON')
    if firebase_json:
        try:
            firebase_config = json.loads(firebase_json)
            cred = credentials.Certificate(firebase_config)
            firebase_admin.initialize_app(cred)
            print("✅ Firebase initialized successfully")
        except json.JSONDecodeError as e:
            print(f"❌ Invalid Firebase JSON format: {e}")
            raise
        except Exception as e:
            print(f"❌ Firebase initialization failed: {e}")
            raise
    else:
        print("❌ FIREBASE_SERVICE_ACCOUNT_JSON environment variable not set")
        raise ValueError("Firebase credentials not configured")
db = firestore.client()


@dataclass
class UserContext:
    """Rich context passed to all tools and functions"""
    user_phone: str
    conversation_history: List[Dict]
    user_preferences: Dict
    current_session: Dict
    conversation_stage: str
    urgency_level: str  # "urgent", "flexible", "scheduled"
    personality_profile: Dict
    
    # Situational awareness
    is_correction: bool = False
    is_retry: bool = False
    rejection_history: List[Dict] = None
    search_reason: str = "new_request"  # Why we're searching
    
    def to_prompt_context(self) -> str:
        """Format context for Claude prompts"""
        return f"""
        USER CONTEXT:
        - Phone: {self.user_phone}
        - Urgency: {self.urgency_level}
        - Is correction: {self.is_correction}
        - Previous rejections: {len(self.rejection_history or [])}
        - Conversation stage: {self.conversation_stage}
        - Preferences: {self.user_preferences}
        """


# State for LangGraph
class PangeaState(TypedDict):
    messages: Annotated[List, add_messages]
    user_phone: str
    user_preferences: Dict
    current_request: Dict
    potential_matches: List[Dict]
    active_negotiations: List[Dict]
    final_group: Optional[Dict]
    conversation_stage: str
    search_attempts: int
    rejection_data: Optional[Dict]  # Store rejection info for counter-proposals
    alternative_suggestions: List[Dict]  # Track suggested alternatives
    proactive_notification_data: Optional[Dict]
    user_context: Optional[UserContext]
    routing_decision: Optional[Dict]
    suggested_response: Optional[str]
    solo_message_sent: Optional[bool] # Store proactive notification data
    is_fresh_request: Optional[bool] # Whether this is a fresh request that should override existing orders
    missing_info: Optional[List] # Missing information fields for incomplete requests
    partial_request: Optional[Dict] # Partially parsed request data
    group_formed: Optional[bool] # Whether a group has been successfully formed


def cleanup_stale_sessions():
    """Clean up old order sessions (call this periodically)"""
    try:
        cutoff_time = datetime.now() - timedelta(hours=2)
        stale_sessions = db.collection('order_sessions')\
                          .where('created_at', '<', cutoff_time)\
                          .get()
        
        for session in stale_sessions:
            session.reference.delete()
            print(f"🗑️ Cleaned up stale session: {session.id}")
            
    except Exception as e:
        print(f"❌ Cleanup failed: {e}")




# Tools following 2025 MCP best practices and Claude 4 capabilities
@tool
def get_user_preferences(phone_number: str) -> Dict:
    """
    Get user's food preferences and location history.
    
    This tool retrieves stored user data to personalize matching.
    
    Args:
        phone_number: User's phone number as unique identifier
        
    Returns:
        Dict containing user preferences, history, and learning data
        
    Example usage:
        preferences = get_user_preferences("+1234567890")
        # Returns: {"favorite_cuisines": ["Mexican", "Pizza"], "usual_locations": ["Student Union"]}
    """
    try:
        user_doc = db.collection('users').document(phone_number).get()
        if user_doc.exists:
            user_data = user_doc.to_dict()
            return {
                'preferences': user_data.get('preferences', {}),
                'successful_matches': user_data.get('successful_matches', []),
                'preferred_times': user_data.get('preferred_times', []),
                'satisfaction_scores': user_data.get('satisfaction_scores', [])
            }
        return {'preferences': {}, 'new_user': True}
    except Exception as e:
        return {'error': f'Failed to retrieve preferences: {str(e)}'}

def cleanup_all_user_data(user_phone: str):
    """Clean up ALL old data for a user when they make a new food request"""
    print(f"🧹 Cleaning up all old data for {user_phone}")
    
    try:
        # 1. Remove from ANY active groups (fake or real matches)
        user_groups = db.collection('active_groups')\
                       .where('members', 'array_contains', user_phone)\
                       .get()
        
        for group in user_groups:
            group.reference.delete()
            print(f"🗑️ Removed user from group: {group.id}")
        
        # 2. Remove their active orders
        user_orders = db.collection('active_orders')\
                       .where('user_phone', '==', user_phone)\
                       .get()
        
        for order in user_orders:
            order.reference.delete()
            print(f"🗑️ Removed active order: {order.id}")
        
        # 3. Clear their order session
        try:
            db.collection('order_sessions').document(user_phone).delete()
            print(f"🗑️ Cleared order session")
        except:
            pass  # OK if no session exists
        
        # 4. Cancel any pending negotiations
        pending_negotiations = db.collection('negotiations')\
                               .where('to_user', '==', user_phone)\
                               .where('status', '==', 'pending')\
                               .get()
        
        for neg in pending_negotiations:
            neg.reference.update({'status': 'cancelled_new_request'})
            print(f"🗑️ Cancelled pending negotiation: {neg.id}")
            
        print(f"✅ Complete cleanup finished for {user_phone}")
        
    except Exception as e:
        print(f"❌ Cleanup failed for {user_phone}: {e}")

@tool
def find_potential_matches_contextual(
    restaurant_preference: str,
    location: str,
    time_window: str,
    requesting_user: str,
    user_context: UserContext
) -> List[Dict]:
    """Context-aware matching that adapts to user situation and scores matches like the old system."""

    print(f"🔍 CONTEXT-AWARE SEARCH for {requesting_user}:")
    print(f"   Reason: {user_context.search_reason}")
    print(f"   Urgency: {user_context.urgency_level}")
    print(f"   Is correction: {user_context.is_correction}")

    # Adapt flexibility based on context
    if user_context.is_correction:
        flexibility_score = 0.7
    elif user_context.urgency_level == "urgent":
        flexibility_score = 0.8
    elif len(user_context.rejection_history or []) > 2:
        flexibility_score = 0.3
    else:
        flexibility_score = 0.5

    # --- Start of old matching logic ---
    print(f"🔍 SEARCHING:")
    print(f"   Looking for: '{restaurant_preference}' at '{location}' ({time_window})")
    print(f"   Excluding: {requesting_user}")

    import time
    from datetime import datetime, timedelta

    time.sleep(1.5)
    print(f"⏱️ Added search delay for spontaneous matching reliability")

    try:
        matches = []

        # Query database
        orders_ref = db.collection('active_orders')
        similar_orders = orders_ref.where('location', '==', location) \
                                   .where('status', '==', 'looking_for_group') \
                                   .where('user_phone', '!=', requesting_user) \
                                   .limit(10).get()

        print(f"📊 Found {len(similar_orders)} potential orders in database")

        # Aggressive filtering for stale or mismatched orders
        current_time = datetime.now()
        filtered_orders = []

        for order in similar_orders:
            order_data = order.to_dict()
            order_time = order_data.get('created_at')
            order_time_pref = order_data.get('time_requested', 'flexible')

            if order_data.get('user_phone') == requesting_user:
                print(f"   🚫 Skipping self-match for {requesting_user}")
                continue

            if order_time:
                try:
                    if hasattr(order_time, 'tzinfo') and order_time.tzinfo is not None:
                        order_time = order_time.replace(tzinfo=None)
                    if hasattr(current_time, 'tzinfo') and current_time.tzinfo is not None:
                        current_time = current_time.replace(tzinfo=None)

                    time_diff = current_time - order_time

                    if time_diff > timedelta(minutes=30):
                        print(f"   ⏰ Skipping stale order: {order_time_pref} from {time_diff} ago (user: {order_data.get('user_phone')})")
                        continue

                    order_hour = order_time.hour
                    current_hour = current_time.hour
                    hour_diff = abs(current_hour - order_hour)
                    if hour_diff > 4 and hour_diff < 20:
                        print(f"   🍽️ Skipping order from different meal period: {order_hour}:00 vs {current_hour}:00")
                        continue

                except Exception as e:
                    print(f"   ⚠️ Error comparing times, skipping problematic order: {e}")
                    continue
            else:
                print(f"   ❌ Skipping order with no timestamp: {order_data}")
                continue

            # ENHANCED: Check if this is a paid solo order that should be included
            order_user_phone = order_data.get('user_phone')
            if is_paid_solo_order(order_user_phone):
                print(f"   💰 Found PAID solo order from {order_user_phone}")
                filtered_orders.append(order)
                continue

            filtered_orders.append(order)

        print(f"📊 After aggressive time filtering: {len(filtered_orders)} potential orders")

        # Compatibility scoring
        for order in filtered_orders:
            order_data = order.to_dict()
            print(f"   Checking: {order_data}")

            compatibility_score = calculate_compatibility(
                user1_restaurant=restaurant_preference,
                user1_time=time_window,
                user2_restaurant=order_data.get('restaurant', ''),
                user2_time=order_data.get('time_requested', 'flexible'),
                user1_phone=requesting_user,
                user2_phone=order_data['user_phone']
            )

            if compatibility_score >= 0.3:
                match = {
                    'user_phone': order_data['user_phone'],
                    'restaurant': order_data['restaurant'],
                    'location': order_data['location'],
                    'time_requested': order_data['time_requested'],
                    'compatibility_score': compatibility_score,
                    'user_flexibility': order_data.get('flexibility_score', flexibility_score)
                }
                matches.append(match)
                print(f"   ✅ MATCH: {match}")
            else:
                print(f"   ❌ No match: score {compatibility_score}")

        matches.sort(key=lambda x: x['compatibility_score'], reverse=True)

        # Apply rejection learning
        if user_context.rejection_history:
            matches = filter_by_rejection_history(matches, user_context.rejection_history)

        print(f"🎯 Final matches: {len(matches[:3])}")
        return matches[:3]

    except Exception as e:
        print(f"❌ Matching failed: {e}")
        return []


def _match_candidates(
    restaurant_preference: str,
    location: str,
    time_window: str,
    requesting_user: str,
    flexibility_score: float
) -> List[Dict]:
    """Internal helper: Runs DB query, filters, and scores potential matches."""
    import time
    from datetime import datetime, timedelta

    print(f"🔍 SEARCHING:")
    print(f"   Looking for: '{restaurant_preference}' at '{location}' ({time_window})")
    print(f"   Excluding: {requesting_user}")

    # Small delay for spontaneous match reliability
    time.sleep(1.5)
    print(f"⏱️ Added search delay for spontaneous matching reliability")

    matches = []

    try:
        # Query database
        orders_ref = db.collection('active_orders')
        similar_orders = orders_ref.where('location', '==', location) \
                                   .where('status', '==', 'looking_for_group') \
                                   .where('user_phone', '!=', requesting_user) \
                                   .limit(10).get()

        print(f"📊 Found {len(similar_orders)} potential orders in database")

        # Aggressive filtering
        current_time = datetime.now()
        filtered_orders = []

        for order in similar_orders:
            order_data = order.to_dict()
            order_time = order_data.get('created_at')
            order_time_pref = order_data.get('time_requested', 'flexible')

            # Skip self
            if order_data.get('user_phone') == requesting_user:
                print(f"   🚫 Skipping self-match for {requesting_user}")
                continue

            # Skip stale or different meal period orders
            if order_time:
                try:
                    if hasattr(order_time, 'tzinfo') and order_time.tzinfo:
                        order_time = order_time.replace(tzinfo=None)
                    if hasattr(current_time, 'tzinfo') and current_time.tzinfo:
                        current_time = current_time.replace(tzinfo=None)

                    time_diff = current_time - order_time
                    if time_diff > timedelta(minutes=30):
                        print(f"   ⏰ Skipping stale order: {order_time_pref} from {time_diff} ago")
                        continue

                    order_hour = order_time.hour
                    current_hour = current_time.hour
                    hour_diff = abs(current_hour - order_hour)
                    if hour_diff > 4 and hour_diff < 20:
                        print(f"   🍽️ Skipping different meal period: {order_hour}:00 vs {current_hour}:00")
                        continue

                except Exception as e:
                    print(f"   ⚠️ Time comparison error, skipping: {e}")
                    continue
            else:
                print(f"   ❌ No timestamp, skipping: {order_data}")
                continue

            filtered_orders.append(order)

        print(f"📊 After filtering: {len(filtered_orders)} potential orders")

        # Score candidates
        for order in filtered_orders:
            order_data = order.to_dict()
            print(f"   Checking: {order_data}")

            compatibility_score = calculate_compatibility(
                user1_restaurant=restaurant_preference,
                user1_time=time_window,
                user2_restaurant=order_data.get('restaurant', ''),
                user2_time=order_data.get('time_requested', 'flexible'),
                user1_phone=requesting_user,
                user2_phone=order_data['user_phone']
            )

            if compatibility_score >= 0.3:
                match = {
                    'user_phone': order_data['user_phone'],
                    'restaurant': order_data['restaurant'],
                    'location': order_data['location'],
                    'time_requested': order_data['time_requested'],
                    'compatibility_score': compatibility_score,
                    'user_flexibility': order_data.get('flexibility_score', flexibility_score)
                }
                matches.append(match)
                print(f"   ✅ MATCH: {match}")
            else:
                print(f"   ❌ No match: score {compatibility_score}")

        matches.sort(key=lambda x: x['compatibility_score'], reverse=True)
        return matches[:3]

    except Exception as e:
        print(f"❌ Matching failed: {e}")
        return []




def filter_by_rejection_history(matches: List[Dict], rejection_history: List[Dict]) -> List[Dict]:
    """Filter out matches similar to previous rejections"""
    if not rejection_history:
        return matches
    
    filtered_matches = []
    for match in matches:
        should_avoid = False
        for rejection in rejection_history:
            if rejection.get('restaurant') == match.get('restaurant'):
                print(f"   ❌ Filtering out {match.get('restaurant')} - previously rejected")
                should_avoid = True
                break
        if not should_avoid:
            filtered_matches.append(match)
    
    return filtered_matches


def convert_time_to_string(time_value):
    """
    Convert any time object (string or DatetimeWithNanoseconds) to a string for matching.
    Handles timezone conversion properly to show correct local times to users.
    
    Args:
        time_value: Can be string like "7:40am-8:00am" or DatetimeWithNanoseconds object
        
    Returns:
        String representation suitable for time matching and display
    """
    # If already a string, return as-is
    if isinstance(time_value, str):
        return time_value
    
    # Handle datetime objects (including DatetimeWithNanoseconds)
    if hasattr(time_value, 'hour') and hasattr(time_value, 'minute'):
        # Check if it's a timezone-aware datetime that needs conversion
        if hasattr(time_value, 'tzinfo') and time_value.tzinfo is not None:
            # Convert UTC to local timezone (Chicago/Central Time)
            import pytz
            try:
                # If it's in UTC, convert to Central Time
                if time_value.tzinfo.zone == 'UTC' or str(time_value.tzinfo) == 'UTC':
                    central_tz = pytz.timezone('America/Chicago')
                    local_time = time_value.astimezone(central_tz)
                else:
                    # Already in local timezone
                    local_time = time_value
            except:
                # Fallback: use the time as-is if timezone conversion fails
                local_time = time_value
        else:
            # No timezone info, assume it's already local time
            local_time = time_value
        
        # Extract hour and minute from the (potentially converted) time
        hour = local_time.hour
        minute = local_time.minute
        
        # Convert to 12-hour format with am/pm
        if hour == 0:
            return f"12:{minute:02d}am"
        elif hour < 12:
            return f"{hour}:{minute:02d}am"
        elif hour == 12:
            return f"12:{minute:02d}pm"
        else:
            return f"{hour - 12}:{minute:02d}pm"
    
    # Fallback: convert to string
    return str(time_value)


def calculate_compatibility(
    user1_restaurant: str,
    user1_time: str, 
    user2_restaurant: str,
    user2_time: str,
    user1_phone: str = "",
    user2_phone: str = ""
) -> float:
    """Calculate compatibility between two users' food orders using deterministic logic first"""
    
    print(f"   🔍 Comparing: '{user1_restaurant}' vs '{user2_restaurant}'")
    print(f"   🕐 Times: '{user1_time}' vs '{user2_time}'")
    
    # RULE 1: DIFFERENT RESTAURANTS = AUTOMATIC 0.0 (NO EXCEPTIONS)
    if not restaurants_match(user1_restaurant, user2_restaurant):
        print(f"   ❌ Different restaurants - automatic 0.0")
        return 0.0
    
    # RULE 2: If restaurants match, check time compatibility
    time_score = calculate_time_compatibility(user1_time, user2_time)
    print(f"   ⏰ Time compatibility: {time_score}")
    
    # Only use LLM for edge cases if needed
    if time_score == 0.5:  # Uncertain cases only
        llm_score = get_llm_time_assessment(user1_time, user2_time)
        final_score = llm_score
    else:
        final_score = time_score
    
    print(f"   ✅ Final compatibility score: {final_score}")
    return final_score

def restaurants_match(rest1: str, rest2: str) -> bool:
    """Deterministic restaurant matching - no LLM needed"""
    
    # Clean and normalize
    rest1_clean = rest1.lower().strip()
    rest2_clean = rest2.lower().strip()
    
    # Exact match
    if rest1_clean == rest2_clean:
        return True
    
    # Known restaurant mappings (deterministic)
    restaurant_aliases = {
    "chipotle": ["chipotle", "mexican", "burrito", "bowl"],
    "mcdonald's": ["mcdonald", "mcdonalds", "mcd", "burger", "fries"],
    "chick-fil-a": ["chickfila", "chick", "chicken", "sandwich"],
    "portillo's": ["portillos", "italian beef", "hot dog", "chicago"],
    "starbucks": ["starbucks", "coffee", "latte", "frappuccino"]
}
    
    # Check if both restaurants map to the same canonical restaurant
    rest1_canonical = None
    rest2_canonical = None
    
    for canonical, aliases in restaurant_aliases.items():
        if any(alias in rest1_clean for alias in aliases):
            rest1_canonical = canonical
        if any(alias in rest2_clean for alias in aliases):
            rest2_canonical = canonical
    
    result = rest1_canonical is not None and rest1_canonical == rest2_canonical
    print(f"   🍕 Restaurant match: {rest1_canonical} == {rest2_canonical} = {result}")
    return result

def calculate_time_compatibility(time1: str, time2: str) -> float:
    """Enhanced time compatibility with proper range vs specific time matching"""
    
    # Convert any DatetimeWithNanoseconds objects to strings first
    time1_str = convert_time_to_string(time1)
    time2_str = convert_time_to_string(time2)
    
    time1_clean = time1_str.lower().strip()
    time2_clean = time2_str.lower().strip()
    
    # Exact matches
    if time1_clean == time2_clean:
        return 1.0
    
    # Immediate time matches
    immediate_times = ["now", "soon", "asap", "immediately"]
    if any(t in time1_clean for t in immediate_times) and any(t in time2_clean for t in immediate_times):
        return 1.0
    
    # Parse both times to check for range/specific time overlaps
    time1_parsed = parse_time_for_matching(time1_clean)
    time2_parsed = parse_time_for_matching(time2_clean)
    
    # Check for range vs specific time compatibility
    if time1_parsed and time2_parsed:
        compatibility = check_time_overlap(time1_parsed, time2_parsed)
        if compatibility > 0:
            return compatibility
    
    # Clear incompatibilities
    incompatible_pairs = [
        (["breakfast", "morning"], ["dinner", "evening", "night"]),
        (["lunch", "noon", "12pm"], ["dinner", "evening", "night"]),
        (["now", "soon"], ["tomorrow", "later", "tonight"]),
    ]
    
    for group1, group2 in incompatible_pairs:
        if (any(t in time1_clean for t in group1) and any(t in time2_clean for t in group2)) or \
           (any(t in time2_clean for t in group1) and any(t in time1_clean for t in group2)):
            return 0.0
    
    # Check for specific hour incompatibilities
    if has_hour_conflict(time1_clean, time2_clean):
        return 0.0
    
    # Similar time periods
    similar_times = [
        ["lunch", "noon", "12pm", "1pm", "lunch time"],
        ["dinner", "evening", "6pm", "7pm", "8pm", "tonight"],
        ["breakfast", "morning", "8am", "9am", "10am"],
    ]
    
    for time_group in similar_times:
        if any(t in time1_clean for t in time_group) and any(t in time2_clean for t in time_group):
            return 0.8
    
    # Uncertain cases - might need LLM
    return 0.5

def parse_time_for_matching(time_str: str):
    """Parse time string into structured format for matching"""
    import re
    
    # Handle range formats like "8:40am-9:00am" or "8:30-9:00am"
    range_match = re.search(r'(\d{1,2}):?(\d{0,2})?\s*(am|pm)?\s*-\s*(\d{1,2}):?(\d{0,2})?\s*(am|pm)', time_str)
    if range_match:
        start_hour = int(range_match.group(1))
        start_min = int(range_match.group(2)) if range_match.group(2) else 0
        start_ampm = range_match.group(3) or range_match.group(6)  # Use end ampm if start missing
        end_hour = int(range_match.group(4))
        end_min = int(range_match.group(5)) if range_match.group(5) else 0
        end_ampm = range_match.group(6)
        
        # Convert to 24h format
        if start_ampm == 'pm' and start_hour != 12:
            start_hour += 12
        elif start_ampm == 'am' and start_hour == 12:
            start_hour = 0
            
        if end_ampm == 'pm' and end_hour != 12:
            end_hour += 12
        elif end_ampm == 'am' and end_hour == 12:
            end_hour = 0
            
        return {
            'type': 'range',
            'start_hour': start_hour,
            'start_min': start_min,
            'end_hour': end_hour,
            'end_min': end_min
        }
    
    # Handle specific times like "9am" or "9:00am"
    specific_match = re.search(r'(\d{1,2}):?(\d{0,2})?\s*(am|pm)', time_str)
    if specific_match:
        hour = int(specific_match.group(1))
        minute = int(specific_match.group(2)) if specific_match.group(2) else 0
        ampm = specific_match.group(3)
        
        # Convert to 24h format
        if ampm == 'pm' and hour != 12:
            hour += 12
        elif ampm == 'am' and hour == 12:
            hour = 0
            
        return {
            'type': 'specific',
            'hour': hour,
            'minute': minute
        }
    
    return None

def check_time_overlap(time1_parsed, time2_parsed):
    """Check if two parsed times have overlap/compatibility"""
    
    # Both specific times - must match exactly for high compatibility
    if time1_parsed['type'] == 'specific' and time2_parsed['type'] == 'specific':
        if (time1_parsed['hour'] == time2_parsed['hour'] and 
            abs(time1_parsed['minute'] - time2_parsed['minute']) <= 15):
            return 1.0
        return 0.0
    
    # One range, one specific - check if specific falls within range
    if time1_parsed['type'] == 'range' and time2_parsed['type'] == 'specific':
        return check_specific_in_range(time2_parsed, time1_parsed)
    elif time1_parsed['type'] == 'specific' and time2_parsed['type'] == 'range':
        return check_specific_in_range(time1_parsed, time2_parsed)
    
    # Both ranges - check for overlap
    if time1_parsed['type'] == 'range' and time2_parsed['type'] == 'range':
        return check_range_overlap(time1_parsed, time2_parsed)
    
    return 0.0

def check_specific_in_range(specific_time, range_time):
    """Check if a specific time falls within a time range"""
    spec_minutes = specific_time['hour'] * 60 + specific_time['minute']
    range_start = range_time['start_hour'] * 60 + range_time['start_min']
    range_end = range_time['end_hour'] * 60 + range_time['end_min']
    
    # Handle overnight ranges
    if range_end < range_start:
        range_end += 24 * 60
    
    if range_start <= spec_minutes <= range_end:
        return 1.0  # Perfect match - specific time is within range
    
    # Check if close (within 30 minutes of range)
    if (range_start - 30 <= spec_minutes <= range_start) or (range_end <= spec_minutes <= range_end + 30):
        return 0.7
    
    return 0.0

def check_range_overlap(range1, range2):
    """Check if two time ranges overlap"""
    start1 = range1['start_hour'] * 60 + range1['start_min']
    end1 = range1['end_hour'] * 60 + range1['end_min']
    start2 = range2['start_hour'] * 60 + range2['start_min']
    end2 = range2['end_hour'] * 60 + range2['end_min']
    
    # Handle overnight ranges
    if end1 < start1:
        end1 += 24 * 60
    if end2 < start2:
        end2 += 24 * 60
    
    # Check for overlap
    if start1 <= end2 and start2 <= end1:
        return 0.9  # High compatibility for overlapping ranges
    
    return 0.0

def has_hour_conflict(time1: str, time2: str) -> bool:
    """Check for obvious hour conflicts like 7pm vs 12am"""
    
    # Convert any DatetimeWithNanoseconds objects to strings first
    time1_str = convert_time_to_string(time1)
    time2_str = convert_time_to_string(time2)
    
    import re
    
    # Skip range times - let smart assessment handle them
    if 'between' in time1_str or 'between' in time2_str:
        return False
    
    # Extract hours from times like "7pm", "12am", "around 7pm"
    hour_pattern = r'(\d{1,2})\s*(am|pm)'
    
    match1 = re.search(hour_pattern, time1_str)
    match2 = re.search(hour_pattern, time2_str)
    
    if match1 and match2:
        hour1, period1 = match1.groups()
        hour2, period2 = match2.groups()
        
        hour1, hour2 = int(hour1), int(hour2)
        
        # Convert to 24-hour format
        if period1 == 'pm' and hour1 != 12:
            hour1 += 12
        elif period1 == 'am' and hour1 == 12:
            hour1 = 0
            
        if period2 == 'pm' and hour2 != 12:
            hour2 += 12
        elif period2 == 'am' and hour2 == 12:
            hour2 = 0
        
        # Check if times are more than 4 hours apart (clearly different meal times)
        time_diff = abs(hour1 - hour2)
        if time_diff > 12:  # Handle day wrap-around
            time_diff = 24 - time_diff
            
        if time_diff > 4:  # More than 4 hours apart = incompatible
            print(f"   ⏰ Hour conflict: {hour1}:00 vs {hour2}:00 ({time_diff}h apart)")
            return True
    
    return False

def get_llm_time_assessment(time1: str, time2: str) -> float:
    """Smart time matching with better heuristics and no signal timeout"""
    
    # Convert any DatetimeWithNanoseconds objects to strings first
    time1_str = convert_time_to_string(time1)
    time2_str = convert_time_to_string(time2)
    
    time1_lower = time1_str.lower().strip()
    time2_lower = time2_str.lower().strip()
    
    print(f"   🧠 Smart time assessment: '{time1_str}' vs '{time2_str}'")
    
    # Extract hours for both times
    import re
    
    def extract_hour_info(time_str):
        """Extract hour and period info from time string"""
        # Handle ranges like "between 6:30 pm to 7:00pm"
        if 'between' in time_str and 'to' in time_str:
            # Extract the range
            range_match = re.search(r'between\s+(\d{1,2})(?::(\d{2}))?\s*(pm|am)?\s*to\s*(\d{1,2})(?::(\d{2}))?\s*(pm|am)', time_str)
            if range_match:
                start_hour = int(range_match.group(1))
                start_period = range_match.group(3) or range_match.group(6) or 'pm'
                end_hour = int(range_match.group(4))
                end_period = range_match.group(6) or 'pm'
                
                # Convert to 24-hour
                if start_period == 'pm' and start_hour != 12:
                    start_hour += 12
                elif start_period == 'am' and start_hour == 12:
                    start_hour = 0
                    
                if end_period == 'pm' and end_hour != 12:
                    end_hour += 12
                elif end_period == 'am' and end_hour == 12:
                    end_hour = 0
                
                return {'type': 'range', 'start': start_hour, 'end': end_hour}
        
        # Handle specific times like "7 pm", "7:30pm"
        time_match = re.search(r'(\d{1,2}):?(\d{0,2})\s*(pm|am)', time_str)
        if time_match:
            hour = int(time_match.group(1))
            period = time_match.group(3)
            
            if period == 'pm' and hour != 12:
                hour += 12
            elif period == 'am' and hour == 12:
                hour = 0
                
            return {'type': 'specific', 'hour': hour}
        
        # Handle "around X" patterns
        around_match = re.search(r'around\s+(\d{1,2})', time_str)
        if around_match:
            hour = int(around_match.group(1))
            # Default to PM for dinner hours
            if hour >= 1 and hour <= 7:
                hour += 12
            return {'type': 'around', 'hour': hour}
            
        return None
    
    time1_info = extract_hour_info(time1_lower)
    time2_info = extract_hour_info(time2_lower)
    
    if time1_info and time2_info:
        # Range vs specific time
        if time1_info['type'] == 'range' and time2_info['type'] == 'specific':
            if time1_info['start'] <= time2_info['hour'] <= time1_info['end']:
                print(f"   ✅ Specific time {time2_info['hour']} falls in range {time1_info['start']}-{time1_info['end']}")
                return 1.0
        elif time2_info['type'] == 'range' and time1_info['type'] == 'specific':
            if time2_info['start'] <= time1_info['hour'] <= time2_info['end']:
                print(f"   ✅ Specific time {time1_info['hour']} falls in range {time2_info['start']}-{time2_info['end']}")
                return 1.0
        
        # Both specific times
        elif time1_info['type'] == 'specific' and time2_info['type'] == 'specific':
            time_diff = abs(time1_info['hour'] - time2_info['hour'])
            if time_diff == 0:
                print(f"   ✅ Exact time match: {time1_info['hour']}")
                return 1.0
            elif time_diff <= 1:
                print(f"   ✅ Close time match: {time_diff}h difference")
                return 0.8
        
        # Around + specific or range
        elif 'around' in time1_info.get('type', '') or 'around' in time2_info.get('type', ''):
            h1 = time1_info.get('hour', 0)
            h2 = time2_info.get('hour', 0) 
            
            # Handle range + around
            if time1_info.get('type') == 'range':
                if time1_info['start'] <= h2 <= time1_info['end']:
                    print(f"   ✅ Around time {h2} falls in range {time1_info['start']}-{time1_info['end']}")
                    return 0.9
            elif time2_info.get('type') == 'range':
                if time2_info['start'] <= h1 <= time2_info['end']:
                    print(f"   ✅ Around time {h1} falls in range {time2_info['start']}-{time2_info['end']}")
                    return 0.9
            else:
                # Both specific or around
                time_diff = abs(h1 - h2)
                if time_diff <= 1:
                    print(f"   ✅ Around match: {time_diff}h difference")
                    return 0.9
    
    # Fallback to simple text matching
    # If both mention same time period, likely compatible
    if any(period in time1_lower and period in time2_lower for period in ['morning', 'lunch', 'dinner', 'evening']):
        print(f"   ⚡ Quick match on time period")
        return 1.0
    
    print(f"   ❌ No clear time match found")
    return 0.0

def simple_compatibility_check(pref1: str, pref2: str, time1: str, time2: str) -> float:
    """Simple fallback when agent reasoning fails"""
    
    # Basic restaurant matching
    if pref1.lower().strip() == pref2.lower().strip():
        print(f"   ✅ Exact restaurant match")
        return 0.9
    elif "mario" in pref1.lower() and "mario" in pref2.lower():
        print(f"   🍕 Mario's Pizza match")
        return 0.9
    elif "thai" in pref1.lower() and "thai" in pref2.lower():
        print(f"   🍜 Thai food match")
        return 0.9
    elif "pizza" in pref1.lower() and "pizza" in pref2.lower():
        print(f"   🍕 Pizza match")
        return 0.8
    elif "sushi" in pref1.lower() and "sushi" in pref2.lower():
        print(f"   🍣 Sushi match")
        return 0.8
    else:
        print(f"   ❌ No restaurant match found")
        return 0.0

def check_historical_compatibility(user1: str, user2: str) -> float:
    """Check if users have successfully ordered together before"""
    try:
        # Query successful group orders containing both users
        successful_orders = db.collection('completed_orders')\
                             .where('participants', 'array_contains', user1)\
                             .where('status', '==', 'successful').get()
        
        for order in successful_orders:
            if user2 in order.to_dict().get('participants', []):
                return 1.0  # Perfect score if they've ordered together successfully
        return 0.5  # Neutral if no history
    except:
        return 0.5

def calculate_restaurant_similarity(pref1: str, pref2: str) -> float:
    """Calculate similarity between restaurant preferences"""
    if pref1.lower() == pref2.lower():
        return 1.0
    
    # Simple cuisine matching (could be enhanced with ML)
    cuisine_map = {
        'thai': ['thai', 'asian', 'spicy'],
        'pizza': ['pizza', 'italian'],
        'sushi': ['sushi', 'japanese', 'asian'],
        'burger': ['burger', 'american', 'fast food'],
        'salad': ['salad', 'healthy', 'green']
    }
    
    pref1_lower = pref1.lower()
    pref2_lower = pref2.lower()
    
    for cuisine, keywords in cuisine_map.items():
        if any(keyword in pref1_lower for keyword in keywords) and \
           any(keyword in pref2_lower for keyword in keywords):
            return 0.7
    
    return 0.2  # Low but non-zero for flexibility

def negotiate_with_other_ai(
    target_ai_user: str,
    proposal: Dict,
    negotiation_id: str,
    strategy: str = "collaborative"
) -> Dict:
    """
    Advanced inter-agent negotiation using Claude 4's enhanced reasoning.
    
    Implements sophisticated negotiation strategies based on user preferences
    and historical success patterns.
    
    Args:
        target_ai_user: Phone number of target user's AI Friend
        proposal: Detailed proposal with restaurant, time, location, alternatives
        negotiation_id: Unique identifier for this negotiation
        strategy: Negotiation approach ("collaborative", "persuasive", "flexible")
        
    Returns:
        Negotiation result with status and any counter-proposals
        
    Example:
        result = negotiate_with_other_ai(
            target_ai_user="+1987654321",
            proposal={
                "primary_restaurant": "Thai Garden",
                "alternative_restaurants": ["Sushi Express", "Green Bowls"],
                "time": "12:30pm",
                "location": "Student Union",
                "requesting_user": "+1234567890",
                "group_size_current": 2,
                "incentive": "Free delivery if we get 4 people"
            },
            negotiation_id="neg_123",
            strategy="collaborative"
        )
    """
    try:
        # Enhanced negotiation with learning from past interactions
        target_user_history = get_user_preferences(target_ai_user)
        
        # Create sophisticated negotiation document
        negotiation_doc = {
            'from_user': proposal.get('requesting_user'),
            'to_user': target_ai_user,
            'proposal': proposal,
            'strategy': strategy,
            'target_user_preferences': target_user_history,
            'status': 'pending',
            'created_at': datetime.now(),
            'negotiation_id': negotiation_id,
            'ai_reasoning': generate_negotiation_reasoning(proposal, target_user_history, strategy)
        }
        
        # Store with enhanced metadata for learning
        db.collection('negotiations').document(negotiation_id).set(negotiation_doc)
        
        # Trigger real-time notification (in production would use pub/sub)
        send_negotiation_notification(target_ai_user, negotiation_doc)
        
        return {
            "status": "sent", 
            "message": f"Advanced proposal sent using {strategy} strategy",
            "negotiation_id": negotiation_id,
            "estimated_success_probability": calculate_negotiation_success_probability(
                proposal, target_user_history
            )
        }
        
    except Exception as e:
        return {"status": "failed", "error": str(e)}

def generate_negotiation_reasoning(proposal: Dict, target_history: Dict, strategy: str) -> str:
    """Generate AI reasoning for negotiation approach"""
    
    reasoning_prompt = f"""
    As an AI Friend negotiating on behalf of my user, analyze this situation:
    
    My user wants: {proposal.get('primary_restaurant')} at {proposal.get('time')}
    Target user's history: {target_history.get('preferences', {})}
    Strategy: {strategy}
    
    Generate a reasoning strategy for this negotiation that maximizes success probability
    while being fair to both users. Consider alternatives and incentives.
    """
    
    # In production, this would use Claude 4's extended thinking
    response = anthropic_llm.invoke([HumanMessage(content=reasoning_prompt)])
    return response.content

def calculate_negotiation_success_probability(proposal: Dict, target_history: Dict) -> float:
    """Calculate estimated success probability for a negotiation"""
    # Simplified calculation - would be more sophisticated in production
    base_probability = 0.5
    
    # Boost if restaurants match user's history
    if proposal.get('primary_restaurant') in target_history.get('preferences', {}).get('favorite_cuisines', []):
        base_probability += 0.3
    
    # Consider flexibility and alternatives
    if len(proposal.get('alternatives', [])) > 0:
        base_probability += 0.1
    
    return min(base_probability, 1.0)

def generate_counter_proposal(
    rejected_proposal: Dict,
    declining_user_preferences: Dict,
    user_phone: str = None
) -> Dict:
    """
    Complete counter-proposal system: finds alternatives AND decides whether to counter-propose
    Now includes location preferences from user data
    
    Args:
        rejected_proposal: The proposal they rejected
        declining_user_preferences: Their food/location preferences
        user_phone: Phone number of user who declined (extracted from preferences if not provided)
        
    Returns:
        Dict with should_counter, counter_proposal, alternatives found, reasoning
    """
    
    # Get user phone from preferences if not provided
    if not user_phone:
        user_phone = declining_user_preferences.get('phone') or declining_user_preferences.get('user_phone', 'unknown')
    
    print(f"🤔 Generating location-aware counter-proposal for {user_phone}")
    
    # STEP 1: Get user's preferences including location patterns
    user_prefs = declining_user_preferences.get('preferences', {})
    preferred_cuisines = user_prefs.get('favorite_cuisines', ['any'])
    preferred_locations = user_prefs.get('usual_locations', [])  # ✅ Get location preferences
    
    print(f"🍽️ User's favorite cuisines: {preferred_cuisines}")
    print(f"📍 User's usual locations: {preferred_locations}")
    
    if not preferred_cuisines or preferred_cuisines == ['any']:
        # Use historical data or fallback to popular options
        successful_matches = declining_user_preferences.get('successful_matches', [])
        if successful_matches:
            preferred_cuisines = [match.get('restaurant') for match in successful_matches[-3:]]
        else:
            preferred_cuisines = ['Thai Garden', 'McDonald\'s', 'Chipotle', 'Starbucks']
    
    # If no preferred locations, use rejected location + nearby alternatives
    if not preferred_locations:
        rejected_location = rejected_proposal.get('location', 'Richard J Daley Library')
        preferred_locations = [rejected_location]
        # Add nearby locations as backup
        nearby_locations = get_nearby_locations(rejected_location)
        preferred_locations.extend(nearby_locations)
    
    # STEP 2: Search for alternatives across user's preferred cuisines AND locations
    alternative_opportunities = []
    
    for cuisine in preferred_cuisines[:3]:  # Check top 3 cuisines
        for location in preferred_locations[:3]:  # Check top 3 locations ✅ Use their preferred locations
            try:
                print(f"🔍 Searching {cuisine} at {location}")
                
                matches = find_potential_matches(
                    restaurant_preference=cuisine,
                    location=location,
                    time_window="flexible",  # More flexible after rejection
                    requesting_user=user_phone
                )
                
                # Add preference scores
                for match in matches:
                    match['location_in_preferences'] = location in user_prefs.get('usual_locations', [])
                    match['cuisine_in_preferences'] = cuisine in user_prefs.get('favorite_cuisines', [])
                
                alternative_opportunities.extend(matches)
                
            except Exception as e:
                print(f"⚠️ Error searching for {cuisine} at {location}: {e}")
                continue
    
    # Remove duplicates and sort by preference matching
    seen_users = set()
    unique_alternatives = []
    
    # Sort by compatibility + preference matching
    def preference_score(alt):
        score = alt.get('compatibility_score', 0) * 0.6
        if alt.get('location_in_preferences'):
            score += 0.2  # Bonus for preferred location
        if alt.get('cuisine_in_preferences'):
            score += 0.2  # Bonus for preferred cuisine
        return score
    
    alternative_opportunities.sort(key=preference_score, reverse=True)
    
    for alt in alternative_opportunities:
        user_phone_alt = alt.get('user_phone')
        if user_phone_alt and user_phone_alt not in seen_users:
            seen_users.add(user_phone_alt)
            unique_alternatives.append(alt)
        if len(unique_alternatives) >= 3:
            break
    
    print(f"🎯 Found {len(unique_alternatives)} preference-aware alternatives")
    
    # STEP 3: Analyze whether to make counter-proposal using Claude with ALL preference context
    if not unique_alternatives:
        return {
            "should_counter": False,
            "counter_proposal": None,
            "alternatives_found": [],
            "reasoning": "No alternative opportunities available"
        }
    
    # ✅ ENHANCED: Pass ALL user preferences to Claude including location preferences
    counter_proposal_prompt = f"""
    A user rejected this group food proposal. Should I make a counter-proposal?
    
    REJECTED PROPOSAL:
    - Restaurant: {rejected_proposal.get('restaurant', 'Unknown')}
    - Time: {rejected_proposal.get('time', 'Unknown')}
    - Location: {rejected_proposal.get('location', 'Unknown')}
    
    USER'S COMPLETE PREFERENCES:
    - Favorite cuisines: {user_prefs.get('favorite_cuisines', [])}
    - Usual delivery locations: {user_prefs.get('usual_locations', [])}
    - Past successful matches: {len(declining_user_preferences.get('successful_matches', []))}
    - Other preferences: {user_prefs}
    
    AVAILABLE ALTERNATIVES:
    {json.dumps([{
        'restaurant': alt.get('restaurant'),
        'location': alt.get('location'), 
        'time': alt.get('time_requested'),
        'compatibility_score': alt.get('compatibility_score'),
        'matches_cuisine_pref': alt.get('cuisine_in_preferences', False),
        'matches_location_pref': alt.get('location_in_preferences', False)
    } for alt in unique_alternatives], indent=2)}
    
    DECISION CRITERIA:
    1. Only counter if alternative genuinely matches their preferences better
    2. Consider BOTH restaurant preferences AND usual delivery locations
    3. Don't be annoying - max 1 counter-proposal per rejection
    4. Respect their "no" if they seem generally uninterested
    5. Prioritize alternatives that match both cuisine AND location preferences
    
    Return JSON ONLY:
    {{
        "should_counter": true/false,
        "reasoning": "brief explanation including why restaurant + location is better",
        "counter_proposal": {{
            "restaurant": "alternative restaurant name",
            "location": "location", 
            "time": "time",
            "why_better": "why this restaurant + location combo matches their preferences",
            "preference_match": "explain what preferences this matches",
            "user_phone": "phone of other user"
        }} or null
    }}
    """
    
    try:
        response = anthropic_llm.invoke([HumanMessage(content=counter_proposal_prompt)])
        response_text = response.content.strip()
        
        # Clean up response - remove any markdown formatting
        if '```json' in response_text:
            start = response_text.find('{')
            end = response_text.rfind('}') + 1
            response_text = response_text[start:end]
        elif '```' in response_text:
            response_text = response_text.replace('```', '').strip()
        
        if not response_text.startswith('{'):
            import re
            json_match = re.search(r'\{.*\}', response_text, re.DOTALL)
            if json_match:
                response_text = json_match.group()
        
        result = json.loads(response_text)
        
        # Validate the response structure
        if not isinstance(result.get('should_counter'), bool):
            raise ValueError("Invalid should_counter value")
        
        # Add the alternatives we found for backward compatibility
        result['alternatives_found'] = unique_alternatives
        
        print(f"✅ Location-aware counter-proposal decision: {result.get('should_counter', False)}")
        print(f"   Reasoning: {result.get('reasoning', 'No reasoning provided')}")
        
        return result
        
    except Exception as e:
        print(f"❌ Counter-proposal analysis failed: {e}")
        
        # FALLBACK: Location-aware heuristic decision
        return location_aware_fallback_counter_proposal(
            rejected_proposal, declining_user_preferences, unique_alternatives
        )

def get_nearby_locations(primary_location: str) -> List[str]:
    """Get nearby/alternative locations based on campus geography"""
    
    location_clusters = {
        "Richard J Daley Library": ["Student Center East", "University Hall"],
        "Student Center East": ["Richard J Daley Library", "Student Center West"], 
        "Student Center West": ["Student Center East", "Student Services Building"],
        "Student Services Building": ["Student Center West", "University Hall"],
        "University Hall": ["Richard J Daley Library", "Student Services Building"]
    }
    
    return location_clusters.get(primary_location, ["Richard J Daley Library"])

def location_aware_fallback_counter_proposal(
    rejected_proposal: Dict, 
    user_prefs: Dict, 
    alternatives: List[Dict]
) -> Dict:
    """Location-aware fallback logic when Claude analysis fails"""
    
    if not alternatives:
        return {
            "should_counter": False,
            "counter_proposal": None,
            "alternatives_found": [],
            "reasoning": "No alternatives available"
        }
    
    # Get user's stored preferences
    favorite_cuisines = user_prefs.get('preferences', {}).get('favorite_cuisines', [])
    usual_locations = user_prefs.get('preferences', {}).get('usual_locations', [])
    
    # Find best alternative that matches BOTH cuisine and location preferences
    best_alternative = None
    best_score = 0
    
    for alt in alternatives:
        score = 0
        alt_restaurant = alt.get('restaurant', '').lower()
        alt_location = alt.get('location', '')
        
        # Restaurant preference match (50%)
        for fav_cuisine in favorite_cuisines:
            if fav_cuisine.lower() in alt_restaurant or alt_restaurant in fav_cuisine.lower():
                score += 0.5
                break
        
        # Location preference match (30%)
        if alt_location in usual_locations:
            score += 0.3
        
        # Compatibility score (20%)
        score += 0.2 * alt.get('compatibility_score', 0)
        
        if score > best_score:
            best_score = score
            best_alternative = alt
    
    # Only counter-propose if score is good (>0.4)
    if best_alternative and best_score > 0.4:
        location_match = best_alternative.get('location') in usual_locations
        cuisine_match = any(
            fav.lower() in best_alternative.get('restaurant', '').lower() 
            for fav in favorite_cuisines
        )
        
        preference_explanation = []
        if cuisine_match:
            preference_explanation.append("your favorite cuisine")
        if location_match:
            preference_explanation.append("your usual delivery location")
        
        preference_text = " and ".join(preference_explanation) if preference_explanation else "better compatibility"
        
        return {
            "should_counter": True,
            "reasoning": f"Found alternative matching stored preferences (score: {best_score:.2f})",
            "counter_proposal": {
                "restaurant": best_alternative.get('restaurant'),
                "location": best_alternative.get('location'),
                "time": best_alternative.get('time_requested'),
                "why_better": f"Matches {preference_text}",
                "preference_match": f"Restaurant and location match your preferences",
                "user_phone": best_alternative.get('user_phone')
            },
            "alternatives_found": alternatives
        }
    
    return {
        "should_counter": False,
        "counter_proposal": None,
        "alternatives_found": alternatives,
        "reasoning": f"Available alternatives don't match stored preferences well enough (best score: {best_score:.2f})"
    }

def handle_group_response_no_node(state: PangeaState) -> PangeaState:
    """Handle NO response with intelligent follow-up and location-aware counter-proposals"""
    
    user_phone = state['user_phone']
    
    try:
        # Find the pending negotiation for this user
        pending_negotiations = db.collection('negotiations')\
                               .where('to_user', '==', user_phone)\
                               .where('status', '==', 'pending')\
                               .limit(1).get()
        
        if len(pending_negotiations) > 0:
            negotiation_doc = pending_negotiations[0]
            negotiation_data = negotiation_doc.to_dict()
            rejected_proposal = negotiation_data.get('proposal', {})
            
            # Update negotiation status to rejected
            negotiation_doc.reference.update({'status': 'rejected'})
            
            # LEARN from rejection
            learn_from_rejection(user_phone, rejected_proposal)
            
            # Use tool pattern - let Claude decide if user preferences are needed
            user_prefs = get_user_preferences(user_phone)
            
            # ENHANCED: Use location-aware generate_counter_proposal (finds alternatives AND decides)
            counter_result = generate_counter_proposal(
                rejected_proposal=rejected_proposal,
                declining_user_preferences=user_prefs,
                user_phone=user_phone
            )
            
            # Check if we found alternatives and should counter-propose
            alternatives = counter_result.get('alternatives_found', [])
            
            if counter_result.get('should_counter', False) and counter_result.get('counter_proposal'):
                # Send intelligent alternative suggestion using counter_proposal data
                counter_prop = counter_result['counter_proposal']
                
                # Enhanced message with location context
                location_context = ""
                if counter_prop.get('preference_match'):
                    location_context = f" {counter_prop.get('preference_match')}!"
                
                alt_message = f"""No worries about {rejected_proposal.get('restaurant', 'that')}! 

How about {counter_prop.get('restaurant')} at {counter_prop.get('location')} instead? {counter_prop.get('why_better', 'It might be a better fit!')}{location_context}

I found someone wanting {counter_prop.get('restaurant')} at {counter_prop.get('location')} around {counter_prop.get('time')}. 

Want me to see if they'd like you to join? 😊 (Just reply YES if interested)"""
                
                send_friendly_message(user_phone, alt_message, message_type="location_aware_counter_proposal")
                
                # Store the alternative suggestion for potential follow-up
                state['alternative_suggestions'] = alternatives
                
            else:
                # Standard acknowledgment
                send_friendly_message(user_phone, "No worries! 👍 Maybe next time. I'll keep an eye out for other opportunities for you.", message_type="general")
            
            # Notify the original requesting user with enhanced feedback
            requesting_user = negotiation_data['from_user']
            restaurant = rejected_proposal.get('restaurant', 'food')
            
            # Check if there are other users in the group who said YES and need solo orders
            try:
                # Find the group this user was part of
                user_groups = db.collection('active_groups')\
                              .where('members', 'array_contains', user_phone)\
                              .where('status', '==', 'pending_responses')\
                              .get()
                
                for group in user_groups:
                    group_data = group.to_dict()
                    group_members = group_data.get('members', [])
                    
                    # Check if other members said YES (have accepted negotiations)
                    for member_phone in group_members:
                        if member_phone != user_phone:  # Don't check the user who just said NO
                            # Check if this member has any accepted negotiations
                            accepted_negs = db.collection('negotiations')\
                                            .where('to_user', '==', member_phone)\
                                            .where('status', '==', 'accepted')\
                                            .get()
                            
                            if len(accepted_negs) > 0:
                                print(f"📦 Found user {member_phone} who said YES, starting solo order for them")
                                
                                # Start solo order for the YES user
                                solo_message_yes = f"""Hey! The other person decided to pass on the group order, but you said YES! 🍔
                                
No worries - you can still get your {restaurant} order. Here's how:

**Quick steps:**
1. Order directly from {restaurant} (app/website/phone) - choose PICKUP
2. Come back with your confirmation number AND what you ordered
3. Pay your share: $3.50 💳

Let me know when you've placed your order!"""
                                
                                send_friendly_message(member_phone, solo_message_yes, message_type="solo_order_start")
                                
                    # Update group status to indicate it's been resolved
                    group.reference.update({'status': 'resolved_mixed_responses'})
                    
            except Exception as group_check_e:
                print(f"⚠️ Could not check for other group members: {group_check_e}")
            
            # Process the original requester as a solo order
            solo_message = f"""Hey! 👋 Great news - found someone nearby who's also craving {restaurant}, so you can split the delivery fee!

Your share will only be $2.50-$3.50 instead of the full amount. Pretty sweet deal 🙌

**Quick steps to get your food:**
1. Order directly from {restaurant} (app/website/phone) - just make sure to choose PICKUP, not delivery
2. Come back here with your confirmation number or name for the order AND what you ordered

Once everyone's ready, your payment will be $3.50 💳

Let me know if you need any help!"""
            
            send_friendly_message(requesting_user, solo_message, message_type="general")
            
            # Start solo order process for the original requester
            try:
                solo_group_id = f"solo_{str(uuid.uuid4())}"
                delivery_time = rejected_proposal.get('time', 'now')
                
                session_data = {
                    'user_phone': requesting_user,  # FIXED: Use 'user_phone' not 'phone_number'
                    'group_id': solo_group_id,
                    'restaurant': restaurant,       # FIXED: Use 'restaurant' not 'restaurant_name'
                    'group_size': 1,
                    'delivery_time': delivery_time,
                    'order_stage': 'need_order_number',  # FIXED: Use correct stage name
                    'pickup_location': RESTAURANTS.get(restaurant, {}).get('address', 'Campus'),
                    'payment_link': 'https://buy.stripe.com/test_placeholder',
                    'order_session_id': str(uuid.uuid4()),
                    'created_at': datetime.now(),
                    'order_number': None,
                    'customer_name': None
                }
                
                # FIXED: Call update_order_session directly (not .invoke())
                from pangea_order_processor import update_order_session
                update_order_session(requesting_user, session_data)
                print(f"✅ Started solo order process for {requesting_user} after partner declined")
                
            except Exception as e:
                print(f"❌ Failed to start solo order process for {requesting_user}: {e}")
                # Fallback message
                fallback_message = f"The person I reached out to for {restaurant} can't join this time. I'm still looking for other matches and will update you soon! 🔍"
                send_friendly_message(requesting_user, fallback_message, message_type="general")
            
            print(f"✅ Group declined with location-aware follow-up: {negotiation_data['negotiation_id']}")
            
        else:
            # No pending negotiation found
            message = "I don't see any pending group invitations for you right now. Want to start a new food order?"
            send_friendly_message(user_phone, message, message_type="general")
            
    except Exception as e:
        print(f"Error handling location-aware group response NO: {e}")
        error_message = "Something went wrong processing your response. Can you try again?"
        send_friendly_message(user_phone, error_message, message_type="general")
    
    state['messages'].append(AIMessage(content="Location-aware group response NO processed"))
    return state

def notify_compatible_users_of_active_groups(
    active_group_data: Dict,
    max_notifications: int = 3,
    compatibility_threshold: float = 0.7
) -> Dict:
    """
    Notify users who would be compatible with an actively forming group.
    
    Only sends notifications to users with high compatibility and appropriate
    location/timing patterns. Avoids spam by checking notification history.
    
    Args:
        active_group_data: The group that's forming (restaurant, location, time, members)
        max_notifications: Max users to notify (prevent overwhelming the group)
        compatibility_threshold: Minimum compatibility score to notify (0.7 = high match)
        
    Returns:
        Dict with notification results and user responses
    """
    try:
        restaurant = active_group_data.get('restaurant', '')
        location = active_group_data.get('location', '')
        time = active_group_data.get('time', 'now')
        current_members = active_group_data.get('current_members', [])
        group_id = active_group_data.get('group_id', '')
        
        print(f"🔔 Finding compatible users for {restaurant} at {location} ({time})")
        print(f"🔔 Current group has {len(current_members)} members")
        
        # Get all users to check compatibility
        users_ref = db.collection('users')
        all_users = users_ref.get()
        
        compatible_users = []
        notifications_sent = 0
        
        for user_doc in all_users:
            if notifications_sent >= max_notifications:
                break
                
            user_data = user_doc.to_dict()
            user_phone = user_data.get('phone', user_doc.id)
            
            # Skip if user is already in the group
            if user_phone in current_members:
                continue
            
            # Skip if user is already in active negotiations
            try:
                active_negotiations = db.collection('negotiations')\
                                      .where('status', '==', 'pending')\
                                      .get()
                
                user_in_negotiations = False
                for neg in active_negotiations:
                    neg_data = neg.to_dict()
                    if (neg_data.get('from_user') == user_phone or 
                        neg_data.get('to_user') == user_phone):
                        user_in_negotiations = True
                        break
                
                if user_in_negotiations:
                    print(f"🔔 Skipping {user_phone}: already in active negotiations")
                    continue
                    
            except Exception as e:
                print(f"⚠️ Error checking negotiations for {user_phone}: {e}")
            
            # Check if user should be notified
            should_notify = check_user_compatibility_for_notification(
                user_phone, user_data, active_group_data, compatibility_threshold
            )
            
            if should_notify:
                # Send notification
                notification_sent = send_proactive_group_notification(
                    user_phone, user_data, active_group_data
                )
                
                if notification_sent:
                    compatible_users.append({
                        'user_phone': user_phone,
                        'notification_sent': True,
                        'compatibility_reason': should_notify.get('reason', 'high_compatibility')
                    })
                    notifications_sent += 1
                    
                    # Track notification in Firebase
                    track_proactive_notification(user_phone, active_group_data)
        
        return {
            'notifications_sent': notifications_sent,
            'compatible_users': compatible_users,
            'group_id': group_id,
            'status': 'success'
        }
        
    except Exception as e:
        print(f"❌ Error in notify_compatible_users_of_active_groups: {e}")
        return {
            'notifications_sent': 0,
            'compatible_users': [],
            'status': 'error',
            'error': str(e)
        }

def check_user_compatibility_for_notification(user_phone: str, user_data: Dict, active_group_data: Dict, threshold: float) -> Dict:
    """Check if user should be notified about active group - smart filtering logic"""
    
    # 0. Check if user is silently matched (solo order customer who shouldn't get notifications)
    if user_data.get('silent_match', False):
        print(f"🔇 Skipping {user_phone}: marked for silent matching (solo order customer)")
        return False
    
    # 1. Check notification fatigue (max 2 per day)
    if check_notification_fatigue(user_phone):
        return False
    
    # 2. Check if user recently declined similar opportunities
    if check_recent_declines(user_phone, active_group_data):
        return False
    
    # 3. Calculate compatibility score
    compatibility_score = calculate_proactive_compatibility(user_data, active_group_data)
    
    if compatibility_score < threshold:
        return False
    
    # 4. Check location intelligence
    location_match = check_location_intelligence(user_data, active_group_data)
    
    if not location_match:
        return False
    
    # 5. Check timing patterns
    timing_match = check_timing_patterns(user_data, active_group_data)
    
    if not timing_match:
        return False
    
    return {
        'should_notify': True,
        'compatibility_score': compatibility_score,
        'reason': f'High compatibility ({compatibility_score:.2f}) + location match + timing match'
    }

def check_notification_fatigue(user_phone: str) -> bool:
    """Check if user has received too many notifications today"""
    try:
        today = datetime.now().date()
        
        # Count notifications sent today
        notifications_today = db.collection('notification_history')\
                               .where('user_phone', '==', user_phone)\
                               .where('date', '==', today)\
                               .where('type', '==', 'proactive_group')\
                               .get()
        
        return len(notifications_today) >= 2  # Max 2 per day
        
    except Exception as e:
        print(f"❌ Error checking notification fatigue: {e}")
        return True  # Default to not notifying if error

def check_recent_declines(user_phone: str, active_group_data: Dict) -> bool:
    """Check if user recently declined similar opportunities"""
    try:
        # Check last 24 hours for declines
        yesterday = datetime.now() - timedelta(hours=24)
        
        recent_declines = db.collection('notification_history')\
                           .where('user_phone', '==', user_phone)\
                           .where('timestamp', '>=', yesterday)\
                           .where('response', '==', 'declined')\
                           .get()
        
        restaurant = active_group_data.get('restaurant', '')
        location = active_group_data.get('location', '')
        
        # Check if declined similar restaurant/location combo
        for decline in recent_declines:
            decline_data = decline.to_dict()
            if (decline_data.get('restaurant') == restaurant and 
                decline_data.get('location') == location):
                return True
        
        return False
        
    except Exception as e:
        print(f"❌ Error checking recent declines: {e}")
        return False

def calculate_proactive_compatibility(user_data: Dict, active_group_data: Dict) -> float:
    """Calculate compatibility score for proactive notifications (higher threshold)"""
    
    preferences = user_data.get('preferences', {})
    successful_matches = user_data.get('successful_matches', [])
    
    restaurant = active_group_data.get('restaurant', '')
    location = active_group_data.get('location', '')
    
    score = 0.0
    
    # 1. Restaurant preference match (40% weight)
    favorite_cuisines = preferences.get('favorite_cuisines', [])
    if restaurant in favorite_cuisines:
        score += 0.4
    elif any(cuisine.lower() in restaurant.lower() for cuisine in favorite_cuisines):
        score += 0.3
    
    # 2. Historical success at this restaurant (30% weight)
    for match in successful_matches:
        if match.get('restaurant') == restaurant:
            score += 0.3
            break
    
    # 3. Location familiarity (20% weight)
    usual_locations = preferences.get('usual_locations', [])
    if location in usual_locations:
        score += 0.2
    
    # 4. Overall success rate (10% weight)
    satisfaction_scores = user_data.get('satisfaction_scores', [])
    if satisfaction_scores:
        avg_satisfaction = sum(satisfaction_scores) / len(satisfaction_scores)
        if avg_satisfaction >= 8:  # High satisfaction users
            score += 0.1
    
    return min(score, 1.0)

def check_location_intelligence(user_data: Dict, active_group_data: Dict) -> bool:
    """Check if user's location patterns match the group location"""
    
    preferences = user_data.get('preferences', {})
    location = active_group_data.get('location', '')
    time = active_group_data.get('time', 'now')
    
    # 1. Check usual locations
    usual_locations = preferences.get('usual_locations', [])
    if location in usual_locations:
        return True
    
    # 2. Check successful patterns at this location
    successful_patterns = user_data.get('successful_patterns', [])
    for pattern in successful_patterns:
        if pattern.get('location') == location:
            return True
    
    # 3. Check interaction history for this location
    interactions = user_data.get('interactions', [])
    for interaction in interactions:
        if (interaction.get('location') == location and 
            interaction.get('interaction_type') == 'successful_group_order'):
            return True
    
    return False

def check_timing_patterns(user_data: Dict, active_group_data: Dict) -> bool:
    """Check if user's timing patterns match the group timing"""
    
    preferences = user_data.get('preferences', {})
    time = active_group_data.get('time', 'now')
    # Convert DatetimeWithNanoseconds to string if needed
    time_str = convert_time_to_string(time)
    
    # 1. Check preferred times
    preferred_times = preferences.get('preferred_times', [])
    if time in preferred_times:
        return True
    
    # 2. Check current time against historical patterns
    current_hour = datetime.now().hour
    
    # Look at successful patterns for similar times
    successful_patterns = user_data.get('successful_patterns', [])
    for pattern in successful_patterns:
        pattern_time = pattern.get('time', '')
        # Simple time matching - could be more sophisticated
        if ('lunch' in time_str.lower() and 'lunch' in pattern_time.lower()) or \
           ('dinner' in time_str.lower() and 'dinner' in pattern_time.lower()) or \
           ('now' in time_str.lower() and 11 <= current_hour <= 14):  # Lunch hours
            return True
    
    return True  # Default to True for flexible timing

def send_proactive_group_notification(user_phone: str, user_data: Dict, active_group_data: Dict) -> bool:
    """Send personalized notification about active group"""
    
    restaurant = active_group_data.get('restaurant', '')
    location = active_group_data.get('location', '')
    time = active_group_data.get('time', 'now')
    group_size = len(active_group_data.get('current_members', []))
    
    # Get user's history for personalization
    successful_matches = user_data.get('successful_matches', [])
    preferences = user_data.get('preferences', {})
    
    # Create personalized message
    personalization = ""
    if any(match.get('restaurant') == restaurant for match in successful_matches):
        personalization = f"You ordered from there last week around this time. "
    elif restaurant.lower() in [cuisine.lower() for cuisine in preferences.get('favorite_cuisines', [])]:
        personalization = f"I know you love {restaurant}! "
    
    message = f"""Hey! 🍕 There's a {restaurant} group forming at {location} in {time}. 
{personalization}
{group_size} people so far - if you join, we'll have {group_size + 1} people total! Reply YES to jump in!"""
    
    return send_friendly_message(user_phone, message, message_type="proactive_group")

def track_proactive_notification(user_phone: str, active_group_data: Dict):
    """Track proactive notification in Firebase for analytics and spam prevention"""
    
    try:
        notification_record = {
            'user_phone': user_phone,
            'type': 'proactive_group',
            'restaurant': active_group_data.get('restaurant', ''),
            'location': active_group_data.get('location', ''),
            'time': active_group_data.get('time', ''),
            'group_id': active_group_data.get('group_id', ''),
            'timestamp': datetime.now(),
            'date': datetime.now().date(),
            'response': 'pending'  # Will be updated when user responds
        }
        
        db.collection('notification_history').add(notification_record)
        
    except Exception as e:
        print(f"❌ Error tracking proactive notification: {e}")

def check_pending_proactive_notifications(user_phone: str) -> Dict:
    """Check if user has pending proactive notifications"""
    try:
        # Check for notifications sent in the last 30 minutes that haven't been responded to
        recent_cutoff = datetime.now() - timedelta(minutes=30)
        
        pending_notifications = db.collection('notification_history')\
                                .where('user_phone', '==', user_phone)\
                                .where('type', '==', 'proactive_group')\
                                .where('timestamp', '>=', recent_cutoff)\
                                .where('response', '==', 'pending')\
                                .limit(1).get()
        
        if len(pending_notifications) > 0:
            return pending_notifications[0].to_dict()
        
        return None
        
    except Exception as e:
        print(f"❌ Error checking pending proactive notifications: {e}")
        return None

def update_proactive_notification_response(user_phone: str, response: str):
    """Update proactive notification with user's response"""
    try:
        recent_cutoff = datetime.now() - timedelta(minutes=30)
        
        pending_notifications = db.collection('notification_history')\
                                .where('user_phone', '==', user_phone)\
                                .where('type', '==', 'proactive_group')\
                                .where('timestamp', '>=', recent_cutoff)\
                                .where('response', '==', 'pending')\
                                .limit(1).get()
        
        if len(pending_notifications) > 0:
            notification_doc = pending_notifications[0]
            notification_doc.reference.update({
                'response': response,
                'response_timestamp': datetime.now()
            })
            print(f"✅ Updated proactive notification response: {response}")
            
    except Exception as e:
        print(f"❌ Error updating proactive notification response: {e}")



def learn_from_rejection(rejecting_user: str, rejected_proposal: Dict, rejection_reason: str = None):
    """Learn from rejections to improve future matching"""
    
    update_user_memory(phone_number=rejecting_user, interaction_data={
        'interaction_type': 'proposal_rejection',
        'rejected_restaurant': rejected_proposal.get('restaurant'),
        'rejected_time': rejected_proposal.get('time'),
        'rejected_location': rejected_proposal.get('location'),
        'rejection_reason': rejection_reason or 'not_specified',
        'timestamp': datetime.now(),
        'proposal_compatibility_score': rejected_proposal.get('compatibility_score', 0.0)
    })


def send_friendly_message(phone_number: str, message: str, message_type: str = "general") -> bool:
    """
    Send contextual, friendly SMS messages using Claude 4's enhanced conversational abilities.
    
    Automatically adapts tone and content based on message type and user history.
    
    Args:
        phone_number: Recipient's phone number
        message: Base message content
        message_type: Type of message for tone adaptation
            - "welcome": First-time user greeting
            - "morning_checkin": Proactive morning outreach  
            - "match_found": Successful group formation
            - "negotiation": Group coordination
            - "reminder": Order follow-up
        
    Returns:
        True if message sent successfully, False otherwise
        
    Example:
        success = send_friendly_message(
            "+1234567890", 
            "Found a great group for Thai food!",
            message_type="match_found"
        )
    """
    print(f"📞 SEND_FRIENDLY_MESSAGE called: to={phone_number}, type={message_type}, message_length={len(message)}")
    
    try:
        # Enhance message with Claude 4's conversational abilities
        user_history = get_user_preferences.invoke({"phone_number": phone_number})
        enhanced_message = enhance_message_with_context(message, message_type, user_history)
        
        print(f"📞 About to call Twilio API...")
        message_instance = twilio_client.messages.create(
            body=enhanced_message,
            from_=os.getenv('TWILIO_PHONE_NUMBER'),
            to=phone_number
        )
        print(f"📞 Twilio API returned - SID: {message_instance.sid}, Status: {message_instance.status}")
        
        # Log interaction for learning
        log_interaction(phone_number, {
            'message_sent': enhanced_message,
            'message_type': message_type,
            'timestamp': datetime.now()
        })
        
        return True
    except Exception as e:
        print(f"📞 SMS failed with exception: {e}")
        import traceback
        print(f"📞 Full SMS exception traceback: {traceback.format_exc()}")
        return False

def enhance_message_with_context(message: str, message_type: str, user_history: Dict) -> str:
    """Use Claude 4 to enhance messages with personalization and context"""
    
    # Skip enhancement for FAQ messages - they're already context-aware
    if message_type == "faq":
        return message
    
    # Get user's name or create friendly identifier
    user_name = user_history.get('preferences', {}).get('name', 'friend')
    past_orders = len(user_history.get('successful_matches', []))
    
    # Determine user status more accurately
    is_truly_new_user = past_orders == 0 and not user_history.get('has_used_system', False)
    
    enhancement_prompt = f"""
    Enhance this message to be more friendly and contextual:
    
    Original message: "{message}"
    Message type: {message_type}
    User context: {past_orders} previous successful orders, {"new user" if is_truly_new_user else "returning user"}
    
    Make it sound like a helpful friend who knows them, but keep it brief and natural.
    Add appropriate emojis and personality. Don't be overly enthusiastic.
    {"Do NOT treat them as a new user or mention 'first order' unless they are truly new." if not is_truly_new_user else ""}
    """
    
    try:
        response = anthropic_llm.invoke([HumanMessage(content=enhancement_prompt)])
        enhanced = response.content.strip()
        
        # Fallback to original if enhancement fails
        return enhanced if len(enhanced) > 0 else message
    except:
        return message

@tool
def update_user_memory(phone_number: str, interaction_data: Dict) -> bool:
    """
    Advanced learning system that adapts to user behavior over time.
    
    Uses Claude 4's reasoning capabilities to extract insights and improve
    future matching and communication.
    
    Args:
        phone_number: User's phone number
        interaction_data: Rich interaction data including outcomes, preferences, satisfaction
        
    Returns:
        True if memory updated successfully
        
    Example:
        success = update_user_memory("+1234567890", {
            "interaction_type": "successful_group_order",
            "restaurant": "Thai Garden", 
            "group_members": ["+1111111111", "+2222222222"],
            "satisfaction_score": 9,
            "order_time": "12:30pm",
            "location": "Student Union",
            "total_cost_per_person": 12.50,
            "delivery_time_minutes": 25
        })
    """
    try:
        # Enhanced learning with Claude 4's reasoning
        insights = extract_learning_insights(phone_number, interaction_data)
        
        user_ref = db.collection('users').document(phone_number)
        
        # Get current data
        current_doc = user_ref.get()
        current_data = current_doc.to_dict() if current_doc.exists else {}
        
        # Update with new interaction and insights
        updated_data = {
            'interactions': firestore.ArrayUnion([{
                **interaction_data,
                'timestamp': datetime.now(),
                'insights': insights
            }]),
            'last_updated': datetime.now()
        }
        
        # Update learned preferences
        if insights.get('preference_updates'):
            current_prefs = current_data.get('preferences', {})
            updated_prefs = {**current_prefs, **insights['preference_updates']}
            updated_data['preferences'] = updated_prefs
        
        # Update success patterns
        if interaction_data.get('satisfaction_score', 0) >= 7:
            updated_data['successful_patterns'] = firestore.ArrayUnion([{
                'restaurant': interaction_data.get('restaurant'),
                'time': interaction_data.get('order_time'),
                'location': interaction_data.get('location'),
                'group_size': len(interaction_data.get('group_members', [])),
                'success_score': interaction_data.get('satisfaction_score')
            }])
        
        user_ref.update(updated_data)
        return True
        
    except Exception as e:
        print(f"Memory update failed: {e}")
        return False

def extract_learning_insights(phone_number: str, interaction_data: Dict) -> Dict:
    """Use Claude 4's reasoning to extract insights from user interactions"""
    
    analysis_prompt = f"""
    Analyze this user interaction to extract learning insights:
    
    User: {phone_number}
    Interaction: {json.dumps(interaction_data, default=str)}
    
    Extract insights about:
    1. Food preferences (what they liked/disliked)
    2. Timing preferences (when they prefer to eat)
    3. Social preferences (group size, types of people they work well with)
    4. Price sensitivity
    5. Any patterns or preferences to remember for future matching
    
    Return as JSON with keys: preference_updates, timing_insights, social_insights, price_insights
    """
    
    try:
        response = anthropic_llm.invoke([HumanMessage(content=analysis_prompt)])
        insights = json.loads(response.content)
        return insights
    except:
        return {"insights_extraction": "failed"}

# ===== UNIFIED INTELLIGENT ROUTER =====
def intelligent_router(phone_number: str, new_message: str) -> Dict:
    """
    Super-flexible router that handles ANY user input contextually
    Supports cancellations, corrections, clarifications, and natural conversation
    PLUS all original food ordering logic
    """
    # Get comprehensive user context
    try:
        user_doc = db.collection('users').document(phone_number).get()
        user_data = user_doc.to_dict() if user_doc.exists else {}
        
        # Main system context
        conversation_stage = user_data.get('conversation_stage', 'new')
        missing_info = user_data.get('missing_info', [])
        partial_request = user_data.get('partial_request', {})
        recent_messages = user_data.get('recent_messages', [])
        pending_group_invites = user_data.get('pending_group_invites', [])
        user_preferences = user_data.get('preferences', {})
        
        # Order session context
        from pangea_order_processor import get_user_order_session
        order_session = get_user_order_session(phone_number)
        
        # Check for active groups and negotiations
        pending_negotiations = db.collection('negotiations')\
                              .where('to_user', '==', phone_number)\
                              .where('status', '==', 'pending')\
                              .limit(1).get()
        
        active_groups = db.collection('active_groups')\
                       .where('members', 'array_contains', phone_number)\
                       .where('status', 'in', ['pending_responses', 'forming'])\
                       .limit(1).get()
        
        # Check for proactive notifications
        proactive_notification = check_pending_proactive_notifications(phone_number)
        
    except Exception as e:
        print(f"⚠️ Error getting user context: {e}")
        user_data = {}
        conversation_stage = 'new'
        missing_info = []
        partial_request = {}
        recent_messages = []
        pending_group_invites = []
        user_preferences = {}
        order_session = {}
        pending_negotiations = []
        active_groups = []
        proactive_notification = None
    
    routing_prompt = f"""You are Pangea's super-intelligent router for a food delivery matching service. Your job is to understand EXACTLY what the user wants and route appropriately, handling ANY type of message naturally.

COMPREHENSIVE CONTEXT:
- Phone: {phone_number}
- Message: "{new_message}"
- Conversation stage: {conversation_stage}
- Missing info needed: {missing_info}
- Partial food request: {partial_request}
- Recent messages: {recent_messages[-3:] if recent_messages else []}
- Pending group invites: {pending_group_invites}
- User preferences: {user_preferences}
- Active order session: {bool(order_session)}
- Order session details: {order_session.get('order_stage', 'none') if order_session else 'none'}
- Pending negotiations: {len(pending_negotiations)}
- Active groups: {len(active_groups)}
- Proactive notifications: {bool(proactive_notification)}

PANGEA FOOD ORDERING FLOW:
1. User makes spontaneous food request ("I want McDonald's", "craving pizza")
2. System extracts: restaurant, location, time (now vs later)
3. If missing info → ask for it via handle_incomplete_request
4. If complete → search for group matches
5. If group found → user gets invited, responds yes/no
6. If no group → solo order process begins
7. User places individual order and provides order details
8. Payment and delivery coordination

CONVERSATIONAL INTELLIGENCE RULES (NEW - HIGHEST PRIORITY):

1. **CANCELLATION/STOPPING** - If user says anything like:
   - "cancel", "stop", "never mind", "forget it", "not anymore"
   - "I changed my mind", "don't want it", "cancel my order"
   → ACTION: "cancel_current_process"

2. **CORRECTIONS/CHANGES** - If user says:
   - "Actually I want X instead", "No, I meant Y", "Change to Z"
   - "I said the wrong thing", "Let me try again"
   → ACTION: "handle_correction" 

3. **CLARIFICATION REQUESTS** - If user asks:
   - "What did I order?", "What's my status?", "Where am I?"
   - "What restaurants?", "What locations?", "How much?"
   → ACTION: "provide_clarification"

ORIGINAL ROUTING DECISION TREE (IN PRIORITY ORDER):

4. **NEW FOOD REQUESTS** (HIGHEST PRIORITY FOR FOOD - always start fresh):
   - "I want [food/restaurant]" → start_fresh_request
   - "craving [food]" → start_fresh_request  
   - "hungry for [food]" → start_fresh_request
   - Messages with restaurant + location + time → start_fresh_request
   - Messages with "delivery" or "delivered" + restaurant → start_fresh_request
   - Any comprehensive food request with multiple details → start_fresh_request
   
   IMPORTANT: If a message contains restaurant name AND delivery details (location/time), 
   this is ALWAYS a new food request regardless of any active order session. 
   Even if user also includes name/order details/food items, if restaurant+location+time 
   are present, treat as start_fresh_request.
   Example: "I want McDonald's delivered to the library at 10pm my name is Jake and I ordered a Big Mac" = start_fresh_request
   
   NOTE: Only route to collect_order_description when user provides ONLY food items/names 
   without restaurant+location context in an active order session.

5. **GROUP RESPONSES** - Simple yes/no to invitations:
   - "yes", "sure", "ok", "yeah", "yep" → group_response_yes
   - "no", "nah", "pass", "not interested" → group_response_no

6. **INCOMPLETE FOOD REQUEST** (conversation_stage='incomplete_request'):
   - User providing restaurant name → handle_incomplete_request
   - User providing location → handle_incomplete_request  
   - User providing time → handle_incomplete_request
   - Completely new request → start_fresh_request

7. **ACTIVE ORDER SESSION** (user already in order fulfillment - HIGH PRIORITY):
   When user has active order session, prioritize order continuation over new requests:
   - "pay"/"payment" → handle_payment_request
   - Order numbers/names (like "My name is Mike") → collect_order_number
   - Food descriptions without restaurant/location (like "I want a Big Mac") → collect_order_description
   - Only if message contains full restaurant+location+time → start_fresh_request (clear session)
   
   IMPORTANT: If user is in active order session and says "My name is [name] and I want [food item]",
   this should be treated as order continuation (collect_order_number), NOT a new food request,
   unless the message also contains restaurant name, location, and time together.

8. **OTHER**:
   - Questions about service → faq_answered
   - Morning greeting responses → morning_response
   - Proactive group responses → proactive_group_yes/no

AVAILABLE ROUTING OPTIONS:

MAIN FOOD MATCHING SYSTEM:
- "start_fresh_request" - NEW food craving/restaurant request (clear old data)
- "continue_food_matching" - Complete request ready for group matching
- "handle_incomplete_request" - User filling in missing info (restaurant/location/time)
- "group_response_yes" - User accepted group invitation (YES/sure/ok)
- "group_response_no" - User declined group invitation (NO/pass/nah)
- "morning_response" - Response to morning location/preference prompt
- "preference_update" - Updating food/location preferences
- "faq_answered" - FAQ, help, non-food chat

ORDER FULFILLMENT SYSTEM:
- "collect_order_number" - User providing order confirmation/name after placing order
- "collect_order_description" - User providing what they ordered
- "handle_payment_request" - User wants to pay (texted "PAY")
- "redirect_to_payment" - Remind about payment options
- "need_order_first" - User trying to pay without order details

NEW CONVERSATIONAL SYSTEM:
- "cancel_current_process" - User wants to stop/cancel
- "handle_correction" - User wants to change something
- "provide_clarification" - Explain current status/options
- "proactive_group_yes" - Accept proactive group notification
- "proactive_group_no" - Decline proactive group notification

CONTEXTUAL RESPONSES - Smart contextual handling:
- If they have pending invitations but ask about restaurants → explain current invitation first
- If they're in order process but ask about new food → acknowledge current order, clarify intent
- If they seem confused → provide helpful context about where they are in the process

Return JSON with:
{{
    "action": "[routing_option]",
    "handler": "[specific_function_to_call]", 
    "reasoning": "detailed explanation of routing decision",
    "confidence": 0.95,
    "context_used": ["list", "of", "context", "factors", "considered"],
    "user_intent": "what you think the user actually wants",
    "suggested_response": "optional: suggest what the system should say to user"
}}

Remember: Be conversational and helpful. If the user's intent is unclear, choose the action that would be most helpful to them based on context. Always prioritize user autonomy - they can change their mind, cancel, or start over at any time.
"""
    
    try:
        llm = ChatAnthropic(model="claude-opus-4-20250514", temperature=0.1, max_tokens=4096)
        response = llm.invoke([HumanMessage(content=routing_prompt)])
        response_text = response.content.strip()
        
        print(f"🤖 Raw Claude response: {response_text[:200]}...")
        
        # Clean up response - remove any markdown formatting
        if '```json' in response_text:
            # Extract JSON from markdown code block
            start = response_text.find('{')
            end = response_text.rfind('}') + 1
            response_text = response_text[start:end]
        elif '```' in response_text:
            # Remove any code block markers
            response_text = response_text.replace('```', '').strip()
        
        # Try to find JSON in the response if it doesn't start with {
        if not response_text.startswith('{'):
            import re
            json_match = re.search(r'\{.*\}', response_text, re.DOTALL)
            if json_match:
                response_text = json_match.group()
        
        print(f"🔍 Cleaned response for parsing: {response_text[:100]}...")
        result = json.loads(response_text)
        
        print(f"🎯 Enhanced Router Decision:")
        print(f"   Action: {result['action']}")
        print(f"   Handler: {result.get('handler', 'default')}")
        print(f"   Confidence: {result.get('confidence', '?')}")
        print(f"   Reasoning: {result.get('reasoning', '')}")
        print(f"   Context: {result.get('context_used', [])}")
        print(f"   User Intent: {result.get('user_intent', 'unclear')}")
        
        return result
        
    except Exception as e:
        print(f"❌ Enhanced router failed: {e}")
        # Enhanced fallback logic with conversational support
        message_lower = new_message.lower().strip()
        
        print(f"🔄 Using enhanced fallback routing logic:")
        print(f"   Conversation stage: {conversation_stage}")
        print(f"   Missing info: {missing_info}")
        print(f"   Has partial request: {bool(partial_request)}")
        print(f"   Order session: {bool(order_session)}")
        print(f"   Pending invites: {bool(pending_group_invites)}")
        print(f"   Pending negotiations: {len(pending_negotiations)}")
        print(f"   Active groups: {len(active_groups)}")
        
        # Priority 1: NEW - Conversational commands
        if any(word in message_lower for word in ['cancel', 'stop', 'never mind', 'forget it']):
            fallback_action = "cancel_current_process"
            print(f"   🛑 Detected cancellation request")
        elif any(word in message_lower for word in ['actually', 'no i meant', 'change to', 'instead']):
            fallback_action = "handle_correction"
            print(f"   🔄 Detected correction request")
        elif any(phrase in message_lower for phrase in ['what did i', 'my status', 'where am i', 'what restaurants']):
            fallback_action = "provide_clarification"
            print(f"   ❓ Detected clarification request")
            
        # Priority 2: Handle incomplete requests (user responding with missing info)
        elif conversation_stage == 'incomplete_request' and missing_info and partial_request:
            fallback_action = "handle_incomplete_request"
            print(f"   🎯 Detected incomplete request follow-up")
            
        # Priority 3: Order session handling
        elif order_session:
            if any(word in message_lower for word in ['pay', 'payment']):
                fallback_action = "handle_payment_request"
                print(f"   💳 Detected payment request")
            # Check if this is actually a new food request despite having an order session
            elif (any(word in message_lower for word in ['want', 'craving', 'hungry', 'order', 'get', 'need']) and
                  ('delivered' in message_lower or 'delivery' in message_lower or 
                   any(location in message_lower for location in ['library', 'union', 'student', 'campus', 'dorm']) or
                   any(time in message_lower for time in ['pm', 'am', 'noon', 'midnight', 'tonight', 'later']))):
                fallback_action = "start_fresh_request"
                print(f"   🍔 Detected new food request (overriding order session)")
            else:
                fallback_action = "collect_order_number"
                print(f"   📋 Detected order continuation")
                
        # Priority 4: Group responses (check both old and new systems)
        elif pending_group_invites or len(pending_negotiations) > 0 or len(active_groups) > 0:
            if any(word in message_lower for word in ['yes', 'y', 'sure', 'ok']):
                fallback_action = "group_response_yes"
                print(f"   ✅ Detected group YES response")
            elif any(word in message_lower for word in ['no', 'n', 'pass', 'nah']):
                fallback_action = "group_response_no"
                print(f"   ❌ Detected group NO response")
            else:
                fallback_action = "faq_answered" 
                print(f"   💬 Detected general conversation with pending invites")
                
        # Priority 5: Proactive notifications
        elif proactive_notification:
            if any(word in message_lower for word in ['yes', 'y', 'sure', 'ok']):
                fallback_action = "proactive_group_yes"
                print(f"   ✅ Detected proactive group YES")
            elif any(word in message_lower for word in ['no', 'n', 'pass', 'nah']):
                fallback_action = "proactive_group_no"
                print(f"   ❌ Detected proactive group NO")
            else:
                fallback_action = "faq_answered"
                print(f"   💬 General conversation with proactive notification")
                
        # Priority 6: New food requests
        elif any(word in message_lower for word in ['want', 'craving', 'hungry', 'order', 'get', 'need']):
            fallback_action = "start_fresh_request"
            print(f"   🍔 Detected new food request")
            
        # Priority 7: Morning responses
        elif conversation_stage == 'morning_greeting_sent':
            fallback_action = "morning_response"
            print(f"   🌅 Detected morning response")
            
        # Default: FAQ/General conversation
        else:
            fallback_action = "faq_answered"
            print(f"   💬 Defaulting to FAQ/general conversation")
            
        return {
            "action": fallback_action,
            "handler": "enhanced_fallback_routing",
            "reasoning": f"Enhanced router failed, using context fallback: {fallback_action}",
            "confidence": 0.3,
            "context_used": ["fallback_keywords", "conversation_stage", "context_analysis", "conversational_intelligence"],
            "user_intent": f"Fallback detected: {fallback_action}",
            "suggested_response": None
        }
# ===== 3. ADD NEW HANDLER NODES =====


def handle_cancellation_node(state: PangeaState) -> PangeaState:
    """Handle when user wants to cancel current process"""
    
    user_phone = state['user_phone']
    
    # Clear any active processes
    try:
        # Clear order session
        from pangea_order_processor import clear_old_order_session
        clear_old_order_session(user_phone)
        
        # Cancel pending negotiations
        pending_negotiations = db.collection('negotiations')\
                               .where('to_user', '==', user_phone)\
                               .where('status', '==', 'pending')\
                               .get()
        
        for neg in pending_negotiations:
            neg.reference.update({'status': 'cancelled_by_user'})
        
        # Remove from active groups
        user_groups = db.collection('active_groups')\
                       .where('members', 'array_contains', user_phone)\
                       .get()
        
        for group in user_groups:
            group.reference.delete()
        
        # Clear user state
        db.collection('users').document(user_phone).update({
            'conversation_stage': 'available',
            'missing_info': firestore.DELETE_FIELD,
            'partial_request': firestore.DELETE_FIELD,
            'last_updated': firestore.SERVER_TIMESTAMP
        })
        
        message = "No problem! I've cancelled everything. Whenever you're ready for food, just let me know what you're craving! 😊"
        
    except Exception as e:
        print(f"❌ Error during cancellation: {e}")
        message = "Got it! I've cancelled your current request. You can start fresh anytime!"
    
    send_friendly_message(user_phone, message, message_type="cancellation")
    state['messages'].append(AIMessage(content=message))
    return state

def handle_correction_node(state: PangeaState) -> PangeaState:
    """Handle when user wants to correct/change something"""
    
    user_phone = state['user_phone']
    user_message = state['messages'][-1].content
    
    # Use Claude to understand what they want to change
    correction_prompt = f"""
    The user wants to make a correction. Understand what they want to change:
    
    User message: "{user_message}"
    
    Common correction patterns:
    - "Actually I want X instead" → changing restaurant
    - "No, I meant location Y" → changing location  
    - "Change the time to Z" → changing time
    - "Let me start over" → complete restart
    
    Return JSON:
    {{
        "correction_type": "restaurant/location/time/complete_restart",
        "new_value": "what they want to change to",
        "should_restart": true/false
    }}
    """
    
    try:
        response = anthropic_llm.invoke([HumanMessage(content=correction_prompt)])
        correction_data = json.loads(response.content)
        
        correction_type = correction_data.get('correction_type')
        new_value = correction_data.get('new_value')
        should_restart = correction_data.get('should_restart', False)
        
        if should_restart or correction_type == 'complete_restart':
            # Clear everything and let them start fresh
            from pangea_order_processor import clear_old_order_session
            clear_old_order_session(user_phone)
            
            message = "No problem! Let's start fresh. What are you craving and where would you like it delivered?"
        else:
            # Apply specific correction
            message = f"Got it! I'll update your {correction_type} to {new_value}. Let me find new matches..."
            
            # Update their request and restart matching
            user_doc = db.collection('users').document(user_phone)
            user_doc.update({
                f'partial_request.{correction_type}': new_value,
                'conversation_stage': 'correction_applied',
                'last_updated': firestore.SERVER_TIMESTAMP
            })
            
    except Exception as e:
        print(f"❌ Error processing correction: {e}")
        message = "I want to make sure I get this right. Can you tell me exactly what you'd like to change?"
    
    send_friendly_message(user_phone, message, message_type="correction")
    state['messages'].append(AIMessage(content=message))
    return state

def provide_clarification_node(state: PangeaState) -> PangeaState:
    """Provide helpful clarification about current status"""
    
    user_phone = state['user_phone']
    
    # Get full context
    user_doc = db.collection('users').document(user_phone).get()
    user_data = user_doc.to_dict() if user_doc.exists else {}
    
    from pangea_order_processor import get_user_order_session
    order_session = get_user_order_session(user_phone)
    
    # Check for pending items
    pending_negotiations = db.collection('negotiations')\
                          .where('to_user', '==', user_phone)\
                          .where('status', '==', 'pending')\
                          .get()
    
    active_groups = db.collection('active_groups')\
                   .where('members', 'array_contains', user_phone)\
                   .where('status', 'in', ['pending_responses', 'forming'])\
                   .get()
    
    # Build status message
    status_parts = []
    
    if order_session:
        restaurant = order_session.get('restaurant', 'Unknown')
        order_stage = order_session.get('order_stage', 'unknown')
        group_size = order_session.get('group_size', 1)
        
        if order_stage == 'need_order_number':
            status_parts.append(f"📋 You're in a {restaurant} group ({group_size} people)")
            status_parts.append("🔄 Next step: Provide your order number or name")
        elif order_stage == 'ready_to_pay':
            status_parts.append(f"✅ Your {restaurant} order is ready")
            status_parts.append("💳 Next step: Text 'PAY' when ready")
        elif order_stage == 'payment_initiated':
            status_parts.append(f"💳 Payment sent for {restaurant}")
            status_parts.append("⏳ Waiting for delivery coordination")
    
    elif len(pending_negotiations) > 0:
        neg_data = pending_negotiations[0].to_dict()
        restaurant = neg_data.get('proposal', {}).get('restaurant', 'food')
        status_parts.append(f"🤝 You have a pending invitation for {restaurant}")
        status_parts.append("🔄 Next step: Reply YES or NO")
    
    elif len(active_groups) > 0:
        group_data = active_groups[0].to_dict()
        restaurant = group_data.get('restaurant', 'food')
        status_parts.append(f"👥 You're in a forming {restaurant} group")
        status_parts.append("⏳ Waiting for group coordination")
    
    else:
        conversation_stage = user_data.get('conversation_stage', 'available')
        if conversation_stage == 'incomplete_request':
            missing_info = user_data.get('missing_info', [])
            status_parts.append("📝 You started a food request")
            status_parts.append(f"🔄 Next step: Provide {', '.join(missing_info)}")
        else:
            status_parts.append("😊 You're all set! No active orders")
            status_parts.append("🍕 Say what you're craving to get started")
    
    # Add helpful options
    status_parts.append("\n📋 **You can always:**")
    status_parts.append("• Cancel: 'cancel' or 'stop'")
    status_parts.append("• Start over: 'I want [restaurant] at [location]'")
    status_parts.append("• Get help: 'what restaurants are available?'")
    
    message = "\n".join(status_parts)
    
    send_friendly_message(user_phone, message, message_type="status_clarification")
    state['messages'].append(AIMessage(content=message))
    return state

def provide_clarification_node(state: PangeaState) -> PangeaState:
    """Provide helpful clarification about current status"""
    
    user_phone = state['user_phone']
    
    # Get full context
    user_doc = db.collection('users').document(user_phone).get()
    user_data = user_doc.to_dict() if user_doc.exists else {}
    
    from pangea_order_processor import get_user_order_session
    order_session = get_user_order_session(user_phone)
    
    # Check for pending items
    pending_negotiations = db.collection('negotiations')\
                          .where('to_user', '==', user_phone)\
                          .where('status', '==', 'pending')\
                          .get()
    
    active_groups = db.collection('active_groups')\
                   .where('members', 'array_contains', user_phone)\
                   .where('status', 'in', ['pending_responses', 'forming'])\
                   .get()
    
    # Build status message
    status_parts = []
    
    if order_session:
        restaurant = order_session.get('restaurant', 'Unknown')
        order_stage = order_session.get('order_stage', 'unknown')
        group_size = order_session.get('group_size', 1)
        
        if order_stage == 'need_order_number':
            status_parts.append(f"📋 You're in a {restaurant} group ({group_size} people)")
            status_parts.append("🔄 Next step: Provide your order number or name")
        elif order_stage == 'ready_to_pay':
            status_parts.append(f"✅ Your {restaurant} order is ready")
            status_parts.append("💳 Next step: Text 'PAY' when ready")
        elif order_stage == 'payment_initiated':
            status_parts.append(f"💳 Payment sent for {restaurant}")
            status_parts.append("⏳ Waiting for delivery coordination")
    
    elif len(pending_negotiations) > 0:
        neg_data = pending_negotiations[0].to_dict()
        restaurant = neg_data.get('proposal', {}).get('restaurant', 'food')
        status_parts.append(f"🤝 You have a pending invitation for {restaurant}")
        status_parts.append("🔄 Next step: Reply YES or NO")
    
    elif len(active_groups) > 0:
        group_data = active_groups[0].to_dict()
        restaurant = group_data.get('restaurant', 'food')
        status_parts.append(f"👥 You're in a forming {restaurant} group")
        status_parts.append("⏳ Waiting for group coordination")
    
    else:
        conversation_stage = user_data.get('conversation_stage', 'available')
        if conversation_stage == 'incomplete_request':
            missing_info = user_data.get('missing_info', [])
            status_parts.append("📝 You started a food request")
            status_parts.append(f"🔄 Next step: Provide {', '.join(missing_info)}")
        else:
            status_parts.append("😊 You're all set! No active orders")
            status_parts.append("🍕 Say what you're craving to get started")
    
    # Add helpful options
    status_parts.append("\n📋 **You can always:**")
    status_parts.append("• Cancel: 'cancel' or 'stop'")
    status_parts.append("• Start over: 'I want [restaurant] at [location]'")
    status_parts.append("• Get help: 'what restaurants are available?'")
    
    message = "\n".join(status_parts)
    
    send_friendly_message(user_phone, message, message_type="status_clarification")
    state['messages'].append(AIMessage(content=message))
    return state

# ===== 4. CONTEXT BUILDER FUNCTION =====
def build_user_context(user_phone: str, current_message: str, routing_decision: Dict, is_fresh_request: bool = False) -> UserContext:
    """Build rich context from all available data sources"""
    
    # Get user data from database
    user_doc = db.collection('users').document(user_phone).get()
    user_data = user_doc.to_dict() if user_doc.exists else {}
    
    # Detect urgency from message
    urgency_keywords = ['now', 'asap', 'urgent', 'hungry', 'starving', 'quick']
    urgency_level = "urgent" if any(word in current_message.lower() for word in urgency_keywords) else "flexible"
    
    # Detect corrections
    correction_keywords = ['actually', 'no', 'instead', 'change', 'wrong', 'meant']
    is_correction = any(word in current_message.lower() for word in correction_keywords)
    
    # Determine search reason
    search_reason = "new_request"
    if is_correction:
        search_reason = "correction"
    elif routing_decision.get('action') == 'expand_search':
        search_reason = "retry"
    
    # Get order session - but not for fresh requests to avoid old data confusion
    if is_fresh_request:
        # Fresh request - don't load old session data to avoid confusion
        current_session = {}
        print(f"🔄 Fresh request detected - not loading old session data for {user_phone}")
    else:
        try:
            from pangea_order_processor import get_user_order_session
            current_session = get_user_order_session(user_phone) or {}
        except:
            current_session = {}
    
    return UserContext(
        user_phone=user_phone,
        conversation_history=user_data.get('recent_messages', [])[-5:],
        user_preferences=user_data.get('preferences', {}),
        current_session=current_session,
        conversation_stage=routing_decision.get('action', 'unknown'),
        urgency_level=urgency_level,
        personality_profile=user_data.get('personality', {'style': 'friendly'}),
        is_correction=is_correction,
        is_retry=search_reason == "retry",
        rejection_history=user_data.get('rejection_history', []),
        search_reason=search_reason
    )


def route_message_intelligently(phone_number: str, message_body: str):
    """Enhanced routing with full conversational support + deep error logging"""
    try:
        print(f"📨 route_message_intelligently START | phone_number={phone_number} | message_body={message_body}")

        try:
            print("🔍 Calling intelligent_router()...")
            routing_decision = intelligent_router(phone_number=phone_number, new_message=message_body)
            print(f"✅ intelligent_router() returned: {routing_decision}")
        except Exception as e:
            import traceback
            print(f"❌ ERROR in intelligent_router: {e}")
            traceback.print_exc()
            raise  # bubble up

        action = routing_decision.get('action')
        print(f"📌 Routing action determined: {action}")

        if action in ['cancel_current_process', 'handle_correction', 'provide_clarification']:
            try:
                print("📨 Routing to handle_incoming_sms()...")
                main_result = handle_incoming_sms(phone_number, message_body, routing_decision)
                print(f"✅ handle_incoming_sms() returned: {main_result}")
                return {'system': 'main_pangea_enhanced', 'result': main_result, 'routing_decision': routing_decision}
            except Exception as e:
                import traceback
                print(f"❌ ERROR in handle_incoming_sms: {e}")
                traceback.print_exc()
                raise

        elif action in ['collect_order_number', 'collect_order_description', 'handle_payment_request']:
            try:
                print("📨 Routing to process_order_message()...")
                order_result = process_order_message(phone_number, message_body)
                print(f"✅ process_order_message() returned: {order_result}")
                return {'system': 'order_processor', 'result': order_result, 'routing_decision': routing_decision}
            except Exception as e:
                import traceback
                print(f"❌ ERROR in process_order_message: {e}")
                traceback.print_exc()
                raise

        else:
            try:
                print("📨 Routing to handle_incoming_sms() (default)...")
                main_result = handle_incoming_sms(phone_number, message_body, routing_decision)
                print(f"✅ handle_incoming_sms() returned: {main_result}")
                return {'system': 'main_pangea_enhanced', 'result': main_result, 'routing_decision': routing_decision}
            except Exception as e:
                import traceback
                print(f"❌ ERROR in handle_incoming_sms (default): {e}")
                traceback.print_exc()
                raise

    except Exception as e:
        import traceback
        print(f"💥 UNHANDLED ERROR in route_message_intelligently: {e}")
        traceback.print_exc()
        # Still raise so Flask returns 500, but now we'll see exactly where it died
        raise

def unified_claude_router_node(state: PangeaState) -> PangeaState:
    """Enhanced router node using the new flexible router"""
    
    last_message = state['messages'][-1].content
    user_phone = state['user_phone']
    
    # Use enhanced router
    routing_decision = intelligent_router(phone_number=user_phone, new_message=last_message)
    
    # Update state based on decision
    state['conversation_stage'] = routing_decision['action']
    
    # Store routing decision for context building
    state['routing_decision'] = routing_decision
    
    # Store suggested response if provided
    if routing_decision.get('suggested_response'):
        state['suggested_response'] = routing_decision['suggested_response']
    
    return state



def get_claude_routing_decision(user_phone: str, message: str, state: PangeaState, system_state: Dict) -> Dict:
    """Use Claude to make intelligent routing decisions with platform context"""
    
    routing_prompt = f"""
You are the intelligent router for Pangea, an AI-powered food delivery coordination system for college students.

PLATFORM GOALS & CONTEXT:
- Help students find "lunch buddies" to split delivery fees and save money
- Create groups of 2-3 people ordering from the same restaurant to the same campus location
- Provide a friendly, conversational AI experience that feels like texting a helpful friend
- Match users based on restaurant preference, delivery location, and timing
- Handle the entire process: matching → group formation → individual ordering → payment coordination

USER EXPERIENCE PRINCIPLES:
- Be helpful and proactive, not pushy
- Make group ordering feel social and fun
- Respect "no" responses and offer alternatives when appropriate
- Keep conversations natural and brief
- Always prioritize user autonomy - they can decline any group invitation

CURRENT SITUATION:
USER MESSAGE: "{message}"

SYSTEM STATE:
- Is New User: {system_state['is_new_user']}
- Has Active Order Session: {system_state['has_active_order_session']}
- Has Pending Group Invitation: {system_state['has_pending_group_invitation']}
- Has Pending Negotiation: {system_state['has_pending_negotiation']}
- Has Pending Proactive Notification: {system_state['has_pending_proactive_notification']}
- Current Request: {system_state['current_request']}
- Conversation Stage: {system_state['conversation_stage']}
- Search Attempts: {system_state['search_attempts']}

AVAILABLE ACTIONS & WHEN TO USE THEM:
- welcome_new_user: First-time users need onboarding and explanation of how Pangea works
- order_continuation: User has active group and is providing order details (confirmation numbers, payment, etc.)
- group_response_yes: User accepts a group invitation - they want to join others for delivery
- group_response_no: User declines a group invitation - handle gracefully, maybe offer alternatives
- proactive_group_yes: User accepts when we proactively suggested joining an existing group
- proactive_group_no: User declines proactive group suggestion
- spontaneous_order: User wants to order food right now - start the matching process
- incomplete_request: User mentioned food but missing key details (restaurant or location)
- faq_answered: User has questions about how the service works, pricing, restaurants, etc.
- morning_response: User responding to morning check-in about lunch plans

ROUTING PRIORITY (most important rules first):
1. New users ALWAYS get welcome_new_user first - they need to understand the platform
2. Users with active order sessions discussing order details → order_continuation
3. YES/NO responses when they have pending invitations → group_response_yes/no
4. Food requests (mentions restaurants/hunger) without active session → spontaneous_order
5. Incomplete food requests (mentioned food but missing restaurant/location) → incomplete_request
6. Service questions → faq_answered

CONTEXT CLUES FOR BETTER ROUTING:
- "I want [restaurant]" or "hungry for [food]" = spontaneous_order
- "Order #123" or "my name is..." = order_continuation (if they have active session)
- "Yes" or "No" = group responses (check for pending invitations first)
- "How does this work?" or "What restaurants?" = faq_answered
- Mentions food but unclear restaurant/location = incomplete_request

AVAILABLE RESTAURANTS: Chipotle, McDonald's, Chick-fil-A, Portillo's, Starbucks
AVAILABLE LOCATIONS: Richard J Daley Library, Student Center East, Student Center West, Student Services Building, University Hall

RESPONSE FORMAT:
Return JSON with contextual understanding:
{{
    "action": "chosen_action",
    "confidence": "high/medium/low",
    "reasoning": "why you chose this action considering platform goals",
    "extracted_data": {{
        "restaurant": "exact restaurant name if mentioned",
        "location": "exact location name if mentioned",
        "time_preference": "user's time preference if mentioned"
    }},
    "missing_info": ["restaurant", "location"] if incomplete food request,
    "response_message": "friendly, contextual message if needed (optional)"
}}

Remember: You're helping create a social, money-saving food experience. Route with both technical accuracy AND user experience in mind.
"""

    try:
        response = anthropic_llm.invoke([HumanMessage(content=routing_prompt)])
        response_text = response.content.strip()
        
        # Clean JSON response
        if '```json' in response_text:
            start = response_text.find('{')
            end = response_text.rfind('}') + 1
            response_text = response_text[start:end]
        elif '```' in response_text:
            response_text = response_text.replace('```', '').strip()
        
        # Extract JSON if wrapped in other text
        if not response_text.startswith('{'):
            import re
            json_match = re.search(r'\{.*\}', response_text, re.DOTALL)
            if json_match:
                response_text = json_match.group()
        
        decision = json.loads(response_text)
        
        # Validate required fields
        if 'action' not in decision:
            raise ValueError("Missing required 'action' field")
        
        print(f"🤖 Claude routing decision: {decision['action']} (confidence: {decision.get('confidence', 'unknown')})")
        print(f"   Reasoning: {decision.get('reasoning', 'No reasoning provided')}")
        
        return decision
        
    except Exception as e:
        print(f"❌ Claude routing failed: {e}")
        # Fallback logic with same robustness
        return get_fallback_routing_decision(message, system_state)

def get_system_state_for_claude(user_phone: str, state: PangeaState) -> Dict:
    """Get system state for Claude's routing decision"""
    
    # Check if user exists
    user_doc = db.collection('users').document(user_phone).get()
    is_new_user = not user_doc.exists
    
    # Check for active order session
    try:
        from pangea_order_processor import get_user_order_session
        has_active_order_session = bool(get_user_order_session(user_phone))
    except:
        has_active_order_session = False
    
    # Check for pending invitations
    has_pending_negotiation = False
    has_pending_group_invitation = False
    has_pending_proactive_notification = False
    
    try:
        # Check negotiations
        pending_negotiations = db.collection('negotiations')\
                               .where('to_user', '==', user_phone)\
                               .where('status', '==', 'pending')\
                               .limit(1).get()
        has_pending_negotiation = len(pending_negotiations) > 0
        
        # Check groups
        pending_groups = db.collection('active_groups')\
                          .where('members', 'array_contains', user_phone)\
                          .where('status', 'in', ['pending_responses', 'forming'])\
                          .limit(1).get()
        has_pending_group_invitation = len(pending_groups) > 0
        
        # Check proactive notifications
        proactive_notification = check_pending_proactive_notifications(user_phone)
        has_pending_proactive_notification = bool(proactive_notification)
        
    except Exception as e:
        print(f"Error checking system state: {e}")
    
    return {
        'is_new_user': is_new_user,
        'has_active_order_session': has_active_order_session,
        'has_pending_negotiation': has_pending_negotiation,
        'has_pending_group_invitation': has_pending_group_invitation,
        'has_pending_proactive_notification': has_pending_proactive_notification,
        'current_request': state.get('current_request', {}),
        'conversation_stage': state.get('conversation_stage', 'initial'),
        'search_attempts': state.get('search_attempts', 0),
        'conversation_length': len(state.get('messages', []))
    }

def get_fallback_routing_decision(message: str, system_state: Dict) -> Dict:
    """Simple fallback when Claude fails"""
    
    message_lower = message.lower().strip()
    
    if system_state['is_new_user']:
        return {"action": "welcome_new_user", "confidence": "high"}
    
    # Check for FAQ questions before assuming order continuation
    faq_keywords = ['restaurant', 'available', 'location', 'where', 'cost', 'price', 'how', 'work', 'help']
    if any(keyword in message_lower for keyword in faq_keywords):
        return {"action": "faq_answered", "confidence": "medium"}
    
    if system_state['has_active_order_session']:
        # Only route to order continuation if it looks like order-related content
        order_keywords = ['pay', 'payment', 'order', 'name', 'confirmation', 'number']
        if any(keyword in message_lower for keyword in order_keywords):
            return {"action": "order_continuation", "confidence": "high"}
        # Otherwise, treat as general conversation that might be FAQ
        return {"action": "faq_answered", "confidence": "low"}
    
    if system_state['has_pending_group_invitation'] or system_state['has_pending_negotiation']:
        if message_lower in ['yes', 'y', 'sure', 'ok']:
            return {"action": "group_response_yes", "confidence": "high"}
        elif message_lower in ['no', 'n', 'pass', 'nah']:
            return {"action": "group_response_no", "confidence": "high"}
    
    if system_state['has_pending_proactive_notification']:
        if message_lower in ['yes', 'y', 'sure', 'ok']:
            return {"action": "proactive_group_yes", "confidence": "high"}
        elif message_lower in ['no', 'n', 'pass', 'nah']:
            return {"action": "proactive_group_no", "confidence": "high"}
    
    # Check for food requests
    food_keywords = ['want', 'order', 'hungry', 'food', 'lunch', 'dinner', 'mcdonald', 'chipotle', 'starbucks']
    if any(keyword in message_lower for keyword in food_keywords):
        return {"action": "start_fresh_request", "confidence": "medium"}
    
    return {"action": "faq_answered", "confidence": "low"}



def handle_order_continuation_node(state: PangeaState) -> PangeaState:
    """Handle messages that should go to order processor"""
    user_phone = state['user_phone']
    message_body = state['messages'][-1].content
    
    # Process through order system
    result = process_order_message(user_phone, message_body)
    
    if result:
        state['messages'].append(AIMessage(content="Order processed"))
    
    return state

def route_based_on_intent(state: PangeaState) -> str:
    """Router function that returns the next node based on conversation stage"""
    return state['conversation_stage']
# ===== WORKFLOW: PROMPT CHAINING (Morning Check-ins) =====

def process_morning_response_node(state: PangeaState) -> PangeaState:
    """Process user's morning response and find matches - ENHANCED for multiple restaurants"""
    
    user_response = state['messages'][-1].content
    
    # Extract preferences using LLM
    extraction_prompt = f"""
    Extract location and food preferences from this response:
    "{user_response}"
    
    Return JSON with: {{"location": "...", "food_preferences": ["..."], "time_preference": "..."}}
    """
    
    response = anthropic_llm.invoke([HumanMessage(content=extraction_prompt)])
    try:
        preferences = json.loads(response.content)
    except:
        preferences = {"location": "Student Union", "food_preferences": ["any"], "time_preference": "lunch"}
    
    state['current_request'] = preferences
    
    # 🔥 NEW: Search for matches across ALL preferred restaurants
    all_matches = []
    food_preferences = preferences.get('food_preferences', ['any'])
    
    print(f"🔍 Searching for matches across {len(food_preferences)} restaurants: {food_preferences}")
    
    for restaurant in food_preferences:
        print(f"   Searching for {restaurant} matches...")
        
        restaurant_matches = find_potential_matches(
            restaurant_preference=restaurant,
            location=preferences.get('location', ''),
            time_window=preferences.get('time_preference', 'lunch time'),
            requesting_user=state['user_phone']
        )
        
        # Add restaurant context to each match
        for match in restaurant_matches:
            match['searched_restaurant'] = restaurant
        
        all_matches.extend(restaurant_matches)
        print(f"   Found {len(restaurant_matches)} matches for {restaurant}")
    
    # Remove duplicates (same user wanting multiple restaurants you also want)
    unique_matches = []
    seen_users = set()
    
    # Sort by compatibility score first
    all_matches.sort(key=lambda x: x.get('compatibility_score', 0), reverse=True)
    
    for match in all_matches:
        user_phone = match.get('user_phone')
        if user_phone not in seen_users:
            seen_users.add(user_phone)
            unique_matches.append(match)
    
    print(f"🎯 Total unique matches across all restaurants: {len(unique_matches)}")
    
    state['potential_matches'] = unique_matches
    return state


def present_morning_matches_node(state: PangeaState) -> PangeaState:
    """Present matches to user in friendly way - ENHANCED with restaurant choice AND Yes/No"""
    
    matches = state['potential_matches']
    food_preferences = state['current_request'].get('food_preferences', ['food'])
    
    if not matches:
        # Handle no matches case
        restaurants_text = " or ".join(food_preferences) if len(food_preferences) > 1 else food_preferences[0]
        
        message = f"""I couldn't find anyone with similar lunch plans for {restaurants_text} right now, but I'll keep looking! 

Want to tell me a specific restaurant you're craving? I might be able to find someone who's flexible! 🤔"""
        
        send_friendly_message(state['user_phone'], message, message_type="morning_checkin")
        state['messages'].append(AIMessage(content=message))
        return state
    
    # 🔥 NEW: Group matches by restaurant and present options
    matches_by_restaurant = {}
    for match in matches:
        restaurant = match.get('searched_restaurant', 'food')
        if restaurant not in matches_by_restaurant:
            matches_by_restaurant[restaurant] = []
        matches_by_restaurant[restaurant].append(match)
    
    # Check if we have matches for multiple restaurants
    if len(matches_by_restaurant) > 1:
        # Multiple restaurant options - present choice with Yes/No
        restaurant_options = []
        
        for restaurant, restaurant_matches in matches_by_restaurant.items():
            count = len(restaurant_matches)
            people_text = "person" if count == 1 else "people"
            restaurant_options.append(f"• {restaurant} ({count} {people_text})")
        
        # Store matches for later use
        state['matches_by_restaurant'] = matches_by_restaurant
        
        options_text = "\n".join(restaurant_options)
        
        message = f"""Great news! I found matches for multiple restaurants:

{options_text}

Want me to form a group? Reply:
• YES [restaurant name] - like "YES Thai Garden"
• NO - to skip for now

Example: "YES {list(matches_by_restaurant.keys())[0]}" 🍜"""
        
    else:
        # Single restaurant - present simple Yes/No
        restaurant = list(matches_by_restaurant.keys())[0]
        restaurant_matches = matches_by_restaurant[restaurant]
        count = len(restaurant_matches)
        people_text = "person" if count == 1 else "people"
        
        # Store matches for later use
        state['matches_by_restaurant'] = matches_by_restaurant
        
        message = f"""Great news! I found {count} {people_text} interested in {restaurant}!

Want me to form a group? Reply:
• YES - to join the {restaurant} group
• NO - to skip for now

I'll coordinate with their AI friends to set it up! 🍜"""
    
    send_friendly_message(state['user_phone'], message, message_type="morning_checkin")
    state['messages'].append(AIMessage(content=message))
    return state


def handle_morning_match_response_node(state: PangeaState) -> PangeaState:
    """Handle user's YES/NO response to morning matches with restaurant choice"""
    
    user_response = state['messages'][-1].content.strip().lower()
    matches_by_restaurant = state.get('matches_by_restaurant', {})
    
    if not matches_by_restaurant:
        message = "I don't have any active match options for you right now. Try making a new food request!"
        send_friendly_message(state['user_phone'], message, message_type="error")
        state['messages'].append(AIMessage(content=message))
        return state
    
    # Check for NO response
    if 'no' in user_response or user_response.strip() == 'n':
        message = "No worries! Maybe next time. I'll keep an eye out for other opportunities for you! 👍"
        send_friendly_message(state['user_phone'], message, message_type="general")
        state['messages'].append(AIMessage(content=message))
        
        # Clear the stored matches
        if 'matches_by_restaurant' in state:
            del state['matches_by_restaurant']
        
        return state
    
    # Check for YES response
    if 'yes' in user_response or user_response.strip() == 'y':
        chosen_restaurant = None
        chosen_matches = None
        
        # If only one restaurant option, use it
        if len(matches_by_restaurant) == 1:
            chosen_restaurant = list(matches_by_restaurant.keys())[0]
            chosen_matches = matches_by_restaurant[chosen_restaurant]
        else:
            # Multiple restaurants - try to extract which one they chose
            for restaurant_name, restaurant_matches in matches_by_restaurant.items():
                if restaurant_name.lower() in user_response:
                    chosen_restaurant = restaurant_name
                    chosen_matches = restaurant_matches
                    break
        
        if not chosen_restaurant:
            # Couldn't determine which restaurant for multiple options
            restaurant_names = list(matches_by_restaurant.keys())
            message = f"""I see you want to join a group! Which restaurant would you prefer?

Reply "YES [restaurant name]":
{chr(10).join(f'• YES {name}' for name in restaurant_names)}

Example: "YES {restaurant_names[0]}" 🍜"""
            
            send_friendly_message(state['user_phone'], message, message_type="clarification")
            state['messages'].append(AIMessage(content=message))
            return state
        
        # Valid choice - start negotiations for chosen restaurant
        count = len(chosen_matches)
        people_text = "person" if count == 1 else "people"
        
        message = f"""Perfect! I'll set up your {chosen_restaurant} group with {count} other {people_text}! 

Let me reach out to their AI friends and coordinate the details. I'll get back to you shortly! ⏰"""
        
        # Start negotiations for chosen restaurant
        for match in chosen_matches:
            negotiation_id = str(uuid.uuid4())
            negotiate_with_other_ai(
                match['user_phone'],
                {
                    'restaurant': chosen_restaurant,
                    'location': state['current_request'].get('location'),
                    'time': state['current_request'].get('time_preference'),
                    'requesting_user': state['user_phone']
                },
                negotiation_id
            )
        
        send_friendly_message(state['user_phone'], message, message_type="morning_group_forming")
        state['messages'].append(AIMessage(content=message))
        
        # Clear the stored matches
        if 'matches_by_restaurant' in state:
            del state['matches_by_restaurant']
        
        return state
    
    # Unclear response
    message = """I didn't catch that! Please reply:
• YES - to join a group
• NO - to skip for now

Or if you have multiple restaurant options, specify which one:
• YES [restaurant name] - like "YES Thai Garden" """
    
    send_friendly_message(state['user_phone'], message, message_type="clarification")
    state['messages'].append(AIMessage(content=message))
    return state

def analyze_spontaneous_request_node_enhanced(state: PangeaState) -> PangeaState:
    """Enhanced analysis with context awareness - FIXED VERSION"""
    
    user_message = state['messages'][-1].content
    user_phone = state['user_phone']
    
    # Build and store context
    routing_decision = getattr(state, 'routing_decision', {})
    user_context = build_user_context(user_phone, user_message, routing_decision, routing_decision.get('action') == 'start_fresh_request')
    state['user_context'] = user_context
    
    # Context-aware extraction prompt
    analysis_prompt = f"""
    Extract food request with context awareness:
    
    USER MESSAGE: "{user_message}"
    {user_context.to_prompt_context()}
    
    CONTEXT-AWARE RULES:
    1. If correction → prioritize new info over context
    2. If urgent → default missing time to "now"  
    3. Use user preferences as smart defaults
    4. Consider previous rejections for disambiguation
    
    Extract: {{"restaurant": "...", "location": "...", "time_preference": "..."}}
    """
    
    try:
        # Use Claude to extract the food request details
        response = anthropic_llm.invoke([HumanMessage(content=analysis_prompt)])
        extracted_text = response.content.strip()
        
        # Try to parse JSON from the response
        import re
        json_match = re.search(r'\{[^}]*\}', extracted_text)
        if json_match:
            extracted_data = json.loads(json_match.group())
        else:
            # Fallback extraction
            extracted_data = {
                'restaurant': '',
                'location': '', 
                'time_preference': ''
            }
            
        # Store the current request
        state['current_request'] = extracted_data
        
        # Check if we have all required info
        missing_info = []
        if not extracted_data.get('restaurant'):
            missing_info.append('restaurant')
        if not extracted_data.get('location'):
            missing_info.append('location')
            
        if missing_info:
            # FIXED: Use 'incomplete_request' not 'incomplete_request' 
            state['conversation_stage'] = 'incomplete_request'
            state['missing_info'] = missing_info
            state['partial_request'] = extracted_data
        else:
            # FIXED: Use 'spontaneous_matching' not 'complete_request'
            state['conversation_stage'] = 'spontaneous_matching'
            
        state['messages'].append(AIMessage(content=f"Analyzed request: {extracted_data}"))
        return state
        
    except Exception as e:
        print(f"❌ Error in enhanced spontaneous analysis: {e}")
        # Fallback to basic extraction with better time detection
        def extract_time_from_message(message):
            """Extract time from message with common patterns"""
            import re
            message_lower = message.lower()
            
            # Look for specific time patterns
            time_patterns = [
                r'\b(\d{1,2}:\d{2}\s*(?:am|pm))\b',  # 1:30pm, 2:00am
                r'\b(\d{1,2}\s*(?:am|pm))\b',        # 1pm, 2am
                r'\b(\d{1,2}:\d{2})\b',              # 13:30, 14:00
                r'between\s+(\d{1,2}:\d{2}\s*(?:am|pm)?\s*-\s*\d{1,2}:\d{2}\s*(?:am|pm)?)', # between 1:30pm - 2:00pm
                r'between\s+(\d{1,2}\s*(?:am|pm)?\s*-\s*\d{1,2}\s*(?:am|pm)?)', # between 1pm - 2pm
            ]
            
            for pattern in time_patterns:
                match = re.search(pattern, message_lower)
                if match:
                    return match.group(1)
            
            # Check for specific times mentioned in the original message
            if 'now' in message_lower or 'soon' in message_lower or 'asap' in message_lower:
                return 'now'
            
            # Return the original message if it contains time-like words
            time_keywords = ['morning', 'afternoon', 'evening', 'night', 'lunch', 'dinner', 'breakfast']
            for keyword in time_keywords:
                if keyword in message_lower:
                    return message_lower
            
            return 'now'  # Final fallback
        
        state['current_request'] = {
            'restaurant': 'McDonald\'s' if 'mcdonald' in user_message.lower() else '',
            'location': 'library' if 'library' in user_message.lower() else '',
            'time_preference': extract_time_from_message(user_message)
        }
        # FIXED: Use 'spontaneous_matching' not 'complete_request'
        state['conversation_stage'] = 'spontaneous_matching'
        state['messages'].append(AIMessage(content="Analyzed request with fallback"))
        return state

def realtime_search_node_enhanced(state: PangeaState) -> PangeaState:
    """Enhanced search with full context"""

    user_context = state.get('user_context')
    if not user_context:
        user_context = build_user_context(state['user_phone'], "", {}, state.get('is_fresh_request', False))

    request = state['current_request']
    search_attempt = state.get('search_attempts', 0) + 1
    state['search_attempts'] = search_attempt

    # Determine search reason
    if user_context.is_correction:
        search_reason = "correction"
    elif search_attempt > 1:
        search_reason = "retry"
    else:
        search_reason = "new_request"

    # Use context-aware search
    matches = find_potential_matches_contextual({
        "restaurant_preference": request.get('restaurant', ''),
        "location": request.get('location', ''),
        "time_window": request.get('time_preference', 'now'),
        "requesting_user": state['user_phone'],
        "user_context": user_context
    })

    state['potential_matches'] = matches
    return state

def create_group_and_send_invitations(state: PangeaState, match: Dict, group_id: str, sorted_phones: List[str]):
    """
    Single-writer pattern: Create group in Firebase and send SMS invitations to both users.
    This function prevents race conditions by having only one user (the creator) handle group creation.
    """
    try:
        # Create the group in Firebase
        group_data = {
            'group_id': group_id,
            'members': sorted_phones,
            'status': 'pending_responses',
            'created_at': datetime.now(),
            'created_by': state['user_phone'],
            'restaurant': state['current_request'].get('restaurant', 'local restaurant'),
            'delivery_location': state['current_request'].get('delivery_location', 'campus'),
            'delivery_time': state['current_request'].get('time_preference', 'ASAP'),
            'invitations_sent': sorted_phones,
            'responses_received': [],
            'match_type': 'perfect_match',
            'compatibility_score': match.get('compatibility_score', 0.8)
        }
        
        # Store group in Firebase
        db.collection('active_groups').document(group_id).set(group_data)
        print(f"✅ Created group {group_id} in Firebase with members: {sorted_phones}")
        
        # Check for users already in solo orders - silently group them instead of re-inviting
        restaurant = state['current_request'].get('restaurant', 'local restaurant')
        delivery_time = state['current_request'].get('time_preference', 'ASAP')
        delivery_location = state['current_request'].get('delivery_location', 'campus')
        
        solo_order_users = []
        new_users = []
        
        # Check which users are already in solo orders (have active order sessions)
        for phone in sorted_phones:
            try:
                session_ref = db.collection('order_sessions').document(phone)
                session_doc = session_ref.get()
                if session_doc.exists and session_doc.to_dict().get('group_size') == 1:
                    solo_order_users.append(phone)
                    print(f"🔄 Found existing solo order user: {phone} - will silently group")
                else:
                    new_users.append(phone)
            except Exception as e:
                print(f"❌ Error checking solo order status for {phone}: {e}")
                new_users.append(phone)  # Default to treating as new user
        
        # Send invitations only to NEW users, not existing solo order users
        for phone in new_users:
            invitation_message = f"🍕 Perfect match found! Someone nearby wants {restaurant} delivered to {delivery_location} at {delivery_time}. Want to split the order and save on delivery? Reply YES to join or NO to pass."
            
            success = send_friendly_message(phone, invitation_message, message_type="match_found")
            if success:
                print(f"📱 Sent invitation SMS to {phone}")
            else:
                print(f"❌ Failed to send SMS to {phone}")
        
        # Silently add solo order users to the group without invitations
        for phone in solo_order_users:
            print(f"🤫 Silently adding solo order user {phone} to group {group_id} (no notification)")
            
            try:
                # Get the solo user's existing session
                from pangea_order_processor import get_user_order_session, update_order_session
                solo_session = get_user_order_session(phone)
                
                if solo_session:
                    # Check if solo user has scheduled delivery and group is immediate
                    solo_delivery_time = solo_session.get('delivery_time', 'now')
                    group_delivery_time = delivery_time
                    
                    # If solo user has scheduled delivery and group is "now", use solo user's time
                    if solo_delivery_time != 'now' and group_delivery_time == 'ASAP':
                        group_data['delivery_time'] = solo_delivery_time
                        print(f"📅 Updated group delivery time to solo user's scheduled time: {solo_delivery_time}")
                    
                    # Update solo user's session to join the group
                    solo_session['group_id'] = group_id
                    solo_session['group_size'] = len(sorted_phones)
                    solo_session['delivery_time'] = group_data['delivery_time']
                    solo_session['silent_match'] = True  # Mark as silently matched
                    
                    # Clear any scheduled delivery flags since they're now in a group
                    if 'delivery_scheduled' in solo_session:
                        del solo_session['delivery_scheduled']
                    if 'scheduled_trigger_time' in solo_session:
                        del solo_session['scheduled_trigger_time']
                    
                    update_order_session(phone, solo_session)
                    print(f"✅ Updated solo user {phone} session to group {group_id} (silent match)")
                
                # Mark solo user as silently matched in Firebase
                solo_user_doc_ref = db.collection('users').document(phone)
                solo_user_doc_ref.update({
                    'conversation_stage': 'matched_to_group',
                    'group_matched': True,
                    'group_id': group_id,
                    'silent_match': True,  # Flag for silent matching - no notifications
                    'last_updated': datetime.now()
                })
                print(f"🛡️ Marked solo user {phone} for silent matching")
                
            except Exception as e:
                print(f"❌ Error updating solo user {phone} session: {e}")
        
        # Update the group data in Firebase to include the correct delivery time
        if solo_order_users:
            db.collection('active_groups').document(group_id).update({'delivery_time': group_data['delivery_time']})
        
        print(f"🎉 Group {group_id} created and invitations sent to both users")
        
    except Exception as e:
        print(f"❌ Error creating group and sending invitations: {e}")
        # Fall back to individual processing if group creation fails
        state['group_formed'] = False

def mark_as_matched_user(state: PangeaState, creator_phone: str, group_id: str):
    """
    Mark this user as matched and waiting for invitation from the group creator.
    This prevents duplicate group creation in the race condition.
    """
    try:
        # Update user's status to indicate they're waiting for an invitation
        user_status = {
            'matched_with_group': group_id,
            'group_creator': creator_phone,
            'waiting_for_invitation': True,
            'matched_at': datetime.now(),
            'status': 'waiting_for_group_invitation'
        }
        
        # Store the matched status in Firebase
        db.collection('users').document(state['user_phone']).update({
            'current_match_status': user_status,
            'last_activity': datetime.now()
        })
        
        print(f"✅ Marked {state['user_phone']} as matched user waiting for invitation from {creator_phone}")
        print(f"👀 Waiting for group {group_id} invitation...")
        
    except Exception as e:
        print(f"❌ Error marking user as matched: {e}")
        # If we can't mark the user, they'll continue with normal flow
        state['group_formed'] = False

def create_group_with_solo_user(state: PangeaState, match: Dict, group_id: str, sorted_phones: List[str], solo_user_phone: str, new_user_phone: str):
    """
    Create a real group when one user has an existing solo order.
    Solo user is silently added, only new user gets invitation.
    ❌ REMOVED: Immediate delivery creation
    ✅ ADDED: Wait for both users to pay before triggering delivery
    """
    try:
        print(f"🎯 Creating group {group_id} with solo user {solo_user_phone} and new user {new_user_phone}")
        
        # Get delivery details from the current request
        restaurant = state['current_request'].get('restaurant', 'local restaurant')
        delivery_time = state['current_request'].get('time_preference', 'ASAP')
        delivery_location = state['current_request'].get('delivery_location', 'campus')
        
        # Get solo user's existing session to preserve their delivery time
        try:
            from pangea_order_processor import get_user_order_session, update_order_session
            solo_session = get_user_order_session(solo_user_phone)
            print(f"✅ Retrieved solo session for {solo_user_phone}: {solo_session.get('delivery_time', 'no time') if solo_session else 'no session'}")
        except Exception as e:
            print(f"⚠️ Failed to get solo session: {e}")
            solo_session = None
        
        # Always prioritize the current request's delivery time for fresh requests
        if (delivery_time in ['ASAP', 'now'] and 
            solo_session and 
            solo_session.get('delivery_time') not in ['now', 'ASAP', None]):
            delivery_time = solo_session.get('delivery_time')
            print(f"📅 Using solo user's scheduled delivery time: {delivery_time}")
        else:
            print(f"📅 Using current request's delivery time: {delivery_time}")
        
        # Create group data
        group_data = {
            'group_id': group_id,
            'restaurant': restaurant,
            'delivery_time': delivery_time,
            'location': delivery_location,
            'members': sorted_phones,
            'creator': new_user_phone,
            'solo_user': solo_user_phone,
            'status': 'pending_responses',
            'created_at': datetime.now(),
            'group_size': 2
        }
        
        # Store group in Firebase
        print(f"📝 Storing group {group_id} in Firebase...")
        db.collection('active_groups').document(group_id).set(group_data)
        print(f"✅ Created group {group_id} in Firebase with solo user silently added")
        
        # Send invitation ONLY to the new user
        invitation_message = f"🍕 Perfect match found! Someone nearby wants {restaurant} delivered to {delivery_location} at {delivery_time}. Want to split the order and save on delivery? Reply YES to join or NO to pass."
        
        print(f"📱 Sending invitation SMS to {new_user_phone}...")
        success = send_friendly_message(new_user_phone, invitation_message, message_type="match_found")
        print(f"📤 SMS send completed with result: {success}")
        
        # ✅ CRITICAL FIX: Update solo user's session WITHOUT triggering delivery
        print(f"🔄 Updating solo user session...")
        if solo_session:
            solo_session['group_id'] = group_id
            solo_session['group_size'] = 2
            solo_session['delivery_time'] = delivery_time
            
            # ✅ FIXED: Clear protection flags so delivery can trigger when both users pay
            solo_session['solo_order'] = False
            solo_session['awaiting_match'] = False
            solo_session['is_scheduled'] = False
            
            # ❌ REMOVED: delivery creation here
            # Clear any scheduled delivery flags since they're now in a group
            if 'delivery_scheduled' in solo_session:
                del solo_session['delivery_scheduled']
            if 'scheduled_trigger_time' in solo_session:
                del solo_session['scheduled_trigger_time']
            
            update_order_session(solo_user_phone, solo_session)
            print(f"✅ Updated solo user {solo_user_phone} session to group {group_id}")
        
        # Update solo user's order status to prevent workflow from sending duplicate messages
        try:
            solo_user_doc_ref = db.collection('users').document(solo_user_phone)
            solo_user_doc_ref.update({
                'conversation_stage': 'matched_to_group',
                'group_matched': True,
                'group_id': group_id,
                'solo_message_sent': True,
                'silent_match': True,
                'last_updated': datetime.now()
            })
            print(f"🛡️ Protected solo user {solo_user_phone} from duplicate messages and marked for silent matching")
        except Exception as e:
            print(f"⚠️ Warning: Could not update solo user protection flags: {e}")
        
        # Mark new user as waiting for their own response
        mark_as_matched_user(state, new_user_phone, group_id)
        
        print(f"🎉 Group {group_id} created - delivery will trigger when BOTH users pay")
        
    except Exception as e:
        print(f"❌ Error creating group with solo user: {e}")
        state['group_formed'] = False


def multi_agent_negotiation_node(state: PangeaState) -> PangeaState:
    """Advanced autonomous negotiation with better perfect match handling"""

    # ADD DEBUG AT THE VERY TOP
    print(f"🔍 ENTERING multi_agent_negotiation_node for user: {state['user_phone']}")
    print(f"🔍 Current request: {state['current_request']}")
    print(f"🔍 is_fresh_request: {state.get('is_fresh_request', False)}")

    request = state['current_request']
    matches = state['potential_matches']
    negotiations = state.get('active_negotiations', [])

    # ✅ FIX: Do not block if existing session is solo/fresh order
    try:
        session_ref = db.collection('order_sessions').document(state['user_phone'])
        session_doc = session_ref.get()
        if session_doc.exists:
            session_data = session_doc.to_dict()
            group_size = session_data.get('group_size', 0)
            status = session_data.get('status', 'active')
            order_type = session_data.get('order_type', '').lower()
            print(f"🔍 Existing session found: group_size={group_size}, status={status}, order_type={order_type}")

            # PATCH: Only block if NOT fresh request and group order (not solo)
            if (not state.get('is_fresh_request', False)
                and group_size > 1
                and status == 'active'
                and order_type != 'solo'):
                print(f"🛑 User {state['user_phone']} has active group order (not solo) - stopping search")
                return state
            else:
                print(f"✅ Fresh request or solo order detected - continuing search")
    except Exception as e:
        print(f"❌ Error checking active order session: {e}")

    # 🆕 NEW: Check if any matches are existing 2-person groups
    for match in matches:
        if match.get('group_size') == 2:
            # This is a 2-person group, join it instead of creating new group
            existing_group_id = match.get('group_id')
            print(f"👥 Found existing 2-person group {existing_group_id} - joining silently as 3rd person")
            
            try:
                # Get existing group data
                group_doc = db.collection('active_groups').document(existing_group_id).get()
                if group_doc.exists:
                    group_data = group_doc.to_dict()
                    existing_members = group_data.get('members', [])
                    restaurant = group_data.get('restaurant')
                    delivery_time = group_data.get('delivery_time')
                    
                    # Add user to existing group
                    updated_members = existing_members + [state['user_phone']]
                    
                    # Update group to 3 people
                    group_doc.reference.update({
                        'members': updated_members,
                        'group_size': 3,
                        'status': 'active',
                        'last_updated': datetime.now()
                    })
                    
                    # Start order process for new user
                    from pangea_order_processor import start_order_process, get_payment_amount
                    
                    order_session = start_order_process(
                        user_phone=state['user_phone'],
                        group_id=existing_group_id,
                        restaurant=restaurant,
                        group_size=3,
                        delivery_time=delivery_time
                    )
                    
                    payment_amount = get_payment_amount(3)
                    
                    # Send welcome message to new user only
                    welcome_message = f"""🎉 Perfect! You joined an existing {restaurant} group!

You're now in a 3-person group (maximum size) which means the best delivery coordination and lowest cost per person!

**Quick steps:**
1. Order directly from {restaurant} (choose PICKUP, not delivery)
2. Come back with your order number/name AND what you ordered

Your share: {payment_amount} 💳

The other 2 people are also placing their orders!"""
                    
                    send_friendly_message(state['user_phone'], welcome_message, message_type="joined_existing_group")
                    
                    # Remove group from discovery (3 people = full)
                    try:
                        discovery_orders = db.collection('active_orders')\
                                           .where('group_id', '==', existing_group_id)\
                                           .get()
                        for discovery_order in discovery_orders:
                            discovery_order.reference.delete()
                        print(f"✅ Removed 3-person group {existing_group_id} from discovery")
                    except Exception as e:
                        print(f"❌ Failed to remove group from discovery: {e}")
                    
                    # Update all member sessions to reflect new group size
                    for member_phone in updated_members:
                        try:
                            from pangea_order_processor import get_user_order_session, update_order_session
                            member_session = get_user_order_session(member_phone)
                            if member_session:
                                member_session['group_size'] = 3
                                update_order_session(member_phone, member_session)
                        except Exception as e:
                            print(f"⚠️ Failed to update session for {member_phone}: {e}")
                    
                    state['group_formed'] = True
                    state['final_group'] = {
                        'group_id': existing_group_id,
                        'members': updated_members,
                        'restaurant': restaurant,
                        'size': 3,
                        'type': 'joined_existing_group'
                    }
                    
                    print(f"✅ Successfully joined existing group {existing_group_id} silently as 3rd person")
                    return state
                    
            except Exception as e:
                print(f"❌ Failed to join existing group: {e}")
                # Continue to normal matching if joining existing group fails

    # ---------------------------
    # PERFECT MATCH HANDLING
    # ---------------------------
    perfect_matches = [m for m in matches if m.get('compatibility_score', 0) >= 0.8]

    print(f"🔍 DEBUG: Found {len(matches)} total matches")
    for i, match in enumerate(matches):
        print(f"   Match {i+1}: score={match.get('compatibility_score', 0)}, user={match.get('user_phone')}")
    print(f"🔍 DEBUG: {len(perfect_matches)} perfect matches (>= 0.8)")

    if perfect_matches:
        match = perfect_matches[0]
        sorted_phones = sorted([state['user_phone'], match['user_phone']])
        deterministic_group_id = f"match_{sorted_phones[0]}_{sorted_phones[1]}"

        print(f"📋 Perfect match pair: {sorted_phones}")
        print(f"📋 Group creator (lower phone): {sorted_phones[0]}")

        # Check if either user is already in a solo order
        current_user_solo = False
        matched_user_solo = False
        try:
            cu_doc = db.collection('order_sessions').document(state['user_phone']).get()
            if cu_doc.exists and cu_doc.to_dict().get('group_size') == 1:
                current_user_solo = True
            mu_doc = db.collection('order_sessions').document(match['user_phone']).get()
            if mu_doc.exists and mu_doc.to_dict().get('group_size') == 1:
                matched_user_solo = True
        except Exception as e:
            print(f"❌ Error checking solo order status: {e}")

        # If solo order exists → create special solo-handling group
        if current_user_solo or matched_user_solo:
            print(f"🤝 Solo order detected - creating real group with solo user silently added")
            if matched_user_solo:
                solo_user_phone = match['user_phone']
                new_user_phone = state['user_phone']
            else:
                solo_user_phone = state['user_phone']
                new_user_phone = match['user_phone']
            create_group_with_solo_user(state, match, deterministic_group_id, sorted_phones, solo_user_phone, new_user_phone)
            state['group_formed'] = True
            return state

        # Normal group creation — only lower phone creates group
        if state['user_phone'] == sorted_phones[0]:
            print(f"👑 I am the group creator - creating group and sending invitations")
            create_group_and_send_invitations(state, match, deterministic_group_id, sorted_phones)
        else:
            print(f"👤 I am the matched user - marking as matched and waiting for invitation")
            mark_as_matched_user(state, sorted_phones[0], deterministic_group_id)

        state['group_formed'] = True
        return state

    # ---------------------------
    # IMPERFECT MATCH NEGOTIATIONS
    # ---------------------------
    for match in matches:
        negotiation_id = str(uuid.uuid4())
        enhanced_proposal = {
            'restaurant': request.get('restaurant'),
            'primary_restaurant': request.get('restaurant'),
            'location': request.get('location'),
            'time': request.get('time_preference'),
            'requesting_user': state['user_phone'],
            'alternatives': [],
            'incentives': ["Group discount", "Faster delivery"],
            'group_size_current': 2,
            'max_group_size': MAX_GROUP_SIZE,
            'compatibility_score': match.get('compatibility_score', 0.5),
            'primary_proposal': {
                'restaurant': request.get('restaurant'),
                'time': request.get('time_preference'),
                'location': request.get('location')
            }
        }

        print(f"🔍 DEBUG - Created proposal with restaurant: '{enhanced_proposal.get('restaurant')}'")

        result = negotiate_with_other_ai(
            target_ai_user=match['user_phone'],
            proposal=enhanced_proposal,
            negotiation_id=negotiation_id,
            strategy="collaborative"
        )

        negotiations.append({
            'negotiation_id': negotiation_id,
            'target_user': match['user_phone'],
            'proposal': enhanced_proposal,
            'status': 'pending',
            'success_probability': result.get('estimated_success_probability', 0.5)
        })

    state['active_negotiations'] = negotiations

    # Immediate user feedback
    if negotiations:
        restaurant = request.get('restaurant', 'food')
        location = request.get('location', 'campus')
        time_pref = request.get('time_preference', 'now')
        feedback_message = f"""Great! I found {len(negotiations)} people interested in {restaurant} at {location} around {time_pref}! 

I'm working with their AI friends to confirm the group order details. Give me about a minute to sort this out! 🤝"""
        send_friendly_message(
            state['user_phone'],
            feedback_message,
            message_type="negotiation"
        )
        state['messages'].append(AIMessage(content=feedback_message))

    return state




def handle_group_response_yes_node(state: PangeaState) -> PangeaState:
    """FIXED: Handle YES response with proper fake match detection"""
    
    user_phone = state['user_phone']
    
    try:
        # STEP 1: Check for real perfect match groups first
        pending_groups = db.collection('active_groups')\
                          .where('members', 'array_contains', user_phone)\
                          .where('status', 'in', ['pending_responses', 'forming'])\
                          .limit(1).get()
        
        if len(pending_groups) > 0:
            # Handle real perfect match group
            group_doc = pending_groups[0]
            group_data = group_doc.to_dict()
            
            # Check if user already responded
            responses_received = group_data.get('responses_received', [])
            if user_phone in responses_received:
                print(f"⚠️ {user_phone} already responded to group {group_data['group_id']}")
                return state
            
            # Check if this is a fake match scenario
            group_members = group_data.get('members', [])
            is_fake_match_scenario = False
            
            # Check if any member is already in solo order (fake match indicator)
            for member_phone in group_members:
                if member_phone != user_phone:
                    try:
                        from pangea_order_processor import get_user_order_session
                        member_session = get_user_order_session(member_phone)
                        if member_session and member_session.get('group_size') == 1:
                            is_fake_match_scenario = True
                            solo_user_phone = member_phone
                            print(f"🎯 DETECTED FAKE MATCH: {solo_user_phone} is solo user, {user_phone} is real user")
                            break
                    except Exception as e:
                        print(f"⚠️ Error checking member session: {e}")
            
            if is_fake_match_scenario:
                # SPECIAL HANDLING: Real user joining fake match
                print(f"🤝 Handling fake match integration: solo user {solo_user_phone} + real user {user_phone}")
                
                # Update the solo user's session to become a real 2-person group
                try:
                    from pangea_order_processor import get_user_order_session, update_order_session
                    solo_session = get_user_order_session(solo_user_phone)
                    
                    if solo_session:
                        # Convert solo session to 2-person group
                        solo_session['group_size'] = 2
                        solo_session['silent_match'] = False  # No longer silent - now real group
                        solo_session['real_group_formed'] = True
                        solo_session['group_members'] = [solo_user_phone, user_phone]
                        update_order_session(solo_user_phone, solo_session)
                        print(f"✅ Converted solo user session to real 2-person group")
                        
                        # Notify solo user about real match
                        restaurant = solo_session.get('restaurant', 'food')
                        solo_message = f"""🎉 Great news! A real person just joined your {restaurant} order!
                        
You're now in a 2-person group, which means:
- Your delivery fee is now split 2 ways 
- Better delivery coordination
- Same great food, better experience!

The other person is placing their order now. I'll coordinate pickup and delivery for both of you! 🍕"""
                        
                        send_friendly_message(solo_user_phone, solo_message, message_type="fake_to_real_upgrade")
                
                except Exception as e:
                    print(f"❌ Error converting solo session: {e}")
                
                # Start order process for the real user
                group_id = group_data['group_id']
                restaurant = group_data['restaurant']
                delivery_time = group_data['delivery_time']
                
                try:
                    from pangea_order_processor import start_order_process, get_payment_amount
                    
                    order_session = start_order_process(
                        user_phone=user_phone,
                        group_id=group_id,
                        restaurant=restaurant,
                        group_size=2,  # Real 2-person group
                        delivery_time=delivery_time
                    )
                    
                    payment_amount = get_payment_amount(2)
                    
                    success_message = f"""Perfect! You've joined the {restaurant} group! 🎉

**You're now in a 2-person group!**

Quick steps:
1. Order directly from {restaurant} (app/website/phone) - choose PICKUP
2. Come back with your order number/name AND what you ordered

Your share: {payment_amount} 💳

The other person already placed their order, so this will be coordinated together!"""
                    
                    send_friendly_message(user_phone, success_message, message_type="fake_match_real_user_joined")
                    
                except Exception as e:
                    print(f"❌ Error starting order for real user in fake match: {e}")
                    send_friendly_message(user_phone, "Great! Setting up your order...", message_type="general")
                
                # Update group status
                group_doc.reference.update({
                    'responses_received': firestore.ArrayUnion([user_phone]),
                    'status': 'active',
                    'fake_match_converted': True,
                    'real_group_size': 2
                })
                
                # 🆕 NEW: Make 2-person group discoverable for 3rd person
                try:
                    active_order_data = {
                        'user_phone': group_members[0],  # Representative user
                        'restaurant': restaurant,
                        'location': group_data.get('delivery_location') or group_data.get('location'),
                        'time_requested': str(delivery_time),
                        'status': 'looking_for_group',
                        'created_at': datetime.now(),
                        'group_id': group_id,
                        'group_size': 2,  # Mark as existing 2-person group
                        'flexibility_score': 0.8
                    }
                    db.collection('active_orders').add(active_order_data)
                    print(f"✅ Made 2-person group {group_id} discoverable for 3rd person")
                except Exception as e:
                    print(f"❌ Failed to make group discoverable: {e}")
                
                return state
            
            # NORMAL PERFECT MATCH HANDLING (not fake match)
            # Extract group information
            group_id = group_data['group_id']
            restaurant = group_data['restaurant']
            delivery_time = group_data['delivery_time']
            group_size = len(group_data['members'])
            
            # Update group with this user's response
            group_doc.reference.update({
                'responses_received': firestore.ArrayUnion([user_phone]),
                'status': 'forming'
            })
            
            # START ORDER PROCESS FOR THIS USER
            try:
                from pangea_order_processor import start_order_process, get_payment_amount
                
                order_session = start_order_process(
                    user_phone=user_phone,
                    group_id=group_id,
                    restaurant=restaurant,
                    group_size=group_size,
                    delivery_time=delivery_time
                )
                
                payment_amount = get_payment_amount(group_size)
                
                success_message = f"""Great! You're part of the {restaurant} group! 🎉

**Quick steps to get your food:**
1. Order directly from {restaurant} (app/website/phone) - choose PICKUP, not delivery
2. Come back here with your confirmation number or name for the order AND what you ordered

Once everyone's ready, your payment will be {payment_amount} 💳

Let me know if you need any help!"""
                
                send_friendly_message(user_phone, success_message, message_type="order_start")
                print(f"✅ Order process started successfully for {user_phone}")
                
            except Exception as e:
                print(f"❌ Error starting order process for {user_phone}: {e}")
                send_friendly_message(user_phone, f"Great! You're part of the {restaurant} group! Setting up your order instructions...", message_type="general")
            
            # Check if all members have responded
            updated_responses = responses_received + [user_phone]
            all_members = group_data['members']
            
            if len(updated_responses) >= len(all_members):
                group_doc.reference.update({'status': 'active'})
                print(f"✅ All members responded to perfect match group {group_id}")
                
                # 🆕 NEW: Make 2-person groups discoverable for 3rd person
                if group_size == 2:
                    try:
                        active_order_data = {
                            'user_phone': all_members[0],  # Representative user
                            'restaurant': restaurant,
                            'location': group_data.get('delivery_location') or group_data.get('location'),
                            'time_requested': str(delivery_time),
                            'status': 'looking_for_group',
                            'created_at': datetime.now(),
                            'group_id': group_id,
                            'group_size': 2,  # Mark as existing 2-person group
                            'flexibility_score': 0.8
                        }
                        db.collection('active_orders').add(active_order_data)
                        print(f"✅ Made 2-person group {group_id} discoverable for 3rd person")
                    except Exception as e:
                        print(f"❌ Failed to make group discoverable: {e}")
            
            state['messages'].append(AIMessage(content="Perfect match group YES response processed with fake match handling"))
            return state
        
        # Fall back to old negotiation system if no perfect match groups
        pending_negotiations = db.collection('negotiations')\
                               .where('to_user', '==', user_phone)\
                               .where('status', '==', 'pending')\
                               .limit(1).get()
        
        if len(pending_negotiations) > 0:
            negotiation_doc = pending_negotiations[0]
            negotiation_data = negotiation_doc.to_dict()
            
            # Update negotiation status to accepted
            negotiation_doc.reference.update({'status': 'accepted'})
            
            proposal = negotiation_data.get('proposal', {})
            restaurant = proposal.get('restaurant', 'Unknown Restaurant')
            group_id = negotiation_data['negotiation_id']
            delivery_time = proposal.get('time', 'now')
            
            # Start order process for this user
            try:
                from pangea_order_processor import start_order_process
                
                order_session = start_order_process(
                    user_phone=user_phone,
                    group_id=group_id,
                    restaurant=restaurant,
                    group_size=2,  # Default group size for negotiations
                    delivery_time=delivery_time
                )
                
                print(f"✅ Order process started for negotiation group {group_id}")
                
            except Exception as e:
                print(f"❌ Error starting order process for negotiation: {e}")
                send_friendly_message(user_phone, f"Great! You're part of the {restaurant} group! Setting up your order instructions...", message_type="general")
            
            print(f"✅ Group accepted and order process started: {negotiation_data['negotiation_id']}")
        
        else:
            send_friendly_message(user_phone, "I don't see any pending group invitations for you right now. Want to start a new food order?", message_type="general")
            
    except Exception as e:
        print(f"❌ Error in fake match handling: {e}")
        send_friendly_message(user_phone, "Something went wrong processing your response. Can you try again?", message_type="general")
        
    state['messages'].append(AIMessage(content="Group response YES processed with fake match handling"))
    return state



def handle_alternative_response_node(state: PangeaState) -> PangeaState:
    """Handle responses to alternative suggestions"""
    
    user_phone = state['user_phone']
    last_message = state['messages'][-1].content.lower().strip()
    
    if 'yes' in last_message or 'y' == last_message or 'sure' in last_message:
        # User wants the alternative - start new negotiation
        alternatives = state.get('alternative_suggestions', [])
        
        if alternatives and len(alternatives) > 0:
            best_alternative = alternatives[0]
            
            # Create new negotiation for the alternative
            negotiation_id = str(uuid.uuid4())
            
            # Use their existing request format but with new restaurant
            new_proposal = {
                'restaurant': best_alternative['restaurant'],
                'location': best_alternative['location'], 
                'time': best_alternative['time_requested'],
                'requesting_user': user_phone  # They become the requester now
            }
            
            result = negotiate_with_other_ai(
                best_alternative['user_phone'],
                new_proposal,
                negotiation_id,
                "collaborative"
            )
            
            success_message = f"Great! I'm reaching out to see if they'd like you to join their {best_alternative['restaurant']} group. I'll let you know what they say! 🤝"
            send_friendly_message(user_phone, success_message, message_type="negotiation")
            
        else:
            send_friendly_message(user_phone, "Sorry, those opportunities are no longer available. I'll keep looking for new matches! 🔍", message_type="general")
    
    else:
        # User declined the alternative too
        send_friendly_message(user_phone, "No problem! I'll keep an eye out for other opportunities that might interest you. 👍", message_type="general")
    
    state['messages'].append(AIMessage(content="Alternative response processed"))
    return state

def handle_proactive_group_yes_node(state: PangeaState) -> PangeaState:
    """Handle YES response to proactive group notification"""
    
    user_phone = state['user_phone']
    proactive_data = state.get('proactive_notification_data', {})
    
    try:
        # Update notification response
        update_proactive_notification_response(user_phone, 'accepted')
        
        # Get group details from the notification
        group_id = proactive_data.get('group_id', '')
        restaurant = proactive_data.get('restaurant', '')
        delivery_time = proactive_data.get('time', 'now')
        
        # Calculate new group size (this would be more sophisticated in real implementation)
        # For now, assume group size is 3 (original + this user)
        new_group_size = 3
        
        # Start order process directly - skip negotiation since group is already forming
        print(f"🚀 User {user_phone} accepted proactive invitation for {restaurant} at {delivery_time}")
        
        # Create order session manually (FIXED VERSION)
        try:
            from pangea_order_processor import get_payment_link, get_payment_amount, update_order_session
            
            session_data = {
                'user_phone': user_phone,
                'group_id': group_id,
                'restaurant': restaurant,
                'group_size': new_group_size,
                'delivery_time': delivery_time,
                'order_stage': 'need_order_number',
                'pickup_location': RESTAURANTS.get(restaurant, {}).get('location', 'Campus'),
                'payment_link': get_payment_link(new_group_size),
                'order_session_id': str(uuid.uuid4()),
                'created_at': datetime.now(),
                'order_number': None,
                'customer_name': None
            }
            
            update_order_session(user_phone, session_data)
            payment_amount = get_payment_amount(new_group_size)
            
            # Send order instructions
            welcome_message = f"""**Quick steps to get your food:**
1. Order directly from {restaurant} (app/website/phone) - just make sure to choose PICKUP, not delivery
2. Come back here with your confirmation number or name for the order AND what you ordered

Once everyone's ready, your payment will be {payment_amount} 💳

Let me know if you need any help!"""
            
            send_friendly_message(user_phone, welcome_message, message_type="order_start")
            
        except Exception as e:
            print(f"❌ Error starting proactive order process: {e}")
            send_friendly_message(user_phone, f"Great! You're part of the {restaurant} group! Setting up your order instructions...", message_type="general")
        
        # Send confirmation message
        confirmation_message = f"Awesome! 🎉 You're now part of the {restaurant} group! Check your messages for order instructions."
        send_friendly_message(user_phone, confirmation_message, message_type="proactive_group_accepted")
        
        print(f"✅ Proactive group acceptance processed for {user_phone}")
        
    except Exception as e:
        print(f"❌ Error processing proactive group YES: {e}")
        error_message = "Something went wrong adding you to the group. Let me try again or you can start a new order."
        send_friendly_message(user_phone, error_message, message_type="general")
    
    state['messages'].append(AIMessage(content="Proactive group YES processed"))
    return state

def handle_proactive_group_no_node(state: PangeaState) -> PangeaState:
    """Handle NO response to proactive group notification"""
    
    user_phone = state['user_phone']
    proactive_data = state.get('proactive_notification_data', {})
    
    try:
        # Update notification response
        update_proactive_notification_response(user_phone, 'declined')
        
        # Send acknowledgment
        acknowledgment_message = "No worries! 👍 I'll keep an eye out for other opportunities that might interest you."
        send_friendly_message(user_phone, acknowledgment_message, message_type="general")
        
        print(f"✅ Proactive group decline processed for {user_phone}")
        
    except Exception as e:
        print(f"❌ Error processing proactive group NO: {e}")
        error_message = "Got it - I'll look for other opportunities for you!"
        send_friendly_message(user_phone, error_message, message_type="general")
    
    state['messages'].append(AIMessage(content="Proactive group NO processed"))
    return state

def wait_for_responses_node(state: PangeaState) -> PangeaState:
    """Wait for negotiation responses and then decide next action"""
    
    user_phone = state['user_phone']
    
    # Check if user is waiting for perfect match group responses - no message needed
    try:
        pending_groups = db.collection('active_groups')\
                          .where('members', 'array_contains', user_phone)\
                          .where('status', '==', 'pending_responses')\
                          .limit(1).get()
        
        if len(pending_groups) > 0:
            # User is waiting for perfect match group responses - just wait silently
            print(f"👀 {user_phone} waiting for perfect match group responses - no message needed")
            state['messages'].append(AIMessage(content="Waiting for perfect match group responses"))
            return state
    except Exception as e:
        print(f"Error checking perfect match groups: {e}")
    
    # For old negotiation system - send waiting message
    message = "I'm still waiting to hear back from potential lunch buddies. I'll check in with you shortly if I find a group! 🤞"
    
    send_friendly_message(
        state['user_phone'],
        message,
        message_type="general"
    )
    
    state['messages'].append(AIMessage(content=message))
    return state

# ─── FAQ terminal node ────────────────────────────────────────────────
def faq_answered_node(state: PangeaState) -> PangeaState:
    """Handle FAQ questions and send informative responses"""
    user_phone = state['user_phone']
    user_message = state['messages'][-1].content.lower()
    
    # Check if user is new or existing
    user_doc = db.collection('users').document(user_phone).get()
    is_new_user = not user_doc.exists
    
    # Check for active order session to understand context
    try:
        from pangea_order_processor import get_user_order_session
        has_active_session = bool(get_user_order_session(user_phone))
    except:
        has_active_session = False
    
    # Determine what kind of FAQ this is and respond appropriately
    if 'restaurant' in user_message or 'food' in user_message or 'available' in user_message:
        # Restaurant availability question - context-aware response
        if is_new_user:
            message = """🍽️ Available restaurants for group delivery:

• McDonald's 🍟
• Chipotle 🌯  
• Chick-fil-A 🐔
• Portillo's 🥩
• Starbucks ☕️

Just tell me what you're craving and I'll find you lunch buddies to split the delivery fee! 

Example: "I want Chipotle delivered to the library at 1pm" """
        else:
            # Existing user - more direct response
            if has_active_session:
                message = """🍽️ Available restaurants for your next order:

• McDonald's 🍟
• Chipotle 🌯  
• Chick-fil-A 🐔
• Portillo's 🥩
• Starbucks ☕️

What are you thinking for your next order? 🤔"""
            else:
                message = """🍽️ Available restaurants right now:

• McDonald's 🍟
• Chipotle 🌯  
• Chick-fil-A 🐔
• Portillo's 🥩
• Starbucks ☕️

What sounds good? Just tell me what you want and where! 😊"""
        
    elif 'location' in user_message or 'where' in user_message:
        # Location question
        message = """📍 Available delivery locations:

• Richard J Daley Library
• Student Center East
• Student Center West  
• Student Services Building
• University Hall

Just mention your preferred spot when ordering! 🎯"""

    elif 'cost' in user_message or 'price' in user_message or 'pay' in user_message:
        # Pricing question
        message = """💰 Pricing is simple:

When matched with others: Split the delivery fee (usually $2.50-$3.50 per person)
Solo orders: Full delivery fee (~$7-10)

You only pay the delivery fee - order your own food directly from the restaurant! 🍕"""

    elif 'work' in user_message or 'how' in user_message:
        # How it works question
        message = """🤝 Here's how Pangea works:

1. Tell me what restaurant + location + time you want
2. I'll find students with similar orders
3. If matched: Split delivery fees ($2.50-3.50 each)
4. Everyone orders their own food from the restaurant
5. One shared delivery brings everyone's orders!

Try: "I want McDonald's at the library at 2pm" 🚀"""

    else:
        # General help
        message = """👋 I'm your AI food delivery buddy!

I help UIC students save money by matching you with others for shared deliveries.

Available restaurants: McDonald's, Chipotle, Chick-fil-A, Portillo's, Starbucks

Just tell me: "I want [restaurant] delivered to [location] at [time]"

Questions? Ask about restaurants, locations, pricing, or how it works! 😊"""

    send_friendly_message(user_phone, message, message_type="faq")
    state['messages'].append(AIMessage(content=message))
    return state

# REPLACE the should_continue_negotiating function in pangea_main.py with this fixed version:

def should_continue_negotiating(state: PangeaState) -> str:
    """
    Enhanced decision-making using Claude 4's reasoning capabilities.
    
    Considers multiple factors beyond simple counting to make optimal decisions.
    """
    
    # Check if a perfect match group was already formed - THIS SHOULD BE FIRST
    if state.get('group_formed'):
        print("🎉 Perfect match group already formed, ending workflow")
        return "wait_for_responses"
    
    user_phone = state['user_phone']
    
    
    # CRITICAL FIX: Check if user is already in an ACTIVE group (not just pending)
    # BUT allow fresh requests to override
    try:
        # Check for any active groups (pending_responses, forming, or active)
        user_groups = db.collection('active_groups')\
                      .where('members', 'array_contains', user_phone)\
                      .where('status', 'in', ['pending_responses', 'forming', 'active'])\
                      .limit(1).get()
        
        if len(user_groups) > 0:
            # Allow fresh requests to override existing group memberships
            is_fresh_request = state.get('is_fresh_request', False)
            if not is_fresh_request:
                group_data = user_groups[0].to_dict()
                group_status = group_data.get('status')
                print(f"🛑 User {user_phone} is already in group with status '{group_status}' - stopping search")
                return "wait_for_responses"
            else:
                print(f"✅ Fresh request detected - allowing search despite existing group membership")
            
    except Exception as e:
        print(f"⚠️ Error checking for active groups: {e}")
    
    # ALSO CHECK: If user has an active order session, they shouldn't be searching
    # PATCH: Only block if NOT fresh request and group order (not solo) - matching multi_agent_negotiation_node logic
    try:
        from pangea_order_processor import get_user_order_session
        session = get_user_order_session(user_phone)
        if session:
            is_fresh_request = state.get('is_fresh_request', False)
            group_size = session.get('group_size', 1)
            status = session.get('status', '')
            order_type = session.get('order_type', '')
            
            # Only block if NOT fresh request and group order (not solo)
            if (not is_fresh_request
                and group_size > 1
                and status == 'active'
                and order_type != 'solo'):
                print(f"🛑 User {user_phone} has active group order (not solo) - stopping search")
                return "wait_for_responses"
            else:
                print(f"✅ Fresh request or solo order detected in should_continue_negotiating - allowing search to continue")
    except Exception as e:
        print(f"⚠️ Error checking order session: {e}")
    
    negotiations = state['active_negotiations']
    confirmed = [neg for neg in negotiations if neg['status'] == 'accepted']
    pending = [neg for neg in negotiations if neg['status'] == 'pending']
    rejected = [neg for neg in negotiations if neg['status'] == 'rejected']
    search_attempts = state.get('search_attempts', 0)
    
    print(f"🔍 Negotiations: {len(negotiations)} total, {len(confirmed)} confirmed, {len(pending)} pending, Search attempts: {search_attempts}")
    
    # Check if this user has pending group invitations - if so, wait for response
    try:
        pending_groups = db.collection('active_groups')\
                          .where('members', 'array_contains', user_phone)\
                          .where('status', '==', 'pending_responses')\
                          .get()
        
        for group in pending_groups:
            group_data = group.to_dict()
            invitations_sent = group_data.get('invitations_sent', [])
            
            # If this user received an invitation and hasn't responded, wait
            if user_phone in invitations_sent:
                print(f"⏳ User has pending group invitation, waiting for response instead of continuing search")
                return "wait_for_responses"
                
    except Exception as e:
        print(f"⚠️ Could not check for pending invitations: {e}")
    
    # CRITICAL FIX: If no negotiations at all and max attempts reached, go to solo order
    if len(negotiations) == 0 and search_attempts >= 3:
        print(f"🛑 No negotiations found after {search_attempts} attempts - triggering solo order")
        return "no_group_found"
    
    # PREVENT INFINITE LOOPS: Max 3 search attempts
    if search_attempts >= 3:
        print(f"🛑 Max search attempts reached ({search_attempts}), checking for pending invitations...")
        
        # Check if this user has pending group invitations before ending search
        try:
            pending_groups = db.collection('active_groups')\
                              .where('members', 'array_contains', user_phone)\
                              .where('status', '==', 'pending_responses')\
                              .get()
            
            for group in pending_groups:
                group_data = group.to_dict()
                invitations_sent = group_data.get('invitations_sent', [])
                
                # If this user received an invitation, wait for their response
                if user_phone in invitations_sent:
                    print(f"⏳ User has pending group invitation, waiting for response instead of solo order")
                    return "wait_for_responses"
            
            print(f"❌ No pending invitations found, proceeding to solo order")
            print(f"🔍 DEBUG: Checked {len(pending_groups)} groups with status='pending_responses' for user {user_phone}")
            
        except Exception as e:
            print(f"⚠️ Could not check for pending invitations: {e}")
        
        return "no_group_found"
    
    # Try Claude 4 reasoning first, but with a fallback
    decision_prompt = f"""
    Analyze this negotiation state and decide next action:
    
    Confirmed acceptances: {len(confirmed)}
    Pending negotiations: {len(pending)} 
    Rejected: {len(rejected)}
    Max group size: 3 people
    
    Options:
    - finalize_group: We have a good group, proceed with order
    - wait_for_responses: Continue waiting for pending responses  
    - expand_search: Look for more potential matches
    - no_group_found: Give up on group order
    
    Return only one of these four options.
    """
    
    try:
        decision_response = anthropic_llm.invoke([HumanMessage(content=decision_prompt)])
        
        # Robust handling of different response formats
        decision = ""
        
        if hasattr(decision_response, 'content'):
            content = decision_response.content
            
            if isinstance(content, list):
                for item in content:
                    if hasattr(item, 'text'):
                        decision = item.text
                        break
                    elif isinstance(item, dict) and 'text' in item:
                        decision = item['text']
                        break
                    elif isinstance(item, str):
                        decision = item
                        break
            elif isinstance(content, str):
                decision = content
            else:
                decision = str(content)
        else:
            decision = str(decision_response)
        
        # Clean up the decision
        decision = decision.strip().lower()
        
        # Validate decision
        valid_decisions = ["finalize_group", "wait_for_responses", "expand_search", "no_group_found"]
        if decision in valid_decisions:
            print(f"✅ Claude decided: {decision}")
            return decision
        
        # Try to extract valid decision from longer response
        for valid_decision in valid_decisions:
            if valid_decision in decision:
                print(f"✅ Claude decided (extracted): {valid_decision}")
                return valid_decision
        
        print(f"⚠️ Claude gave unclear response: '{decision}', using fallback logic")
        
    except Exception as e:
        print(f"❌ Claude API failed: {e}, using fallback logic")
    
    # FALLBACK: Simple, decisive logic that always ends the workflow
    if len(confirmed) >= 1:
        print("🎯 Fallback: Finalizing group")
        return "finalize_group"
    elif len(pending) > 0:
        print("🎯 Fallback: Wait for responses")
        return "wait_for_responses"  # This goes to wait node → END
    else:
        print("🎯 Fallback: No group found")
        return "no_group_found"

# ===== WELCOME FLOW WITH 2025 ENHANCEMENTS =====
def welcome_new_user_node(state: PangeaState) -> PangeaState:
    """
    Enhanced welcome experience using Claude 4's conversational abilities.
    
    Creates personalized onboarding and establishes AI Friend relationship.
    """
    
    # Use Claude 4 to create personalized welcome message
    welcome_prompt = f"""
    Create a warm, friendly welcome message for a new user joining Pangea food delivery.
    
    Requirements:
    - Sound like a helpful friend, not a bot
    - Mention the 5 available restaurants naturally
    - Explain the concept briefly but engagingly
    - Set expectations for how the AI friend will help
    - Include appropriate emojis but don't overdo it
    - Keep it conversational and exciting
    
    Available restaurants:
    {', '.join(RESTAURANTS)}
    
    The tone should be: friendly, helpful, slightly excited about food, trustworthy
    """
    
    try:
        welcome_response = anthropic_llm.invoke([HumanMessage(content=welcome_prompt)])
        welcome_message = welcome_response.content
    except:
        # Fallback message if Claude call fails
        welcome_message = f"""Hey there! 👋 Welcome to Pangea - I'm your AI friend for group food orders!

I'm here to help you find lunch buddies and save money on delivery! Here are the restaurants I can help you order from:

🌯 {AVAILABLE_RESTAURANTS[0]}
🍔 {AVAILABLE_RESTAURANTS[1]}
🐓 {AVAILABLE_RESTAURANTS[2]}
🌭 {AVAILABLE_RESTAURANTS[3]}
☕️ {AVAILABLE_RESTAURANTS[4]}

Just text me anytime you're hungry, or I'll check in with you in the mornings to plan ahead! 

What sounds good? 😊"""
    
    # Create enhanced user profile with learning capabilities
    user_profile = {
        'phone': state['user_phone'],
        'created_at': datetime.now(),
        'preferences': {
            'onboarding_completed': True,
            'communication_style': 'friendly',  # Can be learned over time
            'preferred_contact_times': []  # Will be learned
        },
        'interactions': [],
        'successful_matches': [],
        'learning_data': {
            'response_patterns': [],
            'satisfaction_scores': [],
            'preferred_group_sizes': []
        }
    }
    
    db.collection('users').document(state['user_phone']).set(user_profile)
    
    # Send welcome message
    send_friendly_message(
        state['user_phone'], 
        welcome_message,
        message_type="welcome"
    )
    
    state['messages'].append(AIMessage(content=welcome_message))
    state['conversation_stage'] = 'welcomed'
    
    # Log welcome interaction for learning
    update_user_memory(phone_number=state['user_phone'], interaction_data={
        'interaction_type': 'welcome',
        'restaurants_shown': RESTAURANTS,
        'onboarding_completed': True
    })
    
    return state

# ===== ENHANCED MORNING CHECK-IN WITH LEARNING =====
def morning_greeting_node(state: PangeaState) -> PangeaState:
    """
    Personalized morning greeting using Claude 4's contextual understanding.
    
    Adapts message based on user's history and preferences.
    """
    
    user_prefs = get_user_preferences.invoke({"phone_number": state['user_phone']})
    
    # Create personalized morning message using Claude 4
    personalization_prompt = f"""
    Create a personalized morning check-in message for this user:
    
    User history: {json.dumps(user_prefs.get('preferences', {}), default=str)}
    Past successful orders: {len(user_prefs.get('successful_matches', []))}
    
    Make it feel natural and personalized, like a friend who knows their food habits.
    Ask about their location and lunch plans for today.
    Reference their past preferences subtly if relevant.
    Keep it brief and friendly.
    """
    
    try:
        greeting_response = anthropic_llm.invoke([HumanMessage(content=personalization_prompt)])
        greeting = greeting_response.content
    except:
        # Fallback greeting
        greeting = """Hey! 👋 Hope you're having a great morning! 

Where are you planning to be on campus today? And what are you thinking about for lunch? 

I can help you find some lunch buddies! 🍜"""
    
    send_friendly_message(
        state['user_phone'], 
        greeting,
        message_type="morning_checkin"
    )
    
    state['conversation_stage'] = 'awaiting_morning_response'
    state['messages'].append(AIMessage(content=greeting))
    
    return state

def handle_incomplete_request_node(state: PangeaState) -> PangeaState:
    """
    Handle messages when user provides missing information for incomplete requests.
    Re-analyzes the user's message with the stored partial request data.
    """
    user_phone = state['user_phone']
    user_message = state['messages'][-1].content
    
    print(f"🔄 Handling incomplete request follow-up from {user_phone}")
    
    # Load missing info and partial request from database (don't rely on state)
    try:
        user_doc = db.collection('users').document(user_phone).get()
        if user_doc.exists:
            user_data = user_doc.to_dict()
            missing_info = user_data.get('missing_info', [])
            partial_request = user_data.get('partial_request', {})
            print(f"📂 HANDLE_INCOMPLETE loaded from DB: missing_info={missing_info}, partial_request={partial_request}")
        else:
            missing_info = []
            partial_request = {}
            print(f"📂 HANDLE_INCOMPLETE: No data in DB")
    except Exception as e:
        print(f"❌ HANDLE_INCOMPLETE DB load error: {e}")
        missing_info = []
        partial_request = {}
    
    print(f"📝 Missing info: {missing_info}")
    print(f"📝 Partial request: {partial_request}")
    
    # Re-analyze the user's message with Claude to extract the missing information
    try:
        llm = ChatAnthropic(model="claude-3-5-sonnet-20241022", temperature=0.1)
        
        analysis_prompt = f"""
        The user previously made an incomplete food order request. We're missing: {', '.join(missing_info)}.
        
        Previous partial request: {partial_request}
        
        User's new message: "{user_message}"
        
        Please extract ONLY the missing information from their new message and update the partial request.
        
        Available restaurants: Chipotle, McDonald's, Chick-fil-A, Portillo's, Starbucks
        Available locations: Richard J Daley Library, Student Center East, Student Center West, University Hall, Student Services Building
        
        Return JSON with:
        {{
            "updated_request": {{updated complete request with new info}},
            "still_missing": [list of any info still missing],
            "interpretation": "brief explanation of what you extracted"
        }}
        """
        
        response = llm.invoke(analysis_prompt)
        import json
        result = json.loads(response.content)
        
        updated_request = result.get('updated_request', {})
        still_missing = result.get('still_missing', [])
        interpretation = result.get('interpretation', '')
        
        print(f"🤖 Claude interpretation: {interpretation}")
        print(f"📊 Updated request: {updated_request}")
        print(f"❓ Still missing: {still_missing}")
        
        # If we still have missing info, ask for clarification again
        if still_missing:
            missing_text = ', '.join(still_missing)
            clarification = f"Thanks! I still need to know: {missing_text}. Could you provide that?"
            
            send_friendly_message(user_phone, clarification, message_type="clarification")
            state['messages'].append(AIMessage(content=clarification))
            state['missing_info'] = still_missing
            state['partial_request'] = updated_request
            
            # Save updated incomplete request data to database
            db.collection('users').document(user_phone).update({
                'conversation_stage': 'incomplete_request',
                'missing_info': still_missing,
                'partial_request': updated_request,
                'last_updated': firestore.SERVER_TIMESTAMP
            })
            
            # Stay in incomplete_request state
            return state
            
        # We have all the info! Continue with normal flow
        state['current_request'] = updated_request
        state['conversation_stage'] = 'spontaneous_matching'
        
        # Clear the incomplete request data from state and database
        if 'missing_info' in state:
            del state['missing_info']
        if 'partial_request' in state:
            del state['partial_request']
            
        # Clear from database too
        db.collection('users').document(user_phone).update({
            'conversation_stage': 'spontaneous_matching',
            'missing_info': firestore.DELETE_FIELD,
            'partial_request': firestore.DELETE_FIELD,
            'last_updated': firestore.SERVER_TIMESTAMP
        })
            
        print(f"✅ Request completed! Moving to matching flow: {updated_request}")
        
        return state
        
    except Exception as e:
        print(f"❌ Error processing incomplete request: {e}")
        
        # Fallback: treat as new request
        state['conversation_stage'] = 'spontaneous_order'
        return state

# ===== MAIN LANGGRAPH WITH 2025 ENHANCEMENTS =====
def create_pangea_graph():
    """Enhanced workflow with conversational flexibility - FIXED VERSION"""
    
    workflow = StateGraph(PangeaState)
    
    # Add all existing nodes
    workflow.add_node("unified_router", unified_claude_router_node)
    workflow.add_node("welcome_new_user", welcome_new_user_node)
    workflow.add_node("morning_greeting", morning_greeting_node)
    workflow.add_node("process_morning_response", process_morning_response_node)
    workflow.add_node("present_morning_matches", present_morning_matches_node)
    workflow.add_node("analyze_spontaneous", analyze_spontaneous_request_node_enhanced)
    workflow.add_node("realtime_search", realtime_search_node_enhanced)
    workflow.add_node("negotiate", multi_agent_negotiation_node)
    workflow.add_node("finalize_group", finalize_group_node)
    workflow.add_node("handle_no_matches", handle_no_matches_node)
    workflow.add_node("wait_for_responses", wait_for_responses_node)
    workflow.add_node("handle_order_continuation", handle_order_continuation_node)
    workflow.add_node("faq_answered", faq_answered_node)
    workflow.add_node("handle_incomplete_request", handle_incomplete_request_node)
    workflow.add_node("handle_group_yes", handle_group_response_yes_node)
    workflow.add_node("handle_group_no", handle_group_response_no_node)
    workflow.add_node("handle_alternative_response", handle_alternative_response_node)
    workflow.add_node("handle_proactive_group_yes", handle_proactive_group_yes_node)
    workflow.add_node("handle_proactive_group_no", handle_proactive_group_no_node)
    
    # NEW: Enhanced conversational nodes
    workflow.add_node("handle_cancellation", handle_cancellation_node)
    workflow.add_node("handle_correction", handle_correction_node)
    workflow.add_node("provide_clarification", provide_clarification_node)
    
    # Enhanced routing with conversational support
    workflow.add_conditional_edges(
        "unified_router",
        lambda state: state['conversation_stage'],
        {
            "welcome_new_user": "welcome_new_user",
            "morning_response": "process_morning_response", 
            "spontaneous_order": "analyze_spontaneous",
            "start_fresh_request": "analyze_spontaneous",
            "preference_update": "process_morning_response",
            "group_response_yes": "handle_group_yes",
            "group_response_no": "handle_group_no",
            "alternative_response": "handle_alternative_response",
            "proactive_group_yes": "handle_proactive_group_yes",
            "proactive_group_no": "handle_proactive_group_no",
            "order_continuation": "handle_order_continuation",
            "faq_answered": "faq_answered",
            "incomplete_request": "handle_incomplete_request",
            # NEW: Conversational flexibility
            "cancel_current_process": "handle_cancellation",
            "handle_correction": "handle_correction", 
            "provide_clarification": "provide_clarification",
        }
    )
    
    # All conversational nodes end gracefully
    workflow.add_edge("handle_cancellation", END)
    workflow.add_edge("handle_correction", "analyze_spontaneous")  # Restart with correction
    workflow.add_edge("provide_clarification", END)
    
    # Morning workflow chain
    workflow.add_edge("process_morning_response", "present_morning_matches")
    workflow.add_edge("present_morning_matches", END)
    workflow.add_edge("faq_answered", END)
    
    # FIXED: Enhanced spontaneous agent flow with conditional routing
    workflow.add_conditional_edges(
        "analyze_spontaneous",
        lambda state: state.get('conversation_stage', 'spontaneous_matching'),
        {
            "incomplete_request": END,  # Will show missing info message and end
            "spontaneous_matching": "realtime_search"  # Continue to search
            # REMOVED: "complete_request" route that was causing the KeyError
        }
    )
    workflow.add_edge("realtime_search", "negotiate")
    
    # Handle incomplete request flow
    workflow.add_conditional_edges(
        "handle_incomplete_request",
        lambda state: state.get('conversation_stage', 'incomplete_request'),
        {
            "spontaneous_matching": "realtime_search",
            "incomplete_request": END,
            "spontaneous_order": "analyze_spontaneous"
        }
    )
    
    workflow.add_conditional_edges(
        "negotiate",
        should_continue_negotiating,
        {
            "finalize_group": "finalize_group",
            "wait_for_responses": "wait_for_responses",
            "expand_search": "realtime_search",  
            "no_group_found": "handle_no_matches"
        }
    )
    
    # Terminal nodes
    workflow.add_edge("finalize_group", END)
    workflow.add_edge("handle_no_matches", END)
    workflow.add_edge("welcome_new_user", END)
    workflow.add_edge("wait_for_responses", END)
    workflow.add_edge("handle_group_yes", END)
    workflow.add_edge("handle_group_no", END)
    workflow.add_edge("handle_alternative_response", END)
    workflow.add_edge("handle_proactive_group_yes", END)
    workflow.add_edge("handle_proactive_group_no", END)
    workflow.add_edge("handle_order_continuation", END)
    workflow.add_edge("handle_incomplete_request", END)
    
    workflow.set_entry_point("unified_router")
    
    return workflow.compile()


def find_optimal_group_time(matches: List[Dict], requesting_user_time: str) -> str:
    """Let agent find the best time for the whole group"""
    
    if not matches:
        return requesting_user_time
    
    try:
        all_times = [requesting_user_time] + [match.get('time_requested', 'flexible') for match in matches]
        
        time_optimization_prompt = f"""
        You have a group wanting to order food together. Here are their preferred times:
        
        {', '.join(all_times)}
        
        What's the best single delivery time that works for everyone? Consider:
        - Most people's preferences
        - Realistic meal times
        - Delivery logistics
        
        Suggest one optimal time (like "12:30pm" or "now" or "in 20 minutes"):
        """
        
        response = anthropic_llm.invoke([HumanMessage(content=time_optimization_prompt)])
        optimal_time = response.content.strip()
        
        print(f"🕐 Agent suggests optimal time: '{optimal_time}' for group")
        return optimal_time
        
    except Exception as e:
        print(f"❌ Time optimization failed: {e}")
        return requesting_user_time


def finalize_group_node(state: PangeaState) -> PangeaState:
    """Finalize group order with enhanced coordination using Claude 4"""
    
    confirmed_members = [neg for neg in state['active_negotiations'] if neg['status'] == 'accepted']
    all_members = [state['user_phone']] + [neg['target_user'] for neg in confirmed_members]
    restaurant = state['current_request'].get('restaurant', 'chosen restaurant')
    group_size = len(all_members)

    # Enforce 3-person maximum
    if group_size > MAX_GROUP_SIZE:
        send_friendly_message(
            state['user_phone'],
            "Oops - a Pangea group can't exceed 3 people. Let me regroup and try again! 🚦",
            message_type="general"
        )
        return state

    # Find optimal time for the group
    requesting_user_time = state['current_request'].get('time_preference', 'now')
    optimal_time = find_optimal_group_time(state['potential_matches'], requesting_user_time)
    
    # Generate unique group ID
    group_id = str(uuid.uuid4())
    
    # FIXED: Call notify_compatible_users_of_active_groups directly (not .invoke())
    if len(all_members) < MAX_GROUP_SIZE:  # Group has room for more people
        print(f"🔔 Group has {len(all_members)} members, looking for more compatible users...")
        
        notify_result = notify_compatible_users_of_active_groups(
            active_group_data={
                "restaurant": restaurant,
                "location": state['current_request'].get('location'),
                "time": optimal_time,
                "current_members": all_members,
                "group_id": group_id
            },
            max_notifications=3,
            compatibility_threshold=0.7
        )
        
        print(f"🔔 Proactive notifications sent: {notify_result.get('notifications_sent', 0)}")
    
    # Clean up active_orders for all group members before starting order process
    for member_phone in all_members:
        try:
            old_orders = db.collection('active_orders')\
                          .where('user_phone', '==', member_phone)\
                          .where('status', '==', 'looking_for_group')\
                          .get()
            
            for old_order in old_orders:
                old_order.reference.delete()
                print(f"🗑️ Cleaned up active order for {member_phone}")
        except Exception as e:
            print(f"❌ Failed to clean up orders for {member_phone}: {e}")
    
    # Start order process for all group members (FIXED VERSION)
    for member_phone in all_members:
        try:
            from pangea_order_processor import get_payment_link, get_payment_amount, update_order_session
            
            session_data = {
                'user_phone': member_phone,
                'group_id': group_id,
                'restaurant': restaurant,
                'group_size': group_size,
                'delivery_time': optimal_time,
                'order_stage': 'need_order_number',
                'pickup_location': RESTAURANTS.get(restaurant, {}).get('location', 'Campus'),
                'payment_link': get_payment_link(group_size),
                'order_session_id': str(uuid.uuid4()),
                'created_at': datetime.now(),
                'order_number': None,
                'customer_name': None
            }
            
            update_order_session(member_phone, session_data)
            payment_amount = get_payment_amount(group_size)
            
            # Send order instructions
            welcome_message = f"""**Quick steps to get your food:**
1. Order directly from {restaurant} (app/website/phone) - just make sure to choose PICKUP, not delivery
2. Come back here with your confirmation number or name for the order AND what you ordered

Once everyone's ready, your payment will be {payment_amount} 💳

Let me know if you need any help!"""
            
            send_friendly_message(member_phone, welcome_message, message_type="order_start")
            print(f"✅ Started order process for {member_phone}")
        except Exception as e:
            print(f"❌ Failed to start order process for {member_phone}: {e}")
    
    # Use Claude 4 to create coordinated group message WITH optimal time
    coordination_prompt = f"""
    Create an exciting group coordination message for a successful food order group.
    
    Group details:
    - Total members: {group_size}
    - Restaurant: {restaurant}
    - Location: {state['current_request'].get('location', 'campus')}
    - Optimal delivery time: {optimal_time}
    
    The message should:
    - Celebrate the successful group formation
    - Mention the optimal delivery time we found for everyone
    - Mention that individual order instructions are being sent
    - Sound enthusiastic but organized
    """
    
    try:
        coord_response = anthropic_llm.invoke([HumanMessage(content=coordination_prompt)])
        success_message = coord_response.content
    except:
        success_message = f"""🎉 Amazing! We've got a group of {group_size} for {restaurant}!

Based on everyone's preferences, the best delivery time is {optimal_time}. 

Everyone is receiving individual order instructions now. Once you all place your orders, I'll coordinate the group payment and pickup!

This is going to be great! 🍜"""
    
    # Send to requesting user
    send_friendly_message(
        state['user_phone'],
        success_message,
        message_type="match_found"
    )
    
    # Update user memory for successful group formation
    for member in all_members:
        update_user_memory(phone_number=member, interaction_data={
            'interaction_type': 'successful_group_formation',
            'group_members': all_members,
            'restaurant': restaurant,
            'location': state['current_request'].get('location'),
            'group_size': group_size,
            'optimal_time': optimal_time,
            'formation_time': datetime.now(),
            'group_id': group_id
        })
    
    state['final_group'] = {
        'members': all_members,
        'restaurant': restaurant,
        'optimal_time': optimal_time,
        'status': 'confirmed',
        'group_id': group_id
    }
    state['messages'].append(AIMessage(content=success_message))
    
    return state

def should_protect_solo_order(user_phone: str, order_data: Dict) -> bool:
    """
    Check if a solo order should be protected from cleanup
    Protects orders that:
    1. Have been paid for
    2. Have not reached their delivery time yet
    3. Are still awaiting potential matches
    """
    try:
        from pangea_order_processor import get_user_order_session
        session = get_user_order_session(user_phone)
        
        if not session:
            return False
            
        is_solo = session.get('solo_order', False) or session.get('group_size') == 1
        is_paid = session.get('payment_requested_at') is not None
        delivery_triggered = session.get('delivery_triggered', False)
        
        if not (is_solo and is_paid) or delivery_triggered:
            return False
            
        # Check if delivery time has passed
        delivery_time_str = order_data.get('time_requested') or session.get('delivery_time')
        if not delivery_time_str:
            return False
            
        from pangea_uber_direct import parse_delivery_time
        from datetime import datetime, timezone, timedelta
        
        try:
            scheduled_time = parse_delivery_time(delivery_time_str) if isinstance(delivery_time_str, str) else delivery_time_str
            current_time = datetime.now()
            
            # Handle timezones
            if scheduled_time.tzinfo is None:
                scheduled_time = scheduled_time.replace(tzinfo=timezone.utc)
            if current_time.tzinfo is None:
                current_time = current_time.replace(tzinfo=timezone.utc)
                
            # Protect if delivery time hasn't passed (30 min buffer)
            buffer_time = scheduled_time + timedelta(minutes=30)
            
            if current_time < buffer_time:
                print(f"🛡️ Protecting paid solo order for {user_phone}")
                return True
                
        except Exception as time_error:
            print(f"⚠️ Time parsing error: {time_error}")
            return True  # Protect on error
            
        return False
        
    except Exception as e:
        print(f"⚠️ Protection check error: {e}")
        return True  # Protect on error

def is_paid_solo_order(user_phone: str) -> bool:
    """Check if user has a paid solo order that should be protected"""
    try:
        from pangea_order_processor import get_user_order_session
        session = get_user_order_session(user_phone)
        
        if not session:
            return False
            
        is_solo = session.get('solo_order', False) or session.get('group_size', 0) == 1
        is_paid = session.get('payment_requested_at') is not None
        delivery_triggered = session.get('delivery_triggered', False)
        
        return is_solo and is_paid and not delivery_triggered
        
    except Exception as e:
        print(f"⚠️ Error checking paid solo status: {e}")
        return False

def handle_no_matches_node(state: PangeaState) -> PangeaState:
   """
   When no matches found, send COMBINED message and trigger order processor flow.
   Updated so solo message goes through if it's a NEW food request,
   unless the user is explicitly resuming their old matched session.
   Deduplication now considers restaurant + location + delivery_time.
   """
   from datetime import datetime, timedelta

   user_phone = state['user_phone']
   restaurant = state['current_request'].get('restaurant', 'that spot')
   location = state['current_request'].get('location', 'campus')
   delivery_time = state['current_request'].get('time_preference', 'now')

   # Create new active_orders entry so this solo user can be found by future users IMMEDIATELY
   try:
       active_order_data = {
           'user_phone': user_phone,
           'restaurant': restaurant,
           'location': location,
           'time_requested': str(delivery_time),  # Ensure it's stored as string, not datetime
           'status': 'looking_for_group',
           'created_at': datetime.now(),
           'flexibility_score': 0.5
       }
       db.collection('active_orders').add(active_order_data)
       print(f"🔄 Created new active_orders entry for {user_phone} - now findable for future matches")
   except Exception as e:
       print(f"❌ Failed to create active_orders entry: {e}")

   # Unique request key for deduplication
   request_key = f"{restaurant}|{location}|{delivery_time}"

   # 🆕 Resume intent detection
   last_message = state['messages'][-1].content if state.get('messages') else ""
   resume_phrases = [
       "resume", "continue", "join again", "pick up where we left",
       "same group", "previous group", "last order", "group order",
       "rejoin", "same match"
   ]
   if any(phrase in last_message.lower() for phrase in resume_phrases):
       print(f"🔄 User {user_phone} is trying to resume old match — skipping solo message")
       return state

   # 🆕 DEDUPLICATION: Prevent duplicate solo sends for same request within 5 minutes
   try:
       user_doc_ref = db.collection('users').document(user_phone)
       user_doc = user_doc_ref.get()
       if user_doc.exists:
           user_data = user_doc.to_dict()
           last_request = user_data.get("last_request")
           if isinstance(last_request, dict) and last_request.get("key") == request_key:
               ts = last_request.get("timestamp")
               if ts and isinstance(ts, datetime):
                   if datetime.now() - ts < timedelta(minutes=5):
                       print(f"🚫 Duplicate solo message prevented for {user_phone} (same request within 5 min)")
                       return state
   except Exception as e:
       print(f"⚠️ Could not check last_request deduplication: {e}")

   # Prevent sending twice for this request in current state
   if state.get('solo_message_sent'):
       print(f"🚫 Solo message already sent for {user_phone}, skipping")
       return state

   # Create solo group_id
   solo_group_id = f"solo_{str(uuid.uuid4())}"

   # Check if user already gave order details
   already_ordered = any(phrase in last_message.lower() for phrase in [
       'ordered', 'i ordered', 'my order', 'order is', 'got a', 'getting a',
       'big mac', 'quarter pounder', 'chicken nuggets', 'fries'
   ])

   # Build message
   time_context = f" around {delivery_time}" if delivery_time != 'now' else ""
   if already_ordered:
       combined_message = f"""Great news! I found someone else who wants {restaurant} delivered to {location}{time_context} too, so you can split the delivery fee!

Since you've already got your order sorted, all you need to do is text "pay" and I'll get your food delivered for $3.50 instead of the full delivery fee 🙌

Text: **pay**"""
   else:
       combined_message = f"""Great news! I found someone else who wants {restaurant} delivered to {location}{time_context} too, so you can split the delivery fee!

Since you want {restaurant} at {location} for {delivery_time}, just place your order and come back with your confirmation details.

Your share will only be $2.50–$3.50 instead of the full delivery fee 🙌

**Quick steps:**
1. Order directly from {restaurant} (choose PICKUP, not delivery)  
2. Come back with your confirmation number/name AND what you ordered  

Then your payment will be $3.50 💳"""

   # Send message
   send_friendly_message(user_phone, combined_message, message_type="general")
   state['solo_message_sent'] = True

   # 🆕 Save last request in Firestore for future deduplication
   try:
       db.collection('users').document(user_phone).update({
           "last_request": {
               "key": request_key,
               "restaurant": restaurant,
               "location": location,
               "delivery_time": delivery_time,
               "timestamp": datetime.now()
           }
       })
       print(f"💾 Stored last_request for {user_phone}: {request_key}")
   except Exception as e:
       print(f"⚠️ Could not update last_request in Firestore: {e}")

   # CRITICAL FIX: Intelligent cleanup with solo order protection
   try:
       current_time = datetime.now()
       old_orders = db.collection('active_orders')\
           .where('user_phone', '==', user_phone)\
           .where('status', '==', 'looking_for_group')\
           .get()
       
       for old_order in old_orders:
           order_data = old_order.to_dict()
           created_at = order_data.get('created_at', current_time)
           delivery_time_str = order_data.get('time_requested', 'now')
           
           # PROTECTION: Check if this is a protected solo order
           if should_protect_solo_order(user_phone, order_data):
               print(f"🛡️ PROTECTING paid solo order for {user_phone}")
               continue  # Skip cleanup
           
           should_cleanup = False
           
           # Standard cleanup logic for non-protected orders
           if delivery_time_str.lower() == 'now':
               if current_time - created_at > timedelta(hours=2):
                   should_cleanup = True
           else:
               try:
                   from pangea_uber_direct import parse_delivery_time
                   scheduled_time = parse_delivery_time(delivery_time_str)
                   if current_time > scheduled_time + timedelta(hours=2):
                       should_cleanup = True
               except:
                   if current_time - created_at > timedelta(hours=6):
                       should_cleanup = True
           
           if should_cleanup:
               old_order.reference.delete()
               print(f"🗑️ Cleaned up expired order for {user_phone}")
           else:
               print(f"✅ Keeping active order for {user_phone}")
               
   except Exception as e:
       print(f"❌ Cleanup error: {e}")

   # Start solo order process
   try:
       from pangea_order_processor import start_order_process
       order_session = start_order_process(
           user_phone=user_phone,
           group_id=solo_group_id,
           restaurant=restaurant,
           group_size=1,
           delivery_time=delivery_time
       )
       print(f"✅ Started solo order process for {user_phone}")

       # If already ordered, try extracting details
       if already_ordered:
           try:
               from langchain_anthropic import ChatAnthropic
               from langchain_core.messages import HumanMessage
               import json, re

               extraction_prompt = f"""
               The user provided order details in their message. Extract the information:
               
               User message: "{last_message}"
               
               Extract:
               1. Customer name (if available)
               2. What they ordered (food items)
               
               Return JSON:
               {{
                   "customer_name": string or null,
                   "order_description": string or null
               }}
               """

               llm = ChatAnthropic(model="claude-opus-4-20250514", temperature=0.1, max_tokens=500)
               response = llm.invoke([HumanMessage(content=extraction_prompt)])
               response_text = response.content.strip()

               if '```json' in response_text:
                   start = response_text.find('{')
                   end = response_text.rfind('}') + 1
                   response_text = response_text[start:end]
               elif '```' in response_text:
                   response_text = response_text.replace('```', '').strip()

               if not response_text.startswith('{'):
                   json_match = re.search(r'\{.*\}', response_text, re.DOTALL)
                   if json_match:
                       response_text = json_match.group()

               extracted_data = json.loads(response_text)

               from pangea_order_processor import update_order_session, get_user_order_session
               current_session = get_user_order_session(user_phone)

               if extracted_data.get('customer_name'):
                   current_session['customer_name'] = extracted_data['customer_name']
               if extracted_data.get('order_description'):
                   current_session['order_description'] = extracted_data['order_description']

               if extracted_data.get('customer_name') and extracted_data.get('order_description'):
                   current_session['order_stage'] = 'ready_to_pay'

               update_order_session(user_phone, current_session)

               print(f"✅ Extracted order details: {extracted_data}")
           except Exception as extraction_error:
               print(f"❌ Failed to extract order details: {extraction_error}")

       # Update user memory
       try:
           update_user_memory.invoke({
               "phone_number": user_phone, 
               "interaction_data": {
                   "interaction_type": "fake_match_solo_order",
                   "restaurant": restaurant,
                   "location": location,
                   "delivery_time": delivery_time,
                   "timestamp": datetime.now(),
               }
           })
       except Exception as memory_error:
           print(f"❌ Failed to update user memory: {memory_error}")

   except Exception as e:
       print(f"❌ Failed to start solo order process: {e}")
       import traceback
       print(traceback.format_exc())

   # Store message in state
   state['messages'].append(AIMessage(content=combined_message))
   return state


# ===== TWILIO WEBHOOK HANDLER =====
def handle_incoming_sms(phone_number: str, message_body: str, routing_decision: dict = None):
    """Enhanced SMS handler with conversational support"""
    
    # Load existing conversation context from database
    try:
        user_doc = db.collection('users').document(phone_number).get()
        if user_doc.exists:
            user_data = user_doc.to_dict()
            conversation_stage = user_data.get('conversation_stage', 'initial')
            missing_info = user_data.get('missing_info', [])
            partial_request = user_data.get('partial_request', {})
            user_preferences = user_data.get('preferences', {})
        else:
            conversation_stage = 'initial'
            missing_info = []
            partial_request = {}
            user_preferences = {}
    except Exception as e:
        print(f"⚠️ Error loading conversation context: {e}")
        conversation_stage = 'initial'
        missing_info = []
        partial_request = {}
        user_preferences = {}
    
    # Determine if this is a fresh request based on routing decision
    is_fresh_request = False
    if routing_decision and routing_decision.get('action') == 'start_fresh_request':
        is_fresh_request = True
        print(f"✅ Setting is_fresh_request=True based on routing decision")
    
    # Initialize state with loaded context and new fields
    initial_state = PangeaState(
        messages=[HumanMessage(content=message_body)],
        user_phone=phone_number,
        user_preferences=user_preferences,
        current_request={},
        potential_matches=[],
        active_negotiations=[],
        final_group=None,
        conversation_stage=conversation_stage,
        search_attempts=0,
        rejection_data=None,
        alternative_suggestions=[],
        proactive_notification_data=None,
        missing_info=missing_info,
        partial_request=partial_request,
        # NEW: Enhanced context fields
        user_context=None,
        routing_decision=routing_decision,
        suggested_response=None,
        solo_message_sent=False,
        is_fresh_request=is_fresh_request
    )
    
    # Run through enhanced LangGraph
    app = create_pangea_graph()
    final_state = app.invoke(initial_state)
    
    return final_state

# ===== SCHEDULED MORNING CHECK-INS WITH LEARNING =====
def send_morning_checkins():
    """
    Enhanced morning check-ins using Claude 4's personalization capabilities.
    
    Learns optimal timing and personalization for each user.
    """
    
    users_ref = db.collection('users')
    users = users_ref.get()
    
    for user_doc in users:
        user_data = user_doc.to_dict()
        phone = user_data['phone']
        
        # Skip if user has preferences to not receive morning messages
        if user_data.get('preferences', {}).get('morning_checkins_disabled'):
            continue
        
        # Create personalized morning state
        morning_state = PangeaState(
            messages=[],
            user_phone=phone,
            user_preferences=user_data.get('preferences', {}),
            current_request={},
            potential_matches=[],
            active_negotiations=[],
            final_group=None,
            conversation_stage="morning_checkin"
        )
        
        # Send personalized morning greeting
        morning_greeting_node(morning_state)

# ===== HELPER FUNCTIONS =====

def answer_faq_question(user_message: str) -> str:
    """
    Uses Claude-4 to answer general questions about Pangea.
    Internal pricing rules (NOT revealed to users):
      • Solo order (fake-matched): $2.50 - $3.50
      • 2-person group:             $4.50 each
      • 3-person group:             $3.50 each
    Public-facing language: "delivery is usually $2.50 - $4.50 per person."
    """
    prompt = f"""
You are **Pangea**, a friendly AI lunch-coordination assistant for college students.

The user asked: \"{user_message}\"

Answer clearly and concisely.  If the user asks:
• **"How does this work?"** → Give the 5-step flow:

1. **Text me your plan**  
   e.g. "Chipotle around 12:30 - 1 p.m."

2. **I find matches**  
   I'll look for up to {MAX_GROUP_SIZE - 1} other students who want the same place + time.

3. **You confirm group & price**  
   I reply with the group and delivery fee (usually **$2.50 - $4.50** per person).  
   Reply "YES" to lock it in.

4. **Pay the link**  
   Your secure Stripe link arrives—pay to activate the order.

5. **Meet at your drop-off spot**  
   I send updates and the pickup pin (Daley Library, SCE, etc.).

• **"What restaurants can I pick from?"** → list them.  
• **"Where can I meet the delivery?"** → list drop-off locations.  
• **"How much does delivery cost?"** → "Delivery is usually $2.50 - $4.50 per person, depending on group size."  
• Any other pricing, timing, or general FAQ → answer in ≤ 5 lines.

When useful, remind the user:  
"Just text me your food + location—I'll handle matching!"

---

**Current restaurant list:**  
{chr(10).join('- ' + r for r in AVAILABLE_RESTAURANTS)}

**Current drop-off locations:**  
{chr(10).join('- ' + d for d in AVAILABLE_DROPOFF_LOCATIONS)}
"""
    resp = anthropic_llm.invoke([HumanMessage(content=prompt)])
    return resp.content.strip()

def send_negotiation_notification(target_user: str, negotiation_doc: Dict):
    """Agent autonomously crafts negotiation message"""
    
    proposal = negotiation_doc['proposal']
    
    # Agent reasons about the best negotiation approach
    restaurant = proposal.get('restaurant', 'food')
    location = proposal.get('location', 'campus')
    time = proposal.get('time', 'soon')
    
    negotiation_prompt = f"""
    Create a specific group order invitation message. Someone wants to order {restaurant} at {location} around {time}.
    
    Write a clear, friendly SMS that:
    1. States the specific restaurant, location, and time
    2. Explains this is a group order to split delivery fees
    3. Asks for YES/NO response
    4. Keep it under 160 characters
    
    Do NOT use generic phrases like "great match" or "chatting with AI assistant"
    BE SPECIFIC about the restaurant and details.
    
    Message:"""
    
    try:
        response = anthropic_llm.invoke([HumanMessage(content=negotiation_prompt)])
        invitation_message = response.content.strip()
        
        # Agent validates its own output
        if len(invitation_message) > 200:
            invitation_message = invitation_message[:160] + "... Reply YES/NO"
            
    except:
        # Simple fallback
        restaurant = proposal.get('restaurant', 'food')
        location = proposal.get('location', 'campus')
        time = proposal.get('time', 'soon')
        
        invitation_message = f"""Hey! 🍕 Someone else wants {restaurant} at {location} around {time} too! 

Want to team up for a group order? You'd save on delivery fees and it's more fun! 

Reply:
- YES to join the group
- NO to pass this time"""
    
    # Send the message
    success = send_friendly_message(target_user, invitation_message, message_type="group_invitation")
    
    if success:
        print(f"✅ Agent sent negotiation: {negotiation_doc['negotiation_id']}")
    else:
        print(f"❌ Agent negotiation failed: {target_user}")

def log_interaction(phone_number: str, interaction_data: Dict):
    """Log interaction for analytics and learning"""
    try:
        db.collection('interaction_logs').add({
            'user_phone': phone_number,
            'timestamp': datetime.now(),
            **interaction_data
        })
    except Exception as e:
        print(f"Logging failed: {e}")

# ===== FLASK WEBHOOK SERVER =====
app = Flask(__name__)

# Add this root route above your existing webhook
@app.route('/', methods=['GET'])
def home():
    """Root route for health checks"""
    return {
        'status': 'online', 
        'service': 'Pangea AI Friend System',
        'version': '2025.1',
        'endpoints': ['/webhook/sms', '/health']
    }, 200

# Your existing webhook with minimal fixes
@app.route('/webhook', methods=['POST'])
@app.route('/webhook/sms', methods=['POST'])
def sms_webhook():
    """Enhanced webhook with full conversational support + debug logging"""
    import time
    import traceback

    start_time = time.time()
    print("🚨 ENHANCED WEBHOOK STARTED")

    try:
        from_number = request.form.get('From')
        message_body = request.form.get('Body')
        print(f"📱 SMS from {from_number}: {message_body}")

        # ADDED: Basic validation
        if not from_number or not message_body:
            print(f"❌ Missing required fields - From: {from_number}, Body: {message_body}")
            return '', 400

        # ---------------------------
        # DEBUG WRAPPER AROUND ROUTER
        # ---------------------------
        try:
            print("🔍 Calling route_message_intelligently()...")
            routing_result = route_message_intelligently(from_number, message_body)
            print(f"✅ route_message_intelligently() returned: {routing_result}")
        except Exception as router_error:
            print("❌ ERROR inside route_message_intelligently or its dependencies!")
            print(f"❌ ERROR TYPE: {type(router_error).__name__}")
            print(f"❌ ERROR DETAILS: {router_error}")
            print(f"❌ TRACEBACK:\n{traceback.format_exc()}")

            # Send user-friendly fail message (won’t block webhook)
            try:
                send_friendly_message(
                    from_number,
                    "Sorry, I hit a technical problem while processing your message. Please try again later.",
                    message_type="error"
                )
                print("✅ Sent error message to user after router failure.")
            except Exception as sms_error:
                print(f"❌ Could not send error SMS: {sms_error}")

            return '', 500
        # ---------------------------

        # Log the enhanced decision
        decision = routing_result.get('routing_decision', {})
        print(f"🤖 Enhanced routing: {decision.get('action')} - {decision.get('user_intent')}")

        print(f"✅ Enhanced processing complete in {time.time() - start_time:.2f}s")
        return '', 200

    except Exception as e:
        print(f"❌ Unhandled webhook error: {e}")
        print(f"❌ ERROR TYPE: {type(e).__name__}")
        print(f"❌ TRACEBACK:\n{traceback.format_exc()}")

        try:
            if 'from_number' in locals() and from_number:
                send_friendly_message(
                    from_number,
                    "Sorry, I'm having technical difficulties. Please try again in a few minutes.",
                    message_type="error"
                )
                print("✅ Error SMS sent to user")
        except Exception as sms_error:
            print(f"❌ Could not send error SMS: {sms_error}")

        return '', 500



@app.route('/health', methods=['GET'])
def health_check():
    return {'status': 'healthy', 'service': 'Pangea AI Friend'}, 200

if __name__ == "__main__":
    print("🍜 Starting Pangea AI Friend System...")
    print("Ready to receive SMS messages!")
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 5000)), debug=False)  # Use PORT env var