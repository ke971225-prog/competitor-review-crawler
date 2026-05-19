from __future__ import annotations

import argparse
import asyncio
import csv
import hashlib
import html
import json
import re
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse
from urllib.request import Request, urlopen

LOCAL_VENDOR = Path(__file__).resolve().parent / "vendor"
if LOCAL_VENDOR.exists():
    sys.path.insert(0, str(LOCAL_VENDOR))

from playwright.async_api import async_playwright


DEFAULT_SITES = ["https://cutevision.shop/", "https://www.quboox.com/"]
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)


@dataclass
class Product:
    site: str
    platform: str
    title: str
    handle: str
    url: str
    price: str = ""
    compare_at_price: str = ""
    currency: str = ""
    rating: str = ""
    review_count: str = ""
    image_urls: list[str] = field(default_factory=list)
    downloaded_images: list[str] = field(default_factory=list)


@dataclass
class Review:
    site: str
    provider: str
    product_title: str
    product_url: str
    author: str = ""
    rating: str = ""
    title: str = ""
    body: str = ""
    date: str = ""
    image_urls: list[str] = field(default_factory=list)
    downloaded_images: list[str] = field(default_factory=list)


def clean_text(value: Any) -> str:
    if value is None:
        return ""
    return re.sub(r"\s+", " ", str(value)).strip()


def request_json(url: str) -> Any:
    req = Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json,text/html,*/*"})
    with urlopen(req, timeout=30) as response:
        return json.loads(response.read().decode("utf-8", "ignore"))


def request_text(url: str) -> str:
    req = Request(url, headers={"User-Agent": USER_AGENT, "Accept": "text/html,*/*"})
    with urlopen(req, timeout=30) as response:
        return response.read().decode("utf-8", "ignore")


def normalize_url(base: str, url: str) -> str:
    if not url:
        return ""
    if url.startswith("//"):
        return "https:" + url
    return urljoin(base, url)


def safe_name(value: str) -> str:
    value = re.sub(r"[^a-zA-Z0-9._-]+", "-", value.lower()).strip("-")
    return value[:80] or "asset"


def detect_platform(home_html: str, products: list[dict[str, Any]]) -> str:
    lowered = home_html.lower()
    if products or "cdn.shopify.com" in lowered or "shopify-section" in lowered:
        return "Shopify"
    if "woocommerce" in lowered or "wp-content/plugins/woocommerce" in lowered:
        return "WooCommerce"
    return "Unknown"


def detect_review_provider(html: str) -> str:
    lowered = html.lower()
    providers = []
    if "judge.me" in lowered or "jdgm" in lowered:
        providers.append("Judge.me")
    if "loox" in lowered:
        providers.append("Loox")
    if "yotpo" in lowered:
        providers.append("Yotpo")
    if "stamped" in lowered:
        providers.append("Stamped")
    return "+".join(providers) if providers else "Generic"


def extract_loox_config(html: str) -> tuple[str, str]:
    client_match = re.search(r"https:\\/\\/loox\.io\\/widget\\/([^\\/]+)\\/loox\.", html)
    if not client_match:
        client_match = re.search(r"https://loox\.io/widget/([^/]+)/loox\.", html)
    hash_match = re.search(r"loox_global_hash\s*=\s*['\"]([^'\"]+)", html)
    return (client_match.group(1) if client_match else "", hash_match.group(1) if hash_match else "")


def fetch_shopify_products(site: str, max_pages: int, per_page: int) -> list[dict[str, Any]]:
    products: list[dict[str, Any]] = []
    for page in range(1, max_pages + 1):
        url = urljoin(site, f"/products.json?limit={per_page}&page={page}")
        payload = request_json(url)
        page_products = payload.get("products", [])
        if not page_products:
            break
        products.extend(page_products)
        if len(page_products) < per_page:
            break
    return products


