from flask import Blueprint, render_template, request, redirect, url_for, flash, jsonify
from flask_login import login_required, current_user
from models import db, Expense, obtener_hora_bogota, ArqueoCaja
from decorators import admin_required
from sqlalchemy import extract
from datetime import datetime
from decimal import Decimal

gastos_bp = Blueprint('gastos_bp', __name__)

MESES_ES = ['', 'Enero', 'Febrero', 'Marzo', 'Abril', 'Mayo', 'Junio', 'Julio', 'Agosto', 'Septiembre', 'Octubre', 'Noviembre', 'Diciembre']

def calcular_kpis_mes(mes, anio, usuario_id=None, es_admin=True):
    """Calcula los totales de gastos para un mes y año específicos."""
    query = Expense.query.filter(
        extract('month', Expense.fecha_gasto) == mes,
        extract('year', Expense.fecha_gasto) == anio
    )
    if not es_admin and usuario_id:
        query = query.filter(Expense.usuario_id == usuario_id)

    gastos = query.all()
    total_diarios = sum((Decimal(str(g.monto or 0)) for g in gastos if g.tipo_gasto == 'Gasto Diario'), Decimal('0'))
    total_indirectos = sum((Decimal(str(g.monto or 0)) for g in gastos if g.tipo_gasto == 'Costo Indirecto'), Decimal('0'))
    total_general = total_diarios + total_indirectos
    total_efectivo = sum((Decimal(str(g.monto or 0)) for g in gastos if (g.metodo_pago or 'efectivo').lower() == 'efectivo'), Decimal('0'))
    total_digital = total_general - total_efectivo

    return {
        'total_diarios': float(total_diarios),
        'total_indirectos': float(total_indirectos),
        'total_general': float(total_general),
        'total_efectivo': float(total_efectivo),
        'total_digital': float(total_digital),
        'conteo_gastos': len(gastos)
    }

