const { chromium } = require("playwright");
(async () => {
  const browser = await chromium.launch({ executablePath: String.raw`C:\Users\samin\AppData\Local\ms-playwright\chromium-1169\chrome-win\chrome.exe` });
  const page = await browser.newPage();
  await page.setViewportSize({ width: 1400, height: 900 });
  await page.goto("http://localhost:8080", { waitUntil: "networkidle" });
  await page.screenshot({ path: String.raw`d:\projects\Inventory Optimization 3\shot_home.png` });
  console.log("HOME OK");
  const catLink = page.locator("text=Catalogue").first();
  await catLink.click();
  await page.waitForTimeout(1500);
  await page.screenshot({ path: String.raw`d:\projects\Inventory Optimization 3\shot_catalog.png` });
  console.log("CATALOG OK");
  await browser.close();
})();
