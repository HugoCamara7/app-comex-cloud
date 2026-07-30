"""Modulo Comex: lectura de facturas comerciales de proveedor (Columbia, Vans, Parfois)."""
import html
import io
import re
from collections import defaultdict

import pandas as pd
import pdfplumber
import streamlit as st

from forus_parsing import (
    clean_composition_text,
    clean_text,
    parse_money,
    parse_quantity,
    split_lines,
)
from forus_ui import format_file_size


SUFIJOS_MARCA = {
    "_CLB.pdf": "COLUMBIA",
    "_PRF.pdf": "PARFOIS",
    "_VNS.pdf": "VANS",
}

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

def render_sidebar():
    """Panel lateral propio del modulo Comex."""
    with st.sidebar:
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


def render():
    """Pantalla completa del modulo Comex."""
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
            <p>El sistema identifica la marca por el nombre del archivo y conserva columnas clave como composici?n, cajas, HS y origen.</p>
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
