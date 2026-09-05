import os
from datetime import datetime, date, timedelta
from decimal import Decimal
import pytz
from flask import Blueprint, render_template, request, redirect, url_for, flash, jsonify
from flask_login import login_required, current_user
from models import db, Sale, SalePayment, ArqueoCaja, Expense, User
from decorators import admin_required

arqueo_bp = Blueprint('arqueo_bp', __name__)

def obtener_hora_bogota():
    return datetime.now(pytz.timezone('America/Bogota')).replace(tzinfo=None)

def clasificar_metodo_digital(metodo_raw):
    m = (metodo_raw or '').lower().strip()
    if 'nequi' in m:
        return 'nequi'
    elif 'bancolombia' in m or 'transferencia' in m:
        return 'bancolombia'
    elif 'daviplata' in m:
        return 'daviplata'
    elif 'datafono' in m or 'tarjeta' in m or 'bolt' in m or 'bold' in m or 'redeban' in m:
        return 'datafono'
    elif 'credito' in m or 'crédito' in m:
        return 'credito'
    else:
        return 'otros'

def calcular_conciliacion_dia(fecha_seleccionada):
    """
    Calcula atómicamente todos los rubros financieros del día seleccionado:
    - total_efectivo_sistema
    - total_transferencia_sistema
    - desglose_digital (por plataforma)
    - gastos_del_dia (efectivo de caja chica)
    - ventas del día con sus detalles
    - gastos del día
    """
    inicio_dia = datetime.combine(fecha_seleccionada, datetime.min.time())
    fin_dia = inicio_dia + timedelta(days=1)

    ventas = Sale.query.filter(
        Sale.fecha_venta >= inicio_dia,
        Sale.fecha_venta < fin_dia
    ).order_by(Sale.fecha_venta.desc()).all()

    total_efectivo = Decimal('0')
    total_digital = Decimal('0')

    desglose_digital = {
        'nequi': Decimal('0'),
        'bancolombia': Decimal('0'),
        'daviplata': Decimal('0'),
        'datafono': Decimal('0'),
        'credito': Decimal('0'),
        'otros': Decimal('0')
    }

    for v in ventas:
        if v.pagos and len(v.pagos) > 0:
            for pago in v.pagos:
                monto = Decimal(str(pago.monto or 0))
                metodo = (pago.metodo_pago or '').lower().strip()
                if metodo == 'efectivo':
                    total_efectivo += monto
                else:
                    total_digital += monto
                    categoria_canal = clasificar_metodo_digital(metodo)
                    desglose_digital[categoria_canal] += monto
        else:
            monto = Decimal(str(v.monto_total or 0))
            metodo = (v.metodo_pago or 'efectivo').lower().strip()
            if metodo == 'efectivo':
                total_efectivo += monto
            else:
                total_digital += monto
                categoria_canal = clasificar_metodo_digital(metodo)
                desglose_digital[categoria_canal] += monto

    # Gastos del día pagados en EFECTIVO tomados de caja chica
    gastos_query = Expense.query.filter(
        Expense.fecha_gasto >= inicio_dia,
        Expense.fecha_gasto < fin_dia
    ).order_by(Expense.fecha_gasto.asc()).all()

    gastos_efectivo_caja = Decimal('0')
    for g in gastos_query:
        # Si es Gasto Diario y su método es efectivo (o nulo por defecto en caja)
        metodo_g = (g.metodo_pago or 'efectivo').lower().strip()
        if g.tipo_gasto == 'Gasto Diario' and metodo_g == 'efectivo':
            gastos_efectivo_caja += Decimal(str(g.monto or 0))

    return {
        'ventas': ventas,
        'gastos': gastos_query,
        'total_efectivo': total_efectivo,
        'total_digital': total_digital,
        'desglose_digital': desglose_digital,
        'gastos_efectivo_caja': gastos_efectivo_caja
    }

