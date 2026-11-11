# Analiza komentarzy kampanii

Prosta aplikacja backendowa do pobierania publicznych komentarzy z postów i rolek na Instagramie oraz zliczania komentarzy, które zawierają pytanie o cenę, prośbę o link lub negatywną opinię. Dane są pobierane przez aktora Apify Store `apify/instagram-comment-scraper`.

API zapisuje żądanie jako asynchroniczny job w MongoDB. Worker pobiera job, uruchamia aktora Apify, klasyfikuje komentarze słowami kluczowymi i zapisuje wyniki w dokumencie kampanii. `GET /campaigns/{campaign_id}` zwraca definicję, posty, komentarze, metryki i podsumowanie w jednym dokumencie.

## Funkcje

- Utworzenie kampanii z listą publicznych URL-i postów i rolek Instagram.
- Asynchroniczne przetwarzanie przez workery; endpoint API nie czeka na zakończenie scrapingu.
- Klasyfikowanie komentarzy w trzech niezależnych kategoriach: pytania o cenę, prośby o link i komentarze negatywne.
- Liczenie liczby komentarzy i odsetka każdej kategorii dla posta i całej kampanii.
- Pobranie kompletnego dokumentu kampanii jednym żądaniem `GET`.
- Idempotentne ponawianie żądań z nagłówkiem `Idempotency-Key` oraz deduplikacja komentarzy według ich ID źródłowego.
- Atomowe pobieranie jobów z MongoDB, umożliwiające uruchomienie wielu workerów.
- Ograniczenie liczby postów, komentarzy na post i maksymalnego kosztu runu Apify.

## Jak rozumieć metryki

Dla `N` komentarzy zwróconych przez aktora i kategorii `x`:

```text
count_x = liczba komentarzy, których tekst pasuje do słów kluczowych kategorii x
share_pct_x = 100 × count_x / N
```

Kategorie mogą się nakładać. Na przykład komentarz „Ile kosztuje? Podeślij link” zostanie policzony jako pytanie o cenę i prośba o link. Gdy `N = 0`, udział kategorii wynosi `null`.

Klasyfikacja jest prostą heurystyką, a nie modelem sentymentu. Kod normalizuje wielkość liter i polskie znaki, a następnie dopasowuje frazy zdefiniowane w [`app/metrics.py`](app/metrics.py). Nie mierzy rzeczywistej intencji zakupowej, zasięgu, wyświetleń ani konwersji. Wyniki obejmują wyłącznie komentarze, które aktor udostępnił w swoim zbiorze danych.

## Wymagania

- Python 3.12 do uruchomienia lokalnego.
- Docker z Docker Compose do uruchomienia całego stosu w kontenerach.
- MongoDB. Można użyć usługi `mongo` z pliku Compose albo własnej instancji.
- Konto Apify i token API (`APIFY_TOKEN`), jeśli mają być przetwarzane rzeczywiste joby.

## Uruchomienie w Dockerze

W katalogu projektu skopiuj przykładowy plik konfiguracji:

```bash
cp .env.example .env
```

Otwórz `.env` i wpisz swój token Apify w `APIFY_TOKEN`. Nie umieszczaj tokenu w repozytorium ani w zgłoszeniach. Plik `.env` jest ignorowany przez Git i wykluczony z kontekstu budowania obrazu.

Uruchom API, workera i MongoDB:

```bash
docker compose up --build
```

Aby uruchomić trzy workery:

```bash
docker compose up --build --scale worker=3
```

API jest dostępne pod `http://localhost:8000`, a dokumentacja interaktywna FastAPI pod `http://localhost:8000/docs`. MongoDB jest publikowane lokalnie na porcie `27017`. Dane są przechowywane w wolumenie Compose `mongo-data`.

Usługi mogą wystartować z pustym `APIFY_TOKEN`, ale worker oznaczy wysłany job jako `failed`, jeśli tokenu zabraknie. Samo uruchomienie stosu nie rozpoczyna scrapingu.

## Uruchomienie lokalne

Ta ścieżka wymaga Pythona 3.12 oraz MongoDB dostępnego pod `mongodb://localhost:27017`. Jeśli chcesz użyć MongoDB z Compose, uruchom tylko tę usługę:

```bash
docker compose up -d mongo
```

Utwórz wirtualne środowisko i zainstaluj przypięte zależności:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Przygotuj `.env` i uzupełnij `APIFY_TOKEN`. Wczytaj zmienne do bieżącej powłoki, ustaw lokalny adres MongoDB i uruchom API:

```bash
cp .env.example .env
set -a
. ./.env
set +a
export MONGO_URI=mongodb://localhost:27017
uvicorn app.api:app --reload
```

