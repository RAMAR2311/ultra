from flask import Blueprint, request, jsonify
from flask_login import login_required, current_user
from models import db, PriceApproval, Product, ProductVariant, obtener_hora_bogota
from decorators import admin_required
from decimal import Decimal
from datetime import datetime

aprobaciones_bp = Blueprint('aprobaciones_bp', __name__)

# =========================================================================
# ENDPOINTS LADO POS / VENDEDOR
# =========================================================================

@aprobaciones_bp.route('/precio/solicitar', methods=['POST'])
@login_required
def solicitar_precio():
    """Crea una nueva solicitud de precio especial para autorización remota del admin."""
    data = request.get_json() or {}

    product_id = data.get('product_id')
    variant_id = data.get('variant_id')
    nombre_producto = data.get('nombre_producto')
    
    try:
        precio_original = Decimal(str(data.get('precio_original', '0')))
        precio_solicitado = Decimal(str(data.get('precio_solicitado', '0')))
    except Exception:
        return jsonify({'error': 'Precios inválidos o no numéricos'}), 400

    if precio_solicitado <= 0:
        return jsonify({'error': 'El precio solicitado debe ser mayor a $0'}), 400

    # Resolver precio original si no vino en el payload
    if precio_original <= 0:
        if variant_id:
            var_obj = ProductVariant.query.get(variant_id)
            if var_obj:
                precio_original = Decimal(str(var_obj.precio_minimo or 0))
        elif product_id:
            prod_obj = Product.query.get(product_id)
            if prod_obj:
                precio_original = Decimal(str(prod_obj.precio_minimo or 0))

    # Resolver nombre de producto si no vino en el payload
    if not nombre_producto:
        if variant_id:
            variante = ProductVariant.query.get(variant_id)
            if variante:
                nombre_producto = f"{variante.producto.nombre} ({variante.nombre_variante})"
        elif product_id:
            producto = Product.query.get(product_id)
            if producto:
                nombre_producto = producto.nombre
        
    if not nombre_producto:
        nombre_producto = "Producto"

    motivo = data.get('motivo', '').strip() if data.get('motivo') else None

    # Regla: Cancelar solicitudes pendientes previas del mismo vendedor para este producto/variante
    PriceApproval.query.filter_by(
        vendedor_id=current_user.id,
        product_id=product_id,
        variant_id=variant_id,
        estado='pendiente'
    ).update({'estado': 'cancelada'}, synchronize_session=False)

    nueva_solicitud = PriceApproval(
        vendedor_id=current_user.id,
        product_id=product_id,
        variant_id=variant_id,
        nombre_producto=nombre_producto,
        precio_original=precio_original,
        precio_solicitado=precio_solicitado,
        motivo=motivo,
        estado='pendiente',
        fecha_solicitud=obtener_hora_bogota()
    )

    db.session.add(nueva_solicitud)
    db.session.commit()

    return jsonify({
        'success': True,
        'id': nueva_solicitud.id,
        'approval_id': nueva_solicitud.id,
        'solicitud_id': nueva_solicitud.id,
        'estado': 'pendiente',
        'mensaje': 'Solicitud enviada al administrador en tiempo real'
    }), 201


@aprobaciones_bp.route('/precio/estado/<int:solicitud_id>', methods=['GET'])
@login_required
def consultar_estado(solicitud_id):
    """Consulta el estado de una solicitud para el polling del POS."""
    solicitud = PriceApproval.query.get_or_404(solicitud_id)

    # Solo el vendedor que la creó o un administrador pueden consultarla
    if current_user.rol != 'admin' and solicitud.vendedor_id != current_user.id:
        return jsonify({'error': 'No autorizado para consultar esta solicitud'}), 403

    return jsonify({
        'id': solicitud.id,
        'approval_id': solicitud.id,
        'solicitud_id': solicitud.id,
        'estado': solicitud.estado,
        'nombre_producto': solicitud.nombre_producto,
        'precio_original': float(solicitud.precio_original),
        'precio_solicitado': float(solicitud.precio_solicitado),
        'precio_aprobado': float(solicitud.precio_aprobado) if solicitud.precio_aprobado is not None else None,
        'motivo': solicitud.motivo,
        'motivo_rechazo': solicitud.motivo_rechazo
    })


@aprobaciones_bp.route('/precio/cancelar/<int:solicitud_id>', methods=['POST'])
@login_required
def cancelar_solicitud(solicitud_id):
    """Permite al vendedor cancelar su solicitud pendiente."""
    solicitud = PriceApproval.query.get_or_404(solicitud_id)

    if current_user.rol != 'admin' and solicitud.vendedor_id != current_user.id:
        return jsonify({'error': 'No autorizado para cancelar esta solicitud'}), 403

    if solicitud.estado == 'pendiente':
        solicitud.estado = 'cancelada'
        solicitud.fecha_resolucion = obtener_hora_bogota()
        db.session.commit()
        return jsonify({'success': True, 'mensaje': 'Solicitud cancelada exitosamente'})
    
    return jsonify({'success': False, 'mensaje': f'No se puede cancelar una solicitud en estado {solicitud.estado}'})


