# Debrief — build & publish the static archive site.
PY := .venv/bin/python
# Serve under a sub-path (alexwoodka.com/debrief). Set BASE= (empty) for a domain-root/subdomain deploy.
BASE ?= /debrief

.PHONY: site preview publish help

help:
	@echo "make site     - build the static archive into ./site   (BASE=$(BASE))"
	@echo "make preview  - build, then serve locally at http://localhost:8000$(BASE)/"
	@echo "make publish  - build, then deploy to Cloudflare Pages (needs: wrangler login)"

site:                       ## Build the static site into ./site
	DEBRIEF_SITE_BASE=$(BASE) $(PY) dashboard/build_site.py

preview: site               ## Build, then preview locally
	@echo "serving ./site — open http://localhost:8000$(BASE)/   (Ctrl-C to stop)"
	$(PY) -m http.server 8000 --directory site

publish: site               ## Build, then deploy to Cloudflare Pages
	wrangler pages deploy site --project-name debrief