def product_from_shopify(site: str, platform: str, payload: dict[str, Any]) -> Product:
    variants = payload.get("variants") or []
    first_variant = variants[0] if variants else {}
    images = [normalize_url(site, image.get("src", "")) for image in payload.get("images", [])]
    handle = clean_text(payload.get("handle"))
    return Product(
        site=site.rstrip("/"),
        platform=platform,
        title=clean_text(payload.get("title")),
        handle=handle,
        url=urljoin(site, f"/products/{handle}"),
        price=clean_text(first_variant.get("price")),
        compare_at_price=clean_text(first_variant.get("compare_at_price")),
        image_urls=[url for url in images if url],
    )


def strip_tags(value: str) -> str:
    return clean_text(html.unescape(re.sub(r"<[^>]+>", " ", value)))


def extract_attr(value: str, attr: str) -> str:
    match = re.search(attr + r"\s*=\s*(['\"])(.*?)\1", value, re.I | re.S)
    return html.unescape(match.group(2)) if match else ""


def timestamp_to_iso(value: str) -> str:
    if not value:
        return ""
    try:
        timestamp = int(value)
        if timestamp > 10_000_000_000:
            timestamp = timestamp / 1000
        return datetime.fromtimestamp(timestamp, tz=timezone.utc).date().isoformat()
    except Exception:
        return ""


def parse_loox_reviews(page_html: str, site: str) -> list[Review]:
    reviews: list[Review] = []
    starts = list(re.finditer(r'<div[^>]+data-id="([^"]+)"[^>]*class="grid-item-wrap[^"]*"[^>]*>', page_html, re.I | re.S))
    blocks = []
    for index, match in enumerate(starts):
        end = starts[index + 1].start() if index + 1 < len(starts) else page_html.find('<div style="padding-bottom:30px"', match.end())
        if end == -1:
            end = page_html.find("</body>", match.end())
        if end == -1:
            end = len(page_html)
        blocks.append((match.group(1), page_html[match.start():end]))
    for review_id, block in blocks:
        title_match = re.search(rf'data-testid="review-{re.escape(review_id)}-title"[^>]*>(.*?)</div>', block, re.I | re.S)
        body_match = re.search(rf'data-testid="review-{re.escape(review_id)}-text"[^>]*>(.*?)</div>', block, re.I | re.S)
        rating_match = re.search(r"Rating icons:\s*([1-5])\s*/\s*5", block, re.I)
        date_match = re.search(r"data-time=['\"](\d+)['\"]", block, re.I)
        image_urls = [
            normalize_url("https://loox.io", src)
            for src in re.findall(r"<img[^>]+src=['\"]([^'\"]+)['\"]", block, re.I)
            if src and not src.startswith("data:")
        ]
        product_image = re.search(r'class="block product-box.*?<img[^>]+alt="([^"]+)"', block, re.I | re.S)
        product_title = html.unescape(product_image.group(1)) if product_image else "Loox aggregate reviews"
        body = strip_tags(body_match.group(1)) if body_match else ""
        author = strip_tags(title_match.group(1)) if title_match else ""
        author = re.sub(r"\bVerified\b", "", author).strip()
        if not body and not image_urls:
            continue
        reviews.append(
            Review(
                site=site.rstrip("/"),
                provider="Loox",
                product_title=clean_text(product_title),
                product_url=site.rstrip("/"),
                author=author,
                rating=rating_match.group(1) if rating_match else "",
                body=body,
                date=timestamp_to_iso(date_match.group(1) if date_match else ""),
                image_urls=list(dict.fromkeys(image_urls)),
            )
        )
    return reviews


def fetch_loox_reviews(site: str, client_id: str, loox_hash: str, product_id: str, max_pages: int = 20) -> list[Review]:
    if not client_id or not loox_hash or not product_id:
        return []
    reviews: list[Review] = []
    seen_ids: set[str] = set()
    for page in range(1, max_pages + 1):
        url = f"https://loox.io/widget/{client_id}/reviews?productId={product_id}&h={loox_hash}&page={page}"
        try:
            page_html = request_text_with_referer(url, site)
        except Exception:
            break
        ids = re.findall(r'data-id="([^"]+)"', page_html)
        new_ids = [item for item in ids if item not in seen_ids]
        if not new_ids:
            break
        seen_ids.update(new_ids)
        reviews.extend(parse_loox_reviews(page_html, site))
        if len(ids) < 20:
            break
    return dedupe_reviews(reviews)


