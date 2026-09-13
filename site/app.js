let bundle;
let selectedId;

const moneyFormatters = new Map();

function byId(id) {
  return document.getElementById(id);
}

function fmtMoney(value, currency) {
  const key = currency || "USD";
  if (!moneyFormatters.has(key)) {
    moneyFormatters.set(
      key,
      new Intl.NumberFormat("en-US", { style: "currency", currency: key, maximumFractionDigits: 2 })
    );
  }
  return moneyFormatters.get(key).format(Number(value || 0));
}

function text(value) {
  return value === null || value === undefined || value === "" ? "none" : String(value);
}

async function boot() {
  const response = await fetch("data/results.json", { cache: "no-store" });
  bundle = await response.json();
  selectedId = bundle.requests[0].request.request_id;
  fillFilters();
  renderSummary();
  wireEvents();
  renderList();
  renderSelected({ resetChat: true });
}

function renderSummary() {
  const statuses = bundle.summary.statuses;
  const chips = [
    ["Requests", bundle.metadata.request_count],
    ["Affordable now", statuses.affordable_now || 0],
    ["With plan", statuses.affordable_with_plan || 0],
    ["Not affordable", statuses.not_affordable || 0],
  ];
  byId("summaryStrip").innerHTML = chips
    .map(([label, value]) => `<div class="summary-chip"><span>${label}</span><strong>${value}</strong></div>`)
    .join("");
}

function fillFilters() {
  fillSelect("statusFilter", "All statuses", Object.keys(bundle.summary.statuses));
  fillSelect("methodFilter", "All methods", Object.keys(bundle.summary.methods));
}

function fillSelect(id, first, values) {
  byId(id).innerHTML = [`<option value="">${first}</option>`]
    .concat(values.sort().map((value) => `<option value="${value}">${value}</option>`))
    .join("");
}

function wireEvents() {
  ["searchInput", "statusFilter", "methodFilter"].forEach((id) => {
    byId(id).addEventListener("input", renderList);
  });
  byId("chatForm").addEventListener("submit", (event) => {
    event.preventDefault();
    const input = byId("chatInput");
    const prompt = input.value.trim();
    if (!prompt) return;
    input.value = "";
    appendMessage("user", prompt);
    setTimeout(() => appendMessage("assistant", answerPrompt(prompt)), 120);
  });
  byId("detailsToggle").addEventListener("click", () => {
    byId("contextPanel").classList.toggle("open");
  });
  document.querySelectorAll(".tab").forEach((button) => {
    button.addEventListener("click", () => {
      document.querySelectorAll(".tab").forEach((tab) => tab.classList.remove("active"));
      document.querySelectorAll(".tab-panel").forEach((panel) => panel.classList.remove("active"));
      button.classList.add("active");
      byId(`tab-${button.dataset.tab}`).classList.add("active");
    });
  });
}

function selected() {
  return bundle.requests.find((item) => item.request.request_id === selectedId);
}

function filteredRequests() {
  const query = byId("searchInput").value.trim().toLowerCase();
  const status = byId("statusFilter").value;
  const method = byId("methodFilter").value;
  return bundle.requests.filter((item) => {
    const row = item.finalDecision;
    if (status && row.affordability_status !== status) return false;
    if (method && row.recommended_payment_method !== method) return false;
    const haystack = [
      item.request.request_id,
      item.request.user_id,
      item.request.request_type,
      row.affordability_status,
      row.recommended_payment_method,
      item.request.request_text,
    ]
      .join(" ")
      .toLowerCase();
    return !query || haystack.includes(query);
  });
}

function renderList() {
  const items = filteredRequests();
  if (!items.find((item) => item.request.request_id === selectedId) && items[0]) {
    selectedId = items[0].request.request_id;
  }
  byId("requestList").innerHTML = items
    .map((item) => {
      const row = item.finalDecision;
      const active = item.request.request_id === selectedId ? " active" : "";
      return `<button class="request-item${active}" data-request="${item.request.request_id}">
        <strong><span>${item.request.request_id}</span><span>${fmtMoney(item.request.requested_amount, item.profile.home_currency)}</span></strong>
        <span class="badge ${row.affordability_status}">${humanStatus(row.affordability_status)}</span>
        <span class="badge ${row.recommended_payment_method}">${humanMethod(row.recommended_payment_method)}</span>
      </button>`;
    })
    .join("");
  document.querySelectorAll(".request-item").forEach((button) => {
    button.addEventListener("click", () => {
      selectedId = button.dataset.request;
      renderList();
      renderSelected({ resetChat: true });
    });
  });
}

