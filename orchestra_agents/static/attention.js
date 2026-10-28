// What waits on the human, on every page of Orchestra's web UI (a plugin script: Orchestra loads it
// and knows nothing of it). Blocked tickets and Supervisor messages not yet read become:
// - a "Supervisor" button in the navigation, with a count when something waits;
// - a banner under the navigation that says what waits, with a way to answer;
// - "(n)" in the tab's title.
// The button opens the Supervisor's chat (agents.js) with the list of what waits above it, and the
// message it announced marked in the transcript (or, when the transcript does not hold it, shown
// in full above it). A message is only what the Supervisor wrote for the human (the agents'
// attention.py: a reply to the human, or a wake-up's reply that says something); one that arrives
// while the chat is open in front of the human is read there and announces nothing. The
// Supervisor is the human's one way in: blocked work is answered by telling it what to do. The
// task system's own banner (its attention.js) shows the same blocked tickets; it is asked to stand
// aside once it is on the page, so there is one banner.
(function () {
  const API = "/api/plugins/agents", TASKS = "/api/plugins/tasks";      // blocked tickets: the task system's, when it is installed
  const POLL_MS = 5000;
  const load = (src) => new Promise((ok, bad) => { const s = document.createElement("script"); s.src = src; s.onload = ok; s.onerror = bad; document.head.appendChild(s); });

  // what the chat drawer needs: agents.js (transcripts, the chat, markdown rendering); the agents'
  // look, agents.css, is a stylesheet the plugin declares, linked by Orchestra in every page's head
  async function ready() {
    window.__agentsJs = window.__agentsJs || (typeof window.openTranscript === "function" ? Promise.resolve() : load("/plugins/agents/static/agents.js").catch(() => {}));
    await window.__agentsJs;                         // loaded once, whichever of the agents' scripts asks first
    if (typeof agentsLibs !== "undefined") await agentsLibs;   // agents.js's markdown libraries
  }

  const css = document.createElement("style");
  css.textContent = `
    .att-btn { display:inline-flex; align-items:center; gap:6px; margin-left:14px; padding:3px 10px; border-radius:999px;
      border:1px solid var(--border2, #2a3142); background:transparent; color:var(--text, #e6e8ee); font:inherit; font-size:12px; cursor:pointer }
    .att-btn:hover { border-color:#6ea8fe }
    .att-btn .att-n { min-width:18px; height:18px; padding:0 5px; border-radius:9px; background:#f59e0b; color:#1a1205;
      font-weight:700; font-size:11px; display:none; align-items:center; justify-content:center }
    .att-btn.on { border-color:#f59e0b; box-shadow:0 0 0 0 rgba(245,158,11,.6); animation:att-pulse 2s infinite }
    .att-btn.count .att-n { display:inline-flex }
    .att-btn .att-dot { width:7px; height:7px; border-radius:50%; background:#6ea8fe; display:none }
    .att-btn.working .att-dot { display:inline-block; animation:att-blink 1s infinite alternate }
    @keyframes att-pulse { 0% { box-shadow:0 0 0 0 rgba(245,158,11,.55) } 70% { box-shadow:0 0 0 8px rgba(245,158,11,0) } 100% { box-shadow:0 0 0 0 rgba(245,158,11,0) } }
    @keyframes att-blink { from { opacity:.25 } to { opacity:1 } }
    .att-bar { display:none; align-items:center; gap:12px; padding:8px 16px; background:linear-gradient(90deg, rgba(245,158,11,.16), rgba(245,158,11,.05));
      border-bottom:1px solid rgba(245,158,11,.45); color:var(--text, #e6e8ee); font-size:13px }
    .att-bar.on { display:flex }
    .att-bar b { color:#fbbf24 }
    .att-bar .att-what { flex:1; min-width:0; overflow:hidden; text-overflow:ellipsis; white-space:nowrap }
    .att-bar button { flex:none; padding:4px 12px; border-radius:6px; border:1px solid #f59e0b; background:#f59e0b; color:#1a1205; font-weight:600; cursor:pointer }
    .att-bar .att-x { background:transparent; border-color:transparent; color:var(--muted, #8a90a3); font-weight:400; padding:4px 6px }
    .att-bar button.att-down, .att-item button.att-down { background:transparent; border-color:var(--border2, #2a3142); color:var(--muted, #8a90a3); font-weight:400 }
    .att-panel { border:1px solid rgba(245,158,11,.5); background:rgba(245,158,11,.07); border-radius:8px; padding:12px 14px; margin:4px 0 14px }
    .att-panel h4 { margin:0 0 8px; color:#fbbf24; font-size:12px; letter-spacing:.06em; text-transform:uppercase }
    .att-item { display:grid; grid-template-columns:1fr auto; gap:4px 12px; padding:8px 0; border-top:1px solid rgba(245,158,11,.2) }
    .att-item:first-of-type { border-top:none }
    .att-item .att-t { font-weight:600 } .att-item .att-t code { color:#fbbf24 }
    .att-item .att-r { grid-column:1; color:var(--muted, #8a90a3); font-size:12px; line-height:1.45; max-height:5.8em; overflow:hidden }
    .att-item .att-acts { grid-column:2; grid-row:1 / span 2; display:flex; flex-direction:column; gap:6px; align-items:stretch }
    .att-item button { padding:3px 10px; border-radius:6px; border:1px solid var(--border2, #2a3142); background:var(--panel2, #151a24); color:var(--text, #e6e8ee); cursor:pointer; font-size:12px }
    .att-item button.primary { border-color:#f59e0b; color:#fbbf24 }
    .att-item .att-full { grid-column:1; font-size:13px } .att-item .att-r a { color:#fbbf24 }
    #transcript .msg.for-you { border-left-color:#f59e0b; background:rgba(245,158,11,.07) }
    #transcript .msg.for-you > .r::after { content:" · for you"; color:#fbbf24; font-weight:600 }
    @media (max-width:640px) {
      .att-bar .att-what { white-space:normal } .att-item { grid-template-columns:1fr } .att-item .att-acts { grid-column:1; grid-row:auto; flex-direction:row }
      /* the navigation is one line that does not wrap: the button shrinks to its icon and count and goes first, the project name goes, and the line scrolls sideways */
      .att-btn { order:-1; margin:0 6px 0 0; padding:3px 7px } .att-btn .att-word { display:none } nav #proj { display:none }
      #nav { overflow-x:auto; scrollbar-width:none } #nav::-webkit-scrollbar { display:none } }`;
  document.head.appendChild(css);

  let A = { blocked: [], message: null, count: 0, working: false, host: { alive: true } };   // blocked: the task system's [{id, reason}]
  let shown = null;                         // the message the open chat was opened for: its panel item stays while the chat is open
  let dismissed = "";                       // the banner's content when the viewer closed it
  let rendered = "";                        // the banner's content as last built: a click never lands on a replaced button
  const baseTitle = document.title;
  const hostUp = () => !A.host || A.host.alive !== false;
  const sig = () => JSON.stringify([A.blocked.map((b) => b.id), A.message && A.message.at, hostUp()]);
  const ago_ = (t) => (typeof ago === "function" && t ? ago(Date.now() / 1000 - t) : "");
  const e_ = (s) => (typeof esc === "function" ? esc(s) : String(s ?? ""));
  // With the host down nothing can be answered: the way in still opens the chat, which says so, but does not promise an answer.
  const DOWN = "the Supervisor is down · start orchestra run";
  const answerBtn = (onclick, label) => (hostUp() ? `<button class="primary" onclick="${onclick}">${label}</button>`
    : `<button class="att-down" onclick="window.SupervisorAttention.open()">${DOWN}</button>`);

  // the navigation button
  const btn = document.createElement("button");
  btn.className = "att-btn"; btn.type = "button";
  btn.innerHTML = '<span>💬<span class="att-word"> Supervisor</span></span><span class="att-dot" title="the Supervisor is working"></span><span class="att-n"></span>';
  btn.onclick = () => openSupervisor();
  const nav = document.getElementById("nav");
  if (nav) nav.insertBefore(btn, document.getElementById("proj"));

  // the banner, right under the navigation
  const bar = document.createElement("div");
  bar.className = "att-bar";
  if (nav) nav.insertAdjacentElement("afterend", bar);

  function render() {
    btn.classList.toggle("count", A.count > 0);
    btn.classList.toggle("on", A.count > 0 && hostUp());        // the pulse asks for an answer; with the host down there is none to give
    btn.classList.toggle("working", !!A.working && hostUp());
    btn.querySelector(".att-n").textContent = A.count || "";
    btn.title = A.count ? `${A.count} thing${A.count === 1 ? "" : "s"} waiting on you${hostUp() ? "" : " (" + DOWN + ")"}` : hostUp() ? "Talk to the Supervisor" : DOWN;
    document.title = (A.count ? `(${A.count}) ` : "") + baseTitle;
    const parts = [];
    if (A.blocked.length) parts.push(`<b>${A.blocked.length} ticket${A.blocked.length === 1 ? " is" : "s are"} blocked</b> waiting for a decision (${A.blocked.slice(0, 3).map((b) => e_(b.id)).join(", ")}${A.blocked.length > 3 ? ", …" : ""})`);
    if (A.message) parts.push(`<b>the Supervisor wrote to you</b>${A.message.count > 1 ? ` (${A.message.count} messages)` : ""} ${ago_(A.message.at)} ago`);
    const show = parts.length && sig() !== dismissed;
    bar.classList.toggle("on", !!show);
    if (show && sig() !== rendered) {
      rendered = sig();
      bar.innerHTML = `<span>⚠</span><span class="att-what">${parts.join(" · ")}</span>
      <button class="${hostUp() ? "" : "att-down"}" onclick="window.SupervisorAttention.open()">${hostUp() ? "Review &amp; answer" : DOWN}</button><button class="att-x" title="hide until something changes" onclick="window.SupervisorAttention.dismiss()">✕</button>`;
    }
    const panel = document.getElementById("att-panel");
    if (panel) panel.outerHTML = panelHtml();
  }

  function panelHtml() {
    const msg = shown || A.message;
    if (!A.blocked.length && !msg) return '<div id="att-panel"></div>';
    const items = A.blocked.map((b) => `<div class="att-item">
        <div class="att-t"><code>${e_(b.id)}</code> <span class="muted" style="font-weight:400">· blocked</span></div>
        <div class="att-r">${e_(b.reason) || "(no reason recorded)"}</div>
        <div class="att-acts">${answerBtn(`window.SupervisorAttention.answer('${e_(b.id)}')`, "Answer")}
          <button onclick="window.openTicket ? openTicket('${e_(b.id)}') : (location.href='/tickets#ticket=${e_(b.id)}')">Ticket</button></div></div>`);
    if (msg) {
      const woke = msg.woken_by && msg.woken_by.length ? `<div class="att-r">An event woke it, not you: ${e_(msg.woken_by[0].slice(0, 240))}${msg.woken_by[0].length > 240 ? "…" : ""}</div>` : "";
      const where = msg.in_chat === false ? `<div class="att-full">${typeof md === "function" ? md(msg.text) : e_(msg.text)}</div>
          <div class="att-r">Its transcript does not hold this message (the turn was cut off, or the transcript moved on), so it is shown here.</div>`
        : `<div class="att-r">${e_(msg.text.slice(0, 300))}${msg.text.length > 300 ? "…" : ""}</div>
           <div class="att-r"><a href="#" onclick="window.SupervisorAttention.show();return false">marked in the transcript below ↓</a></div>`;
      items.unshift(`<div class="att-item">
        <div class="att-t">The Supervisor wrote to you <span class="muted" style="font-weight:400">· ${ago_(msg.at)} ago${msg.count > 1 ? ` · ${msg.count} messages since you last looked` : ""}</span></div>
        ${woke}${where}
        <div class="att-acts">${answerBtn("document.getElementById('chat-input')?.focus()", "Reply")}</div></div>`);
    }
    return `<div id="att-panel" class="att-panel"><h4>Waiting on you</h4>${items.join("")}
      ${A.blocked.length ? '<div class="muted" style="font-size:11px;margin-top:6px">Answer by telling the Supervisor what to do; it unblocks, changes or cancels the ticket.</div>' : ""}</div>`;
  }

  async function refresh() {
    try {
      const d = await (await fetch(API + "/attention")).json();
      const t = await fetch(TASKS + "/attention").then((r) => (r.ok ? r.json() : {})).catch(() => ({}));
      d.blocked = t.blocked || [];
      // the chat is open in front of the human: what the Supervisor writes there now is read there
      if (d.message && chatOpen()) { const m = d.message; await markSeen(); d.message = null; setTimeout(() => mark(m), POLL_MS + 500); }
      d.count = (d.message ? 1 : 0) + d.blocked.length;
      if (d.ok) { A = d; render(); }
    } catch (e) { /* the task system is down: keep the last state */ }
  }

  async function markSeen() {
    try { await fetch(API + "/attention/seen", { method: "POST" }); } catch (e) {}
  }
  const chatOpen = () => document.visibilityState === "visible" && !!document.getElementById("chat")
    && !!(document.getElementById("overlay") || {}).classList?.contains("open");
  // the announced message, in the open transcript: marked "for you" and brought into view
  function mark(m, scroll) {
    if (!m || m.index == null) return false;
    const el = document.querySelector(`#transcript [data-i="${m.index}"]`);
    if (!el) return false;
    el.classList.add("for-you");
    if (scroll) el.scrollIntoView({ block: "center" });
    return true;
  }

  // the Supervisor's chat, with what waits listed above the message box
  async function openSupervisor(prefill) {
    if (typeof window.openDrawer === "function") openDrawer('<h3>Supervisor</h3><div class="empty">opening the Supervisor…</div>');   // the scripts take a moment
    await ready();
    await openTranscript("supervisor");
    const chat = document.getElementById("chat"), head = document.getElementById("transcript-head");
    if (!document.getElementById("att-panel")) {
      if (chat) chat.insertAdjacentHTML("beforebegin", panelHtml());
      else if (head) head.insertAdjacentHTML("afterend", panelHtml());
    }
    if (prefill) {
      const ta = document.getElementById("chat-input");
      if (ta && !ta.disabled) { ta.value = prefill; ta.focus(); ta.setSelectionRange(ta.value.length, ta.value.length); }
    }
    // reading the chat reads the message; blocked tickets stay until they are unblocked
    shown = A.message;
    const panel = document.getElementById("att-panel"); if (panel) panel.outerHTML = panelHtml();
    if (A.message) { mark(A.message); await markSeen(); A.message = null; A.count = A.blocked.length; render(); }
  }

  // after the human writes, the Supervisor's reply arrives in the open chat: it is read
  const wrapSend = () => {
    if (typeof window.sendToSupervisor !== "function" || window.sendToSupervisor.__att) return;
    const orig = window.sendToSupervisor;
    window.sendToSupervisor = async function (ev) { const r = await orig(ev); await markSeen(); refresh(); return r; };
    window.sendToSupervisor.__att = true;
  };

  window.SupervisorAttention = {
    open: () => openSupervisor(),
    show: () => mark(shown, true),
    dismiss: () => { dismissed = sig(); render(); },
    answer: (id) => openSupervisor(`About ${id}, which is blocked: `),
  };

  const aside = () => { const t = window.TaskAttention; if (!t || !t.standAside) return false; t.standAside(); return true; };
  if (!aside()) { const t = setInterval(() => { if (aside()) clearInterval(t); }, 500); setTimeout(() => clearInterval(t), 30000); }

  refresh();
  setInterval(() => { wrapSend(); refresh(); }, POLL_MS);
  setTimeout(wrapSend, 500);
})();
