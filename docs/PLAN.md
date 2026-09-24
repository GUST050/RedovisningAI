# RedovisningAI – reviderad produkt- och arkitekturplan (v2)

> Den här versionen bygger på ChatGPT-planen (v1) och behåller dess grundprinciper.
> Den rättar svagheter i affärsidé, svensk redovisningsdomän, AI-design, juridik och byggordning.
> Paragrafhänvisningar som "(v1 §69)" syftar på numreringen i den ursprungliga planen.

---

## 0. Sammanfattning: de tolv viktigaste ändringarna

| # | v1 sa | v2 säger | Varför |
|---|---|---|---|
| 1 | Fyra produkter på en gång (Review, Analytics, Spend, Advisor) | **En kil först: "Månadsgranskning + kundmötesunderlag".** Resten byggs på samma motor efteråt | En ensam utvecklare kan inte bygga fyra produkter till kommersiell kvalitet. Granskning sparar debiterbar tid direkt, och kunden ser värdet snabbt |
| 2 | SIE-uppladdning i MVP, Fortnox API i V2 | **Fortnox-API i MVP, med SIE4 som format** (`GET /3/sie/4?financialyear=`). Manuell uppladdning finns som reserv för Visma, BL och andra | Ingen konsult laddar upp 300 filer i månaden. Utan automatisk synk blir portföljvyn aldrig aktuell. Samma parser används för båda vägarna |
| 3 | Konkurrenter nämns inte | **Positionera mot Fortnox Insikter** (avvikelsebevakning för byråer), Business Board och liknande, samt generiska "chatta med Fortnox"-MCP-verktyg | Fortnox bygger redan avvikelsebevakning och AI-assistent. En chatt ovanpå bokföringen blir snart standard. Vallgraven är granskningsflöde, dokumentation och spårbarhet, inte chatten |
| 4 | Spend Intelligence är en flaggskeppsfunktion i MVP | **Spend blir V1.5** och kräver leverantörsdata (Fortnox leverantörsfakturor/reskontra) | SIE4 saknar strukturerade motparter och reskontra. Leverantörsanalys bara ur verifikationstexter blir för osäker för ett flaggskepp |
| 5 | Månadsjämförelser tas för givna | **Motorn mäter periodiseringsmognad och bokföringsmetod** (kontantmetod, periodiseras avskrivningar och semesterlöneskuld löpande?) och anpassar jämförelser och tröskelvärden | Många småbolag periodiserar bara vid bokslut. Naiva MoM-jämförelser ger då en flod av falsklarm |
| 6 | Generiska granskningsregler | **Svenska kontroller**: moms, skattekonto, arbetsgivaravgifter, semesterlöneskuld, förbjudna lån (ABL 21 kap), kontrollbalansräkning (ABL 25 kap), periodiseringsfonder, nummerserieluckor, ändringar i godkänd period | Det är här byrån faktiskt lägger tid, och här kan en generell BI- eller AI-tjänst inte konkurrera |
| 7 | AI skriver text, sedan verifieras siffror (v1 §54) | **AI skriver aldrig siffror.** Den refererar till `fact_id`, och servern renderar alla belopp och procent | Efterhandsverifiering missar härledda tal, avrundningar och fel period. Konstruktionen gör sifferfel omöjliga i stället för osannolika |
| 8 | "Manager + agenter" | **Kodstyrda arbetsflöden med sju avgränsade AI-uppgifter** och en separat granskare. Endast Q&A-agenten får loopa fritt, och bara med läsverktyg | Förutsägbart, testbart och billigt. Varje uppgift får egen eval (se §7) |
| 9 | Temporal från start | **Postgres-baserad jobbkö** (t.ex. Procrastinate) med idempotenta steg. Temporal utvärderas när flöden faktiskt blir långa | En SIE4-import tar sekunder. Temporal är en tung driftkomponent för ett ensamt team |
| 10 | Sex byråroller + SSO tidigt | **Tre roller** (Byråadmin, Konsult, Läsare) + klienttilldelning + en särskild behörighet för lönekonton. Microsoft-SSO när första större byrån kräver det | De flesta svenska byråer är små. Lönedata per person är det känsligaste i bokföringen |
| 11 | GDPR, retention | **Plus:** biträdeskedja (kund → byrå → vi), penningtvättslagens meddelandeförbud (tipping-off), AI Act art. 50 (gäller sedan 2 aug 2026), REKO-dokumentation och exfiltrationsskydd i AI-UI:t | v1 missar de regler som specifikt träffar redovisningsbyråer |
| 12 | Pilot i steg 22 av 24 | **Fas 0: concierge-pilot med 3–5 byråer innan produkten byggs.** Skript → Excel/PDF-rapport, manuellt kvalitetssäkrad | Validerar vad byråer betalar för innan 6 månaders bygge. Ger även ett riktigt SIE-testkorpus |

**Det som behålls oförändrat från v1:** multi-tenancy runt byrån, deterministisk ekonomimotor, "AI är aldrig sifferfacit", bevarade originalfiler med SHA-256, versionerade dataset och mappningar, Decimal/NUMERIC, statusvärden i stället för `null = 0`, Review Priority i stället för Fraud Score, typade AI-verktyg utan fri SQL, intern rapport skild från kundrapport, modulär monolit, Python/FastAPI + Next.js + PostgreSQL med RLS, analysis snapshots och golden tests.

---

## 1. Svagheter i v1 – analys

