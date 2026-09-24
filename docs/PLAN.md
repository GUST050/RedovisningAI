# RedovisningAI – produkt- och arkitekturplan (v3.1, researchbaserad)

> **Historik**
> - v1: ursprunglig plan (ChatGPT).
> - v2: första revidering (svagheter i domän, AI-design, juridik, byggordning).
> - v3: egen research om marknad, konkurrenter, API:er, svensk lag, AI-leverantörer och säkerhet (september 2026). Flera antaganden i v2 visade sig vara fel eller svaga och har ändrats. Se §0.
> - **v3.1 (denna):** kontroll av v3:s differentiering. Skattekontoavstämning, PTL/KYC och Reko-checklistor finns redan hos andra. Differentieringen är omarbetad i §3.1: ärenden i stället för signaler, kundminne och kundfrågeloop.
>
> Paragrafhänvisningar som "(v1 §69)" syftar på numreringen i den ursprungliga planen. Källor finns i §17.

---

## 0. Vad researchen ändrade

| # | Antagande i v1/v2 | Vad researchen visar | Ändring i v3 |
|---|---|---|---|
| 1 | Svenska granskningskontroller (moms, förbrukat aktiekapital, kostnadsavvikelser) är vår differentiering | **Fortnox Insikter har redan** kostnadsavvikelser, momsavvikelser (ingående moms), omsättningstrend (>30 % mot föregående år/R12), kostnadstrend, förbrukat aktiekapital och EU-handel, gratis inne i Fortnox byråmiljö | Kontroller är **hygien, inte differentiering**. Differentieringen flyttas. **Se §3.1 (v3.1)**, där PTL och skattekonto inte längre räknas som unika |
| 2 | Fortnox räcker som första integration | Fortnox har ~37 % av marknaden (585 000 kunder, Q3 2024). **Resten av en typisk byrås kunder ligger i andra system** (Spiris, Björn Lundén m.fl.) | MVP = Fortnox-API **+** fullvärdig SIE4-filväg. **Spiris API** i V1.5 i stället för V2 |
| 3 | Fortnox är en neutral plattform | Fortnox ägs sedan 2025 av EQT/Hallrup och är avnoterat. **Fortnox har lanserat BLINK, en egen bokföringstjänst med anställda konsulter** som konkurrerar med byråerna | Positionera som **byråns oberoende verktyg**. Plattformsrisken är högre än i v2: SIE-filvägen måste alltid fungera fullt ut |
| 4 | Integrationen är gratis | Fortnox: API-licens för klient på byråpaket ca **59 kr/mån** (direktmodellen), eller en marknadsplatsmodell där kostnaden läggs på kundens Fortnox-faktura utan separat licens. Spiris: API-tillägg ca **69 kr/mån** | Enhetsekonomin måste räkna med detta. **Välj Fortnox marknadsplatsmodell** och bekräfta villkoren i Fas 0 |
| 5 | Nattlig synk av 300 klienter är enkel | Fortnox **service accounts** (client credentials + TenantId) ger obevakad åtkomst, men **varje klientbolag måste auktoriseras av en administratör**. Rate limit 25 anrop/5 s per klient-id och tenant | Bygg ett **massonboardingflöde** (auktoriseringslänkar per klient, status per koppling). SIE4-endpointen (`/3/sie/4?financialyear=`) ger ett helt år i ett anrop |
| 6 | Skattekontot kan bara flaggas | **Skatteverkets Skattekonto-API** (v2.0) ger saldo och transaktioner. Byrån kan få läsbehörighet som ombud via "Ombud och behörigheter". Kräver organisationscertifikat | Avstämning 1630 ↔ skattekonto i V1.5. **v3.1:** Fortnox har redan en gratis Skatteverket-koppling som läser in skattekontotransaktioner (avstämningen i "Stäm av konto" är dock manuell), och det finns tredjepartsappar. Detta är **hygien, inte differentiering** |
| 7 | Svenska regler är statiska | Flera regler ändrades 2026: **matmoms 6 %** (1 apr 2026 – 31 dec 2027), **sänkta arbetsgivaravgifter för unga** (20,81 %, 1 apr 2026 – 30 sep 2027, lön upp till 25 000 kr/mån). **Kontrollbalansräkningen föreslås slopas** (SOU 2023:34, status oklar) | Ny komponent: **regelkatalog med lagstöd och giltighetsperioder** (§5). Ingen skattesats eller procentgräns hårdkodas |
| 8 | PTL är bara en risk att hantera | Redovisningskonsulter utgör ~90 % av verksamhetsutövarna under länsstyrelsernas PTL-tillsyn. Myndigheterna släppte 2025 en vägledning med ~90 varningssignaler. Rapportering sker via goAML | PTL-signaler ur bokföringen i V1.5. **v3.1:** KYC och riskbedömning finns redan (Visma Advisor KYC, Björn Lundéns Lundify). Vi bygger **bara transaktionssignaler** och exporterar till byråns KYC-verktyg, inget eget KYC-system |
| 9 | "Välj AI-leverantör efter eval" | **Claude via Anthropics eget API har i dag ingen EU-inferens** (bara `us`/`global`). EU-körning av Claude kräver AWS Bedrock eller Google Vertex i EU-region. Azure OpenAI i Sweden Central har EU Data Zone men hade **ett långt avbrott 27 jan 2026**. Mistral är EU-bolag, men noll datalagring (ZDR) kräver Scale-plan (annars 30 dagars lagring) | Krav: **två EU-hostade leverantörer i olika regioner** bakom abstraktionen, med automatisk failover. Ingen global routning |
| 10 | LLM:er kan kontrolleras i efterhand | Forskning visar att LLM:er klarar enkla uppslag men **faller kraftigt på flerstegsberäkningar** i finansdata. FinanceBench: GPT-4-Turbo med retrieval svarade fel eller vägrade på 81 % av frågorna | Bekräftar v2:s beslut: **AI:n skriver aldrig siffror**, servern renderar dem |
| 11 | Falsklarm hanteras med suppression | Forskningen om journal entry testing: regelbaserade röda flaggor ger **många falsklarm från återföringar, avsättningar och bokslutsposter**. Hybridmodeller (regler + ML-rangordning) förbättrar precisionen | Mät **precision per regel**. Bokslutsmönster känns igen före larm. ML-rangordning på byråns egen feedback i V2 |
| 12 | AI Act art. 4 kräver "tillräcklig AI-kunnighet" | Digital Omnibus (förordning (EU) 2026/1744, i kraft 27 juli 2026) mjukade upp art. 4 till en insatsplikt ("stödja utvecklingen av"). Art. 50 (transparens) gäller sedan 2 aug 2026 | Krav i §11 uppdaterade |
| 13 | Prissättning "per klient" | Priset för löpande bokföring av ett enmansaktiebolag uppges ha sjunkit från ~800 till ~500–600 kr/mån (2020→2026, uppgift från en AI-bokföringsaktör, alltså partisk källa). AI-byråer (Wint m.fl.) pressar priserna. Syft tar $19–119 per bolag/mån | Priset måste vara en **liten andel av byråns intäkt per kund** och motiveras med sparad tid (§13). Förväntad nivå: 39–99 kr per aktiv klient/mån |

**Oförändrat från v1/v2:**
- Multi-tenancy runt byrån.
- Deterministisk ekonomimotor. AI är aldrig sifferfacit.
- Originalfiler bevaras med SHA-256.
- Versionerade dataset och mappningar. Decimal/NUMERIC. Statusvärden i stället för `null = 0`.
- Review Priority i stället för Fraud Score.
- Typade AI-verktyg utan fri SQL.
- Intern rapport skild från kundrapport.
- Modulär monolit: Python/FastAPI + Next.js + PostgreSQL med RLS.
- Analysis snapshots och golden tests.
- Från v2: fact_id-renderade siffror, verifikationsversionering, periodmognad, fyndlivscykel, tre roller, Postgres-kö, Fas 0-validering.

---

## 1. Researchunderlag – fakta och konsekvenser

### 1.1 Marknad
| Fakta | Konsekvens för produkten |
|---|---|
| Srf konsulterna har ~6 500 medlemmar som arbetar med lön och redovisning åt ~330 000 företag | En Srf-kanal når en stor del av marknaden. Srf och FAR (Reko) är viktiga för trovärdigheten |
| Byråer med färre än tio anställda står för över 60 % av branschens omsättning (>17 mdr kr). Ingen tydlig konsolidering | Målgruppen är **många små byråer**: enkel onboarding, självbetjäning, låg lägstanivå på priset, ingen tung SSO/upphandling i början |
| Största kedjor: Aspia (~1 550 anst.), Ludvig & Co (~1 000 anst., 45 000 kunder), Azets, Klara | Senare segment med krav på SSO, ISO 27001 och DPA-förhandling. Inte MVP-kunder |
| Fortnox: 585 000 kunder, ~37 % andel (Q3 2024); ~9 000 byråer arbetar i Fortnox | Fortnox-integration är nödvändig men täcker inte en hel byråportfölj |
| Visma eEkonomi heter nu **Spiris Bokföring & Fakturering**, företaget Visma Spiris AB. Visma Advisor har en AI-assistent | Använd de nya namnen i UI och marknadsföring. Spiris är nästa integration |
| Srf varnar för brist på redovisningskonsulter | Argumentet "fler kunder per konsult" väger tyngre än "sälj mer rådgivning" |

