"""Turn project layers into the plain dicts that end up in data.js."""
import json
import os
import re

from qgis.core import (
    NULL,
    Qgis,
    QgsCoordinateReferenceSystem,
    QgsCoordinateTransform,
    QgsExpression,
    QgsExpressionContext,
    QgsExpressionContextUtils,
    QgsJsonExporter,
    QgsMapRendererParallelJob,
    QgsMapSettings,
    QgsProject,
    QgsProviderRegistry,
    QgsVectorLayerSimpleLabeling,
)
from qgis.PyQt.QtCore import QSize
from qgis.PyQt.QtGui import QColor, QImage

from .styles import build_style

WGS84 = QgsCoordinateReferenceSystem("EPSG:4326")
WEB_MERCATOR = QgsCoordinateReferenceSystem("EPSG:3857")
SECRET_IN_URL = re.compile(r"(api_?key|access_?token|token|key|sig(nature)?)=", re.I)


def layer_bounds_wgs84(layer, project):
    """Layer extent in EPSG:4326 as a QgsRectangle, or None if empty."""
    extent = layer.extent()
    if extent.isNull() or extent.isEmpty():
        return None
    ct = QgsCoordinateTransform(layer.crs(), WGS84, project)
    return ct.transformBoundingBox(extent)


# --------------------------------------------------------------------------
# Popups and labels
# --------------------------------------------------------------------------
def popup_fields(layer):
    out = []
    for i, fld in enumerate(layer.fields()):
        try:
            if layer.editorWidgetSetup(i).type() == "Hidden":
                continue
        except Exception:
            pass
        out.append({"name": fld.name(), "alias": layer.attributeDisplayName(i)})
    return out


def selected_popup_fields(layer, opts):
    """Popup fields in the order and with the aliases the user chose."""
    chosen = opts.get("popup_fields")
    if chosen is None:      # None = default (all visible fields); [] = no fields at all
        return popup_fields(layer)
    names = set(layer.fields().names())
    out = []
    for f in chosen:
        if f.get("name") in names:
            out.append({"name": f["name"], "alias": f.get("alias") or f["name"]})
    return out


def _known_field(layer, name):
    return name if name and name in layer.fields().names() else None


def _text(value):
    if value is None or value == NULL:
        return None
    s = str(value).strip()
    return s or None


def label_info(layer, features, warnings):
    """Simple labels only: one text per feature (fields or expressions)."""
    if not layer.labelsEnabled():
        return None
    labeling = layer.labeling()
    if not isinstance(labeling, QgsVectorLayerSimpleLabeling):
        warnings.append(f"{layer.name()}: only simple labels are supported yet; labels skipped.")
        return None

    settings = labeling.settings()
    values = []
    if settings.isExpression:
        expr = QgsExpression(settings.fieldName)
        ctx = QgsExpressionContext()
        ctx.appendScopes(QgsExpressionContextUtils.globalProjectLayerScopes(layer))
        expr.prepare(ctx)
        for f in features:
            ctx.setFeature(f)
            values.append(_text(expr.evaluate(ctx)))
    else:
        idx = layer.fields().indexOf(settings.fieldName)
        values = [_text(f.attribute(idx)) if idx >= 0 else None for f in features]

    fmt = settings.format()
    return {
        "values": values,
        "color": fmt.color().name(),
        "size": max(9, round(fmt.size() * 1.333)),
    }


# --------------------------------------------------------------------------
# Vector layers
# --------------------------------------------------------------------------
def collect_vector(layer, opts, precision, warnings):
    features = list(layer.getFeatures())
    spec, style_idx = build_style(layer, features, warnings)
    pfields = selected_popup_fields(layer, opts) if opts.get("popup", True) else []
    search_field = _known_field(layer, opts.get("search_field"))
    filter_field = _known_field(layer, opts.get("filter_field"))
    labels = label_info(layer, features, warnings) if opts.get("label", True) else None

    digits = 15 if precision == "maintain" else int(precision)
    geojson = json.loads(QgsJsonExporter(layer, digits).exportFeatures(features))

    keep = {p["name"] for p in pfields}
    if spec.get("field"):
        keep.add(spec["field"])
    keep.update(f for f in (search_field, filter_field) if f)

    for i, ft in enumerate(geojson.get("features", [])):
        props = ft.get("properties") or {}
        slim = {k: v for k, v in props.items() if k in keep}
        if style_idx is not None and i < len(style_idx):
            slim["_st"] = style_idx[i]
        if labels and i < len(labels["values"]) and labels["values"][i]:
            slim["_label"] = labels["values"][i]
        ft["properties"] = slim

    is_point = layer.geometryType() == Qgis.GeometryType.Point
    entry = {
        "id": layer.id(),
        "kind": "vector",
        "name": layer.name(),
        "geometry": Qgis.GeometryType(layer.geometryType()).name,
        "style": spec,
        "popup": pfields,
        "search_field": search_field,
        "filter_field": filter_field,
        "cluster": bool(opts.get("cluster", False)) and is_point,
        "opacity": layer.opacity(),
        "geojson": geojson,
    }
    if labels:
        entry["label"] = {"color": labels["color"], "size": labels["size"]}
    return entry


