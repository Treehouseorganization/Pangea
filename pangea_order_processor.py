"""
Pangea Order Processing System
Handles the order flow after users join a group
Integrated with main Pangea system for seamless group ordering
"""

import os
import json
from datetime import datetime, timedelta
from typing import Dict, List, Optional, TypedDict, Annotated
from dataclasses import dataclass
from dotenv import load_dotenv
import uuid
import random
import threading
import time
from pangea_locations import RESTAURANTS

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

MAX_GROUP_SIZE = 2 

load_dotenv()

# Initialize services (if not already initialized)
try:
    # Use existing Twilio client from main file
    from pangea_main import twilio_client, anthropic_llm, db, send_friendly_message
except ImportError:
    # Fallback initialization if running standalone
    twilio_client = Client(os.getenv('TWILIO_ACCOUNT_SID'), os.getenv('TWILIO_AUTH_TOKEN'))
    anthropic_llm = ChatAnthropic(
        model="claude-opus-4-20250514",
        api_key=os.getenv('ANTHROPIC_API_KEY'),
        temperature=0.1,
        max_tokens=4096
    )
    if not firebase_admin._apps:
        firebase_json = os.getenv('FIREBASE_SERVICE_ACCOUNT_JSON')
        if firebase_json:
            try:
                firebase_config = json.loads(firebase_json)
                cred = credentials.Certificate(firebase_config)
                firebase_admin.initialize_app(cred)
                print("✅ Firebase initialized successfully in order processor")
            except json.JSONDecodeError as e:
                print(f"❌ Invalid Firebase JSON format in order processor: {e}")
                raise
            except Exception as e:
                print(f"❌ Firebase initialization failed in order processor: {e}")
                raise
        else:
            print("❌ FIREBASE_SERVICE_ACCOUNT_JSON environment variable not set in order processor")
            raise ValueError("Firebase credentials not configured in order processor")
    
    # Initialize Firestore client
    db = firestore.client()

# Payment Link Logic
PAYMENT_LINKS = {
    1: [os.getenv("STRIPE_LINK_250"),
        os.getenv("STRIPE_LINK_350")],   # solo “discount” links
    2: [os.getenv("STRIPE_LINK_450")],    # $4.50
    3: [os.getenv("STRIPE_LINK_350")],    # $3.50
}

# Order State Management
class OrderState(TypedDict):
    messages: Annotated[List, add_messages]
    user_phone: str
    group_id: str
    restaurant: str
    order_stage: str  # "need_order_number", "need_order_description", "ready_to_pay", "payment_initiated"
    pickup_location: str
    group_size: int
    payment_link: str
    order_session_id: str
    order_number: Optional[str]
    customer_name: Optional[str]
    order_description: Optional[str]

def get_payment_link(size: int) -> str:
    """Return a Stripe URL for the given group size (1-2)."""
    if size not in PAYMENT_LINKS or size > 2:
        raise ValueError("Group size exceeds 2.")
    return random.choice(PAYMENT_LINKS[size])

def get_payment_amount(size: int) -> str:
    """Human-readable share text."""
    if size == 2:
        return "$4.50"
    else:   # size == 1 (our 'fake match')
        return random.choice(["$2.50", "$3.50"])


def get_user_order_session(phone_number: str) -> Dict:
    """Get user's current order session"""
    try:
        session_doc = db.collection('order_sessions').document(phone_number).get()
        if session_doc.exists:
            return session_doc.to_dict()
        return {}
    except Exception as e:
        print(f"Error getting order session: {e}")
        return {}


def update_order_session(phone_number: str, session_data: Dict) -> bool:
    """Update user's order session"""
    try:
        session_data['last_updated'] = datetime.now()
        db.collection('order_sessions').document(phone_number).set(session_data, merge=True)
        return True
    except Exception as e:
        print(f"Error updating order session: {e}")
        return False

def start_order_process(user_phone: str, group_id: str, restaurant: str, group_size: int, delivery_time: str = 'now'):
    """Called from main system when user joins a group - starts the order process"""
    
    # Create order session
    session_data = {
        'user_phone': user_phone,
        'group_id': group_id,
        'restaurant': restaurant,
        'group_size': group_size,
        'delivery_time': delivery_time,
        'order_stage': 'need_order_number',
        'pickup_location': RESTAURANTS.get(restaurant, {}).get('address', 'Campus'),
        'delivery_location': 'Richard J Daley Library',  # FIX: Add delivery location
        'payment_link': get_payment_link(group_size),
        'order_session_id': str(uuid.uuid4()),
        'created_at': datetime.now(),
        'order_number': None,
        'customer_name': None,
        # Flag fixes for scheduled deliveries
        'solo_order': delivery_time != 'now' and group_size == 1,
        'is_scheduled': delivery_time != 'now',
        'awaiting_match': delivery_time != 'now' and group_size == 1
    }
    
    # Set protection flags for scheduled deliveries
    is_scheduled_delivery = delivery_time not in ['now', 'ASAP', 'soon', 'immediately']
    if is_scheduled_delivery:
        session_data['is_scheduled'] = True
        session_data['awaiting_match'] = True
        if group_size == 1:
            session_data['solo_order'] = True
    
    update_order_session(user_phone, session_data)
    
    payment_amount = get_payment_amount(group_size)
    
    # Send order instructions - REMOVED: duplicate message already sent in solo order trigger
    
    return session_data

def format_menu_items(restaurant: str) -> str:
    """Format menu items for display (not needed for simplified flow)"""
    return ""

# Order Processing Nodes
def classify_order_intent_node(state: OrderState) -> OrderState:
    """Classify user's message during order process"""
    
    last_message = state['messages'][-1].content.lower().strip()
    user_phone = state['user_phone']
    
    # Get current order session
    session = get_user_order_session(user_phone)
    
    if not session:
        state['order_stage'] = "no_session"
        return state
    
    current_stage = session.get('order_stage', 'need_order_number')
    
    # Check for payment trigger (only if they have order number)
    if 'pay' in last_message and len(last_message) <= 10:
        if current_stage == 'ready_to_pay':
            state['order_stage'] = "payment_request"
        else:
            state['order_stage'] = "need_order_first"
        return state
    
    # Handle based on current stage
    if current_stage == 'need_order_number':
        state['order_stage'] = "collect_order_number"
        return state
    elif current_stage == 'need_order_description':
        state['order_stage'] = "collect_order_description"
        return state
    elif current_stage == 'ready_to_pay':
        state['order_stage'] = "redirect_to_payment"
        return state
    else:
        state['order_stage'] = "redirect_to_payment"
        return state

