import streamlit as st
import requests
from bs4 import BeautifulSoup
import re
import json
import urllib.parse
import base64
from pathlib import Path
import time

# ─── Config ──────────────────────────────────────────────────────────────────

st.set_page_config(page_title="Pokémon Movepool Explorer", page_icon="⚡", layout="wide")

ROMAN = {1: "I", 2: "II", 3: "III", 4: "IV", 5: "V", 6: "VI", 7: "VII", 8: "VIII"}
# Gen 9 excluded per request

GEN_RANGES = [
    (1, 151, 1), (152, 251, 2), (252, 386, 3), (387, 493, 4),
    (494, 649, 5), (650, 721, 6), (722, 809, 7), (810, 905, 8),
]

# Tutor game counts per generation
TUTOR_GAMES = {1: 2, 2: 1, 3: 4, 4: 5, 5: 4, 6: 4, 7: 4, 8: 5}

HEADERS = {"User-Agent": "PokemonMovePoolDashboard/1.0 (educational project)"}

SCRIPT_DIR = Path(__file__).parent.resolve()
CACHE_DIR = SCRIPT_DIR / ".pokemon_cache"
CACHE_DIR.mkdir(exist_ok=True)
POKEMON_LIST_CACHE = CACHE_DIR / "pokemon_list.json"
SPRITES_DIR = SCRIPT_DIR / "sprites"

SECTION_KEYS = [
    ("By leveling up", "By_leveling_up"),
    ("By TM/HM", "By_TM/HM"),
    ("By TM", "By_TM"),
    ("By TM/TR", "By_TM/TR"),
    ("By breeding", "By_breeding"),
    ("By tutoring", "By_tutoring"),
    ("By a prior Evolution", "By_a_prior_Evolution"),
]

TYPE_COLORS = {
    "dark": "rgb(98, 77, 78)",
    "dragon": "rgb(80, 96, 225)",
    "electric": "rgb(250, 192, 0)",
    "fighting": "rgb(255, 128, 0)",
    "fire": "rgb(230, 40, 41)",
    "flying": "rgb(129, 185, 239)",
    "grass": "rgb(63, 161, 41)",
    "ground": "rgb(145, 81, 33)",
    "normal": "rgb(159, 161, 159)",
    "psychic": "rgb(239, 65, 121)",
    "rock": "rgb(175, 169, 129)",
    "steel": "rgb(96, 161, 184)",
    "water": "rgb(41, 128, 239)",
    "poison": "rgb(145, 65, 203)",
    "ice": "rgb(61, 206, 243)",
    "bug": "rgb(145, 161, 25)",
    "ghost": "rgb(112, 65, 112)",
    "fairy": "rgb(239, 112, 239)",
}

GEN7_GAMES = [
    ("Sun, Moon, Ultra Sun and Ultra Moon", "SMUSUM"),
    ("Let's Go, Pikachu! and Let's Go, Eevee!", "LGPE"),
]

GEN8_GAMES = [
    ("Sword and Shield", "SwSh"),
    ("Brilliant Diamond and Shining Pearl", "BDSP"),
]

# ─── CSS ─────────────────────────────────────────────────────────────────────

CUSTOM_CSS = """
<style>
.pokemon-table {
    width: 100%;
    border-collapse: collapse;
    font-family: 'Segoe UI', sans-serif;
    font-size: 14px;
    margin-bottom: 16px;
}
.pokemon-table th {
    background: #2d2d2d;
    color: #fff;
    padding: 8px 12px;
    text-align: left;
    border-bottom: 2px solid #444;
    position: sticky;
    top: 0;
    z-index: 1;
}
.pokemon-table td {
    padding: 6px 12px;
    border-bottom: 1px solid #333;
}
.pokemon-table tr:hover td {
    background: rgba(255,255,255,0.05);
}
.type-cell {
    color: #fff !important;
    font-weight: 600;
    padding: 4px 10px !important;
    border-radius: 4px;
    text-align: center;
    text-shadow: 1px 1px 1px rgba(0,0,0,0.3);
}
.father-cell img {
    width: 32px;
    height: 32px;
    vertical-align: middle;
    margin: 1px;
    image-rendering: pixelated;
}
.game-available {
    color: #4caf50;
    font-weight: bold;
    text-align: center;
}
.game-unavailable {
    color: #666;
    text-align: center;
}
.stab-move {
    font-weight: bold;
}
</style>
"""


# ─── Utility ─────────────────────────────────────────────────────────────────

def intro_gen(dex_num: int) -> int:
    for lo, hi, gen in GEN_RANGES:
        if lo <= dex_num <= hi:
            return gen
    return 8


def sprite_b64(dex_num: int) -> str | None:
    p = SPRITES_DIR / f"{dex_num:03d}.png"
    if not p.exists():
        return None
    with open(p, "rb") as f:
        return base64.b64encode(f.read()).decode()


def sprite_img_tag(dex_num: int, size: int = 32) -> str:
    b64 = sprite_b64(dex_num)
    if b64:
        return f'<img src="data:image/png;base64,{b64}" width="{size}" height="{size}" style="image-rendering:pixelated;vertical-align:middle;">'
    return ""


def type_cell_html(type_name: str) -> str:
    key = type_name.strip().lower()
    bg = TYPE_COLORS.get(key, "#555")
    #print(f"Return Background Color {bg} for {type_name}")
    return f'<span class="type-cell" style="background:{bg};display:inline-block;min-width:70px;">{type_name}</span>'


