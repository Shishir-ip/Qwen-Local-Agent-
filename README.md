# Qwen Local Agent

A local FastAPI app that provides a browser UI for running a Qwen coding assistant against files in your workspace.

## Project Files

- `server.py` - Backend API and streaming chat server
- `tools.py` - Filesystem and command tools used by the agent
- `static/index.html` - UI shell
- `static/app.js` - UI logic and streaming client
- `static/style.css` - UI styles
- `requirements.txt` - Python dependencies
- `start.bat` - One-click Windows startup script
- `config.example.json` - Safe config template

## Requirements

- Python 3.9+
- Internet access to your model endpoint
- A valid API key for your configured OpenAI-compatible provider

## Setup

1. Open a terminal in the project directory.
2. Create and activate a virtual environment:
   - `python -m venv venv`
   - Windows: `venv\Scripts\activate`
   - macOS/Linux: `source venv/bin/activate`
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

```bash
python server.py
```

Then open in your browser:

```
http://127.0.0.1:8765
```

## How it works

- The frontend connects to `/api/chat` using server-sent events.
- Chat sessions are stored locally in `sessions.json`.
- Settings are stored locally in `config.json`.
- Tool actions (`edit`, `delete`, `run command`) can require approval depending on permission mode.

## Security and privacy notes

- Do **not** commit `config.json` or `sessions.json`.
- Keep your API key private; never share it in code, screenshots, or logs.
- This repository now ignores local secret/state files through `.gitignore`.

## Missing or Optional Files

- `.gitignore` was missing and has now been added.
- `config.json` is intentionally not tracked; generate it locally from `config.example.json`.
- `sessions.json` is created at runtime to store chat sessions.
