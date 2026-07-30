# Portal Forus — Comex + Contabilidad + Recursos Humanos

Convierte `app-comex-cloud` en un portal de tres módulos. El Comex actual **no
cambia de comportamiento**: se mueve tal cual a su propio archivo.

Commit base: **`2a25d7d` — "Update app_comex_cloud.py"**.

---

## 1. Qué subir, en qué orden

El orden importa. Streamlit Cloud redespliega en cada commit, así que
`app_comex_cloud.py` va **al final**: hasta ese momento la app sigue
funcionando exactamente como hoy, porque nadie importa todavía los archivos
nuevos.

| # | Ruta en el repo | Acción |
|---|---|---|
| 1 | `forus_ui.py` | **Nuevo** |
| 2 | `forus_parsing.py` | **Nuevo** |
| 3 | `forus_auth.py` | **Nuevo** |
| 4 | `modules/__init__.py` | **Nuevo** (crea la carpeta `modules/`) |
| 5 | `modules/comex.py` | **Nuevo** |
| 6 | `modules/contabilidad.py` | **Nuevo** |
| 7 | `modules/alquileres.py` | **Nuevo** |
| 8 | `modules/rrhh.py` | **Nuevo** |
| 9 | `app_comex_cloud.py` | **Reemplaza** el que existe |

Para crear la carpeta desde la web de GitHub: *Add file → Create new file* y
escribe `modules/__init__.py` en el nombre; la carpeta se crea sola.

**No hay que tocar** `requirements.txt`, `.devcontainer/devcontainer.json` ni
los dos PNG del logo. El nombre del entrypoint sigue siendo
`app_comex_cloud.py` a propósito, para no reconfigurar Streamlit Cloud ni el
devcontainer.

---

## 2. Configurar los permisos en Secrets

En Streamlit Cloud → *Settings → Secrets*, añade la sección `[modulos]` debajo
de la que ya tienes:

```toml
[auth]
"liliana.vitate@forus.pe" = "la-que-ya-tienes"
"danitza.cupe@forus.pe"   = "la-que-ya-tienes"
"hugo.camara@forus.pe"    = "la-que-ya-tienes"
"romulo.rasilla@forus.pe" = "la-que-ya-tienes"
"bi@forus.pe"             = "la-que-ya-tienes"

[modulos]
"hugo.camara@forus.pe"    = "todos"
"bi@forus.pe"             = "todos"
"liliana.vitate@forus.pe" = "comex"
"danitza.cupe@forus.pe"   = "comex,contabilidad"
"romulo.rasilla@forus.pe" = "comex"

# Opcional. Si lo pones, Contabilidad distingue mejor al proveedor del cliente:
# el RUC que aparezca aquí se toma como cliente y el otro como emisor.
[empresa]
ruc = "20514811271"
```

Claves válidas: `comex`, `contabilidad`, `rrhh`, o `todos`.

**Sin la sección `[modulos]` solo se ve Comex.** Es el valor por defecto, para
que nadie pierda el acceso que ya tenía al subir esta versión. Los módulos
nuevos permanecen invisibles hasta que asignes permisos: **este paso hay que
hacerlo o Contabilidad y Recursos Humanos no aparecerán.**

### Cómo se navega

En el panel lateral, el desplegable **Sitio destino** lleva a cualquiera de las
cuatro pantallas en un solo paso:

- Comex
- Contabilidad
- Recursos Humanos - Boletas de pago
- Recursos Humanos - Arriendos

Solo aparecen las que permita el usuario, y quien tenga una sola no ve
desplegable. El antiguo "Sitio destino" de Comex —un desplegable con la única
opción "Comex Forus", que no hacía nada— se eliminó: ese hueco lo ocupa ahora
este selector.

**En el ZIP no va ningún `secrets.toml`.** Las contraseñas se escriben
únicamente en el panel de Streamlit Cloud.

---

## 3. Los tres módulos

### Comex — sin cambios

