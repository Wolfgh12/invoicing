import os
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

# Ensure all models and forms are imported correctly
from .models import Invoice, Student, InvoiceItem, Payment, SystemConfiguration, EmailLog, Receipt
from .forms import InvoiceForm, InvoiceItemFormSet, StudentForm

@login_required
def dashboard(request):
    query = request.GET.get('q')
    
    # 1. Base queryset (keep this name as all_invoices so metrics work)
    all_invoices = Invoice.objects.filter(user=request.user)
    
    # Dashboard Metrics Calculations
    total_revenue = Payment.objects.filter(
        invoice__user=request.user
    ).aggregate(total=Sum('amount'))['total'] or Decimal('0.00')

    # Fixed calculation to avoid SQLite casting errors
    total_billed = sum(
        (item.quantity or 0) * (item.rate or 0) 
        for inv in all_invoices.prefetch_related('items') 
        for item in inv.items.all()
    )
    total_billed = Decimal(str(total_billed))

    pending_amount = total_billed - total_revenue
    total_count = all_invoices.count()
    pending_count = all_invoices.filter(is_paid=False).count()

    # 2. Search & List Logic
    qs = all_invoices.select_related('student').order_by('-date_created')
    
    if query:
        qs = qs.filter(
            Q(student__full_name__icontains=query) | 
            Q(student__index_number__icontains=query) |
            Q(invoice_number__icontains=query)
        ).distinct()
    
    # --- PAGINATION LOGIC ---
    paginator = Paginator(qs, 10) 
    page_number = request.GET.get('page')
    invoices = paginator.get_page(page_number)
        
    return render(request, 'invoices/dashboard.html', {
        'invoices': invoices,
        'query': query,
        'total_revenue': total_revenue,
        'pending_amount': pending_amount,
        'total_count': total_count,
        'pending_count': pending_count,
    })

@login_required
def reports_view(request):
    all_invoices = Invoice.objects.filter(user=request.user).prefetch_related('items')
    
    # 1. Base Metrics (Python-side summation to avoid SQLite 0/None errors)
    total_billed = sum(
        (item.quantity or 0) * (item.rate or 0) 
        for inv in all_invoices 
        for item in inv.items.all()
    )
    total_billed = Decimal(str(total_billed))
    
    total_collected = Payment.objects.filter(
        invoice__user=request.user
    ).aggregate(total=Sum('amount'))['total'] or Decimal('0.00')
    
    outstanding = total_billed - total_collected
    efficiency = (total_collected / total_billed * 100) if total_billed > 0 else 0
    
    # 2. Top Students
    top_students = Student.objects.filter(
        invoices__user=request.user
    ).annotate(
        revenue=Sum('invoices__payments__amount')
    ).filter(revenue__gt=0).order_by('-revenue')[:5]

    # 3. Collection Velocity (Days Early/Late)
    payments = Payment.objects.filter(invoice__user=request.user)
    velocity_days = 0
    if payments.exists():
        total_days_diff = 0
        for p in payments:
            diff = (p.invoice.due_date - p.date).days
            total_days_diff += diff
        velocity_days = total_days_diff / payments.count()

    # 4. Heatmap Data (Unpaid items)
    heatmap_stats = InvoiceItem.objects.filter(
        invoice__user=request.user,
        invoice__is_paid=False
    ).values('description').annotate(
        total_debt=Sum(F('quantity') * F('rate'))
    ).order_by('-total_debt')

    # 5. Service Performance Breakdown
    service_stats = InvoiceItem.objects.filter(
        invoice__user=request.user
    ).values('description').annotate(
        total_value=Sum(F('quantity') * F('rate')),
        usage_count=Count('id')
    ).order_by('-total_value')

    # 6. 30-Day Forecast
    next_30_days = timezone.now().date() + timezone.timedelta(days=30)
    forecast_qs = Invoice.objects.filter(
        user=request.user,
        is_paid=False,
        due_date__range=[timezone.now().date(), next_30_days]
    ).prefetch_related('items')
    
    expected_inflow = sum(
        (item.quantity or 0) * (item.rate or 0) 
        for inv in forecast_qs 
        for item in inv.items.all()
    )

    # 7. Chart Data JSON
    chart_data = {
        'labels': ['Collected', 'Outstanding'],
        'values': [float(total_collected), float(outstanding)],
        'student_names': [s.full_name for s in top_students],
        'student_revenue': [float(s.revenue if s.revenue else 0) for s in top_students],
        'heatmap_labels': [h['description'] for h in heatmap_stats],
        'heatmap_values': [float(h['total_debt']) for h in heatmap_stats],
    }

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
    })