### 1.1 Affär och marknad
1. **Ingen konkurrensanalys.** Fortnox Insikter automatiserar redan "att hålla koll på och hantera avvikelser i kundernas bokföring" för byråer. Fortnox lanserar också successivt en AI-assistent under 2026. Tredjepartsverktyg (Nordsynk, Klartext MCP m.fl.) låter byråer fråga Claude eller ChatGPT direkt mot Fortnox. *"Fråga AI om bokföringen"* är alltså redan en vara och ingen differentiering.
2. **Ingen affärsmodell**: pris, betalningsvilja, kostnad per klient-månad och säljkanal saknas helt.
3. **Ingen hypotes om vem som betalar och varför.** Byråer har ofta fastpris per kund. Verktyget tjänar då pengar åt byrån på två sätt: (a) kortare tid per månadsavstämning, eller (b) rådgivning som går att sälja. (a) går att mäta och sälja från dag ett. (b) är svårare, eftersom många byråer vill sälja rådgivning men har svårt att ta betalt för den.
4. **Plattformsrisk**: produkten blir beroende av Fortnox API-villkor. Det måste hanteras (se §3), inte ignoreras.

### 1.2 Data och svensk redovisning
5. **Manuell SIE-uppladdning som enda källa** gör portföljvyn (v1 §3) inaktuell i praktiken.
6. **SIE4 har inte det Spend Intelligence behöver**: ingen strukturerad motpart och ingen kund- eller leverantörsreskontra. Kundfordringarnas åldersanalys är omöjlig ur SIE4 ensamt.
7. **YoY kräver föregående års verifikationer.** SIE4 innehåller #IB/#UB/#RES för föregående år, men inte föregående års transaktioner per månad. Onboarding måste därför hämta 2–3 räkenskapsår bakåt.
8. **Periodiseringsproblemet saknas.** Kontantmetoden, avskrivningar och semesterlöneskuld bokas ofta bara vid bokslut, och fakturor kommer sent. Utan en mognadsbedömning larmar systemet varje månad om saker som inte är fel.
9. **Periodens fullständighet saknas.** "Augusti klar" beror inte på att filen finns, utan på att bank, leverantörsfakturor och löner är bokade. Systemet måste uppskatta *hur klar* perioden är.
10. **Legal uppställning och management-vy blandas.** Mappningshierarkin (v1 §28) behöver två separata träd: ÅRL/K2/K3-uppställning (resultat- och balansräkning) och byråns/kundens management-kategorier (spend).
11. **Svenska nyckeltalsdefinitioner saknas**, t.ex. soliditet med justerat eget kapital (obeskattade reserver × (1 − skattesats)). Fel definition förstör förtroendet direkt.
12. **Dataset-versionering som helkopior** exploderar i lagring vid nattlig synk av 300 klienter. Den ger heller inget svar på den viktigaste granskningsfrågan: *vad ändrades sedan förra gången?*

### 1.3 AI
13. **Faktaverifiering i efterhand (v1 §54) räcker inte.** "Konsultkostnaden ökade 18 %" kan vara härlett, avrundat eller jämföra fel perioder, och ändå klara en kontroll av typen "finns evidence_id?".
14. **AI används där deterministisk kod är bättre** (variansförklaring, nya/försvunna kostnader, periodicitet), men inte där AI är bäst: mappningsförslag, normalisering av fritext, triage av falsklarm och formulering av kundfrågor.
15. **Ingen hantering av falsklarm.** Utan undertryckningsregler och återkoppling slutar konsulter läsa fynden inom en månad. Det är det vanligaste sättet granskningsverktyg dör på.
16. **Prompt injection behandlas bara på instruktionsnivå.** Den verkliga risken är exfiltration: en injicerad text som får UI:t att rendera en markdown-bild eller länk till en extern URL med data i.
17. **Ingen kostnadsstyrning** av AI-anrop per byrå eller klient.

### 1.4 Juridik och säkerhet
18. **Biträdeskedjan saknas.** Klientbolaget är personuppgiftsansvarigt, byrån är biträde och vi är underbiträde. Avtal, underbiträdeslista och instruktioner måste följa den kedjan.
19. **Penningtvättslagen.** Redovisningsbyråer är verksamhetsutövare enligt PTL och omfattas av meddelandeförbud. Fynd som kan kopplas till misstänkt penningtvätt får **aldrig** synas i kundrapport eller kundportal, och AI får inte föreslå att man frågar kunden om dem.
20. **Enskilda firmor**: organisationsnumret *är* personnumret, så hela bokföringen är personuppgifter.
21. **Lönedata** för bolag med 1–3 anställda avslöjar individuella löner, både för alla som ser kontot och för AI-leverantören.
22. **AI Act art. 50** (transparens för interaktiva AI-system) gäller sedan 2 augusti 2026. **Art. 4 (AI-kunnighet)** gäller byrån som tillhandahållare. Produkten bör hjälpa byrån uppfylla båda.
23. **Molnsuveränitet**: Azure är amerikanskägt (CLOUD Act). För många privata byråer spelar det mindre roll, men det bör vara ett medvetet beslut och inte en slump (se §9.6).

### 1.5 Genomförande
24. **24 steg utan kundkontakt före steg 22.** Risken är att bygga rätt saker i fel ordning, eller fel saker väldigt korrekt.
25. **Överdimensionerad infrastruktur** för start: Temporal, sex roller, SAML, ECharts-sankey, fyra mappningsnivåer + transaktions-override.
26. **Ingen exportstrategi.** Redovisningskonsulter lever i Excel och redigerar kundrapporter i Word/PowerPoint. Rapporter som bara kan läsas i webben blir inte använda.

---

## 2. Reviderad produktdefinition

### 2.1 Positionering
> **Byråns granskningskollega.** Varje månad går den igenom alla kunders bokföring och säger var konsulten ska lägga sin tid. Den dokumenterar granskningen och tar fram underlaget till kundmötet, med varje siffra spårbar till verifikationen.

Skillnad mot befintliga alternativ:

