# enhanced_pangea_main.py
"""
Enhanced Pangea Main Application
Smart chatbot that feels conversational while using LangGraph and Claude tools
"""

import os
import json
from datetime import datetime, timedelta
from typing import Dict, List, Optional
from dotenv import load_dotenv
import uuid

# Core dependencies
from langchain_anthropic import ChatAnthropic
from twilio.rest import Client
import firebase_admin
from firebase_admin import credentials, firestore
from flask import Flask, request

# Our enhanced modules
from smart_session_manager import SmartSessionManager
from intelligent_matching import IntelligentMatcher
from simple_intelligent_chatbot import SimpleIntelligentChatbot
from delivery_trigger_system import DeliveryTriggerSystem

load_dotenv()

def initialize_services():
    """Initialize all external services"""
    
    # Twilio
    twilio_client = Client(
        os.getenv('TWILIO_ACCOUNT_SID'), 
        os.getenv('TWILIO_AUTH_TOKEN')
    )
    
    # Claude Opus 4
    anthropic_llm = ChatAnthropic(
        model="claude-opus-4-20250514",
        api_key=os.getenv('ANTHROPIC_API_KEY'),
        temperature=0.1,
        max_tokens=4096
    )
    
    # Firebase
    if not firebase_admin._apps:
        firebase_json = os.getenv('FIREBASE_SERVICE_ACCOUNT_JSON')
        if firebase_json:
            try:
                firebase_config = json.loads(firebase_json)
                cred = credentials.Certificate(firebase_config)
                firebase_admin.initialize_app(cred)
                print("✅ Firebase initialized successfully")
            except Exception as e:
                print(f"❌ Firebase initialization failed: {e}")
                raise
        else:
            print("❌ FIREBASE_SERVICE_ACCOUNT_JSON not set")
            raise ValueError("Firebase credentials not configured")
    
    db = firestore.client()
    
    return twilio_client, anthropic_llm, db

# Initialize global services
twilio_client, anthropic_llm, db = initialize_services()

def send_friendly_message(phone_number: str, message: str, message_type: str = "general") -> bool:
    """Send SMS via Twilio with enhanced message context"""
    try:
        # Get user preferences for message enhancement
        try:
            user_doc = db.collection('users').document(phone_number).get()
            user_prefs = user_doc.to_dict() if user_doc.exists else {}
            
            # Enhance message based on user history if needed
            if message_type in ["welcome", "first_time"]:
                # Don't enhance welcome messages
                pass
            elif len(user_prefs.get('successful_matches', [])) > 0:
                # Returning user - can use more casual tone
                pass
            
        except Exception:
            # If enhancement fails, just send original message
            pass
        
        # Send via Twilio
        twilio_client.messages.create(
            body=message,
            from_=os.getenv('TWILIO_PHONE_NUMBER'),
            to=phone_number
        )
        
        print(f"✅ SMS sent to {phone_number}")
        return True
        
    except Exception as e:
        print(f"❌ SMS failed to {phone_number}: {e}")
        return False

# Initialize our smart systems
print("🔧 Initializing session manager...")
session_manager = SmartSessionManager(db, anthropic_llm)
print("✅ Session manager initialized")

print("🔧 Initializing intelligent matcher...")
matcher = IntelligentMatcher(db, anthropic_llm)
print("✅ Intelligent matcher initialized")

print("🔧 Initializing delivery system...")
delivery_system = DeliveryTriggerSystem(db, session_manager, send_friendly_message)
print("✅ Delivery system initialized")

print("🔧 Initializing simple intelligent chatbot...")
try:
    chatbot = SimpleIntelligentChatbot(anthropic_llm, db, session_manager, matcher, delivery_system, send_friendly_message)
    print("✅ Simple intelligent chatbot initialized successfully")
except Exception as init_error:
    print(f"❌ CHATBOT INIT FAILED: {init_error}")
    import traceback
    traceback.print_exc()
    raise init_error

