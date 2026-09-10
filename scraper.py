import random
import time

from playwright.sync_api import Page, sync_playwright

from config import MAX_DELAY_SECONDS, MAX_LEADS, MIN_DELAY_SECONDS


def _human_delay() -> None:
    time.sleep(random.uniform(MIN_DELAY_SECONDS, MAX_DELAY_SECONDS))


def _extract_listing_details(page: Page) -> dict | None:
    try:
        name = page.locator("h1.DUwDvf").first.inner_text(timeout=5000).strip()
    except Exception:
        return None

    address = None
    phone = None

    try:
        address_label = page.locator('button[data-item-id="address"]').first.get_attribute(
            "aria-label", timeout=3000
        )
        address = address_label.split(":", 1)[1].strip() if address_label else None
    except Exception:
        pass

    try:
        phone_label = page.locator('button[data-item-id^="phone:tel:"]').first.get_attribute(
            "aria-label", timeout=3000
        )
        phone = phone_label.split(":", 1)[1].strip() if phone_label else None
    except Exception:
        pass

    has_website = page.locator('a[data-item-id="authority"]').count() > 0

    rating = None
    reviews_count = None
    try:
        rating_container = page.locator("div.F7nice").first
        rating_text = rating_container.inner_text(timeout=3000)
        # Пример текста: "4.7\n(2,196)"
        lines = [line.strip() for line in rating_text.splitlines() if line.strip()]
        if lines:
            rating = lines[0]
        if len(lines) > 1:
            reviews_count = lines[1].strip("()").replace(",", "")
    except Exception:
        pass

    return {
        "name": name,
        "phone": phone,
        "address": address,
        "has_website": "да" if has_website else "нет",
        "rating": rating or "",
        "reviews_count": reviews_count or "",
    }


def run(query: str, max_leads: int = MAX_LEADS):
    """Генератор: ищет query в Google Maps и по одному отдаёт найденные места
    (name/phone/address). Останавливается на max_leads или когда результаты
    заканчиваются."""
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        page = browser.new_page()
        page.goto("https://www.google.com/maps", timeout=60000)

        page.locator('input[name="q"]').fill(query)
        page.keyboard.press("Enter")
        page.wait_for_timeout(4000)

        feed = page.locator('div[role="feed"]')
        listings = feed.locator("a.hfpxzc")

        seen_count = 0
        collected = 0
        stagnant_scrolls = 0

        while collected < max_leads:
            total = listings.count()

            if total <= seen_count:
                feed.evaluate("el => el.scrollTop = el.scrollHeight")
                page.wait_for_timeout(2000)
                if listings.count() <= total:
                    stagnant_scrolls += 1
                    if stagnant_scrolls >= 3:
                        break
                else:
                    stagnant_scrolls = 0
                continue

            for i in range(seen_count, total):
                if collected >= max_leads:
                    break

                listings.nth(i).click()
                page.wait_for_timeout(2000)
                _human_delay()

                try:
                    details = _extract_listing_details(page)
                except Exception:
                    details = None

                seen_count = i + 1

                if details is None:
                    continue

                yield details
                collected += 1

        browser.close()
