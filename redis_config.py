"""
Redis configuration for caching and message brokering
"""
import redis
import json
import pickle
from typing import Any, Optional
from datetime import timedelta
import os

# Redis configuration
REDIS_HOST = os.getenv("REDIS_HOST", "localhost")
REDIS_PORT = int(os.getenv("REDIS_PORT", 6379))
REDIS_DB = int(os.getenv("REDIS_DB", 0))
REDIS_PASSWORD = os.getenv("REDIS_PASSWORD", None)

# Redis client (for general caching)
redis_client = redis.Redis(
    host=REDIS_HOST,
    port=REDIS_PORT,
    db=REDIS_DB,
    password=REDIS_PASSWORD,
    decode_responses=False,  # Handle binary data
    socket_connect_timeout=5,
    socket_timeout=5
)

# Redis client for Celery (message broker)
CELERY_BROKER_URL = os.getenv(
    "CELERY_BROKER_URL",
    f"redis://{REDIS_HOST}:{REDIS_PORT}/1"
)

CELERY_RESULT_BACKEND = os.getenv(
    "CELERY_RESULT_BACKEND",
    f"redis://{REDIS_HOST}:{REDIS_PORT}/2"
)


class CacheManager:
    """
    Utility class for Redis caching operations
    """
    
    def __init__(self, client: redis.Redis = redis_client):
        self.client = client
    
    def set(self, key: str, value: Any, ttl: Optional[int] = None) -> bool:
        """
        Set a value in cache
        
        Args:
            key: Cache key
            value: Value to cache (will be pickled)
            ttl: Time to live in seconds (None = no expiration)
            
        Returns:
            bool: True if successful
        """
        try:
            serialized = pickle.dumps(value)
            if ttl:
                return self.client.setex(key, ttl, serialized)
            else:
                return self.client.set(key, serialized)
        except Exception as e:
            print(f"Cache set error for key {key}: {e}")
            return False
    
    def get(self, key: str) -> Optional[Any]:
        """
        Get a value from cache
        
        Args:
            key: Cache key
            
        Returns:
            The cached value or None if not found
        """
        try:
            data = self.client.get(key)
            if data is None:
                return None
            return pickle.loads(data)
        except Exception as e:
            print(f"Cache get error for key {key}: {e}")
            return None
    
    def delete(self, key: str) -> bool:
        """
        Delete a key from cache
        
        Args:
            key: Cache key
            
        Returns:
            bool: True if key was deleted
        """
        try:
            return bool(self.client.delete(key))
        except Exception as e:
            print(f"Cache delete error for key {key}: {e}")
            return False
    
    def exists(self, key: str) -> bool:
        """
        Check if a key exists in cache
        
        Args:
            key: Cache key
            
        Returns:
            bool: True if key exists
        """
        try:
            return bool(self.client.exists(key))
        except Exception as e:
            print(f"Cache exists error for key {key}: {e}")
            return False
    
    def set_json(self, key: str, value: dict, ttl: Optional[int] = None) -> bool:
        """
        Set a JSON-serializable value in cache
        
        Args:
            key: Cache key
            value: Dictionary to cache
            ttl: Time to live in seconds
            
        Returns:
            bool: True if successful
        """
        try:
            serialized = json.dumps(value).encode('utf-8')
            if ttl:
                return self.client.setex(key, ttl, serialized)
            else:
                return self.client.set(key, serialized)
        except Exception as e:
            print(f"Cache set_json error for key {key}: {e}")
            return False
    
    def get_json(self, key: str) -> Optional[dict]:
        """
        Get a JSON value from cache
        
        Args:
            key: Cache key
            
        Returns:
            Dictionary or None if not found
        """
        try:
            data = self.client.get(key)
            if data is None:
                return None
            return json.loads(data.decode('utf-8'))
        except Exception as e:
            print(f"Cache get_json error for key {key}: {e}")
            return None
    
    def increment(self, key: str, amount: int = 1) -> int:
        """
        Increment a counter
        
        Args:
            key: Cache key
            amount: Amount to increment by
            
        Returns:
            New value after increment
        """
        try:
            return self.client.incrby(key, amount)
        except Exception as e:
            print(f"Cache increment error for key {key}: {e}")
            return 0
    
    def set_with_ttl(self, key: str, value: Any, minutes: int) -> bool:
        """
        Set a value with TTL in minutes
        
        Args:
            key: Cache key
            value: Value to cache
            minutes: Expiration time in minutes
            
        Returns:
            bool: True if successful
        """
        return self.set(key, value, ttl=minutes * 60)
    
    def flush_all(self):
        """
        Clear all cache (USE WITH CAUTION)
        """
        try:
            self.client.flushdb()
            print("Cache flushed successfully")
        except Exception as e:
            print(f"Cache flush error: {e}")


# Cache key generators
class CacheKeys:
    """
    Standardized cache key generators
    """
    
    @staticmethod
    def user_profile(spotify_user_id: str) -> str:
        """Cache key for user profile"""
        return f"user:profile:{spotify_user_id}"
    
    @staticmethod
    def spotify_tracks(user_id: str, time_range: str) -> str:
        """Cache key for Spotify tracks"""
        return f"spotify:tracks:{user_id}:{time_range}"
    
    @staticmethod
    def audio_features(track_ids_hash: str) -> str:
        """Cache key for audio features"""
        return f"spotify:audio_features:{track_ids_hash}"
    
    @staticmethod
    def listening_snapshot(user_id: str, date: str, time_range: str) -> str:
        """Cache key for listening snapshot"""
        return f"snapshot:{user_id}:{date}:{time_range}"
    
    @staticmethod
    def generated_insight(snapshot_id: str, insight_type: str) -> str:
        """Cache key for generated insights"""
        return f"insight:{snapshot_id}:{insight_type}"
    
    @staticmethod
    def rate_limit(endpoint: str, user_id: str) -> str:
        """Cache key for rate limiting"""
        return f"rate_limit:{endpoint}:{user_id}"


# Singleton cache manager instance
cache = CacheManager(redis_client)


# Cache decorators
def cache_result(ttl_minutes: int = 60):
    """
    Decorator to cache function results
    
    Usage:
        @cache_result(ttl_minutes=30)
        def expensive_function(param1, param2):
            # ... expensive operation
            return result
    """
    def decorator(func):
        def wrapper(*args, **kwargs):
            # Create cache key from function name and arguments
            cache_key = f"func:{func.__name__}:{str(args)}:{str(kwargs)}"
            
            # Try to get from cache
            cached_value = cache.get(cache_key)
            if cached_value is not None:
                return cached_value
            
            # Execute function and cache result
            result = func(*args, **kwargs)
            cache.set(cache_key, result, ttl=ttl_minutes * 60)
            return result
        
        return wrapper
    return decorator


def check_redis_connection() -> bool:
    """
    Check if Redis connection is working
    
    Returns:
        bool: True if connected
    """
    try:
        redis_client.ping()
        print("Redis connection successful")
        return True
    except Exception as e:
        print(f"Redis connection failed: {e}")
        return False


if __name__ == "__main__":
    # Test Redis connection
    check_redis_connection()
    
    # Test cache operations
    print("\nTesting cache operations...")
    cache.set("test_key", "test_value", ttl=60)
    value = cache.get("test_key")
    print(f"Cached value: {value}")
    
    cache.set_json("test_json", {"name": "test", "value": 123}, ttl=60)
    json_value = cache.get_json("test_json")
    print(f"Cached JSON: {json_value}")
