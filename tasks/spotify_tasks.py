"""
Celery tasks for Spotify API integration
Handles token refresh, data ingestion, and rate limiting
"""
from celery import Task
from celery_config import celery_app
from database_config import get_db_session
from database_models import User, SpotifyToken, ListeningSnapshot, TimeRange, BackgroundJob
from redis_config import cache, CacheKeys
from datetime import datetime, timedelta
import spotipy
from spotipy.oauth2 import SpotifyOAuth
import uuid
import time
import os
from typing import Dict, List, Optional
from sqlalchemy.orm import Session


class SpotifyTask(Task):
    """Base task for Spotify API operations with error handling"""
    autoretry_for = (Exception,)
    retry_kwargs = {'max_retries': 3, 'countdown': 60}
    retry_backoff = True


def get_spotify_client(access_token: str) -> spotipy.Spotify:
    """Create authenticated Spotify client"""
    return spotipy.Spotify(auth=access_token)


def refresh_spotify_token(user_id: str, db: Session) -> Optional[str]:
    """
    Refresh Spotify access token for a user
    
    Args:
        user_id: User UUID string
        db: Database session
        
    Returns:
        New access token or None if refresh failed
    """
    try:
        user_uuid = uuid.UUID(user_id)
        token_record = db.query(SpotifyToken).filter(
            SpotifyToken.user_id == user_uuid
        ).first()
        
        if not token_record:
            return None
        
        # Create OAuth handler
        sp_oauth = SpotifyOAuth(
            client_id=os.getenv("SPOTIFY_CLIENT_ID"),
            client_secret=os.getenv("SPOTIFY_CLIENT_SECRET"),
            redirect_uri=os.getenv("SPOTIFY_REDIRECT_URI")
        )
        
        # Refresh token
        token_info = sp_oauth.refresh_access_token(token_record.refresh_token)
        
        # Update database
        token_record.access_token = token_info["access_token"]
        token_record.expires_at = datetime.now() + timedelta(seconds=token_info["expires_in"])
        token_record.updated_at = datetime.now()
        
        if "refresh_token" in token_info:
            token_record.refresh_token = token_info["refresh_token"]
        
        db.commit()
        
        return token_info["access_token"]
    
    except Exception as e:
        print(f"Error refreshing token for user {user_id}: {e}")
        return None


@celery_app.task(bind=True, base=SpotifyTask, name="tasks.spotify_tasks.refresh_token")
def refresh_token(self, user_id: str) -> Dict:
    """
    Background task to refresh a user's Spotify token
    
    Args:
        user_id: User UUID string
        
    Returns:
        Task result with new token info
    """
    db = next(get_db_session())
    
    try:
        new_token = refresh_spotify_token(user_id, db)
        
        if new_token:
            return {
                "success": True,
                "user_id": user_id,
                "message": "Token refreshed successfully"
            }
        else:
            return {
                "success": False,
                "user_id": user_id,
                "message": "Failed to refresh token"
            }
    
    finally:
        db.close()


@celery_app.task(bind=True, base=SpotifyTask, name="tasks.spotify_tasks.refresh_expiring_tokens")
def refresh_expiring_tokens(self) -> Dict:
    """
    Scheduled task to refresh tokens expiring within 1 hour
    
    Returns:
        Summary of refreshed tokens
    """
    db = next(get_db_session())
    
    try:
        # Find tokens expiring in next hour
        expiring_soon = datetime.now() + timedelta(hours=1)
        expiring_tokens = db.query(SpotifyToken).filter(
            SpotifyToken.expires_at <= expiring_soon
        ).all()
        
        refreshed_count = 0
        failed_count = 0
        
        for token_record in expiring_tokens:
            user_id = str(token_record.user_id)
            new_token = refresh_spotify_token(user_id, db)
            
            if new_token:
                refreshed_count += 1
            else:
                failed_count += 1
        
        return {
            "success": True,
            "refreshed": refreshed_count,
            "failed": failed_count,
            "total_checked": len(expiring_tokens)
        }
    
    finally:
        db.close()


def fetch_top_tracks(sp: spotipy.Spotify, time_range: str, limit: int = 50) -> List[Dict]:
    """Fetch user's top tracks from Spotify"""
    return sp.current_user_top_tracks(time_range=time_range, limit=limit)["items"]


def fetch_top_artists(sp: spotipy.Spotify, time_range: str, limit: int = 50) -> List[Dict]:
    """Fetch user's top artists from Spotify"""
    return sp.current_user_top_artists(time_range=time_range, limit=limit)["items"]


