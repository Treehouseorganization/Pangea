"""
Clean up specific user data from Firebase
"""
import os
import json
from dotenv import load_dotenv
import firebase_admin
from firebase_admin import credentials, firestore

# Load environment variables
load_dotenv()

# Initialize Firebase
if not firebase_admin._apps:
    # Try JSON content first, then file path
    firebase_json = os.getenv('FIREBASE_SERVICE_ACCOUNT_JSON')
    firebase_path = os.getenv('FIREBASE_SERVICE_ACCOUNT_PATH')
    
    if firebase_json:
        try:
            firebase_config = json.loads(firebase_json)
            cred = credentials.Certificate(firebase_config)
            firebase_admin.initialize_app(cred)
            print("✅ Firebase initialized successfully from JSON")
        except Exception as e:
            print(f"❌ Firebase initialization failed from JSON: {e}")
            exit(1)
    elif firebase_path:
        try:
            cred = credentials.Certificate(firebase_path)
            firebase_admin.initialize_app(cred)
            print("✅ Firebase initialized successfully from file path")
        except Exception as e:
            print(f"❌ Firebase initialization failed from file path: {e}")
            exit(1)
    else:
        print("❌ Neither FIREBASE_SERVICE_ACCOUNT_JSON nor FIREBASE_SERVICE_ACCOUNT_PATH environment variable set")
        exit(1)

# Initialize Firestore client
db = firestore.client()

def cleanup_user_data(phone_number):
    """Delete all data for specific phone number"""
    
    print(f"🧹 Cleaning up data for {phone_number}...")
    
    collections_to_clean = [
        'active_orders',
        'negotiations', 
        'notification_history',
        'order_sessions',
        'users',
        'pending_invitations',
        'group_sessions'
    ]
    
    total_deleted = 0
    
    for collection_name in collections_to_clean:
        try:
            print(f"🔍 Checking collection: {collection_name}")
            docs = db.collection(collection_name).get()
            doc_count = len(docs)
            print(f"  📊 Found {doc_count} documents in {collection_name}")
            
            deleted_count = 0
            for i, doc in enumerate(docs):
                if i % 10 == 0 and i > 0:
                    print(f"  📍 Processed {i}/{doc_count} documents...")
                    
                doc_data = doc.to_dict()
                
                # Check for phone number matches
                should_delete = False
                
                if 'user_phone' in doc_data and doc_data['user_phone'] == phone_number:
                    should_delete = True
                
                if 'phone' in doc_data and doc_data['phone'] == phone_number:
                    should_delete = True
                    
                # Check group members
                if 'members' in doc_data:
                    members = doc_data.get('members', [])
                    if phone_number in members:
                        should_delete = True
                
                # Check group IDs that contain this phone number
                if 'group_id' in doc_data:
                    group_id = doc_data.get('group_id', '')
                    if phone_number.replace('+', '') in group_id:
                        should_delete = True
                
                if should_delete:
                    print(f"  🗑️ Deleting doc {doc.id} from {collection_name}")
                    doc.reference.delete()
                    deleted_count += 1
            
            total_deleted += deleted_count
            if deleted_count > 0:
                print(f"✅ Deleted {deleted_count} documents from {collection_name}")
            else:
                print(f"✅ No matching documents found in {collection_name}")
            
        except Exception as e:
            print(f"❌ Error cleaning {collection_name}: {e}")
    
    print(f"🎉 Cleanup completed! Deleted {total_deleted} total documents for {phone_number}")

if __name__ == "__main__":
    cleanup_user_data("+17087965432")