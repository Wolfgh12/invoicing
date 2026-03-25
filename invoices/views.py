import os
import random
import calendar
from django.db.models import Q
import json
from django.db.models import Q, Sum
from datetime import datetime, time, date
from django.http import HttpResponse, JsonResponse
from django.core.paginator import Paginator
from django.conf import settings
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.template.loader import get_template
from django.db.models import Q, Sum, F, Count
from django.db.models.functions import ExtractMonth
from django.contrib import messages
from xhtml2pdf import pisa
from decimal import Decimal
from django.core.mail import send_mail, EmailMessage
from django.utils import timezone
from io import BytesIO

# --- OTP Requirement for 2FA (Imported but swapped for @login_required to prevent AttributeErrors) ---
from django_otp.decorators import otp_required

# Ensure all models and forms are imported correctly
# UPDATED: Added ActivityLog to imports
from .models import Invoice, Student, InvoiceItem, Payment, SystemConfiguration, EmailLog, Receipt, ActivityLog
from .forms import InvoiceForm, InvoiceItemFormSet, StudentForm

def link_callback(uri, rel):
    """
    Convert HTML URIs to absolute system paths.
    """
    # Handle Static files
    if uri.startswith(settings.STATIC_URL):
        relative_path = uri.replace(settings.STATIC_URL, "")
        # This builds: C:\Users\Nana\Desktop\invoicing\invoices\static\fonts\DejaVuSans.ttf
        path = os.path.join(settings.BASE_DIR, 'invoices', 'static', relative_path)
    
    # Handle Media files
    elif uri.startswith(settings.MEDIA_URL):
        relative_path = uri.replace(settings.MEDIA_URL, "")
        path = os.path.join(settings.MEDIA_ROOT, relative_path)
    else:
        return uri

    # CRITICAL: Clean up the path for Windows
    path = os.path.abspath(path)

    if not os.path.isfile(path):
        # This will show up in your CMD/Terminal window if it fails
        print(f"--- PISA ERROR: File not found: {path} ---")
        return uri
        
    return path
    

# --- UPDATED DASHBOARD VIEW ---
@login_required 
def dashboard(request):
    query = request.GET.get('q')
    all_invoices = Invoice.objects.all()
    
    # NEW: Fetch the latest 10 activities for the activity feed
    # Added .select_related('user') for better performance
    recent_activities = ActivityLog.objects.select_related('user').all()[:10]
    
    # 1. Identify EVERY unique currency used in your invoices
    used_currencies = all_invoices.values_list('currency', flat=True).distinct()
    
    # 2. Get Paid Totals grouped by currency
    revenue_by_currency = Payment.objects.all().values('invoice__currency').annotate(
        total=Sum('amount')
    ).order_by('invoice__currency')
    
    # Map revenue for quick lookup
    revenue_dict = {
        item['invoice__currency']: item['total'] or Decimal('0.00') 
        for item in revenue_by_currency
    }

    # 3. Build the breakdown for EVERY currency found
    pending_metrics = []
    for curr in used_currencies:
        if not curr:
            continue 
            
        items_total = InvoiceItem.objects.filter(
            invoice__currency=curr
        ).aggregate(
            total=Sum(F('quantity') * F('rate'))
        )['total'] or Decimal('0.00')
        
        fees_total = all_invoices.filter(currency=curr).aggregate(
            total_fees=Sum(F('application_fee') + F('tuition_fee'))
        )['total_fees'] or Decimal('0.00')
        
        total_billed = items_total + fees_total
        total_collected = revenue_dict.get(curr, Decimal('0.00'))
        total_owed = total_billed - total_collected
        
        pending_metrics.append({
            'currency': curr,
            'amount': total_owed,
            'collected': total_collected,
            'total_billed': total_billed
        })

    total_count = all_invoices.count()
    pending_count = all_invoices.filter(is_paid=False).count()

    # 4. Search & Pagination Logic
    qs = all_invoices.select_related('student').order_by('-date_created')
    if query:
        qs = qs.filter(
            Q(student__full_name__icontains=query) | 
            Q(student__index_number__icontains=query) |
            Q(invoice_number__icontains=query)
        ).distinct()
    
    paginator = Paginator(qs, 10) 
    page_number = request.GET.get('page')
    invoices = paginator.get_page(page_number)
        
    return render(request, 'invoices/dashboard.html', {
        'invoices': invoices,
        'query': query,
        'revenue_metrics': revenue_by_currency, 
        'pending_metrics': pending_metrics,     
        'total_count': total_count,
        'pending_count': pending_count,
        'recent_activities': recent_activities, # Pass activities to template
    })

# --- UPDATED FULL HISTORY VIEW ---
import calendar
from django.db.models import Q

@login_required
def activity_log_view(request):
    # 1. SECURITY CHECK: Only allow superusers
    if not request.user.is_superuser:
        # Use the specific message you requested
        messages.error(request, "Sorry you do not have clearance to view this page, visit the dashboard to see all activity logs.")
        return redirect('dashboard')

    # 2. Capture search query
    query = request.GET.get('q', '').strip()
    
    # 3. Base Queryset (Latest first)
    activities_list = ActivityLog.objects.all().select_related('user').order_by('-timestamp')

    # 4. Search Logic
    if query:
        filters = Q(user__username__icontains=query) | \
                  Q(user__first_name__icontains=query) | \
                  Q(user__last_name__icontains=query) | \
                  Q(action__icontains=query)
        
        # Numeric checks for years or days
        if query.isdigit():
            val = int(query)
            if 2000 <= val <= 2100:
                filters |= Q(timestamp__year=val)
            if 1 <= val <= 31:
                filters |= Q(timestamp__day=val)

        activities_list = activities_list.filter(filters).distinct()

    # 5. Pagination
    paginator = Paginator(activities_list, 20)
    page_number = request.GET.get('page')
    activities = paginator.get_page(page_number)
    
    return render(request, 'invoices/activity_log.html', {
        'activities': activities,
        'query': query,
    })

@login_required
def system_activity_log(request):
    activities_list = ActivityLog.objects.all()
    paginator = Paginator(activities_list, 50) # Show 50 per page
    page_number = request.GET.get('page')
    activities = paginator.get_page(page_number)
    return render(request, 'invoices/system_activity_log.html', {'activities': activities})

