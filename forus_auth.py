"""Acceso al portal: usuarios autorizados, contrasenas y permisos por modulo."""
import streamlit as st

from forus_ui import LOGO_PATH, image_to_base64


ALLOWED_AUTH_USERS = [
    "liliana.vitate@forus.pe",
    "danitza.cupe@forus.pe",
    "hugo.camara@forus.pe",
    "romulo.rasilla@forus.pe",
    "bi@forus.pe",
]


# Modulos del portal, en el orden en que aparecen en el menu lateral.
MODULOS = {
    "comex": "Comex",
    "contabilidad": "Contabilidad",
    "rrhh": "Recursos Humanos",
}

# Si un usuario autorizado no figura en la seccion [modulos] de los secrets,
# conserva el acceso que ya tenia antes de esta version: solo Comex.
DEFAULT_MODULES = ["comex"]


def get_module_config():
    try:
        return dict(st.secrets.get("modulos", {}))
    except Exception:
        return {}


def get_user_modules(email):
    """Modulos permitidos para un correo, segun la seccion [modulos] de los secrets.

    Acepta "comex,rrhh", ["comex", "rrhh"] o "todos". Devuelve siempre claves
    validas y en el orden de MODULOS.
    """
    configurados = get_module_config()
    crudo = configurados.get(email)

    if crudo is None:
        permitidos = set(DEFAULT_MODULES)
    elif isinstance(crudo, str):
        texto = crudo.strip().lower()
        if texto in ("todos", "*", "all"):
            permitidos = set(MODULOS)
        else:
            permitidos = {parte.strip().lower() for parte in texto.replace(";", ",").split(",")}
    else:
        permitidos = {str(parte).strip().lower() for parte in crudo}

    return [clave for clave in MODULOS if clave in permitidos]

def get_auth_passwords():
    try:
        configured = dict(st.secrets.get("auth", {}))
    except Exception:
        configured = {}
    return {email: str(configured.get(email, "")) for email in ALLOWED_AUTH_USERS}


def is_authenticated():
    return bool(st.session_state.get("auth_ok")) and st.session_state.get("auth_user") in ALLOWED_AUTH_USERS


def current_user_modules():
    """Modulos permitidos para la sesion en curso."""
    if not is_authenticated():
        return []
    guardados = st.session_state.get("auth_modules")
    if guardados is None:
        guardados = get_user_modules(st.session_state["auth_user"])
        st.session_state["auth_modules"] = guardados
    return guardados

def render_login_screen():
    logo_base64 = image_to_base64(LOGO_PATH)
    logo_html = (
        f'<img src="data:image/png;base64,{logo_base64}" alt="Forus">'
        if logo_base64
        else '<div class="login-logo-text">FORUS</div><div class="login-logo-sub">CONSUMER FANATIC</div>'
    )
    st.markdown(
        f"""
        <style>
        .stApp {{
            background: #142238 !important;
        }}
        header[data-testid="stHeader"],
        [data-testid="stToolbar"],
        [data-testid="stDecoration"],
        section[data-testid="stSidebar"],
        #MainMenu,
        footer {{
            display: none !important;
            visibility: hidden !important;
        }}
        .block-container {{
            max-width: 560px !important;
            padding-top: 4.2rem !important;
            padding-bottom: 2rem !important;
        }}
        div[data-testid="stForm"] {{
            max-width: 448px !important;
            margin: 0 auto !important;
            border-radius: 8px !important;
            border: 1px solid #d8dde8 !important;
            padding: 1.25rem !important;
            box-shadow: none !important;
        }}
        div[data-testid="stForm"] label {{
            color: #081a35 !important;
            font-weight: 650 !important;
        }}
        div[data-testid="stForm"] input {{
            min-height: 42px !important;
        }}
        div[data-testid="stForm"] button {{
            width: auto !important;
            min-width: 92px !important;
            background: #0b4d88 !important;
            border-radius: 8px !important;
            box-shadow: none !important;
        }}
        </style>
        <div class="login-shell">
            <div class="login-hero">
                <div class="login-logo">{logo_html}</div>
                <div class="login-title">Lectura Documentos Forus</div>
                <div class="login-subtitle">Comex | Contabilidad | Recursos Humanos</div>
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    with st.form("login_form"):
        email = st.text_input("Correo electronico", placeholder="hugo.camara@forus.pe").strip().lower()
        password = st.text_input("Contrasena", type="password")
        submitted = st.form_submit_button("Ingresar")

    st.markdown(
        """
        <div class="login-footer">
            Sistema exclusivo para personal autorizado<br>
            Comex | Contabilidad | Recursos Humanos
        </div>
        """,
        unsafe_allow_html=True,
    )

    if submitted:
        auth_passwords = get_auth_passwords()
        if email not in ALLOWED_AUTH_USERS:
            st.error("Este correo no tiene acceso autorizado.")
            st.stop()
        expected_password = auth_passwords.get(email)
        if not expected_password:
            st.error("Falta configurar la contrasena de este usuario en Streamlit Secrets.")
            st.stop()
        if password == expected_password:
            modulos = get_user_modules(email)
            if not modulos:
                st.error("Este usuario no tiene ningun modulo asignado. Avisa al administrador.")
                st.stop()
            st.session_state["auth_ok"] = True
            st.session_state["auth_user"] = email
            st.session_state["auth_modules"] = modulos
            st.session_state["modulo_activo"] = modulos[0]
            st.rerun()
        st.error("Correo o contrasena incorrectos.")

    st.stop()


def require_login():
    if not is_authenticated():
        render_login_screen()
