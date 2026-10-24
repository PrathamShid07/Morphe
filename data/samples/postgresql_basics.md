# PostgreSQL fundamentals

## Index types

PostgreSQL offers btree, hash, gin, gist and brin indexes. Btree is the default and handles equality and range queries. Use gin for jsonb and full text search, and run EXPLAIN ANALYZE to see whether the planner chooses an index scan or a sequential scan.

## Vacuum and autovacuum

Updates and deletes leave dead tuples behind. VACUUM reclaims that space and autovacuum runs it automatically once about 20 percent of a table changed, which is autovacuum_vacuum_scale_factor 0.2. The server listens on port 5432 by default.

## Memory settings

Set shared_buffers to roughly 25 percent of system RAM as a starting point. work_mem is allocated per sort or hash operation, so a large value multiplied by many connections can exhaust memory. effective_cache_size tells the planner how much the operating system caches.

## Connections and pooling

max_connections defaults to 100 and every connection is a separate backend process. Put pgbouncer in front of the database in transaction pooling mode so thousands of clients can share a small number of server connections.

## Replication and backups

Streaming replication ships write ahead log records to standby servers. pg_dump creates logical backups, while pg_basebackup together with WAL archiving enables point in time recovery.
