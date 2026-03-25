import random
from django.db import models
from django.contrib.auth.models import User
from django.db.models import Sum, F
from django.db.models.signals import post_save, post_delete
from django.dispatch import receiver
from django.templatetags.static import static 
from django.core.mail import send_mail
from django.conf import settings
from django.utils import timezone
from django.contrib.auth.signals import user_logged_in

class SystemConfiguration(models.Model):
    """
    Stores global settings for the portal (Singleton Pattern).
    Ensures the 'Financial Preferences' toggles actually save data.
    """
    institution_name = models.CharField(max_length=255, default="UGC Finance")
    institution_email = models.EmailField(default="finance@ugc.edu.gh")
    institution_address = models.TextField(default="P.O. Box 123, Accra, Ghana")
    base_currency = models.CharField(max_length=10, default="GHS")
    
    default_payment_instructions = models.TextField(
        blank=True, 
        null=True, 
        help_text="Default bank details shown on invoices (e.g. ABSA account info)"
    )
    
    auto_generate_ledger = models.BooleanField(default=True)
    auto_send_email_receipts = models.BooleanField(default=False)

    def __str__(self):
        return "System Configuration"

    class Meta:
        verbose_name = "System Configuration"
        verbose_name_plural = "System Configuration"

class Student(models.Model):
    LEVEL_CHOICES = [
        ('100', 'Level 100'),
        ('200', 'Level 200'),
        ('300', 'Level 300'),
        ('400', 'Level 400'),
        ('PG', 'Post-Graduate'),
    ]

    index_number = models.CharField(max_length=50, unique=True)
    full_name = models.CharField(max_length=200)
    program = models.CharField(max_length=200) 
    # UPDATE: Made level optional (blank=True, null=True) so it can be left out of forms
    level = models.CharField(max_length=10, choices=LEVEL_CHOICES, default='100', blank=True, null=True)
    email = models.EmailField()
    phone = models.CharField(max_length=20, blank=True)
    profile_image = models.ImageField(upload_to='profile_pics/', null=True, blank=True)
    date_joined = models.DateTimeField(auto_now_add=True, null=True)

    def __str__(self):
        return f"{self.index_number} - {self.full_name}"

    @property
    def get_photo_url(self):
        if self.profile_image and hasattr(self.profile_image, 'url'):
            return self.profile_image.url
        return static('images/default-avatar.png')

    @property
    def available_currencies(self):
        """Returns a list of unique currencies used in this student's invoices."""
        return self.invoices.values_list('currency', flat=True).distinct()

class Service(models.Model):
    CATEGORY_CHOICES = [
        ('TUITION', 'Tuition Fees'),
        ('ACCOMMODATION', 'Accommodation'),
        ('ADMIN', 'Administrative Fees'),
        ('OTHER', 'Other Services'),
    ]
    name = models.CharField(max_length=255)
    category = models.CharField(max_length=20, choices=CATEGORY_CHOICES, default='TUITION')
    default_rate = models.DecimalField(max_digits=10, decimal_places=2)

    def __str__(self):
        return f"{self.name} (GHS {self.default_rate})"

class Invoice(models.Model):
    CURRENCY_CHOICES = [
        ('GHS', 'Ghana Cedi (GHS)'),
        ('USD', 'US Dollar ($)'),
        ('EUR', 'Euro (€)'),
        ('GBP', 'British Pound (£)'),
    ]

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="invoices")
    student = models.ForeignKey(Student, on_delete=models.SET_NULL, null=True, related_name="invoices")
    invoice_number = models.CharField(max_length=50, unique=True)
    date_created = models.DateTimeField(auto_now_add=True)
    due_date = models.DateField()
    currency = models.CharField(max_length=3, choices=CURRENCY_CHOICES, default='GHS')
    invoice_type = models.CharField(max_length=50, default="Fees")
    payment_instructions = models.TextField(blank=True, null=True)

    account_name = models.CharField(max_length=255, blank=True, null=True)
    account_number = models.CharField(max_length=100, blank=True, null=True)
    bank_name = models.CharField(max_length=255, blank=True, null=True)
    branch_name = models.CharField(max_length=255, blank=True, null=True)
    
    application_fee = models.DecimalField(max_digits=10, decimal_places=2, blank=True, null=True)
    tuition_fee = models.DecimalField(max_digits=10, decimal_places=2, blank=True, null=True)
    
    expected_payment_date = models.DateField(null=True, blank=True)
    is_paid = models.BooleanField(default=False)
    mail_sent = models.BooleanField(default=False)

    def save(self, *args, **kwargs):
        if not self.invoice_number:
            while True:
                random_suffix = random.randint(100000, 999999)
                new_number = f"UGC-{random_suffix}"
                if not Invoice.objects.filter(invoice_number=new_number).exists():
                    self.invoice_number = new_number
                    break
        
        if not self.expected_payment_date:
            self.expected_payment_date = self.due_date
        
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.invoice_number} - {self.student.full_name if self.student else 'No Student'}"

    @property
    def grand_total(self):
        from decimal import Decimal
        app_fee = self.application_fee or Decimal('0.00')
        tui_fee = self.tuition_fee or Decimal('0.00')
        item_total = sum(item.total for item in self.items.all())
        return app_fee + tui_fee + item_total

    @property
    def total_paid(self):
        payments = self.payments.all()
        return sum(p.amount for p in payments)

    @property
    def balance_due(self):
        return self.grand_total - self.total_paid

