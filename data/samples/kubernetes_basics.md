# Kubernetes essentials

## Pods and Deployments

A Pod is the smallest deployable unit and holds one or more containers. A Deployment manages a ReplicaSet that keeps the desired number of pod replicas running and performs rolling updates when the image changes.

## Services

A Service gives pods a stable virtual IP and DNS name. ClusterIP is reachable only inside the cluster, NodePort opens a port on every node, and LoadBalancer provisions a cloud load balancer.

## Configuration

ConfigMaps hold non secret configuration and Secrets hold sensitive values, both injected as environment variables or mounted files. Secrets are only base64 encoded by default, so enable encryption at rest.

## Health probes

The liveness probe restarts a container that is stuck, the readiness probe removes a pod from Service endpoints until it can serve traffic, and the startup probe protects slow starting applications.

## kubectl basics

kubectl apply -f deployment.yaml creates or updates resources, kubectl get pods lists them, kubectl logs shows container output and kubectl rollout undo reverts a bad deployment.
