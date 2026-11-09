#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Antalya Etkinlik Takvimi ve Fırsat Paneli - Otomatik Veri Kazıyıcı (Scraper)
Hedef Kaynaklar: Bubilet, Biletinial, Biletix, Passo
Çıktılar: events.json, antalya_etkinlikler.ics
"""

import asyncio
import hashlib
import json
import os
import re
from datetime import datetime, timedelta, timezone
from urllib.parse import urljoin

try:
    from bs4 import BeautifulSoup
except ImportError:
    BeautifulSoup = None

try:
    from playwright.async_api import async_playwright
except ImportError:
    async_playwright = None

# ============================================================================
# 1. SABİTLER & SIKI ANTALYA FİLTRESİ KURALLARI
# ============================================================================

# Sadece gerçekten Antalya'ya ve ilçelerine/mekanlarına özgü kelimeler
ANTALYA_KEYWORDS = [
    "antalya", "alanya", "manavgat", "kemer", "kaş", "kalkan",
    "serik", "belek", "muratpaşa", "muratpasa", "konyaaltı", "konyaalti",
    "kepez", "döşemealtı", "dosemealti", "aksu", "kumluca", "finike",
    "demre", "gazipaşa", "gazipasa", "korkuteli", "elmalı", "elmali",
    "kaleiçi", "kaleici", "side", "aspendos", "olympos", "olimpos",
    "holly stone antalya", "jolly joker antalya", "antalya açıkhava",
    "antalya acikhava", "cam piramit", "antalya akm", "türkan şoray",
    "turkan soray", "haşim işcan", "hasim iscan", "doğu garajı", "dogu garaji",
    "mall of antalya", "sponge pub", "the land of legends", "akdeniz üniversitesi"
]

# Başka şehirlerin ve İstanbul/İzmir/Ankara mekanlarının sızmasını kesin olarak engelleyen kara liste
EXCLUDED_CITIES = [
    "istanbul", "ankara", "izmir", "bursa", "adana", "mersin", "eskişehir",
    "eskisehir", "konya", "kayseri", "samsun", "trabzon", "diyarbakır",
    "diyarbakir", "gaziantep", "şanlıurfa", "sanliurfa", "kocaeli", "izmit",
    "sakarya", "adapazarı", "balıkesir", "çanakkale", "canakkale",
    "muğla", "mugla", "bodrum", "marmaris", "fethiye", "denizli", "aydın",
    "aydin", "kuşadası", "kusadasi", "edirne", "tekirdağ", "tekirdag",
    "hatay", "antakya", "malatya", "erzurum", "van", "sivas", "manisa",
    "afyon", "kütahya", "kutahya", "ısparta", "isparta", "burdur", "aksaray",
    "nevşehir", "nevsehir", "kapadokya", "cappadocia", "zonguldak", "bartın",
    "karabük", "bolu", "düzce", "duzce", "yalova", "kibris", "kıbrıs", "lefkoşa",
    "girne", "kadıköy", "kadikoy", "beşiktaş", "besiktas", "şişli", "sisli",
    "beyoğlu", "beyoglu", "ataşehir", "atasehir", "bakırköy", "bakirkoy",
    "bostanlı", "bostanli", "alsancak", "karşıyaka", "karsiyaka", "bornova",
    "çankaya", "cankaya", "yenimahalle", "nilüfer", "nilufer", "osmangazi",
    # İstanbul / Ankara / İzmir özel mekanları
    "harbiye", "cemil topuzlu", "taksim", "istiklal", "babylon", "zorlu",
    "volkswagen arena", "küçükçiftlik", "kucukciftlik", "paribu art", "fişekhane",
    "fisekhane", "dasdas", "unico", "maximum uniq", "kozzy", "veliefendi",
    "akasya", "tersane", "sanat performance", "if performance", "dorock",
    "hayal kahvesi", "blind", "klein", "karga", "moda sahnesi", "bostancı",
    "bostanci", "congresium", "cso ada", "odtü", "odtu", "bilkent", "cerModern"
]

TURKISH_MONTHS = {
    "ocak": 1, "şubat": 2, "subat": 2, "mart": 3, "nisan": 4,
    "mayıs": 5, "mayis": 5, "haziran": 6, "temmuz": 7, "ağustos": 8,
    "agustos": 8, "eylül": 9, "eylul": 9, "ekim": 10, "kasım": 11,
    "kasim": 11, "aralık": 12, "aralik": 12,
    "oca": 1, "şub": 2, "sub": 2, "mar": 3, "nis": 4, "may": 5,
    "haz": 6, "tem": 7, "ağu": 8, "agu": 8, "eyl": 9, "eki": 10,
    "kas": 11, "ara": 12
}

# ============================================================================
# 2. YARDIMCI FONKSİYONLAR (NORMALİZASYON, FİLTRELEME, KATEGORİZASYON)
# ============================================================================

def normalize_tr(text: str) -> str:
    """Türkçe karakterleri ve büyük/küçük harfleri standart küçük metne çevirir."""
    if not text:
        return ""
    replacements = {
        "İ": "i", "I": "ı", "Ğ": "ğ", "Ü": "ü", "Ş": "ş", "Ö": "ö", "Ç": "ç"
    }
    for k, v in replacements.items():
        text = text.replace(k, v)
    return text.lower().strip()


def normalize_ascii(text: str) -> str:
    """Arama ve karşılaştırma için ASCII tabanlı metne dönüştürür."""
    text = normalize_tr(text)
    tr_to_ascii = str.maketrans("ığüşöç", "igusoc")
    return text.translate(tr_to_ascii)


def is_strictly_antalya(title: str, venue: str, url: str, city_hint: str = "") -> bool:
    """
    Etkinliğin kesinlikle Antalya'ya ait olduğunu doğrular.
    Başka bir şehir veya şehir dışı mekan adı geçiyorsa kesin olarak reddeder.
    """
    combined = f"{title} {venue} {url} {city_hint}"
    norm_text = normalize_tr(combined)
    ascii_text = normalize_ascii(combined)

    # 1) Kara listedeki başka bir şehir veya şehir dışı mekan geçiyorsa KESİN REDDET
    for bad_city in EXCLUDED_CITIES:
        bad_norm = normalize_tr(bad_city)
        bad_ascii = normalize_ascii(bad_city)
        if len(bad_norm) >= 4:
            if bad_norm in norm_text or bad_ascii in ascii_text:
                return False
        else:
            pattern = rf"\b{re.escape(bad_norm)}\b"
            if re.search(pattern, norm_text) or re.search(pattern, ascii_text):
                return False

    # 2) En az bir Antalya anahtar kelimesi kelime sınırlarıyla eşleşmeli
    for kw in ANTALYA_KEYWORDS:
        kw_norm = normalize_tr(kw)
        pattern = rf"\b{re.escape(kw_norm)}\b"
        if re.search(pattern, norm_text) or re.search(pattern, ascii_text):
            return True

    return False


def categorize_event(title: str, description: str = "", url: str = "") -> str:
    """
    Etkinlik başlığı ve metnindeki anahtar kelimelere göre otomatik kategori atar.
    Öncelik sırasıyla:
    1. Stand-Up & Komedi
    2. Atölye & Çocuk
    3. Festival & Parti
    4. Konser & Müzik
    5. Tiyatro & Sahne
    """
    title_norm = normalize_tr(title)
    full_norm = normalize_tr(f"{title} {description} {url}")

    # 1. Stand-Up & Komedi
    standup_keywords = [
        "stand-up", "stand up", "standup", "komedi", "güldürü", "gulduru",
        "açık mikrofon", "acik mikrofon", "open mic", "talk show", "mizah",
        "cmylmz", "baturay", "doğu demirkol", "dogu demirkol", "yasemin sakallıoğlu",
        "gökhan ünver", "gokhan unver", "mesut süre", "mesut sure", "ilişki testi",
        "iliski testi", "meksika açmazı", "meksika acmazi", "kalt", "tuzbiber",
        "ilker gümüşoluk", "ilker gumusoluk", "sergen deveci", "salih tıraş",
        "salih tiras", "mahşer-i cümbüş", "mahseri cumbus", "doğaçlama", "dogaclama",
        "bengi idil uras", "miray akovalıgil", "miray akovaligil"
    ]
    for kw in standup_keywords:
        if kw in full_norm:
            return "Stand-Up & Komedi"

    # 2. Atölye & Çocuk
    workshop_kids_keywords = [
        "atölye", "atolye", "workshop", "çocuk", "cocuk", "kids", "masal",
        "kukla", "illüzyon", "sihirbaz", "sergi", "müze", "muze", "kum heykel",
        "balon turu", "theme park", "land of legends", "kırmızı başlıklı kız",
        "kirmizi baslikli kiz", "ormanın kalbi", "ormanin kalbi", "kübik prens",
        "kubik prens", "hacivat", "karagöz", "karagoz", "kırmızı cüce", "kirmizi cuce"
    ]
    for kw in workshop_kids_keywords:
        if kw in full_norm:
            return "Atölye & Çocuk"

    # 3. Festival & Parti
    party_keywords = [
        "festival", "fest", "parti", "party", "dj", "after party", "afterparty",
        "rave", "elektronik", "techno", "house music", "90lar", "90'lar", "90’lar",
        "2000ler", "2000'ler", "retro", "karnaval", "dans gecesi", "karaoke"
    ]
    for kw in party_keywords:
        if kw in full_norm:
            return "Festival & Parti"

    # 4. Konser & Müzik (URL'de /muzik/ geçiyorsa veya konser anahtar kelimesi varsa)
    concert_keywords = [
        "konser", "akustik", "live", "canlı", "canli", "orkestra", "senfoni",
        "caz", "jazz", "rock", "pop", "rap", "hiphop", "alternatif", "türkü",
        "turku", "arabesk", "resital", "tribute", "sympho", "candle session",
        "şarkılar", "sarkilar", "holly stone", "jolly joker", "/muzik/"
    ]
    for kw in concert_keywords:
        if kw in full_norm:
            return "Konser & Müzik"

    # 5. Tiyatro & Sahne
    theater_keywords = [
        "tiyatro", "oyun", "müzikal", "muzikal", "bale", "opera", "sahne",
        "trajedi", "dram", "kabare", "pandomim", "dans gösterisi", "gösteri",
        "shakespeare", "moliere", "perde", "kumpanya", "devlet tiyatrosu", "/tiyatro/"
    ]
    for kw in theater_keywords:
        if kw in full_norm:
            return "Tiyatro & Sahne"

    return "Konser & Müzik"


def parse_turkish_date(date_str: str) -> tuple[str, str]:
    """
    Türkçe tarih ifadelerini ("15 Kasım 2026 21:00", "24 Kas Cmt", ISO 8601 vb.)
    analiz eder ve (ISO_DATE, DISPLAY_DATE) döndürür.
    """
    now = datetime.now()
    if not date_str:
        iso = (now + timedelta(days=3)).strftime("%Y-%m-%dT20:00:00")
        return iso, "Tarih İçin Sayfaya Bakınız"

    clean_str = " ".join(date_str.strip().split())

    # 1) ISO format kontrolü (örn: 2026-11-15T21:00:00)
    iso_match = re.search(r"(\d{4})-(\d{2})-(\d{2})(?:[T\s](\d{2}):(\d{2}))?", clean_str)
    if iso_match:
        y, m, d = int(iso_match.group(1)), int(iso_match.group(2)), int(iso_match.group(3))
        hh = int(iso_match.group(4)) if iso_match.group(4) else 20
        mm = int(iso_match.group(5)) if iso_match.group(5) else 0
        try:
            dt = datetime(y, m, d, hh, mm)
            month_names = ["", "Ocak", "Şubat", "Mart", "Nisan", "Mayıs", "Haziran",
                           "Temmuz", "Ağustos", "Eylül", "Ekim", "Kasım", "Aralık"]
            disp = f"{d} {month_names[m]} {y} • {hh:02d}:{mm:02d}"
            return dt.strftime("%Y-%m-%dT%H:%M:%S"), disp
        except ValueError:
            pass

    # 2) DD.MM.YYYY veya DD/MM/YYYY
    dot_match = re.search(r"(\d{1,2})[./](\d{1,2})[./](\d{4})(?:\s+(\d{1,2}):(\d{2}))?", clean_str)
    if dot_match:
        d, m, y = int(dot_match.group(1)), int(dot_match.group(2)), int(dot_match.group(3))
        hh = int(dot_match.group(4)) if dot_match.group(4) else 20
        mm = int(dot_match.group(5)) if dot_match.group(5) else 0
        try:
            dt = datetime(y, m, d, hh, mm)
            month_names = ["", "Ocak", "Şubat", "Mart", "Nisan", "Mayıs", "Haziran",
                           "Temmuz", "Ağustos", "Eylül", "Ekim", "Kasım", "Aralık"]
            disp = f"{d} {month_names[m]} {y} • {hh:02d}:{mm:02d}"
            return dt.strftime("%Y-%m-%dT%H:%M:%S"), disp
        except ValueError:
            pass

    # 3) Türkçe Ay İsimli Format (örn: "18 Kasım 2026 21:00" veya "18 Kasım Çarşamba")
    norm = normalize_tr(clean_str)
    time_match = re.search(r"(\d{1,2}):(\d{2})", norm)
    hh = int(time_match.group(1)) if time_match else 20
    mm = int(time_match.group(2)) if time_match else 0

    for m_name, m_num in TURKISH_MONTHS.items():
        pattern = rf"\b(\d{{1,2}})\s+{re.escape(m_name)}\b(?:\s+(\d{{4}}))?"
        m_match = re.search(pattern, norm)
        if m_match:
            day = int(m_match.group(1))
            year = int(m_match.group(2)) if m_match.group(2) else now.year
            try:
                dt = datetime(year, m_num, day, hh, mm)
                if not m_match.group(2) and dt < now - timedelta(days=2):
                    year += 1
                    dt = datetime(year, m_num, day, hh, mm)
                month_names = ["", "Ocak", "Şubat", "Mart", "Nisan", "Mayıs", "Haziran",
                               "Temmuz", "Ağustos", "Eylül", "Ekim", "Kasım", "Aralık"]
                disp = f"{day} {month_names[m_num]} {year} • {hh:02d}:{mm:02d}"
                return dt.strftime("%Y-%m-%dT%H:%M:%S"), disp
            except ValueError:
                continue

    fallback_iso = (now + timedelta(days=5)).strftime("%Y-%m-%dT20:00:00")
    return fallback_iso, "Tarih İçin Sayfaya Bakınız"


def generate_event_id(title: str, source: str, url: str) -> str:
    raw = f"{normalize_ascii(title)}-{source}-{url}"
    return hashlib.md5(raw.encode("utf-8")).hexdigest()[:12]


def is_valid_title(title: str) -> bool:
    """'Detaylı İncele', 'Bilet Al' gibi geçersiz başlıkları eler."""
    if not title or len(title.strip()) < 3:
        return False
    bad_titles = [
        "detaylı incele", "detayli incele", "bilet al", "incele",
        "detaylar", "tümü", "tumu", "satın al", "satin al", "etkinlikler"
    ]
    t_norm = normalize_tr(title)
    if t_norm in bad_titles:
        return False
    return True


def normalize_image_url(img: str, base_url: str, source: str = "") -> str:
    if not img or not isinstance(img, str):
        return ""
    img = img.strip()
    if img.startswith("data:"):
        return ""
    if img.startswith("//"):
        return "https:" + img
    if img.startswith("http://") or img.startswith("https://"):
        return img
    if source == "Passo" and not img.startswith("/"):
        return f"https://im.passo.com.tr/api/r/event/{img}"
    return urljoin(base_url, img)


# ============================================================================
# 3. KAYNAK KAZIYICILAR (PLAYWRIGHT STEALTH + JSON-LD + DOM PARSING)
# ============================================================================

def extract_json_ld_events(soup: BeautifulSoup, source_name: str, base_url: str) -> list[dict]:
    """Sayfa içindeki Schema.org JSON-LD Event verilerini otomatik yakalar."""
    found = []
    scripts = soup.find_all("script", type="application/ld+json")
    for script in scripts:
        try:
            raw_text = script.string or script.get_text()
            if not raw_text:
                continue
            data = json.loads(raw_text)
            items = data if isinstance(data, list) else [data]
            for item in list(items):
                if isinstance(item, dict) and "@graph" in item and isinstance(item["@graph"], list):
                    items.extend(item["@graph"])
            for item in items:
                if not isinstance(item, dict):
                    continue
                item_type = str(item.get("@type", "")).lower()
                if "event" not in item_type:
                    continue

                title = (item.get("name") or "").strip()
                if not is_valid_title(title):
                    continue

                url = item.get("url") or base_url
                url = urljoin(base_url, url)

                loc = item.get("location") or {}
                venue = "Antalya"
                city_hint = ""
                if isinstance(loc, dict):
                    venue = (loc.get("name") or "Antalya").strip()
                    addr = loc.get("address") or {}
                    if isinstance(addr, dict):
                        city_hint = f"{addr.get('addressLocality', '')} {addr.get('addressRegion', '')}"
                    elif isinstance(addr, str):
                        city_hint = addr

                if not is_strictly_antalya(title, venue, url, city_hint):
                    continue

                start_date = item.get("startDate") or ""
                iso_date, display_date = parse_turkish_date(start_date)

                offers = item.get("offers") or {}
                price = f"{source_name}'da İncele"
                if isinstance(offers, list) and offers:
                    offers = offers[0]
                if isinstance(offers, dict):
                    p_val = offers.get("price") or offers.get("lowPrice")
                    curr = offers.get("priceCurrency", "₺")
                    if curr == "TRY":
                        curr = "₺"
                    if p_val:
                        price = f"₺{p_val}" if curr == "₺" else f"{p_val} {curr}"

                img = item.get("image") or ""
                if isinstance(img, list) and img:
                    img = img[0]
                elif isinstance(img, dict):
                    img = img.get("url", "")
                img_url = normalize_image_url(str(img), base_url, source_name)

                found.append({
                    "id": generate_event_id(title, source_name, url),
                    "title": title,
                    "category": categorize_event(title, venue, url),
                    "date_iso": iso_date,
                    "date_display": display_date,
                    "venue": venue if "antalya" in normalize_tr(venue) else f"{venue}, Antalya",
                    "price": price,
                    "image": img_url,
                    "url": url,
                    "source": source_name,
                })
        except Exception:
            continue
    return found


async def scrape_bubilet(context) -> list[dict]:
    """Bubilet Antalya sayfasını kazır."""
    source = "Bubilet"
    target_url = "https://www.bubilet.com.tr/antalya"
    events = []
    print(f"[*] {source} taranıyor: {target_url}")

    page = await context.new_page()
    try:
        await page.goto(target_url, wait_until="networkidle", timeout=35000)
        await page.wait_for_timeout(3000)
        for _ in range(4):
            await page.evaluate("window.scrollBy(0, 1200)")
            await page.wait_for_timeout(800)

        html = await page.content()
        soup = BeautifulSoup(html, "lxml")

        events.extend(extract_json_ld_events(soup, source, target_url))
        seen_urls = {e["url"] for e in events}

        # Bubilet Next.js / Nuxt gömülü state kontrolü
        next_data_script = soup.find("script", id="__NEXT_DATA__")
        if next_data_script and next_data_script.string:
            try:
                nd = json.loads(next_data_script.string)
                raw_json_str = json.dumps(nd, ensure_ascii=False)
                # İçerideki etkinlik nesnelerini yakala
            except Exception:
                pass

        links = soup.select("a[href*='/antalya/etkinlik/'], a[href*='/etkinlik/']")
        for a in links:
            href = a.get("href", "")
            full_url = urljoin(target_url, href)
            if full_url in seen_urls:
                continue

            card_text_parts = [t.strip() for t in a.stripped_strings if t.strip()]
            if not card_text_parts:
                continue

            heading = a.find(["h2", "h3", "h4", "strong"])
            title = heading.get_text(strip=True) if heading else card_text_parts[0]
            if not is_valid_title(title):
                if len(card_text_parts) > 1 and is_valid_title(card_text_parts[1]):
                    title = card_text_parts[1]
                else:
                    continue

            venue = "Antalya"
            date_raw = ""
            price = "Bubilet'te İncele"

            for part in card_text_parts:
                if part == title:
                    continue
                p_norm = normalize_tr(part)
                if any(sym in part for sym in ["₺", "TL", "tl"]) or re.search(r"\d+\s*(?:₺|TL)", part):
                    price = part
                elif any(m in p_norm for m in TURKISH_MONTHS.keys()) or re.search(r"\d{1,2}[./]\d{1,2}", part):
                    if not date_raw:
                        date_raw = part
                elif any(v in p_norm for v in ["sahne", "merkezi", "holly", "jolly", "tiyatro", "pub", "arena", "piramit", "açıkhava", "acikhava", "salon", "otel", "hotel", "kültür", "kultur", "antalya", "alanya", "manavgat", "kaş", "kemer"]):
                    venue = part

            city_hint = "antalya" if "/antalya/" in full_url else ""
            if not is_strictly_antalya(title, venue, full_url, city_hint):
                continue

            img_tag = a.find("img")
            img_url = ""
            if img_tag:
                raw_img = img_tag.get("data-src") or img_tag.get("src") or ""
                img_url = normalize_image_url(raw_img, target_url, source)

            iso_date, display_date = parse_turkish_date(date_raw)
            seen_urls.add(full_url)
            events.append({
                "id": generate_event_id(title, source, full_url),
                "title": title,
                "category": categorize_event(title, venue, full_url),
                "date_iso": iso_date,
                "date_display": display_date,
                "venue": venue if "antalya" in normalize_tr(venue) else f"{venue}, Antalya",
                "price": price,
                "image": img_url,
                "url": full_url,
                "source": source,
            })
    except Exception as exc:
        print(f"[!] {source} hata: {exc}")
    finally:
        await page.close()

    print(f"[+] {source}: {len(events)} Antalya etkinliği bulundu.")
    return events


async def scrape_biletinial(context) -> list[dict]:
    """Biletinial Antalya etkinlik sayfalarını kazır."""
    source = "Biletinial"
    urls = [
        "https://biletinial.com/tr-tr/muzik/antalya",
        "https://biletinial.com/tr-tr/tiyatro/antalya",
        "https://biletinial.com/tr-tr/etkinlik-takvimi/7",
        "https://biletinial.com/tr-tr/sehir/antalya"
    ]
    events = []
    seen_urls = set()
    print(f"[*] {source} taranıyor...")

    page = await context.new_page()
    try:
        for target_url in urls:
            try:
                await page.goto(target_url, wait_until="domcontentloaded", timeout=30000)
                await page.wait_for_timeout(2500)
                await page.evaluate("window.scrollBy(0, 1200)")
                await page.wait_for_timeout(600)

                html = await page.content()
                soup = BeautifulSoup(html, "lxml")

                for ev in extract_json_ld_events(soup, source, target_url):
                    if ev["url"] not in seen_urls:
                        seen_urls.add(ev["url"])
                        events.append(ev)

                cards = soup.select("li, div.card, div.event-item, div[class*='event'], div[class*='etkinlik']")
                for card in cards:
                    a_tag = card.find("a", href=True)
                    if not a_tag:
                        continue
                    href = a_tag["href"]
                    if not any(seg in href for seg in ["/tiyatro/", "/muzik/", "/etkinlik/", "/stand-up/", "/opera-bale/"]):
                        continue
                    if href.rstrip("/").endswith("/antalya"):
                        continue

                    full_url = urljoin("https://biletinial.com", href)
                    if full_url in seen_urls:
                        continue

                    title_el = card.find(["h2", "h3", "h4", "strong"]) or a_tag
                    title = title_el.get_text(strip=True) if title_el else ""
                    if not is_valid_title(title):
                        title = a_tag.get("title", "").strip()
                    if not is_valid_title(title):
                        continue

                    card_text = " | ".join(t.strip() for t in card.stripped_strings if t.strip())
                    venue = "Antalya"
                    date_raw = ""
                    price = "Biletinial'da İncele"

                    for part in card.stripped_strings:
                        p_str = part.strip()
                        p_norm = normalize_tr(p_str)
                        if p_str == title:
                            continue
                        if "₺" in p_str or "tl" in p_norm:
                            price = p_str
                        elif any(v in p_norm for v in [
                            "sahne", "merkezi", "holly", "jolly", "piramit",
                            "açıkhava", "acikhava", "akm", "belediye", "salon",
                            "kültür", "kultur", "alanya", "manavgat", "doğu garajı",
                            "dogu garaji", "haşim işcan", "hasim iscan", "mall of",
                            "türkan şoray", "turkan soray", "nazım hikmet", "antalya bahçe"
                        ]):
                            venue = p_str
                        elif any(m in p_norm for m in TURKISH_MONTHS.keys()) and re.search(r"\d", p_norm):
                            if not date_raw:
                                date_raw = p_str

                    # Biletinial Antalya sayfasından çekildiği için şehir ipucu "antalya",
                    # fakat is_strictly_antalya başka şehir adı varsa yine reddeder.
                    if not is_strictly_antalya(title, f"{venue} {card_text}", full_url, "antalya"):
                        continue

                    img_tag = card.find("img")
                    img_url = ""
                    if img_tag:
                        raw_img = img_tag.get("data-src") or img_tag.get("data-original") or img_tag.get("src") or ""
                        img_url = normalize_image_url(raw_img, "https://biletinial.com", source)

                    iso_date, display_date = parse_turkish_date(date_raw)
                    seen_urls.add(full_url)
                    events.append({
                        "id": generate_event_id(title, source, full_url),
                        "title": title,
                        "category": categorize_event(title, f"{venue} {card_text}", full_url),
                        "date_iso": iso_date,
                        "date_display": display_date,
                        "venue": venue if "antalya" in normalize_tr(venue) else f"{venue}, Antalya",
                        "price": price,
                        "image": img_url,
                        "url": full_url,
                        "source": source,
                    })
            except Exception as inner_e:
                print(f"    [!] {source} alt sayfa uyarısı ({target_url}): {inner_e}")
                continue
    except Exception as exc:
        print(f"[!] {source} genel hata: {exc}")
    finally:
        await page.close()

    print(f"[+] {source}: {len(events)} Antalya etkinliği bulundu.")
    return events


async def scrape_biletix(context) -> list[dict]:
    """Biletix Antalya aramasını kazır (sadece kendi kartında Antalya geçenleri alır)."""
    source = "Biletix"
    target_url = "https://www.biletix.com/search/TURKIYE/tr?city_sb=Antalya#!city_sb:Antalya"
    events = []
    seen_urls = set()
    print(f"[*] {source} taranıyor: {target_url}")

    page = await context.new_page()
    try:
        await page.goto(target_url, wait_until="domcontentloaded", timeout=35000)
        await page.wait_for_timeout(4500)
        await page.evaluate("window.scrollBy(0, 1200)")
        await page.wait_for_timeout(1200)

        html = await page.content()
        soup = BeautifulSoup(html, "lxml")

        for ev in extract_json_ld_events(soup, source, target_url):
            if ev["url"] not in seen_urls:
                seen_urls.add(ev["url"])
                events.append(ev)

        links = soup.select("a[href*='/etkinlik/'], a[href*='/etkinlik-grup/']")
        for a in links:
            href = a.get("href", "")
            if not href or "javascript:" in href:
                continue
            full_url = urljoin("https://www.biletix.com", href)
            if full_url in seen_urls:
                continue

            # Sadece doğrudan kart kapsayıcısını al (tüm liste konteynerini alma!)
            card_strings = [s.strip() for s in a.stripped_strings if s.strip()]
            if not card_strings:
                continue

            title = card_strings[0]
            if not is_valid_title(title):
                continue

            card_own_text = " ".join(card_strings)
            venue = card_strings[1] if len(card_strings) > 1 else "Antalya"
            date_raw = card_strings[2] if len(card_strings) > 2 else card_own_text

            # Biletix'te kendi kart metni içinde açıkça Antalya/ilçesi geçmeli
            if not is_strictly_antalya(title, card_own_text, full_url, ""):
                continue

            img_tag = a.find("img")
            img_url = ""
            if img_tag:
                raw_img = img_tag.get("data-src") or img_tag.get("src") or ""
                img_url = normalize_image_url(raw_img, "https://www.biletix.com", source)

            iso_date, display_date = parse_turkish_date(date_raw)
            seen_urls.add(full_url)
            events.append({
                "id": generate_event_id(title, source, full_url),
                "title": title,
                "category": categorize_event(title, card_own_text, full_url),
                "date_iso": iso_date,
                "date_display": display_date,
                "venue": venue if "antalya" in normalize_tr(venue) else f"{venue}, Antalya",
                "price": "Biletix'te İncele",
                "image": img_url,
                "url": full_url,
                "source": source,
            })
    except Exception as exc:
        print(f"[!] {source} hata: {exc}")
    finally:
        await page.close()

    print(f"[+] {source}: {len(events)} Antalya etkinliği bulundu.")
    return events


async def scrape_passo(context) -> list[dict]:
    """Passo etkinliklerini (API yanıtlarını ve DOM'u dinleyerek) kazır."""
    source = "Passo"
    target_url = "https://www.passo.com.tr/tr/kategori/muzik-konser-festival-biletleri/8615"
    events = []
    seen_urls = set()
    captured_json_items = []
    print(f"[*] {source} taranıyor...")

    page = await context.new_page()

    async def handle_response(response):
        try:
            if "json" in (response.headers.get("content-type") or "").lower():
                if any(k in response.url.lower() for k in ["event", "etkinlik", "search", "list"]):
                    data = await response.json()
                    if isinstance(data, dict):
                        for key in ["valueList", "items", "data", "result", "events"]:
                            if isinstance(data.get(key), list):
                                captured_json_items.extend(data[key])
                    elif isinstance(data, list):
                        captured_json_items.extend(data)
        except Exception:
            pass

    page.on("response", handle_response)

    try:
        await page.goto(target_url, wait_until="domcontentloaded", timeout=35000)
        await page.wait_for_timeout(4000)

        for item in captured_json_items:
            if not isinstance(item, dict):
                continue
            title = (item.get("name") or item.get("title") or item.get("eventName") or "").strip()
            if not is_valid_title(title):
                continue

            venue = (item.get("venueName") or item.get("location") or item.get("place") or "").strip()
            city = (item.get("cityName") or item.get("city") or "").strip()
            seo_url = item.get("seoUrl") or item.get("url") or item.get("slug") or ""
            event_id = item.get("id") or ""

            if not is_strictly_antalya(title, venue, str(seo_url), city):
                continue

            full_url = f"https://www.passo.com.tr/tr/etkinlik/{seo_url}/{event_id}" if seo_url and event_id else "https://www.passo.com.tr/tr"
            if full_url in seen_urls:
                continue

            date_raw = str(item.get("date") or item.get("startDate") or item.get("eventDate") or "")
            iso_date, display_date = parse_turkish_date(date_raw)

            raw_img = item.get("imageUrl") or item.get("homePageImagePath") or ""
            img_url = normalize_image_url(str(raw_img), "https://www.passo.com.tr", source)

            seen_urls.add(full_url)
            events.append({
                "id": generate_event_id(title, source, full_url),
                "title": title,
                "category": categorize_event(title, venue, full_url),
                "date_iso": iso_date,
                "date_display": display_date,
                "venue": venue if "antalya" in normalize_tr(venue) else f"{venue}, Antalya",
                "price": "Passo'da İncele",
                "image": img_url,
                "url": full_url,
                "source": source,
            })
    except Exception as exc:
        print(f"[!] {source} hata: {exc}")
    finally:
        await page.close()

    print(f"[+] {source}: {len(events)} Antalya etkinliği bulundu.")
    return events


# ============================================================================
# 4. YEDEK (FALLBACK) MEKAN & PLATFORM VERİLERİ
# ============================================================================

def get_curated_antalya_venues_fallback() -> list[dict]:
    now = datetime.now()
    sample_items = [
        {
            "title": "Antalya Açıkhava Tiyatrosu Konser & Sahne Takvimi",
            "venue": "Antalya Açıkhava Tiyatrosu, Konyaaltı / Antalya",
            "price": "Resmi Satış Sayfası",
            "url": "https://www.bubilet.com.tr/antalya",
            "source": "Bubilet",
            "days_ahead": 2,
            "hour": 21,
            "image": "https://images.unsplash.com/photo-1514525253161-7a46d19cd819?auto=format&fit=crop&w=800&q=80"
        },
        {
            "title": "Holly Stone Performance Hall Antalya Canlı Konserler",
            "venue": "Holly Stone Performance Hall, Kaleiçi / Antalya",
            "price": "Bilet Al",
            "url": "https://www.bubilet.com.tr/antalya",
            "source": "Bubilet",
            "days_ahead": 3,
            "hour": 22,
            "image": "https://images.unsplash.com/photo-1470229722913-7c0e2dbbafd3?auto=format&fit=crop&w=800&q=80"
        },
        {
            "title": "Antalya Devlet Tiyatrosu & Haşim İşcan Kültür Merkezi Oyunları",
            "venue": "Haşim İşcan Kültür Merkezi, Muratpaşa / Antalya",
            "price": "Biletinial'da İncele",
            "url": "https://biletinial.com/tr-tr/tiyatro/antalya",
            "source": "Biletinial",
            "days_ahead": 4,
            "hour": 20,
            "image": "https://images.unsplash.com/photo-1507676184212-d03ab07a01bf?auto=format&fit=crop&w=800&q=80"
        }
    ]
    results = []
    for item in sample_items:
        dt = (now + timedelta(days=item["days_ahead"])).replace(hour=item["hour"], minute=0, second=0)
        iso_date, disp_date = parse_turkish_date(dt.strftime("%Y-%m-%dT%H:%M:%S"))
        results.append({
            "id": generate_event_id(item["title"], item["source"], item["url"]),
            "title": item["title"],
            "category": categorize_event(item["title"], item["venue"], item["url"]),
            "date_iso": iso_date,
            "date_display": disp_date,
            "venue": item["venue"],
            "price": item["price"],
            "image": item["image"],
            "url": item["url"],
            "source": item["source"],
        })
    return results


# ============================================================================
# 5. ÇIKTI ÜRETİCİLERİ (events.json ve antalya_etkinlikler.ics)
# ============================================================================

def deduplicate_and_sort(events: list[dict]) -> list[dict]:
    """Tekrarlayan etkinlikleri temizler, başlıkları kontrol eder ve tarihe göre sıralar."""
    unique = {}
    for ev in events:
        title = ev.get("title", "")
        venue = ev.get("venue", "")
        url = ev.get("url", "")
        if not is_valid_title(title):
            continue
        if not is_strictly_antalya(title, venue, url, ""):
            continue
        key = normalize_ascii(f"{title}-{ev.get('date_iso', '')[:10]}-{ev.get('source', '')}")
        if key not in unique:
            unique[key] = ev

    return sorted(unique.values(), key=lambda x: x.get("date_iso", "9999-12-31"))


def generate_ics_file(events: list[dict], filepath: str) -> None:
    """Standart RFC 5545 formatında iCalendar (.ics) takvim dosyası üretir."""
    now_utc = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//Antalya Etkinlik Takvimi ve Firsat Paneli//TR",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        "X-WR-CALNAME:Antalya Etkinlik Takvimi",
        "X-WR-TIMEZONE:Europe/Istanbul",
    ]

    for ev in events:
        try:
            dt_start = datetime.strptime(ev["date_iso"][:19], "%Y-%m-%dT%H:%M:%S")
        except Exception:
            dt_start = datetime.now() + timedelta(days=3)
        dt_end = dt_start + timedelta(hours=2, minutes=30)

        uid = f"{ev['id']}@antalya-etkinlik-takvimi"
        summary = ev["title"].replace(",", r"\,").replace(";", r"\;").replace("\n", " ")
        location = ev["venue"].replace(",", r"\,").replace(";", r"\;").replace("\n", " ")
        desc = (
            f"Kategori: {ev['category']}\\n"
            f"Kaynak: {ev['source']}\\n"
            f"Fiyat/Durum: {ev['price']}\\n"
            f"Bilet ve Detaylar: {ev['url']}"
        )

        lines.extend([
            "BEGIN:VEVENT",
            f"UID:{uid}",
            f"DTSTAMP:{now_utc}",
            f"DTSTART;TZID=Europe/Istanbul:{dt_start.strftime('%Y%m%dT%H%M%S')}",
            f"DTEND;TZID=Europe/Istanbul:{dt_end.strftime('%Y%m%dT%H%M%S')}",
            f"SUMMARY:{summary}",
            f"LOCATION:{location}",
            f"DESCRIPTION:{desc}",
            f"URL:{ev['url']}",
            "END:VEVENT"
        ])

    lines.append("END:VCALENDAR")
    with open(filepath, "w", encoding="utf-8", newline="\r\n") as f:
        f.write("\r\n".join(lines) + "\r\n")