### 1.2 Konkurrens
| Aktör | Vad de gör | Vårt svar |
|---|---|---|
| **Fortnox Insikter** (Digital Byrå) | Kostnadsavvikelser (konto 4000–7999, statistiskt förväntat värde), momsavvikelser (ingående moms mot kostnad), omsättnings- och kostnadstrend, förbrukat aktiekapital, EU-handel. Gratis för Fortnox-byråer | Konkurrera inte på samma kontroller. Täck **alla system**, ge **evidens, arbetsflöde och dokumentation**, och **ta in Fortnox-insikter som signal** om det går |
| Fortnox AI-assistent (lanseras successivt 2026), Fortnox Access, BLINK | AI-frågor i Fortnox. BLINK = Fortnox egen byråtjänst | "Chatta med bokföringen" är ingen differentiering. BLINK gör att byråer har skäl att vilja ha ett oberoende verktyg |
| Spiris / Visma Advisor | Byråstöd, AI-assistent för programfrågor | Samma svar som för Fortnox |
| MCP-bryggor (Nordsynk, Klartext m.fl.) | Byrån frågar ChatGPT/Claude direkt mot Fortnox | Saknar deterministiska siffror, evidens, portfölj och revisionsspår. Tydligt argument i säljet |
| Rapportverktyg (Business Board, Fortnox Rapport & Analys) | Dashboards, budget, rapporter | Vi granskar och förklarar. Budget mot utfall läggs till via #PBUDGET |
| AI-byråer (Wint, Accounted m.fl.) | Automatiserad bokföring med människor där det behövs | De pressar priserna. Vi säljer till traditionella byråer som behöver bli effektivare för att klara prispressen |
| Internationellt (Syft, MindBridge m.fl.) | Rapportering / anomalidetektion över huvudbok | Saknar BAS, SIE, svensk moms, AGA, ABL, PTL och svenskt arbetsflöde |
| Fortnox Byråstöd, WeSoft Byråstöd m.fl. | Checklistor, tidrapportering, resursplanering, löpande avstämningar och dokumentation (Fortnox: "stöder Reko och Rex, men inte fullt ut än") | Checklistor och Reko-dokumentation är **inte unika**. Vi kopplar dokumentationen till fynd och evidens i stället för till checkrutor |
| Fortnox Skatteverket-koppling, JSI Skattekonto | Skattekontots transaktioner läses in, automatisk bokföring via regler | Skattekontoavstämning är **inte unik**. Vi erbjuder den för icke-Fortnox-kunder och inom granskningsflödet |
| Visma Advisor KYC, Lundify (Björn Lundén) | PTL: riskbedömning, kundkännedom, bevakning, avvikelser, rapportering | Vi bygger inget KYC-system, bara transaktionssignaler som matar deras verktyg |

### 1.3 Data och API:er
| Fakta | Konsekvens |
|---|---|
| Fortnox `/3/sie/4?financialyear=` ger kontoplan, IB/UB och alla transaktioner, ca 2 s per helår | Primär hämtväg. Samma parser som filuppladdning |
| Fortnox service accounts: client credentials + TenantId, inga refresh-tokens. Admin-auktorisering per klient | Enkel drift, men onboarding per klient är friktion. Bygg massonboarding |
| Rate limit 25 anrop / 5 s per klient-id och tenant | Inget hinder för SIE-hämtning. Leverantörsfakturor per rad kräver kö och backoff |
| Fortnox granskar appen före lansering (integration, landningssida, pris, användaravtal) och kräver App Partner-avtal. Byråns biträdesavtal ska spegla kedjan | Planera in granskningen i tidslinjen. Juristgranska utvecklaravtalet (dataanvändning, konkurrensklausuler) |
| Spiris exporterar SIE4 och har API (tillägg ca 69 kr/mån) | SIE-fil i MVP, API i V1.5 |
| Björn Lundén har API, utvecklarportal och SIE-export | SIE-fil i MVP, API i V2 |
| SIE 5 (XML, med reskontra) har fortfarande begränsat stöd hos programmen | SIE 5 stannar i V2+ |
| Skatteverket Skattekonto-API v2.0: saldo, transaktioner, betalningsspärrar. Ombud kan få läsbehörighet. Organisationscertifikat krävs | V1.5: avstämning 1630 mot skattekontot. Kräver organisationscertifikat och ombudsflöde |

### 1.4 Lag och regler (måste verifieras av auktoriserad konsult/jurist innan release)
| Regel | Konsekvens |
|---|---|
| BFL 5 kap. 2 §: kontanta in- och utbetalningar bokförs senast påföljande arbetsdag, andra affärshändelser "så snart det kan ske" (BFNAR 2013:2 p. 3.1, 3.3) | Kontroll av bokföringsfördröjning skiljer på kontant och övrigt |
| ABL 21 kap.: låneförbud till aktieägare, styrelse, VD och närstående. Straffbart (30 kap. 1 §). Typiska konton: 1685 (kortfristig fordran på delägare/närstående), 1360 (långfristig), 2893 (skuld till närstående) | Kontroll med hög rådgivningsnytta. Formuleras som "fordran på närstående – kontrollera låneförbudet", aldrig som ett konstaterat brott |
| ABL 25 kap. 13 §: kontrollbalansräkning när eget kapital kan understiga hälften av aktiekapitalet. **SOU 2023:34 föreslår att reglerna slopas** | Regeln har giltighetsdatum i regelkatalogen. Fortnox har redan "förbrukat aktiekapital", så vi erbjuder samma kontroll men med evidens och dokumentation |
| Moms: matmoms 6 % 2026-04-01 – 2027-12-31, sedan tillbaka till 12 % | Momsrimlighetskontroller måste vara datumkänsliga |
| Arbetsgivaravgift 31,42 % standard. Unga (18–22 vid årets ingång) 20,81 % på lön upp till 25 000 kr/mån, 2026-04-01 – 2027-09-30 | AGA-kontrollen använder ett intervall och en regelkatalog, inte en fast procentsats |
| PTL: byråer ska riskbedöma verksamhet och kunder, ha rutiner och rapportera misstankar till Finanspolisen (goAML). Meddelandeförbud gäller. Tillsyn: länsstyrelserna i Stockholm, Skåne och Västra Götaland, med vite och sanktionsavgifter | PTL-modul med separat behörighet. Aldrig synligt i kundrapport eller kundportal |
| Reko (Srf/FAR) gäller alla FAR-auktoriserade redovisningskonsulter, med nio obligatoriska ska-krav. Reko 140 handlar om dokumentation. Senaste utgåvan på FAR Online är daterad jan 2026 | Granskningsdokumentationen exporteras i ett format som stöder Reko 140. Stäm av med Srf/FAR |
| AI Act art. 50 (transparens) gäller sedan 2 aug 2026. Art. 4 ändrad till insatsplikt (Omnibus 2026/1744) | UI-märkning av AI-innehåll. Kort utbildningsmaterial till byråerna |
| Cybersäkerhetslagen (2025:1506, NIS2) gäller sedan 15 jan 2026. Molnleverantörer omfattas, och medelstora företag i vissa sektorer | Vi omfattas sannolikt inte direkt som litet bolag, men större kunder kommer att ställa NIS2-krav på leverantörer. Bygg för ISO 27001 |
| EU–US Data Privacy Framework löser inte CLOUD Act-exponeringen | Medvetet molnval (§11.6) |

### 1.5 AI och säkerhet
| Fakta | Konsekvens |
|---|---|
| LLM:er tappar kraftigt i precision på flerstegsberäkningar i finansdata | Alla beräkningar sker i motorn. AI:n refererar till fakta |
| Journal entry testing: regelbaserade flaggor ger mycket brus från återföringar, avsättningar och bokslutsposter. Hybrid (regler + ML) förbättrar resultatet. Mät med average precision | Precision per regel, igenkänning av bokslutsmönster, ML-rangordning i V2 |
| Indirekt prompt injection är OWASP LLM01. Exfiltration via markdown-bilder har drabbat stora AI-produkter. Försvaret ligger i renderingslagret | Strikt rendering, CSP, inga externa resurser i AI-output (§9.5) |
| Anthropics API: `inference_geo` bara `us`/`global`, workspace geo bara `us`. Bedrock och Vertex styr region via endpoint | Claude används bara via EU-region hos molnleverantör |
| Azure OpenAI Sweden Central: EU Data Zone, men avbrott 27 jan 2026 | Failover till en andra EU-region eller leverantör |
| Mistral: EU-bolag, standardlagring 30 dagar, ZDR bara på Scale-plan | Kräver rätt plan om Mistral väljs |
| RLS + PgBouncer i transaktionsläge: `SET` (utan LOCAL) läcker tenant-kontext mellan klienter. Även `SET LOCAL` kräver explicita transaktioner och en verifierad reset | Bara `SET LOCAL`/`set_config(..., true)` inuti explicita transaktioner, plus automatiska läckagetester (§12) |
| Procrastinate: Postgres-kö för Python, ingen separat broker, inget inbyggt UI | Räcker för MVP. Bygg en enkel jobbvy i admin |

