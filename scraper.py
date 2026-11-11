#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Antalya Etkinlik Takvimi ve Fırsat Paneli - Otomatik Veri Kazıyıcı (Scraper)
Hedef Kaynaklar: Bubilet (RSC Stream + Venue/Tag Tarama), Biletix (Solr API), Biletinial, Passo
Çıktılar: events.json, antalya_etkinlikler.ics
"""

import asyncio
import hashlib
import json
import os
import re
import urllib.parse
import urllib.request
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

ANTALYA_KEYWORDS = [
    "antalya", "alanya", "manavgat", "kemer", "kaş", "kalkan",
    "serik", "belek", "muratpaşa", "muratpasa", "konyaaltı", "konyaalti",
    "kepez", "döşemealtı", "dosemealti", "aksu", "kumluca", "finike",
    "demre", "gazipaşa", "gazipasa", "korkuteli", "elmalı", "elmali",
    "kaleiçi", "kaleici", "side", "aspendos", "olympos", "olimpos",
    "holly stone antalya", "alanya holly stone", "jolly joker antalya",
    "antalya açıkhava", "antalya acikhava", "cam piramit", "antalya akm",
    "türkan şoray", "turkan soray", "haşim işcan", "hasim iscan",
    "doğu garajı", "dogu garaji", "mall of antalya", "sponge pub",
    "the sponge", "the bar kaleiçi", "the bar kaleici", "the land of legends",
    "akdeniz üniversitesi", "ibrahim sözen", "ibrahim sozen", "özgün sahne",
    "ozgun sahne", "dada tiyatro", "rockbull", "aktüel sahne", "aktuel sahne",
    "mimar sinan kongre", "feslikan", "delicious bekir"
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
    "bostanci", "congresium", "cso ada", "odtü", "odtu", "bilkent", "cermodern"
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

MONTH_NAMES_TR = [
    "", "Ocak", "Şubat", "Mart", "Nisan", "Mayıs", "Haziran",
    "Temmuz", "Ağustos", "Eylül", "Ekim", "Kasım", "Aralık"
]

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

    # 2) En az bir Antalya anahtar kelimesi eşleşmeli
    for kw in ANTALYA_KEYWORDS:
        kw_norm = normalize_tr(kw)
        pattern = rf"\b{re.escape(kw_norm)}\b"
        if re.search(pattern, norm_text) or re.search(pattern, ascii_text):
            return True

    return False


def categorize_event(title: str, description: str = "", url: str = "") -> str:
    """
    Etkinlik başlığı ve metnindeki anahtar kelimelere göre otomatik kategori atar.
    """
    full_norm = normalize_tr(f"{title} {description} {url}")

    # 1. Stand-Up & Komedi
    standup_keywords = [
        "stand-up", "stand up", "standup", "stand_up", "komedi", "güldürü", "gulduru",
        "açık mikrofon", "acik mikrofon", "open mic", "talk show", "mizah",
        "cmylmz", "baturay", "doğu demirkol", "dogu demirkol", "yasemin sakallıoğlu",
        "gökhan ünver", "gokhan unver", "mesut süre", "mesut sure", "ilişki testi",
        "iliski testi", "meksika açmazı", "meksika acmazi", "kalt", "tuzbiber",
        "ilker gümüşoluk", "ilker gumusoluk", "sergen deveci", "salih tıraş",
        "salih tiras", "mahşer-i cümbüş", "mahseri cumbus", "doğaçlama", "dogaclama",
        "bengi idil uras", "miray akovalıgil", "miray akovaligil", "yoshi enomoto",
        "sefa doğanay", "sefa doganay", "ali congun", "tahsin hasoğlu", "tahsin hasoglu",
        "özgür turhan", "ozgur turhan", "deniz göktaş", "deniz goktas", "sedat şahin"
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
        "kubik prens", "hacivat", "karagöz", "karagoz", "kırmızı cüce", "kirmizi cuce",
        "pinokyo", "arı maya", "ari maya", "iyilik takımı", "farklıyız ama arkadaşız",
        "family", "attractioncenter"
    ]
    for kw in workshop_kids_keywords:
        if kw in full_norm:
            return "Atölye & Çocuk"

    # 3. Festival & Parti
    party_keywords = [
        "festival", "fest", "parti", "party", "dj", "after party", "afterparty",
        "rave", "elektronik", "techno", "house music", "90lar", "90'lar", "90’lar",
        "80ler", "80'ler", "80’ler", "2000ler", "2000'ler", "retro", "karnaval",
        "dans gecesi", "karaoke", "pop delisi", "disconset"
    ]
    for kw in party_keywords:
        if kw in full_norm:
            return "Festival & Parti"

    # 4. Konser & Müzik
    concert_keywords = [
        "konser", "akustik", "live", "canlı", "canli", "orkestra", "senfoni",
        "caz", "jazz", "rock", "pop", "rap", "hiphop", "alternatif", "türkü",
        "turku", "arabesk", "resital", "tribute", "sympho", "candle", "candles",
        "şarkılar", "sarkilar", "holly stone", "jolly joker", "the bar", "sponge",
        "/muzik/", "music", "tour"
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


def parse_utc_or_turkish_date(date_str: str) -> tuple[str, str]:
    """
    UTC ISO tarihlerini ("2026-11-14T19:00:00+00:00" veya "...Z") Türkiye saatine (UTC+3)
    çevirir; ayrıca Türkçe tarih metinlerini ayrıştırır.
    """
    now = datetime.now()
    if not date_str:
        iso = (now + timedelta(days=3)).strftime("%Y-%m-%dT20:00:00")
        return iso, "Tarih İçin Sayfaya Bakınız"

    clean_str = " ".join(str(date_str).strip().split())

    # 1) UTC (+00:00 veya Z) kontrolü -> Türkiye saati için +3 saat ekle
    utc_match = re.search(
        r"(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})(?::(\d{2}))?(?:\.\d+)?(Z|\+00:00)",
        clean_str
    )
    if utc_match:
        y, m, d = int(utc_match.group(1)), int(utc_match.group(2)), int(utc_match.group(3))
        hh, mm = int(utc_match.group(4)), int(utc_match.group(5))
        try:
            dt_utc = datetime(y, m, d, hh, mm)
            dt_tr = dt_utc + timedelta(hours=3)
            disp = f"{dt_tr.day} {MONTH_NAMES_TR[dt_tr.month]} {dt_tr.year} • {dt_tr.hour:02d}:{dt_tr.minute:02d}"
            return dt_tr.strftime("%Y-%m-%dT%H:%M:%S"), disp
        except ValueError:
            pass

    # 2) Yerel ISO format kontrolü (örn: 2026-11-15T20:00:00)
    iso_match = re.search(r"(\d{4})-(\d{2})-(\d{2})(?:[T\s](\d{2}):(\d{2}))?", clean_str)
    if iso_match:
        y, m, d = int(iso_match.group(1)), int(iso_match.group(2)), int(iso_match.group(3))
        hh = int(iso_match.group(4)) if iso_match.group(4) else 20
        mm = int(iso_match.group(5)) if iso_match.group(5) else 0
        try:
            dt = datetime(y, m, d, hh, mm)
            disp = f"{d} {MONTH_NAMES_TR[m]} {y} • {hh:02d}:{mm:02d}"
            return dt.strftime("%Y-%m-%dT%H:%M:%S"), disp
        except ValueError:
            pass

    # 3) DD.MM.YYYY veya DD/MM/YYYY
    dot_match = re.search(r"(\d{1,2})[./](\d{1,2})[./](\d{4})(?:\s+(\d{1,2}):(\d{2}))?", clean_str)
    if dot_match:
        d, m, y = int(dot_match.group(1)), int(dot_match.group(2)), int(dot_match.group(3))
        hh = int(dot_match.group(4)) if dot_match.group(4) else 20
        mm = int(dot_match.group(5)) if dot_match.group(5) else 0
        try:
            dt = datetime(y, m, d, hh, mm)
            disp = f"{d} {MONTH_NAMES_TR[m]} {y} • {hh:02d}:{mm:02d}"
            return dt.strftime("%Y-%m-%dT%H:%M:%S"), disp
        except ValueError:
            pass

    # 4) Türkçe Ay İsimli Format
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
                disp = f"{day} {MONTH_NAMES_TR[m_num]} {year} • {hh:02d}:{mm:02d}"
                return dt.strftime("%Y-%m-%dT%H:%M:%S"), disp
            except ValueError:
                continue

    fallback_iso = (now + timedelta(days=5)).strftime("%Y-%m-%dT20:00:00")
    return fallback_iso, "Tarih İçin Sayfaya Bakınız"


def generate_event_id(title: str, source: str, url: str) -> str:
    raw = f"{normalize_ascii(title)}-{source}-{url}"
    return hashlib.md5(raw.encode("utf-8")).hexdigest()[:12]


def is_valid_title(title: str) -> bool:
    if not title or len(title.strip()) < 3:
        return False
    bad_titles = [
        "detaylı incele", "detayli incele", "bilet al", "incele",
        "detaylar", "tümü", "tumu", "satın al", "satin al", "etkinlikler"
    ]
    return normalize_tr(title) not in bad_titles


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
# 3. KAYNAK 1: BUBİLET (RSC STREAM + ANTALYA MEKAN & KATEGORİ TARAMASI)
# ============================================================================

BUBILET_ANTALYA_URLS = [
    "https://www.bubilet.com.tr/antalya",
    "https://www.bubilet.com.tr/antalya/etiket/konser",
    "https://www.bubilet.com.tr/antalya/etiket/tiyatro",
    "https://www.bubilet.com.tr/antalya/etiket/stand-up",
    "https://www.bubilet.com.tr/antalya/etiket/festival",
    "https://www.bubilet.com.tr/antalya/etiket/cocuk-aktiviteleri",
    "https://www.bubilet.com.tr/antalya/etiket/eglence",
    "https://www.bubilet.com.tr/antalya/etiket/elektronik-muzik",
    "https://www.bubilet.com.tr/mekan/holly-stone-performance-hall-antalya",
    "https://www.bubilet.com.tr/mekan/the-bar-kaleici",
    "https://www.bubilet.com.tr/mekan/turkan-soray-kultur-merkezi-antalya",
    "https://www.bubilet.com.tr/mekan/konyaalti-belediyesi-nazim-hikmet-fuar-ve-kongre-merkezi",
    "https://www.bubilet.com.tr/mekan/ataturk-kultur-merkezi-aspendos-salonu",
    "https://www.bubilet.com.tr/mekan/antalya-acikhava",
    "https://www.bubilet.com.tr/mekan/holly-stone-performance-hall-alanya",
    "https://www.bubilet.com.tr/mekan/sponge-pub-konyaalti",
    "https://www.bubilet.com.tr/mekan/antalya-nazim-hikmet-fuar-ve-kongre-merkezi",
    "https://www.bubilet.com.tr/mekan/dada-tiyatro",
    "https://www.bubilet.com.tr/mekan/muratpasa-belediyesi-kultur-salonu",
    "https://www.bubilet.com.tr/mekan/alanya-kultur-merkezi-a-salonu",
    "https://www.bubilet.com.tr/mekan/delicious-bekir-lara",
    "https://www.bubilet.com.tr/mekan/alanya-acikhava-tiyatrosu",
    "https://www.bubilet.com.tr/mekan/aktuel-acikhava-tiyatrosu"
]


def fetch_bubilet_sync() -> list[dict]:
    """
    Bubilet Next.js App Router RSC payload'larını (self.__next_f.push) birleştirip
    tüm Antalya etkinlik nesnelerini doğrudan JSON olarak çözer.
    """
    source = "Bubilet"
    print(f"[*] {source} Antalya kategori ve mekan sayfaları taranıyor...")
    raw_objects = {}
    decoder = json.JSONDecoder()

    headers = {
        "User-Agent": "Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "tr-TR,tr;q=0.9",
    }

    for url in BUBILET_ANTALYA_URLS:
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=15) as resp:
                html = resp.read().decode("utf-8", errors="ignore")

            chunks = re.findall(
                r'self\.__next_f\.push\(\[1,"(.*?)"\]\)</script>', html, re.DOTALL
            )
            if not chunks:
                continue

            stream = "".join(json.loads('"' + c + '"') for c in chunks)
            pos = 0
            while True:
                m = re.search(r'\{"id":\d+,"name":"', stream[pos:])
                if not m:
                    break
                start = pos + m.start()
                try:
                    obj, end = decoder.raw_decode(stream, start)
                    if (
                        isinstance(obj, dict)
                        and "slug" in obj
                        and ("dates" in obj or "sessions" in obj)
                    ):
                        slug = obj["slug"]
                        # Daha zengin verisi olan nesneyi tercih et
                        if slug not in raw_objects or ("price" in obj and "price" not in raw_objects[slug]):
                            raw_objects[slug] = obj
                    pos = end
                except Exception:
                    pos = start + 10
        except Exception as exc:
            print(f"    [!] Bubilet alt sayfa uyarısı ({url}): {exc}")
            continue

    events = []
    for slug, d in raw_objects.items():
        title = (d.get("name") or "").strip()
        if not is_valid_title(title):
            continue

        # Mekan bilgisi
        venue = "Antalya"
        if isinstance(d.get("venues"), list) and d["venues"]:
            v_first = d["venues"][0]
            if isinstance(v_first, dict) and v_first.get("name"):
                venue = v_first["name"].strip()
        elif isinstance(d.get("sessions"), list) and d["sessions"]:
            s_first = d["sessions"][0]
            if isinstance(s_first, dict) and s_first.get("venueName"):
                venue = s_first["venueName"].strip()

        # Tarih bilgisi
        date_raw = ""
        if isinstance(d.get("dates"), list) and d["dates"]:
            date_raw = d["dates"][0]
        elif isinstance(d.get("sessions"), list) and d["sessions"]:
            s_first = d["sessions"][0]
            if isinstance(s_first, dict) and s_first.get("date"):
                date_raw = s_first["date"]

        iso_date, display_date = parse_utc_or_turkish_date(date_raw)

        # Fiyat bilgisi
        price_val = d.get("discountedPrice") or d.get("price")
        if not price_val and isinstance(d.get("sessions"), list) and d["sessions"]:
            s_first = d["sessions"][0]
            if isinstance(s_first, dict):
                price_val = s_first.get("minDiscountedPrice") or s_first.get("minPrice")

        price_str = f"₺{price_val}" if price_val else "Bubilet'te İncele"

        # Görsel URL
        img_url = ""
        if isinstance(d.get("files"), list) and d["files"]:
            f_path = d["files"][0].get("url") or ""
            if f_path:
                img_url = f"https://cdn.bubilet.com.tr/cdn-cgi/image/width=750,quality=80,format=auto{f_path}"

        full_url = f"https://www.bubilet.com.tr/antalya/etkinlik/{slug}"

        if not is_strictly_antalya(title, venue, full_url, "antalya"):
            continue

        events.append({
            "id": generate_event_id(title, source, full_url),
            "title": title,
            "category": categorize_event(title, venue, full_url),
            "date_iso": iso_date,
            "date_display": display_date,
            "venue": venue if "antalya" in normalize_tr(venue) else f"{venue}, Antalya",
            "price": price_str,
            "image": img_url,
            "url": full_url,
            "source": source,
        })

    print(f"[+] {source}: {len(events)} Antalya etkinliği bulundu.")
    return events


# ============================================================================
# 4. KAYNAK 2: BİLETİX (SOLR JSON API DOĞRUDAN SORGUSU)
# ============================================================================

def fetch_biletix_solr_sync() -> list[dict]:
    """
    Biletix'in resmi Solr arama uç noktasından city:"Antalya" filtresiyle
    tüm Antalya etkinliklerini doğrudan JSON olarak çeker.
    """
    source = "Biletix"
    print(f"[*] {source} Solr API üzerinden Antalya etkinlikleri çekiliyor...")
    now_utc = datetime.now(timezone.utc)
    start_str = now_utc.strftime("%Y-%m-%dT00:00:00Z")
    end_str = (now_utc + timedelta(days=365)).strftime("%Y-%m-%dT00:00:00Z")

    query_params = (
        f"start=0&rows=200&q=*:*&fq=end%3A%5B{ urllib.parse.quote(start_str) }"
        f"%20TO%20{ urllib.parse.quote(end_str) }%5D&sort=start%20asc&wt=json"
        f"&fq=city:%22Antalya%22"
    )
    api_url = f"https://www.biletix.com/solr/tr/select/?{query_params}"

    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
        "Accept": "application/json, text/plain, */*",
        "Referer": "https://www.biletix.com/search/TURKIYE/tr",
    }

    events = []
    try:
        req = urllib.request.Request(api_url, headers=headers)
        with urllib.request.urlopen(req, timeout=20) as resp:
            data = json.loads(resp.read().decode("utf-8", errors="ignore"))

        docs = data.get("response", {}).get("docs", [])
        for doc in docs:
            doc_id = doc.get("id")
            doc_type = doc.get("type", "event")
            title = (doc.get("sname") or "").strip()
            if not title and isinstance(doc.get("name"), list) and doc["name"]:
                title = str(doc["name"][0]).strip()

            if not is_valid_title(title) or not doc_id:
                continue

            venue = (doc.get("svenue") or "Antalya").strip()
            cities = doc.get("city") or []
            if isinstance(cities, list) and "Antalya" not in cities:
                continue

            start_val = doc.get("start") or ""
            # Eğer başlangıç tarihi geçmişte olan uzun süreli bir grup/müze etkinliğiyse bugünün tarihine çek
            iso_date, display_date = parse_utc_or_turkish_date(start_val)
            try:
                dt_parsed = datetime.strptime(iso_date[:19], "%Y-%m-%dT%H:%M:%S")
                if dt_parsed < datetime.now() - timedelta(days=1):
                    tomorrow = (datetime.now() + timedelta(days=1)).replace(hour=19, minute=0, second=0)
                    iso_date, display_date = parse_utc_or_turkish_date(tomorrow.strftime("%Y-%m-%dT%H:%M:%S"))
            except Exception:
                pass

            if doc_type == "group":
                full_url = f"https://www.biletix.com/etkinlik-grup/{doc_id}/TURKIYE/tr"
            else:
                full_url = f"https://www.biletix.com/etkinlik/{doc_id}/TURKIYE/tr"

            img_file = doc.get("image_url") or ""
            img_url = f"https://www.biletix.com/static/images/live/event/eventimages/{img_file}" if img_file else ""

            cat_hint = f"{doc.get('category', '')} {doc.get('subcategory', '')} {venue}"
            if not is_strictly_antalya(title, venue, full_url, "Antalya"):
                continue

            events.append({
                "id": generate_event_id(title, source, full_url),
                "title": title,
                "category": categorize_event(title, cat_hint, full_url),
                "date_iso": iso_date,
                "date_display": display_date,
                "venue": venue if "antalya" in normalize_tr(venue) else f"{venue}, Antalya",
                "price": "Biletix'te İncele",
                "image": img_url,
                "url": full_url,
                "source": source,
            })
    except Exception as exc:
        print(f"[!] {source} Solr API hata: {exc}")

    print(f"[+] {source}: {len(events)} Antalya etkinliği bulundu.")
    return events


# ============================================================================
# 5. KAYNAK 3 & 4: BİLETİNİAL VE PASSO (PLAYWRIGHT)
# ============================================================================

def extract_json_ld_events(soup: BeautifulSoup, source_name: str, base_url: str) -> list[dict]:
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

                url = urljoin(base_url, item.get("url") or base_url)
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
                iso_date, display_date = parse_utc_or_turkish_date(start_date)

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

                    if not is_strictly_antalya(title, f"{venue} {card_text}", full_url, "antalya"):
                        continue

                    img_tag = card.find("img")
                    img_url = ""
                    if img_tag:
                        raw_img = img_tag.get("data-src") or img_tag.get("data-original") or img_tag.get("src") or ""
                        img_url = normalize_image_url(raw_img, "https://biletinial.com", source)

                    iso_date, display_date = parse_utc_or_turkish_date(date_raw)
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


async def scrape_passo(context) -> list[dict]:
    """Passo etkinliklerini kazır."""
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
            iso_date, display_date = parse_utc_or_turkish_date(date_raw)

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
# 6. ÇIKTI ÜRETİCİLERİ (events.json ve antalya_etkinlikler.ics)
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

    base_dir = os.path.dirname(os.path.abspath(__file__))
    json_path = os.path.join(base_dir, "events.json")
    ics_path = os.path.join(base_dir, "antalya_etkinlikler.ics")

    all_events = []

    # 1) Bubilet (RSC Stream Parser) ve Biletix (Solr API) - Hızlı ve doğrudan JSON
    loop = asyncio.get_running_loop()
    bubilet_events, biletix_events = await asyncio.gather(
        loop.run_in_executor(None, fetch_bubilet_sync),
        loop.run_in_executor(None, fetch_biletix_solr_sync),
    )
    all_events.extend(bubilet_events)
    all_events.extend(biletix_events)

    # 2) Biletinial ve Passo (Playwright varsa canlı kazı)
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
                scrape_biletinial(context),
                scrape_passo(context),
                return_exceptions=True
            )

            for res in results:
                if isinstance(res, list):
                    all_events.extend(res)

            await browser.close()

    # 3) Herhangi bir kaynak (ör. Bubilet veya Biletinial) veri merkezi IP engeli (403)
    # nedeniyle 0 etkinlik döndürürse, mevcut events.json içindeki o kaynağa ait
    # gelecek tarihli etkinlikleri otomatik olarak koru (asla silme!):
    active_sources = {ev.get("source") for ev in all_events}
    if os.path.exists(json_path):
        try:
            with open(json_path, "r", encoding="utf-8") as f:
                old_data = json.load(f)
                today_prefix = datetime.now().strftime("%Y-%m-%d")
                for src in ["Bubilet", "Biletix", "Biletinial", "Passo"]:
                    if src not in active_sources:
                        kept = [
                            ev for ev in old_data.get("events", [])
                            if ev.get("source") == src and ev.get("date_iso", "9999") >= today_prefix
                        ]
                        if kept:
                            print(f"[*] {src} canlı sorguda 0 döndü (Cloudflare/IP Koruması); mevcut {len(kept)} {src} etkinliği korundu.")
                            all_events.extend(kept)
        except Exception as exc:
            print(f"[!] Mevcut events.json okuma uyarısı: {exc}")

    cleaned_events = deduplicate_and_sort(all_events)

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
    for src in payload["sources"]:
        cnt = sum(1 for e in cleaned_events if e["source"] == src)
        print(f"    - {src}: {cnt} etkinlik")
    print("=" * 68)


if __name__ == "__main__":
    asyncio.run(main())
