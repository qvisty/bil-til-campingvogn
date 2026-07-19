# Bil til campingvogn

Et enkelt, lokalt beslutningsværktøj der hjælper med at finde og vurdere brugte
biler til en helt konkret profil: en familiebil der også skal kunne trække en
campingvogn på ca. 1.400 kg trygt og stabilt.

Værktøjet henter bilannoncer fra Bilbasen, normaliserer og scorer dem, frasorterer
irrelevante biler og præsenterer det hele i en statisk hjemmeside med
markedsoverblik, filtrering, sammenligning, favoritter og noter.

- **Ingen database, ingen login, ingen build-proces.**
- Frontend: almindelig HTML + CSS + vanilla JavaScript.
- Backend: et Python-script til scraping og databehandling.
- Data gemmes i JSON-filer; favoritter/noter gemmes i browserens `localStorage`.

---

## 1. Projektets formål

Bilen skal passe til denne profil (kan ændres i `data/settings.json` og i UI):

- Ca. 25.000 km/år, pendling + ture til København og Hamborg, ~3 ugers
  campingferie i Europa årligt.
- Campingvogn ca. 1.400 kg; bilen skal kunne trække mindst 1.600 kg (helst 1.700+).
- **Tilladt:** benzin, benzin-hybrid (HEV), benzin-mildhybrid (vurderes konkret).
- **Frasorteres:** diesel, dieselhybrid, plug-in-hybrid, elbil.
- **Krav:** automatgear (klassisk momentomformer og e-CVT prioriteres; tørkoblet
  DCT får en tydelig risikoadvarsel).
- Pris ≤ 250.000 kr. (helst ≤ 230.000), modelår ≥ 2019, ≤ 125.000 km, kun forhandler.

## 2. Installation af Python-afhængigheder

Kræver Python 3.9+.

```bash
pip install -r requirements.txt
```

Det installerer `requests`, `beautifulsoup4` og `pytest`. Playwright er valgfrit og
kun nødvendigt, hvis væsentlige data skulle kræve JavaScript-rendering.

## 3. Kørsel af scraperen

```bash
# Fuld live-scrape mod Bilbasen (gennemgår alle resultatsider / paginering)
python scraper.py

# Offline test mod gemte HTML-fixtures – kræver ikke netværk
python scraper.py --fixtures

# Genberegn normalisering og score fra eksisterende data/cars.json
python scraper.py --score-only

# Manuel import (se afsnit 6)
python scraper.py --import kopier_section.txt
```

Scraperen:

- Gennemgår **alle** resultatsider, ikke kun den første.
- Bruger Bilbasens annonce-id som primær nøgle og undgår dubletter.
- Cacher detaljesider i `cache/` og genbesøger dem kun efter behov.
- Venter et konfigurerbart tidsrum mellem kald (`REQUEST_DELAY_SECONDS`).
- Bruger en tydelig User-Agent, håndterer midlertidige fejl med retries og logger
  fejl uden at stoppe hele kørslen.
- Registrerer første/seneste observation, prisændringer, ændret km-stand og
  forsvundne annoncer.

> **Vigtigt om scraping:** Scriptet respekterer `robots.txt`, en rimelig
> belastningsgrænse og forsøger **ikke** at omgå CAPTCHA, login eller anden
> adgangskontrol. Hvis scraping ikke er tilladt eller bliver blokeret, så brug
> manuel import (afsnit 6).

## 4. Start af hjemmesiden

Fra projektmappen:

```bash
python -m http.server 8000
```

Åbn derefter <http://localhost:8000/index.html> i browseren.

> Hjemmesiden **skal** serveres via en webserver (ikke åbnes som `file://`),
> fordi den henter JSON-filer med `fetch`.

## 5. Opdatering af data

1. Kør `python scraper.py` i en terminal.
2. Genindlæs hjemmesiden.

Forsiden viser "Opdatér bildata"-instruktionen samt seneste scrapingstatus
(seneste opdatering, antal hentede annoncer, nye biler, prisændringer og fejl).
En statisk side kan af sikkerhedshensyn ikke selv starte Python.

