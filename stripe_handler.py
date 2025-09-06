"""
Stripe Payment Handler
Handles payment link creation and webhook processing for Pangea food delivery
"""

import stripe
import os
import json
import logging
from typing import Dict, Optional, Any
from datetime import datetime, timezone
from dataclasses import dataclass

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

@dataclass
class PaymentConfig:
    """Configuration for Stripe payments"""
    api_key: str = os.getenv('STRIPE_SECRET_KEY')
    webhook_secret: str = os.getenv('STRIPE_WEBHOOK_SECRET')
    solo_price_cents: int = 350  # $3.50
    group_price_cents: int = 450  # $4.50
    currency: str = 'usd'
    
    def __post_init__(self):
        if not self.api_key:
            raise ValueError("STRIPE_SECRET_KEY environment variable required")
        if not self.webhook_secret:
            logger.warning("STRIPE_WEBHOOK_SECRET not set - webhook verification disabled")
        stripe.api_key = self.api_key

class StripeHandler:
    """Handles Stripe payment operations for Pangea"""
    
    def __init__(self):
        self.config = PaymentConfig()
        
    def create_payment_link(self, 
                          user_id: str, 
                          order_type: str = 'solo',
                          order_details: Dict = None) -> Dict:
        """
        Create a Stripe payment link for user
        
        Args:
            user_id (str): User's phone number/ID
            order_type (str): 'solo' or 'group'  
            order_details (Dict): Order information for metadata
            
        Returns:
            Dict: Payment link data with URL and session info
        """
        try:
            # Determine price based on order type
            price_cents = (self.config.solo_price_cents if order_type == 'solo' 
                          else self.config.group_price_cents)
            
            # Build metadata for tracking
            metadata = {
                'user_id': user_id,
                'order_type': order_type,
                'created_at': datetime.now(timezone.utc).isoformat(),
                'platform': 'pangea_delivery'
            }
            
            # Add order details to metadata if provided
            if order_details:
                metadata.update({
                    'restaurant': order_details.get('restaurant', ''),
                    'location': order_details.get('location', ''),
                    'group_id': order_details.get('group_id', ''),
                    'delivery_time': order_details.get('delivery_time', 'now')
                })
            
            # Create payment link
            payment_link = stripe.PaymentLink.create(
                line_items=[
                    {
                        'price_data': {
                            'currency': self.config.currency,
                            'product_data': {
                                'name': f'Pangea Food Delivery - {order_type.title()} Order',
                                'description': f'Food delivery coordination fee'
                            },
                            'unit_amount': price_cents,
                        },
                        'quantity': 1,
                    }
                ],
                metadata=metadata,
                after_completion={
                    'type': 'hosted_confirmation',
                    'hosted_confirmation': {
                        'custom_message': '🎉 Payment successful! Your delivery will be coordinated shortly. You\'ll receive tracking info via text message.'
                    }
                }
            )
            
            logger.info(f"Created payment link for {user_id}: {payment_link.id}")
            
            return {
                'payment_url': payment_link.url,
                'payment_link_id': payment_link.id,
                'amount_cents': price_cents,
                'order_type': order_type,
                'created_at': datetime.now(timezone.utc).isoformat()
            }
            
        except stripe.error.StripeError as e:
            logger.error(f"Stripe error creating payment link: {e}")
            return {
                'error': 'payment_creation_failed',
                'message': 'Unable to create payment link. Please try again.'
            }
        except Exception as e:
            logger.error(f"Unexpected error creating payment link: {e}")
            return {
                'error': 'unexpected_error',
                'message': 'Payment system temporarily unavailable.'
            }
    
    def verify_webhook_signature(self, payload: bytes, signature: str) -> bool:
        """
        Verify Stripe webhook signature
        
        Args:
            payload (bytes): Raw webhook payload
            signature (str): Stripe signature header
            
        Returns:
            bool: True if signature is valid
        """
        if not self.config.webhook_secret:
            logger.warning("Webhook signature verification skipped - no secret configured")
            return True
            
        try:
            stripe.Webhook.construct_event(
                payload, signature, self.config.webhook_secret
            )
            return True
        except stripe.error.SignatureVerificationError:
            logger.error("Invalid webhook signature")
            return False
        except Exception as e:
            logger.error(f"Webhook verification error: {e}")
            return False
    
    def process_webhook_event(self, event_data: Dict) -> Dict:
        """
        Process Stripe webhook event
        
        Args:
            event_data (Dict): Stripe webhook event data
            
        Returns:
            Dict: Processing result with payment info
        """
        event_type = event_data.get('type')
        
        if event_type == 'checkout.session.completed':
            return self._handle_payment_completed(event_data)
        elif event_type == 'payment_intent.succeeded':
            return self._handle_payment_succeeded(event_data)
        else:
            logger.info(f"Unhandled webhook event type: {event_type}")
            return {'status': 'ignored', 'event_type': event_type}
    
    def _handle_payment_completed(self, event_data: Dict) -> Dict:
        """Handle successful payment completion"""
        try:
            session = event_data['data']['object']
            metadata = session.get('metadata', {})
            
            user_id = metadata.get('user_id')
            if not user_id:
                logger.error("Payment completed but no user_id in metadata")
                return {'status': 'error', 'message': 'Missing user identification'}
            
            payment_info = {
                'status': 'payment_completed',
                'user_id': user_id,
                'order_type': metadata.get('order_type', 'solo'),
                'group_id': metadata.get('group_id'),
                'restaurant': metadata.get('restaurant'),
                'location': metadata.get('location'),
                'delivery_time': metadata.get('delivery_time', 'now'),
                'amount_cents': session.get('amount_total'),
                'payment_intent_id': session.get('payment_intent'),
                'session_id': session.get('id'),
                'payment_timestamp': datetime.now(timezone.utc).isoformat(),
                'stripe_event_id': event_data.get('id')
            }
            
            logger.info(f"Payment completed for user {user_id}: {payment_info}")
            return payment_info
            
        except Exception as e:
            logger.error(f"Error processing payment completion: {e}")
            return {'status': 'error', 'message': str(e)}
    
    def _handle_payment_succeeded(self, event_data: Dict) -> Dict:
        """Handle payment intent succeeded (backup handler)"""
        try:
            payment_intent = event_data['data']['object']
            metadata = payment_intent.get('metadata', {})
            
            user_id = metadata.get('user_id')
            if not user_id:
                return {'status': 'ignored', 'message': 'No user_id in payment intent'}
            
            payment_info = {
                'status': 'payment_succeeded',
                'user_id': user_id,
                'order_type': metadata.get('order_type', 'solo'),
                'group_id': metadata.get('group_id'),
                'amount_cents': payment_intent.get('amount'),
                'payment_intent_id': payment_intent.get('id'),
                'payment_timestamp': datetime.now(timezone.utc).isoformat(),
                'stripe_event_id': event_data.get('id')
            }
            
            logger.info(f"Payment intent succeeded for user {user_id}")
            return payment_info
            
        except Exception as e:
            logger.error(f"Error processing payment intent: {e}")
            return {'status': 'error', 'message': str(e)}

