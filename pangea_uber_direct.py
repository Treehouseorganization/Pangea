"""
Pangea Uber Direct Integration
Handles delivery creation and tracking through Uber Direct API
Integrated with main Pangea group ordering system
"""

import os
import json
import requests
import hashlib
import hmac
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Union
from dataclasses import dataclass
from dotenv import load_dotenv
import uuid
import re
import pytz

# Firebase imports
import firebase_admin
from firebase_admin import credentials, firestore

# Import from main Pangea system
try:
    from pangea_main import db, send_friendly_message, anthropic_llm
    from pangea_locations import RESTAURANTS, DROPOFFS
except ImportError:
    # Fallback initialization if running standalone
    load_dotenv()
    # Fallback initialization if running standalone
load_dotenv()
if not firebase_admin._apps:
    import json
    firebase_json = os.getenv('FIREBASE_SERVICE_ACCOUNT_JSON')
    if firebase_json:
        try:
            firebase_config = json.loads(firebase_json)
            cred = credentials.Certificate(firebase_config)
            firebase_admin.initialize_app(cred)
            print("✅ Firebase initialized successfully in uber direct")
        except json.JSONDecodeError as e:
            print(f"❌ Invalid Firebase JSON format in uber direct: {e}")
            raise
        except Exception as e:
            print(f"❌ Firebase initialization failed in uber direct: {e}")
            raise
    else:
        print("❌ FIREBASE_SERVICE_ACCOUNT_JSON environment variable not set in uber direct")
        raise ValueError("Firebase credentials not configured in uber direct")
db = firestore.client()

load_dotenv()

@dataclass
class UberDeliveryConfig:
    """Configuration for Uber Direct API"""
    client_id: str = os.getenv('UBER_CLIENT_ID')
    client_secret: str = os.getenv('UBER_CLIENT_SECRET')
    customer_id: str = os.getenv('UBER_CUSTOMER_ID')
    test_mode: str = os.getenv('UBER_DIRECT_TEST_MODE', 'true')  # 'true' for sandbox, 'false' for production
    base_url: str = None
    webhook_secret: str = os.getenv('UBER_WEBHOOK_SECRET')
    
    def __post_init__(self):
        """Set base URL based on test mode"""
        if self.test_mode.lower() == 'true':
            self.base_url = 'https://sandbox-api.uber.com'
            print("🧪 Using Uber Direct SANDBOX environment")
        else:
            self.base_url = 'https://api.uber.com'
            print("🚀 Using Uber Direct PRODUCTION environment")
        
        # Validate required API keys
        if not self.client_id:
            raise ValueError("UBER_CLIENT_ID environment variable is required")
        if not self.client_secret:
            raise ValueError("UBER_CLIENT_SECRET environment variable is required")
        if not self.customer_id:
            raise ValueError("UBER_CUSTOMER_ID environment variable is required")
        
        print(f"✅ Uber Direct configured with Customer ID: {self.customer_id[:8]}...")

