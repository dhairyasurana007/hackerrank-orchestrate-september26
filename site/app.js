let bundle;
let selectedId;
let uploadedTable = null;
let selectedUploadIndex = 0;
let pendingAssistantTimer = null;

const moneyFormatters = new Map();
const CHAT_RESPONSE_DELAY_MS = 650;
const TYPE_LABELS = {
  debt_repayment: "Debt repayment",
  education: "Education payment",
  emergency_expense: "Emergency expense",
  family_transfer: "Family transfer",
  housing: "Housing payment",
  investment: "Investment contribution",
  other: "Other payment",
  purchase: "Purchase",
  travel: "Travel booking",
};

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
  if (uploadedTable) {
    byId("summaryStrip").innerHTML = [
      ["Rows", uploadedTable.rows.length],
      ["Columns", uploadedTable.columns.length],
      ["Source", "CSV"],
      ["Mode", "Chat"],
    ]
      .map(([label, value]) => `<div class="summary-chip"><span>${label}</span><strong>${value}</strong></div>`)
      .join("");
    return;
  }
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
  byId("csvUpload").addEventListener("change", handleCsvUpload);
  byId("clearUpload").addEventListener("click", clearUpload);
  byId("chatForm").addEventListener("submit", (event) => {
    event.preventDefault();
    const input = byId("chatInput");
    const prompt = input.value.trim();
    if (!prompt) return;
    input.value = "";
    appendMessage("user", prompt);
    queueAssistantResponse(() => answerPrompt(prompt));
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
  if (uploadedTable) {
    return uploadedTable.rows[selectedUploadIndex] || null;
  }
  return bundle.requests.find((item) => item.request.request_id === selectedId);
}

function filteredRequests() {
  const query = byId("searchInput").value.trim().toLowerCase();
  if (uploadedTable) {
    return uploadedTable.rows.filter((row) => {
      const haystack = uploadedTable.columns.map((column) => row[column]).join(" ").toLowerCase();
      return !query || haystack.includes(query);
    });
  }
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
  if (uploadedTable) {
    if (!items.includes(uploadedTable.rows[selectedUploadIndex]) && items[0]) {
      selectedUploadIndex = uploadedTable.rows.indexOf(items[0]);
    }
    byId("requestList").innerHTML = items
      .map((row) => {
        const index = uploadedTable.rows.indexOf(row);
        const active = index === selectedUploadIndex ? " active" : "";
        return `<button class="request-item${active}" data-upload-index="${index}">
          <strong><span>${escapeHtml(uploadedRowTitle(row, index))}</span><span>${escapeHtml(uploadedRowAmount(row))}</span></strong>
          <span class="badge">${escapeHtml(uploadedRowSubtitle(row))}</span>
        </button>`;
      })
      .join("");
    document.querySelectorAll("[data-upload-index]").forEach((button) => {
      button.addEventListener("click", () => {
        selectedUploadIndex = Number(button.dataset.uploadIndex);
        renderList();
        renderSelected({ resetChat: true });
      });
    });
    return;
  }
  if (!items.find((item) => item.request.request_id === selectedId) && items[0]) {
    selectedId = items[0].request.request_id;
  }
  byId("requestList").innerHTML = items
    .map((item) => {
      const row = item.finalDecision;
      const active = item.request.request_id === selectedId ? " active" : "";
      return `<button class="request-item${active}" data-request="${item.request.request_id}">
        <strong><span>${escapeHtml(requestTitle(item))}</span><span>${fmtMoney(item.request.requested_amount, item.profile.home_currency)}</span></strong>
        <span class="request-key">${escapeHtml(item.request.request_id)}</span>
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
  if (uploadedTable) {
    renderUploadedSelected({ resetChat });
    return;
  }
  const item = selected();
  const row = item.finalDecision;
  const currency = item.profile.home_currency;
  byId("selectedMeta").textContent = `${item.request.request_id} · ${item.request.user_id} · ${item.request.request_type} · ${item.request.request_date}`;
  byId("selectedTitle").innerHTML = `${escapeHtml(requestTitle(item))} <span class="badge ${row.affordability_status}">${humanStatus(row.affordability_status)}</span>`;
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
  queueAssistantResponse(() => openingAnswer(item), { clearThread: true });
}

function openingAnswer(item) {
  const row = item.finalDecision;
  const currency = item.profile.home_currency;
  const amount = fmtMoney(item.request.requested_amount, currency);
  const safe = fmtMoney(row.amount_safe_to_pay, currency);
  const fullDate = row.earliest_date_for_full_payment
    ? `Full payment date: ${row.earliest_date_for_full_payment}.`
    : "No safe full-payment date in the forecast.";
  return [
    `${requestTitle(item)} (${item.request.request_id})`,
    `Recommendation: ${conciseRecommendation(row)}.`,
    `Safe today: ${safe} of ${amount}. ${fullDate}`,
  ].join("\n\n");
}

function renderPromptChips() {
  const chips = uploadedTable
    ? ["Summarize this row", "What columns are in this CSV?", "Find expensive rows", "View chart data", "Show row 5"]
    : ["Why?", "What is the payment plan?", "What could make it affordable?", "View chart data", "Show not affordable cases"];
  byId("promptChips").innerHTML = chips
    .map((chip) => `<button type="button" class="prompt-chip">${chip}</button>`)
    .join("");
  document.querySelectorAll(".prompt-chip").forEach((button) => {
    button.addEventListener("click", () => {
      appendMessage("user", button.textContent);
      queueAssistantResponse(() => answerPrompt(button.textContent));
    });
  });
}

function queueAssistantResponse(contentFactory, { clearThread = false } = {}) {
  clearPendingAssistant();
  const thread = byId("chatThread");
  if (clearThread) {
    thread.innerHTML = "";
  }
  const typing = appendTypingIndicator();
  pendingAssistantTimer = window.setTimeout(() => {
    typing.remove();
    pendingAssistantTimer = null;
    appendMessage("assistant", contentFactory());
  }, CHAT_RESPONSE_DELAY_MS);
}

function clearPendingAssistant() {
  if (pendingAssistantTimer !== null) {
    window.clearTimeout(pendingAssistantTimer);
    pendingAssistantTimer = null;
  }
  document.querySelectorAll(".typing-indicator").forEach((node) => node.remove());
}

function appendTypingIndicator() {
  const node = document.createElement("article");
  node.className = "message assistant typing-indicator";
  node.setAttribute("aria-label", "Thinking");
  node.innerHTML = '<span></span><span></span><span></span>';
  byId("chatThread").appendChild(node);
  node.scrollIntoView({ block: "end" });
  return node;
}

function appendMessage(role, content) {
  const node = document.createElement("article");
  node.className = `message ${role}`;
  if (role === "assistant" && content && typeof content === "object" && content.html) {
    node.classList.add("rich");
    node.innerHTML = content.html;
  } else {
    node.innerHTML = String(content)
      .split("\n\n")
      .map((paragraph) => `<p>${escapeHtml(paragraph)}</p>`)
      .join("");
  }
  byId("chatThread").appendChild(node);
  node.scrollIntoView({ block: "end" });
}

function answerPrompt(prompt) {
  if (uploadedTable) {
    return answerUploadedPrompt(prompt);
  }
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
  if (mentionsChart(lower)) {
    return chartAnswer(item);
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

function mentionsChart(lower) {
  return /chart|graph|curve|trough|floor|data point|forecast/.test(lower);
}

function directAnswer(item) {
  const row = item.finalDecision;
  const currency = item.profile.home_currency;
  return [
    `I would ${plainRecommendation(row)} for ${requestTitle(item)} (${item.request.request_id}).`,
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
    `I found ${messages} message(s), ${images} image document(s), and ${amendments} applied amendment(s) in scope for ${requestTitle(item)} (${item.request.request_id}).`,
    firstMessage
      ? `Most recent useful message shown here: "${firstMessage.message_text}"`
      : "There is no request-scoped message text for this one.",
  ].join("\n\n");
}

function chartAnswer(item) {
  const currency = item.profile.home_currency;
  const curve = item.curve || [];
  const samplePoints = [0, 7, 14, 30, 60, curve.length - 1]
    .filter((index, position, all) => index >= 0 && index < curve.length && all.indexOf(index) === position)
    .map((index) => `${curve[index].date}: ${fmtMoney(curve[index].balance, currency)}`);
  const payments = paymentMarkers(item.finalDecision.payment_plan)
    .map((payment) => `${fmtMoney(payment.amount, currency)} on ${payment.date}`)
    .join("; ");
  return richAssistantMessage(
    [
      `Here is the balance forecast for ${requestTitle(item)} (${item.request.request_id}).`,
      `Opening balance: ${fmtMoney(curve[0]?.balance || item.profile.current_available_balance, currency)}. Floor: ${fmtMoney(item.floor, currency)}. Trough: ${fmtMoney(item.trough.balance, currency)} on ${item.trough.date}.`,
      payments ? `Planned payment markers: ${payments}.` : "No payment markers are on the chart.",
      samplePoints.length ? `Sample points: ${samplePoints.join(" | ")}` : "No curve points are available.",
    ],
    forecastChartHtml(item)
  );
}

function richAssistantMessage(paragraphs, htmlBlock) {
  return {
    html: paragraphs.map((paragraph) => `<p>${escapeHtml(paragraph)}</p>`).join("") + htmlBlock,
  };
}

function forecastChartHtml(item) {
  const chart = balanceChartSvg({
    curve: item.curve || [],
    floor: Number(item.floor),
    trough: item.trough,
    payments: paymentMarkers(item.finalDecision.payment_plan),
    currency: item.profile.home_currency,
  });
  return `
    <div class="chat-chart">
      <div class="chat-chart-head">
        <strong>Balance forecast</strong>
        <span>${escapeHtml(item.request.request_date)} to ${escapeHtml(item.trough.through)}</span>
      </div>
      ${chart}
      <div class="chat-chart-legend">
        <span><i class="dot balance"></i>Balance</span>
        <span><i class="dot floor"></i>Floor</span>
        <span><i class="dot pay"></i>Payment</span>
        <span><i class="dot trough"></i>Trough</span>
      </div>
    </div>
  `;
}

function balanceChartSvg({ curve, floor, trough, payments, currency }) {
  if (!curve.length) {
    return `<svg viewBox="0 0 640 220" role="img" aria-label="Empty forecast chart"><text x="28" y="112" fill="var(--muted)" font-size="16">No chart points available.</text></svg>`;
  }
  const values = curve.map((point) => Number(point.balance)).concat([floor]);
  const min = Math.min(...values);
  const max = Math.max(...values);
  const pad = Math.max((max - min) * 0.08, 1);
  const yMin = min - pad;
  const yMax = max + pad;
  const width = 640;
  const height = 220;
  const margin = { left: 52, right: 16, top: 14, bottom: 32 };
  const innerW = width - margin.left - margin.right;
  const innerH = height - margin.top - margin.bottom;
  const x = (index) => margin.left + (innerW * index) / Math.max(curve.length - 1, 1);
  const y = (value) => margin.top + innerH - ((value - yMin) / (yMax - yMin)) * innerH;
  const path = curve
    .map((point, index) => `${index ? "L" : "M"}${x(index).toFixed(1)},${y(Number(point.balance)).toFixed(1)}`)
    .join(" ");
  const floorY = y(floor);
  const troughIndex = Math.max(
    curve.findIndex((point) => point.date === trough.date),
    0
  );
  const paymentDots = payments
    .map((payment) => {
      const index = curve.findIndex((point) => point.date === payment.date);
      if (index < 0) return "";
      const label = `${payment.date}: ${fmtMoney(payment.amount, currency)}`;
      return `<circle cx="${x(index)}" cy="${y(Number(curve[index].balance)).toFixed(1)}" r="5" fill="var(--green)"><title>${escapeHtml(label)}</title></circle>`;
    })
    .join("");
  const troughLabel = `Trough ${trough.date}: ${fmtMoney(trough.balance, currency)}`;
  return `
    <svg viewBox="0 0 ${width} ${height}" role="img" aria-label="Forecast balance chart">
      <rect x="0" y="0" width="${width}" height="${height}" rx="8" fill="#fff"></rect>
      <line x1="${margin.left}" y1="${floorY.toFixed(1)}" x2="${width - margin.right}" y2="${floorY.toFixed(1)}" stroke="var(--red)" stroke-dasharray="7 5"></line>
      <path d="${path}" fill="none" stroke="var(--blue)" stroke-width="3"></path>
      ${paymentDots}
      <circle cx="${x(troughIndex)}" cy="${y(Number(trough.balance)).toFixed(1)}" r="6" fill="var(--amber)"><title>${escapeHtml(troughLabel)}</title></circle>
      <text x="${margin.left}" y="${height - 10}" fill="var(--muted)" font-size="11">${escapeHtml(curve[0].date)}</text>
      <text x="${width - margin.right}" y="${height - 10}" fill="var(--muted)" font-size="11" text-anchor="end">${escapeHtml(curve[curve.length - 1].date)}</text>
      <text x="8" y="${Math.max(14, floorY - 6).toFixed(1)}" fill="var(--red)" font-size="11">floor</text>
    </svg>
  `;
}

function requestTitle(item) {
  const request = item.request || {};
  const lower = String(request.request_text || "").toLowerCase();
  const phraseRules = [
    [/laptop|laptop yang saya incar/i, "Laptop purchase"],
    [/family trip|travel option|book the trip|book.*family trip|trip/i, "Family trip"],
    [/professional course|course fee|course/i, "Course fee"],
    [/rental deposit|full deposit|deposit|move requires/i, "Housing deposit"],
    [/repair bill|urgent repair|repair/i, "Urgent repair"],
    [/loan payment|additional loan|debt/i, "Loan repayment"],
    [/investment contribution|investing this amount|opportunity to invest|invest/i, "Investment contribution"],
  ];
  const matched = phraseRules.find(([pattern]) => pattern.test(lower));
  if (matched) return matched[1];
  if (request.request_type === "family_transfer" || /family|transfer/.test(lower)) return "Family transfer";
  return TYPE_LABELS[request.request_type] || titleCase(String(request.request_type || "Request").replaceAll("_", " "));
}

function titleCase(value) {
  return value.replace(/\b[a-z]/g, (letter) => letter.toUpperCase());
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
    return `${requestTitle(item)} (${item.request.request_id}): ${fmtMoney(item.request.requested_amount, item.profile.home_currency)} · ${humanStatus(row.affordability_status)} · ${humanMethod(row.recommended_payment_method)}`;
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
    return `${requestTitle(item)} (${item.request.request_id}): ${humanStatus(row.affordability_status)}, ${humanMethod(row.recommended_payment_method)}, ${fmtMoney(item.request.requested_amount, item.profile.home_currency)}`;
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

function conciseRecommendation(row) {
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
      return "do not proceed";
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

function handleCsvUpload(event) {
  const file = event.target.files && event.target.files[0];
  if (!file) return;
  const reader = new FileReader();
  reader.onload = () => {
    try {
      uploadedTable = parseCsv(String(reader.result || ""), file.name);
      selectedUploadIndex = 0;
      byId("sourceHint").textContent = `${file.name}: ${uploadedTable.rows.length} row(s), ${uploadedTable.columns.length} column(s).`;
      byId("clearUpload").hidden = false;
      byId("statusFilter").disabled = true;
      byId("methodFilter").disabled = true;
      byId("searchInput").value = "";
      renderSummary();
      renderList();
      renderSelected({ resetChat: true });
    } catch (error) {
      uploadedTable = null;
      byId("sourceHint").textContent = `Could not read CSV: ${error.message}`;
      byId("clearUpload").hidden = true;
      renderSummary();
      renderList();
      renderSelected({ resetChat: true });
    }
  };
  reader.readAsText(file);
}

function clearUpload() {
  uploadedTable = null;
  selectedUploadIndex = 0;
  byId("csvUpload").value = "";
  byId("sourceHint").textContent = "Using the built-in Buy-or-Wait decisions.";
  byId("clearUpload").hidden = true;
  byId("statusFilter").disabled = false;
  byId("methodFilter").disabled = false;
  renderSummary();
  renderList();
  renderSelected({ resetChat: true });
}

function parseCsv(source, name) {
  const rows = [];
  let current = [];
  let field = "";
  let quoted = false;
  for (let index = 0; index < source.length; index += 1) {
    const char = source[index];
    const next = source[index + 1];
    if (quoted) {
      if (char === '"' && next === '"') {
        field += '"';
        index += 1;
      } else if (char === '"') {
        quoted = false;
      } else {
        field += char;
      }
    } else if (char === '"') {
      quoted = true;
    } else if (char === ",") {
      current.push(field);
      field = "";
    } else if (char === "\n") {
      current.push(field);
      rows.push(current);
      current = [];
      field = "";
    } else if (char !== "\r") {
      field += char;
    }
  }
  current.push(field);
  rows.push(current);
  const nonEmpty = rows.filter((row) => row.some((cell) => cell.trim() !== ""));
  if (nonEmpty.length < 2) {
    throw new Error("expected a header row and at least one data row");
  }
  const columns = nonEmpty[0].map((column, index) => column.trim() || `column_${index + 1}`);
  const dataRows = nonEmpty.slice(1).map((row, rowIndex) => {
    const item = { __rowNumber: rowIndex + 1 };
    columns.forEach((column, columnIndex) => {
      item[column] = (row[columnIndex] || "").trim();
    });
    return item;
  });
  return { name, columns, rows: dataRows };
}

function renderUploadedSelected({ resetChat = false } = {}) {
  const row = selected();
  const title = uploadedRowTitle(row, selectedUploadIndex);
  byId("selectedMeta").textContent = `${uploadedTable.name} · row ${selectedUploadIndex + 1}`;
  byId("selectedTitle").innerHTML = `${escapeHtml(title)} <span class="badge">CSV row</span>`;
  byId("metricGrid").innerHTML = uploadedTable.columns
    .slice(0, 4)
    .map((column) => `<div><dt>${escapeHtml(column)}</dt><dd>${escapeHtml(text(row[column]))}</dd></div>`)
    .join("");
  renderPromptChips();
  byId("rowDetails").innerHTML = uploadedTable.columns
    .map((column) => `<div class="detail-line"><span>${escapeHtml(column)}</span><strong>${escapeHtml(text(row[column]))}</strong></div>`)
    .join("");
  renderUploadedChart();
  byId("candidateTable").innerHTML = table(["column", "value"], uploadedTable.columns.map((column) => [column, row[column]]));
  byId("rejectedList").innerHTML = empty("Upload mode shows raw row fields instead of Buy-or-Wait candidate plans.");
  byId("messageList").innerHTML = stack("Loaded CSV", `${uploadedTable.name} is being analyzed locally in this browser.`);
  byId("imageList").innerHTML = empty("CSV upload mode does not process image files.");
  byId("drawdownTable").innerHTML = table(
    ["row", "preview"],
    uploadedTable.rows.slice(0, 12).map((item, index) => [index + 1, uploadedRowCompact(item)])
  );
  byId("usageReport").textContent = "CSV upload mode is local-only: no server upload, no model calls, no token usage.";
  if (resetChat) {
    queueAssistantResponse(() => openingUploadedAnswer(row), { clearThread: true });
  }
}

function renderUploadedChart() {
  const numeric = uploadedTable.columns
    .map((column) => ({ column, values: uploadedTable.rows.map((row) => parseLooseNumber(row[column])).filter((value) => value !== null) }))
    .filter((item) => item.values.length >= 2)[0];
  const svg = byId("curveChart");
  if (!numeric) {
    svg.innerHTML = `<rect x="0" y="0" width="900" height="320" fill="#fff"></rect><text x="32" y="160" fill="var(--muted)" font-size="18">No numeric column to chart.</text>`;
    return;
  }
  const values = uploadedTable.rows.map((row) => parseLooseNumber(row[numeric.column]) || 0);
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
  const x = (index) => margin.left + (innerW * index) / Math.max(values.length - 1, 1);
  const y = (value) => margin.top + innerH - ((value - yMin) / (yMax - yMin)) * innerH;
  const path = values.map((value, index) => `${index ? "L" : "M"}${x(index).toFixed(1)},${y(value).toFixed(1)}`).join(" ");
  const selectedX = x(selectedUploadIndex);
  svg.innerHTML = `
    <rect x="0" y="0" width="${width}" height="${height}" fill="#fff"></rect>
    <path d="${path}" fill="none" stroke="var(--blue)" stroke-width="3"></path>
    <circle cx="${selectedX}" cy="${y(values[selectedUploadIndex] || 0)}" r="6" fill="var(--amber)"></circle>
    <text x="${margin.left}" y="24" fill="var(--muted)" font-size="13">${escapeHtml(numeric.column)}</text>
  `;
}

function openingUploadedAnswer(row) {
  return [
    `${uploadedTable.name}`,
    `Viewing row ${selectedUploadIndex + 1}: ${uploadedRowCompact(row)}.`,
    "Ask about a row, column, recommendation field, or chart.",
  ].join("\n\n");
}

function answerUploadedPrompt(prompt) {
  const lower = prompt.toLowerCase();
  const rowMatch = lower.match(/\brow\s+(\d+)\b/);
  if (rowMatch) {
    const wanted = Number(rowMatch[1]) - 1;
    if (wanted >= 0 && wanted < uploadedTable.rows.length) {
      selectedUploadIndex = wanted;
      renderList();
      renderSelected();
      return `Switched to row ${wanted + 1}.\n\n${uploadedRowNarrative(uploadedTable.rows[wanted])}`;
    }
    return `I only have ${uploadedTable.rows.length} row(s), so row ${wanted + 1} is outside this CSV.`;
  }
  const foundByValue = findUploadedRow(prompt);
  if (foundByValue !== null && foundByValue !== selectedUploadIndex) {
    selectedUploadIndex = foundByValue;
    renderList();
    renderSelected();
    return `I found that in row ${foundByValue + 1}.\n\n${uploadedRowNarrative(uploadedTable.rows[foundByValue])}`;
  }
  if (/column|schema|field|header/.test(lower)) {
    return `This CSV has ${uploadedTable.columns.length} column(s):\n\n${uploadedTable.columns.join(", ")}`;
  }
  if (mentionsChart(lower)) {
    return uploadedChartAnswer();
  }
  if (/summary|overview|how many|dataset/.test(lower)) {
    return uploadedDatasetSummary();
  }
  if (/expensive|highest|largest|biggest|costliest/.test(lower)) {
    return uploadedExtremes("desc");
  }
  if (/cheap|lowest|smallest/.test(lower)) {
    return uploadedExtremes("asc");
  }
  if (/recommend|buy|decision|afford|should/.test(lower)) {
    return uploadedRecommendationLikeAnswer(selected());
  }
  return uploadedRowNarrative(selected());
}

function uploadedDatasetSummary() {
  const numericColumns = uploadedTable.columns.filter((column) =>
    uploadedTable.rows.some((row) => parseLooseNumber(row[column]) !== null)
  );
  return [
    `${uploadedTable.name} has ${uploadedTable.rows.length} row(s) and ${uploadedTable.columns.length} column(s).`,
    numericColumns.length ? `Numeric-looking columns: ${numericColumns.join(", ")}.` : "I did not detect numeric columns.",
    `Current row: ${uploadedRowCompact(selected())}.`,
  ].join("\n\n");
}

function uploadedChartAnswer() {
  const numericColumn = uploadedBestNumericColumn();
  if (!numericColumn) {
    return "I do not see a numeric column to chart in this CSV.";
  }
  const ranked = uploadedTable.rows
    .map((row, index) => ({ row, index, value: parseLooseNumber(row[numericColumn]) }))
    .filter((item) => item.value !== null);
  const values = ranked.map((item) => item.value);
  const min = Math.min(...values);
  const max = Math.max(...values);
  const current = parseLooseNumber(selected()[numericColumn]);
  const points = ranked
    .slice(0, 8)
    .map((item) => `row ${item.index + 1}: ${item.value} · ${uploadedRowTitle(item.row, item.index)}`)
    .join("\n");
  return richAssistantMessage(
    [
      `Here is a chart from the uploaded CSV using "${numericColumn}".`,
      `Range: ${min} to ${max}. Current row value: ${current === null ? "blank" : current}.`,
      `First chart points: ${points.replaceAll("\n", " | ")}`,
    ],
    uploadedChartHtml(numericColumn, ranked)
  );
}

function uploadedChartHtml(numericColumn, ranked) {
  const values = ranked.map((item) => item.value);
  const min = Math.min(...values);
  const max = Math.max(...values);
  const pad = Math.max((max - min) * 0.08, 1);
  const yMin = min - pad;
  const yMax = max + pad;
  const width = 640;
  const height = 220;
  const margin = { left: 48, right: 16, top: 14, bottom: 32 };
  const innerW = width - margin.left - margin.right;
  const innerH = height - margin.top - margin.bottom;
  const x = (index) => margin.left + (innerW * index) / Math.max(values.length - 1, 1);
  const y = (value) => margin.top + innerH - ((value - yMin) / (yMax - yMin)) * innerH;
  const path = ranked
    .map((item, index) => `${index ? "L" : "M"}${x(index).toFixed(1)},${y(item.value).toFixed(1)}`)
    .join(" ");
  const selectedRankIndex = Math.max(
    ranked.findIndex((item) => item.index === selectedUploadIndex),
    0
  );
  const selectedValue = ranked[selectedRankIndex]?.value || 0;
  return `
    <div class="chat-chart">
      <div class="chat-chart-head">
        <strong>${escapeHtml(numericColumn)}</strong>
        <span>${uploadedTable.rows.length} rows</span>
      </div>
      <svg viewBox="0 0 ${width} ${height}" role="img" aria-label="Uploaded CSV chart">
        <rect x="0" y="0" width="${width}" height="${height}" rx="8" fill="#fff"></rect>
        <path d="${path}" fill="none" stroke="var(--blue)" stroke-width="3"></path>
        <circle cx="${x(selectedRankIndex).toFixed(1)}" cy="${y(selectedValue).toFixed(1)}" r="6" fill="var(--amber)"><title>Current row ${selectedUploadIndex + 1}: ${selectedValue}</title></circle>
        <text x="${margin.left}" y="${height - 10}" fill="var(--muted)" font-size="11">row ${ranked[0].index + 1}</text>
        <text x="${width - margin.right}" y="${height - 10}" fill="var(--muted)" font-size="11" text-anchor="end">row ${ranked[ranked.length - 1].index + 1}</text>
      </svg>
      <div class="chat-chart-legend">
        <span><i class="dot balance"></i>${escapeHtml(numericColumn)}</span>
        <span><i class="dot trough"></i>Current row</span>
      </div>
    </div>
  `;
}

function uploadedExtremes(direction) {
  const numericColumn = uploadedBestNumericColumn();
  if (!numericColumn) {
    return "I need a numeric amount/price/cost column to rank rows, and I do not see one in this CSV.";
  }
  const ranked = uploadedTable.rows
    .map((row, index) => ({ row, index, value: parseLooseNumber(row[numericColumn]) }))
    .filter((item) => item.value !== null)
    .sort((a, b) => (direction === "asc" ? a.value - b.value : b.value - a.value))
    .slice(0, 5);
  return [
    `Using ${numericColumn}, the ${direction === "asc" ? "lowest" : "highest"} rows are:`,
    ranked.map((item) => `row ${item.index + 1}: ${item.value} · ${uploadedRowCompact(item.row)}`).join("\n"),
  ].join("\n\n");
}

function uploadedRecommendationLikeAnswer(row) {
  const columns = uploadedTable.columns;
  const statusColumn = columns.find((column) => /status|afford|recommend|decision/i.test(column));
  const amountColumn = uploadedBestNumericColumn();
  const pieces = [];
  if (statusColumn) pieces.push(`${statusColumn}: ${row[statusColumn] || "blank"}`);
  if (amountColumn) pieces.push(`${amountColumn}: ${row[amountColumn] || "blank"}`);
  return pieces.length
    ? `For this uploaded row, the closest decision fields I can infer are:\n\n${pieces.join("\n")}\n\nThis is a local CSV chat view, so I am not recomputing financial affordability unless the CSV already includes those fields.`
    : `I can explain the row, but this uploaded CSV does not include obvious decision or amount fields. Current row: ${uploadedRowCompact(row)}.`;
}

function uploadedRowNarrative(row) {
  const fields = uploadedTable.columns
    .filter((column) => row[column] !== "")
    .slice(0, 10)
    .map((column) => `${column}: ${row[column]}`);
  return `Row ${selectedUploadIndex + 1} says:\n\n${fields.join("\n") || "No populated fields in this row."}`;
}

function uploadedRowTitle(row, index) {
  const idColumn = uploadedTable.columns.find((column) => /(^id$|_id$|request|product|item|name|title)/i.test(column));
  return idColumn && row[idColumn] ? row[idColumn] : `Row ${index + 1}`;
}

function uploadedRowAmount(row) {
  const column = uploadedBestNumericColumn();
  return column && row[column] ? row[column] : "";
}

function uploadedRowSubtitle(row) {
  const preview = uploadedTable.columns
    .filter((column) => row[column] && row[column] !== uploadedRowTitle(row, row.__rowNumber - 1))
    .slice(0, 2)
    .map((column) => `${column}: ${row[column]}`)
    .join(" · ");
  return preview || "CSV row";
}

function uploadedRowCompact(row) {
  return uploadedTable.columns
    .filter((column) => row[column])
    .slice(0, 4)
    .map((column) => `${column}=${row[column]}`)
    .join(", ");
}

function uploadedBestNumericColumn() {
  return (
    uploadedTable.columns.find((column) => /amount|price|cost|total|value|payment|balance/i.test(column) && uploadedTable.rows.some((row) => parseLooseNumber(row[column]) !== null)) ||
    uploadedTable.columns.find((column) => uploadedTable.rows.some((row) => parseLooseNumber(row[column]) !== null))
  );
}

function findUploadedRow(prompt) {
  const lower = prompt.toLowerCase();
  if (lower.length < 3) return null;
  const index = uploadedTable.rows.findIndex((row) =>
    uploadedTable.columns.some((column) => String(row[column] || "").toLowerCase().includes(lower))
  );
  return index >= 0 ? index : null;
}

function parseLooseNumber(value) {
  const cleaned = String(value || "").replace(/[^0-9.-]/g, "");
  if (!cleaned || cleaned === "-" || cleaned === "." || cleaned === "-.") return null;
  const number = Number(cleaned);
  return Number.isFinite(number) ? number : null;
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
