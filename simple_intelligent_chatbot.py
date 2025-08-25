# simple_intelligent_chatbot.py
"""
Simplified SMS chatbot that replaces the complex workflow system.
Single intelligent agent with comprehensive knowledge of the entire system.
"""

import json
from typing import Dict, Any, Optional
from datetime import datetime
from smart_session_manager import SmartSessionManager
from intelligent_matching import IntelligentMatcher
from delivery_system import DeliverySystem


class SimpleIntelligentChatbot:
    """Single intelligent chatbot that handles ALL SMS conversations"""
    
    def __init__(self, llm, db, session_manager: SmartSessionManager, 
                 intelligent_matcher: IntelligentMatcher, delivery_system: DeliverySystem, 
                 send_sms_func):
        self.llm = llm
        self.db = db
        self.session_manager = session_manager
        self.intelligent_matcher = intelligent_matcher
        self.delivery_system = delivery_system
        self.send_sms = send_sms_func
    
    def handle_message(self, user_phone: str, message: str) -> Dict[str, Any]:
        """Handle any incoming SMS message with full intelligence"""
        
        print(f"🤖 Simple chatbot handling: {user_phone} -> {message}")
        
        try:
            # Get user context for conversation continuity
            context = self.session_manager.get_user_context(user_phone)
            
            # Handle payment trigger immediately (highest priority)
            if message.lower().strip() == "pay":
                return self._handle_payment(user_phone, context)
            
            # Generate intelligent response using comprehensive prompt
            response = self._generate_intelligent_response(user_phone, message, context)
            
            # Send response to user
            if response.get('sms_message'):
                success = self.send_sms(user_phone, response['sms_message'])
                print(f"📤 SMS sent: {'✅' if success else '❌'}")
            
            # Update conversation memory
            self.session_manager.update_user_context(context, message)
            
            return {
                "status": "success",
                "action": response.get("action", "conversation"),
                "response_sent": bool(response.get('sms_message'))
            }
            
        except Exception as e:
            print(f"❌ Chatbot error: {e}")
            # Fallback response
            self.send_sms(user_phone, "Sorry, I'm having technical difficulties. Please try again!")
            return {"status": "error", "error": str(e)}
    
    def _generate_intelligent_response(self, user_phone: str, message: str, context) -> Dict[str, Any]:
        """Generate intelligent response using comprehensive system knowledge"""
        
        # Build comprehensive prompt with all system knowledge
        system_prompt = self._build_comprehensive_prompt(user_phone, message, context)
        
        try:
            response = self.llm.invoke([{"role": "user", "content": system_prompt}])
            response_text = response.content.strip()
            
            # Parse JSON response
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
            
            # Execute any actions the AI determined
            if result.get("action") == "start_matching":
                self._execute_matching(user_phone, result)
            elif result.get("action") == "update_session":
                self._update_user_session(user_phone, result, context)
            
            return result
            
        except Exception as e:
            print(f"❌ AI response failed: {e}")
            return {
                "sms_message": "I'm here to help with food delivery! Tell me what restaurant you want and where to deliver it. 🍕",
                "action": "fallback_response"
            }
    
    def _build_comprehensive_prompt(self, user_phone: str, message: str, context) -> str:
        """Build comprehensive prompt with ALL system knowledge"""
        
        return f"""You are Pangea's intelligent SMS food delivery coordinator. You help students coordinate group food orders.

**CURRENT USER:** {user_phone}
**USER MESSAGE:** "{message}"
**TIMESTAMP:** {datetime.now()}

**USER CONTEXT:**
- Session Type: {context.session_type}
- Current Food Request: {context.current_food_request}
- Active Order: {context.active_order_session}
- Pending Invites: {context.pending_group_invites}
- Recent Messages: {context.conversation_memory[-3:] if context.conversation_memory else "None"}

**SYSTEM OVERVIEW:**
Pangea coordinates group food deliveries to save money. Users request food, get matched with others, then everyone orders individually and splits delivery costs.

**AVAILABLE RESTAURANTS:**
- McDonald's, Chipotle, Chick-fil-A, Portillo's, Starbucks

**DELIVERY LOCATIONS:**
- Richard J Daley Library (main location)
- Student Center East, Student Center West  
- Student Services Building, University Hall

**CORE WORKFLOW:**
1. User requests: restaurant + location + time → Find matches → Order process → Payment
2. Groups: 2-person groups (real matches) or 1-person "fake matches" (solo orders)
3. Pricing: $4.50 for groups, $3.50 for solo orders

**MATCHING RULES:**
- Find users with same restaurant + location + compatible time
- Time compatibility: "now"+"now", "1pm"+"lunch", "10pm"+"9:30-10pm" etc.
- If no real match found, create fake match (solo order disguised as group)
- Silent upgrades: Convert solo orders to real groups when possible

**ORDER PROCESS:**
After matching, users must provide:
1. Name for pickup coordination
2. Order details: "order number OR name AND what they ordered"
3. Pay command: User texts "PAY" to complete

**PAYMENT SYSTEM:**
- Text "PAY" triggers payment processing and delivery scheduling
- Only works if user has provided required order info
- Schedules delivery based on requested time

**CONVERSATION RULES:**
- Be conversational, excited, and helpful
- Detect missing info dynamically (restaurant? location? order details? name?)
- Ask for one missing piece at a time, naturally
- Handle edge cases gracefully
- Remember conversation context
- Use emojis appropriately

**RESPONSE FORMAT:**
Return JSON with:
{{
    "sms_message": "Response to send to user",
    "action": "conversation|start_matching|update_session|request_info",
    "session_updates": {{
        "session_type": "idle|food_request|order_process",
        "current_food_request": {{"restaurant": "X", "location": "Y", "delivery_time": "Z"}},
        "notes": "any internal notes"
    }},
    "matching_data": {{
        "restaurant": "restaurant name",
        "location": "location name", 
        "delivery_time": "time string"
    }}
}}

**EXAMPLES:**

User: "I want McDonald's at the library at 10pm"
→ Extract: McDonald's, library, 10pm → Start matching → Respond with match results

User: "My name is Jake"  
→ In order process → Store name → Ask for order details

User: "Cancel"
→ Clear sessions → Friendly cancellation message

**DYNAMIC INTELLIGENCE REQUIRED:**
- Detect what info is missing and ask naturally
- Handle typos and variations in restaurant/location names  
- Understand time expressions ("now", "lunch", "around 2pm", "between 9-10pm")
- Recognize when user is providing order details vs starting new request
- Balance being helpful vs not overwhelming user

Generate the appropriate response for this user's message right now."""

    def _execute_matching(self, user_phone: str, ai_response: Dict[str, Any]):
        """Execute matching based on AI's determination"""
        
        matching_data = ai_response.get("matching_data", {})
        restaurant = matching_data.get("restaurant")
        location = matching_data.get("location") 
        delivery_time = matching_data.get("delivery_time", "now")
        
        if not restaurant or not location:
            print("❌ Cannot start matching - missing restaurant or location")
            return
        
        print(f"🔍 AI triggered matching: {restaurant} at {location} ({delivery_time})")
        
        # Use existing matching system
        match_result = self.intelligent_matcher.find_compatible_matches(
            user_phone, restaurant, location, delivery_time
        )
        
        if match_result["has_real_match"]:
            # Real match found
            if match_result.get("is_silent_upgrade"):
                # Silent upgrade of existing solo order
                best_match = match_result["matches"][0]
                group_id = self.intelligent_matcher.create_silent_upgrade_group(
                    user_phone, best_match['user_phone'], restaurant, location, 
                    delivery_time, best_match.get('group_id')
                )
            else:
                # New 2-person group
                best_match = match_result["matches"][0]
                group_id = self.intelligent_matcher.create_group_match(
                    user_phone, best_match['user_phone'], restaurant, location, delivery_time
                )
            
            # Start order process
            if group_id:
                self.session_manager.start_order_process(user_phone, group_id, restaurant, 2)
                
        else:
            # No real match - create fake match (solo order)
            group_id = self.intelligent_matcher.create_fake_match(
                user_phone, restaurant, location, delivery_time
            )
            if group_id:
                self.session_manager.start_order_process(user_phone, group_id, restaurant, 1)
    
    def _update_user_session(self, user_phone: str, ai_response: Dict[str, Any], context):
        """Update user session based on AI's determination"""
        
        session_updates = ai_response.get("session_updates", {})
        
        if "session_type" in session_updates:
            context.session_type = session_updates["session_type"]
        
        if "current_food_request" in session_updates:
            context.current_food_request = session_updates["current_food_request"]
        
        self.session_manager.update_user_context(context)
        print(f"✅ Updated session for {user_phone}")
    
    def _handle_payment(self, user_phone: str, context) -> Dict[str, Any]:
        """Handle PAY command (unchanged from original system)"""
        
        print(f"💳 Processing PAY command from {user_phone}")
        
        # Import original payment processor
        try:
            from pangea_order_processor import process_payment_message
            result = process_payment_message(user_phone, "PAY")
            
            return {
                "status": "success",
                "action": "payment_processed",
                "response_sent": True
            }
            
        except Exception as e:
            print(f"❌ Payment processing failed: {e}")
            self.send_sms(user_phone, "Payment processing failed. Please try again or contact support.")
            return {"status": "error", "error": str(e)}