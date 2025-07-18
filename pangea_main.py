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

@tool
def find_potential_matches(
    restaurant_preference: str,
    location: str, 
    time_window: str,
    requesting_user: str,
    flexibility_score: float = 0.5
) -> List[Dict]:
    """
    Find compatible users for group food orders using database filtering.
    
    This tool queries the database for potential matches and uses the
    calculate_compatibility tool to score each candidate.
    
    Args:
        restaurant_preference: Specific restaurant name or cuisine type
        location: Campus location for delivery
        time_window: When user wants to eat (e.g., "now", "12:30pm", "lunch time")
        requesting_user: Phone number of requesting user
        flexibility_score: How flexible user is (0-1, higher = more flexible)
        
    Returns:
        List of compatible users with compatibility scores above 0.3
        
    Example:
        matches = find_potential_matches(
            restaurant_preference="Chipotle",
            location="Campus Center", 
            time_window="7pm",
            requesting_user="+1234567890",
            flexibility_score=0.7
        )
    """
    print(f"🔍 SEARCHING:")
    print(f"   Looking for: '{restaurant_preference}' at '{location}' ({time_window})")
    print(f"   Excluding: {requesting_user}")
    
    try:
        matches = []
        
        # Query database for potential candidates
        orders_ref = db.collection('active_orders')
        similar_orders = orders_ref.where('location', '==', location)\
                                  .where('status', '==', 'looking_for_group')\
                                  .where('user_phone', '!=', requesting_user)\
                                  .limit(10).get()
        
        print(f"📊 Found {len(similar_orders)} potential orders in database")
        
        # Filter out old orders (older than 2 hours for "now/soon", or from different meal periods)
        current_time = datetime.now()
        filtered_orders = []

        for order in similar_orders:
            order_data = order.to_dict()
            order_time = order_data.get('created_at')
            order_time_pref = order_data.get('time_requested', 'flexible')
            
            # Skip very old orders
            if order_time:
                try:
                    # Handle timezone differences by converting both to naive datetime
                    if hasattr(order_time, 'tzinfo') and order_time.tzinfo is not None:
                        # Convert timezone-aware to naive by removing timezone info
                        order_time = order_time.replace(tzinfo=None)
                    
                    if hasattr(current_time, 'tzinfo') and current_time.tzinfo is not None:
                        # Convert timezone-aware to naive by removing timezone info  
                        current_time = current_time.replace(tzinfo=None)
                    
                    time_diff = current_time - order_time
                    
                    # If someone said "now" or "soon" more than 2 hours ago, skip it
                    if order_time_pref in ['now', 'soon'] and time_diff > timedelta(hours=2):
                        print(f"   ⏰ Skipping stale order: {order_time_pref} from {time_diff} ago")
                        continue
                except Exception as e:
                    print(f"   ⚠️ Error comparing times, including order anyway: {e}")
                    # If there's any error with time comparison, just include the order
            
            filtered_orders.append(order)
        
        print(f"📊 After time filtering: {len(filtered_orders)} potential orders")
        
        # Use calculate_compatibility tool to score each candidate
        for order in filtered_orders:
            order_data = order.to_dict()
            print(f"   Checking: {order_data}")
            
            # Call the calculate_compatibility tool
            compatibility_score = calculate_compatibility.invoke({
                "user1_restaurant": restaurant_preference,
                "user1_time": time_window,
                "user2_restaurant": order_data.get('restaurant', ''),
                "user2_time": order_data.get('time_requested', 'flexible'),
                "user1_phone": requesting_user,
                "user2_phone": order_data['user_phone']
            })
            
            # Only include matches above threshold
            if compatibility_score > 0.3:
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
        return [{'error': f'Matching failed: {str(e)}'}]


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
    """Only use LLM for truly ambiguous cases"""
    
    simple_prompt = f"""
Two users want to order food:
Time 1: "{time1}"
Time 2: "{time2}"

Can they realistically order together for the same meal? 
Reply with just a number:
- 1.0 if times are compatible
- 0.0 if times are incompatible

Number:"""
    
    try:
        response = anthropic_llm.invoke([HumanMessage(content=simple_prompt)])
        
        # Extract number from response
        import re
        numbers = re.findall(r'\b[01]\.?\d*\b', response.content)
        if numbers:
            score = float(numbers[0])
            return min(max(score, 0.0), 1.0)
        
        # Fallback
        if "1.0" in response.content or "compatible" in response.content.lower():
            return 1.0
        else:
            return 0.0
            
    except Exception as e:
        print(f"   ❌ LLM assessment failed: {e}")
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

