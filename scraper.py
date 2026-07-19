"""Scraper og databehandling for brugte biler paa Bilbasen.

Scriptet henter aktuelle bilannoncer fra en Bilbasen-soegning, gennemgaar ALLE
resultatsider (paginering), normaliserer og scorer bilerne og gemmer resultatet
i JSON-filer, som den statiske hjemmeside laeser.

Designprincipper:
- Respekterer robots.txt, en konfigurerbar forsinkelse og en tydelig User-Agent.
- Forsoeger ikke at omgaa CAPTCHA, login eller anden adgangskontrol.
- Bruger Bilbasens annonce-id som primaer identifikation og undgaar dubletter.
- Cacher detaljesider og genbesoeger dem kun naar noedvendigt.
- Foelger pris- og kilometeraendringer samt forsvundne annoncer.
- Kan koere mod gemte HTML-testdata uden netvaerksadgang.
- Understoetter manuel import af HTML, JSON, CSV og semikolonsepareret tekst.

Brug:
    python scraper.py                 # live scrape (kraever netvaerk + tilladelse)
    python scraper.py --fixtures      # parse gemte HTML-filer i tests/fixtures
    python scraper.py --import fil     # manuel import (html/json/csv/txt)
    python scraper.py --score-only     # genberegn score fra eksisterende cars.json
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import logging
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple
from urllib.parse import urljoin, urlparse
from urllib import robotparser

import normalizer
import scoring

try:
    import requests
except ImportError:  # requests er kun noedvendig ved live scrape.
    requests = None

try:
    from bs4 import BeautifulSoup
except ImportError:  # BeautifulSoup er noedvendig til HTML-parsing.
    BeautifulSoup = None


BASE_URL = "https://www.bilbasen.dk"
SEARCH_URL = (
    "https://www.bilbasen.dk/brugt/bil?adaptivecruisecontrol&cartype=stationcar"
    "&cartype=suv&cartype=cuv&cartype=mpv&cartype=sedan&cartype=hatchback"
    "&fuel=1&fuel=6&gear=automatic&mileageto=125000&mintow=1600&priceto=250000"
    "&pricetype=Retail&regfrom=2019-01&sortby=price&sortorder=asc"
)
USER_AGENT = "BilTilCampingvognBot/1.0 (personligt beslutningsvaerktoej)"

ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "data"
CACHE_DIR = ROOT / "cache"
FIXTURE_DIR = ROOT / "tests" / "fixtures"

REQUEST_DELAY_SECONDS = 4.0
MAX_PAGES = 50
MAX_RETRIES = 3
DETAIL_CACHE_MAX_AGE_HOURS = 24 * 7  # genbesoeg detaljeside efter en uge

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("scraper")


# --------------------------------------------------------------------------- #
# Tid og fil-hjaelpere
# --------------------------------------------------------------------------- #
def now_iso() -> str:
    """Returner nuvaerende UTC-tidsstempel i ISO-format.

    Returns:
        Tidsstempel som ISO 8601-streng.
    """
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def load_json(path: Path, default: Any) -> Any:
    """Indlaes JSON fra en fil med en fallback-vaerdi.

    Args:
        path: Sti til filen.
        default: Vaerdi der returneres hvis filen mangler eller er ugyldig.

    Returns:
        Det indlaeste JSON-indhold eller default.
    """
    if not path.exists():
        return default
    try:
        with path.open("r", encoding="utf-8") as fh:
            return json.load(fh)
    except (json.JSONDecodeError, OSError) as exc:
        log.warning("Kunne ikke laese %s: %s", path, exc)
        return default


def save_json(path: Path, data: Any) -> None:
    """Gem data som pretty-printet UTF-8 JSON.

    Args:
        path: Destination.
        data: Serialiserbart data.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)


# --------------------------------------------------------------------------- #
# Netvaerk (kun ved live scrape)
# --------------------------------------------------------------------------- #
def check_robots(url: str) -> bool:
    """Kontroller om robots.txt tillader at hente den givne URL.

    Args:
        url: Absolut URL der oenskes hentet.

    Returns:
        True hvis tilladt (eller robots.txt ikke kan laeses), ellers False.
    """
    try:
        rp = robotparser.RobotFileParser()
        rp.set_url(urljoin(BASE_URL, "/robots.txt"))
        rp.read()
        allowed = rp.can_fetch(USER_AGENT, url)
        if not allowed:
            log.warning("robots.txt tillader ikke hentning af %s", url)
        return allowed
    except Exception as exc:  # robuste defaults ved fejl.
        log.warning("Kunne ikke laese robots.txt (%s) - fortsaetter forsigtigt", exc)
        return True


def fetch(url: str, session: "requests.Session") -> Optional[str]:
    """Hent en URL med retries, timeout og hoeflig forsinkelse.

    Args:
        url: Absolut URL.
        session: En requests-session med User-Agent sat.

    Returns:
        HTML-teksten, eller None ved vedvarende fejl.
    """
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = session.get(url, timeout=30)
            if resp.status_code == 200 and "awswaf.com" not in resp.text and "AwsWafIntegration" not in resp.text:
                return resp.text
            if resp.status_code == 202 or "awswaf.com" in resp.text or "AwsWafIntegration" in resp.text:
                log.error("Bilbasen svarer med en AWS WAF bot-udfordring (CAPTCHA) paa %s. "
                          "Denne tekniske beskyttelse omgaas IKKE. Brug manuel import i stedet: "
                          "gem soegeresultatet fra din browser og koer 'python scraper.py --import <fil>'.", url)
                return None
            if resp.status_code in (403, 429):
                log.warning("Adgang begraenset (%s) paa %s - respekterer og stopper denne URL",
                            resp.status_code, url)
                return None
            log.warning("HTTP %s paa %s (forsoeg %s)", resp.status_code, url, attempt)
        except Exception as exc:
            log.warning("Fejl ved hentning af %s (forsoeg %s): %s", url, attempt, exc)
        time.sleep(REQUEST_DELAY_SECONDS * attempt)
    log.error("Opgav hentning af %s efter %s forsoeg", url, MAX_RETRIES)
    return None


def page_url(page: int) -> str:
    """Byg URL til en bestemt resultatside i soegningen.

    Args:
        page: Sidetal (1-baseret).

    Returns:
        Fuld URL til den paagaeldende resultatside.
    """
    if page <= 1:
        return SEARCH_URL
    sep = "&" if "?" in SEARCH_URL else "?"
    return f"{SEARCH_URL}{sep}page={page}"


