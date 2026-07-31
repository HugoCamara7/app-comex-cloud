"""Identificacion de comprobantes electronicos peruanos.

Estas piezas nacieron leyendo facturas reales de centros comerciales y son las
que evitan los tres errores tipicos: tomar una cuenta bancaria por un numero de
factura, quedarse con el numero del documento que una nota de credito corrige,
y tratar los anexos -detalle de facturacion, estado de cuenta- como si fueran
comprobantes aparte. Las usan tanto Contabilidad como Arriendos, para que las
dos identifiquen igual.
"""
import re

from forus_parsing import REEMPLAZO, normalize, parse_amount, split_lines


# El titulo del comprobante electronico. Es lo unico que distingue de verdad la
# pagina de la factura de sus anexos (detalle de facturacion, estado de cuenta),
# que tambien nombran facturas y llevan numeros de serie.
MARCADOR_DOCUMENTO_RE = re.compile(
    r"\b(?:FACTURA|BOLETA\s+DE\s+VENTA"
    rf"|NOTA\s+DE\s+D[E{REEMPLAZO}]BITO|NOTA\s+DE\s+CR[E{REEMPLAZO}]DITO)"
    rf"\s+ELECTR[O{REEMPLAZO}]NICA",
    re.I,
)

TIPOS_DOCUMENTO = [
    (rf"NOTA\s+DE\s+CR[E{REEMPLAZO}]DITO", "Nota de Credito"),
    (rf"NOTA\s+DE\s+D[E{REEMPLAZO}]BITO", "Nota de Debito"),
    (r"BOLETA\s+DE\s+VENTA", "Boleta de Venta"),
    (r"FACTURA", "Factura"),
]

# "N| F004 - 00332954", "F102- 00162720", "F001 N| 00946359": el separador
# cambia en cada proveedor, incluido el simbolo de numero mal decodificado.
NUMERO_FACTURA_RE = re.compile(
    rf"\b([FBE][A-Z0-9]{{2,3}})\s*(?:N[°ºO{REEMPLAZO}]?\.?\s*)?[-–]?\s*(\d{{5,10}})\b",
    re.I,
)

def detect_tipo_documento(texto):
    plano = normalize(texto)
    for patron, etiqueta in TIPOS_DOCUMENTO:
        if re.search(patron, plano):
            return etiqueta
    return None


def detect_numero_factura(texto):
    match = NUMERO_FACTURA_RE.search(texto or "")
    if not match:
        return None
    return f"{match.group(1).upper()}-{match.group(2)}"


def es_pagina_de_documento(texto):
    return bool(MARCADOR_DOCUMENTO_RE.search(normalize(texto or "")))

def agrupar_documentos(paginas):
    """Una factura por cada pagina con titulo de comprobante; el resto son anexos."""
    documentos = []

    for pagina in paginas:
        texto = pagina["texto"]
        numero = detect_numero_factura(texto)
        abre = es_pagina_de_documento(texto) and bool(numero) and (
            not documentos or documentos[-1]["numero"] != numero
        )

        if abre:
            documentos.append({
                "numero": numero,
                "pagina_inicial": pagina["numero"],
                "paginas": [pagina],
                "anexos": [],
            })
        elif documentos:
            documentos[-1]["anexos"].append(pagina)

    return documentos


def deducir_totales_por_suma(texto):
    """Rescata base, IGV y total cuando vienen sueltos bajo sus encabezados.

    Algunos proveedores imprimen la fila de totales sin repetir las etiquetas,
    dejando solo tres importes. Solo se aceptan si el tercero es la suma de los
    dos primeros, que es la comprobacion que los identifica sin ambiguedad.
    """
    for linea in split_lines(texto):
        importes = re.findall(r"(?:S/|US\s*\$|\$)\s*(-?[\d.,]+)", linea)
        if len(importes) != 3:
            continue
        valores = [parse_amount(importe) for importe in importes]
        if any(valor is None for valor in valores):
            continue
        if abs((valores[0] + valores[1]) - valores[2]) <= 0.10:
            return valores[0], valores[1], valores[2]
    return None, None, None
