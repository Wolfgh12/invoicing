from django import template
from decimal import Decimal

register = template.Library()

@register.filter(name='multiply')
def multiply(value, arg):
    try:
        return float(value) * float(arg)
    except (ValueError, TypeError):
        return 0

@register.filter(name='currency_symbol')
def currency_symbol(value):
    symbols = {
        'GHS': 'GH₵',
        'USD': '$',
        'EUR': '€',
        'GBP': '£',
    }
    return symbols.get(value, value)

@register.filter(name='split')
def split(value, arg):
    try:
        return value.split(arg)
    except (AttributeError, TypeError):
        return [value]

@register.filter(name='get_currency_total')
def get_currency_total(metrics_data, currency_code):
    """
    Finds the total OWED (Pending) for a specific currency. 
    """
    if not metrics_data:
        return 0
        
    for item in metrics_data:
        # Check for currency in both QuerySet and List formats
        curr = item.get('invoice__currency') or item.get('currency')

        if curr == currency_code or (curr is None and currency_code == "GHS"):
            # Returns 'amount' (the calculated balance) or 'total'
            return item.get('amount') or item.get('total') or 0
            
    return 0

@register.filter(name='get_currency_collected')
def get_currency_collected(metrics_data, currency_code):
    """
    Finds the total COLLECTED (Paid) for a specific currency.
    """
    if not metrics_data:
        return 0
        
    for item in metrics_data:
        curr = item.get('invoice__currency') or item.get('currency')

        if curr == currency_code or (curr is None and currency_code == "GHS"):
            # Returns the 'collected' value we added to the view
            return item.get('collected') or 0
            
    return 0

@register.filter(name='get_currency_billed')
def get_currency_billed(metrics_data, currency_code):
    """
    Finds the total BILLED (Gross) for a specific currency.
    """
    if not metrics_data:
        return 0
        
    for item in metrics_data:
        curr = item.get('invoice__currency') or item.get('currency')

        if curr == currency_code or (curr is None and currency_code == "GHS"):
            # Returns the 'total_billed' value from the context
            return item.get('total_billed') or 0
            
    return 0