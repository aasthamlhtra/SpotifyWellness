# Migration Guide: V1.0 to V2.0 Scalable Architecture

This document explains the architectural changes and migration path from the original stateless application to the scalable, production-ready version.

## 📋 Overview of Changes

### Architecture Evolution

| Aspect | V1.0 (Original) | V2.0 (Scalable) |
|--------|-----------------|-----------------|
| **Data Storage** | In-memory (ephemeral) | PostgreSQL (persistent) |
| **Processing** | Synchronous (HTTP blocking) | Asynchronous (Celery workers) |
| **Caching** | None | Redis (responses + message broker) |
| **Scalability** | Single instance | Horizontal scaling ready |
| **Token Management** | Per-request | Auto-refresh with lifecycle |
| **Data Aggregation** | Real-time calculation | Pre-computed snapshots |
| **LLM Calls** | Direct (blocking) | Background jobs (non-blocking) |

## 🔄 Data Model Changes

### V1.0 Data Flow
```
User Request → Spotify API → Audio Features → Analytics → LLM → Response
(All in one HTTP request - can timeout)
```

### V2.0 Data Flow
```
User Request → Queue Background Task → Return Task ID
Background Worker → Spotify API → Process → Save to DB
User Polls → Get Result from DB/Cache
```

## 🗄️ New Database Schema

### 1. Users Table
**Purpose**: Decouple user identity from Spotify

```python
# V1.0: No user persistence
# V2.0: Persistent user records
user = User(
    spotify_user_id="spotify_123",
    display_name="John Doe",
    email="john@example.com"
)
```

### 2. Spotify Tokens Table  
**Purpose**: Manage OAuth lifecycle with auto-refresh

```python
# V1.0: Token passed in every request
# V2.0: Stored securely, auto-refreshed
token = SpotifyToken(
    user_id=user.id,
    access_token="BQD...",  # Should be encrypted
    refresh_token="AQC...",
    expires_at=datetime.now() + timedelta(hours=1)
)
```

### 3. Listening Snapshots Table
**Purpose**: Store aggregated listening data over time

```python
# V1.0: Fetched fresh every time
# V2.0: Pre-aggregated daily snapshots
snapshot = ListeningSnapshot(
    user_id=user.id,
    snapshot_date=datetime.now(),
    time_range="medium_term",
    audio_features={
        "avg_valence": 0.65,
        "avg_energy": 0.72,
        "std_valence": 0.15
    },
    mood_patterns={
        "happy": {"percentage": 0.35, "track_count": 18},
        "calm": {"percentage": 0.25, "track_count": 13}
    },
    total_tracks_analyzed=50
)
```

**Benefits**:
- Enables longitudinal analysis
- Reduces Spotify API calls
- Faster response times
- Historical trend tracking

### 4. Generated Insights Table
**Purpose**: Version and cache LLM outputs

```python
# V1.0: Generated on-demand, not saved
# V2.0: Versioned, reusable insights
insight = GeneratedInsight(
    user_id=user.id,
    snapshot_id=snapshot.id,
    insight_type="wellness",
    llm_model="gpt-4",
    prompt_version="v1.0",
    tone_mode="supportive",
    content="Your wellness insight...",
    structured_output={
        "wellness_tips": [...],
        "mood_trend": "..."
    }
)
```

**Benefits**:
- Avoid regenerating identical insights
- Track prompt/model versions
- A/B testing different prompts
- Cost optimization

## 🔄 Background Processing

### Celery Task Queues

#### Queue 1: `spotify` (Spotify API Operations)
```python
# Tasks:
- refresh_token(user_id)
- ingest_listening_data(user_id, time_range)
- refresh_expiring_tokens()  # Scheduled every 30 min

# Example:
task = ingest_listening_data.delay(user_id, "medium_term")
# Returns immediately with task.id
```

#### Queue 2: `insights` (LLM Operations)
```python
# Tasks:
- generate_wellness_insight(snapshot_id, tone_mode)
- generate_roast(snapshot_id)

# Example:
task = generate_wellness_insight.delay(snapshot_id, "neutral")
# LLM processing happens in background
```

#### Queue 3: `scheduled` (Cron Jobs)
```python
# Tasks:
- ingest_all_users_data()  # Daily 2 AM
- generate_weekly_summaries()  # Sunday 9 AM
- cleanup_old_jobs()  # Monday 3 AM
```

### Task Status Tracking

