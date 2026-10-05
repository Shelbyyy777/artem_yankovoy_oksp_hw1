"use strict";

const $ = (id) => document.getElementById(id);
const state = { user: null, services: [], specialists: [], selectedSlot: null, slotsPage: 1, recordsPage: 1, slotsSize: 6, recordsSize: 5, slotsTotal: 0, recordsTotal: 0, detailId: null };
const statusLabels = { booked: "Запланирована", cancelled: "Отменена", completed: "Завершена" };
const numberFormat = new Intl.NumberFormat("ru-RU");
const dateFormat = new Intl.DateTimeFormat("ru-RU", { day: "2-digit", month: "short", year: "numeric" });
const timeFormat = new Intl.DateTimeFormat("ru-RU", { hour: "2-digit", minute: "2-digit" });

function element(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

function notify(message, error = false) {
  const box = $("notification");
  box.textContent = message;
  box.className = error ? "notification error" : "notification";
  box.hidden = false;
}

function clearNotification() { $("notification").hidden = true; }

async function request(path, options = {}) {
  const response = await fetch(path, { cache: "no-store", credentials: "same-origin", ...options, headers: { "Content-Type": "application/json", ...(options.headers || {}) } });
  const body = response.status === 204 ? null : await response.json();
  if (!response.ok) {
    if (response.status === 401 && path !== "/api/login") showLogin();
    let message = body && body.detail;
    if (Array.isArray(message)) message = "Проверьте параметры: услугу, даты и обязательные поля.";
    const fallback = { 401: "Неверные учётные данные или сессия завершена.", 403: "Отменять можно только собственную запись.", 404: "Запись или слот не найдены.", 409: "Это время уже занято. Обновите расписание и выберите другой слот.", 422: "Проверьте заполненные поля и границы периода." };
    const error = new Error(typeof message === "string" ? message : fallback[response.status] || "Не удалось выполнить операцию.");
    error.status = response.status;
    throw error;
  }
  return body;
}

function query(params) {
  const encoded = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) if (value !== "" && value !== null && value !== undefined) encoded.set(key, value);
  return encoded.toString();
}

function period(fromId, toId) {
  const from = $(fromId).value;
  const to = $(toId).value;
  if (!from || !to || from >= to) throw new Error("Начало периода должно быть раньше его окончания.");
  return { from_at: `${from}T00:00:00`, to_at: `${to}T00:00:00` };
}

function dates(item) {
  const start = new Date(item.start_at);
  const end = new Date(item.end_at);
  return { day: dateFormat.format(start), time: `${timeFormat.format(start)}–${timeFormat.format(end)}` };
}

function statusBadge(status) { return element("span", `status status-${status}`, statusLabels[status] || status); }

async function run(action) {
  clearNotification();
  try { await action(); } catch (error) { notify(error.message || "Соединение с сервисом недоступно.", true); }
}

function showLogin() {
  state.user = null;
  $("login-view").hidden = false;
  $("app-view").hidden = true;
  $("header-user").hidden = true;
}

function fillOptions(selectId, items, nameKey, emptyLabel) {
  const select = $(selectId);
  select.replaceChildren();
  if (emptyLabel) {
    const option = element("option", "", emptyLabel);
    option.value = "";
    select.append(option);
  }
  for (const item of items) {
    const label = nameKey === "service" ? `${item.name} · ${item.duration_minutes} мин` : item[nameKey];
    const option = element("option", "", label);
    option.value = item.id;
    select.append(option);
  }
}

async function openApp(user) {
  state.user = user.user || user;
  $("current-user").textContent = state.user.full_name || state.user.username;
  $("login-view").hidden = true;
  $("app-view").hidden = false;
  $("header-user").hidden = false;
  const [services, specialists] = await Promise.all([request("/api/services"), request("/api/specialists")]);
  state.services = services.items;
  state.specialists = specialists.items;
  fillOptions("service-select", state.services, "service");
  fillOptions("slot-specialist", state.specialists, "full_name", "Все специалисты");
  fillOptions("record-specialist", state.specialists, "full_name", "Все специалисты");
  state.slotsPage = state.recordsPage = 1;
  state.detailId = null;
  clearSelection();
  await Promise.all([loadSlots(), loadRecords(), loadSummary()]);
}

function clearSelection() {
  state.selectedSlot = null;
  $("selected-slot-label").textContent = "Выберите свободный слот выше";
  $("book-button").disabled = true;
}