def handle_incoming_message(user_phone: str, message: str) -> Dict:
    """
    Main message handler - uses simple intelligent chatbot
    Single AI that handles everything dynamically
    """
    
    print(f"📱 Message from {user_phone}: {message}")
    import pytz
    chicago_tz = pytz.timezone('America/Chicago')
    chicago_now = datetime.now(chicago_tz)
    print(f"🕐 Timestamp: {chicago_now.strftime('%Y-%m-%d %H:%M:%S')}")
    
    try:
        print(f"🔍 Starting message processing...")
        
        print(f"🤖 Routing to simple intelligent chatbot...")
        
        # Route through simple intelligent chatbot
        try:
            result = chatbot.handle_message(user_phone, message)
            print(f"✅ Simple chatbot completed successfully")
        except Exception as chatbot_error:
            print(f"❌ Simple chatbot failed: {chatbot_error}")
            import traceback
            traceback.print_exc()
            raise chatbot_error
        
        print(f"🤖 Workflow result: {result['status']}")
        print(f"🎯 Action: {result.get('action', 'unknown')}")
        print(f"💭 Intent: {result.get('intent', 'unknown')}")
        
        return result
        
    except Exception as e:
        print(f"❌ FULL MESSAGE HANDLING FAILED: {e}")
        print(f"❌ Error type: {type(e).__name__}")
        print(f"❌ Error args: {e.args}")
        import traceback
        print("❌ FULL TRACEBACK:")
        traceback.print_exc()
        print("❌ END TRACEBACK")
        
        # Send friendly error message
        error_response = "Sorry, I had a technical hiccup! Can you try that again? 😊"
        send_friendly_message(user_phone, error_response)
        
        return {
            'status': 'error',
            'error': str(e),
            'response_sent': True
        }

def handle_payment_message(user_phone: str) -> Dict:
    """Special handler for PAY messages - integrates with delivery system"""
    
    print(f"💳 Processing PAY message from {user_phone}")
    
    try:
        # Use delivery system to handle payment
        payment_result = delivery_system.handle_user_payment(user_phone)
        
        if payment_result['status'] == 'success':
            # Payment processed successfully
            context = session_manager.get_user_context(user_phone)
            order_session = context.active_order_session
            
            if order_session:
                restaurant = order_session.get('restaurant', 'restaurant')
                group_size = order_session.get('group_size', 1)
                
                # Generate payment link
                payment_amount = "$3.50" if group_size == 1 else "$4.50"
                payment_link = get_payment_link(group_size)
                
                response_message = f"""💳 Payment for {restaurant}

Your share: {payment_amount}

Pay here: {payment_link}

After payment, I'll coordinate your delivery! 🚚"""
                
                send_friendly_message(user_phone, response_message)
                
                return {
                    'status': 'success',
                    'action': 'payment_processed',
                    'response_sent': True
                }
            
        elif payment_result['status'] == 'waiting':
            # Waiting for group partner
            response_message = "Perfect! I've received your payment request. Waiting for your group partner to pay, then I'll trigger the delivery! 🚚"
            send_friendly_message(user_phone, response_message)
            
            return {
                'status': 'success',
                'action': 'payment_waiting',
                'response_sent': True
            }
            
        elif payment_result['status'] == 'scheduled':
            # Scheduled delivery - still need to show payment link
            context = session_manager.get_user_context(user_phone)
            order_session = context.active_order_session
            
            if order_session:
                restaurant = order_session.get('restaurant', 'restaurant')
                group_size = order_session.get('group_size', 1)
                scheduled_time = payment_result.get('scheduled_time', 'scheduled time')
                
                # Generate payment link
                payment_amount = "$3.50" if group_size == 1 else "$4.50"
                payment_link = get_payment_link(group_size)
                
                response_message = f"""💳 Payment for {restaurant}

Your share: {payment_amount}

Pay here: {payment_link}

After payment, delivery will be triggered at {scheduled_time}! ⏰"""
            else:
                response_message = f"Perfect! Your payment is processed. Delivery will be triggered at {scheduled_time}. ⏰"
            
            send_friendly_message(user_phone, response_message)
            
            return {
                'status': 'success',
                'action': 'payment_scheduled',
                'response_sent': True
            }
            
        elif payment_result['status'] == 'conditional_scheduled':
            # Conditional scheduled delivery - show payment link and explain timing
            context = session_manager.get_user_context(user_phone)
            order_session = context.active_order_session
            
            if order_session:
                restaurant = order_session.get('restaurant', 'restaurant')
                group_size = order_session.get('group_size', 1)
                scheduled_time = payment_result.get('scheduled_time', 'scheduled time')
                
                # Generate payment link
                payment_amount = "$3.50" if group_size == 1 else "$4.50"
                payment_link = get_payment_link(group_size)
                
                response_message = f"""💳 Payment for {restaurant}

Your share: {payment_amount}

Pay here: {payment_link}

Delivery will be triggered at {scheduled_time}! ⏰"""
            else:
                scheduled_time = payment_result.get('scheduled_time', 'scheduled time')
                response_message = f"Perfect! Your payment is processed. Delivery will be triggered at {scheduled_time}! ⏰"
            
            send_friendly_message(user_phone, response_message)
            
            return {
                'status': 'success',
                'action': 'payment_conditional_scheduled',
                'response_sent': True
            }
            
        else:
            # Payment error
            error_message = payment_result.get('message', 'Payment processing error')
            response_message = f"Sorry, there was an issue with your payment: {error_message}. Please try again or contact support."
            send_friendly_message(user_phone, response_message)
            
            return {
                'status': 'error',
                'action': 'payment_error',
                'response_sent': True
            }
            
    except Exception as e:
        print(f"❌ Payment handling error: {e}")
        
        error_response = "Sorry, I had trouble processing your payment. Please try again in a moment! 💳"
        send_friendly_message(user_phone, error_response)
        
        return {
            'status': 'error',
            'error': str(e),
            'response_sent': True
        }

