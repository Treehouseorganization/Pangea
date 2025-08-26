# models.py
"""
Shared data models for Pangea Food Delivery Coordination System
Contains UserState and OrderStage definitions used across modules
"""

from datetime import datetime
from typing import Dict, List, Optional
from dataclasses import dataclass, asdict
from enum import Enum


class OrderStage(Enum):
    """Order progression stages"""
    IDLE = "idle"
    REQUESTING_FOOD = "requesting_food" 
    WAITING_FOR_MATCH = "waiting_for_match"
    MATCHED = "matched"
    COLLECTING_ORDER_INFO = "collecting_order_info"
    READY_TO_PAY = "ready_to_pay"
    PAYMENT_PENDING = "payment_pending" 
    DELIVERY_SCHEDULED = "delivery_scheduled"
    DELIVERED = "delivered"


@dataclass
class UserState:
    """Complete user state with memory and context"""
    user_phone: str
    session_id: str
    stage: OrderStage = OrderStage.IDLE
    
    # Order details
    restaurant: Optional[str] = None
    location: Optional[str] = None
    delivery_time: str = "now"
    order_number: Optional[str] = None
    customer_name: Optional[str] = None
    order_description: Optional[str] = None
    
    # Group information
    group_id: Optional[str] = None
    group_size: int = 1
    is_fake_match: bool = False
    
    # Payment tracking
    payment_requested_at: Optional[datetime] = None
    payment_amount: str = "$3.50"
    
    # Conversation memory
    conversation_history: List[Dict] = None
    last_activity: datetime = None
    
    # Missing information tracking
    missing_info: List[str] = None
    
    def __post_init__(self):
        if self.conversation_history is None:
            self.conversation_history = []
        if self.last_activity is None:
            self.last_activity = datetime.now()
        if self.missing_info is None:
            self.missing_info = []
    
    def to_dict(self) -> Dict:
        """Convert to dictionary for storage"""
        data = asdict(self)
        data['stage'] = self.stage.value
        data['last_activity'] = self.last_activity.isoformat() if self.last_activity else None
        data['payment_requested_at'] = self.payment_requested_at.isoformat() if self.payment_requested_at else None
        return data
    
    @classmethod
    def from_dict(cls, data: Dict) -> 'UserState':
        """Create from dictionary"""
        if 'stage' in data:
            data['stage'] = OrderStage(data['stage'])
        if 'last_activity' in data and data['last_activity']:
            data['last_activity'] = datetime.fromisoformat(data['last_activity'])
        if 'payment_requested_at' in data and data['payment_requested_at']:
            data['payment_requested_at'] = datetime.fromisoformat(data['payment_requested_at'])
        return cls(**data)