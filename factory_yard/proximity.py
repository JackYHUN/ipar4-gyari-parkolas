"""Deterministic bumper-mounted parking sensor geometry.

These are finite fan rays against supplied rectangular simulation bodies, not
radar echoes, ultrasound propagation or a certified park-assist model. Distances
start at each sensor's mounting point on the ego body's bumper/side. Headings
follow the yard's SVG convention: clockwise from +x; positive local bearing is
right. A missing return establishes nothing outside the tested rays/range.
"""
from __future__ import annotations

import math
from typing import Any


def _point(x: float, y: float) -> dict[str, float]:
    return {"x_m": round(x, 6), "y_m": round(y, 6)}


def ray_rectangle_distance(origin: tuple[float, float], direction: tuple[float, float],
                           body: dict, max_range: float) -> float | None:
    """First nonnegative ray hit on an oriented rectangle, including overlap.

    The caller supplies a unit direction. The slab intersection treats edge
    contacts as returns and an origin inside a target as zero clearance.
    """
    angle = math.radians(body.get("heading_deg", 0.0))
    c, s = math.cos(angle), math.sin(angle)
    dx, dy = origin[0] - body["x_m"], origin[1] - body["y_m"]
    local_origin = (dx * c + dy * s, -dx * s + dy * c)
    local_direction = (direction[0] * c + direction[1] * s,
                       -direction[0] * s + direction[1] * c)
    low, high = 0.0, max_range
    for coordinate, velocity, half_size in zip(local_origin, local_direction,
                                               (body["length_m"] / 2, body["width_m"] / 2)):
        if abs(velocity) < 1e-12:
            if coordinate < -half_size - 1e-12 or coordinate > half_size + 1e-12:
                return None
            continue
        a, b = (-half_size - coordinate) / velocity, (half_size - coordinate) / velocity
        low, high = max(low, min(a, b)), min(high, max(a, b))
        if low > high + 1e-12:
            return None
    return max(0.0, min(max_range, low)) if high >= -1e-12 else None


def _surface_distance(point: tuple[float, float], body: dict) -> float:
    angle = math.radians(body.get("heading_deg", 0.0))
    c, s = math.cos(angle), math.sin(angle)
    x, y = point[0] - body["x_m"], point[1] - body["y_m"]
    return math.hypot(max(0.0, abs(x * c + y * s) - body["length_m"] / 2),
                      max(0.0, abs(-x * s + y * c) - body["width_m"] / 2))


