#!/usr/bin/env python3
"""
Streamlit site crawler

Requirements:
  pip install streamlit requests beautifulsoup4 pandas

Run:
  streamlit run site_crawler_streamlit.py

What it does:
  - Accepts a website URL
  - Respects robots.txt (simple check)
  - Crawls only internal links up to max_pages
  - Extracts page title, path, and visible text content
  - Shows a table and allows CSV download
  - Highlights pages whose paths contain common page keywords (about, contact, service, etc.)

Notes:
  - This is a polite crawler for small sites and demos. Don't use it to scrape large sites rapidly.
  - You can increase max_pages but be mindful of load and robots rules.
"""

from urllib.parse import urljoin, urlparse
import time
import re
import requests
from bs4 import BeautifulSoup
import pandas as pd
import streamlit as st
import urllib.robotparser as robotparser

# ---------- Helpers ----------
HEADERS = {
    "User-Agent": "SiteCrawlerBot/1.0 (+https://example.com)"
}

COMMON_PAGE_KEYWORDS = [
    "about", "contact", "service", "services", "products", "pricing", "team", "blog", "privacy", "terms", "faq",
]

@st.cache_data
def fetch_url(url, timeout=8):
    try:
        resp = requests.get(url, headers=HEADERS, timeout=timeout)
        resp.raise_for_status()
        return resp.text
    except Exception as e:
        return None


def is_same_domain(base_netloc, link):
    try:
        p = urlparse(link)
        # empty netloc means relative URL
        if not p.netloc:
            return True
        return p.netloc == base_netloc
    except Exception:
        return False


def extract_links(html, base_url):
    soup = BeautifulSoup(html, "html.parser")
    anchors = soup.find_all("a", href=True)
    links = set()
    for a in anchors:
        href = a.get("href").split("#")[0].strip()
        if not href:
            continue
        # Skip mailto:, tel:, javascript:
        if href.startswith("mailto:") or href.startswith("tel:") or href.startswith("javascript:"):
            continue
        full = urljoin(base_url, href)
        links.add(full)
    return links


def extract_text(html):
    soup = BeautifulSoup(html, "html.parser")
    # remove scripts/styles
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()
    text = soup.get_text(separator=" ")
    # collapse whitespace
    text = re.sub(r"\s+", " ", text).strip()
    # optionally truncate long text for display
    return text


def extract_title(html):
    soup = BeautifulSoup(html, "html.parser")
    if soup.title and soup.title.string:
        return soup.title.string.strip()
    # fallback: first h1
    h1 = soup.find("h1")
    if h1 and h1.get_text():
        return h1.get_text().strip()
    return "(no title)"


def allowed_by_robots(base_url, user_agent="*"):
    try:
        parsed = urlparse(base_url)
        robots_url = f"{parsed.scheme}://{parsed.netloc}/robots.txt"
        rp = robotparser.RobotFileParser()
        rp.set_url(robots_url)
        rp.read()
        return rp
    except Exception:
        return None


# ---------- Crawler ----------

def crawl_site(start_url, max_pages=50):
    parsed = urlparse(start_url)
    base_netloc = parsed.netloc
    base_root = f"{parsed.scheme}://{parsed.netloc}"

    rp = allowed_by_robots(start_url)

    to_visit = [start_url]
    seen = set()
    results = []

    while to_visit and len(results) < max_pages:
        url = to_visit.pop(0)
        if url in seen:
            continue
        seen.add(url)

        # robots check
        if rp:
            try:
                allowed = rp.can_fetch(HEADERS["User-Agent"], url)
            except Exception:
                allowed = True
        else:
            allowed = True

        if not allowed:
            results.append({"url": url, "status": "blocked_by_robots", "title": "", "content": ""})
            continue

        html = fetch_url(url)
        if not html:
            results.append({"url": url, "status": "failed", "title": "", "content": ""})
            continue

        title = extract_title(html)
        text = extract_text(html)

        results.append({"url": url, "status": "ok", "title": title, "content": text})

        # extract links
        links = extract_links(html, base_root)
        for link in links:
            # keep same domain only
            if not is_same_domain(base_netloc, link):
                continue
            # normalize: strip query params for crawling ease (optional)
            parsed_l = urlparse(link)
            norm = parsed_l._replace(fragment="").geturl()
            if norm not in seen and norm not in to_visit:
                to_visit.append(norm)

        # be polite
        time.sleep(0.2)

    return results


# ---------- Streamlit UI ----------

def main():
    st.title("URL-based Site Crawler — Streamlit")
    st.write("Enter a website URL and the app will crawl internal pages (home, about, contact, etc.) and extract content.")

    col1, col2 = st.columns([3,1])
    with col1:
        url = st.text_input("Start URL", value="https://example.com")
    with col2:
        max_pages = st.number_input("Max pages", min_value=1, max_value=500, value=30)

    start = st.button("Crawl site")

    if start and url:
        with st.spinner("Crawling — this may take a bit..."):
            try:
                data = crawl_site(url, max_pages=max_pages)
            except Exception as e:
                st.error(f"Crawler failed: {e}")
                return

        df = pd.DataFrame(data)
        if df.empty:
            st.info("No pages found or crawl blocked.")
            return

        # derive path and keyword tags
        df["path"] = df["url"].apply(lambda u: urlparse(u).path or "/")

        def keyword_tag(path):
            path_low = path.lower()
            hits = [k for k in COMMON_PAGE_KEYWORDS if k in path_low]
            return ", ".join(hits) if hits else ""

        df["keywords_found"] = df["path"].apply(keyword_tag)
        # short preview
        df["preview"] = df["content"].apply(lambda t: (t[:500] + "...") if len(t) > 500 else t)

        st.success(f"Crawled {len(df)} pages")

        # show the pages that match common names first
        matching = df[df["keywords_found"]!=""]
        if not matching.empty:
            st.subheader("Pages that look like Home/About/Contact/Service/etc.")
            st.table(matching[["url", "title", "keywords_found"]].head(20))

        st.subheader("All pages found")
        st.dataframe(df[["url", "title", "path", "keywords_found", "preview", "status"]])

        # allow download
        csv = df.to_csv(index=False)
        st.download_button("Download CSV", data=csv, file_name="site_pages.csv", mime="text/csv")

        # let user click to view full content of a row
        st.subheader("View page content")
        idx = st.number_input("Row index", min_value=0, max_value=max(0, len(df)-1), value=0)
        row = df.iloc[int(idx)]
        st.markdown(f"### {row['title']}")
        st.write(f"**URL:** {row['url']}")
        st.write(row["content"])


if __name__ == "__main__":
    main()
