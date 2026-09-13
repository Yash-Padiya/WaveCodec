"""
WaveCode v1 — Core
------------------
Shared encode/decode/checksum logic. This module IS the machine-readable
spec (see WAVECODE_SPEC.md for the human-readable version) and is the
single source of truth used by app.py (Flask API + templates).

Fields:
  - name              required, 1..NAME_MAX_LEN characters, >=1 letter
  - day/month/year     optional, all three or none
  - message            optional, 1..MESSAGE_MAX_LEN characters, >=1 letter
  - additional_info    optional, 1..ADDITIONAL_MAX_LEN characters, >=1 letter

Because everything but name is optional, the bar sequence needs a small
self-describing HEADER bar (see build_bars / split_segments) so a
decoder that has never seen this particular code before still knows
which optional segments to expect.
"""

from io import BytesIO

import numpy as np
from PIL import Image, ImageDraw

# ----------------------------------------------------------------------
# WAVECODE v1 CONSTANTS (this block IS the spec — keep it fixed)
# ----------------------------------------------------------------------
H_MIN = 8.0              # minimum bar amplitude (total height, in SVG units)
H_MAX = 100.0            # maximum bar amplitude (total height, in SVG units)

BAR_WIDTH = 6.0          # width of every bar
CAL_BAR_WIDTH = 3.0      # calibration bars are visibly thinner -> easy to spot
BAR_GAP = 5.0            # gap between two ordinary bars (same segment)
SEGMENT_GAP = 14.0       # gap between two different segments
CAL_GAP = 26.0           # gap between a calibration bar and the content
                         # (CAL_GAP > SEGMENT_GAP > BAR_GAP: this strict
                         # ordering is how the decoder finds every boundary)

MARGIN_X = 20.0
MARGIN_Y = 20.0
CANVAS_H = H_MAX + 2 * MARGIN_Y

CHECKSUM_MOD = 26        # checksum is folded into a letter-shaped 1-26 value

YEAR_MIN = 1900
YEAR_MAX = 2099

NAME_MAX_LEN = 20
MESSAGE_MAX_LEN = 26
ADDITIONAL_MAX_LEN = 24

MONTH_NAMES = ["", "January", "February", "March", "April", "May", "June",
               "July", "August", "September", "October", "November", "December"]


class WaveCodeError(ValueError):
    """Raised for invalid input or an undecodable photo."""


# ----------------------------------------------------------------------
# Shared value <-> height helpers
# ----------------------------------------------------------------------

def letter_value(ch: str) -> int:
    """A=1 ... Z=26"""
    return ord(ch.upper()) - ord('A') + 1


def scale(value: float, v_min: float, v_max: float) -> float:
    """Linear map value in [v_min, v_max] -> bar amplitude in [H_MIN, H_MAX]."""
    v = max(v_min, min(v_max, value))
    return H_MIN + (v - v_min) * (H_MAX - H_MIN) / (v_max - v_min)


def _letters_to_values(text: str):
    return [letter_value(ch) for ch in text.upper() if ch.isalpha()]


def _validate_text_field(value, field_name, max_len, required=False):
    """Trims, length-checks, and confirms a text field has >=1 letter.
    Returns None for an absent optional field, otherwise the trimmed string."""
    value = (value or "").strip()
    if not value:
        if required:
            raise WaveCodeError(f"{field_name} is required.")
        return None
    if len(value) > max_len:
        raise WaveCodeError(f"{field_name} must be at most {max_len} characters.")
    if not any(ch.isalpha() for ch in value):
        raise WaveCodeError(f"{field_name} must contain at least one letter.")
    return value


def compute_checksum(name_values, date_values=None, message_values=None, additional_values=None):
    """
    A simple position-weighted checksum over the decoded values of every
    segment that's present, folded into range [1, 26] so it can be drawn
    as a letter-shaped bar.

    This catches the common failure modes of an aged/degraded photo
    (one bar misread, two bars swapped) well enough to warn the user —
    it is NOT cryptographic, just a durability check.
    """
    weighted = sum(i * v for i, v in enumerate(name_values, start=1))
    if date_values:
        day, month, year = date_values
        weighted += day * 100 + month * 200 + year * 3
    if message_values:
        weighted += sum(i * v for i, v in enumerate(message_values, start=1)) * 7
    if additional_values:
        weighted += sum(i * v for i, v in enumerate(additional_values, start=1)) * 13
    return (weighted % CHECKSUM_MOD) + 1