@gastos_bp.route('/', methods=['GET', 'POST'])
@login_required
def index():
    ahora = obtener_hora_bogota()

    if request.method == 'POST':
        # Detección de petición AJAX
        is_ajax = request.headers.get('X-Requested-With') == 'XMLHttpRequest' or request.is_json

        if request.is_json:
            data = request.get_json()
            tipo_gasto = data.get('tipo_gasto')
            categoria = data.get('categoria', '').strip()
            descripcion = data.get('descripcion', '').strip()
            montos = data.get('montos') or [data.get('monto')]
            metodos_pago = data.get('metodos_pago') or [data.get('metodo_pago', 'efectivo')]
            fecha_str = data.get('fecha_gasto')
        else:
            tipo_gasto = request.form.get('tipo_gasto')
            categoria = (request.form.get('categoria') or '').strip()
            descripcion = (request.form.get('descripcion') or '').strip()
            montos = request.form.getlist('monto[]')
            metodos_pago = request.form.getlist('metodo_pago[]')
            if not montos:
                monto_single = request.form.get('monto')
                if monto_single:
                    montos = [monto_single]
                    metodos_pago = [request.form.get('metodo_pago', 'efectivo')]
            fecha_str = request.form.get('fecha_gasto')

        # Restricción: Vendedores sólo registran gastos diarios / operativos
        if current_user.rol != 'admin' or not tipo_gasto:
            tipo_gasto = 'Gasto Diario'

        if not categoria:
            categoria = 'Varios'

        # Resolver fecha
        if fecha_str:
            try:
                fecha_obj = datetime.strptime(fecha_str, '%Y-%m-%d')
            except ValueError:
                fecha_obj = ahora
        else:
            fecha_obj = ahora

        # Validar si la caja para la fecha del gasto ya fue cerrada
        caja_cerrada = ArqueoCaja.query.filter_by(fecha_arqueo=fecha_obj.date()).first()
        if caja_cerrada:
            msg = f'La caja del día {fecha_obj.date().strftime("%Y-%m-%d")} ya está cerrada. No se pueden registrar gastos en una jornada cerrada.'
            if is_ajax:
                return jsonify({'success': False, 'error': msg}), 400
            flash(msg, 'danger')
            return redirect(url_for('gastos_bp.index'))

        nuevos_gastos = []
        try:
            for monto, metodo in zip(montos, metodos_pago):
                if monto is None or str(monto).strip() == '':
                    continue
                valor_float = float(monto)
                if valor_float > 0:
                    nuevo_gasto = Expense(
                        usuario_id=current_user.id,
                        tipo_gasto=tipo_gasto,
                        categoria=categoria,
                        descripcion=descripcion,
                        monto=valor_float,
                        metodo_pago=(metodo or 'efectivo').lower(),
                        fecha_gasto=fecha_obj
                    )
                    db.session.add(nuevo_gasto)
                    nuevos_gastos.append(nuevo_gasto)

            db.session.commit()

            if is_ajax:
                kpis = calcular_kpis_mes(
                    fecha_obj.month, 
                    fecha_obj.year, 
                    current_user.id, 
                    current_user.rol == 'admin'
                )
                gastos_serializados = [{
                    'id': g.id,
                    'fecha': g.fecha_gasto.strftime('%d/%m/%Y'),
                    'fecha_raw': g.fecha_gasto.strftime('%Y-%m-%d'),
                    'usuario_nombre': current_user.nombre,
                    'tipo_gasto': g.tipo_gasto,
                    'categoria': g.categoria,
                    'descripcion': g.descripcion,
                    'metodo_pago': g.metodo_pago,
                    'monto': float(g.monto or 0)
                } for g in nuevos_gastos]

                return jsonify({
                    'success': True,
                    'message': 'Gasto registrado exitosamente.',
                    'gastos': gastos_serializados,
                    'kpis': kpis
                })

            flash('Gasto registrado exitosamente.', 'success')
        except Exception as e:
            db.session.rollback()
            msg = f'Error al registrar el gasto: {str(e)}'
            if is_ajax:
                return jsonify({'success': False, 'error': msg}), 500
            flash('Error al intentar registrar el gasto en la base de datos.', 'danger')

        return redirect(url_for('gastos_bp.index'))

    # GET Logic (Filters selected month and year, default current month)
    try:
        mes_seleccionado = int(request.args.get('mes', ahora.month))
        if mes_seleccionado < 1 or mes_seleccionado > 12:
            mes_seleccionado = ahora.month
    except (TypeError, ValueError):
        mes_seleccionado = ahora.month

    try:
        anio_seleccionado = int(request.args.get('anio', ahora.year))
        if anio_seleccionado < 2020 or anio_seleccionado > 2040:
            anio_seleccionado = ahora.year
    except (TypeError, ValueError):
        anio_seleccionado = ahora.year

    # Cálculo de mes anterior y siguiente para navegación rápida
    if mes_seleccionado == 1:
        mes_ant, anio_ant = 12, anio_seleccionado - 1
    else:
        mes_ant, anio_ant = mes_seleccionado - 1, anio_seleccionado

    if mes_seleccionado == 12:
        mes_sig, anio_sig = 1, anio_seleccionado + 1
    else:
        mes_sig, anio_sig = mes_seleccionado + 1, anio_seleccionado

    # Consultamos registros del mes y del año seleccionado
    query = Expense.query.filter(
        extract('month', Expense.fecha_gasto) == mes_seleccionado,
        extract('year', Expense.fecha_gasto) == anio_seleccionado
    )
    
    # Restricción de visibilidad: Si no es admin, solo ve los propios
    if current_user.rol != 'admin':
        query = query.filter(Expense.usuario_id == current_user.id)
        
    gastos_mes = query.order_by(Expense.fecha_gasto.desc(), Expense.id.desc()).all()

    total_diarios = sum((Decimal(str(g.monto or 0)) for g in gastos_mes if g.tipo_gasto == 'Gasto Diario'), Decimal('0'))
    total_indirectos = sum((Decimal(str(g.monto or 0)) for g in gastos_mes if g.tipo_gasto == 'Costo Indirecto'), Decimal('0'))
    total_general = total_diarios + total_indirectos
    total_efectivo = sum((Decimal(str(g.monto or 0)) for g in gastos_mes if (g.metodo_pago or 'efectivo').lower() == 'efectivo'), Decimal('0'))
    total_digital = total_general - total_efectivo

    hoy_str = ahora.strftime('%Y-%m-%d')
    es_mes_actual = (mes_seleccionado == ahora.month and anio_seleccionado == ahora.year)
    nombre_mes = MESES_ES[mes_seleccionado]

    # Categorías frecuentes para autocompletado y botones rápidos
    categorias_frecuentes = [
        {'nombre': 'Alimentación', 'icono': 'fa-utensils', 'color': '#f59e0b'},
        {'nombre': 'Transporte / Fletes', 'icono': 'fa-motorcycle', 'color': '#0ea5e9'},
        {'nombre': 'Aseo y Limpieza', 'icono': 'fa-broom', 'color': '#10b981'},
        {'nombre': 'Servicios Públicos', 'icono': 'fa-bolt', 'color': '#eab308'},
        {'nombre': 'Empaques e Insumos', 'icono': 'fa-box', 'color': '#8b5cf6'},
        {'nombre': 'Mantenimiento', 'icono': 'fa-wrench', 'color': '#64748b'},
        {'nombre': 'Nómina / Turno', 'icono': 'fa-hand-holding-dollar', 'color': '#ec4899'},
        {'nombre': 'Arriendo', 'icono': 'fa-building', 'color': '#6366f1'},
    ]

    return render_template(
        'gastos/index.html',
        gastos=gastos_mes,
        total_diarios=float(total_diarios),
        total_indirectos=float(total_indirectos),
        total_general=float(total_general),
        total_efectivo=float(total_efectivo),
        total_digital=float(total_digital),
        hoy=hoy_str,
        mes_seleccionado=mes_seleccionado,
        anio_seleccionado=anio_seleccionado,
        nombre_mes=nombre_mes,
        es_mes_actual=es_mes_actual,
        mes_ant=mes_ant,
        anio_ant=anio_ant,
        mes_sig=mes_sig,
        anio_sig=anio_sig,
        meses_lista=enumerate(MESES_ES[1:], 1),
        categorias_frecuentes=categorias_frecuentes
    )

