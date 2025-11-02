"""
Test that verifies redis package is available for Celery broker.

This test ensures that the redis dependency is properly installed,
which is required for Celery to connect to Redis as a message broker.
"""

from __future__ import annotations


def test_redis_package_installed():
    """Verify that redis package is installed and can be imported."""
    try:
        import redis
        assert hasattr(redis, "Redis"), "redis.Redis class should be available"
    except ImportError as e:
        raise AssertionError(
            "redis package not installed. Add 'redis>=5.0,<6' to dependencies."
        ) from e


def test_celery_can_use_redis_transport():
    """Verify that Celery can use redis as a transport/broker."""
    try:
        from kombu.transport.redis import PrefixedStrictRedis
        assert PrefixedStrictRedis is not None
    except ImportError as e:
        raise AssertionError(
            "Cannot import PrefixedStrictRedis from kombu.transport.redis. "
            "This indicates redis package is missing."
        ) from e
    except AttributeError as e:
        # This is the error that was happening before the fix
        if "'NoneType' object has no attribute 'Redis'" in str(e):
            raise AssertionError(
                "redis module is None. This means redis package is not installed."
            ) from e
        raise
