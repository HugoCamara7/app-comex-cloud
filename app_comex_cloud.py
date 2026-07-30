"""Portal Forus: Comex, Contabilidad y Recursos Humanos en una sola aplicacion.

Este archivo es solo el arranque: configura la pagina, pide el acceso y decide
que modulo mostrar. La logica de cada area vive en modules/.
"""
import streamlit as st

from forus_auth import MODULOS, current_user_modules, require_login
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

render_sidebar_header()

modulos_permitidos = [clave for clave in current_user_modules() if clave in REGISTRO_MODULOS]

if not modulos_permitidos:
    st.error("Tu usuario no tiene ningun modulo asignado. Avisa al administrador del portal.")
    st.stop()

with st.sidebar:
    st.markdown('<div class="side-nav-title">Modulo</div>', unsafe_allow_html=True)

    if len(modulos_permitidos) == 1:
        modulo_activo = modulos_permitidos[0]
        st.markdown(
            f'<div class="side-card">{MODULOS[modulo_activo]}</div>',
            unsafe_allow_html=True,
        )
    else:
        if st.session_state.get("modulo_activo") not in modulos_permitidos:
            st.session_state["modulo_activo"] = modulos_permitidos[0]
        modulo_activo = st.radio(
            "Modulo",
            modulos_permitidos,
            format_func=lambda clave: MODULOS[clave],
            key="modulo_activo",
            label_visibility="collapsed",
        )

render_panel_lateral, render_pantalla = REGISTRO_MODULOS[modulo_activo]
render_panel_lateral()
render_pantalla()
