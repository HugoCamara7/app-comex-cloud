"""Modulo Contabilidad: lectura de comprobantes de proveedor y salida a Excel.

Cubre factura, boleta, nota de credito, nota de debito y recibo por honorarios
en formato electronico peruano. La lectura es por etiquetas, no por posicion,
de modo que tolera que cada proveedor arme el PDF a su manera. Lo que no logra
identificar no se inventa: queda vacio y se reporta en la hoja Auditoria.
"""
import io
import re

import pandas as pd
import pdfplumber
import streamlit as st

from forus_parsing import (
    collapse_spaces,
    cuadra,
    detect_currency,
    faltantes,
    find_date,
    find_label_value,
    find_money,
    find_percent,
    find_razon_social_emisor,
    fix_mojibake,
    normalize,
    parse_amount,
    split_lines,
)
from forus_ui import (
    close_card,
    format_file_size,
    open_card,
    render_banner,
    render_benefits,
    render_empty,
    render_file_list,
    render_hero,
    render_pipeline,
    render_result_grid,
    render_rules,
    render_stat_grid,
)

DOCUMENT_COLUMNS = [
    "PDF File",
    "Pagina Inicial",
    "Paginas",
    "Tipo Documento",
    "Serie",
    "Correlativo",
    "Numero",
    "RUC Emisor",
    "Razon Social Emisor",
    "RUC Cliente",
    "Razon Social Cliente",
    "Fecha Emision",
    "Fecha Vencimiento",
    "Moneda",
    "Tipo de Cambio",
    "Op. Gravadas",
    "Op. Exoneradas",
    "Op. Inafectas",
    "Op. Gratuitas",
    "Descuentos",
    "ISC",
    "IGV",
    "Otros Cargos",
    "Importe Total",
    "Detraccion %",
    "Detraccion Monto",
    "Orden de Compra",
    "Forma de Pago",
    "Guia de Remision",
    "Items",
    "Cuadra Total",
    "Observaciones",
]

ITEM_COLUMNS = [
    "PDF File",
    "Numero",
    "Tipo Documento",
    "Fecha Emision",
    "RUC Emisor",
    "Pagina",
    "Linea",
    "Codigo",
    "Descripcion",
    "Cantidad",
    "UM",
    "Valor Unitario",
    "Precio Unitario",
    "Descuento",
    "Importe",
]

SUMMARY_COLUMNS = ["Metric", "Value"]

AUDIT_COLUMNS = ["PDF File", "Pagina", "Rol", "Numero", "Detalle"]

CAMPOS_OBLIGATORIOS = [
    "Numero",
    "RUC Emisor",
    "Fecha Emision",
    "Importe Total",
]

# El orden manda: una nota de credito nombra a la factura que corrige, asi que
# hay que descartarla como nota antes de mirar si dice "factura".
TIPOS_DOCUMENTO = [
    ("NOTA DE CREDITO", "Nota de Credito"),
    ("NOTA DE DEBITO", "Nota de Debito"),
    ("RECIBO POR HONORARIOS", "Recibo por Honorarios"),
    ("BOLETA DE VENTA", "Boleta de Venta"),
    ("FACTURA", "Factura"),
]

TIPO_POR_SERIE = {
    "F": "Factura",
    "B": "Boleta de Venta",
    "E": "Recibo por Honorarios",
}

SERIE_ELECTRONICA_RE = re.compile(r"\b([FBE][A-Z0-9]{2,3})\s*[-‐-―]\s*(\d{1,10})\b")
SERIE_FISICA_RE = re.compile(r"\b(\d{3,4})\s*[-‐-―]\s*(\d{5,10})\b")

