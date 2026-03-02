from django.db import models
from django.contrib.auth.models import User
from django.db.models import Sum, F
from django.db.models.signals import post_save
from django.dispatch import receiver
from django.templatetags.static import static # Added for default image handling
from django.core.mail import send_mail
from django.conf import settings
from django.utils import timezone

class SystemConfiguration(models.Model):
    """
    Stores global settings for the portal (Singleton Pattern).
    Ensures the 'Financial Preferences' toggles actually save data.
    """
    institution_name = models.CharField(max_length=255, default="UGC Finance")
    institution_email = models.EmailField(default="finance@ugc.edu.gh")
    institution_address = models.TextField(default="P.O. Box 123, Accra, Ghana")
    base_currency = models.CharField(max_length=10, default="GHS")
    
    # These match your Settings toggle screenshot
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
    level = models.CharField(max_length=10, choices=LEVEL_CHOICES, default='100')
    email = models.EmailField()
    phone = models.CharField(max_length=20, blank=True)
    profile_image = models.ImageField(upload_to='profile_pics/', null=True, blank=True)
    
    # TEMPORARY UPDATE: Changed auto_now_add=True to default=timezone.now to allow manual fixing of dates
    date_joined = models.DateTimeField(auto_now_add=True, null=True)

    def __str__(self):
        return f"{self.index_number} - {self.full_name}"

    @property
    def get_photo_url(self):
        """
        Return the profile image URL if it exists, 
        otherwise return a path to a default avatar.
        """
        if self.profile_image and hasattr(self.profile_image, 'url'):
            return self.profile_image.url
        return static('images/default-avatar.png') # Ensure you have a default image in static

class Service(models.Model):
    # ADDED FOR HEATMAP: Categorization of services
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
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="invoices")
    student = models.ForeignKey(Student, on_delete=models.SET_NULL, null=True, related_name="invoices")
    invoice_number = models.CharField(max_length=50, unique=True)
    date_created = models.DateTimeField(auto_now_add=True)
    due_date = models.DateField()
    
    # ADDED: This field stores the "Services", "Academic", "Accommodation" tags
    invoice_type = models.CharField(max_length=50, default="Fees")
    
    # UPDATED: Automation for expected_payment_date
    expected_payment_date = models.DateField(null=True, blank=True)
    
    is_paid = models.BooleanField(default=False)
    mail_sent = models.BooleanField(default=False)

    def save(self, *args, **kwargs):
        # AUTOMATION: Default expected_payment_date to due_date if empty
        if not self.expected_payment_date:
            self.expected_payment_date = self.due_date
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.invoice_number} - {self.student.full_name if self.student else 'No Student'}"

    @property
    def grand_total(self):
        items = self.items.all()
        total = sum((item.quantity * item.rate) for item in items)
        return total

    @property
    def total_paid(self):
        payments = self.payments.all()
        return sum(p.amount for p in payments)

    @property
    def balance_due(self):
        return self.grand_total - self.total_paid

class InvoiceItem(models.Model):
    invoice = models.ForeignKey(Invoice, related_name='items', on_delete=models.CASCADE)
    service = models.ForeignKey(Service, on_delete=models.SET_NULL, null=True, blank=True)
    description = models.CharField(max_length=255)
    quantity = models.PositiveIntegerField(default=1)
    rate = models.DecimalField(max_digits=10, decimal_places=2)
    
    # ADDED FOR FORECASTING: To distinguish one-time fees from recurring ones
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
    
    # NEW: Field to store the history of installments in a text format
    payment_log = models.TextField(blank=True, null=True, editable=False)

    def save(self, *args, **kwargs):
        # 1. PREPARE THE NEW LOG ENTRY
        method_display = self.get_method_display() or self.method
        new_log_entry = f"{self.date}: GHS {self.amount} ({method_display})"

        # 2. CONSOLIDATION LOGIC: Check if this invoice already has a payment record
        if not self.pk:
            existing_payment = Payment.objects.filter(invoice=self.invoice).first()

            if existing_payment:
                # Update existing record data
                new_amount = existing_payment.amount + self.amount
                new_reference = f"Multiple: {self.reference or 'N/A'}"
                current_log = existing_payment.payment_log or ""
                new_log = f"{current_log}\n{new_log_entry}".strip()
                
                # FIXED: Use .update() instead of .save() to prevent the infinite recursion loop
                Payment.objects.filter(pk=existing_payment.pk).update(
                    amount=new_amount,
                    date=self.date,
                    method=self.method,
                    reference=new_reference,
                    payment_log=new_log
                )
                
                # Check if Invoice is now paid
                inv = existing_payment.invoice
                if inv.balance_due <= 0:
                    Invoice.objects.filter(pk=inv.pk).update(is_paid=True)
                
                # Assign attributes to current instance so the View can access them after return
                self.pk = existing_payment.pk
                self.receipt_number = existing_payment.receipt_number
                return # Stop here to avoid creating a duplicate row

        # 3. IF NEW PRIMARY PAYMENT: Initialize log and generate Receipt Number
        if not self.payment_log:
            self.payment_log = new_log_entry

        if not self.receipt_number:
            last_payment = Payment.objects.all().order_by('id').last()
            new_id = (last_payment.id + 1) if last_payment else 1
            self.receipt_number = f"REC-{1000 + new_id}"

        super().save(*args, **kwargs)

        # Update Invoice Paid Status
        inv = self.invoice
        if inv.balance_due <= 0:
            Invoice.objects.filter(pk=inv.pk).update(is_paid=True)

    def __str__(self):
        # FIXED: Provide a fallback for receipt_number to prevent "No Receipt" or crashes in Admin
        # Uses the property if it exists, otherwise falls back to a temporary label
        receipt_id = self.receipt_number if self.receipt_number else "New Payment"
        return f"{receipt_id} - GHS {self.amount}"

# --- UPDATED MODEL FOR MAILING HISTORY ---
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

# --- AUTOMATION LOGIC ---
@receiver(post_save, sender=Payment)
def auto_send_receipt(sender, instance, created, **kwargs):
    config = SystemConfiguration.objects.first()
    if config and config.auto_send_email_receipts:
        invoice = instance.invoice
        # Extra safety check for Student and Email to prevent "None" errors
        if invoice and invoice.student and invoice.student.email:
            subject = f"Payment Receipt: {instance.receipt_number}"
            message_body = (
                f"Hello {invoice.student.full_name},\n\n"
                f"We have successfully processed a payment on your account.\n"
                f"Total Amount Paid on this Receipt: GHS {instance.amount}.\n"
                f"Receipt Number: {instance.receipt_number}\n"
                f"Your remaining balance for invoice {invoice.invoice_number} is GHS {invoice.balance_due}.\n\n"
                f"Thank you."
            )
            try:
                send_mail(
                    subject,
                    message_body,
                    settings.DEFAULT_FROM_EMAIL,
                    [invoice.student.email],
                    fail_silently=False
                )
                EmailLog.objects.create(
                    student=invoice.student,
                    subject=subject,
                    message=message_body,
                    email_type='RECEIPT',
                    status="Sent"
                )
            except Exception as e:
                print(f"AUTOMATED EMAIL ERROR: {e}")

# --- PROXY MODELS FOR ADMIN SIDEBAR ---
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