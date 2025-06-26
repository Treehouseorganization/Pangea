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

## 🏗️ System Architecture

### Core Components

#### 1. **AI Friend System** (`pangea_main.py`)
- **LangGraph Workflow**: Orchestrates complex conversation flows using Anthropic's recommended patterns
- **Multi-Agent Negotiation**: AI Friends negotiate on behalf of their users
- **Intelligent Matching**: Uses Claude 4's reasoning to find compatible users
- **SMS Interface**: Handles all user communication through Twilio

#### 2. **Order Processing System** (`pangea_order_processor.py`)
- **Order Flow Management**: Handles the process after group formation
- **Payment Coordination**: Manages group payment collection via Stripe
- **Pickup Coordination**: Ensures smooth group pickup logistics

#### 3. **Data Layer** (`pangea-firebase-key.json`)
- **Firebase Firestore**: Stores user preferences, order sessions, and conversation history
- **Real-time Updates**: Enables live coordination between users

## 🚀 Key Features

### 🤖 AI-Powered Intelligence
- **Claude 4 Integration**: Latest AI model for natural conversations and complex reasoning
- **Contextual Understanding**: Remembers user preferences and past interactions
- **Adaptive Learning**: Improves matching and communication over time

### 👥 Smart Group Formation
- **Preference Matching**: Matches users based on restaurant, location, and timing
- **Compatibility Scoring**: Uses deterministic logic + AI reasoning for optimal matches
- **Multi-Agent Negotiation**: AI Friends negotiate on behalf of their users

### 💬 SMS-First Interface
- **Natural Conversations**: Claude 4 powers friendly, contextual SMS interactions
- **Real-time Coordination**: Instant updates and negotiations via SMS
- **Accessible Design**: Works on any phone without app installation

### 💳 Payment Integration
- **Group Discounts**: Automatic delivery fee reduction based on group size
- **Stripe Integration**: Secure payment processing for group fees
- **Transparent Pricing**: Clear cost breakdown for all participants

## 📁 Project Structure

```
pangea/
├── pangea_main.py              # Core AI Friend system
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

### 4. Multi-Agent Negotiation
- AI Friends negotiate on behalf of their users
- Propose alternatives, timing adjustments, and incentives
- Reach consensus on restaurant, time, and group composition

### 5. Group Formation
- Once group is formed, all members receive order instructions
- Each user places individual order with restaurant
- System coordinates payment collection and pickup

### 6. Order Coordination
- Users provide order confirmation numbers
- System manages group payment via Stripe
- Coordinates pickup logistics for the group

## 🎯 Use Cases

### Morning Planning
- AI Friend proactively asks about lunch plans
- Finds matches early for better coordination

### Spontaneous Orders
- User texts immediate food request
- System quickly finds available matches
- Fast group formation for immediate orders

### Group Coordination
- Handles complex multi-user negotiations
- Manages timing conflicts and preferences
- Ensures smooth group logistics

## 🔧 Configuration

### Environment Variables
See `env_template.txt` for complete configuration options:

- **AI Configuration**: Claude 4 model settings
- **SMS Configuration**: Twilio credentials and phone number
- **Database Configuration**: Firebase connection settings
- **Payment Configuration**: Stripe payment links
- **System Behavior**: Timeouts, retry limits, etc.

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

### Key Metrics
- Group formation success rate
- User satisfaction scores
- Order completion rates
- System response times

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