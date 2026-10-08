/* Leapmotor Gateway: dashboard strategy for Home Assistant.
 *
 * Use it from the "Add dashboard" dialog (choose "Leapmotor"), or create a dashboard whose raw
 * configuration is only
 *     strategy:
 *       type: custom:leapmotor-gateway
 *
 * Every vehicle gets its views, built from cards that ship with Home Assistant plus the trip
 * computer card below. Which entity has which role comes from the integration (WebSocket
 * leapmotor_gateway/dashboard). There are no fixed entity ids in this file.
 *
 * Interaction rules:
 * - Every action on the car asks for confirmation first, also when the icon is tapped.
 * - Blocked commands get no button. For commands that need approval the dialog says that the
 *   approval request will be sent to the phone.
 * - The lock tile only shows the state. Unlocking is not offered here.
 * - The climate tile opens the Home Assistant dialog. Its icon does not switch anything.
 * The gateway does the actual checks (role, level, approval).
 */

const TEXTS = {
  en: {
    tripComputer: "Trip computer", status: "Status", climate: "Climate", tyres: "Tyre pressure",
    controls: "Controls", doors: "Doors and windows", charging: "Charging", location: "Location", energy: "Energy",
    gateway: "Gateway", insideTemp: "Cabin temperature",
    preset: "Preset (not sent to the car yet)", commands: "Commands to the car", lock: "Lock",
    confirm: (title) => `${title}: run now?`,
    approval: " You will then be asked to approve it on your phone.",
    tyreHint: "Warning below 2.3 bar, above 3.2 bar or more than 0.2 bar difference on one axle.",
    empty: "No vehicle has been set up in the Leapmotor Gateway yet.",
    history: "History",
    card: { battery: "Battery", range: "Range", energy: "Energy in battery", status: "Status", distance: "Distance",
            drivingTime: "Driving time", consumption: "Ø consumption", totalEnergy: "Total energy", lastTrip: "Last trip",
            currentTrip: "Current trip", since: "since", todayEmpty: "No trip today yet. The shares appear after the first trip of the day.",
            energyToday: "Energy shares today", todayAll: "all trips and standby of the day", sinceCharge: "Since last charge",
            sinceChargeEnergy: "Energy use since last charge", week: "shares of the last week", odometer: "Odometer",
            sixWeeks: "Ø of the last 6 weeks", driving: "Driving", climate: "Climate", other: "Other", trips: "Trips",
            charges: "Charges", start: "Start", duration: "Duration", chargeStart: "Start · place", type: "Type",
            charged: "Charged", inBattery: "in battery", newest: "Newest", newer: "Newer", older: "Older",
            page: (p, n, count, what) => `Page ${p} of ${n} · ${count} ${what}`,
            backfilled: "* added later (between two polls, driving time unknown)", noTrip: "No completed trip yet.",
            noCharge: "No charge yet." },
    error: "The Leapmotor Gateway integration does not respond. Is it set up, and has Home Assistant been restarted since the update?",
  },
  de: {
    tripComputer: "Bordcomputer", status: "Zustand", climate: "Klima", tyres: "Reifendruck",
    controls: "Bedienen", doors: "Türen und Fenster", charging: "Laden", location: "Standort", energy: "Energie",
    gateway: "Gateway", insideTemp: "Innentemperatur",
    preset: "Vorwahl (schickt noch nichts ans Auto)", commands: "Befehle ans Auto", lock: "Verriegeln",
    confirm: (title) => `${title}: jetzt ausführen?`,
    approval: " Danach kommt die Freigabe aufs Telefon.",
    tyreHint: "Warnung bei unter 2,3 bar, über 3,2 bar oder mehr als 0,2 bar Unterschied auf einer Achse.",
    empty: "Im Leapmotor Gateway ist noch kein Fahrzeug eingerichtet.",
    history: "Historie",
    card: { battery: "Akku", range: "Reichweite", energy: "Energie im Akku", status: "Status", distance: "Strecke",
            drivingTime: "Fahrzeit", consumption: "Ø Verbrauch", totalEnergy: "Gesamt­energie", lastTrip: "Letzte Fahrt",
            currentTrip: "Aktuelle Fahrt", since: "seit", todayEmpty: "Heute noch keine Fahrt. Die Anteile erscheinen nach der ersten Fahrt des Tages.",
            energyToday: "Energie­anteile heute", todayAll: "alle Fahrten und Standzeit des Tages", sinceCharge: "Seit letztem Laden",
            sinceChargeEnergy: "Energie­verbrauch seit letztem Laden", week: "Anteile der letzten Woche", odometer: "Kilometerstand",
            sixWeeks: "Ø der letzten 6 Wochen", driving: "Fahren", climate: "Klimaanlage", other: "Sonstiges", trips: "Fahrten",
            charges: "Ladungen", start: "Beginn", duration: "Dauer", chargeStart: "Ladebeginn · Ort", type: "Art",
            charged: "Geladen", inBattery: "im Akku", newest: "Neueste", newer: "Neuer", older: "Älter",
            page: (p, n, count, what) => `Seite ${p} von ${n} · ${count} ${what}`,
            backfilled: "* nachgetragen (zwischen zwei Abfragen, Fahrzeit unbekannt)", noTrip: "Noch keine abgeschlossene Fahrt.",
            noCharge: "Noch keine Ladung." },
    error: "Die Integration Leapmotor Gateway antwortet nicht. Ist sie eingerichtet und Home Assistant seit dem Update neu gestartet?",
  },
};

