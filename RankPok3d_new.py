"""
RankPok3d — сканирование Booking.com на 3 ночи (Гданьск) и запись в лист «Rank POK3d».

Исправлено относительно старой версии:
  * листинг и колонки — как в основной таблице (RankPokGdansk), Сопот/Гдыни в этом листе нет;
  * при ошибке загрузки карточек таблица НЕ очищается (раньше все ячейки обнулялись);
  * если все объекты найдены — результат сохраняется (раньше его отбрасывал порог 50 карточек);
  * кнопка «ещё» ищется на польском и английском языке;
  * названия сравниваются без диакритики (Beksiński = Beksinski, René = Rene);
  * запись в таблицу одним batch-запросом вместо ~115 отдельных запросов с паузами.
"""
import asyncio
import datetime as dt
import html
import json
import os
import re
import socket
import sys
import time
import unicodedata
import urllib.parse
from typing import Dict, Optional, Tuple

import gspread
from google.oauth2.service_account import Credentials
from gspread.exceptions import APIError
from playwright.async_api import async_playwright, Page

# ============================================================
#                      КОНСТАНТЫ
# ============================================================
SPREADSHEET_URL = "https://docs.google.com/spreadsheets/d/1t8jZFnJ5PxW9ry8SPN8WAGhbsgICn5F9BZCR9d4vB5U/edit"
SHEET_NAME = "Rank POK3d"
STAY_NIGHTS = 3            # ночей в бронировании
DAYS_FORWARD = 60          # сколько дней вперёд сканировать
MAX_RANK = 500             # по шапке листа: «rank до 500»
MIN_CARDS = 50             # меньше карточек — страница не прогрузилась, результат не используем
MMRENT_COUNT_COLUMN = 3    # колонка C: «MMsrent 101p»
CREDENTIALS_FILE = "silken-glyph-443313-i4-6141460e5c8b.json"
GDANSK_CITY = "Гданьск"

