"""Spell-checking and dictionary helpers."""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

# Word runs as a writer means them: letters (accented included),
# optionally joined by one apostrophe — "don't", "don’t", "Someone's".
# Digits excluded: "2nd" is not a typo the dictionary can judge.
_WORD_RE = re.compile(r"[^\W\d_]+(?:['’][^\W\d_]+)*", re.UNICODE)

ENGINES = (("aspell", ["list"]), ("hunspell", ["-l"]))
BATCH_TIMEOUT = 15
PROBE_TIMEOUT = 5

_engine: list[str] | None = None
_probed: bool = False
_language: str = ""  # "" means use the system's default dictionary
_session_cache: dict[str, bool] = {}
_word_list_path: Path | None = None
_word_list_words: frozenset[str] = frozenset()
_word_list_stamp: tuple[str, int] | None = None


def set_language(language: str | None) -> None:
    """Set the system language for the next engine probe (``""`` = system
    default). Changing it resets the probe and the word cache — verdicts
    from another language must not leak."""
    global _language
    _language = (language or "").strip()
    reset_probes()


def reset_probes() -> None:
    global _engine, _probed
    _probed = False
    _engine = None
    _session_cache.clear()


def set_word_list(path: Path | None) -> None:
    """Point the squiggle pass at the writer's own word list.

    One word per line; the contents teach the session which words to
    trust. ``None`` (the default) means no list — the system dictionary
    judges alone (docs/PREFERENCES.md).
    """
    global _word_list_path, _word_list_words, _word_list_stamp
    _word_list_path = path
    _word_list_words = frozenset()
    _word_list_stamp = None


def reset() -> None:
    """Forget the probe and the word cache (tests, and a re-install)."""
    global _engine, _probed
    _probed = False
    _engine = None
    _session_cache.clear()
    set_word_list(None)


def _list_words() -> frozenset[str]:
    """The list's words, re-read when its mtime moves.

    Membership is re-checked on every call and never cached as a
    verdict, so editing the list teaches the very next refresh — no
    one has to clear anything."""
    global _word_list_words, _word_list_stamp
    path = _word_list_path
    if path is None:
        return _word_list_words
    try:
        stamp = (str(path), path.stat().st_mtime_ns)
    except OSError:
        stamp = (str(path), -1)
    if stamp != _word_list_stamp:
        _word_list_stamp = stamp
        try:
            _word_list_words = frozenset(
                line.strip() for line in path.read_text(
                    encoding="utf-8").splitlines() if line.strip())
        except OSError:
            _word_list_words = frozenset()
    return _word_list_words


def find_engine(language: str | None = None) -> list[str] | None:
    """The first spell checker on PATH that runs, probed once.

    Once per process, not per keystroke: a dead or absent checker
    answers None every time and the toggle can say so up front.
    ``language`` overrides ``set_language`` for this probe; empty means
    the system's default dictionary.
    """
    global _engine, _probed
    lang = (language.strip() if language is not None else _language) or ""
    key = (lang, _probed)
    if _probed and getattr(find_engine, "_lang", None) == lang:
        return _engine
    # Re-probe when the language changed — verdicts from another
    # language must not leak.
    if getattr(find_engine, "_lang", None) != lang and _probed:
        pass
    find_engine._lang = lang  # type: ignore[attr-defined]
    _probed = True
    engines = []
    for name, base in ENGINES:
        args = list(base)
        if lang:
            args += ["-d", lang]
        engines.append((name, args))
    for name, args in engines:
        path = shutil.which(name)
        if not path:
            continue
        try:
            result = subprocess.run(
                [path, *args], input="", capture_output=True,
                text=True, timeout=PROBE_TIMEOUT,
            )
        except (OSError, subprocess.TimeoutExpired):
            continue
        if result.returncode == 0:
            _engine = [path, *args]
            return _engine
    _engine = None
    return None