def collect_order_number_node(state: OrderState) -> OrderState:
    """Collect order confirmation number or customer name"""
    
    user_phone = state['user_phone']
    user_message = state['messages'][-1].content.strip()
    session = get_user_order_session(user_phone)
    
    # Use Claude to extract order number, name, and what they ordered
    extraction_prompt = f"""
    The user is providing their order confirmation number, name for pickup, and what they ordered.
    
    User message: "{user_message}"
    
    Extract:
    1. Order confirmation number/ID (if available)
    2. Customer name (if available)
    3. What they ordered (food items)
    
    Return JSON with:
    - "order_number": confirmation number or null
    - "customer_name": name or null
    - "order_description": what they ordered or null
    
    Examples:
    - "My order number is ABC123, I got a Big Mac meal" → {{"order_number": "ABC123", "customer_name": null, "order_description": "Big Mac meal"}}
    - "Order #4567, name is John, I ordered chicken nuggets" → {{"order_number": "4567", "customer_name": "John", "order_description": "chicken nuggets"}}
    - "Just use my name Maria, I got a quarter pounder" → {{"order_number": null, "customer_name": "Maria", "order_description": "quarter pounder"}}
    
    IMPORTANT: Return ONLY valid JSON, no other text.
    """
    
    try:
        response = anthropic_llm.invoke([HumanMessage(content=extraction_prompt)])
        response_text = response.content.strip()
        
        # Clean up response - remove any markdown formatting or extra text
        if '```json' in response_text:
            # Extract JSON from markdown code block
            start = response_text.find('{')
            end = response_text.rfind('}') + 1
            response_text = response_text[start:end]
        elif '```' in response_text:
            # Remove any code block markers
            response_text = response_text.replace('```', '').strip()
        
        # Try to find JSON in the response
        if not response_text.startswith('{'):
            # Look for JSON in the response
            import re
            json_match = re.search(r'\{.*\}', response_text, re.DOTALL)
            if json_match:
                response_text = json_match.group()
        
        print(f"🔍 Trying to parse: '{response_text}'")
        extracted_data = json.loads(response_text)
        
        # Store extracted information
        order_number = extracted_data.get("order_number")
        customer_name = extracted_data.get("customer_name")
        order_description = extracted_data.get("order_description")
        
        if order_number:
            session['order_number'] = order_number
            identifier_for_message = f"order number: {order_number}"
        elif customer_name:
            session['customer_name'] = customer_name
            name = customer_name
            identifier_for_message = f"name: {name}"
        
        # Store order description if provided
        if order_description:
            session['order_description'] = order_description
            
        # Enhanced validation - check what's missing
        missing_items = []
        if not (order_number or customer_name):
            missing_items.append("identifier")
        if not order_description or order_description.lower().strip() in ['', 'food', 'meal', 'order']:
            missing_items.append("description")
            
        if missing_items:
            # Store what we have so far
            session['missing_order_info'] = missing_items
            if 'identifier' not in missing_items:
                # We have identifier but missing description
                session['order_stage'] = 'need_order_description'
            else:
                # Missing identifier (and possibly description)
                session['order_stage'] = 'need_order_number'
                
            update_order_session(user_phone, session)
            
            # Craft message based on what's missing
            if set(missing_items) == {'identifier', 'description'}:
                message = f"""I need both your order details and what you ordered.

Please provide:
• Your order confirmation number (like "ABC123" or "#4567") OR your name
• What you ordered (like "Big Mac meal, large fries, Coke")

This helps me coordinate pickup with {session.get('restaurant', 'the restaurant')}!"""
            elif 'identifier' in missing_items:
                message = f"""I couldn't find an order number or name in that message.

Please provide:
• Your order confirmation number (like "ABC123" or "#4567")
• Your name if there's no order number (like "John Smith")

This helps me coordinate pickup with {session.get('restaurant', 'the restaurant')}!"""
            else:  # missing only description
                message = f"""I have your order info but need to know what you ordered.

Please tell me what food items you ordered (like "Big Mac meal, large fries, Coke").

This helps me coordinate pickup with {session.get('restaurant', 'the restaurant')}!"""
            
            send_friendly_message(user_phone, message, message_type="order_update")
            state['messages'].append(AIMessage(content=message))
            return state
        
        # All required info provided
        session['order_stage'] = 'ready_to_pay'
        if 'missing_order_info' in session:
            del session['missing_order_info']
        
        # Successfully got order info
        update_order_session(user_phone, session)
        
        payment_amount = get_payment_amount(session.get('group_size', 2))
        
        # ✅ FIXED: Use identifier_for_message which is always defined
        message = f"""Perfect! I've got your {identifier_for_message} for {session.get('restaurant')}! ✅

Your payment share: {payment_amount}
Pickup location: {session.get('pickup_location')}

When you're ready to pay, just text:
**PAY**

I'll send you the payment link! 💳"""
        
        send_friendly_message(user_phone, message, message_type="order_update")
        check_group_completion_and_trigger_delivery(user_phone)  # Trigger delivery check
        state['messages'].append(AIMessage(content=message))
        return state  # Exit after successful processing
        
        
    except (json.JSONDecodeError, ValueError) as e:
        print(f"JSON parsing error: {e}")
        # Fallback: Simple name extraction
        user_message_lower = user_message.lower()
        
        # Check if it looks like a name
        if any(word in user_message_lower for word in ['name is', 'i am', 'im ', 'call me']):
            # Try to extract name using simple logic
            if 'name is' in user_message_lower:
                name = user_message.split('name is', 1)[1].strip()
            elif 'i am' in user_message_lower:
                name = user_message.split('i am', 1)[1].strip()
            elif 'im ' in user_message_lower:
                name = user_message.split('im ', 1)[1].strip()
            elif 'call me' in user_message_lower:
                name = user_message.split('call me', 1)[1].strip()
            else:
                name = user_message.strip()
            
            # Clean up the name
            name = name.replace('.', '').replace(',', '').strip()
            
            if name and len(name) < 50:  # Reasonable name length
                session['customer_name'] = name
                session['order_stage'] = 'ready_to_pay'
                update_order_session(user_phone, session)
                payment_amount = get_payment_amount(session.get('group_size', 2))
                
                message = f"""Perfect! I've got your name: {name} for {session.get('restaurant')}! ✅

Your payment share: {payment_amount}
Pickup location: {session.get('pickup_location')}

When you're ready to pay, just text:
**PAY**

I'll send you the payment link! 💳"""
            else:
                message = f"""I couldn't understand that. Please provide either:
• Your order confirmation number (like "Order #123")
• Your name for pickup (like "My name is John")

Try again!"""
        else:
            message = f"""I couldn't understand that. Please provide either:
• Your order confirmation number (like "Order #123") 
• Your name for pickup (like "My name is John")

This helps me coordinate pickup with {session.get('restaurant', 'the restaurant')}!"""
    
    except Exception as e:
        print(f"Error extracting order info: {e}")
        message = f"""I couldn't understand that. Please provide either:
• Your order confirmation number
• Your name for pickup

Try something like "Order #123" or "My name is John"."""
    
    send_friendly_message(user_phone, message, message_type="order_update")
    check_group_completion_and_trigger_delivery(user_phone)
    state['messages'].append(AIMessage(content=message))
    return state