@login_required 
def reports_view(request):
    # 1. CAPTURE SELECTED CURRENCY (Defaults to GHS)
    selected_currency = request.GET.get('currency', 'GHS')
    
    # Filter base invoices by the selected currency
    all_invoices = Invoice.objects.filter(
        currency=selected_currency
    ).prefetch_related('items')
    
    # 2. Base Metrics (Filtered by Currency)
    total_billed_items = sum(
        ((item.quantity or 0) * (item.rate or 0) for inv in all_invoices for item in inv.items.all())
    )
    fees_total = all_invoices.aggregate(
        total_fees=Sum(F('application_fee') + F('tuition_fee'))
    )['total_fees'] or Decimal('0.00')
    
    total_billed = Decimal(str(total_billed_items)) + fees_total
    
    # Filter payments by the currency of their parent invoice
    total_collected = Payment.objects.filter(
        invoice__currency=selected_currency
    ).aggregate(total=Sum('amount'))['total'] or Decimal('0.00')
    
    outstanding = total_billed - total_collected
    efficiency = (total_collected / total_billed * 100) if total_billed > 0 else 0
    
    # 3. Top Students (Filtered by Currency)
    top_students = Student.objects.filter(
        invoices__currency=selected_currency
    ).annotate(
        revenue=Sum('invoices__payments__amount')
    ).filter(revenue__gt=0).order_by('-revenue')[:5]

    # 4. Collection Velocity
    payments = Payment.objects.filter(
        invoice__currency=selected_currency
    )
    velocity_days = 0
    if payments.exists():
        total_days_diff = 0
        for p in payments:
            diff = (p.invoice.due_date - p.date).days
            total_days_diff += diff
        velocity_days = total_days_diff / payments.count()

    # 5. Heatmap Data (Unpaid items for this currency)
    heatmap_stats = InvoiceItem.objects.filter(
        invoice__is_paid=False,
        invoice__currency=selected_currency
    ).values('description').annotate(
        total_debt=Sum(F('quantity') * F('rate'))
    ).order_by('-total_debt')

    # 6. Service Performance Breakdown
    service_stats = InvoiceItem.objects.filter(
        invoice__currency=selected_currency
    ).values('description').annotate(
        total_value=Sum(F('quantity') * F('rate')),
        usage_count=Count('id')
    ).order_by('-total_value')

    # 7. 30-Day Forecast (Filtered by Currency)
    next_30_days = timezone.now().date() + timezone.timedelta(days=30)
    forecast_qs = Invoice.objects.filter(
        is_paid=False,
        currency=selected_currency,
        due_date__range=[timezone.now().date(), next_30_days]
    ).prefetch_related('items')
    
    expected_inflow_items = sum(
        (item.quantity or 0) * (item.rate or 0) 
        for inv in forecast_qs 
        for item in inv.items.all()
    )
    expected_inflow_fees = forecast_qs.aggregate(
        f=Sum(F('application_fee') + F('tuition_fee'))
    )['f'] or Decimal('0.00')
    
    expected_inflow = Decimal(str(expected_inflow_items)) + expected_inflow_fees

    # 8. Chart Data JSON
    chart_data = {
        'labels': ['Collected', 'Outstanding'],
        'values': [float(total_collected), float(outstanding)],
        'student_names': [s.full_name for s in top_students],
        'student_revenue': [float(s.revenue if s.revenue else 0) for s in top_students],
        'heatmap_labels': [h['description'] for h in heatmap_stats],
        'heatmap_values': [float(h['total_debt']) for h in heatmap_stats],
    }

    # List of available currencies for the template dropdown
    all_currencies = ['GHS', 'USD', 'EUR', 'GBP']

    return render(request, 'invoices/reports.html', {
        'total_billed': total_billed,
        'total_collected': total_collected,
        'outstanding': outstanding,
        'efficiency': efficiency,
        'top_students': top_students,
        'velocity_days': round(velocity_days, 1),
        'expected_inflow': expected_inflow,
        'service_stats': service_stats,
        'chart_data_json': json.dumps(chart_data),
        'selected_currency': selected_currency,
        'all_currencies': all_currencies,
    })

def debt_detail_json(request):
    description = request.GET.get('description') 
    debtors_list = []
    if description:
        unpaid_items = InvoiceItem.objects.filter(
            invoice__is_paid=False,
            description=description
        ).select_related('invoice__student')

        for item in unpaid_items:
            student_name = item.invoice.student.full_name if (item.invoice and item.invoice.student) else "Unknown Student"
            debtors_list.append({
                'student': student_name,
                'owed': float(item.quantity * item.rate)
            })
    return JsonResponse({'debtors': debtors_list})

