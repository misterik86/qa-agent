import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from prometheus_client import Counter, Histogram, generate_latest, CONTENT_TYPE_LATEST
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address
from starlette.responses import Response

from app.api import auth, dashboard, developer, api_keys, payments, runs, scenarios, schedules, webhooks, admin, suites, credentials, public
from app.api.deps import get_current_user
from app.config import settings
from app.models.user import User

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger(__name__)

limiter = Limiter(
    key_func=get_remote_address,
    default_limits=[settings.RATE_LIMIT_IP],
)

REQUEST_COUNT = Counter("http_requests_total", "HTTP requests", ["method", "path"])
REQUEST_TIME = Histogram("http_request_duration_seconds", "HTTP latency", ["method", "path"])
AI_FAILURES = Counter("ai_failures_total", "AI model failures")
TEST_RUNS = Counter("test_runs_total", "Test runs", ["mode"])


@asynccontextmanager
async def lifespan(app: FastAPI):
    from app.database import Base, engine

    # SQLite (local dev): schema is created directly from models.
    # PostgreSQL (Docker/prod): schema is managed by alembic (runs in the entrypoint),
    # so create_all must NOT run here — it would create ENUM types that alembic
    # then tries to recreate, failing with "type ... already exists".
    if engine.dialect.name != "postgresql":
        Base.metadata.create_all(bind=engine)
        logger.info("Database tables ensured (non-postgres)")
    yield


app = FastAPI(
    title="QA Agent — AI-агент автоматизированного тестирования",
    description="Веб-сервис автоматизированного тестирования веб-приложений с AI без написания скриптов.",
    version="1.0.0",
    lifespan=lifespan,
    docs_url="/api/docs",
    openapi_url="/api/openapi.json",
    redoc_url="/api/redoc",
)

app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.FRONTEND_URL, "http://localhost:8000", "http://127.0.0.1:8000",
                   "http://localhost:8080", "http://127.0.0.1:8080"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def metrics_middleware(request: Request, call_next):
    # Normalize paths with ids to keep metric cardinality bounded.
    path = request.url.path
    for token in ("/api/runs/", "/api/scenarios/", "/api/schedules/", "/api/webhooks/", "/api/keys/", "/ws/time/"):
        if token in path:
            head = path.split(token, 1)[0] + token
            path = head + "{id}"
            break
    REQUEST_COUNT.labels(request.method, path).inc()
    with REQUEST_TIME.labels(request.method, path).time():
        response = await call_next(request)
    return response


app.include_router(auth.router)
app.include_router(scenarios.router)
app.include_router(runs.router)
app.include_router(schedules.router)
app.include_router(webhooks.router)
app.include_router(payments.router)
app.include_router(api_keys.router)
app.include_router(developer.router)
app.include_router(dashboard.router)
app.include_router(admin.router)
app.include_router(suites.router)
app.include_router(credentials.router)
app.include_router(public.router)


@app.get("/health")
def health():
    return {"status": "ok", "app": settings.APP_NAME}


@app.get("/metrics")
def metrics():
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.get("/api/limits/usage")
def get_usage(request: Request, user: "User" = Depends(get_current_user)):
    from app.services.limits import monthly_used, usage_percent

    return {"used": monthly_used(user), "percent": usage_percent(user)}


# Time prediction over WebSocket: updates every 5s
class ConnectionManager:
    def __init__(self):
        self.connections: dict[str, WebSocket] = {}

    async def connect(self, ws: WebSocket, run_id: str):
        await ws.accept()
        self.connections[run_id] = ws

    def disconnect(self, run_id: str):
        self.connections.pop(run_id, None)

    async def send(self, run_id: str, payload: dict):
        ws = self.connections.get(run_id)
        if ws:
            try:
                await ws.send_json(payload)
            except Exception:
                self.disconnect(run_id)


manager = ConnectionManager()


@app.websocket("/ws/time/{run_id}")
async def ws_time(websocket: WebSocket, run_id: str):
    await manager.connect(websocket, run_id)
    try:
        while True:
            from app.database import SessionLocal
            from app.models.test_run import TestRun

            db = SessionLocal()
            try:
                run = db.query(TestRun).filter(TestRun.id == run_id).first()
                if run:
                    from app.services.time_prediction import predict_duration

                    elapsed = round(run.duration_seconds, 1)
                    timeout = (run.timeout_minutes or 5) * 60
                    await manager.send(run_id, {
                        "elapsed": elapsed,
                        "remaining_est": max(0, round(timeout - elapsed, 1)),
                        "status": run.status.value if hasattr(run.status, "value") else run.status,
                        "predicted": predict_duration(db, run.owner_id, max(1, run.total_steps or 1)),
                    })
            finally:
                db.close()
            import asyncio

            await asyncio.sleep(5)
    except WebSocketDisconnect:
        manager.disconnect(run_id)
    except Exception:
        manager.disconnect(run_id)


# Serve frontend static files
frontend_dir = settings.FRONTEND_DIR
if not os.path.isdir(frontend_dir):
    # resolve relative to project root (backend/../frontend or backend/../../frontend)
    for cand in (
        os.path.join(os.path.dirname(__file__), "..", "..", "frontend"),
        os.path.join(os.getcwd(), "..", "frontend"),
        os.path.join(os.getcwd(), "frontend"),
    ):
        cand = os.path.abspath(cand)
        if os.path.isdir(cand):
            frontend_dir = cand
            break

if os.path.isdir(frontend_dir):
    app.mount("/static", StaticFiles(directory=os.path.join(frontend_dir, "static")), name="static")
else:
    app.mount("/static", StaticFiles(directory="static", check_dir=False), name="static")


@app.get("/")
def index():
    from fastapi.responses import FileResponse

    index_path = os.path.join(frontend_dir, "index.html")
    if os.path.exists(index_path):
        return FileResponse(index_path)
    return Response("QA Agent API работает. Документация: /api/docs", media_type="text/plain")


for _page in ("dashboard.html", "legal.html", "privacy.html", "admin.html", "report.html", "faq.html"):
    @app.get(f"/{_page}")
    def _serve_page(_page: str = _page):
        from fastapi.responses import FileResponse

        path = os.path.join(frontend_dir, _page)
        if os.path.exists(path):
            return FileResponse(path)
        return Response("Not found", status_code=404)


@app.get("/report/{token}")
def _serve_public_report(token: str):
    from fastapi.responses import FileResponse

    path = os.path.join(frontend_dir, "report.html")
    if os.path.exists(path):
        return FileResponse(path)
    return Response("Not found", status_code=404)