@tool 
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
        target_user_history = get_user_preferences.invoke({"phone_number": target_ai_user})
        
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

@tool
def generate_counter_proposal(
    rejected_proposal: Dict,
    declining_user_preferences: Dict,
    available_alternatives: List[Dict]
) -> Dict:
    """Generate intelligent counter-proposal after rejection"""
    
    counter_proposal_prompt = f"""
    A user rejected this proposal: {json.dumps(rejected_proposal, default=str)}
    
    User's preferences: {json.dumps(declining_user_preferences, default=str)}
    Available alternatives nearby: {json.dumps(available_alternatives, default=str)}
    
    Should I make a counter-proposal? Consider:
    1. User's favorite cuisines vs what they rejected
    2. Their typical eating times vs rejected time
    3. Success probability vs user annoyance risk
    4. Quality of available alternatives
    
    Return JSON:
    - "should_counter": true/false
    - "counter_proposal": {...} or null
    - "reasoning": "why this approach"
    
    If should_counter is false, return null for counter_proposal.
    """
    
    try:
        response = anthropic_llm.invoke([HumanMessage(content=counter_proposal_prompt)])
        result = json.loads(response.content)
        return result
    except Exception as e:
        return {"should_counter": False, "counter_proposal": None, "reasoning": f"Error: {e}"}

@tool
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
{group_size} people are already in - want to join? Reply YES to jump in!"""
    
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

def find_alternatives_for_user(user_phone: str, rejected_proposal: Dict) -> List[Dict]:
    """Find alternative group opportunities for users who decline"""
    
    user_prefs = get_user_preferences.invoke({"phone_number": user_phone})
    preferred_cuisines = user_prefs.get('preferences', {}).get('favorite_cuisines', ['any'])
    
    if not preferred_cuisines or preferred_cuisines == ['any']:
        # Use historical data or fallback to popular options
        preferred_cuisines = ['Thai Garden', 'Mario\'s Pizza', 'Sushi Express']
    
    alternative_opportunities = []
    
    for cuisine in preferred_cuisines[:2]:  # Check top 2 preferences
        matches = find_potential_matches.invoke({
            "restaurant_preference": cuisine,
            "location": rejected_proposal.get('location', 'Campus Center'),
            "time_window": "flexible",  # More flexible after rejection
            "requesting_user": user_phone
        })
        alternative_opportunities.extend(matches)
    
    # Remove duplicates and limit to top 3
    seen_users = set()
    unique_alternatives = []
    for alt in alternative_opportunities:
        if alt['user_phone'] not in seen_users:
            seen_users.add(alt['user_phone'])
            unique_alternatives.append(alt)
        if len(unique_alternatives) >= 3:
            break
    
    return unique_alternatives

def learn_from_rejection(rejecting_user: str, rejected_proposal: Dict, rejection_reason: str = None):
    """Learn from rejections to improve future matching"""
    
    update_user_memory.invoke({
        "phone_number": rejecting_user,
        "interaction_data": {
            'interaction_type': 'proposal_rejection',
            'rejected_restaurant': rejected_proposal.get('restaurant'),
            'rejected_time': rejected_proposal.get('time'),
            'rejected_location': rejected_proposal.get('location'),
            'rejection_reason': rejection_reason or 'not_specified',
            'timestamp': datetime.now(),
            'proposal_compatibility_score': rejected_proposal.get('compatibility_score', 0.0)
        }
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
    try:
        # Enhance message with Claude 4's conversational abilities
        user_history = get_user_preferences.invoke({"phone_number": phone_number})
        enhanced_message = enhance_message_with_context(message, message_type, user_history)
        
        twilio_client.messages.create(
            body=enhanced_message,
            from_=os.getenv('TWILIO_PHONE_NUMBER'),
            to=phone_number
        )
        
        # Log interaction for learning
        log_interaction(phone_number, {
            'message_sent': enhanced_message,
            'message_type': message_type,
            'timestamp': datetime.now()
        })
        
        return True
    except Exception as e:
        print(f"SMS failed: {e}")
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
        pending_negotiations = db.collection('negotiations')\
                               .where('to_user', '==', user_phone)\
                               .where('status', '==', 'pending')\
                               .limit(1).get()
        
        if len(pending_negotiations) > 0:
            # This user has a pending group invitation
            message_lower = last_message.lower().strip()
            if 'yes' in message_lower or 'y' == message_lower or 'sure' in message_lower or 'ok' in message_lower:
                state['conversation_stage'] = "group_response_yes"
                return state
            elif 'no' in message_lower or 'n' == message_lower or 'pass' in message_lower or 'nah' in message_lower:
                state['conversation_stage'] = "group_response_no"
                return state
    except Exception as e:
        print(f"Error checking pending negotiations: {e}")
    
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
    - spontaneous_order: User wants food now/soon
    - morning_response: Response to "where will you be today" question  
    - preference_update: User updating their food preferences
    - group_response: Response to a group invitation
    
    Return only the classification.
    """
    
    response = anthropic_llm.invoke([HumanMessage(content=classification_prompt)])
    intent = response.content.strip().lower()
    
    # If no clear intent is found, try FAQ fallback
    if intent not in ['spontaneous_order', 'morning_response', 'preference_update', 'group_response']:
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
    """Process user's morning response and find matches"""
    
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
    
    # Find matches
    matches = find_potential_matches.invoke({
        "restaurant_preference": preferences.get('food_preferences', [''])[0],
        "location": preferences.get('location', ''),
        "time_window": preferences.get('time_preference', 'lunch time'),
        "requesting_user": state['user_phone']
    })
    
    state['potential_matches'] = matches
    return state

