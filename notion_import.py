#!/usr/bin/env python3
"""
Notion to Anki Converter

Converts Notion HTML exports to Anki flashcard decks (.apkg) or CSV files.
Supports subdecks, media files (images, audio), and hashtag tagging.
"""

import argparse
import csv
import os
import re
import shutil
import tempfile
import zipfile
from typing import Dict, List, Optional, Tuple
from urllib.parse import unquote

import genanki
from bs4 import BeautifulSoup, NavigableString, Tag

try:
    from download_notion_page import download_notion_page

    NOTION_DOWNLOAD_AVAILABLE = True
except ImportError:
    NOTION_DOWNLOAD_AVAILABLE = False

# Constants
AUDIO_EXTENSIONS = (".mp3", ".wav", ".ogg", ".m4a")
IMAGE_EXTENSIONS = (".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp")
DEFAULT_SUBDECK_NAME = "Default"
ANKI_MODEL_ID = 1607392319
CARD_STYLE_CSS_FILE = os.path.join(os.path.dirname(__file__), "card_style.css")
CARD_STYLE_CSS = open(CARD_STYLE_CSS_FILE, "r", encoding="utf-8").read()


class NotionCard:
    """Represents a single flashcard extracted from Notion."""

    def __init__(self, front: str, back: str, tags: List[str], media_files: List[str]):
        self.front = front
        self.back = back
        self.tags = tags
        self.media_files = media_files

    def to_tuple(self) -> Tuple[str, str, List[str], List[str]]:
        """Convert to tuple format for backwards compatibility."""
        return (self.front, self.back, self.tags, self.media_files)


def extract_hashtags(element: Tag, keep_tags: bool = True) -> Tuple[Tag, List[str]]:
    """
    Extract hashtags from visible text inside a BeautifulSoup element.

    Args:
        element: BeautifulSoup element to process
        keep_tags: If False, removes hashtags from the text

    Returns:
        Tuple of (modified element, list of extracted tags)
    """
    tags = []

    for text_node in element.find_all(string=True):
        # Skip script and style tags
        if text_node.parent.name in {"script", "style"}:
            continue

        # Find all hashtags in this text node
        found_tags = re.findall(r"#(\w+)", text_node)
        tags.extend(tag.lower() for tag in found_tags)

        # Remove hashtags if requested
        if not keep_tags and found_tags:
            cleaned_text = re.sub(r"#\w+", "", text_node)
            cleaned_text = re.sub(r"\s+", " ", cleaned_text).strip()
            text_node.replace_with(NavigableString(cleaned_text))

    return element, tags


def process_images(element: Tag, assets_dir: str) -> List[str]:
    """
    Process images in HTML element, updating src paths and collecting media files.

    Args:
        element: BeautifulSoup element containing images
        assets_dir: Directory containing media assets

    Returns:
        List of full paths to media files
    """
    media_files = []

    for img in element.find_all("img"):
        src = img.get("src")
        if not src:
            continue

        # Decode URL encoding and get filename
        decoded_src = unquote(src)
        filename = os.path.basename(decoded_src)
        full_path = os.path.join(assets_dir, filename)

        if os.path.exists(full_path):
            media_files.append(full_path)
            img["src"] = filename  # Update to relative path for Anki
        else:
            print(f"⚠️ Missing media: {full_path}")

    return media_files


def process_audio_files(element: Tag, assets_dir: str) -> List[str]:
    """
    Process audio file links, converting them to Anki sound format.

    Args:
        element: BeautifulSoup element containing audio links
        assets_dir: Directory containing media assets

    Returns:
        List of full paths to audio files
    """
    media_files = []

    for link in element.find_all("a"):
        href = link.get("href", "")
        if not any(href.lower().endswith(ext) for ext in AUDIO_EXTENSIONS):
            continue

        filename = os.path.basename(href)
        full_path = os.path.join(assets_dir, filename)

        if os.path.exists(full_path):
            media_files.append(full_path)
            # Replace link with Anki sound format
            sound_span = element.new_tag("span")
            sound_span.string = f"[sound:{filename}]"
            link.replace_with(sound_span)

    return media_files