def collect_order_description_node(state: OrderState) -> OrderState:
    """Collect missing order description when we already have identifier"""
    
    user_phone = state['user_phone']
    user_message = state['messages'][-1].content
    session = get_user_order_session(user_phone)
    
    print(f"🍕 Collecting missing order description from {user_phone}")
    print(f"📝 User message: {user_message}")
    
    try:
        # Use Claude to extract order description
        llm = ChatAnthropic(model="claude-3-5-sonnet-20241022", temperature=0.1)
        
        extraction_prompt = f"""
        The user already provided their order identifier but we need their food order description.
        
        User message: "{user_message}"
        Restaurant: {session.get('restaurant', 'unknown')}
        
        Extract the food order description from their message. Look for:
        - Specific food items (Big Mac, Chipotle bowl, etc.)
        - Meal details (large fries, no pickles, etc.)
        - Drinks or sides
        
        Return JSON with:
        {{
            "order_description": "detailed description of what they ordered",
            "confidence": "high/medium/low"
        }}
        
        If you can't find clear food items, return empty order_description.
        """
        
        response = llm.invoke(extraction_prompt)
        import json
        import re
        
        # Extract JSON from the response (handle cases where Claude adds extra text)
        response_text = response.content.strip()
        print(f"🤖 Raw Claude response: {response_text}")
        
        # Try to find JSON in the response
        json_match = re.search(r'\{.*\}', response_text, re.DOTALL)
        if json_match:
            json_text = json_match.group()
            result = json.loads(json_text)
        else:
            # Fallback: treat the whole response as the order description
            result = {
                "order_description": response_text,
                "confidence": "medium"
            }
        
        order_description = result.get('order_description', '').strip()
        confidence = result.get('confidence', 'low')
        
        print(f"🤖 Extracted description: '{order_description}' (confidence: {confidence})")
        
        # Validate the extracted description
        if order_description and confidence in ['high', 'medium'] and len(order_description) > 5:
            # Valid description found
            session['order_description'] = order_description
            session['order_stage'] = 'ready_to_pay'
            if 'missing_order_info' in session:
                del session['missing_order_info']
                
            update_order_session(user_phone, session)
            
            payment_amount = get_payment_amount(session.get('group_size', 2))
            identifier = session.get('order_number') or session.get('customer_name', 'your order')
            
            message = f"""Perfect! I've got your order details for {session.get('restaurant')}! ✅

Order: {order_description}
Identifier: {identifier}

Your payment share: {payment_amount}

Reply "pay" when ready to complete your order! 💳"""
            
            send_friendly_message(user_phone, message, message_type="order_update")
            state['messages'].append(AIMessage(content=message))
            
            # Check if group is complete
            check_group_completion_and_trigger_delivery(user_phone)
            
        else:
            # Still need clearer description
            message = f"""I need more details about what you ordered.

Please tell me specifically what food items you got from {session.get('restaurant', 'the restaurant')}:
• Main item (like "Big Mac meal" or "Chicken bowl")
• Size/modifications (like "large fries" or "no onions")
• Drinks or sides

Example: "Big Mac meal with large fries and a Coke" """
            
            send_friendly_message(user_phone, message, message_type="order_update")
            state['messages'].append(AIMessage(content=message))
            
    except Exception as e:
        print(f"❌ Error extracting order description: {e}")
        
        # Fallback message
        message = f"""I need to know what you ordered from {session.get('restaurant', 'the restaurant')}.

Please tell me what food items you got:
• Main item (like "Big Mac meal" or "Chicken bowl") 
• Size/modifications (like "large" or "no pickles")
• Drinks or sides

This helps me coordinate pickup!"""
        
        send_friendly_message(user_phone, message, message_type="order_update")
        state['messages'].append(AIMessage(content=message))
    
    return state

def handle_need_order_first_node(state: OrderState) -> OrderState:
    """Handle when user tries to pay without providing order number"""
    
    user_phone = state['user_phone']
    session = get_user_order_session(user_phone)
    restaurant = session.get('restaurant', 'the restaurant')
    
    message = f"""Hold on! I need your order information first before you can pay.

Please provide either:
• Your order confirmation number from {restaurant}
• Your name if there's no order number

Once I have that, you can text PAY! 📝"""
    
    send_friendly_message(user_phone, message, message_type="order_needed")
    state['messages'].append(AIMessage(content=message))
    return state

def handle_redirect_to_payment_node(state: OrderState) -> OrderState:
    """Redirect user back to payment flow"""
    
    user_phone = state['user_phone']
    session = get_user_order_session(user_phone)
    
    payment_amount = get_payment_amount(session.get('group_size', 2))
    restaurant = session.get('restaurant', 'your group')
    
    # Check if they have order info
    order_number = session.get('order_number')
    customer_name = session.get('customer_name')
    
    if order_number:
        identifier = f"order #{order_number}"
    elif customer_name:
        identifier = f"name: {customer_name}"
    else:
        identifier = "order info"
    
    message = f"""You're all set in the {restaurant} group with {identifier}! 

Your share is {payment_amount}. When you're ready to pay, just text:
**PAY**

I'll send you the payment link! 💳"""
    
    send_friendly_message(user_phone, message, message_type="payment_reminder")
    state['messages'].append(AIMessage(content=message))
    return state

def handle_order_confirmation_node(state: OrderState) -> OrderState:
    """Handle order confirmation"""
    
    user_phone = state['user_phone']
    session = get_user_order_session(user_phone)
    
    # Order is confirmed, move to payment stage
    session['order_stage'] = 'ready_to_pay'
    update_order_session(user_phone, session)
    
    payment_amount = get_payment_amount(session.get('group_size', 2))
    
    message = f"""Perfect! Your order is confirmed! ✅

Once everyone in your group has placed their orders, I'll coordinate the group payment.

When you're ready to pay your share of {payment_amount}, just text me:
**PAY**

I'll send you the payment link and keep track of the group order status! 💳"""
    
    send_friendly_message(user_phone, message, message_type="order_confirmed")
    state['messages'].append(AIMessage(content=message))
    return state

