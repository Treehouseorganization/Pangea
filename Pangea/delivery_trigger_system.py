# delivery_trigger_system.py
"""
Bulletproof delivery trigger system following exact rules:
- 2-person groups max
- Fake matches (solo orders) 
- Delivery triggers only when user texts "PAY"
- Scheduled vs immediate delivery handling
- Solo delivery if only one person pays before scheduled time
"""

from datetime import datetime, timedelta
from typing import Dict, List, Optional
from langchain_core.tools import tool
import threading
import time

class DeliveryTriggerSystem:
    """Manages delivery triggering with exact business rules"""
    
    def __init__(self, db, session_manager, send_message_func=None):
        self.db = db
        self.session_manager = session_manager
        self.send_friendly_message = send_message_func
    
    def handle_user_payment(self, user_phone: str) -> Dict:
        """Handle when user texts PAY - core delivery trigger logic"""
        
        print(f"💳 Processing payment for {user_phone}")
        
        try:
            # Get user's context and order session
            context = self.session_manager.get_user_context(user_phone)
            
            if not context.active_order_session:
                return {
                    'status': 'error',
                    'message': 'No active order session found'
                }
            
            order_session = context.active_order_session
            group_id = order_session.get('group_id')
            restaurant = order_session.get('restaurant')
            group_size = order_session.get('group_size', 1)
            
            # Mark user as paid - sync BOTH payment tracking fields
            import pytz
            chicago_tz = pytz.timezone('America/Chicago')
            now = datetime.now(chicago_tz)
            order_session['payment_timestamp'] = now  # Pangea system field
            order_session['payment_requested_at'] = now  # Main system field  
            order_session['order_stage'] = 'paid'
            
            # Update both session manager and order processor
            context.active_order_session = order_session
            self.session_manager.update_user_context(context)
            
            # Also update order_sessions collection for compatibility
            try:
                from pangea_order_processor import update_order_session
                update_order_session(user_phone, order_session)
            except ImportError:
                # Fallback: update Firestore directly
                self.db.collection('order_sessions').document(user_phone).set(order_session, merge=True)
            
            print(f"✅ Marked {user_phone} as paid for group {group_id}")
            
            # Get delivery time from original food request
            delivery_time = 'now'
            if context.current_food_request:
                delivery_time = context.current_food_request.get('delivery_time', 'now')
            
            # Apply delivery trigger rules
            if group_size == 1:
                # Solo order (fake match) - trigger immediately or schedule
                return self._handle_solo_order_payment(user_phone, group_id, delivery_time, order_session)
            
            elif group_size == 2:
                # Real 2-person group - check if both paid
                return self._handle_group_order_payment(user_phone, group_id, delivery_time, order_session)
            
            else:
                return {
                    'status': 'error', 
                    'message': f'Invalid group size: {group_size}'
                }
                
        except Exception as e:
            print(f"❌ Payment handling error: {e}")
            return {
                'status': 'error',
                'message': str(e)
            }
    
    def _get_intelligent_group_time(self, delivery_times: List[tuple]) -> str:
        """Use Claude AI to determine optimal delivery time for group"""
        
        try:
            from langchain_anthropic import ChatAnthropic
            
            # Initialize Claude (you may need to adjust this based on your setup)
            llm = ChatAnthropic(
                model="claude-3-5-sonnet-20241022",
                temperature=0.1,
                max_tokens=500
            )
            
            # Prepare time preferences for analysis
            time_preferences = []
            for user_phone, time_pref in delivery_times:
                time_preferences.append(f"User {user_phone[-4:]}: wants delivery '{time_pref}'")
            
            prompt = f"""You are helping coordinate a group food delivery. Analyze these delivery time preferences and suggest the optimal time that works best for everyone:

{chr(10).join(time_preferences)}

Rules:
1. If times are compatible (within 30 minutes), choose a time that works for both
2. For time ranges like "between 1:40pm and 2:00pm", consider the MIDDLE of the range, not just the start
3. Priority: exact matches > overlapping windows > closest times
4. Be smart about time flexibility ("around 2pm" = 1:45-2:15pm window)

Return ONLY the optimal time in a simple format like "2:00 PM" or "1:50 PM"."""
            
            response = llm.invoke([{"role": "user", "content": prompt}])
            optimal_time = response.content.strip()
            
            # Clean up response to get just the time
            import re
            time_match = re.search(r'\d{1,2}:\d{2}\s*(AM|PM|am|pm)', optimal_time)
            if time_match:
                return time_match.group(0)
            
            # Fallback to first user's time if Claude response is unclear
            return delivery_times[0][1] if delivery_times else 'now'
            
        except Exception as e:
            print(f"❌ Error getting intelligent group time: {e}")
            # Fallback: use first user's time
            return delivery_times[0][1] if delivery_times else 'now'
    
    def _handle_solo_order_payment(self, user_phone: str, group_id: str, delivery_time: str, order_session: Dict) -> Dict:
        """Handle payment for solo order (fake match)"""
        
        print(f"🎭 Handling solo order payment: {group_id}")
        
        if delivery_time == 'now' or delivery_time in ['asap', 'soon', 'immediately']:
            # Immediate delivery - trigger right away
            print(f"🚚 Triggering immediate solo delivery")
            return self._trigger_delivery_now(group_id, [user_phone], order_session)
        
        else:
            # Scheduled delivery - set up timer
            print(f"⏰ Scheduling solo delivery for {delivery_time}")
            return self._schedule_delivery(group_id, [user_phone], delivery_time, order_session)
    
    def _handle_group_order_payment(self, user_phone: str, group_id: str, delivery_time: str, order_session: Dict) -> Dict:
        """Handle payment for 2-person group"""
        
        print(f"👥 Handling group order payment: {group_id}")
        
        # Get all users in this group who have paid
        paid_users = self._get_paid_users_in_group(group_id)
        total_users = self._get_total_users_in_group(group_id)
        all_users = self._get_all_users_in_group(group_id)
        
        print(f"📊 Group status: {len(paid_users)}/{total_users} paid")
        print(f"🔍 Debug - All users in group: {all_users}")
        print(f"🔍 Debug - Paid users: {paid_users}")
        print(f"🔍 Debug - Expected group size should be 2 for real matches")
        
        if len(paid_users) == total_users:
            # Both users paid - ALWAYS trigger delivery immediately regardless of scheduled time
            print(f"🚚 Both users paid - triggering immediate group delivery (ignoring scheduled time)")
            return self._trigger_delivery_now(group_id, paid_users, order_session)
        
        else:
            # Only one user paid so far
            print(f"⏳ Only {len(paid_users)}/{total_users} paid - waiting for other user")
            return {
                'status': 'waiting',
                'message': 'Waiting for your group partner to pay'
            }
    
    def _get_paid_users_in_group(self, group_id: str) -> List[str]:
        """Get list of users who have paid in this group"""
        
        try:
            paid_users = []
            
            # Check both payment_timestamp (Pangea system) and payment_requested_at (Main system)
            order_sessions = self.db.collection('order_sessions')\
                .where('group_id', '==', group_id)\
                .get()
            
            for session_doc in order_sessions:
                session_data = session_doc.to_dict()
                user_phone = session_data.get('user_phone')
                payment_timestamp = session_data.get('payment_timestamp')
                payment_requested_at = session_data.get('payment_requested_at')
                
                # Consider user paid if EITHER field is set (fixes sync issue)
                if payment_timestamp or payment_requested_at:
                    paid_users.append(user_phone)
                    print(f"   ✅ {user_phone} has paid")
                else:
                    print(f"   ⏳ {user_phone} has not paid yet")
            
            return paid_users
            
        except Exception as e:
            print(f"❌ Error getting paid users: {e}")
            return []
    
    def _get_all_users_in_group(self, group_id: str) -> List[str]:
        """Get list of all users in this group (paid and unpaid)"""
        
        try:
            all_users = []
            
            # Check order_sessions collection
            order_sessions = self.db.collection('order_sessions')\
                .where('group_id', '==', group_id)\
                .get()
            
            for session_doc in order_sessions:
                session_data = session_doc.to_dict()
                user_phone = session_data.get('user_phone')
                if user_phone:
                    all_users.append(user_phone)
            
            return all_users
            
        except Exception as e:
            print(f"❌ Error getting all users: {e}")
            return []
    
    def _get_total_users_in_group(self, group_id: str) -> int:
        """Get total number of users in group"""
        
        try:
            order_sessions = self.db.collection('order_sessions')\
                .where('group_id', '==', group_id)\
                .get()
            
            return len(order_sessions)
            
        except Exception as e:
            print(f"❌ Error getting total users: {e}")
            return 0
    
    def _calculate_group_delivery_time(self, group_id: str, paid_users: List[str]) -> str:
        """Calculate optimal delivery time for group using Claude AI"""
        
        try:
            delivery_times = []
            
            # Get delivery time preferences from each user
            for user_phone in paid_users:
                try:
                    context = self.session_manager.get_user_context(user_phone)
                    if context.current_food_request:
                        user_delivery_time = context.current_food_request.get('delivery_time', 'now')
                        delivery_times.append((user_phone, user_delivery_time))
                        print(f"   📋 {user_phone} wants delivery at: {user_delivery_time}")
                except Exception as e:
                    print(f"⚠️ Could not get delivery time for {user_phone}: {e}")
                    delivery_times.append((user_phone, 'now'))
            
            # If any user wants immediate delivery, deliver immediately
            if any(time in ['now', 'asap', 'soon', 'immediately'] for _, time in delivery_times):
                print(f"⚡ At least one user wants immediate delivery - choosing 'now'")
                return 'now'
            
            # Use Claude AI to find optimal time for group
            if len(delivery_times) >= 2:
                optimal_time = self._get_intelligent_group_time(delivery_times)
                print(f"🧠 Claude AI selected optimal group time: {optimal_time}")
                return optimal_time
            
            # Single user fallback
            if delivery_times:
                chosen_time = delivery_times[0][1]
                print(f"🎯 Single user time: {chosen_time}")
                return chosen_time
            
            # Fallback
            return 'now'
            
        except Exception as e:
            print(f"❌ Error calculating group delivery time: {e}")
            return 'now'
    
    def _delivery_already_triggered(self, group_id: str) -> bool:
        """Check if delivery has already been triggered for this group"""
        
        try:
            # Check if any user in the group has delivery_triggered = True
            order_sessions = self.db.collection('order_sessions')\
                .where('group_id', '==', group_id)\
                .get()
            
            for session_doc in order_sessions:
                session_data = session_doc.to_dict()
                if session_data.get('delivery_triggered', False):
                    return True
            
            return False
            
        except Exception as e:
            print(f"❌ Error checking delivery status: {e}")
            return False
    
    def _trigger_delivery_now(self, group_id: str, paid_users: List[str], order_session: Dict) -> Dict:
        """Trigger delivery immediately"""
        
        try:
            print(f"🚀 Triggering delivery now for group: {group_id}")
            print(f"👥 Paid users: {paid_users}")
            
            # Check if delivery has already been triggered for this group
            if self._delivery_already_triggered(group_id):
                print(f"⚠️ Delivery already triggered for group {group_id} - skipping duplicate")
                return {
                    'status': 'already_triggered',
                    'message': 'Delivery already triggered for this group'
                }
            
            # Build delivery data
            delivery_data = self._build_delivery_data(group_id, paid_users, order_session)
            print(f"📦 Built delivery data: {delivery_data}")
            
            # Create delivery via Uber Direct
            print(f"📞 Calling Uber Direct API...")
            from pangea_uber_direct import create_group_delivery
            result = create_group_delivery(delivery_data)
            
            print(f"🔄 Uber Direct API response: {result}")
            
            if result.get('success'):
                delivery_id = result.get('delivery_id')
                tracking_url = result.get('tracking_url')
                
                print(f"✅ Delivery created successfully!")
                print(f"📋 Delivery ID: {delivery_id}")
                print(f"🔗 Tracking URL: {tracking_url}")
                
                # Update all paid users' sessions
                print(f"📝 Updating user sessions...")
                for user_phone in paid_users:
                    print(f"   ✏️ Updating session for {user_phone}")
                    self._mark_delivery_triggered(user_phone, delivery_id, tracking_url)
                
                # Send notifications after delay
                print(f"📱 Scheduling delivery notifications...")
                self._schedule_delivery_notifications(delivery_data, result)
                
                print(f"🎉 Delivery trigger completed successfully!")
                
                return {
                    'status': 'success',
                    'delivery_id': delivery_id,
                    'message': 'Delivery triggered successfully'
                }
            else:
                print(f"❌ Delivery creation failed!")
                print(f"💥 Failure details: {result}")
                return {
                    'status': 'error',
                    'message': 'Failed to create delivery'
                }
                
        except Exception as e:
            print(f"❌ Delivery trigger error: {e}")
            return {
                'status': 'error',
                'message': str(e)
            }
    
    def _schedule_delivery(self, group_id: str, paid_users: List[str], delivery_time: str, order_session: Dict) -> Dict:
        """Schedule delivery for specific time"""
        
        try:
            # Parse delivery time
            from pangea_uber_direct import parse_delivery_time
            scheduled_datetime = parse_delivery_time(delivery_time)
            
            # Calculate delay
            import pytz
            chicago_tz = pytz.timezone('America/Chicago')
            
            if scheduled_datetime.tzinfo is None:
                scheduled_datetime = chicago_tz.localize(scheduled_datetime)
            
            current_time = datetime.now(chicago_tz)
            delay_seconds = (scheduled_datetime - current_time).total_seconds()
            
            if delay_seconds <= 0:
                # Time has passed - trigger immediately
                print(f"⚡ Scheduled time has passed - triggering immediately")
                return self._trigger_delivery_now(group_id, paid_users, order_session)
            
            print(f"⏰ Scheduling delivery in {delay_seconds} seconds ({scheduled_datetime.strftime('%I:%M %p')})")
            print(f"📅 Scheduled for: {scheduled_datetime}")
            print(f"👥 Users for scheduled delivery: {paid_users}")
            
            # Start background thread to trigger delivery
            def delayed_trigger():
                print(f"⏰ Timer started for {delay_seconds} seconds...")
                time.sleep(delay_seconds)
                print(f"🔔 Timer expired! Triggering scheduled delivery for {group_id}")
                print(f"👥 Triggering for users: {paid_users}")
                self._trigger_delivery_now(group_id, paid_users, order_session)
            
            thread = threading.Thread(target=delayed_trigger)
            thread.daemon = True
            thread.start()
            
            return {
                'status': 'scheduled',
                'scheduled_time': scheduled_datetime.strftime('%I:%M %p'),
                'message': f'Delivery scheduled for {scheduled_datetime.strftime("%I:%M %p")}'
            }
            
        except Exception as e:
            print(f"❌ Scheduling error: {e}")
            return {
                'status': 'error',
                'message': str(e)
            }
    
    def _schedule_conditional_delivery(self, group_id: str, paid_users: List[str], delivery_time: str, order_session: Dict) -> Dict:
        """Schedule delivery but trigger solo if other user doesn't pay in time"""
        
        try:
            from pangea_uber_direct import parse_delivery_time
            scheduled_datetime = parse_delivery_time(delivery_time)
            
            # Calculate delay
            import pytz
            chicago_tz = pytz.timezone('America/Chicago')
            
            if scheduled_datetime.tzinfo is None:
                scheduled_datetime = chicago_tz.localize(scheduled_datetime)
            
            current_time = datetime.now(chicago_tz)
            delay_seconds = (scheduled_datetime - current_time).total_seconds()
            
            if delay_seconds <= 0:
                # Time has passed - trigger solo for paid users only
                print(f"⚡ Scheduled time passed - triggering solo delivery for paid users")
                return self._trigger_delivery_now(group_id, paid_users, order_session)
            
            print(f"⏰ Setting up conditional delivery check in {delay_seconds} seconds")
            
            def conditional_trigger():
                time.sleep(delay_seconds)
                print(f"⏰ Checking conditional delivery for {group_id}")
                
                # Re-check who has paid
                current_paid_users = self._get_paid_users_in_group(group_id)
                total_users = self._get_total_users_in_group(group_id)
                
                if len(current_paid_users) == total_users:
                    # Both paid - trigger group delivery
                    print(f"✅ Both users paid - triggering group delivery")
                    self._trigger_delivery_now(group_id, current_paid_users, order_session)
                elif len(current_paid_users) > 0:
                    # Only some paid - trigger solo delivery for paid users and send missed delivery message to unpaid users
                    print(f"⚠️ Only {len(current_paid_users)} users paid - triggering solo delivery")
                    
                    # Get all users in group and identify unpaid users
                    all_users = self._get_all_users_in_group(group_id)
                    unpaid_users = [user for user in all_users if user not in current_paid_users]
                    
                    # Send missed delivery messages to unpaid users
                    if unpaid_users and self.send_friendly_message:
                        for unpaid_user in unpaid_users:
                            missed_message = "⏰ You missed your scheduled delivery because you didn't complete your payment in time. Your group partner's order was delivered without you. Reply 'ORDER' to start a new order! 🍴"
                            print(f"📱 Sending missed delivery message to {unpaid_user}")
                            self.send_friendly_message(unpaid_user, missed_message)
                    
                    # Trigger delivery for paid users only
                    self._trigger_delivery_now(group_id, current_paid_users, order_session)
                else:
                    # Nobody paid - no delivery
                    print(f"❌ No users paid - no delivery triggered")
            
            thread = threading.Thread(target=conditional_trigger)
            thread.daemon = True
            thread.start()
            
            return {
                'status': 'conditional_scheduled',
                'scheduled_time': scheduled_datetime.strftime('%I:%M %p'),
                'message': f'Delivery will trigger at {scheduled_datetime.strftime("%I:%M %p")} for whoever has paid'
            }
            
        except Exception as e:
            print(f"❌ Conditional scheduling error: {e}")
            return {
                'status': 'error',
                'message': str(e)
            }
    
    def _build_delivery_data(self, group_id: str, paid_users: List[str], order_session: Dict) -> Dict:
        """Build delivery data for Uber Direct"""
        
        restaurant = order_session.get('restaurant', 'Unknown')
        
        # Get location from first user's context
        location = 'Richard J Daley Library'  # Default
        try:
            first_user_context = self.session_manager.get_user_context(paid_users[0])
            if first_user_context.current_food_request:
                location = first_user_context.current_food_request.get('location', location)
        except Exception as e:
            print(f"⚠️ Could not get location: {e}")
        
        # Build order details for each paid user
        order_details = []
        for user_phone in paid_users:
            try:
                user_session = self.db.collection('order_sessions').document(user_phone).get()
                if user_session.exists:
                    session_data = user_session.to_dict()
                    order_details.append({
                        'user_phone': user_phone,
                        'order_number': session_data.get('order_number'),
                        'customer_name': session_data.get('customer_name'),
                        'order_description': session_data.get('order_description')
                    })
            except Exception as e:
                print(f"⚠️ Could not get order details for {user_phone}: {e}")
                order_details.append({
                    'user_phone': user_phone,
                    'order_number': None,
                    'customer_name': None,
                    'order_description': None
                })
        
        return {
            'group_id': group_id,
            'restaurant': restaurant,
            'location': location,
            'members': paid_users,
            'group_size': len(paid_users),
            'order_details': order_details,
            'delivery_time': 'now'  # Since we're triggering now
        }
    
    def _mark_delivery_triggered(self, user_phone: str, delivery_id: str, tracking_url: str):
        """Mark user's session as delivery triggered"""
        
        try:
            # Update session manager context
            context = self.session_manager.get_user_context(user_phone)
            if context.active_order_session:
                context.active_order_session['delivery_triggered'] = True
                context.active_order_session['delivery_id'] = delivery_id
                context.active_order_session['tracking_url'] = tracking_url
                context.active_order_session['order_stage'] = 'delivered'
                self.session_manager.update_user_context(context)
            
            # Update order_sessions collection
            from pangea_order_processor import update_order_session, get_user_order_session
            order_session = get_user_order_session(user_phone)
            if order_session:
                order_session['delivery_triggered'] = True
                order_session['delivery_id'] = delivery_id
                order_session['tracking_url'] = tracking_url
                order_session['order_stage'] = 'delivered'
                update_order_session(user_phone, order_session)
            
            print(f"✅ Marked delivery triggered for {user_phone}")
            
        except Exception as e:
            print(f"❌ Error marking delivery triggered: {e}")
    
    def _schedule_delivery_notifications(self, delivery_data: Dict, delivery_result: Dict):
        """Schedule delayed delivery notifications"""
        
        print(f"📱 Scheduling delivery notifications in 50 seconds...")
        print(f"   📦 Delivery ID: {delivery_result.get('delivery_id', 'N/A')}")
        print(f"   👥 Recipients: {delivery_data.get('members', [])}")
        
        def send_delayed_notifications():
            print(f"⏱️ Starting 50-second notification delay...")
            # Wait 50 seconds before sending notifications
            time.sleep(50)
            
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
            
            # Send to all group members
            for user_phone in delivery_data.get('members', []):
                try:
                    # Use the send_sms method from the initialized send_message_func
                    if self.send_friendly_message:
                        self.send_friendly_message(user_phone, message)
                        print(f"✅ Sent delivery notification to {user_phone}")
                    else:
                        print(f"⚠️ No send_message function available for {user_phone}")
                except Exception as e:
                    print(f"❌ Failed to send notification to {user_phone}: {e}")
        
        # Start notification thread
        thread = threading.Thread(target=send_delayed_notifications)
        thread.daemon = True
        thread.start()
        print(f"⏰ Scheduled delivery notifications for 50 seconds")
    
    def check_and_trigger_group_delivery(self, group_id: str) -> Dict:
        """Check if group is ready for delivery and trigger if needed"""
        
        try:
            paid_users = self._get_paid_users_in_group(group_id)
            total_users = self._get_total_users_in_group(group_id)
            
            print(f"🔍 Checking group {group_id}: {len(paid_users)}/{total_users} paid")
            
            if len(paid_users) == total_users and len(paid_users) > 0:
                # Everyone has paid - check delivery time
                
                # Get delivery time from first user
                if paid_users:
                    first_user_context = self.session_manager.get_user_context(paid_users[0])
                    delivery_time = 'now'
                    
                    if first_user_context.current_food_request:
                        delivery_time = first_user_context.current_food_request.get('delivery_time', 'now')
                    
                    # Get order session for additional data
                    order_session = first_user_context.active_order_session or {}
                    
                    # Trigger delivery
                    return self._trigger_delivery_now(group_id, paid_users, order_session)
            
            return {
                'status': 'waiting',
                'message': f'Waiting for {total_users - len(paid_users)} more users to pay'
            }
            
        except Exception as e:
            print(f"❌ Error checking group delivery: {e}")
            return {
                'status': 'error',
                'message': str(e)
            }
