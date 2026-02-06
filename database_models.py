"""
Database models for scalable Spotify insights application
Uses SQLAlchemy ORM with PostgreSQL
"""
from sqlalchemy import (
    Column, String, DateTime, Float, Boolean, Text, 
    ForeignKey, Enum as SQLEnum, UniqueConstraint, Index
)
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
import uuid
from datetime import datetime
from enum import Enum

Base = declarative_base()


class TimeRange(str, Enum):
    """Time range for Spotify data"""
    SHORT_TERM = "short_term"  # Last 4 weeks
    MEDIUM_TERM = "medium_term"  # Last 6 months
    LONG_TERM = "long_term"  # Last year


class InsightType(str, Enum):
    """Types of generated insights"""
    ROAST = "roast"
    WELLNESS = "wellness"
    PRODUCTIVITY = "productivity"


class User(Base):
    """Application-level user identity, decoupled from Spotify"""
    __tablename__ = "users"
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    spotify_user_id = Column(String(255), unique=True, nullable=False, index=True)
    display_name = Column(String(255))
    email = Column(String(255), nullable=True)
    country = Column(String(10), nullable=True)
    
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), 
                       onupdate=func.now(), nullable=False)
    
    # Relationships
    tokens = relationship("SpotifyToken", back_populates="user", cascade="all, delete-orphan")
    snapshots = relationship("ListeningSnapshot", back_populates="user", cascade="all, delete-orphan")
    insights = relationship("GeneratedInsight", back_populates="user", cascade="all, delete-orphan")
    
    def __repr__(self):
        return f"<User(id={self.id}, spotify_user_id={self.spotify_user_id})>"


class SpotifyToken(Base):
    """OAuth tokens for Spotify API access"""
    __tablename__ = "spotify_tokens"
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    
    # Encrypted token storage (encryption handled at application layer)
    access_token = Column(Text, nullable=False)
    refresh_token = Column(Text, nullable=False)
    
    expires_at = Column(DateTime(timezone=True), nullable=False)
    scope = Column(String(500), nullable=True)
    token_type = Column(String(50), default="Bearer")
    
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), 
                       onupdate=func.now(), nullable=False)
    
    # Relationships
    user = relationship("User", back_populates="tokens")
    
    # Indexes
    __table_args__ = (
        Index('idx_spotify_tokens_user_id', 'user_id'),
        Index('idx_spotify_tokens_expires_at', 'expires_at'),
    )
    
    def __repr__(self):
        return f"<SpotifyToken(id={self.id}, user_id={self.user_id})>"


class ListeningSnapshot(Base):
    """Aggregated listening behavior for a specific time period"""
    __tablename__ = "listening_snapshots"
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    
    snapshot_date = Column(DateTime(timezone=True), nullable=False)
    time_range = Column(SQLEnum(TimeRange), nullable=False)
    
    # Aggregated audio features (JSONB for flexibility)
    audio_features = Column(JSONB, nullable=False, default={})
    # Example: {
    #   "avg_valence": 0.65,
    #   "avg_energy": 0.72,
    #   "avg_danceability": 0.58,
    #   "avg_acousticness": 0.23,
    #   "avg_tempo": 125.4,
    #   "std_valence": 0.15,
    #   "std_energy": 0.18
    # }
    
    # Genre distribution
    genre_distribution = Column(JSONB, nullable=False, default={})
    # Example: {"pop": 0.35, "rock": 0.25, "indie": 0.20, "electronic": 0.20}
    
    # Mood patterns
    mood_patterns = Column(JSONB, nullable=False, default={})
    # Example: {
    #   "melancholic": {"percentage": 0.25, "track_count": 12},
    #   "happy": {"percentage": 0.35, "track_count": 18},
    #   "focused": {"percentage": 0.40, "track_count": 20}
    # }
    
    # Metrics
    artist_diversity_score = Column(Float, nullable=True)
    mood_diversity_score = Column(Float, nullable=True)
    total_tracks_analyzed = Column(Float, nullable=False, default=0)
    
    # Listening hours by time of day (JSONB)
    listening_hours = Column(JSONB, nullable=True, default={})
    # Example: {"morning": 2.5, "afternoon": 4.2, "evening": 3.8, "night": 1.5}
    
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    
    # Relationships
    user = relationship("User", back_populates="snapshots")
    insights = relationship("GeneratedInsight", back_populates="snapshot", cascade="all, delete-orphan")
    
    # Constraints and indexes
    __table_args__ = (
        UniqueConstraint('user_id', 'snapshot_date', 'time_range', 
                        name='uq_user_snapshot_time_range'),
        Index('idx_listening_snapshots_user_id', 'user_id'),
        Index('idx_listening_snapshots_date', 'snapshot_date'),
    )
    
    def __repr__(self):
        return f"<ListeningSnapshot(id={self.id}, user_id={self.user_id}, date={self.snapshot_date})>"


class GeneratedInsight(Base):
    """LLM-generated insights with versioning"""
    __tablename__ = "generated_insights"
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    snapshot_id = Column(UUID(as_uuid=True), ForeignKey("listening_snapshots.id", ondelete="CASCADE"), 
                        nullable=False)
    
    insight_type = Column(SQLEnum(InsightType), nullable=False)
    
    # LLM metadata
    llm_model = Column(String(100), nullable=False)  # e.g., "gpt-4", "gpt-3.5-turbo"
    prompt_version = Column(String(50), nullable=False)  # e.g., "v1.2"
    tone_mode = Column(String(50), nullable=True)  # e.g., "roast", "neutral", "supportive"
    
    # Generated content
    content = Column(Text, nullable=False)  # Main narrative insight
    
    # Structured output (JSONB for flexibility)
    structured_output = Column(JSONB, nullable=True, default={})
    # Example: {
    #   "wellness_nudges": [...],
    #   "key_patterns": [...],
    #   "recommendations": [...]
    # }
    
    # Metadata
    generation_time_ms = Column(Float, nullable=True)
    tokens_used = Column(Float, nullable=True)
    
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    
    # Relationships
    user = relationship("User", back_populates="insights")
    snapshot = relationship("ListeningSnapshot", back_populates="insights")
    
    # Indexes
    __table_args__ = (
        Index('idx_generated_insights_user_id', 'user_id'),
        Index('idx_generated_insights_snapshot_id', 'snapshot_id'),
        Index('idx_generated_insights_type', 'insight_type'),
        Index('idx_generated_insights_created_at', 'created_at'),
    )
    
    def __repr__(self):
        return f"<GeneratedInsight(id={self.id}, type={self.insight_type}, user_id={self.user_id})>"


class BackgroundJob(Base):
    """Track background job execution for monitoring"""
    __tablename__ = "background_jobs"
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    job_type = Column(String(100), nullable=False)  # e.g., "ingest_listening_data", "generate_insights"
    celery_task_id = Column(String(255), unique=True, nullable=True)
    
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    
    status = Column(String(50), nullable=False, default="pending")  # pending, running, success, failed
    
    # Job metadata
    params = Column(JSONB, nullable=True, default={})
    result = Column(JSONB, nullable=True, default={})
    error_message = Column(Text, nullable=True)
    
    started_at = Column(DateTime(timezone=True), nullable=True)
    completed_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    
    # Indexes
    __table_args__ = (
        Index('idx_background_jobs_user_id', 'user_id'),
        Index('idx_background_jobs_status', 'status'),
        Index('idx_background_jobs_job_type', 'job_type'),
        Index('idx_background_jobs_created_at', 'created_at'),
    )
    
    def __repr__(self):
        return f"<BackgroundJob(id={self.id}, type={self.job_type}, status={self.status})>"