@arqueo_bp.route('/nuevo', methods=['GET', 'POST'])
@login_required
def nuevo():
    # Obtener fecha de consulta o usar fecha actual en Bogotá
    fecha_str = request.args.get('fecha', request.form.get('fecha', obtener_hora_bogota().strftime('%Y-%m-%d')))
    try:
        fecha_seleccionada = datetime.strptime(fecha_str, '%Y-%m-%d').date()
    except (ValueError, TypeError):
        fecha_seleccionada = obtener_hora_bogota().date()
        fecha_str = fecha_seleccionada.strftime('%Y-%m-%d')

    # Verificar si ya existe arqueo cerrado para esa fecha
    arqueo_existente = ArqueoCaja.query.filter_by(fecha_arqueo=fecha_seleccionada).first()
    caja_cerrada = arqueo_existente is not None

    # Procesar guardado de arqueo (Cierre de Caja)
    if request.method == 'POST':
        if caja_cerrada:
            flash(f'La caja del día {fecha_str} ya fue cerrada por {arqueo_existente.cajero.nombre if arqueo_existente.cajero else "el sistema"}. Los datos son inmutables.', 'warning')
            return redirect(url_for('arqueo_bp.nuevo', fecha=fecha_str))

        try:
            base_inicial = Decimal(str(request.form.get('base_inicial', '0') or '0'))
        except Exception:
            base_inicial = Decimal('0')

        try:
            efectivo_raw = request.form.get('efectivo_fisico') or request.form.get('efectivo_fisico_contado') or '0'
            efectivo_fisico_contado = Decimal(str(efectivo_raw))
        except Exception:
            efectivo_fisico_contado = Decimal('0')

        observaciones = (request.form.get('observaciones_diferencia') or request.form.get('observaciones') or '').strip()

        # Recálculo atómico en backend
        datos_calc = calcular_conciliacion_dia(fecha_seleccionada)
        total_efectivo = datos_calc['total_efectivo']
        total_digital = datos_calc['total_digital']
        gastos_caja = datos_calc['gastos_efectivo_caja']

        efectivo_esperado = (base_inicial + total_efectivo) - gastos_caja
        diferencia = efectivo_fisico_contado - efectivo_esperado

        # Validación: si hay descuadre, requerir justificación
        if abs(diferencia) >= Decimal('1') and not observaciones:
            flash('Debes ingresar una justificación en las observaciones explicando el descuadre de caja.', 'danger')
            return redirect(url_for('arqueo_bp.nuevo', fecha=fecha_str))

        hora_cierre = obtener_hora_bogota()

        nuevo_arqueo = ArqueoCaja(
            vendedor_id=current_user.id,
            fecha_arqueo=fecha_seleccionada,
            base_inicial=base_inicial,
            total_efectivo_sistema=total_efectivo,
            total_transferencia_sistema=total_digital,
            gastos_del_dia=gastos_caja,
            efectivo_fisico_contado=efectivo_fisico_contado,
            diferencia=diferencia,
            observaciones=observaciones,
            observaciones_diferencia=observaciones,
            fecha_cierre=hora_cierre,
            fecha_creacion=hora_cierre
        )

        try:
            db.session.add(nuevo_arqueo)
            db.session.commit()
            flash(f'✅ ¡Caja del día {fecha_str} cerrada y archivada exitosamente!', 'success')
            return redirect(url_for('arqueo_bp.nuevo', fecha=fecha_str))
        except Exception as e:
            db.session.rollback()
            flash(f'Ocurrió un error al registrar el cierre de caja: {str(e)}', 'danger')
            return redirect(url_for('arqueo_bp.nuevo', fecha=fecha_str))

    # Método GET: Consultar datos del día
    datos_calc = calcular_conciliacion_dia(fecha_seleccionada)

    # Si ya está cerrada, usamos los valores congelados del arqueo
    if caja_cerrada:
        base_inicial_val = float(arqueo_existente.base_inicial or 0)
        total_efectivo_val = float(arqueo_existente.total_efectivo_sistema or 0)
        total_digital_val = float(arqueo_existente.total_transferencia_sistema or 0)
        gastos_dia_val = float(arqueo_existente.gastos_del_dia or 0)
        efectivo_fisico_val = float(arqueo_existente.efectivo_fisico_contado or 0)
        diferencia_val = float(arqueo_existente.diferencia if arqueo_existente.diferencia is not None else (efectivo_fisico_val - ((base_inicial_val + total_efectivo_val) - gastos_dia_val)))
        observaciones_val = arqueo_existente.observaciones or arqueo_existente.observaciones_diferencia or ''
    else:
        base_inicial_val = 0.0
        total_efectivo_val = float(datos_calc['total_efectivo'])
        total_digital_val = float(datos_calc['total_digital'])
        gastos_dia_val = float(datos_calc['gastos_efectivo_caja'])
        efectivo_fisico_val = None
        diferencia_val = None
        observaciones_val = ''

    efectivo_esperado_val = (base_inicial_val + total_efectivo_val) - gastos_dia_val
    total_neto_val = (total_efectivo_val + total_digital_val) - gastos_dia_val

    return render_template(
        'arqueo/form.html',
        fecha=fecha_str,
        fecha_obj=fecha_seleccionada,
        ventas=datos_calc['ventas'],
        gastos=datos_calc['gastos'],
        desglose_digital=datos_calc['desglose_digital'],
        total_efectivo=total_efectivo_val,
        total_digital=total_digital_val,
        total_gastos=gastos_dia_val,
        base_inicial=base_inicial_val,
        efectivo_fisico=efectivo_fisico_val,
        efectivo_esperado=efectivo_esperado_val,
        diferencia=diferencia_val,
        total_neto=total_neto_val,
        observaciones=observaciones_val,
        caja_cerrada=caja_cerrada,
        arqueo=arqueo_existente
    )

