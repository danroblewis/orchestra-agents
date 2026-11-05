// The agents' shared page code: session transcripts (paged, live), the Supervisor chat, session
// chips and phases. Loaded after Orchestra's /static/app.js by the Agents page and by the agents'
// every-page scripts. Its names do not clash with the task system's tasks.js (which has the ticket
// drawers): a page may load both, and each calls the other's functions only when they are there.
const AGENTS_API = "/api/plugins/agents";

// Agent prose and human input are markdown: marked renders it, DOMPurify sanitizes it, highlight.js
// colours the code blocks. They are vendored here (static/vendor, README.md there: the pinned
// releases, unchanged), never fetched from a CDN, so the page works behind a proxy that blocks
// one; each is loaded once by whichever page loads this file; until they arrive md() shows the
// plain text. `agentsLibs` settles when they have loaded or failed.
function agentsLoad(src) {
  return new Promise((ok, bad) => { const s = document.createElement("script"); s.src = src; s.onload = ok; s.onerror = bad; document.head.appendChild(s); });
}
const AGENTS_VENDOR = "/plugins/agents/static/vendor";
if (!document.querySelector(`link[href^="${AGENTS_VENDOR}/github-dark"]`)) {
  const l = document.createElement("link"); l.rel = "stylesheet"; l.href = `${AGENTS_VENDOR}/github-dark.min.css`; document.head.appendChild(l);
}
const agentsLibs = Promise.all([
  window.marked || agentsLoad(`${AGENTS_VENDOR}/marked.min.js`).catch(() => {}),
  window.DOMPurify || agentsLoad(`${AGENTS_VENDOR}/purify.min.js`).catch(() => {}),
  window.hljs || agentsLoad(`${AGENTS_VENDOR}/highlight.min.js`).catch(() => {}),
]);

function md(text) {
  if (!text) return "";
  if (!(window.marked && window.DOMPurify)) return `<pre>${esc(text)}</pre>`;
  return `<div class="md">${DOMPurify.sanitize(marked.parse(text, { gfm: true, breaks: true }))}</div>`;
}
// Highlights code blocks, guessing the language when the fence does not name one.
function highlightCode(root) {
  if (!window.hljs) return;
  root.querySelectorAll("pre code").forEach((el) => {
    if (el.dataset.highlighted) return;
    const named = [...el.classList].find((c) => c.startsWith("language-"));
    if (named && hljs.getLanguage(named.slice(9))) hljs.highlightElement(el);
    else {
      const r = hljs.highlightAuto(el.textContent);
      el.innerHTML = r.value; el.classList.add("hljs", `language-${r.language || "plaintext"}`);
      el.dataset.highlighted = "yes";
    }
  });
}

// opening any drawer ends the paging of a transcript that was open in it, and highlights its code
const orchestraOpenDrawer = openDrawer;
openDrawer = function (html) { transcriptPaging = null; orchestraOpenDrawer(html); highlightCode($("drawer")); };   // eslint-disable-line no-func-assign

let transcriptPaging = null;   // {id, nextBefore, hasMore, loading}

