"""Fuzzy matching of playlist tracks to local audio files."""

import re
from pathlib import Path
from thefuzz import fuzz
from config import AUDIO_EXTENSIONS, DEFAULT_FUZZY_THRESHOLD


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


def match_tracks(tracks: list[dict], audio_files: list[Path],
                 threshold: int = DEFAULT_FUZZY_THRESHOLD) -> list[dict]:
    """Match playlist tracks to local files using fuzzy matching.

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

    for i, track in enumerate(tracks, 1):
        artist = track["artist"]
        title = track["title"]
        search_str = normalize(f"{artist} {title}")
        title_only = normalize(title)

        best_score = 0
        best_file = None

        for file_path, norm_name in file_index:
            # Score combines full "artist title" match and title-only match
            score_full = fuzz.token_set_ratio(search_str, norm_name)
            score_title = fuzz.token_set_ratio(title_only, norm_name)
            # Weight: full match matters more, but title-only helps when
            # filename doesn't include artist
            score = max(score_full, int(score_title * 0.85))

            if score > best_score:
                best_score = score
                best_file = file_path

        matched = best_score >= threshold
        if matched:
            found += 1

        results.append({
            "artist": artist,
            "title": title,
            "match_path": str(best_file) if matched else None,
            "score": best_score if matched else 0,
        })

        # Progress indicator
        print(f"\r  Matching: {i}/{total} ({found} found)", end="", flush=True)

    print()  # newline after progress
    return results
