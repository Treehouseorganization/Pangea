"""
Matching Engine Wrapper
Integrates existing matching logic with new architecture
"""

from typing import Dict, List, Optional
from datetime import datetime, timedelta
import uuid

# Import existing matching logic (keep as-is)
from intelligent_matching import IntelligentMatcher

class MatchingEngine:
    """Wrapper for existing matching logic with new architecture integration"""
    
    def __init__(self, db, anthropic_llm):
        self.db = db
        # Use existing intelligent matcher
        self.matcher = IntelligentMatcher(db, anthropic_llm)
        
    def find_compatible_matches(self, user_phone: str, restaurant: str, location: str, delivery_time: str) -> Dict:
        """Find compatible matches using existing logic"""
        return self.matcher.find_compatible_matches(user_phone, restaurant, location, delivery_time)
    
    def create_group_match(self, user1_phone: str, user2_phone: str, restaurant: str, location: str, optimal_time: str) -> str:
        """Create a real 2-person group match"""
        return self.matcher.create_group_match(user1_phone, user2_phone, restaurant, location, optimal_time)
    
    def create_silent_upgrade_group(self, new_user_phone: str, solo_user_phone: str, restaurant: str, location: str, optimal_time: str, existing_group_id: str) -> str:
        """Upgrade solo order to real 2-person group silently"""
        return self.matcher.create_silent_upgrade_group(new_user_phone, solo_user_phone, restaurant, location, optimal_time, existing_group_id)
    
    def create_fake_match(self, user_phone: str, restaurant: str, location: str, delivery_time: str) -> str:
        """Create a fake match (solo order disguised as group)"""
        return self.matcher.create_fake_match(user_phone, restaurant, location, delivery_time)

