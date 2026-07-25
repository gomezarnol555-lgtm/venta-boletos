import random
from datetime import datetime

import pandas as pd
import streamlit as st
from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
from streamlit_gsheets import GSheetsConnection


def dibujar_fondo_autenticidad(canvas, doc):
    """Dibuja un fondo sutil tipo certificado para dar apariencia de autenticidad."""
    width, height = doc.pagesize

    canvas.saveState()

    # Fondo general
    canvas.setFillColor(colors.HexColor("#F8FAFC"))
    canvas.rect(0, 0, width, height, fill=1, stroke=0)

    # Marco exterior minimalista
    canvas.setStrokeColor(colors.HexColor("#CBD5E1"))
    canvas.setLineWidth(1)
    canvas.rect(24, 24, width - 48, height - 48, fill=0, stroke=1)

    # Patrón diagonal sutil
    canvas.setStrokeColor(colors.HexColor("#E2E8F0"))
    canvas.setLineWidth(0.35)
    step = 34
    limite = int(width + height)
    for x in range(-int(height), limite, step):
        canvas.line(x, 24, x + height, height - 24)

    # Sello de autenticidad
    sello_x = width - 78
    sello_y = height - 82
    canvas.setStrokeColor(colors.HexColor("#94A3B8"))
    canvas.circle(sello_x, sello_y, 30, stroke=1, fill=0)
    canvas.circle(sello_x, sello_y, 22, stroke=1, fill=0)
    canvas.setFont("Helvetica-Bold", 6.5)
    canvas.setFillColor(colors.HexColor("#64748B"))
    canvas.drawCentredString(sello_x, sello_y + 4, "BOLETO")
    canvas.drawCentredString(sello_x, sello_y - 4, "AUTÉNTICO")

    # Marca de agua suave
    canvas.saveState()
    canvas.translate(width * 0.55, height * 0.52)
    canvas.rotate(28)
    canvas.setFillColor(colors.HexColor("#E5E7EB"))
    canvas.setFont("Helvetica-Bold", 34)
    canvas.drawCentredString(0, 0, "AUTENTICIDAD")
    canvas.restoreState()

    canvas.restoreState()


