"""
Celery scheduled tasks for periodic maintenance and data processing
Runs via Celery Beat scheduler
"""
from celery import Task
from celery_config import celery_app
from database_config import get_db_session
from database_models import User, SpotifyToken, BackgroundJob, ListeningSnapshot, GeneratedInsight
from datetime import datetime, timedelta
from typing import Dict, List
from sqlalchemy import and_


class ScheduledTask(Task):
    """Base task for scheduled operations"""
    autoretry_for = (Exception,)
    retry_kwargs = {'max_retries': 1, 'countdown': 300}


@celery_app.task(bind=True, base=ScheduledTask, name="tasks.scheduled_tasks.ingest_all_users_data")
def ingest_all_users_data(self) -> Dict:
    """
    Daily task to ingest listening data for all active users
    Runs at 2 AM UTC daily
    
    Returns:
        Summary of ingestion tasks queued
    """
    db = next(get_db_session())
    
    try:
        # Get all users with valid tokens
        valid_tokens = db.query(SpotifyToken).filter(
            SpotifyToken.expires_at > datetime.now()
        ).all()
        
        user_ids = [str(token.user_id) for token in valid_tokens]
        
        # Queue ingestion tasks for each user
        from tasks.spotify_tasks import ingest_listening_data
        
        queued_count = 0
        failed_count = 0
        
        for user_id in user_ids:
            try:
                # Queue task with medium_term (6 months) as default
                ingest_listening_data.delay(user_id, time_range="medium_term")
                queued_count += 1
            except Exception as e:
                print(f"Failed to queue ingestion for user {user_id}: {e}")
                failed_count += 1
        
        result = {
            "success": True,
            "total_users": len(user_ids),
            "queued": queued_count,
            "failed": failed_count,
            "timestamp": datetime.now().isoformat()
        }
        
        print(f"Daily ingestion scheduled: {result}")
        return result
    
    finally:
        db.close()


@celery_app.task(bind=True, base=ScheduledTask, name="tasks.scheduled_tasks.generate_weekly_summaries")
def generate_weekly_summaries(self) -> Dict:
    """
    Weekly task to generate summary insights for all active users
    Runs Sunday at 9 AM UTC
    
    Returns:
        Summary of insight generation tasks queued
    """
    db = next(get_db_session())
    
    try:
        # Get all users who had activity in the past week
        one_week_ago = datetime.now() - timedelta(days=7)
        
        recent_snapshots = db.query(ListeningSnapshot).filter(
            ListeningSnapshot.snapshot_date >= one_week_ago
        ).distinct(ListeningSnapshot.user_id).all()
        
        # Queue insight generation for latest snapshot of each user
        from tasks.insight_tasks import generate_wellness_insight
        
        queued_count = 0
        failed_count = 0
        
        for snapshot in recent_snapshots:
            try:
                # Get user's most recent snapshot
                latest_snapshot = db.query(ListeningSnapshot).filter(
                    ListeningSnapshot.user_id == snapshot.user_id
                ).order_by(
                    ListeningSnapshot.snapshot_date.desc()
                ).first()
                
                if latest_snapshot:
                    # Generate supportive weekly wellness insight
                    generate_wellness_insight.delay(
                        str(latest_snapshot.id),
                        tone_mode="supportive"
                    )
                    queued_count += 1
            
            except Exception as e:
                print(f"Failed to queue weekly summary for user {snapshot.user_id}: {e}")
                failed_count += 1
        
        result = {
            "success": True,
            "total_users": len(recent_snapshots),
            "queued": queued_count,
            "failed": failed_count,
            "timestamp": datetime.now().isoformat()
        }
        
        print(f"Weekly summaries scheduled: {result}")
        return result
    
    finally:
        db.close()


@celery_app.task(bind=True, base=ScheduledTask, name="tasks.scheduled_tasks.cleanup_old_jobs")
def cleanup_old_jobs(self, retention_days: int = 30) -> Dict:
    """
    Clean up old background job records
    Runs Monday at 3 AM UTC
    
    Args:
        retention_days: Number of days to retain job records
        
    Returns:
        Summary of cleanup operation
    """
    db = next(get_db_session())
    
    try:
        cutoff_date = datetime.now() - timedelta(days=retention_days)
        
        # Delete old completed jobs
        deleted_count = db.query(BackgroundJob).filter(
            and_(
                BackgroundJob.created_at < cutoff_date,
                BackgroundJob.status.in_(["success", "failed"])
            )
        ).delete(synchronize_session=False)
        
        db.commit()
        
        result = {
            "success": True,
            "deleted_jobs": deleted_count,
            "cutoff_date": cutoff_date.isoformat(),
            "retention_days": retention_days,
            "timestamp": datetime.now().isoformat()
        }
        
        print(f"Job cleanup completed: {result}")
        return result
    
    finally:
        db.close()


@celery_app.task(bind=True, base=ScheduledTask, name="tasks.scheduled_tasks.cleanup_old_snapshots")
def cleanup_old_snapshots(self, retention_days: int = 365) -> Dict:
    """
    Clean up old listening snapshots
    Optional task - only run if data retention policy requires it
    
    Args:
        retention_days: Number of days to retain snapshots (default 1 year)
        
    Returns:
        Summary of cleanup operation
    """
    db = next(get_db_session())
    
    try:
        cutoff_date = datetime.now() - timedelta(days=retention_days)
        
        # Delete old snapshots (this will cascade to associated insights)
        deleted_count = db.query(ListeningSnapshot).filter(
            ListeningSnapshot.snapshot_date < cutoff_date
        ).delete(synchronize_session=False)
        
        db.commit()
        
        result = {
            "success": True,
            "deleted_snapshots": deleted_count,
            "cutoff_date": cutoff_date.isoformat(),
            "retention_days": retention_days,
            "timestamp": datetime.now().isoformat()
        }
        
        print(f"Snapshot cleanup completed: {result}")
        return result
    
    finally:
        db.close()


