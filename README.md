# Qwen Local Agent

A local FastAPI app that provides a browser UI for running a Qwen coding assistant against files in your workspace.

## Project files

- `/home/runner/work/Qwen-Local-Agent-/Qwen-Local-Agent-/server.py` - backend API and streaming chat server
- `/home/runner/work/Qwen-Local-Agent-/Qwen-Local-Agent-/tools.py` - filesystem and command tools used by the agent
- `/home/runner/work/Qwen-Local-Agent-/Qwen-Local-Agent-/static/index.html` - UI shell
- `/home/runner/work/Qwen-Local-Agent-/Qwen-Local-Agent-/static/app.js` - UI logic and streaming client
- `/home/runner/work/Qwen-Local-Agent-/Qwen-Local-Agent-/static/style.css` - UI styles
- `/home/runner/work/Qwen-Local-Agent-/Qwen-Local-Agent-/requirements.txt` - Python dependencies
- `/home/runner/work/Qwen-Local-Agent-/Qwen-Local-Agent-/start.bat` - one-click Windows startup script
- `/home/runner/work/Qwen-Local-Agent-/Qwen-Local-Agent-/config.example.json` - safe config template

## Requirements

- Python 3.9+
- Internet access to your model endpoint
- A valid API key for your configured OpenAI-compatible provider

## Setup

1. Open a terminal in `/home/runner/work/Qwen-Local-Agent-/Qwen-Local-Agent-`.
2. Create and activate a virtual environment.
3. Install dependencies:
   - `pip install -r requirements.txt`
4. Create local config:
   - Copy `config.example.json` to `config.json`
   - Add your API key in `config.json` or via the Settings panel after launch

## Run

### Windows (recommended)

- Double-click `start.bat`, or run:
  - `start.bat`

### macOS/Linux/WSL

Run:

- `python server.py`

Then open:

- `http://127.0.0.1:8765`

## How it works

- The frontend connects to `/api/chat` using server-sent events.
- Chat sessions are stored locally in `sessions.json`.
- Settings are stored locally in `config.json`.
- Tool actions (`edit`, `delete`, `run command`) can require approval depending on permission mode.

## Security and privacy notes

- Do **not** commit `config.json` or `sessions.json`.
- Keep your API key private; never share it in code, screenshots, or logs.
- This repository now ignores local secret/state files through `.gitignore`.

## Missing or optional files

- `README.md` was missing and has now been added.
- `.gitignore` was missing and has now been added.
- `config.json` is intentionally not tracked; generate it locally from `config.example.json`.
