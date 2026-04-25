# Calendar Scheduling Agent Design Doc

## Goal

Build a working tool-runner agent that can interpret natural-language scheduling requests, find available time slots, create meetings, detect conflicts, and reschedule existing meetings while handling time zones safely.

## Architecture

The project uses a local Python HTTP server as the tool runner and UI host.

- Frontend: single-page browser UI for entering natural-language requests and displaying tool traces
- Agent layer: LM Studio local LLM planner when enabled, with a rule-based fallback parser
- Tool layer: HTTP endpoints for availability lookup, event creation, deletion, and rescheduling
- Calendar backend: direct Google Calendar REST API calls using an OAuth access token
- Fallback mode: seeded demo data used when no Google access token is configured

## Key design decisions

### 1. Real tool endpoints instead of an all-in-one script

The brief is specifically a tool runner project, so the code exposes separate HTTP endpoints for each calendar action. This makes the system prompt and tool schema submission straightforward and keeps the agent behavior inspectable.

### 2. Real AI request interpretation with a safe fallback

When LM Studio is running, the server sends the user request to the local `chat/completions` endpoint and asks the model to return a structured JSON parse of intent, attendees, time references, and clarification needs. That means the system is using an actual model for the natural-language understanding step instead of relying only on hand-written parsing rules.

If the LM Studio call fails or is disabled, the app falls back to a rule-based parser so the demo remains usable offline.

### 3. Dependency-free external API integration

I used Python standard-library HTTP calls for both LM Studio and Google Calendar instead of third-party SDKs. This keeps the project easier to run in a clean environment and makes the actual REST contracts visible in the code.

### 4. Confirmation gating for destructive changes

Delete and reschedule operations create a pending action first. The agent only executes the final write after explicit confirmation, which directly matches the project brief.

### 5. Time zone-first presentation

The UI always has PT, ET, and KST preview cards so the grader can immediately verify cross-time-zone handling. The slot summaries are also rendered in those three zones.

### 6. Demo mode for reliable presentation

The course project requires live calendar tooling, but demos often fail because of credentials. To avoid a broken presentation, the same app can run against seeded data when credentials are absent. With a valid `GOOGLE_ACCESS_TOKEN`, the exact same tool endpoints switch to live mode.

## Limitations

- The LLM is currently used for request understanding, not full autonomous multi-step planning.
- Live multi-participant conflict detection depends on the OAuth identity having free/busy access to the participants' calendars.
- Pending confirmations are stored in server memory only.
- Access-token refresh is not implemented; the current version expects a valid token to be supplied.

## Next improvements

- Add OAuth authorization-code flow and refresh-token handling.
- Expand parsing to handle more ambiguous requests and chained constraints.
- Add recurring-event support.
- Add persistence for pending actions and audit logs.
- Replace the rule-based parser with an LLM planner while keeping the same tool contracts.