def present_morning_matches_node(state: PangeaState) -> PangeaState:
    """Present matches to user in friendly way"""
    
    matches = state['potential_matches']
    
    if not matches:
        message = """I couldn't find anyone with similar lunch plans right now, but I'll keep looking! 

Want to tell me a specific restaurant you're craving? I might be able to find someone who's flexible! 🤔"""
    else:
        restaurant = state['current_request'].get('food_preferences', [''])[0]
        message = f"""Great news! I found {len(matches)} other people interested in {restaurant}! 

Let me reach out to their AI friends and see if we can form a group. I'll get back to you in just a minute! ⏰"""
        
        # Start negotiations with other AI Friends
        for match in matches:
            negotiation_id = str(uuid.uuid4())
            negotiate_with_other_ai.invoke({
                "target_ai_user": match['user_phone'],
                "proposal": {
                    'restaurant': restaurant,
                    'location': state['current_request'].get('location'),
                    'time': state['current_request'].get('time_preference'),
                    'requesting_user': state['user_phone']
                },
                "negotiation_id": negotiation_id
            })
    
    send_friendly_message(state['user_phone'], message, message_type="morning_checkin")
    state['messages'].append(AIMessage(content=message))
    return state

# ===== AGENT PATTERN (Spontaneous Orders) =====
def analyze_spontaneous_request_node(state: PangeaState) -> PangeaState:
    """Agent analyzes spontaneous food request with better extraction"""
    
    user_message = state['messages'][-1].content
    print(f"🔍 User said: '{user_message}'")
    
    # CLEAR ANY OLD ORDER SESSION since this is a new request
    try:
        db.collection('order_sessions').document(state['user_phone']).delete()
        print(f"🗑️ Cleared old order session for new request: {state['user_phone']}")
    except Exception as e:
        print(f"⚠️ No old session to clear: {e}")
    
    # UPDATED: Let Claude agent handle extraction intelligently
    # REPLACE the analysis_prompt in analyze_spontaneous_request_node with this:

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

Examples:
- "McDonald's at library at 5pm" → {{"restaurant": "McDonald's", "location": "Richard J Daley Library", "time_preference": "5pm"}}
- "Chipotle at student center" → {{"restaurant": "Chipotle", "location": "Student Center East", "time_preference": "now"}}
- "food at library" → {{"restaurant": "any", "location": "Richard J Daley Library", "time_preference": "now"}}

