from .models import SystemConfiguration

def system_config(request):
    """
    This function fetches your System Configuration (Logo, School Name, etc.)
    and makes it available to every HTML page automatically.
    """
    config, created = SystemConfiguration.objects.get_or_create(id=1)
    return {'config': config}