Idéntico a hoy: sufijos `_CLB.pdf`, `_VNS.pdf`, `_PRF.pdf` y el mismo Excel de
salida. Solo cambió de archivo.

### Contabilidad — comprobantes de proveedor

Facturas, boletas de venta, notas de crédito y débito y recibos por honorarios.
No hay que renombrar archivos. Excel con las hojas **Documentos** (32 columnas
de cabecera), **Detalle** (líneas del comprobante), **Resumen** y **Auditoría**.
Valida que `gravadas + IGV` cuadre con el importe total.

### Recursos Humanos — dos pantallas

Ambas se eligen directamente en *Sitio destino*:

**Boletas de pago** → `salida_rrhh_boletas.xlsx`
Hojas **Boletas** (45 columnas: trabajador, días, 9 ingresos, 10 descuentos,
5 aportes del empleador, totales), **Conceptos** (todas las líneas leídas, con
el bloque en que aparecían), **Resumen** y **Auditoría**. Cada concepto se
clasifica según el bloque de la boleta donde está, no solo por su nombre, así
que "comisión" en ingresos y "comisión sobre flujo" en descuentos no se
confunden. Valida `ingresos − descuentos = neto`.

**Facturas de alquiler** → `control_alquileres.xlsx`
Es el control que pediste. La hoja **Control** trae **solo tus nueve columnas,
en tu orden**, una fila por concepto, para copiar y pegar sin borrar nada:

| Fecha | Tienda | MES | Concepto | SOLES | DOLARES | # Factura | RAZON SOCIAL | FECHA DE ENTREGA |

Las demás hojas: **Control ampliado** (las mismas filas más contrato, local,
periodo, importe con IGV, referencia, RUC, archivo, página y observaciones),
**Facturas** (una por comprobante, con el cuadre y los duplicados),
**Resumen** y **Auditoría**.

Cómo sale cada columna:

- **Fecha** — fecha de emisión del comprobante.
- **Tienda** — el local cuando la factura lo trae (`S146 HUSH PUPPIES`,
  `L-149/150/151 DHOUSE`, `HUSH PUPPIES PAQP`) y, cuando no, el número de
  contrato (`9244`) o la referencia del local (`LCS 1070`). En la hoja *Control
  ampliado* van contrato y local en columnas separadas.
- **MES** — mes del periodo facturado, sacado del propio concepto
  (`01.06.2026 - 30.06.2026` → JUNIO). Si el concepto no lo trae, se busca
  escrito en el detalle (`JUNIO 2026`) y, en último caso, se usa el mes de
  emisión. **De dónde salió queda anotado en la hoja Control ampliado.**
- **Concepto** — la descripción limpia, sin código, cantidad, unidad ni fechas.
- **SOLES / DOLARES** — el importe **sin IGV**, en la columna de su moneda.
- **# Factura** — serie y correlativo normalizados a `F004-00332954`.
- **RAZON SOCIAL** — el proveedor, no Forus.
- **FECHA DE ENTREGA** — **va vacía**: ese dato no está en el PDF, es de
  ustedes. La columna queda lista para llenarla a mano.

**Cómo acierta el importe sin IGV.** Cada emisor pone esa columna en un sitio
distinto: en Jockey es la última, en Mall Plaza la cuarta por el final (las dos
últimas ya llevan IGV). En vez de adivinar, se prueban todas las columnas y se
elige aquella cuya suma cuadra con las operaciones gravadas más inafectas del
documento. Si ninguna cuadra, se avisa en Observaciones en vez de dar un número
equivocado por bueno.

También marca **facturas repetidas** (mismo comprobante en dos archivos, como
los dos `F002-1894` que enviaste) y descarta los **anexos** —detalle de
facturación, estado de cuenta— para que sus importes no entren al control,
aunque sí los usa para completar el nombre del local.

---

## 4. Diff exacto contra `2a25d7d`

El original era un archivo de **2.213 líneas y 36 funciones**. Ahora son
**9 archivos, 5.381 líneas y 127 funciones**.

