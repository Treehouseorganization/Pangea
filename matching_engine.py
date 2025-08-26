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
        print(f"🔍 MATCHING ENGINE SEARCH:")
        print(f"   👤 User: {user_phone}")
        print(f"   🍕 Restaurant: {restaurant}")
        print(f"   📍 Location: {location}")
        print(f"   🕒 Time: {delivery_time}")
        
        result = self.matcher.find_compatible_matches(user_phone, restaurant, location, delivery_time)
        
        print(f"   📋 SEARCH RESULTS:")
        print(f"      Has Real Match: {result.get('has_real_match', False)}")
        print(f"      Total Matches: {len(result.get('matches', []))}")
        print(f"      Is Silent Upgrade: {result.get('is_silent_upgrade', False)}")
        if result.get('matches'):
            for i, match in enumerate(result['matches'][:3], 1):  # Show first 3
                print(f"      Match {i}: {match.get('user_phone', 'unknown')} - {match.get('compatibility_score', 'N/A')} score")
        
        return result
    
    def create_group_match(self, user1_phone: str, user2_phone: str, restaurant: str, location: str, optimal_time: str) -> str:
        """Create a real 2-person group match"""
        print(f"👥 CREATING GROUP MATCH:")
        print(f"   User 1: {user1_phone}")
        print(f"   User 2: {user2_phone}")
        print(f"   Restaurant: {restaurant}")
        print(f"   Location: {location}")
        print(f"   Time: {optimal_time}")
        
        group_id = self.matcher.create_group_match(user1_phone, user2_phone, restaurant, location, optimal_time)
        print(f"   ✅ Group Created: {group_id}")
        
        return group_id
    
    def create_silent_upgrade_group(self, new_user_phone: str, solo_user_phone: str, restaurant: str, location: str, optimal_time: str, existing_group_id: str) -> str:
        """Upgrade solo order to real 2-person group silently"""
        print(f"🔄 SILENT UPGRADE TO GROUP:")
        print(f"   New User: {new_user_phone}")
        print(f"   Solo User: {solo_user_phone}")
        print(f"   Existing Group ID: {existing_group_id}")
        print(f"   Restaurant: {restaurant}")
        print(f"   Location: {location}")
        
        group_id = self.matcher.create_silent_upgrade_group(new_user_phone, solo_user_phone, restaurant, location, optimal_time, existing_group_id)
        print(f"   ✅ Silent Upgrade Complete: {group_id}")
        
        return group_id
    
    def create_fake_match(self, user_phone: str, restaurant: str, location: str, delivery_time: str) -> str:
        """Create a fake match (solo order disguised as group)"""
        print(f"🎭 CREATING FAKE MATCH:")
        print(f"   User: {user_phone}")
        print(f"   Restaurant: {restaurant}")
        print(f"   Location: {location}")
        print(f"   Time: {delivery_time}")
        
        group_id = self.matcher.create_fake_match(user_phone, restaurant, location, delivery_time)
        print(f"   ✅ Fake Match Created: {group_id}")
        print(f"   💡 Solo order disguised as group to reduce delivery fee")
        
        return group_id