def clean_text(el) -> str:
    if el is None:
        return ""
    # Work on a copy to avoid modifying the original soup
    from copy import copy
    el_copy = copy(el)
    for tag in el_copy.find_all("span", class_="sortkey"):
        tag.decompose()
    for tag in el_copy.find_all(style=re.compile(r"display:\s*none")):
        tag.decompose()
    text = el_copy.get_text(strip=True)
    return re.sub(r"\s+", " ", text)


def extract_type_from_cell(cell) -> str:
    link = cell.find("a", title=lambda t: t and "(type)" in t)
    if link:
        return link.get("title", "").replace(" (type)", "")
    return clean_text(cell)


def is_stab(cell) -> bool:
    return cell.find("b") is not None or cell.find("strong") is not None


def extract_father_images(cell) -> str:
    html_parts = []
    for img in cell.find_all("img"):
        src = img.get("src", "")
        if not src.startswith("http"):
            src = ("https:" + src) if src.startswith("//") else ("https://bulbapedia.bulbagarden.net" + src)
        alt = img.get("alt", "")
        html_parts.append(
            f'<img src="{src}" alt="{alt}" title="{alt}" '
            f'style="width:32px;height:32px;image-rendering:pixelated;vertical-align:middle;margin:1px;">'
        )
    return " ".join(html_parts) if html_parts else clean_text(cell)


def game_available(cell, gen) -> bool:
    #print(gen)
    if gen in (6, 7):
        print(f"Detected Gen: {gen} | Applying alternate Tutor Availabilty Logic for cell: {cell}")
        # Gen 6+: check font color of span inside link
        # White font (rgb(255, 255, 255)) = available
        link = cell.find('a')
        if not link:
            return False

        span = link.find('span')
        if not span:
            return False

        style = span.get('style', '')

        if "#FFFFFF" in style.upper():
            return True
        if "#FFF" in style:
            print(f"Gen {gen} Detection for Gen 7: found Available Tutor.")
            return True

        return False
    else:
        # Gen 1-5: white background = not available
        style = cell.get("style", "")
        if "rgb(255, 255, 255)" in style or "background:#FFF" in style.upper() or "background: #FFF" in style.upper():
            return False
        return cell.find("a") is not None


def clean_value(val: str) -> str:
    # NOT USED
    #print(f"Value to clean up: {val}")
    # Pattern like "04040" → "40", "08080" → "80", "120120" → "120"
    m = re.match(r"^0*(\d+)\1$", val)
    if m:
        return m.group(1)
    # Pattern like "100}}100%" or "070}}70%"
    m = re.match(r"\d+\}\}(\d+%?)$", val)
    if m:
        return m.group(1)
    # "0000—" or "00—"
    if re.match(r"^0+—$", val):
        return "—"
    # "00—}}—%" → "—"
    if "—" in val and "}}" in val:
        return "—"
    # "101—%" → "—" (Gen I accuracy artifacts)
    if re.match(r"^\d+—%?$", val):
        return "—"
    return val if val else "—"


# ─── Pokémon list ────────────────────────────────────────────────────────────

def _scrape_pokemon_list() -> list[dict]:
    url = "https://bulbapedia.bulbagarden.net/wiki/List_of_Pok%C3%A9mon_by_National_Pok%C3%A9dex_number"
    resp = requests.get(url, headers=HEADERS, timeout=30)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "lxml")
    pokemon_list = []
    seen = set()
    for table in soup.find_all("table"):
        for row in table.find_all("tr"):
            cells = row.find_all("td")
            if len(cells) < 3:
                continue
            dex_text = cells[0].get_text(strip=True)
            m = re.match(r"#?0*(\d+)", dex_text)
            if not m:
                continue
            dex_num = int(m.group(1))
            if dex_num < 1 or dex_num > 905:
                continue
            name = None
            for cell in cells[1:]:
                link = cell.find("a", title=lambda t: t and "(Pokémon)" in t)
                if link:
                    name = link.get("title", "").replace(" (Pokémon)", "")
                    break
            if not name or name in seen:
                continue
            seen.add(name)
            pokemon_list.append({"dex": dex_num, "name": name, "intro_gen": intro_gen(dex_num)})
    pokemon_list.sort(key=lambda p: p["dex"])
    return pokemon_list


def load_pokemon_list() -> list[dict]:
    if POKEMON_LIST_CACHE.exists():
        try:
            data = json.loads(POKEMON_LIST_CACHE.read_text("utf-8"))
            if len(data) > 100:
                return data
        except Exception:
            pass
    with st.spinner("Fetching Pokémon list from Bulbapedia (one-time)…"):
        data = _scrape_pokemon_list()
        if data:
            POKEMON_LIST_CACHE.write_text(json.dumps(data, ensure_ascii=False), "utf-8")
            return data
    st.error("Could not fetch Pokémon list. Check your connection.")
    return []


# ─── Table parsing helpers ───────────────────────────────────────────────────

def _find_section_anchor(soup, label: str, anchor_id: str):
    anchor = soup.find(id=anchor_id)
    if anchor is None:
        anchor = soup.find(id=anchor_id.replace("/", "%2F"))
    if anchor is None:
        for tag in soup.find_all(["h4", "h3", "span"]):
            if label.lower() in tag.get_text(strip=True).lower():
                anchor = tag
                break
    return anchor