def fetch_audio_features(sp: spotipy.Spotify, track_ids: List[str]) -> List[Dict]:
    """Fetch audio features for tracks"""
    # Spotify API allows max 100 tracks per request
    all_features = []
    for i in range(0, len(track_ids), 100):
        batch = track_ids[i:i+100]
        features = sp.audio_features(batch)
        all_features.extend([f for f in features if f is not None])
    return all_features


def calculate_audio_feature_stats(audio_features: List[Dict]) -> Dict:
    """Calculate aggregate statistics for audio features"""
    if not audio_features:
        return {}
    
    import statistics
    
    features_to_analyze = [
        "valence", "energy", "danceability", "acousticness",
        "instrumentalness", "speechiness", "tempo", "loudness"
    ]
    
    stats = {}
    for feature in features_to_analyze:
        values = [f[feature] for f in audio_features if feature in f]
        if values:
            stats[f"avg_{feature}"] = statistics.mean(values)
            stats[f"std_{feature}"] = statistics.stdev(values) if len(values) > 1 else 0.0
            stats[f"min_{feature}"] = min(values)
            stats[f"max_{feature}"] = max(values)
    
    return stats


def extract_genre_distribution(artists: List[Dict]) -> Dict:
    """Extract and normalize genre distribution from artists"""
    genre_counts = {}
    total_genres = 0
    
    for artist in artists:
        for genre in artist.get("genres", []):
            genre_counts[genre] = genre_counts.get(genre, 0) + 1
            total_genres += 1
    
    if total_genres == 0:
        return {}
    
    # Normalize to percentages and sort by frequency
    genre_distribution = {
        genre: round(count / total_genres, 3)
        for genre, count in genre_counts.items()
    }
    
    # Return top 10 genres
    sorted_genres = sorted(genre_distribution.items(), key=lambda x: x[1], reverse=True)
    return dict(sorted_genres[:10])


def calculate_mood_patterns(audio_features: List[Dict]) -> Dict:
    """
    Categorize tracks by mood based on audio features
    
    Mood categories:
    - Happy: High valence (>0.6), high energy (>0.6)
    - Sad/Melancholic: Low valence (<0.4), low energy (<0.5)
    - Energetic: High energy (>0.7), high danceability (>0.6)
    - Calm/Relaxed: Low energy (<0.4), high acousticness (>0.5)
    - Focused: Low speechiness (<0.3), moderate energy (0.3-0.7)
    """
    mood_counts = {
        "happy": 0,
        "sad": 0,
        "energetic": 0,
        "calm": 0,
        "focused": 0,
        "other": 0
    }
    
    for features in audio_features:
        valence = features.get("valence", 0.5)
        energy = features.get("energy", 0.5)
        danceability = features.get("danceability", 0.5)
        acousticness = features.get("acousticness", 0.5)
        speechiness = features.get("speechiness", 0.5)
        
        # Categorize (tracks can belong to multiple categories)
        categorized = False
        
        if valence > 0.6 and energy > 0.6:
            mood_counts["happy"] += 1
            categorized = True
        elif valence < 0.4 and energy < 0.5:
            mood_counts["sad"] += 1
            categorized = True
        
        if energy > 0.7 and danceability > 0.6:
            mood_counts["energetic"] += 1
            categorized = True
        elif energy < 0.4 and acousticness > 0.5:
            mood_counts["calm"] += 1
            categorized = True
        
        if speechiness < 0.3 and 0.3 <= energy <= 0.7:
            mood_counts["focused"] += 1
            categorized = True
        
        if not categorized:
            mood_counts["other"] += 1
    
    total_tracks = len(audio_features)
    if total_tracks == 0:
        return {}
    
    # Convert to percentages
    mood_patterns = {
        mood: {
            "percentage": round(count / total_tracks, 3),
            "track_count": count
        }
        for mood, count in mood_counts.items()
        if count > 0
    }
    
    return mood_patterns


def calculate_diversity_scores(artists: List[Dict], genres: Dict) -> Dict:
    """Calculate artist and genre diversity scores"""
    # Artist diversity: Shannon entropy based on listen counts
    # For simplicity, we'll use artist count vs total as proxy
    artist_diversity = min(len(artists) / 50.0, 1.0)  # Normalize to 0-1
    
    # Genre diversity: Shannon entropy of genre distribution
    import math
    genre_entropy = 0.0
    if genres:
        for percentage in genres.values():
            if percentage > 0:
                genre_entropy -= percentage * math.log2(percentage)
    
    # Normalize genre entropy to 0-1 scale (max entropy for 10 genres ≈ 3.32)
    mood_diversity = min(genre_entropy / 3.32, 1.0) if genre_entropy > 0 else 0.0
    
    return {
        "artist_diversity_score": round(artist_diversity, 3),
        "mood_diversity_score": round(mood_diversity, 3)
    }


