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
from models import UserState, OrderStage
from conversation_manager import ConversationManager
from order_state_machine import OrderStateMachine
from delivery_coordinator import DeliveryCoordinator
from matching_engine import MatchingEngine  # Keep existing matching logic
from memory_manager import MemoryManager

load_dotenv()


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
            print(f"   📱 SENDING SMS:")
            print(f"      📞 To: {phone_number}")
            print(f"      💬 Length: {len(message)} chars")
            
            result = self.twilio_client.messages.create(
                body=message,
                from_=os.getenv('TWILIO_PHONE_NUMBER'),
                to=phone_number
            )
            
            print(f"      ✅ SMS SENT:")
            print(f"         SID: {result.sid}")
            print(f"         Status: {result.status}")
            return True
            
        except Exception as e:
            print(f"      ❌ SMS FAILED:")
            print(f"         Error: {str(e)}")
            print(f"         Type: {type(e).__name__}")
            return False
    
    async def handle_message(self, user_phone: str, message: str) -> Dict:
        """Main message handling with proper state management"""
        print(f"📱 MAIN MESSAGE HANDLER:")
        print(f"   📞 User: {user_phone}")
        print(f"   💬 Message: '{message}'")
        print(f"   🕒 Start Time: {datetime.now().isoformat()}")
        
        try:
            # Get or create user state
            print(f"🧠 Retrieving user state for {user_phone}...")
            user_state = await self.memory_manager.get_user_state(user_phone)
            print(f"   📊 Current Stage: {user_state.stage.value}")
            print(f"   🏪 Restaurant: {user_state.restaurant or 'None'}")
            print(f"   📍 Location: {user_state.location or 'None'}")
            print(f"   👥 Group ID: {user_state.group_id or 'None'}")
            print(f"   🎭 Is Fake Match: {user_state.is_fake_match}")
            
            # Update conversation history
            user_state.conversation_history.append({
                'message': message,
                'timestamp': datetime.now().isoformat(),
                'type': 'user'
            })
            user_state.last_activity = datetime.now()
            
            # Process message through conversation manager
            print(f"💭 CONVERSATION PROCESSING...")
            conversation_result = await self.conversation_manager.process_message(
                message, user_state
            )
            print(f"   ✅ Analysis Complete: {conversation_result.get('analysis', {}).get('primary_intent', 'unknown')}")
            print(f"   🎯 Actions to Execute: {[a.get('type') for a in conversation_result.get('actions', [])]}")
            print(f"   🔄 State Updates: {list(conversation_result.get('state_updates', {}).keys())}")
            
            # Execute any triggered actions
            actions = conversation_result.get('actions', [])
            print(f"⚡ EXECUTING {len(actions)} ACTIONS...")
            action_results = await self._execute_actions(actions, user_state)
            print(f"   📋 Action Results: {[r.get('status') for r in action_results]}")
            
            # Update user state based on results
            if conversation_result.get('state_updates'):
                self._update_user_state(user_state, conversation_result['state_updates'])
            
            # Send response if provided
            response_message = conversation_result.get('response')
            if response_message:
                print(f"📤 SENDING RESPONSE:")
                print(f"   💌 Message: '{response_message[:100]}{'...' if len(response_message) > 100 else ''}'")
                
                # Add to conversation history
                user_state.conversation_history.append({
                    'message': response_message,
                    'timestamp': datetime.now().isoformat(),
                    'type': 'assistant'
                })
                
                # Send SMS
                sms_success = self.send_sms(user_phone, response_message)
                print(f"   📱 SMS Status: {'✅ Sent' if sms_success else '❌ Failed'}")
            
            # Save updated state
            await self.memory_manager.save_user_state(user_state)
            
            result = {
                'status': 'success',
                'stage': user_state.stage.value,
                'actions_taken': [r.get('type', 'unknown') for r in action_results],
                'response_sent': response_message,
                'payment_amount': user_state.payment_amount,
                'restaurant': user_state.restaurant,
                'location': user_state.location
            }
            
            print(f"✅ MESSAGE HANDLING COMPLETE:")
            print(f"   🎯 Final Stage: {user_state.stage.value}")
            print(f"   ⚡ Actions Executed: {len(action_results)}")
            print(f"   📤 Response Sent: {'Yes' if response_message else 'No'}")
            
            return result
            
        except Exception as e:
            print(f"❌ MESSAGE HANDLING ERROR:")
            print(f"   🚨 Exception: {str(e)}")
            print(f"   📋 Exception Type: {type(e).__name__}")
            import traceback
            print(f"   📚 Traceback: {traceback.format_exc()}")
            
            # Send error response
            error_msg = "Sorry, I had a technical issue. Can you try again?"
            error_sent = self.send_sms(user_phone, error_msg)
            
            return {
                'status': 'error',
                'error': str(e),
                'debug_info': traceback.format_exc(),
                'response_sent': error_sent
            }
    
    async def _execute_actions(self, actions: List[Dict], user_state: UserState) -> List[Dict]:
        """Execute triggered actions based on conversation analysis"""
        results = []
        
        for i, action in enumerate(actions, 1):
            action_type = action.get('type')
            action_data = action.get('data', {})
            
            print(f"   ⚡ ACTION {i}/{len(actions)}: {action_type}")
            print(f"      📋 Data: {action_data}")
            
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
                
                print(f"      ✅ Result: {result.get('status')}")
                results.append(result)
                
            except Exception as e:
                print(f"      ❌ ACTION FAILED:")
                print(f"         🚨 Error: {str(e)}")
                import traceback
                print(f"         📚 Traceback: {traceback.format_exc()}")
                results.append({
                    'status': 'error',
                    'type': action_type,
                    'error': str(e)
                })
        
        return results
    
    async def _handle_find_matches(self, user_state: UserState, action_data: Dict) -> Dict:
        """Handle finding matches using existing matching engine"""
        print(f"         🔍 FINDING MATCHES:")
        print(f"            🏪 Restaurant: {user_state.restaurant}")
        print(f"            📍 Location: {user_state.location}")
        print(f"            🕒 Time: {user_state.delivery_time}")
        
        if not all([user_state.restaurant, user_state.location]):
            print(f"            ⚠️ Missing info: {user_state.missing_info}")
            return {'status': 'missing_info', 'missing': user_state.missing_info}
        
        # Use existing matching engine logic
        print(f"            🔍 Calling matching engine...")
        match_result = self.matching_engine.find_compatible_matches(
            user_state.user_phone,
            user_state.restaurant, 
            user_state.location,
            user_state.delivery_time
        )
        
        print(f"            🎯 Match Result:")
        print(f"               Has Real Match: {match_result.get('has_real_match', False)}")
        print(f"               Matches Found: {len(match_result.get('matches', []))}")
        print(f"               Is Silent Upgrade: {match_result.get('is_silent_upgrade', False)}")
        
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
                
                print(f"            ✅ SILENT UPGRADE MATCH:")
                print(f"               Group ID: {group_id}")
                print(f"               Partner: {best_match['user_phone']}")
                
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
                
                print(f"            ✅ REAL MATCH FOUND:")
                print(f"               Group ID: {group_id}")
                print(f"               Partner: {best_match['user_phone']}")
                print(f"               Sending notification to partner...")
                
                # Notify matched user
                match_message = f"""Great news! Another student wants {user_state.restaurant} at {user_state.location} too!

**Group Confirmed (2 people)**
Your share: $4.50 each (vs $8+ solo)

**Next steps:**
1. Order from {user_state.restaurant} (choose PICKUP, not delivery)
2. Come back with your order number/name AND what you ordered
3. Text "PAY" when ready

Time to order!"""
                
                partner_sms_sent = self.send_sms(best_match['user_phone'], match_message)
                print(f"               Partner SMS: {'✅ Sent' if partner_sms_sent else '❌ Failed'}")
                
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
            
            print(f"            🎭 FAKE MATCH CREATED:")
            print(f"               Group ID: {group_id}")
            print(f"               Solo order disguised as group")
            
            return {
                'status': 'fake_match_created',
                'group_id': group_id
            }
    
    async def _handle_request_payment(self, user_state: UserState, action_data: Dict) -> Dict:
        """Handle payment request"""
        print(f"         💳 PAYMENT REQUEST:")
        print(f"            📊 Order Complete: {self._has_complete_order_info(user_state)}")
        print(f"            💰 Payment Amount: {user_state.payment_amount}")
        print(f"            👥 Group Size: {user_state.group_size}")
        
        if not self._has_complete_order_info(user_state):
            print(f"            ⚠️ Incomplete order, missing: {user_state.missing_info}")
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
        
        print(f"            🔗 Generated payment link: {payment_link}")
        
        # Send payment message
        payment_message = f"""💳 Payment for {user_state.restaurant}

Your share: {user_state.payment_amount}

Click here to pay:
{payment_link}

After payment, I'll coordinate your delivery!"""
        
        payment_sms_sent = self.send_sms(user_state.user_phone, payment_message)
        print(f"            📱 Payment SMS: {'✅ Sent' if payment_sms_sent else '❌ Failed'}")
        
        # Check if delivery should be triggered
        should_trigger = await self._should_trigger_delivery_now(user_state)
        print(f"            🚚 Should trigger delivery now: {should_trigger}")
        if should_trigger:
            print(f"            ⚡ Triggering delivery immediately...")
            await self._handle_trigger_delivery(user_state, {})
        
        return {
            'status': 'payment_requested',
            'payment_link': payment_link
        }
    
    async def _handle_trigger_delivery(self, user_state: UserState, action_data: Dict) -> Dict:
        """Handle delivery triggering with proper timing logic"""
        print(f"         🚚 DELIVERY TRIGGER:")
        print(f"            🕒 Delivery Time: {user_state.delivery_time}")
        print(f"            🎭 Is Fake Match: {user_state.is_fake_match}")
        print(f"            👥 Group ID: {user_state.group_id}")
        
        # Check delivery timing rules
        is_immediate = user_state.delivery_time in ['now', 'asap', 'soon', 'immediately']
        print(f"            ⏱️ Is Immediate: {is_immediate}")
        
        if user_state.is_fake_match:
            print(f"            👤 Solo order logic")
            if is_immediate:
                print(f"            ⚡ Immediate solo delivery - triggering now")
                return await self._trigger_delivery_now(user_state)
            else:
                print(f"            🗓️ Scheduled solo delivery - scheduling for later")
                return await self._schedule_delivery(user_state)
        else:
            print(f"            👥 Group order logic")
            group_ready = await self._is_group_ready_for_delivery(user_state.group_id)
            print(f"            📋 Group Ready: {group_ready}")
            
            if group_ready and is_immediate:
                print(f"            ⚡ Immediate group delivery - triggering now")
                return await self._trigger_delivery_now(user_state)
            elif group_ready and not is_immediate:
                print(f"            🗓️ Scheduled group delivery - scheduling for later")
                return await self._schedule_delivery(user_state)
            else:
                print(f"            ⏳ Waiting for other group members")
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

# Note: Flask routes moved to app.py to avoid conflicts
# This file now only contains the PangeaApp class for import

if __name__ == "__main__":
    print("🍜 Starting Pangea Food Coordination System...")
    print("⚠️  Please use 'python app.py' instead - this standalone mode is deprecated")
    
    # Deprecated standalone Flask server
    from flask import Flask
    app = Flask(__name__)
    pangea_app = PangeaApp()
    
    @app.route('/webhook/sms', methods=['POST'])
    async def sms_webhook():
        """Handle incoming SMS messages"""
        try:
            from flask import request
            from_number = request.form.get('From')
            message_body = request.form.get('Body')
            
            if not from_number or not message_body:
                return '', 400
            
            result = await pangea_app.handle_message(from_number, message_body)
            return '', 200
            
        except Exception as e:
            print(f"❌ Webhook error: {e}")
            return '', 500
    
    port = int(os.environ.get('PORT', 8000))
    app.run(host='0.0.0.0', port=port, debug=True)