def request_text_with_referer(url: str, referer: str) -> str:
    req = Request(url, headers={"User-Agent": USER_AGENT, "Accept": "text/html,*/*", "Referer": referer})
    with urlopen(req, timeout=30) as response:
        return response.read().decode("utf-8", "ignore")


async def auto_scroll(page) -> None:
    previous_height = 0
    stable_rounds = 0
    for _ in range(12):
        height = await page.evaluate("document.body.scrollHeight")
        if height == previous_height:
            stable_rounds += 1
        else:
            stable_rounds = 0
        if stable_rounds >= 2:
            break
        previous_height = height
        await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        await page.wait_for_timeout(900)


async def click_review_pagination(page) -> None:
    labels = re.compile(r"(show more|load more|more reviews|next|下一页|更多)", re.I)
    for _ in range(8):
        button = page.get_by_text(labels).first
        try:
            if not await button.is_visible(timeout=1200):
                break
            await button.click(timeout=2500)
            await page.wait_for_timeout(1200)
        except Exception:
            break


async def extract_review_dom(page, site: str, product: Product, provider: str) -> list[Review]:
    rows = await page.evaluate(
        """() => {
            const candidates = Array.from(document.querySelectorAll([
              '.jdgm-rev', '.loox-review', '.yotpo-review', '.stamped-review',
              '[class*="review"]', '[data-review-id]'
            ].join(',')));
            const seen = new Set();
            return candidates.map((node) => {
              const text = (node.innerText || '').replace(/\\s+/g, ' ').trim();
              if (!text || text.length < 12 || seen.has(text)) return null;
              seen.add(text);
              const q = (selectors) => {
                for (const selector of selectors) {
                  const found = node.querySelector(selector);
                  if (found && found.textContent && found.textContent.trim()) return found.textContent.trim();
                }
                return '';
              };
              const imgs = Array.from(node.querySelectorAll('img')).map((img) =>
                img.currentSrc || img.src || img.getAttribute('data-src') || img.getAttribute('data-original') || ''
              ).filter(Boolean);
              const aria = Array.from(node.querySelectorAll('[aria-label]')).map((el) => el.getAttribute('aria-label') || '').join(' ');
              const cls = node.className || '';
              const ratingText = `${aria} ${cls} ${text}`.match(/([1-5](?:\\.\\d)?)\\s*(?:out of|\\/|stars?|星)/i);
              return {
                author: q(['.jdgm-rev__author', '.loox-review-author', '.yotpo-user-name', '.stamped-review-header-title', '[class*="author"]']),
                rating: ratingText ? ratingText[1] : '',
                title: q(['.jdgm-rev__title', '.loox-review-title', '.yotpo-review-title', '.stamped-review-title', '[class*="title"]']),
                body: q(['.jdgm-rev__body', '.loox-review-content', '.yotpo-review-content', '.stamped-review-content-body', '[class*="content"]']) || text,
                date: q(['.jdgm-rev__timestamp', '.loox-review-date', '.yotpo-review-date', '.stamped-review-date', 'time', '[class*="date"]']),
                image_urls: imgs
              };
            }).filter(Boolean);
        }"""
    )
    reviews: list[Review] = []
    for row in rows:
        body = clean_text(row.get("body"))
        if not body:
            continue
        reviews.append(
            Review(
                site=site.rstrip("/"),
                provider=provider,
                product_title=product.title,
                product_url=product.url,
                author=clean_text(row.get("author")),
                rating=clean_text(row.get("rating")),
                title=clean_text(row.get("title")),
                body=body,
                date=clean_text(row.get("date")),
                image_urls=[normalize_url(product.url, url) for url in row.get("image_urls", [])],
            )
        )
    return dedupe_reviews(reviews)


