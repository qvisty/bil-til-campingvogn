"""Tests for parsing, paginering, dubletter, historik og manuel import.

Testene kraever ikke live adgang til Bilbasen - de arbejder udelukkende med
gemte HTML-fixtures og syntetiske data.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import scraper  # noqa: E402
import normalizer  # noqa: E402

FIXTURES = ROOT / "tests" / "fixtures"


def _read(name: str) -> str:
    """Laes en fixture-fil som tekst.

    Args:
        name: Filnavn i fixtures-mappen.

    Returns:
        Filens indhold.
    """
    return (FIXTURES / name).read_text(encoding="utf-8")


def test_parse_listing_finds_all_cars():
    """Resultatside 1 skal give praecis tre biler med korrekte id'er."""
    cars = scraper.parse_listing_page(_read("listing_page1.html"))
    ids = {c["id"] for c in cars}
    assert ids == {"10000001", "10000002", "10000003"}


def test_parse_listing_extracts_price_and_make():
    """JSON-LD skal give maerke og pris for foerste bil."""
    cars = {c["id"]: c for c in scraper.parse_listing_page(_read("listing_page1.html"))}
    rav4 = cars["10000001"]
    assert rav4["make"] == "Toyota"
    assert normalizer._to_int(rav4["price"]) == 249000


def test_pagination_merges_two_pages_without_duplicates():
    """To resultatsider skal samlet give seks unikke biler (ingen dubletter)."""
    all_raw = {}
    for page in ("listing_page1.html", "listing_page2.html"):
        for c in scraper.parse_listing_page(_read(page)):
            all_raw.setdefault(c["id"], c)
    # Simuler at side 1 hentes igen (dublet-scenarie).
    for c in scraper.parse_listing_page(_read("listing_page1.html")):
        all_raw.setdefault(c["id"], c)
    assert len(all_raw) == 6


def test_detail_page_enriches_weights():
    """Detaljesiden skal tilfoeje traekvaegt og egenvaegt til bilen."""
    base = {"id": "10000001", "url": "x", "make": "Toyota", "model": "RAV4"}
    car = scraper.parse_detail_page(_read("detail_10000001.html"), base)
    assert normalizer._to_int(car["tow_capacity_kg"]) == 1650
    assert normalizer._to_int(car["kerb_weight_kg"]) == 1710


def test_id_extraction_from_href():
    """Annonce-id skal kunne udledes af en typisk annonce-URL."""
    assert scraper._extract_id_from_href("/brugt/bil/toyota/rav4/10000001") == "10000001"
    assert scraper._extract_id_from_href("https://x/bil?id=987654") == "987654"


def test_merge_tracks_new_and_price_changes():
    """Fletning skal registrere nye biler og prisaendringer korrekt."""
    existing = [{"id": "A", "price": 200000, "first_seen": "2026-01-01T00:00:00+00:00",
                 "status": "active"}]
    price_history = {"A": {"prices": [{"date": "2026-01-01T00:00:00+00:00", "price": 200000}], "mileage": []}}
    scraped = [
        {"id": "A", "price": "190000"},   # prisfald
        {"id": "B", "price": "150000"},   # ny bil
    ]
    merged, hist, stats = scraper.merge_and_track(scraped, existing, price_history)
    by_id = {c["id"]: c for c in merged}
    assert stats["new"] == 1
    assert stats["price_changes"] == 1
    assert len(hist["A"]["prices"]) == 2


def test_merge_tracks_disappeared():
    """En bil der ikke laengere ses, skal markeres som forsvundet."""
    existing = [{"id": "A", "price": 200000, "status": "active"},
                {"id": "B", "price": 150000, "status": "active"}]
    scraped = [{"id": "A", "price": "200000"}]
    merged, _hist, stats = scraper.merge_and_track(scraped, existing, {})
    by_id = {c["id"]: c for c in merged}
    assert by_id["B"]["status"] == "disappeared"
    assert stats["disappeared"] == 1


def test_parse_embedded_next_data():
    """Annoncer i et __NEXT_DATA__ JSON-blob skal kunne udtraekkes (browser-gemt side)."""
    cars = {c["id"]: c for c in scraper.parse_listing_page(_read("listing_nextdata.html"))}
    assert "20000001" in cars and "20000002" in cars
    mazda = cars["20000001"]
    assert mazda["make"] == "Mazda"
    assert normalizer._to_int(mazda["price"]) == 229000
    assert mazda["url"].endswith("/brugt/bil/mazda/cx-5/20000001")


