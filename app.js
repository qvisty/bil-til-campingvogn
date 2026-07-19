/* app.js - delt logik for hele hjemmesiden "Bil til campingvogn".
   Vanilla JavaScript uden framework. Laeser JSON fra data/ og gemmer brugerdata i localStorage. */

'use strict';

/* ----------------------------------------------------------------------- *
 * localStorage-lag
 * ----------------------------------------------------------------------- */
const LS_KEY = 'biltilcamping.v1';
const SS_KEY = 'biltilcamping.session.v1';
// Kun disse felter gemmes VARIGT (localStorage). Alt andet (indsatte biler, noter,
// status, sammenligning m.m.) er kun midlertidigt for den aktuelle browsersession.
const PERSIST_KEYS = ['favorites', 'favoriteSnapshots', 'settings'];

/** Standardstruktur for brugerdata i localStorage. */
function defaultUserData() {
  return {
    favorites: {},        // id -> true
    dismissed: {},        // id -> true (fravalg)
    notes: {},            // id -> tekst
    statuses: {},         // id -> status-noegle
    dealerAnswers: {},    // id -> tekst
    offeredPrice: {},     // id -> tal
    testDriveResult: {},  // id -> tekst
    compare: [],          // liste af id'er (maks 5)
    settings: {},         // brugerens overrides af profil/oekonomi
    importedRaw: {},      // id -> raa bil importeret via "Indsaet tekst" i browseren
    favoriteSnapshots: {},// id -> fuldt bil-objekt gemt som reference ved favorit
    manualWeights: {},    // id -> {kerb_weight_kg} indtastet manuelt af brugeren
    hideBaseCars: false   // skjul medfoelgende data/cars.json (efter "Fjern alle biler")
  };
}

const Store = {
  /** Indlaes brugerdata: favoritter/indstillinger fra localStorage (varigt),
   *  resten fra sessionStorage (kun denne browsersession). */
  load() {
    const data = defaultUserData();
    try {
      const ls = JSON.parse(localStorage.getItem(LS_KEY) || '{}');
      PERSIST_KEYS.forEach(k => { if (k in ls) data[k] = ls[k]; });  // ignorér gamle ikke-varige felter
    } catch (e) { console.warn('Kunne ikke laese localStorage:', e); }
    try {
      const ss = JSON.parse(sessionStorage.getItem(SS_KEY) || '{}');
      Object.keys(ss).forEach(k => { if (!PERSIST_KEYS.includes(k)) data[k] = ss[k]; });
    } catch (e) { console.warn('Kunne ikke laese sessionStorage:', e); }
    return data;
  },
  /** Gem brugerdata opdelt: varige felter i localStorage, resten i sessionStorage. */
  save(data) {
    try {
      const persist = {};
      PERSIST_KEYS.forEach(k => { persist[k] = data[k]; });
      localStorage.setItem(LS_KEY, JSON.stringify(persist));
    } catch (e) { console.warn('Kunne ikke gemme localStorage:', e); }
    try {
      const session = {};
      Object.keys(data).forEach(k => { if (!PERSIST_KEYS.includes(k)) session[k] = data[k]; });
      sessionStorage.setItem(SS_KEY, JSON.stringify(session));
    } catch (e) { console.warn('Kunne ikke gemme sessionStorage:', e); }
  },
  /** Hent aktuelle brugerdata (cachet i State). */
  get() { return State.user; },
  /** Gem og opdater cache. */
  commit() { this.save(State.user); }
};

/* ----------------------------------------------------------------------- *
 * Global tilstand
 * ----------------------------------------------------------------------- */
const State = {
  cars: [],
  status: {},
  settings: {},
  priceHistory: {},
  gearboxKnowledge: {},
  trailerKnowledge: {},
  user: defaultUserData()
};

/* Samler-kommando til browser-consollen. Indsættes og køres på hver resultatside:
   gemmer sidens annoncekort i sessionStorage (med tidsstempel), spørger ved data
   ældre end 2 min om nulstilling, og spørger til sidst om kopiér-alt vs. næste side. */
const CONSOLE_SNIPPET = `(() => {
  const KEY='bb_collect', MAXAGE=120000, now=Date.now();
  let s=null; try { s=JSON.parse(sessionStorage.getItem(KEY)); } catch(e){}
  if (s && now - s.ts > MAXAGE) {
    if (confirm('Der ligger indsamlet data, men den er over 2 minutter gammel.\\n\\nOK = NULSTIL og start forfra\\nAnnuller = FORTSÆT (tilføj til det eksisterende)')) s=null;
  }
  if (!s) s={ ts:now, pages:0, html:[] };
  const cards=[...document.querySelectorAll('article[class*="Listing_listing__"]')].map(a=>a.outerHTML);
  if (!cards.length) { alert('Fandt ingen annoncekort på denne side. Er du på et søgeresultat?'); return; }
  s.html.push(...cards); s.pages++; s.ts=now;
  sessionStorage.setItem(KEY, JSON.stringify(s));
  const next=document.querySelector('a[data-e2e="pagination-next"]') || document.querySelector('a[rel="next"], a[aria-label*="æste"]');
  const hasNext=!!(next && next.getAttribute('href') && !next.hasAttribute('disabled') && !/disabled/i.test(next.className||''));
  const cur=(document.querySelector('[data-e2e="pagination-current"]')||{}).textContent;
  const tot=(document.querySelector('[data-e2e="pagination-total"]')||{}).textContent;
  const sideInfo=(cur&&tot)?(' (side '+cur+' af '+tot+')'):'';
  const finish=confirm('Gemt'+sideInfo+' (+'+cards.length+' biler). I alt '+s.html.length+' annoncer indsamlet.\\n\\nOK = KOPIÉR ALT til udklipsholder (jeg er færdig)\\nAnnuller = '+(hasNext?'gå til NÆSTE side (indsæt og kør koden igen der)':'DETTE ER SIDSTE SIDE – vælg OK for at kopiere alt'));
  if (finish) {
    const out='<section class="srp_results">'+s.html.join('')+'</section>';
    const done=()=>{ sessionStorage.removeItem(KEY); console.log('✓ Kopieret '+s.html.length+' annoncer til udklipsholderen. Indsæt i kopier_section.txt eller i \\'Indsæt tekst\\'.'); };
    if (typeof copy==='function') { copy(out); done(); }
    else navigator.clipboard.writeText(out).then(done, ()=>alert('Kunne ikke kopiere automatisk. Data ligger i sessionStorage[\\'bb_collect\\'].'));
  } else if (hasNext) { next.click(); console.log('→ Gik til næste side. Indsæt og kør koden igen.'); }
  else { alert('Ingen næste side fundet. Kør koden igen og vælg OK for at kopiere alt.'); }
})();`;

/* Brugerens statusser for en bil. */
const USER_STATUSES = [
  'Ny', 'Skal undersoeges', 'Favorit', 'Kontakt forhandler',
  'Proevekoersel bestilt', 'Afvist', 'Solgt eller fjernet'
];

/* ----------------------------------------------------------------------- *
 * Formattering
 * ----------------------------------------------------------------------- */
/** Formater et heltal med dansk tusindtalsseparator, eller '-' hvis tom. */
function fmtNum(n, suffix = '') {
  if (n === null || n === undefined || n === '' || Number.isNaN(Number(n))) return '–';
  return Number(n).toLocaleString('da-DK') + suffix;
}

/** Formater en pris i kr. */
function fmtPrice(n) {
  if (n === null || n === undefined || n === '') return '–';
  return Number(n).toLocaleString('da-DK') + ' kr.';
}

/** Returner CSS-klasse for en score (0-100). */
function scoreClass(score) {
  if (score >= 70) return 's-high';
  if (score >= 50) return 's-mid';
  return 's-low';
}

/** Vaerdital: score pr. 10.000 kr. (hoejere = mere egnethed for pengene).
 *  Vises ALTID sammen med scoren og risici - aldrig som eneste maal. */
function valuePerTenK(car) {
  if (!car.price || !car.score) return null;
  return Math.round(car.score / (car.price / 10000) * 10) / 10;
}

/** Har bilen registrerede risici (fx toerkoblet DCT, ukendt vaegt, hoej km)? */
function carHasWarning(car) {
  return !!(car.risks && car.risks.length);
}

/** Anslaaet ÅOP for en bil (stiger med alderen). */
function estimatedAOP(car) {
  const f = Object.assign({ base_aop: 0.079, aop_per_year_over_age: 0.006, min_age_free_years: 1, max_aop: 0.16 },
    State.settings.financing || {}, (State.user.settings && State.user.settings.financing) || {});
  const age = car.model_year ? Math.max(0, new Date().getFullYear() - car.model_year) : 6;
  const aop = f.base_aop + Math.max(0, age - f.min_age_free_years) * f.aop_per_year_over_age;
  return Math.min(aop, f.max_aop);
}

/** Anslaaet maanedlig ydelse (annuitetslaan) for en bil. Rene skoen, ikke et tilbud. */
function monthlyPayment(car) {
  if (!car.price) return null;
  const f = Object.assign({ down_payment_pct: 0.20, term_months: 60 },
    State.settings.financing || {}, (State.user.settings && State.user.settings.financing) || {});
  const principal = car.price * (1 - f.down_payment_pct);
  const r = estimatedAOP(car) / 12;
  const n = f.term_months;
  const m = r > 0 ? principal * r / (1 - Math.pow(1 + r, -n)) : principal / n;
  return Math.round(m);
}

/** Byg en laesbar bilbetegnelse. */
function carName(car) {
  return [car.make, car.model, car.variant].filter(Boolean).join(' ').trim() || ('Annonce ' + car.id);
}

/** HTML-escape en streng. */
function esc(s) {
  if (s === null || s === undefined) return '';
  return String(s).replace(/[&<>"']/g, c => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
  }[c]));
}

/** Laes query-parameter fra URL. */
function queryParam(name) {
  return new URLSearchParams(window.location.search).get(name);
}

/* ----------------------------------------------------------------------- *
 * Dataindlaesning
 * ----------------------------------------------------------------------- */
/** Hent en JSON-fil med fallback. */
async function fetchJSON(path, fallback) {
  try {
    const resp = await fetch(path, { cache: 'no-store' });
    if (!resp.ok) throw new Error('HTTP ' + resp.status);
    return await resp.json();
  } catch (e) {
    console.warn('Kunne ikke hente ' + path + ':', e.message);
    return fallback;
  }
}

