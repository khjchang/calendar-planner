# Calendar Scheduling Agent

Project 3 implementation for the calendar scheduling agent brief.

## What this version includes

- Local tool-runner server with real HTTP endpoints
- Natural-language agent flow for booking, deleting, and rescheduling
- Conflict detection before writes
- Confirmation required before delete and reschedule
- PT / ET / KST rendering
- Multi-participant free/busy support
- Demo mode when no Google credential is configured
- Submission artifacts: system prompt, tool schemas, design doc, seed examples, headroom tasks

## Files

- App UI: [index.html](index.html)
- Frontend logic: [app.js](app.js)
- Tool server: [server.py](server.py)
- Env template: [.env.example](.env.example)
- Prompt: [system_prompt.md](system_prompt.md)
- Tool schema: [tool_definitions.json](tool_definitions.json)
- Design doc: [design_doc.md](design_doc.md)
- Seed examples: [seed_examples.json](seed_examples.json)
- Headroom tasks: [headroom_tasks.md](headroom_tasks.md)

## Run

```bash
python3 server.py
```

Then open `http://127.0.0.1:8080`.

To run on a different port:

```bash
PORT=8081 python3 server.py
```

## Live Google Calendar setup

This project is dependency-free and uses direct REST calls to Google Calendar.

For course submission, do not rely on demo mode only. You should run and demonstrate the tool flow with a real Google Calendar account.

1. Create a Google Cloud project.
2. Enable the Google Calendar API.
3. Configure the OAuth consent screen.
4. Create OAuth credentials for a desktop app or web app.
5. Obtain an OAuth access token with a Calendar scope.
6. Export the environment variables from [.env.example](.env.example).

Minimum useful scopes:

- `https://www.googleapis.com/auth/calendar`
- `https://www.googleapis.com/auth/calendar.events`
- `https://www.googleapis.com/auth/calendar.freebusy`

If `GOOGLE_ACCESS_TOKEN` is empty, the server falls back to demo mode automatically.

### Required environment for live mode

Set these in your shell before starting the server:

- `GOOGLE_ACCESS_TOKEN`
- `GOOGLE_CALENDAR_ID` (use `primary` unless your grader uses another calendar)
- `DEFAULT_TIMEZONE` (for example `America/Los_Angeles`)

Quick check after server start:

```bash
curl -s http://127.0.0.1:8080/api/config.json
```

Confirm that `"demo_mode": false` is shown in the JSON response.

### Minimal live verification sequence

Run these once with a real token to prove end-to-end tool behavior:

1. `POST /api/tools/get_available_slots` returns candidate slots.
2. `POST /api/tools/create_event` creates a real event.
3. `POST /api/tools/reschedule_event` moves that event after confirmation flow.
4. `POST /api/tools/delete_event` removes that event after confirmation flow.

You can use the UI trace panel or `curl` responses as evidence for Milestone 4.

## LM Studio planner setup

To make the app use your local LM Studio model for request interpretation, start the LM Studio local server and export:

- `LLM_ENABLED=1`
- `LLM_BASE_URL=http://127.0.0.1:1234/v1`
- `LLM_API_KEY=lm-studio`
- `LLM_MODEL=google/gemma-3-12b`

If LM Studio is not running or the model call fails, the server falls back to the local rule-based parser so the demo still works.

## Local endpoints

- `POST /api/tools/get_available_slots`
- `POST /api/tools/create_event`
- `POST /api/tools/delete_event`
- `POST /api/tools/reschedule_event`
- `POST /api/agent/run`
- `POST /api/agent/confirm`
- `GET /api/calendar/state`
- `GET /api/config` (human-readable status page)
- `GET /api/config.json` (raw API JSON for scripts/checks)

## Notes

- In live mode, participant free/busy checks only work for calendars the OAuth identity can query.
- This server keeps pending confirmations in memory, so restarting the server clears them.
- The natural-language parser is intentionally lightweight; the course-facing agent behavior is represented by the prompt plus tool orchestration.
- For final grading readiness, see [SUBMISSION_CHECKLIST.md](SUBMISSION_CHECKLIST.md).