W drugim terminalu aktywuj to samo środowisko, wczytaj zmienne i uruchom workera:

```bash
source .venv/bin/activate
set -a
. ./.env
set +a
export MONGO_URI=mongodb://localhost:27017
python -m app.worker
```

`uvicorn` uruchamia serwer API. `python -m app.worker` uruchamia proces, który nasłuchuje kolejki jobów w MongoDB.

## Konfiguracja

Wartości można ustawić w `.env` dla Docker Compose albo jako zmienne środowiskowe. Nazwy i domyślne wartości wynikają z `.env.example`, `docker-compose.yml` oraz kodu aplikacji.

| Zmienna | Domyślna wartość | Zastosowanie |
|---|---|---|
| `APIFY_TOKEN` | pusta | Token Apify; wymagany do uruchomienia aktora. |
| `APIFY_ACTOR_ID` | `apify/instagram-comment-scraper` | ID aktora w Apify Store. |
| `APIFY_MAX_TOTAL_CHARGE_USD` | `15.00` | Limit opłaty przekazywany do runu aktora. |
| `MONGO_DATABASE` | `campaign_analytics` | Nazwa bazy MongoDB. |
| `MONGO_PUBLISHED_PORT` | `27017` | Port MongoDB publikowany na hoście przez Compose. |
| `API_PUBLISHED_PORT` | `8000` | Port API publikowany na hoście przez Compose. |
| `WORKER_POLL_SECONDS` | `2` | Przerwa między sprawdzeniami kolejki przez worker. |
| `JOB_LEASE_SECONDS` | `86400` | Czas dzierżawy joba; po wygaśnięciu job może zostać przejęty ponownie. |
| `LOG_LEVEL` | `INFO` | Poziom logowania workerów. |
| `MONGO_URI` | `mongodb://localhost:27017` lokalnie; `mongodb://mongo:27017` w Compose | Adres MongoDB. W Compose wartość jest ustawiana w konfiguracji usługi. |

`.env.example` zawiera konfigurację dla Compose. Nie zawiera rzeczywistego tokenu ani poświadczeń MongoDB.

## Korzystanie z API

### 1. Utworzenie kampanii i joba

Podaj publiczny URL posta lub rolki. Przykładowy URL jest wyłącznie wzorem — zastąp go właściwym adresem.

```bash
curl -i http://localhost:8000/campaigns \
  -H 'Content-Type: application/json' \
  -H 'Idempotency-Key: oskar-kuban-2026-11' \
  -d '{
    "name": "Oskar Kuban",
    "platform": "instagram",
    "post_urls": ["https://www.instagram.com/p/ABCdef1234g/"],
    "comments_per_post_limit": 100
  }'
```

Żądanie zwraca status HTTP `202 Accepted` oraz `job_id` i `campaign_id`. Ten sam `Idempotency-Key` dla tego samego żądania zwróci istniejący job zamiast tworzyć kolejny.

Pola żądania:

- `name` — nazwa kampanii; domyślnie `Oskar Kuban`.
- `platform` — obecnie obsługiwane jest `instagram`.
- `post_urls` — od 1 do 50 URL-i postów lub rolek Instagram; duplikaty są usuwane.
- `comments_per_post_limit` — limit od 1 do 100; domyślnie `100`.

### 2. Sprawdzenie statusu joba

```bash
curl http://localhost:8000/jobs/job:REPLACE_WITH_ID
```

Job może mieć status `queued`, `running`, `done` lub `failed`. Odpowiedź zawiera także `apify_run_id`, `cost_usd` i ewentualny komunikat błędu.

### 3. Pobranie dokumentu kampanii

```bash
curl http://localhost:8000/campaigns/campaign:REPLACE_WITH_ID
```

Dokument zawiera definicję kampanii, osadzone posty i komentarze, metryki dla poszczególnych postów, podsumowanie oraz `last_job_id`. Zastąp `REPLACE_WITH_ID` wartościami zwróconymi przez POST.

### 4. Sprawdzenie zdrowia API

```bash
curl http://localhost:8000/health
```

Endpoint zwraca `{"status":"ok"}`, jeśli API może połączyć się z MongoDB; w przeciwnym razie zwraca HTTP `503`.

## Przykładowy przebieg

1. Klient wysyła `POST /campaigns` z URL-em i limitem komentarzy.
2. API zapisuje dokument kampanii oraz job o statusie `queued`, po czym od razu zwraca identyfikatory.
3. Jeden z workerów atomowo przejmuje job w MongoDB i uruchamia aktora przez oficjalny pakiet `apify-client`.
4. Worker klasyfikuje zwrócone komentarze, zapisuje snapshot kampanii i metryki, a job oznacza jako `done` albo `failed`.
5. Klient pobiera status przez `GET /jobs/{job_id}` i kompletny wynik przez `GET /campaigns/{campaign_id}`.

