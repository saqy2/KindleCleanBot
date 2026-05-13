"""Phase 1: Scan novel and produce a structure fingerprint JSON.

The fingerprint is <3KB and captures chapter patterns, wrapper characters,
duplicates, anomalies, ad detection with position context - enough for AI
to make decisions without reading the entire book.
"""

import re
from pathlib import Path
from collections import Counter

from .ad_patterns import AD_PATTERNS as AD_DETECTORS
from .patterns_engine import get_automaton, validate_regex


# Broad chapter patterns to detect (order matters - try specific first)
CHAPTER_PATTERNS = [
    re.compile(r"(第[0-9一二三四五六七八九十零〇百千两]+章)", re.UNICODE),
    re.compile(r"(Chapter\s+\d+)", re.IGNORECASE),
    re.compile(r"(Section\s+\d+)", re.IGNORECASE),
    re.compile(r"(\d+[\.\、\s]+[^\d\s]{2,30})", re.UNICODE),
]

AD_PATTERNS = re.compile(r"[（(](?:求|感谢|为盟主|第二更|第三更|大章|二合一)[^）)]*[）)]|\d+字大章")
WRAPPER_CHARS = set("=#*-~+_^")

MAX_AD_SAMPLES = 3

# ── Volume detection ──
VOLUME_PATTERN = re.compile(r"第[0-9一二三四五六七八九十零〇百千两 ]+[卷部篇]", re.UNICODE)

# ── Chinese numeral → integer ──
_CN_NUM = {
    "零": 0, "〇": 0, "一": 1, "二": 2, "三": 3, "四": 4,
    "五": 5, "六": 6, "七": 7, "八": 8, "九": 9, "十": 10,
    "百": 100, "千": 1000, "万": 10000, "两": 2,
}

def cn_to_int(cn: str) -> int | None:
    """Convert Chinese or Arabic numeral string to integer. Returns None on failure."""
    if not cn:
        return None
    cn = cn.strip()
    try:
        return int(cn)
    except ValueError:
        pass
    # Chinese numeral parsing: "十二"→12, "二十"→20, "一百二十三"→123
    result = 0
    seg = 0
    for ch in cn:
        if ch in ("十", "百", "千", "万"):
            unit = _CN_NUM[ch]
            if seg == 0:
                seg = 1
            seg *= unit
            if unit >= 10:
                result += seg
                seg = 0
        else:
            seg += _CN_NUM.get(ch, 0)
    result += seg
    return result if result > 0 else None


def _strip_wrappers(text: str) -> str:
    """Strip common wrapper characters from both ends of a string."""
    s = text.strip()
    while s and s[0] in WRAPPER_CHARS:
        s = s[1:]
    while s and s[-1] in WRAPPER_CHARS:
        s = s[:-1]
    return s.strip()


def read_file(filepath: str) -> str:
    path = Path(filepath)
    raw = path.read_bytes()
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        pass
    try:
        if raw[:2] == b'\xff\xfe':
            return raw.decode("utf-16-le")
        if raw[:2] == b'\xfe\xff':
            return raw.decode("utf-16-be")
    except UnicodeDecodeError:
        pass
    try:
        return raw.decode("gbk")
    except UnicodeDecodeError:
        pass
    return raw.decode("utf-8", errors="replace")


def _sample_lines(text: str) -> dict:
    all_lines = text.splitlines()
    total = len(all_lines)
    first_n = min(300, total)
    position_samples = []
    for pct in range(10, 100, 10):
        start = int(total * pct / 100)
        end = min(start + 100, total)
        position_samples.append((start, end))
    seen_ranges = [(0, first_n)]
    for s, e in position_samples:
        merged = False
        for i, (ps, pe) in enumerate(seen_ranges):
            if s <= pe and e >= ps:
                seen_ranges[i] = (min(ps, s), max(pe, e))
                merged = True
                break
        if not merged:
            seen_ranges.append((s, e))
    sampled = []
    for s, e in sorted(seen_ranges):
        sampled.extend(all_lines[s:e])
    return {
        "total_lines": total,
        "sampled_lines": len(sampled),
        "first_lines": all_lines[:first_n],
        "sampled": sampled,
        "all_lines": all_lines,
    }


