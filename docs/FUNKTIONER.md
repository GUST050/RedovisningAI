# RedovisningAI – funktioner och hur de fungerar

> Den här texten beskriver programmet ur användarens perspektiv: **vad varje funktion är, vad den gör för konsulten och hur den fungerar bakom kulisserna.**
> Den bygger på planen i [`PLAN.md`](PLAN.md) (v3.1). Märkningen **[MVP]**, **[V1.5]** och **[V2]** anger när funktionen byggs.

---

## Innehåll
1. [Programmet på en minut](#1-programmet-på-en-minut)
2. [En vanlig månad för en konsult](#2-en-vanlig-månad-för-en-konsult)
3. [Funktionerna en och en](#3-funktionerna-en-och-en)
   - 3.1 Koppla kunder
   - 3.2 Import och versioner
   - 3.3 Periodmognad – "hur klar är månaden?"
   - 3.4 Kontroller och regelkatalog
   - 3.5 Kundminne
   - 3.6 Ärenden
   - 3.7 Portföljvyn (startsidan)
   - 3.8 Kundens arbetsyta
   - 3.9 "Förklara" – varför ändrades en siffra?
   - 3.10 Kundfrågor
   - 3.11 Godkänna och låsa en månad
   - 3.12 Kundmötesunderlag och rapporter
   - 3.13 AI-analytikern (fråga fritt)
   - 3.14 Dokumentation och revisionslogg
   - 3.15 Behörigheter och känsliga uppgifter
4. [Funktioner i senare versioner](#4-funktioner-i-senare-versioner)
5. [Så används AI – och så används den inte](#5-så-används-ai--och-så-används-den-inte)
6. [Vad programmet inte gör](#6-vad-programmet-inte-gör)
7. [Ordlista](#7-ordlista)

---

## 1. Programmet på en minut

RedovisningAI är ett **granskningsverktyg för redovisningsbyråer**. Varje natt hämtar det bokföringen för alla byråns kunder, oavsett om kunden har Fortnox, Spiris, Björn Lundén eller något annat system. Sedan gör det tre saker:

1. **Kontrollerar** bokföringen med svenska regler (moms, arbetsgivaravgifter, lån till delägare, obalanser, dubbletter m.m.).
2. **Samlar** alla larm till några få **ärenden** per kund, där varje ärende har en trolig orsak och bevis ända ner till verifikationen. Det **minns** också hur byrån bedömde samma sak förra gången.
3. **Hjälper konsulten att avsluta**: fråga kunden, godkänna månaden, dokumentera granskningen och ta fram underlag till kundmötet.

**Målet:** att konsulten på några minuter per kund ska veta vad som behöver göras, i stället för att gå igenom bokföringen rad för rad eller läsa en lång lista med larm.

```text
Bokföring från alla system
        ↓
Kontroller (fasta regler, inga gissningar)
        ↓
Kundminne ("samma sak som i mars – bedömt OK")
        ↓
Ärenden (3 beslut i stället för 30 larm)
        ↓
Konsulten beslutar  →  frågar kunden  →  godkänner månaden
        ↓
Dokumentation + kundmötesunderlag + rapport
```

---

## 2. En vanlig månad för en konsult

*Exempel: Anna är redovisningskonsult och har 35 kunder. Det är den 8 oktober och september ska granskas.*

**Måndag morgon – startsidan.** Anna öppnar programmet och ser sin portfölj:

```text
MINA KUNDER – SEPTEMBER 2026                          Sortering: behöver dig mest

  Kund          System    Status               Ärenden   Anmärkning
  Bygg & Co AB  Fortnox   Behöver granskning   2 High    Ändrad efter godkännande (aug)
  Café Lilja    SIE-fil   Behöver granskning   1 High    Momssats 12 % efter 1 april?
  Konsult X AB  Spiris    Preliminär           –         Löner ej bokade för sept
  Nord Frakt AB Fortnox   Behöver granskning   0 High    3 ärenden, alla förslag "OK" från kundminnet
  …
  Koppling saknas: 2 kunder (Fortnox-auktorisering har gått ut)
```

**Bygg & Co AB – två ärenden.**
- *Ärende 1: "Leverantörsfaktura från Byggvaruhuset troligen bokförd två gånger."* Programmet har slagit ihop tre larm: en dubblettkandidat, en kostnadsökning på konto 4010 och för hög ingående moms. Anna klickar på ärendet, ser de två verifikationerna sida vid sida och väljer **"Åtgärda"**. Hon makulerar dubbletten i Fortnox, och nästa natts synk stänger ärendet automatiskt.
- *Ärende 2: "Augusti ändrades efter att den godkändes."* Två verifikationer har lagts till i augusti, efter att Anna godkände månaden den 12 september. Programmet visar exakt vilka. Anna ser att kunden har bokat ett sent kvitto och godkänner ändringen med en kommentar.

**Nord Frakt AB – kundminnet gör jobbet.** Tre ärenden, men alla har samma förslag: *"Samma mönster som juni 2026 – bedömt OK av Anna: kvartalsvis försäkringspremie."* Anna kvitterar alla tre med ett klick.

**Café Lilja – fråga kunden.** Ärendet: *"Försäljning bokad med 12 % moms efter 1 april – matmomsen är 6 % till 2027-12-31."* Anna är osäker på om det gäller servering (12 %) eller livsmedel (6 %). Hon klickar på **"Fråga kunden"**. Programmet föreslår en fråga på enkel svenska, Anna justerar den och skickar. Kunden svarar via en säker länk och bifogar ett kassarapportsexempel. Svaret hamnar direkt på ärendet.

**Avslut.** När ärendena är hanterade klickar Anna på **"Godkänn september"**. Månaden låses. Granskningsdokumentationen sparas, och ett utkast till kundmötesunderlag skapas som hon kan redigera och exportera till Word.

**Tid:** ungefär 5–10 minuter per kund i stället för 30–60. *(Det är målet och hypotesen som testas med pilotbyråer.)*

---

## 3. Funktionerna en och en

### 3.1 Koppla kunder [MVP]

**Vad det är:** hur kundens bokföring kommer in i programmet.

**Vad det gör för dig:** du kopplar kunden en gång. Sedan hämtas bokföringen automatiskt varje natt (Fortnox) eller när du laddar upp en fil (övriga system).

**Hur det fungerar:**
- **Fortnox:** du skickar en auktoriseringslänk. En administratör hos kunden, eller du via byråbehörighet, godkänner kopplingen en gång. Programmet hämtar sedan hela räkenskapsåret som en SIE4-fil via Fortnox API varje natt. Vid första kopplingen hämtas även de två föregående åren, så att jämförelser bakåt fungerar direkt.
- **Övriga system (Spiris, Björn Lundén, Hogia, Bokio …):** du exporterar en SIE4-fil och laddar upp den. Du kan ladda upp **många filer samtidigt** (t.ex. en zip-fil). Programmet läser organisationsnumret i varje fil och kopplar den till rätt kund.
- **Kopplingshälsa:** för varje kund visas om kopplingen fungerar, när senaste lyckade hämtning var och eventuella fel (t.ex. "auktoriseringen har gått ut").

**Exempel:** "Konsult X AB – senast hämtad i natt 02:14 ✓" eller "Bygg & Co AB – Fortnox svarar inte sedan 3 dagar ⚠".

---

### 3.2 Import och versioner [MVP]

**Vad det är:** varje gång bokföringen hämtas sparas den som en ny version. Inget skrivs över.

**Vad det gör för dig:**
- Du kan alltid se **vad som ändrats sedan förra gången**: vilka verifikationer som lagts till, ändrats eller tagits bort.
- En rapport du tog fram i augusti ser likadan ut om ett år, även om bokföringen ändrats sedan dess.

**Hur det fungerar:**
1. Originalfilen sparas oförändrad med ett digitalt fingeravtryck (SHA-256), så att det går att bevisa exakt vilken fil analysen byggde på.
2. Filen läses rad för rad. Programmet klarar svenska tecken i det gamla SIE-formatet, rättade verifikationer och brutna räkenskapsår.
3. Varje verifikation får ett eget fingeravtryck. Vid nästa import jämförs fingeravtrycken, och bara det som ändrats sparas som nytt.
4. Allt räknas med exakta decimaltal, aldrig avrundade flyttal. Summorna ska stämma **på öret** mot bokföringssystemets egna rapporter.

---

### 3.3 Periodmognad – "hur klar är månaden?" [MVP]

**Vad det är:** en bedömning av om månadens bokföring är komplett nog att granska, och hur kunden brukar bokföra.

**Vad det gör för dig:** programmet larmar inte i onödan. Många småföretag bokar avskrivningar och semesterlöneskuld bara vid bokslut, eller använder kontantmetoden. Då ser varje månad "konstig" ut om man jämför rakt av.

**Hur det fungerar:** programmet tittar på kundens mönster:
- Bokförs avskrivningar varje månad eller bara i december?
- Används kundfordringar och leverantörsskulder löpande (faktureringsmetoden) eller bara vid årsskiftet (kontantmetoden)?
- Är lönerna bokade? Finns det ungefär lika många verifikationer och bankrörelser som vanligt?

**Resultatet:**
- **Klar:** månaden granskas normalt.
- **Preliminär:** något saknas (t.ex. löner). Månaden kan granskas, men inte godkännas för kundrapport utan att du aktivt överstyr.
- **Låg periodiseringsgrad:** programmet visar hellre rullande 12 månader och hittills i år än månad mot månad, och höjer larmgränserna för sådant som beror på periodisering.

---

### 3.4 Kontroller och regelkatalog [MVP]

**Vad det är:** ett 25-tal automatiska kontroller baserade på svenska regler och god redovisningssed.

**Vad det gör för dig:** programmet hittar felen du annars letar efter manuellt.

**Exempel på kontroller:**

| Område | Exempel |
|---|---|
| Grundläggande | Verifikation där debet ≠ kredit. Ingående balans ≠ förra årets utgående. Luckor i verifikationsnumreringen. Konton utanför kontoplanen |
| Tidpunkt | Sent bokförda poster (kontanta betalningar ska bokas senast nästa arbetsdag) |
| Moms | Fel momssats för datumet (t.ex. matmoms 6 % från 1 april 2026). Momskonton som inte nollställts efter momsperioden. Ingående moms som inte stämmer med kostnaden |
| Lön | Arbetsgivaravgifter som inte stämmer mot lönerna (med hänsyn till sänkt avgift för unga). Skatteskulder som inte nollas varje månad. Semesterlöneskuld som aldrig ändras |
| Balans | Hängkonton med kvarstående saldo. Inventarier utan avskrivningar. Negativt bankkonto. Periodiseringsfond som ska återföras |
| Aktiebolag | Fordran på delägare/närstående, som kan vara ett förbjudet lån (ABL 21 kap.). Eget kapital under halva aktiekapitalet |
| Mönster | Dubblettkandidater. Stora manuella poster i slutet av månaden. Bokning som återförs direkt. Ovanlig kontokombination för just den här kunden. Kostnad som avviker från historiken |

**Hur det fungerar:**
- Kontrollerna är **fasta regler, inte AI-gissningar**. Samma bokföring ger alltid samma resultat.
- Alla regler ligger i en **regelkatalog**. Varje regel har lagstöd (t.ex. "BFL 5 kap. 2 §"), vilka datum den gäller och vilka satser som gäller. När regler ändras (ny momssats, nya arbetsgivaravgifter) uppdateras katalogen, inte programkoden. Gamla rapporter behåller den regelversion som gällde då.
- **Väsentlighet:** byrån, och vid behov varje kund, kan ställa in beloppsgränser så att småsaker inte larmar.
- **Bokslutsposter** (återföringar, periodiseringar, avskrivningar) känns igen och bedöms för sig, eftersom de annars ger mycket brus.
- Programmet **mäter hur ofta varje regel ger rätt**, alltså hur ofta ett larm faktiskt leder till en åtgärd. Regler som mest ger falsklarm hos en byrå får lägre prioritet där.

---

### 3.5 Kundminne [MVP]

**Vad det är:** programmet kommer ihåg hur byrån har bedömt olika saker hos varje kund.

**Vad det gör för dig:**
- **Mindre upprepning:** återkommande saker som redan är förklarade kommer med ett färdigt förslag.
- **Enklare överlämning:** om du är ledig eller slutar ser kollegan direkt hur kunden brukar se ut och varför.

**Hur det fungerar:**
1. När du fattar ett beslut i ett ärende ("OK – kvartalsvis försäkring") sparas beslutet tillsammans med ett **mönster**: vilken regel, vilket konto eller vilken leverantör, ungefärligt belopp och hur ofta det återkommer.
2. När ett nytt larm har samma mönster visas förslaget: *"Samma mönster som juni 2026 – bedömt OK av Anna: kvartalsvis försäkringspremie. [Kvittera] [Utred ändå]"*
3. **En människa kvitterar alltid.** Minnet föreslår, det beslutar aldrig. Allvarliga (High) larm stängs aldrig automatiskt.
4. Bedömningar kan ha ett slutdatum ("gäller till årsskiftet"), så att gamla förklaringar inte lever kvar för evigt.

**Varför det är viktigt:** med tiden samlar byrån en kunskapsbank om varje kund som inte finns någon annanstans. Den gör programmet mer värt ju längre det används.

---

### 3.6 Ärenden [MVP]

**Vad det är:** i stället för en lång lista med larm grupperar programmet larm som hör ihop till **ärenden**. Varje ärende har en trolig orsak.

**Vad det gör för dig:** du fattar ett beslut per problem i stället för per larm.

**Exempel:**

```text
ÄRENDE: Trolig dubbelbokad leverantörsfaktura – Byggvaruhuset          [High]

Trolig orsak:   Samma faktura verkar bokad två gånger (ver. A122 och A128).
Hör ihop med:   • Dubblettkandidat: samma belopp, konto och text, 2 dagar isär
                • Kostnadsökning konto 4010: +48 tkr mot snittet
                • Ingående moms högre än väntat för perioden
Bevis:          ver. A122, ver. A128 (klicka för att se raderna)
Förslag:        Utred / makulera dubbletten
Tidigare:       Inget liknande beslut hos denna kund

[Åtgärda]  [OK – ingen åtgärd]  [Fråga kunden]  [Skjut upp]
```

**Hur det fungerar:**
1. Kontrollerna (3.4) ger larm med bevis.
2. Kundminnet (3.5) letar efter tidigare beslut.
3. **Ärendebyggaren (AI)** läser larmen och bevisen och föreslår vilka som hör ihop och varför. Det kräver bedömning, och där gör AI nytta.
4. AI:n får **inte** hitta på siffror. Alla belopp i ärendet hämtas från programmets egna beräkningar (se avsnitt 5).
5. Varje ärende får status: *Nytt → Under utredning → Fråga kund → Åtgärdat / OK / Undertryckt*. Allt loggas med vem, när och varför.

---

### 3.7 Portföljvyn (startsidan) [MVP]

**Vad det är:** en översikt över alla dina (eller hela byråns) kunder, sorterade efter var du behövs mest.

**Vad det gör för dig:** svarar på frågan *"Var ska jag lägga min tid i dag?"*

**Hur det fungerar:**
- Varje kund får en **prioriteringspoäng** räknad av fasta regler, alltså inte av AI: antal allvarliga ärenden, ändringar efter godkännande, stora förändringar i marginal, hur länge sedan kunden granskades, saknad data och trasig koppling.
- Du ser alltid **varför** en kund ligger högt ("2 High-ärenden + ändrad efter godkännande").
- Filter: per konsult, per bokföringssystem, per status.
- En **"Att göra"-ruta:** "3 kunder saknar septemberdata", "2 kopplingar behöver förnyas", "5 kundfrågor obesvarade sedan över 7 dagar".

---

### 3.8 Kundens arbetsyta [MVP]

**Vad det är:** sidan du kommer till när du klickar på en kund.

**Flikar:**
- **Översikt:** månadens status, ärenden, nyckeltal och viktigaste förändringar.
- **Ärenden:** alla öppna och stängda ärenden (3.6).
- **Resultat- och balansräkning:** månad, hittills i år (YTD) och rullande 12 månader, jämfört med föregående år. Uppställning enligt K2/K3.
- **Nyckeltal:** t.ex. rörelsemarginal, soliditet, kassalikviditet. Varje nyckeltal har en "Så räknas detta"-förklaring. Om underlaget inte räcker visas "otillräckligt underlag" i stället för en missvisande siffra.
- **Transaktioner:** sök och filtrera. Klicka dig från en siffra → konton → verifikationer → enskilda rader → originalfilen.
- **Kundfrågor:** skickade frågor och svar (3.10).
- **Data:** importer, versioner och kopplingshälsa.

**Export:** alla tabeller kan exporteras till **Excel** med verifikationsnummer, så att du kan arbeta vidare där.

---

### 3.9 "Förklara" – varför ändrades en siffra? [MVP]

**Vad det är:** en knapp vid viktiga siffror som visar vad förändringen består av.

**Exempel:** du klickar på "Rörelseresultat: −580 tkr mot i fjol" och får:

```text
Förändring rörelseresultat                    −580 tkr
  Intäkter                                     +520 tkr
  Material                                     −210 tkr
  Personal                                     −330 tkr
  Konsulttjänster                              −410 tkr   ← största förklaringen
  Övrigt                                       −150 tkr
```

Klick på "Konsulttjänster" visar vilka konton och verifikationer som ligger bakom.

**Hur det fungerar:**
- **Bryggan räknas av programmet, inte av AI.** Den är exakt och summerar alltid till totalen.
- AI skriver därefter en kort text: *"Resultatet försämrades främst av högre konsultkostnader, som började i april."* Alla siffror i texten fylls i av programmet (avsnitt 5).
- Texten märks tydligt som AI-genererad. Påståenden som inte kan bevisas med data märks som **hypotes** ("Det kan hänga ihop med …").

---

### 3.10 Kundfrågor [MVP]

**Vad det är:** ett sätt att ställa frågor till kunden direkt från ett ärende och få svaret tillbaka på samma ställe.

**Vad det gör för dig:** du slipper mejla, jaga svar och klistra in dem i dokumentationen.

**Hur det fungerar:**
1. I ett ärende klickar du på **"Fråga kunden"**.
2. Programmet föreslår en fråga på enkel svenska. Du redigerar och skickar. **Inget skickas utan att du godkänt det.**
3. Kunden får en **säker länk** (tidsbegränsad, ingen inloggning krävs i första versionen), svarar och kan bifoga underlag som kvitton och avtal.
4. Svaret och bilagorna kopplas automatiskt till ärendet och syns i dokumentationen.
5. Obesvarade frågor syns i portföljvyn, och påminnelser kan skickas.

**Skydd:** frågor som rör vissa känsliga fynd (se 4.3, penningtvätt) kan inte skickas till kunden. Programmet spärrar det.

---

### 3.11 Godkänna och låsa en månad [MVP]

**Vad det är:** när granskningen är klar godkänner du månaden, och den låses.

**Vad det gör för dig:**
- Du vet exakt vad du godkände: vilken bokföringsversion, vilka regler, vilka ärenden och beslut.
- **Ändringar efter godkännandet upptäcks automatiskt.** Om kunden eller någon annan lägger till, ändrar eller tar bort verifikationer i en godkänd månad byter månaden status till *"Ändrad efter godkännande"*, och du ser exakt vad som ändrats.

**Hur det fungerar:** godkännandet skapar en ögonblicksbild som pekar på exakt de versioner av data, regler och beräkningar som användes. Nästa natts import jämförs mot ögonblicksbilden.

**Månadens status:**
*Ej påbörjad → Data mottagen → Bearbetas → Preliminär → Behöver granskning → Granskad → Godkänd → Rapporterad* (+ *Ändrad efter godkännande*)

---

### 3.12 Kundmötesunderlag och rapporter [MVP]

**Vad det är:** färdiga dokument för kunden och för byråns interna bruk.

**Två olika rapporter, aldrig ihopblandade:**

| Intern rapport | Kundrapport |
|---|---|
| Alla ärenden och beslut | Omsättning, resultat, marginal, likviditet, nyckeltal |
| Datakvalitet och osäkerheter | Viktigaste förändringarna, förklarade |
| Regelversioner och ändringar efter godkännande | 3–7 frågor och råd inför kundmötet |
| | Bara sådant som är markerat som lämpligt för kunden |

**Hur det fungerar:**
- AI skriver ett **utkast**. Du redigerar och godkänner. Inget skickas automatiskt.
- Kundrapporten byggs **bara** av uppgifter som är märkta som lämpliga för kunden. En separat kontroll stoppar interna uppgifter från att hamna där.
- Export till **PDF, Word** (så att du kan redigera vidare) och **Excel** (siffror med verifikationsreferenser). Byråns logotyp och mallar används.

**Exempel på kundmötesfrågor:**
1. Konsultkostnaderna har ökat kraftigt sedan april. Är det tillfälligt eller permanent?
2. Tre nya programvaruabonnemang motsvarar en betydande årskostnad. Används alla?
3. Kundfordringarna växer snabbare än försäljningen. Behöver betalningsvillkoren ses över?

---

### 3.13 AI-analytikern (fråga fritt) [MVP, slutet]

**Vad det är:** en ruta där du kan ställa frågor om kunden på vanlig svenska.

**Exempel på frågor:**
- "Vad har förändrats mest den här månaden?"
- "Varför har marginalen minskat?"
- "Vilka leverantörer har ökat mest i år?"
- "Vad har ändrats sedan jag godkände augusti?"

**Hur det fungerar:**
- AI:n får **inte** läsa databasen fritt. Den använder färdiga, säkra verktyg ("jämför perioder", "visa verifikation", "lista ärenden" …) som bara ger tillgång till **den kund du har öppen**.
- Svaret delas in i **observation** (bevisad med data), **förklaring**, **hypotes** (ej bevisad) och **fråga**.
- Alla siffror fylls i av programmet, och varje påstående länkar till sitt underlag.
- Varje fråga har en gräns för hur mycket AI:n får arbeta, så att kostnaden hålls nere.

---

### 3.14 Dokumentation och revisionslogg [MVP]

**Vad det är:** en automatisk logg över granskningsarbetet.

**Vad det gör för dig:** du kan visa hur du har granskat kunden, t.ex. vid byråns kvalitetskontroll enligt Reko, eller om en kund ifrågasätter något.

**Hur det fungerar:**
- Varje beslut, kvittens, kundfråga, godkännande och export loggas med vem, när, vilken kund, vilken period och motivering.
- Känsliga visningar (t.ex. lönetransaktioner) loggas också.
- **Export per kund och period** som PDF: vad som kontrollerades, vilka ärenden som fanns, hur de hanterades och vem som godkände.

---

### 3.15 Behörigheter och känsliga uppgifter [MVP]

**Roller:**
- **Byråadmin:** användare, kunder, inställningar.
- **Konsult:** arbetar med de kunder hen är tilldelad.
- **Läsare:** kan bara läsa.

**Extra behörigheter:**
- **Lönedata:** krävs för att se enskilda lönetransaktioner. Utan den ser man bara totaler. Viktigt i små bolag, där en lönepost avslöjar en persons lön.
- **PTL-ansvarig:** krävs för att se signaler om penningtvätt (4.3).
- **Godkänna kundrapport.**

**Hur det fungerar:** behörigheten kontrolleras på två nivåer, i programmet och direkt i databasen. Även om en bugg skulle uppstå i programmet kan en konsult inte se en kund hen saknar behörighet till, och en byrå kan aldrig se en annan byrås data.

---

## 4. Funktioner i senare versioner

### 4.1 Leverantörs- och kostnadsanalys [V1.5]
**Vad:** *"Vart går pengarna?"* Kostnader per kategori (IT, lokaler, konsulter …) och per leverantör. Nya och försvunna kostnader, återkommande abonnemang, nivåskiften ("IT-kostnaderna ökade permanent från april") och hur beroende kunden är av sina största leverantörer.
**Hur:** hämtar leverantörsfakturor från Fortnox (och senare Spiris). AI hjälper till att slå ihop leverantörsnamn ("MSFT", "Microsoft Ireland" → Microsoft) och föreslå kategorier. Du bekräftar.

### 4.2 Skattekontoavstämning [V1.5]
**Vad:** jämför konto 1630 i bokföringen med kundens skattekonto hos Skatteverket.
**Hur:** hämtar skattekontot via Skatteverkets API med byråns ombudsbehörighet. Avvikelser blir ärenden. *(Fortnox har redan en egen koppling. Värdet för oss är främst kunder i andra system och att avvikelser hamnar i samma granskningsflöde.)*

### 4.3 Signaler om penningtvätt (PTL) [V1.5]
**Vad:** stöd för byråns skyldigheter enligt penningtvättslagen. Programmet flaggar mönster i bokföringen som myndigheterna pekat ut som varningssignaler, t.ex. ovanligt stora kontantposter eller snabba in- och utflöden utan affärslogik.
**Hur:** fasta regler, **ingen AI**. Syns bara för den som är PTL-ansvarig. **Kan aldrig hamna i kundrapport eller kundfråga**, eftersom byrån inte får avslöja misstankar för kunden. Programmet bedömer inte om något är penningtvätt och rapporterar ingenting själv. Signalerna kan exporteras till byråns befintliga KYC-verktyg.

### 4.4 Budget mot utfall [V1.5]
**Vad:** jämför utfallet med kundens budget, om budgeten finns i bokföringssystemet.

### 4.5 Veckans prioriteringar [V1.5]
**Vad:** en kort sammanfattning varje måndag: *"Det här behöver du göra den här veckan."*

### 4.6 Regelbevakning [V1.5]
**Vad:** AI bevakar Skatteverket, Bolagsverket, BFN och branschorganisationerna och föreslår uppdateringar av regelkatalogen (t.ex. en ny momssats). **En expert godkänner alltid** innan något ändras. AI:n ser aldrig kunddata i den här funktionen.

### 4.7 Fler kopplingar och underlag [V1.5–V2]
- Spiris API (automatisk hämtning i stället för filuppladdning) [V1.5]
- Björn Lundén API, SIE 5 [V2]
- Fakturabilder: AI läser fakturor, och transaktioner matchas mot faktura och bank [V2]
- Bankdata, kundportal med BankID, prognoser [V2+]
- Rangordning av larm som lär sig av byråns egna beslut [V2]

---

## 5. Så används AI – och så används den inte

**Grundregel: AI är aldrig sifferfacit.** Alla belopp, summor, procent och nyckeltal räknas av programmets egen beräkningsmotor med exakt matematik.

**Hur det garanteras:** AI:n skriver aldrig siffror själv. Den skriver text med **platshållare** som pekar på beräknade värden, och programmet fyller i siffrorna:

```text
AI skriver:        "Konsultkostnaderna ökade med {f:konsult_diff} ({f:konsult_pct})."
Programmet visar:  "Konsultkostnaderna ökade med 410 tkr (+46 %)."
```

Om AI:n ändå försöker skriva en siffra själv underkänns texten och skrivs om. En separat **granskare** kontrollerar dessutom varje text:
- Stöds varje påstående av data?
- Påstår texten ett orsakssamband som inte är bevisat?
- Innehåller en kundtext interna uppgifter?

| AI gör | AI gör inte |
|---|---|
| Grupperar larm till ärenden och föreslår trolig orsak | Räknar summor, nyckeltal eller avvikelser |
| Föreslår vilket konto som hör till vilken kategori | Avgör om något är fel. Det gör konsulten |
| Föreslår frågor till kunden | Skickar något till kunden |
| Skriver utkast till kommentarer och rapporter | Stänger allvarliga ärenden |
| Svarar på frågor med hjälp av säkra verktyg | Läser databasen fritt eller ser andra kunder |
| Slår ihop leverantörsnamn [V1.5] | Används för penningtvättssignaler |

**Dataskydd vid AI-användning:**
- AI:n får bara det underlag som behövs för just den uppgiften, aldrig hela bokföringen.
- Namn och personnummer ersätts med koder innan något skickas till AI-tjänsten.
- Enskilda lönetransaktioner skickas aldrig, bara totaler.
- AI-tjänsten körs inom EU, sparar inte data längre än nödvändigt och tränar inte på kundernas data.
- Texter från bokföringen (t.ex. en verifikationstext med "ignorera alla instruktioner …") behandlas alltid som data, aldrig som instruktioner. AI-svaren kan inte visa externa bilder eller länkar, så de kan inte användas för att föra ut data.

---

## 6. Vad programmet inte gör

- **Bokför inte.** Programmet läser bokföringen men ändrar aldrig i kundens bokföringssystem. Rättelser gör du i Fortnox, Spiris osv.
- **Ersätter inte konsulten.** Programmet föreslår, du beslutar.
- **Anklagar inte kunden.** Det visar *granskningsprioritet*, inte "sannolikhet för bedrägeri".
- **Deklarerar och betalar inte.** Moms-, skatte- och arbetsgivardeklarationer görs i ordinarie system.
- **Är inte bokföringsarkivet.** Kundens bokföringssystem är fortfarande det officiella arkivet.
- **Rapporterar inte till myndigheter.** Varken till Skatteverket eller Finanspolisen.

---

## 7. Ordlista

| Ord | Betydelse |
|---|---|
| **SIE4** | Svenskt standardformat för att föra över bokföring mellan program |
| **Import / version** | En hämtning av kundens bokföring. Varje hämtning sparas separat |
| **Kontroll / regel** | En automatisk granskning, t.ex. "momskonton nollställda?" |
| **Larm / fynd** | Något en kontroll har hittat |
| **Ärende** | En eller flera larm som hör ihop, med en trolig orsak och ett beslut |
| **Kundminne** | Byråns sparade bedömningar per kund, som återanvänds som förslag |
| **Periodmognad** | Hur komplett och löpande periodiserad månadens bokföring är |
| **Ögonblicksbild** | Den låsta versionen av en godkänd månad |
| **Ändrad efter godkännande** | Status när bokföringen ändrats i en redan godkänd månad |
| **Väsentlighet** | Beloppsgräns under vilken avvikelser inte larmar |
| **Regelkatalog** | Förteckningen över alla regler, med lagstöd, giltighetsdatum och satser |
| **Reko** | Svensk standard för redovisningstjänster (Srf konsulterna och FAR) |
| **PTL** | Penningtvättslagen |
| **Observation / Förklaring / Hypotes / Fråga** | AI-textens fyra typer: bevisat, förklarat med data, ej bevisat, fråga att ställa |
