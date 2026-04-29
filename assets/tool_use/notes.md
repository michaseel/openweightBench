# Sprint Notes — KW 17

## Erledigt
- API-Endpoint /users mit Pagination
- Migrations für `orders` Tabelle eingespielt

## Offen
- TODO: Caching-Layer für Produktsuche evaluieren (Redis vs in-memory)
- TODO: Logging vereinheitlichen — momentan mischen wir loguru + logging
- Bugfix: Datumsformat in der Rechnungs-PDF (siehe Issue #4711)
- TODO: Stripe-Webhook-Handler braucht Idempotency-Keys

## Notizen aus dem Standup
Kunde X meldet, dass beim Login-Redirect manchmal die `next`-URL verloren geht. Reproduzierbar nur in Safari iOS. Verdacht auf Service-Worker-Cache.

TODO: Safari-Repro-Setup auf TestFlight provisionieren.