```python
# Check task status
status = celery_app.AsyncResult(task_id)

if status.state == "SUCCESS":
    result = status.result
elif status.state == "FAILURE":
    error = status.info
elif status.state == "PENDING":
    # Still queued
elif status.state == "STARTED":
    # Currently running
```

## 📊 API Endpoint Changes

### V1.0 Endpoints (Synchronous)
```python
POST /api/wellness/insights
# Returns: Complete insight (can take 30+ seconds)
# Problem: HTTP timeout, no scalability
```

### V2.0 Endpoints (Asynchronous)

#### Step 1: Queue the Job
```python
POST /api/listening/ingest
# Returns: {"task_id": "abc-123", "status": "queued"}
# Immediate response
```

#### Step 2: Check Status
```python
GET /api/tasks/{task_id}
# Returns: {"status": "SUCCESS", "result": {...}}
```

#### Step 3: Get Result
```python
GET /api/listening/snapshots
# Returns: Pre-computed snapshot data
```

## 🔄 Migration Steps

### For Developers

#### 1. Update Environment Variables
```bash
# Add to .env
DATABASE_URL=postgresql://user:pass@localhost:5432/spotify_insights
REDIS_HOST=localhost
CELERY_BROKER_URL=redis://localhost:6379/1
```

#### 2. Start New Services
```bash
# Start PostgreSQL and Redis
docker-compose up postgres redis -d

# Initialize database
python -c "from database_config import init_db; init_db()"
```

#### 3. Update Code to Use Async Pattern
```python
# OLD (V1.0)
@app.post("/api/wellness/insights")
def get_insights(access_token: str):
    # Fetch data
    # Process
    # Generate LLM insight
    # Return (30+ seconds)
    return insight

# NEW (V2.0)
@app.post("/api/listening/ingest")
def trigger_ingest(user_id: str):
    task = ingest_listening_data.delay(user_id)
    return {"task_id": task.id}

@app.get("/api/tasks/{task_id}")
def get_task_status(task_id: str):
    task = celery_app.AsyncResult(task_id)
    return {"status": task.state, "result": task.result}
```

### For Users

#### V1.0 Usage (Direct)
```javascript
// Single blocking request
fetch('/api/wellness/insights?access_token=...', {
    method: 'POST',
    body: JSON.stringify({tone_mode: 'neutral'})
})
.then(r => r.json())
.then(insight => displayInsight(insight))
```

#### V2.0 Usage (Poll for Status)
```javascript
// 1. Queue the job
fetch('/api/listening/ingest?user_id=123&time_range=medium_term', {
    method: 'POST'
})
.then(r => r.json())
.then(data => {
    // 2. Poll for completion
    const taskId = data.task_id;
    
    const checkStatus = setInterval(() => {
        fetch(`/api/tasks/${taskId}`)
        .then(r => r.json())
        .then(status => {
            if (status.status === 'SUCCESS') {
                clearInterval(checkStatus);
                // 3. Fetch the result
                fetch(`/api/listening/snapshots?user_id=123`)
                .then(r => r.json())
                .then(snapshots => displayData(snapshots));
            }
        });
    }, 2000);  // Poll every 2 seconds
});
```

#### V2.0 Usage (WebSocket - Future Enhancement)
```javascript
// Even better: Use WebSockets for real-time updates
const ws = new WebSocket('ws://localhost:8000/ws');

ws.send(JSON.stringify({
    action: 'ingest',
    user_id: '123'
}));

ws.onmessage = (event) => {
    const data = JSON.parse(event.data);
    if (data.status === 'completed') {
        displayData(data.result);
    }
};
```

## 🎯 Benefits of V2.0

### Performance
- ✅ **No HTTP timeouts**: Long operations run in background
- ✅ **Faster responses**: API returns immediately
- ✅ **Better caching**: Redis reduces redundant API calls
- ✅ **Parallel processing**: Multiple workers handle concurrent tasks

### Scalability
- ✅ **Horizontal scaling**: Add more API instances
- ✅ **Worker scaling**: Scale workers independently per queue
- ✅ **Database pooling**: Handle high concurrent connections
- ✅ **Load balancing**: Stateless API enables load distribution

### Reliability
- ✅ **Retry logic**: Failed tasks automatically retry
- ✅ **Task monitoring**: Track all background jobs
- ✅ **Auto-recovery**: Workers restart on failure
- ✅ **Data persistence**: No data loss on server restart