function renderSelected({ resetChat = false } = {}) {
  const item = selected();
  const row = item.finalDecision;
  const currency = item.profile.home_currency;
  byId("selectedMeta").textContent = `${item.request.user_id} · ${item.request.request_type} · ${item.request.request_date}`;
  byId("selectedTitle").innerHTML = `${item.request.request_id}: <span class="badge ${row.affordability_status}">${humanStatus(row.affordability_status)}</span>`;
  byId("metricGrid").innerHTML = [
    ["Requested", fmtMoney(item.request.requested_amount, currency)],
    ["Safe today", fmtMoney(row.amount_safe_to_pay, currency)],
    ["Recommendation", humanMethod(row.recommended_payment_method)],
    ["Trough", `${fmtMoney(item.trough.balance, currency)} on ${item.trough.date}`],
  ]
    .map(([label, value]) => `<div><dt>${label}</dt><dd>${value}</dd></div>`)
    .join("");
  renderPromptChips();
  renderRowDetails(item);
  renderChart(item);
  renderCandidates(item);
  renderEvidence(item);
  renderQuality();
  if (resetChat) {
    resetThread(item);
  }
}

function resetThread(item) {
  byId("chatThread").innerHTML = "";
  appendMessage("assistant", openingAnswer(item));
}

function openingAnswer(item) {
  const row = item.finalDecision;
  const currency = item.profile.home_currency;
  const amount = fmtMoney(item.request.requested_amount, currency);
  const safe = fmtMoney(row.amount_safe_to_pay, currency);
  return [
    `For ${item.request.request_id}, I would ${plainRecommendation(row)}.`,
    `The request is for ${amount}. Safe to pay today is ${safe}.`,
    row.decision_explanation,
  ].join("\n\n");
}

function renderPromptChips() {
  const chips = ["Why?", "What is the payment plan?", "What could make it affordable?", "Show not affordable cases"];
  byId("promptChips").innerHTML = chips
    .map((chip) => `<button type="button" class="prompt-chip">${chip}</button>`)
    .join("");
  document.querySelectorAll(".prompt-chip").forEach((button) => {
    button.addEventListener("click", () => {
      appendMessage("user", button.textContent);
      setTimeout(() => appendMessage("assistant", answerPrompt(button.textContent)), 120);
    });
  });
}

function appendMessage(role, content) {
  const node = document.createElement("article");
  node.className = `message ${role}`;
  node.innerHTML = content
    .split("\n\n")
    .map((paragraph) => `<p>${escapeHtml(paragraph)}</p>`)
    .join("");
  byId("chatThread").appendChild(node);
  node.scrollIntoView({ block: "end" });
}

function answerPrompt(prompt) {
  const lower = prompt.toLowerCase();
  const explicit = findRequest(prompt);
  if (explicit && explicit.request.request_id !== selectedId) {
    selectedId = explicit.request.request_id;
    renderList();
    renderSelected();
  }
  const item = explicit || selected();
  if (mentionsCompare(lower)) {
    return compareAnswer(lower);
  }
  if (mentionsPortfolio(lower)) {
    return portfolioAnswer(lower);
  }
  if (mentionsPlan(lower)) {
    return planAnswer(item);
  }
  if (mentionsWhy(lower)) {
    return whyAnswer(item);
  }
  if (mentionsEvidence(lower)) {
    return evidenceAnswer(item);
  }
  if (mentionsAlternatives(lower)) {
    return alternativeAnswer(item);
  }
  return directAnswer(item);
}

function findRequest(prompt) {
  const match = prompt.match(/request[_\s-]?(\d+)/i);
  if (!match) return null;
  const id = `request_${match[1].padStart(2, "0")}`;
  return bundle.requests.find((item) => item.request.request_id.toLowerCase() === id.toLowerCase()) || null;
}