---

## 2. Svagheter i v1 (sammanfattning, uppdaterad)

**Affär**
1. Ingen konkurrensanalys. Fortnox Insikter täcker redan grundkontrollerna.
2. Ingen affärsmodell eller enhetsekonomi. Integrationslicenser saknas i kalkylen.
3. Fel kil: fyra produkter samtidigt, där de "unika" delarna inte är unika.
4. Plattformsrisken hos Fortnox ignoreras (nytt ägande, egen byråtjänst).

**Data och domän**
5. Manuell SIE-uppladdning som enda källa. Portföljvyn blir inaktuell.
6. Bara en integration (Fortnox), trots att ~63 % av marknaden ligger i andra system.
7. SIE4 saknar motparter och reskontra, så Spend Intelligence kan inte vara MVP.
8. Föregående års transaktioner behövs för YoY. Onboarding måste hämta 2–3 år.
9. Periodiseringsmognad och periodens fullständighet saknas, vilket ger falsklarm.
10. Regler är hårdkodade trots att satser och gränser ändras (moms, AGA, ABL).
11. Legal uppställning och management-kategorier blandas.
12. Dataset som helkopior. Ingen ändringsdiff.

**AI**
13. Efterhandsverifiering av siffror räcker inte.
14. AI används där deterministisk kod är bättre, och inte där AI är bäst.
15. Ingen hantering av falsklarm och ingen precisionsmätning.
16. Prompt injection behandlas på instruktionsnivå i stället för i renderings- och verktygslagret.
17. AI-leverantörens region och tillgänglighet behandlas inte (ingen EU-inferens hos vissa leverantörer, regionala avbrott).

**Juridik och säkerhet**
18. Biträdeskedjan saknas. Fortnox utvecklaravtal saknas.
19. PTL behandlas inte, varken som risk (meddelandeförbud) eller som möjlighet (tillsynsbehov).
20. Enskilda firmor och lönedata för få anställda.
21. Ingen NIS2-, ISO- eller CLOUD Act-hållning.

**Genomförande**
22. Pilot först i steg 22.
23. Överdimensionerad infrastruktur (Temporal, sex roller, SAML).
24. Ingen Excel/Word-export.

---

## 3. Positionering och kil

### 3.1 Positionering och ärlig differentieringsanalys (v3.1)

**Ärligt läge:** nästan varje enskild funktion i v1–v3 finns redan någonstans.

| Funktion | Finns redan hos | Unik för oss? |
|---|---|---|
| Avvikelse- och trendkontroller, moms, förbrukat aktiekapital | Fortnox Insikter (gratis för Fortnox-byråer) | Nej |
| Checklistor, avstämningar, Reko-dokumentation | Fortnox Byråstöd, WeSoft m.fl. | Nej |
| Skattekontots transaktioner | Fortnox Skatteverket-koppling (gratis), JSI Skattekonto | Nej |
| PTL/KYC, riskbedömning | Visma Advisor KYC, Lundify | Nej |
| Chatta med bokföringen | Fortnox AI-assistent, MCP-bryggor | Nej |
| Rapporter och dashboards | Fortnox Rapport & Analys, Business Board | Nej |

Det som **inte** hittades i researchen är kombinationen nedan. Den blir därför produktens kärna:

#### Fem skäl att välja oss (hypoteser att bevisa i Fas 0)

1. **Ett ärende i stället för tio signaler (rotorsaksgruppering).**
   Befintliga verktyg larmar per signal: "kostnadsavvikelse", "momsavvikelse" och "trendbrott" var för sig. Oftast har de en gemensam orsak. Exempel: *en saknad leverantörsfaktura* ger en kostnadsavvikelse, en momsavvikelse och ett oförklarat bankuttag samtidigt. Vi grupperar relaterade fynd till **ett ärende** med en rotorsakshypotes, evidens och en föreslagen åtgärd. Här gör AI verklig nytta: den kopplar ihop deterministiska fynd till en förklaring (det kräver bedömning, inte beräkning). Konsulten ska få **3 beslut per kund i stället för 30 larm**.

2. **Kundminne: byrån glömmer aldrig hur något förklarades.**
   Varje bedömning sparas per kund och mönster: vem, när, motivering och underlag. Nästa gång samma mönster dyker upp visas: *"Samma mönster som mars 2026, bedömt OK av Anna: kvartalsvis hyresfaktura."* Det ger:
   - mindre brus varje månad (automatiskt förslag baserat på tidigare beslut, alltid kvitterat av människa),
   - **enkel överlämning** när en konsult slutar eller är på semester. Branschen har brist på konsulter, och kunskapen om varje kund sitter i dag i huvudet på den som har kunden,
   - en **inlåsningseffekt som växer över tid**: byråns samlade bedömningar finns inte hos någon annan. Det är produktens egentliga vallgrav.

3. **Kundfrågeloop kopplad till ärendet.**
   Frågan till kunden skapas från ärendet och skickas som en säker länk (senare via kundportal). Kundens svar och underlag (kvitto, avtal) hamnar **direkt på ärendet**, och därmed i dokumentationen. I dag jagar konsulter svar via mejl och klistrar in dem manuellt.

4. **En portfölj oavsett system, från en leverantör som inte konkurrerar med byrån.**
   Fortnox, Spiris, BL och övriga via SIE i samma vy och med samma regler. Fortnox kan strukturellt inte göra detta för Spiris-kunder, och driver samtidigt en egen byråtjänst (BLINK). Styrkan i det här argumentet beror på hur blandade byråernas portföljer är, och det måste mätas i Fas 0.

5. **Spårbarhet som ingen annan har: ändrat efter godkännande + evidenskedja.**
   Varje siffra spåras till verifikationen. Varje godkänd period låses, och **ändringar i efterhand upptäcks automatiskt** (verifikationer som lagts till, ändrats eller tagits bort efter att konsulten godkänt månaden).

**Hygien (måste finnas, men säljer inte ensamt):** svenska kontroller, regelkatalog, skattekontoavstämning, Reko-export, PTL-signaler (som export till byråns KYC-verktyg), AI-kommentarer och kundmötesunderlag.

#### Hur sårbar är differentieringen?
| Skäl | Kan Fortnox kopiera för sina egna kunder? | Kan Fortnox kopiera för andra systems kunder? | Försvarbarhet |
|---|---|---|---|
| 1. Ärenden | Ja, 12–24 mån | Nej | Medel: försprång + kvalitet |
| 2. Kundminne | Ja, funktionen | Nej, **inte byråns historik hos oss** | **Hög** över tid (data + inlåsning) |
| 3. Kundfrågeloop | Ja | Nej | Låg–medel |
| 4. Alla system, neutral | Nej | Nej | **Hög** om portföljerna är blandade |
| 5. Ändrat efter godkännande | Ja | Nej | Medel |

**Slutsats:** produkten blir något utöver det som finns genom att flytta enheten från *signal* till *ärende med minne*, över *alla system*. Enskilda kontroller eller en AI-chatt räcker inte som skäl.

#### Stoppkriterier i Fas 0 (om hypoteserna inte håller)
- Om pilotbyråerna har **> 85 % av kunderna i Fortnox och är nöjda med Insikter**, faller skäl 4. Då ska ärenden + kundminne bära produkten ensamma. Alternativ: **en app på Fortnox marknadsplats** som bygger just ärenden och minne ovanpå Fortnox data.
- Om konsulterna inte upplever att **gruppering till ärenden sparar tid** jämfört med Insikter-listan: pröva i stället **bokslut/årsbokslut** som kil (färre tillfällen men högre värde per tillfälle).
- Om ingen betalningsvilja ≥ 39 kr/klient/mån finns: stoppa eller byt kund (t.ex. större byråkedjor med egna kvalitetsavdelningar).

### 3.2 Kilen (MVP) – "Portföljgranskning"
```text
Nattlig synk (Fortnox API) / SIE-uppladdning (Spiris, BL, övriga)
   ↓
Import + ändringsdiff mot förra importen
   ↓
Periodmognad + fullständighet
   ↓
Regelkatalog → deterministiska kontroller (med lagstöd och giltighet)
   ↓
Kundminne: matcha mot tidigare bedömningar ("samma mönster som …")
   ↓
Ärendebyggare (AI): grupperar relaterade fynd → ärende med rotorsak, evidens, föreslagen åtgärd
   ↓
Portföljvy: prioriterad lista över kunder och ärenden
   ↓
Konsult beslutar per ärende (OK / åtgärda / fråga kund) → beslut sparas i kundminnet
   ↓
Kundfrågeloop: fråga → kundens svar + underlag hamnar på ärendet
   ↓
Periodanalys → kundmötesunderlag
   ↓
Godkänn → låst snapshot → export (PDF/Word/Excel + Reko-dokumentation)
```