@gastos_bp.route('/<int:id>/editar', methods=['POST'])
@login_required
def editar_gasto(id):
    gasto = Expense.query.get_or_404(id)
    is_ajax = request.headers.get('X-Requested-With') == 'XMLHttpRequest' or request.is_json

    # Restricción de permisos
    if current_user.rol != 'admin' and gasto.usuario_id != current_user.id:
        msg = 'No tienes permiso para editar este gasto.'
        if is_ajax:
            return jsonify({'success': False, 'error': msg}), 403
        flash(msg, 'danger')
        return redirect(url_for('gastos_bp.index'))

    # Validar si la caja de la fecha original ya fue cerrada
    caja_original_cerrada = ArqueoCaja.query.filter_by(fecha_arqueo=gasto.fecha_gasto.date()).first()
    if caja_original_cerrada:
        msg = f'No se puede editar este gasto porque la caja del día {gasto.fecha_gasto.date().strftime("%Y-%m-%d")} ya fue cerrada.'
        if is_ajax:
            return jsonify({'success': False, 'error': msg}), 400
        flash(msg, 'danger')
        return redirect(url_for('gastos_bp.index'))

    data = request.get_json() if request.is_json else request.form

    categoria = (data.get('categoria') or '').strip()
    descripcion = (data.get('descripcion') or '').strip()
    monto_raw = data.get('monto')
    metodo_pago = (data.get('metodo_pago') or 'efectivo').lower()
    fecha_str = data.get('fecha_gasto')
    tipo_gasto = data.get('tipo_gasto')

    try:
        nuevo_monto = float(monto_raw)
        if nuevo_monto <= 0:
            raise ValueError('El monto debe ser mayor a 0.')
    except Exception:
        msg = 'Monto inválido.'
        if is_ajax:
            return jsonify({'success': False, 'error': msg}), 400
        flash(msg, 'danger')
        return redirect(url_for('gastos_bp.index'))

    # Validar nueva fecha si cambió
    if fecha_str:
        try:
            nueva_fecha = datetime.strptime(fecha_str, '%Y-%m-%d')
            if nueva_fecha.date() != gasto.fecha_gasto.date():
                caja_nueva_cerrada = ArqueoCaja.query.filter_by(fecha_arqueo=nueva_fecha.date()).first()
                if caja_nueva_cerrada:
                    msg = f'No se puede trasladar el gasto a la fecha {nueva_fecha.date().strftime("%Y-%m-%d")} porque esa jornada ya fue cerrada.'
                    if is_ajax:
                        return jsonify({'success': False, 'error': msg}), 400
                    flash(msg, 'danger')
                    return redirect(url_for('gastos_bp.index'))
                gasto.fecha_gasto = nueva_fecha
        except ValueError:
            pass

    gasto.categoria = categoria or gasto.categoria
    gasto.descripcion = descripcion
    gasto.monto = nuevo_monto
    gasto.metodo_pago = metodo_pago

    if current_user.rol == 'admin' and tipo_gasto in ['Gasto Diario', 'Costo Indirecto']:
        gasto.tipo_gasto = tipo_gasto

    try:
        db.session.commit()
        if is_ajax:
            kpis = calcular_kpis_mes(
                gasto.fecha_gasto.month,
                gasto.fecha_gasto.year,
                current_user.id,
                current_user.rol == 'admin'
            )
            return jsonify({
                'success': True,
                'message': 'Gasto actualizado correctamente.',
                'gasto': {
                    'id': gasto.id,
                    'fecha': gasto.fecha_gasto.strftime('%d/%m/%Y'),
                    'fecha_raw': gasto.fecha_gasto.strftime('%Y-%m-%d'),
                    'usuario_nombre': gasto.usuario.nombre,
                    'tipo_gasto': gasto.tipo_gasto,
                    'categoria': gasto.categoria,
                    'descripcion': gasto.descripcion,
                    'metodo_pago': gasto.metodo_pago,
                    'monto': float(gasto.monto or 0)
                },
                'kpis': kpis
            })
        flash('Gasto actualizado correctamente.', 'success')
    except Exception as e:
        db.session.rollback()
        msg = f'Error al actualizar: {str(e)}'
        if is_ajax:
            return jsonify({'success': False, 'error': msg}), 500
        flash(msg, 'danger')

    return redirect(url_for('gastos_bp.index'))

