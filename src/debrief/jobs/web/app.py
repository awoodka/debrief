"""The jobs site (docs/jobs/PLAN.md, M4): jobs.alexwoodka.com, private behind Cloudflare Access.

Server-rendered pages, plain form posts, no JavaScript. It shows what the nightly run stored and
writes only Alex's own edits: the Tracked mark, the profile and the watchlist. It never collects
jobs or calls the model; adding a watchlist company looks up its board (watch.add) through the
same polite, robots-checked client as the collector.

Every request passes the gate first: a valid Cloudflare Access token for an allowed email
(auth.py), else 403. A form post must also come from the site itself (its Origin, or
Sec-Fetch-Site: same-origin), else 403. While a rebuild runs (collect.rebuilding_marker) writes
are refused, since the rebuild would lose them.
"""
import os
from datetime import datetime, timezone
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape
from starlette.applications import Starlette
from starlette.concurrency import run_in_threadpool
from starlette.middleware import Middleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import HTMLResponse, PlainTextResponse, RedirectResponse
from starlette.routing import Route

from .. import config as jconfig, profile as profiles, watch
from ..collect import rebuilding_marker
from ..store import JobsStore, state_path
from . import data, profile_form as pf
from .auth import AccessVerifier

SECURITY_HEADERS = {
    "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; form-action 'self'; base-uri 'none'; "
                               "frame-ancestors 'none'",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "same-origin",
    "Cache-Control": "no-store",
}
TEMPLATES = Path(__file__).with_name("templates")


def _now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _back(path):
    """A redirect target from a form: a path on this site, never another host."""
    return path if isinstance(path, str) and path.startswith("/") and not path.startswith("//") and "\\" not in path else "/"


