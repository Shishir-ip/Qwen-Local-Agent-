import os, sys, json, asyncio, shutil
from asyncio.subprocess import PIPE

IGNORE_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv", "env",
               "dist", "build", ".next", ".nuxt", "target", ".idea", ".vscode",
               ".turbo", "coverage", ".pytest_cache"}
READ_BYTES_CAP = 600_000
READ_LINE_CAP = 2500
UI_CAP = 200_000
CMD_OUT_CAP = 40_000


class ToolContext:
    def __init__(self, workspace, settings, emit, pending):
        self.workspace = workspace
        self.settings = settings
        self.emit = emit            # sync fn(event_dict)
        self._pending = pending     # server-side confirm futures
        self.current_part = None    # set by server before each tool runs
        self.current_proc = None    # set during run_command (for cancel/kill)

    async def confirm(self, tool_id, name, args, summary):
        # Master switch from the composer dropdown: "ask" or "full".
        mode = self.settings.get("permission_mode", "ask")
        if mode == "full":
            return True  # Full Access -> never prompt
        # Ask Permission -> prompt only for state-changing tools
        need = name in ("write_file", "edit_file", "delete_file", "run_command")
        if not need:
            return True
        loop = asyncio.get_event_loop()
        fut = loop.create_future()
        self._pending[tool_id] = fut
        self.emit({"type": "tool_status", "id": tool_id, "status": "confirm"})
        self.emit({"type": "confirm_request", "id": tool_id,
                   "name": name, "args": args, "summary": summary})
        try:
            return await asyncio.wait_for(fut, timeout=self.settings.get("confirm_timeout", 300))
        except asyncio.TimeoutError:
            return False
        finally:
            self._pending.pop(tool_id, None)



def _resolve(ctx, p):
    p = (p or "").strip()
    if not p:
        raise ValueError("Path is empty.")
    p = os.path.expanduser(p)
    if not os.path.isabs(p):
        p = os.path.join(ctx.workspace, p)
    return os.path.normpath(p)


def _cap(s, n=UI_CAP):
    return s if len(s) <= n else s[:n] + f"\n...[truncated {len(s)-n} chars]"


async def read_file(args, ctx):
    path = _resolve(ctx, args["path"])
    if not os.path.exists(path):
        raise FileNotFoundError(f"No such file: {path}")
    if os.path.isdir(path):
        raise IsADirectoryError(f"That is a directory: {path}")
    raw = open(path, "rb").read()
    if b"\x00" in raw[:4096]:
        raise ValueError(f"Binary file, cannot read as text: {path}")
    text = raw.decode("utf-8", "surrogateescape")
    lines = text.splitlines(keepends=True)
    total = len(lines)
    offset = max(1, int(args.get("offset") or 1))
    limit = int(args.get("limit") or READ_LINE_CAP)
    sliced = lines[offset - 1: offset - 1 + limit]
    body = "".join(sliced)
    truncated = (offset - 1 + limit) < total
    meta = ""
    if offset > 1 or truncated:
        meta = (f"# [read_file] showing lines {offset}-{min(offset-1+limit, total)} "
                f"of {total} for: {path}  (this line is METADATA, never put it in edit_file old_text)\n")
    model_text = meta + body
    if truncated:
        model_text += "\n\n[TRUNCATED - more lines exist; call read_file with offset/limit to continue]"
    return {"model_text": _cap(model_text, READ_BYTES_CAP),
            "ui": {"path": path, "content": _cap(body), "total_lines": total,
                   "offset": offset, "limit": limit, "truncated": truncated,
                   "bytes": len(raw)}}


async def write_file(args, ctx):
    path = _resolve(ctx, args["path"])
    existed = os.path.exists(path)
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    content = args["content"]
    with open(path, "w", encoding="utf-8", newline="") as f:
        f.write(content)
    return {"model_text": f"{'Overwrote' if existed else 'Created'} {path} ({len(content)} chars).",
            "ui": {"path": path, "content": _cap(content), "existed": existed, "chars": len(content)}}