async def enrich_product_page(page, product: Product) -> tuple[str, list[Review]]:
    await page.goto(product.url, wait_until="domcontentloaded", timeout=45000)
    await page.wait_for_timeout(1800)
    await auto_scroll(page)
    await click_review_pagination(page)
    html = await page.content()
    provider = detect_review_provider(html)
    meta = await page.evaluate(
        """() => {
          const rating = document.querySelector('[itemprop="ratingValue"], [data-rating], .jdgm-prev-badge__stars, .loox-rating, .yotpo-stars');
          const count = document.querySelector('[itemprop="reviewCount"], .jdgm-prev-badge__text, .loox-rating-label, .yotpo-bottomline .text-m');
          return {
            rating: rating ? (rating.getAttribute('content') || rating.getAttribute('data-rating') || rating.textContent || '').trim() : '',
            review_count: count ? (count.getAttribute('content') || count.textContent || '').trim() : ''
          };
        }"""
    )
    product.rating = clean_text(meta.get("rating"))
    product.review_count = clean_text(meta.get("review_count"))
    reviews = await extract_review_dom(page, product.site, product, provider)
    return provider, reviews


def dedupe_reviews(reviews: list[Review]) -> list[Review]:
    seen: set[str] = set()
    candidates: list[Review] = []
    for review in reviews:
        review.image_urls = list(dict.fromkeys(review.image_urls))
        review.downloaded_images = list(dict.fromkeys(review.downloaded_images))
        body = clean_text(review.body)
        if not body or re.fullmatch(r"[★☆\s().0-9A-Za-z]+Reviews?\)?", body, re.I):
            continue
        if not review.author and not review.image_urls and re.search(r"\(\s*\d+\s+Reviews?\s*\)", body, re.I):
            continue
        if len(body) > 500 and len(review.image_urls) > 1:
            continue
        key = "|".join(
            [
                review.site,
                review.product_url,
                review.author,
                review.rating,
                review.date,
                " ".join(review.image_urls),
                review.body[:240],
            ]
        )
        if key in seen:
            continue
        seen.add(key)
        candidates.append(review)

    output: list[Review] = []
    authored_bodies = [
        (item.site, item.product_url, clean_text(item.body))
        for item in candidates
        if item.author or item.image_urls
    ]
    for review in candidates:
        body = clean_text(review.body)
        if not review.author and not review.image_urls:
            if any(
                review.site == site and review.product_url == product_url and body and body in authored_body
                for site, product_url, authored_body in authored_bodies
            ):
                continue
        output.append(review)
    return output


def download_asset(url: str, target_dir: Path, prefix: str) -> str:
    if not url or url.startswith("data:"):
        return ""
    parsed = urlparse(url)
    suffix = Path(parsed.path).suffix.split("?")[0].lower()
    if suffix not in {".jpg", ".jpeg", ".png", ".webp", ".gif", ".avif"}:
        suffix = ".jpg"
    digest = hashlib.sha1(url.encode("utf-8")).hexdigest()[:10]
    target = target_dir / f"{safe_name(prefix)}-{digest}{suffix}"
    if target.exists():
        return str(target)
    try:
        req = Request(url, headers={"User-Agent": USER_AGENT, "Referer": f"{parsed.scheme}://{parsed.netloc}/"})
        with urlopen(req, timeout=30) as response:
            target.write_bytes(response.read())
        return str(target)
    except Exception:
        return ""


def write_csv(path: Path, rows: list[dict[str, Any]], fieldnames: list[str]) -> None:
    with path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({key: row.get(key, "") for key in fieldnames})


