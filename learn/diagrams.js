// Interactive diagrams: nodes (boxes), edges (arrows), optional steps. Rendered as inline SVG.
window.DIAGRAMS = {};

window.DIAGRAMS.architecture = {
  view: [900, 480],
  nodes: [
    {id: "ui", x: 330, y: 10, w: 240, h: 56, label: "Control Center", sub: "Next.js · :3000", link: "frontend/app/page.tsx",
     info: "The web dashboard you look at. It only talks to the control plane over HTTP; it never touches Docker or the database."},
    {id: "ollama", x: 40, y: 110, w: 180, h: 56, label: "Ollama", sub: "local LLM (on your PC)", link: "app/ai/llm.py",
     info: "A language model running locally. It only EXPLAINS evidence that the control plane hands it. It cannot run commands."},
    {id: "cp", x: 330, y: 110, w: 240, h: 60, label: "Control plane", sub: "FastAPI · :8000", link: "main.py",
     info: "The brain. Runs experiments, measures damage, builds the resilience model, predicts, asks the AI, enforces remediation policy."},
    {id: "redis", x: 600, y: 110, w: 100, h: 56, label: "Redis", sub: "live state", link: "app/redis_client.py",
     info: "Fast memory for the newest service state. Filled by the telemetry-consumer, read by the control plane."},
    {id: "agent", x: 730, y: 110, w: 150, h: 56, label: "fault-agent", sub: "Docker access · :8095", link: "fault-agent/agent/core.py",
     info: "The ONLY program allowed to touch Docker. It refuses anything not on its allowlist and always undoes faults after a time limit."},
    {id: "pg", x: 40, y: 215, w: 170, h: 56, label: "Postgres", sub: "long-term storage", link: "app/models.py",
     info: "Stores experiments, their results, telemetry events, incidents, the service registry and remediation audit log."},
    {id: "consumer", x: 250, y: 215, w: 170, h: 56, label: "telemetry-consumer", sub: "Kafka → DB", link: "app/telemetry_consumer.py",
     info: "Reads the telemetry topic from Kafka and writes each event into Postgres (history) and Redis (latest state)."},
    {id: "kafka", x: 460, y: 215, w: 110, h: 56, label: "Kafka", sub: "topic: telemetry", link: "app/telemetry.py",
     info: "A message pipe. Every monitored service drops one small JSON event per request into it, fire-and-forget."},
    {id: "docker", x: 730, y: 215, w: 150, h: 46, label: "Docker engine", sub: "", link: "docker-compose.yml",
     info: "Runs every container. The fault-agent asks it to stop, pause, slow down or starve containers."},
    {id: "wl", group: true, x: 200, y: 330, w: 690, h: 135, label: "Monitored workload — the system we break on purpose"},
    {id: "users", x: 30, y: 375, w: 140, h: 56, label: "Synthetic users", sub: "workload runner", link: "app/experiments/workload.py",
     info: "Fake users sent by the control plane during an experiment. They measure damage from the OUTSIDE, so it is visible even if a service is dead."},
    {id: "gateway", x: 225, y: 375, w: 140, h: 56, label: "gateway", sub: "Python", link: "gateway/main.py",
     info: "The front door of the demo system. Users call POST /api/jobs here."},
    {id: "java", x: 385, y: 375, w: 140, h: 56, label: "java-service", sub: "Spring Boot", link: "java-service/src/main/java/com/aegis/service/jobs/JobController.java",
     info: "Business service. Calls the C++ service to compute, saves the job in Postgres."},
    {id: "cpp", x: 545, y: 375, w: 140, h: 56, label: "cpp-service", sub: "C++", link: "cpp-service/src/main.cpp",
     info: "A fast compute service written in C++. Does CPU work for each request."},
    {id: "jt", x: 705, y: 375, w: 140, h: 56, label: "job-tracker", sub: "your app (test copy)", link: "targets/job-tracker/README.md",
     info: "An isolated test copy of your own Job Application Tracker, monitored the same way."},
  ],
  edges: [
    {from: "ui", to: "cp", label: "HTTP"}, {from: "cp", to: "ollama", label: "explain"},
    {from: "cp", to: "redis"}, {from: "cp", to: "pg", label: "read/write"},
    {from: "cp", to: "agent", label: "inject fault"}, {from: "agent", to: "docker"},
    {from: "kafka", to: "consumer"}, {from: "consumer", to: "pg"},
    {from: "wl", to: "kafka", label: "telemetry", dash: true},
    {from: "agent", to: "wl", label: "faults land here", dash: true},
    {from: "users", to: "gateway"}, {from: "gateway", to: "java"}, {from: "java", to: "cpp"},
    {from: "java", to: "pg"},
  ],
};