def _unknown(engine: list[str], words: list[str]) -> set[str] | None:
    """Batch-check every unseen word in one spawn.

    None means the checker failed and nothing may be concluded — the
    caller flags nothing and, deliberately, caches nothing either, so
    the next refresh retries instead of trusting a broken reading.
    """
    try:
        result = subprocess.run(
            engine, input="\n".join(words),
            capture_output=True, text=True, timeout=BATCH_TIMEOUT,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    return {line.strip()
            for line in result.stdout.splitlines() if line.strip()}


def misspellings(
    text: str,
    cache: dict[str, bool] | None = None,
) -> list[tuple[int, int]]:
    """(start, end) UTF-16 ranges of words the dictionary rejects.

    The cache maps exact words to verdicts for the session; words in it
    are never sent to the checker again, so a re-read of an unchanged
    document costs no processes at all.
    """
    engine = find_engine()
    if engine is None or not text:
        return []
    if cache is None:
        cache = _session_cache
    matches = list(_WORD_RE.finditer(text))
    if not matches:
        return []
    own_words = _list_words()
    unseen: list[str] = []
    seen: set[str] = set()
    for match in matches:
        word = match.group(0)
        if word in own_words:
            # The writer's list judges its own words; the engine is
            # never asked about them and no verdict is needed.
            seen.add(word)
            continue
        if word not in seen and word not in cache:
            seen.add(word)
            unseen.append(word)
    if unseen:
        unknown = _unknown(engine, unseen)
        if unknown is None:
            return []
        for word in unseen:
            cache[word] = word in unknown
    return [
        (_utf16(text, match.start()), _utf16(text, match.end()))
        for match in matches
        # True = the checker rejected it, and only those are squiggled.
        # List membership is judged live, never through a cached verdict:
        # adding the word to the list un-flags it at the very next refresh.
        if cache.get(match.group(0), False)
        and match.group(0) not in own_words
    ]


def _utf16(text: str, index: int) -> int:
    """Code-point index → QTextDocument offset (UTF-16 code units)."""
    return len(text[:index].encode("utf-16-le")) // 2


_STOP_WORDS = frozenset({
    "The", "This", "That", "These", "Those", "It", "He", "She", "We",
    "They", "One", "Now", "Then", "Later", "Here", "There", "When",
    "While", "After", "Before", "Also", "But", "And", "For", "With",
    "From", "Into", "Over", "Under", "About", "Between",
})


def entity_candidates(project) -> list[tuple[str, int]]:
    """Capitalised words in the project not yet in the word list.

    Scans titles, synopses, metadata values and prose for tokens like
    `Virelia`, tallies them, and keeps only the ones the system checker
    also flags — so common English sentence starters never clutter the
    harvest dialog. Returns `[(word, count), …]`, most frequent first.
    Pure scan, no AI call (`copy-paste champion` accelerator).
    """
    from collections import Counter
    texts: list[str] = []
    try:
        docs = project.documents()
    except Exception:
        return []
    own_probe: set[str] = set()
    try:
        own_probe.update(_list_words())
    except Exception:
        pass
    # Also read the project file directly: the session word-list path
    # may still point at the previous project, and already-known
    # words must never reappear as candidates.
    try:
        root = getattr(project, "root", None)
        if root is not None:
            target = Path(root) / "dictionary.txt"
            if target.is_file():
                own_probe.update(
                    line.strip()
                    for line in target.read_text(encoding="utf-8").splitlines()
                    if line.strip())
    except Exception:
        pass
    for doc in docs:
        texts.append(getattr(doc, "title", "") or "")
        texts.append(getattr(doc, "synopsis", "") or "")
        try:
            for value, _vt, _opts in project.metadata_entries(doc.id).values():
                if value:
                    texts.append(str(value))
        except Exception:
            pass
        try:
            texts.append(project.plain_text(project.read_content(doc.id)))
        except Exception:
            pass
    try:
        for value, _vt, _opts in project.project_metadata().values():
            if value:
                texts.append(str(value))
    except Exception:
        pass

    counts: Counter[str] = Counter()
    for text in texts:
        if not text:
            continue
        for match in _WORD_RE.finditer(text):
            word = match.group(0)
            if len(word) <= 2 or word in _STOP_WORDS:
                continue
            if not word[0].isupper():
                continue
            if word in own_probe:
                continue
            counts[word] += 1
    if not counts:
        return []
    # Keep only what the system dictionary would flag: an English word
    # like "London" isn't a worldbuilding term, don't bother prompting.
    engine = find_engine()
    if engine is not None:
        unknown = _unknown(engine, list(counts.keys()))
        if unknown is not None:
            counts = Counter({w: c for w, c in counts.items() if w in unknown})
    return sorted(counts.items(), key=lambda kv: (-kv[1], kv[0].casefold()))[:200]