function mentionsPortfolio(lower) {
  return /not affordable|affordable cases|all requests|which requests|summary|overview/.test(lower);
}

function mentionsPlan(lower) {
  return /plan|pay|payment|installment|partial|wait|when/.test(lower);
}

function mentionsWhy(lower) {
  return /why|explain|reason|because|rationale/.test(lower);
}

function mentionsEvidence(lower) {
  return /evidence|message|image|document|receipt|invoice/.test(lower);
}

function mentionsAlternatives(lower) {
  return /alternative|option|change|make it affordable|reduce|stop|what could/.test(lower);
}

function mentionsCompare(lower) {
  return /best|largest|smallest|most expensive|cheapest|highest|lowest/.test(lower);
}

function directAnswer(item) {
  const row = item.finalDecision;
  const currency = item.profile.home_currency;
  return [
    `I would ${plainRecommendation(row)} for ${item.request.request_id}.`,
    `Requested: ${fmtMoney(item.request.requested_amount, currency)}. Safe today: ${fmtMoney(row.amount_safe_to_pay, currency)}. Status: ${humanStatus(row.affordability_status)}.`,
    shortPlan(row, currency),
  ].join("\n\n");
}

function whyAnswer(item) {
  const row = item.finalDecision;
  const currency = item.profile.home_currency;
  const rejected = item.rejected
    .slice(0, 3)
    .map((entry) => `${entry.item}: ${entry.reason}`)
    .join("; ");
  return [
    row.decision_explanation,
    `The forecast trough is ${fmtMoney(item.trough.balance, currency)} on ${item.trough.date}, against a floor of ${fmtMoney(item.floor, currency)}.`,
    rejected ? `The main rejected path was: ${rejected}.` : "No major rejected path was recorded for this request.",
  ].join("\n\n");
}

function planAnswer(item) {
  const row = item.finalDecision;
  const currency = item.profile.home_currency;
  return [
    shortPlan(row, currency),
    row.earliest_date_for_full_payment
      ? `The first safe full-payment date is ${row.earliest_date_for_full_payment}.`
      : "There is no safe full-payment date in the forecast window.",
  ].join("\n\n");
}

function evidenceAnswer(item) {
  const messages = item.evidence.messages.length;
  const images = item.evidence.images.length;
  const amendments = item.appliedAmendments.length;
  const firstMessage = item.evidence.messages[0];
  return [
    `I found ${messages} message(s), ${images} image document(s), and ${amendments} applied amendment(s) in scope for ${item.request.request_id}.`,
    firstMessage
      ? `Most recent useful message shown here: "${firstMessage.message_text}"`
      : "There is no request-scoped message text for this one.",
  ].join("\n\n");
}

function alternativeAnswer(item) {
  const currency = item.profile.home_currency;
  const rows = item.candidates
    .slice()
    .sort((a, b) => a.total_payable - b.total_payable)
    .slice(0, 3)
    .map((candidate) => `${humanMethod(candidate.method)} for ${fmtMoney(candidate.total_payable, currency)}`)
    .join("; ");
  const changes = item.finalDecision.spending_changes_needed;
  return [
    rows ? `The strongest available alternatives are: ${rows}.` : "I do not have another safe candidate recorded for this request.",
    changes && changes !== "none"
      ? `The submitted row depends on these spending changes: ${changes}.`
      : "The submitted recommendation does not require spending changes.",
  ].join("\n\n");
}

function compareAnswer(lower) {
  let items = bundle.requests.slice();
  if (/not affordable/.test(lower)) {
    items = items.filter((item) => item.finalDecision.affordability_status === "not_affordable");
  }
  if (/cheapest|smallest|lowest/.test(lower)) {
    items.sort((a, b) => a.request.requested_amount - b.request.requested_amount);
  } else {
    items.sort((a, b) => b.request.requested_amount - a.request.requested_amount);
  }
  const lines = items.slice(0, 5).map((item) => {
    const row = item.finalDecision;
    return `${item.request.request_id}: ${fmtMoney(item.request.requested_amount, item.profile.home_currency)} · ${humanStatus(row.affordability_status)} · ${humanMethod(row.recommended_payment_method)}`;
  });
  return `Here are the top matches:\n\n${lines.join("\n")}\n\nSay a request id to switch to it.`;
}