### Cost Optimization
- ✅ **Reduced API calls**: Cached and pre-computed data
- ✅ **LLM efficiency**: Reuse insights when data unchanged
- ✅ **Batch processing**: Group operations efficiently

### Developer Experience
- ✅ **Better testing**: Mock background tasks easily
- ✅ **Monitoring**: Flower dashboard for task visibility
- ✅ **Debugging**: Structured logging and job history
- ✅ **Versioning**: Track prompt and model versions

## 🔧 Backward Compatibility

The V1.0 endpoints can still work with minor modifications:

```python
# Wrapper for backward compatibility
@app.post("/api/wellness/insights")
async def get_insights_sync(access_token: str):
    # 1. Ingest data synchronously (with timeout)
    task = ingest_listening_data.delay(user_id, "medium_term")
    result = task.get(timeout=30)  # Wait max 30 seconds
    
    # 2. Generate insight synchronously
    snapshot_id = result["snapshot_id"]
    insight_task = generate_wellness_insight.delay(snapshot_id, "neutral")
    insight = insight_task.get(timeout=60)
    
    return insight
```

⚠️ **Note**: This defeats the purpose of async processing. Use only for gradual migration.

## 📈 Monitoring Changes

### V1.0
- Basic FastAPI logs
- No task visibility
- No performance metrics

### V2.0
- **Flower Dashboard**: http://localhost:5555
  - Task execution times
  - Worker status
  - Queue depths
  - Success/failure rates
  
- **Database Metrics**:
  - Connection pool stats
  - Query performance
  - Table sizes
  
- **Redis Metrics**:
  - Cache hit rates
  - Memory usage
  - Key expiration

## 🚀 Deployment Changes

### V1.0 Deployment
```bash
# Single container
docker run -p 8000:8000 spotify-app
```

### V2.0 Deployment
```bash
# Multi-container orchestration
docker-compose up

# Services:
# - api (FastAPI)
# - postgres (Database)
# - redis (Cache + Broker)
# - celery_worker_spotify
# - celery_worker_insights
# - celery_worker_scheduled
# - celery_beat (Scheduler)
# - flower (Monitoring)
```

## 📝 Testing Changes

### V1.0 Tests
```python
def test_get_insights():
    response = client.post("/api/wellness/insights")
    assert response.status_code == 200
```

### V2.0 Tests
```python
def test_ingest_data():
    # Test async task queuing
    response = client.post("/api/listening/ingest")
    assert "task_id" in response.json()

@pytest.mark.celery
def test_ingest_task():
    # Test actual task execution
    result = ingest_listening_data.delay(user_id, "medium_term")
    assert result.get(timeout=10)["snapshot_id"]
```

## 🔐 Security Improvements

### Token Security
```python
# V1.0: Token in every request URL
POST /api/wellness/insights?access_token=BQD...

# V2.0: Token stored securely in DB
# Access via user_id, token auto-refreshed
```

### Rate Limiting
```python
# V2.0: Redis-backed rate limiting
from redis_config import cache

def rate_limit(user_id: str, limit: int = 100):
    key = f"rate_limit:api:{user_id}"
    count = cache.increment(key)
    if count == 1:
        cache.expire(key, 3600)  # Reset after 1 hour
    return count <= limit
```

## 📚 Additional Resources

- **Database Schema**: See `database_models.py`
- **Task Definitions**: See `tasks/` directory
- **API Documentation**: http://localhost:8000/docs
- **Flower Monitoring**: http://localhost:5555

## ❓ FAQ

**Q: Can I migrate gradually?**
A: Yes, run both versions side-by-side and gradually move users to V2.0 endpoints.

**Q: What if a background task fails?**
A: Tasks retry automatically (configurable). Failed tasks are logged in `background_jobs` table.

**Q: How do I scale workers?**
A: Increase replicas in docker-compose or Kubernetes deployment.

**Q: Can I use a different database?**
A: Yes, SQLAlchemy supports MySQL, SQLite, etc. Update `DATABASE_URL`.

**Q: How to monitor in production?**
A: Use APM tools (DataDog, New Relic) or Prometheus + Grafana.

## 🎓 Next Steps

1. ✅ Read this migration guide
2. ✅ Review `README.md` for setup
3. ✅ Explore `database_models.py` for schema
4. ✅ Check `tasks/` for background job logic
5. ✅ Test locally with Docker Compose
6. ✅ Deploy to staging environment
7. ✅ Monitor with Flower
8. ✅ Optimize based on metrics

---

**Questions?** Open an issue on GitHub or check the documentation.
