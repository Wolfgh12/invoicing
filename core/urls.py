"""
URL configuration for core project.
"""
from django.contrib import admin
from django.urls import path
from django.contrib.auth import views as auth_views
from django.conf import settings 
from django.conf.urls.static import static 
from invoices import views 

urlpatterns = [
    # 1. AUTHENTICATION ROUTES
    path('login/', auth_views.LoginView.as_view(template_name='registration/login.html'), name='login'),
    path('logout/', auth_views.LogoutView.as_view(next_page='login'), name='logout'),

    # 2. ADMIN ROUTE
    path('admin/', admin.site.urls),
    
    # 3. INVOICE MANAGEMENT ROUTES
    path('', views.dashboard, name='dashboard'),
    path('create/', views.create_invoice, name='create_invoice'),
    path('payment/<int:invoice_id>/', views.record_payment, name='record_payment'),
    path('payments/history/', views.payment_list, name='payment_list'),
    path('receipts/', views.receipt_list, name='receipt_list'),
    path('pdf/<int:invoice_id>/', views.generate_pdf, name='generate_pdf'),
    
    # Route for individual Payment Receipt PDF
    path('receipt/pdf/<int:payment_id>/', views.generate_receipt_pdf, name='generate_receipt_pdf'),
    
    path('delete/<int:invoice_id>/', views.delete_invoice, name='delete_invoice'),
    path('bulk-delete/', views.bulk_delete_invoices, name='bulk_delete_invoices'),
    
    # --- CONFIG & ANALYTICS ROUTES ---
    path('ledger/', views.ledger_list, name='ledger_list'),
    path('reports/', views.reports_view, name='reports_view'),
    
    # THE MISSING PIECE: Route for the Heatmap student breakdown
    path('reports/debt-detail/', views.debt_detail_json, name='debt_detail_json'),
    
    # Route for downloading the Financial Intelligence PDF Report
    path('reports/export-pdf/', views.export_report_pdf, name='export_report_pdf'),
    
    # --- SETTINGS ROUTES ---
    path('settings/', views.settings_view, name='settings_view'),
    path('settings/update/', views.settings_view, name='settings_update'),
    
    # --- MAILING & COMMUNICATION ROUTES ---
    # UPDATED: name changed from 'mailing_view' to 'mailing_center' to fix NoReverseMatch
    path('mailing/', views.mailing_view, name='mailing_center'),
    
    # Compose email (supports optional student_id via URL or POST)
    path('mailing/compose/', views.compose_email, name='compose_email'),
    path('mailing/compose/<int:student_id>/', views.compose_email, name='compose_email_direct'),
    
    # Intelligence: Send Invoice/Receipt PDFs via email
    path('mailing/send-invoice-pdf/<int:invoice_id>/', views.send_invoice_pdf_email, name='send_invoice_pdf_email'),
    path('mailing/send-receipt-pdf/<int:payment_id>/', views.send_receipt_pdf_email, name='send_receipt_pdf_email'),
    
    # Mailing History Management
    path('mailing/delete-log/<int:log_id>/', views.delete_email_log, name='delete_email_log'),
    path('mailing/clear-history/', views.clear_all_logs, name='clear_all_logs'),

    # 4. STUDENT MANAGEMENT ROUTES
    path('students/', views.student_list, name='student_list'),
    path('students/add/', views.add_student, name='add_student'), 
    path('students/<int:student_id>/', views.student_detail, name='student_detail'),
    path('students/edit/<int:student_id>/', views.edit_student, name='edit_student'),
    path('students/delete/<int:student_id>/', views.delete_student, name='delete_student'),
]

# --- SERVE MEDIA FILES DURING DEVELOPMENT ---
if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)