def debt_detail_json(request):
    description = request.GET.get('description') 
    debtors_list = []
    if description:
        unpaid_items = InvoiceItem.objects.filter(
            invoice__user=request.user,
            invoice__is_paid=False,
            description=description
        ).select_related('invoice__student')

        for item in unpaid_items:
            # FIX: Check if student exists before accessing name
            student_name = item.invoice.student.full_name if (item.invoice and item.invoice.student) else "Unknown Student"
            debtors_list.append({
                'student': student_name,
                'owed': float(item.quantity * item.rate)
            })
    return JsonResponse({'debtors': debtors_list})

@login_required
def ledger_list(request):
    start_date_str = request.GET.get('start_date')
    end_date_str = request.GET.get('end_date')
    today = timezone.now()
    
    start_date = datetime.strptime(start_date_str, '%Y-%m-%d').date() if start_date_str else None
    end_date = datetime.strptime(end_date_str, '%Y-%m-%d').date() if end_date_str else None

    # --- 1. CALCULATE OPENING BALANCE ---
    opening_balance = Decimal('0')
    if start_date:
        start_datetime = datetime.combine(start_date, time.min)
        prior_invoices = Invoice.objects.filter(user=request.user, date_created__lt=start_datetime).prefetch_related('items')
        
        prior_billed = sum(
            (item.quantity or 0) * (item.rate or 0) 
            for inv in prior_invoices 
            for item in inv.items.all()
        )
        
        prior_paid = Payment.objects.filter(
            invoice__user=request.user, 
            date__lt=start_date
        ).aggregate(total=Sum('amount'))['total'] or Decimal('0')
        
        opening_balance = Decimal(str(prior_billed)) - prior_paid

    # --- 2. FILTERED DATA ---
    user_invoices = Invoice.objects.filter(user=request.user).prefetch_related('items', 'student')
    user_payments = Payment.objects.filter(invoice__user=request.user).select_related('invoice', 'invoice__student').order_by('invoice__student__full_name', '-date')

    if start_date:
        start_dt = datetime.combine(start_date, time.min)
        user_invoices = user_invoices.filter(date_created__gte=start_dt)
        user_payments = user_payments.filter(date__gte=start_date)
    if end_date:
        end_dt = datetime.combine(end_date, time.max)
        user_invoices = user_invoices.filter(date_created__lte=end_dt)
        user_payments = user_payments.filter(date__lte=end_date)

    # --- 3. YEAR-OVER-YEAR (YoY) INTELLIGENCE ---
    current_year = today.year
    prev_year = current_year - 1
    
    def get_monthly_totals(year):
        data = Payment.objects.filter(invoice__user=request.user, date__year=year)\
            .annotate(month=ExtractMonth('date'))\
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

    # --- 4. DATA COMPILATION ---
    total_billed = sum(
        (item.quantity or 0) * (item.rate or 0) 
        for inv in user_invoices 
        for item in inv.items.all()
    )
    total_billed = Decimal(str(total_billed))
    total_received = user_payments.aggregate(total=Sum('amount'))['total'] or Decimal('0')
    
    entries = []
    for inv in user_invoices:
        # FIX: Safety check for student name
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
        # FIX: Safety check for student name via invoice
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

    return render(request, 'invoices/ledger_list.html', {
        'ledger_entries': ledger_entries,
        'start_date': start_date_str,
        'end_date': end_date_str,
        'total_billed': total_billed,
        'total_received': total_received,
        'opening_balance': opening_balance,
        'outstanding_balance': total_billed - total_received,
        'yoy_chart_json': json.dumps(yoy_chart_data),
        'config': SystemConfiguration.objects.first()
    })