def process_media_in_html(element: Tag, assets_dir: str) -> Tuple[str, List[str]]:
    """
    Process all media (images, audio) in HTML element.

    Args:
        element: BeautifulSoup element to process
        assets_dir: Directory containing media assets

    Returns:
        Tuple of (HTML string, list of media file paths)
    """
    media_files = []

    # Process images
    media_files.extend(process_images(element, assets_dir))

    # Process audio files (currently commented out in original)
    # media_files.extend(process_audio_files(element, assets_dir))

    return str(element), media_files


def extract_zip_file(zip_path: str, temp_dir: str) -> Tuple[str, str]:
    """
    Extract a ZIP file and locate the HTML file and assets directory.

    Handles nested ZIP files (ZIP within ZIP).

    Args:
        zip_path: Path to the ZIP file
        temp_dir: Temporary directory for extraction

    Returns:
        Tuple of (HTML file path, assets directory path)

    Raises:
        FileNotFoundError: If no HTML file is found in the ZIP
    """
    # Extract main ZIP
    with zipfile.ZipFile(zip_path, "r") as zip_ref:
        zip_ref.extractall(temp_dir)

    # Check for nested ZIPs
    inner_zips = [f for f in os.listdir(temp_dir) if f.endswith(".zip")]

    if inner_zips:
        inner_extract_dir = os.path.join(temp_dir, "extracted")
        os.makedirs(inner_extract_dir, exist_ok=True)

        for inner_zip in inner_zips:
            inner_zip_path = os.path.join(temp_dir, inner_zip)
            with zipfile.ZipFile(inner_zip_path, "r") as zip_ref:
                zip_ref.extractall(inner_extract_dir)

        work_dir = inner_extract_dir
    else:
        work_dir = temp_dir

    # Find HTML file
    html_files = []
    for root, _, files in os.walk(work_dir):
        for file in files:
            if file.endswith((".html", ".htm")):
                html_files.append(os.path.join(root, file))

    if not html_files:
        raise FileNotFoundError("No HTML file found in ZIP")

    if len(html_files) > 1:
        print(f"⚠️  Multiple HTML files found, using {os.path.basename(html_files[0])}")

    html_file = html_files[0]
    assets_dir = os.path.dirname(html_file)

    return html_file, assets_dir


def extract_css_from_html(html_path: str) -> str:
    """
    Extract CSS from the Notion HTML file.

    Args:
        html_path: Path to the HTML file

    Returns:
        CSS content as a string
    """
    with open(html_path, "r", encoding="utf-8") as f:
        soup = BeautifulSoup(f, "html.parser")

    style_tag = soup.find("style")
    return style_tag.string if style_tag else ""


def parse_callout(
    callout: Tag, assets_dir: str, keep_tags: bool = True
) -> Optional[NotionCard]:
    """
    Parse a single callout figure into a flashcard.

    Takes the first element inside the first div as front text,
    rest as back HTML.

    Args:
        callout: BeautifulSoup callout figure element
        assets_dir: Directory containing media assets
        keep_tags: Whether to keep hashtags in the text

    Returns:
        NotionCard object or None if parsing fails
    """
    # Find the first div inside callout
    first_div = callout.find("div")
    if not first_div:
        return None

    # Extract hashtags
    first_div, tags = extract_hashtags(first_div, keep_tags=keep_tags)

    # Find the first element inside that div
    first_element = None
    for child in first_div.children:
        if hasattr(child, "name") and child.name:  # Skip NavigableStrings
            first_element = child
            break

    if not first_element:
        return None

    # Extract front text from first element
    front_text = first_element.get_text(strip=True)
    if not front_text:
        return None

    # Remove the first element from the div to process the rest as back
    first_element.extract()

    # Process remaining content as back HTML with media
    back_html, media_files = process_media_in_html(first_div, assets_dir)

    # Make tags unique and lowercase
    unique_tags = sorted(set(tag.lower() for tag in tags))

    return NotionCard(
        front=front_text, back=back_html, tags=unique_tags, media_files=media_files
    )


