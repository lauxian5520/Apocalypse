"""天启·深研场 — the Deep Research RL environment, synthesis and training side.

`rl/` depends on `backend/harness`; `backend/` never imports `rl/`. That
direction is deliberate and load-bearing: the corpus reader, the BM25 index and
the environment tools live under `harness/` because they define what the agent
sees, and this package is the training and evaluation machinery wrapped around
them. A dependency the other way would put torch in the web server's import
graph.

The `sys.path` insert below is the same idiom `tools/*.py` uses — the backend is
a top-level package root rather than an installed distribution, so anything
outside it has to say where it is. Doing it here, once, means no individual
module has to.
"""
import os
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BACKEND_DIR = os.path.join(REPO_ROOT, "backend")

for _path in (REPO_ROOT, BACKEND_DIR):
    if _path not in sys.path:
        sys.path.insert(0, _path)
