"""Normalisering af raa bildata fra Bilbasen til et ensartet, struktureret format.

Modulet omsaetter loest struktureret annoncetekst (drivmiddel, gearkasse, vaegte,
udstyr osv.) til normaliserede vaerdier og gemmer samtidig kilde og confidence for
hvert felt, saa datakvaliteten kan vurderes i brugerfladen.

Alle offentlige funktioner er rene (uden sideeffekter) og kan derfor testes isoleret.
"""
from __future__ import annotations

import json
import re
import unicodedata
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

DATA_DIR = Path(__file__).resolve().parent / "data"


# --------------------------------------------------------------------------- #
# Hjaelpefunktioner
# --------------------------------------------------------------------------- #
def _strip_accents(text: str) -> str:
    """Fjern diakritiske tegn, saa 'æ/ø/å' og 'é' kan matches robust.

    Args:
        text: Vilkaarlig tekst.

    Returns:
        Tekst uden diakritiske tegn (ae/oe/aa bevares dog laesbart via erstatning).
    """
    if not text:
        return ""
    replacements = {"æ": "ae", "ø": "oe", "å": "aa", "Æ": "AE", "Ø": "OE", "Å": "AA"}
    for src, dst in replacements.items():
        text = text.replace(src, dst)
    nfkd = unicodedata.normalize("NFKD", text)
    return "".join(c for c in nfkd if not unicodedata.combining(c))


def _norm(text: Optional[str]) -> str:
    """Normaliser tekst til lowercase uden accenter og med enkelt mellemrum.

    Args:
        text: Tekst der skal normaliseres, kan vaere None.

    Returns:
        Normaliseret soegbar streng.
    """
    if not text:
        return ""
    text = _strip_accents(str(text)).lower()
    return re.sub(r"\s+", " ", text).strip()


# Maerke-aliaser: Bilbasens korte navne -> kanonisk navn brugt i videns-filerne.
MAKE_ALIASES: Dict[str, str] = {
    "vw": "volkswagen",
    "mercedes": "mercedes-benz",
    "mercedes benz": "mercedes-benz",
    "merc": "mercedes-benz",
    "volvo cars": "volvo",
    "skoda": "skoda",
    "citroën": "citroen",
    "citroen": "citroen",
}


def normalize_make(make: Optional[str]) -> str:
    """Oversaet et maerkenavn til den kanoniske form brugt i videns-filerne.

    Bilbasen bruger fx 'VW' og 'Mercedes', mens videns-filerne bruger
    'Volkswagen' og 'Mercedes-Benz'. Denne funktion bygger bro.

    Args:
        make: Maerkenavn som det staar i annoncen.

    Returns:
        Normaliseret (lowercase) kanonisk maerkenavn.
    """
    key = _norm(make)
    return MAKE_ALIASES.get(key, key)


def _to_int(value: Any) -> Optional[int]:
    """Konverter en vaerdi til heltal ved at fjerne alt undtagen cifre.

    Args:
        value: Tal, streng med tusindtalsseparatorer, enheder m.m.

    Returns:
        Heltal, eller None hvis ingen cifre kunne findes.
    """
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return int(value)
    digits = re.sub(r"[^\d]", "", str(value))
    return int(digits) if digits else None


def _to_float(value: Any) -> Optional[float]:
    """Konverter en vaerdi til kommatal (accepterer baade ',' og '.').

    Args:
        value: Tal eller streng, evt. med enhed.

    Returns:
        Kommatal, eller None hvis konvertering ikke er mulig.
    """
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    match = re.search(r"-?\d+(?:[.,]\d+)?", str(value))
    if not match:
        return None
    return float(match.group(0).replace(",", "."))


def _field(value: Any, source: str, confidence: str,
           original: Any = None, conflict: Optional[str] = None) -> Dict[str, Any]:
    """Byg en feltbeskrivelse med vaerdi, kilde og confidence (provenance).

    Args:
        value: Den normaliserede vaerdi.
        source: Datakilde, fx 'annonce', 'beregnet', 'modelviden'.
        confidence: 'high' | 'medium' | 'low'.
        original: Den oprindelige raa tekst, hvis relevant.
        conflict: Beskrivelse af eventuel konflikt mellem kilder.

    Returns:
        Dict med noeglerne value, source, confidence, original, conflict.
    """
    return {
        "value": value,
        "source": source,
        "confidence": confidence,
        "original": original,
        "conflict": conflict,
    }