async def main():
    print("=" * 68)
    print(" ANTALYA ETKİNLİK TAKVİMİ VE FIRSAT PANELİ - VERİ KAZIYICI BAŞLADI")
    print("=" * 68)

    all_events = []

    if async_playwright is not None and BeautifulSoup is not None:
        async with async_playwright() as p:
            browser = await p.chromium.launch(
                headless=True,
                args=[
                    "--disable-blink-features=AutomationControlled",
                    "--no-sandbox",
                    "--disable-setuid-sandbox",
                    "--disable-dev-shm-usage",
                ]
            )
            context = await browser.new_context(
                locale="tr-TR",
                timezone_id="Europe/Istanbul",
                viewport={"width": 1440, "height": 900},
                user_agent=(
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/130.0.0.0 Safari/537.36"
                ),
                extra_http_headers={
                    "Accept-Language": "tr-TR,tr;q=0.9,en-US;q=0.8,en;q=0.7",
                }
            )

            await context.add_init_script(
                "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});"
            )

            results = await asyncio.gather(
                scrape_bubilet(context),
                scrape_biletinial(context),
                scrape_biletix(context),
                scrape_passo(context),
                return_exceptions=True
            )

            for res in results:
                if isinstance(res, list):
                    all_events.extend(res)

            await browser.close()
    else:
        print("[!] Playwright veya BeautifulSoup kurulu değil.")

    cleaned_events = deduplicate_and_sort(all_events)

    base_dir = os.path.dirname(os.path.abspath(__file__))
    json_path = os.path.join(base_dir, "events.json")
    ics_path = os.path.join(base_dir, "antalya_etkinlikler.ics")

    if not cleaned_events:
        if os.path.exists(json_path):
            try:
                with open(json_path, "r", encoding="utf-8") as f:
                    old_data = json.load(f)
                    if old_data.get("events"):
                        cleaned_events = deduplicate_and_sort(old_data["events"])
            except Exception:
                pass
        if not cleaned_events:
            cleaned_events = get_curated_antalya_venues_fallback()

    # Türkiye saatiyle (UTC+3) son güncelleme zamanı
    tr_now = datetime.now(timezone(timedelta(hours=3)))
    payload = {
        "city": "Antalya",
        "updated_at": tr_now.strftime("%d.%m.%Y %H:%M"),
        "total_count": len(cleaned_events),
        "categories": sorted(list({e["category"] for e in cleaned_events})),
        "sources": sorted(list({e["source"] for e in cleaned_events})),
        "events": cleaned_events,
    }

    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)

    generate_ics_file(cleaned_events, ics_path)

    print("=" * 68)
    print(f"[✓] Tamamlandı! Toplam {len(cleaned_events)} saf Antalya etkinliği kaydedildi.")
    print(f"    -> JSON: {json_path}")
    print(f"    -> ICS : {ics_path}")
    print("=" * 68)


if __name__ == "__main__":
    asyncio.run(main())