## 6. Import af data (vigtigt)

Bilbasens **søgeresultater er beskyttet af en bot-spærring (AWS WAF)**, der svarer
med en JavaScript-CAPTCHA på ikke-browser-forespørgsler. Værktøjet forsøger
**ikke** at omgå den. Din egen browser kommer lovligt igennem, så data hentes via
import.

### Trin 1: Åbn søgningen i din browser

Det fulde link med alle filtre (benzin + benzin-hybrid, automatgear, mintow 1600 kg,
≤ 125.000 km, ≤ 250.000 kr., modelår fra 2019, forhandler/Retail, sorteret på pris):

```
https://www.bilbasen.dk/brugt/bil?adaptivecruisecontrol&cartype=stationcar&cartype=suv&cartype=cuv&cartype=mpv&cartype=sedan&cartype=hatchback&fuel=1&fuel=6&gear=automatic&mileageto=125000&mintow=1600&priceto=250000&pricetype=Retail&regfrom=2019-01&sortby=price&sortorder=asc
```

(Samme link ligger som en klikbar knap på forsiden.)

### Trin 2: Kopiér resultaterne (samler-kommando)

**Nemmest — browser-consollen.** Åbn udviklerværktøjet (F12) → fanen **Console**,
indsæt kommandoen herunder og tryk Enter. Kør den **på hver resultatside**:

- Første gang oprettes en samlevariabel i `sessionStorage` med tidsstempel.
- Er der allerede data, som er **over 2 minutter gammel**, spørger den, om den skal
  **nulstille** (OK) eller **fortsætte og tilføje** (Annuller).
- Sidens annoncekort gemmes i variablen.
- Til sidst spørger den: **OK = kopiér ALT** til udklipsholderen (færdig), eller
  **Annuller = gå til næste side** (så indsætter og kører du koden igen der).

Samme kommando ligger klar i **Indsæt tekst**-vinduet på forsiden (med en
"Kopiér kommando"-knap), så du slipper for at kopiere den herfra.

```js
(() => {
  const KEY='bb_collect', MAXAGE=120000, now=Date.now();
  const nextSel='a[rel="next"], a[aria-label*="æste"], a[aria-label*="Næste"], button[aria-label*="æste"], a[aria-label*="Next"], button[aria-label*="Next"]';
  let s=null; try { s=JSON.parse(sessionStorage.getItem(KEY)); } catch(e){}
  if (s && now - s.ts > MAXAGE) {
    if (confirm('Der ligger indsamlet data, men den er over 2 minutter gammel.\n\nOK = NULSTIL og start forfra\nAnnuller = FORTSÆT (tilføj til det eksisterende)')) s=null;
  }
  if (!s) s={ ts:now, pages:0, html:[] };
  const cards=[...document.querySelectorAll('article[class*="Listing_listing__"]')].map(a=>a.outerHTML);
  if (!cards.length) { alert('Fandt ingen annoncekort på denne side. Er du på et søgeresultat?'); return; }
  s.html.push(...cards); s.pages++; s.ts=now;
  sessionStorage.setItem(KEY, JSON.stringify(s));
  const next=document.querySelector(nextSel);
  const finish=confirm('Side '+s.pages+' gemt (+'+cards.length+' biler). I alt '+s.html.length+' annoncer.\n\nOK = KOPIÉR ALT til udklipsholder (jeg er færdig)\nAnnuller = '+(next?'gå til NÆSTE side (indsæt og kør koden igen der)':'INGEN næste-knap fundet – naviger selv og kør koden igen'));
  if (finish) {
    const out='<section class="srp_results">'+s.html.join('')+'</section>';
    const done=()=>{ sessionStorage.removeItem(KEY); console.log('✓ Kopieret '+s.html.length+' annoncer til udklipsholderen.'); };
    if (typeof copy==='function') { copy(out); done(); }
    else navigator.clipboard.writeText(out).then(done, ()=>alert('Kunne ikke kopiere automatisk. Data ligger i sessionStorage.bb_collect'));
  } else if (next) { next.click(); console.log('→ Gik til næste side. Indsæt og kør koden igen.'); }
})();
```

