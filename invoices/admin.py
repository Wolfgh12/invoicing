from django.contrib import admin
from django.shortcuts import redirect, render, get_object_or_404
from django.urls import reverse, path
from django.utils.html import format_html
from django.utils.safestring import mark_safe 
from django.db.models import Sum, F 
from .models import (
    Invoice, Student, Service, InvoiceItem, EmailLog, 
    SystemConfiguration, Payment, Ledger, FinancialReport,
    Receipt, ActivityLog
)

# --- GLOBAL SITE CUSTOMIZATION ---
admin.site.site_header = "UGC Financial Portal"
admin.site.site_title = "UGC Admin"
admin.site.index_title = "Finance & Invoice Management"

# --- SIDEBAR REMOVAL CSS ---
SIDEBAR_CSS = """
    <style>
        #nav-sidebar, .nav-sidebar-toggler { display: none !important; }
        #main { margin-left: 0 !important; }
        .main.shifted { margin-left: 0 !important; }
        #content { padding: 20px !important; }
    </style>
"""

# Base class to apply the sidebar removal to all models automatically
class BaseAdmin(admin.ModelAdmin):
    class Media:
        css = {'all': ('admin/css/custom_admin.css',)}

    def changelist_view(self, request, extra_context=None):
        extra_context = extra_context or {}
        extra_context['sidebar_removal'] = mark_safe(SIDEBAR_CSS)
        return super().changelist_view(request, extra_context=extra_context)

    def render_change_form(self, request, context, *args, **kwargs):
        context['sidebar_removal'] = mark_safe(SIDEBAR_CSS)
        return super().render_change_form(request, context, *args, **kwargs)

@admin.register(Service)
class ServiceAdmin(BaseAdmin):
    pass

class InvoiceItemInline(admin.TabularInline):
    model = InvoiceItem
    extra = 1

@admin.register(Ledger)
class LedgerAdmin(BaseAdmin):
    def changelist_view(self, request, extra_context=None):
        return redirect(reverse('ledger_list'))

@admin.register(FinancialReport)
class FinancialReportAdmin(BaseAdmin):
    def changelist_view(self, request, extra_context=None):
        return redirect(reverse('reports_view'))

class MailingCenter(EmailLog):
    class Meta:
        proxy = True
        verbose_name = "Mailing Center"
        verbose_name_plural = "Mailing Center"

@admin.register(MailingCenter)
class MailingCenterAdmin(BaseAdmin):
    def changelist_view(self, request, extra_context=None):
        return redirect(reverse('mailing_center'))

@admin.register(SystemConfiguration)
class SystemConfigurationAdmin(BaseAdmin):
    def has_add_permission(self, request):
        if SystemConfiguration.objects.exists():
            return False
        return True

@admin.register(Invoice)
class InvoiceAdmin(BaseAdmin):
    list_display = ('invoice_number', 'get_student_name', 'date_created', 'is_paid', 'mail_sent', 'download_pdf')
    list_filter = ('is_paid', 'date_created', 'mail_sent')
    search_fields = ('invoice_number', 'student__full_name')
    inlines = [InvoiceItemInline]

    def get_student_name(self, obj):
        return obj.student.full_name if obj.student else mark_safe('<span style="color: red;">Unassigned</span>')
    get_student_name.short_description = "Student"

    def download_pdf(self, obj):
        url = reverse('generate_pdf', args=[obj.id])
        return format_html(
            '<a class="btn btn-primary btn-xs text-white" href="{}" target="_blank">'
            '<i class="fa-solid fa-file-pdf mr-1"></i> Download PDF</a>', 
            url
        )
    download_pdf.short_description = "Actions"