@login_required
def ledger_list(request):
    # 1. CAPTURE FILTERS (Date and Currency)
    start_date_str = request.GET.get('start_date')
    end_date_str = request.GET.get('end_date')
    selected_currency = request.GET.get('currency', 'GHS') 
    today = timezone.now()
    
    start_date = datetime.strptime(start_date_str, '%Y-%m-%d').date() if start_date_str else None
    end_date = datetime.strptime(end_date_str, '%Y-%m-%d').date() if end_date_str else None

    # --- 2. CALCULATE OPENING BALANCE ---
    opening_balance = Decimal('0')
    if start_date:
        start_datetime = datetime.combine(start_date, time.min)
        prior_invoices = Invoice.objects.filter(
            date_created__lt=start_datetime,
            currency=selected_currency
        ).prefetch_related('items')
        
        prior_billed_items = sum(
            (item.quantity or 0) * (item.rate or 0) 
            for inv in prior_invoices 
            for item in inv.items.all()
        )
        prior_billed_fees = prior_invoices.aggregate(
            f=Sum(F('application_fee') + F('tuition_fee'))
        )['f'] or Decimal('0.00')
        
        prior_billed = Decimal(str(prior_billed_items)) + prior_billed_fees
        
        prior_paid = Payment.objects.filter(
            date__lt=start_date,
            invoice__currency=selected_currency
        ).aggregate(total=Sum('amount'))['total'] or Decimal('0')
        
        opening_balance = prior_billed - prior_paid

    # --- 3. FILTERED DATA ---
    user_invoices = Invoice.objects.filter(
        currency=selected_currency
    ).prefetch_related('items', 'student')

    user_payments = Payment.objects.filter(
        invoice__currency=selected_currency
    ).select_related('invoice', 'invoice__student').order_by('invoice__student__full_name', '-date')

    if start_date:
        start_dt = datetime.combine(start_date, time.min)
        user_invoices = user_invoices.filter(date_created__gte=start_dt)
        user_payments = user_payments.filter(date__gte=start_date)
    if end_date:
        end_dt = datetime.combine(end_date, time.max)
        user_invoices = user_invoices.filter(date_created__lte=end_dt)
        user_payments = user_payments.filter(date__lte=end_date)

    # --- 4. YEAR-OVER-YEAR (YoY) INTELLIGENCE ---
    current_year = today.year
    prev_year = current_year - 1
    
    def get_monthly_totals(year):
        data = Payment.objects.filter(
            date__year=year,
            invoice__currency=selected_currency
        ).annotate(month=ExtractMonth('date'))\
            .values('month')\
            .annotate(total=Sum('amount'))\
            .order_by('month')
        
        monthly_map = {i: 0 for i in range(1, 13)}
        for entry in data:
            monthly_map[entry['month']] = float(entry['total'])
        return list(monthly_map.values())

    yoy_chart_data = {
        'current_year': get_monthly_totals(current_year),
        'prev_year': get_monthly_totals(prev_year),
        'labels': ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
    }

    # --- 5. DATA COMPILATION ---
    total_billed_items = sum(
        (item.quantity or 0) * (item.rate or 0) 
        for inv in user_invoices 
        for item in inv.items.all()
    )
    total_billed_fees = user_invoices.aggregate(
        f=Sum(F('application_fee') + F('tuition_fee'))
    )['f'] or Decimal('0.00')
    
    total_billed = Decimal(str(total_billed_items)) + total_billed_fees
    total_received = user_payments.aggregate(total=Sum('amount'))['total'] or Decimal('0')
    
    entries = []
    for inv in user_invoices:
        student_name = inv.student.full_name if inv.student else "Unknown Student"
        entries.append({
            'date': inv.date_created.date(),
            'reference': inv.invoice_number,
            'description': f"Invoice issued to {student_name}",
            'type': 'INVOICE',
            'amount': inv.grand_total,
            'raw_amount': inv.grand_total 
        })

    for pay in user_payments:
        student_name = pay.invoice.student.full_name if (pay.invoice and pay.invoice.student) else "Unknown Student"
        entries.append({
            'date': pay.date,
            'reference': pay.invoice.invoice_number if pay.invoice else "N/A",
            'description': f"Payment received from {student_name}",
            'type': 'PAYMENT',
            'amount': pay.amount,
            'raw_amount': -pay.amount 
        })

    entries.sort(key=lambda x: x['date'])

    current_bal = opening_balance
    for entry in entries:
        current_bal += Decimal(str(entry['raw_amount']))
        entry['running_balance'] = current_bal

    entries.reverse()

    paginator = Paginator(entries, 15)
    page_number = request.GET.get('page')
    ledger_entries = paginator.get_page(page_number)

    all_currencies = ['GHS', 'USD', 'EUR', 'GBP']

    return render(request, 'invoices/ledger_list.html', {
        'ledger_entries': ledger_entries,
        'start_date': start_date_str,
        'end_date': end_date_str,
        'selected_currency': selected_currency,
        'all_currencies': all_currencies,
        'total_billed': total_billed,
        'total_received': total_received,
        'opening_balance': opening_balance,
        'outstanding_balance': total_billed - total_received,
        'yoy_chart_json': json.dumps(yoy_chart_data),
        'config': SystemConfiguration.objects.first()
    })

@login_required
def generate_invoice_number(request):
    while True:
        random_suffix = random.randint(100000, 999999)
        new_number = f"UGC-{random_suffix}"
        if not Invoice.objects.filter(invoice_number=new_number).exists():
            return JsonResponse({'invoice_number': new_number})

@login_required
def create_invoice(request):
    config = SystemConfiguration.objects.first() or SystemConfiguration.objects.create(id=1)
    
    if request.method == 'POST':
        form = InvoiceForm(request.POST)
        formset = InvoiceItemFormSet(request.POST)
        
        if form.is_valid() and formset.is_valid():
            invoice = form.save(commit=False)
            invoice.user = request.user 
            
            app_fee = form.cleaned_data.get('application_fee') or 0
            tui_fee = form.cleaned_data.get('tuition_fee') or 0
            has_priority_fees = (app_fee > 0 or tui_fee > 0)

            custom_type = request.POST.get('invoice_type')
            if custom_type:
                invoice.invoice_type = custom_type
            
            instructions = request.POST.get('payment_instructions')
            if instructions:
                invoice.payment_instructions = instructions

            invoice.save()
            
            # LOG ACTIVITY: Invoice Created
            ActivityLog.objects.create(
                user=request.user, 
                action=f"Created Invoice {invoice.invoice_number} for {invoice.student.full_name if invoice.student else 'Unknown'}"
            )
            
            instances = formset.save(commit=False)
            for i, instance in enumerate(instances):
                instance.invoice = invoice
                instance.is_recurring = formset.forms[i].cleaned_data.get('is_recurring', False)

                if has_priority_fees:
                    instance.rate = 0
                elif instance.rate is None:
                    instance.rate = 0
                instance.save()
            
            for obj in formset.deleted_objects:
                obj.delete()

            if config.auto_send_email_receipts and invoice.student and invoice.student.email:
                if send_invoice_email(invoice):
                    invoice.mail_sent = True
                    invoice.save()
                    messages.success(request, f"Invoice created and sent to {invoice.student.email}")
                else:
                    messages.error(request, "Invoice created but email failed.")
            else:
                messages.success(request, "Invoice created successfully.")
            
            return redirect('dashboard')
        else:
            messages.error(request, "There was an error. Please check the line items.")
    else:
        form = InvoiceForm(initial={
            'application_fee': None, 
            'tuition_fee': None,
            'payment_instructions': ""
        })
        formset = InvoiceItemFormSet(queryset=InvoiceItem.objects.none())
    
    return render(request, 'invoices/create_invoice.html', {
        'form': form,
        'formset': formset
    })

