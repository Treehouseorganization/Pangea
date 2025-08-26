# main.py
"""
Pangea Food Delivery Coordination System
Clean architecture with unified conversation management and proper state tracking
"""

import os
import json
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any
from dataclasses import dataclass, asdict
from enum import Enum
import uuid
import threading
import time

# External dependencies
from langchain_anthropic import ChatAnthropic
from twilio.rest import Client
import firebase_admin
from firebase_admin import credentials, firestore
from flask import Flask, request
from dotenv import load_dotenv

# Core modules
from conversation_manager import ConversationManager
from order_state_machine import OrderStateMachine
from delivery_coordinator import DeliveryCoordinator
from matching_engine import MatchingEngine  # Keep existing matching logic
from memory_manager import MemoryManager

load_dotenv()

class OrderStage(Enum):
    """Order progression stages"""
    IDLE = "idle"
    REQUESTING_FOOD = "requesting_food" 
    WAITING_FOR_MATCH = "waiting_for_match"
    MATCHED = "matched"
    COLLECTING_ORDER_INFO = "collecting_order_info"
    READY_TO_PAY = "ready_to_pay"
    PAYMENT_PENDING = "payment_pending" 
    DELIVERY_SCHEDULED = "delivery_scheduled"
    DELIVERED = "delivered"

@dataclass
class UserState:
    """Complete user state with memory and context"""
    user_phone: str
    session_id: str
    stage: OrderStage = OrderStage.IDLE
    
    # Order details
    restaurant: Optional[str] = None
    location: Optional[str] = None
    delivery_time: str = "now"
    order_number: Optional[str] = None
    customer_name: Optional[str] = None
    order_description: Optional[str] = None
    
    # Group information
    group_id: Optional[str] = None
    group_size: int = 1
    is_fake_match: bool = False
    
    # Payment tracking
    payment_requested_at: Optional[datetime] = None
    payment_amount: str = "$3.50"
    
    # Conversation memory
    conversation_history: List[Dict] = None
    last_activity: datetime = None
    
    # Missing information tracking
    missing_info: List[str] = None
    
    def __post_init__(self):
        if self.conversation_history is None:
            self.conversation_history = []
        if self.last_activity is None:
            self.last_activity = datetime.now()
        if self.missing_info is None:
            self.missing_info = []
    
    def to_dict(self) -> Dict:
        """Convert to dictionary for storage"""
        data = asdict(self)
        data['stage'] = self.stage.value
        data['last_activity'] = self.last_activity.isoformat() if self.last_activity else None
        data['payment_requested_at'] = self.payment_requested_at.isoformat() if self.payment_requested_at else None
        return data
    
    @classmethod
    def from_dict(cls, data: Dict) -> 'UserState':
        """Create from dictionary"""
        if 'stage' in data:
            data['stage'] = OrderStage(data['stage'])
        if 'last_activity' in data and data['last_activity']:
            data['last_activity'] = datetime.fromisoformat(data['last_activity'])
        if 'payment_requested_at' in data and data['payment_requested_at']:
            data['payment_requested_at'] = datetime.fromisoformat(data['payment_requested_at'])
        return cls(**data)

