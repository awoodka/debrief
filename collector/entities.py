"""Entity resolution + releases ledger (work order P0-3). Deterministic, no LLM.

Clusters items announcing the SAME model/release across sources into ONE 'release' record, so a big
drop (Claude Sonnet 5, GPT-5.6 Sol, Nano Banana 2 Lite, ...) appears once with all its source links.

High precision over recall: an item is a release only if it has a RELEASE SIGNAL (introduce/launch/
release/preview/open-weights/Show-HN) AND a name that is either a known BRAND + variant, or a single
versioned token (Ornith-1.0, Qwen3.6). Extend BRANDS as new families appear.
"""
import re
from collections import defaultdict

# Known model families -> org. Extend freely.
BRANDS = {
    "claude": "Anthropic", "gpt": "OpenAI", "chatgpt": "OpenAI", "sora": "OpenAI",
    "gemini": "Google", "gemma": "Google", "nano banana": "Google", "veo": "Google", "imagen": "Google",
    "llama": "Meta", "qwen": "Alibaba", "wan": "Alibaba", "mistral": "Mistral", "mixtral": "Mistral",
    "magistral": "Mistral", "leanstral": "Mistral", "deepseek": "DeepSeek", "grok": "xAI",
    "phi": "Microsoft", "pangu": "Huawei", "kimi": "Moonshot", "yi": "01.AI", "command": "Cohere",
    "jamba": "AI21", "flux": "Black Forest Labs", "glm": "Zhipu", "minimax": "MiniMax",
    "hunyuan": "Tencent", "ernie": "Baidu", "nemotron": "NVIDIA", "ornith": "", "olmo": "AI2",
}
_BRAND_RE = re.compile(r"\b(" + "|".join(sorted(BRANDS, key=len, reverse=True)) + r")\b", re.I)
_RELEASE_VERB = re.compile(r"\b(introduc|announc|unveil|debut|launch|releas|ship|preview|"
                           r"now available|available now|open.?sourc|open.?weight|start building|meet the)\b", re.I)
_STRIP = re.compile(r"^(introducing the|introducing|meet|announcing|presenting|previewing|the|"
                    r"start building with|show hn:|launch hn:)\s+", re.I)
# a trailing model token: a version, a Capitalized variant word, or a known suffix
_VARIANT = re.compile(r"^(v?\d[\d.]*|[A-Z][A-Za-z0-9]*|lite|flash|pro|turbo|mini|preview|base|"
                      r"instruct|thinking|air|sol|max|omni|nano)$")
_STOP = {"the", "and", "app", "is", "can", "for", "with", "to", "a", "an", "now", "on", "in",
         "of", "that", "are", "how", "new", "our", "your", "you"}
# single-token model-version, catches new brands: Ornith-1.0, Qwen3.6, openPangu-2.0-Flash, GPT-5.6
_TOKEN_VER = re.compile(r"\b([A-Za-z][A-Za-z0-9]*(?:[-.][A-Za-z0-9]+)*[-.]?\d[\d.]*(?:-[A-Za-z]+)*)\b")
# hardware / non-model tokens to reject (GPUs, RAM, arches)
_HARDWARE = re.compile(r"^(v\d{2,3}|gb\d{2,3}|ddr\d+|gfx\d+|[ah]\d00|rtx\d+|rx\d+|sm\d+x?|cuda\d*|fp\d+|nvfp\d+|int\d+)$", re.I)
_RESOLVE_TYPES = ("lab_news", "discussion", "article", "news", "funding")


def _norm(s):
    return re.sub(r"[^a-z0-9]+", " ", (s or "").lower()).strip()


def _release_signal(it):
    if _RELEASE_VERB.search(it.get("title", "") or ""):
        return True
    rs = it["raw_signal"]
    return bool(rs.get("show_hn") or rs.get("launch_hn"))


def extract_name(it):
    """(norm_name, display) of a VERSIONED model/release named in the title, else None.
    Precision comes from the name shape (brand+version or a single versioned token), not a verb gate."""
    title = _STRIP.sub("", (it.get("title") or "")).strip()
    m = _BRAND_RE.search(title)
    if m:
        toks = title[m.start():].split()
        name = [toks[0]]
        for tk in toks[1:5]:
            t = tk.strip(",:.")
            if t.lower() in _STOP or not _VARIANT.match(t):
                break
            name.append(t)
        disp = " ".join(name).strip(" -:.,")
        if any(c.isdigit() for c in disp) and len(_norm(disp)) >= 3:   # require a version
            return _norm(disp), disp
    for tm in _TOKEN_VER.finditer(title):
        tok = tm.group(1)
        if _HARDWARE.match(tok):
            continue
        if (re.match(r"^[A-Za-z]", tok) and any(c.isdigit() for c in tok) and len(_norm(tok)) >= 4
                and not re.match(r"^(19|20)\d\d$", re.sub(r"\D", "", tok))):
            return _norm(tok), tok
    return None


def _org(norm_name, item):
    for b, org in BRANDS.items():
        if b and b in norm_name:
            return org
    for key, org in (("openai", "OpenAI"), ("anthropic", "Anthropic"), ("gemini", "Google"),
                     ("deepmind", "Google"), ("google", "Google"), ("meta", "Meta"),
                     ("mistral", "Mistral"), ("xai", "xAI"), ("nvidia", "NVIDIA")):
        if key in item.get("source", ""):
            return org
    return ""


def _merge_signals(sigs):
    out = {}
    for s in sigs:
        for k, v in s.items():
            out[k] = max(out.get(k, 0), v) if isinstance(v, (int, float)) else out.get(k, v)
    return out


def resolve(items):
    """Return (new_items, releases). Release clusters become one 'release' record; everything else
    passes through untouched."""
    groups, leftovers = defaultdict(list), []
    for it in items:
        ex = extract_name(it) if it["type"] in _RESOLVE_TYPES else None
        (groups[ex[0]].append((it, ex[1])) if ex else leftovers.append(it))

    releases = []
    for norm, members in groups.items():
        base = max(members, key=lambda m: len(m[0].get("summary", "")))[0]
        display = max((m[1] for m in members), key=len)
        srcs = sorted({s for it, _ in members for s in it.get("sources", [it["source"]])})
        releases.append({
            "source": "release", "type": "release", "sources": srcs,
            "entity": {"name": display.title() if display.islower() else display, "org": _org(norm, base)},
            "title": display, "url": base.get("url", ""), "summary": base.get("summary", ""),
            "published": base.get("published"), "arxiv_id": None, "tags": [], "seen_in": srcs,
            "links": [{"source": it["source"], "url": it.get("url", "")} for it, _ in members if it.get("url")],
            "raw_signal": {**_merge_signals([it["raw_signal"] for it, _ in members]),
                           "axis": "mainstream", "release_sources": len(srcs)},
        })
    releases.sort(key=lambda r: r["raw_signal"]["release_sources"], reverse=True)
    return leftovers + releases, releases
