# Docker essentials

## Images and layers

A Docker image is built from a Dockerfile with docker build. Every instruction such as FROM, RUN and COPY creates a cached layer, so put rarely changing steps first and copy dependency manifests before the source code to keep rebuilds fast.

## Running containers

docker run -d -p 8080:80 nginx starts a container in the background and maps host port 8080 to container port 80. docker logs shows output, docker exec -it opens a shell and docker stop sends SIGTERM.

## Volumes

Data written inside a container disappears with it. Named volumes created with docker volume create persist data outside the container, and bind mounts map a host directory into the container for development.

## Docker Compose

A compose.yaml file describes multi container applications: services, networks, volumes and environment variables. docker compose up starts everything together and depends_on controls startup order.

## Networking

Containers on a user defined bridge network resolve each other by service name. Publishing a port with -p exposes it on the host, while containers on the same network talk directly without publishing.
