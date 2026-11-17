# Web Map Exporter (v0.3)

Web menu > Web Map Exporter > **Create web map**.

## Layers and Groups tab (per layer)
Export / Popups / Labels / Cluster, plus
- **Popup fields**: choose which fields show in the popup and on hover, their order and labels
- **Search by**: the field the search box looks in (e.g. a plot or building number)
- **Filter by**: the field used for the filter drop-down

## Widgets (Appearance tab)
Search box (position, hint text) with auto-zoom, highlight and a Navigate button;
Navigate in popups (Google Maps / Waze / Apple Maps); popups and highlight on hover (click pins the popup);
zoom to full extent, full screen, print, geolocate, mouse coordinates (click copies), overview mini map,
filter panel, measure tool, address search, legend, layers list, title and abstract, widget colors.

## Layers supported
Vector (points, lines, polygons) as GeoJSON; file rasters rendered by QGIS to an image
(size and format in Data export); XYZ tile layers (layers whose URL carries an access key are skipped).

## Output
`index.html`, `data.js`, `lib/` (Leaflet + plugins) and `images/` when rasters are used.
Open index.html directly. Settings are stored in the QGIS project.

## Not yet
WMS/WMTS/WFS, rule-based labels, scale-dependent visibility, layer groups in the web layer list,
OpenLayers writer, raster tiling for very large rasters.

## Bundled libraries (lib/)
Leaflet 1.9.4 (BSD-2), Leaflet.markercluster 1.5.3 (MIT), leaflet-measure 3.1.0 (MIT),
leaflet-control-geocoder 2.4.0 (BSD-2), Leaflet-MiniMap 3.6.1 (BSD-2). Each folder keeps its LICENSE.


## License

Web Map Exporter is licensed under the GNU General Public License, version 2 or (at your option) any later version. See the `LICENSE` file. Bundled third-party libraries retain their respective licenses; see the `lib/` subdirectories.
