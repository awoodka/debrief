# Debrief — build and preview the static archive site.
# The live site (debrief.alexwoodka.com) is built and published by the server's daily run, not from here.
PY := .venv/bin/python
# Serves at the domain root by default. Set BASE=/some-path to build for a sub-path instead.
BASE ?=

.PHONY: site preview help

help:
	@echo "make site     - build the static archive into ./site   (BASE=$(BASE))"
	@echo "make preview  - build, then serve locally at http://localhost:8000$(BASE)/"

site:                       ## Build the static site into ./site
	DEBRIEF_SITE_BASE=$(BASE) $(PY) dashboard/build_site.py

preview: site               ## Build, then preview locally
	@echo "serving ./site — open http://localhost:8000$(BASE)/   (Ctrl-C to stop)"
	$(PY) -m http.server 8000 --directory site
