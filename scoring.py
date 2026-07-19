"""Filtrering, vaegtforhold og gennemsigtig scoring af biler til campingvognsbrug.

Scoren gaar fra 0-100 og bestaar af seks vaegtede delscorer. Alle mellemresultater
gemmes, saa brugerfladen kan forklare praecist hvordan en bil er vurderet.

Modulet er rent og deterministisk og kan derfor testes uden netvaerk.
"""
from __future__ import annotations

import json
import statistics
from pathlib import Path
from typing import Any, Dict, List, Optional

DATA_DIR = Path(__file__).resolve().parent / "data"


def load_settings() -> Dict[str, Any]:
    """Indlaes standardindstillinger fra data/settings.json.

    Returns:
        Indstillings-dict med profil, vaegte og taerskler.
    """
    path = DATA_DIR / "settings.json"
    with path.open("r", encoding="utf-8") as fh:
        return json.load(fh)


def _clamp(value: float, low: float = 0.0, high: float = 100.0) -> float:
    """Begraens en vaerdi til intervallet [low, high].

    Args:
        value: Vaerdien der begraenses.
        low: Nedre graense.
        high: OEvre graense.

    Returns:
        Den begraensede vaerdi.
    """
    return max(low, min(high, value))


# --------------------------------------------------------------------------- #
# Filtrering / afvisning
# --------------------------------------------------------------------------- #
def evaluate_rejections(car: Dict[str, Any], settings: Dict[str, Any]) -> List[str]:
    """Valider en normaliseret bil mod hard-krav og returner afvisningsgrunde.

    Selv om Bilbasen-soegningen allerede filtrerer, valideres alt igen her.

    Args:
        car: Normaliseret bil-dict.
        settings: Indstillinger med profilkrav.

    Returns:
        Liste af praecise afvisningsgrunde (tom hvis bilen godkendes).
    """
    p = settings["profile"]
    reasons: List[str] = []

    fuel = car.get("fuel")
    hybrid = car.get("hybrid_type")

    if fuel == "diesel" and hybrid != "diesel-hybrid":
        reasons.append("Diesel er fravalgt")
    if hybrid == "diesel-hybrid":
        reasons.append("Dieselhybrid er fravalgt")
    if hybrid == "PHEV":
        reasons.append("Plug-in-hybrid er fravalgt")
    if fuel == "el":
        reasons.append("Elbil er som udgangspunkt fravalgt")

    gtype = car.get("gearbox_type_normalized")
    gname = (car.get("gearbox_name") or "").lower()
    if gtype == "amt" or "manuel" in gname and "automat" not in gname:
        # Manuelt gear identificeres via navn hvis normaliseringen ikke fangede det.
        if "manuel" in gname and "automat" not in gname:
            reasons.append("Manuelt gear (automatgear er et krav)")

    tow = car.get("tow_capacity_kg")
    tow_source = car.get("field_provenance", {}).get("tow_capacity_kg", {}).get("source")
    # Gaettet traekvaegt (modelviden) maa ikke udloese haardt fravalg - kun annoncens egen.
    if tow is not None and tow < p["min_tow_kg"] and tow_source != "modelviden":
        reasons.append(f"Traekvaegt {tow} kg er under kravet paa {p['min_tow_kg']} kg")

    year = car.get("model_year")
    if year is not None and year < p["model_year_min"]:
        reasons.append(f"Modelaar {year} er aeldre end {p['model_year_min']}")

    mileage = car.get("mileage_km")
    if mileage is not None and mileage > p["mileage_max_km"]:
        reasons.append(f"Kilometerstand {mileage} km overstiger {p['mileage_max_km']} km")

    price = car.get("price")
    if price is not None and price > p["price_max_dkk"]:
        reasons.append(f"Pris {price} kr. overstiger maksimum {p['price_max_dkk']} kr.")

    sale = car.get("sale_type")
    if sale in ("cvr", "engros"):
        reasons.append("Salgsform er CVR/engros")
    if sale == "leasing":
        reasons.append("Bilen udbydes som leasing")
    if sale == "uklar":
        reasons.append("Uklar juridisk salgsform")

    if car.get("registration_tax_missing") is True:
        reasons.append("Registreringsafgift mangler")

    return reasons