const language = (hass) => String(hass?.locale?.language || hass?.language || "en");
const texts = (hass) => TEXTS[language(hass).slice(0, 2)] || TEXTS.en;

// Display tiles per section (roles = entity keys of the integration)
const DISPLAY = {
  vehicle: ["battery", "range", "odometer", "energy", "ready", "outside_temp", "vehicle_time"],
  climate: ["outside_temp", "target_temp_left", "fan_speed"],
  doors: ["driver_door", "passenger_door", "rear_left_door", "rear_right_door", "trunk",
          "window_fl", "window_fr", "window_rl", "window_rr", "sunshade"],
  charging: ["charging", "charge_state", "charging_power", "charge_time_left", "charge_end_forecast", "energy_to_charge",
             "ac_cable", "dc_cable", "battery_current", "battery_voltage", "battery_temp_min"],
  tyres: ["tyre_fl", "tyre_fr", "tyre_rl", "tyre_rr"],
  energy: ["today_driving_percent", "today_climate_percent", "today_other_percent", "week_driving_percent",
           "week_climate_percent", "week_other_percent", "total_energy_kwh", "distance_7_days_km", "consumption_6_weeks"],
  gateway: ["status", "approvals_pending", "last_command", "last_message", "messages_unread", "trip_end_min"],
};
const HEADER_BADGES = ["plate", "state", "battery", "range"];
const TOP_VIEW = "/leapmotor_gateway/frontend/top-view.svg";

const NONE = { action: "none" };
const HALF = { columns: 6 };
const FULL = { columns: 12 };

const group = (command) =>
  /^(start_charging|stop_charging|release_charging_cable)$/.test(command) ? "charging"
    : /^(climate_|quick_|defrost_|battery_preheat)/.test(command) ? "climate" : "controls";

