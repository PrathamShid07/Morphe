# PostgreSQL tuning notes

## Choosing an index

PostgreSQL provides btree, hash, gin, gist and brin indexes. Btree is the default and serves equality and range lookups. Choose gin for jsonb and full text search, and use EXPLAIN ANALYZE to confirm whether the planner picks an index scan or a sequential scan.

## Autovacuum behaviour

Dead tuples accumulate after updates and deletes. Autovacuum cleans them up automatically once roughly 20 percent of a table has changed, which corresponds to autovacuum_vacuum_scale_factor 0.2, and the database accepts connections on port 5432 by default.

## Sizing memory

A reasonable starting value for shared_buffers is about 25 percent of the machine RAM. Remember that work_mem is granted for each sort or hash step, so a big value times many concurrent connections can run the server out of memory.

## Connection pooling

max_connections defaults to 100 and each connection is its own backend process. Run pgbouncer in transaction pooling mode in front of PostgreSQL so a large number of clients share a small pool of server connections.

## Standby servers and backups

Streaming replication sends write ahead log records to standby servers. Use pg_dump for logical backups, and combine pg_basebackup with WAL archiving for point in time recovery.
