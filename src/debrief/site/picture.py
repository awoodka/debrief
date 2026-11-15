"""Each v2 day's picture: a drawing made from the shape of the issue, never its words, written as
<date>/picture.svg and shown under the standfirst.

A Day is everything the drawings read. There are seven styles; `pictures` gives each day one,
picked by a hash of its date and never the style the day before had, so the same briefs always
draw the same bytes. The hash (FNV-1a), the random numbers (mulberry32) and the drawings are
ports of the JavaScript the design was approved in, so a day draws the picture it drew there.

An <img> can't see the page's CSS, so each SVG carries its own colors: site.css's paper, ink and
accent, light and dark, with every mix worked out to a plain hex color.
"""
import math
import re
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from typing import Callable

KINDS = ("papers", "releases", "announcements", "press_policy", "discussion")
WIDTH, HEIGHT = 1200, 500
M32 = 0xFFFFFFFF


@dataclass(frozen=True)
class Section:
    words: int
    stories: int
    split: bool


@dataclass(frozen=True)
class Story:
    """One scan row: its kind (the scan section), whether the article covered it, and how much
    it was discussed: its threads' comments plus a fifth of their points."""
    kind: str
    covered: bool
    discussion: int


@dataclass(frozen=True)
class Day:
    date: str
    headline: str
    sections: tuple[Section, ...]
    updates: int
    scan: tuple[Story, ...]

    @classmethod
    def from_brief(cls, doc):
        def discussion(sid):
            threads = doc["stories"].get(sid, {}).get("threads", [])
            return sum(t["comments"] or 0 for t in threads) + sum(t["points"] or 0 for t in threads) // 5
        return cls(doc["date"], doc["headline"],
                   tuple(Section(len(s["body"].split()), len(s["stories"]), bool(s.get("split"))) for s in doc["sections"]),
                   len(doc["updates"]),
                   tuple(Story(x["section"], bool(x["covered"]), discussion(x["id"])) for x in doc["scan"]))


def fnv1a(s):
    """FNV-1a, 32 bits, over UTF-16 code units, as JavaScript's charCodeAt sees a string."""
    h, units = 2166136261, s.encode("utf-16-le")
    for i in range(0, len(units), 2):
        h = ((h ^ (units[i] | units[i + 1] << 8)) * 16777619) & M32
    return h


def mulberry32(seed):
    """Numbers in [0, 1) from a 32-bit seed, bit for bit the same as the JavaScript."""
    def rand():
        nonlocal seed
        seed = (seed + 0x6D2B79F5) & M32
        t = ((seed ^ (seed >> 15)) * (seed | 1)) & M32
        t = ((t + (((t ^ (t >> 7)) * (t | 61)) & M32)) & M32) ^ t
        return ((t ^ (t >> 14)) & M32) / 4294967296
    return rand


def rnd(x):
    """JavaScript's Math.round: halves go up."""
    return math.floor(x + 0.5)


def fixed(x, places):
    """JavaScript's toFixed: the exact value, halves away from zero, trailing zeros dropped."""
    s = str(Decimal(x).quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_UP))
    return s.rstrip("0").rstrip(".") if "." in s else s


def polyline(points):
    """An SVG path through integer points: the first absolute, the rest relative to the one before."""
    (x0, y0), out = points[0], []
    for x, y in points[1:]:
        if (x, y) != (x0, y0):
            out.append(f"{x - x0} {y - y0}")
            x0, y0 = x, y
    return f"M{points[0][0]} {points[0][1]}" + ("l" + " ".join(out) if out else "")


def _thin(points, tolerance=0.5):
    """The fewest of `points` that keep the line within `tolerance` of where it was (Douglas-Peucker)."""
    if len(points) < 3:
        return points
    (x1, y1), (x2, y2) = points[0], points[-1]
    length = math.hypot(x2 - x1, y2 - y1) or 1
    far, i = max((abs((x2 - x1) * (y1 - y) - (x1 - x) * (y2 - y1)) / length, i) for i, (x, y) in enumerate(points[1:-1], 1))
    if far <= tolerance:
        return [points[0], points[-1]]
    return _thin(points[:i + 1], tolerance)[:-1] + _thin(points[i:], tolerance)