def get_payment_link(group_size: int) -> str:
    """Get payment link for group size"""
    
    payment_links = {
        1: os.getenv("STRIPE_LINK_350", "https://pay.stripe.com/solo_order"),
        2: os.getenv("STRIPE_LINK_450", "https://pay.stripe.com/group_order")
    }
    
    return payment_links.get(group_size, payment_links[1])

def cleanup_old_data():
    """Clean up old data to prevent confusion"""
    
    try:
        cutoff_time = datetime.now() - timedelta(hours=3)
        
        print(f"🧹 Starting cleanup of data older than {cutoff_time}")
        
        # Clean up old user sessions
        old_sessions = db.collection('user_sessions')\
            .where('last_activity', '<', cutoff_time)\
            .get()
        
        for session in old_sessions:
            session.reference.delete()
            print(f"🗑️ Cleaned up user session: {session.id}")
        
        # Clean up old order sessions
        old_orders = db.collection('order_sessions')\
            .where('created_at', '<', cutoff_time)\
            .get()
        
        for order in old_orders:
            order_data = order.to_dict()
            
            # Don't clean up if delivery was recently triggered
            if order_data.get('delivery_triggered'):
                delivery_time = order_data.get('delivery_triggered_at')
                if delivery_time and datetime.now() - delivery_time < timedelta(hours=1):
                    continue
            
            order.reference.delete()
            print(f"🗑️ Cleaned up order session: {order.id}")
        
        # Clean up old negotiations
        old_negotiations = db.collection('negotiations')\
            .where('created_at', '<', cutoff_time)\
            .get()
        
        for neg in old_negotiations:
            neg.reference.delete()
            print(f"🗑️ Cleaned up negotiation: {neg.id}")
        
        # Clean up old groups
        old_groups = db.collection('active_groups')\
            .where('created_at', '<', cutoff_time)\
            .get()
        
        for group in old_groups:
            group.reference.delete()
            print(f"🗑️ Cleaned up group: {group.id}")
        
        print(f"✅ Cleanup completed")
        
    except Exception as e:
        print(f"❌ Cleanup failed: {e}")


# Flask webhook server
app = Flask(__name__)

@app.route('/', methods=['GET'])
def health_check():
    """Health check endpoint"""
    return {
        'status': 'healthy',
        'service': 'Enhanced Pangea Food Coordination',
        'timestamp': datetime.now().isoformat(),
        'version': '2.0-smart-chatbot'
    }, 200

