import io
import re
from collections import defaultdict
from pathlib import Path

import pandas as pd
import pdfplumber
import streamlit as st


LOGO_PATH = Path("logo_forus.png")

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

    cartons = None
    m_cartons = re.search(r"Cartons:\s*([\d.,]+)", text)
    if m_cartons:
        cartons = parse_quantity(m_cartons.group(1))

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
    match = re.search(r"HS:\s*([0-9]+).*?Made in:\s*([A-Za-z ]+)", line)
    if not match:
        return None, None
    return match.group(1), clean_text(match.group(2))


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

        while i < len(lines):
            line = lines[i]
            if line.startswith("Color Color Description Size/Dim"):
                break

            found_hs, found_origin = parse_hs_origin(line)
            if found_hs:
                hs = found_hs
                made_in = found_origin
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

    cartons = None
    m_cartons = re.search(r"Total Number of Cartons\s+.*?(\d{3,})", text, flags=re.S)
    if m_cartons:
        cartons = parse_quantity(m_cartons.group(1))

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
    match = re.search(r"Pág\.:\s*\d+/\s*(\d+)", text)
    return parse_quantity(match.group(1)) if match else 1


def parse_parfois_header(first_text, full_text):
    invoice_number = None
    m_invoice = re.search(r"Consolidación de Facturas\s+(.+)", first_text)
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

    cartons = None
    m_cartons = re.search(r"Número total de cajas:\s*([\d.,]+)", full_text)
    if m_cartons:
        cartons = parse_quantity(m_cartons.group(1))

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
        if not article or article.startswith("Código") or article.startswith("Outbound"):
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
        --forus-ink: #071832;
        --forus-muted: #60708d;
        --forus-border: #dbe5f4;
        --forus-soft: #f5f8fd;
    }

    .stApp {
        background: linear-gradient(135deg, #f7faff 0%, #ffffff 45%, #f3f7ff 100%);
    }

    .block-container {
        padding-top: 2rem;
        padding-bottom: 2.5rem;
        max-width: 1180px;
    }

    .hero {
        display: grid;
        grid-template-columns: 1fr 260px;
        gap: 2rem;
        align-items: center;
        margin-bottom: 1.4rem;
    }

    .hero h1 {
        margin: 0;
        color: var(--forus-ink);
        font-size: 2.35rem;
        line-height: 1.08;
        letter-spacing: 0;
    }

    .top-logo {
        margin-bottom: 1.2rem;
        display: flex;
        align-items: center;
    }

    .top-logo img {
        max-width: 230px;
        height: auto;
    }

    .top-logo-text {
        color: var(--forus-blue);
        font-size: 2.2rem;
        font-weight: 850;
        letter-spacing: 0.05em;
    }

    .top-logo-sub {
        color: var(--forus-blue);
        font-size: 0.75rem;
        letter-spacing: 0.34em;
        margin-top: 0.35rem;
    }

    .hero p {
        color: var(--forus-muted);
        font-size: 1.02rem;
        margin-top: 0.9rem;
    }

    .hero-art {
        min-height: 160px;
        border-radius: 24px;
        background:
            radial-gradient(circle at 78% 68%, rgba(40,119,255,0.28), transparent 28%),
            linear-gradient(145deg, #ffffff 0%, #eaf2ff 100%);
        border: 1px solid var(--forus-border);
        position: relative;
        box-shadow: 0 22px 50px rgba(8,36,119,0.10);
    }

    .hero-art::before {
        content: "PDF";
        position: absolute;
        left: 68px;
        top: 58px;
        color: white;
        background: linear-gradient(135deg, #0b48d8, #1d7cff);
        border-radius: 10px;
        padding: 0.65rem 1rem;
        font-size: 1.55rem;
        font-weight: 850;
        box-shadow: 0 16px 30px rgba(13,71,201,0.30);
    }

    .hero-art::after {
        content: "";
        position: absolute;
        right: 54px;
        top: 40px;
        width: 88px;
        height: 112px;
        border-radius: 14px;
        background: #ffffff;
        box-shadow: inset 0 -42px 0 #eef5ff, 0 18px 36px rgba(8,36,119,0.10);
    }

    .rule-box {
        background: linear-gradient(90deg, #eaf4ff 0%, #f7fbff 100%);
        border: 1px solid #d5e8ff;
        border-radius: 14px;
        padding: 1.1rem 1.25rem;
        color: var(--forus-ink);
        margin: 1rem 0 1.4rem;
    }

    .rule-box b {
        color: var(--forus-blue);
    }

    .panel {
        background: rgba(255,255,255,0.92);
        border: 1px solid var(--forus-border);
        border-radius: 18px;
        padding: 1.35rem;
        box-shadow: 0 18px 46px rgba(8,36,119,0.08);
        margin-bottom: 1.25rem;
    }

    .panel h3 {
        margin-top: 0;
        color: var(--forus-ink);
    }

    div[data-testid="stFileUploader"] {
        border: 1px dashed #bfd2ec;
        background: #fbfdff;
        border-radius: 16px;
        padding: 1.1rem;
    }

    div[data-testid="stFileUploader"] section {
        border: 0;
        background: transparent;
    }

    .stButton button, .stDownloadButton button {
        background: linear-gradient(90deg, #082477, #0b48d8);
        color: #ffffff;
        border: 0;
        border-radius: 11px;
        padding: 0.7rem 1.15rem;
        font-weight: 750;
        box-shadow: 0 12px 24px rgba(8,36,119,0.18);
    }

    .stButton button:hover, .stDownloadButton button:hover {
        color: #ffffff;
        border: 0;
        filter: brightness(1.05);
    }

    .benefits {
        display: grid;
        grid-template-columns: repeat(3, 1fr);
        gap: 1rem;
        margin-top: 1.2rem;
    }

    .benefit {
        background: #ffffff;
        border: 1px solid var(--forus-border);
        border-radius: 16px;
        padding: 1rem;
        box-shadow: 0 14px 36px rgba(8,36,119,0.06);
    }

    .benefit b {
        color: var(--forus-ink);
    }

    .benefit p {
        margin: 0.35rem 0 0;
        color: var(--forus-muted);
        font-size: 0.92rem;
    }

    @media (max-width: 900px) {
        .hero {
            grid-template-columns: 1fr;
        }
        .benefits {
            grid-template-columns: 1fr;
        }
    }
    </style>
    """,
    unsafe_allow_html=True,
)

if LOGO_PATH.exists():
    st.markdown('<div class="top-logo">', unsafe_allow_html=True)
    st.image(str(LOGO_PATH), width=160)
    st.markdown("</div>", unsafe_allow_html=True)
else:
    st.markdown(
        """
        <div class="top-logo">
            <div>
                <div class="top-logo-text">FORUS</div>
                <div class="top-logo-sub">CONSUMER FANATIC</div>
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

st.markdown(
    """
    <div class="hero">
        <div>
            <h1>Lectura PDF Forus - Comex</h1>
            <p>Sube facturas y descarga el Excel consolidado con la estructura requerida.</p>
        </div>
        <div class="hero-art"></div>
    </div>
    <div class="rule-box">
        <b>Nombres esperados:</b><br>
        Columbia/Mountain <b>_CLB.pdf</b> &nbsp;|&nbsp;
        Parfois <b>_PRF.pdf</b> &nbsp;|&nbsp;
        Vans <b>_VNS.pdf</b>
    </div>
    """,
    unsafe_allow_html=True,
)

uploaded_files = st.file_uploader(
    "Subir PDFs",
    type=["pdf"],
    accept_multiple_files=True,
)

valid_files = []
invalid_files = []

st.markdown('<div class="panel"><h3>Archivos cargados</h3>', unsafe_allow_html=True)

if uploaded_files:
    for file in uploaded_files:
        brand = get_brand_from_filename(file.name)
        if brand:
            valid_files.append(file)
        else:
            invalid_files.append(file.name)

    col1, col2 = st.columns(2)
    col1.metric("PDFs validos", len(valid_files))
    col2.metric("PDFs ignorados", len(invalid_files))

    if invalid_files:
        st.warning("Estos archivos se ignoraran porque no terminan en _CLB.pdf, _PRF.pdf o _VNS.pdf:")
        st.write(invalid_files)

    if valid_files:
        st.dataframe(
            [
                {
                    "PDF": file.name,
                    "Marca": get_brand_from_filename(file.name),
                    "Estado": "Listo para procesar",
                }
                for file in valid_files
            ],
            use_container_width=True,
            hide_index=True,
        )
else:
    st.write("Carga tus archivos PDF para comenzar el proceso.")

st.markdown("</div>", unsafe_allow_html=True)

st.markdown('<div class="panel"><h3>Procesar y generar Excel</h3><p>Convierte tus PDFs en un Excel consolidado con las hojas Detalle, Resumen, Facturas y Auditoria_Paginas.</p>', unsafe_allow_html=True)

if st.button("Procesar archivos", type="primary", disabled=not valid_files):
    with st.spinner("Procesando PDFs..."):
        excel_bytes, detail_rows, invoice_rows = build_excel(valid_files)

    st.success("Excel generado correctamente.")
    st.metric("Facturas", len(invoice_rows))
    st.metric("Filas detalle", len(detail_rows))

    st.download_button(
        "Descargar Excel",
        data=excel_bytes,
        file_name="salida_comex_multi_marca.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )

st.markdown("</div>", unsafe_allow_html=True)

st.markdown(
    """
    <div class="benefits">
        <div class="benefit"><b>Seguro y controlado</b><p>La lectura usa reglas de Python para las marcas implementadas.</p></div>
        <div class="benefit"><b>Procesamiento rapido</b><p>Genera el Excel en minutos sin consumo de tokens de IA.</p></div>
        <div class="benefit"><b>Estructura garantizada</b><p>Entrega siempre las mismas hojas y columnas de salida.</p></div>
    </div>
    """,
    unsafe_allow_html=True,
)