ALIAS_COLUMNAS = [
    ("Codigo", ["CODIGO INTERNO", "COD. PRODUCTO", "CODIGO", "COD.", "COD", "ITEM"]),
    ("Descripcion", ["DESCRIPCION", "DETALLE", "CONCEPTO", "PRODUCTO", "BIEN O SERVICIO"]),
    ("Cantidad", ["CANTIDAD", "CANT.", "CANT", "CTD"]),
    ("UM", ["UNIDAD DE MEDIDA", "UNIDAD", "U.M.", "UM", "MEDIDA"]),
    ("Valor Unitario", ["VALOR UNITARIO", "VALOR UNIT.", "V. UNITARIO", "VALOR UNIT"]),
    ("Precio Unitario", ["PRECIO UNITARIO", "PRECIO UNIT.", "P. UNITARIO", "PRECIO UNIT", "PRECIO"]),
    ("Descuento", ["DESCUENTO", "DSCTO.", "DSCTO"]),
    ("Importe", ["IMPORTE TOTAL", "VALOR DE VENTA", "VALOR VENTA", "IMPORTE", "SUBTOTAL", "TOTAL"]),
]

UNIDADES = {
    "NIU", "ZZ", "UND", "UNI", "UNIDAD", "KGM", "KG", "GRM", "LTR", "MTR",
    "CJA", "BOL", "PAR", "SET", "DOC", "GLL", "M2", "M3", "SER", "PZA",
}


def get_ruc_empresa():
    """RUC de la empresa, opcional, para distinguir al emisor del cliente."""
    try:
        empresa = dict(st.secrets.get("empresa", {}))
    except Exception:
        empresa = {}
    ruc = str(empresa.get("ruc", "")).strip()
    return ruc if re.fullmatch(r"\d{11}", ruc) else None


def detect_tipo_documento(text):
    plano = normalize(text)
    for patron, etiqueta in TIPOS_DOCUMENTO:
        if patron in plano:
            return etiqueta
    return None


def detect_serie_correlativo(text):
    """Serie y correlativo del comprobante, priorizando la serie electronica."""
    match = SERIE_ELECTRONICA_RE.search(text or "")
    if match:
        return match.group(1).upper(), match.group(2)

    match = SERIE_FISICA_RE.search(text or "")
    if match:
        return match.group(1), match.group(2)

    return None, None


def formatear_numero(serie, correlativo):
    if not serie or not correlativo:
        return None
    return f"{serie}-{correlativo}"


def extraer_rucs(text):
    """Devuelve (ruc_emisor, ruc_cliente) mirando todos los RUC del documento."""
    encontrados = []
    for match in re.finditer(r"\b((?:10|15|16|17|20)\d{9})\b", text or ""):
        ruc = match.group(1)
        if ruc not in encontrados:
            encontrados.append(ruc)

    if not encontrados:
        return None, None

    ruc_empresa = get_ruc_empresa()
    if ruc_empresa and ruc_empresa in encontrados:
        emisor = next((ruc for ruc in encontrados if ruc != ruc_empresa), None)
        return emisor, ruc_empresa

    emisor = encontrados[0]
    cliente = encontrados[1] if len(encontrados) > 1 else None
    return emisor, cliente


