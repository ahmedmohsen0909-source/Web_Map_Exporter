import os

from qgis.core import QgsApplication
from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtGui import QIcon

try:  # Qt6 (QGIS 4) keeps QAction in QtGui
    from qgis.PyQt.QtGui import QAction
except ImportError:  # Qt5
    from qgis.PyQt.QtWidgets import QAction


class WebMapExporterPlugin:
    MENU = "&Web Map Exporter"

    def __init__(self, iface):
        self.iface = iface
        self.action = None
        self.dlg = None

    def initGui(self):
        icon = QIcon(os.path.join(os.path.dirname(__file__), "icon.svg"))
        if icon.isNull():
            icon = QgsApplication.getThemeIcon("/mActionSharingExport.svg")
        self.action = QAction(icon, "Create web map", self.iface.mainWindow())
        self.action.triggered.connect(self.run)
        self.iface.addPluginToWebMenu(self.MENU, self.action)
        self.iface.addWebToolBarIcon(self.action)

    def unload(self):
        if self.dlg is not None:
            self.dlg.close()
            self.dlg = None
        if self.action is not None:
            self.iface.removePluginWebMenu(self.MENU, self.action)
            self.iface.removeWebToolBarIcon(self.action)
            self.action = None

    def run(self):
        # imported here so a QtWebEngine problem cannot stop the plugin from loading
        from .dialog import MainDialog

        if self.dlg is None:
            self.dlg = MainDialog(self.iface)
            self.dlg.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
            self.dlg.destroyed.connect(lambda *_: setattr(self, "dlg", None))
        self.dlg.show()
        self.dlg.raise_()