def parse_delivery_time(time_str) -> datetime:
    """
    Parse user time preferences into datetime objects for Uber Direct scheduling
    
    Args:
        time_str: User's time preference like "3pm", "5:30pm", "now", "lunch", etc.
                  Can be string or DatetimeWithNanoseconds object
        
    Returns:
        datetime object for the scheduled delivery time
    """
    # If it's already a datetime object, return it as-is
    if isinstance(time_str, datetime):
        return time_str
    
    # Get current time in Chicago timezone for consistent handling
    chicago_tz = pytz.timezone('America/Chicago')
    chicago_now = datetime.now(chicago_tz)
    
    # Ensure time_str is a string
    if not isinstance(time_str, str):
        time_str = str(time_str)
    
    # Handle immediate delivery
    if time_str.lower() in ['now', 'asap', 'immediately']:
        return chicago_now + timedelta(minutes=25)  # 25 minutes from now (minimum prep time)
    
    # Handle meal periods
    meal_times = {
        'breakfast': 9,  # 9am
        'lunch': 12,     # 12pm
        'dinner': 18,    # 6pm
        'late night': 21 # 9pm
    }
    
    for meal, hour in meal_times.items():
        if meal in time_str.lower():
            target_time = chicago_now.replace(hour=hour, minute=0, second=0, microsecond=0)
            # If the time has passed today, schedule for tomorrow
            if target_time <= chicago_now:
                target_time += timedelta(days=1)
            return target_time
    
    # Handle time ranges like "10:50-11:00pm", "between 10:50 and 11:00pm"
    range_patterns = [
        r'between\s+(\d{1,2}):(\d{2})\s+and\s+(\d{1,2}):(\d{2})\s*(pm|am)',  # between 10:50 and 11:00pm
        r'(\d{1,2}):(\d{2})\s*-\s*(\d{1,2}):(\d{2})\s*(pm|am)',  # 10:50-11:00pm
        r'(\d{1,2}):(\d{2})\s*to\s*(\d{1,2}):(\d{2})\s*(pm|am)',  # 10:50 to 11:00pm
    ]
    
    for pattern in range_patterns:
        match = re.search(pattern, time_str.lower())
        if match:
            groups = match.groups()
            if len(groups) >= 5:
                # Take the earlier time from the range
                hour = int(groups[0])
                minute = int(groups[1])
                period = groups[4]
                
                # Convert to 24-hour format
                if period == 'pm' and hour != 12:
                    hour += 12
                elif period == 'am' and hour == 12:
                    hour = 0
                
                # Create target time
                target_time = chicago_now.replace(hour=hour, minute=minute, second=0, microsecond=0)
                
                # If the time has passed today, schedule for tomorrow
                if target_time <= chicago_now:
                    target_time += timedelta(days=1)
                    
                print(f"✅ Parsed time range '{time_str}' -> scheduling for {target_time.strftime('%I:%M %p')}")
                return target_time

    # Handle specific times like "3pm", "5:30pm", "2:15"
    time_patterns = [
        r'(\d{1,2}):(\d{2})\s*(pm|am)',  # 3:30pm, 2:15am
        r'(\d{1,2})\s*(pm|am)',          # 3pm, 2am
        r'(\d{1,2}):(\d{2})',            # 15:30, 14:00 (24-hour)
        r'(\d{1,2})'                     # 3 (assume current period)
    ]
    
    for pattern in time_patterns:
        match = re.search(pattern, time_str.lower())
        if match:
            groups = match.groups()
            
            if len(groups) >= 3 and groups[2]:  # has am/pm with minutes (3:30pm)
                hour = int(groups[0])
                minute = int(groups[1]) if groups[1] else 0
                period = groups[2]
                
                # Convert to 24-hour format
                if period == 'pm' and hour != 12:
                    hour += 12
                elif period == 'am' and hour == 12:
                    hour = 0
                    
            elif len(groups) == 2 and groups[1] in ['am', 'pm']:  # has am/pm without minutes (2am, 3pm)
                hour = int(groups[0])
                minute = 0
                period = groups[1]
                
                # Convert to 24-hour format
                if period == 'pm' and hour != 12:
                    hour += 12
                elif period == 'am' and hour == 12:
                    hour = 0
                    
            elif len(groups) == 2 and groups[1] and ':' in time_str:  # 24-hour format
                hour = int(groups[0])
                minute = int(groups[1])
                
            else:  # just hour number
                hour = int(groups[0])
                minute = 0
                
                # Smart defaults: if hour is 1-7, assume PM; if 8-12, assume current period
                if hour <= 7:
                    hour += 12  # assume PM
                elif hour >= 8 and hour <= 12:
                    # keep as is for now, but check if it's passed
                    pass
            
            # Create target time
            target_time = chicago_now.replace(hour=hour, minute=minute, second=0, microsecond=0)
            
            # If the time has passed today, schedule for tomorrow
            if target_time <= chicago_now:
                target_time += timedelta(days=1)
                
            return target_time
    
    # Default fallback: 30 minutes from now
    print(f"⚠️ Could not parse time '{time_str}', defaulting to 30 minutes from now")
    return chicago_now + timedelta(minutes=30)

def _get_intelligent_range_time(time_str: str, groups: tuple) -> Optional[datetime]:
    """Use Claude AI to intelligently select time from a range"""
    
    try:
        from langchain_anthropic import ChatAnthropic
        
        llm = ChatAnthropic(
            model="claude-3-5-sonnet-20241022",
            temperature=0.1,
            max_tokens=200
        )
        
        prompt = f"""A user requested food delivery '{time_str}'. This is a time range, not a specific time.

For delivery scheduling, what would be the optimal time within this range? Consider:
1. User likely wants delivery in the middle of their window, not at the earliest possible time
2. Middle of range shows flexibility and consideration for coordination
3. For "between 1:40pm and 2:00pm" - optimal time would be around 1:50pm

Return ONLY the optimal time in format like "1:50 PM" or "2:15 PM"."""
        
        response = llm.invoke([{"role": "user", "content": prompt}])
        suggested_time = response.content.strip()
        
        # Parse Claude's suggested time
        import re
        time_match = re.search(r'(\d{1,2}):(\d{2})\s*(AM|PM|am|pm)', suggested_time)
        if time_match:
            hour = int(time_match.group(1))
            minute = int(time_match.group(2))
            period = time_match.group(3).lower()
            
            # Convert to 24-hour format
            if period == 'pm' and hour != 12:
                hour += 12
            elif period == 'am' and hour == 12:
                hour = 0
            
            # Create target time
            import pytz
            chicago_tz = pytz.timezone('America/Chicago')
            chicago_now = datetime.now(chicago_tz)
            
            target_time = chicago_now.replace(hour=hour, minute=minute, second=0, microsecond=0)
            
            # If the time has passed today, schedule for tomorrow
            if target_time <= chicago_now:
                target_time += timedelta(days=1)
                
            print(f"🧠 Claude AI selected '{suggested_time}' from range '{time_str}'")
            return target_time
            
    except Exception as e:
        print(f"⚠️ Claude AI time selection failed: {e}")
        
    return None

