# NGINX configuration

## Server blocks

NGINX selects a server block by listen port and server_name. A minimal block has listen 80, a server_name, a root directory and location blocks that decide how each URI is handled. Test changes with nginx -t before reloading.

## Reverse proxy

proxy_pass forwards requests to an upstream application. Set proxy_set_header Host $host and X-Forwarded-For so the application sees the original host and client address, and use an upstream block to list several backend servers.

## TLS

Enable HTTPS with listen 443 ssl plus ssl_certificate and ssl_certificate_key. Allow only TLSv1.2 and TLSv1.3 in ssl_protocols and redirect port 80 to HTTPS with return 301.

## Load balancing

An upstream block balances round robin by default. least_conn sends traffic to the server with the fewest active connections, ip_hash keeps a client on the same server, and max_fails with fail_timeout removes unhealthy servers.

## Caching and compression

proxy_cache_path defines a cache zone and proxy_cache enables it for a location. gzip on compresses text responses, and expires or Cache-Control headers let browsers cache static assets.
