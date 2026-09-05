import os
import time
from datetime import datetime
from flask import Blueprint, render_template, request, redirect, url_for, flash, current_app
from flask_login import login_required, current_user
from werkzeug.utils import secure_filename
from models import db, Provider, ProviderInvoice, ProviderPayment, obtener_hora_bogota
from decorators import admin_required

providers_bp = Blueprint('providers_bp', __name__, url_prefix='/proveedores')

ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'pdf'}

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

def parse_fecha(fecha_str):
    if not fecha_str:
        return obtener_hora_bogota()
    try:
        dt = datetime.strptime(fecha_str.strip(), '%Y-%m-%d')
        ahora = obtener_hora_bogota()
        return dt.replace(hour=ahora.hour, minute=ahora.minute, second=ahora.second)
    except (ValueError, TypeError):
        return obtener_hora_bogota()

@providers_bp.route('/', methods=['GET'])
@login_required
@admin_required
def index():
    proveedores = Provider.query.order_by(Provider.nombre.asc()).all()
    hoy_str = obtener_hora_bogota().strftime('%Y-%m-%d')

    proveedores_data = []
    total_deuda_global = 0
    proveedores_con_deuda = 0

    for p in proveedores:
        total_facturado = sum([inv.monto_total for inv in p.facturas])
        total_abonado = sum([pay.monto_abonado for pay in p.pagos])
        saldo = total_facturado - total_abonado
        if saldo > 0:
            total_deuda_global += saldo
            proveedores_con_deuda += 1

        proveedores_data.append({
            'proveedor': p,
            'total_facturado': total_facturado,
            'total_abonado': total_abonado,
            'saldo': saldo
        })

    return render_template('admin/proveedores/index.html',
                           proveedores_data=proveedores_data,
                           total_proveedores=len(proveedores_data),
                           total_deuda_global=total_deuda_global,
                           proveedores_con_deuda=proveedores_con_deuda,
                           hoy=hoy_str)

@providers_bp.route('/crear', methods=['POST'])
@login_required
@admin_required
def crear():
    nombre = request.form.get('nombre', '').strip()
    empresa = request.form.get('empresa', '').strip()
    telefono = request.form.get('telefono', '').strip()
    
    if not nombre:
        flash('El nombre del proveedor es obligatorio.', 'danger')
        return redirect(url_for('providers_bp.index'))
        
    nuevo_proveedor = Provider(
        nombre=nombre,
        empresa=empresa,
        telefono=telefono
    )
    db.session.add(nuevo_proveedor)
    db.session.commit()
    
    flash(f'Proveedor "{nombre}" creado correctamente.', 'success')
    return redirect(url_for('providers_bp.index'))

@providers_bp.route('/<int:id>/editar', methods=['POST'])
@login_required
@admin_required
def editar(id):
    proveedor = Provider.query.get_or_404(id)
    nombre = request.form.get('nombre', '').strip()
    empresa = request.form.get('empresa', '').strip()
    telefono = request.form.get('telefono', '').strip()

    if not nombre:
        flash('El nombre del proveedor no puede estar vacío.', 'danger')
        return redirect(request.referrer or url_for('providers_bp.detalle', id=id))

    proveedor.nombre = nombre
    proveedor.empresa = empresa
    proveedor.telefono = telefono
    db.session.commit()

    flash(f'Datos del proveedor "{nombre}" actualizados con éxito.', 'success')
    return redirect(request.referrer or url_for('providers_bp.detalle', id=id))

@providers_bp.route('/<int:id>', methods=['GET'])
@login_required
@admin_required
def detalle(id):
    proveedor = Provider.query.get_or_404(id)
    hoy_str = obtener_hora_bogota().strftime('%Y-%m-%d')
    
    total_facturado = sum([inv.monto_total for inv in proveedor.facturas])
    total_abonado = sum([pay.monto_abonado for pay in proveedor.pagos])
    saldo_pendiente = total_facturado - total_abonado
    
    facturas = ProviderInvoice.query.filter_by(provider_id=id).order_by(ProviderInvoice.fecha_factura.desc()).all()
    pagos = ProviderPayment.query.filter_by(provider_id=id).order_by(ProviderPayment.fecha_pago.desc()).all()
    
    return render_template('admin/proveedores/detalle.html', 
                           proveedor=proveedor,
                           total_facturado=total_facturado,
                           total_abonado=total_abonado,
                           saldo_pendiente=saldo_pendiente,
                           facturas=facturas,
                           pagos=pagos,
                           hoy=hoy_str)

