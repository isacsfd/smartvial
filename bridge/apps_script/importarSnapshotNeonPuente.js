/* =========================================================================
   PROPUESTA DE CAMBIO (no modifica el proyecto original)
   -------------------------------------------------------------------------
   Copia del flujo de importación que reemplaza la conexión JDBC directa por
   llamadas HTTPS al puente FastAPI. Para usarla:

     1. Copiar este archivo al proyecto Apps Script original.
     2. Guardar en Script Properties:
          SMARTVIAL_BRIDGE_URL   = https://<servicio>.onrender.com
          SMARTVIAL_BRIDGE_TOKEN = <token del puente>
     3. Ejecutar `importarSnapshotNeonPuente()` desde el editor.
        Para una importación limitada (prueba controlada):
          importarSnapshotNeonPuente({LIMITE_FILAS: 5})   // máx. 5 filas de datos por hoja
        Sin `LIMITE_FILAS` (o con null/""), importa la hoja completa.

   No usa JDBC en ningún punto. Excluye siempre `Usuarios`, valida conteos y
   recibe el checksum calculado por el servidor. Depende de utilidades ya
   presentes en el proyecto: `abrirSpreadsheetSmartVial_`, `SS_ID`,
   `obtenerMapaEncabezados_` y `normalizarClave_`.
   ========================================================================= */

const SMARTVIAL_BRIDGE_HOJAS = [
  "Empresas", "PESV", "Vehiculos", "Conductores", "Documentos",
  "EvaluacionesIA", "Actividades", "Inspecciones", "Mantenimientos",
  "Siniestros", "Paso5Diagnostico", "Paso5Contratistas",
  "Paso5Colaboradores", "Paso5Rutas", "Paso5Encuestas", "Historial",
  "Diagnosticos"
];

const SMARTVIAL_BRIDGE_COLUMNA_REGISTRO = {
  Empresas: "ID_EMPRESA",
  PESV: "ID_REGISTRO",
  Vehiculos: "ID_VEHICULO",
  Conductores: "ID_CONDUCTOR",
  Documentos: "ID_DOCUMENTO",
  EvaluacionesIA: "ID_EVALUACION",
  Actividades: "ID_ACTIVIDAD",
  Inspecciones: "ID_INSPECCION",
  Mantenimientos: "ID_MANTENIMIENTO",
  Siniestros: "ID_SINIESTRO",
  Paso5Diagnostico: "ID_DIAGNOSTICO_P5",
  Paso5Contratistas: "ID_CONTRATISTA_P5",
  Paso5Colaboradores: "ID_COLABORADOR_P5",
  Paso5Rutas: "ID_RUTA_P5",
  Paso5Encuestas: "ID_ENCUESTA_P5",
  Historial: "ID_EVENTO",
  Diagnosticos: "ID_DIAGNOSTICO"
};

const SMARTVIAL_BRIDGE_BLOQUE_FILAS = 100;

/*
 * Lee los encabezados REALES (fila 1) de cada hoja permitida sin leer filas de
 * datos, sin enviar nada a Neon y sin crear lotes ni modificar registros.
 * Excluye `Usuarios`. Informa por hoja si existe, si tiene encabezados y la
 * lista normalizada de encabezados.
 *
 * Devuelve: { <hoja>: { existe, estado, encabezados } }
 *   estado: "OK" | "FALTA_HOJA" | "SIN_ENCABEZADOS"
 */
function inspeccionarEncabezadosSnapshotPuente() {
  const ss = abrirSpreadsheetSmartVial_();
  const informe = {};
  hojasPuentePermitidas_().forEach(function(nombreHoja) {
    const hoja = ss.getSheetByName(nombreHoja);
    if (!hoja) {
      informe[nombreHoja] = {existe: false, estado: "FALTA_HOJA", encabezados: []};
      return;
    }
    const ultimaColumna = hoja.getLastColumn();
    if (ultimaColumna < 1) {
      informe[nombreHoja] = {existe: true, estado: "SIN_ENCABEZADOS", encabezados: []};
      return;
    }
    const encabezados = normalizarEncabezadosPuente_(
      hoja.getRange(1, 1, 1, ultimaColumna).getValues()[0]
    );
    const todosVacios = encabezados.every(function(nombre) {
      return /^COLUMNA_\d+$/.test(nombre);
    });
    informe[nombreHoja] = {
      existe: true,
      estado: todosVacios ? "SIN_ENCABEZADOS" : "OK",
      encabezados: encabezados
    };
  });
  return informe;
}

/*
 * Lista de hojas del snapshot con exclusión explícita de `Usuarios` (por si la
 * constante se editara por error). Nunca se importa `Usuarios`.
 */
