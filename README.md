# Book Discovery

Co dwa tygodnie automatycznie skanuje [lubimyczytac.pl](https://lubimyczytac.pl) w poszukiwaniu nowych polskich książek non-fiction i wysyła Ci digest emailowy z nowymi pozycjami.

**Kryteria filtrowania:** ocena ≥ 7,0 / 10 oraz ≥ 20 ocen

**Kategorie:** Literatura faktu, Biografie, Historia, Psychologia, Literatura popularnonaukowa

**Koszt: całkowicie bezpłatne.**

---

## Jak to działa

```
GitHub Actions (co 2 tygodnie)
  └─ Python scraper
       ├─ Scraping lubimyczytac.pl (curl_cffi + BeautifulSoup)
       ├─ Opisy AI via Gemini (opcjonalne, darmowe)
       └─ Zapis nowych książek do Google Sheets

Google Apps Script (codziennie o 10:00)
  └─ Sprawdza czy są nowe książki w arkuszu
       └─ Jeśli tak → wysyła email via GmailApp (bez hasła)
            └─ Oznacza książki jako wysłane w arkuszu
```

**Bezpieczeństwo — co jest publiczne:**
- Kod w repo (`.py`, `.gs`, `.yml`) — bez żadnych danych wrażliwych ✅
- Sekrety GitHub — zaszyfrowane, niewidoczne nawet w publicznym repo ✅
- Właściwości Apps Script — prywatne w edytorze Apps Script ✅

---

## Jednorazowa konfiguracja (~45 minut)

### Krok 1 — Google Cloud (dostęp do Sheets)

1. Wejdź na [console.cloud.google.com](https://console.cloud.google.com) → **Nowy projekt** → nazwa: `book-discovery`
2. **API i usługi → Włącz API** → wyszukaj i włącz:
   - **Google Sheets API**
   - **Google Drive API**
3. **API i usługi → Dane logowania → Utwórz dane logowania → Konto usługi**
   - Nazwa: `book-bot` → Utwórz → Gotowe
4. Kliknij konto usługi → zakładka **Klucze** → **Dodaj klucz → Utwórz nowy klucz → JSON**
   - Pobierz plik JSON. **Nie commituj go do repo.**
5. Skopiuj wartość `client_email` z pliku JSON (wygląda jak `book-bot@projekt.iam.gserviceaccount.com`)

### Krok 2 — Google Sheets

1. Wejdź na [sheets.google.com](https://sheets.google.com) → utwórz nowy arkusz
2. Nazwij go: **Book Discovery**
3. **Udostępnij** arkusz adresowi `client_email` z kroku 1 → uprawnienia: **Edytor**
4. Skopiuj ID arkusza z URL:
   ```
   https://docs.google.com/spreadsheets/d/TO_JEST_ID/edit
   ```

### Krok 3 — Klucz Gemini API (opcjonalny, darmowy)

1. Wejdź na [aistudio.google.com/apikey](https://aistudio.google.com/apikey)
2. **Utwórz klucz API** → skopiuj

### Krok 4 — Upublicznij repo

GitHub Actions jest bezpłatny bez limitu minut dla **publicznych repozytoriów**.

**Settings → General → Danger Zone → Change visibility → Public**

### Krok 5 — Sekrety GitHub

**Settings → Secrets and variables → Actions → New repository secret**

Dodaj **3 sekrety** (nie 5 jak wcześniej — brak Gmail):

| Nazwa sekretu | Wartość |
|---|---|
| `GOOGLE_SHEETS_CREDENTIALS_JSON` | Pełna zawartość pliku JSON z kroku 1 |
| `GOOGLE_SHEET_ID` | ID arkusza z kroku 2 |
| `GEMINI_API_KEY` | Klucz z kroku 3 (lub pomiń jeśli nie chcesz opisów AI) |

### Krok 6 — Google Apps Script (wysyłka emaili)

1. Wejdź na [script.google.com](https://script.google.com) → **Nowy projekt**
2. Wklej całą zawartość pliku `apps_script/BookDiscovery.gs`
3. Zapisz projekt (Ctrl+S)
4. **Ustawienia projektu → Właściwości skryptu** → dodaj:

   | Właściwość | Wartość |
   |---|---|
   | `SPREADSHEET_ID` | ID arkusza z kroku 2 |
   | `RECIPIENT_EMAIL` | Twój adres email (na który mają przychodzić maile) |
   | `GEMINI_API_KEY` | Klucz z kroku 3 (opcjonalnie) |

5. W edytorze wybierz funkcję **`setupTrigger`** → kliknij **Uruchom**
   - Przy pierwszym uruchomieniu Google poprosi o uprawnienia — zaakceptuj
   - To ustawia codzienne sprawdzanie o 10:00

### Krok 7 — Inicjalizacja arkusza

Uruchom skrypt konfiguracyjny lokalnie (lub pomiń — pierwsze uruchomienie GitHub Actions zrobi to automatycznie):

```bash
pip install -r requirements.txt

# Ustaw zmienne środowiskowe (lub stwórz plik .env na podstawie .env.example)
export GOOGLE_SHEETS_CREDENTIALS_JSON='{ ...pełny JSON... }'
export GOOGLE_SHEET_ID='twoje_id_arkusza'
export GEMINI_API_KEY='twój_klucz'  # opcjonalne

python scripts/setup_sheet.py
```

### Krok 8 — Test

1. **Actions** → **Book Discovery** → **Run workflow** → uruchom ręcznie
2. Po ~5-10 minutach sprawdź Google Sheets — powinny pojawić się nowe książki
3. Uruchom `sendNewBooksDigest` w edytorze Apps Script → sprawdź email

---

## Harmonogram

| Co | Kiedy |
|---|---|
| GitHub Actions scraping | 1. i 15. każdego miesiąca o 8:00 UTC |
| Apps Script — sprawdzenie i wysyłka emaila | Codziennie o 10:00 (wysyła tylko gdy są nowe książki) |

---

## Konfiguracja przez arkusz

W zakładce **preferences** możesz zmieniać ustawienia bez modyfikowania kodu:

| Klucz | Domyślnie | Opis |
|---|---|---|
| `min_rating` | `7.0` | Minimalna ocena (skala 0–10) |
| `min_ratings_count` | `20` | Minimalna liczba ocen |

---

## Oznaczanie przeczytanych książek

W zakładce **books** znajdź dowolną książkę i wpisz `TRUE` w kolumnie `already_read`.
Ta książka nie pojawi się w kolejnych emailach.

---

## Struktura projektu

```
book_discovery/        Python scraper (GitHub Actions)
  main.py              Główny orchestrator
  scraper.py           Scraping lubimyczytac.pl
  sheets_client.py     Odczyt/zapis Google Sheets
  ai_descriptions.py   Opisy AI via Gemini
  models.py            Dataclass Book
  config.py            Konfiguracja i stałe

apps_script/
  BookDiscovery.gs     Google Apps Script — wysyłka emaili

scripts/
  setup_sheet.py       Jednorazowa inicjalizacja arkusza

.github/workflows/
  book_discovery.yml   GitHub Actions cron
```

---

## Rozwiązywanie problemów

**Scraping nie działa (błąd 403):**
Scraper używa `curl_cffi` do imitowania Chrome. Jeśli problem nadal występuje, zaktualizuj wersję `impersonate` w `scraper.py`.

**Email nie przychodzi:**
Sprawdź logi w edytorze Apps Script (Wykonania → ostatnie uruchomienie).
Upewnij się że właściwości skryptu są ustawione poprawnie.

**Arkusz nie jest aktualizowany:**
Sprawdź czy konto usługi ma uprawnienia Edytora do arkusza.

**Logi GitHub Actions:**
Actions → ostatni run → pobierz artefakt `run-log-N`.