@login_required
def record_payment(request, invoice_id):
    invoice = get_object_or_404(Invoice, id=invoice_id)
    config = SystemConfiguration.objects.first() or SystemConfiguration.objects.create(id=1) 
    balance_due = invoice.balance_due

    if request.method == 'POST':
        amount_val = Decimal(request.POST.get('amount', 0))
        date_val = request.POST.get('date')
        method_val = request.POST.get('method')
        reference_val = request.POST.get('reference')

        if amount_val <= 0:
            messages.error(request, "Payment amount must be greater than zero.")
        elif amount_val > balance_due:
            messages.error(request, f"Amount exceeds balance due ({invoice.currency} {balance_due})")
        else:
            new_log_entry = f"{date_val}: {invoice.currency} {amount_val} ({method_val.upper()})"
            existing_payment = Payment.objects.filter(invoice=invoice).first()
            
            if existing_payment:
                existing_payment.amount += amount_val 
                existing_payment.date = date_val
                existing_payment.method = method_val
                existing_payment.reference = reference_val
                if existing_payment.payment_log:
                    existing_payment.payment_log += f"\n{new_log_entry}"
                else:
                    existing_payment.payment_log = new_log_entry
                existing_payment.save()
                target_payment = existing_payment
            else:
                target_payment = Payment.objects.create(
                    invoice=invoice,
                    amount=amount_val,
                    date=date_val,
                    method=method_val,
                    reference=reference_val,
                    payment_log=new_log_entry
                )
            
            target_payment.refresh_from_db()
            invoice.refresh_from_db()

            # LOG ACTIVITY: Payment Recorded
            ActivityLog.objects.create(
                user=request.user,
                action=f"Recorded Payment of {invoice.currency} {amount_val} for {invoice.invoice_number}"
            )

            student_name = invoice.student.full_name if invoice.student else "Student"
            receipt_no = target_payment.receipt_number or "N/A"

            if config.auto_send_email_receipts and invoice.student and invoice.student.email:
                send_invoice_email(invoice)
                messages.success(request, f"Recorded {invoice.currency} {amount_val} ({receipt_no}) and receipt sent.")
            else:
                messages.success(request, f"Recorded {invoice.currency} {amount_val} for {student_name}. Receipt: {receipt_no}")
            
            return redirect('dashboard')

    return render(request, 'invoices/payment.html', {
        'invoice': invoice,
        'balance_due': balance_due,
    })

@login_required
def payment_list(request):
    payments = Payment.objects.all().order_by('-date')
    total_revenue = payments.aggregate(total=Sum('amount'))['total'] or Decimal('0.00')
    
    return render(request, 'invoices/payment_list.html', {
        'payments': payments,
        'total_revenue': total_revenue
    })

@login_required
def receipt_list(request):
    payments = Payment.objects.all().select_related('invoice', 'invoice__student').order_by('-date')
    
    q = request.GET.get('q')
    start_date = request.GET.get('start_date')
    end_date = request.GET.get('end_date')

    if q:
        payments = payments.filter(
            Q(receipt_number__icontains=q) | 
            Q(invoice__student__full_name__icontains=q) |
            Q(invoice__invoice_number__icontains=q)
        )
    
    if start_date:
        payments = payments.filter(date__gte=start_date)
    if end_date:
        payments = payments.filter(date__lte=end_date)
    
    total_collected = payments.aggregate(Sum('amount'))['amount__sum'] or Decimal('0.00')
    unique_invoice_ids = payments.values_list('invoice_id', flat=True).distinct()
    invoices_involved = Invoice.objects.filter(id__in=unique_invoice_ids).prefetch_related('items')
    
    total_invoiced_items = sum(
        (item.quantity or 0) * (item.rate or 0) 
        for inv in invoices_involved 
        for item in inv.items.all()
    )
    total_invoiced_fees = invoices_involved.aggregate(
        f=Sum(F('application_fee') + F('tuition_fee'))
    )['f'] or Decimal('0.00')
    
    total_invoiced = Decimal(str(total_invoiced_items)) + total_invoiced_fees
    balance_owed = total_invoiced - total_collected

    return render(request, 'invoices/receipt_list.html', {
        'payments': payments,
        'total_collected': total_collected,
        'total_invoiced': total_invoiced,
        'balance_owed': balance_owed,
        'request': request 
    })

@login_required
def generate_pdf(request, invoice_id):
    invoice = get_object_or_404(Invoice, id=invoice_id)
    config = SystemConfiguration.objects.first()
    logo_path = os.path.join(settings.BASE_DIR, 'invoices', 'static', '1.png')

    symbols = {'GHS': 'GHS', 'USD': '$', 'EUR': '€', 'GBP': '£'}
    currency_symbol = symbols.get(invoice.currency, 'GHS')
    
    context = {
        'invoice': invoice, 
        'logo_path': logo_path,
        'config': config,
        'generated_at': timezone.now(),
        'user': request.user,
        'exchange_rate': Decimal('1.0'),
        'currency_symbol': currency_symbol,
        'payment_instructions': invoice.payment_instructions,
    }
    
    response = HttpResponse(content_type='application/pdf')
    response['Content-Disposition'] = f'attachment; filename="Invoice_{invoice.invoice_number}.pdf"'
    
    template = get_template('invoices/pdf_template.html')
    html = template.render(context)
    pisa_status = pisa.CreatePDF(html, dest=response, link_callback=link_callback)
    
    if pisa_status.err:
        return HttpResponse('Error generating PDF')
    return response