window.DIAGRAMS.journey = {
  view: [900, 330],
  nodes: [
    {id: "users", x: 20, y: 30, w: 120, h: 56, label: "Synthetic user", sub: "workload runner", link: "app/experiments/workload.py"},
    {id: "gateway", x: 190, y: 30, w: 120, h: 56, label: "gateway", sub: "Python :8090", link: "gateway/main.py"},
    {id: "java", x: 360, y: 30, w: 120, h: 56, label: "java-service", sub: "Spring :8081", link: "java-service/src/main/java/com/aegis/service/jobs/JobController.java"},
    {id: "cpp", x: 530, y: 30, w: 120, h: 56, label: "cpp-service", sub: "C++ :8082", link: "cpp-service/src/main.cpp"},
    {id: "pg", x: 720, y: 30, w: 150, h: 56, label: "Postgres", sub: "jobs table", link: "java-service/src/main/java/com/aegis/service/jobs/Job.java"},
    {id: "kafka", x: 330, y: 220, w: 150, h: 56, label: "Kafka", sub: "topic: telemetry", link: "app/telemetry.py"},
    {id: "consumer", x: 520, y: 220, w: 150, h: 56, label: "telemetry-consumer", sub: "", link: "app/telemetry_consumer.py"},
    {id: "pg2", x: 740, y: 180, w: 130, h: 46, label: "Postgres", sub: "telemetry_events", link: "app/models.py"},
    {id: "redis", x: 740, y: 260, w: 130, h: 46, label: "Redis", sub: "live state", link: "app/redis_client.py"},
  ],
  edges: [
    {from: "users", to: "gateway", label: "POST /api/jobs"},
    {from: "gateway", to: "java", label: "POST /jobs"},
    {from: "java", to: "cpp", label: "GET /compute"},
    {from: "java", to: "pg", label: "save Job"},
    {from: "gateway", to: "kafka", dash: true}, {from: "java", to: "kafka", dash: true}, {from: "cpp", to: "kafka", dash: true},
    {from: "kafka", to: "consumer"}, {from: "consumer", to: "pg2"}, {from: "consumer", to: "redis"},
  ],
  steps: [
    {title: "A user asks for a job", nodes: ["users", "gateway"], edges: [0],
     text: "The synthetic user sends POST /api/jobs?n=100000 to the gateway. The gateway creates a request id and a trace id — a label that will follow this request everywhere."},
    {title: "The gateway forwards it", nodes: ["gateway", "java"], edges: [1],
     text: "The gateway calls java-service with the same ids in the X-Request-ID and X-Trace-Id headers. It waits at most 1 s to connect and 5 s for an answer."},
    {title: "Java asks C++ to do the work", nodes: ["java", "cpp"], edges: [2],
     text: "java-service calls cpp-service (1 s connect, 2 s read timeout). If C++ is down, Java turns the failure into a 503/504 that names the broken dependency."},
    {title: "C++ burns some CPU", nodes: ["cpp"], edges: [],
     text: "cpp-service counts the primes up to n with a sieve — real CPU work whose cost grows with n — and replies with the count and how long it took."},
    {title: "Java saves the result", nodes: ["java", "pg"], edges: [3],
     text: "java-service writes a row in the jobs table in Postgres, then answers the gateway. The answer travels back java → gateway → user."},
    {title: "Every service reports what happened", nodes: ["gateway", "java", "cpp", "kafka"], edges: [4, 5, 6],
     text: "Meanwhile each service dropped a small telemetry event into Kafka (latency, status code, trace id). Fire-and-forget: if Kafka is down, the user's request is not affected."},
    {title: "The consumer stores the events", nodes: ["kafka", "consumer"], edges: [7],
     text: "telemetry-consumer reads the events in batches and validates each one."},
    {title: "History in Postgres, latest state in Redis", nodes: ["consumer", "pg2", "redis"], edges: [8, 9],
     text: "Each event is saved once (duplicates are ignored) into Postgres for history, and Redis keeps the newest state per service. Blast-radius analysis later reads these rows."},
  ],
};

