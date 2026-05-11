from uuid import uuid4

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.encoders import jsonable_encoder

from app.api.router import api_router
from app.dependencies import _build_services, _attach_services
from app.dependencies import lifespan
from app.core.config import settings
from app.schemas.response import error_response, success_response


app = FastAPI(title=settings.app_name, lifespan=lifespan)
_attach_services(app, _build_services())

# 前端开发服务运行在 8001 端口。CORS 统一放在应用入口配置，
# 路由模块只负责处理具体请求。
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(api_router, prefix=settings.api_prefix)


def _get_trace_id(request: Request) -> str:
    return getattr(request.state, "trace_id", str(uuid4()))


@app.middleware("http")
async def add_trace_id(request: Request, call_next):
    # 优先沿用调用方传入的 trace id，便于前端日志、后端日志和测试断言
    # 对齐到同一次请求。
    trace_id = request.headers.get("X-Request-ID") or str(uuid4())
    request.state.trace_id = trace_id
    response = await call_next(request)
    response.headers["X-Request-ID"] = trace_id
    return response


@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException) -> JSONResponse:
    # 将 FastAPI 的 HTTP 异常转换成和成功响应一致的 envelope 结构。
    trace_id = _get_trace_id(request)
    code = "NOT_FOUND" if exc.status_code == 404 else "HTTP_ERROR"
    payload = error_response(
        code=code,
        message=str(exc.detail),
        details={"detail": exc.detail},
        trace_id=trace_id,
    )
    return JSONResponse(
        status_code=exc.status_code,
        content=payload.model_dump(mode="json"),
        headers={"X-Request-ID": trace_id},
    )


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    # 校验错误也使用统一错误结构，避免前端单独适配 FastAPI 默认错误格式。
    trace_id = _get_trace_id(request)
    payload = error_response(
        code="VALIDATION_ERROR",
        message="请求参数校验失败",
        details={"errors": jsonable_encoder(exc.errors())},
        trace_id=trace_id,
    )
    return JSONResponse(
        status_code=422,
        content=payload.model_dump(mode="json"),
        headers={"X-Request-ID": trace_id},
    )


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    # 默认隐藏原始异常细节；本地排查问题时可通过 DEBUG=true 打开。
    trace_id = _get_trace_id(request)
    details = {"error": str(exc)} if settings.debug else None
    payload = error_response(
        code="INTERNAL_SERVER_ERROR",
        message="服务内部异常",
        details=details,
        trace_id=trace_id,
    )
    return JSONResponse(
        status_code=500,
        content=payload.model_dump(mode="json"),
        headers={"X-Request-ID": trace_id},
    )


@app.get("/", tags=["系统"])
async def root(request: Request):
    return success_response(
        data={"app_name": settings.app_name, "app_env": settings.app_env},
        trace_id=_get_trace_id(request),
    )
