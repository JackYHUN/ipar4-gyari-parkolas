/* Navigation and channel-selection regressions against actual private app
   functions. Deferred requests exercise races; no browser, production debug
   hooks, network requests or reimplementation of the loader are needed. */
"use strict";
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const source = fs.readFileSync(path.join(__dirname, "../web/app.js"), "utf8");
class Element {
  constructor() {
    this.textContent = ""; this.hidden = false; this.disabled = false; this.value = "";
    this.checked = false; this.attributes = {}; this.children = []; this.scrolls = [];
    const classes = new Set();
    this.classList = {toggle(name, active) {active ? classes.add(name) : classes.delete(name);}, contains(name) {return classes.has(name);}};
  }
  setAttribute(name, value) {this.attributes[name] = String(value);}
  append(child) {this.children.push(child);}
  replaceChildren(...children) {this.children = children;}
  scrollIntoView(options) {this.scrolls.push(options);}
}
global.document = {createElementNS: () => new Element()};
require("../web/perception.js");
require("../web/perception-analysis.js");
function extract(name) {
  const expression = new RegExp(`^  (?:async )?function ${name}\\(`, "m"), match = expression.exec(source);
  assert.ok(match, `production function ${name} exists`);
  const tail = source.slice(match.index), next = tail.search(/\n  (?:function |async function |const |let )/);
  return next >= 0 ? tail.slice(0, next) : tail;
}
function deferred() {
  let resolve, reject;
  const promise = new Promise((yes, no) => {resolve = yes; reject = no;});
  return {promise, resolve, reject};
}
function harness() {
  const elements = new Map(), requests = [], renders = [];
  const context = {
    URLSearchParams, Map, Set,
    window: {YardPerception: global.YardPerception, YardPerceptionAnalysis: global.YardPerceptionAnalysis},
    $: id => {if (!elements.has(id)) elements.set(id, new Element()); return elements.get(id);},
    number: (value, fallback = 0) => Number.isFinite(Number(value)) ? Number(value) : fallback,
    hasValue: value => value !== null && value !== undefined && Number.isFinite(Number(value)),
    reading: (value, unit = "s", digits = 1) => value !== null && value !== undefined && Number.isFinite(Number(value)) ? `${Number(value).toLocaleString("hu-HU", {minimumFractionDigits: digits, maximumFractionDigits: digits})} ${unit}` : "—",
    fmt: (value, digits = 1) => Number(value).toLocaleString("hu-HU", {minimumFractionDigits: digits, maximumFractionDigits: digits}),
    trip: null, tripVersion: 0, tripLoading: false, catalogueVersion: 0, inspectSelectedTrip() {},
    current: {run_id: "RUN-A"}, observedRun: "RUN-A",
    perceptionFrames: new Map(), proximitySelections: new Map(),
    fitView: {w: 140, h: 90}, view: {x: 0, y: 0, w: 140, h: 90}, followCar: true,
    getJSON: url => {const request = {url, ...deferred()}; requests.push(request); return request.promise;},
    scenarioToMap: config => ({fromConfig: config}),
    rebuildTripMap: () => renders.push("map"), renderTripDecisions(){},renderTripDecisionNavigation(){},renderTripSummary: () => renders.push("summary"),
    renderPerceptionCoverage: () => renders.push("coverage"), renderTripMission: () => {}, renderTripEvents: () => renders.push("events"),
    renderTripPoint: index => {context.trip.index = index; renders.push(`point:${index}`);},
    renderPerception: (prefix, data) => renders.push({kind: "perception", prefix, data}),
    renderSensorOverlay: () => renders.push("live-overlay"),
    renderTripSensorOverlay: (point, held) => renders.push({kind: "trip-overlay", point, held}),
    updateView: () => renders.push("live-view"), updateTripView: () => renders.push("trip-view"),
  };
  vm.createContext(context);
  for (const name of ["tripError", "stopTrip", "tripQuery", "tripRunId", "renderTripNavigation", "invalidateTripSelection", "loadTrip", "loadTripWindow", "selectedProximityChannel", "selectProximityChannel", "focusProximityChannel", "renderTripQuality"]) {
    vm.runInContext(extract(name), context, {filename: `app.js:${name}`});
  }
  return {context, elements, requests, renders};
}
function page(run = "RUN-A", car = "CAR001", samples = [{id: 1, sim_time: 1}], options = {}) {
  const first = samples[0]?.id ?? null, last = samples.at(-1)?.id ?? null;
  return {
    run_id: run, car_id: car, telemetry: samples, events: [{id: 11, sim_time: 1}], summary: {},
    until_id: 500, map: {id: "saved-map"}, target_id: first, seek: null,
    window: {start_ordinal: samples.length ? 1 : 0, end_ordinal: samples.length, total_count: samples.length, page_limit: 2000, first_id: first, last_id: last, previous_before_id: null, next_after_id: null, order: "receipt_id_ascending"},
    ...options,
  };
}
function requestQuery(request) {return new URL(request.url, "http://unit.invalid").searchParams;}
async function respond(h, options, response) {
  const task = h.context.loadTripWindow(options), request = h.requests.shift();
  assert.ok(request, "a bounded history request was issued"); request.resolve(response); await task;
  return request;
}
function sensorFrame(time = 0, fresh = true, distance = .4) {
  return {
    schema_version: 1, source: "virtual_onboard_bundle", sampled: true,
    sample_sim_time_s: time, fresh, fault_active: !fresh,
    proximity: {schema_version: 1, source: "virtual_bumper_ray_fan", sampled: true, sample_sim_time_s: time,
      range_m: 3, thresholds_m: {close: .55, warning: 1.2}, channels: [
        {id: "front_center", label: "Elöl közép", bearing_deg: 0, status: distance <= .55 ? "close" : "warning", distance_m: distance,
          origin_world_m: {x_m: 12.25, y_m: 20}, hit_world_m: {x_m: 12.25 + distance, y_m: 20}, target: {id: "WALL", label: "Fal"}},
        {id: "rear_center", label: "Hátul közép", bearing_deg: 180, status: "no_return", distance_m: null,
          origin_world_m: {x_m: 7.75, y_m: 20}, hit_world_m: null, target: null},
      ]},
  };
}
let checks = 0;
async function test(name, callback) {await callback(); checks++; process.stdout.write(`PASS ${name}\n`);}
async function main() {
  await test("out-of-order requests cannot replace a newer run/car window or release its loading state", async () => {
    const h = harness(), first = h.context.loadTripWindow({run: "RUN-A", car: "CAR001"}), second = h.context.loadTripWindow({run: "RUN-B", car: "CAR002"});
    assert.equal(h.requests.length, 2); assert.equal(h.context.tripLoading, true);
    h.requests[0].resolve(page("RUN-A", "CAR001")); await first;
    assert.equal(h.context.trip, null); assert.equal(h.context.tripLoading, true); assert.equal(h.context.$("trip-load").disabled, true);
    h.requests[1].resolve(page("RUN-B", "CAR002", [{id: 9, sim_time: 20}])); await second;
    assert.equal(h.context.trip.runId, "RUN-B"); assert.equal(h.context.trip.carId, "CAR002"); assert.equal(h.context.tripLoading, false);
    assert.equal(h.renders.filter(value => value === "map").length, 1);
  });
  await test("late old success or error cannot overwrite a completed newer selection", async () => {
    for (const rejectOld of [false, true]) {
      const h = harness(), oldTask = h.context.loadTripWindow({run: "RUN-A", car: "CAR001"}), newTask = h.context.loadTripWindow({run: "RUN-B", car: "CAR002"});
      h.requests[1].resolve(page("RUN-B", "CAR002")); await newTask;
      const identity = h.context.trip;
      rejectOld ? h.requests[0].reject(new Error("old disconnected request")) : h.requests[0].resolve(page("RUN-A", "CAR001"));
      await oldTask;
      assert.equal(h.context.trip, identity); assert.equal(h.context.$("trip-error").hidden, true); assert.equal(h.context.tripLoading, false);
    }
  });
  await test("frozen until_id survives paging and time seek; explicit refresh releases the ceiling", async () => {
    const h = harness(); await respond(h, {run: "RUN-A", car: "CAR001"}, page());
    let request = await respond(h, {direction: "after", cursor: 1}, page("RUN-A", "CAR001", [{id: 2, sim_time: 2}]));
    assert.equal(requestQuery(request).get("until_id"), "500"); assert.equal(requestQuery(request).get("cursor"), "1");
    request = await respond(h, {direction: "time", simTime: 1.5}, page("RUN-A", "CAR001", [{id: 1, sim_time: 1}], {seek: {requested_sim_time: 1.5, resolved_sim_time: 1, resolution: "predecessor", predecessor_found: true}}));
    assert.equal(requestQuery(request).get("until_id"), "500"); assert.equal(requestQuery(request).get("sim_time"), "1.5");
    const refresh = h.context.loadTrip(true), refreshRequest = h.requests.shift();
    assert.ok(refreshRequest); assert.equal(requestQuery(refreshRequest).has("until_id"), false);
    refreshRequest.resolve(page("RUN-A", "CAR001", [{id: 3, sim_time: 3}], {until_id: 800})); await refresh;
    assert.equal(h.context.trip.untilId, 800);
  });
  await test("zero ceiling stays frozen and unrelated identity never inherits another car's prefix", async () => {
    const h = harness(); await respond(h, {run: "RUN-A", car: "EMPTY"}, page("RUN-A", "EMPTY", [], {until_id: 0}));
    let request = await respond(h, {direction: "start"}, page("RUN-A", "EMPTY", [], {until_id: 0}));
    assert.equal(requestQuery(request).get("until_id"), "0");
    request = await respond(h, {run: "RUN-B", car: "CAR002"}, page("RUN-B", "CAR002"));
    assert.equal(requestQuery(request).has("until_id"), false);
  });
  await test("receipt order and target receipt ID remain exact even when simulation clocks regress", async () => {
    const h = harness(), samples = [{id: 100, sim_time: 10}, {id: 102, sim_time: 8}, {id: 107, sim_time: 10}], before = JSON.stringify(samples);
    await respond(h, {run: "RUN-A", car: "CAR001"}, page("RUN-A", "CAR001", samples, {target_id: 102}));
    assert.deepEqual(h.context.trip.samples.map(row => row.id), [100, 102, 107]);
    assert.equal(h.context.trip.index, 1); assert.equal(h.context.trip.samples[1].sim_time, 8); assert.equal(JSON.stringify(samples), before);
    assert.equal(h.context.$("trip-slider").max, 2);
  });
  await test("invalid time seeks make no request and do not clear an existing loaded archive", async () => {
    const h = harness(); await respond(h, {run: "RUN-A", car: "CAR001"}, page());
    const identity = h.context.trip, version = h.context.tripVersion;
    for (const simTime of [undefined, null, "", "  ", NaN, Infinity, -1, "-0.1", true, {}]) {
      await h.context.loadTripWindow({direction: "time", simTime});
      assert.equal(h.requests.length, 0); assert.equal(h.context.trip, identity); assert.equal(h.context.tripVersion, version);
      assert.equal(h.context.$("trip-error").hidden, false);
    }
  });
  await test("empty and earliest-fallback responses have explicit notes with no invented position samples", async () => {
    const h = harness();
    await respond(h, {run: "RUN-A", car: "EMPTY", direction: "time", simTime: 0}, page("RUN-A", "EMPTY", [], {until_id: 0, target_id: null, seek: {resolution: "no_timed_sample", requested_sim_time: 0, resolved_sim_time: null, predecessor_found: false}}));
    assert.equal(h.context.trip.samples.length, 0); assert.equal(h.context.$("trip-play").disabled, true);
    assert.equal(h.context.$("trip-slider").max, 0); assert.ok(h.context.$("trip-seek-note").textContent.includes("Nincs időbélyeges"));
    await respond(h, {run: "RUN-A", car: "CAR001", direction: "time", simTime: 0}, page("RUN-A", "CAR001", [{id: 20, sim_time: 7}], {target_id: 20, seek: {resolution: "earliest_fallback", requested_sim_time: 0, resolved_sim_time: 7, predecessor_found: false}}));
    assert.equal(h.context.trip.samples[0].sim_time, 7); assert.ok(h.context.$("trip-seek-note").textContent.includes("előtt nincs tárolt minta"));
    assert.ok(h.context.$("trip-seek-note").textContent.includes("7,00 s"));
  });
  await test("mismatched identities and malformed or oversized windows cannot replace a valid loaded archive", async () => {
    const h = harness(); await respond(h, {run: "RUN-A", car: "CAR001"}, page()); const identity = h.context.trip;
    for (const response of [page("RUN-WRONG", "CAR001"), page("RUN-A", "WRONG-CAR"), page("RUN-A", "CAR001", Array.from({length: 2001}, (_, id) => ({id, sim_time: id}))), page("RUN-A", "CAR001", [], {window: null}), page("RUN-A", "CAR001", [], {telemetry: {bad: "not-an-array"}})]) {
      await respond(h, {direction: "start"}, response);
      assert.equal(h.context.trip, identity); assert.equal(h.context.tripLoading, false); assert.equal(h.context.$("trip-error").hidden, false);
    }
  });
  await test("selection invalidation discards pending archive requests and removes trip channel state", async () => {
    const h = harness(), task = h.context.loadTripWindow({run: "RUN-A", car: "CAR001"});
    h.context.perceptionFrames.set("trip", {run_id: "RUN-A", car_id: "CAR001"}); h.context.proximitySelections.set("trip", {runId: "RUN-A", carId: "CAR001", channelId: "front_center"});
    h.context.invalidateTripSelection(); h.requests[0].resolve(page()); await task;
    assert.equal(h.context.trip, null); assert.equal(h.context.tripLoading, false); assert.equal(h.context.perceptionFrames.has("trip"), false);
    assert.equal(h.context.proximitySelections.has("trip"), false); assert.equal(h.context.$("trip-content").hidden, true);
  });
  await test("channel selection is scoped to prefix, run, car and the actual saved channel set", () => {
    const h = harness(), sensors = sensorFrame(), original = JSON.stringify(sensors);
    const live = {run_id: "RUN-A", car_id: "CAR001", sensors}, archived = {run_id: "RUN-B", car_id: "CAR001", sensors};
    h.context.perceptionFrames.set("live", live); h.context.perceptionFrames.set("trip", archived);
    h.context.selectProximityChannel("live", "front_center");
    assert.equal(h.context.selectedProximityChannel("live").id, "front_center"); assert.equal(h.context.selectedProximityChannel("trip"), null);
    assert.equal(h.context.selectedProximityChannel("live", {...live, run_id: "RUN-B"}), null);
    assert.equal(h.context.selectedProximityChannel("live", {...live, car_id: "CAR002"}), null);
    assert.equal(h.context.selectedProximityChannel("live", {...live, sensors: {...sensors, proximity: {...sensors.proximity, channels: []}}}), null);
    h.context.selectProximityChannel("trip", "rear_center"); assert.equal(h.context.selectedProximityChannel("trip").id, "rear_center");
    assert.equal(h.context.selectedProximityChannel("live").id, "front_center");
    h.context.selectProximityChannel("live", "NOT-SAVED"); assert.equal(h.context.selectedProximityChannel("live"), null);
    assert.equal(h.context.proximitySelections.has("live"), false); assert.equal(JSON.stringify(sensors), original);
  });
  await test("sensor focus uses archived mount/hit geometry and keeps live/trip views isolated", () => {
    const h = harness(), sensors = sensorFrame(), original = JSON.stringify(sensors), data = {run_id: "RUN-A", car_id: "CAR001", sensors};
    h.context.trip = {runId: "RUN-A", carId: "CAR001", samples: [{sensors}], index: 0, bounds: {w: 140, h: 90}, view: {x: 0, y: 0, w: 140, h: 90}, follow: true};
    h.context.perceptionFrames.set("live", data); h.context.perceptionFrames.set("trip", data);
    h.context.selectProximityChannel("live", "front_center"); h.context.focusProximityChannel("live");
    assert.equal(h.context.followCar, false); assert.equal(h.context.$("show-sensors").checked, true);
    assert.ok(Math.abs(h.context.view.x + h.context.view.w / 2 - 12.45) < 1e-10); assert.equal(h.context.view.y + h.context.view.h / 2, 20);
    const liveView = {...h.context.view}; assert.equal(h.context.trip.view.x, 0);
    h.context.selectProximityChannel("trip", "rear_center"); h.context.focusProximityChannel("trip");
    assert.equal(h.context.trip.follow, false); assert.equal(h.context.trip.view.x + h.context.trip.view.w / 2, 7.75);
    assert.equal(h.context.trip.view.y + h.context.trip.view.h / 2, 20); assert.deepEqual({...h.context.view}, liveView);
    assert.equal(JSON.stringify(sensors), original);
  });
  await test("quality cards analyze the loaded received window and retain the exact minimum evidence row", () => {
    const h = harness(), rows = [{id: 1, car_id: "CAR001", sim_time: 0, sensors: sensorFrame(0, true, .4)}, {id: 2, car_id: "CAR001", sim_time: .1, sensors: sensorFrame(0, false, .4)}, {id: 3, car_id: "CAR001", sim_time: .2, sensors: sensorFrame(.2, true, 1)}], original = JSON.stringify(rows);
    h.context.trip = {samples: rows}; h.context.renderTripQuality();
    assert.equal(h.context.$("trip-quality-fresh").textContent, "2 / 3"); assert.equal(h.context.$("trip-quality-distinct").textContent, "2");
    assert.ok(h.context.$("trip-quality-distinct-note").textContent.includes("1 ismételt keret"));
    assert.equal(h.context.trip.quality.proximity.minimum_clearance.row_index, 0); assert.equal(h.context.trip.quality.proximity.minimum_clearance.telemetry_id, 1);
    assert.equal(h.context.$("trip-quality-minimum").disabled, false); assert.ok(h.context.$("trip-quality-minimum").textContent.includes("0,40 m"));
    assert.equal(JSON.stringify(rows), original);
    h.context.trip = {samples: []}; h.context.renderTripQuality(); assert.equal(h.context.$("trip-quality-minimum").disabled, true);
    assert.ok(h.context.$("trip-quality-minimum-note").textContent.includes("nem bizonyítja"));
  });
  process.stdout.write(`${checks} frontend navigation checks passed.\n`);
}
main().catch(error => {process.stderr.write(`${error.stack}\n`); process.exitCode = 1;});