# --------------------------------------------------------------------------- #
# HTML-parsing
# --------------------------------------------------------------------------- #
def _text(node) -> str:
    """Uddrag renset tekst fra en BeautifulSoup-node.

    Args:
        node: BeautifulSoup-node eller None.

    Returns:
        Trimmet tekst, tom streng hvis node er None.
    """
    if node is None:
        return ""
    return re.sub(r"\s+", " ", node.get_text(" ", strip=True)).strip()


def extract_jsonld(soup: "BeautifulSoup") -> List[Dict[str, Any]]:
    """Uddrag JSON-LD strukturerede data fra en side.

    Args:
        soup: Parset HTML-dokument.

    Returns:
        Liste af JSON-LD objekter (dicts).
    """
    results: List[Dict[str, Any]] = []
    for script in soup.find_all("script", attrs={"type": "application/ld+json"}):
        raw = script.string or script.get_text()
        if not raw:
            continue
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if isinstance(data, list):
            results.extend([d for d in data if isinstance(d, dict)])
        elif isinstance(data, dict):
            if "@graph" in data and isinstance(data["@graph"], list):
                results.extend([d for d in data["@graph"] if isinstance(d, dict)])
            else:
                results.append(data)
    return results


def _extract_id_from_href(href: str) -> Optional[str]:
    """Uddrag Bilbasens annonce-id fra en annonce-URL.

    Args:
        href: Relativ eller absolut URL til en annonce.

    Returns:
        Annonce-id som streng, eller None hvis det ikke kan findes.
    """
    if not href:
        return None
    # Typiske moenstre: /brugt/bil/.../<id> eller ?id=<id>
    m = re.search(r"/(\d{5,})(?:[/?#]|$)", href)
    if m:
        return m.group(1)
    m = re.search(r"[?&]id=(\d{5,})", href)
    if m:
        return m.group(1)
    return None


def parse_listing_page(html: str) -> List[Dict[str, Any]]:
    """Parse en Bilbasen-resultatside til en liste af raa bil-dicts.

    Robust overfor mindre aendringer i markup: forsoeger JSON-LD foerst og
    falder tilbage til at laese annoncekort via links.

    Args:
        html: HTML for en resultatside.

    Returns:
        Liste af raa bil-dicts (mindst id og url; oevrige felter hvis muligt).
    """
    if BeautifulSoup is None:
        raise RuntimeError("BeautifulSoup (bs4) er ikke installeret - koer 'pip install -r requirements.txt'")

    soup = BeautifulSoup(html, "html.parser")
    cars: Dict[str, Dict[str, Any]] = {}

    # 1) JSON-LD: ItemList / Product / Car / Vehicle.
    for obj in extract_jsonld(soup):
        typ = obj.get("@type", "")
        types = typ if isinstance(typ, list) else [typ]
        if any(t in ("Car", "Vehicle", "Product") for t in types):
            car = _car_from_jsonld(obj)
            if car and car.get("id"):
                cars[car["id"]] = {**car, "_source": "jsonld"}
        if "itemListElement" in obj:
            for el in obj.get("itemListElement", []):
                item = el.get("item", el) if isinstance(el, dict) else {}
                car = _car_from_jsonld(item)
                if car and car.get("id"):
                    cars.setdefault(car["id"], {**car, "_source": "jsonld"})

    # 1b) Bilbasens annoncekort (article.Listing_listing__*) - den rigeste kilde
    #     naar man kopierer selve resultat-elementet (outerHTML) fra browseren.
    for car in parse_bilbasen_cards(soup):
        cid = car.get("id")
        if not cid:
            continue
        existing = cars.get(cid)
        if existing:
            for k, v in car.items():
                if v and not existing.get(k):
                    existing[k] = v
        else:
            cars[cid] = car

    # 2) Next.js/indlejret JSON (__NEXT_DATA__ og andre inline JSON-blobs).
    #    Dette er den rigeste kilde naar man gemmer en fuldt renderet side fra
    #    browseren, hvor annoncerne ligger som strukturerede objekter.
    for car in _cars_from_embedded_json(soup, html):
        cid = car.get("id")
        if not cid:
            continue
        existing = cars.get(cid)
        if existing:
            for k, v in car.items():
                if v and not existing.get(k):
                    existing[k] = v
        else:
            cars[cid] = {**car, "_source": car.get("_source", "next-data")}

    # 3) Fallback: annoncekort via links til /brugt/bil/.
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if "/brugt/bil/" not in href:
            continue
        ad_id = _extract_id_from_href(href)
        if not ad_id:
            continue
        url = urljoin(BASE_URL, href)
        entry = cars.setdefault(ad_id, {"id": ad_id, "url": url, "_source": "html"})
        entry.setdefault("url", url)
        # Titel fra linktekst hvis vi ikke har maerke/model.
        title = _text(a)
        if title and not entry.get("make"):
            make, model, variant = _split_title(title)
            entry.update({"make": make, "model": model, "variant": variant, "title": title})
        # Forsoeg at laese pris/km fra naerliggende kort-container.
        card = a.find_parent(["article", "div", "li"])
        if card is not None:
            _augment_from_card(entry, card)

    return list(cars.values())


# Noeglenavne der ofte optraeder i Bilbasens indlejrede JSON (heuristisk mapping).
_JSON_FIELD_ALIASES: Dict[str, List[str]] = {
    "make": ["make", "makename", "brand", "brandname", "maerke", "manufacturer"],
    "model": ["model", "modelname", "modelnavn"],
    "variant": ["variant", "name", "title", "headline", "displayname", "description", "subtitle"],
    "price": ["price", "cashprice", "retailprice", "pricevalue", "amount", "pris"],
    "mileage_km": ["mileage", "mileagevalue", "km", "kilometers", "kmstand", "odometer"],
    "model_year": ["year", "modelyear", "regyear", "productionyear", "aargang", "firstregistration", "modeldate"],
    "fuel": ["fueltype", "fuel", "propellant", "braendstof", "drivmiddel"],
    "gearbox_name": ["transmission", "gear", "geartype", "gearbox", "gearkasse"],
    "hp": ["horsepower", "hp", "power", "hk", "effect"],
    "image": ["image", "imageurl", "thumbnail", "thumbnailurl", "picture", "mainimage"],
    "dealer": ["dealer", "dealername", "sellername", "forhandler", "seller"],
    "url": ["url", "uri", "permalink", "link", "detailurl", "href"],
}


