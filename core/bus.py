"""Foundation Redis bus: pub/sub + key-value helpers."""

# We import json so dictionaries/lists can be converted to strings for Redis.
import json

# We import redis for Redis pub/sub and key-value operations.
import redis

# We import load_config so Redis host/port come from centralized config.
from core.config import load_config
# We import get_logger so every bus action is logged consistently.
from core.logger import get_logger

# We create one shared logger for this module.
log = get_logger()

# We keep a cached Redis client so repeated calls are fast and simple.
_redis_client = None


# This helper creates and validates Redis connection safely.
def _get_redis_client():
    # We mark this variable global so we can reuse one client instance.
    global _redis_client

    # We return existing client if it was already created successfully.
    if _redis_client is not None:
        return _redis_client

    # We load configuration values from core.config.
    config = load_config()
    # We read host from config and default to localhost for safety.
    host = config.get("REDIS_HOST", "localhost")
    # We read port from config and default to 6379 as required.
    port = int(config.get("REDIS_PORT", 6379))

    try:
        # We create the Redis client with decode_responses for plain strings.
        client = redis.Redis(host=host, port=port, decode_responses=True)
        # We ping Redis once to fail fast if server is not reachable.
        client.ping()
        # We cache the connected client for later calls.
        _redis_client = client
        # We log successful Redis connection details.
        log.info(f"Redis connected successfully at {host}:{port}")
        # We return the connected client.
        return _redis_client
    except redis.RedisError as error:
        # We log connection error with context for debugging.
        log.error(f"Redis connection failed at {host}:{port} | error={error}")
        # We raise a runtime error so calling code can handle the failure clearly.
        raise RuntimeError("Redis connection failed. Ensure Redis is running.") from error


# This function publishes JSON data to a Redis pub/sub channel.
def publish(channel, data):
    # We get a safe connected Redis client.
    client = _get_redis_client()
    # We serialize input data to JSON string before publishing.
    payload = json.dumps(data)
    # We publish JSON payload to provided channel.
    receivers = client.publish(channel, payload)
    # We log channel name and number of subscribers that received the message.
    log.info(f"publish | channel={channel} | subscribers={receivers} | payload={payload}")
    # We return number of subscribers for basic validation.
    return receivers


# This function stores a value in Redis key-value storage using JSON serialization.
def set_value(key, value, *, silent: bool = False):
    # We get a safe connected Redis client.
    client = _get_redis_client()
    # We convert Python value into JSON string before saving.
    payload = json.dumps(value)
    # We write the JSON payload at the given Redis key.
    result = client.set(key, payload)
    # Heartbeats and dashboards should pass silent=True to avoid log storms.
    if not silent:
        log.info(f"set_value | key={key} | success={result} | payload={payload}")
    # We return Redis set result (True on success).
    return result


# This function fetches a key and deserializes JSON back into Python data.
def get_value(key, *, silent: bool = False):
    # We get a safe connected Redis client.
    client = _get_redis_client()
    # We read raw value from Redis for the given key.
    raw_value = client.get(key)

    # We return None when key is missing to keep behavior explicit.
    if raw_value is None:
        if not silent:
            log.info(f"get_value | key={key} | value=None")
        return None

    try:
        # We parse JSON string back into Python object.
        parsed_value = json.loads(raw_value)
        if not silent:
            log.info(f"get_value | key={key} | json_loaded=True")
        # We return parsed Python object.
        return parsed_value
    except json.JSONDecodeError:
        # We log fallback behavior when value is not valid JSON.
        log.warning(f"get_value | key={key} | json_loaded=False | returning_raw_string=True")
        # We return raw string to avoid data loss.
        return raw_value
