# dispatch.castafero.com

A clean, ad-free live dispatch board for Chester and Delaware County, PA.

- **Chester County:** live Fire, EMS and Traffic calls from the county's official public [WebCAD](https://webcad.chesco.org/WebCad/webcad.asp) page. Checked every 5 minutes and kept for 48 hours. Cross streets are placed on the map with OpenStreetMap's Nominatim, and the results are cached in `state/geocache.json`.
- **Delaware County:** the county publishes no public live 911 feed. The page links Broadcastify scanner audio and shows NWS alerts and local incident news (satire sites excluded).
- Chester County road closures and weather alerts.

The page is a static shell (`index.html`) that loads `data.json` every 60 seconds. It has filters by type, town and text, an active-only view, a map, and optional browser notifications for new calls.

`build.py` is standard-library Python. `.github/workflows/update.yml` runs it every 5 minutes and carries call history between runs in the Actions cache. Yesterday's calls are archived to `archive/` daily.
