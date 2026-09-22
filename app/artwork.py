"""Album-art lookup.

A Fusion stereo doesn't send cover art over NMEA 2000, so the dashboard looks
it up by artist/album/title from Apple's free iTunes Search API. That sends
those names to Apple, so it can be turned off (BOAT_ALBUM_ART=false), and it
needs an internet connection; without one the UI shows generated artwork.

Lookups run on a background thread and are cached, so callers never block:
get() returns the URL if it's known, else None (and starts a lookup).
"""
import json
import threading
import time
import urllib.parse
import urllib.request

FOUND_TTL_S = float("inf")
NOT_FOUND_TTL_S = 3600.0
ERROR_TTL_S = 60.0


def itunes_lookup(artist, album, title):
    """Return a cover-art URL for the given track, or None if Apple has no match."""
    if album:
        term, entity = f"{artist} {album}".strip(), "album"
    else:
        term, entity = f"{artist} {title}".strip(), "song"
    url = "https://itunes.apple.com/search?" + urllib.parse.urlencode({"term": term, "entity": entity, "limit": 1})
    with urllib.request.urlopen(url, timeout=5) as resp:
        results = json.load(resp).get("results", [])
    if not results:
        return None
    art = results[0].get("artworkUrl100", "")
    art = art.replace("100x100bb", "400x400bb")
    host = urllib.parse.urlparse(art).netloc
    return art if art.startswith("https://") and host.endswith(".mzstatic.com") else None


class ArtworkFinder:
    def __init__(self, lookup=itunes_lookup, clock=time.monotonic):
        self._lookup = lookup
        self._clock = clock
        self._cache = {}  # key -> (url or None, expires_at)
        self._pending = set()
        self._lock = threading.Lock()

    def get(self, artist, album, title):
        artist, album, title = (artist or "").strip(), (album or "").strip(), (title or "").strip()
        if not artist or not (album or title):
            return None
        key = (artist.lower(), (album or title).lower())
        with self._lock:
            hit = self._cache.get(key)
            if hit and hit[1] > self._clock():
                return hit[0]
            if key in self._pending:
                return hit[0] if hit else None
            self._pending.add(key)
        threading.Thread(target=self._run, args=(key, artist, album, title), daemon=True).start()
        return hit[0] if hit else None

    def _run(self, key, artist, album, title):
        try:
            url = self._lookup(artist, album, title)
            ttl = FOUND_TTL_S if url else NOT_FOUND_TTL_S
        except Exception:  # offline, timeout, bad response: try again later
            url, ttl = None, ERROR_TTL_S
        with self._lock:
            self._cache[key] = (url, self._clock() + ttl)
            self._pending.discard(key)