class PangeaApp:
    """Main application class with clean architecture"""
    
    def __init__(self):
        self.db = self._initialize_firebase()
        self.twilio_client = self._initialize_twilio()
        self.anthropic_llm = self._initialize_anthropic()
        
        # Core components
        self.memory_manager = MemoryManager(self.db)
        self.conversation_manager = ConversationManager(self.anthropic_llm, self.memory_manager)
        self.matching_engine = MatchingEngine(self.db, self.anthropic_llm)  # Keep existing
        self.order_state_machine = OrderStateMachine()
        self.delivery_coordinator = DeliveryCoordinator(self.db)
        
        print("✅ Pangea application initialized")
    
    def _initialize_firebase(self):
        """Initialize Firebase connection"""
        if not firebase_admin._apps:
            firebase_json = os.getenv('FIREBASE_SERVICE_ACCOUNT_JSON')
            if firebase_json:
                try:
                    firebase_config = json.loads(firebase_json)
                    cred = credentials.Certificate(firebase_config)
                    firebase_admin.initialize_app(cred)
                except Exception as e:
                    raise Exception(f"Firebase initialization failed: {e}")
            else:
                raise ValueError("Firebase credentials not configured")
        return firestore.client()
    
    def _initialize_twilio(self):
        """Initialize Twilio client"""
        return Client(
            os.getenv('TWILIO_ACCOUNT_SID'), 
            os.getenv('TWILIO_AUTH_TOKEN')
        )
    
    def _initialize_anthropic(self):
        """Initialize Anthropic Claude client"""
        return ChatAnthropic(
            model="claude-opus-4-20250514",
            api_key=os.getenv('ANTHROPIC_API_KEY'),
            temperature=0.1,
            max_tokens=4096
        )
    
    def send_sms(self, phone_number: str, message: str) -> bool:
        """Send SMS with error handling"""
        try:
            self.twilio_client.messages.create(
                body=message,
                from_=os.getenv('TWILIO_PHONE_NUMBER'),
                to=phone_number
            )
            print(f"📤 SMS sent to {phone_number}")
            return True
        except Exception as e:
            print(f"❌ SMS failed to {phone_number}: {e}")
            return False
    
    async def handle_message(self, user_phone: str, message: str) -> Dict:
        """Main message handling with proper state management"""
        print(f"📱 Processing message from {user_phone}: {message}")
        
        try:
            # Get or create user state
            user_state = await self.memory_manager.get_user_state(user_phone)
            
            # Update conversation history
            user_state.conversation_history.append({
                'message': message,
                'timestamp': datetime.now().isoformat(),
                'type': 'user'
            })
            user_state.last_activity = datetime.now()
            
            # Process message through conversation manager
            conversation_result = await self.conversation_manager.process_message(
                message, user_state
            )
            
            # Execute any triggered actions
            action_results = await self._execute_actions(
                conversation_result.get('actions', []), user_state
            )
            
            # Update user state based on results
            if conversation_result.get('state_updates'):
                self._update_user_state(user_state, conversation_result['state_updates'])
            
            # Send response if provided
            response_message = conversation_result.get('response')
            if response_message:
                # Add to conversation history
                user_state.conversation_history.append({
                    'message': response_message,
                    'timestamp': datetime.now().isoformat(),
                    'type': 'assistant'
                })
                
                # Send SMS
                self.send_sms(user_phone, response_message)
            
            # Save updated state
            await self.memory_manager.save_user_state(user_state)
            
            return {
                'status': 'success',
                'stage': user_state.stage.value,
                'actions_executed': action_results,
                'response_sent': bool(response_message)
            }
            
        except Exception as e:
            print(f"❌ Message handling error: {e}")
            
            # Send error response
            error_msg = "Sorry, I had a technical issue. Can you try again?"
            self.send_sms(user_phone, error_msg)
            
            return {
                'status': 'error',
                'error': str(e),
                'response_sent': True
            }
    
    async def _execute_actions(self, actions: List[Dict], user_state: UserState) -> List[Dict]:
        """Execute triggered actions based on conversation analysis"""
        results = []
        
        for action in actions:
            action_type = action.get('type')
            action_data = action.get('data', {})
            
            try:
                if action_type == 'find_matches':
                    result = await self._handle_find_matches(user_state, action_data)
                elif action_type == 'request_payment':
                    result = await self._handle_request_payment(user_state, action_data)
                elif action_type == 'trigger_delivery':
                    result = await self._handle_trigger_delivery(user_state, action_data)
                elif action_type == 'cancel_order':
                    result = await self._handle_cancel_order(user_state, action_data)
                else:
                    result = {'status': 'unknown_action', 'type': action_type}
                
                results.append(result)
                
            except Exception as e:
                print(f"❌ Action execution error ({action_type}): {e}")
                results.append({
                    'status': 'error',
                    'type': action_type,
                    'error': str(e)
                })
        
        return results
    
    async def _handle_find_matches(self, user_state: UserState, action_data: Dict) -> Dict:
        """Handle finding matches using existing matching engine"""
        if not all([user_state.restaurant, user_state.location]):
            return {'status': 'missing_info', 'missing': user_state.missing_info}
        
        # Use existing matching engine logic
        match_result = self.matching_engine.find_compatible_matches(
            user_state.user_phone,
            user_state.restaurant, 
            user_state.location,
            user_state.delivery_time
        )
        
        if match_result['has_real_match']:
            # Real match found
            best_match = match_result['matches'][0]
            
            if match_result.get('is_silent_upgrade'):
                # Silent upgrade scenario
                group_id = self.matching_engine.create_silent_upgrade_group(
                    user_state.user_phone, 
                    best_match['user_phone'],
                    user_state.restaurant,
                    user_state.location,
                    best_match.get('delivery_time', user_state.delivery_time),
                    best_match.get('group_id')
                )
                user_state.group_id = group_id
                user_state.group_size = 2
                user_state.is_fake_match = False
                user_state.stage = OrderStage.MATCHED
                user_state.payment_amount = "$4.50"
                
                return {
                    'status': 'silent_upgrade_match',
                    'group_id': group_id,
                    'partner': best_match['user_phone']
                }
            else:
                # Regular real match
                group_id = self.matching_engine.create_group_match(
                    user_state.user_phone,
                    best_match['user_phone'], 
                    user_state.restaurant,
                    user_state.location,
                    best_match.get('time_analysis', {}).get('optimal_time', user_state.delivery_time)
                )
                
                user_state.group_id = group_id
                user_state.group_size = 2 
                user_state.is_fake_match = False
                user_state.stage = OrderStage.MATCHED
                user_state.payment_amount = "$4.50"
                
                # Notify matched user
                match_message = f"""Great news! Another student wants {user_state.restaurant} at {user_state.location} too!

**Group Confirmed (2 people)**
Your share: $4.50 each (vs $8+ solo)

**Next steps:**
1. Order from {user_state.restaurant} (choose PICKUP, not delivery)
2. Come back with your order number/name AND what you ordered
3. Text "PAY" when ready

Time to order!"""
                
                self.send_sms(best_match['user_phone'], match_message)
                
                return {
                    'status': 'real_match_found',
                    'group_id': group_id,
                    'partner': best_match['user_phone']
                }
        else:
            # No real match - create fake match
            group_id = self.matching_engine.create_fake_match(
                user_state.user_phone,
                user_state.restaurant,
                user_state.location, 
                user_state.delivery_time
            )
            
            user_state.group_id = group_id
            user_state.group_size = 1
            user_state.is_fake_match = True
            user_state.stage = OrderStage.MATCHED
            user_state.payment_amount = "$3.50"
            
            return {
                'status': 'fake_match_created',
                'group_id': group_id
            }
    
    async def _handle_request_payment(self, user_state: UserState, action_data: Dict) -> Dict:
        """Handle payment request"""
        if not self._has_complete_order_info(user_state):
            return {
                'status': 'incomplete_order',
                'missing': user_state.missing_info
            }
        
        # Generate payment link
        payment_links = {
            1: os.getenv("STRIPE_LINK_350", "https://pay.stripe.com/solo_order"),
            2: os.getenv("STRIPE_LINK_450", "https://pay.stripe.com/group_order")
        }
        payment_link = payment_links.get(user_state.group_size, payment_links[1])
        
        # Update state
        user_state.payment_requested_at = datetime.now()
        user_state.stage = OrderStage.PAYMENT_PENDING
        
        # Send payment message
        payment_message = f"""💳 Payment for {user_state.restaurant}

Your share: {user_state.payment_amount}

Click here to pay:
{payment_link}

After payment, I'll coordinate your delivery!"""
        
        self.send_sms(user_state.user_phone, payment_message)
        
        # Check if delivery should be triggered
        if await self._should_trigger_delivery_now(user_state):
            await self._handle_trigger_delivery(user_state, {})
        
        return {
            'status': 'payment_requested',
            'payment_link': payment_link
        }
    
    async def _handle_trigger_delivery(self, user_state: UserState, action_data: Dict) -> Dict:
        """Handle delivery triggering with proper timing logic"""
        
        # Check delivery timing rules
        is_immediate = user_state.delivery_time in ['now', 'asap', 'soon', 'immediately']
        
        if user_state.is_fake_match:
            # Solo order logic
            if is_immediate:
                # Immediate solo delivery - trigger now
                return await self._trigger_delivery_now(user_state)
            else:
                # Scheduled solo delivery - wait until delivery time
                return await self._schedule_delivery(user_state)
        else:
            # Group order logic  
            group_ready = await self._is_group_ready_for_delivery(user_state.group_id)
            
            if group_ready and is_immediate:
                # Immediate group delivery - trigger now
                return await self._trigger_delivery_now(user_state)
            elif group_ready and not is_immediate:
                # Scheduled group delivery - trigger at delivery time
                return await self._schedule_delivery(user_state)
            else:
                # Wait for other group members
                return {'status': 'waiting_for_group'}
    
    async def _handle_cancel_order(self, user_state: UserState, action_data: Dict) -> Dict:
        """Handle order cancellation"""
        # Clear order state
        user_state.stage = OrderStage.IDLE
        user_state.restaurant = None
        user_state.location = None
        user_state.delivery_time = "now"
        user_state.order_number = None
        user_state.customer_name = None
        user_state.order_description = None
        user_state.group_id = None
        user_state.group_size = 1
        user_state.is_fake_match = False
        user_state.payment_requested_at = None
        user_state.missing_info = []
        
        return {'status': 'order_cancelled'}
    
    def _update_user_state(self, user_state: UserState, updates: Dict):
        """Update user state with new information"""
        for key, value in updates.items():
            if hasattr(user_state, key):
                setattr(user_state, key, value)
        
        # Update missing info tracking
        user_state.missing_info = self._calculate_missing_info(user_state)
    
    def _calculate_missing_info(self, user_state: UserState) -> List[str]:
        """Calculate what information is still missing"""
        missing = []
        
        if not user_state.restaurant:
            missing.append('restaurant')
        if not user_state.location:
            missing.append('location')
        
        # For order collection stage
        if user_state.stage == OrderStage.COLLECTING_ORDER_INFO:
            if not (user_state.order_number or user_state.customer_name):
                missing.append('order_identifier')
            if not user_state.order_description:
                missing.append('order_description')
        
        return missing
    
    def _has_complete_order_info(self, user_state: UserState) -> bool:
        """Check if user has provided complete order information"""
        has_identifier = user_state.order_number or user_state.customer_name
        has_description = user_state.order_description
        has_restaurant = user_state.restaurant
        has_location = user_state.location
        
        return all([has_identifier, has_description, has_restaurant, has_location])
    
    async def _should_trigger_delivery_now(self, user_state: UserState) -> bool:
        """Determine if delivery should be triggered immediately"""
        if user_state.is_fake_match:
            # Solo order - only trigger if immediate
            return user_state.delivery_time in ['now', 'asap', 'soon', 'immediately']
        else:
            # Group order - check if all members have paid
            return await self._is_group_ready_for_delivery(user_state.group_id)
    
    async def _is_group_ready_for_delivery(self, group_id: str) -> bool:
        """Check if all group members have paid"""
        if not group_id:
            return False
        
        try:
            # Get all users in this group who have paid
            group_members = await self.memory_manager.get_group_members(group_id)
            paid_members = [
                member for member in group_members 
                if member.payment_requested_at is not None
            ]
            
            return len(paid_members) == len(group_members) and len(paid_members) > 0
            
        except Exception as e:
            print(f"❌ Error checking group readiness: {e}")
            return False
    
    async def _trigger_delivery_now(self, user_state: UserState) -> Dict:
        """Trigger delivery immediately"""
        try:
            group_members = [user_state]
            if not user_state.is_fake_match:
                group_members = await self.memory_manager.get_group_members(user_state.group_id)
            
            # Build delivery data
            delivery_data = {
                'group_id': user_state.group_id,
                'restaurant': user_state.restaurant,
                'location': user_state.location,
                'members': [member.user_phone for member in group_members],
                'group_size': len(group_members),
                'order_details': [
                    {
                        'user_phone': member.user_phone,
                        'order_number': member.order_number,
                        'customer_name': member.customer_name,
                        'order_description': member.order_description
                    }
                    for member in group_members
                ]
            }
            
            # Use delivery coordinator
            result = await self.delivery_coordinator.create_delivery(delivery_data)
            
            if result.get('success'):
                # Update user states
                for member in group_members:
                    member.stage = OrderStage.DELIVERED
                    await self.memory_manager.save_user_state(member)
                
                # Schedule delivery notifications
                self._schedule_delivery_notifications(delivery_data, result)
            
            return result
            
        except Exception as e:
            print(f"❌ Delivery trigger error: {e}")
            return {'status': 'error', 'error': str(e)}
    
    async def _schedule_delivery(self, user_state: UserState) -> Dict:
        """Schedule delivery for specific time"""
        from pangea_uber_direct import parse_delivery_time
        import pytz
        
        try:
            # Parse delivery time
            scheduled_datetime = parse_delivery_time(user_state.delivery_time)
            chicago_tz = pytz.timezone('America/Chicago')
            
            if scheduled_datetime.tzinfo is None:
                scheduled_datetime = chicago_tz.localize(scheduled_datetime)
            
            current_time = datetime.now(chicago_tz)
            delay_seconds = (scheduled_datetime - current_time).total_seconds()
            
            if delay_seconds <= 0:
                # Time has passed - trigger immediately
                return await self._trigger_delivery_now(user_state)
            
            # Update state
            user_state.stage = OrderStage.DELIVERY_SCHEDULED
            
            # Start background thread to trigger delivery
            def delayed_trigger():
                time.sleep(delay_seconds)
                # Re-get user state and trigger delivery
                import asyncio
                asyncio.run(self._trigger_delivery_now(user_state))
            
            thread = threading.Thread(target=delayed_trigger)
            thread.daemon = True
            thread.start()
            
            return {
                'status': 'delivery_scheduled',
                'scheduled_time': scheduled_datetime.strftime('%I:%M %p'),
                'delay_seconds': delay_seconds
            }
            
        except Exception as e:
            print(f"❌ Delivery scheduling error: {e}")
            return {'status': 'error', 'error': str(e)}
    
    def _schedule_delivery_notifications(self, delivery_data: Dict, delivery_result: Dict):
        """Schedule delayed delivery notifications"""
        def send_delayed_notifications():
            time.sleep(50)  # 50-second delay
            
            restaurant = delivery_data.get('restaurant')
            location = delivery_data.get('location')
            tracking_url = delivery_result.get('tracking_url', '')
            delivery_id = delivery_result.get('delivery_id', '')
            
            message = f"""🚚 Your {restaurant} delivery is on the way!

📍 Delivery to: {location}
📱 Track your order: {tracking_url}
📦 Delivery ID: {delivery_id[:8]}...

Your driver will contact you when they arrive!"""
            
            for member_phone in delivery_data.get('members', []):
                self.send_sms(member_phone, message)
        
        thread = threading.Thread(target=send_delayed_notifications)
        thread.daemon = True
        thread.start()

