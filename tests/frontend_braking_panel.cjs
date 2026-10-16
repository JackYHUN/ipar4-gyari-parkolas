/* Integration checks run the real app renderer with the real pure diagnostic
   analyzer. The small DOM records presentation only; it computes no braking. */
"use strict";
const assert = require("node:assert/strict");
const fs = require("node:fs"), path = require("node:path"), vm = require("node:vm");
require("../web/perception-analysis.js");
const source = fs.readFileSync(path.join(__dirname, "../web/app.js"), "utf8");
const markup = fs.readFileSync(path.join(__dirname, "../web/index.html"), "utf8");
const styles = fs.readFileSync(path.join(__dirname, "../web/perception.css"), "utf8");
const allIds = [...markup.matchAll(/\bid="([^"]+)"/g)].map(match => match[1]);
function extract(name) {
  const start = source.indexOf(`  function ${name}(`);
  assert.ok(start >= 0, `real production function ${name} exists`);
  const tail = source.slice(start), next = tail.search(/\n  (?:function |async function |const |let )/);
  return next >= 0 ? tail.slice(0, next) : tail;
}
class Element {
  constructor() { this.textContent = ""; this.className = ""; this.dataset = {}; this.style = {}; this.hidden = false; this.attributes = {}; }
  setAttribute(key, value) { this.attributes[key] = String(value); }
  getAttribute(key) { return this.attributes[key] ?? null; }
  removeAttribute(key) { delete this.attributes[key]; }
}
const format = (value, digits = 1) => Number(value).toLocaleString("hu-HU", {
  minimumFractionDigits: digits, maximumFractionDigits: digits});
const reading = (value, unit = "s", digits = 1) => value !== null && value !== undefined
  && Number.isFinite(Number(value)) ? `${format(value, digits)} ${unit}` : "—";