def _cars_from_embedded_json(soup: "BeautifulSoup", html: str) -> List[Dict[str, Any]]:
    """Uddrag annoncer fra indlejrede JSON-blobs (fx Next.js __NEXT_DATA__).

    Gaar rekursivt gennem alle inline JSON-scripts og finder objekter, der ligner
    en bilannonce (indeholder et /brugt/bil/-link eller et id samt pris/maerke).
    Robust overfor ukendt schema, saa browser-gemte sider kan importeres.

    Args:
        soup: Parset HTML-dokument.
        html: Den raa HTML (til regex-fald tilbage paa __NEXT_DATA__).

    Returns:
        Liste af raa bil-dicts.
    """
    blobs: List[Any] = []

    # __NEXT_DATA__ og lignende application/json-scripts.
    for script in soup.find_all("script", attrs={"type": "application/json"}):
        raw = script.string or script.get_text()
        if not raw:
            continue
        try:
            blobs.append(json.loads(raw))
        except json.JSONDecodeError:
            continue

    # Fald tilbage til at finde __NEXT_DATA__ direkte i HTML hvis noedvendigt.
    if not blobs:
        m = re.search(r'id="__NEXT_DATA__"[^>]*>(\{.*?\})</script>', html, re.S)
        if m:
            try:
                blobs.append(json.loads(m.group(1)))
            except json.JSONDecodeError:
                pass

    found: Dict[str, Dict[str, Any]] = {}
    for blob in blobs:
        _scan_json_for_cars(blob, found)
    return list(found.values())


def _scan_json_for_cars(node: Any, found: Dict[str, Dict[str, Any]]) -> None:
    """Traverser en JSON-struktur rekursivt og opsaml bil-lignende objekter.

    Args:
        node: Aktuel node (dict, liste eller primitiv).
        found: Akkumulator fra annonce-id til bil-dict (opdateres in-place).
    """
    if isinstance(node, dict):
        car = _car_from_json_object(node)
        if car and car.get("id"):
            found.setdefault(car["id"], car)
        for value in node.values():
            _scan_json_for_cars(value, found)
    elif isinstance(node, list):
        for item in node:
            _scan_json_for_cars(item, found)