@login_required
def create_invoice(request):
    config = SystemConfiguration.objects.first() or SystemConfiguration.objects.create(id=1)
    
    if request.method == 'POST':
        form = InvoiceForm(request.POST)
        formset = InvoiceItemFormSet(request.POST)
        
        if form.is_valid() and formset.is_valid():
            invoice = form.save(commit=False)
            invoice.user = request.user 
            
            # --- NEW LOGIC TO CAPTURE DYNAMIC TYPE ---
            # Explicitly grab the value set by your JavaScript from the POST data
            custom_type = request.POST.get('invoice_type')
            if custom_type:
                invoice.invoice_type = custom_type
            # -----------------------------------------

            invoice.save()
            
            formset.instance = invoice
            formset.save()

            if config.auto_send_email_receipts and invoice.student and invoice.student.email:
                if send_invoice_email(invoice):
                    invoice.mail_sent = True
                    invoice.save()
                    messages.success(request, f"Invoice created and automatically sent to {invoice.student.email}")
                else:
                    messages.error(request, "Invoice created but automatic email failed.")
            else:
                messages.success(request, "Invoice created successfully.")
            
            return redirect('dashboard')
    else:
        form = InvoiceForm()
        formset = InvoiceItemFormSet()
    
    return render(request, 'invoices/create_invoice.html', {
        'form': form,
        'formset': formset
    })

@login_required
def record_payment(request, invoice_id):
    invoice = get_object_or_404(Invoice, id=invoice_id, user=request.user)
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
            messages.error(request, f"Amount exceeds balance due (GHS {balance_due})")
        else:
            # --- TIMELINE LOGIC START ---
            # Create a clean string for this specific installment
            new_log_entry = f"{date_val}: GHS {amount_val} ({method_val.upper()})"
            
            existing_payment = Payment.objects.filter(invoice=invoice).first()
            
            if existing_payment:
                # Add amount to the existing record
                existing_payment.amount += amount_val 
                existing_payment.date = date_val
                existing_payment.method = method_val
                existing_payment.reference = reference_val
                
                # Append the new entry to the bottom of the timeline
                if existing_payment.payment_log:
                    existing_payment.payment_log += f"\n{new_log_entry}"
                else:
                    existing_payment.payment_log = new_log_entry
                    
                existing_payment.save()
                target_payment = existing_payment
            else:
                # First payment: Start the timeline
                target_payment = Payment.objects.create(
                    invoice=invoice,
                    amount=amount_val,
                    date=date_val,
                    method=method_val,
                    reference=reference_val,
                    payment_log=new_log_entry
                )
            # --- TIMELINE LOGIC END ---
            
            target_payment.refresh_from_db()
            invoice.refresh_from_db()

            student_name = invoice.student.full_name if invoice.student else "Student"
            receipt_no = target_payment.receipt_number or "N/A"

            if config.auto_send_email_receipts and invoice.student and invoice.student.email:
                send_invoice_email(invoice)
                messages.success(request, f"Recorded GHS {amount_val} ({receipt_no}) and receipt sent.")
            else:
                messages.success(request, f"Recorded GHS {amount_val} for {student_name}. Receipt: {receipt_no}")
            
            return redirect('dashboard')

    return render(request, 'invoices/payment.html', {
        'invoice': invoice,
        'balance_due': balance_due,
    })

@login_required
def payment_list(request):
    payments = Payment.objects.filter(invoice__user=request.user).order_by('-date')
    total_revenue = payments.aggregate(total=Sum('amount'))['total'] or Decimal('0.00')
    
    return render(request, 'invoices/payment_list.html', {
        'payments': payments,
        'total_revenue': total_revenue
    })

@login_required
def receipt_list(request):
    payments = Payment.objects.filter(invoice__user=request.user).select_related('invoice', 'invoice__student').order_by('-date')
    
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
    
    total_invoiced = sum(
        (item.quantity or 0) * (item.rate or 0) 
        for inv in invoices_involved 
        for item in inv.items.all()
    )
    total_invoiced = Decimal(str(total_invoiced))
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
    
    context = {
        'invoice': invoice, 
        'logo_path': logo_path,
        'config': config,
        'generated_at': timezone.now(),
    }
    
    response = HttpResponse(content_type='application/pdf')
    response['Content-Disposition'] = f'attachment; filename="Invoice_{invoice.invoice_number}.pdf"'
    
    template = get_template('invoices/pdf_template.html')
    html = template.render(context)
    pisa_status = pisa.CreatePDF(html, dest=response)
    
    if pisa_status.err:
        return HttpResponse('Error generating PDF')
    return response

