import os, sys, json, asyncio, platform, uuid, time
from pathlib import Path
import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import StreamingResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from openai import AsyncOpenAI
import tools

BASE = Path(__file__).resolve().parent
CONFIG_PATH = BASE / "config.json"
SESSIONS_PATH = BASE / "sessions.json"
SENTINEL = object()

DEFAULTS = {"api_key": "", "base_url": "https://dashscope-intl.aliyuncs.com/compatible-mode/v1",
            "model": "qwen3.7-max", "default_workspace": "", "enable_thinking": True,
            "confirm_file_changes": True, "confirm_commands": True, "confirm_timeout": 300,
            "max_turns": 30, "command_timeout": 180, "permission_mode": "ask"}


def load_config():
    if CONFIG_PATH.exists():
        try:
            d = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        except Exception:
            d = {}
    else:
        d = {}
    cfg = {**DEFAULTS, **d}
    return cfg


def save_config(cfg):
    CONFIG_PATH.write_text(json.dumps(cfg, indent=2, ensure_ascii=False), encoding="utf-8")


def load_sessions():
    if SESSIONS_PATH.exists():
        try:
            return json.loads(SESSIONS_PATH.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def save_sessions():
    SESSIONS_PATH.write_text(json.dumps(SESSIONS, indent=2, ensure_ascii=False), encoding="utf-8")


SETTINGS = load_config()
SESSIONS = load_sessions()
PENDING = {}   # confirm_id -> asyncio.Future


def effective_workspace():
    return SETTINGS.get("default_workspace") or os.getcwd()


def system_prompt():
    ws = effective_workspace()
    osinfo = f"{platform.system()} {platform.release()} ({platform.machine()})"
    tpl = """You are Qwen3.7-Max running as a LOCAL coding agent on the user's computer. You can read/write/edit/delete files and run shell commands via the provided tools.

Current workspace (default dir for relative paths and commands): {{WORKSPACE}}
Operating system: {{OS}}

Hard rules:
- ALWAYS read_file before edit_file. Never guess file contents.
- Prefer edit_file (exact substring replace) over write_file for existing files. Use write_file only for new files or full rewrites.
- Make the SMALLEST correct change; preserve formatting and unrelated code.
- Relative paths resolve to the workspace above; absolute paths (e.g. C:\\...) are allowed.
- To explore a project: list_directory first (recursive=true to walk; heavy dirs auto-skipped), then read_file the relevant files.
- Bug-fixing workflow: locate files by listing/reading -> find root cause -> apply a minimal edit_file -> VERIFY with run_command (build/test/lint/run) when possible -> report what and why.
- If a fix needs an action you cannot do yourself (a Supabase/DB SQL change, a cloud-console setting, a paid service), do NOT pretend. Give the user the EXACT command/query/steps in a fenced code block, ask them to run it, then continue once they confirm.
- run_command uses cmd.exe; for PowerShell-only syntax prefix with: powershell -NoProfile -Command "...". Keep commands bounded (timeouts apply).
- State-changing tools (write/edit/delete/run) may pop an approval dialog. If the user DENIES, do not retry the same action; explain and propose an alternative.
- Be concise but complete. Use Markdown and fenced code blocks. After editing, summarize changes as a short bullet list.
- Reason step by step, act with tools, then give a clear final answer."""
    return tpl.replace("{{WORKSPACE}}", ws).replace("{{OS}}", osinfo)


def get_reasoning(delta):
    r = getattr(delta, "reasoning_content", None)
    if r:
        return r
    extra = getattr(delta, "model_extra", None) or {}
    if isinstance(extra, dict) and extra.get("reasoning_content"):
        return extra["reasoning_content"]
    return ""


def make_summary(name, args):
    if name == "run_command":
        return f"$ {str(args.get('command',''))[:200]}"
    if name in ("read_file", "write_file", "edit_file", "delete_file", "list_directory"):
        extra = ""
        if name == "edit_file":
            extra = f"  (replace {str(args.get('old_text',''))[:60]!r} -> {str(args.get('new_text',''))[:60]!r})"
        return f"{args.get('path','')}{extra}"
    return json.dumps(args, ensure_ascii=False)[:200]


async def run_agent(session, user_message, emit, q):
    cfg = SETTINGS
    if not cfg.get("api_key"):
        emit({"type": "error", "message": "No API key set. Open Settings (gear icon) and paste your Model Studio API key."})
        return
    client = AsyncOpenAI(api_key=cfg["api_key"], base_url=cfg["base_url"], timeout=600.0)
    msgs = session["model_messages"]
    msgs.append({"role": "user", "content": user_message})
    cur = {"role": "assistant", "parts": []}
    session["display"].append(cur)

    def ensure_part(ptype):
        if cur["parts"] and cur["parts"][-1]["type"] == ptype:
            return cur["parts"][-1]
        p = {"type": ptype, "text": ""}
        cur["parts"].append(p)
        return p

    ctx = tools.ToolContext(workspace=effective_workspace(), settings=cfg, emit=emit, pending=PENDING)
    thinking_on = bool(cfg.get("enable_thinking", True))
    max_turns = int(cfg.get("max_turns", 30))

    for turn in range(max_turns):
        kwargs = dict(model=cfg["model"], messages=msgs, tools=tools.TOOL_SCHEMAS,
                      stream=True, stream_options={"include_usage": True},
                      extra_body={"enable_thinking": thinking_on})
        try:
            stream = await client.chat.completions.create(**kwargs)
        except Exception as e:
            emit({"type": "error", "message": f"Model API error: {e}"})
            return

        reasoning, content, tool_acc, finish, usage = "", "", {}, None, None
        try:
            async for chunk in stream:
                if not getattr(chunk, "choices", None):
                    usage = getattr(chunk, "usage", None)
                    continue
                ch = chunk.choices[0]
                delta = ch.delta
                finish = ch.finish_reason or finish
                r = get_reasoning(delta)
                if r:
                    reasoning += r
                    ensure_part("thinking")["text"] += r
                    emit({"type": "thinking_delta", "text": r})
                c = getattr(delta, "content", None)
                if c:
                    content += c
                    ensure_part("content")["text"] += c
                    emit({"type": "content_delta", "text": c})
                tcs = getattr(delta, "tool_calls", None)
                if tcs:
                    for tc in tcs:
                        slot = tool_acc.setdefault(tc.index, {"id": "", "name": "", "args": ""})
                        if getattr(tc, "id", None):
                            slot["id"] = tc.id
                        fn = getattr(tc, "function", None)
                        if fn:
                            if getattr(fn, "name", None):
                                slot["name"] += fn.name
                            if getattr(fn, "arguments", None):
                                slot["args"] += fn.arguments
        except asyncio.CancelledError:
            emit({"type": "error", "message": "Generation stopped."})
            save_sessions()
            return
        except Exception as e:
            emit({"type": "error", "message": f"Stream error: {e}"})
            save_sessions()
            return

        # build assistant history message (echo reasoning_content for tool turns!)
        am = {"role": "assistant", "content": content if content else ""}
        if reasoning:
            am["reasoning_content"] = reasoning
        tc_list = []
        for i in sorted(tool_acc):
            s = tool_acc[i]
            tc_list.append({"id": s["id"] or f"call_{turn}_{i}", "type": "function",
                            "function": {"name": s["name"], "arguments": s["args"] or "{}"}})
        if tc_list:
            am["tool_calls"] = tc_list
        msgs.append(am)

        if usage:
            emit({"type": "usage",
                  "input": getattr(usage, "prompt_tokens", None),
                  "output": getattr(usage, "completion_tokens", None),
                  "total": getattr(usage, "total_tokens", None)})

        if not tc_list:
            save_sessions()
            return  # final answer

        # execute tool calls sequentially
        for s in (tool_acc[i] for i in sorted(tool_acc)):
            tc_id, name, raw = s["id"] or f"call_{turn}", s["name"], s["args"]
            try:
                args = json.loads(raw) if raw else {}
            except Exception:
                args = {"_raw": raw}
            emit({"type": "tool_call", "id": tc_id, "name": name, "args": args})
            part = {"type": "tool", "id": tc_id, "name": name, "status": "running",
                    "args": args, "ui": None, "stdout": "", "stderr": ""}
            cur["parts"].append(part)
            ctx.current_part = part

            allowed = await ctx.confirm(tc_id, name, args, make_summary(name, args))
            if not allowed:
                part["status"] = "denied"
                part["ui"] = {"denied": True}
                emit({"type": "tool_status", "id": tc_id, "status": "denied"})
                emit({"type": "tool_result", "id": tc_id, "status": "denied", "ui": part["ui"]})
                result_text = "The user DENIED this operation. Do NOT retry it. Explain and propose a safe alternative, or ask the user."
            else:
                emit({"type": "tool_status", "id": tc_id, "status": "running"})
                try:
                    res = await tools.execute(name, args, ctx)
                    part["ui"] = res["ui"]
                    part["status"] = "success"
                    emit({"type": "tool_result", "id": tc_id, "status": "success", "ui": res["ui"]})
                    result_text = res["model_text"]
                except asyncio.CancelledError:
                    part["status"] = "error"
                    part["ui"] = {"error": "cancelled"}
                    emit({"type": "tool_result", "id": tc_id, "status": "error", "ui": part["ui"]})
                    result_text = "ERROR: operation cancelled by user."
                    msgs.append({"role": "tool", "tool_call_id": tc_id, "content": result_text})
                    save_sessions()
                    raise
                except Exception as e:
                    part["status"] = "error"
                    part["ui"] = {"error": str(e)}
                    emit({"type": "tool_result", "id": tc_id, "status": "error", "ui": part["ui"]})
                    result_text = f"ERROR: {e}"
            msgs.append({"role": "tool", "tool_call_id": tc_id, "content": result_text})
            save_sessions()
    emit({"type": "error", "message": f"Reached max tool turns ({max_turns}). Stopping."})
    save_sessions()


# ----------------------------- FastAPI app -----------------------------
app = FastAPI()


@app.get("/api/settings")
async def get_settings():
    return SETTINGS


@app.post("/api/settings")
async def post_settings(req: Request):
    global SETTINGS
    d = await req.json()
    for k in DEFAULTS:
        if k in d:
            SETTINGS[k] = d[k]
    save_config(SETTINGS)
    return {"ok": True, "settings": SETTINGS}


@app.post("/api/test")
async def test_conn(req: Request):
    cfg = SETTINGS
    if not cfg.get("api_key"):
        return {"ok": False, "error": "API key is empty."}
    try:
        client = AsyncOpenAI(api_key=cfg["api_key"], base_url=cfg["base_url"], timeout=30.0)
        r = await client.chat.completions.create(
            model=cfg["model"], messages=[{"role": "user", "content": "ping"}],
            extra_body={"enable_thinking": False}, max_tokens=5)
        return {"ok": True, "model": cfg["model"],
                "reply": (r.choices[0].message.content or "").strip()[:80]}
    except Exception as e:
        return {"ok": False, "error": str(e)}


@app.get("/api/sessions")
async def list_sessions():
    out = []
    for sid, s in SESSIONS.items():
        out.append({"id": sid, "title": s.get("title", "New chat"), "updated": s.get("updated", 0)})
    out.sort(key=lambda x: x["updated"], reverse=True)
    return out


@app.get("/api/sessions/{sid}")
async def get_session(sid: str):
    s = SESSIONS.get(sid)
    if not s:
        return JSONResponse({"error": "not found"}, 404)
    return {"id": sid, "title": s.get("title"), "display": s.get("display", [])}


@app.delete("/api/sessions/{sid}")
async def del_session(sid: str):
    SESSIONS.pop(sid, None)
    save_sessions()
    return {"ok": True}


@app.post("/api/confirm")
async def confirm(req: Request):
    d = await req.json()
    cid, allowed = d.get("id"), bool(d.get("allowed"))
    fut = PENDING.get(cid)
    if fut and not fut.done():
        fut.set_result(allowed)
    return {"ok": True}


@app.post("/api/chat")
async def chat(req: Request):
    d = await req.json()
    sid = d.get("session_id")
    message = (d.get("message") or "").strip()
    if not message:
        return JSONResponse({"error": "empty message"}, 400)
    if not sid or sid not in SESSIONS:
        sid = uuid.uuid4().hex
        SESSIONS[sid] = {"id": sid, "title": message[:48], "display": [],
                         "model_messages": [{"role": "system", "content": system_prompt()}],
                         "created": time.time(), "updated": time.time()}
    session = SESSIONS[sid]
    session["updated"] = time.time()
    # refresh system prompt workspace each turn
    if session["model_messages"] and session["model_messages"][0]["role"] == "system":
        session["model_messages"][0]["content"] = system_prompt()
    save_sessions()

    queue = asyncio.Queue()

    def emit(ev):
        try:
            queue.put_nowait(ev)
        except Exception:
            pass

    async def wrapper():
        try:
            emit({"type": "session", "id": sid})
            await run_agent(session, message, emit, queue)
        except asyncio.CancelledError:
            pass
        except Exception as e:
            emit({"type": "error", "message": f"Internal error: {e}"})
        finally:
            session["updated"] = time.time()
            save_sessions()
            queue.put_nowait(SENTINEL)

    task = asyncio.create_task(wrapper())

    async def gen():
        try:
            while True:
                if await req.is_disconnected():
                    task.cancel()
                    break
                try:
                    ev = await asyncio.wait_for(queue.get(), timeout=1.0)
                except asyncio.TimeoutError:
                    continue
                if ev is SENTINEL:
                    break
                yield f"data: {json.dumps(ev, ensure_ascii=False)}\n\n"
        finally:
            if not task.done():
                task.cancel()

    return StreamingResponse(gen(), media_type="text/event-stream")


app.mount("/", StaticFiles(directory=str(BASE / "static"), html=True), name="static")

if __name__ == "__main__":
    print("\n  Qwen Local Agent -> http://127.0.0.1:8765\n")
    uvicorn.run(app, host="127.0.0.1", port=8765, log_level="info")