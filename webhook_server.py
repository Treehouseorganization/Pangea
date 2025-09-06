"""
Stripe Webhook Server
Flask server to handle Stripe payment webhooks for Pangea food delivery
"""

from flask import Flask, request, jsonify
import logging
from stripe_handler import process_webhook

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)

@app.route('/stripe/webhook', methods=['POST'])
def stripe_webhook():
    """Handle Stripe webhook events"""
    try:
        # Get raw payload and signature
        payload = request.get_data()
        signature = request.headers.get('Stripe-Signature')
        
        if not signature:
            logger.error("Missing Stripe signature header")
            return jsonify({'error': 'Missing signature'}), 400
        
        # Process the webhook
        result = process_webhook(payload, signature)
        
        if result.get('status') == 'success':
            logger.info(f"Webhook processed successfully: {result}")
            return jsonify({'status': 'success'}), 200
        elif result.get('status') == 'error':
            logger.error(f"Webhook processing error: {result}")
            return jsonify({'error': result.get('message', 'Unknown error')}), 400
        else:
            # Webhook event was ignored (not a payment event we care about)
            logger.info(f"Webhook ignored: {result}")
            return jsonify({'status': 'ignored'}), 200
            
    except Exception as e:
        logger.error(f"Unexpected webhook error: {e}")
        return jsonify({'error': 'Internal server error'}), 500

@app.route('/health', methods=['GET'])
def health_check():
    """Health check endpoint"""
    return jsonify({'status': 'healthy', 'service': 'pangea-webhook-server'}), 200

if __name__ == '__main__':
    import os
    
    # Get port from environment or default to 8080
    port = int(os.environ.get('WEBHOOK_PORT', 8080))
    debug_mode = os.environ.get('FLASK_DEBUG', 'False').lower() == 'true'
    
    logger.info(f"Starting Pangea webhook server on port {port}")
    logger.info("Webhook endpoint: /stripe/webhook")
    logger.info("Health check endpoint: /health")
    
    app.run(host='0.0.0.0', port=port, debug=debug_mode)