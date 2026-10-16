"""Convert source block provenance into safe top-left percentage viewer regions."""
import math

from app.schemas.shreckworld import CitationRectangle


def citation_rectangles(spans: list[dict]) -> list[CitationRectangle]:
    areas = []
    seen = set()
    for span in spans:
        box, width, height = span.get("bounding_box"), span.get("page_width"), span.get("page_height")
        if not isinstance(box, dict) or not width or not height:
            continue
        try:
            width, height = float(width), float(height)
            # Shared normalized provenance uses full names; accept older Docling
            # primitive keys as well when inspecting previously stored blocks.
            left, right, top, bottom = (float(box.get(full, box.get(short))) for full, short in
                                       (("left", "l"), ("right", "r"), ("top", "t"), ("bottom", "b")))
            if not all(math.isfinite(value) for value in (left, right, top, bottom, width, height)) or width <= 0 or height <= 0:
                continue
            origin = str(box.get("coord_origin", "")).upper()
            if origin == "BOTTOMLEFT":
                top, bottom = height - top, height - bottom
            elif origin != "TOPLEFT":
                continue
            x1, x2 = sorted((max(0, min(width, left)), max(0, min(width, right))))
            y1, y2 = sorted((max(0, min(height, top)), max(0, min(height, bottom))))
            key = (span["page_number"], x1, x2, y1, y2)
            if x2 > x1 and y2 > y1 and key not in seen:
                seen.add(key)
                areas.append(CitationRectangle(page_number=span["page_number"], left=100*x1/width,
                    top=100*y1/height, width=100*(x2-x1)/width, height=100*(y2-y1)/height))
        except (KeyError, TypeError, ValueError):
            continue
    return areas
