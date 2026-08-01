#!/usr/bin/env python3
"""
Envía el email diario vía Buttondown SOLO si scrape.py dejó new_events.json.
Env:
  BUTTONDOWN_API_KEY  (requerido)
  DRY_RUN=1           crea el email como borrador (no envía)
"""
import json
import os
import sys
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
NEW_EVENTS_PATH = os.path.join(ROOT, "new_events.json")
API = "https://api.buttondown.com/v1/emails"

MEDAL_EMOJI = {"oro": "🥇", "plata": "🥈", "bronce": "🥉", "medalla": "🏅", "podio": "🏅"}
MEDAL_TXT = {
    "oro": "Campeones del mundo",
    "plata": "Subcampeonato mundial",
    "bronce": "Bronce mundial",
    "medalla": "Medalla mundial",
    "podio": "Podio mundial",
}

def main():
    if not os.path.exists(NEW_EVENTS_PATH):
        print("No hay eventos nuevos. Hoy no se envía nada. 🧉")
        return
    key = os.environ.get("BUTTONDOWN_API_KEY")
    if not key:
        print("FALTA BUTTONDOWN_API_KEY", file=sys.stderr)
        sys.exit(1)

    with open(NEW_EVENTS_PATH, encoding="utf-8") as f:
        events = json.load(f)
    if not events:
        print("new_events.json vacío; no se envía.")
        return

    if len(events) == 1:
        ev = events[0]
        subject = f"{MEDAL_EMOJI.get(ev['medal'], '🏅')} Otra coronación de gloria"
    else:
        subject = f"🇦🇷 {len(events)} coronaciones de gloria hoy"

    lines = ["**¡Buen día! Hoy te despertás coronado: Argentina al podio del mundo.**", ""]
    for ev in events:
        emoji = MEDAL_EMOJI.get(ev["medal"], "🏅")
        src = f" — _{ev['source']}_" if ev.get("source") else ""
        lines.append(f"{emoji} **[{ev['title']}]({ev['url']})**{src}")
        lines.append("")
    lines += [
        "---",
        "",
        "Ver todas las coronaciones: https://jmmenendez1.github.io/coronadosdegloria/",
        "",
        "_Recibís este correo porque te suscribiste a **Otra Coronación de Gloria**, el rastreador de argentinos en podios mundiales._",
    ]
    body = "\n".join(lines)

    payload = {"subject": subject, "body": body}
    payload["status"] = "draft" if os.environ.get("DRY_RUN") == "1" else "about_to_send"

    req = urllib.request.Request(
        API,
        data=json.dumps(payload).encode(),
        headers={"Authorization": f"Token {key}", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            resp = json.load(r)
            print(f"Email {'BORRADOR creado' if payload['status']=='draft' else 'ENVIADO'}: {resp.get('id')} — {subject}")
    except urllib.error.HTTPError as e:
        print(f"Buttondown error {e.code}: {e.read().decode()[:500]}", file=sys.stderr)
        sys.exit(1)

if __name__ == "__main__":
    main()
