"""Try one page from the command line, without changing your artist list:

    python -m app.check bandcamp artistname
    python -m app.check soundcloud username
"""
import asyncio
import sys

from .refresh import make_client
from .sources import SOURCES


async def main(source_key, text):
    source = SOURCES[source_key]
    handle, url = source.normalize(text)
    account = {"id": 0, "artist_id": 0, "handle": handle, "url": url, "meta": {}}
    async with make_client() as client:
        result = await source.fetch(client, account, set())
    print(f"{result.display_name or handle} - {len(result.posts)} posts")
    for p in sorted(result.posts, key=lambda p: p.published_at or "", reverse=True):
        print(f"  {p.published_at or '(no date)':20}  {p.kind:6} {p.title}  {p.url}")
    if result.warning:
        print("note:", result.warning)


if __name__ == "__main__":
    if len(sys.argv) != 3 or sys.argv[1] not in SOURCES:
        sys.exit(__doc__)
    asyncio.run(main(sys.argv[1], sys.argv[2]))
