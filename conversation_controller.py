# conversation_controller.py
"""
Unified Conversation Controller for Pangea
Provides natural chatbot experience while preserving all business logic
"""

import json
from datetime import datetime, timedelta
from typing import Dict, List, Optional
from langchain_core.messages import HumanMessage
import re

class ConversationController:
    """Master controller that provides conversational interface to all business logic"""
    
    def __init__(self, session_manager, matcher, anthropic_llm, send_sms_func, db):
        self.session_manager = session_manager
        self.matcher = matcher
        self.llm = anthropic_llm
        self.send_sms = send_sms_func
        self.db = db
        
        # Store references for accessing business logic functions later
        # Import functions locally to avoid circular imports
    
    def handle_message(self, user_phone: str, message: str) -> Dict:
        """Main entry point - handle any user message conversationally"""
        
        print(f"🤖 Conversational handler processing: {user_phone} -> {message}")
        
        try:
            # Get full user context
            context = self.session_manager.get_user_context(user_phone)
            
            # Analyze message with full context awareness
            analysis = self._analyze_message_comprehensively(message, context)
            
            # Execute appropriate business logic with conversational response
            result = self._execute_business_action_conversationally(analysis, context)
            
            # Send response to user
            if result.get('response_message'):
                self.send_sms(user_phone, result['response_message'])
            
            return {
                'status': 'success',
                'action': result.get('business_action', 'conversation'),
                'intent': analysis.get('primary_intent', 'general'),
                'response_sent': bool(result.get('response_message'))
            }
            
        except Exception as e:
            print(f"❌ Conversation controller error: {e}")
            import traceback
            traceback.print_exc()
            
            # Send friendly error response
            error_response = "Sorry, I had a technical hiccup! Can you try that again?"
            self.send_sms(user_phone, error_response)
            
            return {
                'status': 'error',
                'error': str(e),
                'response_sent': True
            }
    
    def _analyze_message_comprehensively(self, message: str, context) -> Dict:
        """Use Claude to analyze message with full business context"""
        
        analysis_prompt = f"""You are the brain of Pangea, an AI food delivery coordinator. Analyze this user message with full business context.

USER MESSAGE: "{message}"

CURRENT USER CONTEXT:
- Session type: {context.session_type}
- Active order session: {context.active_order_session is not None}
- Current food request: {context.current_food_request}
- Pending group invites: {len(context.pending_group_invites)}
- Recent conversation: {context.conversation_memory if context.conversation_memory else []}
- User preferences: {context.user_preferences}

ACTIVE ORDER SESSION DETAILS:
{f"- Restaurant: {context.active_order_session.get('restaurant')}" if context.active_order_session else "- No active order session"}
{f"- Location: {context.active_order_session.get('delivery_location')}" if context.active_order_session else ""}
{f"- Customer name: {context.active_order_session.get('customer_name')}" if context.active_order_session and context.active_order_session.get('customer_name') else ""}
{f"- Order description: {context.active_order_session.get('order_description')}" if context.active_order_session and context.active_order_session.get('order_description') else ""}
{f"- Order number: {context.active_order_session.get('order_number')}" if context.active_order_session and context.active_order_session.get('order_number') else ""}
{f"- Order stage: {context.active_order_session.get('order_stage')}" if context.active_order_session else ""}

IMPORTANT: If the user has already provided order details above, DO NOT ask for them again. Only ask for missing information.

BUSINESS RULES TO PRESERVE:
1. Order completion requires: (order_number OR customer_name) AND order_description
2. Payment triggers delivery when ALL group members have paid
3. Scheduled deliveries wait until specified time before triggering
4. Group matching based on restaurant + location + timing compatibility
5. Maximum 3 people per group
6. Solo orders get fake match experience but still get delivered

AVAILABLE RESTAURANTS: Chipotle, McDonald's, Chick-fil-A, Portillo's, Starbucks
AVAILABLE LOCATIONS: Richard J Daley Library, Student Center East, Student Center West, Student Services Building, University Hall

INTENTS TO DETECT:
- new_food_request: Starting new order (restaurant + location + optional time)
- modify_request: Changing existing request (restaurant/location/time)
- cancel_request: Canceling current request/order
- provide_order_details: Giving order number/name/description for active order
- request_payment: Saying "pay" or asking for payment link
- group_response: Answering yes/no to group invitation
- conflict_response: Responding to order conflict (CANCEL/KEEP/YES/NO)
- ask_question: General questions about service/restaurants/etc
- general_chat: Casual conversation, greetings

EXTRACT ALL RELEVANT DATA:
- Restaurant names mentioned
- Locations mentioned
- Times mentioned (now, ASAP, soon, 1pm, lunch, dinner, in 30 minutes, etc) - SET TO NULL if no time explicitly mentioned
- Order numbers (ABC123, #456, etc)
- Customer names ("my name is John", "call me Maria")
- Food descriptions (what they ordered)
- Yes/no responses
- Cancellation phrases

RETURN JSON:
{{
    "primary_intent": "most likely intent from list above",
    "confidence": "high/medium/low",
    "extracted_data": {{
        "restaurant": "exact restaurant name or null",
        "location": "exact location name or null", 
        "delivery_time": "parsed time like 'now', '1pm', 'lunch' or null if NO TIME mentioned",
        "order_number": "extracted order number or null",
        "customer_name": "extracted name or null",
        "order_description": "what they ordered or null",
        "yes_no_response": "yes/no/null",
        "cancellation_detected": true/false
    }},
    "missing_for_completion": ["what's still needed for current business process"],
    "business_context": {{
        "in_order_process": {context.active_order_session is not None},
        "has_pending_invites": {len(context.pending_group_invites) > 0},
        "current_order_stage": "{context.active_order_session.get('order_stage') if context.active_order_session else 'none'}"
    }},
    "conversational_tone": "casual/helpful/urgent based on message",
    "reasoning": "explanation of analysis decisions"
}}

Be thorough in extraction - this drives all business logic."""

        try:
            response = self.llm.invoke([HumanMessage(content=analysis_prompt)])
            response_text = response.content.strip()
            
            # Clean JSON response
            if '```json' in response_text:
                start = response_text.find('{')
                end = response_text.rfind('}') + 1
                response_text = response_text[start:end]
            elif '```' in response_text:
                response_text = response_text.replace('```', '').strip()
            
            if not response_text.startswith('{'):
                json_match = re.search(r'\{.*\}', response_text, re.DOTALL)
                if json_match:
                    response_text = json_match.group()
            
            result = json.loads(response_text)
            
            print(f"🧠 Analysis: {result.get('primary_intent')} (confidence: {result.get('confidence')})")
            print(f"   Reasoning: {result.get('reasoning')}")
            
            # Store original message for fallback responses
            result['original_message'] = message
            
            return result
            
        except Exception as e:
            print(f"❌ Comprehensive analysis failed: {e}")
            return self._fallback_analysis(message, context)
    
    def _execute_business_action_conversationally(self, analysis: Dict, context) -> Dict:
        """Execute appropriate business logic based on analysis, responding conversationally"""
        
        intent = analysis['primary_intent']
        extracted = analysis['extracted_data']
        user_phone = context.user_phone
        
        print(f"🎯 Executing business action for intent: {intent}")
        
        # Route to appropriate business handler
        if intent == 'new_food_request':
            return self._handle_food_request_conversationally(extracted, context, analysis)
            
        elif intent == 'modify_request':
            return self._handle_modification_conversationally(extracted, context, analysis)
            
        elif intent == 'cancel_request':
            return self._handle_cancellation_conversationally(context, analysis)
            
        elif intent == 'provide_order_details':
            return self._handle_order_details_conversationally(extracted, context, analysis)
            
        elif intent == 'request_payment':
            return self._handle_payment_request_conversationally(context, analysis)
            
        elif intent == 'group_response':
            return self._handle_group_response_conversationally(extracted, context, analysis)
            
        elif intent == 'conflict_response':
            return self._handle_conflict_response_conversationally(extracted, context, analysis)
            
        elif intent == 'ask_question':
            return self._handle_question_conversationally(analysis, context)
            
        else:  # general_chat
            return self._handle_general_conversation(analysis, context)
    
    def _handle_food_request_conversationally(self, extracted: Dict, context, analysis: Dict) -> Dict:
        """Handle new food requests with conversational flow"""
        
        restaurant = extracted.get('restaurant')
        location = extracted.get('location') 
        delivery_time = extracted.get('delivery_time')  # Don't default to 'now' yet
        user_phone = context.user_phone
        
        # Check what's missing
        missing = []
        if not restaurant:
            missing.append('restaurant')
        if not location:
            missing.append('location')
        if not delivery_time:
            missing.append('delivery_time')
        
        if missing:
            # Generate conversational request for missing info
            response = self._generate_missing_info_response(missing, restaurant, location)
            
            # Update context with partial request
            context.session_type = "food_request"
            context.current_food_request = {
                "restaurant": restaurant,
                "location": location,
                "delivery_time": delivery_time,
                "missing_info": missing,
                "timestamp": datetime.now()
            }
            self.session_manager.update_user_context(context)
            
            return {
                'business_action': 'requested_missing_info',
                'response_message': response
            }
        
        # Complete request - default delivery_time to 'now' if still None
        if delivery_time is None:
            delivery_time = 'now'
            
        # Complete request - check for conflicts first
        print(f"🍕 Processing food request: {restaurant} at {location} ({delivery_time})")
        
        # Check for conflicting existing orders
        conflict_response = self._check_for_order_conflicts(context, restaurant, location, delivery_time)
        if conflict_response:
            return conflict_response
        
        # Clear any old sessions and start fresh
        self.session_manager.start_fresh_food_request(user_phone, restaurant, location, delivery_time)
        
        # Find matches using existing logic
        match_result = self.matcher.find_compatible_matches(user_phone, restaurant, location, delivery_time)
        
        # Generate conversational response based on match result
        if match_result['has_real_match']:
            response = self._generate_real_match_response(match_result, restaurant, location, delivery_time)
            business_action = 'real_match_found'
            
            # Handle group creation logic (preserve existing logic)
            if match_result.get('is_silent_upgrade'):
                # Silent upgrade scenario
                best_match = match_result['matches'][0]
                existing_group_id = best_match.get('group_id')
                optimal_time = best_match.get('delivery_time', delivery_time)
                
                group_id = self.matcher.create_silent_upgrade_group(
                    user_phone, best_match['user_phone'], restaurant, location, optimal_time, existing_group_id
                )
                self.session_manager.transition_to_order_process(user_phone, group_id, restaurant, 2)
            else:
                # Regular match
                best_match = match_result['matches'][0]
                optimal_time = best_match.get('time_analysis', {}).get('optimal_time', delivery_time)
                
                group_id = self.matcher.create_group_match(
                    user_phone, best_match['user_phone'], restaurant, location, optimal_time
                )
                
                # Transition both users
                self.session_manager.transition_to_order_process(user_phone, group_id, restaurant, 2)
                self.session_manager.transition_to_order_process(best_match['user_phone'], group_id, restaurant, 2)
                
                # Notify matched user
                match_message = f"""Great news! Another student wants {restaurant} at {location} too!

**Group Confirmed (2 people)**
Your share: $4.50 each (vs $8+ solo)

**Next steps:**
1. Order from {restaurant} (choose PICKUP, not delivery)
2. Come back with your order number/name AND what you ordered
3. Text "PAY" when ready

Time to order!"""
                self.send_sms(best_match['user_phone'], match_message)
        
        else:
            # No real match - create solo order with fake match experience
            group_id = self.matcher.create_fake_match(user_phone, restaurant, location, delivery_time)
            self.session_manager.transition_to_order_process(user_phone, group_id, restaurant, 1)
            
            response = self._generate_solo_match_response(restaurant, location, delivery_time)
            business_action = 'solo_order_created'
        
        return {
            'business_action': business_action,
            'response_message': response
        }
    
    def _handle_order_details_conversationally(self, extracted: Dict, context, analysis: Dict) -> Dict:
        """Handle order details provision with conversational validation"""
        
        if not context.active_order_session:
            return {
                'business_action': 'no_active_order',
                'response_message': "You don't have an active order right now. Want to start a new food order? Just tell me what you're craving!"
            }
        
        order_session = context.active_order_session
        current_stage = order_session.get('order_stage', 'unknown')
        restaurant = order_session.get('restaurant', 'restaurant')
        
        # Extract order information
        order_number = extracted.get('order_number')
        customer_name = extracted.get('customer_name') 
        order_description = extracted.get('order_description')
        
        # Update order session with new info
        if order_number:
            order_session['order_number'] = order_number
        if customer_name:
            order_session['customer_name'] = customer_name
        if order_description:
            order_session['order_description'] = order_description
        
        # Check what's still missing
        missing = []
        if not (order_session.get('order_number') or order_session.get('customer_name')):
            missing.append('identifier')
        if not order_session.get('order_description'):
            missing.append('description')
        
        if missing:
            # Still missing information
            response = self._generate_order_missing_info_response(missing, restaurant, order_session)
            order_session['order_stage'] = 'need_order_number'  # Keep in collection stage
        else:
            # Complete! Ready for payment
            order_session['order_stage'] = 'ready_to_pay'
            
            try:
                from pangea_order_processor import get_payment_amount
                payment_amount = get_payment_amount(order_session.get('group_size', 2))
            except ImportError:
                # Fallback payment amounts
                group_size = order_session.get('group_size', 2)
                payment_amount = "$4.50" if group_size >= 2 else "$3.50"
            
            identifier = order_session.get('order_number', order_session.get('customer_name', 'your order'))
            response = f"""Perfect! I've got your order details for {restaurant}!

Order: {order_session.get('order_description')}
Identifier: {identifier}

Your payment share: {payment_amount}

When you're ready to pay, just text: **PAY**

I'll send you the payment link!"""
        
        # Update order session
        try:
            from pangea_order_processor import update_order_session
            update_order_session(context.user_phone, order_session)
        except ImportError:
            print("Warning: Could not import update_order_session")
        
        return {
            'business_action': 'order_details_processed',
            'response_message': response
        }
    
    def _handle_payment_request_conversationally(self, context, analysis: Dict) -> Dict:
        """Handle payment requests using existing payment logic"""
        
        if not context.active_order_session:
            return {
                'business_action': 'no_active_order',
                'response_message': "You don't have an active order to pay for. Want to start a new food order?"
            }
        
        order_session = context.active_order_session
        
        # Check if ready to pay
        has_identifier = order_session.get('order_number') or order_session.get('customer_name')
        has_description = order_session.get('order_description')
        
        if not has_identifier or not has_description:
            missing = []
            if not has_identifier:
                missing.append('order number or name')
            if not has_description:
                missing.append('what you ordered')
            
            response = f"""I need your order details before you can pay!

Still missing:
{chr(10).join(f'• {item}' for item in missing)}

Please provide these first, then you can pay."""
            
            return {
                'business_action': 'payment_blocked',
                'response_message': response
            }
        
        # Ready to pay - use existing payment logic
        group_size = order_session.get('group_size', 2)
        restaurant = order_session.get('restaurant', 'restaurant')
        
        try:
            from pangea_order_processor import get_payment_link, get_payment_amount, update_order_session, check_group_completion_and_trigger_delivery
            
            payment_link = get_payment_link(group_size)
            payment_amount = get_payment_amount(group_size)
            
            # Mark payment as requested
            order_session['order_stage'] = 'payment_initiated'
            order_session['payment_requested_at'] = datetime.now()
            update_order_session(context.user_phone, order_session)
            
            response = f"""Payment for {restaurant}

Your share: {payment_amount}

Click here to pay:
{payment_link}

After payment, I'll coordinate with your group to place the order!"""
            
            # Trigger delivery check (preserve existing logic)
            check_group_completion_and_trigger_delivery(context.user_phone)
            
        except ImportError as e:
            print(f"Warning: Could not import payment functions: {e}")
            response = f"""Payment for {restaurant}

Your share: $3.50

Please text back when you're ready to proceed with payment."""
        
        return {
            'business_action': 'payment_link_sent',
            'response_message': response
        }
    
    def _handle_cancellation_conversationally(self, context, analysis: Dict) -> Dict:
        """Handle cancellations conversationally"""
        
        user_phone = context.user_phone
        
        # Determine what they're canceling
        if context.active_order_session:
            restaurant = context.active_order_session.get('restaurant', 'food')
            response = f"No problem! I've canceled your {restaurant} order. No worries at all!\n\nReady for something else? Just let me know what you're craving and I'll help you get it delivered!"
            
        elif context.current_food_request:
            restaurant = context.current_food_request.get('restaurant', 'food')  
            response = f"No worries! I've canceled your {restaurant} request.\n\nWhenever you're ready for delivery, just tell me what you want and where you want it delivered!"
            
        else:
            response = "No problem! You don't have any active orders right now.\n\nWhenever you're hungry, just let me know what you want delivered!"
        
        # Clear all sessions
        self.session_manager.clear_user_session(user_phone)
        
        return {
            'business_action': 'cancellation_completed',
            'response_message': response
        }
    
    def _handle_modification_conversationally(self, extracted: Dict, context, analysis: Dict) -> Dict:
        """Handle modifications to existing requests"""
        
        # Treat modifications as new requests but acknowledge the change
        result = self._handle_food_request_conversationally(extracted, context, analysis)
        
        # Add acknowledgment of change to response
        if result.get('response_message'):
            result['response_message'] = "Got it! " + result['response_message']
            result['business_action'] = 'modification_' + result.get('business_action', 'processed')
        
        return result
    
    def _handle_group_response_conversationally(self, extracted: Dict, context, analysis: Dict) -> Dict:
        """Handle yes/no responses to group invitations"""
        
        user_phone = context.user_phone
        yes_no = extracted.get('yes_no_response', '').lower()
        
        if not context.pending_group_invites:
            return {
                'business_action': 'no_pending_invites',
                'response_message': "I don't see any pending group invitations for you right now. Want to start a new food order? Just tell me what you're craving!"
            }
        
        # Handle existing group invitation logic (preserve existing code)
        try:
            from pangea_main import handle_group_invitation_response
            handled = handle_group_invitation_response(user_phone, yes_no)
            
            if handled:
                return {
                    'business_action': 'group_response_handled',
                    'response_message': None  # Already sent by existing handler
                }
        except:
            pass
        
        # Fallback response
        if yes_no == 'yes':
            response = "I'm processing your group acceptance..."
        elif yes_no == 'no':
            response = "No worries! I'll keep looking for other opportunities for you."
        else:
            response = "I didn't catch that. If you have a pending group invitation, please reply YES to join or NO to pass."
        
        return {
            'business_action': 'group_response_processed',
            'response_message': response
        }
    
    def _handle_question_conversationally(self, analysis: Dict, context) -> Dict:
        """Handle questions about the service"""
        
        message = analysis.get('original_message', '')
        message_lower = message.lower()
        
        if 'restaurant' in message_lower or 'food' in message_lower:
            response = """Available restaurants:

• **Chipotle** - Mexican bowls & burritos
• **McDonald's** - Burgers & fries  
• **Chick-fil-A** - Chicken sandwiches
• **Portillo's** - Chicago-style hot dogs & Italian beef
• **Starbucks** - Coffee & pastries

Just tell me what you're craving! Example: "I want Chipotle at the library" """
            
        elif 'location' in message_lower or 'where' in message_lower:
            response = """Delivery locations:

• **Richard J Daley Library**
• **Student Center East**
• **Student Center West**
• **Student Services Building** 
• **University Hall**

Where would you like your food delivered?"""
            
        elif 'cost' in message_lower or 'price' in message_lower:
            response = """**Pricing:**

2-person group: $4.50 per person
Solo orders: $3.50 per person

You order your own food from the restaurant - we coordinate delivery to save money!"""
            
        elif 'how' in message_lower or 'work' in message_lower:
            response = """**Here's how it works:**

1. Tell me what restaurant + location you want
2. I'll find someone with similar orders
3. You both order your own food from the restaurant
4. Split the delivery fee ($4.50 each vs $8+ solo)
5. Get your food delivered together!

Try: "I want McDonald's at the library" """
            
        else:
            response = """I'm your AI food coordinator! I help you find others to split delivery fees with.

**Quick start:** Tell me what you're craving!
Example: "I want Chipotle delivered to the library"

**Questions?** Ask about restaurants, locations, pricing, or how it works!"""
        
        return {
            'business_action': 'question_answered',
            'response_message': response
        }
    
    def _handle_general_conversation(self, analysis: Dict, context) -> Dict:
        """Handle general conversation with dynamic LLM responses"""
        
        conversation_prompt = f"""You are Pangea, a friendly AI food delivery coordinator for university students. 

The user just said: "{analysis.get('original_message', '')}"

Their current context:
- Active order: {context.active_order_session is not None}
- Current request: {context.current_food_request}
- Recent chat: {context.conversation_memory[-2:] if context.conversation_memory else []}

Respond naturally and conversationally while guiding them toward food ordering if appropriate. Keep it friendly, helpful, and concise.

Available: Chipotle, McDonald's, Chick-fil-A, Portillo's, Starbucks
Locations: Library, Student Centers, University Hall, Student Services Building"""

        try:
            response = self.llm.invoke([HumanMessage(content=conversation_prompt)])
            response_text = response.content.strip()
            
            # Ensure reasonable length
            if len(response_text) > 1400:
                response_text = response_text[:1400] + "..."
                
            return {
                'business_action': 'general_conversation',
                'response_message': response_text
            }
            
        except Exception as e:
            print(f"❌ General conversation error: {e}")
            return {
                'business_action': 'conversation_fallback',
                'response_message': "I'm here to help you coordinate food delivery! What are you craving?"
            }
    
    # Helper methods for generating responses
    
    def _generate_missing_info_response(self, missing: List[str], restaurant: str = None, location: str = None) -> str:
        """Generate helpful response for missing information"""
        
        if set(missing) == {'restaurant', 'location', 'delivery_time'}:
            return """I'd love to help you order! I need to know:

🍕 **Which restaurant?**
• Chipotle, McDonald's, Chick-fil-A, Portillo's, or Starbucks

📍 **Where should it be delivered?**
• Richard J Daley Library, Student Center East, Student Center West, Student Services Building, or University Hall

⏰ **When do you want it delivered?**
• Reply **"NOW"** if you want it right away
• Or specify a **specific time** like "1pm", "lunch", "dinner", etc.

Example: "I want Chipotle delivered to the library now" """
        
        elif set(missing) == {'restaurant', 'location'}:
            return """I'd love to help you order! I need to know:

🍕 **Which restaurant?**
• Chipotle, McDonald's, Chick-fil-A, Portillo's, or Starbucks

📍 **Where should it be delivered?**
• Richard J Daley Library, Student Center East, Student Center West, Student Services Building, or University Hall

Example: "I want Chipotle delivered to the library" """
        
        elif set(missing) == {'restaurant', 'delivery_time'}:
            return f"""Got it - you want food delivered to {location}!

🍕 **Which restaurant?**
• Chipotle, McDonald's, Chick-fil-A, Portillo's, or Starbucks

⏰ **When do you want it delivered?**
• Reply **"NOW"** if you want it right away
• Or specify a **specific time** like "1pm", "lunch", "dinner"

Example: "McDonald's now" or "Starbucks at 2pm" """
        
        elif set(missing) == {'location', 'delivery_time'}:
            return f"""Perfect - {restaurant} it is!

📍 **Where should it be delivered?**
• Richard J Daley Library, Student Center East, Student Center West, Student Services Building, or University Hall  

⏰ **When do you want it delivered?**
• Reply **"NOW"** if you want it right away
• Or specify a **specific time** like "1pm", "lunch", "dinner"

Example: "Library now" or "Student Center at 3pm" """
        
        elif 'restaurant' in missing:
            return f"""Got it - you want food delivered to {location}!

🍕 **Which restaurant?**
• Chipotle, McDonald's, Chick-fil-A, Portillo's, or Starbucks

Just tell me which one sounds good!"""
        
        elif 'location' in missing:
            return f"""Perfect - {restaurant} it is!

📍 **Where should it be delivered?**
• Richard J Daley Library
• Student Center East
• Student Center West  
• Student Services Building
• University Hall

Which location works for you?"""
        
        elif 'delivery_time' in missing:
            return f"""Awesome! {restaurant} at {location} - just need one more detail:

⏰ **When do you want it delivered?**

Please choose:
• Reply **"NOW"** if you want it delivered right away
• Reply with a **specific time** like "1pm", "lunch", "dinner", or "in 2 hours"

Do you want it delivered **right now** or at a **specific time**?"""
        
        return "Let me find you a group..."
    
    def _generate_real_match_response(self, match_result: Dict, restaurant: str, location: str, delivery_time: str) -> str:
        """Generate response for real matches"""
        
        time_context = ""
        if delivery_time and delivery_time not in ['now', 'asap', 'soon']:
            time_context = f" for {delivery_time}"
            
        return f"""Great news! I found someone who also wants {restaurant} at {location}{time_context}!

**Group Confirmed (2 people)**
Your share: $4.50 each (vs $8+ solo)

**Next steps:**
1. Order from {restaurant} (choose PICKUP, not delivery)
2. Come back with your order number/name AND what you ordered
3. Text "PAY" when ready

Let's get your food!"""
    
    def _generate_solo_match_response(self, restaurant: str, location: str, delivery_time: str) -> str:
        """Generate response for solo orders (fake matches)"""
        
        time_context = ""
        if delivery_time and delivery_time not in ['now', 'asap', 'soon']:
            time_context = f" for {delivery_time}"
            
        return f"""Great news! I found someone who also wants {restaurant} at {location}{time_context}!

Your share will be $3.50 instead of the full delivery fee.

**Next steps:**
1. Order from {restaurant} (choose PICKUP, not delivery)
2. Come back with your order number/name AND what you ordered
3. Text "PAY" when ready

Let's get your food!"""
    
    def _generate_order_missing_info_response(self, missing: List[str], restaurant: str, order_session: Dict) -> str:
        """Generate response for missing order information"""
        
        if set(missing) == {'identifier', 'description'}:
            return f"""I need your order details for {restaurant}.

Please provide:
• Your order confirmation number (like "ABC123") OR your name
• What you ordered (like "Big Mac meal, large fries, Coke")

Example: "Order #123, Big Mac meal" or "Name is John, chicken nuggets" """
        
        elif 'identifier' in missing:
            return f"""I need your order number or name for pickup from {restaurant}.

Please provide:
• Your order confirmation number (like "ABC123")
• OR your name if there's no order number

This helps me coordinate pickup!"""
        
        elif 'description' in missing:
            return f"""I have your order info but need to know what you ordered.

Please tell me what food items you got from {restaurant}:
• Main item (like "Big Mac meal" or "Chicken bowl")
• Size/modifications (like "large fries" or "no onions")  
• Drinks or sides

This helps me coordinate pickup!"""
        
        return f"I need a bit more info for your {restaurant} order."
    
    def _fallback_analysis(self, message: str, context) -> Dict:
        """Simple fallback analysis when Claude fails"""
        
        message_lower = message.lower()
        
        # Detect cancellation
        if any(phrase in message_lower for phrase in ['cancel', 'never mind', 'dont want', "don't want"]):
            return {
                'primary_intent': 'cancel_request',
                'confidence': 'medium',
                'extracted_data': {'cancellation_detected': True},
                'reasoning': 'Fallback cancellation detection',
                'original_message': message
            }
        
        # Detect payment
        if 'pay' in message_lower and len(message_lower) <= 10:
            return {
                'primary_intent': 'request_payment', 
                'confidence': 'high',
                'extracted_data': {},
                'reasoning': 'Fallback payment detection',
                'original_message': message
            }
        
        # Detect conflict responses
        if any(word in message_lower for word in ['cancel', 'keep', 'yes', 'no', 'switch', 'stay']):
            return {
                'primary_intent': 'conflict_response',
                'confidence': 'medium',
                'extracted_data': {'response': message_lower},
                'reasoning': 'Fallback conflict response detection',
                'original_message': message
            }
        
        # Detect food requests
        restaurants = ['chipotle', 'mcdonalds', 'chick-fil-a', 'portillos', 'starbucks']
        if any(rest in message_lower for rest in restaurants):
            return {
                'primary_intent': 'new_food_request',
                'confidence': 'medium', 
                'extracted_data': {'restaurant': next(rest for rest in restaurants if rest in message_lower)},
                'reasoning': 'Fallback restaurant detection',
                'original_message': message
            }
        
        # Default to general chat
        return {
            'primary_intent': 'general_chat',
            'confidence': 'low',
            'extracted_data': {},
            'reasoning': 'Fallback default',
            'original_message': message
        }
    
    def _check_for_order_conflicts(self, context, new_restaurant: str, new_location: str, new_delivery_time: str) -> Optional[Dict]:
        """Check if new request conflicts with existing order and ask user to confirm cancellation"""
        
        existing_request = None
        conflict_type = None
        
        # Check for active order session
        if context.active_order_session:
            existing_restaurant = context.active_order_session.get('restaurant')
            existing_location = context.active_order_session.get('delivery_location')
            existing_time = context.active_order_session.get('delivery_time', 'now')
            existing_stage = context.active_order_session.get('order_stage', 'unknown')
            
            # Check for conflicts
            if (existing_restaurant and existing_restaurant.lower() != new_restaurant.lower()) or \
               (existing_location and existing_location.lower() != new_location.lower()):
                existing_request = f"{existing_restaurant} at {existing_location} ({existing_time})"
                conflict_type = "active_order"
                
                # More urgent if they're deeper in the process
                if existing_stage in ['payment_initiated', 'order_details_complete']:
                    conflict_type = "active_payment_order"
        
        # Check for pending food request (if no active order)
        elif context.current_food_request:
            existing_restaurant = context.current_food_request.get('restaurant')
            existing_location = context.current_food_request.get('location')  
            existing_time = context.current_food_request.get('delivery_time', 'now')
            
            # Check for conflicts
            if (existing_restaurant and existing_restaurant.lower() != new_restaurant.lower()) or \
               (existing_location and existing_location.lower() != new_location.lower()):
                existing_request = f"{existing_restaurant} at {existing_location} ({existing_time})"
                conflict_type = "pending_request"
        
        # If there's a conflict, ask for confirmation
        if existing_request:
            if conflict_type == "active_payment_order":
                response = f"""Hold on! You already have an active order in progress:

**Current order:** {existing_request}
**New request:** {new_restaurant} at {new_location}

You're already in the payment/ordering process for your current order. Do you want to:

• **"CANCEL"** your current order and start fresh with {new_restaurant}
• **"KEEP"** your current {existing_restaurant} order (I'll ignore this new request)

What would you like to do?"""
            
            elif conflict_type == "active_order":
                response = f"""I notice you already have an order in progress:

**Current:** {existing_request}  
**New request:** {new_restaurant} at {new_location}

Would you like me to **"CANCEL"** your current order and start fresh with {new_restaurant}? Or do you want to **"KEEP"** your current order?"""
            
            else:  # pending_request
                response = f"""I see you were already looking for:

**Previous:** {existing_request}
**New request:** {new_restaurant} at {new_location}

Should I switch you to {new_restaurant} instead? Just say **"YES"** to switch or **"NO"** to keep your original request."""
            
            # Store the pending decision in context
            context.session_type = "order_conflict_pending"
            context.pending_new_request = {
                'restaurant': new_restaurant,
                'location': new_location, 
                'delivery_time': new_delivery_time,
                'timestamp': datetime.now()
            }
            self.session_manager.update_user_context(context)
            
            return {
                'business_action': 'order_conflict_detected',
                'response_message': response
            }
        
        return None  # No conflict
    
    def _handle_conflict_response_conversationally(self, extracted: Dict, context, analysis: Dict) -> Dict:
        """Handle user's response to order conflict"""
        
        user_phone = context.user_phone
        message = analysis.get('original_message', '').lower().strip()
        
        # Check if user has a pending conflict to resolve
        if context.session_type != "order_conflict_pending" or not context.pending_new_request:
            return {
                'business_action': 'no_pending_conflict',
                'response_message': "I don't see any pending order conflicts to resolve. What can I help you with?"
            }
        
        pending_request = context.pending_new_request
        new_restaurant = pending_request['restaurant']
        new_location = pending_request['location']
        new_delivery_time = pending_request['delivery_time']
        
        # Determine user's choice
        wants_to_cancel = False
        if any(word in message for word in ['cancel', 'yes', 'switch', 'new', 'fresh']):
            wants_to_cancel = True
        elif any(word in message for word in ['keep', 'no', 'stay', 'continue']):
            wants_to_cancel = False
        else:
            # Unclear response
            return {
                'business_action': 'unclear_conflict_response',
                'response_message': """I didn't catch that. Please say:
• **"CANCEL"** to cancel your current order and start fresh
• **"KEEP"** to stick with your current order

What would you like to do?"""
            }
        
        if wants_to_cancel:
            # Cancel old order and start new one
            print(f"🔄 User chose to cancel existing order, starting fresh with {new_restaurant}")
            
            # Clear old sessions
            self.session_manager.start_fresh_food_request(user_phone, new_restaurant, new_location, new_delivery_time)
            
            # Clear the pending conflict
            context.session_type = "food_request"
            context.pending_new_request = None
            self.session_manager.update_user_context(context)
            
            # Start matching process
            match_result = self.matcher.find_compatible_matches(user_phone, new_restaurant, new_location, new_delivery_time)
            
            response = f"Perfect! I've canceled your previous order and started fresh with {new_restaurant} at {new_location}."
            
            # Process the match
            if match_result['has_real_match']:
                response += f"\n\nGreat news! I found someone else who wants {new_restaurant} too! You'll both save money by grouping up. Sending you the details now..."
                
                # Handle match creation (similar to _handle_food_request_conversationally)
                if match_result.get('is_silent_upgrade'):
                    best_match = match_result['matches'][0]
                    existing_group_id = best_match.get('group_id')
                    optimal_time = best_match.get('delivery_time', new_delivery_time)
                    
                    group_id = self.matcher.create_silent_upgrade_group(
                        user_phone, best_match['user_phone'], new_restaurant, new_location, optimal_time, existing_group_id
                    )
                    self.session_manager.transition_to_order_process(user_phone, group_id, new_restaurant, 2)
                else:
                    best_match = match_result['matches'][0]
                    optimal_time = best_match.get('time_analysis', {}).get('optimal_time', new_delivery_time)
                    
                    group_id = self.matcher.create_group_match(
                        user_phone, best_match['user_phone'], new_restaurant, new_location, optimal_time
                    )
                    
                    self.session_manager.transition_to_order_process(user_phone, group_id, new_restaurant, 2)
                    self.session_manager.transition_to_order_process(best_match['user_phone'], group_id, new_restaurant, 2)
                    
                    # Notify matched user
                    match_message = f"Great news! Another student wants {new_restaurant} at {new_location} too!\n\n**Group Confirmed (2 people)**\nYour share: $4.50 each (vs $8+ solo)\n\nTime to order!"
                    self.send_sms(best_match['user_phone'], match_message)
                
                business_action = 'conflict_resolved_with_match'
            else:
                # No real match - create fake match
                group_id = self.matcher.create_fake_match(user_phone, new_restaurant, new_location, new_delivery_time)
                self.session_manager.transition_to_order_process(user_phone, group_id, new_restaurant, 1)
                
                response += f"\n\n{self._generate_solo_match_response(new_restaurant, new_location, new_delivery_time)}"
                business_action = 'conflict_resolved_solo'
            
            return {
                'business_action': business_action,
                'response_message': response
            }
        
        else:
            # Keep existing order
            print(f"🔄 User chose to keep existing order, ignoring new request for {new_restaurant}")
            
            # Clear the pending conflict and return to previous state
            if context.active_order_session:
                context.session_type = "order_process"
            elif context.current_food_request:
                context.session_type = "food_request" 
            else:
                context.session_type = "idle"
                
            context.pending_new_request = None
            self.session_manager.update_user_context(context)
            
            existing_desc = "your existing order"
            if context.active_order_session:
                restaurant = context.active_order_session.get('restaurant', '')
                location = context.active_order_session.get('delivery_location', '')
                if restaurant and location:
                    existing_desc = f"your {restaurant} order at {location}"
            
            response = f"Got it! I'll stick with {existing_desc}. Your new {new_restaurant} request has been canceled.\n\nLet me know if you need help with your current order!"
            
            return {
                'business_action': 'conflict_resolved_keep_existing',
                'response_message': response
            }