def _classify_position(line_idx: int, sampled_lines: list[str], chapter_indices: set[int]) -> dict:
    """Classify a matched ad line's position context by inspecting actual surrounding lines."""
    total = len(sampled_lines)
    prev_line = sampled_lines[line_idx - 1].strip() if line_idx > 0 else ""
    next_line = sampled_lines[line_idx + 1].strip() if line_idx < total - 1 else ""

    near_chapter = any(
        abs(line_idx - ci) <= 2 and ci != line_idx
        for ci in chapter_indices
    )
    standalone = not prev_line and not next_line
    embedded = bool(prev_line) and bool(next_line) and not near_chapter

    return {
        "near_chapter": near_chapter,
        "standalone": standalone,
        "embedded": embedded,
    }


def _detect_ads(sampled_lines: list[str], all_lines: list[str] | None, chapter_indices: set[int]) -> dict:
    """Single-pass AC scan for full-file counts + sampled position context."""
    A = get_automaton()
    results = {}
    total_hits = 0

    # ── Full-file count: one inlined AC pass on all lines ──
    counts: dict[str, int] = {key: 0 for key in AD_DETECTORS}
    if all_lines:
        for line in all_lines:
            matched: set[str] = set()
            for end, (key, trigger) in A.iter(line):
                matched.add(key)
            for key in matched:
                if validate_regex(key, line):
                    counts[key] += 1

        # ── Fallback: trigger-less types scanned by full regex ──
        for key, det in AD_DETECTORS.items():
            if det.get("triggers"):
                continue
            pat = det["re"]
            for line in all_lines:
                if pat.search(line):
                    counts[key] += 1

    # ── Sampled position context ──
    sampled_counts: dict[str, int] = {key: 0 for key in AD_DETECTORS}
    sampled_samples: dict[str, list] = {key: [] for key in AD_DETECTORS}
    sampled_pos: dict[str, dict] = {}

    for line_idx, line in enumerate(sampled_lines):
        matched_keys: set[str] = set()
        for end, (key, trigger) in A.iter(line):
            matched_keys.add(key)
        for ad_key in matched_keys:
            if not validate_regex(ad_key, line):
                continue
            sc = sampled_counts[ad_key]
            if sc < MAX_AD_SAMPLES:
                vals = AD_DETECTORS[ad_key]["re"].findall(line)
                if vals:
                    for v in vals:
                        sampled_samples[ad_key].append(str(v).strip())
                        if len(sampled_samples[ad_key]) >= MAX_AD_SAMPLES:
                            break
            sampled_counts[ad_key] += 1
            if ad_key not in sampled_pos:
                sampled_pos[ad_key] = {"near_chapter": 0, "standalone": 0, "embedded": 0}
            pos = _classify_position(line_idx, sampled_lines, chapter_indices)
            for pk in sampled_pos[ad_key]:
                if pos.get(pk):
                    sampled_pos[ad_key][pk] += 1

        # ── Fallback: trigger-less types in sampled lines ──
        for key, det in AD_DETECTORS.items():
            if det.get("triggers"):
                continue
            pat = det["re"]
            for line_idx, line in enumerate(sampled_lines):
                if pat.search(line):
                    sc = sampled_counts[key]
                    if sc < MAX_AD_SAMPLES:
                        vals = pat.findall(line)
                        if vals:
                            for v in vals:
                                sampled_samples[key].append(str(v).strip())
                                if len(sampled_samples[key]) >= MAX_AD_SAMPLES:
                                    break
                    sampled_counts[key] += 1
                    if key not in sampled_pos:
                        sampled_pos[key] = {"near_chapter": 0, "standalone": 0, "embedded": 0}
                    pos = _classify_position(line_idx, sampled_lines, chapter_indices)
                    for pk in sampled_pos[key]:
                        if pos.get(pk):
                            sampled_pos[key][pk] += 1

    # ── Build results ──
    for key, det in AD_DETECTORS.items():
        full_count = counts.get(key, 0)
        sc = sampled_counts.get(key, 0)
        final = full_count or sc
        if final > 0:
            total_hits += final
            results[key] = {
                "total": full_count,
                "sampled": sc,
                "level": det["level"],
                "label": det["label"],
                "samples": sampled_samples.get(key, [])[:MAX_AD_SAMPLES],
                "position": sampled_pos.get(key, {"near_chapter": 0, "standalone": 0, "embedded": 0}),
            }

    return {
        "total_hits": total_hits,
        "details": results,
    }


def _detect_chapter_pattern(sampled_lines: list[str]) -> str | None:
    counts = Counter()
    for line in sampled_lines:
        stripped = line.strip()
        for pat in CHAPTER_PATTERNS:
            if pat.search(stripped):
                counts[pat.pattern] += 1
                break
    if counts:
        return counts.most_common(1)[0][0]
    return None


