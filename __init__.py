def classFactory(iface):
    """Called by QGIS to load the plugin."""
    from .plugin import WebMapExporterPlugin
    return WebMapExporterPlugin(iface)