async function loadSlots() {
  const data = await request(`/api/slots?${query({ page: state.slotsPage, size: state.slotsSize, service_id: $("service-select").value, specialist_id: $("slot-specialist").value, ...period("slots-from", "slots-to") })}`);
  state.slotsTotal = data.total;
  $("slots-count").textContent = `Свободных слотов: ${numberFormat.format(data.total)}`;
  $("slots-page").textContent = `${data.page} / ${Math.max(1, Math.ceil(data.total / data.size))}`;
  $("slots-prev").disabled = data.page <= 1;
  $("slots-next").disabled = data.page * data.size >= data.total;
  const list = $("slots-list");
  list.replaceChildren();
  if (!data.items.length) list.append(element("p", "empty-message", "В этом периоде нет подходящих свободных слотов."));
  for (const slot of data.items) {
    const date = dates(slot);
    const button = element("button", "slot-button");
    button.type = "button";
    button.append(element("strong", "", date.time), element("span", "", date.day), element("span", "", slot.specialist.full_name));
    button.addEventListener("click", () => {
      state.selectedSlot = slot;
      list.querySelectorAll("button").forEach((other) => other.classList.remove("selected"));
      button.classList.add("selected");
      $("selected-slot-label").textContent = `${date.day}, ${date.time} · ${slot.specialist.full_name}`;
      $("book-button").disabled = false;
    });
    list.append(button);
  }
}

async function loadRecords() {
  const data = await request(`/api/appointments?${query({ page: state.recordsPage, size: state.recordsSize, status: $("status-select").value, specialist_id: $("record-specialist").value })}`);
  state.recordsTotal = data.total;
  $("records-count").textContent = `${numberFormat.format(data.total)} записей`;
  $("records-page").textContent = `${data.page} / ${Math.max(1, Math.ceil(data.total / data.size))}`;
  $("records-page-description").textContent = data.total ? `${(data.page - 1) * data.size + 1}–${Math.min(data.page * data.size, data.total)} из ${numberFormat.format(data.total)}` : "Нет записей";
  $("records-prev").disabled = data.page <= 1;
  $("records-next").disabled = data.page * data.size >= data.total;
  const body = $("records-body");
  body.replaceChildren();
  if (!data.items.length) {
    const row = element("tr");
    const cell = element("td", "empty-message", "По выбранным фильтрам записей нет.");
    cell.colSpan = 5;
    row.append(cell);
    body.append(row);
  }
  for (const appointment of data.items) {
    const row = element("tr");
    const date = dates(appointment);
    const dateCell = element("td", "", date.day);
    dateCell.append(element("small", "", date.time));
    const clientCell = element("td", "", appointment.client_name);
    clientCell.append(element("small", "", appointment.service.name));
    const specialistCell = element("td", "", appointment.specialist.full_name);
    const statusCell = element("td");
    statusCell.append(statusBadge(appointment.status));
    const actionCell = element("td");
    const open = element("button", "row-open", "↗");
    open.type = "button";
    open.setAttribute("aria-label", `Открыть запись №${appointment.id}`);
    open.addEventListener("click", () => run(() => loadDetail(appointment.id)));
    actionCell.append(open);
    row.append(dateCell, clientCell, specialistCell, statusCell, actionCell);
    body.append(row);
  }
}

function detailPair(label, value, extra) {
  const box = element("div");
  const term = element("dt", "", label);
  const description = element("dd", "", value);
  if (extra) description.append(element("small", "", extra));
  box.append(term, description);
  return box;
}

async function loadDetail(id) {
  const appointment = await request(`/api/appointments/${id}`);
  state.detailId = id;
  const box = $("appointment-detail");
  box.className = "";
  box.replaceChildren();
  const lead = element("div", "detail-lead");
  lead.append(element("strong", "", `Запись №${appointment.id}`), statusBadge(appointment.status));
  const date = dates(appointment);
  const grid = element("dl", "detail-grid");
  grid.append(detailPair("Дата и время", `${date.day}, ${date.time}`), detailPair("Клиент", appointment.client_name), detailPair("Специалист", appointment.specialist.full_name, `Специалист №${appointment.specialist.id} · слот №${appointment.slot_id}`), detailPair("Услуга", appointment.service.name, `${appointment.service.duration_minutes} мин · ${numberFormat.format(appointment.service.price)} ₽`), detailPair("Учётная запись", appointment.user.full_name || appointment.user.username, appointment.user.username));
  box.append(lead, grid);
  const actions = element("div", "detail-actions");
  if (appointment.status === "booked" && appointment.user.id === state.user.id) {
    const cancel = element("button", "button button-danger", "Отменить запись");
    cancel.type = "button";
    cancel.addEventListener("click", () => run(async () => {
      cancel.disabled = true;
      try {
        await request(`/api/appointments/${id}/cancel`, { method: "POST" });
        clearSelection();
        await Promise.all([loadSlots(), loadRecords(), loadSummary(), loadDetail(id)]);
        notify(`Запись №${id} отменена. Время специалиста снова доступно.`);
      } finally { cancel.disabled = false; }
    }));
    actions.append(cancel, element("p", "detail-note", "После отмены слот снова появится в свободном расписании."));
  } else {
    actions.append(element("p", "detail-note", appointment.status === "cancelled" ? "Запись отменена; слот освобождён." : appointment.status === "completed" ? "Приём завершён." : "Эта запись принадлежит другой учётной записи."));
  }
  box.append(actions);
}

