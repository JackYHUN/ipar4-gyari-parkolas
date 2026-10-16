/* Receipt-order decision navigation: compact independent controls keep this
   runnable from the public source without a server or a large run archive. */
"use strict";
const assert = require("node:assert/strict");
require("../web/perception-analysis.js");
const summarize = global.YardBrakingAnalysis.summarizeWindow;
let checks = 0;
function test(name, callback) { callback(); checks++; process.stdout.write(`PASS ${name}\n`); }
function row(time, seq, changes = {}) {
  return {id: `receipt-${seq}`, seq, sim_time: time,
    sensors: {schema_version: 1, source: "virtual_onboard_bundle", sampled: true,
      sample_sim_time_s: time, fresh: true, fault_active: false},
    control: {source: "ideal_route_controller", decision_sim_time_s: time,
      motion_interval_s: .1, speed_before_m_s: 3, speed_after_m_s: 2.8,
      target_speed_m_s: 0, applied_acceleration_m_s2: -2,
      braking_distance_m: 2.25, reaction_allowance_m: .3,
      braking_deceleration_m_s2: 2, stop_limit_remaining_m: 4,
      sensor_sample_sim_time_s: time, target_state: "pedestrian_stop", ...changes}};
}
function held(original, time, seq) {
  const copy = structuredClone(original); copy.id = `receipt-${seq}`;
  copy.seq = seq; copy.sim_time = time; copy.sensors.fresh = false; return copy;
}
function section(start, end, firstTime, lastTime, decisions, starts = false, ends = false) {
  return {start_index: start, end_index: end, start_time_s: firstTime, end_time_s: lastTime,
    decision_count: decisions, starts_at_window_edge: starts, ends_at_window_edge: ends};
}
test("empty and absent windows preserve explicit loaded-window scope", () => {
  const expected = {schema_version: 1, scope: "loaded_received_window", frame_count: 0,
    unique_decisions: 0, duplicate_decisions: 0, unavailable_frames: 0,
    invalid_decisions: 0, inconsistent_decisions: 0, retained_decisions: 0, freshness_unknown_decisions: 0,
    braking_sections: [], minimum_moving_reserve: null, strongest_braking: null};
  for (const input of [[], null, undefined, {}]) assert.deepEqual(summarize(input), expected);
  assert.equal(typeof global.YardBrakingAnalysis.analyzeFrame, "function");
  assert.equal(typeof global.YardPerceptionAnalysis.summarizeFrames, "function");
  assert.equal(Object.isFrozen(global.YardBrakingAnalysis), true);
});
test("contiguous receipt-order decisions form one explicitly edge-bounded section", () => {
  const result = summarize([row(1, 10), row(1.1, 11), row(1.2, 12)]);
  assert.equal(result.frame_count, 3); assert.equal(result.unique_decisions, 3);
  assert.deepEqual(result.braking_sections, [section(0, 2, 1, 1.2, 3, true, true)]);
  assert.deepEqual(result.minimum_moving_reserve, {index: 0, decision_sim_time_s: 1, model_reserve_m: 1.75});
  assert.deepEqual(result.strongest_braking, {index: 0, decision_sim_time_s: 1, applied_acceleration_m_s2: -2});
});
test("cruising and an actual zero-speed endpoint split observed braking", () => {
  const stopped = row(1.3, 4, {speed_before_m_s: .2, speed_after_m_s: 0,
    braking_distance_m: .01, stop_limit_remaining_m: .01});
  const result = summarize([row(1, 1), row(1.1, 2, {speed_after_m_s: 3,
    applied_acceleration_m_s2: 0}), row(1.2, 3), stopped]);
  assert.deepEqual(result.braking_sections,
    [section(0, 0, 1, 1, 1, true), section(2, 2, 1.2, 1.2, 1)]);
  assert.equal(result.minimum_moving_reserve.index, 3, "moving-to-stop decision remains a valid extremum");
});
test("held duplicates extend receipt indices without inventing decision time or count", () => {
  const original = row(1, 1), duplicate = held(original, 1.1, 2);
  const result = summarize([original, duplicate]);
  assert.equal(result.unique_decisions, 1); assert.equal(result.duplicate_decisions, 1);
  assert.equal(result.retained_decisions, 1);
  assert.deepEqual(result.braking_sections, [section(0, 1, 1, 1, 1, true, true)]);
  assert.equal(result.minimum_moving_reserve.index, 0);
});
test("a fresh decision after a held copy extends the same contiguous section once", () => {
  const original = row(1, 1);
  const result = summarize([original, held(original, 1.1, 2), row(1.2, 3)]);
  assert.equal(result.unique_decisions, 2); assert.equal(result.duplicate_decisions, 1);
  assert.deepEqual(result.braking_sections, [section(0, 2, 1, 1.2, 2, true, true)]);
});
test("retained control evidence remains usable without claiming current sensors", () => {
  const first = row(1, 1), second = row(1.1, 2);
  first.sensors.fresh = false; second.sensors.fault_active = true;
  const result = summarize([first, second]);
  assert.equal(result.retained_decisions, 2); assert.equal(result.unavailable_frames, 0);
  assert.deepEqual(result.braking_sections, [section(0, 1, 1, 1.1, 2, true, true)]);
});
test("unknown sensor freshness does not fabricate retained observations or invalidate control", () => {
  const input = row(1, 1); input.sensors = null;
  const result = summarize([input]);
  assert.equal(result.retained_decisions, 0); assert.equal(result.unavailable_frames, 0);
  assert.equal(result.freshness_unknown_decisions, 1);
  assert.equal(result.braking_sections.length, 1); assert.equal(result.minimum_moving_reserve.index, 0);
});
test("unknown freshness counts distinct available clocks and may overlap retained receptions", () => {
  const original = row(1, 1), unknown = held(original, 1.1, 2), retained = held(original, 1.2, 3);
  unknown.sensors = null;
  const malformed = row(1.3, 4, {speed_after_m_s: "2.8"}); malformed.sensors = null;
  const result = summarize([original, unknown, retained, malformed]);
  assert.equal(result.unique_decisions, 2); assert.equal(result.duplicate_decisions, 2);
  assert.equal(result.freshness_unknown_decisions, 1, "unavailable diagnostics do not create unknown-freshness evidence");
  assert.equal(result.retained_decisions, 1); assert.equal(result.invalid_decisions, 1);
  assert.deepEqual(result.braking_sections, [section(0, 2, 1, 1, 1, true)]);
});
test("the same held diagnostic across a gap forms disconnected locally counted sections", () => {
  const original = row(1, 1), duplicate = held(original, 1.1, 2); duplicate.break_before = true;
  const result = summarize([original, duplicate]);
  assert.equal(result.unique_decisions, 1); assert.equal(result.duplicate_decisions, 1);
  assert.deepEqual(result.braking_sections,
    [section(0, 0, 1, 1, 1, true), section(1, 1, 1, 1, 1, false, true)]);
});
test("canonical control comparison ignores object key insertion order at every depth", () => {
  const original = row(1, 1); original.control.extra = {a: 1, b: {x: [1, null], y: 2}};
  const duplicate = held(original, 1.1, 2);
  duplicate.control = Object.fromEntries(Object.entries(duplicate.control).reverse());
  duplicate.control.extra = {b: {y: 2, x: [1, null]}, a: 1};
  const result = summarize([original, duplicate]);
  assert.equal(result.inconsistent_decisions, 0);
  assert.deepEqual(result.braking_sections, [section(0, 1, 1, 1, 1, true, true)]);
});
test("a conflicting duplicate retroactively excludes its earliest extrema and section", () => {
  const original = row(1, 1, {stop_limit_remaining_m: 0}), duplicate = held(original, 1.1, 2);
  duplicate.control.stop_limit_remaining_m = 10;
  const result = summarize([original, duplicate, row(1.2, 3)]);
  assert.equal(result.unique_decisions, 2); assert.equal(result.duplicate_decisions, 1);
  assert.equal(result.inconsistent_decisions, 1);
  assert.equal(result.minimum_moving_reserve.index, 2); assert.equal(result.strongest_braking.index, 2);
  assert.deepEqual(result.braking_sections, [section(2, 2, 1.2, 1.2, 1, false, true)]);
});
test("a conflict in an additional received control field is not silently ignored", () => {
  const original = row(1, 1), duplicate = held(original, 1.1, 2);
  duplicate.control.target_state = "sensor_stop";
  const result = summarize([original, duplicate]);
  assert.equal(result.inconsistent_decisions, 1); assert.deepEqual(result.braking_sections, []);
  assert.equal(result.minimum_moving_reserve, null); assert.equal(result.strongest_braking, null);
});
test("internally contradictory diagnostics stay identifiable but never become normal decisions", () => {
  const result = summarize([row(1, 1, {braking_distance_m: 1})]);
  assert.equal(result.unique_decisions, 1); assert.equal(result.inconsistent_decisions, 1);
  assert.equal(result.invalid_decisions, 0); assert.equal(result.unavailable_frames, 0);
  assert.deepEqual(result.braking_sections, []); assert.equal(result.minimum_moving_reserve, null);
});
test("one mismatched sensor clock excludes all identical-control occurrences of its decision", () => {
  const original = row(1, 1), duplicate = held(original, 1.1, 2);
  duplicate.sensors.sample_sim_time_s = .9;
  const result = summarize([original, duplicate]);
  assert.equal(result.inconsistent_decisions, 1); assert.equal(result.minimum_moving_reserve, null);
  assert.deepEqual(result.braking_sections, []);
});
test("malformed scalar diagnostics have a separate distinct invalid-clock count", () => {
  const original = row(1, 1, {speed_after_m_s: "2.8"}), duplicate = held(original, 1.1, 2);
  const result = summarize([original, duplicate]);
  assert.equal(result.unique_decisions, 1); assert.equal(result.duplicate_decisions, 1);
  assert.equal(result.unavailable_frames, 2); assert.equal(result.invalid_decisions, 1);
  assert.equal(result.inconsistent_decisions, 0); assert.deepEqual(result.braking_sections, []);
});
test("missing controls and unknown sources never manufacture identifiable controller decisions", () => {
  const first = row(1, 1); delete first.control;
  const second = row(1.1, 2, {source: "guess"});
  const result = summarize([first, null, second, {control: []}]);
  assert.equal(result.frame_count, 4); assert.equal(result.unavailable_frames, 4);
  assert.equal(result.unique_decisions, 0); assert.equal(result.duplicate_decisions, 0);
  assert.equal(result.invalid_decisions, 0); assert.deepEqual(result.braking_sections, []);
});
test("decision and telemetry clocks require finite nonnegative numbers without coercion", () => {
  for (const clock of [null, undefined, "1", true, NaN, Infinity, -1]) {
    const result = summarize([row(1, 1, {decision_sim_time_s: clock})]);
    assert.equal(result.unique_decisions, 0, String(clock));
    assert.equal(result.unavailable_frames, 1, String(clock));
    assert.deepEqual(result.braking_sections, [], String(clock));
  }
  for (const time of [null, undefined, "1", true, NaN, Infinity, -1]) {
    const input = row(1, 1); input.sim_time = time;
    assert.equal(summarize([input]).unique_decisions, 0, String(time));
  }
  assert.equal(summarize([row(0, 0)]).unique_decisions, 1, "zero is a real clock and sequence");
});
test("future decisions do not count or become navigation targets", () => {
  const result = summarize([row(1, 1, {decision_sim_time_s: 1.01})]);
  assert.equal(result.unique_decisions, 0); assert.equal(result.unavailable_frames, 1);
  assert.equal(result.minimum_moving_reserve, null); assert.deepEqual(result.braking_sections, []);
});
test("explicit break, gap and positive numeric gap annotations split observations", () => {
  for (const patch of [{break_before: true}, {gap_before: true}, {time_gap_before_s: .001}]) {
    const second = row(1.1, 2); Object.assign(second, patch);
    assert.deepEqual(summarize([row(1, 1), second]).braking_sections,
      [section(0, 0, 1, 1, 1, true), section(1, 1, 1.1, 1.1, 1, false, true)]);
  }
});
test("sequence skips, repeats, reverse order and invalid sequence types split sections", () => {
  for (const seq of [1, 0, 3, undefined, null, "2", 2.5, NaN, Infinity]) {
    const result = summarize([row(1, 1), row(1.1, seq)]);
    assert.equal(result.braking_sections.length, 2, String(seq));
  }
  assert.equal(summarize([row(1, 1), row(1.1, 2)]).braking_sections.length, 1);
});
test("nonincreasing receipt times split even an otherwise identical held decision", () => {
  const original = row(1, 1);
  for (const time of [1, .9]) {
    const duplicate = held(original, time, 2);
    // At .9, keep the original controller clock nonfuture to isolate the
    // receipt ordering check from future-diagnostic rejection.
    if (time < 1) {
      original.sim_time = 1.1; original.control.decision_sim_time_s = .8;
      duplicate.control.decision_sim_time_s = .8;
    }
    const result = summarize([original, duplicate]);
    assert.equal(result.braking_sections.length, 2, String(time));
  }
});
test("backward decision clocks split without silently sorting receipt order", () => {
  const second = row(.9, 2); second.sim_time = 1.1;
  const result = summarize([row(1, 1), second]);
  assert.deepEqual(result.braking_sections,
    [section(0, 0, 1, 1, 1, true), section(1, 1, .9, .9, 1, false, true)]);
});
test("sampling-period gaps use a bounded default and the explicit valid period", () => {
  const rows = [row(1, 1), row(1.2, 2)];
  assert.equal(summarize(rows).braking_sections.length, 2);
  assert.equal(summarize(rows, {sample_period_s: .2}).braking_sections.length, 1);
  for (const period of [null, undefined, "0.2", true, 0, -1, NaN, Infinity])
    assert.equal(summarize(rows, {sample_period_s: period}).braking_sections.length, 2, String(period));
  assert.equal(summarize([row(1, 1), row(1.15, 2)]).braking_sections.length, 1,
    "six-decimal clock rounding at the default boundary is tolerated");
  assert.equal(summarize([row(1, 1), row(1.15001, 2)]).braking_sections.length, 2);
});
test("missing target remains null while signed negative reserves remain candidates", () => {
  const first = row(1, 1, {stop_limit_remaining_m: null});
  const negative = row(1.1, 2, {stop_limit_remaining_m: 1});
  const result = summarize([first, negative]);
  assert.deepEqual(result.minimum_moving_reserve, {index: 1, decision_sim_time_s: 1.1, model_reserve_m: -1.25});
  assert.equal(summarize([first]).minimum_moving_reserve, null);
});
test("stationary decisions cannot hide a moving reserve with a misleading zero minimum", () => {
  const stopped = row(1.1, 2, {speed_before_m_s: 0, speed_after_m_s: 0,
    applied_acceleration_m_s2: 0, braking_distance_m: 0, stop_limit_remaining_m: 0});
  const result = summarize([row(1, 1), stopped]);
  assert.equal(result.minimum_moving_reserve.index, 0); assert.equal(result.minimum_moving_reserve.model_reserve_m, 1.75);
  assert.equal(summarize([stopped]).minimum_moving_reserve, null);
  assert.equal(summarize([stopped]).strongest_braking, null);
});
test("strongest actual braking includes a moving step reaching zero and excludes positive acceleration", () => {
  const stop = row(1.1, 2, {speed_before_m_s: .3, speed_after_m_s: 0,
    applied_acceleration_m_s2: -3, braking_distance_m: .0225});
  const result = summarize([row(1, 1), stop]);
  assert.deepEqual(result.strongest_braking,
    {index: 1, decision_sim_time_s: 1.1, applied_acceleration_m_s2: -3});
  const accelerating = row(1, 1, {speed_after_m_s: 3.1, applied_acceleration_m_s2: 1});
  assert.equal(summarize([accelerating]).strongest_braking, null);
});
test("extremum ties preserve the earliest receipt rather than sorting diagnostic clocks", () => {
  const second = row(.9, 2); second.sim_time = 1.1;
  const result = summarize([row(1, 1), second]);
  assert.equal(result.minimum_moving_reserve.index, 0); assert.equal(result.strongest_braking.index, 0);
});
test("invalid frames split sections and preserve exact original array indices", () => {
  const result = summarize([row(1, 1), null, row(1.2, 3)]);
  assert.equal(result.unavailable_frames, 1);
  assert.deepEqual(result.braking_sections,
    [section(0, 0, 1, 1, 1, true), section(2, 2, 1.2, 1.2, 1, false, true)]);
});
test("a 2000-row loaded window is deterministic, detached and does not mutate received data", () => {
  const rows = Array.from({length: 2000}, (_, index) => row(index / 10, index));
  const before = structuredClone(rows), first = summarize(rows), second = summarize(rows);
  assert.equal(first.frame_count, 2000); assert.equal(first.unique_decisions, 2000);
  assert.deepEqual(first.braking_sections, [section(0, 1999, 0, 199.9, 2000, true, true)]);
  assert.deepEqual(first, second); assert.deepEqual(rows, before);
  first.braking_sections[0].decision_count = 0;
  first.minimum_moving_reserve.index = -1;
  assert.deepEqual(summarize(rows), second); assert.deepEqual(JSON.parse(JSON.stringify(second)), second);
});
process.stdout.write(`PASS ${checks} received-decision window checks\n`);