class UberDirectClient:
    """Uber Direct API client for Pangea food delivery"""
    
    def __init__(self):
        self.config = UberDeliveryConfig()
        self.access_token = None
        self.token_expires_at = None
        
    def authenticate(self) -> str:
        """Get OAuth 2.0 access token for Uber Direct API"""
        
        if self.access_token and self.token_expires_at > datetime.now():
            return self.access_token
            
        auth_url = "https://auth.uber.com/oauth/v2/token"
        
        headers = {
            'Content-Type': 'application/x-www-form-urlencoded'
        }
        
        data = {
            'client_id': self.config.client_id,
            'client_secret': self.config.client_secret,
            'grant_type': 'client_credentials',
            'scope': 'eats.deliveries'
        }
        
        print(f"🔐 Authenticating with Uber Direct API...")
        print(f"   Client ID: {self.config.client_id[:8]}...")
        print(f"   Environment: {self.config.base_url}")
        
        try:
            response = requests.post(auth_url, headers=headers, data=data)
            
            if response.status_code != 200:
                print(f"❌ Authentication failed with status {response.status_code}")
                print(f"   Response: {response.text}")
                response.raise_for_status()
            
            token_data = response.json()
            self.access_token = token_data['access_token']
            
            # Set expiration time (subtract 5 minutes for safety)
            expires_in = token_data.get('expires_in', 3600)
            self.token_expires_at = datetime.now() + timedelta(seconds=expires_in - 300)
            
            print(f"✅ Uber authentication successful!")
            print(f"   Token expires at: {self.token_expires_at}")
            print(f"   Scope: {token_data.get('scope', 'N/A')}")
            
            return self.access_token
            
        except requests.exceptions.RequestException as e:
            print(f"❌ Uber authentication failed: {e}")
            if hasattr(e, 'response') and e.response is not None:
                print(f"   Status Code: {e.response.status_code}")
                print(f"   Response Body: {e.response.text}")
            raise Exception(f"Failed to authenticate with Uber: {e}")

    def create_delivery_quote(self, pickup_location: str, dropoff_location: str) -> Dict:
        """
        Create a delivery quote to check feasibility and cost
        
        Args:
            pickup_location: Restaurant location
            dropoff_location: Student delivery location
            
        Returns:
            Quote data with pricing and timing estimates
        """
        
        access_token = self.authenticate()
        
        # ✅ FIX: Use correct endpoint for quotes
        quote_url = f"{self.config.base_url}/v1/customers/{self.config.customer_id}/delivery_quotes"
        
        headers = {
            'Authorization': f'Bearer {access_token}',
            'Content-Type': 'application/json'
        }
        
        # ✅ FIX: Use proper address format for quotes
        pickup_address = self._get_restaurant_address_string(pickup_location)
        dropoff_address = self._get_dropoff_address_string(dropoff_location)
        
        payload = {
            "pickup_address": pickup_address,
            "dropoff_address": dropoff_address
        }
        
        try:
            response = requests.post(quote_url, headers=headers, json=payload)
            response.raise_for_status()
            
            quote_data = response.json()
            
            print(f"✅ Quote created: ${quote_data['fee']/100:.2f}, {quote_data['duration']} min ETA")
            
            # Store quote in Firebase for tracking
            self._store_quote(quote_data)
            
            return quote_data
            
        except requests.exceptions.RequestException as e:
            print(f"❌ Quote creation failed: {e}")
            return {"error": f"Failed to create quote: {e}"}

    def create_delivery(self, group_data: Dict, quote_id: str) -> Dict:
        """
        Create actual delivery using a valid quote
        
        Args:
            group_data: Group order information
            quote_id: Valid quote ID from create_delivery_quote
            
        Returns:
            Delivery data with tracking information
        """
        
        access_token = self.authenticate()
        
        # ✅ FIX: Use correct delivery endpoint
        delivery_url = f"{self.config.base_url}/v1/customers/{self.config.customer_id}/deliveries"
        
        headers = {
            'Authorization': f'Bearer {access_token}',
            'Content-Type': 'application/json'
        }
        
        # Build delivery payload
        payload = self._build_delivery_payload(group_data, quote_id)
        
        # Debug logging
        print(f"🔍 DEBUG - Delivery payload:")
        print(json.dumps(payload, indent=2, default=str))
        
        try:
            response = requests.post(delivery_url, headers=headers, json=payload)
            
            if response.status_code != 200:
                print(f"❌ Delivery creation failed with status {response.status_code}")
                print(f"   Response: {response.text}")
                try:
                    error_data = response.json()
                    print(f"   Error details: {json.dumps(error_data, indent=2)}")
                except:
                    pass
            
            response.raise_for_status()
            
            delivery_data = response.json()
            
            print(f"✅ Delivery created: {delivery_data['id']}")
            print(f"📱 Tracking URL: {delivery_data['tracking_url']}")
            
            # Store delivery in Firebase
            self._store_delivery(delivery_data, group_data)
            
            # Notify group members
            self._notify_group_about_delivery(group_data, delivery_data)
            
            return delivery_data
            
        except requests.exceptions.RequestException as e:
            print(f"❌ Delivery creation failed: {e}")
            if hasattr(e, 'response') and e.response is not None:
                print(f"   Status Code: {e.response.status_code}")
                print(f"   Response Body: {e.response.text}")
            return {"error": f"Failed to create delivery: {e}"}

    def get_delivery_status(self, delivery_id: str) -> Dict:
        """Get current status of a delivery"""
        
        access_token = self.authenticate()
        
        status_url = f"{self.config.base_url}/v1/customers/{self.config.customer_id}/deliveries/{delivery_id}"
        
        headers = {
            'Authorization': f'Bearer {access_token}'
        }
        
        try:
            response = requests.get(status_url, headers=headers)
            response.raise_for_status()
            
            return response.json()
            
        except requests.exceptions.RequestException as e:
            print(f"❌ Status check failed: {e}")
            return {"error": f"Failed to get delivery status: {e}"}

    def cancel_delivery(self, delivery_id: str) -> Dict:
        """Cancel a delivery if needed"""
        
        access_token = self.authenticate()
        
        cancel_url = f"{self.config.base_url}/v1/customers/{self.config.customer_id}/deliveries/{delivery_id}/cancel"
        
        headers = {
            'Authorization': f'Bearer {access_token}',
            'Content-Type': 'application/json'
        }
        
        try:
            response = requests.post(cancel_url, headers=headers)
            response.raise_for_status()
            
            return response.json()
            
        except requests.exceptions.RequestException as e:
            print(f"❌ Delivery cancellation failed: {e}")
            return {"error": f"Failed to cancel delivery: {e}"}

    def _get_restaurant_address(self, restaurant_name: str) -> str:
        """Convert restaurant name to full address"""
        
        restaurant_addresses = {
            "Chipotle": '{"street_address": ["633 N State St"], "city": "Chicago", "state": "IL", "zip_code": "60654"}',
            "McDonald's": '{"street_address": ["210 N Canal St"], "city": "Chicago", "state": "IL", "zip_code": "60606"}',
            "Chick-fil-A": '{"street_address": ["30 E Adams St"], "city": "Chicago", "state": "IL", "zip_code": "60603"}',
            "Portillo's": '{"street_address": ["520 W Taylor St"], "city": "Chicago", "state": "IL", "zip_code": "60607"}',
            "Starbucks": '{"street_address": ["1 N State St"], "city": "Chicago", "state": "IL", "zip_code": "60602"}'
        }
        
        return restaurant_addresses.get(restaurant_name, restaurant_addresses["Chipotle"])

    def _get_dropoff_address(self, dropoff_location: str) -> str:
        """Convert dropoff location to full address"""
        
        dropoff_addresses = {
            "Student Union": '{"street_address": ["750 S Halsted St"], "city": "Chicago", "state": "IL", "zip_code": "60607"}',
            "Campus Center": '{"street_address": ["828 S Wolcott Ave"], "city": "Chicago", "state": "IL", "zip_code": "60612"}',
            "Library Plaza": '{"street_address": ["801 S Morgan St"], "city": "Chicago", "state": "IL", "zip_code": "60607"}',
            "Recreation Center": '{"street_address": ["901 W Roosevelt Rd"], "city": "Chicago", "state": "IL", "zip_code": "60608"}',
            "Health Sciences Building": '{"street_address": ["1601 W Taylor St"], "city": "Chicago", "state": "IL", "zip_code": "60612"}'
        }
        
        return dropoff_addresses.get(dropoff_location, dropoff_addresses["Student Union"])

    def _build_delivery_payload(self, group_data: Dict, quote_id: str) -> Dict:
        """Build the delivery request payload with correct structure and FIXED timezone handling"""
        restaurant = group_data.get('restaurant', 'Unknown Restaurant')
        dropoff_location = group_data.get('location', 'Student Union')
        group_members = group_data.get('members', [])
        order_details = group_data.get('order_details', [])
        
        # Use first group member as primary contact
        primary_contact = group_members[0] if group_members else "+1234567890"
        
        # Build detailed pickup notes with each person's order
        pickup_notes = f"PANGEA GROUP ORDER - {len(group_members)} people:\n"
        for i, order_detail in enumerate(order_details):
            order_number = order_detail.get('order_number', '')
            customer_name = order_detail.get('customer_name', '')
            order_description = order_detail.get('order_description', '')
            
            line = f"{i+1}. "
            if order_number:
                line += f"Order #{order_number}"
            elif customer_name:
                line += f"Name: {customer_name}"
            else:
                line += "Student order"
            
            if order_description:
                line += f" - {order_description}"
            
            pickup_notes += line + "\n"
        pickup_notes += f"\nTotal: {len(group_members)} orders to pick up"
        
        # Build manifest items for each individual order
        manifest_items = []
        for i, order_detail in enumerate(order_details):
            order_number = order_detail.get('order_number', '')
            customer_name = order_detail.get('customer_name', '')
            order_description = order_detail.get('order_description', '')
            
            if order_number:
                item_name = f"Order #{order_number}"
            elif customer_name:
                item_name = f"{customer_name}'s Order"
            else:
                item_name = f"Student Order {i+1}"
            
            if order_description:
                item_name += f" - {order_description}"
            
            manifest_items.append({
                "name": item_name,
                "quantity": 1,
                "size": "small"
            })
        
        # Use string addresses for delivery creation
        pickup_address = self._get_restaurant_address_string(restaurant)
        dropoff_address = self._get_dropoff_address_string(dropoff_location)
        
        # ✅ FIXED: Better timezone handling for scheduled delivery time
        import pytz
        
        # Get scheduled delivery time from group data
        delivery_time_str = group_data.get('delivery_time', 'now')
        
        # Parse the user's requested time (returns datetime in local timezone)
        user_requested_time = parse_delivery_time(delivery_time_str)
        
        # ✅ CRITICAL FIX: Ensure we're working in Chicago timezone consistently
        chicago_tz = pytz.timezone('America/Chicago')
        
        # Handle timezone conversion
        if user_requested_time.tzinfo is None:
            # This is a naive datetime - assume it's in Chicago timezone
            chicago_time = chicago_tz.localize(user_requested_time)
            print(f"🕐 Localized naive time to Chicago: {chicago_time.strftime('%I:%M %p %Z')}")
        else:
            # Already timezone-aware - convert to Chicago time if needed
            chicago_time = user_requested_time.astimezone(chicago_tz)
            print(f"🕐 Already timezone-aware, converted to Chicago: {chicago_time.strftime('%I:%M %p %Z')}")
        
        # ✅ NEW FIX: Validate in Chicago time BEFORE converting to UTC
        chicago_now = datetime.now(chicago_tz)
        min_chicago_time = chicago_now + timedelta(minutes=20)
        
        if chicago_time < min_chicago_time:
            print(f"⚠️ Requested Chicago time {chicago_time.strftime('%I:%M %p')} is too soon")
            print(f"   Current Chicago time: {chicago_now.strftime('%I:%M %p')}")
            print(f"   Minimum Chicago time: {min_chicago_time.strftime('%I:%M %p')}")
            print(f"   Adjusting to minimum 20 minutes from now in Chicago time")
            chicago_time = min_chicago_time
        
        # Now convert Chicago time to UTC for Uber API
        utc_time = chicago_time.astimezone(pytz.UTC)
        pickup_ready_dt = utc_time.strftime('%Y-%m-%dT%H:%M:%S.000Z')
        
        print(f"🕐 User requested delivery time: {delivery_time_str}")
        print(f"🕐 Chicago time: {chicago_time.strftime('%I:%M %p on %B %d, %Y (%Z)')}")
        print(f"🕐 UTC time for Uber: {utc_time.strftime('%I:%M %p on %B %d, %Y (%Z)')}")
        print(f"🕐 Uber API timestamp: {pickup_ready_dt}")
        
        payload = {
            "quote_id": quote_id,
            "pickup_name": f"{restaurant} Pickup",
            "pickup_phone_number": "+15555555555",  # Restaurant phone
            "pickup_business_name": restaurant,
            "pickup_address": pickup_address,
            "pickup_notes": pickup_notes,
            "dropoff_name": "Pangea Group Order",
            "dropoff_phone_number": primary_contact,
            "dropoff_address": dropoff_address,
            "dropoff_notes": f"Group delivery for {len(group_members)} students - Meet at main entrance",
            "manifest_items": manifest_items,
            "manifest_reference": f"PANGEA-{group_data.get('group_id', 'unknown')}",
            "manifest_total_value": len(group_members) * 1500,  # $15 per person estimated
            "deliverable_action": "deliverable_action_meet_at_door",
            "undeliverable_action": "return",
            "requires_dropoff_signature": False,
            "requires_id": False,
            # ✅ FIXED: Properly calculated UTC timestamp
            "pickup_ready_dt": pickup_ready_dt,
            "tip": 300,  # $3 tip
            "idempotency_key": str(uuid.uuid4())
        }
        
        return payload

    def _get_restaurant_address_string(self, restaurant_name: str) -> str:
        """Convert restaurant name to address string for delivery creation"""
        
        # ✅ UPDATED: Using your actual restaurant addresses
        restaurant_addresses = {
            "Chipotle": "1132 S Clinton St, Chicago, IL 60607",
            "McDonald's": "2315 W Ogden Ave, Chicago, IL 60608", 
            "Chick-fil-A": "1106 S Clinton St, Chicago, IL 60607",
            "Portillo's": "520 W Taylor St, Chicago, IL 60607",
            "Starbucks": "1430 W Taylor St, Chicago, IL 60607"
        }
        
        return restaurant_addresses.get(restaurant_name, restaurant_addresses["Chipotle"])

    def _get_dropoff_address_string(self, dropoff_location: str) -> str:
        """Convert dropoff location to address string for delivery creation"""
        
        # ✅ UPDATED: Using your actual dropoff addresses
        dropoff_addresses = {
            "Richard J Daley Library": "801 S Morgan St, Chicago, IL 60607",
            "Student Center East": "750 S Halsted St, Chicago, IL 60607",
            "Student Center West": "828 S Wolcott Ave, Chicago, IL 60612", 
            "Student Services Building": "1200 W Harrison St, Chicago, IL 60607",
            "University Hall": "601 S Morgan St, Chicago, IL 60607"
        }
        
        return dropoff_addresses.get(dropoff_location, dropoff_addresses["Richard J Daley Library"])

    def _get_restaurant_address(self, restaurant_name: str) -> str:
        """Convert restaurant name to JSON address for quotes"""
        
        # ✅ UPDATED: Using your actual restaurant addresses in JSON format
        restaurant_addresses = {
            "Chipotle": '{"street_address": ["1132 S Clinton St"], "city": "Chicago", "state": "IL", "zip_code": "60607"}',
            "McDonald's": '{"street_address": ["2315 W Ogden Ave"], "city": "Chicago", "state": "IL", "zip_code": "60608"}',
            "Chick-fil-A": '{"street_address": ["1106 S Clinton St"], "city": "Chicago", "state": "IL", "zip_code": "60607"}',
            "Portillo's": '{"street_address": ["520 W Taylor St"], "city": "Chicago", "state": "IL", "zip_code": "60607"}',
            "Starbucks": '{"street_address": ["1430 W Taylor St"], "city": "Chicago", "state": "IL", "zip_code": "60607"}'
        }
        
        return restaurant_addresses.get(restaurant_name, restaurant_addresses["Chipotle"])

    def _get_dropoff_address(self, dropoff_location: str) -> str:
        """Convert dropoff location to JSON address for quotes"""
        
        # ✅ UPDATED: Using your actual dropoff addresses in JSON format
        dropoff_addresses = {
            "Richard J Daley Library": '{"street_address": ["801 S Morgan St"], "city": "Chicago", "state": "IL", "zip_code": "60607"}',
            "Student Center East": '{"street_address": ["750 S Halsted St"], "city": "Chicago", "state": "IL", "zip_code": "60607"}',
            "Student Center West": '{"street_address": ["828 S Wolcott Ave"], "city": "Chicago", "state": "IL", "zip_code": "60612"}',
            "Student Services Building": '{"street_address": ["1200 W Harrison St"], "city": "Chicago", "state": "IL", "zip_code": "60607"}',
            "University Hall": '{"street_address": ["601 S Morgan St"], "city": "Chicago", "state": "IL", "zip_code": "60607"}'
        }
        
        return dropoff_addresses.get(dropoff_location, dropoff_addresses["Richard J Daley Library"])

    def _store_quote(self, quote_data: Dict):
        """Store quote in Firebase for tracking"""
        try:
            db.collection('uber_quotes').document(quote_data['id']).set({
                **quote_data,
                'created_at': datetime.now(),
                'pangea_service': 'group_delivery'
            })
        except Exception as e:
            print(f"❌ Failed to store quote: {e}")

    def _store_delivery(self, delivery_data: Dict, group_data: Dict):
        """Store delivery in Firebase for tracking"""
        try:
            db.collection('uber_deliveries').document(delivery_data['id']).set({
                **delivery_data,
                'group_data': group_data,
                'created_at': datetime.now(),
                'pangea_service': 'group_delivery',
                'status': 'pending'
            })
        except Exception as e:
            print(f"❌ Failed to store delivery: {e}")

    def _notify_group_about_delivery(self, group_data: Dict, delivery_data: Dict):
        """Notify all group members about delivery status"""
        
        # Check if this is a fake match delivery
        is_fake_match = group_data.get('is_fake_match', False) or group_data.get('status') == 'fake_match'
        delivery_time_str = group_data.get('delivery_time', 'now')
        
        if is_fake_match and delivery_time_str != 'now':
            # Check if this is being called at the actual delivery time vs early
            from datetime import datetime, timedelta
            import pytz
            
            try:
                chicago_tz = pytz.timezone('America/Chicago')
                current_time = datetime.now(chicago_tz)
                scheduled_time = parse_delivery_time(delivery_time_str)
                
                if scheduled_time.tzinfo is None:
                    scheduled_time = chicago_tz.localize(scheduled_time)
                
                # If current time is at or after scheduled time, allow notification
                # Handle case where scheduled_time might be for tomorrow 
                if scheduled_time > current_time + timedelta(hours=12):
                    scheduled_time = scheduled_time.replace(day=current_time.day)
                
                if current_time >= scheduled_time:
                    print(f"🕐 Scheduled delivery time reached for fake match - sending notification")
                    # Continue to send notification
                else:
                    # For fake match scheduled deliveries, suppress immediate notification
                    print(f"🕐 Suppressing immediate notification for fake match scheduled delivery - will notify at delivery time")
                    return
            except Exception as e:
                print(f"⚠️ Error parsing scheduled time, suppressing notification: {e}")
                return
        elif not is_fake_match and delivery_time_str != 'now':
            # ✅ MCDONALD'S BUG FIX: For real matched scheduled deliveries, suppress immediate notifications
            # Only send delayed notifications that are handled separately
            print(f"🚫 SCHEDULED GROUP ORDER: Suppressing immediate notifications - only delayed notifications will be sent")
            return
        elif not is_fake_match and len(group_data.get('members', [])) == 2:
            # ✅ FIX: For 2-person upgraded deliveries, suppress immediate notifications
            # Only send delayed notifications (50-second delay)
            print(f"🚫 2-PERSON UPGRADED DELIVERY: Suppressing immediate notifications - only delayed notifications will be sent")
            return
        elif is_fake_match and delivery_time_str == 'now':
            # ✅ CHIPOTLE BUG FIX: For immediate solo fake match orders, suppress immediate notifications
            # Only send delayed notifications
            print(f"🚫 IMMEDIATE SOLO ORDER: Suppressing immediate notifications - only delayed notifications will be sent")
            return
        else:
            # For immediate deliveries of real matches, send notification
            print(f"🚚 Sending notification for immediate delivery")
        
        restaurant = group_data.get('restaurant', 'your restaurant')
        location = group_data.get('location', 'your location')
        tracking_url = delivery_data.get('tracking_url', '')
        
        message = f"""🚚 Your {restaurant} delivery is on the way!

📍 Delivery to: {location}
📱 Track your order: {tracking_url}

The driver will meet you at the delivery location. I'll send updates as your order progresses! 🍕"""
        
        for member_phone in group_data.get('members', []):
            try:
                if 'send_friendly_message' in globals():
                    send_friendly_message(member_phone, message, message_type="delivery_started")
                else:
                    # Send SMS directly using Twilio
                    try:
                        from twilio.rest import Client
                        import os
                        
                        client = Client(os.getenv('TWILIO_ACCOUNT_SID'), os.getenv('TWILIO_AUTH_TOKEN'))
                        sms = client.messages.create(
                            body=message,
                            from_=os.getenv('TWILIO_PHONE_NUMBER'),
                            to=member_phone
                        )
                        print(f"✅ SMS sent to {member_phone}: {sms.sid}")
                    except Exception as sms_e:
                        print(f"❌ Failed to send SMS to {member_phone}: {sms_e}")
                        print(f"📱 Would send to {member_phone}: {message}")
            except Exception as e:
                print(f"❌ Failed to notify {member_phone}: {e}")

    def verify_webhook(self, payload: bytes, signature: str) -> bool:
        """Verify webhook signature for security"""
        
        if not self.config.webhook_secret:
            print("⚠️ No webhook secret configured")
            return True  # Allow if no secret configured
            
        try:
            expected_signature = hmac.new(
                self.config.webhook_secret.encode(),
                payload,
                hashlib.sha256
            ).hexdigest()
            
            return hmac.compare_digest(signature, expected_signature)
            
        except Exception as e:
            print(f"❌ Webhook verification failed: {e}")
            return False

    def handle_webhook(self, payload: Dict, signature: str = None) -> Dict:
        """Handle incoming webhook from Uber"""
        
        try:
            event_type = payload.get('event_type')
            delivery_id = payload.get('delivery_id')
            
            if event_type == 'delivery.status':
                return self._handle_delivery_status_update(payload)
            elif event_type == 'courier.update':
                return self._handle_courier_update(payload)
            else:
                print(f"⚠️ Unknown webhook event: {event_type}")
                return {"status": "ignored"}
                
        except Exception as e:
            print(f"❌ Webhook handling failed: {e}")
            return {"status": "error", "error": str(e)}

    def _handle_delivery_status_update(self, payload: Dict) -> Dict:
        """Handle delivery status change webhook"""
        
        delivery_id = payload.get('delivery_id')
        new_status = payload.get('status')
        
        print(f"📦 Delivery {delivery_id} status: {new_status}")
        
        try:
            # Update delivery status in Firebase
            db.collection('uber_deliveries').document(delivery_id).update({
                'status': new_status,
                'last_status_update': datetime.now(),
                'webhook_data': payload
            })
            
            # Get group data for notifications
            delivery_doc = db.collection('uber_deliveries').document(delivery_id).get()
            if delivery_doc.exists:
                delivery_data = delivery_doc.to_dict()
                group_data = delivery_data.get('group_data', {})
                
                # Send status updates to group
                self._send_status_update_to_group(group_data, new_status, payload)
            
            return {"status": "processed"}
            
        except Exception as e:
            print(f"❌ Failed to process delivery status update: {e}")
            return {"status": "error"}

    def _handle_courier_update(self, payload: Dict) -> Dict:
        """Handle courier location update webhook"""
        
        delivery_id = payload.get('delivery_id')
        courier_location = payload.get('location', {})
        
        try:
            # Update courier location in Firebase
            db.collection('uber_deliveries').document(delivery_id).update({
                'courier_location': courier_location,
                'last_courier_update': datetime.now()
            })
            
            return {"status": "processed"}
            
        except Exception as e:
            print(f"❌ Failed to process courier update: {e}")
            return {"status": "error"}

    def _send_status_update_to_group(self, group_data: Dict, status: str, payload: Dict):
        """Send status updates to group members"""
        
        restaurant = group_data.get('restaurant', 'your restaurant')
        
        # Check if this is a scheduled delivery that hasn't started yet
        delivery_time_str = group_data.get('delivery_time', 'now')
        if delivery_time_str != 'now':
            scheduled_time = parse_delivery_time(delivery_time_str)
            chicago_tz = pytz.timezone('America/Chicago')
            current_time = datetime.now(chicago_tz)
            
            # If delivery is scheduled for the future, suppress early status updates
            if scheduled_time > current_time + timedelta(minutes=10):
                early_statuses = ['pending', 'pickup', 'pickup_complete']
                if status in early_statuses:
                    print(f"🕐 Suppressing early status update '{status}' for scheduled delivery at {scheduled_time.strftime('%I:%M %p')}")
                    return
        
        status_messages = {
            'pending': f"📝 Your {restaurant} order is confirmed and being prepared for pickup!",
            'pickup': f"🚚 Driver is picking up your {restaurant} order now!",
            'pickup_complete': f"✅ Your {restaurant} order has been picked up and is on the way!",
            'dropoff': f"📍 Driver is arriving with your {restaurant} order!",
            'delivered': f"🎉 Your {restaurant} order has been delivered! Enjoy your meal!",
            'canceled': f"❌ Your {restaurant} delivery was canceled. Please contact support.",
            'returned': f"🔄 Your {restaurant} order couldn't be delivered and is being returned."
        }
        
        message = status_messages.get(status, f"📦 Your {restaurant} order status: {status}")
        
        # Add ETA if available
        if status == 'pickup_complete' and payload.get('dropoff_eta'):
            eta = payload['dropoff_eta']
            message += f"\n\n⏰ Estimated delivery: {eta}"
        
        for member_phone in group_data.get('members', []):
            try:
                if 'send_friendly_message' in globals():
                    send_friendly_message(member_phone, message, message_type="delivery_update")
                else:
                    # Send SMS directly using Twilio
                    try:
                        from twilio.rest import Client
                        import os
                        
                        client = Client(os.getenv('TWILIO_ACCOUNT_SID'), os.getenv('TWILIO_AUTH_TOKEN'))
                        sms = client.messages.create(
                            body=message,
                            from_=os.getenv('TWILIO_PHONE_NUMBER'),
                            to=member_phone
                        )
                        print(f"✅ SMS sent to {member_phone}: {sms.sid}")
                    except Exception as sms_e:
                        print(f"❌ Failed to send SMS to {member_phone}: {sms_e}")
                        print(f"📱 Would send to {member_phone}: {message}")
            except Exception as e:
                print(f"❌ Failed to notify {member_phone}: {e}")