async function loadSummary() {
  const data = await request(`/api/summary?${query(period("summary-from", "summary-to"))}`);
  $("total-appointments").textContent = numberFormat.format(data.total_appointments);
  $("cancelled-share").textContent = `${numberFormat.format(Math.round(data.cancelled_share * 10000) / 100)} %`;
  $("cancelled-count").textContent = `${numberFormat.format(data.cancelled_appointments)} отменённых записей`;
  const list = $("specialists-summary");
  list.replaceChildren();
  if (!data.specialists.length) list.append(element("p", "empty-message", "За выбранный период данных нет."));
  for (const specialist of data.specialists) {
    const item = element("div", "specialist-summary");
    const row = element("div", "specialist-row");
    row.append(element("span", "", specialist.full_name), element("span", "", `${numberFormat.format(specialist.load_percent)} % · ${numberFormat.format(specialist.booked_minutes)} / ${numberFormat.format(specialist.slot_minutes)} мин`));
    const track = element("div", "load-track");
    const fill = element("div", "load-fill");
    fill.style.width = `${Math.min(100, Math.max(0, specialist.load_percent))}%`;
    track.append(fill);
    item.append(row, track);
    list.append(item);
  }
}

$("login-form").addEventListener("submit", (event) => {
  event.preventDefault();
  run(async () => {
    const button = event.target.querySelector("button");
    button.disabled = true;
    try { await openApp(await request("/api/login", { method: "POST", body: JSON.stringify({ username: $("login-username").value.trim(), password: $("login-password").value }) })); }
    finally { button.disabled = false; }
  });
});
$("logout").addEventListener("click", () => run(async () => { await request("/api/logout", { method: "POST" }); showLogin(); }));
$("slots-filter").addEventListener("submit", (event) => { event.preventDefault(); run(async () => { state.slotsPage = 1; clearSelection(); await loadSlots(); }); });
$("service-select").addEventListener("change", clearSelection);
$("slots-prev").addEventListener("click", () => run(async () => { state.slotsPage--; clearSelection(); await loadSlots(); }));
$("slots-next").addEventListener("click", () => run(async () => { state.slotsPage++; clearSelection(); await loadSlots(); }));
$("records-filter").addEventListener("submit", (event) => { event.preventDefault(); run(async () => { state.recordsPage = 1; await loadRecords(); }); });
$("records-prev").addEventListener("click", () => run(async () => { state.recordsPage--; await loadRecords(); }));
$("records-next").addEventListener("click", () => run(async () => { state.recordsPage++; await loadRecords(); }));
$("summary-filter").addEventListener("submit", (event) => { event.preventDefault(); run(loadSummary); });
$("booking-form").addEventListener("submit", (event) => {
  event.preventDefault();
  run(async () => {
    if (!state.selectedSlot) throw new Error("Сначала выберите свободный слот.");
    const clientName = $("client-name").value.trim();
    if (!clientName) throw new Error("Укажите имя клиента.");
    $("book-button").disabled = true;
    try {
      const appointment = await request("/api/appointments", { method: "POST", body: JSON.stringify({ slot_id: state.selectedSlot.id, service_id: Number($("service-select").value), client_name: clientName }) });
      clearSelection();
      state.recordsPage = 1;
      $("status-select").value = "";
      $("record-specialist").value = "";
      await Promise.all([loadSlots(), loadRecords(), loadSummary(), loadDetail(appointment.id)]);
      notify(`Запись №${appointment.id} создана. Подробности открыты в карточке приёма.`);
    } finally { $("book-button").disabled = !state.selectedSlot; }
  });
});

(async () => {
  try { await openApp(await request("/api/me")); }
  catch (error) { if (error.status !== 401) notify(error.message || "Сервис недоступен.", true); }
})();