async def edit_file(args, ctx):
    path = _resolve(ctx, args["path"])
    old = args["old_text"]
    new = args["new_text"]
    if old == "":
        raise ValueError("old_text is empty; provide the exact text to replace (read the file first).")
    if not os.path.exists(path):
        raise FileNotFoundError(f"No such file: {path}")
    before = open(path, "r", encoding="utf-8", errors="surrogateescape").read()
    count = before.count(old)
    if count == 0:
        raise ValueError("old_text not found in file. Re-read the file and copy an EXACT, unique substring.")
    if count > 1 and not args.get("replace_all", False):
        raise ValueError(f"old_text matches {count} places. Provide more surrounding context, or set replace_all=true.")
    after = before.replace(old, new) if args.get("replace_all", False) else before.replace(old, new, 1)
    with open(path, "w", encoding="utf-8", errors="surrogateescape", newline="") as f:
        f.write(after)
    n = count if args.get("replace_all", False) else 1
    return {"model_text": f"Edited {path}: replaced {n} occurrence(s).",
            "ui": {"path": path, "before": _cap(before), "after": _cap(after), "replacements": n}}


async def delete_file(args, ctx):
    path = _resolve(ctx, args["path"])
    if not os.path.exists(path):
        raise FileNotFoundError(f"No such path: {path}")
    if os.path.isdir(path):
        if os.listdir(path):
            raise ValueError("Directory is not empty. Delete its contents first or use run_command (e.g. rmdir /s /q) with approval.")
        os.rmdir(path)
        kind = "empty_dir"
    else:
        os.remove(path)
        kind = "file"
    return {"model_text": f"Deleted {kind}: {path}", "ui": {"path": path, "kind": kind}}


async def list_directory(args, ctx):
    path = _resolve(ctx, args["path"])
    if not os.path.isdir(path):
        raise NotADirectoryError(f"Not a directory: {path}")
    recursive = bool(args.get("recursive", False))
    max_entries = int(args.get("max_entries") or 1000)
    entries, truncated = [], False

    def add(rel, name, is_dir, size):
        nonlocal truncated
        if len(entries) >= max_entries:
            truncated = True
            return
        entries.append({"name": (rel + name) if rel else name,
                        "type": "dir" if is_dir else "file", "size": size})

    if not recursive:
        try:
            it = sorted(os.scandir(path), key=lambda e: (not e.is_dir(), e.name.lower()))
        except PermissionError as e:
            raise PermissionError(f"Cannot list {path}: {e}")
        for e in it:
            try:
                add("", e.name, e.is_dir(), e.stat().st_size if e.is_file() else 0)
            except OSError:
                add("", e.name, e.is_dir(), 0)
    else:
        for root, dirs, files in os.walk(path):
            dirs[:] = sorted(d for d in dirs if d not in IGNORE_DIRS)
            rel = os.path.relpath(root, path)
            rel = "" if rel == "." else rel.replace(os.sep, "/") + "/"
            for d in sorted(dirs):
                add(rel, d, True, 0)
                if truncated: break
            for fn in sorted(files):
                fp = os.path.join(root, fn)
                try: sz = os.path.getsize(fp)
                except OSError: sz = 0
                add(rel, fn, False, sz)
                if truncated: break
            if truncated: break

    lines = []
    for e in entries:
        tag = "[dir] " if e["type"] == "dir" else "      "
        sz = "" if e["type"] == "dir" else f"  ({e['size']} B)"
        lines.append(f"{tag}{e['name']}{sz}")
    model_text = "\n".join(lines) if lines else "(empty directory)"
    if truncated:
        model_text += f"\n\n[TRUNCATED at {max_entries} entries]"
    return {"model_text": _cap(model_text), "ui": {"path": path, "entries": entries,
                                                   "recursive": recursive, "truncated": truncated}}