def _header_value(has_date, has_message, has_additional):
    bitmask = (1 if has_date else 0) | (2 if has_message else 0) | (4 if has_additional else 0)
    return bitmask + 1  # keep in [1, 8], same 1-based convention as everything else


def _header_flags(header_value):
    bitmask = header_value - 1
    return {
        "has_date": bool(bitmask & 1),
        "has_message": bool(bitmask & 2),
        "has_additional": bool(bitmask & 4),
    }


# ----------------------------------------------------------------------
# Encoding
# ----------------------------------------------------------------------

def build_bars(name, day=None, month=None, year=None, message=None, additional_info=None):
    """
    Returns a list of bar dicts, each:
      {segment: 'cal'|'header'|'name'|'date'|'message'|'additional'|'checksum',
       symbol: str, value, height: float}
    in left-to-right drawing order, WITHOUT x positions yet.

    Structure: CAL_MIN, HEADER, name letters..., [day, month, year],
               [message letters...], [additional-info letters...],
               CHECKSUM, CAL_MAX
    (the three bracketed segments are each present only if their field
    was provided; HEADER records which ones to expect.)
    """
    name = _validate_text_field(name, "Name", NAME_MAX_LEN, required=True)
    message = _validate_text_field(message, "Special message", MESSAGE_MAX_LEN)
    additional_info = _validate_text_field(additional_info, "Additional information", ADDITIONAL_MAX_LEN)

    date_fields_given = [f for f in (day, month, year) if f is not None]
    has_date = len(date_fields_given) > 0
    if has_date:
        if len(date_fields_given) != 3:
            raise WaveCodeError("Birth date needs day, month, and year all together, or none of them.")
        if not (1 <= day <= 31):
            raise WaveCodeError("Day must be between 1 and 31.")
        if not (1 <= month <= 12):
            raise WaveCodeError("Month must be between 1 and 12.")
        if not (YEAR_MIN <= year <= YEAR_MAX):
            raise WaveCodeError(f"Year must be between {YEAR_MIN} and {YEAR_MAX}.")

    has_message = message is not None
    has_additional = additional_info is not None

    bars = [{"segment": "cal", "symbol": "CAL_MIN", "value": None, "height": H_MIN}]

    header_value = _header_value(has_date, has_message, has_additional)
    bars.append({
        "segment": "header", "symbol": "HEADER", "value": header_value,
        "height": scale(header_value, 1, 8),
    })

    name_values = _letters_to_values(name)
    for ch, v in zip((c for c in name.upper() if c.isalpha()), name_values):
        bars.append({"segment": "name", "symbol": ch, "value": v, "height": scale(v, 1, 26)})

    if has_date:
        bars.append({"segment": "date", "symbol": "DAY", "value": day, "height": scale(day, 1, 31)})
        bars.append({"segment": "date", "symbol": "MONTH", "value": month, "height": scale(month, 1, 12)})
        bars.append({"segment": "date", "symbol": "YEAR", "value": year,
                     "height": scale(year, YEAR_MIN, YEAR_MAX)})

    message_values = _letters_to_values(message) if has_message else None
    if has_message:
        for ch, v in zip((c for c in message.upper() if c.isalpha()), message_values):
            bars.append({"segment": "message", "symbol": ch, "value": v, "height": scale(v, 1, 26)})

    additional_values = _letters_to_values(additional_info) if has_additional else None
    if has_additional:
        for ch, v in zip((c for c in additional_info.upper() if c.isalpha()), additional_values):
            bars.append({"segment": "additional", "symbol": ch, "value": v, "height": scale(v, 1, 26)})

    checksum_val = compute_checksum(
        name_values,
        (day, month, year) if has_date else None,
        message_values,
        additional_values,
    )
    bars.append({
        "segment": "checksum", "symbol": "CHECKSUM", "value": checksum_val,
        "height": scale(checksum_val, 1, 26),
    })

    bars.append({"segment": "cal", "symbol": "CAL_MAX", "value": None, "height": H_MAX})
    return bars


