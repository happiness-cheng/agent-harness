"""本机Cookie身份与CSRF门；状态权威在应用层，不信任前端owner参数。"""

from contextlib import asynccontextmanager
from pathlib import Path
import secrets
from uuid import UUID

from fastapi import FastAPI, Request, BackgroundTasks
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.trustedhost import TrustedHostMiddleware
from pydantic import BaseModel, ConfigDict, Field

from min_agent.application import GoalParameters
from min_agent.events import SessionEvents
from min_agent.notifications import Notifications
from min_agent.artifacts import ArtifactStore


class _Request(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")


class NewSession(_Request):
    label: str = Field(default="新会话", min_length=1, max_length=60)


class Input(_Request):
    text: str = Field(min_length=1, max_length=20000)


class Mode(_Request):
    mode: str


class WebPermission(_Request):
    enabled: bool


class MemoryPermission(_Request):
    enabled: bool
    scenario_ids: list[str] | None = Field(default=None, max_length=10)


class Shutdown(_Request):
    instance_id: str


class Confirm(_Request):
    proposal_id: str
    continue_task: bool = False


class Resume(_Request):
    checkpoint_id: str | None = None
    task_id: str | None = None
    text: str = Field(default="继续已确认任务", min_length=1, max_length=20000)


class Control(_Request):
    kind: str
    task_id: str | None = None
    text: str = Field(default="", max_length=20000)
    scope_changed: bool = True


def create_app(harness, *, identity=None):
    token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
    static = Path(__file__).parent / "static"
    @asynccontextmanager
    async def lifespan(app):
        harness.start_scheduler()
        try:
            yield
        finally:
            harness.close()
            app.state.harness_closed = True
    app = FastAPI(title="Agent Harness", docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)
    from min_agent.long_memory.integration import install_routes
    install_routes(app,harness)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost"])

    @app.middleware("http")
    async def boundary(request, call_next):
        origin = request.headers.get("origin")
        if origin is not None and origin.rstrip("/") != str(request.base_url).rstrip("/"):
            return JSONResponse({"error": "origin_refused"}, status_code=403)
        if request.url.path.startswith("/api/") and request.url.path != "/api/bootstrap":
            if not secrets.compare_digest(request.cookies.get("harness_session", ""), token):
                return JSONResponse({"error": "authentication_required"}, status_code=401)
        if request.method in {"POST", "PUT", "PATCH", "DELETE"}:
            if not secrets.compare_digest(request.headers.get("x-harness-csrf", ""), csrf):
                return JSONResponse({"error": "csrf_refused"}, status_code=403)
            try: size = int(request.headers.get("content-length", "-1"))
            except ValueError: size = -1
            if not 0 <= size <= 64000:
                return JSONResponse({"error": "body_size_refused"}, status_code=413)
        response = await call_next(request)
        response.headers.update({"X-Content-Type-Options": "nosniff", "Referrer-Policy": "no-referrer",
            "Content-Security-Policy": "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'",
            "Cache-Control": "no-store"})
        return response

    @app.exception_handler(PermissionError)
    async def permission_error(request, error): return JSONResponse({"error": "access_or_confirmation_refused"}, status_code=403)
    @app.exception_handler(ValueError)
    async def value_error(request, error): return JSONResponse({"error": "invalid_request"}, status_code=400)
    @app.exception_handler(RuntimeError)
    async def conflict(request, error): return JSONResponse({"error": "task_state_conflict"}, status_code=409)
    @app.exception_handler(KeyError)
    async def missing(request, error): return JSONResponse({"error": "not_found"}, status_code=404)

    @app.get("/api/bootstrap")
    def bootstrap():
        response = JSONResponse({"csrf": csrf, "demo": harness.demo, "sessions": harness.list_sessions(),
            "instance_id": identity['instance_id'] if identity else None,
            "can_shutdown": identity is not None and getattr(app.state,'shutdown_callback',None) is not None})
        response.set_cookie("harness_session", token, httponly=True, samesite="strict", secure=False)
        return response

    @app.get("/_adrian/identity")
    def service_identity():
        if identity is None:return JSONResponse({'error':'identity_not_configured'},status_code=404)
        return dict(identity,ready=True,provider='demo' if harness.demo else getattr(harness.model,'_provider','custom'),
                    model_requests=getattr(harness.model,'request_count',None))

    @app.post('/api/adrian/shutdown',status_code=202)
    def shutdown(body: Shutdown, background: BackgroundTasks):
        callback = getattr(app.state,'shutdown_callback',None)
        if identity is None or callback is None:
            return JSONResponse({'error':'shutdown_not_available'},status_code=404)
        if not secrets.compare_digest(body.instance_id,identity['instance_id']):
            raise RuntimeError('服务实例已变化，请刷新页面')
        background.add_task(callback)
        return {'status':'closing'}

    @app.get("/api/sessions")
    def sessions(): return harness.list_sessions()
    @app.post("/api/sessions", status_code=201)
    def new_session(body: NewSession): return harness.create_session(body.label)
    @app.get("/api/sessions/{session_id}")
    def session(session_id: UUID): return harness.snapshot(session_id)
    @app.post("/api/sessions/{session_id}/input", status_code=202)
    def task_input(session_id: UUID, body: Input):
        harness.submit(session_id, body.text); return {"queued": True}
    @app.post("/api/sessions/{session_id}/mode")
    def mode(session_id: UUID, body: Mode):
        return harness.set_mode(session_id, body.mode).model_dump(mode="json")
    @app.post("/api/sessions/{session_id}/web-permission")
    def web_permission(session_id: UUID, body: WebPermission):
        harness.allow_web(session_id, body.enabled); return {"enabled": body.enabled}
    @app.post("/api/sessions/{session_id}/memory-permission")
    def memory_permission(session_id: UUID, body: MemoryPermission):
        return harness.configure_memory(session_id,body.enabled,body.scenario_ids)
    @app.get('/api/sessions/{session_id}/memory/evidence/{identity}')
    def memory_evidence(session_id: UUID, identity: str):
        from min_agent.long_memory.delivery import read_delivered_evidence
        context = harness.context(session_id)
        preferences = harness.sessions.extension(context,'long_memory_preferences',{})
        return read_delivered_evidence(harness.memory,context,identity,preferences.get('scenario_ids',[]))
    @app.post("/api/sessions/{session_id}/proposal")
    def proposal(session_id: UUID, body: GoalParameters):
        context = harness.context(session_id)
        if harness.sessions.snapshot(context).task and harness.sessions.snapshot(context).task.status == "running":
            raise RuntimeError("运行中请使用显式引导")
        return harness.propose(context, body.model_dump()).model_dump(mode="json")
    @app.post("/api/sessions/{session_id}/confirm")
    def confirm(session_id: UUID, body: Confirm):
        harness.confirm(session_id, body.proposal_id)
        if body.continue_task:
            saved = harness.sessions.snapshot(harness.context(session_id))
            harness.resume(session_id, task_id=str(saved.task.task_id) if saved.task else None,
                           text="用户已定向确认当前提案，按模式与范围继续；保留先前结果。")
        return {"confirmed": True}
    @app.post("/api/sessions/{session_id}/resume", status_code=202)
    def resume(session_id: UUID, body: Resume):
        harness.resume(session_id, **body.model_dump()); return {"resuming": True}
    @app.post("/api/sessions/{session_id}/control", status_code=202)
    def control(session_id: UUID, body: Control):
        events = SessionEvents(harness.sessions, harness.context(session_id))
        if body.kind == "stop": events.stop()
        elif body.kind == "steer": events.steer(UUID(body.task_id), body.text, scope_changed=body.scope_changed)
        elif body.kind == "dispatch": harness.dispatch(session_id, body.text)
        else: raise ValueError("控制类型无效")
        return {"accepted": True}
    @app.get("/api/sessions/{session_id}/events")
    def events(session_id: UUID, after: int = 0):
        saved = harness.snapshot(session_id)
        sequence = saved["events"]["sequence"]
        return {"sequence": sequence, "changed": sequence > after, "state": saved if sequence > after else None}
    @app.get("/api/sessions/{session_id}/traces/{run_id}")
    def run_trace(session_id: UUID, run_id: UUID):
        context = harness.context(session_id)
        records = harness.sessions.extension(context, "trace_runs", [])
        record = next((record for record in records if record["run_id"] == str(run_id)), None)
        if record is None: raise KeyError("未保留此run trace")
        return record
    @app.get("/api/sessions/{session_id}/artifacts/{artifact_id}")
    def artifact(session_id: UUID, artifact_id: UUID, start: int = 0, limit: int = 4000):
        return ArtifactStore(harness.sessions, harness.context(session_id)).read(artifact_id, start=start, limit=limit)
    @app.post("/api/sessions/{session_id}/notifications/{event_id}/read")
    def read_notification(session_id: UUID, event_id: UUID):
        Notifications(harness.sessions).mark_read(harness.context(session_id), event_id); return {"read": True}
    @app.post("/api/sessions/{session_id}/notifications/{event_id}/retry")
    def retry_notification(session_id: UUID, event_id: UUID):
        Notifications(harness.sessions).retry_desktop(harness.context(session_id), event_id); return {"queued": True}
    @app.get("/")
    def index(): return FileResponse(static / "index.html")
    app.mount("/static", StaticFiles(directory=static), name="static")
    return app