function hojasPuentePermitidas_() {
  const excluida = normalizarClave_("Usuarios");
  return SMARTVIAL_BRIDGE_HOJAS.filter(function(nombreHoja) {
    return normalizarClave_(nombreHoja) !== excluida;
  });
}

/*
 * Valida el parámetro opcional LIMITE_FILAS.
 * - undefined/null/"": sin límite (importación completa) -> null.
 * - Entero positivo: devuelve el número.
 * - Cualquier otro valor (0, negativos, decimales, texto, booleanos): error.
 */
function validarLimiteFilas_(valor) {
  if (valor === undefined || valor === null) return null;
  if (typeof valor === "string" && valor.trim() === "") return null;
  if (typeof valor === "boolean") {
    throw new Error("LIMITE_FILAS debe ser un entero positivo.");
  }
  const numero = Number(valor);
  if (!Number.isInteger(numero) || numero <= 0) {
    throw new Error("LIMITE_FILAS debe ser un entero positivo.");
  }
  return numero;
}

function importarSnapshotNeonPuente(opciones) {
  opciones = opciones || {};

  const usuarioActivo = String(Session.getActiveUser().getEmail() || "").toLowerCase();
  const usuarioEfectivo = String(Session.getEffectiveUser().getEmail() || "").toLowerCase();
  if (!usuarioActivo || usuarioActivo !== usuarioEfectivo) {
    throw new Error("La importación solo se puede iniciar desde el editor con la cuenta propietaria del proyecto.");
  }

  const props = PropertiesService.getScriptProperties();
  const url = String(props.getProperty("SMARTVIAL_BRIDGE_URL") || "").trim().replace(/\/+$/, "");
  const token = String(props.getProperty("SMARTVIAL_BRIDGE_TOKEN") || "");
  if (!url || !token) {
    throw new Error("Faltan SMARTVIAL_BRIDGE_URL o SMARTVIAL_BRIDGE_TOKEN en Script Properties.");
  }
  if (!/^https:\/\//i.test(url)) {
    throw new Error("SMARTVIAL_BRIDGE_URL debe usar HTTPS (https://). No se admite HTTP.");
  }

  const ss = abrirSpreadsheetSmartVial_();
  let lote = String(opciones.batchId || "").trim() || Utilities.getUuid();
  const limiteFilas = validarLimiteFilas_(opciones.LIMITE_FILAS);

  // 1) Crear o reanudar el lote (idempotente).
  const loteInfo = llamarPuenteSmartVial_(url, token, "POST", "/import/batches", {
    batch_id: lote,
    spreadsheet_id: SS_ID,
    notes: JSON.stringify({source: "importarSnapshotNeonPuente", usersSheetImported: false})
  });
  if (loteInfo.status !== "running") {
    throw new Error("El lote no quedó en estado 'running': " + String(loteInfo.status));
  }

  // 2) Enviar filas por hoja en bloques de 100. Todas las hojas permitidas
  //    quedan registradas en conteosEsperados, incluso las de cero filas.
  //    Con LIMITE_FILAS se procesan como máximo esas filas de datos por hoja
  //    (la fila de encabezados no cuenta).
  const conteosEsperados = {};
  const informe = {};
  hojasPuentePermitidas_().forEach(function(nombreHoja) {
    const hoja = ss.getSheetByName(nombreHoja);
    if (!hoja || hoja.getLastRow() < 2 || hoja.getLastColumn() < 1) {
      conteosEsperados[nombreHoja] = 0;
      informe[nombreHoja] = {encontradas: 0, seleccionadas: 0, enviadas: 0};
      Logger.log("[informe] " + nombreHoja + ": encontradas=0 seleccionadas=0 enviadas=0");
      return;
    }

    const ultimaFila = hoja.getLastRow();
    const ultimaColumna = hoja.getLastColumn();
    const encabezados = normalizarEncabezadosPuente_(hoja.getRange(1, 1, 1, ultimaColumna).getValues()[0]);
    const mapa = obtenerMapaEncabezados_(encabezados);
    const indiceEmpresa = mapa[normalizarClave_("ID_EMPRESA")];
    const columnaRegistro = SMARTVIAL_BRIDGE_COLUMNA_REGISTRO[nombreHoja] || "ID_REGISTRO";
    const indiceRegistro = mapa[normalizarClave_(columnaRegistro)];

    const encontradas = ultimaFila - 1;
    const seleccionadas = (limiteFilas === null) ? encontradas : Math.min(limiteFilas, encontradas);
    const ultimaFilaSeleccionada = 1 + seleccionadas;

    let enviadas = 0;
    for (let inicio = 2; inicio <= ultimaFilaSeleccionada; inicio += SMARTVIAL_BRIDGE_BLOQUE_FILAS) {
      const cantidadBloque = Math.min(SMARTVIAL_BRIDGE_BLOQUE_FILAS, ultimaFilaSeleccionada - inicio + 1);
      const valores = hoja.getRange(inicio, 1, cantidadBloque, ultimaColumna).getValues();
      const filas = [];
      valores.forEach(function(fila, posicion) {
        if (fila.every(function(valor) { return valor === "" || valor === null; })) return;
        const objeto = {};
        encabezados.forEach(function(encabezado, indice) {
          const valor = fila[indice];
          objeto[encabezado] = valor instanceof Date ? valor.toISOString() : valor;
        });
        const idRegistro = indiceRegistro === undefined ? "" : String(fila[indiceRegistro] || "").trim();
        const idEmpresa = indiceEmpresa === undefined ? "" : String(fila[indiceEmpresa] || "").trim().toUpperCase();
        filas.push({
          source_row_number: inicio + posicion,
          record_id: idRegistro || null,
          company_id: idEmpresa || null,
          payload: objeto
        });
        enviadas++;
      });
      enviarFilasPuente_(url, token, lote, nombreHoja, filas);
    }
    conteosEsperados[nombreHoja] = enviadas;
    informe[nombreHoja] = {encontradas: encontradas, seleccionadas: seleccionadas, enviadas: enviadas};
    Logger.log("[informe] " + nombreHoja + ": encontradas=" + encontradas +
      " seleccionadas=" + seleccionadas + " enviadas=" + enviadas);
  });

  // 3) Finalizar: el servidor exige las 17 hojas, recalcula conteos y checksum.
  const resultado = llamarPuenteSmartVial_(url, token, "POST",
    "/import/batches/" + lote + "/finalize",
    {expected_row_counts: conteosEsperados});

  const conteosServidor = resultado.row_counts || {};
  Object.keys(conteosEsperados).forEach(function(nombreHoja) {
    if (Number(conteosEsperados[nombreHoja] || 0) !== Number(conteosServidor[nombreHoja] || 0)) {
      throw new Error("Discrepancia de conteos en " + nombreHoja + ".");
    }
  });

  return {
    status: resultado.status,
    batchId: lote,
    limiteFilas: limiteFilas,
    rowCounts: conteosEsperados,
    informePorHoja: informe,
    checksum: resultado.checksum,
    usersSheetImported: false
  };
}