def parse_html_file(
    html_path: str, assets_dir: str, keep_tags: bool = True
) -> Tuple[str, Dict[str, List[NotionCard]], str]:
    """
    Extract cards organized by subdecks from Notion HTML export.

    Structure:
    - details > summary = Subdeck name
    - figure.callout inside details > indented = Card in that subdeck
    - figure.callout outside details = Card in Default subdeck
    - First element in callout = Front text
    - Remaining content = Back HTML

    Args:
        html_path: Path to the HTML file
        assets_dir: Directory containing media assets
        keep_tags: Whether to keep hashtags in the text

    Returns:
        Tuple of (deck_name, subdecks_dict, css_string)
    """
    with open(html_path, "r", encoding="utf-8") as f:
        soup = BeautifulSoup(f, "html.parser")

    subdecks: Dict[str, List[NotionCard]] = {}
    deck_name = "Notion Deck"

    # Extract CSS
    css = extract_css_from_html(html_path)

    # Find main article
    article = soup.find("article")
    if not article:
        return deck_name, subdecks, css

    # Extract deck name from page title
    header = article.select_one("header > h1")
    if header:
        deck_name = header.get_text(strip=True)

    # Find page body
    page_body = article.find("div", class_="page-body")
    if not page_body:
        return deck_name, subdecks, css

    # Process top-level elements
    for element in page_body.find_all(["details", "figure"], recursive=False):
        if element.name == "details":
            _process_details_subdeck(element, subdecks, assets_dir, keep_tags)
        elif element.name == "figure" and "callout" in element.get("class", []):
            _process_standalone_callout(element, subdecks, assets_dir, keep_tags)

    return deck_name, subdecks, css


def _process_details_subdeck(
    details: Tag,
    subdecks: Dict[str, List[NotionCard]],
    assets_dir: str,
    keep_tags: bool,
) -> None:
    """Process a details element as a subdeck."""
    summary = details.find("summary")
    if not summary:
        return

    subdeck_name = summary.get_text(strip=True)
    if subdeck_name not in subdecks:
        subdecks[subdeck_name] = []

    # Find all callouts within this detail's indented section
    indented = details.find("div", class_="indented")
    if not indented:
        return

    callouts = indented.find_all("figure", class_="callout", recursive=False)
    for callout in callouts:
        card = parse_callout(callout, assets_dir, keep_tags)
        if card:
            subdecks[subdeck_name].append(card)


def _process_standalone_callout(
    callout: Tag,
    subdecks: Dict[str, List[NotionCard]],
    assets_dir: str,
    keep_tags: bool,
) -> None:
    """Process a callout outside of details as Default subdeck."""
    if DEFAULT_SUBDECK_NAME not in subdecks:
        subdecks[DEFAULT_SUBDECK_NAME] = []

    card = parse_callout(callout, assets_dir, keep_tags)
    if card:
        subdecks[DEFAULT_SUBDECK_NAME].append(card)


def export_csv(
    deck_name: str, subdecks: Dict[str, List[NotionCard]], out_path: str
) -> None:
    """
    Export the extracted deck structure to CSV for inspection.

    Args:
        deck_name: Name of the main deck
        subdecks: Dictionary of subdeck names to lists of cards
        out_path: Output CSV file path
    """
    with open(out_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["Deck", "Subdeck", "Front", "Back", "Tags"])

        for subdeck_name, cards in subdecks.items():
            for card in cards:
                front, back, tags, _ = card.to_tuple()
                tags_str = ", ".join(tags)
                writer.writerow([deck_name, subdeck_name, front, back, tags_str])

    print(f"✅ Wrote CSV: {out_path}")


