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
    cred = credentials.Certificate(os.getenv('FIREBASE_SERVICE_ACCOUNT_PATH'))
    firebase_admin.initialize_app(cred)
db = firestore.client()

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
    proactive_notification_data: Optional[Dict]  # Store proactive notification data


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

# Replace find_potential_matches function with direct calls
def find_potential_matches(
   restaurant_preference: str,
   location: str, 
   time_window: str,
   requesting_user: str,
   flexibility_score: float = 0.5
) -> List[Dict]:
   """Find compatible users for group food orders using database filtering."""
   
   print(f"🔍 SEARCHING:")
   print(f"   Looking for: '{restaurant_preference}' at '{location}' ({time_window})")
   print(f"   Excluding: {requesting_user}")
   
   # Add delay for spontaneous matching to allow database writes to complete
   import time
   time.sleep(1.5)
   print(f"⏱️ Added search delay for spontaneous matching reliability")
   
   try:
       matches = []
       
       # Query database for potential candidates
       orders_ref = db.collection('active_orders')
       similar_orders = orders_ref.where('location', '==', location)\
                                 .where('status', '==', 'looking_for_group')\
                                 .where('user_phone', '!=', requesting_user)\
                                 .limit(10).get()
       
       print(f"📊 Found {len(similar_orders)} potential orders in database")
       
       # Filter out old orders with MORE AGGRESSIVE filtering
       current_time = datetime.now()
       filtered_orders = []

       for order in similar_orders:
           order_data = order.to_dict()
           order_time = order_data.get('created_at')
           order_time_pref = order_data.get('time_requested', 'flexible')
           
           # Safety check: prevent self-matching
           if order_data.get('user_phone') == requesting_user:
               print(f"   🚫 Skipping self-match for {requesting_user}")
               continue
           
           # Skip very old orders
           if order_time:
               try:
                   # Handle timezone differences by converting both to naive datetime
                   if hasattr(order_time, 'tzinfo') and order_time.tzinfo is not None:
                       order_time = order_time.replace(tzinfo=None)
                   
                   if hasattr(current_time, 'tzinfo') and current_time.tzinfo is not None:
                       current_time = current_time.replace(tzinfo=None)
                   
                   time_diff = current_time - order_time
                   
                   # FIXED: More aggressive cleanup - ANY order older than 30 minutes is stale
                   if time_diff > timedelta(minutes=30):
                       print(f"   ⏰ Skipping stale order: {order_time_pref} from {time_diff} ago (user: {order_data.get('user_phone')})")
                       continue
                       
                   # ADDITIONAL: Skip orders from different meal periods
                   order_hour = order_time.hour
                   current_hour = current_time.hour
                   
                   # If order is from a different meal period (more than 4 hours apart), skip it
                   hour_diff = abs(current_hour - order_hour)
                   if hour_diff > 4 and hour_diff < 20:  # Avoid midnight wraparound issues
                       print(f"   🍽️ Skipping order from different meal period: {order_hour}:00 vs {current_hour}:00")
                       continue
                       
               except Exception as e:
                   print(f"   ⚠️ Error comparing times, skipping problematic order: {e}")
                   continue  # Skip problematic orders instead of including them
           else:
               # No timestamp - this is suspicious, skip it
               print(f"   ❌ Skipping order with no timestamp: {order_data}")
               continue
           
           filtered_orders.append(order)
       
       print(f"📊 After aggressive time filtering: {len(filtered_orders)} potential orders")
       
       # Use calculate_compatibility to score each candidate
       for order in filtered_orders:
           order_data = order.to_dict()
           print(f"   Checking: {order_data}")
           
           # Call calculate_compatibility using .invoke() method for @tool decorated function
           compatibility_score = calculate_compatibility.invoke({
               "user1_restaurant": restaurant_preference,
               "user1_time": time_window,
               "user2_restaurant": order_data.get('restaurant', ''),
               "user2_time": order_data.get('time_requested', 'flexible'),
               "user1_phone": requesting_user,
               "user2_phone": order_data['user_phone']
           })
           
           # Only include matches above threshold
           if compatibility_score >= 0.3:
               match = {
                   'user_phone': order_data['user_phone'],
                   'restaurant': order_data['restaurant'],
                   'location': order_data['location'],
                   'time_requested': order_data['time_requested'],
                   'compatibility_score': compatibility_score,
                   'user_flexibility': order_data.get('flexibility_score', 0.5)
               }
               matches.append(match)
               print(f"   ✅ MATCH: {match}")
           else:
               print(f"   ❌ No match: score {compatibility_score}")
       
       # Sort by compatibility score (best matches first)
       matches.sort(key=lambda x: x['compatibility_score'], reverse=True)
       print(f"🎯 Final matches: {len(matches[:3])}")
       return matches[:3]  # Return top 3 matches
       
   except Exception as e:
       print(f"❌ Matching failed: {e}")
       return []


