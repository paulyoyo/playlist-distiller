"""Fuzzy matching of playlist tracks to local audio files."""
from __future__ import annotations

import hashlib
import json
import re
import subprocess
import sys
import termios
import tty
from pathlib import Path
from thefuzz import fuzz
from config import AUDIO_EXTENSIONS, DEFAULT_FUZZY_THRESHOLD, MIN_DURATION_SECONDS

PICKS_CACHE_DIR = Path(__file__).parent / ".pick_cache"

# Salvaje palette — truecolor ANSI escapes
_GOLDEN = "\033[38;2;234;216;47m"   # Golden Glow #EAD82F
_WISTERIA = "\033[38;2;197;153;226m"  # Wisteria #C599E2
_LILAC = "\033[38;2;137;86;186m"    # Deep Lilac #8956BA
_DIM = "\033[2m"
_RESET = "\033[0m"

# Max candidates to show when asking user to pick


def normalize(text: str) -> str:
    """Normalize text for comparison: lowercase, remove special chars."""
    text = text.lower()
    # Remove common suffixes that streaming adds but filenames won't have
    for suffix in ["(original mix)", "(extended mix)", "(radio edit)",
                   "- original mix", "- extended mix", "- radio edit",
                   "[original mix]", "[extended mix]", "[radio edit]"]:
        text = text.replace(suffix, "")
    # Remove feat./ft. variations for cleaner matching
    text = re.sub(r"\(?feat\.?\s.*?\)?", "", text)
    text = re.sub(r"\(?ft\.?\s.*?\)?", "", text)
    # Remove special characters, keep alphanumeric and spaces
    text = re.sub(r"[^\w\s]", " ", text)
    # Collapse whitespace
    text = re.sub(r"\s+", " ", text).strip()
    return text


def scan_audio_files(disk_path: str) -> list[Path]:
    """Recursively find all audio files on the given path."""
    root = Path(disk_path)
    if not root.exists():
        raise FileNotFoundError(f"Path not found: {disk_path}")

    audio_files = []
    print(f"{_LILAC}Scanning {disk_path} for audio files...{_RESET}")
    for f in root.rglob("*"):
        if f.suffix.lower() in AUDIO_EXTENSIONS and f.is_file():
            audio_files.append(f)

    print(f"{_WISTERIA}Found {len(audio_files)} audio files.{_RESET}")
    return audio_files