function portfolioAnswer(lower) {
  const status = /not affordable/.test(lower)
    ? "not_affordable"
    : /later/.test(lower)
      ? "affordable_later"
      : /plan/.test(lower)
        ? "affordable_with_plan"
        : /now/.test(lower)
          ? "affordable_now"
          : null;
  const items = status
    ? bundle.requests.filter((item) => item.finalDecision.affordability_status === status)
    : bundle.requests.slice(0, 8);
  const lines = items.slice(0, 8).map((item) => {
    const row = item.finalDecision;
    return `${item.request.request_id}: ${humanStatus(row.affordability_status)}, ${humanMethod(row.recommended_payment_method)}, ${fmtMoney(item.request.requested_amount, item.profile.home_currency)}`;
  });
  return [
    status ? `I found ${items.length} ${humanStatus(status).toLowerCase()} request(s).` : `There are ${bundle.metadata.request_count} requests in the bundle.`,
    lines.join("\n"),
  ].join("\n\n");
}

function shortPlan(row, currency) {
  if (!row.payment_plan || row.payment_plan === "none") {
    return "Payment plan: none.";
  }
  const pieces = row.payment_plan.split("|").map((part) => {
    const [date, amount] = part.split(":");
    return `${fmtMoney(amount, currency)} on ${date}`;
  });
  return `Payment plan: ${pieces.join(", ")}.`;
}

function plainRecommendation(row) {
  switch (row.recommended_payment_method) {
    case "full_payment":
      return "pay in full";
    case "partial_payment":
      return "use partial payment";
    case "installments":
      return "use installments";
    case "wait":
      return "wait";
    default:
      return "not proceed";
  }
}

function humanStatus(status) {
  return (
    {
      affordable_now: "Affordable now",
      affordable_with_plan: "Affordable with plan",
      affordable_later: "Affordable later",
      not_affordable: "Not affordable",
    }[status] || status
  );
}

function humanMethod(method) {
  return (
    {
      full_payment: "Full payment",
      partial_payment: "Partial payment",
      installments: "Installments",
      wait: "Wait",
      not_recommended: "Not recommended",
    }[method] || method
  );
}

function renderRowDetails(item) {
  const row = item.finalDecision;
  byId("rowDetails").innerHTML = Object.entries(row)
    .filter(([key]) => key !== "decision_explanation")
    .map(([key, value]) => `<div class="detail-line"><span>${escapeHtml(key)}</span><strong>${escapeHtml(text(value))}</strong></div>`)
    .join("");
}

function renderChart(item) {
  const svg = byId("curveChart");
  const curve = item.curve;
  const payments = paymentMarkers(item.finalDecision.payment_plan);
  const values = curve.map((point) => point.balance).concat([item.floor]);
  const min = Math.min(...values);
  const max = Math.max(...values);
  const pad = Math.max((max - min) * 0.08, 1);
  const yMin = min - pad;
  const yMax = max + pad;
  const width = 900;
  const height = 320;
  const margin = { left: 58, right: 16, top: 18, bottom: 36 };
  const innerW = width - margin.left - margin.right;
  const innerH = height - margin.top - margin.bottom;
  const x = (index) => margin.left + (innerW * index) / Math.max(curve.length - 1, 1);
  const y = (value) => margin.top + innerH - ((value - yMin) / (yMax - yMin)) * innerH;
  const path = curve
    .map((point, index) => `${index ? "L" : "M"}${x(index).toFixed(1)},${y(point.balance).toFixed(1)}`)
    .join(" ");
  const floorY = y(item.floor);
  const troughIndex = curve.findIndex((point) => point.date === item.trough.date);
  const paymentDots = payments
    .map((payment) => {
      const index = curve.findIndex((point) => point.date === payment.date);
      if (index < 0) return "";
      return `<circle cx="${x(index)}" cy="${y(curve[index].balance)}" r="5" fill="var(--green)"><title>${payment.date}: ${payment.amount}</title></circle>`;
    })
    .join("");
  svg.innerHTML = `
    <rect x="0" y="0" width="${width}" height="${height}" fill="#fff"></rect>
    <line x1="${margin.left}" y1="${floorY}" x2="${width - margin.right}" y2="${floorY}" stroke="var(--red)" stroke-dasharray="7 5"></line>
    <path d="${path}" fill="none" stroke="var(--blue)" stroke-width="3"></path>
    <circle cx="${x(troughIndex)}" cy="${y(item.trough.balance)}" r="6" fill="var(--amber)"><title>Trough ${item.trough.date}</title></circle>
    ${paymentDots}
    <text x="${margin.left}" y="${height - 12}" fill="var(--muted)" font-size="12">${curve[0].date}</text>
    <text x="${width - margin.right}" y="${height - 12}" fill="var(--muted)" font-size="12" text-anchor="end">${curve[curve.length - 1].date}</text>
    <text x="8" y="${floorY - 6}" fill="var(--red)" font-size="12">floor</text>
  `;
}

