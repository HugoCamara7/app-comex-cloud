import base64
import html
import io
import re
from collections import defaultdict
from pathlib import Path

import pandas as pd
import pdfplumber
import streamlit as st


LOGO_PATH = Path("forus_logo_web.png")

SUFIJOS_MARCA = {
    "_CLB.pdf": "COLUMBIA",
    "_PRF.pdf": "PARFOIS",
    "_VNS.pdf": "VANS",
}

ALLOWED_AUTH_USERS = [
    "liliana.vitate@forus.pe",
    "danitza.cupe@forus.pe",
    "hugo.camara@forus.pe",
    "romulo.rasilla@forus.pe",
    "bi@forus.pe",
]




def format_file_size(size_bytes):
    if size_bytes is None:
        return "PDF"
    mb = size_bytes / (1024 * 1024)
    if mb >= 1:
        return f"{mb:.1f} MB"
    return f"{size_bytes / 1024:.0f} KB"

def image_to_base64(path):
    if not path.exists():
        return None
    return base64.b64encode(path.read_bytes()).decode("utf-8")


def clean_composition_text(value):
    text = clean_text(value)
    if not text:
        return None
    text = re.sub(r"\s+\bFOOTWEAR\b\s*$", "", text, flags=re.I)
    text = re.sub(r"\bMade\s+in\s*:?\s*[A-Za-z ]+", "", text, flags=re.I)
    text = re.sub(r"\s+", " ", text).strip(" :-;|")
    return text or None


def get_auth_passwords():
    try:
        configured = dict(st.secrets.get("auth", {}))
    except Exception:
        configured = {}
    return {email: str(configured.get(email, "")) for email in ALLOWED_AUTH_USERS}


def is_authenticated():
    return bool(st.session_state.get("auth_ok")) and st.session_state.get("auth_user") in ALLOWED_AUTH_USERS


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
                <div class="login-title">Lectura PDF Forus - Comex</div>
                <div class="login-subtitle">Sistema de lectura de documentos</div>
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
            Lectura de documentos Comex Forus
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
            st.session_state["auth_ok"] = True
            st.session_state["auth_user"] = email
            st.rerun()
        st.error("Correo o contrasena incorrectos.")

    st.stop()


def require_login():
    if not is_authenticated():
        render_login_screen()

def get_brand_from_filename(filename):
    upper_name = filename.upper()
    for suffix, brand in SUFIJOS_MARCA.items():
        if upper_name.endswith(suffix.upper()):
            return brand
    return None

DETAIL_COLUMNS = [
    "Start Page",
    "Invoice #",
    "Order No",
    "Brand",
    "Style",
    "Style Description",
    "Composition",
    "Color",
    "Color Description",
    "Size",
    "Quantity Shipped",
    "Base Price",
    "Net Price",
    "Cartons",
    "HS",
    "Made in",
    "Customer PO",
    "Invoice Date",
    "UM",
    "Unit Discount",
    "Extended Price",
    "Invoice Total USD",
    "Invoice Pages",
]

SUMMARY_COLUMNS = ["Metric", "Value"]

INVOICE_COLUMNS = [
    "Invoice #",
    "Start Page",
    "Invoice Pages",
    "Order No",
    "Customer PO",
    "Brand",
    "Style",
    "Style Description",
    "Colors",
    "Item Rows",
    "Total Quantity Shipped",
    "Cartons",
    "Invoice Total USD",
]

AUDIT_COLUMNS = ["Page", "Role", "Invoice #"]


def parse_money(value):
    if value is None:
        return None

    text = str(value).strip().replace(" ", "")
    if not text:
        return None

    is_negative = text.endswith("-")
    if is_negative:
        text = text[:-1]

    if "," in text and "." in text:
        text = text.replace(".", "").replace(",", ".")
    elif "," in text:
        text = text.replace(",", ".")
    elif re.fullmatch(r"\d{1,3}(\.\d{3})+", text):
        text = text.replace(".", "")

    try:
        number = float(text)
    except ValueError:
        return None

    return -number if is_negative else number


def parse_quantity(value):
    if value is None:
        return None

    text = str(value).strip().replace(" ", "")
    if not text:
        return None

    # En cantidades, 1.300 significa 1300.
    text = text.replace(".", "").replace(",", "")

    try:
        return int(text)
    except ValueError:
        return None

def extract_cartons_value(text):
    if not text:
        return None

    patterns = [
        r"Cartons:\s*([\d.,]+)",
        r"Total\s+Number\s+of\s+Cartons\s+.*?(\d{1,6})",
        r"N(?:ú|u|Ãº)mero\s+total\s+de\s+cajas\s*:?\s*([\d.,]+)",
        r"total\s+de\s+cajas\s*:?\s*([\d.,]+)",
    ]

    for pattern in patterns:
        match = re.search(pattern, text, flags=re.I | re.S)
        if match:
            cartons = parse_quantity(match.group(1))
            if cartons is not None:
                return cartons

    return None

def clean_text(value):
    if value is None:
        return None
    text = str(value).strip()
    return text if text else None


def extract_header_invoice(text):
    match = re.search(r"PERUFORUS S\.A\.\s+(\d{8,12})\s+(.+?)\s+PERUFORUS S\.A\.", text)
    return match.group(1) if match else None


def extract_footer_invoice(text):
    match = re.search(r"Invoice #:\s*(\d{8,12})\s+Page\s+\d+\s+of\s+\d+", text)
    return match.group(1) if match else None


def extract_invoice_pages(text):
    matches = re.findall(r"Invoice #:\s*\d{8,12}\s+Page\s+\d+\s+of\s+(\d+)", text)
    if not matches:
        return 1
    return max(parse_quantity(value) or 1 for value in matches)


def classify_page(text, current_invoice):
    if "COMMERCIAL INVOICE" in text:
        invoice = extract_footer_invoice(text) or extract_header_invoice(text)
        return "invoice_start", invoice

    if "Packing List" in text:
        return "packing_list", current_invoice

    footer_invoice = extract_footer_invoice(text)
    if footer_invoice:
        return "invoice_continuation", footer_invoice

    if "BILL OF LADING" in text.upper():
        return "bill_of_lading", None

    return "other", current_invoice