def _find_table_after(anchor):
    parent = anchor
    for _ in range(5):
        if parent.name in ("h3", "h4", "h5", "div"):
            break
        if parent.parent:
            parent = parent.parent
    for sibling in parent.find_all_next():
        if sibling.name == "table" and sibling.find("td"):
            return sibling
        if sibling.name in ("h3", "h4", "h5") and sibling != parent:
            break
    return None


def _find_header_row(table):
    for i, row in enumerate(table.find_all("tr")):
        ths = row.find_all("th")
        if len(ths) >= 3:
            text = " ".join(clean_text(th) for th in ths).lower()
            if "move" in text and "type" in text:
                return i, ths
    return None, None


def _is_footer_row(row) -> bool:
    text = row.get_text(strip=True)
    if len(row.find_all("td")) <= 2:
        if "Bold indicates" in text or "STAB" in text or "Click on the generation" in text:
            return True
    return False


def _build_table(headers: list[str], rows: list[str]) -> str | None:
    if not rows:
        return None
    hdr = "".join(f"<th>{h}</th>" for h in headers)
    body = "".join(rows)
    return f'<table class="pokemon-table"><thead><tr>{hdr}</tr></thead><tbody>{body}</tbody></table>'


def _move_idx_from_headers(headers_text: list[str]) -> int | None:
    for i, h in enumerate(headers_text):
        if h.lower() == "move":
            return i
    return None


def _has_cat(post_move_headers: list[str]) -> bool:
    return any("cat" in h.lower() for h in post_move_headers)


def _parse_move_type_cat_pwr_acc_pp(cells, move_idx: int, has_cat: bool, description: str = "N/F") -> list[str]:
    """Parse the common Move → Type → [Cat.] → Pwr → Acc → PP columns into HTML <td> fragments."""
    parts = []

    # Move
    move_cell = cells[move_idx]
    move_name = clean_text(move_cell)
    stab = is_stab(move_cell)
    cls = ' class="stab-move"' if stab else ""
    parts.append(f'<td{cls}><span title="{description}" style="cursor:help;">{move_name}</span></td>')  # Adding Tooltip Move Descriptions if enabled, else "N/F"

    # Type
    if move_idx + 1 < len(cells):
        type_name = extract_type_from_cell(cells[move_idx + 1])
        parts.append(f"<td>{type_cell_html(type_name)}</td>")
    else:
        parts.append("<td>—</td>")

    # Cat. modify offset to hide/show category column
    offset = move_idx + 2
    if has_cat:
        if offset < len(cells):
            parts.append(f"<td>{clean_text(cells[offset])}</td>")
        else:
            parts.append("<td>—</td>")
        offset += 1

    # Pwr, Acc, PP
    for j in range(3):
        if offset + j < len(cells):
            val = clean_text(cells[offset + j])
            #print(f"After Cleansing: {val}")
            parts.append(f"<td>{val}</td>")
        else:
            parts.append("<td>—</td>")

    return parts


def _common_out_headers(has_cat: bool) -> list[str]:
    h = ["Move", "Type"]
    if has_cat:
        h.append("Cat.")
    h += ["Pwr.", "Acc.", "PP"]
    return h


def _is_duplicate_header_row(row_values: list[str], headers: list[str]) -> bool:
    """Check if a data row is actually a duplicate of the headers."""
    #print(f"Checking if {row_values} matches column headers: {headers}...")
    if len(row_values) != len(headers):
        #print(f"The lengths of headers and row_values are different, Headers are \nNOT DUPLICATE.")
        return False
    # Compare cleaned/normalized values
    for rv, hv in zip(row_values, headers):
        rv_clean = rv.lower().strip()
        hv_clean = hv.lower().strip()
        if rv_clean != hv_clean:
            #print(f' Found differing element: {rv}[{rv_clean}] <> {hv}[{hv_clean}], Headers are \nNOT DUPLICATE.')
            return False
    #print("All Elements are identical, Headers are \nDUPLICATE!")
    return True


def _find_game_headers(soup: BeautifulSoup, gen: int) -> list[tuple[str, str, any]]:
    # find game-specific h4 headers for Gen 7/8. Returns [(display_name, key, anchor_element), ...]
    if gen == 7:
        games = GEN7_GAMES
    elif gen == 8:
        games = GEN8_GAMES
    else:
        return []

    found = []
    for h4 in soup.find_all("h4"):
        text = _normalize_text(h4.get_text(strip=True))
        #print(f"Trying to retreive game headers from h4's: {text}")
        for display, key in games:
            normalized_display = _normalize_text(display)
            if normalized_display in text or key.lower() in text:
                found.append((display, key, h4))
                print(f"Found game: {(display, key)} in h4: {h4}")
                break
            else:
                print(f"Could not retrieve game: {(display, key)} in h4: {h4}")
    return found


def _normalize_text(s: str) -> str:
    """Collapse whitespace and normalize for comparison."""
    return re.sub(r'\s+', ' ', s.lower().replace(',', ', ')).strip()