/** Indlaes alle datafiler og brugerdata. */
async function loadAll() {
  State.user = Store.load();
  const [cars, status, settings, priceHistory, gbKnow, trKnow, cityCoords] = await Promise.all([
    fetchJSON('data/cars.json', []),
    fetchJSON('data/scrape_status.json', {}),
    fetchJSON('data/settings.json', {}),
    fetchJSON('data/price_history.json', {}),
    fetchJSON('data/gearbox_knowledge.json', {}),
    fetchJSON('data/trailer_stability_knowledge.json', {}),
    fetchJSON('data/city_coords.json', {})
  ]);
  // "Fjern alle biler" skjuler de medfoelgende data/cars.json (kun browser-importerede
  // og favoritter vises derefter). Kan vises igen via banneret i oversigten.
  State.cars = State.user.hideBaseCars ? [] : (Array.isArray(cars) ? cars : []);
  State.status = status || {};
  State.settings = settings || {};
  State.priceHistory = priceHistory || {};
  State.gearboxKnowledge = gbKnow || {};
  State.trailerKnowledge = trKnow || {};
  State.cityCoords = cityCoords || {};
  // Flet brugerens gemte indstillinger over profilen.
  if (State.user.settings && Object.keys(State.user.settings).length && State.settings.profile) {
    State.settings.profile = Object.assign({}, State.settings.profile, State.user.settings);
  }
  processImportedIntoCars();
  injectFavoriteSnapshots();
  applyManualWeights();
}

/** Genberegn (re-score) biler, hvor brugeren har indtastet egenvaegt manuelt,
 *  saa vaegtforhold, campingscore og samlet score afspejler den indtastede vaerdi. */
function applyManualWeights() {
  if (typeof Pipeline === 'undefined') return;
  const mw = State.user.manualWeights || {};
  if (!Object.keys(mw).length) return;
  const pool = State.cars.filter(c => c.status === 'active' && !c.rejected);
  State.cars = State.cars.map(c => (mw[c.id] && mw[c.id].kerb_weight_kg) ? rescoreCar(c, pool) : c);
}

/** Byg en gen-scoret kopi af en bil ud fra den effektive egenvaegt (manuel eller annonce).
 *  Bevarer den oprindelige annonce-egenvaegt i _baseKerb, saa "Nulstil" kan gendanne den. */
function rescoreCar(car, pool) {
  if (typeof Pipeline === 'undefined') return car;
  const baseKerb = ('_baseKerb' in car) ? car._baseKerb : (car.kerb_weight_kg || null);
  const manual = State.user.manualWeights[car.id] && State.user.manualWeights[car.id].kerb_weight_kg;
  const kerb = manual || baseKerb;
  const over = Object.assign({}, car, { kerb_weight_kg: kerb, _baseKerb: baseKerb });
  if (manual) {
    over.field_provenance = Object.assign({}, car.field_provenance, {
      kerb_weight_kg: { value: kerb, source: 'manuel indtastning', confidence: 'low',
        original: baseKerb, conflict: 'Indtastet manuelt - verificér paa registreringsattesten' }
    });
  }
  const scored = Pipeline.scoreCar(over, State.settings, pool || activeCars(), caravanWeight());
  scored._baseKerb = baseKerb;
  if (manual) scored._manualKerb = true; else delete scored._manualKerb;
  if (car._snapshot) scored._snapshot = true;
  return scored;
}

/** Gem/nulstil brugerens manuelle egenvaegt for en bil og gentegn detaljesiden. */
function setManualKerb(car, value) {
  State.user.manualWeights = State.user.manualWeights || {};
  if (value && value > 0) State.user.manualWeights[car.id] = { kerb_weight_kg: value };
  else delete State.user.manualWeights[car.id];
  Store.commit();
  rescoreCarInPlace(car);
}

/** Reager paa aendret campingvognsvaegt: gen-scor bilen og gentegn. */
function applyCaravanWeightChange(car) {
  rescoreCarInPlace(car);
}

/** Gen-scor en bil (manuel egenvaegt + aktuel vognvaegt), opdatér State og gentegn detaljesiden. */
function rescoreCarInPlace(car) {
  const root = document.getElementById('car-detail');
  let updated = car;
  if (typeof Pipeline !== 'undefined') {
    updated = rescoreCar(car, activeCars());
    const i = State.cars.findIndex(c => String(c.id) === String(car.id));
    if (i >= 0) State.cars[i] = updated;
  }
  renderCarDetail(updated, root);
}

/** Bevar favoritter paa tvaers af data-nulstillinger.
 *  Favoritter, der stadig findes i data, faar deres snapshot opdateret.
 *  Favoritter, der ikke laengere er i seneste import, vises fra det gemte snapshot,
 *  markeret som reference (bilens info er gemt, selv om annoncen er væk). */
function injectFavoriteSnapshots() {
  const snaps = State.user.favoriteSnapshots || {};
  const byId = new Map(State.cars.map(c => [String(c.id), c]));
  Object.keys(State.user.favorites || {}).forEach(id => {
    const present = byId.get(String(id));
    if (present) {
      // Opdater snapshot med den nyeste info.
      State.user.favoriteSnapshots[id] = JSON.parse(JSON.stringify(present));
    } else if (snaps[id]) {
      // Ikke i seneste data - vis fra reference-snapshot.
      const ref = Object.assign({}, snaps[id], { _snapshot: true });
      State.cars.push(ref);
    }
  });
  Store.commit();
}

/** Normaliser + scor browser-importerede biler og flet dem ind i State.cars.
 *  Bruger den delte Pipeline (parse-score.js) og de samme videns-filer som Python. */
function processImportedIntoCars() {
  const raw = Object.values(State.user.importedRaw || {});
  if (!raw.length || typeof Pipeline === 'undefined') return;
  const existingActive = State.cars.filter(c => c.status === 'active' && !c.rejected);
  const scored = Pipeline.processRaw(raw, State.settings, State.gearboxKnowledge,
    State.trailerKnowledge, existingActive, caravanWeight());
  const byId = new Map(State.cars.map(c => [String(c.id), c]));
  scored.forEach(c => byId.set(String(c.id), c));  // importerede vinder ved id-sammenfald
  State.cars = [...byId.values()];
}

/** Parse indsat tekst, tilfoej til localStorage og genindlaes siden. */
function importPastedText(text) {
  if (typeof Pipeline === 'undefined') { alert('Pipelinen (parse-score.js) kunne ikke indlaeses.'); return; }
  const parsed = Pipeline.parseAny(text);
  if (!parsed.length) {
    alert('Kunne ikke finde nogen biler i teksten. Kopiér hele søgeresultatet fra Bilbasen (enten den synlige liste eller selve resultat-elementet).');
    return;
  }
  // Nulstil: hver indsætning erstatter det tidligere importerede datasæt.
  // Favoritter bevares separat via favoriteSnapshots.
  State.user.importedRaw = {};
  parsed.forEach(c => { State.user.importedRaw[c.id] = c; });
  Store.commit();
  alert(`Importerede ${parsed.length} biler (datasættet er nulstillet). Favoritter bevares. Siden genindlæses.`);
  location.reload();
}

/** Fjern ALLE biler fra oversigten: browser-importerede ryddes, og de medfoelgende
 *  (data/cars.json) skjules. Favoritter, noter og indstillinger beholdes. */
function clearAllCars() {
  if (!confirm('Fjern alle indsatte biler fra oversigten?\n\nDine favoritter beholdes.')) return;
  State.user.importedRaw = {};
  Store.commit();
  location.reload();
}

/** Vis de medfoelgende data/cars.json-biler igen. */
function showBaseCars() {
  State.user.hideBaseCars = false;
  Store.commit();
  location.reload();
}

/* ----------------------------------------------------------------------- *
 * Afledte data-hjaelpere
 * ----------------------------------------------------------------------- */
/** Aktive, ikke-afviste biler. */
function activeCars() {
  return State.cars.filter(c => c.status === 'active' && !c.rejected);
}
/** Afviste biler (uanset status). */
function rejectedCars() {
  return State.cars.filter(c => c.rejected);
}
/** Find en bil ud fra id. */
function carById(id) {
  return State.cars.find(c => String(c.id) === String(id));
}

/** Bilens effektive egenvaegt: brugerens manuelle indtastning vinder over annoncen. */
function effectiveKerb(car) {
  const m = State.user.manualWeights && State.user.manualWeights[car.id];
  if (m && m.kerb_weight_kg) return m.kerb_weight_kg;
  if ('_baseKerb' in car) return car._baseKerb;   // oprindelig annonce-vaerdi (kan vaere null)
  return car.kerb_weight_kg || null;
}

/** Beregn vaegtforhold klient-side (til live-opdatering ved aendret vognvaegt). */
function computeWeightRatio(car, caravanWeight) {
  const th = State.settings.weight_ratio_thresholds || { excellent: 85, green: 90, yellow: 100 };
  const kerb = effectiveKerb(car);
  if (!kerb) return { ratio: null, color: 'unknown' };
  const ratio = (caravanWeight / kerb) * 100;
  let color = 'green';
  if (ratio > th.yellow) color = 'red';
  else if (ratio > th.green) color = 'yellow';
  return { ratio: Math.round(ratio * 10) / 10, color, excellent: ratio <= th.excellent };
}

/** Aktuel campingvognsvaegt (brugerindstilling eller profil-standard). */
function caravanWeight() {
  const p = State.settings.profile || {};
  return Number(State.user.settings.caravan_weight_kg || p.caravan_weight_kg || 1400);
}

/* ----------------------------------------------------------------------- *
 * localStorage-handlinger
 * ----------------------------------------------------------------------- */
function isFavorite(id) { return !!State.user.favorites[id]; }
function toggleFavorite(id) {
  if (State.user.favorites[id]) {
    delete State.user.favorites[id];
    delete State.user.favoriteSnapshots[id];  // fjern reference-kopi ved fravalg
  } else {
    State.user.favorites[id] = true;
    const car = carById(id);
    if (car) State.user.favoriteSnapshots[id] = JSON.parse(JSON.stringify(car));  // gem info til reference
  }
  Store.commit();
}
function isDismissed(id) { return !!State.user.dismissed[id]; }
function toggleDismissed(id) {
  if (State.user.dismissed[id]) delete State.user.dismissed[id];
  else State.user.dismissed[id] = true;
  Store.commit();
}
function getStatus(id) { return State.user.statuses[id] || 'Ny'; }
function setStatus(id, s) { State.user.statuses[id] = s; Store.commit(); }
function getNote(id) { return State.user.notes[id] || ''; }
function setNote(id, t) {
  if (t) State.user.notes[id] = t; else delete State.user.notes[id];
  Store.commit();
}

/** Til-/fravaelg en bil i sammenligningslisten (maks 5). */
function toggleCompare(id) {
  const arr = State.user.compare;
  const i = arr.indexOf(String(id));
  if (i >= 0) { arr.splice(i, 1); }
  else {
    if (arr.length >= 5) { alert('Du kan hoejst sammenligne fem biler.'); return false; }
    arr.push(String(id));
  }
  Store.commit();
  return true;
}
function inCompare(id) { return State.user.compare.indexOf(String(id)) >= 0; }

/* ----------------------------------------------------------------------- *
 * Fælles UI: topbar-status + eksport/import
 * ----------------------------------------------------------------------- */