## Testy

Testy wymagają działającego MongoDB. Możesz uruchomić usługę MongoDB w tle:

```bash
docker compose up -d mongo
```

Następnie, w aktywnym wirtualnym środowisku z zainstalowanym `requirements.txt`, uruchom:

```bash
TEST_MONGO_URI=mongodb://localhost:27017 pytest
```

`tests/test_metrics.py` sprawdza metryki na polskim fixture. `tests/test_idempotency.py` zapisuje ponownie ten sam sample aktora i weryfikuje, że nie przybywa duplikatów komentarzy. `tests/conftest.py` tworzy tymczasową bazę testową i usuwa ją po teście. Jeśli MongoDB jest niedostępne, testy zostaną pominięte.

Przykładowe dane wyjściowe aktora znajdują się w [`tests/fixtures/apify_output_sample.json`](tests/fixtures/apify_output_sample.json), a fixture klasyfikacji w [`tests/fixtures/keyword_comments.json`](tests/fixtures/keyword_comments.json). Format wejścia i wyjścia jest oparty na [dokumentacji aktora Apify](https://apify.com/apify/instagram-comment-scraper).

## Typowe problemy

- **Job kończy się statusem `failed` z informacją o braku `APIFY_TOKEN`:** uzupełnij token w lokalnym `.env` i uruchom ponownie job z nowym kluczem idempotencji.
- **`/health` zwraca HTTP `503`:** sprawdź, czy MongoDB działa i czy `MONGO_URI` wskazuje właściwy adres. W Compose API używa hosta `mongo`; lokalny proces używa zwykle `localhost`.
- **Testy są pomijane:** testowy fixture pomija testy, gdy nie może połączyć się z MongoDB. Uruchom MongoDB i ustaw `TEST_MONGO_URI`.
- **Aktor zwraca mniej komentarzy niż limit:** `comments_per_post_limit` jest limitem maksymalnym, a nie gwarantowaną liczbą. Wynik zależy od danych dostępnych aktorowi dla publicznego posta.
- **Koszt runu jest pusty:** `cost_usd` pochodzi z rekordu runu Apify. Jeśli API nie zwróci wartości kosztu, pole pozostaje `null`.

## Struktura repozytorium

```text
app/
  api.py          endpointy FastAPI i modele odpowiedzi API
  worker.py       pobieranie i obsługa jobów
  models.py       schematy Pydantic, walidacja URL-i i klucze biznesowe
  db.py           indeksy oraz operacje i agregacje MongoDB
  apify.py        adapter oficjalnego klienta Apify
  metrics.py      klasyfikacja słowami kluczowymi
tests/
  fixtures/       przykładowe dane aktora i komentarze do testów
  conftest.py     tymczasowa baza MongoDB dla testów
  test_metrics.py test agregacji metryk
  test_idempotency.py test ponownego zapisu
Dockerfile        obraz Python 3.12 dla API i workerów
docker-compose.yml usługi api, worker i mongo
requirements.txt przypięte wersje zależności Pythona
.env.example     przykładowe zmienne środowiskowe
.gitignore       wyklucza m.in. .env i .venv
.dockerignore    wyklucza sekrety i pliki developerskie z kontekstu obrazu
```

## Dokumentacja i dane źródłowe

- [FastAPI — dokumentacja](https://fastapi.tiangolo.com/)
- [Pydantic — dokumentacja](https://docs.pydantic.dev/)
- [PyMongo — dokumentacja](https://www.mongodb.com/docs/languages/python/pymongo-driver/current/)
- [Apify Python client — dokumentacja](https://docs.apify.com/api/client/python/docs/overview)
- [Apify Instagram Comments Scraper — opis aktora](https://apify.com/apify/instagram-comment-scraper)

## Informacje do uzupełnienia przed publikacją

Repozytorium nie zawiera pliku licencji ani potwierdzonych danych autora, dlatego README ich nie deklaruje. Jeśli projekt ma być publikowany jako open source, wybierz licencję i dodaj odpowiedni plik. Nie dodawaj do GitHub pliku `.env` ani tokenu Apify.

README przygotowano na podstawie kodu w `app/`, konfiguracji `Dockerfile`, `docker-compose.yml`, `requirements.txt`, `.env.example`, `.gitignore`, `.dockerignore`, testów i fixture’ów w `tests/` oraz przykładowego wyniku działania API. Przed publikacją sprawdź, czy opisane zmienne i przykładowe URL-e odpowiadają konfiguracji używanej w danym środowisku.
