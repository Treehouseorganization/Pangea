"""
Delivery Coordinator
Handles delivery creation and timing with Uber Direct integration
"""

from typing import Dict, List, Optional
from datetime import datetime, timedelta
import threading
import time
import os

class DeliveryCoordinator:
    """Coordinates delivery creation and timing"""
    
    def __init__(self, db):
        self.db = db
    
    async def _enhance_with_user_order_details(self, delivery_data: Dict) -> Dict:
        """Fetch user order details from user_states and add to delivery data"""
        try:
            enhanced_data = delivery_data.copy()
            members = delivery_data.get('members', [])
            order_details = []
            
            # Check if this is a direct invitation group
            group_id = delivery_data.get('group_id')
            is_direct_invitation = False
            
            if group_id:
                try:
                    group_doc = self.db.collection('active_groups').document(group_id).get()
                    if group_doc.exists:
                        group_data = group_doc.to_dict()
                        is_direct_invitation = group_data.get('type') == 'direct_invitation'
                        print(f"   🔍 Group type: {'direct_invitation' if is_direct_invitation else 'regular'}")
                except Exception as e:
                    print(f"   ⚠️ Could not check group type: {e}")
            
            print(f"   🔍 Fetching order details for {len(members)} members...")
            
            if is_direct_invitation:
                # SPECIAL HANDLING: Direct invitations combine both people's food under one order
                print(f"   📋 DIRECT INVITATION: Combining orders under one name")
                combined_descriptions = []
                primary_user_data = None
                primary_phone = None
                
                # INVITE FEATURE FIX: First collect food descriptions from ALL users
                for member_phone in members:
                    try:
                        user_doc = self.db.collection('user_states').document(member_phone).get()
                        if user_doc.exists:
                            user_data = user_doc.to_dict()
                            order_description = user_data.get('order_description', '')
                            if order_description:
                                combined_descriptions.append(order_description)
                                print(f"   🍕 Added food: {order_description}")
                    except Exception as e:
                        print(f"   ❌ Error fetching details for {member_phone}: {e}")
                
                # Then find the user who actually has order details (the one who placed the order)
                for member_phone in members:
                    try:
                        user_doc = self.db.collection('user_states').document(member_phone).get()
                        if user_doc.exists:
                            user_data = user_doc.to_dict()
                            order_number = user_data.get('order_number', '')
                            customer_name = user_data.get('customer_name', '')
                            
                            # If this user has actual order details, make them primary
                            if order_number or (customer_name and customer_name != 'Student Order'):
                                primary_user_data = user_data
                                primary_phone = member_phone
                                print(f"   👑 Primary orderer: {member_phone} ({customer_name})")
                                break
                    except Exception as e:
                        print(f"   ❌ Error fetching details for {member_phone}: {e}")
                
                # Create single combined order entry
                if primary_user_data:
                    combined_food = " + ".join(combined_descriptions) if combined_descriptions else "Combined order for 2 people"
                    order_details = [{
                        'user_phone': primary_phone,
                        'order_number': primary_user_data.get('order_number', ''),
                        'customer_name': primary_user_data.get('customer_name', 'Direct invitation order'),
                        'order_description': combined_food
                    }]
                    print(f"   ✅ Created combined order: {order_details[0]['customer_name']} - {combined_food}")
                else:
                    # Fallback if no primary user found
                    order_details = [{
                        'user_phone': members[0],
                        'order_number': '',
                        'customer_name': 'Direct invitation order',
                        'order_description': 'Combined order for 2 people'
                    }]
                    print(f"   ⚠️ No primary orderer found, using fallback")
            else:
                # EXISTING LOGIC: Regular groups and solo orders (UNCHANGED)
                for member_phone in members:
                    try:
                        # Get user state from database
                        user_doc = self.db.collection('user_states').document(member_phone).get()
                        
                        if user_doc.exists:
                            user_data = user_doc.to_dict()
                            
                            # Extract order information
                            order_detail = {
                                'user_phone': member_phone,
                                'order_number': user_data.get('order_number', ''),
                                'customer_name': user_data.get('customer_name', ''),
                                'order_description': user_data.get('order_description', '')
                            }
                            
                            # Only add if we have some order info
                            if any([order_detail['order_number'], order_detail['customer_name'], order_detail['order_description']]):
                                order_details.append(order_detail)
                                print(f"   ✅ Found order for {member_phone}: {order_detail['customer_name']} - {order_detail['order_description']}")
                            else:
                                # Add placeholder if no details
                                order_details.append({
                                    'user_phone': member_phone,
                                    'order_number': '',
                                    'customer_name': 'Student Order',
                                    'order_description': 'Order details not provided'
                                })
                                print(f"   ⚠️ No order details for {member_phone}, using placeholder")
                        else:
                            # User not found, add placeholder
                            order_details.append({
                                'user_phone': member_phone,
                                'order_number': '',
                                'customer_name': 'Student Order',
                                'order_description': 'Order details not provided'
                            })
                            print(f"   ⚠️ User {member_phone} not found, using placeholder")
                            
                    except Exception as e:
                        print(f"   ❌ Error fetching details for {member_phone}: {e}")
                        # Add placeholder for failed lookup
                        order_details.append({
                            'user_phone': member_phone,
                            'order_number': '',
                            'customer_name': 'Student Order',
                            'order_description': 'Order details not available'
                        })
            
            enhanced_data['order_details'] = order_details
            print(f"   📊 Enhanced delivery data with {len(order_details)} order details")
            
            return enhanced_data
            
        except Exception as e:
            print(f"   ❌ Error enhancing delivery data: {e}")
            # Return original data if enhancement fails
            return delivery_data
    
    async def _sync_group_status(self, delivery_data: Dict) -> Dict:
        """Sync group status from database to fix notification issues"""
        try:
            group_id = delivery_data.get('group_id')
            if not group_id:
                return delivery_data
            
            # Get current group status from active_groups collection
            group_doc = self.db.collection('active_groups').document(group_id).get()
            if group_doc.exists:
                group_data = group_doc.to_dict()
                
                # Update delivery data with current group status
                current_is_fake_match = group_data.get('is_fake_match', False)
                current_group_size = group_data.get('group_size', 1)
                
                # CRITICAL FIX: Use current database values, not original delivery_data
                delivery_data['is_fake_match'] = current_is_fake_match
                delivery_data['group_size'] = current_group_size
                
                print(f"   🔄 Synced group status: is_fake_match={current_is_fake_match}, group_size={current_group_size}")
                
                # If group was upgraded from fake to real, ensure we have all members
                if not current_is_fake_match and current_group_size == 2:
                    current_members = group_data.get('members', [])
                    if len(current_members) == 2:
                        delivery_data['members'] = current_members
                        print(f"   👥 Updated members list: {current_members}")
            
            return delivery_data
            
        except Exception as e:
            print(f"   ❌ Error syncing group status: {e}")
            return delivery_data
    
    async def create_delivery(self, delivery_data: Dict) -> Dict:
        """Create delivery using existing Uber Direct integration"""
        try:
            print(f"🚚 DELIVERY COORDINATOR - CREATE DELIVERY:")
            print(f"   🍕 Restaurant: {delivery_data.get('restaurant')}")
            print(f"   📍 Location: {delivery_data.get('location')}")
            print(f"   👥 Group Size: {delivery_data.get('group_size')}")
            print(f"   📱 Members: {delivery_data.get('members', [])}")
            print(f"   🎯 Group ID: {delivery_data.get('group_id')}")
            
            # ✅ FIX: Fetch real user order details from user_states
            enhanced_delivery_data = await self._enhance_with_user_order_details(delivery_data)
            print(f"   📋 Enhanced with order details: {enhanced_delivery_data.get('order_details', [])}")
            
            # ✅ FIX: Get current group status to fix notification issue
            enhanced_delivery_data = await self._sync_group_status(enhanced_delivery_data)
            
            # Import existing Uber Direct integration
            from pangea_uber_direct import create_group_delivery
            
            print(f"   🔗 Calling Uber Direct API...")
            
            # Use existing delivery creation logic with enhanced data
            result = create_group_delivery(enhanced_delivery_data)
            
            print(f"   📋 UBER DIRECT RESPONSE:")
            print(f"      Success: {result.get('success', False)}")
            print(f"      Delivery ID: {result.get('delivery_id', 'N/A')}")
            print(f"      Tracking URL: {result.get('tracking_url', 'N/A')}")
            print(f"      Error: {result.get('error', 'None')}")
            
            if result.get('success'):
                # Store delivery information
                print(f"   💾 Storing delivery record...")
                await self._store_delivery_record(delivery_data, result)
                
                print(f"   ✅ DELIVERY SUCCESSFULLY CREATED:")
                print(f"      ID: {result.get('delivery_id')}")
                print(f"      Tracking: {result.get('tracking_url')}")
                
                return {
                    'success': True,
                    'delivery_id': result.get('delivery_id'),
                    'tracking_url': result.get('tracking_url')
                }
            else:
                print(f"   ❌ DELIVERY CREATION FAILED:")
                print(f"      Error: {result.get('error', 'Unknown error')}")
                return {
                    'success': False,
                    'error': result.get('error', 'Unknown error')
                }
                
        except Exception as e:
            print(f"   ❌ DELIVERY COORDINATOR ERROR:")
            print(f"      Exception: {str(e)}")
            print(f"      Type: {type(e).__name__}")
            import traceback
            print(f"      Traceback: {traceback.format_exc()}")
            return {
                'success': False,
                'error': str(e)
            }
    
    async def schedule_delivery_for_time(self, delivery_data: Dict, scheduled_time: datetime) -> Dict:
        """Schedule delivery for specific time"""
        try:
            # Calculate delay
            import pytz
            chicago_tz = pytz.timezone('America/Chicago')
            
            if scheduled_time.tzinfo is None:
                scheduled_time = chicago_tz.localize(scheduled_time)
            
            current_time = datetime.now(chicago_tz)
            delay_seconds = (scheduled_time - current_time).total_seconds()
            
            if delay_seconds <= 0:
                # Time has passed - create immediately
                return await self.create_delivery(delivery_data)
            
            print(f"⏰ Scheduling delivery in {delay_seconds} seconds ({scheduled_time.strftime('%I:%M %p')})")
            
            # Start background thread to create delivery at scheduled time
            def delayed_delivery():
                time.sleep(delay_seconds)
                print(f"🔔 Scheduled time reached - checking group validity before creating delivery")
                
                # CRITICAL FIX: Check if group is still valid before executing delivery
                group_id = delivery_data.get('group_id')
                if group_id:
                    try:
                        group_doc = self.db.collection('active_groups').document(group_id).get()
                        if group_doc.exists:
                            group_data = group_doc.to_dict()
                            group_status = group_data.get('status', 'active')
                            
                            if group_status == 'cancelled':
                                print(f"🚫 Group {group_id} was cancelled - skipping scheduled delivery")
                                return
                            elif group_status not in ['active', 'fake_match', 'matched']:
                                print(f"🚫 Group {group_id} has invalid status '{group_status}' - skipping scheduled delivery")
                                return
                        else:
                            print(f"🚫 Group {group_id} no longer exists - skipping scheduled delivery")
                            return
                            
                    except Exception as e:
                        print(f"❌ Error checking group validity: {e} - proceeding with delivery")
                
                # Additional check: Verify payment status for group members
                members = delivery_data.get('members', [])
                if members:
                    try:
                        # Check if all members have actually paid (payment_timestamp exists)
                        all_paid = True
                        for member_phone in members:
                            user_doc = self.db.collection('user_states').document(member_phone).get()
                            if user_doc.exists:
                                user_data = user_doc.to_dict()
                                if not user_data.get('payment_timestamp'):
                                    print(f"🚫 User {member_phone} hasn't completed payment - skipping scheduled delivery")
                                    all_paid = False
                                    break
                            else:
                                print(f"🚫 User {member_phone} not found - skipping scheduled delivery")
                                all_paid = False
                                break
                        
                        if not all_paid:
                            return
                            
                    except Exception as e:
                        print(f"❌ Error checking payment status: {e} - skipping delivery for safety")
                        return
                
                print(f"✅ Group is valid and all members paid - proceeding with scheduled delivery")
                import asyncio
                asyncio.run(self.create_delivery(delivery_data))
            
            thread = threading.Thread(target=delayed_delivery)
            thread.daemon = False
            thread.start()
            
            return {
                'success': True,
                'scheduled': True,
                'scheduled_time': scheduled_time.strftime('%I:%M %p'),
                'delay_seconds': delay_seconds
            }
            
        except Exception as e:
            print(f"❌ Delivery scheduling error: {e}")
            return {
                'success': False,
                'error': str(e)
            }
    
    async def _store_delivery_record(self, delivery_data: Dict, result: Dict):
        """Store delivery record in database"""
        try:
            delivery_record = {
                'delivery_id': result.get('delivery_id'),
                'tracking_url': result.get('tracking_url'),
                'group_id': delivery_data.get('group_id'),
                'restaurant': delivery_data.get('restaurant'),
                'location': delivery_data.get('location'),
                'members': delivery_data.get('members', []),
                'created_at': datetime.now(),
                'status': 'created'
            }
            
            print(f"      💾 STORING DELIVERY RECORD:")
            print(f"         Collection: deliveries")
            print(f"         Document ID: {result.get('delivery_id')}")
            print(f"         Data: {delivery_record}")
            
            self.db.collection('deliveries').document(result.get('delivery_id')).set(delivery_record)
            
            print(f"      ✅ Delivery record stored successfully")
            
        except Exception as e:
            print(f"      ❌ ERROR STORING DELIVERY RECORD:")
            print(f"         Error: {str(e)}")
            print(f"         Type: {type(e).__name__}")
