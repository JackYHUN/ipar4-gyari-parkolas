/* Saved-frame evidence analysis: no browser DOM, live state or synthetic hits. */
"use strict";
const assert = require("node:assert/strict");
require("../web/perception-analysis.js");
const analyze = global.YardPerceptionAnalysis.summarizeFrames;
let checks = 0;
function test(name, callback) { callback(); checks++; process.stdout.write(`PASS ${name}\n`); }
function channel(id, distance, status = null) {
  return {id, label: `Csatorna ${id}`, bearing_deg: 0, range_m: 3,
    distance_m: distance, status: status || (distance === null ? "no_return" : distance <= .55 ? "close" : distance <= 1.2 ? "warning" : "clear"),
    target: distance === null ? null : {id: "TARGET", kind: "vehicle", label: "Cél", source: "fixture"},
    origin_world_m: {x_m: 0, y_m: 0}, hit_world_m: distance === null ? null : {x_m: distance, y_m: 0}};
}
function frame(id, time, channels = [channel("FRONT", .4)], fresh = true, rowTime = time, carId = "CAR001") {
  return {id, sim_time: rowTime, car_id: carId, sensors: {
    schema_version: 1, source: "virtual_onboard_bundle", sampled: true, fresh,
    sample_sim_time_s: time, age_s: rowTime - time,
    proximity: {schema_version: 1, source: "virtual_bumper_ray_fan", sampled: true,
      sample_sim_time_s: time, range_m: 3, thresholds_m: {close: .55, warning: 1.2}, channels},
  }};
}
function clone(value) { return structuredClone(value); }
test("empty inputs produce serializable null minima and interval statistics", () => {
  for (const input of [null, undefined, "no-array", []]) {
    const result = analyze(input);
    assert.equal(result.frames.received, 0); assert.equal(result.samples.distinct, 0);
    assert.deepEqual(result.intervals, {count: 0, min_s: null, mean_s: null, max_s: null});
    assert.equal(result.proximity.minimum_clearance, null);
    assert.deepEqual(JSON.parse(JSON.stringify(result)), result);
    assert.equal(result.scope, "loaded_window_not_full_run");
  }
});
test("duplicate fresh and stale cache copies count one original observation and one proximity exposure", () => {
  const rows = [frame(101, 0, undefined, false, 2), frame(100, 0), frame(102, 0, undefined, true, .1), frame(103, .2, [channel("FRONT", 1)])];
  const result = analyze(rows);
  assert.deepEqual(result.frames, {received: 4, missing: 0, unsampled: 0, invalid: 0, fresh: 3, stale: 1, freshness_unknown: 0, fault_active: 0});
  assert.equal(result.samples.distinct, 2); assert.equal(result.samples.fresh_distinct, 2); assert.equal(result.samples.repeated, 2);
  assert.equal(result.proximity.close_samples, 1); assert.equal(result.proximity.warning_samples, 1);
  assert.equal(result.proximity.minimum_clearance.telemetry_id, 100);
  assert.equal(result.proximity.minimum_clearance.row_index, 1);
  assert.equal(result.proximity.minimum_clearance.fresh_when_received, true);
  assert.equal(result.intervals.mean_s, .2);
});
test("a stale-only sample keeps historical minimum provenance but never adds fresh exposure or intervals", () => {
  const result = analyze([frame(7, 1, [channel("REAR", .3)], false, 4), frame(8, 1, [channel("REAR", .3)], false, 4.1)]);
  assert.equal(result.samples.stale_only_distinct, 1); assert.equal(result.proximity.fresh_distinct_samples, 0);
  assert.equal(result.proximity.close_samples, 0); assert.equal(result.intervals.count, 0);
  assert.deepEqual(result.proximity.minimum_clearance, {row_index: 0, telemetry_id: 7, row_sim_time_s: 4,
    sample_sim_time_s: 1, car_id: "CAR001", channel_id: "REAR", channel_label: "Csatorna REAR",
    target: {id: "TARGET", kind: "vehicle", label: "Cél", source: "fixture"}, distance_m: .3,
    status: "close", fresh_when_received: false});
});