class ParkingSensorModel:
    """Eight independently mounted virtual short-range sensor channels."""

    def __init__(self, range_m: float = 3.0, close_m: float = .55, warning_m: float = 1.2,
                 beam_half_angle_deg: float = 20.0, rays_per_channel: int = 9):
        values = (range_m, close_m, warning_m, beam_half_angle_deg)
        if not all(math.isfinite(float(value)) for value in values):
            raise ValueError("A virtuális parkolószenzor paraméterei véges számok legyenek")
        if not 0 <= close_m < warning_m < range_m:
            raise ValueError("Parkolószenzor: 0 ≤ közeli < figyelmeztető < hatótáv szükséges")
        if not 0 < beam_half_angle_deg <= 45:
            raise ValueError("Parkolószenzor: a sugárnyaláb fél látószöge 0–45 fok között legyen")
        if isinstance(rays_per_channel, bool) or not isinstance(rays_per_channel, int) or not 3 <= rays_per_channel <= 33 or rays_per_channel % 2 == 0:
            raise ValueError("Parkolószenzor: 3–33 közötti páratlan sugárszám szükséges")
        self.range_m, self.close_m, self.warning_m = float(range_m), float(close_m), float(warning_m)
        self.beam_half_angle_deg, self.rays_per_channel = float(beam_half_angle_deg), rays_per_channel
        self.offsets = [self.beam_half_angle_deg * (2 * index / (rays_per_channel - 1) - 1)
                        for index in range(rays_per_channel)]

    def sample(self, ego: dict, bodies: list[dict], sample_time: float) -> dict[str, Any]:
        """Sample supplied bodies without random draws or simulation mutation.

        A conservative centre/surface broad phase excludes only objects no ray
        can reach. Nearest hit per ray handles geometric occlusion along that
        tested ray. Finite ray spacing still leaves unobserved angular gaps.
        """
        half_l, half_w = ego["length_m"] / 2, ego["width_m"] / 2
        layouts = (
            ("front_left", "Elöl bal", -35., half_l, -half_w * .72),
            ("front_center", "Elöl közép", 0., half_l, 0.),
            ("front_right", "Elöl jobb", 35., half_l, half_w * .72),
            ("rear_left", "Hátul bal", -145., -half_l, -half_w * .72),
            ("rear_center", "Hátul közép", 180., -half_l, 0.),
            ("rear_right", "Hátul jobb", 145., -half_l, half_w * .72),
            ("left", "Bal oldal", -90., 0., -half_w),
            ("right", "Jobb oldal", 90., 0., half_w),
        )
        heading = math.radians(ego.get("heading_deg", 0.0))
        c, s = math.cos(heading), math.sin(heading)
        centre = ego["x_m"], ego["y_m"]
        envelope = self.range_m + math.hypot(half_l, half_w)
        candidates = []
        for body in sorted(bodies, key=lambda item: str(item["id"])):
            if body["id"] == ego["id"]:
                continue
            half_diagonal = math.hypot(body["length_m"], body["width_m"]) / 2
            if math.hypot(body["x_m"] - centre[0], body["y_m"] - centre[1]) > envelope + half_diagonal:
                continue
            if _surface_distance(centre, body) <= envelope + 1e-9:
                candidates.append(body)
        channels = []
        for channel_id, label, bearing, forward, right in layouts:
            origin = centre[0] + forward * c - right * s, centre[1] + forward * s + right * c
            nearest, nearest_body, hit_bearing = None, None, None
            for offset in self.offsets:
                local_bearing = bearing + offset
                angle = heading + math.radians(local_bearing)
                direction = math.cos(angle), math.sin(angle)
                for body in candidates:
                    distance = ray_rectangle_distance(origin, direction, body, self.range_m)
                    if distance is not None and (nearest is None or distance < nearest - 1e-9):
                        nearest, nearest_body, hit_bearing = distance, body, local_bearing
            endpoints = []
            for offset in (-self.beam_half_angle_deg, self.beam_half_angle_deg):
                angle = heading + math.radians(bearing + offset)
                endpoints.append(_point(origin[0] + self.range_m * math.cos(angle),
                                        origin[1] + self.range_m * math.sin(angle)))
            hit = None
            if nearest is not None:
                angle = heading + math.radians(hit_bearing)
                hit = _point(origin[0] + nearest * math.cos(angle), origin[1] + nearest * math.sin(angle))
            reported_distance = round(nearest, 6) if nearest is not None else None
            # Classify the same quantised value that is recorded/displayed;
            # binary floating-point noise must not turn 1.200000 m green.
            status = ("no_return" if reported_distance is None else "close" if reported_distance <= self.close_m
                      else "warning" if reported_distance <= self.warning_m else "clear")
            channels.append({
                "id": channel_id, "label": label, "bearing_deg": bearing,
                "origin_local_m": {"forward": round(forward, 6), "right": round(right, 6)},
                "origin_world_m": _point(*origin), "range_m": self.range_m,
                "distance_m": reported_distance,
                "status": status,
                "target": ({key: nearest_body.get(key) for key in ("id", "kind", "label", "source")}
                           if nearest_body else None),
                "hit_bearing_deg": hit_bearing, "hit_world_m": hit,
                "beam_endpoints_world_m": endpoints,
            })
        distances = [channel["distance_m"] for channel in channels if channel["distance_m"] is not None]
        return {
            "schema_version": 1, "source": "virtual_bumper_ray_fan",
            "model": "oriented_rectangle_first_return", "sample_sim_time_s": round(sample_time, 6),
            "sampled": True, "range_m": self.range_m,
            "beam_half_angle_deg": self.beam_half_angle_deg, "rays_per_channel": self.rays_per_channel,
            "thresholds_m": {"close": self.close_m, "warning": self.warning_m},
            "vehicle": {"length_m": ego["length_m"], "width_m": ego["width_m"]},
            "reference_pose": {**_point(*centre), "heading_deg": round(ego.get("heading_deg", 0.0), 6)},
            "channels": channels, "nearest_distance_m": min(distances) if distances else None,
            "assumptions": {
                "coordinate_frame": "SVG_world_clockwise_heading_local_forward_right",
                "distance_origin": "sensor_mount_to_first_rectangular_surface",
                "added_range_noise_m": 0.0, "range_resolution_m": .000001,
                "coordinate_resolution_m": .000001,
                "body_shape": "configured_oriented_rectangle",
                "threshold_source": "educational_model_not_manufacturer_specification",
                "control_input": False, "nearest_ray_occlusion": True,
                "continuous_beam_coverage": False, "finite_ray_gaps_unobserved": True,
                "no_return_meaning": "no_supplied_body_hit_on_tested_rays_within_range",
                "pedestrian_geometry_modelled": False, "reflectivity_modelled": False,
                "ultrasound_propagation_modelled": False, "world_boundaries_are_obstacles": False,
            },
        }