@login_required
def generate_receipt_pdf(request, payment_id):
    payment = get_object_or_404(Payment, id=payment_id, invoice__user=request.user)
    config = SystemConfiguration.objects.first()
    logo_path = os.path.join(settings.BASE_DIR, 'invoices', 'static', '1.png')
    
    context = {
        'payment': payment,
        'invoice': payment.invoice,
        'logo_path': logo_path,
        'config': config,
        'generated_at': timezone.now(),
    }
    
    filename = f"Receipt_{payment.receipt_number or 'N/A'}.pdf"
    response = HttpResponse(content_type='application/pdf')
    response['Content-Disposition'] = f'attachment; filename="{filename}"'
    
    template = get_template('invoices/receipt_pdf_template.html')
    html = template.render(context)
    pisa_status = pisa.CreatePDF(html, dest=response)
    
    if pisa_status.err:
        return HttpResponse('Error generating Receipt PDF')
    return response

@login_required
def delete_invoice(request, invoice_id):
    invoice = get_object_or_404(Invoice, id=invoice_id, user=request.user)
    if request.method == 'POST':
        invoice.delete()
        messages.success(request, "Invoice deleted successfully.")
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
            form.save()
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
            messages.success(request, "Student updated successfully.")
            return redirect('student_list')
    else:
        form = StudentForm(instance=student)
    return render(request, 'invoices/add_student.html', {'form': form, 'edit_mode': True, 'student': student})

@login_required
def delete_student(request, student_id):
    student = get_object_or_404(Student, id=student_id)
    if request.method == 'POST':
        student.delete()
        messages.success(request, "Student deleted successfully.")
    return redirect('student_list')