# --------------------------------------------------------------------------- #
# Vaegtforhold
# --------------------------------------------------------------------------- #
def compute_weight_ratio(car: Dict[str, Any], settings: Dict[str, Any],
                         caravan_weight: Optional[float] = None) -> Dict[str, Any]:
    """Beregn vaegtforholdet mellem campingvogn og bilens koereklar vaegt.

    Formel: vaegtforhold = campingvognens vaegt / bilens koereklar vaegt * 100.
    Dette er en sikkerhedsvejledning, ikke et lovkrav.

    Args:
        car: Normaliseret bil-dict (bruger kerb_weight_kg).
        settings: Indstillinger med taerskler.
        caravan_weight: Campingvognens vaegt i kg; default fra settings.

    Returns:
        Dict med ratio, color (green/yellow/red), excellent (bool),
        caravan_weight, kerb_weight og en forklarende note.
    """
    p = settings["profile"]
    th = settings["weight_ratio_thresholds"]
    caravan = caravan_weight if caravan_weight is not None else p["caravan_weight_kg"]
    kerb = car.get("kerb_weight_kg")

    if not kerb:
        return {
            "ratio": None,
            "color": "unknown",
            "excellent": False,
            "caravan_weight": caravan,
            "kerb_weight": None,
            "note": "Bilens koereklar vaegt er ukendt - vaegtforhold kan ikke beregnes. Verificer paa registreringsattesten.",
        }

    ratio = caravan / kerb * 100.0
    if ratio > th["yellow"]:
        color = "red"
    elif ratio > th["green"]:
        color = "yellow"
    else:
        color = "green"
    excellent = ratio <= th["excellent"]

    note = (f"Campingvogn {caravan:.0f} kg / bil {kerb:.0f} kg = {ratio:.0f}%. "
            "Vejledning: hoejst 90% er trygt, 90-100% kraever erfaring, over 100% frarades. "
            "Dette er en sikkerhedsvejledning, ikke et lovkrav.")

    return {
        "ratio": round(ratio, 1),
        "color": color,
        "excellent": excellent,
        "caravan_weight": caravan,
        "kerb_weight": kerb,
        "note": note,
    }


