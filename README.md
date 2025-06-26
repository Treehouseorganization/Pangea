# 🍜 Pangea - AI-Powered Food Delivery Coordination

**Pangea** is an innovative AI-powered food delivery coordination system designed for college campuses. It uses Claude 4 (Anthropic's latest AI model) to act as an "AI Friend" that helps students find lunch buddies and save money on delivery fees through intelligent group ordering.

## 🎯 Project Concept

### The Problem
College students often want to order food but face high delivery fees when ordering alone. Finding friends with similar food preferences and timing can be challenging and time-consuming.

### The Solution
Pangea creates an AI Friend for each user that:
- **Matches** users with similar restaurant preferences and timing
- **Negotiates** group formations between AI Friends representing different users
- **Coordinates** the entire ordering process via SMS
- **Manages** group payments and pickup logistics
- **Learns** and adapts to each user's preferences over time

## 🏗️ System Architecture

### Core Components

#### 1. **AI Friend System** (`pangea_main.py`)
- **LangGraph Workflow**: Orchestrates complex conversation flows using Anthropic's recommended patterns
- **Multi-Agent Negotiation**: AI Friends negotiate on behalf of their users
- **Intelligent Matching**: Uses Claude 4's reasoning to find compatible users
- **SMS Interface**: Handles all user communication through Twilio
- **Adaptive Learning**: Continuously learns from user interactions and improves performance

#### 2. **Order Processing System** (`pangea_order_processor.py`)
- **Order Flow Management**: Handles the process after group formation
- **Payment Coordination**: Manages group payment collection via Stripe
- **Pickup Coordination**: Ensures smooth group pickup logistics

#### 3. **Data Layer** (`pangea-firebase-key.json`)
- **Firebase Firestore**: Stores user preferences, order sessions, and conversation history
- **Real-time Updates**: Enables live coordination between users
- **Learning Database**: Stores AI-generated insights and user interaction patterns

## 🚀 Key Features

### 🤖 AI-Powered Intelligence
- **Claude 4 Integration**: Latest AI model for natural conversations and complex reasoning
- **Contextual Understanding**: Remembers user preferences and past interactions
- **Adaptive Learning**: Improves matching and communication over time

### 👥 Smart Group Formation
- **Preference Matching**: Matches users based on restaurant, location, and timing
- **Compatibility Scoring**: Uses deterministic logic + AI reasoning for optimal matches
- **Multi-Agent Negotiation**: AI Friends negotiate on behalf of their users
- **Historical Compatibility**: Remembers successful user combinations for future matching

### 💬 SMS-First Interface
- **Natural Conversations**: Claude 4 powers friendly, contextual SMS interactions
- **Real-time Coordination**: Instant updates and negotiations via SMS
- **Accessible Design**: Works on any phone without app installation
- **Personalized Communication**: Adapts tone and style based on user preferences

### 💳 Payment Integration
- **Group Discounts**: Automatic delivery fee reduction based on group size
- **Stripe Integration**: Secure payment processing for group fees
- **Transparent Pricing**: Clear cost breakdown for all participants

## 🧠 AI Learning Capabilities & Code Implementation

The learning system is implemented through several key functions in `pangea_main.py`:

### 1. **Memory Storage & Retrieval**
- **`get_user_preferences()` [Lines 98-127]**: Retrieves stored user data including:
  - Food preferences and favorite cuisines
  - Successful matches history
  - Preferred times and locations
  - Satisfaction scores from past orders

- **`update_user_memory()` [Lines 684-753]**: Stores new interaction data including:
  - Interaction type (successful_group_order, no_matches_found, etc.)
  - Restaurant, location, timing preferences
  - Group members and satisfaction scores
  - AI-generated insights from `extract_learning_insights()`

### 2. **AI-Powered Insight Extraction**
- **`extract_learning_insights()` [Lines 754-780]**: Uses Claude 4 to analyze interactions and extract:
  - Food preference updates (what they liked/disliked)
  - Timing insights (when they prefer to eat)
  - Social preferences (group size, compatibility patterns)
  - Price sensitivity patterns
  - Communication style preferences

### 3. **Historical Compatibility Learning**
- **`check_historical_compatibility()` [Lines 461-476]**: Checks if users have successfully ordered together before and returns perfect compatibility score (1.0) if they have

### 4. **Negotiation Strategy Learning**
- **`negotiate_with_other_ai()` [Lines 501-573]**: Uses target user's history to:
  - Access their preferences via `get_user_preferences()`
  - Generate personalized negotiation reasoning
  - Calculate success probability based on historical patterns

### 5. **Personalized Communication**
- **`enhance_message_with_context()` [Lines 657-683]**: Adapts message tone and content based on:
  - User's past interactions and preferences
  - Communication style patterns
  - Previous satisfaction scores

### 6. **Success Pattern Recognition**
- **`update_user_memory()`** stores successful patterns when satisfaction_score >= 7:
  - Restaurant preferences that worked well
  - Optimal group sizes for each user
  - Timing patterns that led to successful orders
  - Location preferences and success rates

### 7. **Learning Data Structure (Firebase Collections)**
- **`users` collection**: Stores per-user learning data
  - `preferences`: Learned food/timing/location preferences
  - `successful_matches`: History of successful group formations
  - `interactions`: Array of all interactions with AI insights
  - `successful_patterns`: Patterns that led to high satisfaction
  - `satisfaction_scores`: Historical satisfaction ratings

- **`negotiations` collection**: Stores negotiation attempts for learning
  - `target_user_preferences`: Used to improve future negotiations
  - `ai_reasoning`: Claude 4's reasoning for each negotiation
  - Success/failure outcomes for pattern recognition

- **`completed_orders` collection**: Stores successful orders for compatibility learning
  - `participants`: Users who successfully ordered together
  - Used by `check_historical_compatibility()` for future matching

## 📁 Project Structure

```
pangea/
├── pangea_main.py              # Core AI Friend system with matching and negotiation
├── pangea_order_processor.py   # Order flow management
├── pangea-firebase-key.json    # Firebase credentials
├── env_template.txt            # Environment configuration template
├── README.md                   # This file
└── requirements.txt            # Python dependencies
```

## 🛠️ Setup Instructions

### Prerequisites
- Python 3.8+
- Anthropic API account (for Claude 4)
- Twilio account (for SMS)
- Firebase project (for database)
- Stripe account (for payments, optional)

### Installation

1. **Clone the repository**
   ```bash
   git clone <repository-url>
   cd pangea
   ```

2. **Install dependencies**
   ```bash
   pip install -r requirements.txt
   ```

3. **Configure environment**
   ```bash
   cp env_template.txt .env
   # Edit .env with your actual credentials
   ```

4. **Set up Firebase**
   - Create a Firebase project
   - Download service account key as `pangea-firebase-key.json`
   - Place in project root

5. **Configure Twilio**
   - Get Account SID and Auth Token from Twilio console
   - Purchase a phone number for SMS
   - Update `.env` with credentials

6. **Set up Anthropic**
   - Get API key from Anthropic console
   - Update `ANTHROPIC_API_KEY` in `.env`

### Running the System

```bash
python pangea_main.py
```

The system will start a Flask server on port 8000 and be ready to receive SMS messages via Twilio webhooks.

## 🔄 How It Works

### 1. User Onboarding
- New users receive a welcome message explaining the system
- AI Friend learns their preferences over time

### 2. Food Request
- User texts their food preference (e.g., "I want Thai food at Student Union")
- AI Friend extracts restaurant, location, and timing preferences

### 3. Smart Matching
- System searches for compatible users in the database
- Uses deterministic logic + AI reasoning for compatibility scoring
- Finds users with similar preferences and timing
- **Learning**: Remembers successful combinations for future matching

### 4. Multi-Agent Negotiation
- AI Friends negotiate on behalf of their users
- Propose alternatives, timing adjustments, and incentives
- Reach consensus on restaurant, time, and group composition
- **Learning**: Improves negotiation strategies based on success rates

### 5. Group Formation
- Once group is formed, all members receive order instructions
- Each user places individual order with restaurant
- System coordinates payment collection and pickup
- **Learning**: Stores successful patterns for future reference

### 6. Order Coordination
- Users provide order confirmation numbers
- System manages group payment via Stripe
- Coordinates pickup logistics for the group
- **Learning**: Analyzes satisfaction and updates user preferences

## 🎯 Use Cases

### Morning Planning
- AI Friend proactively asks about lunch plans
- Finds matches early for better coordination
- **Learning**: Adapts timing based on user response patterns

### Spontaneous Orders
- User texts immediate food request
- System quickly finds available matches
- Fast group formation for immediate orders
- **Learning**: Improves speed and accuracy over time

### Group Coordination
- Handles complex multi-user negotiations
- Manages timing conflicts and preferences
- Ensures smooth group logistics
- **Learning**: Optimizes group size and composition

## 🔧 Configuration

### Environment Variables
See `env_template.txt` for complete configuration options:

- **AI Configuration**: Claude 4 model settings
- **SMS Configuration**: Twilio credentials and phone number
- **Database Configuration**: Firebase connection settings
- **Payment Configuration**: Stripe payment links
- **System Behavior**: Timeouts, retry limits, etc.
- **Learning Configuration**: AI learning and adaptation settings

### Restaurant Configuration
Currently supports 5 campus restaurants:
- Thai Garden (Student Union)
- Mario's Pizza (Campus Center)
- Sushi Express (Library Plaza)
- Burger Barn (Recreation Center)
- Green Bowls (Health Sciences Building)

## 🧪 Testing

### Development Mode
```bash
# Enable mock services for testing
ENABLE_MOCK_SMS=True
ENABLE_MOCK_PAYMENTS=True
DEBUG_MODE=True
```

### SMS Testing
- Use Twilio's test credentials for development
- Test webhook endpoints locally with ngrok

## 🚀 Deployment

### Production Setup
1. Set `FLASK_ENV=production`
2. Configure production Firebase project
3. Set up production Twilio phone number
4. Configure Stripe production payment links
5. Set up proper logging and monitoring

### Hosting Options
- **Heroku**: Easy deployment with add-ons
- **AWS**: EC2 with RDS for database
- **Google Cloud**: App Engine with Firestore
- **DigitalOcean**: Droplet with managed database

## 📊 Monitoring & Analytics

### Built-in Logging
- Conversation logs for debugging
- User interaction analytics
- Performance metrics
- Error tracking
- **Learning Analytics**: Track AI improvement over time

### Key Metrics
- Group formation success rate
- User satisfaction scores
- Order completion rates
- System response times
- **Learning Metrics**: Pattern recognition accuracy, negotiation success rates

## 🔒 Security & Privacy

### Data Protection
- User phone numbers stored securely
- No sensitive payment data stored
- Firebase security rules configured
- HTTPS for all webhook communications

### Privacy Features
- Users can opt out of morning check-ins
- Conversation history can be deleted
- No personal data shared between users
- GDPR-compliant data handling
- **Learning Privacy**: AI insights are user-specific and not shared

## 🤝 Contributing

### Development Guidelines
- Follow PEP 8 Python style guide
- Add comprehensive docstrings
- Include unit tests for new features
- Update documentation for changes

### Testing Strategy
- Unit tests for core functions
- Integration tests for workflows
- End-to-end SMS flow testing
- Performance testing for scaling
- **Learning Tests**: Validate AI improvement over time

## 📈 Future Enhancements

### Planned Features
- **Mobile App**: Native iOS/Android apps
- **Restaurant Integration**: Direct API connections
- **Advanced Analytics**: ML-powered insights
- **Campus Expansion**: Multi-campus support
- **Dietary Restrictions**: Allergy and preference filtering

### Technical Improvements
- **Real-time Updates**: WebSocket connections
- **Caching Layer**: Redis for performance
- **Microservices**: Service-oriented architecture
- **AI Enhancements**: Custom fine-tuned models
- **Advanced Learning**: Deep learning for pattern recognition

## 📞 Support

### Documentation
- API documentation available
- User guides and tutorials
- Troubleshooting guides
- FAQ section

### Contact
- Technical issues: GitHub Issues
- Feature requests: GitHub Discussions
- General questions: Project maintainers

## 📄 License

This project is licensed under the MIT License - see the LICENSE file for details.

---

**Pangea** - Making lunch coordination as easy as texting a friend! 🍕👥 