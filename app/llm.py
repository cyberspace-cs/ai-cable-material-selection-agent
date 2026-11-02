"""
叙述层：把结构化结果转成工程人员可读的文字。

重要约束：LLM 只能复述 result 中的事实，不得引入新的数值或标准条款。
未配置密钥时走确定性模板（离线模式），保证评审者无密钥也能看到完整链路。
"""
from __future__ import annotations

import json
import os

SYSTEM_PROMPT = """你是电缆材料选型助手。你的唯一信息来源是用户提供的 JSON 结果。
规则：
1. 不得新增任何数值、标准号、条款或材料牌号；只能引用 JSON 中已有的内容。
2. 不得把"工程经验"表述为"标准规定"。
3. 用简洁的中文工程语言，先给结论，再说依据与边界。
4. 输出 JSON：{"headline": "...", "sections": [{"title": "...", "body": "..."}]}，3~4 个小节。"""


def _facts(result: dict) -> dict:
    """裁剪成叙述所需的精简事实，避免把整份结果塞进上下文。"""
    layers = []
    for L in result["layers"]:
        rec = L.get("recommended")
        layers.append(
            {
                "层": L["title"],
                "是否适用": L["required"],
                "推荐": None if not rec else {"材料": rec["name_cn"], "牌号": rec["grade"], "评分": rec["score"]},
                "理由": [] if not rec else rec["reasons"][:3],
                "代价": [] if not rec else rec["cautions"][:2],
                "不适用说明": L.get("skip_reason"),
            }
        )
    return {
        "输入型号": result["summary"]["input_model"],
        "建议型号": result["summary"]["recommended_model"],
        "置信度": result["summary"]["confidence"],
        "层": layers,
        "校核": [{"级别": c["level"], "标题": c["title"], "说明": c["detail"]} for c in result["checks"]],
        "待确认": result["questions"],
    }


def _offline(result: dict) -> dict:
    s = result["summary"]
    recs = [(L["title"], L["recommended"]) for L in result["layers"] if L["recommended"]]
    lead = "、".join(f"{t}采用{r['name_cn']}" for t, r in recs[:3])
    warns = [c for c in result["checks"] if c["level"] in ("warn", "error")]

    sections = []
    sections.append(
        {
            "title": "结论",
            "body": (
                f"输入 {s['input_model']}，在给定约束下建议采用 {s['recommended_model']}。"
                f"{lead}。共 {len(recs)} 个结构层给出推荐，{s['counts']['skipped']} 个结构层判定为不适用并说明了理由。"
                f"整体置信度 {s['confidence']}（{s['confidence_label']}）。"
            ),
        }
    )
    body2 = []
    for L in result["layers"]:
        if not L["recommended"]:
            continue
        r = L["recommended"]
        body2.append(f"{L['title']}：{r['name_cn']} —— {r['reasons'][0].lstrip('+·− ')}")
    sections.append({"title": "为什么这样选", "body": "\n".join(body2)})

    if warns:
        sections.append(
            {
                "title": "主要风险与未闭合项",
                "body": "\n".join(f"· {c['title']}" for c in warns[:4])
                + "\n这些项不能用材料级数据直接判定，需要成品试验或现场信息补全。",
            }
        )
    if result["questions"]:
        sections.append(
            {
                "title": "需要补充的信息",
                "body": "\n".join(f"· {q}" for q in result["questions"][:3]),
            }
        )

    return {
        "mode": "offline-template",
        "headline": f"{s['recommended_model']}｜{len(recs)} 层材料建议，置信度 {s['confidence']}",
        "sections": sections,
    }


def _call_llm(result: dict) -> dict:
    import requests

    base = os.environ.get("LLM_BASE_URL", "https://api.openai.com/v1").rstrip("/")
    key = os.environ["LLM_API_KEY"]
    model = os.environ.get("LLM_MODEL", "gpt-4o-mini")
    payload = {
        "model": model,
        "temperature": 0.2,
        "response_format": {"type": "json_object"},
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": json.dumps(_facts(result), ensure_ascii=False)},
        ],
    }
    resp = requests.post(
        f"{base}/chat/completions",
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        json=payload,
        timeout=25,
    )
    resp.raise_for_status()
    content = resp.json()["choices"][0]["message"]["content"]
    data = json.loads(content)
    data["mode"] = f"llm:{model}"
    return data


def narrate(result: dict) -> dict:
    if os.environ.get("LLM_API_KEY") and os.environ.get("LLM_ENABLE", "1") == "1":
        try:
            return _call_llm(result)
        except Exception as exc:  # 降级路径：任何异常都回到模板
            out = _offline(result)
            out["mode"] = "offline-template (fallback)"
            out["fallback_reason"] = f"{type(exc).__name__}: {exc}"[:160]
            return out
    return _offline(result)