# --------------------------------------------------------------------------
# XYZ tile layers
# --------------------------------------------------------------------------
def collect_xyz(layer, warnings):
    """Return an XYZ layer entry, or None when the layer is not exportable."""
    if layer.providerType() != "wms":
        return None
    try:
        parts = QgsProviderRegistry.instance().decodeUri("wms", layer.source())
    except Exception:
        return None
    if parts.get("type") != "xyz" or not parts.get("url"):
        return None

    url = parts["url"]
    if SECRET_IN_URL.search(url):
        warnings.append(f"{layer.name()}: its address carries an access key; "
                        "not exported so the key is not published.")
        return None

    tms = "{-y}" in url
    return {
        "id": layer.id(),
        "kind": "xyz",
        "name": layer.name(),
        "url": url.replace("{-y}", "{y}"),
        "tms": tms,
        "minZoom": int(parts.get("zmin", 0) or 0),
        "maxZoom": int(parts.get("zmax", 19) or 19),
        "opacity": layer.opacity(),
    }


# --------------------------------------------------------------------------
# Raster layers (rendered by QGIS, so the web map looks like the project)
# --------------------------------------------------------------------------
def collect_raster(layer, out_dir, max_px, fmt, warnings):
    """Render a file-based raster to an image in Web Mercator.

    The image is drawn in EPSG:3857, so Leaflet can place it exactly from its
    corner coordinates. Returns a layer entry, or None if not exportable.
    """
    if layer.providerType() != "gdal":
        return None, None

    ct_in = QgsCoordinateTransform(layer.crs(), WEB_MERCATOR, QgsProject.instance())
    extent = ct_in.transformBoundingBox(layer.extent())
    if extent.isNull() or extent.isEmpty() or extent.width() <= 0 or extent.height() <= 0:
        warnings.append(f"{layer.name()}: empty extent, skipped.")
        return None, None

    scale = float(max_px) / max(extent.width(), extent.height())
    size = QSize(max(1, int(extent.width() * scale)), max(1, int(extent.height() * scale)))

    jpeg = fmt.upper() == "JPEG"
    settings = QgsMapSettings()
    settings.setLayers([layer])
    settings.setDestinationCrs(WEB_MERCATOR)
    settings.setExtent(extent)
    settings.setOutputSize(size)
    settings.setOutputImageFormat(QImage.Format.Format_ARGB32_Premultiplied)
    settings.setBackgroundColor(QColor(255, 255, 255) if jpeg else QColor(0, 0, 0, 0))

    job = QgsMapRendererParallelJob(settings)
    job.start()
    job.waitForFinished()
    image = job.renderedImage()

    images_dir = os.path.join(out_dir, "images")
    os.makedirs(images_dir, exist_ok=True)
    safe = re.sub(r"[^\w\-]+", "_", layer.id())
    name = f"{safe}.{'jpg' if jpeg else 'png'}"
    ok = image.save(os.path.join(images_dir, name), "JPEG" if jpeg else "PNG", 90 if jpeg else -1)
    if not ok:
        warnings.append(f"{layer.name()}: could not write the image, skipped.")
        return None, None

    ct_out = QgsCoordinateTransform(WEB_MERCATOR, WGS84, QgsProject.instance())
    b = ct_out.transformBoundingBox(extent)
    return {
        "id": layer.id(),
        "kind": "image",
        "name": layer.name(),
        "url": "images/" + name,
        "bounds": [[b.yMinimum(), b.xMinimum()], [b.yMaximum(), b.xMaximum()]],
        "opacity": layer.opacity(),
    }, b

