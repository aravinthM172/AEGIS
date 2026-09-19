(function () {
  const D = window.LEARN;
  const $ = (s, r = document) => r.querySelector(s);
  const main = $("#main"), side = $("#side");
  const esc = s => s.replace(/[&<>"]/g, c => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;"}[c]));
  const files = {}, fileArea = {};
  D.areas.forEach(a => a.files.forEach(f => { files[f.path] = f; fileArea[f.path] = a; }));
  const order = D.areas.flatMap(a => a.files.map(f => f.path));

  // ---------- progress (localStorage is optional) ----------
  let read = new Set();
  try { read = new Set(JSON.parse(localStorage.getItem("fs-learn-read") || "[]")); } catch (e) {}
  const saveRead = () => { try { localStorage.setItem("fs-learn-read", JSON.stringify([...read])); } catch (e) {} };
  const total = () => order.length + D.lessons.length;
  function toggleRead(id) { read.has(id) ? read.delete(id) : read.add(id); saveRead(); buildSide(); progress(); }
  function progress() {
    const n = [...read].filter(x => files[x] || D.lessons.some(l => "lesson:" + l.id === x)).length;
    $("#progress").textContent = `${n}/${total()} read`;
  }

  // ---------- tiny markdown ----------
  function inline(t) {
    const codes = [];
    t = t.replace(/`([^`]+)`/g, (_, c) => { codes.push(c); return '@@C' + (codes.length - 1) + '@@'; });
    t = esc(t);
    t = t.replace(/\*\*([^*]+)\*\*/g, "<b>$1</b>");
    t = t.replace(/(^|[^\w*])\*([^*\n]+)\*(?![\w*])/g, "$1<i>$2</i>");
    t = t.replace(/\[\[([^\]|]+)(?:\|([^\]]+))?\]\]/g, (_, p, label) => {
      if (files[p]) return `<a href="#/file/${p}">${esc(label || p.split("/").slice(-2).join("/"))}</a>`;
      if (D.lessons.some(l => l.id === p)) return `<a href="#/lesson/${p}">${esc(label || p)}</a>`;
      return `<code>${esc(label || p)}</code>`;
    });
    return t.replace(/@@C(\d+)@@/g, (_, i) => `<code>${esc(codes[+i])}</code>`);
  }
  function md(text, file) {
    const out = [], lines = text.split("\n");
    let i = 0;
    while (i < lines.length) {
      const ln = lines[i];
      if (!ln.trim()) { i++; continue; }
      let m;
      if ((m = ln.match(/^\{\{snip:(\d+)\}\}$/)) && file) { out.push(snippet(file.snips[+m[1]])); i++; continue; }
      if (ln.startsWith("```")) {
        const buf = []; i++;
        while (i < lines.length && !lines[i].startsWith("```")) buf.push(lines[i++]);
        i++;
        out.push(`<pre class="code plain">${esc(buf.join("\n"))}</pre>`); continue;
      }
      if ((m = ln.match(/^@diagram (\S+)/))) { out.push(`<div data-diagram="${m[1]}"></div>`); i++; continue; }
      if (ln.startsWith("??")) {
        const [q, a] = ln.slice(2).split("||");
        out.push(`<details class="quiz"><summary>🧠 ${inline(q.trim())}</summary><div class="a">${inline((a || "").trim())}</div></details>`); i++; continue;
      }
      if (ln.startsWith("> ")) {
        const buf = []; while (i < lines.length && lines[i].startsWith(">")) buf.push(lines[i++].replace(/^>\s?/, ""));
        const warn = /^(Careful|Warning|Honest)/i.test(buf[0]);
        out.push(`<div class="callout${warn ? " warn" : ""}">${buf.map(b => `<p>${inline(b)}</p>`).join("")}</div>`); continue;
      }
      if (ln.startsWith("|")) {
        const rows = []; while (i < lines.length && lines[i].startsWith("|")) rows.push(lines[i++]);
        const cells = r => r.replace(/^\||\|$/g, "").split("|").map(c => c.trim());
        const head = cells(rows[0]), body = rows.slice(2).map(cells);
        out.push(`<table><tr>${head.map(h => `<th>${inline(h)}</th>`).join("")}</tr>${body.map(r => `<tr>${r.map(c => `<td>${inline(c)}</td>`).join("")}</tr>`).join("")}</table>`); continue;
      }
      if (/^(- |\d+\. )/.test(ln)) {
        const ordered = /^\d+\. /.test(ln), items = [];
        while (i < lines.length && (/^(- |\d+\. )/.test(lines[i]) || /^\s{2,}\S/.test(lines[i]))) {
          if (/^\s{2,}\S/.test(lines[i])) items[items.length - 1] += " " + lines[i].trim();
          else items.push(lines[i].replace(/^(- |\d+\. )/, ""));
          i++;
        }
        out.push(`<${ordered ? "ol" : "ul"}>${items.map(x => `<li>${inline(x)}</li>`).join("")}</${ordered ? "ol" : "ul"}>`); continue;
      }
      if ((m = ln.match(/^(#{2,3}) (.*)/))) { out.push(`<h${m[1].length}>${inline(m[2])}</h${m[1].length}>`); i++; continue; }
      const buf = []; while (i < lines.length && lines[i].trim() && !/^(- |\d+\. |> |\||\?\?|@diagram|```|\{\{snip)/.test(lines[i])) buf.push(lines[i++]);
      out.push(`<p>${inline(buf.join(" "))}</p>`);
    }
    return out.join("\n");
  }

  // ---------- syntax highlighting (small, generic) ----------
  const KW = {
    python: "def class return if elif else for while in not and or is None True False import from as with try except finally raise yield lambda pass break continue global nonlocal async await assert del",
    java: "public private protected class interface extends implements return if else for while new static final void int long double boolean String throw throws try catch finally import package this null true false switch case break continue @Override",
    cpp: "int long double float char bool void return if else for while struct class static const auto namespace using include define new delete nullptr true false switch case break continue try catch throw unsigned std string",
    ts: "const let var function return if else for while import export from default async await new class extends type interface true false null undefined try catch throw as of in typeof",
    yaml: "true false null", json: "true false null", docker: "FROM RUN COPY CMD ENV EXPOSE WORKDIR ARG AS ENTRYPOINT VOLUME", props: "",
    css: "", md: "", text: "", xml: "",
  };
  function highlight(code, lang) {
    const kws = new Set((KW[lang] || "").split(" "));
    const cmt = ["python", "yaml", "docker", "props"].includes(lang) ? "#[^\\n]*" : lang === "md" || lang === "text" ? "(?!)" : "\\/\\/[^\\n]*|\\/\\*[\\s\\S]*?\\*\\/";
    const strs = lang === "python" ? "\"\"\"[\\s\\S]*?\"\"\"|'''[\\s\\S]*?'''|\"(?:\\\\.|[^\"\\\\\\n])*\"|'(?:\\\\.|[^'\\\\\\n])*'" : "\"(?:\\\\.|[^\"\\\\\\n])*\"|'(?:\\\\.|[^'\\\\\\n])*'|`[^`]*`";
    const re = new RegExp(`(${cmt})|(${strs})|(\\b\\d[\\d._]*\\b)|([A-Za-z_@][\\w]*)`, "g");
    let out = "", last = 0, m;
    const wrap = (cls, t) => t.split("\n").map(p => p ? `<span class="${cls}">${esc(p)}</span>` : "").join("\n");
    while ((m = re.exec(code))) {
      out += esc(code.slice(last, m.index));
      if (m[1]) out += wrap("c", m[1]); else if (m[2]) out += wrap("s", m[2]);
      else if (m[3]) out += `<span class="n">${m[3]}</span>`;
      else out += kws.has(m[4]) ? `<span class="k">${m[4]}</span>` : esc(m[4]);
      last = m.index + m[0].length;
    }
    return out + esc(code.slice(last));
  }
  function codeBlock(code, lang, start, hit) {
    const lines = highlight(code, lang).split("\n");
    return `<pre class="code">${lines.map((l, i) => `<span class="ln${hit && i + start >= hit[0] && i + start <= hit[1] ? " hit" : ""}" id="L${i + start}"><span class="no">${i + start}</span>${l}</span>`).join("")}</pre>`;
  }
  function snippet(s) {
    return `<div class="snip"><div class="cap"><span>${esc(s.caption)}</span><span class="muted"><a href="#/file/${s.path}/src/${s.start}-${s.end}">${esc(s.path.split("/").pop())} · lines ${s.start}–${s.end}</a></span></div>${codeBlock(s.code, s.lang, s.start)}</div>`;
  }

  // ---------- sidebar ----------
  function buildSide() {
    const cur = decodeURIComponent(location.hash);
    const a = (href, text, id, sub) => `<a href="${href}" class="${cur.startsWith(href) ? "on " : ""}${read.has(id) ? "done" : ""}">${esc(text)}${sub ? `<span class="sub">${esc(sub)}</span>` : ""}</a>`;
    let h = `<a href="#/home" class="${cur === "#/home" || cur === "" ? "on" : ""}">🏠 Start here</a>`;
    h += `<h4>Learning path</h4>` + D.lessons.map((l, i) => a(`#/lesson/${l.id}`, `${i + 1}. ${l.title}`, "lesson:" + l.id, `${l.minutes} min`)).join("");
    h += `<h4>Every file, by area</h4>`;
    for (const ar of D.areas) {
      const open = cur.includes("/area/" + ar.id) || ar.files.some(f => cur.startsWith("#/file/" + f.path));
      h += `<details${open ? " open" : ""}><summary>${esc(ar.title)}<span class="sub">${ar.files.length} files</span></summary>` +
        `<a href="#/area/${ar.id}">Overview of this area</a>` +
        ar.files.map(f => a(`#/file/${f.path}`, f.path.split("/").pop(), f.path, f.title)).join("") + `</details>`;
    }
    h += `<h4>Reference</h4>` + a("#/glossary", "📖 Glossary (plain words)", "");
    side.innerHTML = h;
  }

  // ---------- pages ----------
  function mountDiagrams() { main.querySelectorAll("[data-diagram]").forEach(el => window.mountDiagram(el, el.dataset.diagram)); }
  function readBtn(id) { return `<button class="btn ${read.has(id) ? "done" : ""}" data-read="${esc(id)}">${read.has(id) ? "✓ Marked as read" : "Mark as read"}</button>`; }
  function wireRead() { main.querySelectorAll("[data-read]").forEach(b => b.onclick = () => { toggleRead(b.dataset.read); b.className = "btn" + (read.has(b.dataset.read) ? " done" : ""); b.textContent = read.has(b.dataset.read) ? "✓ Marked as read" : "Mark as read"; }); }

  function home() {
    const pct = Math.round(100 * [...read].filter(x => files[x] || x.startsWith("lesson:")).length / total());
    main.innerHTML = `<div class="hero"><h1>Understand FaultScope, file by file</h1>
      <p class="lead">A guided tour of the whole project: what every part is for, how the pieces talk to each other, and the real code behind it. No experience assumed.</p>
      <a class="btn primary" href="#/lesson/${D.lessons[0] ? D.lessons[0].id : ""}">Start lesson 1 →</a>
      <div class="bar"><i style="width:${pct}%"></i></div><div class="small muted">${pct}% of pages marked as read</div></div>
      <h2>How to use this site</h2>
      <ul><li>Follow the <b>Learning path</b> (left) in order. Each lesson is 5–10 minutes and has small questions to test yourself.</li>
      <li>Every file has two tabs: <b>Explain</b> (plain words + the important real lines) and <b>Source</b> (the full real file).</li>
      <li>Stuck on a word? Open the <a href="#/glossary">Glossary</a>. Looking for something? Use the search box (press <code>/</code>).</li></ul>
      <h2>The big picture</h2><div data-diagram="architecture"></div>
      <h2>Jump into an area</h2><div class="grid">${D.areas.map(a => `<a class="card" href="#/area/${a.id}"><b>${esc(a.title)}</b><span class="muted small">${esc(a.desc)}</span><br><span class="small">${a.files.length} files</span></a>`).join("")}</div>`;
    mountDiagrams();
  }
  function lesson(id) {
    const i = D.lessons.findIndex(l => l.id === id); if (i < 0) return notFound();
    const l = D.lessons[i], p = D.lessons[i - 1], n = D.lessons[i + 1];
    main.innerHTML = `<div class="muted small">Lesson ${i + 1} of ${D.lessons.length} · ${l.minutes} min</div><h1>${esc(l.title)}</h1>${md(l.md)}
      <div class="nav2"><div>${p ? `<a class="btn" href="#/lesson/${p.id}">← ${esc(p.title)}</a>` : ""}</div>${readBtn("lesson:" + l.id)}<div>${n ? `<a class="btn primary" href="#/lesson/${n.id}">${esc(n.title)} →</a>` : `<a class="btn primary" href="#/area/${D.areas[0].id}">Explore the files →</a>`}</div></div>`;
    mountDiagrams(); wireRead();
  }
  function area(id) {
    const a = D.areas.find(x => x.id === id); if (!a) return notFound();
    main.innerHTML = `<h1>${esc(a.title)}</h1><p class="lead">${esc(a.desc)}</p>${md(a.intro)}<h2>Files in this area</h2>
      <div class="grid">${a.files.map(f => `<a class="card" href="#/file/${f.path}"><b>${esc(f.path.split("/").pop())}${f.kind === "test" ? '<span class="badge test">test</span>' : ""}</b><span class="muted small">${esc(f.title)}</span></a>`).join("")}</div>`;
    mountDiagrams();
  }
  function file(path, tab, range) {
    const f = files[path]; if (!f) return notFound();
    const idx = order.indexOf(path), p = files[order[idx - 1]], n = files[order[idx + 1]];
    const hit = range ? range.split("-").map(Number) : null;
    const explain = f.sections.map(s => `<h2>${inline(s.h)}</h2>${md(s.md, f)}`).join("");
    main.innerHTML = `<div class="muted small"><a href="#/area/${fileArea[path].id}">${esc(fileArea[path].title)}</a> ›</div>
      <h1>${esc(f.path.split("/").pop())}<span class="badge${f.kind === "test" ? " test" : ""}">${esc(f.kind)}</span></h1>
      <div class="path">${esc(f.path)} · ${f.lines} lines</div><p class="lead">${esc(f.title)}</p>
      <div class="tabs"><button data-t="e" class="${tab !== "src" ? "on" : ""}">Explain</button><button data-t="s" class="${tab === "src" ? "on" : ""}">Source (real file)</button></div>
      <div id="tabbody">${tab === "src" ? `<div class="snip">${codeBlock(D.sources[path], f.lang, 1, hit)}</div>` : explain}</div>
      <div class="nav2"><div>${p ? `<a class="btn" href="#/file/${p.path}">← ${esc(p.path.split("/").pop())}</a>` : ""}</div>${readBtn(path)}<div>${n ? `<a class="btn primary" href="#/file/${n.path}">${esc(n.path.split("/").pop())} →</a>` : ""}</div></div>`;
    main.querySelectorAll(".tabs button").forEach(b => b.onclick = () => { location.hash = `#/file/${path}${b.dataset.t === "s" ? "/src" : ""}`; });
    mountDiagrams(); wireRead();
    if (hit) { const el = document.getElementById("L" + hit[0]); if (el) el.scrollIntoView({block: "center"}); }
  }
  function glossary() {
    main.innerHTML = `<h1>Glossary</h1><p class="lead">Plain-language meanings of every term used here.</p><input id="gq" type="search" placeholder="Filter terms…" style="width:100%;padding:8px 12px;border:1px solid var(--line);border-radius:8px;background:var(--panel);color:var(--ink);font:inherit">
      <dl class="gl" id="gl"></dl>`;
    const draw = q => { $("#gl").innerHTML = D.glossary.filter(g => !q || (g.term + g.def).toLowerCase().includes(q)).map(g => `<dt>${esc(g.term)}</dt><dd>${inline(g.def)}</dd>`).join(""); };
    draw(""); $("#gq").oninput = e => draw(e.target.value.toLowerCase());
  }
  function search(q) {
    q = decodeURIComponent(q).toLowerCase().trim();
    const hits = [];
    const add = (title, href, text, kind) => { text = text.replace(/\{\{snip:\d+\}\}|@diagram \S+|@@C\d+@@|\?\?/g, " "); const i = text.toLowerCase().indexOf(q); if (i >= 0) hits.push({title, href, kind, ctx: text.slice(Math.max(0, i - 60), i + 140)}); };
    D.lessons.forEach(l => add(l.title, "#/lesson/" + l.id, l.md, "lesson"));
    D.areas.forEach(a => a.files.forEach(f => { const t = f.path + " " + f.title + " " + f.sections.map(s => s.h + " " + s.md).join(" "); add(f.path, "#/file/" + f.path, t, a.title); }));
    D.glossary.forEach(g => add(g.term, "#/glossary", g.term + ": " + g.def, "glossary"));
    const mk = t => esc(t).replace(new RegExp(q.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"), "ig"), m => `<mark>${m}</mark>`);
    main.innerHTML = `<h1>Search: “${esc(q)}”</h1><p class="muted">${hits.length} result${hits.length === 1 ? "" : "s"}</p>` +
      hits.slice(0, 40).map(h => `<div class="hit"><a class="t" href="${h.href}">${esc(h.title)}</a> <span class="badge">${esc(h.kind)}</span><div class="small muted">…${mk(h.ctx)}…</div></div>`).join("");
  }
  const notFound = () => { main.innerHTML = `<h1>Not found</h1><p><a href="#/home">Back to start</a></p>`; };

  function route() {
    const h = decodeURIComponent(location.hash || "#/home");
    document.body.classList.remove("open");
    let m;
    if ((m = h.match(/^#\/file\/(.+?)(?:\/(src)(?:\/([\d-]+))?)?$/))) file(m[1], m[2], m[3]);
    else if ((m = h.match(/^#\/lesson\/(.+)/))) lesson(m[1]);
    else if ((m = h.match(/^#\/area\/(.+)/))) area(m[1]);
    else if (h === "#/glossary") glossary();
    else if ((m = h.match(/^#\/search\/(.+)/))) search(m[1]);
    else home();
    buildSide(); progress();
    if (!(m && m[2] && m[3])) window.scrollTo(0, 0);
  }

  $("#menu").onclick = () => document.body.classList.toggle("open");
  $("#theme").onclick = () => {
    const el = document.documentElement, dark = getComputedStyle(el).getPropertyValue("--bg").trim() === "#10141c";
    el.dataset.theme = dark ? "light" : "dark";
  };
  let t;
  $("#q").oninput = e => { clearTimeout(t); const v = e.target.value.trim(); t = setTimeout(() => { if (v.length >= 2) location.hash = "#/search/" + encodeURIComponent(v); }, 250); };
  document.addEventListener("keydown", e => { if (e.key === "/" && document.activeElement.tagName !== "INPUT") { e.preventDefault(); $("#q").focus(); } });
  window.addEventListener("hashchange", route);
  route();
})();