def extract_header_fields(text):
    invoice_number = extract_footer_invoice(text) or extract_header_invoice(text)

    header = re.search(
        r"PERUFORUS S\.A\.\s+(\d{8,12})\s+(.+?)\s+PERUFORUS S\.A\.",
        text,
    )
    customer_po = clean_text(header.group(2)) if header else None

    invoice_date = None
    date_matches = re.findall(r"\b\d{2}\.\d{2}\.\d{4}\b", text)
    if date_matches:
        invoice_date = date_matches[0]

    order_style = re.search(
        r"^(\d+/\d+)\s+([A-Z]{2,5})\s+(\d+)\s+(.+)$",
        text,
        flags=re.MULTILINE,
    )

    order_no = clean_text(order_style.group(1)) if order_style else None
    brand = clean_text(order_style.group(2)) if order_style else "COL"
    style = clean_text(order_style.group(3)) if order_style else None
    style_desc = clean_text(order_style.group(4)) if order_style else None

    total_qty = None
    m_total_qty = re.search(r"Total Quantity Shipped:\s*([\d.,]+)", text)
    if m_total_qty:
        total_qty = parse_quantity(m_total_qty.group(1))

    cartons = extract_cartons_value(text)

    invoice_total = None
    m_total = re.search(r"Invoice Total\s+([A-Z]{3}):\s*([\d.,]+)", text)
    if m_total:
        invoice_total = parse_money(m_total.group(2))

    return {
        "invoice_number": invoice_number,
        "invoice_date": invoice_date,
        "order_no": order_no,
        "customer_po": customer_po,
        "brand": brand,
        "style": style,
        "style_desc": style_desc,
        "total_quantity": total_qty,
        "cartons": cartons,
        "invoice_total": invoice_total,
        "invoice_pages": extract_invoice_pages(text),
    }


def should_skip_continuation_line(line):
    skipped_prefixes = (
        "Invoice #:",
        "Continued",
        "(FRM.",
        "COMMERCIAL INVOICE",
        "Subject to terms",
        "A finance charge",
        "Comments:",
        "Cartons:",
        "Columbia Brands",
    )
    return line.startswith(skipped_prefixes)


def parse_color_header(line):
    marker = "Color Color Description Size/Dim"
    if marker not in line:
        return None

    tail = line.split(marker, 1)[1].strip()
    tokens = tail.split()
    if len(tokens) < 7:
        return None

    return {
        "sizes": tokens[:-6],
        "um": tokens[-5],
        "base_price": parse_money(tokens[-4]),
        "unit_discount": parse_money(tokens[-3]),
        "net_price": parse_money(tokens[-2]),
        "extended_price": parse_money(tokens[-1]),
    }


def parse_color_qty_line(line):
    match = re.match(r"^(\S+)\s+(.+?)\s+Qty\s+(.+)$", line)
    if not match:
        return None

    qty_tokens = [
        token for token in match.group(3).split()
        if re.fullmatch(r"[\d.,]+", token)
    ]

    return {
        "color": match.group(1),
        "color_description": clean_text(match.group(2)),
        "quantities": [parse_quantity(token) for token in qty_tokens],
    }


def parse_hs_origin(line):
    match = re.search(
        r"HS:\s*([0-9]+)\s*(.*?)(?:\bFOOTWEAR\b\s*)?Made\s+in:\s*([A-Za-z ]+)",
        line,
        flags=re.I,
    )
    if not match:
        return None, None, None
    hs_code = match.group(1)
    composition_text = clean_composition_text(match.group(2))
    made_in = clean_text(match.group(3))
    return hs_code, made_in, composition_text


def extract_items_from_invoice_text(text, header, start_page):
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    items = []
    i = 0

    while i < len(lines):
        color_header = parse_color_header(lines[i])
        if not color_header:
            i += 1
            continue

        if i + 1 >= len(lines):
            break

        qty_info = parse_color_qty_line(lines[i + 1])
        if not qty_info:
            i += 1
            continue

        i += 2
        extra_description = []
        hs = None
        made_in = None
        composition = None

        while i < len(lines):
            line = lines[i]
            if line.startswith("Color Color Description Size/Dim"):
                break

            found_hs, found_origin, found_composition = parse_hs_origin(line)
            if found_hs:
                hs = found_hs
                made_in = found_origin
                composition = found_composition
                i += 1
                break

            if not should_skip_continuation_line(line):
                extra_description.append(line)

            i += 1

        if extra_description:
            qty_info["color_description"] = clean_text(
                qty_info["color_description"] + " " + " ".join(extra_description)
            )

        sizes = color_header["sizes"]
        quantities = qty_info["quantities"]

        for idx, size in enumerate(sizes):
            quantity = quantities[idx] if idx < len(quantities) else None
            items.append({
                "Start Page": start_page,
                "Invoice #": header["invoice_number"],
                "Order No": header["order_no"],
                "Brand": header["brand"] or "COL",
                "Style": header["style"],
                "Style Description": header["style_desc"],
                "Composition": composition,
                "Color": qty_info["color"],
                "Color Description": qty_info["color_description"],
                "Size": size,
                "Quantity Shipped": quantity,
                "Base Price": color_header["base_price"],
                "Net Price": color_header["net_price"],
                "Cartons": 0,
                "HS": hs,
                "Made in": made_in,
                "Customer PO": header["customer_po"],
                "Invoice Date": header["invoice_date"],
                "UM": color_header["um"],
                "Unit Discount": color_header["unit_discount"],
                "Extended Price": color_header["extended_price"],
                "Invoice Total USD": header["invoice_total"],
                "Invoice Pages": header["invoice_pages"],
            })

    if items and header["cartons"] is not None:
        items[0]["Cartons"] = header["cartons"]

    return items


def build_invoice_summary(detail_rows):
    grouped = defaultdict(list)
    for row in detail_rows:
        grouped[row["Invoice #"]].append(row)

    rows = []
    for invoice_number, invoice_rows in grouped.items():
        first = invoice_rows[0]
        rows.append({
            "Invoice #": invoice_number,
            "Start Page": first["Start Page"],
            "Invoice Pages": first["Invoice Pages"],
            "Order No": first["Order No"],
            "Customer PO": first["Customer PO"],
            "Brand": first["Brand"],
            "Style": first["Style"],
            "Style Description": first["Style Description"],
            "Colors": len({row["Color"] for row in invoice_rows if row["Color"]}),
            "Item Rows": len(invoice_rows),
            "Total Quantity Shipped": sum(row["Quantity Shipped"] or 0 for row in invoice_rows),
            "Cartons": sum(row["Cartons"] or 0 for row in invoice_rows),
            "Invoice Total USD": first["Invoice Total USD"],
        })

    return rows


def split_lines(value):
    if value is None:
        return []
    return [line.strip() for line in str(value).splitlines() if line.strip()]


def extract_vans_invoice_pages(text):
    matches = re.findall(r"Page\s+\d+\s+of\s+(\d+)", text)
    if not matches:
        return 1
    return max(parse_quantity(value) or 1 for value in matches)


