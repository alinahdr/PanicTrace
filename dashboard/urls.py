from django.urls import path
from .views import dashboard_view, add_attack_view, export_csv, export_pdf

urlpatterns = [
    path("",            dashboard_view,  name="dashboard"),
    path("add-attack/", add_attack_view, name="add_attack"),
    path("export/csv/", export_csv,      name="export_csv"),
    path("export/pdf/", export_pdf,      name="export_pdf"),
]