def handle_payment_request_node(state: OrderState) -> OrderState:
    """Handle payment request when user texts PAY"""
    
    user_phone = state['user_phone']
    session = get_user_order_session(user_phone)
    
    if not session:
        message = "You don't have an active group to pay for. Please join a group first!"
        send_friendly_message(user_phone, message, message_type="error")
        return state
    
    group_size = session.get('group_size', 2)
    payment_link = get_payment_link(group_size)
    payment_amount = get_payment_amount(group_size)
    restaurant = session.get('restaurant', 'your group')
    
    # Mark as payment initiated
    session['order_stage'] = 'payment_initiated'
    session['payment_requested_at'] = datetime.now()
    update_order_session(user_phone, session)
    
    message = f"""💳 Payment for {restaurant}

Your share: {payment_amount}

Click here to pay:
{payment_link}

After payment, I'll coordinate with your group to place the order! 🍕"""
    
    send_friendly_message(user_phone, message, message_type="payment")
    
    # Check if all group members have now paid and trigger delivery if so
    check_group_completion_and_trigger_delivery(user_phone)
    
    state['messages'].append(AIMessage(content=message))
    return state

def handle_clarification_node(state: OrderState) -> OrderState:
    """Handle cases where user input needs clarification"""
    
    user_phone = state['user_phone']
    session = get_user_order_session(user_phone)
    restaurant = session.get('restaurant', '')
    
    message = f"""I want to make sure I get your order right! 

Please confirm:
• YES - My order is correct
• NO - I want to make changes

Or you can:
• Add specific items: "Add Margherita Pizza"
• Start over: "Clear my order"

Current menu for {restaurant}:
{format_menu_items(restaurant)}"""
    
    send_friendly_message(user_phone, message, message_type="clarification")
    state['messages'].append(AIMessage(content=message))
    return state

def handle_no_session_node(state: OrderState) -> OrderState:
    """Handle when user doesn't have an active order session"""
    
    user_phone = state['user_phone']
    
    message = """You don't have an active group order session. 

To start ordering, you need to join a food group first! Try texting something like:
"I want Mario's Pizza at Campus Center" 

I'll help you find other people to order with! 🍕"""
    
    send_friendly_message(user_phone, message, message_type="no_session")
    state['messages'].append(AIMessage(content=message))
    return state

def route_order_flow(state: OrderState) -> str:
    """Route to appropriate order processing node"""
    return state['order_stage']

# Create Order Processing Graph
def create_order_graph():
    """Create the order processing workflow graph"""
    
    workflow = StateGraph(OrderState)
    
    # Add nodes
    workflow.add_node("classify_order_intent", classify_order_intent_node)
    workflow.add_node("collect_order_number", collect_order_number_node)
    workflow.add_node("collect_order_description", collect_order_description_node)
    workflow.add_node("handle_payment_request", handle_payment_request_node)
    workflow.add_node("handle_redirect_to_payment", handle_redirect_to_payment_node)
    workflow.add_node("handle_need_order_first", handle_need_order_first_node)
    workflow.add_node("handle_no_session", handle_no_session_node)
    
    # Add conditional routing
    workflow.add_conditional_edges(
        "classify_order_intent",
        route_order_flow,
        {
            "collect_order_number": "collect_order_number",
            "collect_order_description": "collect_order_description",
            "payment_request": "handle_payment_request",
            "redirect_to_payment": "handle_redirect_to_payment",
            "need_order_first": "handle_need_order_first",
            "no_session": "handle_no_session"
        }
    )
    
    # All nodes end the workflow
    workflow.add_edge("collect_order_number", END)
    workflow.add_edge("collect_order_description", END)
    workflow.add_edge("handle_payment_request", END)
    workflow.add_edge("handle_redirect_to_payment", END)
    workflow.add_edge("handle_need_order_first", END)
    workflow.add_edge("handle_no_session", END)
    
    workflow.set_entry_point("classify_order_intent")
    
    return workflow.compile()

# ADD these new functions to pangea_order_processor.py (around line 50, before start_order_process)

def is_new_food_request(message: str, phone_number: str = None) -> bool:
   """Use Claude Opus 4 to intelligently detect if message is food-related vs general question"""
   
   from langchain_anthropic import ChatAnthropic
   from langchain_core.messages import HumanMessage
   import os
   
   # CRITICAL FIX: Handle YES/NO responses to group invitations
   message_lower = message.lower().strip()
   group_response_keywords = ['yes', 'y', 'no', 'n', 'sure', 'ok', 'pass', 'nah']
   
   # If it's a simple group response, let main system handle it (NOT order processor)
   if message_lower in group_response_keywords:
       print(f"🎯 Detected group response: '{message}' - routing to main system")
       return True  # Route to main system to handle group responses
   
   # CONTEXT FIX: Check if user has active order session
   active_session = None
   if phone_number:
       active_session = get_user_order_session(phone_number)
   
   # If there's an active order session, food mentions are likely order continuation
   if active_session:
       print(f"🔄 Active order session exists - treating food mentions as order continuation")
       return False
   
   # Use same Claude Opus 4 model as main system
   anthropic_llm = ChatAnthropic(
       model="claude-opus-4-20250514",
       api_key=os.getenv('ANTHROPIC_API_KEY'),
   )
   
   classification_prompt = f"""
   Classify this message into one of these categories:

   Message: "{message}"

   Categories:
   - general_question: Non-food related questions, greetings, general conversation, help requests
   - new_food_request: User wants to order food, mentions restaurants, craving food
   - order_continuation: User providing details for existing order (name, payment, order number, contact info, "my name is", "call me")

   Return only the category name.
   """
   
   try:
       response = anthropic_llm.invoke([HumanMessage(content=classification_prompt)])
       classification = response.content.strip().lower()
       
       # If it's a general question, don't treat as new food request - let it be handled by FAQ system
       if classification == "general_question":
           return False
       elif classification == "new_food_request":
           return True
       else:  # order_continuation
           return False
           
   except Exception as e:
       print(f"Error in message classification: {e}")
       # Fallback to simple keyword detection
       message_lower = message.lower().strip()
       order_keywords = ['my order number', 'order #', 'pay', 'payment', 'my name is']
       return not any(keyword in message_lower for keyword in order_keywords)

def clear_old_order_session(phone_number: str):
    """Clear user's old order session"""
    try:
        db.collection('order_sessions').document(phone_number).delete()
        print(f"🗑️ Cleared old order session for {phone_number}")
    except Exception as e:
        print(f"❌ Failed to clear order session: {e}")


