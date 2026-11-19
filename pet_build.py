"""Pre-render /pet GIFs from her media into pets/. Run locally (needs Pillow), then ./deploy.sh."""
import hashlib
import html
import re
import sys
import tomllib
import urllib.parse
import urllib.request
from pathlib import Path

from petpet import petpet

ROOT = Path(__file__).parent
OUT = ROOT / "pets"
IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".webp"}
UA = {"User-Agent": "Mozilla/5.0"}
OG_IMAGE = re.compile(r'property="og:image" content="([^"]+)"')


def get(url: str) -> bytes:
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=20) as r:
        return r.read(20 * 1024 * 1024)


def image_url(url: str) -> str | None:
    # tenor pages -> their gif, direct image links as is, videos skipped
    if "tenor.com/view/" in url:
        m = OG_IMAGE.search(get(url).decode("utf-8", "replace"))
        return html.unescape(m.group(1)) if m else None
    path = urllib.parse.urlsplit(url).path.lower()
    return url if Path(path).suffix in IMAGE_EXTS else None


def main() -> None:
    config = tomllib.loads((ROOT / "config.toml").read_text(encoding="utf-8"))
    OUT.mkdir(exist_ok=True)
    sources = [(u, None) for u in config["seal"]["links"]]
    sources += [(str(p), p) for p in sorted((ROOT / "media").iterdir()) if p.suffix.lower() in IMAGE_EXTS]
    made = skipped = failed = 0
    for key, local in sources:
        dest = OUT / f"{hashlib.sha1(key.encode()).hexdigest()[:12]}.gif"
        if dest.exists():
            skipped += 1
            continue
        try:
            if local:
                data = local.read_bytes()
            else:
                src = image_url(key)
                if not src:
                    print(f"skip (not an image): {key}")
                    continue
                data = get(src)
            dest.write_bytes(petpet(data))
            made += 1
            print(f"ok {dest.name} {dest.stat().st_size // 1024}KB  {key}")
        except Exception as e:  # one bad source shouldn't stop the rest
            failed += 1
            print(f"FAIL {key}: {e}", file=sys.stderr)
    print(f"made {made}, already had {skipped}, failed {failed}, total {len(list(OUT.glob('*.gif')))}")


if __name__ == "__main__":
    main()
