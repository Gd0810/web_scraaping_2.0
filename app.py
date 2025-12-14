#!/usr/bin/env python3
"""
Streamlit site crawler — body-only, sectioned output

This version:
 - Ignores <head> entirely (no meta/seo tags)
 - Extracts only content inside <body>
 - Splits each page into sections based on headings (h1-h4)
 - Keeps an 'Intro' section for content before the first heading
 - Preserves lists as bullet lines and paragraphs as compact text
 - Shows per-page sections in the UI and allows CSV/JSON download

Run:
    streamlit run site_crawler_streamlit.py

Deps:
    pip install streamlit requests beautifulsoup4 pandas
"""
from urllib.parse import urljoin, urlparse
import time
import re
import requests
from bs4 import BeautifulSoup, Tag
import pandas as pd
import streamlit as st
import urllib.robotparser as robotparser
import json

HEADERS = {"User-Agent": "SiteCrawlerBot/1.0 (+https://example.com)"}

@st.cache_data
def fetch_url(url: str, timeout: int = 8) -> str | None:
    try:
        r = requests.get(url, headers=HEADERS, timeout=timeout)
        r.raise_for_status()
        return r.text
    except Exception:
        return None

def allowed_by_robots(base_url: str):
    try:
        parsed = urlparse(base_url)
        robots_url = f"{parsed.scheme}://{parsed.netloc}/robots.txt"
        rp = robotparser.RobotFileParser()
        rp.set_url(robots_url)
        rp.read()
        return rp
    except Exception:
        return None

def is_same_domain(base_netloc: str, link: str) -> bool:
    try:
        p = urlparse(link)
        if not p.netloc:
            return True
        return p.netloc == base_netloc
    except Exception:
        return False

def extract_links(html: str, base_url: str):
    """Light link discovery (anchors only) — keeps crawler polite/simple."""
    soup = BeautifulSoup(html, "html.parser")
    links = set()
    for a in soup.find_all("a", href=True):
        href = a.get("href").split("#")[0].strip()
        if not href:
            continue
        if href.startswith(("mailto:", "tel:", "javascript:")):
            continue
        links.add(urljoin(base_url, href))
    return links

# ---------- BODY SECTIONING ----------
def _clean_tag(tag: Tag):
    """Remove scripts/styles/noscript from a subtree in-place."""
    for bad in tag.find_all(["script", "style", "noscript", "iframe"]):
        bad.decompose()
    return tag

def _elem_to_text(elem: Tag) -> str:
    """Convert a tag to readable text:
       - lists -> bullet lines
       - paragraphs, divs -> single-line cleaned text
       - keeps minimal whitespace
    """
    name = elem.name.lower()
    if name in ("ul", "ol"):
        items = []
        for li in elem.find_all("li"):
            t = li.get_text(separator=" ", strip=True)
            if t:
                items.append("- " + re.sub(r"\s+", " ", t))
        return "\n".join(items).strip()
    else:
        text = elem.get_text(separator=" ", strip=True)
        text = re.sub(r"\s+", " ", text).strip()
        return text

def extract_body_sections(html: str):
    """
    Return a list of sections for the page:
      [ {"heading": "Intro" or heading_text, "content": "..."}, ... ]
    Only content from <body> is used.
    """
    soup = BeautifulSoup(html, "html.parser")
    body = soup.body or soup  # fallback to full soup if body missing
    _clean_tag(body)

    sections = []
    current_heading = "Intro"
    current_lines = []

    # iterate only over direct children of body to preserve top-level structure
    for child in body.children:
        if not isinstance(child, Tag):
            # text node at top-level; add to current
            txt = (child.string or "").strip()
            if txt:
                current_lines.append(re.sub(r"\s+", " ", txt))
            continue

        tag_name = child.name.lower()

        # treat headings as section delimiters
        if re.match(r"h[1-4]$", tag_name):
            # flush previous
            if current_lines:
                content = "\n\n".join([l for l in current_lines if l])
                sections.append({"heading": current_heading, "content": content})
            # start new section
            current_heading = child.get_text(separator=" ", strip=True)
            current_lines = []
            continue

        # for other tags collect texts (lists, paragraphs, divs, sections)
        if tag_name in ("p", "div", "section", "article", "ul", "ol", "address"):
            txt = _elem_to_text(child)
            if txt:
                current_lines.append(txt)
            continue

        # for other tags (nav, footer, header) skip to avoid repeated boilerplate
        if tag_name in ("nav", "footer", "header", "form", "script", "style"):
            continue

        # fallback: extract text
        txt = child.get_text(separator=" ", strip=True)
        if txt:
            current_lines.append(re.sub(r"\s+", " ", txt))

    # flush remaining
    if current_lines:
        content = "\n\n".join([l for l in current_lines if l])
        sections.append({"heading": current_heading, "content": content})
    # If no sections found, still return at least an empty Intro
    if not sections:
        sections = [{"heading": "Intro", "content": ""}]
    return sections

