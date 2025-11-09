#!/usr/bin/env python3
"""
Download and render Notion hosted pages.

Notion hosted pages are client-side rendered with React, so this script uses
Selenium to render the JavaScript and extract the full HTML content.
"""

import os
import time
from typing import Tuple
from urllib.parse import unquote, urlparse
from urllib.request import Request, urlopen

from bs4 import BeautifulSoup

try:
    from selenium import webdriver
    from selenium.webdriver.chrome.options import Options
    from selenium.webdriver.common.by import By
    from selenium.webdriver.support import expected_conditions as EC
    from selenium.webdriver.support.ui import WebDriverWait

    SELENIUM_AVAILABLE = True
except ImportError:
    SELENIUM_AVAILABLE = False


def download_notion_page(url: str, temp_dir: str) -> Tuple[str, str]:
    """
    Download a Notion hosted page and save it as HTML.

    Notion hosted pages are client-side rendered with React, so we need
    Selenium to render the JavaScript and get the full HTML content.

    Args:
        url: URL of the Notion hosted page
        temp_dir: Temporary directory to save files

    Returns:
        Tuple of (html_file_path, assets_dir_path)

    Raises:
        RuntimeError: If Selenium is not available or rendering fails
    """
    print(f"🌐 Downloading Notion page from: {url}")

    if not SELENIUM_AVAILABLE:
        raise RuntimeError(
            "Selenium is required to download Notion hosted pages.\n"
            "Install it with: pip install selenium\n"
            "You also need Chrome/Chromium browser installed."
        )

    # Set up Chrome options for headless browsing
    chrome_options = Options()
    chrome_options.add_argument("--headless")
    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument("--disable-dev-shm-usage")
    chrome_options.add_argument("--disable-gpu")
    chrome_options.add_argument(
        "user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
    )

    try:
        # Initialize the Chrome driver
        driver = webdriver.Chrome(options=chrome_options)
        driver.get(url)

        # Wait for the page to load - try multiple selectors
        print("  ⏳ Waiting for page to render...")

        # Try waiting for different elements that indicate page is loaded
        loaded = False
        wait_conditions = [
            (By.TAG_NAME, "article"),
            (By.CSS_SELECTOR, "div[data-block-id]"),  # Notion block
            (By.CLASS_NAME, "notion-page-content"),
            (By.CSS_SELECTOR, ".notion-app-inner"),
        ]

        for by, selector in wait_conditions:
            try:
                WebDriverWait(driver, 5).until(
                    EC.presence_of_element_located((by, selector))
                )
                loaded = True
                print(f"  ✓ Found element: {selector}")
                break
            except:
                continue

        if not loaded:
            # Fallback: just wait for body to be present and give it time
            print("  ⚠️  Standard selectors not found, using fallback wait...")
            WebDriverWait(driver, 10).until(
                EC.presence_of_element_located((By.TAG_NAME, "body"))
            )

        # Additional wait for dynamic content to load
        time.sleep(3)

        # Get the fully rendered HTML
        html_content = driver.page_source
        driver.quit()

        print("  ✅ Page rendered successfully")

    except Exception as e:
        try:
            driver.quit()
        except:
            pass
        raise RuntimeError(f"Failed to render page with Selenium: {e}")

    # Create assets directory
    assets_dir = os.path.join(temp_dir, "assets")
    os.makedirs(assets_dir, exist_ok=True)

    # Parse HTML to download media files
    soup = BeautifulSoup(html_content, "html.parser")

    # Create headers for downloads
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
    }

    # Download images
    downloaded_count = 0
    for img in soup.find_all("img"):
        src = img.get("src")
        if src:
            # Handle relative URLs
            if not src.startswith("http"):
                if src.startswith("//"):
                    src = "https:" + src
                elif src.startswith("/"):
                    parsed_url = urlparse(url)
                    src = f"{parsed_url.scheme}://{parsed_url.netloc}{src}"
                else:
                    continue

            try:
                # Get filename from URL
                parsed_url = urlparse(src)
                filename = os.path.basename(unquote(parsed_url.path))
                if not filename or filename == "/":
                    filename = f"image_{downloaded_count}.png"

                # Download file with headers
                local_path = os.path.join(assets_dir, filename)
                req = Request(src, headers=headers)
                with urlopen(req) as response:
                    with open(local_path, "wb") as f:
                        f.write(response.read())

                # Update src to local path
                img["src"] = filename
                downloaded_count += 1
                print(f"  📥 Downloaded image: {filename}")
            except Exception as e:
                print(f"  ⚠️  Failed to download image {src}: {e}")

    # Download audio files
    for audio in soup.find_all("audio"):
        src = audio.get("src")
        if src:
            if not src.startswith("http"):
                if src.startswith("//"):
                    src = "https:" + src
                elif src.startswith("/"):
                    parsed_url = urlparse(url)
                    src = f"{parsed_url.scheme}://{parsed_url.netloc}{src}"
                else:
                    continue

            try:
                parsed_url = urlparse(src)
                filename = os.path.basename(unquote(parsed_url.path))
                if not filename:
                    filename = f"audio_{downloaded_count}.mp3"

                local_path = os.path.join(assets_dir, filename)
                req = Request(src, headers=headers)
                with urlopen(req) as response:
                    with open(local_path, "wb") as f:
                        f.write(response.read())

                audio["src"] = filename
                downloaded_count += 1
                print(f"  📥 Downloaded audio: {filename}")
            except Exception as e:
                print(f"  ⚠️  Failed to download audio {src}: {e}")

    # Save modified HTML
    html_file = os.path.join(temp_dir, "notion_page.html")
    with open(html_file, "w", encoding="utf-8") as f:
        f.write(str(soup))

    # Check if we actually got any content
    article = soup.find("article")
    page_body = soup.find("div", class_="page-body")

    if not article and not page_body:
        print(
            "\n⚠️  WARNING: Downloaded page doesn't have the expected export structure."
        )
        print(
            "   Notion hosted pages may not contain all nested content (details/callouts)."
        )
        print("   For best results:")
        print("   1. Open your Notion page")
        print("   2. Click '...' menu → Export")
        print("   3. Choose 'HTML' format and 'Include subpages'")
        print("   4. Use the downloaded ZIP file with this script\n")

    print(f"✅ Downloaded page and {len(os.listdir(assets_dir))} media files")

    return html_file, assets_dir


if __name__ == "__main__":
    import sys
    import tempfile

    if len(sys.argv) != 2:
        print("Usage: python download_notion_page.py <notion_url>")
        sys.exit(1)

    url = sys.argv[1]
    temp_dir = tempfile.mkdtemp()

    try:
        html_file, assets_dir = download_notion_page(url, temp_dir)
        print(f"\n📄 HTML saved to: {html_file}")
        print(f"📁 Assets saved to: {assets_dir}")
    except Exception as e:
        print(f"❌ Error: {e}")
        sys.exit(1)