> **Bemærk:** `copy()` findes kun, når kommandoen køres direkte i DevTools-consollen
> (ikke i et Snippet). Snippet'et bruger `navigator.clipboard` som fallback.
> Finder den ikke "næste side"-knappen automatisk, så naviger selv og kør igen —
> data samles stadig, indtil du vælger OK (kopiér alt).

**Alternativt — uden consollen:** marker den synlige liste og Ctrl+C, eller
Inspicér → højreklik på `<section class="srp_results…">` → Copy → Copy element.

### Trin 3: Importér — to veje

**A) Indsæt direkte på siden (uden Python):**
Klik **Indsæt tekst** øverst på forsiden, indsæt (Ctrl+V), tryk **Analysér og tilføj**.
Bilerne parses og scores direkte i browseren og gemmes i `localStorage`.

**B) Via terminal (Python):**
Indsæt i filen `kopier_section.txt` (findes i projektmappen), gem, og kør her i
terminalen:

```bash
python scraper.py --import kopier_section.txt   # HTML-element ELLER kopieret tekst (auto-detekteres)
python scraper.py --import gemt_side.html        # gemt HTML-side
python scraper.py --import mine_biler.json        # JSON (liste eller {"cars": [...]})
python scraper.py --import mine_biler.csv         # komma-, semikolon- eller tab-separeret
```

### Nulstilling: hver import erstatter alt

Hver import **nulstiller datasættet** — der beholdes ingen data på tværs af kørsler
(heller ikke prishistorik). `data/cars.json` afspejler præcis den seneste import.
Opdaterer du `kopier_section.txt` og importerer igen, forsvinder de gamle biler
automatisk. Det gælder både terminal-import og browser-import (**Indsæt tekst**
erstatter også det forrige indsatte datasæt).

- Vil du undtagelsesvis **flette** flere filer i stedet: `python scraper.py --import kopier_section.txt --append`.

**Favoritter er undtagelsen.** Når du markerer en bil som favorit (⭐ i browseren),
gemmes bilens fulde info som et snapshot i `localStorage`. Forsvinder bilen fra en
senere import, vises den stadig — markeret som "gemt favorit" — så du beholder
referencen. Fjerner du favoritten, slettes snapshottet.

CSV/tekst med overskriftsrække matcher felterne i `data/cars.json`
(fx `id;make;model;price;fuel;model_year;mileage_km;tow_capacity_kg;kerb_weight_kg`).

> **Bemærk om datadybde:** Resultatlisten indeholder ikke trækvægt/egenvægt — de står
> på hver annonces detaljeside. Uden dem markeres vægtene "verificér på
> registreringsattesten", og campingvognsscoren beregnes på det, der kendes.
> Gearkassens tør/våd-kobling udledes af variant + modelviden, så tørkoblet DCT
> stadig får en tydelig træk-advarsel.

### Python og browser giver samme resultat

Parsing og scoring findes både i Python (`scraper.py`/`normalizer.py`/`scoring.py`)
og i browseren (`parse-score.js`). Begge bruger **de samme videns-filer**
(`gearbox_knowledge.json`, `trailer_stability_knowledge.json`, `settings.json`) som
eneste kilde. Resultaterne er verificeret identiske på rigtige data.

## 7. Kørsel af tests

```bash
python -m pytest
```

Testene kræver **ikke** live adgang til Bilbasen. De dækker bl.a. parsing af gemt
HTML, paginering, dubletter, prisændringer, forsvundne annoncer,
drivmiddelklassifikation (inkl. frasortering af plug-in- og dieselhybrid),
gearkasseklassifikation, vægtforhold og scoreberegning.

## 8. Beskrivelse af JSON-filerne (`data/`)

