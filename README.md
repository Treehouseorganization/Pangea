# 🍜 Pangea - AI-Powered Group Food Ordering

**Pangea** is an AI-powered food ordering coordination system that helps users find others to group order with, reducing delivery fees through intelligent matching and group coordination via SMS.

## 🎯 Project Overview

### The Problem
High delivery fees make individual food orders expensive. Finding compatible people to group order with requires coordination that's often too time-consuming.

### The Solution
Pangea uses AI to:
- **Match** users with similar restaurant and location preferences
- **Coordinate** group formation and timing via SMS
- **Integrate** with Uber Direct for actual delivery management
- **Learn** from user interactions to improve future matching

## 🏗️ System Architecture

### Core Components

#### 1. **Main Coordination System** (`pangea_main.py`)
- **LangGraph Workflow**: Orchestrates conversation flows and state management
- **User Matching**: Finds compatible users based on restaurant, location, and timing preferences
- **SMS Interface**: Handles all communication through Twilio
- **Preference Learning**: Stores and learns from user interactions to improve matching

#### 2. **Order Processing** (`pangea_order_processor.py`)
- **Group Order Management**: Coordinates the ordering process after group formation
- **Payment Links**: Manages Stripe payment links for delivery fees
- **Status Tracking**: Monitors order progress and notifications

#### 3. **Delivery Integration** (`pangea_uber_direct.py`)
- **Uber Direct API**: Creates and tracks actual deliveries
- **Restaurant Coordination**: Manages pickup from restaurants
- **Delivery Tracking**: Provides real-time delivery updates

#### 4. **Location Management** (`pangea_locations.py`)
- **Restaurant Database**: Chicago-area restaurants (Chipotle, McDonald's, Chick-fil-A, Portillo's, Starbucks)
- **Drop-off Locations**: UIC campus locations (Library, Student Centers, University Hall)

#### 5. **Data Layer** (Firebase Firestore)
- **User Profiles**: Stores preferences and interaction history
- **Group Sessions**: Manages active ordering groups
- **Learning Data**: AI-extracted insights for improving matches

## 🚀 Key Features

### 🤖 AI-Powered Matching
- **Claude Opus 4**: Powers natural conversation and matching logic
- **Preference Learning**: Remembers user food, location, and timing preferences
- **Smart Matching**: Finds compatible users based on multiple factors
- **Contextual Responses**: Adapts communication based on user history

### 👥 Group Coordination
- **Restaurant & Location Matching**: Matches users wanting the same restaurant and delivery location
- **Timing Coordination**: Finds optimal group ordering times
- **Group Size Limits**: Maximum 3 people per group for manageable coordination
- **Targeted Invitations**: Sends group invitations to compatible users who haven't ordered yet
- **Alternative Suggestions**: Proposes alternatives when initial matches fail

### 📱 SMS Interface
- **Twilio Integration**: All communication via SMS - no app required
- **Morning Check-ins**: Proactive outreach around 11 AM to boost match rates
- **Real-time Updates**: Instant notifications about group status
- **Natural Language**: Conversational interface for easy interaction

### 🚚 Delivery Management
- **Uber Direct Integration**: Actual delivery creation and tracking
- **Payment Links**: Stripe payment links for delivery fees
- **Real-time Tracking**: Live updates on delivery status

## 🧠 AI Learning & Matching System

### Learning Capabilities
The system learns from user interactions to improve future matching:

#### **Preference Learning** (`get_user_preferences()` & `update_user_memory()`)
- **Food Preferences**: Remembers favorite cuisines and restaurants
- **Location Patterns**: Tracks usual delivery locations  
- **Timing Preferences**: Learns preferred meal times
- **Group History**: Stores successful group combinations

#### **AI Insight Extraction** (`extract_learning_insights()`)
- Uses Claude to analyze interactions and extract patterns
- Updates user profiles with learned preferences
- Identifies compatibility patterns between users

#### **Smart Matching Algorithm**
The matching system combines multiple scoring factors:

1. **Restaurant Compatibility** (40% weight) - `restaurant_compatibility_score()`
2. **Location Matching** (30% weight) - `location_compatibility_score()` 
3. **Timing Alignment** (20% weight) - `timing_compatibility_score()`
4. **Historical Success** (10% weight) - `historical_compatibility_score()`

#### **Rejection Learning** (`learn_from_rejection()`)
- Analyzes why users declined group invitations
- Suggests better alternatives based on user preferences
- Improves future matching accuracy

## 📁 Project Structure

```
Pangea-2/
├── pangea_main.py              # Core coordination system with AI matching
├── pangea_order_processor.py   # Order flow management after group formation  
├── pangea_uber_direct.py       # Uber Direct delivery integration
├── pangea_locations.py         # Restaurant and location database
├── pangea-firebase-key.json    # Firebase service account credentials
├── env_template.txt            # Environment configuration template
├── requirements.txt            # Python dependencies
├── test_*.py                   # Test files for different functionality
└── cleanup_*.py                # Database maintenance scripts
```

## 🛠️ Setup Instructions

### Prerequisites
- Python 3.8+
- Anthropic API account (for Claude Opus 4)
- Twilio account (for SMS)
- Firebase project (for database)
- Uber Direct API access (for delivery)
- Stripe account (for payment links)

### Installation

