"""Line display metadata: official colours."""

LINE_COLORS: dict[str, str] = {
    "Red": "#DA291C",
    "Orange": "#ED8B00",
    "Blue": "#003DA5",
    "Green": "#00843D",
    "Mattapan": "#DA291C",
}


def line_color(route_id: str) -> str:
    """Hex colour of a route: Green-B..E share the Green line colour."""
    if route_id.startswith("Green"):
        return LINE_COLORS["Green"]
    return LINE_COLORS.get(route_id, "#7C878E")


def hex_to_rgb(color: str) -> list[int]:
    c = color.lstrip("#")
    return [int(c[i : i + 2], 16) for i in (0, 2, 4)]
