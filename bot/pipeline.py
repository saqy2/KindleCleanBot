"""Phase 3: Orchestrate the full processing pipeline.

Clean the novel → convert with kaf-cli → return output files.
"""

import re
import subprocess
from pathlib import Path

from .config import get_config


def run(input_path: str, recipe: dict, output_dir: str) -> dict:
    """Run full pipeline: clean + convert. Returns dict with clean_stats, outputs, bookname, author."""
    from .cleaner import clean
    from .scanner import read_file

    input_path = Path(input_path)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    # Determine book name
    bookname = recipe.get("bookname") or input_path.stem
    bookname = re.sub(r'[_=#*\-]+$', '', bookname).strip()

    # Step 1: Read file once, pass text to cleaner
    text = read_file(str(input_path))
    clean_path = output_dir / f"{input_path.stem}_clean.txt"
    clean_stats = clean(str(input_path), str(clean_path), recipe, text=text)

    # Step 2: Convert with kaf-cli
    config = get_config()
    kaf_cli = config.get("kaf_cli", {}).get("path", "./bin/kaf-cli")
    if not Path(kaf_cli).exists():
        for loc in ["/usr/local/bin/kaf-cli", "/app/bin/kaf-cli", "./bin/kaf-cli", "kaf-cli"]:
            if Path(loc).exists():
                kaf_cli = loc
                break

    formats = recipe.get("format") or "epub"
    if isinstance(formats, str):
        formats = [formats]

    output_base = output_dir / bookname
    author = recipe.get("author") or "Unknown"
    lang = recipe.get("lang") or "zh"
    chapter_pattern = recipe.get("chapter_pattern") or ""

    results = []
    for fmt in formats:
        out_path = output_dir / f"{bookname}.{fmt}"
        cmd = [
            kaf_cli,
            "-filename", str(clean_path),
            "-format", fmt,
            "-lang", lang,
            "-author", author,
            "-bookname", bookname,
            "-out", str(output_base),
            "-tips=false",
        ]
        if chapter_pattern and fmt != "all":
            cmd.extend(["-match", chapter_pattern])

        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=300,
        )

        if proc.returncode == 0:
            out_file = Path(f"{output_base}.{fmt}")
            if out_file.exists():
                results.append({
                    "format": fmt,
                    "path": str(out_file),
                    "size_mb": round(out_file.stat().st_size / (1024 * 1024), 2),
                })
        else:
            results.append({
                "format": fmt,
                "error": proc.stderr.strip()[-200:] if proc.stderr else "Unknown error",
            })

    # Clean up intermediate file
    try:
        clean_path.unlink()
    except OSError:
        pass

    return {
        "clean_stats": clean_stats,
        "outputs": results,
        "bookname": bookname,
        "author": author,
    }
