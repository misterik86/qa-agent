# QA Agent — AI-агент автоматизированного тестирования веб-приложений

Веб-сервис, который позволяет QA-инженерам, разработчикам и владельцам продуктов
автоматизировать регрессионное и функциональное тестирование веб-сайтов с
использованием ИИ **без написания скриптов**.

Пользователь описывает сценарий на русском языке (или выбирает автоматическое
сканирование страницы); система выполняет тестирование в изолированной песочнице
(Docker + Playwright), формирует детальный отчёт и предоставляет планирование,
интеграции с мессенджерами, CI/CD и тест-менеджерами.

## Как это сделано

Проект спроектирован и реализован автором с помощью **opencode**
(AI-assisted development): архитектура, декомпозиция на модули, контракты между
ними и критерии приёмки определены вручную, код сгенерирован и отревьюен. Это
осознанный выбор под задачу — продукт целиком состоит из LLM-контура, и
контроль того, что модель выдаёт корректный результат, здесь и есть основная
инженерная работа.

Что сделано руками и не делегировано: архитектура мультиагентной системы,
дизайн двухуровневой валидации, модель данных и миграции, схема изоляции
прогона, SSRF-защита, обход резолвера Docker, модель подписки и юридическая
часть, CI. Всё это видно в коммитах и в структуре репозитория.

## Статус проекта

- Код открыт, лицензия — см. репозиторий
- Живое демо: ⟨ССЫЛКА — вставить после деплоя⟩
- Подписочная модель и оплата спроектированы и реализованы в коде; первые
  платящие пользователи ещё не запущены
- Приглашаю к пилотированию: QA-команды, у которых есть боли с поддержкой
  автотестов. Напишите в Telegram @yurkiselev или откройте issue

## Возможности

- Аутентификация: регистрация с подтверждением email, вход, refresh-токены с ротацией, восстановление пароля
- Сценарии: версионирование, статусы (Draft/Active/Deprecated/Failed), метки, шаблоны, откат
- Два режима: «Конкретный сценарий» и «Проверить всю страницу» (до 50 элементов)
- Двухуровневый валидатор: синтаксический (правила) + семантический (AI), кэш в Redis
- Мультиагентная система: Стратег, Валидатор, Диспетчер, Навигатор, Сканер, Судья, Хранитель, Страж
- Песочница: каждый тест в отдельном Docker-контейнере (CPU/RAM/таймаут), SSRF-защита
- Отчёты: PDF, HTML, JSON, JUnit XML; скриншоты при ошибках
- Планировщик: Celery Beat, уведомления в Telegram / VK / email с ретраями
- REST API + API-ключи + исходящие вебхуки
- Тарифы: Trial 3 дня, Pro 3 500 ₽/мес, Enterprise 15 000 ₽/мес, оплата ЮKassa
- Мониторинг: Prometheus + Grafana
- Локализация: русский (основной), переключение на английский

## Быстрый старт (Docker)

```bash
cp .env.example .env
docker compose build          # собирает backend + sandbox
docker compose up -d          # поднимает всё: postgres, redis, backend, worker, beat, mailhog, prometheus, grafana
```

После старта:

| Сервис          | URL                          |
|-----------------|------------------------------|
| Веб-интерфейс   | http://localhost:8000        |
| Swagger/OpenAPI | http://localhost:8000/api/docs|
| MailHog (почта) | http://localhost:8025        |
| Prometheus      | http://localhost:9090        |
| Grafana         | http://localhost:3000 (admin/admin) |

> **Важно про sandbox.** По умолчанию `SANDBOX_RUNNER=local` — тесты выполняются
> Playwright в процессе воркера. Для полноценной изоляции установите
> `SANDBOX_RUNNER=docker` (нужен доступ воркера к docker socket) и предварительно
> соберите образ: `docker build -f docker/Dockerfile.sandbox -t qa-agent-sandbox:latest .`
>
> На Windows (Docker Desktop) системный UDP-резолвер внутри контейнеров может не
> работать, из-за чего браузер получает `ERR_NAME_NOT_RESOLVED`. Runner
> автоматически резолвит недоступные домены через TCP-DNS (8.8.8.8/1.1.1.1) и
> передаёт их в Chromium через `--host-resolver-rules` — отдельная настройка не
> требуется.

## Быстрый старт (без Docker, для разработки)

```bash
python -m venv .venv
# Windows: .\.venv\Scripts\activate
pip install -r backend/requirements.txt
pip install -r sandbox/requirements.txt
playwright install chromium

# запуск API (нужны postgres + redis, либо поменяйте DATABASE_URL на sqlite в .env)
uvicorn app.main:app --reload --port 8000        # из каталога backend

# воркер и планировщик (нужен redis)
celery -A app.tasks.celery_app worker --loglevel=info
celery -A app.tasks.celery_app beat --loglevel=info
```