async function openTranscript(id) {
  const r = await fetch(`${AGENTS_API}/sessions/${id}/transcript?limit=30`);
  if (!r.ok) {
    const perTicket = /^(worker|reviewer)-/.test(id);
    return openDrawer(`<h3>${esc(id)}</h3><div class="empty">${perTicket
      ? "No record of this session (" + esc(id) + "): it never ran, or it was removed."
      : "This session has not been started. It comes up with the agents' host, which <code>orchestra run</code> starts."}</div>`);
  }
  const d = await r.json(); const s = d.session;
  const perTicket = /^(worker|reviewer)-/.test(id);
  let html = `<div id="transcript-head">${transcriptHead(id, s, d)}</div>`;
  if (id === "supervisor") html += `<form id="chat" class="chat top" onsubmit="return sendToSupervisor(event)">
      <textarea id="chat-input" rows="3" placeholder="Message the Supervisor… (Enter to send, Shift+Enter for a new line)" ${s.alive ? "" : "disabled"}></textarea>
      <button type="submit" ${s.alive ? "" : "disabled"}>Send</button>
      ${s.alive ? "" : '<div class="empty">The Supervisor is reachable while the agents&#39; host is up (<code>orchestra run</code> starts it).</div>'}
    </form><div id="pending">${renderPending(d.pending, s)}</div><div id="live"></div>`;
  if (d.started_with) html += `<details class="started"><summary>The prompt this session was started with <span class="muted">(${d.started_with.length.toLocaleString()} chars; roles, team responsibilities, where it works)</span></summary><div class="prompt md">${md(d.started_with)}</div></details>`;
  else if (!perTicket) html += `<div class="empty">The prompt file this session was started with is missing: the agents' state directory was cleaned since. The agent's current prompt is on the Agents page.</div>`;
  html += `<h2>Transcript · newest first</h2>${id === "supervisor" ? `<label class="convo-only muted"><input type="checkbox" id="convo-only" onchange="setConversationOnly(this.checked)" ${conversationOnly() ? "checked" : ""}> Only the conversation</label>` : ""}<div id="transcript"${id === "supervisor" && conversationOnly() ? ' class="convo"' : ""}>`;
  // Only markers at least as new as the oldest message on this page are placed; older ones belong to older pages.
  const oldest = d.messages.length ? Math.min(...d.messages.map(tsOf)) : 0;
  const first = withMarkers(d.messages, d.markers, d.has_more ? oldest - 1 : 0);
  html += first.length ? first.map(renderMessage).join("") : `<div class="empty">${s.file_exists ? "No messages yet." : "No turns yet: the harness creates the session file on the first prompt. This agent is woken by events or, for the Supervisor, by a message."}</div>`;
  html += `</div><div id="transcript-more" class="empty" style="margin-top:8px">${d.has_more ? "scroll for older messages…" : "beginning of session"}</div>`;
  openDrawer(html);
  transcriptPaging = { id, nextBefore: d.next_before, hasMore: d.has_more, loading: false, total: d.total,
                       markerTs: Math.max(0, ...(d.markers || []).map(tsOf)), finished: !transcriptLive(d.session) };
  const ta = $("chat-input");
  if (ta) { ta.focus(); ta.addEventListener("keydown", (e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); $("chat").requestSubmit(); } }); }
}

function transcriptHead(id, s, d) {
  const st = agentStatus(s, Date.now() / 1000, { host_alive: d.host && d.host.alive });
  const when = (t) => (t ? new Date(t * 1000).toLocaleString() : "—");
  const perTicket = /^(worker|reviewer)-/.test(id);
  const agentLink = s.kind && !perTicket ? `<a href="/agents#agent=${encodeURIComponent(s.kind)}" class="muted" style="margin-left:auto;font-size:11px" title="The agent's definition, schedule, and every run it has made">↩ ${esc(s.kind)}: definition and all runs</a>` : "";
  // a harness that keeps a process between turns has its own pid; one that starts a process per turn has none between them
  const process = s.pid ? `pid ${s.pid} (the turn's process)` : s.alive ? "none between turns; the session is kept by the agents' host" : "none";
  const kv = `<div class="kv"><div>kind</div><div>${esc(s.kind || "")}</div><div>process</div><div>${esc(process)}</div>
    ${s.ticket_id ? `<div>ticket</div><div><a href="#" onclick="(window.openTicket ? openTicket('${esc(s.ticket_id)}') : (location.href='/tickets#ticket=${esc(s.ticket_id)}'));return false">${esc(s.ticket_id)}</a></div>` : ""}
    <div>last started</div><div>${when(s.started_at)}${s.status === "running" ? ` <span class="muted">(turn running ${ago((Date.now() / 1000) - s.started_at)})</span>` : ""}</div>
    <div>last output</div><div>${s.last_output_at ? ago((Date.now() / 1000) - s.last_output_at) + " ago" : "—"}</div>
    <div>last turn ended</div><div>${when(s.settled_at)}</div>
    <div>turns</div><div>${d.turns} · ${d.total} messages <span class="muted">· ${transcriptLive(s) ? `live, refreshes every ${TRANSCRIPT_POLL_MS / 1000}s` : `finished ${when(s.settled_at)}`}</span></div><div>session file</div><div class="muted" style="word-break:break-all">${s.file_exists ? esc(s.session_file) : "(created on the first turn)"}</div></div>`;
  const scripted = id === "supervisor" && s.harness === "dummy" ? ' <span class="tag" title="the dummy harness: a script answers, no model">scripted (dummy)</span>' : "";
  return `<h3>${esc(id)} <span class="chip ${st.cls}" title="${esc(st.title)}"><span class="dot"></span>${esc(st.text)}${st.tag ? `<span class="chip-tag">${esc(st.tag)}</span>` : ""}</span>${scripted}${agentLink}</h3>
    ${id === "supervisor" ? `<details class="started"><summary class="muted">session details</summary>${kv}</details>` : kv}
    ${s.status === "running" && (s.partial_text || s.thinking_chars) ? `<div class="msg assistant inprogress"><div class="r">reply in progress <span class="muted">· not in the transcript until the message completes${s.thinking_chars ? ` · ${Number(s.thinking_chars).toLocaleString()} chars of thinking so far` : ""}</span></div>${s.partial_text ? md(s.partial_text) : '<span class="muted">(thinking; no reply text yet)</span>'}</div>` : ""}`;
}

