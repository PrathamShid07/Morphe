# Redis caching and data structures

## Key expiry and eviction

Redis keys expire with EXPIRE or SET with EX, and TTL reports the remaining seconds. When maxmemory is reached the eviction policy decides what to remove: allkeys-lru evicts the least recently used key, volatile-ttl prefers keys with the shortest time to live, and noeviction rejects writes.

## Persistence

RDB snapshots write the dataset to disk at intervals, which is compact but can lose the most recent writes. The append only file AOF logs every write and with appendfsync everysec loses at most about one second of data.

## Cache aside pattern

Applications read Redis first and load from the database on a miss, then store the result with a TTL. Invalidate or overwrite the key when the underlying row changes, and add jitter to TTL values to avoid a stampede of simultaneous expirations.

## Data structures

Strings, hashes, lists, sets and sorted sets cover most uses. Sorted sets power leaderboards through ZADD and ZRANGE, and hashes store objects field by field with HSET.

## Publish and subscribe

PUBLISH sends a message to every client subscribed to a channel, but messages are not stored. Redis Streams add persistence and consumer groups for reliable processing. The server listens on port 6379 by default.