def endpaper(day):
    r = mulberry32(fnv1a(day.date + day.headline))
    waves = [(0.002 + r() * 0.006, 0.003 + r() * 0.008, r() * 6.283, 0.6 + r() * 0.9) for _ in range(max(3, len(day.sections)))]
    tilt = (r() - 0.5) * 0.8

    def angle(x, y):
        t = tilt
        for i, (fx, fy, ph, a) in enumerate(waves):
            t += a * math.sin(fx * x + fy * y * (1 if i % 2 else -1) + ph)
        return t

    out = []
    for _ in range(120):
        x, y = r() * 1200, r() * 500
        points = [(rnd(x), rnd(y))]
        for _ in range(110):
            a = angle(x, y)
            x += math.cos(a) * 6
            y += math.sin(a) * 6
            if x < -20 or x > 1220 or y < -20 or y > 520:
                break
            points.append((rnd(x), rnd(y)))
        c = r()
        cls = "flow" if c < 0.55 else "flow soft" if c < 0.85 else "flow ink"
        width, opacity = fixed(1 + r() * 2.2, 2), fixed(0.45 + r() * 0.5, 2)
        out.append(f'<path class="{cls}" d="{polyline(_thin(points))}" stroke-width="{width}" stroke-opacity="{opacity}"/>')
    return out


def _kinds(day):
    return [k for k in KINDS if any(x.kind == k for x in day.scan)]


def _has_sections(day):
    return bool(day.sections)


def _has_stories(day):
    return bool(_kinds(day))


def map_of_the_day(day):
    present = _kinds(day)
    w, base = 1200 / len(present), max(13, 36 - len(day.scan) * 0.75)
    out = []
    for i, kind in enumerate(present):
        cx, cy = w * (i + 0.5), 235
        items = sorted(((base + math.sqrt(x.discussion) * 1.15, x.covered) for x in day.scan if x.kind == kind), key=lambda it: -it[0])
        out.append(f'<path class="guide" d="M{rnd(cx)} 30V440"/>')
        placed = []
        for radius, covered in items:
            for t in range(4000):
                ang, rad = t * 2.39996, base * 0.32 * math.sqrt(t)
                x, y = cx + math.cos(ang) * rad, cy + math.sin(ang) * rad * 1.15
                if x - radius < cx - w / 2 + 8 or x + radius > cx + w / 2 - 8 or y - radius < 20 or y + radius > 460:
                    continue
                if all(math.sqrt((px - x) * (px - x) + (py - y) * (py - y)) > pr + radius + 5 for px, py, pr in placed):
                    placed.append((x, y, radius))
                    out.append(f'<circle class="{"dot" if covered else "dot open"}" cx="{rnd(x)}" cy="{rnd(y)}" r="{fixed(radius, 1)}"/>')
                    break
    return out


def spines(day):
    r = mulberry32(fnv1a(day.date))
    books = [(58 + s.stories * 14, min(410, 150 + s.words * 0.52), s.split, i + 1, f"sp{i % 4}") for i, s in enumerate(day.sections)]
    books += [(26, 150 + r() * 70, False, None, "spu") for _ in range(day.updates)]
    gap, base = 6, 455
    x = (1200 - (sum(b[0] + gap for b in books) - gap)) / 2
    out = [f'<rect class="shelf" x="70" y="{base}" width="1060" height="10" rx="2"/>']
    for i, (w, h, split, num, cls) in enumerate(books):
        top = rnd(base - h)
        g = [f'<rect class="{cls}" x="{rnd(x)}" y="{top}" width="{w}" height="{base - top}" rx="3"/>']
        if split:
            g += [f'<path class="band" d="M{rnd(x + 6)} {top + dy}h{w - 12}"/>' for dy in (26, 36)]
        if num:
            g.append(f'<text class="num" x="{rnd(x + w / 2)}" y="{base - 22}">{num}</text>')
        if i == len(books) - 1 and r() < 0.6:
            out.append(f'<g transform="rotate(-7 {rnd(x + w)} {base})">{"".join(g)}</g>')
        else:
            out += g
        x += w + gap
    return out


