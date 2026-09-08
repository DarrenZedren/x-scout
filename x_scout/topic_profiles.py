from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(slots=True)
class TopicProfile:
    name: str
    description: str
    wanted_terms: list[str]
    blocked_terms: list[str]
    activity_terms: list[str]
    query_groups: list[list[str]]


SAFE_SPAM_BLOCKS = [
    "ragebait", "rage bait", "giveaway", "follow for follow", "follow4follow",
    "airdrop", "dm me for", "guaranteed profit", "engagement farming",
]

DARREN_AVOID = [
    "politics", "political", "election", "democrat", "republican", "left wing", "right wing",
    "culture war", "woke", "anti woke", "immigration", "immigrant", "migrant", "border crisis",
    "religion", "religious", "christianity", "islam", "muslim", "murder", "murdered", "homicide",
    "true crime", "school shooting", "mass shooting", "outrage", "breaking news",
]

GENERIC_ACTIVITY = [
    "build", "building", "built", "make", "making", "made", "create", "creating", "created",
    "ship", "shipping", "shipped", "test", "testing", "tested", "design", "designing", "restore",
    "restoring", "repair", "coding", "coded", "develop", "developing", "shoot", "photograph",
    "train", "training", "cook", "working on", "experiment", "experimenting", "project", "prototype",
]

PRESETS: dict[str, TopicProfile] = {}


def _groups(terms: list[str], size: int = 7, max_groups: int = 4) -> list[list[str]]:
    unique: list[str] = []
    seen: set[str] = set()
    for term in terms:
        clean = re.sub(r"\s+", " ", term.strip())
        key = clean.lower()
        if clean and key not in seen:
            unique.append(clean)
            seen.add(key)
    return [unique[i:i + size] for i in range(0, min(len(unique), size * max_groups), size)] or [["technology"]]


def query_groups_for_terms(terms: list[str]) -> list[list[str]]:
    """Build live X query groups from the user's currently selected core signals."""
    return _groups(terms)


def _preset(name: str, description: str, wanted: list[str], blocked: list[str] | None = None,
            activity: list[str] | None = None) -> TopicProfile:
    p = TopicProfile(
        name=name,
        description=description,
        wanted_terms=wanted,
        blocked_terms=list(dict.fromkeys(SAFE_SPAM_BLOCKS + (blocked or []))),
        activity_terms=list(dict.fromkeys(GENERIC_ACTIVITY + (activity or []))),
        query_groups=_groups(wanted),
    )
    PRESETS[name] = p
    return p


_preset(
    "AI Builders",
    "AI-assisted coding and people actively building software, agents, automation, APIs, games and useful systems with tools such as ChatGPT, Grok, Claude and Cursor.",
    [
        "AI coding", "vibe coding", "vibecoding", "ChatGPT", "Grok", "Claude", "Cursor",
        "AI agents", "AI agent", "agentic", "MCP", "automation", "orchestration", "automated systems",
        "Python", "API", "APIs", "building in public", "AI builder", "game dev", "gamedev",
        "local AI", "LLM", "workflow", "software builder", "prompt engineering",
    ],
    DARREN_AVOID,
    ["coding", "prototype", "deploy", "agent", "workflow", "API", "Python"],
)

_preset(
    "Indie Game Dev",
    "Independent game developers sharing current builds, game design experiments, engines, art pipelines and devlogs.",
    ["indie game", "indie dev", "game dev", "gamedev", "Godot", "Unity", "Unreal Engine", "game design",
     "pixel art", "devlog", "game jam", "Steam", "level design", "procedural generation"],
    ["NFT game", "crypto game", "casino", "gambling"],
    ["devlog", "prototype", "playtest", "level design", "shipped"],
)

_preset(
    "Classic Cars",
    "Owners, restorers, mechanics and enthusiasts actively working on classic cars, restorations and old-school mechanical projects.",
    ["classic cars", "classic car", "car restoration", "restoration", "vintage cars", "muscle cars", "hot rod",
     "Chevrolet", "Ford", "Mopar", "garage build", "engine rebuild", "mechanic", "project car", "barn find"],
    ["car crash outrage", "road rage"],
    ["restore", "restoring", "repair", "rebuild", "garage", "project car", "engine"],
)

_preset(
    "Photography",
    "Photographers sharing current work, techniques, gear experiments, editing workflows and visual projects.",
    ["photography", "photographer", "street photography", "landscape photography", "portrait photography", "camera",
     "photo editing", "Lightroom", "Sony Alpha", "Canon", "Nikon", "Fujifilm", "photo walk", "behind the scenes"],
    ["paparazzi outrage"],
    ["shoot", "shot", "photograph", "editing", "photo walk", "project"],
)