def schedule_delayed_delivery_notifications(group_data: Dict, delivery_result: Dict):
    """
    Schedule 50-second delayed delivery notifications for each user individually
    """
    def send_delayed_notification(user_phone: str, delivery_info: Dict):
        # Wait 50 seconds
        time.sleep(50)
        
        restaurant = group_data.get('restaurant', 'your restaurant')
        
        # FIX: Get the actual dropoff location name and address
        dropoff_location_name = group_data.get('location', 'campus')
        
        # Get the actual dropoff address from the DROPOFFS dictionary
        try:
            from pangea_locations import DROPOFFS
            dropoff_address = DROPOFFS.get(dropoff_location_name, {}).get('address', dropoff_location_name)
        except ImportError:
            # Fallback if import fails
            dropoff_address = dropoff_location_name
        
        tracking_url = delivery_info.get('tracking_url', '')
        delivery_id = delivery_info.get('delivery_id', 'N/A')
        
        message = f"""🚚 Your {restaurant} delivery is on the way!

📍 Delivery to: {dropoff_address}
📱 Track your order: {tracking_url}
📦 Delivery ID: {delivery_id}

Your driver will contact you when they arrive! 🎉"""
        
        try:
            send_friendly_message(user_phone, message, message_type="delivery_notification")
            print(f"✅ Sent delayed delivery notification to {user_phone}")
        except Exception as e:
            print(f"❌ Failed to send delayed notification to {user_phone}: {e}")
    
    # Start individual delayed notification threads for each user
    for user_phone in group_data.get('members', []):
        thread = threading.Thread(
            target=send_delayed_notification,
            args=(user_phone, delivery_result)
        )
        thread.daemon = True  # Don't block program exit
        thread.start()
        print(f"⏰ Scheduled 50s delayed notification for {user_phone}")



def schedule_delayed_triggered_notifications(group_data: Dict, delivery_result: Dict):
    """
    Schedule 50-second delayed DELIVERY TRIGGERED notifications for scheduled deliveries
    """
    def send_delayed_triggered_notification(user_phone: str, group_info: Dict, delivery_info: Dict):
        # Wait 50 seconds
        time.sleep(50)
        
        restaurant = group_info.get('restaurant')
        
        # FIX: Get the actual dropoff location name and address
        dropoff_location_name = group_info.get('delivery_location') or group_info.get('location')
        
        # Get the actual dropoff address from the DROPOFFS dictionary
        try:
            from pangea_locations import DROPOFFS
            dropoff_address = DROPOFFS.get(dropoff_location_name, {}).get('address', dropoff_location_name)
        except ImportError:
            # Fallback if import fails
            dropoff_address = dropoff_location_name
        
        # Get restaurant pickup address
        try:
            from pangea_locations import RESTAURANTS
            pickup_address = restaurant  # FIX: Just use restaurant name instead of full address
        except ImportError:
            # Fallback if import fails
            pickup_address = restaurant
        
        tracking_url = delivery_info.get('tracking_url', '')
        delivery_id = delivery_info.get('delivery_id', '')
        
        message = f"""🚚 DELIVERY TRIGGERED! 🎉

Your {restaurant} group order is now being processed!

📍 Pickup: {pickup_address}
📍 Dropoff: {dropoff_address}
🆔 Delivery ID: {delivery_id[:8]}...

The driver will pick up all individual orders and deliver them to {dropoff_address}. 

📱 Track delivery: {tracking_url}

I'll keep you updated as the driver picks up and delivers your orders! 🍕"""
        
        try:
            send_friendly_message(user_phone, message, message_type="delivery_triggered")
            print(f"✅ Sent delayed triggered notification to {user_phone}")
        except Exception as e:
            print(f"❌ Failed to send delayed triggered notification to {user_phone}: {e}")
    
    # Start background thread for each user
    for user_phone in group_data.get('members', []):
        thread = threading.Thread(
            target=send_delayed_triggered_notification,
            args=(user_phone, group_data, delivery_result)
        )
        thread.daemon = True  # Don't block program exit
        thread.start()
        print(f"⏰ Scheduled 50s delayed triggered notification for {user_phone}")


def schedule_immediate_delayed_delivery(group_data: Dict, delay_seconds: int):
    """
    Schedule delivery to trigger after a short delay (for silently matched groups)
    """
    def trigger_delivery_after_delay():
        print(f"⏰ Waiting {delay_seconds} seconds before triggering silently matched delivery...")
        time.sleep(delay_seconds)
        
        # Trigger the delivery immediately after delay
        print(f"🚚 Triggering silently matched delivery after {delay_seconds}s delay")
        try:
            from pangea_uber_direct import create_group_delivery
            delivery_result = create_group_delivery(group_data)
            
            if delivery_result.get('success'):
                print(f"✅ Silently matched delivery created: {delivery_result.get('delivery_id')}")
                
                # Update sessions for ALL group members to mark delivery as triggered
                group_id = group_data.get('group_id')
                all_members = group_data.get('members', [])
                
                for member_phone in all_members:
                    session = get_user_order_session(member_phone)
                    if session:
                        session['delivery_triggered'] = True
                        session['delivery_id'] = delivery_result.get('delivery_id')
                        session['tracking_url'] = delivery_result.get('tracking_url')
                        session['delivery_scheduled'] = False  # Clear scheduled flag
                        update_order_session(member_phone, session)
                        print(f"✅ Updated session for silently matched member {member_phone}")
                
                # Send notifications
                schedule_delayed_triggered_notifications(group_data, delivery_result)
                
            else:
                print(f"❌ Silently matched delivery creation failed: {delivery_result}")
                
        except Exception as e:
            print(f"❌ Silently matched delivery trigger error: {e}")
    
    # Start background thread to wait and trigger delivery
    thread = threading.Thread(target=trigger_delivery_after_delay)
    thread.daemon = True
    thread.start()
    print(f"🤝 Silently matched delivery trigger scheduled for {delay_seconds} seconds from now")