| Alternativ | Vad de gör | Vår skillnad |
|---|---|---|
| Fortnox Insikter | Avvikelsebevakning och nyckeltal inom Fortnox | Systemoberoende (Fortnox, Visma, BL, SIE-fil). Djupare svenska kontroller med evidens. Granskningsflöde med status, kvittens och dokumentation. Ändringsspårning mellan importer |
| BI/rapportverktyg (Business Board m.fl.) | Rapporter, budget och dashboards | Vi granskar och förklarar, vi visar inte bara siffror |
| "Chatta med Fortnox" (MCP) | Fri fråga mot bokföringen | Deterministiska siffror, evidenskedja, portföljvy, arbetsflöde och revisionsspår. Chatten är en funktion hos oss, inte produkten |
| Internationella verktyg (Fathom, Syft, MindBridge m.fl.) | Rådgivningsrapporter / anomalidetektion | Svensk kontoplan (BAS), SIE, ABL, moms, AGI och svenska arbetsflöden |

### 2.2 Kilen (MVP) – "Månadsgranskning"
Kärnflödet konsulten gör varje månad:

```text
Nattlig synk (Fortnox API) / SIE-uppladdning
   ↓
Import + ändringsdiff mot förra importen
   ↓
Fullständighets- och mognadsbedömning av perioden
   ↓
Svenska granskningskontroller (deterministiska)
   ↓
AI-triage: grupperar, förklarar och föreslår "troligen OK / utred"
   ↓
Konsulten kvitterar fynd (OK / Åtgärdat / Fråga kund / Undertryck regel)
   ↓
Periodanalys (RR/BR, månad, YTD, R12, avvikelsebrygga)
   ↓
Kundmötesunderlag: 1-sidig sammanfattning + frågor till kunden
   ↓
Godkänn → Analysis Snapshot (låst) → Export PDF/Word/Excel
```

Portföljvyn (v1 §3) behålls och blir startsidan. Den blir användbar först när datan synkas automatiskt.

### 2.3 Definition av "färdig produkt"
v1 §108 behålls, med två mätbara krav till:
- **Tidsbesparing:** medianen för tid per kund-månadsgranskning sjunker med ≥ 30 % hos pilotbyråerna.
- **Precision:** ≥ 50 % av High-fynd leder till en åtgärd eller en fråga till kunden. Mäts via kvittensstatus.

---

## 3. Datakällor (ersätter v1 §6–7)

| Fas | Källa | Innehåll | Kommentar |
|---|---|---|---|
| MVP | **Fortnox API → SIE4** (`/3/sie/4?financialyear=`) | Kontoplan, IB/UB, alla verifikationer, dimensioner | Samma parser som filimport. Hämta innevarande + 2 föregående räkenskapsår vid onboarding |
| MVP | **SIE4-filuppladdning** | Samma | Reserv för Visma eEkonomi, Björn Lundén, Hogia m.fl. Visma exporterar SIE4 (inkl. SIE4E) |
| V1.5 | **Fortnox API: leverantörsfakturor, kundfakturor, leverantörer, kunder** | Motpart, förfallodatum, betalstatus, fakturarader (där de finns) | Möjliggör Spend Intelligence, reskontraavstämning (1510/2440) och åldersanalys |
| V2 | Visma eEkonomi API | Som ovan | API-tillägg kostar kunden per månad, vilket måste finnas med i prismodellen |
| V2 | SIE5 | Reskontra, dokumentkopplingar | När exportörer faktiskt stöder den |
| V3 | Bank (PSD2/Open Banking), lön (AGI-underlag) | Kassaflöde, avstämning | |

**Plattformsrisk och motåtgärder:**
- Allt går via en `SourceConnector`-abstraktion. Kärnan känner bara till normaliserade data.
- SIE-filvägen ska alltid fungera fullt ut, så att produkten överlever om ett API stängs.
- Sök partnerskap/listning på Fortnox integrationsmarknad tidigt. Det är också en säljkanal.

**Onboarding-krav:** en ny klient är inte "klar" förrän minst 13 månaders historik finns. Annars visas YoY- och R12-jämförelser som `INSUFFICIENT_DATA`.

---

## 4. Svensk domänlogik som saknades

### 4.1 SIE4-detaljer parsern måste klara
- Teckenkodning: `#FORMAT PC8` = CP437. Testa åäö i konto- och verifikationstexter från varje exportör.
- `#RTRANS` / `#BTRANS` (tillagda/borttagna rader i rättade verifikationer). Rättelser syns alltså både som egna korrigeringsverifikationer och som rad-historik.
- `#KSUMMA` (kontrollsumma) när den finns.
- `#PBUDGET` / `#PSALDO` (budget och periodsaldon). Budget mot utfall är en rådgivningsfunktion som redan finns i datan.
- `#DIM` / `#OBJEKT` / objektlistor på `#TRANS` (kostnadsställe, projekt).
- `#RAR` med brutna räkenskapsår och förlängt första räkenskapsår (upp till 18 månader).
- Registreringsdatum på `#VER` (används för kontrollen av bokföringsfördröjning).
- Teckenkonvention: debet positivt, kredit negativt. Presentationslagret vänder tecken för intäkter, skulder och eget kapital. Det görs aldrig i lagringen.

### 4.2 Periodmognad (ny motor, före analysmotorn)
För varje kund och period räknas indikatorer fram (deterministiskt):

| Indikator | Hur |
|---|---|
| Bokföringsmetod | Heuristik: finns kundfordringar och leverantörsskulder löpande under året, eller bara vid årsskiftet? Kan överstyras av konsulten |
| Löpande avskrivningar | 78xx bokförs varje månad eller bara i bokslutsmånaden? |
| Löpande semesterlöneskuld | 2920 rör sig under året? |
| Periodens fullständighet | Antal verifikationer, bankrörelser och leverantörsfakturor jämfört med kundens normala mönster. Löner bokade? |
| Senaste bokföringsdatum | Finns verifikationer daterade efter periodslut (tecken på att perioden är stängd)? |

Utfallet **styr analysen**:
- Låg mognad ger R12 och YTD som standardvy i stället för MoM, en varningsbanner och högre väsentlighetströsklar för periodrelaterade fynd.
- Ofullständig period gör att analysen märks "preliminär" och att kundrapport inte kan godkännas utan aktiv överstyrning.

