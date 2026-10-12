/* Shared live/archive renderers. All observations come from the supplied bundle.
   Deliberately passive SVG: exported frames need no script, image or external font. */
(function (global) {
  "use strict";
  const NS = "http://www.w3.org/2000/svg";
  const valid = value => value !== null && value !== undefined && Number.isFinite(Number(value));
  const number = (value, fallback = 0) => valid(value) ? Number(value) : fallback;
  const clamp = (value, low, high) => Math.min(high, Math.max(low, value));
  const f = (value, digits = 1) => number(value).toLocaleString("hu-HU", {minimumFractionDigits: digits, maximumFractionDigits: digits});
  const point = (x, y) => ({x: Number(x.toFixed(3)), y: Number(y.toFixed(3))});
  function node(tag, attributes, parent, text) {
    const element = document.createElementNS(NS, tag);
    for (const [key, value] of Object.entries(attributes || {})) element.setAttribute(key, String(value));
    if (text !== undefined) element.textContent = String(text);
    if (parent) parent.append(element);
    return element;
  }
  function path(points) { return points.map((p, i) => `${i ? "L" : "M"}${p.x},${p.y}`).join(" ") + " Z"; }
  function reset(svg, sample, fresh) {
    svg.replaceChildren();
    svg.setAttribute("font-family", "Segoe UI, Arial, sans-serif");
    svg.setAttribute("data-sample-time", String(number(sample.sample_sim_time_s)));
    svg.setAttribute("data-fresh", String(Boolean(fresh)));
  }
  function relativeObservation(sample, observation, frame = null) {
    const origin = frame || sample?.reference_pose;
    if (!valid(observation?.x_m) || !valid(observation?.y_m) || !valid(origin?.x_m) || !valid(origin?.y_m)) return null;
    const angle = number(frame?.heading_deg, number(sample.pose?.heading_deg, number(origin.heading_deg))) * Math.PI / 180;
    const dx = Number(observation.x_m) - Number(origin.x_m), dy = Number(observation.y_m) - Number(origin.y_m);
    const forward = dx * Math.cos(angle) + dy * Math.sin(angle), right = -dx * Math.sin(angle) + dy * Math.cos(angle);
    return {forward, right, distance: Math.hypot(dx, dy), bearing: Math.atan2(right, forward) * 180 / Math.PI};
  }
  function channelVisual(channel, fresh, thresholds = {close: .55, warning: 1.2}) {
    const distance = valid(channel?.distance_m) ? Math.max(0, Number(channel.distance_m)) : null;
    const observed = distance !== null && ["close", "warning", "clear"].includes(channel?.status);
    if (!fresh) return {status: "stale", distance, observed, color: "#b69869", activeBand: observed ? 2 : -1, text: observed ? `${f(distance, distance < 1 ? 2 : 1)} m` : "—"};
    if (!observed) return {status: "no_return", distance: null, observed: false, color: "#526473", activeBand: -1, text: "—"};
    const status = distance <= number(thresholds.close, .55) ? "close" : distance <= number(thresholds.warning, 1.2) ? "warning" : "clear";
    return {status, distance, observed: true, color: {close: "#ff5b61", warning: "#f9bb54", clear: "#65d6a4"}[status], activeBand: {close: 0, warning: 1, clear: 2}[status], text: `${f(distance, distance < 1 ? 2 : 1)} m`};
  }
  function bandPath(cx, cy, inner, outer, bearing, halfAngle) {
    const a = (bearing - halfAngle) * Math.PI / 180, b = (bearing + halfAngle) * Math.PI / 180;
    const polar = (radius, angle) => point(cx + Math.sin(angle) * radius, cy - Math.cos(angle) * radius);
    const p1 = polar(outer, a), p2 = polar(outer, b), p3 = polar(inner, b), p4 = polar(inner, a);
    return `M${p1.x},${p1.y} A${outer},${outer} 0 0 1 ${p2.x},${p2.y} L${p3.x},${p3.y} A${inner},${inner} 0 0 0 ${p4.x},${p4.y} Z`;
  }
  function carTop(svg, cx, cy, fresh) {
    node("path", {d: `M${cx - 22},${cy - 49} Q${cx},${cy - 59} ${cx + 22},${cy - 49} L${cx + 28},${cy - 25} L${cx + 29},${cy + 40} Q${cx + 29},${cy + 52} ${cx + 17},${cy + 54} H${cx - 17} Q${cx - 29},${cy + 52} ${cx - 29},${cy + 40} L${cx - 28},${cy - 25} Z`, fill: "#0a1119", opacity: .8}, svg);
    for (const side of [-1, 1]) for (const y of [-28, 30]) node("rect", {x: cx + side * 27 - 3, y: cy + y - 10, width: 6, height: 20, rx: 2, fill: "#020609", stroke: "#40505b", "stroke-width": .8}, svg);
    node("path", {d: `M${cx - 20},${cy - 51} Q${cx},${cy - 58} ${cx + 20},${cy - 51} L${cx + 26},${cy - 26} L${cx + 25},${cy + 43} Q${cx + 23},${cy + 50} ${cx + 15},${cy + 51} H${cx - 15} Q${cx - 23},${cy + 50} ${cx - 25},${cy + 43} L${cx - 26},${cy - 26} Z`, fill: fresh ? "#c1d1dc" : "#a3a6a3", stroke: "#eef4f7", "stroke-width": 1}, svg);
    node("path", {d: `M${cx - 16},${cy - 47} Q${cx},${cy - 52} ${cx + 16},${cy - 47} L${cx + 20},${cy - 28} H${cx - 20} Z`, fill: "#dde6ed", opacity: .8}, svg);
    node("path", {d: `M${cx - 20},${cy - 23} L${cx + 20},${cy - 23} L${cx + 15},${cy - 5} H${cx - 15} Z`, fill: "#293f51", stroke: "#86a2b4", "stroke-width": .7}, svg);
    node("path", {d: `M${cx - 13},${cy} H${cx + 13} L${cx + 14},${cy + 22} H${cx - 14} Z`, fill: "#a5b9c8", stroke: "#d6e2ea", "stroke-width": .6}, svg);
    node("path", {d: `M${cx - 14},${cy + 26} H${cx + 14} L${cx + 19},${cy + 36} H${cx - 19} Z`, fill: "#314859", stroke: "#829aa9", "stroke-width": .6}, svg);
    node("path", {d: `M${cx - 23},${cy - 40} L${cx - 20},${cy - 34} M${cx + 23},${cy - 40} L${cx + 20},${cy - 34}`, fill: "none", stroke: "#f5fcff", "stroke-width": 2.5, "stroke-linecap": "round"}, svg);
    node("path", {d: `M${cx - 20},${cy + 44} H${cx - 10} M${cx + 10},${cy + 44} H${cx + 20}`, fill: "none", stroke: "#f39797", "stroke-width": 2.4, "stroke-linecap": "round"}, svg);
    node("path", {d: `M${cx - 27},${cy - 17} L${cx - 32},${cy - 13} M${cx + 27},${cy - 17} L${cx + 32},${cy - 13}`, fill: "none", stroke: "#b9ceda", "stroke-width": 3, "stroke-linecap": "round"}, svg);
  }
  function proximityChannels(sample) {
    const p = sample?.proximity;
    return p?.schema_version === 1 && p.source === "virtual_bumper_ray_fan" && p.sampled === true && Array.isArray(p.channels) ? p.channels.filter(c => valid(c.bearing_deg)) : [];
  }
  // Selection is a presentation hint only. It must refer to a channel that was
  // actually saved in this bundle; old archives and unknown IDs add no geometry.
  function selectedChannel(sample, options) {
    return typeof options?.selectedChannel === "string" ? proximityChannels(sample).find(channel => channel.id === options.selectedChannel) || null : null;
  }
  // New geometric scans already carry their sampling frame. Use that exact
  // saved frame for the whole forward reconstruction, including body poses.
  // Legacy bundles retain their original measured-heading interpretation.
  function frontReference(sample) {
    if (!proximityChannels(sample).length) return null;
    const pose = sample.proximity?.reference_pose || sample.reference_pose;
    return valid(pose?.x_m) && valid(pose?.y_m) && valid(pose?.heading_deg) ? pose : null;
  }
  function frontHeading(sample) {
    return number(frontReference(sample)?.heading_deg, number(sample.pose?.heading_deg, number(sample.reference_pose?.heading_deg)));
  }
  function drawLegacyRadar(svg, sample, fresh, carId) {
    const cx = 170, cy = 164, radius = 113, range = Math.max(.1, number(sample.range_m, 18)), scale = radius / range;
    for (const r of [radius / 3, radius * 2 / 3, radius]) {
      node("circle", {cx, cy, r, fill: "none", stroke: "#304653", "stroke-width": 1, "stroke-dasharray": "2 6"}, svg);
      node("text", {x: cx + 7, y: cy + r - 4, fill: "#76909e", "font-size": 9}, svg, `${f(r / scale, 0)} m`);
    }
    const observations = (sample.environment?.vehicles || []).map(item => ({item, relative: relativeObservation(sample, item)})).filter(o => o.relative);
    for (const {item, relative} of observations) {
      const x = cx + relative.right * scale, y = cy - relative.forward * scale;
      const circle = node("circle", {cx: x, cy: y, r: 4, fill: fresh ? "#83c9d5" : "#b69869", stroke: "#13202b", "stroke-width": 2}, svg);
      node("title", {}, circle, `${item.id} · régi középpont-távolság: ${f(item.distance_m)} m; nincs lökhárítós csatornaminta.`);
    }
    carTop(svg, cx, cy, fresh);
    node("text", {x: 170, y: 294, "text-anchor": "middle", fill: "#b8cbd4", "font-size": 10}, svg, "Régi környezeti minta · nincs közelségcsatorna");
    node("text", {x: 170, y: 313, "text-anchor": "middle", fill: "#738f9e", "font-size": 9}, svg, `${carId} · középpontok, nem lökhárító-távolság`);
    return observations.length;
  }
  function drawRadar(svg, sample, fresh, carId, options = {}) {
    reset(svg, sample, fresh);
    node("rect", {width: 340, height: 340, rx: 14, fill: "#101c27"}, svg);
    node("rect", {x: 1, y: 1, width: 338, height: 338, rx: 13, fill: "none", stroke: "#314350", "stroke-width": 1}, svg);
    const channels = proximityChannels(sample), proximity = sample.proximity;
    node("text", {x: 16, y: 24, fill: "#edf4f8", "font-size": 10, "font-weight": 600}, svg, channels.length ? "KÖZELSÉGÉRZÉKELÉS" : "KÖRNYEZETI ÁTTEKINTÉS");
    node("text", {x: 324, y: 24, "text-anchor": "end", fill: fresh ? "#8ca5b3" : "#d5b783", "font-size": 9, "font-weight": 600}, svg, fresh ? `${f(sample.sample_sim_time_s, 1)} s` : "UTOLSÓ MINTA");
    if (!channels.length) return drawLegacyRadar(svg, sample, fresh, carId);
    const cx = 170, cy = 157, half = Math.min(16, number(proximity.beam_half_angle_deg, 20)), selected = selectedChannel(sample, options);
    for (const channel of channels) {
      const visual = channelVisual(channel, fresh, proximity.thresholds_m), bearing = Number(channel.bearing_deg), isSelected = channel === selected;
      const group = node("g", {}, svg);
      node("title", {}, group, `${isSelected ? "Kijelölt csatorna · " : ""}${channel.label || channel.id}: ${visual.observed ? `${f(visual.distance, 2)} m a lökhárítótól · ${channel.target?.label || channel.target?.id || "testfelület"}` : "nincs visszatérés ebben a csatornában"}${fresh ? "" : " · elavult megfigyelés"}`);
      for (let band = 0; band < 3; band++) {
        const active = visual.observed && band >= visual.activeBand;
        node("path", {d: bandPath(cx, cy, 67 + band * 15, 77 + band * 15, bearing, half), fill: active ? visual.color : "#263844", stroke: active ? visual.color : "#3b515f", "stroke-width": .65, opacity: active ? (band === visual.activeBand ? .96 : .28) : .55}, group);
      }
      const angle = bearing * Math.PI / 180, label = point(cx + Math.sin(angle) * 119, cy - Math.cos(angle) * 119);
      node("text", {x: label.x, y: label.y + 3, "text-anchor": "middle", fill: visual.observed ? visual.color : "#6e8797", "font-size": 10, "font-weight": visual.observed ? 650 : 400}, group, visual.text);
      const origin = point(cx + Math.sin(angle) * 57, cy - Math.cos(angle) * 57);
      node("circle", {cx: origin.x, cy: origin.y, r: 2, fill: visual.observed ? visual.color : "#678697"}, group);
      if (isSelected) {
        node("path", {d: bandPath(cx, cy, 64, 110, bearing, half + 2), fill: "none", stroke: "#a3e8f1", "stroke-width": 2, opacity: .95}, group);
        node("circle", {cx: origin.x, cy: origin.y, r: 4.5, fill: "none", stroke: "#a3e8f1", "stroke-width": 1.4}, group);
      }
    }
    carTop(svg, cx, cy, fresh);
    node("path", {d: "M170 89 L167 94 H173 Z", fill: "#dce9ed"}, svg);
    node("text", {x: 170, y: 215, "text-anchor": "middle", fill: "#b5c9d5", "font-size": 9, "font-weight": 600}, svg, carId);
    const detected = channels.filter(channel => channelVisual(channel, fresh, proximity.thresholds_m).observed);
    const nearest = [...detected].sort((a, b) => Number(a.distance_m) - Number(b.distance_m))[0];
    node("rect", {x: 15, y: 282, width: 310, height: 21, rx: 5, fill: "#1d2e3b"}, svg);
    node("text", {x: 170, y: 296, "text-anchor": "middle", fill: fresh ? "#bdccd5" : "#d5b783", "font-size": 9}, svg, !fresh ? "Elavult észlelés · a színek nem jelentenek friss állapotot" : nearest ? `Legközelebb: ${nearest.label || nearest.id} · ${f(nearest.distance_m, 2)} m` : `${channels.length} virtuális csatorna · ${f(proximity.range_m, 2)} m · nincs visszatérés`);
    const thresholds = proximity.thresholds_m || {close: .55, warning: 1.2};
    const legend = [{x: 23, color: "#ff5b61", label: `≤ ${f(thresholds.close, 2)} m`}, {x: 111, color: "#f9bb54", label: `≤ ${f(thresholds.warning, 2)} m`}, {x: 201, color: "#65d6a4", label: `≤ ${f(proximity.range_m, 2)} m`}, {x: 284, color: "#526473", label: "—"}];
    for (const item of legend) {
      node("circle", {cx: item.x, cy: 320, r: 3, fill: fresh ? item.color : "#687481"}, svg);
      node("text", {x: item.x + 7, y: 323, fill: "#96acba", "font-size": 8}, svg, item.label);
    }
    return detected.length;
  }
  function projectGround(right, forward, height = 0) {
    const denominator = Math.max(1.3, forward + 3.5);
    return point(255 + 220 * right / denominator, 83 + 220 * (3 - height) / denominator);
  }
  function proximityFrontHits(sample) {
    return proximityChannels(sample).map(channel => ({channel, relative: relativeObservation(sample, channel.hit_world_m, frontReference(sample))})).filter(hit => channelVisual(hit.channel, true, sample.proximity.thresholds_m).observed && hit.relative && hit.relative.forward > .2 && Math.abs(hit.relative.bearing) <= 45);
  }
  function drawProximityHit(svg, hit, sample, fresh, selected = null) {
    const visual = channelVisual(hit.channel, fresh, sample.proximity.thresholds_m), p = projectGround(hit.relative.right, hit.relative.forward, .025), origin = relativeObservation(sample, hit.channel.origin_world_m, frontReference(sample)), group = node("g", {}, svg);
    node("title", {}, group, `${hit.channel === selected ? "Kijelölt csatorna · " : ""}Első felületi találat · ${hit.channel.label || hit.channel.id} · ${f(hit.channel.distance_m, 2)} m a lökhárítótól · ${hit.channel.target?.label || hit.channel.target?.id || "felület"}${fresh ? "" : " · elavult minta"}. A pontból nem állítjuk vissza az akadály teljes alakját.`);
    if (origin && origin.forward > .2) {
      const a = projectGround(origin.right, origin.forward, .025);
      node("line", {x1: a.x, y1: a.y, x2: p.x, y2: p.y, stroke: visual.color, "stroke-width": .8, "stroke-dasharray": "2 3", opacity: .48}, group);
    }
    node("circle", {cx: p.x, cy: p.y, r: 6.5, fill: "none", stroke: visual.color, "stroke-width": 1.1, opacity: .75}, group);
    node("circle", {cx: p.x, cy: p.y, r: 2.7, fill: visual.color, stroke: "#163041", "stroke-width": 1}, group);
    if (hit.channel === selected) node("circle", {cx: p.x, cy: p.y, r: 10, fill: "none", stroke: "#a3e8f1", "stroke-width": 2}, group);
  }
  function vehicleFootprint(relative, item, sample, length = 1, width = 1, shift = 0) {
    const theta = valid(item.heading_deg) ? (Number(item.heading_deg) - frontHeading(sample)) * Math.PI / 180 : 0;
    const halfLength = number(item.length_m, 4.5) * length / 2, halfWidth = number(item.width_m, 1.9) * width / 2;
    return [[halfLength + shift, -halfWidth], [halfLength + shift, halfWidth], [-halfLength + shift, halfWidth], [-halfLength + shift, -halfWidth]].map(([forward, right]) => ({forward: relative.forward + Math.cos(theta) * forward - Math.sin(theta) * right, right: relative.right + Math.sin(theta) * forward + Math.cos(theta) * right}));
  }
  function drawVehicle(svg, item, relative, sample, fresh) {
    const group = node("g", {}, svg), points = vehicleFootprint(relative, item, sample), project = (points, height) => points.map(p => projectGround(p.right, p.forward, height));
    node("title", {}, group, `${item.id} · fogadott virtuális tárgy: ${f(item.distance_m)} m; ${f(item.speed_m_s)} m/s${valid(item.heading_deg) ? `; mintavételkori modellirány ${f(item.heading_deg, 0)}°` : "; az alak jelképes, irányadat nem volt mentve"}. A karosszéria geometriai modell, nem kamerafelismerés.`);
    const base = project(points, .17), belt = project(points, .81), shadow = project(vehicleFootprint(relative, item, sample, 1.08, 1.12), 0);
    node("path", {d: path(shadow.map(p => point(p.x, p.y + 2))), fill: "#101f2b", opacity: .24}, group);
    const faces = points.map((p, i) => ({i, depth: (p.forward + points[(i + 1) % 4].forward) / 2})).sort((a, b) => b.depth - a.depth);
    const colors = fresh ? ["#658fa4", "#456d83", "#507a91", "#739bb0"] : ["#9c978b", "#797567", "#8b8577", "#a9a394"];
    for (const {i} of faces) {
      const next = (i + 1) % 4;
      node("path", {d: path([base[i], base[next], belt[next], belt[i]]), fill: colors[i], stroke: fresh ? "#b2cbd7" : "#c5beb1", "stroke-width": .7}, group);
      const wheelA = point(base[i].x * .78 + base[next].x * .22, base[i].y * .78 + base[next].y * .22), wheelB = point(base[i].x * .22 + base[next].x * .78, base[i].y * .22 + base[next].y * .78);
      if (i === 1 || i === 3) for (const wheel of [wheelA, wheelB]) node("circle", {cx: wheel.x, cy: wheel.y, r: clamp(17 / (relative.forward + 3), 1.5, 5), fill: "#1b2933", stroke: "#7c919d", "stroke-width": .7}, group);
    }
    node("path", {d: path(belt), fill: fresh ? "#96b3c3" : "#b0aa9c", stroke: "#d4e2e9", "stroke-width": .8}, group);
    const cabinPoints = vehicleFootprint(relative, item, sample, .46, .79, -.08), roof = project(cabinPoints, 1.48), cabinBottom = project(vehicleFootprint(relative, item, sample, .67, .94, -.08), .81);
    for (const {i} of faces) {
      const next = (i + 1) % 4;
      node("path", {d: path([cabinBottom[i], cabinBottom[next], roof[next], roof[i]]), fill: fresh ? "#294554" : "#555649", stroke: "#a7c6d5", "stroke-width": .8}, group);
      const reflection = [point(cabinBottom[i].x * .6 + roof[i].x * .4, cabinBottom[i].y * .6 + roof[i].y * .4), point(cabinBottom[next].x * .6 + roof[next].x * .4, cabinBottom[next].y * .6 + roof[next].y * .4)];
      node("line", {x1: reflection[0].x, y1: reflection[0].y, x2: reflection[1].x, y2: reflection[1].y, stroke: "#88aebd", "stroke-width": .8, opacity: .5}, group);
    }
    node("path", {d: path(roof), fill: fresh ? "#b0c9d5" : "#c5bca9", stroke: "#e6eef2", "stroke-width": .8}, group);
    const imagePoints = [...base, ...roof], minX = Math.min(...imagePoints.map(p => p.x)), maxX = Math.max(...imagePoints.map(p => p.x)), minY = Math.min(...imagePoints.map(p => p.y)), maxY = Math.max(...imagePoints.map(p => p.y));
    const left = clamp(minX - 5, 8, 492), right = clamp(maxX + 5, 18, 502), top = clamp(minY - 5, 75, 235), bottom = clamp(maxY + 5, 85, 250), bracket = fresh ? "#69cac6" : "#b79b6b";
    if (right > left && bottom > top) node("path", {d: `M${left},${top + 8} V${top} H${left + 8} M${right - 8},${top} H${right} V${top + 8} M${left},${bottom - 8} V${bottom} H${left + 8} M${right - 8},${bottom} H${right} V${bottom - 8}`, fill: "none", stroke: bracket, "stroke-width": 1.1}, group);
    const labelX = clamp((left + right) / 2, 57, 453), labelY = clamp(top - 8, 69, 231);
    node("rect", {x: labelX - 49, y: labelY - 13, width: 98, height: 18, rx: 4, fill: "#163041", opacity: .94}, group);
    node("text", {x: labelX, y: labelY, "text-anchor": "middle", fill: "#eff6f8", "font-size": 9, "font-weight": 600}, group, `${item.id} · ${f(item.distance_m)} m`);
  }
  function drawCrossing(svg, item, relative, fresh) {
    const clear = item.pedestrian_state === "no_pedestrian_observed", active = item.pedestrian_state === "crossing", color = !fresh ? "#b49a73" : clear ? "#77aa99" : active ? "#da7268" : "#d49e4c";
    const group = node("g", {}, svg), width = 2.4, depth = .7;
    node("title", {}, group, `${item.id} · ismert térképi zebrahelyhez társított állapot: ${active ? "gyalogos átkel" : clear ? "gyalogos nincs észlelve" : "gyalogos vár"}; nincs mentett gyalogos-koordináta.`);
    node("path", {d: path([projectGround(relative.right - width, relative.forward - depth), projectGround(relative.right + width, relative.forward - depth), projectGround(relative.right + width, relative.forward + depth), projectGround(relative.right - width, relative.forward + depth)]), fill: color, opacity: .21}, group);
    for (let stripe = -2; stripe <= 2; stripe++) {
      const r = relative.right + stripe * .85;
      node("path", {d: path([projectGround(r - .21, relative.forward - depth), projectGround(r + .21, relative.forward - depth), projectGround(r + .21, relative.forward + depth), projectGround(r - .21, relative.forward + depth)]), fill: "#e5f0f1", stroke: color, "stroke-width": .5, opacity: .83}, group);
    }
    const p = projectGround(relative.right, relative.forward), x = relative.right < 0 ? 75 : 435, y = clamp(p.y + 18, 95, 239);
    node("line", {x1: p.x, y1: p.y, x2: x + (relative.right < 0 ? 65 : -65), y2: y - 4, stroke: color, "stroke-width": .8, "stroke-dasharray": "3 3", opacity: .65}, group);
    node("rect", {x: x - 65, y: y - 13, width: 130, height: 18, rx: 4, fill: "#1a3444", opacity: .94}, group);
    node("circle", {cx: x - 54, cy: y - 4, r: 3, fill: color}, group);
    node("text", {x: x + 4, y, "text-anchor": "middle", fill: "#edf5f7", "font-size": 9}, group, `${item.id} · ${active ? "átkelés" : clear ? "nincs észlelés" : "várakozás"}`);
  }
  function drawFrontView(svg, sample, fresh, carId, options = {}) {
    reset(svg, sample, fresh);
    node("rect", {width: 510, height: 300, rx: 14, fill: "#dbe7ec"}, svg);
    node("rect", {x: 0, y: 0, width: 510, height: 48, fill: "#e8f0f3"}, svg);
    node("rect", {x: 0, y: 48, width: 510, height: 35, fill: "#d6e3e9"}, svg);
    node("rect", {x: 0, y: 83, width: 510, height: 217, fill: "#748a95"}, svg);
    node("path", {d: "M0,84 H510 V118 H0 Z", fill: "#9aadb6"}, svg);
    node("path", {d: "M0,118 H510 V166 H0 Z", fill: "#899fa9"}, svg);
    node("path", {d: "M0,166 H510 V216 H0 Z", fill: "#7b929e"}, svg);
    node("line", {x1: 0, y1: 83, x2: 510, y2: 83, stroke: "#bdced6", "stroke-width": 1.1}, svg);
    node("path", {d: path([projectGround(-3.3, 0), projectGround(3.3, 0), projectGround(3.3, 28), projectGround(-3.3, 28)]), fill: "#647e8a", opacity: .68}, svg);
    for (const distance of [2, 4, 8, 12, 18]) {
      const a = projectGround(-12, distance), b = projectGround(12, distance);
      node("line", {x1: clamp(a.x, 0, 510), y1: a.y, x2: clamp(b.x, 0, 510), y2: b.y, stroke: "#c3d1d7", "stroke-width": .65, opacity: .22}, svg);
      node("text", {x: 11, y: a.y - 5, fill: "#d5e2e8", "font-size": 8}, svg, `${distance} m`);
    }
    for (const right of [-9, -6, -3, 0, 3, 6, 9]) {
      const a = projectGround(right, 0), b = projectGround(right, 28);
      node("line", {x1: a.x, y1: a.y, x2: b.x, y2: b.y, stroke: "#d0e0e5", "stroke-width": .7, opacity: .12}, svg);
    }
    for (const right of [-3.3, 3.3]) {
      const a = projectGround(right, 0), b = projectGround(right, 28);
      node("line", {x1: a.x, y1: a.y, x2: b.x, y2: b.y, stroke: "#d5e9e9", "stroke-width": 1.5, opacity: .45}, svg);
    }
    const guidance = sample.guidance || {};
    if (guidance.available && valid(guidance.heading_error_deg)) {
      const angle = (valid(guidance.route_heading_deg) ? Number(guidance.route_heading_deg) - frontHeading(sample) : -Number(guidance.heading_error_deg)) * Math.PI / 180;
      if (Math.abs(angle) < Math.PI / 3) for (const start of [1, 4, 7, 10, 13, 16, 19, 22]) {
        const a = projectGround(Math.sin(angle) * start, Math.cos(angle) * start), b = projectGround(Math.sin(angle) * (start + 1.5), Math.cos(angle) * (start + 1.5));
        node("line", {x1: a.x, y1: a.y, x2: b.x, y2: b.y, stroke: fresh ? "#8de1d5" : "#d5ba8c", "stroke-width": Math.max(1.2, 8 / (start + 3)), opacity: .75, "stroke-linecap": "round"}, svg);
      }
    }
    const observations = [...(sample.environment?.vehicles || []).map(item => ({item, kind: "vehicle"})), ...(sample.environment?.crossings || []).map(item => ({item, kind: "crossing"}))].map(o => ({...o, relative: relativeObservation(sample, o.item, frontReference(sample))})).filter(o => o.relative && o.relative.forward > .2 && Math.abs(o.relative.bearing) <= 45).sort((a, b) => b.relative.forward - a.relative.forward);
    for (const observation of observations) {
      if (observation.kind === "vehicle") drawVehicle(svg, observation.item, observation.relative, sample, fresh);
      else drawCrossing(svg, observation.item, observation.relative, fresh);
    }
    const hits = proximityFrontHits(sample), selected = selectedChannel(sample, options);
    for (const hit of hits) drawProximityHit(svg, hit, sample, fresh, selected);
    node("rect", {x: 12, y: 13, width: 178, height: 30, rx: 7, fill: "#193545", opacity: .94}, svg);
    node("circle", {cx: 25, cy: 28, r: 3, fill: fresh ? "#6bd1b3" : "#dbbb87"}, svg);
    node("text", {x: 35, y: 26, fill: "#eff5f8", "font-size": 10, "font-weight": 650}, svg, `${carId} · ${fresh ? "fogadott észlelések" : "utolsó észlelések"}`);
    node("text", {x: 35, y: 37, fill: "#a5c0ce", "font-size": 8}, svg, `90° vetület · ${f(sample.sample_sim_time_s, 2)} s`);
    node("text", {x: 498, y: 28, "text-anchor": "end", fill: "#4e6e7e", "font-size": 9, "font-weight": 600}, svg, "SZINTETIKUS NÉZET");
    node("text", {x: 498, y: 42, "text-anchor": "end", fill: "#6c8794", "font-size": 8}, svg, "Kamerakép nem készül");
    if (selected) {
      const visible = hits.some(hit => hit.channel === selected), visual = channelVisual(selected, fresh, sample.proximity.thresholds_m);
      const detail = visible ? `${f(visual.distance, 2)} m · ${selected.target?.label || selected.target?.id || "felület"}` : !visual.observed ? "nincs felületi találat" : !relativeObservation(sample, selected.hit_world_m, frontReference(sample)) ? "nincs mentett felületi pont" : "az előrenézeten kívül";
      const selectionText = `Kijelölt: ${selected.label || selected.id} · ${detail}${fresh ? "" : " · utolsó minta"}`;
      const group = node("g", {}, svg);
      node("title", {}, group, selectionText);
      node("rect", {x: 12, y: 54, width: 486, height: 19, rx: 5, fill: "#193545", opacity: .94}, group);
      node("text", {x: 20, y: 67, fill: "#a3e8f1", "font-size": 9}, group, selectionText.length > 85 ? selectionText.slice(0, 84) + "…" : selectionText);
    }
    if (!observations.length && !hits.length) {
      node("rect", {x: 126, y: 137, width: 258, height: 25, rx: 7, fill: "#223d4c", opacity: .72}, svg);
      node("text", {x: 255, y: 153, "text-anchor": "middle", fill: "#dbe8ec", "font-size": 10}, svg, "A mintában nincs előretekintő megfigyelés");
    }
    node("path", {d: "M0,278 Q74,258 143,268 Q255,250 366,268 Q437,258 510,278 V300 H0 Z", fill: "#192e3c"}, svg);
    node("path", {d: "M112,288 Q255,263 398,288", fill: "none", stroke: "#4c6675", "stroke-width": 2, "stroke-linecap": "round"}, svg);
    node("text", {x: 15, y: 288, fill: "#9db7c5", "font-size": 8}, svg, `${observations.length} tárgy · ${hits.length} első felületi találat`);
    if (hits.length) {
      node("circle", {cx: 18, cy: 249, r: 3, fill: fresh ? "#78c8b2" : "#b69869", stroke: "#163041", "stroke-width": 1}, svg);
      node("text", {x: 26, y: 252, fill: "#e8f1f3", "font-size": 8, "paint-order": "stroke", stroke: "#6a8390", "stroke-width": 2}, svg, "Körjel: első felületi találat · szín: mért közelség");
    }
    node("text", {x: 495, y: 288, "text-anchor": "end", fill: fresh ? "#9db7c5" : "#dbc190", "font-size": 8}, svg, fresh ? "Segédsík ≠ felismert út" : "ELAVULT / UTOLSÓ MINTA");
    return observations.length;
  }
  global.YardPerception = Object.freeze({drawRadar, drawFrontView, relativeObservation, channelVisual, proximityChannels, proximityFrontHits, projectGround, frontReference, frontHeading});
})(globalThis);