# ============================================================
#     ЛИСТИНГИ ГДАНЬСКА: название -> номер колонки (A=1, I=9)
#     Совпадает с листом «Rank POK3d» (проверено по шапке)
# ============================================================
LISTING_COLUMNS_GDANSK = {
    "Cactus Room 10 Min to Old Town Gdańsk,PKP & Shopping Mall": 9,
    "Pineapple Room 10 Min to Old Town Gdańsk,PKP & Shopping Mall": 10,
    "Jager Room 10 Min to Old Town Gdańsk,PKP & Shopping Mall": 11,
    "Cosy Room - Peaceful Retreat, 2 Minutes to Gdańsk Old Town": 12,
    "Lovely Room - Peaceful Retreat, 2 Minutes to Gdańsk Old Town": 13,
    "Spacious Room - Peaceful Retreat, 2 Minutes to Gdańsk Old Town": 14,
    "Bay Room -15 Minutes to Gdańsk Old Town & Seaside": 15,
    "Mirror Room -15 Minutes to Gdańsk Old Town & Seaside": 16,
    "Beacon Room -15 Minutes to Gdańsk Old Town & Seaside": 17,
    "Superfly room 10 Min to Old Town Gdańsk,PKP & Shopping Mall": 18,
    "Sensational room 10 Min to Old Town Gdańsk,PKP & Shopping Mall": 19,
    "Vintage room 10 Min to Old Town Gdańsk,PKP & Shopping Mall": 20,
    "Leaf Room - 20 Minutes to Gdańsk Old Town & Seaside": 21,
    "Nest Room - 20 Minutes to Gdańsk Old Town & Seaside": 22,
    "Square Room - 20 Minutes to Gdańsk Old Town & Seaside": 23,
    "Twig Room - 20 Minutes to Gdańsk Old Town & Seaside": 24,
    "Alpha Room 15 min to Old Town Gdansk": 25,
    "Beta Room 10 min to Old Town Gdansk": 26,
    "Gamma Room 10 min to Old Town Gdansk": 27,
    "Downtown Room at Old Town Gdansk": 28,
    "Plaza Room at Old Town Gdansk": 29,
    "Danzig Room at Old Town Gdansk": 30,
    "Dali Room -10 Minutes to Gdańsk Old Town & Sea": 31,
    "Munch Room -10 Minutes to Gdańsk Old Town & Sea": 32,
    "Picasso Room -10 Minutes to Gdańsk Old Town & Sea": 33,
    "Beksiński Room -10 Minutes to Gdańsk Old Town & Sea": 34,
    "René Room -10 Minutes to Gdańsk Old Town & Sea": 35,
    "Van Gogh Room - 10 Minutes to Gdańsk Old Town & Sea": 36,
    "Creative Room - 10 Minutes to Gdańsk Old Town & Seaside": 37,
    "World Room - 10 Minutes to Gdańsk Old Town & Seaside": 38,
    "Lama Room - 10 Minutes to Gdańsk Old Town & Seaside": 39,
    "Colourful Room - 10 Minutes to Gdańsk Old Town & Seaside": 40,
    "Dolce Vita Room - 10 Minutes to Gdańsk Old Town & Seaside": 41,
    "Shrek Room 10 Min to Old Town Gdańsk,PKP & Shopping Mall": 43,
    "Scooby Doo Room 10 Min to Old Town Gdańsk,PKP & Shopping Mall": 45,
    "Pink Panther Room 10 Min to Old Town Gdańsk,PKP & Shopping Mall": 46,
    "Spongebob Room 10 Min to Old Town Gdańsk,PKP & Shopping Mall": 47,
    "Big Ben Room - Peaceful Retreat, 10 Minutes to Gdańsk Old Town & Seaside": 48,
    "Cracov Room - Peaceful Retreat, 10 Minutes to Gdańsk Old Town & Seaside": 49,
    "Breslau Room - Peaceful Retreat, 15 Minutes to Gdańsk Old Town & Seaside": 50,
    "Manhattan Room - Peaceful Retreat, 10 Minutes to Gdańsk Old Town & Seaside": 51,
    "Roma Room - Peaceful Retreat, 10 Minutes to Gdańsk Old Town & Seaside": 52,
    "Motlava Room - Peaceful Retreat, 10 Minutes to Gdańsk Old Town & Seaside": 53,
    "Shark Room - 3 km to Baltic Sea": 54,
    "Piggy Room - 3 km to Baltic Sea": 55,
    "Turtle Room - 3 km to Baltic Sea": 56,
    "Tiny Room - 10 Minutes to Gdańsk Old Town": 57,
    "Astro Room - 10 Minutes to Gdańsk Old Town": 58,
    "Future Room - 10 Minutes to Gdańsk Old Town": 59,
    "Hope Room - 10 Minutes to Gdańsk Old Town": 60,
    "Brand Room - 10 Minutes to Gdańsk Old Town": 61,
    "Umbrella Room - 10 Min to Old Town Gdańsk,PKP & Shopping Mall": 62,
    "Duo Room - 10 Min to Old Town Gdańsk,PKP & Shopping Mall": 63,
    "Teddy Bear Room - 10 Min to Old Town Gdańsk,PKP & Shopping Mall": 64,
    "Circus Room - 10 Min to Old Town Gdańsk,PKP & Shopping Mall": 65,
    "Bonjour Room - 10 Min to Old Town Gdańsk,PKP & Shopping Mall": 66,
    "Ocean Room - 1 km to Baltic Sea & 15 Minutes to Gdańsk Old Town": 67,
    "Sandcastle Room - 1 km to Baltic Sea & 15 Minutes to Gdańsk Old Town": 68,
    "Coral Room - 1 km to Baltic Sea & 15 Minutes to Gdańsk Old Town": 69,
    "Jungle Room Old Town Gdansk": 70,
    "Rose Room Old Town Gdansk": 71,
    "Paris Room Old Town Gdansk": 72,
    "Giraffe Room 5 min to Old Town Gdańsk": 73,
    "Zebra Room 5 min to Old Town Gdańsk": 74,
    "Monkey Room 5 min to Old Town Gdańsk": 75,
    "Snake Room 5 min to Old Town Gdańsk": 76,
    "Stitch Room 10 Min to Old Town Gdańsk,PKP & Shopping Mall": 42,
    "Lion King Room 10 Min to Old Town Gdańsk,PKP & Shopping Mall": 44,
    "Ruby Room at Old Town Gdańsk": 77,
    "Silver Room at Old Town Gdańsk": 78,
    "Bronze Room at Old Town Gdańsk": 79,
    "Mercury Room - 15 min to Old Town Gdansk": 80,
    "Hostel Room - 15 min to Old Town Gdansk": 81,
    "Earth Room - 15 min to Old Town Gdansk": 82,
    "Mars Room - 15 min to Old Town Gdansk": 83,
    "Jupiter Room - 15 min to Old Town Gdansk": 84,
    "Saturn Room - 15 min to Old Town Gdansk": 85,
    "Uranus Room - 15 min to Old Town Gdansk": 86,
    "Neptune Room - 15 min to Old Town Gdansk": 87,
    "Pluto Room - 15 min to Old Town Gdansk": 88,
    "Milkyway Room - 15 min to Old Town Gdansk": 89,
    "Sunlight Room - 15 min to Old Town Gdansk": 90,
    "Empire Room - 15 Minutes to Gdańsk Old Town & Seaside & Airport": 91,
    "Droid Room - 15 Minutes to Gdańsk Old Town & Seaside & Airport": 92,
    "Stormley Room - 15 Minutes to Gdańsk Old Town & Seaside & Airport": 93,
    "Bounty Room - 15 Minutes to Gdańsk Old Town & Seaside & Airport": 94,
    "Sir Yodington Room - 15 Minutes to Gdańsk Old Town & Seaside & Airport": 95,
    "Cinque Terre Guest Room - at Old Town Gdansk": 96,
    "Verona Guest Room - at Old Town Gdansk": 97,
    "Lisbon Guest Room - at Old Town Gdansk": 98,
    "Lucca Guest Room - at Old Town Gdansk": 99,
    "Adore Room - at Old Town Gdansk": 100,
    "Ballerina's Room - at Old Town Gdansk": 101,
    "Rosie Room - at Old Town Gdansk": 102,
    "Seredino Room 4 - at Old Town Gdansk": 103,
    "Petite Room - at Old Town Gdansk": 104,
    "Atelier Room - at Old Town Gdansk": 105,
    "Jazz Room - 15 Minutes to Gdańsk Old Town & Seaside & Airport": 106,
    "Baroque Rebel Room - 15 Minutes to Gdańsk Old Town & Seaside & Airport": 107,
    "Angel Mood Room - 15 Minutes to Gdańsk Old Town & Seaside & Airport": 108,
    "Dream Big Room - 15 Minutes to Gdańsk Old Town & Seaside & Airport": 109,
    "Frog Swing Room - 15 Minutes to Gdańsk Old Town & Seaside & Airport": 110,
    "Fast & Steady Room - 15 Minutes to Gdańsk Old Town & Seaside & Airport": 111,
    "Tiger Country Club Room - 15 Minutes to Gdańsk Old Town & Seaside & Airport": 112,
    "Mr Fox Room - 15 Minutes to Gdańsk Old Town & Seaside & Airport": 113,
    "Bengal Room - 10 Minutes to Gdańsk Old Town & Sea": 114,
    "Birman Room - 10 Minutes to Gdańsk Old Town & Sea": 115,
    "Siamese Room - 10 Minutes to Gdańsk Old Town & Sea": 116,
    "Maine Con Room - 10 Minutes to Gdańsk Old Town & Sea": 117,
    "Scotish Fold Room - 10 Minutes to Gdańsk Old Town & Sea": 118,
    "Ragdoll Room - 10 Minutes to Gdańsk Old Town & Sea": 119,
    "Industrial Room - at Old Town Gdansk": 120,
    "Loft Room - at Old Town Gdansk": 121,
    "Nordic Room - at Old Town Gdansk": 122,
    "Woody Room - at Old Town Gdansk": 123,
}