def generar_pdf_boleto(datos_boleto):
    nombre_archivo = datos_boleto.get('Comprobante', f"Boleto_{datos_boleto['ID_Boleto']}.pdf")
    doc = SimpleDocTemplate(nombre_archivo, pagesize=letter, rightMargin=32, leftMargin=32, topMargin=40, bottomMargin=32)
    story = []

    styles = getSampleStyleSheet()
    estilo_titulo = ParagraphStyle(
        'TituloBoleto',
        parent=styles['Heading1'],
        fontSize=19,
        leading=23,
        textColor=colors.HexColor("#0F172A"),
        alignment=1,
        spaceAfter=8,
    )
    estilo_subtitulo = ParagraphStyle(
        'SubtituloBoleto',
        parent=styles['Normal'],
        fontSize=9.5,
        leading=12,
        textColor=colors.HexColor("#475569"),
        alignment=1,
    )
    estilo_normal = ParagraphStyle(
        'TextoBoleto',
        parent=styles['Normal'],
        fontSize=10.5,
        leading=13,
        textColor=colors.HexColor("#334155"),
    )
    estilo_pequeno = ParagraphStyle(
        'TextoPequeno',
        parent=styles['Normal'],
        fontSize=8.5,
        leading=10,
        textColor=colors.HexColor("#64748B"),
        alignment=1,
    )

    story.append(Spacer(1, 14))
    story.append(Paragraph("BOLETO OFICIAL", estilo_titulo))
    story.append(Paragraph("Diseño minimalista con fondo de autenticidad", estilo_subtitulo))
    story.append(Spacer(1, 16))

    data = [
        [Paragraph("<b>ID de Boleto:</b>", estilo_normal), Paragraph(str(datos_boleto['ID_Boleto']), estilo_normal)],
        [Paragraph("<b>Asistente:</b>", estilo_normal), Paragraph(datos_boleto['Nombre'], estilo_normal)],
        [Paragraph("<b>Correo:</b>", estilo_normal), Paragraph(datos_boleto['Correo'], estilo_normal)],
        [Paragraph("<b>Número telefónico:</b>", estilo_normal), Paragraph(str(datos_boleto['Numero_Telefonico']), estilo_normal)],
        [Paragraph("<b>Evento:</b>", estilo_normal), Paragraph(datos_boleto['Evento'], estilo_normal)],
        [Paragraph("<b>N° de Boleto:</b>", estilo_normal), Paragraph(str(datos_boleto['Numero_Boleto']), estilo_normal)],
        [Paragraph("<b>Precio Pagado:</b>", estilo_normal), Paragraph(f"${datos_boleto['Precio']:.2f} MXN", estilo_normal)],
        [Paragraph("<b>Método de Pago:</b>", estilo_normal), Paragraph(str(datos_boleto['Metodo_Pago']), estilo_normal)],
        [Paragraph("<b>Código de Pago:</b>", estilo_normal), Paragraph(str(datos_boleto['Codigo_Pago']), estilo_normal)],
        [Paragraph("<b>Fecha de Emisión:</b>", estilo_normal), Paragraph(str(datos_boleto['Fecha_Compra']), estilo_normal)],
    ]

    t = Table(data, colWidths=[165, 300])
    t.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, -1), colors.HexColor("#FFFFFF")),
        ('BOX', (0, 0), (-1, -1), 1, colors.HexColor("#CBD5E1")),
        ('INNERGRID', (0, 0), (-1, -1), 0.6, colors.HexColor("#E2E8F0")),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('LEFTPADDING', (0, 0), (-1, -1), 12),
        ('RIGHTPADDING', (0, 0), (-1, -1), 12),
        ('TOPPADDING', (0, 0), (-1, -1), 8),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 8),
        ('ROWBACKGROUNDS', (0, 0), (-1, -1), [colors.HexColor("#FFFFFF"), colors.HexColor("#F8FAFC")]),
    ]))

    story.append(t)
    story.append(Spacer(1, 18))

    resumen = (
        "<b>Verificación:</b> Este comprobante corresponde a un registro único y oficial de la rifa. "
        "Conserva este archivo para cualquier validación posterior."
    )
    story.append(Paragraph(resumen, estilo_normal))
    story.append(Spacer(1, 16))
    story.append(
        Paragraph(
            "Presenta este boleto para participar en la Rifa de celular. ¡Mucha suerte!",
            estilo_pequeno,
        )
    )

    doc.build(story, onFirstPage=dibujar_fondo_autenticidad, onLaterPages=dibujar_fondo_autenticidad)
    return nombre_archivo


def obtener_datos_gsheets():
    """Obtiene el inventario y ventas desde Google Sheets con manejo de errores."""
    try:
        conn = st.connection("gsheets", type=GSheetsConnection)
        df_ventas = conn.read(worksheet="Ventas", ttl=0)

        if df_ventas is not None and not df_ventas.empty:
            df_ventas = df_ventas.dropna(how="all")
        else:
            df_ventas = pd.DataFrame(columns=[
                "ID_Boleto",
                "Nombre",
                "Correo",
                "Evento",
                "Numero_Boleto",
                "Precio",
                "Metodo_Pago",
                "Codigo_Pago",
                "Fecha_Compra",
                "Numero_Telefonico",
            ])

        return conn, df_ventas
    except Exception as e:
        st.error(f"⚠️ Error al conectar con Google Sheets: {e}")
        return None, None


