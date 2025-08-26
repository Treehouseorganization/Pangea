"""
Configuration management for the rewritten system
"""

import os
from typing import Dict, List

class Config:
    """Application configuration"""
    
    # Twilio Configuration
    TWILIO_ACCOUNT_SID = os.getenv('TWILIO_ACCOUNT_SID')
    TWILIO_AUTH_TOKEN = os.getenv('TWILIO_AUTH_TOKEN')
    TWILIO_PHONE_NUMBER = os.getenv('TWILIO_PHONE_NUMBER')
    
    # Anthropic Configuration
    ANTHROPIC_API_KEY = os.getenv('ANTHROPIC_API_KEY')
    ANTHROPIC_MODEL = "claude-opus-4-20250514"
    
    # Firebase Configuration
    FIREBASE_SERVICE_ACCOUNT_JSON = os.getenv('FIREBASE_SERVICE_ACCOUNT_JSON')
    
    # Uber Direct Configuration
    UBER_CLIENT_ID = os.getenv('UBER_CLIENT_ID')
    UBER_CLIENT_SECRET = os.getenv('UBER_CLIENT_SECRET')
    UBER_CUSTOMER_ID = os.getenv('UBER_CUSTOMER_ID')
    UBER_DIRECT_TEST_MODE = os.getenv('UBER_DIRECT_TEST_MODE', 'true')
    
    # Payment Links
    STRIPE_LINK_350 = os.getenv("STRIPE_LINK_350", "https://pay.stripe.com/solo_order")
    STRIPE_LINK_450 = os.getenv("STRIPE_LINK_450", "https://pay.stripe.com/group_order")
    
    # Application Settings
    DEBUG_MODE = os.getenv('DEBUG_MODE', 'false').lower() == 'true'
    PORT = int(os.getenv('PORT', 8000))
    
    # Business Logic
    RESTAURANTS: List[str] = [
        'Chipotle', 
        'McDonald\'s', 
        'Chick-fil-A', 
        'Portillo\'s', 
        'Starbucks'
    ]
    
    LOCATIONS: List[str] = [
        'Richard J Daley Library',
        'Student Center East',
        'Student Center West', 
        'Student Services Building',
        'University Hall'
    ]
    
    # Timing
    SESSION_TIMEOUT_HOURS = 3
    CONVERSATION_HISTORY_LIMIT = 20
    MATCHING_WINDOW_MINUTES = 30
    DELIVERY_NOTIFICATION_DELAY_SECONDS = 50
    
    @classmethod
    def validate_config(cls) -> List[str]:
        """Validate required configuration"""
        missing = []
        
        required_fields = [
            'TWILIO_ACCOUNT_SID',
            'TWILIO_AUTH_TOKEN', 
            'TWILIO_PHONE_NUMBER',
            'ANTHROPIC_API_KEY',
            'FIREBASE_SERVICE_ACCOUNT_JSON'
        ]
        
        for field in required_fields:
            if not getattr(cls, field):
                missing.append(field)
        
        return missing
