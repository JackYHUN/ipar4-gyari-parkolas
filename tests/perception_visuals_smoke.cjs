/* Render-contract checks, independent of the browser and of live world state. */
"use strict";
const assert = require("node:assert/strict");
class SvgNode {
  constructor(tag) { this.tag = tag; this.attributes = {}; this.children = []; this.textContent = ""; }
  setAttribute(key, value) { this.attributes[key] = String(value); }
  append(child) { this.children.push(child); }
  replaceChildren() { this.children = []; }
}
global.document = {createElementNS: (_namespace, tag) => new SvgNode(tag)};
require("../web/perception.js");
const renderer = global.YardPerception;
const fixture = {
  schema_version: 1, source: "virtual_onboard_bundle", sampled: true, sample_sim_time_s: 12.4,
  pose: {heading_deg: 0}, reference_pose: {x_m: 0, y_m: 0, heading_deg: 0}, range_m: 18,
  guidance: {available: true, heading_error_deg: -4},
  environment: {vehicles: [{id: "CAR002", x_m: 5, y_m: 1, distance_m: Math.sqrt(26), speed_m_s: 2, heading_deg: 12, length_m: 4.5, width_m: 1.9}], crossings: [{id: "C1", x_m: 9, y_m: 0, distance_m: 9, pedestrian_state: "crossing"}]},
  proximity: {schema_version: 1, source: "virtual_bumper_ray_fan", sampled: true, range_m: 3, beam_half_angle_deg: 20, thresholds_m: {close: .55, warning: 1.2},
    channels: [-35, 0, 35, -145, 180, 145, -90, 90].map((bearing, index) => ({id: `S${index}`, label: `S${index}`, bearing_deg: bearing, distance_m: index === 0 ? .4 : index === 1 ? 1 : index === 2 ? 2.5 : null, status: index === 0 ? "close" : index === 1 ? "warning" : index === 2 ? "clear" : "no_return", target: index < 3 ? {id: "CAR002", label: "CAR002"} : null}))}
};
let checks = 0;
function test(name, callback) { callback(); checks++; process.stdout.write(`PASS ${name}\n`); }
function walk(root) { return [root, ...root.children.flatMap(walk)]; }
function colors(root) { return walk(root).map(n => n.attributes.fill).filter(Boolean); }
test("body clearance thresholds include boundaries; far green requires an actual hit", () => {
  assert.equal(renderer.channelVisual({distance_m: .55, status: "close"}, true).status, "close");
  assert.equal(renderer.channelVisual({distance_m: 1.2, status: "warning"}, true).status, "warning");
  assert.equal(renderer.channelVisual({distance_m: 2.9, status: "clear"}, true).color, "#65d6a4");
  assert.equal(renderer.channelVisual({distance_m: null, status: "no_return"}, true).observed, false);
  assert.equal(renderer.channelVisual({distance_m: 0, status: "no_return"}, true).observed, false);
});
test("stale hits retain distances without fresh red or green indication", () => {
  const visual = renderer.channelVisual({distance_m: .3, status: "close"}, false);
  assert.equal(visual.status, "stale"); assert.equal(visual.distance, .3); assert.equal(visual.color, "#b69869");
});
test("relative observation uses sampled origin; bearing right is positive", () => {
  const right = renderer.relativeObservation(fixture, {x_m: 0, y_m: 2});
  assert.equal(right.right, 2); assert.equal(right.bearing, 90);
  const rotated = renderer.relativeObservation({...fixture, pose: {heading_deg: 90}}, {x_m: 0, y_m: 2});
  assert.ok(Math.abs(rotated.forward - 2) < 1e-10); assert.ok(Math.abs(rotated.right) < 1e-10);
  assert.equal(renderer.relativeObservation(fixture, {x_m: null, y_m: 2}), null);
});
test("parking renderer retains eight channels; no live model access or input mutation", () => {
  const before = JSON.stringify(fixture), svg = new SvgNode("svg");
  assert.equal(renderer.drawRadar(svg, fixture, true, "CAR001"), 3);
  assert.equal(walk(svg).filter(n => n.tag === "title" && n.textContent.includes("S")).length, 8);
  assert.equal(svg.attributes["data-sample-time"], "12.4"); assert.equal(JSON.stringify(fixture), before);
  assert.ok(colors(svg).includes("#ff5b61")); assert.ok(colors(svg).includes("#65d6a4"));
});
test("stale radar contains no fresh green glyphs, even in the legend", () => {
  const svg = new SvgNode("svg"); renderer.drawRadar(svg, fixture, false, "CAR001");
  assert.ok(!colors(svg).includes("#65d6a4")); assert.equal(svg.attributes["data-fresh"], "false");
});
test("legacy archive never fabricates proximity bands", () => {
  const legacy = {...fixture}; delete legacy.proximity;
  const svg = new SvgNode("svg"); assert.equal(renderer.drawRadar(svg, legacy, true, "CAR001"), 1);
  assert.ok(walk(svg).some(n => n.textContent.includes("nincs közelségcsatorna")));
  assert.ok(!colors(svg).includes("#65d6a4"));
});
test("forward renderer excludes rear targets and models only saved heading/dimensions", () => {
  const svg = new SvgNode("svg"), original = JSON.stringify(fixture);
  assert.equal(renderer.drawFrontView(svg, {...fixture, environment: {...fixture.environment, vehicles: [...fixture.environment.vehicles, {id: "BEHIND", x_m: -4, y_m: 0, distance_m: 4}]}}, true, "CAR001"), 2);
  assert.ok(!walk(svg).some(n => n.textContent.includes("BEHIND")));
  assert.ok(walk(svg).some(n => n.textContent.includes("mintavételkori modellirány")));
  assert.equal(JSON.stringify(fixture), original);
  const different = new SvgNode("svg"); renderer.drawFrontView(different, {...fixture, environment: {...fixture.environment, vehicles: [{...fixture.environment.vehicles[0], heading_deg: 90}]}}, true, "CAR001");
  assert.notDeepEqual(walk(svg).filter(n => n.tag === "path").map(n => n.attributes.d), walk(different).filter(n => n.tag === "path").map(n => n.attributes.d));
});
test("forward surface hits use archived hit coordinates; no-return and legacy do not invent hits", () => {
  const sampled = {...fixture, proximity: {...fixture.proximity, channels: [
    {...fixture.proximity.channels[0], hit_world_m: {x_m: 3, y_m: .1}, origin_world_m: {x_m: 2.25, y_m: -.8}},
    {...fixture.proximity.channels[1], hit_world_m: {x_m: -3, y_m: 0}},
    {...fixture.proximity.channels[2], hit_world_m: {x_m: 5, y_m: .5}, origin_world_m: {x_m: 2.25, y_m: .8}},
    {...fixture.proximity.channels[3], hit_world_m: {x_m: 3, y_m: .2}}
  ]}};
  assert.equal(renderer.proximityFrontHits(sampled).length, 2);
  const fresh = new SvgNode("svg"); renderer.drawFrontView(fresh, sampled, true, "CAR001");
  assert.ok(walk(fresh).some(n => n.tag === "title" && n.textContent.includes("Első felületi találat")));
  assert.ok(colors(fresh).includes("#ff5b61")); assert.ok(colors(fresh).includes("#65d6a4"));
  const stale = new SvgNode("svg"); renderer.drawFrontView(stale, sampled, false, "CAR001");
  assert.ok(!colors(stale).includes("#ff5b61")); assert.ok(!colors(stale).includes("#65d6a4"));
  assert.ok(walk(stale).some(n => n.tag === "title" && n.textContent.includes("elavult minta")));
  const legacy = {...sampled}; delete legacy.proximity;
  assert.deepEqual(renderer.proximityFrontHits(legacy), []);
});
test("geometric proximity and forward reconstruction use their saved sampling frame despite localization heading noise", () => {
  const scan = {...fixture, proximity: {...fixture.proximity, reference_pose: {x_m: 0, y_m: 0, heading_deg: 0}, channels: [{...fixture.proximity.channels[0], distance_m: .75, status: "warning", origin_world_m: {x_m: 2.25, y_m: 0}, hit_world_m: {x_m: 3, y_m: 0}}]}};
  const noisy = {...scan, pose: {heading_deg: 1}};
  assert.ok(renderer.relativeObservation(noisy, {x_m: 3, y_m: 0}).right < 0);
  assert.equal(renderer.proximityFrontHits(noisy)[0].relative.right, 0);
  assert.equal(renderer.proximityFrontHits(noisy)[0].relative.forward, 3);
  const cleanSvg = new SvgNode("svg"), noisySvg = new SvgNode("svg");
  renderer.drawFrontView(cleanSvg, scan, true, "CAR001"); renderer.drawFrontView(noisySvg, noisy, true, "CAR001");
  assert.deepEqual(walk(cleanSvg).map(n => n.attributes), walk(noisySvg).map(n => n.attributes));
  const legacy = {...noisy}; delete legacy.proximity;
  assert.equal(renderer.frontReference(legacy), null); assert.equal(renderer.frontHeading(legacy), 1);
});
test("rotated saved proximity frames keep clockwise right-positive geometry without mutating recorded coordinates", () => {
  const scan = {...fixture, pose: {heading_deg: 91}, proximity: {...fixture.proximity, reference_pose: {x_m: 10, y_m: 20, heading_deg: 90}, channels: [{...fixture.proximity.channels[0], distance_m: .4, status: "close", origin_world_m: {x_m: 10, y_m: 22.25}, hit_world_m: {x_m: 9.5, y_m: 23}}]}};
  const original = JSON.stringify(scan), hit = renderer.proximityFrontHits(scan)[0];
  assert.ok(Math.abs(hit.relative.forward - 3) < 1e-10); assert.ok(Math.abs(hit.relative.right - .5) < 1e-10);
  assert.equal(JSON.stringify(scan), original);
});
function selectionSample() {
  const sample = JSON.parse(JSON.stringify(fixture));
  sample.proximity.reference_pose = {...sample.reference_pose};
  Object.assign(sample.proximity.channels[0], {origin_world_m: {x_m: 2.25, y_m: -.8}, hit_world_m: {x_m: 3, y_m: -.5}});
  Object.assign(sample.proximity.channels[4], {distance_m: .8, status: "warning", origin_world_m: {x_m: -2.25, y_m: 0}, hit_world_m: {x_m: -3.05, y_m: 0}, target: {id: "REAR-CAR", label: "REAR-CAR"}});
  return sample;
}
test("selected saved channel has a neutral outline and target title without changing colors, metadata or samples", () => {
  const sample = selectionSample(), original = JSON.stringify(sample), options = {selectedChannel: "S0"}, svg = new SvgNode("svg");
  const defaultSvg = new SvgNode("svg"); renderer.drawRadar(defaultSvg, sample, true, "CAR001");
  assert.equal(renderer.drawRadar(svg, sample, true, "CAR001", options), 4);
  assert.ok(walk(svg).some(n => n.tag === "path" && n.attributes.stroke === "#a3e8f1" && n.attributes.fill === "none"));
  assert.ok(walk(svg).some(n => n.tag === "title" && n.textContent.includes("Kijelölt csatorna · S0") && n.textContent.includes("CAR002")));
  assert.deepEqual(svg.attributes, defaultSvg.attributes);
  assert.equal(JSON.stringify(sample), original); assert.deepEqual(options, {selectedChannel: "S0"});
  assert.equal(renderer.channelVisual(sample.proximity.channels[0], true).status, "close");
  const front = new SvgNode("svg"); renderer.drawFrontView(front, sample, true, "CAR001", options);
  assert.ok(walk(front).some(n => n.tag === "circle" && n.attributes.stroke === "#a3e8f1" && n.attributes.r === "10"));
  assert.ok(walk(front).some(n => n.tag === "title" && n.textContent.includes("Kijelölt csatorna · Első felületi találat · S0")));
  assert.equal(JSON.stringify(sample), original);
});
test("unknown selection and legacy bundles do not invent channels or change default geometry", () => {
  const legacy = {...fixture}; delete legacy.proximity;
  for (const sample of [selectionSample(), legacy]) for (const fn of [renderer.drawRadar, renderer.drawFrontView]) {
    const base = new SvgNode("svg"), unknown = new SvgNode("svg");
    fn(base, sample, true, "CAR001"); fn(unknown, sample, true, "CAR001", {selectedChannel: "NOT-SAVED"});
    assert.deepEqual(unknown, base);
    const nullOptions = new SvgNode("svg"); fn(nullOptions, sample, true, "CAR001", null);
    assert.deepEqual(nullOptions, base);
  }
});
test("stale selected channel keeps stale readings and neutral selection without fresh proximity colors", () => {
  const sample = selectionSample(), original = JSON.stringify(sample);
  for (const fn of [renderer.drawRadar, renderer.drawFrontView]) {
    const svg = new SvgNode("svg"); fn(svg, sample, false, "CAR001", {selectedChannel: "S0"});
    assert.equal(svg.attributes["data-fresh"], "false"); assert.equal(svg.attributes["data-sample-time"], "12.4");
    assert.ok(!colors(svg).includes("#ff5b61")); assert.ok(!colors(svg).includes("#65d6a4"));
    assert.ok(walk(svg).some(n => n.attributes.stroke === "#a3e8f1"));
    assert.ok(walk(svg).some(n => n.tag === "title" && n.textContent.includes("elavult")));
  }
  assert.equal(JSON.stringify(sample), original);
});
test("rear, no-return and missing-coordinate selection only explains why no forward point is available", () => {
  const sample = selectionSample();
  for (const [id, note] of [["S4", "az előrenézeten kívül"], ["S6", "nincs felületi találat"], ["S1", "nincs mentett felületi pont"]]) {
    const base = new SvgNode("svg"), selected = new SvgNode("svg"), original = JSON.stringify(sample);
    renderer.drawFrontView(base, sample, true, "CAR001"); renderer.drawFrontView(selected, sample, true, "CAR001", {selectedChannel: id});
    assert.ok(walk(selected).some(n => n.tag === "text" && n.textContent.includes(`Kijelölt: ${id}`) && n.textContent.includes(note)));
    assert.deepEqual(walk(selected).filter(n => n.tag === "circle").map(n => n.attributes), walk(base).filter(n => n.tag === "circle").map(n => n.attributes));
    assert.ok(!walk(selected).some(n => n.tag === "title" && n.textContent.includes("Első felületi találat") && n.textContent.includes("REAR-CAR")));
    assert.equal(JSON.stringify(sample), original);
  }
});
test("custom threshold and range legends preserve two decimal places instead of rounding sensor settings", () => {
  const sample = {...fixture, proximity: {...fixture.proximity, range_m: 3.4, thresholds_m: {close: .55, warning: 1.25}, channels: fixture.proximity.channels.map(channel => ({...channel, distance_m: null, status: "no_return"}))}};
  const svg = new SvgNode("svg"); renderer.drawRadar(svg, sample, true, "CAR001");
  const labels = walk(svg).filter(n => n.tag === "text").map(n => n.textContent);
  assert.ok(labels.includes("≤ 0,55 m")); assert.ok(labels.includes("≤ 1,25 m")); assert.ok(labels.includes("≤ 3,40 m"));
  assert.ok(labels.some(label => label.includes("3,40 m · nincs visszatérés")));
});
test("both renderers obey the passive SVG export vocabulary; all geometry is finite", () => {
  const geometry = {svg: ["x", "y", "width", "height", "viewBox"], g: [], rect: ["x", "y", "width", "height", "rx", "ry"], circle: ["cx", "cy", "r"], path: ["d"], line: ["x1", "y1", "x2", "y2"], text: ["x", "y"], title: [], metadata: []};
  const presentation = ["fill", "stroke", "stroke-width", "stroke-dasharray", "stroke-linecap", "font-family", "font-size", "font-weight", "text-anchor", "paint-order", "opacity", "role", "aria-label", "data-sample-time", "data-fresh"];
  for (const fn of [renderer.drawRadar, renderer.drawFrontView]) for (const fresh of [true, false]) for (const selectedChannel of [null, "S0", "S4", "S6", "UNKNOWN"]) {
    const svg = new SvgNode("svg"); fn(svg, selectionSample(), fresh, "CAR001", {selectedChannel});
    assert.ok(walk(svg).length < 2000);
    for (const n of walk(svg)) {
      assert.ok(Object.hasOwn(geometry, n.tag));
      for (const [attribute, value] of Object.entries(n.attributes)) {
        assert.ok([...geometry[n.tag], ...presentation].includes(attribute), `${n.tag}.${attribute}`);
        assert.ok(!/NaN|Infinity|undefined|url\(/.test(value), `${attribute}=${value}`);
      }
    }
  }
});
process.stdout.write(`${checks} renderer checks passed.\n`);