### 4.3 Granskningskontroller för MVP (ersätter v1 §48)
Alla är deterministiska, har konfigurerbar väsentlighet per byrå och kund, och ger evidens (verifikationer och konton).

**Integritet och bokföringsregler**
1. Obalanserad verifikation (debet ≠ kredit)
2. IB ≠ föregående års UB
3. Luckor eller dubbletter i verifikationsnummerserier
4. Lång bokföringsfördröjning (registreringsdatum mot verifikationsdatum), bedömd mot BFL:s krav på löpande bokföring
5. **Ändringar i redan godkänd period** (ändringsdiff mot låst snapshot), med vilka verifikationer som lagts till, ändrats eller tagits bort
6. Konton som saknas i kontoplanen eller ligger utanför BAS-intervallen
7. Konto med onormalt tecken (t.ex. intäktskonto med debetsaldo, negativ kassa 1910)

**Moms och skatt**
8. Momskonton (26xx) inte nollställda mot redovisningskontot efter momsperiodens slut. Momsperiod (månad/kvartal/år) konfigureras per kund
9. Rimlighet mellan ingående moms och momsbärande kostnader, och mellan utgående moms och intäkter per momssats
10. Skattekonto 1630: saldo och rörelse som bör stämmas av (full avstämning kräver Skatteverket-data senare)
11. Personalskatt (2710) och arbetsgivaravgifter (2730) nollas inte månadsvis
12. Arbetsgivaravgifter (75xx) utanför förväntat intervall mot bruttolöner (70xx–72xx). Intervallet är konfigurerbart eftersom åldersrabatter och tillfälliga nedsättningar påverkar det

**Balansposter och bokslut**
13. Hängkonton/avräkningskonton med kvarstående saldo (byrådefinierad lista)
14. Inventarier (12xx) finns men inga avskrivningar (78xx) bokade
15. Semesterlöneskuld (2920) oförändrad trots löner
16. Periodiseringsfond som ska återföras (6:e året)
17. Bank (1930) negativt utan känd checkkredit

**Aktiebolagsrätt (hög rådgivningsnytta)**
18. **Fordringar på delägare/närstående** (BAS 168x m.fl.). Möjligt förbjudet lån enligt ABL 21 kap
19. **Varning för kontrollbalansräkning**: eget kapital understiger hälften av registrerat aktiekapital (ABL 25 kap). Detta är en indikator, inte en juridisk bedömning

**Transaktionsmönster**
20. Dubblettkandidater (belopp, konto, text-likhet, datumfönster)
21. Stora manuella poster nära periodslut
22. Snabb återföring (bokning och motbokning inom kort tid)
23. Ovanlig kontokombination för kunden (historisk frekvens)
24. Nytt konto eller ny motpart över väsentlighetsgräns
25. Runda belopp på konton där det är ovanligt

### 4.4 Två separata klassificeringsträd (ersätter v1 §28 och §37–38)

```text
Konto (importerat, orört)
   ├── Legal uppställning: ÅRL/K2/K3 kostnadsslagsindelad RR + BR
   │     (systemstandard från BAS, sällan ändrad, styr RR/BR och nyckeltal)
   └── Management-kategori: Personal / Lokaler / IT / Konsulter / ...
         (byråmall → kundöverstyrning, styr spend och kundrapport)
```

Precedens för varje träd: **System → Branschmall → Byrå → Kund**. Transaktions-override (v1 §28 nivå 5) skjuts till V2. Den är dyr att bygga rätt, eftersom varje override ger en ny mappningsversion.

### 4.5 Nyckeltal
- Följ vedertagna svenska definitioner och dokumentera formeln i UI:t ("Så räknas detta").
- Soliditet = justerat eget kapital / totalt kapital, där justerat EK = EK + obeskattade reserver × (1 − bolagsskattesats). Skattesatsen är versionerad i metric-registret, inte hårdkodad.
- Varje nyckeltal har `required_data` och `min_maturity`. Till exempel ger kassalikviditet vid låg periodmognad status `PARTIAL`.

---

## 5. Datamodell – ändringar

### 5.1 Verifikationsversionering i stället för helkopior (ersätter v1 §23)
```text
voucher_version
---------------
company_id, fiscal_year_id
series, number                 -- naturlig nyckel
content_hash                   -- hash av normaliserat innehåll inkl. rader
valid_from_import_id
valid_to_import_id (null = aktuell)
```
- En import skriver bara nya rader för verifikationer vars hash ändrats, tillkommit eller försvunnit.
- **Dataset-version = import_id.** "Dataset V7" betyder alla voucher_version-rader giltiga vid import 7. Rapporter blir fortfarande exakt reproducerbara.
- Ändringsdiff (kontroll 5) blir en billig fråga.
- Förberäknad faktatabell `account_period_balance(company, import_id, account, period, amount)`, materialiserad per import och används av alla motorer.

### 5.2 Fakta och evidens som förstklassig entitet (skärper v1 §32, §50)
```text
fact
----
id, company_id, import_id, mapping_version, calc_version
kind            -- metric | balance | variance_component | aggregate | count
subject         -- t.ex. "metric:operating_margin" / "category:IT"
period_spec     -- normaliserad period + jämförelseperiod
value NUMERIC, unit, status
lineage JSONB   -- konton, filter, ingående fact_id:n
```
- Alla siffror i UI, rapporter och AI-svar är `fact`-rader.
- Klick på en siffra visar lineage: konton → verifikationer → rader → `source_file` + `source_line`.
- Fakta är oföränderliga. Ny beräkning ger nya rader, och snapshots pekar på fact_id:n.