# =========================================================================
# ENDPOINTS LADO ADMINISTRADOR
# =========================================================================

@aprobaciones_bp.route('/aprobaciones/pendientes', methods=['GET'])
@login_required
@admin_required
def listar_pendientes():
    """Devuelve todas las solicitudes pendientes para el polling del administrador."""
    pendientes = PriceApproval.query.filter_by(estado='pendiente').order_by(PriceApproval.fecha_solicitud.asc()).all()

    ahora = obtener_hora_bogota()
    resultado = []
    for sol in pendientes:
        segundos = int((ahora - sol.fecha_solicitud).total_seconds()) if sol.fecha_solicitud else 0
        if segundos < 60:
            tiempo_str = f"hace {segundos}s"
        else:
            minutos = segundos // 60
            tiempo_str = f"hace {minutos}m"

        resultado.append({
            'id': sol.id,
            'vendedor': sol.vendedor.nombre if sol.vendedor else 'Vendedor',
            'vendedor_id': sol.vendedor_id,
            'vendedor_nombre': sol.vendedor.nombre if sol.vendedor else 'Vendedor',
            'product_id': sol.product_id,
            'variant_id': sol.variant_id,
            'producto': sol.nombre_producto,
            'nombre_producto': sol.nombre_producto,
            'variante': sol.variante.nombre_variante if sol.variante else None,
            'precio_original': float(sol.precio_original),
            'precio_solicitado': float(sol.precio_solicitado),
            'diferencia': float(sol.precio_original - sol.precio_solicitado),
            'motivo': sol.motivo or '',
            'fecha': tiempo_str,
            'fecha_solicitud': sol.fecha_solicitud.strftime('%H:%M:%S') if sol.fecha_solicitud else '',
            'tiempo_transcurrido': tiempo_str
        })

    return jsonify(resultado)


@aprobaciones_bp.route('/aprobaciones/<int:solicitud_id>/aprobar', methods=['POST'])
@login_required
@admin_required
def aprobar_solicitud(solicitud_id):
    """Aprueba la solicitud con el precio solicitado o una contraoferta."""
    solicitud = PriceApproval.query.get_or_404(solicitud_id)

    if solicitud.estado != 'pendiente':
        return jsonify({'error': f'La solicitud ya no está pendiente (estado actual: {solicitud.estado})'}), 400

    data = request.get_json() or {}
    precio_aprobado_raw = data.get('precio_aprobado')

    if precio_aprobado_raw is not None and str(precio_aprobado_raw).strip():
        try:
            precio_aprobado = Decimal(str(precio_aprobado_raw))
        except Exception:
            return jsonify({'error': 'Precio de aprobación inválido'}), 400
    else:
        precio_aprobado = solicitud.precio_solicitado

    if precio_aprobado <= 0:
        return jsonify({'error': 'El precio aprobado debe ser mayor a $0'}), 400

    solicitud.estado = 'aprobado'
    solicitud.precio_aprobado = precio_aprobado
    solicitud.admin_id = current_user.id
    solicitud.fecha_resolucion = obtener_hora_bogota()

    db.session.commit()

    return jsonify({
        'success': True,
        'solicitud_id': solicitud.id,
        'precio_aprobado': float(solicitud.precio_aprobado),
        'mensaje': f'Precio de ${precio_aprobado:,.0f} aprobado exitosamente'
    })


@aprobaciones_bp.route('/aprobaciones/<int:solicitud_id>/rechazar', methods=['POST'])
@login_required
@admin_required
def rechazar_solicitud(solicitud_id):
    """Rechaza la solicitud de precio con un motivo justificado opcional."""
    solicitud = PriceApproval.query.get_or_404(solicitud_id)

    if solicitud.estado != 'pendiente':
        return jsonify({'error': f'La solicitud ya no está pendiente (estado actual: {solicitud.estado})'}), 400

    data = request.get_json() or {}
    motivo = data.get('motivo', '').strip() or 'Precio por debajo del margen permitido'

    solicitud.estado = 'rechazado'
    solicitud.motivo_rechazo = motivo
    solicitud.admin_id = current_user.id
    solicitud.fecha_resolucion = obtener_hora_bogota()

    db.session.commit()

    return jsonify({
        'success': True,
        'solicitud_id': solicitud.id,
        'mensaje': 'Solicitud rechazada exitosamente'
    })