window.DIAGRAMS.lifecycle = {
  view: [900, 300],
  nodes: [
    {id: "pending", x: 15, y: 45, w: 110, h: 50, label: "PENDING", sub: "validated, saved"},
    {id: "run", group: true, x: 150, y: 15, w: 600, h: 110, label: "RUNNING"},
    {id: "baseline", x: 165, y: 45, w: 100, h: 50, label: "baseline", sub: "watch normal"},
    {id: "injecting", x: 290, y: 45, w: 100, h: 50, label: "injecting", sub: "fault ON"},
    {id: "rollback", x: 415, y: 45, w: 100, h: 50, label: "rollback", sub: "fault OFF"},
    {id: "recovering", x: 540, y: 45, w: 100, h: 50, label: "recovering", sub: "watch heal"},
    {id: "completed", x: 775, y: 45, w: 110, h: 50, label: "COMPLETED", sub: "then analysis"},
    {id: "aborted", x: 190, y: 200, w: 120, h: 50, label: "ABORTED", sub: "operator / restart"},
    {id: "failed", x: 380, y: 200, w: 120, h: 50, label: "FAILED", sub: "error, rolled back"},
    {id: "rbfailed", x: 570, y: 200, w: 170, h: 50, label: "ROLLBACK_FAILED", sub: "blocks new runs"},
  ],
  edges: [
    {from: "pending", to: "baseline"}, {from: "baseline", to: "injecting"}, {from: "injecting", to: "rollback"},
    {from: "rollback", to: "recovering"}, {from: "recovering", to: "completed"},
    {from: "run", to: "aborted", label: "abort", dash: true}, {from: "run", to: "failed", label: "error", dash: true},
    {from: "rollback", to: "rbfailed", label: "undo fails", dash: true},
  ],
  steps: [
    {title: "Create", nodes: ["pending"], edges: [],
     text: "The request is validated (known target, allowed fault, sane numbers). The row is saved with active_slot = TRUE — the database itself now blocks any second experiment."},
    {title: "Baseline", nodes: ["baseline"], edges: [0],
     text: "Nothing is broken yet. FaultScope (and the synthetic users) record how the system normally behaves. This is what the fault will be compared against."},
    {title: "Injecting", nodes: ["injecting"], edges: [1],
     text: "The injector asks the fault-agent to apply the fault. fault_applied_at is stamped only after the agent confirms the effect is real. The fault stays on for duration_s."},
    {title: "Rollback — always", nodes: ["rollback"], edges: [2],
     text: "The fault is removed on EVERY exit path: normal end, abort, or error. The agent verifies the system is really restored."},
    {title: "Recovering", nodes: ["recovering"], edges: [3],
     text: "The system is watched while it heals. How long until normal? That is the recovery time."},
    {title: "Completed", nodes: ["completed"], edges: [4],
     text: "The slot is freed and a few seconds later the blast-radius analysis compares baseline, fault and recovery windows."},
    {title: "Things can go wrong", nodes: ["aborted", "failed"], edges: [5, 6],
     text: "ABORTED: an operator pressed the kill switch or the control plane restarted (rollback still executed). FAILED: an error occurred, but the fault was rolled back."},
    {title: "The dangerous case", nodes: ["rbfailed"], edges: [7],
     text: "If undoing the fault fails, the experiment becomes ROLLBACK_FAILED and keeps the slot occupied: the fault may still be applied, so no new experiment may start until it is resolved."},
  ],
};

