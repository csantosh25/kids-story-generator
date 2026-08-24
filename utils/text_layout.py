from PIL import ImageFont


def measure_text_width(draw, text, font):

    bbox = draw.textbbox((0, 0), text, font=font)

    return bbox[2] - bbox[0]


def wrap_text_to_width(draw, text, font, max_width):
    """Word-wraps text using actual rendered pixel width instead of a
    fixed character count, so wrapping stays accurate across font sizes."""

    words = text.split()

    if not words:
        return [""]

    lines = []
    current = words[0]

    for word in words[1:]:

        candidate = f"{current} {word}"

        if measure_text_width(draw, candidate, font) <= max_width:
            current = candidate
        else:
            lines.append(current)
            current = word

    lines.append(current)

    return lines


def fit_text_block(
    draw,
    text,
    font_path,
    max_width,
    max_height,
    start_size,
    min_size,
    line_spacing=1.15,
    step=2,
):
    """Finds the largest font size (from start_size down to min_size) at
    which the word-wrapped text fits within max_width x max_height.

    Returns (font, lines, line_height). If even min_size does not fit,
    returns the min_size result anyway (better than invisible/oversized
    text with no fallback)."""

    size = start_size
    result = None

    while size >= min_size:

        font = ImageFont.truetype(str(font_path), size)

        lines = wrap_text_to_width(draw, text, font, max_width)

        bbox = draw.textbbox((0, 0), "Agy", font=font)
        line_height = (bbox[3] - bbox[1]) * line_spacing

        total_height = line_height * len(lines)

        result = (font, lines, line_height)

        if total_height <= max_height:
            return result

        size -= step

    return result


def draw_text_block(draw, lines, font, x, y, line_height, fill, align="left", box_width=None):
    """Draws pre-wrapped lines starting at (x, y). Returns the y position
    immediately below the last drawn line, for stacking further content."""

    cy = y

    for line in lines:

        if align == "center" and box_width is not None:
            line_width = measure_text_width(draw, line, font)
            line_x = x + (box_width - line_width) / 2
        else:
            line_x = x

        draw.text((line_x, cy), line, font=font, fill=fill)

        cy += line_height

    return cy
