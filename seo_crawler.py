"""
Senior-Level Technical SEO Crawler
====================================
Just give a website URL — the crawler auto-discovers the sitemap,
audit every page, and sends results to n8n for Google Sheets.

Usage:
    python seo_crawler.py https://example.com
"""

import sys
import io

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

import re
import json
import time
import threading
import warnings
import requests
from concurrent.futures import ThreadPoolExecutor, as_completed
from bs4 import BeautifulSoup, XMLParsedAsHTMLWarning
from urllib.parse import urljoin, urlparse

from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

warnings.filterwarnings("ignore", category=XMLParsedAsHTMLWarning)

# ── Config ─────────────────────────────────────────────────────────────────────
if len(sys.argv) < 2:
    print("Usage: python seo_crawler.py https://example.com")
    sys.exit(1)

# Normalise the input URL — always treat it as the site root
INPUT_URL    = sys.argv[1].rstrip("/")
parsed_base  = urlparse(INPUT_URL)
BASE_URL     = f"{parsed_base.scheme}://{parsed_base.netloc}"
CLIENT_DOMAIN = parsed_base.netloc.replace("www.", "")

# Use the production webhook URL as requested
WEBHOOK_URL = "https://n8n.srv891967.hstgr.cloud/webhook/fd1f9be6-432c-450e-bbc9-5849dbe5d7c5"
HEADERS     = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/605.1.15 (KHTML, like Gecko) "
        "Version/17.4 Safari/605.1.15"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}

# Create a robust, persistent HTTP session with automatic retries
session = requests.Session()
retry_strategy = Retry(
    total=3,
    backoff_factor=1.5,
    status_forcelist=[500, 502, 503, 504],
    raise_on_status=False
)
adapter = HTTPAdapter(max_retries=retry_strategy)
session.mount("http://", adapter)
session.mount("https://", adapter)
session.headers.update(HEADERS)

THIRD_PARTY_DOMAINS = (
    "facebook.com", "fb.com", "instagram.com", "linkedin.com",
    "twitter.com", "x.com", "youtube.com", "youtu.be",
    "pinterest.com", "tiktok.com", "whatsapp.com", "telegram.org",
    "t.me", "play.google.com", "apps.apple.com", "github.com",
    "google.com", "apple.com"
)

SOFTWARE_MEDIA_EXTS = (
    ".pdf", ".zip", ".rar", ".7z", ".tar", ".gz",
    ".exe", ".apk", ".dmg", ".msi", ".deb", ".bin",
    ".jpg", ".jpeg", ".png", ".gif", ".webp", ".svg", ".ico",
    ".mp4", ".mp3", ".avi", ".mov", ".wav",
    ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx", ".csv"
)

EXCLUDE_PATH_PATTERNS = [
    # Page builder templates (Elementor, ElementsKit, Divi, etc.)
    "elementskit_template", "elementor_library", "elementor-hf",
    "ae_global_templates", "/template/", "/templates/",

    # WordPress author archive pages
    "/author/",

    # WordPress taxonomy archive pages (standard URL structures)
    "/tag/", "/category/", "/page/",

    # WordPress system & feed pages
    "/feed/", "/wp-json", "/wp-sitemap",

    # Utility / account / form pages (universal)
    "/thank-you", "/thanks", "/newsletter", "/cart", "/checkout",
    "/my-account", "/login", "/register", "/wp-login.php", "/wp-admin",
    "/feed", "/rss", "/trackback", "/xmlrpc.php",

    # Legal pages (not SEO content)
    "/privacy-policy", "/terms-of-cookies", "/terms-of-service", "/terms-and-conditions",
    "/cookie-policy", "/disclaimer", "/sitemap",

    # Test / placeholder pages (should be deleted from any site)
    "/sample", "/test", "/hello-world", "/untitled",
]

# ── WordPress Sitemap Sub-file Patterns to SKIP ────────────────────────────────
# These sitemap index entries point to archive/taxonomy/template pages.
# Skipping them at the sitemap level means zero archive pages get crawled
# on ANY WordPress site — no hardcoded site-specific paths needed.
SKIP_SITEMAP_PATTERNS = [
    "taxonomies",            # wp-sitemap-taxonomies-category-1.xml, -tag-1.xml, etc.
    "-users-",               # wp-sitemap-users-1.xml (author archives)
    "elementskit_template",  # Elementor template sitemaps
    "elementor_library",     # Elementor saved-blocks sitemaps
    "ae_global_templates",   # AE Global Templates
    "-nav_menu_item-",       # Navigation menu items
    "-wp_block-",            # Gutenberg reusable blocks
    "-wp_template",          # Block theme templates
    "-acf-",                 # Advanced Custom Fields sitemaps
]

EXCLUDE_QUERY_PATTERNS = [
    "elementskit_template=", "elementor_library=", "preview=",
    "replytocom=", "add-to-cart=", "action=", "s="
]


def is_third_party_or_software(url):
    """Return True if URL points to a third-party domain (social, software) or non-HTML file."""
    try:
        parsed = urlparse(url)
        netloc = parsed.netloc.lower()
        path   = parsed.path.lower()
        if any(domain in netloc for domain in THIRD_PARTY_DOMAINS):
            return True
        if any(path.endswith(ext) for ext in SOFTWARE_MEDIA_EXTS):
            return True
        return False
    except Exception:
        return False


def is_valid_internal_page(url, client_domain):
    """Return True if URL is a valid internal HTML web page under client_domain."""
    try:
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https"):
            return False
        netloc = parsed.netloc.lower()
        if client_domain not in netloc:
            return False

        url_lower   = url.lower()
        path_lower  = parsed.path.lower()
        query_lower = parsed.query.lower()

        # Skip third-party domains or media/software files
        if is_third_party_or_software(url):
            return False

        # Skip non-public, template, author, or utility paths
        for pat in EXCLUDE_PATH_PATTERNS:
            if pat in path_lower or pat in url_lower:
                return False

        # Skip query params for templates, previews, search, etc.
        for q_pat in EXCLUDE_QUERY_PATTERNS:
            if q_pat in query_lower:
                return False

        return True
    except Exception:
        return False

