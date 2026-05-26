# Mafe Agentic

La asistente AI de Golden Gate Grid. Vive en Slack, trabaja para todo el equipo, y también atiende como MCP server para clientes como Cowork y Claude Desktop.

## Qué hace

Cualquier persona del workspace de Golden Gate Grid puede @mencionarla en Slack:

- Lee canales y te resume lo que pasó esta semana
- Programa juntas en tu Google Calendar con link de Meet automático
- Crea presentaciones desde una plantilla de Google Slides — le pasas el link de la plantilla, le dices "hazme una propuesta para Tiendas Neto" y te entrega la copia rellena en tu Drive
- Crea Sheets con datos y fórmulas
- Hace gráficos y diagramas de flujo
- Crea Canvas en Slack para dejar info estructurada
- Crea listas en Slack (checklists, pendientes)

Cada quien usa SUS permisos. Mafe identifica al invocador por su correo de Workspace y actúa como esa persona via Domain-Wide Delegation. Cero credenciales personales, cero llaves descargadas.

## Arquitectura

Una sola app en Railway expone tres endpoints:

- `POST /slack/events` — recibe @menciones de Slack
- `POST /mcp` — sigue funcionando para clientes MCP (Cowork, Claude Desktop)
- `GET /` — healthcheck

Adentro:

- `server.py` — entry point Starlette, monta los tres endpoints
- `slack_handler.py` — Slack Bolt, escucha `app_mention`, dispara el cerebro
- `agent_brain.py` — orquestador Claude con tool use loop
- `system_prompt.py` — personalidad y reglas de marca
- `identity.py` — Slack user_id → email corporativo
- `auth.py` — Google auth via Service Account + DWD (con fallback a WIF)
- `drive.py` — cliente de Drive con upload + convert
- `tools/` — las 13 herramientas que Claude puede invocar:
  - `drive_tools.py`: slides_from_template, slides_from_scratch, sheets, chart, diagram
  - `calendar_tools.py`: list_events, find_free_slots, create_event (con Meet)
  - `slack_tools.py`: read_channel, read_thread, search, create_canvas, create_list
- `generators/` — constructores de archivos: pptx, xlsx, chart (matplotlib), diagram (graphviz)

## Variables de entorno (Railway)

| Variable | Para qué sirve |
|---|---|
| `ANTHROPIC_API_KEY` | Llamar a Claude (sk-ant-...) |
| `SLACK_BOT_TOKEN` | xoxb-... bot token de la Slack App |
| `SLACK_SIGNING_SECRET` | Verificar firma de Slack |
| `SLACK_USER_TOKEN` | xoxp-... opcional, habilita search.messages |
| `GOOGLE_SERVICE_ACCOUNT_JSON` | JSON del service account con DWD habilitado |
| `MAFE_DEFAULT_USER` | Email fallback si no hay identidad resoluble |
| `MAFE_MODEL` | Modelo Claude (default `claude-sonnet-4-5`) |
| `LOG_LEVEL` | `info` o `debug` |

## Cómo se invoca Mafe en Slack

En cualquier canal donde esté agregada:

- `@Mafe resúmeme lo que pasó esta semana en este canal`
- `@Mafe programa una junta con christian@goldengategrid.com mañana a las 10am, 30 minutos`
- `@Mafe usa esta plantilla https://docs.google.com/presentation/d/ABC/edit y arma una propuesta para Tiendas Neto, reemplazando {{CLIENTE}}, {{FECHA}} y {{INDUSTRIA}}`
- `@Mafe haz un Canvas con los acuerdos de la junta que tuvimos ayer`
- `@Mafe crea una lista de pendientes para el lanzamiento`

Responde en hilo para no llenar el canal.

## MCP server (para Cowork/Claude Desktop)

URL: `https://web-production-c5daa.up.railway.app/mcp`
Header requerido: `X-Mafe-User-Email: tu.correo@goldengategrid.com`

Esto sigue funcionando igual que antes — paralelo a Slack.

## Privacidad y seguridad

El service account vive solo en variables de entorno de Railway, encriptado. Cada request usa la identidad del invocador (Slack o MCP header) via DWD — los archivos quedan en SU Drive. No hay almacenamiento persistente — Mafe no guarda nada de lo que le pides. Logs en Railway no incluyen contenido de archivos generados ni texto completo de Slack. La firma de Slack se verifica en cada request entrante (Bolt lo hace solo).

---

Hecho con cariño por Golden Gate Grid.
