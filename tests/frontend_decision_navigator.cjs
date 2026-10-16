/* Exercise the real received-decision navigator. The DOM records presentation;
   real analysis selects exact archive rows without HTTP or synthesized samples. */
"use strict";
const assert = require("node:assert/strict"), fs = require("node:fs"),
  path = require("node:path"), vm = require("node:vm");
require("../web/perception-analysis.js");
const source = fs.readFileSync(path.join(__dirname, "../web/app.js"), "utf8");
const markup = fs.readFileSync(path.join(__dirname, "../web/index.html"), "utf8");
const allIds = [...markup.matchAll(/\bid="([^"]+)"/g)].map(match => match[1]);
function extract(name) {
  const match = new RegExp(`^  (?:async )?function ${name}\\(`, "m").exec(source);
  assert.ok(match, `real production function ${name} exists`);
  const tail = source.slice(match.index), next = tail.search(/\n  (?:function |async function |const |let )/);
  return next < 0 ? tail : tail.slice(0, next);
}
class Element {
  constructor() {
    this.textContent = ""; this.disabled = false; this.hidden = false;
    this.dataset = {}; this.attributes = {}; this.handlers = {};
    this.scrolls = []; this.focuses = []; this.style = {};
    const names = new Set();
    this.classList = {toggle(name, value) {value ? names.add(name) : names.delete(name);}};
  }
  setAttribute(name, value) {this.attributes[name] = String(value);}
  removeAttribute(name) {delete this.attributes[name];}
  addEventListener(name, handler) {this.handlers[name] = handler;}
  scrollIntoView(options) {this.scrolls.push(options);}
  focus(options) {this.focuses.push(options);}
}
const fmt = (value, digits = 1) => Number(value).toLocaleString("hu-HU", {
  minimumFractionDigits: digits, maximumFractionDigits: digits});
const reading = (value, unit = "s", digits = 1) => value != null && Number.isFinite(Number(value))
  ? `${fmt(value, digits)} ${unit}` : "—";