# --------------------------------------------------------------------------- #
# Videns-baser
# --------------------------------------------------------------------------- #
def load_json(path: Path) -> Dict[str, Any]:
    """Indlaes en JSON-fil til en dict.

    Args:
        path: Sti til JSON-filen.

    Returns:
        Indholdet som dict. Tom dict hvis filen ikke findes.
    """
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as fh:
        return json.load(fh)


def load_gearbox_knowledge() -> Dict[str, Any]:
    """Indlaes gearkasseviden fra data/gearbox_knowledge.json.

    Returns:
        Gearkasse-videns-dict.
    """
    return load_json(DATA_DIR / "gearbox_knowledge.json")


def load_trailer_knowledge() -> Dict[str, Any]:
    """Indlaes viden om anhaengerstabilisering.

    Returns:
        Trailer-stabiliserings-videns-dict.
    """
    return load_json(DATA_DIR / "trailer_stability_knowledge.json")


# --------------------------------------------------------------------------- #
# Drivmiddel / hybridtype
# --------------------------------------------------------------------------- #
def classify_fuel(raw_fuel: Optional[str], text: str = "") -> Dict[str, Any]:
    """Klassificer drivmiddel og hybridtype ud fra annoncefelter og fritekst.

    Skelner mellem benzin, diesel, el samt hybridvarianter (HEV, mildhybrid,
    plug-in, dieselhybrid). Bruges baade til visning og til filtrering.

    Args:
        raw_fuel: Bilbasens drivmiddelfelt, fx 'Benzin' eller 'Hybrid (Benzin)'.
        text: Ekstra kontekst (variant + beskrivelse) til at forfine typen.

    Returns:
        Dict med:
            fuel: 'benzin' | 'diesel' | 'el' | 'ukendt'
            hybrid_type: '' | 'HEV' | 'MHEV' | 'PHEV' | 'diesel-hybrid'
            label: Menneskelaesbar etiket.
    """
    # VIGTIGT: braendstof udledes KUN af det eksplicitte felt + variant/model
    # (motorkode), ALDRIG af marketing-beskrivelsen, hvor ord som 'elektrisk'
    # (fx 'elektrisk trae k') og 'el-ruder' ellers ville fejlklassificere bilen.
    blob = _norm(f"{raw_fuel or ''} {text or ''}")
    fuel_token = _norm(raw_fuel or "")

    is_diesel = bool(re.search(r"\bdiesel\b|\btdi\b|\bhdi\b|\bdci\b|\bcdi\b|\bcrdi\b|\bbluehdi\b|skyactiv-d|bluetec", blob))
    is_petrol = bool(re.search(r"\bbenzin\b|\btsi\b|\betsi\b|\be-tsi\b|\btfsi\b|\bgdi\b|\bt-gdi\b|\bmpi\b|\bvti\b|\bthp\b|\btce\b|ecoboost|puretech|skyactiv-g|\bdig-t\b|\bpetrol\b", blob))
    ev_markers = (r"\be-tron\b|\bid\.?[3-9]\b|mach-?e|enyaq|\beqa\b|\beqb\b|\beqc\b|\beqe\b|\beqs\b|"
                  r"ioniq [56]|e-niro|kona electric|soul ev|\bleaf\b|\bzoe\b|cupra born|\bborn\b|"
                  r"\bec3\b|e-2008|e-208|e-c4|bz4x|mx-30|\bex30\b|\bex40\b|\bev\b")
    is_electric = (fuel_token in ("el", "elbil", "electric")
                   or bool(re.search(r"\belbil\b|\belektrisk\b|\belectric\b|\bbev\b|" + ev_markers, blob))) \
        and not re.search(r"hybrid", blob)

    is_plugin = bool(re.search(r"plug-?in|\bphev\b|opladelig", blob))
    is_mild = bool(re.search(r"mild-?hybrid|\bmhev\b|48v|48 v|mild hybrid|ehybrid mild", blob))
    is_hybrid = bool(re.search(r"hybrid|\bhev\b|\bhsd\b|e:hev|self-?charging|full hybrid|fuld hybrid", blob))

    # Grunddrivmiddel
    if is_electric:
        fuel = "el"
    elif is_diesel and not is_petrol:
        fuel = "diesel"
    elif is_petrol:
        fuel = "benzin"
    elif is_diesel:
        fuel = "diesel"
    else:
        fuel = "ukendt"

    # Hybridtype
    hybrid_type = ""
    if is_plugin:
        hybrid_type = "PHEV"
    elif is_diesel and is_hybrid:
        hybrid_type = "diesel-hybrid"
    elif is_mild:
        hybrid_type = "MHEV"
    elif is_hybrid:
        hybrid_type = "HEV"

    # En benzin-/mildhybrid uden tydelig motormarkoer antages at vaere benzin
    # (diesel- og plug-in-hybrider er allerede udskilt ovenfor).
    if fuel == "ukendt" and hybrid_type in ("HEV", "MHEV"):
        fuel = "benzin"

    labels = {
        "benzin": "Benzin",
        "diesel": "Diesel",
        "el": "El",
        "ukendt": "Ukendt drivmiddel",
    }
    label = labels.get(fuel, "Ukendt")
    if hybrid_type == "HEV":
        label = "Benzin-hybrid (HEV)" if fuel == "benzin" else f"{label} hybrid"
    elif hybrid_type == "MHEV":
        label = f"{label} mildhybrid"
    elif hybrid_type == "PHEV":
        label = f"{label} plug-in hybrid"
    elif hybrid_type == "diesel-hybrid":
        label = "Diesel-hybrid"

    return {"fuel": fuel, "hybrid_type": hybrid_type, "label": label}