| Fil | Indhold |
|-----|---------|
| `cars.json` | Alle biler med normaliserede felter, score, delscorer, vægtforhold, risici og afvisningsgrunde. Genereres af scraperen. |
| `price_history.json` | Pris- og km-historik pr. annonce-id. |
| `scrape_status.json` | Status for seneste kørsel (tidspunkt, antal, fejl). |
| `gearbox_knowledge.json` | Redigerbar viden om gearkasser (type, tør/våd kobling, risici, kilder, confidence). |
| `trailer_stability_knowledge.json` | Viden og søgetermer om anhængerstabilisering. |
| `model_specs.json` | Modelviden: typiske egenvægte, trækvægte, moment m.m. pr. model+motor. Udfylder felter, der mangler i Bilbasens liste. |
| `city_coords.json` | Bykoordinater til kortet (genereres af `geocode.py`). |
| `settings.json` | Profil, scoringsvægte, tærskler, driftsøkonomi og finansieringssatser. |

### Modelviden — udfyld manglende egenvægt/trækvægt

Bilbasens resultatliste mangler ofte **egenvægt** og **trækvægt** (de står kun på
detaljesiden). `data/model_specs.json` indeholder typiske værdier pr. model+motor,
som begge pipelines (Python + browser) bruger til at **udfylde huller** — markeret
som "modelviden" og altid med "verificér på registreringsattesten".

Vigtigt: en **gættet** trækvægt udløser aldrig et hårdt fravalg (kun annoncens egen
trækvægt kan det). En gættet lav trækvægt sænker blot campingvognsscoren.

Hver regel matcher på mærke → model → `variant_patterns` (regex på varianten,
fx `"1[.,]5 tsi"`) → evt. årgang, fra mest til mindst specifik. Felter: `kerb_weight_kg`
(køreklar), `tow_capacity_kg` (bremset), `torque_nm`, `hp`, `drivetrain`,
`body_type`, `trunk_liters`, plus `confidence`.

**Sådan holder du den opdateret:** når du har en ny liste biler, så bed om at få
tilføjet modelviden for de nye modeller — så udfyldes filen med bedste bud, og alle
biler får beregnet vægtforhold og en fuld campingvognsscore.

## 9. Tilpasning af brugerprofil og scoringsregler

- **Profil og krav:** ret `data/settings.json` (fx `caravan_weight_kg`,
  `min_tow_kg`, `price_max_dkk`, `mileage_max_km`). Kør derefter
  `python scraper.py --score-only`.
- **Scoringsvægte:** ret `weights` i samme fil (summen bør give 1,0).
- **Gearkasseviden:** tilføj/ret regler i `data/gearbox_knowledge.json`. Reglerne
  matches fra mest til mindst specifik (mærke → model → motor → årgang → navnemønster).
- **Campingvognens vægt** kan også ændres direkte i UI på den enkelte bilside;
  vægtforholdet opdateres live og gemmes i `localStorage`.

## 10. Vurderingsmodellen (0–100)

| Delscore | Vægt |
|----------|------|
| Campingvognsegnethed | 30 % |
| Drivlinje og gearkasse | 20 % |
| Pris og markedsværdi | 15 % |
| Alder og kilometerstand | 15 % |
| Sikkerhed og udstyr | 10 % |
| Driftsøkonomi | 10 % |

Alle delscorer og deres faktorer vises på bilsiden, så beregningen kan forklares.

**Vægtforhold** = campingvognens vægt / bilens køreklare vægt × 100:

- 🟢 Grøn: ≤ 90 % · 🟡 Gul: 90–100 % · 🔴 Rød: > 100 % · ★ ≤ 85 % (særlig stabilt).

Dette er en **sikkerhedsvejledning**, ikke et lovkrav.

## 11. Backup af favoritter og noter

Favoritter, fravalg, noter, statusser, forhandlersvar, tilbudt pris og
sammenligningsvalg gemmes i browserens `localStorage`.

- **Eksportér:** knappen "Eksportér data" på forsiden gemmer alt som JSON.
- **Importér:** knappen "Importér data" indlæser en tidligere backup.

