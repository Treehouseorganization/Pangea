"""
Pangea Agent Testing Suite - Enhanced with Complete Message Tracking
Shows both incoming (your texts) and outgoing (AI responses) messages
"""

import json
import time
import random
from datetime import datetime, timedelta
from typing import Dict, List
import uuid
import os

# Import your main system
from pangea_main import (
    handle_incoming_sms,
    get_user_preferences,
    find_potential_matches,
    calculate_compatibility,
    notify_compatible_users_of_active_groups,
    db,
    send_friendly_message
)

class EnhancedMessageTracker:
    """Track both incoming and outgoing messages"""
    def __init__(self):
        self.messages_sent = []      # Outgoing (AI responses)
        self.messages_received = []  # Incoming (your texts)
        self.original_send_function = None
        self.original_sms_handler = None
    
    def track_outgoing_message(self, phone_number: str, message: str, message_type: str = "general"):
        """Track outgoing AI messages"""
        self.messages_sent.append({
            'timestamp': datetime.now(),
            'direction': 'outgoing',
            'to': phone_number,
            'message': message,
            'type': message_type
        })
        
        print(f"📤 AI → {phone_number}: {message}")
        
        # Still send the actual message
        if not os.getenv('PANGEA_TEST_MOCK_SMS', 'false').lower() == 'true':
            return self.original_send_function(phone_number, message, message_type)
        else:
            print("   (MOCK MODE - SMS not actually sent)")
            return True
    
    def track_incoming_message(self, phone_number: str, message_body: str):
        """Track incoming user messages"""
        self.messages_received.append({
            'timestamp': datetime.now(),
            'direction': 'incoming',
            'from': phone_number,
            'message': message_body
        })
        
        print(f"📥 {phone_number} → AI: {message_body}")
        
        # Call original handler
        return self.original_sms_handler(phone_number, message_body)
    
    def start_tracking(self):
        """Start intercepting both directions"""
        import pangea_main
        
        # Track outgoing messages
        self.original_send_function = pangea_main.send_friendly_message
        pangea_main.send_friendly_message = self.track_outgoing_message
        
        # Track incoming messages
        self.original_sms_handler = pangea_main.handle_incoming_sms
        pangea_main.handle_incoming_sms = self.track_incoming_message
    
    def stop_tracking(self):
        """Stop intercepting messages"""
        import pangea_main
        if self.original_send_function:
            pangea_main.send_friendly_message = self.original_send_function
        if self.original_sms_handler:
            pangea_main.handle_incoming_sms = self.original_sms_handler
    
    def print_conversation_flow(self):
        """Print complete conversation flow"""
        print("\n💬 COMPLETE CONVERSATION FLOW:")
        print("=" * 80)
        
        # Combine and sort all messages by timestamp
        all_messages = self.messages_sent + self.messages_received
        all_messages.sort(key=lambda x: x['timestamp'])
        
        for msg in all_messages:
            timestamp = msg['timestamp'].strftime("%H:%M:%S")
            
            if msg['direction'] == 'incoming':
                print(f"[{timestamp}] 📥 {msg['from']} → AI: {msg['message']}")
            else:
                print(f"[{timestamp}] 📤 AI → {msg['to']}: {msg['message']}")
        
        print(f"\nTotal messages: {len(all_messages)}")
        print(f"  Incoming (from you): {len(self.messages_received)}")
        print(f"  Outgoing (from AI): {len(self.messages_sent)}")
    
    def save_conversation_log(self):
        """Save conversation to a file"""
        all_messages = self.messages_sent + self.messages_received
        all_messages.sort(key=lambda x: x['timestamp'])
        
        log_content = f"Pangea Agent Test - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
        log_content += "=" * 80 + "\n\n"
        
        for msg in all_messages:
            timestamp = msg['timestamp'].strftime("%H:%M:%S")
            if msg['direction'] == 'incoming':
                log_content += f"[{timestamp}] INCOMING from {msg['from']}: {msg['message']}\n"
            else:
                log_content += f"[{timestamp}] OUTGOING to {msg['to']}: {msg['message']}\n"
        
        filename = f"pangea_test_log_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
        with open(filename, 'w') as f:
            f.write(log_content)
        
        print(f"📄 Conversation log saved to: {filename}")