def gap_before(prev, cur):
    """Determine which gap size sits between two consecutive bars."""
    if prev is None:
        return 0
    if prev["segment"] == "cal" or cur["segment"] == "cal":
        return CAL_GAP
    if prev["segment"] != cur["segment"]:
        return SEGMENT_GAP
    return BAR_GAP


def layout(bars):
    """Assign an x-position + width to every bar."""
    x = MARGIN_X
    prev = None
    positioned = []
    for b in bars:
        x += gap_before(prev, b)
        width = CAL_BAR_WIDTH if b["segment"] == "cal" else BAR_WIDTH
        positioned.append({**b, "x": x, "width": width})
        x += width
        prev = b
    total_width = x + MARGIN_X - (positioned[-1]["width"] if positioned else 0)
    return positioned, total_width


def build_svg(positioned, total_width) -> str:
    center_y = CANVAS_H / 2
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" '
        f'viewBox="0 0 {total_width:.2f} {CANVAS_H:.2f}" '
        f'width="{total_width:.2f}mm" height="{CANVAS_H:.2f}mm">',
        f'<rect x="0" y="0" width="{total_width:.2f}" height="{CANVAS_H:.2f}" fill="white"/>',
        f'<line x1="0" y1="{center_y:.2f}" x2="{total_width:.2f}" y2="{center_y:.2f}" '
        f'stroke="#dddddd" stroke-width="0.4" stroke-dasharray="1,2"/>',
    ]
    for b in positioned:
        half = b["height"] / 2
        y_top = center_y - half
        rect_h = b["height"]
        w = b["width"]
        rx = w / 2  # fully rounded ends (pill shape)
        parts.append(
            f'<rect x="{b["x"]:.2f}" y="{y_top:.2f}" '
            f'width="{w:.2f}" height="{rect_h:.2f}" '
            f'rx="{rx:.2f}" ry="{rx:.2f}" fill="black" '
            f'data-segment="{b["segment"]}" data-symbol="{b["symbol"]}"/>'
        )
    parts.append("</svg>")
    return "\n".join(parts)


