# Project 3 Submission Checklist (Calendar Scheduling Agent)

Use this checklist before Milestone 4 upload.

## 1) Required artifacts

- [ ] `system_prompt.md`
- [ ] `tool_definitions.json`
- [ ] `seed_examples.json` (20 examples)
- [ ] `design_doc.md` (about 1 page)
- [ ] Project code zip file
- [ ] Tool endpoint base URL and route list for evaluator
- [ ] Test account details for evaluator

## 2) Live Google Calendar proof (must pass)

- [ ] `.env` or shell has `GOOGLE_ACCESS_TOKEN` set
- [ ] `GOOGLE_CALENDAR_ID` is set (usually `primary`)
- [ ] Server starts with `python3 server.py`
- [ ] `GET /api/config.json` shows `"demo_mode": false`
- [ ] Real run of `get_available_slots` succeeds
- [ ] Real run of `create_event` succeeds
- [ ] Real run of `reschedule_event` succeeds (with confirmation)
- [ ] Real run of `delete_event` succeeds (with confirmation)

## 3) Time zone and conflict requirements

- [ ] PT / ET / KST conversions are visible in outputs
- [ ] Conflict case returns alternatives instead of forcing a booking
- [ ] No-slot case is handled gracefully
- [ ] Ambiguous request triggers clarification question
- [ ] Existing-event modification asks for explicit confirmation

## 4) Evaluator handoff package

- [ ] Base URL for tool endpoints (example: `http://<host>:8080`)
- [ ] Endpoint list:
  - `POST /api/tools/get_available_slots`
  - `POST /api/tools/create_event`
  - `POST /api/tools/delete_event`
  - `POST /api/tools/reschedule_event`
- [ ] Evaluator account / test calendar access instructions
- [ ] Known limitations disclosed (token refresh, external free/busy access limits, etc.)

## 5) Final self-test script (quick)

1. Start server.
2. Check config: `curl -s http://127.0.0.1:8080/api/config.json`
3. Book from UI or `POST /api/agent/run`.
4. Confirm with `POST /api/agent/confirm`.
5. Verify event appears in Google Calendar UI.
6. Reschedule then delete the same event.
7. Save screenshots or logs for submission evidence.
