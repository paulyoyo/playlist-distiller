"""Fuzzy matching of playlist tracks to local audio files."""
from __future__ import annotations

import re
import subprocess
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


def _pick_candidate(track_label: str, candidates: list[tuple[Path, int]]) -> tuple[Path, int] | None:
    """Interactive picker when multiple files match a track.

    candidates: list of (file_path, score), sorted by score descending.
    Returns selected (file_path, score) or None to skip.
    """
    print(f"\n  Multiple matches for: {track_label}")
    for i, (fp, sc) in enumerate(candidates, 1):
        print(f"    {i}. [{sc}%] {fp.name}")
        print(f"       {fp.parent}")

    preview_proc = None
    while True:
        try:
            choice = input(f"    Select (1-{len(candidates)}), p<N> to preview, s to skip: ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            _kill_preview(preview_proc)
            return None

        if choice == "s":
            _kill_preview(preview_proc)
            return None

        # Preview: p1, p2, etc.
        if choice.startswith("p"):
            _kill_preview(preview_proc)
            try:
                idx = int(choice[1:]) - 1
                if 0 <= idx < len(candidates):
                    print(f"    Previewing: {candidates[idx][0].name}")
                    preview_proc = _preview_file(str(candidates[idx][0]))
                else:
                    print(f"    Invalid option. Use p1-p{len(candidates)}")
            except ValueError:
                print(f"    Invalid. Use p1-p{len(candidates)}")
            continue

        # Direct selection: 1, 2, etc.
        try:
            idx = int(choice) - 1
            if 0 <= idx < len(candidates):
                _kill_preview(preview_proc)
                return candidates[idx]
        except ValueError:
            pass

        print(f"    Invalid. Enter 1-{len(candidates)}, p<N>, or s")


def match_tracks(tracks: list[dict], audio_files: list[Path],
                 threshold: int = DEFAULT_FUZZY_THRESHOLD,
                 interactive: bool = True) -> list[dict]:
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

        candidates = []
        # Minimum title similarity to avoid matching different songs
        # by the same artist (e.g. "Ryan Castro - AMIGA" when looking
        # for "Ryan Castro - LA VILLA")
        title_min = max(50, threshold - 10)

        for file_path, norm_name in file_index:
            score_full = fuzz.token_set_ratio(search_str, norm_name)
            score_title = fuzz.token_set_ratio(title_only, norm_name)
            score = max(score_full, int(score_title * 0.85))

            if score >= threshold and score_title >= title_min:
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

        for idx, label, candidates in picks_needed:
            picked = _pick_candidate(label, candidates)
            if picked:
                results[idx]["match_path"] = str(picked[0])
                results[idx]["score"] = picked[1]
                found += 1

        print(f"\n  Resolution complete. Total matched: {found}/{total}")

    return results