# --------------------------------------------------------------------------- #
# Delscorer
# --------------------------------------------------------------------------- #
def score_caravan(car: Dict[str, Any], settings: Dict[str, Any],
                  weight_ratio: Dict[str, Any]) -> Dict[str, Any]:
    """Beregn delscore for campingvognsegnethed (0-100).

    Kombinerer traekvaegt, vaegtforhold, moment, gearkasse-egnethed,
    anhaengerstabilisering, lasteevne og firehjulstraek (plus, ikke krav).

    Args:
        car: Normaliseret bil.
        settings: Indstillinger.
        weight_ratio: Resultat fra compute_weight_ratio.

    Returns:
        Dict med score og en liste af 'factors' (navn, vaerdi, bidrag, note).
    """
    p = settings["profile"]
    factors: List[Dict[str, Any]] = []
    total = 0.0

    # Traekvaegt (30 point) - jo mere over min, jo bedre op til preferred.
    tow = car.get("tow_capacity_kg")
    tow_pts = 0.0
    if tow is None:
        note = "Traekvaegt ukendt - verificer paa registreringsattesten"
        tow_pts = 12.0
    elif tow < p["min_tow_kg"]:
        note = f"{tow} kg er under kravet"
        tow_pts = 0.0
    elif tow >= p["preferred_tow_kg"]:
        note = f"{tow} kg opfylder det foretrukne niveau"
        tow_pts = 30.0
    else:
        frac = (tow - p["min_tow_kg"]) / max(1, p["preferred_tow_kg"] - p["min_tow_kg"])
        tow_pts = 20.0 + 10.0 * frac
        note = f"{tow} kg (mellem krav og foretrukket)"
    factors.append({"name": "Traekvaegt", "value": tow, "points": round(tow_pts, 1), "max": 30, "note": note})
    total += tow_pts

    # Vaegtforhold (25 point)
    ratio = weight_ratio.get("ratio")
    color = weight_ratio.get("color")
    if ratio is None:
        wr_pts = 10.0
        note = "Vaegtforhold ukendt"
    elif color == "green" and weight_ratio.get("excellent"):
        wr_pts = 25.0
        note = f"{ratio}% - fremragende stabilitet"
    elif color == "green":
        wr_pts = 22.0
        note = f"{ratio}% - trygt"
    elif color == "yellow":
        wr_pts = 14.0
        note = f"{ratio}% - kraever erfaring"
    else:
        wr_pts = 4.0
        note = f"{ratio}% - frarades"
    factors.append({"name": "Vaegtforhold", "value": ratio, "points": round(wr_pts, 1), "max": 25, "note": note})
    total += wr_pts

    # Gearkasse-egnethed (15 point)
    grating = car.get("gearbox", {}).get("towing_rating", {"score": 0.5, "note": ""})
    g_pts = 15.0 * float(grating.get("score", 0.5))
    factors.append({"name": "Gearkasse til traek", "value": car.get("gearbox", {}).get("label"),
                    "points": round(g_pts, 1), "max": 15, "note": grating.get("note", "")})
    total += g_pts

    # Moment (12 point) - godt traek kraever draejningsmoment.
    torque = car.get("torque_nm")
    if torque is None:
        t_pts = 5.0
        note = "Moment ukendt"
    else:
        # 300 Nm+ giver fuldt; skaler fra 150 Nm.
        t_pts = _clamp((torque - 150) / (300 - 150) * 12.0, 0, 12)
        note = f"{torque} Nm"
    factors.append({"name": "Moment", "value": torque, "points": round(t_pts, 1), "max": 12, "note": note})
    total += t_pts

    # Anhaengerstabilisering (10 point)
    ts = car.get("trailer_stability", {})
    status = ts.get("status")
    ts_map = {"documented_car": 10.0, "documented_model": 7.0, "requires_module": 6.0,
              "seller_claim": 6.0, "not_found": 2.0, "unknown": 3.0}
    ts_pts = ts_map.get(status, 3.0)
    factors.append({"name": "Anhaengerstabilisering", "value": ts.get("status_label"),
                    "points": round(ts_pts, 1), "max": 10, "note": ts.get("note", "")})
    total += ts_pts

    # Firehjulstraek (5 point, plus - ikke krav)
    drivetrain = (car.get("drivetrain") or "").lower()
    is_awd = any(k in drivetrain for k in ("4wd", "awd", "firehjul", "4x4", "4motion", "quattro", "xdrive"))
    awd_pts = 5.0 if is_awd else 2.5
    factors.append({"name": "Firehjulstraek (plus)", "value": "Ja" if is_awd else "Nej/ukendt",
                    "points": awd_pts, "max": 5, "note": "Firehjulstraek er en fordel, ikke et krav"})
    total += awd_pts

    # Lasteevne (3 point)
    payload = car.get("payload_kg")
    if payload is None:
        pl_pts = 1.5
        note = "Lasteevne ukendt"
    else:
        pl_pts = _clamp((payload - 400) / (700 - 400) * 3.0, 0, 3)
        note = f"{payload} kg"
    factors.append({"name": "Lasteevne", "value": payload, "points": round(pl_pts, 1), "max": 3, "note": note})
    total += pl_pts

    return {"score": round(_clamp(total), 1), "factors": factors}


