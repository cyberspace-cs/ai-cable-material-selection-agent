"""复现 README 中的截图。

用法：
    1. 先启动服务：uvicorn app.main:app --port 8000
    2. python scripts/screenshot.py [base_url] [out_dir]
"""
import asyncio
import sys
from pathlib import Path

from playwright.async_api import async_playwright

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8000"
OUT = Path(sys.argv[2] if len(sys.argv) > 2 else "screenshots")
OUT.mkdir(parents=True, exist_ok=True)


async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        page = await browser.new_page(viewport={"width": 1600, "height": 1000}, device_scale_factor=1.125)
        await page.goto(BASE, wait_until="networkidle")
        await page.wait_for_timeout(900)

        await page.screenshot(path=str(OUT / "00-hero.jpg"), clip={"x": 0, "y": 0, "width": 1600, "height": 960}, quality=90, type="jpeg")
        await page.screenshot(path=str(OUT / "01-workbench.jpg"), full_page=True, quality=88, type="jpeg")

        await page.click('#scenarioChips .chip[data-idx="1"]')
        await page.wait_for_timeout(900)
        await page.screenshot(path=str(OUT / "02-coastal.jpg"), full_page=True, quality=88, type="jpeg")

        await page.click('#scenarioChips .chip[data-idx="2"]')
        await page.wait_for_timeout(900)
        await page.screenshot(path=str(OUT / "02b-fire.jpg"), full_page=True, quality=88, type="jpeg")

        for view, name in [("materials", "03-materials.jpg"), ("evidence", "04-evidence.jpg"), ("validation", "05-validation.jpg")]:
            await page.click(f'[data-view="{view}"]')
            await page.wait_for_timeout(500)
            await page.screenshot(path=str(OUT / name), full_page=True, quality=88, type="jpeg")

        await browser.close()
        print("saved to", OUT.resolve())


if __name__ == "__main__":
    asyncio.run(main())
