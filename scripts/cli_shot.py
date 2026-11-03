"""把 CLI 的真实运行输出渲染成终端风格截图。

不使用伪造文本：直接调用 app.cli.main()，用 rich 录制真实输出，再导出 HTML 截图。
输出：screenshots/cli-01.png（基准场景）、screenshots/cli-02.png（沿海场景）

用法：python3 scripts/cli_shot.py [--out screenshots]
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from rich.console import Console
from rich.theme import Theme

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import cli  # noqa: E402

# 深色终端配色：把 rich 的命名色重映射到 GitHub Dark 调色板
DARK = Theme(
    {
        "white": "#e6edf3",
        "grey23": "#21262d",
        "grey30": "#30363d",
        "grey37": "#484f58",
        "grey42": "#6e7681",
        "grey54": "#8b949e",
        "grey62": "#9aa4af",
        "grey70": "#b3bcc6",
        "grey78": "#d0d7de",
        "green": "#3fb950",
        "yellow": "#d29922",
        "cyan": "#58a6ff",
        "red": "#f85149",
    },
    inherit=True,
)

WRAP = """<!DOCTYPE html><html><head><meta charset="utf-8">
<style>
  html,body {{ margin:0; padding:0; background:#010409; }}
  body {{ padding:28px; }}
  .win {{
    background:#0d1117; border:1px solid #30363d; border-radius:10px;
    box-shadow:0 18px 48px rgba(0,0,0,.55); overflow:hidden; display:inline-block;
  }}
  .bar {{
    display:flex; align-items:center; gap:8px; padding:11px 16px;
    background:#161b22; border-bottom:1px solid #30363d;
    font:13px/1 -apple-system,"Segoe UI",sans-serif; color:#8b949e;
  }}
  .dot {{ width:11px; height:11px; border-radius:50%; }}
  .t {{ margin-left:10px; letter-spacing:.2px; }}
  .cmd {{ margin-left:auto; font-family:"Noto Sans Mono CJK SC",monospace; font-size:12px; color:#6e7681; }}
  pre {{
    margin:0; padding:20px 22px 24px;
    font-family:"Noto Sans Mono CJK SC","DejaVu Sans Mono",monospace;
    font-size:13px; line-height:1.75; color:#e6edf3; background:#0d1117;
    white-space:pre;
  }}
</style></head><body>
<div class="win">
  <div class="bar">
    <span class="dot" style="background:#ff5f56"></span>
    <span class="dot" style="background:#ffbd2e"></span>
    <span class="dot" style="background:#27c93f"></span>
    <span class="t">cablemind — zsh</span>
    <span class="cmd">{cmd}</span>
  </div>
  {pre}
</div>
</body></html>"""


def build_html(argv: list[str], cmd: str) -> str:
    console = Console(
        record=True,
        width=104,
        force_terminal=True,
        color_system="truecolor",
        theme=DARK,
        highlight=False,
    )
    cli.main(argv, console=console)
    inner = console.export_html(inline_styles=True)
    pre = inner.split("<body>", 1)[1].rsplit("</body>", 1)[0].strip()
    # rich 在 <pre> 上内联了 Menlo/DejaVu，会覆盖 CSS；统一换成中文等宽字体，
    # 保证 ASCII:中文 = 1:2，和终端列宽一致
    for old in ("Menlo,'DejaVu Sans Mono',consolas,'Courier New',monospace",
                "Menlo, 'DejaVu Sans Mono', consolas, 'Courier New', monospace"):
        pre = pre.replace(old, "'Noto Sans Mono CJK SC',monospace")
    return WRAP.format(cmd=cmd, pre=pre)


async def shoot(pairs: list[tuple[str, Path]]) -> None:
    from playwright.async_api import async_playwright

    async with async_playwright() as p:
        b = await p.chromium.launch()
        page = await b.new_page(viewport={"width": 1100, "height": 900}, device_scale_factor=2)
        for html, out in pairs:
            tmp = Path("/tmp") / (out.stem + ".html")
            tmp.write_text(html, encoding="utf-8")
            await page.goto(tmp.as_uri(), wait_until="networkidle")
            await page.wait_for_timeout(250)
            el = await page.query_selector(".win")
            await el.screenshot(path=str(out))
            print("saved", out, out.stat().st_size // 1024, "KB")
        await b.close()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(ROOT / "screenshots"))
    args = ap.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    pairs = [
        (build_html(["--scenario", "S1"], "python -m app.cli --scenario S1"), out / "cli-01.png"),
        (build_html(["--scenario", "S2"], "python -m app.cli --scenario S2"), out / "cli-02.png"),
    ]
    asyncio.run(shoot(pairs))


if __name__ == "__main__":
    main()