def extraer_cabecera(text, pagina_inicial, paginas):
    serie, correlativo = detect_serie_correlativo(text)
    tipo = detect_tipo_documento(text)
    if not tipo and serie:
        tipo = TIPO_POR_SERIE.get(serie[0].upper())

    ruc_emisor, ruc_cliente = extraer_rucs(text)

    moneda = detect_currency(find_label_value(text, ["MONEDA", "TIPO DE MONEDA"]) or "")
    if not moneda:
        moneda = detect_currency(text)

    gravadas = find_money(text, [
        "OP. GRAVADAS", "OP GRAVADAS", "OPERACIONES GRAVADAS", "OP. GRAVADA",
        "TOTAL OPERACIONES GRAVADAS", "SUB TOTAL VENTAS", "VALOR DE VENTA",
        "SUBTOTAL", "SUB TOTAL",
    ])
    igv = find_money(text, [
        "IGV (18%)", "I.G.V. (18%)", "IGV 18%", "I.G.V. 18%", "IGV(18%)",
        "IMPUESTO GENERAL A LAS VENTAS", "I.G.V.", "IGV",
    ])
    total = find_money(text, [
        "IMPORTE TOTAL", "TOTAL A PAGAR", "PRECIO VENTA", "TOTAL VENTA",
        "TOTAL COMPROBANTE", "TOTAL",
    ])

    fila = {
        "Pagina Inicial": pagina_inicial,
        "Paginas": paginas,
        "Tipo Documento": tipo,
        "Serie": serie,
        "Correlativo": correlativo,
        "Numero": formatear_numero(serie, correlativo),
        "RUC Emisor": ruc_emisor,
        "Razon Social Emisor": find_razon_social_emisor(text, excluir=("FORUS",)),
        "RUC Cliente": ruc_cliente,
        "Razon Social Cliente": find_label_value(text, [
            "SENOR(ES)", "SENORES", "SENOR", "CLIENTE", "ADQUIRIENTE",
            "RAZON SOCIAL", "DENOMINACION",
        ]),
        "Fecha Emision": find_date(text, [
            "FECHA DE EMISION", "FECHA EMISION", "F. EMISION", "FECHA",
        ]),
        "Fecha Vencimiento": find_date(text, [
            "FECHA DE VENCIMIENTO", "FECHA VENCIMIENTO", "F. VENCIMIENTO",
        ]),
        "Moneda": moneda,
        "Tipo de Cambio": find_money(text, ["TIPO DE CAMBIO", "T.C.", "TIPO CAMBIO"]),
        "Op. Gravadas": gravadas,
        "Op. Exoneradas": find_money(text, [
            "OP. EXONERADAS", "OP EXONERADAS", "OPERACIONES EXONERADAS", "OP. EXONERADA",
        ]),
        "Op. Inafectas": find_money(text, [
            "OP. INAFECTAS", "OP INAFECTAS", "OPERACIONES INAFECTAS", "OP. INAFECTA",
        ]),
        "Op. Gratuitas": find_money(text, [
            "OP. GRATUITAS", "OP GRATUITAS", "OPERACIONES GRATUITAS",
        ]),
        "Descuentos": find_money(text, [
            "TOTAL DESCUENTOS", "DESCUENTO GLOBAL", "DESCUENTOS",
        ]),
        "ISC": find_money(text, ["I.S.C.", "ISC"]),
        "IGV": igv,
        "Otros Cargos": find_money(text, ["OTROS CARGOS", "OTROS TRIBUTOS"]),
        "Importe Total": total,
        "Detraccion %": find_percent(text, [
            "PORCENTAJE DE DETRACCION", "DETRACCION", "OPERACION SUJETA A DETRACCION",
        ]),
        "Detraccion Monto": find_money(text, [
            "MONTO DE DETRACCION", "MONTO DETRACCION", "TOTAL DETRACCION",
        ]),
        "Orden de Compra": find_label_value(text, [
            "ORDEN DE COMPRA", "ORDEN COMPRA", "O/C", "NRO. ORDEN", "NUMERO DE ORDEN",
        ], max_chars=40),
        "Forma de Pago": find_label_value(text, [
            "FORMA DE PAGO", "CONDICION DE PAGO", "CONDICIONES DE PAGO", "TIPO DE PAGO",
        ], max_chars=40),
        "Guia de Remision": find_label_value(text, [
            "GUIA DE REMISION", "GUIA REMISION",
        ], max_chars=40),
    }

    esperado = None
    if gravadas is not None or igv is not None:
        esperado = (gravadas or 0) + (igv or 0)
        for extra in ("Op. Exoneradas", "Op. Inafectas", "ISC", "Otros Cargos"):
            esperado += fila.get(extra) or 0
    fila["Cuadra Total"] = cuadra(esperado, total, tolerancia=0.10)

    return fila


