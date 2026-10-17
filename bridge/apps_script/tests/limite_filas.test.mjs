/*
 * Pruebas del importador Apps Script (LIMITE_FILAS e inspección de encabezados)
 * sin tocar Google Sheets ni Neon. Se simulan los globals de Apps Script y se
 * ejecuta el archivo real dentro de un contexto `vm`.
 *
 * Uso:  node apps_script/tests/limite_filas.test.mjs
 * Salida: código 0 si todo pasa; 1 si hay fallos.
 */

import fs from "node:fs";
import path from "node:path";
import vm from "node:vm";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const RUTA_JS = path.join(__dirname, "..", "importarSnapshotNeonPuente.js");

const HOJAS = [
  "Empresas", "PESV", "Vehiculos", "Conductores", "Documentos",
  "EvaluacionesIA", "Actividades", "Inspecciones", "Mantenimientos",
  "Siniestros", "Paso5Diagnostico", "Paso5Contratistas",
  "Paso5Colaboradores", "Paso5Rutas", "Paso5Encuestas", "Historial",
  "Diagnosticos",
];

let total = 0;
let fallos = 0;

function check(condicion, mensaje) {
  total += 1;
  if (!condicion) {
    fallos += 1;
    console.error("FALLO: " + mensaje);
  }
}

function igual(real, esperado, mensaje) {
  check(
    JSON.stringify(real) === JSON.stringify(esperado),
    mensaje + " (esperado=" + JSON.stringify(esperado) + ", real=" + JSON.stringify(real) + ")"
  );
}

function normalizarClave(valor) {
  return String(valor || "")
    .trim()
    .toUpperCase()
    .normalize("NFD")
    .replace(/[\u0300-\u036f]/g, "")
    .replace(/[^A-Z0-9]+/g, "_")
    .replace(/^_+|_+$/g, "");
}

function hojaFalsa(encabezados, filas) {
  const grid = [encabezados, ...(filas || [])];
  const ultimaColumna = grid.reduce((max, fila) => Math.max(max, fila.length), 0);
  return {
    getLastRow() {
      return grid.length;
    },
    getLastColumn() {
      return ultimaColumna;
    },
    getRange(fila, col, numFilas, numCols) {
      return {
        getValues() {
          const salida = [];
          for (let r = fila; r < fila + numFilas; r++) {
            const datos = [];
            for (let c = col; c < col + numCols; c++) {
              const valor = grid[r - 1] && grid[r - 1][c - 1] !== undefined ? grid[r - 1][c - 1] : "";
              datos.push(valor);
            }
            salida.push(datos);
          }
          return salida;
        },
      };
    },
  };
}

function crearFetch() {
  const llamadas = [];
  return {
    llamadas,
    fetch(url, opciones) {
      const cuerpo = JSON.parse(opciones.payload);
      llamadas.push({ url, cuerpo });
      let respuesta = {};
      if (url.endsWith("/import/batches")) {
        respuesta = { status: "running", created: true };
      } else if (url.endsWith("/finalize")) {
        respuesta = {
          status: "validated",
          row_counts: cuerpo.expected_row_counts,
          checksum: "a".repeat(64),
        };
      } else if (url.endsWith("/import/rows")) {
        respuesta = { rows_written: (cuerpo.rows || []).length };
      }
      return {
        getResponseCode() {
          return 200;
        },
        getContentText() {
          return JSON.stringify(respuesta);
        },
      };
    },
  };
}

function cargarImporter(sheets, fetchMock) {
  const sandbox = {
    SS_ID: "SS-TEST",
    Session: {
      getActiveUser: () => ({ getEmail: () => "owner@test" }),
      getEffectiveUser: () => ({ getEmail: () => "owner@test" }),
    },
    PropertiesService: {
      getScriptProperties: () => ({
        getProperty: (clave) => {
          if (clave === "SMARTVIAL_BRIDGE_URL") return "https://bridge.test";
          if (clave === "SMARTVIAL_BRIDGE_TOKEN") return "token-de-prueba";
          return "";
        },
      }),
    },
    SpreadsheetApp: {},
    abrirSpreadsheetSmartVial_: () => ({ getSheetByName: (nombre) => sheets[nombre] || null }),
    obtenerMapaEncabezados_: (encabezados) => {
      const mapa = {};
      encabezados.forEach((valor, indice) => {
        const clave = normalizarClave(valor);
        if (clave) mapa[clave] = indice;
      });
      return mapa;
    },
    normalizarClave_: normalizarClave,
    UrlFetchApp: { fetch: fetchMock.fetch },
    Utilities: { getUuid: () => "11111111-1111-1111-1111-111111111111" },
    Logger: { log: () => {} },
  };
  vm.createContext(sandbox);
  vm.runInContext(fs.readFileSync(RUTA_JS, "utf8"), sandbox, {
    filename: "importarSnapshotNeonPuente.js",
  });
  return sandbox;
}

