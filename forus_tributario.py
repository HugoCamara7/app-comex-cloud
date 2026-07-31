"""Detraccion y retencion: decide sola si una factura esta afecta y por cuanto.

La regla practica es esta:

1. Si el comprobante dice que esta sujeto a detraccion (SPOT), manda eso. El
   proveedor ya determino el tipo de servicio y su tasa, y ahi no hay que
   adivinar: se lee el porcentaje que trae impreso.
2. Si no hay detraccion y el importe total no pasa de S/ 700, no corresponde
   retencion.
3. Si pasa de S/ 700 y el proveedor no es agente de retencion, buen
   contribuyente ni agente de percepcion, corresponde retencion del IGV.

Detraccion y retencion son excluyentes: nunca se aplican las dos a la vez.

Cuando falta un dato para decidir -por ejemplo una factura en dolares sin tipo
de cambio con el que llevar el importe a soles- no se inventa una respuesta: la
fila sale como REVISAR diciendo que falta.
"""
import re

import streamlit as st

from forus_parsing import normalize, parse_amount

UMBRAL_DEFECTO = 700.0
TASA_RETENCION_DEFECTO = 3.0

DETRACCION = "DETRACCION"
RETENCION = "RETENCION"
NO_AFECTO = "NO AFECTO"
REVISAR = "REVISAR"

SENALES_DETRACCION = (
    "DETRACCION",
    "SPOT",
    "SISTEMA DE PAGO DE OBLIGACIONES TRIBUTARIAS",
    "DECRETO LEGISLATIVO 940",
    "D.L. 940",
)

# Si el proveedor ya es agente de retencion, percepcion o buen contribuyente,
# no se le retiene.
SENALES_SIN_RETENCION = (
    "AGENTE DE RETENCION",
    "AGENTES DE RETENCION",
    "AGENTE DE PERCEPCION",
    "AGENTES DE PERCEPCION",
    "BUEN CONTRIBUYENTE",
    "BUENOS CONTRIBUYENTES",
)

# "Porcentaje Detracción: 10.0%", "DETRACCION DEL 10 %" y tambien la forma sin
# simbolo que usa la representacion impresa de SUNAT: "Porcentaje de detracción: 10.00".
PORCENTAJE_DETRACCION_RE = re.compile(
    r"(?:PORCENTAJE\s+(?:DE\s+)?DETRACCION|DETRACCION\s+DEL?|SPOT)"
    r"[^\d%]{0,40}?(\d{1,2}(?:[.,]\d{1,2})?)\s*%",
    re.I,
)
PORCENTAJE_DETRACCION_SIN_SIMBOLO_RE = re.compile(
    r"PORCENTAJE\s+(?:DE\s+)?DETRACCI[OÓ]N\s*[:.]?\s*(\d{1,2}(?:[.,]\d{1,2})?)",
    re.I,
)
PORCENTAJE_SUELTO_RE = re.compile(r"(\d{1,2}(?:[.,]\d{1,2})?)\s*%")
CODIGO_DETRACCION_RE = re.compile(r"C[O�]DIGO\s+(?:DE\s+)?DETRACCI[O�]N\s*:?\s*(\d{3})", re.I)


def get_parametros():
    """Umbral y tasa de retencion, ajustables desde los secrets sin tocar codigo."""
    try:
        configurado = dict(st.secrets.get("tributario", {}))
    except Exception:
        configurado = {}

    umbral = parse_amount(configurado.get("umbral", UMBRAL_DEFECTO))
    tasa = parse_amount(configurado.get("tasa_retencion", TASA_RETENCION_DEFECTO))

    return {
        "umbral": umbral if umbral is not None else UMBRAL_DEFECTO,
        "tasa_retencion": tasa if tasa is not None else TASA_RETENCION_DEFECTO,
        # Algunos comprobantes dicen que hay detraccion pero no imprimen la
        # tasa. Por defecto no se inventa ninguna; si se configura una aqui, se
        # aplica y queda dicho en el motivo de esa fila.
        "tasa_detraccion_defecto": parse_amount(configurado.get("tasa_detraccion_defecto")),
    }


def detectar_detraccion(texto):
    """(sujeta, porcentaje, codigo) segun lo que declare el propio comprobante."""
    plano = normalize(texto or "")
    sujeta = any(senal in plano for senal in SENALES_DETRACCION)
    if not sujeta:
        return False, None, None

    codigo = None
    match_codigo = CODIGO_DETRACCION_RE.search(texto or "")
    if match_codigo:
        codigo = match_codigo.group(1)

    porcentaje = None
    match = PORCENTAJE_DETRACCION_RE.search(texto or "")
    if match:
        porcentaje = parse_amount(match.group(1))

    if porcentaje is None:
        match = PORCENTAJE_DETRACCION_SIN_SIMBOLO_RE.search(texto or "")
        if match:
            porcentaje = parse_amount(match.group(1))

    if porcentaje is None:
        # Algunos lo imprimen suelto, en la misma linea de la cuenta del Banco
        # de la Nacion. Solo se acepta si es una tasa de detraccion plausible.
        for linea in (texto or "").splitlines():
            if not any(senal in normalize(linea) for senal in SENALES_DETRACCION):
                continue
            for candidato in PORCENTAJE_SUELTO_RE.findall(linea):
                valor = parse_amount(candidato)
                if valor is not None and 1 <= valor <= 15:
                    porcentaje = valor
                    break
            if porcentaje is not None:
                break

    return True, porcentaje, codigo


def proveedor_sin_retencion(texto):
    """El comprobante declara una condicion que excluye la retencion."""
    plano = normalize(texto or "")
    for senal in SENALES_SIN_RETENCION:
        if senal in plano:
            return senal.capitalize()
    return None


