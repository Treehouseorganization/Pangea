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
        cred = credentials.Certificate(os.getenv('FIREBASE_SERVICE_ACCOUNT_PATH'))
        firebase_admin.initialize_app(cred)
    db = firestore.client()

# Restaurant Data
RESTAURANTS = {
    "Mario's Pizza - Campus Center": {
        "location": "Campus Center",
        "menu_items": [
            "Margherita Pizza", "Pepperoni Pizza", "Supreme Pizza", "Hawaiian Pizza",
            "BBQ Chicken Pizza", "Veggie Pizza", "Meat Lovers Pizza", "White Pizza"
        ]
    },
    "Thai Garden - Student Union": {
        "location": "Student Union", 
        "menu_items": [
            "Pad Thai", "Green Curry", "Red Curry", "Tom Yum Soup", "Fried Rice",
            "Drunken Noodles", "Massaman Curry", "Spring Rolls", "Thai Basil Stir Fry"
        ]
    },
    "Sushi Express - Library Plaza": {
        "location": "Library Plaza",
        "menu_items": [
            "California Roll", "Spicy Tuna Roll", "Salmon Avocado Roll", "Dragon Roll",
            "Rainbow Roll", "Chicken Teriyaki Bento", "Beef Teriyaki Bento", "Miso Soup"
        ]
    },
    "Burger Barn - Recreation Center": {
        "location": "Recreation Center",
        "menu_items": [
            "Classic Burger", "Cheeseburger", "BBQ Burger", "Mushroom Swiss Burger",
            "Chicken Sandwich", "Fish Sandwich", "Veggie Burger", "Bacon Burger"
        ]
    },
    "Green Bowls - Health Sciences Building": {
        "location": "Health Sciences Building",
        "menu_items": [
            "Buddha Bowl", "Quinoa Power Bowl", "Greek Salad", "Caesar Salad",
            "Protein Bowl", "Smoothie Bowl", "Avocado Toast", "Grain Bowl"
        ]
    }
}

# Payment Link Logic
PAYMENT_LINKS = {
    2: "https://buy.stripe.com/cNi00i96U8Uu0qO5nSdwc01",  # $4.50
    3: "https://buy.stripe.com/8x2eVc6YM2w6ddA03ydwc02",  # $3.50
    4: "https://buy.stripe.com/eVqbJ0gzm3Aa6Pcg2wdwc03"   # $2.50 (4+ people)
}

# Order State Management
class OrderState(TypedDict):
    messages: Annotated[List, add_messages]
    user_phone: str
    group_id: str
    restaurant: str
    order_stage: str  # "need_order_number", "ready_to_pay", "payment_initiated"
    pickup_location: str
    group_size: int
    payment_link: str
    order_session_id: str
    order_number: Optional[str]
    customer_name: Optional[str]

def get_payment_link(group_size: int) -> str:
    if group_size == 1:                       
        return PAYMENT_LINKS[3]
    elif group_size == 2:
        return PAYMENT_LINKS[2]
    elif group_size == 3:
        return PAYMENT_LINKS[3]
    else:
        return PAYMENT_LINKS[4]
    

def get_payment_amount(group_size: int) -> str:
    """Get payment amount text based on group size"""
    if group_size <= 2:
        return "$4.50"
    elif group_size == 3:
        return "$3.50"
    else:
        return "$2.50"

@tool
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

@tool
def update_order_session(phone_number: str, session_data: Dict) -> bool:
    """Update user's order session"""
    try:
        session_data['last_updated'] = datetime.now()
        db.collection('order_sessions').document(phone_number).set(session_data, merge=True)
        return True
    except Exception as e:
        print(f"Error updating order session: {e}")
        return False