def is_archive_page(soup):
    """
    Universally detect archive / listing pages on ANY CMS.
    Returns True if the page is a category/tag/author/date archive,
    search results page, or any other non-content listing page.
    Works for WordPress, Shopify, custom CMS, etc.
    """
    # 1. WordPress body CSS classes (most reliable)
    body_tag = soup.find("body")
    body_classes = " ".join(body_tag.get("class", [])).lower() if body_tag else ""
    wp_archive_classes = [
        "archive", "category-", "tag-", "tax-", "date-",
        "author-", "search-results", "search-no-results",
        "post-type-archive", "blog",
    ]
    if any(cls in body_classes for cls in wp_archive_classes):
        return True

    # 2. Title/H1 text that signals an archive page (works on all CMS)
    h1_tags = soup.find_all("h1")
    h1_text = " ".join(h.get_text(strip=True) for h in h1_tags).lower()
    page_title_el = soup.find("title")
    title_text = (page_title_el.text.strip() if page_title_el else "").lower()

    archive_text_signals = [
        "category:", "tag:", "archive:", "author:",
        "search results for", "results for:",
    ]
    if any(sig in h1_text or sig in title_text for sig in archive_text_signals):
        return True

    # 3. Meta robots noindex (archive pages are often noindexed intentionally)
    # We don’t block on noindex alone — we audit noindexed CONTENT pages
    # but archive pages identified above are skipped entirely.

    return False


# ══════════════════════════════════════════════════════════════════════════════

COMMON_SITEMAPS = [
    "/sitemap_index.xml",
    "/sitemap.xml",
    "/sitemap-index.xml",
    "/wp-sitemap.xml",
    "/sitemap/sitemap-index.xml",
    "/news-sitemap.xml",
    "/page-sitemap.xml",
]

def find_sitemap_from_robots(base_url):
    """Check robots.txt for a Sitemap: directive."""
    try:
        resp = session.get(f"{base_url}/robots.txt", timeout=10)
        if resp.status_code == 200:
            for line in resp.text.splitlines():
                if line.lower().startswith("sitemap:"):
                    return line.split(":", 1)[1].strip()
    except Exception:
        pass
    return None


def discover_sitemap(base_url):
    """Try robots.txt first, then common paths. Return sitemap URL or None."""
    print("  Searching for sitemap ...")

    # 1. robots.txt
    sitemap = find_sitemap_from_robots(base_url)
    if sitemap:
        print(f"  ✅ Found via robots.txt: {sitemap}")
        return sitemap

    # 2. Common sitemap paths
    for path in COMMON_SITEMAPS:
        url = base_url + path
        try:
            r = session.get(url, timeout=8, stream=True, allow_redirects=True)
            if r.status_code == 200:
                print(f"  ✅ Found sitemap: {url}")
                return url
        except Exception:
            pass
        time.sleep(0.1)

    print("  ⚠️  No sitemap found — will crawl from homepage instead.")
    return None


def get_urls_from_sitemap(sitemap_url, depth=0):
    """
    Recursively fetch all page URLs from a sitemap or sitemap index.
    • Sub-sitemaps pointing to archives/taxonomies/users are auto-skipped.
    • Auto-handles HTTP 429 rate-limiting with exponential backoff.
    Works on any WordPress, Shopify, or custom CMS sitemap.
    """
    urls = []
    try:
        resp = session.get(sitemap_url, timeout=30)
        if resp.status_code == 429:
            print(f"  [WARN] Rate limited (429) on {sitemap_url}, retrying in 2 seconds...")
            time.sleep(2)
            resp = session.get(sitemap_url, timeout=30)

        if resp.status_code != 200:
            print(f"  [WARN] Sitemap HTTP {resp.status_code}: {sitemap_url}")
            return urls

        text_content = resp.text
        loc_urls = re.findall(r'<loc>(.*?)</loc>', text_content, re.IGNORECASE)
        if not loc_urls:
            soup = BeautifulSoup(text_content, "html.parser")
            loc_urls = [loc.text.strip() for loc in soup.find_all("loc")]

        for href in loc_urls:
            href = href.strip()
            if href.endswith(".xml"):
                # Auto-skip sitemaps that contain archive/taxonomy/template pages
                href_lower = href.lower()
                if any(pat in href_lower for pat in SKIP_SITEMAP_PATTERNS):
                    print(f"  [SKIP] Archive/template sitemap: {href}")
                    continue
                # Fetch remaining sub-sitemaps independently
                sub_urls = []
                try:
                    time.sleep(0.1)
                    sub_urls = get_urls_from_sitemap(href, depth + 1)
                except Exception as sub_exc:
                    print(f"  [WARN] Skipped sub-sitemap ({href}): {sub_exc}")
                urls.extend(sub_urls)
            else:
                if is_valid_internal_page(href, CLIENT_DOMAIN):
                    urls.append(href)
    except Exception as exc:
        if depth == 0:
            print(f"  [WARN] Could not read sitemap ({sitemap_url}): {exc}")
        else:
            print(f"  [WARN] Sub-sitemap timeout, skipping: {sitemap_url}")
    return sorted(list(set(urls)))



