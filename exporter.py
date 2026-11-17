"""Export orchestration. No UI code here, so it can be run from scripts too."""
import json
import os
import shutil
import zipfile

from qgis.core import (
    Qgis,
    QgsProject,
    QgsRasterLayer,
    QgsRectangle,
    QgsVectorLayer,
)

from .data import collect_raster, collect_vector, collect_xyz, layer_bounds_wgs84
from .params import BASEMAPS, LAYER_DEFAULTS, merged

PLUGIN_DIR = os.path.dirname(__file__)

# Settings the web page reads (everything else stays in QGIS)
PAGE_SETTINGS = (
    "title", "abstract", "layers_list", "legend", "geolocate", "measure",
    "address_search", "popups_hover", "highlight_hover", "basemap",
    "widget_icon", "widget_background", "restrict_extent", "min_zoom", "max_zoom",
    "search_pos", "search_placeholder", "nav_app", "fullscreen", "home_button",
    "print_button", "coordinates", "minimap", "filter_panel",
)


class ExportResult:
    def __init__(self):
        self.out_dir = None
        self.index_path = None
        self.zip_path = None
        self.layer_count = 0
        self.warnings = []


def _rect_to_bounds(rect):
    return [[rect.yMinimum(), rect.xMinimum()], [rect.yMaximum(), rect.xMaximum()]]


def export_webmap(settings, layer_opts, out_dir, project=None, canvas_bounds=None):
    """Write a standalone web map into out_dir.

    settings      -- dict of options (see params.DEFAULTS)
    layer_opts    -- {layer_id: {"export": bool, "popup": bool, "label": bool, "cluster": bool}}
    canvas_bounds -- QgsRectangle in EPSG:4326 (the current map canvas), optional
    """
    project = project or QgsProject.instance()
    s = merged(settings)
    result = ExportResult()
    result.out_dir = out_dir
    os.makedirs(out_dir, exist_ok=True)
    shutil.rmtree(os.path.join(out_dir, "images"), ignore_errors=True)  # stale raster images

    root = project.layerTreeRoot()
    entries = []
    total = None

    # layerOrder() is top-first; Leaflet draws in the order layers are added
    for layer in reversed(root.layerOrder()):
        opts = dict(LAYER_DEFAULTS)
        opts.update((layer_opts or {}).get(layer.id(), {}))
        if not opts["export"]:
            continue

        node = root.findLayer(layer.id())
        visible = node.isVisible() if node is not None else True
        bounds = None

        if isinstance(layer, QgsVectorLayer):
            if layer.geometryType() in (Qgis.GeometryType.Null, Qgis.GeometryType.Unknown):
                result.warnings.append(f"{layer.name()}: no geometry, skipped.")
                continue
            entry = collect_vector(layer, opts, s["precision"], result.warnings)
            bounds = layer_bounds_wgs84(layer, project)
        elif isinstance(layer, QgsRasterLayer):
            before = len(result.warnings)
            entry = collect_xyz(layer, result.warnings)
            if entry is None:
                entry, bounds = collect_raster(
                    layer, out_dir, s["raster_max_px"], s["raster_format"], result.warnings)
            if entry is None:
                if len(result.warnings) == before:
                    result.warnings.append(
                        f"{layer.name()}: only file rasters and XYZ tiles are supported yet; skipped.")
                continue
        else:
            continue

        entry["visible"] = visible
        entries.append(entry)
        if bounds is not None:
            if total is None:
                total = QgsRectangle(bounds)
            else:
                total.combineExtentWith(bounds)

    if not entries:
        raise ValueError("No exportable layers were selected.")

    if s["extent"] == "Fit to vector layers extent" and total is not None:
        view = total
    else:
        view = canvas_bounds or total

    title = (s["map_title"] or project.title() or "Web Map").strip()
    page = {k: s[k] for k in PAGE_SETTINGS}
    webmap = {
        "title": title,
        "abstract": project.metadata().abstract() or "",
        "settings": page,
        "bounds": _rect_to_bounds(view) if view is not None and not view.isEmpty() else None,
        "basemaps": BASEMAPS,
        "layers": entries,
    }

    with open(os.path.join(out_dir, "data.js"), "w", encoding="utf-8") as f:
        f.write("window.WEBMAP = ")
        if s["minify"]:
            json.dump(webmap, f, ensure_ascii=False, separators=(",", ":"))
        else:
            json.dump(webmap, f, ensure_ascii=False, indent=1)
        f.write(";\n")

    shutil.copytree(os.path.join(PLUGIN_DIR, "lib"), os.path.join(out_dir, "lib"),
                    dirs_exist_ok=True)

    with open(os.path.join(PLUGIN_DIR, "templates", "map.html"), encoding="utf-8") as f:
        html = f.read().replace("{{TITLE}}", _escape_html(title))
    result.index_path = os.path.join(out_dir, "index.html")
    with open(result.index_path, "w", encoding="utf-8") as f:
        f.write(html)

    result.layer_count = len(entries)
    return result


def zip_folder(folder, zip_path):
    """ZIP the contents of folder (index.html at the archive root)."""
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as z:
        for base, _dirs, files in os.walk(folder):
            for name in files:
                full = os.path.join(base, name)
                z.write(full, os.path.relpath(full, folder))
    return zip_path


def _escape_html(text):
    return (text.replace("&", "&amp;").replace("<", "&lt;")
                .replace(">", "&gt;").replace('"', "&quot;"))