class PangeaAgentTester:
    def __init__(self, mode="live"):
        self.test_users = []
        self.test_results = []
        self.message_tracker = EnhancedMessageTracker()
        self.mode = mode
        self.setup_test_users()
        
        if mode == "mock":
            os.environ['PANGEA_TEST_MOCK_SMS'] = 'true'
            print("🔧 MOCK MODE - SMS messages will be logged but not sent")
        else:
            print("📱 LIVE MODE - SMS messages will be sent via Twilio")
            print("💡 Make sure ngrok is running and Twilio webhook is configured")
    
    def setup_test_users(self):
        """Create test users with your actual phone numbers"""
        self.test_users = [
            {
                "phone": "+17089011754",
                "name": "Phone 1 (Alice)",
                "preferences": {
                    "favorite_cuisines": ["Thai Garden", "Sushi Express"],
                    "usual_locations": ["Student Union", "Library Plaza"],
                    "preferred_times": ["lunch", "12pm", "1pm"]
                }
            },
            {
                "phone": "+16305470891",
                "name": "Phone 2 (Bob)",
                "preferences": {
                    "favorite_cuisines": ["Mario's Pizza", "Burger Barn"],
                    "usual_locations": ["Campus Center", "Student Union"],
                    "preferred_times": ["lunch", "now", "12:30pm"]
                }
            }
        ]
    
    def create_test_users_in_db(self):
        """Create test users in Firebase"""
        print("🔧 Setting up test users in database...")
        
        for user in self.test_users:
            user_data = {
                'phone': user['phone'],
                'created_at': datetime.now(),
                'preferences': user['preferences'],
                'interactions': [],
                'successful_matches': [],
                'learning_data': {
                    'response_patterns': [],
                    'satisfaction_scores': [8, 9, 8],
                    'preferred_group_sizes': [2, 3]
                }
            }
            
            db.collection('users').document(user['phone']).set(user_data)
            print(f"✅ Created test user: {user['name']} ({user['phone']})")
    
    def cleanup_test_data(self):
        """Clean up test data from database"""
        print("🧹 Cleaning up test data...")
        
        for user in self.test_users:
            try:
                db.collection('users').document(user['phone']).delete()
            except:
                pass
        
        collections_to_clean = ['active_orders', 'negotiations', 'notification_history', 'order_sessions']
        for collection in collections_to_clean:
            try:
                docs = db.collection(collection).where('user_phone', 'in', [u['phone'] for u in self.test_users]).get()
                for doc in docs:
                    doc.reference.delete()
            except:
                pass
        
        print("✅ Test data cleaned up")
    
    def check_ngrok_setup(self):
        """Check if ngrok is likely running"""
        print("\n🌐 NGROK SETUP CHECK:")
        print("1. Make sure ngrok is running: 'ngrok http 8000'")
        print("2. Copy the HTTPS URL (like https://abc123.ngrok.io)")
        print("3. Set Twilio webhook to: https://abc123.ngrok.io/webhook/sms")
        print("4. Make sure your Pangea server is running on port 8000")
        
        ngrok_ready = input("\n✅ Is ngrok running and Twilio webhook configured? (y/n): ")
        if ngrok_ready.lower() != 'y':
            print("❌ Please set up ngrok first!")
            print("\nSetup Steps:")
            print("1. Open new terminal")
            print("2. Run: ngrok http 8000")
            print("3. Copy the https URL")
            print("4. Go to Twilio Console → Phone Numbers → Your Number")
            print("5. Set webhook URL to: https://YOUR_NGROK_URL.ngrok.io/webhook/sms")
            print("6. Save and return here")
            return False
        return True
    
    def run_interactive_tests(self):
        """Run interactive tests with real phone numbers"""
        print("🚀 Starting Enhanced Pangea Agent Test Suite")
        print("=" * 80)
        
        # Check ngrok setup
        if not self.check_ngrok_setup():
            return
        
        # Start message tracking
        self.message_tracker.start_tracking()
        
        # Setup
        self.create_test_users_in_db()
        time.sleep(2)
        
        try:
            print("\n🎯 TESTING OVERVIEW:")
            print("You'll see all messages in real-time:")
            print("📥 = Messages you send from your phones")
            print("📤 = Messages the AI sends back")
            print("\nLet's begin!\n")
            
            # Run tests
            self.test_spontaneous_matching_interactive()
            self.test_group_responses_interactive()
            self.test_proactive_notifications_interactive()
            
            # Results
            self.print_test_results()
            self.message_tracker.print_conversation_flow()
            self.message_tracker.save_conversation_log()
            
        finally:
            self.message_tracker.stop_tracking()
            
            cleanup = input("\n🧹 Clean up test data from database? (y/n): ")
            if cleanup.lower() == 'y':
                self.cleanup_test_data()
    
    def test_spontaneous_matching_interactive(self):
        """Test spontaneous matching with real phones"""
        print("\n🧪 TEST 1: Spontaneous Order Matching")
        print("=" * 60)
        print("📋 WHAT THIS TESTS:")
        print("   - Message parsing and restaurant extraction")
        print("   - User matching based on preferences")
        print("   - Inter-agent negotiation")
        print("   - Group formation")
        
        print("\n📱 STEP-BY-STEP INSTRUCTIONS:")
        print(f"1. From {self.test_users[0]['phone']}: Text 'I want Thai Garden at Student Union now'")
        print(f"2. From {self.test_users[1]['phone']}: Text 'Thai food at Student Union in 10 minutes'")
        print("3. Watch the terminal for real-time message tracking")
        print("4. Respond to any AI messages you receive")
        
        input("\n⏳ Press Enter when you're ready to start...")
        
        print("\n🕐 WAITING FOR YOUR MESSAGES...")
        print("Send the messages now! You have 2 minutes...")
        
        # Wait and show countdown
        for i in range(120):
            time.sleep(1)
            if i % 15 == 0 and i > 0:
                print(f"   ⏰ {120-i} seconds remaining...")
        
        # Check results
        self.check_matching_occurred(self.test_users[0]['phone'], self.test_users[1]['phone'], "Spontaneous Thai Match")
        
        print("\n✅ Test 1 completed!")
        input("\nPress Enter to continue to next test...")
    
    def test_group_responses_interactive(self):
        """Test group response handling"""
        print("\n🧪 TEST 2: Group Response Handling")
        print("=" * 60)
        print("📋 WHAT THIS TESTS:")
        print("   - YES/NO response classification")
        print("   - Order process initiation")
        print("   - Counter-proposal system")
        
        # Create manual negotiation
        negotiation_id = str(uuid.uuid4())
        phone1 = self.test_users[0]['phone']
        phone2 = self.test_users[1]['phone']
        
        negotiation_data = {
            'from_user': phone1,
            'to_user': phone2,
            'proposal': {
                'restaurant': 'Mario\'s Pizza',
                'location': 'Campus Center',
                'time': 'lunch',
                'requesting_user': phone1
            },
            'status': 'pending',
            'created_at': datetime.now(),
            'negotiation_id': negotiation_id
        }
        
        db.collection('negotiations').document(negotiation_id).set(negotiation_data)
        
        print(f"\n📱 INSTRUCTIONS:")
        print(f"1. From {phone2}: Text 'YES' to accept the group invitation")
        print("2. Watch for order process confirmation")
        print("3. Then try texting 'NO' to test rejection handling")
        
        input("\n⏳ Press Enter when ready...")
        
        print("\n🕐 WAITING FOR YES/NO RESPONSES...")
        print("Send your responses now! (60 seconds)")
        time.sleep(60)
        
        # Check if order process started
        order_session = db.collection('order_sessions').document(phone2).get()
        if order_session.exists:
            print("✅ YES response correctly started order process")
            self.test_results.append(("Group Response YES", True, "Order process started"))
        else:
            print("❌ YES response failed to start order process")
            self.test_results.append(("Group Response YES", False, "Order process not started"))
        
        print("\n✅ Test 2 completed!")
        input("\nPress Enter to continue to final test...")
    
    def test_proactive_notifications_interactive(self):
        """Test proactive notifications"""
        print("\n🧪 TEST 3: Proactive Notifications")
        print("=" * 60)
        print("📋 WHAT THIS TESTS:")
        print("   - Smart user targeting based on preferences")
        print("   - Location intelligence")
        print("   - Spam prevention")
        print("   - Notification personalization")
        
        # Create active group
        active_group_data = {
            "restaurant": "Thai Garden",
            "location": "Student Union",
            "time": "lunch",
            "current_members": [self.test_users[0]['phone']],
            "group_id": str(uuid.uuid4())
        }
        
        print(f"\n🔔 Triggering proactive notifications for Thai Garden group...")
        print("   This will check if the other user should be notified based on:")
        print("   - Their preference history")
        print("   - Location patterns")
        print("   - Recent notification limits")
        
        # Call the tool
        result = notify_compatible_users_of_active_groups.invoke({
            "active_group_data": active_group_data,
            "max_notifications": 2,
            "compatibility_threshold": 0.6
        })
        
        notifications_sent = result.get('notifications_sent', 0)
        print(f"\n📊 RESULT: {notifications_sent} notifications sent")
        
        if notifications_sent > 0:
            print("\n📱 INSTRUCTIONS:")
            print("1. Check your phones for proactive notifications")
            print("2. Reply YES or NO to test the response handling")
            print("3. Watch the message flow")
            
            print("\n🕐 WAITING FOR RESPONSES...")
            time.sleep(45)
            
            print("✅ Proactive notifications working")
            self.test_results.append(("Proactive Notifications", True, f"{notifications_sent} notifications sent"))
        else:
            print("ℹ️  No notifications sent (may be due to compatibility threshold or spam prevention)")
            self.test_results.append(("Proactive Notifications", False, "No notifications sent"))
        
        print("\n✅ All tests completed!")
    
    def check_matching_occurred(self, phone1: str, phone2: str, test_name: str):
        """Check if matching occurred"""
        time.sleep(3)
        
        negotiations = db.collection('negotiations')\
                        .where('from_user', '==', phone1)\
                        .get()
        
        found_match = False
        for neg in negotiations:
            neg_data = neg.to_dict()
            if neg_data.get('to_user') == phone2:
                found_match = True
                break
        
        if found_match:
            print(f"✅ {test_name}: Phones matched successfully!")
            self.test_results.append((test_name, True, "Phones matched"))
        else:
            print(f"❌ {test_name}: No match found")
            self.test_results.append((test_name, False, "Phones didn't match"))
    
    def print_test_results(self):
        """Print test results"""
        print("\n" + "=" * 80)
        print("📊 PANGEA AGENT TEST RESULTS")
        print("=" * 80)
        
        passed = 0
        failed = 0
        
        for test_name, success, details in self.test_results:
            status = "✅ PASS" if success else "❌ FAIL"
            print(f"{status} {test_name}: {details}")
            
            if success:
                passed += 1
            else:
                failed += 1
        
        print("-" * 80)
        if len(self.test_results) > 0:
            print(f"Total Tests: {len(self.test_results)}")
            print(f"Passed: {passed}")
            print(f"Failed: {failed}")
            print(f"Success Rate: {(passed/len(self.test_results)*100):.1f}%")
        
        if failed == 0 and len(self.test_results) > 0:
            print("🎉 ALL TESTS PASSED! Your agents are working correctly!")
        elif failed > 0:
            print(f"⚠️  {failed} tests need attention. Check the conversation flow above.")