function views(vehicle, data, t, index, several) {
  const role = (r) => vehicle.entities[r];
  const display = (r) => role(r) && { type: "tile", entity: role(r).entity_id, name: role(r).name, grid_options: HALF };
  const button = (entityId, name, icon, text, service) => {
    const action = { action: "perform-action", perform_action: service, target: { entity_id: entityId }, confirmation: { text } };
    return { type: "tile", entity: entityId, name, icon, hide_state: true, vertical: false, grid_options: HALF,
             tap_action: action, icon_tap_action: action, hold_action: NONE, double_tap_action: NONE };
  };
  const question = (c) => t.confirm(c.title) + (c.level === "approval" && data.approval_push ? t.approval : "");
  const buttons = (g) => Object.entries(data.commands || {})
    .filter(([n, c]) => c.button && c.level !== "blocked" && group(n) === g && role(`button_${n}`))
    .map(([n, c]) => button(role(`button_${n}`).entity_id, role(`button_${n}`).name || c.title, c.icon, question(c), "button.press"));
  const section = (title, icon, cards) => {
    const present = cards.filter(Boolean);
    return present.length ? { type: "grid", cards: [{ type: "heading", heading: title, icon }, ...present] } : null;
  };

  const dashboard = (typeof location !== "undefined" && location.pathname.split("/")[1]) || "";
  const base = `vehicle-${index + 1}`;
  const title = (x) => (several ? `${vehicle.name} · ${x}` : x);
  const tyrePath = `${dashboard ? "/" + dashboard : ""}/${base}-tyres`;
  const tyreTile = role("tyre_pressure") && {
    type: "tile", entity: role("tyre_pressure").entity_id, name: role("tyre_pressure").name, grid_options: HALF,
    tap_action: { action: "navigate", navigation_path: tyrePath }, icon_tap_action: { action: "navigate", navigation_path: tyrePath },
    hold_action: NONE, double_tap_action: NONE };
  const lockDisplay = role("lock") && {
    type: "tile", entity: role("lock").entity_id, name: role("lock").name, grid_options: HALF,
    tap_action: NONE, icon_tap_action: NONE, hold_action: NONE, double_tap_action: NONE };
  const header = { type: "heading", heading: vehicle.name, icon: "mdi:car-electric",
                   badges: HEADER_BADGES.filter(role).map((r) => ({ type: "entity", entity: role(r).entity_id, show_icon: r !== "plate", show_state: true })) };
  const lock = data.commands?.lock;
  const climateOn = data.commands?.climate_on;

  // The trip computer is its own card (below), so the strategy needs no custom cards from HACS
  const roleIds = Object.fromEntries(Object.entries(vehicle.entities).map(([r, e]) => [r, e.entity_id]));
  const card = (part) => ({ type: `custom:${CARD}`, part, entities: roleIds, grid_options: FULL });
  const tripComputer = { title: title(t.tripComputer), path: `${base}-trip-computer`, icon: "mdi:gauge", type: "sections",
    max_columns: 2, sections: [
      section(t.tripComputer, "mdi:car-electric", [card("overview"), tyreTile]),
      section(t.history, "mdi:history", [role("trips") && card("trips"), role("charges") && card("charges")]),
    ].filter(Boolean) };

  const status = { title: title(t.status), path: `${base}-status`, icon: "mdi:car-info", type: "sections", max_columns: 4,
    sections: [
      { type: "grid", cards: [
        header,
        // Hidden while there is no image (first poll pending, or no image for this model).
        role("image") && { type: "conditional",
                           conditions: [{ condition: "state", entity: role("image").entity_id, state_not: "unavailable" }],
                           card: { type: "picture-entity", entity: role("image").entity_id, show_name: false, show_state: false,
                                   tap_action: NONE, hold_action: NONE, double_tap_action: NONE } },
        ...DISPLAY.vehicle.map(display), lockDisplay, tyreTile,
      ].filter(Boolean) },
      section(t.controls, "mdi:gesture-tap", [
        role("lock") && lock?.level !== "blocked" &&
          button(role("lock").entity_id, t.lock, "mdi:car-key", question({ ...(lock || {}), title: t.lock }), "lock.lock"),
        ...buttons("controls"),
      ]),
      section(t.doors, "mdi:car-door", DISPLAY.doors.map(display)),
      section(t.charging, "mdi:ev-station", [...DISPLAY.charging.map(display), ...buttons("charging")]),
      section(t.location, "mdi:map-marker", [
        role("location") && { type: "map", entities: [{ entity: role("location").entity_id }], default_zoom: 15, hours_to_show: 0 },
        display("location"),
      ]),
      section(t.energy, "mdi:lightning-bolt", DISPLAY.energy.map(display)),
      section(t.gateway, "mdi:router-wireless", [
        ...DISPLAY.gateway.map(display),
        role("button_refresh") && button(role("button_refresh").entity_id, role("button_refresh").name,
                                         "mdi:refresh", t.confirm(role("button_refresh").name), "button.press"),
      ]),
    ].filter(Boolean) };

  // Climate as in the Leapmotor app: preset first, then "Start climate" (with confirmation)
  const preset = (r, features) => role(r) && { type: "tile", entity: role(r).entity_id, name: role(r).name,
                                               grid_options: FULL, features };
  const climate = { title: title(t.climate), path: `${base}-climate`, icon: "mdi:air-conditioner", type: "sections", max_columns: 2,
    sections: [
      section(t.insideTemp, "mdi:thermometer", [
        role("climate_control") && { type: "tile", entity: role("climate_control").entity_id, name: role("climate_control").name,
                                     grid_options: HALF, tap_action: { action: "more-info" }, icon_tap_action: NONE,
                                     hold_action: NONE, double_tap_action: NONE },
        ...DISPLAY.climate.map(display),
      ]),
      section(t.preset, "mdi:tune-variant", [
        preset("preset_mode", [{ type: "select-options" }]),
        preset("preset_temperature", [{ type: "numeric-input", style: "buttons" }]),
        preset("preset_fan_speed", [{ type: "numeric-input", style: "slider" }]),
        role("preset_recirculation") && { type: "tile", entity: role("preset_recirculation").entity_id,
                                          name: role("preset_recirculation").name, grid_options: HALF,
                                          tap_action: { action: "toggle" }, icon_tap_action: { action: "toggle" },
                                          hold_action: NONE, double_tap_action: NONE },
      ]),
      section(t.commands, "mdi:send", [
        role("start_climate") && climateOn?.level !== "blocked" &&
          button(role("start_climate").entity_id, role("start_climate").name, "mdi:play-circle",
                 question({ ...(climateOn || {}), title: role("start_climate").name }), "button.press"),
        ...buttons("climate"),
      ]),
    ].filter(Boolean) };

  // Subview "Tyre pressure": top view with the four pressures at the wheels
  const pressure = (r, top, left) => role(r) && { type: "state-label", entity: role(r).entity_id,
                                                   style: { top, left, "font-size": "22px", "font-weight": "500" } };
  const tyres = role("tyre_fl") && { title: t.tyres, path: `${base}-tyres`, subview: true, icon: "mdi:tire",
    type: "sections", max_columns: 1, sections: [{ type: "grid", cards: [
      { type: "heading", heading: title(t.tyres), icon: "mdi:tire" },
      { type: "picture-elements", image: TOP_VIEW, grid_options: FULL,
        elements: [pressure("tyre_fl", "25%", "17%"), pressure("tyre_fr", "25%", "83%"),
                   pressure("tyre_rl", "75%", "17%"), pressure("tyre_rr", "75%", "83%")].filter(Boolean) },
      { type: "markdown", content: t.tyreHint, grid_options: FULL },
      ...DISPLAY.tyres.map(display),
    ].filter(Boolean) }] };

  return [tripComputer, status, climate, tyres].filter(Boolean);
}

