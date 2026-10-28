// The agents on Orchestra's pages (a plugin script; Orchestra.machineCards in Orchestra's
// app.js), inside Orchestra's machine-card frame:
// - a resident agent (the Supervisor, the Coordinator) is a row: its letter in its role's colour,
//   what it last said, and its session chip (resident, working, waiting);
// - workers and reviewers are the tickets they were assigned: a card per ticket an instance is on,
//   in the ticket's state colour, with its id, title, re-engagement badge and state, and a lane
//   with what the agent is saying now.
// The agents' host is no machine (it never announces itself or publishes as itself), so it has no
// card; whether it is up is on the Agents page and on each agent's session chip.
// A click opens the agent's transcript (the ticket id opens the ticket, when a task system with a
// ticket view is loaded); "machine details", on the Machines page, opens Orchestra's own view.
// Under the live cards, "finished sessions" lists the role's recent sessions that are over, whose
// instance has moved on, each a click from its transcript.
(async function () {
  if (!window.Orchestra || !Orchestra.machineCards) return;
  const API = "/api/plugins/agents";
  window.__agentsJs = window.__agentsJs || (typeof window.sessionChip === "function" ? Promise.resolve()   // transcripts, chat, chips
    : new Promise((ok) => { const s = document.createElement("script"); s.src = "/plugins/agents/static/agents.js"; s.onload = ok; s.onerror = ok; document.head.appendChild(s); }));
  await window.__agentsJs;
  let D = null, last = "";
  const e_ = (s) => esc(s);
  const ROLE_C = { supervisor: "var(--r-supervisor)", coordinator: "var(--r-coordinator)", worker: "var(--r-worker)", reviewer: "var(--r-reviewer)" };
  const RANK = { supervisor: 10, coordinator: 11, worker: 30, reviewer: 31 };
  const stateColor = (st) => (typeof color === "function" ? color(st) : "var(--muted)");
  const kindOf = (m) => (m.kinds && m.kinds.agent ? "agent" : m.kind);   // a machine with any agent instance is an agent here (worker: agent ×2, program ×1)
  const roleOf = (m) => (m.about && m.about.role) || m.id;
  const sessionsOf = (m) => (D ? D.sessions.filter((s) => s.role === roleOf(m)) : []);
  // what is known of the agent beyond its sessions, for its status (agents.js agentStatus)
  const agentOf = (m) => { const a = D && D.agents.find((x) => x.name === roleOf(m)); return a ? { ...a, host_alive: D.host.alive } : { host_alive: D ? D.host.alive : undefined }; };
  // a per-ticket role's machine shows the tickets it is on, even when a program fills the role beside the agents (its mode is then mixed)
  const perTicket = (m) => { const a = D && D.agents.find((x) => x.name === roleOf(m)); return a ? !a.resident : m.mode === "ad hoc"; };
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
    // the stored text is a message's tail, markdown and all: drop fence lines, emphasis markers and
    // backticks (the words stay), then start it at a sentence, not mid-word
    const tail = (s ? s.last_text || "" : "").replace(/^\s*```.*$/gm, "").replace(/\*\*|__|`|(^|\s)[*_](?=\S)|(?<=\S)[*_](?=\s|$)/g, "$1").trim(), cut = tail.search(/[.!?]\s+[A-Z(]/);
    const role = roleOf(m), say = tail.length >= 290 && cut >= 0 && cut < 160 ? tail.slice(cut + 1).trim() : tail;
    const a = agentOf(m), next = a.next_scheduled_at && !(s && s.status === "running") ? `next wake-up in ${ago(a.next_scheduled_at - D.now)}` : "";
    // with nothing said yet the status line already names the next wake-up
    const text = m.unseen ? "declared, not run yet" : say ? [say, next].filter(Boolean).join(" · ") : typeof agentStatus === "function" ? agentStatus(s, D.now, a).title : "";
    return `<div class="agent ${m.unseen ? "unseen-agent" : ""}" style="--rc:${ROLE_C[role] || "#c4b5fd"}" title="${e_(agentOf(m).description || role)}">
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
    return `<h2><span>${e_(m.id)} <span class="muted">· on tickets</span></span><span class="hctl"><span class="count">${live.length}</span></span></h2>${cards || empty}${compact ? "" : past(role)}${more(m, compact)}`;
  }

  // the sessions this role finished, newest first: an instance moves on to its next ticket (or is
  // gone), and its transcript stays a click away here and in the ticket's drawer
  function past(role) {
    const done = ((D && D.recent) || []).filter((x) => x.role === role);
    if (!done.length) return "";
    return `<details class="tkc-past" onclick="event.stopPropagation()"><summary>finished sessions (${done.length})</summary>${done.map((x) => `
      <div class="row" onclick="event.stopPropagation();AgentCards.transcript('${e_(x.session)}')" title="Open this session's transcript">
        <span class="id" onclick="event.stopPropagation();AgentCards.ticket('${e_(x.ticket)}')" title="Open the ticket">${e_(x.ticket)}</span>
        <span class="muted">${e_(x.instance)}</span><span>${e_(x.title || (x.last_text || "").replace(/\s+/g, " "))}</span>
        <span class="muted">${x.settled_at ? ago(D.now - x.settled_at) + " ago" : ""}</span></div>`).join("")}</details>`;
  }

  window.AgentCards = {
    transcript: (sid) => openTranscript(sid),
    ticket: (id) => (typeof window.openTicket === "function" ? window.openTicket(id) : null),
  };

  Orchestra.machineCards.register("agents", {
    rank: (m) => RANK[roleOf(m)] ?? (kindOf(m) === "agent" ? 12 : 25),
    // a few words for the Dashboard's machine list: what the machine is on now
    summary(m) {
      if (kindOf(m) !== "agent") return null;
      const live = sessionsOf(m).filter((s) => s.status === "running");
      if (live.length) return live.map((s) => `${e_(s.instance || roleOf(m))} on ${e_((s.ticket || {}).id || "a turn")}`).join(" · ") + " " + chip(live[0], m);
      const s = sessionsOf(m).sort((a, b) => (b.settled_at || 0) - (a.settled_at || 0))[0];
      return s || m.mode === "ad hoc" ? chip(s || null, m) : null;
    },
    card(m, { compact } = {}) {
      if (kindOf(m) !== "agent") return null;
      return perTicket(m) ? ticketsCard(m, compact) : residentCard(m, compact);
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
      const sig = JSON.stringify([d.sessions.map((s) => [s.session, s.status, s.phase, s.last_text, s.tool_name]), (d.recent || []).map((x) => x.session), d.host.alive]);
      D = d;
      if (force || sig !== last) { last = sig; Orchestra.machineCards.changed(); }
    } catch (e) {}
  }
  refresh(true); setInterval(refresh, 3000);
})();
