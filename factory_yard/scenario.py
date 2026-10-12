"""Validate editable scenarios and compile directed, metric educational yard maps.

Hall positions follow a public Audi map; roads, distances and capacity are explicit
model assumptions. Preview uses this same compiler without touching a live run.
"""
import copy
import heapq
import math

SOURCE = "https://audi.hu/content/dam/audi-hu-site/hu/hu/news/2023/audi-hungaria-ny%C3%ADlt-nap/Programf%C3%BCzet.pdf"
PRESETS = [
    {"id": "gyor", "name": "Audi Hungaria · Győr", "description": "Hivatalos 2023-as térkép szerinti csarnokelrendezés; oktatási udvari úthálózat."},
    {"id": "training", "name": "Oktatási gyárudvar", "description": "Áttekinthető gyárudvar ugyanazzal az ellenőrizhető közlekedési modellel."},
]
LIMITS = {"car_count": [1, 30], "parking_count": [4, 96], "initial_car_count": [0, 30],
          "initial_occupied_count": [0, 96], "spawn_interval_s": [1, 120],
          "parking_dwell_s": [5, 600], "production_limit": [1, 1000]}


def _number(data, key, default, low, high, integer=False):
    value = data.get(key, default)
    if isinstance(value, bool):
        raise ValueError(f"{key}: szám szükséges")
    try:
        result = float(value)
    except (ValueError, TypeError):
        raise ValueError(f"{key}: szám szükséges") from None
    if not math.isfinite(result) or not low <= result <= high or (integer and result != int(result)):
        raise ValueError(f"{key}: {low}–{high} közötti {'egész ' if integer else ''}szám szükséges")
    return int(result) if integer else result


def _point(item):
    if not isinstance(item, dict):
        raise ValueError("Minden pozíció x és y mezőt tartalmazó objektum legyen")
    return (_number(item, "x", None, 0, 600), _number(item, "y", None, 0, 400))


def _hall(id, label, x, y, w, h, kind="production"):
    return dict(id=id, label=label, x=x, y=y, w=w, h=h, kind=kind)


def _geometry(preset):
    buildings = [
        _hall("G1", "G1 · Motorgyár", 25, 130, 65, 90),
        _hall("G10", "G10 · Motorgyár", 105, 130, 65, 90),
        _hall("G20", "G20 · Motorgyár", 185, 130, 60, 90),
        _hall("G7", "G7 · Központi raktár", 105, 85, 65, 35, "logistics"),
        _hall("G18", "G18 · Logisztika", 35, 235, 210, 40, "logistics"),
        _hall("G42", "G42 · Energiaközpont", 270, 125, 40, 45, "energy"),
        _hall("G40", "G40 · Szerszámgyár", 265, 255, 45, 55),
        _hall("G60", "G60 · Lakkozó", 355, 115, 65, 70),
        _hall("G70", "G70 · Összeszerelés", 430, 115, 55, 70),
        _hall("G80", "G80 · Végellenőrzés", 490, 115, 80, 70),
        _hall("G50", "G50 · Karosszériaüzem", 365, 245, 190, 50),
        _hall("G55", "G55 · Présüzem", 470, 300, 25, 35),
        _hall("G86", "G86 / G89 · Fejlesztés", 420, 78, 150, 25, "research"),
        _hall("PTC", "PTC · Projekt- és Oktató Központ", 235, 355, 85, 35, "research"),
    ] if preset == "gyor" else [
        _hall("ASSEMBLY", "Összeszerelés és végellenőrzés", 400, 115, 170, 70),
        _hall("PARTS", "Alkatrészraktár", 60, 95, 175, 85, "logistics"),
        _hall("QUALITY", "Minőségellenőrzés", 370, 260, 180, 60),
        _hall("POWER", "Energiaközpont", 265, 110, 45, 70, "energy"),
    ]
    nodes = {"EXIT80": (565, 205), "EXIT70": (565, 225),
             "ALT1": (350, 225), "ALT2": (350, 205), "MAIN": (330, 205),
             "N1": (330, 65), "N2": (595, 65), "N3": (595, 30), "N4": (340, 30),
             "S1": (330, 345), "S2": (595, 345), "S3": (595, 380), "S4": (340, 380)}
    connections = [("EXIT80", "MAIN"), ("EXIT70", "ALT1"), ("ALT1", "ALT2"), ("ALT2", "MAIN"),
                   ("MAIN", "N1"), ("N1", "N2"), ("N2", "N3"), ("N3", "N4"),
                   ("MAIN", "S1"), ("S1", "S2"), ("S2", "S3"), ("S3", "S4")]
    edges = [dict(id=f"E{i:02d}", **{"from": a, "to": b}, width=8) for i, (a, b) in enumerate(connections, 1)]
    roads = [dict(id=e["id"], points=[list(nodes[e["from"]]), list(nodes[e["to"]])], width=e["width"])
             for e in edges]
    zones = [dict(id="C1" if name == "MAIN" else "C-" + name, x=nodes[name][0], y=nodes[name][1], w=8, h=8)
             for name in ("MAIN", "ALT1", "ALT2", "N1", "N2", "N3", "S1", "S2", "S3")]
    areas = [dict(id="north", label="Felső készjármű-parkoló", x=340, y=5, w=250, h=50, aisle_y=30, entrance_node="N3"),
             dict(id="south", label="Alsó készjármű-parkoló", x=340, y=355, w=250, h=44, aisle_y=380, entrance_node="S3")]
    exits = []
    for id, label, y in (("EXIT80", "G80 · Végellenőrzési kijárat", 205), ("EXIT70", "G70 · Alternatív gyártási kijárat", 225)):
        positions = [dict(x=round(358 + 6.8*i, 4), y=y, heading=180) for i in range(30)]
        exits.append(dict(id=id, label=label, spawn=[565, y], initial_lane=[[565, y], [358, y]], initial_car_positions=positions))
    return buildings, nodes, edges, roads, zones, areas, exits


