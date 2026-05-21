"""Playlist providers: Spotify and Tidal."""

import re
import sys


def detect_provider(url: str) -> str:
    """Detect playlist provider from URL."""
    if "spotify.com" in url or "spotify:" in url:
        return "spotify"
    elif "tidal.com" in url:
        return "tidal"
    else:
        print(f"Error: Could not detect provider from URL: {url}")
        print("Supported: Spotify or Tidal playlist URLs")
        sys.exit(1)


def extract_spotify_playlist_id(url: str) -> str:
    """Extract playlist ID from Spotify URL or URI."""
    # Handle spotify:playlist:ID format
    if url.startswith("spotify:playlist:"):
        return url.split(":")[-1]
    # Handle https://open.spotify.com/playlist/ID?si=...
    match = re.search(r"playlist/([a-zA-Z0-9]+)", url)
    if match:
        return match.group(1)
    print(f"Error: Could not extract playlist ID from: {url}")
    sys.exit(1)


def extract_tidal_playlist_id(url: str) -> str:
    """Extract playlist ID from Tidal URL."""
    # https://tidal.com/browse/playlist/GUID or https://listen.tidal.com/playlist/GUID
    match = re.search(r"playlist/([a-f0-9-]+)", url)
    if match:
        return match.group(1)
    print(f"Error: Could not extract playlist ID from: {url}")
    sys.exit(1)


def _get_spotify_client_credentials():
    """Get a Spotify client using client credentials (read-only)."""
    import spotipy
    from spotipy.oauth2 import SpotifyClientCredentials
    from config import get_spotify_credentials

    client_id, client_secret = get_spotify_credentials()
    return spotipy.Spotify(auth_manager=SpotifyClientCredentials(
        client_id=client_id,
        client_secret=client_secret,
    ))


def fetch_spotify_tracks(url: str) -> list[dict]:
    """Fetch tracks from a Spotify playlist. Returns list of {artist, title, uri}."""
    sp = _get_spotify_client_credentials()
    playlist_id = extract_spotify_playlist_id(url)
    tracks = []
    offset = 0

    while True:
        results = sp.playlist_items(playlist_id, offset=offset, limit=100,
                                     fields="items(track(name,uri,artists(name))),next")
        for item in results["items"]:
            track = item.get("track")
            if not track:
                continue
            artist = ", ".join(a["name"] for a in track["artists"])
            title = track["name"]
            tracks.append({"artist": artist, "title": title, "uri": track.get("uri")})

        if not results.get("next"):
            break
        offset += 100

    return tracks


def fetch_tidal_tracks(url: str) -> list[dict]:
    """Fetch tracks from a Tidal playlist. Returns list of {artist, title}."""
    import tidalapi

    session = tidalapi.Session()

    # Try loading saved session first
    try:
        from config import load_config, save_config, CONFIG_FILE
        cfg = load_config()
        token_type = cfg.get("tidal_token_type")
        access_token = cfg.get("tidal_access_token")
        refresh_token = cfg.get("tidal_refresh_token")
        expiry_time = cfg.get("tidal_expiry_time")

        if all([token_type, access_token, refresh_token, expiry_time]):
            from datetime import datetime
            expiry = datetime.fromisoformat(expiry_time)
            session.load_oauth_session(token_type, access_token, refresh_token, expiry)
    except Exception:
        pass

    if not session.check_login():
        print("\n--- Tidal Login ---")
        print("A browser window will open for you to log in to Tidal.")
        login, future = session.login_oauth()
        print(f"If browser doesn't open, visit: https://{login.verification_uri_complete}")
        future.result()

        # Save session for next time
        from config import save_config
        save_config({
            "tidal_token_type": session.token_type,
            "tidal_access_token": session.access_token,
            "tidal_refresh_token": session.refresh_token,
            "tidal_expiry_time": session.expiry_time.isoformat() if session.expiry_time else None,
        })
        print("Tidal session saved.\n")

    playlist_id = extract_tidal_playlist_id(url)
    playlist = session.playlist(playlist_id)
    tidal_tracks = playlist.tracks()

    tracks = []
    for t in tidal_tracks:
        artist = t.artist.name if t.artist else "Unknown"
        title = t.name
        tracks.append({"artist": artist, "title": title, "tidal_id": t.id})

    return tracks


def _get_tidal_session():
    """Get an authenticated Tidal session (reuses saved token)."""
    import tidalapi
    from config import load_config, save_config

    session = tidalapi.Session()

    try:
        cfg = load_config()
        token_type = cfg.get("tidal_token_type")
        access_token = cfg.get("tidal_access_token")
        refresh_token = cfg.get("tidal_refresh_token")
        expiry_time = cfg.get("tidal_expiry_time")

        if all([token_type, access_token, refresh_token, expiry_time]):
            from datetime import datetime
            expiry = datetime.fromisoformat(expiry_time)
            session.load_oauth_session(token_type, access_token, refresh_token, expiry)
    except Exception:
        pass

    if not session.check_login():
        print("\n--- Tidal Login ---")
        login, future = session.login_oauth()
        print(f"If browser doesn't open, visit: https://{login.verification_uri_complete}")
        future.result()
        save_config({
            "tidal_token_type": session.token_type,
            "tidal_access_token": session.access_token,
            "tidal_refresh_token": session.refresh_token,
            "tidal_expiry_time": session.expiry_time.isoformat() if session.expiry_time else None,
        })

    return session


def create_missing_spotify_playlist(missing_tracks: list[dict], source_name: str) -> str:
    """Create a Spotify playlist with the missing (unmatched) tracks.

    Returns the playlist URL.
    """
    from config import get_spotify_user_auth

    sp = get_spotify_user_auth()
    user_id = sp.current_user()["id"]

    playlist_name = f"{source_name} - Missing Tracks"
    playlist = sp.user_playlist_create(
        user_id, playlist_name,
        public=False,
        description="Tracks not found locally - generated by Playlist Distiller"
    )

    # Add tracks in batches of 100
    uris = [t["uri"] for t in missing_tracks if t.get("uri")]
    for i in range(0, len(uris), 100):
        sp.playlist_add_items(playlist["id"], uris[i:i+100])

    return playlist["external_urls"]["spotify"]


def create_missing_tidal_playlist(missing_tracks: list[dict], source_name: str) -> str:
    """Create a Tidal playlist with the missing (unmatched) tracks.

    Returns the playlist ID.
    """
    session = _get_tidal_session()

    playlist_name = f"{source_name} - Missing Tracks"
    new_playlist = session.user.create_playlist(playlist_name,
        description="Tracks not found locally - generated by Playlist Distiller")

    track_ids = [t["tidal_id"] for t in missing_tracks if t.get("tidal_id")]
    if track_ids:
        new_playlist.add(track_ids)

    return new_playlist.id