def _car_from_json_object(obj: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Byg en raa bil-dict ud fra et generisk JSON-objekt hvis det ligner en annonce.

    Args:
        obj: Et JSON-objekt (dict).

    Returns:
        Raa bil-dict, eller None hvis objektet ikke ligner en annonce.
    """
    lower = {str(k).lower(): v for k, v in obj.items()}

    # Find et annonce-id: enten via et /brugt/bil/-link eller et rent id-felt.
    ad_id = None
    for key in ("url", "uri", "permalink", "link", "detailurl", "href", "canonicalurl"):
        val = lower.get(key)
        if isinstance(val, str) and "/brugt/bil/" in val:
            ad_id = _extract_id_from_href(val)
            if ad_id:
                break
    if not ad_id:
        for key in ("id", "listingid", "adid", "carid"):
            val = lower.get(key)
            if isinstance(val, (str, int)) and re.fullmatch(r"\d{5,}", str(val)):
                ad_id = str(val)
                break
    if not ad_id:
        return None

    # Kraev mindst ét meningsfuldt bilfelt for at undgaa stoej.
    has_price = any(k in lower for k in _JSON_FIELD_ALIASES["price"])
    has_make = any(k in lower for k in _JSON_FIELD_ALIASES["make"])
    if not (has_price or has_make):
        return None

    car: Dict[str, Any] = {"id": ad_id, "_source": "next-data"}
    for field, aliases in _JSON_FIELD_ALIASES.items():
        for alias in aliases:
            if alias in lower and lower[alias] not in (None, ""):
                car[field] = _flatten_scalar(lower[alias])
                break
    if car.get("url"):
        car["url"] = urljoin(BASE_URL, str(car["url"]))
    return car


def _flatten_scalar(value: Any) -> Any:
    """Reducer et evt. indlejret vaerdi-objekt til en skalar (tal/tekst).

    Args:
        value: Vaerdi der kan vaere dict/list/skalar.

    Returns:
        En skalar vaerdi bedst muligt.
    """
    if isinstance(value, dict):
        for key in ("value", "name", "amount", "text", "url", "label"):
            if key in value:
                return value[key]
        return next((v for v in value.values() if isinstance(v, (str, int, float))), None)
    if isinstance(value, list):
        return _flatten_scalar(value[0]) if value else None
    return value


def _split_make_model(make_model: str) -> Tuple[str, str]:
    """Del en 'maerke model'-streng i maerke og model (haandter to-ords maerker).

    Args:
        make_model: Fx 'Renault Grand Scenic IV' eller 'VW Passat'.

    Returns:
        Tuple (maerke, model).
    """
    low = make_model.lower().strip()
    for two in _TWO_WORD_MAKES:
        if low.startswith(two):
            return make_model[:len(two)], make_model[len(two):].strip()
    parts = make_model.strip().split(" ", 1)
    return parts[0], (parts[1] if len(parts) > 1 else "")


def parse_bilbasen_cards(soup: "BeautifulSoup") -> List[Dict[str, Any]]:
    """Parse Bilbasens annoncekort (article.Listing_listing__*) fra kopieret HTML.

    Denne struktur indeholder den direkte annonce-URL med annonce-id, billede,
    pris, egenskaber (1. reg, km, forbrug, gear, braendstof), udstyrsbeskrivelse
    og by - og er derfor den bedste kilde ved manuel import fra browseren.
    Haandterer flere <section>-elementer (flere resultatsider) i samme fil.

    Args:
        soup: Parset HTML (kan vaere et fragment med et eller flere kort).

    Returns:
        Liste af raa bil-dicts.
    """
    cars: List[Dict[str, Any]] = []
    articles = soup.find_all("article", class_=re.compile(r"Listing_listing__"))
    date_re = re.compile(r"^\d{1,2}/\d{4}$")
    km_re = re.compile(r"^[\d.]+\s*km$")

    for art in articles:
        link = art.find("a", href=re.compile(r"/brugt/bil/"))
        href = link["href"] if link else ""
        ad_id = _extract_id_from_href(href)
        if not ad_id:
            continue
        # Bilbasen-resultater er forhandlersalg som udgangspunkt (leasing/privat
        # fanges af normaliseringen via beskrivelsen).
        car: Dict[str, Any] = {"id": ad_id, "url": urljoin(BASE_URL, href),
                               "_source": "html-kort", "sale_type": "Forhandler"}

        mm = art.find(class_=re.compile(r"Listing_makeModel__"))
        if mm:
            parts = [s for s in mm.stripped_strings]
            if parts:
                car["make"], car["model"] = _split_make_model(parts[0])
            if len(parts) > 1:
                car["variant"] = parts[1]

        price = art.find(class_=re.compile(r"Listing_price__"))
        if price:
            car["price"] = _text(price)

        # Foretraek 'Listing_details__' (indeholder ogsaa gear + braendstof),
        # fald tilbage til 'Listing_properties__' (kun dato/km/forbrug).
        props = art.find(class_=re.compile(r"Listing_details__")) \
            or art.find(class_=re.compile(r"Listing_properties__"))
        if props:
            for tok in props.stripped_strings:
                t = tok.strip()
                if date_re.match(t) and "first_registration" not in car:
                    car["first_registration"] = t
                    car["model_year"] = t.split("/")[1]
                elif km_re.match(t) and "mileage_km" not in car:
                    car["mileage_km"] = t
                elif t.endswith("km/l") and "wltp_consumption" not in car:
                    car["wltp_consumption"] = t.replace("km/l", "").strip()
                elif "gear" in t.lower() and "gearbox_name" not in car:
                    car["gearbox_name"] = t
                elif t.lower() in _FUEL_WORDS and "fuel" not in car:
                    car["fuel"] = t

        desc = art.find(class_=re.compile(r"Listing_description__"))
        if desc:
            car["description"] = _text(desc)

        loc = art.find(class_=re.compile(r"Listing_location__"))
        if loc:
            addr = _text(loc)
            car["dealer_address"] = addr
            car["city"] = addr.split(",")[0].strip()

        img = art.find("img")
        if img:
            src = img.get("src") or ""
            if not src and img.get("srcset"):
                src = img["srcset"].split(" ")[0]
            if src:
                car["image"] = src

        cars.append(car)
    return cars


def _looks_like_html(content: str) -> bool:
    """Afgoer om en filtekst er HTML (annoncekort) frem for ren tekst/CSV.

    Args:
        content: Filens indhold.

    Returns:
        True hvis indholdet ser ud som HTML med annoncekort.
    """
    head = content.lstrip()[:2000].lower()
    return head.startswith("<") or "<article" in head or "<section" in head or "listing_listing__" in content[:5000].lower()


def _split_title(title: str) -> Tuple[str, str, str]:
    """Del en annoncetitel op i maerke, model og variant (bedste bud).

    Args:
        title: Annoncetitel, fx 'Toyota RAV4 2.5 Hybrid H3 5d'.

    Returns:
        Tuple (maerke, model, variant).
    """
    parts = title.split()
    if not parts:
        return "", "", ""
    make = parts[0]
    model = parts[1] if len(parts) > 1 else ""
    variant = " ".join(parts[2:]) if len(parts) > 2 else ""
    return make, model, variant


def _augment_from_card(entry: Dict[str, Any], card) -> None:
    """Berig et annoncekort med pris, km og aar laest fra kort-teksten.

    Args:
        entry: Bil-dict der opdateres in-place.
        card: BeautifulSoup-container for annoncekortet.
    """
    text = _text(card)
    if entry.get("price") is None:
        m = re.search(r"([\d\.]{4,})\s*kr", text)
        if m:
            entry["price"] = m.group(1)
    if entry.get("mileage_km") is None:
        m = re.search(r"([\d\.]{2,})\s*km", text)
        if m:
            entry["mileage_km"] = m.group(1)
    if entry.get("model_year") is None:
        m = re.search(r"\b(19|20)\d{2}\b", text)
        if m:
            entry["model_year"] = m.group(0)


def _car_from_jsonld(obj: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Byg en raa bil-dict ud fra et JSON-LD Car/Product-objekt.

    Args:
        obj: JSON-LD objekt.

    Returns:
        Raa bil-dict, eller None hvis der ikke er brugbare data.
    """
    if not isinstance(obj, dict):
        return None
    url = obj.get("url") or obj.get("@id") or ""
    ad_id = _extract_id_from_href(url) or (str(obj.get("sku")) if obj.get("sku") else None)
    if not ad_id:
        return None

    offers = obj.get("offers") or {}
    if isinstance(offers, list):
        offers = offers[0] if offers else {}
    price = offers.get("price") if isinstance(offers, dict) else None
    seller = offers.get("seller", {}) if isinstance(offers, dict) else {}
    dealer = seller.get("name") if isinstance(seller, dict) else None

    brand = obj.get("brand")
    if isinstance(brand, dict):
        brand = brand.get("name")

    car = {
        "id": str(ad_id),
        "url": urljoin(BASE_URL, url) if url else "",
        "make": brand or "",
        "model": obj.get("model", ""),
        "variant": obj.get("name", ""),
        "model_year": obj.get("productionDate") or obj.get("modelDate") or obj.get("vehicleModelDate"),
        "mileage_km": _jsonld_mileage(obj),
        "price": price,
        "fuel": _jsonld_fuel(obj),
        "hp": None,
        "description": obj.get("description", ""),
        "dealer": dealer or "",
        "image": _jsonld_image(obj),
        "gearbox_name": obj.get("vehicleTransmission", ""),
    }
    return car


def _jsonld_mileage(obj: Dict[str, Any]) -> Optional[str]:
    """Uddrag kilometerstand fra et JSON-LD objekt.

    Args:
        obj: JSON-LD objekt.

    Returns:
        Kilometerstand som streng eller None.
    """
    m = obj.get("mileageFromOdometer")
    if isinstance(m, dict):
        return m.get("value")
    return m


def _jsonld_fuel(obj: Dict[str, Any]) -> str:
    """Uddrag drivmiddel fra et JSON-LD objekt.

    Args:
        obj: JSON-LD objekt.

    Returns:
        Drivmiddel som streng.
    """
    f = obj.get("fuelType")
    if isinstance(f, dict):
        return f.get("name", "")
    return f or ""


def _jsonld_image(obj: Dict[str, Any]) -> str:
    """Uddrag primaert billede-URL fra et JSON-LD objekt.

    Args:
        obj: JSON-LD objekt.

    Returns:
        Billed-URL som streng.
    """
    img = obj.get("image")
    if isinstance(img, list):
        return img[0] if img else ""
    if isinstance(img, dict):
        return img.get("url", "")
    return img or ""


def parse_detail_page(html: str, base: Dict[str, Any]) -> Dict[str, Any]:
    """Parse en detaljeside og berig en eksisterende bil-dict.

    Uddrager tekniske specifikationer (vaegte, moment, udstyr) fra
    specifikationstabeller samt strukturerede data.

    Args:
        html: HTML for detaljesiden.
        base: Eksisterende raa bil-dict fra resultatsiden.

    Returns:
        Beriget bil-dict.
    """
    if BeautifulSoup is None:
        raise RuntimeError("BeautifulSoup (bs4) er ikke installeret")

    soup = BeautifulSoup(html, "html.parser")
    car = dict(base)

    # Strukturerede data.
    for obj in extract_jsonld(soup):
        extra = _car_from_jsonld(obj)
        if extra:
            for k, v in extra.items():
                if v and not car.get(k):
                    car[k] = v

    # Specifikationstabeller: label/value-par.
    specs = _extract_spec_pairs(soup)
    _map_specs(car, specs)

    # Udstyrsliste.
    equipment: List[str] = list(car.get("equipment", []) or [])
    for ul in soup.find_all(["ul", "div"], attrs={"class": re.compile(r"equipment|udstyr|feature", re.I)}):
        for li in ul.find_all("li"):
            t = _text(li)
            if t:
                equipment.append(t)
    if equipment:
        car["equipment"] = sorted(set(equipment))

    # Beskrivelse.
    desc_node = soup.find(attrs={"class": re.compile(r"description|beskrivelse", re.I)})
    if desc_node is not None:
        desc = _text(desc_node)
        if desc:
            car["description"] = desc

    return car


def _extract_spec_pairs(soup: "BeautifulSoup") -> Dict[str, str]:
    """Uddrag label/value-par fra specifikationstabeller og definitionslister.

    Args:
        soup: Parset detaljeside.

    Returns:
        Dict fra normaliseret label til vaerditekst.
    """
    pairs: Dict[str, str] = {}

    for row in soup.find_all("tr"):
        cells = row.find_all(["td", "th"])
        if len(cells) >= 2:
            key = _text(cells[0]).lower()
            val = _text(cells[1])
            if key and val:
                pairs[key] = val

    for dl in soup.find_all("dl"):
        dts = dl.find_all("dt")
        dds = dl.find_all("dd")
        for dt, dd in zip(dts, dds):
            key = _text(dt).lower()
            val = _text(dd)
            if key and val:
                pairs[key] = val

    return pairs


SPEC_MAP: List[Tuple[str, str]] = [
    (r"traekvaegt|max.*traek|anhaenger.*brems|paahaeng.*brems", "tow_capacity_kg"),
    (r"egenvaegt|koereklar|tjenestevaegt", "kerb_weight_kg"),
    (r"totalvaegt|tilladt totalvaegt", "total_weight_kg"),
    (r"vogntogsvaegt|vogntog", "train_weight_kg"),
    (r"kugletryk|paahaengets", "nose_weight_kg"),
    (r"lasteevne|last\b", "payload_kg"),
    (r"hestekr|effekt|\bhk\b|\bkw\b", "hp"),
    (r"moment|nm\b|draejningsmoment", "torque_nm"),
    (r"forbrug|km/l|wltp", "wltp_consumption"),
    (r"co2|co₂|udledning", "co2"),
    (r"gr.?nafgift|periodisk afgift|ejerafgift|halvaarlig", "periodic_tax"),
    (r"traekhjul|traektype|firehjul", "drivetrain"),
    (r"gear|transmission", "gearbox_name"),
    (r"antal gear", "gears"),
    (r"karrosseri|biltype", "body_type"),
    (r"bagagerum|kuffert", "trunk_liters"),
    (r"1\. reg|foerste reg|reg\.? dato", "first_registration"),
    (r"modelaar|aargang|aar\b", "model_year"),
    (r"km-?stand|kilometer", "mileage_km"),
    (r"motorst|slagvolumen|liter", "engine_size_l"),
]


def _map_specs(car: Dict[str, Any], specs: Dict[str, str]) -> None:
    """Afbild raa specifikationstekst til strukturerede bilfelter (in-place).

    Args:
        car: Bil-dict der opdateres.
        specs: Label/value-par fra detaljesiden.
    """
    for label, value in specs.items():
        for pattern, field in SPEC_MAP:
            if re.search(pattern, label):
                if not car.get(field):
                    car[field] = value
                break


# --------------------------------------------------------------------------- #
# Cache af detaljesider
# --------------------------------------------------------------------------- #
def cache_path(ad_id: str) -> Path:
    """Beregn cache-stien for en detaljeside.

    Args:
        ad_id: Annonce-id.

    Returns:
        Sti til cache-filen.
    """
    return CACHE_DIR / f"detail_{ad_id}.html"


def get_detail_html(ad_id: str, url: str, session, use_network: bool) -> Optional[str]:
    """Hent detaljeside-HTML fra cache eller netvaerk efter behov.

    Args:
        ad_id: Annonce-id.
        url: Detaljeside-URL.
        session: requests-session (kan vaere None ved offline).
        use_network: Om der maa hentes fra netvaerket.

    Returns:
        HTML-tekst, eller None.
    """
    path = cache_path(ad_id)
    if path.exists():
        age_hours = (time.time() - path.stat().st_mtime) / 3600
        if age_hours < DETAIL_CACHE_MAX_AGE_HOURS:
            return path.read_text(encoding="utf-8")
    if not use_network or session is None:
        return path.read_text(encoding="utf-8") if path.exists() else None
    html = fetch(url, session)
    if html:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        path.write_text(html, encoding="utf-8")
        time.sleep(REQUEST_DELAY_SECONDS)
    return html


# --------------------------------------------------------------------------- #
# Sammenfletning og historik
# --------------------------------------------------------------------------- #
def merge_and_track(scraped: List[Dict[str, Any]],
                    existing: List[Dict[str, Any]],
                    price_history: Dict[str, Any],
                    keep_disappeared: bool = True) -> Tuple[List[Dict[str, Any]], Dict[str, Any], Dict[str, int]]:
    """Sammenflet nyligt scrapede biler med eksisterende data og spor aendringer.

    Registrerer foerste/seneste observation, forsvundne annoncer samt aendringer
    i pris og kilometerstand. Prishistorik og foerste-observation bevares altid pr.
    annonce-id, ogsaa i erstat-tilstand.

    Args:
        scraped: Nyligt scrapede (raa) biler fra denne koersel.
        existing: Tidligere gemte biler (fra cars.json).
        price_history: Eksisterende prishistorik pr. annonce-id.
        keep_disappeared: Hvis True beholdes tidligere biler, der ikke laengere ses,
            markeret som 'disappeared'. Hvis False (erstat/nulstil) droppes de helt,
            saa datasaettet praecis afspejler den nyeste import.

    Returns:
        Tuple (flettede_biler, opdateret_prishistorik, statistik).
    """
    ts = now_iso()
    by_id_existing = {c["id"]: c for c in existing}
    scraped_ids = {c["id"] for c in scraped}
    stats = {"total": 0, "new": 0, "price_changes": 0, "disappeared": 0, "returned": 0}

    merged: Dict[str, Dict[str, Any]] = {}

    for raw in scraped:
        cid = raw["id"]
        prev = by_id_existing.get(cid)
        car = dict(raw)
        hist = price_history.get(cid, {"prices": [], "mileage": []})

        new_price = normalizer._to_int(raw.get("price"))
        new_km = normalizer._to_int(raw.get("mileage_km"))

        if prev is None:
            car["first_seen"] = ts
            stats["new"] += 1
        else:
            car["first_seen"] = prev.get("first_seen", ts)
            if prev.get("status") == "disappeared":
                stats["returned"] += 1
            prev_price = normalizer._to_int(prev.get("price"))
            if new_price is not None and prev_price is not None and new_price != prev_price:
                stats["price_changes"] += 1

        # Prishistorik.
        prices = hist.get("prices", [])
        if new_price is not None and (not prices or prices[-1].get("price") != new_price):
            prices.append({"date": ts, "price": new_price})
        hist["prices"] = prices

        mileage = hist.get("mileage", [])
        if new_km is not None and (not mileage or mileage[-1].get("km") != new_km):
            mileage.append({"date": ts, "km": new_km})
        hist["mileage"] = mileage

        price_history[cid] = hist
        car["price_history"] = prices
        car["mileage_history"] = mileage
        car["last_seen"] = ts
        car["status"] = "active"
        merged[cid] = car

    # Forsvundne annoncer.
    for cid, prev in by_id_existing.items():
        if cid not in scraped_ids:
            if prev.get("status") != "disappeared":
                stats["disappeared"] += 1
            if keep_disappeared:
                prev = dict(prev)
                prev["status"] = "disappeared"
                prev.setdefault("last_seen", prev.get("last_seen", ts))
                merged[cid] = prev
            # I erstat-tilstand droppes forsvundne biler helt (kun deres
            # prishistorik bevares i price_history).

    stats["total"] = len([c for c in merged.values() if c.get("status") == "active"])
    return list(merged.values()), price_history, stats


# --------------------------------------------------------------------------- #
# Manuel import
# --------------------------------------------------------------------------- #
def import_file(path: Path) -> List[Dict[str, Any]]:
    """Importer biler manuelt fra HTML, JSON, CSV eller semikolonsepareret tekst.

    Bruges hvis scraping ikke er tilladt eller bliver blokeret.

    Args:
        path: Sti til importfilen.

    Returns:
        Liste af raa bil-dicts.
    """
    suffix = path.suffix.lower()
    content = path.read_text(encoding="utf-8", errors="replace")

    # HTML kan ogsaa ligge i en .txt-fil (kopieret element fra browseren).
    if suffix in (".html", ".htm") or _looks_like_html(content):
        return parse_listing_page(content)
    if suffix == ".json":
        data = json.loads(content)
        if isinstance(data, dict) and "cars" in data:
            return data["cars"]
        return data if isinstance(data, list) else [data]
    if suffix in (".csv", ".txt", ".tsv"):
        if _looks_like_copied_text(content):
            return parse_copied_text(content)
        return _import_delimited(content)
    raise ValueError(f"Ukendt filtype: {suffix}")


import hashlib

# Kendte to-ords bilmaerker, saa maerke/model deles korrekt i kopieret tekst.
_TWO_WORD_MAKES = {"land rover", "alfa romeo", "aston martin", "great wall", "mercedes benz"}
_FUEL_WORDS = {"benzin", "diesel", "el", "elbil", "hybrid", "plug-in hybrid",
               "mild hybrid", "hybrid (benzin)", "hybrid (diesel)"}


def parse_copied_text(text: str) -> List[Dict[str, Any]]:
    """Parse tekst kopieret direkte fra Bilbasens soegeresultater.

    Kopieret tekst er linjebaseret pr. annonce: fuld titel, maerke+model, variant,
    pris, 1. registrering (M/AAAA), kilometer, forbrug, gear, braendstof og sted.
    Kopieret tekst indeholder hverken annonce-id eller URL, saa der dannes et
    stabilt syntetisk id (hash), saa gentagne imports ikke laver dubletter og
    prisaendringer stadig kan spores.

    Args:
        text: Raatekst kopieret fra en eller flere resultatsider.

    Returns:
        Liste af raa bil-dicts.
    """
    lines = [ln.strip() for ln in text.splitlines()]
    price_re = re.compile(r"^[\d.]+\s*kr\.?$")
    date_re = re.compile(r"^(\d{1,2})/(\d{4})$")
    km_re = re.compile(r"^[\d.]+\s*km$")
    cons_re = re.compile(r"km/l$")
    loc_re = re.compile(r"^[^,]+,\s*[^,]+$")

    cars: List[Dict[str, Any]] = []
    for i, line in enumerate(lines):
        if not price_re.match(line):
            continue
        if i < 2:
            continue
        variant = lines[i - 1]
        make_model = lines[i - 2]
        if not make_model or not variant:
            continue

        # Del maerke og model (haandter kendte to-ords maerker).
        low = make_model.lower()
        make, model = "", ""
        for two in _TWO_WORD_MAKES:
            if low.startswith(two):
                make = make_model[:len(two)]
                model = make_model[len(two):].strip()
                break
        if not make:
            parts = make_model.split(" ", 1)
            make = parts[0]
            model = parts[1] if len(parts) > 1 else ""

        # Retail-soegning med forhandlerlogo => forhandlersalg som standard.
        car: Dict[str, Any] = {
            "make": make, "model": model, "variant": variant,
            "price": line, "_source": "kopieret-tekst", "url": "",
            "sale_type": "Forhandler",
        }

        # Klassificér de efterfoelgende linjer i et vindue frem til naeste annonce.
        for j in range(i + 1, min(i + 9, len(lines))):
            val = lines[j]
            if not val or price_re.match(val):
                break
            md = date_re.match(val)
            if md and "first_registration" not in car:
                car["first_registration"] = val
                car["model_year"] = md.group(2)
            elif km_re.match(val) and "mileage_km" not in car:
                car["mileage_km"] = val
            elif cons_re.search(val) and "wltp_consumption" not in car:
                car["wltp_consumption"] = val.replace("km/l", "").strip()
            elif "gear" in val.lower() and "gearbox_name" not in car:
                car["gearbox_name"] = val
            elif val.lower() in _FUEL_WORDS and "fuel" not in car:
                car["fuel"] = val
            elif loc_re.match(val) and "dealer_address" not in car and "gear" not in val.lower():
                car["dealer_address"] = val
            elif val == "Ny annonce":
                car["_new_badge"] = True

        # By udledes af sted-linjen ("Skive, Vestjylland" -> "Skive").
        if car.get("dealer_address"):
            car["city"] = car["dealer_address"].split(",")[0].strip()

        # "Find annoncen"-link paa Bilbasen (kopieret tekst har ingen direkte URL).
        car["url"] = _bilbasen_model_url(make, model)

        # Stabilt syntetisk id ud fra bilens identitet (ikke pris/km).
        ident = f"{make_model}|{variant}|{car.get('first_registration','')}|{car.get('dealer_address','')}"
        car["id"] = "bb-" + hashlib.md5(ident.encode("utf-8")).hexdigest()[:12]
        cars.append(car)

    return cars


def _slugify(value: str) -> str:
    """Lav en URL-slug (aa/oe/ae -> a/o/a, mellemrum -> bindestreg).

    Args:
        value: Tekst der skal blive til en slug.

    Returns:
        Slug egnet til en Bilbasen-sti.
    """
    v = value.lower()
    for src, dst in (("æ", "ae"), ("ø", "oe"), ("å", "aa"), ("é", "e"), ("ü", "u"), ("ö", "o"), ("ä", "a")):
        v = v.replace(src, dst)
    v = re.sub(r"[^a-z0-9]+", "-", v).strip("-")
    return v


def _bilbasen_model_url(make: str, model: str) -> str:
    """Byg et Bilbasen model-landingslink til at genfinde en annonce.

    Kopieret tekst indeholder ikke den direkte annonce-URL, saa der linkes til
    maerkets/modellens brugtbil-side, hvor bilen kan findes paa pris og km.
    Modellens generationstal (fx 'IV') fjernes fra slug'en.

    Args:
        make: Bilmaerke som i annoncen (fx 'VW').
        model: Model (fx 'Megane IV').

    Returns:
        Absolut Bilbasen-URL.
    """
    make_slug = _slugify(make)
    # Fjern efterstillede romertal-generationer fra modelnavnet.
    model_clean = re.sub(r"\b(i{1,3}|iv|v|vi{0,3}|ix|x)\b", " ", model, flags=re.I)
    model_slug = _slugify(model_clean)
    base = f"{BASE_URL}/brugt/bil/{make_slug}"
    return f"{base}/{model_slug}" if model_slug else base


def _looks_like_copied_text(content: str) -> bool:
    """Afgoer om en tekstfil er kopieret Bilbasen-tekst frem for afgraenset CSV.

    Args:
        content: Filens indhold.

    Returns:
        True hvis indholdet ligner kopieret soegeresultat-tekst.
    """
    price_lines = re.findall(r"(?m)^[\d.]+\s*kr\.?$", content)
    first = next((ln for ln in content.splitlines() if ln.strip()), "")
    has_header = (";" in first or ",") and re.search(r"\b(id|make|model|price)\b", first, re.I)
    return len(price_lines) >= 2 and not (";" in first and has_header)


def _import_delimited(content: str) -> List[Dict[str, Any]]:
    """Importer biler fra afgraenset tekst (komma, semikolon eller tab).

    Args:
        content: Filens tekstindhold med en overskriftsraekke.

    Returns:
        Liste af raa bil-dicts.
    """
    sample = content[:2048]
    delimiter = ";" if sample.count(";") >= sample.count(",") else ","
    if "\t" in sample and sample.count("\t") > sample.count(delimiter):
        delimiter = "\t"
    reader = csv.DictReader(io.StringIO(content), delimiter=delimiter)
    cars: List[Dict[str, Any]] = []
    for row in reader:
        clean = {k.strip().lower(): (v.strip() if isinstance(v, str) else v)
                 for k, v in row.items() if k}
        if not clean.get("id"):
            # Udled et id af URL eller lav et syntetisk.
            clean["id"] = _extract_id_from_href(clean.get("url", "")) or f"manual-{len(cars)+1}"
        clean["_source"] = "manuel-import"
        cars.append(clean)
    return cars


# --------------------------------------------------------------------------- #
# Pipeline
# --------------------------------------------------------------------------- #
def process_and_save(scraped: List[Dict[str, Any]], scrape_errors: List[str],
                     source_label: str, replace: bool = False) -> Dict[str, Any]:
    """Normaliser, scor, flet og gem scrapede biler til data-mappen.

    Args:
        scraped: Raa biler fra scrape eller import.
        scrape_errors: Liste af fejlbeskeder undervejs.
        source_label: Beskrivelse af datakilden (til status).
        replace: Hvis True erstattes hele bil-listen af denne import (tidligere
            biler, der ikke er med, fjernes) og INGEN data beholdes paa tvaers af
            koersler - heller ikke prishistorik. Datasaettet afspejler kun denne import.

    Returns:
        Status-dict med statistik.
    """
    # I erstat-tilstand nulstilles alt: hverken tidligere biler eller prishistorik
    # beholdes. Favoritter bevares separat i browserens localStorage (snapshot).
    existing = [] if replace else load_json(DATA_DIR / "cars.json", [])
    price_history = {} if replace else load_json(DATA_DIR / "price_history.json", {})

    merged, price_history, stats = merge_and_track(
        scraped, existing, price_history, keep_disappeared=not replace)

    # Normaliser og scor alle biler (aktive + forsvundne bevares).
    gb = normalizer.load_gearbox_knowledge()
    tr = normalizer.load_trailer_knowledge()
    settings = scoring.load_settings()

    ms = normalizer.load_model_specs()
    normalized = [normalizer.normalize_car(c, gb, tr, ms) for c in merged]
    scored = scoring.score_all(normalized, settings)

    save_json(DATA_DIR / "cars.json", scored)
    save_json(DATA_DIR / "price_history.json", price_history)

    active = [c for c in scored if c.get("status") == "active"]
    rejected = [c for c in active if c.get("rejected")]
    prices = [c["price"] for c in active if not c.get("rejected") and c.get("price")]

    status = {
        "last_run": now_iso(),
        "source": source_label,
        "fetched": len(scraped),
        "active": len(active),
        "new": stats["new"],
        "price_changes": stats["price_changes"],
        "disappeared": stats["disappeared"],
        "rejected": len(rejected),
        "median_price": int(statistics_median(prices)) if prices else None,
        "errors": scrape_errors,
        "success": len(scrape_errors) == 0,
    }
    save_json(DATA_DIR / "scrape_status.json", status)

    log.info("Faerdig: %s aktive, %s nye, %s prisaendringer, %s afviste, %s forsvundne",
             status["active"], status["new"], status["price_changes"],
             status["rejected"], status["disappeared"])
    return status


def statistics_median(values: List[float]) -> float:
    """Beregn median af en liste (tom liste giver 0).

    Args:
        values: Talvaerdier.

    Returns:
        Medianen, eller 0 hvis listen er tom.
    """
    import statistics as _st
    return _st.median(values) if values else 0


def run_live_scrape() -> Dict[str, Any]:
    """Koer en fuld live scrape mod Bilbasen over alle resultatsider.

    Returns:
        Status-dict fra process_and_save.
    """
    if requests is None:
        raise RuntimeError("requests er ikke installeret - koer 'pip install -r requirements.txt'")

    errors: List[str] = []
    if not check_robots(SEARCH_URL):
        msg = ("robots.txt tillader ikke denne soegning. Brug manuel import i stedet: "
               "python scraper.py --import <fil>")
        log.error(msg)
        return process_and_save([], [msg], "live (blokeret af robots.txt)")

    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT, "Accept-Language": "da-DK,da;q=0.9"})

    all_raw: Dict[str, Dict[str, Any]] = {}
    for page in range(1, MAX_PAGES + 1):
        url = page_url(page)
        log.info("Henter resultatside %s: %s", page, url)
        html = fetch(url, session)
        if html is None:
            errors.append(f"Kunne ikke hente side {page}")
            break
        page_cars = parse_listing_page(html)
        if not page_cars:
            log.info("Ingen biler paa side %s - antager sidste side", page)
            break
        new_on_page = 0
        for c in page_cars:
            if c["id"] not in all_raw:
                all_raw[c["id"]] = c
                new_on_page += 1
        log.info("Side %s: %s annoncer (%s nye)", page, len(page_cars), new_on_page)
        if new_on_page == 0:
            log.info("Ingen nye annonce-id'er paa side %s - stopper paginering", page)
            break
        time.sleep(REQUEST_DELAY_SECONDS)

    # Hent detaljesider (cache-styret).
    for cid, car in all_raw.items():
        try:
            detail_html = get_detail_html(cid, car.get("url", ""), session, use_network=True)
            if detail_html:
                all_raw[cid] = parse_detail_page(detail_html, car)
        except Exception as exc:
            errors.append(f"Detaljeside {cid}: {exc}")
            log.warning("Fejl paa detaljeside %s: %s", cid, exc)

    return process_and_save(list(all_raw.values()), errors, "live scrape")


