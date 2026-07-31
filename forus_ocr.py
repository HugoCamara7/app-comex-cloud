"""Lectura de facturas escaneadas.

Un PDF electronico lleva el texto dentro y se lee directo. Uno escaneado es una
foto del papel: no hay texto que extraer. Para esos se rasteriza la pagina y se
pasa por OCR.

El OCR devuelve trozos sueltos con sus coordenadas, no lineas. Aqui se vuelven
a juntar por su posicion vertical y se ordenan de izquierda a derecha, de modo
que el resultado se parece a lo que habria devuelto un PDF con texto: eso es lo
que permite que el resto del programa -que busca "IMPORTE TOTAL" y lee lo que
sigue en la misma linea- funcione igual con un escaneo.

Todo esto es opcional: si las librerias no estan instaladas la aplicacion sigue
funcionando con los PDFs normales y avisa de los que no pudo leer.
"""
import io

import streamlit as st

# Resolucion de rasterizado. 200 ppp es el punto donde el OCR ya lee bien los
# importes sin que la pagina tarde de mas.
RESOLUCION = 200

# Por debajo de esta confianza el trozo se descarta: mas vale un hueco que un
# numero inventado.
CONFIANZA_MINIMA = 0.5


def estado_ocr():
    """(disponible, motivo). El motivo explica que falta cuando no se puede usar.

    Importa de verdad las librerias en vez de solo comprobar que esten: OpenCV,
    que va por debajo del OCR, necesita libGL del sistema y falla al importarse
    si no esta. Sin este detalle el OCR se apagaba en silencio y las facturas
    escaneadas salian como ilegibles sin decir por que.
    """
    try:
        import numpy  # noqa: F401
        import pypdfium2  # noqa: F401
    except ImportError as error:
        return False, (
            f"Falta una libreria de lectura de PDF ({error.name}). "
            "Anade pypdfium2 a requirements.txt"
        )

    try:
        from rapidocr_onnxruntime import RapidOCR  # noqa: F401
    except ImportError as error:
        return False, (
            f"Falta el motor de OCR ({error.name}). Anade rapidocr-onnxruntime "
            "a requirements.txt"
        )
    except Exception as error:
        return False, (
            f"El motor de OCR no arranca: {error}. Suele faltar una libreria del "
            "sistema: crea un packages.txt con libgl1 y libglib2.0-0"
        )

    return True, None


def ocr_disponible():
    return estado_ocr()[0]


@st.cache_resource(show_spinner=False)
def _motor():
    """El motor de OCR se carga una sola vez y se reutiliza."""
    from rapidocr_onnxruntime import RapidOCR

    return RapidOCR()


def _agrupar_en_lineas(bloques):
    """Reconstruye las lineas del documento a partir de las cajas del OCR."""
    elementos = []
    for caja, texto, confianza in bloques:
        if not texto:
            continue
        try:  # la confianza llega unas veces como numero y otras como texto
            if float(confianza) < CONFIANZA_MINIMA:
                continue
        except (TypeError, ValueError):
            pass
        ys = [punto[1] for punto in caja]
        xs = [punto[0] for punto in caja]
        elementos.append({
            "centro": sum(ys) / len(ys),
            "alto": max(ys) - min(ys),
            "x": min(xs),
            "texto": str(texto).strip(),
        })

    if not elementos:
        return ""

    elementos.sort(key=lambda e: (e["centro"], e["x"]))

    lineas = []
    actual = [elementos[0]]
    for elemento in elementos[1:]:
        referencia = actual[-1]
        # Dos trozos son de la misma linea si sus centros estan mas cerca que
        # media altura de texto.
        tolerancia = max(referencia["alto"], elemento["alto"]) * 0.6
        if abs(elemento["centro"] - referencia["centro"]) <= tolerancia:
            actual.append(elemento)
        else:
            lineas.append(actual)
            actual = [elemento]
    lineas.append(actual)

    return "\n".join(
        " ".join(trozo["texto"] for trozo in sorted(linea, key=lambda e: e["x"]))
        for linea in lineas
    )


def texto_por_ocr(datos_pdf, numeros_pagina):
    """Texto de las paginas indicadas de un PDF, leidas por OCR.

    Devuelve {numero_de_pagina: texto}. Las paginas que fallen no aparecen.
    """
    if not numeros_pagina or not ocr_disponible():
        return {}

    import numpy as np
    import pypdfium2 as pdfium

    motor = _motor()
    resultados = {}

    documento = pdfium.PdfDocument(io.BytesIO(datos_pdf))
    try:
        for numero in numeros_pagina:
            indice = numero - 1
            if indice < 0 or indice >= len(documento):
                continue
            try:
                imagen = documento[indice].render(scale=RESOLUCION / 72).to_pil()
                bloques, _ = motor(np.array(imagen))
            except Exception:
                continue  # una pagina ilegible no debe frenar a las demas
            texto = _agrupar_en_lineas(bloques or [])
            if texto.strip():
                resultados[numero] = texto
    finally:
        documento.close()

    return resultados


def completar_paginas_vacias(datos_pdf, paginas):
    """Rellena por OCR las paginas que no traian texto.

    Devuelve la lista de paginas leidas asi, para poder avisar de que esas
    cifras vienen de un escaneo y conviene revisarlas.
    """
    vacias = [p["numero"] for p in paginas if not (p["texto"] or "").strip()]
    if not vacias:
        return []

    leidas = texto_por_ocr(datos_pdf, vacias)
    for pagina in paginas:
        if pagina["numero"] in leidas:
            pagina["texto"] = leidas[pagina["numero"]]
            pagina["ocr"] = True

    return sorted(leidas)
