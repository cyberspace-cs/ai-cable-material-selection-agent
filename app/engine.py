"""
电缆材料选型引擎。

设计原则：
1. 先硬约束（合规/安全），再软约束（工程偏好）打分，两者分离，避免"分数高就合规"。
2. 每条结论必须携带引用（材料库 sources → 标准库）。
3. 引擎不生成自然语言事实，只输出结构化事实与理由片段，叙述层只能复述。
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

DATA_DIR = Path(__file__).resolve().parent.parent / "data"


def _load(name: str) -> dict:
    with open(DATA_DIR / name, encoding="utf-8") as f:
        return json.load(f)


MATERIALS: list[dict] = _load("materials.json")["materials"]
STANDARDS: dict[str, dict] = {s["id"]: s for s in _load("standards.json")["standards"]}
MAT = {m["id"]: m for m in MATERIALS}
BY_CAT: dict[str, list[dict]] = {}
for _m in MATERIALS:
    BY_CAT.setdefault(_m["category"], []).append(_m)

CATEGORY_META = {
    "conductor": ("导体", "承担载流与机械支撑，决定电阻、载流量与连接可靠性"),
    "insulation": ("绝缘", "承受电场，决定长期耐温等级与短路容量"),
    "filler": ("填充", "成缆圆整、阻水与阻燃，填充料是成束燃烧的主要燃料之一"),
    "tape": ("包带", "隔离、缓冲、阻水与阻燃隔离层"),
    "shield": ("屏蔽", "均化电场或提供电磁兼容，中压电缆为强制结构"),
    "armor": ("铠装", "抗机械外力、防鼠与抗拉，单芯交流回路的磁性铠装是禁忌"),
    "sheath": ("外护套", "抵御环境（水、油、UV、磨损），并承担阻燃低烟的第一道防线"),
}

INSTALL_LABEL = {
    "tray": "室内电缆桥架",
    "duct": "穿管敷设",
    "buried": "直埋敷设",
    "outdoor": "户外架空/明敷",
    "shaft": "竖井垂直敷设",
    "mobile": "移动敷设",
}

# 电气性能专家序数评分（0-1），引擎内维护，可替换为实测介电强度数据
ELEC = {
    "INS-XLPE": 1.00,
    "INS-LSZH": 0.93,
    "INS-EPR": 0.88,
    "INS-SIR": 0.80,
    "INS-PVC70": 0.62,
    "INS-MICA": 0.50,
}
CONNECT = {"CON-CU": 1.00, "CON-CU-TIN": 1.00, "CON-AL": 0.50, "CON-AL-ALLOY": 0.80}
WEIGHT = {"CON-CU": 0.40, "CON-CU-TIN": 0.40, "CON-AL": 1.00, "CON-AL-ALLOY": 1.00}

FLAME_ORDER = {"none": 0, "ZC": 1, "ZB": 2, "ZA": 3, "A": 4}


# ---------------------------------------------------------------- 型号解析


def parse_model(raw: str) -> dict:
    """解析 GB/T 12706 习惯的型号串，返回结构化属性与解析告警。"""
    text = (raw or "").strip().upper().replace(" ", "")
    warnings: list[str] = []
    rest = text

    # 前缀：无卤低烟 / 阻燃 / 耐火
    prefix = ""
    m = re.match(r"^(WDZAN|WDZBN|WDZCN|WDZA|WDZB|WDZC|WDZN|WDZ|ZRA|ZRB|ZRC|ZR|NH)", rest)
    if m:
        prefix = m.group(1)
        rest = rest[len(prefix):].lstrip("-")

    lszh = prefix.startswith("WDZ")
    fire = prefix == "NH" or prefix == "WDZN"
    flame = "none"
    if prefix in ("WDZA", "ZRA", "ZA"):
        flame = "ZA"
    elif prefix in ("WDZB", "ZRB", "ZB"):
        flame = "ZB"
    elif prefix.startswith("WDZ") or prefix.startswith("ZR"):
        flame = "ZC"
    if fire:
        flame = flame if flame != "none" else "ZC"

    # 主体：绝缘代号 + 导体代号(L) + 护套代号 + 铠装代号
    body = re.match(r"^(YJ|Y|V|E)(L?)(V|Y|F)?(\d{2})?", rest)
    insulation = sheath = conductor = armor = ""
    if body:
        insulation = body.group(1)
        conductor = "AL" if body.group(2) == "L" else "CU"
        sheath = body.group(3) or ""
        armor = body.group(4) or ""
    else:
        warnings.append(f"型号主体「{rest}」不符合 GB/T 12706 命名习惯，按未识别处理，仅使用显式输入条件。")

    # 电压
    u0 = u = 0.0
    vm = re.search(r"(\d+(?:\.\d+)?)\s*/\s*(\d+(?:\.\d+)?)\s*KV", text)
    if vm:
        u0, u = float(vm.group(1)), float(vm.group(2))
    else:
        warnings.append("未从型号中解析到额定电压（形如 0.6/1kV），按 0.6/1kV 处理。")
        u0, u = 0.6, 1.0

    # 芯数与截面
    cores, section, neutral = 0, 0.0, None
    cm = re.search(r"(\d+)\s*[×X*]\s*(\d+(?:\.\d+)?)", text)
    if cm:
        cores, section = int(cm.group(1)), float(cm.group(2))
        nm = re.search(r"\+\s*(\d+)\s*[×X*]\s*(\d+(?:\.\d+)?)", text)
        if nm:
            neutral = (int(nm.group(1)), float(nm.group(2)))
    else:
        warnings.append("未解析到芯数×截面，按 3×35 处理。")
        cores, section = 3, 35.0

    armor_map = {
        "22": ("STA", "钢带铠装 + PVC 外护套"),
        "23": ("STA", "钢带铠装 + PE 外护套"),
        "32": ("SWA", "细钢丝铠装 + PVC 外护套"),
        "33": ("SWA", "细钢丝铠装 + PE 外护套"),
        "42": ("SWA", "粗钢丝铠装 + PVC 外护套"),
        "43": ("SWA", "粗钢丝铠装 + PE 外护套"),
    }
    armor_type, armor_desc = armor_map.get(armor, ("", ""))
    if armor and not armor_type:
        warnings.append(f"铠装代号 {armor} 未识别，按无铠装处理。")

    return {
        "raw": raw,
        "prefix": prefix,
        "insulation": insulation,
        "conductor": conductor,
        "sheath": sheath,
        "armor": armor,
        "armor_type": armor_type,
        "armor_desc": armor_desc,
        "lszh": lszh,
        "fire_resistant": bool(fire),
        "flame": flame,
        "u0": u0,
        "u": u,
        "kv": u,
        "cores": cores,
        "section": section,
        "neutral": neutral,
        "warnings": warnings,
    }


def build_model_string(parsed: dict, need: "Needs", rec_ids: dict) -> str:
    """按最终推荐的材料反推型号，用于「型号升级建议」。"""
    ins = parsed["insulation"] or "YJ"
    cond = "L" if parsed["conductor"] == "AL" else ""
    sh_id = rec_ids.get("sheath")
    sh = "Y" if sh_id in ("SH-LSZH", "SH-PE") else "V"
    arm_id = rec_ids.get("armor")
    arm = ""
    if arm_id == "ARM-STA":
        arm = "23" if sh == "Y" else "22"
    elif arm_id == "ARM-SWA":
        arm = "33" if sh == "Y" else "32"

    if need.lszh and need.fire_resistant:
        head = "WDZN-"
    elif need.lszh:
        head = "WDZ-"
    elif need.fire_resistant:
        head = "NH-"
    elif need.flame != "none":
        head = {"ZA": "ZA-", "ZB": "ZB-", "ZC": "ZR-"}.get(need.flame, "ZR-")
    else:
        head = ""

    volt = f"{parsed['u0']:g}/{parsed['u']:g}kV"
    spec = f"{parsed['cores']}×{parsed['section']:g}"
    if parsed["neutral"]:
        spec += f"+{parsed['neutral'][0]}×{parsed['neutral'][1]:g}"
    return f"{head}{ins}{cond}{sh}{arm}-{volt} {spec}"


def _model_head(s: str) -> str:
    return re.split(r"[- ]", s.strip())[0].upper()


# ---------------------------------------------------------------- 需求推导


@dataclass
class Needs:
    kv: float
    mv: bool
    temp_class: int
    lszh: bool
    flame: str
    fire_resistant: bool
    fire_rating: Optional[str]
    shield: bool
    armor: bool
    single_core: bool
    ambient: float
    installation: str
    oil: bool
    uv: bool
    rodent: bool
    flexible: bool
    wet: bool
    corrosive: bool
    bundle: bool
    section: float
    cores: int


def derive_needs(req, parsed) -> Needs:
    kv = parsed["kv"]
    mv = kv >= 6
    flame = req.flame if req.flame in FLAME_ORDER else parsed["flame"]
    if parsed["fire_resistant"]:
        req_fire = True
    else:
        req_fire = req.fire_resistant

    temp_class = 90 if (mv or req.ambient >= 45 or req.lszh or req_fire) else 70
    shield = mv or req.emc
    armor = req.buried or req.rodent or req.installation in ("shaft", "buried") or parsed["armor_type"] != ""

    return Needs(
        kv=kv,
        mv=mv,
        temp_class=temp_class,
        lszh=req.lszh or parsed["lszh"],
        flame=flame,
        fire_resistant=req_fire,
        fire_rating=req.fire_rating,
        shield=shield,
        armor=armor,
        single_core=parsed["cores"] == 1,
        ambient=req.ambient,
        installation=req.installation,
        oil=req.oil,
        uv=req.uv,
        rodent=req.rodent,
        flexible=req.flexible,
        wet=req.wet or req.installation in ("buried", "duct"),
        corrosive=req.corrosive,
        bundle=req.bundle,
        section=parsed["section"],
        cores=parsed["cores"],
    )


# ---------------------------------------------------------------- 打分


def _score(criteria: list[tuple[str, float, float, str]]) -> tuple[int, list[str]]:
    rows = [c for c in criteria if c[1] > 0]
    if not rows:
        return 50, []
    tw = sum(c[1] for c in rows)
    raw = sum(c[1] * max(0.0, min(1.0, c[2])) for c in rows) / tw
    ranked = sorted(rows, key=lambda c: -c[1] * c[2])
    factors = []
    for label, w, v, note in ranked[:3]:
        mark = "+" if v >= 0.75 else ("−" if v < 0.5 else "·")
        q = "优" if v >= 0.85 else "良" if v >= 0.7 else "中" if v >= 0.5 else "弱"
        factors.append(f"{mark} {label}（{q}）：{note}")
    return round(raw * 100), factors


def _brief(m: dict, score: int, decision: str, reasons: list[str], cautions: list[str]) -> dict:
    return {
        "id": m["id"],
        "name_cn": m["name_cn"],
        "grade": m["grade"],
        "score": score,
        "decision": decision,
        "reasons": reasons,
        "cautions": cautions,
        "abstract": m["abstract"],
        "cost_index": m["cost_index"],
        "evidence": m["evidence"],
    }


def _citations(m: dict) -> list[dict]:
    out = []
    for sid in m["sources"]:
        s = STANDARDS.get(sid)
        if not s:
            continue
        out.append(
            {
                "code": s["code"],
                "title": s["title"],
                "level": s["precision"],
                "publisher": s["publisher"],
                "url": s["url"],
            }
        )
    return out


# ---------------------------------------------------------------- 各层选型


def _pick_conductor(m: dict, need: Needs) -> list[str]:
    fails = []
    if need.flexible and m["id"] == "CON-AL":
        fails.append("柔性/移动敷设要求下，纯铝导体反复弯折易断，工程上不采用")
    if need.corrosive and m["id"] == "CON-AL":
        fails.append("沿海或化学腐蚀环境下铝导体电化学腐蚀风险高，须铜铝过渡并密封")
    return fails


def _score_conductor(m: dict, need: Needs):
    a = m["attrs"]
    is_al = m["id"] in ("CON-AL", "CON-AL-ALLOY")
    # 工程惯例：小截面多芯电力电缆以铜为主，铝芯一般用于 50mm² 以上
    habit = 1.0 if not is_al else max(0.15, min(1.0, need.section / 120))
    return _score(
        [
            ("载流能力", 0.20, a["cond"] / 3, f"导电率序数 {a['cond']}/3"),
            ("连接可靠性", 0.22, CONNECT.get(m["id"], 0.7), "螺栓连接与铜铝过渡难度"),
            (
                "耐蚀性",
                0.16 if (need.corrosive or need.wet) else 0.06,
                a["water"] / 3,
                f"潮湿/腐蚀环境适应度 {a['water']}/3",
            ),
            ("成本", 0.18, 1 - (a["cost"] - 1) / 4, f"成本指数 {a['cost']}/5（越低越好）"),
            ("重量", 0.12, WEIGHT.get(m["id"], 0.4), "影响敷设牵引与支架荷载"),
            ("截面适配", 0.15, habit, f"{need.section:g}mm² 下铜/铝的工程适用度"),
            ("柔性", 0.08 if need.flexible else 0.0, a["flex"] / 3, f"柔性序数 {a['flex']}/3"),
        ]
    )


def _pick_insulation(m: dict, need: Needs) -> list[str]:
    fails = []
    mid = m["id"]
    if mid == "INS-MICA":
        return ["作为耐火层与主绝缘复合使用，不单独参与主绝缘比选"]
    if mid == "INS-PVC70":
        if need.mv:
            fails.append("6kV 及以上中压电缆绝缘须为 XLPE/EPR，PVC 介电强度与耐温不足")
        if need.lszh:
            fails.append("PVC 含卤，燃烧释放氯化氢，无法满足无卤低烟要求")
        if need.temp_class > 70:
            fails.append(f"需求耐温等级 {need.temp_class}℃，PVC 长期允许仅 70℃")
        if need.ambient <= -20:
            fails.append("PVC 低温脆化约 -15℃，不满足低温环境")
    if mid == "INS-SIR":
        if need.mv:
            fails.append("硅橡胶多用于低压柔性/高温场合，中压电力电缆不采用")
        if need.oil:
            fails.append("硅橡胶耐矿物油差，油污环境不适用")
        if need.lszh and need.flame in ("ZA", "A"):
            fails.append("硅橡胶阻燃等级有限，达不到 A 类成束阻燃结构要求")
    if mid == "INS-EPR" and need.oil:
        fails.append("乙丙橡胶耐矿物油差，油污环境不适用")
    return fails


def _score_insulation(m: dict, need: Needs):
    a = m["attrs"]
    flame_part = min(1.0, a["flame"] / 3 * 1.35)
    smoke_part = 1 - a["smoke"] / 3
    if need.lszh:
        fr = 0.55 * flame_part + 0.45 * smoke_part
        fnote = f"阻燃序数 {a['flame']}/3，烟密度序数 {a['smoke']}/3（越低越好）"
    else:
        fr = 0.75 * flame_part + 0.25 * smoke_part
        fnote = f"阻燃序数 {a['flame']}/3"
    return _score(
        [
            (
                "耐温等级",
                0.22,
                1.0 if a["temp_long"] >= need.temp_class else max(0.0, 1 - (need.temp_class - a["temp_long"]) / 30),
                f"长期 {a['temp_long']}℃ vs 需求 {need.temp_class}℃",
            ),
            ("电气性能", 0.20, ELEC.get(m["id"], 0.7), "介电强度与介质损耗综合序数"),
            (
                "阻燃/低烟",
                0.26 if need.lszh else (0.18 if need.flame != "none" else 0.06),
                fr,
                fnote,
            ),
            ("机械与工艺", 0.12, a["mech"] / 3, f"机械强度 {a['mech']}/3"),
            ("成本", 0.20 if need.lszh else 0.28, 1 - (a["cost"] - 1) / 4, f"成本指数 {a['cost']}/5（越低越好）"),
        ]
    )


def _pick_filler(m: dict, need: Needs) -> list[str]:
    mid = m["id"]
    fails = []
    if mid == "FIL-PP":
        if need.lszh:
            fails.append("PP 绳燃烧烟大且滴落，不满足低烟无卤结构要求")
        elif need.flame != "none":
            fails.append("PP 绳易燃且熔融滴落，阻燃电缆不应作为主填充")
        if need.fire_resistant:
            fails.append("耐火结构要求填充不燃，PP 绳不适用")
    if mid == "FIL-GLASS" and need.lszh and need.flame in ("ZA", "A"):
        fails.append("玻纤吸潮后影响绝缘电阻，A 类成束阻燃结构优先选低烟阻燃填充")
    return fails


def _score_filler(m: dict, need: Needs):
    a = m["attrs"]
    return _score(
        [
            ("阻燃贡献", 0.34, min(1.0, a["flame"] / 3 * 1.2), f"阻燃序数 {a['flame']}/3"),
            ("低烟", 0.22 if need.lszh else 0.03, 1 - a["smoke"] / 3, f"烟密度序数 {a['smoke']}/3"),
            ("吸潮/绝缘影响", 0.20, a["water"] / 3, f"防潮能力 {a['water']}/3"),
            ("成本", 0.22, 1 - (a["cost"] - 1) / 4, f"成本指数 {a['cost']}/5"),
            ("工艺柔性", 0.10, a["flex"] / 3, f"成缆填充柔顺性 {a['flex']}/3"),
        ]
    )


def _pick_tape(m: dict, need: Needs) -> list[str]:
    fails = []
    if m["id"] == "TAP-NONWOVEN" and need.lszh:
        fails.append("无纺布带可燃且燃烧产烟，与低烟无卤结构不匹配")
    if need.fire_resistant and m["id"] == "TAP-NONWOVEN":
        fails.append("耐火结构隔离层应选不燃包带")
    return fails


def _score_tape(m: dict, need: Needs):
    a = m["attrs"]
    return _score(
        [
            ("阻燃", 0.34, min(1.0, a["flame"] / 3 * 1.2), f"阻燃序数 {a['flame']}/3"),
            ("低烟", 0.20 if need.lszh else 0.03, 1 - a["smoke"] / 3, f"烟密度序数 {a['smoke']}/3"),
            ("阻水", 0.20 if need.wet else 0.05, a["water"] / 3, "直埋/穿管需径向阻水"),
            ("成本", 0.16, 1 - (a["cost"] - 1) / 4, f"成本指数 {a['cost']}/5"),
            ("工艺", 0.10, a["flex"] / 3, "绕包服帖性"),
        ]
    )


def _pick_shield(m: dict, need: Needs) -> list[str]:
    fails = []
    mid = m["id"]
    if mid == "SHD-SC":
        return ["半导电屏蔽为中压电缆强制结构层，作为复合结构计入，不参与金属屏蔽比选"]
    if need.mv and mid == "SHD-AL-PET":
        fails.append("铝塑复合带屏蔽电阻大，不能承受中压系统短路电流，不可替代金属屏蔽")
    if need.mv and mid == "SHD-CU-BRAID" and need.section >= 240:
        fails.append("大截面中压电缆短路容量要求高，编织层截面不足时需校核，本原型按不推荐处理")
    return fails


def _score_shield(m: dict, need: Needs):
    a = m["attrs"]
    # 短路容量：铜带 > 编织 > 铝塑复合带（后者不能承担短路电流）
    sc = {"SHD-CU-TAPE": 1.0, "SHD-CU-BRAID": 0.8, "SHD-AL-PET": 0.15}.get(m["id"], 0.5)
    return _score(
        [
            ("屏蔽效能", 0.34, a["shield"] / 3, f"屏蔽序数 {a['shield']}/3"),
            ("短路容量", 0.22 if need.mv else 0.05, sc, "金属屏蔽层的短路热稳定能力"),
            ("柔性", 0.18 if need.flexible else 0.08, a["flex"] / 3, f"弯曲适应性 {a['flex']}/3"),
            ("成本", 0.24, 1 - (a["cost"] - 1) / 4, f"成本指数 {a['cost']}/5"),
        ]
    )


def _pick_armor(m: dict, need: Needs) -> list[str]:
    fails = []
    mid = m["id"]
    if need.single_core and mid in ("ARM-STA", "ARM-SWA"):
        fails.append("单芯交流回路采用磁性铠装会产生涡流损耗与护套感应电压，GB 50217 明确不应采用")
    if mid == "ARM-SWA" and need.installation not in ("shaft", "buried", "water") and not need.rodent:
        fails.append("钢丝铠装用于承受轴向拉力，本场景无抗拉需求，选用会显著增加重量与成本")
    return fails


def _score_armor(m: dict, need: Needs):
    a = m["attrs"]
    corr = {"ARM-STA": 0.55, "ARM-SWA": 0.50, "ARM-AL-INTERLOCK": 1.0}.get(m["id"], 0.6)
    return _score(
        [
            ("抗侧压", 0.30, a["mech"] / 3, f"抗机械外力 {a['mech']}/3"),
            ("抗拉", 0.22 if need.installation in ("shaft", "water") else 0.06, a["tensile"] / 3, f"抗拉序数 {a['tensile']}/3"),
            ("防鼠", 0.14 if need.rodent else 0.05, a["rodent"] / 3, f"防鼠序数 {a['rodent']}/3"),
            ("耐蚀", 0.26 if need.corrosive else 0.04, corr, "沿海/腐蚀环境下镀锌层的耐久性"),
            ("弯曲适应性", 0.14 if need.flexible else 0.06, a["flex"] / 3, f"柔性 {a['flex']}/3"),
            ("成本", 0.20, 1 - (a["cost"] - 1) / 4, f"成本指数 {a['cost']}/5"),
            ("重量", 0.08, 1 - (a["mech"] - 1) / 3 * 0.5, "影响敷设与支架荷载"),
        ]
    )


def _pick_sheath(m: dict, need: Needs) -> list[str]:
    fails = []
    mid = m["id"]
    if mid in ("SH-PVC-ST1", "SH-PVC-ST2"):
        if need.lszh:
            fails.append("PVC 含卤，燃烧释放氯化氢且烟密度高，不满足低烟无卤要求")
        if need.ambient <= -25:
            fails.append("PVC 低温脆化约 -15℃，不满足低温环境")
        if need.uv and need.installation == "outdoor":
            fails.append("PVC 耐紫外老化差，户外长期明敷易开裂")
    if mid == "SH-PE":
        if need.lszh:
            fails.append("PE 虽无卤但易燃、燃烧烟浓，不能作为低烟无卤护套")
        if need.oil:
            fails.append("PE 耐矿物油差，接触油品会溶胀")
        if need.flame in ("ZA", "ZB", "A"):
            fails.append("PE 本身易燃，达不到高等级成束阻燃要求")
    if mid in ("SH-TPU", "SH-PA") and need.lszh:
        fails.append("TPU/尼龙可用作附加外护层，但非无卤低烟定型牌号，不能单独作为 WDZ 护套宣称")
    return fails


def _score_sheath(m: dict, need: Needs):
    a = m["attrs"]
    return _score(
        [
            ("阻燃", 0.24 if need.flame != "none" else 0.08, min(1.0, a["flame"] / 3 * 1.2), f"阻燃序数 {a['flame']}/3"),
            ("低烟无卤", 0.22 if need.lszh else 0.04, 1 - a["smoke"] / 3 if a["halogen"] == 0 else 0.05, "含卤材料该维度归零"),
            ("耐温", 0.10, min(1.0, a["temp_long"] / 90), f"长期 {a['temp_long']}℃"),
            ("低温", 0.14 if need.ambient < 0 else 0.04, min(1.0, max(0.0, (a["temp_low"] + 50) / 50)), f"低温下限 {a['temp_low']}℃"),
            ("耐油", 0.16 if need.oil else 0.04, a["oil"] / 3, f"耐油序数 {a['oil']}/3"),
            ("耐UV", 0.12 if need.uv else 0.03, a["uv"] / 3, f"耐候序数 {a['uv']}/3"),
            ("耐磨/防鼠", 0.12 if need.rodent else 0.04, max(a["abrasion"], a["rodent"]) / 3, "磨损与啮齿防护"),
            ("成本", 0.18, 1 - (a["cost"] - 1) / 4, f"成本指数 {a['cost']}/5"),
        ]
    )


LAYER_SPECS = [
    ("conductor", "conductor", _pick_conductor, _score_conductor, True),
    ("insulation", "insulation", _pick_insulation, _score_insulation, True),
    ("filler", "filler", _pick_filler, _score_filler, True),
    ("tape", "tape", _pick_tape, _score_tape, True),
    ("shield", "shield", _pick_shield, _score_shield, False),
    ("armor", "armor", _pick_armor, _score_armor, False),
    ("sheath", "sheath", _pick_sheath, _score_sheath, True),
]


def _build_layer(key: str, cat: str, picker, scorer, need: Needs) -> dict:
    title, role = CATEGORY_META[cat]
    included, excluded = [], []
    for m in BY_CAT.get(cat, []):
        fails = picker(m, need)
        if fails:
            excluded.append(_brief(m, 0, "排除", fails[:2], []))
            continue
        s, factors = scorer(m, need)
        cautions = m["limitations"][:2]
        included.append(_brief(m, s, "候选", factors, cautions))

    included.sort(key=lambda x: -x["score"])

    required = True
    skip_reason = None
    if key == "shield" and not need.shield:
        required = False
        skip_reason = (
            "低压多芯电缆无电磁兼容要求，按 GB/T 12706.1 不要求金属屏蔽；"
            "若供电对象为变频器或存在弱电干扰，应补充铜丝编织屏蔽。"
        )
    if key == "armor" and not need.armor:
        required = False
        skip_reason = (
            f"{INSTALL_LABEL.get(need.installation, need.installation)}且无防鼠/抗拉需求，"
            "按 GB 50217 可不设铠装；若后期改为直埋或鼠害区域，需追加钢带铠装。"
        )

    composite = None
    if key == "insulation" and need.fire_resistant:
        composite = "云母带（2 层，重叠率 50%）+ 主绝缘"
        for b in included:
            b["reasons"].insert(0, "+ 复合云母带耐火层后满足 750℃/90min 线路完整性")
    if key == "shield" and need.mv:
        composite = "导体屏蔽 + 绝缘屏蔽（三层共挤）"
        for b in included:
            b["reasons"].insert(0, "+ 中压结构强制半导电屏蔽，与金属屏蔽复合")

    rec = included[0] if included else None
    if rec:
        rec["decision"] = "推荐"
        for b in included[1:4]:
            b["decision"] = "备选"
    for e in excluded:
        e["decision"] = "排除"

    if not required:
        # 不适用层：保留候选供"若条件变化"时参考，但不给出推荐
        for b in included:
            b["decision"] = "可选"
        rec = None
        composite = None

    cites = []
    if rec:
        cites = _citations(MAT[rec["id"]])

    return {
        "key": key,
        "title": title,
        "role": role,
        "required": required and bool(included),
        "skip_reason": skip_reason,
        "recommended": rec,
        "composite": composite,
        "candidates": included[:4],
        "excluded": excluded,
        "citations": cites,
        "_all_included": included,
    }


def _rationale(layer: dict, need: Needs) -> str:
    rec = layer.get("recommended")
    if not rec:
        return layer.get("skip_reason") or "本层在当前条件下无适用材料。"
    m = MAT[rec["id"]]
    first = rec["reasons"][0].lstrip("+·− ") if rec["reasons"] else ""
    lead = {
        "conductor": f"在 {need.section:g} mm²、{need.cores} 芯、{need.kv:g}kV 条件下，",
        "insulation": f"需求耐温等级 {need.temp_class}℃、{'中压' if need.mv else '低压'}结构，",
        "filler": "按成缆圆整与阻燃结构要求，",
        "tape": "按隔离、阻水与阻燃匹配要求，",
        "shield": "按屏蔽功能要求，",
        "armor": "按机械保护与敷设方式要求，",
        "sheath": "按环境防护与燃烧性能要求，",
    }.get(layer["key"], "")
    return f"{lead}推荐 {rec['name_cn']}（{rec['grade']}）。主要依据：{first}。"


def _tradeoff(layer: dict) -> str:
    rec = layer.get("recommended")
    cands = [c for c in layer["candidates"] if c["decision"] == "备选"]
    if not rec:
        return ""
    limits = "；".join(MAT[rec["id"]]["limitations"][:2])
    alt = ""
    if cands:
        alt = f" 备选 {cands[0]['name_cn']} 可在此项上权衡：{cands[0]['reasons'][0].lstrip('+·− ')}。"
    return f"代价与边界：{limits}。{alt}".strip()


# ---------------------------------------------------------------- 耦合检查


def _checks(req, parsed, need: Needs, layers: dict) -> list[dict]:
    out: list[dict] = []
    rec = {k: (v.get("recommended") or {}).get("id") for k, v in layers.items()}

    # 型号与需求冲突
    if need.lszh and parsed["sheath"] == "V":
        out.append(
            {
                "id": "C-MODEL-LSZH",
                "level": "warn",
                "title": f"输入型号 {parsed['raw'].split('-')[0]} 与「低烟无卤」要求冲突",
                "detail": (
                    "型号中的 V 表示聚氯乙烯护套，含卤且烟密度高，与低烟无卤要求直接矛盾。"
                    "本方案已按需求给出无卤低烟结构，型号应同步升级为 WDZ-YJY 系列；"
                    "若坚持原型号，须在文件中明确放弃低烟要求。"
                ),
                "refs": ["STD-17650", "STD-17651"],
            }
        )
    if need.fire_resistant and not parsed["fire_resistant"]:
        out.append(
            {
                "id": "C-MODEL-NH",
                "level": "warn",
                "title": "输入型号未包含耐火代号，但需求为耐火",
                "detail": "型号应向 NH- 或 WDZN- 前缀升级，并在规格书中注明耐火试验条件（750℃/90min 或更高）。",
                "refs": ["STD-19216", "STD-19666"],
            }
        )
    if need.mv and parsed["armor_type"] == "STA" and need.single_core:
        out.append(
            {
                "id": "C-SINGLE-ARMOR",
                "level": "error",
                "title": "单芯交流回路 + 钢带铠装：结构不成立",
                "detail": (
                    "单芯电缆的交流磁场会在钢带中产生涡流，造成附加损耗与局部过热，"
                    "GB 50217 规定单芯电缆不应采用磁性铠装。应改为非磁性铠装（铝合金连锁）或不铠装。"
                ),
                "refs": ["STD-50217"],
            }
        )
    if need.single_core and req.installation in ("tray", "duct") and parsed["armor_type"]:
        out.append(
            {
                "id": "C-SINGLE-ARMOR-2",
                "level": "warn",
                "title": "单芯电缆按型号带铠装，需确认是否为磁性材料",
                "detail": "桥架/穿管敷设的单芯电缆通常不需要铠装；若必须铠装，应选铝合金连锁等非磁性结构。",
                "refs": ["STD-50217"],
            }
        )

    # 成束与阻燃等级
    if need.bundle and need.flame in ("ZC", "ZB"):
        cnt = f"（已知 {req.bundle_count} 根）" if req.bundle_count else "（根数未知）"
        out.append(
            {
                "id": "C-BUNDLE",
                "level": "warn",
                "title": f"成束敷设{cnt}，{need.flame} 类是否成立需按成束试验校核",
                "detail": (
                    "阻燃等级由 GB/T 18380.3 系列成束燃烧试验判定，与电缆根数、排列方式、桥架是否封闭强相关。"
                    "单根垂直燃烧试验（GB/T 18380.1）不能替代。根数未知时，本结论只能作为选型建议，不能作为合规声明。"
                ),
                "refs": ["STD-18380-3", "STD-19666"],
            }
        )
    if need.fire_rating in ("A", "B1"):
        out.append(
            {
                "id": "C-31247",
                "level": "warn",
                "title": f"GB 31247 {need.fire_rating} 级需整体送检，不能由材料指标推定",
                "detail": (
                    "B1 级综合考核热释放、烟密度、燃烧滴落与腐蚀性，属成品级结论。"
                    "材料库中的氧指数只能用于配方筛选，不能作为分级依据。"
                ),
                "refs": ["STD-31247"],
            }
        )

    # 耐火结构
    if need.fire_resistant:
        out.append(
            {
                "id": "C-FIRE",
                "level": "info",
                "title": "已按耐火结构加入云母带，需确认试验条件",
                "detail": (
                    "本方案按 750℃/90min 线路完整性（GB/T 19216.21）配置。"
                    "消防泵、排烟风机等回路若按地方要求需 950℃ 或带冲击喷水条件，云母带层数与结构需重新设计。"
                ),
                "refs": ["STD-19216", "STD-19666"],
            }
        )

    # 低温
    if need.ambient <= -30 and rec.get("sheath") == "SH-LSZH":
        out.append(
            {
                "id": "C-LOWTEMP",
                "level": "warn",
                "title": "环境温度低于常规 LSZH 护套的低温下限",
                "detail": (
                    f"环境 {need.ambient:g}℃，而库内 LSZH 护套常规牌号低温脆化约 -25℃。"
                    "当前排序未区分耐寒牌号，结论存在过度自信风险：必须向供应商确认 -40℃ 耐寒料，"
                    "或改用 TPU / 耐寒 PE + 阻燃结构。"
                ),
                "refs": ["STD-32129", "STD-2951"],
            }
        )

    # 敷设
    if req.buried and not rec.get("armor"):
        out.append(
            {
                "id": "C-BURIED",
                "level": "warn",
                "title": "直埋敷设但未配置铠装",
                "detail": "直埋电缆承受回填土石侧压与后期挖掘风险，GB 50217 建议设置铠装或采取等同的机械保护措施。",
                "refs": ["STD-50217"],
            }
        )
    if need.wet and "TAP-WATERBLOCK" not in [c["id"] for c in layers["tape"]["candidates"]]:
        out.append(
            {
                "id": "C-WATER",
                "level": "warn",
                "title": "潮湿/直埋场景未进入阻水结构",
                "detail": "建议在成缆时增加阻水带或阻水纱，形成径向阻水，否则水分沿轴向渗透会显著缩短寿命。",
                "refs": ["STD-50217"],
            }
        )
    if need.flexible and rec.get("armor") in ("ARM-STA", "ARM-SWA"):
        out.append(
            {
                "id": "C-FLEX-ARMOR",
                "level": "warn",
                "title": "柔性要求与钢质铠装冲突",
                "detail": "钢带/钢丝铠装会显著提高最小弯曲半径，移动敷设场合应改为编织加强或非金属抗拉元件。",
                "refs": ["STD-50217"],
            }
        )
    if need.oil and rec.get("sheath") == "SH-LSZH":
        out.append(
            {
                "id": "C-OIL",
                "level": "info",
                "title": "LSZH 护套耐油性一般",
                "detail": "长期接触矿物油时建议增加尼龙内衬或改用 TPU 护套，并在规格书中给出耐油试验条件。",
                "refs": ["DOC-TEXTBOOK"],
            }
        )
    if need.uv and rec.get("sheath") == "SH-LSZH":
        out.append(
            {
                "id": "C-UV",
                "level": "warn",
                "title": "LSZH 护套耐紫外等级一般，户外需耐候配方或遮阳",
                "detail": (
                    "常规无卤低烟护套的耐候序数为 1/3，长期紫外直射会出现粉化与开裂。"
                    "户外明敷时应要求供应商提供含炭黑或受阻胺光稳定剂的耐候牌号，"
                    "或改为带遮阳的桥架/穿管敷设。"
                ),
                "refs": ["STD-32129", "STD-2951"],
            }
        )

    # 恒定的能力边界声明
    out.append(
        {
            "id": "C-SCOPE-1",
            "level": "info",
            "title": "未做载流量与短路热稳定计算",
            "detail": (
                "本原型只输出材料与结构建议，不计算载流量、电压降与短路热稳定。"
                "截面选择需按 GB 50217 并结合敷设修正系数单独校核。"
            ),
            "refs": ["STD-50217"],
        }
    )
    out.append(
        {
            "id": "C-SCOPE-2",
            "level": "info",
            "title": "材料级指标不等于成品级结论",
            "detail": (
                "阻燃等级、烟密度、卤酸气体均为成品试验结论。本原型由材料属性推断结构可行性，"
                "最终必须由成品送检报告确认。"
            ),
            "refs": ["STD-19666", "STD-31247"],
        }
    )
    return out


# ---------------------------------------------------------------- 置信度


def _confidence(req, need: Needs, checks: list[dict]) -> float:
    c = 0.90
    c -= 0.12 * sum(1 for x in checks if x["level"] == "error")
    c -= 0.045 * sum(1 for x in checks if x["level"] == "warn")
    if need.bundle and not req.bundle_count:
        c -= 0.05
    if need.lszh and not need.fire_rating:
        c -= 0.04
    if need.mv:
        c -= 0.03
    return round(max(0.30, min(0.95, c)), 2)


def _questions(req, need: Needs) -> list[str]:
    q = []
    if need.bundle and not req.bundle_count:
        q.append("成束敷设的电缆根数、排列层数与桥架是否封闭？这直接决定阻燃 A/B/C/D 类能否成立。")
    if need.lszh and not need.fire_rating:
        q.append("项目是否引用 GB 31247 燃烧性能等级（B1/B2）及附加信息 d0/t0/a1？")
    if need.mv:
        q.append("系统中性点接地方式与单相接地故障持续时间？这决定金属屏蔽截面与铠装选型。")
    if req.ambient >= 40:
        q.append("现场极端最高温度及持续时间？是否需要按 45℃ 以上修正载流量？")
    if req.installation == "tray":
        q.append("桥架材质与是否封闭？封闭桥架散热差，载流量需折减。")
    if need.corrosive:
        q.append("腐蚀介质类型与浓度（盐雾/硫化氢/酸碱）？决定镀层与护套牌号。")
    return q[:4]


ASSUMPTIONS = [
    "型号解析基于 GB/T 12706 命名习惯；非标或厂商自定义型号可能解析失败，此时仅使用显式输入条件。",
    "耐温等级按「中压 / 环境≥45℃ / 无卤低烟 / 耐火」判定为 90℃，其余按 70℃ 处理。",
    "材料属性中的 0-3 序数为工程评分，用于同层候选排序，不代表实测性能，也不可跨层比较。",
    "未计算载流量、电压降与短路热稳定，截面与并联根数需另行校核。",
    "阻燃、烟密度、卤酸气体为成品级试验结论，本原型仅做结构可行性推断。",
]


# ---------------------------------------------------------------- 主流程


def _build_core(req, parsed) -> dict:
    need = derive_needs(req, parsed)
    layers = {}
    for key, cat, picker, scorer, _req in LAYER_SPECS:
        layers[key] = _build_layer(key, cat, picker, scorer, need)

    for k, v in layers.items():
        v["rationale"] = _rationale(v, need)
        v["tradeoff"] = _tradeoff(v)
        v.pop("_all_included", None)

    checks = _checks(req, parsed, need, layers)
    return {"need": need, "layers": layers, "checks": checks}


def _alternatives(req, parsed, core, primary_names: dict) -> list[dict]:
    alts = []
    need: Needs = core["need"]

    if req.lszh and parsed["sheath"] == "V":
        alt_req = req.model_copy(update={"lszh": False})
        alt_core = _build_core(alt_req, parsed)
        changes = []
        for k, v in alt_core["layers"].items():
            r = (v.get("recommended") or {}).get("id")
            if r and primary_names.get(k) and r != primary_names[k]:
                changes.append(
                    {
                        "layer": v["title"],
                        "from": MAT[primary_names[k]]["name_cn"],
                        "to": MAT[r]["name_cn"],
                    }
                )
        if changes:
            alts.append(
                {
                    "name": "保持输入型号（最小改动）",
                    "tag": "成本优先",
                    "note": "沿用 YJV 的 PVC 护套与普通填充，不改型号、不改工艺。",
                    "changes": changes,
                    "impact": "不满足低烟无卤：燃烧释放氯化氢，烟密度与腐蚀性气体超标，人员密集与地下场所不可接受。",
                }
            )

    if not req.fire_resistant:
        alt_req = req.model_copy(update={"fire_resistant": True})
        alt_core = _build_core(alt_req, parsed)
        changes = []
        for k, v in alt_core["layers"].items():
            r = (v.get("recommended") or {}).get("id")
            if r and primary_names.get(k) and r != primary_names[k]:
                changes.append(
                    {
                        "layer": v["title"],
                        "from": MAT[primary_names[k]]["name_cn"],
                        "to": MAT[r]["name_cn"],
                    }
                )
        if "云母带" not in str(changes):
            changes.append(
                {
                    "layer": "绝缘",
                    "from": "无耐火层",
                    "to": "云母带（2 层）+ 主绝缘复合",
                }
            )
        alts.append(
            {
                "name": "加严方案（耐火升级）",
                "tag": "消防回路",
                "note": "增加云母带耐火层，按 750℃/90min 线路完整性配置。",
                "changes": changes,
                "impact": f"外径增加约 1~2 mm，成本上升约 15%~25%，载流量略降（{need.kv:g}kV 结构）。",
            }
        )
    return alts


def select(req) -> dict:
    parsed = parse_model(req.model)
    core = _build_core(req, parsed)
    layers = core["layers"]
    checks = core["checks"]
    need: Needs = core["need"]

    primary_names = {k: (v.get("recommended") or {}).get("id") for k, v in layers.items() if v["recommended"]}
    alts = _alternatives(req, parsed, core, primary_names)

    # 汇总引用
    cites: dict[str, dict] = {}
    for k, v in layers.items():
        if not v["recommended"]:
            continue
        for c in v["citations"]:
            key = c["code"]
            if key not in cites:
                cites[key] = {**c, "used_by": []}
            if v["title"] not in cites[key]["used_by"]:
                cites[key]["used_by"].append(v["title"])
    for x in checks:
        for sid in x["refs"]:
            s = STANDARDS.get(sid)
            if not s:
                continue
            if s["code"] not in cites:
                cites[s["code"]] = {
                    "code": s["code"],
                    "title": s["title"],
                    "level": s["precision"],
                    "publisher": s["publisher"],
                    "url": s["url"],
                    "used_by": ["结构校核"],
                }

    lv_count = {"clause": 0, "standard": 0, "document": 0}
    for c in cites.values():
        lv_count[c["level"]] = lv_count.get(c["level"], 0) + 1

    conf = _confidence(req, need, checks)
    rec_ids = {k: (v.get("recommended") or {}).get("id") for k, v in layers.items() if v["recommended"]}
    rec_model = build_model_string(parsed, need, rec_ids)
    armor_note = ""
    if rec_ids.get("armor") == "ARM-AL-INTERLOCK":
        armor_note = "铝合金连锁铠装无 GB/T 12706 标准代号，型号串按无铠装表示，需在规格书中单独注明。"

    summary = {
        "confidence": conf,
        "confidence_label": "较高" if conf >= 0.8 else ("中等" if conf >= 0.6 else "偏低"),
        "recommended_model": rec_model,
        "model_changed": rec_model.replace(" ", "") != parsed["raw"].replace(" ", ""),
        "armor_note": armor_note,
        "input_model": parsed["raw"],
        "temp_class": need.temp_class,
        "structure": "中压三层共挤" if need.mv else "低压挤包绝缘",
        "evidence_mix": lv_count,
        "counts": {
            "layers": sum(1 for v in layers.values() if v["required"]),
            "skipped": sum(1 for v in layers.values() if not v["required"]),
            "candidates": sum(len(v["candidates"]) for v in layers.values() if v["required"]),
            "excluded": sum(len(v["excluded"]) for v in layers.values()),
            "checks_error": sum(1 for c in checks if c["level"] == "error"),
            "checks_warn": sum(1 for c in checks if c["level"] == "warn"),
        },
    }

    return {
        "input": req.model_dump(),
        "parsed": parsed,
        "summary": summary,
        "layers": list(layers.values()),
        "checks": checks,
        "alternatives": alts,
        "citations": list(cites.values()),
        "assumptions": ASSUMPTIONS,
        "questions": _questions(req, need),
    }
