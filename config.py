import os
import json
from pathlib import Path

CONFIG_FILE = Path(__file__).parent / ".playlist_distiller_config.json"

AUDIO_EXTENSIONS = {".mp3", ".flac", ".wav", ".aiff", ".aif", ".m4a", ".ogg"}
SPOTIFY_TOKEN_CACHE = CONFIG_FILE.parent / ".spotify_token_cache"

# Fuzzy match threshold (0-100). 100 = exact match. Lower = more lenient.
DEFAULT_FUZZY_THRESHOLD = 70

# Minimum file duration in seconds to exclude sound effects / jingles
MIN_DURATION_SECONDS = 30


def load_config():
    if CONFIG_FILE.exists():
        return json.loads(CONFIG_FILE.read_text())
    return {}


def save_config(data):
    existing = load_config()
    existing.update(data)
    CONFIG_FILE.write_text(json.dumps(existing, indent=2))


def get_spotify_credentials():
    """Get Spotify credentials from config or environment."""
    cfg = load_config()
    client_id = os.environ.get("SPOTIPY_CLIENT_ID") or cfg.get("spotify_client_id")
    client_secret = os.environ.get("SPOTIPY_CLIENT_SECRET") or cfg.get("spotify_client_secret")

    if not client_id or not client_secret:
        print("\n--- Spotify Setup ---")
        print("Get credentials at: https://developer.spotify.com/dashboard")
        client_id = input("Spotify Client ID: ").strip()
        client_secret = input("Spotify Client Secret: ").strip()
        save_config({
            "spotify_client_id": client_id,
            "spotify_client_secret": client_secret,
        })
        print("Credentials saved to config.\n")

    return client_id, client_secret


SPOTIFY_REDIRECT_URI = "http://127.0.0.1:8888/callback"
SPOTIFY_SCOPES = "playlist-modify-public playlist-modify-private"


def get_spotify_user_auth():
    """Get Spotify client with user authorization (needed for creating playlists)."""
    import spotipy
    from spotipy.oauth2 import SpotifyOAuth

    client_id, client_secret = get_spotify_credentials()
    cache_path = CONFIG_FILE.parent / ".spotify_token_cache"

    print("\n--- Spotify Authorization ---")
    print("A browser will open for you to log in to Spotify.")
    print("After approving, the browser will redirect back automatically.\n")

    sp = spotipy.Spotify(auth_manager=SpotifyOAuth(
        client_id=client_id,
        client_secret=client_secret,
        redirect_uri=SPOTIFY_REDIRECT_URI,
        scope=SPOTIFY_SCOPES,
        cache_path=str(cache_path),
        open_browser=True,
    ))
    return sp