def score_drivetrain(car: Dict[str, Any], settings: Dict[str, Any]) -> Dict[str, Any]:
    """Beregn delscore for drivlinje og gearkasse (0-100).

    Belonner klassisk momentomformer/e-CVT og straffer toerkoblet DCT samt
    uafklarede gearkasser.

    Args:
        car: Normaliseret bil.
        settings: Indstillinger.

    Returns:
        Dict med score og factors.
    """
    factors: List[Dict[str, Any]] = []
    total = 0.0

    gearbox = car.get("gearbox", {})
    grating = gearbox.get("towing_rating", {"score": 0.5})
    g_pts = 55.0 * float(grating.get("score", 0.5))
    factors.append({"name": "Gearkassetype", "value": gearbox.get("label"),
                    "points": round(g_pts, 1), "max": 55, "note": grating.get("note", "")})
    total += g_pts

    # Effekt (25 point) - tilstraekkelig kraft til traek.
    hp = car.get("hp")
    if hp is None:
        hp_pts = 10.0
        note = "Effekt ukendt"
    else:
        hp_pts = _clamp((hp - 110) / (200 - 110) * 25.0, 0, 25)
        note = f"{hp} hk"
    factors.append({"name": "Effekt", "value": hp, "points": round(hp_pts, 1), "max": 25, "note": note})
    total += hp_pts

    # Confidence i gearkasseklassifikation (20 point)
    conf = gearbox.get("confidence", "low")
    conf_pts = {"high": 20.0, "medium": 13.0, "low": 6.0}.get(conf, 6.0)
    factors.append({"name": "Sikkerhed i gearkassedata", "value": conf,
                    "points": conf_pts, "max": 20,
                    "note": "Lav sikkerhed betyder at typen boer verificeres"})
    total += conf_pts

    return {"score": round(_clamp(total), 1), "factors": factors}