def start_order_process(user_phone: str, group_id: str, restaurant: str, group_size: int):
    """Called from main system when user joins a group - starts the order process"""
    
    # Create order session
    session_data = {
        'user_phone': user_phone,
        'group_id': group_id,
        'restaurant': restaurant,
        'group_size': group_size,
        'order_stage': 'need_order_number',
        'pickup_location': RESTAURANTS.get(restaurant, {}).get('location', 'Campus'),
        'payment_link': get_payment_link(group_size),
        'order_session_id': str(uuid.uuid4()),
        'created_at': datetime.now(),
        'order_number': None,
        'customer_name': None
    }
    
    update_order_session.invoke({"phone_number": user_phone, "session_data": session_data})
    
    payment_amount = get_payment_amount(group_size)
    
    # Send order instructions
    welcome_message = f"""🎉 You're now in the {restaurant} group!

Great! To order from {restaurant}, please follow these steps:

1. First, place your order directly with {restaurant}'s app/website/phone and select PICKUP option (not delivery)
2. Once you've placed your order, come back and let me know your order confirmation number

Have you already placed your order with {restaurant}? If so, do you have your order confirmation number? If you don't have an order number or if {restaurant} doesn't provide one, just let me know your name instead.

Your payment share will be {payment_amount} once everyone has their orders ready! 💳"""
    
    send_friendly_message(user_phone, welcome_message, message_type="order_start")
    
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
    session = get_user_order_session.invoke({"phone_number": user_phone})
    
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
    session = get_user_order_session.invoke({"phone_number": user_phone})
    
    # Use Claude to extract order number or name
    extraction_prompt = f"""
    The user is providing their order confirmation number or name for pickup.
    
    User message: "{user_message}"
    
    Extract either:
    1. An order confirmation number/ID (letters, numbers, or combination)
    2. A customer name if no order number is provided
    
    Return JSON with:
    - "type": "order_number" or "customer_name"
    - "value": the extracted value
    
    Examples:
    - "My order number is ABC123" → {{"type": "order_number", "value": "ABC123"}}
    - "Order #4567" → {{"type": "order_number", "value": "4567"}}
    - "Just use my name John Smith" → {{"type": "customer_name", "value": "John Smith"}}
    - "My name is Maria" → {{"type": "customer_name", "value": "Maria"}}
    - "I don't have an order number, use Sarah" → {{"type": "customer_name", "value": "Sarah"}}
    
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
        
        if extracted_data.get("type") == "order_number":
            session['order_number'] = extracted_data.get("value")
            session['order_stage'] = 'ready_to_pay'
            identifier = f"order #{extracted_data.get('value')}"
        elif extracted_data.get("type") == "customer_name":
            session['customer_name'] = extracted_data.get("value")
            session['order_stage'] = 'ready_to_pay'
            identifier = f"name: {extracted_data.get('value')}"
        else:
            # Couldn't extract valid info
            message = f"""I couldn't find an order number or name in that message. 

Please provide either:
• Your order confirmation number (like "ABC123" or "#4567")
• Your name if there's no order number (like "John Smith")

This helps me coordinate pickup with {session.get('restaurant', 'the restaurant')}!"""
            
            send_friendly_message(user_phone, message, message_type="clarification")
            state['messages'].append(AIMessage(content=message))
            return state
        
        # Successfully got order info
        update_order_session.invoke({"phone_number": user_phone, "session_data": session})
        
        payment_amount = get_payment_amount(session.get('group_size', 2))
        
        message = f"""Perfect! I've got your {identifier} for {session.get('restaurant')}! ✅

Your payment share: {payment_amount}
Pickup location: {session.get('pickup_location')}

When you're ready to pay, just text:
**PAY**

I'll send you the payment link! 💳"""
        
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
                update_order_session.invoke({"phone_number": user_phone, "session_data": session})
                
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
    state['messages'].append(AIMessage(content=message))
    return state

