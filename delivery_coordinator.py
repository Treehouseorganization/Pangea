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
    
    async def create_delivery(self, delivery_data: Dict) -> Dict:
        """Create delivery using existing Uber Direct integration"""
        try:
            print(f"🚚 DELIVERY COORDINATOR - CREATE DELIVERY:")
            print(f"   🍕 Restaurant: {delivery_data.get('restaurant')}")
            print(f"   📍 Location: {delivery_data.get('location')}")
            print(f"   👥 Group Size: {delivery_data.get('group_size')}")
            print(f"   📱 Members: {delivery_data.get('members', [])}")
            print(f"   🎯 Group ID: {delivery_data.get('group_id')}")
            
            # Import existing Uber Direct integration
            from pangea_uber_direct import create_group_delivery
            
            print(f"   🔗 Calling Uber Direct API...")
            
            # Use existing delivery creation logic
            result = create_group_delivery(delivery_data)
            
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
                print(f"🔔 Scheduled time reached - creating delivery")
                import asyncio
                asyncio.run(self.create_delivery(delivery_data))
            
            thread = threading.Thread(target=delayed_delivery)
            thread.daemon = True
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