_preset(
    "Food & Cooking",
    "Cooks, chefs and food creators sharing recipes, techniques, experiments and kitchen projects.",
    ["cooking", "recipe", "home cooking", "chef", "barbecue", "BBQ", "smoking meat", "baking", "kitchen",
     "meal prep", "food creator", "grilling", "sourdough", "restaurant kitchen"],
    [],
    ["cook", "cooking", "bake", "baking", "recipe", "grill", "smoke"],
)

_preset(
    "Fitness",
    "People sharing practical training, strength, running and fitness progress without outrage or miracle-product spam.",
    ["strength training", "weight training", "running", "fitness", "gym", "powerlifting", "bodyweight training",
     "mobility", "conditioning", "training plan", "workout", "endurance"],
    ["miracle supplement", "guaranteed weight loss", "fat burner"],
    ["train", "training", "workout", "lift", "run", "program"],
)


CATEGORY_HINTS: list[tuple[list[str], str]] = [
    (["ai", "chatgpt", "grok", "claude", "cursor", "agent", "mcp", "api", "automation", "coding", "python"], "AI Builders"),
    (["game", "godot", "unity", "unreal", "pixel art", "steam", "gamedev"], "Indie Game Dev"),
    (["classic car", "vintage car", "restoration", "muscle car", "hot rod", "chevy", "ford", "mopar"], "Classic Cars"),
    (["photo", "photography", "camera", "lightroom", "portrait", "landscape"], "Photography"),
    (["cook", "cooking", "recipe", "barbecue", "bbq", "baking", "chef", "food"], "Food & Cooking"),
    (["fitness", "gym", "strength", "running", "workout", "powerlifting", "training"], "Fitness"),
]

STOPWORDS = {
    "about", "after", "again", "also", "among", "because", "been", "being", "build", "building", "could", "from",
    "have", "into", "just", "like", "more", "people", "really", "share", "sharing", "that", "their", "them", "they",
    "this", "those", "using", "want", "with", "would", "your", "things", "stuff", "current", "active",
}


def analyse_description(description: str, avoid_text: str = "") -> TopicProfile:
    text = re.sub(r"\s+", " ", description.strip())
    lower = text.lower()
    matched_names: list[str] = []
    for hints, preset_name in CATEGORY_HINTS:
        if any(h in lower for h in hints):
            matched_names.append(preset_name)

    wanted: list[str] = []
    activity: list[str] = list(GENERIC_ACTIVITY)
    blocked: list[str] = list(SAFE_SPAM_BLOCKS)
    for name in matched_names:
        p = PRESETS[name]
        wanted.extend(p.wanted_terms)
        activity.extend(p.activity_terms)
        blocked.extend(p.blocked_terms)

    # Preserve useful phrases the user actually typed.
    chunks = [c.strip(" .") for c in re.split(r"[,;/]|\band\b", text, flags=re.IGNORECASE)]
    for chunk in chunks:
        if 2 <= len(chunk.split()) <= 5 and 3 <= len(chunk) <= 45:
            wanted.append(chunk)

    words = re.findall(r"[A-Za-z0-9+#.-]{3,}", text)
    for word in words:
        if word.lower() not in STOPWORDS:
            wanted.append(word)

    if avoid_text.strip():
        blocked.extend([x.strip() for x in re.split(r"[,;\n]", avoid_text) if x.strip()])

    # Keep the profile compact enough for sensible X queries.
    dedup_wanted: list[str] = []
    seen: set[str] = set()
    for term in wanted:
        key = term.lower().strip()
        if key and key not in seen:
            dedup_wanted.append(term.strip())
            seen.add(key)
        if len(dedup_wanted) >= 28:
            break

    if not dedup_wanted:
        dedup_wanted = ["technology", "creator", "project"]

    label = " + ".join(matched_names[:2]) if matched_names else "Custom Scout"
    return TopicProfile(
        name=label,
        description=text or "Custom X Scout topic profile",
        wanted_terms=dedup_wanted,
        blocked_terms=list(dict.fromkeys(blocked)),
        activity_terms=list(dict.fromkeys(activity)),
        query_groups=_groups(dedup_wanted),
    )


def preset_profiles() -> list[TopicProfile]:
    return [PRESETS[name] for name in PRESETS]