// ── Trip computer card ───────────────────────────────────────────────────
// A plain element instead of custom:button-card, so the strategy works without HACS.
// Parts: overview, trips, charges. Configuration: { part, entities: { role: entity_id } }, roles as above.
const CARD = "leapmotor-trip-computer-card";
const COLORS = { driving: "#5fd8d0", climate: "#62d26f", other: "#f2c744" };
const CARD_STYLE = `
ha-card{padding:14px 16px}
.k{color:var(--secondary-text-color);font-size:12px;font-weight:400}
.head{display:flex;gap:18px;flex-wrap:wrap;justify-content:space-between;margin-bottom:10px;font-size:14px}
.head b{font-size:20px;font-weight:500}
table{width:100%;border-collapse:collapse;font-size:15px;table-layout:fixed}
th{font-weight:600;color:var(--secondary-text-color);text-align:left;padding:8px 6px;border-bottom:1px solid var(--divider-color);font-size:13px;vertical-align:bottom}
td{padding:10px 6px;border-bottom:1px solid var(--divider-color);text-align:left;vertical-align:top}
td:first-child{font-weight:600;overflow-wrap:break-word;hyphens:manual}
th{hyphens:manual}
tr:last-child td{border-bottom:none}
.h td{padding:7px 6px;font-size:14px} .r{text-align:right!important}
.gap{height:14px}
.bar{display:flex;height:12px;border-radius:6px;overflow:hidden;background:var(--divider-color);margin:2px 0 6px}
.bar span{display:block;height:100%}
.leg{font-size:12px;color:var(--secondary-text-color);font-weight:400}
.leg i{display:inline-block;width:10px;height:10px;border-radius:50%;margin:0 4px 0 10px;vertical-align:-1px}
.leg i:first-child{margin-left:0}
h3{margin:2px 0 6px;font-size:16px;font-weight:500;color:${COLORS.driving}}
.paging{display:flex;gap:8px;margin-top:10px}
.paging button{flex:1;padding:8px;border-radius:10px;border:1px solid var(--divider-color);background:none;color:var(--primary-text-color);font:inherit;font-size:13px;cursor:pointer}
.paging button:disabled{opacity:.4;cursor:default}`;
const PER_PAGE = 8;