@login_required
def generate_receipt_pdf(request, payment_id):
    payment = get_object_or_404(Payment, id=payment_id)
    config = SystemConfiguration.objects.first()
    logo_path = os.path.join(settings.BASE_DIR, 'invoices', 'static', '1.png')

    symbols = {'GHS': 'GH₵', 'USD': '$', 'EUR': '€', 'GBP': '£'}
    selected_currency = payment.invoice.currency if payment.invoice else 'GHS'
    currency_symbol = symbols.get(selected_currency, selected_currency)
    
    context = {
        'payment': payment,
        'invoice': payment.invoice,
        'logo_path': logo_path,
        'config': config,
        'generated_at': timezone.now(),
        'user': request.user,
        'currency_symbol': currency_symbol,
        'exchange_rate': Decimal('1.0'),
    }
    
    filename = f"Receipt_{payment.receipt_number or 'N/A'}.pdf"
    response = HttpResponse(content_type='application/pdf')
    response['Content-Disposition'] = f'attachment; filename="{filename}"'
    
    template = get_template('invoices/receipt_pdf_template.html')
    html = template.render(context)
    pisa_status = pisa.CreatePDF(html, dest=response, link_callback=link_callback)
    
    if pisa_status.err:
        return HttpResponse('Error generating Receipt PDF')
    return response

@login_required
def delete_invoice(request, invoice_id):
    invoice = get_object_or_404(Invoice, id=invoice_id)
    if request.method == 'POST':
        invoice_num = invoice.invoice_number
        invoice.delete()
        # LOG ACTIVITY: Invoice Deleted
        ActivityLog.objects.create(user=request.user, action=f"Deleted Invoice {invoice_num}")
        messages.success(request, "Invoice deleted successfully.")
    return redirect('dashboard')@login_required
def delete_invoice(request, invoice_id):
    invoice = get_object_or_404(Invoice, id=invoice_id)
    if request.method == 'POST':
        # Capture the number first so it's available for the log after deletion
        invoice_num = invoice.invoice_number
        invoice.delete()
        
        # Log the activity using the captured number
        ActivityLog.objects.create(
            user=request.user, 
            action=f"Deleted Invoice {invoice_num}"
        )
        
        messages.success(request, f"Invoice {invoice_num} deleted successfully.")
    return redirect('dashboard')

@login_required
def student_list(request):
    query = request.GET.get('q')
    students = Student.objects.all().order_by('full_name')
    if query:
        students = students.filter(
            Q(full_name__icontains=query) | Q(index_number__icontains=query) | Q(email__icontains=query)
        ).distinct()
    
    return render(request, 'invoices/student_list.html', {
        'students': students,
        'query': query,
        'student_count': students.count()
    })

@login_required
def add_student(request):
    if request.method == 'POST':
        form = StudentForm(request.POST, request.FILES)
        if form.is_valid():
            student = form.save()
            # LOG ACTIVITY: Student Added
            ActivityLog.objects.create(user=request.user, action=f"Added Student: {student.full_name}")
            messages.success(request, "Student added successfully.")
            return redirect('student_list')
    else:
        form = StudentForm()
    return render(request, 'invoices/add_student.html', {'form': form})

@login_required
def edit_student(request, student_id):
    student = get_object_or_404(Student, id=student_id)
    if request.method == 'POST':
        form = StudentForm(request.POST, request.FILES, instance=student)
        if form.is_valid():
            form.save()
            # LOG ACTIVITY: Student Edited
            ActivityLog.objects.create(user=request.user, action=f"Edited details for Student: {student.full_name}")
            messages.success(request, "Student updated successfully.")
            return redirect('student_list')
    else:
        form = StudentForm(instance=student)
    return render(request, 'invoices/add_student.html', {'form': form, 'edit_mode': True, 'student': student})

@login_required
def delete_student(request, student_id):
    student = get_object_or_404(Student, id=student_id)
    if request.method == 'POST':
        student_name = student.full_name
        student.delete()
        # LOG ACTIVITY: Student Deleted
        ActivityLog.objects.create(user=request.user, action=f"Deleted Student: {student_name}")
        messages.success(request, "Student deleted successfully.")
    return redirect('student_list')

@login_required
def student_detail(request, student_id):
    student = get_object_or_404(Student, id=student_id)
    
    # 1. Define the master list of currencies you support
    all_supported_currencies = ['GHS', 'USD', 'EUR', 'GBP']
    
    # 2. Capture selected currency (default to GHS)
    selected_currency = request.GET.get('currency', 'GHS')
    
    # 3. Filter Invoices and Payments by the student AND the selected currency
    inv_qs = student.invoices.filter(currency=selected_currency).prefetch_related('items')
    payments_qs = Payment.objects.filter(
        invoice__student=student, 
        invoice__currency=selected_currency
    ).order_by('-date')

    # 4. Calculate Aggregates
    total_billed_items = sum(
        (item.quantity or 0) * (item.rate or 0) 
        for inv in inv_qs 
        for item in inv.items.all()
    )
    total_billed_fees = inv_qs.aggregate(
        f=Sum(F('application_fee') + F('tuition_fee'))
    )['f'] or Decimal('0.00')
    
    total_billed = Decimal(str(total_billed_items)) + total_billed_fees
    total_paid = payments_qs.aggregate(total=Sum('amount'))['total'] or Decimal('0.00')
    balance_due = total_billed - total_paid

    # 5. Check if data exists for this specific selection
    has_data = inv_qs.exists() or payments_qs.exists()

    invoices = inv_qs.order_by('-date_created')
    payments_with_logs = []
    for pmt in payments_qs:
        payments_with_logs.append({'payment': pmt, 'log': pmt.payment_log or ""})

    return render(request, 'invoices/student_detail.html', {
        'student': student,
        'total_billed': total_billed,
        'total_paid': total_paid,
        'balance_due': balance_due,
        'invoices': invoices,
        'payments_with_logs': payments_with_logs,
        'selected_currency': selected_currency,
        'all_supported_currencies': all_supported_currencies, # Always passed
        'has_data': has_data, # Used for the empty state message
    })

