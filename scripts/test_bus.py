"""Foundation Redis bus test script."""

# We import publish to send a test message to a Redis pub/sub channel.
from core.bus import publish

# We import set_value to write test data into Redis key-value storage.
from core.bus import set_value


# This function runs a simple publish + set test for the Redis bus layer.
def run_bus_test():
    # We define the test channel name used for pub/sub testing.
    test_channel = "system:test"

    # We define a test message dictionary that will be serialized to JSON.
    test_message = {"event": "BUS_TEST", "message": "Hello from test_bus.py"}

    # We publish the test message to Redis channel and capture subscriber count.
    subscriber_count = publish(test_channel, test_message)

    # We print a clear success message for beginner-friendly visibility.
    print(f"[SUCCESS] Published test message to channel '{test_channel}'.")

    # We print subscriber count so user understands who received the message.
    print(f"[INFO] Subscriber count: {subscriber_count}")

    # We define the required key for system status in Redis key-value store.
    status_key = "system:status"

    # We define the required value exactly as requested.
    status_value = {"status": "RUNNING"}

    # We store the required status value in Redis using JSON serialization.
    set_result = set_value(status_key, status_value)

    # We print success result for key-value write operation.
    print(f"[SUCCESS] Set '{status_key}' = {status_value}")

    # We print raw Redis set response so user can confirm operation state.
    print(f"[INFO] Redis set result: {set_result}")


# This block allows running the script directly from terminal.
if __name__ == "__main__":
    # We execute the bus test flow.
    run_bus_test()