def run_quick_compatibility_test():
    """Quick test without SMS"""
    print("🚀 Running Quick Compatibility Test (No SMS)")
    
    result = calculate_compatibility.invoke({
        "user1_restaurant": "Thai Garden",
        "user1_time": "lunch",
        "user2_restaurant": "Thai Garden", 
        "user2_time": "12pm",
        "user1_phone": "+17089011754",
        "user2_phone": "+16305470891"
    })
    
    print(f"🔍 Compatibility Score: {result}")
    
    if result > 0.5:
        print("✅ Core matching logic working")
    else:
        print("❌ Core matching logic may have issues")

if __name__ == "__main__":
    import sys
    
    print("🍜 Pangea Agent Testing Suite - Enhanced Edition")
    print("=" * 80)
    
    if len(sys.argv) > 1:
        if sys.argv[1] == "quick":
            run_quick_compatibility_test()
        elif sys.argv[1] == "mock":
            print("🎭 MOCK MODE - Messages logged but not sent")
            tester = PangeaAgentTester(mode="mock")
            tester.run_interactive_tests()
        else:
            print("Usage: python test_pangea_agents_enhanced.py [quick|mock]")
    else:
        print("📱 LIVE MODE - Real SMS testing with complete message tracking")
        print("Phones: +17089011754 and +16305470891")
        tester = PangeaAgentTester(mode="live")
        tester.run_interactive_tests()