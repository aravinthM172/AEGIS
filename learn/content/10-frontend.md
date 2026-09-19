# AREA frontend | 14. The Control Center (dashboard) | The web UI you see at localhost:3000
The dashboard is a **Next.js 16 + React 19 + TypeScript** app. It has no database and no logic about failures: it only **calls the control plane's API** and draws the answers.

Concepts for beginners:
- **React** builds a page from small reusable pieces called *components*. A component is a function that returns HTML-like markup (JSX).
- **Next.js** adds routing on top: a file at `app/experiments/page.tsx` becomes the URL `/experiments`. A folder named `[id]` means "any value here", so `app/experiments/[id]/page.tsx` serves `/experiments/abc123`.
- **TypeScript** is JavaScript with types. `strict: true` makes the compiler catch many mistakes before you run anything.
- **Polling**: most pages call the API every few seconds (`usePoll`) so numbers stay live — no WebSockets needed.

Every page follows the same pattern: `usePoll("/api/…")` → data → draw tables/cards. Actions (start an experiment, approve something) call `api(path, {method: "POST"})` and send the **API key** from the sidebar.

## FILE frontend/lib/api.ts | The one function that talks to the control plane | code
### What it is
Everything the UI needs to call the backend: the base URL, API-key storage, error translation and number formatters.

### How it works
- `API_URL` comes from `NEXT_PUBLIC_API_URL` (baked in at build time) or defaults to `http://localhost:8000`.
- The API key is kept in the browser's `localStorage` under `faultscope.apiKey`; `setApiKey` also fires a custom event so other parts of the page notice. Every access is wrapped in `try/catch` because storage can be blocked.
- `api(path, {method, body})` adds `Content-Type` for bodies and the **`X-API-Key`** header if a key is set, always fetches with `cache: "no-store"` (never show stale data), and throws an `ApiError` on failure.
- `describe` turns status codes into human messages: 401 "set a valid API key in the sidebar", 403 "your role isn't allowed", plus the server's own `detail`/`errors` text. If the server can't be reached at all: "Cannot reach the control plane at … Is it running?"
- `fmt` formats numbers consistently (ms vs s, %, "5m ago"), printing `–` for missing values instead of `null`.

### Key parts
@snippet 9-25 | API key in localStorage, safely
@snippet 37-45 | Turn errors into plain language
@snippet 47-71 | The api() function
@snippet 73-84 | Formatters

## FILE frontend/lib/hooks.ts | usePoll: fetch now and every N seconds | code
### What it is
A React **hook** (a reusable piece of behaviour). `usePoll("/api/overview", 3000)` returns `{data, error, loading, refresh}`.

### How it works
- Fetches immediately and then on an interval (0 = once). Pass `null` as the path to pause (used to wait for something else first).
- **Keeps the last good data** if a refresh fails, and shows the error separately — the page doesn't blank out when the backend hiccups.
- An `alive` flag prevents updating state after the component is gone (a classic React warning).
- The cleanup function clears the timer when you leave the page.
- `useApiKey` re-reads the stored key when the `faultscope-key` event fires.

### Key parts
@snippet 14-48 | usePoll
@snippet 50-59 | useApiKey

??Why keep the previous data when a poll fails? || A single failed request (a restart, a slow moment) shouldn't erase the whole screen. Showing the last good data plus an error banner is more useful and less alarming.

## FILE frontend/app/layout.tsx | The frame around every page | code
### What it is
Next.js's **root layout**: every page is rendered inside it. It sets the browser tab title ("FaultScope Control Center"), imports the global CSS and wraps `children` (the current page) in the `Shell` component.

### Key parts
@snippet 6-19 | Metadata and layout

## FILE frontend/components/Shell.tsx | Sidebar navigation and the API-key box | code
### What it is
The permanent sidebar: brand, 12 navigation links and a password-style input where you paste your API key.

### How it works
- `NAV` lists the pages. The link for the current URL gets the `active` class (`/` matches exactly; others by prefix).
- Typing in the key box saves it immediately (`setApiKey`). It's stored **only in your browser**.
- The label states which actions need it: running experiments, actions and analysis.

### Key parts
@snippet 8-21 | The navigation list
@snippet 42-56 | The API-key input

## FILE frontend/components/ui.tsx | Small shared building blocks | code
### What it is
Reusable pieces used by every page: `Pill` (coloured label), `Dot`, `Card`, `Bar` (a horizontal bar), `ScoreBar` (0–100 resilience score), `KV`, `Empty`, `ErrorNote`, and status pills.

### How it works
- **Colour language** is consistent everywhere: green = good/verified, amber = warning/pending, red = failed/anomalous, blue = in progress, violet = AI/hypothesis, grey = unknown.
- `ExperimentStatus`, `ActionStatus`, `LevelPill` (HIGH/MEDIUM/LOW), `ConfidencePill` map a status string to a tone through a lookup table.
- `ServiceState` renders a service's status — and relabels `insufficient_data` as "idle / no traffic" and `no_telemetry` as "not instrumented", because those are what they actually mean.
- `Bar` chooses green/amber/red by value (≥ 60 red, ≥ 20 amber).