/** Marker aktivt menupunkt i topbaren. */
function markActiveNav() {
  const page = document.body.dataset.page;
  document.querySelectorAll('.topbar nav a').forEach(a => {
    if (a.dataset.page === page) a.classList.add('active');
  });
}

/** Eksporter alle brugerdata (favoritter, noter osv.) som JSON-fil. */
function exportUserData() {
  const blob = new Blob([JSON.stringify(State.user, null, 2)], { type: 'application/json' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = 'bil-favoritter-backup.json';
  a.click();
  URL.revokeObjectURL(url);
}

/** Importer brugerdata fra en valgt JSON-fil. */
function importUserData(file, onDone) {
  const reader = new FileReader();
  reader.onload = () => {
    try {
      const data = JSON.parse(reader.result);
      State.user = Object.assign(defaultUserData(), data);
      Store.commit();
      alert('Brugerdata importeret.');
      if (onDone) onDone();
    } catch (e) {
      alert('Kunne ikke laese filen: ' + e.message);
    }
  };
  reader.readAsText(file);
}

/** Tilknyt knapper til "Indsaet tekst"-modalen. */
function wireImportUI() {
  const modal = document.getElementById('paste-modal');
  if (!modal) return;
  const cmd = document.getElementById('console-cmd');
  if (cmd) cmd.value = CONSOLE_SNIPPET;
  document.getElementById('copy-cmd')?.addEventListener('click', () => {
    const text = CONSOLE_SNIPPET;
    (navigator.clipboard ? navigator.clipboard.writeText(text) : Promise.reject())
      .then(() => { const b = document.getElementById('copy-cmd'); if (b) { b.textContent = 'Kopieret ✓'; setTimeout(() => b.textContent = 'Kopiér kommando', 1500); } },
        () => { if (cmd) { cmd.focus(); cmd.select(); } });
  });
  const open = () => {
    const n = Object.keys(State.user.importedRaw || {}).length;
    const st = document.getElementById('paste-status');
    if (st) st.textContent = n ? `${n} biler er allerede importeret via browseren.` : '';
    modal.classList.remove('hidden');
    document.getElementById('paste-text')?.focus();
  };
  const close = () => modal.classList.add('hidden');
  document.getElementById('paste-btn')?.addEventListener('click', open);
  document.getElementById('paste-cancel')?.addEventListener('click', close);
  modal.addEventListener('click', e => { if (e.target === modal) close(); });
  document.getElementById('paste-do')?.addEventListener('click', () => {
    const text = document.getElementById('paste-text')?.value || '';
    if (text.trim()) importPastedText(text);
  });
  document.getElementById('paste-clear')?.addEventListener('click', clearAllCars);
}

/** Tilknyt eksport/import-knapper hvis de findes paa siden. */
function wireDataButtons() {
  const exp = document.getElementById('export-btn');
  if (exp) exp.addEventListener('click', exportUserData);
  const imp = document.getElementById('import-input');
  if (imp) imp.addEventListener('change', e => {
    if (e.target.files[0]) importUserData(e.target.files[0], () => location.reload());
  });
}

/* ----------------------------------------------------------------------- *
 * Star-/kort-komponenter
 * ----------------------------------------------------------------------- */
/** Byg HTML for et favorit-stjerneikon. */
function starHTML(id) {
  return `<button class="star ${isFavorite(id) ? 'on' : ''}" data-fav="${esc(id)}" title="Favorit">★</button>`;
}

/** Byg et score-badge. */
function scoreBadge(score) {
  return `<span class="score-badge ${scoreClass(score)}">${Math.round(score)}</span>`;
}

/** Byg vaegtforholds-chip for en bil (bruger aktuel vognvaegt). */
function weightChip(car) {
  const wr = computeWeightRatio(car, caravanWeight());
  if (wr.ratio === null) return `<span class="chip unknown">vaegt ukendt</span>`;
  const label = wr.ratio + '%' + (wr.excellent ? ' ★' : '');
  return `<span class="chip ${wr.color}">${label}</span>`;
}

/* ======================================================================= *
 * SIDE: Forside (dashboard) + oversigt
 * ======================================================================= */
async function initIndex() {
  await loadAll();
  markActiveNav();
  wireDataButtons();
  wireImportUI();
  renderDashboard();
  renderScrapeStatus();
  setupOverview();
}

/** Render dashboard-noegletal og top-lister. */
function renderDashboard() {
  const active = activeCars();
  const rejected = rejectedCars();
  const st = State.status;
  const newCount = active.filter(c => {
    return st.last_run && c.first_seen === c.last_seen;
  }).length;
  const priceDrops = active.filter(c => {
    const ph = c.price_history || [];
    return ph.length >= 2 && ph[ph.length - 1].price < ph[ph.length - 2].price;
  }).length;
  const prices = active.map(c => c.price).filter(Boolean).sort((a, b) => a - b);
  const median = prices.length ? prices[Math.floor(prices.length / 2)] : null;

  setStat('stat-active', active.length);
  setStat('stat-new', st.new !== undefined ? st.new : newCount);
  setStat('stat-drops', st.price_changes !== undefined ? st.price_changes : priceDrops);
  setStat('stat-rejected', rejected.length);
  setStat('stat-median', median ? fmtPrice(median) : '–');

  const byScore = [...active].sort((a, b) => b.score - a.score);
  const byValue = [...active].filter(c => c.subscores)
    .sort((a, b) => b.subscores.price.score - a.subscores.price.score);
  const byBestValue = [...active].filter(c => valuePerTenK(c) !== null)
    .sort((a, b) => valuePerTenK(b) - valuePerTenK(a));
  const byTow = [...active].sort((a, b) => b.caravan_score - a.caravan_score);

  renderTopList('top-score', byScore.slice(0, 5), c => `Score ${Math.round(c.score)} · ${fmtPrice(c.price)}`);
  renderTopList('top-bestvalue', byBestValue.slice(0, 5), c =>
    `${valuePerTenK(c).toLocaleString('da-DK')} pr. 10.000 kr. · score ${Math.round(c.score)} · ${fmtPrice(c.price)}${carHasWarning(c) ? ' ⚠' : ''}`);
  renderTopList('top-value', byValue.slice(0, 5), c => {
    const m = c.market || {};
    return m.sufficient ? `${m.diff_pct > 0 ? '+' : ''}${m.diff_pct}% ift. median` : 'Prisscore ' + Math.round(c.subscores.price.score);
  });
  renderTopList('top-tow', byTow.slice(0, 5), c => `Camping ${Math.round(c.caravan_score)} · traek ${fmtNum(c.tow_capacity_kg, ' kg')}`);
}

/** Saet tekst paa et noegletal. */
function setStat(id, val) {
  const el = document.getElementById(id);
  if (el) el.textContent = typeof val === 'number' ? fmtNum(val) : val;
}

/** Render en top-5-liste. */
function renderTopList(elId, cars, subFn) {
  const el = document.getElementById(elId);
  if (!el) return;
  if (!cars.length) { el.innerHTML = '<li class="muted">Ingen biler endnu</li>'; return; }
  el.innerHTML = cars.map((c, i) => `
    <li>
      <span class="rank">${i + 1}</span>
      ${scoreBadge(c.score)}
      <div class="li-main">
        <div class="name"><a href="car.html?id=${esc(c.id)}">${esc(carName(c))}</a></div>
        <div class="sub">${esc(subFn(c))}</div>
      </div>
      ${weightChip(c)}
    </li>`).join('');
}

/** Render seneste scrapingstatus. */
function renderScrapeStatus() {
  const el = document.getElementById('scrape-status');
  if (!el) return;
  const st = State.status;
  if (!st || !st.last_run) {
    el.innerHTML = '<p class="muted">Ingen scraping registreret endnu. Koer <code>python scraper.py</code> eller <code>python scraper.py --fixtures</code>.</p>';
    return;
  }
  const errs = (st.errors && st.errors.length)
    ? `<div class="warn-box">Fejl under seneste koersel:<ul>${st.errors.map(e => `<li>${esc(e)}</li>`).join('')}</ul></div>`
    : '<p class="small muted">Ingen fejl registreret.</p>';
  el.innerHTML = `
    <dl class="specs">
      <dt>Seneste opdatering</dt><dd>${esc(formatDate(st.last_run))}</dd>
      <dt>Kilde</dt><dd>${esc(st.source || '–')}</dd>
      <dt>Hentede annoncer</dt><dd>${fmtNum(st.fetched)}</dd>
      <dt>Aktive biler</dt><dd>${fmtNum(st.active)}</dd>
      <dt>Nye biler</dt><dd>${fmtNum(st.new)}</dd>
      <dt>Prisaendringer</dt><dd>${fmtNum(st.price_changes)}</dd>
      <dt>Forsvundne</dt><dd>${fmtNum(st.disappeared)}</dd>
      <dt>Afviste</dt><dd>${fmtNum(st.rejected)}</dd>
    </dl>${errs}`;
}

/** Formater et ISO-tidsstempel til dansk dato/tid. */
function formatDate(iso) {
  if (!iso) return '–';
  const d = new Date(iso);
  if (isNaN(d)) return iso;
  return d.toLocaleString('da-DK', { dateStyle: 'medium', timeStyle: 'short' });
}

/* ------------------------- Oversigt (tabel/kort) ------------------------ */
const Overview = {
  view: 'table',
  sortKey: 'score',
  sortDir: -1,
  search: '',
  showRejected: false,
  onlyFavorites: false,
  hideDismissed: true,
  makeFilter: '',
  fuelFilter: '',
  gearboxFilter: '',
  yearFrom: '',
  maxMonthly: ''
};

/** Saet oversigtens kontroller op og render foerste gang. */
function setupOverview() {
  const s = document.getElementById('search');
  if (s) s.addEventListener('input', e => { Overview.search = e.target.value.toLowerCase(); renderOverview(); });

  document.getElementById('view-table')?.addEventListener('click', () => setView('table'));
  document.getElementById('view-cards')?.addEventListener('click', () => setView('cards'));

  document.getElementById('filter-make')?.addEventListener('change', e => { Overview.makeFilter = e.target.value; renderOverview(); });
  document.getElementById('filter-fuel')?.addEventListener('change', e => { Overview.fuelFilter = e.target.value; renderOverview(); });
  document.getElementById('filter-gearbox')?.addEventListener('change', e => { Overview.gearboxFilter = e.target.value; renderOverview(); });
  document.getElementById('filter-year')?.addEventListener('change', e => { Overview.yearFrom = e.target.value ? Number(e.target.value) : ''; renderOverview(); });
  document.getElementById('filter-monthly')?.addEventListener('input', e => { Overview.maxMonthly = e.target.value ? Number(e.target.value) : ''; renderOverview(); });
  document.getElementById('clear-filters')?.addEventListener('click', clearFilters);
  document.getElementById('filter-favorites')?.addEventListener('change', e => { Overview.onlyFavorites = e.target.checked; renderOverview(); });
  document.getElementById('filter-dismissed')?.addEventListener('change', e => { Overview.hideDismissed = e.target.checked; renderOverview(); });
  document.getElementById('sort-select')?.addEventListener('change', e => {
    setSort(e.target.value);
  });

  document.getElementById('clear-all-cars')?.addEventListener('click', clearAllCars);
  renderHideBaseBanner();
  populateFilterOptions();
  renderOverview();
}

/** Vis en banner naar de medfoelgende biler er skjult, med mulighed for at vise dem igen. */
function renderHideBaseBanner() {
  const el = document.getElementById('hidebase-banner');
  if (!el) return;
  if (State.user.hideBaseCars) {
    el.innerHTML = `<div class="info-box" style="display:flex;justify-content:space-between;align-items:center;gap:12px;margin-bottom:12px">
      <span>De medfølgende biler er skjult. Kun dine indsatte biler og favoritter vises.</span>
      <button id="show-base-cars" class="btn small">Vis medfølgende igen</button></div>`;
    document.getElementById('show-base-cars')?.addEventListener('click', showBaseCars);
  } else {
    el.innerHTML = '';
  }
}

/** Udfyld drivmiddel- og gearkassefiltre ud fra data. */
function populateFilterOptions() {
  const makes = [...new Set(activeCars().map(c => c.make).filter(Boolean))].sort((a, b) => a.localeCompare(b, 'da'));
  const makeSel = document.getElementById('filter-make');
  if (makeSel) makes.forEach(m => makeSel.insertAdjacentHTML('beforeend', `<option value="${esc(m)}">${esc(m)}</option>`));

  const fuels = [...new Set(activeCars().map(c => c.fuel_label).filter(Boolean))];
  const fuelSel = document.getElementById('filter-fuel');
  if (fuelSel) fuels.forEach(f => fuelSel.insertAdjacentHTML('beforeend', `<option value="${esc(f)}">${esc(f)}</option>`));

  const gLabels = { torque_converter: 'Momentomformer', ecvt_hybrid: 'e-CVT/hybrid', wet_dct: 'Vaadkoblet DCT', dry_dct: 'Toerkoblet DCT', cvt: 'CVT', amt: 'Automatiseret manuel', unknown: 'Ukendt' };
  const gears = [...new Set(activeCars().map(c => c.gearbox_type_normalized).filter(Boolean))];
  const gSel = document.getElementById('filter-gearbox');
  if (gSel) gears.forEach(g => gSel.insertAdjacentHTML('beforeend', `<option value="${esc(g)}">${esc(gLabels[g] || g)}</option>`));

  const years = [...new Set(activeCars().map(c => c.model_year).filter(Boolean))].sort((a, b) => b - a);
  const ySel = document.getElementById('filter-year');
  if (ySel) years.forEach(y => ySel.insertAdjacentHTML('beforeend', `<option value="${y}">Fra ${y}</option>`));
}

/** Standard-sorteringsretning for en kolonne (1 = stigende, -1 = faldende). */
function defaultSortDir(key) {
  // "Højere er bedre" sorteres faldende som udgangspunkt; resten stigende.
  const descKeys = ['score', 'caravan', 'tow_capacity_kg', 'model_year', 'kerb_weight_kg', 'value'];
  return descKeys.includes(key) ? -1 : 1;
}

/** Vælg sorteringskolonne. Klik på samme kolonne skifter mellem asc/desc. */
function setSort(key, { toggle = false } = {}) {
  if (toggle && Overview.sortKey === key) {
    Overview.sortDir = -Overview.sortDir;
  } else {
    Overview.sortKey = key;
    Overview.sortDir = defaultSortDir(key);
  }
  const sel = document.getElementById('sort-select');
  if (sel && sel.value !== key) sel.value = key;
  renderOverview();
}

/** Nulstil alle filtre og felter i oversigten. */
function clearFilters() {
  Object.assign(Overview, { search: '', makeFilter: '', fuelFilter: '', gearboxFilter: '', yearFrom: '', maxMonthly: '', onlyFavorites: false });
  ['search', 'filter-make', 'filter-fuel', 'filter-gearbox', 'filter-year', 'filter-monthly'].forEach(id => {
    const el = document.getElementById(id); if (el) el.value = '';
  });
  const fav = document.getElementById('filter-favorites'); if (fav) fav.checked = false;
  renderOverview();
}

/** Skift mellem tabel- og kortvisning. */
function setView(v) {
  Overview.view = v;
  document.getElementById('view-table')?.classList.toggle('active', v === 'table');
  document.getElementById('view-cards')?.classList.toggle('active', v === 'cards');
  renderOverview();
}

/** Beregn den aktuelt filtrerede og sorterede liste. */
function currentList() {
  let list = activeCars();
  if (Overview.onlyFavorites) list = list.filter(c => isFavorite(c.id));
  if (Overview.hideDismissed) list = list.filter(c => !isDismissed(c.id));
  if (Overview.makeFilter) list = list.filter(c => c.make === Overview.makeFilter);
  if (Overview.yearFrom) list = list.filter(c => c.model_year && c.model_year >= Overview.yearFrom);
  if (Overview.maxMonthly) list = list.filter(c => { const m = monthlyPayment(c); return m !== null && m <= Overview.maxMonthly; });
  if (Overview.fuelFilter) list = list.filter(c => c.fuel_label === Overview.fuelFilter);
  if (Overview.gearboxFilter) list = list.filter(c => c.gearbox_type_normalized === Overview.gearboxFilter);
  if (Overview.search) {
    const q = Overview.search;
    list = list.filter(c => carName(c).toLowerCase().includes(q) || (c.dealer || '').toLowerCase().includes(q) || carLocation(c).toLowerCase().includes(q));
  }
  const key = Overview.sortKey;
  const getVal = c => {
    switch (key) {
      case 'score': return c.score;
      case 'name': return carName(c).toLowerCase();
      case 'price': return c.price || Infinity;
      case 'mileage_km': return c.mileage_km || Infinity;
      case 'model_year': return c.model_year || 0;
      case 'tow_capacity_kg': return c.tow_capacity_kg || 0;
      case 'kerb_weight_kg': return c.kerb_weight_kg || 0;
      case 'weight_ratio': return (computeWeightRatio(c, caravanWeight()).ratio) || 999;
      case 'gearbox': return c.gearbox_type_normalized || 'zzz';
      case 'caravan': return c.caravan_score;
      case 'value': return valuePerTenK(c) || 0;
      case 'monthly': return monthlyPayment(c) || Infinity;
      default: return c.score;
    }
  };
  const dir = Overview.sortDir === 1 ? 1 : -1;  // 1 = stigende, -1 = faldende
  list.sort((a, b) => {
    const va = getVal(a), vb = getVal(b);
    if (va < vb) return -dir;
    if (va > vb) return dir;
    return 0;
  });
  return list;
}

/** Render oversigten (tabel eller kort). */
function renderOverview() {
  const list = currentList();
  const countEl = document.getElementById('overview-count');
  if (countEl) countEl.textContent = `${list.length} biler`;
  const container = document.getElementById('overview');
  if (!container) return;
  if (!list.length) {
    const noData = !activeCars().length;
    container.innerHTML = noData
      ? '<div class="empty-state">Ingen biler endnu. Tryk <strong>Indsæt tekst</strong> øverst og indsæt dit Bilbasen-søgeresultat, så vises og scores bilerne her. Dine data gemmes kun i din browser.</div>'
      : '<div class="empty-state">Ingen biler matcher filtrene. Tryk “Nulstil filtre”.</div>';
    return;
  }
  container.innerHTML = Overview.view === 'table' ? renderTable(list) : renderCards(list);
  wireCardActions(container);
}

/** Byg tabel-HTML. */
function renderTable(list) {
  const rows = list.map(c => {
    const wr = computeWeightRatio(c, caravanWeight());
    return `<tr>
      <td>${starHTML(c.id)}</td>
      <td>${scoreBadge(c.score)}</td>
      <td class="name-cell"><a href="car.html?id=${esc(c.id)}">${esc(carName(c))}</a>${c._snapshot ? ' <span class="chip info small">gemt favorit</span>' : ''}<div class="small muted">📍 ${esc(carLocation(c) || '–')}</div></td>
      <td>${fmtPrice(c.price)}</td>
      <td>${fmtNum(c.mileage_km, ' km')}</td>
      <td>${fmtNum(c.model_year)}</td>
      <td>${fmtNum(c.tow_capacity_kg, ' kg')}</td>
      <td>${fmtNum(c.kerb_weight_kg, ' kg')}</td>
      <td><span class="chip ${wr.color}">${wr.ratio === null ? '–' : wr.ratio + '%'}</span></td>
      <td class="small">${esc(gearboxLabel(c))}</td>
      <td>${Math.round(c.caravan_score)}</td>
      <td title="Score pr. 10.000 kr.${carHasWarning(c) ? ' – OBS: bilen har risici (se bilen)' : ''}">${valuePerTenK(c) !== null ? valuePerTenK(c).toLocaleString('da-DK') : '–'}${carHasWarning(c) ? ' <span class="chip yellow small" style="padding:0 5px">⚠</span>' : ''}</td>
      <td title="Anslået månedlig ydelse (annuitet, se bilen for forudsætninger)">${monthlyPayment(c) !== null ? monthlyPayment(c).toLocaleString('da-DK') + ' kr.' : '–'}</td>
      <td><button data-compare="${esc(c.id)}" class="${inCompare(c.id) ? 'toggle-btn active' : ''}" title="Til sammenligning">⇄</button></td>
    </tr>`;
  }).join('');
  const cols = [
    { label: '', key: null },
    { label: 'Score', key: 'score' },
    { label: 'Bil', key: 'name' },
    { label: 'Pris', key: 'price' },
    { label: 'Km', key: 'mileage_km' },
    { label: 'Aar', key: 'model_year' },
    { label: 'Traek', key: 'tow_capacity_kg' },
    { label: 'Egenvaegt', key: 'kerb_weight_kg' },
    { label: 'Vaegtforhold', key: 'weight_ratio' },
    { label: 'Gearkasse', key: 'gearbox' },
    { label: 'Camping', key: 'caravan' },
    { label: 'Værdi/10k', key: 'value' },
    { label: 'Ydelse/md', key: 'monthly' },
    { label: '', key: null }
  ];
  const headCells = cols.map(col => {
    if (!col.key) return `<th>${col.label}</th>`;
    const active = Overview.sortKey === col.key;
    const cls = 'sortable' + (active ? (Overview.sortDir === 1 ? ' sort-asc' : ' sort-desc') : '');
    const title = active ? 'Klik for at vende sorteringen' : 'Klik for at sortere';
    return `<th class="${cls}" data-sort="${col.key}" title="${title}">${col.label}</th>`;
  }).join('');
  return `<div class="table-wrap"><table class="cars">
    <thead><tr>${headCells}</tr></thead><tbody>${rows}</tbody></table></div>`;
}

/** Byg kort-HTML. */
function renderCards(list) {
  const cards = list.map(c => {
    const img = c.image ? `style="background-image:url('${esc(c.image)}')"` : '';
    return `<div class="car-card">
      <div class="thumb" ${img}>${c.image ? '' : 'Intet billede'}${scoreBadge(c.score)}</div>
      <div class="card-body">
        <div class="card-title"><a href="car.html?id=${esc(c.id)}">${esc(carName(c))}</a></div>
        <div class="card-sub">${esc(c.fuel_label || '')} · ${fmtNum(c.model_year)} · ${fmtNum(c.mileage_km, ' km')}</div>
        <div class="card-sub">${fmtPrice(c.price)}${monthlyPayment(c) !== null ? ' · ~' + monthlyPayment(c).toLocaleString('da-DK') + ' kr./md.' : ''}${carLocation(c) ? ' · 📍 ' + esc(carLocation(c)) : ''}</div>
        <div class="card-meta">
          ${weightChip(c)}
          <span class="chip neutral">${esc(gearboxLabel(c))}</span>
          <span class="chip info">Camping ${Math.round(c.caravan_score)}</span>
        </div>
        <div class="card-actions">
          ${starHTML(c.id)}
          <button data-compare="${esc(c.id)}" class="${inCompare(c.id) ? 'active' : ''}">Sammenlign</button>
          <button data-dismiss="${esc(c.id)}">${isDismissed(c.id) ? 'Fortryd fravalg' : 'Fravaelg'}</button>
        </div>
      </div>
    </div>`;
  }).join('');
  return `<div class="card-grid">${cards}</div>`;
}

/** Bilens lokation (by) til visning. */
function carLocation(c) {
  return c.city || c.dealer_address || c.dealer || '';
}

/** Menneskelaesbar gearkasse-etiket for en bil. */
function gearboxLabel(c) {
  return (c.gearbox && c.gearbox.label) || c.gearbox_type_normalized || 'Ukendt';
}

/** Tilknyt klik-handlere til stjerner, sammenlign- og fravalgsknapper. */
function wireCardActions(container) {
  container.querySelectorAll('[data-fav]').forEach(b => b.addEventListener('click', e => {
    toggleFavorite(e.currentTarget.dataset.fav); renderOverview();
  }));
  container.querySelectorAll('[data-compare]').forEach(b => b.addEventListener('click', e => {
    if (toggleCompare(e.currentTarget.dataset.compare)) renderOverview();
  }));
  container.querySelectorAll('[data-dismiss]').forEach(b => b.addEventListener('click', e => {
    toggleDismissed(e.currentTarget.dataset.dismiss); renderOverview();
  }));
  container.querySelectorAll('th[data-sort]').forEach(th => th.addEventListener('click', () => {
    setSort(th.dataset.sort, { toggle: true });
  }));
}

/* ======================================================================= *
 * SIDE: Afviste biler
 * ======================================================================= */
async function initRejected() {
  await loadAll();
  markActiveNav();
  const list = rejectedCars().sort((a, b) => b.score - a.score);
  const el = document.getElementById('rejected-list');
  const countEl = document.getElementById('rejected-count');
  if (countEl) countEl.textContent = `${list.length} afviste biler`;
  if (!el) return;
  if (!list.length) { el.innerHTML = '<div class="empty-state">Ingen afviste biler.</div>'; return; }
  el.innerHTML = `<div class="table-wrap"><table class="cars">
    <thead><tr><th>Bil</th><th>Pris</th><th>Drivmiddel</th><th>Gearkasse</th><th>Afvisningsgrund</th><th>Annonce</th></tr></thead>
    <tbody>${list.map(c => `<tr>
      <td class="name-cell"><a href="car.html?id=${esc(c.id)}">${esc(carName(c))}</a></td>
      <td>${fmtPrice(c.price)}</td>
      <td>${esc(c.fuel_label || '')}</td>
      <td class="small">${esc(gearboxLabel(c))}</td>
      <td>${c.rejection_reasons.map(r => `<div class="tag-rejected">${esc(r)}</div>`).join('')}</td>
      <td>${c.url ? `<a href="${esc(c.url)}" target="_blank" rel="noopener">Bilbasen</a>` : '–'}</td>
    </tr>`).join('')}</tbody></table></div>`;
}

/* ======================================================================= *
 * SIDE: Bildetalje
 * ======================================================================= */
async function initCar() {
  await loadAll();
  markActiveNav();
  const car = carById(queryParam('id'));
  const root = document.getElementById('car-detail');
  if (!car) { root.innerHTML = '<div class="empty-state">Bilen blev ikke fundet.</div>'; return; }
  renderCarDetail(car, root);
}

/** Render hele bildetaljesiden. */
function renderCarDetail(car, root) {
  const wr = computeWeightRatio(car, caravanWeight());
  const storedWr = car.weight_ratio || {};
  const img = car.image ? `style="background-image:url('${esc(car.image)}')"` : '';
  const snapshotBanner = car._snapshot
    ? `<div class="info-box"><strong>Gemt favorit:</strong> denne bil er ikke længere i det senest importerede datasæt. Informationen vises fra dit gemte snapshot og opdateres ikke længere.</div>` : '';
  const rejectedBanner = car.rejected
    ? `<div class="warn-box"><strong>Afvist:</strong> ${car.rejection_reasons.map(esc).join('; ')}</div>` : '';

  root.innerHTML = `
    ${snapshotBanner}
    ${rejectedBanner}
    <div class="detail-head">
      <div class="hero-img" ${img}>${car.image ? '' : '<span class="muted">Intet billede</span>'}</div>
      <div class="head-info">
        <h2>${esc(carName(car))}</h2>
        <div class="muted">${carLocation(car) ? '📍 ' + esc(carLocation(car)) + ' · ' : ''}${esc(car.dealer || car.sale_type_label || '')}</div>
        <div class="big-score">
          ${scoreBadge(car.score)}
          <div><strong>Samlet score ${Math.round(car.score)}/100</strong><div class="small muted">Campingegnethed ${Math.round(car.caravan_score)}/100</div></div>
        </div>
        <div class="card-meta">
          ${starButtonHTML(car.id)}
          <button id="detail-compare" class="${inCompare(car.id) ? 'toggle-btn active' : ''}">${inCompare(car.id) ? 'I sammenligning' : 'Tilfoej til sammenligning'}</button>
          ${car.url ? `<a class="btn" href="${esc(car.url)}" target="_blank" rel="noopener">Se paa Bilbasen ↗</a>` : ''}
          ${car.dealer_url ? `<a class="btn" href="${esc(car.dealer_url)}" target="_blank" rel="noopener">Forhandler ↗</a>` : ''}
        </div>
      </div>
    </div>

    <div class="grid-2">
      <div class="panel">
        <h3>Grunddata</h3>
        <dl class="specs">
          <dt>Pris</dt><dd>${fmtPrice(car.price)}</dd>
          <dt>Modelaar</dt><dd>${fmtNum(car.model_year)}</dd>
          <dt>1. registrering</dt><dd>${esc(car.first_registration || '–')}</dd>
          <dt>Kilometerstand</dt><dd>${fmtNum(car.mileage_km, ' km')}</dd>
          <dt>Drivmiddel</dt><dd>${esc(car.fuel_label || '–')}</dd>
          <dt>Motor</dt><dd>${fmtNum(car.hp, ' hk')} · ${fmtNum(car.torque_nm, ' Nm')} · ${car.engine_size_l ? car.engine_size_l + ' l' : '–'}</dd>
          <dt>Gearkasse</dt><dd>${esc(gearboxLabel(car))}${car.gears ? ' (' + car.gears + ' gear)' : ''}</dd>
          <dt>Traekhjul</dt><dd>${esc(car.drivetrain || '–')}</dd>
          <dt>Karrosseri</dt><dd>${esc(car.body_type || '–')}</dd>
          <dt>WLTP-forbrug</dt><dd>${car.wltp_consumption ? car.wltp_consumption + ' km/l' : '–'}</dd>
          <dt>CO2</dt><dd>${fmtNum(car.co2, ' g/km')}</dd>
          <dt>Periodisk afgift</dt><dd>${fmtPrice(car.periodic_tax)}</dd>
          <dt>Garanti</dt><dd>${esc(car.warranty || '–')}</dd>
        </dl>
      </div>

      <div class="panel">
        <h3>Campingvognsvurdering</h3>
        <div class="controls">
          <label>Campingvognens vaegt (kg)
            <input type="number" id="caravan-weight" value="${caravanWeight()}" min="500" max="3500" step="50" style="width:100px">
          </label>
        </div>
        <div id="weight-ratio-box">${weightRatioBox(car)}</div>
        <h3>Vaegte</h3>
        <dl class="specs">
          <dt>Traekvaegt</dt><dd>${provDD(car, 'tow_capacity_kg', ' kg')}</dd>
          <dt>Egenvaegt (koereklar)</dt><dd>${provDD(car, 'kerb_weight_kg', ' kg')}</dd>
          <dt>Totalvaegt</dt><dd>${fmtNum(car.total_weight_kg, ' kg')}</dd>
          <dt>Vogntogsvaegt</dt><dd>${provDD(car, 'train_weight_kg', ' kg')}</dd>
          <dt>Lasteevne</dt><dd>${fmtNum(car.payload_kg, ' kg')}</dd>
          <dt>Kugletryk</dt><dd>${provDD(car, 'nose_weight_kg', ' kg')}</dd>
        </dl>
        <div class="info-box small">Vaegtforholdet er en <strong>sikkerhedsvejledning</strong>, ikke et lovkrav. Verificér alle vaegte paa registreringsattesten.</div>
      </div>
    </div>

    <div class="panel">
      <h3>Delscorer</h3>
      <div class="grid-2">${renderSubscores(car)}</div>
    </div>

    <div class="grid-2">
      <div class="panel">
        <h3>Fordele</h3>
        <ul class="pill-list">${listItems(car.pros, 'pro', 'Ingen saerlige fordele registreret')}</ul>
        <h3>Ulemper</h3>
        <ul class="pill-list">${listItems(car.cons, 'con', 'Ingen registreret')}</ul>
        <h3>Risici</h3>
        <ul class="pill-list">${listItems(car.risks, 'risk', 'Ingen registreret')}</ul>
      </div>
      <div class="panel">
        <h3>Anhaengerstabilisering</h3>
        ${trailerStabilityBox(car)}
        <h3>Gearkasse</h3>
        ${gearboxBox(car)}
      </div>
    </div>

    <div class="grid-2">
      <div class="panel">
        <h3>Driftsoekonomi (vejledende)</h3>
        ${economyBox(car)}
        <h3>Finansiering (anslået)</h3>
        ${financingBox(car)}
      </div>
      <div class="panel">
        <h3>Prishistorik</h3>
        <canvas id="price-chart" height="160"></canvas>
        <div id="price-history-fallback"></div>
        <h3>Markedsvurdering</h3>
        ${marketBox(car)}
      </div>
    </div>

    <div class="panel">
      <h3>Sammenlignelige biler</h3>
      <div id="comparable-cars"></div>
    </div>

    <div class="grid-2">
      <div class="panel">
        <h3>Kontrolpunkter foer koeb</h3>
        <ul class="pill-list">${listItems(checkpoints(car), 'check', '')}</ul>
      </div>
      <div class="panel">
        <h3>Spoergsmaal til forhandleren</h3>
        <ul class="pill-list">${listItems(dealerQuestions(car), 'check', '')}</ul>
      </div>
    </div>

    <div class="panel">
      <h3>Mine noter og status</h3>
      <div class="controls">
        <label>Status
          <select id="user-status" class="status-select">
            ${USER_STATUSES.map(s => `<option ${getStatus(car.id) === s ? 'selected' : ''}>${esc(s)}</option>`).join('')}
          </select>
        </label>
        <label>Tilbudt pris (kr.)
          <input type="number" id="offered-price" value="${esc(State.user.offeredPrice[car.id] || '')}" style="width:120px">
        </label>
      </div>
      <label class="small muted">Egne noter</label>
      <textarea class="note-input" id="user-note" placeholder="Skriv dine noter her...">${esc(getNote(car.id))}</textarea>
      <label class="small muted" style="margin-top:10px;display:block">Forhandlerens svar</label>
      <textarea class="note-input" id="dealer-answers" placeholder="Notér forhandlerens svar...">${esc(State.user.dealerAnswers[car.id] || '')}</textarea>
      <label class="small muted" style="margin-top:10px;display:block">Resultat af proevekoersel</label>
      <textarea class="note-input" id="testdrive" placeholder="Notér indtryk fra proevekoersel...">${esc(State.user.testDriveResult[car.id] || '')}</textarea>
    </div>

    <div class="panel">
      <h3>Datakilder og usikkerheder</h3>
      ${provenanceBox(car)}
    </div>
  `;

  wireCarDetail(car);
  renderComparable(car);
  renderPriceChart(car);
}

/** Byg favorit-knap med tekst til detaljesiden. */
function starButtonHTML(id) {
  return `<button id="detail-fav" class="btn">${isFavorite(id) ? '★ Favorit' : '☆ Marker favorit'}</button>`;
}

/** Byg vaegtforholds-boks med felt til manuel egenvaegt. */
function weightRatioBox(car) {
  const wr = computeWeightRatio(car, caravanWeight());
  const kerb = effectiveKerb(car);
  const isManual = !!(State.user.manualWeights[car.id] && State.user.manualWeights[car.id].kerb_weight_kg);
  const marker = isManual
    ? '<span class="chip info small">indtastet</span>'
    : (car.kerb_weight_kg ? '<span class="chip neutral small">fra annoncen</span>' : '<span class="chip yellow small">ukendt i annoncen</span>');

  const inputRow = `
    <div class="controls" style="margin:2px 0 10px">
      <label>Egenvægt (kg)
        <input type="number" id="manual-kerb" value="${kerb || ''}" min="700" max="3500" step="10" placeholder="fx 1500" style="width:110px">
      </label>
      ${marker}
      ${isManual ? '<button id="clear-kerb" class="small">Nulstil</button>' : ''}
    </div>`;

  if (wr.ratio === null) {
    return inputRow + `<div class="info-box small">Bilens egenvægt står ikke i annoncen. Indtast den selv (google fx "${esc(carName(car))} egenvægt" eller se registreringsattesten) — så beregnes vægtforholdet og alle scorer opdateres. Det er en sikkerhedsvejledning, ikke et lovkrav.</div>`;
  }
  const colorText = { green: 'Trygt', yellow: 'Kraever erfaring', red: 'Frarades' }[wr.color];
  return inputRow + `
    <div style="display:flex;align-items:center;gap:14px;margin:6px 0">
      <span class="chip ${wr.color}" style="font-size:1.1rem;padding:6px 14px">${wr.ratio}%${wr.excellent ? ' ★' : ''}</span>
      <div><strong>${colorText}</strong><div class="small muted">Campingvogn ${fmtNum(caravanWeight())} kg / bil ${fmtNum(kerb)} kg</div></div>
    </div>
    <div class="small muted">Grøn ≤ 90% · gul 90–100% · rød &gt; 100%. Under 85% er saerligt stabilt.</div>`;
}

/** Byg dd-indhold med provenance-markering for et vaegtfelt. */
function provDD(car, field, suffix) {
  const prov = (car.field_provenance || {})[field] || {};
  const v = car[field];
  if (v === null || v === undefined) {
    return `<span class="chip yellow">verificér paa reg.attest</span>`;
  }
  if (prov.source && /manuel/i.test(prov.source)) {
    return fmtNum(v, suffix) + ' <span class="chip info small">indtastet</span>';
  }
  const conf = prov.confidence;
  const mark = conf === 'low' ? ' <span class="chip yellow small">usikker</span>' : '';
  return fmtNum(v, suffix) + mark;
}

/** Byg liste-elementer med klasse. */
function listItems(arr, cls, emptyText) {
  if (!arr || !arr.length) return emptyText ? `<li class="muted">${esc(emptyText)}</li>` : '';
  return arr.map(x => `<li class="${cls}">${esc(x)}</li>`).join('');
}

/** Render de seks delscorer med bars og faktorer. */
function renderSubscores(car) {
  const sub = car.subscores || {};
  const weights = State.settings.weights || {};
  const labels = {
    caravan: 'Campingvognsegnethed', drivetrain: 'Drivlinje og gearkasse', price: 'Pris og markedsvaerdi',
    age_mileage: 'Alder og kilometerstand', safety_equipment: 'Sikkerhed og udstyr', running_cost: 'Driftsoekonomi'
  };
  return Object.keys(labels).map(key => {
    const s = sub[key];
    if (!s) return '';
    const w = Math.round((weights[key] || 0) * 100);
    const factors = (s.factors || []).map(f =>
      `<li><span>${esc(f.name)}${f.value !== undefined && f.value !== null ? ' (' + esc(f.value) + ')' : ''}</span><span>${f.points}/${f.max}</span></li>`
    ).join('');
    return `<div class="subscore">
      <div class="row"><strong>${labels[key]} <span class="muted small">· vaegt ${w}%</span></strong><span>${Math.round(s.score)}/100</span></div>
      <div class="bar"><span style="width:${Math.round(s.score)}%"></span></div>
      <ul class="factor-list">${factors}</ul>
    </div>`;
  }).join('');
}

/** Byg boks om anhaengerstabilisering. */
function trailerStabilityBox(car) {
  const ts = car.trailer_stability || {};
  const chips = {
    documented_car: 'green', documented_model: 'yellow', requires_module: 'yellow',
    seller_claim: 'yellow', not_found: 'red', unknown: 'unknown'
  };
  const checkpoints = (ts.checkpoints || []).map(c => `<li class="check">${esc(c)}</li>`).join('');
  const assist = ts.has_trailer_assist
    ? `<div class="info-box small">Bemaerk: annoncen naevner "Trailer Assist" (bakhjaelp) – det er ikke det samme som anhaengerstabilisering.</div>` : '';
  return `
    <p><span class="chip ${chips[ts.status] || 'unknown'}">${esc(ts.status_label || 'Ukendt')}</span></p>
    <p class="small">${esc(ts.note || '')}</p>
    ${assist}
    <div class="small muted">Kontrolpunkter:</div>
    <ul class="pill-list">${checkpoints}</ul>`;
}

/** Byg boks om gearkasse. */
function gearboxBox(car) {
  const g = car.gearbox || {};
  const risks = (g.risks || []).map(r => `<li class="risk">${esc(r)}</li>`).join('');
  const checks = (g.checkpoints || []).map(c => `<li class="check">${esc(c)}</li>`).join('');
  const src = (g.sources && g.sources.length) ? `<div class="small muted">Kilde: ${g.sources.map(esc).join(', ')}</div>` : '';
  return `
    <p><span class="chip neutral">${esc(g.label || 'Ukendt')}</span>
       <span class="chip ${g.confidence === 'high' ? 'green' : g.confidence === 'medium' ? 'yellow' : 'red'} small">sikkerhed: ${esc(g.confidence || 'lav')}</span></p>
    <ul class="pill-list">${risks}${checks}</ul>
    ${src}`;
}

/** Byg driftsoekonomi-boks. */
function economyBox(car) {
  const e = car.economy || {};
  const kindChip = k => `<span class="chip ${k === 'fakta' ? 'green' : k === 'beregnet' ? 'info' : 'neutral'} small">${esc(k)}</span>`;
  return `
    <dl class="specs">
      <dt>Braendstof/aar</dt><dd>${fmtPrice(e.fuel_cost)} ${kindChip(e.fuel_cost_kind)}</dd>
      <dt>Periodisk afgift</dt><dd>${fmtPrice(e.periodic_tax)} ${kindChip(e.periodic_tax_kind)}</dd>
      <dt>Service/vedligehold</dt><dd>${fmtPrice(e.maintenance)} ${kindChip(e.maintenance_kind)}</dd>
      <dt>Vaerditab/aar</dt><dd>${fmtPrice(e.depreciation)} ${kindChip(e.depreciation_kind)}</dd>
      <dt><strong>Samlet pr. aar</strong></dt><dd><strong>${fmtPrice(e.annual_total)}</strong> ${kindChip(e.annual_total_kind)}</dd>
      <dt>Pris pr. km</dt><dd>${e.cost_per_km ? e.cost_per_km.toLocaleString('da-DK') + ' kr.' : '–'}</dd>
    </dl>
    <div class="small muted">Baseret paa ${fmtNum((e.assumptions || {}).annual_km)} km/aar. Grønt = fakta, blåt = beregnet, gråt = skøn.</div>`;
}

/** Byg finansierings-boks (anslaaet annuitetslaan). */
function financingBox(car) {
  const m = monthlyPayment(car);
  if (m === null) return '<p class="small muted">Ingen pris - ydelse kan ikke anslaas.</p>';
  const f = Object.assign({ down_payment_pct: 0.20, term_months: 60 },
    State.settings.financing || {}, (State.user.settings && State.user.settings.financing) || {});
  const aop = estimatedAOP(car);
  const down = Math.round(car.price * f.down_payment_pct);
  const principal = car.price - down;
  const totalPaid = m * f.term_months + down;
  return `<dl class="specs">
      <dt><strong>Anslået ydelse</strong></dt><dd><strong>${fmtNum(m)} kr./md.</strong></dd>
      <dt>Udbetaling</dt><dd>${fmtPrice(down)} (${Math.round(f.down_payment_pct * 100)}%)</dd>
      <dt>Løbetid</dt><dd>${f.term_months} mdr.</dd>
      <dt>Kreditbeløb</dt><dd>${fmtPrice(principal)}</dd>
      <dt>Anslået ÅOP</dt><dd>${(aop * 100).toLocaleString('da-DK', { maximumFractionDigits: 1 })}% <span class="muted small">(stiger med bilens alder)</span></dd>
      <dt>Samlet tilbagebetaling</dt><dd>${fmtPrice(Math.round(totalPaid))}</dd>
    </dl>
    <div class="warn-box small">Rent <strong>skøn</strong> ud fra pris, alder og standardsatser — <strong>ikke et lånetilbud</strong>. Faktisk ÅOP, gebyrer og krav afhænger af långiver og din kreditvurdering. Satser kan ændres i <code>data/settings.json</code>.</div>`;
}

/** Byg markedsvurderings-boks. */
function marketBox(car) {
  const m = car.market || {};
  if (!m.sufficient) {
    return `<div class="warn-box small">Utilstraekkeligt datagrundlag til markedsvurdering (${m.count || 0} sammenlignelige biler).</div>`;
  }
  return `<dl class="specs">
    <dt>Medianpris</dt><dd>${fmtPrice(m.median)}</dd>
    <dt>Prisinterval</dt><dd>${fmtPrice(m.low)} – ${fmtPrice(m.high)}</dd>
    <dt>Sammenlignelige</dt><dd>${fmtNum(m.count)}</dd>
    <dt>Denne bil ift. median</dt><dd><span class="chip ${m.diff_to_median <= 0 ? 'green' : 'yellow'}">${m.diff_to_median > 0 ? '+' : ''}${fmtPrice(m.diff_to_median)} (${m.diff_pct}%)</span></dd>
    <dt>Datakvalitet</dt><dd>${esc(m.data_quality)}</dd>
  </dl>`;
}

/** Byg provenance-oversigt. */
function provenanceBox(car) {
  const prov = car.field_provenance || {};
  const rows = Object.keys(prov).map(k => {
    const p = prov[k];
    return `<tr><td>${esc(k.replace(/_/g, ' '))}</td><td>${p.value === null ? '<span class="chip yellow small">mangler</span>' : esc(p.value)}</td><td>${esc(p.source || '')}</td><td>${esc(p.confidence || '')}</td><td>${esc(p.conflict || '')}</td></tr>`;
  }).join('');
  return `<div class="table-wrap"><table class="cars"><thead><tr><th>Felt</th><th>Vaerdi</th><th>Kilde</th><th>Sikkerhed</th><th>Note</th></tr></thead><tbody>${rows}</tbody></table></div>`;
}

/** Byg kontrolpunkter foer koeb. */
function checkpoints(car) {
  const base = [
    'Kontroller servicehistorik og seneste service',
    'Kontroller gearolieservice iht. serviceplan',
    'Sammenhold stelnummer med registreringsattest',
    'Verificér traekvaegt, vogntogsvaegt og kugletryk paa registreringsattesten',
    'Kontroller for originalt eller korrekt kodet trailermodul og 13-polet stik',
    'Bekraeft at anhaengerstabilisering (ESC) er aktiveret for anhaengerdrift'
  ];
  return base.concat((car.gearbox && car.gearbox.checkpoints) || [])
    .concat((car.trailer_stability && car.trailer_stability.checkpoints) || [])
    .filter((v, i, a) => a.indexOf(v) === i);
}

/** Generér relevante spoergsmaal til forhandleren. */
function dealerQuestions(car) {
  return [
    'Er der fuld servicehistorik, og hvornaar er der sidst udfoert service?',
    'Er gearolie/transmissionsservice udfoert efter forskrift?',
    'Kan jeg faa stelnummer og se registreringsattest (del 1 og 2)?',
    'Hvad er den attesterede traekvaegt, vogntogsvaegt og kugletryk?',
    'Er anhaengertraekket originalt eller eftermonteret?',
    'Er der 13-polet stik, og er trailermodulet korrekt kodet?',
    'Er anhaengerstabilisering (Trailer Stability Assist) aktiveret?',
    'Er der garanti paa motor, gearkasse og mekatronik?',
    'Har bilen haft skader, og er den synet uden anmaerkninger?',
    'Hvor mange ejere har bilen haft, og er den importeret?',
    'Foelger der sommer-, vinter- eller helaarshjul med?'
  ];
}

/** Tilknyt handlere paa detaljesiden (favorit, status, noter, vognvaegt). */
function wireCarDetail(car) {
  document.getElementById('detail-fav')?.addEventListener('click', () => {
    toggleFavorite(car.id);
    document.getElementById('detail-fav').outerHTML = starButtonHTML(car.id);
    wireCarDetail(car); // genbind efter outerHTML-udskiftning
  });
  document.getElementById('detail-compare')?.addEventListener('click', e => {
    if (toggleCompare(car.id)) {
      e.target.classList.toggle('active', inCompare(car.id));
      e.target.textContent = inCompare(car.id) ? 'I sammenligning' : 'Tilfoej til sammenligning';
    }
  });
  const cw = document.getElementById('caravan-weight');
  cw?.addEventListener('change', e => {
    const v = Number(e.target.value) || 1400;
    State.user.settings.caravan_weight_kg = v;
    Store.commit();
    // Ny vognvaegt paavirker vaegtforhold og score -> gen-scor og gentegn alt.
    applyCaravanWeightChange(car);
  });
  document.getElementById('manual-kerb')?.addEventListener('change', e => {
    const v = Number(e.target.value) || null;
    setManualKerb(car, v);
  });
  document.getElementById('clear-kerb')?.addEventListener('click', () => setManualKerb(car, null));
  document.getElementById('user-status')?.addEventListener('change', e => setStatus(car.id, e.target.value));
  document.getElementById('user-note')?.addEventListener('input', e => setNote(car.id, e.target.value));
  document.getElementById('offered-price')?.addEventListener('input', e => {
    if (e.target.value) State.user.offeredPrice[car.id] = Number(e.target.value);
    else delete State.user.offeredPrice[car.id];
    Store.commit();
  });
  document.getElementById('dealer-answers')?.addEventListener('input', e => {
    State.user.dealerAnswers[car.id] = e.target.value; Store.commit();
  });
  document.getElementById('testdrive')?.addEventListener('input', e => {
    State.user.testDriveResult[car.id] = e.target.value; Store.commit();
  });
}

/** Render sammenlignelige biler paa detaljesiden. */
function renderComparable(car) {
  const el = document.getElementById('comparable-cars');
  if (!el) return;
  const comps = activeCars().filter(o =>
    String(o.id) !== String(car.id) &&
    (o.make || '').toLowerCase() === (car.make || '').toLowerCase() &&
    (o.model || '').toLowerCase() === (car.model || '').toLowerCase()
  ).sort((a, b) => (a.price || 0) - (b.price || 0)).slice(0, 6);
  if (!comps.length) { el.innerHTML = '<p class="muted small">Ingen sammenlignelige biler i datasaettet.</p>'; return; }
  el.innerHTML = `<div class="table-wrap"><table class="cars"><thead><tr><th>Bil</th><th>Pris</th><th>Km</th><th>Aar</th><th>Score</th></tr></thead><tbody>${comps.map(c => `<tr>
    <td class="name-cell"><a href="car.html?id=${esc(c.id)}">${esc(carName(c))}</a></td>
    <td>${fmtPrice(c.price)}</td><td>${fmtNum(c.mileage_km, ' km')}</td><td>${fmtNum(c.model_year)}</td><td>${scoreBadge(c.score)}</td>
  </tr>`).join('')}</tbody></table></div>`;
}

/** Render prishistorik som graf (Chart.js) med tabel-fallback. */
function renderPriceChart(car) {
  const ph = car.price_history || [];
  const fallback = document.getElementById('price-history-fallback');
  const canvas = document.getElementById('price-chart');
  if (ph.length < 2) {
    if (canvas) canvas.style.display = 'none';
    if (fallback) fallback.innerHTML = ph.length
      ? `<p class="small muted">Kun én observeret pris: ${fmtPrice(ph[0].price)} (${formatDate(ph[0].date)}).</p>`
      : '<p class="small muted">Ingen prishistorik endnu.</p>';
    return;
  }
  if (typeof Chart === 'undefined' || !canvas) {
    if (canvas) canvas.style.display = 'none';
    if (fallback) fallback.innerHTML = `<div class="table-wrap"><table class="cars"><thead><tr><th>Dato</th><th>Pris</th></tr></thead><tbody>${ph.map(p => `<tr><td>${formatDate(p.date)}</td><td>${fmtPrice(p.price)}</td></tr>`).join('')}</tbody></table></div>`;
    return;
  }
  new Chart(canvas.getContext('2d'), {
    type: 'line',
    data: {
      labels: ph.map(p => formatDate(p.date)),
      datasets: [{ label: 'Pris (kr.)', data: ph.map(p => p.price), borderColor: '#1f6feb', backgroundColor: 'rgba(31,111,235,0.12)', tension: 0.2, fill: true }]
    },
    options: { responsive: true, plugins: { legend: { display: false } }, scales: { y: { ticks: { callback: v => v.toLocaleString('da-DK') } } } }
  });
}

/* ======================================================================= *
 * SIDE: Sammenligning
 * ======================================================================= */
async function initCompare() {
  await loadAll();
  markActiveNav();
  renderCompare();
  document.getElementById('clear-compare')?.addEventListener('click', () => {
    State.user.compare = []; Store.commit(); renderCompare();
  });
}

/** Render sammenligningstabellen. */
function renderCompare() {
  const root = document.getElementById('compare-root');
  const ids = State.user.compare;
  const cars = ids.map(carById).filter(Boolean);
  if (!cars.length) {
    root.innerHTML = '<div class="empty-state">Du har ikke valgt nogen biler til sammenligning. Gaa til oversigten og tryk "Sammenlign".</div>';
    return;
  }
  // Hver raekke: label, visning (fn) og evt. 'best' = {extract, dir} til groen markering.
  // dir: 'max' = hoejere er bedst, 'min' = lavere er bedst.
  const rows = [
    { label: 'Pris', fn: c => fmtPrice(c.price), best: { extract: c => c.price, dir: 'min' } },
    { label: 'Modelaar', fn: c => fmtNum(c.model_year), best: { extract: c => c.model_year, dir: 'max' } },
    { label: 'Kilometer', fn: c => fmtNum(c.mileage_km, ' km'), best: { extract: c => c.mileage_km, dir: 'min' } },
    { label: 'Drivmiddel', fn: c => esc(c.fuel_label) },
    { label: 'Motor (hk)', fn: c => fmtNum(c.hp), best: { extract: c => c.hp, dir: 'max' } },
    { label: 'Moment (Nm)', fn: c => fmtNum(c.torque_nm), best: { extract: c => c.torque_nm, dir: 'max' } },
    { label: 'Gearkassetype', fn: c => esc(gearboxLabel(c)), best: { extract: c => ((c.gearbox || {}).towing_rating || {}).score, dir: 'max' } },
    { label: 'Traekvaegt', fn: c => fmtNum(c.tow_capacity_kg, ' kg'), best: { extract: c => c.tow_capacity_kg, dir: 'max' } },
    { label: 'Egenvaegt', fn: c => fmtNum(c.kerb_weight_kg, ' kg'), best: { extract: c => c.kerb_weight_kg, dir: 'max' } },
    { label: 'Vaegtforhold', fn: c => { const wr = computeWeightRatio(c, caravanWeight()); return wr.ratio === null ? '–' : `<span class="chip ${wr.color}">${wr.ratio}%</span>`; }, best: { extract: c => computeWeightRatio(c, caravanWeight()).ratio, dir: 'min' } },
    { label: 'Lasteevne', fn: c => fmtNum(c.payload_kg, ' kg'), best: { extract: c => c.payload_kg, dir: 'max' } },
    { label: 'Anhaengerstabilisering', fn: c => esc((c.trailer_stability || {}).status_label) },
    { label: 'Forbrug', fn: c => c.wltp_consumption ? c.wltp_consumption + ' km/l' : '–', best: { extract: c => c.wltp_consumption, dir: 'max' } },
    { label: 'Periodisk afgift', fn: c => fmtPrice(c.periodic_tax), best: { extract: c => c.periodic_tax, dir: 'min' } },
    { label: 'Anslået ydelse/md.', fn: c => { const m = monthlyPayment(c); return m !== null ? m.toLocaleString('da-DK') + ' kr.' : '–'; }, best: { extract: c => monthlyPayment(c), dir: 'min' } },
    { label: 'Samlet aarlig omkostning', fn: c => fmtPrice((c.economy || {}).annual_total), best: { extract: c => (c.economy || {}).annual_total, dir: 'min' } },
    { label: 'Udstyr (antal prioriteret)', fn: c => { const s = c.subscores && c.subscores.safety_equipment; return s ? (s.found || []).length + '/' + ((s.found || []).length + (s.missing || []).length) : '–'; }, best: { extract: c => { const s = c.subscores && c.subscores.safety_equipment; return s ? (s.found || []).length : null; }, dir: 'max' } },
    { label: 'Garanti', fn: c => esc(c.warranty || '–') },
    { label: 'Samlet score', fn: c => scoreBadge(c.score), best: { extract: c => c.score, dir: 'max' } },
    { label: 'Værdi (score pr. 10.000 kr.)', fn: c => { const v = valuePerTenK(c); return v !== null ? v.toLocaleString('da-DK') + (carHasWarning(c) ? ' ⚠' : '') : '–'; }, best: { extract: c => valuePerTenK(c), dir: 'max' } },
    { label: 'Campingegnethed', fn: c => Math.round(c.caravan_score) + '/100', best: { extract: c => c.caravan_score, dir: 'max' } },
    { label: 'Vigtigste risiko', fn: c => esc((c.risks && c.risks[0]) || 'Ingen registreret') },
    { label: 'Anbefaling', fn: c => esc(recommendation(c)) }
  ];

  /** Find hvilke bil-indeks der har den bedste vaerdi i en raekke (kan vaere flere ved lige). */
  const bestSet = best => {
    if (!best || cars.length < 2) return new Set();
    let top = null; let idxs = [];
    cars.forEach((c, i) => {
      const v = best.extract(c);
      if (v === null || v === undefined || Number.isNaN(Number(v))) return;
      const num = Number(v);
      if (top === null || (best.dir === 'max' ? num > top : num < top)) { top = num; idxs = [i]; }
      else if (num === top) idxs.push(i);
    });
    // Undlad markering hvis alle er lige (ingen differentiering).
    return idxs.length === cars.length ? new Set() : new Set(idxs);
  };

  root.innerHTML = `<div class="table-wrap"><table class="compare">
    <thead><tr><th class="rowlabel">Bil</th>${cars.map(c => `<th><a href="car.html?id=${esc(c.id)}">${esc(carName(c))}</a><br><button data-remove="${esc(c.id)}" class="small">Fjern</button></th>`).join('')}</tr></thead>
    <tbody>${rows.map(row => {
      const winners = bestSet(row.best);
      return `<tr><th class="rowlabel">${row.label}</th>${cars.map((c, i) => `<td${winners.has(i) ? ' class="best-cell"' : ''}>${row.fn(c)}</td>`).join('')}</tr>`;
    }).join('')}</tbody>
  </table></div>`;
  root.querySelectorAll('[data-remove]').forEach(b => b.addEventListener('click', e => {
    toggleCompare(e.currentTarget.dataset.remove); renderCompare();
  }));
}

/** Giv en kort samlet anbefaling for en bil. */
function recommendation(car) {
  if (car.rejected) return 'Frarades (' + (car.rejection_reasons[0] || 'afvist') + ')';
  if (car.score >= 72) return 'Staerk kandidat';
  if (car.score >= 60) return 'God mulighed – tjek risici';
  if (car.score >= 50) return 'Mulig – kraever forbehold';
  return 'Svag ift. dit behov';
}

/* ======================================================================= *
 * SIDE: Kort
 * ======================================================================= */
let _map = null;

/** Slaa koordinater op for en by: bundlet fil, localStorage-cache, ellers null. */
function cityLatLon(city) {
  if (!city) return null;
  if (State.cityCoords[city]) return State.cityCoords[city];
  const cache = geoCache();
  return cache[city] || null;
}

/** Hent/gem localStorage-geocache (til byer importeret i browseren). */
function geoCache() {
  try { return JSON.parse(localStorage.getItem('biltilcamping.geocache') || '{}'); }
  catch (e) { return {}; }
}
function saveGeoCache(cache) {
  try { localStorage.setItem('biltilcamping.geocache', JSON.stringify(cache)); } catch (e) {}
}

async function initMap() {
  await loadAll();
  markActiveNav();
  const el = document.getElementById('map');
  const countEl = document.getElementById('map-count');
  const cars = activeCars().filter(c => c.city);

  if (typeof L === 'undefined') {
    document.getElementById('map-fallback').innerHTML =
      '<div class="warn-box">Kortet kunne ikke indlæses (Leaflet er offline). Prøv igen med internetforbindelse.</div>';
    el.style.display = 'none';
  } else {
    _map = L.map('map', { scrollWheelZoom: true }).setView([56.0, 10.6], 7);
    L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
      maxZoom: 19, attribution: '© OpenStreetMap'
    }).addTo(_map);
  }

  // Geokod ukendte byer live (fx browser-importerede) og cache dem.
  await geocodeMissingCities(cars);

  const placed = [];
  const unplaced = [];
  cars.forEach(c => (cityLatLon(c.city) ? placed : unplaced).push(c));

  if (countEl) countEl.textContent = `${placed.length} biler på kortet` + (unplaced.length ? ` · ${unplaced.length} uden placering` : '');

  if (typeof L !== 'undefined') plotCityMarkers(placed);
  renderUnplaced(unplaced);
}

