# Backend

## Setup

```bash
cd backend
python -m venv .venv
.venv/Scripts/activate   # or source .venv/bin/activate on macOS/Linux
pip install -r requirements.txt
cp ../.env.example ../.env   # then fill in GROQ_API_KEY / GEMINI_API_KEY
```

## Layout

- `app/contracts` -- canonical shared models (enums, tactic taxonomy, the
  composition-based action rule). Import via `from app.contracts import ...`.
- `app/detection` -- layer-1 regex/keyword tactic detector. Import via
  `from app.detection import detect_tactics, TacticEvent`.
- `app/{agent,camara,incidents,websocket,api,database,recovery}` -- other
  backend modules (in progress).
- `tests/` -- pytest suite, mirrors `app/`.
- `experimental/` -- de-risking scripts and their tests: the ReAct agent
  loop toy, the Groq/Gemini tooling smoke test, and the tactic-detector
  report. Not production code.

## Tests

```bash
cd backend
pytest -q
```

All offline, zero LLM tokens.

## Running experimental scripts

Run as modules from `backend/` so `app`/`tests`/`experimental` resolve as
packages:

```bash
python -m experimental.tactics_report
python -m experimental.verify_tooling      # calls live Groq/Gemini APIs
python -m experimental.agent_loop_toy      # calls live Groq API
```