### 3.3 Definition av färdig (utöver v1 §108)
- Minst **30 % kortare** mediantid per kund-månadsgranskning hos pilotbyråerna.
- Minst **50 % av High-fynden** leder till en åtgärd eller en fråga till kunden (precision).
- **0 missade** kända fel i den kurerade testsviten (recall).
- Minst 1 pilotbyrå med **blandad systemportfölj** (inte bara Fortnox) använder produkten varje vecka.
- Median **≤ 5 ärenden per kund-månad** (i stället för en lång signallista), och ≥ 30 % av återkommande mönster får ett korrekt förslag från kundminnet.

---

## 4. Datakällor och connectors

| Fas | Källa | Innehåll | Kostnad/villkor | Kommentar |
|---|---|---|---|---|
| MVP | **Fortnox API → SIE4** | Kontoplan, IB/UB, verifikationer, dimensioner, #PBUDGET | Marknadsplatsmodell (kostnad på kundens Fortnox-faktura) eller integrationslicens ~59 kr/mån (byråpaket) | Service account per klient. Massonboarding. Hämta innevarande + 2 år bakåt |
| MVP | **SIE4-filuppladdning** | Samma | Gratis | Spiris, BL, Hogia, Bokio m.fl. **Massuppladdning** (zip med många filer, automatisk matchning mot klient via org.nr i `#ORGNR`) |
| V1.5 | **Fortnox leverantörs- och kundfakturor** | Motpart, förfallodatum, betalstatus | Samma licens | Spend Intelligence, reskontraavstämning 1510/2440, åldersanalys |
| V1.5 | **Spiris API** | SIE-motsvarande data + reskontra | ~69 kr/mån för kunden | Automatiserar den näst största källan |
| V1.5 | **Skatteverket Skattekonto-API** | Saldo, transaktioner | Organisationscertifikat + ombudsbehörighet | Avstämning 1630 ↔ skattekonto. Hygien: Fortnox har redan en gratis koppling. Värdet ligger i icke-Fortnox-kunder och i att avvikelser blir ärenden |
| V2 | Björn Lundén API, SIE 5, fakturadokument | | | |
| V3 | Bank (PSD2), lön (AGI-underlag) | | | Undersök om Fortnox gratis bankkoppling exponeras via API |

**Connector-regler**
- Allt går via `SourceConnector`. Kärnan känner bara till normaliserade data.
- Varje connector rapporterar **kopplingshälsa** per klient: auktoriserad, senaste lyckade synk, fel. Det visas i portföljvyn.
- Fortnox-specifika signaler (t.ex. Insikter) tas **inte** in i MVP. Undersök i Fas 0 om API:et exponerar dem och om byråerna vill se dem i vår vy.

**Onboarding:** en klient är "komplett" först med 13 månaders historik. Annars visas YoY och R12 som `INSUFFICIENT_DATA`.

---

## 5. Regelkatalog och kontroller

### 5.1 Regelkatalog (ny kärnkomponent)
Alla regler och parametrar som beror på lag, skattesatser eller praxis ligger i en versionerad katalog, inte i koden:

```text
rule_definition
---------------
code                  -- t.ex. "ABL21_RELATED_PARTY_RECEIVABLE"
version
title_sv, description_sv
legal_basis           -- t.ex. "ABL 21 kap. 1 §", "BFL 5 kap. 2 §", "SFL ..."
valid_from, valid_to  -- regelns giltighet
parameters JSONB      -- konton, trösklar, satser med egna giltighetsintervall
applies_to            -- AB / EF / HB / ek.för.; K2/K3; momsperiod
severity_default
visibility_default    -- INTERNAL | CLIENT_SAFE | RESTRICTED_AML
owner                 -- ansvarig domänexpert
reviewed_at           -- senaste juridiska/fackliga granskning

rate_table            -- momssatser, AGA-satser, bolagsskatt, schabloner
----------
code, valid_from, valid_to, value, source_url
```

- Varje fynd sparar `rule_code` + `rule_version`, så gamla rapporter kan reproduceras.
- En **regelbevakningsrutin** (månatlig, manuell i början) går igenom Skatteverket, Bolagsverket, BFN och Srf/FAR. Kända aktuella exempel: matmoms 6 % till 2027-12-31, AGA för unga till 2027-09-30, och den eventuella avskaffningen av kontrollbalansräkningen.
- Byrån kan justera **parametrar** (trösklar, kontolistor) men inte lagstöd. Ändringar loggas.

### 5.2 Kontroller i MVP
Märkning: 🟰 = finns i någon form i Fortnox Insikter, 🆕 = inte listat där.

**Integritet och bokföringsregler**
1. 🆕 Obalanserad verifikation
2. 🆕 IB ≠ föregående års UB
3. 🆕 Luckor/dubbletter i verifikationsnummerserier
4. 🆕 Bokföringsfördröjning (reg.datum mot verifikationsdatum). Kontanta poster jämförs mot "påföljande arbetsdag", övriga mot byråns tröskel
5. 🆕 **Ändringar i godkänd period** (ändringsdiff mot låst snapshot)
6. 🆕 Konton utanför kontoplan/BAS-intervall. Onormalt tecken (t.ex. negativ kassa 1910)

**Moms och skatt**
7. 🟰 Momsrimlighet per kostnadskonto (ingående moms mot kostnad), **datumkänslig** mot rate_table
8. 🆕 Momskonton (26xx) inte nollställda mot redovisningskontot efter momsperiodens slut
9. 🆕 Utgående moms mot intäktskonton per sats (6/12/25 %), datumkänslig (matmomsen)
10. 🆕 Personalskatt (2710) och arbetsgivaravgifter (2730) nollas inte månadsvis
11. 🆕 AGA (75xx) mot bruttolön (70xx–72xx) utanför intervall [lägsta tillämpliga sats, 31,42 %], med marginal. Intervallet hämtas ur rate_table
12. 🆕 Skattekonto 1630 med ovanligt saldo eller rörelse (full avstämning i V1.5)

**Balansposter och bokslut**
13. 🆕 Hängkonton/avräkningskonton med kvarstående saldo (byråns lista)
14. 🆕 Inventarier (12xx) finns, men inga avskrivningar (78xx), med hänsyn till periodmognad
15. 🆕 Semesterlöneskuld (2920) oförändrad trots löner, med hänsyn till periodmognad
16. 🆕 Periodiseringsfond som ska återföras
17. 🆕 Bank 1930 negativt

**Aktiebolagsrätt**
18. 🆕 Fordran på delägare/närstående (1685, 1360) eller ovanliga rörelser mot 2893. "Kontrollera låneförbudet (ABL 21 kap.)"
19. 🟰 Eget kapital mot registrerat aktiekapital (förbrukat aktiekapital). Regeln har giltighetsdatum

**Mönster**
20. 🆕 Dubblettkandidater (belopp, konto, textlikhet, datumfönster)
21. 🆕 Stora manuella poster nära periodslut
22. 🆕 Snabb återföring
23. 🆕 Ovanlig kontokombination för kunden
24. 🟰 Kostnadsavvikelse mot historiskt mönster. Vårt tillägg är evidens, förklaring och kvittens
25. 🟰 Omsättnings- och kostnadstrend (R12 mot föregående år)

**Hantering av falsklarm (baserat på forskningen om journal entry testing)**
- **Bokslutsmönster känns igen före larm**: återföringar, periodiseringar (17xx/29xx), avskrivningar och bokslutsdispositioner (88xx) märks och bedöms för sig.
- **Precision per regel** mäts löpande via kvittensstatus. Regler under 20 % precision hos en byrå får lägre standardprioritet där. Det syns i en regelhälsovy.
- **V2:** ML-rangordning ovanpå reglerna (inte ersättning), tränad per byrå på dess egen feedback. Utvärderas med average precision.

### 5.3 PTL-signaler (V1.5, bakom behörigheten *PTL-ansvarig*)
> **v3.1:** KYC och riskbedömning finns redan i Visma Advisor KYC och Lundify. Vi bygger **bara transaktionssignaler ur bokföringen** och exporterar dem (CSV/API) till byråns KYC-verktyg. Behovet är tydligt: länsstyrelsen gav 2022 sanktionsavgift till alla utom en av de granskade redovisningsbyråerna. Men det är ett tillägg, inte kärnan.

