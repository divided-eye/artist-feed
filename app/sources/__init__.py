from .bandcamp import Bandcamp
from .soundcloud import SoundCloud
from .youtube import YouTube

# Register sources here. Order is the order shown in the UI.
SOURCES = {s.key: s for s in (Bandcamp(), SoundCloud(), YouTube())}
