"""Tests for normalisering, drivmiddelklassifikation, gearkasse, vaegtforhold og score.

Testene er offline og deterministiske.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import normalizer  # noqa: E402
import scoring  # noqa: E402

SETTINGS = scoring.load_settings()


# --------------------------------------------------------------------------- #
# Drivmiddel
# --------------------------------------------------------------------------- #
def test_classify_petrol_hybrid():
    """En benzin-hybrid skal klassificeres som benzin + HEV."""
    r = normalizer.classify_fuel("Hybrid (Benzin)", "2.5 Hybrid AWD")
    assert r["fuel"] == "benzin"
    assert r["hybrid_type"] == "HEV"


def test_classify_plugin_hybrid():
    """En plug-in hybrid skal faa hybrid_type PHEV."""
    r = normalizer.classify_fuel("Plug-in Hybrid (Benzin)", "iV opladelig")
    assert r["hybrid_type"] == "PHEV"


def test_classify_diesel():
    """Diesel skal klassificeres som diesel."""
    r = normalizer.classify_fuel("Diesel", "2.0 TDI DSG")
    assert r["fuel"] == "diesel"
    assert r["hybrid_type"] == ""


def test_elektrisk_in_description_is_not_ev():
    """Ordet 'elektrisk' i beskrivelsen maa ikke fejlklassificere en benzinbil som elbil."""
    car = normalizer.normalize_car({
        "id": "e1", "make": "Seat", "model": "Ateca", "variant": "1,5 TSi 150 FR DSG",
        "description": "Svingbart traek (elektrisk) 1900kg, el-ruder, el-sidespejle",
        "dealer": "Auto"})
    assert car["fuel"] == "benzin"
    reasons = scoring.evaluate_rejections(car, SETTINGS)
    assert not any("Elbil" in r for r in reasons)


def test_petrol_mild_hybrid_defaults_to_benzin():
    """En benzin-mildhybrid uden tydelig motormarkoer antages at vaere benzin."""
    r = normalizer.classify_fuel("", "Qashqai 1,3 mHEV 158")
    assert r["hybrid_type"] == "MHEV"
    assert r["fuel"] == "benzin"


def test_ev_classified_but_not_hard_rejected():
    """En elbil klassificeres korrekt, men afvises ikke haardt (blodt filter i UI)."""
    car = normalizer.normalize_car({
        "id": "ev1", "make": "VW", "model": "ID.4", "variant": "Pro Performance",
        "fuel": "El", "dealer": "Auto"})
    assert car["fuel"] == "el"
    reasons = scoring.evaluate_rejections(car, SETTINGS)
    assert not any("Elbil" in r for r in reasons)


def test_classify_mild_hybrid():
    """En mildhybrid skal faa hybrid_type MHEV og fuel benzin."""
    r = normalizer.classify_fuel("Mild-hybrid (Benzin)", "B4 48V")
    assert r["fuel"] == "benzin"
    assert r["hybrid_type"] == "MHEV"


# --------------------------------------------------------------------------- #
# Gearkasse
# --------------------------------------------------------------------------- #
def test_gearbox_dry_dct_flagged():
    """Ford PowerShift skal klassificeres som toerkoblet DCT med risiko."""
    g = normalizer.classify_gearbox("Ford", "Focus", "1.5 EcoBoost", "PowerShift", 2020, "")
    assert g["type"] == "dry_dct"
    assert g["risks"]


def test_gearbox_torque_converter():
    """Volvo Geartronic skal klassificeres som momentomformer."""
    g = normalizer.classify_gearbox("Volvo", "XC60", "B4", "Geartronic", 2020, "")
    assert g["type"] == "torque_converter"


def test_gearbox_unknown_not_from_automatgear():
    """Et ukendt maerke maa ikke klassificeres alene ud fra 'automatgear'."""
    g = normalizer.classify_gearbox("Ukendtmaerke", "Model", "1.0", "Automatgear", 2020, "")
    assert g["type"] == "unknown"
    assert g["confidence"] == "low"


def test_gearbox_ecvt_hybrid():
    """Toyota hybrid skal klassificeres som e-CVT/hybridsystem."""
    g = normalizer.classify_gearbox("Toyota", "RAV4", "2.5 Hybrid", "Automatgear", 2021, "Hybrid")
    assert g["type"] == "ecvt_hybrid"


# --------------------------------------------------------------------------- #
# Vaegtforhold
# --------------------------------------------------------------------------- #
def test_weight_ratio_green():
    """1400 kg vogn / 1710 kg bil = ca. 82% -> groen og fremragende."""
    car = {"kerb_weight_kg": 1710}
    wr = scoring.compute_weight_ratio(car, SETTINGS, caravan_weight=1400)
    assert wr["color"] == "green"
    assert wr["excellent"] is True


def test_weight_ratio_red():
    """En let bil giver over 100% -> roed."""
    car = {"kerb_weight_kg": 1300}
    wr = scoring.compute_weight_ratio(car, SETTINGS, caravan_weight=1400)
    assert wr["color"] == "red"


def test_weight_ratio_unknown():
    """Uden egenvaegt kan vaegtforhold ikke beregnes."""
    wr = scoring.compute_weight_ratio({}, SETTINGS, caravan_weight=1400)
    assert wr["ratio"] is None
    assert wr["color"] == "unknown"


# --------------------------------------------------------------------------- #
# Afvisning / filtrering
# --------------------------------------------------------------------------- #
def _normalize(raw):
    """Normaliser en raa bil til test.

    Args:
        raw: Raa bil-dict.

    Returns:
        Normaliseret bil.
    """
    return normalizer.normalize_car(raw)


def test_diesel_classified_but_not_hard_rejected():
    """Diesel klassificeres korrekt, men afvises IKKE haardt (styres som blodt filter)."""
    car = _normalize({"id": "d", "make": "VW", "model": "Passat", "fuel": "Diesel",
                      "variant": "2.0 TDI", "gearbox_name": "DSG", "tow_capacity_kg": 1800,
                      "model_year": 2020, "mileage_km": 90000, "price": 180000,
                      "dealer": "VW Bilhuset"})
    assert car["fuel"] == "diesel"
    reasons = scoring.evaluate_rejections(car, SETTINGS)
    assert not any("Diesel" in r for r in reasons)


def test_plugin_hybrid_classified_but_not_hard_rejected():
    """Plug-in hybrid klassificeres, men afvises ikke haardt (blodt filter i UI)."""
    car = _normalize({"id": "p", "make": "Skoda", "model": "Superb", "fuel": "Plug-in Hybrid",
                      "variant": "iV opladelig", "gearbox_name": "DSG", "tow_capacity_kg": 1800,
                      "model_year": 2021, "mileage_km": 70000, "price": 240000, "dealer": "Skoda"})
    assert car["hybrid_type"] == "PHEV"
    reasons = scoring.evaluate_rejections(car, SETTINGS)
    assert not any("Plug-in" in r for r in reasons)


def test_reject_low_tow():
    """En bil med traekvaegt under 1600 kg skal afvises."""
    car = _normalize({"id": "t", "make": "Ford", "model": "Focus", "fuel": "Benzin",
                      "variant": "1.5", "gearbox_name": "Automatgear", "tow_capacity_kg": 1500,
                      "model_year": 2020, "mileage_km": 90000, "price": 165000, "dealer": "Ford"})
    reasons = scoring.evaluate_rejections(car, SETTINGS)
    assert any("Traekvaegt" in r for r in reasons)


def test_reject_high_mileage():
    """En bil over 125.000 km skal afvises."""
    car = _normalize({"id": "m", "make": "Toyota", "model": "RAV4", "fuel": "Hybrid (Benzin)",
                      "variant": "2.5", "gearbox_name": "Automatgear", "tow_capacity_kg": 1650,
                      "model_year": 2020, "mileage_km": 130000, "price": 200000, "dealer": "Toyota"})
    reasons = scoring.evaluate_rejections(car, SETTINGS)
    assert any("Kilometerstand" in r for r in reasons)


def test_valid_car_not_rejected():
    """En bil der opfylder alle krav maa ikke afvises."""
    car = _normalize({"id": "ok", "make": "Toyota", "model": "RAV4", "fuel": "Hybrid (Benzin)",
                      "variant": "2.5 Hybrid", "gearbox_name": "Automatgear", "tow_capacity_kg": 1650,
                      "kerb_weight_kg": 1710, "model_year": 2021, "mileage_km": 78000,
                      "price": 249000, "dealer": "Toyota Nord"})
    reasons = scoring.evaluate_rejections(car, SETTINGS)
    assert reasons == []


# --------------------------------------------------------------------------- #
# Samlet score
# --------------------------------------------------------------------------- #
def test_score_car_produces_subscores():
    """En scoret bil skal have samlet score og alle seks delscorer."""
    car = _normalize({"id": "ok", "make": "Toyota", "model": "RAV4", "fuel": "Hybrid (Benzin)",
                      "variant": "2.5 Hybrid AWD", "gearbox_name": "Automatgear",
                      "tow_capacity_kg": 1650, "kerb_weight_kg": 1710, "torque_nm": 221,
                      "hp": 222, "model_year": 2021, "mileage_km": 78000, "price": 249000,
                      "periodic_tax": 1130, "dealer": "Toyota Nord",
                      "description": "Adaptiv fartpilot bakkamera LED anhaengertraek"})
    scored = scoring.score_car(car, SETTINGS)
    assert 0 <= scored["score"] <= 100
    assert set(scored["subscores"]) == {
        "caravan", "drivetrain", "price", "age_mileage", "safety_equipment", "running_cost"}


def test_good_tow_car_scores_higher_than_weak():
    """En stabil trailerbil skal score hoejere paa campingvogn end en svag."""
    strong = scoring.score_car(_normalize({
        "id": "s", "make": "Volvo", "model": "XC60", "fuel": "Mild-hybrid (Benzin)",
        "variant": "B4", "gearbox_name": "Geartronic", "tow_capacity_kg": 2000,
        "kerb_weight_kg": 1899, "torque_nm": 300, "hp": 197, "model_year": 2020,
        "mileage_km": 89000, "price": 245000}), SETTINGS)
    weak = scoring.score_car(_normalize({
        "id": "w", "make": "Ukendt", "model": "Model", "fuel": "Benzin",
        "variant": "1.0", "gearbox_name": "Automatgear", "tow_capacity_kg": 1600,
        "kerb_weight_kg": 1250, "torque_nm": 160, "hp": 110, "model_year": 2019,
        "mileage_km": 120000, "price": 240000}), SETTINGS)
    assert strong["caravan_score"] > weak["caravan_score"]


def test_market_value_insufficient_data():
    """Med faerre end tre sammenlignelige biler skal markedsvurdering vaere utilstraekkelig."""
    car = {"id": "1", "make": "Toyota", "model": "RAV4", "price": 249000, "model_year": 2021}
    market = scoring.assess_market_value(car, [car])
    assert market["sufficient"] is False


def test_economy_computed():
    """Driftsoekonomi skal give en positiv samlet aarlig omkostning."""
    car = _normalize({"id": "e", "make": "Toyota", "model": "RAV4", "fuel": "Hybrid (Benzin)",
                      "variant": "2.5", "price": 249000, "periodic_tax": 1130})
    econ = scoring.compute_economy(car, SETTINGS)
    assert econ["annual_total"] > 0
    assert econ["fuel_cost_kind"] == "beregnet"
