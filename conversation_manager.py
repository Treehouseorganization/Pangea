# conversation_manager.py
"""
Intelligent Conversation Manager
Handles natural conversation flow while tracking state and triggering actions
"""

import json
import re
from typing import Dict, List, Optional, Any, Tuple
from datetime import datetime
from langchain_core.messages import HumanMessage
from models import UserState, OrderStage

class ConversationManager:
    """Manages conversation flow with memory and context awareness"""
    
    def __init__(self, anthropic_llm, memory_manager):
        self.llm = anthropic_llm
        self.memory_manager = memory_manager
        
        # Available options
        self.restaurants = ['Chipotle', 'McDonald\'s', 'Chick-fil-A', 'Portillo\'s', 'Starbucks']
        self.locations = [
            'Richard J Daley Library',
            'Student Center East', 
            'Student Center West',
            'Student Services Building',
            'University Hall'
        ]
    
    async def process_message(self, message: str, user_state: UserState) -> Dict:
        """Process message with full context awareness"""
        
        # Check if this is a response to a direct invitation
        invitation_response = await self._check_invitation_response(message, user_state)
        if invitation_response:
            return invitation_response
        
        # Analyze message intent and content
        analysis = await self._analyze_message(message, user_state)
        
        # Determine what actions need to be triggered
        actions = self._determine_actions(analysis, user_state)
        
        # Generate appropriate response
        response = await self._generate_response(analysis, user_state, actions)
        
        # Determine state updates
        state_updates = self._determine_state_updates(analysis, user_state)
        
        return {
            'analysis': analysis,
            'actions': actions,
            'response': response,
            'state_updates': state_updates
        }
    
    async def _analyze_message(self, message: str, user_state: UserState) -> Dict:
        """Comprehensive message analysis with context"""
        
        # Build context for Claude
        context_info = self._build_context_for_analysis(user_state)
        
        analysis_prompt = f"""You are analyzing a message from a user in a food delivery coordination system. 

CURRENT USER STATE:
{context_info}

USER MESSAGE: "{message}"

SYSTEM CAPABILITIES:
- Available restaurants: {', '.join(self.restaurants)}
- Available locations: {', '.join(self.locations)}
- Can find 2-person groups or create solo orders
- Collects order details: identifier (order number OR name), description of food items
- Triggers payment links and deliveries

ANALYSIS TASKS:
1. Determine the user's primary intent
2. Extract any new information provided
3. Identify what information is still missing
4. Assess if any actions should be triggered

POSSIBLE INTENTS:
- new_food_request: Starting new order (restaurant + location + time)
- modify_request: Changing existing request details  
- provide_order_details: Giving order number/name/description
- request_payment: Asking to pay (usually "pay" or "payment")
- cancel_order: Wanting to cancel current order
- invite_specific_user: Inviting specific user by phone number (e.g., "mcdonald's at library 7pm with user +17089011754", "invite +17089011754 to chipotle")
- ask_question: Questions about service/restaurants/process/options ("what restaurants", "which food", "what's available")
- general_chat: Casual conversation

Return JSON:
{{
    "primary_intent": "intent from list above",
    "confidence": "high/medium/low",
    "extracted_info": {{
        "restaurant": "exact name or null",
        "location": "exact name or null", 
        "delivery_time": "parsed time or null",
        "order_number": "extracted number or null",
        "customer_name": "extracted name or null",
        "order_description": "what they ordered or null",
        "invitee_phone": "phone number to invite (with + prefix) or null"
    }},
    "missing_info": ["list of what's still needed"],
    "should_trigger_actions": ["list of actions to trigger"],
    "conversational_tone": "casual/helpful/urgent",
    "reasoning": "explanation of analysis"
}}

Be thorough and context-aware. Consider their current stage and conversation history."""
        
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
            
            # Add original message to result for response generation
            result['original_message'] = message
            
            print(f"🧠 MESSAGE ANALYSIS COMPLETE:")
            print(f"   🎯 Intent: {result.get('primary_intent')} ({result.get('confidence', 'unknown')} confidence)")
            print(f"   📊 Extracted Info: {result.get('extracted_info', {})}")
            print(f"   ❓ Missing Info: {result.get('missing_info', [])}")
            print(f"   ⚡ Should Trigger: {result.get('should_trigger_actions', [])}")
            print(f"   💭 Reasoning: {result.get('reasoning', 'none')}")
            
            return result
            
        except Exception as e:
            print(f"❌ Message analysis failed: {e}")
            return self._fallback_analysis(message, user_state)
    
    def _build_context_for_analysis(self, user_state: UserState) -> str:
        """Build context string for Claude analysis"""
        
        recent_conversation = ""
        if user_state.conversation_history:
            recent_messages = user_state.conversation_history[-6:]  # Last 3 exchanges
            for msg in recent_messages:
                role = "User" if msg['type'] == 'user' else "Assistant"
                recent_conversation += f"{role}: {msg['message']}\n"
        
        return f"""
Stage: {user_state.stage.value}
Restaurant: {user_state.restaurant or "Not set"}
Location: {user_state.location or "Not set"} 
Delivery time: {user_state.delivery_time}
Order number: {user_state.order_number or "Not provided"}
Customer name: {user_state.customer_name or "Not provided"}
Order description: {user_state.order_description or "Not provided"}
Group ID: {user_state.group_id or "None"}
Group size: {user_state.group_size}
Is fake match: {user_state.is_fake_match}
Missing info: {user_state.missing_info}
Payment requested: {user_state.payment_requested_at is not None}

Recent conversation:
{recent_conversation}
"""
    
    def _determine_actions(self, analysis: Dict, user_state: UserState) -> List[Dict]:
        """Determine what actions should be triggered based on analysis"""
        actions = []
        
        intent = analysis.get('primary_intent')
        extracted_info = analysis.get('extracted_info', {})
        suggested_actions = analysis.get('should_trigger_actions', [])
        
        # New food request - need to find matches
        if intent == 'new_food_request':
            if extracted_info.get('restaurant') and extracted_info.get('location'):
                actions.append({
                    'type': 'find_matches',
                    'data': {
                        'restaurant': extracted_info['restaurant'],
                        'location': extracted_info['location'],
                        'delivery_time': extracted_info.get('delivery_time', 'now')
                    }
                })
        
        # Payment request
        elif intent == 'request_payment' or 'request_payment' in suggested_actions:
            actions.append({
                'type': 'request_payment',
                'data': {}
            })
        
        # Order cancellation
        elif intent == 'cancel_order':
            actions.append({
                'type': 'cancel_order',
                'data': {}
            })
        
        # Auto-trigger payment if order is complete and user is ready
        elif (user_state.stage == OrderStage.COLLECTING_ORDER_INFO and 
              self._is_order_info_complete_from_analysis(analysis, user_state)):
            # Order info is now complete, transition to ready to pay
            pass  # No action needed, state update will handle this
        
        return actions
    
    def _determine_state_updates(self, analysis: Dict, user_state: UserState) -> Dict:
        """Determine how to update user state based on analysis"""
        updates = {}
        
        extracted_info = analysis.get('extracted_info', {})
        intent = analysis.get('primary_intent')
        
        # Stage transitions based on intent and current state first
        if intent == 'new_food_request':
            if extracted_info.get('restaurant') and extracted_info.get('location'):
                updates['stage'] = OrderStage.WAITING_FOR_MATCH
                # Clear any stale data from previous orders
                updates['payment_timestamp'] = None
                updates['payment_requested_at'] = None
                updates['order_number'] = None
                updates['customer_name'] = None
                updates['order_description'] = None
            else:
                updates['stage'] = OrderStage.REQUESTING_FOOD
        
        # Update extracted information (this should come AFTER clearing old data)
        for key, value in extracted_info.items():
            if value and hasattr(user_state, key):
                updates[key] = value
        
        if intent == 'provide_order_details' or intent == 'modify_request':
            if user_state.stage in [OrderStage.MATCHED, OrderStage.COLLECTING_ORDER_INFO]:
                # Check if order info is now complete
                has_identifier = (extracted_info.get('order_number') or 
                                extracted_info.get('customer_name') or
                                user_state.order_number or 
                                user_state.customer_name)
                has_description = (extracted_info.get('order_description') or 
                                 user_state.order_description)
                
                if has_identifier and has_description:
                    updates['stage'] = OrderStage.READY_TO_PAY
                else:
                    updates['stage'] = OrderStage.COLLECTING_ORDER_INFO
        
        elif intent == 'cancel_order':
            updates['stage'] = OrderStage.IDLE
        
        return updates
    
    async def _generate_response(self, analysis: Dict, user_state: UserState, actions: List[Dict]) -> str:
        """Generate contextual response based on analysis and actions"""
        
        intent = analysis.get('primary_intent')
        missing_info = analysis.get('missing_info', [])
        tone = analysis.get('conversational_tone', 'helpful')
        
        # Handle different scenarios
        if intent == 'new_food_request':
            return self._generate_food_request_response(analysis, user_state, missing_info)
        
        elif intent == 'provide_order_details' or intent == 'modify_request':
            return self._generate_order_details_response(analysis, user_state, missing_info)
        
        elif intent == 'request_payment':
            return self._generate_payment_response(analysis, user_state)
        
        elif intent == 'cancel_order':
            return self._generate_cancellation_response(analysis, user_state)
        
        elif intent == 'invite_specific_user':
            return await self._handle_direct_invitation(analysis, user_state)
        
        elif intent == 'ask_question':
            return self._generate_faq_response(analysis, user_state)
        
        elif intent == 'general_chat':
            return await self._generate_dynamic_response(analysis, user_state)
        
        else:
            return await self._generate_dynamic_response(analysis, user_state)
    
    def _generate_food_request_response(self, analysis: Dict, user_state: UserState, missing_info: List[str]) -> str:
        """Generate response for food requests"""
        
        if 'restaurant' in missing_info and 'location' in missing_info:
            return f"""I'd love to help you order! I need to know:

🍕 **Which restaurant?**
{', '.join(self.restaurants)}

📍 **Where should it be delivered?**
{', '.join(self.locations)}

Example: "I want Chipotle delivered to the library" """
        
        elif 'restaurant' in missing_info:
            location = analysis['extracted_info'].get('location')
            return f"""Perfect - you want food delivered to {location}!

🍕 **Which restaurant?**
{', '.join(self.restaurants)}

Just tell me which one sounds good!"""
        
        elif 'location' in missing_info:
            restaurant = analysis['extracted_info'].get('restaurant')
            return f"""Great choice - {restaurant}!

📍 **Where should it be delivered?**
{', '.join(self.locations)}

Which location works for you?"""
        
        else:
            # Complete request - will trigger matching
            restaurant = analysis['extracted_info'].get('restaurant')
            location = analysis['extracted_info'].get('location')
            delivery_time = analysis['extracted_info'].get('delivery_time', 'now')
            
            time_context = ""
            if delivery_time not in ['now', 'asap', 'soon', 'immediately']:
                time_context = f" for {delivery_time}"
            
            if user_state.is_fake_match:
                return f"""Great news! I found someone who also wants {restaurant} at {location}{time_context}!

Your share will be $3.50 instead of the full delivery fee.

**Next steps:**
1. Order from {restaurant} (choose PICKUP, not delivery)
2. Come back with your order number/name AND what you ordered
3. Text "PAY" when ready

Let's get your food!"""
            else:
                return f"""Great news! I found someone who also wants {restaurant} at {location}{time_context}!

**Group Confirmed (2 people)**
Your share: $4.50 each (vs $8+ solo)

**Next steps:**
1. Order from {restaurant} (choose PICKUP, not delivery)
2. Come back with your order number/name AND what you ordered
3. Text "PAY" when ready

Let's get your food!"""
    
    def _generate_order_details_response(self, analysis: Dict, user_state: UserState, missing_info: List[str]) -> str:
        """Generate response for order details collection"""
        
        restaurant = user_state.restaurant or "the restaurant"
        extracted_info = analysis.get('extracted_info', {})
        
        if 'order_identifier' in missing_info and 'order_description' in missing_info:
            return f"""I need your order details for {restaurant}.

Please provide:
• Your order confirmation number (like "ABC123") OR your name
• What you ordered (like "Big Mac meal, large fries, Coke")

Example: "Order #123, Big Mac meal" or "Name is John, chicken nuggets" """
        
        elif 'order_identifier' in missing_info:
            return f"""I need your order number or name for pickup from {restaurant}.

Please provide:
• Your order confirmation number (like "ABC123")
• OR your name if there's no order number

This helps me coordinate pickup!"""
        
        elif 'order_description' in missing_info:
            return f"""I have your order info but need to know what you ordered.

Please tell me what food items you got from {restaurant}:
• Main item (like "Big Mac meal" or "Chicken bowl")
• Size/modifications (like "large fries" or "no onions")  
• Drinks or sides

This helps me coordinate pickup!"""
        
        else:
            # Order info is complete
            identifier = (extracted_info.get('order_number') or 
                         extracted_info.get('customer_name') or
                         user_state.order_number or 
                         user_state.customer_name)
            description = (extracted_info.get('order_description') or 
                          user_state.order_description)
            
            return f"""Perfect! I've got your order details for {restaurant}!

Order: {description}
Identifier: {identifier}

Your payment share: {user_state.payment_amount}

When you're ready to pay, just text: **PAY**

I'll send you the payment link!"""
    
    def _generate_payment_response(self, analysis: Dict, user_state: UserState) -> str:
        """Generate response for payment requests"""
        
        if not self._has_complete_order_info(user_state):
            missing = []
            if not (user_state.order_number or user_state.customer_name):
                missing.append('order number or name')
            if not user_state.order_description:
                missing.append('what you ordered')
            
            return f"""I need your order details before you can pay!

Still missing:
{chr(10).join(f'• {item}' for item in missing)}

Please provide these first, then you can pay."""
        
        # Payment will be handled by action, just acknowledge
        return None  # Payment action will send the actual payment message
    
    def _generate_cancellation_response(self, analysis: Dict, user_state: UserState) -> str:
        """Generate response for cancellations"""
        
        if user_state.stage != OrderStage.IDLE:
            if user_state.restaurant:
                return f"No problem! I've canceled your {user_state.restaurant} order. No worries at all!\n\nReady for something else? Just let me know what you're craving!"
            else:
                return "No worries! I've canceled your current request.\n\nWhenever you're ready for delivery, just tell me what you want and where!"
        else:
            return "No problem! You don't have any active orders right now.\n\nWhenever you're hungry, just let me know what you want delivered!"
    
    async def _handle_direct_invitation(self, analysis: Dict, user_state: UserState) -> str:
        """Handle direct invitation to specific user"""
        
        extracted_info = analysis.get('extracted_info', {})
        invitee_phone = extracted_info.get('invitee_phone')
        restaurant = extracted_info.get('restaurant')
        location = extracted_info.get('location')
        delivery_time = extracted_info.get('delivery_time', 'now')
        
        # Validate required information
        missing_info = []
        if not invitee_phone:
            missing_info.append('phone number to invite')
        if not restaurant:
            missing_info.append('restaurant')
        if not location:
            missing_info.append('location')
        
        if missing_info:
            return f"I'd love to help you invite someone! I just need:\n\n{chr(10).join(f'• {info}' for info in missing_info)}\n\nExample: \"invite +17089011754 to McDonald's at the library for 3pm\""
        
        # Send invitation to the specified user
        invitation_sent = await self._send_direct_invitation(
            inviter_phone=user_state.user_phone,
            invitee_phone=invitee_phone,
            restaurant=restaurant,
            location=location,
            delivery_time=delivery_time
        )
        
        if invitation_sent:
            return f"✅ Perfect! I've sent an invitation to {invitee_phone} for {restaurant} delivery to {location} at {delivery_time}.\n\nThey'll get a message asking if they want to join. I'll let you know when they respond! 📱"
        else:
            return f"❌ Sorry, I couldn't send the invitation to {invitee_phone}. Please double-check the phone number format (should start with +1)."
    
    async def _send_direct_invitation(self, inviter_phone: str, invitee_phone: str, restaurant: str, location: str, delivery_time: str) -> bool:
        """Send invitation message to specified user"""
        
        try:
            # Create invitation message
            time_text = f"at {delivery_time}" if delivery_time != 'now' else "now"
            invitation_message = f"""🍕 You've been invited to order together!

{inviter_phone} invited you to get {restaurant} delivered to {location} {time_text}.

Want to join? Reply:
• "YES" to accept the invitation
• "NO" to decline

If you accept, you'll both get order instructions! 🤝"""

            # Send invitation via SMS
            from pangea_main import send_friendly_message
            result = send_friendly_message(invitee_phone, invitation_message, message_type="direct_invitation")
            
            # Store invitation in database for tracking
            invitation_data = {
                'inviter_phone': inviter_phone,
                'invitee_phone': invitee_phone,
                'restaurant': restaurant,
                'location': location,
                'delivery_time': delivery_time,
                'status': 'pending',
                'created_at': datetime.now()
            }
            
            self.db.collection('direct_invitations').document(f"{inviter_phone}_{invitee_phone}_{int(datetime.now().timestamp())}").set(invitation_data)
            
            return True
            
        except Exception as e:
            print(f"❌ Failed to send direct invitation: {e}")
            return False
    
    async def _check_invitation_response(self, message: str, user_state: UserState) -> Dict:
        """Check if message is a yes/no response to a direct invitation"""
        
        message_lower = message.lower().strip()
        
        # Check for yes/no responses
        if message_lower not in ['yes', 'no', 'y', 'n']:
            return None
        
        # Look for pending invitations for this user
        invitations_query = self.db.collection('direct_invitations').where('invitee_phone', '==', user_state.user_phone).where('status', '==', 'pending')
        invitations = list(invitations_query.stream())
        
        if not invitations:
            return None  # No pending invitations
        
        # Get the most recent invitation
        invitation_doc = max(invitations, key=lambda doc: doc.to_dict().get('created_at'))
        invitation_data = invitation_doc.to_dict()
        
        inviter_phone = invitation_data['inviter_phone']
        restaurant = invitation_data['restaurant']
        location = invitation_data['location']
        delivery_time = invitation_data['delivery_time']
        
        if message_lower in ['yes', 'y']:
            # Accept invitation - create a direct invitation group
            await self._create_direct_invitation_group(invitation_data, user_state)
            
            # Update invitation status
            invitation_doc.reference.update({'status': 'accepted'})
            
            # Notify both users
            await self._notify_invitation_accepted(inviter_phone, user_state.user_phone, restaurant, location, delivery_time)
            
            response = f"🎉 Great! You've joined the {restaurant} order to {location}!\n\nSince this is a direct invitation, have ONE person place the order for pickup:\n• Use your name when ordering\n• Order for both people\n• We'll handle the delivery!\n\nReady to order? Reply with your order details! 🍕"
            
        else:  # no/n
            # Decline invitation
            invitation_doc.reference.update({'status': 'declined'})
            
            # Notify inviter
            from pangea_main import send_friendly_message
            send_friendly_message(inviter_phone, f"👋 {user_state.user_phone} declined your invitation for {restaurant}. No worries - you can still order solo or try inviting someone else!", message_type="invitation_declined")
            
            response = f"No problem! I've let {inviter_phone} know you won't be joining their {restaurant} order.\n\nIf you want to order something for yourself, just let me know! 🍴"
        
        return {
            'analysis': {'primary_intent': 'invitation_response'},
            'actions': [],
            'response': response,
            'state_updates': {}
        }
    
    async def _create_direct_invitation_group(self, invitation_data: Dict, invitee_state: UserState):
        """Create a special group for direct invitation"""
        
        import uuid
        group_id = str(uuid.uuid4())
        
        # Create group data
        group_data = {
            'group_id': group_id,
            'type': 'direct_invitation',
            'inviter': invitation_data['inviter_phone'],
            'invitee': invitation_data['invitee_phone'],
            'restaurant': invitation_data['restaurant'],
            'location': invitation_data['location'],
            'delivery_time': invitation_data['delivery_time'],
            'members': [invitation_data['inviter_phone'], invitation_data['invitee_phone']],
            'group_size': 2,
            'status': 'matched',
            'is_fake_match': False,
            'created_at': datetime.now()
        }
        
        # Store in active_groups collection
        self.db.collection('active_groups').document(group_id).set(group_data)
        
        # Update both users' states to point to this group
        await self._update_user_for_direct_invitation(invitation_data['inviter_phone'], group_id, invitation_data)
        await self._update_user_for_direct_invitation(invitation_data['invitee_phone'], group_id, invitation_data)
        
    async def _update_user_for_direct_invitation(self, user_phone: str, group_id: str, invitation_data: Dict):
        """Update user state for direct invitation group"""
        
        user_state = await self.memory_manager.get_user_state(user_phone)
        user_state.stage = OrderStage.MATCHED
        user_state.group_id = group_id
        user_state.restaurant = invitation_data['restaurant']
        user_state.location = invitation_data['location']
        user_state.delivery_time = invitation_data['delivery_time']
        user_state.group_size = 2
        user_state.is_fake_match = False
        
        await self.memory_manager.save_user_state(user_state)
        
    async def _notify_invitation_accepted(self, inviter_phone: str, invitee_phone: str, restaurant: str, location: str, delivery_time: str):
        """Notify inviter that their invitation was accepted"""
        
        time_text = f"at {delivery_time}" if delivery_time != 'now' else "now"
        message = f"🎉 {invitee_phone} accepted your invitation for {restaurant}!\n\nSince this is a direct invitation, have ONE person place the order for pickup:\n• Use your name when ordering\n• Order for both people\n• We'll handle the delivery to {location} {time_text}!\n\nReady to order? Reply with your order details! 🍕"
        
        from pangea_main import send_friendly_message
        send_friendly_message(inviter_phone, message, message_type="invitation_accepted")
    
    def _generate_faq_response(self, analysis: Dict, user_state: UserState) -> str:
        """Generate FAQ responses"""
        
        message = analysis.get('original_message', '').lower()
        
        if 'restaurant' in message or 'food' in message or 'available' in message:
            return f"""Available restaurants:

{chr(10).join(f'• **{restaurant}**' for restaurant in self.restaurants)}

Just tell me what you're craving! Example: "I want Chipotle at the library" """
        
        elif 'location' in message or 'where' in message:
            return f"""Delivery locations:

{chr(10).join(f'• **{location}**' for location in self.locations)}

Where would you like your food delivered?"""
        
        elif 'cost' in message or 'price' in message:
            return """**Pricing:**

2-person group: $4.50 per person
Solo orders: $3.50 per person

You order your own food from the restaurant - we coordinate delivery to save money!"""
        
        elif 'how' in message or 'work' in message:
            return """**Here's how it works:**

1. Tell me what restaurant + location you want
2. I'll find someone with similar orders
3. You both order your own food from the restaurant
4. Split the delivery fee ($4.50 each vs $8+ solo)
5. Get your food delivered together!

Try: "I want McDonald's at the library" """
        
        else:
            return """I'm your AI food coordinator! I help you find others to split delivery fees with.

**Quick start:** Tell me what you're craving!
Example: "I want Chipotle delivered to the library"

**Questions?** Ask about restaurants, locations, pricing, or how it works!"""
    
    async def _generate_dynamic_response(self, analysis: Dict, user_state: UserState) -> str:
        """Generate dynamic conversational response using LLM"""
        
        # Check for restaurant availability questions that might have been misclassified
        original_message = analysis.get('original_message', '')
        if not original_message:
            # Extract from reasoning or other fields if needed
            for key in ['reasoning', 'user_message']:
                if key in analysis:
                    original_message = str(analysis[key])
                    break
        
        message_lower = original_message.lower()
        restaurant_question_patterns = [
            'what restaurant', 'which restaurant', 'restaurant available', 
            'available restaurant', 'restaurant option', 'what food',
            'which food', 'food available', 'what can i get', 'what options'
        ]
        
        if any(pattern in message_lower for pattern in restaurant_question_patterns):
            return f"""Available restaurants:

{chr(10).join(f'• **{restaurant}**' for restaurant in self.restaurants)}

Just tell me what you're craving! Example: "I want Chipotle at the library" """
        
        context_info = self._build_context_for_analysis(user_state)
        
        conversation_prompt = f"""You are Pangea, a friendly AI food coordinator. Respond naturally to this user.

USER STATE:
{context_info}

USER MESSAGE: "{analysis.get('original_message', '')}"

Guidelines:
- Be conversational and helpful
- Remember their current context and stage
- Guide them toward completing their food order if appropriate
- For general chat, be friendly but try to guide toward food ordering
- Keep responses concise but engaging
- If they seem confused, offer specific examples

Respond naturally as their food coordinator:"""
        
        try:
            response = self.llm.invoke([HumanMessage(content=conversation_prompt)])
            response_text = response.content.strip()
            
            # Ensure reasonable length for SMS
            if len(response_text) > 1400:
                response_text = response_text[:1400] + "..."
            
            return response_text
            
        except Exception as e:
            print(f"❌ Dynamic response generation failed: {e}")
            return "I'm here to help you coordinate food delivery! What are you craving?"
    
    def _fallback_analysis(self, message: str, user_state: UserState) -> Dict:
        """Simple fallback analysis when Claude fails"""
        
        message_lower = message.lower()
        
        # Detect cancellation
        if any(phrase in message_lower for phrase in ['cancel', 'never mind', 'dont want', "don't want"]):
            return {
                'primary_intent': 'cancel_order',
                'confidence': 'medium',
                'extracted_info': {},
                'missing_info': [],
                'should_trigger_actions': ['cancel_order'],
                'reasoning': 'Fallback cancellation detection',
                'original_message': message
            }
        
        # Detect payment
        if 'pay' in message_lower and len(message_lower) <= 10:
            return {
                'primary_intent': 'request_payment',
                'confidence': 'high', 
                'extracted_info': {},
                'missing_info': [],
                'should_trigger_actions': ['request_payment'],
                'reasoning': 'Fallback payment detection',
                'original_message': message
            }
        
        # Detect food requests
        restaurants = ['chipotle', 'mcdonalds', 'chick-fil-a', 'portillos', 'starbucks']
        if any(rest in message_lower for rest in restaurants):
            restaurant = next(rest for rest in restaurants if rest in message_lower)
            return {
                'primary_intent': 'new_food_request',
                'confidence': 'medium',
                'extracted_info': {'restaurant': restaurant.title()},
                'missing_info': ['location'],
                'should_trigger_actions': [],
                'reasoning': 'Fallback restaurant detection',
                'original_message': message
            }
        
        # Detect restaurant availability questions
        restaurant_question_patterns = [
            'what restaurant', 'which restaurant', 'restaurant available', 
            'available restaurant', 'restaurant option', 'what food',
            'which food', 'food available', 'what can i get', 'what options'
        ]
        
        if any(pattern in message_lower for pattern in restaurant_question_patterns):
            return {
                'primary_intent': 'ask_question',
                'confidence': 'high',
                'extracted_info': {},
                'missing_info': [],
                'should_trigger_actions': [],
                'reasoning': 'Fallback restaurant availability question detection',
                'original_message': message
            }
        
        # Default to general conversation
        return {
            'primary_intent': 'general_chat',
            'confidence': 'low',
            'extracted_info': {},
            'missing_info': [],
            'should_trigger_actions': [],
            'reasoning': 'Fallback default',
            'original_message': message
        }
    
    def _is_order_info_complete_from_analysis(self, analysis: Dict, user_state: UserState) -> bool:
        """Check if order info is complete based on analysis"""
        extracted_info = analysis.get('extracted_info', {})
        
        has_identifier = (extracted_info.get('order_number') or 
                         extracted_info.get('customer_name') or
                         user_state.order_number or 
                         user_state.customer_name)
        has_description = (extracted_info.get('order_description') or 
                          user_state.order_description)
        
        return bool(has_identifier and has_description)
    
    def _has_complete_order_info(self, user_state: UserState) -> bool:
        """Check if user has provided complete order information"""
        has_identifier = user_state.order_number or user_state.customer_name
        has_description = user_state.order_description
        has_restaurant = user_state.restaurant
        has_location = user_state.location
        
        return all([has_identifier, has_description, has_restaurant, has_location])