# ============================================================
#                      GOOGLE SHEETS
# ============================================================
def col_to_letter(col: int) -> str:
    result = ""
    while col > 0:
        col, rem = divmod(col - 1, 26)
        result = chr(65 + rem) + result
    return result


def authorize_gspread() -> gspread.Client:
    if "GOOGLE_CREDS" in os.environ:
        creds_json = json.loads(os.environ["GOOGLE_CREDS"])
        creds = Credentials.from_service_account_info(
            creds_json, scopes=["https://www.googleapis.com/auth/spreadsheets"])
        print("✅ GOOGLE_CREDS загружены из переменной окружения.")
    else:
        if not os.path.exists(CREDENTIALS_FILE):
            sys.exit(f"❌ Файл учетных данных '{CREDENTIALS_FILE}' не найден.")
        creds = Credentials.from_service_account_file(
            CREDENTIALS_FILE, scopes=["https://www.googleapis.com/auth/spreadsheets"])
        print(f"✅ Учетные данные загружены из файла {CREDENTIALS_FILE}")
    return gspread.authorize(creds)


def open_worksheet(gc: gspread.Client) -> gspread.Worksheet:
    return gc.open_by_url(SPREADSHEET_URL).worksheet(SHEET_NAME)


def find_row_by_date(ws: gspread.Worksheet, target: dt.date, retries: int = 3) -> Optional[int]:
    for attempt in range(1, retries + 1):
        try:
            for idx, cell in enumerate(ws.col_values(1), start=1):
                value = cell.strip()
                if not value:
                    continue
                for fmt in ("%d.%m.%Y", "%Y-%m-%d"):
                    try:
                        if dt.datetime.strptime(value, fmt).date() == target:
                            return idx
                    except ValueError:
                        pass
            return None
        except (APIError, ConnectionError, socket.error) as e:
            print(f"⚠️ Ошибка чтения дат (попытка {attempt}/{retries}): {e}")
            time.sleep(5 * attempt)
    raise RuntimeError(f"Не удалось прочитать даты из таблицы для {target}")


