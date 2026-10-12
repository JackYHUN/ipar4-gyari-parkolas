"""Draw the course diagrams locally with Matplotlib, without a PUML service.

The four .puml files are the editable semantic design sources. This program
manually composes equivalent, condensed diagrams for a 5.87-inch report column;
it is not a PlantUML parser or PlantUML renderer. Figure captions should say
"saját programmal szerkesztett ábra a megvalósítás alapján". Component tabs,
package boundary, sequence lifelines and state initial/final markers preserve
the relevant UML notation. The dataflow figure is a complementary DFD sketch.

Run from projekt, using the bundled Python and local tmp/course-tools packages:
    python -m experiments.course_diagrams
Outputs include 300 dpi PNGs, text-preserving SVGs, three review montages and
a SHA-256 manifest of this renderer, the PUML sources and generated figures.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Circle, FancyArrowPatch, FancyBboxPatch, Polygon, Rectangle
from PIL import Image, ImageDraw, ImageFont


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "docs/course-report"
OUT = SOURCE / "figures"
WIDTH = 5.87
DPI = 300
INK = "#183548"
BLUE = "#286587"
TEAL = "#237C78"
LIGHT_BLUE = "#EEF5F9"
LIGHT_TEAL = "#EEF7F5"
GREY = "#637683"
PALE = "#F6F8FA"

plt.rcParams.update({
    "font.family": "DejaVu Sans", "font.size": 10.5,
    "text.color": INK, "axes.edgecolor": INK, "svg.fonttype": "none",
    "svg.hashsalt": "factory-yard-course-diagrams-v1",
    "figure.facecolor": "white", "savefig.facecolor": "white",
})


def canvas(height: float):
    fig = plt.figure(figsize=(WIDTH, height))
    ax = fig.add_axes([0, 0, 1, 1])
    ax.set_xlim(0, WIDTH)
    ax.set_ylim(0, height)
    ax.set_aspect("equal")
    ax.axis("off")
    return fig, ax


def text(ax, x, y, label, *, size=10.5, color=INK, bold=False,
         ha="center", va="center", rotation=0, background=False):
    bbox = {"facecolor": "white", "edgecolor": "none", "pad": 1.8} if background else None
    return ax.text(x, y, label, fontsize=size, color=color, ha=ha, va=va,
                   fontweight="bold" if bold else "normal", linespacing=1.3,
                   rotation=rotation, bbox=bbox, zorder=8)


def box(ax, x, y, width, height, label, *, kind="component", fill=LIGHT_BLUE,
        edge=BLUE, size=10.5, bold=False):
    left, bottom = x - width / 2, y - height / 2
    if kind in {"state", "process"}:
        patch = FancyBboxPatch((left, bottom), width, height,
                              boxstyle="round,pad=0.01,rounding_size=0.11",
                              linewidth=1.1, facecolor=fill, edgecolor=edge, zorder=3)
    else:
        patch = Rectangle((left, bottom), width, height, linewidth=1.1,
                          facecolor=fill, edgecolor=edge, zorder=3)
    ax.add_patch(patch)
    if kind == "component":
        # UML component icon: rectangle with two projecting small tabs.
        icon_x, icon_y = left + width - .23, bottom + height - .25
        ax.add_patch(Rectangle((icon_x, icon_y), .15, .17,
                              fill=False, edgecolor=edge, linewidth=.8, zorder=4))
        for yy in (icon_y + .025, icon_y + .10):
            ax.add_patch(Rectangle((icon_x - .045, yy), .075, .043,
                                  facecolor=fill, edgecolor=edge, linewidth=.7, zorder=4))
    text(ax, x, y, label, size=size, bold=bold)
    return left, bottom, left + width, bottom + height


def line(ax, points, *, color=GREY, style="-", width=1.0, arrow=True,
         both=False, label=None, label_xy=None, size=10.0):
    for start, end in zip(points, points[1:-1]):
        ax.plot([start[0], end[0]], [start[1], end[1]],
                color=color, linewidth=width, linestyle=style, zorder=2)
    start, end = points[-2:]
    if arrow:
        patch = FancyArrowPatch(start, end, arrowstyle="<->" if both and len(points) == 2 else "-|>",
                                mutation_scale=11, linewidth=width,
                                color=color, linestyle=style, zorder=2,
                                shrinkA=0, shrinkB=0)
        ax.add_patch(patch)
        if both and len(points) > 2:
            first_end = (points[0][0] + (points[1][0] - points[0][0]) * .12,
                         points[0][1] + (points[1][1] - points[0][1]) * .12)
            ax.add_patch(FancyArrowPatch(first_end, points[0], arrowstyle="-|>",
                                        mutation_scale=11, linewidth=width, color=color,
                                        shrinkA=0, shrinkB=0, zorder=2))
    else:
        ax.plot([start[0], end[0]], [start[1], end[1]], color=color,
                linewidth=width, linestyle=style, zorder=2)
    if label is not None:
        lx, ly = label_xy or ((start[0] + end[0]) / 2, (start[1] + end[1]) / 2 + .12)
        text(ax, lx, ly, label, size=size, background=True)


def actor(ax, x, y, label):
    ax.add_patch(Circle((x, y + .22), .065, facecolor="white", edgecolor=INK, linewidth=1.1))
    for a, b in [((x, y+.15), (x, y-.03)), ((x-.10, y+.08), (x+.10, y+.08)),
                 ((x, y-.03), (x-.10, y-.14)), ((x, y-.03), (x+.10, y-.14))]:
        line(ax, [a, b], color=INK, arrow=False)
    text(ax, x, y - .27, label, size=10.5)


def components():
    fig, ax = canvas(6.80)
    ax.add_patch(Rectangle((.18, 3.55), 5.51, 3.03,
                          facecolor=PALE, edgecolor=GREY, linewidth=1.0, zorder=0))
    ax.add_patch(Rectangle((.18, 6.34), 2.11, .24,
                          facecolor="white", edgecolor=GREY, linewidth=1.0, zorder=1))
    text(ax, 1.24, 6.46, "Szimulátorfolyamat", size=10.5, bold=True)
    box(ax, 2.94, 5.87, 2.84, .64, "Fizikai folyamatmodell\nAutó · gyalogos · parkoló", size=10.5)
    box(ax, 1.54, 4.88, 2.25, .66, "Virtuális érzékelés\nMintavétel és korlátos zaj", fill=LIGHT_TEAL, edge=TEAL)
    box(ax, 4.33, 4.88, 2.25, .66, "Helyi vezérlés\nMozgás és fékezés", fill=LIGHT_TEAL, edge=TEAL)
    box(ax, 2.94, 3.93, 2.80, .59, "Szimulátor MQTT-kliens", size=10.5)
    line(ax, [(2.16, 5.55), (1.54, 5.22)], label="állapot", label_xy=(1.58, 5.49))
    line(ax, [(4.33, 5.22), (3.73, 5.55)], label="beavatkozás", label_xy=(4.30, 5.49))
    line(ax, [(2.67, 4.88), (3.20, 4.88)], color=TEAL,
         label="helyi\nminta", label_xy=(2.94, 5.03))
    line(ax, [(1.54, 4.55), (2.08, 4.23)], label="telemetria\nfoglaltság", label_xy=(1.15, 4.29))
    line(ax, [(4.33, 4.55), (3.83, 4.23)], both=True,
         label="kérés / engedély\nesemény", label_xy=(4.63, 4.29))
    box(ax, 2.94, 2.88, 2.86, .67, "AMQTT-közvetítő\n127.0.0.1:18883", size=10.5)
    line(ax, [(2.94, 3.63), (2.94, 3.22)], both=True,
         label="MQTT 3.1.1", label_xy=(3.66, 3.44))
    box(ax, 1.52, 1.76, 2.36, .73, "Koordinátor\nParkoló- és zónafoglalás", size=10.5)
    box(ax, 4.35, 1.76, 2.37, .73, "Megfigyelő és naplózó\nFogadott állapot · SQLite", size=10.5)
    line(ax, [(2.36, 2.545), (1.69, 2.125)], both=True,
         label="mérés / engedély", label_xy=(1.37, 2.40))
    line(ax, [(3.52, 2.545), (4.18, 2.125)],
         label="külön adatág", label_xy=(4.60, 2.40))
    box(ax, 4.35, .64, 2.37, .68, "HTTP és műszerfal\nÉlő kép és archívum", size=10.5)
    line(ax, [(4.35, 1.395), (4.35, .98)], label="HTTP-adat", label_xy=(4.98, 1.19))
    actor(ax, 1.0, .79, "Kezelő")
    line(ax, [(1.24, .77), (3.16, .77)], label="forgatókönyv", label_xy=(2.12, .91))
    line(ax, [(3.16, .50), (2.96, .50), (2.96, 2.545)], color=TEAL,
         label="kezelői\nparancs", label_xy=(2.55, 1.23))
    text(ax, 1.45, .20, "A koordinátor a teljes\nigazságállapotot nem olvassa.", size=10.0)
    return fig


def parking_sequence():
    fig, ax = canvas(6.45)
    xs = [1.03, 2.93, 4.84]
    for x, label in zip(xs, ["Autó és virtuális\nparkolóérzékelő", "MQTT-\nközvetítő", "Központi\nkoordinátor"]):
        box(ax, x, 6.05, 1.77, .57, label, kind="participant", size=10.5)
        line(ax, [(x, 5.765), (x, .65)], style=(0, (3, 3)), width=.8, arrow=False)
    def message(a, b, y, label, *, reply=False):
        line(ax, [(xs[a], y), (xs[b], y)], color=TEAL if reply else BLUE,
             style=(0, (4, 2)) if reply else "-", label=label,
             label_xy=((xs[a] + xs[b]) / 2, y + .15), size=10.0)
    message(0, 1, 5.47, "foglaltságminta [seq = n]")
    message(1, 2, 5.05, "teljes, friss keret")
    message(0, 1, 4.61, "parkolókérés [request_id]")
    message(1, 2, 4.19, "kérelem továbbítása")
    # A self message denotes local state update before grant publication.
    line(ax, [(xs[2], 3.99), (5.48, 3.99), (5.48, 3.75), (xs[2], 3.75)], color=TEAL)
    text(ax, 4.01, 3.91, "foglalás rögzítése", size=10.0, background=True)
    message(2, 1, 3.50, "hely és foglalásazonosító", reply=True)
    message(1, 0, 3.08, "parking_grant", reply=True)
    ax.add_patch(Polygon([(.18, 2.23), (5.67, 2.23), (5.67, 2.69), (5.52, 2.84), (.18, 2.84)],
                        closed=True, facecolor="white", edgecolor=GREY, linewidth=.9, zorder=4))
    line(ax, [(5.52, 2.84), (5.52, 2.69), (5.67, 2.69)], arrow=False)
    text(ax, 2.94, 2.68, "Ismételt, még érvényes kérelem:", size=10.0, bold=True)
    text(ax, 2.94, 2.43, "azonos foglalás tér vissza; nincs második kiosztás.", size=10.0)
    message(0, 1, 1.98, "departed [reservation_id]")
    message(1, 2, 1.57, "egyezés; aktuális seq = k")
    message(0, 1, 1.15, "üres minta [seq > k]")
    message(1, 2, .74, "igazolt felszabadítás")
    text(ax, 2.94, .31, "Időtúllépés önmagában nem szabadít helyet.\nA távozás közlekedési útvonala egyszerűsített.", size=10.0)
    return fig


def car_states():
    fig, ax = canvas(6.00)
    center = 2.94
    ax.add_patch(Circle((center, 5.66), .066, facecolor=INK, edgecolor=INK))
    box(ax, center, 5.18, 2.40, .48, "Parkolókérésre vár", kind="state", size=10.5)
    box(ax, center, 4.12, 1.95, .50, "Halad", kind="state", size=11.0, bold=True)
    box(ax, 1.14, 3.06, 1.92, .62, "Gyalogos miatt\nfékez / vár", kind="state", fill=LIGHT_TEAL, edge=TEAL)
    box(ax, 4.74, 3.06, 1.92, .62, "Zónaengedélyre\nvár", kind="state", fill=LIGHT_TEAL, edge=TEAL)
    box(ax, 1.14, 1.85, 1.92, .65, "Érzékelőhiba miatt\nfékez / vár", kind="state", fill=PALE, edge=GREY, size=10.0)
    box(ax, 4.74, 1.85, 1.92, .65, "Kapcsolathiány miatt\nfékez / vár", kind="state", fill=PALE, edge=GREY, size=10.0)
    box(ax, center, .91, 1.95, .48, "Beparkolt", kind="state", size=10.5)
    line(ax, [(center, 5.594), (center, 5.42)])
    line(ax, [(center, 4.94), (center, 4.37)], label="érvényes engedély\nés friss helyi minta", label_xy=(center, 4.66))
    line(ax, [(2.30, 3.87), (1.14, 3.37)], color=TEAL,
         label="észlelt gyalogos", label_xy=(1.21, 3.69))
    line(ax, [(3.58, 3.87), (4.74, 3.37)], color=TEAL,
         label="engedély hiányzik", label_xy=(4.66, 3.69))
    line(ax, [(1.77, 3.37), (2.02, 3.56), (2.25, 3.87)], color=TEAL)
    line(ax, [(4.11, 3.37), (3.86, 3.56), (3.63, 3.87)], color=TEAL)
    text(ax, 2.94, 3.27, "szabad átkelő /\nérvényes zónaengedély", size=10.0, background=True)
    line(ax, [(1.965, 4.12), (.15, 4.12), (.15, 1.85), (.18, 1.85)], color=GREY)
    text(ax, .73, 4.35, "elavult minta", size=10.0)
    line(ax, [(3.915, 4.12), (5.72, 4.12), (5.72, 1.85), (5.70, 1.85)], color=GREY)
    text(ax, 5.03, 4.35, "kapcsolatvesztés", size=10.0)
    line(ax, [(2.10, 1.85), (2.48, 1.85), (2.48, 3.87)], color=GREY)
    line(ax, [(3.78, 1.85), (3.40, 1.85), (3.40, 3.87)], color=GREY)
    text(ax, 2.94, 2.15, "helyreállás;\ntovábbhaladás\nlehetséges", size=10.0, background=True)
    line(ax, [(2.94, 3.87), (2.94, 1.15)], color=BLUE,
         label="célhely\nelérve", label_xy=(2.94, 1.51))
    line(ax, [(center, .67), (center, .30)], label="absztrakt kiadás", label_xy=(4.18, .45))
    ax.add_patch(Circle((center, .21), .085, facecolor="white", edgecolor=INK, linewidth=1.0))
    ax.add_patch(Circle((center, .21), .054, facecolor=INK, edgecolor=INK))
    text(ax, .20, .56, "Fő átmenetek vázlata.\nA megállás fékezési folyamat.", ha="left", size=10.0)
    return fig


def dataflow():
    fig, ax = canvas(5.95)
    box(ax, 2.94, 5.47, 3.02, .66, "Szimulált fizikai folyamat\nAutó · gyalogos · parkoló", kind="process", size=10.5)
    box(ax, 1.51, 4.26, 2.43, .71, "Virtuális érzékelés\nés publikáló\n0,1 s névleges periódus", kind="process", fill=LIGHT_TEAL, edge=TEAL, size=10.0)
    box(ax, 4.34, 4.26, 2.42, .71, "Helyi vezérlés\nÉrzékelésből számolt\nfékezés", kind="process", fill=LIGHT_TEAL, edge=TEAL, size=10.0)
    box(ax, 1.51, 2.92, 2.43, .62, "MQTT-közvetítő\nyard/<run_id>/...", kind="process", size=10.5)
    box(ax, 4.34, 2.92, 2.42, .62, "Központi feldolgozás\nEllenőrzés és kiosztás", kind="process", size=10.5)
    box(ax, 1.51, 1.49, 2.43, .77, "Megfigyelő és archívum\nMűszerfal\nFogadott állapot", kind="process", fill=PALE, edge=GREY, size=10.0)
    actor(ax, 4.34, 1.51, "Kezelő")
    line(ax, [(2.20, 5.14), (1.51, 4.615)], label="állapot", label_xy=(1.22, 4.98))
    line(ax, [(4.34, 4.615), (3.69, 5.14)], color=TEAL,
         label="beavatkozás", label_xy=(4.64, 4.99))
    line(ax, [(2.725, 4.26), (3.13, 4.26)], color=TEAL,
         label="helyi minta", label_xy=(2.94, 4.62))
    line(ax, [(1.51, 3.905), (1.51, 3.23)], label="telemetria\nés foglaltság", label_xy=(1.51, 3.59))
    line(ax, [(2.725, 2.92), (3.13, 2.92)], both=True,
         label="mérés / engedély", label_xy=(2.94, 3.26))
    line(ax, [(2.725, 3.09), (2.94, 3.09), (2.94, 3.75), (4.34, 3.75), (4.34, 3.905)],
         label="járműparancs", label_xy=(3.80, 3.56))
    line(ax, [(1.51, 2.61), (1.51, 1.875)], label="külön adatág", label_xy=(1.51, 2.23))
    line(ax, [(4.13, 1.48), (2.725, 1.48)], label="forgatókönyv", label_xy=(3.49, 1.68))
    line(ax, [(.295, 1.49), (.12, 1.49), (.12, 2.92), (.295, 2.92)], color=TEAL)
    text(ax, .72, 1.98, "kezelői\nparancs", size=10.0, background=True)
    # A separate display snapshot is not a coordinator measurement input.
    line(ax, [(1.43, 5.47), (.16, 5.47), (.16, 3.08), (.295, 3.08)], color=GREY)
    text(ax, .14, 4.30, "snapshot / kérés / esemény", size=10.0, rotation=90, background=True)
    text(ax, 2.94, .48, "A helyi fékezés saját mintából készül.\nA snapshot csak a megjelenítési adatágat látja el.", size=10.0)
    return fig


def hash_file(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def save(fig, name):
    fig.savefig(OUT / f"{name}.png", dpi=DPI, metadata={"Software": "Matplotlib; course_diagrams.py"})
    fig.savefig(OUT / f"{name}.svg", metadata={"Date": None, "Creator": "Local Matplotlib course diagram renderer"})
    plt.close(fig)


def montage(names, target, columns=2, item_width=1100):
    images = []
    for name in names:
        image = Image.open(OUT / f"{name}.png").convert("RGB")
        new_height = round(image.height * item_width / image.width)
        image = image.resize((item_width, new_height), Image.Resampling.LANCZOS)
        images.append((name, image))
    rows = (len(images) + columns - 1) // columns
    row_height = max(image.height for _, image in images) + 74
    sheet = Image.new("RGB", (columns * (item_width + 30) + 30, rows * row_height + 30), "white")
    draw = ImageDraw.Draw(sheet)
    font = ImageFont.truetype("C:/Windows/Fonts/arial.ttf", 28)
    for i, (name, image) in enumerate(images):
        x, y = 30 + (i % columns) * (item_width + 30), 30 + (i // columns) * row_height
        draw.text((x, y), name, font=font, fill=INK)
        sheet.paste(image, (x, y + 50))
    sheet.save(OUT / target)


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    functions = {"components": components, "parking_sequence": parking_sequence,
                 "car_states": car_states, "dataflow": dataflow}
    for name, function in functions.items():
        save(function(), name)
    montage(["components", "dataflow"], "montage-architecture.png")
    montage(["parking_sequence", "car_states"], "montage-protocol.png")
    montage(list(functions), "montage-overview.png", item_width=800)
    renderer = Path(__file__).resolve()
    files = [renderer, *[SOURCE / f"{name}.puml" for name in functions],
             *[OUT / f"{name}.{ext}" for name in functions for ext in ("png", "svg")],
             *[OUT / name for name in ("montage-architecture.png", "montage-protocol.png", "montage-overview.png")]]
    manifest = {
        "schema_version": 1,
        "method": "Locally composed Matplotlib diagrams based on own PUML semantic sources; not automatic PlantUML rendering",
        "caption_source_hu": "Saját programmal szerkesztett ábra a megvalósítás alapján.",
        "figure_width_inches": WIDTH, "png_dpi": DPI,
        "minimum_font_size_points": 10.0,
        "font_family": "DejaVu Sans", "matplotlib_version": matplotlib.__version__,
        "files": {str(path.relative_to(ROOT)).replace("\\", "/"): {"sha256": hash_file(path), "bytes": path.stat().st_size}
                  for path in files},
        "figures": {name: {"png": f"{name}.png", "svg": f"{name}.svg",
                           "png_size_px": list(Image.open(OUT / f"{name}.png").size)} for name in functions},
        "review_montages": ["montage-architecture.png", "montage-protocol.png", "montage-overview.png"],
    }
    (OUT / "diagrams-manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"output": str(OUT), "figures": len(functions), "png_dpi": DPI,
                      "manifest": str(OUT / "diagrams-manifest.json")}, ensure_ascii=False))


if __name__ == "__main__":
    main()