def mapear_columnas(encabezado):
    """Empareja las celdas del encabezado de una tabla con nuestros campos."""
    mapa = {}
    usados = set()

    for indice, celda in enumerate(encabezado):
        plano = normalize(collapse_spaces(celda) or "")
        if not plano:
            continue
        for campo, alias in ALIAS_COLUMNAS:
            if campo in usados:
                continue
            if any(nombre in plano for nombre in alias):
                mapa[campo] = indice
                usados.add(campo)
                break

    return mapa


def items_desde_tablas(tables, pagina):
    """Lee los items de las tablas cuyo encabezado reconocemos."""
    items = []

    for table in tables or []:
        if not table or len(table) < 2:
            continue

        mapa = mapear_columnas(table[0])
        if "Descripcion" not in mapa or not ({"Cantidad", "Importe"} & set(mapa)):
            continue

        for fila_tabla in table[1:]:
            if not fila_tabla:
                continue

            def valor(campo):
                indice = mapa.get(campo)
                if indice is None or indice >= len(fila_tabla):
                    return None
                return collapse_spaces(fila_tabla[indice])

            descripcion = valor("Descripcion")
            importe = parse_amount(valor("Importe"))
            cantidad = parse_amount(valor("Cantidad"))

            if not descripcion and importe is None:
                continue
            if descripcion and normalize(descripcion).startswith(("SON ", "TOTAL", "SUB TOTAL")):
                continue

            items.append({
                "Pagina": pagina,
                "Codigo": valor("Codigo"),
                "Descripcion": descripcion,
                "Cantidad": cantidad,
                "UM": valor("UM"),
                "Valor Unitario": parse_amount(valor("Valor Unitario")),
                "Precio Unitario": parse_amount(valor("Precio Unitario")),
                "Descuento": parse_amount(valor("Descuento")),
                "Importe": importe,
            })

    return items


LINEA_ITEM_RE = re.compile(
    r"^(?P<cantidad>\d[\d.,]*)\s+(?P<um>[A-Z0-9]{1,10})\s+(?P<resto>.+?)\s+"
    r"(?P<unitario>-?[\d.,]+)\s+(?P<importe>-?[\d.,]+)$"
)


def items_desde_texto(text, pagina):
    """Respaldo para PDFs sin tablas: lineas 'cantidad UM descripcion unit importe'."""
    items = []

    for linea in split_lines(text):
        plano = normalize(linea)
        if plano.startswith(("SON ", "TOTAL", "SUB TOTAL", "OP.", "IGV", "IMPORTE TOTAL")):
            continue

        match = LINEA_ITEM_RE.match(collapse_spaces(linea) or "")
        if not match:
            continue
        if normalize(match.group("um")) not in UNIDADES:
            continue

        items.append({
            "Pagina": pagina,
            "Codigo": None,
            "Descripcion": collapse_spaces(match.group("resto")),
            "Cantidad": parse_amount(match.group("cantidad")),
            "UM": match.group("um"),
            "Valor Unitario": parse_amount(match.group("unitario")),
            "Precio Unitario": None,
            "Descuento": None,
            "Importe": parse_amount(match.group("importe")),
        })

    return items


def leer_paginas(uploaded_file):
    """Texto y tablas de cada pagina, con la codificacion ya corregida."""
    paginas = []
    with pdfplumber.open(io.BytesIO(uploaded_file.getvalue())) as pdf:
        for numero, page in enumerate(pdf.pages, start=1):
            paginas.append({
                "numero": numero,
                "texto": fix_mojibake(page.extract_text() or ""),
                "tablas": page.extract_tables() or [],
            })
    return paginas


