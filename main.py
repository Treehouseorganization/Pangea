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
        self.conversation_manager = ConversationManager(self.anthropic_llm, self.memory_manager, self.send_sms, self.db)
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
            
            # Update user state BEFORE executing actions
            if conversation_result.get('state_updates'):
                print(f"🔄 APPLYING STATE UPDATES: {conversation_result['state_updates']}")
                self._update_user_state(user_state, conversation_result['state_updates'])
                print(f"   After updates - Restaurant: {user_state.restaurant}, Location: {user_state.location}")
            
            # Execute any triggered actions
            actions = conversation_result.get('actions', [])
            print(f"⚡ EXECUTING {len(actions)} ACTIONS...")
            action_results = await self._execute_actions(actions, user_state)
            print(f"   📋 Action Results: {[r.get('status') for r in action_results]}")
            
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
        # Apply extracted data from action to user state if missing
        if action_data.get('restaurant') and not user_state.restaurant:
            user_state.restaurant = action_data['restaurant']
            print(f"         🔧 Applied restaurant from action data: {user_state.restaurant}")
        if action_data.get('location') and not user_state.location:
            user_state.location = action_data['location']
            print(f"         🔧 Applied location from action data: {user_state.location}")
        if action_data.get('delivery_time') and user_state.delivery_time == 'now':
            user_state.delivery_time = action_data['delivery_time']
            print(f"         🔧 Applied delivery_time from action data: {user_state.delivery_time}")
        
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
        
        # Update state - mark user as paid immediately when they request payment
        payment_time = datetime.now()
        user_state.payment_requested_at = payment_time
        user_state.payment_timestamp = payment_time  # Also set payment_timestamp to mark as paid
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
        
        # Save the updated user state before checking delivery trigger
        await self.memory_manager.save_user_state(user_state)
        
        # CRITICAL FIX: Also sync payment data to order_sessions collection for delivery trigger system
        try:
            order_session_data = {
                'user_phone': user_state.user_phone,
                'group_id': user_state.group_id,
                'restaurant': user_state.restaurant,
                'location': user_state.location,
                'delivery_time': user_state.delivery_time,
                'customer_name': user_state.customer_name,
                'order_number': user_state.order_number,
                'order_description': user_state.order_description,
                'payment_timestamp': payment_time,
                'payment_requested_at': payment_time,
                'order_stage': 'payment_pending',
                'group_size': user_state.group_size,
                'is_fake_match': user_state.is_fake_match
            }
            self.db.collection('order_sessions').document(user_state.user_phone).set(order_session_data, merge=True)
            print(f"            📊 Synced payment data to order_sessions collection")
        except Exception as e:
            print(f"            ⚠️ Failed to sync to order_sessions: {e}")
        
        # CRITICAL FIX: For direct invitations, prevent duplicate delivery creation
        # Check if this is a direct invitation group and if delivery already exists
        delivery_already_exists = await self._check_delivery_already_exists(user_state.group_id)
        if delivery_already_exists:
            print(f"            🚫 Delivery already exists for group {user_state.group_id}, skipping trigger")
            return {
                'status': 'payment_requested',
                'payment_link': payment_link,
                'delivery_skipped': True
            }
        
        # Check if delivery should be triggered
        should_trigger = await self._should_trigger_delivery_now(user_state)
        print(f"            🚚 Should trigger delivery now: {should_trigger}")
        if should_trigger:
            print(f"            🚚 Triggering delivery immediately...")
            result = await self._trigger_delivery_now(user_state)
            print(f"            ⏰ Scheduling notifications for 50 seconds...")
            self._schedule_delayed_notifications(user_state, result)
        
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
            
            # Get paid users count for sophisticated delivery logic
            group_members = await self.memory_manager.get_group_members(user_state.group_id)
            paid_users = [member for member in group_members if member.payment_requested_at is not None]
            total_users = len(group_members)
            
            print(f"            📊 Group status: {len(paid_users)}/{total_users} paid")
            
            if len(paid_users) == total_users:
                # Both users paid - proceed with delivery
                if is_immediate:
                    print(f"            ⚡ Both users paid - triggering immediate group delivery")
                    return await self._trigger_delivery_now(user_state)
                else:
                    # Both users paid - ALWAYS trigger delivery immediately (send tracking link 50s later)
                    print(f"            ⚡ Both users paid - triggering immediate group delivery (tracking in 50s)")
                    return await self._trigger_delivery_now(user_state)
            else:
                # Only one user paid so far
                if is_immediate:
                    print(f"            ⏳ Immediate order - waiting for other user to pay")
                    return {'status': 'waiting_for_group', 'message': 'Waiting for your group partner to pay'}
                else:
                    print(f"            ⏰ Scheduled order - setting up conditional delivery")
                    return await self._schedule_conditional_delivery(user_state, paid_users)
    
    async def _handle_cancel_order(self, user_state: UserState, action_data: Dict) -> Dict:
        """Handle order cancellation with group member notification and scheduled delivery cancellation"""
        
        # CRITICAL FIX: Mark group as cancelled in database to prevent scheduled delivery execution
        if user_state.group_id:
            try:
                print(f"🚫 CANCELLING GROUP: {user_state.group_id}")
                
                # Mark the group as cancelled in active_groups collection
                self.db.collection('active_groups').document(user_state.group_id).update({
                    'status': 'cancelled',
                    'cancelled_at': datetime.now(),
                    'cancelled_by': user_state.user_phone
                })
                
                print(f"✅ Marked group {user_state.group_id} as cancelled in database")
                
            except Exception as e:
                print(f"⚠️ Error marking group as cancelled: {e}")
        
        # If this was a group order, notify the other member they can continue solo
        if user_state.group_id and not user_state.is_fake_match:
            try:
                group_members = await self.memory_manager.get_group_members(user_state.group_id)
                other_members = [member for member in group_members if member.user_phone != user_state.user_phone]
                
                for other_member in other_members:
                    # Silently convert their order to a fake match (solo order)
                    other_member.is_fake_match = True
                    other_member.group_size = 1
                    other_member.group_id = f"solo_{other_member.user_phone}_{int(time.time())}"
                    await self.memory_manager.save_user_state(other_member)
                    
                    print(f"✅ Silently converted {other_member.user_phone} to solo order: {other_member.group_id}")
                    
            except Exception as e:
                print(f"⚠️ Error handling group cancellation: {e}")
        
        # Clear order state for canceling user
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
    
    def _is_direct_invitation_group(self, user_state: UserState) -> bool:
        """Check if user is in a direct invitation group"""
        if not user_state.group_id:
            return False
            
        try:
            group_doc = self.db.collection('active_groups').document(user_state.group_id).get()
            if group_doc.exists:
                group_data = group_doc.to_dict()
                return group_data.get('type') == 'direct_invitation'
        except Exception as e:
            print(f"   ⚠️ Error checking group type: {e}")
        
        return False

    def _calculate_missing_info(self, user_state: UserState) -> List[str]:
        """Calculate what information is still missing"""
        missing = []
        
        if not user_state.restaurant:
            missing.append('restaurant')
        if not user_state.location:
            missing.append('location')
        
        # For order collection stage
        if user_state.stage == OrderStage.COLLECTING_ORDER_INFO:
            # Check if this is a direct invitation group
            is_direct_invitation = self._is_direct_invitation_group(user_state)
            
            # For direct invitation groups, don't require order identifier
            if not is_direct_invitation:
                if not (user_state.order_number or user_state.customer_name):
                    missing.append('order_identifier')
            
            if not user_state.order_description:
                missing.append('order_description')
        
        return missing
    
    def _has_complete_order_info(self, user_state: UserState) -> bool:
        """Check if user has provided complete order information"""
        has_description = user_state.order_description
        has_restaurant = user_state.restaurant
        has_location = user_state.location
        
        # For direct invitation groups, only require description (no identifier needed)
        is_direct_invitation = self._is_direct_invitation_group(user_state)
        if is_direct_invitation:
            return all([has_description, has_restaurant, has_location])
        
        # For regular users, require identifier as before
        has_identifier = user_state.order_number or user_state.customer_name
        return all([has_identifier, has_description, has_restaurant, has_location])
    
    async def _check_delivery_already_exists(self, group_id: str) -> bool:
        """Check if a delivery already exists for this group to prevent duplicates"""
        if not group_id:
            return False
            
        try:
            # Check the deliveries collection for this group_id
            deliveries_query = self.db.collection('deliveries').where('group_id', '==', group_id)
            existing_deliveries = list(deliveries_query.stream())
            
            if existing_deliveries:
                print(f"            🔍 Found {len(existing_deliveries)} existing deliveries for group {group_id}")
                return True
                
            return False
            
        except Exception as e:
            print(f"            ❌ Error checking existing deliveries: {e}")
            return False  # If we can't check, allow the delivery to proceed
    
    async def _should_trigger_delivery_now(self, user_state: UserState) -> bool:
        """Determine if delivery should be triggered immediately"""
        # CRITICAL FIX: Always check current group status from database, not user state
        # This handles cases where user was upgraded from fake to real match
        group_id = user_state.group_id
        if not group_id:
            return False
            
        try:
            # Get current group info from database to check if it's still a fake match
            group_doc = self.db.collection('active_groups').document(group_id).get()
            if not group_doc.exists:
                # Fall back to user state logic
                if user_state.is_fake_match:
                    return user_state.delivery_time in ['now', 'asap', 'soon', 'immediately']
                else:
                    return await self._is_group_ready_for_delivery(group_id)
            
            group_data = group_doc.to_dict()
            current_is_fake_match = group_data.get('is_fake_match', False)
            current_group_size = group_data.get('group_size', 1)
            
            print(f"            📊 Current group status: is_fake_match={current_is_fake_match}, size={current_group_size}")
            
            if current_is_fake_match:
                # Still a solo order - only trigger if immediate
                return user_state.delivery_time in ['now', 'asap', 'soon', 'immediately']
            else:
                # ✅ FIX: For real group orders, trigger delivery when all members have paid (regardless of timing)
                # This enables the 50-second delay for scheduled group orders
                return await self._is_group_ready_for_delivery(group_id)
                
        except Exception as e:
            print(f"            ❌ Error checking group status: {e}")
            # Fall back to original logic
            if user_state.is_fake_match:
                return user_state.delivery_time in ['now', 'asap', 'soon', 'immediately']
            else:
                return await self._is_group_ready_for_delivery(group_id)
    
    async def _is_group_ready_for_delivery(self, group_id: str) -> bool:
        """Check if all group members have paid"""
        if not group_id:
            return False
        
        try:
            # Get all users in this group who have paid
            group_members = await self.memory_manager.get_group_members(group_id)
            paid_members = [
                member for member in group_members 
                if member.payment_requested_at is not None or member.payment_timestamp is not None
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
                
                # Don't schedule notifications here - they will be scheduled separately with delay
            
            return result
            
        except Exception as e:
            print(f"❌ Delivery trigger error: {e}")
            return {'status': 'error', 'error': str(e)}
    
    def _schedule_delayed_notifications(self, user_state: UserState, delivery_result: Dict):
        """Schedule delivery notifications with 50-second delay to simulate payment processing"""
        import threading
        
        print(f"         📱 SCHEDULING DELAYED NOTIFICATIONS:")
        print(f"            👥 Group ID: {user_state.group_id}")
        print(f"            ⏰ Adding 50-second delay before sending notifications")
        
        def delayed_notifications():
            """Send notifications after delay in background thread"""
            import time
            print(f"            🕒 Starting 50-second notification delay...")
            time.sleep(50)
            print(f"            📱 Delay complete - sending delivery notifications now!")
            
            try:
                # Build delivery data for notifications
                delivery_data = {
                    'restaurant': user_state.restaurant,
                    'location': user_state.location,
                    'group_id': user_state.group_id,
                    'members': [user_state.user_phone]  # Will be expanded to all group members
                }
                
                # Get all group members if it's not a fake match
                if not user_state.is_fake_match:
                    # Create a new event loop for async operations
                    import asyncio
                    loop = asyncio.new_event_loop()
                    asyncio.set_event_loop(loop)
                    try:
                        group_members = loop.run_until_complete(self.memory_manager.get_group_members(user_state.group_id))
                        delivery_data['members'] = [member.user_phone for member in group_members]
                    finally:
                        loop.close()
                
                # Send notifications using existing method
                self._send_delivery_notifications_now(delivery_data, delivery_result)
                
            except Exception as e:
                print(f"            ❌ Error in delayed notifications: {e}")
        
        # Start background thread for delayed notifications
        thread = threading.Thread(target=delayed_notifications)
        thread.daemon = False
        thread.start()
    
    def _send_delivery_notifications_now(self, delivery_data: Dict, delivery_result: Dict):
        """Send delivery notifications immediately"""
        restaurant = delivery_data.get('restaurant')
        location = delivery_data.get('location')
        tracking_url = delivery_result.get('tracking_url', '')
        delivery_id = delivery_result.get('delivery_id', '')
        
        message = f"""🚚 Your {restaurant} delivery is on the way!

📍 Delivery to: {location}
📱 Track your order: {tracking_url}
📦 Delivery ID: {delivery_id[:8]}...

Your driver will contact you when they arrive! 🎉"""
        
        # Send to all group members
        for user_phone in delivery_data.get('members', []):
            try:
                success = self.send_sms(user_phone, message)
                if success:
                    print(f"            ✅ Sent delivery notification to {user_phone}")
                else:
                    print(f"            ❌ Failed to send notification to {user_phone}")
            except Exception as e:
                print(f"            ❌ Failed to send notification to {user_phone}: {e}")
    
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
                print(f"⏰ Timer expired! Triggering scheduled delivery for {user_state.user_phone}")
                
                # Create new event loop for this thread
                import asyncio
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                
                try:
                    # Re-get fresh user state and validate payment before triggering
                    fresh_user_state = loop.run_until_complete(self.memory_manager.get_user_state(user_state.user_phone))
                    
                    # Check if user actually paid and didn't cancel
                    if fresh_user_state.stage == OrderStage.IDLE:
                        print(f"🚫 User {user_state.user_phone} cancelled order - skipping scheduled delivery")
                        return
                    
                    # CRITICAL FIX: Also check if group was cancelled in database
                    if fresh_user_state.group_id:
                        try:
                            group_doc = self.db.collection('active_groups').document(fresh_user_state.group_id).get()
                            if group_doc.exists:
                                group_data = group_doc.to_dict()
                                group_status = group_data.get('status', 'active')
                                
                                if group_status == 'cancelled':
                                    print(f"🚫 Group {fresh_user_state.group_id} was cancelled - skipping scheduled delivery")
                                    return
                            else:
                                print(f"🚫 Group {fresh_user_state.group_id} no longer exists - skipping scheduled delivery")
                                return
                                
                        except Exception as group_check_error:
                            print(f"❌ Error checking group status: {group_check_error} - proceeding with delivery")
                        
                    if not fresh_user_state.payment_requested_at and not fresh_user_state.payment_timestamp:
                        print(f"🚫 User {user_state.user_phone} never paid - skipping scheduled delivery")
                        return
                    
                    print(f"✅ User {user_state.user_phone} paid and didn't cancel - proceeding with delivery")
                    result = loop.run_until_complete(self._trigger_delivery_now(fresh_user_state))
                    print(f"✅ Scheduled delivery triggered successfully: {result}")
                except Exception as e:
                    print(f"❌ Scheduled delivery failed: {e}")
                finally:
                    loop.close()
            
            thread = threading.Thread(target=delayed_trigger)
            thread.daemon = False
            thread.start()
            
            return {
                'status': 'delivery_scheduled',
                'scheduled_time': scheduled_datetime.strftime('%I:%M %p'),
                'delay_seconds': delay_seconds
            }
            
        except Exception as e:
            print(f"❌ Delivery scheduling error: {e}")
            return {'status': 'error', 'error': str(e)}
    
    async def _schedule_conditional_delivery(self, user_state: UserState, paid_users: List) -> Dict:
        """Schedule delivery but trigger solo if other user doesn't pay in time"""
        
        from pangea_uber_direct import parse_delivery_time
        import pytz
        import threading
        import time
        
        try:
            # Parse delivery time
            scheduled_datetime = parse_delivery_time(user_state.delivery_time)
            chicago_tz = pytz.timezone('America/Chicago')
            
            if scheduled_datetime.tzinfo is None:
                scheduled_datetime = chicago_tz.localize(scheduled_datetime)
            
            current_time = datetime.now(chicago_tz)
            delay_seconds = (scheduled_datetime - current_time).total_seconds()
            
            if delay_seconds <= 0:
                # Time has passed - trigger solo for paid users only
                print(f"⚡ Scheduled time passed - triggering solo delivery for paid users")
                return await self._trigger_delivery_now(user_state)
            
            print(f"⏰ Setting up conditional delivery check in {delay_seconds} seconds")
            
            # Update state
            user_state.stage = OrderStage.DELIVERY_SCHEDULED
            
            def conditional_trigger():
                time.sleep(delay_seconds)
                print(f"⏰ Checking conditional delivery for group {user_state.group_id}")
                
                # Create new event loop for this thread
                import asyncio
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                
                try:
                    # CRITICAL FIX: Check if group was cancelled before proceeding
                    try:
                        group_doc = self.db.collection('active_groups').document(user_state.group_id).get()
                        if group_doc.exists:
                            group_data = group_doc.to_dict()
                            group_status = group_data.get('status', 'active')
                            
                            if group_status == 'cancelled':
                                print(f"🚫 Group {user_state.group_id} was cancelled - skipping conditional delivery")
                                return
                        else:
                            print(f"🚫 Group {user_state.group_id} no longer exists - skipping conditional delivery")
                            return
                            
                    except Exception as group_check_error:
                        print(f"❌ Error checking group status: {group_check_error} - proceeding with delivery")
                    
                    # Re-check who has paid
                    fresh_group_members = loop.run_until_complete(
                        self.memory_manager.get_group_members(user_state.group_id)
                    )
                    current_paid_users = [
                        member for member in fresh_group_members 
                        if member.payment_requested_at is not None
                    ]
                    total_users = len(fresh_group_members)
                    
                    if len(current_paid_users) == total_users and len(current_paid_users) > 0:
                        # Both paid - trigger group delivery
                        print(f"✅ Both users paid - triggering group delivery")
                        # Use first paid user as representative
                        loop.run_until_complete(self._trigger_delivery_now(current_paid_users[0]))
                    elif len(current_paid_users) > 0:
                        # Only some paid - trigger solo delivery for paid users
                        print(f"⚠️ Only {len(current_paid_users)} users paid - triggering solo delivery")
                        
                        # Send missed delivery messages to unpaid users
                        all_users = [member.user_phone for member in fresh_group_members]
                        paid_phones = [member.user_phone for member in current_paid_users]
                        unpaid_users = [user for user in all_users if user not in paid_phones]
                        
                        for unpaid_user in unpaid_users:
                            missed_message = "⏰ You missed your scheduled delivery because you didn't complete your payment in time. Your group partner's order was delivered without you. Reply 'ORDER' to start a new order! 🍴"
                            print(f"📱 Sending missed delivery message to {unpaid_user}")
                            self.send_sms(unpaid_user, missed_message)
                        
                        # Trigger delivery for paid users only
                        loop.run_until_complete(self._trigger_delivery_now(current_paid_users[0]))
                    else:
                        # Nobody paid - no delivery
                        print(f"❌ No users paid - no delivery triggered")
                
                except Exception as e:
                    print(f"❌ Conditional delivery failed: {e}")
                finally:
                    loop.close()
            
            thread = threading.Thread(target=conditional_trigger)
            thread.daemon = False
            thread.start()
            
            return {
                'status': 'conditional_scheduled',
                'scheduled_time': scheduled_datetime.strftime('%I:%M %p'),
                'message': f'Delivery will trigger at {scheduled_datetime.strftime("%I:%M %p")} for whoever has paid'
            }
            
        except Exception as e:
            print(f"❌ Conditional delivery scheduling error: {e}")
            return {'status': 'error', 'error': str(e)}
    
    def _schedule_delivery_notifications(self, delivery_data: Dict, delivery_result: Dict):
        """Schedule delayed delivery notifications"""
        print(f"📱 Scheduling delivery notifications in 50 seconds...")
        print(f"   📦 Delivery ID: {delivery_result.get('delivery_id', 'N/A')}")
        print(f"   👥 Recipients: {delivery_data.get('members', [])}")
        
        def send_delayed_notifications():
            try:
                print(f"⏱️ Starting 50-second notification delay...")
                time.sleep(50)  # 50-second delay
                
                print(f"📬 Sending delivery notifications now!")
                
                restaurant = delivery_data.get('restaurant')
                location = delivery_data.get('location')
                tracking_url = delivery_result.get('tracking_url', '')
                delivery_id = delivery_result.get('delivery_id', '')
                
                message = f"""🚚 Your {restaurant} delivery is on the way!

📍 Delivery to: {location}
📱 Track your order: {tracking_url}
📦 Delivery ID: {delivery_id[:8]}...

Your driver will contact you when they arrive! 🎉"""
                
                for member_phone in delivery_data.get('members', []):
                    try:
                        success = self.send_sms(member_phone, message)
                        if success:
                            print(f"✅ Sent delivery notification to {member_phone}")
                        else:
                            print(f"❌ Failed to send delivery notification to {member_phone}")
                    except Exception as e:
                        print(f"❌ Error sending notification to {member_phone}: {e}")
                        
            except Exception as e:
                print(f"❌ Error in delayed notifications: {e}")
        
        thread = threading.Thread(target=send_delayed_notifications)
        thread.daemon = False
        thread.start()
        print(f"⏰ Scheduled delivery notifications for 50 seconds")

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