# Main integration functions for Pangea
def create_group_delivery(group_data: Dict) -> Dict:
    """
    Main function to create a delivery for a Pangea group order
    
    Args:
        group_data: Dict containing:
            - restaurant: Restaurant name
            - location: Dropoff location
            - members: List of group member phone numbers
            - group_id: Unique group identifier
            
    Returns:
        Delivery result with tracking info
    """
    
    client = UberDirectClient()
    
    try:
        print(f"🚚 Creating delivery for {group_data.get('restaurant')} group...")
        print(f"📋 Group data: {group_data}")
        
        # Step 1: Create quote
        print(f"📝 Creating quote for pickup: {group_data.get('restaurant')}, dropoff: {group_data.get('location')}")
        quote_result = client.create_delivery_quote(
            pickup_location=group_data.get('restaurant'),
            dropoff_location=group_data.get('location')
        )
        
        print(f"💰 Quote result: {quote_result}")
        if 'error' in quote_result:
            print(f"❌ Quote creation failed: {quote_result}")
            return quote_result
        
        quote_id = quote_result['id']
        print(f"✅ Quote created with ID: {quote_id}")
        
        # Step 2: Create delivery
        print(f"🚚 Creating delivery with quote ID: {quote_id}")
        delivery_result = client.create_delivery(group_data, quote_id)
        
        print(f"📦 Delivery result: {delivery_result}")
        if 'error' in delivery_result:
            print(f"❌ Delivery creation failed: {delivery_result}")
            return delivery_result
        
        print(f"✅ Delivery created successfully: {delivery_result['id']}")
        print(f"🔗 Tracking URL: {delivery_result.get('tracking_url', 'N/A')}")
        
        return {
            'success': True,
            'delivery_id': delivery_result['id'],
            'tracking_url': delivery_result.get('tracking_url'),
            'quote_data': quote_result,
            'delivery_data': delivery_result
        }
        
    except Exception as e:
        print(f"❌ Group delivery creation failed: {e}")
        return {'error': f'Failed to create group delivery: {e}'}


