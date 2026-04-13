# Book Discovery — Automatyczny radar nowości non-fiction

Co dwa tygodnie automatycznie skanuje [lubimyczytac.pl](https://lubimyczytac.pl) w poszukiwaniu nowych polskich książek non-fiction z wysokimi ocenami, zapisuje wyniki w Google Sheets i wysyła digest e-mailowy.

**Koszt: 0 zł** — korzysta wyłącznie z darmowych tierów.

---

## Jak to działa

```
GitHub Actions (1. i 15. każdego miesiąca)
        │
        ▼
Python (book_discovery/)
  ├─ Pobiera nowości z lubimyczytac.pl — 5 kategorii
  ├─ Filtruje: ocena ≥ 7.0, liczba ocen ≥ 20, data wydania w oknie czasowym
  ├─ Deduplikuje względem istniejącej bazy
  ├─ Generuje opisy AI przez Gemini (opcjonalnie)
  └─ Zapisuje nowe pozycje do Google Sheets
        │
        ▼
Google Sheets (baza danych)
        │
        ▼
Google Apps Script (codziennie o 10:00)
  └─ Wysyła digest HTML na Twój adres Gmail (tylko gdy są nowe książki)
```

---

## Wymagania wstępne

- Konto Google (Gmail + Google Sheets + Google Cloud)
- Konto GitHub

---

## Konfiguracja — jednorazowa (~45 min)

### Krok 1 — Google Cloud: Service Account

1. Wejdź na [console.cloud.google.com](https://console.cloud.google.com)
2. Utwórz nowy projekt (np. `book-discovery`)
3. **APIs & Services → Enable APIs** — włącz dwa:
   - **Google Sheets API**
   - **Google Drive API**
4. **IAM & Admin → Service Accounts → Create Service Account**
   - Nazwa: `book-discovery-bot`
   - Kliknij Continue → Done (bez przypisywania roli)
5. Kliknij w utworzone konto → zakładka **Keys → Add Key → Create new key → JSON**
6. Zapisz pobrany plik JSON — zawiera klucz prywatny

---

### Krok 2 — Google Sheets: arkusz bazy danych

1. Utwórz nowy arkusz na [sheets.google.com](https://sheets.google.com)
2. Z pliku JSON (krok 1) skopiuj wartość `"client_email"`
3. W arkuszu: **Share → wklej client_email → Editor → Share**
4. Skopiuj **Sheet ID** z URL arkusza:
   `https://docs.google.com/spreadsheets/d/`**`TU_JEST_ID`**`/edit`

---

### Krok 3 — Gemini API Key (opcjonalne — opisy AI)

1. Wejdź na [aistudio.google.com/apikey](https://aistudio.google.com/apikey)
2. Kliknij **Create API key** i skopiuj klucz

Bez klucza scraper działa normalnie — kolumna `description_ai` pozostaje pusta.

---

### Krok 4 — GitHub Secrets

W repozytorium: **Settings → Secrets and variables → Actions → New repository secret**

Dodaj trzy sekrety:

| Nazwa sekretu | Wartość |
|---|---|
| `GOOGLE_SHEETS_CREDENTIALS_JSON` | Cała zawartość pliku JSON z kroku 1 (od `{` do `}`) |
| `GOOGLE_SHEET_ID` | Sheet ID z kroku 2 |
| `GEMINI_API_KEY` | Klucz z kroku 3 (lub pusty string `""` jeśli nie używasz) |

> Sekrety widzi tylko właściciel repozytorium — nigdy nie trafiają do kodu ani logów publicznych.

---

### Krok 5 — Merge do main

Workflow GitHub Actions działa tylko z brancha `main`. Utwórz Pull Request z brancha `claude/book-search-app-yEZup` do `main` i go zmerguj.

---

### Krok 6 — Google Apps Script: wysyłka e-mail

1. Wejdź na [script.google.com](https://script.google.com) → **New project**
2. Zmień nazwę projektu na `BookDiscovery`
3. Usuń domyślny kod i wklej całą zawartość pliku `apps_script/BookDiscovery.gs`
4. Zapisz (Ctrl+S)
5. **Project Settings (⚙️) → Script properties → Add script property** — dodaj:

| Property | Wartość |
|---|---|
| `SPREADSHEET_ID` | Sheet ID z kroku 2 |
| `RECIPIENT_EMAIL` | Twój adres Gmail |
| `GEMINI_API_KEY` | Klucz Gemini (opcjonalnie) |

6. Wybierz funkcję **`setupTrigger`** z dropdownu → **Run ▶**
   - Przy pierwszym uruchomieniu kliknij **Review permissions → Allow**
   - Ustawia codzienny automatyczny check o 10:00

---

### Krok 7 — Pierwsze uruchomienie

#### Przez GitHub Actions (zalecane)

1. Wejdź w repozytorium → zakładka **Actions**
2. Wybierz workflow **Book Discovery**
3. Kliknij **Run workflow**
4. Po ~15 minutach sprawdź arkusz — powinny pojawić się książki z ostatnich 2 miesięcy

#### Lokalnie

```bash
pip install -r requirements.txt
cp .env.example .env        # uzupełnij wartości w .env
python -m book_discovery.main
```

---

### Krok 8 — Wyślij pierwszy e-mail

W Apps Script wybierz funkcję **`sendNewBooksDigest`** → **Run ▶**

E-mail dotrze w ciągu kilku sekund. Od teraz Apps Script sprawdza arkusz codziennie o 10:00 i wysyła maila automatycznie gdy są nowe książki.

---

## Harmonogram

| Co | Kiedy |
|---|---|
| Scraping (GitHub Actions) | 1. i 15. każdego miesiąca, godz. 8:00 UTC |
| Wysyłka e-mail (Apps Script) | Codziennie o 10:00 — tylko gdy są nowe książki |

Oba można uruchomić ręcznie w dowolnej chwili.

---

## Struktura arkusza Google Sheets

### Zakładka `books` — baza danych

| Kolumna | Opis | Edytowalne |
|---|---|---|
| `book_id` | Numeryczne ID z URL lubimyczytac.pl | — |
| `title` | Tytuł | — |
| `author` | Autor | — |
| `category` | Kategoria po polsku | — |
| `rating` | Ocena / 10 | — |
| `ratings_count` | Liczba ocen | — |
| `url` | Link do lubimyczytac.pl | — |
| `isbn` | ISBN-13 | — |
| `cover_url` | URL okładki | — |
| `description` | Opis wydawniczy | — |
| `description_ai` | Opis AI (Gemini) | — |
| `tags` | Tagi gatunkowe | — |
| `first_seen_date` | Data pierwszego znalezienia | — |
| `emailed_date` | Data wysłania w digest | — |
| `empik_url` | Link wyszukiwania w Empiku | — |
| **`already_read`** | **Wpisz `TRUE` aby wykluczyć z przyszłych digestów** | ✓ |
| **`notes`** | **Twoje notatki** | ✓ |

### Zakładka `preferences` — ustawienia

Edytuj bezpośrednio w arkuszu:

| Klucz | Domyślnie | Opis |
|---|---|---|
| `min_rating` | `7.0` | Minimalna średnia ocena (0–10) |
| `min_ratings_count` | `20` | Minimalna liczba ocen |

### Zakładka `email_log` — historia uruchomień

Automatycznie uzupełniana — data, liczba znalezionych książek, ewentualne błędy.

---

## Kategorie

Scraper przeszukuje 5 kategorii na lubimyczytac.pl:

| Kategoria | ID | URL |
|---|---|---|
| Literatura faktu / Reportaż | 46 | `/ksiazki/k/46/literatura-faktu` |
| Biografie i Autobiografie | 40 | `/ksiazki/k/40/biografia-autobiografia-pamietnik` |
| Historia | 64 | `/ksiazki/k/64/historia` |
| Psychologia i Nauki społeczne | 67 | `/ksiazki/k/67/nauki-spoleczne-psychologia-socjologia-itd` |
| Literatura popularnonaukowa | 107 | `/ksiazki/k/107/literatura-popularnonaukowa` |

Aby dodać kategorię: edytuj listę `CATEGORIES` w `book_discovery/config.py`.

---

## Logika scrapowania

### Dwa przejścia na kategorię

**Przejście 1 — po dacie wydania** (`orderBy=publishDate&desc=1`)
- Strony od najnowszych do najstarszych
- Zatrzymuje się natychmiast gdy napotka książkę starszą niż okno dat
- Łapie świeże tytuły z mniejszą liczbą ocen

**Przejście 2 — po ocenach** (`orderBy=ratings&desc=1`)
- Strony od najwyżej ocenianych
- Pomija książki już znalezione w przejściu 1 (wspólny `seen_ids`)
- Zatrzymuje się gdy pobrane strony nie przynoszą nowych wyników
- Łapie popularne nowości które mogły nie trafić do przejścia po dacie

### Okno dat

| Uruchomienie | Okno | Opis |
|---|---|---|
| Pierwsze (pusty arkusz) | 60 dni wstecz | Zapełnia bazę startową |
| Każde następne | 14 dni wstecz | Tylko najnowsze wydania |

### Deduplikacja

Klucz: `book_id` (numeryczne ID z URL). Książka już obecna w arkuszu nigdy nie zostanie dodana ponownie — niezależnie od kategorii ani run.

---

## Zmienne środowiskowe

Plik `.env` (lokalnie) lub GitHub Secrets (Actions):

```env
GOOGLE_SHEETS_CREDENTIALS_JSON={"type":"service_account","project_id":"...","private_key":"..."}
GOOGLE_SHEET_ID=1BxiMVs0XRA5nFMdKvBdBZjgmUUqptlbs74OgVE2upms
GEMINI_API_KEY=AIza...
```

Szablon: `.env.example`

---

## Struktura projektu

```
book_discovery/
├── main.py              # Orkiestrator (config → scraping → dedup → AI → zapis)
├── scraper.py           # Scraper lubimyczytac.pl (curl_cffi + BeautifulSoup)
├── sheets_client.py     # Google Sheets read/write (gspread)
├── ai_descriptions.py   # Opisy AI przez Gemini API
├── models.py            # Dataclass Book
└── config.py            # Stałe: kategorie, progi, nazwy zakładek

apps_script/
└── BookDiscovery.gs     # Google Apps Script: czyta arkusz → wysyła e-mail

scripts/
└── setup_sheet.py       # Jednorazowa inicjalizacja zakładek arkusza

.github/workflows/
└── book_discovery.yml   # GitHub Actions cron + workflow_dispatch
```

---

## Troubleshooting

### Scraper znalazł 0 książek
Sprawdź logi: GitHub Actions → ostatni run → **Artifacts → run-log-N**

Częste przyczyny:
- Błędne ID kategorii — strona zwraca HTML bez kart książek (logi wypisują snippet HTML)
- Wszystkie książki w oknie dat zostały już dodane do arkusza w poprzednim runie

### Workflow nie widoczny w GitHub Actions
Plik `.github/workflows/book_discovery.yml` musi być na branchu **main** — workflow pojawia się dopiero po zmergowaniu.

### E-mail nie wysłany
- W Apps Script: **Executions** — sprawdź czy `sendNewBooksDigest` jest wywoływana
- **Script Properties** — sprawdź czy `SPREADSHEET_ID` i `RECIPIENT_EMAIL` są ustawione
- Kolumna `emailed_date` w arkuszu — jeśli wypełniona, książki uznano za wysłane

### Chcę oznaczyć przeczytaną książkę
W zakładce `books` wpisz `TRUE` w kolumnie `already_read`. Książka nie pojawi się w przyszłych digestach.

### Chcę zmienić próg oceny
W zakładce `preferences` zmień wartość `min_rating` lub `min_ratings_count`. Zmiana obowiązuje od następnego runu.