def main():
    st.set_page_config(page_title="Rifa de Celular", page_icon="📱", layout="wide")
    st.title("📱 Sistema de Registro y Venta - Rifa de Celular")

    conn, df_ventas = obtener_datos_gsheets()
    if conn is None or df_ventas is None:
        return

    # Inicializar session_state para control de widgets, selección y persistencia de descarga
    if "form_id" not in st.session_state:
        st.session_state.form_id = 0
    if "selected_ticket" not in st.session_state:
        st.session_state.selected_ticket = None
    if "generated_pdf_bytes" not in st.session_state:
        st.session_state.generated_pdf_bytes = None
    if "generated_pdf_name" not in st.session_state:
        st.session_state.generated_pdf_name = None
    if "success_message" not in st.session_state:
        st.session_state.success_message = None

    # --- CONFIGURACIÓN DE 100 BOLETOS (000 al 099) ---
    precio_base = 100.00

    boletos_vendidos = []
    if not df_ventas.empty and "Numero_Boleto" in df_ventas.columns:
        boletos_vendidos = [f"{int(x):03d}" for x in df_ventas["Numero_Boleto"].dropna().values]

    # Construir mapa de disponibilidad general
    matriz_boletos = []
    for i in range(100):
        num_str = f"{i:03d}"
        esta_vendido = num_str in boletos_vendidos
        estado_label = f"{num_str} ❌" if esta_vendido else f"{num_str} ✅"
        matriz_boletos.append(estado_label)

    columnas_unicas = [f"{' ' * i}" for i in range(10)]
    filas_grid = [matriz_boletos[i:i + 10] for i in range(0, 100, 10)]
    df_grid = pd.DataFrame(filas_grid, columns=columnas_unicas)

    col1, col2 = st.columns([1.2, 1])

    with col1:
        st.markdown("### Disponibilidad")
        st.write("Estado actual de todos los 100 boletos de la rifa:")
        st.dataframe(df_grid, use_container_width=True, height=450, hide_index=True)
        st.caption("✅ = Disponible | ❌ = Vendido / No disponible")

    with col2:
        st.markdown("### 🛒 Registro de Compra")

        # Mostrar panel de descarga persistente si hay un boleto recién creado
        if st.session_state.success_message:
            st.success(st.session_state.success_message)
            st.download_button(
                label="📥 Descargar Comprobante PDF del Boleto",
                data=st.session_state.generated_pdf_bytes,
                file_name=st.session_state.generated_pdf_name,
                mime="application/pdf",
                key="btn_descarga_persistente"
            )
            if st.button("✖️ Ocultar aviso y continuar"):
                st.session_state.generated_pdf_bytes = None
                st.session_state.generated_pdf_name = None
                st.session_state.success_message = None
                st.rerun()
            st.markdown("---")

        boletos_libres_count = 100 - len(boletos_vendidos)
        if boletos_libres_count <= 0:
            st.warning("⚠️ ¡Lo sentimos! Todos los boletos de la rifa han sido vendidos.")
            return

        # Widgets con clave dinámica basada en form_id para vaciarse automáticamente
        nombre = st.text_input("Nombre completo:", key=f"nombre_{st.session_state.form_id}")
        correo = st.text_input("Correo electrónico:", key=f"correo_{st.session_state.form_id}")
        telefono = st.text_input("Número telefónico:", key=f"telefono_{st.session_state.form_id}")
        evento = st.text_input("Evento:", value="Rifa de celular", disabled=True, key=f"evento_{st.session_state.form_id}")

        selected_ticket = st.session_state.selected_ticket

        with st.expander("🎫 Desplegar tabla para seleccionar número de boleto", expanded=(selected_ticket is None)):
            st.markdown("Los boletos no disponibles aparecen en color más claro y no se pueden seleccionar:")
            for fila in range(10):
                cols_btn = st.columns(10)
                for col_idx in range(10):
                    num = fila * 10 + col_idx
                    num_str = f"{num:03d}"
                    vendido = num_str in boletos_vendidos

                    with cols_btn[col_idx]:
                        if vendido:
                            st.button(f"{num_str}", key=f"sel_{num_str}", disabled=True, use_container_width=True)
                        else:
                            is_current = (selected_ticket == num_str)
                            label = f"[{num_str}]" if is_current else f"{num_str}"
                            if st.button(label, key=f"sel_{num_str}", use_container_width=True):
                                st.session_state.selected_ticket = num_str
                                st.rerun()

        if selected_ticket:
            st.success(f"🎯 Boleto seleccionado: **N° {selected_ticket}**")
        else:
            st.info("👆 Haz clic en el recuadro superior para desplegar y seleccionar tu número.")

        st.write(f"**Monto a pagar:** ${precio_base:.2f} MXN")

        metodo_pago = st.radio("Método de Pago:", ["Transferencia", "Tarjeta"], key=f"pago_{st.session_state.form_id}")
        pago_realizado = st.checkbox("Confirmo que el pago ha sido efectuado correctamente.", key=f"chk_{st.session_state.form_id}")

        submit_compra = st.button("💳 Registrar Compra y Generar Boleto")

        if submit_compra:
            if not selected_ticket:
                st.error("⚠️ Debes seleccionar un número de boleto disponible.")
            elif not nombre or not correo or not telefono:
                st.error("⚠️ Por favor completa tu nombre, correo y número telefónico antes de continuar. Tus datos están a salvo.")
            elif not pago_realizado:
                st.warning("⚠️ Debes marcar la casilla para confirmar que el pago fue realizado.")
            else:
                with st.spinner('Procesando pago, generando PDF y guardando en Google Sheets...'):
                    id_boleto = f"RIFA-{int(datetime.now().timestamp())}"
                    codigo_pago = f"PAY-{random.randint(10000, 99999)}"
                    fecha_compra = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    nombre_archivo_pdf = f"Boleto_{id_boleto}.pdf"

                    datos_nuevo_boleto = {
                        "ID_Boleto": id_boleto,
                        "Nombre": nombre,
                        "Correo": correo,
                        "Evento": "Rifa de celular",
                        "Numero_Boleto": str(selected_ticket),
                        "Precio": float(precio_base),
                        "Metodo_Pago": metodo_pago,
                        "Codigo_Pago": codigo_pago,
                        "Fecha_Compra": fecha_compra,
                        "Numero_Telefonico": telefono,
                        "Comprobante": nombre_archivo_pdf,
                    }

                    columnas_estandar = [
                        "ID_Boleto",
                        "Nombre",
                        "Correo",
                        "Evento",
                        "Numero_Boleto",
                        "Precio",
                        "Metodo_Pago",
                        "Codigo_Pago",
                        "Fecha_Compra",
                        "Numero_Telefonico",
                    ]
                    df_nuevo_registro = pd.DataFrame([datos_nuevo_boleto])

                    if df_ventas.empty:
                        df_actualizado = df_nuevo_registro[columnas_estandar]
                    else:
                        for col in columnas_estandar:
                            if col not in df_ventas.columns:
                                df_ventas[col] = ""
                        df_ventas = df_ventas[columnas_estandar]
                        df_actualizado = pd.concat([df_ventas, df_nuevo_registro[columnas_estandar]], ignore_index=True)

                    conn.update(worksheet="Ventas", data=df_actualizado)

                    # Generar PDF oficial
                    archivo_pdf = generar_pdf_boleto(datos_nuevo_boleto)

                    # Guardar bytes del PDF en session_state para que la descarga sea persistente
                    with open(archivo_pdf, "rb") as pdf_file:
                        st.session_state.generated_pdf_bytes = pdf_file.read()
                    st.session_state.generated_pdf_name = archivo_pdf
                    st.session_state.success_message = f"✅ ¡Registro completado! Código de pago asignado: **{codigo_pago}**"

                # Incrementar form_id para vaciar los campos y limpiar selección para el siguiente usuario
                st.session_state.form_id += 1
                st.session_state.selected_ticket = None

                st.rerun()


if __name__ == "__main__":
    main()