def schedule_solo_delivery_trigger(group_data: Dict):
    """
    Schedule solo order delivery to be triggered at the specified time
    Also check for solo fallback if only one person paid by delivery time
    """
    def trigger_delivery_at_scheduled_time():
        delivery_time = group_data.get('delivery_time', 'now')
        group_id = group_data.get('group_id')
        
        # Parse the delivery time and calculate delay
        from pangea_uber_direct import parse_delivery_time
        scheduled_datetime = parse_delivery_time(delivery_time)
        
        if scheduled_datetime:
            from datetime import timezone
            # Ensure both datetimes have timezone info
            if scheduled_datetime.tzinfo is None:
                scheduled_datetime = scheduled_datetime.replace(tzinfo=timezone.utc)
            
            current_time = datetime.now(timezone.utc)
            delay_seconds = (scheduled_datetime - current_time).total_seconds()
            
            if delay_seconds > 0:
                print(f"⏰ Solo delivery scheduled for {delivery_time} - waiting {delay_seconds} seconds")
                time.sleep(delay_seconds)
        
        # NEW: Check at delivery time if only one person paid - trigger solo delivery
        print(f"🚚 Checking group status at scheduled delivery time: {delivery_time}")
        try:
            # Re-check group status at delivery time
            all_group_sessions = db.collection('order_sessions')\
                                .where('group_id', '==', group_id)\
                                .get()
            
            group_sessions = [doc.to_dict() for doc in all_group_sessions]
            members_who_paid = []
            
            for session_data in group_sessions:
                payment_requested_at = session_data.get('payment_requested_at')
                if payment_requested_at:
                    members_who_paid.append({
                        'user_phone': session_data.get('user_phone'),
                        'order_number': session_data.get('order_number'),
                        'customer_name': session_data.get('customer_name'),
                        'session_data': session_data
                    })
            
            paid_count = len(members_who_paid)
            total_count = len(group_sessions)
            
            print(f"📊 At delivery time: {paid_count}/{total_count} members have paid")
            
            if paid_count == 1 and total_count == 2:
                # Only one person paid - trigger solo delivery for that person
                solo_member = members_who_paid[0]
                print(f"🚚 SOLO FALLBACK: Only {solo_member['user_phone']} paid - triggering solo delivery")
                
                # Build solo delivery data
                solo_group_data = {
                    'restaurant': group_data.get('restaurant'),
                    'pickup_location': group_data.get('pickup_location'),
                    'delivery_location': group_data.get('delivery_location'),
                    'delivery_time': delivery_time,
                    'members': [solo_member['user_phone']],
                    'group_id': group_id,
                    'group_size': 1,
                    'order_details': [
                        {
                            'user_phone': solo_member['user_phone'],
                            'order_number': solo_member['order_number'],
                            'customer_name': solo_member['customer_name'],
                            'order_description': solo_member['session_data'].get('order_description')
                        }
                    ]
                }
                
                from pangea_uber_direct import create_group_delivery
                delivery_result = create_group_delivery(solo_group_data)
                
                if delivery_result.get('success'):
                    print(f"✅ Solo fallback delivery created: {delivery_result.get('delivery_id')}")
                    
                    # Update solo member's session
                    solo_session = solo_member['session_data']
                    solo_session['delivery_triggered'] = True
                    solo_session['delivery_id'] = delivery_result.get('delivery_id')
                    solo_session['tracking_url'] = delivery_result.get('tracking_url')
                    solo_session['delivery_scheduled'] = False
                    solo_session['solo_fallback_triggered'] = True
                    update_order_session(solo_member['user_phone'], solo_session)
                    
                    # Send solo delivery notification
                    send_friendly_message(
                        solo_member['user_phone'],
                        f"""🚚 Your solo delivery has been triggered!

Your {group_data.get('restaurant')} order is being picked up now since the delivery time arrived and you were the only one who paid.

📱 Track your order: {delivery_result.get('tracking_url')}
📦 Delivery ID: {delivery_result.get('delivery_id', 'N/A')}

Your driver will contact you when they arrive! 🎉""",
                        message_type="solo_fallback_delivery"
                    )
                    
                    # Clear non-paying member from group
                    for session_data in group_sessions:
                        if not session_data.get('payment_requested_at'):
                            non_paying_phone = session_data.get('user_phone')
                            print(f"🗑️ Removing non-paying member {non_paying_phone} from completed group")
                            session_data['group_id'] = None
                            session_data['group_size'] = 1
                            session_data['awaiting_match'] = False
                            update_order_session(non_paying_phone, session_data)
                    
                else:
                    print(f"❌ Solo fallback delivery creation failed: {delivery_result}")
                    
            elif paid_count == 2:
                # Both members paid - proceed with normal group delivery
                print(f"🚚 Both members paid - triggering group delivery")
                from pangea_uber_direct import create_group_delivery
                delivery_result = create_group_delivery(group_data)
                
                if delivery_result.get('success'):
                    print(f"✅ Group delivery created: {delivery_result.get('delivery_id')}")
                    
                    # Update sessions for ALL group members
                    for member in members_who_paid:
                        member_session = member['session_data']
                        member_session['delivery_triggered'] = True
                        member_session['delivery_id'] = delivery_result.get('delivery_id')
                        member_session['tracking_url'] = delivery_result.get('tracking_url')
                        member_session['delivery_scheduled'] = False
                        update_order_session(member['user_phone'], member_session)
                    
                    # Send notifications
                    schedule_delayed_triggered_notifications(group_data, delivery_result)
                    
                else:
                    print(f"❌ Group delivery creation failed: {delivery_result}")
            
            else:
                print(f"⚠️ Unexpected payment state at delivery time: {paid_count}/{total_count}")
                
        except Exception as e:
            print(f"❌ Solo delivery trigger error: {e}")
    
    # Start background thread to wait and trigger delivery
    thread = threading.Thread(target=trigger_delivery_at_scheduled_time)
    thread.daemon = True
    thread.start()
    print(f"⏰ Solo delivery trigger scheduled for {group_data.get('delivery_time')}")