def render_png(positioned, total_width, scale_factor=8) -> Image.Image:
    """Renders the bar layout to an in-memory PIL Image (caller decides
    whether to .save() it to disk or to a BytesIO for a HTTP response)."""
    W = int(total_width * scale_factor)
    H = int(CANVAS_H * scale_factor)
    img = Image.new("RGB", (W, H), "white")
    draw = ImageDraw.Draw(img)
    center_y = CANVAS_H / 2

    draw.line(
        [(0, center_y * scale_factor), (W, center_y * scale_factor)],
        fill=(220, 220, 220), width=max(1, scale_factor // 8)
    )

    for b in positioned:
        half = b["height"] / 2
        x0 = b["x"] * scale_factor
        x1 = (b["x"] + b["width"]) * scale_factor
        y0 = (center_y - half) * scale_factor
        y1 = (center_y + half) * scale_factor
        radius = (b["width"] / 2) * scale_factor
        draw.rounded_rectangle([x0, y0, x1, y1], radius=radius, fill="black")

    return img


def artist_measurements(positioned, total_width):
    """
    Physical bar-by-bar geometry (position, width, height, and whether a
    bar is one of the two thin calibration markers) — the reference sheet
    a tattoo artist needs to draw or verify the design at any scale.

    Deliberately excludes anything about what's encoded (no letters,
    values, or segment/field names) — that's mechanism, not measurement,
    and stays out of anything shown to a user.
    """
    return {
        "canvas_width_mm": round(total_width, 2),
        "canvas_height_mm": round(CANVAS_H, 2),
        "bars": [
            {
                "index": i + 1,
                "type": "calibration" if b["segment"] == "cal" else "content",
                "x_mm": round(b["x"], 2),
                "width_mm": round(b["width"], 2),
                "height_mm": round(b["height"], 2),
            }
            for i, b in enumerate(positioned)
        ],
    }


def generate(name, day=None, month=None, year=None, message=None, additional_info=None):
    """High-level encode entry point used by the Flask app (HTML + API)."""
    bars = build_bars(name, day, month, year, message, additional_info)
    positioned, total_width = layout(bars)
    svg = build_svg(positioned, total_width)
    png = render_png(positioned, total_width)
    return {
        "bars": positioned,
        "total_width": total_width,
        "svg": svg,
        "png": png,
        "measurements": artist_measurements(positioned, total_width),
    }


def png_bytes(png_image: Image.Image) -> bytes:
    buf = BytesIO()
    png_image.save(buf, format="PNG")
    return buf.getvalue()


# ----------------------------------------------------------------------
# Decoding
# ----------------------------------------------------------------------

def load_binary_columns(path_or_fileobj, threshold=128):
    """Return a 1D array: for each pixel-column, the count of dark pixels.
    Accepts a file path (str) or a file-like object (e.g. an upload)."""
    img = Image.open(path_or_fileobj).convert("L")
    arr = np.array(img)
    dark = arr < threshold
    col_ink = dark.sum(axis=0)
    return col_ink


def find_bars(col_ink, min_ink=2):
    """
    Walk columns; group consecutive 'has ink' columns into bars.
    Returns list of dicts: {start, end, height_px, gap_before_px}
    """
    has_ink = col_ink > min_ink
    bars = []
    in_bar = False
    start = 0
    last_end = None
    for x, v in enumerate(has_ink):
        if v and not in_bar:
            in_bar = True
            start = x
        elif not v and in_bar:
            in_bar = False
            end = x - 1
            height_px = col_ink[start:end + 1].max()
            gap_before_px = start - last_end - 1 if last_end is not None else 0
            bars.append({"start": start, "end": end,
                         "height_px": int(height_px), "gap_before": gap_before_px})
            last_end = end
    if in_bar:
        end = len(has_ink) - 1
        height_px = col_ink[start:end + 1].max()
        gap_before_px = start - last_end - 1 if last_end is not None else 0
        bars.append({"start": start, "end": end,
                     "height_px": int(height_px), "gap_before": gap_before_px})
    return bars


def isolate_calibration(bars):
    """Strip the CAL_MIN/CAL_MAX bars (first and last) and return the
    content bars in between (header + name + optional segments + checksum)."""
    if len(bars) < 5:  # cal_min + header + >=1 name letter + checksum + cal_max
        raise WaveCodeError(
            "Not enough bars detected — check photo quality/crop, or the "
            "code may be too degraded to read."
        )
    cal_min_bar = bars[0]
    cal_max_bar = bars[-1]
    content = bars[1:-1]
    if len(content) < 3:  # header + >=1 name letter + checksum
        raise WaveCodeError("Not enough content bars between calibration marks.")
    return cal_min_bar, cal_max_bar, content


def split_segments(content, px_min, px_max):
    """
    content[0] is always the HEADER bar. Its decoded value tells us which
    of the three optional segments (date, message, additional info) to
    expect, and therefore exactly how many segment boundaries to look
    for among the remaining gaps — the two largest gaps within a run are
    always SEGMENT_GAPs (see WAVECODE_SPEC.md Section 5).

    Returns (name_bars, date_bars_or_None, message_bars_or_None,
             additional_bars_or_None, checksum_bar).
    """
    header_bar = content[0]
    rest = content[1:]
    if not rest:
        raise WaveCodeError("No content bars found after the header — check photo quality.")

    header_value = px_height_to_value(header_bar["height_px"], px_min, px_max, 1, 8)
    flags = _header_flags(header_value)

    segment_count = 2 + sum(flags.values())  # name + checksum, plus whichever optionals
    needed_splits = segment_count - 1

    if needed_splits == 0:
        groups = [rest]
    else:
        gaps = [(i, b["gap_before"]) for i, b in enumerate(rest) if i > 0]
        if len(gaps) < needed_splits:
            raise WaveCodeError("Could not find expected segment boundaries — check photo quality.")
        top = sorted(gaps, key=lambda t: t[1], reverse=True)[:needed_splits]
        split_points = sorted(i for i, _ in top)
        groups = []
        prev = 0
        for p in split_points:
            groups.append(rest[prev:p])
            prev = p
        groups.append(rest[prev:])

    i = 0
    name_bars = groups[i]
    i += 1
    if not name_bars:
        raise WaveCodeError("No name bars found.")

    date_bars = None
    if flags["has_date"]:
        date_bars = groups[i]
        i += 1
        if len(date_bars) != 3:
            raise WaveCodeError(
                f"Expected 3 date bars (day, month, year), found {len(date_bars)} — "
                "check photo quality."
            )

    message_bars = None
    if flags["has_message"]:
        message_bars = groups[i]
        i += 1
        if not message_bars:
            raise WaveCodeError("Expected message bars but found none — check photo quality.")

    additional_bars = None
    if flags["has_additional"]:
        additional_bars = groups[i]
        i += 1
        if not additional_bars:
            raise WaveCodeError("Expected additional-info bars but found none — check photo quality.")

    checksum_bars = groups[i]
    if len(checksum_bars) != 1:
        raise WaveCodeError(
            f"Expected 1 checksum bar, found {len(checksum_bars)} — check photo quality."
        )

    return name_bars, date_bars, message_bars, additional_bars, checksum_bars[0]


def px_height_to_value(height_px, px_min, px_max, v_min, v_max):
    """
    Convert a bar's pixel height to a real WaveCode value, using the
    two calibration bars (px_min <-> H_MIN, px_max <-> H_MAX) for an
    exact, photo-specific pixel-to-unit conversion.
    """
    if px_max == px_min:
        px_max = px_min + 1  # avoid div-by-zero on degenerate images
    h = H_MIN + (height_px - px_min) * (H_MAX - H_MIN) / (px_max - px_min)
    v = v_min + (h - H_MIN) * (v_max - v_min) / (H_MAX - H_MIN)
    return round(max(v_min, min(v_max, v)))


def _bars_to_word(bars, px_min, px_max):
    values = [px_height_to_value(b["height_px"], px_min, px_max, 1, 26) for b in bars]
    word = "".join(chr(ord('A') + v - 1) for v in values)
    return word, values


def decode(path_or_fileobj):
    col_ink = load_binary_columns(path_or_fileobj)
    bars = find_bars(col_ink)
    cal_min_bar, cal_max_bar, content = isolate_calibration(bars)

    px_min = cal_min_bar["height_px"]
    px_max = cal_max_bar["height_px"]

    name_bars, date_bars, message_bars, additional_bars, checksum_bar = split_segments(
        content, px_min, px_max
    )

    name, name_values = _bars_to_word(name_bars, px_min, px_max)

    date_info = None
    date_values = None
    if date_bars:
        day = px_height_to_value(date_bars[0]["height_px"], px_min, px_max, 1, 31)
        month = px_height_to_value(date_bars[1]["height_px"], px_min, px_max, 1, 12)
        year = px_height_to_value(date_bars[2]["height_px"], px_min, px_max, YEAR_MIN, YEAR_MAX)
        date_values = (day, month, year)
        date_info = {
            "day": day, "month": month, "year": year,
            "month_name": MONTH_NAMES[month],
            "date_str": f"{day} {MONTH_NAMES[month]} {year}",
        }

    message = None
    message_values = None
    if message_bars:
        message, message_values = _bars_to_word(message_bars, px_min, px_max)

    additional_info = None
    additional_values = None
    if additional_bars:
        additional_info, additional_values = _bars_to_word(additional_bars, px_min, px_max)

    checksum_found = px_height_to_value(checksum_bar["height_px"], px_min, px_max, 1, 26)
    checksum_expected = compute_checksum(name_values, date_values, message_values, additional_values)
    verified = checksum_found == checksum_expected

    # checksum_found/checksum_expected/raw bar counts are internal
    # consistency-check plumbing, not something an end user needs to see —
    # only the pass/fail outcome and a plain-language note are returned.
    return {
        "name": name,
        "date": date_info,
        "message": message,
        "additional_info": additional_info,
        "verified": verified,
        "note": None if verified else (
            "This photo may be unclear or the WaveCode may be degraded, so "
            "the details above might not be fully accurate. Try a clearer, "
            "straight-on, evenly-lit photo to confirm."
        ),
    }