def create_app(cfg=None, verifier=None, origin=None, env=None, client=None):
    env = os.environ if env is None else env
    cfg = cfg or jconfig.load()
    verifier = verifier or AccessVerifier.from_env(env)
    origin = (origin or env.get("JOBS_WEB_ORIGIN") or "https://jobs.alexwoodka.com").rstrip("/")
    jinja = Environment(loader=FileSystemLoader(TEMPLATES), autoescape=select_autoescape(["html"]))

    def page(request, template, status=200, **ctx):
        body = jinja.get_template(template).render(email=getattr(request.state, "email", ""), path=request.url.path,
                                               catchups=_with_store(data.catchup_lists), **ctx)
        return HTMLResponse(body, status_code=status)

    def _with_store(fn, *a):
        store = JobsStore(state_path(cfg))
        try:
            return fn(store, *a)
        finally:
            store.close()

    def rebuilding():
        return rebuilding_marker(cfg).exists()

    def busy(request):
        return page(request, "message.html", status=503, title="Maintenance",
                    message="The job data is being rebuilt. Try again in a minute.")

    class Gate(BaseHTTPMiddleware):
        async def dispatch(self, request, call_next):
            email = await run_in_threadpool(verifier.email_for, request.headers.get("cf-access-jwt-assertion"))
            if not email:
                return PlainTextResponse("Forbidden", status_code=403, headers=SECURITY_HEADERS)
            if request.method not in ("GET", "HEAD"):
                o = (request.headers.get("origin") or "").rstrip("/")
                if not (o == origin or (not o and request.headers.get("sec-fetch-site") == "same-origin")):
                    return PlainTextResponse("Forbidden (form from another site)", status_code=403, headers=SECURITY_HEADERS)
            request.state.email = email
            resp = await call_next(request)
            for k, v in SECURITY_HEADERS.items():
                resp.headers[k] = v
            return resp

    # ------------------------------------------------------------------------------- lists
    def today(request):
        prof = profiles.latest(cfg)[1]
        view = _with_store(data.daily_view, prof, request.query_params.get("date"))
        return page(request, "today.html", view=view, back=request.url.path + ("?" + request.url.query if request.url.query else ""))

    def catchup(request):
        name = request.path_params["name"]
        prof = profiles.latest(cfg)[1]
        view = _with_store(data.catchup_view, prof, name)
        if view is None:
            return page(request, "message.html", status=404, title="Not found", message=f"No list called {name}.")
        return page(request, "catchup.html", view=view, back=request.url.path)

    async def track(request):
        form = await request.form()
        pid, on, back = str(form.get("posting_id") or ""), form.get("on") == "1", _back(form.get("back"))
        if rebuilding():
            return busy(request)

        def write(store):
            if store.posting(pid) is None:
                return False
            with store.transaction():
                store.set_tracked(pid, on, _now())
            return True
        if not await run_in_threadpool(_with_store, write):
            return page(request, "message.html", status=404, title="Not found", message="That job isn't in the data.")
        return RedirectResponse(f"{back}#{data.anchor(pid)}", status_code=303)

    # ---------------------------------------------------------------------------- watchlist
    def show_watchlist(request, form, *, note="", status=200, message=None, notice=None, checking=False, changes=()):
        prof = profiles.latest(cfg)[1]
        rows = _with_store(lambda s: data.watch_view(cfg, s, prof))
        return page(request, "watchlist.html", status=status, rows=rows, form=form.view(prof, pf.WATCH_TABLE) if prof else None,
                    note=note, message=message or request.query_params.get("msg"), notice=notice, checking=checking,
                    changes=pf.by_group(changes))

    def watchlist(request):
        return show_watchlist(request, pf.open_form(*profiles.latest(cfg), pf.WATCH_TABLE))

    async def watchlist_edit(request):
        posted = await request.form()
        note, checking = str(posted.get("note") or "").strip(), posted.get("action") != "save"
        latest = profiles.latest(cfg)
        if latest[1] is None:
            return RedirectResponse("/watchlist", status_code=303)
        sub = pf.submit(posted, latest, lambda n: profiles.version(cfg, n), pf.WATCH_TABLE)
        return await finish(request, sub, show_watchlist, "/watchlist", note, checking, "edited the watchlist")

    async def watch_add(request):
        form = await request.form()
        name, url = str(form.get("name") or "").strip(), str(form.get("url") or "").strip() or None
        if rebuilding():
            return busy(request)
        if not name:
            return RedirectResponse("/watchlist?msg=" + _q("Give the company's name."), status_code=303)

        def add():
            store = JobsStore(state_path(cfg))
            try:
                entry, notes, version = watch.add(cfg, name, url=url, client=client, store=store)
            finally:
                store.close()
            found = f"board {entry['board']}" if entry.get("board") else "no public board found"
            return f"Added {name} ({found}; profile version {version}). {entry.get('note') or ''} {' '.join(notes)}".strip()
        try:
            msg = await run_in_threadpool(add)
        except watch.WatchError as e:
            msg = str(e)
        except profiles.ProfileError:
            msg = pf.add_refused(name, profiles.latest(cfg)[1])
        return RedirectResponse("/watchlist?msg=" + _q(msg), status_code=303)

    def watch_remove_confirm(request):
        return page(request, "watch_remove.html", name=request.query_params.get("name") or "")

    async def watch_remove(request):
        form = await request.form()
        name = str(form.get("name") or "").strip()
        if rebuilding():
            return busy(request)
        try:
            version = await run_in_threadpool(watch.remove, cfg, name)
            msg = f"Removed {name} (profile version {version})."
        except watch.WatchError as e:
            msg = str(e)
        return RedirectResponse("/watchlist?msg=" + _q(msg), status_code=303)

    # ------------------------------------------------------------------------------ profile
    async def finish(request, sub, draw, home, note, checking, default_note):
        if sub.form.errors:
            return draw(request, sub.form, note=note, status=409 if sub.conflict else 400, checking=checking)
        if checking:
            return draw(request, sub.form, note=note, message=sub.check_message, checking=True, changes=sub.changes)
        if rebuilding():
            return draw(request, sub.form, note=note, status=503,
                        notice="The job data is being rebuilt, so nothing was saved. Your changes are still here. "
                               "Press Save again in a minute.")
        version, saved = await run_in_threadpool(profiles.save, cfg, sub.doc, note or default_note)
        return RedirectResponse(f"{home}?msg=" + _q(sub.saved_message(version, saved)), status_code=303)

    def show(request, form, *, note="", status=200, message=None, notice=None, checking=False, changes=()):
        return page(request, "profile.html", status=status, form=form.view(profiles.version(cfg, form.base)), note=note,
                    message=message or request.query_params.get("msg"), notice=notice, checking=checking,
                    changes=pf.by_group(changes), history=data.profile_history(cfg))

    def profile_get(request):
        return show(request, pf.open_form(*profiles.latest(cfg)))

    async def profile_post(request):
        posted = await request.form()
        note, checking = str(posted.get("note") or "").strip(), posted.get("action") != "save"
        sub = pf.submit(posted, profiles.latest(cfg), lambda n: profiles.version(cfg, n))
        return await finish(request, sub, show, "/profile", note, checking, "edited on the site")

    def version_page(request, n, *, status=200, message=None, errors=(), note=""):
        doc = profiles.version(cfg, n)
        if doc is None:
            return page(request, "message.html", status=404, title="Not found", message=f"No profile version {n}.")
        current_n, current = profiles.latest(cfg)
        before = profiles.version(cfg, n - 1)
        history = data.profile_history(cfg)
        return page(request, "profile_version.html", status=status, n=n, current=current_n, message=message, errors=errors, note=note,
                    entry=next(h for h in history if h["version"] == n), first=before is None,
                    previous=n - 1 if before is not None else None, following=n + 1 if n < current_n else None,
                    changes=pf.by_group(pf.changes(before, doc)) if before is not None else [],
                    restoring=pf.by_group(pf.changes(current, doc)) if n != current_n else [], settings=pf.read_only(doc))

    def profile_version(request):
        return version_page(request, request.path_params["n"])

    async def profile_restore(request):
        """Exactly version n's content, so the id comes back and its stored answers are reused."""
        n, posted = request.path_params["n"], await request.form()
        note = str(posted.get("note") or "").strip()
        doc = profiles.version(cfg, n)
        if doc is None:
            return page(request, "message.html", status=404, title="Not found", message=f"No profile version {n}.")
        if rebuilding():
            return busy(request)
        if str(posted.get("base")) != str(profiles.latest(cfg)[0]):
            return version_page(request, n, status=409, note=note,
                                message="The profile changed while this page was open, so nothing was restored. "
                                        "Below is what restoring would change now.")
        if bad := pf.plain_problems(doc):
            return version_page(request, n, status=400, errors=bad, note=note)
        version, saved = await run_in_threadpool(profiles.save, cfg, doc, note or f"restored version {n}")
        msg = f"Restored version {n} as version {version}." if saved else f"No change: it's the same as version {version}."
        return RedirectResponse("/profile?msg=" + _q(msg), status_code=303)

    # ----------------------------------------------------------------------------- coverage
    def coverage_page(request):
        return page(request, "coverage.html", view=_with_store(lambda s: data.coverage_view(cfg, s)))

    routes = [
        Route("/", today), Route("/catchup/{name}", catchup), Route("/track", track, methods=["POST"]),
        Route("/watchlist", watchlist), Route("/watchlist/add", watch_add, methods=["POST"]),
        Route("/watchlist/edit", watchlist_edit, methods=["POST"]),
        Route("/watchlist/remove", watch_remove_confirm), Route("/watchlist/remove", watch_remove, methods=["POST"]),
        Route("/profile", profile_get), Route("/profile", profile_post, methods=["POST"]),
        Route("/profile/v/{n:int}", profile_version), Route("/profile/v/{n:int}/restore", profile_restore, methods=["POST"]),
        Route("/coverage", coverage_page),
    ]
    return Starlette(routes=routes, middleware=[Middleware(Gate)])


def _q(s):
    from urllib.parse import quote
    return quote(s, safe="")
