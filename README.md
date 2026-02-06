# Spotify Insights - Scalable Production Architecture

A production-ready FastAPI application for analyzing Spotify listening patterns with AI-powered insights. Features persistent storage, background processing, caching, and horizontal scalability.

## 🏗️ Architecture Overview

This is a complete redesign following the **Scalable Spotify Insights Application** system design document. The architecture has been evolved from a stateless demo into a production-oriented system with:

- **Persistent Data Storage** (PostgreSQL)
- **Background Task Processing** (Celery + Redis)  
- **Caching Layer** (Redis)
- **Horizontal Scalability** (Stateless API, multiple workers)
- **LLM Integration** (LangChain + OpenAI)

```
CLIENT → FastAPI API (Stateless) → PostgreSQL / Redis
                ↓
            Celery Workers (Spotify, Insights, Scheduled)
                ↓
        Spotify API / OpenAI API
```

## ✨ What's New in V2.0 (Scalable Architecture)

### Data Model Changes
- ✅ **Users table**: Decoupled from Spotify, supports app-level identity
- ✅ **Spotify tokens table**: Secure OAuth token management with lifecycle tracking
- ✅ **Listening snapshots table**: Aggregated time-series data instead of raw history
- ✅ **Generated insights table**: Versioned LLM outputs with metadata
- ✅ **Background jobs table**: Track async task execution

### Background Processing
- ✅ **Celery workers**: Separate queues for Spotify, insights, and scheduled tasks
- ✅ **Token refresh**: Automatic token renewal before expiration
- ✅ **Data ingestion**: Async fetching and aggregation of listening data
- ✅ **Insight generation**: LLM-powered analysis in background
- ✅ **Scheduled tasks**: Daily ingestion, weekly summaries, cleanup

### Caching Strategy
- ✅ **Redis caching**: API responses, snapshots, and rate limiting
- ✅ **Cache invalidation**: Automatic on data updates
- ✅ **Performance**: Reduced latency and API costs

### Scalability
- ✅ **Stateless API**: No in-memory user state, horizontal scaling ready
- ✅ **Worker pools**: Scale independently per task type
- ✅ **Database pooling**: Connection management for high concurrency
- ✅ **Health checks**: Load balancer integration

## 📋 Requirements

- Python 3.11+
- Docker & Docker Compose
- PostgreSQL 15
- Redis 7
- Spotify Developer Account
- OpenAI API Key

## 🚀 Quick Start

### 1. Environment Setup

```bash
# Create .env file
cat > .env << EOF
SPOTIFY_CLIENT_ID=your_client_id
SPOTIFY_CLIENT_SECRET=your_client_secret
SPOTIFY_REDIRECT_URI=http://localhost:8000/callback
OPENAI_API_KEY=your_openai_key
EOF
```

### 2. Start All Services

```bash
docker-compose up --build
```

This starts:
- PostgreSQL (port 5432)
- Redis (port 6379)
- FastAPI API (port 8000)
- 3 Celery worker pools
- Celery Beat scheduler
- Flower monitoring (port 5555)

### 3. Access the Application

- **API**: http://localhost:8000
- **API Docs**: http://localhost:8000/docs  
- **Wellness Page**: http://localhost:8000/wellness
- **Flower**: http://localhost:5555

## 📊 Database Schema

### Core Tables

**users**: Application users (decoupled from Spotify)
**spotify_tokens**: OAuth tokens with auto-refresh
**listening_snapshots**: Aggregated listening data by time period
**generated_insights**: LLM-generated analysis with versioning
**background_jobs**: Async task monitoring

See database_models.py for complete schema.

## 🔄 Background Task Queues

### Queue: `spotify`
- Token refresh
- Data ingestion from Spotify API
- API rate limiting

### Queue: `insights`  
- LLM-powered wellness insights
- Music taste roasts
- Productivity analysis

### Queue: `scheduled`
- Daily data ingestion for all users (2 AM)
- Weekly summaries (Sunday 9 AM)
- Job cleanup (Monday 3 AM)

## 🔌 Key API Endpoints

### User & Auth
```
POST /api/users/register         - Register user
POST /api/tokens/store           - Store OAuth tokens
```

### Listening Data (Async)
```
POST /api/listening/ingest       - Queue data ingestion
GET  /api/listening/snapshots    - Get snapshots
```

### Insights (Async)
```
POST /api/insights/generate      - Queue insight generation
GET  /api/insights/{id}          - Get insight
GET  /api/insights/user/{id}     - Get user's insights
```

### Tasks
```
GET /api/tasks/{task_id}         - Check background task status
```

## 💡 Usage Example

