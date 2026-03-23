from django import forms
from .models import Invoice, InvoiceItem, Student

class StudentForm(forms.ModelForm):
    class Meta:
        model = Student
        # REMOVED: 'level' from the fields list to hide it from the page
        fields = ['full_name', 'index_number', 'program', 'email', 'phone', 'profile_image']
        widgets = {
            'full_name': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Enter Full Name'}),
            'index_number': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'e.g. 01234567'}),
            'program': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'e.g. BSc Computer Science'}),
            # REMOVED: 'level' widget
            'email': forms.EmailInput(attrs={'class': 'form-control', 'placeholder': 'email@example.com'}),
            'phone': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'e.g. +233...'}),
            'profile_image': forms.FileInput(attrs={'class': 'form-control'}),
        }

class InvoiceForm(forms.ModelForm):
    class Meta:
        model = Invoice
        # UPDATED: Added bank details and fee fields to the fields list
        fields = [
            'student', 'invoice_number', 'due_date', 'currency', 'invoice_type', 
            'payment_instructions', 'account_name', 'account_number', 'bank_name', 
            'branch_name', 'application_fee', 'tuition_fee', 'is_paid'
        ]
        widgets = {
            'due_date': forms.DateInput(attrs={'type': 'date', 'class': 'form-control'}),
            'student': forms.Select(attrs={'class': 'form-select'}),
            # UPDATED: Added 'readonly': 'readonly' to prevent manual editing
            # This is now valid because we removed editable=False from the model
            'invoice_number': forms.TextInput(attrs={
                'class': 'form-control', 
                'readonly': 'readonly', 
                'placeholder': 'Auto-generated on student selection'
            }),
            'currency': forms.Select(attrs={'class': 'form-select'}),
            'is_paid': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
            # Added Textarea for payment_instructions to allow ABSA/Dollar account details
            'payment_instructions': forms.Textarea(attrs={
                'class': 'form-control', 
                'placeholder': 'Enter bank details or payment instructions here...',
                'rows': 3
            }),
            # Added HiddenInput for invoice_type so the JavaScript can populate it
            'invoice_type': forms.HiddenInput(),
            
            # NEW: Widgets for the optional bank and fee fields
            'account_name': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Account Name'}),
            'account_number': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Account Number'}),
            'bank_name': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Bank Name'}),
            'branch_name': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Branch'}),
            'application_fee': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01', 'placeholder': '0.00'}),
            'tuition_fee': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01', 'placeholder': '0.00'}),
        }

    def clean(self):
        """
        Final Validation: If Optional Fees are provided, we ensure 
        the form ignores line item calculations on the backend.
        """
        cleaned_data = super().clean()
        app_fee = cleaned_data.get('application_fee')
        tui_fee = cleaned_data.get('tuition_fee')

        # Logic: If any optional fee is present, the line items should 
        # effectively contribute 0 to the grand total in the database.
        if (app_fee and app_fee > 0) or (tui_fee and tui_fee > 0):
            # We communicate this state to the view using a custom attribute
            self.has_priority_fees = True
        else:
            self.has_priority_fees = False
            
        return cleaned_data

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # REMOVED: Initial hardcoded bank details to prevent "sticky" auto-fill on new pages.
        # These are now handled dynamically by the 'Invoice Type' selector in the HTML.

class InvoiceItemForm(forms.ModelForm):
    class Meta:
        model = InvoiceItem
        fields = ['description', 'quantity', 'rate']
        widgets = {
            'description': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Item description'}),
            'quantity': forms.NumberInput(attrs={'class': 'form-control', 'min': '1'}),
            'rate': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # UPDATE: Make rate optional in the form so it doesn't block saving
        # when disabled by the Optional Fees logic in the frontend.
        self.fields['rate'].required = False

# FormSet for handling multiple line items per invoice
InvoiceItemFormSet = forms.inlineformset_factory(
    Invoice, 
    InvoiceItem, 
    form=InvoiceItemForm, 
    extra=1, 
    can_delete=True
)