1. **Clone the repository**
   ```bash
   git clone <repository-url>
   cd Pangea-2
   ```

2. **Install dependencies**
   ```bash
   pip install -r requirements.txt
   ```

3. **Configure environment**
   ```bash
   cp env_template.txt .env
   # Edit .env with your actual API keys and credentials
   ```

4. **Set up Firebase**
   - Create a Firebase project with Firestore
   - Download service account key as `pangea-firebase-key.json`
   - Place in project root

5. **Configure Services**
   - **Twilio**: SMS capabilities with webhook URL
   - **Anthropic**: Claude Opus 4 API access
   - **Uber Direct**: Delivery API credentials
   - **Stripe**: Payment link creation

### Running the System

```bash
python pangea_main.py
```

The system starts a Flask server on port 8000 for Twilio webhooks.

## 🔄 How It Works

### 1. User Request
- User texts food request (e.g., "I want Chipotle delivered to the Library")
- System extracts restaurant, location, and timing preferences
- Searches for compatible users with similar requests

### 2. Smart Matching  
- Finds users wanting the same restaurant and delivery location
- Scores compatibility based on timing and historical success
- Sends targeted invitations to compatible users who haven't ordered yet

### 3. Group Formation
- Users can accept/decline group invitations via SMS
- System handles up to 3 people per group
- Coordinates optimal ordering time for the group

### 4. Order Processing
- Transitions to order processing system
- Creates Uber Direct delivery request
- Provides Stripe payment links for delivery fees
- Tracks order status and notifies group members

### 5. Learning & Improvement
- Stores successful group combinations
- Learns from rejections to improve future matching
- Updates user preferences based on interactions

## 🎯 Use Cases

### Morning Check-ins
- System sends proactive messages around 11 AM to boost match rates
- Users can respond with lunch preferences and locations
- Increases group formation success by coordinating early
- Users can opt out of morning messages if preferred

### Spontaneous Orders
- "I want Chipotle at Library now" 
- System quickly finds compatible users
- Forms groups for immediate ordering

### Scheduled Deliveries
- "I want McDonald's at 2pm"
- System waits and coordinates timing
- Triggers delivery at specified time

## 🔧 Configuration

### Environment Variables
Key configuration in `env_template.txt`:

- **Claude Opus 4**: `ANTHROPIC_API_KEY` and model settings
- **Twilio SMS**: Account SID, auth token, phone number
- **Firebase**: Service account JSON for Firestore
- **Uber Direct**: API credentials for delivery
- **Stripe**: Payment links for different group sizes

### Restaurant Database
Currently configured for Chicago/UIC area:

**Restaurants:**
- Chipotle (1132 S Clinton St)
- McDonald's (2315 W Ogden Ave) 
- Chick-fil-A (1106 S Clinton St)
- Portillo's (520 W Taylor St)
- Starbucks (1430 W Taylor St)

**Drop-off Locations:**
- Richard J Daley Library
- Student Center East/West
- Student Services Building  
- University Hall

## 🧪 Testing

### Test Files
The project includes comprehensive test suites:

- `test_pangea_agents_enhanced.py` - Core matching functionality
- `test_scheduled_delivery.py` - Delivery timing tests
- `test_group_limit_and_payment_links.py` - Group size and payment tests
- `test_overall_functionality_7_14_25.py` - End-to-end workflow tests

### Development Mode
```bash
# Environment variables for testing
ENABLE_MOCK_SMS=True
ENABLE_MOCK_PAYMENTS=True  
DEBUG_MODE=True
```

## 🚀 Deployment

### Production Setup
1. Set `FLASK_ENV=production` 
2. Configure production Firebase project
3. Set up production Twilio phone number and webhooks
4. Configure Uber Direct production API credentials
5. Set up Stripe production payment links

### Webhook Configuration
- Twilio webhook URL: `your-domain.com/sms` (for SMS handling)
- Flask server runs on port 8000 by default

## 📊 Monitoring & Analytics

### Built-in Features
- Conversation logging for debugging
- User preference tracking  
- Group formation analytics
- Order completion metrics
- Firebase-based data persistence

## 🔒 Security & Privacy

### Data Protection
- Firebase security rules for user data
- No payment data stored (Stripe handles payments)
- HTTPS for webhook communications
- User phone numbers encrypted in storage

### Privacy Features
- Users can opt out of morning check-ins
- No personal data shared between users  
- User preferences stored individually
- Conversation history managed per user

## 🤝 Contributing

### Development Guidelines
- Follow PEP 8 Python style guide
- Test new features with provided test files
- Update documentation for changes
- Use the existing LangGraph workflow patterns

### Testing
Run the included test suites to validate functionality:
```bash
python test_pangea_agents_enhanced.py
python test_scheduled_delivery.py
```

## 📈 Technical Notes

### Current Architecture
- **LangGraph**: State machine for conversation flows
- **Claude Opus 4**: AI reasoning and natural language
- **Firebase Firestore**: Document-based data storage  
- **Uber Direct API**: Real delivery management
- **Twilio**: SMS communication gateway

### Known Limitations
- Currently configured for Chicago/UIC area
- Maximum 3 people per group
- Requires manual restaurant/location database updates
- SMS-only interface (no web UI)

---

**Pangea** - AI-powered group food ordering via SMS 🍕📱 