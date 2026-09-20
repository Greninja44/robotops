"""Documentation hygiene: every relative link / image in the Markdown files resolves, every #anchor exists,
and the tracked files contain no machine-specific paths or credentials."""
import re
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
LINK = re.compile(r"(?<!\!)\[[^\]]*\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)|!\[[^\]]*\]\(([^)\s]+)\)|<img[^>]+src=\"([^\"]+)\"|<a[^>]+href=\"([^\"]+)\"")


def tracked_and_new() -> list[Path]:
    out = subprocess.run(["git", "ls-files", "-co", "--exclude-standard"], cwd=ROOT, capture_output=True, text=True, check=True).stdout
    return [ROOT / p for p in out.splitlines() if (ROOT / p).is_file()]


def markdown_files() -> list[Path]:
    return [p for p in tracked_and_new() if p.suffix == ".md" and "node_modules" not in p.parts]


def slug(heading: str) -> str:
    s = re.sub(r"[`*_]", "", heading.strip().lower())
    s = re.sub(r"[^\w\- ]", "", s)
    return s.replace(" ", "-")


def anchors(path: Path) -> set[str]:
    text = re.sub(r"```.*?```", "", path.read_text(), flags=re.S)
    return {slug(m.group(1)) for m in re.finditer(r"^#{1,6}\s+(.+?)\s*$", text, flags=re.M)}


def links(path: Path) -> list[str]:
    text = re.sub(r"```.*?```", "", path.read_text(), flags=re.S)
    text = re.sub(r"`[^`\n]*`", "", text)
    return [next(g for g in m.groups() if g) for m in LINK.finditer(text)]


@pytest.mark.parametrize("md", markdown_files(), ids=lambda p: str(p.relative_to(ROOT)))
def test_relative_links_and_anchors_resolve(md):
    problems = []
    for target in links(md):
        if re.match(r"^[a-z][a-z0-9+.-]*:", target):      # http(s):, mailto:
            continue
        path_part, _, frag = target.partition("#")
        dest = md if not path_part else (md.parent / path_part).resolve()
        if not dest.exists():
            problems.append(f"missing file: {target}")
            continue
        if frag and dest.suffix == ".md" and frag not in anchors(dest):
            problems.append(f"missing anchor: {target}")
    assert not problems, f"{md.relative_to(ROOT)}: {problems}"


def test_readme_references_existing_media():
    readme = (ROOT / "README.md").read_text()
    for needed in ("docs/media/hero-demo.gif", "docs/media/hero-demo.mp4", "docs/examples/controller_failure_trace.json"):
        assert needed in readme and (ROOT / needed).exists()
    assert (ROOT / "docs/media/hero-demo.gif").stat().st_size < 10 * 1024 * 1024


TEXT_SUFFIXES = {".py", ".md", ".sh", ".json", ".ts", ".tsx", ".css", ".html", ".xml", ".txt", ".ini", ".yml", ".yaml"}
FORBIDDEN = [
    (re.compile(r"/home/[a-z][\w.-]*/"), "absolute home-directory path"),
    (re.compile(r"[A-Z]:\\Users\\", re.I), "Windows user path"),
    (re.compile(r"\b(ghp_[A-Za-z0-9]{20,}|sk-[A-Za-z0-9]{20,}|AKIA[0-9A-Z]{16}|-----BEGIN [A-Z ]*PRIVATE KEY)"), "credential"),
]


def test_no_machine_paths_or_credentials_in_tracked_files():
    hits = []
    for p in tracked_and_new():
        if p.suffix not in TEXT_SUFFIXES or p.name == "package-lock.json" or p.name == "test_docs_links.py":
            continue
        text = p.read_text(errors="ignore")
        for rx, what in FORBIDDEN:
            for m in rx.finditer(text):
                hits.append(f"{p.relative_to(ROOT)}: {what}: {m.group(0)[:40]}")
    assert not hits, hits
