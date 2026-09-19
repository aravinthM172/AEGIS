# AREA topology | 9. Topology: who depends on whom | The map of services, kept honest by evidence
To predict how a failure spreads you first need a **map**: which service calls which. FaultScope keeps two maps and compares them:

- **Declared** — written by a human in `config/topology.json`. Every dependency *must* cite evidence (a file or config line in this repo). No guessing edges.
- **Observed** — computed from real telemetry: if `gateway` really emitted `dependency_call` events targeting `java-service`, that edge exists.

`X → Y` always means **X depends on Y**. If Y fails, everything that (transitively) depends on it is *structurally at risk*. "At risk" is a prediction from structure; *measured* impact only comes from experiments.

## FILE app/topology_graph.py | Pure graph algorithms: validation, cycles, reach, reconciliation | code
### What it is
Pure Python (no database) so it's easy to test. It holds the graph logic used all over the project.

### How it works
- `validate` rejects edges with unknown endpoints, self-dependencies, duplicates, and **cycles** (with the actual path in the message, e.g. `a -> b -> a`).
- `find_cycle` uses depth-first search with three colours: WHITE (unvisited), GREY (on the current path), BLACK (finished). Meeting a GREY node again means a cycle.
- `_reach` is **breadth-first search** returning each reachable node with its hop distance. `dependencies(node)` follows edges forward (everything `node` needs); `dependents(node)` follows them in reverse (everything that would break if `node` fails — the *structural blast radius*).
- `impact_ranking` sorts services by how many others transitively depend on them.
- `load_file` reads and checks `topology.json`: required fields, unique names, **mandatory evidence on every edge**, and that the graph is a valid DAG (directed acyclic graph).
- `reconcile` compares declared and observed edges:

| Category | Meaning |
|---|---|
| `confirmed` | declared **and** seen in telemetry |
| `unobserved` | declared and *observable* (a `calls` edge) but no calls seen — idle or broken |
| `not_instrumented` | declared but this kind of edge emits no telemetry (databases, Kafka), so absence proves nothing |
| `undeclared` | seen calling but missing from the declared map — **drift**: the config is out of date |

### Key parts
@snippet 19-39 | validate: unknown ends, self-edges, duplicates, cycles
@snippet 42-71 | find_cycle: three-colour depth-first search
@snippet 74-86 | BFS with hop counts
@snippet 99-107 | dependencies vs dependents
@snippet 132-155 | load_file: evidence is mandatory
@snippet 163-212 | reconcile: declared vs observed

??Why must every edge carry `evidence`? || An unsupported edge silently corrupts every prediction built on it. Requiring a pointer to real code or config means each edge can be checked, and the test suite verifies the shipped file really does provide evidence for all of them.

## FILE app/topology_registry.py | Copies the topology file into the database at start-up | code
### What it is
`seed_registry(db)` makes the `services` and `service_dependencies` tables match `config/topology.json`. It runs on every control-plane start ([[main.py]]).

### How it works
1. `load_file` validates first — a broken file raises `TopologyError` and **nothing is changed**.
2. Upserts each service (update if it exists, else insert); `flush()` so the services exist before the edges that reference them.
3. Computes wanted vs current **declared** edges; deletes the ones no longer in the file; inserts/updates the rest with their evidence.
4. Rows whose `origin` isn't `declared` are never touched. It's idempotent: running it twice changes nothing.

Because the file is baked into the Docker image, adding a service to it needs a rebuild of the control plane (that's what was done to add `job-tracker`).

### Key parts
@snippet 18-32 | Validate then upsert services
@snippet 34-54 | Sync the declared edges

## FILE app/topology_observed.py | Derives real edges from telemetry | code
### What it is
Computes **observed** edges on demand (never stored) from `dependency_call` events, so it's always exactly what the telemetry says and can be asked about any time window.

### How it works
Two SQL queries group events by (caller, target): call count, error count, first/last seen, average and 95th-percentile latency (`percentile_cont`), plus which error types occurred. A failed call still proves the dependency exists, so failures are included.

### Key parts
@snippet 12-29 | The statistics query
@snippet 47-69 | Turn rows into edge dictionaries

## FILE app/topology_routes.py | The topology HTTP endpoints | code
### What it is
Read-only URLs under `/api` for the dashboard's Services and Topology pages.

### How it works
| URL | Returns |
|---|---|
| `GET /services`, `/services/{name}` | The registry, plus latest telemetry facts from Redis; a service's direct dependencies and dependents |
| `GET /services/{name}/dependencies` | Everything it transitively depends on (with hops) |
| `GET /services/{name}/dependents?basis=…` | Who is **at risk** if it fails. `basis` = `declared`, `observed` or `combined` |
| `GET /topology` | All nodes and edges |
| `GET /topology/impact` | Services ranked by structural blast radius |
| `GET /topology/observed` | Edges seen in telemetry with call/error/latency stats |
| `GET /topology/reconciliation` | Declared vs observed drift report |

Time windows default to the last 60 minutes. `_pairs` builds the edge list for the chosen basis.

### Key parts
@snippet 58-65 | Choose declared, observed or combined edges
@snippet 116-134 | Dependents: structural risk, not measured impact
@snippet 177-189 | The drift report

## FILE config/topology.json | The declared map: 9 services and 12 evidenced edges | config
### What it is
The human-written map. The **services** list gives each node a name, kind (`service`, `database`, `cache`, `broker`), technology and the Docker container name the fault-agent will use. The **dependencies** list holds the edges.

### How it works
- Two `calls` edges form the workload chain: `gateway → java-service` and `java-service → cpp-service`.
- The others are `reads_writes` (services ↔ Postgres/Redis), and `produces`/`consumes` (Kafka).
- Each edge has an `evidence` string, e.g. "gateway/main.py create_job POSTs java-service /jobs".
- `job-tracker` (your app's test copy) appears as a **standalone** node with no declared dependencies — its external dependencies weren't verified, so none were invented.

### Key parts
@snippet 123-128 | gateway → java-service, with evidence
@snippet 135-140 | java-service → cpp-service, with evidence
@snippet 61-66 | The job-tracker node
