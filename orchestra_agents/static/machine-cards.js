// The agents on Orchestra's pages (a plugin script; Orchestra.machineCards in Orchestra's
// app.js), inside Orchestra's machine-card frame:
// - a resident agent (the Supervisor, the Coordinator) is a row: its letter in its role's colour,
//   what it last said, and its session chip (resident, working, waiting);
// - workers and reviewers are the tickets they were assigned: a card per ticket an instance is on,
//   in the ticket's state colour, with its id, title, re-engagement badge and state, and a lane
//   with what the agent is saying now;
// - the agents' host is a line: alive or not, and which product runs the agents.
// A click opens the agent's transcript (the ticket id opens the ticket, when a task system with a
// ticket view is loaded); "machine details", on the Machines page, opens Orchestra's own view.
(async function () {
  if (!window.Orchestra || !Orchestra.machineCards) return;
  const API = "/api/plugins/agents";
  if (!document.querySelector('link[href^="/plugins/agents/static/agents.css"]')) {      // the agents' look
    const l = document.createElement("link"); l.rel = "stylesheet"; l.href = "/plugins/agents/static/agents.css"; document.head.appendChild(l);
  }
  window.__agentsJs = window.__agentsJs || (typeof window.sessionChip === "function" ? Promise.resolve()   // transcripts, chat, chips
    : new Promise((ok) => { const s = document.createElement("script"); s.src = "/plugins/agents/static/agents.js"; s.onload = ok; s.onerror = ok; document.head.appendChild(s); }));
  await window.__agentsJs;
  let D = null, last = "";
  const e_ = (s) => esc(s);
  const ROLE_C = { supervisor: "var(--r-supervisor)", coordinator: "var(--r-coordinator)", worker: "var(--r-worker)", reviewer: "var(--r-reviewer)" };
  const RANK = { supervisor: 10, coordinator: 11, agents: 15, worker: 30, reviewer: 31 };
  const stateColor = (st) => (typeof color === "function" ? color(st) : "var(--muted)");
  const kindOf = (m) => (m.about && m.about.kind) || (m.declared && m.declared.kind);
  const roleOf = (m) => (m.about && m.about.role) || m.id;
  const sessionsOf = (m) => (D ? D.sessions.filter((s) => s.role === roleOf(m)) : []);
  // what is known of the agent beyond its sessions, for its status (agents.js agentStatus)
  const agentOf = (m) => { const a = D && D.agents.find((x) => x.name === roleOf(m)); return a ? { ...a, host_alive: D.host.alive } : { host_alive: D ? D.host.alive : undefined }; };
  const chip = (s, m) => (typeof sessionChip === "function" ? sessionChip(s, D ? D.now : Date.now() / 1000, m ? agentOf(m) : undefined) : "");
  const more = (m, compact) => (compact ? "" : `<div class="tkc-more" onclick="event.stopPropagation();Orchestra.machineCards.openDefault('${e_(m.id)}')">machine details</div>`);

  const css = document.createElement("style");
  css.textContent = `
    .tkc-more { text-align:right; font-size:10.5px; color:var(--muted); text-decoration:underline dotted; cursor:pointer; margin:6px 2px 0 }
    .agent.unseen-agent { opacity:.6; border-style:dashed }`;
  document.head.appendChild(css);

  // how many times a worker has taken the ticket (the assignment's brief says; the ticket says why)
  function turns(t) {
    if (!t || !t.worker_turns || t.worker_turns < 2) return "";
    return `<span class="turns" title="a worker took this ticket ${t.worker_turns} times; the ticket says why">↺${t.worker_turns}</span>`;
  }
  function residentCard(m, compact) {
    const s = sessionsOf(m).sort((a, b) => (b.started_at || 0) - (a.started_at || 0))[0];
    // the stored text is a message's tail: start it at a sentence, not mid-word
    const tail = s ? s.last_text || "" : "", cut = tail.search(/[.!?]\s+[A-Z*`(]/);
    const role = roleOf(m), say = tail.length >= 290 && cut >= 0 && cut < 160 ? tail.slice(cut + 1).trim() : tail;
    const a = agentOf(m), next = a.next_scheduled_at && !(s && s.status === "running") ? `next wake-up in ${ago(a.next_scheduled_at - D.now)}` : "";
    // with nothing said yet the status line already names the next wake-up
    const text = m.unseen ? "declared, not run yet" : say ? [say, next].filter(Boolean).join(" · ") : typeof agentStatus === "function" ? agentStatus(s, D.now, a).title : "";
    return `<div class="agent ${m.unseen ? "unseen-agent" : ""}" style="--rc:${ROLE_C[role] || "#c4b5fd"}" title="${e_((m.declared || {}).description || role)}">
        <div class="av">${e_(m.id[0].toUpperCase())}</div><div class="name">${e_(m.id)}</div><div class="say">${e_(text)}</div>${s ? chip(s, m) : ""}</div>${more(m, compact)}`;
  }

  function ticketsCard(m, compact) {
    const role = roleOf(m), live = sessionsOf(m).filter((s) => s.status === "running");
    const cards = live.map((s) => {
      const t = s.ticket || { id: s.session, title: "", state: "IN_PROGRESS" };
      return `<div class="awork" style="--c:${stateColor(t.state)}" onclick="event.stopPropagation();AgentCards.transcript('${e_(s.session)}')" title="Open the running agent's transcript; the ticket id opens the ticket">
        <div class="top"><span><span class="id" onclick="event.stopPropagation();AgentCards.ticket('${e_(t.id)}')" title="Open the ticket">${e_(t.id)}</span> <span class="title">${e_(t.title)}</span></span>
        <span class="right">${turns(t)}<span class="pill">${e_(t.state.replace(/_/g, " "))}</span></span></div>
        <div class="lane"><span class="who">${e_(s.instance || role)}</span><span class="msg">${e_(s.last_text || "")}</span>${chip(s)}</div></div>`;
    }).join("");
    const empty = m.unseen ? `<div class="empty">Declared, not run yet: ${e_(role)}s start when a ticket is ready${role === "reviewer" ? " for review" : ""}.</div>`
      : `<div class="empty">No ${e_(role)} is on a ticket right now.${role === "worker" ? " Ready tickets start as soon as a worker slot is free." : ""}</div>`;
    return `<h2><span>${e_(m.id)} <span class="muted">· on tickets</span></span><span class="hctl"><span class="count">${live.length}</span></span></h2>${cards || empty}${more(m, compact)}`;
  }

  function hostCard(m, compact) {
    const h = (D && D.host) || {};
    const what = !D ? "reading…" : h.alive ? `running ${e_(D.harness || "")}${D.model ? " · " + e_(D.model) : ""} · fills ${e_((D.serves || []).join(", ") || "no task roles")}`
      : m.unseen ? "declared, not run yet (orchestra-agents run)" : "not running";
    return `<div class="agent ${h.alive ? "" : "unseen-agent"}" style="--rc:#94a3b8"><div class="av">A</div><div class="name">agents</div><div class="say">${what}</div></div>${more(m, compact)}`;
  }

  window.AgentCards = {
    transcript: (sid) => openTranscript(sid),
    ticket: (id) => (typeof window.openTicket === "function" ? window.openTicket(id) : null),
  };

  Orchestra.machineCards.register("agents", {
    rank: (m) => RANK[roleOf(m)] ?? (kindOf(m) === "agent" ? 12 : 25),
    // a few words for the Dashboard's machine list: what the machine is on now
    summary(m) {
      if (m.id === "agents") return D ? (D.host.alive ? `running · fills ${e_((D.serves || []).join(", ") || "no task roles")}` : "not running") : null;
      if (kindOf(m) !== "agent") return null;
      const live = sessionsOf(m).filter((s) => s.status === "running");
      if (live.length) return live.map((s) => `${e_(s.instance || roleOf(m))} on ${e_((s.ticket || {}).id || "a turn")}`).join(" · ") + " " + chip(live[0], m);
      const s = sessionsOf(m).sort((a, b) => (b.settled_at || 0) - (a.settled_at || 0))[0];
      return s || m.mode === "ad hoc" ? chip(s || null, m) : null;
    },
    card(m, { compact } = {}) {
      if (m.id === "agents") return hostCard(m, compact);
      if (kindOf(m) !== "agent") return null;
      return m.mode === "ad hoc" ? ticketsCard(m, compact) : residentCard(m, compact);
    },
    open(m) {
      if (kindOf(m) !== "agent" || m.unseen) return false;
      const s = sessionsOf(m).filter((x) => x.status === "running")[0] || sessionsOf(m).sort((a, b) => (b.started_at || 0) - (a.started_at || 0))[0];
      if (!s) return false;
      openTranscript(s.session);
      return true;
    },
  });

  async function refresh(force) {
    try {
      const d = await (await fetch(API + "/agents")).json();
      if (!d.ok) return;
      const sig = JSON.stringify([d.sessions.map((s) => [s.session, s.status, s.phase, s.last_text, s.tool_name]), d.host.alive]);
      D = d;
      if (force || sig !== last) { last = sig; Orchestra.machineCards.changed(); }
    } catch (e) {}
  }
  refresh(true); setInterval(refresh, 3000);
})();