function libroBase(overrides) {
  const sheets = {};
  HOJAS.forEach((hoja) => {
    sheets[hoja] = hojaFalsa(["ID_REGISTRO"], []);
  });
  Object.assign(sheets, overrides || {});
  return sheets;
}

function filasEmpresa(n) {
  const filas = [];
  for (let i = 1; i <= n; i++) filas.push(["EMPTEST" + i, "Ficticia " + i]);
  return filas;
}

function ejecutar(sheets, limite) {
  const fetchMock = crearFetch();
  const sandbox = cargarImporter(sheets, fetchMock);
  check(typeof sandbox.importarSnapshotNeonPuente === "function", "importarSnapshotNeonPuente cargado");
  const opciones = limite === undefined ? undefined : { LIMITE_FILAS: limite };
  const resultado = sandbox.importarSnapshotNeonPuente(opciones);
  return { resultado, fetchMock, sandbox };
}

// 1) Sin límite -> importación completa.
{
  const sheets = libroBase({
    Empresas: hojaFalsa(["ID_EMPRESA", "NOMBRE_EMPRESA"], filasEmpresa(3)),
    Vehiculos: hojaFalsa(["ID_VEHICULO", "ID_EMPRESA"], [["V1", "EMP1"], ["V2", "EMP1"]]),
  });
  const { resultado } = ejecutar(sheets, undefined);
  igual(resultado.limiteFilas, null, "sin límite -> limiteFilas null");
  check(Object.keys(resultado.rowCounts).length === 17, "rowCounts tiene 17 hojas");
  igual(resultado.rowCounts.Empresas, 3, "sin límite Empresas=3");
  igual(resultado.rowCounts.Vehiculos, 2, "sin límite Vehiculos=2");
  igual(
    resultado.informePorHoja.Empresas,
    { encontradas: 3, seleccionadas: 3, enviadas: 3 },
    "informe Empresas sin límite"
  );
}

// 2) Límite 1.
{
  const sheets = libroBase({ Empresas: hojaFalsa(["ID_EMPRESA"], filasEmpresa(4)) });
  const { resultado } = ejecutar(sheets, 1);
  igual(resultado.rowCounts.Empresas, 1, "límite 1 -> 1 fila");
  igual(resultado.informePorHoja.Empresas, { encontradas: 4, seleccionadas: 1, enviadas: 1 }, "informe límite 1");
}

// 3) Límite 5 con hoja de 3 filas (menos que el límite).
{
  const sheets = libroBase({ Empresas: hojaFalsa(["ID_EMPRESA"], filasEmpresa(3)) });
  const { resultado } = ejecutar(sheets, 5);
  igual(resultado.rowCounts.Empresas, 3, "límite 5 con 3 filas -> 3");
  igual(resultado.informePorHoja.Empresas, { encontradas: 3, seleccionadas: 3, enviadas: 3 }, "informe límite>filas");
}

// 4) Límite 5 con hoja de 10 filas.
{
  const sheets = libroBase({ Empresas: hojaFalsa(["ID_EMPRESA"], filasEmpresa(10)) });
  const { resultado } = ejecutar(sheets, 5);
  igual(resultado.rowCounts.Empresas, 5, "límite 5 con 10 filas -> 5");
  igual(resultado.informePorHoja.Empresas, { encontradas: 10, seleccionadas: 5, enviadas: 5 }, "informe límite<filas");
}

// 5) Límite N (7) y string numérico ("7").
{
  const sheets = libroBase({ Empresas: hojaFalsa(["ID_EMPRESA"], filasEmpresa(9)) });
  igual(ejecutar(sheets, 7).resultado.rowCounts.Empresas, 7, "límite N=7");
  igual(ejecutar(sheets, "7").resultado.rowCounts.Empresas, 7, "límite '7' (string numérico)");
}

// 6) Valores inválidos: 0, negativos, decimales, texto, booleanos.
{
  [0, -1, -5, 1.5, "abc", true, false, NaN].forEach((valor) => {
    const sheets = libroBase({ Empresas: hojaFalsa(["ID_EMPRESA"], filasEmpresa(2)) });
    const fetchMock = crearFetch();
    const sandbox = cargarImporter(sheets, fetchMock);
    let lanzo = false;
    try {
      sandbox.importarSnapshotNeonPuente({ LIMITE_FILAS: valor });
    } catch (error) {
      lanzo = true;
    }
    check(lanzo, "LIMITE_FILAS inválido rechazado: " + String(valor));
    check(fetchMock.llamadas.length === 0, "sin llamadas de red con límite inválido: " + String(valor));
  });
}

