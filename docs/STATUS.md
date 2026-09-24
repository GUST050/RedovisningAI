# Status: plan mot implementation

Den här sidan visar hur [PLAN.md](PLAN.md) har implementerats. Den listar också det som inte går
att bygga i kod: avtal, intervjuer, certifikat och granskningar som måste göras av människor.

Teckenförklaring: ✅ klart och testat · 🟡 delvis · ⏳ inte byggt ännu · 👤 kräver åtgärd utanför koden

## Fas 1 – Kärna

| # | Del | Status | Var |
|---|---|---|---|
| 1 | Repo, Docker Compose (Postgres, MinIO, PgBouncer), FastAPI, Next.js, CI | ✅ | `docker-compose.yml`, `.github/workflows/ci.yml`. Hela stacken är verifierad i Docker med webbläsartest. |
| 2 | Organisation, användare, kunder, roller, RLS | ✅ | `db/models.py`, `migrations/0002`. RLS med FORCE; läckagetest genom PgBouncer. |
| 3 | SIE4-parser, filuppladdning, massuppladdning (zip) | ✅ | `sie/parser.py`, `POST /imports`, `POST /imports/bulk` (kopplas via orgnr) |
| 4 | Fortnox-koppling | 🟡 | `connectors/fortnox.py`: servicekonto, SIE4 per år, hastighetsgräns, nattlig synk, kopplingshälsa. Testad mot simulerat API, inte mot Fortnox sandbox (👤 kräver utvecklarkonto). |
| 5 | Verifikationsversionering, ändringsdiff, `account_period_balance` | ✅ | `db/repo.py` |
| 6 | Periodmotor, RR/BR, golden tests | ✅ | `accounting/`, `tests/fixtures/golden_small.se` |
| 7 | Periodmognad | ✅ | `maturity/assess.py` |
| 8 | Regelkatalog och satser (2026) | ✅ | `rules/catalog/*.toml` med lagstöd, version och giltighetsdatum (t.ex. matmoms 6 % 2026-04-01–2027-12-31, sänkt AGA för unga). 👤 Katalogen ska granskas av auktoriserad redovisningskonsult. |

## Fas 2 – Granskning

| # | Del | Status | Var |
|---|---|---|---|
| 9 | Fakta med id och härkomst, nyckeltalsregister | ✅ | `facts/model.py`, `accounting/metrics.py` |
| 10 | 25 kontroller, bokslutsmönster, fyndens livscykel, undertryckning, precision | ✅ | `rules/controls.py`, `findings/lifecycle.py`, sidan *Regelhälsa* |
| 11 | Kundvy med drilldown och Excel-export | ✅ | `apps/web/app/clients/[id]` |
| 12 | Portföljvy med prioriteringspoäng och kopplingshälsa | ✅ | `portfolio/score.py`, startsidan. Filter per system (inte per konsult ännu). |
| 13 | A1 + A2 (ärendebyggare), kundminne, verifierare, evals, EU-leverantör med failover | ✅ | `ai/`, `cases/builder.py`, `memory/`. 👤 Evals mot riktig modell kräver nycklar till Bedrock/Vertex i EU-region. |

## Fas 3 – Rådgivning, rapport, härdning

| # | Del | Status | Var |
|---|---|---|---|
| 14 | Variansbrygga, kategoridrilldown, budget mot utfall | ✅ | `accounting/variance.py`, `analytics/budget.py` |
| 15 | A3, A4, rapporter (PDF/Word), Reko-dokumentation, kundfrågor med säker länk | ✅ | `reports/`, publik sida `/q/[token]`. Kundrapporten tar bara med godkänt mötesunderlag. |
| 16 | Ögonblicksbild vid godkännande, "ändrad efter godkännande" | ✅ | `review/workflow.py`, `jobs/pipeline.py` |
| 17 | A5 AI-analytiker med läsverktyg och budget | ✅ | `ai/tools.py`, fliken *AI-analytiker* |
| 18 | Händelselogg, AI-märkning, CSP | ✅ | Loggen går inte att ändra i databasen. 👤 Penetrationstest utförs av extern part. |
| 19 | Fortnox appgranskning och listning | 👤 | Kräver partneravtal och granskning hos Fortnox |

## Fas 4 – V1.5