def _get_sections_between(start_elem, end_elem, section_keys):
    # Extract Tables for each section between two elements (or until end of page)
    results = {}
    current = start_elem.find_next()

    while current and current != end_elem:
        # Check if this is relevant section header
        for label, anchor_id in section_keys:
            # check for span with matching id
            if current.name == "span" and current.get("id"):
                span_id = current.get("id", "")
                if anchor_id.lower() in span_id.lower() or anchor_id.replace("/", "%2F").lower() in span_id.lower():
                    # Find the table after this anchor
                    tbl = _find_table_after(current)
                    if tbl:
                        parser = SECTION_PARSERS.get(label, parse_levelup)
                        # Determine gen from context
                        html = parser(tbl, 7)   # MODIFY DEBUG
                        if html:
                            display_label = DISPLAY_LABELS.get(label, label)
                            results[display_label] = html
                    break
            # Also check for h5 headers (Gen 7 uses h5 for section names under h4 game headers)
            if current.name in ("h4", "h5"):
                header_text = current.get_text(strip=True).lower()
                if label.lower() in header_text:
                    tbl = _find_table_after(current)
                    if tbl:
                        parser = SECTION_PARSERS.get(label, parse_levelup)
                        html = parser(tbl, 7)
                        if html:
                            display_label = DISPLAY_LABELS.get(label, label)
                            results[display_label] = html
                    break

        current = current.find_next()

    return results


def _detect_gen8_games(soup: BeautifulSoup) -> list[tuple[str, str]]:
    # Detect which Gen 8 games a Pokémon is available in from intro paragraph
    for p in soup.find_all("p"):
        text = p.get_text(strip=True).lower()
        if "is available in" in text:
            games = []
            if "sword and shield" in text:
                games.append(("Sword and Shield", "SwSh"))
            if "brilliant diamond" in text:
                games.append(("Brilliant Diamond and Shining Pearl", "BDSP"))
            if games:
                return games
    # If no intro found = SqSh Only
    return [("Sword and Shield", "SwSh")]


def _find_gen8_section_tables(section_anchor, next_section_anchor) -> dict[str, any]:
    # find all tables in a Gen 8 section, grouped by game marker
    # return {"shared": table, "SwSh": table, "BDSP": table,} whichever exists
    results = {}
    current = section_anchor.find_next()

    pending_game = None # track most recent game marker seen

    while current:
        # Stop for next section
        if current.name == "h4" and current != section_anchor:
            break
        if next_section_anchor and current == next_section_anchor:
            break

        # check for game marker:
        if current.name == "p":
            p_text = current.get_text(strip=True)
            if p_text in ("SwSh", "BDSP") or "Sw" in p_text and "Sh" in p_text:
                pending_game = "SwSh"
            elif "BD" in p_text and "SP" in p_text:
                pending_game = "BDSP"

        # Found a table
        if current.name == "table" and current.find("td"):
            if pending_game:
                results[pending_game] = current
                pending_game = None
            elif "shared" not in results:
                # first table without marker = shared
                results["shared"] = current

        current = current.find_next()

    return results


def _get_next_section_anchor(soup: BeautifulSoup, current_anchor_id: str, section_keys) -> any:
    # find anchor for next section after current one
    found_current = False
    for label, anchor_id in section_keys:
        if anchor_id == current_anchor_id:
            found_current = True
            continue
        if found_current:
            anchor = _find_section_anchor(soup, label, anchor_id)
            if anchor:
                return anchor
    return None


def get_move_description(move_name: str) -> str:
    resp = requests.get(f"https://pokeapi.co/api/v2/move/{move_name.lower().replace(' ', '-')}/")
    if not resp.ok:
        return ""

    data = resp.json()
    entries = data.get("effect_entries", [])
    en = next((e for e in entries if e["language"]["name"] == "en"), None)
    if not en:
        return ""

    text = en["short_effect"]

    # Replace placeholders with actual values
    effect_chance = data.get("effect_chance")
    if effect_chance is not None:
        text = text.replace("$effect_chance", str(effect_chance))

    return text

# ─── Section parsers ─────────────────────────────────────────────────────────

def parse_levelup(table, gen: int) -> str | None:
    hdr_idx, hdr_ths = _find_header_row(table)
    if hdr_idx is None:
        return None
    headers_text = [clean_text(th) for th in hdr_ths]
    move_idx = _move_idx_from_headers(headers_text)
    if move_idx is None:
        return None

    level_headers = headers_text[:move_idx]
    cat = _has_cat(headers_text[move_idx + 1:])
    out_headers = level_headers + _common_out_headers(cat)

    if level_headers[0] != "Level":
        print("Extra Game Version Detected")
    else:
        print(f"Normal Level Structure, first Header: {level_headers[0]}")

    rows_html = []
    first_data_row = True
    for row in table.find_all("tr")[hdr_idx + 1:]:
        if _is_footer_row(row):
            continue
        cells = row.find_all(["td", "th"])
        if len(cells) < move_idx + 3:
            continue

        # Build row values to check for duplicate header
        row_values = []
        for i in range(move_idx):
            row_values.append(clean_text(cells[i]))

        # Move including Tooltip Move Description
        if show_descriptions:
            description = get_move_description(clean_text(cells[move_idx]))
        else:
            description = "None"
        print(clean_text(cells[move_idx]))
        row_values.append(clean_text(cells[move_idx]))

        if move_idx + 1 < len(cells):
            row_values.append(extract_type_from_cell(cells[move_idx + 1]))  # Type

        # Skip if this row is a duplicate of the headers
        if first_data_row:
            first_data_row = False
            #print(f"LEVEL UP TABLE HAS FOLLOWING HEADERS: {out_headers}, and following first row_values: {row_values}")
            #if _is_duplicate_header_row(row_values[:len(out_headers)], out_headers):
            if row_values[0] == "Level" or row_values[0] == out_headers[0]:
                #print(f"SKIPPED ADDING FIRST ROW: DUPLICATE COLUMN HEADS")
                continue

        parts = []
        for i in range(move_idx):
            #print(f"Trying to Append to parts dirty Value : {cells[i]} in LEVEL-UP Table.")
            val = clean_text(cells[i])
            parts.append(f"<td>{val}</td>")
        parts += _parse_move_type_cat_pwr_acc_pp(cells, move_idx, cat, description)
        rows_html.append("<tr>" + "".join(parts) + "</tr>")

    return _build_table(out_headers, rows_html)


