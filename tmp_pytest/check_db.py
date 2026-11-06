import os
import sys
sys.path.insert(0, "backend")
sys.path.insert(0, ".")
os.environ["DJANGO_SETTINGS_MODULE"] = "auditapi.test_settings"
import django
django.setup()
from django.db import connection
print("NAME:", connection.settings_dict["NAME"])
