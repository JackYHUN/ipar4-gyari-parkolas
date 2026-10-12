"""Observed fleet incidents, independent of MQTT actuation and model time.

One evaluation belongs to one persisted transport observation. Unknown data
breaks confirmation streaks and cannot establish recovery. Acknowledgment is a
human bookkeeping action; only subsequent observed healthy samples resolve a
fault. This monitor makes no physical safety or standard compliance claim.
"""
import math
import re


THRESHOLDS = dict(startup_grace_s=5, communication_timeout_s=2,
    processing_timeout_s=2, fresh_snapshot_s=.75, fault_observations=2,
    healthy_observations=2, observation_gap_s=2.5, observation_interval_s=1)

RULES = {
    "communication_unavailable": dict(title="A szimulátor MQTT-kapcsolata nem elérhető", severity="error",
        advice="Ellenőrizze a broker és a szimulátor naplóját, valamint a kommunikációs hibainjektálást. A nyugtázás nem állítja helyre a kapcsolatot."),
    "processing_lag": dict(title="A kijelzett állapot feldolgozása lemaradt", severity="warning",
        advice="Friss MQTT-állapot érkezik, de a feldolgozás késik. Ellenőrizze a fogadási sort; szükség esetén kérjen szünetet vagy 1× sebességet."),
    "sensor_unavailable": dict(title="A jármű virtuális érzékelőmintája nem használható", severity="error",
        advice="Ellenőrizze a mentett érzékelőmintát és a hibainjektálást. A nyugtázás nem oldja fel a jármű érzékelő miatti megállását."),
    "worker_stopped": dict(title="Egy háttérfolyamat leállt", severity="error",
        advice="Ellenőrizze a megadott folyamat naplóját. A leállás ebben a futásban fennmaradó incidens; új futás indítása szükséges."),
}


class OperationsConflict(ValueError):
    """The named run or lifecycle revision changed before acknowledgment."""


def finite_age(value):
    return float(value) if (isinstance(value, (int, float)) and not isinstance(value, bool)
        and math.isfinite(value) and value >= 0) else None


def evidence(sample):
    return dict(transport_sample_id=sample["id"], telemetry_until_id=sample["telemetry_until_id"],
        events_until_id=sample["events_until_id"], sim_time=sample["sim_time"], utc=sample["utc"])