function paymentMarkers(plan) {
  if (!plan || plan === "none") return [];
  return plan.split("|").map((part) => {
    const [date, amount] = part.split(":");
    return { date, amount: Number(amount) };
  });
}

function renderCandidates(item) {
  byId("candidateTable").innerHTML = table(
    ["method", "payments", "total", "deadline"],
    item.candidates.map((candidate) => [
      humanMethod(candidate.method),
      candidate.payments.map((payment) => `${payment.date}:${payment.amount}`).join(" | ") || "none",
      fmtMoney(candidate.total_payable, item.profile.home_currency),
      candidate.completes_by_deadline ? "yes" : "no",
    ])
  );
  byId("rejectedList").innerHTML =
    item.rejected.map((entry) => stack(entry.item, entry.reason)).join("") || empty("No rejected options recorded.");
}

function renderEvidence(item) {
  byId("messageList").innerHTML =
    item.evidence.messages
      .map((message) => stack(`${message.message_id} · ${message.source_type} · ${message.sent_at}`, message.message_text))
      .join("") || empty("No request-scoped message evidence.");
  const images = item.evidence.images.map(
    (image) =>
      `<div class="stack-item image-evidence"><strong>${image.image_id}</strong><img src="${image.path}" alt="${image.image_id} evidence document" loading="lazy" /><p>Related event ${image.related_event_id}</p></div>`
  );
  const amendments =
    item.appliedAmendments.map((amendment, index) => stack(`Amendment ${index + 1}`, JSON.stringify(amendment))).join("") ||
    empty("No applied amendments in this static bundle.");
  byId("imageList").innerHTML = images.join("") + amendments;
}

function renderQuality() {
  byId("drawdownTable").innerHTML = table(
    ["request", "kind", "target", "predicted", "error"],
    bundle.drawdown.samples.map((sample) => [
      sample.request_id,
      sample.kind,
      sample.target_drawdown.toLocaleString(),
      sample.predicted_drawdown.toLocaleString(),
      sample.relative_error === null
        ? sample.bound_violated
          ? "bound violated"
          : "within bound"
        : `${(sample.relative_error * 100).toFixed(1)}%`,
    ])
  );
  byId("usageReport").textContent = bundle.usageReportMarkdown || "No usage report found in this checkout.";
}

function table(headers, rows) {
  if (!rows.length) return empty("No rows.");
  return `<table><thead><tr>${headers.map((header) => `<th>${header}</th>`).join("")}</tr></thead><tbody>${rows
    .map((row) => `<tr>${row.map((cell) => `<td>${escapeHtml(text(cell))}</td>`).join("")}</tr>`)
    .join("")}</tbody></table>`;
}

function stack(title, body) {
  return `<div class="stack-item"><strong>${escapeHtml(title)}</strong><p>${escapeHtml(text(body))}</p></div>`;
}

function empty(label) {
  return `<p class="empty">${label}</p>`;
}

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

boot().catch((error) => {
  document.body.innerHTML = `<main class="fallback"><h1>Could not load site data</h1><p>${escapeHtml(error.message)}</p></main>`;
});