Для локальной отладки без SMTP/Redis включите в `.env`:
`DEV_AUTO_VERIFY=true` (автоподтверждение email) и `CELERY_ALWAYS_EAGER=true`
(задачи выполняются в процессе). Для почты удобно поднять только MailHog:
`docker run -d -p 8025:8025 -p 1025:1025 mailhog/mailhog:v1.0.1`.

## Переменные окружения

Все настройки — через `.env` (см. `.env.example`):

- `SECRET_KEY`, `FERNET_KEY` — секреты (обязательно смените в проде)
- `DATABASE_URL` — PostgreSQL
- `REDIS_URL` — Redis/Celery
- `SMTP_*` — исходящая почта (прод: Яндекс 360 / Mail.ru / Postmark / Sendgrid; локально — MailHog)
- `DEEPSEEK_API_KEY`, `GIGACHAT_API_KEY`, `YANDEXGPT_API_KEY` — AI-модели (fallback-цепочка)
- `SANDBOX_RUNNER`, `SANDBOX_CPU_LIMIT`, `SANDBOX_MEM_LIMIT`, `SANDBOX_DNS_WHITELIST` — песочница
- `YOOKASSA_SHOP_ID`, `YOOKASSA_SECRET_KEY` — ЮKassa (тест-режим `YOOKASSA_TEST_MODE=true`)
- `TELEGRAM_BOT_TOKEN`, `VK_API_TOKEN` — уведомления
- `TESTRAIL_URL`, `TESTRAIL_USER`, `TESTRAIL_PASSWORD` — интеграция TestRail/Xray (Enterprise)

## Тесты

```bash
cd backend
DATABASE_URL="sqlite:///./test.db" python -m pytest ../tests -q -m "not network"
```

Юнит-тесты покрывают auth, валидатор (синтаксис/семантика/SSRF), диспетчер,
отчёты (PDF/HTML/JSON/JUnit), лимиты, платежи (вебхуки ЮKassa), таски и полный
E2E-флоу (регистрация → сценарий → запуск → отчёты). Покрытие — `pytest --cov=app`.

Сетевые E2E-тесты (`@pytest.mark.network`) требуют живой сети и Playwright
и по умолчанию пропускаются. Тот же набор тестов проходит и против
PostgreSQL: `DATABASE_URL="postgresql+psycopg2://..." python -m pytest ../tests -m "not network"`.

Миграции базы: `cd backend && alembic upgrade head` (SQLite по умолчанию,
PostgreSQL — при `DATABASE_URL`). Цикл `upgrade`/`downgrade` проверен на
PostgreSQL 16.

## Структура проекта

```
backend/app/
  api/        — REST API (auth, scenarios, runs, schedules, webhooks, payments, keys, dashboard, developer)
  core/       — security, crypto, ssrf, audit
  models/     — SQLAlchemy-модели
  services/   — email, ai, validator, limits, reports, messengers, payments, webhooks, testrail, time_prediction
  agent/      — стратег, диспетчер, навигатор, сканер, судья, хранитель, страж, песочница, раннер
  tasks/      — Celery: запуск тестов, планировщик, уведомления, ретраи, очистка
sandbox/      — изолированный Playwright-раннер (образ qa-agent-sandbox)
frontend/     — лендинг + личный кабинет (Bootstrap, vanilla JS)
monitoring/   — Prometheus-конфиг
tests/        — pytest-тесты
```

## API (кратко)

| Метод | Endpoint | Описание |
|-------|----------|----------|
| POST | `/api/auth/register` | Регистрация |
| POST | `/api/auth/login` | Вход (access + refresh) |
| POST | `/api/auth/refresh` | Ротация refresh-токена |
| POST | `/api/auth/forgot-password` | Восстановление пароля |
| POST | `/api/scenarios` | Создание сценария |
| POST | `/api/scenarios/validate` | Валидация сценария |
| POST | `/api/runs` | Запуск теста (согласие обязательно) |
| GET  | `/api/runs/{id}/report/{json,html,pdf,junit}` | Скачивание отчёта |
| POST | `/api/schedules` | Расписание |
| POST | `/api/payments/create` | Создание оплаты ЮKassa |
| POST | `/api/v1/tests/run` | Запуск теста по API-ключу (CI/CD) |
| WS   | `/ws/time/{run_id}` | Прогноз времени выполнения |

Полная документация: `/api/docs` (Swagger).

## Юридические аспекты

Пользовательское соглашение и политика конфиденциальности размещены на сайте
(`/legal.html`, `/privacy.html`). Перед запуском каждого теста требуется
подтверждение: «Я имею право тестировать этот сайт». Сервис предназначен для
тестирования сайтов, к которым у пользователя есть законное право доступа
(собственные, клиентские с договором, тестовые). Данные обрабатываются в
соответствии с 152-ФЗ «О персональных данных» и GDPR.