def parse_tm_hm(table, gen: int) -> str | None:
    hdr_idx, hdr_ths = _find_header_row(table)
    if hdr_idx is None:
        return None
    headers_text = [clean_text(th) for th in hdr_ths]
    move_idx = _move_idx_from_headers(headers_text)
    if move_idx is None:
        return None

    cat = _has_cat(headers_text[move_idx + 1:])
    out_headers = ["TM"] + _common_out_headers(cat)

    rows_html = []
    first_data_row = True
    for row in table.find_all("tr")[hdr_idx + 1:]:
        #print(row)
        if _is_footer_row(row):
            continue
        cells = row.find_all(["td", "th"])

        # Since the first columns are always empty we ignore the first element always
        if cells and not clean_text(cells[0]):
            cells = cells[1:]

        #if len(cells) < move_idx + 3:
        if len(cells) < 4:
            continue

        # Find TM## text in pre-move cells
        tm_text = ""
        for cell in cells:
            t = clean_text(cell)
            if re.match(r"(TM|HM|TR)\d+", t):
                tm_text = t
                break

        if not tm_text:
            continue

        # Find actual Move Cell by looking for link to _(move) page
        actual_move_idx = None
        for i, cell in enumerate(cells):
            if cell.find("a", href=lambda h: h and "_(move)" in h):
                actual_move_idx = i
                break

        if actual_move_idx is None:
            continue

        if first_data_row:
            first_data_row = False
            if tm_text == "TM":
                continue

        print(clean_text(cells[actual_move_idx]))
        if show_extended_desc:
            description = get_move_description(clean_text(cells[actual_move_idx]))
        else:
            description = "None"

        parts = [f"<td>{tm_text}</td>"]
        parts += _parse_move_type_cat_pwr_acc_pp(cells, actual_move_idx, cat, description)
        rows_html.append("<tr>" + "".join(parts) + "</tr>")

    return _build_table(out_headers, rows_html)


def parse_breeding(table, gen: int) -> str | None:
    hdr_idx, hdr_ths = _find_header_row(table)
    if hdr_idx is None:
        return None
    headers_text = [clean_text(th) for th in hdr_ths]
    move_idx = _move_idx_from_headers(headers_text)
    if move_idx is None:
        return None

    cat = _has_cat(headers_text[move_idx + 1:])
    out_headers = ["Father"] + _common_out_headers(cat)

    rows_html = []
    first_data_row = True
    for row in table.find_all("tr")[hdr_idx + 1:]:
        if _is_footer_row(row):
            continue
        cells = row.find_all(["td", "th"])
        if len(cells) < move_idx + 3:
            continue

        father_html = extract_father_images(cells[0])

        # Build row values for duplicate check
        row_values = [clean_text(cells[0]), clean_text(cells[move_idx])]
        if move_idx + 1 < len(cells):
            row_values.append(extract_type_from_cell(cells[move_idx + 1]))

        # Skip if this row is a duplicate of the headers
        if first_data_row:
            first_data_row = False
            #if _is_duplicate_header_row(row_values[:len(out_headers)], out_headers):
            if row_values[0] == "Father" or row_values[0] == "Parent":
                continue

        parts = [f'<td class="father-cell">{father_html}</td>']
        parts += _parse_move_type_cat_pwr_acc_pp(cells, move_idx, cat)
        rows_html.append("<tr>" + "".join(parts) + "</tr>")

    return _build_table(out_headers, rows_html)


def parse_tutoring(table, gen: int) -> str | None:
    hdr_idx, hdr_ths = _find_header_row(table)
    if hdr_idx is None:
        return None

    headers_text = [clean_text(th) for th in hdr_ths]
    cat = _has_cat(headers_text)

    all_rows = table.find_all("tr")
    rows_html = []
    game_headers = None

    for row in all_rows[hdr_idx + 1:]:
        if _is_footer_row(row):
            continue

        # Separate <th> (game cells) from <td> (data cells)
        ths = row.find_all("th")
        tds = row.find_all("td")

        if not tds or not ths:
            continue

        # First row: capture game headers from the <th> texts
        if game_headers is None:
            game_headers = [clean_text(th) for th in ths]

        # Find move cell in <td> elements by looking for _(move) link
        actual_move_idx = None
        for i, td in enumerate(tds):
            if td.find("a", href=lambda h: h and "_(move)" in h):
                actual_move_idx = i
                break

        if actual_move_idx is None:
            continue

        # Build game availability columns from <th> cells
        parts = []
        for th in ths:
            avail = game_available(th, gen)
            if avail:
                parts.append(f'<td class="game-available">✓</td>')
            else:
                parts.append(f'<td class="game-unavailable">—</td>')

        # Parse move, type, etc. from <td> cells
        parts += _parse_move_type_cat_pwr_acc_pp(tds, actual_move_idx, cat)
        rows_html.append("<tr>" + "".join(parts) + "</tr>")

    if not game_headers:
        return None

    out_headers = game_headers + _common_out_headers(cat)
    return _build_table(out_headers, rows_html)


