# 🏆 Otra Coronación de Gloria

Rastreador automático de argentinos y argentinas saliendo **1°, 2° o 3°** en cualquier
competencia mundial — deportes, olimpiadas científicas, gastronomía, tango, lo que sea.

**Sitio:** https://otracoronacion.github.io/

## Cómo funciona

1. Todas las mañanas un workflow de GitHub Actions ejecuta
   `scraper/scrape.py`, que consulta Google News RSS con ~20 búsquedas en español e inglés.
2. Un pipeline de filtros descarta previas, aniversarios, apuestas y torneos no mundiales,
   y deduplica la cobertura de distintos medios en un solo evento.
3. Los eventos confirmados se agregan a `data/podios.json`, que alimenta la landing
   (GitHub Pages).
4. **Solo si hubo una coronación**, se envía un email a los suscriptores vía
   [Buttondown](https://buttondown.com). Días sin podio = silencio total.

## Estructura

```
scraper/scrape.py      # scraping + filtros + dedup (stdlib puro)
scraper/send_email.py  # email diario vía Buttondown API
data/podios.json       # feed público de eventos
data/seen.json         # estado de deduplicación
index.html             # landing page (GitHub Pages)
.github/workflows/     # daily.yml (cron) + dryrun.yml (calibración)
```

## Operación

- **Corrida manual:** Actions → "Coronación diaria" → Run workflow (`dry_run=1` para
  crear el email como borrador en vez de enviarlo).
- **Calibración:** Actions → "Dry run (calibración)" → publica `candidates.json` y
  `buttondown.json` en el branch `calibration`.
- **Reenvío manual:** Actions → "Reenviar coronación" con el `id` del evento
  (está en `data/podios.json`). Útil si un envío falló definitivamente.
- **Secreto requerido:** `BUTTONDOWN_API_KEY` (Settings → Secrets → Actions).

Hecho con orgullo y un poco de código 🧉
