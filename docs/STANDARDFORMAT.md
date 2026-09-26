# Standardformat och importformat

All bokföring som kommer in i RedovisningAI – SIE-fil, Fortnox, CSV- eller Excel-export – översätts
först till **samma normaliserade modell** (`domain/ledger.py`). Nyckeltal, kontroller, jämförelser och
rapporter arbetar bara mot den modellen och vet aldrig vilket format uppgifterna kom i.

Modellen kan exporteras och läsas in igen som **standardformatet**: JSON, versionerat och exakt.

```text
SIE 1–4 (.se/.si)           ─┐
Fortnox (API → SIE4)        ─┤
CSV / Excel (verifikations-  ├─→  standardmodellen (Ledger)  ─→  nyckeltal, jämförelser, kontroller, rapporter
lista, huvudbok)            ─┤          │
Standardformat (.json)      ─┘          └─→  export: redovisningai.ledger v1.0 (JSON)
```

## Importformat

| Format | Känns igen på | Ersätter vid import | Anmärkning |
|---|---|---|---|
| SIE typ 1–4 | `#FLAGGA`, `#SIETYP`, `#VER` … i filen (inte filändelsen) | Hela räkenskapsår | CP437/Windows-1252/UTF-8, `#RTRANS`/`#BTRANS`, brutna år. Föregående år (`#RAR -1`) blir sammandrag med IB/UB/RES. |
| Fortnox | Kopplingen hämtar SIE4 per år via API | Hela räkenskapsår | Samma tolkning som SIE-fil. |
| CSV | Rubrikrad med verifikation, datum, konto och belopp | Filens månader | Semikolon, komma, tab eller `\|`. UTF-8, UTF-16 och Windows-1252. |
| Excel (.xlsx) | Arbetsbok med sådan rubrikrad i något blad | Filens månader | Datum- och talceller läses som värden. Äldre `.xls` stöds inte – spara som `.xlsx`. |
| Standardformat (.json) | `"format": "redovisningai.ledger"` | Hela räkenskapsår | Belopp som decimalsträngar; flyttal avvisas. |

Zip-filer laddas upp via massuppladdningen; varje fil kopplas till kund via organisationsnumret.

### CSV och Excel – kolumner som känns igen

Rubrikerna jämförs utan hänsyn till versaler, punkter och å/ä/ö. Rubrikraden får ligga en bit ner i
filen (rapportrubrik, bolagsnamn och period ovanför är tillåtna).

| Fält | Exempel på rubriker |
|---|---|
| Verifikation | Vernr, Ver.nr, Verifikation, Verifikationsnummer, Voucher |
| Serie + nummer (i stället för ovan) | Serie / Verifikationsserie + Nummer / Nr |
| Datum | Datum, Bokföringsdatum, Verifikationsdatum, Transaktionsdatum |
| Konto | Konto, Kontonr, Kontonummer (får innehålla namnet: "1930 Företagskonto") |
| Kontonamn | Kontonamn, Benämning, Kontobeskrivning |
| Belopp | Belopp (debet positivt) – eller Debet och Kredit |
| Text | Text, Beskrivning, Verifikationstext |
| Radtext | Transaktionsinfo, Trans.info, Radtext, Specifikation |
| Kostnadsställe / projekt | Kostnadsställe, Ks, Kst / Projekt, Projnr |
| Antal, registreringsdatum, signatur | Antal, Registreringsdatum, Signatur |
| Saldo | Saldo (används bara för IB-rader) |

Stöds:

- **Platta listor** – en rad per konteringsrad.
- **Grupperade verifikationslistor** – verifikationsnummer, datum och text står bara på första raden.
- **Huvudböcker** – konto som rubrik och verifikationsrader under; raderna byggs ihop till
  verifikationer igen.
- **Belopp** i svenska och engelska format: `1 234,50`, `1.234,50`, `1,234.50`, `−500`, `500-`, `(500)`.
- **Ingående balanser** från rader med verifikation "IB" eller texten "Ingående balans".
  Rader med "Utgående balans", "Summa" och liknande hoppas över (och räknas).
- **Organisationsnummer och period** läses från rader ovanför rubriken (t.ex. "Period 2026-01-01 – 2026-03-31").

Det som inte står i filen hittas inte på:

- **Saknas IB** markeras året `opening_balances_status: "missing"`. Resultatnyckeltal räknas, men
  balansposter (kassa, soliditet, likviditet, rörelsekapital) blir *otillräckligt underlag* – tills IB
  finns, t.ex. från föregående års SIE-fil (då härleds IB ur UB och ett ej bokslutsfört resultat förs
  till eget kapital) eller från IB-rader.
