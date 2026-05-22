"""Fuzzy matching of playlist tracks to local audio files."""
from __future__ import annotations

import re
import subprocess
import sys
import termios
import tty
from pathlib import Path
from thefuzz import fuzz
from config import AUDIO_EXTENSIONS, DEFAULT_FUZZY_THRESHOLD

# Max candidates to show when asking user to pick
MAX_CANDIDATES = 8


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
    print(f"Scanning {disk_path} for audio files...")
    for f in root.rglob("*"):
        if f.suffix.lower() in AUDIO_EXTENSIONS and f.is_file():
            audio_files.append(f)

    print(f"Found {len(audio_files)} audio files.")
    return audio_files


def _preview_file(file_path: str):
    """Open a file with macOS Quick Look (qlmanage) for audio preview."""
    try:
        proc = subprocess.Popen(
            ["qlmanage", "-p", file_path],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return proc
    except FileNotFoundError:
        print("    (qlmanage not available, trying open...)")
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


def _pick_candidate(track_label: str, candidates: list[tuple[Path, int]],
                    disk_root: str = "") -> tuple[Path, int] | None:
    """Interactive picker when multiple files match a track.

    candidates: list of (file_path, score), sorted by score descending.
    disk_root: volume path to strip from display.
    Returns selected (file_path, score) or None to skip.
    """
    print(f"\n  Multiple matches for: {track_label}")
    for i, (fp, sc) in enumerate(candidates, 1):
        rel = str(fp.parent)
        if disk_root and rel.startswith(disk_root):
            rel = rel[len(disk_root):].lstrip("/")
        print(f"    {i}. \033[1;36m{fp.name}\033[0m")
        if rel:
            print(f"       \033[2m{rel}\033[0m")

    n = len(candidates)
    preview_proc = None
    print(f"    \033[2m1-{n} select, p+N preview, s skip\033[0m")

    while True:
        try:
            fd = sys.stdin.fileno()
            old = termios.tcgetattr(fd)
            try:
                tty.setraw(fd)
                ch = sys.stdin.read(1)
            finally:
                termios.tcsetattr(fd, termios.TCSADRAIN, old)
        except (EOFError, KeyboardInterrupt):
            _kill_preview(preview_proc)
            print()
            return None

        if ch in ("\x03", "\x04"):  # Ctrl-C, Ctrl-D
            _kill_preview(preview_proc)
            print()
            return None

        if ch == "s":
            _kill_preview(preview_proc)
            print("  skip")
            return None

        # Preview: p then digit
        if ch == "p":
            try:
                fd = sys.stdin.fileno()
                old = termios.tcgetattr(fd)
                try:
                    tty.setraw(fd)
                    digit = sys.stdin.read(1)
                finally:
                    termios.tcsetattr(fd, termios.TCSADRAIN, old)
            except (EOFError, KeyboardInterrupt):
                _kill_preview(preview_proc)
                print()
                return None
            _kill_preview(preview_proc)
            try:
                idx = int(digit) - 1
                if 0 <= idx < n:
                    print(f"  previewing: {candidates[idx][0].name}")
                    preview_proc = _preview_file(str(candidates[idx][0]))
            except ValueError:
                pass
            continue

        # Direct selection: digit
        try:
            idx = int(ch) - 1
            if 0 <= idx < n:
                _kill_preview(preview_proc)
                print(f"  -> {candidates[idx][0].name}")
                return candidates[idx]
        except ValueError:
            pass


def match_tracks(tracks: list[dict], audio_files: list[Path],
                 threshold: int = DEFAULT_FUZZY_THRESHOLD,
                 interactive: bool = True,
                 disk_path: str = "") -> list[dict]:
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
                                     candidates[:MAX_CANDIDATES]))

        results.append(result)
        print(f"\r  Matching: {i}/{total} ({found} found, {len(picks_needed)} need review)", end="", flush=True)

    print()  # newline after progress

    # Phase 2: interactive resolution
    if picks_needed:
        print(f"\n{'='*60}")
        print(f"  {len(picks_needed)} tracks have multiple close matches — please pick:")
        print(f"  (Use p<N> to Quick Look preview, then select a number)")
        print(f"{'='*60}")

        for pick_num, (idx, label, candidates) in enumerate(picks_needed, 1):
            print(f"\n  [{pick_num}/{len(picks_needed)}]", end="")
            picked = _pick_candidate(label, candidates, disk_root=disk_path)
            if picked:
                results[idx]["match_path"] = str(picked[0])
                results[idx]["score"] = picked[1]
                found += 1

        print(f"\n  Resolution complete. Total matched: {found}/{total}")

    return results
