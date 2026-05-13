"""Pattern matching engine: Aho-Corasick automaton + statistical features.

Performance:
  - AC automaton: single-pass full-text matching for all ad triggers (~100ms for 30MB)
  - Statistical features: lightweight per-line heuristics for unsupervised classification
"""

import re

import ahocorasick

from .ad_patterns import AD_PATTERNS

# ── zhon characters for Chinese detection ──
try:
    from zhon.hanzi import characters as ZHON_CHARS
except ImportError:
    ZHON_CHARS = (
        "\u4e00-\u9fff\u3400-\u4dbf\uf900-\ufaff"
        "\U00020000-\U0002a6df\U0002a700-\U0002b73f"
        "\U0002b740-\U0002b81f\U0002b820-\U0002ceaf"
    )

_DIGITS_RE = re.compile(r"\d")
_CHINESE_RE = re.compile(f"[{ZHON_CHARS}]")

# ── Global automaton singleton ──
_ac: ahocorasick.Automaton | None = None


def _build_ac() -> ahocorasick.Automaton:
    """Build Aho-Corasick automaton from all triggers across all ad types."""
    A = ahocorasick.Automaton()
    for key, info in AD_PATTERNS.items():
        triggers = info.get("triggers", [])
        if not triggers:
            continue  # No triggers → handled by caller's regex fallback
        for trigger in triggers:
            A.add_word(trigger, (key, trigger))
    A.make_automaton()
    return A


def get_automaton() -> ahocorasick.Automaton:
    """Return the global AC automaton (lazy init, cached)."""
    global _ac
    if _ac is None:
        _ac = _build_ac()
    return _ac


def ac_matches(text: str) -> list[tuple[str, str]]:
    """Run AC automaton on text, return [(ad_key, trigger_text), ...] deduplicated by key."""
    A = get_automaton()
    matched_keys: dict[str, str] = {}
    for end, (key, trigger) in A.iter(text):
        if key not in matched_keys:
            matched_keys[key] = trigger
    return [(k, v) for k, v in matched_keys.items()]


def validate_regex(ad_key: str, line: str) -> bool:
    """Confirm AC trigger with the full regex pattern for precision."""
    info = AD_PATTERNS.get(ad_key, {})
    pat = info.get("re")
    if pat is None:
        # For trigger-less types, test if they match anything
        return False
    return bool(pat.search(line))


def compute_line_features(line: str) -> dict[str, float | int] | None:
    """Compute statistical features for a single line."""
    stripped = line.strip()
    if not stripped:
        return None
    total = len(stripped)
    chinese = len(_CHINESE_RE.findall(stripped))
    digits = len(_DIGITS_RE.findall(stripped))
    return {
        "length": total,
        "chinese_ratio": chinese / total if total else 0,
        "digit_ratio": digits / total if total else 0,
    }


def auto_classify(features: dict | None) -> str | None:
    """Unsupervised classification based on statistical features."""
    if features is None:
        return None
    # High digit ratio + short line → L1 (phone, QQ, numeric ID)
    if features["digit_ratio"] > 0.3 and features["length"] < 20:
        return "L1"
    # Very low Chinese ratio → suspicious
    if features["chinese_ratio"] < 0.5 and features["length"] > 2:
        return "L2"
    return None
