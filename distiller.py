#!/usr/bin/env python3
"""
Playlist Distiller - Create Traktor-compatible .m3u playlists from Spotify/Tidal.

Usage:
    python distiller.py <playlist_url> [--threshold N] [--output NAME]

The script will prompt you to select which external disk to search.
"""

import re
import sys
import warnings
import argparse

warnings.filterwarnings("ignore", message="urllib3 v2 only supports OpenSSL")
from datetime import date
from pathlib import Path

from providers import (detect_provider, fetch_spotify_tracks, fetch_tidal_tracks,
                       create_missing_spotify_playlist, create_missing_tidal_playlist)
from matcher import scan_audio_files, match_tracks
from config import DEFAULT_FUZZY_THRESHOLD


def list_external_disks() -> list[Path]:
    """List mounted volumes (macOS) excluding system ones."""
    volumes = Path("/Volumes")
    system_volumes = {"Macintosh HD", "Macintosh HD - Data", "Recovery"}
    disks = []
    for v in sorted(volumes.iterdir()):
        if v.name not in system_volumes and v.is_dir():
            disks.append(v)
    return disks


def select_disk() -> str:
    """Prompt user to select an external disk."""
    disks = list_external_disks()
    if not disks:
        print("No external disks found in /Volumes/")
        sys.exit(1)

    print("\nAvailable disks:")
    for i, d in enumerate(disks, 1):
        print(f"  {i}. {d.name}")

    while True:
        try:
            choice = input(f"\nSelect disk (1-{len(disks)}): ").strip()
            idx = int(choice) - 1
            if 0 <= idx < len(disks):
                selected = disks[idx]
                print(f"Selected: {selected}")
                return str(selected)
        except (ValueError, IndexError):
            pass
        print("Invalid selection, try again.")


def write_m3u(results: list[dict], output_path: str, playlist_name: str):
    """Write matched tracks to an .m3u file."""
    matched = [r for r in results if r["match_path"]]
    with open(output_path, "w", encoding="utf-8") as f:
        f.write("#EXTM3U\n")
        f.write(f"# Playlist: {playlist_name}\n")
        f.write(f"# Matched: {len(matched)}/{len(results)} tracks\n")
        for r in matched:
            f.write(f"#EXTINF:-1,{r['artist']} - {r['title']}\n")
            f.write(f"{r['match_path']}\n")
    return len(matched)


def main():
    parser = argparse.ArgumentParser(
        description="Create Traktor .m3u playlists from Spotify/Tidal playlists"
    )
    parser.add_argument("url", help="Spotify or Tidal playlist URL")
    parser.add_argument("--threshold", type=int, default=DEFAULT_FUZZY_THRESHOLD,
                        help=f"Fuzzy match threshold 0-100 (default: {DEFAULT_FUZZY_THRESHOLD})")
    parser.add_argument("--output", "-o", type=str, default=None,
                        help="Output .m3u filename (default: auto from playlist)")
    parser.add_argument("--no-missing", action="store_true",
                        help="Skip creating a playlist with unmatched tracks")
    parser.add_argument("--auto", action="store_true",
                        help="Auto-select best match without interactive prompts")

    args = parser.parse_args()

    # 1. Detect provider and fetch tracks
    provider = detect_provider(args.url)
    print(f"\nFetching playlist from {provider.capitalize()}...")

    if provider == "spotify":
        playlist_name, tracks = fetch_spotify_tracks(args.url)
    else:
        playlist_name, tracks = fetch_tidal_tracks(args.url)

    if not tracks:
        print("No tracks found in playlist.")
        sys.exit(1)

    print(f"Playlist: {playlist_name}")
    print(f"Found {len(tracks)} tracks.\n")

    # Show first few tracks as preview
    print("Preview:")
    for t in tracks[:5]:
        print(f"  {t['artist']} - {t['title']}")
    if len(tracks) > 5:
        print(f"  ... and {len(tracks) - 5} more\n")

    # 2. Select disk to search
    disk_path = select_disk()

    # 3. Scan and match
    audio_files = scan_audio_files(disk_path)
    if not audio_files:
        print("No audio files found on selected disk.")
        sys.exit(1)

    results = match_tracks(tracks, audio_files, threshold=args.threshold,
                           interactive=not args.auto, disk_path=disk_path)

    # 4. Report results
    matched = [r for r in results if r["match_path"]]
    missing = [r for r in results if not r["match_path"]]

    print(f"\n{'='*60}")
    print(f"RESULTS: {len(matched)}/{len(results)} tracks matched")
    print(f"{'='*60}")

    if matched:
        print("\nMatched:")
        for r in matched:
            print(f"  [{r['score']}%] {r['artist']} - {r['title']}")
            print(f"         -> {r['match_path']}")

    if missing:
        print(f"\nMissing ({len(missing)}):")
        for r in missing:
            print(f"  x {r['artist']} - {r['title']}")

    # 5. Write .m3u
    if not matched:
        print("\nNo matches found. No .m3u file created.")
        sys.exit(0)

    # Ensure playlists/ directory exists
    playlists_dir = Path(__file__).parent / "playlists"
    playlists_dir.mkdir(exist_ok=True)

    if args.output:
        output_name = args.output
        if not output_name.endswith(".m3u"):
            output_name += ".m3u"
    else:
        safe_name = re.sub(r'[^\w\s-]', '', playlist_name).strip().replace(' ', '_')
        today = date.today().strftime("%Y-%m-%d")
        output_name = f"{safe_name}_{today}_{provider}.m3u"

    output_path = str(playlists_dir / output_name)
    count = write_m3u(results, output_path, playlist_name)
    print(f"\nPlaylist saved: {output_path} ({count} tracks)")
    print("Import this file into Traktor via: File > Import Collection/Playlist")

    # 6. Create missing tracks playlist on the same provider (default behavior)
    if not args.no_missing and missing:
        print(f"\nCreating missing tracks playlist on {provider.capitalize()}...")
        if provider == "spotify":
            url = create_missing_spotify_playlist(missing, playlist_name)
            print(f"Spotify playlist created: {url}")
        else:
            pid = create_missing_tidal_playlist(missing, playlist_name)
            print(f"Tidal playlist created: https://listen.tidal.com/playlist/{pid}")
    elif not args.no_missing and not missing:
        print("\nAll tracks matched - no missing playlist needed.")


if __name__ == "__main__":
    main()