def strata(day):
    r = mulberry32(fnv1a("strata" + day.date))
    layers = [(s.words, f"sp{i % 4}", False) for i, s in enumerate(day.sections)] + [(0, "spu", True)] * day.updates
    words, room = sum(s.words for s in day.sections) or 1, 400 - day.updates * 14
    prev = [(x, 500.0) for x in range(0, 1201, 20)]
    out = []
    for t, cls, thin in layers:
        th = 14 if thin else max(22, room * t / words)
        amp = th * 0.38
        f1, f2, p1, p2 = 0.003 + r() * 0.006, 0.008 + r() * 0.01, r() * 6.28, r() * 6.28
        top = [(x, min(y - th + amp * math.sin(f1 * x + p1) * 0.7 + amp * 0.3 * math.sin(f2 * x + p2), y - 6)) for x, y in prev]
        out.append(f'<path class="{cls}" d="{polyline([(rnd(x), rnd(y)) for x, y in top + prev[::-1]])}z"/>')
        prev = top
    return out


SEGMENTS = {1: ("LB",), 2: ("BR",), 3: ("LR",), 4: ("TR",), 5: ("LT", "BR"), 6: ("TB",), 7: ("LT",), 8: ("LT",),
            9: ("TB",), 10: ("TR", "LB"), 11: ("TR",), 12: ("LR",), 13: ("BR",), 14: ("LB",)}


def _chains(segments):
    """Segments that share end points joined into as few lines as possible, in a fixed order."""
    ends, used, out = {}, [False] * len(segments), []
    for i, (a, b) in enumerate(segments):
        ends.setdefault(a, []).append(i)
        ends.setdefault(b, []).append(i)

    def grow(line):
        while True:
            nxt = next((j for j in ends[line[-1]] if not used[j]), None)
            if nxt is None:
                return line
            used[nxt] = True
            a, b = segments[nxt]
            line.append(b if a == line[-1] else a)

    for i, (a, b) in enumerate(segments):
        if not used[i]:
            used[i] = True
            out.append(grow([a, b])[::-1])
            out[-1] = grow(out[-1])
    return out


def contours(day):
    r = mulberry32(fnv1a("contour" + day.date))
    hills = [(120 + r() * 960, 70 + r() * 360, 90 + r() * 160, 70 + r() * 110, 0.45 + s.words / 650) for s in day.sections]

    def height(x, y):
        v = 0
        for hx, hy, sx, sy, h in hills:
            dx, dy = (x - hx) / sx, (y - hy) / sy
            v += h * math.exp(-(dx * dx + dy * dy) / 2)
        return v

    cs, n = 12, 16
    nx, ny = math.ceil(1200 / cs), math.ceil(500 / cs)
    g = [[height(i * cs, j * cs) for i in range(nx + 1)] for j in range(ny + 1)]
    top = max(max(row) for row in g)
    out = []
    for level in range(1, n + 1):
        L, segments = top * level / (n + 1), []
        for j in range(ny):
            for i in range(nx):
                a, b, c, e = g[j][i], g[j][i + 1], g[j + 1][i + 1], g[j + 1][i]
                idx = (a > L) << 3 | (b > L) << 2 | (c > L) << 1 | (e > L)
                if idx in (0, 15):
                    continue
                x0, y0 = i * cs, j * cs
                edge = {"T": lambda: (x0 + cs * (L - a) / (b - a), y0), "R": lambda: (x0 + cs, y0 + cs * (L - b) / (c - b)),
                        "B": lambda: (x0 + cs * (L - e) / (c - e), y0 + cs), "L": lambda: (x0, y0 + cs * (L - a) / (e - a))}
                segments += [(edge[p](), edge[q]()) for p, q in SEGMENTS[idx]]
        d = "".join(polyline([(rnd(x), rnd(y)) for x, y in line]) for line in _chains(segments))
        out.append(f'<path class="{"iso" if level % 4 == 0 else "iso soft"}" d="{d}" stroke-width="{2.2 if level % 4 == 0 else 1.2}"/>')
    return out


def weave(day):
    r = mulberry32(fnv1a("weave" + day.date))
    cols, rows = max(10, min(36, len(day.scan))), max(9, min(22, len(day.sections) * 3 + day.updates))
    cw, rh = 1200 / cols, 500 / rows
    period = 2 + math.floor(r() * 3)
    shift, gap = 1 + math.floor(r() * (period - 1)), 3
    out = [f'<rect class="{"weft alt" if j % 2 else "weft"}" x="0" y="{rnd(j * rh + gap / 2)}" width="1200" height="{rnd(rh - gap)}"/>'
           for j in range(rows)]
    for i in range(cols):
        cls = ("warp alt" if i % 2 else "warp") if day.scan[i % len(day.scan)].covered else "warp off"
        for j in range(rows):
            if (i + j * shift) % period == 0 or (period > 2 and (i + j * shift) % period == 1):
                out.append(f'<rect class="{cls}" x="{rnd(i * cw + gap / 2)}" y="{rnd(j * rh)}" width="{rnd(cw - gap)}" height="{rnd(rh) + 1}"/>')
    return out


