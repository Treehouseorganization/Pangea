# intelligent_matching.py
"""
Intelligent matching system using Claude's reasoning for time compatibility
Maximum 2-person groups with smart time analysis
"""

from datetime import datetime, timedelta
from typing import Dict, List, Optional
import json

class IntelligentMatcher:
    """Claude-powered matching with intelligent time analysis"""
    
    def __init__(self, db, anthropic_llm):
        self.db = db
        self.llm = anthropic_llm
    
    def find_compatible_matches(self, user_phone: str, restaurant: str, location: str, delivery_time: str) -> Dict:
        """Find compatible matches using Claude's intelligence"""
        
        print(f"🔍 Finding matches for {user_phone}: {restaurant} at {location} ({delivery_time})")
        
        try:
            # STEP 1: Check for existing solo orders to upgrade (silent matching)
            solo_matches = self._find_upgradeable_solo_orders(restaurant, location, user_phone)
            
            if solo_matches:
                best_solo = solo_matches[0]
                print(f"🤫 Found solo order to upgrade: {best_solo['user_phone']} -> silent matching")
                
                return {
                    "has_real_match": True,
                    "matches": [best_solo],
                    "is_silent_upgrade": True,
                    "should_create_fake_match": False
                }
            
            # STEP 2: Query for potential real matches (food requests)
            potential_matches = self._query_potential_matches(restaurant, location, user_phone)
            
            if not potential_matches:
                print(f"❌ No potential matches found in database")
                return {
                    "has_real_match": False,
                    "matches": [],
                    "should_create_fake_match": True
                }
            
            print(f"📊 Found {len(potential_matches)} potential matches, analyzing compatibility...")
            
            # Use Claude to analyze time compatibility for each match
            compatible_matches = []
            
            for match in potential_matches:
                match_time = match.get('delivery_time', 'now')
                match_phone = match.get('user_phone')
                
                print(f"   Checking {match_phone}: {match_time} vs {delivery_time}")
                
                compatibility = self._analyze_time_compatibility(delivery_time, match_time)
                
                if compatibility['is_compatible']:
                    match['compatibility_score'] = compatibility['score']
                    match['time_analysis'] = compatibility
                    compatible_matches.append(match)
                    print(f"   ✅ Compatible: {compatibility['score']:.2f} - {compatibility['reasoning']}")
                else:
                    print(f"   ❌ Not compatible: {compatibility['reasoning']}")
            
            # Sort by compatibility score
            compatible_matches.sort(key=lambda x: x['compatibility_score'], reverse=True)
            
            if compatible_matches:
                # Take best match for 2-person group
                best_match = compatible_matches[0]
                print(f"🎯 Best match: {best_match['user_phone']} (score: {best_match['compatibility_score']:.2f})")
                
                return {
                    "has_real_match": True,
                    "matches": [best_match],
                    "should_create_fake_match": False
                }
            else:
                print(f"❌ No compatible matches found")
                return {
                    "has_real_match": False,
                    "matches": [],
                    "should_create_fake_match": True
                }
                
        except Exception as e:
            print(f"❌ Matching error: {e}")
            return {
                "has_real_match": False,
                "matches": [],
                "should_create_fake_match": True
            }
    
    
    def _analyze_time_compatibility(self, time1: str, time2: str) -> Dict:
        """Use Claude to intelligently analyze time compatibility"""
        
        compatibility_prompt = f"""Analyze time compatibility between two food delivery requests.

TIME 1: "{time1}"
TIME 2: "{time2}"

Your task: Determine if these two delivery times are compatible for a group order.

COMPATIBILITY RULES:
1. HIGHLY COMPATIBLE (0.9-1.0): Exact matches or very close times
   - "now" + "now" = 1.0
   - "1pm" + "1:15pm" = 0.95
   - "lunch" + "12:30pm" = 0.9

2. COMPATIBLE (0.7-0.8): Similar time windows that can work together
   - "1pm" + "between 12:30-2pm" = 0.8
   - "lunch" + "1:30pm" = 0.75
   - "around 2pm" + "2:15pm" = 0.8

3. POSSIBLY COMPATIBLE (0.5-0.6): Require some coordination
   - "now" + "30 minutes" = 0.6
   - "lunch" + "2pm" = 0.5
   - "1pm" + "1:45pm" = 0.6

4. NOT COMPATIBLE (0.0-0.4): Too far apart or conflicting
   - "now" + "tonight" = 0.0
   - "lunch" + "dinner" = 0.0
   - "12pm" + "6pm" = 0.0

ANALYSIS CONSIDERATIONS:
- "now", "asap", "soon" are immediate (within 30 minutes)
- "lunch" typically means 11:30am-1:30pm
- "dinner" typically means 5:30pm-8:30pm
- Specific times like "2pm" have ±15 minute flexibility
- Time ranges like "between X-Y" are flexible within that range
- "around X" means ±30 minutes of X

Return JSON:
{{
    "is_compatible": true/false,
    "score": 0.85,
    "reasoning": "detailed explanation of compatibility",
    "optimal_time": "suggested best time for both",
    "requires_coordination": true/false
}}

Examples:
- "now" + "asap" → {{"is_compatible": true, "score": 1.0, "reasoning": "Both want immediate delivery"}}
- "1pm" + "lunch" → {{"is_compatible": true, "score": 0.9, "reasoning": "1pm is prime lunch time"}}
- "12pm" + "7pm" → {{"is_compatible": false, "score": 0.0, "reasoning": "5 hour difference - lunch vs dinner"}}

Return ONLY valid JSON."""
        
        try:
            response = self.llm.invoke([{"role": "user", "content": compatibility_prompt}])
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
            
            # Validate result
            if not isinstance(result.get('is_compatible'), bool):
                raise ValueError("Invalid is_compatible value")
            
            score = result.get('score', 0.0)
            if not isinstance(score, (int, float)) or score < 0 or score > 1:
                result['score'] = 0.5
            
            return result
            
        except Exception as e:
            print(f"❌ Time compatibility analysis failed: {e}")
            
            # Fallback: simple time matching
            return self._simple_time_compatibility(time1, time2)
    
    def _find_upgradeable_solo_orders(self, restaurant: str, location: str, excluding_user: str) -> List[Dict]:
        """Find existing solo orders (fake matches) that can be upgraded to real 2-person groups"""
        
        try:
            print(f"🤫 Searching for solo orders to upgrade: {restaurant} at {location}")
            
            upgradeable_solos = []
            import pytz
            chicago_tz = pytz.timezone('America/Chicago')
            cutoff_time = datetime.now(chicago_tz) - timedelta(minutes=30)  # Recent solo orders only
            
            # Check active_groups for fake matches (solo orders)
            # Simplified query to avoid complex index requirement
            fake_groups = self.db.collection('active_groups')\
                .where('restaurant', '==', restaurant)\
                .where('location', '==', location)\
                .where('is_fake_match', '==', True)\
                .get()
            
            for group_doc in fake_groups:
                group_data = group_doc.to_dict()
                
                # Filter manually to avoid complex index requirement
                if group_data.get('group_size') != 1:
                    continue
                
                created_at = group_data.get('created_at')
                if created_at:
                    # Handle timezone comparison safely
                    if hasattr(created_at, 'tzinfo') and created_at.tzinfo is not None:
                        # created_at is timezone-aware
                        if created_at < cutoff_time:
                            continue
                    else:
                        # created_at is timezone-naive, assume it's in Chicago time
                        created_at_chicago = chicago_tz.localize(created_at)
                        if created_at_chicago < cutoff_time:
                            continue
                
                solo_user_phone = group_data.get('members', [None])[0]
                
                # Skip self
                if solo_user_phone == excluding_user:
                    continue
                
                # Check if solo user is still in solo order process
                # Note: pangea_order_processor module removed, skipping solo session check
                solo_delivery_time = group_data.get('delivery_time', 'now')
                
                # Quick compatibility check (can be more sophisticated)
                print(f"   🔍 Checking solo user {solo_user_phone}: {solo_delivery_time}")
                
                upgradeable_solos.append({
                    'user_phone': solo_user_phone,
                    'restaurant': restaurant,
                    'location': location,
                    'delivery_time': solo_delivery_time,
                    'group_id': group_data.get('group_id'),
                    'created_at': group_data.get('created_at'),
                    'is_solo_upgrade': True
                })
                
                print(f"   ✅ Found upgradeable solo order: {solo_user_phone}")
            
            # Sort by creation time (most recent first)
            upgradeable_solos.sort(key=lambda x: x.get('created_at', datetime.min.replace(tzinfo=chicago_tz)), reverse=True)
            
            print(f"🤫 Found {len(upgradeable_solos)} upgradeable solo orders")
            return upgradeable_solos[:1]  # Return max 1 for 2-person group
            
        except Exception as e:
            print(f"❌ Error finding upgradeable solo orders: {e}")
            return []
    
    def _query_potential_matches(self, restaurant: str, location: str, excluding_user: str) -> List[Dict]:
        """Query database for potential matches using new user_states collection"""
        
        try:
            # Get recent food requests (last 30 minutes) 
            import pytz
            chicago_tz = pytz.timezone('America/Chicago')
            cutoff_time = datetime.now(chicago_tz) - timedelta(minutes=30)
            
            print(f"🔍 Querying user_states for matches: {restaurant} at {location}")
            
            # Query user_states for users waiting for matches
            user_states = self.db.collection('user_states')\
                .where('restaurant', '==', restaurant)\
                .where('location', '==', location)\
                .where('stage', '==', 'waiting_for_match')\
                .get()
            
            matches = []
            
            for user_doc in user_states:
                user_data = user_doc.to_dict()
                user_phone = user_data.get('user_phone')
                
                # Skip self
                if user_phone == excluding_user:
                    continue
                
                # ✅ NEW: Skip users who are unmatchable (abandoned)
                if not self._is_user_matchable(user_data):
                    print(f"   ⏭️ Skipping {user_phone}: inactive for >30 minutes")
                    continue
                
                # Check if request is recent 
                last_activity = user_data.get('last_activity')
                
                if last_activity:
                    # Parse timestamp string if needed
                    if isinstance(last_activity, str):
                        try:
                            last_activity = datetime.fromisoformat(last_activity.replace('Z', '+00:00'))
                        except:
                            continue
                    
                    # Handle timezone comparison safely
                    if hasattr(last_activity, 'tzinfo') and last_activity.tzinfo is not None:
                        # last_activity is timezone-aware
                        if last_activity < cutoff_time:
                            continue  # Too old
                    else:
                        # last_activity is timezone-naive, assume Chicago time
                        last_activity_chicago = chicago_tz.localize(last_activity)
                        if last_activity_chicago < cutoff_time:
                            continue  # Too old
                
                print(f"   Found potential match: {user_phone} - {user_data.get('delivery_time', 'now')}")
                
                # Add to potential matches
                matches.append({
                    'user_phone': user_phone,
                    'restaurant': restaurant,
                    'location': location,
                    'delivery_time': user_data.get('delivery_time', 'now'),
                    'request_time': last_activity,
                    'user_state': user_data
                })
            
            print(f"🔍 Found {len(matches)} potential matches in user_states")
            return matches
            
        except Exception as e:
            print(f"❌ Error querying potential matches: {e}")
            return []
    
    def _is_user_matchable(self, user_data: Dict) -> bool:
        """Check if user should be available for matching (prevents abandoned user matching)"""
        try:
            last_activity_str = user_data.get('last_activity')
            if not last_activity_str:
                return False
            
            last_activity = datetime.fromisoformat(last_activity_str)
            
            # Don't match users who haven't been active in 30 minutes
            # (prevents matching with abandoned users)
            return datetime.now() - last_activity <= timedelta(minutes=30)
            
        except Exception:
            return False
    
    def _simple_time_compatibility(self, time1: str, time2: str) -> Dict:
        """Fallback simple time compatibility"""
        
        time1_lower = time1.lower()
        time2_lower = time2.lower()
        
        # Immediate delivery matches
        immediate_words = ['now', 'asap', 'soon', 'immediately']
        if any(word in time1_lower for word in immediate_words) and \
           any(word in time2_lower for word in immediate_words):
            return {
                "is_compatible": True,
                "score": 1.0,
                "reasoning": "Both want immediate delivery",
                "optimal_time": "now",
                "requires_coordination": False
            }
        
        # Meal period matches
        meal_periods = [
            ['breakfast', 'morning'],
            ['lunch', 'noon'],
            ['dinner', 'evening', 'night']
        ]
        
        for period in meal_periods:
            if any(word in time1_lower for word in period) and \
               any(word in time2_lower for word in period):
                return {
                    "is_compatible": True,
                    "score": 0.8,
                    "reasoning": f"Both want {period[0]} delivery",
                    "optimal_time": period[0],
                    "requires_coordination": True
                }
        
        # Default: not compatible
        return {
            "is_compatible": False,
            "score": 0.0,
            "reasoning": "Times appear incompatible",
            "optimal_time": time1,
            "requires_coordination": False
        }
    
    
    def create_group_match(self, user1_phone: str, user2_phone: str, restaurant: str, location: str, optimal_time: str) -> str:
        """Create a 2-person group match"""
        
        try:
            group_id = f"group_{user1_phone}_{user2_phone}_{datetime.now().timestamp()}"
            
            # Create group record
            group_data = {
                'group_id': group_id,
                'members': [user1_phone, user2_phone],
                'restaurant': restaurant,
                'location': location,
                'delivery_time': optimal_time,
                'status': 'pending_responses',
                'created_at': datetime.now(),
                'group_size': 2,
                'creator': user1_phone
            }
            
            self.db.collection('active_groups').document(group_id).set(group_data)
            
            print(f"✅ Created 2-person group: {group_id}")
            return group_id
            
        except Exception as e:
            print(f"❌ Error creating group match: {e}")
            return None
    
    
    def create_silent_upgrade_group(self, new_user_phone: str, solo_user_phone: str, restaurant: str, location: str, optimal_time: str, existing_group_id: str) -> str:
        """Upgrade solo order to real 2-person group silently"""
        
        try:
            print(f"🤫 Creating silent upgrade: {solo_user_phone} (solo) + {new_user_phone} (new)")
            
            # Update existing fake group to real group
            group_data = {
                'group_id': existing_group_id,
                'members': [solo_user_phone, new_user_phone],
                'restaurant': restaurant,
                'location': location,
                'delivery_time': optimal_time,
                'status': 'active',
                'created_at': datetime.now(),
                'group_size': 2,
                'is_fake_match': False,  # No longer fake
                'silent_upgrade': True,
                'upgraded_at': datetime.now(),
                'original_solo_user': solo_user_phone
            }
            
            self.db.collection('active_groups').document(existing_group_id).update(group_data)
            
            # Update solo user's session silently (no notification)
            # Note: pangea_order_processor module removed, skipping session update
            print(f"✅ Silent upgrade completed for user {solo_user_phone} (session update skipped)")
            
            print(f"✅ Silent upgrade completed: {existing_group_id}")
            return existing_group_id
            
        except Exception as e:
            print(f"❌ Error creating silent upgrade: {e}")
            return None
    
    
    def create_fake_match(self, user_phone: str, restaurant: str, location: str, delivery_time: str) -> str:
        """Create a fake match (solo order disguised as group)"""
        
        try:
            group_id = f"solo_{user_phone}_{datetime.now().timestamp()}"
            
            # Create fake group record (for tracking)
            group_data = {
                'group_id': group_id,
                'members': [user_phone],
                'restaurant': restaurant,
                'location': location,
                'delivery_time': delivery_time,
                'status': 'fake_match',
                'created_at': datetime.now(),
                'group_size': 1,
                'is_fake_match': True
            }
            
            self.db.collection('active_groups').document(group_id).set(group_data)
            
            # Schedule delivery for the specified time if not immediate
            self._schedule_fake_match_delivery(group_data)
            
            print(f"✅ Created fake match (solo order): {group_id}")
            return group_id
            
        except Exception as e:
            print(f"❌ Error creating fake match: {e}")
            return None
    
    def _schedule_fake_match_delivery(self, group_data: Dict):
        """Schedule delivery for fake match at specified time"""
        try:
            delivery_time_str = group_data.get('delivery_time', 'now')
            
            # Skip scheduling for immediate delivery
            immediate_words = ['now', 'asap', 'soon', 'immediately']
            if any(word in delivery_time_str.lower() for word in immediate_words):
                print(f"⏰ Fake match delivery time is immediate - no scheduling needed")
                return
            
            # Import time parsing function
            from pangea_uber_direct import parse_delivery_time
            from delivery_coordinator import DeliveryCoordinator
            
            # Parse the delivery time
            scheduled_time = parse_delivery_time(delivery_time_str)
            print(f"⏰ Scheduling fake match delivery for: {scheduled_time.strftime('%I:%M %p on %B %d, %Y')}")
            
            # Create delivery data for the coordinator
            delivery_data = {
                'restaurant': group_data['restaurant'],
                'location': group_data['location'],
                'group_id': group_data['group_id'],
                'members': group_data['members'],
                'group_size': 1,
                'is_fake_match': True,
                'delivery_time': delivery_time_str,
                'order_details': []  # Will be populated when user provides order info
            }
            
            # Initialize delivery coordinator and schedule delivery
            coordinator = DeliveryCoordinator(self.db)
            
            # Use asyncio to run the async method
            import asyncio
            import threading
            
            def schedule_delivery():
                try:
                    # Create new event loop for this thread
                    loop = asyncio.new_event_loop()
                    asyncio.set_event_loop(loop)
                    
                    # Schedule the delivery
                    result = loop.run_until_complete(
                        coordinator.schedule_delivery_for_time(delivery_data, scheduled_time)
                    )
                    
                    if result.get('success'):
                        print(f"✅ Fake match delivery scheduled successfully for {result.get('scheduled_time')}")
                    else:
                        print(f"❌ Failed to schedule fake match delivery: {result.get('error')}")
                    
                    loop.close()
                    
                except Exception as e:
                    print(f"❌ Error in delivery scheduling thread: {e}")
            
            # Run scheduling in background thread
            thread = threading.Thread(target=schedule_delivery)
            thread.daemon = True
            thread.start()
            
        except Exception as e:
            print(f"❌ Error scheduling fake match delivery: {e}")