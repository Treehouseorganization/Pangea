"""
Clean Flask Application with Async Support
Main entry point for the rewritten Pangea system
"""

import asyncio
import os
from flask import Flask, request, jsonify
from main import PangeaApp
from datetime import datetime

# Initialize Flask app
app = Flask(__name__)

# Initialize Pangea application
pangea_app = None

def initialize_pangea():
    """Initialize Pangea application"""
    global pangea_app
    if pangea_app is None:
        pangea_app = PangeaApp()
    return pangea_app

@app.route('/', methods=['GET'])
def health_check():
    """Health check endpoint"""
    return {
        'status': 'healthy',
        'service': 'Pangea Food Coordination System',
        'timestamp': datetime.now().isoformat(),
        'version': '3.0-clean-architecture'
    }, 200

@app.route('/webhook/sms', methods=['POST'])
@app.route('/webhook', methods=['POST'])
def sms_webhook():
    """Handle incoming SMS messages"""
    try:
        # Ensure Pangea app is initialized
        app_instance = initialize_pangea()
        
        from_number = request.form.get('From')
        message_body = request.form.get('Body')
        
        if not from_number or not message_body:
            print("❌ Missing required fields in webhook")
            return '', 400
        
        print(f"📱 SMS WEBHOOK RECEIVED:")
        print(f"   📞 From: {from_number}")
        print(f"   💬 Message: '{message_body}'")
        print(f"   🕒 Timestamp: {datetime.now().isoformat()}")
        print(f"   📊 Message Length: {len(message_body)} chars")
        
        # Handle message asynchronously
        print(f"🔄 Processing message for {from_number}...")
        result = asyncio.run(app_instance.handle_message(from_number, message_body))
        print(f"✅ Message processing completed with result: {result}")
        
        if result['status'] == 'success':
            print(f"✅ MESSAGE PROCESSING SUCCESS:")
            print(f"   🎯 User Stage: {result.get('stage', 'unknown')}")
            print(f"   📋 Actions Triggered: {result.get('actions_taken', [])}")
            print(f"   💰 Payment Amount: {result.get('payment_amount', 'N/A')}")
            print(f"   🍕 Restaurant: {result.get('restaurant', 'N/A')}")
            print(f"   📍 Location: {result.get('location', 'N/A')}")
            if result.get('response_sent'):
                print(f"   📤 Response Sent: '{result['response_sent'][:100]}{'...' if len(result.get('response_sent', '')) > 100 else ''}'")
        else:
            print(f"❌ MESSAGE PROCESSING FAILED:")
            print(f"   ⚠️ Error: {result.get('error', 'unknown')}")
            print(f"   🔍 Debug Info: {result.get('debug_info', 'none')}")
        
        return '', 200
        
    except Exception as e:
        print(f"❌ WEBHOOK ERROR:")
        print(f"   🚨 Exception: {str(e)}")
        print(f"   📋 Exception Type: {type(e).__name__}")
        import traceback
        print(f"   📚 Traceback: {traceback.format_exc()}")
        
        # Try to send error message to user if possible
        try:
            if 'from_number' in locals() and from_number:
                app_instance = initialize_pangea()
                error_response = "Sorry, I'm having technical difficulties. Please try again in a few minutes!"
                app_instance.send_sms(from_number, error_response)
        except Exception as sms_error:
            print(f"❌ ERROR SMS FAILED:")
            print(f"   📱 SMS Error: {str(sms_error)}")
            print(f"   📞 Target Number: {from_number}")
        
        return '', 500

@app.route('/status/<phone_number>', methods=['GET'])
def user_status(phone_number):
    """Get user status for debugging"""
    try:
        app_instance = initialize_pangea()
        
        # Get user state
        user_state = asyncio.run(app_instance.memory_manager.get_user_state(phone_number))
        
        return {
            'user_phone': phone_number,
            'stage': user_state.stage.value,
            'restaurant': user_state.restaurant,
            'location': user_state.location,
            'delivery_time': user_state.delivery_time,
            'group_id': user_state.group_id,
            'group_size': user_state.group_size,
            'is_fake_match': user_state.is_fake_match,
            'missing_info': user_state.missing_info,
            'payment_requested': user_state.payment_requested_at is not None,
            'last_activity': user_state.last_activity.isoformat() if user_state.last_activity else None,
            'conversation_length': len(user_state.conversation_history)
        }, 200
        
    except Exception as e:
        return {'error': str(e)}, 500

@app.route('/cleanup', methods=['POST'])
def manual_cleanup():
    """Manual cleanup endpoint for maintenance"""
    try:
        app_instance = initialize_pangea()
        
        # Clean up stale states
        cleaned_count = asyncio.run(app_instance.memory_manager.cleanup_stale_states())
        
        return {
            'status': 'cleanup completed',
            'cleaned_states': cleaned_count,
            'timestamp': datetime.now().isoformat()
        }, 200
        
    except Exception as e:
        return {'error': str(e)}, 500

@app.route('/test/message', methods=['POST'])
def test_message():
    """Test endpoint for development"""
    
    if not os.getenv('DEBUG_MODE'):
        return {'error': 'Not available in production'}, 403
    
    try:
        data = request.get_json()
        phone_number = data.get('phone_number')
        message = data.get('message')
        
        if not phone_number or not message:
            return {'error': 'Missing phone_number or message'}, 400
        
        app_instance = initialize_pangea()
        result = asyncio.run(app_instance.handle_message(phone_number, message))
        
        return result, 200
        
    except Exception as e:
        return {'error': str(e)}, 500

@app.route('/stats', methods=['GET'])
def system_stats():
    """Get system statistics"""
    try:
        app_instance = initialize_pangea()
        
        # Get stats from memory manager
        from models import OrderStage
        
        stats = {}
        for stage in OrderStage:
            users = asyncio.run(app_instance.memory_manager.get_users_by_stage(stage))
            stats[stage.value] = len(users)
        
        return {
            'user_stages': stats,
            'timestamp': datetime.now().isoformat()
        }, 200
        
    except Exception as e:
        return {'error': str(e)}, 500

if __name__ == "__main__":
    print("🍜 Starting Pangea Food Coordination System v3.0...")
    print("🏗️ Clean architecture with unified conversation management")
    
    port = int(os.environ.get('PORT', 8000))
    debug_mode = os.getenv('DEBUG_MODE', 'false').lower() == 'true'
    
    if debug_mode:
        print("🐛 Debug mode enabled")
    
    # Initialize the app
    initialize_pangea()
    
    app.run(host='0.0.0.0', port=port, debug=debug_mode)