def run_fixtures() -> Dict[str, Any]:
    """Koer scraperen mod gemte HTML-fixtures uden netvaerksadgang.

    Laeser alle listing_*.html og detail_*.html i tests/fixtures.

    Returns:
        Status-dict fra process_and_save.
    """
    errors: List[str] = []
    listing_files = sorted(FIXTURE_DIR.glob("listing_*.html"))
    all_raw: Dict[str, Dict[str, Any]] = {}
    for lf in listing_files:
        try:
            cars = parse_listing_page(lf.read_text(encoding="utf-8"))
            for c in cars:
                all_raw.setdefault(c["id"], c)
            log.info("Fixture %s: %s biler", lf.name, len(cars))
        except Exception as exc:
            errors.append(f"{lf.name}: {exc}")

    for cid, car in all_raw.items():
        detail = FIXTURE_DIR / f"detail_{cid}.html"
        if detail.exists():
            try:
                all_raw[cid] = parse_detail_page(detail.read_text(encoding="utf-8"), car)
            except Exception as exc:
                errors.append(f"detail_{cid}: {exc}")

    return process_and_save(list(all_raw.values()), errors, "fixtures (offline test)")


def run_import(path: Path, replace: bool = True) -> Dict[str, Any]:
    """Koer manuel import af en fil og gem resultatet.

    Standard er erstat-tilstand: datasaettet nulstilles og afspejler kun denne fil
    (ingen data beholdes paa tvaers af koersler). Brug --append for at flette i stedet.

    Args:
        path: Sti til importfilen.
        replace: Hvis True (standard) erstattes hele datasaettet af denne import.

    Returns:
        Status-dict.
    """
    cars = import_file(path)
    label = "manuel import (erstat)" if replace else "manuel import (flet)"
    log.info("Importerede %s biler fra %s (%s)", len(cars), path,
             "erstatter alt" if replace else "fletter")
    return process_and_save(cars, [], f"{label}: {path.name}", replace=replace)


