# SmartVial Neon Bridge

Puente HTTPS (FastAPI) que recibe el snapshot de operación desde Google Apps
Script y lo escribe en Neon PostgreSQL. Reemplaza la conexión JDBC directa
desde Apps Script, que fallaba por incompatibilidades SSL.

## Contrato (JSON)

Todas las rutas de importación exigen la cabecera `X-Bridge-Token`.

| Método | Ruta | Cuerpo | Descripción |
| --- | --- | --- | --- |
| `POST` | `/import/batches` | `{batch_id, spreadsheet_id, notes?}` | Crea o reanuda un lote (`running`). Idempotente por `id`. |
| `POST` | `/import/rows` | `{batch_id, sheet_name, rows:[{source_row_number, record_id?, company_id?, payload}]}` | Inserta/actualiza filas con UPSERT idempotente. |
| `POST` | `/import/batches/{batch_id}/finalize` | `{expected_row_counts, client_checksum?}` | Valida conteos, calcula checksum y sella en `validated`. |
| `GET` | `/import/batches/{batch_id}` | — | Consulta estado del lote. |
| `GET` | `/neon-check` | — | Verifica conexión PostgreSQL. |
| `GET` | `/health` | — | Sonda de salud (sin token); incluye `database.{checked,connected,tls_enabled}`. |

`.payload` es un objeto JSON cuyas claves son los encabezados de la hoja y cuyos
valores son escalares (`string`, `number`, `boolean`, `null`). Las fechas se
envían como texto ISO 8601. Los contenedores anidados se rechazan.

### Semántica de idempotencia y reanudación

- `migration_batches.id` es la clave del lote; reintentar la creación devuelve
  `created: false` sin duplicar.
- `legacy_sheet_rows` respeta `UNIQUE (batch_id, sheet_name, source_row_number)`
  y usa `ON CONFLICT ... DO UPDATE`, por lo que reenviar filas no las duplica.
- Solo se pueden reanudar lotes en estado `running` o `failed`. Un lote
  `validated`/`committed` devuelve `409` y no admite más escrituras.

### Integridad de la finalización

`expected_row_counts` debe incluir **las 17 hojas permitidas**, incluidas las de
cero filas. Si falta una hoja o aparece una no permitida, la finalización
devuelve `409` y el lote no se sella. Si los conteos reales no coinciden con los
esperados, también devuelve `409`. Esto evita que una hoja olvidada termine
"validada" como si estuviera vacía.

## Límites y pérdida de datos

Ninguna ruta trunca valores ni omite filas: si algo excede un límite, responde
un error claro (`413`/`422`) y no persiste nada parcial de esa petición.

| Límite | Valor por defecto | Variable | Comentario |
| --- | --- | --- | --- |
| Longitud de un valor | 50 000 | `MAX_VALUE_LENGTH` | Tope de una celda de Google Sheets. |
| Filas por petición | 500 | `MAX_ROWS_PER_REQUEST` | El importador envía bloques de 100. |
| Columnas por fila | 200 | `MAX_PAYLOAD_KEYS` | La hoja más ancha documentada es `Diagnosticos` (52). |
| Cuerpo por petición | 2 MiB | `MAX_BODY_BYTES` | Middleware ASGI cuenta bytes reales (incluye `Transfer-Encoding: chunked`). |
| Número de fila | 1 000 000 | `MAX_ROW_NUMBER` | Cota superior de `source_row_number`. |

Si un bloque de 100 filas excediera el tamaño de cuerpo, el importador divide el
bloque a la mitad y reintenta (`enviarFilasPuente_`); nunca descarta filas. Si
una sola fila excede el límite, se propaga el error sin escribirla.

El límite de cuerpo lo aplica `LimiteCuerpoMiddleware` (`src/body_limit.py`) a
nivel ASGI: rechaza temprano si `Content-Length` ya supera el límite y, si no,
cuenta los bytes de cada fragmento `receive`, cortando con `413` en cuanto se
supera. No depende de `Content-Length`, por lo que cubre transferencia
fragmentada. El cuerpo admitido se reproduce intacto a FastAPI.

## Verificación TLS (`sslmode=verify-full`)

`psycopg` usa `libpq`, que soporta `sslmode=verify-full`. Para validar el
certificado del servidor se necesita una CA: el puente usa por defecto el bundle
de **certifi** (`certifi.where()`), incluido en el wheel, de modo que
`verify-full` funciona de forma portable en Render sin depender del CA por
defecto del `libpq` empaquetado (que puede no existir en el runtime). Neon usa
certificados de Let's Encrypt, presentes en el bundle de certifi.

