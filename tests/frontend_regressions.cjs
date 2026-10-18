/* Targeted regressions against actual private app functions. No browser, live
   fetches, production debug hooks or secondary implementation are required. */
"use strict";
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const source = fs.readFileSync(require("node:path").join(__dirname, "../web/app.js"), "utf8");
class Element {
  constructor(tag = "div") {
    this.tag = tag; this.attributes = {}; this.children = []; this.textContent = ""; this.listeners = {}; this.dataset = {}; this.hidden = false;
    this.style = {setProperty() {}};
    const classes = new Set(); this.classList = {toggle(name, active) {active ? classes.add(name) : classes.delete(name);}, add(name) {classes.add(name);}, contains(name) {return classes.has(name);}};
  }
  setAttribute(key, value) {this.attributes[key] = String(value);}
  getAttribute(key) {return this.attributes[key] ?? null;}
  append(...children) {this.children.push(...children);}
  replaceChildren(...children) {this.children = children;}
  addEventListener(kind, callback) {(this.listeners[kind] ||= []).push(callback);}
  dispatch(kind, event) {for (const callback of this.listeners[kind] || []) callback(event);}
}
const document = {createElementNS: (_ns, tag) => new Element(tag), createElement: tag => new Element(tag), createTextNode: text => Object.assign(new Element("#text"), {textContent: String(text)})};
global.document = document; require("../web/perception.js");
function extract(name) {
  const start = source.indexOf(`  function ${name}(`);
  assert.ok(start >= 0, `production function ${name} exists`);
  const tail = source.slice(start), next = tail.search(/\n  (?:function |async function |const |let )/);
  return next >= 0 ? tail.slice(0, next) : tail;
}
const elements = new Map();
const context = {
  document, window: {YardPerception: global.YardPerception}, NS: "http://www.w3.org/2000/svg",
  $: id => {if (!elements.has(id)) elements.set(id, new Element()); return elements.get(id);},
  number: (value, fallback = 0) => Number.isFinite(Number(value)) ? Number(value) : fallback,
  hasValue: value => value !== null && value !== undefined && Number.isFinite(Number(value)),
  fmt: (value, digits = 1) => Number(value).toLocaleString("hu-HU", {minimumFractionDigits: digits, maximumFractionDigits: digits}),
  reading: (value, unit = "s", digits = 1) => value !== null && value !== undefined && Number.isFinite(Number(value)) ? `${Number(value).toLocaleString("hu-HU", {minimumFractionDigits: digits, maximumFractionDigits: digits})} ${unit}` : "—",
  html: (tag, attrs = {}, text = null) => {const n = new Element(tag); for (const [key, value] of Object.entries(attrs)) n.setAttribute(key, value); if (text !== null) n.textContent = String(text); return n;},
  empty: element => element.replaceChildren(), parkingNodes: new Map(), crossingNodes: new Map(),
  current: {cars: []}, selectedCar: "ARCHIVED", selectedSlot: null, previewMap: null, movedPointer: true, followCar: false,
  view: {x: 0, y: 0, w: 140, h: 90}, fitView: {w: 140, h: 90}, fullMap: {width: 140},
  trip: null, parkingRenderCount: 0, renderParking: () => context.parkingRenderCount++, fillPlacementItems() {}, layoutParkingLabels() {}, renderSensorOverlay() {}, selectedProximityChannel() {return null;}, selectProximityChannel() {}
};
vm.createContext(context);
for (const name of ["node", "pointSegmentDistance", "renderMapGeometry", "yardMapPixel", "updateView", "nearestTripSlot", "tripLocation", "renderProximityTable", "perceptionSourceNote", "drawMapSensorOverlay"]) vm.runInContext(extract(name), context);
let checks = 0;
function test(name, callback) {callback(); checks++; process.stdout.write(`PASS ${name}\n`);}
function walk(element) {return [element, ...element.children.flatMap(walk)];}
test("parked Győr P03 resolves to its nearest slot instead of earlier nearby P01; assignment does not override actual position", () => {
  const map = {slots: [{id: "P01", x: 346.5, y: 17}, {id: "P03", x: 350, y: 18}], crossings: [], roads: []};
  context.trip = {map};
  assert.equal(context.tripLocation({x: 350, y: 18, state: "parked", slot_id: "P03"}), "Parkoló: P03");
  assert.equal(context.nearestTripSlot({x: 346.5, y: 17, slot_id: "P03"}, map).id, "P01");
  assert.equal(context.nearestTripSlot({x: 400, y: 30}, map), null);
  assert.equal(context.nearestTripSlot({x: 348.25, y: 17.5, slot_id: "P03"}, map).id, "P03");
});
test("drag suppresses only pointer click; Enter, Space and assistive keyboard click still select the slot", () => {
  const layers = Object.fromEntries(["buildings", "roads", "zones", "crossings", "parking", "annotations"].map(name => [name, new Element("g")]));
  context.renderMapGeometry({bounds: {x: 0, y: 0, w: 140, h: 90}, width: 140, slots: [{id: "P03", x: 50, y: 18}]}, layers, {interactive: true});
  const slot = context.parkingNodes.get("P03");
  slot.dispatch("click", {detail: 1}); assert.equal(context.selectedSlot, null);
  let prevented = false; slot.dispatch("keydown", {key: "Enter", preventDefault() {prevented = true;}}); assert.ok(prevented); assert.equal(context.selectedSlot, "P03");
  context.selectedSlot = null; slot.dispatch("keydown", {key: " ", preventDefault() {}}); assert.equal(context.selectedSlot, "P03");
  context.selectedSlot = null; slot.dispatch("click", {detail: 0}); assert.equal(context.selectedSlot, "P03");
});
test("main-map follow button is disabled for archived cars and preview, enabled only for the active selected car", () => {
  context.updateView(); assert.equal(context.$("map-focus-car").disabled, true);
  context.current = {cars: [{id: "ARCHIVED"}]}; context.updateView(); assert.equal(context.$("map-focus-car").disabled, false);
  context.previewMap = {}; context.updateView(); assert.equal(context.$("map-focus-car").disabled, true); context.previewMap = null;
});
const sample = {
  schema_version: 1, source: "virtual_onboard_bundle", sampled: true, sample_sim_time_s: 12, range_m: 36,
  reference_pose: {x_m: 10, y_m: 20, heading_deg: 90}, pose: {x_m: 10.1, y_m: 19.9, heading_deg: 91}, position_uncertainty_m: .1,
  environment: {vehicles: [], crossings: []}, guidance: {available: false},
  proximity: {schema_version: 1, source: "virtual_bumper_ray_fan", sampled: true, sample_sim_time_s: 12, range_m: 5, thresholds_m: {close: 1, warning: 2}, rays_per_channel: 11, beam_half_angle_deg: 30, reference_pose: {x_m: 10, y_m: 20, heading_deg: 90}, channels: [
    {id: "front_center", label: "Elöl közép", bearing_deg: 0, status: "close", distance_m: .4, target: {id: "BUILDING:WALL", label: "Csarnok fala"}, origin_world_m: {x_m: 10, y_m: 22.25}, hit_world_m: {x_m: 10, y_m: 22.65}, beam_endpoints_world_m: [{x_m: 12.5, y_m: 26.58}, {x_m: 7.5, y_m: 26.58}]},
    {id: "rear_center", label: "Hátul közép", bearing_deg: 180, status: "no_return", distance_m: null, origin_world_m: {x_m: 10, y_m: 17.75}, hit_world_m: null, beam_endpoints_world_m: [{x_m: 7.5, y_m: 13.42}, {x_m: 12.5, y_m: 13.42}]}
  ]}
};
test("shared map overlay uses recorded mount, fan and first-hit coordinates; stale keeps geometry without fresh colours", () => {
  const before = JSON.stringify(sample), fresh = new Element("g"), stale = new Element("g");
  context.drawMapSensorOverlay(fresh, {car: {id: "CAR001"}, sample, fresh: true}, .2);
  context.drawMapSensorOverlay(stale, {car: {id: "CAR001"}, sample, fresh: false}, .2);
  const hit = walk(fresh).find(n => n.attributes.class === "sensor-proximity-hit");
  assert.equal(hit.attributes.cx, "10"); assert.equal(hit.attributes.cy, "22.65"); assert.equal(hit.attributes.fill, "#ff5b61");
  assert.equal(walk(fresh).filter(n => n.attributes.class === "sensor-proximity-mount").length, 2);
  assert.equal(walk(fresh).filter(n => n.attributes.class === "sensor-proximity-hit").length, 1);
  const staleHit = walk(stale).find(n => n.attributes.class === "sensor-proximity-hit"); assert.equal(staleHit.attributes.cx, hit.attributes.cx); assert.equal(staleHit.attributes.cy, hit.attributes.cy); assert.equal(staleHit.attributes.fill, "#b69869");
  assert.ok(walk(fresh).some(n => n.attributes.d === "M10 22.25L12.5 26.58L7.5 26.58Z"));
  assert.equal(JSON.stringify(sample), before);
});
test("live and archived channel tables use same distances and targets; stale/no-return cannot advertise fresh clear space", () => {
  context.renderProximityTable("live", sample, true); context.renderProximityTable("trip", sample, false);
  const liveRows = context.$("live-proximity-rows").children, tripRows = context.$("trip-proximity-rows").children;
  assert.equal(liveRows.length, 2); assert.equal(liveRows[0].children[1].textContent, "0,40"); assert.equal(liveRows[0].children[3].textContent, "Csarnok fala");
  assert.equal(liveRows[1].children[1].textContent, "—"); assert.equal(liveRows[1].children[2].textContent, "Nincs visszatérés");
  assert.equal(tripRows[0].children[1].textContent, "0,40"); assert.equal(tripRows[0].children[2].textContent, "Elavult találat");
  context.renderProximityTable("trip", null, false); assert.equal(context.$("trip-proximity-table").hidden, true); assert.equal(context.$("trip-proximity-rows").children.length, 0);
});
test("model explanations follow custom archived range, thresholds and beam rather than fixed defaults", () => {
  const note = context.perceptionSourceNote(sample);
  for (const value of ["5,00 m", "1,00 m", "2,00 m", "36,0 m", "11 tesztelt sugár", "±30,0 °"]) assert.ok(note.includes(value), value);
  assert.ok(!note.includes("18 m")); assert.ok(!note.includes("0,55 m"));
  const legacy = {...sample}; delete legacy.proximity; assert.ok(context.perceptionSourceNote(legacy).includes("régi mért irány"));
});
test("telemetry redraw preserves channel button identity and updates readings without losing keyboard focus", () => {
  context.renderProximityTable("live", sample, true);
  const firstRow = context.$("live-proximity-rows").children[0], firstButton = firstRow.children[0].children[0];
  const changed = structuredClone(sample); changed.proximity.channels[0].distance_m = .2;
  changed.proximity.channels[0].label = "Új címke";
  context.renderProximityTable("live", changed, false);
  assert.equal(context.$("live-proximity-rows").children[0], firstRow);
  assert.equal(firstRow.children[0].children[0], firstButton);
  assert.equal(firstButton.children[1].textContent, "Új címke");
  assert.equal(firstRow.children[1].textContent, "0,20");
  assert.equal(firstRow.children[2].textContent, "Elavult találat");
});
test("sensor range caption stays inside the live map instead of following a car off the right edge", () => {
  const layer=new Element("g"), view={x:0,y:0,w:146,h:82};
  context.drawMapSensorOverlay(layer,{car:{id:"CAR001"},sample,fresh:true,view},.2);
  const label=walk(layer).find(n=>n.attributes.class==="sensor-map-label");
  assert.equal(label.attributes.x,"144");assert.equal(label.attributes.y,"3.2");assert.equal(label.attributes["text-anchor"],"end");
});
test("archived map caption follows a panned viewport without changing sensor coordinates", () => {
  const layer=new Element("g"),view={x:-50,y:100,w:80,h:45};
  const before=JSON.stringify(sample);
  context.drawMapSensorOverlay(layer,{car:{id:"CAR001"},sample,fresh:false,view},.25);
  const label=walk(layer).find(n=>n.attributes.class==="sensor-map-label");
  assert.equal(label.attributes.x,"27.5");assert.equal(label.attributes.y,"104");assert.equal(JSON.stringify(sample),before);
});
process.stdout.write(`${checks} frontend regressions passed.\n`);