def run_score_only() -> Dict[str, Any]:
    """Genberegn normalisering og score fra eksisterende cars.json uden scrape.

    Nyttig efter aendring af scoringsregler eller videns-filer.

    Returns:
        Status-dict.
    """
    existing = load_json(DATA_DIR / "cars.json", [])
    gb = normalizer.load_gearbox_knowledge()
    tr = normalizer.load_trailer_knowledge()
    settings = scoring.load_settings()
    ms = normalizer.load_model_specs()
    normalized = [normalizer.normalize_car(c, gb, tr, ms) for c in existing]
    scored = scoring.score_all(normalized, settings)
    save_json(DATA_DIR / "cars.json", scored)
    log.info("Genberegnet score for %s biler", len(scored))
    status = load_json(DATA_DIR / "scrape_status.json", {})
    status["last_rescored"] = now_iso()
    save_json(DATA_DIR / "scrape_status.json", status)
    return status


def main(argv: Optional[List[str]] = None) -> int:
    """Indgangspunkt for kommandolinjen.

    Args:
        argv: Argumentliste (default sys.argv).

    Returns:
        Exit-kode (0 ved succes).
    """
    parser = argparse.ArgumentParser(description="Scraper til brugte biler paa Bilbasen")
    parser.add_argument("--fixtures", action="store_true", help="Koer mod gemte HTML-testdata")
    parser.add_argument("--import", dest="import_path", metavar="FIL",
                        help="Manuel import af HTML/JSON/CSV/semikolon-fil (nulstiller datasaettet)")
    parser.add_argument("--append", action="store_true",
                        help="Flet importen med eksisterende data i stedet for at nulstille")
    parser.add_argument("--score-only", action="store_true",
                        help="Genberegn score fra eksisterende cars.json")
    args = parser.parse_args(argv)

    if args.import_path:
        run_import(Path(args.import_path), replace=not args.append)
    elif args.fixtures:
        run_fixtures()
    elif args.score_only:
        run_score_only()
    else:
        run_live_scrape()
    return 0


if __name__ == "__main__":
    sys.exit(main())