| Del | Status | Kommentar |
|---|---|---|
| Spiris API | ⏳ 👤 | Gränssnitt finns (`connectors/other.py`). Kräver partneravtal. Tills dess: SIE4-export från Spiris. |
| Skatteverkets Skattekonto-API | 🟡 👤 | Avstämning mot konto 1630 fungerar med CSV-utdrag. API:t kräver organisationscertifikat och ombudsbehörighet. |
| PTL-modul | ✅ | 3 signaler, egen behörighet, riskbedömning, CSV-export till KYC-verktyg. Ingen AI och inget visas för kund. |
| Spend Intelligence | 🟡 | Motparter, återkommande kostnader, nya kostnader, prisförändringar och koncentration från bokföringen. Hämtning av leverantörsfakturor finns i Fortnox-kopplingen men används inte i analysen ännu. A6 (motpartsnormalisering) är byggd och utvärderad men inte inkopplad i flödet. |
| Reskontraavstämning och åldersanalys | ⏳ | |
| A7 portföljbrief, A8 regelbevakare | ✅ | Startsidan respektive *Regler och inställningar → Regelbevakning*. Förslag från A8 måste granskas av människa. |
| Branschmallar | ⏳ | |

## Fas 5 – V2+

⏳ Björn Lundén API, SIE 5, fakturadokument (A9), ML-rangordning av fynd, bank, kundportal med
BankID, prognoser. Inloggning via OIDC finns (fungerar med Entra ID). 👤 ISO 27001.

## Tvärgående krav

| Krav (plan §) | Status |
|---|---|
| Siffror renderas av servern, AI använder fakta-id (§9.2) | ✅ Verifieraren avvisar egna tal, okända fakta-id och orsakspåståenden utan stöd. |
| Dataminimering och pseudonymisering (§9.4) | ✅ Personnamn maskeras, lönerader och PTL skickas aldrig till AI (testat). |
| Prompt injection och exfiltration (§9.5) | ✅ Data kapslas in som data, länkar och bilder blockeras i AI-text, AI-text visas som ren text, strikt CSP. |
| Leverantörer, regioner, kostnadstak (§9.6) | ✅ Bedrock/Vertex/Anthropic, failover, tokenbudget per byrå och månad, reservmodell vid avböjt svar. |
| AI-förordningen art. 50 (§11.4) | ✅ Märkning *AI-genererad* eller *Regelbaserad text* överallt där text visas. |
| Retention (§11.8) | ✅ Jobb som rensar AI-spår (30 dagar) och källfiler (konfigurerbart). |
| Break-glass för support (§11.9) | ⏳ |
| Krypterad lagring per byrå | ✅ En datanyckel per byrå, skyddad av huvudnyckel (KMS/Key Vault i produktion). |
| Produktionsskydd | ✅ API:t startar inte i produktionsläge med utvecklingsinloggning, standardnycklar eller standardlösenord. |

## Testning (§12)

- 122 automatiska tester: parser (inkl. CP437, #RTRANS/#BTRANS, brutna räkenskapsår), golden
  tests för RR/BR, regler med datumgränsfall, fyndlivscykel, AI-verifierare och
  pseudonymisering, RLS (byrå/kund/lön/PTL/läsare/publik länk), PgBouncer-läckagetest, API och
  kopplingar.
- AI-evals: `redovisningai eval` (sifferfel, avvisade påståenden, läckage till kundtext,
  mappningsträffsäkerhet).
- Webben: end-to-end i webbläsare (portfölj, alla flikar, kundfråga med svar via publik länk,
  beslut, godkännande med motivering, rapportnedladdning, SIE-uppladdning, läsarbehörighet).
- 👤 SIE-korpus med riktiga filer från fler exportörer (Spiris, BL, Hogia, Bokio) behövs från
  pilotbyråerna. Syntetiska demobolag används tills dess.

## Kvar utanför koden (Fas 0 m.m.)

1. 👤 8–10 intervjuer och 3–5 pilotbyråer med biträdesavtal (plan §14 Fas 0, §16).
2. 👤 Juristgranskning av Fortnox utvecklar- och partneravtal, samt biträdesavtalsmall och
   underbiträdeslista (§11.1–2).
3. 👤 Regelkatalogen granskas av auktoriserad redovisningskonsult (§1.4).
4. 👤 AI-leverantör: avtal och nycklar i EU-region, samt evals på pilotdata.
5. 👤 Driftmiljö (t.ex. Azure Sweden Central): hanterad PostgreSQL, objektlagring, Key Vault,
   Entra ID-app, övervakning och säkerhetskopiering.
6. 👤 Penetrationstest före första betalande kund.