## 12. Kendte begrænsninger

- Bilbasens sidestruktur kan ændre sig; parseren bruger både JSON-LD og en
  HTML-fallback, men kan kræve justering. Brug `--fixtures` og manuel import som
  robust alternativ.
- Bilbasens generelle modeloplysninger kan være forkerte eller tilhøre en anden
  variant. Derfor gemmer værktøjet kilde og confidence, og markerer
  "verificér på registreringsattesten" for usikre vægte.
- Markedsvurdering kræver mindst tre sammenlignelige biler; ellers vises
  "utilstrækkeligt datagrundlag".
- Driftsøkonomi er vejledende (fakta vs. beregnet vs. skøn er tydeligt markeret).

## 13. Udgivelse på GitHub Pages

Hele **browser-delen** virker på GitHub Pages, fordi siden er 100 % statisk (HTML +
CSS + JS + JSON). Besøgende kan bruge **Indsæt tekst** til at loade deres egne data
(gemmes i deres egen `localStorage`). Kortet, sammenligning, favoritter, egenvægt-
indtastning m.m. virker alt sammen.

> Python-scriptet (`scraper.py`, `geocode.py`) kører **ikke** på Pages — det er et
> lokalt værktøj. Data på det udgivne site er det, du har committet i `data/cars.json`.
> Vil du opdatere det offentlige datasæt: kør importen lokalt og commit den nye
> `data/cars.json` (+ `data/city_coords.json`).

**Sådan udgiver du (engangsopsætning):**

```bash
git init
git add .
git commit -m "Bil til campingvogn – beslutningsværktøj"
git branch -M main
git remote add origin https://github.com/qvisty/bil-til-campingvogn.git
git push -u origin main
```

Aktivér derefter Pages: **GitHub → repo → Settings → Pages → Source: "Deploy from a
branch" → Branch: `main` / `/ (root)` → Save.** Efter ca. et minut ligger siden på:

```
https://qvisty.github.io/bil-til-campingvogn/
```

Ved senere ændringer: `git add -A && git commit -m "..." && git push`.

Bemærk:
- `kopier_section.txt` (din rå HTML-dump) er i `.gitignore` og udgives ikke.
- `data/cars.json` udgives **tom** (`[]`) — der følger ingen biler med, så du undgår
  forkert/forældet data. Du indsætter selv dine data via **Indsæt tekst**.
- **Kun favoritter og indstillinger gemmes varigt** (localStorage). Indsatte biler,
  noter, status og sammenligning er kun midlertidige for den aktuelle browsersession
  (sessionStorage) og forsvinder, når du lukker fanen — så en ny session starter rent.
  En favorit gemmer bilens fulde info som snapshot og bevares.

## 14. Juridiske og tekniske hensyn ved scraping

Respektér altid Bilbasens vilkår, `robots.txt` og rimelige belastningsgrænser.
Værktøjet er tænkt til personligt brug. Det forsøger ikke at omgå tekniske
beskyttelser. Bliver adgang blokeret, så anvend manuel import.

## Projektstruktur

```
.
├── kopier_section.txt  # Indsæt Bilbasens resultat-element her, og importér
├── index.html          # Forside (markedsoverblik) + biloversigt
├── car.html            # Detaljeside for én bil
├── compare.html        # Sammenligning af op til 5 biler
├── parse-score.js      # Browser-pipeline (parsing + scoring, spejler Python)
├── rejected.html       # Afviste biler med begrundelse
├── styles.css          # Al styling
├── app.js              # Al frontend-logik
├── scraper.py          # Scraper + databehandling (CLI)
├── geocode.py          # Geokoder byer til kort-pins (CLI)
├── scoring.py          # Filtrering, vægtforhold og scoring
├── normalizer.py       # Normalisering af rå data + gearkasse/drivmiddel/modelviden
├── requirements.txt
├── data/               # JSON-data og redigerbar viden
├── cache/              # Cache af detaljesider (oprettes automatisk)
└── tests/              # pytest-tests + HTML-fixtures
```