def constellation(day):
    r = mulberry32(fnv1a("stars" + day.date))
    out = [f'<circle class="dust" cx="{rnd(r() * 1200)}" cy="{rnd(r() * 500)}" r="{fixed(0.8 + r() * 1.4, 1)}"/>' for _ in range(90)]
    present = _kinds(day)
    w = 1200 / len(present)
    for i, kind in enumerate(present):
        stars = [(w * i + 40 + r() * (w - 80), 50 + r() * 400, 4 + math.sqrt(x.discussion) * 0.55, x.covered) for x in day.scan if x.kind == kind]
        lit = sorted((s for s in stars if s[3]), key=lambda s: s[0])
        if len(lit) > 1:
            out.append(f'<path class="link" d="{polyline([(rnd(x), rnd(y)) for x, y, _, _ in lit])}"/>')
        out += [f'<circle class="{"star" if c else "star dim"}" cx="{rnd(x)}" cy="{rnd(y)}" r="{fixed(s, 1)}"/>' for x, y, s, c in stars]
    return out


def _n(n, one, many):
    return f"{n} {one if n == 1 else many}"


def _updates(day, what):
    return f", with a thin gray {what} for each update" if day.updates else ""


@dataclass(frozen=True)
class Style:
    """A way to draw a Day. `caption` says what the style is and what it shows; `alt` describes
    the drawing for one day; `draws` says whether a day has what the drawing needs."""
    id: str
    caption: str
    alt: Callable[[Day], str]
    draw: Callable[[Day], list]
    draws: Callable[[Day], bool]


STYLES = (
    Style("endpaper", "Today's picture is an endpaper. Its marbled lines come from the date and headline, "
                      "with one wave for each section.",
          lambda d: "Marbled lines in blue and gray, flowing across the page.", endpaper, lambda d: True),
    Style("map", "Today's picture is a map of the day. Each dot is a story kept, grouped by kind, "
                 "and the filled dots made the article.",
          lambda d: f"Circles for {_n(len(d.scan), 'story', 'stories')} in {_n(len(_kinds(d)), 'column', 'columns')}, "
                    f"{sum(x.covered for x in d.scan)} of them filled in blue.", map_of_the_day, _has_stories),
    Style("spines", "Today's picture is a shelf of book spines. Each section is a book, taller when it runs longer "
                    "and wider when it cites more stories.",
          lambda d: f"{_n(len(d.sections), 'numbered book spine', 'numbered book spines')} in shades of blue on a shelf"
                    f"{_updates(d, 'spine')}.",
          spines, _has_sections),
    Style("strata", "Today's picture is a cut through layers of rock. Each section is a layer, thicker when it runs "
                    "longer, and the updates are the thin layers on top.",
          lambda d: f"{_n(len(d.sections), 'wavy layer', 'wavy layers')} of rock in shades of blue{_updates(d, 'layer on top')}.",
          strata, _has_sections),
    Style("contours", "Today's picture is a contour map. Each section is a hill, taller when it runs longer.",
          lambda d: f"Contour lines around {_n(len(d.sections), 'hill', 'hills')}.", contours, _has_sections),
    Style("weave", "Today's picture is a weave. Each thread is a story kept, and the blue ones made the article.",
          lambda d: "A woven pattern of pale blue and gray threads.",
          weave, _has_stories),
    Style("constellation", "Today's picture is a constellation. Each story is a star, larger when it was discussed more, "
                           "and stories of one kind that made the article are joined.",
          lambda d: f"{_n(len(d.scan), 'star', 'stars')} on a dusty sky, some blue and joined by lines.", constellation,
          _has_stories),
)

