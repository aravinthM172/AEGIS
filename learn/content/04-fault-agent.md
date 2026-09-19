# AREA agent | 6. The fault-agent | The one small program allowed to break things — and its safety net
Breaking containers requires control of Docker, which is powerful: whoever has it can do almost anything to your PC's containers. So FaultScope isolates that power in **one small program**, the fault-agent, with strict rules. The big control plane (and the AI) never gets Docker access; it can only *ask* the agent, over HTTP, with a password.

**The safety rules** (each one is covered by automated tests):
1. **Allowlist** — only containers labelled `faultscope.injectable=true` can be touched.
2. **Time limit (dead-man's switch)** — every fault has a TTL. If the control plane crashes, the agent undoes the fault itself.
3. **Write intent first** — the fault is saved to `state.json` *before* being applied. After a crash the agent reads it on startup and reverts everything.
4. **Verify** — after applying, the agent checks the effect really happened; after undoing, it checks that the system is back to normal.
5. **One fault at a time** per container and per experiment.

> The agent is bound to `127.0.0.1:8095` only, and every action needs the `X-Agent-Token` header.

@diagram architecture

## FILE fault-agent/agent/core.py | FaultManager: apply, verify, roll back, expire — for 7 fault types | code
### What it is
The heart of the agent. `FaultManager` keeps a dictionary of active faults, a map from fault type to an **(apply, undo)** pair of functions, timers for the TTL, and a state file. Everything is written so it can be tested with a *fake* Docker client (that's what the tests do).

### How it works — applying a fault
1. `apply()` rejects unknown fault types, then `_get_allowed(container)` fetches the container and **refuses it unless it has the allow label** (`NotAllowed`).
2. Under a lock it checks there's no other fault on that experiment/container (`Conflict`), then records the intent (with `applied_at` and `expires_at`) and **saves it to disk first**.
3. It runs the fault's apply function. If that raises, it tries to undo any partial effect, forgets the fault, and re-raises — never leaves a half-applied fault.
4. On success it arms the TTL timer and returns `applied: True, verified: True`.

### How it works — undoing
- `rollback()` runs the fault's undo function; **if undo raises, the fault stays "active"** so the timer keeps retrying (up to 5 times, 10 s apart). It cancels the timer, writes a history entry (`reverted_by`: `rollback`, `ttl` or `startup`) and saves.
- Rolling back something not active is harmless (`not_active`) — **idempotent**.
- `revert_all_on_startup()` rolls back every fault found in the state file — the crash-recovery path.

### Key parts
@snippet 67-75 | The seven faults, each an (apply, undo) pair
@snippet 114-150 | apply: allowlist, conflicts, intent-first, undo on failure
@snippet 152-175 | rollback: undo, cancel timer, record history
@snippet 209-218 | The TTL timer: revert by itself, retry if it fails
@snippet 246-250 | The allowlist check

??Why save the fault to disk *before* applying it? || If the agent (or the PC) crashes right in the middle of applying, there's still a record saying "this container may be broken". On restart the agent reads it and repairs the container. Saving after would leave an untracked broken container.

### The seven faults
| Fault | What it does | How it checks it worked |
|---|---|---|
| `stop_container` | `docker stop` | container status is not `running` |
| `pause_container` | `docker pause` (freezes the process, connections just hang) | status is `paused` |
| `restart_container` | `docker restart` | waits until running (and healthy) |
| `latency` | Adds network delay with Linux `tc netem` | `tc qdisc show` must contain `netem` |
| `cpu_stress` | Caps the container's CPU quota **and** starts busy loops | measures real CPU use over 2 s |
| `memory_stress` | Holds N MB inside the container | measures the memory working set grew ≥ 60% |
| `http_error` | Calls the service's own fault hook | hook must report `active` |

@snippet 264-268 | latency: run `tc` in a helper container sharing the target's network
@snippet 310-323 | Apply latency, then verify netem is really active
@snippet 381-402 | cpu_stress: cap the quota, start workers, measure the effect
@snippet 404-412 | cpu_stress undo: `-1` removes the quota; verify it is gone
@snippet 416-439 | memory_stress: pick a technique, measure growth
@snippet 459-471 | http_error: call the hook, require `active`

### Lessons the code encodes (bugs found by real runs)
- **`docker update --cpu-quota 0` means "leave unchanged"**, not "remove the limit". The first rollback *reported success while the quota stayed*. Now it uses `-1` and **checks the quota afterwards**.
- The memory-hold trick works differently on Alpine vs Debian (`mawk` can't build a 1 MB string), so a **capability probe** picks the technique up front. A `||` fallback could race with cleanup and leave orphan processes.
- The cleanup script writes its marker as two adjacent quoted pieces so its own command line can't match itself and kill itself (there's a test for that).

### `run_action` — remediation primitives
`restart` and `lift_cpu_cap` are also here, because they need Docker. The **policy** (who may do what, when) lives in the control plane ([[app/remediation/policy.py]]); the agent only does the primitive and **refuses while an experiment fault is active on that container**.

@snippet 84-112 | run_action: restart or lift the CPU cap, with checks

??Which part decides whether an action is *allowed* — the agent or the control plane? || Both, for different things. The control plane's policy engine decides whether an action should happen (allowlist of action types, protected targets, cooldown, approval). The agent still enforces its own container allowlist and refuses if an experiment fault is active. Two independent gates.

### Connects to
- [[app/experiments/injectors.py]] — the control plane's client for this agent
- [[app/fault_hook.py]] — the `http_error` hook it calls
- [[fault-agent/tests/test_core.py]], [[fault-agent/tests/test_stress_faults.py]], [[fault-agent/tests/test_actions.py]] — proofs of the safety rules

## FILE fault-agent/agent/main.py | The agent's HTTP API and start-up | code
### What it is
The FastAPI wrapper (port 8095) around `FaultManager`.

### How it works
- **Refuses to start without a token**: `lifespan` raises if `FAULT_AGENT_TOKEN` isn't set — an unauthenticated agent would be a disaster.
- On start-up it creates the Docker client and calls `revert_all_on_startup()`.
- `require_token` compares the `X-Agent-Token` header using `hmac.compare_digest` — a **constant-time** comparison so timing can't be used to guess the token.
- Routes: `POST /faults` (apply), `DELETE /faults/{id}` (rollback), `GET /faults` (active), `GET /faults/history`, `POST /actions` (remediation), `GET /health`.
- Errors map to clear statuses: not allowed → **403**, unknown container → **404**, unsupported fault → **422**, conflict → **409**, other → **500**.
- `ttl_s` must be 5–900 seconds.

### Key parts
@snippet 29-41 | Start-up: token required, revert leftovers
@snippet 47-49 | Constant-time token check
@snippet 65-78 | POST /faults and its error mapping

## FILE fault-agent/Dockerfile | Image for the agent (includes `tc`) | infra
### What it is
Python 3.12 plus `iproute2`, which provides `tc` — the Linux traffic-control tool used to add network delay. The latency fault starts a **helper container from this same image** in the target's network namespace (`network_mode=container:<id>`) so `tc` changes the target's network, not the agent's.

### Key parts
@snippet 1-17 | The whole file

## FILE fault-agent/requirements.txt | Libraries for the agent | config
### What it is
`fastapi` and `uvicorn` (the API), `docker` (the Docker SDK — talks to the Docker socket) and `httpx` (calls service fault hooks).

### Key parts
@snippet 1-4 | The whole file

## FILE fault-agent/tests/test_core.py | Tests that prove the safety rules | test
### What it is
Uses `FakeContainer`, `FakeClient` and `FakeTimer` so no real Docker is needed. Each test is one promise the agent makes:

| Test | Promise |
|---|---|
| `test_stop_container_is_verified_and_rolled_back` | apply is verified, rollback restores it |
| `test_unlabelled_container_is_refused_and_untouched` | the allowlist really blocks |
| `test_one_active_fault_per_container_and_per_experiment` | no stacking of faults |
| `test_ttl_expiry_reverts_the_fault_without_the_control_plane` | dead-man's switch works |
| `test_rollback_is_idempotent` | undoing twice is safe |
| `test_failed_apply_undoes_partial_effect_and_forgets_the_fault` | no half-broken leftovers |
| `test_agent_restart_reverts_faults_persisted_by_the_previous_process` | crash recovery |
| `test_failed_rollback_keeps_fault_active_and_ttl_retries` | a failed undo isn't forgotten |
| `test_latency_*` | netem is applied, verified, and undo is idempotent |

### Key parts
@snippet 148-157 | The TTL test: time passes, the fault is reverted with no help
@snippet 187-199 | The crash-recovery test

## FILE fault-agent/tests/test_stress_faults.py | Tests for CPU, memory and HTTP-error faults | test
### What it is
A `StressContainer` fake simulates CPU and memory numbers. It checks:
- cpu_stress caps the quota, runs workers and **measures** the effect; if nothing is burned it **fails loudly and cleans up**
- memory_stress verifies the working set really grew, and fails if it didn't
- the cleanup script cannot match its own command line
- http_error activates the hook with token + TTL, fails if the hook doesn't report active, and refuses containers with no hook port
- CPU rollback fails loudly if the quota is still applied (the bug described above)

### Key parts
@snippet 85-95 | cpu_stress: quota set, workers started, effect measured
@snippet 161-169 | The regression test for the quota-not-removed bug

## FILE fault-agent/tests/test_actions.py | Tests for the remediation primitives | test
### What it is
Checks `restart` (starts a stopped container, *unpauses* a paused one instead of restarting, restarts a running one), `lift_cpu_cap` (removes the quota and verifies — fails loudly if Docker ignores it), that actions obey the allowlist, and that **nothing is remediated while an experiment fault is active** on that container.

### Key parts
@snippet 43-65 | Allowlist and the "experiment in progress" refusal
