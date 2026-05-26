# Mafe Agentic

El MCP server agéntico de Golden Gate Grid. Vive en la nube (Railway), escucha por HTTP, y crea cosas en el Google Drive de cada persona que la invoca.

## Por qué Mafe Agentic y no Mariana

Mariana original era un bot de Slack corriendo en la Mac de Maria — útil pero atada a esa máquina. Mariana MCP fue el paso intermedio — corre local pero ya como MCP server. **Mafe Agentic** es el paso definitivo: una sola instancia hospedada en Railway que todo el equipo de Golden Gate Grid usa al mismo tiempo, cada quien con su cuenta de Google.

## Qué sabe hacer

- `generar_presentacion` — Google Slides nativo en tu Drive
- `generar_sheets` — Google Sheets con fórmulas
- `generar_grafico` — PNG (bar/line/pie) en tu Drive
- `generar_diagrama_flujo` — PNG con cajas/rombos/elipses

## Cómo funciona la magia multi-usuario

**Workload Identity Federation + Domain-Wide Delegation.** Cero llaves estáticas. Esto es el next-level de seguridad.

Lo cuento por partes:

1. Un admin de Golden Gate Grid (Chris) crea el **service account** `mafe-agentic-sa` en Google Cloud
2. Le habilita **Domain-Wide Delegation** en Google Workspace Admin
3. Eso le da permiso de "actuar como" cualquier usuario del dominio `goldengategrid.com`
4. **Aquí entra WIF**: configuramos un Workload Identity Pool que confía en el OIDC issuer de Railway
5. Cuando arranca Mafe Agentic en Railway, Railway le da un OIDC token corto (no es secreto)
6. Ese token se intercambia con GCP por credenciales temporales (~1h)
7. Esas credenciales impersonan a `mafe-agentic-sa`
8. Para cada request, `mafe-agentic-sa` impersona al usuario final via DWD (header `X-Mafe-User-Email`)
9. Los archivos quedan en el Drive de ese usuario

**Por qué esto es mejor que pegar la llave JSON en Railway:**

- **Cero credenciales permanentes** en Railway. Todo es temporal.
- **Imposible filtrar la "llave"** porque no existe — los tokens duran 1 hora.
- **Audit logs nativos** en Google Cloud Audit Logs.
- **Rotación automática** sin tocar Railway nunca.
- **Pasa cualquier security review** de cliente enterprise.

## Configuración inicial (una sola vez, hecha por Chris/Maria)

### En Google Cloud Console
1. Crear service account `mafe-agentic@<project>.iam.gserviceaccount.com`
2. Generar clave JSON y guardarla segura
3. Habilitar APIs: Drive, Slides, Sheets, Calendar
4. Anotar el Client ID del service account

### En Google Workspace Admin
1. Security → API Controls → Domain-wide delegation
2. Add new → pegar Client ID del service account
3. Scopes:
   ```
   https://www.googleapis.com/auth/drive.file,
   https://www.googleapis.com/auth/spreadsheets,
   https://www.googleapis.com/auth/presentations,
   https://www.googleapis.com/auth/calendar
   ```
4. Authorize

### En Railway
1. Crear proyecto Mafe Agentic desde este repo
2. Variables de entorno:
   - `GOOGLE_SERVICE_ACCOUNT_JSON` — JSON completo del service account (en una sola línea)
   - `MAFE_DEFAULT_USER` — `maria.fernanda@goldengategrid.com` (para testing)
   - `LOG_LEVEL` — `info`
3. Deploy automático. URL queda algo como `mafe-agentic-production.up.railway.app`

## Cómo se conecta cada persona del equipo

### Desde Cowork

Configuración → MCP Servers → Add Server (HTTP):
```json
{
  "name": "mafe-agentic",
  "url": "https://mafe-agentic-production.up.railway.app/mcp",
  "headers": {
    "X-Mafe-User-Email": "tu.correo@goldengategrid.com"
  }
}
```

### Desde Claude Desktop

En `~/Library/Application Support/Claude/claude_desktop_config.json`:
```json
{
  "mcpServers": {
    "mafe-agentic": {
      "url": "https://mafe-agentic-production.up.railway.app/mcp",
      "headers": {
        "X-Mafe-User-Email": "tu.correo@goldengategrid.com"
      }
    }
  }
}
```

### Desde Slack (via Agentforce connectors)

Cuando esté disponible en tu workspace, pegar la URL como MCP Connector y Slack identificará al usuario automáticamente.

## Arquitectura

```
mafe-agentic/
  pyproject.toml             Dependencias y metadata
  Procfile                   Comando de arranque para Railway
  railway.json               Config de deploy
  nixpacks.toml              Build con Python + graphviz
  README.md                  Esto
  .gitignore
  mafe_agentic/
    __init__.py
    server.py                MCP server HTTP con middleware multi-user
    auth.py                  Service Account + Domain-Wide Delegation
    drive.py                 Cliente Google Drive con impersonation
    generators/
      pptx.py                Construye .pptx (luego se convierte a Slides)
      xlsx.py                Construye .xlsx (luego se convierte a Sheets)
      chart.py               Genera PNG con matplotlib
      diagram.py             Genera PNG con graphviz
```

## Costo

- **Railway**: plan Hobby USD 5/mes (incluye USD 5 de uso). Para un MCP server de este tamaño alcanza sin problema.
- **Google APIs**: gratis dentro de los límites generosos de Workspace.
- **Service Account**: gratis.

## Privacidad

- El service account JSON vive solo en las variables de entorno de Railway, encriptado.
- Cada request crea archivos solo en el Drive del usuario indicado en el header.
- No hay almacenamiento persistente — Mafe Agentic no guarda nada de lo que tú le pides.
- Logs en Railway no incluyen contenido de los archivos generados.

---

Hecho con cariño por Golden Gate Grid.