class PaymentWebhookProcessor:
    """Processes Stripe webhooks and triggers delivery logic"""
    
    def __init__(self):
        self.stripe_handler = StripeHandler()
        
    def process_payment_webhook(self, payload: bytes, signature: str) -> Dict:
        """
        Main webhook processing entry point
        
        Args:
            payload (bytes): Raw webhook payload
            signature (str): Stripe signature header
            
        Returns:
            Dict: Processing result
        """
        # Verify webhook signature
        if not self.stripe_handler.verify_webhook_signature(payload, signature):
            return {'status': 'error', 'message': 'Invalid signature'}
        
        try:
            # Parse webhook event
            event_data = json.loads(payload.decode('utf-8'))
            
            # Process the payment event
            payment_result = self.stripe_handler.process_webhook_event(event_data)
            
            if payment_result.get('status') == 'payment_completed':
                # Trigger delivery logic with the same rules as before
                delivery_result = self._trigger_delivery_logic(payment_result)
                return {
                    'status': 'success',
                    'payment_info': payment_result,
                    'delivery_result': delivery_result
                }
            else:
                return payment_result
                
        except json.JSONDecodeError as e:
            logger.error(f"Invalid JSON in webhook payload: {e}")
            return {'status': 'error', 'message': 'Invalid JSON payload'}
        except Exception as e:
            logger.error(f"Webhook processing error: {e}")
            return {'status': 'error', 'message': str(e)}
    
    def _trigger_delivery_logic(self, payment_info: Dict) -> Dict:
        """
        Trigger the EXACT SAME delivery logic as before, just with real payment
        
        Args:
            payment_info (Dict): Confirmed payment information
            
        Returns:
            Dict: Delivery trigger result
        """
        try:
            user_id = payment_info['user_id']
            order_type = payment_info['order_type']
            group_id = payment_info.get('group_id')
            delivery_time = payment_info.get('delivery_time', 'now')
            
            # Import the main delivery coordination logic
            from main import PangeaBot
            
            # Create bot instance to access delivery methods
            bot = PangeaBot()
            
            # Update user payment status in database
            self._mark_user_as_paid(user_id, payment_info)
            
            if order_type == 'solo':
                # SOLO ORDER LOGIC (same as before)
                if delivery_time.lower() in ['now', 'asap', 'soon', 'immediately']:
                    # Trigger delivery immediately - NO 50 second delay needed
                    logger.info(f"Triggering immediate solo delivery for {user_id}")
                    return bot._trigger_delivery_now(user_id)
                else:
                    # Schedule for specific time
                    logger.info(f"Scheduling solo delivery for {user_id} at {delivery_time}")
                    return bot._schedule_delivery(user_id, delivery_time)
            
            elif order_type == 'group' and group_id:
                # GROUP ORDER LOGIC (same as before)
                all_members_paid = self._check_all_group_members_paid(group_id)
                
                if all_members_paid:
                    # THE KEY RULE: When ALL members pay → ALWAYS trigger immediately
                    # (ignores scheduled time)
                    logger.info(f"All group members paid - triggering immediate delivery for group {group_id}")
                    return bot._trigger_delivery_now(group_id, is_group=True)
                else:
                    # Only some members paid - wait for others OR scheduled time
                    if delivery_time.lower() not in ['now', 'asap', 'soon', 'immediately']:
                        # Schedule conditional delivery (existing logic)
                        logger.info(f"Scheduling conditional group delivery for {group_id}")
                        return bot._schedule_conditional_delivery(group_id, delivery_time)
                    else:
                        # Wait for other members to pay
                        logger.info(f"Waiting for other group members to pay: {group_id}")
                        return {'status': 'waiting_for_group', 'group_id': group_id}
            
            return {'status': 'unknown_order_type', 'order_type': order_type}
            
        except Exception as e:
            logger.error(f"Error triggering delivery logic: {e}")
            return {'status': 'error', 'message': str(e)}
    
    def _mark_user_as_paid(self, user_id: str, payment_info: Dict):
        """Mark user as paid in database (replaces fake payment timestamp)"""
        try:
            from memory_manager import get_memory_manager
            
            memory_manager = get_memory_manager()
            
            # Update user's payment status with real payment data
            memory_manager.update_user_state(user_id, {
                'payment_timestamp': payment_info['payment_timestamp'],
                'payment_intent_id': payment_info.get('payment_intent_id'),
                'stripe_session_id': payment_info.get('session_id'),
                'amount_paid_cents': payment_info.get('amount_cents'),
                'stage': 'PAYMENT_COMPLETED'  # New stage for confirmed payments
            })
            
            # Also sync to order_sessions collection for delivery triggers
            memory_manager.sync_payment_data_to_db(user_id, payment_info)
            
            logger.info(f"User {user_id} marked as paid with amount ${payment_info.get('amount_cents', 0)/100}")
            
        except Exception as e:
            logger.error(f"Error marking user as paid: {e}")
            raise
    
    def _check_all_group_members_paid(self, group_id: str) -> bool:
        """Check if all group members have completed payment"""
        try:
            from memory_manager import get_memory_manager
            
            memory_manager = get_memory_manager()
            
            # Get all group members and their payment status
            group_data = memory_manager.get_group_data(group_id)
            if not group_data:
                return False
            
            members = group_data.get('members', [])
            if len(members) == 0:
                return False
            
            # Check payment status for each member
            paid_count = 0
            for member_id in members:
                user_state = memory_manager.get_user_state(member_id)
                if user_state and user_state.get('payment_timestamp'):
                    paid_count += 1
            
            all_paid = paid_count == len(members)
            logger.info(f"Group {group_id}: {paid_count}/{len(members)} members paid")
            
            return all_paid
            
        except Exception as e:
            logger.error(f"Error checking group payment status: {e}")
            return False

# Convenience functions for external use
def create_payment_link(user_id: str, order_type: str = 'solo', order_details: Dict = None) -> Dict:
    """Create a Stripe payment link"""
    handler = StripeHandler()
    return handler.create_payment_link(user_id, order_type, order_details)

def process_webhook(payload: bytes, signature: str) -> Dict:
    """Process a Stripe webhook"""
    processor = PaymentWebhookProcessor()
    return processor.process_payment_webhook(payload, signature)