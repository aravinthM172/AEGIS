# Learn FaultScope (local teaching site)

    python learn/serve.py        # builds from the real source, serves http://localhost:8200, opens the browser

`build.py` embeds the actual repository files and validates every code excerpt's line range, and lists any
tracked source file that has no explanation yet. Explanations live in `content/*.md` (format described at the
top of `build.py`).
