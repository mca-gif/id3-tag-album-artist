"""Verification harness for id3_tag_album_artist.py — not part of the shipped tool.

Tests extract_featured and the rename helpers. File-level tag I/O is delegated
to mutagen, which is well-tested upstream.
"""

import sys
from pathlib import Path

from id3_tag_album_artist import (
    HAS_FEAT,
    compute_renamed_path,
    extract_featured,
    sanitize_for_path,
    strip_trailing_feat_parenthetical,
)


CASES = [
    # (label, artist, album_artist, expected_featured, expected_credit)
    ("standard_feat",
     "Blackalicious feat. Bosko", "Blackalicious",
     "Bosko", "feat. Bosko"),
    ("ft_variant",
     "Artist ft. Friend", "Artist",
     "Friend", "feat. Friend"),
    ("featuring_variant",
     "Artist featuring Pal", "Artist",
     "Pal", "feat. Pal"),
    ("with_variant",
     "Artist with Buddy", "Artist",
     "Buddy", "feat. Buddy"),
    ("amp_only",
     "Artist & Other", "Artist",
     "Other", "feat. Other"),
    ("implicit_comma",
     "Arrested Development, Configa & Speech", "Arrested Development",
     "Configa & Speech", "feat. Configa & Speech"),
    ("album_artist_with_amp",
     "Arrested Development, Configa & Speech feat. Guest",
     "Arrested Development, Configa & Speech",
     "Guest", "feat. Guest"),
    ("case_insensitive_keyword",
     "Artist FEAT. Friend", "Artist",
     "Friend", "feat. Friend"),
    ("nothing_after_album_artist",
     "Blackalicious", "Blackalicious",
     None, None),
    ("only_separators_after",
     "Blackalicious   ", "Blackalicious",
     None, None),
    ("mismatch_skip",
     "Some Other Artist feat. Guest", "Blackalicious",
     None, "skip"),
    ("remainder_already_has_feat",
     # Real-world: artist field is "Artist, X feat. Y" — after stripping the
     # leading comma we get "X feat. Y". We return verbatim to avoid double feat.
     "Artist, X feat. Y", "Artist",
     "X feat. Y", "X feat. Y"),
]


def main():
    failed = 0
    for label, artist, album_artist, want_feat, want_credit in CASES:
        got_feat, got_credit = extract_featured(artist, album_artist)
        ok = got_feat == want_feat and got_credit == want_credit
        status = "OK  " if ok else "FAIL"
        print(f"[{status}] {label}")
        if not ok:
            failed += 1
            print(f"        artist:       {artist!r}")
            print(f"        album_artist: {album_artist!r}")
            print(f"        got:          ({got_feat!r}, {got_credit!r})")
            print(f"        want:         ({want_feat!r}, {want_credit!r})")

    # HAS_FEAT sanity checks (used downstream to guard against double feat. in title)
    assert HAS_FEAT.search("Inspired By (feat. Bosko)")
    assert HAS_FEAT.search("Track ft. Pal")
    assert HAS_FEAT.search("Track featuring Buddy")
    assert not HAS_FEAT.search("Plain Track")
    assert not HAS_FEAT.search("Defeat the Enemy")  # word-boundary check
    print("[OK  ] HAS_FEAT word-boundary checks")

    # sanitize_for_path: strips characters that Windows/macOS reject.
    assert sanitize_for_path("Inspired By (feat. Bosko)") == "Inspired By (feat. Bosko)"
    assert sanitize_for_path("AC/DC") == "AC-DC"
    assert sanitize_for_path('Title: "quoted"') == "Title- -quoted-"
    assert sanitize_for_path("Trailing dots...") == "Trailing dots"
    assert sanitize_for_path("Question?!") == "Question-!"
    assert sanitize_for_path("Why??") == "Why-"  # runs of unsafe chars collapse
    print("[OK  ] sanitize_for_path checks")

    # compute_renamed_path: splice new title into the filename stem.
    p = compute_renamed_path(Path("/m/01 Inspired By.mp3"), "Inspired By", "Inspired By (feat. Bosko)")
    assert p == Path("/m/01 Inspired By (feat. Bosko).mp3"), p
    # Title-bearing portion is case-insensitive (some rippers title-case stems).
    p = compute_renamed_path(Path("/m/inspired by.mp3"), "Inspired By", "Inspired By (feat. Bosko)")
    assert p == Path("/m/Inspired By (feat. Bosko).mp3"), p
    # Slash in title gets sanitized in both lookup and replacement.
    p = compute_renamed_path(Path("/m/AC-DC Live.mp3"), "AC/DC Live", "AC/DC Live (feat. Guest)")
    assert p == Path("/m/AC-DC Live (feat. Guest).mp3"), p
    # No match in stem -> None (caller emits a warning).
    assert compute_renamed_path(Path("/m/track01.mp3"), "Inspired By", "Inspired By (feat. Bosko)") is None
    # Fuzzy match: filename was sanitized by a different tool (':' -> '_'
    # here, ':' -> '-' in our sanitizer). The matched span is kept verbatim
    # so the original punctuation in the stem is preserved.
    p = compute_renamed_path(
        Path("/m/26 - RRNN_ Straight Outta Shibuya.mp3"),
        "RRNN: Straight Outta Shibuya",
        "RRNN: Straight Outta Shibuya (feat. 高木完)",
    )
    assert p == Path("/m/26 - RRNN_ Straight Outta Shibuya (feat. 高木完).mp3"), p
    # Fuzzy match works with kanji in the title body too (kanji are word chars).
    p = compute_renamed_path(
        Path("/m/01 高木完 Song.mp3"),
        "高木完: Song",
        "高木完: Song (feat. Guest)",
    )
    assert p == Path("/m/01 高木完 Song (feat. Guest).mp3"), p
    print("[OK  ] compute_renamed_path checks")

    # strip_trailing_feat_parenthetical: recover the base title for files
    # tagged on a previous run but never renamed.
    assert strip_trailing_feat_parenthetical("Inspired By (feat. Bosko)") == "Inspired By"
    assert strip_trailing_feat_parenthetical("Track [ft. Pal]") == "Track"
    assert strip_trailing_feat_parenthetical("Track (featuring Buddy)") == "Track"
    # Not at the end -> leave it alone (we won't guess where to splice).
    assert strip_trailing_feat_parenthetical("Track (feat. X) (Remix)") == "Track (feat. X) (Remix)"
    # No trailing parenthetical credit -> unchanged.
    assert strip_trailing_feat_parenthetical("Plain Track") == "Plain Track"
    assert strip_trailing_feat_parenthetical("Defeat the Enemy") == "Defeat the Enemy"
    print("[OK  ] strip_trailing_feat_parenthetical checks")

    # End-to-end: a previously-modified file's filename gets the credit spliced
    # in using the recovered base title.
    title = "Inspired By (feat. Bosko)"
    base = strip_trailing_feat_parenthetical(title)
    p = compute_renamed_path(Path("/m/01 Inspired By.mp3"), base, title)
    assert p == Path("/m/01 Inspired By (feat. Bosko).mp3"), p
    print("[OK  ] previously-modified filename catch-up")

    if failed:
        print(f"\n{failed} test(s) failed.")
        sys.exit(1)
    print(f"\nAll {len(CASES)} cases + sanity checks passed.")


if __name__ == "__main__":
    main()