// Configuration of the card before the English port (name leapmotor-bordcomputer-card)
const LEGACY_PARTS = { uebersicht: "overview", fahrten: "trips", ladungen: "charges" };
const LEGACY_ROLES = {
  akku: "battery", reichweite: "range", energie: "energy", zustand: "state", kilometerstand: "odometer",
  fahrt_km: "trip_distance", fahrt_dauer: "trip_duration", fahrt_energie: "trip_energy", fahrt_verbrauch: "trip_consumption",
  seit_laden_km: "since_charge_distance", seit_laden_energie: "since_charge_energy", seit_laden_verbrauch: "since_charge_consumption",
  heute_fahren_prozent: "today_driving_percent", heute_klima_prozent: "today_climate_percent",
  heute_sonstiges_prozent: "today_other_percent", woche_fahren_prozent: "week_driving_percent",
  woche_klima_prozent: "week_climate_percent", woche_sonstiges_prozent: "week_other_percent",
  gesamtenergie_kwh: "total_energy_kwh", verbrauch_6_wochen: "consumption_6_weeks", fahrten: "trips", ladungen: "charges",
};

function migrateConfig(config) {
  if (!config || config.entities || !config.entitaeten) return config;
  const { teil, entitaeten, ...rest } = config;
  return { ...rest, part: LEGACY_PARTS[teil] || teil,
           entities: Object.fromEntries(Object.entries(entitaeten).map(([r, e]) => [LEGACY_ROLES[r] || r, e])) };
}

class LeapmotorTripComputerCard extends HTMLElement {
  setConfig(config) {
    const c = migrateConfig(config);
    if (!c || !c.entities) throw new Error("entities missing");
    this._c = { part: "overview", ...c };
    this._page = 1;
    if (!this.shadowRoot) this.attachShadow({ mode: "open" });
  }

  set hass(hass) {
    this._hass = hass;
    const stamp = Object.values(this._c.entities).map((e) => hass.states[e]?.last_updated).join("|");
    if (stamp !== this._stamp) { this._stamp = stamp; this._render(); }
  }

  getCardSize() { return this._c?.part === "overview" ? 7 : 6; }
  getGridOptions() { return { columns: 12 }; }

  _t() { return texts(this._hass).card; }
  _st(role) { const e = this._c.entities[role]; return e ? this._hass.states[e] : undefined; }
  _val(role) { const s = this._st(role); return s && !["unknown", "unavailable"].includes(s.state) ? s.state : undefined; }
  _n(v, digits = 1) {
    const x = parseFloat(v);
    return isNaN(x) ? "–" : x.toLocaleString(language(this._hass), { minimumFractionDigits: digits, maximumFractionDigits: digits });
  }
  _time(s) {
    return s ? new Date(s).toLocaleString(language(this._hass), { weekday: "short", day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" }) : "–";
  }
  _duration(min) {
    const m = Math.round(parseFloat(min)); if (isNaN(m)) return "–";
    return m < 60 ? `${m} min` : `${Math.floor(m / 60)} h` + (m % 60 ? ` ${m % 60} min` : "");
  }
  _esc(x) { return String(x ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c])); }
  _items(role) { return this._st(role)?.attributes?.items || []; }
  _bar(driving, climate, other, note) {
    const t = this._t(), [a, b, c] = [driving, climate, other].map((x) => parseFloat(x) || 0), sum = a + b + c || 1;
    return `<div class="bar"><span style="width:${a / sum * 100}%;background:${COLORS.driving}"></span><span style="width:${b / sum * 100}%;background:${COLORS.climate}"></span><span style="width:${c / sum * 100}%;background:${COLORS.other}"></span></div>
      <div class="leg"><i style="background:${COLORS.driving}"></i>${t.driving} ${this._n(a)} % <i style="background:${COLORS.climate}"></i>${t.climate} ${this._n(b)} % <i style="background:${COLORS.other}"></i>${t.other} ${this._n(c)} % · ${note}</div>`;
  }

