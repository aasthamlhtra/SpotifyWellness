"""
Celery configuration for background task processing
"""
from celery import Celery
from celery.schedules import crontab
import os

from redis_config import CELERY_BROKER_URL, CELERY_RESULT_BACKEND

# Create Celery app
celery_app = Celery(
    "spotify_insights",
    broker=CELERY_BROKER_URL,
    backend=CELERY_RESULT_BACKEND,
    include=[
        "tasks.spotify_tasks",
        "tasks.insight_tasks",
        "tasks.scheduled_tasks"
    ]
)

# Celery configuration
celery_app.conf.update(
    # Task execution
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    
    # Task routing
    task_routes={
        "tasks.spotify_tasks.*": {"queue": "spotify"},
        "tasks.insight_tasks.*": {"queue": "insights"},
        "tasks.scheduled_tasks.*": {"queue": "scheduled"},
    },
    
    # Task time limits
    task_soft_time_limit=300,  # 5 minutes soft limit
    task_time_limit=600,  # 10 minutes hard limit
    
    # Result backend
    result_expires=3600,  # Results expire after 1 hour
    result_persistent=True,
    
    # Task retry configuration
    task_acks_late=True,  # Acknowledge task after completion
    task_reject_on_worker_lost=True,
    
    # Worker configuration
    worker_prefetch_multiplier=4,  # Number of tasks to prefetch
    worker_max_tasks_per_child=1000,  # Restart worker after 1000 tasks
    
    # Beat schedule (periodic tasks)
    beat_schedule={
        # Refresh expiring tokens every 30 minutes
        "refresh-expiring-tokens": {
            "task": "tasks.spotify_tasks.refresh_expiring_tokens",
            "schedule": crontab(minute="*/30"),
            "options": {"queue": "spotify"}
        },
        
        # Generate weekly summaries every Sunday at 9 AM
        "weekly-summary": {
            "task": "tasks.scheduled_tasks.generate_weekly_summaries",
            "schedule": crontab(hour=9, minute=0, day_of_week=0),
            "options": {"queue": "scheduled"}
        },
        
        # Ingest listening data daily at 2 AM
        "daily-ingest": {
            "task": "tasks.scheduled_tasks.ingest_all_users_data",
            "schedule": crontab(hour=2, minute=0),
            "options": {"queue": "spotify"}
        },
        
        # Clean up old job records weekly
        "cleanup-old-jobs": {
            "task": "tasks.scheduled_tasks.cleanup_old_jobs",
            "schedule": crontab(hour=3, minute=0, day_of_week=1),
            "options": {"queue": "scheduled"}
        },
    }
)

# Task base classes
celery_app.Task.track_started = True  # Track when tasks start


if __name__ == "__main__":
    # Start Celery worker
    celery_app.start()
