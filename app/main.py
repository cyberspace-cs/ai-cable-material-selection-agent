"""FastAPI 入口：静态页面 + 选型 API。"""
from __future__ import annotations

import json
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .engine import MATERIALS, STANDARDS, select
from .llm import narrate
from .models import SelectionRequest

ROOT = Path(__file__).resolve().parent.parent
WEB = ROOT / "web"
DATA = ROOT / "data"

app = FastAPI(title="CableMind｜AI 电缆材料选型智能体", version="1.0.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


def _read(name: str) -> dict:
    with open(DATA / name, encoding="utf-8") as f:
        return json.load(f)


@app.get("/api/health")
def health():
    return {"status": "ok", "materials": len(MATERIALS), "standards": len(STANDARDS)}


@app.get("/api/scenarios")
def scenarios():
    return _read("scenarios.json")


@app.get("/api/materials")
def materials():
    return _read("materials.json")


@app.get("/api/standards")
def standards():
    return _read("standards.json")


@app.get("/api/validation")
def validation():
    return _read("validation.json")


@app.post("/api/select")
def do_select(req: SelectionRequest):
    if not req.model.strip():
        raise HTTPException(status_code=400, detail="型号不能为空")
    result = select(req)
    result["narrative"] = narrate(result)
    return result


@app.get("/")
def index():
    return FileResponse(WEB / "index.html")


app.mount("/static", StaticFiles(directory=WEB), name="static")
