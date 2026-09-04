const $ = (s, r = document) => r.querySelector(s);
const ICONS = {read_file:"📄", list_directory:"📁", write_file:"🖊️", edit_file:"✏️",
               delete_file:"🗑️", run_command:"💻"};
const S = {settings:null, sessionId:null, sessions:[], busy:false, abort:null, toolParts:{}};

function esc(s){return (s??"").replace(/[&<>]/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;"}[c]));}
function md(text){
  if(!text) return "";
  let h;
  if(window.marked){ try{ h = marked.parse(text); }catch(e){ h = esc(text); } }
  else h = esc(text).replace(/\n/g,"<br>");
  if(window.DOMPurify) h = DOMPurify.sanitize(h);
  return h;
}
function hl(root){
  if(!window.hljs) return;
  root.querySelectorAll("pre code").forEach(el=>{ if(!el.dataset.hl){ try{hljs.highlightElement(el);}catch(e){} el.dataset.hl="1"; }});
}

/* ---------- message rendering ---------- */
function newMsgEl(role){
  const wrap = document.createElement("div"); wrap.className = "wrap";
  const m = document.createElement("div"); m.className = "msg " + role;
  if(role === "assistant"){
    const av = document.createElement("div"); av.className = "avatar"; av.textContent = "Q";
    m.appendChild(av);
  }
  const b = document.createElement("div"); b.className = "bubble";
  m.appendChild(b); wrap.appendChild(m); return {wrap, bubble:b};
}
function addUser(text){
  $("#welcome")?.remove();
  const {wrap, bubble} = newMsgEl("user"); bubble.textContent = text;
  $("#chat").appendChild(wrap); scroll();
}
function addAssistant(){
  $("#welcome")?.remove();
  const {wrap, bubble} = newMsgEl("assistant");
  $("#chat").appendChild(wrap); scroll();
  return {wrap, bubble, parts:[], renderTimers:{}};
}
function scroll(){ const c=$("#chat"); c.scrollTop = c.scrollHeight; }

function thinkingEl(){
  const d = document.createElement("div"); d.className = "thinking";
  d.innerHTML = `<div class="th-head"><span class="dot run"></span><span class="lbl">Deep thinking…</span><span class="chev">▾</span></div><div class="th-body"></div>`;
  d.querySelector(".th-head").onclick = () => d.classList.toggle("collapsed");
  return d;
}
function contentEl(){
  const d = document.createElement("div"); d.className = "part content";
  d.innerHTML = `<div class="md"></div><span class="cursor"></span>`;
  return d;
}
function ensureThinking(st){
  let last = st.parts[st.parts.length-1];
  if(last && last.type === "thinking") return last;
  const el = thinkingEl(); st.bubble.appendChild(el); scroll();
  const p = {type:"thinking", text:"", el, body:el.querySelector(".th-body")};
  st.parts.push(p); return p;
}
function ensureContent(st){
  let last = st.parts[st.parts.length-1];
  if(last && last.type === "content") return last;
  const el = contentEl(); st.bubble.appendChild(el); scroll();
  const p = {type:"content", text:"", el, mdEl:el.querySelector(".md"), cur:el.querySelector(".cursor")};
  st.parts.push(p); return p;
}
function renderContent(p, st){
  p.mdEl.innerHTML = md(p.text); hl(p.mdEl); scroll();
}
function scheduleContent(p, st){
  if(st.renderTimers[p===ensureContent(st)?0:0]) {}
  if(p._t) return;
  p._t = setTimeout(()=>{ p._t=null; renderContent(p, st); }, 90);
}

function toolEl(part){
  const d = document.createElement("div"); d.className = "tool open"; d.dataset.status = part.status;
  d.innerHTML = `<div class="tool-head"><span class="ic">${ICONS[part.name]||"🔧"}</span>
     <span class="nm">${esc(part.name)}</span><span class="sum"></span>
     <span class="pill ${part.status}">${pillText(part.status)}</span><span class="chev">▾</span></div>
     <div class="tool-body"></div>`;
  d.querySelector(".tool-head").onclick = () => d.classList.toggle("open");
  part.el = d; part.head = d.querySelector(".tool-head");
  part.pill = d.querySelector(".pill"); part.sum = d.querySelector(".sum");
  part.body = d.querySelector(".tool-body");
  d.querySelector(".sum").textContent = summary(part);
  renderToolBody(part);
  return d;
}
function pillText(s){return {running:"Running…", confirm:"Waiting for approval",
  success:"Done", error:"Error", denied:"Denied"}[s] || s;}
function summary(p){
  const a = p.args || {};
  if(p.name === "run_command") return "$ " + String(a.command||"").slice(0,90);
  return String(a.path||"") + (p.name==="edit_file" ? "  (edit)" : "");
}
function renderToolBody(p){
  const b = p.body; if(!b) return;
  const ui = p.ui || {};
  if(p.name === "run_command"){
    b.innerHTML = `<div class="term"><div class="cmd">$ ${esc((p.args||{}).command||"")}</div>
      <pre class="out"></pre><pre class="err"></pre><span class="rc"></span></div>`;
    p._out = b.querySelector(".out"); p._err = b.querySelector(".err"); p._rc = b.querySelector(".rc");
    p._out.textContent = p.stdout || ui.stdout || "";
    p._err.textContent = p.stderr || ui.stderr || "";
    if(ui.returncode !== undefined) p._rc.textContent = `exit ${ui.returncode}${ui.timed_out?" · timed out":""}`;
    termScroll(p);
  } else if(p.name === "edit_file" && ui.before !== undefined){
    b.innerHTML = `<div class="kv">${esc(ui.path)} · ${ui.replacements} replacement(s)</div><div class="diff"></div>`;
    b.querySelector(".diff").innerHTML = renderDiff(ui.before, ui.after);
  } else if(p.name === "read_file" && ui.content !== undefined){
    b.innerHTML = `<div class="kv">${esc(ui.path)} · ${ui.total_lines} lines</div><pre><code>${esc(ui.content)}</code></pre>`;
    hl(b);
  } else if(p.name === "write_file" && ui.content !== undefined){
    b.innerHTML = `<div class="kv">${esc(ui.path)} · ${ui.existed?"overwrote":"created"}</div><pre><code>${esc(ui.content)}</code></pre>`;
    hl(b);
  } else if(p.name === "list_directory" && ui.entries){
    b.innerHTML = `<div class="kv">${esc(ui.path)}${ui.recursive?" · recursive":""}</div><pre>` +
      ui.entries.map(e=>`${e.type==="dir"?"[dir] ":"     "}${esc(e.name)}${e.type==="file"?"  ("+e.size+" B)":""}`).join("\n") + `</pre>`;
  } else if(p.name === "delete_file"){
    b.innerHTML = `<div class="kv">deleted ${esc(ui.kind||"")} : ${esc(ui.path||"")}</div>`;
  } else if(ui && ui.denied){
    b.innerHTML = `<div class="kv" style="color:var(--red)">Operation denied by user.</div>`;
  } else if(ui && ui.error){
    b.innerHTML = `<div class="kv" style="color:var(--red)">${esc(ui.error)}</div>`;
  } else {
    b.innerHTML = `<pre>${esc(JSON.stringify(p.args,null,2))}</pre>`;
  }
}
function termScroll(p){ if(p._out) p._out.parentElement.scrollTop = 9e9; }
function renderDiff(before, after){
  if(window.Diff){
    return Diff.diffLines(before||"", after||"").map(part=>{
      const cls = part.added?"add":part.removed?"del":"ctx";
      return part.value.split("\n").slice(0,-1).concat(part.value.endsWith("\n")?[]:[""])
        .filter((_,i,a)=> i<a.length-1 || _!=="")
        .map(line=>`<span class="${cls}">${esc(line)||" "}</span>`).join("");
    }).join("");
  }
  return `<pre><code>${esc(after)}</code></pre>`;
}

/* hydrate a saved display message (static) */
function renderSaved(msg){
  if(msg.role === "user"){ addUser(msg.content); return; }
  const st = addAssistant();
  for(const part of msg.parts){
    if(part.type === "thinking"){
      const p = ensureThinking(st); p.text = part.text; p.body.textContent = part.text;
      p.el.querySelector(".dot").classList.remove("run");
      p.el.querySelector(".lbl").textContent = "Deep thinking";
    } else if(part.type === "content"){
      const p = ensureContent(st); p.text = part.text; p.cur.remove();
      p.mdEl.innerHTML = md(part.text); hl(p.mdEl);
    } else if(part.type === "tool"){
      part.stdout = (part.ui&&part.ui.stdout)||""; part.stderr = (part.ui&&part.ui.stderr)||"";
      const el = toolEl(part); st.bubble.appendChild(el);
      el.classList.remove("open");
      if(part.status==="error"||part.status==="confirm") el.classList.add("open");
    }
  }
  scroll();
}

/* ---------- streaming ---------- */
function finalize(st){
  for(const p of st.parts){
    if(p.type === "thinking"){ p.el.querySelector(".dot").classList.remove("run");
      p.el.querySelector(".lbl").textContent = "Deep thinking"; }
    if(p.type === "content"){ if(p._t){clearTimeout(p._t);renderContent(p,st);} p.cur?.remove(); }
    if(p.type === "tool"){ p.el?.classList.remove("open"); }
  }
}
function showError(bubble, msg){
  const e = document.createElement("div"); e.className = "errbox"; e.textContent = "⚠ " + msg;
  bubble.appendChild(e); scroll();
}

async function consume(resp, st){
  const reader = resp.body.getReader();
  const dec = new TextDecoder(); let buf = "";
  while(true){
    const {value, done} = await reader.read();
    if(done) break;
    buf += dec.decode(value, {stream:true});
    let idx;
    while((idx = buf.indexOf("\n\n")) >= 0){
      const block = buf.slice(0, idx); buf = buf.slice(idx+2);
      for(const line of block.split("\n")){
        if(!line.startsWith("data: ")) continue;
        handle(JSON.parse(line.slice(6)), st);
      }
    }
  }
}
function handle(ev, st){
  switch(ev.type){
    case "session":
      S.sessionId = ev.id;
      if(!S.sessions.find(s=>s.id===ev.id)) S.sessions.unshift({id:ev.id,title:"New chat",updated:Date.now()/1000});
      renderSessions(); break;
    case "thinking_delta": {
      const p = ensureThinking(st); p.text += ev.text; p.body.textContent = p.text; scroll(); break;
    }
    case "content_delta": {
      const p = ensureContent(st); p.text += ev.text; scheduleContent(p, st); scroll(); break;
    }
    case "tool_call": {
      const part = {type:"tool", id:ev.id, name:ev.name, status:"running", args:ev.args,
                    ui:null, stdout:"", stderr:""};
      st.parts.push(part); S.toolParts[ev.id] = part;
      st.bubble.appendChild(toolEl(part)); scroll(); break;
    }
    case "tool_status": {
      const part = S.toolParts[ev.id]; if(!part) break;
      part.status = ev.status; part.pill.className = "pill " + ev.status; part.pill.textContent = pillText(ev.status);
      if(ev.status === "confirm") part.el.classList.add("open");
      break;
    }
    case "confirm_request": openConfirm(ev); break;
    case "tool_output": {
      const part = S.toolParts[ev.id]; if(!part) break;
      part[ev.stream] = (part[ev.stream]||"") + ev.text;
      if(part._out && ev.stream === "stdout"){ part._out.textContent += ev.text; termScroll(part); }
      if(part._err && ev.stream === "stderr"){ part._err.textContent += ev.text; termScroll(part); }
      break;
    }
    case "tool_result": {
      const part = S.toolParts[ev.id]; if(!part) break;
      part.status = ev.status; part.ui = ev.ui;
      part.pill.className = "pill " + ev.status; part.pill.textContent = pillText(ev.status);
      renderToolBody(part); break;
    }
    case "usage": {
      let u = st.bubble.querySelector(".usage");
      if(!u){ u = document.createElement("div"); u.className="usage"; st.bubble.appendChild(u); }
      u.textContent = `input ${ev.input??0} · output ${ev.output??0} · total ${ev.total??0} tokens`;
      break;
    }
    case "error": showError(st.bubble, ev.message); break;
  }
}

/* ---------- send ---------- */
async function send(text){
  text = (text ?? $("#input").value).trim();
  if(!text || S.busy) return;
  $("#input").value = ""; autosize();
  addUser(text);
  const st = addAssistant();
  S.busy = true; S.toolParts = {}; updateBusy();
  S.abort = new AbortController();
  try{
    const resp = await fetch("/api/chat", {method:"POST",
      headers:{"Content-Type":"application/json"},
      body:JSON.stringify({session_id:S.sessionId, message:text}), signal:S.abort.signal});
    if(!resp.ok) throw new Error("HTTP " + resp.status);
    await consume(resp, st);
  }catch(e){ if(e.name !== "AbortError") showError(st.bubble, e.message); }
  finally{ finalize(st); S.busy = false; S.abort = null; updateBusy(); loadSessions(); }
}
function updateBusy(){
  $("#sendBtn").disabled = S.busy;
  $("#stopBtn").classList.toggle("hidden", !S.busy);
}

/* ---------- confirm modal ---------- */
let confirmId = null;
function openConfirm(ev){
  confirmId = ev.id;
  $("#cfTitle").textContent = "Approve " + ev.name + "?";
  $("#cfSummary").textContent = ev.summary || "";
  $("#cfArgs").textContent = JSON.stringify(ev.args, null, 2);
  $("#confirmOverlay").classList.remove("hidden");
}
async function resolveConfirm(allowed){
  $("#confirmOverlay").classList.add("hidden");
  if(confirmId){ try{ await fetch("/api/confirm",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({id:confirmId,allowed})}); }catch(e){} }
  confirmId = null;
}

/* ---------- sessions ---------- */
function renderSessions(){
  const list = $("#sessionList"); list.innerHTML = "";
  for(const s of S.sessions){
    const d = document.createElement("div");
    d.className = "sess" + (s.id===S.sessionId?" active":"");
    d.innerHTML = `<span class="t">${esc(s.title)}</span><span class="del" title="delete">✕</span>`;
    d.querySelector(".t").onclick = () => openSession(s.id);
    d.querySelector(".del").onclick = (e)=>{ e.stopPropagation(); delSession(s.id); };
    list.appendChild(d);
  }
}
async function loadSessions(){ S.sessions = await (await fetch("/api/sessions")).json(); renderSessions(); }
async function openSession(id){
  const data = await (await fetch("/api/sessions/"+id)).json();
  if(data.error) return;
  S.sessionId = id; $("#chat").innerHTML = ""; $("#welcome")?.remove();
  for(const m of data.display) renderSaved(m);
  renderSessions();
}
async function delSession(id){
  await fetch("/api/sessions/"+id,{method:"DELETE"});
  if(S.sessionId === id) newChat();
  await loadSessions();
}
function newChat(){ S.sessionId = null; $("#chat").innerHTML = ""; showWelcome(); renderSessions(); }
function showWelcome(){
  if($("#welcome")) return;
  const w = document.createElement("div"); w.id = "welcome"; w.className = "welcome";
  w.innerHTML = `<h1>Qwen3.7‑Max, on your machine.</h1>
   <p>Set your <b>Workspace</b> above, then ask it to read, edit, create or delete files and run commands.</p>
   <div class="examples">
     <button class="ex">This workspace is my App Store repo. The app icons/logos aren't showing — find the bug and fix it.</button>
     <button class="ex">List the project, read package.json, then run the build and fix any errors.</button>
     <button class="ex">Add a new file src/utils/format.ts with a currency formatter and import it where needed.</button>
     <button class="ex">Search the code for the Supabase client and tell me the exact query to add a row.</button>
   </div>`;
  $("#main").insertBefore(w, $("#composer"));
  w.querySelectorAll(".ex").forEach(b => b.onclick = () => { $("#input").value = b.textContent; autosize(); $("#input").focus(); });
}

/* ---------- settings ---------- */
function openSettings(){
  const s = S.settings;
  $("#s_key").value = s.api_key||""; $("#s_base").value = s.base_url||""; $("#s_model").value = s.model||"";
  $("#s_ws").value = s.default_workspace||""; $("#s_think").checked = !!s.enable_thinking;
  $("#s_cto").value = s.command_timeout; $("#s_mt").value = s.max_turns;
  $("#settingsStatus").textContent = ""; $("#settingsOverlay").classList.remove("hidden");
}
async function saveSettings(){
  const st = $("#settingsStatus");
  const payload = {api_key:$("#s_key").value.trim(), base_url:$("#s_base").value.trim(),
    model:$("#s_model").value.trim(), default_workspace:$("#s_ws").value.trim(),
    enable_thinking:$("#s_think").checked,
    command_timeout:+$("#s_cto").value||180, max_turns:+$("#s_mt").value||30};
  const r = await (await fetch("/api/settings",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(payload)})).json();
  S.settings = r.settings; applyTopbar(); applyPermMode(S.settings.permission_mode||"ask");
  st.className = "status ok"; st.textContent = "Saved.";
}
async function testConn(){
  const st = $("#settingsStatus"); st.className="status"; st.textContent="Testing…";
  await saveSettings();
  const r = await (await fetch("/api/test",{method:"POST"})).json();
  st.className = "status " + (r.ok?"ok":"err");
  st.textContent = r.ok ? ("✓ Connected. Model replied: " + (r.reply||"(ok)")) : ("✗ " + r.error);
}
function applyTopbar(){
  $("#modelBadge").textContent = S.settings.model;
  $("#workspace").value = S.settings.default_workspace || "";
  $("#thinkToggle").checked = !!S.settings.enable_thinking;
}