### Key parts
@snippet 29-40 | Bar: colour by severity
@snippet 55-70 | Status → colour tables
@snippet 72-84 | ServiceState with honest labels

## FILE frontend/components/Graph.tsx | Draws the dependency graph as SVG | code
### What it is
A hand-written graph renderer (no chart library). Boxes for infrastructure (database, cache, broker), ellipses for services, arrows for "depends on".

### How it works
- `layers` assigns each node a **depth** by relaxing edges repeatedly (a node sits one level below whatever depends on it) — a simple layered layout. Nodes in a layer are sorted by name and spread evenly.
- **Colours**: in *live* mode by service status (green normal, red anomalous, amber idle, grey not instrumented); in *overlay* mode (an experiment picked) red = fault target, amber = measured affected, green = measured unaffected, grey = not measurable.
- **Edges**: dashed grey = declared only, blue = seen in telemetry, red and thick = on the measured propagation path or > 20 % errors. Hovering shows calls and error rate.
- `edgesFrom` merges declared and observed edges depending on the chosen basis.

### Key parts
@snippet 18-31 | Layering by depth
@snippet 56-66 | Node colour in live and overlay modes
@snippet 121-136 | Merge declared and observed edges

## FILE frontend/components/AnalysisView.tsx | Shows one AI analysis without ever overselling it | code
### What it is
Renders an analysis result. It always shows the **measured, code-written summary** first ("Measured (no model)"). The model's hypothesis is shown only if its status is `accepted`, and labelled *"LLM hypothesis, validated against evidence"*.

### How it works
- `running` → "Local model is reading the evidence…".
- `accepted` → root cause with confidence, evidence findings with their `[refs]`, recommended actions with an allowlisted-action pill, alternatives and limitations.
- Anything else (`rejected`, `llm_unavailable`, `failed`) → a red box: **"Not presented as fact"** with the reasons.

### Key parts
@snippet 6-16 | Status, measured summary, and the validated label
@snippet 29-31 | Rejected answers are never shown as fact

## FILE frontend/app/page.tsx | Dashboard: live services, active experiment, risks | code
### What it is
The home page. It polls `/api/overview` every 3 seconds (one call — see [[app/overview.py]]) and shows: the running experiment (with a "watch live" link), counts, service states, recent experiments, top risks and recent actions.

### Key parts
@snippet 8-12 | One poll drives the page
@snippet 20-36 | The running-experiment banner

## FILE frontend/app/services/page.tsx | List of services with live status | code
### What it is
A table of the registry from `/api/overview`: name, kind, technology, status (with failure modes), last seen and events emitted.

### Key parts
@snippet 8-14 | Poll and title

## FILE frontend/app/services/[name]/page.tsx | One service in detail | code
### What it is
Shows a service's description and declared dependencies, **who is at risk if it fails** (`/dependents?basis=combined&window_minutes=1440` — structural, not measured), and its resilience profile from `/api/resilience/{name}`.

### Key parts
@snippet 11-13 | Three API calls

## FILE frontend/app/topology/page.tsx | The interactive dependency graph | code
### What it is
Draws [[frontend/components/Graph.tsx]] with a **basis** selector (declared / observed / both) and an **overlay** selector: pick a finished real experiment and the graph turns into that experiment's measured blast radius.

### How it works
The `overlay` is computed from the experiment's analysis result: direct → `direct`, indirect → `indirect`, unaffected non-client services → healthy, and the propagation edges are highlighted.

### Key parts
@snippet 10-18 | State and API calls
@snippet 20-22 | Build edges and live status

## FILE frontend/app/experiments/page.tsx | Launch experiments and campaigns | code
### What it is
The control panel. A form to choose target, fault type, its parameters, timings, workload rate, and **dry run vs real**. Targets and parameter fields come from `/api/experiments/catalog` and `/api/overview`, so the form always matches the backend's rules; protected targets are hidden and only compatible target kinds are offered. Also: start the **standard campaign** (with N repetitions), abort it, and list recent experiments.

### How it works
`launch` collects the parameters that are filled in, converts them to numbers, POSTs to `/api/experiments` and jumps to the new experiment's page. Errors (e.g. 409 "another experiment is already active", 401 wrong key) appear as a message.

### Key parts
@snippet 17-27 | Form state
@snippet 37-56 | launch: build the request, send, navigate
@snippet 58-65 | Start the standard suite

## FILE frontend/app/experiments/[id]/page.tsx | Watch an experiment live and read its results | code
### What it is
Polls the experiment every 2 s. While it runs: status, phase, timeline and an **abort** button. When finished it fetches the measured result (`/result`), the failure signature and — on request — asks the AI to analyse it.