def shortest_path(nodes, edges, start, end):
    """Dijkstra on directed metric edges; deterministic ties use node identifiers."""
    graph = {node: [] for node in nodes}
    for edge in edges:
        a, b = edge["from"], edge["to"]
        graph[a].append((b, math.dist(nodes[a], nodes[b])))
    queue, seen = [(0, start, [start])], set()
    while queue:
        distance, node, route = heapq.heappop(queue)
        if node in seen:
            continue
        if node == end:
            return [list(nodes[key]) for key in route]
        seen.add(node)
        for next_node, length in graph[node]:
            if next_node not in seen:
                heapq.heappush(queue, (distance+length, next_node, route+[next_node]))
    raise ValueError("A parkoló nem érhető el a kiválasztott kijáratból")


def compile_scenario(base, requested=None):
    if requested is None:
        requested = {}
    if not isinstance(requested, dict):
        raise ValueError("A forgatókönyv objektum legyen")
    preset = requested.get("preset", "gyor")
    if preset not in {p["id"] for p in PRESETS}:
        raise ValueError("Ismeretlen térképsablon")
    params = dict(preset=preset)
    defaults = dict(car_count=6, parking_count=24, initial_car_count=3, initial_occupied_count=4,
                    spawn_interval_s=9, parking_dwell_s=45)
    for key, default in defaults.items():
        low, high = LIMITS[key]
        params[key] = _number(requested, key, default, low, high, integer=key.endswith("count"))
    if params["initial_car_count"] > params["car_count"]:
        raise ValueError("A kezdeti autók száma nem haladhatja meg az aktív autókorlátot")
    if params["initial_occupied_count"] > params["parking_count"]:
        raise ValueError("Több foglalt hely nem lehet, mint ahány parkolóhely van")
    params["production_limit"] = None if requested.get("production_limit") in (None, "") else _number(requested, "production_limit", None, 1, 1000, True)
    if params["production_limit"] is not None and params["production_limit"] < params["initial_car_count"]:
        raise ValueError("A gyártási darabszám legalább a kezdeti autók száma legyen")
    buildings, nodes, edges, roads, zones, areas, exits = _geometry(preset)
    exit_id, area_id = requested.get("production_exit_id", "EXIT80"), requested.get("parking_area_id", "north")
    exit = next((item for item in exits if item["id"] == exit_id), None)
    area = next((item for item in areas if item["id"] == area_id), None)
    if exit is None or area is None:
        raise ValueError("Ismeretlen gyártási kijárat vagy parkolóterület")
    params.update(production_exit_id=exit_id, parking_area_id=area_id)
    prefix = shortest_path(nodes, edges, exit_id, area["entrance_node"])
    slots = []
    for i in range(params["parking_count"]):
        slots.append(dict(id=f"P{i+1:02d}", x=345 + (i//2)*5, y=area["aisle_y"] + (-12 if i%2 == 0 else 12)))
    overrides = requested.get("slots", [])
    if not isinstance(overrides, list) or len(overrides) > params["parking_count"]:
        raise ValueError("Hibás parkolópozíció-lista")
    ids = {s["id"] for s in slots}
    seen = set()
    for item in overrides:
        if not isinstance(item, dict) or item.get("id") not in ids or item["id"] in seen:
            raise ValueError("Ismeretlen vagy ismétlődő parkolóazonosító")
        seen.add(item["id"])
        point = _point(item)
        slot = next(s for s in slots if s["id"] == item["id"])
        slot["x"], slot["y"] = point
    for i, slot in enumerate(slots):
        x, y = slot["x"], slot["y"]
        if not (area["x"]+3 <= x <= area["x"]+area["w"]-3 and area["y"]+3 <= y <= area["y"]+area["h"]-3):
            raise ValueError(f"{slot['id']}: a hely a kiválasztott parkolóterület belsejében legyen")
        if not 9 <= abs(y-area["aisle_y"]) <= 20:
            raise ValueError(f"{slot['id']}: a parkolóhely 9–20 m-re legyen a közlekedősávtól")
        for other in slots[:i]:
            # Rectangular envelopes account for parked cars facing up/down.
            if abs(x-other["x"]) < 3.4 and abs(y-other["y"]) < 6.8:
                raise ValueError(f"{slot['id']} és {other['id']}: a parkolóhelyek túl közel vannak")
        slot["area_id"] = area_id
        slot["route"] = copy.deepcopy(prefix) + [[x, area["aisle_y"]], [x, y]]
    initial_count = params["initial_car_count"]
    initial = requested.get("initial_cars")
    if initial is None:
        initial = exit["initial_car_positions"][:initial_count]
    if not isinstance(initial, list) or len(initial) != initial_count:
        raise ValueError("A kezdő autópozíciók száma egyezzen a kezdeti autók számával")
    cars = []
    for i, item in enumerate(initial):
        x, y = _point(item)
        if not (358 <= x <= 558 and abs(y-exit["spawn"][1]) <= 0.05):
            raise ValueError("A kezdő autók a kijárat bevezető sávján helyezhetők el (x:358–558 m, y: a kijárat sávja)")
        if any(math.dist((x, y), (other["x"], other["y"])) < 6.8-1e-8 for other in cars):
            raise ValueError("A kezdő autók között legalább 6,8 m távolság szükséges")
        cars.append(dict(id=f"CAR{i+1:03d}", x=x, y=y, heading=180))
    params["initial_cars"] = cars
    params["slots"] = [{key: slot[key] for key in ("id", "x", "y")} for slot in slots]
    config = copy.deepcopy(base)
    config["world"] = dict(width=600, height=400, origin=exit["spawn"], production_exit=exit["spawn"], buildings=buildings, roads=roads)
    config["network"] = dict(nodes=[dict(id=key, x=point[0], y=point[1]) for key, point in nodes.items()], edges=edges)
    config["crossings"] = [dict(id="ZEBRA1", x=440, y=exit["spawn"][1], axis="vertical"),
                           dict(id="ZEBRA2", x=330, y=140, axis="horizontal"),
                           dict(id="ZEBRA3", x=330, y=300, axis="horizontal")]
    config["zones"], config["slots"] = zones, slots
    config["parking_areas"], config["production_exits"] = areas, exits
    config["simulation"].update(max_active=params["car_count"], spawn_interval=params["spawn_interval_s"],
        parking_dwell=params["parking_dwell_s"], initial_occupied_count=params["initial_occupied_count"],
        initial_cars=cars, production_limit=params["production_limit"])
    config["scenario"] = params
    config["layout"] = dict(preset=preset, title="Audi Hungaria · Győr" if preset == "gyor" else "Oktatási gyárudvar",
        source=SOURCE if preset == "gyor" else "Saját szerkesztés", source_page=2 if preset == "gyor" else None,
        scope="Saját oktatási rajz. A csarnokok relatív helye forrásalapú; a 600×400 m modellméret, utak, parkolók és zebrák feltevések. Nem méretarányos üzemi térkép.")
    return params, config


def scenario_payload(base, requested=None):
    active, config = compile_scenario(base, requested)
    return dict(schema_version=1, presets=PRESETS, limits=LIMITS, active=active, config=config)
