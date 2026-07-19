/* parse-score.js - JavaScript-port af Bilbasen-parsing, normalisering og scoring.
   Bruges af browser-import ("Indsæt tekst"), saa data kan behandles uden Python.
   Spejler normalizer.py / scoring.py / scraper.py og bruger DE SAMME videns-filer
   (gearbox_knowledge.json, trailer_stability_knowledge.json, settings.json) som
   eneste kilde til viden og indstillinger. Kun selve algoritmen er duplikeret.

   Al funktionalitet eksponeres som det globale objekt `Pipeline`. */

'use strict';

const Pipeline = (function () {

  /* --------------------------- Tekst-hjaelpere --------------------------- */
  /** Fjern diakritiske tegn og oversaet æ/ø/å, saa tekst kan matches robust. */
  function stripAccents(t) {
    if (!t) return '';
    const rep = { 'æ': 'ae', 'ø': 'oe', 'å': 'aa', 'Æ': 'AE', 'Ø': 'OE', 'Å': 'AA' };
    t = String(t).replace(/[æøåÆØÅ]/g, c => rep[c]);
    return t.normalize('NFKD').replace(/[̀-ͯ]/g, '');
  }
  /** Normaliser tekst til lowercase uden accenter og med enkelt mellemrum. */
  function norm(t) {
    if (!t) return '';
    return stripAccents(String(t)).toLowerCase().replace(/\s+/g, ' ').trim();
  }
  /** Konverter til heltal ved at fjerne alt undtagen cifre. */
  function toInt(v) {
    if (v === null || v === undefined) return null;
    if (typeof v === 'number') return Math.trunc(v);
    const d = String(v).replace(/[^\d]/g, '');
    return d ? parseInt(d, 10) : null;
  }
  /** Konverter til kommatal (accepterer ',' og '.'). */
  function toFloat(v) {
    if (v === null || v === undefined) return null;
    if (typeof v === 'number') return v;
    const m = String(v).match(/-?\d+(?:[.,]\d+)?/);
    return m ? parseFloat(m[0].replace(',', '.')) : null;
  }

  /* --------------------------- Maerke-aliaser --------------------------- */
  const MAKE_ALIASES = {
    'vw': 'volkswagen', 'mercedes': 'mercedes-benz', 'mercedes benz': 'mercedes-benz',
    'merc': 'mercedes-benz', 'volvo cars': 'volvo', 'citroën': 'citroen', 'citroen': 'citroen'
  };
  /** Oversaet et maerkenavn til kanonisk form brugt i videns-filerne. */
  function normalizeMake(make) {
    const k = norm(make);
    return MAKE_ALIASES[k] || k;
  }

  /* --------------------------- Drivmiddel --------------------------- */
  /** Klassificer drivmiddel og hybridtype ud fra felt + fritekst. */
  function classifyFuel(rawFuel, text) {
    // VIGTIGT: braendstof udledes KUN af eksplicit felt + variant/model, ALDRIG
    // af marketing-beskrivelsen (hvor 'elektrisk'/'el-ruder' ellers forstyrrer).
    const blob = norm(`${rawFuel || ''} ${text || ''}`);
    const fuelToken = norm(rawFuel || '');
    const isDiesel = /\bdiesel\b|\btdi\b|\bhdi\b|\bdci\b|\bcdi\b|\bcrdi\b|\bbluehdi\b|skyactiv-d|bluetec/.test(blob);
    const isPetrol = /\bbenzin\b|\btsi\b|\betsi\b|\be-tsi\b|\btfsi\b|\bgdi\b|\bt-gdi\b|\bmpi\b|\bvti\b|\bthp\b|\btce\b|ecoboost|puretech|skyactiv-g|\bdig-t\b|\bpetrol\b/.test(blob);
    const evMarkers = /\be-tron\b|\bid\.?[3-9]\b|mach-?e|enyaq|\beqa\b|\beqb\b|\beqc\b|\beqe\b|\beqs\b|ioniq [56]|e-niro|kona electric|soul ev|\bleaf\b|\bzoe\b|cupra born|\bborn\b|\bec3\b|e-2008|e-208|e-c4|bz4x|mx-30|\bex30\b|\bex40\b|\bev\b/;
    const isElectric = (['el', 'elbil', 'electric'].includes(fuelToken)
      || /\belbil\b|\belektrisk\b|\belectric\b|\bbev\b/.test(blob) || evMarkers.test(blob)) && !/hybrid/.test(blob);
    const isPlugin = /plug-?in|\bphev\b|opladelig/.test(blob);
    const isMild = /mild-?hybrid|\bmhev\b|48v|48 v|mild hybrid/.test(blob);
    const isHybrid = /hybrid|\bhev\b|\bhsd\b|e:hev|self-?charging|full hybrid|fuld hybrid/.test(blob);

    let fuel;
    if (isElectric) fuel = 'el';
    else if (isDiesel && !isPetrol) fuel = 'diesel';
    else if (isPetrol) fuel = 'benzin';
    else if (isDiesel) fuel = 'diesel';
    else fuel = 'ukendt';

    let hybrid = '';
    if (isPlugin) hybrid = 'PHEV';
    else if (isDiesel && isHybrid) hybrid = 'diesel-hybrid';
    else if (isMild) hybrid = 'MHEV';
    else if (isHybrid) hybrid = 'HEV';

    // Benzin-/mildhybrid uden tydelig motormarkoer antages at vaere benzin.
    if (fuel === 'ukendt' && (hybrid === 'HEV' || hybrid === 'MHEV')) fuel = 'benzin';

    const labels = { benzin: 'Benzin', diesel: 'Diesel', el: 'El', ukendt: 'Ukendt drivmiddel' };
    let label = labels[fuel] || 'Ukendt';
    if (hybrid === 'HEV') label = fuel === 'benzin' ? 'Benzin-hybrid (HEV)' : `${label} hybrid`;
    else if (hybrid === 'MHEV') label = `${label} mildhybrid`;
    else if (hybrid === 'PHEV') label = `${label} plug-in hybrid`;
    else if (hybrid === 'diesel-hybrid') label = 'Diesel-hybrid';
    return { fuel, hybrid_type: hybrid, label };
  }

  /* --------------------------- Gearkasse --------------------------- */
  /** Klassificer gearkassens konstruktionstype ud fra redigerbar viden. */
  function classifyGearbox(make, model, engine, gearboxName, year, text, knowledge) {
    const rules = (knowledge && knowledge.rules) || [];
    const typeLabels = (knowledge && knowledge.type_labels) || {};
    const towing = (knowledge && knowledge.type_towing_rating) || {};
    const hay = norm(`${gearboxName} ${engine} ${text}`).replace(/,/g, '.');
    const makeN = normalizeMake(make);
    const modelN = norm(model);
    const engineN = norm(engine).replace(/,/g, '.');

    let best = null, bestSpec = -1;
    for (const rule of rules) {
      if (norm(rule.make || '') !== makeN) continue;
      let spec = 0;
      if (rule.model) { if (!modelN.includes(norm(rule.model))) continue; spec += 2; }
      if (rule.engine) {
        const e = norm(rule.engine);
        if (!engineN.includes(e) && !hay.includes(e)) continue;
        spec += 2;
      }
      if (year != null) {
        if (rule.year_from && year < rule.year_from) continue;
        if (rule.year_to && year > rule.year_to) continue;
        if (rule.year_from || rule.year_to) spec += 1;
      }
      const patterns = rule.name_patterns || [];
      if (patterns.length) {
        let ok = false;
        for (const p of patterns) { try { if (new RegExp(p, 'i').test(hay)) { ok = true; break; } } catch (e) { } }
        if (!ok) continue;
        spec += 1;
      }
      if (spec > bestSpec) { bestSpec = spec; best = rule; }
    }

    if (!best) {
      return {
        type: 'unknown', clutch: 'unknown', label: typeLabels.unknown || 'Ukendt',
        risks: ['Gearkassetype er uafklaret og maa ikke antages ud fra markedsnavnet'],
        checkpoints: ['Bekraeft praecis transmissionstype paa registreringsattest eller ved prVe'],
        sources: [], confidence: 'low', towing_rating: towing.unknown || { score: 0.5, note: '' }
      };
    }
    const gtype = best.type || 'unknown';
    return {
      type: gtype, clutch: best.clutch || 'unknown', label: typeLabels[gtype] || gtype,
      risks: (best.risks || []).slice(), checkpoints: (best.checkpoints || []).slice(),
      sources: (best.sources || []).slice(), confidence: best.confidence || 'low',
      towing_rating: towing[gtype] || { score: 0.5, note: '' }
    };
  }

  /* --------------------------- Anhaengerstabilisering --------------------------- */
  /** Vurder status for anhaengerstabilisering (ikke Trailer Assist). */
  function assessTrailer(make, equipment, description, knowledge) {
    const statuses = (knowledge && knowledge.statuses) || {};
    const terms = (knowledge && knowledge.search_terms) || [];
    const assistTerms = (knowledge && knowledge.trailer_assist_terms) || [];
    const checkpoints = (knowledge && knowledge.checkpoints) || [];
    const blob = norm((equipment || []).join(' ') + ' ' + (description || ''));
    const matched = terms.filter(t => blob.includes(norm(t)));
    const hasAssist = assistTerms.some(t => blob.includes(norm(t)));

    let status = 'not_found', note = '';
    if (matched.length) {
      status = 'seller_claim';
      note = 'Naevnt i annoncen: ' + matched.join(', ') + '. Verificer aktivering og korrekt kodet trailermodul.';
    } else {
      for (const md of (knowledge.model_defaults || [])) {
        if (norm(md.make || '') === normalizeMake(make)) { status = md.status || 'documented_model'; note = md.note || ''; break; }
      }
    }
    if (status === 'not_found' && !matched.length) note = 'Ingen omtale af anhaengerstabilisering fundet i annoncen.';
    return { status, status_label: statuses[status] || 'Ukendt', note, has_trailer_assist: hasAssist, checkpoints, evidence: matched };
  }

  /* --------------------------- Salgsform --------------------------- */
  /** Klassificer salgsform (forhandler, leasing, engros/CVR, privat, uklar). */
  function classifySale(dealer, text) {
    const blob = norm(`${dealer || ''} ${text || ''}`);
    if (/leasing|leaset|privatleasing|erhvervsleasing/.test(blob)) return { sale_type: 'leasing', label: 'Leasing' };
    if (/engros|wholesale|eksport|kun eksport/.test(blob)) return { sale_type: 'engros', label: 'Engros/eksport' };
    if (/\bcvr\b|momsfri handel mellem virksomheder|kun til erhverv/.test(blob)) return { sale_type: 'cvr', label: 'CVR/erhvervssalg' };
    if (/\bprivat\b|privatsalg/.test(blob) && !/forhandler|bilhus|auto|automobil/.test(blob)) return { sale_type: 'privat', label: 'Privatsalg' };
    if (/forhandler|bilhus|automobil|autohuset|\bauto\b|bilcenter|bilernes/.test(blob)) return { sale_type: 'forhandler', label: 'Forhandler' };
    if (dealer && norm(dealer)) return { sale_type: 'forhandler', label: 'Forhandler' };
    return { sale_type: 'uklar', label: 'Uklar salgsform' };
  }

  /* --------------------------- Udstyr --------------------------- */
  const EQUIPMENT_PATTERNS = [
    ['adaptiv fartpilot', /adaptiv fartpilot|adaptive cruise|acc\b|adaptiv cruise/],
    ['bakkamera', /bakkamera|360 kamera|360-kamera|parkeringskamera|reversing camera/],
    ['parkeringssensorer for', /parkeringssensor.*for|p-sensor.*for|sensorer for og bag|parkeringssensorer for og bag/],
    ['parkeringssensorer bag', /parkeringssensor.*bag|p-sensor.*bag|sensorer for og bag|parkeringssensorer for og bag/],
    ['blindvinkelassistent', /blindvinkel|blind spot|bsm|blis/],
    ['vognbaneassistent', /vognbane|lane assist|lane keep|lka|lane departure/],
    ['led-forlygter', /led-forlygter|led forlygter|full led|led lygter|matrix led/],
    ['apple carplay', /carplay|apple car play/],
    ['android auto', /android auto/],
    ['saedevarme', /saedevarme|opvarmede saeder|saede varme/],
    ['anhaengertraek', /anhaengertraek|traekkrog|traek\b|tow bar|kroge/],
    ['13-polet', /13-?polet|13 polet/],
    ['anhaengerstabilisering', /anhaengerstabiliser|trailer stability|trailer sway|tsa\b|tsc\b/],
    ['navigation', /navigation|nav\b|gps/],
    ['klimaanlaeg', /klima|aircondition|automatisk klima|2-zone|3-zone/],
    ['keyless', /keyless|noeglefri/]
  ];
  /** Udled normaliserede udstyrsnoegler fra udstyrsliste + beskrivelse. */
  function detectEquipment(equipment, description) {
    const blob = norm((equipment || []).join(' ') + ' ' + (description || ''));
    const found = [];
    for (const [key, pat] of EQUIPMENT_PATTERNS) if (pat.test(blob)) found.push(key);
    return [...new Set(found)].sort();
  }

  /* --------------------------- Normalisering --------------------------- */
  /** Byg en feltbeskrivelse med provenance. */
  function field(value, source, confidence, original, conflict) {
    return { value, source, confidence, original: original ?? null, conflict: conflict ?? null };
  }
  /** Normaliser en raa bil-dict til det berigede format (spejler normalizer.py). */
  function normalizeCar(raw, gearboxKnowledge, trailerKnowledge) {
    const car = Object.assign({}, raw);
    const make = raw.make || '', model = raw.model || '', variant = raw.variant || '';
    const description = raw.description || '';
    const equipmentRaw = raw.equipment || [];

    car.model_year = toInt(raw.model_year);
    car.mileage_km = toInt(raw.mileage_km);
    car.price = toInt(raw.price);
    car.hp = toInt(raw.hp);
    car.torque_nm = toInt(raw.torque_nm);
    car.gears = toInt(raw.gears);
    car.engine_size_l = toFloat(raw.engine_size_l);
    car.co2 = toInt(raw.co2);
    car.periodic_tax = toInt(raw.periodic_tax);
    car.trunk_liters = toInt(raw.trunk_liters);
    car.wltp_consumption = toFloat(raw.wltp_consumption);

    const kerb = toInt(raw.kerb_weight_kg), tow = toInt(raw.tow_capacity_kg);
    const total = toInt(raw.total_weight_kg), train = toInt(raw.train_weight_kg);
    const nose = toInt(raw.nose_weight_kg);
    let payload = toInt(raw.payload_kg);
    if (payload == null && total != null && kerb != null) payload = total - kerb;
    car.kerb_weight_kg = kerb; car.tow_capacity_kg = tow; car.total_weight_kg = total;
    car.train_weight_kg = train; car.nose_weight_kg = nose; car.payload_kg = payload;

    const prov = {};
    [['kerb_weight_kg', kerb], ['tow_capacity_kg', tow], ['total_weight_kg', total],
     ['train_weight_kg', train], ['nose_weight_kg', nose]].forEach(([n, v]) => {
      prov[n] = v != null ? field(v, raw._source || 'annonce', 'medium', raw[n])
        : field(null, 'mangler', 'low', null, 'Skal verificeres paa registreringsattesten');
    });
    car.field_provenance = prov;

    const fuelInfo = classifyFuel(raw.fuel, `${model} ${variant}`);
    car.fuel = fuelInfo.fuel; car.hybrid_type = fuelInfo.hybrid_type; car.fuel_label = fuelInfo.label;

    const gearbox = classifyGearbox(make, model, variant, raw.gearbox_name || '', car.model_year, description, gearboxKnowledge);
    car.gearbox_type_normalized = gearbox.type; car.gearbox = gearbox;

    const equipment = detectEquipment(equipmentRaw, description);
    car.equipment_raw = equipmentRaw.slice(); car.equipment = equipment;
    car.has_tow_bar = equipment.includes('anhaengertraek') || (tow != null && tow > 0);
    car.trailer_stability = assessTrailer(make, equipmentRaw, description, trailerKnowledge);

    const sale = classifySale(raw.dealer || '', `${description} ${raw.sale_type || ''}`);
    car.sale_type = sale.sale_type; car.sale_type_label = sale.label;
    if (raw.city) car.city = raw.city;
    return car;
  }

  /* --------------------------- Scoring --------------------------- */
  const clamp = (v, lo = 0, hi = 100) => Math.max(lo, Math.min(hi, v));
  const r1 = v => Math.round(v * 10) / 10;

  /** Valider en bil mod hard-krav og returner afvisningsgrunde. */
  function evaluateRejections(car, settings) {
    const p = settings.profile; const reasons = [];
    const fuel = car.fuel, hybrid = car.hybrid_type;
    if (fuel === 'diesel' && hybrid !== 'diesel-hybrid') reasons.push('Diesel er fravalgt');
    if (hybrid === 'diesel-hybrid') reasons.push('Dieselhybrid er fravalgt');
    if (hybrid === 'PHEV') reasons.push('Plug-in-hybrid er fravalgt');
    if (fuel === 'el') reasons.push('Elbil er som udgangspunkt fravalgt');
    const gname = (car.gearbox_name || '').toLowerCase();
    if (gname.includes('manuel') && !gname.includes('automat')) reasons.push('Manuelt gear (automatgear er et krav)');
    if (car.tow_capacity_kg != null && car.tow_capacity_kg < p.min_tow_kg) reasons.push(`Traekvaegt ${car.tow_capacity_kg} kg er under kravet paa ${p.min_tow_kg} kg`);
    if (car.model_year != null && car.model_year < p.model_year_min) reasons.push(`Modelaar ${car.model_year} er aeldre end ${p.model_year_min}`);
    if (car.mileage_km != null && car.mileage_km > p.mileage_max_km) reasons.push(`Kilometerstand ${car.mileage_km} km overstiger ${p.mileage_max_km} km`);
    if (car.price != null && car.price > p.price_max_dkk) reasons.push(`Pris ${car.price} kr. overstiger maksimum ${p.price_max_dkk} kr.`);
    if (car.sale_type === 'cvr' || car.sale_type === 'engros') reasons.push('Salgsform er CVR/engros');
    if (car.sale_type === 'leasing') reasons.push('Bilen udbydes som leasing');
    if (car.sale_type === 'uklar') reasons.push('Uklar juridisk salgsform');
    if (car.registration_tax_missing === true) reasons.push('Registreringsafgift mangler');
    return reasons;
  }

  /** Beregn vaegtforhold mellem campingvogn og bilens koereklar vaegt. */
  function computeWeightRatio(car, settings, caravanWeight) {
    const p = settings.profile, th = settings.weight_ratio_thresholds;
    const caravan = caravanWeight != null ? caravanWeight : p.caravan_weight_kg;
    const kerb = car.kerb_weight_kg;
    if (!kerb) return { ratio: null, color: 'unknown', excellent: false, caravan_weight: caravan, kerb_weight: null, note: 'Bilens koereklar vaegt er ukendt - vaegtforhold kan ikke beregnes. Verificer paa registreringsattesten.' };
    const ratio = caravan / kerb * 100;
    let color = 'green';
    if (ratio > th.yellow) color = 'red'; else if (ratio > th.green) color = 'yellow';
    return { ratio: r1(ratio), color, excellent: ratio <= th.excellent, caravan_weight: caravan, kerb_weight: kerb,
      note: `Campingvogn ${caravan} kg / bil ${kerb} kg = ${Math.round(ratio)}%. Vejledning: hoejst 90% er trygt, 90-100% kraever erfaring, over 100% frarades.` };
  }

  /** Delscore: campingvognsegnethed. */
  function scoreCaravan(car, settings, wr) {
    const p = settings.profile; const factors = []; let total = 0;
    const tow = car.tow_capacity_kg; let towPts, note;
    if (tow == null) { towPts = 12; note = 'Traekvaegt ukendt - verificer paa registreringsattesten'; }
    else if (tow < p.min_tow_kg) { towPts = 0; note = `${tow} kg er under kravet`; }
    else if (tow >= p.preferred_tow_kg) { towPts = 30; note = `${tow} kg opfylder det foretrukne niveau`; }
    else { towPts = 20 + 10 * (tow - p.min_tow_kg) / Math.max(1, p.preferred_tow_kg - p.min_tow_kg); note = `${tow} kg (mellem krav og foretrukket)`; }
    factors.push({ name: 'Traekvaegt', value: tow, points: r1(towPts), max: 30, note }); total += towPts;

    let wrPts;
    if (wr.ratio == null) { wrPts = 10; note = 'Vaegtforhold ukendt'; }
    else if (wr.color === 'green' && wr.excellent) { wrPts = 25; note = `${wr.ratio}% - fremragende stabilitet`; }
    else if (wr.color === 'green') { wrPts = 22; note = `${wr.ratio}% - trygt`; }
    else if (wr.color === 'yellow') { wrPts = 14; note = `${wr.ratio}% - kraever erfaring`; }
    else { wrPts = 4; note = `${wr.ratio}% - frarades`; }
    factors.push({ name: 'Vaegtforhold', value: wr.ratio, points: r1(wrPts), max: 25, note }); total += wrPts;

    const grating = (car.gearbox && car.gearbox.towing_rating) || { score: 0.5, note: '' };
    const gPts = 15 * (grating.score || 0.5);
    factors.push({ name: 'Gearkasse til traek', value: car.gearbox && car.gearbox.label, points: r1(gPts), max: 15, note: grating.note || '' }); total += gPts;

    const torque = car.torque_nm; let tPts;
    if (torque == null) { tPts = 5; note = 'Moment ukendt'; } else { tPts = clamp((torque - 150) / 150 * 12, 0, 12); note = `${torque} Nm`; }
    factors.push({ name: 'Moment', value: torque, points: r1(tPts), max: 12, note }); total += tPts;

    const ts = car.trailer_stability || {};
    const tsMap = { documented_car: 10, documented_model: 7, requires_module: 6, seller_claim: 6, not_found: 2, unknown: 3 };
    const tsPts = tsMap[ts.status] != null ? tsMap[ts.status] : 3;
    factors.push({ name: 'Anhaengerstabilisering', value: ts.status_label, points: r1(tsPts), max: 10, note: ts.note || '' }); total += tsPts;

    const dt = (car.drivetrain || '').toLowerCase();
    const awd = ['4wd', 'awd', 'firehjul', '4x4', '4motion', 'quattro', 'xdrive'].some(k => dt.includes(k));
    const awdPts = awd ? 5 : 2.5;
    factors.push({ name: 'Firehjulstraek (plus)', value: awd ? 'Ja' : 'Nej/ukendt', points: awdPts, max: 5, note: 'Firehjulstraek er en fordel, ikke et krav' }); total += awdPts;

    const payload = car.payload_kg; let plPts;
    if (payload == null) { plPts = 1.5; note = 'Lasteevne ukendt'; } else { plPts = clamp((payload - 400) / 300 * 3, 0, 3); note = `${payload} kg`; }
    factors.push({ name: 'Lasteevne', value: payload, points: r1(plPts), max: 3, note }); total += plPts;
    return { score: r1(clamp(total)), factors };
  }

  /** Delscore: drivlinje og gearkasse. */
  function scoreDrivetrain(car) {
    const factors = []; let total = 0;
    const gb = car.gearbox || {}; const grating = gb.towing_rating || { score: 0.5 };
    const gPts = 55 * (grating.score || 0.5);
    factors.push({ name: 'Gearkassetype', value: gb.label, points: r1(gPts), max: 55, note: grating.note || '' }); total += gPts;
    const hp = car.hp; let hpPts, note;
    if (hp == null) { hpPts = 10; note = 'Effekt ukendt'; } else { hpPts = clamp((hp - 110) / 90 * 25, 0, 25); note = `${hp} hk`; }
    factors.push({ name: 'Effekt', value: hp, points: r1(hpPts), max: 25, note }); total += hpPts;
    const conf = gb.confidence || 'low';
    const confPts = { high: 20, medium: 13, low: 6 }[conf] || 6;
    factors.push({ name: 'Sikkerhed i gearkassedata', value: conf, points: confPts, max: 20, note: 'Lav sikkerhed betyder at typen boer verificeres' }); total += confPts;
    return { score: r1(clamp(total)), factors };
  }

  /** Delscore: pris og markedsvaerdi. */
  function scorePrice(car, settings, market) {
    const p = settings.profile; const factors = []; const price = car.price;
    if (price == null) return { score: 40, factors: [{ name: 'Pris', value: null, points: 40, max: 100, note: 'Pris ukendt' }] };
    let mvPts, note;
    if (market && market.sufficient) {
      const diff = (price - market.median) / market.median * 100;
      mvPts = clamp((15 - diff) / 30 * 60, 0, 60);
      note = `${diff > 0 ? '+' : ''}${Math.round(diff)}% ift. median ${Math.round(market.median)} kr. (${market.count} sammenlignelige)`;
    } else { mvPts = 30; note = 'Utilstraekkeligt datagrundlag til markedsvurdering'; }
    factors.push({ name: 'Ift. markedet', points: r1(mvPts), max: 60, note });
    let prefPts;
    if (price <= p.price_preferred_dkk) { prefPts = 40; note = `${price} kr. er under foretrukket graense (${p.price_preferred_dkk} kr.)`; }
    else if (price <= p.price_max_dkk) { prefPts = 40 * (1 - (price - p.price_preferred_dkk) / Math.max(1, p.price_max_dkk - p.price_preferred_dkk)); note = `${price} kr. mellem foretrukket og maksimum`; }
    else { prefPts = 0; note = `${price} kr. over maksimum`; }
    factors.push({ name: 'Ift. budget', value: price, points: r1(prefPts), max: 40, note });
    return { score: r1(clamp(mvPts + prefPts)), factors };
  }

  /** Delscore: alder og kilometerstand. */
  function scoreAgeMileage(car, settings) {
    const p = settings.profile, bands = settings.mileage_bands; const factors = []; let total = 0;
    const km = car.mileage_km; let kmPts, note;
    if (km == null) { kmPts = 25; note = 'Kilometerstand ukendt'; }
    else if (km < bands.great_below) { kmPts = 60; note = `${km} km - saerligt attraktivt (under ${bands.great_below})`; }
    else if (km < bands.acceptable_below) { kmPts = 45; note = `${km} km - acceptabelt`; }
    else if (km < bands.conditional_below) { kmPts = 28; note = `${km} km - kraever god pris og dokumenteret historik`; }
    else { kmPts = 5; note = `${km} km - hoejt`; }
    factors.push({ name: 'Kilometerstand', value: km, points: r1(kmPts), max: 60, note }); total += kmPts;
    const year = car.model_year; let agePts;
    if (year == null) { agePts = 18; note = 'Modelaar ukendt'; } else { agePts = clamp(20 + Math.max(0, year - p.model_year_min) * 4, 0, 40); note = `Modelaar ${year}`; }
    factors.push({ name: 'Alder', value: year, points: r1(agePts), max: 40, note }); total += agePts;
    return { score: r1(clamp(total)), factors };
  }

  /** Delscore: sikkerhed og udstyr. */
  function scoreSafety(car, settings) {
    const priority = settings.priority_equipment; const equipment = new Set(car.equipment || []);
    const found = priority.filter(e => equipment.has(e));
    const missing = priority.filter(e => !equipment.has(e));
    const score = r1(clamp(found.length / Math.max(1, priority.length) * 100));
    return { score, factors: [{ name: 'Prioriteret udstyr fundet', value: `${found.length}/${priority.length}`, points: score, max: 100, note: found.length ? found.join(', ') : 'Intet prioriteret udstyr fundet' }], found, missing };
  }

  /** Beregn vejledende driftsoekonomi. */
  function computeEconomy(car, settings, overrides) {
    const p = Object.assign({}, settings.profile, overrides || {});
    const annualKm = p.annual_km, caravanKm = p.caravan_km_per_year || 0;
    const soloKm = Math.max(0, annualKm - caravanKm);
    const consSolo = p.expected_consumption_km_per_l, consTow = p.expected_consumption_towing_km_per_l || consSolo * 0.6;
    let fuelCost = 0;
    if (consSolo) fuelCost += soloKm / consSolo * p.fuel_price_dkk;
    if (consTow) fuelCost += caravanKm / consTow * p.fuel_price_dkk;
    const tax = car.periodic_tax || 0;
    const maintenance = settings.maintenance_dkk_per_year || 7000;
    const depreciation = (car.price || 0) * (settings.depreciation_rate_per_year || 0.11);
    const annualTotal = fuelCost + tax + maintenance + depreciation;
    return {
      fuel_cost: Math.round(fuelCost), fuel_cost_kind: 'beregnet',
      periodic_tax: tax, periodic_tax_kind: car.periodic_tax ? 'fakta' : 'skoen',
      maintenance, maintenance_kind: 'skoen',
      depreciation: Math.round(depreciation), depreciation_kind: 'skoen',
      annual_total: Math.round(annualTotal), annual_total_kind: 'beregnet',
      cost_per_km: annualKm ? r1(annualTotal / annualKm * 10) / 10 : null,
      assumptions: { annual_km: annualKm, caravan_km: caravanKm, fuel_price: p.fuel_price_dkk, consumption_solo: consSolo, consumption_towing: consTow }
    };
  }

  /** Delscore: driftsoekonomi. */
  function scoreRunningCost(car, settings, economy) {
    economy = economy || computeEconomy(car, settings);
    const annual = economy.annual_total;
    if (annual == null) return { score: 50, factors: [{ name: 'Driftsoekonomi', points: 50, max: 100, note: 'Utilstraekkelige data' }] };
    const score = r1(clamp((60000 - annual) / 35000 * 100));
    return { score, factors: [{ name: 'Samlet aarlig omkostning', value: Math.round(annual), points: score, max: 100, note: `Ca. ${Math.round(annual).toLocaleString('da-DK')} kr./aar (vejledende)` }] };
  }

  /** Lokal markedsvurdering ud fra sammenlignelige biler. */
  function assessMarket(car, allCars) {
    if (car.price == null) return { sufficient: false, count: 0, reason: 'Ingen pris' };
    const make = (car.make || '').toLowerCase(), model = (car.model || '').toLowerCase();
    const gtype = car.gearbox_type_normalized, body = (car.body_type || '').toLowerCase();
    const year = car.model_year, km = car.mileage_km;
    const prices = [];
    for (const o of allCars) {
      if (o.id === car.id) continue;
      if (o.price == null) continue;
      if ((o.make || '').toLowerCase() !== make) continue;
      if ((o.model || '').toLowerCase() !== model) continue;
      if (gtype && o.gearbox_type_normalized && o.gearbox_type_normalized !== gtype) continue;
      if (body && o.body_type && (o.body_type || '').toLowerCase() !== body) continue;
      if (year && o.model_year && Math.abs(o.model_year - year) > 2) continue;
      if (km && o.mileage_km && Math.abs(o.mileage_km - km) > 40000) continue;
      prices.push(o.price);
    }
    if (prices.length < 3) return { sufficient: false, count: prices.length, reason: 'Utilstraekkeligt datagrundlag', data_quality: 'lav' };
    prices.sort((a, b) => a - b);
    // Median som Pythons statistics.median: gennemsnit af de to midterste ved lige antal.
    const mid = Math.floor(prices.length / 2);
    const median = prices.length % 2 ? prices[mid] : (prices[mid - 1] + prices[mid]) / 2;
    const diff = car.price - median;
    return { sufficient: true, count: prices.length, median, low: prices[0], high: prices[prices.length - 1], diff_to_median: diff, diff_pct: median ? r1(diff / median * 100) : null, data_quality: prices.length >= 6 ? 'god' : 'moderat' };
  }

  /** Udled fordele, ulemper og risici. */
  function derivePCR(car, settings, wr, sub) {
    const pros = [], cons = [], risks = [];
    const tow = car.tow_capacity_kg;
    if (tow && tow >= settings.profile.preferred_tow_kg) pros.push(`Traekker ${tow} kg - opfylder det foretrukne niveau`);
    else if (tow && tow >= settings.profile.min_tow_kg) pros.push(`Traekker ${tow} kg - opfylder kravet`);
    if (wr.color === 'green') pros.push(`Godt vaegtforhold (${wr.ratio}%)`);
    else if (wr.color === 'yellow') cons.push(`Vaegtforhold ${wr.ratio}% kraever erfaring`);
    else if (wr.color === 'red') risks.push(`Vaegtforhold ${wr.ratio}% overstiger 100% - frarades`);
    const gb = car.gearbox || {};
    if (gb.type === 'torque_converter') pros.push('Klassisk momentomformer - robust til traek');
    else if (gb.type === 'ecvt_hybrid') pros.push('e-CVT hybridsystem - driftssikkert');
    else if (gb.type === 'dry_dct') risks.push('Toerkoblet DCT - mindre egnet til vedvarende tung anhaengertraek');
    else if (gb.type === 'unknown') risks.push('Gearkassetype uafklaret - skal verificeres');
    (gb.risks || []).forEach(rk => risks.push(rk));
    const km = car.mileage_km, bands = settings.mileage_bands;
    if (km != null) { if (km < bands.great_below) pros.push(`Lav kilometerstand (${km} km)`); else if (km >= bands.acceptable_below) cons.push(`Hoej kilometerstand (${km} km) - kraev dokumenteret historik`); }
    const ts = car.trailer_stability || {};
    if (ts.status === 'not_found' || ts.status === 'unknown') risks.push('Anhaengerstabilisering ikke bekraeftet - kontroller trailermodul og kodning');
    for (const wf of ['kerb_weight_kg', 'tow_capacity_kg', 'train_weight_kg', 'nose_weight_kg']) {
      if (((car.field_provenance || {})[wf] || {}).value == null) { risks.push(`${wf.replace(/_/g, ' ')} mangler - skal verificeres paa registreringsattesten`); break; }
    }
    if (sub.price && sub.price.score >= 70) pros.push('Attraktiv pris ift. markedet/budget');
    return { pros, cons, risks };
  }

  /** Beregn samlet score og alle delresultater for en normaliseret bil. */
  function scoreCar(car, settings, allCars, caravanWeight) {
    car = Object.assign({}, car);
    allCars = allCars || [];
    car.rejection_reasons = evaluateRejections(car, settings);
    car.rejected = car.rejection_reasons.length > 0;
    const wr = computeWeightRatio(car, settings, caravanWeight); car.weight_ratio = wr;
    const market = assessMarket(car, allCars); car.market = market;
    const economy = computeEconomy(car, settings); car.economy = economy;
    const sub = {
      caravan: scoreCaravan(car, settings, wr),
      drivetrain: scoreDrivetrain(car),
      price: scorePrice(car, settings, market),
      age_mileage: scoreAgeMileage(car, settings),
      safety_equipment: scoreSafety(car, settings),
      running_cost: scoreRunningCost(car, settings, economy)
    };
    car.subscores = sub;
    const w = settings.weights;
    const total = sub.caravan.score * w.caravan + sub.drivetrain.score * w.drivetrain + sub.price.score * w.price
      + sub.age_mileage.score * w.age_mileage + sub.safety_equipment.score * w.safety_equipment + sub.running_cost.score * w.running_cost;
    car.score = r1(clamp(total));
    car.caravan_score = sub.caravan.score;
    const pcr = derivePCR(car, settings, wr, sub);
    car.pros = pcr.pros; car.cons = pcr.cons; car.risks = pcr.risks;
    return car;
  }

  /* --------------------------- Kopieret tekst --------------------------- */
  const TWO_WORD_MAKES = new Set(['land rover', 'alfa romeo', 'aston martin', 'great wall', 'mercedes benz']);
  const FUEL_WORDS = new Set(['benzin', 'diesel', 'el', 'elbil', 'hybrid', 'plug-in hybrid', 'mild hybrid', 'hybrid (benzin)', 'hybrid (diesel)']);

  /** Lav en URL-slug til Bilbasen-stier. */
  function slugify(v) {
    v = String(v).toLowerCase();
    const rep = { 'æ': 'ae', 'ø': 'oe', 'å': 'aa', 'é': 'e', 'ü': 'u', 'ö': 'o', 'ä': 'a' };
    v = v.replace(/[æøåéüöä]/g, c => rep[c]);
    return v.replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '');
  }
  /** Byg et Bilbasen model-landingslink til at genfinde en annonce. */
  function bilbasenModelUrl(make, model) {
    const makeSlug = slugify(make);
    const modelClean = String(model).replace(/\b(i{1,3}|iv|v|vi{0,3}|ix|x)\b/gi, ' ');
    const modelSlug = slugify(modelClean);
    const base = `https://www.bilbasen.dk/brugt/bil/${makeSlug}`;
    return modelSlug ? `${base}/${modelSlug}` : base;
  }
  /** Simpel FNV-hash til et stabilt syntetisk annonce-id. */
  function hashId(s) {
    let h = 0x811c9dc5;
    for (let i = 0; i < s.length; i++) { h ^= s.charCodeAt(i); h = Math.imul(h, 0x01000193); }
    return 'bb-' + (h >>> 0).toString(16).padStart(8, '0');
  }
  /** Parse tekst kopieret direkte fra Bilbasens soegeresultater. */
  function parseCopiedText(text) {
    const lines = String(text).split(/\r?\n/).map(l => l.trim());
    const priceRe = /^[\d.]+\s*kr\.?$/, dateRe = /^(\d{1,2})\/(\d{4})$/, kmRe = /^[\d.]+\s*km$/;
    const consRe = /km\/l$/, locRe = /^[^,]+,\s*[^,]+$/;
    const cars = [];
    for (let i = 0; i < lines.length; i++) {
      if (!priceRe.test(lines[i]) || i < 2) continue;
      const variant = lines[i - 1], makeModel = lines[i - 2];
      if (!makeModel || !variant) continue;
      const low = makeModel.toLowerCase(); let make = '', model = '';
      for (const two of TWO_WORD_MAKES) { if (low.startsWith(two)) { make = makeModel.slice(0, two.length); model = makeModel.slice(two.length).trim(); break; } }
      if (!make) { const parts = makeModel.split(/ (.+)/); make = parts[0]; model = parts[1] || ''; }
      const car = { make, model, variant, price: lines[i], _source: 'kopieret-tekst', url: '', sale_type: 'Forhandler' };
      for (let j = i + 1; j < Math.min(i + 9, lines.length); j++) {
        const val = lines[j];
        if (!val || priceRe.test(val)) break;
        const md = val.match(dateRe);
        if (md && !car.first_registration) { car.first_registration = val; car.model_year = md[2]; }
        else if (kmRe.test(val) && !car.mileage_km) car.mileage_km = val;
        else if (consRe.test(val) && !car.wltp_consumption) car.wltp_consumption = val.replace('km/l', '').trim();
        else if (val.toLowerCase().includes('gear') && !car.gearbox_name) car.gearbox_name = val;
        else if (FUEL_WORDS.has(val.toLowerCase()) && !car.fuel) car.fuel = val;
        else if (locRe.test(val) && !car.dealer_address && !val.toLowerCase().includes('gear')) car.dealer_address = val;
      }
      if (car.dealer_address) car.city = car.dealer_address.split(',')[0].trim();
      car.url = bilbasenModelUrl(make, model);
      car.id = hashId(`${makeModel}|${variant}|${car.first_registration || ''}|${car.dealer_address || ''}`);
      cars.push(car);
    }
    return cars;
  }

  /** Saml tekst fra leaf-elementer i doerkendte raekkefoelge (som Pythons stripped_strings). */
  function leafTexts(el) {
    if (!el) return [];
    const out = [];
    el.querySelectorAll('*').forEach(n => { if (n.children.length === 0) { const t = n.textContent.trim(); if (t) out.push(t); } });
    if (!out.length) { const t = el.textContent.trim(); if (t) out.push(t); }
    return out;
  }
  /** Udled annonce-id fra en /brugt/bil/-URL. */
  function idFromHref(href) {
    if (!href) return null;
    let m = href.match(/\/(\d{5,})(?:[/?#]|$)/); if (m) return m[1];
    m = href.match(/[?&]id=(\d{5,})/); if (m) return m[1];
    return null;
  }
  /** Parse Bilbasens annoncekort (article.Listing_listing__*) fra kopieret HTML.
   *  Spejler scraper.parse_bilbasen_cards; haandterer flere sektioner (sider). */
  function parseListingHtml(html) {
    if (typeof DOMParser === 'undefined') return [];
    const doc = new DOMParser().parseFromString(html, 'text/html');
    const articles = doc.querySelectorAll('article[class*="Listing_listing__"]');
    const dateRe = /^\d{1,2}\/\d{4}$/, kmRe = /^[\d.]+\s*km$/;
    const cars = [];
    articles.forEach(art => {
      const link = art.querySelector('a[href*="/brugt/bil/"]');
      const href = link ? link.getAttribute('href') : '';
      const id = idFromHref(href);
      if (!id) return;
      const car = { id, url: href, _source: 'html-kort', sale_type: 'Forhandler' };
      const mm = art.querySelector('[class*="Listing_makeModel__"]');
      if (mm) {
        // Maerke+model staar i <h3>; varianten er loes tekst ved siden af.
        const h3 = mm.querySelector('h3');
        const full = mm.textContent.trim().replace(/\s+/g, ' ');
        const parts = leafTexts(mm);
        const makeModel = h3 ? h3.textContent.trim().replace(/\s+/g, ' ') : (parts[0] || full);
        let variant = h3 ? full.slice(makeModel.length).trim() : (parts[1] || '');
        const low = makeModel.toLowerCase(); let make = '', model = '';
        for (const two of TWO_WORD_MAKES) { if (low.startsWith(two)) { make = makeModel.slice(0, two.length); model = makeModel.slice(two.length).trim(); break; } }
        if (!make) { const p = makeModel.split(/ (.+)/); make = p[0]; model = p[1] || ''; }
        car.make = make; car.model = model;
        if (variant) car.variant = variant;
      }
      const price = art.querySelector('[class*="Listing_price__"]');
      if (price) car.price = price.textContent.trim();
      // Foretraek Listing_details (har ogsaa gear+braendstof), ellers Listing_properties.
      const props = art.querySelector('[class*="Listing_details__"]') || art.querySelector('[class*="Listing_properties__"]');
      if (props) leafTexts(props).forEach(t => {
        if (dateRe.test(t) && !car.first_registration) { car.first_registration = t; car.model_year = t.split('/')[1]; }
        else if (kmRe.test(t) && !car.mileage_km) car.mileage_km = t;
        else if (t.endsWith('km/l') && !car.wltp_consumption) car.wltp_consumption = t.replace('km/l', '').trim();
        else if (t.toLowerCase().includes('gear') && !car.gearbox_name) car.gearbox_name = t;
        else if (FUEL_WORDS.has(t.toLowerCase()) && !car.fuel) car.fuel = t;
      });
      const desc = art.querySelector('[class*="Listing_description__"]');
      if (desc) car.description = desc.textContent.trim();
      const loc = art.querySelector('[class*="Listing_location__"]');
      if (loc) { const a = loc.textContent.trim(); car.dealer_address = a; car.city = a.split(',')[0].trim(); }
      const img = art.querySelector('img');
      if (img) { let src = img.getAttribute('src') || ''; if (!src && img.getAttribute('srcset')) src = img.getAttribute('srcset').split(' ')[0]; if (src) car.image = src; }
      cars.push(car);
    });
    return cars;
  }
  /** Auto-detekter om indsat tekst er HTML-kort eller ren kopieret tekst. */
  function parseAny(text) {
    const head = text.slice(0, 3000).toLowerCase();
    if (head.includes('<article') || head.includes('listing_listing__') || head.trimStart().startsWith('<')) {
      const cards = parseListingHtml(text);
      if (cards.length) return cards;
    }
    return parseCopiedText(text);
  }

  /** Fuld behandling: normaliser + scor en liste af raa biler (spejler score_all). */
  function processRaw(rawList, settings, gearboxKnowledge, trailerKnowledge, existingActive, caravanWeight) {
    // Deduplikér paa annonce-id (som Pythons merge_and_track) - undgaar at en
    // dublet paavirker markedsvurderingens antal og median.
    const seen = new Set();
    rawList = rawList.filter(r => { const k = String(r.id); if (seen.has(k)) return false; seen.add(k); return true; });
    const normalized = rawList.map(r => normalizeCar(r, gearboxKnowledge, trailerKnowledge));
    const pool = (existingActive || []).slice();
    normalized.forEach(c => { if (!evaluateRejections(c, settings).length) pool.push(c); });
    return normalized.map(c => {
      const scored = scoreCar(c, settings, pool, caravanWeight);
      scored.status = 'active';
      scored.first_seen = scored.first_seen || null;
      return scored;
    });
  }

  return {
    parseCopiedText, parseListingHtml, parseAny, normalizeCar, scoreCar, processRaw,
    computeWeightRatio, classifyFuel, classifyGearbox, assessMarket, computeEconomy, _norm: norm
  };
})();

if (typeof module !== 'undefined' && module.exports) module.exports = Pipeline;