@gastos_bp.route('/<int:id>/eliminar', methods=['POST'])
@login_required
@admin_required
def eliminar_gasto(id):
    gasto = Expense.query.get_or_404(id)
    is_ajax = request.headers.get('X-Requested-With') == 'XMLHttpRequest' or request.is_json
    
    # Validar si la caja de la fecha del gasto ya fue cerrada
    caja_cerrada = ArqueoCaja.query.filter_by(fecha_arqueo=gasto.fecha_gasto.date()).first()
    if caja_cerrada:
        msg = f'No se puede eliminar este gasto porque la caja del día {gasto.fecha_gasto.date().strftime("%Y-%m-%d")} ya fue cerrada.'
        if is_ajax:
            return jsonify({'success': False, 'error': msg}), 400
        flash(msg, 'danger')
        return redirect(url_for('gastos_bp.index'))

    mes = gasto.fecha_gasto.month
    anio = gasto.fecha_gasto.year
    descripcion = gasto.descripcion or gasto.categoria

    try:
        db.session.delete(gasto)
        db.session.commit()
        if is_ajax:
            kpis = calcular_kpis_mes(mes, anio, current_user.id, True)
            return jsonify({
                'success': True,
                'message': f'Gasto "{descripcion}" eliminado correctamente.',
                'deleted_id': id,
                'kpis': kpis
            })
        flash(f'Gasto "{descripcion}" eliminado correctamente.', 'success')
    except Exception as e:
        db.session.rollback()
        msg = f'Error al intentar eliminar el gasto: {str(e)}'
        if is_ajax:
            return jsonify({'success': False, 'error': msg}), 500
        flash('Error al intentar eliminar el gasto.', 'danger')

    return redirect(url_for('gastos_bp.index'))