/* ---------- misc ui ---------- */
function autosize(){ const t=$("#input"); t.style.height="auto"; t.style.height=Math.min(t.scrollHeight,220)+"px"; }
/* ---------- permission mode dropdown ---------- */
const PERM_META = {
  ask:  {label:"Ask Permission", ic:"🛡️"},
  full: {label:"Full Access",    ic:"⚡"}
};
function applyPermMode(mode){
  mode = (mode === "full") ? "full" : "ask";
  const wrap = $("#permWrap"); if(!wrap) return;
  wrap.dataset.mode = mode;
  $("#permLabel").textContent = PERM_META[mode].label;
  $("#permIc").textContent = PERM_META[mode].ic;
  document.querySelectorAll(".perm-item").forEach(it =>
    it.classList.toggle("active", it.dataset.mode === mode));
}
function setPermMode(mode){
  applyPermMode(mode);
  S.settings.permission_mode = mode;
  fetch("/api/settings",{method:"POST",headers:{"Content-Type":"application/json"},
    body:JSON.stringify({permission_mode:mode})})
    .then(r=>r.json()).then(r=>{ if(r && r.settings) S.settings = r.settings; }).catch(()=>{});
}
function wirePerm(){
  const wrap = $("#permWrap"), btn = $("#permBtn"), menu = $("#permMenu");
  if(!wrap) return;
  btn.addEventListener("click", (e)=>{ e.stopPropagation(); wrap.classList.toggle("open"); });
  menu.querySelectorAll(".perm-item").forEach(it=>{
    it.addEventListener("click", (e)=>{ e.stopPropagation(); setPermMode(it.dataset.mode); wrap.classList.remove("open"); });
  });
  document.addEventListener("click", ()=> wrap.classList.remove("open"));
  document.addEventListener("keydown", e=>{ if(e.key==="Escape") wrap.classList.remove("open"); });
}
async function boot(){
  // server may start a touch after the browser opens -> retry until ready
  for(let i=0;i<60;i++){
    try{ const r = await fetch("/api/settings"); if(r.ok){ S.settings = await r.json(); break; } }catch(e){}
    await new Promise(r=>setTimeout(r,700));
  }
  if(!S.settings){ $("#connecting").innerHTML = "<div>Could not reach the local server. Is start.bat running?</div>"; return; }
  $("#connecting").style.display = "none";
  applyTopbar();
  wirePerm();
  applyPermMode(S.settings.permission_mode || "ask");
  await loadSessions();
  showWelcome();
}