def check_group_completion_and_trigger_delivery(user_phone: str):
   """FIXED: Two-person group logic with 50-second delay and solo fallback"""
   
   session = get_user_order_session(user_phone)
   if not session:
       return
   
   # ENHANCED: Check if this is a solo order that should wait for matches
   group_size = session.get('group_size', 1)
   delivery_time = session.get('delivery_time', 'now')
   
   # Check if we're close to delivery time and should finalize solo orders
   close_to_delivery_time = False
   if delivery_time != 'now' and (group_size == 1 or group_size == 2):
       try:
           from pangea_uber_direct import parse_delivery_time
           from datetime import datetime, timedelta
           import pytz
           
           scheduled_datetime = parse_delivery_time(delivery_time)
           if scheduled_datetime:
               # Use Chicago timezone
               chicago_tz = pytz.timezone('America/Chicago')
               current_time = datetime.now(chicago_tz)
               
               if scheduled_datetime.tzinfo is None:
                   scheduled_datetime = chicago_tz.localize(scheduled_datetime)
               else:
                   scheduled_datetime = scheduled_datetime.astimezone(chicago_tz)
                   
               time_until_delivery = (scheduled_datetime - current_time).total_seconds() / 60  # minutes
               
               # If within 10 minutes of delivery time, stop waiting for match
               if time_until_delivery <= 10:
                   close_to_delivery_time = True
                   print(f"⏰ Within {time_until_delivery:.1f} minutes of delivery - finalizing solo order")
                   
                   # Clear awaiting_match flags for all group members (or solo user)
                   if group_size == 1:
                       # Solo order - clear own awaiting_match
                       session['awaiting_match'] = False
                       update_order_session(user_phone, session)
                       print(f"✅ Cleared awaiting_match for solo user {user_phone}")
                   else:
                       # Group order - clear for all members
                       group_id = session.get('group_id')
                       if group_id:
                           group_sessions_docs = db.collection('order_sessions').where('group_id', '==', group_id).get()
                           for member_doc in group_sessions_docs:
                               member_session = member_doc.to_dict()
                               member_phone = member_session.get('user_phone')
                               if member_session.get('awaiting_match'):
                                   member_session['awaiting_match'] = False
                                   update_order_session(member_phone, member_session)
                                   print(f"✅ Cleared awaiting_match for {member_phone}")
       except Exception as e:
           print(f"⚠️ Error checking delivery timing: {e}")
   
   # ONLY protection for solo orders - NO 2-person group protection since we enforce 2-person max
   should_wait_for_solo_match = (
       # Only protect solo orders waiting for a match (not 2-person groups)
       group_size == 1 and 
       session.get('solo_order') and 
       session.get('is_scheduled') and 
       session.get('awaiting_match') and
       delivery_time != 'now' and 
       not session.get('delivery_triggered') and 
       not close_to_delivery_time
   )
   
   if should_wait_for_solo_match:
       print(f"⏳ Solo order awaiting match - NOT triggering delivery")
       print(f"   Reason: group_size={group_size}, delivery_time={delivery_time}, delivery_triggered={session.get('delivery_triggered', False)}")
       return
   
   group_id = session.get('group_id')
   if not group_id:
       return
   
   print(f"🔍 Checking if group {group_id} is ready for delivery...")
   
   # Get ALL sessions for this group
   try:
       all_group_sessions = db.collection('order_sessions')\
                             .where('group_id', '==', group_id)\
                             .get()
       
       group_sessions = [doc.to_dict() for doc in all_group_sessions]
       total_members = len(group_sessions)
       
       print(f"📊 Group {group_id}: {total_members} total members")
       
       # Check if ALL members have paid (texted PAY)
       members_who_paid = []
       payment_times = []
       
       for session_data in group_sessions:
           user_phone_session = session_data.get('user_phone')
           order_stage = session_data.get('order_stage')
           payment_requested_at = session_data.get('payment_requested_at')
           
           print(f"  📱 {user_phone_session}: stage={order_stage}, paid={payment_requested_at is not None}")
           
           # Check if this member has paid (payment_requested_at exists)
           if payment_requested_at:
               members_who_paid.append({
                   'user_phone': user_phone_session,
                   'order_number': session_data.get('order_number'),
                   'customer_name': session_data.get('customer_name'),
                   'session_data': session_data,
                   'payment_time': payment_requested_at
               })
               payment_times.append(payment_requested_at)
       
       print(f"✅ {len(members_who_paid)} members have paid")
       
       # ✅ STRICT 2-PERSON ENFORCEMENT: Auto-remove extra users
       if total_members > 2:
           print(f"🔧 AUTO-FIX: Group {group_id} has {total_members} members but limit is 2")
           print(f"   Members found: {[session_data.get('user_phone') for session_data in group_sessions]}")
           
           # Keep only the first 2 members and remove the rest
           valid_sessions = group_sessions[:2]
           extra_sessions = group_sessions[2:]
           
           for extra_session in extra_sessions:
               extra_phone = extra_session.get('user_phone')
               print(f"🗑️ Removing extra user {extra_phone} from group {group_id}")
               
               # Clear their group_id to remove them from this group
               try:
                   extra_session['group_id'] = None
                   extra_session['group_size'] = 1
                   extra_session['awaiting_match'] = False
                   update_order_session(extra_phone, extra_session)
                   print(f"✅ Cleared group assignment for {extra_phone}")
               except Exception as e:
                   print(f"❌ Failed to clear group for {extra_phone}: {e}")
           
           # Update group_sessions to only include valid members
           group_sessions = valid_sessions
           total_members = len(group_sessions)
           print(f"✅ Group {group_id} now has {total_members} members (auto-fixed)")
       
       # ✅ NEW: Two-person group logic with 50-second delay
       if len(members_who_paid) == total_members and len(members_who_paid) == 2:
           print(f"🚚 ALL 2 GROUP MEMBERS PAID! Processing two-person delivery logic for group {group_id}")
           
           # Build group data with individual order details
           group_data = {
               'restaurant': session.get('restaurant'),
               'pickup_location': session.get('pickup_location'),
               'delivery_location': session.get('delivery_location'),
               'delivery_time': session.get('delivery_time', 'now'),
               'members': [member['user_phone'] for member in members_who_paid],
               'group_id': group_id,
               'group_size': session.get('group_size', len(members_who_paid)),
               'order_details': [
                   {
                       'user_phone': member['user_phone'],
                       'order_number': member['order_number'],
                       'customer_name': member['customer_name'],
                       'order_description': member['session_data'].get('order_description')
                   }
                   for member in members_who_paid
               ]
           }
           
           delivery_time = group_data.get('delivery_time', 'now')
           is_scheduled_delivery = delivery_time not in ['now', 'ASAP', 'soon', 'immediately']
           
           # Check if ANY member in the group has delivery_triggered=True
           any_delivery_triggered = any(member['session_data'].get('delivery_triggered', False) for member in members_who_paid)
           
           if is_scheduled_delivery and not any_delivery_triggered:
               # For scheduled deliveries, add 50-second delay after last payment
               last_payment_time = max(payment_times)
               
               print(f"⏰ TWO-PERSON SCHEDULED DELIVERY: Adding 50-second delay after last payment")
               print(f"   Last payment at: {last_payment_time}")
               print(f"   Scheduled delivery time: {delivery_time}")
               
               def trigger_delayed_scheduled_delivery():
                   time.sleep(50)  # 50-second delay
                   
                   print(f"🚚 Triggering two-person scheduled delivery after 50s delay")
                   try:
                       from pangea_uber_direct import create_group_delivery
                       delivery_result = create_group_delivery(group_data)
                       
                       if delivery_result.get('success'):
                           print(f"✅ Two-person scheduled delivery created: {delivery_result.get('delivery_id')}")
                           
                           # Update all sessions to mark delivery as triggered
                           for member in members_who_paid:
                               member_session = member['session_data']
                               member_session['delivery_triggered'] = True
                               member_session['delivery_id'] = delivery_result.get('delivery_id')
                               member_session['tracking_url'] = delivery_result.get('tracking_url')
                               member_session['delivery_scheduled'] = False
                               update_order_session(member['user_phone'], member_session)
                           
                           # Send notifications 50 seconds after delivery trigger (total 100s from last payment)
                           schedule_delayed_triggered_notifications(group_data, delivery_result)
                           
                       else:
                           print(f"❌ Two-person scheduled delivery creation failed: {delivery_result}")
                           
                   except Exception as e:
                       print(f"❌ Two-person scheduled delivery trigger error: {e}")
               
               # Start background thread for 50-second delayed delivery
               thread = threading.Thread(target=trigger_delayed_scheduled_delivery)
               thread.daemon = True
               thread.start()
               print(f"⏰ Scheduled two-person delivery trigger for 50 seconds from now")
               
               # Update session to mark as payment completed but delivery scheduled
               for member in members_who_paid:
                   member_session = member['session_data']
                   member_session['delivery_scheduled'] = True
                   member_session['scheduled_trigger_time'] = delivery_time
                   member_session['group_payment_complete'] = True
                   member_session['group_payment_delay_start'] = datetime.now()
                   update_order_session(member['user_phone'], member_session)
               
               return  # Don't trigger delivery immediately
           
           # Import and trigger delivery IMMEDIATELY (for "now" orders only)
           try:
               from pangea_uber_direct import create_group_delivery
               delivery_result = create_group_delivery(group_data)
               
               if delivery_result.get('success'):
                   print(f"✅ Immediate delivery created: {delivery_result.get('delivery_id')}")
                   
                   # Check delivery type and send appropriate 50-second delayed notification
                   delivery_time = group_data.get('delivery_time', 'now')
                   if delivery_time == 'now':
                       # Immediate delivery: send 2nd message after 50 seconds
                       schedule_delayed_delivery_notifications(group_data, delivery_result)
                   else:
                       # Scheduled delivery: send 1st message after 50 seconds
                       schedule_delayed_triggered_notifications(group_data, delivery_result)
                   
                   # Update all sessions to mark delivery as triggered
                   for member in members_who_paid:
                       member_session = member['session_data']
                       member_session['delivery_triggered'] = True
                       member_session['delivery_id'] = delivery_result.get('delivery_id')
                       member_session['tracking_url'] = delivery_result.get('tracking_url')
                       
                       update_order_session(member['user_phone'], member_session)
               
               else:
                   print(f"❌ Delivery creation failed: {delivery_result}")
                   
           except ImportError:
               print("❌ Uber Direct integration not available")
           except Exception as e:
               print(f"❌ Delivery creation error: {e}")
       
       elif len(members_who_paid) > 2:
           print(f"❌ ERROR: Group has {len(members_who_paid)} members - groups are limited to 2 people maximum")
           print(f"❌ Delivery NOT triggered for oversized group {group_id}")
       else:
           missing_count = total_members - len(members_who_paid)
           print(f"⏳ Waiting for {missing_count} more members to pay")
           
   except Exception as e:
       print(f"❌ Error checking group completion: {e}")