// 7) Filas vacías dentro del rango seleccionado: se cuentan como seleccionadas,
//    no se envían (comportamiento actual que omite filas totalmente vacías).
{
  const sheets = libroBase({
    Empresas: hojaFalsa(["ID_EMPRESA"], [["EMP1"], [""], ["EMP2"]]),
  });
  const { resultado } = ejecutar(sheets, 3);
  igual(resultado.informePorHoja.Empresas, { encontradas: 3, seleccionadas: 3, enviadas: 2 }, "vacías: enviadas=2");
  igual(resultado.rowCounts.Empresas, 2, "vacías: conteo=2");
}

// 8) Hoja vacía (solo encabezado) y hoja sin columnas.
{
  const sheets = libroBase({
    Empresas: hojaFalsa(["ID_EMPRESA"], []),
    Vehiculos: hojaFalsa([], []),
  });
  const { resultado } = ejecutar(sheets, 5);
  igual(resultado.informePorHoja.Empresas, { encontradas: 0, seleccionadas: 0, enviadas: 0 }, "hoja vacía");
  igual(resultado.informePorHoja.Vehiculos, { encontradas: 0, seleccionadas: 0, enviadas: 0 }, "hoja sin columnas");
}

// 9) `Usuarios` nunca se importa ni aparece en el informe.
{
  const sheets = libroBase({
    Empresas: hojaFalsa(["ID_EMPRESA"], filasEmpresa(2)),
    Usuarios: hojaFalsa(["Correo", "Rol"], [["a@b.c", "ADMIN"]]),
  });
  const { resultado, fetchMock } = ejecutar(sheets, 2);
  check(!("Usuarios" in resultado.rowCounts), "rowCounts sin Usuarios");
  check(!("Usuarios" in resultado.informePorHoja), "informe sin Usuarios");
  const tocoUsuarios = fetchMock.llamadas.some((c) => c.cuerpo && c.cuerpo.sheet_name === "Usuarios");
  check(!tocoUsuarios, "ninguna petición de filas para Usuarios");
}

// 10) Inspección de encabezados: solo lectura, sin red, con estados claros.
{
  const sheets = libroBase({
    Empresas: hojaFalsa(["ID_EMPRESA", "NOMBRE_EMPRESA"], [["EMP1", "Uno"]]),
    Documentos: hojaFalsa(["", ""], []),
  });
  delete sheets.PESV; // hoja ausente
  const fetchMock = crearFetch();
  const sandbox = cargarImporter(sheets, fetchMock);
  const informe = sandbox.inspeccionarEncabezadosSnapshotPuente();
  check(fetchMock.llamadas.length === 0, "inspección no hace peticiones de red");
  check(Object.keys(informe).length === 17, "inspección cubre 17 hojas");
  igual(informe.Empresas.estado, "OK", "Empresas estado OK");
  igual(informe.Empresas.encabezados, ["ID_EMPRESA", "NOMBRE_EMPRESA"], "Empresas encabezados");
  igual(informe.PESV.estado, "FALTA_HOJA", "PESV FALTA_HOJA");
  igual(informe.Documentos.estado, "SIN_ENCABEZADOS", "Documentos SIN_ENCABEZADOS");
}

// 11) Compatibilidad con finalize: 17 hojas y conteos iguales.
{
  const sheets = libroBase({
    Empresas: hojaFalsa(["ID_EMPRESA"], filasEmpresa(4)),
    Vehiculos: hojaFalsa(["ID_VEHICULO"], [["V1"], ["V2"]]),
  });
  const { resultado, fetchMock } = ejecutar(sheets, 2);
  const finalize = fetchMock.llamadas.find((c) => c.url.endsWith("/finalize"));
  check(!!finalize, "se llamó a finalize");
  igual(
    Object.keys(finalize.cuerpo.expected_row_counts).sort(),
    [...HOJAS].sort(),
    "finalize incluye exactamente las 17 hojas"
  );
  igual(finalize.cuerpo.expected_row_counts, resultado.rowCounts, "expected_row_counts == rowCounts");
}

console.log("Pruebas ejecutadas: " + total + ", fallos: " + fallos);
process.exit(fallos === 0 ? 0 : 1);
