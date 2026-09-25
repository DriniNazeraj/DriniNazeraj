#!/usr/bin/env python3
"""Render Drini's profile README assets.

Reads assets/profile.json and assets/projects.json, fetches public GitHub
data, and writes SVG files into assets/. Animations are SMIL, which GitHub
plays when the SVG is loaded through an <img> tag.

  python scripts/generate.py
"""

from __future__ import annotations

import json
import math
import os
import subprocess
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "assets"
PROFILE_PATH = ASSETS / "profile.json"
PROJECTS_PATH = ASSETS / "projects.json"
STATS_PATH = ASSETS / "stats.json"

W = 1100

# Palette aligned with the terminal / CRT profile reel: deep navy, cyan,
# purple-pink glow, and green terminal text.
BG = "#07090F"
CARD = "#0C1222"
PANEL = "#0E1729"
LINE = "#243044"
CYAN = "#67E8F9"
CYAN_DIM = "#22D3EE"
PURPLE = "#C4B5FD"
PINK = "#F0ABFC"
GREEN = "#4ADE80"
TEXT = "#E8EEF9"
MUTED = "#8B9CB8"
AMBER = "#FBBF24"

FONT = "ui-monospace, SFMono-Regular, Menlo, Consolas, monospace"


def esc(value: object) -> str:
    return (
        str(value)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def svg_doc(width: int, height: int, body: str, title: str, desc: str, extra_attrs: str = "") -> str:
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
        f'viewBox="0 0 {width} {height}" role="img" {extra_attrs}>'
        f"<title>{esc(title)}</title><desc>{esc(desc)}</desc>"
        f"{body}</svg>\n"
    )


def load_json(path: Path) -> dict | list:
    return json.loads(path.read_text())


def github_token() -> str | None:
    for key in ("GITHUB_TOKEN", "GH_TOKEN"):
        value = os.environ.get(key)
        if value:
            return value
    try:
        return subprocess.check_output(["gh", "auth", "token"], text=True).strip() or None
    except (OSError, subprocess.CalledProcessError):
        return None


def request_json(url: str, token: str | None, method: str = "GET", payload: dict | None = None):
    data = None if payload is None else json.dumps(payload).encode()
    headers = {
        "User-Agent": "drini-profile-readme",
        "Accept": "application/vnd.github+json",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if payload is not None:
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=40) as response:
        return json.loads(response.read().decode())


def fetch_bytes(url: str, token: str | None = None) -> bytes:
    headers = {"User-Agent": "drini-profile-readme"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=40) as response:
        return response.read()


def previous_stats() -> dict:
    if STATS_PATH.exists():
        return json.loads(STATS_PATH.read_text())
    return {}


def fetch_profile(login: str, token: str | None) -> dict:
    previous = previous_stats()
    user = request_json(f"https://api.github.com/users/{login}", token)
    repos = request_json(
        f"https://api.github.com/users/{login}/repos?per_page=100&type=owner&sort=updated",
        token,
    )
    languages: dict[str, dict[str, int]] = {}
    for repo in repos:
        name = repo["name"]
        try:
            languages[name] = request_json(
                f"https://api.github.com/repos/{login}/{name}/languages",
                token,
            )
        except urllib.error.HTTPError as error:
            print(f"warn: languages for {name} failed ({error.code})")
            languages[name] = {}

    contributions = previous.get("contributions_1y")
    current = previous.get("current_streak")
    longest = previous.get("longest_streak")
    calendar_days = None
    try:
        graph = request_json(
            "https://api.github.com/graphql",
            token,
            method="POST",
            payload={
                "query": """
                query($login: String!) {
                  user(login: $login) {
                    contributionsCollection {
                      contributionCalendar {
                        totalContributions
                        weeks { contributionDays { date contributionCount } }
                      }
                    }
                  }
                }
                """,
                "variables": {"login": login},
            },
        )
        calendar = graph["data"]["user"]["contributionsCollection"]["contributionCalendar"]
        contributions = calendar["totalContributions"]
        days = [
            (day["date"], day["contributionCount"])
            for week in calendar["weeks"]
            for day in week["contributionDays"]
        ]
        calendar_days = days
        current, longest = streak_stats(days)
    except (urllib.error.HTTPError, KeyError, TypeError, urllib.error.URLError) as error:
        print(f"warn: contribution calendar unavailable ({error}); keeping previous streak")

    by_repo = []
    for repo in repos:
        total = sum(languages.get(repo["name"], {}).values())
        by_repo.append(
            {
                "name": repo["name"],
                "stars": repo.get("stargazers_count", 0),
                "forks": repo.get("forks_count", 0),
                "language": repo.get("language"),
                "bytes": total,
                "pushed_at": repo.get("pushed_at"),
                "description": repo.get("description"),
            }
        )

    return {
        "user": {
            "login": user["login"],
            "name": user.get("name"),
            "public_repos": user.get("public_repos", 0),
            "followers": user.get("followers", 0),
            "following": user.get("following", 0),
            "created_at": user.get("created_at"),
            "avatar_url": user.get("avatar_url"),
        },
        "stars": sum(repo["stars"] for repo in by_repo),
        "contributions_1y": contributions,
        "current_streak": current,
        "longest_streak": longest,
        "repos": by_repo,
        "languages_by_repo": languages,
        "calendar_days": calendar_days,
    }


def streak_stats(days: list[tuple[str, int]]) -> tuple[int, int]:
    longest = 0
    run = 0
    for _date, count in days:
        if count > 0:
            run += 1
            longest = max(longest, run)
        else:
            run = 0

    seq = list(days)
    # Today is still open. An empty today does not break the streak.
    if seq and seq[-1][1] == 0:
        seq = seq[:-1]
    current = 0
    for _date, count in reversed(seq):
        if count > 0:
            current += 1
        else:
            break
    return current, longest