### 5.3 Fynd med livscykel (ersätter v1 §50)
```text
finding
-------
id, company_id, rule_code, rule_version, period, severity
fingerprint          -- stabil identitet över importer (regel + nyckelobjekt)
status               -- NEW | IN_PROGRESS | ASK_CLIENT | RESOLVED | ACCEPTED_OK | SUPPRESSED
evidence_fact_ids, evidence_voucher_ids
ai_triage_id         -- AI-förklaring (se §7), valfri
visibility           -- INTERNAL | CLIENT_SAFE | RESTRICTED_AML
resolved_by, resolved_at, resolution_note

suppression_rule
----------------
company_id | organization_id, rule_code, match_criteria, reason, expires_at, created_by
```
- `fingerprint` gör att samma fynd inte dyker upp igen varje natt.
- Undertryckning kräver en motivering och har ett utgångsdatum. Den loggas som REKO-dokumentation.
- `RESTRICTED_AML` syns bara för rollen med PTL-ansvar, exkluderas från allt kundriktat och skickas aldrig till kundmötesagenten.

### 5.4 Känsliga konton
- `account_sensitivity` per kontointervall: `NORMAL | PAYROLL | PERSONAL`. Standard: 70xx–76xx och 2710 = PAYROLL.
- Transaktionsrader på PAYROLL-konton kräver behörigheten *Lönedata*. Utan den visas bara aggregat per period och kategori. Maskeringen görs i API-lagret **och** i RLS-policy.
- PAYROLL-rader skickas aldrig på radnivå till AI, bara aggregat.

---

## 6. Arkitektur och teknik – justeringar

| Område | v1 | v2 |
|---|---|---|
| Stil | Modulär monolit | **Behålls** |
| Backend | Python + FastAPI | **Behålls.** Lägg till `ruff`, `mypy --strict` på domänmoduler, Pydantic v2 |
| Frontend | Next.js + shadcn + TanStack Table + ECharts | **Behålls.** Next.js används som ren klient mot FastAPI (ingen affärslogik i server components). ECharts för linje, stapel och vattenfall i MVP, resten senare |
| Databas | PostgreSQL + RLS | **Behålls.** RLS på `organization_id` på alla tabeller. Klienttilldelning via en `user_company_access`-vy i policyn. `SET LOCAL app.current_org / app.current_user` per transaktion. Testa med PgBouncer i transaction mode |
| Jobb | Temporal | **Postgres-kö** (Procrastinate eller motsvarande, `FOR UPDATE SKIP LOCKED`). Idempotensnyckel `(import_id, step, step_version)` behålls. Temporal omprövas om flöden med mänskliga väntesteg eller dygnslånga jobb uppstår |
| Schemaläggning | – | Nattlig synk per byrå, spridd över natten, med backoff mot Fortnox rate limits |
| Lagring | Object storage | **Behålls.** Kryptering per byrå (envelope, egen DEK per organisation) så radering av byrå = radering av nyckel + data |
| Auth | OIDC, senare Entra/SAML | OIDC via hanterad IdP med EU-hosting + TOTP/passkeys. **Microsoft Entra** när första byrån kräver det. BankID först när kundportal byggs |
| Roller | 6 st | **Byråadmin, Konsult, Läsare** + behörighetsflaggor: *Lönedata*, *PTL-ansvarig*, *Godkänna kundrapport*. Tilldelning per klient |
| Export | – | **Excel (xlsx) från varje tabell** med verifikationsreferenser. Kundrapport som **PDF och Word (docx)**. Excel-export i MVP |
| Observability | Monitoring | OpenTelemetry, strukturerade loggar **utan** belopp, texter eller personuppgifter. Separat, åtkomstbegränsad AI-trace-lagring med kort retention |

**Projektstruktur (v1 §85)** behålls, med tillägg:
```text
services/api/app/
  connectors/      # fortnox/, sie_file/  (SourceConnector-interface)
  maturity/        # periodmognad & fullständighet
  facts/           # fact-tabell, lineage, rendering
  findings/        # livscykel, suppression, fingerprint
  ai/
    tasks/         # en modul per AI-uppgift (se §7), med schema + prompt + eval
    verifier/
    providers/
  exports/         # xlsx, docx, pdf
```

---

## 7. AI-arkitektur och agenter (ersätter v1 §51–56, §74–76, §93)

### 7.1 Princip: AI där den är bättre än kod, och bara där
| Uppgift | Deterministisk kod | AI |
|---|---|---|
| Summor, nyckeltal, varians, bryggor | ✅ | ❌ |
| Nya/försvunna kostnader, periodicitet, nivåskiften | ✅ (statistik) | ❌ |
| Granskningskontroller | ✅ | ❌ |
| Förslag på kontomappning för okända/avvikande konton | | ✅ (konsulten bekräftar) |
| Normalisering av motparter ur fritext | Delvis (regler, fuzzy) | ✅ (konsulten bekräftar ovan väsentlighet) |
| Triage av fynd: "troligen OK, för att …" | | ✅ |
| Sammanfatta och prioritera för människan | | ✅ |
| Formulera frågor till kunden | | ✅ |
| Fritt Q&A | Verktyg | ✅ (orkestrerar verktyg) |

### 7.2 Påståenden refererar fakta, siffror renderas av servern
AI:n returnerar **strukturerad output**, aldrig fri text med siffror:

```json
{
  "claims": [
    {
      "type": "OBSERVATION",
      "text": "Kostnaden för {f:ext_consult_chg} ökade med {f:ext_consult_delta_sek} ({f:ext_consult_delta_pct}) jämfört med {period:cmp}.",
      "fact_ids": ["ext_consult_chg", "ext_consult_delta_sek", "ext_consult_delta_pct"]
    },
    {
      "type": "HYPOTHESIS",
      "text": "Ökningen sammanfaller med tre nya leverantörer från april; det kan höra ihop med ett projekt.",
      "fact_ids": ["new_cp_apr_list"]
    },
    {
      "type": "QUESTION",
      "text": "Är konsultinsatserna från april tillfälliga eller löpande?",
      "fact_ids": []
    }
  ]
}
```