# --------------------------------------------------------------------------- #
# Gearkasse
# --------------------------------------------------------------------------- #
def classify_gearbox(make: str, model: str, engine: str, gearbox_name: str,
                     year: Optional[int], text: str,
                     knowledge: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Klassificer gearkassens konstruktionstype ud fra redigerbar viden.

    En gearkasse maa ALDRIG klassificeres alene ud fra ordet 'automatgear'.
    Reglerne matches fra mest til mindst specifik (maerke+model+motor+aar).

    Args:
        make: Bilmaerke.
        model: Model.
        engine: Motorbetegnelse/variant.
        gearbox_name: Bilbasens gearkassenavn.
        year: Modelaar.
        text: Fritekst (beskrivelse) til yderligere moenstermatch.
        knowledge: Gearkasseviden; indlaeses hvis udeladt.

    Returns:
        Dict med type, clutch, label, risks, checkpoints, sources, confidence,
        og towing_rating (score + note) til brug i scoringen.
    """
    knowledge = knowledge or load_gearbox_knowledge()
    rules: List[Dict[str, Any]] = knowledge.get("rules", [])
    type_labels: Dict[str, str] = knowledge.get("type_labels", {})
    towing: Dict[str, Any] = knowledge.get("type_towing_rating", {})

    # Normaliser og goer komma-decimaler ('1,5') til punktum ('1.5'),
    # saa motorbaserede regler kan matche uanset skrivemaade.
    hay = _norm(f"{gearbox_name} {engine} {text}").replace(",", ".")
    make_n = normalize_make(make)
    model_n = _norm(model)
    engine_n = _norm(engine).replace(",", ".")

    best: Optional[Dict[str, Any]] = None
    best_specificity = -1

    for rule in rules:
        if _norm(rule.get("make", "")) != make_n:
            continue
        specificity = 0
        if rule.get("model"):
            if _norm(rule["model"]) not in model_n:
                continue
            specificity += 2
        if rule.get("engine"):
            if _norm(rule["engine"]) not in engine_n and _norm(rule["engine"]) not in hay:
                continue
            specificity += 2
        yf, yt = rule.get("year_from"), rule.get("year_to")
        if year is not None:
            if yf and year < yf:
                continue
            if yt and year > yt:
                continue
            if yf or yt:
                specificity += 1
        patterns: List[str] = rule.get("name_patterns", [])
        if patterns:
            if not any(re.search(p, hay) for p in patterns):
                continue
            specificity += 1
        if specificity > best_specificity:
            best_specificity = specificity
            best = rule

    if best is None:
        gtype = "unknown"
        rating = towing.get("unknown", {"score": 0.5, "note": ""})
        return {
            "type": gtype,
            "clutch": "unknown",
            "label": type_labels.get(gtype, "Ukendt"),
            "risks": ["Gearkassetype er uafklaret og maa ikke antages ud fra markedsnavnet"],
            "checkpoints": ["Bekraeft praecis transmissionstype paa registreringsattest eller ved prVe"],
            "sources": [],
            "confidence": "low",
            "towing_rating": rating,
        }

    gtype = best.get("type", "unknown")
    rating = towing.get(gtype, {"score": 0.5, "note": ""})
    return {
        "type": gtype,
        "clutch": best.get("clutch", "unknown"),
        "label": type_labels.get(gtype, gtype),
        "risks": list(best.get("risks", [])),
        "checkpoints": list(best.get("checkpoints", [])),
        "sources": list(best.get("sources", [])),
        "confidence": best.get("confidence", "low"),
        "towing_rating": rating,
    }


# --------------------------------------------------------------------------- #
# Anhaengerstabilisering
# --------------------------------------------------------------------------- #
def assess_trailer_stability(make: str, equipment: Iterable[str], description: str,
                             has_tow_bar: bool,
                             knowledge: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Vurder status for anhaengerstabilisering (ikke det samme som Trailer Assist).

    Antager ALDRIG at stabilisering er aktiv, blot fordi bilen har traek.

    Args:
        make: Bilmaerke (til modelviden).
        equipment: Liste af udstyrspunkter fra annoncen.
        description: Annoncebeskrivelse.
        has_tow_bar: Om bilen har anhaengertraek.
        knowledge: Trailer-viden; indlaeses hvis udeladt.

    Returns:
        Dict med status, status_label, note, has_trailer_assist (bool),
        checkpoints og evidence.
    """
    knowledge = knowledge or load_trailer_knowledge()
    statuses: Dict[str, str] = knowledge.get("statuses", {})
    terms: List[str] = knowledge.get("search_terms", [])
    assist_terms: List[str] = knowledge.get("trailer_assist_terms", [])
    checkpoints: List[str] = knowledge.get("checkpoints", [])

    blob = _norm(" ".join(list(equipment)) + " " + (description or ""))

    matched_terms = [t for t in terms if _norm(t) in blob]
    has_assist = any(_norm(t) in blob for t in assist_terms)

    status = "not_found"
    note = ""
    if matched_terms:
        status = "seller_claim"
        note = "Naevnt i annoncen: " + ", ".join(matched_terms) + ". Verificer aktivering og korrekt kodet trailermodul."
    else:
        for md in knowledge.get("model_defaults", []):
            if _norm(md.get("make", "")) == normalize_make(make):
                status = md.get("status", "documented_model")
                note = md.get("note", "")
                break

    if status == "not_found" and not matched_terms:
        note = "Ingen omtale af anhaengerstabilisering fundet i annoncen."

    return {
        "status": status,
        "status_label": statuses.get(status, "Ukendt"),
        "note": note,
        "has_trailer_assist": has_assist,
        "checkpoints": checkpoints,
        "evidence": matched_terms,
    }


# --------------------------------------------------------------------------- #
# Salgsform
# --------------------------------------------------------------------------- #
def classify_sale_type(dealer: str, text: str) -> Dict[str, Any]:
    """Klassificer salgsform (forhandler, leasing, engros/CVR, privat, uklar).

    Args:
        dealer: Forhandlernavn.
        text: Fritekst med salgsvilkaar.

    Returns:
        Dict med sale_type og label.
    """
    blob = _norm(f"{dealer} {text}")
    if re.search(r"leasing|leaset|privatleasing|erhvervsleasing", blob):
        return {"sale_type": "leasing", "label": "Leasing"}
    if re.search(r"engros|wholesale|eksport|kun eksport", blob):
        return {"sale_type": "engros", "label": "Engros/eksport"}
    if re.search(r"\bcvr\b|momsfri handel mellem virksomheder|kun til erhverv", blob):
        return {"sale_type": "cvr", "label": "CVR/erhvervssalg"}
    if re.search(r"\bprivat\b|privatsalg", blob) and not re.search(r"forhandler|bilhus|auto|automobil", blob):
        return {"sale_type": "privat", "label": "Privatsalg"}
    if re.search(r"forhandler|bilhus|automobil|autohuset|\bauto\b|bilcenter|bilernes", blob):
        return {"sale_type": "forhandler", "label": "Forhandler"}
    if dealer and _norm(dealer):
        return {"sale_type": "forhandler", "label": "Forhandler"}
    return {"sale_type": "uklar", "label": "Uklar salgsform"}


# --------------------------------------------------------------------------- #
# Udstyrsdetektering
# --------------------------------------------------------------------------- #
EQUIPMENT_PATTERNS: List[Tuple[str, str]] = [
    ("adaptiv fartpilot", r"adaptiv fartpilot|adaptive cruise|acc\b|adaptiv cruise"),
    ("bakkamera", r"bakkamera|360 kamera|360-kamera|parkeringskamera|reversing camera"),
    ("parkeringssensorer for", r"parkeringssensor.*for|p-sensor.*for|sensorer for og bag|parkeringssensorer for og bag"),
    ("parkeringssensorer bag", r"parkeringssensor.*bag|p-sensor.*bag|sensorer for og bag|parkeringssensorer for og bag"),
    ("blindvinkelassistent", r"blindvinkel|blind spot|bsm|blis"),
    ("vognbaneassistent", r"vognbane|lane assist|lane keep|lka|lane departure"),
    ("led-forlygter", r"led-forlygter|led forlygter|full led|led lygter|matrix led"),
    ("apple carplay", r"carplay|apple car play"),
    ("android auto", r"android auto"),
    ("saedevarme", r"saedevarme|opvarmede saeder|saede varme"),
    ("anhaengertraek", r"anhaengertraek|traekkrog|traek\b|tow bar|kroge"),
    ("13-polet", r"13-?polet|13 polet"),
    ("anhaengerstabilisering", r"anhaengerstabiliser|trailer stability|trailer sway|tsa\b|tsc\b"),
    ("navigation", r"navigation|nav\b|gps"),
    ("klimaanlaeg", r"klima|aircondition|automatisk klima|2-zone|3-zone"),
    ("keyless", r"keyless|noeglefri"),
]


def detect_equipment(equipment: Iterable[str], description: str) -> List[str]:
    """Udled normaliserede udstyrsnoegler fra annoncens udstyrsliste og beskrivelse.

    Args:
        equipment: Raa udstyrspunkter.
        description: Annoncebeskrivelse.

    Returns:
        Sorteret liste af fundne udstyrsnoegler (fx 'adaptiv fartpilot').
    """
    blob = _norm(" ".join(list(equipment)) + " " + (description or ""))
    found = []
    for key, pattern in EQUIPMENT_PATTERNS:
        if re.search(pattern, blob):
            found.append(key)
    return sorted(set(found))


# --------------------------------------------------------------------------- #
# Hoved-normalisering
# --------------------------------------------------------------------------- #
def normalize_car(raw: Dict[str, Any],
                  gearbox_knowledge: Optional[Dict[str, Any]] = None,
                  trailer_knowledge: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Normaliser en raa bil-dict til det endelige, berigede format.

    Tilfoejer normaliserede felter (drivmiddel, gearkassetype, udstyr,
    anhaengerstabilisering, salgsform) samt provenance for de usikre vaegtfelter.

    Args:
        raw: Raa bildata fra scraperen eller manuel import.
        gearbox_knowledge: Valgfri forudindlaest gearkasseviden.
        trailer_knowledge: Valgfri forudindlaest trailer-viden.

    Returns:
        En ny dict med alle oprindelige felter plus normaliserede felter.
    """
    gearbox_knowledge = gearbox_knowledge or load_gearbox_knowledge()
    trailer_knowledge = trailer_knowledge or load_trailer_knowledge()

    car = dict(raw)

    make = raw.get("make", "") or ""
    model = raw.get("model", "") or ""
    variant = raw.get("variant", "") or ""
    description = raw.get("description", "") or ""
    equipment_raw = raw.get("equipment", []) or []
    context_text = f"{variant} {description}"

    # Numeriske felter
    car["model_year"] = _to_int(raw.get("model_year"))
    car["mileage_km"] = _to_int(raw.get("mileage_km"))
    car["price"] = _to_int(raw.get("price"))
    car["hp"] = _to_int(raw.get("hp"))
    car["torque_nm"] = _to_int(raw.get("torque_nm"))
    car["gears"] = _to_int(raw.get("gears"))
    car["engine_size_l"] = _to_float(raw.get("engine_size_l"))
    car["co2"] = _to_int(raw.get("co2"))
    car["periodic_tax"] = _to_int(raw.get("periodic_tax"))
    car["trunk_liters"] = _to_int(raw.get("trunk_liters"))
    car["wltp_consumption"] = _to_float(raw.get("wltp_consumption"))

    # Vaegte med provenance
    kerb = _to_int(raw.get("kerb_weight_kg"))
    tow = _to_int(raw.get("tow_capacity_kg"))
    total = _to_int(raw.get("total_weight_kg"))
    train = _to_int(raw.get("train_weight_kg"))
    nose = _to_int(raw.get("nose_weight_kg"))
    payload = _to_int(raw.get("payload_kg"))
    if payload is None and total is not None and kerb is not None:
        payload = total - kerb

    car["kerb_weight_kg"] = kerb
    car["tow_capacity_kg"] = tow
    car["total_weight_kg"] = total
    car["train_weight_kg"] = train
    car["nose_weight_kg"] = nose
    car["payload_kg"] = payload

    provenance: Dict[str, Any] = {}
    for name, val in (("kerb_weight_kg", kerb), ("tow_capacity_kg", tow),
                      ("total_weight_kg", total), ("train_weight_kg", train),
                      ("nose_weight_kg", nose)):
        if val is not None:
            provenance[name] = _field(val, raw.get("_source", "annonce"), "medium",
                                      original=raw.get(name))
        else:
            provenance[name] = _field(None, "mangler", "low", conflict="Skal verificeres paa registreringsattesten")
    car["field_provenance"] = provenance

    # Drivmiddel - kun ud fra eksplicit felt + model/variant (ikke beskrivelsen).
    fuel_info = classify_fuel(raw.get("fuel"), f"{model} {variant}")
    car["fuel"] = fuel_info["fuel"]
    car["hybrid_type"] = fuel_info["hybrid_type"]
    car["fuel_label"] = fuel_info["label"]

    # Gearkasse
    gearbox = classify_gearbox(make, model, variant, raw.get("gearbox_name", "") or "",
                               car["model_year"], description, gearbox_knowledge)
    car["gearbox_type_normalized"] = gearbox["type"]
    car["gearbox"] = gearbox

    # Udstyr
    equipment = detect_equipment(equipment_raw, description)
    # Bevar ogsaa raa udstyrsliste
    car["equipment_raw"] = list(equipment_raw)
    car["equipment"] = equipment

    has_tow_bar = ("anhaengertraek" in equipment) or (tow is not None and tow > 0)

    # Anhaengerstabilisering
    car["trailer_stability"] = assess_trailer_stability(make, equipment_raw, description,
                                                        has_tow_bar, trailer_knowledge)

    # Salgsform
    sale = classify_sale_type(raw.get("dealer", "") or "", f"{description} {raw.get('sale_type', '')}")
    car["sale_type"] = sale["sale_type"]
    car["sale_type_label"] = sale["label"]

    car["has_tow_bar"] = has_tow_bar

    return car


if __name__ == "__main__":
    # Lille selvtest med et enkelt eksempel.
    example = {
        "id": "TEST1", "make": "Toyota", "model": "RAV4", "variant": "2.5 Hybrid AWD",
        "fuel": "Hybrid (Benzin)", "gearbox_name": "Automatgear", "model_year": 2021,
        "mileage_km": "78.000 km", "price": "319.000 kr.", "tow_capacity_kg": "1650",
        "kerb_weight_kg": "1710", "description": "Adaptiv fartpilot, bakkamera, LED-forlygter, anhaengertraek.",
        "equipment": ["Adaptiv fartpilot", "Bakkamera"], "dealer": "Bilhuset Test",
    }
    import pprint
    pprint.pprint(normalize_car(example))