/*
 * Envía un bloque de filas. Si el puente responde 413 (cuerpo o número de
 * filas demasiado grande), divide el bloque a la mitad y reintenta; nunca
 * omite filas. Si una sola fila sigue recibiendo 413, propaga el error.
 */
function enviarFilasPuente_(url, token, lote, nombreHoja, filas) {
  if (!filas.length) return;
  try {
    llamarPuenteSmartVial_(url, token, "POST", "/import/rows", {
      batch_id: lote,
      sheet_name: nombreHoja,
      rows: filas
    });
  } catch (error) {
    if (error && error.codigoHttp === 413 && filas.length > 1) {
      const mitad = Math.floor(filas.length / 2);
      enviarFilasPuente_(url, token, lote, nombreHoja, filas.slice(0, mitad));
      enviarFilasPuente_(url, token, lote, nombreHoja, filas.slice(mitad));
      return;
    }
    throw error;
  }
}

function normalizarEncabezadosPuente_(encabezadosRaw) {
  const usados = {};
  return encabezadosRaw.map(function(valor, indice) {
    let nombre = String(valor == null ? "" : valor).trim();
    if (!nombre) nombre = "COLUMNA_" + (indice + 1);
    const base = nombre;
    let sufijo = 2;
    while (usados[nombre]) nombre = base + "_" + (sufijo++);
    usados[nombre] = true;
    return nombre;
  });
}

function llamarPuenteSmartVial_(url, token, metodo, ruta, cuerpo) {
  const respuesta = UrlFetchApp.fetch(url + ruta, {
    method: metodo,
    contentType: "application/json",
    headers: {"X-Bridge-Token": token},
    payload: JSON.stringify(cuerpo),
    muteHttpExceptions: true
  });

  const codigo = respuesta.getResponseCode();
  const texto = respuesta.getContentText() || "{}";
  let json = null;
  try { json = JSON.parse(texto); } catch (e) { json = null; }

  if (codigo < 200 || codigo >= 300) {
    const detalle = json && json.detail ? String(json.detail) : "sin detalle";
    const error = new Error("El puente respondió " + codigo + ": " + detalle);
    error.codigoHttp = codigo;
    throw error;
  }
  return json || {};
}