Servern:
1. Validerar att varje `fact_id` finns, tillhör rätt bolag och import och ingick i det evidenspaket som skickades.
2. **Renderar värdet** med byråns formatregler (tkr/Mkr, avrundning, tecken).
3. **Avvisar siffror skrivna som text** i `text` (regex på tal + valuta/procent) och genererar om.
4. Tillåter `OBSERVATION` och `EXPLANATION` bara om varje påstående har minst ett fact_id. Kausala ord ("på grund av", "orsakades av") är bara tillåtna i `EXPLANATION` med variance_component-fakta, annars nedgraderas påståendet till `HYPOTHESIS`.

De fyra typerna från v1 §55 behålls och får ett tekniskt tvång i stället för att bara vara en UI-konvention.

### 7.3 Agenter och AI-uppgifter
Orkestreringen är **kod** (jobbkön), inte en LLM-"manager". Varje uppgift har en fast verktygsuppsättning, ett fast outputschema, en modellnivå och en egen eval-svit.

| # | Uppgift | Fas | Trigger | Input | Output | Verktyg | Modellnivå | Människa i loopen |
|---|---|---|---|---|---|---|---|---|
| A1 | **Mappningsassistent** | MVP | Nytt/okänt konto, ny kund | Kontonamn, BAS-intervall, exempeltexter, kundens bransch | Förslag: legal rad + management-kategori + konfidens + motivering | Inga (allt i input) | Liten/billig | Konsult bekräftar. Bekräftelse → byråmall |
| A2 | **Granskningstriage** | MVP | Nya fynd efter kontroller | Fynd + evidenspaket (relaterade verifikationer, historik för kontot) | Per fynd: sammanfattning, "troligen OK/utred/fråga kund", motivering med fact_ids | `get_voucher`, `get_account_history`, `get_similar_vouchers` (läs) | Mellan | Kan aldrig stänga fynd. High kan inte nedgraderas av AI |
| A3 | **Periodanalytiker** | MVP | Period "Needs review" | Förberäknat analyspaket (RR/BR, bryggor, topp-avvikelser, mognad) | Intern månadskommentar (claims) | Inga (paketet är komplett) | Stark | Konsult redigerar/godkänner |
| A4 | **Kundmötesagent** | MVP | Konsult klickar "Skapa kundunderlag" | Endast `CLIENT_SAFE`-fakta och -fynd + A3-utkast | 1-sidig sammanfattning + 3–7 frågor/råd till kunden | Inga | Stark | Konsult godkänner. Aldrig autoutskick |
| A5 | **Analytiker (Q&A)** | MVP-slut | Fri fråga i klientvyn | Fråga + klientkontext | Claims | Läsverktyg från v1 §51 + `get_maturity`, `list_changes_since` | Stark | Svaret är rådgivande. Budget: max N verktygsanrop och M tokens per fråga |
| A6 | **Motpartsresolver** | V1.5 | Ny leverantör/fritext | Leverantörsnamn, org.nr (från Fortnox), texter | Kanonisk motpart + alias + konfidens + spend-kategori | `search_counterparties` | Liten | Bekräftelse krävs över väsentlighetsgräns |
| A7 | **Portföljbrief** | V1.5 | Måndag morgon per konsult | Deterministisk prioriteringspoäng per klient + topp-fynd | "Veckans prioriteringar", 5–10 rader | Inga | Mellan | Informativ |
| A8 | **Dokumentagent** | V2 | Faktura-PDF | Dokument | Fakturahuvud och rader (strukturerat) | OCR/Document AI | Specialmodell | Matchningsförslag bekräftas |
| V | **Granskare (verifier)** | MVP | Efter A2–A5 | Output + evidenspaket | Pass/fail per claim + skäl | Deterministiska regler (§7.2) + LLM-bedömning för "kausalt påstående utan stöd" och "intern info i kundtext" | Mellan (annan prompt än producenten) | Fail → en omgenerering → annars visas utan de underkända påståendena |

**Varför inte fler agenter:** en separat "Spend agent", "Review agent" osv. blir bara verktygsuppsättningar till A5. Nya agenter läggs bara till när en ny *output-typ* behövs, inte för nya frågor.

### 7.4 Evidenspaket och dataminimering
- Varje uppgift får ett **förberäknat paket** från backend. Det är det enda uppgiften ser, utöver läsverktygen.
- Pseudonymisering före modellen: personnamn i verifikationstexter (NER + kundens anställdlista om den finns), personnummer (regex + Luhn), e-post och telefon ersätts med tokens. Servern återställer dem i UI:t.
- Paketen loggas (hash + innehåll med kort retention) så att varje AI-svar kan reproduceras vid klagomål.

### 7.5 Prompt injection och exfiltration
- All kunddata (verifikationstexter, fakturor) ligger i avgränsade datablock och behandlas aldrig som instruktioner (behålls från v1 §84).
- **Viktigare: inga utgående kanaler.** AI-uppgifterna har inga skriv-, mejl- eller webbverktyg. UI:t renderar **aldrig** markdown-bilder eller externa länkar från AI-output (strikt CSP, allowlist för länkar till egna routes).
- Verktyg är tenant-bundna på serversidan. AI:n kan inte ange `company_id`, den kommer från sessionen.

### 7.6 Leverantör, kostnad och kvalitet
- `ModelProvider`-abstraktion (v1 §74) behålls. Konfiguration per byrå: `provider, model_tier→model, region, retention_mode`.
- Krav på leverantör: EU-databehandling, ingen träning på data, noll eller kort retention i avtal, DPA. Kandidater att utvärdera: Azure-hostade modeller i Sweden Central/EU Data Zone, Anthropic Claude via EU-regioner hos molnleverantör, samt en EU-leverantör (t.ex. Mistral) som reserv. **Välj med evals (§10.3), inte på förhand.**
- Tre modellnivåer (liten/mellan/stark) med mappning i konfiguration, så att modellbyte blir en konfigurationsändring + eval-körning.
- Kostnadsbudget per byrå och månad, mätt per uppgift. Mål: AI-kostnad < 10 % av intäkten per klient-månad. Prompt-cache för stabila systemprompter, batch-API för nattliga uppgifter (A1, A2, A7).