def parse_prior_evo(table, gen: int) -> str | None:
    hdr_idx, hdr_ths = _find_header_row(table)
    if hdr_idx is None:
        return None
    headers_text = [clean_text(th) for th in hdr_ths]
    move_idx = _move_idx_from_headers(headers_text)
    if move_idx is None:
        return None

    pre_headers = headers_text[:move_idx] if headers_text[:move_idx] else ["Source"]
    cat = _has_cat(headers_text[move_idx + 1:])
    out_headers = pre_headers + _common_out_headers(cat)

    rows_html = []
    first_data_row = True
    for row in table.find_all("tr")[hdr_idx + 1:]:
        if _is_footer_row(row):
            continue
        cells = row.find_all(["td", "th"])
        if len(cells) < move_idx + 3:
            continue

        # Build row values for duplicate check
        row_values = []
        for i in range(move_idx):
            row_values.append(clean_text(cells[i]))
        row_values.append(clean_text(cells[move_idx]))

        # Skip if this row is a duplicate of the headers
        if first_data_row:
            first_data_row = False
            #if _is_duplicate_header_row(row_values[:len(out_headers)], out_headers):
            #print(f"ROW VALUES FOR BY_PRIOR EVOLUTION: {row_values}")
            if row_values[0] == "Stage":
                continue

        parts = []
        for i in range(move_idx):
            cell = cells[i]
            if cell.find("img"):
                parts.append(f'<td class="father-cell">{extract_father_images(cell)}</td>')
            else:
                parts.append(f"<td>{clean_text(cell)}</td>")
        parts += _parse_move_type_cat_pwr_acc_pp(cells, move_idx, cat)
        rows_html.append("<tr>" + "".join(parts) + "</tr>")

    return _build_table(out_headers, rows_html)


SECTION_PARSERS = {
    "By leveling up": parse_levelup,
    "By TM/HM": parse_tm_hm,
    "By TM": parse_tm_hm,
    "By TM/TR": parse_tm_hm,
    "By breeding": parse_breeding,
    "By tutoring": parse_tutoring,
    "By a prior Evolution": parse_prior_evo,
}

DISPLAY_LABELS = {"By TM": "By TM/HM", "By TM/TR": "By TM/HM"}


# ─── Fetch learnset (NO CACHING) ─────────────────────────────────────────────

def fetch_learnset(pokemon_name: str, gen: int) -> dict[str, str] | None:
    encoded = urllib.parse.quote(f"{pokemon_name}_(Pokémon)/Generation_{ROMAN[gen]}_learnset")
    url = f"https://bulbapedia.bulbagarden.net/wiki/{encoded}"
    try:
        resp = requests.get(url, headers=HEADERS, timeout=30)
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
    except requests.RequestException as e:
        st.error(f"Failed to fetch learnset: {e}")
        return None

    soup = BeautifulSoup(resp.text, "lxml")
    results = {}
    for label, anchor_id in SECTION_KEYS:
        anchor = _find_section_anchor(soup, label, anchor_id)
        if anchor is None:
            continue
        tbl = _find_table_after(anchor)
        if tbl is None:
            continue
        parser = SECTION_PARSERS.get(label, parse_levelup)
        html = parser(tbl, gen)
        if html:
            display_label = DISPLAY_LABELS.get(label, label)
            results[display_label] = html
    return results