### Funciones heredadas del Comex: 36 de 36 conservadas

| Estado | Cantidad | Detalle |
|---|---|---|
| Idénticas **byte a byte** | **34** | Verificado comparando el árbol AST contra el commit base |
| Modificadas a propósito | **2** | `render_login_screen`, `is_authenticated` |
| Eliminadas | **0** | — |

Las dos modificadas:

- **`render_login_screen`** — el título pasa de "Lectura PDF Forus - Comex" a
  "Portal Forus"; al validar la contraseña ahora también carga los módulos del
  usuario y rechaza a quien no tenga ninguno. El CSS del login no se tocó.
- **`is_authenticated`** — sin cambios en su cuerpo; se le añadió al lado
  `current_user_modules()`.

`split_lines` se movió de Comex a `forus_parsing.py` (mismo cuerpo, ahora
compartida). Del panel lateral de Comex se quitaron dos líneas: el desplegable
muerto "Sitio destino", cuyo sitio ocupa el selector del portal. Todo el motor
de lectura de Comex — `process_columbia_pdf`,
`process_vans_pdf`, `process_parfois_pdf`, `extract_items_from_invoice_text`,
`parse_money`, `parse_quantity` y los demás — quedó intacto.

### Dónde quedó cada parte del archivo viejo

| Líneas del original | Destino |
|---|---|
| 13, 32–43 (logo, helpers de UI) | `forus_ui.py` |
| 15–19 (`SUFIJOS_MARCA`) | `modules/comex.py` |
| 21–27, 56–164 (acceso) | `forus_auth.py` |
| 46–53, 220–261, 283–287 (parseo) | `forus_parsing.py` |
| 166–996 (motor Comex) | `modules/comex.py` |
| 999–1003 (`set_page_config`) | `app_comex_cloud.py` |
| 1005–1979 (CSS, 975 líneas) | `forus_ui.py` como `GLOBAL_CSS` |
| 1983–2009 (logo y sesión) | `forus_ui.render_sidebar_header()` |
| 2011–2035 (sidebar de marcas) | `modules/comex.render_sidebar()` |
| 2037–2214 (pantalla Comex) | `modules/comex.render()` |

### Archivos

| Archivo | Líneas | Funciones | Qué hace |
|---|---|---|---|
| `app_comex_cloud.py` | 59 | 0 | Arranque, login y router de módulos |
| `forus_ui.py` | 1.286 | 16 | CSS completo y componentes de pantalla |
| `forus_auth.py` | 181 | 7 | Login y permisos por módulo |
| `forus_parsing.py` | 483 | 26 | Utilidades de parseo compartidas |
| `modules/comex.py` | 1.010 | 27 | Comex, sin cambios de comportamiento |
| `modules/contabilidad.py` | 740 | 16 | Comprobantes de proveedor → Excel |
| `modules/rrhh.py` | 921 | 17 | Boletas de pago y router de RRHH |
| `modules/alquileres.py` | 700 | 18 | Facturas de arriendo → control |

---

## 5. Comprobaciones que se pasaron

**Estáticas** (`validate.py`):

- Los 9 archivos compilan.
- Cero funciones definidas y nunca llamadas.
- Las 34 funciones heredadas no modificadas son idénticas byte a byte al
  commit base, comparadas por AST.
- Ninguna función del original se perdió.
- El CSS es una cadena literal, no un f-string (el fallo de las llaves sin
  doblar no puede repetirse aquí).

**Funcionales** (`test_app.py`, 137 comprobaciones):

- Importes en formato peruano y europeo, negativos, con símbolo de moneda.
- Fechas en cuatro formatos.
- Contabilidad: factura completa (15 campos de cabecera, 2 líneas) y dos
  comprobantes en un mismo PDF.
- Boletas: 19 campos incluidos los tres conceptos de AFP; dos boletas en un PDF.
- Alquileres: contrato, local, mes del periodo, importe sin IGV, dólares,
  anexos descartados, razón social partida por el RUC, duplicados marcados y
  el caso en que el valor sin IGV **no** es la última columna.