def importe_en_soles(total, moneda, tipo_cambio):
    """Lleva el importe a soles, que es donde se mide el umbral."""
    if total is None:
        return None
    if moneda in (None, "PEN"):
        return total
    if tipo_cambio:
        return round(total * tipo_cambio, 2)
    return None


def evaluar(total, moneda, tipo_cambio, texto, monto_detraccion=None, parametros=None,
            igv=None, tipo_documento=None):
    """Decide si la factura va con detraccion, con retencion o con ninguna.

    Devuelve las columnas listas para el Excel, incluido el motivo, para que no
    haya que rehacer el razonamiento a mano factura por factura.
    """
    parametros = parametros or get_parametros()
    umbral = parametros["umbral"]
    tasa_retencion = parametros["tasa_retencion"]

    en_soles = importe_en_soles(total, moneda, tipo_cambio)
    sujeta, porcentaje, codigo = detectar_detraccion(texto)

    vacio = {
        "Afecto a": REVISAR,
        "Motivo": None,
        "Codigo Detraccion": codigo,
        "% Aplicado": None,
        "Monto Detraccion/Retencion": None,
        "Neto a Pagar": None,
        "Importe en Soles": en_soles,
    }

    if total is None:
        vacio["Motivo"] = "No se pudo leer el importe total"
        return vacio

    # 0. Una nota de credito no genera detraccion ni retencion propias: lo que
    # hace es restar del total a pagar al proveedor.
    if tipo_documento == "Nota de Credito":
        return {
            "Afecto a": NO_AFECTO,
            "Motivo": "Nota de credito: resta del total a pagar, sin detraccion ni retencion propia",
            "Codigo Detraccion": None,
            "% Aplicado": None,
            "Monto Detraccion/Retencion": 0.0,
            "Neto a Pagar": total,
            "Importe en Soles": en_soles,
        }

    # 1. La detraccion declarada en el comprobante manda sobre cualquier calculo.
    if sujeta:
        monto = monto_detraccion
        por_defecto = False

        if porcentaje is None and parametros.get("tasa_detraccion_defecto"):
            porcentaje = parametros["tasa_detraccion_defecto"]
            por_defecto = True

        if monto is None and porcentaje is not None:
            bruto = total * porcentaje / 100
            # El deposito de la detraccion se hace en soles enteros. En una
            # factura en dolares el importe se conserva con sus decimales: el
            # redondeo corresponde al monto en soles del dia del deposito.
            monto = float(round(bruto)) if moneda in (None, "PEN") else round(bruto, 2)

        if monto is None:
            return {
                **vacio,
                "Afecto a": DETRACCION,
                "Motivo": "El comprobante indica detraccion pero no imprime el porcentaje: completar a mano",
                "% Aplicado": None,
            }

        if por_defecto:
            motivo = f"Detraccion sin porcentaje impreso: se aplico el {porcentaje}% configurado"
        elif porcentaje is not None:
            motivo = f"El comprobante indica detraccion del {porcentaje}%"
        else:
            motivo = "El comprobante indica detraccion"

        return {
            "Afecto a": DETRACCION,
            "Motivo": motivo,
            "Codigo Detraccion": codigo,
            "% Aplicado": porcentaje,
            "Monto Detraccion/Retencion": monto,
            "Neto a Pagar": round(total - monto, 2),
            "Importe en Soles": en_soles,
        }

    # 2. Sin detraccion, el umbral se mide en soles.
    if en_soles is None:
        vacio["Motivo"] = f"Factura en {moneda or 'moneda desconocida'} sin tipo de cambio: no se puede comparar con el umbral"
        return vacio

    if en_soles <= umbral:
        return {
            "Afecto a": NO_AFECTO,
            "Motivo": f"Importe de S/ {en_soles:,.2f} menor o igual al umbral de S/ {umbral:,.0f}",
            "Codigo Detraccion": None,
            "% Aplicado": None,
            "Monto Detraccion/Retencion": 0.0,
            "Neto a Pagar": total,
            "Importe en Soles": en_soles,
        }

    # 3. La retencion es del IGV: sin IGV que retener, no corresponde. Pasa con
    # los intereses moratorios y demas operaciones inafectas.
    if igv is not None and igv == 0:
        return {
            "Afecto a": NO_AFECTO,
            "Motivo": "Operacion sin IGV (inafecta o exonerada): no hay retencion que aplicar",
            "Codigo Detraccion": None,
            "% Aplicado": None,
            "Monto Detraccion/Retencion": 0.0,
            "Neto a Pagar": total,
            "Importe en Soles": en_soles,
        }

    # 4. Pasa el umbral: retencion, salvo que el proveedor este excluido.
    excluido = proveedor_sin_retencion(texto)
    if excluido:
        return {
            "Afecto a": NO_AFECTO,
            "Motivo": f"Supera el umbral pero el proveedor figura como {excluido}",
            "Codigo Detraccion": None,
            "% Aplicado": None,
            "Monto Detraccion/Retencion": 0.0,
            "Neto a Pagar": total,
            "Importe en Soles": en_soles,
        }

    monto = round(total * tasa_retencion / 100, 2)
    return {
        "Afecto a": RETENCION,
        "Motivo": f"Supera el umbral de S/ {umbral:,.0f} y el proveedor no esta excluido",
        "Codigo Detraccion": None,
        "% Aplicado": tasa_retencion,
        "Monto Detraccion/Retencion": monto,
        "Neto a Pagar": round(total - monto, 2),
        "Importe en Soles": en_soles,
    }