- Deterministiska signaler ur bokföringen som kan kopplas till myndigheternas vägledning från 2025. Exempel: ovanligt stora kontantposter/kassasaldon, runda belopp mot närstående, snabba in- och utflöden utan affärslogik, betalningar till och från utländska motparter som avviker från verksamheten. **Den exakta signallistan tas fram tillsammans med en PTL-kunnig konsult utifrån vägledningen.**
- Stöd för **kundriskbedömning**: bransch, bolagsform, kontantintensitet, förändringar i ägarstruktur (senare via Bolagsverket).
- **Synlighet `RESTRICTED_AML`**: visas aldrig i kundrapport, kundmötesunderlag eller kundportal. AI-uppgifter för kundriktad text får aldrig se dem.
- Produkten **rapporterar inte** till Finanspolisen och bedömer inte misstanke. Den dokumenterar underlag och byråns beslut.

---

## 6. Periodmognad och fullständighet
(Behålls från v2 §4.2.)

| Indikator | Hur |
|---|---|
| Bokföringsmetod | Heuristik: löpande 1510/2440 under året eller bara vid årsskiftet? Konsulten kan överstyra |
| Löpande avskrivningar | 78xx varje månad eller bara i bokslutsmånaden |
| Löpande semesterlöneskuld | 2920 rör sig under året? |
| Periodens fullständighet | Antal verifikationer, bankrörelser och leverantörsfakturor mot kundens normala mönster. Lön bokad? |
| Stängningssignal | Verifikationer daterade efter periodslut finns |

Låg mognad ger R12/YTD som standardvy, högre trösklar för periodberoende regler och varningsbanner. En ofullständig period kan inte godkännas för kundrapport utan aktiv överstyrning.

---

## 7. Datamodell

Behålls från v2:
- `voucher_version` med `content_hash` och giltighet per import (ändringsdiff, reproducerbarhet).
- `account_period_balance` materialiserad per import.
- `fact` med lineage (alla siffror i UI, rapporter och AI).
- `finding` med `fingerprint`, livscykel, `visibility`, `suppression_rule`.
- `account_sensitivity` (PAYROLL-rader kräver behörigheten *Lönedata* och skickas bara aggregerade till AI).
- Två klassificeringsträd: legal uppställning (ÅRL/K2/K3) och management-kategorier. Precedens: System → Bransch → Byrå → Kund.

Nytt i v3:
```text
connection               -- en per klient och källa
----------
company_id, source (fortnox|spiris|sie_file|skv), status, tenant_ref,
authorized_by, authorized_at, last_success_at, last_error, cost_model

rule_definition, rate_table   -- se §5.1

rule_precision_stat      -- per byrå × regel × månad
-------------------
organization_id, rule_code, findings, actioned, accepted_ok, suppressed

case                     -- ärende: grupp av relaterade fynd (v3.1)
----
id, company_id, period, title, root_cause_hypothesis, claims JSONB (fact_ids),
finding_ids[], status, decision, decided_by, decided_at, client_question_id

resolution_memory        -- kundminne (v3.1)
-----------------
company_id, pattern_signature   -- regel + konto/motpart + beloppsintervall + periodicitet
decision, rationale, evidence_refs, decided_by, decided_at, valid_until,
times_reused, last_reused_at

client_question          -- kundfrågeloop (v3.1)
---------------
id, company_id, case_id, text, sent_via (link|email|portal), token_hash, expires_at,
answered_at, answer_text, attachments (object keys)

aml_assessment           -- PTL, RESTRICTED
--------------
company_id, risk_level, factors JSONB, decided_by, decided_at, notes
```

---

## 8. Arkitektur och teknik

| Område | Beslut |
|---|---|
| Stil | Modulär monolit |
| Backend | Python + FastAPI, Pydantic v2, `ruff`, `mypy --strict` på domänmoduler |
| Frontend | Next.js som ren klient mot FastAPI. shadcn/ui, TanStack Table (server-side), ECharts (linje, stapel, vattenfall) |
| Databas | PostgreSQL + RLS på `organization_id` på alla tabeller + klienttilldelning i policy. **Tenant-kontext bara via `SET LOCAL`/`set_config(..., true)` inuti explicita transaktioner.** Runtime-rollen utan BYPASSRLS och inte tabellägare. Automatiskt läckagetest genom PgBouncer i transaktionsläge i CI |
| Jobb | Procrastinate (Postgres-kö). Idempotensnyckel `(import_id, step, step_version)`. Enkel jobbvy i admin |
| Schemaläggning | Nattlig synk spridd över natten per byrå, kö per källa med backoff mot rate limits |
| Lagring | Object storage med envelope-kryptering per byrå |
| Auth | OIDC via EU-hostad IdP + passkeys/TOTP. Microsoft Entra när en byrå kräver det. BankID först för kundportal |
| Roller | Byråadmin, Konsult, Läsare + flaggor: *Lönedata*, *PTL-ansvarig*, *Godkänna kundrapport* |
| Export | Excel från varje tabell. Kundrapport som PDF + Word. Reko-dokumentation som PDF |
| Observability | OpenTelemetry. Loggar utan belopp, texter eller personuppgifter. AI-traces separat med ≤ 30 dagars retention |

Projektstruktur (tillägg till v1 §85):
```text
services/api/app/
  connectors/      # fortnox/, sie_file/, spiris/ (V1.5), skatteverket/ (V1.5)
  rules/           # regelkatalog, rate_table, regelmotor, precisionsstatistik
  maturity/
  facts/
  findings/
  aml/             # V1.5, egen behörighetskontroll
  ai/
    tasks/         # en modul per AI-uppgift: schema + prompt + eval
    verifier/
    providers/     # EU-regioner, failover
  exports/
```

---

## 9. AI-arkitektur och agenter

### 9.1 Var AI används
| Uppgift | Kod | AI |
|---|---|---|
| Summor, nyckeltal, varians, bryggor, trender | ✅ | ❌ |
| Nya/försvunna kostnader, periodicitet, nivåskiften | ✅ | ❌ |
| Kontroller och PTL-signaler | ✅ | ❌ |
| Mappningsförslag för okända konton | | ✅ (bekräftas) |
| Normalisering av motparter ur fritext | Delvis | ✅ (bekräftas) |
| Triage av fynd | | ✅ |
| Sammanfattning och prioritering | | ✅ |
| Frågor till kunden | | ✅ |
| Fritt Q&A | Verktyg | ✅ (orkestrerar) |

### 9.2 Siffror renderas av servern
(Från v2 §7.2, bekräftat av forskningen om LLM:ers numeriska precision.)
- AI:n returnerar `claims` med typ (OBSERVATION / EXPLANATION / HYPOTHESIS / QUESTION), text med platshållare `{f:fact_id}` och en lista med fact_ids.
- Servern validerar att fact_ids finns i paketet och tillhör rätt bolag och import, renderar värdena och **avvisar siffror skrivna som text**.
- Kausala formuleringar kräver variance_component-fakta. Annars nedgraderas påståendet till HYPOTHESIS.

### 9.3 Agenter och AI-uppgifter
Orkestreringen är kod (jobbkön). Varje uppgift har fast verktygsuppsättning, fast outputschema, modellnivå och egen eval-svit.

| # | Uppgift | Fas | Input | Output | Verktyg | Modellnivå | Människa |
|---|---|---|---|---|---|---|---|
| A1 | **Mappningsassistent** | MVP | Kontonamn, BAS-intervall, exempeltexter, bransch | Legal rad + kategori + konfidens + motivering | – | Liten (batch) | Bekräftar. Blir byråmall |
| A2 | **Ärendebyggare** (triage + rotorsaksgruppering, v3.1) | MVP | Periodens fynd + evidenspaket (verifikationer, kontohistorik, bokslutsmönster) + **träffar i kundminnet** | Ärenden: grupperade fynd, rotorsakshypotes, "troligen OK/utred/fråga kund", motivering med fact_ids, hänvisning till tidigare beslut | Läs: `get_voucher`, `get_account_history`, `get_similar_vouchers`, `get_prior_resolutions` | Stark (batch) | Kan aldrig stänga fynd eller sänka High. Förslag från kundminnet kräver alltid kvittens |
| A3 | **Periodanalytiker** | MVP | Förberäknat analyspaket inkl. mognad | Intern månadskommentar | – | Stark | Redigerar/godkänner |
| A4 | **Kundmötes- och frågeagent** | MVP | Bara `CLIENT_SAFE`-fakta + A3-utkast + ärenden markerade "fråga kund" | 1-sidig sammanfattning + 3–7 frågor/råd + **kundfrågor per ärende** i lättförståelig svenska | – | Stark | Godkänner. Aldrig autoutskick |
| A5 | **Analytiker (Q&A)** | MVP-slut | Fråga + klientkontext | Claims | Läsverktyg (v1 §51) + `get_maturity`, `list_changes_since`, `explain_rule` | Stark | Budget: N verktygsanrop, M tokens |
| V | **Granskare** | MVP | Output + paket | Pass/fail per claim | Regler + LLM-bedömning (kausalitet, internt i kundtext) | Mellan, annan leverantör/prompt än producenten om möjligt | Underkänt → en omgenerering → annars bortfiltrerat |
| A6 | **Motpartsresolver** | V1.5 | Leverantörsnamn, org.nr, texter | Kanonisk motpart + kategori + konfidens | `search_counterparties` | Liten | Bekräftelse över väsentlighet |
| A7 | **Portföljbrief** | V1.5 | Prioriteringspoäng + topp-fynd | "Veckans prioriteringar" | – | Mellan | Informativ |
| A8 | **Regelbevakare** | V1.5 | Nyheter/ändringar från Skatteverket, Bolagsverket, BFN, Srf/FAR | Utkast till ändring i regelkatalog/rate_table med källa | Webbhämtning (endast allowlistade myndighetsdomäner), **ingen** kunddata | Mellan | **Domänexpert godkänner alltid.** Inget ändras automatiskt |
| A9 | **Dokumentagent** | V2 | Faktura-PDF | Strukturerad faktura | OCR/Document AI | Specialmodell | Matchning bekräftas |

