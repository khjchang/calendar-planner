const DISPLAY_ZONES = [
  { label: "PT", value: "America/Los_Angeles" },
  { label: "ET", value: "America/New_York" },
  { label: "KST", value: "Asia/Seoul" },
];

let lastResponse = null;
let pendingConfirmationToken = null;

const els = {
  request: document.querySelector("#request"),
  run: document.querySelector("#run-agent"),
  reset: document.querySelector("#reset-demo"),
  trace: document.querySelector("#tool-trace"),
  result: document.querySelector("#result-card"),
  calendar: document.querySelector("#calendar-list"),
  zone: document.querySelector("#display-zone"),
  preview: document.querySelector("#timezone-preview"),
  modeBadge: document.querySelector("#mode-badge"),
  traceBadge: document.querySelector("#trace-badge"),
};

document.querySelectorAll(".prompt-chip").forEach((button) => {
  button.addEventListener("click", () => {
    els.request.value = button.dataset.prompt;
  });
});

els.run.addEventListener("click", runAgent);
els.reset.addEventListener("click", resetDemo);
els.zone.addEventListener("change", renderCalendar);

async function runAgent() {
  const request = els.request.value.trim();

  if (!request) {
    renderResult({
      message: "Please enter a scheduling request.",
      detail: "The server expects a natural-language booking, deletion, or rescheduling request.",
    });
    return;
  }

  els.run.disabled = true;
  try {
    const response = await postJson("/api/agent/run", { request });
    handleResponse(response);
  } catch (error) {
    renderResult({
      message: "The agent request failed.",
      detail: error.message,
    });
  } finally {
    els.run.disabled = false;
  }
}

async function confirmAction(index) {
  if (!pendingConfirmationToken) {
    return;
  }

  try {
    const response = await postJson("/api/agent/confirm", {
      token: pendingConfirmationToken,
      option_index: index,
    });
    handleResponse(response);
  } catch (error) {
    renderResult({
      message: "Confirmation failed.",
      detail: error.message,
    });
  }
}

async function resetDemo() {
  try {
    const response = await postJson("/api/demo/reset", {});
    handleResponse({
      ...response,
      message: "Demo calendar reset complete.",
      detail: "Seeded events and pending actions were restored.",
    });
  } catch (error) {
    renderResult({
      message: "Reset failed.",
      detail: error.message,
    });
  }
}

function handleResponse(response) {
  lastResponse = response;
  pendingConfirmationToken = response.confirmation_token || null;
  maybeShowAlert(response);
  renderResult(response);
  renderTrace(response.tool_trace || []);
  renderCalendar();
  renderPreview(response.preview || []);
}

function renderResult(response) {
  const suggestions = response.suggestions || [];
  const actionLabel = response.status === "needs_confirmation" ? "Confirm This Option" : "Use This Option";
  const suggestionsHtml = suggestions.length
    ? `<div class="suggestions">${suggestions
        .map(
          (suggestion, index) => `
            <div class="suggestion">
              <strong>Option ${index + 1}</strong>
              <div><code>${escapeHtml(suggestion.summary)}</code></div>
              ${pendingConfirmationToken ? `<button data-index="${index}">${actionLabel}</button>` : ""}
            </div>
          `,
        )
        .join("")}</div>`
    : "";

  els.result.innerHTML = `
    <h3>${escapeHtml(response.message || "Ready")}</h3>
    <p class="muted" style="margin-top:10px;">${escapeHtml(response.detail || "")}</p>
    ${suggestionsHtml}
  `;

  els.result.querySelectorAll("button[data-index]").forEach((button) => {
    button.addEventListener("click", () => confirmAction(Number(button.dataset.index)));
  });
}

function renderTrace(toolTrace) {
  if (!toolTrace.length) {
    els.trace.innerHTML = `<p class="muted">No tool calls yet.</p>`;
    return;
  }

  els.trace.innerHTML = toolTrace
    .map(
      (item) => `
        <div class="trace-item">
          <strong>${escapeHtml(item.tool)}</strong>
          <div class="event-meta">input: <code>${escapeHtml(JSON.stringify(item.input))}</code></div>
          <div class="event-meta">output: <code>${escapeHtml(JSON.stringify(item.output))}</code></div>
        </div>
      `,
    )
    .join("");
}

async function renderCalendar() {
  try {
    const response = await fetch("/api/calendar/state");
    const state = await response.json();
    const zone = els.zone.value;

    els.calendar.innerHTML = state.events
      .map(
        (event) => `
          <div class="event-card">
            <h3>${escapeHtml(event.title)}</h3>
            <div class="event-meta">${escapeHtml(formatDateTime(event.start, zone))} - ${escapeHtml(
              formatDateTime(event.end, zone),
            )}</div>
            <div class="event-meta">Attendees: ${escapeHtml((event.attendees || []).join(", "))}</div>
            <div class="event-meta">ID: ${escapeHtml(event.id)}</div>
          </div>
        `,
      )
      .join("");
  } catch (error) {
    els.calendar.innerHTML = `<p class="muted">Failed to load calendar state.</p>`;
  }
}

function renderPreview(preview) {
  if (!preview.length) {
    els.preview.innerHTML = `<p class="muted">No preview available.</p>`;
    return;
  }

  els.preview.innerHTML = preview
    .map(
      (item) => `
        <div class="zone-card">
          <h3>${escapeHtml(item.label)}</h3>
          <div class="zone-time">${escapeHtml(item.start)}</div>
          <div class="zone-time">${escapeHtml(item.end)}</div>
        </div>
      `,
    )
    .join("");
}

function formatDateTime(dateLike, zone) {
  return new Intl.DateTimeFormat("en-US", {
    timeZone: zone,
    weekday: "short",
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
  }).format(new Date(dateLike));
}

function escapeHtml(text) {
  return String(text)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;");
}

function maybeShowAlert(response) {
  if (response.status === "already_exists") {
    window.alert(`이미 비슷한 일정이 있습니다.\n\n${response.detail || response.message}`);
  }
}

async function postJson(url, body) {
  const response = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  const payload = await response.json();
  if (!response.ok) {
    throw new Error(payload.detail || payload.message || `HTTP ${response.status}`);
  }
  return payload;
}

async function bootstrap() {
  try {
    const response = await fetch("/api/config.json");
    const config = await response.json();
    const dataMode = config.demo_mode ? "Demo Data" : "Google Calendar Live";
    const parserMode = config.ai_enabled ? `LLM ${config.llm_model}` : "Rule Parser";
    els.modeBadge.textContent = dataMode;
    els.traceBadge.textContent = parserMode;
    renderResult({
      message: `${dataMode} / ${parserMode}`,
      detail: config.ai_enabled
        ? `Requests are interpreted by your local LLM server at ${config.llm_base_url} before the calendar tools run.`
        : "LLM parsing is disabled, so the app is using the local rule-based parser.",
    });
    renderCalendar();
  } catch (error) {
    renderResult({
      message: "Failed to initialize the app.",
      detail: error.message,
    });
  }
}

bootstrap();
