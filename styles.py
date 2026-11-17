"""Convert QGIS renderers into small style specs the web page understands.

Single, categorized and graduated renderers on plain fields become compact
class lists. Everything else (rule-based, or classes driven by an expression)
is evaluated by QGIS itself for every feature and stored as a style table, so
the page never has to re-implement QGIS expressions.
"""
import json

from qgis.core import (
    Qgis,
    QgsCategorizedSymbolRenderer,
    QgsExpressionContextUtils,
    QgsGraduatedSymbolRenderer,
    QgsRenderContext,
    QgsSingleSymbolRenderer,
)
from qgis.PyQt.QtCore import Qt

MM_TO_PX = 3.78
HIDDEN = {"stroke": False, "fill": False}
DEFAULT_STYLE = {
    "color": "#3388ff", "weight": 2, "opacity": 1,
    "fillColor": "#3388ff", "fillOpacity": 0.4, "radius": 5,
}


def _alpha(color, factor):
    return round(color.alphaF() * factor, 3)


def _valid(color, fallback):
    return color if color is not None and color.isValid() else fallback


def _dash(pen_style):
    """Map a Qt pen style to an SVG dash array (or None for solid)."""
    try:
        table = {
            Qt.PenStyle.DashLine: "8,4",
            Qt.PenStyle.DotLine: "1,5",
            Qt.PenStyle.DashDotLine: "8,4,1,4",
            Qt.PenStyle.DashDotDotLine: "8,4,1,4,1,4",
        }
        return table.get(pen_style)
    except Exception:
        return None


def symbol_style(symbol, geom_type, layer_opacity=1.0):
    """Convert one QgsSymbol into a Leaflet path-style dict."""
    if symbol is None:
        return dict(DEFAULT_STYLE)

    k = symbol.opacity() * layer_opacity
    sl = symbol.symbolLayer(0) if symbol.symbolLayerCount() else None
    color = symbol.color()

    if geom_type == Qgis.GeometryType.Line:
        width_mm = sl.width() if sl is not None and hasattr(sl, "width") else 0.5
        style = {
            "color": color.name(), "opacity": _alpha(color, k),
            "weight": max(1, round(width_mm * MM_TO_PX, 1)),
        }
        pen = sl.penStyle() if sl is not None and hasattr(sl, "penStyle") else None
        dash = _dash(pen) if pen is not None else None
        if dash:
            style["dashArray"] = dash
        return style

    stroke = _valid(sl.strokeColor() if sl is not None else None, color)
    fill = _valid(sl.fillColor() if sl is not None else None, color)
    width_mm = sl.strokeWidth() if sl is not None and hasattr(sl, "strokeWidth") else 0.26

    style = {
        "color": stroke.name(), "opacity": _alpha(stroke, k),
        "weight": max(0.5, round(width_mm * MM_TO_PX, 1)),
        "fillColor": fill.name(), "fillOpacity": _alpha(fill, k),
    }
    try:
        if sl is not None and hasattr(sl, "brushStyle") and sl.brushStyle() == Qt.BrushStyle.NoBrush:
            style["fill"] = False
        if sl is not None and hasattr(sl, "strokeStyle"):
            if sl.strokeStyle() == Qt.PenStyle.NoPen:
                style["stroke"] = False
            else:
                dash = _dash(sl.strokeStyle())
                if dash:
                    style["dashArray"] = dash
    except Exception:
        pass

    if geom_type == Qgis.GeometryType.Point:
        size_mm = symbol.size() if hasattr(symbol, "size") else 2.0
        style["radius"] = max(2, round(size_mm * MM_TO_PX / 2, 1))
    return style


def _per_feature(layer, features, warnings):
    """Let QGIS pick each feature's symbol; return (spec, style index per feature)."""
    gt = layer.geometryType()
    lop = layer.opacity()
    renderer = layer.renderer().clone()
    ctx = QgsRenderContext()
    ctx.setRendererScale(0)  # 0 = ignore scale ranges in rules
    ectx = ctx.expressionContext()
    ectx.appendScopes(QgsExpressionContextUtils.globalProjectLayerScopes(layer))

    styles, seen, idxs = [], {}, []
    renderer.startRender(ctx, layer.fields())
    try:
        for f in features:
            ectx.setFeature(f)
            symbol = renderer.symbolForFeature(f, ctx)
            if symbol is None:
                idxs.append(-1)
                continue
            style = symbol_style(symbol, gt, lop)
            key = json.dumps(style, sort_keys=True)
            if key not in seen:
                seen[key] = len(styles)
                styles.append(style)
            idxs.append(seen[key])
    finally:
        renderer.stopRender(ctx)

    return {"type": "table", "styles": styles}, idxs


def build_style(layer, features, warnings):
    """Return (spec, per-feature style indexes or None) for a vector layer."""
    renderer = layer.renderer()
    gt = layer.geometryType()
    lop = layer.opacity()
    fields = layer.fields().names()

    if renderer is None:
        return {"type": "single", "style": dict(DEFAULT_STYLE)}, None

    if isinstance(renderer, QgsSingleSymbolRenderer):
        return {"type": "single", "style": symbol_style(renderer.symbol(), gt, lop)}, None

    if isinstance(renderer, QgsCategorizedSymbolRenderer) and renderer.classAttribute() in fields:
        cats = []
        for c in renderer.categories():
            if not c.renderState():
                continue
            value = c.value()
            cats.append({
                "value": "" if value is None else str(value),
                "label": c.label(),
                "style": symbol_style(c.symbol(), gt, lop),
            })
        return {"type": "categorized", "field": renderer.classAttribute(),
                "categories": cats, "fallback": HIDDEN}, None

    if isinstance(renderer, QgsGraduatedSymbolRenderer) and renderer.classAttribute() in fields:
        ranges = []
        for r in renderer.ranges():
            if not r.renderState():
                continue
            ranges.append({
                "lower": r.lowerValue(), "upper": r.upperValue(),
                "label": r.label(), "style": symbol_style(r.symbol(), gt, lop),
            })
        return {"type": "graduated", "field": renderer.classAttribute(),
                "ranges": ranges, "fallback": HIDDEN}, None

    # Rule-based, expression-driven classes, anything else: ask QGIS per feature.
    try:
        spec, idxs = _per_feature(layer, features, warnings)
        if renderer.type() != "ruleBased":
            warnings.append(f"{layer.name()}: renderer '{renderer.type()}' was evaluated "
                            "per feature (legend not available).")
        return spec, idxs
    except Exception as e:
        warnings.append(f"{layer.name()}: could not evaluate its style ({e}); used a default style.")
        return {"type": "single", "style": dict(DEFAULT_STYLE)}, None
