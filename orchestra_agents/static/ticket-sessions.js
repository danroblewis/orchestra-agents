// The agents' sessions in the task system's ticket drawer (a plugin script, on every page). The
// task system's tasks.js announces each drawer it opens with "tasks:ticket-drawer" on document,
// detail {id, ticket, mount}: `mount` is an empty element in the drawer for other packages. This
// fills it with every agent turn that worked on the ticket (GET /sessions?ticket=: the worker's
// turns, each review round, and the Coordinator's, the Supervisor's or another agent's turns about
// it), each with its role, instance, when, outcome and last words, and its transcript a click
// away: inline, one turn read at a glance (agents.js renderTurn), or the whole session. While the
// drawer is open it follows the ticket every few seconds, so a running turn shows as it goes.
// The two packages import nothing of each other; without the task system this never runs.
(function () {
  const API = "/api/plugins/agents";
  const POLL_MS = 5000;
  const ROLE_C = { supervisor: "var(--r-supervisor)", coordinator: "var(--r-coordinator)", worker: "var(--r-worker)", reviewer: "var(--r-reviewer)" };
  const OUT_C = { APPROVED: "#22c55e", DONE: "#22c55e", READY_FOR_REVIEW: "#a78bfa", REJECTED: "#f87171", BLOCKED: "#fb923c", running: "#60a5fa",
                  "cut off": "#fbbf24", failed: "#f87171", interrupted: "#f87171", "no report": "#9ca3af", "no verdict": "#9ca3af" };
  const libs = () => (window.__agentsJs = window.__agentsJs || (typeof window.renderTurn === "function" ? Promise.resolve()
    : new Promise((ok) => { const s = document.createElement("script"); s.src = "/plugins/agents/static/agents.js"; s.onload = ok; s.onerror = ok; document.head.appendChild(s); })));
  let cur = null;   // {id, mount, sig, open: Set of "session:start", timer}

  const key = (t) => `${t.session}:${t.start}`;
  const clock = (t) => (t ? new Date(t * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" }) : "");
  const dur = (s) => (s == null ? "" : s >= 3600 ? `${Math.floor(s / 3600)}h ${Math.round((s % 3600) / 60)}m` : s >= 60 ? `${Math.floor(s / 60)}m ${Math.round(s % 60)}s` : `${Math.max(0, Math.round(s))}s`);

  // what a turn was, in a few words: a worker's turn, a review round, or another agent's turn about the ticket
  function what(t, rounds) {
    if (t.own && t.role === "reviewer") return `review round ${rounds[key(t)]}`;
    if (t.own && t.kind === "assignment") return `turn ${rounds[key(t)]}`;
    if (t.own) return t.session.includes("-verdict-") ? "prediction verdict" : t.kind;
    const why = t.kind === "wake" ? "woken" : t.kind === "chat" ? "chat with you" : t.kind;
    return `${why}${t.touched.length ? " · " + t.touched.join(", ") : " · mentions it"}`;
  }

  function row(t, now, rounds) {
    const ended = t.running ? `running ${dur(now - t.started)}` : `${clock(t.started)}${t.ended && t.started ? ` · ${dur(t.ended - t.started)}` : ""}`;
    const open = cur.open.has(key(t));
    const say = (t.reply || "").replace(/\s+/g, " ").trim();
    return `<div class="tks-row" style="--rc:${ROLE_C[t.role] || "#c4b5fd"}" data-k="${esc(key(t))}">
      <div class="tks-top"><span class="who">${esc(t.instance || t.role)}</span><span class="what">${esc(what(t, rounds))}</span>
        <span class="when" title="${t.started ? esc(new Date(t.started * 1000).toLocaleString()) : ""}">${esc(ended)}${t.archived ? ' <span class="muted">· session archived</span>' : ""}</span>
        <span class="out ${t.running ? "running" : ""}" style="--oc:${OUT_C[t.outcome] || "#9ca3af"}">${esc(String(t.outcome).replace(/_/g, " "))}</span>
        <span class="acts"><button onclick="TicketSessions.toggle('${esc(key(t))}')">${open ? "hide" : "transcript"}</button><button title="the whole session (every turn); its ticket link comes back here" onclick="TicketSessions.session('${esc(t.session)}')">session ↗</button></span></div>
      ${say ? `<div class="say" title="${esc(say.slice(0, 600))}">${esc(say.slice(0, 240))}</div>` : ""}
      ${open ? `<div class="tks-turn" id="tks-${esc(key(t))}"><div class="empty">loading…</div></div>` : ""}</div>`;
  }

  async function load() {
    const c = cur;
    if (!c) return;
    if (!document.body.contains(c.mount)) { stop(); return; }
    let d;
    try { d = await (await fetch(`${API}/sessions?ticket=${encodeURIComponent(c.id)}`)).json(); } catch (e) { return; }
    if (cur !== c || !d.ok) return;
    c.turns = d.turns;
    const sig = JSON.stringify([d.turns.map((t) => [key(t), t.outcome, t.end, t.reply.length, t.running]), [...c.open]]);
    const running = d.turns.some((t) => t.running);
    if (sig === c.sig && !running) return;
    const first = c.sig === undefined;
    c.sig = sig;
    const rounds = {}, n = {};
    d.turns.filter((t) => t.own).forEach((t) => { n[t.session] = (n[t.session] || 0) + 1; rounds[key(t)] = n[t.session]; });
    const live = d.turns.filter((t) => t.running).length;
    c.mount.innerHTML = `<div class="tks"><div class="tks-head"><h2 style="margin:0">Agent sessions</h2><span class="muted">${d.turns.length
      ? `${d.turns.length} turn${d.turns.length === 1 ? "" : "s"}${live ? ` · ${live} running, following it live` : ""}` : ""}</span></div>
      ${d.turns.map((t) => row(t, d.now, rounds)).join("") || `<div class="empty">No agent has worked on this ticket${first ? "" : " yet"}. A program may have (the Machines page shows who took it).</div>`}</div>`;
    for (const k of c.open) fill(k);
  }

  async function fill(k) {
    const c = cur, t = c && (c.turns || []).find((x) => key(x) === k);
    const box = document.getElementById(`tks-${k}`);
    if (!t || !box) return;
    const q = t.running ? `start=${t.start}` : `start=${t.start}&end=${t.end}`;
    try {
      const d = await (await fetch(`${API}/sessions/${encodeURIComponent(t.session)}/messages?${q}`)).json();
      if (!document.getElementById(`tks-${k}`)) return;
      const opened = [...box.querySelectorAll("details[open]")].map((x) => x.querySelector("summary").textContent);
      box.innerHTML = renderTurn(d.messages || [], d.markers || []);
      box.querySelectorAll("details").forEach((x) => { if (opened.includes(x.querySelector("summary").textContent)) x.open = true; });
      if (typeof highlightCode === "function") highlightCode(box);
    } catch (e) { box.innerHTML = `<div class="bad">${esc(String(e))}</div>`; }
  }

  function stop() { if (cur) clearInterval(cur.timer); cur = null; }

  window.TicketSessions = {
    toggle(k) { if (!cur) return; cur.open.has(k) ? cur.open.delete(k) : cur.open.add(k); cur.sig = null; load(); },
    session(sid) { openTranscript(sid); },
  };

  document.addEventListener("tasks:ticket-drawer", async (e) => {
    const { id, mount } = e.detail || {};
    if (!id || !mount) return;
    stop();
    cur = { id, mount, open: new Set(), sig: undefined };
    mount.innerHTML = '<div class="tks"><h2>Agent sessions</h2><div class="empty">loading…</div></div>';
    await libs();
    if (!cur || cur.mount !== mount) return;
    cur.timer = setInterval(load, POLL_MS);
    load();
  });
})();
