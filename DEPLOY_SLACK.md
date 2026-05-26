# Cómo deployar la versión Slack de Mafe Agentic

Esta guía tiene los pasos que hay que hacer manualmente porque requieren tus credenciales o clicks en interfaces externas. Todo el código ya está escrito.

## Paso 1 — Subir el código nuevo a GitHub

En GitHub.com, ir al repo `mafe-agentic` y reemplazar estos archivos con los del paquete `mafe-agentic-slack-vX.zip`:

- `mafe_agentic/server.py`
- `mafe_agentic/system_prompt.py` (nuevo)
- `mafe_agentic/identity.py` (nuevo)
- `mafe_agentic/agent_brain.py` (nuevo)
- `mafe_agentic/slack_handler.py` (nuevo)
- `mafe_agentic/tools/__init__.py` (nuevo)
- `mafe_agentic/tools/drive_tools.py` (nuevo)
- `mafe_agentic/tools/calendar_tools.py` (nuevo)
- `mafe_agentic/tools/slack_tools.py` (nuevo)
- `pyproject.toml`
- `nixpacks.toml`
- `README.md`
- `slack-app-manifest.json` (nuevo)

La forma más fácil es borrar la carpeta `mafe-agentic` actual en GitHub y volver a subir todo desde el zip.

Cuando hagas commit, Railway detecta el push y arranca un redeploy automático.

## Paso 2 — Crear/actualizar la Slack App con el manifest

Ir a https://api.slack.com/apps. Si ya tienes una Slack App de Mafe creada antes, ábrela y vamos a actualizarla. Si no, crea una nueva con "From manifest".

En la pestaña "App Manifest", pegar el contenido de `slack-app-manifest.json` (está en el zip). Antes de guardar, revisar que la URL del `request_url` apunte a tu Railway:

```
https://web-production-c5daa.up.railway.app/slack/events
```

Guardar cambios. Si te pide reinstalar la app al workspace, hazlo — los scopes nuevos lo requieren.

Después de guardar, ve a "OAuth & Permissions" y copia:

- **Bot User OAuth Token** → empieza con `xoxb-...`
- (Opcional) **User OAuth Token** → empieza con `xoxp-...`, solo si quieres habilitar búsqueda global

Y en "Basic Information" copia:

- **Signing Secret**

## Paso 3 — Agregar variables nuevas en Railway

En tu proyecto Railway, en Variables del servicio `web`, agregar:

- `ANTHROPIC_API_KEY` = `sk-ant-...` (la que ya tenías para Mariana)
- `SLACK_BOT_TOKEN` = `xoxb-...` del paso 2
- `SLACK_SIGNING_SECRET` = lo del paso 2
- `SLACK_USER_TOKEN` = `xoxp-...` (solo si quieres búsqueda global, opcional)

Las que ya estaban (`GOOGLE_SERVICE_ACCOUNT_JSON`, `MAFE_DEFAULT_USER`) se quedan igual.

Apretar "Deploy" para aplicar los cambios.

## Paso 4 — Verificar que arrancó bien

En Railway, abrir los logs del deployment más reciente. Deberías ver al final:

```
Mafe Agentic arrancando en 0.0.0.0:8080
  POST /slack/events  ← Slack Events
  POST /mcp           ← MCP HTTP
  GET  /              ← healthcheck
Application startup complete.
Uvicorn running on http://0.0.0.0:8080
```

Si ves errores, copiame las últimas 15 líneas y lo arreglamos.

## Paso 5 — Verificar que Slack ya puede contactar la URL

En https://api.slack.com/apps, abrir tu app, "Event Subscriptions". Slack hace una verificación automática del Request URL cuando lo guardas. Si dice "Verified" en verde, todo bien. Si dice "Failed", probablemente Railway todavía no respondía cuando Slack intentó — esperar un minuto y darle "Retry".

## Paso 6 — Invitar a Mafe a un canal y probarla

En Slack, en cualquier canal:

1. Escribir `/invite @Mafe` para agregarla
2. Probar: `@Mafe hola, ¿qué puedes hacer?`
3. Probar algo real: `@Mafe ¿qué tengo en mi calendario esta semana?`

Si responde bonito y con la info correcta, está lista.

## Cosas que pueden salir y cómo arreglarlas

**"Aún no estoy autorizada con tu correo"**
El profile.email del usuario en Slack no está visible. Pídele al admin del workspace que active "Show email in profile" para todos.

**Mafe no responde**
1. Revisar en Railway logs si llegó el evento (debería loggear "@mention de UXXXX en CXXXX")
2. Si no llegó, revisar Event Subscriptions en Slack — probablemente el Request URL falló
3. Si llegó pero falló, copiame el stack trace

**Error con Google APIs (403 / forbidden)**
El service account no tiene Domain-Wide Delegation activado para los scopes nuevos. Chris (admin de Workspace) debe agregar los scopes en Security → API Controls → Domain-wide delegation.

**Error con plantillas de Slides**
El usuario que invocó no tiene acceso al Slides original. La plantilla debe ser visible para todo Golden Gate Grid o compartida con quien la quiera usar.

---

Cualquier cosa que se atore, mándame screenshot y lo arreglamos juntos.