```python
import requests, time

# 1. Register user
user = requests.post("http://localhost:8000/api/users/register", json={
    "spotify_user_id": "spotify_123",
    "display_name": "Alice"
}).json()

# 2. Store token
requests.post("http://localhost:8000/api/tokens/store", json={
    "user_id": user["user_id"],
    "access_token": "BQD...",
    "refresh_token": "AQC...",
    "expires_in": 3600
})

# 3. Trigger data ingestion (async)
ingest_task = requests.post("http://localhost:8000/api/listening/ingest", params={
    "user_id": user["user_id"],
    "time_range": "medium_term"
}).json()

# 4. Check task status
status = requests.get(f"http://localhost:8000/api/tasks/{ingest_task['task_id']}").json()
print(status["status"])  # PENDING → STARTED → SUCCESS

# 5. Get snapshot
time.sleep(15)  # Wait for ingestion
snapshots = requests.get("http://localhost:8000/api/listening/snapshots", params={
    "user_id": user["user_id"]
}).json()

# 6. Generate insight (async)
insight_task = requests.post("http://localhost:8000/api/insights/generate", params={
    "snapshot_id": snapshots[0]["snapshot_id"],
    "insight_type": "wellness",
    "tone_mode": "supportive"
}).json()

# 7. Get generated insight
time.sleep(20)  # Wait for LLM
insight = requests.get(f"http://localhost:8000/api/insights/{insight_task['result']['insight_id']}").json()
print(insight["content"])
```

## 🔧 Development

### Local Setup (Without Docker)

```bash
# Install dependencies
pip install -r requirements.txt

# Start services manually
# PostgreSQL on port 5432
# Redis on port 6379

# Initialize DB
python -c "from database_config import init_db; init_db()"

# Start API
uvicorn main:app --reload

# Start workers (separate terminals)
celery -A celery_config worker --queues=spotify -l info
celery -A celery_config worker --queues=insights -l info
celery -A celery_config beat -l info
```

## 📈 Monitoring

### Flower Dashboard
http://localhost:5555
- Task monitoring
- Worker status
- Task history and statistics

### Logs
```bash
docker-compose logs -f api
docker-compose logs -f celery_worker_spotify
```

## 🚀 Production Deployment

### Recommended Setup
1. **Managed PostgreSQL**: AWS RDS, Google Cloud SQL
2. **Managed Redis**: AWS ElastiCache, Google Cloud Memorystore
3. **Container Orchestration**: Kubernetes, AWS ECS
4. **Load Balancer**: ALB, nginx
5. **Monitoring**: Prometheus, Datadog, New Relic

### Scaling Strategy
- **API**: Horizontal (multiple instances behind LB)
- **Workers**: Scale per queue based on depth
- **Database**: Read replicas for queries
- **Cache**: Redis Cluster for high availability

## 🔒 Security

- ✅ Token encryption (implement in production)
- ✅ Environment variables for secrets
- ✅ Database SSL connections
- ✅ Input validation (Pydantic)
- ⚠️ Add API authentication (JWT/OAuth)
- ⚠️ Rate limiting on endpoints

## 📚 Documentation

- **System Design**: See uploaded docx
- **API Docs**: http://localhost:8000/docs
- **Architecture**: ARCHITECTURE.md
- **Code**: All files heavily commented

## 🆘 Troubleshooting

```bash
# Reset everything
docker-compose down -v
docker-compose up --build

# Check service health
docker-compose ps
docker-compose logs postgres
docker-compose logs redis

# Database issues
docker-compose exec api python -c "from database_config import DatabaseManager; DatabaseManager.check_connection()"

# Redis issues  
docker-compose exec redis redis-cli ping
```

## 📝 Migration from V1.0

The current wellness.html and services still work but are now stateless. To use the new scalable version:

1. All data is persisted in PostgreSQL
2. Background processing via Celery
3. Use new API endpoints for async operations
4. Frontend can poll /api/tasks/{id} for status

Old endpoints still work for backwards compatibility but are not recommended for production.

## ⚙️ Configuration

Key environment variables:
- `DATABASE_URL`: PostgreSQL connection
- `REDIS_HOST`, `REDIS_PORT`: Redis config
- `CELERY_BROKER_URL`: Celery broker
- `SPOTIFY_CLIENT_ID`, `SPOTIFY_CLIENT_SECRET`: OAuth
- `OPENAI_API_KEY`: LLM API key
- `ENVIRONMENT`: development/production

## 📄 File Structure

```
.
├── main.py                    # FastAPI application
├── database_models.py         # SQLAlchemy models
├── database_config.py         # DB connection management
├── redis_config.py            # Redis cache manager
├── celery_config.py           # Celery setup
├── tasks/
│   ├── spotify_tasks.py       # Spotify API tasks
│   ├── insight_tasks.py       # LLM insight tasks
│   └── scheduled_tasks.py     # Cron tasks
├── wellness_models.py         # Pydantic models (legacy)
├── docker-compose.yml         # Container orchestration
├── Dockerfile                 # Container image
└── requirements.txt           # Python dependencies
```

## 🤝 Contributing

1. Fork repo
2. Create feature branch
3. Add tests
4. Submit PR

## 📄 License

MIT License

---

Built with ❤️ following production best practices for scalable backend engineering.
