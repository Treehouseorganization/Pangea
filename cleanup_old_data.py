"""
Clean up old test data from Firebase
"""
from datetime import datetime, timedelta
from pangea_main import db

def cleanup_all_old_data():
    """Clean up all old test data"""
    print("🧹 Cleaning up old data...")
    
    collections_to_clean = [
        'active_orders',
        'negotiations', 
        'notification_history',
        'order_sessions',
        'users'
    ]
    
    for collection_name in collections_to_clean:
        try:
            # Get all documents in collection
            docs = db.collection(collection_name).get()
            
            deleted_count = 0
            for doc in docs:
                doc_data = doc.to_dict()
                
                # Delete obvious test data by phone number
                should_delete = False
                
                # Check for test phone numbers
                if 'user_phone' in doc_data:
                    phone = doc_data['user_phone']
                    if phone in ['+17408349474', '+1555TEST001', '+1555TEST002', '+1555TEST003', '+1555TEST004']:
                        should_delete = True
                
                if 'phone' in doc_data:
                    phone = doc_data['phone']
                    if phone in ['+17408349474', '+1555TEST001', '+1555TEST002', '+1555TEST003', '+1555TEST004']:
                        should_delete = True
                
                # Check for old documents (simple date check without timezone comparison)
                if 'created_at' in doc_data:
                    created_at = doc_data['created_at']
                    try:
                        # Convert to datetime if it's a timestamp
                        if hasattr(created_at, 'timestamp'):
                            created_timestamp = created_at.timestamp()
                            current_timestamp = datetime.now().timestamp()
                            # Delete if older than 7 days
                            if current_timestamp - created_timestamp > (7 * 24 * 60 * 60):
                                should_delete = True
                    except:
                        pass  # Skip date comparison if it fails
                
                # Check for specific old restaurants that don't match your current setup
                if 'restaurant' in doc_data:
                    restaurant = doc_data['restaurant']
                    if restaurant == 'Burger Barn':  # This was in your old data
                        should_delete = True
                
                if should_delete:
                    doc.reference.delete()
                    deleted_count += 1
            
            print(f"✅ Cleaned {deleted_count} documents from {collection_name}")
            
        except Exception as e:
            print(f"❌ Error cleaning {collection_name}: {e}")
    
    print("🎉 Cleanup completed!")

if __name__ == "__main__":
    cleanup_all_old_data()