---

## 8. Arbetsflöde, UX och rapporter

### 8.1 Periodstatus (behålls från v1 §61, med tillägg)
`Ej påbörjad → Data mottagen → Bearbetas → Preliminär (ofullständig) → Behöver granskning → Granskad → Godkänd → Rapporterad`, plus en automatisk övergång **"Ändrad efter godkännande"** när ändringsdiffen hittar nya eller ändrade verifikationer i en låst period.

### 8.2 Portföljvy (v1 §3) – prioriteringspoäng
Deterministisk poäng per klient: `High-fynd × vikt + ändring efter godkännande + marginalförändring över tröskel + dagar sedan senaste granskning + saknad data`. Poängens beståndsdelar visas alltid ("varför står den här kunden överst?").

### 8.3 Kvittens och REKO-stöd
- Varje fynd och varje godkänd period loggar vem, när, beslut och motivering.
- Export av "granskningsdokumentation per kund och period" (PDF) som byrån kan använda i sin kvalitetsdokumentation enligt REKO. Stäm av innehållet med en auktoriserad redovisningskonsult i pilotfasen.

### 8.4 Rapporter (v1 §66–68)
- Intern rapport och kundrapport behålls som separata dokumenttyper. Kundrapporten byggs **bara** av `CLIENT_SAFE`-fakta. Detta verifieras av granskaren (V) och av ett deterministiskt filter.
- Export: PDF + **Word** (konsulten vill redigera) + Excel-bilaga med siffror och verifikationsreferenser.
- Byråns logotyp och mallar.

### 8.5 Återkoppling som förbättrar systemet
- Varje "ACCEPTED_OK" med motivering blir kandidat till en suppression-regel eller en tröskeljustering för kunden.
- Varje korrigerat AI-förslag (A1, A2, A6) sparas som eval-exempel för den byrån (inte för träning, se v1 §77).

---

## 9. Juridik, säkerhet och förtroende (kompletterar v1 §76–84)

1. **Avtalskedja:** klientbolag (PUA) → byrå (biträde) → RedovisningAI (underbiträde) → moln- och AI-leverantörer (underbiträden i led 2). Publicera en underbiträdeslista, ha en färdig biträdesavtalsmall och ge byrån ett underlag den kan visa sina kunder.
2. **Penningtvättslagen:** synlighetsnivån `RESTRICTED_AML`, inga PTL-relaterade formuleringar i kundriktad output, och ingen AI-genererad "fråga kunden om detta" för sådana fynd. Produkten *indikerar* granskningsbehov och bedömer inte misstanke.
3. **AI Act:** tydlig märkning i UI:t av att användaren interagerar med AI och vilka texter som är AI-genererade (art. 50). Kort AI-kunnighetsmaterial för byråns personal (stöd för deras art. 4-ansvar). Systemet bedöms inte vara högrisk (ingen kreditbedömning av fysiska personer), men bedömningen dokumenteras.
4. **Enskilda firmor och lönedata:** §5.4 + pseudonymisering §7.4.
5. **Retention (v1 §81):** vi är *inte* bokföringsarkivet (det är ekonomisystemet), så vi behöver inte spara källdata i 7 år för BFL:s skull. Standard: källfiler 13–36 månader (konfigurerbart), låsta snapshots och granskningsdokumentation enligt byråns inställning, AI-traces ≤ 30 dagar.
6. **Molnsuveränitet (beslut att fatta):** alternativ A är Azure Sweden Central (bra ekosystem, enkel Entra, AI-modeller i regionen). Alternativ B är EU-ägt moln för data + AI via EU-region. Rekommendation: **A för MVP**, containeriserat och portabelt. Frågan tas upp i pilotintervjuerna, och om större byråer kräver B är migreringen förberedd.
7. **Certifiering:** ISO 27001 som mål inom 18–24 månader. Större byråkedjor kommer att fråga. Bygg loggning och åtkomstkontroll så att revisionen blir enkel.
8. **Break-glass (v1 §83):** behålls.

---

## 10. Testning (kompletterar v1 §94–97)

1. **SIE-korpus:** minst en fil per exportör (Fortnox, Visma eEkonomi, BL, Hogia, Visma Administration, Bokio m.fl.), brutna räkenskapsår, PC8-kodning, #RTRANS/#BTRANS, 100k+ transaktioner. Hämtas från pilotbyråer (med biträdesavtal) + syntetiska filer.
2. **Golden tests (v1 §95):** facit från konsult för RR/BR/nyckeltal. Rapporten jämförs mot Fortnox egen RR/BR-utskrift för samma period, och skillnader måste vara 0,00 kr.
3. **AI-evals per uppgift:**
   - A1: träffsäkerhet på mappning mot konsultfacit.
   - A2: andel falsklarm korrekt märkta "troligen OK" utan att riktiga fel missas (recall på verkliga fel = 100 % krav på testsviten).
   - A3/A4/A5: 0 renderade sifferfel (konstruktionsmässigt), andel otillåtna kausala påståenden, andel interna fakta i kundtext = 0.
   - Adversariella tester: injektionstexter i verifikationer, försök att få ut data från annan klient.
4. **Säkerhet (v1 §97):** behålls + test att PAYROLL-rader aldrig når AI-paketet, och att `RESTRICTED_AML` aldrig når A4 eller kundexport.

---

## 11. Affärsmodell och mätetal (saknades i v1)