- Por defecto el puente usa `PG_SSLMODE=verify-full` y `PG_SSLROOTCERT=certifi`.
- Un `sslmode` inseguro o desconocido (`disable`/`allow`/`prefer`) se eleva a
  `verify-full`; nunca se degrada ni se oculta un fallo de certificado.
- `PG_SSLROOTCERT` solo se respeta si apunta a un archivo PEM existente; en
  cualquier otro caso (`system`, vacío o ruta inexistente) se usa el bundle de
  certifi, para que `verify-full` no falle por el CA del `libpq` empaquetado.
- `/neon-check` determina el TLS real con `pgconn.ssl_in_use` (no con
  `pg_stat_ssl`), robusto con el endpoint *pooled* de Neon.
- En producción, al arrancar se ejecuta una comprobación de Neon (con
  reintentos) y su resultado (solo booleanos, sin secretos) se publica en
  `/health` como `database.{checked,connected,tls_enabled}`. Así la conexión
  real se puede verificar sin token; `/neon-check` sigue requiriendo token.

## Hoja `Documentos` (encabezados)

Las constantes del proyecto **no son autoritativas** y difieren:

- `Gestion.js` (`SMARTVIAL_DOCUMENTOS_HEADERS`): 23 encabezados.
- `Código.js` (`SMARTVIAL_ESTRUCTURAS.Documentos`): 26 encabezados.
- Coinciden en 20; `Gestion.js` añade `DOC_SOURCE_FILE_ID`, `PDF_FILE_ID`,
  `PDF_URL`; `Código.js` añade 6 columnas de revisión normativa.

El puente es **agnóstico a los encabezados**: guarda como JSONB las claves que
reciba, sean las que sean. La estructura real se obtiene de la fila 1 de la hoja
viva con `inspeccionarEncabezadosSnapshotPuente()` (lee solo la fila 1, sin
datos). Usa esa salida como evidencia antes de importar.

## Seguridad

- Autenticación por token con `hmac.compare_digest` (tiempo constante).
- TLS verificado en PostgreSQL: `psycopg` con `sslmode=verify-full` y
  `sslrootcert=system` por defecto. Cualquier valor inseguro se eleva a
  `verify-full`; nunca se desactiva el cifrado ni la validación del certificado.
- SQL siempre parametrizado; nunca se interpola SQL con datos del cliente.
- Se rechaza explícitamente la hoja `Usuarios` y cualquier hoja fuera de la
  lista blanca. Nunca se modifica `active_migration`.
- Sin operaciones destructivas (`DROP`/`DELETE`/`TRUNCATE`).
- Los errores internos no se devuelven al cliente; se registran en el servidor.
- En `ENVIRONMENT=production`, `/docs`, `/redoc` y `/openapi.json` quedan
  deshabilitados.

## Configuración en Render

Valores en `render.yaml`; los secretos se definen en el panel del servicio
(`sync: false`), nunca en el repositorio:

| Variable | Origen | Notas |
| --- | --- | --- |
| `NEON_DATABASE_URL` | Panel de Render | Endpoint pooled de Neon. |
| `BRIDGE_TOKEN` | Panel de Render | Token largo y aleatorio compartido con Apps Script. |
| `ENVIRONMENT` | `render.yaml` | `production` para ocultar OpenAPI. |
| `PG_SSLMODE` | `render.yaml` | `verify-full`. |
| `PG_SSLROOTCERT` | `render.yaml` | `certifi` (CA del wheel). |

Render despliega el subdirectorio `bridge` (`rootDir`) y arranca con
`uvicorn src.main:app`. El secreto nunca está en el repositorio: `NEON_DATABASE_URL`
y `BRIDGE_TOKEN` usan `sync: false`.

### Configurar los secretos (solo panel de Render)

1. Render → servicio `smartvial-neon-bridge` → **Environment**.
2. `NEON_DATABASE_URL`: pegar la cadena *pooled* de Neon (botón **Connect**).
3. `BRIDGE_TOKEN`: pegar el token generado abajo (mismo valor que en Apps Script).
4. Guardar y redeploy. No pegues estos valores en archivos, terminal ni chat.

## Generar el token del puente (sin mostrarlo)

Genera 32 bytes aleatorios y escríbelos en un archivo temporal, sin imprimirlos:

```bash
# Linux/macOS
python -c "import secrets,base64,tempfile,os;open(os.path.join(tempfile.gettempdir(),'bridge_token.txt'),'w').write(base64.urlsafe_b64encode(secrets.token_bytes(32)).decode())"
```