def _format_dup_keys(duplicates: dict, volume_labels: dict[int, str]) -> str:
    """Format duplicate keys like V1C17×2, V3C5×2 into readable text."""
    parts = []
    for key, count in sorted(duplicates.items()):
        m = re.match(r"V(\d+)C(\d+)", key)
        if m:
            v, c = m.groups()
            vlabel = volume_labels.get(int(v), f"第{v}卷")
            if vlabel:
                label = f"《{vlabel[:20]}》第{c}章" if vlabel else f"第{v}卷-第{c}章"
            else:
                label = f"V{v}C{c}"
        else:
            label = key
        parts.append(f"{label}×{count}")
    return "; ".join(parts[:8]) + ("..." if len(parts) > 8 else "")


def _extract_chapter_info(lines: list[str], chapter_pattern: str) -> dict:
    pat = re.compile(chapter_pattern, re.UNICODE)
    chapters = []
    seen_pairs = Counter()  # "V{vol}C{ch}" → count
    titles = []
    chapter_line_indices = set()

    current_volume = 1
    volume_labels: dict[int, str] = {1: ""}

    for lineno, line in enumerate(lines):
        stripped = line.strip()
        if not stripped:
            continue

        # ── Detect volume ──
        unwrapped = _strip_wrappers(stripped)
        # Volume lines are short (<40 chars) and not chapter headers
        if len(unwrapped) <= 40:
            vm = VOLUME_PATTERN.search(unwrapped)
            if vm and not pat.search(unwrapped):
                vm_text = vm.group(0)
                vm_num_match = re.search(r"[\d一二三四五六七八九十零〇百千两]+", vm_text)
                if vm_num_match:
                    vn = cn_to_int(vm_num_match.group(0))
                    if vn is not None:
                        current_volume = vn
                        volume_labels.setdefault(vn, unwrapped[:40])

        # ── Detect chapter ──
        m = pat.search(stripped)
        if not m:
            continue
        chapter_line_indices.add(lineno)
        ch_text = m.group(0)
        num_match = re.search(r"[\d一二三四五六七八九十零〇百千两]+", ch_text)
        if not num_match:
            continue
        ch_num_raw = num_match.group(0)
        ch_num = cn_to_int(ch_num_raw) or ch_num_raw  # fall back to string if conversion fails
        prefix_wrapper = ""
        suffix_wrapper = ""
        idx = stripped.find(ch_text)
        if idx > 0:
            prefix = stripped[:idx]
            if all(c in WRAPPER_CHARS for c in prefix):
                prefix_wrapper = prefix
        after = stripped[idx + len(ch_text):]
        if after:
            if all(c in WRAPPER_CHARS for c in after):
                suffix_wrapper = after
        ad_match = AD_PATTERNS.search(stripped)
        chapters.append({
            "lineno": lineno + 1,
            "number": ch_num,
            "number_raw": ch_num_raw,
            "volume": current_volume,
            "text": ch_text,
            "full_line": stripped,
            "prefix_wrapper": prefix_wrapper,
            "suffix_wrapper": suffix_wrapper,
            "has_ad": bool(ad_match),
        })
        pair_key = f"V{current_volume}C{ch_num}"
        seen_pairs[pair_key] += 1
        if len(titles) < 20:
            titles.append(stripped)

    duplicates = {k: v for k, v in seen_pairs.items() if v > 1}
    wrappers = set()
    for ch in chapters:
        if ch["prefix_wrapper"]:
            wrappers.add(f"{ch['prefix_wrapper']}...")
        if ch["suffix_wrapper"]:
            wrappers.add(f"...{ch['suffix_wrapper']}")

    anomalies = []
    if duplicates:
        dup_labels = _format_dup_keys(duplicates, volume_labels)
        anomalies.append(f"重复章节: {len(duplicates)} 处 ({dup_labels})")
    ad_count = sum(1 for c in chapters if c["has_ad"])
    if ad_count > 0:
        anomalies.append(f"广告后缀: {ad_count} 个章节标题含广告")
    if wrappers:
        anomalies.append(f"包裹字符: {', '.join(sorted(wrappers))}")

    # Per-volume chapter gap detection (avoid fake gaps across volumes)
    seen_volumes = set()
    vol_chapters: dict[int, list[int]] = {}
    for ch in chapters:
        v = ch["volume"]
        seen_volumes.add(v)
        if isinstance(ch["number"], int):
            vol_chapters.setdefault(v, []).append(ch["number"])
    if vol_chapters:
        gaps = []
        for v, nums in sorted(vol_chapters.items()):
            nums.sort()
            for i in range(1, len(nums)):
                if nums[i] - nums[i-1] > 1:
                    label = f"V{v}" if len(seen_volumes) > 1 else ""
                    gaps.append(f"{label}{nums[i-1]}→{nums[i]}")
        if gaps:
            anomalies.append(f"章节跳跃: {', '.join(gaps[:5])}" + ("..." if len(gaps) > 5 else ""))

    return {
        "total_chapters_detected": len(chapters),
        "samples": titles[:15],
        "wrappers": list(wrappers),
        "duplicates": duplicates,
        "anomalies": anomalies,
        "raw_chapters": chapters,
        "chapter_line_indices": chapter_line_indices,
        "volume_count": len(seen_volumes),
        "volume_labels": volume_labels,
    }


