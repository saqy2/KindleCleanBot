"""Apply cleaning operations to a novel file based on AI recipe.

Ad actions:
  delete           - Remove entire matching line (except chapter headers)
  keep             - No action
  standalone_only  - Remove only if line is isolated (surrounded by blanks)
  strip_title      - Strip matched ad pattern from chapter title lines only
"""

import re
from pathlib import Path

from .ad_patterns import AD_PATTERNS
from .patterns_engine import ac_matches, validate_regex
from .scanner import cn_to_int, _strip_wrappers as _unwrap, VOLUME_PATTERN as _VOLUME_RE


def _line_matches_ad_key(line: str, ad_actions: dict, strategy_filter: set[str]) -> str | None:
    """Return the ad key if line matches any pattern whose action is in strategy_filter.

    Uses AC automaton for fast trigger detection, then regex for precision.
    """
    stripped = line.strip()
    if not stripped:
        return None
    for ad_key, trigger in ac_matches(stripped):
        if ad_key in strategy_filter and validate_regex(ad_key, stripped):
            return ad_key
    # ── Fallback: trigger-less types matched by full regex ──
    for key, info in AD_PATTERNS.items():
        if info.get("triggers"):
            continue
        if key in strategy_filter and info["re"].search(stripped):
            return key
    return None


def _is_standalone(lines: list[str], idx: int) -> bool:
    """Check if line at idx is surrounded by empty lines."""
    prev_blank = idx == 0 or not lines[idx - 1].strip()
    next_blank = idx >= len(lines) - 1 or not lines[idx + 1].strip()
    return prev_blank and next_blank