test("an active sensor fault is unavailable even within cache age timeout, matching archive and UI freshness", () => {
  const cached = frame(2, 0, undefined, true, .1); cached.sensors.fault_active = true;
  const original = frame(1, 0), result = analyze([cached, original]);
  assert.equal(result.frames.fresh, 1); assert.equal(result.frames.stale, 1); assert.equal(result.frames.fault_active, 1);
  assert.equal(result.samples.distinct, 1); assert.equal(result.samples.repeated, 1);
  assert.equal(result.proximity.minimum_clearance.telemetry_id, 1);
  const alone = analyze([cached]);
  assert.equal(alone.samples.stale_only_distinct, 1); assert.equal(alone.proximity.close_samples, 0);
  assert.equal(alone.proximity.minimum_clearance.fresh_when_received, false);
  assert.equal(cached.sensors.fresh, true); // Preserve original controller evidence.
});
test("old archives and absent or unsampled bundles do not fabricate bumper data", () => {
  const legacy = frame(1, 0); delete legacy.sensors.proximity;
  const unsampled = frame(2, 1); unsampled.sensors.sampled = false; unsampled.sensors.sample_sim_time_s = null;
  const result = analyze([{id: 0}, legacy, unsampled, {id: 3, sensors: null}]);
  assert.equal(result.frames.missing, 2); assert.equal(result.frames.unsampled, 1);
  assert.equal(result.frames.fresh, 1); assert.equal(result.proximity.missing_fresh_samples, 1);
  assert.equal(result.proximity.minimum_clearance, null); assert.equal(result.proximity.close_samples, 0);
});
test("missing freshness is unknown instead of silently classified fresh or stale", () => {
  const row = frame(1, 0); delete row.sensors.fresh;
  const result = analyze([row]);
  assert.equal(result.frames.freshness_unknown, 1); assert.equal(result.samples.freshness_unknown_distinct, 1);
  assert.equal(result.proximity.close_samples, 0); assert.equal(result.proximity.minimum_clearance.fresh_when_received, false);
});
test("schema and clock mismatches, nonfinite or negative clocks are rejected", () => {
  const rows = [];
  for (const value of [-1, Infinity, NaN, "1", null]) {
    const row = frame(rows.length, 1); row.sensors.sample_sim_time_s = value; rows.push(row);
  }
  const schema = frame(20, 1); schema.sensors.schema_version = 2; rows.push(schema);
  const source = frame(21, 1); source.sensors.source = "camera"; rows.push(source);
  const sampled = frame(22, 1); sampled.sensors.sampled = "true"; rows.push(sampled);
  const result = analyze(rows);
  assert.equal(result.frames.invalid, rows.length); assert.equal(result.samples.distinct, 0);
  for (const change of [p => p.sample_sim_time_s = 3, p => p.schema_version = 2, p => p.source = "ultrasound", p => p.sampled = false]) {
    const row = frame(1, 1); change(row.sensors.proximity);
    const checked = analyze([row]); assert.equal(checked.proximity.invalid_frames, 1); assert.equal(checked.proximity.minimum_clearance, null);
  }
});
test("invalid proximity configurations never feed distance or status summaries", () => {
  for (const patch of [{range_m: Infinity}, {range_m: -1}, {range_m: 0},
    {thresholds_m: {close: -.1, warning: 1.2}}, {thresholds_m: {close: 1.2, warning: 1.2}},
    {thresholds_m: {close: .55, warning: 3}}, {thresholds_m: {close: .55, warning: NaN}}, {channels: []}]) {
    const row = frame(1, 1); Object.assign(row.sensors.proximity, patch);
    const result = analyze([row]);
    assert.equal(result.proximity.invalid_frames, 1); assert.equal(result.proximity.fresh_distinct_samples, 0);
    assert.equal(result.proximity.minimum_clearance, null);
  }
});
test("negative, nonfinite, out-of-range, mistyped, unlabelled or status-inconsistent hits are ignored", () => {
  const bad = [channel("negative", -.1), channel("nan", NaN), channel("infinite", Infinity),
    channel("far", 3.01), channel("string", "0.3", "close"), channel("wrong-band", 2, "close"),
    {...channel("target", .2), target: null}, {...channel("bearing", .2), bearing_deg: NaN},
    {...channel("point", .2), hit_world_m: {x_m: Infinity, y_m: 0}},
    {...channel("range", .2), range_m: 4}, {...channel("", .2)}];
  const result = analyze([frame(1, 1, [...bad, channel("VALID", 1.2)])]);
  assert.equal(result.proximity.invalid_channels, bad.length); assert.equal(result.proximity.close_samples, 0);
  assert.equal(result.proximity.warning_samples, 1); assert.equal(result.proximity.minimum_clearance.channel_id, "VALID");
});
test("exact close, warning and range boundaries preserve recorded configured states", () => {
  const result = analyze([frame(1, 1, [channel("C", .55), channel("W", 1.2), channel("F", 3)])]);
  assert.equal(result.proximity.close_samples, 1); assert.equal(result.proximity.warning_samples, 1);
  assert.equal(result.proximity.clear_hit_samples, 1); assert.equal(result.proximity.minimum_clearance.distance_m, .55);
  const custom = frame(2, 2, [channel("CUSTOM", 1.25, "warning")]);
  custom.sensors.proximity.range_m = 3.4; custom.sensors.proximity.thresholds_m = {close: .6, warning: 1.25};
  custom.sensors.proximity.channels[0].range_m = 3.4;
  assert.equal(analyze([custom]).proximity.warning_samples, 1);
});
test("no-return channels remain unknown and never supply clearance or a green hit", () => {
  const result = analyze([frame(1, 1, [channel("A", null), channel("B", null)])]);
  assert.equal(result.proximity.no_return_only_samples, 1); assert.equal(result.proximity.clear_hit_samples, 0);
  assert.equal(result.proximity.minimum_clearance, null);
  const corrupted = analyze([frame(2, 2, [channel("A", null), channel("BAD", .4, "no_return")])]);
  assert.equal(corrupted.proximity.invalid_channels, 1); assert.equal(corrupted.proximity.no_return_only_samples, 0);
});
test("intervals sort distinct clocks per car and keep gaps without claiming network frequency", () => {
  const result = analyze([frame(1, 1, undefined, true, 1, "A"), frame(2, .2, undefined, true, .2, "A"),
    frame(3, 0, undefined, true, 0, "A"), frame(4, .2, undefined, false, 2, "A"),
    frame(5, 0, undefined, true, 0, "B"), frame(6, .4, undefined, true, .4, "B")]);
  assert.equal(result.intervals.count, 3); assert.equal(result.intervals.min_s, .2);
  assert.equal(result.intervals.max_s, .8); assert.ok(Math.abs(result.intervals.mean_s - 1.4 / 3) < 1e-12);
  assert.match(result.definitions.intervals, /nem hálózati/); assert.equal(result.samples.distinct, 5);
});
test("legacy nested bundle fallback works but never hides malformed top-level content", () => {
  const nested = frame(1, 1); nested.details = {sensors: nested.sensors}; delete nested.sensors;
  const malformed = clone(nested); malformed.sensors = {};
  const result = analyze([nested, malformed]);
  assert.equal(result.frames.fresh, 1); assert.equal(result.frames.invalid, 1);
  assert.equal(result.proximity.minimum_clearance.row_index, 0);
});
test("duplicate channel identifiers cannot masquerade as extra exposures", () => {
  const result = analyze([frame(1, 1, [channel("A", 1), channel("A", .1)])]);
  assert.equal(result.proximity.invalid_channels, 1); assert.equal(result.proximity.close_samples, 0);
  assert.equal(result.proximity.minimum_clearance.distance_m, 1);
});
test("analysis preserves arrays, bundles and target objects without returning mutable references", () => {
  const rows = [frame(1, .2), frame(2, 0, [channel("SIDE", 2)])], before = clone(rows);
  const result = analyze(rows);
  assert.deepEqual(rows, before); result.proximity.minimum_clearance.target.id = "CHANGED";
  assert.deepEqual(rows, before); assert.deepEqual(JSON.parse(JSON.stringify(analyze(rows))), analyze(rows));
});
process.stdout.write(`PASS ${checks} loaded-frame analysis checks\n`);