Return ONLY this JSON format:
{{"restaurant": "exact match from list", "location": "exact match from list", "time_preference": "PRESERVE EXACT USER TIME"}}
"""
    
    response = anthropic_llm.invoke([HumanMessage(content=analysis_prompt)])
    try:
        request_data = json.loads(response.content.strip())
        print(f"✅ Agent extracted: {request_data}")
    except Exception as e:
        print(f"❌ Agent extraction failed: {e}")
        # Simple fallback
        request_data = {"restaurant": "any", "location": "Campus Center", "time_preference": "now"}
    
    state['current_request'] = request_data
    state['conversation_stage'] = 'spontaneous_matching'
    
    # CREATE THE ACTIVE ORDER SO OTHER USERS CAN FIND IT
    try:
        db.collection('active_orders').add({
            'user_phone': state['user_phone'],
            'restaurant': request_data.get('restaurant', ''),
            'location': request_data.get('location', ''),
            'time_requested': request_data.get('time_preference', 'now'),
            'status': 'looking_for_group',
            'created_at': datetime.now(),
            'flexibility_score': 0.5
        })
        print(f"✅ Created active order for {state['user_phone']} - Restaurant: {request_data.get('restaurant')}, Location: {request_data.get('location')}, Time: {request_data.get('time_preference')}")
    except Exception as e:
        print(f"❌ Failed to create active order: {e}")
    
    return state


# REPLACE realtime_search_node function with this:

def realtime_search_node(state: PangeaState) -> PangeaState:
    """Agent searches for immediate matches"""
    
    # Increment search attempts
    state['search_attempts'] = state.get('search_attempts', 0) + 1
    
    request = state['current_request']
    
    # Use the ACTUAL time preference, not hardcoded "now"
    time_window = request.get('time_preference', 'now')
    
    # Find matches with the user's actual time preference
    matches = find_potential_matches.invoke({
        "restaurant_preference": request.get('restaurant', ''),
        "location": request.get('location', ''),
        "time_window": time_window,  # ✅ Use actual time instead of hardcoded 'now'
        "requesting_user": state['user_phone']
    })
    
    state['potential_matches'] = matches
    
    # If matches found, immediately start negotiation
    if matches:
        friendly_response = f"""Perfect timing! I found {len(matches)} people who might want {request.get('restaurant', 'food')} too! 

Give me 30 seconds to check with their AI friends... 🤝"""
        
        send_friendly_message(state['user_phone'], friendly_response, message_type="negotiation")
        state['messages'].append(AIMessage(content=friendly_response))
        
    return state

def multi_agent_negotiation_node(state: PangeaState) -> PangeaState:
    """
    Advanced autonomous negotiation using Claude 4's enhanced reasoning and planning.
    
    Implements sophisticated multi-turn negotiation with learning from outcomes.
    Uses Claude 4's extended thinking for complex decision-making.
    """
    
    request = state['current_request']
    matches = state['potential_matches']
    
    # Enhanced negotiation with Claude 4's planning capabilities
    negotiations = []
    
    # PROACTIVE NOTIFICATION: Check if we have viable matches to notify others
    if len(matches) >= 1:  # We have at least one match, group is forming
        print(f"🔔 Group is forming with {len(matches)} matches, notifying compatible users...")
        
        # Create preliminary group data for notifications
        preliminary_group_data = {
            "restaurant": request.get('restaurant'),
            "location": request.get('location'),
            "time": request.get('time_preference'),
            "current_members": [state['user_phone']],  # Just the requesting user for now
            "group_id": "preliminary_" + str(uuid.uuid4())
        }
        
        notify_result = notify_compatible_users_of_active_groups.invoke({
            "active_group_data": preliminary_group_data,
            "max_notifications": 2,  # Limit to 2 during formation
            "compatibility_threshold": 0.8  # Higher threshold during formation
        })
        
        print(f"🔔 Proactive notifications sent: {notify_result.get('notifications_sent', 0)}")
    
    for match in matches:
        negotiation_id = str(uuid.uuid4())
        
        # Use Claude 4's extended thinking for negotiation strategy
        strategy_prompt = f"""
        <thinking>
        I need to negotiate on behalf of my user who wants {request.get('restaurant', 'food')}.
        The potential match wanted {match.get('restaurant')}.
        
        Key considerations:
        1. My user's flexibility score: {request.get('flexibility_score', 0.5)}
        2. Match's compatibility score: {match.get('compatibility_score', 0.5)}
        3. Historical success between these users: {match.get('historical_success', 'unknown')}
        4. Current group size and remaining spots needed
        5. Time constraints and delivery logistics
        
        I should create a proposal that:
        - Maximizes likelihood of acceptance
        - Offers meaningful alternatives
        - Shows understanding of both users' preferences
        - Includes incentives if appropriate
        </thinking>
        
        You are negotiating on behalf of your user who wants {request.get('restaurant', 'food')}.
        The potential match wanted {match.get('restaurant')}.
        
        Create a compelling proposal that could work for both users.
        Be strategic and consider alternatives, timing flexibility, and incentives.
        
        User contexts:
        - Your user's flexibility: {request.get('flexibility_score', 0.5)}/1.0
        - Match compatibility: {match.get('compatibility_score', 0.5)}/1.0
        - Time constraints: {request.get('time_preference', 'flexible')}
        
        Return a negotiation proposal as JSON with:
        - primary_proposal
        - alternatives
        - incentives
        - reasoning
        """
        
        negotiation_response = anthropic_llm.invoke([HumanMessage(content=strategy_prompt)])
        
        try:
            proposal_data = json.loads(negotiation_response.content)
        except:
            # Fallback if JSON parsing fails
            proposal_data = {
                "primary_proposal": {
                    "restaurant": request.get('restaurant'),
                    "time": request.get('time_preference'),
                    "location": request.get('location')
                },
                "alternatives": [match.get('restaurant')],
                "incentives": ["Group discount", "Faster delivery"],
                "reasoning": "Attempting to find middle ground"
            }
        
        # FIX: Enhanced proposal with CLEAR restaurant field structure
        enhanced_proposal = {
            # Make sure restaurant is at the top level AND in primary_proposal
            'restaurant': request.get('restaurant'),  # ✅ Clear top-level restaurant
            'primary_restaurant': request.get('restaurant'),  # ✅ Alternative key
            'location': request.get('location'),
            'time': request.get('time_preference'),
            'requesting_user': state['user_phone'],
            'alternatives': proposal_data.get('alternatives', []),
            'incentives': proposal_data.get('incentives', []),
            'group_size_current': 2,  # Requesting user + this match
            'max_group_size': MAX_GROUP_SIZE,
            'negotiation_reasoning': proposal_data.get('reasoning', ''),
            'compatibility_score': match.get('compatibility_score', 0.5),
            # Also include the primary_proposal structure for compatibility
            'primary_proposal': {
                'restaurant': request.get('restaurant'),  # ✅ Restaurant in nested structure too
                'time': request.get('time_preference'),
                'location': request.get('location')
            }
        }
        
        print(f"🔍 DEBUG - Created proposal with restaurant: '{enhanced_proposal.get('restaurant')}'")
        
        # Send negotiation using enhanced tool
        result = negotiate_with_other_ai.invoke({
            "target_ai_user": match['user_phone'],
            "proposal": enhanced_proposal,
            "negotiation_id": negotiation_id,
            "strategy": "collaborative"  # Could be dynamic based on user history
        })
        
        negotiations.append({
            'negotiation_id': negotiation_id,
            'target_user': match['user_phone'],
            'proposal': enhanced_proposal,
            'status': 'pending',
            'success_probability': result.get('estimated_success_probability', 0.5)
        })
    
    state['active_negotiations'] = negotiations
    
    # Send immediate feedback to user
    if negotiations:
        feedback_message = f"""Great! I found {len(negotiations)} potential matches and I'm negotiating with their AI friends right now! 