@celery_app.task(bind=True, base=ScheduledTask, name="tasks.scheduled_tasks.generate_monthly_trends")
def generate_monthly_trends(self) -> Dict:
    """
    Generate monthly trend analysis for all users
    Runs on the 1st of each month
    
    Returns:
        Summary of trend generation
    """
    db = next(get_db_session())
    
    try:
        # Get all users with snapshots in the past month
        one_month_ago = datetime.now() - timedelta(days=30)
        
        users_with_activity = db.query(User).join(ListeningSnapshot).filter(
            ListeningSnapshot.snapshot_date >= one_month_ago
        ).distinct().all()
        
        trends_generated = 0
        
        for user in users_with_activity:
            # Get snapshots for the past month
            monthly_snapshots = db.query(ListeningSnapshot).filter(
                and_(
                    ListeningSnapshot.user_id == user.id,
                    ListeningSnapshot.snapshot_date >= one_month_ago
                )
            ).order_by(ListeningSnapshot.snapshot_date).all()
            
            if len(monthly_snapshots) >= 2:
                # Calculate trends (simplified - in production, this would be more sophisticated)
                first_snapshot = monthly_snapshots[0]
                last_snapshot = monthly_snapshots[-1]
                
                # Compare mood diversity
                mood_change = (
                    last_snapshot.mood_diversity_score - 
                    first_snapshot.mood_diversity_score
                )
                
                trends_data = {
                    "user_id": str(user.id),
                    "period": "monthly",
                    "snapshots_analyzed": len(monthly_snapshots),
                    "mood_diversity_trend": "increasing" if mood_change > 0 else "decreasing",
                    "mood_change": round(mood_change, 3)
                }
                
                # Store trends (could be a separate table or insight)
                print(f"Monthly trends for user {user.id}: {trends_data}")
                trends_generated += 1
        
        result = {
            "success": True,
            "users_analyzed": len(users_with_activity),
            "trends_generated": trends_generated,
            "timestamp": datetime.now().isoformat()
        }
        
        print(f"Monthly trends generated: {result}")
        return result
    
    finally:
        db.close()


@celery_app.task(bind=True, base=ScheduledTask, name="tasks.scheduled_tasks.health_check_services")
def health_check_services(self) -> Dict:
    """
    Periodic health check of external services
    Runs every hour
    
    Returns:
        Health status of services
    """
    import requests
    from redis_config import check_redis_connection
    from database_config import DatabaseManager
    
    health_status = {
        "timestamp": datetime.now().isoformat(),
        "services": {}
    }
    
    # Check database
    try:
        db_healthy = DatabaseManager.check_connection()
        health_status["services"]["database"] = {
            "status": "healthy" if db_healthy else "unhealthy",
            "type": "postgresql"
        }
    except Exception as e:
        health_status["services"]["database"] = {
            "status": "error",
            "error": str(e)
        }
    
    # Check Redis
    try:
        redis_healthy = check_redis_connection()
        health_status["services"]["redis"] = {
            "status": "healthy" if redis_healthy else "unhealthy",
            "type": "cache"
        }
    except Exception as e:
        health_status["services"]["redis"] = {
            "status": "error",
            "error": str(e)
        }
    
    # Check Spotify API (simple ping)
    try:
        response = requests.get("https://api.spotify.com/v1", timeout=5)
        health_status["services"]["spotify_api"] = {
            "status": "healthy" if response.status_code in [200, 401] else "degraded",
            "status_code": response.status_code
        }
    except Exception as e:
        health_status["services"]["spotify_api"] = {
            "status": "error",
            "error": str(e)
        }
    
    # Overall health
    all_healthy = all(
        service.get("status") == "healthy" 
        for service in health_status["services"].values()
    )
    
    health_status["overall"] = "healthy" if all_healthy else "degraded"
    
    print(f"Health check completed: {health_status['overall']}")
    return health_status


@celery_app.task(bind=True, base=ScheduledTask, name="tasks.scheduled_tasks.update_user_statistics")
def update_user_statistics(self) -> Dict:
    """
    Update aggregated user statistics
    Runs daily at midnight
    
    Returns:
        Summary of statistics update
    """
    db = next(get_db_session())
    
    try:
        # Count active users (with recent activity)
        thirty_days_ago = datetime.now() - timedelta(days=30)
        
        total_users = db.query(User).count()
        
        active_users = db.query(User).join(ListeningSnapshot).filter(
            ListeningSnapshot.snapshot_date >= thirty_days_ago
        ).distinct().count()
        
        total_snapshots = db.query(ListeningSnapshot).count()
        total_insights = db.query(GeneratedInsight).count()
        
        # Recent activity
        recent_snapshots = db.query(ListeningSnapshot).filter(
            ListeningSnapshot.snapshot_date >= thirty_days_ago
        ).count()
        
        recent_insights = db.query(GeneratedInsight).filter(
            GeneratedInsight.created_at >= thirty_days_ago
        ).count()
        
        statistics = {
            "timestamp": datetime.now().isoformat(),
            "total_users": total_users,
            "active_users_30d": active_users,
            "total_snapshots": total_snapshots,
            "total_insights": total_insights,
            "recent_snapshots_30d": recent_snapshots,
            "recent_insights_30d": recent_insights,
            "avg_snapshots_per_active_user": (
                round(recent_snapshots / active_users, 2) if active_users > 0 else 0
            )
        }
        
        print(f"User statistics updated: {statistics}")
        return statistics
    
    finally:
        db.close()
