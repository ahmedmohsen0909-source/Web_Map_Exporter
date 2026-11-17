"""All export options in one place: choices shown in the dialog and defaults."""

POSITIONS = ["None", "upper right", "lower right", "lower left", "upper left"]

BASEMAPS = {
    "OpenStreetMap": {
        "url": "https://tile.openstreetmap.org/{z}/{x}/{y}.png",
        "attribution": "&copy; OpenStreetMap contributors", "maxZoom": 19,
    },
    "CartoDB Positron": {
        "url": "https://{s}.basemaps.cartocdn.com/light_all/{z}/{x}/{y}{r}.png",
        "attribution": "&copy; OpenStreetMap contributors &copy; CARTO", "maxZoom": 20,
    },
    "CartoDB Dark Matter": {
        "url": "https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png",
        "attribution": "&copy; OpenStreetMap contributors &copy; CARTO", "maxZoom": 20,
    },
    "Esri World Imagery": {
        "url": "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
        "attribution": "Tiles &copy; Esri", "maxZoom": 19,
    },
    "OpenTopoMap": {
        "url": "https://{s}.tile.opentopomap.org/{z}/{x}/{y}.png",
        "attribution": "&copy; OpenStreetMap contributors, SRTM | &copy; OpenTopoMap (CC-BY-SA)",
        "maxZoom": 17,
    },
}

CHOICES = {
    "title": POSITIONS,
    "abstract": POSITIONS,
    "layers_list": ["None", "Collapsed", "Expanded"],
    "measure": ["None", "Metric", "Imperial"],
    "address_search": ["None", "Nominatim OSM"],
    "basemap": ["None"] + list(BASEMAPS.keys()),
    "extent": ["Canvas extent", "Fit to vector layers extent"],
    "precision": ["maintain"] + [str(i) for i in range(1, 16)],
    "exporter": ["Folder", "ZIP file"],
    "search_pos": POSITIONS,
    "nav_app": ["None", "Google Maps", "Waze", "Apple Maps"],
    "raster_format": ["PNG", "JPEG"],
}

DEFAULTS = {
    # Appearance
    "title": "upper right",
    "abstract": "None",
    "layers_list": "Expanded",
    "legend": True,
    "geolocate": False,
    "measure": "None",
    "address_search": "None",
    "popups_hover": False,
    "highlight_hover": False,
    "basemap": "OpenStreetMap",
    # Widgets
    "search_pos": "upper left",
    "search_placeholder": "Search...",
    "nav_app": "Google Maps",
    "fullscreen": True,
    "home_button": True,
    "print_button": False,
    "coordinates": False,
    "minimap": False,
    "filter_panel": True,
    "widget_icon": "#444444",
    "widget_background": "#f8f8f8",
    # Data export
    "precision": "6",
    "minify": True,
    "exporter": "Folder",
    "raster_max_px": 2048,
    "raster_format": "PNG",
    # Scale / zoom
    "restrict_extent": False,
    "extent": "Canvas extent",
    "min_zoom": 1,
    "max_zoom": 28,
    # Output
    "output_folder": "",
    "open_after_export": True,
    "map_title": "",
}

# Per-layer options (set in the Layers and Groups tab)
LAYER_DEFAULTS = {
    "export": True, "popup": True, "label": True, "cluster": False,
    "popup_fields": None,   # None = all visible fields; else [{"name", "alias"}] in order
    "search_field": "",     # field used by the search box ("" = layer is not searchable)
    "filter_field": "",     # field used by the filter panel ("" = no filter)
}


def merged(settings):
    """Defaults overlaid with whatever the caller provides."""
    out = dict(DEFAULTS)
    out.update(settings or {})
    return out
