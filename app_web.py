import os
import random
from datetime import datetime
import pandas as pd
import streamlit as st
from reportlab.lib.pagesizes import letter
from reportlab.lib import colors
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle

# Archivo Excel para el control de registros
EXCEL_FILE = "registro_boletos.xlsx"

def inicializar_excel():
    """Crea el archivo Excel si no existe."""
    if not os.path.exists(EXCEL_FILE):
        df = pd.DataFrame(columns=[
            "ID_Boleto", "Nombre", "Correo", "Evento", "Precio", "Codigo_Pago", "Fecha_Compra"
        ])
        df.to_excel(EXCEL_FILE, index=False)

def generar_pdf_boleto(datos_boleto):
    """Genera el comprobante/boleto en formato PDF y devuelve el nombre del archivo."""
    nombre_archivo = f"Boleto_{datos_boleto['ID_Boleto']}.pdf"
    doc = SimpleDocTemplate(nombre_archivo, pagesize=letter)
    story = []
    
    styles = getSampleStyleSheet()
    estilo_titulo = ParagraphStyle(
        'TituloBoleto',
        parent=styles['Heading1'],
        fontSize=20,
        textColor=colors.HexColor("#1A365D"),
        alignment=1
    )
    
    estilo_normal = ParagraphStyle(
        'TextoBoleto',
        parent=styles['Normal'],
        fontSize=12,
        textColor=colors.HexColor("#2D3748")
    )
    
    # Contenido del Boleto
    story.append(Paragraph("COMPROBANTE OFICIAL DE BOLETO", estilo_titulo))
    story.append(Spacer(1, 20))
    
    data = [
        [Paragraph("<b>ID de Boleto:</b>", estilo_normal), Paragraph(str(datos_boleto['ID_Boleto']), estilo_normal)],
        [Paragraph("<b>Asistente:</b>", estilo_normal), Paragraph(datos_boleto['Nombre'], estilo_normal)],
        [Paragraph("<b>Correo:</b>", estilo_normal), Paragraph(datos_boleto['Correo'], estilo_normal)],
        [Paragraph("<b>Evento:</b>", estilo_normal), Paragraph(datos_boleto['Evento'], estilo_normal)],
        [Paragraph("<b>Precio Pagado:</b>", estilo_normal), Paragraph(f"${datos_boleto['Precio']:.2f} MXN", estilo_normal)],
        [Paragraph("<b>Código de Pago:</b>", estilo_normal), Paragraph(datos_boleto['Codigo_Pago'], estilo_normal)],
        [Paragraph("<b>Fecha de Emisión:</b>", estilo_normal), Paragraph(str(datos_boleto['Fecha_Compra']), estilo_normal)]
    ]
    
    t = Table(data, colWidths=[150, 300])
    t.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,-1), colors.HexColor("#F7FAFC")),
        ('GRID', (0,0), (-1,-1), 1, colors.HexColor("#E2E8F0")),
        ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
        ('PADDING', (0,0), (-1,-1), 10),
    ]))
    
    story.append(t)
    story.append(Spacer(1, 30))
    story.append(Paragraph("<i>Presenta este comprobante en la entrada del evento. ¡Gracias por tu compra!</i>", ParagraphStyle('Footer', parent=estilo_normal, fontSize=10, alignment=1, textColor=colors.HexColor("#718096"))))
    
    doc.build(story)
    return nombre_archivo

def registrar_en_excel(datos_boleto):
    """Registra la información del boleto en el archivo Excel."""
    df = pd.read_excel(EXCEL_FILE)
    nuevo_registro = pd.DataFrame([datos_boleto])
    df = pd.concat([df, nuevo_registro], ignore_index=True)
    df.to_excel(EXCEL_FILE, index=False)

def main():
    st.set_page_config(page_title="Venta de Boletos", page_icon="🎟️")
    st.title("🎟️ Sistema de Registro y Venta de Boletos")
    
    inicializar_excel()
    
    # Formulario web
    with st.form("formulario_compra"):
        st.subheader("Datos del Asistente")
        nombre = st.text_input("Ingrese su nombre completo:")
        correo = st.text_input("Ingrese su correo electrónico:")
        evento = st.text_input("Nombre del evento:")
        
        st.subheader("Pago")
        precio = st.number_input("Precio del boleto (MXN):", min_value=1.0, step=50.0)
        metodo = st.selectbox("Seleccione método de pago:", ["Tarjeta de Crédito/Débito", "Transferencia SPEI"])
        
        # Botón de envío
        submit = st.form_submit_button("Generar Código y Pagar")
        
    if submit:
        if not nombre or not correo or not evento:
            st.error("⚠️ Por favor, llena todos los campos antes de continuar.")
        else:
            # Simular procesamiento de pago
            with st.spinner('Procesando pago...'):
                id_boleto = f"BOL-{int(datetime.now().timestamp())}"
                codigo_pago = f"PAY-{random.randint(10000, 99999)}"
                fecha_compra = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                
                datos_boleto = {
                    "ID_Boleto": id_boleto,
                    "Nombre": nombre,
                    "Correo": correo,
                    "Evento": evento,
                    "Precio": precio,
                    "Codigo_Pago": codigo_pago,
                    "Fecha_Compra": fecha_compra
                }
                
                # Generar PDF y guardar en Excel
                archivo_pdf = generar_pdf_boleto(datos_boleto)
                registrar_en_excel(datos_boleto)
            
            st.success("✅ ¡Pago procesado exitosamente!")
            st.info(f"Tu código de confirmación de pago es: **{codigo_pago}**")
            
            # Botón para descargar el PDF
            with open(archivo_pdf, "rb") as pdf_file:
                pdf_bytes = pdf_file.read()
                
            st.download_button(
                label="📥 Descargar Comprobante (PDF)",
                data=pdf_bytes,
                file_name=archivo_pdf,
                mime="application/pdf"
            )

if __name__ == "__main__":
    main()