def write_results(ws: gspread.Worksheet, row: int, ranks: Dict[str, Optional[int]], mmrent: int) -> None:
    """Пишет все ранги строки одним batch-запросом. Ненайденные объекты очищаются."""
    updates = [{
        "range": f"{col_to_letter(MMRENT_COUNT_COLUMN)}{row}",
        "values": [[mmrent]],
    }]
    for name, col in LISTING_COLUMNS_GDANSK.items():
        rank = ranks.get(name)
        updates.append({
            "range": f"{col_to_letter(col)}{row}",
            "values": [[rank if rank is not None else ""]],
        })

    for attempt in range(1, 4):
        try:
            ws.batch_update(updates, value_input_option="USER_ENTERED")
            found = sum(1 for n in LISTING_COLUMNS_GDANSK if ranks.get(n) is not None)
            print(f"✅ [GSheets] строка {row}: записано, найдено {found}/{len(LISTING_COLUMNS_GDANSK)}")
            return
        except APIError as e:
            print(f"⚠️ Ошибка записи (попытка {attempt}/3): {e}")
            time.sleep(30 * attempt)  # лимит запросов Google — ждём
    print(f"❌ Строка {row} не записана после 3 попыток.")


# ============================================================
#                      BOOKING
# ============================================================
def build_gdansk_url(checkin: dt.date, checkout: dt.date) -> str:
    params = {
        "ss": "Gdańsk",
        "dest_id": "-501400",
        "dest_type": "city",
        "checkin_year": checkin.year,
        "checkin_month": checkin.month,
        "checkin_monthday": checkin.day,
        "checkout_year": checkout.year,
        "checkout_month": checkout.month,
        "checkout_monthday": checkout.day,
        "group_adults": "2",
        "no_rooms": "1",
        "sb_price_type": "total",
        "order": "price",
    }
    return "https://www.booking.com/searchresults.pl.html?" + urllib.parse.urlencode(params)


def norm(text: str) -> str:
    """Нижний регистр, без диакритики и знаков: «Beksiński» -> «beksinski»."""
    text = html.unescape(text or "").lower()
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return " ".join(re.sub(r"[^a-z0-9\s]", " ", text).split())


async def block_heavy_resources(page: Page) -> None:
    async def handler(route):
        if route.request.resource_type in {"image", "media", "font"}:
            await route.abort()
        else:
            await route.continue_()
    await page.route("**/*", handler)


CARD_SELECTOR = 'div[data-testid="property-card"]'
TITLE_SELECTOR = 'h2, h3, [data-testid="title"]'
# Кнопка «ещё»: польский и английский варианты текста
SHOW_MORE_JS = """() => {
    const re = /Załaduj więcej wyników|Load more results|Show more results/i;
    const btn = Array.from(document.querySelectorAll('button, [role="button"]'))
        .find(b => re.test(b.innerText || ''));
    if (!btn) return false;
    btn.scrollIntoView({block: 'center'});
    btn.click();
    return true;
}"""