def handle_need_order_first_node(state: OrderState) -> OrderState:
    """Handle when user tries to pay without providing order number"""
    
    user_phone = state['user_phone']
    session = get_user_order_session.invoke({"phone_number": user_phone})
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
    session = get_user_order_session.invoke({"phone_number": user_phone})
    
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
    session = get_user_order_session.invoke({"phone_number": user_phone})
    
    # Order is confirmed, move to payment stage
    session['order_stage'] = 'ready_to_pay'
    update_order_session.invoke({"phone_number": user_phone, "session_data": session})
    
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
    session = get_user_order_session.invoke({"phone_number": user_phone})
    
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
    update_order_session.invoke({"phone_number": user_phone, "session_data": session})
    
    message = f"""💳 Payment for {restaurant}

Your share: {payment_amount}
Group size: {group_size} people

Click here to pay:
{payment_link}

After payment, I'll coordinate with your group to place the order! 🍕"""
    
    send_friendly_message(user_phone, message, message_type="payment")
    state['messages'].append(AIMessage(content=message))
    return state

def handle_clarification_node(state: OrderState) -> OrderState:
    """Handle cases where user input needs clarification"""
    
    user_phone = state['user_phone']
    session = get_user_order_session.invoke({"phone_number": user_phone})
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
            "payment_request": "handle_payment_request",
            "redirect_to_payment": "handle_redirect_to_payment",
            "need_order_first": "handle_need_order_first",
            "no_session": "handle_no_session"
        }
    )
    
    # All nodes end the workflow
    workflow.add_edge("collect_order_number", END)
    workflow.add_edge("handle_payment_request", END)
    workflow.add_edge("handle_redirect_to_payment", END)
    workflow.add_edge("handle_need_order_first", END)
    workflow.add_edge("handle_no_session", END)
    
    workflow.set_entry_point("classify_order_intent")
    
    return workflow.compile()

# ADD these new functions to pangea_order_processor.py (around line 50, before start_order_process)

def is_new_food_request(message: str) -> bool:
    """Detect if message is a new food request vs order continuation"""
    
    message_lower = message.lower().strip()
    
    # Keywords that indicate NEW food requests
    new_request_indicators = [
        'i want', 'want to order', 'craving', 'hungry for',
        'delivered to', 'delivery to', 'order from',
        'pizza', 'thai', 'sushi', 'burger', 'salad',
        'mario', 'thai garden', 'sushi express', 'burger barn', 'green bowls'
    ]
    
    # Keywords that indicate ORDER continuation
    order_continuation_indicators = [
        'my order number', 'order #', 'confirmation',
        'my name is', 'call me', 'pay', 'payment'
    ]
    
    # Check for new request indicators
    has_new_request = any(indicator in message_lower for indicator in new_request_indicators)
    
    # Check for order continuation indicators  
    has_order_continuation = any(indicator in message_lower for indicator in order_continuation_indicators)
    
    # If it has new request indicators and no clear order continuation, it's a new request
    if has_new_request and not has_order_continuation:
        return True
        
    # If message mentions specific restaurants or delivery, it's likely new
    restaurants = ['mario', 'thai', 'sushi', 'burger', 'green']
    if any(restaurant in message_lower for restaurant in restaurants):
        return True
        
    return False

def clear_old_order_session(phone_number: str):
    """Clear user's old order session"""
    try:
        db.collection('order_sessions').document(phone_number).delete()
        print(f"🗑️ Cleared old order session for {phone_number}")
    except Exception as e:
        print(f"❌ Failed to clear order session: {e}")

# REPLACE the existing process_order_message function (around line 400) with this:

def process_order_message(phone_number: str, message_body: str):
    """Main function to process order-related messages"""
    
    # FIRST: Check if this is a new food request
    if is_new_food_request(message_body):
        print(f"🆕 Detected new food request from {phone_number}: {message_body}")
        # Clear any old order session
        clear_old_order_session(phone_number)
        # Return None so main system handles it
        return None
    
    # Check if user has an active order session
    session = get_user_order_session.invoke({"phone_number": phone_number})
    
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
