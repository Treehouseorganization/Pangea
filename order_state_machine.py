"""
Order State Machine
Manages order flow transitions and validation
"""

from enum import Enum
from typing import Dict, Optional, List
from models import OrderStage, UserState

class OrderStateMachine:
    """Manages order state transitions"""
    
    def __init__(self):
        # Define valid state transitions
        self.valid_transitions = {
            OrderStage.IDLE: [OrderStage.REQUESTING_FOOD],
            OrderStage.REQUESTING_FOOD: [OrderStage.WAITING_FOR_MATCH, OrderStage.IDLE],
            OrderStage.WAITING_FOR_MATCH: [OrderStage.MATCHED, OrderStage.IDLE],
            OrderStage.MATCHED: [OrderStage.COLLECTING_ORDER_INFO, OrderStage.READY_TO_PAY, OrderStage.IDLE],
            OrderStage.COLLECTING_ORDER_INFO: [OrderStage.READY_TO_PAY, OrderStage.IDLE],
            OrderStage.READY_TO_PAY: [OrderStage.PAYMENT_PENDING, OrderStage.IDLE],
            OrderStage.PAYMENT_PENDING: [OrderStage.DELIVERY_SCHEDULED, OrderStage.DELIVERED, OrderStage.IDLE],
            OrderStage.DELIVERY_SCHEDULED: [OrderStage.DELIVERED, OrderStage.IDLE],
            OrderStage.DELIVERED: [OrderStage.IDLE]
        }
    
    def can_transition(self, current_stage: OrderStage, target_stage: OrderStage) -> bool:
        """Check if transition is valid"""
        return target_stage in self.valid_transitions.get(current_stage, [])
    
    def get_next_stage(self, current_stage: OrderStage, action: str, user_state: UserState) -> Optional[OrderStage]:
        """Determine next stage based on current stage and action"""
        
        if action == 'start_food_request':
            if current_stage == OrderStage.IDLE:
                return OrderStage.REQUESTING_FOOD
        
        elif action == 'complete_food_request':
            if current_stage == OrderStage.REQUESTING_FOOD:
                return OrderStage.WAITING_FOR_MATCH
        
        elif action == 'match_found':
            if current_stage == OrderStage.WAITING_FOR_MATCH:
                return OrderStage.MATCHED
        
        elif action == 'start_order_collection':
            if current_stage == OrderStage.MATCHED:
                return OrderStage.COLLECTING_ORDER_INFO
        
        elif action == 'complete_order_info':
            if current_stage == OrderStage.COLLECTING_ORDER_INFO:
                return OrderStage.READY_TO_PAY
        
        elif action == 'request_payment':
            if current_stage == OrderStage.READY_TO_PAY:
                return OrderStage.PAYMENT_PENDING
        
        elif action == 'schedule_delivery':
            if current_stage == OrderStage.PAYMENT_PENDING:
                return OrderStage.DELIVERY_SCHEDULED
        
        elif action == 'deliver':
            if current_stage in [OrderStage.PAYMENT_PENDING, OrderStage.DELIVERY_SCHEDULED]:
                return OrderStage.DELIVERED
        
        elif action == 'cancel':
            return OrderStage.IDLE  # Can cancel from any stage
        
        return None
    
    def validate_state(self, user_state: UserState) -> List[str]:
        """Validate current state and return any issues"""
        issues = []
        
        stage = user_state.stage
        
        if stage == OrderStage.REQUESTING_FOOD:
            if not user_state.restaurant:
                issues.append("Missing restaurant")
            if not user_state.location:
                issues.append("Missing location")
        
        elif stage == OrderStage.WAITING_FOR_MATCH:
            if not all([user_state.restaurant, user_state.location]):
                issues.append("Missing restaurant or location for matching")
        
        elif stage == OrderStage.MATCHED:
            if not user_state.group_id:
                issues.append("Missing group ID")
        
        elif stage == OrderStage.COLLECTING_ORDER_INFO:
            if not (user_state.order_number or user_state.customer_name):
                issues.append("Missing order identifier")
            if not user_state.order_description:
                issues.append("Missing order description")
        
        elif stage == OrderStage.READY_TO_PAY:
            required_fields = [
                user_state.restaurant,
                user_state.location,
                user_state.order_number or user_state.customer_name,
                user_state.order_description
            ]
            if not all(required_fields):
                issues.append("Missing required order information")
        
        elif stage == OrderStage.PAYMENT_PENDING:
            if not user_state.payment_requested_at:
                issues.append("Payment not requested")
        
        return issues