@providers_bp.route('/<int:id>/invoice', methods=['POST'])
@login_required
@admin_required
def registrar_factura(id):
    proveedor = Provider.query.get_or_404(id)
    
    try:
        monto_total = float(request.form.get('monto_total', 0))
    except (ValueError, TypeError):
        monto_total = 0.0
        
    numero_factura = request.form.get('numero_factura', '').strip()
    descripcion = request.form.get('descripcion', '').strip()
    fecha_factura = parse_fecha(request.form.get('fecha_factura'))
    
    if monto_total <= 0:
        flash('El monto de la factura debe ser mayor a cero.', 'danger')
        return redirect(url_for('providers_bp.detalle', id=id))
        
    archivo = request.files.get('comprobante')
    filename_saved = None
    
    if archivo and archivo.filename != '':
        if allowed_file(archivo.filename):
            ext = archivo.filename.rsplit('.', 1)[1].lower()
            timestamp = int(time.time())
            filename_saved = f"prov_{id}_{timestamp}.{ext}"
            
            providers_upload_folder = os.path.join(current_app.config['UPLOAD_FOLDER'], 'providers')
            os.makedirs(providers_upload_folder, exist_ok=True)
            
            file_path = os.path.join(providers_upload_folder, filename_saved)
            archivo.save(file_path)
        else:
            flash('Formato de archivo no permitido (solo png, jpg, jpeg, pdf).', 'warning')
            
    nueva_factura = ProviderInvoice(
        provider_id=id,
        monto_total=monto_total,
        numero_factura=numero_factura,
        descripcion=descripcion,
        comprobante=filename_saved,
        fecha_factura=fecha_factura
    )
    
    db.session.add(nueva_factura)
    db.session.commit()
    
    flash('Factura registrada correctamente.', 'success')
    return redirect(url_for('providers_bp.detalle', id=id))

@providers_bp.route('/factura/<int:factura_id>/editar', methods=['POST'])
@login_required
@admin_required
def editar_factura(factura_id):
    factura = ProviderInvoice.query.get_or_404(factura_id)
    provider_id = factura.provider_id

    try:
        monto_total = float(request.form.get('monto_total', 0))
    except (ValueError, TypeError):
        monto_total = 0.0

    if monto_total <= 0:
        flash('El monto de la factura debe ser mayor a cero.', 'danger')
        return redirect(url_for('providers_bp.detalle', id=provider_id))

    numero_factura = request.form.get('numero_factura', '').strip()
    descripcion = request.form.get('descripcion', '').strip()
    fecha_factura = parse_fecha(request.form.get('fecha_factura'))

    # Manejo de comprobante
    archivo = request.files.get('comprobante')
    eliminar_adjunto = request.form.get('eliminar_comprobante') == '1'
    providers_upload_folder = os.path.join(current_app.config['UPLOAD_FOLDER'], 'providers')

    if archivo and archivo.filename != '':
        if allowed_file(archivo.filename):
            # Borrar comprobante anterior si existía
            if factura.comprobante:
                viejo_path = os.path.join(providers_upload_folder, factura.comprobante)
                if os.path.exists(viejo_path):
                    try:
                        os.remove(viejo_path)
                    except OSError:
                        pass

            ext = archivo.filename.rsplit('.', 1)[1].lower()
            timestamp = int(time.time())
            filename_saved = f"prov_{provider_id}_{timestamp}.{ext}"
            os.makedirs(providers_upload_folder, exist_ok=True)
            archivo.save(os.path.join(providers_upload_folder, filename_saved))
            factura.comprobante = filename_saved
        else:
            flash('Formato de archivo no permitido. Se conservó el soporte anterior.', 'warning')
    elif eliminar_adjunto and factura.comprobante:
        viejo_path = os.path.join(providers_upload_folder, factura.comprobante)
        if os.path.exists(viejo_path):
            try:
                os.remove(viejo_path)
            except OSError:
                pass
        factura.comprobante = None

    factura.monto_total = monto_total
    factura.numero_factura = numero_factura
    factura.descripcion = descripcion
    factura.fecha_factura = fecha_factura
    db.session.commit()

    flash(f'Factura #{numero_factura or factura_id} actualizada correctamente.', 'success')
    return redirect(url_for('providers_bp.detalle', id=provider_id))