def test_embedded_phev_classified_after_scoring():
    """En PHEV importeret via __NEXT_DATA__ klassificeres som PHEV (blodt filter, ej haardt fravalg)."""
    import scoring
    cars = scraper.parse_listing_page(_read("listing_nextdata.html"))
    niro = normalizer.normalize_car(next(c for c in cars if c["id"] == "20000002"))
    assert niro["hybrid_type"] == "PHEV"
    reasons = scoring.evaluate_rejections(niro, scoring.load_settings())
    assert not any("Plug-in" in r for r in reasons)


def test_replace_drops_old_cars():
    """I erstat-tilstand (keep_disappeared=False) droppes biler, der ikke er i importen."""
    existing = [{"id": "A", "price": 200000, "status": "active"},
                {"id": "B", "price": 150000, "status": "active"}]
    scraped = [{"id": "A", "price": "200000"}]
    merged, _hist, _stats = scraper.merge_and_track(scraped, existing, {}, keep_disappeared=False)
    ids = {c["id"] for c in merged}
    assert ids == {"A"}  # B er droppet helt, ikke markeret forsvundet


def test_manual_import_semicolon(tmp_path):
    """Manuel import af semikolonsepareret tekst skal give normaliserbare biler."""
    csv_text = "id;make;model;price;fuel;model_year;mileage_km\n"
    csv_text += "555;Mazda;CX-5;219000;Benzin;2021;70000\n"
    f = tmp_path / "biler.txt"
    f.write_text(csv_text, encoding="utf-8")
    cars = scraper.import_file(f)
    assert len(cars) == 1
    assert cars[0]["make"] == "Mazda"


BILBASEN_CARD = '''
<section class="srp_results__x">
  <article class="Listing_listing__abc">
    <a href="https://www.bilbasen.dk/brugt/bil/seat/ateca/15-tsi-150-fr-dsg-5d/6912345" class="Listing_link__x"></a>
    <div class="Listing_makeModel__y"><div><h3 class="font-bold">Seat Ateca</h3>1,5 TSi 150 FR DSG 5d</div></div>
    <div class="Listing_price__z"><h3 class="font-bold">239.900&nbsp;kr.</h3></div>
    <div class="Listing_details__d"><ul class="ListingDetails_list__x">
      <li>5/2021</li><li>72.000 km</li><li>16,0 km/l</li><li>Automatisk gear</li><li>Benzin</li></ul></div>
    <div class="Listing_properties__p"><div>5/2021</div><div>72.000 km</div><div>16,0 km/l</div></div>
    <div class="Listing_description__d">Svingbart traek (elektrisk) 1900kg, adaptiv fartpilot, bakkamera, el-ruder</div>
    <div class="Listing_location__l"><div><span>Aalborg, Nordjylland</span></div></div>
    <img src="https://billeder.bilbasen.dk/x.jpeg" alt="Seat Ateca">
  </article>
</section>
'''


def test_parse_bilbasen_cards_extracts_id_and_fields():
    """En annoncekort-HTML skal give annonce-id, maerke, variant, pris, by og braendstof."""
    cars = {c["id"]: c for c in scraper.parse_listing_page(BILBASEN_CARD)}
    assert "6912345" in cars
    car = cars["6912345"]
    assert car["make"] == "Seat"
    assert car["model"] == "Ateca"
    assert car["variant"] == "1,5 TSi 150 FR DSG 5d"
    assert normalizer._to_int(car["price"]) == 239900
    assert car["city"] == "Aalborg"
    assert car["fuel"] == "Benzin"          # laest eksplicit fra Listing_details
    assert car["gearbox_name"] == "Automatisk gear"
    assert car["url"].endswith("/6912345")


def test_bilbasen_card_html_in_txt_is_detected():
    """HTML-kort i en .txt-fil skal parses som annoncer (ikke som CSV)."""
    import tempfile, os
    fd, path = tempfile.mkstemp(suffix=".txt")
    os.write(fd, BILBASEN_CARD.encode("utf-8"))
    os.close(fd)
    try:
        cars = scraper.import_file(Path(path))
        assert any(c["id"] == "6912345" for c in cars)
    finally:
        os.unlink(path)


def test_manual_import_json(tmp_path):
    """Manuel import af JSON skal understoette baade liste og {cars:[...]}."""
    f = tmp_path / "biler.json"
    f.write_text('{"cars": [{"id": "1", "make": "Kia"}]}', encoding="utf-8")
    cars = scraper.import_file(f)
    assert cars[0]["make"] == "Kia"