I'm being strategic about this - looking for the best combination of restaurant, timing, and group chemistry. Give me about a minute to work out the details! 🤝"""
        
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
        # --- find the pending negotiation (unchanged) ------------------
        pending_negotiations = db.collection('negotiations')\
                               .where('to_user', '==', user_phone)\
                               .where('status', '==', 'pending')\
                               .limit(1).get()
        
        if len(pending_negotiations) > 0:
            negotiation_doc  = pending_negotiations[0]
            negotiation_data = negotiation_doc.to_dict()
            
            # ------------------------------------------------------------
            # 1. collect essentials BEFORE accepting
            requesting_user = negotiation_data['from_user']
            other_accepted  = db.collection('negotiations')\
                                 .where('from_user', '==', requesting_user)\
                                 .where('status', '==', 'accepted')\
                                 .get()
            proposed_group_size = len(other_accepted) + 2  # requester + this user + accepted others
            # 2. FULL-GROUP gate
            if proposed_group_size > MAX_GROUP_SIZE:
                send_friendly_message(
                    user_phone,
                    "Sorry, that group filled up just before you replied. "
                    "I'll look for another match right away! 🔄",
                    message_type="general"
                )
                # keep the negotiation from flipping to 'accepted'
                negotiation_doc.reference.update({'status': 'declined_full'})
                state['messages'].append(AIMessage(
                    content="Group response YES rejected (group full)"
                ))
                return state
            # ------------------------------------------------------------
            # 3. we still have room – now mark the negotiation accepted
            negotiation_doc.reference.update({'status': 'accepted'})
            # ------------------------------------------------------------
            # DEBUG…
            print(f"🔍 DEBUG - Full negotiation data: {json.dumps(negotiation_data, default=str, indent=2)}")
            
            proposal   = negotiation_data.get('proposal', {})
            restaurant = (
                proposal.get('restaurant')
                or proposal.get('primary_restaurant')
                or proposal.get('restaurant_name')
                or 'Unknown Restaurant'
            )
            print(f"🍽️ DEBUG - Extracted restaurant: '{restaurant}'")
            print(f"🍽️ DEBUG - Proposal keys: {list(proposal.keys())}")
            
            group_id   = negotiation_data['negotiation_id']
            group_size = proposed_group_size    # <— we already calculated it
            
            # START ORDER PROCESS FOR THIS USER
            print(f"🚀 DEBUG - Starting order process with restaurant: '{restaurant}'")
            start_order_process(user_phone, group_id, restaurant, group_size)
            
            # also kick off for requester if not started
            requesting_user_session = db.collection('order_sessions').document(requesting_user).get()
            if not requesting_user_session.exists:
                start_order_process(requesting_user, group_id, restaurant, group_size)
            
            print(f"✅ Group accepted and order process started: {negotiation_data['negotiation_id']}")
        
        else:
            send_friendly_message(
                user_phone,
                "I don't see any pending group invitations for you right now. Want to start a new food order?",
                message_type="general"
            )
            
    except Exception as e:
        print(f"Error handling group response YES: {e}")
        send_friendly_message(
            user_phone,
            "Something went wrong processing your response. Can you try again?",
            message_type="general"
        )
    
    state['messages'].append(AIMessage(content="Group response YES processed"))
    return state