def parse_vans_header(text, tables):
    invoice_number = None
    invoice_date = None
    customer_po = None

    if tables:
        for row in tables[0]:
            for cell in row:
                lines = split_lines(cell)
                if not lines:
                    continue

                label = lines[0].lower()
                value = clean_text(lines[1]) if len(lines) > 1 else None

                if label == "invoice number":
                    invoice_number = value
                elif label == "invoice date":
                    invoice_date = value
                elif label == "purchase order #":
                    customer_po = value

    sales_order = None
    shipment_reference = None
    m_ref = re.search(r"Sales Order #\s+Shipment Reference #\s+PE\s+(\S+)\s+(\S+)", text)
    if m_ref:
        sales_order = m_ref.group(1)
        shipment_reference = m_ref.group(2)

    cartons = extract_cartons_value(text)

    total_qty = None
    invoice_total = None
    m_total = re.search(r"Total Quantity:\s*([\d.,]+)\s+([\d.,]+)", text)
    if m_total:
        total_qty = parse_quantity(m_total.group(1))
        invoice_total = parse_money(m_total.group(2))

    return {
        "invoice_number": invoice_number,
        "invoice_date": invoice_date,
        "order_no": sales_order,
        "customer_po": customer_po,
        "brand": "VANS",
        "cartons": cartons,
        "total_quantity": total_qty,
        "invoice_total": invoice_total,
        "shipment_reference": shipment_reference,
        "invoice_pages": extract_vans_invoice_pages(text),
    }


