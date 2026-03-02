from django import forms
from .models import Invoice, InvoiceItem, Student

class StudentForm(forms.ModelForm):
    class Meta:
        model = Student
        # Added 'profile_image' to the fields list
        fields = ['full_name', 'index_number', 'program', 'level', 'email', 'phone', 'profile_image']
        widgets = {
            'full_name': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Enter Full Name'}),
            'index_number': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'e.g. 01234567'}),
            'program': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'e.g. BSc Computer Science'}),
            'level': forms.Select(attrs={'class': 'form-select'}),
            'email': forms.EmailInput(attrs={'class': 'form-control', 'placeholder': 'email@example.com'}),
            'phone': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'e.g. +233...'}),
            # The profile_image is handled manually in our template, 
            # but including it here allows Django to handle the upload logic.
            'profile_image': forms.FileInput(attrs={'class': 'form-control'}),
        }

class InvoiceForm(forms.ModelForm):
    class Meta:
        model = Invoice
        # UPDATED: Added 'invoice_type' to the fields list
        fields = ['student', 'invoice_number', 'due_date', 'invoice_type', 'is_paid']
        widgets = {
            'due_date': forms.DateInput(attrs={'type': 'date', 'class': 'form-control'}),
            'student': forms.Select(attrs={'class': 'form-select'}),
            'invoice_number': forms.TextInput(attrs={'class': 'form-control'}),
            'is_paid': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
            # Added HiddenInput for invoice_type so the JavaScript can populate it
            'invoice_type': forms.HiddenInput(),
        }

class InvoiceItemForm(forms.ModelForm):
    class Meta:
        model = InvoiceItem
        fields = ['description', 'quantity', 'rate']
        widgets = {
            'description': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Item description'}),
            'quantity': forms.NumberInput(attrs={'class': 'form-control', 'min': '1'}),
            'rate': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01'}),
        }

# FormSet for handling multiple line items per invoice
InvoiceItemFormSet = forms.inlineformset_factory(
    Invoice, 
    InvoiceItem, 
    form=InvoiceItemForm, 
    extra=1, 
    can_delete=True
)