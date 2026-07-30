"""Portal Forus: Comex, Contabilidad y Recursos Humanos en una sola aplicacion.

Este archivo es solo el arranque: configura la pagina, pide el acceso y decide
que modulo mostrar. La logica de cada area vive en modules/.
"""
import streamlit as st

from forus_auth import current_user_modules, require_login
from forus_ui import inject_css, render_sidebar_header
from modules import comex, contabilidad, rrhh

st.set_page_config(
    page_title="Portal Forus",
    page_icon="PDF",
    layout="wide",
)

inject_css()

require_login()

# Cada modulo aporta su panel lateral y su pantalla principal.
REGISTRO_MODULOS = {
    "comex": (comex.render_sidebar, comex.render),
    "contabilidad": (contabilidad.render_sidebar, contabilidad.render),
    "rrhh": (rrhh.render_sidebar, rrhh.render),
}

# Cada destino es un sitio al que se puede ir desde el desplegable. Recursos
# Humanos aparece con sus dos pantallas para llegar de un solo paso.
DESTINOS = [
    ("comex", "Comex", None),
    ("contabilidad", "Contabilidad", None),
    ("rrhh", "Recursos Humanos - Boletas de pago", "boletas"),
    ("rrhh", "Recursos Humanos - Arriendos", "alquileres"),
]

render_sidebar_header()

modulos_permitidos = [clave for clave in current_user_modules() if clave in REGISTRO_MODULOS]

if not modulos_permitidos:
    st.error("Tu usuario no tiene ningun modulo asignado. Avisa al administrador del portal.")
    st.stop()

destinos = [destino for destino in DESTINOS if destino[0] in modulos_permitidos]
etiquetas = [etiqueta for _, etiqueta, _ in destinos]

with st.sidebar:
    st.markdown('<div class="side-nav-title">Sitio destino</div>', unsafe_allow_html=True)

    if len(etiquetas) == 1:
        etiqueta_activa = etiquetas[0]
        st.markdown(f'<div class="side-card">{etiqueta_activa}</div>', unsafe_allow_html=True)
    else:
        if st.session_state.get("destino_activo") not in etiquetas:
            st.session_state["destino_activo"] = etiquetas[0]
        etiqueta_activa = st.selectbox(
            "Sitio destino",
            etiquetas,
            key="destino_activo",
            label_visibility="collapsed",
        )

modulo_activo, _, proceso = next(
    destino for destino in destinos if destino[1] == etiqueta_activa
)
if proceso:
    st.session_state["rrhh_proceso"] = proceso
st.session_state["modulo_activo"] = modulo_activo

render_panel_lateral, render_pantalla = REGISTRO_MODULOS[modulo_activo]
render_panel_lateral()
render_pantalla()
