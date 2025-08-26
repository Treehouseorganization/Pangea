"""
Memory Manager
Handles persistent user state storage and retrieval with proper session management
"""

from typing import Dict, List, Optional
from datetime import datetime, timedelta
import uuid
from main import UserState, OrderStage

class MemoryManager:
    """Manages user state persistence and retrieval"""
    
    def __init__(self, db):
        self.db = db
        self.collection_name = 'user_states'
    
    async def get_user_state(self, user_phone: str) -> UserState:
        """Get user state with proper session management"""
        try:
            # Get user state document
            state_doc = self.db.collection(self.collection_name).document(user_phone).get()
            
            if state_doc.exists:
                state_data = state_doc.to_dict()
                
                # Check if state is stale (older than 3 hours)
                if self._is_state_stale(state_data):
                    print(f"🕐 State stale for {user_phone}, creating fresh state")
                    return self._create_fresh_state(user_phone)
                
                # Convert back to UserState
                user_state = UserState.from_dict(state_data)
                
                # Update last activity
                user_state.last_activity = datetime.now()
                
                return user_state
            else:
                # Create new user state
                return self._create_fresh_state(user_phone)
                
        except Exception as e:
            print(f"❌ Error getting user state for {user_phone}: {e}")
            return self._create_fresh_state(user_phone)
    
    async def save_user_state(self, user_state: UserState) -> bool:
        """Save user state to database"""
        try:
            # Update last activity
            user_state.last_activity = datetime.now()
            
            # Trim conversation history to keep it manageable
            if len(user_state.conversation_history) > 20:
                user_state.conversation_history = user_state.conversation_history[-15:]
            
            # Convert to dict and save
            state_data = user_state.to_dict()
            
            self.db.collection(self.collection_name).document(user_state.user_phone).set(state_data)
            
            # Also maintain group membership if user is in a group
            if user_state.group_id:
                await self._update_group_membership(user_state)
            
            return True
            
        except Exception as e:
            print(f"❌ Error saving user state for {user_state.user_phone}: {e}")
            return False
    
    async def get_group_members(self, group_id: str) -> List[UserState]:
        """Get all members of a group"""
        try:
            # Query all users with this group_id
            group_members = self.db.collection(self.collection_name)\
                .where('group_id', '==', group_id)\
                .get()
            
            members = []
            for member_doc in group_members:
                member_data = member_doc.to_dict()
                member_state = UserState.from_dict(member_data)
                members.append(member_state)
            
            return members
            
        except Exception as e:
            print(f"❌ Error getting group members for {group_id}: {e}")
            return []
    
    async def clear_user_state(self, user_phone: str) -> bool:
        """Clear user state completely"""
        try:
            # Get current state first to handle group cleanup
            user_state = await self.get_user_state(user_phone)
            
            # If user is in a group, handle group cleanup
            if user_state.group_id:
                await self._handle_group_cleanup(user_state)
            
            # Delete user state
            self.db.collection(self.collection_name).document(user_phone).delete()
            
            print(f"✅ Cleared user state for {user_phone}")
            return True
            
        except Exception as e:
            print(f"❌ Error clearing user state for {user_phone}: {e}")
            return False
    
    async def get_users_by_stage(self, stage: OrderStage) -> List[UserState]:
        """Get all users in a specific stage"""
        try:
            users = self.db.collection(self.collection_name)\
                .where('stage', '==', stage.value)\
                .get()
            
            user_states = []
            for user_doc in users:
                user_data = user_doc.to_dict()
                user_state = UserState.from_dict(user_data)
                user_states.append(user_state)
            
            return user_states
            
        except Exception as e:
            print(f"❌ Error getting users by stage {stage}: {e}")
            return []
    
    async def cleanup_stale_states(self) -> int:
        """Clean up stale user states"""
        try:
            cutoff_time = datetime.now() - timedelta(hours=6)
            
            # Get all states older than cutoff
            stale_states = self.db.collection(self.collection_name)\
                .where('last_activity', '<', cutoff_time.isoformat())\
                .get()
            
            cleaned_count = 0
            for state_doc in stale_states:
                state_doc.reference.delete()
                cleaned_count += 1
            
            print(f"🧹 Cleaned up {cleaned_count} stale user states")
            return cleaned_count
            
        except Exception as e:
            print(f"❌ Error cleaning up stale states: {e}")
            return 0
    
    def _create_fresh_state(self, user_phone: str) -> UserState:
        """Create fresh user state"""
        return UserState(
            user_phone=user_phone,
            session_id=str(uuid.uuid4()),
            stage=OrderStage.IDLE,
            conversation_history=[],
            last_activity=datetime.now(),
            missing_info=[]
        )
    
    def _is_state_stale(self, state_data: Dict) -> bool:
        """Check if state is stale (older than 3 hours)"""
        try:
            last_activity_str = state_data.get('last_activity')
            if not last_activity_str:
                return True
            
            last_activity = datetime.fromisoformat(last_activity_str)
            return datetime.now() - last_activity > timedelta(hours=3)
            
        except Exception:
            return True
    
    async def _update_group_membership(self, user_state: UserState):
        """Update group membership information"""
        try:
            if not user_state.group_id:
                return
            
            # Store group information for matching engine compatibility
            group_data = {
                'group_id': user_state.group_id,
                'restaurant': user_state.restaurant,
                'location': user_state.location,
                'delivery_time': user_state.delivery_time,
                'group_size': user_state.group_size,
                'is_fake_match': user_state.is_fake_match,
                'members': [user_state.user_phone],  # Will be updated when we get all members
                'last_updated': datetime.now()
            }
            
            # Get all group members to update member list
            all_members = await self.get_group_members(user_state.group_id)
            group_data['members'] = [member.user_phone for member in all_members]
            group_data['group_size'] = len(all_members)
            
            # Store in groups collection for matching engine compatibility
            self.db.collection('active_groups').document(user_state.group_id).set(group_data, merge=True)
            
        except Exception as e:
            print(f"❌ Error updating group membership: {e}")
    
    async def _handle_group_cleanup(self, user_state: UserState):
        """Handle cleanup when user leaves a group"""
        try:
            if not user_state.group_id:
                return
            
            # Get all group members
            group_members = await self.get_group_members(user_state.group_id)
            
            # If this is the last member, clean up the group
            if len(group_members) <= 1:
                # Delete from active_groups
                self.db.collection('active_groups').document(user_state.group_id).delete()
                print(f"🗑️ Deleted empty group {user_state.group_id}")
            else:
                # Update group membership
                remaining_members = [
                    member.user_phone for member in group_members 
                    if member.user_phone != user_state.user_phone
                ]
                
                self.db.collection('active_groups').document(user_state.group_id).update({
                    'members': remaining_members,
                    'group_size': len(remaining_members)
                })
                print(f"🔄 Updated group {user_state.group_id} member count")
            
        except Exception as e:
            print(f"❌ Error handling group cleanup: {e}")
    
    async def find_users_for_matching(self, restaurant: str, location: str, excluding_user: str = None) -> List[UserState]:
        """Find users who might be available for matching"""
        try:
            # Find users in WAITING_FOR_MATCH stage with same restaurant/location
            potential_matches = self.db.collection(self.collection_name)\
                .where('stage', '==', OrderStage.WAITING_FOR_MATCH.value)\
                .where('restaurant', '==', restaurant)\
                .where('location', '==', location)\
                .get()
            
            matches = []
            for match_doc in potential_matches:
                match_data = match_doc.to_dict()
                user_phone = match_data.get('user_phone')
                
                # Skip the requesting user
                if user_phone == excluding_user:
                    continue
                
                # Check if request is recent (within 30 minutes)
                last_activity_str = match_data.get('last_activity')
                if last_activity_str:
                    last_activity = datetime.fromisoformat(last_activity_str)
                    if datetime.now() - last_activity > timedelta(minutes=30):
                        continue  # Too old
                
                user_state = UserState.from_dict(match_data)
                matches.append(user_state)
            
            return matches
            
        except Exception as e:
            print(f"❌ Error finding users for matching: {e}")
            return []
    
    async def find_solo_orders_for_upgrade(self, restaurant: str, location: str, excluding_user: str = None) -> List[UserState]:
        """Find solo orders (fake matches) that can be upgraded to real groups"""
        try:
            # Find users with fake matches that are recent
            solo_orders = self.db.collection(self.collection_name)\
                .where('is_fake_match', '==', True)\
                .where('restaurant', '==', restaurant)\
                .where('location', '==', location)\
                .where('group_size', '==', 1)\
                .get()
            
            upgradeable_solos = []
            cutoff_time = datetime.now() - timedelta(minutes=30)
            
            for solo_doc in solo_orders:
                solo_data = solo_doc.to_dict()
                user_phone = solo_data.get('user_phone')
                
                # Skip the requesting user
                if user_phone == excluding_user:
                    continue
                
                # Check if recent
                last_activity_str = solo_data.get('last_activity')
                if last_activity_str:
                    last_activity = datetime.fromisoformat(last_activity_str)
                    if last_activity < cutoff_time:
                        continue  # Too old
                
                # Check if they're still in a compatible stage
                stage = solo_data.get('stage')
                if stage in [OrderStage.MATCHED.value, OrderStage.COLLECTING_ORDER_INFO.value, OrderStage.READY_TO_PAY.value]:
                    user_state = UserState.from_dict(solo_data)
                    upgradeable_solos.append(user_state)
            
            return upgradeable_solos
            
        except Exception as e:
            print(f"❌ Error finding solo orders for upgrade: {e}")
            return []