async def scrape_ranks(page: Page) -> Tuple[Optional[Dict[str, Optional[int]]], int]:
    """Возвращает (ranks, mmrent_count). ranks = None, если страница не прогрузилась."""
    targets = {}                                   # нормализованная начальная часть имени -> полное имя
    for name in LISTING_COLUMNS_GDANSK:
        targets[" ".join(norm(name).split()[:3])] = name
    ranks: Dict[str, Optional[int]] = {name: None for name in LISTING_COLUMNS_GDANSK}
    remaining = dict(targets)                       # ключ -> имя, ещё не найденные
    mmrent_count = 0
    scanned = 0
    retries = 0

    try:
        await page.wait_for_selector(CARD_SELECTOR, timeout=30000)
    except Exception:
        print("   ⚠️ Карточки не появились на странице.")
        return None, 0

    for _ in range(60):
        await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        await asyncio.sleep(1.5)

        titles = await page.evaluate(f"""() => Array.from(document.querySelectorAll('{CARD_SELECTOR}'))
            .map(c => {{ const t = c.querySelector('{TITLE_SELECTOR}');
                         return t ? (t.innerText || '').split('\\n')[0] : ''; }})""")
        total = len(titles)

        if total > scanned:
            for i, raw in enumerate(titles[scanned:MAX_RANK], start=scanned):
                rank = i + 1
                title = norm(raw)
                if "mmrent" in title:
                    mmrent_count += 1
                for key, name in list(remaining.items()):
                    if key in title:
                        ranks[name] = rank
                        del remaining[key]
                        print(f"      ✨ #{rank}: {name}")
            scanned = min(total, MAX_RANK)
            retries = 0
            print(f"      [Страница] Загружено карточек: {scanned}")
        else:
            retries += 1

        if not remaining or scanned >= MAX_RANK or retries >= 10:
            break

        before = total
        clicked = await page.evaluate(SHOW_MORE_JS)
        if clicked:
            for _ in range(20):                    # ждём до 10 с, пока появятся новые карточки
                await asyncio.sleep(0.5)
                if await page.evaluate(f"document.querySelectorAll('{CARD_SELECTOR}').length") > before:
                    break
        else:
            await asyncio.sleep(2)

    if remaining:
        print(f"   ❌ НЕ НАЙДЕНО ({len(remaining)}):")
        for name in remaining.values():
            print(f"      - {name}")
    else:
        print("   🎯 ВСЕ объекты найдены!")

    # Защита от неполной загрузки — только если не все объекты найдены.
    # Если все найдены, результат верный даже при небольшом числе карточек.
    if remaining and scanned < MIN_CARDS:
        print(f"   ⚠️ Загружено только {scanned} карточек (< {MIN_CARDS}). Результат не используется.")
        return None, 0

    return ranks, mmrent_count


# ============================================================
#                      ОСНОВНОЙ ЦИКЛ
# ============================================================
async def process_date(page: Page, ws: gspread.Worksheet, date_obj: dt.date) -> None:
    print(f"\n>>>> ДАТА: {date_obj:%d.%m.%Y} ({STAY_NIGHTS} ночи) <<<<")
    row = find_row_by_date(ws, date_obj)
    if not row:
        print(f"⚠️ Даты {date_obj:%d.%m.%Y} нет в листе. Пропуск.")
        return

    checkin = date_obj
    checkout = checkin + dt.timedelta(days=STAY_NIGHTS)
    try:
        await page.goto(build_gdansk_url(checkin, checkout), timeout=120000, wait_until="domcontentloaded")
        await asyncio.sleep(3)
        try:
            cookie_btn = page.locator("button#onetrust-accept-btn-handler")
            if await cookie_btn.is_visible(timeout=5000):
                await cookie_btn.click()
                print("🍪 Cookies закрыты.")
        except Exception:
            pass

        ranks, mmrent = await scrape_ranks(page)
        if ranks is None:
            print(f"⚠️ {date_obj:%d.%m.%Y}: карточки не загрузились, таблица не изменена.")
            return
        write_results(ws, row, ranks, mmrent)
    except Exception as e:
        print(f"❌ Ошибка на дату {date_obj:%d.%m.%Y}: {e}")


async def main_async() -> None:
    gc = authorize_gspread()
    ws = open_worksheet(gc)
    now = dt.datetime.now().strftime("%d.%m.%Y %H:%M:%S")
    try:
        ws.update_cell(1, 1, f"📅 Skanowanie rozpoczęło się: {now}")
    except Exception as e:
        print(f"❌ Не записано время старта: {e}")

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=["--disable-blink-features=AutomationControlled", "--no-sandbox",
                  "--disable-dev-shm-usage", "--disable-gpu"],
        )
        context = await browser.new_context(
            user_agent=("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"),
            locale="pl-PL",
            viewport={"width": 1920, "height": 1080},
        )
        page = await context.new_page()
        await block_heavy_resources(page)

        today = dt.date.today()
        for i in range(DAYS_FORWARD):
            await process_date(page, ws, today + dt.timedelta(days=i))
            await asyncio.sleep(2)

        await browser.close()
    print("\n🏁 ГОТОВО — Гданьск (3 ночи) обработан\n")


if __name__ == "__main__":
    asyncio.run(main_async())