@login_required
def settings_view(request):
    config = SystemConfiguration.objects.first()
    if not config:
        config = SystemConfiguration.objects.create(id=1, institution_name="My Institution")
    
    if request.method == 'POST':
        config.institution_name = request.POST.get('institution_name')
        config.institution_email = request.POST.get('institution_email')
        config.institution_address = request.POST.get('institution_address')
        config.base_currency = request.POST.get('base_currency')
        config.default_payment_instructions = request.POST.get('default_payment_instructions')
        config.auto_generate_ledger = 'auto_ledger' in request.POST
        config.auto_send_email_receipts = 'auto_receipt' in request.POST
        if 'logo' in request.FILES:
            config.logo = request.FILES['logo']
        config.save()
        # LOG ACTIVITY: Settings Updated
        ActivityLog.objects.create(user=request.user, action="Updated System Settings")
        messages.success(request, "Settings updated successfully.")
        return redirect('settings_view')

    return render(request, 'invoices/settings.html', {'config': config})

def send_invoice_email(invoice):
    try:
        if not invoice.student or not invoice.student.email:
            return False
        student_name = invoice.student.full_name or "Student"
        curr_code = invoice.currency
        subject = f"Invoice {invoice.invoice_number} from UGC"
        message_body = (
            f"Hello {student_name},\n\n"
            f"Your invoice {invoice.invoice_number} for {curr_code} {invoice.grand_total} has been generated.\n\n"
            f"Please log in to the portal to make a payment.\n\n"
            f"Thank you."
        )
        send_mail(subject, message_body, settings.DEFAULT_FROM_EMAIL, [invoice.student.email], fail_silently=False)
        EmailLog.objects.create(student=invoice.student, subject=subject, message=message_body, status="Sent")
        return True
    except Exception as e:
        print(f"EMAIL ERROR: {e}")
        return False

@login_required
def mailing_view(request):
    delete_id = request.GET.get('delete_pending')
    if delete_id:
        pending_inv = get_object_or_404(Invoice, id=delete_id)
        pending_inv.mail_sent = True 
        pending_inv.save()
        messages.success(request, "Message removed from Pending Dispatch.")
        return redirect('mailing_center')

    if request.GET.get('send_all') == 'true':
        pending = Invoice.objects.filter(mail_sent=False)
        sent_count = 0
        for inv in pending:
            if send_invoice_email(inv):
                inv.mail_sent = True
                inv.save()
                sent_count += 1
        messages.success(request, f"Batch complete. {sent_count} emails sent.")
        return redirect('mailing_center')

    send_id = request.GET.get('send_invoice')
    if send_id:
        invoice_to_send = get_object_or_404(Invoice, id=send_id)
        if send_invoice_email(invoice_to_send):
            invoice_to_send.mail_sent = True
            invoice_to_send.save()
            messages.success(request, f"Email sent to {invoice_to_send.student.full_name}")
        else:
            messages.error(request, "Failed to send email.")
        return redirect('mailing_center')

    # UPDATED: Fetching students and defining currencies for the modal dropdowns
    pending_invoices = Invoice.objects.filter(mail_sent=False).order_by('-date_created')
    email_history = EmailLog.objects.all().order_by('-date_sent')
    students = Student.objects.all().order_by('full_name')
    all_currencies = ['GHS', 'USD', 'EUR', 'GBP'] 
    
    return render(request, 'invoices/mailing.html', {
        'pending_invoices': pending_invoices, 
        'email_history': email_history, 
        'students': students,
        'all_currencies': all_currencies
    })

    pending_invoices = Invoice.objects.filter(mail_sent=False).order_by('-date_created')
    email_history = EmailLog.objects.all().order_by('-date_sent')
    students = Student.objects.all().order_by('full_name')
    return render(request, 'invoices/mailing.html', {'pending_invoices': pending_invoices, 'email_history': email_history, 'students': students})

@login_required
def delete_email_log(request, log_id):
    log = get_object_or_404(EmailLog, id=log_id)
    if request.method == 'POST':
        log.delete()
        messages.success(request, "Email record deleted.")
    return redirect('mailing_center')

@login_required
def bulk_delete_invoices(request):
    if request.method == 'POST':
        invoice_ids = request.POST.getlist('invoice_ids')
        if invoice_ids:
            # 1. Fetch the invoice numbers before they are deleted
            # This ensures we have the data for the Activity Log
            invoices_to_delete = Invoice.objects.filter(id__in=invoice_ids)
            invoice_numbers = list(invoices_to_delete.values_list('invoice_number', flat=True))
            count = len(invoice_numbers)
            
            # 2. Perform the deletion
            invoices_to_delete.delete()
            
            # 3. Log the specific details
            details = ", ".join(invoice_numbers[:5]) # List first 5 for brevity
            if count > 5:
                details += "..."
                
            ActivityLog.objects.create(
                user=request.user, 
                action=f"Bulk deleted {count} invoices: {details}"
            )
            
            messages.success(request, f"Successfully deleted {count} selected invoices.")
    return redirect('dashboard')

@login_required
def clear_all_logs(request):
    if request.method == 'POST':
        EmailLog.objects.all().delete()
        messages.success(request, "Mailing history cleared.")
    return redirect('mailing_center')

@login_required
def bulk_delete_students(request):
    if request.method == 'POST':
        student_ids = request.POST.getlist('student_ids')
        if student_ids:
            # Count for the message and log
            count = Student.objects.filter(id__in=student_ids).count()
            Student.objects.filter(id__in=student_ids).delete()
            
            # Log the activity
            ActivityLog.objects.create(
                user=request.user,
                category='DELETE',
                action=f"Bulk deleted {count} students"
            )
            messages.success(request, f"Successfully deleted {count} students.")
        else:
            messages.warning(request, "No students were selected for deletion.")
            
    return redirect('student_list')

@login_required
def bulk_delete_logs(request):
    if request.method == 'POST':
        log_ids = request.POST.getlist('log_ids')
        if log_ids:
            logs_to_delete = ActivityLog.objects.filter(id__in=log_ids)
            count = logs_to_delete.count()
            logs_to_delete.delete()
            
            # Log that we deleted logs (ironic, but good for auditing)
            ActivityLog.objects.create(
                user=request.user, 
                action=f"Bulk deleted {count} activity log entries."
            )
            
            messages.success(request, f"Successfully deleted {count} log entries.")
    return redirect('activity_log') # Redirect back to the pulse page

