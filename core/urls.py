"""
URL configuration for core project.
"""
from django.contrib import admin
from django.urls import path, include 
from django.contrib.auth import views as auth_views
from django.conf import settings 
from django.conf.urls.static import static 
from django.views.generic import RedirectView 
from invoices import views 
from two_factor.urls import urlpatterns as tf_urls 

urlpatterns = [
    # 1. ADMIN ROUTE 
    # This will now use the default Django login because of the settings change
    path('admin/', admin.site.urls),

    # 2. REDIRECT EMPTY PATH TO LOGIN
    path('', RedirectView.as_view(url='/account/login/', permanent=True)),

    # 3. AUTHENTICATION ROUTES
    # Standard login for your custom portal
    path('account/login/', auth_views.LoginView.as_view(template_name='registration/login.html'), name='login'),
    
    # Rest of the 2FA logic (QR codes, etc.) remains available if needed
    path('', include(tf_urls)), 
    
    path('logout/', auth_views.LogoutView.as_view(next_page='login'), name='logout'),

    # 4. INVOICE MANAGEMENT ROUTES
    path('dashboard/', views.dashboard, name='dashboard'), 
    # UPDATED: Added trailing slash to ensure POST requests finalize correctly
    path('create/', views.create_invoice, name='create_invoice'),
    
    # NEW: Added route for AJAX auto-generation of invoice numbers
    path('generate-invoice-number/', views.generate_invoice_number, name='generate_invoice_number'),
    
    path('payment/<int:invoice_id>/', views.record_payment, name='record_payment'),
    path('payments/history/', views.payment_list, name='payment_list'),
    path('receipts/', views.receipt_list, name='receipt_list'),
    path('pdf/<int:invoice_id>/', views.generate_pdf, name='generate_pdf'),
    
    path('receipt/pdf/<int:payment_id>/', views.generate_receipt_pdf, name='generate_receipt_pdf'),
    
    # UPDATED: Confirmed deletion route mapping to the robust delete_invoice view
    path('delete/<int:invoice_id>/', views.delete_invoice, name='delete_invoice'),
    path('bulk-delete/', views.bulk_delete_invoices, name='bulk_delete_invoices'),
    
    # --- CONFIG & ANALYTICS ROUTES ---
    path('ledger/', views.ledger_list, name='ledger_list'),
    path('reports/', views.reports_view, name='reports_view'),
    path('reports/debt-detail/', views.debt_detail_json, name='debt_detail_json'),
    
    # This path handles the PDF export and will accept the ?currency= query parameter
    path('reports/export-pdf/', views.export_report_pdf, name='export_report_pdf'),
    
    # --- SETTINGS ROUTES ---
    path('settings/', views.settings_view, name='settings_view'),
    path('settings/update/', views.settings_view, name='settings_update'),
    
    # --- MAILING & COMMUNICATION ROUTES ---
    path('mailing/', views.mailing_view, name='mailing_center'),
    path('mailing/compose/', views.compose_email, name='compose_email'),
    path('mailing/compose/<int:student_id>/', views.compose_email, name='compose_email_direct'),
    
    path('mailing/send-invoice-pdf/<int:invoice_id>/', views.send_invoice_pdf_email, name='send_invoice_pdf_email'),
    path('mailing/send-receipt-pdf/<int:payment_id>/', views.send_receipt_pdf_email, name='send_receipt_pdf_email'),
    
    path('mailing/delete-log/<int:log_id>/', views.delete_email_log, name='delete_email_log'),
    path('mailing/clear-history/', views.clear_all_logs, name='clear_all_logs'),

    # 5. STUDENT MANAGEMENT ROUTES
    path('students/', views.student_list, name='student_list'),
    # UPDATED: Added trailing slash to student addition
    path('students/add/', views.add_student, name='add_student'), 
    path('students/<int:student_id>/', views.student_detail, name='student_detail'),
    path('students/edit/<int:student_id>/', views.edit_student, name='edit_student'),
    path('students/delete/<int:student_id>/', views.delete_student, name='delete_student'),

    # --- ACTIVITY LOG ROUTES ---
    # NEW: Link to the full history view
    path('activity-log/', views.activity_log_view, name='activity_log'),

    path('students/bulk-delete/', views.bulk_delete_students, name='bulk_delete_students'),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)