async def crawl(
    sites: list[str],
    output_dir: Path,
    max_pages: int,
    per_page: int,
    download_images: bool,
    http_only: bool,
) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    image_dir = output_dir / "images"
    image_dir.mkdir(parents=True, exist_ok=True)
    products: list[Product] = []
    reviews: list[Review] = []

    browser = None
    context = None
    page = None
    p = None
    if not http_only:
        p = await async_playwright().start()
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(user_agent=USER_AGENT, viewport={"width": 1440, "height": 1200})
        page = await context.new_page()

    try:
        for site in sites:
            home_html = request_text(site)
            raw_products = fetch_shopify_products(site, max_pages=max_pages, per_page=per_page)
            platform = detect_platform(home_html, raw_products)
            site_products = [product_from_shopify(site, platform, item) for item in raw_products]
            products.extend(site_products)
            loox_client_id, loox_hash = extract_loox_config(home_html)
            loox_reviews = []
            if loox_client_id and loox_hash and raw_products:
                loox_reviews = fetch_loox_reviews(site, loox_client_id, loox_hash, str(raw_products[0].get("id", "")))
                reviews.extend(loox_reviews)
            for product in site_products:
                if page is not None and not loox_reviews:
                    try:
                        _, product_reviews = await enrich_product_page(page, product)
                        reviews.extend(product_reviews)
                    except Exception as exc:
                        reviews.append(
                            Review(
                                site=site.rstrip("/"),
                                provider="Error",
                                product_title=product.title,
                                product_url=product.url,
                                body=f"SCRAPE_ERROR: {type(exc).__name__}: {exc}",
                            )
                        )
                if download_images:
                    product.downloaded_images = [
                        path
                        for path in (
                            download_asset(url, image_dir, f"{urlparse(site).netloc}-{product.handle}")
                            for url in product.image_urls
                        )
                        if path
                    ]
    finally:
        if context is not None:
            await context.close()
        if browser is not None:
            await browser.close()
        if p is not None:
            await p.stop()

    if download_images:
        for review in reviews:
            review.downloaded_images = [
                path
                for path in (
                    download_asset(url, image_dir, f"review-{safe_name(review.product_title)}")
                    for url in review.image_urls
                )
                if path
            ]

    product_rows = []
    for product in products:
        row = asdict(product)
        row["image_urls"] = " | ".join(product.image_urls)
        row["downloaded_images"] = " | ".join(product.downloaded_images)
        product_rows.append(row)

    review_rows = []
    for review in dedupe_reviews(reviews):
        row = asdict(review)
        row["image_urls"] = " | ".join(review.image_urls)
        row["downloaded_images"] = " | ".join(review.downloaded_images)
        review_rows.append(row)

    write_csv(
        output_dir / "products.csv",
        product_rows,
        ["site", "platform", "title", "handle", "url", "price", "compare_at_price", "currency", "rating", "review_count", "image_urls", "downloaded_images"],
    )
    write_csv(
        output_dir / "reviews.csv",
        review_rows,
        ["site", "provider", "product_title", "product_url", "author", "rating", "title", "body", "date", "image_urls", "downloaded_images"],
    )
    payload = {"products": product_rows, "reviews": review_rows}
    (output_dir / "data.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return payload


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Shopify/WooCommerce competitor review crawler.")
    parser.add_argument("--site", action="append", dest="sites", help="Target site URL. Repeat for multiple sites.")
    parser.add_argument("--output", default="output", help="Output directory.")
    parser.add_argument("--max-pages", type=int, default=5, help="Max product-list pages per site.")
    parser.add_argument("--per-page", type=int, default=250, help="Products per Shopify products.json page.")
    parser.add_argument("--no-images", action="store_true", help="Do not download product/review images.")
    parser.add_argument("--http-only", action="store_true", help="Skip Playwright rendering and export products/images only.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    sites = args.sites or DEFAULT_SITES
    output_dir = Path(args.output)
    payload = asyncio.run(
        crawl(
            sites=sites,
            output_dir=output_dir,
            max_pages=args.max_pages,
            per_page=args.per_page,
            download_images=not args.no_images,
            http_only=args.http_only,
        )
    )
    print(f"Products: {len(payload['products'])}")
    print(f"Reviews: {len(payload['reviews'])}")
    print(f"Output: {output_dir.resolve()}")


if __name__ == "__main__":
    main()