@celery_app.task(bind=True, base=SpotifyTask, name="tasks.spotify_tasks.ingest_listening_data")
def ingest_listening_data(self, user_id: str, time_range: str = "medium_term") -> Dict:
    """
    Ingest and aggregate listening data from Spotify for a user
    
    Args:
        user_id: User UUID string
        time_range: short_term, medium_term, or long_term
        
    Returns:
        Task result with snapshot information
    """
    db = next(get_db_session())
    
    try:
        # Create background job record
        job = BackgroundJob(
            job_type="ingest_listening_data",
            celery_task_id=self.request.id,
            user_id=uuid.UUID(user_id),
            status="running",
            params={"time_range": time_range},
            started_at=datetime.now()
        )
        db.add(job)
        db.commit()
        
        # Get user's access token
        user_uuid = uuid.UUID(user_id)
        token_record = db.query(SpotifyToken).filter(
            SpotifyToken.user_id == user_uuid
        ).first()
        
        if not token_record:
            job.status = "failed"
            job.error_message = "No Spotify token found for user"
            job.completed_at = datetime.now()
            db.commit()
            raise ValueError("No Spotify token found for user")
        
        # Check if token is expired
        if token_record.expires_at <= datetime.now():
            new_token = refresh_spotify_token(user_id, db)
            if not new_token:
                job.status = "failed"
                job.error_message = "Failed to refresh expired token"
                job.completed_at = datetime.now()
                db.commit()
                raise ValueError("Failed to refresh expired token")
            access_token = new_token
        else:
            access_token = token_record.access_token
        
        # Create Spotify client
        sp = get_spotify_client(access_token)
        
        # Fetch data from Spotify
        print(f"Fetching top tracks for user {user_id}, time_range={time_range}")
        top_tracks = fetch_top_tracks(sp, time_range, limit=50)
        
        print(f"Fetching top artists for user {user_id}")
        top_artists = fetch_top_artists(sp, time_range, limit=50)
        
        # Get audio features for tracks
        track_ids = [track["id"] for track in top_tracks]
        print(f"Fetching audio features for {len(track_ids)} tracks")
        audio_features = fetch_audio_features(sp, track_ids)
        
        # Aggregate data
        print("Calculating aggregate statistics")
        audio_stats = calculate_audio_feature_stats(audio_features)
        genre_dist = extract_genre_distribution(top_artists)
        mood_patterns = calculate_mood_patterns(audio_features)
        diversity_scores = calculate_diversity_scores(top_artists, genre_dist)
        
        # Create snapshot
        snapshot = ListeningSnapshot(
            user_id=user_uuid,
            snapshot_date=datetime.now(),
            time_range=TimeRange(time_range),
            audio_features=audio_stats,
            genre_distribution=genre_dist,
            mood_patterns=mood_patterns,
            artist_diversity_score=diversity_scores["artist_diversity_score"],
            mood_diversity_score=diversity_scores["mood_diversity_score"],
            total_tracks_analyzed=len(top_tracks)
        )
        
        db.add(snapshot)
        db.commit()
        db.refresh(snapshot)
        
        # Update job status
        job.status = "success"
        job.completed_at = datetime.now()
        job.result = {
            "snapshot_id": str(snapshot.id),
            "tracks_analyzed": len(top_tracks),
            "artists_analyzed": len(top_artists)
        }
        db.commit()
        
        # Invalidate cache
        cache.delete(f"snapshots:user:{user_id}")
        
        print(f"Snapshot created successfully: {snapshot.id}")
        
        return {
            "success": True,
            "snapshot_id": str(snapshot.id),
            "user_id": user_id,
            "time_range": time_range,
            "tracks_analyzed": len(top_tracks),
            "artists_analyzed": len(top_artists)
        }
    
    except Exception as e:
        print(f"Error ingesting data for user {user_id}: {e}")
        
        # Update job status
        if 'job' in locals():
            job.status = "failed"
            job.error_message = str(e)
            job.completed_at = datetime.now()
            db.commit()
        
        raise
    
    finally:
        db.close()


@celery_app.task(bind=True, base=SpotifyTask, name="tasks.spotify_tasks.batch_ingest_users")
def batch_ingest_users(self, user_ids: List[str], time_range: str = "medium_term") -> Dict:
    """
    Batch ingest listening data for multiple users
    
    Args:
        user_ids: List of user UUID strings
        time_range: Time range for ingestion
        
    Returns:
        Summary of batch ingestion
    """
    results = {
        "success": [],
        "failed": [],
        "total": len(user_ids)
    }
    
    for user_id in user_ids:
        try:
            result = ingest_listening_data.delay(user_id, time_range)
            results["success"].append(user_id)
        except Exception as e:
            results["failed"].append({
                "user_id": user_id,
                "error": str(e)
            })
    
    return results