const TRANSCRIPT_POLL_MS = 5000;
// a transcript that can still grow: its turn runs, or its session is kept between turns (resident)
const transcriptLive = (s) => !!s && s.status !== "archived" && (s.status === "running" || !!s.alive);

// The open transcript polls for what arrived since it was rendered and prepends it
// (newest first), keeping the reader's place and the older pages already loaded.
async function refreshTranscript() {
  const p = transcriptPaging;
  if (!p || p.loading || p.finished || !$("overlay").classList.contains("open") || !$("transcript")) return;
  p.loading = true;
  try {
    const r = await fetch(`${AGENTS_API}/sessions/${p.id}/transcript?after=${p.total}`);
    if (!r.ok) return;
    const d = await r.json();
    if (d.total < p.total) { transcriptPaging = null; return openTranscript(p.id); }  // file replaced: start over
    const head = $("transcript-head"); if (head) head.innerHTML = transcriptHead(p.id, d.session, d);
    showPending(d.pending, d.session);
    if (!transcriptLive(d.session)) p.finished = true;          // nothing more will come: stop polling it
    const fresh = withMarkers(d.messages, d.markers, p.markerTs);
    p.markerTs = Math.max(p.markerTs || 0, ...(d.markers || []).map(tsOf));
    if (!fresh.length) return;
    const box = $("transcript"), drawer = $("drawer");
    const empty = box.querySelector(".empty"); if (empty) empty.remove();
    const before = drawer.scrollHeight;
    box.insertAdjacentHTML("afterbegin", fresh.map(renderMessage).join(""));
    highlightCode(box);
    if (drawer.scrollTop > 0) drawer.scrollTop += drawer.scrollHeight - before;  // reading older messages: do not yank
    p.nextBefore += d.messages.length; p.total = d.total;
  } catch (e) { /* next tick tries again */ }
  finally { p.loading = false; }
}

async function loadOlderTranscript() {
  const p = transcriptPaging; if (!p || !p.hasMore || p.loading) return;
  p.loading = true;
  try {
    const d = await (await fetch(`${AGENTS_API}/sessions/${p.id}/transcript?before=${p.nextBefore}&limit=30`)).json();
    const box = $("transcript"); if (!box) return;
    box.insertAdjacentHTML("beforeend", d.messages.map(renderMessage).join(""));
    highlightCode(box);
    p.nextBefore = d.next_before; p.hasMore = d.has_more;
    $("transcript-more").textContent = p.hasMore ? "scroll for older messages…" : "beginning of session";
  } finally { p.loading = false; }
}
// An open transcript follows its session and pages back as the drawer scrolls. On the pages that
// load this file late (every page but the Agents page: attention.js and machine-cards.js load it
// on demand, after the page has loaded) DOMContentLoaded has passed, so it starts at once there.
function agentsFollowTranscripts() {
  $("drawer").addEventListener("scroll", () => { const el = $("drawer"); if (transcriptPaging && el.scrollTop + el.clientHeight > el.scrollHeight - 300) loadOlderTranscript(); });
  setInterval(refreshTranscript, TRANSCRIPT_POLL_MS);
}
if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", agentsFollowTranscripts);
else agentsFollowTranscripts();