@login_required
def student_detail(request, student_id):
    student = get_object_or_404(Student, id=student_id)
    
    inv_qs = student.invoices.all().prefetch_related('items')
    total_billed = sum(
        (item.quantity or 0) * (item.rate or 0) 
        for inv in inv_qs 
        for item in inv.items.all()
    )
    total_billed = Decimal(str(total_billed))
    
    total_paid = Payment.objects.filter(
        invoice__student=student
    ).aggregate(total=Sum('amount'))['total'] or Decimal('0.00')
    
    balance_due = total_billed - total_paid
    
    invoices = student.invoices.all().order_by('-date_created')
    payments_qs = Payment.objects.filter(invoice__student=student).order_by('-date')
    
    payments_with_logs = []
    for pmt in payments_qs:
        log_data = pmt.payment_log or ""
        payments_with_logs.append({
            'payment': pmt,
            'log': log_data
        })

    return render(request, 'invoices/student_detail.html', {
        'student': student,
        'total_billed': total_billed,
        'total_paid': total_paid,
        'balance_due': balance_due,
        'invoices': invoices,
        'payments_with_logs': payments_with_logs,
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
        config.auto_generate_ledger = 'auto_ledger' in request.POST
        config.auto_send_email_receipts = 'auto_receipt' in request.POST
        
        if 'logo' in request.FILES:
            config.logo = request.FILES['logo']
        
        config.save()
        messages.success(request, "Settings updated successfully.")
        return redirect('settings_view')

    return render(request, 'invoices/settings.html', {'config': config})

def send_invoice_email(invoice):
    try:
        # FIX: Ensure student exists before sending
        if not invoice.student or not invoice.student.email:
            return False

        student_name = invoice.student.full_name or "Student"
        subject = f"Invoice {invoice.invoice_number} from UGC"
        message_body = (
            f"Hello {student_name},\n\n"
            f"Your invoice {invoice.invoice_number} for GHS {invoice.grand_total} has been generated.\n\n"
            f"Please log in to the portal to make a payment.\n\n"
            f"Thank you."
        )
        recipient_list = [invoice.student.email]
        
        send_mail(
            subject, 
            message_body, 
            settings.DEFAULT_FROM_EMAIL, 
            recipient_list, 
            fail_silently=False
        )
        
        EmailLog.objects.create(
            student=invoice.student,
            subject=subject,
            message=message_body,
            status="Sent"
        )
        return True
    except Exception as e:
        print(f"EMAIL ERROR: {e}")
        return False

@login_required
def mailing_view(request):
    if request.GET.get('send_all') == 'true':
        pending = Invoice.objects.filter(user=request.user, mail_sent=False)
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
        invoice_to_send = get_object_or_404(Invoice, id=send_id, user=request.user)
        if send_invoice_email(invoice_to_send):
            invoice_to_send.mail_sent = True
            invoice_to_send.save()
            student_name = invoice_to_send.student.full_name if invoice_to_send.student else "Student"
            messages.success(request, f"Email sent to {student_name}")
        else:
            messages.error(request, "Failed to send email.")
        return redirect('mailing_center')

    pending_invoices = Invoice.objects.filter(user=request.user, mail_sent=False).order_by('-date_created')
    email_history = EmailLog.objects.all().order_by('-date_sent')
    students = Student.objects.all().order_by('full_name')
    
    return render(request, 'invoices/mailing.html', {
        'pending_invoices': pending_invoices,
        'email_history': email_history,
        'students': students,
    })

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
            Invoice.objects.filter(id__in=invoice_ids, user=request.user).delete()
            messages.success(request, "Selected invoices deleted.")
    return redirect('dashboard')

@login_required
def clear_all_logs(request):
    if request.method == 'POST':
        EmailLog.objects.all().delete()
        messages.success(request, "Mailing history cleared.")
    return redirect('mailing_center')

@login_required
def compose_email(request, student_id=None):
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

        cc_list = [cc_email] if cc_email else []
        email = EmailMessage(
            subject, 
            message_body, 
            settings.DEFAULT_FROM_EMAIL, 
            [student.email],
            cc=cc_list
        )
        
        log_type, attach_name = 'MANUAL', None

        if attach_type == 'invoice':
            latest_inv = student.invoices.first()
            if latest_inv:
                template = get_template('invoices/pdf_template.html')
                html = template.render({'invoice': latest_inv, 'logo_path': logo_path, 'config': config, 'generated_at': timezone.now()})
                pdf_output = BytesIO()
                pisa.CreatePDF(html, dest=pdf_output)
                email.attach(f"Invoice_{latest_inv.invoice_number}.pdf", pdf_output.getvalue(), 'application/pdf')
                log_type, attach_name = 'INVOICE', f"Invoice_{latest_inv.invoice_number}.pdf"
        
        elif attach_type == 'receipt':
            latest_pay = Payment.objects.filter(invoice__student=student).order_by('-date').first()
            if latest_pay:
                template = get_template('invoices/receipt_pdf_template.html')
                html = template.render({'payment': latest_pay, 'invoice': latest_pay.invoice, 'logo_path': logo_path, 'config': config, 'generated_at': timezone.now()})
                pdf_output = BytesIO()
                pisa.CreatePDF(html, dest=pdf_output)
                receipt_no = latest_pay.receipt_number or "N/A"
                email.attach(f"Receipt_{receipt_no}.pdf", pdf_output.getvalue(), 'application/pdf')
                log_type, attach_name = 'RECEIPT', f"Receipt_{receipt_no}.pdf"

        try:
            email.send()
            final_log_msg = f"{message_body}\n\n[CC: {cc_email}]" if cc_email else message_body
            EmailLog.objects.create(
                student=student, 
                subject=subject, 
                message=final_log_msg, 
                email_type=log_type, 
                attachment_name=attach_name, 
                status="Sent"
            )
            messages.success(request, f"Email sent successfully to {student.email}." + (f" copied to {cc_email}" if cc_email else ""))
        except Exception as e:
            messages.error(request, f"Failed to send email: {e}")
        return redirect('mailing_center')
            
    return render(request, 'invoices/compose_email.html', {'student': student})

@login_required
def send_invoice_pdf_email(request, invoice_id):
    invoice = get_object_or_404(Invoice, id=invoice_id)
    # FIX: Safety check for student email
    if not invoice.student or not invoice.student.email:
        messages.error(request, "Student has no email address.")
        return redirect('mailing_center')

    config = SystemConfiguration.objects.first()
    logo_path = os.path.join(settings.BASE_DIR, 'invoices', 'static', '1.png')
    
    template = get_template('invoices/pdf_template.html')
    html = template.render({'invoice': invoice, 'logo_path': logo_path, 'config': config, 'generated_at': timezone.now()})
    
    pdf_output = BytesIO()
    pisa.CreatePDF(html, dest=pdf_output)
    
    student_name = invoice.student.full_name or "Student"
    email = EmailMessage(f"Invoice: {invoice.invoice_number}", f"Hello {student_name}, invoice attached.", settings.DEFAULT_FROM_EMAIL, [invoice.student.email])
    email.attach(f"Invoice_{invoice.invoice_number}.pdf", pdf_output.getvalue(), 'application/pdf')
    
    try:
        email.send()
        invoice.mail_sent = True
        invoice.save()
        EmailLog.objects.create(student=invoice.student, subject=f"Invoice Attachment: {invoice.invoice_number}", message="PDF Invoice sent.", email_type='INVOICE', attachment_name=f"Invoice_{invoice.invoice_number}.pdf", status="Sent")
        messages.success(request, "Invoice PDF sent!")
    except Exception as e:
        messages.error(request, f"Error: {e}")
    return redirect('mailing_center')

@login_required
def send_receipt_pdf_email(request, payment_id):
    payment = get_object_or_404(Payment, id=payment_id)
    # FIX: Safety check for invoice and student email
    if not payment.invoice or not payment.invoice.student or not payment.invoice.student.email:
        messages.error(request, "Student email not found.")
        return redirect('mailing_center')

    config = SystemConfiguration.objects.first()
    logo_path = os.path.join(settings.BASE_DIR, 'invoices', 'static', '1.png')
    
    template = get_template('invoices/receipt_pdf_template.html')
    html = template.render({'payment': payment, 'invoice': payment.invoice, 'logo_path': logo_path, 'config': config, 'generated_at': timezone.now()})
    
    pdf_output = BytesIO()
    pisa.CreatePDF(html, dest=pdf_output)
    
    receipt_no = payment.receipt_number or "N/A"
    email = EmailMessage(f"Receipt: {receipt_no}", f"Hello, receipt attached.", settings.DEFAULT_FROM_EMAIL, [payment.invoice.student.email])
    email.attach(f"Receipt_{receipt_no}.pdf", pdf_output.getvalue(), 'application/pdf')
    
    try:
        email.send()
        EmailLog.objects.create(student=payment.invoice.student, subject=f"Receipt: {receipt_no}", message="PDF Receipt sent.", email_type='RECEIPT', attachment_name=f"Receipt_{receipt_no}.pdf", status="Sent")
        messages.success(request, "Receipt PDF sent!")
    except Exception as e:
        messages.error(request, f"Error: {e}")
    return redirect('mailing_center')

@login_required
def export_report_pdf(request):
    config = SystemConfiguration.objects.first()
    logo_path = os.path.join(settings.BASE_DIR, 'invoices', 'static', '1.png')
    all_invoices = Invoice.objects.filter(user=request.user).prefetch_related('items')
    
    total_billed = sum(
        (item.quantity or 0) * (item.rate or 0) 
        for inv in all_invoices 
        for item in inv.items.all()
    )
    total_billed = Decimal(str(total_billed))
    total_collected = Payment.objects.filter(invoice__user=request.user).aggregate(total=Sum('amount'))['total'] or Decimal('0.00')
    outstanding = total_billed - total_collected
    efficiency = (total_collected / total_billed * 100) if total_billed > 0 else 0
    
    top_students = Student.objects.filter(invoices__user=request.user).annotate(revenue=Sum('invoices__payments__amount')).filter(revenue__gt=0).order_by('-revenue')[:10]
    service_stats = InvoiceItem.objects.filter(invoice__user=request.user).values('description').annotate(total_value=Sum(F('quantity') * F('rate')), usage_count=Count('id')).order_by('-total_value')

    context = {'total_billed': total_billed, 'total_collected': total_collected, 'outstanding': outstanding, 'efficiency': efficiency, 'top_students': top_students, 'service_stats': service_stats, 'config': config, 'logo_path': logo_path, 'generated_at': timezone.now()}
    
    response = HttpResponse(content_type='application/pdf')
    response['Content-Disposition'] = f'attachment; filename="Financial_Report_{timezone.now().date()}.pdf"'
    template = get_template('invoices/report_pdf_template.html')
    html = template.render(context)
    pisa_status = pisa.CreatePDF(html, dest=response)
    return response

# --- DAISYUI HELPER INJECTION ---
def get_daisy_alert_class(level):
    if level == messages.SUCCESS: return "alert-success"
    if level == messages.ERROR: return "alert-error"
    if level == messages.WARNING: return "alert-warning"
    return "alert-info"