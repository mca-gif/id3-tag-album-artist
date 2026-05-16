#!/usr/bin/env python3
"""Copy album artist to artist field, preserving featured artist info in the title."""

import argparse
import re
import sys
from pathlib import Path

import mutagen

EXTENSIONS = {".mp3", ".flac", ".ogg", ".m4a", ".aac", ".wma", ".opus"}

# Strips leading separators (whitespace, comma, semicolon, ampersand) and
# keywords (feat./ft./featuring/with) from the remainder after the
# album-artist prefix. Only matches at the start, so internal '&' between
# featured-artist names (e.g. "Configa & Speech") is preserved.
LEADING_NOISE = re.compile(
    # Lookahead instead of \b after the keyword: with \b, the engine backtracks
    # past the optional '.' because the t/. transition already satisfies the
    # word boundary, leaving a stray period in the remainder.
    r"^(?:\s+|[,;&]+|(?:feat\.?|ft\.?|featuring|with)(?=\s|[,;&]|$))+",
    re.IGNORECASE,
)

# Check whether a string contains a featured artist credit.
HAS_FEAT = re.compile(r"\b(?:feat\.?|ft\.?|featuring)\b", re.IGNORECASE)
TITLE_HAS_FEAT = HAS_FEAT

# Characters that are unsafe in filenames on common filesystems (the Windows
# set is a superset of Linux/macOS, so sanitizing for it covers everything).
FILESYSTEM_UNSAFE = re.compile(r'[<>:"/\\|?*\x00-\x1f]')

# Trailing "(feat. X)" / "[ft. X]" credit on a title — used to recover the
# base title for files that were tagged on a previous run but never renamed.
TRAILING_FEAT_PAREN = re.compile(
    r"\s*[(\[][^)\]]*\b(?:feat\.?|ft\.?|featuring)\b[^)\]]*[)\]]\s*$",
    re.IGNORECASE,
)


def strip_trailing_feat_parenthetical(title):
    """Drop a trailing '(feat. X)' / '[ft. X]' style credit from a title."""
    return TRAILING_FEAT_PAREN.sub("", title)


def get_tag(tags, key):
    """Get first value for a tag key, or None."""
    val = tags.get(key)
    if val:
        v = val[0] if isinstance(val, list) else val
        return str(v).strip() or None
    return None


def extract_featured(artist, album_artist):
    """Extract featured artist info from the artist field.

    Returns (featured_string, credit_to_append) or (None, None) or (None, "skip").

    credit_to_append is the parenthetical body (without the surrounding parens),
    e.g. "feat. Different Artist". If the remainder already contains feat./ft./featuring,
    it is returned verbatim to avoid double-prefixing.
    """
    if artist.lower().startswith(album_artist.lower()):
        remainder = artist[len(album_artist):]
        remainder = LEADING_NOISE.sub("", remainder).strip()
        if not remainder:
            return None, None
        if HAS_FEAT.search(remainder):
            return remainder, remainder
        return remainder, f"feat. {remainder}"

    return None, "skip"


def sanitize_for_path(name):
    """Make a string safe for use as a filename component.

    Replaces filesystem-unsafe characters with '-', collapses runs of hyphens
    or whitespace, and trims trailing dots/spaces (which Windows rejects).
    """
    name = FILESYSTEM_UNSAFE.sub("-", name)
    name = re.sub(r"-{2,}", "-", name)
    name = re.sub(r"\s+", " ", name).strip()
    return name.rstrip(". ")


def compute_renamed_path(filepath, old_title, new_title):
    """Derive a new path that swaps the old title for the new in the filename.

    Returns the new Path if the old title (sanitized) is found in the stem,
    otherwise None — we won't guess where to splice in the credit.

    Lookup tries exact, then case-insensitive, then a fuzzy match where any
    run of punctuation/whitespace (incl. '_') is treated as equivalent.
    Different tools sanitize unsafe characters differently (e.g. ':' -> '_'
    vs ':' -> '-'), so the title's punctuation may show up in a different
    form in the filename. When new_s strictly extends old_s (the common case
    — we're appending a "(feat. X)" credit), the matched span is kept
    verbatim and only the new suffix is inserted, so the original stem's
    punctuation is preserved.
    """
    stem = filepath.stem
    old_s = sanitize_for_path(old_title)
    new_s = sanitize_for_path(new_title)
    if not old_s or old_s == new_s:
        return None

    fuzzy = False
    idx = stem.find(old_s)
    end = idx + len(old_s)
    if idx < 0:
        idx = stem.lower().find(old_s.lower())
        end = idx + len(old_s)
    if idx < 0:
        segments = [seg for seg in re.split(r"[\W_]+", old_s) if seg]
        if segments:
            pattern = r"[\W_]+".join(re.escape(s) for s in segments)
            m = re.search(pattern, stem, re.IGNORECASE)
            if m:
                idx, end = m.span()
                fuzzy = True
    if idx < 0:
        return None

    if fuzzy and new_s.startswith(old_s):
        new_stem = stem[:end] + new_s[len(old_s):] + stem[end:]
    else:
        new_stem = stem[:idx] + new_s + stem[end:]
    if new_stem == stem:
        return None
    return filepath.with_name(new_stem + filepath.suffix)