@login_required
def compose_email(request, student_id=None):
    # Support both URL parameter and POST data for student_id
    if request.method == 'POST' and not student_id:
        student_id = request.POST.get('student_id')
        
    if not student_id:
        messages.error(request, "No student selected.")
        return redirect('mailing_center')

    student = get_object_or_404(Student, id=student_id)
    config = SystemConfiguration.objects.first()
    logo_path = os.path.join(settings.BASE_DIR, 'invoices', 'static', '1.png')

    if request.method == 'POST':
        subject = request.POST.get('subject')
        message_body = request.POST.get('message')
        attach_type = request.POST.get('attachment_type')
        cc_email = request.POST.get('cc_email')
        # NEW: Capture selected currency from the dropdown
        selected_currency = request.POST.get('currency', 'GHS')
        
        cc_list = [cc_email] if cc_email else []
        email = EmailMessage(subject, message_body, settings.DEFAULT_FROM_EMAIL, [student.email], cc=cc_list)
        log_type, attach_name = 'MANUAL', None
        symbols = {'GHS': 'GHS', 'USD': '$', 'EUR': '€', 'GBP': '£'}
        curr_symbol = symbols.get(selected_currency, 'GHS')

        # NEW: Validation Logic for Invoices
        if attach_type == 'invoice':
            latest_inv = student.invoices.filter(currency=selected_currency).first()
            if not latest_inv:
                messages.error(request, f"This student has no invoices in {selected_currency}.")
                return redirect('mailing_center')
            
            template = get_template('invoices/pdf_template.html')
            html = template.render({
                'invoice': latest_inv, 
                'logo_path': logo_path, 
                'config': config, 
                'generated_at': timezone.now(),
                'user': request.user, 
                'currency_symbol': curr_symbol,
                'exchange_rate': Decimal('1.0'), 
                'payment_instructions': latest_inv.payment_instructions,
            })
            pdf_output = BytesIO()
            pisa.CreatePDF(html, dest=pdf_output, link_callback=link_callback)
            email.attach(f"Invoice_{latest_inv.invoice_number}.pdf", pdf_output.getvalue(), 'application/pdf')
            log_type, attach_name = 'INVOICE', f"Invoice_{latest_inv.invoice_number}.pdf"
        
        # NEW: Validation Logic for Receipts
        elif attach_type == 'receipt':
            latest_pay = Payment.objects.filter(
                invoice__student=student, 
                invoice__currency=selected_currency
            ).order_by('-date').first()
            
            if not latest_pay:
                messages.error(request, f"This student has no receipts/payments in {selected_currency}.")
                return redirect('mailing_center')
                
            pay_symbol = symbols.get(selected_currency, curr_symbol)
            template = get_template('invoices/receipt_pdf_template.html')
            html = template.render({
                'payment': latest_pay, 
                'invoice': latest_pay.invoice, 
                'logo_path': logo_path, 
                'config': config, 
                'generated_at': timezone.now(),
                'user': request.user,
                'currency_symbol': pay_symbol,
                'exchange_rate': Decimal('1.0')
            })
            pdf_output = BytesIO()
            pisa.CreatePDF(html, dest=pdf_output, link_callback=link_callback)
            receipt_no = latest_pay.receipt_number or "N/A"
            email.attach(f"Receipt_{receipt_no}.pdf", pdf_output.getvalue(), 'application/pdf')
            log_type, attach_name = 'RECEIPT', f"Receipt_{receipt_no}.pdf"

        try:
            email.send()
            EmailLog.objects.create(
                student=student, 
                subject=subject, 
                message=message_body, 
                email_type=log_type, 
                attachment_name=attach_name, 
                status="Sent"
            )
            ActivityLog.objects.create(user=request.user, action=f"Sent Custom Email to {student.full_name}")
            messages.success(request, f"Email sent successfully to {student.email}.")
        except Exception as e:
            messages.error(request, f"Failed to send email: {e}")
        return redirect('mailing_center')
        
    return render(request, 'invoices/compose_email.html', {'student': student})

@login_required
def send_invoice_pdf_email(request, invoice_id):
    invoice = get_object_or_404(Invoice, id=invoice_id)
    if not invoice.student or not invoice.student.email:
        messages.error(request, "Student has no email address.")
        return redirect('mailing_center')

    selected_currency = invoice.currency
    symbols = {'GHS': 'GHS', 'USD': '$', 'EUR': '€', 'GBP': '£'}
    curr_symbol = symbols.get(selected_currency, 'GHS')

    config = SystemConfiguration.objects.first()
    logo_path = os.path.join(settings.BASE_DIR, 'invoices', 'static', '1.png')
    template = get_template('invoices/pdf_template.html')
    
    # FIX: Added 'user': request.user to the context dictionary
    html = template.render({
        'invoice': invoice, 
        'logo_path': logo_path, 
        'config': config, 
        'generated_at': timezone.now(),
        'user': request.user,  # This allows {{ user.username }} to work in the PDF
        'currency_symbol': curr_symbol,
        'exchange_rate': Decimal('1.0'),
        'payment_instructions': invoice.payment_instructions,
    })
    
    pdf_output = BytesIO()
    pisa.CreatePDF(html, dest=pdf_output, link_callback=link_callback)
    
    email = EmailMessage(
        f"Invoice: {invoice.invoice_number}", 
        f"Hello, invoice attached in {selected_currency}.", 
        settings.DEFAULT_FROM_EMAIL, 
        [invoice.student.email]
    )
    email.attach(f"Invoice_{invoice.invoice_number}.pdf", pdf_output.getvalue(), 'application/pdf')
    
    try:
        email.send()
        invoice.mail_sent = True
        invoice.save()
        EmailLog.objects.create(
            student=invoice.student, 
            subject=f"Invoice: {invoice.invoice_number} ({selected_currency})", 
            status="Sent"
        )
        messages.success(request, f"Invoice PDF sent in {selected_currency}!")
    except Exception as e:
        messages.error(request, f"Error: {e}")
        
    return redirect('mailing_center')

