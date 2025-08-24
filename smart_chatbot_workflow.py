# smart_chatbot_workflow.py
"""
Smart Chatbot LangGraph Workflow
Feels like intelligent conversation while maintaining agent structure
"""

from langchain_core.messages import HumanMessage, AIMessage
from langgraph.graph import StateGraph, END
from langgraph.graph.message import add_messages
from langchain_core.tools import tool
from typing import Dict, List, Annotated
from typing_extensions import TypedDict
from datetime import datetime
import json

# State for our smart chatbot
class SmartChatbotState(TypedDict):
    messages: Annotated[List, add_messages]
    user_phone: str
    user_context: Dict  # Rich context from session manager
    current_intent: str  # What user wants to do
    extracted_data: Dict  # Extracted information
    response_message: str  # Response to send to user
    action_taken: str  # What action was performed
    needs_followup: bool  # Whether followup is needed

class SmartChatbotWorkflow:
    """Intelligent chatbot workflow using LangGraph and Claude tools"""
    
    def __init__(self, session_manager, matcher, anthropic_llm, send_sms_func):
        self.session_manager = session_manager
        self.matcher = matcher
        self.llm = anthropic_llm
        self.send_sms = send_sms_func
        self.workflow = self._create_workflow()
    
    def _create_workflow(self) -> StateGraph:
        """Create the smart chatbot workflow"""
        
        workflow = StateGraph(SmartChatbotState)
        
        # Add nodes
        workflow.add_node("understand_intent", self._understand_intent_node)
        workflow.add_node("handle_new_food_request", self._handle_new_food_request_node)
        workflow.add_node("handle_cancellation", self._handle_cancellation_node)
        workflow.add_node("handle_missing_info", self._handle_missing_info_node)
        workflow.add_node("find_matches", self._find_matches_node)
        workflow.add_node("handle_group_response", self._handle_group_response_node)
        workflow.add_node("handle_order_process", self._handle_order_process_node)
        workflow.add_node("handle_general_conversation", self._handle_general_conversation_node)
        workflow.add_node("send_response", self._send_response_node)
        
        # Entry point
        workflow.set_entry_point("understand_intent")
        
        # Smart routing based on intent
        workflow.add_conditional_edges(
            "understand_intent",
            self._route_by_intent,
            {
                "new_food_request": "handle_new_food_request",
                "cancellation": "handle_cancellation",
                "missing_info": "handle_missing_info", 
                "group_response": "handle_group_response",
                "order_process": "handle_order_process",
                "general_conversation": "handle_general_conversation"
            }
        )
        
        # Food request flow
        workflow.add_conditional_edges(
            "handle_new_food_request",
            lambda state: "find_matches" if not state.get('extracted_data', {}).get('missing_info') else "send_response",
            {
                "find_matches": "find_matches",
                "send_response": "send_response"
            }
        )
        
        workflow.add_edge("handle_missing_info", "find_matches")
        workflow.add_edge("find_matches", "send_response")
        workflow.add_edge("handle_cancellation", "send_response")
        workflow.add_edge("handle_group_response", "send_response")
        workflow.add_edge("handle_order_process", "send_response")
        workflow.add_edge("handle_general_conversation", "send_response")
        workflow.add_edge("send_response", END)
        
        return workflow.compile()
    
    def _understand_intent_node(self, state: SmartChatbotState) -> SmartChatbotState:
        """Understand user intent with full context awareness"""
        
        user_message = state['messages'][-1].content
        user_phone = state['user_phone']
        
        # Get rich user context
        context = self.session_manager.get_user_context(user_phone)
        state['user_context'] = context.__dict__
        
        print(f"🧠 Understanding intent for {user_phone}: '{user_message}'")
        print(f"📋 Context: {context.session_type}, active_order={context.active_order_session is not None}")
        
        # Always use Claude for intelligent intent analysis first
        intent_analysis = self._analyze_intent_with_claude(user_message, context)
        
        state['current_intent'] = intent_analysis['intent']
        state['extracted_data'] = intent_analysis.get('extracted_data', {})
        
        print(f"🎯 Intent: {state['current_intent']}")
        return state
    
    def _analyze_intent_with_claude(self, message: str, context) -> Dict:
        """Use Claude to analyze user intent with full context"""
        
        intent_prompt = f"""Analyze the user's intent based on their message and current context.

USER MESSAGE: "{message}"

CURRENT CONTEXT:
- Session type: {context.session_type}
- Has active order: {context.active_order_session is not None}
- Pending group invites: {len(context.pending_group_invites)}
- Recent conversation: {context.conversation_memory[-2:] if context.conversation_memory else []}

POSSIBLE INTENTS:
1. **new_food_request**: Starting fresh food order (even if in other session)
2. **cancellation**: Canceling/no longer wanting current food request/order
3. **missing_info**: Providing missing restaurant/location info for current request
4. **group_response**: Responding YES/NO to group invitation
5. **order_process**: In order flow (providing order details, payment)
6. **general_conversation**: Questions, help, or general chat

CONTEXT CLUES:
- If they mention restaurant + location + time → likely new_food_request
- If they express not wanting current request ("don't want", "never mind", "cancel") → cancellation
- If they say "yes"/"no" and have pending invites → group_response  
- If they provide order number/name and in order process → order_process
- If they ask questions about service → general_conversation
- If they're filling in previously missing info → missing_info

Return JSON:
{{
    "intent": "one of the 6 intents above",
    "confidence": "high/medium/low",
    "reasoning": "explanation of decision",
    "extracted_data": {{"any relevant extracted info"}} or {{}},
    "context_used": ["list of context factors that influenced decision"]
}}

Examples:
- "I want Chipotle at library" → intent: "new_food_request"
- "Actually I don't want that anymore" → intent: "cancellation"
- "Yes" (with pending invite) → intent: "group_response"
- "Order #ABC123" (in order process) → intent: "order_process"
- "What restaurants are available?" → intent: "general_conversation"

Return ONLY valid JSON."""
        
        try:
            response = self.llm.invoke([{"role": "user", "content": intent_prompt}])
            response_text = response.content.strip()
            
            # Clean JSON
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
            
            print(f"🤖 Intent analysis: {result.get('intent')} (confidence: {result.get('confidence')})")
            print(f"   Reasoning: {result.get('reasoning')}")
            
            return result
            
        except Exception as e:
            print(f"❌ Intent analysis failed: {e}")
            print(f"❌ Error type: {type(e).__name__}")
            print(f"❌ Error details: {str(e)}")
            import traceback
            print("❌ Full traceback:")
            traceback.print_exc()
            
            # Fallback logic
            return self._fallback_intent_analysis(message, context)
    
    def _fallback_intent_analysis(self, message: str, context) -> Dict:
        """Simple fallback intent analysis"""
        
        message_lower = message.lower()
        
        # Check for cancellation phrases
        cancellation_phrases = [
            "don't want", "dont want", "not want", "no longer want",
            "never mind", "nevermind", "cancel", "not anymore", 
            "change my mind", "changed my mind", "forget it",
            "actually no", "not interested"
        ]
        if any(phrase in message_lower for phrase in cancellation_phrases):
            return {"intent": "cancellation", "confidence": "high", "reasoning": "Detected cancellation phrases"}
        
        # Check for group responses
        if context.pending_group_invites and message_lower.strip() in ['yes', 'y', 'no', 'n', 'sure', 'ok']:
            return {"intent": "group_response", "confidence": "high", "reasoning": "Simple yes/no with pending invites"}
        
        # Check for order process
        if context.active_order_session:
            if 'pay' in message_lower:
                return {"intent": "order_process", "confidence": "high", "reasoning": "Payment request in order session"}
            elif any(word in message_lower for word in ['order', 'name', 'number']):
                return {"intent": "order_process", "confidence": "medium", "reasoning": "Order details in active session"}
        
        # Check for food requests (including restaurant/location/time changes)
        restaurants = ['chipotle', 'mcdonalds', 'chick-fil-a', 'portillos', 'starbucks']
        locations = ['library', 'student center', 'university hall', 'student services']
        food_words = ['want', 'craving', 'hungry', 'order']
        time_words = ['1pm', '2pm', '3pm', '4pm', '5pm', '11am', '12pm', '1:00', '2:00', '3:00', '4:00', '5:00', 'noon', 'morning', 'afternoon', 'evening', 'later', 'earlier']
        change_phrases = ['actually i want', 'actually i', 'instead', 'let me get', 'change to', 'deliver to', 'make it', 'change delivery time', 'change time', 'delivery time']
        
        # High confidence for explicit restaurant, location, or time changes
        has_change_phrase = any(phrase in message_lower for phrase in change_phrases)
        has_restaurant = any(rest in message_lower for rest in restaurants)
        has_location = any(loc in message_lower for loc in locations)
        has_time = any(time in message_lower for time in time_words)
        
        if has_change_phrase and (has_restaurant or has_location or has_time):
            change_type = []
            if has_restaurant: change_type.append("restaurant")
            if has_location: change_type.append("location")
            if has_time: change_type.append("time")
            return {"intent": "new_food_request", "confidence": "high", "reasoning": f"Change detected: {', '.join(change_type)}"}
        
        # Medium confidence for general food requests
        if any(rest in message_lower for rest in restaurants) or any(word in message_lower for word in food_words):
            return {"intent": "new_food_request", "confidence": "medium", "reasoning": "Food-related keywords"}
        
        # Default to general conversation
        return {"intent": "general_conversation", "confidence": "low", "reasoning": "No clear intent detected"}
    
    def _route_by_intent(self, state: SmartChatbotState) -> str:
        """Route to appropriate handler based on intent"""
        return state['current_intent']
    
    def _handle_new_food_request_node(self, state: SmartChatbotState) -> SmartChatbotState:
        """Handle new food request with extraction and validation"""
        
        user_message = state['messages'][-1].content
        user_phone = state['user_phone']
        
        print(f"🍕 Processing new food request from {user_phone}")
        
        # Extract food request details with Claude
        try:
            extracted = self._extract_food_request_details(user_message)
        except Exception as e:
            print(f"❌ Food request extraction failed: {e}")
            # Fallback: basic extraction from message
            extracted = self._basic_food_request_extraction(user_message)
        
        restaurant = extracted.get('restaurant')
        location = extracted.get('location')
        delivery_time = extracted.get('delivery_time', 'now')
        missing_info = extracted.get('missing_info', [])
        
        if missing_info:
            # Generate helpful response for missing info
            state['response_message'] = self._generate_missing_info_response(missing_info, restaurant, location)
            state['action_taken'] = "asked_for_missing_info"
            
            # Update context but don't start full request yet
            context = self.session_manager.get_user_context(user_phone)
            context.session_type = "food_request"
            context.current_food_request = {
                "restaurant": restaurant,
                "location": location,
                "delivery_time": delivery_time,
                "missing_info": missing_info
            }
            self.session_manager.update_user_context(context, user_message)
            
        else:
            # Complete request - start fresh food request
            self.session_manager.start_fresh_food_request(user_phone, restaurant, location, delivery_time)
            state['extracted_data'] = {
                "restaurant": restaurant,
                "location": location, 
                "delivery_time": delivery_time,
                "missing_info": []
            }
            state['action_taken'] = "started_food_request"
            
            # Store change context for contextual messaging
            user_message = state['messages'][-1].content.lower()
            state['change_context'] = {
                "is_change": any(phrase in user_message for phrase in ['actually', 'instead', 'change', 'make it']),
                "original_message": state['messages'][-1].content
            }
        
        return state
    
    def _handle_cancellation_node(self, state: SmartChatbotState) -> SmartChatbotState:
        """Handle order cancellation - clear current request and respond appropriately"""
        
        user_message = state['messages'][-1].content
        user_phone = state['user_phone']
        
        print(f"🚫 Processing cancellation from {user_phone}: '{user_message}'")
        
        # Get current context to see what they're canceling
        context = self.session_manager.get_user_context(user_phone)
        
        if context.session_type in ['food_request', 'order_process'] or context.active_order_session:
            # They have something active to cancel
            if context.active_order_session:
                response_message = "Got it! I've canceled your current order. No worries at all! 😊\n\nReady for something else? Just let me know what you're craving and I'll help you get it delivered! 🍴"
                print(f"📋 Canceled active order for {user_phone}")
            elif context.current_food_request:
                restaurant = context.current_food_request.get('restaurant', 'food')
                response_message = f"No problem! I've canceled your {restaurant} request. 😊\n\nWhenever you're ready for delivery, just tell me what you want and where you want it delivered! 🍴"
                print(f"🍽️ Canceled food request for {user_phone}: {restaurant}")
            else:
                response_message = "Sure thing! I've cleared everything out. 😊\n\nWhenever you're hungry, just let me know what you want delivered! 🍴"
                print(f"🧹 General cancellation for {user_phone}")
            
            # Clear all sessions and context
            self.session_manager.clear_user_session(user_phone)
            context = self.session_manager.get_user_context(user_phone)  # Get fresh context
            
        else:
            # Nothing specific to cancel
            response_message = "No worries! You don't have any active orders right now. 😊\n\nWhenever you're ready to order some food, just tell me what you want and where you want it delivered! 🍴"
            print(f"❓ Nothing to cancel for {user_phone}")
        
        # Update conversation memory
        self.session_manager.update_user_context(context, user_message, "cancellation")
        
        state['response_message'] = response_message
        state['action_taken'] = "cancelled_request"
        
        return state
    
    def _handle_missing_info_node(self, state: SmartChatbotState) -> SmartChatbotState:
        """Handle when user provides missing information"""
        
        user_message = state['messages'][-1].content
        user_phone = state['user_phone']
        
        print(f"📝 Processing missing info from {user_phone}")
        
        # Get current context
        context = self.session_manager.get_user_context(user_phone)
        current_request = context.current_food_request or {}
        
        # Extract new information
        extracted = self._extract_food_request_details(user_message, current_request)
        
        restaurant = extracted.get('restaurant') or current_request.get('restaurant')
        location = extracted.get('location') or current_request.get('location')
        delivery_time = extracted.get('delivery_time') or current_request.get('delivery_time', 'now')
        missing_info = extracted.get('missing_info', [])
        
        if missing_info:
            # Still missing info
            state['response_message'] = self._generate_missing_info_response(missing_info, restaurant, location)
            state['action_taken'] = "still_missing_info"
            
            # Update context
            context.current_food_request.update({
                "restaurant": restaurant,
                "location": location,
                "delivery_time": delivery_time,
                "missing_info": missing_info
            })
            self.session_manager.update_user_context(context, user_message)
        else:
            # Complete now - start matching
            self.session_manager.start_fresh_food_request(user_phone, restaurant, location, delivery_time)
            state['extracted_data'] = {
                "restaurant": restaurant,
                "location": location,
                "delivery_time": delivery_time,
                "missing_info": []
            }
            state['action_taken'] = "completed_food_request"
        
        return state
    
    def _find_matches_node(self, state: SmartChatbotState) -> SmartChatbotState:
        """Find matches using intelligent matching system"""
        
        user_phone = state['user_phone']
        extracted = state['extracted_data']
        
        restaurant = extracted['restaurant']
        location = extracted['location']
        delivery_time = extracted.get('delivery_time', 'now')
        
        print(f"🔍 Finding matches for {restaurant} at {location} ({delivery_time})")
        
        # Use intelligent matcher
        match_result = self.matcher.find_compatible_matches(user_phone, restaurant, location, delivery_time)
        
        if match_result['has_real_match']:
            best_match = match_result['matches'][0]
            match_phone = best_match['user_phone']
            
            # Check if this is a silent upgrade scenario
            if match_result.get('is_silent_upgrade'):
                print(f"🤫 Silent upgrade scenario: {match_phone} (solo) + {user_phone} (new)")
                
                # Silent upgrade: solo user gets upgraded, new user gets "found someone" message
                existing_group_id = best_match.get('group_id')
                optimal_time = best_match.get('delivery_time', delivery_time)
                
                # Create silent upgrade
                group_id = self.matcher.create_silent_upgrade_group(
                    user_phone, match_phone, restaurant, location, optimal_time, existing_group_id
                )
                
                # Transition new user to order process
                self.session_manager.transition_to_order_process(user_phone, group_id, restaurant, 2)
                
                # Send message to NEW user (they get told they found someone)
                # Check if user provided complete info in their first message
                user_provided_complete_info = self._user_provided_complete_info_initially(state['messages'][-1].content)
                
                # Generate dynamic response using Claude
                state['response_message'] = self._generate_dynamic_match_response(
                    restaurant, location, optimal_time, 
                    state.get('change_context', {}), is_fake_match=False, 
                    user_provided_complete_info=user_provided_complete_info,
                    user_original_message=state['messages'][-1].content
                )
                
                # SOLO user gets NO notification (silent upgrade)
                # Their fake match just became real, but they don't know
                
                state['action_taken'] = "silent_upgrade_group"
                
            else:
                # Regular real match found
                print(f"🎯 Real match found: {match_phone}")
                
                # Create group and send invitation
                group_id = self.matcher.create_group_match(
                    user_phone, match_phone, restaurant, location, 
                    best_match.get('time_analysis', {}).get('optimal_time', delivery_time)
                )
                
                # Transition both users to order process
                self.session_manager.transition_to_order_process(user_phone, group_id, restaurant, 2)
                self.session_manager.transition_to_order_process(match_phone, group_id, restaurant, 2)
                
                # Send messages to both users with contextual intro
                # Check if user provided complete info in their first message
                user_provided_complete_info = self._user_provided_complete_info_initially(state['messages'][-1].content)
                optimal_time = best_match.get('time_analysis', {}).get('optimal_time', delivery_time)
                
                # Generate dynamic response using Claude
                state['response_message'] = self._generate_dynamic_match_response(
                    restaurant, location, optimal_time, 
                    state.get('change_context', {}), is_fake_match=False,
                    user_provided_complete_info=user_provided_complete_info,
                    user_original_message=state['messages'][-1].content
                )
                
                match_message = f"""🎉 Great news! Another student wants {restaurant} at {location} too!

**Group Confirmed (2 people)**
Your share: $4.50 each (vs $8+ solo)

**Next steps:**
1. Order from {restaurant} (choose PICKUP, not delivery)  
2. Come back with your order number OR name and what you ordered
3. Text "PAY" when ready

Time to order! 🍕"""
                
                # Send to matched user
                self.send_sms(match_phone, match_message)
                
                state['action_taken'] = "created_real_group"
            
        else:
            # No real match - create fake match (solo order)
            print(f"🎭 Creating fake match for {user_phone}")
            
            group_id = self.matcher.create_fake_match(user_phone, restaurant, location, delivery_time)
            
            # Transition to order process as solo
            self.session_manager.transition_to_order_process(user_phone, group_id, restaurant, 1)
            
            # Generate contextual message
            # Check if user provided complete info in their first message
            user_provided_complete_info = self._user_provided_complete_info_initially(state['messages'][-1].content)
            
            # Generate dynamic response using Claude
            state['response_message'] = self._generate_dynamic_match_response(
                restaurant, location, delivery_time, 
                state.get('change_context', {}), is_fake_match=True,
                user_provided_complete_info=user_provided_complete_info,
                user_original_message=state['messages'][-1].content
            )
            
            state['action_taken'] = "created_fake_match"
        
        return state
    
    def _handle_group_response_node(self, state: SmartChatbotState) -> SmartChatbotState:
        """Handle YES/NO responses to group invitations"""
        
        user_message = state['messages'][-1].content
        user_phone = state['user_phone']
        
        print(f"👥 Processing group response from {user_phone}: {user_message}")
        
        # This would integrate with existing group invitation system
        # For now, provide helpful response
        
        message_lower = user_message.lower().strip()
        
        if message_lower in ['yes', 'y', 'sure', 'ok', 'yeah']:
            state['response_message'] = "I don't see any pending group invitations for you right now. Want to start a new food order? Just tell me what you're craving! 🍕"
            state['action_taken'] = "no_pending_invites"
            
        elif message_lower in ['no', 'n', 'nah', 'pass']:
            state['response_message'] = "No worries! I'll keep looking for other opportunities. Want to try a different restaurant or time? 😊"
            state['action_taken'] = "declined_group"
            
        else:
            state['response_message'] = "I didn't catch that. If you have a pending group invitation, please reply YES to join or NO to pass. Otherwise, let me know what you'd like to order! 🍕"
            state['action_taken'] = "unclear_group_response"
        
        return state
    
    def _handle_order_process_node(self, state: SmartChatbotState) -> SmartChatbotState:
        """Handle order process messages with context awareness"""
        
        user_message = state['messages'][-1].content
        user_phone = state['user_phone']
        
        print(f"📋 Processing order message from {user_phone}")
        
        # Get user context to preserve food order details
        context = self.session_manager.get_user_context(user_phone)
        
        # Check if this is a conversational message that should use LLM instead of legacy processor
        message_lower = user_message.lower()
        
        # Conversational patterns that should use LLM, not legacy processor
        conversational_patterns = [
            'my name is', 'name is', 'i\'m', 'im ', 'it\'s', 'its ',
            'hi ', 'hello', 'hey', 'thanks', 'thank you'
        ]
        
        is_conversational = any(pattern in message_lower for pattern in conversational_patterns) or \
                           (len(user_message.split()) <= 3 and not any(word in message_lower for word in ['order', 'number', '#', 'pay']))
        
        if not is_conversational:
            # Try legacy processor for structured order data (PAY, order numbers, etc.)
            try:
                from pangea_order_processor import process_order_message
                result = process_order_message(user_phone, user_message)
                
                if result:
                    state['response_message'] = "Order processed successfully! ✅"
                    state['action_taken'] = "order_processed"
                    return state
                    
            except Exception as e:
                print(f"⚠️ Legacy order processor failed: {e}")
        
        # Use LLM for intelligent conversational order processing
        # Build proper context dict with conversation history
        conversation_context = {
            'conversation_history': getattr(context, 'conversation_memory', []),
            'current_food_request': getattr(context, 'current_food_request', {}),
            'active_order_session': getattr(context, 'active_order_session', {}),
            'session_type': getattr(context, 'session_type', 'unknown'),
            'status': f"Active order for {context.active_order_session.get('restaurant', 'unknown restaurant') if context.active_order_session else 'No active order'}"
        }
        
        state['response_message'] = self._generate_dynamic_conversation_response(
            user_message, user_phone, conversation_context
        )
        state['action_taken'] = "intelligent_order_conversation"
        
        return state
    
    def _handle_general_conversation_node(self, state: SmartChatbotState) -> SmartChatbotState:
        """Handle general conversation with full LLM conversational capabilities"""
        
        user_message = state['messages'][-1].content
        user_phone = state['user_phone']
        context = state['user_context']
        
        print(f"💬 Processing dynamic conversation from {user_phone}")
        
        # Generate contextual, conversational response using LLM
        state['response_message'] = self._generate_dynamic_conversation_response(
            user_message, user_phone, context
        )
        state['action_taken'] = "dynamic_conversation"
        
        return state
    
    def _send_response_node(self, state: SmartChatbotState) -> SmartChatbotState:
        """Send response to user"""
        
        user_phone = state['user_phone']
        message = state.get('response_message', '')
        
        if message:
            success = self.send_sms(user_phone, message)
            print(f"📤 Response sent to {user_phone}: {'✅' if success else '❌'}")
            
            # Update conversation memory
            context = self.session_manager.get_user_context(user_phone)
            self.session_manager.update_user_context(context, state['messages'][-1].content)
        
        return state
    
    # Helper methods
    def _extract_food_request_details(self, message: str, existing_request: Dict = None) -> Dict:
        """Extract food request details using Claude"""
        
        prompt = f"""Extract food order details from this message:

Message: "{message}"
Existing request: {existing_request or {}}

Available restaurants: Chipotle, McDonald's, Chick-fil-A, Portillo's, Starbucks
Available locations: Richard J Daley Library, Student Center East, Student Center West, Student Services Building, University Hall

Extract and return JSON:
{{
    "restaurant": "exact restaurant name or null",
    "location": "exact location name or null",
    "delivery_time": "parsed time or 'now'",
    "missing_info": ["restaurant", "location"] // what's still missing
}}

Return ONLY valid JSON."""
        
        try:
            response = self.llm.invoke([{"role": "user", "content": prompt}])
            response_text = response.content.strip()
            
            # Clean JSON
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
            
            # Normalize location
            location = result.get('location')
            if location:
                from pangea_locations import normalize_location
                result['location'] = normalize_location(location)
            
            # Determine missing info
            missing = []
            if not result.get('restaurant'):
                missing.append('restaurant')
            if not result.get('location'):
                missing.append('location')
            result['missing_info'] = missing
            
            return result
            
        except Exception as e:
            print(f"❌ Food extraction failed: {e}")
            return {"restaurant": None, "location": None, "delivery_time": "now", "missing_info": ["restaurant", "location"]}
    
    def _basic_food_request_extraction(self, message: str) -> Dict:
        """Basic fallback extraction without Claude API"""
        
        message_lower = message.lower()
        
        # Extract restaurant
        restaurants = {
            'chipotle': 'Chipotle',
            'mcdonalds': "McDonald's", 
            'mcdonald': "McDonald's",
            'chick-fil-a': 'Chick-fil-A',
            'chick fil a': 'Chick-fil-A',
            'portillos': "Portillo's",
            'portillo': "Portillo's", 
            'starbucks': 'Starbucks'
        }
        
        restaurant = None
        for key, value in restaurants.items():
            if key in message_lower:
                restaurant = value
                break
        
        # Extract location (basic detection)
        locations = {
            'library': 'Richard J Daley Library',
            'student center east': 'Student Center East',
            'student center west': 'Student Center West', 
            'student services': 'Student Services Building',
            'university hall': 'University Hall'
        }
        
        location = None
        for key, value in locations.items():
            if key in message_lower:
                location = value
                break
        
        # Extract time (basic detection)
        delivery_time = "now"
        if any(time in message_lower for time in ['1pm', '2pm', '3pm', '4pm', '5pm', '11am', '12pm']):
            for time in ['1pm', '2pm', '3pm', '4pm', '5pm', '11am', '12pm']:
                if time in message_lower:
                    delivery_time = time
                    break
        
        # Determine missing info
        missing_info = []
        if not restaurant:
            missing_info.append("restaurant")
        if not location:
            missing_info.append("location")
        
        print(f"🔄 Basic extraction: restaurant={restaurant}, location={location}, time={delivery_time}, missing={missing_info}")
        
        return {
            "restaurant": restaurant,
            "location": location, 
            "delivery_time": delivery_time,
            "missing_info": missing_info
        }
    
    def _generate_missing_info_response(self, missing_info: List[str], restaurant: str = None, location: str = None) -> str:
        """Generate helpful response for missing information"""
        
        if set(missing_info) == {'restaurant', 'location'}:
            return """I'd love to help you order! I need to know:

🍕 **Which restaurant?**
• Chipotle, McDonald's, Chick-fil-A, Portillo's, or Starbucks

📍 **Where should it be delivered?**
• Richard J Daley Library, Student Center East, Student Center West, Student Services Building, or University Hall

Example: "I want Chipotle delivered to the library" """
        
        elif 'restaurant' in missing_info:
            return f"""Got it - you want food delivered to {location}! 

🍕 **Which restaurant?**
• Chipotle, McDonald's, Chick-fil-A, Portillo's, or Starbucks

Just tell me which one sounds good! 😊"""
        
        elif 'location' in missing_info:
            return f"""Perfect - {restaurant} it is! 

📍 **Where should it be delivered?**
• Richard J Daley Library
• Student Center East  
• Student Center West
• Student Services Building
• University Hall

Which location works for you?"""
        
        else:
            return "I think I have everything I need! Let me find you a group..."
    
    def _generate_context_aware_order_response(self, message: str, user_phone: str, context, state: SmartChatbotState) -> str:
        """Generate context-aware order process response using LLM with full conversation memory"""
        
        message_lower = message.lower()
        
        if context.active_order_session:
            restaurant = context.active_order_session.get('restaurant', 'restaurant')
            
            # Check if this looks like just providing a name
            name_patterns = ['my name is', 'name is', 'i\'m', 'im ', 'it\'s', 'its ']
            is_name_only = any(pattern in message_lower for pattern in name_patterns) or \
                          (len(message.split()) <= 3 and not any(word in message_lower for word in ['order', 'number', '#', 'pay']))
            
            if is_name_only:
                # User just provided their name - use LLM with full conversation context
                return self._generate_name_provided_response(message, user_phone, context, restaurant)
            
            elif 'pay' in message_lower:
                return "Processing your payment... 💳"
            
            else:
                # They might be providing order details
                return f"Perfect! Processing your {restaurant} order details... 🍕"
        
        else:
            return "You don't have an active order. Want to start a new food order? Just tell me what you're craving! 🍕"
    
    def _extract_original_food_context(self, context) -> str:
        """Extract original food order context from conversation memory"""
        
        # Look through conversation memory for the original food request
        if hasattr(context, 'conversation_memory') and context.conversation_memory:
            for msg in context.conversation_memory:
                if isinstance(msg, str):
                    msg_lower = msg.lower()
                    # Look for specific food items mentioned
                    food_items = ['burger', 'meal', 'sandwich', 'fries', 'drink', 'combo', 'double', 'big mac', 'whopper']
                    if any(food in msg_lower for food in food_items):
                        # Found original food context
                        return f"Remember, you wanted to order the items you mentioned earlier."
        
        # Look in current food request if available
        if hasattr(context, 'current_food_request') and context.current_food_request:
            food_request = context.current_food_request
            if 'food_items' in food_request:
                return f"You mentioned wanting: {food_request['food_items']}"
        
        return "Please let me know what specific food items you're ordering."
    
    def _generate_order_process_response(self, message: str, user_phone: str) -> str:
        """Generate helpful order process response (legacy fallback)"""
        
        context = self.session_manager.get_user_context(user_phone)
        
        if context.active_order_session:
            order_stage = context.active_order_session.get('order_stage', 'unknown')
            restaurant = context.active_order_session.get('restaurant', 'restaurant')
            
            if 'pay' in message.lower():
                if order_stage == 'ready_to_pay':
                    return "Processing your payment... 💳"
                else:
                    return "I need your order details first before you can pay. Please provide your order number OR name and what you ordered."
            
            elif order_stage == 'need_order_number':
                return f"""I need your order details for {restaurant}.

Please provide:
• Your order confirmation number (like "ABC123")
• OR your name if there's no order number
• What you ordered

Example: "Order #123, Big Mac meal" or "Name is John, chicken nuggets" """
            
            else:
                return f"I'm here to help with your {restaurant} order! Text PAY when you're ready to pay, or let me know if you need help. 😊"
        
        else:
            return "You don't have an active order. Want to start a new food order? Just tell me what you're craving! 🍕"
    
    def _generate_faq_response(self, message: str) -> str:
        """Generate FAQ response"""
        
        message_lower = message.lower()
        
        if 'restaurant' in message_lower or 'food' in message_lower:
            return """🍕 Available restaurants:

• **Chipotle** - Mexican bowls & burritos
• **McDonald's** - Burgers & fries
• **Chick-fil-A** - Chicken sandwiches  
• **Portillo's** - Chicago-style hot dogs & Italian beef
• **Starbucks** - Coffee & pastries

Just tell me what you're craving! Example: "I want Chipotle at the library" ��"""
        
        elif 'location' in message_lower or 'where' in message_lower:
            return """📍 Delivery locations:

• **Richard J Daley Library**
• **Student Center East**
• **Student Center West** 
• **Student Services Building**
• **University Hall**

Where would you like your food delivered? 🚚"""
        
        elif 'cost' in message_lower or 'price' in message_lower:
            return """💰 **Pricing:**

2-person group: $4.50 per person
Solo orders: $3.50 per person

You order your own food from the restaurant - we coordinate delivery to save money! 🍕💳"""
        
        elif 'how' in message_lower or 'work' in message_lower:
            return """🤝 **Here's how it works:**

1. Tell me what restaurant + location you want
2. I'll find someone with similar orders
3. You both order your own food from the restaurant  
4. Split the delivery fee ($4.50 each vs $8+ solo)
5. Get your food delivered together!

Try: "I want McDonald's at the library" 🍔"""
        
        else:
            return """👋 I'm your AI food coordinator! I help you find others to split delivery fees with.

**Quick start:** Tell me what you're craving!
Example: "I want Chipotle delivered to the library"

**Questions?** Ask about restaurants, locations, pricing, or how it works! 😊🍕"""
    
    def _generate_dynamic_match_response(self, restaurant: str, location: str, delivery_time: str, change_context: Dict, is_fake_match: bool = True, user_provided_complete_info: bool = False, user_original_message: str = "") -> str:
        """Generate dynamic, conversational match response using Claude"""
        
        # Determine pricing and group context
        if is_fake_match:
            pricing_info = "Your share will be $3.50 instead of the full delivery fee"
            group_status = "solo order (disguised as group match)"
        else:
            pricing_info = "Group Confirmed (2 people) - Your share: $4.50 each (vs $8+ solo)"
            group_status = "real 2-person group"
        
        # Determine what user provided and what's needed
        is_change = change_context.get('is_change', False)
        
        # Business logic context for Claude
        order_instructions_context = f"""
        CRITICAL ORDER INSTRUCTIONS (must always include):
        - User MUST order from {restaurant} app or call them directly
        - When ordering, they MUST choose PICKUP (never delivery)
        - After ordering, they come back with their order number/confirmation OR their name
        - Then they text "PAY" to get payment link and trigger delivery
        
        BUSINESS LOGIC CONTEXT:
        - This is a {group_status}
        - {pricing_info}
        - Delivery time: {delivery_time}
        - Delivery location: {location}
        """
        
        prompt = f"""You are Pangea, a friendly AI that helps students coordinate group food deliveries. 
        
        CONTEXT:
        - User said: "{user_original_message}"
        - I found them a match for {restaurant} at {location} for {delivery_time}
        - User provided complete info: {user_provided_complete_info}
        - Is this a change request: {is_change}
        
        {order_instructions_context}
        
        SITUATION ANALYSIS:
        - User wants: {restaurant} at {location} 
        - They specified what food they want in their message
        - I found them a compatible match
        
        YOUR TASK:
        Generate a conversational, dynamic response that:
        1. Celebrates finding a match (be excited!)
        2. Acknowledges what they told me they want to order
        3. Explains the pricing ({pricing_info})
        4. Asks for their name (I need this to coordinate pickup)
        5. Gives clear pickup ordering instructions (use the app/call, choose PICKUP not delivery)
        6. Explains next steps (come back with order number OR name, then text PAY)
        
        Be conversational like Claude, not templated. Make it feel natural and excited.
        Use emojis appropriately. Keep it concise but complete.
        
        Remember: They haven't ordered yet - they need to place their order first, then come back with details.
        """
        
        try:
            response = self.llm.invoke([{"role": "user", "content": prompt}])
            return response.content.strip()
        except Exception as e:
            print(f"❌ Dynamic response generation failed: {e}")
            # Fallback to simple response
            return f"""Great news! I found someone who also wants {restaurant} at {location}! 🎉

{pricing_info}

What's your name? I need this to coordinate pickup.

Please order from {restaurant} (app or call) - choose PICKUP, not delivery. Then come back with your order number OR name and what you ordered, then text "PAY" when ready! 🍕"""

    def _user_provided_complete_info_initially(self, message: str) -> bool:
        """Check if user provided complete order information (restaurant + location + specifics) in their first message"""
        
        message_lower = message.lower()
        
        # Check if message contains restaurant
        restaurants = ['chipotle', 'mcdonalds', 'mcdonald', 'chick-fil-a', 'chick fil a', 'portillos', 'portillo', 'starbucks']
        has_restaurant = any(rest in message_lower for rest in restaurants)
        
        # Check if message contains location
        locations = ['library', 'student center', 'student services', 'university hall']
        has_location = any(loc in message_lower for loc in locations)
        
        # Check if message contains specific food items (indicates they know what they want)
        food_specifics = ['big mac', 'quarter pounder', 'chicken', 'nuggets', 'fries', 'burger', 'meal', 'bowl', 'burrito', 'sandwich', 'coffee', 'latte']
        has_food_specifics = any(food in message_lower for food in food_specifics)
        
        # Check if message contains delivery/order context
        order_context = ['delivered', 'delivery', 'order', 'want', 'get']
        has_order_context = any(context in message_lower for context in order_context)
        
        # User provided complete info if they have restaurant + location + (food specifics OR clear order intent)
        # This indicates they're ready to order specific items, not just browsing
        return has_restaurant and has_location and (has_food_specifics or has_order_context)
    

    def _generate_dynamic_conversation_response(self, message: str, user_phone: str, context: Dict) -> str:
        """Generate dynamic, contextual conversation responses using LLM"""
        
        # Build comprehensive context for the LLM
        context_info = f"""
User Context:
- Phone: {user_phone}
- Previous interactions: {context.get('conversation_history', [])}
- Active food request: {context.get('current_food_request', 'None')}
- Recent activity: {context.get('recent_activity', 'None')}

Available Services:
- Restaurants: Chipotle, McDonald's, Chick-fil-A, Portillo's, Starbucks  
- Locations: Richard J Daley Library, Student Center East, Student Center West, Student Services Building, University Hall
- Service: Group food delivery coordination to split costs

Current Status: {context.get('status', 'No active orders')}
"""
        
        prompt = f"""You are Pangea, a friendly AI assistant that helps university students coordinate group food deliveries to split delivery costs. 

{context_info}

User just said: "{message}"

Guidelines:
- Be conversational, helpful, and natural
- Remember context from previous interactions  
- For food requests, guide them to specify restaurant + location
- For questions about service, explain how group ordering works
- For general chat, be friendly but try to guide toward food ordering
- Use appropriate emojis to be engaging
- Keep responses concise but helpful
- If they seem confused, offer specific examples

Respond naturally as if you're having a real conversation with this student:"""

        try:
            response = self.llm.invoke([{"role": "user", "content": prompt}])
            response_text = response.content.strip()
            
            # Ensure response isn't too long (SMS limit consideration)
            if len(response_text) > 1500:
                response_text = response_text[:1450] + "... 📱"
            
            return response_text
            
        except Exception as e:
            print(f"❌ LLM conversation error: {e}")
            # Fallback to basic FAQ if LLM fails
            return self._generate_faq_response(message)
    
    def process_message(self, user_phone: str, message: str) -> Dict:
        """Main entry point - process incoming message"""
        
        initial_state = SmartChatbotState(
            messages=[HumanMessage(content=message)],
            user_phone=user_phone,
            user_context={},
            current_intent="",
            extracted_data={},
            response_message="",
            action_taken="",
            needs_followup=False
        )
        
        try:
            final_state = self.workflow.invoke(initial_state)
            
            return {
                'status': 'success',
                'action': final_state.get('action_taken', 'unknown'),
                'response_sent': bool(final_state.get('response_message')),
                'intent': final_state.get('current_intent', 'unknown')
            }
            
        except Exception as e:
            print(f"❌ Workflow error: {e}")
            
            # Send friendly error message
            error_response = "Sorry, I had a technical hiccup! Can you try again? 😊"
            self.send_sms(user_phone, error_response)
            
            return {
                'status': 'error',
                'error': str(e),
                'response_sent': True
            }