async def run_command(args, ctx):
    command = args["command"].strip()
    if not command:
        raise ValueError("command is empty.")
    cwd = _resolve(ctx, args["cwd"]) if args.get("cwd") else ctx.workspace
    if not os.path.isdir(cwd):
        cwd = ctx.workspace
    timeout = min(int(args.get("timeout") or ctx.settings.get("command_timeout", 180)), 1800)
    part = ctx.current_part
    part["stdout"] = ""
    part["stderr"] = ""
    timed_out = False

    flags = 0
    if sys.platform == "win32":
        flags = 0x08000000  # CREATE_NO_WINDOW (no flashing cmd windows)
    try:
        proc = await asyncio.create_subprocess_shell(
            command, stdout=PIPE, stderr=PIPE, cwd=cwd, creationflags=flags)
    except TypeError:
        proc = await asyncio.create_subprocess_shell(
            command, stdout=PIPE, stderr=PIPE, cwd=cwd)
    ctx.current_proc = proc

    async def pump(stream, label):
        while True:
            line = await stream.readline()
            if not line:
                break
            t = line.decode("utf-8", "replace")
            part[label] += t
            ctx.emit({"type": "tool_output", "id": part["id"], "stream": label, "text": t})

    try:
        try:
            await asyncio.wait_for(
                asyncio.gather(pump(proc.stdout, "stdout"),
                               pump(proc.stderr, "stderr"), proc.wait()),
                timeout=timeout)
        except asyncio.TimeoutError:
            timed_out = True
            try: proc.kill()
            except ProcessLookupError: pass
            await proc.wait()
    except asyncio.CancelledError:
        try: proc.kill()
        except ProcessLookupError: pass
        raise
    finally:
        ctx.current_proc = None

    rc = proc.returncode
    out, err = part["stdout"], part["stderr"]
    model = ""
    if out: model += "--- stdout ---\n" + out
    if err: model += ("\n" if model else "") + "--- stderr ---\n" + err
    if not model: model = "(no output)"
    model += f"\n[exit code: {rc}]"
    if timed_out: model += f" [TIMED OUT after {timeout}s - process killed]"
    return {"model_text": _cap(model, CMD_OUT_CAP),
            "ui": {"command": command, "cwd": cwd, "returncode": rc,
                   "stdout": _cap(out), "stderr": _cap(err), "timed_out": timed_out}}


EXEC = {"read_file": read_file, "write_file": write_file, "edit_file": edit_file,
        "delete_file": delete_file, "list_directory": list_directory, "run_command": run_command}


async def execute(name, args, ctx):
    fn = EXEC.get(name)
    if not fn:
        raise ValueError(f"Unknown tool: {name}")
    return await fn(args, ctx)


TOOL_SCHEMAS = [
 {"type": "function", "function": {"name": "list_directory",
   "description": "List files/folders in a directory. Use to explore a project. Set recursive=true to walk the tree (node_modules/.git/etc. are auto-skipped). Relative paths use the workspace.",
   "parameters": {"type": "object", "properties": {
       "path": {"type": "string", "description": "Directory path (relative to workspace or absolute)."},
       "recursive": {"type": "boolean", "description": "Walk subdirectories. Default false."},
       "max_entries": {"type": "integer", "description": "Cap entries returned. Default 1000."}},
       "required": ["path"]}}},
 {"type": "function", "function": {"name": "read_file",
   "description": "Read a text file. ALWAYS read before editing. Use offset/limit for big files. A leading line starting with '# [read_file]' is metadata, NOT file content - never include it in edit_file old_text.",
   "parameters": {"type": "object", "properties": {
       "path": {"type": "string"},
       "offset": {"type": "integer", "description": "1-based start line."},
       "limit": {"type": "integer", "description": "Max lines to read."}},
       "required": ["path"]}}},
 {"type": "function", "function": {"name": "write_file",
   "description": "Create a new file or fully overwrite one. Prefer edit_file for modifying existing files. Parent folders are created automatically.",
   "parameters": {"type": "object", "properties": {
       "path": {"type": "string"}, "content": {"type": "string", "description": "Full file content."}},
       "required": ["path", "content"]}}},
 {"type": "function", "function": {"name": "edit_file",
   "description": "Make a SURGICAL edit by replacing an exact substring. old_text must match the file verbatim and be unique (else add more context or set replace_all=true). Read the file first.",
   "parameters": {"type": "object", "properties": {
       "path": {"type": "string"},
       "old_text": {"type": "string", "description": "Exact text to find (verbatim)."},
       "new_text": {"type": "string", "description": "Replacement text."},
       "replace_all": {"type": "boolean", "description": "Replace every occurrence. Default false."}},
       "required": ["path", "old_text", "new_text"]}}},
 {"type": "function", "function": {"name": "delete_file",
   "description": "Delete a file, or an EMPTY directory. Non-empty dirs are refused for safety.",
   "parameters": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}}},
 {"type": "function", "function": {"name": "run_command",
   "description": "Run a shell command (cmd.exe on Windows) and stream its output. Use to build, test, lint, run git, install deps, etc. For PowerShell-only commands prefix with: powershell -NoProfile -Command \"...\". Avoid starting long-lived servers (respect timeout).",
   "parameters": {"type": "object", "properties": {
       "command": {"type": "string", "description": "The command line to execute."},
       "cwd": {"type": "string", "description": "Working dir (default workspace)."},
       "timeout": {"type": "integer", "description": "Seconds before kill. Default from settings."}},
       "required": ["command"]}}},
]