// The owner's messages the host accepted and the transcript does not hold yet (owner_messages.py,
// the transcript's `pending`), newest first like the transcript: sent and queued, being answered,
// answered (the transcript is about to show it) or not answered, with why. Never shown as lost.
let sendingText = null, sendingTaken = false;   // the message this tab is sending, until the host's list (or the transcript) has it
function renderPending(list, s) {
  list = list || [];
  const running = s && s.status === "running", answering = list.some((p) => p.status === "answering");
  const state = (p) => p.status === "answering" ? "sent · the Supervisor is answering it"
    : p.status === "waiting" ? (p.place > 1 ? `sent · queued behind ${p.place - 1} other message${p.place > 2 ? "s" : ""} of yours`
                                : answering ? "sent · queued: next, after the message being answered"
                                : running && p.behind ? `sent · queued: next, once ${p.behind} ends (it is not interrupted; no other wake-up turn starts before your message)`
                                : running ? "sent · queued: next, once the Supervisor's current turn ends" : "sent · starting")
    : p.status === "answered" ? "sent · answered; the transcript below shows it in a moment"
    : `sent · not answered: ${p.error || "the turn failed"}. Send it again if it still matters.`;
  const rows = [...list].reverse().map((p) => `<div class="msg user owner pending ${esc(p.status)}"><div class="r"><b>you</b>${msgTime({ ts: p.at })} <span class="muted">· ${esc(state(p))}</span></div>${md(p.text)}</div>`);
  if (sendingText != null && !list.some((p) => p.text === sendingText)) rows.unshift(`<div class="msg user owner pending sending"><div class="r"><b>you</b> <span class="muted">· sending…</span></div>${md(sendingText)}</div>`);
  return rows.join("");
}
function showPending(list, s) {
  const box = $("pending"); if (!box) return;
  // in the host's list now, or taken and already out of it: the transcript holds it
  if (sendingText != null && (sendingTaken || (list || []).some((p) => p.text === sendingText))) sendingText = null;
  box.innerHTML = renderPending(list, s); highlightCode(box);
}

// "Only the conversation": the owner's messages and the Supervisor's written replies, without the
// tool calls, their results and Orchestra's markers. Remembered per browser.
function conversationOnly() { try { return localStorage.getItem("agents.conversationOnly") === "1"; } catch (e) { return false; } }
function setConversationOnly(on) {
  try { localStorage.setItem("agents.conversationOnly", on ? "1" : "0"); } catch (e) { /* not remembered */ }
  const box = $("transcript"); if (box) box.classList.toggle("convo", !!on);
}

async function sendToSupervisor(ev) {
  ev.preventDefault();
  const ta = $("chat-input"), btn = ev.target.querySelector("button"), live = $("live");
  const message = ta.value.trim();
  if (!message) return false;
  ta.value = ""; ta.disabled = true; btn.disabled = true;
  sendingText = message; sendingTaken = false;
  const box = $("pending"); if (box) { box.insertAdjacentHTML("afterbegin", renderPending([], null)); highlightCode(box); }
  live.insertAdjacentHTML("afterbegin", `<div class="msg assistant"><div class="r">supervisor · working…</div><div id="live-body"></div></div>`);
  $("drawer").scrollTop = 0;
  let accepted = false;
  // Not accepted (refused, or the host is down): the words go back into the box, never lost.
  const notSent = (why) => {
    sendingText = null; const b = $("pending"); if (b) b.querySelector(".sending")?.remove();
    if (!ta.value) ta.value = message;
    $("live-body").innerHTML += `<div class="bad">Not sent: ${why} Your message is back in the box.</div>`;
  };
  try {
    const resp = await fetch(AGENTS_API + "/supervisor", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ message }) });
    if (!resp.ok) {
      const body = await resp.text();
      const proxy = /^\s*<!doctype html|<html/i.test(body);
      if (proxy) $("live-body").innerHTML = `<div class="bad">The connection was closed by the proxy in front of this page (HTTP ${resp.status}). If the host took your message it is listed above, with where it stands; the reply will appear in the transcript below.</div>`;
      else notSent(esc(body.slice(0, 300)));
      return false;
    }
    const reader = resp.body.getReader(); const dec = new TextDecoder(); let buf = "";
    while (true) {
      const { value, done } = await reader.read(); if (done) break;
      buf += dec.decode(value, { stream: true });
      let nl; while ((nl = buf.indexOf("\n")) >= 0) {
        const line = buf.slice(0, nl); buf = buf.slice(nl + 1); if (!line.trim()) continue;
        const e = JSON.parse(line); const body = $("live-body");
        if (e.type === "accepted") { accepted = sendingTaken = true; refreshTranscript(); }
        else if (e.type === "tool_start") body.innerHTML += `<div class="tc"><div class="r">⚙ ${esc(e.name || "tool")}</div></div>`;
        else if (e.type === "assistant_end") {
          const txt = e.text || "";
          if (txt.trim()) { body.innerHTML += md(txt); highlightCode(body); }
        } else if (e.type === "done") {
          if (!accepted && e.error) { body.previousElementSibling.textContent = "supervisor"; notSent(md(e.error)); continue; }
          body.previousElementSibling.textContent = e.error ? "supervisor · failed" : e.timed_out ? "supervisor · timed out" : "supervisor";
          if (e.error) body.innerHTML += `<div class="bad">${md(e.error)}</div>`;
        }
      }
    }
  } catch (err) {
    $("live-body").innerHTML += `<div class="bad">${esc(String(err))}. ${accepted ? "Your message was taken: it is listed above until the transcript shows it." : ""}</div>`;
    return false;
  } finally { ta.disabled = false; btn.disabled = false; ta.focus(); if (accepted) sendingTaken = true; }
  await refreshTranscript();   // the exchange is in the session file now; show it once, there
  live.innerHTML = "";
  return false;
}

