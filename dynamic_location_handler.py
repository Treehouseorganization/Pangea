"""
Dynamic Location Handler
Handles Google API geocoding and address confirmation for drop-off locations
"""

import requests
import os
from typing import Dict, List, Tuple, Optional

class DynamicLocationHandler:
    def __init__(self, api_key: str = None):
        self.api_key = api_key or os.getenv('GOOGLE_MAPS_API_KEY')
        self.base_url = "https://maps.googleapis.com/maps/api/geocode/json"
        
        # School detection keywords
        self.school_keywords = {
            "DePaul University": ["depaul", "depaul university", "dpu"],
            "Northern Illinois University": ["northern illinois", "niu", "northern illinois university", "dekalb"],
            "Western Illinois University": ["western illinois", "wiu", "western illinois university", "macomb"],
            "University of Illinois Chicago": ["uic", "university of illinois chicago", "illinois chicago"]
        }
        
        # School locations for proximity matching
        self.school_locations = {
            "DePaul University": {
                "address": "1 E Jackson Blvd, Chicago, IL 60604",
                "lat": 41.8781,
                "lng": -87.6298,
                "city": "Chicago"
            },
            "Northern Illinois University": {
                "address": "1425 W Lincoln Hwy, DeKalb, IL 60115", 
                "lat": 41.9312,
                "lng": -88.7537,
                "city": "DeKalb"
            },
            "Western Illinois University": {
                "address": "1 University Cir, Macomb, IL 61455",
                "lat": 40.4648,
                "lng": -90.6712,
                "city": "Macomb"
            },
            "University of Illinois Chicago": {
                "address": "1200 W Harrison St, Chicago, IL 60607",
                "lat": 41.8746,
                "lng": -87.6584,
                "city": "Chicago"
            }
        }

    def geocode_address(self, location_input: str, school_context: str = None) -> Optional[Dict]:
        """
        Geocode a location input using Google Maps API
        
        Args:
            location_input (str): User's location input
            school_context (str): School name for context if known
            
        Returns:
            Dict: Address data with lat/lng if successful, None if failed
        """
        if not self.api_key:
            print("❌ Google Maps API key not found")
            return None
            
        # Enhance query with school context if available
        query = location_input
        if school_context and school_context in self.school_locations:
            school_data = self.school_locations[school_context]
            query = f"{location_input} near {school_data['city']}"
        
        params = {
            'address': query,
            'key': self.api_key
        }
        
        try:
            response = requests.get(self.base_url, params=params)
            response.raise_for_status()
            data = response.json()
            
            if data['status'] == 'OK' and data['results']:
                result = data['results'][0]
                return {
                    'formatted_address': result['formatted_address'],
                    'lat': result['geometry']['location']['lat'],
                    'lng': result['geometry']['location']['lng'],
                    'place_id': result['place_id'],
                    'address_components': result['address_components']
                }
            else:
                print(f"Geocoding failed: {data.get('status', 'Unknown error')}")
                return None
        except Exception as e:
            print(f"Geocoding error: {e}")
            return None

    def detect_school_from_input(self, user_input: str) -> Optional[str]:
        """
        Detect which school the user is referring to based on their input
        
        Args:
            user_input (str): User's message or location input
            
        Returns:
            str: School name if detected, None otherwise
        """
        user_text = (user_input or "").lower()
        
        for school, keywords in self.school_keywords.items():
            for keyword in keywords:
                if keyword in user_text:
                    return school
        
        return None

    def detect_school_from_address(self, address: str) -> Optional[str]:
        """
        Detect school based on confirmed address proximity
        
        Args:
            address (str): Confirmed address from Google
            
        Returns:
            str: School name if detected, None otherwise
        """
        address_lower = (address or "").lower()
        
        # Check for city names in address
        for school, data in self.school_locations.items():
            city = data["city"].lower()
            if city in address_lower:
                return school
        
        return None

    def get_restaurant_locations_for_school(self, school_name: str) -> Optional[Dict]:
        """
        Import and return restaurant locations for a specific school
        
        Args:
            school_name (str): Name of the school
            
        Returns:
            dict: Restaurant locations or None if school not found
        """
        try:
            if school_name == "DePaul University":
                from depaul_locations import RESTAURANTS
                return RESTAURANTS
            elif school_name == "Northern Illinois University":
                from northern_illinois_locations import RESTAURANTS
                return RESTAURANTS  
            elif school_name == "Western Illinois University":
                from western_illinois_locations import RESTAURANTS
                return RESTAURANTS
            elif school_name == "University of Illinois Chicago":
                from pangea_locations import RESTAURANTS
                return RESTAURANTS
            else:
                print(f"Unknown school: {school_name}")
                return None
        except ImportError as e:
            print(f"Could not import restaurant locations for {school_name}: {e}")
            return None

    def format_confirmation_message(self, address_data: Dict, detected_school: str = None) -> str:
        """
        Format address confirmation message for the user
        
        Args:
            address_data (Dict): Geocoded address data
            detected_school (str): Detected school name
            
        Returns:
            str: Formatted confirmation message
        """
        address = address_data.get('formatted_address', 'Unknown address')
        school_text = f" by {detected_school}" if detected_school else ""
        
        return f"Is this the correct drop-off address{school_text}?\n\n📍 {address}\n\nRespond 'yes' to confirm, or provide the correct address if this is wrong."

    def process_location_request(self, user_input: str, context: Dict = None) -> Dict:
        """
        Main function to process a location request with confirmation flow
        
        Args:
            user_input (str): User's location input
            context (Dict): Additional context (restaurant choice, etc.)
            
        Returns:
            Dict: Processing result with status and data
        """
        # Try to detect school from user input
        detected_school = self.detect_school_from_input(user_input)
        
        # Geocode the address
        address_data = self.geocode_address(user_input, detected_school)
        
        if not address_data:
            return {
                'status': 'geocoding_failed',
                'message': "Sorry, I couldn't find that location. Please try a more specific address."
            }
        
        # Try to detect school from address if not already detected
        if not detected_school:
            detected_school = self.detect_school_from_address(address_data['formatted_address'])
        
        # Format confirmation message
        confirmation_msg = self.format_confirmation_message(address_data, detected_school)
        
        return {
            'status': 'awaiting_confirmation',
            'message': confirmation_msg,
            'address_data': address_data,
            'detected_school': detected_school,
            'needs_confirmation': True
        }

    def handle_final_confirmation(self, user_response: str, pending_data: Dict) -> Dict:
        """
        Handle final confirmation for corrected addresses
        
        Args:
            user_response (str): User's final confirmation response
            pending_data (Dict): Address data awaiting final confirmation
            
        Returns:
            Dict: Final result with confirmed address and restaurant data
        """
        user_response_lower = user_response.lower().strip()
        
        # Check for positive confirmation
        if any(word in user_response_lower for word in ['yes', 'correct', 'right', 'good', 'ok', 'okay']):
            address_data = pending_data['address_data']
            detected_school = pending_data['detected_school']
            restaurants = pending_data['restaurants']
            
            return {
                'status': 'confirmed',
                'address_data': address_data,
                'detected_school': detected_school,
                'restaurants': restaurants,
                'message': f"✅ Final address confirmed! {'Restaurant locations loaded for ' + detected_school if restaurants else 'Using default restaurant locations.'}"
            }
        else:
            # User still not satisfied - start over with their new input
            return self.process_location_request(user_response)

    def handle_confirmation_response(self, user_response: str, pending_data: Dict) -> Dict:
        """
        Handle user's response to address confirmation
        
        Args:
            user_response (str): User's confirmation response
            pending_data (Dict): Previously stored address data awaiting confirmation
            
        Returns:
            Dict: Final result with confirmed address and restaurant data
        """
        user_response_lower = user_response.lower().strip()
        
        # Check for positive confirmation
        if any(word in user_response_lower for word in ['yes', 'correct', 'right', 'good', 'ok', 'okay']):
            # User confirmed the address
            address_data = pending_data['address_data']
            detected_school = pending_data['detected_school']
            
            # Get restaurant locations for the school
            restaurants = None
            if detected_school:
                restaurants = self.get_restaurant_locations_for_school(detected_school)
            
            return {
                'status': 'confirmed',
                'address_data': address_data,
                'detected_school': detected_school,
                'restaurants': restaurants,
                'message': f"✅ Address confirmed! {'Restaurant locations loaded for ' + detected_school if restaurants else 'Using default restaurant locations.'}"
            }
        
        else:
            # User provided a different address - try to geocode it
            corrected_address = user_response
            detected_school = pending_data.get('detected_school')
            
            # Try geocoding the corrected address
            address_data = self.geocode_address(corrected_address, detected_school)
            
            if not address_data:
                return {
                    'status': 'geocoding_failed', 
                    'message': "Sorry, I couldn't find that corrected address. Please try again with a more specific address."
                }
            
            # Re-detect school from new address if needed
            if not detected_school:
                detected_school = self.detect_school_from_address(address_data['formatted_address'])
            
            # Get restaurant locations
            restaurants = None
            if detected_school:
                restaurants = self.get_restaurant_locations_for_school(detected_school)
            
            # Format confirmation message for the corrected address
            confirmation_msg = self.format_confirmation_message(address_data, detected_school)
            
            return {
                'status': 'awaiting_final_confirmation',
                'message': confirmation_msg,
                'address_data': address_data,
                'detected_school': detected_school,
                'restaurants': restaurants,
                'needs_confirmation': True
            }