def _preview_file(file_path: str):
    """Play audio file immediately using macOS afplay."""
    try:
        proc = subprocess.Popen(
            ["afplay", file_path],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return proc
    except FileNotFoundError:
        subprocess.Popen(["open", file_path],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return None


def _kill_preview(proc):
    """Terminate a Quick Look preview process."""
    if proc and proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            proc.kill()


def _get_duration(file_path: Path) -> float | None:
    """Get audio duration in seconds via afinfo (macOS)."""
    try:
        out = subprocess.run(
            ["afinfo", str(file_path)],
            capture_output=True, text=True, timeout=5,
        )
        for line in out.stdout.splitlines():
            if "estimated duration:" in line.lower():
                return float(line.split(":")[-1].strip().split()[0])
    except (subprocess.TimeoutExpired, ValueError, OSError):
        pass
    return None


def _filter_short_files(candidates: list[tuple[Path, int]],
                        min_seconds: float) -> list[tuple[Path, int]]:
    """Remove candidates shorter than min_seconds."""
    kept = []
    for fp, score in candidates:
        dur = _get_duration(fp)
        if dur is None or dur >= min_seconds:
            kept.append((fp, score))
    return kept


def _picks_cache_path(playlist_name: str) -> Path:
    """Return cache file path for a playlist's pick session."""
    key = hashlib.md5(playlist_name.encode()).hexdigest()[:12]
    return PICKS_CACHE_DIR / f"{key}.json"


def _load_picks_cache(playlist_name: str) -> dict:
    """Load saved picks: {track_label: {"path": str|null, "score": int}}."""
    path = _picks_cache_path(playlist_name)
    if path.exists():
        try:
            return json.loads(path.read_text())
        except (json.JSONDecodeError, OSError):
            pass
    return {}


def _save_picks_cache(playlist_name: str, cache: dict):
    """Persist picks cache to disk."""
    PICKS_CACHE_DIR.mkdir(exist_ok=True)
    path = _picks_cache_path(playlist_name)
    path.write_text(json.dumps(cache, indent=2))


def _pick_candidate(track_label: str, candidates: list[tuple[Path, int]],
                    disk_root: str = "") -> tuple[Path, int] | None:
    """Interactive picker when multiple files match a track.

    candidates: list of (file_path, score), sorted by score descending.
    disk_root: volume path to strip from display.
    Returns selected (file_path, score) or None to skip.
    """
    # Limit to 10 options, numbered 1-9,0 matching keyboard layout
    candidates = candidates[:10]
    n = len(candidates)
    # Display labels: 1,2,...,9,0 — maps to candidate indices 0,1,...,8,9
    labels = [str((i + 1) % 10) for i in range(n)]

    print(f"\n  Multiple matches for: {track_label}")
    for i, (fp, sc) in enumerate(candidates):
        rel = str(fp.parent)
        if disk_root and rel.startswith(disk_root):
            rel = rel[len(disk_root):].lstrip("/")
        print(f"    {_GOLDEN}{labels[i]}. {fp.name}{_RESET}")
        if rel:
            print(f"       {_DIM}{rel}{_RESET}")

    # Build key-to-index map: '1'->0, '2'->1, ..., '9'->8, '0'->9
    key_map = {labels[i]: i for i in range(n)}

    preview_proc = None
    hint = ",".join(labels)
    print(f"    {_DIM}{hint} select, p+N preview, s skip, u undo, q quit & save{_RESET}")

    def _read_key():
        fd = sys.stdin.fileno()
        old = termios.tcgetattr(fd)
        try:
            tty.setraw(fd)
            return sys.stdin.read(1)
        finally:
            termios.tcsetattr(fd, termios.TCSADRAIN, old)

    while True:
        try:
            ch = _read_key()
        except (EOFError, KeyboardInterrupt):
            _kill_preview(preview_proc)
            print()
            return None

        if ch in ("\x03", "\x04", "q"):
            _kill_preview(preview_proc)
            print("  quit")
            return "QUIT"

        if ch == "s":
            _kill_preview(preview_proc)
            print("  skip")
            return None

        if ch == "u":
            _kill_preview(preview_proc)
            print("  undo")
            return "UNDO"

        if ch == "p":
            try:
                digit = _read_key()
                if digit in key_map:
                    idx = key_map[digit]
                    _kill_preview(preview_proc)
                    print(f"  previewing: {_WISTERIA}{candidates[idx][0].name}{_RESET}")
                    preview_proc = _preview_file(str(candidates[idx][0]))
            except (EOFError, KeyboardInterrupt):
                pass
            continue

        if ch in key_map:
            idx = key_map[ch]
            _kill_preview(preview_proc)
            print(f"  -> {_GOLDEN}{candidates[idx][0].name}{_RESET}")
            return candidates[idx]


def match_tracks(tracks: list[dict], audio_files: list[Path],
                 threshold: int = DEFAULT_FUZZY_THRESHOLD,
                 interactive: bool = True,
                 disk_path: str = "",
                 playlist_name: str = "") -> list[dict]:
    """Match playlist tracks to local files using fuzzy matching.

    When interactive=True and multiple files match above threshold,
    prompts the user to pick which version to use.

    Returns list of {artist, title, match_path, score} for found tracks,
    and {artist, title, match_path: None, score: 0} for missing ones.
    """
    # Pre-compute normalized filenames (stem only, no extension)
    file_index = []
    for f in audio_files:
        norm_name = normalize(f.stem)
        file_index.append((f, norm_name))

    results = []
    found = 0
    total = len(tracks)
    picks_needed = []  # (index, track_label, candidates) for interactive resolution

    # Phase 1: score all tracks
    for i, track in enumerate(tracks, 1):
        artist = track["artist"]
        title = track["title"]
        search_str = normalize(f"{artist} {title}")
        title_only = normalize(title)
        # Core title: strip parenthetical/bracket content (version, remix, edit info)
        # e.g. "La Plena (W Sound 05)" -> "La Plena"
        core_title = normalize(re.sub(r"\(.*?\)|\[.*?\]", "", title))

        candidates = []
        # Minimum title similarity to avoid matching different songs
        # by the same artist (e.g. "Ryan Castro - AMIGA" when looking
        # for "Ryan Castro - LA VILLA")
        title_min = max(50, threshold - 10)

        for file_path, norm_name in file_index:
            score_full = fuzz.token_set_ratio(search_str, norm_name)
            score_title = fuzz.token_set_ratio(title_only, norm_name)
            # Also check core title (without version/remix info) to catch
            # edits credited to different artists
            score_core = fuzz.token_set_ratio(core_title, norm_name) if core_title != title_only else 0
            best_title = max(score_title, score_core)
            score = max(score_full, int(best_title * 0.85))

            # Accept if: full match is good AND title matches,
            # OR title alone matches well (catches DJ edits with
            # different artist credits, misspelled tags, etc.)
            if (score >= threshold and best_title >= title_min) \
                    or best_title >= threshold:
                candidates.append((file_path, score))

        # Sort by score descending
        candidates.sort(key=lambda x: x[1], reverse=True)

        # Filter out short files (sound effects, jingles)
        if candidates:
            candidates = _filter_short_files(candidates, MIN_DURATION_SECONDS)

        result = {
            "artist": artist,
            "title": title,
            "match_path": None,
            "score": 0,
        }
        if "uri" in track:
            result["uri"] = track["uri"]
        if "tidal_id" in track:
            result["tidal_id"] = track["tidal_id"]

        if len(candidates) == 1:
            result["match_path"] = str(candidates[0][0])
            result["score"] = candidates[0][1]
            found += 1
        elif len(candidates) > 1:
            # Check if top score is clearly better (5+ points gap)
            if not interactive or (candidates[0][1] - candidates[1][1] >= 5):
                result["match_path"] = str(candidates[0][0])
                result["score"] = candidates[0][1]
                found += 1
            else:
                # Queue for interactive pick
                picks_needed.append((len(results), f"{artist} - {title}",
                                     candidates))

        results.append(result)
        print(f"\r  Matching: {i}/{total} ({found} found, {len(picks_needed)} need review)", end="", flush=True)

    print()  # newline after progress

    # Phase 2: interactive resolution
    if picks_needed:
        picks_cache = _load_picks_cache(playlist_name) if playlist_name else {}
        # Restore previously saved picks
        restored = 0
        unresolved = []
        for idx, label, candidates in picks_needed:
            if label in picks_cache:
                saved = picks_cache[label]
                if saved["path"]:
                    # Verify the saved file still exists
                    if Path(saved["path"]).exists():
                        results[idx]["match_path"] = saved["path"]
                        results[idx]["score"] = saved["score"]
                        found += 1
                        restored += 1
                        continue
                    else:
                        del picks_cache[label]
                else:
                    # Previously skipped
                    restored += 1
                    continue
            unresolved.append((idx, label, candidates))

        if restored:
            print(f"\n  Restored {restored} saved picks from previous session.")

        if unresolved:
            print(f"\n{'='*60}")
            print(f"  {len(unresolved)} tracks have multiple close matches — please pick:")
            print(f"  (Use p<N> to Quick Look preview, then select a number)")
            print(f"{'='*60}")

            pick_pos = 0
            while pick_pos < len(unresolved):
                idx, label, candidates = unresolved[pick_pos]
                print(f"\n  [{pick_pos + 1}/{len(unresolved)}]", end="")
                picked = _pick_candidate(label, candidates, disk_root=disk_path)
                if picked == "QUIT":
                    if playlist_name:
                        _save_picks_cache(playlist_name, picks_cache)
                        print(f"\n  Progress saved ({len(picks_cache)} picks). Resume next run.")
                    break
                if picked == "UNDO":
                    if pick_pos > 0:
                        pick_pos -= 1
                        prev_idx, prev_label = unresolved[pick_pos][0], unresolved[pick_pos][1]
                        if results[prev_idx]["match_path"]:
                            found -= 1
                        results[prev_idx]["match_path"] = None
                        results[prev_idx]["score"] = 0
                        picks_cache.pop(prev_label, None)
                    else:
                        print("  (nothing to undo)")
                    continue
                if picked:
                    results[idx]["match_path"] = str(picked[0])
                    results[idx]["score"] = picked[1]
                    found += 1
                    picks_cache[label] = {"path": str(picked[0]), "score": picked[1]}
                else:
                    picks_cache[label] = {"path": None, "score": 0}
                if playlist_name:
                    _save_picks_cache(playlist_name, picks_cache)
                pick_pos += 1
            else:
                # Completed all picks — clean up cache
                if playlist_name:
                    cache_path = _picks_cache_path(playlist_name)
                    if cache_path.exists():
                        cache_path.unlink()
                    print(f"\n  All picks resolved — cache cleared.")

        print(f"\n  Resolution complete. Total matched: {found}/{total}")

    return results