### How it works
- The result poll only starts once the experiment is in a final state, and keeps polling every 3 s until analysis exists.
- `dur` uses the *measured* window lengths when analysed, else the planned ones.
- `askAi` posts `{experiment_id}` to `/api/analysis` and then polls that analysis.
- Shows a banner for the hypothesis, error text and "control · nothing applied" for dry runs.

### Key parts
@snippet 27-34 | The chain of polls
@snippet 42-51 | Ask the AI about this experiment

## FILE frontend/app/resilience/page.tsx | Scores, critical dependencies, noise floor | code
### What it is
Polls `/api/resilience` every 10 s. Shows the criticality ranking, the **noise-floor** banner ("control runs showed up to X % normal variation — impacts near that are not distinguishable") and per-service profiles you can expand. Untested combinations are listed, never scored.

### Key parts
@snippet 9-14 | Data, sorted worst-first
@snippet 33-42 | The noise-floor explanation

## FILE frontend/app/predictions/page.tsx | Live early warning and the backtest | code
### What it is
Left: live per-service state (state, p95 multiplier, error %). Right: warnings ("Resembles: latency on postgres", similarity, stage, at-risk services with risk numbers, expected impact/recovery). A button runs the **backtest** and shows measured accuracy with its caveats.

### Key parts
@snippet 8-22 | Live poll and running the backtest

## FILE frontend/app/twin/page.tsx | "What if?" simulator | code
### What it is
Choose a target and a fault, optionally an architecture change (queue, fallback with hit ratio, replicas). Shows **measured** vs **simulated** side by side, each labelled by basis, plus the twin's validation accuracy.

### Key parts
@snippet 10-13 | Data sources
@snippet 26-42 | run(): what-if or architecture comparison

## FILE frontend/app/analyst/page.tsx | Ask the AI about an experiment | code
### What it is
Pick an experiment (or type a free-text question — entity linking finds the right one), start an analysis, and watch it via [[frontend/components/AnalysisView.tsx]]. Past analyses are listed on the side.

### Key parts
@snippet 16-22 | Experiments, history, and the shown analysis

## FILE frontend/app/actions/page.tsx | Remediation: propose, approve, audit | code
### What it is
Three panels: live **recommendations** (from predictions, each with its policy verdict), the list of **actions** with status pills and approve/reject buttons (admin only), and the **audit log**. New actions default to *dry run*.

### Key parts
@snippet 11-14 | Four polls
@snippet 23-23 | Propose an action
@snippet 32-32 | Approve or reject

## FILE frontend/app/incidents/page.tsx | The original Aegis incident feature | code
### What it is
Create and list incidents (API → Kafka → the Java consumer, plus Redis state). Experiments are recorded separately under Experiments.

### Key parts
@snippet 11-11 | List incidents
@snippet 21-21 | Create one

## FILE frontend/app/knowledge/page.tsx | Search the AI's library | code
### What it is
Library statistics (chunks, by source, retrieval mode), a search box that calls `/api/knowledge/search` (so you can see what the AI would retrieve), and a reindex button (operator).

### Key parts
@snippet 11-11 | Stats
@snippet 20-20 | Search call

## FILE frontend/app/logs/page.tsx | Live event feed | code
### What it is
Polls `/api/events/recent` every 2 s: telemetry events and experiment lifecycle events merged, newest first, filterable by service, pausable.

### Key parts
@snippet 11-12 | Overview for the filter, feed with optional service filter

## FILE frontend/app/globals.css | The dark theme | config
### What it is
CSS variables for the palette (`--bg`, `--panel`, `--accent`, `--green`, `--amber`, `--red`, `--violet`), the two-column shell (220 px sidebar + main), and the classes the components use (`.card`, `.pill`, `.bar`, `.banner`, `.grid g2/g4`).

### Key parts
@snippet 1-25 | Palette and base styles

## FILE frontend/package.json | Frontend dependencies and scripts | config
### What it is
Next.js `^16.3.5` (upgraded after `npm audit` found a vulnerable pinned version), React 19.1, TypeScript 5.7. Scripts: `dev`, `build`, `start`, `typecheck`.

### Key parts
@snippet 5-21 | Scripts and dependencies

## FILE frontend/tsconfig.json | TypeScript settings | config
### What it is
Standard Next.js config with **`strict: true`**. `paths` maps `@/` to the project root (why imports look like `@/lib/api`).

### Key parts
@snippet 24-29 | The @/ import alias

## FILE frontend/next.config.mjs | Next.js settings | config
### What it is
Two options: `output: "standalone"` (produces a small self-contained server for Docker) and `reactStrictMode`.

### Key parts
@snippet 1-3 | The whole file

## FILE frontend/Dockerfile | Two-stage build for the dashboard | infra
### What it is
Stage 1 installs and builds; the API URL is baked in through the build argument `NEXT_PUBLIC_API_URL` (browser code can't read runtime environment variables). Stage 2 copies only the standalone output, so the final image is small, and runs `node server.js`.

### Key parts
@snippet 1-16 | The whole file