class InvoiceItem(models.Model):
    invoice = models.ForeignKey(Invoice, on_delete=models.CASCADE, related_name="items")
    description = models.CharField(max_length=255)
    quantity = models.PositiveIntegerField(default=1)
    rate = models.DecimalField(max_digits=10, decimal_places=2)
    is_recurring = models.BooleanField(default=False)

    @property
    def total(self):
        return self.quantity * self.rate

class Payment(models.Model):
    METHOD_CHOICES = [
        ('momo', 'Mobile Money'),
        ('bank', 'Bank Transfer'),
        ('cash', 'Cash'),
    ]
    
    invoice = models.ForeignKey(Invoice, on_delete=models.CASCADE, related_name="payments")
    receipt_number = models.CharField(max_length=50, unique=True, editable=False, null=True)
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    date = models.DateField()
    method = models.CharField(max_length=20, choices=METHOD_CHOICES)
    reference = models.CharField(max_length=100, blank=True, null=True)
    payment_log = models.TextField(blank=True, null=True, editable=False)

    def save(self, *args, **kwargs):
        method_display = self.get_method_display() or self.method
        curr = self.invoice.currency if self.invoice else "GHS"
        new_log_entry = f"{self.date}: {curr} {self.amount} ({method_display})"

        if not self.pk:
            # Automatic unique reference generation
            if not self.reference:
                while True:
                    ref_suffix = random.randint(100000, 999999)
                    new_ref = f"REF-{ref_suffix}"
                    if not Payment.objects.filter(reference=new_ref).exists():
                        self.reference = new_ref
                        break

            existing_payment = Payment.objects.filter(invoice=self.invoice).first()
            if existing_payment:
                new_amount = existing_payment.amount + self.amount
                new_reference = f"Multiple: {self.reference}"
                current_log = existing_payment.payment_log or ""
                new_log = f"{current_log}\n{new_log_entry}".strip()
                
                Payment.objects.filter(pk=existing_payment.pk).update(
                    amount=new_amount,
                    date=self.date,
                    method=self.method,
                    reference=new_reference,
                    payment_log=new_log
                )
                
                inv = existing_payment.invoice
                if inv.balance_due <= 0:
                    Invoice.objects.filter(pk=inv.pk).update(is_paid=True)
                
                self.pk = existing_payment.pk
                self.receipt_number = existing_payment.receipt_number
                return 

        if not self.payment_log:
            self.payment_log = new_log_entry

        if not self.receipt_number:
            last_payment = Payment.objects.all().order_by('id').last()
            new_id = (last_payment.id + 1) if last_payment else 1
            self.receipt_number = f"REC-{1000 + new_id}"

        super().save(*args, **kwargs)

        inv = self.invoice
        if inv.balance_due <= 0:
            Invoice.objects.filter(pk=inv.pk).update(is_paid=True)

    def __str__(self):
        receipt_id = self.receipt_number if self.receipt_number else "New Payment"
        curr = self.invoice.currency if self.invoice else "GHS"
        return f"{receipt_id} - {curr} {self.amount}"

class EmailLog(models.Model):
    TYPE_CHOICES = [
        ('MANUAL', 'Manual Email'),
        ('INVOICE', 'Invoice PDF'),
        ('RECEIPT', 'Receipt PDF'),
    ]

    student = models.ForeignKey(Student, on_delete=models.CASCADE, related_name="email_logs")
    subject = models.CharField(max_length=255)
    message = models.TextField()
    email_type = models.CharField(max_length=20, choices=TYPE_CHOICES, default='MANUAL')
    attachment_name = models.CharField(max_length=255, blank=True, null=True) 
    date_sent = models.DateTimeField(auto_now_add=True)
    status = models.CharField(max_length=50, default="Sent")

    def __str__(self):
        name = self.student.full_name if self.student else 'Unknown'
        return f"{name} - {self.subject} ({self.date_sent.date()})"

