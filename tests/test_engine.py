"""引擎单元测试：约束优先级、覆盖完整性与引用完整性。"""
import json
from pathlib import Path

import pytest

from app.engine import parse_model, select
from app.models import SelectionRequest

ROOT = Path(__file__).resolve().parent.parent


def run(**kw):
    base = dict(model="YJV-0.6/1kV 4×35+1×16")
    base.update(kw)
    return select(SelectionRequest(**base))


def test_parse_model_basic():
    p = parse_model("WDZN-YJY23-0.6/1kV 4×35+1×16")
    assert p["lszh"] is True
    assert p["fire_resistant"] is True
    assert p["insulation"] == "YJ"
    assert p["sheath"] == "Y"
    assert p["armor_type"] == "STA"
    assert p["cores"] == 4 and p["section"] == 35
    assert p["neutral"] == (1, 16.0)


def test_parse_model_aluminium():
    p = parse_model("YJLV22-0.6/1kV 3×120+1×70")
    assert p["conductor"] == "AL"
    assert p["armor_type"] == "STA"
    assert p["kv"] == 1.0


def test_baseline_covers_six_layers():
    r = run()
    keys = [L["key"] for L in r["layers"]]
    assert keys == ["conductor", "insulation", "filler", "tape", "shield", "armor", "sheath"]
    # 低压桥架：屏蔽与铠装应判为不适用，但有明确理由
    skipped = [L for L in r["layers"] if not L["required"]]
    assert {L["key"] for L in skipped} == {"shield", "armor"}
    assert all(L["skip_reason"] for L in skipped)


def test_baseline_recommends_copper_and_lszh():
    r = run()
    rec = {L["key"]: L["recommended"]["id"] for L in r["layers"] if L["recommended"]}
    assert rec["conductor"] == "CON-CU"
    assert rec["insulation"] == "INS-LSZH"
    assert rec["sheath"] == "SH-LSZH"


def test_lszh_requirement_excludes_all_halogen_materials():
    r = run(lszh=True)
    for L in r["layers"]:
        if not L["required"]:
            continue
        assert L["recommended"]["id"] not in {"INS-PVC70", "SH-PVC-ST1", "SH-PVC-ST2", "FIL-PP"}


def test_model_conflict_is_reported_not_silently_fixed():
    r = run()
    ids = [c["id"] for c in r["checks"]]
    assert "C-MODEL-LSZH" in ids
    assert r["summary"]["recommended_model"].startswith("WDZ-YJY")


def test_single_core_mv_rejects_magnetic_armor():
    r = run(model="YJV22-26/35kV 1×300", buried=True, lszh=False, flame="ZC", ambient=35)
    errs = [c for c in r["checks"] if c["level"] == "error"]
    assert any(c["id"] == "C-SINGLE-ARMOR" for c in errs)
    armor = next(L for L in r["layers"] if L["key"] == "armor")
    assert armor["recommended"]["id"] == "ARM-AL-INTERLOCK"
    excluded = {c["id"] for c in armor["excluded"]}
    assert {"ARM-STA", "ARM-SWA"} <= excluded


def test_mv_requires_semicon_shield():
    r = run(model="YJV22-26/35kV 1×300", buried=True, lszh=False, flame="ZC", ambient=35)
    shield = next(L for L in r["layers"] if L["key"] == "shield")
    assert shield["required"] is True
    assert "屏蔽" in (shield["composite"] or "")
    assert shield["recommended"]["id"] in {"SHD-CU-TAPE", "SHD-CU-BRAID"}


def test_fire_resistant_adds_mica_composite():
    r = run(model="WDZN-YJY-0.6/1kV 4×25+1×16", fire_resistant=True, flame="ZB")
    ins = next(L for L in r["layers"] if L["key"] == "insulation")
    assert "云母带" in (ins["composite"] or "")
    assert any(c["id"] == "C-FIRE" for c in r["checks"])


def test_low_temperature_raises_warning():
    r = run(model="WDZ-YJY-0.6/1kV 3×70+1×35", ambient=-40, uv=True, buried=True)
    assert any(c["id"] == "C-LOWTEMP" for c in r["checks"])


def test_every_recommendation_has_citation():
    for sc in json.load(open(ROOT / "data" / "scenarios.json", encoding="utf-8"))["scenarios"]:
        r = select(SelectionRequest(**sc["payload"]))
        for L in r["layers"]:
            if L["recommended"]:
                assert L["citations"], f"{sc['id']} {L['key']} 缺少引用"
                assert all(c["code"] for c in L["citations"])


def test_all_material_sources_exist():
    m = json.load(open(ROOT / "data" / "materials.json", encoding="utf-8"))
    s = {x["id"] for x in json.load(open(ROOT / "data" / "standards.json", encoding="utf-8"))["standards"]}
    for mat in m["materials"]:
        assert mat["sources"], mat["id"]
        for sid in mat["sources"]:
            assert sid in s, f"{mat['id']} 引用不存在的来源 {sid}"


def test_dataset_size_requirement():
    m = json.load(open(ROOT / "data" / "materials.json", encoding="utf-8"))
    assert len(m["materials"]) >= 15
    cats = {x["category"] for x in m["materials"]}
    assert {"conductor", "insulation", "filler", "tape", "shield", "armor", "sheath"} <= cats


def test_deterministic_output():
    a = run()
    b = run()
    assert json.dumps(a["layers"], ensure_ascii=False, sort_keys=True) == json.dumps(
        b["layers"], ensure_ascii=False, sort_keys=True
    )


def test_confidence_in_range():
    for sc in json.load(open(ROOT / "data" / "scenarios.json", encoding="utf-8"))["scenarios"]:
        r = select(SelectionRequest(**sc["payload"]))
        assert 0.3 <= r["summary"]["confidence"] <= 0.95