def agrupar_documentos(paginas):
    """Reparte las paginas entre comprobantes.

    Una pagina abre un comprobante nuevo cuando trae un tipo de documento y un
    numero de serie distinto al que se venia leyendo. Un PDF con un solo
    comprobante de varias paginas se queda como un unico documento.
    """
    documentos = []

    for pagina in paginas:
        texto = pagina["texto"]
        serie, correlativo = detect_serie_correlativo(texto)
        numero = formatear_numero(serie, correlativo)
        tiene_tipo = detect_tipo_documento(texto) is not None

        abre_documento = bool(numero) and tiene_tipo and (
            not documentos or documentos[-1]["numero"] != numero
        )

        if abre_documento or not documentos:
            documentos.append({
                "numero": numero,
                "pagina_inicial": pagina["numero"],
                "paginas": [pagina],
            })
        else:
            documentos[-1]["paginas"].append(pagina)

    return documentos


def process_pdf(uploaded_file):
    """Devuelve (documentos, items, auditoria) para un PDF."""
    paginas = leer_paginas(uploaded_file)
    documentos_agrupados = agrupar_documentos(paginas)

    filas_documento = []
    filas_item = []
    filas_auditoria = []

    for documento in documentos_agrupados:
        texto_completo = "\n".join(pagina["texto"] for pagina in documento["paginas"])
        cabecera = extraer_cabecera(
            texto_completo,
            documento["pagina_inicial"],
            len(documento["paginas"]),
        )
        cabecera["PDF File"] = uploaded_file.name

        items = []
        for pagina in documento["paginas"]:
            encontrados = items_desde_tablas(pagina["tablas"], pagina["numero"])
            if not encontrados:
                encontrados = items_desde_texto(pagina["texto"], pagina["numero"])
            items.extend(encontrados)

        for orden, item in enumerate(items, start=1):
            item.update({
                "PDF File": uploaded_file.name,
                "Numero": cabecera["Numero"],
                "Tipo Documento": cabecera["Tipo Documento"],
                "Fecha Emision": cabecera["Fecha Emision"],
                "RUC Emisor": cabecera["RUC Emisor"],
                "Linea": orden,
            })

        cabecera["Items"] = len(items)
        cabecera["Observaciones"] = faltantes(cabecera, CAMPOS_OBLIGATORIOS)

        filas_documento.append(cabecera)
        filas_item.extend(items)

        for indice, pagina in enumerate(documento["paginas"]):
            filas_auditoria.append({
                "PDF File": uploaded_file.name,
                "Pagina": pagina["numero"],
                "Rol": "inicio_documento" if indice == 0 else "continuacion",
                "Numero": cabecera["Numero"],
                "Detalle": cabecera["Observaciones"] and f"Sin leer: {cabecera['Observaciones']}",
            })

    return filas_documento, filas_item, filas_auditoria


def build_summary_rows(documentos, items, auditoria, archivos):
    por_moneda = {}
    for documento in documentos:
        moneda = documento.get("Moneda") or "Sin moneda"
        por_moneda[moneda] = por_moneda.get(moneda, 0) + (documento.get("Importe Total") or 0)

    filas = [
        {"Metric": "Archivos procesados", "Value": archivos},
        {"Metric": "Paginas leidas", "Value": len(auditoria)},
        {"Metric": "Comprobantes", "Value": len(documentos)},
        {"Metric": "Lineas de detalle", "Value": len(items)},
        {"Metric": "Total IGV", "Value": round(sum(d.get("IGV") or 0 for d in documentos), 2)},
    ]

    for moneda, total in sorted(por_moneda.items()):
        filas.append({"Metric": f"Importe total {moneda}", "Value": round(total, 2)})

    filas.append({
        "Metric": "Comprobantes que no cuadran",
        "Value": sum(1 for d in documentos if d.get("Cuadra Total") == "No"),
    })
    filas.append({
        "Metric": "Comprobantes con campos sin leer",
        "Value": sum(1 for d in documentos if d.get("Observaciones")),
    })

    return filas