def get_group_delivery_status(delivery_id: str) -> Dict:
    """Get status of a group delivery"""
    
    client = UberDirectClient()
    return client.get_delivery_status(delivery_id)


def handle_uber_webhook(request_data: Dict, signature: str = None) -> Dict:
    """Handle incoming Uber webhook"""
    
    client = UberDirectClient()
    return client.handle_webhook(request_data, signature)


# Example usage and testing
if __name__ == "__main__":
    # Test configuration and authentication
    print("🧪 Testing Uber Direct integration...")
    
    # Check environment variables
    required_env_vars = ['UBER_CLIENT_ID', 'UBER_CLIENT_SECRET', 'UBER_CUSTOMER_ID']
    missing_vars = [var for var in required_env_vars if not os.getenv(var)]
    
    if missing_vars:
        print(f"❌ Missing required environment variables: {', '.join(missing_vars)}")
        print("\nAdd these to your .env file:")
        print("UBER_CLIENT_ID=your_client_id_here")
        print("UBER_CLIENT_SECRET=your_client_secret_here") 
        print("UBER_CUSTOMER_ID=your_customer_id_here")
        print("UBER_DIRECT_TEST_MODE=true  # Use 'false' for production")
        print("UBER_WEBHOOK_SECRET=your_webhook_secret_here")
        exit(1)
    
    # Test authentication
    try:
        client = UberDirectClient()
        token = client.authenticate()
        print(f"✅ Authentication test passed!")
        
        # Test with sample group data
        sample_group = {
            'restaurant': 'Chipotle',
            'location': 'Student Union',
            'members': ['+1234567890', '+0987654321', '+1122334455'],
            'group_id': 'test_group_123',
            'order_details': [
                {'user_phone': '+1234567890', 'order_number': 'ABC123', 'customer_name': None},
                {'user_phone': '+0987654321', 'order_number': None, 'customer_name': 'Maria Rodriguez'},
                {'user_phone': '+1122334455', 'order_number': 'XYZ789', 'customer_name': None}
            ]
        }
        
        print("🧪 Testing quote creation...")
        quote_result = client.create_delivery_quote(
            pickup_location=sample_group['restaurant'],
            dropoff_location=sample_group['location']
        )
        
        if 'error' not in quote_result:
            print(f"✅ Quote test passed! Fee: ${quote_result['fee']/100:.2f}")
            
            # Note: Uncomment below to test actual delivery creation in sandbox
            # print("🧪 Testing delivery creation...")
            # delivery_result = client.create_delivery(sample_group, quote_result['id'])
            # print(f"Delivery test result: {delivery_result}")
        else:
            print(f"❌ Quote test failed: {quote_result['error']}")
            
    except Exception as e:
        print(f"❌ Integration test failed: {e}")
        
    print("\n🔧 Environment Configuration:")
    print(f"   Test Mode: {os.getenv('UBER_DIRECT_TEST_MODE', 'true')}")
    print(f"   Client ID: {os.getenv('UBER_CLIENT_ID', 'NOT_SET')[:8]}...")
    print(f"   Customer ID: {os.getenv('UBER_CUSTOMER_ID', 'NOT_SET')[:8]}...")
    print(f"   Webhook Secret: {'SET' if os.getenv('UBER_WEBHOOK_SECRET') else 'NOT_SET'}")