def language_totals(languages_by_repo: dict[str, dict[str, int]]) -> list[dict]:
    totals: dict[str, int] = defaultdict(int)
    for counts in languages_by_repo.values():
        for name, amount in counts.items():
            totals[name] += amount
    grand = sum(totals.values()) or 1
    ranked = sorted(totals.items(), key=lambda item: (-item[1], item[0]))
    return [
        {"name": name, "bytes": amount, "percent": round(100 * amount / grand, 1)}
        for name, amount in ranked
    ]


def activity_mix(repos: list[dict]) -> list[dict]:
    """Byte share of public repositories, grouped by what the repos actually are."""
    web = {"prive-travel", "Logistic-Company", "architectural-vision", "Task-Handler"}
    whatsapp = {"whatsApp-comment"}
    patterns = {"Abstract-Factory", "Factory-method", "Mediator_Patern"}
    buckets = {
        "Web apps": 0,
        "WhatsApp API": 0,
        "C": 0,
        "Python": 0,
        "Other": 0,
    }
    for repo in repos:
        amount = repo["bytes"]
        name = repo["name"]
        language = repo.get("language")
        if name in web:
            buckets["Web apps"] += amount
        elif name in whatsapp:
            buckets["WhatsApp API"] += amount
        elif name in patterns:
            buckets["Other"] += amount
        elif language == "C" or name.startswith("C-") or name.startswith("Common-Core"):
            buckets["C"] += amount
        elif language == "Python":
            buckets["Python"] += amount
        else:
            buckets["Other"] += amount
    grand = sum(buckets.values()) or 1
    rows = []
    for name in ("Web apps", "WhatsApp API", "C", "Python", "Other"):
        amount = buckets[name]
        percent = round(100 * amount / grand, 1)
        if name == "Other" and percent < 1:
            continue
        rows.append({"name": name, "bytes": amount, "percent": percent})
    return rows


def fetch_views(login: str, previous: dict) -> dict:
    url = f"https://komarev.com/ghpvc/?username={login}&style=flat&label=profile+views"
    cached = previous.get("views") or {}
    try:
        raw = fetch_bytes(url).decode("utf-8", "replace")
        # The badge renders the count as the last text node.
        digits = []
        for chunk in raw.split("<text"):
            if ">" not in chunk:
                continue
            text = chunk.split(">", 1)[1].split("<", 1)[0].strip()
            if text.isdigit():
                digits.append(int(text))
        if not digits:
            raise ValueError("no count in komarev badge")
        return {"count": digits[-1], "source": "komarev", "stale": False}
    except (urllib.error.URLError, urllib.error.HTTPError, ValueError, TimeoutError) as error:
        print(f"warn: profile views unavailable ({error}); using last committed count")
        if "count" in cached:
            return {"count": cached["count"], "source": cached.get("source", "cache"), "stale": True}
        return {"count": None, "source": "unavailable", "stale": True}


def is_background(r: int, g: int, b: int) -> bool:
    spread = max(r, g, b) - min(r, g, b)
    average = (r + g + b) / 3
    return spread < 28 and average > 196