def handle_group_response_no_node(state: PangeaState) -> PangeaState:
    """Handle NO response with intelligent follow-up and counter-proposals"""
    
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
            
            # FIND alternatives they might actually want
            alternatives = find_alternatives_for_user(user_phone, rejected_proposal)
            
            if alternatives and len(alternatives) > 0:
                # GET their preferences to make smart suggestions
                user_prefs = get_user_preferences.invoke({"phone_number": user_phone})
                
                # GENERATE counter-proposal using AI reasoning
                counter_result = generate_counter_proposal.invoke({
                    "rejected_proposal": rejected_proposal,
                    "declining_user_preferences": user_prefs,
                    "available_alternatives": alternatives
                })
                
                if counter_result.get('should_counter', False) and counter_result.get('counter_proposal'):
                    # Send intelligent alternative suggestion
                    best_alt = alternatives[0]
                    alt_message = f"""No worries about {rejected_proposal.get('restaurant', 'that')}! 
                    
I see you usually prefer {user_prefs.get('preferences', {}).get('favorite_cuisines', ['other food'])[0] if user_prefs.get('preferences', {}).get('favorite_cuisines') else 'other options'}. I found {len(alternatives)} people wanting {best_alt['restaurant']} nearby. 

Want me to see if they'd like you to join? 😊 (Just reply YES if interested)"""
                    
                    send_friendly_message(user_phone, alt_message, message_type="alternative_suggestion")
                    
                    # Store the alternative suggestion for potential follow-up
                    state['alternative_suggestions'] = alternatives
                    
                else:
                    # Standard acknowledgment
                    send_friendly_message(user_phone, "No worries! 👍 Maybe next time. I'll keep an eye out for other opportunities for you.", message_type="general")
            else:
                # No good alternatives found
                send_friendly_message(user_phone, "No worries! 👍 Maybe next time. I'll keep an eye out for other opportunities for you.", message_type="general")
            
            # Notify the original requesting user with enhanced feedback
            requesting_user = negotiation_data['from_user']
            restaurant = rejected_proposal.get('restaurant', 'food')
            
            # Enhanced notification to original requester
            update_message = f"The person I reached out to for {restaurant} can't join this time. I'm still looking for other matches and will update you soon! 🔍"
            send_friendly_message(requesting_user, update_message, message_type="general")
            
            print(f"✅ Group declined with enhanced follow-up: {negotiation_data['negotiation_id']}")
            
        else:
            # No pending negotiation found
            message = "I don't see any pending group invitations for you right now. Want to start a new food order?"
            send_friendly_message(user_phone, message, message_type="general")
            
    except Exception as e:
        print(f"Error handling enhanced group response NO: {e}")
        error_message = "Something went wrong processing your response. Can you try again?"
        send_friendly_message(user_phone, error_message, message_type="general")
    
    state['messages'].append(AIMessage(content="Enhanced group response NO processed"))
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
            
            result = negotiate_with_other_ai.invoke({
                "target_ai_user": best_alternative['user_phone'],
                "proposal": new_proposal,
                "negotiation_id": negotiation_id,
                "strategy": "collaborative"
            })
            
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
        
        # Calculate new group size (this would be more sophisticated in real implementation)
        # For now, assume group size is 3 (original + this user)
        new_group_size = 3
        
        # Start order process directly - skip negotiation since group is already forming
        print(f"🚀 User {user_phone} accepted proactive invitation for {restaurant}")
        
        # Import and call the order processor
        from pangea_order_processor import start_order_process
        
        start_order_process(user_phone, group_id, restaurant, new_group_size)
        
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
    
    # In a real system, this would check for actual responses
    # For now, just simulate waiting and then decide
    
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