def score_price(car: Dict[str, Any], settings: Dict[str, Any],
                market: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Beregn delscore for pris og markedsvaerdi (0-100).

    Bruger lokal markedsvurdering hvis tilgaengelig, ellers en profilbaseret
    vurdering ift. foretrukken prisgraense.

    Args:
        car: Normaliseret bil.
        settings: Indstillinger.
        market: Valgfri markedsvurdering fra assess_market_value.

    Returns:
        Dict med score og factors.
    """
    p = settings["profile"]
    factors: List[Dict[str, Any]] = []
    price = car.get("price")

    if price is None:
        return {"score": 40.0, "factors": [{"name": "Pris", "value": None, "points": 40, "max": 100,
                                            "note": "Pris ukendt"}]}

    # Markedsvurdering (60 point)
    if market and market.get("sufficient"):
        median = market["median"]
        diff_pct = (price - median) / median * 100.0
        # -15% => fuldt, +15% => 0.
        mv_pts = _clamp((15 - diff_pct) / 30 * 60.0, 0, 60)
        note = f"{diff_pct:+.0f}% ift. median {median:.0f} kr. ({market['count']} sammenlignelige)"
    else:
        mv_pts = 30.0
        note = "Utilstraekkeligt datagrundlag til markedsvurdering"
    factors.append({"name": "Ift. markedet", "points": round(mv_pts, 1), "max": 60, "note": note})

    # Ift. foretrukken prisgraense (40 point)
    if price <= p["price_preferred_dkk"]:
        pref_pts = 40.0
        note = f"{price} kr. er under foretrukket graense ({p['price_preferred_dkk']} kr.)"
    elif price <= p["price_max_dkk"]:
        frac = (price - p["price_preferred_dkk"]) / max(1, p["price_max_dkk"] - p["price_preferred_dkk"])
        pref_pts = 40.0 * (1 - frac)
        note = f"{price} kr. mellem foretrukket og maksimum"
    else:
        pref_pts = 0.0
        note = f"{price} kr. over maksimum"
    factors.append({"name": "Ift. budget", "value": price, "points": round(pref_pts, 1), "max": 40, "note": note})

    total = mv_pts + pref_pts
    return {"score": round(_clamp(total), 1), "factors": factors}


def score_age_mileage(car: Dict[str, Any], settings: Dict[str, Any]) -> Dict[str, Any]:
    """Beregn delscore for alder og kilometerstand (0-100).

    Args:
        car: Normaliseret bil.
        settings: Indstillinger med kilometer-baand.

    Returns:
        Dict med score og factors.
    """
    p = settings["profile"]
    bands = settings["mileage_bands"]
    factors: List[Dict[str, Any]] = []
    total = 0.0

    # Kilometer (60 point)
    km = car.get("mileage_km")
    if km is None:
        km_pts = 25.0
        note = "Kilometerstand ukendt"
    elif km < bands["great_below"]:
        km_pts = 60.0
        note = f"{km} km - saerligt attraktivt (under {bands['great_below']})"
    elif km < bands["acceptable_below"]:
        km_pts = 45.0
        note = f"{km} km - acceptabelt"
    elif km < bands["conditional_below"]:
        km_pts = 28.0
        note = f"{km} km - kraever god pris og dokumenteret historik"
    else:
        km_pts = 5.0
        note = f"{km} km - hoejt"
    factors.append({"name": "Kilometerstand", "value": km, "points": round(km_pts, 1), "max": 60, "note": note})
    total += km_pts

    # Alder (40 point)
    year = car.get("model_year")
    if year is None:
        age_pts = 18.0
        note = "Modelaar ukendt"
    else:
        # 2019 => basis, nyere => bedre. Antag referenceaar 2026.
        span = max(0, year - p["model_year_min"])
        age_pts = _clamp(20.0 + span * 4.0, 0, 40)
        note = f"Modelaar {year}"
    factors.append({"name": "Alder", "value": year, "points": round(age_pts, 1), "max": 40, "note": note})
    total += age_pts

    return {"score": round(_clamp(total), 1), "factors": factors}


def score_safety_equipment(car: Dict[str, Any], settings: Dict[str, Any]) -> Dict[str, Any]:
    """Beregn delscore for sikkerhed og udstyr (0-100).

    Baseret paa hvor mange prioriterede udstyrspunkter bilen har.

    Args:
        car: Normaliseret bil.
        settings: Indstillinger med prioriteret udstyrsliste.

    Returns:
        Dict med score og factors (fundet/manglende udstyr).
    """
    priority: List[str] = settings["priority_equipment"]
    equipment = set(car.get("equipment", []))
    # 13-polet noegle matcher '13-polet'
    found = [e for e in priority if e in equipment]
    missing = [e for e in priority if e not in equipment]
    frac = len(found) / max(1, len(priority))
    score = round(_clamp(frac * 100.0), 1)
    factors = [
        {"name": "Prioriteret udstyr fundet", "value": f"{len(found)}/{len(priority)}",
         "points": score, "max": 100, "note": ", ".join(found) if found else "Intet prioriteret udstyr fundet"},
    ]
    return {"score": score, "factors": factors, "found": found, "missing": missing}


def score_running_cost(car: Dict[str, Any], settings: Dict[str, Any],
                       economy: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Beregn delscore for driftsoekonomi (0-100).

    Lavere samlet aarlig omkostning giver hoejere score.

    Args:
        car: Normaliseret bil.
        settings: Indstillinger.
        economy: Valgfri forudberegnet oekonomi fra compute_economy.

    Returns:
        Dict med score og factors.
    """
    economy = economy or compute_economy(car, settings)
    annual = economy.get("annual_total")
    factors: List[Dict[str, Any]] = []
    if annual is None:
        return {"score": 50.0, "factors": [{"name": "Driftsoekonomi", "points": 50, "max": 100,
                                            "note": "Utilstraekkelige data"}]}
    # 25.000 kr/aar => fuldt, 60.000 kr/aar => 0.
    score = round(_clamp((60000 - annual) / (60000 - 25000) * 100.0), 1)
    factors.append({"name": "Samlet aarlig omkostning", "value": round(annual),
                    "points": score, "max": 100, "note": f"Ca. {annual:,.0f} kr./aar (vejledende)".replace(",", ".")})
    return {"score": score, "factors": factors}


# --------------------------------------------------------------------------- #
# Driftsoekonomi
# --------------------------------------------------------------------------- #
def compute_economy(car: Dict[str, Any], settings: Dict[str, Any],
                    overrides: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Beregn vejledende driftsoekonomi for en bil.

    Skelner mellem annoncerede fakta (afgift), beregnede vaerdier (braendstof)
    og skoen (vaerditab, service).

    Args:
        car: Normaliseret bil.
        settings: Indstillinger med profil og satser.
        overrides: Valgfri brugerindstillinger der overskriver profil.

    Returns:
        Dict med braendstof, afgift, service, vaerditab, samlet aarlig omkostning
        og omkostning pr. km, hver med et 'kind' (fakta/beregnet/skoen).
    """
    p = dict(settings["profile"])
    if overrides:
        p.update(overrides)

    annual_km = p["annual_km"]
    caravan_km = p.get("caravan_km_per_year", 0)
    solo_km = max(0, annual_km - caravan_km)
    fuel_price = p["fuel_price_dkk"]
    cons_solo = p["expected_consumption_km_per_l"]
    cons_tow = p.get("expected_consumption_towing_km_per_l", cons_solo * 0.6)

    fuel_cost = 0.0
    if cons_solo:
        fuel_cost += solo_km / cons_solo * fuel_price
    if cons_tow:
        fuel_cost += caravan_km / cons_tow * fuel_price

    tax = car.get("periodic_tax") or 0
    maintenance = settings.get("maintenance_dkk_per_year", 7000)

    price = car.get("price") or 0
    depreciation = price * settings.get("depreciation_rate_per_year", 0.11)

    annual_total = fuel_cost + tax + maintenance + depreciation
    cost_per_km = annual_total / annual_km if annual_km else None

    return {
        "fuel_cost": round(fuel_cost),
        "fuel_cost_kind": "beregnet",
        "periodic_tax": tax,
        "periodic_tax_kind": "fakta" if car.get("periodic_tax") else "skoen",
        "maintenance": maintenance,
        "maintenance_kind": "skoen",
        "depreciation": round(depreciation),
        "depreciation_kind": "skoen",
        "annual_total": round(annual_total),
        "annual_total_kind": "beregnet",
        "cost_per_km": round(cost_per_km, 2) if cost_per_km else None,
        "assumptions": {
            "annual_km": annual_km, "caravan_km": caravan_km, "fuel_price": fuel_price,
            "consumption_solo": cons_solo, "consumption_towing": cons_tow,
        },
    }


# --------------------------------------------------------------------------- #
# Markedsvurdering
# --------------------------------------------------------------------------- #
def assess_market_value(car: Dict[str, Any], all_cars: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Lav en lokal markedsvurdering ud fra sammenlignelige biler i datasaettet.

    Sammenligner primaert paa maerke+model+gearkasse+karrosseri, hoejst to aars
    forskel og sammenlignelig kilometerstand.

    Args:
        car: Bilen der vurderes.
        all_cars: Alle (ikke-afviste) biler i datasaettet.

    Returns:
        Dict med sufficient (bool), median, low, high, count, diff_to_median,
        diff_pct og data_quality.
    """
    if car.get("price") is None:
        return {"sufficient": False, "count": 0, "reason": "Ingen pris"}

    make = (car.get("make") or "").lower()
    model = (car.get("model") or "").lower()
    gtype = car.get("gearbox_type_normalized")
    body = (car.get("body_type") or "").lower()
    year = car.get("model_year")
    km = car.get("mileage_km")

    comparable_prices: List[int] = []
    for other in all_cars:
        if other.get("id") == car.get("id"):
            continue
        if other.get("price") is None:
            continue
        if (other.get("make") or "").lower() != make:
            continue
        if (other.get("model") or "").lower() != model:
            continue
        if gtype and other.get("gearbox_type_normalized") and other.get("gearbox_type_normalized") != gtype:
            continue
        if body and other.get("body_type") and (other.get("body_type") or "").lower() != body:
            continue
        oy = other.get("model_year")
        if year and oy and abs(oy - year) > 2:
            continue
        okm = other.get("mileage_km")
        if km and okm and abs(okm - km) > 40000:
            continue
        comparable_prices.append(other["price"])

    count = len(comparable_prices)
    if count < 3:
        return {"sufficient": False, "count": count,
                "reason": "Utilstraekkeligt datagrundlag", "data_quality": "lav"}

    median = statistics.median(comparable_prices)
    diff = car["price"] - median
    return {
        "sufficient": True,
        "count": count,
        "median": median,
        "low": min(comparable_prices),
        "high": max(comparable_prices),
        "diff_to_median": diff,
        "diff_pct": round(diff / median * 100.0, 1) if median else None,
        "data_quality": "god" if count >= 6 else "moderat",
    }


# --------------------------------------------------------------------------- #
# Fordele / ulemper / risici
# --------------------------------------------------------------------------- #
def derive_pros_cons_risks(car: Dict[str, Any], settings: Dict[str, Any],
                           weight_ratio: Dict[str, Any],
                           subscores: Dict[str, Any]) -> Dict[str, List[str]]:
    """Udled menneskelaesbare styrker, svagheder og risici for en bil.

    Args:
        car: Normaliseret bil.
        settings: Indstillinger.
        weight_ratio: Vaegtforholds-resultat.
        subscores: Beregnede delscorer.

    Returns:
        Dict med noeglerne pros, cons og risks (lister af strenge).
    """
    pros: List[str] = []
    cons: List[str] = []
    risks: List[str] = []

    tow = car.get("tow_capacity_kg")
    if tow and tow >= settings["profile"]["preferred_tow_kg"]:
        pros.append(f"Traekker {tow} kg - opfylder det foretrukne niveau")
    elif tow and tow >= settings["profile"]["min_tow_kg"]:
        pros.append(f"Traekker {tow} kg - opfylder kravet")

    if weight_ratio.get("color") == "green":
        pros.append(f"Godt vaegtforhold ({weight_ratio.get('ratio')}%)")
    elif weight_ratio.get("color") == "yellow":
        cons.append(f"Vaegtforhold {weight_ratio.get('ratio')}% kraever erfaring")
    elif weight_ratio.get("color") == "red":
        risks.append(f"Vaegtforhold {weight_ratio.get('ratio')}% overstiger 100% - frarades")

    gearbox = car.get("gearbox", {})
    if gearbox.get("type") == "torque_converter":
        pros.append("Klassisk momentomformer - robust til traek")
    elif gearbox.get("type") == "ecvt_hybrid":
        pros.append("e-CVT hybridsystem - driftssikkert")
    elif gearbox.get("type") == "dry_dct":
        risks.append("Toerkoblet DCT - mindre egnet til vedvarende tung anhaengertraek")
    elif gearbox.get("type") == "unknown":
        risks.append("Gearkassetype uafklaret - skal verificeres")
    for r in gearbox.get("risks", []):
        risks.append(r)

    km = car.get("mileage_km")
    bands = settings["mileage_bands"]
    if km is not None:
        if km < bands["great_below"]:
            pros.append(f"Lav kilometerstand ({km} km)")
        elif km >= bands["acceptable_below"]:
            cons.append(f"Hoej kilometerstand ({km} km) - kraev dokumenteret historik")

    ts = car.get("trailer_stability", {})
    if ts.get("status") in ("not_found", "unknown"):
        risks.append("Anhaengerstabilisering ikke bekraeftet - kontroller trailermodul og kodning")

    for wf in ("kerb_weight_kg", "tow_capacity_kg", "train_weight_kg", "nose_weight_kg"):
        prov = car.get("field_provenance", {}).get(wf, {})
        if prov.get("value") is None:
            risks.append(f"{wf.replace('_', ' ')} mangler - skal verificeres paa registreringsattesten")
            break

    if subscores.get("price", {}).get("score", 0) >= 70:
        pros.append("Attraktiv pris ift. markedet/budget")

    return {"pros": pros, "cons": cons, "risks": risks}


# --------------------------------------------------------------------------- #
# Samlet scoring
# --------------------------------------------------------------------------- #
def score_car(car: Dict[str, Any], settings: Optional[Dict[str, Any]] = None,
              all_cars: Optional[List[Dict[str, Any]]] = None,
              caravan_weight: Optional[float] = None) -> Dict[str, Any]:
    """Beregn den samlede score og alle delresultater for en normaliseret bil.

    Args:
        car: Normaliseret bil-dict.
        settings: Indstillinger; indlaeses hvis udeladt.
        all_cars: Alle biler (til markedsvurdering); valgfri.
        caravan_weight: Campingvognens vaegt; default fra settings.

    Returns:
        En ny bil-dict beriget med score, subscores, weight_ratio, market,
        economy, pros, cons, risks og rejection_reasons.
    """
    settings = settings or load_settings()
    all_cars = all_cars or []
    car = dict(car)

    rejection_reasons = evaluate_rejections(car, settings)
    car["rejection_reasons"] = rejection_reasons
    car["rejected"] = bool(rejection_reasons)

    weight_ratio = compute_weight_ratio(car, settings, caravan_weight)
    car["weight_ratio"] = weight_ratio

    market = assess_market_value(car, all_cars)
    car["market"] = market

    economy = compute_economy(car, settings)
    car["economy"] = economy

    sub = {
        "caravan": score_caravan(car, settings, weight_ratio),
        "drivetrain": score_drivetrain(car, settings),
        "price": score_price(car, settings, market),
        "age_mileage": score_age_mileage(car, settings),
        "safety_equipment": score_safety_equipment(car, settings),
        "running_cost": score_running_cost(car, settings, economy),
    }
    car["subscores"] = sub

    weights = settings["weights"]
    total = (
        sub["caravan"]["score"] * weights["caravan"]
        + sub["drivetrain"]["score"] * weights["drivetrain"]
        + sub["price"]["score"] * weights["price"]
        + sub["age_mileage"]["score"] * weights["age_mileage"]
        + sub["safety_equipment"]["score"] * weights["safety_equipment"]
        + sub["running_cost"]["score"] * weights["running_cost"]
    )
    car["score"] = round(_clamp(total), 1)
    car["caravan_score"] = sub["caravan"]["score"]

    pcr = derive_pros_cons_risks(car, settings, weight_ratio, sub)
    car["pros"] = pcr["pros"]
    car["cons"] = pcr["cons"]
    car["risks"] = pcr["risks"]

    return car


def score_all(cars: List[Dict[str, Any]], settings: Optional[Dict[str, Any]] = None,
              caravan_weight: Optional[float] = None) -> List[Dict[str, Any]]:
    """Score en hel liste af normaliserede biler (to pass for markedsvurdering).

    Args:
        cars: Liste af normaliserede biler.
        settings: Indstillinger; indlaeses hvis udeladt.
        caravan_weight: Campingvognens vaegt.

    Returns:
        Liste af scorede biler.
    """
    settings = settings or load_settings()
    # Pass 1: foreloebig afvisning for at bestemme markedsgrundlag (kun godkendte).
    prelim = []
    for c in cars:
        cc = dict(c)
        cc["rejection_reasons"] = evaluate_rejections(cc, settings)
        prelim.append(cc)
    active = [c for c in prelim if not c["rejection_reasons"]]
    # Pass 2: fuld scoring med markedsgrundlag fra aktive biler.
    return [score_car(c, settings, active, caravan_weight) for c in cars]


if __name__ == "__main__":
    settings = load_settings()
    print("Indstillinger indlaest. Vaegte:", settings["weights"])