@receiver(post_save, sender=Payment)
def auto_send_receipt(sender, instance, created, **kwargs):
    config = SystemConfiguration.objects.first()
    if config and config.auto_send_email_receipts:
        invoice = instance.invoice
        if invoice and invoice.student and invoice.student.email:
            subject = f"Payment Receipt: {instance.receipt_number}"
            curr_code = invoice.currency 
            
            message_body = (
                f"Hello {invoice.student.full_name},\n\n"
                f"Date Paid: {instance.date.strftime('%d %B, %Y')}\n"
                f"Total Amount Paid: {curr_code} {instance.amount}.\n"
                f"Receipt Number: {instance.receipt_number}\n"
                f"Your remaining balance is {curr_code} {invoice.balance_due}.\n\n"
                f"Thank you."
            )
            try:
                send_mail(subject, message_body, settings.DEFAULT_FROM_EMAIL, [invoice.student.email], fail_silently=False)
                EmailLog.objects.create(student=invoice.student, subject=subject, message=message_body, email_type='RECEIPT', status="Sent")
            except Exception as e:
                print(f"EMAIL ERROR: {e}")

class Ledger(Invoice):
    class Meta:
        proxy = True
        verbose_name = "General Ledger"
        verbose_name_plural = "General Ledger"

class FinancialReport(Invoice):
    class Meta:
        proxy = True
        verbose_name = "Financial Intelligence"
        verbose_name_plural = "Financial Intelligence"

class Receipt(Payment):
    class Meta:
        proxy = True
        verbose_name = "Consolidated Receipt"
        verbose_name_plural = "Consolidated Receipts"

class ActivityLog(models.Model):
    CATEGORY_CHOICES = [
        ('CREATE', 'Creation'),
        ('UPDATE', 'Update'),
        ('DELETE', 'Deletion'),
        ('AUTH', 'Authentication'),
        ('FINANCE', 'Financial Action'),
    ]

    user = models.ForeignKey(User, on_delete=models.CASCADE)
    category = models.CharField(max_length=20, choices=CATEGORY_CHOICES, default='UPDATE')
    action = models.CharField(max_length=255)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    browser = models.CharField(max_length=255, null=True, blank=True)
    timestamp = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-timestamp']

    def __str__(self):
        return f"{self.user.username}: {self.action} at {self.timestamp}"

@receiver(user_logged_in)
def log_user_login(sender, request, user, **kwargs):
    ActivityLog.objects.create(
        user=user, 
        category='AUTH',
        action="Logged into the dashboard",
        ip_address=request.META.get('REMOTE_ADDR'),
        browser=request.META.get('HTTP_USER_AGENT')
    )

@receiver(post_save, sender=Invoice)
def log_invoice_save(sender, instance, created, **kwargs):
    if instance.user:
        ActivityLog.objects.create(
            user=instance.user, 
            category='CREATE' if created else 'UPDATE',
            action=f"{'Created' if created else 'Updated'} Invoice {instance.invoice_number}"
        )

@receiver(post_delete, sender=Invoice)
def log_invoice_delete(sender, instance, **kwargs):
    log_user = instance.user if instance.user else User.objects.filter(is_superuser=True).first()
    if log_user:
        ActivityLog.objects.create(
            user=log_user, 
            category='DELETE',
            action=f"Deleted Invoice {instance.invoice_number}"
        )

@receiver(post_save, sender=Payment)
def log_payment_save(sender, instance, created, **kwargs):
    if created and instance.invoice and instance.invoice.user:
        ActivityLog.objects.create(
            user=instance.invoice.user, 
            category='FINANCE',
            action=f"Recorded Payment {instance.receipt_number} for {instance.invoice.invoice_number}"
        )

@receiver(post_save, sender=Student)
def log_student_save(sender, instance, created, **kwargs):
    action_text = f"{'Registered' if created else 'Updated'} Student: {instance.full_name}"
    admin = User.objects.filter(is_superuser=True).first()
    if admin:
        ActivityLog.objects.create(
            user=admin, 
            category='CREATE' if created else 'UPDATE',
            action=action_text
        )

@receiver(post_delete, sender=Student)
def log_student_delete(sender, instance, **kwargs):
    """Logs when a student record is removed from the system."""
    admin = User.objects.filter(is_superuser=True).first()
    if admin:
        ActivityLog.objects.create(
            user=admin, 
            category='DELETE',
            action=f"Deleted Student: {instance.full_name} ({instance.index_number})"
        )