@admin.register(Student)
class StudentAdmin(BaseAdmin):
    list_display = ('full_name', 'index_number', 'date_joined', 'program', 'level', 'view_profile_button')
    search_fields = ('full_name', 'index_number', 'email')
    list_filter = ('level', 'date_joined')
    
    readonly_fields = ('date_joined', 'image_preview')
    fields = ('full_name', 'index_number', 'email', 'program', 'level', 'profile_image', 'image_preview', 'date_joined')

    def image_preview(self, obj):
        if obj.profile_image:
            return format_html(
                '<div class="avatar"><div class="w-12 rounded-full ring ring-primary ring-offset-base-100 ring-offset-2">'
                '<img src="{}" /></div></div>', obj.profile_image.url
            )
        return format_html('<span class="badge badge-ghost">No Photo</span>')
    image_preview.short_description = "Current Photo"

    def get_urls(self):
        urls = super().get_urls()
        custom_urls = [
            path('<path:object_id>/profile/', self.admin_site.admin_view(self.student_profile), name='student-profile'),
        ]
        return custom_urls + urls

    def view_profile_button(self, obj):
        url = reverse('admin:student-profile', args=[obj.pk])
        return format_html(
            '<a class="btn btn-accent btn-xs text-slate-900" href="{}">'
            '<i class="fa-solid fa-user-gear mr-1"></i> View Profile</a>', 
            url
        )
    view_profile_button.short_description = "Profile"

    def student_profile(self, request, object_id):
        student = get_object_or_404(Student, pk=object_id)
        invoices = Invoice.objects.filter(student=student)
        
        total_billed = InvoiceItem.objects.filter(
            invoice__in=invoices
        ).aggregate(
            total=Sum(F('quantity') * F('rate'))
        )['total'] or 0
        
        total_paid = Payment.objects.filter(invoice__in=invoices).aggregate(Sum('amount'))['amount__sum'] or 0
        balance_due = total_billed - total_paid

        context = {
            **self.admin_site.each_context(request),
            'student': student,
            'total_billed': total_billed,
            'total_paid': total_paid,
            'balance_due': balance_due,
            'title': f"Student Profile: {student.full_name}",
            'sidebar_removal': mark_safe(SIDEBAR_CSS),
        }
        return render(request, 'admin/student_profile.html', context)

@admin.register(EmailLog)
class EmailLogAdmin(BaseAdmin):
    list_display = ('get_student_name', 'subject', 'email_type', 'attachment_name', 'date_sent', 'status')
    list_filter = ('date_sent', 'status', 'email_type')
    search_fields = ('student__full_name', 'subject', 'attachment_name')
    readonly_fields = ('date_sent',)

    def get_student_name(self, obj):
        return obj.student.full_name if obj.student else "Unknown"
    get_student_name.short_description = "Student"

@admin.register(Payment)
class PaymentAdmin(BaseAdmin):
    list_display = ('get_invoice_no', 'formatted_amount', 'date', 'method', 'reference')
    list_filter = ('date', 'method')
    search_fields = ('invoice__invoice_number', 'reference', 'invoice__student__full_name')

    def formatted_amount(self, obj):
        amount_str = "{:,.2f}".format(obj.amount)
        curr = obj.invoice.currency if obj.invoice else "GHS"
        return format_html('<b style="color: #2e7d32;">{} {}</b>', curr, amount_str)
    formatted_amount.short_description = "Amount Paid"

    def get_invoice_no(self, obj):
        return obj.invoice.invoice_number if obj.invoice else "N/A"
    get_invoice_no.short_description = "Invoice"

