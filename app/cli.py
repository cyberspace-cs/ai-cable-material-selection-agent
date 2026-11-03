"""命令行入口：不启 Web 也能完成一次完整选型。

用法：
    python -m app.cli                                  # 基准场景
    python -m app.cli --scenario S2                     # 场景预设
    python -m app.cli --model "YJV22-8.7/15kV 3×240" --installation buried --wet --corrosive
    python -m app.cli --list                            # 列出场景预设
    python -m app.cli --json                            # 输出机器可读 JSON

设计约束：所有结论都来自 app.engine.select()，CLI 只做呈现，不产生新的判断逻辑。
"""
from __future__ import annotations

import argparse
import json
import sys
import unicodedata
from pathlib import Path

from rich.cells import cell_len
from rich.console import Console, Group
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from app.engine import select
from app.models import SelectionRequest

ROOT = Path(__file__).resolve().parent.parent
SCENARIOS = ROOT / "data" / "scenarios.json"

LEVEL_STYLE = {"error": ("bold red", "✕ 阻断"), "warn": ("yellow", "▲ 风险"), "info": ("cyan", "· 提示")}


def _cw(ch: str) -> int:
    return 2 if unicodedata.east_asian_width(ch) in "WF" else 1


def clip(s: str, width: int) -> str:
    """按显示宽度截断（中文计 2 列），保证表格单元格不换行。"""
    out, w = "", 0
    for ch in s:
        c = _cw(ch)
        if w + c > width - 1:
            return out + "…"
        out += ch
        w += c
    return out


def wrap_cjk(s: str, width: int) -> list[str]:
    """按终端显示宽度折行（中文按 2 列计），避免 rich 二次折行丢失缩进。"""
    lines, cur, w = [], "", 0
    for ch in s:
        c = _cw(ch)
        if w + c > width and cur:
            lines.append(cur)
            cur, w = ch, c
        else:
            cur += ch
            w += c
    if cur:
        lines.append(cur)
    return lines


def load_scenarios() -> list[dict]:
    if not SCENARIOS.exists():
        return []
    return json.loads(SCENARIOS.read_text(encoding="utf-8")).get("scenarios", [])


def build_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="cablemind", description="电缆材料选型智能体（CLI）")
    p.add_argument("--scenario", help="场景预设 ID，如 S1/S2/S3/S4")
    p.add_argument("--model", help="电缆型号，含电压与规格")
    p.add_argument("--installation", default=None, help="tray/duct/buried/outdoor/shaft/mobile")
    p.add_argument("--ambient", type=float, default=None, help="环境温度 ℃")
    p.add_argument("--flame", default=None, help="阻燃等级 none/ZC/ZB/ZA/A")
    p.add_argument("--no-lszh", action="store_true", help="不要求无卤低烟")
    p.add_argument("--fire-resistant", action="store_true", help="要求耐火")
    p.add_argument("--buried", action="store_true")
    p.add_argument("--wet", action="store_true")
    p.add_argument("--corrosive", action="store_true")
    p.add_argument("--rodent", action="store_true")
    p.add_argument("--oil", action="store_true")
    p.add_argument("--uv", action="store_true")
    p.add_argument("--flexible", action="store_true")
    p.add_argument("--emc", action="store_true")
    p.add_argument("--json", action="store_true", help="输出 JSON")
    p.add_argument("--list", action="store_true", help="列出场景预设")
    p.add_argument("--width", type=int, default=0, help=argparse.SUPPRESS)
    return p.parse_args(argv)


def pick_payload(args: argparse.Namespace) -> tuple[dict, str]:
    """返回 (请求参数, 场景说明)。显式参数优先于场景预设。"""
    payload: dict = {}
    label = "自定义输入"
    if args.scenario:
        for s in load_scenarios():
            if s["id"].lower() == args.scenario.lower():
                payload = dict(s["payload"])
                label = f'{s["id"]} · {s.get("full_name") or s["name"]}'
                break
        else:
            raise SystemExit(f"未知场景 {args.scenario}，用 --list 查看可用预设")

    if args.model:
        payload["model"] = args.model
    if args.installation:
        payload["installation"] = args.installation
    if args.ambient is not None:
        payload["ambient"] = args.ambient
    if args.flame:
        payload["flame"] = args.flame
    if args.no_lszh:
        payload["lszh"] = False
    for flag, key in [
        ("fire_resistant", "fire_resistant"),
        ("buried", "buried"),
        ("wet", "wet"),
        ("corrosive", "corrosive"),
        ("rodent", "rodent"),
        ("oil", "oil"),
        ("uv", "uv"),
        ("flexible", "flexible"),
        ("emc", "emc"),
    ]:
        if getattr(args, flag):
            payload[key] = True
    return payload or {}, label


TAGS = {
    "error": ("阻断", "bold #f85149"),
    "warn": ("风险", "bold #d29922"),
    "info": ("提示", "bold #58a6ff"),
}


def section(title: str, note: str = "", width: int = 100) -> Text:
    head = f"== {title} "
    tail = f" {note}" if note else ""
    fill = max(0, width - cell_len(head) - cell_len(tail) - 1)
    t = Text(head, style="bold #58a6ff")
    t.append("=" * fill, style="grey23")
    if note:
        t.append(tail, style="grey42")
    return t


def score_bar(score: int) -> Text:
    n = max(0, min(10, round(score / 10)))
    color = "green" if score >= 78 else ("yellow" if score >= 60 else "red")
    t = Text("[", style="grey37")
    t.append("#" * n, style=color)
    t.append("." * (10 - n), style="grey23")
    t.append("]", style="grey37")
    t.append(f" {score}", style=f"bold {color}")
    return t