def crawl_from_homepage(base_url, max_pages=500):
    """
    Fallback: crawl internal links starting from the homepage.
    Used when the sitemap is completely unavailable.
    Automatically skips archive/listing pages on ANY CMS using is_archive_page().
    """
    print(f"  ⚠️  Sitemap unavailable — crawling from homepage (up to {max_pages} pages) ...")
    visited = set()
    queue   = [base_url]
    found   = []

    while queue and len(found) < max_pages:
        url = queue.pop(0)
        if url in visited:
            continue
        visited.add(url)
        try:
            r = session.get(url, timeout=15, allow_redirects=True)
            if r.status_code == 200 and "text/html" in r.headers.get("Content-Type", ""):
                soup = BeautifulSoup(r.text, "html.parser")

                # Skip archive/listing pages on any CMS (generic detection)
                if not is_archive_page(soup):
                    found.append(url)

                # Always harvest links even from archive pages
                for a in soup.find_all("a", href=True):
                    href = a["href"].strip()
                    full = urljoin(base_url, href).split("#")[0].rstrip("/")
                    if is_valid_internal_page(full, CLIENT_DOMAIN) and full not in visited and full not in queue:
                        queue.append(full)
        except Exception:
            pass
        time.sleep(0.05)

    print(f"  Found {len(found)} internal pages via link crawl.")
    return sorted(list(set(found)))





def discover_all_urls(base_url):
    """Main entry — discover URLs via sitemap or homepage crawl."""
    sitemap = discover_sitemap(base_url)
    if sitemap:
        urls = get_urls_from_sitemap(sitemap)
        if urls:
            return urls
        print("  ⚠️  Sitemap found but returned 0 valid pages — falling back to link crawl.")
    return crawl_from_homepage(base_url)



# ══════════════════════════════════════════════════════════════════════════════
# LINK BROKEN CHECK  (cached)
# ══════════════════════════════════════════════════════════════════════════════

link_cache: dict = {}
link_cache_lock = threading.Lock()

def is_broken(url, valid_urls_set=None):
    """Return True if the URL returns a 4xx/5xx or times out."""
    if valid_urls_set and url in valid_urls_set:
        return False
    with link_cache_lock:
        if url in link_cache:
            return link_cache[url]
    broken = False
    try:
        r = session.get(url, timeout=(1.0, 1.5), stream=True)
        broken = r.status_code >= 400
    except Exception:
        broken = True
    with link_cache_lock:
        link_cache[url] = broken
    return broken


# ══════════════════════════════════════════════════════════════════════════════
# CORE SEO AUDIT
# ══════════════════════════════════════════════════════════════════════════════