function frame(index, overrides = {}) {
  const time = index / 10, speed = 3 - index / 10;
  return {id: 100 + index * 7, seq: index + 1, sim_time: time,
    sensors: {schema_version: 1, source: "virtual_onboard_bundle", sampled: true,
      sample_sim_time_s: time, fresh: true, fault_active: false},
    control: {source: "ideal_route_controller", decision_sim_time_s: time,
      motion_interval_s: .1, speed_before_m_s: speed, speed_after_m_s: speed - .1,
      target_speed_m_s: 0, applied_acceleration_m_s2: -1,
      braking_distance_m: speed * speed / 4, reaction_allowance_m: .3,
      braking_deceleration_m_s2: 2, stop_limit_remaining_m: 5 - index,
      sensor_sample_sim_time_s: time, target_state: "pedestrian_stop"}, ...overrides};
}
function harness(samples = [frame(0), frame(1), frame(2)]) {
  const elements = new Map(), rendered = [], calls = {summary: 0, stop: 0, requests: 0};
  const context = {$: id => {
    assert.ok(allIds.includes(id), `target ${id} exists in production markup`);
    if (!elements.has(id)) elements.set(id, new Element());
    return elements.get(id);
  }, window: {matchMedia: () => ({matches: false}), YardBrakingAnalysis: {summarizeWindow(rows, options) {
    calls.summary++; return global.YardBrakingAnalysis.summarizeWindow(rows, options);
  }}}, reading, fmt, number: (value, fallback = 0) => Number.isFinite(Number(value)) ? Number(value) : fallback,
  trip: {runId: "RUN-A", carId: "CAR001", samples, index: 0, summary: {sample_period_s: .1},
    untilId: 999, eventsUntilId: 500, window: {start_ordinal: 10, total_count: 900}, playing: true},
  tripLoading: false,
  stopTrip() {calls.stop++; if (context.trip) context.trip.playing = false;},
  renderTripPoint(index) {context.trip.index = index; rendered.push(context.trip.samples[index]);},
  getJSON() {calls.requests++; throw new Error("local navigation must not request new archive rows");}};
  vm.createContext(context);
  for (const name of ["renderTripDecisions", "renderTripDecisionNavigation", "jumpTripDecision", "stepTripDecision"])
    vm.runInContext(extract(name), context, {filename: `production:${name}`});
  return {context, calls, rendered, get: field => context.$(`trip-decisions-${field}`)};
}
let checks = 0;
function test(name, callback) {callback(); checks++; process.stdout.write(`PASS ${name}\n`);}
test("navigator has unique native button targets and an explicit loaded-window scope", () => {
  for (const suffix of ["panel", "status", "sections", "minimum", "minimum-note", "strongest",
    "strongest-note", "counts", "scope", "previous", "next", "position"])
    assert.equal(allIds.filter(id => id === `trip-decisions-${suffix}`).length, 1, suffix);
  for (const suffix of ["minimum", "strongest", "previous", "next"])
    assert.match(markup, new RegExp(`<button\\b[^>]*id="trip-decisions-${suffix}"[^>]*type="button"|<button\\b[^>]*type="button"[^>]*id="trip-decisions-${suffix}"`));
  assert.match(markup, /id="trip-perception-title"[^>]*tabindex="-1"/);
});
test("real loaded decisions populate signed minima and actual deceleration without changing source rows", () => {
  const h = harness(), before = JSON.stringify(h.context.trip.samples);
  h.context.renderTripDecisions();
  assert.equal(h.calls.summary, 1);
  assert.equal(h.context.trip.decisionWindow.scope, "loaded_received_window");
  assert.match(h.get("minimum").textContent, /1,04 m/);
  assert.match(h.get("strongest").textContent, /-1,00 m\/s²/);
  assert.match(h.get("scope").textContent, /ablak/i);
  assert.equal(JSON.stringify(h.context.trip.samples), before);
});
test("exact receipt jumps pause playback and focus the historical perception heading without fetching", () => {
  const h = harness(); h.context.renderTripDecisions();
  const identity = h.context.trip, point = identity.samples[2];
  h.context.jumpTripDecision(2, identity);
  assert.equal(h.calls.stop, 1); assert.equal(identity.playing, false);
  assert.equal(identity.index, 2); assert.equal(h.rendered[0], point);
  assert.equal(h.context.$("trip-perception-title").scrolls.length, 1);
  assert.equal(h.context.$("trip-perception-title").focuses.length, 1);
  assert.equal(h.context.$("trip-perception-title").focuses[0].preventScroll, true);
  assert.equal(identity.untilId, 999); assert.equal(identity.eventsUntilId, 500);
  assert.equal(h.calls.requests, 0);
});
test("loading, stale identity, absent analysis and malformed indices cannot move or pause playback", () => {
  for (const mode of ["loading", "stale", "uncached", "missing", "string", "fraction", "negative", "past", "nan"]) {
    const h = harness(); h.context.renderTripDecisions(); const identity = h.context.trip;
    let index = 1, requested = identity;
    if (mode === "loading") h.context.tripLoading = true;
    if (mode === "stale") requested = {...identity};
    if (mode === "uncached") delete identity.decisionWindow;
    if (mode === "missing") h.context.trip = null;
    if (mode === "string") index = "1";
    if (mode === "fraction") index = 1.2;
    if (mode === "negative") index = -1;
    if (mode === "past") index = identity.samples.length;
    if (mode === "nan") index = NaN;
    h.context.jumpTripDecision(index, requested);
    assert.equal(h.calls.stop, 0, mode); assert.equal(h.rendered.length, 0, mode);
    assert.equal(identity.index, 0, mode); assert.equal(identity.playing, true, mode);
  }
});
test("previous and next select section starts in receipt order without wrapping or growing the window", () => {
  const h = harness(), c = h.context; c.renderTripDecisions();
  c.trip.decisionWindow.braking_sections = [
    {start_index: 0, end_index: 0}, {start_index: 2, end_index: 2}];
  c.trip.index = 1; const identity = c.trip, samples = identity.samples;
  c.stepTripDecision(1); assert.equal(c.trip.index, 2);
  c.stepTripDecision(1); assert.equal(c.trip.index, 2); assert.equal(h.calls.stop, 1);
  c.stepTripDecision(-1); assert.equal(c.trip.index, 0);
  c.stepTripDecision(-1); assert.equal(c.trip.index, 0); assert.equal(h.calls.stop, 2);
  assert.equal(c.trip, identity); assert.equal(c.trip.samples, samples);
  assert.equal(h.calls.requests, 0); assert.equal(h.calls.summary, 1);
});
test("previous braking section skips the current section when selected inside it", () => {
  const h = harness(Array.from({length: 7}, (_, index) => frame(index))), c = h.context;
  c.renderTripDecisions();
  c.trip.decisionWindow.braking_sections = [
    {start_index: 0, end_index: 2}, {start_index: 4, end_index: 6}];
  c.trip.index = 1; c.renderTripDecisionNavigation();
  assert.equal(h.get("previous").disabled, true);
  c.stepTripDecision(-1);
  assert.equal(c.trip.index, 1); assert.equal(h.calls.stop, 0);
  c.trip.index = 5; c.renderTripDecisionNavigation();
  assert.equal(h.get("previous").disabled, false);
  c.stepTripDecision(-1);
  assert.equal(c.trip.index, 0); assert.equal(h.calls.stop, 1);
  assert.equal(h.rendered.at(-1), c.trip.samples[0]);
  assert.equal(h.calls.requests, 0); assert.equal(h.calls.summary, 1);
});
test("section navigation rejects non-directions and blocks cached actions during loading", () => {
  for (const direction of [0, 2, -2, "1", true, null, undefined]) {
    const h = harness(); h.context.renderTripDecisions();
    h.context.trip.decisionWindow.braking_sections = [{start_index: 1, end_index: 2}];
    h.context.stepTripDecision(direction);
    assert.equal(h.rendered.length, 0); assert.equal(h.calls.stop, 0);
  }
  const h = harness(); h.context.renderTripDecisions(); h.context.tripLoading = true;
  h.context.trip.decisionWindow.braking_sections = [{start_index: 1, end_index: 2}];
  h.context.stepTripDecision(1); assert.equal(h.rendered.length, 0);
});
test("native navigation state updates from cached summary and respects section boundaries and loading", () => {
  const h = harness(), c = h.context; c.renderTripDecisions();
  c.trip.decisionWindow.braking_sections = [{start_index: 0, end_index: 0}, {start_index: 2, end_index: 2}];
  c.trip.index = 0; c.renderTripDecisionNavigation();
  assert.equal(h.get("previous").disabled, true); assert.equal(h.get("next").disabled, false);
  c.trip.index = 2; c.renderTripDecisionNavigation();
  assert.equal(h.get("previous").disabled, false); assert.equal(h.get("next").disabled, true);
  c.tripLoading = true; c.renderTripDecisionNavigation();
  for (const suffix of ["minimum", "strongest", "previous", "next"]) assert.equal(h.get(suffix).disabled, true, suffix);
  c.tripLoading = false; c.renderTripDecisionNavigation();
  assert.equal(h.get("minimum").disabled, false); assert.equal(h.get("strongest").disabled, false);
  assert.equal(h.calls.summary, 1);
});
test("empty and legacy windows expose no invented minimum or braking jump", () => {
  for (const samples of [[], [{id: 1, sim_time: 0, seq: 1}], [frame(0, {control: null})]]) {
    const h = harness(samples); h.context.renderTripDecisions();
    assert.equal(h.context.trip.decisionWindow.minimum_moving_reserve, null);
    assert.equal(h.context.trip.decisionWindow.strongest_braking, null);
    for (const suffix of ["minimum", "strongest", "previous", "next"]) assert.equal(h.get(suffix).disabled, true, suffix);
    h.context.stepTripDecision(1); assert.equal(h.rendered.length, 0);
  }
});
test("a missing stopping target leaves the reserve action disabled while preserving reported braking", () => {
  const row = frame(0); row.control.stop_limit_remaining_m = null;
  const h = harness([row]); h.context.renderTripDecisions();
  assert.equal(h.context.trip.decisionWindow.minimum_moving_reserve, null);
  assert.equal(h.get("minimum").disabled, true);
  assert.equal(h.get("strongest").disabled, false);
  assert.equal(h.context.trip.decisionWindow.strongest_braking.index, 0);
  assert.match(h.get("minimum-note").textContent, /céllal/);
});
test("control-only archive rows can be inspected without claiming fresh sensor observations", () => {
  const row = frame(0, {sensors: undefined}), h = harness([row]);
  assert.equal(global.YardBrakingAnalysis.analyzeFrame({control: row.control,
    telemetry_sim_time: row.sim_time}).freshness, "unknown");
  h.context.renderTripDecisions();
  assert.equal(h.context.trip.decisionWindow.freshness_unknown_decisions, 1);
  assert.match(h.get("counts").textContent, /1[^·.]*ismeretlen[^·.]*frissesség/i);
  assert.equal(h.get("strongest").disabled, false);
  h.context.jumpTripDecision(0);
  assert.equal(h.rendered.at(-1), row);
  assert.ok(!Object.hasOwn(h.rendered.at(-1), "fresh"));
  assert.ok(!/friss érzékelő|friss észlelés/i.test(h.get("scope").textContent));
});
test("reduced-motion preference prevents animated scroll while retaining keyboard focus", () => {
  const h = harness(); h.context.window.matchMedia = () => ({matches: true});
  h.context.renderTripDecisions(); h.context.jumpTripDecision(1);
  assert.equal(h.context.$("trip-perception-title").scrolls[0].behavior, "instant");
  assert.equal(h.context.$("trip-perception-title").focuses[0].preventScroll, true);
});
test("selection invalidation clears previous metrics and cannot leave enabled old decision controls", () => {
  const h = harness(); h.context.renderTripDecisions(); h.context.trip = null;
  h.context.renderTripDecisions();
  assert.equal(h.calls.summary, 1);
  for (const suffix of ["minimum", "strongest", "previous", "next"]) assert.equal(h.get(suffix).disabled, true, suffix);
  assert.ok(!h.get("minimum").textContent.includes("1,04"));
  assert.ok(!h.get("strongest").textContent.includes("-1,00"));
});
test("production lifecycle and button handlers use cached received-window actions", () => {
  assert.match(extract("loadTripWindow"), /renderTripDecisions\(\)/);
  assert.match(extract("invalidateTripSelection"), /renderTripDecisions\(\)/);
  assert.match(extract("renderTripPoint"), /renderTripDecisionNavigation\(\)/);
  assert.match(extract("renderTripNavigation"), /renderTripDecisionNavigation\(\)/);
  const h = harness(), c = h.context; c.renderTripDecisions();
  const expressions = [
    ["minimum", "jumpTripDecision"], ["strongest", "jumpTripDecision"],
    ["previous", "stepTripDecision"], ["next", "stepTripDecision"]];
  for (const [suffix, action] of expressions) {
    const line = source.split(/\r?\n/).find(value => value.includes(`$("trip-decisions-${suffix}").addEventListener`));
    assert.ok(line, `native ${suffix} handler exists`);
    assert.ok(line.includes(action), `${suffix} uses ${action}`);
    vm.runInContext(line, c, {filename: `production:handler-${suffix}`});
  }
  h.get("minimum").handlers.click();
  assert.equal(h.rendered.at(-1), c.trip.samples[c.trip.decisionWindow.minimum_moving_reserve.index]);
  h.get("strongest").handlers.click();
  assert.equal(h.rendered.at(-1), c.trip.samples[c.trip.decisionWindow.strongest_braking.index]);
  assert.equal(h.calls.requests, 0);
});
process.stdout.write(`PASS ${checks} received-decision navigator integration checks\n`);