@tool
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
    """Deterministic time compatibility - clear rules"""
    
    time1_clean = time1.lower().strip()
    time2_clean = time2.lower().strip()
    
    # Exact matches
    if time1_clean == time2_clean:
        return 1.0
    
    # Immediate time matches
    immediate_times = ["now", "soon", "asap", "immediately"]
    if any(t in time1_clean for t in immediate_times) and any(t in time2_clean for t in immediate_times):
        return 1.0
    
    # Clear incompatibilities
    incompatible_pairs = [
        (["breakfast", "morning", "am"], ["dinner", "evening", "night", "pm"]),
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

def has_hour_conflict(time1: str, time2: str) -> bool:
    """Check for obvious hour conflicts like 7pm vs 12am"""
    
    import re
    
    # Skip range times - let smart assessment handle them
    if 'between' in time1 or 'between' in time2:
        return False
    
    # Extract hours from times like "7pm", "12am", "around 7pm"
    hour_pattern = r'(\d{1,2})\s*(am|pm)'
    
    match1 = re.search(hour_pattern, time1)
    match2 = re.search(hour_pattern, time2)
    
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
    
    time1_lower = time1.lower().strip()
    time2_lower = time2.lower().strip()
    
    print(f"   🧠 Smart time assessment: '{time1}' vs '{time2}'")
    
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
            
            # FIXED: Call get_user_preferences directly (not .invoke())
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
        if ('lunch' in time.lower() and 'lunch' in pattern_time.lower()) or \
           ('dinner' in time.lower() and 'dinner' in pattern_time.lower()) or \
           ('now' in time.lower() and 11 <= current_hour <= 14):  # Lunch hours
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
    
    update_user_memory(rejecting_user, {
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
        user_history = get_user_preferences(phone_number)
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
    
    # Get user's name or create friendly identifier
    user_name = user_history.get('preferences', {}).get('name', 'friend')
    past_orders = len(user_history.get('successful_matches', []))
    
    enhancement_prompt = f"""
    Enhance this message to be more friendly and contextual:
    
    Original message: "{message}"
    Message type: {message_type}
    User context: {past_orders} previous successful orders
    
    Make it sound like a helpful friend who knows them, but keep it brief and natural.
    Add appropriate emojis and personality. Don't be overly enthusiastic.
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

# ===== ROUTING PATTERN =====
def classify_message_intent_node(state: PangeaState) -> PangeaState:
    """Anthropic's Routing pattern - classify input and direct to specialized task"""
    
    last_message = state['messages'][-1].content
    user_phone = state['user_phone']
    
    # FIRST: Check if user has active order session - this takes priority
    try:
        session = db.collection('order_sessions').document(user_phone).get()
        if session.exists:
            # User has active order session, send to order processor
            state['conversation_stage'] = "order_continuation"
            return state
    except Exception as e:
        print(f"Error checking order session: {e}")
    
    # Check if first-time user
    user_doc = db.collection('users').document(user_phone).get()
    if not user_doc.exists:
        state['conversation_stage'] = "welcome_new_user"
        return state
    
    # SECOND: Check if this is a response to a group invitation
    try:
        # Check for old negotiation-based invitations
        pending_negotiations = db.collection('negotiations')\
                               .where('to_user', '==', user_phone)\
                               .where('status', '==', 'pending')\
                               .limit(1).get()
        
        # Check for new perfect match group invitations  
        pending_groups = db.collection('active_groups')\
                          .where('members', 'array_contains', user_phone)\
                          .where('status', '==', 'pending_responses')\
                          .limit(1).get()
        
        # ALSO check for 'forming' status groups in case of race condition
        forming_groups = db.collection('active_groups')\
                          .where('members', 'array_contains', user_phone)\
                          .where('status', '==', 'forming')\
                          .limit(1).get()
        
        if len(pending_negotiations) > 0 or len(pending_groups) > 0 or len(forming_groups) > 0:
            # This user has a pending group invitation (either type)
            message_lower = last_message.lower().strip()
            if 'yes' in message_lower or 'y' == message_lower or 'sure' in message_lower or 'ok' in message_lower:
                state['conversation_stage'] = "group_response_yes"
                return state
            elif 'no' in message_lower or 'n' == message_lower or 'pass' in message_lower or 'nah' in message_lower:
                state['conversation_stage'] = "group_response_no"
                return state
    except Exception as e:
        print(f"Error checking pending invitations: {e}")
    
    # THIRD: Check if this is a response to proactive group notifications
    proactive_notification = check_pending_proactive_notifications(user_phone)
    if proactive_notification:
        message_lower = last_message.lower().strip()
        if 'yes' in message_lower or 'y' == message_lower or 'sure' in message_lower or 'ok' in message_lower:
            state['conversation_stage'] = "proactive_group_yes"
            state['proactive_notification_data'] = proactive_notification
            return state
        elif 'no' in message_lower or 'n' == message_lower or 'pass' in message_lower or 'nah' in message_lower:
            state['conversation_stage'] = "proactive_group_no"
            state['proactive_notification_data'] = proactive_notification
            return state
    
    # If not a group response, use LLM to classify intent
    classification_prompt = f"""
    Classify this message intent for a food delivery matching service:
    
    Message: "{last_message}"
    
    Options:
    - spontaneous_order: User wants food now/soon (e.g., "I want pizza", "ordering lunch", "hungry for burgers")
    - morning_response: Response to "where will you be today" question  
    - preference_update: User updating their food preferences
    - group_response: Response to a group invitation
    - general_question: Questions ABOUT the service, help requests, greetings (e.g., "what restaurants are available?", "how does this work?", "hello")
    
    Return only the classification.
    """
    
    response = anthropic_llm.invoke([HumanMessage(content=classification_prompt)])
    intent = response.content.strip().lower()
    
    # If it's a general question OR no clear intent is found, try FAQ fallback
    if intent == 'general_question' or intent not in ['spontaneous_order', 'morning_response', 'preference_update', 'group_response', 'general_question']:
        faq_answer = answer_faq_question(last_message)
        if faq_answer and not faq_answer.lower().startswith("sorry"):
            send_friendly_message(user_phone, faq_answer, message_type="general")
            state['messages'].append(AIMessage(content=faq_answer))
            state['conversation_stage'] = "faq_answered"
            return state
    
    state['conversation_stage'] = intent
    return state

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

def analyze_spontaneous_request_node(state: PangeaState) -> PangeaState:
   """Agent analyzes spontaneous food request with better extraction"""
   
   user_message = state['messages'][-1].content
   user_phone = state['user_phone']
   print(f"🔍 User said: '{user_message}'")
   
   # 🧹 CLEAN SLATE: Remove ALL old data for this user when they make a new request
   cleanup_all_user_data(user_phone)
   
   # Extract request data using Claude
   analysis_prompt = f"""
You are a smart location-matching agent. Extract information from this food request:

User message: "{user_message}"

AVAILABLE LOCATIONS (you MUST pick exactly one):
- Richard J Daley Library
- Student Center East
- Student Center West
- Student Services Building
- University Hall

AVAILABLE RESTAURANTS (pick the BEST match):
- Chipotle
- McDonald's
- Chick-fil-A
- Portillo's
- Starbucks

RESTAURANT MATCHING RULES:
- "McDonald's" or "McDonalds" → "McDonald's"
- "Chipotle" → "Chipotle"
- "Chick-fil-A" or "Chickfila" or "Chick fil A" → "Chick-fil-A"
- "Portillo's" or "Portillos" → "Portillo's"
- "Starbucks" or "coffee" → "Starbucks"
- If NO specific restaurant mentioned → "any"

LOCATION MATCHING RULES:
- "library" or "daley" → "Richard J Daley Library"
- "student center east" or "sce" → "Student Center East"
- "student center west" or "scw" → "Student Center West"
- "student services" or "ssb" → "Student Services Building"
- "university hall" or "uh" → "University Hall"
- If NO specific location mentioned → "Richard J Daley Library" (default)

IMPORTANT: For time, preserve the EXACT user intent. Don't convert to generic terms.

Return ONLY this JSON format:
{{"restaurant": "exact match from list", "location": "exact match from list", "time_preference": "PRESERVE EXACT USER TIME"}}
"""
   
   response = anthropic_llm.invoke([HumanMessage(content=analysis_prompt)])
   try:
       request_data = json.loads(response.content.strip())
       print(f"✅ Agent extracted: {request_data}")
   except Exception as e:
       print(f"❌ Agent extraction failed: {e}")
       request_data = {"restaurant": "any", "location": "Richard J Daley Library", "time_preference": "now"}
   
   # VALIDATE: Check if we have required information
   missing_info = []
   
   # Check if restaurant/food is missing or too generic
   restaurant = request_data.get('restaurant', '').lower()
   if restaurant in ['any', '', 'food', 'something'] or not restaurant:
       missing_info.append('restaurant')
   
   # Check if location is missing or defaulted
   location = request_data.get('location', '')
   if location == 'Richard J Daley Library' and 'library' not in user_message.lower() and 'daley' not in user_message.lower():
       # This was likely a default, not user-specified
       if not any(loc_word in user_message.lower() for loc_word in ['library', 'daley', 'student center', 'sce', 'scw', 'university hall', 'student services']):
           missing_info.append('location')
   
   # If information is missing, ask for clarification
   if missing_info:
       state['conversation_stage'] = 'incomplete_request'
       state['missing_info'] = missing_info
       state['partial_request'] = request_data
       
       # Generate a helpful message asking for missing information
       if 'restaurant' in missing_info and 'location' in missing_info:
           clarification = "I'd love to help you order food! Could you tell me:\n\n1️⃣ What restaurant/food do you want?\n2️⃣ Where should it be delivered?\n\nExample: \"I want Chipotle delivered to the library\""
       elif 'restaurant' in missing_info:
           clarification = f"Great! I can help with delivery to {location}. What restaurant or food are you craving?\n\nAvailable: Chipotle, McDonald's, Chick-fil-A, Portillo's, Starbucks"
       elif 'location' in missing_info:
           clarification = f"Perfect! {restaurant} sounds good. Where would you like it delivered?\n\nAvailable locations:\n• Richard J Daley Library\n• Student Center East\n• Student Center West\n• Student Services Building\n• University Hall"
       
       send_friendly_message(state['user_phone'], clarification, message_type="clarification")
       state['messages'].append(AIMessage(content=clarification))
       return state
   
   # If we have all required info, continue with normal flow
   state['current_request'] = request_data
   state['conversation_stage'] = 'spontaneous_matching'
   
   # CRITICAL FIX: Search BEFORE creating our order to avoid race conditions
   print(f"🔍 IMMEDIATE SEARCH before creating order for {state['user_phone']}")
   
   # Search immediately to see if there are existing matches
   existing_matches = find_potential_matches(
       restaurant_preference=request_data.get('restaurant', ''),
       location=request_data.get('location', ''),
       time_window=request_data.get('time_preference', 'now'),
       requesting_user=state['user_phone']
   )
   
   print(f"🔍 Found {len(existing_matches)} existing matches before creating order")
   
   # CLEAN UP OLD ACTIVE ORDERS FOR THIS USER
   try:
       old_orders = db.collection('active_orders')\
                     .where('user_phone', '==', state['user_phone'])\
                     .where('status', '==', 'looking_for_group')\
                     .get()
       
       for old_order in old_orders:
           old_order.reference.delete()
           print(f"🗑️ Removed old active order for {state['user_phone']}")
   except Exception as e:
       print(f"❌ Failed to clean old orders: {e}")
   
   # CREATE THE NEW ACTIVE ORDER with immediate processing flag
   try:
       order_doc_data = {
           'user_phone': state['user_phone'],
           'restaurant': request_data.get('restaurant', ''),
           'location': request_data.get('location', ''),
           'time_requested': request_data.get('time_preference', 'now'),
           'status': 'looking_for_group',
           'created_at': datetime.now(),
           'flexibility_score': 0.5,
           'has_existing_matches': len(existing_matches) > 0
       }
       
       db.collection('active_orders').add(order_doc_data)
       print(f"✅ Created active order for {state['user_phone']} - Restaurant: {request_data.get('restaurant')}, Location: {request_data.get('location')}, Time: {request_data.get('time_preference')}")
       
       # Store existing matches in state for immediate processing
       state['potential_matches'] = existing_matches
       
   except Exception as e:
       print(f"❌ Failed to create active order: {e}")
   
   return state

# REPLACE realtime_search_node function with this:

def realtime_search_node(state: PangeaState) -> PangeaState:
    """Agent searches for immediate matches with better concurrency handling"""
    
    # Increment search attempts
    search_attempt = state.get('search_attempts', 0) + 1
    state['search_attempts'] = search_attempt
    
    request = state['current_request']
    
    # CRITICAL: Check if we already have matches from analyze_spontaneous_request_node
    if search_attempt == 1 and state.get('potential_matches'):
        print(f"🚀 Using existing matches found during order creation: {len(state['potential_matches'])}")
        matches = state['potential_matches']
        # DON'T overwrite state['potential_matches'] when using existing matches!
    else:
        # Add strategic delay for subsequent searches
        if search_attempt > 1:
            import time
            delay = min(3.0 + (search_attempt * 2), 10.0)  # Progressive delay, max 10s
            print(f"⏳ Waiting {delay}s before search attempt {search_attempt}")
            time.sleep(delay)
        
        # Use the ACTUAL time preference, not hardcoded "now"
        time_window = request.get('time_preference', 'now')
        
        # Find matches with the user's actual time preference
        matches = find_potential_matches(
            restaurant_preference=request.get('restaurant', ''),
            location=request.get('location', ''),
            time_window=time_window,
            requesting_user=state['user_phone']
        )
        
        # Only update state when we actually searched for new matches
        state['potential_matches'] = matches
    
    # CRITICAL: Only send negotiation message for NON-PERFECT matches on FIRST search
    if matches and search_attempt == 1:
        # Check if we have perfect matches - they get different handling
        perfect_matches = [match for match in matches if match.get('compatibility_score', 0) >= 0.8]
        
        if not perfect_matches:
            # Only send negotiation message for non-perfect matches
            restaurant = request.get('restaurant', 'food')
            location = request.get('location', 'campus')
            time_pref = request.get('time_preference', 'now')
            
            friendly_response = f"""Perfect timing! I found {len(matches)} people who also want {restaurant} around {time_pref} at {location}! 

I'm checking with their AI friends to see if they want to team up for group delivery. Give me 30 seconds... 🤝"""
            
            send_friendly_message(state['user_phone'], friendly_response, message_type="negotiation")
            state['messages'].append(AIMessage(content=friendly_response))
        else:
            print(f"🎯 Perfect matches found, skipping negotiation message")
    
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
        
        # Send SMS invitations to both users
        restaurant = state['current_request'].get('restaurant', 'local restaurant')
        delivery_time = state['current_request'].get('time_preference', 'ASAP')
        delivery_location = state['current_request'].get('delivery_location', 'campus')
        
        for phone in sorted_phones:
            invitation_message = f"🍕 Perfect match found! Someone nearby wants {restaurant} delivered to {delivery_location} at {delivery_time}. Want to split the order and save on delivery? Reply YES to join or NO to pass."
            
            success = send_friendly_message(phone, invitation_message, message_type="match_found")
            if success:
                print(f"📱 Sent invitation SMS to {phone}")
            else:
                print(f"❌ Failed to send SMS to {phone}")
        
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

def multi_agent_negotiation_node(state: PangeaState) -> PangeaState:
    """Advanced autonomous negotiation with better perfect match handling"""
    
    # ADD THIS DEBUG AT THE VERY TOP
    print(f"🔍 ENTERING multi_agent_negotiation_node for user: {state['user_phone']}")
    print(f"🔍 Current request: {state['current_request']}")
    
    request = state['current_request']
    matches = state['potential_matches']
    
    # Enhanced negotiation with Claude 4's planning capabilities
    negotiations = state.get('active_negotiations', [])
    
    # Check for perfect matches - immediately form groups without negotiation
    perfect_matches = [match for match in matches if match.get('compatibility_score', 0) >= 0.8]
    
    print(f"🔍 DEBUG: Found {len(matches)} total matches")
    for i, match in enumerate(matches):
        print(f"   Match {i+1}: score={match.get('compatibility_score', 0)}, user={match.get('user_phone')}")
    print(f"🔍 DEBUG: {len(perfect_matches)} perfect matches (>= 0.8)")
    
    if perfect_matches:
        # Perfect match found! Use single-writer pattern to prevent race conditions
        match = perfect_matches[0]  # Take the first perfect match
        
        # Deterministic writer selection - only lower phone number creates the group
        sorted_phones = sorted([state['user_phone'], match['user_phone']])
        deterministic_group_id = f"match_{sorted_phones[0]}_{sorted_phones[1]}"
        
        print(f"📋 Perfect match pair: {sorted_phones}")
        print(f"📋 Current user: {state['user_phone']}")
        print(f"📋 Group creator (lower phone): {sorted_phones[0]}")
        
        # Single-writer pattern: Only the user with the lower phone number creates the group
        if state['user_phone'] == sorted_phones[0]:
            print(f"👑 I am the group creator - creating group and sending invitations")
            create_group_and_send_invitations(state, match, deterministic_group_id, sorted_phones)
        else:
            print(f"👤 I am the matched user - marking as matched and waiting for invitation")
            mark_as_matched_user(state, sorted_phones[0], deterministic_group_id)
        
        # Mark that perfect match group was handled
        state['group_formed'] = True
        return state
        
        # Removed complex transaction code - single-writer pattern is much simpler!
    
    # No perfect matches - proceed with negotiations for imperfect matches
    for match in matches:
        negotiation_id = str(uuid.uuid4())
        
        # Enhanced proposal structure
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
        
        # FIXED: Call negotiate_with_other_ai directly (not .invoke())
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
    
    # Send immediate feedback to user only if we have negotiations
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
    """Handle YES response to group invitation and start order process"""
    
    user_phone = state['user_phone']
    
    try:
        # Check for perfect match group invitations first (new system)
        # Check both pending_responses and forming status to handle race conditions
        pending_groups = db.collection('active_groups')\
                          .where('members', 'array_contains', user_phone)\
                          .where('status', 'in', ['pending_responses', 'forming'])\
                          .limit(1).get()
        
        if len(pending_groups) > 0:
            # Handle perfect match group response
            group_doc = pending_groups[0]
            group_data = group_doc.to_dict()
            
            # Check if user already responded to avoid double-processing
            responses_received = group_data.get('responses_received', [])
            if user_phone in responses_received:
                print(f"⚠️ {user_phone} already responded to group {group_data['group_id']}")
                state['messages'].append(AIMessage(content="Already responded to perfect match group"))
                return state
            
            # Extract group information
            group_id = group_data['group_id']
            restaurant = group_data['restaurant']
            delivery_time = group_data['delivery_time']
            group_size = len(group_data['members'])
            
            # Update group with this user's response
            group_doc.reference.update({
                'responses_received': firestore.ArrayUnion([user_phone]),
                'status': 'forming'  # Change status from pending_responses
            })
            
            # START ORDER PROCESS FOR THIS USER (FIXED VERSION)
            try:
                # FIXED: Import the start_order_process function properly
                from pangea_order_processor import start_order_process
                
                # FIXED: Call start_order_process with correct parameters
                order_session = start_order_process(
                    user_phone=user_phone,
                    group_id=group_id,
                    restaurant=restaurant,
                    group_size=group_size,
                    delivery_time=delivery_time
                )
                
                print(f"✅ Order process started successfully for {user_phone}")
                
            except Exception as order_error:
                print(f"❌ Error starting order process for {user_phone}: {order_error}")
                import traceback
                print(f"Full traceback: {traceback.format_exc()}")
                
                # Fallback: send manual order instructions
                try:
                    from pangea_order_processor import get_payment_amount
                    payment_amount = get_payment_amount(group_size)
                    
                    fallback_message = f"""Great! You're part of the {restaurant} group! 🎉

**Quick steps to get your food:**
1. Order directly from {restaurant} (app/website/phone) - choose PICKUP, not delivery
2. Come back here with your confirmation number or name for the order AND what you ordered

Once everyone's ready, your payment will be {payment_amount} 💳

Let me know if you need any help!"""
                    
                    send_friendly_message(user_phone, fallback_message, message_type="order_start")
                    
                except Exception as fallback_error:
                    print(f"❌ Even fallback failed: {fallback_error}")
                    send_friendly_message(
                        user_phone,
                        f"Great! You're part of the {restaurant} group! Setting up your order instructions...",
                        message_type="general"
                    )
            
            # Check if all members have responded
            updated_responses = responses_received + [user_phone]
            all_members = group_data['members']
            
            if len(updated_responses) >= len(all_members):
                # All members responded - start order process for everyone
                group_doc.reference.update({'status': 'active'})
                print(f"✅ All members responded to perfect match group {group_id}")
            
            state['messages'].append(AIMessage(content="Perfect match group YES response processed"))
            return state
        
        # Fall back to old negotiation system
        pending_negotiations = db.collection('negotiations')\
                               .where('to_user', '==', user_phone)\
                               .where('status', '==', 'pending')\
                               .limit(1).get()
        
        if len(pending_negotiations) > 0:
            negotiation_doc = pending_negotiations[0]
            negotiation_data = negotiation_doc.to_dict()
            
            # DEBUG: Check if this user should be handled by perfect match system instead
            print(f"⚠️ {user_phone} fell through to old negotiation system - checking if perfect match user")
            
            # Safety check: If user has an active perfect match group, don't process old negotiation
            try:
                active_groups = db.collection('active_groups')\
                              .where('members', 'array_contains', user_phone)\
                              .where('status', 'in', ['pending_responses', 'forming', 'active'])\
                              .limit(1).get()
                              
                if len(active_groups) > 0:
                    print(f"🚨 CONFLICT: {user_phone} has perfect match group but also old negotiation - cleaning up old negotiation")
                    negotiation_doc.reference.update({'status': 'obsolete'})
                    state['messages'].append(AIMessage(content="Cleaned up conflicting old negotiation"))
                    return state
            except Exception as e:
                print(f"Error checking for group conflict: {e}")
            
            # 1. collect essentials BEFORE accepting
            requesting_user = negotiation_data['from_user']
            other_accepted = db.collection('negotiations')\
                                 .where('from_user', '==', requesting_user)\
                                 .where('status', '==', 'accepted')\
                                 .get()
            proposed_group_size = len(other_accepted) + 2  # requester + this user + accepted others
            
            print(f"🔍 OLD SYSTEM: requester={requesting_user}, other_accepted={len(other_accepted)}, proposed_size={proposed_group_size}")
            
            # 2. FULL-GROUP gate
            if proposed_group_size > MAX_GROUP_SIZE:
                send_friendly_message(
                    user_phone,
                    "Sorry, that group filled up just before you replied. "
                    "I'll look for another match right away! 🔄",
                    message_type="general"
                )
                negotiation_doc.reference.update({'status': 'declined_full'})
                state['messages'].append(AIMessage(content="Group response YES rejected (group full)"))
                return state
            
            # 3. we still have room – now mark the negotiation accepted
            negotiation_doc.reference.update({'status': 'accepted'})
            
            proposal = negotiation_data.get('proposal', {})
            restaurant = (
                proposal.get('restaurant')
                or proposal.get('primary_restaurant')
                or proposal.get('restaurant_name')
                or 'Unknown Restaurant'
            )
            
            group_id = negotiation_data['negotiation_id']
            group_size = proposed_group_size
            
            # START ORDER PROCESS FOR THIS USER (FIXED VERSION)
            delivery_time = proposal.get('time', 'now')
            
            try:
                # FIXED: Import and call start_order_process properly
                from pangea_order_processor import start_order_process
                
                order_session = start_order_process(
                    user_phone=user_phone,
                    group_id=group_id,
                    restaurant=restaurant,
                    group_size=group_size,
                    delivery_time=delivery_time
                )
                
                # Also start for requester if not started
                try:
                    from pangea_order_processor import get_user_order_session
                    requesting_user_session = get_user_order_session(requesting_user)
                    
                    if not requesting_user_session:  # Check if session exists properly
                        requester_order_session = start_order_process(
                            user_phone=requesting_user,
                            group_id=group_id,
                            restaurant=restaurant,
                            group_size=group_size,
                            delivery_time=delivery_time
                        )
                        print(f"✅ Also started order process for requester {requesting_user}")
                
                except Exception as requester_error:
                    print(f"❌ Error starting requester order process: {requester_error}")
                
                print(f"✅ Order process started for both users in negotiation {group_id}")
                
            except Exception as e:
                print(f"❌ Error starting order process for negotiation: {e}")
                import traceback
                print(f"Full traceback: {traceback.format_exc()}")
                
                # Send fallback message
                send_friendly_message(
                    user_phone, 
                    f"Great! You're part of the {restaurant} group! Setting up your order instructions...", 
                    message_type="general"
                )
            
            print(f"✅ Group accepted and order process started: {negotiation_data['negotiation_id']}")
        
        else:
            send_friendly_message(
                user_phone,
                "I don't see any pending group invitations for you right now. Want to start a new food order?",
                message_type="general"
            )
            
    except Exception as e:
        print(f"Error handling group response YES: {e}")
        import traceback
        print(f"Full traceback: {traceback.format_exc()}")
        send_friendly_message(
            user_phone,
            "Something went wrong processing your response. Can you try again?",
            message_type="general"
        )
    
    state['messages'].append(AIMessage(content="Group response YES processed"))
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
    """
    Terminal step after answering a general FAQ.
    Does nothing except return the state unchanged.
    """
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
    try:
        # Check for any active groups (pending_responses, forming, or active)
        user_groups = db.collection('active_groups')\
                      .where('members', 'array_contains', user_phone)\
                      .where('status', 'in', ['pending_responses', 'forming', 'active'])\
                      .limit(1).get()
        
        if len(user_groups) > 0:
            group_data = user_groups[0].to_dict()
            group_status = group_data.get('status')
            print(f"🛑 User {user_phone} is already in group with status '{group_status}' - stopping search")
            return "wait_for_responses"
            
    except Exception as e:
        print(f"⚠️ Error checking for active groups: {e}")
    
    # ALSO CHECK: If user has an active order session, they shouldn't be searching
    try:
        from pangea_order_processor import get_user_order_session
        session = get_user_order_session(user_phone)
        if session:
            print(f"🛑 User {user_phone} has active order session - stopping search")
            return "wait_for_responses"
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
    update_user_memory(state['user_phone'], {
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
    
    user_prefs = get_user_preferences(state['user_phone'])
    
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
    
    # Get stored missing info and partial request
    missing_info = state.get('missing_info', [])
    partial_request = state.get('partial_request', {})
    
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
            
            # Stay in incomplete_request state
            return state
            
        # We have all the info! Continue with normal flow
        state['current_request'] = updated_request
        state['conversation_stage'] = 'spontaneous_matching'
        
        # Clear the incomplete request data
        if 'missing_info' in state:
            del state['missing_info']
        if 'partial_request' in state:
            del state['partial_request']
            
        print(f"✅ Request completed! Moving to matching flow: {updated_request}")
        
        return state
        
    except Exception as e:
        print(f"❌ Error processing incomplete request: {e}")
        
        # Fallback: treat as new request
        state['conversation_stage'] = 'spontaneous_order'
        return state

# ===== MAIN LANGGRAPH WITH 2025 ENHANCEMENTS =====
def create_pangea_graph():
    """
    Create enhanced LangGraph using 2025 best practices and Claude 4 capabilities.
    
    Implements Anthropic's recommended patterns with advanced reasoning and learning.
    Now includes group response handling for YES/NO replies to invitations.
    """
    
    # Initialize the StateGraph with enhanced capabilities
    workflow = StateGraph(PangeaState)
    
    # Add all nodes with enhanced functionality
    workflow.add_node("classify_intent", classify_message_intent_node)
    workflow.add_node("welcome_new_user", welcome_new_user_node)
    workflow.add_node("morning_greeting", morning_greeting_node)
    workflow.add_node("process_morning_response", process_morning_response_node)
    workflow.add_node("present_morning_matches", present_morning_matches_node)
    workflow.add_node("analyze_spontaneous", analyze_spontaneous_request_node)
    workflow.add_node("realtime_search", realtime_search_node)
    workflow.add_node("negotiate", multi_agent_negotiation_node)
    workflow.add_node("finalize_group", finalize_group_node)
    workflow.add_node("handle_no_matches", handle_no_matches_node)
    workflow.add_node("wait_for_responses", wait_for_responses_node)
    workflow.add_node("handle_order_continuation", handle_order_continuation_node)
    workflow.add_node("faq_answered", faq_answered_node) 
    workflow.add_node("handle_incomplete_request", handle_incomplete_request_node)
    
    # ADD NEW GROUP RESPONSE NODES
    workflow.add_node("handle_group_yes", handle_group_response_yes_node)
    workflow.add_node("handle_group_no", handle_group_response_no_node)
    workflow.add_node("handle_alternative_response", handle_alternative_response_node)
    workflow.add_node("handle_proactive_group_yes", handle_proactive_group_yes_node)
    workflow.add_node("handle_proactive_group_no", handle_proactive_group_no_node)
    
    # Enhanced conditional routing with Claude 4 reasoning and group response handling
    workflow.add_conditional_edges(
        "classify_intent",
        route_based_on_intent,
        {
            "welcome_new_user": "welcome_new_user",
            "morning_response": "process_morning_response", 
            "spontaneous_order": "analyze_spontaneous",
            "preference_update": "process_morning_response",
            "group_response_yes": "handle_group_yes",  # NEW: Handle YES to group invitation
            "group_response_no": "handle_group_no",    # NEW: Handle NO to group invitation
            "alternative_response": "handle_alternative_response",  # NEW: Handle alternative response
            "proactive_group_yes": "handle_proactive_group_yes",  # NEW: Handle YES to proactive notification
            "proactive_group_no": "handle_proactive_group_no",
             "order_continuation": "handle_order_continuation",
            "faq_answered": "faq_answered",  # ← ADD THIS LINE# NEW: Handle NO to proactive notification
            "incomplete_request": "handle_incomplete_request",
        }
    )
    
    # Morning workflow chain (Anthropic's Prompt Chaining pattern)
    workflow.add_edge("process_morning_response", "present_morning_matches")
    workflow.add_edge("present_morning_matches", END)
    workflow.add_edge("faq_answered", END)
    
    # Enhanced spontaneous agent flow with learning
    workflow.add_edge("analyze_spontaneous", "realtime_search")
    workflow.add_edge("realtime_search", "negotiate")
    
    # Handle incomplete request flow - route based on conversation_stage
    workflow.add_conditional_edges(
        "handle_incomplete_request",
        lambda state: state.get('conversation_stage', 'incomplete_request'),
        {
            "spontaneous_matching": "realtime_search",
            "incomplete_request": END,  # Stay in incomplete state if still missing info
            "spontaneous_order": "analyze_spontaneous"  # Fallback to re-analyze
        }
    )
    workflow.add_conditional_edges(
        "negotiate",
        should_continue_negotiating,
        {
            "finalize_group": "finalize_group",
            "wait_for_responses": "wait_for_responses",  # ✅ Goes to wait node instead!
            "expand_search": "realtime_search",  
            "no_group_found": "handle_no_matches"
        }
    )
    
    # Final outcome nodes
    workflow.add_edge("finalize_group", END)
    workflow.add_edge("handle_no_matches", END)
    workflow.add_edge("welcome_new_user", END)
    workflow.add_edge("wait_for_responses", END)
    workflow.add_edge("handle_group_yes", END)  # NEW: Group YES response ends workflow
    workflow.add_edge("handle_group_no", END)   # NEW: Group NO response ends workflow
    workflow.add_edge("handle_alternative_response", END)  # NEW: Alternative response ends workflow
    workflow.add_edge("handle_proactive_group_yes", END)  # NEW: Proactive group YES ends workflow
    workflow.add_edge("handle_proactive_group_no", END) # NEW: Proactive group NO ends workflow
    workflow.add_edge("handle_order_continuation", END)
    # Set entry point
    workflow.set_entry_point("classify_intent")

    # Terminal FAQ answered node
    

    
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
    
    # FIXED: Call update_user_memory directly (not .invoke())
    for member in all_members:
        update_user_memory(
            phone_number=member,
            interaction_data={
                'interaction_type': 'successful_group_formation',
                'group_members': all_members,
                'restaurant': restaurant,
                'location': state['current_request'].get('location'),
                'group_size': group_size,
                'optimal_time': optimal_time,
                'formation_time': datetime.now(),
                'group_id': group_id
            }
        )
    
    state['final_group'] = {
        'members': all_members,
        'restaurant': restaurant,
        'optimal_time': optimal_time,
        'status': 'confirmed',
        'group_id': group_id
    }
    state['messages'].append(AIMessage(content=success_message))
    
    return state

def handle_no_matches_node(state: PangeaState) -> PangeaState:
    """
    When no matches found, send COMBINED message and trigger order processor flow
    """
    user_phone = state['user_phone']
    restaurant = state['current_request'].get('restaurant', 'that spot')
    location = state['current_request'].get('location', 'campus')
    
    # Prevent multiple solo messages per request
    if state.get('solo_message_sent'):
        print(f"🚫 Solo message already sent for {user_phone}, skipping")
        return state
    
    # Create a fake group_id for solo ordering
    solo_group_id = f"solo_{str(uuid.uuid4())}"
    delivery_time = state['current_request'].get('time_preference', 'now')
    
    # COMBINED MESSAGE: Match announcement + Order instructions
    combined_message = f"""Hey! 👋 Great news - found someone nearby who's also craving {restaurant}, so you can split the delivery fee!

Your share will only be $2.50-$3.50 instead of the full amount. Pretty sweet deal 🙌

**Quick steps to get your food:**
1. Order directly from {restaurant} (app/website/phone) - just make sure to choose PICKUP, not delivery
2. Come back here with your confirmation number or name for the order AND what you ordered

Once everyone's ready, your payment will be $3.50 💳

Let me know if you need any help!"""
    
    # Send the COMBINED message
    send_friendly_message(user_phone, combined_message, message_type="general")
    
    # Mark that solo message has been sent for this request
    state['solo_message_sent'] = True
    
    # Clean up only OLD active orders for this user (older than 5 minutes to allow concurrent matching)
    try:
        from datetime import timedelta
        cutoff_time = datetime.now() - timedelta(minutes=5)
        
        old_orders = db.collection('active_orders')\
                      .where('user_phone', '==', user_phone)\
                      .where('status', '==', 'looking_for_group')\
                      .where('created_at', '<', cutoff_time)\
                      .get()
        
        for old_order in old_orders:
            old_order.reference.delete()
            print(f"🗑️ Cleaned up old solo order for {user_phone} (older than 5 min)")
        
        # DON'T clean up current active orders - leave them findable for other users
        print(f"🔄 Leaving current order findable for future matches")
            
    except Exception as e:
        print(f"❌ Failed to clean up solo orders: {e}")
    
    # NOW trigger the order processor flow (FIXED VERSION)
    try:
        # FIXED: Import and call start_order_process properly
        from pangea_order_processor import start_order_process
        
        order_session = start_order_process(
            user_phone=user_phone,
            group_id=solo_group_id,
            restaurant=restaurant,
            group_size=1,  # Solo order (fake match)
            delivery_time=delivery_time
        )
        
        print(f"✅ Started solo order process for {user_phone} - {restaurant} at {delivery_time}")
        
        # FIXED: Call update_user_memory directly (not .invoke())
        update_user_memory(
            phone_number=user_phone,
            interaction_data={
                "interaction_type": "fake_match_solo_order",
                "restaurant": restaurant,
                "location": location,
                "timestamp": datetime.now(),
            }
        )
        
    except Exception as e:
        print(f"❌ Failed to start solo order process: {e}")
        import traceback
        print(f"Full traceback: {traceback.format_exc()}")

    state['messages'].append(AIMessage(content=combined_message))
    return state

# ===== TWILIO WEBHOOK HANDLER =====
def handle_incoming_sms(phone_number: str, message_body: str):
    """Handle incoming SMS and route through LangGraph"""
    
    # Initialize state
    initial_state = PangeaState(
        messages=[HumanMessage(content=message_body)],
        user_phone=phone_number,
        user_preferences={},
        current_request={},
        potential_matches=[],
        active_negotiations=[],
        final_group=None,
        conversation_stage="initial",
        search_attempts=0,
        rejection_data=None,
        alternative_suggestions=[],
        proactive_notification_data=None
    )
    
    # Run through LangGraph
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

@app.route('/webhook/sms', methods=['POST'])
def sms_webhook():
    """Handle incoming SMS from Twilio with proper routing between order processor and main system"""
    try:
        from_number = request.form.get('From')
        message_body = request.form.get('Body')
        
        print(f"📱 SMS from {from_number}: {message_body}")
        
        # Import the classification function from order processor
        from pangea_order_processor import is_new_food_request, get_user_order_session
        
        # Strategy: Check for existing order sessions FIRST, then route new requests appropriately
        
        # 1. Check if user has an existing order session (priority routing to order processor)
        existing_session = get_user_order_session(from_number)
        
        if existing_session:
            print(f"🔄 User has existing order session, routing to order processor first")
            # Try order processor first for users with active sessions
            order_result = process_order_message(from_number, message_body)
            
            if order_result is not None:
                # Message was successfully processed by order system
                print(f"✅ Order processed: {order_result.get('order_stage', 'unknown')}")
                return '', 200
            else:
                # Order processor couldn't handle it, fall back to main system
                print(f"🔄 Order processor couldn't handle message, falling back to main system")
        
        # 2. Check if message is a group response (YES/NO)
        message_lower = message_body.lower().strip()
        group_responses = ['yes', 'y', 'no', 'n', 'sure', 'ok', 'pass', 'nah']
        
        if message_lower in group_responses:
            print(f"🎯 DETECTED GROUP RESPONSE: '{message_body}' - routing directly to main system")
            result = handle_incoming_sms(from_number, message_body)
            print(f"✅ Main system processed group response: {result.get('conversation_stage', 'unknown')}")
            return '', 200
        
        # 3. For users without active sessions, check if it's a new food request
        if not existing_session and is_new_food_request(message_body):
            print(f"🆕 New food request detected, routing to main Pangea system")
            # Route new food requests directly to main system
            result = handle_incoming_sms(from_number, message_body)
            print(f"✅ Main system processed new request: {result.get('conversation_stage', 'unknown')}")
            return '', 200
        
        # 4. For users without sessions and non-food messages, try order processor first (might be payment/order details)
        if not existing_session:
            print(f"🔍 No session found, checking if order processor can handle non-food message")
            order_result = process_order_message(from_number, message_body)
            
            if order_result is not None:
                # Message was processed by order system (e.g., payment, order details)
                print(f"✅ Order processed: {order_result.get('order_stage', 'unknown')}")
                return '', 200
        
        # 5. Default fallback to main Pangea system
        print(f"🔄 Routing to main Pangea system as final fallback")
        result = handle_incoming_sms(from_number, message_body)
        print(f"✅ Main system processed: {result.get('conversation_stage', 'unknown')}")
        
        return '', 200
    except Exception as e:
        print(f"❌ Error in SMS webhook: {e}")
        # Fallback to main system on error
        try:
            result = handle_incoming_sms(from_number, message_body)
            print(f"✅ Error fallback to main system: {result.get('conversation_stage', 'unknown')}")
            return '', 200
        except Exception as fallback_error:
            print(f"❌ Fallback also failed: {fallback_error}")
            return '', 500

@app.route('/health', methods=['GET'])
def health_check():
    return {'status': 'healthy', 'service': 'Pangea AI Friend'}, 200

if __name__ == "__main__":
    print("🍜 Starting Pangea AI Friend System...")
    print("Ready to receive SMS messages!")
    app.run(host='0.0.0.0', port=8000, debug=True)