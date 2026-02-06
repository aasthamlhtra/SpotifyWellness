"""
Scalable FastAPI application for Spotify Insights
Production-ready with database, caching, and background processing
"""
from fastapi import FastAPI, HTTPException, Depends, Query, BackgroundTasks, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from typing import Optional, List
from datetime import datetime, timedelta
import uuid
import os

# Database and models
from database_config import get_db_session, init_db, DatabaseManager
from database_models import User, SpotifyToken, ListeningSnapshot, GeneratedInsight, TimeRange, InsightType

# Caching
from redis_config import cache, CacheKeys, check_redis_connection

# Background tasks
from tasks.spotify_tasks import ingest_listening_data, refresh_token
from tasks.insight_tasks import generate_wellness_insight, generate_roast

# Pydantic models
from wellness_models import (
    WellnessInsightRequest,
    WellnessInsightResponse,
    ToneMode
)

# Create FastAPI app
app = FastAPI(
    title="Spotify Insights API",
    description="Scalable Spotify listening analytics with AI-powered insights",
    version="2.0.0"
)

# Templates
templates = Jinja2Templates(directory="templates")

# Startup event
@app.on_event("startup")
async def startup_event():
    """Initialize services on startup"""
    print("Starting Spotify Insights API...")
    
    # Check database connection
    if DatabaseManager.check_connection():
        print("✓ Database connected")
        # Initialize database tables
        try:
            init_db()
            print("✓ Database tables initialized")
        except Exception as e:
            print(f"⚠ Database initialization warning: {e}")
    else:
        print("✗ Database connection failed")
    
    # Check Redis connection
    if check_redis_connection():
        print("✓ Redis connected")
    else:
        print("✗ Redis connection failed")
    
    print("API ready!")


@app.on_event("shutdown")
async def shutdown_event():
    """Cleanup on shutdown"""
    print("Shutting down Spotify Insights API...")


# Health check endpoint
@app.get("/health")
async def health_check():
    """Health check endpoint for load balancers"""
    return {
        "status": "healthy",
        "timestamp": datetime.now().isoformat(),
        "version": "2.0.0"
    }


# User management endpoints

@app.post("/api/users/register")
async def register_user(
    spotify_user_id: str,
    display_name: str,
    email: Optional[str] = None,
    db: Session = Depends(get_db_session)
):
    """
    Register a new user or return existing user
    
    Args:
        spotify_user_id: Spotify user ID
        display_name: User's display name
        email: Optional email address
        
    Returns:
        User object
    """
    # Check if user exists
    existing_user = db.query(User).filter(
        User.spotify_user_id == spotify_user_id
    ).first()
    
    if existing_user:
        return {
            "user_id": str(existing_user.id),
            "spotify_user_id": existing_user.spotify_user_id,
            "display_name": existing_user.display_name,
            "created_at": existing_user.created_at.isoformat()
        }
    
    # Create new user
    new_user = User(
        spotify_user_id=spotify_user_id,
        display_name=display_name,
        email=email
    )
    
    db.add(new_user)
    db.commit()
    db.refresh(new_user)
    
    return {
        "user_id": str(new_user.id),
        "spotify_user_id": new_user.spotify_user_id,
        "display_name": new_user.display_name,
        "created_at": new_user.created_at.isoformat()
    }


