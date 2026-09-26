# RedovisningAI

Granskningsverktyg för redovisningsbyråer. Verktyget läser kundernas bokföring (SIE4-fil eller
Fortnox), räknar fram resultat- och balansräkning och nyckeltal, kör svenska kontroller och
grupperar fynden till ett fåtal ärenden per kund och månad. AI används för text (kommentarer,
kundfrågor, mötesunderlag) och för att föreslå grupperingar. AI räknar aldrig själv: alla
siffror kommer från beräkningsmotorn och verifieras innan de visas.

- **Plan:** [docs/PLAN.md](docs/PLAN.md)
- **Funktionerna förklarade:** [docs/FUNKTIONER.md](docs/FUNKTIONER.md)
- **Vad som är byggt och vad som återstår:** [docs/STATUS.md](docs/STATUS.md)

## Struktur

```
services/api/     Python 3.11+: SIE-parser, ekonomimotor, kontroller, AI-lager, FastAPI, jobb, CLI
  src/redovisningai/
    sie/          SIE4-parser (PC8/CP437, #RTRANS/#BTRANS, #PSALDO/#PBUDGET) och skrivare
    accounting/   perioder, saldon, RR/BR (K2/K3), nyckeltal, variansbrygga, kostnadsträd
    maturity/     periodmognad (fakturametod/kontantmetod, periodiseringar, fullständighet)
    rules/        regelkatalog (TOML, med lagstöd och giltighetsdatum), satser, 25 kontroller, PTL-signaler
    findings/     fyndens livscykel, undertryckning, precision per regel
    cases/        ärendebyggare (grupperar fynd efter trolig grundorsak)
    memory/       kundminne (tidigare bedömningar som förslag)
    ai/           leverantörer (Claude via Bedrock/Vertex/Anthropic samt OpenAI + failover), uppgifter A1–A8, verifierare,
                  pseudonymisering, analytikerns läsverktyg, evals
    db/           SQLAlchemy-modeller, radnivåskydd (RLS), versionerad import
    api/          FastAPI
    jobs/         import- och granskningspipeline, bakgrundsarbetare (Procrastinate), retention
    connectors/   Fortnox (servicekonto, SIE4 per år, hastighetsbegränsning)
    reports/      PDF, Word och Excel (kundrapport, internrapport, Reko-dokumentation)
  migrations/     Alembic (schema, RLS-policyer, funktioner)
  tests/          API-/databastester, bl.a. RLS-läckage genom PgBouncer i transaktionsläge
apps/web/         Next.js 16: portfölj, kundens arbetsyta, publik svarssida, regler och inställningar
docker-compose.yml, .github/workflows/ci.yml
```

## Kom igång på Mac (ett kommando)

```bash
./scripts/start-mac.sh
```

Skriptet startar Docker Desktop vid behov, skapar `.env` med slumpade hemligheter, bygger och
startar allt, lägger in demodata och öppnar http://localhost:3000.

Om Docker Desktop inte startar eller bygget ger `read-only file system`:

```bash
./scripts/fix-docker-mac.sh
```

Det kontrollerar först orsaken (oftast för lite ledigt utrymme på Macen) och installerar sedan om
Docker Desktop rent och testar att både körning och bygge fungerar.

## Kom igång med Docker

```bash
cp .env.example .env        # byt lösenord och RAI_MASTER_KEY
docker compose up -d --build
docker compose run --rm api redovisningai seed-demo
```

Öppna http://localhost:3000 – du är automatiskt inloggad som `anna@demobyran.se` (byråadmin),
ingen inloggningssida. Vill du testa som läsare: http://localhost:3000/login → `lisa@demobyran.se`.
Demobyrån har fyra kunder med inplanterade fel: felaktig matmoms, lucka i nummerserie,
dubbelbokning, saknade löner, förfallen periodiseringsfond m.m.

Den automatiska inloggningen finns bara i utvecklingsläge (`RAI_AUTH_MODE=dev`, användaren styrs
av `RAI_DEV_DEFAULT_USER`). I produktion krävs `RAI_ENV=prod` och
`RAI_AUTH_MODE=oidc` (t.ex. Microsoft Entra ID). API:t vägrar starta med standardnycklar,
standardlösenord eller utvecklingsinloggning i produktionsläge.

## Utveckling utan Docker

Krav: Python 3.11+, Node 22, PostgreSQL 16 (och gärna PgBouncer för det testet).

```bash
cd services/api
python -m venv .venv && .venv/bin/pip install -e ".[ai,worker,dev]"
scripts/dev-postgres.sh start               # lokal PostgreSQL på port 54329
.venv/bin/redovisningai migrate
.venv/bin/redovisningai worker-schema       # jobbkön för bakgrundsarbetaren
.venv/bin/redovisningai seed-demo
.venv/bin/uvicorn redovisningai.api.app:app --reload --port 8000

cd ../../apps/web
npm install && npm run dev                  # http://localhost:3000, /api proxas till :8000
```

