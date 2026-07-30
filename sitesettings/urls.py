from django.urls import path

from .views import BackupDownloadView, BackupStatusView, ResetAllDataView

urlpatterns = [
    path('backup/status/', BackupStatusView.as_view(), name='backup_status'),
    path('backup/download/', BackupDownloadView.as_view(), name='backup_download'),
    path('reset/', ResetAllDataView.as_view(), name='reset_all_data'),
]