  _overview() {
    const t = this._t(), state = this._st("state");
    const status = state ? (this._hass.formatEntityState ? this._hass.formatEntityState(state) : state.state) : "–";
    const driving = state?.state === "driving";
    const trip = this._items("trips")[0], charge = this._items("charges")[0];
    const today = ["today_driving_percent", "today_climate_percent", "today_other_percent"].map((r) => this._val(r));
    const week = ["week_driving_percent", "week_climate_percent", "week_other_percent"].map((r) => this._val(r));
    const todayEmpty = today.every((v) => !(parseFloat(v) > 0));
    const row = (title, sub, ...cells) => `<tr><td>${title}${sub ? `<div class="k">${sub}</div>` : ""}</td>${cells.map((c) => `<td>${c}</td>`).join("")}</tr>`;
    return `<div class="head">
      <div><span class="k">${t.battery}</span><br><b>${this._n(this._val("battery"), 0)} %</b></div>
      <div><span class="k">${t.range}</span><br><b>${this._n(this._val("range"), 0)} km</b></div>
      <div><span class="k">${t.energy}</span><br><b>${this._n(this._val("energy"))} kWh</b></div>
      <div><span class="k">${t.status}</span><br><b>${this._esc(status)}</b></div></div>
      <table><tr><th style="width:30%"></th><th>${t.distance}</th><th>${t.drivingTime}</th><th>${t.consumption}</th></tr>
      ${row(driving ? t.currentTrip : t.lastTrip, trip && !driving ? `${t.since} ${this._time(trip.start)}` : "",
            `${this._n(this._val("trip_distance"))} km`, this._duration(this._val("trip_duration")), `${this._n(this._val("trip_consumption"))} kWh/100 km`)}
      ${week.some((v) => v !== undefined) || today.some((v) => v !== undefined)
        ? `<tr><td>${t.energyToday}</td><td colspan="3">${todayEmpty ? `<div class="k">${t.todayEmpty}</div>` : this._bar(...today, t.todayAll)}</td></tr>` : ""}
      </table><div class="gap"></div>
      <table><tr><th style="width:30%"></th><th>${t.distance}</th><th>${t.totalEnergy}</th><th>${t.consumption}</th></tr>
      ${row(t.sinceCharge, charge ? this._time(charge.end || charge.start) : "",
            `${this._n(this._val("since_charge_distance"))} km`, `${this._n(this._val("since_charge_energy"))} kWh`, `${this._n(this._val("since_charge_consumption"))} kWh/100 km`)}
      ${week.some((v) => v !== undefined) ? `<tr><td>${t.sinceChargeEnergy}</td><td colspan="3">${this._bar(...week, t.week)}</td></tr>` : ""}
      ${row(t.odometer, this._val("consumption_6_weeks") !== undefined ? t.sixWeeks : "",
            `${this._n(this._val("odometer"), 0)} km`, `${this._n(this._val("total_energy_kwh"))} kWh`,
            this._val("consumption_6_weeks") !== undefined ? `${this._n(this._val("consumption_6_weeks"))} kWh/100 km` : "–")}
      </table>`;
  }