@login_required
def send_receipt_pdf_email(request, payment_id):
    payment = get_object_or_404(Payment, id=payment_id)
    if not payment.invoice or not payment.invoice.student or not payment.invoice.student.email:
        messages.error(request, "Student email not found.")
        return redirect('mailing_center')

    selected_currency = payment.invoice.currency
    symbols = {'GHS': 'GHS', 'USD': '$', 'EUR': '€', 'GBP': '£'}
    curr_symbol = symbols.get(selected_currency, 'GHS')

    config = SystemConfiguration.objects.first()
    logo_path = os.path.join(settings.BASE_DIR, 'invoices', 'static', '1.png')
    template = get_template('invoices/receipt_pdf_template.html')
    
    html = template.render({
        'payment': payment, 
        'invoice': payment.invoice, 
        'logo_path': logo_path, 
        'config': config, 
        'generated_at': timezone.now(),
        'currency_symbol': curr_symbol,
        'exchange_rate': Decimal('1.0'),
        'user': request.user
    })
    
    pdf_output = BytesIO()
    pisa.CreatePDF(html, dest=pdf_output, link_callback=link_callback)
    
    receipt_no = payment.receipt_number or "N/A"
    email = EmailMessage(f"Receipt: {receipt_no}", f"Receipt attached in {selected_currency}.", settings.DEFAULT_FROM_EMAIL, [payment.invoice.student.email])
    email.attach(f"Receipt_{receipt_no}.pdf", pdf_output.getvalue(), 'application/pdf')
    try:
        email.send()
        EmailLog.objects.create(student=payment.invoice.student, subject=f"Receipt: {receipt_no} ({selected_currency})", status="Sent")
        messages.success(request, f"Receipt PDF sent in {selected_currency}!")
    except Exception as e:
        messages.error(request, f"Error: {e}")
    return redirect('mailing_center')

@login_required
def export_report_pdf(request):
    config = SystemConfiguration.objects.first()
    logo_path = os.path.join(settings.BASE_DIR, 'invoices', 'static', '1.png')
    
    selected_currency = request.GET.get('currency', 'GHS')
    all_invoices = Invoice.objects.filter(currency=selected_currency).prefetch_related('items')
    
    total_billed_items = sum(
        ((item.quantity or 0) * (item.rate or 0) for inv in all_invoices for item in inv.items.all())
    )
    total_billed_fees = all_invoices.aggregate(
        f=Sum(F('application_fee') + F('tuition_fee'))
    )['f'] or Decimal('0.00')
    
    total_billed = Decimal(str(total_billed_items)) + total_billed_fees
    total_collected = Payment.objects.filter(invoice__currency=selected_currency).aggregate(total=Sum('amount'))['total'] or Decimal('0.00')
    
    outstanding = total_billed - total_collected
    efficiency = (total_collected / total_billed * 100) if total_billed > 0 else 0
    
    top_students = Student.objects.filter(
        invoices__currency=selected_currency
    ).annotate(
        revenue=Sum('invoices__payments__amount')
    ).filter(revenue__gt=0).order_by('-revenue')[:10]
    
    service_stats = InvoiceItem.objects.filter(
        invoice__currency=selected_currency
    ).values('description').annotate(
        total_value=Sum(F('quantity') * F('rate')), 
        usage_count=Count('id')
    ).order_by('-total_value')

    context = {
        'total_billed': total_billed, 
        'total_collected': total_collected, 
        'outstanding': outstanding, 
        'efficiency': efficiency, 
        'top_students': top_students, 
        'service_stats': service_stats, 
        'config': config, 
        'logo_path': logo_path, 
        'generated_at': timezone.now(),
        'user': request.user,
        'selected_currency': selected_currency, 
    }

    response = HttpResponse(content_type='application/pdf')
    response['Content-Disposition'] = f'attachment; filename="Financial_Report_{selected_currency}_{timezone.now().date()}.pdf"'
    
    template = get_template('invoices/report_pdf_template.html')
    html = template.render(context)
    pisa_status = pisa.CreatePDF(html, dest=response, link_callback=link_callback)
    
    if pisa_status.err:
        return HttpResponse('Error generating Report PDF')
    return response

## --- CORRECTED ACTIVITY LOG VIEW ---
@login_required
def activity_log_view(request):
    # 1. Capture and clean the query
    query = request.GET.get('q', '').strip()
    
    # 2. Start with the base queryset
    activities_list = ActivityLog.objects.all().select_related('user').order_by('-timestamp')

    # 3. Apply Filtering Logic
    if query:
        # Basic Text Filters (User, Email, Action)
        filters = Q(user__username__icontains=query) | \
                  Q(user__first_name__icontains=query) | \
                  Q(user__last_name__icontains=query) | \
                  Q(user__email__icontains=query) | \
                  Q(action__icontains=query)

        # Month Filtering (e.g., "March")
        month_names = {name.lower(): i for i, name in enumerate(calendar.month_name) if name}
        if query.lower() in month_names:
            filters |= Q(timestamp__month=month_names[query.lower()])

        # Day of Week Filtering (e.g., "Monday")
        day_names = {name.lower(): i for i, name in enumerate(calendar.day_name) if name}
        if query.lower() in day_names:
            django_day = (list(calendar.day_name).index(query.capitalize()) + 2) % 7
            if django_day == 0: django_day = 7
            filters |= Q(timestamp__week_day=django_day)

        # Numeric Filtering (Year, Day of Month, or Hour)
        if query.isdigit():
            val = int(query)
            if 1 <= val <= 31: 
                filters |= Q(timestamp__day=val)
            if 2000 <= val <= 2100: 
                filters |= Q(timestamp__year=val)
            if 0 <= val <= 23: 
                filters |= Q(timestamp__hour=val)

        # Apply all filters at once
        activities_list = activities_list.filter(filters).distinct()

    # 4. Pagination
    paginator = Paginator(activities_list, 20)
    page_number = request.GET.get('page')
    activities = paginator.get_page(page_number)
    
    # 5. Return with search context
    return render(request, 'invoices/activity_log.html', {
        'activities': activities,
        'query': query,
        'results_count': activities_list.count() # Useful for the UI
    })

# Keep this helper at the very bottom
def get_daisy_alert_class(level):
    if level == messages.SUCCESS: return "alert-success"
    if level == messages.ERROR: return "alert-error"
    if level == messages.WARNING: return "alert-warning"
    return "alert-info"
