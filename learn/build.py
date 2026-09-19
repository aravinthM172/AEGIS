"""Build the FaultScope learning site data.

Reads the hand-written explanations in learn/content/*.md, embeds the REAL source of every file it
explains (so the "Source" tab and every code excerpt are the actual repository text), and writes
learn/data.js. Fails loudly if an excerpt range is outside its file, and prints which tracked
source files still have no explanation.

Content format (see learn/content/):
  # AREA id | Title | one-line description       starts an area; text below = area intro
  ## FILE path | Title | kind                     a file page (kind: code|config|test|docs|infra)
  ### Heading                                    a section inside the file page
  @snippet 10-30 | caption                       real lines of the current file (or: @snippet other/path.py 5-9 | caption)
  # LESSON id | Title | minutes                  (lessons.md) a lesson; body may use @diagram name and  ?? question || answer
  term :: definition                             (glossary.md)
"""
import json
import re
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
CONTENT = HERE / "content"
OUT = HERE / "data.js"

LANG = {".py": "python", ".java": "java", ".cpp": "cpp", ".hpp": "cpp", ".ts": "ts", ".tsx": "ts", ".js": "ts",
        ".mjs": "ts", ".json": "json", ".yml": "yaml", ".yaml": "yaml", ".md": "md", ".css": "css",
        ".properties": "props", ".xml": "xml", ".txt": "text", ".toml": "text"}


def read_source(rel: str) -> str:
    p = ROOT / rel
    if not p.is_file():
        sys.exit(f"build: explained file does not exist: {rel}")
    return p.read_text(encoding="utf-8", errors="replace").replace("\r\n", "\n")


def lang_of(rel: str) -> str:
    name = Path(rel).name
    if name == "Dockerfile":
        return "docker"
    if name.endswith(".env.example") or name == ".env.example":
        return "props"
    return LANG.get(Path(rel).suffix, "text")


def parse_snippet(arg: str, current: str):
    head, _, caption = arg.partition("|")
    parts = head.split()
    path = current
    if len(parts) == 2:
        path, rng = parts
    else:
        rng = parts[0]
    a, _, b = rng.partition("-")
    return path, int(a), int(b or a), caption.strip()


def make_snippet(path, a, b, caption, sources):
    src = sources.setdefault(path, read_source(path))
    lines = src.split("\n")
    if a < 1 or b > len(lines) or a > b:
        sys.exit(f"build: snippet {path}:{a}-{b} is outside the file ({len(lines)} lines)")
    return {"path": path, "start": a, "end": b, "caption": caption, "lang": lang_of(path),
            "code": "\n".join(lines[a - 1:b])}


def parse_areas(sources):
    areas, area, fil, sec = [], None, None, None
    for f in sorted(CONTENT.glob("*.md")):
        if f.name in ("lessons.md", "glossary.md"):
            continue
        for raw in f.read_text(encoding="utf-8").split("\n"):
            line = raw.rstrip()
            if line.startswith("# AREA "):
                pid, title, desc = [x.strip() for x in line[7:].split("|", 2)]
                area = {"id": pid, "title": title, "desc": desc, "intro": [], "files": []}
                areas.append(area)
                fil = sec = None
            elif line.startswith("## FILE "):
                path, title, kind = [x.strip() for x in line[8:].split("|", 2)]
                src = read_source(path)
                sources[path] = src
                fil = {"path": path, "title": title, "kind": kind, "lang": lang_of(path),
                       "lines": src.count("\n") + 1, "sections": [], "snips": []}
                area["files"].append(fil)
                sec = None
            elif line.startswith("### ") and fil is not None:
                sec = {"h": line[4:].strip(), "md": []}
                fil["sections"].append(sec)
            elif line.startswith("@snippet ") and fil is not None:
                path, a, b, cap = parse_snippet(line[9:], fil["path"])
                fil["snips"].append(make_snippet(path, a, b, cap, sources))
                if sec is None:
                    sec = {"h": "Key parts", "md": []}
                    fil["sections"].append(sec)
                sec["md"].append("{{snip:%d}}" % (len(fil["snips"]) - 1))
            elif fil is not None and sec is not None:
                sec["md"].append(line)
            elif area is not None and fil is None:
                area["intro"].append(line)
    for a in areas:
        a["intro"] = "\n".join(a["intro"]).strip()
        for f in a["files"]:
            for s in f["sections"]:
                s["md"] = "\n".join(s["md"]).strip()
    return areas


def parse_lessons():
    out, cur = [], None
    p = CONTENT / "lessons.md"
    if not p.exists():
        return out
    for raw in p.read_text(encoding="utf-8").split("\n"):
        line = raw.rstrip()
        if line.startswith("# LESSON "):
            lid, title, mins = [x.strip() for x in line[9:].split("|", 2)]
            cur = {"id": lid, "title": title, "minutes": int(mins), "md": []}
            out.append(cur)
        elif cur is not None:
            cur["md"].append(line)
    for c in out:
        c["md"] = "\n".join(c["md"]).strip()
    return out


def parse_glossary():
    out = []
    p = CONTENT / "glossary.md"
    if p.exists():
        for line in p.read_text(encoding="utf-8").split("\n"):
            if "::" in line:
                term, _, d = line.partition("::")
                out.append({"term": term.strip(), "def": d.strip()})
    return sorted(out, key=lambda x: x["term"].lower())


def uncovered(explained):
    tracked = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True, text=True).stdout.split("\n")
    skip = re.compile(r"^(legacy/|llm-service|spark-job|minio|learn/|\.vscode/|.*package-lock\.json$|"
                      r".*\.(png|ico|svg|jar|wrapper)$|java-service/mvnw|java-service/\.mvn/|.*\.dockerignore$|.*\.gitignore$)")
    return [t for t in tracked if t and t not in explained and not skip.match(t)
            and not t.endswith("__init__.py")]


def main():
    sources = {}
    areas = parse_areas(sources)
    explained = {f["path"] for a in areas for f in a["files"]}
    data = {"areas": areas, "lessons": parse_lessons(), "glossary": parse_glossary(),
            "sources": {p: sources[p] for p in sorted(explained)}}
    OUT.write_text("window.LEARN = " + json.dumps(data, ensure_ascii=False) + ";\n", encoding="utf-8")
    n = sum(len(a["files"]) for a in areas)
    print(f"build: {len(areas)} areas, {n} files explained, {len(data['lessons'])} lessons, "
          f"{len(data['glossary'])} glossary terms -> {OUT.name} ({OUT.stat().st_size // 1024} KB)")
    missing = uncovered(explained)
    if missing:
        print(f"build: {len(missing)} tracked files have no explanation yet:")
        for m in missing:
            print("   ", m)


if __name__ == "__main__":
    main()
