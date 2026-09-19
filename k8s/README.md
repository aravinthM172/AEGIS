# FaultScope on Kubernetes (Phase 12)

The monitored workload, its infrastructure, the telemetry consumer and the control plane run in a local
[kind](https://kind.sigs.k8s.io/) cluster. Everything below was executed and measured on this repository.

## Run it

```powershell
# one-time: kind v0.24.0 (checksum-verified) into tools/bin, git-ignored
./tools/bin/kind.exe create cluster --name faultscope

# images come from the compose build
docker tag aegis-aegis:latest          faultscope/app:dev
docker tag aegis-java-service:latest   faultscope/java-service:dev
docker tag aegis-cpp-service:latest    faultscope/cpp-service:dev
foreach ($i in "app","java-service","cpp-service") { ./tools/bin/kind.exe load docker-image faultscope/${i}:dev --name faultscope }

kubectl --context kind-faultscope apply -k k8s
kubectl --context kind-faultscope -n faultscope get pods
kubectl --context kind-faultscope -n faultscope port-forward svc/control-plane 18000:8000   # API
./tools/bin/kind.exe delete cluster --name faultscope                                        # tear down
```

## What is in the manifests

| Concern | How |
|---|---|
| Deployments / Services | one Deployment + ClusterIP Service per component (`infra.yaml`, `workload.yaml`) |
| Service discovery | Kubernetes DNS (`gateway` → `java-service` → `cpp-service`, `postgres`, `kafka`, `redis`) |
| Health probes | readiness + liveness on every service; a `startupProbe` on `java-service` so Spring Boot is not killed while starting |
| Start order | none guaranteed: pods crash-loop until Postgres/Kafka are up and then converge (see below) |
| Kafka | single-node KRaft, `strategy: Recreate`, `enableServiceLinks: false` (a Service called `kafka` would otherwise inject `KAFKA_PORT`, which the image reads as configuration) |

## Measured on the cluster

Steady load of 8 requests/s from a pod inside the cluster (so kube-proxy load-balances), `POST /api/jobs`.

| Scenario | Result |
|---|---|
| Workload smoke test | 20/20 requests OK; telemetry reaches the in-cluster control plane |
| Delete the `java-service` pod, **1 replica** | **14.3 % of requests failed** (86/600) over 12 s; the pod was replaced and became ready again |
| Delete the `java-service` pod, **2 replicas** | **0 failed requests** (0/600), no slow seconds |
| Deploy a non-existent image to `cpp-service`, then `kubectl rollout undo` | new pod `ImagePullBackOff`, the 2 healthy pods kept serving; undo restored the old image; **0 of 560** user requests failed |

The digital twin *simulated* "2 replicas" as roughly 50 % impact; the measurement above is 0 %. The twin's
replica assumption is a pessimistic bound for an abrupt failure and now says so. Graceful pod deletion removes the
pod from the Service endpoints first.

## Bug found by running here

The control plane seeded its service registry once at startup. Started before Postgres was ready (which
Docker Compose hides with `depends_on`), it logged "will retry on first use" and never did, leaving an empty
registry. Initialisation now retries in the background until the database is reachable.

## Limits (be honest about these)

- **Fault injection is Docker-only.** The fault-agent drives Docker; there is no Kubernetes injector, so the
  cluster's control plane refuses real experiments (no `FAULTSCOPE_ENV` / `FAULT_AGENT_URL`). Dry runs and all
  read/analysis features work. The chaos above was done with `kubectl` and a load pod, not with the experiment engine.
- **`ROLLBACK_DEPLOYMENT`, `SCALE_SERVICE` and `RESTART_SERVICE` are not wired to Kubernetes.** The policy engine
  still reports `ROLLBACK_DEPLOYMENT` as unsupported. A `kubectl`-based executor (`rollout undo`, `scale`,
  `rollout restart`) is the natural next step; the cluster behaviour it would use is demonstrated above.
- **Data is not durable** (`emptyDir`), a single node, no Ingress, no HPA, no network policies, no resource
  requests/limits. It is a demo cluster.