**Medvetet ingen AI i PTL-modulen** i MVP/V1.5. Signaler och riskbedömning är deterministiska. Konsekvenserna av fel (meddelandeförbud, sanktioner) är för stora för genererad text. Omprövas efter juridisk granskning.

### 9.4 Evidenspaket och dataminimering
- Varje uppgift får ett förberäknat paket. Det är det enda den ser, utöver läsverktygen.
- Pseudonymisering före modellen: personnamn (NER + anställdlista), personnummer (regex + Luhn), e-post och telefon ersätts med tokens. Återställs i UI:t.
- PAYROLL-rader skickas bara som aggregat. `RESTRICTED_AML` skickas aldrig.
- Paket och svar sparas (≤ 30 dagar, åtkomstbegränsat) för reproducerbarhet.

### 9.5 Prompt injection och exfiltration
- Kunddata ligger i avgränsade datablock och är aldrig instruktioner.
- **Inga utgående kanaler**: inga skriv-, mejl- eller webbverktyg för uppgifter med kunddata (A8 har webb men aldrig kunddata).
- **Renderingslagret**: AI-output renderas som begränsad markdown utan bilder, externa länkar eller HTML. Strikt CSP (`img-src 'self'`, `connect-src 'self'`). Länkar bara till interna routes via allowlist.
- Verktyg är tenant-bundna på serversidan. AI:n kan aldrig ange `company_id`.
- Adversariella tester i CI (§12).

### 9.6 Leverantörer, regioner och kostnad
- **Krav:** EU-inferens, ingen träning på data, noll eller kort retention i avtal, DPA, **två leverantörer eller regioner med failover**.
- **Kandidater att utvärdera med evals på Fas 0-data:**
  - Azure-hostade modeller i Sweden Central med EU Data Zone (primär om Azure väljs som moln). Failover till annan EU-region.
  - Claude via AWS Bedrock eller Google Vertex i EU-region (**inte** via Anthropics eget API, som i dag saknar EU-inferens).
  - Mistral (EU-bolag), med Scale-plan om ZDR krävs.
- Tre modellnivåer (liten/mellan/stark) mappade i konfiguration. Modellbyte = konfiguration + eval-körning.
- Batch för A1, A2 och A7 (nattligt). Prompt-cache för stabila systemprompter.
- **Budget:** AI-kostnad < 10 % av intäkten per klient-månad. Mäts per uppgift och byrå. Hård spärr per byrå.

---

## 10. Arbetsflöde, UX och rapporter

- **Periodstatus:** Ej påbörjad → Data mottagen → Bearbetas → Preliminär → Behöver granskning → Granskad → Godkänd → Rapporterad. Plus automatisk övergång **"Ändrad efter godkännande"**.
- **Portföljvy:** deterministisk prioriteringspoäng (High-fynd, ändring efter godkännande, marginalförändring, dagar sedan granskning, saknad data, **kopplingshälsa**). Poängens delar visas alltid.
- **Portföljfilter per system** (Fortnox/Spiris/SIE-fil) och per konsult.
- **Kvittens:** varje fynd och period loggar vem, när, beslut och motivering. Export av Reko 140-stödjande granskningsdokumentation per kund och period.
- **Rapporter:** intern rapport och kundrapport separata. Kundrapport bara från `CLIENT_SAFE`. Export PDF + Word + Excel-bilaga.
- **Regelhälsa (admin):** precision per regel, suppression-regler och utgångsdatum, regelversioner i bruk.
- **Återkoppling:** ACCEPTED_OK med motivering blir förslag till suppression eller tröskeljustering. Korrigerade AI-förslag blir eval-exempel för byrån (ingen träning utan avtal).

---

## 11. Juridik, säkerhet och förtroende

1. **Avtalskedja:** klientbolag (personuppgiftsansvarig) → byrå (biträde) → vi (underbiträde) → moln- och AI-leverantörer. Publicerad underbiträdeslista, färdig biträdesavtalsmall och kundinformation byrån kan vidarebefordra.
2. **Fortnox utvecklar- och App Partner-avtal:** juristgranskas före Fas 1 (dataanvändning, AI, konkurrensbestämmelser, uppsägning). Samma för Spiris.
3. **PTL:** se §5.3. Synlighetsnivå, separat behörighet, ingen AI och inga kundriktade formuleringar.
4. **AI Act:** art. 50-märkning i UI:t (användaren interagerar med AI, AI-genererade texter märks). Utbildningsmaterial som stöd för byråns art. 4-insatser. Dokumenterad bedömning att systemet inte är högrisk.
5. **Enskilda firmor och lönedata:** behörighet, aggregering och pseudonymisering.
6. **Molnsuveränitet:**
   - **A. Azure Sweden Central** (Entra, AI-modeller i regionen, men CLOUD Act och regionala avbrott).
   - **B. EU-ägt moln** för data + AI via EU-region hos hyperscaler.
   - **Rekommendation:** A för MVP, containeriserat. Frågan ställs i Fas 0-intervjuerna.
7. **NIS2/cybersäkerhetslagen:** vi omfattas sannolikt inte direkt i början, men förbered leverantörsfrågor från större byråer. **ISO 27001** som mål inom 18–24 månader.
8. **Retention:** vi är inte bokföringsarkivet. Källfiler 13–36 mån (konfigurerbart), snapshots och Reko-dokumentation enligt byrån, AI-traces ≤ 30 dagar. Radering omfattar databas, object storage, index och cache.
9. **Break-glass** för support (v1 §83).

---

## 12. Testning

1. **SIE-korpus:** minst en fil per exportör (Fortnox, Spiris, BL, Hogia, Bokio m.fl.), brutna räkenskapsår, förlängt första år, PC8-kodning (CP437), #RTRANS/#BTRANS, #KSUMMA, 100k+ transaktioner, skadade filer.
2. **Golden tests:** RR/BR/nyckeltal mot konsultfacit och mot Fortnox egen utskrift. Differens 0,00 kr.
3. **Regeltester:** varje regel har positiva och negativa fall, **datumgränsfall** (t.ex. 2026-03-31/2026-04-01 för matmoms, 2027-09-30/2027-10-01 för AGA) och tester för bokslutsmönster.
4. **AI-evals per uppgift:** A1 träffsäkerhet. A2 precision på "troligen OK" med 100 % recall på kända fel. A3–A5: 0 sifferfel, andel otillåtna kausala påståenden, 0 interna fakta i kundtext.
5. **Säkerhet:**
   - Åtkomst: användare A kan inte läsa bolag B, byrå A kan inte läsa byrå B.
   - **RLS-läckagetest genom PgBouncer i transaktionsläge**, inklusive avbrutna transaktioner.
   - Dataskydd: PAYROLL-rader når aldrig AI-paketet, och RESTRICTED_AML når aldrig A4, kundexport eller AI.
   - AI-angrepp: prompt injection i verifikationstexter och markdown-exfiltration.
   - Filangrepp: filspoofing och stora filer.
6. **Connector-tester:** Fortnox sandbox, rate limit-beteende, återkallad auktorisering, delvis synk.

---

## 13. Affärsmodell och enhetsekonomi

**Prishypotes (testas i Fas 0):**
- **39–99 kr per aktiv klient/mån** i trappa efter volym, med en minimiavgift per byrå.
- Paket: *Granskning* (MVP) och *Rådgivning + PTL + skattekonto* (V1.5).

**Kalkyl att validera (antaganden markerade):**
| Post | Värde | Status |
|---|---|---|
| Byråns intäkt per mikrobolag och månad | ~500–800 kr | Branschuppgift, partisk källa. Verifiera |
| Tid för månadsgenomgång per kund | 30–60 min | **Antagande**, mät i Fas 0 |
| Konsultens kostnad/debiteringsvärde | 700–1 000 kr/h | **Antagande**, mät i Fas 0 |
| Värde av 30 % tidsbesparing | ~100–300 kr/kund/mån | Härlett |
| Integrationskostnad | 0 (SIE-fil / marknadsplats) till ~59–69 kr/mån | Fortnox/Spiris-uppgifter. **Avgör om marknadsplatsmodellen tar bort licensen** |
| AI + hosting | < 10 kr/kund/mån | Mål. Mät |