def clean(input_path: str, output_path: str, recipe: dict, text: str | None = None) -> dict:
    """Apply cleaning operations and write cleaned file. Returns stats with ad_report.

    If text is provided, it is used directly and file reading is skipped.
    """
    if text is None:
        path = Path(input_path)
        raw = path.read_bytes()
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            try:
                text = raw.decode("gbk")
            except UnicodeDecodeError:
                text = raw.decode("utf-8", errors="replace")

    original_lines = text.splitlines()
    lines = list(original_lines)  # mutable copy
    stats = {"original_lines": len(lines), "operations": []}
    ch_pattern = recipe.get("chapter_pattern") or r"第\d+章"
    chapter_pat = re.compile(ch_pattern, re.UNICODE)
    ad_actions = recipe.get("ad_actions") or {}

    # ── Ad report tracking ──
    ad_report = {"deleted": [], "kept": [], "deleted_original": {}}

    # 0. Ad removal by strategy
    delete_keys = {k for k, v in ad_actions.items() if v == "delete"}
    standalone_keys = {k for k, v in ad_actions.items() if v == "standalone_only"}
    strip_title_keys = {k for k, v in ad_actions.items() if v == "strip_title"}

    if delete_keys or standalone_keys or strip_title_keys:
        ad_removed = 0
        new_lines = []
        for i, line in enumerate(lines):
            stripped = line.strip()
            if not stripped:
                new_lines.append(line)
                continue

            is_chapter = bool(chapter_pat.search(stripped))

            # Strategy: delete — remove entire line (but NOT chapter headers)
            if not is_chapter:
                ad_key = _line_matches_ad_key(line, ad_actions, delete_keys)
                if ad_key:
                    info = AD_PATTERNS.get(ad_key, {})
                    ad_report["deleted"].append({
                        "type": ad_key,
                        "label": info.get("label", ad_key),
                        "line": i + 1,
                        "matched": stripped[:80],
                    })
                    ad_report["deleted_original"][str(i + 1)] = line
                    ad_removed += 1
                    continue

            # Strategy: standalone_only — remove only if isolated
            if not is_chapter:
                ad_key = _line_matches_ad_key(line, ad_actions, standalone_keys)
                if ad_key:
                    if _is_standalone(lines, i):
                        info = AD_PATTERNS.get(ad_key, {})
                        ad_report["deleted"].append({
                            "type": ad_key,
                            "label": info.get("label", ad_key),
                            "line": i + 1,
                            "matched": stripped[:80],
                        })
                        ad_report["deleted_original"][str(i + 1)] = line
                        ad_removed += 1
                        continue
                    else:
                        # Kept: embedded in text, likely dialogue
                        info = AD_PATTERNS.get(ad_key, {})
                        ad_report["kept"].append({
                            "type": ad_key,
                            "label": info.get("label", ad_key),
                            "line": i + 1,
                            "matched": stripped[:80],
                            "reason": "嵌入正文，可能为剧情内容",
                        })

            # Strategy: strip_title — strip ad pattern from chapter title lines
            if is_chapter:
                ad_key = _line_matches_ad_key(line, ad_actions, strip_title_keys)
                if ad_key:
                    for key in strip_title_keys:
                        pat_info = AD_PATTERNS.get(key, {})
                        pat = pat_info.get("re")
                        if pat:
                            stripped = pat.sub("", stripped).strip()
                    info = AD_PATTERNS.get(ad_key, {})
                    ad_report["deleted"].append({
                        "type": ad_key,
                        "label": info.get("label", ad_key),
                        "line": i + 1,
                        "matched": stripped[:80],
                    })
                    ad_report["deleted_original"][str(i + 1)] = line
                    new_lines.append(stripped)
                    ad_removed += 1
                    continue

            new_lines.append(line)

        lines = new_lines
        if ad_removed > 0:
            actions_summary = []
            if delete_keys:
                actions_summary.append(f"强制删除({','.join(sorted(delete_keys))})")
            if standalone_keys:
                actions_summary.append(f"独立行删除({','.join(sorted(standalone_keys))})")
            if strip_title_keys:
                actions_summary.append(f"标题去广告({','.join(sorted(strip_title_keys))})")
            stats["operations"].append(f"分级删广告: {ad_removed} 行 ({'; '.join(actions_summary)})")
        stats["ad_lines_removed"] = ad_removed

    # 1. Strip wrappers from chapter lines
    if recipe.get("strip_wrappers"):
        cleaned = 0
        new_lines = []
        for line in lines:
            stripped = line.strip()
            m = chapter_pat.search(stripped)
            if m:
                wrapper_chars = set("=#*-~+_")
                start = 0
                end = len(stripped)
                while start < end and stripped[start] in wrapper_chars:
                    start += 1
                while end > start and stripped[end - 1] in wrapper_chars:
                    end -= 1
                clean_line = stripped[start:end]
                if clean_line != stripped:
                    cleaned += 1
                new_lines.append(clean_line)
            else:
                new_lines.append(line)
        lines = new_lines
        stats["operations"].append(f"去除包裹字符: {cleaned} 行")
        stats["wrappers_stripped"] = cleaned

    # 2. Strip ad suffixes from chapter titles
    if recipe.get("strip_ad_suffixes"):
        ad_pat = re.compile(r"[（(](?:求|感谢|为盟主|第二更|第三更|大章|二合一|加更)[^）)]*[）)]|\d+字大章")
        stripped_count = 0
        new_lines = []
        for line in lines:
            if chapter_pat.search(line) and ad_pat.search(line):
                new_line = ad_pat.sub("", line).strip()
                stripped_count += 1
                new_lines.append(new_line)
            else:
                new_lines.append(line)
        lines = new_lines
        stats["operations"].append(f"去除广告后缀: {stripped_count} 行")
        stats["ad_suffixes_stripped"] = stripped_count

    # 3. Extra replacements
    if recipe.get("extra_replacements"):
        for rep in recipe["extra_replacements"]:
            pat = rep["pattern"]
            repl = rep.get("replacement", "")
            count = 0
            new_lines = []
            for line in lines:
                new_line, n = re.subn(pat, repl, line)
                count += n
                new_lines.append(new_line)
            lines = new_lines
            stats["operations"].append(f"额外替换 '{pat}': {count} 处")

    # 4. Deduplicate chapters (volume-aware)
    if recipe.get("deduplicate_chapters"):
        num_pat = re.compile(r"第([\d一二三四五六七八九十零〇百千两]+)章", re.UNICODE)
        current_volume = 1
        last_num = -1
        removed = 0
        new_lines = []
        for line in lines:
            stripped = line.strip()
            if not stripped:
                new_lines.append(line)
                continue

            # Check for volume line (short lines only, to avoid body text false positives)
            unwrapped = _unwrap(stripped)
            if len(unwrapped) <= 40:
                vm = _VOLUME_RE.search(unwrapped)
                if vm and not chapter_pat.search(unwrapped):
                    vn_match = re.search(r"[\d一二三四五六七八九十零〇百千两]+", vm.group(0))
                    if vn_match:
                        vn = cn_to_int(vn_match.group(0))
                        if vn is not None and vn != current_volume:
                            current_volume = vn
                            last_num = -1

            # Check for chapter line
            m = chapter_pat.search(stripped)
            if m:
                nm = num_pat.search(stripped)
                if nm:
                    ch_num = cn_to_int(nm.group(1))
                    if ch_num is not None:
                        if ch_num == last_num:
                            removed += 1
                            continue
                        last_num = ch_num

            new_lines.append(line)
        lines = new_lines
        stats["operations"].append(f"去除重复章节: {removed} 个")
        stats["duplicates_removed"] = removed

    stats["final_lines"] = len(lines)
    stats["ad_report"] = ad_report
    Path(output_path).write_text("\n".join(lines), encoding="utf-8")
    return stats
