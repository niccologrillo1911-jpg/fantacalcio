"""Downloads a league calendar from Fantacalcio Leghe by driving a headless browser.

Fantacalcio Leghe has no official API and protects its endpoints with a browser
challenge, so we log in through the real login form and download the same
'Calendario' Excel a user would download by hand.
"""

import asyncio
import base64
import logging
import os
import re
import sys
import unicodedata
from pathlib import Path

logger = logging.getLogger(__name__)

BASE = "https://leghe.fantacalcio.it"
LOGIN_URL = f"{BASE}/login"
APP_KEY = "ICiELOObd5DF5uJEATi77CRvHiiRuMU0"  # public web-app key shipped in Fantacalcio Leghe's own JS bundle
UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
)

_install_lock = asyncio.Lock()


class FantalegheError(Exception):
    """Error with a user-facing Italian message."""


def _set_browsers_path() -> None:
    if "PLAYWRIGHT_BROWSERS_PATH" not in os.environ and Path("/pw-browsers").is_dir():
        os.environ["PLAYWRIGHT_BROWSERS_PATH"] = "/pw-browsers"


async def _install_browser() -> None:
    async with _install_lock:
        proc = await asyncio.create_subprocess_exec(
            sys.executable, "-m", "playwright", "install", "chromium-headless-shell",
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
        )
        out, _ = await proc.communicate()
        logger.info("playwright install: %s", out.decode(errors="ignore")[-500:])


async def _launch(pw):
    args = ["--no-sandbox", "--disable-dev-shm-usage"]
    try:
        return await pw.chromium.launch(headless=True, args=args)
    except Exception as exc:
        if "Executable doesn't exist" not in str(exc):
            raise
        await _install_browser()
        return await pw.chromium.launch(headless=True, args=args)


def _competition_candidates(payload) -> list[tuple[str, str]]:
    found: list[tuple[str, str]] = []

    def walk(node):
        if isinstance(node, dict):
            cid = node.get("id") or node.get("id_competizione") or node.get("competitionId")
            name = node.get("name") or node.get("nome") or node.get("nome_competizione")
            if cid is not None and isinstance(name, str):
                found.append((str(cid), name))
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)

    walk(payload)
    return found


_FETCH_JS = """
async ([url, appKey]) => {
  const r = await fetch(url, { headers: { app_key: appKey }, credentials: 'include' });
  if (!r.ok) return null;
  const ct = r.headers.get('content-type') || '';
  if (ct.includes('json') || ct.includes('html')) return null;
  const buf = new Uint8Array(await r.arrayBuffer());
  let s = '';
  for (let i = 0; i < buf.length; i++) s += String.fromCharCode(buf[i]);
  return btoa(s);
}
"""


def _norm(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().lower()).strip()


def _slug(text: str) -> str:
    return _norm(text).replace(" ", "-")


ALIAS_KEYS = ("alias", "alias_lega", "aliasLega", "slug")
NAME_KEYS = ("nome", "name", "nome_lega", "nomeLega", "denominazione")


def _league_candidates(payloads: list) -> list[tuple[str, str]]:
    """(name, alias) pairs found anywhere in the JSON the site loaded after login."""
    found: list[tuple[str, str]] = []

    def walk(node):
        if isinstance(node, dict):
            alias = next((node[k] for k in ALIAS_KEYS if isinstance(node.get(k), str) and node[k]), None)
            name = next((node[k] for k in NAME_KEYS if isinstance(node.get(k), str) and node[k]), None)
            if alias and name:
                found.append((name, alias.strip("/").split("/")[-1]))
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)

    walk(payloads)
    return found


_LINKS_JS = """
() => Array.from(document.querySelectorAll('a[href]')).map(a => [a.innerText || a.title || '', a.getAttribute('href')])
"""


def _resolve_alias(league_name: str, pairs: list[tuple[str, str]], links: list) -> str | None:
    wanted = _norm(league_name)
    for name, alias in pairs:
        if _norm(name) == wanted:
            return alias
    for text, href in links:
        if not isinstance(href, str) or not isinstance(text, str):
            continue
        path = href.replace(BASE, "").strip("/").split("?")[0]
        if path and "/" not in path and wanted and _norm(text.split("\n")[0]) == wanted:
            return path
    # last resort: the name written the way Fantacalcio Leghe builds its addresses, or an address pasted by mistake
    for name, alias in pairs:
        if alias == _slug(league_name) or alias == league_name.strip().strip("/").split("/")[-1]:
            return alias
    return None