def export_apkg(
    deck_name: str, subdecks: Dict[str, List[NotionCard]], out_path: str, css: str = ""
) -> None:
    """
    Export cards to Anki package (.apkg) with subdeck structure.

    Args:
        deck_name: Name of the main deck
        subdecks: Dictionary of subdeck names to lists of cards
        out_path: Output .apkg file path
        css: CSS styling from Notion export
    """
    # Combine Notion CSS with custom card styling
    card_css = css + "\n" + CARD_STYLE_CSS

    # Create Anki model with custom styling
    model = genanki.Model(
        1607392319,
        "Notion Basic Model",
        fields=[{"name": "Front"}, {"name": "Back"}],
        templates=[
            {
                "name": "Card 1",
                "qfmt": '<div class="front">{{Front}}</div>',
                "afmt": '{{FrontSide}}<hr id=answer><div class="back">{{Back}}</div>',
            }
        ],
        css=card_css,
    )

    all_decks: List[genanki.Deck] = []
    all_media: List[str] = []
    total_cards = 0

    # Create decks and add cards
    for subdeck_name, cards in subdecks.items():
        # Build full deck name with subdeck hierarchy
        full_deck_name = (
            f"{deck_name}::{subdeck_name}"
            if subdeck_name != DEFAULT_SUBDECK_NAME
            else deck_name
        )

        # Generate unique deck ID from name
        deck_id = abs(hash(full_deck_name)) % (10**10)
        deck = genanki.Deck(deck_id, full_deck_name)

        # Add cards to deck
        for card in cards:
            front, back, tags, media_files = card.to_tuple()
            note = genanki.Note(model=model, fields=[front, back], tags=tags)
            deck.add_note(note)
            all_media.extend(media_files)
            total_cards += 1

        all_decks.append(deck)

    # Create and write package
    pkg = genanki.Package(all_decks)
    pkg.media_files = list(set(all_media))  # Remove duplicates
    pkg.write_to_file(out_path)

    print(
        f"✅ Wrote Anki package: {out_path} "
        f"({total_cards} cards, {len(subdecks)} subdeck(s), "
        f"{len(pkg.media_files)} media files)"
    )


def main() -> None:
    """Main entry point for the Notion to Anki converter."""
    parser = argparse.ArgumentParser(
        description="Convert Notion HTML export to Anki flashcard deck"
    )
    parser.add_argument(
        "-f",
        "--file",
        required=True,
        help="Input ZIP file, HTML file, or Notion hosted page URL",
    )
    parser.add_argument(
        "-o", "--output", required=True, help="Output .apkg or .csv file"
    )
    parser.add_argument(
        "--keep-tags",
        action="store_true",
        default=True,
        help="Keep hashtags in card text (default: True)",
    )
    parser.add_argument(
        "--remove-tags",
        dest="keep_tags",
        action="store_false",
        help="Remove hashtags from card text",
    )
    args = parser.parse_args()

    temp_dir = None
    try:
        # Determine input type and process accordingly
        if args.file.startswith("http://") or args.file.startswith("https://"):
            # URL - download Notion hosted page
            if not NOTION_DOWNLOAD_AVAILABLE:
                raise SystemExit(
                    "❌ URL download not available.\n"
                    "Please ensure download_notion_page.py is in the same directory.\n"
                    "Also install Selenium: pip install selenium"
                )
            temp_dir = tempfile.mkdtemp()
            html_file, assets_dir = download_notion_page(args.file, temp_dir)
        elif args.file.endswith(".zip"):
            # ZIP file - extract it
            temp_dir = tempfile.mkdtemp()
            html_file, assets_dir = extract_zip_file(args.file, temp_dir)
            print(f"📦 Extracted ZIP to: {temp_dir}")
            print(f"📄 Found HTML: {os.path.basename(html_file)}")
        else:
            # Direct HTML file
            html_file = args.file
            assets_dir = os.path.dirname(html_file)

        # Parse Notion HTML
        deck_name, subdecks, css = parse_html_file(
            html_file, assets_dir, keep_tags=args.keep_tags
        )

        # Validate cards were found
        total_cards = sum(len(cards) for cards in subdecks.values())
        if total_cards == 0:
            raise SystemExit("❌ No cards found in HTML file")

        # Export based on output format
        if args.output.endswith(".csv"):
            export_csv(deck_name, subdecks, args.output)
        elif args.output.endswith(".apkg"):
            export_apkg(deck_name, subdecks, args.output, css=css)
        else:
            raise SystemExit("❌ Output must end with .csv or .apkg")

    except Exception as e:
        print(f"❌ Error: {e}")
        raise
    finally:
        # Clean up temporary directory
        if temp_dir and os.path.exists(temp_dir):
            shutil.rmtree(temp_dir)
            print(f"🧹 Cleaned up temporary files")


if __name__ == "__main__":
    main()
