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
    
    def __init__(self, db, session_manager):
        self.db = db
        self.session_manager = session_manager
    
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
            
            # Mark user as paid
            order_session['payment_timestamp'] = datetime.now()
            order_session['order_stage'] = 'paid'
            
            # Update both session manager and order processor
            context.active_order_session = order_session
            self.session_manager.update_user_context(context)
            
            # Also update order_sessions collection for compatibility
            from pangea_order_processor import update_order_session
            update_order_session(user_phone, order_session)
            
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
        
        print(f"📊 Group status: {len(paid_users)}/{total_users} paid")
        
        if len(paid_users) == total_users:
            # Both users paid
            if delivery_time == 'now' or delivery_time in ['asap', 'soon', 'immediately']:
                # Immediate delivery - trigger right away
                print(f"🚚 Both users paid - triggering immediate group delivery")
                return self._trigger_delivery_now(group_id, paid_users, order_session)
            else:
                # Scheduled delivery - set up timer
                print(f"⏰ Both users paid - scheduling group delivery for {delivery_time}")
                return self._schedule_delivery(group_id, paid_users, delivery_time, order_session)
        
        else:
            # Only one user paid so far
            if delivery_time == 'now' or delivery_time in ['asap', 'soon', 'immediately']:
                # Immediate delivery but waiting for other user
                print(f"⏳ Immediate order - waiting for other user to pay")
                return {
                    'status': 'waiting',
                    'message': 'Waiting for your group partner to pay'
                }
            else:
                # Scheduled delivery - set up conditional timer
                print(f"⏰ Scheduled order - setting up conditional timer")
                return self._schedule_conditional_delivery(group_id, paid_users, delivery_time, order_session)
    
    def _get_paid_users_in_group(self, group_id: str) -> List[str]:
        """Get list of users who have paid in this group"""
        
        try:
            paid_users = []
            
            # Check order_sessions collection
            order_sessions = self.db.collection('order_sessions')\
                .where('group_id', '==', group_id)\
                .get()
            
            for session_doc in order_sessions:
                session_data = session_doc.to_dict()
                user_phone = session_data.get('user_phone')
                payment_timestamp = session_data.get('payment_timestamp')
                
                if payment_timestamp:
                    paid_users.append(user_phone)
                    print(f"   ✅ {user_phone} has paid")
                else:
                    print(f"   ⏳ {user_phone} has not paid yet")
            
            return paid_users
            
        except Exception as e:
            print(f"❌ Error getting paid users: {e}")
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
    
    def _trigger_delivery_now(self, group_id: str, paid_users: List[str], order_session: Dict) -> Dict:
        """Trigger delivery immediately"""
        
        try:
            # Build delivery data
            delivery_data = self._build_delivery_data(group_id, paid_users, order_session)
            
            # Create delivery via Uber Direct
            from pangea_uber_direct import create_group_delivery
            result = create_group_delivery(delivery_data)
            
            if result.get('success'):
                delivery_id = result.get('delivery_id')
                tracking_url = result.get('tracking_url')
                
                print(f"✅ Delivery created: {delivery_id}")
                
                # Update all paid users' sessions
                for user_phone in paid_users:
                    self._mark_delivery_triggered(user_phone, delivery_id, tracking_url)
                
                # Send notifications after delay
                self._schedule_delivery_notifications(delivery_data, result)
                
                return {
                    'status': 'success',
                    'delivery_id': delivery_id,
                    'message': 'Delivery triggered successfully'
                }
            else:
                print(f"❌ Delivery creation failed: {result}")
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
            
            # Start background thread to trigger delivery
            def delayed_trigger():
                time.sleep(delay_seconds)
                print(f"⏰ Triggering scheduled delivery for {group_id}")
                self._trigger_delivery_now(group_id, paid_users, order_session)
            
            thread = threading.Thread(target=delayed_trigger)
            thread.daemon = True
            thread.start()
            
            return {
                'status': 'scheduled',
                'scheduled_time': scheduled_datetime.isoformat(),
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
                    # Only some paid - trigger solo delivery for paid users
                    print(f"⚠️ Only {len(current_paid_users)} users paid - triggering solo delivery")
                    self._trigger_delivery_now(group_id, current_paid_users, order_session)
                else:
                    # Nobody paid - no delivery
                    print(f"❌ No users paid - no delivery triggered")
            
            thread = threading.Thread(target=conditional_trigger)
            thread.daemon = True
            thread.start()
            
            return {
                'status': 'conditional_scheduled',
                'scheduled_time': scheduled_datetime.isoformat(),
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
        
        def send_delayed_notifications():
            # Wait 50 seconds before sending notifications
            time.sleep(50)
            
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
                    from pangea_main import send_friendly_message
                    send_friendly_message(user_phone, message, message_type="delivery_notification")
                    print(f"✅ Sent delivery notification to {user_phone}")
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
