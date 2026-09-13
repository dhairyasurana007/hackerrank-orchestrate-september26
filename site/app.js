let bundle;
let selectedId;

const moneyFormatters = new Map();

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

function byId(id) {
  return document.getElementById(id);
}

async function boot() {
  const response = await fetch("data/results.json", { cache: "no-store" });
  bundle = await response.json();
  selectedId = bundle.requests[0].request.request_id;
  fillFilters();
  renderSummary();
  wireEvents();
  renderList();
  renderSelected();
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
  document.querySelectorAll(".tab").forEach((button) => {
    button.addEventListener("click", () => {
      document.querySelectorAll(".tab").forEach((tab) => tab.classList.remove("active"));
      document.querySelectorAll(".tab-panel").forEach((panel) => panel.classList.remove("active"));
      button.classList.add("active");
      byId(`tab-${button.dataset.tab}`).classList.add("active");
    });
  });
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
        <span class="badge ${row.affordability_status}">${row.affordability_status}</span>
        <span class="badge ${row.recommended_payment_method}">${row.recommended_payment_method}</span>
      </button>`;
    })
    .join("");
  document.querySelectorAll(".request-item").forEach((button) => {
    button.addEventListener("click", () => {
      selectedId = button.dataset.request;
      renderList();
      renderSelected();
    });
  });
}

function selected() {
  return bundle.requests.find((item) => item.request.request_id === selectedId);
}

function renderSelected() {
  const item = selected();
  const row = item.finalDecision;
  const currency = item.profile.home_currency;
  byId("selectedMeta").textContent = `${item.request.user_id} · ${item.request.request_type} · ${item.request.request_date}`;
  byId("selectedTitle").innerHTML = `${item.request.request_id}: <span class="badge ${row.affordability_status}">${row.affordability_status}</span> <span class="badge ${row.recommended_payment_method}">${row.recommended_payment_method}</span>`;
  byId("decisionExplanation").textContent = row.decision_explanation;
  byId("metricGrid").innerHTML = [
    ["Requested", fmtMoney(item.request.requested_amount, currency)],
    ["Safe today", fmtMoney(row.amount_safe_to_pay, currency)],
    ["Floor", fmtMoney(item.floor, currency)],
    ["Trough", `${fmtMoney(item.trough.balance, currency)} on ${item.trough.date}`],
  ]
    .map(([label, value]) => `<div><dt>${label}</dt><dd>${value}</dd></div>`)
    .join("");
  renderRowDetails(item);
  renderChart(item);
  renderCandidates(item);
  renderMovements(item);
  renderEvidence(item);
  renderQuality();
}

function renderRowDetails(item) {
  const row = item.finalDecision;
  byId("rowDetails").innerHTML = Object.entries(row)
    .filter(([key]) => key !== "decision_explanation")
    .map(([key, value]) => `<div class="detail-line"><span>${key}</span><strong>${text(value)}</strong></div>`)
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
  const path = curve.map((point, index) => `${index ? "L" : "M"}${x(index).toFixed(1)},${y(point.balance).toFixed(1)}`).join(" ");
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
    <text x="8" y="${y(yMax) + 12}" fill="var(--muted)" font-size="12">${Math.round(yMax).toLocaleString()}</text>
    <text x="8" y="${y(yMin) - 4}" fill="var(--muted)" font-size="12">${Math.round(yMin).toLocaleString()}</text>
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
    ["method", "status", "payments", "total", "deadline", "changes"],
    item.candidates.map((candidate) => [
      candidate.method,
      candidate.status,
      candidate.payments.map((payment) => `${payment.date}:${payment.amount}`).join(" | ") || "none",
      fmtMoney(candidate.total_payable, item.profile.home_currency),
      candidate.completes_by_deadline ? "yes" : "no",
      candidate.spending_changes.join(" | ") || "none",
    ])
  );
  byId("rejectedList").innerHTML =
    item.rejected.map((entry) => stack(entry.item, entry.reason)).join("") || empty("No rejected options recorded.");
}

function renderMovements(item) {
  byId("explicitTable").innerHTML = table(
    ["date", "amount", "event", "category", "status"],
    item.movements.explicit.map((movement) => [
      movement.date,
      fmtMoney(movement.amount, item.profile.home_currency),
      `${movement.event_id} · ${movement.description}`,
      movement.category,
      movement.status,
    ])
  );
  byId("projectedTable").innerHTML = table(
    ["date", "amount", "category", "series"],
    item.movements.projected.map((movement) => [
      movement.date,
      fmtMoney(movement.amount, item.profile.home_currency),
      movement.category,
      movement.series_key.join(" / "),
    ])
  );
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
    empty("No live evidence amendments embedded in this static offline bundle.");
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
      sample.relative_error === null ? (sample.bound_violated ? "bound violated" : "within bound") : `${(sample.relative_error * 100).toFixed(1)}%`,
    ])
  );
  byId("usageReport").textContent = bundle.usageReportMarkdown || "No usage report found in this checkout.";
}

function table(headers, rows) {
  if (!rows.length) return empty("No rows.");
  return `<table><thead><tr>${headers.map((header) => `<th>${header}</th>`).join("")}</tr></thead><tbody>${rows
    .map((row) => `<tr>${row.map((cell) => `<td>${text(cell)}</td>`).join("")}</tr>`)
    .join("")}</tbody></table>`;
}

function stack(title, body) {
  return `<div class="stack-item"><strong>${title}</strong><p>${text(body)}</p></div>`;
}

function empty(label) {
  return `<p class="empty">${label}</p>`;
}

boot().catch((error) => {
  document.body.innerHTML = `<main class="workspace"><h1>Could not load explorer data</h1><p>${error.message}</p></main>`;
});