- **Bara de månader filen täcker** räknas som kända. En export för januari–mars gör inte april–december
  till noll.
- **En del-årsexport ersätter bara sina egna månader.** Verifikationer i andra månader, tidigare IB och
  budget behålls. En rättad export för samma månader ersätter verifikationerna där (och tar bort dem som
  saknas).
- Verifikationer som inte balanserar, rader som inte går att tolka och antaget räkenskapsår redovisas
  med radnummer. Balanserar många verifikationer inte varnar importen för att exporten verkar sakna rader.

Räkenskapsår: bolagets kända räkenskapsår används; annars antas kalenderår (eller startmånad enligt
`--fiscal-year-start` i kommandoraden).

## Standardformatet `redovisningai.ledger` v1.0

```json
{
  "format": "redovisningai.ledger",
  "version": "1.0",
  "company": { "name": "Testbolaget AB", "org_number": "556123-4567", "currency": "SEK" },
  "source": { "system": "sie_file", "program": "Fortnox 3.0", "files": [{ "name": "…", "sha256": "…" }] },
  "accounts": [{ "number": 1930, "name": "Företagskonto", "type": "T", "sru": null }],
  "dimensions": [{ "id": "1", "name": "Kostnadsställe" }],
  "objects": [{ "dimension": "1", "id": "10", "name": "Administration" }],
  "fiscal_years": [
    {
      "start": "2026-01-01",
      "end": "2026-12-31",
      "has_vouchers": true,
      "opening_balances_status": "known",
      "covered_months": null,
      "opening_balances": [{ "account": 1930, "amount": "52000.00" }],
      "closing_balances": [],
      "results": [],
      "period_balances": [],
      "budget": [{ "period": "2026-01", "account": 3001, "amount": "-12000.00" }],
      "vouchers": [
        {
          "series": "A", "number": "1", "date": "2026-01-05", "text": "Kundfaktura 1",
          "registered": "2026-01-05", "signature": "Gustav T", "source_line": 51,
          "content_hash": "…",
          "rows": [
            { "account": 1510, "amount": "12500.00", "date": null, "text": null, "quantity": null,
              "objects": [], "status": "normal", "source_line": 53 }
          ]
        }
      ]
    }
  ],
  "summary": { "fiscal_years": 1, "accounts": 1, "vouchers": 1, "rows": 1 }
}
```

| Fält | Betydelse |
|---|---|
| `amount` | Decimalsträng. Debet positivt, kredit negativt (SIE-konventionen). |
| `has_vouchers` | `false` = året finns bara som sammandrag (IB/UB/RES), t.ex. föregående år i en SIE-fil. Helår kan då jämföras, enskilda månader inte. |
| `opening_balances_status` | `known` eller `missing` (se ovan). |
| `covered_months` | `null` = hela räkenskapsåret; annars de månader (`ÅÅÅÅ-MM`) som verifikationslistan täcker. |
| `status` (rad) | `normal`, `added` (#RTRANS) eller `removed` (#BTRANS, räknas inte i saldon). |
| `source_line` | Radnummer i originalfilen, för spårbarhet ända ner till källan. |
| `content_hash` | Verifikationens fingeravtryck. Stämmer det inte vid inläsning har innehållet ändrats efter exporten (varning). |

Serialiseringen är stabil: samma bokföring ger samma JSON. Strukturfel (fel typ, saknat fält,
överlappande räkenskapsår) avvisar filen med sökväg till felet, t.ex.
`$.fiscal_years[0].vouchers[3].rows[1].amount: ogiltigt belopp`.

## Var det används

- **Webben:** fliken *Data* tar emot alla format ovan och visar vilket format och vilka kolumner som
  tolkades. *Exportera standardformat* laddar ner bolagets bokföring (kräver behörigheten Lönedata,
  eftersom exporten innehåller lönerader).
- **API:** `POST /api/companies/{id}/imports`, `POST /api/imports/bulk`,
  `GET /api/companies/{id}/export/ledger.json`.
- **Kommandorad:** `redovisningai convert FIL … --out bokforing.json`; `analyze` och `compare` läser
  samma format.
- **Kod:** `redovisningai.standard` – `load_source`, `load_ledger`, `to_standard`, `from_standard`,
  `dumps`, `loads`.