# ---------- Crawler ----------
def crawl_site(start_url: str, max_pages: int = 50):
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
            continue

        html = fetch_url(url)
        if not html:
            continue

        sections = extract_body_sections(html)
        title = ""
        # get visible title from first H1/H2 in body (avoid head/title)
        soup = BeautifulSoup(html, "html.parser")
        if soup.body:
            h = soup.body.find(re.compile(r"^h[1-2]$", re.I))
            if h:
                title = h.get_text(strip=True)
        if not title:
            # fallback to domain
            title = urlparse(url).netloc

        results.append({
            "url": url,
            "title": title,
            "path": urlparse(url).path or "/",
            "sections": sections
        })

        # discover links (anchors)
        links = extract_links(html, base_root)
        for link in links:
            if not is_same_domain(base_netloc, link):
                continue
            parsed_l = urlparse(link)
            norm = parsed_l._replace(fragment="").geturl()
            if norm not in seen and norm not in to_visit:
                to_visit.append(norm)

        time.sleep(0.2)

    return results

# ---------- Streamlit UI ----------
def main():
    st.title("Site Crawler — body-only, sectioned output")
    st.write("Crawl a site and get per-page sections (Intro + H1–H4 sections). Head/meta tags are ignored.")

    col1, col2 = st.columns([3,1])
    with col1:
        url = st.text_input("Start URL", value="https://example.com")
    with col2:
        max_pages = st.number_input("Max pages", min_value=1, max_value=500, value=30)

    if st.button("Crawl site") and url:
        with st.spinner("Crawling — extracting body sections..."):
            data = crawl_site(url, max_pages=max_pages)

        if not data:
            st.info("No pages found or fetch blocked.")
            return

        # Show summary table
        df = pd.DataFrame([{"title": r["title"], "path": r["path"], "url": r["url"], "num_sections": len(r["sections"])} for r in data])
        st.subheader("Pages found")
        st.dataframe(df)

        st.subheader("View page sections")
        idx = st.number_input("Row index", min_value=0, max_value=max(0, len(data)-1), value=0)
        row = data[int(idx)]
        st.markdown(f"## {row['title']}")
        st.write(f"**URL:** {row['url']}")
        st.write(f"**Path:** {row['path']}")
        # render sections
        for sec in row["sections"]:
            st.markdown(f"### {sec['heading']}")
            # show lists & paragraphs with preserved newlines
            st.text(sec["content"] or "")

        # downloads: JSON of sections
        blob = json.dumps(data, ensure_ascii=False, indent=2)
        st.download_button("Download JSON", data=blob, file_name="site_sections.json", mime="application/json")
        # also CSV: flatten sections to rows
        rows = []
        for r in data:
            for i, s in enumerate(r["sections"]):
                rows.append({"url": r["url"], "path": r["path"], "title": r["title"], "section_index": i, "heading": s["heading"], "content": s["content"]})
        df_flat = pd.DataFrame(rows)
        csv = df_flat.to_csv(index=False)
        st.download_button("Download CSV", data=csv, file_name="site_sections.csv", mime="text/csv")

if __name__ == "__main__":
    main()