Tester och kontroller (samma som i CI):

```bash
cd services/api
.venv/bin/ruff check src tests && .venv/bin/ruff format --check src tests
.venv/bin/mypy
.venv/bin/pytest
.venv/bin/redovisningai eval              # AI-evals (utan riktig modell används en fejkad leverantör)
cd ../../apps/web && npm run typecheck && npm run build
```

## Fas 0-verktyget (utan databas)

```bash
redovisningai demo-sie --out demo/
redovisningai analyze demo/Bygg_och_Co_AB_*.se --out rapport/
```

Skriver Excel med RR/BR, ärenden och fynd, en intern PDF och ett utkast till kundrapport.

## AI

AI är avstängt som standard och allt fungerar utan AI (regelbaserade texter som märks som
sådana). Slå på med `RAI_AI_ENABLED=true` och välj plattform:

| `RAI_AI_PLATFORM` | Behöver | Kommentar |
|---|---|---|
| `bedrock` | AWS-nycklar, `RAI_AI_REGION` (t.ex. `eu-north-1`) | Data stannar i vald AWS-region |
| `vertex` | GCP-projekt (`RAI_AI_PROJECT_ID`), region | Data stannar i vald GCP-region |
| `anthropic` | `ANTHROPIC_API_KEY` | Direkt mot Anthropic |
| `openai` | `OPENAI_API_KEY` | OpenAI Responses API; sätt rätt projekt och datavillkor innan riktiga kunddata används |

För en försiktig provkörning: sätt `RAI_AI_PLATFORM=openai` och `OPENAI_API_KEY` i din lokala
`.env`, eller `RAI_AI_PLATFORM=anthropic` och `ANTHROPIC_API_KEY` för Claude. Sätt
`RAI_AI_ENABLED=true` och starta om API och worker (`docker compose up -d --build api worker`).
Hemligheterna ska finnas på servern, aldrig i webbläsaren, Git eller loggar. Du kan lägga in
båda nycklarna och välja primär leverantör med `RAI_AI_PLATFORM`. I full drift kan du sätta
`RAI_AI_SECONDARY_PLATFORM` till den andra leverantören; byte sker bara vid tillfälligt fel.
För Claude i AWS/GCP används i stället `bedrock`/`vertex` och deras respektive behörigheter.

`RAI_AI_TEST_MODE=true` är standard. Det begränsar AI till ett utkast utan omskrivning eller
automatiska SDK-omförsök per uppgift, 1 500
utdata-tokens per modellbegäran, tre läsverktygsanrop och högst 20 000 bokförda tokens per
byrå och månad (eller byråns lägre budget). Analytikerns verktygsloop kan fortfarande göra
flera modellbegäranden. Automatisk reservleverantör är avstängd i testläget.
Med `RAI_AI_TEST_MODEL` (t.ex. `gpt-6-luna`) körs alla nivåer på en billig modell så länge
testläget är på; i full drift används de vanliga modellinställningarna igen.
Ändra gränserna med `RAI_AI_TEST_*`. För full drift, sätt `RAI_AI_TEST_MODE=false` först efter
egen utvärdering. Tokenbudgeten mäts efter ett svar och är därför **inte** ett absolut
kostnadstak vid samtidiga anrop. Sätt också projektets utgiftsgräns hos leverantören.
Testa först på syntetiska SIE-filer; en API-nyckel innebär inte att data skickas förrän AI
aktiveras och en AI-funktion körs.

`RAI_AI_SECONDARY_PLATFORM` ger failover i full drift. Personnamn maskeras innan anrop, lönerader och
PTL-signaler skickas aldrig och varje påstående kontrolleras mot beräknade fakta innan det visas.
AI-genererad text märks i gränssnittet (AI-förordningen art. 50).
`store=false` används på OpenAI-anrop, men leverantörens övriga databehandling och eventuell
EU-datalokalisering beror på projektets avtal och inställningar – verifiera detta separat.

## Säkerhet i korthet

- Radnivåskydd (RLS) i PostgreSQL på alla kunddatatabeller; applikationen kör som en roll utan
  BYPASSRLS. Behörigheten sätts per transaktion och är testad genom PgBouncer i transaktionsläge.
- Konsulter ser bara tilldelade kunder. Lönerader och PTL-signaler kräver egna behörigheter.
- Källfiler och bilagor krypteras med en nyckel per byrå. Händelseloggen går inte att ändra.
- Kundens svarslänk är personlig, tidsbegränsad och ger bara åtkomst till den enskilda frågan.
