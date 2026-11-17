"""The "Create web map" dialog."""
import json
import os
import re
import shutil
import tempfile

from qgis.core import (
    Qgis,
    QgsCoordinateReferenceSystem,
    QgsCoordinateTransform,
    QgsLayerTree,
    QgsProject,
    QgsProviderRegistry,
    QgsRasterLayer,
    QgsVectorLayer,
)
from qgis.gui import QgsColorButton
from qgis.PyQt.QtCore import Qt, QUrl
from qgis.PyQt.QtGui import QColor, QDesktopServices, QIcon
from qgis.PyQt.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from .exporter import export_webmap, zip_folder
from .params import CHOICES, DEFAULTS

try:  # not every QGIS build ships QtWebEngine
    from qgis.PyQt.QtWebEngineWidgets import QWebEngineView
except Exception:  # pragma: no cover
    QWebEngineView = None

WGS84 = QgsCoordinateReferenceSystem("EPSG:4326")
STATE_SCOPE, STATE_KEY = "webmap_exporter", "state"
(COL_NAME, COL_EXPORT, COL_POPUP, COL_LABEL, COL_CLUSTER,
 COL_FIELDS, COL_SEARCH, COL_FILTER) = range(8)
NO_FIELD = "(none)"


def _is_xyz(layer):
    if not isinstance(layer, QgsRasterLayer) or layer.providerType() != "wms":
        return False
    try:
        return QgsProviderRegistry.instance().decodeUri("wms", layer.source()).get("type") == "xyz"
    except Exception:
        return False


def _is_file_raster(layer):
    return isinstance(layer, QgsRasterLayer) and layer.providerType() == "gdal"


class FieldsDialog(QDialog):
    """Choose which fields appear in a layer's popup, their order and their labels."""

    def __init__(self, layer, selection, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"Popup fields - {layer.name()}")
        self.resize(520, 420)

        hidden = set()
        display = {}
        for i, fld in enumerate(layer.fields()):
            display[fld.name()] = layer.attributeDisplayName(i)
            try:
                if layer.editorWidgetSetup(i).type() == "Hidden":
                    hidden.add(fld.name())
            except Exception:
                pass

        rows, used = [], set()
        if selection is None:
            for name in layer.fields().names():
                if name not in hidden:
                    rows.append([True, name, display[name]])
                    used.add(name)
        else:
            for f in selection:
                if f["name"] in display:
                    rows.append([True, f["name"], f.get("alias") or display[f["name"]]])
                    used.add(f["name"])
        for name in layer.fields().names():
            if name not in used:
                rows.append([False, name, display[name]])

        outer = QVBoxLayout(self)
        outer.addWidget(QLabel("Tick the fields to show (popup and hover). "
                               "Double-click an alias to rename it."))
        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["Show", "Field", "Label shown"])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        outer.addWidget(self.table, 1)
        self._fill(rows)

        row = QHBoxLayout()
        for text, fn in (("Select all", lambda: self._set_all(True)),
                         ("Select none", lambda: self._set_all(False)),
                         ("Move up", lambda: self._move(-1)),
                         ("Move down", lambda: self._move(1))):
            b = QPushButton(text)
            b.clicked.connect(fn)
            row.addWidget(b)
        outer.addLayout(row)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        outer.addWidget(buttons)

    def _fill(self, rows):
        self.table.setRowCount(len(rows))
        for r, (checked, name, alias) in enumerate(rows):
            show = QTableWidgetItem()
            show.setFlags(Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEnabled)
            show.setCheckState(Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked)
            field = QTableWidgetItem(name)
            field.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
            self.table.setItem(r, 0, show)
            self.table.setItem(r, 1, field)
            self.table.setItem(r, 2, QTableWidgetItem(alias))

    def _rows(self):
        out = []
        for r in range(self.table.rowCount()):
            out.append([self.table.item(r, 0).checkState() == Qt.CheckState.Checked,
                        self.table.item(r, 1).text(), self.table.item(r, 2).text()])
        return out

    def _set_all(self, on):
        rows = self._rows()
        for row in rows:
            row[0] = on
        self._fill(rows)

    def _move(self, step):
        r = self.table.currentRow()
        rows = self._rows()
        t = r + step
        if r < 0 or t < 0 or t >= len(rows):
            return
        rows[r], rows[t] = rows[t], rows[r]
        self._fill(rows)
        self.table.selectRow(t)

    def selection(self):
        return [{"name": n, "alias": a.strip() or n} for checked, n, a in self._rows() if checked]