async def fetch_calendar_files(league_name: str, username: str, password: str) -> tuple[str, list[bytes]]:
    """Log in, find the league by its real name, return (alias, candidate calendar files)."""
    from playwright.async_api import async_playwright

    _set_browsers_path()
    async with async_playwright() as pw:
        try:
            browser = await _launch(pw)
        except Exception as exc:
            logger.exception("browser launch failed")
            raise FantalegheError("Il servizio di automazione non è disponibile al momento. Carica il file Excel.") from exc
        try:
            ctx = await browser.new_context(locale="it-IT", user_agent=UA, accept_downloads=True)
            page = await ctx.new_page()
            payloads: list = []
            competitions: list = []

            async def on_response(resp):
                if not resp.ok or "json" not in (resp.headers.get("content-type") or ""):
                    return
                try:
                    data = await resp.json()
                except Exception:
                    return
                payloads.append(data)
                if "/competitions" in resp.url or "ompetizion" in resp.url:
                    competitions.append(data)

            page.on("response", on_response)

            await page.goto(LOGIN_URL, wait_until="networkidle", timeout=60_000)
            for label in ("Accetta", "ACCETTA", "Accetto", "Accetta tutto"):
                try:
                    await page.get_by_role("button", name=label).first.click(timeout=1500)
                    break
                except Exception:
                    continue

            await page.fill('input[formcontrolname="username"]', username, timeout=20_000)
            await page.fill('input[formcontrolname="password"]', password, timeout=20_000)
            async with page.expect_response(
                lambda r: "/onboarding/v1/login" in r.url and r.request.method == "POST", timeout=45_000
            ) as info:
                await page.get_by_role("button", name="LOGIN").first.click(timeout=15_000)
            resp = await info.value
            if resp.status in (400, 401):
                raise FantalegheError("Username o password di Fantacalcio Leghe non corretti.")
            if resp.status == 403:
                raise FantalegheError(
                    "Fantacalcio Leghe ha bloccato l'accesso automatico. Riprova più tardi oppure carica il file Excel."
                )
            if not resp.ok:
                raise FantalegheError(f"Accesso a Fantacalcio Leghe non riuscito (errore {resp.status}).")

            await page.wait_for_timeout(3000)
            try:
                await page.wait_for_load_state("networkidle", timeout=20_000)
            except Exception:
                pass
            links = await page.evaluate(_LINKS_JS)
            alias = _resolve_alias(league_name, _league_candidates(payloads), links)
            if not alias:
                raise FantalegheError(
                    f"Non trovo la lega \"{league_name}\" nel tuo account Fantacalcio Leghe. "
                    "Scrivi il nome esattamente come appare su Fantacalcio Leghe."
                )

            competitions.clear()
            await page.goto(f"{BASE}/{alias}/calendario", wait_until="networkidle", timeout=60_000)
            await page.wait_for_timeout(2000)

            # the cookie banner (pubtech CMP) covers the page and swallows clicks: accept it, then drop it
            for label in ("Accetta", "ACCETTA", "Accetto", "Accetta tutto", "ACCETTA E CHIUDI", "Accetta e chiudi"):
                try:
                    await page.get_by_role("button", name=label).first.click(timeout=1500)
                    break
                except Exception:
                    continue
            await page.evaluate("() => document.querySelectorAll('#pubtech-cmp, [id^=\"pubtech\"]').forEach(e => e.remove())")

            files: list[bytes] = []
            try:
                async with page.expect_download(timeout=45_000) as dl_info:
                    has_fn = await page.evaluate("() => typeof window.downloadCalendar === 'function'")
                    if has_fn:
                        await page.evaluate("() => { window.downloadCalendar(); }")
                    else:
                        await page.locator("#toexcel").or_(page.get_by_text("Scarica ora")).first.click(
                            timeout=10_000, force=True
                        )
                download = await dl_info.value
                path = await download.path()
                if path:
                    files.append(Path(path).read_bytes())
            except Exception as exc:
                logger.info("download button path failed: %s", exc)

            from urllib.parse import urlencode

            seen: set[str] = set()
            for cid, name in _competition_candidates(competitions):
                if cid in seen:
                    continue
                seen.add(cid)
                qs = urlencode({"alias_lega": alias, "id_competizione": cid, "nome_competizione": name})
                b64 = await page.evaluate(_FETCH_JS, [f"{BASE}/servizi/v1_legheCompetizione/excel?{qs}", APP_KEY])
                if b64:
                    files.append(base64.b64decode(b64))
                if len(seen) >= 6:
                    break

            if not files:
                raise FantalegheError(
                    f"Non riesco a scaricare il calendario della lega \"{league_name}\" da Fantacalcio Leghe."
                )
            return alias, files
        except FantalegheError:
            raise
        except Exception as exc:
            logger.exception("fantaleghe sync failed")
            raise FantalegheError(
                "Fantacalcio Leghe non ha risposto come previsto. Riprova più tardi oppure carica il file Excel."
            ) from exc
        finally:
            await browser.close()
