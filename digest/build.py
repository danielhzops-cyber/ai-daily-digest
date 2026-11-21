"""Rebuild the HTML site from saved data without calling any API.

    python -m digest.build            # data/ -> docs/
    python -m digest.build --preview  # preview/data -> preview/site
Handy after editing templates or CSS.
"""
import argparse

from .common import ROOT, Paths, load_config, log
from .site import build_site

if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--preview", action="store_true")
    paths = Paths(preview=p.parse_args().preview)
    build_site(paths, load_config())
    log(f"Site rebuilt in {paths.site.relative_to(ROOT)}/")