  _history(part) {
    const t = this._t(), items = this._items(part), pages = Math.max(1, Math.ceil(items.length / PER_PAGE));
    this._page = Math.min(Math.max(1, this._page), pages);
    const shown = items.slice((this._page - 1) * PER_PAGE, this._page * PER_PAGE);
    let head, rows;
    if (part === "trips") {
      head = `<tr><th style="width:32%">${t.start}</th><th class="r">${t.distance}</th><th class="r">${t.duration}</th><th class="r">kWh</th><th class="r">kWh/100 km</th></tr>`;
      rows = shown.map((f) => `<tr><td>${this._time(f.start)}${f.backfilled ? " *" : ""}</td><td class="r">${this._n(f.km)} km</td>
        <td class="r">${f.h == null ? "–" : this._duration(f.h * 60)}</td><td class="r">${this._n(f.kwh, 2)}</td><td class="r">${this._n(f.consumption)}</td></tr>`).join("")
        || `<tr><td colspan="5">${t.noTrip}</td></tr>`;
    } else {
      head = `<tr><th style="width:44%">${t.chargeStart}</th><th class="r">${t.type}</th><th class="r">${t.charged}</th></tr>`;
      rows = shown.map((c) => `<tr><td>${this._time(c.start)}<div class="k">${this._esc(c.place || "–")}</div></td><td class="r">${this._esc(c.type || "–")}</td>
        <td class="r">${this._n(c.charged_kwh)} kWh<div class="k">${c.soc_from != null ? `${this._n(c.soc_from, 0)} → ${this._n(c.soc_to, 0)} %` : t.inBattery}</div></td></tr>`).join("")
        || `<tr><td colspan="3">${t.noCharge}</td></tr>`;
    }
    const backfilled = part === "trips" && items.some((f) => f.backfilled) ? ` · ${t.backfilled}` : "";
    const what = part === "trips" ? t.trips : t.charges;
    return `<h3>${what}</h3><table class="h">${head}${rows}</table>
      <div class="k" style="margin-top:6px">${t.page(this._page, pages, items.length, what)}${backfilled}</div>
      ${pages > 1 ? `<div class="paging"><button data-p="1"${this._page === 1 ? " disabled" : ""}>« ${t.newest}</button>
        <button data-p="${this._page - 1}"${this._page === 1 ? " disabled" : ""}>‹ ${t.newer}</button>
        <button data-p="${this._page + 1}"${this._page === pages ? " disabled" : ""}>${t.older} ›</button></div>` : ""}`;
  }

  _render() {
    if (!this._hass || !this.shadowRoot) return;
    const content = this._c.part === "overview" ? this._overview() : this._history(this._c.part);
    this.shadowRoot.innerHTML = `<style>${CARD_STYLE}</style><ha-card>${content}</ha-card>`;
    this.shadowRoot.querySelectorAll(".paging button").forEach((b) =>
      b.addEventListener("click", () => { this._page = parseInt(b.dataset.p, 10); this._render(); }));
  }
}

// The same card under its name before the English port, so existing dashboards keep working
const LEGACY_CARD = "leapmotor-bordcomputer-card";
class LeapmotorTripComputerCardLegacy extends LeapmotorTripComputerCard {}

const notice = (title, text) => ({ title, views: [{ title, cards: [{ type: "markdown", content: text }] }] });

class LeapmotorGatewayStrategy extends HTMLElement {
  static async generate(config, hass) {
    const t = texts(hass);
    const title = config.title || "Leapmotor";
    let data;
    try {
      data = await hass.callWS({ type: "leapmotor_gateway/dashboard" });
    } catch (err) {
      return notice(title, t.error);
    }
    if (!data?.vehicles?.length) return notice(title, t.empty);
    const several = data.vehicles.length > 1;
    return { title, views: data.vehicles.flatMap((v, i) => views(v, data, t, i, several)) };
  }
}

// Register only once the Home Assistant app exists: at start the frontend replaces
// window.customElements with its own scoped registry. Elements defined earlier are not found
// there ("Timeout waiting for strategy element"). Home Assistant loads this file very early
// (add_extra_js_url), before the app.
const STRATEGY = "ll-strategy-dashboard-leapmotor-gateway";
customElements.whenDefined("home-assistant").then(() => {
  if (!window.customElements.get(STRATEGY)) window.customElements.define(STRATEGY, LeapmotorGatewayStrategy);
  if (!window.customElements.get(CARD)) window.customElements.define(CARD, LeapmotorTripComputerCard);
  if (!window.customElements.get(LEGACY_CARD)) window.customElements.define(LEGACY_CARD, LeapmotorTripComputerCardLegacy);
});
window.customCards = window.customCards || [];
if (!window.customCards.some((c) => c.type === CARD)) {
  window.customCards.push({ type: CARD, name: "Leapmotor trip computer",
    description: "Current or last trip, since last charge, energy shares. Or the trips or charges as a paged list." });
}

// Offer it in the "Add dashboard" dialog
window.customStrategies = window.customStrategies || [];
if (!window.customStrategies.some((s) => s.type === "leapmotor-gateway")) {
  window.customStrategies.push({
    type: "leapmotor-gateway", strategyType: "dashboard", name: "Leapmotor",
    description: "One set of views per vehicle of the Leapmotor Gateway: trip computer, status, controls with confirmation, charging, map.",
  });
}