document.addEventListener("DOMContentLoaded", () => {
  $("#newChat").onclick = newChat;
  $("#openSettings").onclick = openSettings;
  $("#closeSettings").onclick = () => $("#settingsOverlay").classList.add("hidden");
  $("#saveSettings").onclick = saveSettings;
  $("#testBtn").onclick = testConn;
  $("#toggleKey").onclick = () => { const i=$("#s_key"); i.type = i.type==="password"?"text":"password"; };
  $("#settingsOverlay").onclick = e => { if(e.target.id==="settingsOverlay") $("#settingsOverlay").classList.add("hidden"); };

  $("#cfAllow").onclick = () => resolveConfirm(true);
  $("#cfDeny").onclick = () => resolveConfirm(false);

  $("#sendBtn").onclick = () => send();
  $("#stopBtn").onclick = () => S.abort && S.abort.abort();
  $("#input").addEventListener("input", autosize);
  $("#input").addEventListener("keydown", e => {
    if(e.key === "Enter" && !e.shiftKey){ e.preventDefault(); send(); }
  });

  $("#workspace").addEventListener("change", async () => {
    S.settings.default_workspace = $("#workspace").value.trim();
    await fetch("/api/settings",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({default_workspace:S.settings.default_workspace})});
  });
  $("#thinkToggle").addEventListener("change", async () => {
    S.settings.enable_thinking = $("#thinkToggle").checked;
    await fetch("/api/settings",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({enable_thinking:S.settings.enable_thinking})});
  });

  document.querySelectorAll("#welcome .ex").forEach(b => b.onclick = () => { $("#input").value=b.textContent; autosize(); $("#input").focus(); });

  boot();
});