def kv_grid(rows: list[tuple[str, Text | str]]) -> Table:
    g = Table.grid(padding=(0, 3))
    g.add_column(style="grey54", no_wrap=True, width=10)
    g.add_column()
    for k, v in rows:
        g.add_row(k, v if isinstance(v, Text) else Text(str(v), style="white"))
    return g


def render(res: dict, label: str, console: Console) -> None:
    s = res["summary"]
    parsed = res["parsed"]
    width = console.width

    console.print()
    console.print(
        Text("CableMind", style="bold #58a6ff"),
        Text("  电缆材料选型智能体", style="bold white"),
        Text("   v0.1 · 规则引擎 + 证据链", style="grey42"),
    )
    console.print(Text("=" * (width - 1), style="grey23"))
    console.print()

    conf = s["confidence"]
    console.print(section("输入与解析", label, width))
    console.print(
        kv_grid(
            [
                ("输入型号", Text(parsed["raw"], style="white")),
                (
                    "解析",
                    Text(
                        f'{parsed["conductor"]} / {parsed["insulation"] or "-"} / {parsed["sheath"] or "-"}'
                        f' / {parsed["cores"]}×{parsed["section"]:g} / {parsed["u0"]:g}/{parsed["u"]:g}kV',
                        style="grey78",
                    ),
                ),
                (
                    "置信度",
                    Text(
                        f'{conf:.2f}  {s["confidence_label"]}',
                        style="bold green" if conf >= 0.8 else "bold yellow",
                    ),
                ),
            ]
        )
    )

    console.print()
    console.print(section("七层结构选型", f'{sum(1 for L in res["layers"] if L["recommended"])} / 7 层适用', width))
    tbl = Table(box=None, pad_edge=False, padding=(0, 2), header_style="bold grey42")
    tbl.add_column("结构层", style="white", width=9, no_wrap=True)
    tbl.add_column("推荐材料", style="bold", width=22, no_wrap=True, overflow="ellipsis")
    tbl.add_column("得分", width=16, no_wrap=True)
    tbl.add_column("关键依据", style="grey70", width=34, no_wrap=True, overflow="ellipsis")
    for L in res["layers"]:
        rec = L["recommended"]
        if not rec:
            skip = (L.get("skip_reason") or "").split("；")[0]
            tbl.add_row(L["title"], Text("不适用", style="grey42"), Text(""), Text(clip(skip, 32), style="grey42"))
            continue
        reason = rec["reasons"][0].lstrip("+·− ") if rec.get("reasons") else ""
        tbl.add_row(L["title"], rec["name_cn"], score_bar(rec["score"]), Text(clip(reason, 33)))
    console.print(tbl)

    console.print()
    rec_model = s["recommended_model"]
    line = Text(" 建议型号  ", style="grey54")
    line.append(rec_model, style="bold green")
    if s.get("model_changed"):
        line.append("   与输入型号不同", style="yellow")
    console.print(line)
    if s.get("armor_note"):
        console.print(Text("            " + s["armor_note"], style="grey42"))
    console.print(Text(" " + "-" * (width - 2), style="grey23"))

    critical = [c for c in res["checks"] if c["level"] in ("error", "warn")]
    if critical:
        console.print()
        console.print(section("耦合与风险检查", f"{len(critical)} 项", width))
        for c in critical[:3]:
            tag, style = TAGS[c["level"]]
            t = Text(" [", style="grey37")
            t.append(tag, style=style)
            t.append("] ", style="grey37")
            t.append(c["title"], style="bold white")
            segs = wrap_cjk(c["detail"], width - 14)
            for i, seg in enumerate(segs[:2]):
                tail = " …" if i == 1 and len(segs) > 2 else ""
                t.append("\n         " + seg + tail, style="grey62")
            console.print(t)

    qs = res["questions"][:3]
    if qs:
        console.print()
        console.print(section("需要补齐的信息", f"共 {len(res['questions'])} 项", width))
        for i, item in enumerate(qs, 1):
            t = Text(f" {i}. ", style="cyan")
            t.append(item, style="grey78")
            console.print(t)

    cites = res["citations"][:3]
    if cites:
        console.print()
        console.print(section("证据链", f"共 {len(res['citations'])} 条来源", width))
        for it in cites:
            t = Text(" ")
            t.append(f'[{it["code"]}]', style="cyan")
            title = it["title"]
            t.append("  " + title[:46] + ("…" if len(title) > 46 else ""), style="grey78")
            console.print(t)
        console.print(Text(" 均为标准号级引用，未核实到条款号的不写条款号", style="grey42"))

    console.print()
    console.print(Text("=" * (width - 1), style="grey23"))
    console.print(
        Text(" 材料级结论，不等同于成品合规；阻燃等级、GB 31247 分级须按成品送检确认。", style="grey42")
    )
    console.print()


def main(argv: list[str] | None = None, console: Console | None = None) -> int:
    args = build_args(argv)
    if console is None:
        console = Console(width=args.width or None, highlight=False)

    if args.list:
        tbl = Table(box=None, header_style="bold grey62")
        tbl.add_column("ID", style="cyan")
        tbl.add_column("场景")
        tbl.add_column("型号", style="grey70")
        for s in load_scenarios():
            tbl.add_row(s["id"], s.get("full_name") or s["name"], s["payload"].get("model", ""))
        console.print(tbl)
        return 0

    payload, label = pick_payload(args)
    res = select(SelectionRequest(**payload))

    if args.json:
        console.print_json(json.dumps(res, ensure_ascii=False, default=str))
        return 0

    render(res, label, console)
    return 0


if __name__ == "__main__":
    sys.exit(main())