/** Geokod byer der mangler koordinater, via OpenStreetMap Nominatim (cachet). */
async function geocodeMissingCities(cars) {
  const missing = [...new Set(cars.map(c => c.city).filter(city => city && !cityLatLon(city)))];
  if (!missing.length) return;
  const cache = geoCache();
  for (const city of missing) {
    try {
      const base = city.replace(/\s+(Jyll|[A-ZÆØÅ]{1,3})$/, '').trim();
      const url = `https://nominatim.openstreetmap.org/search?format=json&limit=1&countrycodes=dk&q=${encodeURIComponent(base + ', Danmark')}`;
      const resp = await fetch(url, { headers: { 'Accept-Language': 'da' } });
      const data = await resp.json();
      if (data && data[0]) cache[city] = [Number(Number(data[0].lat).toFixed(5)), Number(Number(data[0].lon).toFixed(5))];
      await new Promise(r => setTimeout(r, 1100));  // respekter Nominatims grænse
    } catch (e) { /* spring byen over ved fejl */ }
  }
  saveGeoCache(cache);
}

/** Tegn én markør pr. by med popup der lister byens biler. */
function plotCityMarkers(cars) {
  const byCity = {};
  cars.forEach(c => { (byCity[c.city] = byCity[c.city] || []).push(c); });
  const bounds = [];
  Object.keys(byCity).forEach(city => {
    const list = byCity[city].sort((a, b) => b.score - a.score);
    const ll = cityLatLon(city);
    if (!ll) return;
    bounds.push(ll);
    const best = list[0].score;
    const color = best >= 70 ? '#1f9d55' : best >= 50 ? '#c47f0a' : '#d1373a';
    const marker = L.circleMarker(ll, {
      radius: Math.min(10 + list.length * 2, 22), color: '#fff', weight: 2,
      fillColor: color, fillOpacity: 0.85
    }).addTo(_map);
    const items = list.map(c => `<li>
      <span class="pp-badge" style="background:${c.score >= 70 ? '#1f9d55' : c.score >= 50 ? '#c47f0a' : '#d1373a'}">${Math.round(c.score)}</span>
      <a href="car.html?id=${esc(c.id)}">${esc(carName(c))}</a>
      <span class="muted small">${fmtPrice(c.price)}</span>
    </li>`).join('');
    marker.bindPopup(`<div class="map-popup"><div class="pp-city">${esc(city)} (${list.length})</div><ul>${items}</ul></div>`,
      { maxWidth: 320 });
  });
  if (bounds.length) _map.fitBounds(bounds, { padding: [40, 40], maxZoom: 11 });
}

/** Vis biler uden kortplacering som en liste. */
function renderUnplaced(cars) {
  const el = document.getElementById('map-unplaced');
  if (!el) return;
  if (!cars.length) { el.innerHTML = ''; return; }
  el.innerHTML = `<div class="panel"><h3>Uden kortplacering (${cars.length})</h3>
    <p class="small muted">Byen kunne ikke geokodes. Kør evt. <code>python geocode.py</code> for at tilføje den.</p>
    <ul class="pill-list">${cars.map(c => `<li><a href="car.html?id=${esc(c.id)}">${esc(carName(c))}</a> <span class="muted small">${esc(c.city || '')} · ${fmtPrice(c.price)}</span></li>`).join('')}</ul></div>`;
}

/* ======================================================================= *
 * Router
 * ======================================================================= */
document.addEventListener('DOMContentLoaded', () => {
  const page = document.body.dataset.page;
  const routes = { index: initIndex, car: initCar, compare: initCompare, rejected: initRejected, map: initMap };
  (routes[page] || (() => {}))();
});
