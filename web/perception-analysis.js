/* Pure analysis of an explicitly loaded received-frame window. No live world,
   clocks, network access or controller calls; stale caches are not new samples. */
(function (global) {
  "use strict";
  const object = value => value !== null && typeof value === "object" && !Array.isArray(value);
  const finite = value => typeof value === "number" && Number.isFinite(value);
  const nonnegative = value => finite(value) && value >= 0;
  const text = value => typeof value === "string" && value.length > 0;
  const clockTolerance = 1e-6;
  const countFields = () => ({received: 0, missing: 0, unsampled: 0, invalid: 0, fresh: 0, stale: 0, freshness_unknown: 0, fault_active: 0});

  function bundle(row) {
    if (!object(row)) return undefined;
    // A present malformed top-level bundle must not be hidden by a fallback.
    return Object.hasOwn(row, "sensors") ? row.sensors : row.details?.sensors;
  }
  function identity(row) { return text(row.car_id) ? row.car_id : "loaded-car"; }
  function recordedStatus(distance, thresholds) {
    return distance <= thresholds.close ? "close" : distance <= thresholds.warning ? "warning" : "clear";
  }
  function validPointIfPresent(value) {
    return value === undefined || (object(value) && finite(value.x_m) && finite(value.y_m));
  }
  function inspectProximity(sample) {
    const proximity = sample.proximity;
    if (proximity === null || proximity === undefined) return {missing: true, invalid: false, channels: [], invalidChannels: 0};
    if (!object(proximity) || proximity.schema_version !== 1 || proximity.source !== "virtual_bumper_ray_fan"
        || proximity.sampled !== true || !nonnegative(proximity.sample_sim_time_s)
        || Math.abs(proximity.sample_sim_time_s - sample.sample_sim_time_s) > clockTolerance
        || !finite(proximity.range_m) || proximity.range_m <= 0
        || !object(proximity.thresholds_m) || !nonnegative(proximity.thresholds_m.close)
        || !finite(proximity.thresholds_m.warning) || proximity.thresholds_m.close >= proximity.thresholds_m.warning
        || proximity.thresholds_m.warning >= proximity.range_m
        || !Array.isArray(proximity.channels) || proximity.channels.length === 0 || proximity.channels.length > 32) {
      return {missing: false, invalid: true, channels: [], invalidChannels: 0};
    }
    const channels = [], identifiers = new Set();
    let invalidChannels = 0;
    for (const channel of proximity.channels) {
      const basic = object(channel) && text(channel.id) && !identifiers.has(channel.id)
        && finite(channel.bearing_deg)
        && (channel.range_m === undefined || channel.range_m === proximity.range_m);
      if (basic) identifiers.add(channel.id);
      const noReturn = basic && channel.status === "no_return" && channel.distance_m === null
        && (channel.target === null || channel.target === undefined)
        && (channel.hit_world_m === null || channel.hit_world_m === undefined);
      const observed = basic && nonnegative(channel.distance_m) && channel.distance_m <= proximity.range_m
        && channel.status === recordedStatus(channel.distance_m, proximity.thresholds_m)
        && object(channel.target) && text(channel.target.id)
        && validPointIfPresent(channel.origin_world_m) && validPointIfPresent(channel.hit_world_m);
      if (!noReturn && !observed) { invalidChannels++; continue; }
      channels.push(channel);
    }
    return {missing: false, invalid: channels.length === 0, channels, invalidChannels};
  }
  function targetSummary(target) {
    const result = {};
    for (const key of ["id", "kind", "label", "source"]) if (typeof target?.[key] === "string") result[key] = target[key];
    return result;
  }
  function minimumEvidence(entry, channel) {
    return {
      row_index: entry.index,
      telemetry_id: typeof entry.row.id === "string" || finite(entry.row.id) ? entry.row.id : null,
      row_sim_time_s: nonnegative(entry.row.sim_time) ? entry.row.sim_time : null,
      sample_sim_time_s: entry.sample.sample_sim_time_s,
      car_id: entry.carId,
      channel_id: channel.id,
      channel_label: text(channel.label) ? channel.label : channel.id,
      target: targetSummary(channel.target),
      distance_m: channel.distance_m,
      status: channel.status,
      fresh_when_received: entry.fresh,
    };
  }

  function summarizeFrames(rows) {
    const loaded = Array.isArray(rows) ? rows : [];
    const frames = countFields(), samplesByClock = new Map();
    let validSampledFrames = 0;
    for (let index = 0; index < loaded.length; index++) {
      const row = loaded[index], sample = bundle(row);
      frames.received++;
      if (sample === null || sample === undefined) { frames.missing++; continue; }
      if (!object(sample) || sample.schema_version !== 1 || sample.source !== "virtual_onboard_bundle"
          || typeof sample.sampled !== "boolean") { frames.invalid++; continue; }
      if (sample.sampled === false) { frames.unsampled++; continue; }
      if (!nonnegative(sample.sample_sim_time_s)) { frames.invalid++; continue; }
      const fresh = sample.fresh === true && sample.fault_active !== true;
      if (sample.fault_active === true) frames.fault_active++;
      if (fresh) frames.fresh++;
      else if (sample.fresh === false || sample.fault_active === true) frames.stale++;
      else frames.freshness_unknown++;
      validSampledFrames++;
      const carId = identity(row), key = JSON.stringify([carId, sample.sample_sim_time_s]);
      const previous = samplesByClock.get(key);
      // Prefer the original fresh reception when the same sample also occurs
      // in later cached diagnostics. Preserve row index for exact replay jump.
      if (!previous || (fresh && !previous.fresh)) samplesByClock.set(key, {row, sample, fresh, carId, index});
    }
    const samples = {distinct: samplesByClock.size, fresh_distinct: 0, stale_only_distinct: 0,
      freshness_unknown_distinct: 0, repeated: validSampledFrames - samplesByClock.size};
    const proximity = {fresh_distinct_samples: 0, close_samples: 0, warning_samples: 0,
      clear_hit_samples: 0, no_return_only_samples: 0, missing_fresh_samples: 0,
      invalid_frames: 0, invalid_channels: 0, valid_distinct_samples: 0,
      minimum_clearance: null};
    const clocksByCar = new Map();
    for (const entry of samplesByClock.values()) {
      if (entry.fresh) {
        samples.fresh_distinct++;
        if (!clocksByCar.has(entry.carId)) clocksByCar.set(entry.carId, []);
        clocksByCar.get(entry.carId).push(entry.sample.sample_sim_time_s);
      } else if (entry.sample.fresh === false || entry.sample.fault_active === true) samples.stale_only_distinct++;
      else samples.freshness_unknown_distinct++;
      const inspected = inspectProximity(entry.sample);
      proximity.invalid_channels += inspected.invalidChannels;
      if (inspected.invalid) proximity.invalid_frames++;
      if (inspected.missing || inspected.invalid) {
        if (entry.fresh) proximity.missing_fresh_samples++;
        continue;
      }
      proximity.valid_distinct_samples++;
      const hits = inspected.channels.filter(channel => channel.status !== "no_return");
      for (const channel of hits) {
        const previous = proximity.minimum_clearance;
        if (!previous || channel.distance_m < previous.distance_m
            || (channel.distance_m === previous.distance_m && entry.fresh && !previous.fresh_when_received)) {
          proximity.minimum_clearance = minimumEvidence(entry, channel);
        }
      }
      if (!entry.fresh) continue;
      proximity.fresh_distinct_samples++;
      if (!hits.length && inspected.invalidChannels === 0) proximity.no_return_only_samples++;
      // These independent counts describe samples containing each band. A
      // sample with both close and warning channels belongs to both groups.
      if (hits.some(channel => channel.status === "close")) proximity.close_samples++;
      if (hits.some(channel => channel.status === "warning")) proximity.warning_samples++;
      if (hits.some(channel => channel.status === "clear")) proximity.clear_hit_samples++;
    }
    const intervals = {count: 0, min_s: null, mean_s: null, max_s: null};
    let intervalTotal = 0;
    for (const clocks of clocksByCar.values()) {
      clocks.sort((a, b) => a - b);
      for (let index = 1; index < clocks.length; index++) {
        const delta = clocks[index] - clocks[index - 1];
        intervals.count++;
        intervalTotal += delta;
        intervals.min_s = intervals.min_s === null ? delta : Math.min(intervals.min_s, delta);
        intervals.max_s = intervals.max_s === null ? delta : Math.max(intervals.max_s, delta);
      }
    }
    if (intervals.count) intervals.mean_s = intervalTotal / intervals.count;
    return {
      schema_version: 1, source: "loaded_received_perception_frames", scope: "loaded_window_not_full_run",
      frames, samples, intervals, proximity,
      definitions: {
        frames: "A betöltött fogadott keretek; ismételt gyorsítótárazott észlelések is szerepelnek. Friss: fresh=true és nincs aktív érzékelőhiba; a hibás gyorsítótárazott keret az elavult csoportba kerül, az eredeti vezérlési bizonyíték változatlan.",
        samples: "Egyedi (járműazonosító, eredeti érzékelő-mintaidő) párok; az idő nulla is érvényes.",
        intervals: "Járművenként rendezett, különböző, frissen fogadott eredeti mintaidők szimulációs időkülönbsége; nem hálózati frekvencia vagy késleltetés.",
        proximity_counts: "Az adott színsávot tartalmazó egyedi friss minták száma. A sávok átfedhetnek; elavult másolat nem új kitettség és a közelség nem ütközési veszélymutató.",
        minimum_clearance: "A betöltött egyedi minták legkisebb érvényes lökhárítós találata; a fresh_when_received jelzi, ha csak elavult másolat áll rendelkezésre.",
        no_return: "Nincs visszatérés a vizsgált sugarakon; nem igazol szabad területet. Részben hibás csatornáknál sem igazol teljes lefedettséget.",
        validation: "1-es séma, ismert forrás, egyező eredeti mintaidő (1 µs tűrés), véges nemnegatív távolság a hatótávon belül, egyező állapot és konfigurált küszöbök.",
      },
    };
  }
  global.YardPerceptionAnalysis = Object.freeze({summarizeFrames});
})(globalThis);