def build_excel(files):
    documentos = []
    items = []
    auditoria = []

    for uploaded_file in files:
        try:
            filas_documento, filas_item, filas_auditoria = process_pdf(uploaded_file)
        except Exception as error:  # un PDF danado no debe tumbar el lote entero
            auditoria.append({
                "PDF File": uploaded_file.name,
                "Pagina": None,
                "Rol": "error",
                "Numero": None,
                "Detalle": f"No se pudo leer: {error}",
            })
            continue

        documentos.extend(filas_documento)
        items.extend(filas_item)
        auditoria.extend(filas_auditoria)

    resumen = build_summary_rows(documentos, items, auditoria, len(files))

    output = io.BytesIO()
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        pd.DataFrame(documentos).reindex(columns=DOCUMENT_COLUMNS).to_excel(
            writer, index=False, sheet_name="Documentos",
        )
        pd.DataFrame(items).reindex(columns=ITEM_COLUMNS).to_excel(
            writer, index=False, sheet_name="Detalle",
        )
        pd.DataFrame(resumen).reindex(columns=SUMMARY_COLUMNS).to_excel(
            writer, index=False, sheet_name="Resumen",
        )
        pd.DataFrame(auditoria).reindex(columns=AUDIT_COLUMNS).to_excel(
            writer, index=False, sheet_name="Auditoria",
        )

    output.seek(0)
    return output, documentos, items


def render_sidebar():
    """Panel lateral propio del modulo Contabilidad."""
    with st.sidebar:
        st.markdown('<div class="side-title">Documentos aceptados</div>', unsafe_allow_html=True)
        st.markdown(
            """
            <div class="side-card">
                FACTURA<br>
                BOLETA DE VENTA<br>
                NOTA DE CREDITO<br>
                NOTA DE DEBITO<br>
                RECIBO POR HONORARIOS
            </div>
            """,
            unsafe_allow_html=True,
        )

        st.markdown('<div class="side-title">Operacion</div>', unsafe_allow_html=True)
        st.markdown(
            """
            <div class="side-note">
                No hace falta renombrar los archivos.<br>
                Un PDF puede traer varios comprobantes.
            </div>
            """,
            unsafe_allow_html=True,
        )


ICONO_CONTABILIDAD = """
<svg viewBox="0 0 96 96" width="72" height="72" aria-label="Contabilidad">
    <rect x="20" y="10" width="56" height="76" rx="8" fill="#ffffff" stroke="#bdd4f7"/>
    <rect x="28" y="20" width="40" height="14" rx="4" fill="#d7f2e7"/>
    <rect x="28" y="42" width="16" height="12" rx="3" fill="#10916d"/>
    <rect x="50" y="42" width="18" height="12" rx="3" fill="#cfe6dd"/>
    <rect x="28" y="60" width="16" height="12" rx="3" fill="#cfe6dd"/>
    <rect x="50" y="60" width="18" height="12" rx="3" fill="#10916d"/>
</svg>
"""