@app.post("/api/tokens/store")
async def store_spotify_token(
    user_id: str,
    access_token: str,
    refresh_token: str,
    expires_in: int,
    scope: Optional[str] = None,
    db: Session = Depends(get_db_session)
):
    """
    Store or update Spotify OAuth tokens for a user
    
    Args:
        user_id: User UUID
        access_token: Spotify access token
        refresh_token: Spotify refresh token
        expires_in: Token expiration time in seconds
        scope: OAuth scope
        
    Returns:
        Token information
    """
    try:
        user_uuid = uuid.UUID(user_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid user ID format")
    
    # Check if user exists
    user = db.query(User).filter(User.id == user_uuid).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    
    # Calculate expiration time
    expires_at = datetime.now() + timedelta(seconds=expires_in)
    
    # Check if token exists
    existing_token = db.query(SpotifyToken).filter(
        SpotifyToken.user_id == user_uuid
    ).first()
    
    if existing_token:
        # Update existing token
        existing_token.access_token = access_token
        existing_token.refresh_token = refresh_token
        existing_token.expires_at = expires_at
        existing_token.scope = scope
        existing_token.updated_at = datetime.now()
        db.commit()
        token_id = existing_token.id
    else:
        # Create new token
        new_token = SpotifyToken(
            user_id=user_uuid,
            access_token=access_token,
            refresh_token=refresh_token,
            expires_at=expires_at,
            scope=scope
        )
        db.add(new_token)
        db.commit()
        db.refresh(new_token)
        token_id = new_token.id
    
    return {
        "token_id": str(token_id),
        "expires_at": expires_at.isoformat()
    }


# Listening data endpoints

@app.post("/api/listening/ingest")
async def trigger_listening_ingest(
    user_id: str,
    time_range: str = "medium_term",
    db: Session = Depends(get_db_session)
):
    """
    Trigger background ingestion of listening data
    
    Args:
        user_id: User UUID
        time_range: short_term, medium_term, or long_term
        
    Returns:
        Task information
    """
    try:
        user_uuid = uuid.UUID(user_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid user ID format")
    
    # Verify user exists
    user = db.query(User).filter(User.id == user_uuid).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    
    # Verify time range
    valid_ranges = ["short_term", "medium_term", "long_term"]
    if time_range not in valid_ranges:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid time_range. Must be one of: {valid_ranges}"
        )
    
    # Queue background task
    task = ingest_listening_data.delay(user_id, time_range)
    
    return {
        "task_id": task.id,
        "status": "queued",
        "user_id": user_id,
        "time_range": time_range
    }


@app.get("/api/listening/snapshots")
async def get_listening_snapshots(
    user_id: str,
    limit: int = 10,
    db: Session = Depends(get_db_session)
):
    """
    Get listening snapshots for a user
    
    Args:
        user_id: User UUID
        limit: Number of snapshots to return
        
    Returns:
        List of snapshots
    """
    try:
        user_uuid = uuid.UUID(user_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid user ID format")
    
    snapshots = db.query(ListeningSnapshot).filter(
        ListeningSnapshot.user_id == user_uuid
    ).order_by(
        ListeningSnapshot.snapshot_date.desc()
    ).limit(limit).all()
    
    return [
        {
            "snapshot_id": str(s.id),
            "snapshot_date": s.snapshot_date.isoformat(),
            "time_range": s.time_range.value,
            "total_tracks": s.total_tracks_analyzed,
            "mood_diversity": s.mood_diversity_score,
            "mood_patterns": s.mood_patterns
        }
        for s in snapshots
    ]


# Insight generation endpoints

@app.post("/api/insights/generate")
async def generate_insight(
    snapshot_id: str,
    insight_type: str = "wellness",
    tone_mode: str = "neutral",
    db: Session = Depends(get_db_session)
):
    """
    Generate AI insight from a listening snapshot
    
    Args:
        snapshot_id: Snapshot UUID
        insight_type: Type of insight (wellness, roast, productivity)
        tone_mode: Tone for the insight (roast, neutral, supportive)
        
    Returns:
        Task information
    """
    try:
        snapshot_uuid = uuid.UUID(snapshot_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid snapshot ID format")
    
    # Verify snapshot exists
    snapshot = db.query(ListeningSnapshot).filter(
        ListeningSnapshot.id == snapshot_uuid
    ).first()
    
    if not snapshot:
        raise HTTPException(status_code=404, detail="Snapshot not found")
    
    # Queue appropriate task based on insight type
    if insight_type == "roast":
        task = generate_roast.delay(snapshot_id)
    elif insight_type == "wellness":
        task = generate_wellness_insight.delay(snapshot_id, tone_mode)
    else:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid insight_type. Must be 'wellness' or 'roast'"
        )
    
    return {
        "task_id": task.id,
        "status": "queued",
        "snapshot_id": snapshot_id,
        "insight_type": insight_type
    }


@app.get("/api/insights/{insight_id}")
async def get_insight(
    insight_id: str,
    db: Session = Depends(get_db_session)
):
    """
    Get a generated insight by ID
    
    Args:
        insight_id: Insight UUID
        
    Returns:
        Insight object
    """
    try:
        insight_uuid = uuid.UUID(insight_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid insight ID format")
    
    # Try cache first
    cache_key = f"insight:{insight_id}"
    cached = cache.get(cache_key)
    if cached:
        return cached
    
    # Query database
    insight = db.query(GeneratedInsight).filter(
        GeneratedInsight.id == insight_uuid
    ).first()
    
    if not insight:
        raise HTTPException(status_code=404, detail="Insight not found")
    
    result = {
        "insight_id": str(insight.id),
        "user_id": str(insight.user_id),
        "snapshot_id": str(insight.snapshot_id),
        "insight_type": insight.insight_type.value,
        "content": insight.content,
        "structured_output": insight.structured_output,
        "llm_model": insight.llm_model,
        "tone_mode": insight.tone_mode,
        "created_at": insight.created_at.isoformat(),
        "generation_time_ms": insight.generation_time_ms
    }
    
    # Cache for 1 hour
    cache.set(cache_key, result, ttl=3600)
    
    return result


@app.get("/api/insights/user/{user_id}")
async def get_user_insights(
    user_id: str,
    limit: int = 10,
    insight_type: Optional[str] = None,
    db: Session = Depends(get_db_session)
):
    """
    Get insights for a specific user
    
    Args:
        user_id: User UUID
        limit: Number of insights to return
        insight_type: Optional filter by insight type
        
    Returns:
        List of insights
    """
    try:
        user_uuid = uuid.UUID(user_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid user ID format")
    
    query = db.query(GeneratedInsight).filter(
        GeneratedInsight.user_id == user_uuid
    )
    
    if insight_type:
        query = query.filter(GeneratedInsight.insight_type == insight_type)
    
    insights = query.order_by(
        GeneratedInsight.created_at.desc()
    ).limit(limit).all()
    
    return [
        {
            "insight_id": str(i.id),
            "snapshot_id": str(i.snapshot_id),
            "insight_type": i.insight_type.value,
            "tone_mode": i.tone_mode,
            "created_at": i.created_at.isoformat(),
            "preview": i.content[:200] + "..." if len(i.content) > 200 else i.content
        }
        for i in insights
    ]


# Task status endpoint

@app.get("/api/tasks/{task_id}")
async def get_task_status(task_id: str):
    """
    Get status of a background task
    
    Args:
        task_id: Celery task ID
        
    Returns:
        Task status information
    """
    from celery_config import celery_app
    
    task = celery_app.AsyncResult(task_id)
    
    response = {
        "task_id": task_id,
        "status": task.state,
    }
    
    if task.state == "SUCCESS":
        response["result"] = task.result
    elif task.state == "FAILURE":
        response["error"] = str(task.info)
    elif task.state == "PENDING":
        response["message"] = "Task is waiting to be executed"
    elif task.state == "STARTED":
        response["message"] = "Task is currently running"
    
    return response


# Frontend routes

@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    """Home page"""
    return templates.TemplateResponse(
        "home.html",
        {"request": request}
    )


@app.get("/wellness", response_class=HTMLResponse)
async def wellness_page(request: Request):
    """Wellness insights page"""
    return templates.TemplateResponse(
        "wellness.html",
        {
            "request": request,
            "tone_modes": [mode.value for mode in ToneMode],
            "time_ranges": {
                "short_term": "Last 4 Weeks",
                "medium_term": "Last 6 Months",
                "long_term": "Last Year"
            }
        }
    )


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=8000,
        reload=True
    )