/* Read-only interpretation of a received controller diagnostic, kept separate
   from the sensor analysis above. It never updates or reruns the controller. */
(function (global) {
  "use strict";
  const object = value => value !== null && typeof value === "object" && !Array.isArray(value);
  const finite = value => typeof value === "number" && Number.isFinite(value);
  const nonnegative = value => finite(value) && value >= 0;
  const close = (first, second) => finite(first) && finite(second)
    && Math.abs(first - second) <= 2e-5 + 1e-5 * Math.max(Math.abs(first), Math.abs(second));
  const clockClose = (first, second) => Math.abs(first - second) <= 1e-6;
  const scalars = ["decision_sim_time_s", "motion_interval_s", "speed_before_m_s", "speed_after_m_s",
    "target_speed_m_s", "applied_acceleration_m_s2", "braking_distance_m", "reaction_allowance_m",
    "stop_limit_remaining_m"];

  function analyzeFrame(data) {
    const result = {schema_version: 1, available: false, freshness: "unknown", phase: null,
      consistency: "unknown", issues: [], decision_sim_time_s: null, motion_interval_s: null,
      speed_before_m_s: null, speed_after_m_s: null, target_speed_m_s: null,
      applied_acceleration_m_s2: null, braking_distance_m: null, reaction_allowance_m: null,
      stop_limit_remaining_m: null, model_reserve_m: null, braking_utilization_pct: null};
    const control = object(data?.control) ? data.control : null;
    if (!control || control.source !== "ideal_route_controller") {
      result.issues.push(control ? "unknown_control_source" : "missing_control");
      return result;
    }
    let valid = true;
    for (const key of scalars) {
      const value = control[key];
      // A null limit explicitly means no assigned stopping target. Missing,
      // mistyped and negative limits remain distinct from that declaration.
      if (key === "stop_limit_remaining_m" && value === null) continue;
      const accepted = key === "applied_acceleration_m_s2" ? finite(value)
        : key === "motion_interval_s" ? finite(value) && value > 0 : nonnegative(value);
      if (!accepted) { result.issues.push(`invalid_${key}`); valid = false; }
      else result[key] = value;
    }
    const deceleration = control.braking_deceleration_m_s2;
    if (!finite(deceleration) || deceleration <= 0) {
      result.issues.push("invalid_braking_deceleration_m_s2"); valid = false;
    }
    const rowTime = data?.telemetry_sim_time;
    if (!nonnegative(rowTime)) { result.issues.push("invalid_telemetry_sim_time"); valid = false; }
    else if (result.decision_sim_time_s !== null && result.decision_sim_time_s - rowTime > 1e-6) {
      result.issues.push("future_control_decision"); valid = false;
    }
    if (!valid) return result;
    result.available = true;
    result.consistency = "consistent";
    const before = result.speed_before_m_s, after = result.speed_after_m_s;
    // A stop target or state name alone does not establish that motion ended.
    result.phase = after === 0 ? "stopped" : after < before ? "braking"
      : after > before ? "accelerating" : "cruising";
    const expectedBraking = before * before / (2 * deceleration);
    const expectedAcceleration = (after - before) / result.motion_interval_s;
    if (!close(result.braking_distance_m, expectedBraking)) {
      result.issues.push("braking_distance_mismatch"); result.consistency = "inconsistent";
    }
    if (!close(result.applied_acceleration_m_s2, expectedAcceleration)) {
      result.issues.push("acceleration_mismatch"); result.consistency = "inconsistent";
    }
    const sample = data?.sensors;
    const validSample = object(sample) && sample.schema_version === 1
      && sample.source === "virtual_onboard_bundle" && sample.sampled === true
      && nonnegative(sample.sample_sim_time_s);
    const controlSampleClock = control.sensor_sample_sim_time_s;
    const matchingClock = validSample && nonnegative(controlSampleClock)
      && clockClose(controlSampleClock, sample.sample_sim_time_s);
    if (validSample && nonnegative(controlSampleClock) && !matchingClock) {
      result.issues.push("sensor_sample_clock_mismatch"); result.consistency = "inconsistent";
    }
    if (validSample) result.freshness = data.fresh === true && data.gap !== true
      && sample.fresh === true && sample.fault_active !== true && matchingClock ? "current" : "retained";
    if (result.consistency === "consistent") {
      // The saved limit already contains the controller's configured margins.
      // Do not subtract reaction_allowance_m for a second time. This reserve
      // belongs to the ideal route model; it is not a real safety guarantee.
      const reserve = result.stop_limit_remaining_m === null ? null
        : result.stop_limit_remaining_m - result.braking_distance_m;
      result.model_reserve_m = finite(reserve) ? reserve : null;
      const utilization = Math.max(0, -result.applied_acceleration_m_s2) / deceleration * 100;
      result.braking_utilization_pct = finite(utilization) ? utilization : null;
    }
    return result;
  }
  global.YardBrakingAnalysis = Object.freeze({analyzeFrame});
})(globalThis);
