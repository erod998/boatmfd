# Vendored browser libraries

The boat has no internet. Anything loaded from a CDN works at the dock and fails on the
water, and for the map library that is not a degraded map -- it is no map at all: without
`L`, app.js throws on its first `L.map()` call and the whole chart screen dies.

So these are committed rather than fetched:

| File | Version | Source |
|---|---|---|
| `leaflet.css`, `leaflet.js` | 1.9.4 | cdnjs.cloudflare.com/ajax/libs/leaflet/1.9.4/ |
| `leaflet-rotate.js` | 0.2.8 | cdn.jsdelivr.net/npm/leaflet-rotate@0.2.8/dist/leaflet-rotate-src.js |
| `images/` | 1.9.4 | Leaflet's own control/marker sprites, referenced by leaflet.css |
| `geojson-vt.js` | 3.2.1 (ISC, Mapbox) | cdn.jsdelivr.net/npm/geojson-vt@3.2.1/geojson-vt.js -- cuts the chart into map tiles in the browser |

To upgrade, re-download the same paths at the new version, bump the table, and check the
chart screen still draws. Do not replace these with CDN URLs.