function harness() {
  const elements = new Map();
  const context = {window: {YardBrakingAnalysis: global.YardBrakingAnalysis}, reading,
    $: id => {
      assert.ok(allIds.includes(id), `renderer target ${id} exists in production HTML`);
      if (!elements.has(id)) elements.set(id, new Element());
      return elements.get(id);
    }};
  vm.createContext(context);
  vm.runInContext(extract("renderBrakingDecision"), context, {filename: "production:renderBrakingDecision"});
  return {context, render: (prefix, data) => context.renderBrakingDecision(prefix, data),
    get: (prefix, field) => context.$(`${prefix}-braking-${field}`)};
}
function frame() {
  return {telemetry_sim_time: 12, fresh: true, gap: false, archive: false,
    sensors: {schema_version: 1, source: "virtual_onboard_bundle", sampled: true,
      sample_sim_time_s: 11.9, fresh: true, fault_active: false},
    control: {source: "ideal_route_controller", decision_sim_time_s: 12,
      motion_interval_s: .1, speed_before_m_s: 3, speed_after_m_s: 2.8,
      target_speed_m_s: 0, applied_acceleration_m_s2: -2,
      braking_distance_m: 2.25, reaction_allowance_m: .3, braking_deceleration_m_s2: 2,
      stop_limit_remaining_m: 4, sensor_sample_sim_time_s: 11.9, target_state: "pedestrian_stop"}};
}
let checks = 0;
function test(name, callback) { callback(); checks++; process.stdout.write(`PASS ${name}\n`); }
const valueFields = ["speed", "acceleration", "limit", "reserve", "use"];
test("both native panels have unique renderer targets and load the merged analyzer before app", () => {
  for (const prefix of ["live", "trip"]) {
    for (const suffix of ["panel", "title", "status", "phase", "clock", ...valueFields, "bar", "used", "marker", "note"]) {
      assert.equal(allIds.filter(id => id === `${prefix}-braking-${suffix}`).length, 1, `${prefix} ${suffix}`);
    }
    assert.match(markup, new RegExp(`id="${prefix}-braking-panel"[^>]*aria-labelledby="${prefix}-braking-title"`));
    assert.match(markup, new RegExp(`id="${prefix}-braking-bar"[^>]*role="img"[^>]*hidden`));
  }
  const analyzer = [...markup.matchAll(/<script\b[^>]*src="\/perception-analysis\.js"[^>]*>/g)];
  assert.equal(analyzer.length, 1); assert.ok(analyzer[0][0].includes("defer"));
  assert.ok(analyzer[0].index < markup.indexOf('src="/app.js"'));
  assert.ok(!markup.includes("braking-analysis.js"), "no unsupported extra HTTP asset");
  assert.match(extract("renderPerception"), /renderBrakingDecision\(prefix,data\);/);
  assert.match(styles, /\.braking-distance-bar\[hidden\]\{display:none\}/);
  assert.match(styles, /\.braking-panel\[data-reserve="negative"\]/);
  assert.match(styles, /@container\(max-width:640px\)/);
});
test("a moving zebra-braking decision displays speeds, deceleration, reserve and accessible relative bar", () => {
  const h = harness(); h.render("live", frame());
  assert.equal(h.get("live", "panel").dataset.state, "braking");
  assert.equal(h.get("live", "phase").textContent, "Fékezés folyamatban");
  assert.equal(h.get("live", "speed").textContent, "3,00 m/s → 2,80 m/s");
  assert.equal(h.get("live", "acceleration").textContent, "-2,00 m/s²");
  assert.equal(h.get("live", "reserve").textContent, "1,75 m");
  assert.equal(h.get("live", "use").textContent, "100 %");
  assert.equal(h.get("live", "bar").hidden, false);
  assert.equal(h.get("live", "used").style.width, "56.25%");
  assert.equal(h.get("live", "marker").style.left, "100%");
  assert.match(h.get("live", "bar").getAttribute("aria-label"), /2,25 m.*4,00 m/);
});
test("stopping and acceleration labels follow reported motion rather than a waiting target", () => {
  const h = harness(), stopped = frame();
  Object.assign(stopped.control, {speed_before_m_s: .2, speed_after_m_s: 0, braking_distance_m: .01});
  h.render("live", stopped);
  assert.equal(h.get("live", "phase").textContent, "Megállt");
  const accelerating = frame(); Object.assign(accelerating.control, {speed_after_m_s: 3.1, applied_acceleration_m_s2: 1});
  h.render("live", accelerating);
  assert.equal(h.get("live", "phase").textContent, "Gyorsítás");
  assert.equal(h.get("live", "use").textContent, "0 %");
});
test("held replay observations keep historical numbers with explicit retained and gap notices", () => {
  const h = harness(), data = frame(); data.gap = true; data.archive = true;
  h.render("trip", data);
  assert.equal(h.get("trip", "panel").dataset.state, "retained");
  assert.equal(h.get("trip", "status").textContent, "Megőrzött döntés");
  assert.equal(h.get("trip", "status").className, "data-badge warn");
  assert.match(h.get("trip", "clock").textContent, /visszajátszási adatrés/);
  assert.match(h.get("trip", "note").textContent, /új vagy élő észlelést nem jelentenek/);
  assert.equal(h.get("trip", "reserve").textContent, "1,75 m");
});
test("missing, malformed and future decisions clear all previous displayed measurements and bar label", () => {
  for (const invalid of [undefined, {}, {control: {}}, {control: {source: "inferred"}},
    (() => { const d = frame(); d.control.motion_interval_s = "0.1"; return d; })(),
    (() => { const d = frame(); d.control.decision_sim_time_s = 12.5; return d; })()]) {
    const h = harness(); h.render("live", frame()); h.render("live", invalid);
    assert.equal(h.get("live", "panel").dataset.state, "unknown");
    assert.equal(h.get("live", "panel").dataset.reserve, "unknown");
    assert.equal(h.get("live", "phase").textContent, "Nem állapítható meg");
    for (const field of valueFields) assert.equal(h.get("live", field).textContent, "—", field);
    assert.equal(h.get("live", "bar").hidden, true);
    assert.equal(h.get("live", "bar").getAttribute("aria-label"), null);
    assert.match(h.get("live", "clock").textContent, /döntésre várunk/);
  }
});
test("contradictory diagnostics preserve reported speeds but suppress derived values and visual comparison", () => {
  const h = harness(), data = frame(); data.control.braking_distance_m = 1;
  h.render("live", frame()); h.render("live", data);
  assert.equal(h.get("live", "panel").dataset.state, "inconsistent");
  assert.equal(h.get("live", "status").className, "data-badge warn");
  assert.equal(h.get("live", "speed").textContent, "3,00 m/s → 2,80 m/s");
  assert.equal(h.get("live", "reserve").textContent, "—");
  assert.equal(h.get("live", "use").textContent, "—");
  assert.equal(h.get("live", "bar").hidden, true);
  assert.equal(h.get("live", "bar").getAttribute("aria-label"), null);
  assert.match(h.get("live", "note").textContent, /nem illeszkednek/);
});
test("an unassigned stopping target keeps kinematics without inventing a limit, reserve or bar", () => {
  const h = harness(), data = frame(); data.control.stop_limit_remaining_m = null;
  h.render("live", data);
  assert.equal(h.get("live", "limit").textContent, "—");
  assert.equal(h.get("live", "reserve").textContent, "—");
  assert.equal(h.get("live", "use").textContent, "100 %");
  assert.equal(h.get("live", "phase").textContent, "Fékezés folyamatban");
  assert.equal(h.get("live", "bar").hidden, true);
});
test("negative model reserve remains signed and gets a distinct panel state without clipping geometry", () => {
  const h = harness(), data = frame(); data.control.stop_limit_remaining_m = 1;
  h.render("live", data);
  assert.equal(h.get("live", "reserve").textContent, "-1,25 m");
  assert.equal(h.get("live", "panel").dataset.reserve, "negative");
  assert.equal(h.get("live", "used").style.width, "100%");
  assert.ok(Math.abs(parseFloat(h.get("live", "marker").style.left) - 100 / 2.25) < 1e-9);
  assert.match(h.get("live", "note").textContent, /nem igazolt fizikai biztonsági távolság/);
});
test("live and archive use the same decision interpretation and maintain independent panel state", () => {
  const h = harness(), live = frame(), archived = structuredClone(live); archived.archive = true;
  h.render("live", live); h.render("trip", archived);
  for (const field of ["status", "phase", "clock", ...valueFields, "note"])
    assert.equal(h.get("live", field).textContent, h.get("trip", field).textContent, field);
  assert.deepEqual(h.get("live", "panel").dataset, h.get("trip", "panel").dataset);
  h.render("trip", {});
  assert.equal(h.get("trip", "panel").dataset.state, "unknown");
  assert.equal(h.get("live", "panel").dataset.state, "braking");
  assert.equal(h.get("live", "reserve").textContent, "1,75 m");
});
test("good to unavailable to good updates restore only the newest decision without mutating its input", () => {
  const h = harness(), first = frame(), last = frame();
  last.control.stop_limit_remaining_m = 5; const before = structuredClone(last);
  h.render("live", first); h.render("live", null); h.render("live", last);
  assert.equal(h.get("live", "reserve").textContent, "2,75 m");
  assert.equal(h.get("live", "panel").dataset.state, "braking");
  assert.equal(h.get("live", "bar").hidden, false);
  assert.equal(h.get("live", "used").style.width, "45%");
  assert.match(h.get("live", "bar").getAttribute("aria-label"), /5,00 m/);
  assert.deepEqual(last, before);
});
test("the actual JSON download exports the same derived diagnostic and keeps raw observations even when unavailable", () => {
  for (const invalid of [false, true]) {
    const h = harness(), data = frame(), submitted = [], messages = [];
    Object.assign(data, {car_id: "CAR001", run_id: "BRAKING-EXPORT", telemetry_source: "snapshot"});
    if (invalid) data.control.source = "unrecognized";
    const before = structuredClone(data), c = h.context;
    c.perceptionFrames = new Map([["live", data]]); c.selectedProximityChannel = () => null;
    c.TextEncoder = TextEncoder; c.URLSearchParams = URLSearchParams;
    c.number = (value, fallback = 0) => typeof value === "number" && Number.isFinite(value) ? value : fallback;
    c.showToast = message => messages.push(message); c.setTimeout = callback => callback();
    c.document = {
      body: {append() {}},
      createElement(tag) {
        const element = {tag, children: [], append(child) { this.children.push(child); }, remove() {}};
        if (tag === "form") element.submit = () => submitted.push({action: element.action,
          fields: Object.fromEntries(element.children.map(child => [child.name, child.value]))});
        return element;
      },
    };
    vm.runInContext(extract("preparePerceptionDownload"), c, {filename: "production:preparePerceptionDownload"});
    let prevented = false;
    c.preparePerceptionDownload({preventDefault() { prevented = true; }}, "live", "json");
    assert.equal(prevented, true); assert.equal(submitted.length, 1); assert.equal(messages.length, 1);
    assert.equal(submitted[0].action, "/api/perception/export");
    assert.equal(submitted[0].fields.format, "json");
    const payload = JSON.parse(submitted[0].fields.content);
    assert.deepEqual(payload.control, data.control); assert.deepEqual(payload.sensors, data.sensors);
    assert.equal(payload.braking_analysis.available, !invalid);
    assert.equal(payload.braking_analysis.model_reserve_m, invalid ? null : 1.75);
    assert.equal(payload.braking_analysis.phase, invalid ? null : "braking");
    assert.equal(payload.view_context.fresh_in_view, true);
    assert.deepEqual(data, before);
  }
});
process.stdout.write(`PASS ${checks} received-braking panel integration checks\n`);