- **Pris (hypotes att testa i Fas 0):** per aktiv klient och månad, med trappa efter volym. Minimiavgift per byrå. Rådgivningsfunktioner (A4, spend) som ett högre paket byrån kan vidarefakturera.
- **Kostnadsbild per klient-månad:** hosting + AI + ev. API-avgift hos ekonomisystemet (t.ex. Vismas integrationstillägg). Räkna på det innan prissättning.
- **Kanaler:** Fortnox integrationsmarknad, Srf konsulternas nätverk och evenemang, mindre byråkedjor, direktförsäljning till byråer med 5–30 anställda.
- **Nordstjärnemått:** antal *granskade klient-månader* per vecka.
- **Stödmått:** tid per granskning, andel High-fynd som leder till åtgärd, andel AI-förslag som accepteras oförändrade, kundmötesunderlag som faktiskt skickas, retention per byrå.

---

## 12. Reviderad byggordning (ersätter v1 §107)

Varje fas har ett **exit-kriterium**. Man går inte vidare förrän det är uppfyllt.

### Fas 0 – Validering (4–6 veckor)
- Intervjua 8–10 byråer (konsult + byråledare). Hur ser månadsgranskningen ut idag, vad tar tid och vad skulle de betala för?
- Rekrytera **3–5 pilotbyråer** med biträdesavtal.
- Bygg ett **CLI-skript**: SIE4 → parser → 10 kontroller + RR/BR/R12 → Excel + 1-sidig PDF med AI-kommentar (§7.2-principen redan här). Kvalitetssäkra manuellt och leverera varje månad.
- **Exit:** ≥ 3 byråer säger att de skulle betala en angiven summa, och vi vet vilka 10 kontroller de värderar högst.

### Fas 1 – Kärna (≈ 8–10 veckor)
1. Repo, Docker Compose (Postgres, MinIO), FastAPI, Next.js, CI (lint, typer, tester)
2. Organisation, användare, klient, tre roller, RLS + säkerhetstester från dag ett
3. SIE4-parser (härdad från Fas 0) + filuppladdning + originalfil med SHA-256
4. **Fortnox-connector** (OAuth, SIE4 per räkenskapsår, nattlig synk, rate limiting)
5. Verifikationsversionering + ändringsdiff + `account_period_balance`
6. Periodmotor + legal uppställning (RR/BR) + golden tests mot Fortnox-utskrifter
7. Periodmognad
- **Exit:** pilotbyråernas kunder synkas automatiskt, och RR/BR stämmer på öret för alla golden-dataset.

### Fas 2 – Granskning (≈ 6–8 veckor)
8. Fakta/lineage + metric-registret + kärnnyckeltal
9. De 25 kontrollerna (§4.3) + fynd-livscykel + suppression
10. Klientvy: översikt, RR/BR, månad/YTD/R12, drilldown till verifikation, Excel-export
11. Portföljvy med prioriteringspoäng
12. AI: A1 (mappning) + A2 (triage) + verifier V + eval-svit
- **Exit:** pilotkonsulter gör sin månadsgranskning i produkten i stället för i Fas 0-rapporten, och andelen High-fynd som leder till åtgärd är ≥ 50 %.

### Fas 3 – Rådgivning och rapport (≈ 4–6 veckor)
13. Variansbrygga + kategori-drilldown
14. A3 (periodanalytiker) + A4 (kundmötesagent) + rapporter PDF/Word
15. Analysis Snapshot + "ändrad efter godkännande"
16. A5 (Q&A) med verktygsbudget
17. Revisionslogg, AI-märkning, säkerhetshärdning, penetrationstest
- **Exit:** första betalande byrå. Tidsbesparing ≥ 30 % uppmätt.

### Fas 4 – V1.5
- Fortnox leverantörs- och kundfakturor → **Spend Intelligence** (v1 §36–47 i sin helhet), A6 motpartsresolver, reskontraavstämning, budget mot utfall (#PBUDGET), A7 portföljbrief, branschmallar.

### Fas 5 – V2+
- Visma API, SIE5, fakturadokument (A8) + matchning, Entra SSO, bank, kundportal (BankID), prognoser. Portföljbenchmarking inom byrån bara med aggregerad data och uttryckligt stöd i avtalen.

---

## 13. Öppna beslut

| Beslut | Rekommendation | När |
|---|---|---|
| Kil: granskning först eller rådgivning först? | Granskning först | Bekräftas i Fas 0-intervjuer |
| Molnleverantör | Azure Sweden Central, portabelt | Före Fas 1 |
| AI-leverantör | Välj efter evals på Fas 0-data, med minst två leverantörer bakom abstraktionen | Slutet av Fas 0 |
| Prismodell | Per aktiv klient/månad | Fas 0 |
| Fortnox-partnerskap | Ansök tidigt | Fas 0–1 |
| Juridisk granskning (biträdesavtal, PTL-hantering, AI Act-bedömning) | Extern jurist, en gång, före första betalande kund | Fas 2 |

---

## 14. Källor och verifiering

Snabbkontroller gjorda vid revideringen (september 2026). Detaljer ska verifieras mot primärkällor innan de byggs in:
- Fortnox API, SIE-resursen (`/3/sie/4`, parametern `financialyear`): <https://developer.fortnox.se/documentation/resources/sie/>
- Fortnox Insikter (avvikelsebevakning för byråer): <https://www.fortnox.se/produkt/insikter>
- Fortnox integrationer för rapportering och analys: <https://www.fortnox.se/integrationer/kategorier/rapportering-analys>
- Visma eEkonomi SIE4-export och API-tillägg för byrå: <https://bokforingssystem.se/faq/visma-eekonomi-byra/vad-kostar-det-att-anvanda-api-fran-visma-eekonomi-byra>
- AI Act art. 50, tillämplig från 2 augusti 2026: <https://artificialintelligenceact.eu/article/50/>
- Svenska lagkrav (BFL, ABL 21 och 25 kap, PTL, momsregler, arbetsgivaravgifter) och BAS-kontointervall är domänkunskap som **måste stämmas av med en auktoriserad redovisningskonsult** innan kontrollerna släpps till kund.