const SHELL_TOOLS = new Set(["bash", "shell", "sh", "powershell"]);

function renderToolCall(t) {
  const args = t.args && typeof t.args === "object" ? t.args : {};
  if (SHELL_TOOLS.has(t.name) && typeof args.command === "string")
    return `<div class="tc"><div class="r">⚙ ${esc(t.name)}</div><pre><code class="language-bash">${esc(args.command)}</code></pre></div>`;
  const shown = Object.keys(args).length ? JSON.stringify(args, null, 1) : (typeof t.args === "string" ? t.args : "");
  return `<div class="tc"><div class="r">⚙ ${esc(t.name)}</div>${shown ? `<pre><code class="language-json">${esc(shown).slice(0, 3000)}</code></pre>` : ""}</div>`;
}

function msgTime(m) {
  if (!m.ts) return "";
  // a harness may record timestamps as ISO strings or epoch millis (or seconds).
  const t = typeof m.ts === "number" ? (m.ts > 1e12 ? m.ts : m.ts * 1000) : Date.parse(m.ts);
  if (!isFinite(t)) return "";
  return `<span class="muted num" style="margin-left:8px">${new Date(t).toLocaleTimeString()} · ${ago((Date.now() - t) / 1000)} ago</span>`;
}
const tsOf = (m) => { const t = typeof m.ts === "number" ? (m.ts > 1e12 ? m.ts : m.ts * 1000) : Date.parse(m.ts || ""); return isFinite(t) ? t : 0; };
function fmtDur(s) { return s == null ? "" : s >= 3600 ? `${Math.floor(s / 3600)}h ${Math.round((s % 3600) / 60)}m` : s >= 60 ? `${Math.floor(s / 60)}m ${Math.round(s % 60)}s` : `${Math.round(s)}s`; }
function renderMarker(e) {
  let text;
  if (e.type === "session.timed_out" && e.stopped_by === "silence") text = `⏱ Orchestra stopped this turn: nothing came from the agent for ${fmtDur(e.limit)} (silence_timeout) while it was ${esc(e.doing || "working")}` +
    (e.phase_for != null ? `, ${fmtDur(e.phase_for)} into that` : "") + ". Orchestra took that as stuck. If the model was only slow, raise silence_timeout in orchestra.toml.";
  else if (e.type === "session.timed_out") text = `⏱ Orchestra stopped this turn: the ${fmtDur(e.limit)} hard turn limit (turn_timeout in orchestra.toml) ran out while the agent was ${esc(e.doing || "working")}` +
    (e.phase_for != null ? ` (${fmtDur(e.phase_for)} into that)` : "") + (e.silent_for != null ? `, last output ${fmtDur(e.silent_for)} earlier` : "") + ". Nothing after this point was the agent's decision.";
  else if (e.type === "session.error") text = `✖ Orchestra ended this turn: ${md(e.error || "harness error")}`;
  else if (e.type === "session.interrupted") text = `■ Orchestra was restarted or stopped during this turn, which cut it off. ${esc(e.reason || "")}`;
  else if (e.type === "agent.crashed") text = `✖ The agent's process exited on its own between turns (crash ${e.count || 1}); Orchestra is restarting it.${e.stderr ? ` Last stderr: <code>${esc(e.stderr.trim().split("\n").slice(-1)[0].slice(0, 160))}</code>` : ""}`;
  else if (e.type === "agent.crash_loop") text = `✖ Crash loop: ${e.count} exits in fifteen minutes. The agent is paused after a crash loop and the Supervisor has been asked to look: ask the Supervisor to resume it once fixed.`;
  else if (e.type === "agent.compacted") text = `⇣ Context compacted by the harness${e.tokens_before ? `: ${Number(e.tokens_before).toLocaleString()}${e.tokens_after ? ` → ${Number(e.tokens_after).toLocaleString()}` : ""} tokens` : ""} (${esc(e.reason || "")}). Older messages became a summary the agent keeps; the transcript is unchanged.`;
  else if (e.type === "agent.reset") text = `⟲ Session started over by ${esc(e.by || "the Supervisor")}${e.reason ? ` (${esc(e.reason)})` : ""}. The conversation before this point is archived as run <code>${esc(e.archived || "")}</code>; the agent keeps the project context and its team's space, not its memory of the chat.`;
  else if (e.type === "agent.corrected") text = `✎ ${md(e.text || "")}`;
  else if (e.type === "session.started") text = `▶ Turn started${e.ticket ? " on " + esc(e.ticket) : ""}.`;
  else text = esc(e.type);
  return `<div class="msg marker ${esc(String(e.type).replace(/\./g, "_"))}"><div class="r">orchestra${msgTime({ ts: e.ts })}</div>${text}</div>`;
}
// Markers are Orchestra's events for the session; they are merged with the messages by time, newest first.
function withMarkers(messages, markers, newerThan) {
  const ms = (markers || []).filter((e) => tsOf(e) > (newerThan || 0)).map((e) => ({ ...e, _marker: true }));
  return [...messages, ...ms].sort((a, b) => tsOf(b) - tsOf(a));
}
// A user message that Orchestra wrote: a wake-up, an assignment, a review request, a nudge.
const FROM_ORCHESTRA = /^(\[Orchestra:|# Wake-up for |# Your assignment|# Review request|You stopped without)/;
// A wake-up is Orchestra's prompt to an agent the human did not write: what woke it, then the
// whole project snapshot. In a transcript it is one line, the reasons, with the prompt folded under
// it, so what the agent answered is what the eye lands on.
function wakeReasons(text) {
  const at = text.indexOf("## Why you were woken");
  if (at < 0) return [];
  return text.slice(at).split("\n## ")[0].split("\n").filter((l) => l.startsWith("- ")).map((l) => l.slice(2).trim()).filter(Boolean);
}
function renderWake(m) {
  const who = (m.text.match(/# Wake-up for the ([^\n]+)/) || [])[1] || "agent";
  const why = wakeReasons(m.text), first = why.slice(0, 2).map((r) => esc(r.slice(0, 160))).join("; ") || "an event";
  return `<details class="msg marker wake"${m.i != null ? ` data-i="${m.i}"` : ""}><summary><b>Orchestra woke the ${esc(who)}</b>, not a message from you: ${first}${why.length > 2 ? ` · and ${why.length - 2} more` : ""}${msgTime(m)}</summary>
    <div class="prompt md">${md(m.text.slice(0, 12000))}</div></details>`;
}
function renderMessage(m) {
  if (m._marker) return renderMarker(m);
  const at = m.i != null ? ` data-i="${m.i}"` : "";
  if (m.role === "toolResult") {
    // folded to one line, the tool and the result's first line; the shell command itself stays open on its call
    const shell = SHELL_TOOLS.has(m.tool_name);
    const body = shell ? `<pre class="term">${highlightLog(m.text)}</pre>` : `<pre><code>${esc(m.text)}</code></pre>`;
    const first = ((m.text || "").split("\n").find((l) => l.trim()) || "(empty)").slice(0, 120);
    return `<details class="msg toolResult ${m.is_error ? "err" : ""}"${at}><summary class="r">result · ${esc(m.tool_name || "tool")}${m.is_error ? ' <span class="bad">error</span>' : ""}${msgTime(m)} <code>${esc(first)}</code></summary>${body}</details>`;
  }
  if (m.role === "user") {
    // the server marks the owner's words in a Supervisor prompt (owner_messages.marked), all of them
    if (m.owner != null) return (m.context ? `<details class="msg marker context"><summary>context the Supervisor was given (${m.context.split("\n").length} lines)</summary><div class="prompt md">${md(m.context.slice(0, 12000))}</div></details>` : "") +
      `<div class="msg user owner"${at}><div class="r"><b>you</b>${msgTime(m)}</div>${md(m.owner)}</div>`;
    if (m.text.startsWith("# Wake-up for ") || (m.text.startsWith("[Orchestra:") && m.text.includes("\n# Wake-up for "))) return renderWake(m);
    const who = FROM_ORCHESTRA.test(m.text) ? "orchestra" : "you";
    return `<div class="msg user"${at}><div class="r">${who}${msgTime(m)}</div>${md(m.text.slice(0, 12000))}</div>`;
  }
  return `<div class="msg ${m.role}${m.role === "assistant" && !(m.text || "").trim() ? " notext" : ""}"${at}><div class="r">${m.role}${msgTime(m)}</div>${md(m.text.slice(0, 12000))}${m.tool_calls.map(renderToolCall).join("")}</div>`;
}

// One turn of a session, in order, for reading at a glance (the ticket drawer's agent sessions,
// ticket-sessions.js): the prompt folded to its first line, what the agent wrote, each tool call
// folded to its name and gist with its result inside, the host's stops, and the last words open.
function toolGist(c) {
  const a = c.args && typeof c.args === "object" ? c.args : {};
  const v = a.command || a.path || a.file_path || a.state || a.verdict || a.message || a.summary || a.pattern || a.query || "";
  return typeof v === "string" ? v.split("\n")[0].slice(0, 90) : "";
}
function renderTurn(msgs, markers) {
  const out = [];
  const lastText = [...msgs].reverse().find((m) => m.role === "assistant" && m.text.trim());
  const pending = [];   // tool results waiting for the call they answer: in order, one per call
  msgs.forEach((m) => {
    if (m.role === "user") {
      const first = m.text.split("\n").find((l) => l.trim() && !l.startsWith("[Orchestra:")) || m.text.split("\n")[0];
      out.push(`<details class="tt-prompt"><summary><span class="muted">${FROM_ORCHESTRA.test(m.text) ? "prompt" : "message"}${msgTime(m)}</span> ${esc(first.replace(/^#+\s*/, "").slice(0, 140))}</summary><div class="prompt md">${md(m.text.slice(0, 12000))}</div></details>`);
    } else if (m.role === "assistant") {
      if (m.text.trim()) out.push(`<div class="tt-say ${m === lastText ? "last" : ""}">${md(m.text.slice(0, 12000))}</div>`);
      (m.tool_calls || []).forEach((c) => { const slot = { c, res: null }; pending.push(slot); out.push(slot); });
    } else if (m.role === "toolResult") {
      const slot = pending.find((x) => !x.res && (x.c.name === m.tool_name || m.tool_name === "?")) || pending.find((x) => !x.res);
      if (slot) slot.res = m; else out.push({ c: { name: m.tool_name || "tool", args: {} }, res: m });
    }
  });
  const html = out.map((x) => {
    if (typeof x === "string") return x;
    const { c, res } = x, gist = toolGist(c), err = res && res.is_error;
    const shown = c.args && typeof c.args === "object" && Object.keys(c.args).length ? JSON.stringify(c.args, null, 1) : "";
    return `<details class="tt-tool ${err ? "err" : ""}"><summary>⚙ <b>${esc(c.name)}</b>${gist ? ` <code>${esc(gist)}</code>` : ""}${res ? (err ? ' <span class="bad">error</span>' : "") : ' <span class="muted">· no result yet</span>'}</summary>
      ${shown ? `<pre><code class="language-json">${esc(shown.slice(0, 3000))}</code></pre>` : ""}${res ? `<div class="r muted">result</div><pre class="term">${esc((res.text || "(empty)").slice(0, 6000))}</pre>` : ""}</details>`;
  });
  (markers || []).forEach((e) => html.push(renderMarker(e)));
  return html.join("") || '<div class="empty">No messages in this turn yet.</div>';
}

document.addEventListener("keydown", (e) => { if (e.key === "Escape") closeDrawer(); });

// #session=<id> opens a transcript (the task system's tasks.js handles #ticket= and #gate=)
function agentsFromHash() {
  const m = location.hash.match(/^#session=([^&]+)/);
  if (m) openTranscript(decodeURIComponent(m[1]));
}
window.addEventListener("hashchange", agentsFromHash);

// The one source of an agent's status text: `s` is its latest session (or null), `a` what is
// known of the agent beyond its session (resident, name, next_scheduled_at, host_alive), when
// known. A per-ticket role or an ephemeral agent with nothing running is idle, never down; down
// is a resident whose host is gone; a resident between turns says what it waits for.
function agentStatus(s, now, a) {
  a = a || {};
  if (a.not_used) return { cls: "", text: "not used: " + a.not_used, title: "never started here: it plans into the task system's tickets, which is not installed" };
  if (s && s.status === "archived") return { cls: "archived", text: "finished", title: "ticket closed" };
  if (s && s.status === "running") return runningPhase(s, now);
  const perTicket = s ? (!!s.ticket_id || /^(worker|reviewer)-/.test(s.id || s.session || "")) : false;
  const resident = a.resident != null ? a.resident : !perTicket && !!s;
  if (!resident) {
    const last = s && s.settled_at ? `last run ${ago(now - s.settled_at)} ago` : "no run yet";
    return { cls: "", text: "idle", title: s && s.last_text ? `${last}: ${s.last_text.slice(0, 160)}` : last };
  }
  if (a.host_alive === false || !(s && s.alive)) return { cls: "down", text: "down", title: "the agents' host (agents.host, started by orchestra run) is not up" };
  const name = a.name || (s && s.kind);
  const wakes = a.next_scheduled_at && a.next_scheduled_at > now ? `wakes in ${ago(a.next_scheduled_at - now)}` : null;
  const text = name === "supervisor" ? "waiting for you" : wakes || "waiting for an event";
  const title = name === "supervisor" ? "waiting for your message" + (a.next_review_at && a.next_review_at > now ? `; next review in ${ago(a.next_review_at - now)}` : "") : wakes ? `next wake-up: ${wakes}` : "waiting for an event";
  return { cls: "resident", text, title, tag: "resident" };
}
function runningPhase(s, now) {
  const age = (t) => ago(now - (t || now));
  const turn = `turn started ${age(s.started_at)} ago`;
  if (s.phase === "tool") return { cls: "working", text: `running ${s.tool_name || "tool"} · ${age(s.phase_since)}`, title: turn };
  if (s.phase === "generating") {
    const sinceTok = s.last_token_at ? now - s.last_token_at : 0;
    return { cls: sinceTok > 60 ? "waiting" : "working", text: `receiving tokens · last token ${ago(sinceTok)} ago`, title: turn };
  }
  // The session is mid-turn and its stream has been quiet since phase_since: normal while a
  // tool runs or the model thinks; past a minute it is worth saying how long.
  const wait = now - (s.phase_since || s.started_at || now);
  if (wait <= 60) return { cls: "working", text: `working · ${ago(wait)}`, title: `${turn}; quiet since then, which is normal while a tool runs` };
  return { cls: "waiting", text: `quiet for ${ago(wait)}`, title: `the turn is still running; no output for ${ago(wait)}` };
}
// compact (a Dashboard machine row): the status's first words, the rest and the tag in its title
function sessionChip(s, now, a, compact) {
  const p = agentStatus(s, now, a);
  if (compact) {
    const short = /^waiting\b/.test(p.text) ? "waiting" : p.text.split(" · ")[0];
    const title = [p.text, p.title, p.tag].filter((x, i, xs) => x && xs.indexOf(x) === i).join(" · ");
    return `<span class="chip ${p.cls}" title="${esc(title)}"><span class="dot"></span>${esc(short)}</span>`;
  }
  return `<span class="chip ${p.cls}" title="${esc(p.title)}"><span class="dot"></span>${esc(p.text)}${p.tag ? `<span class="chip-tag">${esc(p.tag)}</span>` : ""}</span>`;
}