→ Om integrationslicensen hamnar på byrån eller kunden ovanpå vårt pris blir SIE-filvägen och marknadsplatsmodellen avgörande för priskänsliga byråer.

**Kanaler:**
- Fortnox integrationsmarknad (kräver appgranskning).
- Srf konsulternas nätverk, utbildningar och tidningen Konsulten.
- Reko-/PTL-vinkel mot FAR-auktoriserade byråer.
- Direktförsäljning till byråer med 3–30 anställda.

**Mått:**
- Nordstjärna: granskade klient-månader per vecka.
- Stödmått: tid per granskning, precision per regel, AI-förslag accepterade oförändrade, retention per byrå, andel klienter med frisk koppling.

---

## 14. Byggordning

### Fas 0 – Validering (4–6 veckor)
- 8–10 intervjuer (se §16). **Specifikt: använder de Fortnox Insikter, och vad saknas?**
- 3–5 pilotbyråer med biträdesavtal, minst en med blandad systemportfölj.
- CLI: SIE4 → parser → 10 kontroller + RR/BR/R12 → Excel + 1-sidig PDF med AI-kommentar (fact_id-principen redan här). Manuell kvalitetssäkring och leverans varje månad.
- Juristgranskning av Fortnox utvecklaravtal. Utred marknadsplatsmodell mot licens.
- **Testa differentieringen:** gör för 2–3 pilotkunder en manuell ärendegruppering och jämför, sida vid sida med Fortnox Insikter-listan, hur lång tid konsulten behöver.
- Evals av 2–3 AI-leverantörer i EU-region på pilotdata.
- **Exit:**
  - ≥ 3 byråer anger betalningsvilja på en nivå.
  - Vi vet de 10 mest värdefulla kontrollerna och om PTL-stöd eller skattekonto väger tyngst.
  - Integrationskostnaden är klarlagd.

### Fas 1 – Kärna (≈ 8–10 veckor)
1. Repo, Docker Compose (Postgres, MinIO, PgBouncer), FastAPI, Next.js, CI (lint, typer, tester, RLS-läckagetest)
2. Organisation, användare, klient, roller, RLS
3. SIE4-parser + filuppladdning (inkl. massuppladdning)
4. Fortnox-connector (service accounts, massonboarding, SIE4 per år, nattlig synk, kopplingshälsa)
5. Verifikationsversionering, ändringsdiff, `account_period_balance`
6. Periodmotor + RR/BR + golden tests
7. Periodmognad
8. Regelkatalog + rate_table (med 2026 års satser)
- **Exit:** pilotbyråernas klienter synkas eller laddas upp, och RR/BR stämmer på öret.

### Fas 2 – Granskning (≈ 6–8 veckor)
9. Fakta/lineage + metric-registret
10. De 25 kontrollerna, igenkänning av bokslutsmönster, fyndlivscykel, suppression, precisionsstatistik
11. Klientvy med drilldown och Excel-export
12. Portföljvy med prioriteringspoäng och kopplingshälsa
13. A1 + A2 (ärendebyggare) + kundminne + granskare V + eval-svit + EU-leverantör med failover
- **Exit:** pilotkonsulterna granskar i produkten, High-precision ≥ 50 %, recall 100 % på testsviten.

