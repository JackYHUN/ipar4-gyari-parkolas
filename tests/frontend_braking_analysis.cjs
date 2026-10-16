/* Received braking diagnostics: independent numerical examples and bad-frame
   boundaries, without a browser, live world, controller call or network. */
"use strict";
const assert = require("node:assert/strict");
require("../web/perception-analysis.js");
const analyze = global.YardBrakingAnalysis.analyzeFrame;
let checks = 0;
function test(name, callback) { callback(); checks++; process.stdout.write(`PASS ${name}\n`); }
function frame() {
  return {telemetry_sim_time: 12, fresh: true, gap: false, archive: false,
    sensors: {schema_version: 1, source: "virtual_onboard_bundle", sampled: true,
      sample_sim_time_s: 11.9, fresh: true, fault_active: false},
    control: {source: "ideal_route_controller", decision_sim_time_s: 12,
      motion_interval_s: .1, speed_before_m_s: 3, speed_after_m_s: 2.8,
      target_speed_m_s: 0, applied_acceleration_m_s2: -2,
      braking_distance_m: 2.25, reaction_allowance_m: .3,
      braking_deceleration_m_s2: 2, stop_limit_remaining_m: 4,
      sensor_sample_sim_time_s: 11.9, target_state: "pedestrian_stop"}};
}
function changed(patch) { const value = frame(); Object.assign(value.control, patch); return value; }
test("bounded braking exposes actual speeds and margin without subtracting reaction twice", () => {
  const result = analyze(frame());
  assert.equal(result.available, true); assert.equal(result.consistency, "consistent");
  assert.equal(result.phase, "braking"); assert.equal(result.freshness, "current");
  assert.equal(result.model_reserve_m, 1.75); assert.equal(result.braking_utilization_pct, 100);
  assert.deepEqual(result.issues, []);
});
test("a stopping target is not an already stopped car", () => {
  assert.equal(analyze(frame()).phase, "braking");
  const result = analyze(changed({speed_after_m_s: 3.1, applied_acceleration_m_s2: 1}));
  assert.equal(result.phase, "accelerating"); assert.equal(result.braking_utilization_pct, 0);
});
test("the step reaching zero is stopped even if its applied acceleration is negative", () => {
  const result = analyze(changed({speed_before_m_s: .2, speed_after_m_s: 0, braking_distance_m: .01}));
  assert.equal(result.phase, "stopped"); assert.equal(result.consistency, "consistent");
});
test("stationary and constant-speed phases use saved speeds rather than target state", () => {
  assert.equal(analyze(changed({speed_before_m_s: 0, speed_after_m_s: 0,
    applied_acceleration_m_s2: 0, braking_distance_m: 0, reaction_allowance_m: 0})).phase, "stopped");
  assert.equal(analyze(changed({speed_after_m_s: 3, applied_acceleration_m_s2: 0})).phase, "cruising");
});
test("no assigned stopping target preserves kinematics without inventing a zero margin", () => {
  const result = analyze(changed({stop_limit_remaining_m: null}));
  assert.equal(result.available, true); assert.equal(result.model_reserve_m, null);
  assert.equal(result.phase, "braking"); assert.equal(result.braking_utilization_pct, 100);
});
test("a negative reserve is preserved and never clipped to a reassuring zero", () => {
  assert.equal(analyze(changed({stop_limit_remaining_m: 1})).model_reserve_m, -1.25);
});
test("unknown and missing controllers do not produce diagnostics", () => {
  for (const data of [null, undefined, {}, {control: []}, {control: {source: "inferred"}}]) {
    const result = analyze(data); assert.equal(result.available, false);
    assert.equal(result.phase, null); assert.equal(result.consistency, "unknown");
    assert.equal(result.model_reserve_m, null); assert.equal(result.freshness, "unknown");
    assert.equal(result.braking_utilization_pct, null);
  }
});
test("missing or mistyped required scalars are not coerced into measurements", () => {
  for (const key of ["decision_sim_time_s", "motion_interval_s", "speed_before_m_s", "speed_after_m_s",
    "target_speed_m_s", "applied_acceleration_m_s2", "braking_distance_m", "reaction_allowance_m",
    "stop_limit_remaining_m", "braking_deceleration_m_s2"]) {
    for (const value of [undefined, "0", true, NaN, Infinity, -Infinity]) {
      const result = analyze(changed({[key]: value}));
      assert.equal(result.available, false, `${key} ${String(value)}`);
      if (key !== "braking_deceleration_m_s2") assert.equal(result[key], null);
      assert.equal(result.model_reserve_m, null);
    }
  }
});
test("negative physical values and zero integration or deceleration are invalid", () => {
  for (const key of ["decision_sim_time_s", "motion_interval_s", "speed_before_m_s", "speed_after_m_s",
    "target_speed_m_s", "braking_distance_m", "reaction_allowance_m", "stop_limit_remaining_m",
    "braking_deceleration_m_s2"]) assert.equal(analyze(changed({[key]: -.01})).available, false, key);
  for (const key of ["motion_interval_s", "braking_deceleration_m_s2"])
    assert.equal(analyze(changed({[key]: 0})).available, false, key);
});
test("a missing, invalid or future telemetry time cannot validate the controller clock", () => {
  for (const time of [undefined, null, "12", true, -.1, NaN, Infinity, 11.99]) {
    const data = frame(); data.telemetry_sim_time = time;
    assert.equal(analyze(data).available, false, String(time));
  }
  assert.equal(analyze(changed({decision_sim_time_s: 12.0000005})).available, true);
});
test("six-decimal rounding is accepted in both independent physical identities", () => {
  const data = changed({speed_before_m_s: 2.123457, speed_after_m_s: 1.974692,
    applied_acceleration_m_s2: -2.97531, motion_interval_s: .05,
    braking_deceleration_m_s2: 3, braking_distance_m: .751512});
  assert.equal(analyze(data).consistency, "consistent");
});
test("contradictory braking distance suppresses derived margin and utilization", () => {
  const result = analyze(changed({braking_distance_m: 1}));
  assert.equal(result.available, true); assert.equal(result.consistency, "inconsistent");
  assert.equal(result.model_reserve_m, null); assert.equal(result.braking_utilization_pct, null);
  assert.ok(result.issues.includes("braking_distance_mismatch")); assert.equal(result.phase, "braking");
});
test("contradictory acceleration is disclosed without replacing a received value", () => {
  const result = analyze(changed({applied_acceleration_m_s2: -1}));
  assert.equal(result.consistency, "inconsistent"); assert.equal(result.applied_acceleration_m_s2, -1);
  assert.equal(result.model_reserve_m, null); assert.ok(result.issues.includes("acceleration_mismatch"));
});
test("an original sensor clock mismatch is not presented as current or consistent", () => {
  const result = analyze(changed({sensor_sample_sim_time_s: 11.8}));
  assert.equal(result.freshness, "retained"); assert.equal(result.consistency, "inconsistent");
  assert.equal(result.model_reserve_m, null); assert.ok(result.issues.includes("sensor_sample_clock_mismatch"));
});
test("stale, faulty, held and caller-stale sensor observations remain retained", () => {
  for (const change of [data => data.sensors.fresh = false, data => data.sensors.fault_active = true,
    data => data.gap = true, data => data.fresh = false, data => delete data.control.sensor_sample_sim_time_s]) {
    const data = frame(); change(data);
    assert.equal(analyze(data).available, true); assert.equal(analyze(data).freshness, "retained");
  }
  const data = frame(); data.archive = true;
  assert.equal(analyze(data).freshness, "current", "archive is contextual, not itself an observation gap");
});
test("missing, unsampled and malformed sensor bundles leave kinematic freshness unknown", () => {
  for (const sensors of [null, [], {}, {schema_version: 1, source: "virtual_onboard_bundle", sampled: false},
    {...frame().sensors, schema_version: 2}, {...frame().sensors, sample_sim_time_s: "11.9"}]) {
    const data = frame(); data.sensors = sensors;
    const result = analyze(data); assert.equal(result.available, true); assert.equal(result.freshness, "unknown");
  }
});
test("overflow in an otherwise finite physical identity cannot create derived Infinity", () => {
  const result = analyze(changed({speed_before_m_s: Number.MAX_VALUE}));
  assert.equal(result.consistency, "inconsistent"); assert.equal(result.model_reserve_m, null);
  assert.equal(result.braking_utilization_pct, null);
  assert.deepEqual(JSON.parse(JSON.stringify(result)), result);
});
// Values below were transcribed from actually received MQTT snapshot frames:
// course-verification-v18/campaign-20261012-0715/core/full-visual-frames.jsonl,
// run 6d682f18f3dc. The compact scalar fixtures keep this test runnable without
// the large visual archive; they do not synthesize a physical camera recording.
test("received zebra-braking frame retains six-decimal diagnostic rounding", () => {
  const data = changed({decision_sim_time_s: 9.45, motion_interval_s: .05,
    speed_before_m_s: 2.486483, speed_after_m_s: 2.390419, target_speed_m_s: 2.390419,
    applied_acceleration_m_s2: -1.921268, braking_distance_m: 1.545649,
    reaction_allowance_m: .248648, stop_limit_remaining_m: 1.677568,
    sensor_sample_sim_time_s: 9.45}); // Receipt 168, CAR001.
  data.telemetry_sim_time = data.sensors.sample_sim_time_s = 9.45;
  const result = analyze(data);
  assert.equal(result.consistency, "consistent"); assert.equal(result.phase, "braking");
  assert.equal(result.freshness, "current");
  assert.ok(Math.abs(result.model_reserve_m - .131919) < 1e-12);
  assert.ok(Math.abs(result.braking_utilization_pct - 96.0634) < 1e-9);
});
test("received sensor-outage braking is retained evidence rather than fresh perception", () => {
  const data = changed({decision_sim_time_s: 32.2, motion_interval_s: .05,
    speed_before_m_s: 1.7, speed_after_m_s: 1.6, target_speed_m_s: 0,
    applied_acceleration_m_s2: -2, braking_distance_m: .7225,
    reaction_allowance_m: .17, stop_limit_remaining_m: 63.123575,
    sensor_sample_sim_time_s: 31.05, target_state: "sensor_stop"}); // Receipt 552, CAR001.
  data.telemetry_sim_time = 32.2; data.sensors.sample_sim_time_s = 31.05;
  data.sensors.fresh = false; data.sensors.fault_active = true; data.fresh = false;
  const result = analyze(data);
  assert.equal(result.available, true); assert.equal(result.consistency, "consistent");
  assert.equal(result.phase, "braking"); assert.equal(result.freshness, "retained");
  assert.ok(Math.abs(result.model_reserve_m - 62.401075) < 1e-10);
});
test("the analyzer is deterministic, detached and does not mutate saved controller or sensor data", () => {
  const data = frame(), before = structuredClone(data), first = analyze(data), second = analyze(data);
  assert.deepEqual(first, second); first.issues.push("edited-output");
  assert.deepEqual(data, before); assert.deepEqual(analyze(data), second);
  assert.deepEqual(JSON.parse(JSON.stringify(second)), second);
  assert.equal(Object.isFrozen(global.YardBrakingAnalysis), true);
  assert.equal(typeof global.YardPerceptionAnalysis.summarizeFrames, "function");
});
process.stdout.write(`PASS ${checks} received-braking diagnostic checks\n`);
