"""
Database configuration and session management
"""
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, scoped_session
from sqlalchemy.pool import QueuePool
from contextlib import contextmanager
import os
from typing import Generator

from database_models import Base

# Database configuration
DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql://spotify_user:spotify_pass@localhost:5432/spotify_insights"
)

# Create engine with connection pooling
engine = create_engine(
    DATABASE_URL,
    poolclass=QueuePool,
    pool_size=10,  # Number of connections to maintain
    max_overflow=20,  # Max connections beyond pool_size
    pool_pre_ping=True,  # Verify connections before using
    pool_recycle=3600,  # Recycle connections after 1 hour
    echo=os.getenv("SQL_ECHO", "false").lower() == "true"  # Log SQL queries if enabled
)

# Session factory
SessionLocal = scoped_session(
    sessionmaker(
        autocommit=False,
        autoflush=False,
        bind=engine
    )
)


def init_db():
    """
    Initialize database - create all tables
    Call this once on application startup
    """
    Base.metadata.create_all(bind=engine)
    print("Database tables created successfully")


def drop_db():
    """
    Drop all tables - USE WITH CAUTION
    Only for development/testing
    """
    Base.metadata.drop_all(bind=engine)
    print("Database tables dropped successfully")


@contextmanager
def get_db() -> Generator:
    """
    Dependency for FastAPI endpoints
    Provides a database session and ensures cleanup
    
    Usage:
        with get_db() as db:
            user = db.query(User).first()
    """
    db = SessionLocal()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def get_db_session():
    """
    Get a new database session
    Must be manually closed after use
    
    Usage in FastAPI:
        from fastapi import Depends
        
        @app.get("/users")
        def get_users(db: Session = Depends(get_db_session)):
            return db.query(User).all()
    """
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


class DatabaseManager:
    """
    Utility class for database operations
    """
    
    @staticmethod
    def create_tables():
        """Create all tables"""
        init_db()
    
    @staticmethod
    def drop_tables():
        """Drop all tables"""
        drop_db()
    
    @staticmethod
    def reset_database():
        """Reset database - drop and recreate all tables"""
        print("Resetting database...")
        drop_db()
        init_db()
        print("Database reset complete")
    
    @staticmethod
    def check_connection():
        """Check if database connection is working"""
        try:
            with engine.connect() as connection:
                connection.execute("SELECT 1")
            print("Database connection successful")
            return True
        except Exception as e:
            print(f"Database connection failed: {e}")
            return False


# For testing and scripts
if __name__ == "__main__":
    import sys
    
    if len(sys.argv) > 1:
        command = sys.argv[1]
        
        if command == "init":
            init_db()
        elif command == "drop":
            drop_db()
        elif command == "reset":
            DatabaseManager.reset_database()
        elif command == "check":
            DatabaseManager.check_connection()
        else:
            print(f"Unknown command: {command}")
            print("Available commands: init, drop, reset, check")
    else:
        print("Usage: python database_config.py [init|drop|reset|check]")