### Fas 3 – Rådgivning, rapport, härdning (≈ 4–6 veckor)
14. Variansbrygga, kategori-drilldown, budget mot utfall (#PBUDGET)
15. A3 + A4 + rapporter (PDF/Word) + Reko-dokumentation + **kundfrågeloop** (säker länk)
16. Snapshot + "ändrad efter godkännande"
17. A5 Q&A med budget
18. Revisionslogg, AI-märkning, CSP-härdning, penetrationstest
19. Fortnox appgranskning → listning på marknadsplatsen
- **Exit:** första betalande byrå. Tidsbesparing ≥ 30 % uppmätt.

### Fas 4 – V1.5
- Spiris API. Skatteverket Skattekonto-API (avstämning 1630).
- PTL-modul (om den validerats).
- Fortnox leverantörs- och kundfakturor → Spend Intelligence (v1 §36–47), A6.
- Reskontraavstämning och åldersanalys. A7 portföljbrief. A8 regelbevakare. Branschmallar.

### Fas 5 – V2+
- BL API, SIE 5, A9 fakturadokument och matchning.
- ML-rangordning av fynd per byrå.
- Entra SSO, bank, kundportal (BankID), prognoser.
- ISO 27001.

---

## 15. Riskregister

| Risk | Sannolikhet | Påverkan | Motåtgärd |
|---|---|---|---|
| Fortnox bygger ut Insikter till samma nivå | Hög | Hög | Ärenden + **kundminne** (byråns historik hos oss kan inte kopieras) + alla system. Fortnox kan inte vara neutral mot Spiris-kunder. Stoppkriterier i §3.1 |
| Differentieringen visar sig för svag (byråerna är nöjda med befintliga verktyg) | Medel | Kritisk | Fas 0 mäter detta först. Alternativ: marknadsplatsapp, bokslutskil eller stopp (§3.1) |
| Fortnox ändrar API-villkor/priser eller nekar appen | Medel | Hög | SIE-filväg fullt fungerande. Juristgranskat avtal. Flera connectors |
| Byråer vill inte betala utöver Fortnox gratisfunktioner | Medel | Hög | Fas 0 mäter betalningsvilja. Pris kopplat till sparad tid |
| Falsklarm dödar användningen | Hög | Hög | Mognadsbedömning, bokslutsmönster, precision per regel, suppression |
| Felaktig regel (lag/sats) ger fel råd | Medel | Hög | Regelkatalog med lagstöd, giltighet, ägare och granskning. Rapporter reproducerbara per regelversion |
| AI-leverantör otillgänglig (regionalt avbrott) | Medel | Medel | Failover. Deterministiska delar fungerar utan AI |
| Dataläcka mellan byråer | Låg | Kritisk | RLS + app-kontroller + läckagetester genom pooler + penetrationstest |
| PTL-information når kund (meddelandeförbud) | Låg | Kritisk | Synlighetsnivå, deterministiskt filter, ingen AI, tester |
| Ensam utvecklare → för långsamt | Hög | Medel | Smal kil, faser med exit-kriterier, ingen Temporal eller SSO i MVP |

---

## 16. Intervjuguide för Fas 0

1. Beskriv er månadsgenomgång av en kund. Vad gör ni, i vilket verktyg, och hur lång tid tar det?
2. Hur fördelas era kunder på bokföringssystem (Fortnox, Spiris, BL, övriga)?
3. Använder ni Fortnox Insikter eller Visma Advisor? Vad är bra, vad saknas, och vad litar ni inte på?
4. Hur dokumenterar ni granskning enligt Reko idag? Har ni haft kvalitetskontroll?
5. Hur arbetar ni med PTL: riskbedömning, kundkännedom, signaler? Har ni haft tillsyn?
6. Hur stämmer ni av skattekontot idag?
7. Vilka kontroller skulle ni vilja att ett system gjorde åt er varje natt? Vilka fel hittar ni oftast?
8. Vad skulle ni betala per kund och månad för att korta genomgången med en tredjedel?
9. Hur ser ni på AI-genererade texter mot kund? Vilka krav har ni på var data lagras (EU, svenskt, CLOUD Act)?
10. Vem på byrån beslutar om nya verktyg, och hur går det till?
11. När ni får flera larm för samma kund, hur ofta har de en gemensam orsak? Visa ett exempel.
12. Hur förs kunskap om en kund vidare när en konsult slutar eller är ledig? Vad går förlorat?
13. Hur ställer ni frågor till kunden och samlar in svar och underlag i dag? Hur lång tid tar det?

---

## 17. Källor

**Marknad och konkurrens**
- Srf konsulterna, medlemmar och kunder: <https://www.srfkonsult.se/en/about-us>
- Visma: små byråer står för 60 % av omsättningen: <https://news.cision.com/se/visma/r/sma-redovisningsbyraer-star-for-60-procent-av-branschens-omsattning,c3681365>
- Sveriges största redovisningsbyråer 2026: <https://redovisningskonsult.se/sveriges-storsta-redovisningsbyraer-2026/>
- Fortnox kunder och marknadsandel: <https://placera.se/analys/analys-stenen-i-skon-hos-fortnox-ar-varderingen-2025-01-10>, <https://revisionsvarlden.se/okategoriserade/fortnox-siktar-mot-12-000-byrakunder-nasta-ar/>
- EQT/Hallrup förvärv och avnotering av Fortnox: <https://www.affarsvarlden.se/verktyg/artiklar-uppkopsguiden/uppkopsaret-2025-fortnox-blev-uppkopt-till-slut>, <https://finanstid.se/eqts-bud-pa-fortnox-fullbordat-bolaget-avnoteras-fran-borsen/>
- Fortnox Insikter: <https://www.fortnox.se/produkt/insikter>. Kostnadsavvikelser: <https://support.fortnox.se/produkthjalp/digital-byra/insikter-kostnadsavvikelser>. Momsavvikelser: <https://support.fortnox.se/produkthjalp/digital-byra/insikter-momsavvikelser-ingande-moms>. Omsättningstrend: <https://support.fortnox.se/produkthjalp/digital-byra/insikter-omsattningstrend>. Kostnadstrend: <https://support.fortnox.se/produkthjalp/digital-byra/insikter-kostnadstrend>. Förbrukat aktiekapital: <https://support.fortnox.se/produkthjalp/digital-byra/insikter-forbrukat-aktiekapital>. EU-handel: <https://support.fortnox.se/produkthjalp/digital-byra/insikter-eu-handel>
- Fortnox förändringar 2026 (AI-assistent, Access, BLINK): <https://redovisning.ai/guider/fortnox-forandringar-2026>
- Visma Spcs → Spiris: <https://developer.vismaonline.com/changelog/weve-changed-our-name-visma-spcs-is-now-spiris>, <https://www.breakit.se/artikel/42883/visma-spcs-byter-namn-blir-spiris-ofta-orsakat-huvudbry>
- Visma Advisor: <https://www.vismaspcs.se/visma-support/visma-advisor/content/getting-started/getting-started.htm>
- Prispress och AI-byråer: <https://www.accounted.se/blogg/byra-i-ai-eran> (partisk källa), <https://www.wint.se/academy/artikel/driva-bolag-smartare-bokforing-salj-inte-din-redovisningsbyra-boosta-den-med-automatisering>
- Syft prisnivåer: <https://claryx.ai/blog/syft-analytics-review/>

**API:er och data**
- Fortnox SIE-resurs: <https://developer.fortnox.se/documentation/resources/sie/>. SIE4-hämtning i praktiken: <https://github.com/Magnus-Gille/noxctl/pull/161>
- Fortnox service accounts: <https://www.fortnox.se/developer/blog/service-accounts>
- Fortnox rate limits: <https://www.fortnox.se/en/developer/guides-and-good-to-know/rate-limits-for-fortnox-api>
- Fortnox integrationspartner och prismodeller: <https://www.fortnox.se/om-fortnox/partners/integrationspartner>. API-kostnad Fortnox + byrå: <https://bokforingssystem.se/faq/fortnox-byra/vad-kostar-det-att-anvanda-api-fran-fortnox-byra>
- Fortnox utvecklaravtal: <https://apps.fortnox.se/api/eula/document-v1/developer>
- Spiris (Visma eEkonomi) API-kostnad för byrå: <https://bokforingssystem.se/faq/visma-eekonomi-byra/vad-kostar-det-att-anvanda-api-fran-visma-eekonomi-byra>
- Björn Lundén utvecklarportal: <https://developer.bjornlunden.se/>
- SIE-Gruppen, format och SIE 5-stöd: <https://sie.se/format/>, <https://sie.se/program/>
- Skatteverket Skattekonto-API: <https://www.skatteverket.se/omoss/digitalasamarbeten/utvecklingsomraden/skattekonto.4.7eada0316ed67d72822728.html>

**Lag och regler**
- BFNAR 2013:2 Bokföring: <https://www.bfn.se/wp-content/uploads/vl13-2-bokforing.pdf>
- Förbjudna lån: <https://www.faronline.se/dokument/rattserien/redovisa-ratt/f/rr_forbjudnalan/>
- Kontrollbalansräkning (Bolagsverket): <https://bolagsverket.se/foretag/aktiebolag/arsredovisningforaktiebolag/kontrollbalansrakningvidmisstankeomatthalftenavdetregistreradeaktiekapitaletarforbrukat.3074.html>. Förslag att slopa: <https://schjodt.com/news/kontrollbalansr%C3%A4kning-icke-%C3%A4ndam%C3%A5lsenliga-regler-som-snart-slopas>
- Matmoms 6 %: <https://www.skatteverket.se/omoss/pressochmedia/nyheter/2026/nyheter/livsmedelsmomsensankstill6procent.5.70685bee19c85dd5dd0a3f.html>
- Sänkta arbetsgivaravgifter för unga: <https://www.skatteverket.se/omoss/pressochmedia/nyheter/2026/nyheter/lagrearbetsgivaravgifterforungdomar.5.70685bee19c85dd5dd02b10.html>, <https://www.far.se/aktuellt/nyheter/2026/mars/tillfalligt-sankta-arbetsgivaravgifter-for-unga--det-behover-lonekonsulten-ha-koll-pa/>
- PTL för redovisningskonsulter: <https://www.srfkonsult.se/kunskap/redovisning/penningtvatt>, <https://www.lansstyrelsen.se/stockholm/om-oss/om-lansstyrelsen-stockholm/nyheter/nyheter---stockholm/2025-03-24-redovisningskonsulter-och-skatteradgivare-uppmarksammas-pa-signaler-om-penningtvatt.html>, <https://polisen.se/siteassets/dokument/om-polisen/penningtvatt/vagledning-till-redovisningskonsulter-och-skatteradgivare.pdf>
- Reko: <https://www.far.se/kunskap/yrkesutovning-och-etik/reko/>, <https://www.faronline.se/dokument/far/reko/reko/>
- AI Act art. 50: <https://artificialintelligenceact.eu/article/50/>, <https://www.cooley.com/news/insight/2026/2026-08-03-eu-ai-act-transparency-obligations-take-effect-2-august-2026>. Art. 4 efter Omnibus: <https://lawandtechnology.eu/en/ai-literacy-digital-omnibus-article-4-ai-act/>, <https://fpf.org/blog/the-ai-act-implementation-timeline-what-changes-under-the-ai-omnibus/>
- Cybersäkerhetslagen: <https://pts.se/sakerhet-och-integritet/cybersakerhetslagen/>
- CLOUD Act och DPF: <https://globaldatashield.com/blog/eu-us-data-privacy-framework-2026>

**Befintliga byrå-, skattekonto- och PTL-verktyg (v3.1)**
- Fortnox Skatteverket-koppling: <https://support.fortnox.se/produkthjalp/bokforing/koppla-ihop-skatteverket-med-fortnox>, JSI Skattekonto: <https://www.fortnox.se/integrationer/integration/jsi-skattekonto-ab/skattekonto>
- Fortnox Byråstöd / Reko: <https://www.fortnox.se/integrationer/kategorier/byrastod>, WeSoft Byråstöd: <https://www.fortnox.se/integrationer/integration/tech-by-wesoft-ab/wesoft-byrastod>
- Visma Advisor KYC: <https://www.visma.se/nyheter/nytt-verktyg-hjalper-redovisningsbyraer-motverka-penningtvatt-och-organiserad-brottslighet>, Lundify KYC/AML: <https://bjornlunden.com/se/juridik-kunskap/compliance-kyc-aml/>
- Sanktionsavgifter vid PTL-tillsyn 2022: <https://www.finanslicenser.se/nyheter/penningtvatt/skydda-din-verksamhet/>

**AI och säkerhet**
- Claude data residency (inference_geo us/global): <https://platform.claude.com/docs/en/manage-claude/data-residency>
- Azure OpenAI EU Data Zone / Sweden Central: <https://learn.microsoft.com/en-us/answers/questions/5630048/azure-openai-deployments-difference-between-data-z>. Avbrott 27 jan 2026: <https://windowsforum.com/windows-news.4/azure-openai-sweden-central-outage-january-27-2026-eu-data-residency-challenge.399223/>
- Mistral datalagring: <https://legal.mistral.ai/terms/data-processing-addendum/>, <https://anarlog.so/blog/mistral-data-retention-policy/>
- LLM och finansiell numerik: <https://arxiv.org/html/2311.11944v1> (FinanceBench), <https://arxiv.org/html/2603.20252v1>
- Journal entry testing och falsklarm: <https://arxiv.org/abs/2609.18228>, <https://www.emergentmind.com/topics/journal-entry-tests-jets>
- Prompt injection: <https://genai.owasp.org/llmrisk/llm01-prompt-injection/>. Markdown-exfiltration: <https://wraith.sh/learn/markdown-image-exfiltration>
- RLS och PgBouncer: <https://dev.to/qays_kadhim_c3fea1c94957f/the-set-local-advice-is-right-and-it-understates-the-problem-1f62>, <https://seedfa.st/blog/pgbouncer-transaction-mode>
- Procrastinate: <https://github.com/procrastinate-org/procrastinate>

> **Förbehåll:** flera källor är sekundära (bloggar, sammanställningar) och vissa sidor kunde inte läsas i sin helhet. Siffror om marknadsandelar, priser och licenskostnader samt alla regeldetaljer ska verifieras mot primärkällor och med en auktoriserad redovisningskonsult och jurist innan de används i produkt eller försäljning.