def audit_page(url, valid_urls_set=None):
    """
    Run a full technical SEO audit on a single URL.
    Returns a flat dict whose keys become Google Sheets column headers.
    """

    # ── Default skeleton (all columns pre-declared) ────────────────────────
    r = {
        # PAGE INFO
        "Page URL"              : url,
        "Final URL"             : url,
        "HTTP Status"           : None,
        "Was Redirected"        : "✅ No",
        "Redirect Count"        : 0,
        "Response Time (ms)"    : None,
        "Page Size (KB)"        : None,

        # URL HEALTH
        "Is HTTPS"              : "✅ Yes" if url.startswith("https://") else "❌ No",
        "URL Too Long (>115)"   : "❌ Yes" if len(url) > 115 else "✅ No",
        "URL Has Underscores"   : "",
        "URL Has Uppercase"     : "",
        "URL Depth (Levels)"    : None,
        "URL Has Query Params"  : "",

        # INDEXABILITY
        "Is Indexable"          : "",
        "Noindex Source"        : "None",
        "Canonical URL"         : "Missing",
        "Canonical Type"        : "Missing",

        # META TAGS
        "Title Tag"             : "",
        "Title Length (chars)"  : 0,
        "Title Status"          : "",
        "Meta Description"      : "",
        "Meta Desc Length"      : 0,
        "Meta Desc Status"      : "",
        "Meta Keywords"         : "",

        # OPEN GRAPH / SOCIAL
        "OG Title"              : "",
        "OG Description"        : "",
        "OG Image"              : "",
        "Twitter Card"          : "",

        # STRUCTURED DATA
        "Schema Markup"         : "",
        "Schema Types Found"    : "None",

        # CONTENT QUALITY
        "Word Count"            : 0,
        "Content Quality"       : "",
        "Reading Time (mins)"   : "",
        "Table of Contents"     : "",

        # HEADING STRUCTURE
        "H1 Text"               : "",
        "H1 Count"              : 0,
        "H1 Status"             : "",
        "H2 Count"              : 0,
        "H3 Count"              : 0,
        "Heading Hierarchy OK"  : "",
        "H1 Before H2"          : "",

        # LINKS
        "Internal Links"        : 0,
        "External Links"        : 0,
        "Nofollow Internal"     : 0,
        "Nofollow External"     : 0,
        "Broken Links Count"    : 0,
        "Broken Links"          : "None",
        "Empty Links Count"     : 0,
        "Empty/Anchor Links"    : "None",

        # IMAGES
        "Total Images"               : 0,
        "Missing Alt Count"          : 0,
        "Missing Alt Images"         : "None",
        "Missing Dimensions Count"   : 0,
        "Missing Dimensions Images"  : "None",
        "Heavy Images Count"         : 0,
        "Heavy Images (>500KB)"      : "None",
        "Broken Images Count"        : 0,
        "Broken Images"              : "None",
        "Non-HTTPS Images Count"     : 0,
        "Non-HTTPS Images"           : "None",
    }

    # ── URL structure analysis ─────────────────────────────────────────────
    parsed = urlparse(url)
    slug   = parsed.path.strip("/")
    depth  = len([s for s in parsed.path.split("/") if s])

    r["URL Has Underscores"]  = "❌ Yes (use hyphens)" if "_" in slug else "✅ No"
    r["URL Has Uppercase"]    = "❌ Yes (use lowercase)" if any(c.isupper() for c in slug) else "✅ No"
    r["URL Depth (Levels)"]   = depth
    r["URL Has Query Params"] = "⚠️ Yes" if parsed.query else "✅ No"

    # ── Fetch the page ─────────────────────────────────────────────────────
    try:
        t0       = time.time()
        response = session.get(url, timeout=20, allow_redirects=True)
        elapsed  = round((time.time() - t0) * 1000)

        r["HTTP Status"]        = response.status_code
        r["Response Time (ms)"] = elapsed
        r["Final URL"]          = response.url
        r["Page Size (KB)"]     = round(len(response.content) / 1024, 1)

        if response.history:
            count = len(response.history)
            r["Was Redirected"] = f"⚠️ Yes ({count} hop{'s' if count > 1 else ''})"
            r["Redirect Count"] = count

        # Stop here for non-200 pages
        if response.status_code != 200:
            return r

        soup = BeautifulSoup(response.text, "html.parser")

        # ── INDEXABILITY ──────────────────────────────────────────────────
        robots_meta  = soup.find("meta", attrs={"name": re.compile(r"^robots$", re.I)})
        x_robots_hdr = response.headers.get("X-Robots-Tag", "")
        noindex      = False
        noindex_src  = "None"

        if robots_meta:
            content = robots_meta.get("content", "").lower()
            if "noindex" in content:
                noindex     = True
                noindex_src = "meta robots tag"

        if "noindex" in x_robots_hdr.lower():
            noindex     = True
            noindex_src = "X-Robots-Tag header"

        r["Is Indexable"]   = "❌ NOT Indexable" if noindex else "✅ Indexable"
        r["Noindex Source"] = noindex_src

        # ── CANONICAL ─────────────────────────────────────────────────────
        canonical_tag = soup.find("link", rel="canonical")
        if canonical_tag and canonical_tag.get("href"):
            canon_href  = canonical_tag["href"].rstrip("/")
            page_clean  = url.rstrip("/")
            final_clean = response.url.rstrip("/")
            r["Canonical URL"] = canonical_tag["href"]
            if canon_href in (page_clean, final_clean):
                r["Canonical Type"] = "✅ Self-Referencing"
            else:
                r["Canonical Type"] = "⚠️ Points Elsewhere"
        else:
            r["Canonical URL"]  = "❌ Missing"
            r["Canonical Type"] = "❌ Missing"

        # ── TITLE TAG ─────────────────────────────────────────────────────
        title_el = soup.find("title")
        if title_el and title_el.text.strip():
            title_text            = title_el.text.strip()
            title_len             = len(title_text)
            r["Title Tag"]        = title_text
            r["Title Length (chars)"] = title_len
            if title_len < 30:
                r["Title Status"] = "⚠️ Too Short (<30 chars)"
            elif title_len <= 60:
                r["Title Status"] = "✅ Optimal (30-60 chars)"
            else:
                r["Title Status"] = "⚠️ Too Long (>60 chars)"
        else:
            r["Title Status"] = "❌ Missing"

        # ── META DESCRIPTION ──────────────────────────────────────────────
        meta_desc_el = soup.find("meta", attrs={"name": re.compile(r"^description$", re.I)})
        if meta_desc_el and meta_desc_el.get("content", "").strip():
            desc_text             = meta_desc_el["content"].strip()
            desc_len              = len(desc_text)
            r["Meta Description"] = desc_text
            r["Meta Desc Length"] = desc_len
            if desc_len < 70:
                r["Meta Desc Status"] = "⚠️ Too Short (<70 chars)"
            elif desc_len <= 160:
                r["Meta Desc Status"] = "✅ Optimal (70-160 chars)"
            else:
                r["Meta Desc Status"] = "⚠️ Too Long (>160 chars)"
        else:
            r["Meta Desc Status"] = "❌ Missing"

        # ── META KEYWORDS ─────────────────────────────────────────────────
        meta_kw_el = soup.find("meta", attrs={"name": re.compile(r"^keywords$", re.I)})
        r["Meta Keywords"] = "✅ Present" if (meta_kw_el and meta_kw_el.get("content", "").strip()) else "❌ Missing"

        # ── OPEN GRAPH ────────────────────────────────────────────────────
        def og(prop):
            tag = soup.find("meta", property=prop)
            return "✅ Present" if (tag and tag.get("content", "").strip()) else "❌ Missing"

        r["OG Title"]       = og("og:title")
        r["OG Description"] = og("og:description")
        r["OG Image"]       = og("og:image")
        tw = soup.find("meta", attrs={"name": "twitter:card"})
        r["Twitter Card"]   = "✅ Present" if tw else "❌ Missing"

        # ── SCHEMA / STRUCTURED DATA ──────────────────────────────────────
        schema_scripts   = soup.find_all("script", type="application/ld+json")
        schema_itemscope = soup.find_all(attrs={"itemscope": True})
        schema_types     = []

        for s in schema_scripts:
            try:
                data = json.loads(s.string or "{}")
                items = data if isinstance(data, list) else [data]
                for item in items:
                    if "@type" in item:
                        t = item["@type"]
                        schema_types += (t if isinstance(t, list) else [t])
            except Exception:
                pass

        if schema_scripts or schema_itemscope:
            r["Schema Markup"]      = "✅ Present"
            r["Schema Types Found"] = ", ".join(sorted(set(schema_types))) if schema_types else "Microdata (itemscope)"
        else:
            r["Schema Markup"]      = "❌ Missing"
            r["Schema Types Found"] = "None"

        # ── CONTENT QUALITY ───────────────────────────────────────────────
        content_soup = BeautifulSoup(response.text, "html.parser")
        for noise in content_soup.find_all(["script", "style", "nav", "header", "footer", "noscript"]):
            noise.decompose()

        body_text  = content_soup.body.get_text(separator=" ", strip=True) if content_soup.body else ""
        word_count = len(body_text.split())
        r["Word Count"] = word_count

        if word_count < 300:
            r["Content Quality"] = "❌ Thin Content (<300 words)"
        elif word_count < 600:
            r["Content Quality"] = "⚠️ Moderate (300-600 words)"
        else:
            r["Content Quality"] = "✅ Good (600+ words)"

        r["Reading Time (mins)"] = f"{max(1, round(word_count / 200))} min"

        toc = soup.find(id=re.compile(r"toc|table.of.content", re.I)) or \
              soup.find(class_=re.compile(r"toc|table.of.content", re.I))
        r["Table of Contents"] = "✅ Yes" if toc else "❌ No"

        # ── HEADING STRUCTURE ─────────────────────────────────────────────
        h1s = soup.find_all("h1")
        h2s = soup.find_all("h2")
        h3s = soup.find_all("h3")

        r["H1 Count"] = len(h1s)
        r["H2 Count"] = len(h2s)
        r["H3 Count"] = len(h3s)

        if len(h1s) == 0:
            r["H1 Text"]   = ""
            r["H1 Status"] = "❌ Missing"
        elif len(h1s) > 1:
            r["H1 Text"]   = " | ".join(h.get_text(strip=True)[:50] for h in h1s)
            r["H1 Status"] = f"❌ Multiple ({len(h1s)} found)"
        else:
            r["H1 Text"]   = h1s[0].get_text(strip=True)
            r["H1 Status"] = "✅ OK (1 H1)"

        # Heading level jump detection — capture the offending heading text
        all_hdrs = soup.find_all(re.compile(r"^h[1-6]$"))
        prev_lvl = 0
        hier_ok  = True
        skip_msg = ""
        for h in all_hdrs:
            lvl = int(h.name[1])
            if prev_lvl and lvl > prev_lvl + 1:
                hier_ok  = False
                skip_msg = f" | Jumped H{prev_lvl}→H{lvl}: \"{h.get_text(strip=True)[:60]}\""
                break
            prev_lvl = lvl
        r["Heading Hierarchy OK"] = "✅ Yes" if hier_ok else f"❌ No (level skipped){skip_msg}"

        # H1 comes before first H2 — if wrong, show which H2 appeared first
        if h1s and h2s:
            first_hdr = soup.find(re.compile(r"^h[12]$"))
            if first_hdr and first_hdr.name == "h2":
                first_h2_text = first_hdr.get_text(strip=True)[:70]
                r["H1 Before H2"] = f"❌ No — First H2 is: \"{first_h2_text}\""
            else:
                r["H1 Before H2"] = "✅ Yes"
        elif not h1s:
            r["H1 Before H2"] = "❌ No H1 tag"
        else:
            r["H1 Before H2"] = "⚠️ No H2 found"


        # ── LINK AUDIT ────────────────────────────────────────────────────
        internal_count = external_count = nf_int = nf_ext = 0
        broken_links = []
        empty_links  = []

        for a_tag in soup.find_all("a"):
            href = (a_tag.get("href") or "").strip()
            rel  = a_tag.get("rel") or []
            if isinstance(rel, str):
                rel = rel.split()
            has_nf = "nofollow" in rel

            if not href or href == "#" or href.startswith("javascript:"):
                empty_links.append(a_tag.get_text(strip=True)[:50] or "[no text]")
                continue
            if href.startswith(("mailto:", "tel:")):
                continue

            full = urljoin(url, href)
            p    = urlparse(full)

            if CLIENT_DOMAIN in p.netloc:
                internal_count += 1
                if has_nf:
                    nf_int += 1
            elif full.startswith("http"):
                external_count += 1
                if has_nf:
                    nf_ext += 1

            if href.startswith(("http", "/")):
                if not is_third_party_or_software(full):
                    if is_broken(full, valid_urls_set):
                        broken_links.append(full)

        r["Internal Links"]    = internal_count
        r["External Links"]    = external_count
        r["Nofollow Internal"] = nf_int
        r["Nofollow External"] = nf_ext

        unique_broken           = sorted(set(broken_links))
        r["Broken Links Count"] = len(unique_broken)
        r["Broken Links"]       = "\n".join(unique_broken) if unique_broken else "None"

        unique_empty             = sorted(set(empty_links))
        r["Empty Links Count"]   = len(unique_empty)
        r["Empty/Anchor Links"]  = "\n".join(unique_empty[:15]) if unique_empty else "None"

        # ── IMAGE AUDIT ───────────────────────────────────────────────────
        missing_alt  = []
        missing_dims = []
        heavy_imgs   = []
        broken_imgs  = []
        non_https    = []
        img_count    = 0

        for img in soup.find_all("img"):
            src = (img.get("src") or "").strip()
            if not src or src.startswith("data:"):
                continue

            img_count += 1
            img_url    = urljoin(url, src)

            alt = img.get("alt")
            if alt is None or not alt.strip():
                missing_alt.append(img_url)

            if not img.get("width") or not img.get("height"):
                missing_dims.append(img_url)

            if not img_url.startswith("https://"):
                non_https.append(img_url)

            try:
                img_r  = session.head(img_url, timeout=7, allow_redirects=True)
                if img_r.status_code >= 400:
                    broken_imgs.append(img_url)
                else:
                    size_b = int(img_r.headers.get("content-length", 0))
                    if size_b > 500_000:
                        heavy_imgs.append(f"{img_url}  ({round(size_b / 1024):,} KB)")
            except Exception:
                broken_imgs.append(img_url)

        r["Total Images"]              = img_count
        r["Missing Alt Count"]         = len(missing_alt)
        r["Missing Alt Images"]        = "\n".join(missing_alt)       if missing_alt  else "None"
        r["Missing Dimensions Count"]  = len(missing_dims)
        r["Missing Dimensions Images"] = "\n".join(missing_dims[:10]) if missing_dims else "None"
        r["Heavy Images Count"]        = len(heavy_imgs)
        r["Heavy Images (>500KB)"]     = "\n".join(heavy_imgs)        if heavy_imgs   else "None"
        r["Broken Images Count"]       = len(broken_imgs)
        r["Broken Images"]             = "\n".join(broken_imgs)       if broken_imgs  else "None"
        r["Non-HTTPS Images Count"]    = len(non_https)
        r["Non-HTTPS Images"]          = "\n".join(non_https)         if non_https    else "None"

    except Exception as exc:
        r["HTTP Status"] = f"Error: {str(exc)[:80]}"

    return r


