from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core.config import settings
from app.db import postgres
from app.routers import candidates, catchment, contours, regions


@asynccontextmanager
async def lifespan(app: FastAPI):
    await postgres.init_schema()
    yield
    await postgres.close_pool()


app = FastAPI(title="Village Pond Planning System API", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/api/health")
async def health():
    return {"status": "ok"}


@app.get("/api/health/db")
async def health_db():
    ok = await postgres.ping()
    return {"status": "ok" if ok else "unreachable"}


app.include_router(regions.router)
app.include_router(contours.router)
app.include_router(candidates.router)
app.include_router(catchment.router)