def process_file(filepath, dry_run):
    """Process a single music file. Returns True if modified, False if skipped."""
    try:
        audio = mutagen.File(filepath, easy=True)
    except Exception as e:
        print(f"  ERROR reading {filepath}: {e}")
        return False

    if audio is None or audio.tags is None:
        return False

    album_artist = get_tag(audio.tags, "albumartist")
    artist = get_tag(audio.tags, "artist")
    title = get_tag(audio.tags, "title")

    if not album_artist or not title:
        return False

    new_title = title
    artist_update_needed = False

    if artist and artist != album_artist:
        featured_name, feat_credit = extract_featured(artist, album_artist)
        if feat_credit == "skip":
            print(f"  WARNING: '{filepath.name}': artist '{artist}' doesn't start with "
                  f"album artist '{album_artist}', skipping")
            return False
        artist_update_needed = True
        if feat_credit and not TITLE_HAS_FEAT.search(title):
            new_title = f"{title} ({feat_credit})"

    # Decide whether the filename needs to change.
    new_path = None
    rename_warning = None
    rename_from_title = None
    if new_title != title:
        # Tag change — splice the new title in over the old one.
        rename_from_title = title
    elif TITLE_HAS_FEAT.search(title) and not HAS_FEAT.search(filepath.stem):
        # Previously-tagged file: title has the credit but filename doesn't.
        # Strip the trailing "(feat. X)" to recover the base for the splice.
        base = strip_trailing_feat_parenthetical(title)
        if base and base != title:
            rename_from_title = base

    if rename_from_title is not None:
        new_path = compute_renamed_path(filepath, rename_from_title, new_title)
        if new_path is None:
            rename_warning = "cannot derive new filename — title not found in stem"
        elif new_path.exists() and new_path != filepath:
            # On case-insensitive filesystems a case-only rename will show the
            # target as existing but pointing at the same inode; allow that.
            try:
                same = filepath.samefile(new_path)
            except OSError:
                same = False
            if not same:
                rename_warning = f"target {new_path.name!r} already exists"
                new_path = None

    if not artist_update_needed and new_path is None:
        return False

    prefix = "[DRY RUN] " if dry_run else ""
    print(f"{prefix}{filepath}")
    if artist_update_needed:
        print(f"  artist: {artist} -> {album_artist}")
    if new_title != title:
        print(f"  title:  {title} -> {new_title}")
    if new_path:
        print(f"  rename: {filepath.name} -> {new_path.name}")
    if rename_warning:
        print(f"  WARNING: {rename_warning}")

    if not dry_run:
        if artist_update_needed:
            audio.tags["artist"] = [album_artist]
            if new_title != title:
                audio.tags["title"] = [new_title]
            audio.save()
        if new_path:
            filepath.rename(new_path)

    return True


def main():
    parser = argparse.ArgumentParser(
        description="Copy album artist to artist field, preserving featured artist info in the title."
    )
    parser.add_argument("path", type=Path, help="Music file or directory to scan")
    parser.add_argument("-r", "--recursive", action="store_true", help="Scan subdirectories (ignored when path is a file)")
    parser.add_argument("-n", "--dry-run", action="store_true", help="Preview changes without writing")
    args = parser.parse_args()

    if args.path.is_file():
        if args.path.suffix.lower() not in EXTENSIONS:
            print(f"Error: {args.path} is not a recognized music file "
                  f"(extensions: {', '.join(sorted(EXTENSIONS))})", file=sys.stderr)
            sys.exit(1)
        files = [args.path]
    elif args.path.is_dir():
        files = []
        for ext in EXTENSIONS:
            pattern = f"**/*{ext}" if args.recursive else f"*{ext}"
            files.extend(args.path.glob(pattern))
        files.sort()
    else:
        print(f"Error: {args.path} is not a file or directory", file=sys.stderr)
        sys.exit(1)

    modified = 0
    skipped = 0
    for f in files:
        if process_file(f, args.dry_run):
            modified += 1
        else:
            skipped += 1

    label = "[DRY RUN] " if args.dry_run else ""
    print(f"\n{label}{modified} files modified, {skipped} files skipped")


if __name__ == "__main__":
    main()