# ══════════════════════════════════════════════════════════════════════════════
# CONVERT PAGE AUDIT RESULTS → DEVELOPER ISSUES LIST
# ══════════════════════════════════════════════════════════════════════════════

SEVERITY_ORDER = {"🔴 Critical": 0, "🟡 Warning": 1, "🔵 Info": 2}

def add_issue(issues, page_url, page_title, severity, category, issue_name, detail):
    """Add one issue row to the flat issues list."""
    issues.append({
        "Severity"      : severity,
        "Category"      : category,
        "Issue"         : issue_name,
        "Page URL"      : page_url,
        "Page Title"    : page_title,
        "Detail / Fix"  : detail,
    })


def convert_to_issues_list(seo_data):
    """
    Convert the raw per-page audit dict into a flat list of individual issues.
    One row = one issue = one task for the developer.
    Perfect pages (0 issues) are skipped entirely.
    Issues are sorted: Critical → Warning → Info, then by Page URL.
    """
    issues = []

    for page in seo_data:
        url        = page.get("Page URL", "")
        title      = page.get("Title Tag", "") or "(No Title)"
        status     = page.get("HTTP Status")

        # ── HTTP Errors ────────────────────────────────────────────────────
        if status and str(status).startswith("Error"):
            add_issue(issues, url, title,
                      "🔴 Critical", "Page Health", "Page Load Error",
                      f"The page could not be fetched. Server returned: {status}")
            continue  # Skip full audit — page didn't load

        if isinstance(status, int) and status >= 400:
            add_issue(issues, url, title,
                      "🔴 Critical", "Page Health", f"HTTP {status} Error",
                      f"Page is broken (HTTP {status}). Remove from sitemap, redirect or fix the URL.")
            continue

        # ── Redirect ───────────────────────────────────────────────────────
        redir = page.get("Was Redirected", "")
        if "⚠️" in str(redir):
            count = page.get("Redirect Count", 0)
            add_issue(issues, url, title,
                      "🟡 Warning", "Page Health", f"Redirect Chain ({count} hop{'s' if count>1 else ''})",
                      f"Update internal links to point directly to the final URL to avoid redirect overhead.")

        # ── Slow Response ──────────────────────────────────────────────────
        resp_time = page.get("Response Time (ms)", 0) or 0
        if resp_time > 2000:
            add_issue(issues, url, title,
                      "🔴 Critical", "Page Health", "Very Slow Response (>2s)",
                      f"Response time: {resp_time}ms. Optimise server, enable caching, use CDN.")
        elif resp_time > 800:
            add_issue(issues, url, title,
                      "🟡 Warning", "Page Health", "Slow Response (>800ms)",
                      f"Response time: {resp_time}ms. Review server performance and caching.")

        # ── Not Indexable ──────────────────────────────────────────────────
        indexable = page.get("Is Indexable", "")
        if "❌" in str(indexable):
            src = page.get("Noindex Source", "")
            add_issue(issues, url, title,
                      "🔴 Critical", "Indexability", "Page is NOT Indexable",
                      f"Noindex found in: {src}. Remove noindex if this page should appear in Google.")

        # ── Canonical ──────────────────────────────────────────────────────
        canon_type = page.get("Canonical Type", "")
        if "Missing" in str(canon_type) or "❌" in str(canon_type):
            add_issue(issues, url, title,
                      "🔴 Critical", "Indexability", "Missing Canonical Tag",
                      "Add <link rel='canonical' href='THIS_PAGE_URL'> to the <head>.")
        elif "⚠️ Points Elsewhere" in str(canon_type):
            canon_url = page.get("Canonical URL", "")
            add_issue(issues, url, title,
                      "🟡 Warning", "Indexability", "Canonical Points to Different URL",
                      f"Canonical points to: {canon_url}. Verify this is intentional.")

        # ── Title Tag ──────────────────────────────────────────────────────
        title_status = page.get("Title Status", "")
        title_len    = page.get("Title Length (chars)", 0)
        if "❌ Missing" in str(title_status):
            add_issue(issues, url, title,
                      "🔴 Critical", "Meta Tags", "Title Tag Missing",
                      "Page has no <title> tag. Add a unique, descriptive title (30–60 characters).")
        elif "Too Short" in str(title_status):
            add_issue(issues, url, title,
                      "🟡 Warning", "Meta Tags", f"Title Too Short ({title_len} chars)",
                      f"Current title: \"{title}\". Expand to 30–60 characters to improve CTR.")
        elif "Too Long" in str(title_status):
            add_issue(issues, url, title,
                      "🟡 Warning", "Meta Tags", f"Title Too Long ({title_len} chars)",
                      f"Current title: \"{title}\". Trim to under 60 characters — Google truncates longer titles.")

        # ── Meta Description ───────────────────────────────────────────────
        meta_status = page.get("Meta Desc Status", "")
        meta_len    = page.get("Meta Desc Length", 0)
        meta_desc   = page.get("Meta Description", "")
        if "❌ Missing" in str(meta_status):
            add_issue(issues, url, title,
                      "🔴 Critical", "Meta Tags", "Meta Description Missing",
                      "Add a unique meta description (70–160 characters) to improve click-through rate.")
        elif "Too Short" in str(meta_status):
            add_issue(issues, url, title,
                      "🟡 Warning", "Meta Tags", f"Meta Description Too Short ({meta_len} chars)",
                      f"Current: \"{meta_desc[:80]}...\". Expand to 70–160 characters.")
        elif "Too Long" in str(meta_status):
            add_issue(issues, url, title,
                      "🟡 Warning", "Meta Tags", f"Meta Description Too Long ({meta_len} chars)",
                      f"Current: \"{meta_desc[:80]}...\". Trim to under 160 characters to prevent truncation.")

        # ── H1 ─────────────────────────────────────────────────────────────
        h1_status = page.get("H1 Status", "")
        h1_text   = page.get("H1 Text", "")
        h1_count  = page.get("H1 Count", 0)
        if "❌ Missing" in str(h1_status):
            add_issue(issues, url, title,
                      "🔴 Critical", "Headings", "H1 Tag Missing",
                      "Add exactly one <h1> tag with the primary keyword for this page.")
        elif "Multiple" in str(h1_status):
            add_issue(issues, url, title,
                      "🔴 Critical", "Headings", f"Multiple H1 Tags ({h1_count} found)",
                      f"H1 texts found: {h1_text}. Keep only ONE <h1> per page. Remove extras from Elementor template/theme.")

        # ── Heading Hierarchy ──────────────────────────────────────────────
        hier = page.get("Heading Hierarchy OK", "")
        if "❌" in str(hier):
            add_issue(issues, url, title,
                      "🟡 Warning", "Headings", "Heading Level Skipped",
                      f"{hier.replace('❌ No (level skipped)', '').strip()}. Fix heading order: H1 → H2 → H3 (no skipping levels).")

        h1_before = page.get("H1 Before H2", "")
        if "❌" in str(h1_before):
            add_issue(issues, url, title,
                      "🟡 Warning", "Headings", "H2 Appears Before H1",
                      f"{h1_before}. Move H1 above all H2 tags in the page template.")

        # ── Content Quality ────────────────────────────────────────────────
        content_q  = page.get("Content Quality", "")
        word_count = page.get("Word Count", 0)
        if "❌ Thin" in str(content_q):
            add_issue(issues, url, title,
                      "🔴 Critical", "Content", f"Thin Content ({word_count} words)",
                      "Page has fewer than 300 words. Add more useful content or merge with another page.")
        elif "⚠️ Moderate" in str(content_q):
            add_issue(issues, url, title,
                      "🟡 Warning", "Content", f"Low Word Count ({word_count} words)",
                      "Page has 300–600 words. Consider expanding with FAQs, details, or supporting content.")

        # ── Broken Links ───────────────────────────────────────────────────
        broken_count = page.get("Broken Links Count", 0)
        broken_urls  = page.get("Broken Links", "None")
        if broken_count and broken_count > 0:
            add_issue(issues, url, title,
                      "🔴 Critical", "Links", f"Broken Links ({broken_count} found)",
                      f"Fix or remove these broken links:\n{broken_urls}")

        # ── Images: Missing Alt ────────────────────────────────────────────
        alt_count    = page.get("Missing Alt Count", 0)
        alt_imgs     = page.get("Missing Alt Images", "None")
        if alt_count and alt_count > 0:
            add_issue(issues, url, title,
                      "🟡 Warning", "Images", f"Missing Alt Text ({alt_count} images)",
                      f"Add descriptive alt attributes to these images:\n{alt_imgs}")

        # ── Images: Heavy ──────────────────────────────────────────────────
        heavy_count = page.get("Heavy Images Count", 0)
        heavy_imgs  = page.get("Heavy Images (>500KB)", "None")
        if heavy_count and heavy_count > 0:
            add_issue(issues, url, title,
                      "🟡 Warning", "Images", f"Large Image Files ({heavy_count} images >500KB)",
                      f"Compress or convert these to WebP format:\n{heavy_imgs}")

        # ── Images: Broken ─────────────────────────────────────────────────
        broken_img_count = page.get("Broken Images Count", 0)
        broken_img_urls  = page.get("Broken Images", "None")
        if broken_img_count and broken_img_count > 0:
            add_issue(issues, url, title,
                      "🔴 Critical", "Images", f"Broken Images ({broken_img_count} found)",
                      f"These image files are missing or returning errors:\n{broken_img_urls}")

        # ── Schema Markup ──────────────────────────────────────────────────
        schema = page.get("Schema Markup", "")
        if "❌ Missing" in str(schema):
            add_issue(issues, url, title,
                      "🔵 Info", "Structured Data", "Schema Markup Missing",
                      "Add JSON-LD structured data (e.g. Organization, Product, BreadcrumbList) to help Google understand the page.")

        # ── OG Tags ────────────────────────────────────────────────────────
        og_img = page.get("OG Image", "")
        og_desc = page.get("OG Description", "")
        if "❌ Missing" in str(og_img):
            add_issue(issues, url, title,
                      "🔵 Info", "Social / OG", "OG Image Missing",
                      "Add og:image meta tag for proper social media sharing previews.")
        if "❌ Missing" in str(og_desc):
            add_issue(issues, url, title,
                      "🔵 Info", "Social / OG", "OG Description Missing",
                      "Add og:description meta tag for social media sharing previews.")

    # Sort: Critical first, then Warning, then Info; within each level by URL
    issues.sort(key=lambda x: (SEVERITY_ORDER.get(x["Severity"], 99), x["Page URL"]))

    return issues


