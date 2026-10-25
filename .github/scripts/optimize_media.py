from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from urllib.parse import unquote
from PIL import Image, ImageOps

ROOT = Path(__file__).resolve().parents[2]
MEDIA = ROOT / "media"
DATA = ROOT / "data"
SUPPORTED = {".png", ".jpg", ".jpeg", ".webp"}
MAX_EDGE = 2000
QUALITY = 86


def relative_url_variants(rel: str):
    rel = rel.replace("\\", "/")
    return (f"/media/{rel}", f"media/{rel}", f"./media/{rel}")


def convert_one(source: Path, occupied: set[str]):
    rel = source.relative_to(MEDIA).as_posix()
    with Image.open(source) as opened:
        if getattr(opened, "is_animated", False):
            print(f"Skipping animated image: {source.relative_to(ROOT)}")
            return None
        has_metadata = bool(
            opened.info.get("exif") or opened.info.get("xmp") or
            opened.info.get("icc_profile") or opened.info.get("comment") or
            opened.info.get("description") or opened.getexif()
        )
        too_large = max(opened.size) > MAX_EDGE
        # Avoid lossy re-encoding WebP files made by this workflow if they already
        # have no metadata and are within the size limit.
        if source.suffix.lower() == ".webp" and not has_metadata and not too_large:
            return (rel, rel)

        if source.suffix.lower() == ".webp":
            dest = source
        else:
            dest = source.with_suffix(".webp")
            if dest.exists() and dest.resolve() != source.resolve():
                dest = source.with_name(f"{source.stem}-{source.suffix[1:].lower()}.webp")
                counter = 2
                while dest.exists():
                    dest = source.with_name(f"{source.stem}-{source.suffix[1:].lower()}-{counter}.webp")
                    counter += 1
        dest_rel = dest.relative_to(MEDIA).as_posix()
        image = ImageOps.exif_transpose(opened)
        image.thumbnail((MAX_EDGE, MAX_EDGE), Image.Resampling.LANCZOS)
        if image.mode not in ("RGB", "RGBA"):
            image = image.convert("RGBA" if "transparency" in image.info else "RGB")
        # Saving a fresh WebP without passing exif/xmp/icc data removes source metadata.
        fd, temp_name = tempfile.mkstemp(suffix=".webp", dir=str(dest.parent))
        os.close(fd)
        temp = Path(temp_name)
        try:
            image.save(temp, format="WEBP", quality=QUALITY, method=6)
            if dest.exists() and dest != source and dest.read_bytes() == temp.read_bytes():
                temp.unlink()
            else:
                temp.replace(dest)
        finally:
            if temp.exists():
                temp.unlink()
    if source != dest and source.exists():
        source.unlink()
    print(f"Converted {source.relative_to(ROOT)} -> {dest.relative_to(ROOT)}")
    return (rel, dest_rel)


def rewrite_json_paths(mapping: dict[str, str]):
    if not mapping or not DATA.exists():
        return
    # Replace paths in any JSON file, including nested src/cover values, without touching other text.
    replacements = {}
    for old_rel, new_rel in mapping.items():
        for old_url, new_url in zip(relative_url_variants(old_rel), relative_url_variants(new_rel)):
            replacements[old_url] = new_url
    def walk(value):
        if isinstance(value, dict):
            return {k: walk(v) for k, v in value.items()}
        if isinstance(value, list):
            return [walk(v) for v in value]
        if isinstance(value, str):
            decoded = unquote(value)
            for old, new in replacements.items():
                if decoded == old:
                    return new
            # Also handle a URL path whose prefix is the repo's public /media path.
            for old_rel, new_rel in mapping.items():
                old_name = "/media/" + old_rel
                new_name = "/media/" + new_rel
                if decoded.endswith(old_name):
                    return decoded[:-len(old_name)] + new_name
            return value
        return value
    for path in DATA.rglob("*.json"):
        try:
            original = path.read_text(encoding="utf-8")
            content = json.loads(original)
        except (OSError, json.JSONDecodeError):
            continue
        updated = walk(content)
        serialized = json.dumps(updated, ensure_ascii=False, indent=2) + "\n"
        if serialized != original:
            path.write_text(serialized, encoding="utf-8")
            print(f"Updated image paths in {path.relative_to(ROOT)}")


def main():
    if not MEDIA.exists():
        print("No media directory found; nothing to optimize.")
        return
    mapping = {}
    occupied = set()
    # Process non-WebP first; WebP sources are then rewritten to remove embedded metadata too.
    files = sorted((p for p in MEDIA.rglob("*") if p.is_file() and p.suffix.lower() in SUPPORTED),
                   key=lambda p: (p.suffix.lower() == ".webp", p.as_posix()))
    for file in files:
        if not file.exists():
            continue
        result = convert_one(file, occupied)
        if result:
            old_rel, new_rel = result
            mapping[old_rel] = new_rel
            occupied.add((MEDIA / new_rel).as_posix())
    rewrite_json_paths(mapping)


if __name__ == "__main__":
    main()