@arqueo_bp.route('/eliminar/<int:id>', methods=['POST'])
@login_required
@admin_required
def eliminar(id):
    arqueo = ArqueoCaja.query.get_or_404(id)
    fecha_str = arqueo.fecha_arqueo.strftime('%Y-%m-%d')
    
    try:
        db.session.delete(arqueo)
        db.session.commit()
        flash(f'🔓 Cierre de caja del día {fecha_str} anulado. La caja ha sido reabierta para edición.', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'Error al reabrir la caja: {str(e)}', 'danger')

    return redirect(url_for('arqueo_bp.nuevo', fecha=fecha_str))

@arqueo_bp.route('/reporte', methods=['GET'])
@login_required
def reporte():
    fecha_param = request.args.get('fecha')
    if fecha_param:
        fecha_inicio_str = fecha_param
        fecha_fin_str = fecha_param
    else:
        fecha_inicio_str = request.args.get('fecha_inicio', obtener_hora_bogota().strftime('%Y-%m-%d'))
        fecha_fin_str = request.args.get('fecha_fin', obtener_hora_bogota().strftime('%Y-%m-%d'))

    try:
        fecha_inicio = datetime.strptime(fecha_inicio_str, '%Y-%m-%d').date()
        fecha_fin = datetime.strptime(fecha_fin_str, '%Y-%m-%d').date()
    except (ValueError, TypeError):
        fecha_inicio = obtener_hora_bogota().date()
        fecha_fin = obtener_hora_bogota().date()
        fecha_inicio_str = fecha_inicio.strftime('%Y-%m-%d')
        fecha_fin_str = fecha_fin.strftime('%Y-%m-%d')

    # Seguridad: Vendedores solo ven el día de hoy
    if current_user.rol != 'admin':
        hoy = obtener_hora_bogota().date()
        fecha_inicio = hoy
        fecha_fin = hoy
        fecha_inicio_str = hoy.strftime('%Y-%m-%d')
        fecha_fin_str = hoy.strftime('%Y-%m-%d')

    arqueos = ArqueoCaja.query.filter(
        ArqueoCaja.fecha_arqueo >= fecha_inicio,
        ArqueoCaja.fecha_arqueo <= fecha_fin
    ).order_by(ArqueoCaja.fecha_arqueo.desc()).all()

    # Obtener ventas y gastos del periodo
    inicio_dt = datetime.combine(fecha_inicio, datetime.min.time())
    fin_dt = datetime.combine(fecha_fin, datetime.max.time())

    ventas_periodo = Sale.query.filter(
        Sale.fecha_venta >= inicio_dt,
        Sale.fecha_venta <= fin_dt
    ).order_by(Sale.fecha_venta.asc()).all()

    gastos_periodo = Expense.query.filter(
        Expense.fecha_gasto >= inicio_dt,
        Expense.fecha_gasto <= fin_dt
    ).order_by(Expense.fecha_gasto.asc()).all()

    # Totales globales consolidados
    total_base = sum(float(a.base_inicial or 0) for a in arqueos)
    total_efectivo = sum(float(a.total_efectivo_sistema or 0) for a in arqueos)
    total_digital = sum(float(a.total_transferencia_sistema or 0) for a in arqueos)
    total_gastos = sum(float(a.gastos_del_dia or 0) for a in arqueos)
    total_fisico = sum(float(a.efectivo_fisico_contado or 0) for a in arqueos)
    total_diferencia = sum(float(a.diferencia or 0) for a in arqueos)

    resumen = {
        'total_base': total_base,
        'total_efectivo': total_efectivo,
        'total_digital': total_digital,
        'total_gastos': total_gastos,
        'total_fisico': total_fisico,
        'total_diferencia': total_diferencia,
        'total_bruto': total_efectivo + total_digital,
        'total_neto': (total_efectivo + total_digital) - total_gastos,
        'efectivo_esperado': (total_base + total_efectivo) - total_gastos
    }

    fecha_generacion = obtener_hora_bogota().strftime('%Y-%m-%d %H:%M')

    return render_template(
        'arqueo/reporte.html',
        arqueos=arqueos,
        resumen=resumen,
        fecha_inicio=fecha_inicio_str,
        fecha_fin=fecha_fin_str,
        fecha_generacion=fecha_generacion,
        ventas_periodo=ventas_periodo,
        gastos_periodo=gastos_periodo
    )