# ══════════════════════════════════════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════════════════════════════════════

def calculate_audit_score(total_pages, critical_count, warning_count, info_count, clean_pages):
    """
    Calculate a normalized Overall SEO Health Score (0 - 100%).
    Takes into account clean pages ratio and weighted penalty per issue type.
    """
    if total_pages <= 0:
        return 0, "🔴 Poor (F)"

    total_penalty = (critical_count * 5.0) + (warning_count * 2.0) + (info_count * 0.5)
    penalty_per_page = total_penalty / total_pages
    score = max(0, min(100, round(100 - (penalty_per_page * 5))))

    if score >= 90:
        grade = "🟢 Excellent (A)"
    elif score >= 75:
        grade = "🟡 Good (B)"
    elif score >= 50:
        grade = "🟠 Needs Improvement (C)"
    else:
        grade = "🔴 Poor (F)"

    return score, grade


def main():
    print("=" * 75)
    print(f"  Technical SEO Crawler  |  Domain: {CLIENT_DOMAIN}")
    print("=" * 75)
    print(f"  Target Website : {BASE_URL}")
    print()

    # Step 1: Discover internal page URLs
    all_urls = discover_all_urls(BASE_URL)
    total    = len(all_urls)

    # Step 2: List all discovered internal URLs upfront
    print("\n" + "=" * 75)
    print(f"  📌 FOUND {total} USER-FACING PAGES TO AUDIT")
    print("=" * 75)
    for idx, u in enumerate(all_urls, 1):
        print(f"  {idx:>3}. {u}")
    print("=" * 75 + "\n")

    # Step 3: Audit every page in parallel (high-speed ThreadPoolExecutor)
    seo_data = [None] * total
    completed = 0
    valid_urls_set = set(all_urls)
    print(f"  Starting high-speed parallel SEO audit (15 workers) for {total} pages ...\n", flush=True)

    lock = threading.Lock()

    def worker(item):
        nonlocal completed
        idx, page_url = item
        data = audit_page(page_url, valid_urls_set)
        with lock:
            completed += 1
            print(f"  [{completed:>3}/{total}] Audited: {page_url}")
            sys.stdout.flush()
        return idx, data

    max_workers = min(15, max(1, total))
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = [executor.submit(worker, (i, u)) for i, u in enumerate(all_urls)]
        for future in as_completed(futures):
            idx, res = future.result()
            seo_data[idx] = res

    # Step 4: Convert to issues list (task-list format)
    issues = convert_to_issues_list(seo_data)

    # Step 5: Calculate Overall SEO Score & Print terminal summary
    critical = [i for i in issues if i["Severity"] == "🔴 Critical"]
    warnings = [i for i in issues if i["Severity"] == "🟡 Warning"]
    info     = [i for i in issues if i["Severity"] == "🔵 Info"]
    clean_pages = total - len({i["Page URL"] for i in issues})

    score, grade = calculate_audit_score(total, len(critical), len(warnings), len(info), clean_pages)

    print("\n" + "=" * 75)
    print(f"  ✅ AUDIT DONE  —  {total} pages  |  {len(issues)} issues found")
    print(f"  🎯 OVERALL SEO AUDIT SCORE : {score} / 100  {grade}")
    print(f"     🔴 Critical: {len(critical)}   🟡 Warning: {len(warnings)}   🔵 Info: {len(info)}   ✅ Clean pages: {clean_pages}")
    print("=" * 75)

    print("\n  🔴 CRITICAL ISSUES (Fix immediately):")
    print("  " + "-" * 71)
    for iss in critical:
        print(f"  [{iss['Category']}] {iss['Issue']}")
        print(f"     ↳ {iss['Page URL']}")
        print(f"     ↳ {iss['Detail / Fix'][:120]}")
        print()

    print("\n  🟡 WARNINGS (Review and fix soon):")
    print("  " + "-" * 71)
    for iss in warnings[:20]:  # Show first 20 to avoid terminal flood
        print(f"  [{iss['Category']}] {iss['Issue']}")
        print(f"     ↳ {iss['Page URL']}")
        print()
    if len(warnings) > 20:
        print(f"  ... and {len(warnings)-20} more warnings in Google Sheet.")

    print("=" * 75 + "\n")

    # Step 6: Send to n8n webhook
    payload = {
        "client_domain"   : CLIENT_DOMAIN,
        "total_pages"     : total,
        "audit_score"     : score,
        "audit_grade"     : grade,
        "total_issues"    : len(issues),
        "critical_count"  : len(critical),
        "warning_count"   : len(warnings),
        "info_count"      : len(info),
        "clean_pages"     : clean_pages,
        "issues"          : issues,
    }

    print("  Sending issues list to n8n webhook ...")
    sheet_search_url = f"https://drive.google.com/drive/search?q=SEO_Report_{CLIENT_DOMAIN}"
    direct_sheet_url = None
    try:
        resp = session.post(WEBHOOK_URL, json=payload, timeout=60)
        if resp.status_code == 200:
            print(f"  ✅ Success! Score {score}% & {len(issues)} issues from {total} pages sent to Google Sheets.")
            raw_text = resp.text.strip()
            
            # 1. Direct full HTTP/HTTPS URL
            if raw_text.startswith("http://") or raw_text.startswith("https://"):
                direct_sheet_url = raw_text
            
            # 2. Raw Google Sheet ID (e.g. 1a1ZVXfoMOYqhf5rAFTlb6hgCJlbRHCb1OoETHissfaE)
            elif len(raw_text) > 20 and not raw_text.startswith("{") and not raw_text.startswith("[") and " " not in raw_text:
                direct_sheet_url = f"https://docs.google.com/spreadsheets/d/{raw_text}/edit"
            
            # 3. JSON response parsing
            else:
                try:
                    resp_data = resp.json()
                    if isinstance(resp_data, list) and len(resp_data) > 0:
                        resp_data = resp_data[0]
                    if isinstance(resp_data, dict):
                        for key in ["sheet_url", "url", "spreadsheetUrl", "spreadsheet_url", "link"]:
                            val = resp_data.get(key)
                            if val and str(val).startswith("http"):
                                direct_sheet_url = str(val)
                                break
                        if not direct_sheet_url:
                            spreadsheet_id = resp_data.get("spreadsheetId") or resp_data.get("id")
                            sheet_tab_id = resp_data.get("sheetId") or resp_data.get("gid")
                            if spreadsheet_id and isinstance(spreadsheet_id, str) and len(str(spreadsheet_id)) > 15:
                                direct_sheet_url = f"https://docs.google.com/spreadsheets/d/{spreadsheet_id}/edit"
                                if sheet_tab_id is not None:
                                    direct_sheet_url += f"#gid={sheet_tab_id}"
                except Exception:
                    pass

            final_url = direct_sheet_url or sheet_search_url
            print(f"  📊 Google Sheet Report: {final_url}")
            print(f"[SheetURL] {final_url}")
        else:
            print(f"  ⚠️ Webhook returned HTTP {resp.status_code}")
            print(f"  Response: {resp.text[:300]}")
            print(f"  📊 Google Sheet Report: {sheet_search_url}")
            print(f"[SheetURL] {sheet_search_url}")
    except Exception as exc:
        print(f"  ❌ Webhook error: {exc}")
        print(f"  📊 Google Sheet Report: {sheet_search_url}")
        print(f"[SheetURL] {sheet_search_url}")

    print("=" * 75)


if __name__ == "__main__":
    main()