# class: (property, color, percent of it, mixed into). Colors are site.css tokens. The weave's
# tints are lighter than the rest: at full width its blocks would otherwise shout over the page.
PAINT = {
    "bg": ("fill", "accent", 5, "paper"),
    "flow": ("stroke", "accent", 100, "paper"), "flow.soft": ("stroke", "accent", 45, "paper"),
    "flow.ink": ("stroke", "ink", 55, "paper"),
    "dot": ("fill", "accent", 100, "paper"), "dot.open": ("stroke", "ink", 50, "paper"),
    "guide": ("stroke", "ink", 14, "paper"),
    "shelf": ("fill", "ink", 70, "paper"),
    "sp0": ("fill", "accent", 100, "paper"), "sp1": ("fill", "accent", 72, "paper"),
    "sp2": ("fill", "accent", 50, "ink"), "sp3": ("fill", "accent", 40, "paper"),
    "spu": ("fill", "ink", 22, "paper"),
    "band": ("stroke", "paper", 100, "paper"), "num": ("fill", "paper", 100, "paper"),
    "iso": ("stroke", "accent", 100, "paper"), "iso.soft": ("stroke", "accent", 50, "paper"),
    "warp": ("fill", "accent", 62, "paper"), "warp.alt": ("fill", "accent", 40, "paper"),
    "warp.off": ("fill", "ink", 16, "paper"),
    "weft": ("fill", "accent", 12, "paper"), "weft.alt": ("fill", "ink", 7, "paper"),
    "star": ("fill", "accent", 100, "paper"), "star.dim": ("fill", "ink", 40, "paper"),
    "dust": ("fill", "ink", 30, "paper"),
    "link": ("stroke", "accent", 55, "paper"),
}
SHAPE = (".flow,.dot.open,.band,.iso,.link{fill:none}.flow,.iso{stroke-linecap:round}.iso{stroke-linejoin:round}"
         ".dot.open{stroke-width:2}.guide{stroke-width:1.5}.band{stroke-width:3;stroke-opacity:.7}.link{stroke-width:1.6}"
         ".num{font:500 30px Newsreader,Georgia,serif;text-anchor:middle}")


def palettes(css):
    """site.css's paper, ink and accent: (light, dark)."""
    light_part, dark_part = css.split("@media (prefers-color-scheme: dark)", 1)
    token = re.compile(r"--(paper|ink|accent):\s*(#[0-9a-fA-F]{6});")
    light = dict(token.findall(light_part))
    return light, {**light, **dict(token.findall(dark_part.split("}", 1)[0]))}


def _mix(a, percent, b):
    ca, cb = (tuple(int(c[i:i + 2], 16) for i in (1, 3, 5)) for c in (a, b))
    return "#" + "".join(f"{rnd(percent / 100 * x + (1 - percent / 100) * y):02x}" for x, y in zip(ca, cb))


def stylesheet(css):
    """The <style> every picture carries: the shapes, then the light colors, then the dark ones."""
    def colors(theme):
        return "".join(f".{cls}{{{prop}:{_mix(theme[a], p, theme[b])}}}" for cls, (prop, a, p, b) in PAINT.items())
    light, dark = palettes(css)
    return f"{SHAPE}{colors(light)}@media (prefers-color-scheme:dark){{{colors(dark)}}}"


@dataclass(frozen=True)
class Picture:
    alt: str
    caption: str
    svg: str


def draw(day, style, css):
    svg = (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {WIDTH} {HEIGHT}" width="{WIDTH}" height="{HEIGHT}">'
           f'<style>{stylesheet(css)}</style><rect class="bg" width="{WIDTH}" height="{HEIGHT}"/>'
           + "\n".join(style.draw(day)) + "</svg>\n")
    return Picture(style.alt(day), style.caption, svg)


def pick(days):
    """{date: Style} for `days` (Days, any order): walking the dates in order, the style at the
    date's hash, or the next one that can draw the day and isn't the style the day before had."""
    out, prev = {}, None
    for day in sorted(days, key=lambda d: d.date):
        start = fnv1a(day.date) % len(STYLES)
        order = [STYLES[(start + k) % len(STYLES)] for k in range(len(STYLES))]
        able = [s for s in order if s.draws(day)]
        prev = out[day.date] = next((s for s in able if s is not prev), able[0])
    return out


def pictures(docs, css):
    """{date: Picture} for every brief.json in `docs` ({date: document})."""
    days = [Day.from_brief(doc) for doc in docs.values()]
    styles = pick(days)
    return {d.date: draw(d, styles[d.date], css) for d in days}