def fetch_learnset_multi_game(pokemon_name: str, gen: int) -> dict[str, dict[str, str]] | dict[str, str] | None:
    # Fetch learnset, returning either:
    # dict[game_key, dict[section, html]] for gen 7/8 with multiple games
    # dict[section, html] for other gens (simple game)
    # None on Error
    encoded = urllib.parse.quote(f"{pokemon_name}_(Pokémon)/Generation_{ROMAN[gen]}_learnset")
    url = f"https://bulbapedia.bulbagarden.net/wiki/{encoded}"

    try:
        resp = requests.get(url, headers=HEADERS, timeout=20)
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
    except requests.RequestException as e:
        st.error(f"Failed to fetch learnset: {e}")
        return None

    soup = BeautifulSoup(resp.text, "lxml")

    # ─── Gen 7: h4 = game headers, h5 = sections ───────────────────────────
    if gen == 7:    # DEBUG
        game_headers = _find_game_headers(soup, gen)
        if len(game_headers) >= 2:
            print("Multiple Games Detected")
            results = {}
            for i, (display, key, h4_elem) in enumerate(game_headers):
                # end element is either next game header, or None
                end_elem = game_headers[i + 1][2] if i + 1 < len(game_headers) else None
                sections = _get_sections_between(h4_elem, end_elem, SECTION_KEYS)
                if sections:
                    print("SECTION FOUND TO APPEND TO RESULTS")
                    results[key] = {"_display": display, **sections}
                else:
                    print("NO SECTION FOUND TO APPEND TO RESULTS")
            if results:
                return results

    # ─── Gen 8: h4 = sections, game markers before tables ──────────────────
    elif gen == 8:
        available_games = _detect_gen8_games(soup)

        if len(available_games) >= 2:
            # Init results for each game
            results = {key: {"_display": display} for display, key in available_games}

            # parse each section
            for label, anchor_id in SECTION_KEYS:
                anchor = _find_section_anchor(soup, label, anchor_id)
                if not anchor:
                    continue

                next_anchor = _get_next_section_anchor(soup, anchor_id, SECTION_KEYS)
                game_tables = _find_gen8_section_tables(anchor, next_anchor)

                parser = SECTION_PARSERS.get(label, parse_levelup)
                display_label = DISPLAY_LABELS.get(label, label)

                for game_key, tbl in game_tables.items():
                    html = parser(tbl, gen)
                    if not html:
                        continue

                    if game_key == "shared":
                        # Add to all available
                        for gk in results:
                            results[gk][display_label] = html
                    elif game_key in results:
                        results[game_key][display_label] = html

            # only return multi-game if actually found content
            if any(len(v) > 1 for v in results.values()):
                return results

    # ─── Fallback: Single-game parsing for all other cases ─────────────────
    results = {}
    for label, anchor_id in SECTION_KEYS:
        anchor = _find_section_anchor(soup, label, anchor_id)
        if anchor is None:
            continue
        tbl = _find_table_after(anchor)
        if tbl is None:
            continue
        parser = SECTION_PARSERS.get(label, parse_levelup)
        html = parser(tbl, gen)
        if html:
            display_label = DISPLAY_LABELS.get(label, label)
            results[display_label] = html

    return results if results else None


def is_multi_game_result(result) -> bool:
    # Check if result features multiple games (Gen 7+)
    if not result:
        print("No Result found.")
        return False
    first_val = next(iter(result.values()), None)
    #rint(f"Trying to Determine Multi-Game Result for : {first_val}")
    return isinstance(first_val, dict)

# ─── Session state ───────────────────────────────────────────────────────────

if "roster" not in st.session_state:
    st.session_state.roster = []
if "loading" not in st.session_state:
    st.session_state.loading = False
    print("Set global Loading State to False")

# ─── CSS injection ───────────────────────────────────────────────────────────

st.markdown(CUSTOM_CSS, unsafe_allow_html=True)

# ─── Load data ───────────────────────────────────────────────────────────────

pokemon_list = load_pokemon_list()
if not pokemon_list:
    st.stop()

name_to_info = {p["name"]: p for p in pokemon_list}
all_names = [p["name"] for p in pokemon_list]

# ─── Control Save ──────────────────────────────────────────────────────────────

def save_gen():
    if "gen_select" in st.session_state:
        st.session_state["saved_gen"] = st.session_state["gen_select"]

# ─── Roster Sidebar ──────────────────────────────────────────────────────────────

with (st.sidebar):
    st.header("🎒 Roster")
    show_descriptions = st.checkbox("Include Move Descriptions (slow)", value=False)
    show_extended_desc = False
    if show_descriptions:
        show_extended_desc = st.checkbox("Include for TM's'? (even slower)", value=False)

    if st.session_state.roster:
        remove_name = None
        lookup_name = None

        for name in st.session_state.roster:
            info = name_to_info.get(name, {})
            dex = info.get("dex", 0)
            sprite = sprite_img_tag(dex, 40)

            c1, c2, c3 = st.columns([1, 4, 1])
            with c1:
                # Sprite
                st.markdown(sprite, unsafe_allow_html=True)
            with c2:
                # Lookup button with sprite
                if st.button(name, key=f"rl_{name}", use_container_width=True):
                    if not st.session_state.loading:
                        lookup_name = name
                    else:
                        print("blocked input because form is still loading")
            with c3:
                # Remove Button
                if st.button("✕", key=f"rr_{name}"):
                    if not st.session_state.loading:
                        remove_name = name
                    else:
                        print("blocked Input because form is still loading")

        if remove_name:
            st.session_state.roster.remove(remove_name)
            st.rerun()
        if lookup_name:
            st.session_state["roster_lookup"] = lookup_name
            st.rerun()

    else:
        st.caption("Put your Roster Pokemon here.")
    st.divider()

# ─── Title ───────────────────────────────────────────────────────────────────

st.title("⚡ Pokémon Movepool Explorer")
st.caption("Data sourced exclusively from [Bulbapedia](https://bulbapedia.bulbagarden.net/) · Gen I–VIII")

# ─── Controls ────────────────────────────────────────────────────────────────

col1, col2, col3 = st.columns([3, 2, 1])

# Handle roster lookup - directly set the pokemon dropdown value
if "roster_lookup" in st.session_state:
    lookup_name = st.session_state.pop("roster_lookup")
    if lookup_name in all_names:
        st.session_state["pokemon_select"] = lookup_name    # Update Dropdown Control

with col1:
    # Pokemon dropdown
    selected_pokemon = st.selectbox(
        "Pokémon", all_names,
        key="pokemon_select",
    )

# Check if pokemon is in roster (for checkbox state)
in_roster = selected_pokemon in st.session_state.roster