def notify_group_about_delivery_creation(group_data: Dict, delivery_result: Dict):
    """Notify all group members that delivery has been triggered"""
    
    restaurant = group_data.get('restaurant')
    location = group_data.get('location')
    tracking_url = delivery_result.get('tracking_url', '')
    delivery_id = delivery_result.get('delivery_id', '')
    
    message = f"""🚚 DELIVERY TRIGGERED! 🎉

Your {restaurant} group order is now being processed!

📍 Pickup: {restaurant}
📍 Dropoff: {location}
🆔 Delivery ID: {delivery_id[:8]}...

The driver will pick up all individual orders and deliver them to {location}. 

📱 Track delivery: {tracking_url}

I'll keep you updated as the driver picks up and delivers your orders! 🍕"""
    
    # Send to all group members
    for member_phone in group_data.get('members', []):
        try:
            send_friendly_message(member_phone, message, message_type="delivery_triggered")
        except Exception as e:
            print(f"❌ Failed to notify {member_phone} about delivery: {e}")


# REPLACE the existing process_order_message function (around line 400) with this:

def process_order_message(phone_number: str, message_body: str):
    """Main function to process order-related messages"""
    
    # FIRST: Check if this is a new food request
    if is_new_food_request(message_body, phone_number):
        print(f"🆕 Detected new food request from {phone_number}: {message_body}")
        # Clear any old order session
        clear_old_order_session(phone_number)
        # Return None so main system handles it
        return None
    
    # Check if user has an active order session
    session = get_user_order_session(phone_number)
    
    if not session:
        # No active session - this message should go to main system
        return None
    
    # Check if session is stale (older than 2 hours)
    session_created = session.get('created_at')
    if session_created:
        try:
            # Handle timezone differences by converting both to naive datetime
            current_time = datetime.now()
            
            if hasattr(session_created, 'tzinfo') and session_created.tzinfo is not None:
                # Convert timezone-aware to naive by removing timezone info
                session_created = session_created.replace(tzinfo=None)
            
            if hasattr(current_time, 'tzinfo') and current_time.tzinfo is not None:
                # Convert timezone-aware to naive by removing timezone info  
                current_time = current_time.replace(tzinfo=None)
            
            time_diff = current_time - session_created
            
            if time_diff > timedelta(hours=2):
                print(f"🕐 Order session is stale ({time_diff}), clearing it")
                clear_old_order_session(phone_number)
                return None
        except Exception as e:
            print(f"⚠️ Error comparing session times, continuing anyway: {e}")
            # If there's any error with time comparison, just continue with the session
    
    print(f"📋 Processing order continuation for {phone_number}")
    
    # User has active order session - process through order workflow
    initial_state = OrderState(
        messages=[HumanMessage(content=message_body)],
        user_phone=phone_number,
        group_id=session.get('group_id', ''),
        restaurant=session.get('restaurant', ''),
        order_stage='',
        pickup_location=session.get('pickup_location', ''),
        group_size=session.get('group_size', 2),
        payment_link=session.get('payment_link', ''),
        order_session_id=session.get('order_session_id', ''),
        order_number=session.get('order_number'),
        customer_name=session.get('customer_name')
    )
    
    app = create_order_graph()
    final_state = app.invoke(initial_state)
    
    return final_state

# Helper function to send message (fallback if not imported)
def send_friendly_message_fallback(phone_number: str, message: str, message_type: str = "general") -> bool:
    """Fallback message sending if main function not available"""
    try:
        twilio_client.messages.create(
            body=message,
            from_=os.getenv('TWILIO_PHONE_NUMBER'),
            to=phone_number
        )
        return True
    except Exception as e:
        print(f"SMS failed: {e}")
        return False

# Use main system's send_friendly_message if available, otherwise use fallback
try:
    send_friendly_message
except NameError:
    send_friendly_message = send_friendly_message_fallback

if __name__ == "__main__":
    print("🍕 Pangea Order Processing System Ready!")
    print("This module handles order flow after users join groups.")