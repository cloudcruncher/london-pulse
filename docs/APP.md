# Path to an app

The site is already an installable PWA (manifest, service worker, offline shell, mobile layout), and all data is a versioned static JSON API. That makes the app a thin client rather than a rebuild.

## Phases
1. **Now: PWA.** "Add to home screen" on iOS and Android. Cached shell and stale-while-revalidate data, so it opens instantly and works offline with yesterday's data.
2. **Next: on-device features, no backend.** Geolocation for "venues near me" (distance computed client-side from `venues.json`, location never leaves the device), saved boroughs in local storage, share links (`#map/Camden`).
3. **Then: store apps.** Wrap the PWA with Capacitor for App Store and Play listings, reusing the same code.
4. **Later: alerts.** "New venues in my borough" push notifications. This is the first feature needing a backend: store a device token and watched boroughs, and have the daily job fan out notifications from `events.json`. Keep it small (a worker plus a key-value store) and the rest stays static.

## Design rules that keep this cheap
- Data contract first: every file has `schema_version`; additive changes only, breaking changes go to `/api/v2/`.
- No accounts. Preferences live on the device until alerts need a token.
- Attribution and the Open Government Licence notice stay in the app's About screen.
- Signals not verdicts: closures are inferred, so the UI always says so.

## Open questions
- Notification provider and cost at scale.
- Whether ward-level detail (not just borough) is worth a postcode-to-ward lookup.
