"""Pydantic 请求/响应模型。"""
from typing import Optional
from pydantic import BaseModel, Field


class SelectionRequest(BaseModel):
    model: str = Field("YJV-0.6/1kV 4×35+1×16", description="电缆型号，含电压与规格")
    installation: str = Field("tray", description="tray/duct/buried/outdoor/shaft/mobile")
    ambient: float = Field(40, description="环境温度 ℃")
    lszh: bool = Field(True, description="是否要求无卤低烟")
    flame: str = Field("ZC", description="阻燃等级：none/ZC/ZB/ZA/A")
    fire_resistant: bool = Field(False, description="是否要求耐火")
    fire_rating: Optional[str] = Field(None, description="GB 31247 等级：A/B1/B2/B3")
    oil: bool = False
    uv: bool = False
    rodent: bool = False
    flexible: bool = False
    bundle: bool = True
    bundle_count: Optional[int] = None
    buried: bool = False
    wet: bool = False
    corrosive: bool = False
    emc: bool = False
    notes: str = ""


class MaterialBrief(BaseModel):
    id: str
    name_cn: str
    grade: str
    score: int
    decision: str
    reasons: list[str] = []
    cautions: list[str] = []


class LayerResult(BaseModel):
    key: str
    title: str
    role: str
    required: bool
    skip_reason: Optional[str] = None
    recommended: Optional[MaterialBrief] = None
    composite: Optional[str] = None
    candidates: list[MaterialBrief] = []
    excluded: list[MaterialBrief] = []
    rationale: str = ""
    tradeoff: str = ""
    citations: list[dict] = []


class CheckItem(BaseModel):
    id: str
    level: str
    title: str
    detail: str
    refs: list[str] = []


class SelectionResult(BaseModel):
    input: dict
    parsed: dict
    summary: dict
    layers: list[LayerResult]
    checks: list[CheckItem]
    alternatives: list[dict]
    citations: list[dict]
    assumptions: list[str]
    questions: list[str]
    narrative: dict
