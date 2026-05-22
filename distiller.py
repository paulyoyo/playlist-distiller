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
from matcher import scan_audio_files, match_tracks, _GOLDEN, _WISTERIA, _LILAC, _DIM, _RESET
from config import DEFAULT_FUZZY_THRESHOLD


def list_disks() -> list[Path]:
    """List all mounted volumes (macOS) excluding system internals."""
    volumes = Path("/Volumes")
    skip = {"Macintosh HD - Data", "Recovery"}
    disks = []
    for v in sorted(volumes.iterdir()):
        if v.name not in skip and v.is_dir():
            disks.append(v)
    return disks


def select_disk() -> str:
    """Prompt user to select a disk with single-keypress input."""
    import termios, tty
    disks = list_disks()
    if not disks:
        print(f"{_LILAC}No disks found in /Volumes/{_RESET}")
        sys.exit(1)

    # Cap at 10 (1-9, 0)
    disks = disks[:10]
    n = len(disks)
    labels = [str((i + 1) % 10) for i in range(n)]
    key_map = {labels[i]: i for i in range(n)}

    print(f"\n{_LILAC}Available disks:{_RESET}")
    for i, d in enumerate(disks):
        print(f"  {_GOLDEN}{labels[i]}. {d.name}{_RESET}")

    hint = ",".join(labels)
    print(f"  {_DIM}{hint} select{_RESET}")

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
            print()
            sys.exit(1)
        if ch in ("\x03", "\x04"):
            print()
            sys.exit(1)
        if ch in key_map:
            selected = disks[key_map[ch]]
            print(f"  -> {_GOLDEN}{selected.name}{_RESET}")
            return str(selected)


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
    print(f"\n{_LILAC}Fetching playlist from {provider.capitalize()}...{_RESET}")

    if provider == "spotify":
        playlist_name, tracks = fetch_spotify_tracks(args.url)
    else:
        playlist_name, tracks = fetch_tidal_tracks(args.url)

    if not tracks:
        print(f"{_LILAC}No tracks found in playlist.{_RESET}")
        sys.exit(1)

    print(f"{_GOLDEN}{playlist_name}{_RESET}")
    print(f"{_WISTERIA}{len(tracks)} tracks{_RESET}\n")

    # Show first few tracks as preview
    print(f"{_LILAC}Preview:{_RESET}")
    for t in tracks[:5]:
        print(f"  {_WISTERIA}{t['artist']}{_RESET} - {_GOLDEN}{t['title']}{_RESET}")
    if len(tracks) > 5:
        print(f"  {_DIM}... and {len(tracks) - 5} more{_RESET}\n")

    # 2. Select disk to search
    disk_path = select_disk()

    # 3. Scan and match
    audio_files = scan_audio_files(disk_path)
    if not audio_files:
        print(f"{_LILAC}No audio files found on selected disk.{_RESET}")
        sys.exit(1)

    results = match_tracks(tracks, audio_files, threshold=args.threshold,
                           interactive=not args.auto, disk_path=disk_path,
                           playlist_name=playlist_name)

    # 4. Report results
    matched = [r for r in results if r["match_path"]]
    missing = [r for r in results if not r["match_path"]]

    print(f"\n{_LILAC}{'='*60}{_RESET}")
    print(f"{_GOLDEN}RESULTS: {len(matched)}/{len(results)} tracks matched{_RESET}")
    print(f"{_LILAC}{'='*60}{_RESET}")

    if matched:
        print(f"\n{_LILAC}Matched:{_RESET}")
        for r in matched:
            path = r['match_path']
            if path.startswith(disk_path):
                path = path[len(disk_path):].lstrip("/")
            print(f"  {_WISTERIA}{r['artist']}{_RESET} - {_GOLDEN}{r['title']}{_RESET}")
            print(f"    {_DIM}-> {path}{_RESET}")

    if missing:
        print(f"\n{_LILAC}Missing ({len(missing)}):{_RESET}")
        for r in missing:
            print(f"  {_DIM}x {r['artist']} - {r['title']}{_RESET}")

    # 5. Write .m3u
    if not matched:
        print(f"\n{_LILAC}No matches found. No .m3u file created.{_RESET}")
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
    print(f"\n{_GOLDEN}Playlist saved:{_RESET} {_DIM}{output_path}{_RESET} {_WISTERIA}({count} tracks){_RESET}")
    print(f"{_DIM}Import this file into Traktor via: File > Import Collection/Playlist{_RESET}")

    # 6. Create missing tracks playlist on the same provider (default behavior)
    if not args.no_missing and missing:
        print(f"\n{_LILAC}Creating missing tracks playlist on {provider.capitalize()}...{_RESET}")
        if provider == "spotify":
            url = create_missing_spotify_playlist(missing, playlist_name)
            print(f"{_GOLDEN}Spotify playlist created:{_RESET} {_WISTERIA}{url}{_RESET}")
        else:
            pid = create_missing_tidal_playlist(missing, playlist_name)
            print(f"{_GOLDEN}Tidal playlist created:{_RESET} {_WISTERIA}https://listen.tidal.com/playlist/{pid}{_RESET}")
    elif not args.no_missing and not missing:
        print(f"\n{_GOLDEN}All tracks matched — no missing playlist needed.{_RESET}")


if __name__ == "__main__":
    main()