```powershell
# Windows PowerShell
python -c "import secrets,base64,tempfile,os;open(os.path.join(tempfile.gettempdir(),'bridge_token.txt'),'w').write(base64.urlsafe_b64encode(secrets.token_bytes(32)).decode())"
```

Abre el archivo temporal en un editor para copiar el valor y bórralo después.
Evita `cat`/`Get-Content`/`echo` para que no quede en el historial de la terminal.
Usa el **mismo** token en Render (`BRIDGE_TOKEN`) y en Apps Script
(`SMARTVIAL_BRIDGE_TOKEN`).

## Procedimiento de prueba controlada (orden)

1. `GET /health` (sin token) → `{"status":"ok"}`.
2. `GET /neon-check` con cabecera `X-Bridge-Token` → `connected:true` y
   `tls_enabled:true` (certificado validado).
3. Apps Script: definir `SMARTVIAL_BRIDGE_URL` (https) y `SMARTVIAL_BRIDGE_TOKEN`
   en Script Properties.
4. Apps Script: ejecutar `inspeccionarEncabezadosSnapshotPuente()` y revisar los
   encabezados reales (solo fila 1, sin datos).
5. Apps Script: ejecutar `importarSnapshotNeonPuente()` con un lote de staging y
   verificar `row_counts`/`checksum`.
6. Confirmar que `active_migration` no cambió y que la lectura desde Neon sigue
   desactivada.

## Configuración en Apps Script

1. Copiar `apps_script/importarSnapshotNeonPuente.js` al proyecto Apps Script.
2. Script Properties:
   - `SMARTVIAL_BRIDGE_URL` = `https://<servicio>.onrender.com`
   - `SMARTVIAL_BRIDGE_TOKEN` = mismo `BRIDGE_TOKEN` del puente.
3. Ejecutar `inspeccionarEncabezadosSnapshotPuente()` para revisar encabezados.
4. Ejecutar `importarSnapshotNeonPuente()` desde el editor (cuenta propietaria).

La propuesta no usa JDBC, mantiene la exclusión de `Usuarios`, envía las 17
hojas (con ceros), reintenta dividiendo ante `413` y valida los conteos contra
el checksum calculado por el servidor.

## Pruebas

```bash
cd bridge
pip install -r requirements-dev.txt
python -m pytest
```

Las pruebas usan un repositorio en memoria; no requieren credenciales reales ni
escriben en Neon. Cubren autenticación, hoja no permitida, límites (filas,
cuerpo incluido fragmentado, rango de fila, longitud de valor, claves,
identificadores), validación de payload, contrato y tipos, integridad de conteos
(17 hojas), idempotencia, protección de lotes validados y configuración TLS
(`verify-full`).

## Limitaciones conocidas

- `verify-full` requiere un almacén de CA disponible en el runtime. En Render
  (Linux) `sslrootcert=system` debería funcionar; si no, la conexión falla
  (fail-closed) y hay que aportar `PG_SSLROOTCERT=/ruta/ca.crt`.
- El esquema no se crea desde el puente: se asume que `backend/schema.sql` ya
  fue aplicado en Neon.
- Los límites por defecto (valores de 50 000, 500 filas, 2 MiB) deben validarse
  contra el volumen real de las hojas antes de una importación completa.

## Qué queda por validar (sin credenciales configuradas)

No hay `NEON_DATABASE_URL` ni `BRIDGE_TOKEN` en este entorno, así que **no se
ejecutó ninguna prueba real contra Neon**. Falta confirmar en el entorno real:

1. Que `verify-full` + `sslrootcert=system` conecta y `/neon-check` devuelve
   `tls_enabled: true` (validación de certificado efectiva).
2. Que existe el esquema (`migration_batches`, `legacy_sheet_rows`, etc.) y que
   las 17 hojas se importan con conteos/checksum correctos en un lote de staging.
3. Los encabezados reales de `Documentos` y demás hojas vía
   `inspeccionarEncabezadosSnapshotPuente()`.

## Pasos pendientes (fuera de esta tarea)

1. Revisar y aprobar la propuesta de Apps Script antes de copiarla al proyecto original.
2. Definir `NEON_DATABASE_URL` y `BRIDGE_TOKEN` en Render y desplegar el servicio.
3. Ejecutar una prueba controlada con un lote de staging y verificar
   `row_counts`/`checksum`, sin activar `active_migration` ni lecturas desde Neon.