@providers_bp.route('/factura/<int:factura_id>/eliminar', methods=['POST'])
@login_required
@admin_required
def eliminar_factura(factura_id):
    factura = ProviderInvoice.query.get_or_404(factura_id)
    provider_id = factura.provider_id

    # Eliminar archivo físico si existe
    if factura.comprobante:
        providers_upload_folder = os.path.join(current_app.config['UPLOAD_FOLDER'], 'providers')
        file_path = os.path.join(providers_upload_folder, factura.comprobante)
        if os.path.exists(file_path):
            try:
                os.remove(file_path)
            except OSError:
                pass

    db.session.delete(factura)
    db.session.commit()

    flash('Factura eliminada del historial y saldo recalculado.', 'success')
    return redirect(url_for('providers_bp.detalle', id=provider_id))

@providers_bp.route('/<int:id>/payment', methods=['POST'])
@login_required
@admin_required
def registrar_pago(id):
    proveedor = Provider.query.get_or_404(id)
    
    try:
        monto_abonado = float(request.form.get('monto_abonado', 0))
    except (ValueError, TypeError):
        monto_abonado = 0.0
        
    observacion = request.form.get('observacion', '').strip()
    fecha_pago = parse_fecha(request.form.get('fecha_pago'))
    
    if monto_abonado <= 0:
        flash('El monto del abono debe ser mayor a cero.', 'danger')
        return redirect(url_for('providers_bp.detalle', id=id))
        
    nuevo_pago = ProviderPayment(
        provider_id=id,
        monto_abonado=monto_abonado,
        observacion=observacion,
        fecha_pago=fecha_pago
    )
    
    db.session.add(nuevo_pago)
    db.session.commit()
    
    flash('Abono registrado correctamente.', 'success')
    return redirect(url_for('providers_bp.detalle', id=id))

@providers_bp.route('/pago/<int:pago_id>/editar', methods=['POST'])
@login_required
@admin_required
def editar_pago(pago_id):
    pago = ProviderPayment.query.get_or_404(pago_id)
    provider_id = pago.provider_id

    try:
        monto_abonado = float(request.form.get('monto_abonado', 0))
    except (ValueError, TypeError):
        monto_abonado = 0.0

    if monto_abonado <= 0:
        flash('El monto del abono debe ser mayor a cero.', 'danger')
        return redirect(url_for('providers_bp.detalle', id=provider_id))

    observacion = request.form.get('observacion', '').strip()
    fecha_pago = parse_fecha(request.form.get('fecha_pago'))

    pago.monto_abonado = monto_abonado
    pago.observacion = observacion
    pago.fecha_pago = fecha_pago
    db.session.commit()

    flash('Abono actualizado correctamente y saldo recalculado.', 'success')
    return redirect(url_for('providers_bp.detalle', id=provider_id))

@providers_bp.route('/pago/<int:pago_id>/eliminar', methods=['POST'])
@login_required
@admin_required
def eliminar_pago(pago_id):
    pago = ProviderPayment.query.get_or_404(pago_id)
    provider_id = pago.provider_id

    db.session.delete(pago)
    db.session.commit()

    flash('Abono eliminado y saldo de cuenta pendiente restablecido.', 'success')
    return redirect(url_for('providers_bp.detalle', id=provider_id))

@providers_bp.route('/eliminar/<int:id>', methods=['POST'])
@login_required
@admin_required
def eliminar(id):
    proveedor = Provider.query.get_or_404(id)
    
    # Eliminar archivos físicos de comprobantes
    providers_upload_folder = os.path.join(current_app.config['UPLOAD_FOLDER'], 'providers')
    for inv in proveedor.facturas:
        if inv.comprobante:
            file_path = os.path.join(providers_upload_folder, inv.comprobante)
            if os.path.exists(file_path):
                try:
                    os.remove(file_path)
                except OSError:
                    pass
                    
    db.session.delete(proveedor)
    db.session.commit()
    
    flash(f'Proveedor "{proveedor.nombre}" eliminado correctamente.', 'success')
    return redirect(url_for('providers_bp.index'))