def scan(filepath: str) -> dict:
    text = read_file(filepath)
    sample_data = _sample_lines(text)

    chapter_pattern = _detect_chapter_pattern(sample_data["sampled"])
    if not chapter_pattern:
        chapter_pattern = _detect_chapter_pattern(sample_data["first_lines"])
    if not chapter_pattern:
        return {"error": "未检测到章节模式", "file_lines": sample_data["total_lines"]}

    ch_info = _extract_chapter_info(sample_data["all_lines"], chapter_pattern)
    ad_info = _detect_ads(sample_data["sampled"], sample_data["all_lines"], ch_info.get("chapter_line_indices", set()))

    fingerprint = {
        "file": {"path": filepath, "total_lines": sample_data["total_lines"]},
        "chapter_pattern": chapter_pattern,
        "chapters": {
            "total_detected": ch_info["total_chapters_detected"],
            "wrappers": ch_info["wrappers"],
            "duplicates": ch_info["duplicates"],
            "anomalies": ch_info["anomalies"],
            "samples": ch_info["samples"],
            "volume_count": ch_info.get("volume_count", 0),
            "volume_labels": ch_info.get("volume_labels", {}),
        },
        "ads": ad_info,
    }
    return fingerprint


def fingerprint_to_prompt(fingerprint: dict) -> str:
    if "error" in fingerprint:
        return f"文件扫描失败: {fingerprint['error']}"

    f = fingerprint
    parts = [
        f"文件: {f['file']['total_lines']} 行",
        f"章节模式: {f['chapter_pattern']}",
        f"检测到 {f['chapters']['total_detected']} 个章节",
    ]
    vc = f["chapters"].get("volume_count", 0)
    if vc > 1:
        parts.append(f"卷/部/篇: 检测到 {vc} 个（单级支持，嵌套取首级）")
    if f["chapters"]["wrappers"]:
        parts.append(f"章节包裹字符: {', '.join(f['chapters']['wrappers'])}")
    if f["chapters"]["duplicates"]:
        dup_info = _format_dup_keys(f["chapters"]["duplicates"], f["chapters"].get("volume_labels", {}))
        parts.append(f"重复章节: {dup_info}")
    if f["chapters"]["anomalies"]:
        for a in f["chapters"]["anomalies"]:
            parts.append(f"异常: {a}")

    # Ad content with position context
    ad_info = f.get("ads", {}).get("details", {})
    if ad_info:
        parts.append("\n📢 广告内容检测（分级可信度）:")
        for key, info in sorted(ad_info.items(), key=lambda x: x[1].get("level", "")):
            samples_str = "; ".join(info["samples"])
            pos = info.get("position", {})
            pos_str = f" 位置: 独立{pos.get('standalone', 0)}/章节旁{pos.get('near_chapter', 0)}/嵌入{pos.get('embedded', 0)}"
            total = info.get("total", 0)
            parts.append(f"  [{info['level']}] {info['label']}: 全量{total}处{pos_str} (样本: {samples_str})")
        all_keys = list(AD_DETECTORS.keys())
        not_detected = [AD_DETECTORS[k]["label"] for k in all_keys if k not in ad_info]
        if not_detected:
            parts.append(f"  未检测到: {', '.join(not_detected)}")

    parts.append("\n章节标题样本:")
    for s in f["chapters"]["samples"][:15]:
        parts.append(f"  {s}")

    return "\n".join(parts)