- Un PDF ilegible no tumba el lote.
- **AppTest**: login correcto y rechazado, los tres módulos y las dos pantallas
  de RRHH cargando sin `st.exception` **ni `st.error`**.
- Arranque headless: **HTTP 200**.

**Contra tus 7 PDFs reales** (Jockey Plaza, Mall del Sur, Real Plaza,
Lambramani ×3, Mall Plaza):

- 7 de 7 facturas leídas, **7 de 7 cuadran** con su base imponible.
- 10 filas de control, 0 facturas sin conceptos.
- S/ 16.530,98 y US$ 371,81.

Durante las pruebas aparecieron y se corrigieron tres fallos reales: el patrón
`\b?` en `find_money` (inválido en `re`, habría reventado en cada lectura de
importes), el encabezado de la tabla que se buscaba sin normalizar (por eso
`DESCRIPCION` nunca casaba con `Descripción` y Jockey no devolvía conceptos), y
el alias `LOCAL` que acertaba dentro de "RENTA MINIMA **LOCAL** DEL 01/07/2026".

---

## 6. Lo que NO se hizo, y por qué

1. **Tienda queda vacía en la factura de Real Plaza.** Esa factura no trae ni
   contrato ni local con una etiqueta propia: el `LOCAL COMERCIAL` es un
   encabezado de tabla cuyo valor cae en otra línea. Preferí dejarla vacía
   antes que llenarla con texto equivocado. En las otras seis sale. El N° de
   factura y la razón social sí salen en las siete.

2. **FECHA DE ENTREGA siempre vacía.** No está en ningún PDF.

3. **El MES puede no ser el que ustedes usan.** En tu Excel de ejemplo las
   facturas de diciembre van a ENERO, o sea que el mes es el de devengue según
   su criterio interno. Yo tomo el del periodo facturado, que es lo único que
   dice el documento. Cuando no viene, lo anoto en Observaciones para que se
   revise.

4. **Los importes salen sin IGV**, porque es lo que cuadra con las cifras de tu
   Excel (el fondo de promociones es exactamente el 10% de la renta mínima). La
   columna *Importe con IGV* va al lado por si en algún caso necesitas la otra.

5. **Dos comprobantes en la *misma página física* no se separan.** La
   separación es por página. Es deliberado: partir dentro de una página crearía
   documentos fantasma, porque una nota de crédito cita el número de la factura
   que corrige. Un documento por página —que es lo normal— sí funciona.

6. **Las boletas de pago no se probaron con documentos reales.** No me enviaste
   ninguna; están probadas con boletas sintéticas en formato de planilla
   peruana. Con las primeras reales seguramente haya que añadir algún alias de
   concepto: la hoja *Conceptos* lista todo lo leído para poder verificarlo.
   Con las facturas de alquiler no hace falta: esas sí están probadas contra
   tus PDFs.

7. **`parse_money` de Comex se quedó como estaba.** Interpreta `1,234.00` como
   1.234 porque asume el punto como separador de miles. Cambiarlo alteraría los
   resultados de Comex en producción, y eso no es parte de este encargo. Los
   módulos nuevos usan `parse_amount`, que detecta ambos formatos. **Vale la
   pena revisarlo aparte**: si alguna factura de Columbia o Vans trae importes
   en formato americano, hoy se están leyendo mal.

8. **No se hizo conciliación bancaria, asientos contables, control de
   asistencia ni legajo de personal.** No eran las opciones elegidas.

9. **Las boletas llevan datos personales.** No se guardan en ningún lado, pero
   conviene decidir con cuidado quién lleva `rrhh` en `[modulos]`.

---

## 7. Si algo sale mal

Para volver atrás basta con restaurar `app_comex_cloud.py` desde el commit
`2a25d7d` en el historial de GitHub. Los ocho archivos nuevos pueden quedarse
donde están: sin el entrypoint nuevo, nadie los importa.