def parse_vans_items_from_tables(tables, header, start_page):
    items = []

    for table in tables:
        if not table or len(table) < 2:
            continue

        table_header = [str(cell or "").replace("\n", " ").strip() for cell in table[0]]
        if "HS CODE" not in table_header:
            continue

        row = table[1]
        if len(row) < 9:
            continue

        hs_values = split_lines(row[0])
        origin_values = split_lines(row[1])
        style_values = split_lines(row[2])
        style_name_lines = split_lines(row[3])
        color_values = split_lines(row[4])
        size_values = split_lines(row[5])
        qty_values = split_lines(row[6])
        unit_price_values = split_lines(row[7])
        amount_values = split_lines(row[8])

        composition_index = next(
            (
                idx for idx, header_name in enumerate(table_header)
                if "COMPOSITION" in header_name.upper() or "MATERIAL" in header_name.upper()
            ),
            None,
        )
        composition_values = split_lines(row[composition_index]) if composition_index is not None and composition_index < len(row) else []

        count = max(
            len(hs_values),
            len(origin_values),
            len(style_values),
            len(color_values),
            len(size_values),
            len(qty_values),
            len(unit_price_values),
            len(amount_values),
        )

        style_name_chunks = []
        if count and style_name_lines:
            chunk_size = max(1, len(style_name_lines) // count)
            for idx in range(count):
                chunk = style_name_lines[idx * chunk_size:(idx + 1) * chunk_size]
                style_name_chunks.append(chunk[0] if chunk else None)

        for idx in range(count):
            unit_price = parse_money(unit_price_values[idx]) if idx < len(unit_price_values) else None
            amount = parse_money(amount_values[idx]) if idx < len(amount_values) else None
            items.append({
                "Start Page": start_page,
                "Invoice #": header["invoice_number"],
                "Order No": header["order_no"],
                "Brand": "VANS",
                "Style": style_values[idx] if idx < len(style_values) else None,
                "Style Description": style_name_chunks[idx] if idx < len(style_name_chunks) else None,
                "Composition": clean_composition_text(composition_values[idx]) if idx < len(composition_values) else None,
                "Color": color_values[idx] if idx < len(color_values) else None,
                "Color Description": color_values[idx] if idx < len(color_values) else None,
                "Size": size_values[idx] if idx < len(size_values) else None,
                "Quantity Shipped": parse_quantity(qty_values[idx]) if idx < len(qty_values) else None,
                "Base Price": unit_price,
                "Net Price": unit_price,
                "Cartons": 0,
                "HS": hs_values[idx] if idx < len(hs_values) else None,
                "Made in": origin_values[idx] if idx < len(origin_values) else None,
                "Customer PO": header["customer_po"],
                "Invoice Date": header["invoice_date"],
                "UM": None,
                "Unit Discount": 0,
                "Extended Price": amount,
                "Invoice Total USD": header["invoice_total"],
                "Invoice Pages": header["invoice_pages"],
            })

    return items


def process_vans_pdf(uploaded_file):
    detail_rows = []
    audit_rows = []

    with pdfplumber.open(io.BytesIO(uploaded_file.getvalue())) as pdf:
        total_pages = len(pdf.pages)
        current_invoice = None
        invoice_first_row = {}

        for page_number, page in enumerate(pdf.pages, start=1):
            text = page.extract_text() or ""
            tables = page.extract_tables() or []
            header = parse_vans_header(text, tables)
            invoice_number = header["invoice_number"] or current_invoice
            current_invoice = invoice_number

            audit_rows.append({
                "PDF File": uploaded_file.name,
                "Page": page_number,
                "Role": "invoice_start" if invoice_number not in invoice_first_row else "invoice_continuation",
                "Invoice #": invoice_number,
            })

            if invoice_number not in invoice_first_row:
                invoice_first_row[invoice_number] = len(detail_rows)

            rows = parse_vans_items_from_tables(tables, header, page_number)
            for row in rows:
                row["PDF File"] = uploaded_file.name
            detail_rows.extend(rows)

            if header["cartons"] is not None and invoice_number in invoice_first_row:
                idx = invoice_first_row[invoice_number]
                if idx < len(detail_rows):
                    detail_rows[idx]["Cartons"] = header["cartons"]
                    detail_rows[idx]["Invoice Total USD"] = header["invoice_total"]

    invoice_rows = build_invoice_summary(detail_rows)
    for row in invoice_rows:
        row["PDF File"] = uploaded_file.name

    summary_rows = build_summary_rows(uploaded_file.name, total_pages, detail_rows, invoice_rows, audit_rows)
    return detail_rows, summary_rows, invoice_rows, audit_rows


def extract_parfois_pages(text):
    match = re.search(r"PÃ¡g\.:\s*\d+/\s*(\d+)", text)
    return parse_quantity(match.group(1)) if match else 1


def parse_parfois_header(first_text, full_text):
    invoice_number = None
    m_invoice = re.search(r"ConsolidaciÃ³n de Facturas\s+(.+)", first_text)
    if m_invoice:
        invoice_number = clean_text(m_invoice.group(1))

    invoice_date = None
    m_date = re.search(r"(\d{4}-\d{2}-\d{2})\s+45 Dias", first_text)
    if m_date:
        invoice_date = m_date.group(1)

    customer_po = None
    m_po = re.search(r"Outbound Booking Nr\.:\s*(\S+)", first_text)
    if m_po:
        customer_po = m_po.group(1)

    cartons = extract_cartons_value(full_text)

    invoice_total = None
    m_total = re.search(r"IMPORTE\s+Obs\.:\s+EUR\s+([\d.,]+)", full_text)
    if m_total:
        invoice_total = parse_money(m_total.group(1))

    return {
        "invoice_number": invoice_number,
        "invoice_date": invoice_date,
        "order_no": customer_po,
        "customer_po": customer_po,
        "brand": "PARFOIS",
        "cartons": cartons,
        "invoice_total": invoice_total,
        "invoice_pages": extract_parfois_pages(first_text),
    }


def parse_parfois_rows_from_table(table, header, start_page):
    rows = []
    for table_row in table:
        if not table_row or len(table_row) < 14:
            continue

        article = clean_text(table_row[0])
        if not article or article.startswith("CÃ³digo") or article.startswith("Outbound"):
            continue

        qty = parse_quantity(table_row[6])
        unit_price = parse_money(table_row[11])
        amount = parse_money(table_row[13])

        if qty is None or amount is None:
            continue

        rows.append({
            "Start Page": start_page,
            "Invoice #": header["invoice_number"],
            "Order No": header["order_no"],
            "Brand": "PARFOIS",
            "Style": article,
            "Style Description": clean_text(table_row[1]),
            "Composition": clean_text(table_row[3]),
            "Color": None,
            "Color Description": None,
            "Size": None,
            "Quantity Shipped": qty,
            "Base Price": unit_price,
            "Net Price": unit_price,
            "Cartons": 0,
            "HS": clean_text(table_row[2]),
            "Made in": clean_text(table_row[4]),
            "Customer PO": header["customer_po"],
            "Invoice Date": header["invoice_date"],
            "UM": None,
            "Unit Discount": parse_money(table_row[12]) or 0,
            "Extended Price": amount,
            "Invoice Total USD": header["invoice_total"],
            "Invoice Pages": header["invoice_pages"],
        })
    return rows


def process_parfois_pdf(uploaded_file):
    detail_rows = []
    audit_rows = []
    all_text = []
    page_tables = []

    with pdfplumber.open(io.BytesIO(uploaded_file.getvalue())) as pdf:
        total_pages = len(pdf.pages)

        for page_number, page in enumerate(pdf.pages, start=1):
            text = page.extract_text() or ""
            all_text.append(text)
            tables = page.extract_tables() or []
            page_tables.append((page_number, tables))
            audit_rows.append({
                "PDF File": uploaded_file.name,
                "Page": page_number,
                "Role": "invoice_start" if page_number == 1 else "invoice_continuation",
                "Invoice #": None,
            })

        header = parse_parfois_header(all_text[0] if all_text else "", "\n".join(all_text))

        for row in audit_rows:
            row["Invoice #"] = header["invoice_number"]

        for page_number, tables in page_tables:
            for table in tables:
                rows = parse_parfois_rows_from_table(table, header, page_number)
                for row in rows:
                    row["PDF File"] = uploaded_file.name
                detail_rows.extend(rows)

    if detail_rows and header["cartons"] is not None:
        detail_rows[0]["Cartons"] = header["cartons"]

    invoice_rows = build_invoice_summary(detail_rows)
    for row in invoice_rows:
        row["PDF File"] = uploaded_file.name

    summary_rows = build_summary_rows(uploaded_file.name, total_pages, detail_rows, invoice_rows, audit_rows)
    return detail_rows, summary_rows, invoice_rows, audit_rows


def build_summary_rows(pdf_name, total_pages, detail_rows, invoice_rows, audit_rows):
    return [
        {"Metric": "PDF File", "Value": pdf_name},
        {"Metric": "Total Pages", "Value": total_pages},
        {"Metric": "Total Invoices", "Value": len(invoice_rows)},
        {"Metric": "Invoice Pages", "Value": sum(row["Invoice Pages"] or 0 for row in invoice_rows)},
        {"Metric": "Packing List Pages", "Value": sum(1 for row in audit_rows if row["Role"] == "packing_list")},
        {"Metric": "Item Rows", "Value": len(detail_rows)},
        {"Metric": "Total Quantity Shipped", "Value": sum(row["Quantity Shipped"] or 0 for row in detail_rows)},
    ]


def process_columbia_pdf(uploaded_file):
    detail_rows = []
    audit_rows = []
    invoice_texts = []
    current_invoice = None

    with pdfplumber.open(io.BytesIO(uploaded_file.getvalue())) as pdf:
        total_pages = len(pdf.pages)

        for page_number, page in enumerate(pdf.pages, start=1):
            text = page.extract_text() or ""
            role, invoice_number = classify_page(text, current_invoice)

            if invoice_number:
                current_invoice = invoice_number

            audit_rows.append({
                "Page": page_number,
                "Role": role,
                "Invoice #": invoice_number,
            })

            if role == "invoice_start":
                header = extract_header_fields(text)
                invoice_texts.append({
                    "invoice_number": header["invoice_number"],
                    "start_page": page_number,
                    "text": text,
                })
                continue

            if role == "invoice_continuation" and invoice_texts:
                invoice_texts[-1]["text"] += "\n" + text

        for invoice_data in invoice_texts:
            header = extract_header_fields(invoice_data["text"])
            rows = extract_items_from_invoice_text(
                invoice_data["text"],
                header,
                invoice_data["start_page"],
            )
            for row in rows:
                row["PDF File"] = uploaded_file.name
            detail_rows.extend(rows)

    invoice_rows = build_invoice_summary(detail_rows)
    for row in invoice_rows:
        row["PDF File"] = uploaded_file.name

    for row in audit_rows:
        row["PDF File"] = uploaded_file.name

    summary_rows = build_summary_rows(uploaded_file.name, total_pages, detail_rows, invoice_rows, audit_rows)

    return detail_rows, summary_rows, invoice_rows, audit_rows


def process_pdf(uploaded_file):
    brand = get_brand_from_filename(uploaded_file.name)

    if brand == "VANS":
        return process_vans_pdf(uploaded_file)

    if brand == "PARFOIS":
        return process_parfois_pdf(uploaded_file)

    return process_columbia_pdf(uploaded_file)


def build_excel(files):
    all_detail = []
    all_summary = []
    all_invoices = []
    all_audit = []

    for uploaded_file in files:
        detail_rows, summary_rows, invoice_rows, audit_rows = process_pdf(uploaded_file)
        all_detail.extend(detail_rows)
        all_summary.extend(summary_rows)
        all_invoices.extend(invoice_rows)
        all_audit.extend(audit_rows)

    output = io.BytesIO()
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        pd.DataFrame(all_detail).reindex(columns=["PDF File"] + DETAIL_COLUMNS).to_excel(
            writer,
            index=False,
            sheet_name="Detalle",
        )
        pd.DataFrame(all_summary).reindex(columns=SUMMARY_COLUMNS).to_excel(
            writer,
            index=False,
            sheet_name="Resumen",
        )
        pd.DataFrame(all_invoices).reindex(columns=["PDF File"] + INVOICE_COLUMNS).to_excel(
            writer,
            index=False,
            sheet_name="Facturas",
        )
        pd.DataFrame(all_audit).reindex(columns=["PDF File"] + AUDIT_COLUMNS).to_excel(
            writer,
            index=False,
            sheet_name="Auditoria_Paginas",
        )

    output.seek(0)
    return output, all_detail, all_invoices


st.set_page_config(
    page_title="Lectura PDF Forus - Comex",
    page_icon="PDF",
    layout="wide",
)

st.markdown(
    """
    <style>
    :root {
        --forus-blue: #082477;
        --forus-blue-2: #0b48d8;
        --forus-cyan: #14a8e8;
        --ink: #061938;
        --muted: #526484;
        --line: #d5e2f3;
        --panel: #ffffff;
        --soft: #f3f7fc;
        --good: #16a765;
        --warn: #df9800;
    }

    .stApp {
        background:
            radial-gradient(circle at 86% 4%, rgba(20,168,232,0.13), transparent 28%),
            linear-gradient(135deg, #eef4fb 0%, #f8fbff 45%, #ffffff 100%);
        color: var(--ink);
    }


    header[data-testid="stHeader"],
    [data-testid="stToolbar"],
    [data-testid="stDecoration"],
    #MainMenu,
    footer {
        display: none !important;
        visibility: hidden !important;
    }
    .block-container {
        padding-top: 0.9rem;
        padding-bottom: 2.6rem;
        max-width: 1220px;
    }

    section[data-testid="stSidebar"] {
        background: linear-gradient(180deg, #eaf1f9 0%, #f6f9fd 100%);
        border-right: 1px solid #cfdbeb;
    }

    section[data-testid="stSidebar"] > div {
        padding-top: 0.75rem;
    }

    [data-testid="stSidebarHeader"],
    [data-testid="stSidebarCollapseButton"],
    button[title="Collapse sidebar"],
    button[aria-label="Close sidebar"],
    button[aria-label="Open sidebar"] {
        display: none !important;
    }

    section[data-testid="stSidebar"] {
        min-width: 260px !important;
        width: 260px !important;
    }

    .side-logo {
        background: #ffffff;
        border: 1px solid var(--line);
        border-radius: 8px;
        padding: 0.75rem 0.8rem;
        box-shadow: 0 16px 36px rgba(8,36,119,0.10);
        margin: 0 0 1.15rem;
    }

    .side-title {
        color: var(--ink);
        font-weight: 850;
        font-size: 0.84rem;
        margin: 0.95rem 0 0.45rem;
    }

    .side-logo img {
        display: block;
        width: 100%;
        max-height: 92px;
        object-fit: contain;
    }

    .side-card {
        background: #ffffff;
        border: 1px solid var(--line);
        border-radius: 8px;
        padding: 1rem;
        box-shadow: 0 14px 30px rgba(8,36,119,0.08);
        color: var(--forus-blue);
        font-weight: 850;
        line-height: 1.9;
        font-size: 0.82rem;
        margin-bottom: 1rem;
    }

    .side-note {
        background: #e8f8ef;
        color: #075329;
        border-radius: 8px;
        padding: 0.95rem;
        font-size: 0.86rem;
        line-height: 1.55;
        border: 1px solid #c6efd6;
    }

    .app-shell {
        display: flex;
        flex-direction: column;
        gap: 1.3rem;
    }

    .hero-card {
        background: rgba(255,255,255,0.93);
        border: 1px solid var(--line);
        border-radius: 8px;
        padding: 1.9rem 2rem;
        display: grid;
        grid-template-columns: 1fr 280px;
        gap: 1.4rem;
        align-items: center;
        box-shadow: 0 18px 50px rgba(8,36,119,0.07);
    }

    .eyebrow {
        color: #0077db;
        font-size: 0.72rem;
        font-weight: 850;
        letter-spacing: 0.42em;
        text-transform: uppercase;
        margin-bottom: 0.9rem;
    }

    .hero-card h1 {
        margin: 0;
        color: var(--ink);
        font-size: 2.05rem;
        line-height: 1.12;
        letter-spacing: 0;
    }

    .hero-card p {
        color: var(--muted);
        margin: 1rem 0 0;
        line-height: 1.65;
        font-size: 0.98rem;
    }

    .hero-tags {
        display: flex;
        flex-wrap: wrap;
        gap: 0.7rem;
        justify-content: flex-end;
        align-items: center;
    }

    .tag {
        border-radius: 999px;
        padding: 0.55rem 0.9rem;
        border: 1px solid #bcd8ff;
        background: #edf6ff;
        color: var(--forus-blue);
        font-weight: 850;
        font-size: 0.76rem;
    }

    .tag.green {
        border-color: #a6e7bf;
        background: #e9faef;
        color: #077a37;
    }

    .pdf-symbol {
        width: 92px;
        height: 92px;
        border-radius: 8px;
        background: linear-gradient(135deg, #ffffff, #e7f1ff);
        border: 1px solid var(--line);
        display: grid;
        place-items: center;
        box-shadow: 0 18px 42px rgba(8,36,119,0.12);
    }

    .pipeline {
        background: rgba(255,255,255,0.94);
        border: 1px solid var(--line);
        border-radius: 8px;
        padding: 1.1rem;
        display: grid;
        grid-template-columns: repeat(4, 1fr);
        gap: 0.85rem;
        box-shadow: 0 18px 45px rgba(8,36,119,0.07);
    }

    .step-card {
        border: 1px solid var(--line);
        background: #f9fbfe;
        border-radius: 8px;
        padding: 1rem;
        display: grid;
        grid-template-columns: 44px 1fr auto;
        gap: 0.85rem;
        align-items: center;
        min-height: 88px;
    }

    .step-card.active {
        background: #edf6ff;
        border-color: #8dbdff;
    }

    .step-card.ok {
        background: #f0fbf5;
        border-color: #aee8c2;
    }

    .step-card.warn {
        background: #fff8e9;
        border-color: #ffd37e;
    }

    .step-number {
        width: 40px;
        height: 40px;
        border-radius: 50%;
        background: #ffffff;
        color: #006fe8;
        display: grid;
        place-items: center;
        font-weight: 900;
        box-shadow: 0 10px 26px rgba(8,36,119,0.08);
    }

    .step-title {
        font-weight: 900;
        color: var(--ink);
        margin-bottom: 0.2rem;
    }

    .step-sub {
        color: var(--muted);
        font-size: 0.78rem;
    }

    .pill {
        border-radius: 999px;
        padding: 0.35rem 0.6rem;
        font-weight: 850;
        font-size: 0.72rem;
        border: 1px solid #a9caff;
        background: #edf4ff;
        color: #0754c8;
    }

    .pill.ok {
        border-color: #9ee2b7;
        background: #eaf9ef;
        color: #08743a;
    }

    .pill.warn {
        border-color: #ffc566;
        background: #fff3d3;
        color: #9a6500;
    }

    .work-card {
        background: rgba(255,255,255,0.94);
        border: 1px solid var(--line);
        border-radius: 8px;
        padding: 1.55rem;
        box-shadow: 0 18px 45px rgba(8,36,119,0.07);
        margin-bottom: 1.2rem;
    }

    .work-card h2, .work-card h3 {
        color: var(--ink);
        margin-top: 0;
        letter-spacing: 0;
    }

    .work-card p {
        color: var(--muted);
        line-height: 1.6;
    }

    .rules-grid {
        display: grid;
        grid-template-columns: repeat(3, 1fr);
        gap: 0.8rem;
        margin-top: 1.1rem;
    }

    .rule-chip {
        border-radius: 8px;
        border: 1px solid #acd0ff;
        background: #eef6ff;
        padding: 0.95rem 1rem;
    }

    .rule-chip b {
        color: var(--forus-blue);
        display: block;
        margin-bottom: 0.4rem;
    }

    .upload-wrap div[data-testid="stFileUploader"] {
        border: 1px dashed #9fc2f3;
        background: #fbfdff;
        border-radius: 8px;
        padding: 1.15rem;
    }

    div[data-testid="stFileUploader"] section {
        border: 0;
        background: transparent;
    }


    .upload-wrap div[data-testid="stFileUploader"] button {
        background: linear-gradient(90deg, #082477, #0b48d8) !important;
        color: transparent !important;
        border: 0 !important;
        border-radius: 8px !important;
        min-height: 42px;
        min-width: 155px;
        box-shadow: 0 12px 26px rgba(8,36,119,0.20);
        position: relative;
    }

    .upload-wrap div[data-testid="stFileUploader"] button::after {
        content: "Sube tu factura";
        color: #ffffff;
        font-weight: 850;
        font-size: 0.88rem;
        position: absolute;
        inset: 0;
        display: grid;
        place-items: center;
    }

    .upload-wrap div[data-testid="stFileUploader"] small,
    .upload-wrap div[data-testid="stFileUploader"] [data-testid="stFileUploaderDropzoneInstructions"] {
        color: var(--muted) !important;
    }
    .stButton button, .stDownloadButton button {
        background: linear-gradient(90deg, #082477, #0b48d8);
        color: #ffffff;
        border: 0;
        border-radius: 8px;
        padding: 0.72rem 1.15rem;
        font-weight: 850;
        box-shadow: 0 12px 26px rgba(8,36,119,0.20);
    }

    .stButton button:hover, .stDownloadButton button:hover {
        color: #ffffff;
        border: 0;
        filter: brightness(1.06);
    }

    .stButton button:disabled {
        opacity: 0.45;
        box-shadow: none;
    }

    div[data-testid="stMetric"] {
        background: #f7faff;
        border: 1px solid var(--line);
        border-radius: 8px;
        padding: 0.85rem;
    }

    .benefits {
        display: grid;
        grid-template-columns: repeat(3, 1fr);
        gap: 0.9rem;
        margin-top: 1.2rem;
    }

    .benefit {
        background: rgba(255,255,255,0.94);
        border: 1px solid var(--line);
        border-radius: 8px;
        padding: 1rem;
        box-shadow: 0 14px 34px rgba(8,36,119,0.06);
    }

    .benefit b {
        color: var(--ink);
    }

    .benefit p {
        margin: 0.35rem 0 0;
        color: var(--muted);
        font-size: 0.88rem;
        line-height: 1.5;
    }


    .section-head {
        display: flex;
        justify-content: space-between;
        align-items: center;
        gap: 1rem;
        margin-bottom: 1rem;
    }

    .section-head h3 {
        margin: 0;
    }

    .section-kicker {
        color: #0077db;
        font-size: 0.72rem;
        font-weight: 900;
        letter-spacing: 0.18em;
        text-transform: uppercase;
    }

    .stat-grid {
        display: grid;
        grid-template-columns: repeat(3, 1fr);
        gap: 0.85rem;
        margin: 0.9rem 0 1rem;
    }

    .stat-card {
        background: linear-gradient(145deg, #ffffff 0%, #f4f8ff 100%);
        border: 1px solid #cfe0f5;
        border-radius: 8px;
        padding: 1rem;
        min-height: 96px;
        display: flex;
        flex-direction: column;
        justify-content: space-between;
    }

    .stat-label {
        color: var(--muted);
        font-size: 0.8rem;
        font-weight: 750;
    }

    .stat-value {
        color: var(--ink);
        font-size: 2rem;
        font-weight: 900;
        line-height: 1;
    }

    .file-list {
        display: flex;
        flex-direction: column;
        gap: 0.65rem;
        margin-top: 0.9rem;
    }

    .file-row {
        display: grid;
        grid-template-columns: 42px 1fr auto auto;
        gap: 0.85rem;
        align-items: center;
        background: #ffffff;
        border: 1px solid #d7e4f3;
        border-radius: 8px;
        padding: 0.8rem 0.9rem;
        box-shadow: 0 10px 24px rgba(8,36,119,0.05);
    }

    .file-icon {
        width: 38px;
        height: 38px;
        border-radius: 8px;
        background: linear-gradient(135deg, #082477, #0b48d8);
        color: #ffffff;
        display: grid;
        place-items: center;
        font-weight: 900;
        font-size: 0.72rem;
    }

    .file-name {
        color: var(--ink);
        font-weight: 850;
        overflow-wrap: anywhere;
    }

    .file-meta {
        color: var(--muted);
        font-size: 0.78rem;
        margin-top: 0.15rem;
    }

    .brand-badge, .status-badge {
        border-radius: 999px;
        padding: 0.42rem 0.7rem;
        font-size: 0.72rem;
        font-weight: 900;
        white-space: nowrap;
    }

    .brand-badge {
        background: #edf6ff;
        border: 1px solid #b9d7ff;
        color: #0754c8;
    }

    .status-badge {
        background: #eaf9ef;
        border: 1px solid #a6e7bf;
        color: #08743a;
    }

    .empty-state {
        border: 1px dashed #bcd2ee;
        background: #f9fcff;
        border-radius: 8px;
        padding: 1.2rem;
        color: var(--muted);
    }

    .action-card {
        background: linear-gradient(145deg, #ffffff 0%, #f8fbff 100%);
        border: 1px solid var(--line);
        border-radius: 8px;
        padding: 1.2rem;
        margin-top: 0.7rem;
    }

    .result-banner {
        margin: 1rem 0;
        background: linear-gradient(90deg, #e7f8ee, #f3fff8);
        border: 1px solid #bcebd0;
        color: #076735;
        border-radius: 8px;
        padding: 1rem 1.1rem;
        font-weight: 850;
    }

    .result-grid {
        display: grid;
        grid-template-columns: repeat(2, 1fr);
        gap: 0.9rem;
        margin: 0.9rem 0 1rem;
    }

    .result-card {
        background: #ffffff;
        border: 1px solid #cfe0f5;
        border-radius: 8px;
        padding: 1rem;
        box-shadow: 0 12px 28px rgba(8,36,119,0.06);
    }

    .result-card span {
        color: var(--muted);
        font-size: 0.82rem;
        font-weight: 750;
    }

    .result-card strong {
        display: block;
        margin-top: 0.4rem;
        color: var(--ink);
        font-size: 2.15rem;
        line-height: 1;
    }




    .login-shell {
        width: 448px;
        max-width: 100%;
        margin: 0 auto;
        background: #ffffff;
        border-radius: 8px 8px 0 0;
        overflow: hidden;
        box-shadow: 0 26px 70px rgba(0,0,0,0.22);
    }

    .login-hero {
        background: linear-gradient(135deg, #2d6bff 0%, #0f53f5 100%);
        padding: 2rem 1.8rem 2.2rem;
        text-align: center;
        color: #ffffff;
    }

    .login-hero .login-logo {
        width: 180px;
        margin: 0 auto 1.55rem;
        background: #ffffff;
        border: 0;
        border-radius: 8px;
        padding: 0.55rem 0.8rem;
        box-shadow: none;
    }

    .login-hero .login-logo img {
        display: block;
        width: 100%;
        height: auto;
    }

    .login-title {
        font-size: 1.75rem;
        font-weight: 950;
        letter-spacing: 0;
        line-height: 1.16;
    }

    .login-subtitle {
        margin-top: 0.75rem;
        font-size: 0.95rem;
        font-weight: 850;
        color: #eaf2ff;
    }

    .login-footer {
        width: 448px;
        max-width: 100%;
        margin: 1.6rem auto 0;
        text-align: center;
        color: #eaf2ff;
        font-weight: 850;
        font-size: 0.82rem;
        line-height: 1.8;
    }

    .login-wrap {
        min-height: 72vh;
        display: grid;
        place-items: center;
        padding: 2rem 1rem 0;
    }

    .login-card {
        width: min(520px, 100%);
        background: rgba(255,255,255,0.96);
        border: 1px solid var(--line);
        border-radius: 8px;
        padding: 2rem;
        box-shadow: 0 28px 70px rgba(8,36,119,0.12);
        text-align: left;
    }

    .login-logo {
        width: 220px;
        background: #ffffff;
        border: 1px solid var(--line);
        border-radius: 8px;
        padding: 0.75rem;
        margin-bottom: 1.4rem;
        box-shadow: 0 12px 26px rgba(8,36,119,0.08);
    }

    .login-logo img {
        width: 100%;
        display: block;
    }

    .login-logo-text {
        color: var(--forus-blue);
        font-size: 2rem;
        font-weight: 900;
        letter-spacing: 0.08em;
    }

    .login-logo-sub {
        color: var(--forus-blue);
        font-size: 0.58rem;
        letter-spacing: 0.28em;
    }

    .login-card h1 {
        margin: 0.35rem 0 0.6rem;
        color: var(--ink);
        font-size: 2rem;
        letter-spacing: 0;
    }

    .login-card p {
        color: var(--muted);
        line-height: 1.6;
        margin-bottom: 0;
    }

    div[data-testid="stForm"] {
        max-width: 520px;
        margin: -1rem auto 0;
        background: #ffffff;
        border: 1px solid var(--line);
        border-radius: 8px;
        padding: 1.4rem;
        box-shadow: 0 18px 45px rgba(8,36,119,0.08);
    }

    div[data-testid="stForm"] button {
        width: 100%;
        background: linear-gradient(90deg, #082477, #0b48d8) !important;
        color: #ffffff !important;
        border: 0 !important;
        border-radius: 8px !important;
        min-height: 44px;
        font-weight: 900;
    }

    .user-chip {
        background: #eef6ff;
        border: 1px solid #bdd7ff;
        color: var(--forus-blue);
        border-radius: 8px;
        padding: 0.7rem 0.8rem;
        font-size: 0.78rem;
        font-weight: 850;
        overflow-wrap: anywhere;
        margin-bottom: 0.8rem;
    }

    @media (max-width: 980px) {
        .hero-card, .pipeline, .rules-grid, .benefits, .stat-grid, .result-grid {
            grid-template-columns: 1fr;
        }
        .hero-tags {
            justify-content: flex-start;
        }
    }
    </style>
    """,
    unsafe_allow_html=True,
)

require_login()

with st.sidebar:
    logo_base64 = image_to_base64(LOGO_PATH)
    if logo_base64:
        st.markdown(
            f'''
            <div class="side-logo">
                <img src="data:image/png;base64,{logo_base64}" alt="Forus">
            </div>
            ''',
            unsafe_allow_html=True,
        )
    else:
        st.markdown(
            """
            <div class="side-logo">
                <div style="font-size:2rem;font-weight:900;color:#082477;letter-spacing:.08em">FORUS</div>
                <div style="font-size:.62rem;color:#082477;letter-spacing:.32em">CONSUMER FANATIC</div>
            </div>
            """,
            unsafe_allow_html=True,
        )

    st.markdown(f'<div class="user-chip">Sesion: {html.escape(st.session_state.get("auth_user", ""))}</div>', unsafe_allow_html=True)
    if st.button("Cerrar sesion"):
        st.session_state.pop("auth_ok", None)
        st.session_state.pop("auth_user", None)
        st.rerun()

    st.markdown('<div class="side-title">Sitio destino</div>', unsafe_allow_html=True)
    st.selectbox("Sitio destino", ["Comex Forus"], label_visibility="collapsed")

    st.markdown('<div class="side-title">Marca(s) permitidas</div>', unsafe_allow_html=True)
    st.markdown(
        """
        <div class="side-card">
            COLUMBIA / MOUNTAIN<br>
            VANS<br>
            PARFOIS
        </div>
        """,
        unsafe_allow_html=True,
    )

    st.markdown('<div class="side-title">Operacion</div>', unsafe_allow_html=True)
    st.markdown(
        """
        <div class="side-note">
            Nombres obligatorios:<br>
            <b>_CLB.pdf</b>, <b>_VNS.pdf</b>, <b>_PRF.pdf</b>
        </div>
        """,
        unsafe_allow_html=True,
    )

st.markdown('<div class="app-shell">', unsafe_allow_html=True)

st.markdown(
    """
    <div class="hero-card">
        <div>
            <div class="eyebrow">COMEX DOCUMENT CENTER</div>
            <h1>Lectura PDF Forus <span style="color:#8fb7f5">›</span> Excel consolidado</h1>
            <p>Sube facturas comerciales y genera un Excel ordenado con Detalle, Resumen, Facturas y Auditoria_Paginas.</p>
        </div>
        <div class="hero-tags">
            <span class="tag green">Especificaciones completas</span>
            <div class="pdf-symbol">
                <svg viewBox="0 0 96 96" width="72" height="72" aria-label="PDF">
                    <rect x="24" y="10" width="44" height="62" rx="7" fill="#ffffff" stroke="#bdd4f7"/>
                    <path d="M54 10h14v16H60c-4 0-6-3-6-6V10z" fill="#bfd5ff"/>
                    <rect x="32" y="32" width="28" height="4" rx="2" fill="#cad8ee"/>
                    <rect x="32" y="43" width="25" height="4" rx="2" fill="#cad8ee"/>
                    <rect x="12" y="52" width="47" height="25" rx="6" fill="#0b48d8"/>
                    <text x="35.5" y="70" text-anchor="middle" fill="#ffffff" font-size="15" font-weight="900" font-family="Arial">PDF</text>
                </svg>
            </div>
        </div>
    </div>
    """,
    unsafe_allow_html=True,
)

st.markdown(
    """
    <div class="pipeline">
        <div class="step-card active"><div class="step-number">1</div><div><div class="step-title">Input</div><div class="step-sub">Facturas PDF</div></div><span class="pill">Pend.</span></div>
        <div class="step-card ok"><div class="step-number">2</div><div><div class="step-title">Lectura</div><div class="step-sub">Reglas por marca</div></div><span class="pill ok">OK</span></div>
        <div class="step-card warn"><div class="step-number">3</div><div><div class="step-title">Validacion</div><div class="step-sub">Sufijo y estructura</div></div><span class="pill warn">Revisar</span></div>
        <div class="step-card"><div class="step-number">4</div><div><div class="step-title">Salida</div><div class="step-sub">Excel Comex</div></div><span class="pill">Pend.</span></div>
    </div>
    """,
    unsafe_allow_html=True,
)

st.markdown(
    """
    <div class="work-card">
        <h2>Preparar lectura de documentos</h2>
        <p>La app reconoce cada marca por el nombre del archivo y entrega siempre la misma estructura de salida.</p>
        <div class="rules-grid">
            <div class="rule-chip"><b>Columbia / Mountain</b>Archivos terminados en _CLB.pdf</div>
            <div class="rule-chip"><b>Parfois</b>Archivos terminados en _PRF.pdf</div>
            <div class="rule-chip"><b>Vans</b>Archivos terminados en _VNS.pdf</div>
        </div>
    </div>
    """,
    unsafe_allow_html=True,
)

st.markdown('<div class="work-card upload-wrap"><h3>1. Cargar facturas</h3>', unsafe_allow_html=True)
uploaded_files = st.file_uploader(
    "Subir PDFs",
    type=["pdf"],
    accept_multiple_files=True,
    label_visibility="collapsed",
)
st.markdown('</div>', unsafe_allow_html=True)

valid_files = []
invalid_files = []

st.markdown(
    '''
    <div class="work-card">
        <div class="section-head">
            <h3>2. Archivos cargados</h3>
            <div class="section-kicker">Control de entrada</div>
        </div>
    ''',
    unsafe_allow_html=True,
)

if uploaded_files:
    for file in uploaded_files:
        brand = get_brand_from_filename(file.name)
        if brand:
            valid_files.append(file)
        else:
            invalid_files.append(file.name)

    valid_count = len(valid_files)
    invalid_count = len(invalid_files)
    brand_count = len({get_brand_from_filename(file.name) for file in valid_files})
    st.markdown(
        f'''
        <div class="stat-grid">
            <div class="stat-card"><div class="stat-label">PDFs validos</div><div class="stat-value">{valid_count}</div></div>
            <div class="stat-card"><div class="stat-label">PDFs ignorados</div><div class="stat-value">{invalid_count}</div></div>
            <div class="stat-card"><div class="stat-label">Marcas detectadas</div><div class="stat-value">{brand_count}</div></div>
        </div>
        ''',
        unsafe_allow_html=True,
    )

    if invalid_files:
        st.warning("Estos archivos se ignoraran porque no terminan en _CLB.pdf, _PRF.pdf o _VNS.pdf:")
        st.write(invalid_files)

    if valid_files:
        file_rows = []
        for file in valid_files:
            brand = get_brand_from_filename(file.name)
            safe_name = html.escape(file.name)
            safe_brand = html.escape(brand or "")
            size_label = html.escape(format_file_size(getattr(file, "size", None)))
            file_rows.append(
                f'<div class="file-row">'
                f'<div class="file-icon">PDF</div>'
                f'<div><div class="file-name">{safe_name}</div>'
                f'<div class="file-meta">{size_label} · Factura comercial</div></div>'
                f'<div class="brand-badge">{safe_brand}</div>'
                f'<div class="status-badge">Listo</div>'
                f'</div>'
            )
        st.markdown('<div class="file-list">' + ''.join(file_rows) + '</div>', unsafe_allow_html=True)
else:
    st.markdown('<div class="empty-state">Carga tus archivos PDF para comenzar el proceso.</div>', unsafe_allow_html=True)

st.markdown('</div>', unsafe_allow_html=True)

st.markdown(
    '''
    <div class="work-card">
        <div class="section-head">
            <div>
                <h3>3. Procesar y generar Excel</h3>
                <p>Convierte tus PDFs en un Excel consolidado con las hojas Detalle, Resumen, Facturas y Auditoria_Paginas.</p>
            </div>
            <div class="section-kicker">Salida final</div>
        </div>

    ''',
    unsafe_allow_html=True,
)

if st.button("Procesar archivos", type="primary", disabled=not valid_files):
    with st.spinner("Procesando PDFs..."):
        excel_bytes, detail_rows, invoice_rows = build_excel(valid_files)

    st.markdown('<div class="result-banner">Excel generado correctamente. Ya puedes descargar la salida consolidada.</div>', unsafe_allow_html=True)
    st.markdown(
        f'''
        <div class="result-grid">
            <div class="result-card"><span>Facturas detectadas</span><strong>{len(invoice_rows)}</strong></div>
            <div class="result-card"><span>Filas de detalle</span><strong>{len(detail_rows)}</strong></div>
        </div>
        ''',
        unsafe_allow_html=True,
    )

    st.download_button(
        "Descargar Excel",
        data=excel_bytes,
        file_name="salida_comex_multi_marca.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )

st.markdown('</div>', unsafe_allow_html=True)

st.markdown(
    """
    <div class="benefits">
        <div class="benefit"><b>Control operativo</b><p>Valida marcas por sufijo y mantiene el flujo claro para Comex.</p></div>
        <div class="benefit"><b>Procesamiento rapido</b><p>Genera el Excel en minutos sin consumo de tokens de IA.</p></div>
        <div class="benefit"><b>Estructura consistente</b><p>Conserva columnas clave como Composition, Cartons, HS y Made in.</p></div>
    </div>
    """,
    unsafe_allow_html=True,
)

st.markdown('</div>', unsafe_allow_html=True)
