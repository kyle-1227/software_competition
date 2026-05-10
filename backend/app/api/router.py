from fastapi import APIRouter

from app.api.routes import health, manual, manuals, query


api_router = APIRouter()
api_router.include_router(health.router, tags=["健康检查"])
api_router.include_router(manual.router, prefix="/manual", tags=["手册管理"])
api_router.include_router(manuals.router, prefix="/manuals", tags=["手册管理"])
api_router.include_router(query.router, prefix="/query", tags=["故障查询"])