def portrait_paths(avatar: bytes) -> str:
    image = Image.open(__import__("io").BytesIO(avatar)).convert("RGB")
    cols = rows = 76
    small = image.resize((cols, rows), Image.Resampling.BOX)
    pixels = small.load()
    buckets: dict[tuple[int, int, int], list[tuple[int, int]]] = defaultdict(list)
    for y in range(rows):
        for x in range(cols):
            r, g, b = pixels[x, y]
            if is_background(r, g, b):
                continue
            key = (r // 16 * 16, g // 16 * 16, b // 16 * 16)
            buckets[key].append((x, y))
    if not buckets:
        return ""
    xs = [x for cells in buckets.values() for x, _y in cells]
    ys = [y for cells in buckets.values() for _x, y in cells]
    min_x, max_x = min(xs), max(xs)
    min_y, max_y = min(ys), max(ys)
    span = max(max_x - min_x, max_y - min_y, 1)
    origin_x, origin_y = 292, 336
    scale = 340 / span
    cell = max(2.2, scale * 0.72)
    parts = ['<g shape-rendering="crispEdges">']
    for (r, g, b), cells in sorted(buckets.items()):
        commands = []
        for x, y in cells:
            px = origin_x + (x - (min_x + max_x) / 2) * scale
            py = origin_y + (y - (min_y + max_y) / 2) * scale
            commands.append(f"M{px:.1f} {py:.1f}h{cell:.1f}v{cell:.1f}h-{cell:.1f}z")
        color = f"#{r:02x}{g:02x}{b:02x}"
        parts.append(f'<path fill="{color}" d="{"".join(commands)}"/>')
    parts.append("</g>")
    return "".join(parts)


def sphere_frames() -> str:
    count = 210
    golden = math.pi * (3 - math.sqrt(5))
    base = []
    for index in range(count):
        y = 1 - (index / (count - 1)) * 2
        radius = math.sqrt(max(0, 1 - y * y))
        theta = golden * index
        base.append((math.cos(theta) * radius, y, math.sin(theta) * radius))

    frames = 24
    duration = 3.2
    cx, cy = 292, 336
    chunks = []
    for frame in range(frames):
        angle = 2 * math.pi * frame / frames
        ca, sa = math.cos(angle), math.sin(angle)
        layers: dict[str, list[str]] = {"back": [], "mid": [], "front": []}
        tilt = 0.42
        ct, st = math.cos(tilt), math.sin(tilt)
        for x, y, z in base:
            xr = x * ca + z * sa
            zr = -x * sa + z * ca
            yr = y * ct - zr * st
            zr2 = y * st + zr * ct
            depth = (zr2 + 1) / 2
            persp = 0.62 + depth * 0.55
            px = cx + xr * 132 * persp
            py = cy + yr * 132 * persp
            size = 1.5 + depth * 2.3
            command = f"M{px:.1f} {py:.1f}h{size:.1f}v{size:.1f}h-{size:.1f}z"
            if depth < 0.34:
                layers["back"].append(command)
            elif depth < 0.67:
                layers["mid"].append(command)
            else:
                layers["front"].append(command)
        if frame == 0:
            values = "1;" + ";".join(["0"] * (frames - 1)) + ";1"
        else:
            values = ";".join("1" if index == frame else "0" for index in range(frames)) + ";0"
        times = [index / frames for index in range(frames)] + [1]
        key_times = ";".join(f"{tick:.4f}" for tick in times)
        colors = {"back": "#1D4E89", "mid": "#22D3EE", "front": "#E9D5FF"}
        body = "".join(
            f'<path fill="{colors[name]}" d="{"".join(commands)}"/>'
            for name, commands in layers.items()
            if commands
        )
        chunks.append(
            f'<g opacity="0">{body}'
            f'<animate attributeName="opacity" values="{values}" keyTimes="{key_times}" '
            f'dur="{duration}s" repeatCount="indefinite" calcMode="discrete"/>'
            f"</g>"
        )
    return "".join(chunks)


def bracket_points() -> list[tuple[float, float]]:
    def stroke(x0: float, y0: float, x1: float, y1: float, thickness: int = 2) -> list[tuple[float, float]]:
        points = []
        length = math.hypot(x1 - x0, y1 - y0)
        steps = max(2, int(length / 7))
        dx, dy = x1 - x0, y1 - y0
        norm = math.hypot(dx, dy) or 1
        nx, ny = -dy / norm, dx / norm
        for step in range(steps + 1):
            t = step / steps
            x = x0 + dx * t
            y = y0 + dy * t
            for offset in range(-thickness, thickness + 1):
                points.append((x + nx * offset * 3.1, y + ny * offset * 3.1))
        return points

    points: list[tuple[float, float]] = []
    points += stroke(70, 18, 16, 110, 3)
    points += stroke(16, 110, 70, 202, 3)
    points += stroke(150, 16, 92, 204, 2)
    points += stroke(176, 18, 230, 110, 3)
    points += stroke(230, 110, 176, 202, 3)
    # Center the cloud on the visual panel.
    mean_x = sum(x for x, _y in points) / len(points)
    mean_y = sum(y for _x, y in points) / len(points)
    return [(x - mean_x + 292, y - mean_y + 336) for x, y in points]


def bracket_group() -> str:
    points = bracket_points()
    pink = "".join(f"M{x:.1f} {y:.1f}h2.4v2.4h-2.4z" for x, y in points)
    cyan = "".join(f"M{x + 3:.1f} {y:.1f}h2.4v2.4h-2.4z" for x, y in points)
    return (
        f'<g>'
        f'<animateTransform attributeName="transform" type="rotate" '
        f'from="0 292 336" to="360 292 336" dur="7.5s" repeatCount="indefinite"/>'
        f'<path fill="{CYAN}" opacity="0.85" d="{cyan}"/>'
        f'<path fill="{PINK}" d="{pink}"/>'
        f"</g>"
    )


def scene_group(content: str, values: str, key_times: str, dur: str = "16", opacity: str = "0") -> str:
    return (
        f'<g opacity="{opacity}">{content}'
        f'<animate attributeName="opacity" values="{values}" keyTimes="{key_times}" '
        f'dur="{dur}s" repeatCount="indefinite"/>'
        f"</g>"
    )


def header_svg(profile: dict, avatar: bytes) -> str:
    lines = profile["lines"]
    portrait = portrait_paths(avatar)
    # Portrait holds the start of the loop so a frozen first frame still shows the avatar.
    portrait_scene = scene_group(
        portrait,
        "1;1;0;0;0;0;1",
        "0;0.26;0.34;0.60;0.68;0.92;1",
        opacity="1",
    )
    sphere_scene = scene_group(
        sphere_frames(),
        "0;0;1;1;0;0",
        "0;0.28;0.36;0.58;0.66;1",
    )
    bracket_scene = scene_group(
        bracket_group(),
        "0;0;1;1;0;0",
        "0;0.60;0.68;0.90;0.98;1",
    )

    rows = []
    top = 132
    gap = 48
    for index, line in enumerate(lines):
        y = top + index * gap
        rows.append(
            f'<text x="596" y="{y}" fill="{MUTED}" font-family="{FONT}" font-size="13">{esc(line["key"])}</text>'
        )
        for offset, part in enumerate(str(line["value"]).split("\n")):
            rows.append(
                f'<text x="760" y="{y + offset * 18}" fill="{GREEN}" font-family="{FONT}" font-size="15">{esc(part)}</text>'
            )

    height = 640
    body = f"""
<defs>
  <linearGradient id="bezel" x1="0" y1="0" x2="1" y2="1">
    <stop offset="0" stop-color="#1B2740"/>
    <stop offset="0.5" stop-color="#121A2C"/>
    <stop offset="1" stop-color="#1A1430"/>
  </linearGradient>
  <linearGradient id="panelGlow" x1="0" y1="0" x2="0" y2="1">
    <stop offset="0" stop-color="#13203A"/>
    <stop offset="1" stop-color="{PANEL}"/>
  </linearGradient>
  <clipPath id="mapClip">
    <rect x="40" y="112" width="500" height="430" rx="8"/>
  </clipPath>
  <filter id="soft" x="-20%" y="-20%" width="140%" height="140%">
    <feGaussianBlur stdDeviation="1.2"/>
  </filter>
</defs>
<rect width="{W}" height="{height}" rx="18" fill="{BG}"/>
<rect x="8" y="8" width="{W - 16}" height="{height - 16}" rx="16" fill="url(#bezel)" stroke="{LINE}"/>
<circle cx="36" cy="36" r="6.5" fill="#FF5F57"/>
<circle cx="58" cy="36" r="6.5" fill="#FEBC2E"/>
<circle cx="80" cy="36" r="6.5" fill="#28C840"/>
<text x="{W / 2}" y="41" text-anchor="middle" fill="{MUTED}" font-family="{FONT}" font-size="14">profile.sh --live</text>
<circle cx="{W - 92}" cy="36" r="4" fill="{GREEN}">
  <animate attributeName="opacity" values="1;0.25;1" dur="1.6s" repeatCount="indefinite"/>
</circle>
<text x="{W - 80}" y="41" fill="{GREEN}" font-family="{FONT}" font-size="12">LIVE</text>
<rect x="24" y="68" width="528" height="500" rx="10" fill="url(#panelGlow)" stroke="{LINE}"/>
<rect x="566" y="68" width="510" height="500" rx="10" fill="{PANEL}" stroke="{LINE}"/>
<text x="44" y="98" fill="{CYAN}" font-family="{FONT}" font-size="13" font-weight="700" letter-spacing="1.4">VISUAL.MAP</text>
<text x="532" y="98" text-anchor="end" fill="{MUTED}" font-family="{FONT}" font-size="11">76×76 · 3 CYCLES</text>
<path d="M48 120h16M48 120v16M524 120h-16M524 120v16M48 530h16M48 530v-16M524 530h-16M524 530v-16" fill="none" stroke="{CYAN_DIM}" stroke-width="1.4" opacity="0.7"/>
<g clip-path="url(#mapClip)">
  <g opacity="0.18" stroke="#1E3A5F">
    <path d="M40 200H540M40 280H540M40 360H540M40 440H540M140 112V542M240 112V542M340 112V542M440 112V542"/>
  </g>
  {portrait_scene}
  {sphere_scene}
  {bracket_scene}
  <rect x="40" y="112" width="500" height="10" fill="{CYAN}" opacity="0.05">
    <animate attributeName="y" values="112;520;112" dur="4.8s" repeatCount="indefinite"/>
  </rect>
</g>
<text x="586" y="98" fill="{PURPLE}" font-family="{FONT}" font-size="13" font-weight="700" letter-spacing="1.4">SYSTEM.INFO</text>
{''.join(rows)}
<circle cx="596" cy="530" r="4.5" fill="{GREEN}">
  <animate attributeName="opacity" values="1;0.35;1" dur="1.8s" repeatCount="indefinite"/>
</circle>
<text x="610" y="535" fill="{GREEN}" font-family="{FONT}" font-size="13" letter-spacing="0.8">ALL SYSTEMS NORMAL</text>
<text x="1048" y="535" text-anchor="end" fill="{MUTED}" font-family="{FONT}" font-size="11">stdout · public</text>
"""
    return svg_doc(
        W,
        height,
        body,
        "profile.sh --live",
        "Terminal card for Drini, a full-stack developer. The visual map cycles an avatar, a sphere, and brackets.",
    )


def marquee_svg(profile: dict, public_repos: int) -> str:
    items = list(profile["marquee_items"])
    items.append(f"{public_repos} PUBLIC REPOS")
    phrase = "    ·    ".join(items) + "    ·    "
    # lengthAdjust forces the rendered width so the loop seam does not depend on the font.
    text_width = round(len(phrase) * 8.7)
    duration = max(22, text_width / 55)
    height = 46
    body = f"""
<defs>
  <linearGradient id="edge" x1="0" y1="0" x2="1" y2="0">
    <stop offset="0" stop-color="white" stop-opacity="0"/>
    <stop offset="0.05" stop-color="white" stop-opacity="1"/>
    <stop offset="0.95" stop-color="white" stop-opacity="1"/>
    <stop offset="1" stop-color="white" stop-opacity="0"/>
  </linearGradient>
  <mask id="edgeMask"><rect width="{W}" height="{height}" fill="url(#edge)"/></mask>
</defs>
<rect width="{W}" height="{height}" rx="10" fill="{CARD}" stroke="{LINE}"/>
<g mask="url(#edgeMask)">
  <g>
    <animateTransform attributeName="transform" type="translate" from="0 0" to="-{text_width:.1f} 0" dur="{duration:.1f}s" repeatCount="indefinite"/>
    <text y="29" fill="{GREEN}" font-family="{FONT}" font-size="14" letter-spacing="0.6" textLength="{text_width}" lengthAdjust="spacing">{esc(phrase)}</text>
    <text x="{text_width}" y="29" fill="{GREEN}" font-family="{FONT}" font-size="14" letter-spacing="0.6" textLength="{text_width}" lengthAdjust="spacing">{esc(phrase)}</text>
  </g>
</g>
"""
    return svg_doc(W, height, body, "Stack marquee", "Scrolling list of technologies found in the public repositories.")


def _typing_frames(phrases: list[str]) -> list[tuple[str, float]]:
    """(text, seconds). The first frame is the full title and stays up long enough to read."""
    frames: list[tuple[str, float]] = [(phrases[0], 5.0)]
    for index, phrase in enumerate(phrases):
        if index == 0:
            continue
        step = 2 if len(phrase) > 18 else 1
        sizes = list(range(step, len(phrase) + 1, step))
        if not sizes or sizes[-1] != len(phrase):
            sizes.append(len(phrase))
        for size in sizes:
            frames.append((phrase[:size] + "▌", 0.14))
        frames.append((phrase, 2.4))
    # Type the title again so the loop joins the opening hold instead of jumping.
    phrase = phrases[0]
    step = 2 if len(phrase) > 18 else 1
    sizes = list(range(step, len(phrase) + 1, step))
    if sizes and sizes[-1] == len(phrase):
        sizes = sizes[:-1]
    for size in sizes:
        frames.append((phrase[:size] + "▌", 0.14))
    return frames


def _opacity_animation(index: int, start: float, end: float, last: bool) -> tuple[str, str]:
    """Discrete opacity that is 1 only on [start, end).

    The off key has to sit at `end`. Putting the second 1 at the end of the
    slot and the 0 at t=1 holds every finished frame visible for the rest of
    the loop, which stacks the lines on top of each other.
    """
    if index == 0:
        values = "1;0;0"
        times = [0.0, end, 1.0]
    elif last:
        values = "0;1;1"
        times = [0.0, start, 1.0]
    else:
        values = "0;1;0;0"
        times = [0.0, start, end, 1.0]
    rounded = [round(tick, 4) for tick in times]
    rounded[0] = 0.0
    rounded[-1] = 1.0
    for cursor in range(1, len(rounded)):
        if rounded[cursor] <= rounded[cursor - 1]:
            rounded[cursor] = min(1.0, round(rounded[cursor - 1] + 0.0001, 4))
    rounded[-1] = 1.0
    if any(rounded[cursor] <= rounded[cursor - 1] for cursor in range(1, len(rounded))):
        raise SystemExit(f"typing keyTimes are not increasing: {rounded}")
    return values, ";".join(f"{tick:.4f}" for tick in rounded)


def typing_svg(profile: dict) -> str:
    phrases = profile["taglines"]
    width, height = 860, 64
    frames = _typing_frames(phrases)
    duration = sum(seconds for _text, seconds in frames)
    groups = []
    cursor = 0.0
    for index, (text, seconds) in enumerate(frames):
        start = cursor / duration
        cursor += seconds
        end = cursor / duration
        last = index == len(frames) - 1
        values, key_times = _opacity_animation(index, start, end if not last else 1.0, last)
        # Base opacity is the still frame: the full title, everything else hidden.
        # Renderers that skip SMIL keep that, so a screenshot still reads cleanly.
        opacity = "1" if index == 0 else "0"
        groups.append(
            f'<g opacity="{opacity}">'
            f'<animate attributeName="opacity" values="{values}" keyTimes="{key_times}" '
            f'dur="{duration:.2f}s" repeatCount="indefinite" calcMode="discrete"/>'
            f'<text x="{width / 2}" y="42" text-anchor="middle" fill="{PURPLE}" '
            f'font-family="{FONT}" font-size="28" font-weight="700">{esc(text)}</text>'
            f"</g>"
        )
    body = f'<rect width="{width}" height="{height}" fill="{BG}"/>' + "".join(groups)
    return svg_doc(width, height, body, phrases[0], "Typing line with the role and the public stack.")


def button_svg(label: str, fill: str, stroke: str, glyph: str, text_fill: str = "#FFFFFF") -> str:
    width, height = 176, 46
    body = f"""
<rect x="2" y="2" width="172" height="42" rx="21" fill="none" stroke="{stroke}" stroke-width="2" opacity="0.45">
  <animate attributeName="opacity" values="0.25;0.95;0.25" dur="2.4s" repeatCount="indefinite"/>
</rect>
<rect x="6" y="6" width="164" height="34" rx="17" fill="{fill}" stroke="{stroke}"/>
<g transform="translate(22 13)">{glyph}</g>
<text x="96" y="29" text-anchor="middle" fill="{text_fill}" font-family="{FONT}" font-size="14" font-weight="700">{esc(label)}</text>
"""
    return svg_doc(width, height, body, label, f"{label} button")


def github_glyph() -> str:
    return (
        '<circle cx="10" cy="10" r="9" fill="none" stroke="#E8EEF9" stroke-width="1.6"/>'
        '<circle cx="10" cy="10" r="2" fill="#4ADE80"/>'
        '<path d="M10 4v4M10 12v4M4 10h4M12 10h4" stroke="#C4B5FD" stroke-width="1.3"/>'
    )


def linkedin_glyph() -> str:
    return '<rect x="2" y="2" width="16" height="16" rx="2" fill="#FFFFFF"/><text x="5" y="15" fill="#0A66C2" font-family="ui-monospace,monospace" font-size="12" font-weight="700">in</text>'


def instagram_glyph() -> str:
    return '<rect x="2" y="2" width="16" height="16" rx="4" fill="none" stroke="#FFFFFF" stroke-width="1.6"/><circle cx="10" cy="10" r="3.2" fill="none" stroke="#FFFFFF" stroke-width="1.4"/><circle cx="14.2" cy="5.8" r="1" fill="#FFFFFF"/>'


def views_svg(views: dict) -> str:
    count = views.get("count")
    label = "—" if count is None else str(count)
    width = 250
    height = 36
    body = f"""
<rect width="{width}" height="{height}" rx="18" fill="{CARD}" stroke="#3B2F63"/>
<text x="18" y="23" fill="{MUTED}" font-family="{FONT}" font-size="12">profile views</text>
<text x="{width - 18}" y="23" text-anchor="end" fill="{PURPLE}" font-family="{FONT}" font-size="14" font-weight="700">{esc(label)}</text>
"""
    extra = f'data-count="{esc(label)}" data-source="{esc(views.get("source", ""))}"'
    return svg_doc(width, height, body, "profile views", "Profile view count cached from the public badge.", extra)


def icon_glyphs(kind: str, cx: float, cy: float) -> str:
    if kind == "ts":
        return f'<text x="{cx}" y="{cy + 6}" text-anchor="middle" fill="#FFFFFF" font-family="{FONT}" font-size="18" font-weight="700">TS</text>'
    if kind == "react":
        rings = []
        for angle in (0, 60, 120):
            rings.append(
                f'<ellipse cx="{cx}" cy="{cy}" rx="16" ry="6.2" fill="none" stroke="#61DAFB" stroke-width="1.6" transform="rotate({angle} {cx} {cy})"/>'
            )
        rings.append(f'<circle cx="{cx}" cy="{cy}" r="2.5" fill="#61DAFB"/>')
        return "".join(rings)
    if kind == "vite":
        return (
            f'<polygon points="{cx+2},{cy-16} {cx-6},{cy-1} {cx+1},{cy-1} {cx-4},{cy+16} {cx+8},{cy+1} {cx+1},{cy+1}" fill="#FFD028"/>'
            f'<polygon points="{cx+2},{cy-16} {cx+8},{cy+1} {cx+1},{cy+1}" fill="#646CFF"/>'
        )
    if kind == "next":
        return f'<text x="{cx}" y="{cy + 7}" text-anchor="middle" fill="#FFFFFF" font-family="{FONT}" font-size="22" font-weight="700">N</text>'
    if kind == "tailwind":
        return (
            f'<path d="M{cx-20} {cy+2}c5-12 10-12 15 0s10 12 15 0c5-12 10-12 15 0" fill="none" stroke="#38BDF8" stroke-width="2.4" stroke-linecap="round"/>'
            f'<path d="M{cx-20} {cy+10}c5-8 10-8 15 0s10 8 15 0c5-8 10-8 15 0" fill="none" stroke="#67E8F9" stroke-width="2" stroke-linecap="round" opacity="0.8"/>'
        )
    if kind == "tanstack":
        return f'<polygon points="{cx},{cy-16} {cx-16},{cy+12} {cx+16},{cy+12}" fill="none" stroke="#FF4154" stroke-width="2.4"/><circle cx="{cx}" cy="{cy+2}" r="3" fill="#FF4154"/>'
    if kind == "node":
        return f'<polygon points="{cx},{cy-16} {cx+14},{cy-8} {cx+14},{cy+8} {cx},{cy+16} {cx-14},{cy+8} {cx-14},{cy-8}" fill="none" stroke="#BBF7D0" stroke-width="2"/>'
    if kind == "nest":
        return f'<path d="M{cx-12} {cy+10}l12-18 12 18" fill="none" stroke="#FFFFFF" stroke-width="2.3"/><path d="M{cx-7} {cy+10}l7-10 7 10" fill="none" stroke="#FFE4E6" stroke-width="2"/>'
    if kind == "python":
        return f'<circle cx="{cx-6}" cy="{cy-4}" r="8" fill="#FFD43B"/><circle cx="{cx+6}" cy="{cy+5}" r="8" fill="#4B8BBE"/>'
    if kind == "c":
        return f'<text x="{cx}" y="{cy + 8}" text-anchor="middle" fill="#FFFFFF" font-family="{FONT}" font-size="26" font-weight="700">C</text>'
    if kind == "docker":
        boxes = []
        for col, row in ((0, 1), (1, 1), (2, 1), (1, 0), (2, 0), (3, 0)):
            boxes.append(
                f'<rect x="{cx - 16 + col * 8}" y="{cy - 8 + row * 8}" width="7" height="7" rx="1" fill="#FFFFFF"/>'
            )
        boxes.append(f'<path d="M{cx-18} {cy+12}h28c4 0 8-3 8-7" fill="none" stroke="#FFFFFF" stroke-width="1.6"/>')
        return "".join(boxes)
    if kind == "git":
        return (
            f'<circle cx="{cx}" cy="{cy-10}" r="3.2" fill="#FFFFFF"/>'
            f'<circle cx="{cx-8}" cy="{cy+8}" r="3.2" fill="#FFFFFF"/>'
            f'<circle cx="{cx+8}" cy="{cy+8}" r="3.2" fill="#FFFFFF"/>'
            f'<path d="M{cx} {cy-7}v8M{cx-1} {cy+1}l-6 5M{cx+1} {cy+1}l6 5" fill="none" stroke="#FFFFFF" stroke-width="1.6"/>'
        )
    return ""


def stack_svg() -> str:
    icons = [
        ("TypeScript", "#3178C6", "ts"),
        ("React", "#0B1B2B", "react"),
        ("Vite", "#1C1230", "vite"),
        ("Next.js", "#111111", "next"),
        ("Tailwind", "#0B2538", "tailwind"),
        ("TanStack", "#1A1014", "tanstack"),
        ("Node.js", "#14532D", "node"),
        ("NestJS", "#9F1239", "nest"),
        ("Python", "#1E3A5F", "python"),
        ("C", "#312E81", "c"),
        ("Docker", "#0C4A6E", "docker"),
        ("Git", "#7C2D12", "git"),
    ]
    width, height = W, 310
    tile = 86
    gap_x = 28
    row_w = 6 * tile + 5 * gap_x
    start_x = (width - row_w) / 2
    parts = [
        f'<rect width="{width}" height="{height}" rx="16" fill="{BG}"/>',
        f'<rect x="8" y="8" width="{width - 16}" height="{height - 16}" rx="14" fill="{CARD}" stroke="{LINE}"/>',
        f'<text x="{width / 2}" y="42" text-anchor="middle" fill="{CYAN}" font-family="{FONT}" font-size="13" letter-spacing="1.6">STACK · FROM THE PUBLIC REPOS</text>',
    ]
    for index, (label, fill, kind) in enumerate(icons):
        row, col = divmod(index, 6)
        x = start_x + col * (tile + gap_x)
        y = 64 + row * 116
        cx, cy = x + tile / 2, y + tile / 2
        parts.append(f'<rect x="{x}" y="{y}" width="{tile}" height="{tile}" rx="18" fill="{fill}" stroke="#334155"/>')
        parts.append(icon_glyphs(kind, cx, cy - 2))
        parts.append(
            f'<text x="{cx}" y="{y + tile + 20}" text-anchor="middle" fill="{TEXT}" font-family="{FONT}" font-size="12">{esc(label)}</text>'
        )
    return svg_doc(width, height, "".join(parts), "Stack", "Technologies taken from package manifests and repository languages.")


def radar_svg(title: str, subtitle: str, rows: list[dict], accent: str, scale: str = "linear") -> str:
    width, height = 520, 500
    cx, cy = 260, 270
    radius = 118
    count = max(3, len(rows))
    labels = list(rows)
    while len(labels) < count:
        labels.append({"name": "", "percent": 0})

    def point(index: int, factor: float) -> tuple[float, float]:
        angle = -math.pi / 2 + index * 2 * math.pi / count
        return cx + math.cos(angle) * radius * factor, cy + math.sin(angle) * radius * factor

    rings = []
    for step in (0.25, 0.5, 0.75, 1):
        poly = " ".join(f"{point(i, step)[0]:.1f},{point(i, step)[1]:.1f}" for i in range(count))
        rings.append(f'<polygon points="{poly}" fill="none" stroke="#1E3A5F" stroke-width="1"/>')
    spokes = []
    for index in range(count):
        x, y = point(index, 1)
        spokes.append(f'<line x1="{cx}" y1="{cy}" x2="{x:.1f}" y2="{y:.1f}" stroke="#1E3A5F"/>')
    def magnitude(percent: float) -> float:
        if scale == "sqrt":
            return math.sqrt(max(percent, 0)) / 10
        return percent / 100

    shape = " ".join(
        f"{point(i, max(0.04, magnitude(rows[i]['percent'])))[0]:.1f},{point(i, max(0.04, magnitude(rows[i]['percent'])))[1]:.1f}"
        for i in range(len(rows))
    )
    dots = []
    texts = []
    for index, row in enumerate(rows):
        x, y = point(index, max(0.04, magnitude(row["percent"])))
        dots.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="3.5" fill="{accent}"/>')
        lx, ly = point(index, 1.18)
        anchor = "middle"
        if lx < cx - 30:
            anchor = "end"
        elif lx > cx + 30:
            anchor = "start"
        texts.append(
            f'<text x="{lx:.1f}" y="{ly:.1f}" text-anchor="{anchor}" fill="{TEXT}" font-family="{FONT}" font-size="12">{esc(row["name"])}</text>'
            f'<text x="{lx:.1f}" y="{ly + 15:.1f}" text-anchor="{anchor}" fill="{accent}" font-family="{FONT}" font-size="11">{row["percent"]:.1f}%</text>'
        )
    body = f"""
<rect width="{width}" height="{height}" rx="16" fill="{BG}"/>
<rect x="8" y="8" width="{width - 16}" height="{height - 16}" rx="14" fill="{CARD}" stroke="{LINE}"/>
<text x="{width / 2}" y="40" text-anchor="middle" fill="{TEXT}" font-family="{FONT}" font-size="16">{esc(title)}</text>
<text x="{width / 2}" y="62" text-anchor="middle" fill="{MUTED}" font-family="{FONT}" font-size="11">{esc(subtitle)}</text>
{''.join(rings)}
{''.join(spokes)}
<polygon points="{shape}" fill="{accent}" opacity="0.28" stroke="{accent}" stroke-width="2"/>
{''.join(dots)}
{''.join(texts)}
"""
    return svg_doc(width, height, body, title, subtitle)


def card_stats(data: dict) -> str:
    user = data["user"]
    created = user.get("created_at") or ""
    joined = created[:7] if created else "—"
    tiles = [
        (str(user["public_repos"]), "public repos"),
        (str(data["contributions_1y"] if data["contributions_1y"] is not None else "—"), "contributions, 1y"),
        (str(user["followers"]), "followers"),
        (str(data["stars"]), "stars"),
        (str(data["current_streak"] if data["current_streak"] is not None else "—"), "current streak, days"),
        (str(data["longest_streak"] if data["longest_streak"] is not None else "—"), "longest streak, days"),
    ]
    width, height = 680, 300
    parts = [
        f'<rect width="{width}" height="{height}" rx="16" fill="{BG}"/>',
        f'<rect x="8" y="8" width="{width - 16}" height="{height - 16}" rx="14" fill="{CARD}" stroke="{LINE}"/>',
        f'<text x="32" y="48" fill="{TEXT}" font-family="{FONT}" font-size="18">{esc(user["login"])}</text>',
        f'<text x="{width - 32}" y="48" text-anchor="end" fill="{MUTED}" font-family="{FONT}" font-size="12">GitHub since {esc(joined)}</text>',
    ]
    for index, (value, label) in enumerate(tiles):
        col, row = index % 3, index // 3
        x = 32 + col * 214
        y = 118 + row * 84
        parts.append(f'<text x="{x}" y="{y}" fill="{GREEN}" font-family="{FONT}" font-size="34" font-weight="700">{esc(value)}</text>')
        parts.append(f'<text x="{x}" y="{y + 24}" fill="{MUTED}" font-family="{FONT}" font-size="12">{esc(label)}</text>')
    return svg_doc(width, height, "".join(parts), "GitHub statistics", "Public repository counts, stars, followers, and the past-year contribution streak.")


def card_languages(rows: list[dict]) -> str:
    width, height = 680, 300
    shown = rows[:6]
    parts = [
        f'<rect width="{width}" height="{height}" rx="16" fill="{BG}"/>',
        f'<rect x="8" y="8" width="{width - 16}" height="{height - 16}" rx="14" fill="{CARD}" stroke="{LINE}"/>',
        f'<text x="32" y="46" fill="{TEXT}" font-family="{FONT}" font-size="16">Top languages</text>',
        f'<text x="{width - 32}" y="46" text-anchor="end" fill="{MUTED}" font-family="{FONT}" font-size="12">share of public code</text>',
    ]
    colors = ["#3178C6", "#A5B4FC", "#38BDF8", "#4ADE80", "#F0ABFC", "#FBBF24"]
    bar_left, bar_max = 168, 360
    for index, row in enumerate(shown):
        y = 84 + index * 34
        width_px = max(2, bar_max * row["percent"] / 100)
        parts.append(f'<text x="32" y="{y}" fill="{TEXT}" font-family="{FONT}" font-size="13">{esc(row["name"])}</text>')
        parts.append(f'<rect x="{bar_left}" y="{y - 12}" width="{bar_max}" height="10" rx="5" fill="#132033"/>')
        parts.append(f'<rect x="{bar_left}" y="{y - 12}" width="{width_px:.1f}" height="10" rx="5" fill="{colors[index % len(colors)]}"/>')
        parts.append(
            f'<text x="{bar_left + bar_max + 16}" y="{y}" fill="{MUTED}" font-family="{FONT}" font-size="12">{row["percent"]:.1f}%</text>'
        )
    return svg_doc(width, height, "".join(parts), "Top languages", "Language share by bytes across public repositories.")


def wrap_text(text: str, limit: int, max_lines: int) -> list[str]:
    words = text.split()
    lines: list[str] = []
    current = ""
    for word in words:
        trial = word if not current else f"{current} {word}"
        if len(trial) <= limit:
            current = trial
            continue
        if current:
            lines.append(current)
        current = word
    if current:
        lines.append(current)
    if len(lines) <= max_lines:
        return lines
    head = lines[: max_lines - 1]
    tail = " ".join(lines[max_lines - 1 :])
    if len(tail) > limit:
        tail = tail[: limit - 1].rstrip() + "…"
    head.append(tail)
    return head


def project_card(project: dict, meta: dict | None) -> str:
    width, height = 520, 210
    stars = meta["stars"] if meta else 0
    language = (meta or {}).get("language") or "—"
    blurb_lines = wrap_text(project["blurb"], 56, 3)
    parts = [
        f'<rect width="{width}" height="{height}" rx="16" fill="{BG}"/>',
        f'<rect x="8" y="8" width="{width - 16}" height="{height - 16}" rx="14" fill="{CARD}" stroke="{LINE}"/>',
        f'<text x="28" y="46" fill="{TEXT}" font-family="{FONT}" font-size="18">{esc(project["repo"])}</text>',
        f'<text x="{width - 28}" y="46" text-anchor="end" fill="{PURPLE}" font-family="{FONT}" font-size="12">{esc(language)}</text>',
        f'<text x="28" y="74" fill="{CYAN}" font-family="{FONT}" font-size="13">{esc(project["stack"])}</text>',
    ]
    for index, line in enumerate(blurb_lines):
        parts.append(
            f'<text x="28" y="{108 + index * 20}" fill="{MUTED}" font-family="{FONT}" font-size="13">{esc(line)}</text>'
        )
    parts.append(
        f'<text x="28" y="184" fill="{GREEN}" font-family="{FONT}" font-size="13">★ {esc(stars)}</text>'
        f'<text x="{width - 28}" y="184" text-anchor="end" fill="{MUTED}" font-family="{FONT}" font-size="12">public</text>'
    )
    return svg_doc(
        width,
        height,
        "".join(parts),
        project["repo"],
        project["blurb"],
    )


def slug(name: str) -> str:
    cleaned: list[str] = []
    for char in name.lower():
        if char.isalnum():
            cleaned.append(char)
        elif not cleaned or cleaned[-1] != "-":
            cleaned.append("-")
    return "".join(cleaned).strip("-")


def write(path: Path, content: str) -> None:
    if "<svg" in content:
        ET.fromstring(content.encode())
    path.write_text(content)


def main() -> None:
    profile = load_json(PROFILE_PATH)
    projects = load_json(PROJECTS_PATH)
    login = profile["login"]
    token = github_token()
    print(f"fetching public data for {login}")
    data = fetch_profile(login, token)
    views = fetch_views(login, previous_stats())
    languages = language_totals(data["languages_by_repo"])
    activity = activity_mix(data["repos"])
    avatar_url = data["user"].get("avatar_url") or "https://avatars.githubusercontent.com/u/199660693"
    avatar = fetch_bytes(avatar_url, token)

    repo_index = {repo["name"]: repo for repo in data["repos"]}
    stats = {
        "fetched_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "user": data["user"],
        "stars": data["stars"],
        "contributions_1y": data["contributions_1y"],
        "current_streak": data["current_streak"],
        "longest_streak": data["longest_streak"],
        "views": views,
        "languages": languages,
        "activity": activity,
    }
    # The contribution calendar is only an input to the streak. Keep stats.json small.
    STATS_PATH.write_text(json.dumps(stats, indent=2) + "\n")

    write(ASSETS / "header.svg", header_svg(profile, avatar))
    write(ASSETS / "marquee.svg", marquee_svg(profile, data["user"]["public_repos"]))
    write(ASSETS / "typing.svg", typing_svg(profile))
    write(
        ASSETS / "btn-github.svg",
        button_svg("GitHub", "#111827", "#A78BFA", github_glyph()),
    )
    write(
        ASSETS / "btn-linkedin.svg",
        button_svg("LinkedIn", "#0A66C2", "#93C5FD", linkedin_glyph()),
    )
    write(
        ASSETS / "btn-instagram.svg",
        button_svg("Instagram", "#3B0764", "#F0ABFC", instagram_glyph()),
    )
    write(ASSETS / "views.svg", views_svg(views))
    write(ASSETS / "stack.svg", stack_svg())
    write(
        ASSETS / "radar-activity.svg",
        radar_svg("Activity mix", "Share of code in public repositories", activity, "#4ADE80"),
    )
    write(
        ASSETS / "radar-languages.svg",
        radar_svg(
            "Language ranking",
            "Square-root scale · percents are the real share",
            languages[:5],
            "#C4B5FD",
            scale="sqrt",
        ),
    )
    stat_card_data = {
        "user": data["user"],
        "stars": data["stars"],
        "contributions_1y": data["contributions_1y"],
        "current_streak": data["current_streak"],
        "longest_streak": data["longest_streak"],
    }
    write(ASSETS / "card-stats.svg", card_stats(stat_card_data))
    write(ASSETS / "card-languages.svg", card_languages(languages))
    for project in projects:
        write(
            ASSETS / f"project-{slug(project['repo'])}.svg",
            project_card(project, repo_index.get(project["repo"])),
        )

    header = (ASSETS / "header.svg").read_text()
    for token_name in ("animate", "animateTransform", "VISUAL.MAP", "ALL SYSTEMS NORMAL"):
        if token_name not in header:
            raise SystemExit(f"header.svg is missing {token_name}")
    print(
        json.dumps(
            {
                "repos": data["user"]["public_repos"],
                "followers": data["user"]["followers"],
                "stars": data["stars"],
                "contributions_1y": data["contributions_1y"],
                "current_streak": data["current_streak"],
                "longest_streak": data["longest_streak"],
                "views": views,
                "languages": languages[:6],
                "activity": activity,
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
