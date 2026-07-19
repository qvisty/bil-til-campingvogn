"""Geokodning af byer fra cars.json til koordinater, gemt i data/city_coords.json.

Bruges til kort-siden (map.html), hvor hver bil vises som en pin. Scriptet slaar
kun ukendte byer op (cache-first) og respekterer OpenStreetMap Nominatims
belastningsgraense paa hoejst ét kald i sekundet.

Brug:
    python geocode.py            # geokod nye byer fra data/cars.json
    python geocode.py --force    # geokod alle byer igen
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path
from typing import Dict, List, Optional

try:
    import requests
except ImportError:
    requests = None

ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "data"
NOMINATIM = "https://nominatim.openstreetmap.org/search"
USER_AGENT = "BilTilCampingvognBot/1.0 (personligt beslutningsvaerktoej)"


def base_city(city: str) -> str:
    """Fjern postdistrikt-endelser fra et bynavn, saa geokodning bliver praecis.

    Fx 'Aalborg SV' -> 'Aalborg', 'Viby J' -> 'Viby', 'Stoholm Jyll' -> 'Stoholm'.

    Args:
        city: Bynavn som det staar i annoncen.

    Returns:
        Basisbynavn uden distriktsendelse.
    """
    return re.sub(r"\s+(Jyll|[A-ZÆØÅ]{1,3})$", "", city).strip()


def load_json(path: Path, default):
    """Indlaes JSON med fallback.

    Args:
        path: Filsti.
        default: Vaerdi hvis filen mangler/er ugyldig.

    Returns:
        Indhold eller default.
    """
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return default


# Omtrentlige centre for Bilbasens landsdele - bruges til at vaelge den rigtige
# kandidat, naar et bynavn findes flere steder (fx Viby J vs. Viby Sjaelland).
REGION_CENTROIDS: Dict[str, List[float]] = {
    "nordjylland": [57.0, 9.9],
    "østjylland": [56.2, 10.0],
    "vestjylland": [56.3, 8.7],
    "syd- og sønderjylland": [55.3, 9.1],
    "sønderjylland": [55.1, 9.2],
    "fyn": [55.4, 10.4],
    "københavn": [55.68, 12.5],
    "nordsjælland": [55.95, 12.3],
    "syd- og vestsjælland": [55.4, 11.6],
    "vestsjælland": [55.5, 11.4],
    "lolland-falster": [54.8, 11.6],
    "bornholm": [55.13, 14.9],
}


def geocode_city(city: str, region: str, session) -> Optional[List[float]]:
    """Slaa koordinater op for en by via Nominatim og vaelg den bedste kandidat.

    Henter op til fem kandidater og vaelger den, der ligger naermest landsdelens
    centrum, saa fx 'Viby J' (Østjylland) ikke forveksles med Viby paa Sjaelland.

    Args:
        city: Bynavn (basisnavn).
        region: Landsdel fra annonceadressen (kan vaere tom).
        session: requests-session med User-Agent.

    Returns:
        [lat, lon] eller None hvis ikke fundet.
    """
    try:
        resp = session.get(NOMINATIM, params={
            "q": f"{city}, Danmark", "format": "json", "limit": 5, "countrycodes": "dk",
        }, timeout=30)
        if resp.status_code != 200:
            return None
        data = resp.json()
        if not data:
            return None
    except Exception:
        return None

    candidates = [[round(float(d["lat"]), 5), round(float(d["lon"]), 5)] for d in data]
    centroid = REGION_CENTROIDS.get((region or "").strip().lower())
    if centroid and len(candidates) > 1:
        candidates.sort(key=lambda ll: (ll[0] - centroid[0]) ** 2 + (ll[1] - centroid[1]) ** 2)
    return candidates[0]


def main(argv: Optional[List[str]] = None) -> int:
    """Indgangspunkt: geokod byer fra cars.json og gem city_coords.json.

    Args:
        argv: Argumentliste.

    Returns:
        Exit-kode.
    """
    parser = argparse.ArgumentParser(description="Geokod byer til kort-pins")
    parser.add_argument("--force", action="store_true", help="Geokod alle byer igen")
    args = parser.parse_args(argv)

    if requests is None:
        print("requests er ikke installeret - koer 'pip install -r requirements.txt'")
        return 1

    cars = load_json(DATA_DIR / "cars.json", [])
    coords: Dict[str, List[float]] = {} if args.force else load_json(DATA_DIR / "city_coords.json", {})

    # By -> region (landsdel) fra annonceadressen, til disambiguering.
    region_of: Dict[str, str] = {}
    for c in cars:
        city = c.get("city")
        addr = c.get("dealer_address", "") or ""
        if city and "," in addr:
            region_of.setdefault(city, addr.split(",", 1)[1].strip())

    cities = sorted({c.get("city") for c in cars if c.get("city")})
    todo = [c for c in cities if c not in coords]
    print(f"{len(cities)} byer i data, {len(todo)} skal geokodes.")

    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT, "Accept-Language": "da"})

    for i, city in enumerate(todo, 1):
        latlon = geocode_city(base_city(city), region_of.get(city, ""), session)
        if latlon:
            coords[city] = latlon
            print(f"  [{i}/{len(todo)}] {city} -> {latlon}")
        else:
            print(f"  [{i}/{len(todo)}] {city} -> IKKE FUNDET")
        time.sleep(1.1)  # respekter Nominatims graense (maks 1/sek)

    DATA_DIR.mkdir(exist_ok=True)
    (DATA_DIR / "city_coords.json").write_text(
        json.dumps(coords, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    print(f"Gemte {len(coords)} bykoordinater i data/city_coords.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