class MainDialog(QDialog):
    def __init__(self, iface):
        super().__init__(iface.mainWindow())
        self.iface = iface
        self.setWindowTitle("Create web map")
        icon = QIcon(os.path.join(os.path.dirname(__file__), "icon.svg"))
        if not icon.isNull():
            self.setWindowIcon(icon)
        self.resize(1180, 680)
        self._w = {}              # setting key -> widget
        self._items = {}          # layer id -> dict(item, cols, layer, search, filter, fields_btn)
        self._fields_sel = {}     # layer id -> None (default) or [{"name", "alias"}]
        self._preview_dir = None

        outer = QVBoxLayout(self)
        split = QSplitter(Qt.Orientation.Horizontal)
        outer.addWidget(split, 1)

        tabs = QTabWidget()
        tabs.addTab(self._build_layers_tab(), "Layers and Groups")
        tabs.addTab(self._build_appearance_tab(), "Appearance")
        tabs.addTab(self._build_zoom_tab(), "Scale/Zoom")
        tabs.addTab(self._build_export_tab(), "Data export")
        split.addWidget(tabs)

        self.preview = None
        if QWebEngineView is not None:
            self.preview = QWebEngineView()
            split.addWidget(self.preview)
            split.setSizes([640, 540])

        row = QHBoxLayout()
        self.status = QLabel("")
        row.addWidget(self.status, 1)
        if self.preview is not None:
            btn_prev = QPushButton("Update preview")
            btn_prev.clicked.connect(self.update_preview)
            row.addWidget(btn_prev)
        btn_export = QPushButton("Export")
        btn_export.clicked.connect(self.run_export)
        btn_close = QPushButton("Close")
        btn_close.clicked.connect(self.close)
        row.addWidget(btn_export)
        row.addWidget(btn_close)
        outer.addLayout(row)

        self._load_state()

    # ------------------------------------------------------------------
    # Layers tab
    # ------------------------------------------------------------------
    def _build_layers_tab(self):
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["Layer", "Export", "Popups", "Labels", "Cluster",
                                   "Popup fields", "Search by", "Filter by"])
        self.tree.setColumnWidth(COL_NAME, 190)
        for col in (COL_EXPORT, COL_POPUP, COL_LABEL, COL_CLUSTER):
            self.tree.setColumnWidth(col, 62)
        self.tree.setColumnWidth(COL_FIELDS, 100)
        self.tree.setColumnWidth(COL_SEARCH, 110)
        self._add_nodes(self.tree.invisibleRootItem(), QgsProject.instance().layerTreeRoot())
        self.tree.expandAll()
        return self.tree

    def _add_nodes(self, parent, node):
        for child in node.children():
            if QgsLayerTree.isGroup(child):
                item = QTreeWidgetItem([child.name()])
                parent.addChild(item)
                self._add_nodes(item, child)
            elif QgsLayerTree.isLayer(child):
                layer = child.layer()
                if layer is not None:
                    self._add_layer_item(parent, layer, child.isVisible())

    def _add_layer_item(self, parent, layer, visible):
        item = QTreeWidgetItem([layer.name()])
        parent.addChild(item)
        info = {"item": item, "cols": set(), "layer": layer, "search": None,
                "filter": None, "fields_btn": None}
        is_vector = isinstance(layer, QgsVectorLayer) and \
            layer.geometryType() not in (Qgis.GeometryType.Null, Qgis.GeometryType.Unknown)

        if is_vector or _is_xyz(layer) or _is_file_raster(layer):
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            self._check(item, COL_EXPORT, visible)
            info["cols"].add(COL_EXPORT)
        else:
            item.setDisabled(True)
            item.setToolTip(COL_NAME, "Not supported yet (vector layers, file rasters and XYZ tiles only).")

        if is_vector:
            self._check(item, COL_POPUP, True)
            info["cols"].add(COL_POPUP)
            if layer.labelsEnabled():
                self._check(item, COL_LABEL, True)
                info["cols"].add(COL_LABEL)
            if layer.geometryType() == Qgis.GeometryType.Point:
                self._check(item, COL_CLUSTER, False)
                info["cols"].add(COL_CLUSTER)

            btn = QPushButton("All fields")
            btn.setToolTip("Choose which fields appear in the popup and on hover")
            btn.clicked.connect(lambda _=False, lid=layer.id(): self._edit_fields(lid))
            self.tree.setItemWidget(item, COL_FIELDS, btn)
            info["fields_btn"] = btn
            self._fields_sel[layer.id()] = None

            names = [NO_FIELD] + layer.fields().names()
            for key, col, tip in (("search", COL_SEARCH, "Field the search box looks in"),
                                  ("filter", COL_FILTER, "Field used for the filter drop-down")):
                combo = QComboBox()
                combo.addItems(names)
                combo.setToolTip(tip)
                self.tree.setItemWidget(item, col, combo)
                info[key] = combo
        self._items[layer.id()] = info

    @staticmethod
    def _check(item, col, on):
        item.setCheckState(col, Qt.CheckState.Checked if on else Qt.CheckState.Unchecked)

    def _edit_fields(self, lid):
        info = self._items[lid]
        dlg = FieldsDialog(info["layer"], self._fields_sel.get(lid), self)
        if dlg.exec():
            self._fields_sel[lid] = dlg.selection()
            self._refresh_fields_button(lid)

    def _refresh_fields_button(self, lid):
        btn = self._items[lid]["fields_btn"]
        sel = self._fields_sel.get(lid)
        if btn is not None:
            btn.setText("All fields" if sel is None else f"{len(sel)} field(s)")

    # ------------------------------------------------------------------
    # Other tabs
    # ------------------------------------------------------------------
    def _combo(self, key, label, form):
        w = QComboBox()
        w.addItems(CHOICES[key])
        form.addRow(label, w)
        self._w[key] = w

    def _check_box(self, key, label, form):
        w = QCheckBox()
        form.addRow(label, w)
        self._w[key] = w

    def _line(self, key, label, form, placeholder=""):
        w = QLineEdit()
        w.setPlaceholderText(placeholder)
        form.addRow(label, w)
        self._w[key] = w

    @staticmethod
    def _section(form, text):
        lab = QLabel(f"<b>{text}</b>")
        lab.setContentsMargins(0, 8, 0, 0)
        form.addRow(lab)

    def _build_appearance_tab(self):
        page = QWidget()
        form = QFormLayout(page)

        self._section(form, "Map")
        self._line("map_title", "Map title text", form, "Defaults to the project title")
        self._combo("title", "Title position", form)
        self._combo("abstract", "Abstract position", form)
        self._combo("basemap", "Basemap", form)
        self._combo("layers_list", "Layers list", form)
        self._check_box("legend", "Legend", form)

        self._section(form, "Search (set the field per layer in Layers and Groups)")
        self._combo("search_pos", "Search box position", form)
        self._line("search_placeholder", "Search box hint", form, "Search...")

        self._section(form, "Popups and navigation")
        self._check_box("popups_hover", "Show popups on hover (click to pin)", form)
        self._check_box("highlight_hover", "Highlight on hover", form)
        self._combo("nav_app", "Navigate button opens", form)

        self._section(form, "Widgets")
        self._check_box("home_button", "Zoom to full extent button", form)
        self._check_box("fullscreen", "Full screen button", form)
        self._check_box("print_button", "Print button", form)
        self._check_box("geolocate", "Geolocate user", form)
        self._check_box("coordinates", "Mouse coordinates (click to copy)", form)
        self._check_box("minimap", "Overview mini map", form)
        self._check_box("filter_panel", "Filter panel (field set per layer)", form)
        self._combo("measure", "Measure tool", form)
        self._combo("address_search", "Address search (places)", form)

        self._section(form, "Colors")
        for key, label in (("widget_icon", "Widget text / buttons"),
                           ("widget_background", "Widget background")):
            btn = QgsColorButton()
            form.addRow(label, btn)
            self._w[key] = btn

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(page)
        return scroll

    def _build_zoom_tab(self):
        page = QWidget()
        form = QFormLayout(page)
        self._check_box("restrict_extent", "Restrict to extent", form)
        self._combo("extent", "Extent", form)
        for key, label in (("min_zoom", "Min zoom level"), ("max_zoom", "Max zoom level")):
            spin = QSpinBox()
            spin.setRange(0, 28)
            form.addRow(label, spin)
            self._w[key] = spin
        return page

    def _build_export_tab(self):
        page = QWidget()
        form = QFormLayout(page)
        self._combo("precision", "Precision (decimals)", form)
        self._check_box("minify", "Minify data file", form)

        spin = QSpinBox()
        spin.setRange(256, 8192)
        spin.setSingleStep(256)
        spin.setToolTip("Longest side of the image made from each raster layer")
        form.addRow("Raster image size (px)", spin)
        self._w["raster_max_px"] = spin
        self._combo("raster_format", "Raster image format", form)

        self._combo("exporter", "Exporter", form)
        row = QHBoxLayout()
        edit = QLineEdit()
        browse = QPushButton("Browse...")
        browse.clicked.connect(self._browse)
        row.addWidget(edit)
        row.addWidget(browse)
        row.setContentsMargins(0, 0, 0, 0)
        holder = QWidget()
        holder.setLayout(row)
        form.addRow("Output folder", holder)
        self._w["output_folder"] = edit

        self._check_box("open_after_export", "Open after export", form)
        return page

    def _browse(self):
        path = QFileDialog.getExistingDirectory(
            self, "Choose output folder", self._w["output_folder"].text())
        if path:
            self._w["output_folder"].setText(path)

    # ------------------------------------------------------------------
    # Reading / writing widget values
    # ------------------------------------------------------------------
    def _get(self, key):
        w = self._w[key]
        if isinstance(w, QComboBox):
            return w.currentText()
        if isinstance(w, QCheckBox):
            return w.isChecked()
        if isinstance(w, QSpinBox):
            return w.value()
        if isinstance(w, QgsColorButton):
            return w.color().name()
        return w.text().strip()

    def _set(self, key, value):
        w = self._w[key]
        if isinstance(w, QComboBox):
            i = w.findText(str(value))
            if i >= 0:
                w.setCurrentIndex(i)
        elif isinstance(w, QCheckBox):
            w.setChecked(bool(value))
        elif isinstance(w, QSpinBox):
            w.setValue(int(value))
        elif isinstance(w, QgsColorButton):
            w.setColor(QColor(str(value)))
        else:
            w.setText(str(value))

    def settings(self):
        return {key: self._get(key) for key in self._w}

    def layer_opts(self):
        out = {}
        for lid, info in self._items.items():
            item, cols = info["item"], info["cols"]

            def on(col):
                return col in cols and item.checkState(col) == Qt.CheckState.Checked

            def field(combo):
                if combo is None or combo.currentText() == NO_FIELD:
                    return ""
                return combo.currentText()

            out[lid] = {
                "export": on(COL_EXPORT), "popup": on(COL_POPUP),
                "label": on(COL_LABEL), "cluster": on(COL_CLUSTER),
                "popup_fields": self._fields_sel.get(lid),
                "search_field": field(info["search"]),
                "filter_field": field(info["filter"]),
            }
        return out

    def _load_state(self):
        for key in self._w:
            self._set(key, DEFAULTS.get(key, ""))
        text, ok = QgsProject.instance().readEntry(STATE_SCOPE, STATE_KEY, "")
        if not (ok and text):
            return
        try:
            state = json.loads(text)
        except ValueError:
            return
        for key, value in state.get("settings", {}).items():
            if key in self._w:
                self._set(key, value)
        for lid, opts in state.get("layers", {}).items():
            info = self._items.get(lid)
            if info is None:
                continue
            for col, name in ((COL_EXPORT, "export"), (COL_POPUP, "popup"),
                              (COL_LABEL, "label"), (COL_CLUSTER, "cluster")):
                if col in info["cols"] and name in opts:
                    self._check(info["item"], col, opts[name])
            if info["fields_btn"] is not None and "popup_fields" in opts:
                self._fields_sel[lid] = opts["popup_fields"]
                self._refresh_fields_button(lid)
            for key, name in (("search", "search_field"), ("filter", "filter_field")):
                combo = info[key]
                if combo is not None and opts.get(name):
                    i = combo.findText(opts[name])
                    if i >= 0:
                        combo.setCurrentIndex(i)

    def _save_state(self):
        state = {"settings": self.settings(), "layers": self.layer_opts()}
        QgsProject.instance().writeEntry(STATE_SCOPE, STATE_KEY, json.dumps(state))

    # ------------------------------------------------------------------
    # Export and preview
    # ------------------------------------------------------------------
    def _canvas_bounds_wgs84(self):
        canvas = self.iface.mapCanvas()
        ct = QgsCoordinateTransform(canvas.mapSettings().destinationCrs(), WGS84,
                                    QgsProject.instance())
        return ct.transformBoundingBox(canvas.extent())

    def _do_export(self, out_dir):
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            return export_webmap(self.settings(), self.layer_opts(), out_dir,
                                 canvas_bounds=self._canvas_bounds_wgs84())
        finally:
            QApplication.restoreOverrideCursor()

    def _push(self, text, level, duration=8):
        self.iface.messageBar().pushMessage("Create web map", text, level=level, duration=duration)

    def update_preview(self):
        if self.preview is None:
            return
        self._cleanup_preview()
        self._preview_dir = tempfile.mkdtemp(prefix="webmap_preview_")
        try:
            result = self._do_export(self._preview_dir)
        except Exception as e:
            self.status.setText(f"Preview failed: {e}")
            return
        self.status.setText(f"Preview: {result.layer_count} layer(s)")
        self.preview.load(QUrl.fromLocalFile(result.index_path))

    def run_export(self):
        folder = self._get("output_folder")
        if not folder:
            self.status.setText("Choose an output folder first.")
            self._w["output_folder"].setFocus()
            return
        os.makedirs(folder, exist_ok=True)
        self._save_state()

        try:
            if self._get("exporter") == "ZIP file":
                tmp = tempfile.mkdtemp(prefix="webmap_zip_")
                try:
                    result = self._do_export(tmp)
                    name = re.sub(r"[^\w\-. ]+", "_", self._get("map_title") or
                                  QgsProject.instance().title() or "webmap").strip() or "webmap"
                    result.zip_path = zip_folder(tmp, os.path.join(folder, name + ".zip"))
                finally:
                    shutil.rmtree(tmp, ignore_errors=True)
                done, target = result.zip_path, folder
            else:
                target = folder
                # never mix with unrelated files: use a subfolder for non-empty folders
                if os.listdir(folder) and not os.path.exists(os.path.join(folder, "data.js")):
                    target = os.path.join(folder, "webmap")
                result = self._do_export(target)
                done = target
        except Exception as e:
            self._push(str(e), Qgis.MessageLevel.Critical, 12)
            self.status.setText(f"Export failed: {e}")
            return

        self._push(f"Exported {result.layer_count} layer(s) to {done}", Qgis.MessageLevel.Success)
        for w in result.warnings:
            self._push(w, Qgis.MessageLevel.Warning, 12)
        self.status.setText(f"Exported to {done}")

        if self._get("open_after_export"):
            if result.zip_path:
                QDesktopServices.openUrl(QUrl.fromLocalFile(target))
            else:
                QDesktopServices.openUrl(QUrl.fromLocalFile(result.index_path))

    # ------------------------------------------------------------------
    def _cleanup_preview(self):
        if self._preview_dir:
            shutil.rmtree(self._preview_dir, ignore_errors=True)
            self._preview_dir = None

    def closeEvent(self, event):
        self._cleanup_preview()
        super().closeEvent(event)
