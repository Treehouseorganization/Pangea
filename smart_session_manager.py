# smart_session_manager.py
"""
Smart Session Manager with contextual memory
Prevents old order confusion while maintaining user context
"""

import json
from datetime import datetime, timedelta
from typing import Dict, Optional, List
from dataclasses import dataclass
import uuid
from langchain_core.tools import tool

@dataclass
class UserContext:
    """Rich user context with memory and session tracking"""
    user_phone: str
    current_session_id: str
    session_type: str  # "idle", "food_request", "order_process", "group_pending"
    conversation_memory: List[Dict]  # Recent conversation history
    current_food_request: Optional[Dict] = None
    active_order_session: Optional[Dict] = None
    pending_group_invites: List[Dict] = None
    user_preferences: Dict = None
    last_activity: datetime = None
    
    def __post_init__(self):
        if not self.last_activity:
            self.last_activity = datetime.now()
        if not self.conversation_memory:
            self.conversation_memory = []
        if not self.pending_group_invites:
            self.pending_group_invites = []
        if not self.user_preferences:
            self.user_preferences = {}

class SmartSessionManager:
    """Manages user sessions with intelligent context awareness"""
    
    def __init__(self, db, anthropic_llm):
        self.db = db
        self.llm = anthropic_llm
    
    def get_user_context(self, user_phone: str) -> UserContext:
        """Get comprehensive user context with memory"""
        try:
            # Get user session
            session_doc = self.db.collection('user_sessions').document(user_phone).get()
            
            # Get user preferences
            user_doc = self.db.collection('users').document(user_phone).get()
            user_prefs = user_doc.to_dict() if user_doc.exists else {}
            
            # Get active order session
            order_session = self._get_active_order_session(user_phone)
            
            # Get pending group invites
            pending_invites = self._get_pending_group_invites(user_phone)
            
            # Get recent conversation history
            conversation_memory = self._get_conversation_memory(user_phone)
            
            if session_doc.exists:
                session_data = session_doc.to_dict()
                
                # Check if session is stale
                if self._is_session_stale(session_data):
                    print(f"🕐 Session stale for {user_phone}, creating fresh context")
                    return self._create_fresh_context(user_phone, user_prefs, conversation_memory)
                
                return UserContext(
                    user_phone=user_phone,
                    current_session_id=session_data.get('session_id', str(uuid.uuid4())),
                    session_type=session_data.get('session_type', 'idle'),
                    conversation_memory=conversation_memory,
                    current_food_request=session_data.get('current_food_request'),
                    active_order_session=order_session,
                    pending_group_invites=pending_invites,
                    user_preferences=user_prefs,
                    last_activity=session_data.get('last_activity', datetime.now())
                )
            else:
                return self._create_fresh_context(user_phone, user_prefs, conversation_memory)
                
        except Exception as e:
            print(f"❌ Error getting user context: {e}")
            return self._create_fresh_context(user_phone, {}, [])
    
    def update_user_context(self, context: UserContext, new_message: str = None) -> bool:
        """Update user context with new information"""
        try:
            context.last_activity = datetime.now()
            
            # Add message to conversation memory
            if new_message:
                context.conversation_memory.append({
                    'message': new_message,
                    'timestamp': datetime.now(),
                    'session_type': context.session_type
                })
                
                # Keep only last 10 messages for memory
                context.conversation_memory = context.conversation_memory[-10:]
            
            # Store in database
            session_data = {
                'user_phone': context.user_phone,
                'session_id': context.current_session_id,
                'session_type': context.session_type,
                'current_food_request': context.current_food_request,
                'last_activity': context.last_activity,
                'conversation_memory': context.conversation_memory[-5:]  # Store last 5 for context
            }
            
            self.db.collection('user_sessions').document(context.user_phone).set(session_data)
            return True
            
        except Exception as e:
            print(f"❌ Error updating user context: {e}")
            return False
    
    def detect_new_food_request(self, user_phone: str, message: str) -> Dict:
        """Use Claude to intelligently detect if this is a new food request"""
        
        context = self.get_user_context(user_phone)
        
        detection_prompt = f"""Analyze this message to determine if the user is making a NEW food request or continuing their current session.

USER MESSAGE: "{message}"

CURRENT USER CONTEXT:
- Session type: {context.session_type}
- Current food request: {context.current_food_request}
- Active order session: {context.active_order_session is not None}
- Recent conversation: {context.conversation_memory[-3:] if context.conversation_memory else []}

DETECTION RULES:
1. NEW FOOD REQUEST if user mentions:
   - Different restaurant than current request
   - Different location than current request  
   - Clear intent to start over ("I want...", "Let me order...", "Can I get...")
   - Expressions of changing mind ("Actually I want...", "Let me try...")

2. CONTINUE CURRENT SESSION if user is:
   - Providing missing info (restaurant/location when asked)
   - Responding to group invitation (yes/no)
   - In order process (providing order details, payment)
   - Asking questions about current request

3. GENERAL CONVERSATION if user is:
   - Asking FAQ questions
   - Making general statements
   - Saying greetings

Return JSON:
{{
    "is_new_food_request": true/false,
    "confidence": "high/medium/low",
    "reasoning": "explanation of decision",
    "extracted_request": {{"restaurant": "...", "location": "...", "time": "..."}} or null,
    "should_clear_session": true/false
}}

Examples:
- "I want Chipotle at the library" → new_food_request: true (clear new request)
- "Yes" (when they have pending invite) → new_food_request: false (group response)
- "My order number is ABC123" (when in order process) → new_food_request: false (continuing order)
- "Actually, let me get McDonald's instead" → new_food_request: true (changing mind)

Return ONLY valid JSON."""
        
        try:
            response = self.llm.invoke([{"role": "user", "content": detection_prompt}])
            response_text = response.content.strip()
            
            # Clean JSON response
            if '```json' in response_text:
                start = response_text.find('{')
                end = response_text.rfind('}') + 1
                response_text = response_text[start:end]
            elif '```' in response_text:
                response_text = response_text.replace('```', '').strip()
            
            if not response_text.startswith('{'):
                import re
                json_match = re.search(r'\{.*\}', response_text, re.DOTALL)
                if json_match:
                    response_text = json_match.group()
            
            result = json.loads(response_text)
            
            print(f"🔍 New request detection: {result.get('is_new_food_request')} (confidence: {result.get('confidence')})")
            print(f"   Reasoning: {result.get('reasoning')}")
            
            return result
            
        except Exception as e:
            print(f"❌ New request detection failed: {e}")
            print(f"❌ Error type: {type(e).__name__}")
            print(f"❌ Error details: {str(e)}")
            import traceback
            print("❌ Full traceback:")
            traceback.print_exc()
            
            # Fallback: simple keyword detection
            return self._simple_new_request_detection(message, context)
    
    def start_fresh_food_request(self, user_phone: str, restaurant: str = None, location: str = None, delivery_time: str = "now") -> UserContext:
        """Start completely fresh food request session"""
        
        # Clear any old sessions
        self._clear_old_sessions(user_phone)
        
        # Create new context
        context = UserContext(
            user_phone=user_phone,
            current_session_id=str(uuid.uuid4()),
            session_type="food_request",
            conversation_memory=[],
            current_food_request={
                "restaurant": restaurant,
                "location": location,
                "delivery_time": delivery_time,
                "timestamp": datetime.now(),
                "session_id": str(uuid.uuid4())
            }
        )
        
        self.update_user_context(context)
        print(f"✅ Started fresh food request for {user_phone}: {restaurant} at {location}")
        return context
    
    def transition_to_order_process(self, user_phone: str, group_id: str, restaurant: str, group_size: int) -> bool:
        """Transition user to order process cleanly"""
        
        try:
            context = self.get_user_context(user_phone)
            context.session_type = "order_process"
            context.active_order_session = {
                "group_id": group_id,
                "restaurant": restaurant,
                "group_size": group_size,
                "order_stage": "need_order_number",
                "created_at": datetime.now(),
                "order_number": None,
                "customer_name": None,
                "payment_timestamp": None
            }
            
            # Also update the order_sessions collection for compatibility
            from pangea_order_processor import update_order_session
            order_session_data = {
                'user_phone': user_phone,
                'group_id': group_id,
                'restaurant': restaurant,
                'group_size': group_size,
                'delivery_time': context.current_food_request.get('delivery_time', 'now') if context.current_food_request else 'now',
                'order_stage': 'need_order_number',
                'created_at': datetime.now(),
                'session_id': context.current_session_id
            }
            update_order_session(user_phone, order_session_data)
            
            self.update_user_context(context)
            print(f"✅ Transitioned {user_phone} to order process: group {group_id}")
            return True
            
        except Exception as e:
            print(f"❌ Error transitioning to order process: {e}")
            return False
    
    def _create_fresh_context(self, user_phone: str, user_prefs: Dict, conversation_memory: List) -> UserContext:
        """Create fresh idle context"""
        return UserContext(
            user_phone=user_phone,
            current_session_id=str(uuid.uuid4()),
            session_type="idle",
            conversation_memory=conversation_memory[-5:],  # Keep recent memory
            user_preferences=user_prefs
        )
    
    def _is_session_stale(self, session_data: Dict) -> bool:
        """Check if session is older than 2 hours"""
        try:
            last_activity = session_data.get('last_activity')
            if not last_activity:
                return True
            
            if hasattr(last_activity, 'tzinfo') and last_activity.tzinfo:
                last_activity = last_activity.replace(tzinfo=None)
            
            return datetime.now() - last_activity > timedelta(hours=2)
            
        except Exception:
            return True
    
    def _get_active_order_session(self, user_phone: str) -> Optional[Dict]:
        """Get active order session if exists"""
        try:
            from pangea_order_processor import get_user_order_session
            return get_user_order_session(user_phone)
        except Exception:
            return None
    
    def _get_pending_group_invites(self, user_phone: str) -> List[Dict]:
        """Get pending group invitations"""
        try:
            invites = []
            
            # Check negotiations
            negotiations = self.db.collection('negotiations')\
                .where('to_user', '==', user_phone)\
                .where('status', '==', 'pending')\
                .get()
            
            for neg in negotiations:
                invites.append({
                    'type': 'negotiation',
                    'data': neg.to_dict()
                })
            
            # Check active groups
            groups = self.db.collection('active_groups')\
                .where('members', 'array_contains', user_phone)\
                .where('status', 'in', ['pending_responses', 'forming'])\
                .get()
            
            for group in groups:
                invites.append({
                    'type': 'group',
                    'data': group.to_dict()
                })
            
            return invites
            
        except Exception as e:
            print(f"❌ Error getting pending invites: {e}")
            return []
    
    def _get_conversation_memory(self, user_phone: str) -> List[Dict]:
        """Get recent conversation history"""
        try:
            # Get from user_sessions or create empty
            session_doc = self.db.collection('user_sessions').document(user_phone).get()
            if session_doc.exists:
                return session_doc.to_dict().get('conversation_memory', [])
            return []
        except Exception:
            return []
    
    def _clear_old_sessions(self, user_phone: str):
        """Clear old sessions when starting fresh"""
        try:
            # Clear user session
            self.db.collection('user_sessions').document(user_phone).delete()
            
            # Clear old order sessions
            old_orders = self.db.collection('order_sessions')\
                .where('user_phone', '==', user_phone)\
                .get()
            
            for order in old_orders:
                order.reference.delete()
                print(f"🗑️ Cleared old order session for {user_phone}")
            
            # Cancel old negotiations
            old_negotiations = self.db.collection('negotiations')\
                .where('to_user', '==', user_phone)\
                .where('status', '==', 'pending')\
                .get()
            
            for neg in old_negotiations:
                neg.reference.update({'status': 'cancelled_new_request'})
                print(f"🗑️ Cancelled old negotiation for {user_phone}")
                
        except Exception as e:
            print(f"❌ Error clearing old sessions: {e}")
    
    def _simple_new_request_detection(self, message: str, context: UserContext) -> Dict:
        """Fallback detection logic"""
        
        message_lower = message.lower()
        
        # Strong indicators of new request
        new_request_phrases = ['i want', 'can i get', 'let me order', 'actually i want', 'instead']
        restaurants = ['chipotle', 'mcdonalds', 'chick-fil-a', 'portillos', 'starbucks']
        
        has_new_phrase = any(phrase in message_lower for phrase in new_request_phrases)
        has_restaurant = any(rest in message_lower for rest in restaurants)
        
        # If user is idle or mentions restaurant with intent phrase, likely new request
        is_new = (context.session_type == "idle" and (has_new_phrase or has_restaurant)) or \
                 (has_new_phrase and has_restaurant)
        
        return {
            "is_new_food_request": is_new,
            "confidence": "medium",
            "reasoning": f"Fallback detection: new_phrase={has_new_phrase}, restaurant={has_restaurant}",
            "extracted_request": None,
            "should_clear_session": is_new
        }