# Flask web server
app = Flask(__name__)
pangea_app = PangeaApp()

@app.route('/webhook/sms', methods=['POST'])
async def sms_webhook():
    """Handle incoming SMS messages"""
    try:
        from_number = request.form.get('From')
        message_body = request.form.get('Body')
        
        if not from_number or not message_body:
            return '', 400
        
        result = await pangea_app.handle_message(from_number, message_body)
        return '', 200
        
    except Exception as e:
        print(f"❌ Webhook error: {e}")
        return '', 500

@app.route('/status/<phone_number>', methods=['GET'])
async def user_status(phone_number):
    """Get user status for debugging"""
    try:
        user_state = await pangea_app.memory_manager.get_user_state(phone_number)
        return {
            'user_phone': phone_number,
            'stage': user_state.stage.value,
            'restaurant': user_state.restaurant,
            'location': user_state.location,
            'group_id': user_state.group_id,
            'missing_info': user_state.missing_info,
            'last_activity': user_state.last_activity.isoformat() if user_state.last_activity else None
        }, 200
    except Exception as e:
        return {'error': str(e)}, 500

if __name__ == "__main__":
    print("🍜 Starting Pangea Food Coordination System...")
    port = int(os.environ.get('PORT', 8000))
    app.run(host='0.0.0.0', port=port, debug=True)