# Get pokemon info for available gens
poke_info = name_to_info.get(selected_pokemon, {"intro_gen": 1, "dex": 0})
available_gens = list(range(poke_info["intro_gen"], 9))
gen_labels = [f"Gen {ROMAN[g]} ({g})" for g in available_gens]

with col2:
    # Gen dropdown - READ ONLY conceptually: we never change this programmatically
    # User has full control. If no generations available, show warning and stop.
    if not gen_labels:
        st.warning("No generations available for this Pokémon.")
        st.stop()

    saved_gen = st.session_state.get("saved_gen")

    if saved_gen and saved_gen in gen_labels:
        gen_idx = gen_labels.index(saved_gen)
    else:
        gen_idx = len(gen_labels) - 1
        #print(f"Could not retrieve the generation {saved_gen} - defaulting to last available.")

    gen_selection = st.selectbox("Generation", gen_labels, index=gen_idx, key="gen_select", on_change=save_gen) # Added On-Change event to save last gen

    selected_gen = available_gens[gen_labels.index(gen_selection)]

with col3:
    # "Add to roster" - Checkmark
    st.markdown("<br>", unsafe_allow_html=True)
    # Checkbox state reflects whether current pokemon is in roster
    roster_checked = st.checkbox("Add to roster", value=in_roster, key=f"roster_chk_{selected_pokemon}")
    if roster_checked and selected_pokemon not in st.session_state.roster:
        st.session_state.roster.append(selected_pokemon)
        st.rerun()
    elif not roster_checked and selected_pokemon in st.session_state.roster:
        st.session_state.roster.remove(selected_pokemon)
        st.rerun()
        #debug

# ─── Header with sprite ─────────────────────────────────────────────────────

dex_num = poke_info.get("dex", 0)
spr = sprite_img_tag(dex_num, 48)
st.markdown(f'<h2>{spr} {selected_pokemon} — Generation {ROMAN[selected_gen]} Learnset</h2>', unsafe_allow_html=True)
st.divider()

# ─── Fetch & display (based solely on dropdown values) ──────────────────────

st.session_state.loading = True

with st.spinner(f"Fetching {selected_pokemon} Gen {ROMAN[selected_gen]} learnset…"):
    result = fetch_learnset_multi_game(selected_pokemon, selected_gen)

#print(result)
st.session_state.loading = False    # Disable Button input while loading to avoid messed up markdown

if result is None:
    st.warning(f"No learnset page found for **{selected_pokemon}** in Generation {ROMAN[selected_gen]}.")
    st.stop()

# Handle multi-game results (Gen 7)
if is_multi_game_result(result):
    game_keys = list(result.keys())
    game_labels = [result[k].get("_display", k) for k in game_keys]

    selected_game_label = st.radio(
        "Select Game Version",
        game_labels,
        horizontal=True,
        key=f"game_select_{selected_pokemon}_{selected_gen}"
    )
    selected_game_key = game_keys[game_labels.index(selected_game_label)]
    sections = {k: v for k, v in result[selected_game_key].items() if k != "_display"}
    #print(sections)
else:
    print("No Multiple games detected.")
    sections = result


if sections is None:
    st.warning(f"No learnset page found for **{selected_pokemon}** in Generation {ROMAN[selected_gen]}.")
    st.stop()
if not sections:
    st.warning(f"Page exists but no tables could be parsed for **{selected_pokemon}** Gen {ROMAN[selected_gen]}.")
    st.stop()

DISPLAY_ORDER = ["By leveling up", "By TM/HM", "By breeding", "By tutoring", "By a prior Evolution"]
render_key = f"{selected_pokemon}-{selected_gen}-{time.time()}"

if "By leveling up" in sections:
    st.subheader("📈 Moves learned by leveling up")
    #print(f"Trying to match to original levelup table: 'table_levelup_{selected_pokemon}_{selected_gen}'")
    st.markdown(
        f'<div data-render-key="{render_key}-levelup">{sections["By leveling up"]}</div>',
        unsafe_allow_html=True
    )
else:
    st.info("No level-up moves found for this Pokémon/generation.")

remaining = [k for k in DISPLAY_ORDER if k != "By leveling up" and k in sections]
if remaining:
    st.divider()
    st.subheader("Other move sources")
    icons = {"By TM/HM": "📀", "By breeding": "🥚", "By tutoring": "🎓", "By a prior Evolution": "🔄"}
    tabs = st.tabs([f"{icons.get(k, '📋')} {k}" for k in remaining])
    for tab, key in zip(tabs, remaining):
        with tab:
            # Unique key per section/pokemon/gen forces re-render of markdown
            safe_key = key.replace(" ", "_").replace("/", "_")
            #print(f"Trying to match safe_key {safe_key} to original tables based on original key {key}: 'table_{safe_key}_{selected_pokemon}_{selected_gen}'")
            st.markdown(
                f'<div data-render-key="{render_key}-{safe_key}">{sections[key]}</div>',
                unsafe_allow_html=True
            )

extra = [k for k in sections if k not in DISPLAY_ORDER]
for key in extra:
    st.subheader(f"📋 {key}")
    st.markdown(sections[key], unsafe_allow_html=True)

st.divider()
st.caption(
    "Data from [Bulbapedia](https://bulbapedia.bulbagarden.net/). "
    "Content available under [CC BY-NC-SA 2.5](https://creativecommons.org/licenses/by-nc-sa/2.5/)."
)