@admin.register(Receipt)
class ReceiptAdmin(BaseAdmin):
    list_display = ('get_receipt_no', 'get_student', 'formatted_amount', 'date', 'download_receipt')
    list_filter = ('date',)
    search_fields = ('receipt_number', 'invoice__student__full_name')
    
    readonly_fields = ('receipt_number', 'date', 'payment_history_timeline')
    fields = ('receipt_number', 'invoice', 'amount', 'date', 'payment_history_timeline')

    def formatted_amount(self, obj):
        amount_str = "{:,.2f}".format(obj.amount)
        curr = obj.invoice.currency if obj.invoice else "GHS"
        return format_html('<b>{} {}</b>', curr, amount_str)
    formatted_amount.short_description = "Amount"

    def get_receipt_no(self, obj):
        return obj.receipt_number or f"PAY-{obj.id}"
    get_receipt_no.short_description = "Receipt No"

    def get_student(self, obj):
        if obj.invoice and obj.invoice.student:
            return obj.invoice.student.full_name
        return mark_safe('<span class="badge badge-outline">N/A</span>')
    get_student.short_description = "Student"

    def payment_history_timeline(self, obj):
        if not obj.invoice:
            return mark_safe('<span class="badge badge-error">No Invoice Linked</span>')
        
        payments = Payment.objects.filter(invoice=obj.invoice).order_by('date')
        curr = obj.invoice.currency if obj.invoice else "GHS"
        
        if not payments.exists():
            return mark_safe('<div class="alert alert-warning py-2">No history recorded yet</div>')
        
        total_so_far = payments.aggregate(Sum('amount'))['amount__sum'] or 0
        
        html = '<div class="payment-timeline-wrapper" style="max-width: 450px; background: #f8fafc; padding: 15px; border-radius: 12px; border: 1px solid #e2e8f0;">'
        html += '<ul class="steps steps-vertical w-full">'
        
        for p in payments:
            date_str = p.date.strftime("%d %b, %Y") if p.date else "No Date"
            method_display = (p.get_method_display() if hasattr(p, "get_method_display") else str(p.method)).upper()
            
            html += f'''
            <li class="step step-primary mb-4">
                <div class="flex flex-col items-start text-left ml-4">
                    <span class="text-[10px] uppercase font-bold text-slate-400 tracking-wider">{date_str}</span>
                    <div class="flex items-center gap-2">
                        <span class="font-black text-slate-700">{curr} {p.amount:,.2f}</span>
                        <span class="badge badge-ghost badge-sm text-[9px]">{method_display}</span>
                    </div>
                </div>
            </li>
            '''
        
        html += '</ul>'
        
        html += f'''
        <div class="mt-4 pt-3 border-t border-dashed border-slate-300 flex justify-between items-center">
            <span class="text-xs font-bold text-slate-500 uppercase">Total Collected</span>
            <span class="badge badge-success font-bold text-white">{curr} {total_so_far:,.2f}</span>
        </div>
        '''
        html += '</div>'
        
        return mark_safe(html)
    payment_history_timeline.short_description = "Payment Installment Timeline"

    def download_receipt(self, obj):
        url = reverse('generate_receipt_pdf', args=[obj.id])
        return format_html(
            '<a class="btn btn-secondary btn-xs" href="{}" target="_blank">'
            '<i class="fa-solid fa-print mr-1"></i> Print Receipt</a>', 
            url
        )
    download_receipt.short_description = "Actions"

# --- NEW: SYSTEM ACTIVITY LOG ADMIN ---
@admin.register(ActivityLog)
class ActivityLogAdmin(BaseAdmin):
    list_display = ('timestamp', 'user', 'colored_category', 'action', 'ip_address')
    list_filter = ('category', 'timestamp', 'user')
    search_fields = ('action', 'user__username', 'ip_address')
    
    # Secure logs: make everything read-only except allow deletion
    readonly_fields = ('user', 'category', 'action', 'timestamp', 'ip_address', 'browser')
    
    # UPDATED: Added 'delete_selected' so you can delete ticked rows via the Run button
    actions = ['delete_selected', 'clear_all_logs']

    def colored_category(self, obj):
        colors = {
            'CREATE': '#2e7d32', # Green
            'UPDATE': '#ed6c02', # Orange
            'DELETE': '#d32f2f', # Red
            'AUTH': '#0288d1',   # Blue
            'FINANCE': '#7b1fa2' # Purple
        }
        color = colors.get(obj.category, '#757575')
        return format_html(
            '<span style="background: {}; color: white; padding: 2px 8px; border-radius: 4px; font-weight: bold; font-size: 10px;">{}</span>',
            color, obj.get_category_display()
        )
    colored_category.short_description = "Category"

    def has_add_permission(self, request):
        return False # Logs cannot be manually created

    def has_delete_permission(self, request, obj=None):
        return True # CRITICAL: This allows the "Run" delete button to work

    def clear_all_logs(self, request, queryset):
        """Action to wipe every log entry in the table"""
        ActivityLog.objects.all().delete()
        self.message_user(request, "All activity logs have been successfully cleared.")
    clear_all_logs.short_description = "Clear ALL activity logs"