def render():
    """Pantalla completa del modulo Contabilidad."""
    st.markdown('<div class="app-shell">', unsafe_allow_html=True)

    render_hero(
        "CONTABILIDAD DOCUMENT CENTER",
        'Comprobantes de proveedor <span style="color:#8ff0cf">&rsaquo;</span> Excel consolidado',
        "Sube facturas, boletas, notas y recibos por honorarios y genera un Excel con Documentos, Detalle, Resumen y Auditoria.",
        tags=[("Lectura por etiquetas", "green")],
        variante="acct",
        icono_svg=ICONO_CONTABILIDAD,
    )

    render_pipeline([
        ("Input", "Comprobantes PDF", "active", "Pend."),
        ("Lectura", "Etiquetas SUNAT", "ok", "OK"),
        ("Validacion", "Gravadas + IGV vs Total", "warn", "Revisar"),
        ("Salida", "Excel Contabilidad", "", "Pend."),
    ])

    render_rules(
        "Preparar lectura de comprobantes",
        "El sistema identifica el tipo de documento por su titulo y su serie, y separa cada comprobante aunque vengan varios en el mismo PDF.",
        [
            ("Cabecera", "RUC, razon social, fechas, moneda y tipo de cambio"),
            ("Importes", "Gravadas, exoneradas, inafectas, IGV, ISC y total"),
            ("Detalle", "Codigo, descripcion, cantidad, unitario e importe"),
        ],
        variante="acct",
    )

    st.markdown('<div class="work-card upload-wrap acct"><h3>1. Cargar comprobantes</h3>', unsafe_allow_html=True)
    uploaded_files = st.file_uploader(
        "Subir PDFs de comprobantes",
        type=["pdf"],
        accept_multiple_files=True,
        label_visibility="collapsed",
        key="contabilidad_uploader",
    )
    st.markdown("</div>", unsafe_allow_html=True)

    open_card("2. Archivos cargados", kicker="Control de entrada")

    if uploaded_files:
        render_stat_grid([
            ("PDFs cargados", len(uploaded_files)),
            ("Tamano total", format_file_size(
                sum(getattr(archivo, "size", 0) or 0 for archivo in uploaded_files)
            )),
            ("Estado", "Listos"),
        ])
        render_file_list(
            [
                (
                    archivo.name,
                    f"{format_file_size(getattr(archivo, 'size', None))} - Comprobante",
                    "PDF",
                    "Listo",
                )
                for archivo in uploaded_files
            ],
            variante="acct",
        )
    else:
        render_empty("Carga los comprobantes de tus proveedores para comenzar.")

    close_card()

    open_card(
        "3. Procesar y generar Excel",
        kicker="Salida final",
        texto="Convierte tus comprobantes en un Excel con las hojas Documentos, Detalle, Resumen y Auditoria.",
    )

    if st.button(
        "Procesar comprobantes",
        type="primary",
        disabled=not uploaded_files,
        key="contabilidad_procesar",
    ):
        with st.spinner("Leyendo comprobantes..."):
            excel_bytes, documentos, items = build_excel(uploaded_files)

        sin_leer = sum(1 for documento in documentos if documento.get("Observaciones"))
        descuadrados = sum(1 for documento in documentos if documento.get("Cuadra Total") == "No")

        if documentos:
            render_banner("Excel generado correctamente. Ya puedes descargar la salida consolidada.")
        else:
            render_banner(
                "No se reconocio ningun comprobante. Revisa la hoja Auditoria del Excel.",
                tipo="warn",
            )

        render_result_grid([
            ("Comprobantes leidos", len(documentos)),
            ("Lineas de detalle", len(items)),
        ])

        if sin_leer or descuadrados:
            render_banner(
                f"{sin_leer} comprobante(s) con campos sin leer y {descuadrados} donde "
                "gravadas + IGV no coincide con el total. Estan marcados en el Excel.",
                tipo="warn",
            )

        if documentos:
            st.dataframe(
                pd.DataFrame(documentos).reindex(columns=[
                    "Numero", "Tipo Documento", "Fecha Emision", "RUC Emisor",
                    "Razon Social Emisor", "Moneda", "Op. Gravadas", "IGV",
                    "Importe Total", "Cuadra Total", "Observaciones",
                ]).head(50),
                use_container_width=True,
                hide_index=True,
            )

        st.download_button(
            "Descargar Excel",
            data=excel_bytes,
            file_name="salida_contabilidad.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            key="contabilidad_descargar",
        )

    close_card()

    render_benefits([
        ("Control de cuadre", "Compara gravadas mas IGV contra el importe total de cada comprobante."),
        ("Sin renombrar archivos", "Detecta el tipo de documento por su contenido, no por el nombre."),
        ("Nada inventado", "Lo que no se puede leer queda vacio y aparece en la hoja Auditoria."),
    ])

    st.markdown("</div>", unsafe_allow_html=True)