@app.route('/webhook/sms', methods=['POST'])
@app.route('/webhook', methods=['POST'])  # Backward compatibility  
def sms_webhook():
    """Handle incoming SMS messages with enhanced error handling"""
    
    try:
        # Force immediate log flush
        import sys
        print("🚀 WEBHOOK STARTED - Incoming SMS request", flush=True)
        sys.stdout.flush()
        
        start_time = datetime.now()
        
        print("🔍 Extracting form data...")
        from_number = request.form.get('From')
        message_body = request.form.get('Body')
        print(f"🔍 Extracted - From: {from_number}, Body: {message_body}")
        
        if not from_number or not message_body:
            print(f"❌ Missing required fields - From: {from_number}, Body: {message_body}")
            return '', 400
        
        print(f"📱 Webhook received: {from_number} -> {message_body}")
        
        print("🔍 Routing to smart chatbot workflow...")
        # Route through smart chatbot workflow (handles all message types)
        try:
            result = handle_incoming_message(from_number, message_body)
            print(f"✅ Smart workflow returned: {result}")
        except Exception as handler_error:
            print(f"❌ SMART WORKFLOW ERROR: {handler_error}")
            import traceback
            traceback.print_exc()
            raise handler_error
        
        processing_time = (datetime.now() - start_time).total_seconds()
        
        if result['status'] == 'success':
            print(f"✅ Message handled successfully in {processing_time:.2f}s: {result.get('action', 'unknown')}")
        else:
            print(f"❌ Message handling failed in {processing_time:.2f}s: {result.get('error', 'unknown')}")
        
        return '', 200
        
    except Exception as e:
        print(f"❌ WEBHOOK CATASTROPHIC ERROR: {e}")
        print(f"❌ Error type: {type(e).__name__}")
        import traceback
        print("❌ FULL WEBHOOK TRACEBACK:")
        traceback.print_exc()
        print("❌ END WEBHOOK TRACEBACK")
        sys.stdout.flush()
        
        # Try to send error message to user
        try:
            if 'from_number' in locals() and from_number:
                error_response = "Sorry, I'm having technical difficulties. Please try again in a few minutes! 🤖"
                send_friendly_message(from_number, error_response)
        except Exception as sms_error:
            print(f"❌ Could not send error SMS: {sms_error}")
        
        return '', 500

@app.route('/cleanup', methods=['POST'])
def manual_cleanup():
    """Manual cleanup endpoint for maintenance"""
    cleanup_old_data()
    return {'status': 'cleanup completed', 'timestamp': datetime.now().isoformat()}, 200

@app.route('/status/<phone_number>', methods=['GET'])
def user_status(phone_number):
    """Get user status for debugging"""
    try:
        context = session_manager.get_user_context(phone_number)
        
        return {
            'user_phone': phone_number,
            'session_type': context.session_type,
            'has_active_order': context.active_order_session is not None,
            'pending_invites': len(context.pending_group_invites),
            'last_activity': context.last_activity.isoformat() if context.last_activity else None,
            'current_request': context.current_food_request
        }, 200
        
    except Exception as e:
        return {'error': str(e)}, 500

@app.route('/test/message', methods=['POST'])
def test_message():
    """Test endpoint for development"""
    
    if not os.getenv('DEBUG_MODE'):
        return {'error': 'Not available in production'}, 403
    
    try:
        data = request.get_json()
        phone_number = data.get('phone_number')
        message = data.get('message')
        
        if not phone_number or not message:
            return {'error': 'Missing phone_number or message'}, 400
        
        result = handle_incoming_message(phone_number, message)
        return result, 200
        
    except Exception as e:
        return {'error': str(e)}, 500

# Scheduled tasks
def run_periodic_cleanup():
    """Run periodic cleanup in background"""
    import threading
    import time
    
    def cleanup_loop():
        while True:
            try:
                time.sleep(3600)  # Run every hour
                cleanup_old_data()
            except Exception as e:
                print(f"❌ Periodic cleanup error: {e}")
    
    cleanup_thread = threading.Thread(target=cleanup_loop)
    cleanup_thread.daemon = True
    cleanup_thread.start()

if __name__ == "__main__":
    print("🍜 Enhanced Pangea Food Coordination Starting...")
    print("🤖 Simple intelligent chatbot ready!")
    print("📱 SMS webhook ready!")
    print("🔧 Claude AI with dynamic intelligence enabled!")
    
    # Run initial cleanup
    cleanup_old_data()
    
    # Start periodic cleanup
    run_periodic_cleanup()
    
    # Start Flask server
    port = int(os.environ.get('PORT', 8000))
    debug_mode = os.getenv('DEBUG_MODE', 'false').lower() == 'true'
    
    if debug_mode:
        print("🐛 Debug mode enabled")
    
    app.run(host='0.0.0.0', port=port, debug=debug_mode)