#  should_continue_negotiating function  more robust version:
def should_continue_negotiating(state: PangeaState) -> str:
    """
    Enhanced decision-making using Claude 4's reasoning capabilities.
    
    Considers multiple factors beyond simple counting to make optimal decisions.
    """
    
    negotiations = state['active_negotiations']
    confirmed = [neg for neg in negotiations if neg['status'] == 'accepted']
    pending = [neg for neg in negotiations if neg['status'] == 'pending']
    rejected = [neg for neg in negotiations if neg['status'] == 'rejected']
    search_attempts = state.get('search_attempts', 0)
    
    print(f"🔍 Negotiations: {len(negotiations)} total, {len(confirmed)} confirmed, {len(pending)} pending, Search attempts: {search_attempts}")
    
    # PREVENT INFINITE LOOPS: Max 3 search attempts
    if search_attempts >= 3:
        print(f"🛑 Max search attempts reached ({search_attempts}), ending search")
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
    update_user_memory.invoke({
        "phone_number": state['user_phone'],
        "interaction_data": {
            'interaction_type': 'welcome',
            'restaurants_shown': RESTAURANTS,
            'onboarding_completed': True
        }
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
        }
    )
    
    # Morning workflow chain (Anthropic's Prompt Chaining pattern)
    workflow.add_edge("process_morning_response", "present_morning_matches")
    workflow.add_edge("present_morning_matches", END)
    workflow.add_edge("faq_answered", END)
    
    # Enhanced spontaneous agent flow with learning
    workflow.add_edge("analyze_spontaneous", "realtime_search")
    workflow.add_edge("realtime_search", "negotiate")
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

    # --- NEW: Enforce 3-person maximum ---------------------------------
    if group_size > MAX_GROUP_SIZE:
        # Trim or abort – this sample simply aborts gracefully
        send_friendly_message(
            state['user_phone'],
            "Oops - a Pangea group can't exceed 3 people. Let me regroup and try again! 🚦",
            message_type="general"
        )
        return state
    # -------------------------------------------------------------------

    # ✨ NEW: Find optimal time for the group
    requesting_user_time = state['current_request'].get('time_preference', 'now')
    optimal_time = find_optimal_group_time(state['potential_matches'], requesting_user_time)
    
    # Generate unique group ID
    group_id = str(uuid.uuid4())
    
    # PROACTIVE NOTIFICATION: Check if group has room for more people
    if len(all_members) < MAX_GROUP_SIZE:  # Group has room for more people
        print(f"🔔 Group has {len(all_members)} members, looking for more compatible users...")
        
        notify_result = notify_compatible_users_of_active_groups.invoke({
            "active_group_data": {
                "restaurant": restaurant,
                "location": state['current_request'].get('location'),
                "time": optimal_time,
                "current_members": all_members,
                "group_id": group_id
            }
        })
        
        print(f"🔔 Proactive notifications sent: {notify_result.get('notifications_sent', 0)}")
    
    # Start order process for all group members
    for member_phone in all_members:
        try:
            start_order_process(member_phone, group_id, restaurant, group_size)
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
    
    # Log successful group formation for learning (include optimal time)
    for member in all_members:
        update_user_memory.invoke({
            "phone_number": member,
            "interaction_data": {
                'interaction_type': 'successful_group_formation',
                'group_members': all_members,
                'restaurant': restaurant,
                'location': state['current_request'].get('location'),
                'group_size': group_size,
                'optimal_time': optimal_time,  # NEW: Log the optimal time
                'formation_time': datetime.now(),
                'group_id': group_id
            }
        })
    
    state['final_group'] = {
        'members': all_members,
        'restaurant': restaurant,
        'optimal_time': optimal_time,  # NEW: Include optimal time in state
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
    
    # Create a fake group_id for solo ordering
    solo_group_id = f"solo_{str(uuid.uuid4())}"
    
    # COMBINED MESSAGE: Match announcement + Order instructions
    combined_message = f"""Hey there! 👋 Just found someone in your area who's also ordering - perfect timing to split delivery costs!

Since you're splitting with someone, your delivery fee is only $2.50-$3.50. Not bad, right?

🎉 You're now in the {restaurant} group!

To order from {restaurant}, please follow these steps:

1. First, place your order directly with {restaurant}'s app/website/phone and select PICKUP option (not delivery)
2. Once you've placed your order, come back and let me know your order confirmation number

Have you already placed your order with {restaurant}? If so, do you have your order confirmation number? If you don't have an order number or if {restaurant} doesn't provide one, just let me know your name instead.

Your payment share will be $3.50 once everyone has their orders ready! 💳"""
    
    # Send the COMBINED message
    send_friendly_message(user_phone, combined_message, message_type="general")
    
    # NOW trigger the order processor flow (but DON'T send another welcome message)
    try:
        # Create order session manually without sending another message
        session_data = {
            'user_phone': user_phone,
            'group_id': solo_group_id,
            'restaurant': restaurant,
            'group_size': 1,
            'order_stage': 'need_order_number',
            'pickup_location': RESTAURANTS.get(restaurant, {}).get('location', 'Campus'),
            'payment_link': 'https://buy.stripe.com/test_placeholder',  # Will be set properly later
            'order_session_id': str(uuid.uuid4()),
            'created_at': datetime.now(),
            'order_number': None,
            'customer_name': None
        }
        
        from pangea_order_processor import update_order_session
        update_order_session.invoke({"phone_number": user_phone, "session_data": session_data})
        print(f"✅ Started solo order process for {user_phone} - {restaurant}")
        
        # Log this as a solo order attempt
        update_user_memory.invoke({
            "phone_number": user_phone,
            "interaction_data": {
                "interaction_type": "fake_match_solo_order",
                "restaurant": restaurant,
                "location": location,
                "timestamp": datetime.now(),
            },
        })
        
    except Exception as e:
        print(f"❌ Failed to start solo order process: {e}")

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
    Public-facing language: “delivery is usually $2.50 - $4.50 per person.”
    """
    prompt = f"""
You are **Pangea**, a friendly AI lunch-coordination assistant for college students.

The user asked: \"{user_message}\"

Answer clearly and concisely.  If the user asks:
• **“How does this work?”** → Give the 5-step flow:

1. **Text me your plan**  
   e.g. “Chipotle around 12:30 - 1 p.m.”

2. **I find matches**  
   I'll look for up to {MAX_GROUP_SIZE - 1} other students who want the same place + time.

3. **You confirm group & price**  
   I reply with the group and delivery fee (usually **$2.50 - $4.50** per person).  
   Reply “YES” to lock it in.

4. **Pay the link**  
   Your secure Stripe link arrives—pay to activate the order.

5. **Meet at your drop-off spot**  
   I send updates and the pickup pin (Daley Library, SCE, etc.).

• **“What restaurants can I pick from?”** → list them.  
• **“Where can I meet the delivery?”** → list drop-off locations.  
• **“How much does delivery cost?”** → “Delivery is usually $2.50 - $4.50 per person, depending on group size.”  
• Any other pricing, timing, or general FAQ → answer in ≤ 5 lines.

When useful, remind the user:  
“Just text me your food + location—I'll handle matching!”

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
    negotiation_prompt = f"""
    You are negotiating a group food order. Craft a compelling invitation message.
    
    Situation: {json.dumps(proposal, default=str)}
    Target user: {target_user}
    
    Create a friendly SMS that:
    1. Explains the group order opportunity
    2. Highlights mutual benefits  
    3. Addresses any timing/preference differences diplomatically
    4. Asks for YES/NO response
    
    Keep it conversational and under 160 characters.
    
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
    """Handle incoming SMS from Twilio"""
    try:
        from_number = request.form.get('From')
        message_body = request.form.get('Body')
        
        print(f"📱 SMS from {from_number}: {message_body}")
        
        # First check if this is an order-related message
        order_result = process_order_message(from_number, message_body)
        
        if order_result is not None:
            # Message was processed by order system
            print(f"✅ Order processed: {order_result.get('order_stage', 'unknown')}")
            return '', 200
        
        # Not an order message - process through main Pangea system
        result = handle_incoming_sms(from_number, message_body)
        print(f"✅ Main system processed: {result.get('conversation_stage', 'unknown')}")
        
        return '', 200
    except Exception as e:
        print(f"❌ Error: {e}")
        return '', 500

@app.route('/health', methods=['GET'])
def health_check():
    return {'status': 'healthy', 'service': 'Pangea AI Friend'}, 200

if __name__ == "__main__":
    print("🍜 Starting Pangea AI Friend System...")
    print("Ready to receive SMS messages!")
    app.run(host='0.0.0.0', port=8000, debug=True)