class OperationsMonitor:
    def __init__(self):
        self.reset()

    def reset(self):
        self._last_ns = self._origin_ns = self._sample_id = self._segment = None
        self._streaks, self._active = {}, {}
        self._run_id = None
        self._known_rules = 0
        self._observation_utc = None
        self._quality = {rule: "unknown" for rule in RULES}

    def summary(self, now_ns=None):
        fresh = (isinstance(now_ns, int) and not isinstance(now_ns, bool) and self._last_ns is not None
            and 0 <= now_ns - self._last_ns <= THRESHOLDS["observation_gap_s"] * 1e9)
        return dict(known_rules=self._known_rules, total_rules=len(RULES),
            observation_utc=self._observation_utc, observation_fresh=fresh, quality=dict(self._quality))

    def _classify(self, now_ns, transport, snapshot, processed_age_s, workers):
        callback = finite_age(transport.get("last_snapshot_age_s"))
        processed = finite_age(processed_age_s)
        broker, fault = transport.get("broker_connected"), transport.get("communication_fault_active")
        measurements = dict(broker_connected=broker if isinstance(broker, bool) else None,
            communication_fault_active=fault if isinstance(fault, bool) else None,
            callback_snapshot_age_s=callback, processed_snapshot_age_s=processed,
            snapshot_received=transport.get("snapshot_received") if isinstance(transport.get("snapshot_received"), bool) else None)
        grace = (now_ns - self._origin_ns) / 1e9 < THRESHOLDS["startup_grace_s"]
        communication = None
        if not grace:
            if broker is False or fault is True or transport.get("snapshot_received") is False or callback is not None and callback > THRESHOLDS["communication_timeout_s"]:
                communication = True
            elif broker is True and fault is False and callback is not None:
                communication = False
        fresh_callback = broker is True and fault is False and callback is not None and callback <= THRESHOLDS["fresh_snapshot_s"]
        lag = processed > THRESHOLDS["processing_timeout_s"] if fresh_callback and processed is not None else None
        observations = [("communication_unavailable", "fleet", None, communication, measurements),
                        ("processing_lag", "fleet", None, lag, measurements)]
        fresh_processed = processed is not None and processed <= THRESHOLDS["fresh_snapshot_s"] and bool(snapshot)
        cars = snapshot.get("cars") if fresh_processed else None
        sensor_known = isinstance(cars, list)
        present = set()
        if isinstance(cars, list):
            for car in cars:
                if not isinstance(car, dict) or not isinstance(car.get("id"), str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", car["id"]):
                    sensor_known = False
                    continue
                car_id = car["id"]
                present.add(car_id)
                sensors = car.get("sensors")
                status = False if car.get("state") == "parked" else None
                if status is None and isinstance(sensors, dict) and sensors.get("sampled") is True:
                    if sensors.get("fresh") is False or sensors.get("fault_active") is True:
                        status = True
                    elif sensors.get("fresh") is True and sensors.get("fault_active") is False:
                        status = False
                if status is None:
                    sensor_known = False
                def flag(name):
                    value = sensors.get(name) if isinstance(sensors, dict) else None
                    return value if isinstance(value, bool) else None
                observed = dict(state=str(car.get("state", ""))[:64], sampled=flag("sampled"),
                    fresh=flag("fresh"), fault_active=flag("fault_active"),
                    sensor_age_s=finite_age(sensors.get("age_s")) if isinstance(sensors, dict) else None,
                    processed_snapshot_age_s=processed)
                observations.append(("sensor_unavailable", car_id, car_id, status, observed))
        # Missing cars are unknown, even in an otherwise fresh snapshot. Their
        # removal is not proof that a formerly failed sensor has recovered.
        for key, item in self._active.items():
            if item["rule"] == "sensor_unavailable" and item["car_id"] not in present:
                observations.append(("sensor_unavailable", item["car_id"], item["car_id"], None,
                                     dict(processed_snapshot_age_s=processed)))
                sensor_known = False
        for worker in workers:
            if isinstance(worker, dict) and worker.get("exit_code") is not None:
                pid = worker.get("pid")
                observations.append(("worker_stopped", "worker:" + str(pid), None, True,
                    dict(worker_pid=pid, exit_code=worker["exit_code"])))
        sensor_fault = any(rule == "sensor_unavailable" and value is True for rule, _, _, value, _ in observations)
        worker_fault = any(rule == "worker_stopped" for rule, _, _, _, _ in observations) or any(item["rule"] == "worker_stopped" for item in self._active.values())
        values = dict(communication_unavailable=communication, processing_lag=lag,
            sensor_unavailable=True if sensor_fault else False if sensor_known else None,
            worker_stopped=worker_fault)
        self._quality = {rule: "unknown" if value is None else "fault" if value else "healthy" for rule, value in values.items()}
        return observations, sum(value is not None for value in values.values())

    def observe(self, journal, *, now_ns, sample, transport, snapshot, processed_age_s, workers=()):
        if not isinstance(now_ns, int) or isinstance(now_ns, bool) or now_ns < 0:
            return False
        if self._run_id != journal.run_id:
            self.reset()
            self._run_id = journal.run_id
            self._active = journal.operations_active()
        if self._sample_id is not None and sample["id"] <= self._sample_id:
            return False
        segment = sample["segment"]
        if self._origin_ns is None or segment != self._segment or (self._last_ns is not None and now_ns <= self._last_ns):
            self._origin_ns = now_ns
            self._streaks.clear()
        elif now_ns - self._last_ns > THRESHOLDS["observation_gap_s"] * 1e9:
            self._streaks.clear()
        self._last_ns, self._sample_id, self._segment = now_ns, sample["id"], segment
        observations, self._known_rules = self._classify(now_ns, transport, snapshot, processed_age_s, workers)
        self._observation_utc = sample["utc"]
        observed_keys = set()
        for rule, subject, car_id, fault, measurements in observations:
            key = rule + ":" + subject
            observed_keys.add(key)
            item = self._active.get(key)
            if fault is None:
                self._streaks.pop(key, None)
                continue
            prior = self._streaks.get(key)
            streak = dict(fault=fault, count=min(2, prior["count"] + 1) if prior and prior["fault"] == fault else 1,
                          first=prior["first"] if prior and prior["fault"] == fault else evidence(sample))
            self._streaks[key] = streak
            if fault and item is None and (rule == "worker_stopped" or streak["count"] >= THRESHOLDS["fault_observations"]):
                item = dict(rule=rule, subject=subject, car_id=car_id, **RULES[rule], active=True, acknowledged=False,
                    opened_utc=sample["utc"], last_seen_utc=sample["utc"], resolved_utc=None, acknowledged_utc=None,
                    note=None, revision=1, opening=streak["first"], latest=evidence(sample), measurements=measurements)
                self._active[key] = journal.save_operation(key, item, transition="opened")
            elif item is not None:
                item = dict(item, last_seen_utc=sample["utc"], latest=evidence(sample), measurements=measurements)
                if not fault and rule != "worker_stopped" and streak["count"] >= THRESHOLDS["healthy_observations"]:
                    item.update(active=False, resolved_utc=sample["utc"], revision=item["revision"] + 1)
                    journal.save_operation(key, item, transition="resolved")
                    del self._active[key]
                else:
                    self._active[key] = journal.save_operation(key, item)
        # A missing subject cannot continue a streak from an earlier sample.
        self._streaks = {key: value for key, value in self._streaks.items() if key in observed_keys}
        journal.save_operations_state(self.summary(now_ns))
        return True

    def acknowledged(self, item):
        key = item["rule"] + ":" + item["subject"]
        if key in self._active:
            self._active[key] = item