(function () {
  const NS = "http://www.w3.org/2000/svg";
  const mk = (tag, attrs, parent) => {
    const e = document.createElementNS(NS, tag);
    for (const k in attrs) e.setAttribute(k, attrs[k]);
    if (parent) parent.appendChild(e);
    return e;
  };
  function anchor(a, b, gap) {
    const ac = {x: a.x + a.w / 2, y: a.y + a.h / 2}, bc = {x: b.x + b.w / 2, y: b.y + b.h / 2};
    const dx = bc.x - ac.x, dy = bc.y - ac.y;
    const t = Math.min(dx ? a.w / 2 / Math.abs(dx) : 1e9, dy ? a.h / 2 / Math.abs(dy) : 1e9);
    const len = Math.hypot(dx, dy) || 1;
    return {x: ac.x + dx * t + dx / len * gap, y: ac.y + dy * t + dy / len * gap};
  }

  window.mountDiagram = function (host, name) {
    const spec = window.DIAGRAMS[name];
    if (!spec) { host.textContent = "(missing diagram: " + name + ")"; return; }
    const byId = Object.fromEntries(spec.nodes.map(n => [n.id, n]));
    const svg = mk("svg", {viewBox: `0 0 ${spec.view[0]} ${spec.view[1]}`, role: "img", "aria-label": name});
    const defs = mk("defs", {}, svg);
    for (const [id, cls] of [["ah", "#8a93a6"], ["ahon", "#2f6fed"]]) {
      const m = mk("marker", {id, viewBox: "0 0 10 10", refX: "9", refY: "5", markerWidth: "7", markerHeight: "7", orient: "auto-start-reverse"}, defs);
      mk("path", {d: "M0 0L10 5L0 10z", fill: cls}, m);
    }
    for (const n of spec.nodes.filter(n => n.group)) {
      mk("rect", {class: "group", x: n.x, y: n.y, width: n.w, height: n.h, rx: 14}, svg);
      const t = mk("text", {class: "gl", x: n.x + 14, y: n.y + 20}, svg); t.textContent = n.label;
    }
    const edgeEls = spec.edges.map(e => {
      const a = byId[e.from], b = byId[e.to];
      const p1 = anchor(a, b, 2), p2 = anchor(b, a, 3);
      const g = {line: mk("line", {class: "edge" + (e.dash ? " dash" : ""), x1: p1.x, y1: p1.y, x2: p2.x, y2: p2.y}, svg)};
      if (e.label) {
        g.label = mk("text", {class: "el", x: (p1.x + p2.x) / 2, y: (p1.y + p2.y) / 2 + 4}, svg);
        g.label.textContent = e.label;
      }
      return g;
    });
    const nodeEls = {};
    for (const n of spec.nodes.filter(n => !n.group)) {
      const g = mk("g", {class: "node" + (n.link || n.info ? " link" : "")}, svg);
      mk("rect", {x: n.x, y: n.y, width: n.w, height: n.h, rx: 10}, g);
      const t1 = mk("text", {x: n.x + n.w / 2, y: n.y + (n.sub ? n.h / 2 - 3 : n.h / 2 + 5)}, g); t1.textContent = n.label;
      if (n.sub) { const t2 = mk("text", {class: "sub", x: n.x + n.w / 2, y: n.y + n.h / 2 + 14}, g); t2.textContent = n.sub; }
      nodeEls[n.id] = g;
      g.addEventListener("click", () => selectNode(n));
    }
    host.classList.add("diagram");
    host.appendChild(svg);
    const box = document.createElement("div");
    box.className = "stepbox";
    host.appendChild(box);

    function paint(nodes, edges) {
      for (const id in nodeEls) nodeEls[id].classList.toggle("on", nodes.includes(id));
      edgeEls.forEach((g, i) => {
        g.line.classList.toggle("on", edges.includes(i));
        if (g.label) g.label.classList.toggle("on", edges.includes(i));
      });
    }
    function selectNode(n) {
      if (steps) stop();
      paint([n.id], []);
      box.innerHTML = `<b></b><span></span> `;
      box.querySelector("b").textContent = n.label + (n.sub ? " — " + n.sub : "");
      box.querySelector("span").textContent = n.info || "";
      if (n.link) {
        const a = document.createElement("a"); a.href = "#/file/" + n.link; a.textContent = "Open the file that implements this →";
        box.appendChild(document.createElement("br")); box.appendChild(a);
      }
    }
    const steps = spec.steps;
    let i = 0, timer = null;
    function stop() { clearInterval(timer); timer = null; if (play) play.textContent = "▶ Auto-play"; }
    let play;
    function show() {
      const s = steps[i];
      paint(s.nodes || [], s.edges || []);
      box.innerHTML = `<b></b><span></span>`;
      box.querySelector("b").textContent = `Step ${i + 1}/${steps.length} — ${s.title}`;
      box.querySelector("span").textContent = s.text;
      count.textContent = `${i + 1} / ${steps.length}`;
    }
    let count;
    if (steps) {
      const ctl = document.createElement("div"); ctl.className = "stepctl";
      const prev = Object.assign(document.createElement("button"), {className: "btn", textContent: "← Back"});
      const next = Object.assign(document.createElement("button"), {className: "btn primary", textContent: "Next step →"});
      play = Object.assign(document.createElement("button"), {className: "btn", textContent: "▶ Auto-play"});
      count = document.createElement("span");
      prev.onclick = () => { stop(); i = Math.max(0, i - 1); show(); };
      next.onclick = () => { stop(); i = Math.min(steps.length - 1, i + 1); show(); };
      play.onclick = () => {
        if (timer) return stop();
        play.textContent = "⏸ Pause";
        if (i >= steps.length - 1) i = -1;
        timer = setInterval(() => { if (i >= steps.length - 1) return stop(); i++; show(); }, 2600);
      };
      ctl.append(prev, next, play, count);
      host.appendChild(ctl);
      show();
    } else {
      box.innerHTML = "<span class='muted'>Click any box to see what it is and jump to its code.</span>";
    }
  };
})();
